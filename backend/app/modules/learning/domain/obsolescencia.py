"""Obsolescência do item do Livro (30.14, `docs/design/aprendizado-vivo.md` §9.2): os sinais de que um item publicado
provavelmente já não serve, e o ÚNICO rebaixamento determinístico novo, `catalogo_sem_efeito`.

Puro: recebe o que já foi lido (o catálogo do app como `CatalogoDoApp`, o `conteudo` do 30.3, o `versao` do 30.6) e
devolve fatos. Quem lê o banco e o registro de apps é a aplicação e a infraestrutura.

Duas saídas, com pesos diferentes:
- `SinaisDeObsolescencia`: o que `domain/saude.py` transforma no rótulo `obsoleto_provavel` (só leitura, D-5: nada é
  desligado pela saúde). É também o dado que o curador (30.11) vai ler; aqui não há IA nem prompt;
- `VereditoDoCatalogo` com `rebaixa`: receita ou fluxo com `commit` num app cujo catálogo ATUAL não respalda o
  efeito. CONSERVADOR de propósito: só rebaixa quando (a) o app tem catálogo e nenhuma ação dele tem efeito externo,
  ou (b) a capability é conhecida sem ambiguidade e a ação dela no catálogo não tem efeito. App sem catálogo nunca
  (é a faixa B, segue a IA livre); capability desconhecida ou ambígua num catálogo que tem efeito vira só o sinal.

Sinais do §9.2 que NÃO têm fonte hoje e ficam fora (sinal inventado é pior que sinal ausente): "sem uso enquanto a
etapa continua sendo executada por outro caminho" (não há cruzamento de `steps` conduzidas por `ai` com o escopo do
item), "duplicado" de lição ou tela em chaves vizinhas (a contradição de mesmo `scope_key` já sai como relação, 30.7)
e "habilidade publicada com a mesma `match_key`" do fluxo (a guarda existe na escrita, `GuardaDoFluxo`, não na leitura
do item). As quedas de eficácia e a contestação já são `degradando`, que vem antes na ordem.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.skills.domain.document import JsonObject, JsonValue

#: O prefixo do motivo estruturado na trilha (`learning_transitions.reason`): `catalogo_sem_efeito:<capability|*>`.
PREFIXO_DO_MOTIVO = "catalogo_sem_efeito"
#: O catálogo inteiro não tem ação com efeito: o motivo não nomeia capability.
QUALQUER = "*"


@dataclass(frozen=True, slots=True)
class CatalogoDoApp:
    """O catálogo de ações de um app reduzido ao que a obsolescência pergunta: cada ação (`key`) tem efeito externo?"""

    efeito_por_acao: Mapping[str, bool] = field(default_factory=dict)

    @property
    def tem_efeito(self) -> bool:
        return any(self.efeito_por_acao.values())

    def efeito(self, capability: str) -> bool | None:
        """`None`: a ação não está no catálogo (desconhecida). A chave é comparada como o catálogo compara."""
        return self.efeito_por_acao.get(capability.strip().upper())


class Respaldo(StrEnum):
    RESPALDADO = "respaldado"
    SEM_RESPALDO = "sem_respaldo"          # rebaixa (determinístico)
    DUVIDOSO = "duvidoso"                  # só o sinal: capability desconhecida ou ambígua num catálogo com efeito


@dataclass(frozen=True, slots=True)
class VereditoDoCatalogo:
    respaldo: Respaldo
    #: A capability que o catálogo não respalda, ou `*` quando o catálogo inteiro não tem efeito; no duvidoso, os
    #: nomes considerados (ou `*` sem nenhum).
    capability: str
    #: O fato em português (o que foi comparado com o quê).
    fato: str

    @property
    def rebaixa(self) -> bool:
        return self.respaldo is Respaldo.SEM_RESPALDO

    @property
    def motivo(self) -> str:
        """O motivo estruturado da trilha. Só faz sentido quando `rebaixa`."""
        return f"{PREFIXO_DO_MOTIVO}:{self.capability}"


def _sem_efeito_no_catalogo() -> VereditoDoCatalogo:
    return VereditoDoCatalogo(Respaldo.SEM_RESPALDO, QUALQUER, "o catálogo do app não tem nenhuma ação com efeito externo")


def _uma(capability: str, catalogo: CatalogoDoApp) -> VereditoDoCatalogo:
    efeito = catalogo.efeito(capability)
    if efeito is None:
        return VereditoDoCatalogo(Respaldo.DUVIDOSO, capability, f"a capability {capability} não está no catálogo")
    if efeito:
        return VereditoDoCatalogo(Respaldo.RESPALDADO, capability, f"a ação {capability} do catálogo tem efeito")
    return VereditoDoCatalogo(Respaldo.SEM_RESPALDO, capability, f"a ação {capability} do catálogo não tem efeito externo")


def respaldo_da_receita(conteudo: JsonObject | None, catalogo: CatalogoDoApp | None, *,
                        tem_efeito: bool) -> VereditoDoCatalogo | None:
    """O catálogo respalda o `commit` da receita? `None`: não se aplica (sem `commit` ou app sem catálogo).

    A capability é a derivada pelo 30.3 (`conteudo.capability`: `nomes`, `ambigua`), lida só quando o catálogo tem
    alguma ação com efeito — sem nenhuma, o veredito não depende dela."""
    if not tem_efeito or catalogo is None:
        return None
    if not catalogo.tem_efeito:
        return _sem_efeito_no_catalogo()
    cap = (conteudo or {}).get("capability")
    nomes = [n for n in cap.get("nomes", []) if isinstance(n, str) and n] if isinstance(cap, dict) else []
    if not nomes:
        return VereditoDoCatalogo(Respaldo.DUVIDOSO, QUALQUER, "a capability da etapa é desconhecida")
    if len(nomes) == 1 and not (isinstance(cap, dict) and cap.get("ambigua") is True):
        return _uma(nomes[0], catalogo)
    if all(catalogo.efeito(n) is True for n in nomes):        # ambígua, mas qualquer uma delas tem efeito
        return VereditoDoCatalogo(Respaldo.RESPALDADO, ",".join(nomes), "todas as capabilities possíveis têm efeito")
    return VereditoDoCatalogo(Respaldo.DUVIDOSO, ",".join(nomes), f"a capability é ambígua: {', '.join(nomes)}")


def respaldo_do_fluxo(conteudo: JsonObject | None, catalogo: CatalogoDoApp | None, *,
                      tem_efeito: bool) -> VereditoDoCatalogo | None:
    """O mesmo para o fluxo: cada etapa com efeito (`conteudo.etapas[].efeito`) traz a capability no próprio plano. Uma
    etapa sem respaldo basta para rebaixar; uma duvidosa (sem capability, ou fora do catálogo) vira só o sinal."""
    if not tem_efeito or catalogo is None:
        return None
    if not catalogo.tem_efeito:
        return _sem_efeito_no_catalogo()
    etapas = (conteudo or {}).get("etapas")
    vereditos: list[VereditoDoCatalogo] = []
    for etapa in etapas if isinstance(etapas, list) else []:
        if not isinstance(etapa, dict) or etapa.get("efeito") is not True:
            continue
        cap = etapa.get("capability")
        vereditos.append(_uma(cap, catalogo) if isinstance(cap, str) and cap else
                         VereditoDoCatalogo(Respaldo.DUVIDOSO, QUALQUER, "uma etapa com efeito não tem capability"))
    if not vereditos:
        return VereditoDoCatalogo(Respaldo.DUVIDOSO, QUALQUER, "as etapas com efeito não foram lidas")
    for respaldo in (Respaldo.SEM_RESPALDO, Respaldo.DUVIDOSO):
        achado = next((v for v in vereditos if v.respaldo is respaldo), None)
        if achado is not None:
            return achado
    return vereditos[0]


def destino_do_rebaixamento(kind: LivroKind, estado: SkillState | None) -> SkillState | None:
    """Para onde o sistema leva o item sem respaldo (§9.2): em prova → `disabled`, publicado → `deprecated`. O fluxo
    não tem aposentadoria (`STATUS_DO_FLUXO`): publicado → `disabled`. `None`: já fora de circulação (nada a fazer).

    Ponto único de propósito: a receita `deprecated` vira `superseded` e o Livro não a reativa
    (`LearningService._mover_nativo`); se o dono preferir que ela volte por pessoa, a troca é esta linha."""
    if estado in (SkillState.CANDIDATE, SkillState.VALIDATED):
        return SkillState.DISABLED
    if estado is SkillState.PUBLISHED:
        return SkillState.DEPRECATED if kind is LivroKind.RECEITA else SkillState.DISABLED
    return None


# ------------------------------------------------------------------ sinais para o rótulo
@dataclass(frozen=True, slots=True)
class Substituta:
    ref: str
    estado: str                            # o status como a fonte o grava
    fonte: str


@dataclass(frozen=True, slots=True)
class SinaisDeObsolescencia:
    """O que cada fonte disse do item. Vazio = nenhum sinal (o rótulo segue a regra de antes)."""

    substituta_viva: Substituta | None = None
    #: A versão do item quando ela não está mais em nenhum aparelho ativo (receita, tela).
    versao_fora_do_parque: str | None = None
    versoes_vivas: tuple[str, ...] = ()
    #: Versões vivas do app em que a chave não tem receita (sem reprodução) e que não são provadamente mais antigas.
    versoes_sem_reproducao: tuple[str, ...] = ()
    efeito: VereditoDoCatalogo | None = None
    absorvida_em: str | None = None


_NUMEROS = re.compile(r"\d+")


def _numeros(versao: str) -> tuple[int, ...] | None:
    partes = _NUMEROS.findall(versao)
    return tuple(int(p) for p in partes) if partes else None


def versoes_sem_reproducao(app_version: str | None, nao_testada_em: Sequence[str]) -> tuple[str, ...]:
    """As versões vivas sem receita da chave que NÃO são provadamente mais antigas que a da receita. Versão que não se
    compara (sem número) entra: a dúvida não vira "está tudo bem"."""
    propria = _numeros(app_version) if app_version else None
    saida: list[str] = []
    for v in nao_testada_em:
        outra = _numeros(v)
        if propria is not None and outra is not None and outra <= propria:
            continue
        saida.append(v)
    return tuple(saida)


def do_quadro_de_versao(quadro: JsonValue) -> tuple[str | None, tuple[str, ...], tuple[str, ...]]:
    """(versão fora do parque, versões vivas, versões sem reprodução) a partir do `versao` do 30.6. Estado `incompativel`
    (a tela sem casar com versão nova no parque) conta como versão viva sem reprodução."""
    if not isinstance(quadro, dict):
        return None, (), ()
    estado, propria = quadro.get("estado"), quadro.get("app_version")
    vivas_brutas = quadro.get("vivas")
    vivas = tuple(str(v["versao"]) for v in vivas_brutas if isinstance(v, dict) and v.get("versao")) \
        if isinstance(vivas_brutas, list) else ()
    fora = propria if estado == "versao_aposentada" and isinstance(propria, str) else None
    nao_testadas = quadro.get("nao_testada_em")
    lista = [str(v) for v in nao_testadas] if isinstance(nao_testadas, list) else []
    sem = versoes_sem_reproducao(propria if isinstance(propria, str) else None, lista)
    if estado == "incompativel" and not sem:
        sem = tuple(v for v in vivas if v != propria)
    return fora, vivas, sem


__all__ = ["PREFIXO_DO_MOTIVO", "QUALQUER", "CatalogoDoApp", "Respaldo", "SinaisDeObsolescencia", "Substituta",
           "VereditoDoCatalogo", "destino_do_rebaixamento", "do_quadro_de_versao", "respaldo_da_receita",
           "respaldo_do_fluxo", "versoes_sem_reproducao"]
