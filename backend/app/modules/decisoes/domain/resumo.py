"""O texto do resumo agrupado das decisões automáticas (item 28.25). Domínio puro.

Regra do aviso externo (docstring de `avisos/domain/mensagem.py`): texto FIXO no código, nenhum campo do fato. Por isso
a regra que decidiu NUNCA é repetida na mensagem: as regras conhecidas têm frase própria, e o resto é somado em "outras
decisões". O que o dono precisa saber é "quantas coisas a plataforma decidiu por ele e onde desfazer", não o conteúdo.
Nada de nome de persona, conta, e-mail, telefone, IP nem texto de comando: o domínio nem recebe esses campos.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.shared.decisoes import Escalar

#: O tipo do aviso na fila (`avisos_entregas.tipo`). Fica fora de `mensagem.ROTULOS_AGRUPADOS` de propósito: sai no máximo
#: um por janela, então a regra da rajada (28.19) nunca o agrupa.
TIPO_DO_AVISO = "decisoes.resumo"
FAMILIA_DA_CHAVE = "decisoes"
TITULO = "A plataforma decidiu sozinha o que esperava você"
#: A aba do painel que lista as decisões (`#/pendencias?aba=decididas`).
CONSULTA_DA_ABA = "?aba=decididas"


@dataclass(frozen=True, slots=True)
class DecisaoParaResumir:
    """O mínimo de uma decisão que o resumo precisa: nada de `item_ref`, `efeito` nem `fatos` inteiros."""

    id: int
    fila: str
    regra: str
    horas: float | None = None


def horas_dos_fatos(fatos: Mapping[str, Escalar]) -> float | None:
    h = fatos.get("horas")
    return float(h) if isinstance(h, (int, float)) and not isinstance(h, bool) else None


def _horas(h: float | None) -> str:
    return "" if h is None else f" havia {int(h) if float(h).is_integer() else h} h"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def _frase(chave: str, n: int, horas: float | None) -> str:
    um = n == 1
    if chave == "pergunta":
        return f"{_plural(n, 'pergunta', 'perguntas')} sem resposta{_horas(horas)} {'foi encerrada' if um else 'foram encerradas'}"
    if chave == "objetivo":
        return (f"{_plural(n, 'objetivo', 'objetivos')} que esperava{'' if um else 'm'} uma execução já terminada "
                f"{'foi encerrado' if um else 'foram encerrados'}")
    if chave == "aprendizado:aprovar":
        return (f"{_plural(n, 'aprendizado', 'aprendizados')} que esperava{'' if um else 'm'} aprovação "
                f"{'foi decidido' if um else 'foram decididos'} pela plataforma")
    if chave == "aprendizado:revisar":
        return (f"{_plural(n, 'aprendizado', 'aprendizados')} em revisão "
                f"{'foi confirmado' if um else 'foram confirmados'} pela plataforma")
    if chave == "aprendizado":
        return f"{_plural(n, 'aprendizado', 'aprendizados')} {'foi decidido' if um else 'foram decididos'} pela plataforma"
    if chave == "pedido":
        return (f"{_plural(n, 'pedido', 'pedidos')} que esperava{'' if um else 'm'} uma pessoa "
                f"{'foi encerrado' if um else 'foram encerrados'}")
    return f"{_plural(n, 'outra decisão', 'outras decisões')} da plataforma"


def chave_da_frase(fila: str, regra: str) -> str:
    """Qual frase fixa descreve a decisão. Regra desconhecida cai na frase da fila (aprendizado) ou em `outras`."""
    if fila == "aprendizado":
        if regra.startswith("auto:qa_para_aprovar"):
            return "aprendizado:aprovar"
        if regra.startswith("auto:qa_revisar"):
            return "aprendizado:revisar"
        return "aprendizado"
    return fila if fila in ("pergunta", "objetivo", "pedido") else "outras"


def corpo_do_resumo(decisoes: Iterable[DecisaoParaResumir], desfazer_dias: float) -> str | None:
    """As frases do resumo, uma por linha, e o prazo do desfazer. `None` sem nenhuma decisão. A ordem é estável (a das
    frases acima), não a do banco."""
    contagem: dict[str, int] = {}
    horas: dict[str, float | None] = {}
    for d in decisoes:
        c = chave_da_frase(d.fila, d.regra)
        contagem[c] = contagem.get(c, 0) + 1
        if d.horas is not None:
            horas[c] = max(horas.get(c) or 0.0, d.horas)
    if not contagem:
        return None
    ordem = ("pergunta", "objetivo", "aprendizado:aprovar", "aprendizado:revisar", "aprendizado", "pedido", "outras")
    linhas = [f"- {_frase(c, contagem[c], horas.get(c))}." for c in ordem if c in contagem]
    dias = int(desfazer_dias) if float(desfazer_dias).is_integer() else desfazer_dias
    linhas.append(f"Dá para desfazer pelo painel em até {dias} {'dia' if dias == 1 else 'dias'}.")
    if any(c.startswith("aprendizado") for c in contagem):
        # O desfazer do aprendizado é desligar o item (contrato da orquestradora, 04/10), nunca "voltar para revisão".
        linhas.append("Os aprendizados decididos sozinhos se desfazem desligando o item.")
    return "\n".join(linhas)


def chave_do_resumo(primeiro_id: int, ultimo_id: int) -> str:
    """A chave de deduplicação na fila de avisos: o intervalo de ids que o resumo cobre. Repetir o mesmo resumo (queda
    entre enfileirar e marcar) é a mesma linha; decisões novas entre uma tentativa e outra mudam o intervalo."""
    return f"{FAMILIA_DA_CHAVE}:resumo:{primeiro_id}-{ultimo_id}"
