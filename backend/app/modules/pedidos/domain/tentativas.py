"""Tentativas, efeito e pausa por falhas seguidas (item 28.5; `docs/design/pedidos-persistentes.md` §7.6).

Duas perguntas, ambas puras:

1. **O que fazer com o fim de uma execução** (`decidir`): repetir a ocorrência (a MESMA linha volta a `devida` com a
   tentativa seguinte), mandá-la a `incerta` (o pedido espera a pessoa) ou deixá-la `falhou` de vez. A regra é a de
   `Scheduler._reconciliar`: efeito possivelmente disparado só se VERIFICA, nunca se repete.

   | fechamento                       | efeito possível | tentativas        | decisão                                   |
   | `incerta`                        | (qualquer)      | (qualquer)        | `incerta`: aguardando a pessoa, sem repetir |
   | `falhou`                         | sim ou ignorado | (qualquer)        | `definitiva` (sem nova tentativa)         |
   | `falhou`                         | não             | `n < max`         | `repetir`, depois de um atraso exponencial |
   | `falhou`                         | não             | `n >= max`        | `definitiva`                              |
   | `falhou`, sem orçamento p/ outra | não             | `n < max`         | `definitiva` (o motivo diz o orçamento)   |
   | outros (`concluida`, `cancelada`)| —               | —                 | `fim`: nada a decidir                     |

   "Efeito possível" é `actions.effect_possible` de QUALQUER ação da execução (inclui o toque que deu certo: `tap`,
   `type_text` etc. são `EFFECT_CAPABLE`), e a execução que sumiu (purga) conta como efeito possível: o que não se sabe
   não é seguro repetir. Falha com efeito possível NÃO vira `incerta` aqui: a etapa com efeito externo que ficou sem
   prova já chega como objetivo `uncertain` (`fechar` a manda a `incerta`); o toque comum de navegação não justifica
   parar o pedido inteiro à espera da pessoa.

2. **Quando o pedido pausa** (`falhas_seguidas`, `deve_pausar`): N ocorrências seguidas que terminaram `falhou`
   (o `pause-on-failure` do Temporal). Conta OCORRÊNCIAS, não execuções: a ocorrência que falhou e foi repetida só conta
   quando a última tentativa também falha. `concluida` zera; `incerta` também (a pessoa entra no meio); `cancelada` não
   conta nem zera (foi gesto, não resultado).

O atraso da tentativa é exponencial com teto: `min(teto, base * 2**(n-1))`, `n` a tentativa que acabou de falhar.

Puro: stdlib. Os estados chegam como texto.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

ACAO_REPETIR = "repetir"
ACAO_INCERTA = "incerta"
ACAO_DEFINITIVA = "definitiva"
ACAO_FIM = "fim"

#: Tipos de aviso que esta peça emite: `(nivel, requer_pessoa)` (contrato 28.9, `AvisoTipo`).
AVISOS: Mapping[str, tuple[str, bool]] = {
    "pausa_automatica": ("warn", False),       # informativo: vai para a caixa de avisos
    "ocorrencia_incerta": ("warn", True),      # pede a pessoa: vai para as Pendências
}


@dataclass(frozen=True)
class Decisao:
    acao: str
    #: `repetir`: o instante a partir do qual a nova tentativa pode ser despachada.
    repetir_em: datetime | None = None
    #: Texto a ACRESCENTAR ao motivo do fechamento (por que não repetiu), ou `None`.
    complemento: str | None = None


def atraso_s(tentativa: int, base_s: float, teto_s: float) -> float:
    """O atraso depois da `tentativa`-ésima falha (a partir de 1): `base`, `2*base`, `4*base`… no máximo `teto`."""
    n = max(1, int(tentativa))
    return float(min(teto_s, base_s * (2 ** min(n - 1, 30))))


def decidir(*, estado: str, tentativa: int, max_tentativas: int, efeito_possivel: bool, agora: datetime,
            base_s: float, teto_s: float, sem_orcamento: str | None = None) -> Decisao:
    """A decisão para o fechamento TERMINAL de uma ocorrência; `estado` é o que `fechamento.fechar` escolheu.

    `tentativa` é a que acabou de fechar (`pedido_ocorrencias.tentativa`); `max_tentativas` é o limite do pedido, o
    número TOTAL de execuções por ocorrência (padrão 2: a primeira e uma repetição). `sem_orcamento` é o motivo de o
    orçamento do pedido não cobrir outra tentativa (`orcamento.motivo_sem_orcamento`), ou `None`.
    """
    if estado == "incerta":
        return Decisao(ACAO_INCERTA)
    if estado != "falhou":
        return Decisao(ACAO_FIM)
    if efeito_possivel:
        return Decisao(ACAO_DEFINITIVA, complemento="sem nova tentativa: ação com efeito externo possível, só se verifica")
    if tentativa >= max_tentativas:
        # `max_tentativas == 1` é o pedido que nunca repete: nada a explicar. Com mais, diz que o limite acabou.
        return Decisao(ACAO_DEFINITIVA, complemento=(f"esgotou as {max_tentativas} tentativas" if max_tentativas > 1 else None))
    if sem_orcamento:
        return Decisao(ACAO_DEFINITIVA, complemento=f"sem nova tentativa: {sem_orcamento}")
    return Decisao(ACAO_REPETIR, repetir_em=agora + timedelta(seconds=atraso_s(tentativa, base_s, teto_s)))


def falhas_seguidas(recentes: Sequence[str]) -> int:
    """Quantas ocorrências `falhou` seguidas há no começo de `recentes` (a mais recente primeiro, só as que fecharam
    com execução). Para na primeira `concluida` ou `incerta`; `cancelada` é pulada."""
    n = 0
    for estado in recentes:
        if estado == "falhou":
            n += 1
        elif estado in ("concluida", "incerta"):
            break
    return n


def deve_pausar(seguidas: int, limite: int) -> bool:
    return limite >= 1 and seguidas >= limite


def aviso(*, tipo: str, aviso_id: str, pedido_id: str, pedido_titulo: str, ocorrencia_id: str | None, mensagem: str,
          criado_em: str, dados: Mapping[str, object] | None = None) -> dict[str, object]:
    """O `AvisoDTO` do contrato 28.9 (`pedido.aviso`), sem `lido_em` preenchido. `aviso_id` é determinístico: a mesma
    situação emitida duas vezes dá a mesma chave de deduplicação no canal de fora (28.11)."""
    nivel, requer_pessoa = AVISOS[tipo]
    return {"id": aviso_id, "pedido_id": pedido_id, "pedido_titulo": pedido_titulo, "ocorrencia_id": ocorrencia_id,
            "tipo": tipo, "nivel": nivel, "mensagem": mensagem, "dados": dict(dados or {}),
            "requer_pessoa": requer_pessoa, "criado_em": criado_em, "lido_em": None}
