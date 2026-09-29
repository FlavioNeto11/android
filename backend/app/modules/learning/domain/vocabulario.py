"""Vocabulários fechados do aprendizado (ADR-054). Tudo o que vira coluna, rótulo ou filtro sai daqui.

Fechado de propósito: texto livre não agrega (o `attempts.error` de hoje é a prova), e um valor fora da lista é recusa
na borda, não uma categoria nova inventada em silêncio. Sem CHECK no banco (a 055 segue a 041–043): a lista muda sem
migração, e quem confere é este módulo.
"""
from __future__ import annotations

from enum import StrEnum


class LivroKind(StrEnum):
    """O que o livro reúne na leitura. Os quatro primeiros têm casa nativa; os demais moram em `learning_items`."""

    RECEITA = "receita"
    FLUXO = "fluxo"
    HABILIDADE = "habilidade"
    MEMORIA = "memoria"
    TELA = "tela"
    LICAO = "licao"
    VOZ = "voz"
    PREFERENCIA = "preferencia"


#: Os tipos que moram em `learning_items` (o conteúdo é do aprendizado).
KINDS_DE_ITEM = frozenset({LivroKind.TELA, LivroKind.LICAO, LivroKind.VOZ, LivroKind.PREFERENCIA})
#: Os tipos cujo conteúdo é de outra peça: o livro só os lê (e, na receita e no fluxo, move o status com trilha).
KINDS_NATIVOS = frozenset({LivroKind.RECEITA, LivroKind.FLUXO, LivroKind.HABILIDADE, LivroKind.MEMORIA})


class SourceKind(StrEnum):
    """De onde nasceu um item de `learning_items`."""

    RECOVERY = "recovery"                       # falha seguida de sucesso na mesma etapa (contraste)
    PLAN_DEFECT = "plan_defect"                 # defeito do plano seguido de plano que comprovou
    SCREEN_OBSERVATION = "screen_observation"
    SESSION_UNKNOWN = "session_unknown"
    APPROVAL_EDIT = "approval_edit"
    ANSWER = "answer"
    DISAMBIGUATION = "disambiguation"
    FEEDBACK_NOTE = "feedback_note"
    MANUAL = "manual"


#: Origens em que o TEXTO veio de uma pessoa: o item nasce com `human_origin=1` e só o dono o publica (D1).
FONTES_HUMANAS = frozenset({SourceKind.APPROVAL_EDIT, SourceKind.ANSWER, SourceKind.FEEDBACK_NOTE,
                            SourceKind.MANUAL})


class Papel(StrEnum):
    """Para quem o conhecimento é contexto. A lição nunca vai ao verificador (ADR-024): não há papel para ele."""

    ACTOR = "actor"
    PLANNER = "planner"
    WRITER = "writer"
    RESOLVER = "resolver"
    CLASSIFIER = "classifier"


class SignalKind(StrEnum):
    FEEDBACK = "feedback"
    CONFIRMOU_A_MAO = "confirmou_a_mao"
    REPETIU_ITEM = "repetiu_item"
    ABANDONOU_ITEM = "abandonou_item"
    REPETIU_EXECUCAO = "repetiu_execucao"
    CANCELOU_EXECUCAO = "cancelou_execucao"
    TOMOU_CONTROLE = "tomou_controle"
    RESPONDEU_PERGUNTA = "respondeu_pergunta"
    ESCOLHEU_HABILIDADE = "escolheu_habilidade"
    APROVACAO_DECIDIDA = "aprovacao_decidida"
    COMANDO_INCERTO_RESOLVIDO = "comando_incerto_resolvido"
    CORRECAO_DE_ENSINO = "correcao_de_ensino"
    TELA_VISTA = "tela_vista"
    TELA_DESCONHECIDA_CHAMOU_PESSOA = "tela_desconhecida_chamou_pessoa"


#: Sinais que contam como intervenção humana na régua diária (`learning_daily.interventions`).
SINAIS_DE_INTERVENCAO = frozenset({SignalKind.TOMOU_CONTROLE, SignalKind.CONFIRMOU_A_MAO,
                                   SignalKind.TELA_DESCONHECIDA_CHAMOU_PESSOA})


class Polaridade(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class Veredito(StrEnum):
    """O botão do D2."""

    CERTO = "certo"
    ERRADO = "errado"


class MotivoDoVoto(StrEnum):
    FEZ_OUTRA_COISA = "fez_outra_coisa"
    ALVO_ERRADO = "alvo_errado"
    NAO_TERMINOU = "nao_terminou"
    TEXTO_RUIM = "texto_ruim"
    DEMOROU_OU_GASTOU = "demorou_ou_gastou"
    PEDIU_AJUDA_A_TOA = "pediu_ajuda_a_toa"
    OUTRO = "outro"


#: "Deu errado" por um destes rebaixa o que o item usou e o que ele aprendeu (D2).
MOTIVOS_DE_NAVEGACAO = frozenset({MotivoDoVoto.FEZ_OUTRA_COISA, MotivoDoVoto.ALVO_ERRADO,
                                  MotivoDoVoto.NAO_TERMINOU})


class Posicao(StrEnum):
    """A posição de uma evidência em relação ao item (`learning_evidence.stance`)."""

    FOR = "for"
    AGAINST = "against"
    CONFLICT = "conflict"


class Braco(StrEnum):
    WITH = "with"
    HOLDOUT = "holdout"


class DetalheDeEstado(StrEnum):
    """Os `state_detail` fixos. `absorvida:<commit>` é montado por `absorvida()`."""

    EM_PROVA = "em_prova"
    FILA_DE_PROVA = "fila_de_prova"
    MEDIDA_AJUDA = "medida:ajuda"
    MEDIDA_NEUTRA = "medida:neutra"
    MEDIDA_ATRAPALHA = "medida:atrapalha"
    CONTRADITA = "contradita"


def absorvida(commit: str) -> str:
    return f"absorvida:{commit.strip()}"


class EstadoDoBacklog(StrEnum):
    OPEN = "open"
    TRIAGED = "triaged"
    PLANNED = "planned"
    FIXED_PENDING_PROOF = "fixed_pending_proof"
    FIXED = "fixed"
    REOPENED = "reopened"
    WONTFIX = "wontfix"


class CategoriaDoBacklog(StrEnum):
    FALHA = "falha"
    PROPOSTA = "proposta"


class TipoDeProposta(StrEnum):
    ACAO_DE_CATALOGO = "acao_de_catalogo"
    PROMOVER_LICAO = "promover_licao"
    PROMOVER_TELA = "promover_tela"


class Modo(StrEnum):
    """Modo por tipo, no molde de `ai.recipes`: `off` não grava, `shadow` grava e mede sem consumir, `on` consome."""

    OFF = "off"
    SHADOW = "shadow"
    ON = "on"


class ModoDeTelas(StrEnum):
    """Telas aprendidas: `observe` grava, minera e valida, mas a sessão não as consome."""

    OFF = "off"
    OBSERVE = "observe"
    ON = "on"


class Origem(StrEnum):
    """Filtro `origem` do livro: quem produziu o conhecimento."""

    EXECUCAO = "execucao"            # aprendido de execução (IA ou contraste)
    TREINO = "treino"                # a pessoa demonstrou
    PESSOA = "pessoa"                # texto ou decisão de pessoa (nota, edição, manual)
    ENSINO = "ensino"                # ensino v2 / habilidade escrita
    SISTEMA = "sistema"              # observação automática (tela, memória)
