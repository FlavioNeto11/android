"""Estados e transições do pedido persistente e da ocorrência dele (docs/design/pedidos-persistentes.md §6.2).

O pedido é o objetivo que dura; a ocorrência é cada vez que ele pede uma execução. As duas máquinas são TABELAS,
como as de `execution/domain/states.py` (e reusam a mesma `MaquinaDeEstados`): cada aresta diz de onde vem, e o que
não está escrito é recusado. A migração 067 grava os mesmos vocabulários em `CHECK`;
`tests/test_pedidos_modelo.py` confere que banco e domínio dizem as mesmas palavras.

Duas diferenças em relação às máquinas de execução, de propósito:

* **Estrito desde o primeiro dia.** Lá, a tabela nasceu descrevendo código que já existia e começou só conferindo.
  Aqui não há código antes dela: `transicionar_*` já levanta `TransicaoInvalida`, e o laço (28.4) nunca escreve um
  estado sem passar por elas. Estender a tabela é uma linha aqui e uma linha no teste, não uma exceção em silêncio.
* **Quem pode.** A transição do pedido carrega o ATOR (`pessoa` ou `sistema`, §6.2): só a pessoa ativa um rascunho,
  só o sistema conclui, e quem conclui é o verificador, nunca a palavra da IA (o `sistema` aqui é o código que
  conferiu o critério). A ocorrência é sempre do sistema e não tem ator.

Uma transição de um estado para ele mesmo nunca é permitida (`pode(x, x)` é falso em todo lugar): reafirmar o estado
é atualizar o detalhe, e isso não passa por aqui.

Lacunas conhecidas da tabela do PEDIDO, todas deixadas como o §6.2 as escreve (quem precisar abre a aresta e testa):
`aguardando_pessoa` só sai para `ativo` ou `cancelado`, então um `fim_em` que passa com o pedido esperando a pessoa
não o encerra sozinho (28.4/28.5 decidem se o encerramento por prazo vale também aí; a 28.10 F1 abriu a aresta
`aguardando_pessoa → encerrado`, e a `rascunho → encerrado`, SÓ para a cascata do pai encerrado: o laço não as percorre); e `pausado` não vai para
`aguardando_pessoa` (a pergunta só nasce de uma ocorrência em curso, e pausado não materializa).

Puro: stdlib e `app.modules.execution.domain.states` (a classe da tabela). Sem banco, sem `app.models`.
"""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from app.modules.execution.domain.states import MaquinaDeEstados

# ------------------------------------------------------------------ vocabulários (o CHECK da 067 diz o mesmo)
#: Teto de autonomia do pedido, do mais restrito ao menos (§6.4). O pedido nunca afrouxa a persona.
AUTONOMIAS: tuple[str, ...] = ("observar", "preparar", "agir")
#: Sobreposição de ocorrências (§7.4); `pular` é o padrão e a única permitida para `agir`.
SOBREPOSICOES: tuple[str, ...] = ("pular", "guardar_uma", "permitir_todas")
#: O que faz o pedido acontecer (§6.1). A `spec` de cada tipo é JSON lido por quem trata o tipo (28.3, 28.8).
TIPOS_DE_GATILHO: tuple[str, ...] = ("agora", "horario", "recorrencia", "evento", "condicao", "persona")
#: De onde a ocorrência veio (§6.1). `agenda` e `recuperacao` do mesmo instante são a MESMA ocorrência (mesma chave).
ORIGENS: tuple[str, ...] = ("agenda", "recuperacao", "evento", "condicao", "persona", "manual", "backfill")
#: Origens sem gatilho: o gesto da pessoa. A chave delas leva a origem e o instante do pedido (§6.3).
ORIGENS_DE_GESTO: frozenset[str] = frozenset({"manual", "backfill"})

ATOR_PESSOA = "pessoa"
ATOR_SISTEMA = "sistema"
ATORES: frozenset[str] = frozenset({ATOR_PESSOA, ATOR_SISTEMA})

#: Motivo do `encerrado` (§6.5). `concluido` e `cancelado` já dizem o motivo pelo próprio estado (critério
#: comprovado; gesto da pessoa); `abandonado` é o pausado há mais de `abandono_dias` sem gesto.
#: `pai` (28.10, F1): o pedido pai terminou (`encerrado`) e leva os filhos com ele (§9: "o pai encerra os filhos quando
#: encerra"). O texto "o pedido pai foi encerrado" vai no motivo das ocorrências canceladas.
MOTIVOS_DE_ENCERRAMENTO: tuple[str, ...] = ("prazo", "contagem", "orcamento", "abandonado", "pai")


class TransicaoInvalida(ValueError):
    """A aresta não está na tabela, o ator não pode percorrê-la ou falta o motivo que o §6.2 manda gravar."""


def _tabela(linhas: Mapping[str, frozenset[str]]) -> Mapping[str, frozenset[str]]:
    return MappingProxyType(dict(linhas))


# ------------------------------------------------------------------ pedido (`pedidos.estado`)
_P, _S, _PS = frozenset({ATOR_PESSOA}), frozenset({ATOR_SISTEMA}), frozenset({ATOR_PESSOA, ATOR_SISTEMA})

#: (de, para) → quem pode. Uma linha por aresta do §6.2; "qualquer não terminal → cancelado" vira quatro.
#: Nasce `rascunho` (INSERT, não é transição).
PEDIDO_ATORES: Mapping[tuple[str, str], frozenset[str]] = MappingProxyType({
    # Prévia dos alvos (ADR-044) e das próximas 5 ocorrências confirmada.
    ("rascunho", "ativo"): _P,
    # A pessoa pausa quando quer; o sistema por N falhas seguidas, orçamento esgotado, conta da persona `blocked`
    # (ADR-055) ou gatilho sem cursor válido. O motivo é sempre gravado.
    ("ativo", "pausado"): _PS,
    # "Retomar daqui" (as perdidas ficam `puladas`) ou "recuperar dentro da janela": escolha da pessoa.
    ("pausado", "ativo"): _P,
    # Aprovação pendente que bloqueia a próxima ocorrência, pergunta (`needs_input`) ou ocorrência `incerta`.
    ("ativo", "aguardando_pessoa"): _S,
    # A resposta vira sucessora da execução (ADR-047) e nota na memória do pedido.
    ("aguardando_pessoa", "ativo"): _P,
    # Critério de sucesso comprovado pelo verificador, nunca pela palavra da IA.
    ("ativo", "concluido"): _S,
    # `fim_em` passou, `max_ocorrencias` atingido, orçamento total gasto, ou abandono (§6.5).
    ("ativo", "encerrado"): _S,
    ("pausado", "encerrado"): _S,
    # 28.10 F1: SÓ a cascata do pai encerrado (`acoes.encerrar_filhos`, motivo `pai`). Um filho em rascunho ou esperando a
    # pessoa precisa de um caminho para o mesmo destino do pai; sem estas duas arestas ele ficaria vivo sob um pai morto.
    # Nenhuma outra parte do sistema as percorre.
    ("rascunho", "encerrado"): _S,
    ("aguardando_pessoa", "encerrado"): _S,
    # A execução em curso recebe o cancelamento de sempre (`RunService.cancel`).
    ("rascunho", "cancelado"): _P,
    ("ativo", "cancelado"): _P,
    ("pausado", "cancelado"): _P,
    ("aguardando_pessoa", "cancelado"): _P,
})

#: Os sete estados do pedido, na ordem em que o §6.2 os apresenta.
_ESTADOS_DO_PEDIDO = ("rascunho", "ativo", "pausado", "aguardando_pessoa", "concluido", "encerrado", "cancelado")


def _destinos(arestas: Mapping[tuple[str, str], object], estados: tuple[str, ...]) -> Mapping[str, frozenset[str]]:
    return _tabela({e: frozenset(para for (de, para) in arestas if de == e) for e in estados})


PEDIDO = MaquinaDeEstados("pedido", _destinos(PEDIDO_ATORES, _ESTADOS_DO_PEDIDO))

# ------------------------------------------------------------------ ocorrência (`pedido_ocorrencias.estado`)
#: Nasce `prevista`, `devida`, `pulada` ou `perdida` (INSERT do laço, não é transição): a materialização já sabe se o
#: instante caiu fora da janela de recuperação (`perdida`, §7.5) ou foi coalescido/pausado (`pulada`).
OCORRENCIA_NASCE_EM: frozenset[str] = frozenset({"prevista", "devida", "pulada", "perdida"})

OCORRENCIA_TRANSICOES: Mapping[str, frozenset[str]] = _tabela({
    # A hora chegou (`devida`); a pausa ou a coalescência a pula; passou da janela sem ninguém despachar, `perdida`;
    # cancelar o pedido ou editá-lo (a versão nova refaz as `prevista`/`devida`, §7.9) a cancela.
    "prevista": frozenset({"devida", "pulada", "perdida", "cancelada"}),
    # O despacho (§7.2 passo 2). Fora dele, sem criar execução: sobreposição, pausa, coalescida, orçamento (`pulada`,
    # §7.4/§7.5/§10); janela vencida (`perdida`); cancelamento do pedido.
    "devida": frozenset({"despachada", "pulada", "perdida", "cancelada"}),
    # Execução criada pela chave. `rodando` quando o objetivo começa; `concluida`/`falhou`/`incerta` DIRETO quando a
    # varredura (§7.2 passo 3) encontra a execução já terminal, sem ter visto `rodando` (queda entre o fim e o
    # gancho); `perdida` se passar de `prazo_inicio_s` sem começar (§7.5, execução cancelada antes da 1ª etapa);
    # `cancelada` pelo cancelamento do pedido (`RunService.cancel`).
    "despachada": frozenset({"rodando", "concluida", "falhou", "incerta", "perdida", "cancelada"}),
    # O fechamento, pelo gancho `on_run_settled` ou pela varredura; `incerta` quando houve efeito possivelmente
    # disparado (§7.6) ou etapa `uncertain`; `cancelada` pelo cancelamento.
    "rodando": frozenset({"concluida", "falhou", "incerta", "cancelada"}),
    # §7.6: nova tentativa só sem efeito possível e sem `incerta`; a MESMA linha volta a `devida` com `tentativa + 1`
    # e a execução da n-ésima usa `chave:t<n>`. A decisão de repetir (e o atraso) é do 28.5; aqui só a aresta.
    # `falhou` sem retentativa é o fim dessa ocorrência.
    "falhou": frozenset({"devida"}),
    # Fins. `incerta` nunca gera tentativa nova: o pedido vai a `aguardando_pessoa` até a pessoa resolver.
    "concluida": frozenset(),
    "incerta": frozenset(),
    "cancelada": frozenset(),
    "pulada": frozenset(),
    "perdida": frozenset(),
})
OCORRENCIA = MaquinaDeEstados("ocorrencia", OCORRENCIA_TRANSICOES)

#: Estados em que a ocorrência já terminou e tem `terminada_em` (a 28.10 F2 usa: a dependência `depois_de` aceita qualquer um
#: deles, e o fim do último deles abre a janela da próxima). `falhou` entra porque, ENQUANTO está `falhou`, a retentativa
#: ainda não existe: ao repetir, a MESMA linha volta a `devida` e deixa de contar (§7.6).
OCORRENCIA_TERMINAIS: tuple[str, ...] = ("concluida", "falhou", "incerta", "cancelada", "pulada", "perdida")

#: Estados da ocorrência em que o §6.2 manda gravar o motivo (`pedido_ocorrencias.motivo`): o fim sem execução
#: "nunca some" — é visível e diz por quê —, e a falha e o incerto dizem o que aconteceu.
OCORRENCIA_EXIGE_MOTIVO: frozenset[str] = frozenset({"pulada", "perdida", "cancelada", "falhou", "incerta"})

MAQUINAS: tuple[MaquinaDeEstados, ...] = (PEDIDO, OCORRENCIA)


# ------------------------------------------------------------------ as regras
def transicionar_pedido(de: str, para: str, *, ator: str, motivo: str | None = None) -> None:
    """Confere a transição do pedido; devolve em silêncio ou levanta `TransicaoInvalida`.

    Três conferências, nesta ordem: a aresta existe; o ator pode percorrê-la; e o motivo que o §6.2 manda gravar
    veio (`pausado` sempre; `encerrado` um de `MOTIVOS_DE_ENCERRAMENTO`). Quem escreve o estado chama isto ANTES do
    `UPDATE … WHERE estado = <de>` (o CAS que fecha a corrida entre dois laços).
    """
    if ator not in ATORES:
        raise TransicaoInvalida(f"ator desconhecido: {ator!r} (esperado um de {sorted(ATORES)})")
    if not PEDIDO.pode(de, para):
        raise TransicaoInvalida(f"pedido: {de} → {para} não está na tabela de transições")
    if ator not in PEDIDO_ATORES[(str(de), str(para))]:
        quem = " ou ".join(sorted(PEDIDO_ATORES[(str(de), str(para))]))
        raise TransicaoInvalida(f"pedido: {de} → {para} é do(a) {quem}, não do(a) {ator}")
    if para == "pausado" and not (motivo or "").strip():
        raise TransicaoInvalida("pedido: pausar exige o motivo (§6.2: o motivo é sempre gravado)")
    if para == "encerrado" and motivo not in MOTIVOS_DE_ENCERRAMENTO:
        raise TransicaoInvalida(f"pedido: encerrar exige um motivo de {list(MOTIVOS_DE_ENCERRAMENTO)}, veio {motivo!r}")


def transicionar_ocorrencia(de: str, para: str, *, motivo: str | None = None) -> None:
    """Confere a transição da ocorrência; devolve em silêncio ou levanta `TransicaoInvalida`."""
    if not OCORRENCIA.pode(de, para):
        raise TransicaoInvalida(f"ocorrência: {de} → {para} não está na tabela de transições")
    if para in OCORRENCIA_EXIGE_MOTIVO and not (motivo or "").strip():
        raise TransicaoInvalida(f"ocorrência: {de} → {para} exige o motivo (nada some em silêncio, §7.5)")
