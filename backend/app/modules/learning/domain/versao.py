"""O estado de VERSÃO de um item do livro (30.6, `docs/design/aprendizado-vivo.md` §7), puro: sem banco, sem relógio.

A versão do app é o eixo que o dono não vê hoje: a receita é validada numa versão e o parque anda para outra. As
entradas são fatos já lidos (as versões VIVAS do app, as receitas da mesma chave) e a saída é o quadro do `versao`
do detalhe. A regra de ouro é a do resto do livro: o que não se sabe é `desconhecido`, escrito, e nunca um estado
inventado; incerteza não conta como validado.

Equivalência `recipes.app_version` × `device_app_state.observed_version_name` (a conferir no §7): as duas vêm do
mesmo `versionName` do aparelho, e aqui se comparam como TEXTO exato. Se um dia divergirem no formato, a versão da
receita some das vivas e aparece como `versao_aposentada` (visível), não como `comprovado`.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.modules.learning.domain.telas import sem_casar
from app.modules.skills.domain.document import JsonObject


class EstadoDeVersao(StrEnum):
    INDEPENDENTE = "independente"
    COMPROVADO = "comprovado"
    NAO_TESTADO = "nao_testado"
    EM_PROVA = "em_prova"
    FALHANDO = "falhando"
    INCOMPATIVEL = "incompativel"
    SUPERSEDED = "superseded"
    VERSAO_APOSENTADA = "versao_aposentada"
    #: Não é do §7: é o "não sei" explícito (sem pacote, sem versão, sem nenhum aparelho observado).
    DESCONHECIDO = "desconhecido"


@dataclass(frozen=True, slots=True)
class VersaoViva:
    """Uma versão do app observada hoje em aparelho ativo, e em quantos."""

    versao: str
    aparelhos: int


@dataclass(frozen=True, slots=True)
class ReceitaDaChave:
    """Uma receita da MESMA chave (pacote, assinatura, variante, `step_hash`), em qualquer versão do app."""

    ref: str
    app_version: str
    versao: int
    status: str
    replay_ok: int
    consecutive_fail: int
    criada_em: str


def _comprovada(r: ReceitaDaChave) -> bool:
    return r.status == "active" and r.replay_ok > 0 and r.consecutive_fail == 0


def estado_da_receita(r: ReceitaDaChave, *, vivas: frozenset[str], da_chave: Sequence[ReceitaDaChave]) -> EstadoDeVersao:
    """O estado de versão de UMA receita, na versão dela. Ordem: `superseded` (a linha foi trocada), versão fora de
    uso (não é falha), quarentena (`incompativel` se a de uma versão anterior estava comprovada, senão `falhando`),
    falha em sequência, `comprovado` (precisa da versão viva) e, por fim, `em_prova`."""
    if r.status == "superseded":
        return EstadoDeVersao.SUPERSEDED
    if vivas and r.app_version not in vivas:
        return EstadoDeVersao.VERSAO_APOSENTADA
    if r.status == "quarantined":
        anterior_comprovada = any(o.app_version != r.app_version and o.criada_em < r.criada_em and _comprovada(o)
                                  for o in da_chave)
        return EstadoDeVersao.INCOMPATIVEL if anterior_comprovada else EstadoDeVersao.FALHANDO
    if r.consecutive_fail > 0:
        return EstadoDeVersao.FALHANDO
    if r.status == "active" and r.replay_ok > 0:
        # Sem nenhuma versão viva observada não há como dizer que ela vale hoje: fica o "não sei".
        return EstadoDeVersao.COMPROVADO if vivas else EstadoDeVersao.DESCONHECIDO
    return EstadoDeVersao.EM_PROVA if r.status == "active" else EstadoDeVersao.DESCONHECIDO


def _vivas_json(vivas: Sequence[VersaoViva]) -> list[JsonObject]:
    return [{"versao": v.versao, "aparelhos": v.aparelhos} for v in vivas]


def quadro_da_receita(r: ReceitaDaChave, *, app: str, da_chave: Sequence[ReceitaDaChave],
                      vivas: Sequence[VersaoViva]) -> JsonObject:
    """O `versao` da receita: o estado dela na versão dela, as versões vivas e UMA linha por versão (as em que a
    chave tem receita e as vivas): `nao_testado` é a viva em que a chave não tem receita nenhuma. A receita da
    versão antiga continua valendo se um aparelho voltar para ela (o `find` usa a chave exata): nada é apagado."""
    vivas_por = {v.versao: v.aparelhos for v in vivas}
    nomes = frozenset(vivas_por)
    por_versao: list[JsonObject] = []
    for versao in sorted({o.app_version for o in da_chave} | nomes):
        daqui = [o for o in da_chave if o.app_version == versao]
        # A representante é a de maior versão da chave (a que o `find` pegaria); a trocada só aparece se for a única.
        melhor = max(daqui, key=lambda o: (o.status != "superseded", o.versao)) if daqui else None
        estado = EstadoDeVersao.NAO_TESTADO if melhor is None else estado_da_receita(melhor, vivas=nomes,
                                                                                      da_chave=da_chave)
        por_versao.append({"versao": versao, "viva": versao in nomes, "aparelhos": vivas_por.get(versao, 0),
                           "estado": estado.value, "receita_ref": melhor.ref if melhor else None})
    proprio = estado_da_receita(r, vivas=nomes, da_chave=da_chave)
    return {"estado": proprio.value, "app": app, "app_version": r.app_version, "vivas": _vivas_json(vivas),
            "nao_testada_em": [str(p["versao"]) for p in por_versao if p["estado"] == EstadoDeVersao.NAO_TESTADO.value],
            "por_versao": por_versao}


def quadro_independente() -> JsonObject:
    """Tipo que não depende de versão (declarado, fluxo, habilidade, lição, voz, preferência, memória)."""
    return {"estado": EstadoDeVersao.INDEPENDENTE.value, "app": None, "app_version": None, "vivas": [],
            "nao_testada_em": [], "por_versao": []}


def quadro_da_tela(*, app: str | None, app_version: str | None, vivas: Sequence[VersaoViva],
                   ultima_a_favor: datetime | None, criada: datetime | None, agora: datetime) -> JsonObject:
    """A tela aprendida entra no mesmo quadro: `incompativel` quando há dias sem casar E versão nova do app no
    parque (`telas.sem_casar`, a regra que já existe), `versao_aposentada` quando a versão dela saiu do parque; sem
    versão ou sem pacote, `desconhecido`. Dentro da janela e na versão viva, NÃO se promete `comprovado` (esse é
    estado de receita): fica `desconhecido`, que aqui significa "sem sinal de quebra"."""
    nomes = [v.versao for v in vivas]
    estado = EstadoDeVersao.DESCONHECIDO
    if app and app_version and nomes:
        if sem_casar(ultima_a_favor=ultima_a_favor, criada=criada or agora, versao_do_item=app_version,
                     versoes_recentes=nomes, agora=agora):
            estado = EstadoDeVersao.INCOMPATIVEL
        elif app_version not in nomes:
            estado = EstadoDeVersao.VERSAO_APOSENTADA
    return {"estado": estado.value, "app": app or None, "app_version": app_version, "vivas": _vivas_json(vivas),
            "nao_testada_em": [], "por_versao": []}


def agrupar_vivas(observadas: Iterable[tuple[str, int]]) -> tuple[VersaoViva, ...]:
    """(versão, aparelhos) já agregados → o quadro ordenado pela versão (texto), sem versão vazia."""
    return tuple(VersaoViva(v, n) for v, n in sorted(observadas) if v)


__all__ = ["EstadoDeVersao", "ReceitaDaChave", "VersaoViva", "agrupar_vivas", "estado_da_receita",
           "quadro_da_receita", "quadro_da_tela", "quadro_independente"]
