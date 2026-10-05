"""A validação automática do que o curador manda "pedir evidência" (item 30.31; desenho aprovado pela orquestradora em
03/10). Puro: recebe fatos já lidos e devolve decisões; quem lê o banco, grava o pedido e enfileira a execução é a
aplicação (`application/validacao.py`).

O laço: o parecer `pedir_evidencia` cuja falta uma execução produz (execução real, outro aparelho, a versão viva do
app, a sombra) vira um PEDIDO; um despachante roda a execução de validação, o comando de origem noutro aparelho
ocioso; a evidência entra pelos caminhos de sempre e dispara a revisão `evidencia_chegou`. O pedido nunca decide:
quem transiciona é o ciclo do livro (A pela regra D1; B vai ao lote do dono até a P2; C item a item).

Regras do desenho que moram aqui:
- `decisao_da_pessoa` e `voto_da_pessoa` não são evidência (é a política da classe): o pedido os ignora;
- o QA Messenger é app nosso e só grava no aparelho: a validação nele pode FAZER o efeito (P1, sim da orquestradora);
- efeito em app real (Instagram, Outlook) não roda sozinho: o ensaio só de leitura até antes do commit é a fatia 2, e
  o commit só em post nosso (decisão do dono de 02/10);
- receita só se valida com fluxo ATIVO para o comando: sem ele o planejador replaneja, a chave da etapa pode mudar e a
  execução gasta sem provar nada; e só se o plano desse fluxo CHEGA à etapa dela (30.36, `sem_caminho`: a variante
  antiga da mesma etapa, de outra redação da pós-condição, nunca é a que roda);
- o fechamento segue a evidência que a execução deixou (30.36): a favor fecha `feita`; contra, `evidencia_contra`, e
  o curador volta ao item; só a forma reescrita, `divergencia_de_forma`; nada, `sem_evidencia`;
- desligado não se valida: a pessoa o devolve à prova antes (item 0);
- o despachante só roda em aparelho ocioso, com o central saudável e sem execução em curso (restart, suíte e deploy
  derrubam isso), dentro do orçamento β = 5 % do gasto de IA da operação na janela e no máximo 4 execuções por hora.
"""
from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.curador import Decisao, Falta
from app.modules.learning.domain.prova import motivo_da_invalida
from app.modules.learning.domain.vocabulario import LivroKind

#: O que uma execução de validação produz. `decisao_da_pessoa`/`voto_da_pessoa` ficam de fora: são a política.
FALTA_AUTOMATIZAVEL = frozenset({Falta.EXECUCAO_REAL, Falta.REPRODUCAO_EM_OUTRO_APARELHO,
                                 Falta.REPRODUCAO_NA_VERSAO_VIVA, Falta.SOMBRA})
#: β do desenho: a fração do gasto de IA da operação na janela que a validação pode usar (sem teto fixo em US$).
BETA_PADRAO = 0.05
#: Execuções de validação por hora, no máximo (o parque é compartilhado com o uso).
MAXIMO_POR_HORA = 4
#: 30.50: as variáveis do plano que dependem do APARELHO e podem faltar nele (o executor as preenche por aparelho;
#: `instance_id` e `run_id` nunca faltam). A prova em aparelho sem uma delas vira `""` e a etapa não tem como passar:
#: em 04/10 a fec1a1 caiu no android-04, sem `account_label`, e fechou `ator_sem_acao` no `check_account`.
VARIAVEIS_DE_APARELHO = ("account_label",)

#: 30.42: provas da MESMA versão do conteúdo do fluxo que o item aceita na janela; a seguinte fecha `limite_de_provas`.
#: 29.75 (04/10, orquestradora): 2 → 4. Medido em 7 dias: 1 recusa `limite_de_provas`; o ambiente é de calibração.
MAXIMO_DE_PROVAS = 4
JANELA_DE_PROVAS_DIAS = 7
#: 30.41 (orquestradora, 03/10): o teto de IA da prova de fluxo é proporcional ao plano, `min(0,40; 0,05 + 0,02 ×
#: etapas)`; o plano cujo teto passaria do máximo não despacha. Medido no P4: o `for_each` de 8 contatos gastou US$ 0,157
#: contra o teto fixo de 0,15 depois de 3 envios (inteiro custaria 0,33 a 0,36); as provas de 6 e 11 etapas, 0,07 a 0,12.
TETO_DA_PROVA_PISO_USD = 0.05
TETO_DA_PROVA_POR_ETAPA_USD = 0.02
#: 29.75 (04/10, orquestradora): máximo 0,40 → 0,80. Medido em 7 dias: 3 recusas `plano_acima_do_teto`.
TETO_DA_PROVA_MAXIMO_USD = 0.80
#: O pedido que não rodou em 72 h expira (o item muda, a volta seguinte pede de novo se ainda faltar).
VALIDADE_DO_PEDIDO_H = 72


class Grupo(StrEnum):
    """O que a execução de validação pode fazer."""

    QA = "qa"                    # app de QA (nosso, efeito só no aparelho): pode fazer o efeito
    LEITURA = "leitura"          # sem efeito: só navega e lê
    EFEITO_REAL = "efeito_real"  # efeito em app real: só ensaio de leitura até o commit (fatia 2)


class EstadoDoPedido(StrEnum):
    PENDENTE = "pendente"
    RODANDO = "rodando"
    FEITA = "feita"
    RECUSADA = "recusada"
    EXPIRADA = "expirada"


VIVOS = frozenset({EstadoDoPedido.PENDENTE, EstadoDoPedido.RODANDO})


class Motivo(StrEnum):
    """Por que o pedido não nasce vivo, não roda agora, ou rodou sem provar. Vocabulário fechado (vai à coluna)."""

    # ao nascer (o pedido nasce `recusada` com o motivo: o painel e as métricas mostram por que não se validou)
    SEM_FALTA_AUTOMATIZAVEL = "sem_falta_automatizavel"
    TIPO_SEM_EXECUCAO = "tipo_sem_execucao"           # lição, tela, habilidade: a evidência vem por outro caminho
    DESLIGADO = "desligado"                           # devolver à prova antes (item 0)
    VETADO = "vetado"
    SESSAO = "sessao_ou_autenticacao"                 # entrar na conta fica com a pessoa (ADR-009/040)
    SEM_ORIGEM = "sem_origem"                         # a execução que ensinou foi purgada: não há comando
    CREDENCIAL = "credencial"                         # o comando parece ter credencial (triagem de sempre)
    EFEITO_REAL = "efeito_real"                       # efeito em app real: o ensaio de leitura é a fatia 2
    SEM_FLUXO_ATIVO = "sem_fluxo_ativo"               # receita sem fluxo ativo para o comando: a chave pode mudar
    #: 30.36: o plano do fluxo ativo do comando não chega à etapa desta receita (outra variante dela é a que roda: a
    #: pós-condição reescrita do RA-20). 30.37: no fluxo, o comando de origem não cabe mais no molde dele (a execução de
    #: prova não teria os parâmetros). Recusa também AO DESPACHAR, sem execução, porque o fluxo pode mudar.
    SEM_CAMINHO = "sem_caminho"
    #: 30.42: recusas AO DESPACHAR, sem execução nem gasto: o item já teve 2 provas da mesma versão do conteúdo em 7
    #: dias; ou a falta pede outro aparelho e nenhum aparelho que serve ficou fora dos já usados (origem e provas).
    LIMITE_DE_PROVAS = "limite_de_provas"
    SEM_APARELHO_NOVO = "sem_aparelho_novo"
    #: 30.41: o plano que a execução rodaria (o do fluxo em prova, ou o fluxo ativo do comando da receita) passa do teto
    #: máximo (`teto_da_prova`), ou o tamanho dele não se sabe. Fecha AO DESPACHAR: sem gasto e sem envio pela metade.
    PLANO_ACIMA_DO_TETO = "plano_acima_do_teto"
    # ao despachar (o pedido fica `pendente` e tenta na volta seguinte)
    AMBIENTE_OCUPADO = "ambiente_ocupado"             # health com problema, execução em curso (restart/suíte/deploy)
    SEM_APARELHO = "sem_aparelho"                     # nenhum aparelho ocioso que sirva
    ORCAMENTO = "orcamento"                           # β da janela gasto
    RITMO = "ritmo"                                   # já rodaram MAXIMO_POR_HORA na última hora
    # ao fechar
    SEM_EVIDENCIA = "sem_evidencia"                   # a execução assentou e não deixou evidência no item
    #: 30.75: duas causas de "sem evidência" que a execução de prova registra de forma estruturada. Nenhuma diz nada
    #: sobre o item (não é chegada do curador, não é contra, não se reabre sozinha): só deixam a medida dizer o que
    #: travou e quanto custou.
    ORCAMENTO_DA_PROVA = "orcamento_da_prova"         # o teto do pedido cortou a execução no meio (`error_kind=budget`)
    APP_SEM_SESSAO = "app_sem_sessao"                 # o app pediu login no aparelho (`objectives.blocked_kind=auth`)
    EVIDENCIA_CONTRA = "evidencia_contra"             # 30.36: deixou evidência CONTRA (o curador volta ao item)
    DIVERGENCIA_DE_FORMA = "divergencia_de_forma"     # 30.36: fez o caminho e só reescreveu a forma (nem a favor)
    #: 30.42: a prova deixou a linha `invalida` (nem a favor nem contra); o motivo é o dela (`domain/prova.py`)
    EFEITO_REPETIDO = "efeito_repetido"
    PONTO_DE_PARTIDA = "ponto_de_partida"
    ATOR_SEM_ACAO = "ator_sem_acao"
    EXECUCAO_FALHOU = "execucao_falhou"
    EXPIROU = "expirou"
    #: 30.31 (fatia 2): o ensaio só de leitura parou antes da etapa com efeito, como devia; não é evidência do fluxo
    ENSAIO_SO_LEITURA = "ensaio_so_leitura"
    #: 30.52: a PESSOA recusou o pedido ainda pendente (`POST /validacoes/{id}/recusar`): sem execução nem gasto, e não
    #: pesa contra o item (não é chegada para o curador, não é contra, não é contestação).
    RECUSADA_PELA_PESSOA = "recusada_pela_pessoa"


#: 30.38 (b): o motivo como a pessoa lê, no painel (Aprendizado › Validação). O código fica no `title`; nunca sai cru na
#: tela. Um texto para cada valor de `Motivo` (o teste reprova o motivo novo sem texto).
MOTIVO_HUMANO: Mapping[Motivo, str] = {
    Motivo.SEM_FALTA_AUTOMATIZAVEL: "Nada do que falta ao parecer sai de uma execução.",
    Motivo.TIPO_SEM_EXECUCAO: "Este tipo de item (lição, tela, habilidade) ganha evidência por outro caminho.",
    Motivo.DESLIGADO: "O item está desligado: volta à prova antes de ser validado.",
    Motivo.VETADO: "O conteúdo do item foi vetado; não se valida.",
    Motivo.SESSAO: "O comando entra numa conta: isso fica com a pessoa.",
    Motivo.SEM_ORIGEM: "A execução que ensinou o item já foi apagada; não há comando para repetir.",
    Motivo.CREDENCIAL: "O comando de origem parece ter uma credencial; não se repete.",
    Motivo.EFEITO_REAL: "O comando tem efeito num app real; por enquanto só a leitura se valida.",
    Motivo.SEM_FLUXO_ATIVO: "Não há fluxo ativo para este comando.",
    Motivo.SEM_CAMINHO: "O fluxo de agora não passa mais pela etapa deste item.",
    # 30.42 e 30.41: recusas ao despachar (sem execução nem gasto) e os fechamentos pela linha `invalida` da prova
    Motivo.LIMITE_DE_PROVAS: "O item já teve 2 provas desta versão nos últimos 7 dias.",
    Motivo.SEM_APARELHO_NOVO: "Pedia outro aparelho, e todos os que servem já foram usados nas provas.",
    Motivo.PLANO_ACIMA_DO_TETO: "O plano é grande demais para o teto de uma prova (ou o tamanho não se sabe).",
    Motivo.EFEITO_REPETIDO: "O efeito saiu mais de uma vez na execução: ela não vale como evidência.",
    Motivo.PONTO_DE_PARTIDA: "A abertura não chegou ao ponto de partida do fluxo: a prova não vale.",
    Motivo.ATOR_SEM_ACAO: "O ator declarou a etapa pronta sem agir: a prova não vale.",
    Motivo.ENSAIO_SO_LEITURA: ("Ensaio só de leitura: percorreu o fluxo e parou antes do efeito, como devia; não conta "
                               "como prova do fluxo."),
    Motivo.AMBIENTE_OCUPADO: "O central estava ocupado (reinício, suíte, implantação ou saúde); tenta na volta seguinte.",
    Motivo.SEM_APARELHO: "Nenhum aparelho ocioso servia; tenta na volta seguinte.",
    Motivo.ORCAMENTO: "O orçamento de validação desta janela acabou.",
    Motivo.RITMO: "Já rodou o máximo de validações desta hora.",
    Motivo.SEM_EVIDENCIA: "A execução terminou sem deixar evidência no item.",
    Motivo.ORCAMENTO_DA_PROVA: "O teto da prova cortou a execução no meio; o gasto não deixou evidência.",
    Motivo.APP_SEM_SESSAO: ("O app pediu login no aparelho escolhido; a prova parou ali. O aparelho sai das próximas "
                            "provas deste app até uma verificação nova."),
    Motivo.EVIDENCIA_CONTRA: "A execução deixou evidência contra o item; o curador volta a ele.",
    Motivo.DIVERGENCIA_DE_FORMA: "A execução fez o caminho, mas só reescreveu a forma; não conta a favor.",
    Motivo.EXECUCAO_FALHOU: "A execução de validação falhou.",
    Motivo.EXPIROU: "O pedido passou do prazo sem rodar.",
    Motivo.RECUSADA_PELA_PESSOA: "Uma pessoa recusou o pedido antes de ele rodar; não pesa contra o item.",
}


def motivo_humano(motivo: str | None) -> str | None:
    """O texto do motivo gravado; `None` sem motivo. Um código fora do vocabulário (linha de versão futura) volta ele
    mesmo: melhor o código do que um texto inventado."""
    if not motivo:
        return None
    try:
        return MOTIVO_HUMANO[Motivo(motivo)]
    except ValueError:
        return motivo


@dataclass(frozen=True, slots=True)
class FatosDoParecer:
    """O que decide se um parecer vira pedido. `efeito`: o item tem ação ou etapa de efeito externo; `app_qa`: o app
    é da categoria QA (`apps.category='qa'`); `fluxo_ativo`: há fluxo ativo cujo modelo casa o comando de origem (só
    importa para a receita); `caminho` (30.36/30.37): o plano desse fluxo chega à etapa da receita, ou, no fluxo, o comando
    de origem cabe no molde dele (nos outros tipos, sim)."""

    decisao: Decisao
    falta: tuple[Falta, ...]
    kind: LivroKind
    estado: SkillState | None
    vetado: bool
    toca_sessao: bool
    efeito: bool
    app_qa: bool
    comando: str | None
    comando_com_credencial: bool
    fluxo_ativo: bool
    caminho: bool = True


@dataclass(frozen=True, slots=True)
class Pedido:
    """O que o parecer gera: `estado` PENDENTE (vai à fila) ou RECUSADA (registro do porquê)."""

    estado: EstadoDoPedido
    grupo: Grupo
    falta: tuple[Falta, ...]
    motivo: Motivo | None = None


def falta_automatizavel(falta: Iterable[Falta]) -> tuple[Falta, ...]:
    """Os rótulos que uma execução produz, sem repetição e na ordem do vocabulário."""
    pedidos = set(falta)
    return tuple(f for f in Falta if f in pedidos and f in FALTA_AUTOMATIZAVEL)


def grupo_de(*, efeito: bool, app_qa: bool) -> Grupo:
    if not efeito:
        return Grupo.LEITURA
    return Grupo.QA if app_qa else Grupo.EFEITO_REAL


def pedido_do_parecer(f: FatosDoParecer) -> Pedido | None:
    """O pedido que este parecer gera, ou `None` quando não há o que pedir (parecer que não é `pedir_evidencia`, ou
    só falta o que é da pessoa). As recusas nascem como registro, na ordem: a primeira que vale explica."""
    if f.decisao is not Decisao.PEDIR_EVIDENCIA:
        return None
    falta = falta_automatizavel(f.falta)
    if not falta:
        return None
    grupo = grupo_de(efeito=f.efeito, app_qa=f.app_qa)
    motivo = _recusa(f, grupo)
    estado = EstadoDoPedido.PENDENTE if motivo is None else EstadoDoPedido.RECUSADA
    return Pedido(estado, grupo, falta, motivo)


def _recusa(f: FatosDoParecer, grupo: Grupo) -> Motivo | None:
    if f.kind not in (LivroKind.RECEITA, LivroKind.FLUXO):
        return Motivo.TIPO_SEM_EXECUCAO
    if f.estado is SkillState.DISABLED:
        return Motivo.DESLIGADO
    if f.vetado:
        return Motivo.VETADO
    if f.toca_sessao:
        return Motivo.SESSAO
    if not (f.comando or "").strip():
        return Motivo.SEM_ORIGEM
    if f.comando_com_credencial:
        return Motivo.CREDENCIAL
    if grupo is Grupo.EFEITO_REAL:
        return Motivo.EFEITO_REAL
    if f.kind is LivroKind.RECEITA and not f.fluxo_ativo:
        return Motivo.SEM_FLUXO_ATIVO
    if not f.caminho:                       # receita: a variante de outra etapa; fluxo: o comando não cabe no molde (30.37)
        return Motivo.SEM_CAMINHO
    return None


# ------------------------------------------------------------------ o despachante
@dataclass(frozen=True, slots=True)
class Ambiente:
    """O central agora: `saudavel` = `GET /api/health` sem `problems`; `execucoes_em_curso` conta as execuções que não
    assentaram (restart, suíte e deploy as derrubam ou as seguram)."""

    saudavel: bool
    execucoes_em_curso: int


@dataclass(frozen=True, slots=True)
class AparelhoCandidato:
    id: str
    online: bool
    ocioso: bool
    tem_o_app: bool
    conta_real: bool              # há conta real de terceiro logada (Instagram, Outlook) neste aparelho


@dataclass(frozen=True, slots=True)
class Folego:
    """O que a janela ainda permite. `g_w`: gasto de IA da OPERAÇÃO na janela (sem curadoria nem validação), o mesmo do
    curador; `gasto_w`: o que a validação já gastou na janela; `extra_usd`: a verba única vigente (P4), somada ao β."""

    g_w: float
    gasto_w: float
    na_ultima_hora: int
    beta: float = BETA_PADRAO
    extra_usd: float = 0.0
    maximo_por_hora: int = MAXIMO_POR_HORA

    @property
    def orcamento(self) -> float:
        return max(0.0, self.beta) * max(0.0, self.g_w) + max(0.0, self.extra_usd)


def pode_despachar(ambiente: Ambiente, folego: Folego, *, custo_estimado: float) -> Motivo | None:
    """`None` quando esta volta pode rodar mais uma execução de validação; senão, por que não (o pedido espera)."""
    if not ambiente.saudavel or ambiente.execucoes_em_curso > 0:
        return Motivo.AMBIENTE_OCUPADO
    if folego.na_ultima_hora >= folego.maximo_por_hora:
        return Motivo.RITMO
    if folego.gasto_w + max(0.0, custo_estimado) > folego.orcamento:
        return Motivo.ORCAMENTO
    return None


def escolher_aparelho(grupo: Grupo, aparelhos: Sequence[AparelhoCandidato], *,
                      excluido: str | Collection[str] | None) -> str | None:
    """O aparelho da validação: online, ocioso, com o app, fora dos EXCLUÍDOS e SEM conta real logada. `excluido` é um
    aparelho (a origem, como sempre) ou um conjunto (30.42: origem + aparelhos das provas anteriores). Determinístico:
    entre os iguais, o menor id.

    Fatia 1 (03/10): nenhum grupo roda onde há conta real, nem a leitura. A regra do dono é conferir a tela antes de
    qualquer experimento numa conta real ("Confirm you're human" é conta bloqueada, e nada toca nela), e o despachante
    não confere tela. O pedido de leitura de um app que só existe logado em conta real (o Instagram de hoje) espera e
    expira; levar a leitura às contas reais é decisão da orquestradora."""
    if grupo is Grupo.EFEITO_REAL:
        return None
    fora = _conjunto(excluido)
    servem = [a for a in aparelhos if a.online and a.ocioso and a.tem_o_app and a.id not in fora and not a.conta_real]
    return min(servem, key=lambda a: a.id).id if servem else None


def _conjunto(excluido: str | Collection[str] | None) -> frozenset[str]:
    if excluido is None:
        return frozenset()
    return frozenset({excluido}) if isinstance(excluido, str) else frozenset(excluido)


def excluidos_da_validacao(falta: Iterable[str], *, origem: str | None, provas: Iterable[str | None]) -> frozenset[str]:
    """30.42: os aparelhos em que a validação NÃO roda. Só a origem, como sempre; com `reproducao_em_outro_aparelho`
    na falta, também os aparelhos onde o item já foi provado (reproduzir noutro aparelho é, justamente, um aparelho que
    ainda não viu o item)."""
    fora = {origem} if origem else set()
    if Falta.REPRODUCAO_EM_OUTRO_APARELHO.value in set(falta):
        fora |= {a for a in provas if a}
    return frozenset(fora)


def sobra_aparelho_novo(grupo: Grupo, aparelhos: Sequence[AparelhoCandidato], excluidos: Collection[str]) -> bool:
    """Falta aparelho NOVO? `False` só quando há aparelho que SERVE ao grupo (tem os apps do item, sem conta real) e todos
    eles estão entre os excluídos: ligado ou não, ocupado ou não, o que serve já foi usado, e esperar não adianta (o
    pedido fecha `sem_aparelho_novo`). Se sobra algum fora dos excluídos, o pedido espera (`sem_aparelho`); se NENHUM
    aparelho serve ainda (o app não está pronto em lugar nenhum), também espera: não é falta de aparelho novo."""
    if grupo is Grupo.EFEITO_REAL:
        return True                                   # nunca chega aqui (nasce `efeito_real`); quem recusa é outro
    servem = [a for a in aparelhos if a.tem_o_app and not a.conta_real]
    return not servem or any(a.id not in excluidos for a in servem)


# ------------------------------------------------------------------ o limite de provas (30.42)
_MARCA = re.compile(r"^\[([0-9a-f]{6,})\]")


def marca_da_evidencia(detalhe: str | None) -> str | None:
    """A marca de conteúdo `[xxxxxxxxxxxx]` no começo do `detail` de uma linha de prova, ou `None` (sem marca)."""
    m = _MARCA.match(detalhe or "")
    return m.group(1) if m else None


@dataclass(frozen=True, slots=True)
class ProvaAnterior:
    """Uma execução de prova que o item já teve: o aparelho, quando rodou e a marca de cada linha de evidência que ela
    deixou (`None` = linha sem marca). Sem linha nenhuma, `marcas` vem vazio."""

    aparelho: str | None
    quando: datetime
    marcas: tuple[str | None, ...] = ()


def conta_para_o_limite(prova: ProvaAnterior, marca_atual: str | None) -> bool:
    """A prova conta como da versão de agora? Só deixa de contar a que TEM linha e todas as linhas dela trazem OUTRA
    marca. Sem linha, sem marca, ou sem a marca de agora a comparar, conta (lado seguro: o limite protege o gasto)."""
    if marca_atual is None or not prova.marcas:
        return True
    marca = marca_atual[:12]
    return any(m is None or m[:12] == marca for m in prova.marcas)


def limite_de_provas_atingido(provas: Iterable[ProvaAnterior], marca_atual: str | None, agora: datetime) -> bool:
    """30.42: o item já teve `MAXIMO_DE_PROVAS` provas da mesma versão do conteúdo nos últimos `JANELA_DE_PROVAS_DIAS`
    dias? Então a próxima não roda (sem gastar)."""
    desde = agora - timedelta(days=JANELA_DE_PROVAS_DIAS)
    n = sum(1 for p in provas if p.quando >= desde and conta_para_o_limite(p, marca_atual))
    return n >= MAXIMO_DE_PROVAS


#: 30.48: a prova de fluxo com `for_each` roda uma AMOSTRA, os N primeiros itens na ordem da tela (sem sorteio), com N
#: o maior que cabe no teto máximo da prova, entre o mínimo e o máximo daqui. Sem isso, o `for_each` de tamanho
#: desconhecido nunca cabia no teto e o fluxo nunca se validava por pedido (o lv-5cf7389f13e4e0f0, 04/10).
AMOSTRA_MINIMA = 2
AMOSTRA_MAXIMA = 3


def maximo_de_etapas_da_prova() -> int:
    """30.48: o maior número de etapas cujo teto (`teto_da_prova`) ainda cabe no máximo (37 com 0,05 + 0,02 × e ≤ 0,80)."""
    return int((TETO_DA_PROVA_MAXIMO_USD - TETO_DA_PROVA_PISO_USD) / TETO_DA_PROVA_POR_ETAPA_USD + 1e-9)


def tamanho_da_amostra(fixas: int, por_item: int) -> int | None:
    """30.48: quantos itens do `for_each` a prova roda (`fixas` etapas fora do laço, `por_item` etapas-modelo por item),
    ou `None` quando nem o mínimo cabe (a prova não despacha) ou não há laço."""
    if por_item <= 0:
        return None
    n = min(AMOSTRA_MAXIMA, (maximo_de_etapas_da_prova() - fixas) // por_item)
    return n if n >= AMOSTRA_MINIMA else None


def teto_da_prova(etapas: int | None) -> float | None:
    """30.41: o teto de IA de UMA execução de `etapas` etapas (`TETO_DA_PROVA_*`), ou `None` quando passa do máximo ou o
    tamanho não se sabe (`etapas is None`: o lado seguro, não despacha)."""
    if etapas is None or etapas < 0:
        return None
    teto = round(TETO_DA_PROVA_PISO_USD + TETO_DA_PROVA_POR_ETAPA_USD * etapas, 4)
    return teto if teto <= TETO_DA_PROVA_MAXIMO_USD else None


def motivo_da_prova_invalida(detalhe: str | None) -> Motivo:
    """O motivo do pedido cuja execução deixou a linha `invalida` (30.42): o dela (`domain/prova.py`); a linha cujo
    detalhe não se lê não prova nada nem aponta o porquê, e fecha `sem_evidencia` (o lado seguro)."""
    m = motivo_da_invalida(detalhe)
    return Motivo.SEM_EVIDENCIA if m is None else Motivo(m.value)


__all__ = ["BETA_PADRAO", "FALTA_AUTOMATIZAVEL", "JANELA_DE_PROVAS_DIAS", "MAXIMO_DE_PROVAS", "MAXIMO_POR_HORA",
           "MOTIVO_HUMANO", "TETO_DA_PROVA_MAXIMO_USD", "TETO_DA_PROVA_PISO_USD", "TETO_DA_PROVA_POR_ETAPA_USD",
           "VALIDADE_DO_PEDIDO_H", "VIVOS", "Ambiente", "AparelhoCandidato", "EstadoDoPedido", "FatosDoParecer",
           "Folego", "Grupo", "Motivo", "Pedido", "ProvaAnterior", "conta_para_o_limite", "escolher_aparelho",
           "excluidos_da_validacao", "falta_automatizavel", "grupo_de", "limite_de_provas_atingido",
           "marca_da_evidencia", "motivo_da_prova_invalida", "motivo_humano", "pedido_do_parecer", "pode_despachar",
           "sobra_aparelho_novo", "teto_da_prova"]
