"""A visão por app (aprendizado vivo §3, 30.1): uma COMPOSIÇÃO DE LEITURA, sem tabela nova e sem cópia.

`apps_do_aprendizado = registro de apps ∪ apps da loja (tabela `apps`) ∪ pacotes com linhas no livro`. Para cada app:

- o DECLARADO: quais arquivos a pasta do repositório tem (`app`, `catalogo`, `telas`, `sessao`), quantas ações e
  quantas telas, lido do registro a cada pedido (nada é copiado para o banco); mais o que a loja guarda do app;
- o APRENDIDO: o que o livro tem para o pacote, por tipo e estado (o que foi absorvido pelo repositório sai daqui);
- o ABSORVIDO: o que foi aprendido e hoje é declarado (`state_detail = absorvida:<commit>`), que não some;
- a CAMADA DE USO por tipo (`domain/camada.py`): o que cada conhecimento faz hoje.

Um app sem linha aparece com zeros, não some. O que o livro não consegue atribuir a um pacote fica em
`nao_resolvido` (30.2) e o que não tem eixo de app (memória da persona, lição sem app) em `fora_do_eixo`. As fontes
(registro e loja) entram por porta: a composição passa as reais, os testes, falsas. Camada de aplicação: sem SQL e sem
registro importado.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.camada import (ArquivoDeclarado, Existencia, ModosDeRuntime, ModosDeUso,
                                                OrigemNaVisao, Uso, origem_do_aprendido, uso_do_declarado,
                                                uso_do_item)
from app.modules.learning.domain.ciclo import NaoEncontrado
from app.modules.learning.domain.livro import EntradaDoLivro, contagem
from app.modules.learning.domain.vocabulario import APP_NAO_RESOLVIDO, absorvida_em


@dataclass(frozen=True, slots=True)
class Declarado:
    """O que o REPOSITÓRIO declara de um app, lido do registro (e da pasta) na hora."""

    package: str
    name: str
    tem_app: bool = False
    tem_catalogo: bool = False
    tem_telas: bool = False
    tem_sessao: bool = False
    acoes: int | None = None                # ações do catálogo; `None` = o app não tem catálogo
    telas: int | None = None                # regras de tela do `telas.yaml`; `None` = não tem ou não carregou
    login_gerenciado: bool = False          # declara provedor de sessão de conta


@dataclass(frozen=True, slots=True)
class AppDaLoja:
    """Uma linha da tabela `apps` (a loja)."""

    package: str
    name: str
    nav_hints: bool = False
    known_selectors: bool = False


class FonteDeDeclarados(Protocol):
    def declarados(self) -> list[Declarado]: ...


class FonteDaLoja(Protocol):
    def apps(self) -> list[AppDaLoja]: ...


@dataclass(frozen=True, slots=True)
class LinhaDoAprendido:
    entrada: EntradaDoLivro
    origem: OrigemNaVisao
    uso: Uso
    absorvida_em: str | None = None


@dataclass(frozen=True, slots=True)
class ItemDeclarado:
    """Um arquivo declarado do app na visão (a origem `declarado` do §3.2)."""

    arquivo: ArquivoDeclarado
    presente: bool
    quantidade: int | None
    uso: Uso | None                         # `None` = arquivo ausente: não faz nada
    nome_do_arquivo: str | None             # no repositório; `None` na loja


@dataclass(frozen=True, slots=True)
class ResumoDoApp:
    pacote: str
    nome: str
    existencia: Existencia | None           # `None` no balde `nao_resolvido`
    declarado: Declarado | None
    loja: AppDaLoja | None
    total_aprendido: int
    contagem: dict[str, dict[str, int]]
    absorvido: int
    uso: dict[str, dict[str, int]]


@dataclass(frozen=True, slots=True)
class VisaoDeApps:
    apps: tuple[ResumoDoApp, ...]
    nao_resolvido: ResumoDoApp
    #: Sem eixo de app (memória da persona, voz, preferência, lição sem app): contagem por tipo e estado.
    fora_do_eixo: dict[str, dict[str, int]]
    modos: ModosDeUso = field(default_factory=ModosDeUso)


@dataclass(frozen=True, slots=True)
class DetalheDoApp:
    resumo: ResumoDoApp
    declarado: tuple[ItemDeclarado, ...]
    aprendido: tuple[LinhaDoAprendido, ...]
    absorvido: tuple[LinhaDoAprendido, ...]
    modos: ModosDeUso = field(default_factory=ModosDeUso)


_NOME_DO_ARQUIVO = {ArquivoDeclarado.APP: "app.yaml", ArquivoDeclarado.CATALOGO: "catalogo.yaml",
                    ArquivoDeclarado.TELAS: "telas.yaml", ArquivoDeclarado.SESSAO: "sessao.yaml"}


class VisaoPorApp:
    def __init__(self, servico: LearningService, declarados: FonteDeDeclarados, loja: FonteDaLoja) -> None:
        self._servico = servico
        self._declarados = declarados
        self._loja = loja

    # ------------------------------------------------------------------ leitura
    def apps(self, runtime: ModosDeRuntime | None = None) -> VisaoDeApps:
        modos = self._modos(runtime)
        declarados, loja, por_app, fora = self._ler()
        todos = (set(declarados) | set(loja) | set(por_app)) - {APP_NAO_RESOLVIDO}
        pacotes = sorted(todos, key=lambda p: (_nome(p, declarados, loja), p))
        resumos = tuple(self._resumo(p, declarados.get(p), loja.get(p), por_app.get(p, []), modos) for p in pacotes)
        balde = self._resumo(APP_NAO_RESOLVIDO, None, None, por_app.get(APP_NAO_RESOLVIDO, []), modos)
        return VisaoDeApps(resumos, balde, contagem(fora), modos)

    def detalhe(self, pacote: str, runtime: ModosDeRuntime | None = None) -> DetalheDoApp:
        modos = self._modos(runtime)
        declarados, loja, por_app, _ = self._ler()
        if pacote not in declarados and pacote not in loja and pacote not in por_app and pacote != APP_NAO_RESOLVIDO:
            raise NaoEncontrado(f"Não há app '{pacote}' na visão do aprendizado: nem declarado, nem na loja, nem "
                                "com linha no livro.")
        d, lj = declarados.get(pacote), loja.get(pacote)
        linhas = [_linha(e, modos) for e in por_app.get(pacote, [])]
        return DetalheDoApp(
            resumo=self._resumo(pacote, d, lj, por_app.get(pacote, []), modos),
            declarado=_itens_declarados(d, lj),
            aprendido=tuple(x for x in linhas if x.origem is OrigemNaVisao.APRENDIDO),
            absorvido=tuple(x for x in linhas if x.origem is OrigemNaVisao.ABSORVIDO), modos=modos)

    # ------------------------------------------------------------------ montagem
    def _modos(self, runtime: ModosDeRuntime | None) -> ModosDeUso:
        r = runtime or ModosDeRuntime()
        ajustes = self._servico.ajustes
        return ModosDeUso(receitas=r.receitas, fluxos=r.fluxos, habilidades=r.habilidades, licoes=ajustes.modo_licoes,
                          telas=ajustes.modo_telas, licoes_por_app=ajustes.por_licoes,
                          telas_por_app=ajustes.por_telas)

    def _ler(self) -> tuple[dict[str, Declarado], dict[str, AppDaLoja], dict[str, list[EntradaDoLivro]],
                            list[EntradaDoLivro]]:
        declarados = {d.package: d for d in self._declarados.declarados()}
        loja = {a.package: a for a in self._loja.apps()}
        por_app: dict[str, list[EntradaDoLivro]] = {}
        fora: list[EntradaDoLivro] = []
        for e in self._servico.livro().itens:
            if e.app is None:
                fora.append(e)
            else:
                por_app.setdefault(e.app, []).append(e)
        return declarados, loja, por_app, fora

    def _resumo(self, pacote: str, d: Declarado | None, lj: AppDaLoja | None, entradas: Sequence[EntradaDoLivro],
                modos: ModosDeUso) -> ResumoDoApp:
        linhas = [_linha(e, modos) for e in entradas]
        aprendidas = [x for x in linhas if x.origem is OrigemNaVisao.APRENDIDO]
        uso: dict[str, Counter[str]] = {}
        for x in aprendidas:
            uso.setdefault(x.entrada.kind.value, Counter())[x.uso.camada.value] += 1
        existencia = None
        if pacote != APP_NAO_RESOLVIDO:
            existencia = (Existencia.DECLARADO if d is not None else Existencia.LOJA if lj is not None
                          else Existencia.SO_APRENDIDO)
        return ResumoDoApp(
            pacote=pacote, nome=_nome(pacote, {pacote: d} if d else {}, {pacote: lj} if lj else {}),
            existencia=existencia, declarado=d, loja=lj, total_aprendido=len(aprendidas),
            contagem=contagem(x.entrada for x in aprendidas), absorvido=len(linhas) - len(aprendidas),
            uso={k: dict(v) for k, v in uso.items()})


def _nome(pacote: str, declarados: dict[str, Declarado] | dict[str, Declarado | None],
          loja: dict[str, AppDaLoja] | dict[str, AppDaLoja | None]) -> str:
    d, lj = declarados.get(pacote), loja.get(pacote)
    if d is not None and d.name:
        return d.name
    if lj is not None and lj.name:
        return lj.name
    return pacote


def _linha(e: EntradaDoLivro, modos: ModosDeUso) -> LinhaDoAprendido:
    origem = origem_do_aprendido(e.kind, e.detail)
    return LinhaDoAprendido(e, origem, uso_do_item(e.kind, e.state, modos.do_pacote(e.app), detalhe=e.detail),
                            absorvida_em(e.detail) if origem is OrigemNaVisao.ABSORVIDO else None)


def _itens_declarados(d: Declarado | None, lj: AppDaLoja | None) -> tuple[ItemDeclarado, ...]:
    itens: list[ItemDeclarado] = []
    if d is not None:
        for arquivo, presente, qtd in ((ArquivoDeclarado.APP, d.tem_app, None),
                                       (ArquivoDeclarado.CATALOGO, d.tem_catalogo, d.acoes),
                                       (ArquivoDeclarado.TELAS, d.tem_telas, d.telas),
                                       (ArquivoDeclarado.SESSAO, d.tem_sessao, None)):
            itens.append(ItemDeclarado(arquivo, presente, qtd if presente else None,
                                       uso_do_declarado(arquivo) if presente else None, _NOME_DO_ARQUIVO[arquivo]))
    if lj is not None:
        itens.append(ItemDeclarado(ArquivoDeclarado.LOJA, True, None,
                                   uso_do_declarado(ArquivoDeclarado.LOJA, com_conteudo=lj.nav_hints
                                                    or lj.known_selectors), None))
    return tuple(itens)
