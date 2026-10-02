"""Consumidor de SOMBRA da intenção (item 31.9, ADR-069): R2 (etapa semântica) e R3 (desempate), FORA da cadeia.

O que ele faz, depois que o `_plan` de uma execução terminou: monta UM pedido `DecisaoFechada` (origem `intencao`, classe C3,
modo `shadow`) com o comando sanitizado e duas perguntas `choice`, e deixa a porta gravar a sombra (31.5). Depois casa, nas
linhas gravadas, o que a cadeia REAL resolveu. Nada do que o Jev responde volta ao caminho de trabalho: a cadeia
(`intent_resolver`, `intent_ports`) não é tocada e a sombra só observa.

- **R2** (`intencao_catalogo`): `choice` sobre o catálogo inteiro (habilidades publicadas e fluxos ativos, como ids opacos com
  descrição C2) mais `nenhuma`. Só vai com 1 a 254 entradas: truncar enviaria um catálogo incompleto e a sombra mediria o
  que o Jev NÃO viu. Decisão real: a habilidade que a cadeia resolveu (ou de que fala, quando falta parâmetro), ou `nenhuma`
  quando nada casou. Empate sem desfecho: fica vazio (ninguém decidiu ainda).
- **R3** (`intencao_desempate`): `choice` entre os candidatos que a cadeia registrou como empatados (2 ou mais). Decisão real
  só quando a cadeia chegou a escolher um deles; senão vazia, e quem sabe a escolha da pessoa (31.10) preenche.
- **C3**: o comando passa por `redact` e por `remover_entidades`, que falha fechada. Se sobrar entidade, o estado vai VAZIO e a
  porta recusa (`privacidade`): a linha da sombra existe, com o motivo, e nada sai.
- **Desligado** (padrão: `enabled=false`, consumidor `off`): `ativo()` é falso, o chamador nem monta nada.

O `desfecho` (`casar_desfecho`) não é preenchido aqui: precisa do fim da execução, que é núcleo compartilhado, e fica para o
31.10. O módulo não importa `modules.skills` nem o serviço de fila: quem o liga (`taskqueue/sombra_intencao.py`) converte.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from ...security.redaction import redact
from .contrato import ID_NENHUMA, MAX_OPCOES, PedidoDeDecisao, Pergunta, pergunta_choice
from .entidades import remover_entidades
from .porta import TIMEOUT_SHADOW_S, Porta, modo_efetivo
from .sombra import RepositorioDeSombra

log = logging.getLogger("poc.ai")

PERGUNTA_CATALOGO: Final = "intencao_catalogo"
PERGUNTA_DESEMPATE: Final = "intencao_desempate"

#: Entradas do catálogo que cabem em R2 (a `nenhuma` ocupa a 255ª).
MAX_CATALOGO: Final = MAX_OPCOES - 1
_DESCRICAO_MAX: Final = 200
_APP: Final = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")

_INSTRUCOES_CATALOGO: Final = (
    "The state is a command written by the owner of a device fleet, in Portuguese, with names and numbers masked. Pick the "
    "catalog entry that this command asks to run, or none if no entry clearly fits.")
_INSTRUCOES_DESEMPATE: Final = (
    "The state is a command written by the owner of a device fleet, in Portuguese, with names and numbers masked. Several "
    "catalog entries match it equally. Pick the one the command asks for, or none if it cannot be told.")


@dataclass(frozen=True)
class EntradaDeCatalogo:
    """Uma habilidade publicada ou fluxo ativo. `descricao` é texto do catálogo do dono (C2)."""

    skill_id: str
    nome: str
    descricao: str = ""


@dataclass(frozen=True)
class CadeiaObservada:
    """O que a cadeia REAL fez com o comando, em ids de habilidade (sem versão).

    `resolvida`: a habilidade escolhida, ou a única de que a cadeia fala quando falta parâmetro; `sem_casamento`: nada casou
    (o planejador fica com o comando); `empatados`: as habilidades entre as quais nada decidiu, ou que a cadeia desempatou."""

    resolvida: str | None = None
    sem_casamento: bool = False
    empatados: tuple[str, ...] = ()


def id_opaco(skill_id: str) -> str:
    """Id opaco e estável da opção: o nome da habilidade nunca vai no id, e o id cabe no formato que a sombra aceita."""
    return "opt:" + hashlib.sha1(skill_id.encode("utf-8")).hexdigest()[:12]


def _descricao(e: EntradaDeCatalogo) -> str:
    texto = " ".join(f"{e.nome}: {e.descricao}".split() if e.descricao else e.nome.split())
    return texto[:_DESCRICAO_MAX] or "(sem nome)"


def _opcoes(entradas: Sequence[EntradaDeCatalogo]) -> dict[str, str]:
    return {id_opaco(e.skill_id): _descricao(e) for e in sorted(entradas, key=lambda e: e.skill_id)}


class ConsumidorDeIntencao:
    def __init__(self, porta: Porta, repositorio: RepositorioDeSombra, *, espera_s: float = TIMEOUT_SHADOW_S + 2.0) -> None:
        self._porta = porta
        self._repositorio = repositorio
        self._espera_s = espera_s

    def ativo(self) -> bool:
        """Com a config padrão (`enabled=false`, consumidor `off`) é falso: nada é montado, nada é chamado."""
        return modo_efetivo("shadow", self._porta.cfg, "intencao") == "shadow"

    def pedido(self, *, run_id: str, comando: str, app: str | None, catalogo: Sequence[EntradaDeCatalogo],
               cadeia: CadeiaObservada) -> PedidoDeDecisao | None:
        """O pedido de sombra, ou `None` se não há pergunta a fazer. Sanitiza o comando (C3); sobra de entidade = estado vazio."""
        perguntas: list[Pergunta] = []
        if 0 < len(catalogo) <= MAX_CATALOGO:
            perguntas.append(pergunta_choice(PERGUNTA_CATALOGO, _INSTRUCOES_CATALOGO, _opcoes(catalogo)))
        elif catalogo:
            log.info("decisao_fechada: catálogo de %d entradas acima do teto; R2 não vai", len(catalogo))
        por_id = {e.skill_id: e for e in catalogo}
        empatados = [por_id[s] for s in dict.fromkeys(cadeia.empatados) if s in por_id]
        if len(empatados) >= 2:
            perguntas.append(pergunta_choice(PERGUNTA_DESEMPATE, _INSTRUCOES_DESEMPATE, _opcoes(empatados)))
        if not perguntas:
            return None
        limpo = remover_entidades(redact(comando) or "") if comando.strip() else None
        estado: dict[str, str] = {}
        if limpo:                                    # `None` (sobrou entidade) ou vazio: estado vazio, a porta recusa
            estado["comando"] = limpo
            if app and _APP.fullmatch(app):
                estado["app"] = app
        return PedidoDeDecisao(origem="intencao", classe="C3", estado=estado, perguntas=tuple(perguntas), modo="shadow",
                               run_id=run_id, ref=run_id)

    def observar(self, *, run_id: str, comando: str, app: str | None, catalogo: Sequence[EntradaDeCatalogo],
                 cadeia: CadeiaObservada) -> None:
        """Bloqueante (roda numa thread, nunca no laço): consulta a porta em shadow e casa a decisão real.

        A porta grava a linha numa thread própria DEPOIS de devolver; por isso o casamento tenta até a linha existir (ou a
        espera acabar). Qualquer falha é engolida: medir nunca derruba o trabalho."""
        try:
            if not self.ativo():
                return
            pedido = self.pedido(run_id=run_id, comando=comando, app=app, catalogo=catalogo, cadeia=cadeia)
            if pedido is None:
                return
            self._porta.consultar(pedido)
            reais = self.decisoes_reais(cadeia, {p.id for p in pedido.perguntas})
            if reais:
                self._casar(run_id, reais)
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: falha na sombra da intenção")

    @staticmethod
    def decisoes_reais(cadeia: CadeiaObservada, perguntas: set[str]) -> dict[str, str]:
        """{pergunta: id opaco do que a cadeia real resolveu}. Pergunta sem decisão real conhecida fica de fora."""
        reais: dict[str, str] = {}
        if PERGUNTA_CATALOGO in perguntas:
            if cadeia.resolvida is not None:
                reais[PERGUNTA_CATALOGO] = id_opaco(cadeia.resolvida)
            elif cadeia.sem_casamento:
                reais[PERGUNTA_CATALOGO] = ID_NENHUMA
        if PERGUNTA_DESEMPATE in perguntas and cadeia.resolvida is not None and cadeia.resolvida in cadeia.empatados:
            reais[PERGUNTA_DESEMPATE] = id_opaco(cadeia.resolvida)
        return reais

    def _casar(self, run_id: str, reais: dict[str, str]) -> None:
        limite = time.monotonic() + self._espera_s
        while True:
            if self._repositorio.casar_decisao_real(reais, ref=run_id):
                return
            if time.monotonic() >= limite:
                log.warning("decisao_fechada: a linha da sombra da intenção não apareceu a tempo")
                return
            time.sleep(0.05)


__all__ = ["CadeiaObservada", "ConsumidorDeIntencao", "EntradaDeCatalogo", "MAX_CATALOGO", "PERGUNTA_CATALOGO",
           "PERGUNTA_DESEMPATE", "id_opaco"]
