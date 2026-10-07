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
    #: 31.149: a correção ensinada que não virou receita na etapa que falhou vira lição do planejador (o caminho é de
    #: uma pessoa: só o dono a publica)
    CORRECAO_ENSINADA = "teaching_correction"
    #: 31.190: o fato da pesquisa de uma operação encerrada (2 domínios), candidato do escritor. O texto é de fonte
    #: externa (a web): entra em `FONTES_HUMANAS` para só o dono o publicar (D1), não por ser de pessoa.
    FATO_DA_OPERACAO = "operation_fact"


#: Origens em que o TEXTO veio de uma pessoa: o item nasce com `human_origin=1` e só o dono o publica (D1). O fato da
#: operação (31.190) entra pela mesma trava: o texto é de fonte externa, e a lição ativa do escritor iria a todo texto
#: do app (revisão de segredos: "só a falta de evidência" não é trava).
FONTES_HUMANAS = frozenset({SourceKind.APPROVAL_EDIT, SourceKind.ANSWER, SourceKind.FEEDBACK_NOTE,
                            SourceKind.MANUAL, SourceKind.CORRECAO_ENSINADA, SourceKind.FATO_DA_OPERACAO})


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
    #: 30.17: a pessoa pediu ao curador a revisão de um item (`source_ref` = `pedido_de_revisao:<item>@<dossie_hash>`)
    #: e a pessoa decidiu um parecer (`source_ref` = `parecer:<lr-id>`; a data da decisão, que `learning_reviews` não
    #: tem coluna para guardar).
    PEDIU_REVISAO = "pediu_revisao"
    PARECER_DECIDIDO = "parecer_decidido"
    #: 30.34: o caso da sombra da autopublicação, o fluxo B que publicaria (`source_ref` = `autopublicaria:<item>`,
    #: `created_by` = sistema: um por item). Não é gesto de pessoa: fica fora da régua de intervenções.
    AUTOPUBLICARIA = "autopublicaria"
    #: 30.55: o caso da sombra da aprovação automática, a receita ou o fluxo que a plataforma decidiria (`source_ref` =
    #: `aprovaria:<item>`, `created_by` = sistema: um por item). Também não é gesto de pessoa.
    APROVARIA = "aprovaria"


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
    #: 30.36: a execução fez o caminho do fluxo e só reescreveu a forma (a pós-condição de uma etapa sem efeito, um
    #: parâmetro fora da ação). Nem a favor nem contra: fica à vista na trilha e fora das contagens. Ao lado de um
    #: `against` da MESMA origem, tira esse `against` do contra (`promocao.efetivas`).
    FORMA = "forma"
    #: 30.42: a execução de prova não vale como evidência do fluxo (efeito repetido, ponto de partida, ator que não
    #: agiu; o motivo vai no começo do `detail`, `domain/prova.py`). Nem a favor nem contra: fica à vista na trilha e
    #: fora das contagens. Ao lado de um `for` ou `against` da MESMA origem, tira essa linha das contagens
    #: (`promocao.efetivas`), como a `forma` faz com o `against`.
    INVALIDA = "invalida"
    #: 30.53: a regra de hoje desfez a `invalida` da MESMA (item, origem) (a conferência do QA que contava duas mensagens
    #: de dois contatos como o efeito saindo duas vezes). Só NEUTRALIZA a `invalida`: não conta a favor nem contra, e
    #: nenhum `for` nasce dela. O log só cresce: a `invalida` fica à vista, ao lado.
    REVALIDADA = "revalidada"


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


PREFIXO_ABSORVIDA = "absorvida:"


def absorvida(commit: str) -> str:
    return f"{PREFIXO_ABSORVIDA}{commit.strip()}"


def absorvida_em(detalhe: str | None) -> str | None:
    """O commit de um `state_detail` `absorvida:<commit>`; `None` quando o detalhe é de outra coisa."""
    if detalhe is None or not detalhe.startswith(PREFIXO_ABSORVIDA):
        return None
    return detalhe[len(PREFIXO_ABSORVIDA):].strip() or "desconhecido"


#: O balde "app não resolvido" (30.2): fluxo e habilidade cujo `app_id` não casa com `apps` nem com um pacote
#: conhecido. Não é pacote Android (pacote tem ponto), então nunca colide com um; o filtro `app=` do livro o aceita.
APP_NAO_RESOLVIDO = "nao_resolvido"


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
    # Item 30.13: nascem do diagnóstico de um grupo de falha (a causa e o alvo vão na proposta; `parent_id` = o grupo).
    # São recomendação para a pessoa: nenhuma muda conhecimento ou código.
    REBAIXAR_RECEITA = "rebaixar_receita"
    REVISAR_LICAO = "revisar_licao"
    REAPRENDER_TELA = "reaprender_tela"
    AJUSTAR_CATALOGO = "ajustar_catalogo"
    INVESTIGAR = "investigar"


class CausaProvavel(StrEnum):
    """A hipótese DETERMINÍSTICA de um grupo de falha (item 30.13, `docs/design/aprendizado-vivo.md` §9.1). Fechada:
    cada valor tem as regras e os fatos em `domain/diagnostico.py`. `indeterminada` é o "não sei" escrito — é o dado que
    o curador lê para decidir se vale pedir a IA; nunca vira outra causa por palpite."""

    TETO_DE_IA = "teto_de_ia"                           # orçamento de chamadas, tokens ou US$
    PROVEDOR_DE_IA = "provedor_de_ia"                   # indisponível, recusa por política ou sem saldo
    SESSAO_OU_AUTENTICACAO = "sessao_ou_autenticacao"   # login, conta errada (a conta é da pessoa)
    APARELHO = "aparelho"                               # camada aparelho/automação/execução, ou só um aparelho falha
    PLANO = "plano"                                     # o plano ligou mal as etapas
    INFORMACAO_DA_PESSOA = "informacao_da_pessoa"       # falta o dado que só quem pediu tem
    CATALOGO_RECUSOU = "catalogo_recusou"               # a guarda do efeito, do catálogo, não foi atendida
    VERIFICADOR = "verificador"                         # a comprovação (pós-condição, efeito) ou a pessoa a desmente
    RECEITA_DIVERGIU = "receita_divergiu"               # a receita conduziu e falhou em todos os aparelhos que tentaram
    VERSAO_NOVA = "versao_nova"                         # receita quarentenada na versão nova, havendo comprovada na anterior
    LICAO_ATRAPALHA = "licao_atrapalha"                 # lição exposta nas falhas e com taxa pior que o controle
    TELA_DESCONHECIDA = "tela_desconhecida"             # a tela da falha não é reconhecida pelo conhecimento declarado
    FALTA_CONHECIMENTO = "falta_conhecimento"           # só a IA conduziu, sem receita nem lição para o passo
    INDETERMINADA = "indeterminada"


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


class Rotulo(StrEnum):
    """Filtro `rotulo` do livro (RA-19): de que conjunto de apps. Os apps de teste são os de `apps.category='qa'` (o QA
    embutido); o acervo deles não é descartado (o fluxo de 17 usos serviu 16 execuções reais), só sai da lista padrão."""

    PRODUTO = "produto"              # sem os apps de teste (a lista padrão)
    QA = "qa"                        # só os apps de teste
    TODOS = "todos"
