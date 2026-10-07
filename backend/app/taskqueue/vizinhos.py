"""31.152: os pacotes vizinhos que o ensino descobriu viram conhecimento do app para o planejador livre. Puro: sem banco.

Medido em 06/10: `pacotes_aceitos` (31.123) só existia no plano de fluxo ensinado. Uma execução livre que abre a busca
do Configurações termina em outro pacote (o da busca) e caía na regra do app em primeiro plano: na prova F2
r-20261006102728-1157c6 a etapa foi recusada e a IA assumiu por US$ 0,039. O saber estava em 5 fluxos ensinados e em
nenhum lugar que o planejador lesse.

Agora o par (app, vizinho) é lido dos fluxos ENSINADOS (`flows.source` = `training:<sessão>`), com a contagem (sessões
de ensino distintas que o declararam) e a origem (as sessões). Conta também o fluxo desligado: o saber é do app, não do
fluxo. No plano livre, cada etapa SEM efeito e sem `pacotes_aceitos` recebe os vizinhos conhecidos do app dela; a
etapa com efeito fica como está (a conclusão de um efeito não se aceita numa tela que ninguém demonstrou para ele).

Nunca entram: o systemui, o lançador (pacote com "launcher") e os apps cadastrados (esses são o app de uma etapa).
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from ..models import Plan
from ..modules.learning.domain.licoes import ACAO_DE_SESSAO

#: Os pacotes que nunca valem como vizinho (o lançador sai pelo nome: "launcher").
FORA_DOS_ACEITOS = ("com.android.systemui",)
#: O formato de um pacote Android: o que não casa não vai ao plano nem à trilha.
_PACOTE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


def vizinho_valido(pacote: object, fora: Iterable[str] = ()) -> bool:
    return (isinstance(pacote, str) and bool(_PACOTE.match(pacote)) and pacote not in FORA_DOS_ACEITOS
            and "launcher" not in pacote and pacote not in set(fora))


@dataclass
class Vizinho:
    """Um pacote vizinho conhecido de um app: quantas sessões de ensino o declararam e quais."""

    pacote: str
    origens: list[str] = field(default_factory=list)

    @property
    def contagem(self) -> int:
        return len(self.origens)


def pares_dos_fluxos(linhas: Iterable[tuple[str | None, str | None, str | None]]
                     ) -> dict[str, dict[str, Vizinho]]:
    """`(source, app_id do fluxo, plano JSON)` de cada fluxo → `{app_id: {vizinho: Vizinho}}`, na ordem em que
    apareceram. Só o fluxo ensinado conta; o app da etapa é o `app_id` dela, senão o do plano, senão o do fluxo."""
    saida: dict[str, dict[str, Vizinho]] = {}
    for source, app_do_fluxo, texto in linhas:
        if not source or not source.startswith("training:"):
            continue
        try:
            plano = json.loads(texto or "null")
        except ValueError:
            continue
        if not isinstance(plano, dict):
            continue
        for passo in plano.get("steps") or []:
            if not isinstance(passo, dict):
                continue
            app = passo.get("app_id") or plano.get("app_id") or app_do_fluxo
            if not isinstance(app, str) or not app:
                continue
            for pacote in passo.get("pacotes_aceitos") or []:
                if not vizinho_valido(pacote):
                    continue
                v = saida.setdefault(app, {}).setdefault(pacote, Vizinho(pacote))
                if source not in v.origens:
                    v.origens.append(source)
    return saida


def aplicar(plan: Plan, conhecidos: Mapping[str, Mapping[str, Vizinho]], cadastrados: Iterable[str]
            ) -> tuple[Plan, list[tuple[str, list[Vizinho]]]]:
    """O plano livre com os vizinhos conhecidos nas etapas sem efeito e sem `pacotes_aceitos`, e o que mudou
    (`[(chave da etapa, vizinhos)]`, para a trilha). Sem nada a pôr, o mesmo plano."""
    fora = set(cadastrados)
    mudou: list[tuple[str, list[Vizinho]]] = []
    passos = []
    for s in plan.steps:
        app = s.app_id or plan.app_id
        vizinhos = [v for v in (conhecidos.get(app) or {}).values() if vizinho_valido(v.pacote, fora)] if app else []
        # a etapa de sessão, login ou desafio fica como veio: lá a tela fora do app não se aceita por semelhança
        if s.pacotes_aceitos or s.side_effect or s.commit_guard or not vizinhos or ACAO_DE_SESSAO.search(s.key):
            passos.append(s)
            continue
        passos.append(s.model_copy(update={"pacotes_aceitos": [v.pacote for v in vizinhos]}))
        mudou.append((s.key, vizinhos))
    return (plan.model_copy(update={"steps": passos}) if mudou else plan), mudou


def linha_da_trilha(mudou: list[tuple[str, list[Vizinho]]]) -> str:
    """A decisão do planejamento: que etapa passou a aceitar que vizinho, e de quantos ensinos veio (só ids)."""
    partes = [f"{chave}: " + ", ".join(f"{v.pacote} ({v.contagem} ensino(s): {', '.join(v.origens)})" for v in vs)
              for chave, vs in mudou]
    return ("Pacotes vizinhos conhecidos do app (31.152), aprendidos no ensino: a etapa aceita a conclusão nessa tela — "
            + "; ".join(partes) + ".")
