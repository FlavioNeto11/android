"""Conhecimento de TELAS de um app, como dado, e o motor genérico que o lê (ADR-052, fatia 1).

Um app declara num arquivo (YAML) os sinais de texto por idioma, as regras de tela em ordem de precedência, as
extrações (quem está logado) e o "estado conhecido" — a tela de onde as leituras funcionam e como voltar a ela sem
efeito externo. Este módulo não conhece app nenhum: interpreta o que o arquivo diz. É o que deixa um app novo ganhar
classificação de tela e volta ao estado conhecido sem escrever Python, e o que deixa um app existente aprender uma
tela nova mudando dado (execução r-20260928165254-e31953: a conversa aberta não estava em nenhuma tabela, e a
checagem de sessão chamou uma pessoa).

A única peça que continua vindo de fora é a detecção do formulário de senha (`formulario`): ela é heurística de
geometria e fica com quem já a tem até a fatia do fluxo de sessão declarativo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from .hierarchy import UiTree

#: Vocabulário dos motores. Quem trata cada tipo é o núcleo (sessão, executor), não o app.
TIPOS = frozenset({"desafio", "dois_fatores", "intersticial", "login", "carregando", "autenticada"})
#: Tipos de onde nunca se "volta": cada um tem tratamento próprio (pessoa, login, dispensa, espera).
NAO_SE_VOLTA = frozenset({"desafio", "dois_fatores", "login", "intersticial", "carregando"})
DESCONHECIDA = "desconhecida"


class ConhecimentoInvalido(ValueError):
    """O arquivo de conhecimento não se sustenta: recusa na carga, antes de classificar a primeira tela."""


@dataclass(frozen=True, slots=True)
class RegraDeTela:
    tela: str
    tipo: str
    razao: str
    autenticada: bool = False
    sinal: str | None = None
    formulario_de_senha: bool = False
    sem_elementos: bool = False
    ids: tuple[str, ...] = ()
    extracao: str | None = None


@dataclass(frozen=True, slots=True)
class Extracao:
    ids: tuple[str, ...]
    padrao: re.Pattern[str]
    arroba_solto: bool = False


@dataclass(frozen=True, slots=True)
class EstadoConhecido:
    telas: tuple[str, ...]
    voltar_max: int = 4
    reabrir: bool = True


@dataclass(frozen=True, slots=True)
class ConhecimentoDeTelas:
    app: str
    versao: int
    idioma_padrao: str
    sinais: dict[str, dict[str, re.Pattern[str]]]
    telas: tuple[RegraDeTela, ...]
    extracoes: dict[str, Extracao]
    estado_conhecido: EstadoConhecido

    def sinais_de(self, locale: str | None) -> dict[str, re.Pattern[str]]:
        """`pt-BR` -> tabela `pt`; idioma não declarado cai no padrão do app."""
        codigo = (locale or "").split("-")[0].split("_")[0].lower()
        return self.sinais.get(codigo, self.sinais[self.idioma_padrao])

    def regra(self, tela: str) -> RegraDeTela | None:
        return next((r for r in self.telas if r.tela == tela), None)

    def autenticada(self, tela: str) -> bool:
        regra = self.regra(tela)
        return bool(regra and regra.autenticada)

    def em_casa(self, tela: str) -> bool:
        return tela in self.estado_conhecido.telas

    def extrair(self, nome: str, tree: UiTree, *, palpite: bool = True) -> str | None:
        """Valor de uma extração declarada: primeiro pelos ids; com `palpite`, pelo primeiro `@texto` da tela."""
        ex = self.extracoes[nome]
        for e in tree.elements:
            if _sufixo(e.resource_id) in ex.ids and (m := ex.padrao.match((e.text or "").strip())):
                return m.group(1).lower()
        if palpite and ex.arroba_solto:
            for e in tree.elements:
                texto = (e.text or "").strip()
                if texto.startswith("@") and (m := ex.padrao.match(texto)):
                    return m.group(1).lower()
        return None


@dataclass(slots=True)
class TelaReconhecida:
    tela: str
    tipo: str
    razao: str
    evidencia: list[str] = field(default_factory=list)
    formulario: object | None = None
    outro_app: bool = False


def _sufixo(resource_id: str) -> str:
    return resource_id.rsplit("/", 1)[-1].lower()


def _tem_id(tree: UiTree, prefixos: tuple[str, ...]) -> bool:
    return any(_sufixo(e.resource_id).startswith(prefixos) for e in tree.elements)


def _texto_da_tela(tree: UiTree) -> str:
    return "\n".join(f"{e.text} {e.desc}".strip() for e in tree.elements if e.text or e.desc)


def _trechos(padrao: re.Pattern[str], texto: str) -> list[str]:
    return [m.group(0)[:80] for m in list(padrao.finditer(texto))[:3]]


def classificar(k: ConhecimentoDeTelas, tree: UiTree, *, package: str | None, locale: str | None = None,
                formulario: Callable[[UiTree], object | None] | None = None) -> TelaReconhecida:
    """A primeira regra declarada que casar vence. Outro app na frente não é classificado."""
    if package and package != k.app:
        return TelaReconhecida(DESCONHECIDA, DESCONHECIDA, f"outro app em primeiro plano ({package})", outro_app=True)
    texto = _texto_da_tela(tree)
    sinais = k.sinais_de(locale)
    for r in k.telas:
        if r.sinal is not None and not ((p := sinais.get(r.sinal)) and p.search(texto)):
            continue
        form = None
        if r.formulario_de_senha:
            form = formulario(tree) if formulario is not None else None
            if form is None:
                continue
        if r.sem_elementos and tree.elements:
            continue
        if r.extracao is not None and not k.extrair(r.extracao, tree):
            continue
        if r.ids and not _tem_id(tree, r.ids):
            continue
        evidencia = _trechos(sinais[r.sinal], texto) if r.sinal and r.tipo in ("desafio", "dois_fatores") else []
        return TelaReconhecida(r.tela, r.tipo, r.razao, evidencia, form)
    return TelaReconhecida(DESCONHECIDA, DESCONHECIDA, "nenhum sinal conhecido na tela")


async def voltar_ao_estado_conhecido(
        k: ConhecimentoDeTelas, *,
        observar: Callable[[], Awaitable[tuple[UiTree, str | None]]],
        voltar: Callable[[], Awaitable[None]],
        reabrir: Callable[[], Awaitable[None]],
        reconhecer: Callable[[UiTree, str | None], TelaReconhecida],
) -> tuple[UiTree, str | None, TelaReconhecida, list[str]]:
    """Leva o app ao estado conhecido declarado, só com "voltar" do Android e, no máximo uma vez, reabrir o app.

    Para numa tela de casa ou numa tela com tratamento próprio (login, desafio, 2FA, intersticial, carregando): dela
    ninguém "volta" por conta própria. Devolve a última observação e os passos dados, para quem chama registrar.
    Nenhum passo aqui tem efeito externo: voltar e abrir o app não publicam, não enviam, não seguem.
    """
    passos: list[str] = []
    tree, pacote = await observar()
    atual = reconhecer(tree, pacote)
    reaberto = False
    for _ in range(k.estado_conhecido.voltar_max + 1):
        if k.em_casa(atual.tela) or atual.tipo in NAO_SE_VOLTA:
            return tree, pacote, atual, passos
        if atual.outro_app:
            if reaberto or not k.estado_conhecido.reabrir:
                return tree, pacote, atual, passos
            await reabrir()
            reaberto = True
            passos.append("reabrir")
        elif len([p for p in passos if p == "voltar"]) < k.estado_conhecido.voltar_max:
            await voltar()
            passos.append("voltar")
        else:
            break
        tree, pacote = await observar()
        atual = reconhecer(tree, pacote)
    if not k.em_casa(atual.tela) and atual.tipo not in NAO_SE_VOLTA and k.estado_conhecido.reabrir and not reaberto:
        await reabrir()
        passos.append("reabrir")
        tree, pacote = await observar()
        atual = reconhecer(tree, pacote)
    return tree, pacote, atual, passos


# ---------------------------------------------------------------------------------------------------- carga
def _regex(valor: object, onde: str) -> re.Pattern[str]:
    if not isinstance(valor, str) or not valor:
        raise ConhecimentoInvalido(f"{onde}: esperava uma expressão regular em texto")
    try:
        return re.compile(valor, re.IGNORECASE)
    except re.error as exc:
        raise ConhecimentoInvalido(f"{onde}: expressão regular inválida ({exc})") from exc


def _mapa(valor: object, onde: str) -> dict[str, object]:
    if valor is None:
        return {}
    if not isinstance(valor, dict):
        raise ConhecimentoInvalido(f"{onde}: esperava um mapa")
    return {str(k): v for k, v in valor.items()}


def _lista(valor: object, onde: str) -> list[object]:
    if valor is None:
        return []
    if not isinstance(valor, list):
        raise ConhecimentoInvalido(f"{onde}: esperava uma lista")
    return list(valor)


def _textos(valor: object, onde: str) -> tuple[str, ...]:
    return tuple(str(x).lower() for x in _lista(valor, onde))


def de_dados(dados: object) -> ConhecimentoDeTelas:
    """Valida e monta. Recusa referência a sinal, extração ou tela de casa que não existe, tipo fora do vocabulário e
    valor do tipo errado — um arquivo errado falha na carga, não no meio de uma execução."""
    raiz = _mapa(dados, "o arquivo")
    app = str(raiz.get("app") or "").strip()
    if not app:
        raise ConhecimentoInvalido("falta `app` (o pacote Android)")
    sinais = {idioma: {nome: _regex(v, f"sinais.{idioma}.{nome}")
                       for nome, v in _mapa(tabela, f"sinais.{idioma}").items()}
              for idioma, tabela in _mapa(raiz.get("sinais"), "sinais").items()}
    idioma_padrao = str(raiz.get("idioma_padrao") or "")
    if idioma_padrao not in sinais:
        raise ConhecimentoInvalido(f"`idioma_padrao` ({idioma_padrao!r}) precisa estar em `sinais`")
    extracoes: dict[str, Extracao] = {}
    for nome, bruto in _mapa(raiz.get("extracoes"), "extracoes").items():
        ex = _mapa(bruto, f"extracoes.{nome}")
        extracoes[nome] = Extracao(ids=_textos(ex.get("ids"), f"extracoes.{nome}.ids"),
                                   padrao=_regex(ex.get("padrao"), f"extracoes.{nome}.padrao"),
                                   arroba_solto=bool(ex.get("arroba_solto", False)))
    regras: list[RegraDeTela] = []
    for i, bruto in enumerate(_lista(raiz.get("telas"), "telas")):
        onde = f"telas[{i}]"
        r = _mapa(bruto, onde)
        tipo = str(r.get("tipo") or "")
        if tipo not in TIPOS:
            raise ConhecimentoInvalido(f"{onde}: tipo {tipo!r} fora de {sorted(TIPOS)}")
        sinal = None if r.get("sinal") is None else str(r.get("sinal"))
        if sinal is not None and sinal not in sinais[idioma_padrao]:
            raise ConhecimentoInvalido(f"{onde}: sinal {sinal!r} não declarado no idioma padrão")
        extracao = None if r.get("extracao") is None else str(r.get("extracao"))
        if extracao is not None and extracao not in extracoes:
            raise ConhecimentoInvalido(f"{onde}: extração {extracao!r} não declarada")
        nome_da_tela = str(r.get("tela") or "")
        if not nome_da_tela:
            raise ConhecimentoInvalido(f"{onde}: falta `tela`")
        regras.append(RegraDeTela(tela=nome_da_tela, tipo=tipo, razao=str(r.get("razao") or nome_da_tela),
                                  autenticada=bool(r.get("autenticada", False)), sinal=sinal,
                                  formulario_de_senha=bool(r.get("formulario_de_senha", False)),
                                  sem_elementos=bool(r.get("sem_elementos", False)),
                                  ids=_textos(r.get("ids"), f"{onde}.ids"), extracao=extracao))
    if not regras:
        raise ConhecimentoInvalido("nenhuma regra em `telas`")
    ec = _mapa(raiz.get("estado_conhecido"), "estado_conhecido")
    casa = tuple(str(t) for t in _lista(ec.get("telas"), "estado_conhecido.telas"))
    nomes = {t.tela for t in regras}
    if not casa or any(t not in nomes for t in casa):
        raise ConhecimentoInvalido("`estado_conhecido.telas` precisa listar telas declaradas")
    voltar_max = ec.get("voltar_max", 4)
    if not isinstance(voltar_max, int) or not 0 <= voltar_max <= 10:
        raise ConhecimentoInvalido("`estado_conhecido.voltar_max` precisa ser um inteiro de 0 a 10")
    versao = raiz.get("versao", 1)
    return ConhecimentoDeTelas(app=app, versao=versao if isinstance(versao, int) else 1, idioma_padrao=idioma_padrao,
                               sinais=sinais, telas=tuple(regras), extracoes=extracoes,
                               estado_conhecido=EstadoConhecido(telas=casa, voltar_max=voltar_max,
                                                                reabrir=bool(ec.get("reabrir", True))))


def carregar(caminho: Path) -> ConhecimentoDeTelas:
    return de_dados(yaml.safe_load(caminho.read_text(encoding="utf-8")) or {})
