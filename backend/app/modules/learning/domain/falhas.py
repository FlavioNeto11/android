"""Falha em vocabulário fechado (ADR-054, decisão 4).

Hoje o motivo de uma falha é texto livre (`attempts.error`) e nada agrega o que mais falha. Aqui o texto final de uma
tentativa (ou o detalhe final de uma etapa) vira UM tipo de `FailureKind`, por uma tabela de trechos em ordem — a
mesma função classifica o que a execução grava (pacote A2, em `repository.finish_attempt`) e o legado na leitura
(marcado retroativo, nunca gravado).

Não conhece app nenhum. Os trechos são os textos que o executor, o roteador de IA e a receita escrevem; uma catraca
por AST (`tests/test_learning_falhas.py`) confere que todo literal de falha do executor cai fora de `outro`, e a
porcentagem de `outro` no relatório diz quando falta regra nova.

A camada e o "onde alterar" NÃO são colunas: saem do tipo, na leitura, pela tabela `CAMADA` e `ONDE_ALTERAR`.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class FailureKind(StrEnum):
    # execução
    PRAZO_DA_ETAPA = "prazo_da_etapa"
    UI_OCUPADA = "ui_ocupada"
    APP_ANR = "app_anr"
    APARELHO_TRAVADO = "aparelho_travado"
    SESSAO_DE_AUTOMACAO = "sessao_de_automacao"
    INTERROMPIDA = "interrompida"
    # conta
    AUTENTICACAO = "autenticacao"
    CONTA_ERRADA = "conta_errada"
    # IA
    IA_INDISPONIVEL = "ia_indisponivel"
    IA_RECUSA = "ia_recusa"
    IA_ORCAMENTO = "ia_orcamento"
    IA_SALDO = "ia_saldo"
    IA_CHAMADA_INVALIDA = "ia_chamada_invalida"
    IA_DECLAROU_BLOQUEIO = "ia_declarou_bloqueio"
    # navegação e efeito
    CICLO_SEM_PROGRESSO = "ciclo_sem_progresso"
    ALVO_AUSENTE = "alvo_ausente"
    EFEITO_ALVO_ERRADO = "efeito_alvo_errado"
    EFEITO_GUARDA_NAO_ATENDIDA = "efeito_guarda_nao_atendida"
    EFEITO_NAO_COMPROVADO = "efeito_nao_comprovado"
    POS_CONDICAO_NAO_COMPROVADA = "pos_condicao_nao_comprovada"
    # coleta e digitação
    COLETA_VAZIA = "coleta_vazia"
    COLETA_INCOMPLETA = "coleta_incompleta"
    DIGITACAO_INCOMPLETA = "digitacao_incompleta"
    # outros
    DEFEITO_DO_PLANO = "defeito_do_plano"
    FALTA_INFORMACAO = "falta_informacao"
    OUTRO = "outro"


_F = FailureKind

#: Status de tentativa ou de etapa que NÃO são falha (nada a classificar). `cancelled` é decisão, não defeito.
STATUS_SEM_FALHA = frozenset({"succeeded", "running", "pending", "ready", "verifying", "retry_wait", "cancelled",
                              "skipped"})

#: Nunca viram lição (ADR-054, decisão 5): autenticação, desafio, conta, IA e infraestrutura. Lição sobre elas
#: ou é evasão (desafio, 2FA, CAPTCHA seguem com a pessoa — ADR-009) ou não ensina navegação nenhuma.
NUNCA_VIRA_LICAO = frozenset({_F.AUTENTICACAO, _F.CONTA_ERRADA, _F.IA_INDISPONIVEL, _F.IA_RECUSA, _F.IA_ORCAMENTO,
                              _F.IA_SALDO, _F.IA_CHAMADA_INVALIDA, _F.IA_DECLAROU_BLOQUEIO, _F.SESSAO_DE_AUTOMACAO,
                              _F.APP_ANR, _F.UI_OCUPADA, _F.APARELHO_TRAVADO, _F.INTERROMPIDA, _F.OUTRO})


def _normal(texto: str) -> str:
    """Sem acento e sem caixa: o executor escreve "autenticação", o provedor às vezes "autenticacao"."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


#: (tipo, trechos) em ORDEM: a primeira regra com um trecho contido no texto normalizado ganha. A ordem importa onde
#: um texto carrega dois motivos: o prazo que vence depois de um ANR diz o ANR ("…esgotado (180s); o <app> parou de
#: responder (ANR)…", r-20260928165254-e31953), e o prazo vencido DURANTE a chamada de IA é prazo, não IA.
REGRAS: tuple[tuple[FailureKind, tuple[str, ...]], ...] = (
    (_F.APP_ANR, ("parou de responder (anr)",)),
    (_F.INTERROMPIDA, ("tentativa interrompida",)),
    (_F.AUTENTICACAO, ("pede autenticacao", "consentimento_pendente", "auth_required", "tela de login",
                       "login manualmente")),
    # "não há UMA conta da pessoa" (item 24.4): a etapa confere a conta e não há conta certa para conferir — é a conta
    # da pessoa no app, não navegação; como a conta errada, fica com ela e nunca vira lição.
    (_F.CONTA_ERRADA, ("wrong_account", "conta errada", "outra conta logada", "nao ha uma conta da pessoa")),
    (_F.PRAZO_DA_ETAPA, ("tempo da etapa esgotado", "prazo da etapa", "passou do prazo restante da etapa")),
    (_F.IA_RECUSA, ("recusou esta requisicao por politica", "recusou a requisicao por politica",
                    "recusou verificar esta etapa por politica", "recusou todas as variacoes")),
    # legado: o texto de um `AIError` é o que o executor escreveu em `attempts.error`; o TIPO (`AIError.kind`) chega por
    # `ai_calls.error_kind` e vence o texto onde existir (`classificar_pelo_tipo_da_ia`). Estas regras ficam para a
    # tentativa sem `ai_calls` (além da purga) e para o texto que o tipo não cobre (a catraca por AST depende delas).
    (_F.IA_SALDO, ("sem credito", "recarregue no console", "recarregue o credito")),  # legado
    (_F.IA_ORCAMENTO, ("teto de gasto de ia", "chamadas de ia por objetivo", "orcamento de")),  # legado
    # O valor que a etapa entrega às seguintes (item 24.3) só existe se o ator chamar `read_value`: como o
    # `collect_list` da coleta, deixar de ler (ou insistir em leitura inválida) é conduta do ator com a ferramenta. E
    # concluir na tela de outro app (item 24.7) também: o executor disse qual app abrir e o ator não abriu.
    (_F.IA_CHAMADA_INVALIDA, ("insistiu em chamadas invalidas", "nao usou collect_list", "acao da receita invalida",
                              "concluiu a etapa sem ler o valor", "o valor da etapa nao foi lido",
                              "entrega as seguintes nao foi lido", "concluir a etapa fora do app dela")),
    (_F.IA_INDISPONIVEL, ("ia indisponivel", "verificacao nao pode ser feita", "sem chave configurada",
                          "chave da anthropic invalida", "credencial recusada por", "falha de rede ao contatar",
                          "limite de requisicoes", "resposta do modelo truncada", "sem chamar nenhuma ferramenta",
                          "sem endpoint configurado")),
    (_F.UI_OCUPADA, ("interface do aparelho seguiu ocupada", "leitura da tela seguiu falhando")),
    (_F.APARELHO_TRAVADO, ("chamada ao aparelho travada", "tempo esgotado numa chamada ao aparelho")),
    (_F.SESSAO_DE_AUTOMACAO, ("sessao de automacao indisponivel", "nao foi possivel observar a tela",
                              "falhas consecutivas do driver")),
    # A etapa cita `{{saida:…}}` que nenhuma etapa anterior leu (item 24.3): o plano ligou mal as etapas.
    (_F.DEFEITO_DO_PLANO, ("defeito do plano", "sem ele ter sido lido por uma etapa anterior")),
    (_F.EFEITO_NAO_COMPROVADO, ("efeito foi disparado, mas nao foi possivel compro",)),
    (_F.EFEITO_ALVO_ERRADO, ("efeito externo foi tentado no elemento errado", "controle de outra publicacao",
                             "alvo do efeito externo")),
    (_F.EFEITO_GUARDA_NAO_ATENDIDA, ("pre-condicoes do efeito externo", "guarda do efeito externo")),
    (_F.CICLO_SEM_PROGRESSO, ("ciclo sem progresso", "acoes por etapa atingido")),
    (_F.COLETA_VAZIA, ("coleta nao encontrou nenhum item",)),
    (_F.COLETA_INCOMPLETA, ("lista nao chegou ao fim", "itens; o limite e")),
    (_F.DIGITACAO_INCOMPLETA, ("texto continua incompleto", "o texto nao foi comprovado",
                               "saiu de foco antes de completar", "digitacao incompleta")),
    (_F.POS_CONDICAO_NAO_COMPROVADA, ("pos-condicao nao comprovada", "pos-condicao nao apareceu")),
    (_F.ALVO_AUSENTE, ("alvo ausente", "nao achou o alvo", "elemento nao encontrado")),
    (_F.FALTA_INFORMACAO, ("parametro ausente", "missing_info", "falta informacao")),
    (_F.IA_DECLAROU_BLOQUEIO, ("bloqueio relatado pela ia", "step_blocked")),
)


def classificar_texto(texto: str | None) -> FailureKind:
    """O tipo do texto de falha, sem olhar o status. Texto vazio ou sem regra é `outro`."""
    t = _normal(texto or "")
    if not t.strip():
        return _F.OUTRO
    for tipo, trechos in REGRAS:
        if any(trecho in t for trecho in trechos):
            return tipo
    return _F.OUTRO


def classificar_falha(error: str | None, status: str | None, error_kind: str | None = None) -> FailureKind | None:
    """O tipo de uma tentativa (ou etapa) no seu status FINAL. `None` quando o status não é falha.

    `interrupted` é sempre `interrompida`, qualquer que seja o texto: é a reconciliação de partida
    (`scheduler._reconciliar`) quem fecha, e o texto guardado pode ser o erro anterior da mesma tentativa.

    `error_kind` (RA-22): o `AIError.kind` que encerrou a tentativa (`attempts.error_kind`). Quando decide
    (`pelo_erro_que_encerrou`), vence o texto: a mensagem do executor deixa de ser contrato. Sem ele, o texto.
    """
    s = (status or "").strip()
    if s in STATUS_SEM_FALHA:
        return None
    if s == "interrupted":
        return _F.INTERROMPIDA
    return pelo_erro_que_encerrou(error_kind) or classificar_texto(error)


#: `AIError.kind` (planning/provider.py) → tipo da falha, em ORDEM de precedência. Só os tipos que ENCERRAM a chamada
#: (o roteador não troca de modelo diante de `budget` nem de `refusal`, e conta e chave pedem a pessoa): um deles gravado
#: numa chamada da tentativa que falhou é a causa dela, qualquer que seja o texto. `step_deadline`, `invalid_output` e
#: `error` não entram: o prazo vence com ou sem IA, e os outros dois não dizem por que a etapa falhou.
_DO_TIPO_DE_ERRO_DE_IA: tuple[tuple[str, FailureKind], ...] = (
    ("budget", _F.IA_ORCAMENTO), ("billing", _F.IA_SALDO), ("balance", _F.IA_SALDO), ("refusal", _F.IA_RECUSA),
    ("not_configured", _F.IA_INDISPONIVEL),
)


def classificar_pelo_tipo_da_ia(tipos: Iterable[str]) -> FailureKind | None:
    """O tipo da falha dado pelos `AIError.kind` gravados em `ai_calls.error_kind` da tentativa; `None` sem nenhum
    dos que decidem. Não olha texto: o teto atingido do pedido ("Orçamento do pedido atingido…") fugia da regra por
    trecho e caía em `outro`."""
    vistos = set(tipos)
    return next((tipo for kind, tipo in _DO_TIPO_DE_ERRO_DE_IA if kind in vistos), None)


#: RA-22: o `AIError.kind` que ENCERROU a tentativa (`attempts.error_kind`) → tipo. Ao contrário de `ai_calls` (uma
#: chamada que falhou e o roteador contornou não diz nada), aqui o erro é a causa; por isso `error` e `invalid_output`
#: entram (a IA não deu resposta usável: é o "IA indisponível" que o executor escreve). `step_deadline` não entra: o
#: prazo vence com ou sem IA, e o ANR anotado no texto (`com_anr`) ganha dele pelas REGRAS.
_DO_ERRO_QUE_ENCERROU: Mapping[str, FailureKind] = {
    **dict(_DO_TIPO_DE_ERRO_DE_IA), "error": _F.IA_INDISPONIVEL, "invalid_output": _F.IA_INDISPONIVEL,
}


def pelo_erro_que_encerrou(error_kind: str | None) -> FailureKind | None:
    """O tipo dado pelo erro de IA que encerrou a tentativa; `None` sem ele, com `step_deadline` ou com kind que o
    mapa não conhece (o texto decide)."""
    return _DO_ERRO_QUE_ENCERROU.get(error_kind or "")


#: `recipes.motivo_do_retorno` (o vocabulário do funil de receitas) → tipo. `outro` do funil continua `outro`.
_DO_RETORNO_DE_RECEITA: Mapping[str, FailureKind] = {
    "acao_invalida": _F.IA_CHAMADA_INVALIDA,
    "alvo_do_efeito": _F.EFEITO_ALVO_ERRADO,
    "guarda_do_efeito": _F.EFEITO_GUARDA_NAO_ATENDIDA,
    "parametro_ausente": _F.FALTA_INFORMACAO,
    "pos_condicao": _F.POS_CONDICAO_NAO_COMPROVADA,
    "alvo_ausente": _F.ALVO_AUSENTE,
}


def falha_do_retorno_de_receita(motivo: str) -> FailureKind:
    return _DO_RETORNO_DE_RECEITA.get(motivo, _F.OUTRO)


# ------------------------------------------------------------------ camada e onde alterar
class Camada(StrEnum):
    IA_ATOR = "ia_ator"
    PLANO = "plano"
    VERIFICACAO = "verificacao"
    CONHECIMENTO_DO_APP = "conhecimento_do_app"
    APARELHO = "aparelho"
    AUTOMACAO = "automacao"
    CONTA_SESSAO = "conta_sessao"
    PROVEDOR_IA = "provedor_ia"
    ORCAMENTO = "orcamento"
    EXECUCAO = "execucao"            # a fila e a reconciliação de partida (tentativa interrompida)
    PESSOA = "pessoa"                # falta a informação que só quem pediu tem; fica fora do backlog por padrão
    INDEFINIDA = "indefinida"        # `outro`: o classificador precisa de regra nova


_C = Camada

CAMADA: Mapping[FailureKind, Camada] = {
    _F.PRAZO_DA_ETAPA: _C.APARELHO, _F.UI_OCUPADA: _C.APARELHO, _F.APP_ANR: _C.APARELHO,
    _F.APARELHO_TRAVADO: _C.APARELHO,
    _F.SESSAO_DE_AUTOMACAO: _C.AUTOMACAO, _F.DIGITACAO_INCOMPLETA: _C.AUTOMACAO,
    _F.INTERROMPIDA: _C.EXECUCAO,
    _F.AUTENTICACAO: _C.CONTA_SESSAO, _F.CONTA_ERRADA: _C.CONTA_SESSAO,
    _F.IA_INDISPONIVEL: _C.PROVEDOR_IA, _F.IA_RECUSA: _C.PROVEDOR_IA, _F.IA_SALDO: _C.PROVEDOR_IA,
    _F.IA_ORCAMENTO: _C.ORCAMENTO,
    _F.IA_CHAMADA_INVALIDA: _C.IA_ATOR, _F.IA_DECLAROU_BLOQUEIO: _C.IA_ATOR, _F.CICLO_SEM_PROGRESSO: _C.IA_ATOR,
    _F.ALVO_AUSENTE: _C.IA_ATOR, _F.EFEITO_ALVO_ERRADO: _C.IA_ATOR, _F.EFEITO_GUARDA_NAO_ATENDIDA: _C.IA_ATOR,
    _F.EFEITO_NAO_COMPROVADO: _C.VERIFICACAO, _F.POS_CONDICAO_NAO_COMPROVADA: _C.VERIFICACAO,
    _F.COLETA_VAZIA: _C.CONHECIMENTO_DO_APP, _F.COLETA_INCOMPLETA: _C.CONHECIMENTO_DO_APP,
    _F.DEFEITO_DO_PLANO: _C.PLANO,
    _F.FALTA_INFORMACAO: _C.PESSOA,
    _F.OUTRO: _C.INDEFINIDA,
}


@dataclass(frozen=True, slots=True)
class OndeAlterar:
    """Para a sessão de desenvolvimento: onde mexer e como provar a correção."""

    arquivos: tuple[str, ...]
    doc: str
    prova: str


ONDE_ALTERAR: Mapping[Camada, OndeAlterar] = {
    _C.IA_ATOR: OndeAlterar(("backend/app/planning/prompts.py", "backend/app/modules/learning (lições)"),
                            "docs/ia.md", "execução de navegação no mesmo app; chamadas por etapa antes × depois"),
    _C.PLANO: OndeAlterar(("backend/app/planning/prompts.py (PLANNER_*)",
                           "backend/app/conhecimento/apps/<pacote>/catalogo.yaml"),
                          "docs/ia.md", "planejar o mesmo comando sem executar (plano/prévia) e conferir a etapa"),
    _C.VERIFICACAO: OndeAlterar(("backend/app/taskqueue/executor.py (_postcondition_holds)",
                                 "backend/app/planning/prompts.py (VERIFIER_SYSTEM)", "pós-condições do catálogo"),
                                "docs/ia.md", "a mesma etapa comprovada pela tela, sem confirmação à mão"),
    _C.CONHECIMENTO_DO_APP: OndeAlterar(("backend/app/conhecimento/apps/<pacote>/telas.yaml",
                                         "backend/app/conhecimento/apps/<pacote>/sessao.yaml",
                                         "fatia 5 (telas aprendidas)"),
                                        "docs/design/conhecimento-de-app.md",
                                        "caso novo em tests/test_conhecimento_de_telas.py e uma execução real"),
    _C.APARELHO: OndeAlterar(("backend/app/devices/manager.py",), "docs/dominios/parque.md",
                             "a mesma etapa no mesmo aparelho, com a medida de CPU/ANR do convidado"),
    _C.AUTOMACAO: OndeAlterar(("backend/app/automation/appium_driver.py", "backend/app/automation/driver.py"),
                              "docs/arquitetura.md", "a ação no aparelho real, com a conferência do driver"),
    _C.CONTA_SESSAO: OndeAlterar(("backend/app/integrations/app_declarado/sessao.py",),
                                 "docs/design/conhecimento-de-app.md",
                                 "a checagem de sessão na conta real (desafio e 2FA seguem com a pessoa)"),
    _C.PROVEDOR_IA: OndeAlterar(("backend/app/planning/*_provider.py", "backend/app/planning/routing.py"),
                                "docs/ia.md", "GET /api/ai e uma chamada pontual ao provedor (ADR-049)"),
    _C.ORCAMENTO: OndeAlterar(("backend/app/taskqueue/projecao.py",), "docs/ia.md",
                              "GET /api/runs/{id}/projection antes e depois"),
    _C.EXECUCAO: OndeAlterar(("backend/app/taskqueue/scheduler.py (_reconciliar)",),
                             "docs/dominios/execution.md", "reinício do central com uma execução em andamento"),
    _C.PESSOA: OndeAlterar(("o comando ou a pergunta ao dono (needs_input)",), "docs/produto.md",
                           "a mesma intenção com a informação no comando"),
    _C.INDEFINIDA: OndeAlterar(("backend/app/modules/learning/domain/falhas.py (regra nova)",),
                               "docs/ia.md", "a porcentagem de 'outro' no relatório desce"),
}


def camada_de(tipo: FailureKind) -> Camada:
    return CAMADA[tipo]


def onde_alterar(tipo: FailureKind) -> OndeAlterar:
    return ONDE_ALTERAR[CAMADA[tipo]]
