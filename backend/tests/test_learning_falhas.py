"""Falha em vocabulário fechado (ADR-054, decisão 4): o classificador sobre os textos REAIS do executor, do roteador de
IA e do funil de receitas, a catraca por AST sobre `executor.py` e a tabela de camada e "onde alterar".

A catraca é a mesma disciplina de `test_arquitetura.py`: todo literal que o executor passa a `fail_or_retry(...)` ou a
`StepOutcome(Outcome.failed|uncertain|waiting_user, ...)` tem de cair num tipo diferente de `outro`. A base é medida
(hoje ZERO) e só desce; um motivo novo sem regra reprova aqui, com o texto.

Medido em 29/09 (só leitura, banco do central em `7a02491`): das 221 tentativas reais com falha, `outro` = 4,5%
(os 10 restantes são textos livres de bloqueio relatado pela IA, que o pacote A2 passa a marcar).

Nível de prova: `simulated` (regra pura e AST do código).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.devices.adb import motivo_de_anr
from app.modules.learning.domain.falhas import (CAMADA, NUNCA_VIRA_LICAO, ONDE_ALTERAR, Camada, FailureKind,
                                                camada_de, classificar_falha, classificar_texto,
                                                falha_do_retorno_de_receita, onde_alterar)
from app.taskqueue import recipes
from app.taskqueue.executor import ciclo_sem_progresso

F = FailureKind
EXECUTOR = Path(__file__).resolve().parents[1] / "app" / "taskqueue" / "executor.py"

#: Catraca: quantos literais de falha do executor ainda caem em `outro`. Só desce.
OUTRO_NO_EXECUTOR = 0


# ------------------------------------------------------------------ os textos que existem de verdade
TEXTOS: list[tuple[str, FailureKind]] = [
    ("Tempo da etapa esgotado (180s).", F.PRAZO_DA_ETAPA),
    # O prazo que vence DEPOIS de um ANR diz o ANR (r-20260928165254-e31953).
    ("Tempo da etapa esgotado (180s); " + motivo_de_anr("Instagram", "android-06") + ".", F.APP_ANR),
    (motivo_de_anr("Instagram", "android-06"), F.APP_ANR),
    ("Prazo da etapa (180s) esgotado durante a decisão da IA: A chamada de IA (decide) passou do prazo restante da "
     "etapa (12 s).", F.PRAZO_DA_ETAPA),
    ("Prazo da etapa esgotado antes da chamada de IA.", F.PRAZO_DA_ETAPA),
    ("A interface do aparelho seguiu ocupada: busy", F.UI_OCUPADA),
    ("A leitura da tela seguiu falhando: adb", F.UI_OCUPADA),
    ("Não foi possível observar a tela: socket hang up", F.SESSAO_DE_AUTOMACAO),
    ("Sessão de automação indisponível: UiAutomator2 caiu", F.SESSAO_DE_AUTOMACAO),
    ("Falhas consecutivas do driver: x", F.SESSAO_DE_AUTOMACAO),
    ("Tempo esgotado numa chamada ao aparelho: tap", F.APARELHO_TRAVADO),
    ("Chamada ao aparelho travada: tap", F.APARELHO_TRAVADO),
    ("Tentativa interrompida: o backend reiniciou", F.INTERROMPIDA),
    ("O app pede autenticação (campo de senha).", F.AUTENTICACAO),
    ("O app pede autenticação e a senha da conta está guardada sem consentimento (x): consentimento_pendente.",
     F.AUTENTICACAO),
    ("O provedor de IA recusou esta requisição por política: x", F.IA_RECUSA),
    ("O provedor de IA recusou verificar esta etapa por política: x", F.IA_RECUSA),
    ("IA indisponível: Falha de rede ao contatar o provedor de IA.", F.IA_INDISPONIVEL),
    ("IA indisponível: Tempo esgotado ao contatar http://127.0.0.1:8001/v1.", F.IA_INDISPONIVEL),
    ("Provedor de IA sem chave configurada (ANTHROPIC_API_KEY).", F.IA_INDISPONIVEL),
    ("Verificação não pôde ser feita: erro", F.IA_INDISPONIVEL),
    ("Sem crédito no provedor de IA.", F.IA_SALDO),
    ("Saldo abaixo do limite. Recarregue no console e registre a recarga em Configuração › IA para retomar.",
     F.IA_SALDO),
    ("Teto de gasto de IA da execução atingido: US$ 0.50 de US$ 0.50. Ajuste o limite em Configuração › Limites "
     "para retomar.", F.IA_ORCAMENTO),
    ("Limite de 60 chamadas de IA por objetivo atingido.", F.IA_ORCAMENTO),
    ("Orçamento de 200000 tokens da execução esgotado.", F.IA_ORCAMENTO),
    ("A etapa passou do orçamento de 13 chamadas de IA para OPEN_POST: o normal, em 9 etapas concluídas nos últimos "
     "14 dias, é 3–9. Parada para não girar até o prazo.", F.IA_ORCAMENTO),
    ("A IA insistiu em chamadas inválidas.", F.IA_CHAMADA_INVALIDA),
    ("A IA não usou collect_list na etapa de coleta.", F.IA_CHAMADA_INVALIDA),
    ("O efeito externo foi tentado no elemento errado.", F.EFEITO_ALVO_ERRADO),
    ("O toque foi tentado no controle de outra publicação.", F.EFEITO_ALVO_ERRADO),
    ("Pré-condições do efeito externo não foram atendidas.", F.EFEITO_GUARDA_NAO_ATENDIDA),
    ("Limite de 40 ações por etapa atingido sem concluir.", F.CICLO_SEM_PROGRESSO),
    ("A coleta não encontrou nenhum item na lista.", F.COLETA_VAZIA),
    ("A lista não chegou ao fim dentro do limite de páginas da coleta.", F.COLETA_INCOMPLETA),
    ("A lista tem 80 itens; o limite é 50.", F.COLETA_INCOMPLETA),
    ("O efeito foi disparado, mas não foi possível comprová-lo: enviando", F.EFEITO_NAO_COMPROVADO),
    ("Defeito do plano — a pós-condição não é comprovável pela tela (descreve processo/histórico); repetir não "
     "resolve: x", F.DEFEITO_DO_PLANO),
    ("Pós-condição não comprovada: a conversa não abriu", F.POS_CONDICAO_NAO_COMPROVADA),
    ("o texto continua incompleto depois de 3 reaplicações", F.DIGITACAO_INCOMPLETA),
]


@pytest.mark.parametrize("texto, tipo", TEXTOS)
def test_o_classificador_nos_textos_reais(texto: str, tipo: FailureKind) -> None:
    assert classificar_texto(texto) is tipo, texto


def test_ciclo_sem_progresso_do_executor_e_classificado() -> None:
    mesma = [("tela", "tela-e", "tap:1")] * 6
    texto = ciclo_sem_progresso(mesma, 3)
    assert texto and classificar_texto(texto) is F.CICLO_SEM_PROGRESSO
    alternando = [("a", "a-e", "tap:1"), ("b", "b-e", "back:"), ("a", "a-e", "tap:1"), ("b", "b-e", "back:")] * 4
    texto = ciclo_sem_progresso(alternando, 3)
    assert texto and classificar_texto(texto) is F.CICLO_SEM_PROGRESSO


def test_os_motivos_do_funil_de_receitas_caem_no_mesmo_vocabulario() -> None:
    for trecho, motivo in recipes._MOTIVOS_DO_RETORNO:
        assert recipes.motivo_do_retorno(trecho) == motivo
        assert falha_do_retorno_de_receita(motivo) is not F.OUTRO, motivo
        assert classificar_texto(trecho) is falha_do_retorno_de_receita(motivo), trecho
    assert falha_do_retorno_de_receita("outro") is F.OUTRO


def test_o_status_decide_antes_do_texto() -> None:
    assert classificar_falha("qualquer coisa", "succeeded") is None
    assert classificar_falha("Pós-condição não comprovada: x", "cancelled") is None
    assert classificar_falha("Pós-condição não comprovada: x", "interrupted") is F.INTERROMPIDA
    assert classificar_falha(None, "failed") is F.OUTRO
    assert classificar_falha("Pós-condição não comprovada: x", "uncertain") is F.POS_CONDICAO_NAO_COMPROVADA
    assert classificar_falha("O app pede autenticação (2fa).", "waiting_user") is F.AUTENTICACAO
    # sem acento e sem caixa: o texto que chega truncado ou normalizado casa igual
    assert classificar_texto("POS-CONDICAO NAO COMPROVADA") is F.POS_CONDICAO_NAO_COMPROVADA


# ------------------------------------------------------------------ catraca por AST sobre o executor
def _literal(no: ast.expr) -> list[str]:
    """Texto de um literal ou f-string (as partes variáveis viram 'N'); IfExp dá os dois ramos; `com_anr(x)` é x."""
    if isinstance(no, ast.Constant) and isinstance(no.value, str):
        return [no.value]
    if isinstance(no, ast.JoinedStr):
        return ["".join(p.value if isinstance(p, ast.Constant) and isinstance(p.value, str) else "N"
                        for p in no.values)]
    if isinstance(no, ast.IfExp):
        return _literal(no.body) + _literal(no.orelse)
    if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id == "com_anr" and no.args:
        return _literal(no.args[0])
    if isinstance(no, ast.BinOp) and isinstance(no.op, ast.Add):
        esquerda, direita = _literal(no.left), _literal(no.right)
        return [a + b for a in esquerda for b in direita] if esquerda and direita else esquerda or direita
    return []


def _e_desfecho_de_falha(no: ast.expr) -> bool:
    nomes = {"failed", "uncertain", "waiting_user"}
    if isinstance(no, ast.Attribute) and isinstance(no.value, ast.Name) and no.value.id == "Outcome":
        return no.attr in nomes
    if isinstance(no, ast.IfExp):
        return _e_desfecho_de_falha(no.body) or _e_desfecho_de_falha(no.orelse)
    return False


def _motivos_do_executor() -> list[str]:
    arvore = ast.parse(EXECUTOR.read_text(encoding="utf-8"))
    motivos: list[str] = []
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call) or not no.args:
            continue
        nome = no.func.id if isinstance(no.func, ast.Name) else no.func.attr if isinstance(no.func, ast.Attribute) \
            else ""
        if nome == "fail_or_retry":
            motivos += _literal(no.args[0])
        elif nome == "StepOutcome" and len(no.args) >= 2 and _e_desfecho_de_falha(no.args[0]):
            motivos += _literal(no.args[1])
    return motivos


def test_todo_motivo_literal_do_executor_cai_fora_de_outro() -> None:
    motivos = _motivos_do_executor()
    assert len(motivos) >= 20, f"a varredura achou só {len(motivos)} motivos: o AST do executor mudou de forma"
    sem_regra = [m for m in motivos if classificar_texto(m) is F.OUTRO]
    assert len(sem_regra) <= OUTRO_NO_EXECUTOR, f"motivo novo do executor sem regra no classificador: {sem_regra}"
    assert len(sem_regra) == OUTRO_NO_EXECUTOR, \
        f"a contagem de 'outro' desceu para {len(sem_regra)}: baixe OUTRO_NO_EXECUTOR para travar o ganho"


# ------------------------------------------------------------------ camada e onde alterar
def test_todo_tipo_tem_camada_e_toda_camada_tem_onde_alterar() -> None:
    assert set(CAMADA) == set(FailureKind)
    assert set(ONDE_ALTERAR) == set(Camada)
    for tipo in FailureKind:
        destino = onde_alterar(tipo)
        assert destino.arquivos and destino.doc.startswith("docs/") and destino.prova
    assert camada_de(F.APP_ANR) is Camada.APARELHO and camada_de(F.OUTRO) is Camada.INDEFINIDA
    assert camada_de(F.POS_CONDICAO_NAO_COMPROVADA) is Camada.VERIFICACAO
    assert camada_de(F.IA_ORCAMENTO) is Camada.ORCAMENTO
    assert "falhas.py" in onde_alterar(F.OUTRO).arquivos[0]


def test_nunca_vira_licao_autenticacao_ia_e_infraestrutura() -> None:
    for tipo in (F.AUTENTICACAO, F.CONTA_ERRADA, F.IA_INDISPONIVEL, F.IA_RECUSA, F.IA_SALDO, F.IA_ORCAMENTO,
                 F.SESSAO_DE_AUTOMACAO, F.APP_ANR, F.UI_OCUPADA, F.APARELHO_TRAVADO, F.INTERROMPIDA, F.OUTRO):
        assert tipo in NUNCA_VIRA_LICAO
    for tipo in (F.ALVO_AUSENTE, F.CICLO_SEM_PROGRESSO, F.EFEITO_ALVO_ERRADO, F.POS_CONDICAO_NAO_COMPROVADA,
                 F.DEFEITO_DO_PLANO):
        assert tipo not in NUNCA_VIRA_LICAO
