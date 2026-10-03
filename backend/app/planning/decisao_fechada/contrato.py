"""Contrato da porta `DecisaoFechada` (item 31.4, ADR-069): tipos puros, sem `Any`, sem rede, sem banco.

Uma decisão por conjunto fechado é: UM estado (campos nomeados de lista fechada por origem), N perguntas sobre ele (fan-out,
uma chamada) e, para cada pergunta, a escolha de um id opaco dentre opções declaradas (até 255, sempre com `nenhuma`).
O poder da resposta é limitado pelo ADR-069 item 2: ela escolhe entre candidatos que passam depois pelos MESMOS guardas,
acrescenta escrutínio ou sugere à pessoa. Nunca autoriza efeito externo, aprovação, consentimento, sucesso nem segurança.

Os vocabulários são fechados de propósito: virarão rótulo de métrica e coluna da tabela de sombra (31.5), e um valor novo
precisa nascer num diff que o revisor veja.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final, Literal, get_args

Origem = Literal["curador", "intencao", "desempate", "apps"]
Classe = Literal["C0", "C1", "C2", "C3"]
Modo = Literal["off", "shadow", "on"]
TipoDePergunta = Literal["choice", "noul", "score"]
#: Por que o caminho do Jev não valeu e o trabalho voltou ao caminho de hoje. 401/422/429/529 são o status HTTP; `rede` cobre
#: timeout, erro de transporte e qualquer falha inesperada do decisor (não houve resposta utilizável); `parse` é resposta que
#: não é JSON no formato; `unknown_choice` é escolha fora das opções enviadas; `abaixo_do_limiar` é confiança insuficiente;
#: `privacidade` é recusa local ANTES de montar o corpo; `desligado` é modo off, config desligada ou decisor nulo.
FallbackReason = Literal["401", "422", "429", "529", "rede", "parse", "unknown_choice", "abaixo_do_limiar",
                         "privacidade", "desligado"]
#: Marcadores de C7 que o CHAMADOR declara no pedido (ADR-069 item 4: C7 nunca, em modo nenhum, sombra inclusa), mais
#: `social_persona`, que é a exclusão D-J5 (pipeline social e de persona fora da porta, AUP 1.3, 1.6 e 3.3).
Marcador = Literal["tela_sensivel", "tela_protegida", "aparelho_loja", "segredo", "credencial", "desafio",
                   "social_persona"]
#: Por que o chamador recusou o pedido por privacidade (`decisao_fechada_sombra.motivo_privacidade`, migração 079;
#: reverificação B do 31.9). `c7_*`: o comando é C7 (`intencao.motivo_c7`); os demais: o filtro da C3 esvaziou o estado
#: (`entidades.remover_entidades_com_motivo`). Fora daqui, a linha grava `outro`.
MotivoDePrivacidade = Literal[
    "c7_bidi", "c7_palavra", "c7_formato", "c7_alfabetos", "c7_ofuscado", "c7_eufemismo", "c7_digitos",
    "c7_login_valor", "c7_par_credencial",
    "nao_texto", "vazio", "alfabetos", "simbolo_colado", "email_ofuscado", "endereco", "documento", "ditado", "numerais",
    "sobra_de_forma", "outro"]

ORIGENS: Final[tuple[str, ...]] = get_args(Origem)
CLASSES: Final[tuple[str, ...]] = get_args(Classe)
MODOS: Final[tuple[str, ...]] = get_args(Modo)
FALLBACKS: Final[tuple[str, ...]] = get_args(FallbackReason)
MARCADORES: Final[tuple[str, ...]] = get_args(Marcador)
MOTIVOS_DE_PRIVACIDADE: Final[tuple[str, ...]] = get_args(MotivoDePrivacidade)

#: Teto de opções de uma pergunta, CONTANDO a `nenhuma` (o mesmo do adaptador, `adapters/jev.py::MAX_OPCOES`).
MAX_OPCOES: Final = 255
#: Id opaco da opção "nenhuma das anteriores" (o mesmo do adaptador de retrieval). Escolhê-la é abster-se.
ID_NENHUMA: Final = "opt:nenhuma"
_DESCRICAO_NENHUMA: Final = "None of the above: no other option answers the question."


@dataclass(frozen=True)
class Pergunta:
    """Uma pergunta sobre o estado. `instrucoes` em inglês (o estado fica em português: ADR-069/roteiro §3 item 3).

    `opcoes` é {id_opaco: descrição}; em `choice` é obrigatória e SEMPRE traz `nenhuma` (`pergunta_choice` a acrescenta).
    `limiar` vale sobre a probabilidade devolvida (no `noul` a pergunta se escreve para o "sim" ser o valor alto, e nunca se
    usa o complemento: P(noul) != 1 - P(nao-noul))."""

    id: str
    tipo: TipoDePergunta
    instrucoes: str
    opcoes: Mapping[str, str] = field(default_factory=dict)
    limiar: float = 0.85

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("pergunta sem id")
        if self.tipo not in get_args(TipoDePergunta):
            raise ValueError("tipo de pergunta fora do vocabulario")
        if not 0.0 <= self.limiar <= 1.0:
            raise ValueError("limiar fora de [0, 1]")
        if len(self.opcoes) > MAX_OPCOES:
            raise ValueError("opcoes acima do teto")
        if self.tipo == "choice" and ID_NENHUMA not in self.opcoes:
            raise ValueError("choice sem a opcao `nenhuma` (use pergunta_choice)")


def pergunta_choice(id: str, instrucoes: str, opcoes: Mapping[str, str], limiar: float = 0.85) -> Pergunta:
    """`choice` com a `nenhuma` garantida. Um id igual ao reservado é erro de quem monta, não colisão silenciosa."""
    if ID_NENHUMA in opcoes:
        raise ValueError("id reservado da opcao `nenhuma`")
    return Pergunta(id, "choice", instrucoes, {**opcoes, ID_NENHUMA: _DESCRICAO_NENHUMA}, limiar)


@dataclass(frozen=True)
class PedidoDeDecisao:
    """O pedido inteiro. `estado` são campos NOMEADOS de lista fechada por origem (`privacidade.CAMPOS_POR_ORIGEM`).

    `marcadores` é o que o chamador SABE sobre o dado (tela sensível, aparelho-loja, credencial...): a porta não adivinha
    C7 olhando texto, ela recusa o pedido inteiro quando qualquer marcador está presente. `run_id`, `step_id` e `ref` só
    identificam o pedido para o registro; nunca vão no corpo.

    `motivo_privacidade` (reverificação B do 31.9, migração 079): POR QUE o chamador marcou credencial ou esvaziou o estado,
    em vocabulário fechado (`MOTIVOS_DE_PRIVACIDADE`). Vai só para a linha da sombra, nunca no corpo; o veredito de
    `privacidade.validar` e o `fallback_reason` não mudam."""

    origem: Origem
    classe: Classe
    estado: Mapping[str, str]
    perguntas: tuple[Pergunta, ...]
    modo: Modo = "off"
    marcadores: frozenset[str] = frozenset()
    run_id: str | None = None
    step_id: str | None = None
    ref: str | None = None
    motivo_privacidade: str | None = None


@dataclass(frozen=True)
class RespostaDeDecisao:
    """Resposta a UMA pergunta. `escolha` é None sempre que há `fallback_reason`: um fallback nunca conta como acerto.

    `escolha == ID_NENHUMA` é resposta de verdade (abstenção), sem fallback. `tokens`, `usd` e `ms` ficam zerados aqui num
    fan-out: a chamada é UMA e o custo dela mora em `ResultadoDeDecisao`, para ninguém somar duas vezes."""

    escolha: str | None = None
    probabilidades: Mapping[str, float] = field(default_factory=dict)
    confianca: float | None = None
    fallback_reason: FallbackReason | None = None
    tokens: int = 0
    usd: float = 0.0
    ms: float = 0.0

    @property
    def valida(self) -> bool:
        """Só conta como acerto potencial o que NÃO caiu em fallback e escolheu algo diferente de abster-se."""
        return self.fallback_reason is None and self.escolha is not None and self.escolha != ID_NENHUMA


@dataclass(frozen=True)
class ResultadoDeDecisao:
    """O que a porta devolve: uma `RespostaDeDecisao` por pergunta (pelo id) e o custo da chamada única."""

    respostas: Mapping[str, RespostaDeDecisao]
    tokens: int = 0
    usd: float = 0.0
    ms: float = 0.0
    #: Motivo único quando a CHAMADA inteira não aconteceu ou falhou (todas as respostas levam o mesmo).
    fallback_reason: FallbackReason | None = None


def resultado_de_fallback(pedido: PedidoDeDecisao, motivo: FallbackReason, *, ms: float = 0.0) -> ResultadoDeDecisao:
    """Resultado em que nenhuma pergunta foi respondida: escolha None e `motivo` em todas."""
    return ResultadoDeDecisao({p.id: RespostaDeDecisao(fallback_reason=motivo) for p in pedido.perguntas},
                              ms=ms, fallback_reason=motivo)


class FalhaDeDecisao(Exception):
    """Falha de um decisor, com o motivo FECHADO. A mensagem é um rótulo: nunca corpo, cabeçalho nem estado."""

    def __init__(self, motivo: FallbackReason) -> None:
        super().__init__(motivo)
        self.motivo: FallbackReason = motivo
