"""Conhecimento de TELAS de um app, como dado, e o motor genérico que o lê (ADR-052, fatia 1).

Um app declara num arquivo (YAML) os sinais de texto por idioma, as regras de tela em ordem de precedência, as
extrações (quem está logado) e o "estado conhecido" — a tela de onde as leituras funcionam e como voltar a ela sem
efeito externo. Este módulo não conhece app nenhum: interpreta o que o arquivo diz. É o que deixa um app novo ganhar
classificação de tela e volta ao estado conhecido sem escrever Python, e o que deixa um app existente aprender uma
tela nova mudando dado (execução r-20260928165254-e31953: a conversa aberta não estava em nenhuma tabela, e a
checagem de sessão chamou uma pessoa).

A única peça que continua vindo de fora é a detecção do formulário de senha (`formulario`): ela é heurística de
geometria e fica com quem já a tem até a fatia do fluxo de sessão declarativo.

Telas APRENDIDAS (ADR-054, fatia 5 do ADR-052): uma tela que o arquivo não conhece, vista em etapas comprovadas, vira
regra de dado de instalação. Ela entra por `com_aprendidas`, sempre DEPOIS das declaradas (desafio, 2FA, login,
intersticial e carregando vencem sempre), só como `autenticada`, casando por `ids_todos` (todos presentes, sufixo
exato) — e é pulada em tela protegida (sensível, com senha ou com texto de verificação). Segurança continua só no
repositório: só ele declara tipo diferente de `autenticada`, sinal de texto, extração e formulário.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import Awaitable, Callable

import yaml

from .hierarchy import (SUBTIPO_CODIGO, SUBTIPO_CONTA_TRAVADA, ContaTravada, UiElement, UiTree,
                        detectar_trava_generica, normalizar_texto_de_tela)

log = logging.getLogger(__name__)

#: Vocabulário dos motores. Quem trata cada tipo é o núcleo (sessão, executor), não o app.
TIPOS = frozenset({"desafio", "dois_fatores", "intersticial", "login", "carregando", "autenticada"})
#: Tipos de onde nunca se "volta": cada um tem tratamento próprio (pessoa, login, dispensa, espera).
NAO_SE_VOLTA = frozenset({"desafio", "dois_fatores", "login", "intersticial", "carregando"})
DESCONHECIDA = "desconhecida"
#: 31.338: quanto esperar uma tela conhecida depois de reabrir o app de frio (3 leituras, 1,5 s entre elas).
ESPERA_APOS_REABRIR_S = 1.5
LEITURAS_APOS_REABRIR = 3
#: Tipos que só o detector de conta travada decide (ADR-055), e o subtipo de cada um.
TIPOS_DE_TRAVA = {"desafio": SUBTIPO_CONTA_TRAVADA, "dois_fatores": SUBTIPO_CODIGO}

#: De onde veio a regra. A do repositório é a base curada, com teste; a aprendida é dado de instalação (ADR-054).
ORIGEM_REPOSITORIO = "repositorio"
ORIGEM_APRENDIDA = "aprendida"
#: O nome de toda tela aprendida começa assim: nunca colide com uma declarada e se reconhece em qualquer leitura.
PREFIXO_APRENDIDA = "aprendida_"
#: Quantos ids uma regra aprendida exige, todos presentes: um só é largo demais; mais de quatro, frágil.
IDS_TODOS_MIN = 2
IDS_TODOS_MAX = 4
#: Os campos que uma regra aprendida pode ter. Sinal de texto, extração, formulário, "sem elementos" e ids por prefixo
#: são do repositório: é por eles que desafio, 2FA, login e a leitura da conta se declaram.
_CAMPOS_DA_APRENDIDA = frozenset({"tela", "tipo", "autenticada", "ids_todos", "casa", "razao"})


#: O alfabeto de um nome de saída de etapa (o mesmo de `models.SAIDA_NOME_RE`; repetido aqui para a camada de automação
#: não importar o modelo de execução).
_NOME_DE_SAIDA = re.compile(r"[a-z][a-z0-9_]{0,39}")


class ConhecimentoInvalido(ValueError):
    """O arquivo de conhecimento não se sustenta: recusa na carga, antes de classificar a primeira tela."""


#: As versões do formato de `telas.yaml` que este código entende (RA-24, como `capabilities.VERSOES_DE_CONTRATO` no
#: catálogo): um arquivo de versão nova lido por código velho seria entendido pela metade, sem aviso. Subir a versão
#: exige o código que a lê, no mesmo commit.
VERSOES_DE_TELAS = (1,)


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
    #: Todos presentes, por sufixo EXATO (o `ids` casa com qualquer um, por prefixo — largo demais para o aprendido).
    ids_todos: tuple[str, ...] = ()
    origem: str = ORIGEM_REPOSITORIO
    #: Só na aprendida: entra no estado conhecido (a declarada diz isso em `estado_conhecido.telas`).
    casa: bool = False
    #: 29.87: folha de aviso que se fecha SEM ESCOLHER, por um toque no fundo escurecido acima dela:
    #: (sufixo do id da folha, sufixo do id do fundo). `None` = a regra não declara fechamento.
    fechar_fora: tuple[str, str] | None = None
    #: 29.87: rótulos que nada automático toca nesta tela ("OK" de um aviso numa conta real é aceitar; ele pede o dono).
    nunca: tuple[str, ...] = ()

    @property
    def aprendida(self) -> bool:
        return self.origem != ORIGEM_REPOSITORIO


@dataclass(frozen=True, slots=True)
class Extracao:
    ids: tuple[str, ...]
    padrao: re.Pattern[str]
    arroba_solto: bool = False
    #: Item 23.8: só elementos DENTRO de um destes (sufixo exato do id do contêiner). Para o valor que o app mostra
    #: num texto sem id, dentro de um painel com id (a gaveta do Outlook: o e-mail da conta num TextView sem id, e à
    #: esquerda a lista de contas, que não conta).
    dentro_de: tuple[str, ...] = ()
    #: 31.286: a extração é da conta ABERTA, e a página de OUTRA pessoa mostra o mesmo cabeçalho (`action_bar_title` com o
    #: @ dela). Esta tela é de terceiro quando tem um destes ids (sufixo exato) ou um botão clicável com um destes
    #: textos exatos (minúsculos): o botão de seguir/mensagem só existe no perfil alheio. Nela a extração devolve
    #: `None` (indeterminado), nunca o @ do terceiro como se fosse a conta logada.
    exceto_ids: tuple[str, ...] = ()
    exceto_textos: tuple[str, ...] = ()

    def de_terceiro(self, tree: UiTree) -> bool:
        """A tela é a página de outra pessoa (tem o botão de seguir ou de mensagem que o próprio perfil nunca tem)."""
        if not (self.exceto_ids or self.exceto_textos):
            return False
        return any((_sufixo(e.resource_id) in self.exceto_ids if e.resource_id else False)
                   or (e.clickable and (e.text or e.desc or "").strip().lower() in self.exceto_textos)
                   for e in tree.elements)

    def cabe(self, tree: UiTree, e: object) -> bool:
        """O elemento está dentro de um dos contêineres declarados (sempre, quando não há contêiner declarado)."""
        if not self.dentro_de:
            return True
        x1, y1, x2, y2 = e.bounds  # type: ignore[attr-defined]
        return any(_sufixo(c.resource_id) in self.dentro_de and c is not e
                   and c.bounds[0] <= x1 and c.bounds[1] <= y1 and x2 <= c.bounds[2] and y2 <= c.bounds[3]
                   for c in tree.elements)


@dataclass(frozen=True, slots=True)
class RegiaoVisual:
    """Onde o app DECLARA que uma saída de etapa pode ser lida da imagem (item 12.5, ADR-070): a tela, os contêineres
    (id da árvore) que contêm a linha e os nomes de saída que valem ali. É uma afirmação da pessoa que escreve o dado do
    app: diz ONDE se pode ler, não que o conteúdo nunca será sensível (a triagem continua obrigatória)."""

    tela: str
    dentro_de: tuple[str, ...]
    saidas: tuple[str, ...]
    #: 31.328: o recorte é conteúdo de TERCEIROS (a linha de uma caixa de e-mail: remetente, assunto e prévia de quem escreveu
    #: a mensagem), não a tela do app. A frase de verificação humana ("Confirm you're human") nele é o texto do e-mail, não
    #: uma tela de desafio; a triagem do recorte deixa de recusá-la. Código, senha e token seguem recusados.
    conteudo_de_terceiros: bool = False

    def cobre(self, tree: UiTree, ancora: object, saida: str) -> bool:
        """A saída vale nesta região e a âncora está DENTRO de um dos contêineres declarados (mesma contenção de
        `Extracao.cabe`: id do contêiner por sufixo exato — ou o id completo, quando declarado assim — e limites)."""
        if saida not in self.saidas:
            return False
        x1, y1, x2, y2 = ancora.bounds  # type: ignore[attr-defined]
        return any(c is not ancora and self._e_o_conteiner(c.resource_id)
                   and c.bounds[0] <= x1 and c.bounds[1] <= y1 and x2 <= c.bounds[2] and y2 <= c.bounds[3]
                   for c in tree.elements)

    def _e_o_conteiner(self, resource_id: str) -> bool:
        rid = (resource_id or "").lower()
        return any(rid == d if ":id/" in d else _sufixo(rid) == d for d in self.dentro_de)


@dataclass(frozen=True, slots=True)
class DicaAoJuiz:
    """Item 31.46: um fato sobre COMO a tela do app é desenhada, dito ao verificador. É sobre a árvore ("a linha da lista
    não traz texto"), nunca sobre o que aceitar: o juiz segue julgando a pós-condição. `telas` vazio = o app inteiro."""
    texto: str
    telas: tuple[str, ...] = ()


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
    #: Item 12.5: as regiões em que a leitura visual vale (`leitura_visual.regioes`). Vazio = nenhuma saída pode ser lida
    #: da imagem neste app, e a leitura visual recusa com `regiao_nao_declarada`.
    regioes_visuais: tuple[RegiaoVisual, ...] = ()
    #: Item 31.46: o que o juiz precisa saber da árvore deste app (`dicas_ao_juiz`). Vazio = o pedido de antes.
    dicas_ao_juiz: tuple[DicaAoJuiz, ...] = ()

    def dicas_para(self, tela: str | None) -> list[str]:
        """Os textos que valem para a tela reconhecida `tela` (`None` = não reconhecida: só os do app inteiro), na ordem
        do arquivo e sem repetir."""
        return list(dict.fromkeys(d.texto for d in self.dicas_ao_juiz if not d.telas or tela in d.telas))

    def regiao_visual(self, tela: str | None, tree: UiTree, ancora: object, saida: str) -> RegiaoVisual | None:
        """A região declarada que cobre esta âncora para esta saída, na tela reconhecida `tela`; `None` se nenhuma."""
        return next((r for r in self.regioes_visuais if r.tela == tela and r.cobre(tree, ancora, saida)), None)

    def sinais_de(self, locale: str | None) -> dict[str, re.Pattern[str]]:
        """`pt-BR` -> tabela `pt`; idioma não declarado cai no padrão do app."""
        codigo = (locale or "").split("-")[0].split("_")[0].lower()
        return self.sinais.get(codigo, self.sinais[self.idioma_padrao])

    def sinal_em_qualquer_idioma(self, nome: str) -> tuple[re.Pattern[str], ...]:
        """O sinal em TODOS os idiomas que o declaram. É o que vale para desafio e código (ADR-055): o idioma do
        aparelho não diz o idioma da tela de verificação, e escolher uma tabela só por `ro.product.locale` deixava
        "Confirm you’re human" desconhecida num aparelho em português."""
        return tuple(tabela[nome] for tabela in self.sinais.values() if nome in tabela)

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
        if ex.de_terceiro(tree):
            return None
        for e in tree.elements:
            if ((_sufixo(e.resource_id) in ex.ids or (not ex.ids and ex.dentro_de)) and ex.cabe(tree, e)
                    and (m := ex.padrao.match((e.text or "").strip()))):
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
    #: Desafio ou código: o que o detector achou (subtipo e trecho), para quem grava a tentativa e avisa o dono.
    trava: ContaTravada | None = None


def _sufixo(resource_id: str) -> str:
    return resource_id.rsplit("/", 1)[-1].lower()


def _tem_id(tree: UiTree, prefixos: tuple[str, ...]) -> bool:
    return any(_sufixo(e.resource_id).startswith(prefixos) for e in tree.elements)


def _tem_todos(tree: UiTree, ids: tuple[str, ...]) -> bool:
    presentes = {_sufixo(e.resource_id) for e in tree.elements if e.resource_id}
    return all(i in presentes for i in ids)


def sufixos(tree: UiTree, pacote: str | None = None) -> list[str]:
    """Os sufixos de resource-id da tela, em minúsculas e COM repetição (quem filtra item de lista precisa contar),
    só dos elementos do `pacote` quando ele é dado (teclado e barra do sistema ficam de fora). Nunca texto."""
    return [_sufixo(e.resource_id) for e in tree.elements
            if e.resource_id and (pacote is None or e.package == pacote)]


def tela_protegida(tree: UiTree) -> bool:
    """Onde nada aprendido vale e nada se aprende (ADR-054): tela sensível (senha, verificação, declarada pelo parque),
    campo de senha, ou texto de verificação — conta travada ou pedido de código, este MESMO sem campo
    de texto (aqui a dúvida protege: a regra aprendida só deixa de valer)."""
    if tree.sensitive or tree.conta_travada is not None or any(e.password for e in tree.elements):
        return True
    normalizado = "\n".join(normalizar_texto_de_tela(f"{e.text} {e.desc}") for e in tree.elements if e.text or e.desc)
    return detectar_trava_generica(normalizado, tem_onde_digitar=True) is not None


def _texto_da_tela(tree: UiTree) -> str:
    return "\n".join(f"{e.text} {e.desc}".strip() for e in tree.elements if e.text or e.desc)


def _trava_declarada(k: ConhecimentoDeTelas, tipo: str, normalizado: str, bruto: str) -> ContaTravada | None:
    """A primeira regra do app daquele tipo cujo sinal casa, em QUALQUER idioma declarado. Casa contra o texto
    normalizado (apóstrofo simples, sem acento — os sinais trazem a forma sem acento em cada classe `[ée]`) e, por
    garantia, contra o texto como veio."""
    for r in k.telas:
        if r.tipo != tipo or r.sinal is None:
            continue
        for padrao in k.sinal_em_qualquer_idioma(r.sinal):
            if m := (padrao.search(normalizado) or padrao.search(bruto)):
                return ContaTravada(TIPOS_DE_TRAVA[tipo], m.group(0)[:80], origem=r.tela)
    return None


def detectar_conta_travada(tree: UiTree, k: ConhecimentoDeTelas | None = None, *,
                           codigo_declarado_exige_campo: bool = True) -> ContaTravada | None:
    """O detector ÚNICO de conta travada (ADR-055). O executor (depois de cada observação, antes da reabertura por ANR,
    da receita e do ator), a leitura da conta e a dispensa do motor de sessão perguntam a ele — e quem ouve "sim" sai
    SEM tocar, teclar nem reabrir.

    - Conta travada ("Confirm you’re human", CAPTCHA, atividade suspeita) vem primeiro e NÃO exige campo de texto: a
      tela real só tem botões ("Continue", "Get support"), e era exatamente por não ter campo que ela passava.
    - Código de login/2FA vem depois. O sinal GENÉRICO exige onde digitar sempre: ele roda em qualquer tela, e a
      linha "Autenticação de dois fatores" do menu de configurações não é pedido de código. O DECLARADO pelo app
      (`two_factor` do `telas.yaml`) exige o campo só no meio da execução (`codigo_declarado_exige_campo`, o padrão),
      pelo mesmo menu; o motor de sessão (`classificar`) desliga a exigência — ver lá o porquê.
    - Sinais genéricos (`hierarchy`, qualquer app) e os declarados pelo app (`telas.yaml`, tipos `desafio` e
      `dois_fatores`), estes na UNIÃO dos idiomas.

    `k=None` (app sem conhecimento declarado) usa só os genéricos."""
    bruto = _texto_da_tela(tree)
    normalizado = "\n".join(normalizar_texto_de_tela(f"{e.text} {e.desc}") for e in tree.elements if e.text or e.desc)
    tem_onde_digitar = any(e.editable for e in tree.elements)
    generica = tree.conta_travada or detectar_trava_generica(normalizado, tem_onde_digitar=tem_onde_digitar)
    if k is not None and (declarada := _trava_declarada(k, "desafio", normalizado, bruto)) is not None:
        return declarada
    if generica is not None and generica.subtipo == SUBTIPO_CONTA_TRAVADA:
        return generica
    if (k is not None and (tem_onde_digitar or not codigo_declarado_exige_campo)
            and (declarada := _trava_declarada(k, "dois_fatores", normalizado, bruto))):
        return declarada
    return generica


def classificar(k: ConhecimentoDeTelas, tree: UiTree, *, package: str | None, locale: str | None = None,
                formulario: Callable[[UiTree], object | None] | None = None) -> TelaReconhecida:
    """A primeira regra declarada que casar vence. Outro app na frente não é classificado.

    Desafio e código não passam pelas regras: quem decide é `detectar_conta_travada`, antes de qualquer outra — o
    mesmo veredito em todo lugar que olha a tela, e tela de verificação nunca cai em "desconhecida" (de onde o motor
    de sessão voltava e dispensava).

    Aqui a regra DECLARADA do código casa só pelo texto, sem exigir campo `EditText` — como antes do detector. O
    Instagram desenha campos com widgets que a heurística de classe não reconhece (`app_declarado/formulario.py`, o
    `sessao.yaml` do app), e na revisão do pacote a tela de código com o campo num `android.view.View` virou
    "desconhecida": o motor voltou dela (o "voltar" já é o toque proibido), caiu no login e ENVIOU A SENHA DE NOVO
    (decisões b e e do ADR-055). O menu de configurações, que motivou a exigência, é do meio da execução — lá ela
    continua. Se o app reabrir justo nele, o motor para e chama uma pessoa sem bloquear (código não bloqueia): um
    clique de quem olha, contra um segundo envio de senha."""
    if package and package != k.app:
        return TelaReconhecida(DESCONHECIDA, DESCONHECIDA, f"outro app em primeiro plano ({package})", outro_app=True)
    if (trava := detectar_conta_travada(tree, k, codigo_declarado_exige_campo=False)) is not None:
        regra = k.regra(trava.origem) or next((r for r in k.telas if r.tipo == trava.tipo), None)
        return TelaReconhecida(regra.tela if regra else trava.tipo, trava.tipo,
                               regra.razao if regra else f"a tela pede verificação ({trava.subtipo})",
                               [trava.trecho], trava=trava)
    texto = _texto_da_tela(tree)
    sinais = k.sinais_de(locale)
    protegida: bool | None = None                  # calculada só se houver regra aprendida a considerar
    for r in k.telas:
        if r.tipo in TIPOS_DE_TRAVA:
            continue
        if r.aprendida:
            if protegida is None:
                protegida = tela_protegida(tree)
            if protegida:
                continue
        if r.ids_todos and not _tem_todos(tree, r.ids_todos):
            continue
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
        return TelaReconhecida(r.tela, r.tipo, r.razao, [], form)
    return TelaReconhecida(DESCONHECIDA, DESCONHECIDA, "nenhum sinal conhecido na tela")


async def voltar_ao_estado_conhecido(
        k: ConhecimentoDeTelas, *,
        observar: Callable[[], Awaitable[tuple[UiTree, str | None]]],
        voltar: Callable[[], Awaitable[None]],
        reabrir: Callable[[], Awaitable[None]],
        reconhecer: Callable[[UiTree, str | None], TelaReconhecida],
        nao_reabrir_sobre: Callable[[UiTree], bool] | None = None,
        espera_apos_reabrir_s: float = ESPERA_APOS_REABRIR_S,
        leituras_apos_reabrir: int = LEITURAS_APOS_REABRIR,
) -> tuple[UiTree, str | None, TelaReconhecida, list[str]]:
    """Leva o app ao estado conhecido declarado, só com "voltar" do Android e, no máximo uma vez, reabrir o app.

    Para numa tela de casa ou numa tela com tratamento próprio (login, desafio, 2FA, intersticial, carregando): dela
    ninguém "volta" por conta própria. Devolve a última observação e os passos dados, para quem chama registrar.
    Nenhum passo aqui tem efeito externo: voltar e abrir o app não publicam, não enviam, não seguem.

    `nao_reabrir_sobre` (29.92): com outro app na frente e esta pergunta dizendo sim, nada é reaberto por cima e a tela
    volta como está (`outro_app`), para quem chama decidir. O motor de sessão a usa no aparelho com conta real.

    31.338: depois de reabrir, o app de frio mostra uma tela de passagem (abertura, animação) que o motor não conhece, e um
    "voltar" nela TIRA o app da frente (prova real do P-043 no Outlook, 11/10: [reabrir, voltar] terminou na tela inicial
    do Android e a exploração não ancorou). Por isso, depois de cada reabrir, a tela é relida (até
    `leituras_apos_reabrir` vezes, `espera_apos_reabrir_s` entre elas) enquanto o app está na frente mas a tela segue
    desconhecida; só então se decide entre estado conhecido, "voltar" ou desistir.
    """
    passos: list[str] = []
    tree, pacote = await observar()
    atual = reconhecer(tree, pacote)
    reaberto = False
    for _ in range(k.estado_conhecido.voltar_max + 1):
        if k.em_casa(atual.tela) or atual.tipo in NAO_SE_VOLTA:
            return tree, pacote, atual, passos
        if atual.outro_app:
            if reaberto or not k.estado_conhecido.reabrir or (nao_reabrir_sobre is not None and nao_reabrir_sobre(tree)):
                return tree, pacote, atual, passos
            await reabrir()
            reaberto = True
            passos.append("reabrir")
            tree, pacote, atual = await _esperar_a_tela_conhecida(observar, reconhecer, espera_apos_reabrir_s,
                                                                  leituras_apos_reabrir)
            continue
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
        tree, pacote, atual = await _esperar_a_tela_conhecida(observar, reconhecer, espera_apos_reabrir_s,
                                                              leituras_apos_reabrir)
    return tree, pacote, atual, passos


async def _esperar_a_tela_conhecida(
        observar: Callable[[], Awaitable[tuple[UiTree, str | None]]],
        reconhecer: Callable[[UiTree, str | None], TelaReconhecida],
        espera_s: float, leituras: int) -> tuple[UiTree, str | None, TelaReconhecida]:
    """31.338: observa; se o app está na frente mas a tela é desconhecida (abertura de frio), espera e relê, até `leituras`
    vezes. Só leitura: nenhum toque, nenhum voltar."""
    tree, pacote = await observar()
    atual = reconhecer(tree, pacote)
    for _ in range(max(0, leituras)):
        if atual.tela != DESCONHECIDA or atual.outro_app:
            break
        await asyncio.sleep(max(0.0, espera_s))
        tree, pacote = await observar()
        atual = reconhecer(tree, pacote)
    return tree, pacote, atual


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
    versao = raiz.get("versao", 1)
    # `bool` é `int` em Python, e `1.0 in (1,)` é verdadeiro: os dois passariam calados sem a conferência do tipo.
    if isinstance(versao, bool) or not isinstance(versao, int) or versao not in VERSOES_DE_TELAS:
        raise ConhecimentoInvalido(f"`versao` {versao!r} não é entendida (aceitas: "
                                   f"{', '.join(map(str, VERSOES_DE_TELAS))}); subir a versão exige o código que a lê")
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
                                   arroba_solto=bool(ex.get("arroba_solto", False)),
                                   dentro_de=_textos(ex.get("dentro_de"), f"extracoes.{nome}.dentro_de"),
                                   exceto_ids=_textos(ex.get("exceto_ids"), f"extracoes.{nome}.exceto_ids")
                                   if "exceto_ids" in ex else (),
                                   exceto_textos=_textos(ex.get("exceto_textos"), f"extracoes.{nome}.exceto_textos")
                                   if "exceto_textos" in ex else ())
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
        fechar_fora, nunca = _fechamento(r, onde, tipo)
        regras.append(RegraDeTela(tela=nome_da_tela, tipo=tipo, razao=str(r.get("razao") or nome_da_tela),
                                  autenticada=bool(r.get("autenticada", False)), sinal=sinal,
                                  formulario_de_senha=bool(r.get("formulario_de_senha", False)),
                                  sem_elementos=bool(r.get("sem_elementos", False)),
                                  ids=_textos(r.get("ids"), f"{onde}.ids"), extracao=extracao,
                                  ids_todos=_textos(r.get("ids_todos"), f"{onde}.ids_todos"),
                                  fechar_fora=fechar_fora, nunca=nunca))
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
    regioes = _regioes_visuais(raiz.get("leitura_visual"), nomes)
    dicas = _dicas_ao_juiz(raiz.get("dicas_ao_juiz"), nomes)
    return ConhecimentoDeTelas(app=app, versao=versao, idioma_padrao=idioma_padrao,
                               sinais=sinais, telas=tuple(regras), extracoes=extracoes,
                               estado_conhecido=EstadoConhecido(telas=casa, voltar_max=voltar_max,
                                                                reabrir=bool(ec.get("reabrir", True))),
                               regioes_visuais=regioes, dicas_ao_juiz=dicas)


def _fechamento(r: dict[str, object], onde: str, tipo: str) -> tuple[tuple[str, str] | None, tuple[str, ...]]:
    """29.87: `fechar: {toque_fora: {folha, fundo}}` e `nunca: [...]`. Só numa tela intermediária, e um vem com o
    outro: o fechamento sem escolher existe para que os rótulos de `nunca` fiquem sem toque."""
    bruto, nunca = r.get("fechar"), _textos(r.get("nunca"), f"{onde}.nunca")
    if bruto is None:
        if nunca:
            raise ConhecimentoInvalido(f"{onde}: `nunca` exige `fechar` (como a tela se fecha sem tocar nesses rótulos)")
        return None, ()
    if tipo != "intersticial":
        raise ConhecimentoInvalido(f"{onde}: `fechar` só vale em tela `intersticial` (é {tipo!r})")
    fechar = _mapa(bruto, f"{onde}.fechar")
    if set(fechar) != {"toque_fora"}:
        raise ConhecimentoInvalido(f"{onde}.fechar: o único fechamento entendido é `toque_fora`")
    fora = _mapa(fechar["toque_fora"], f"{onde}.fechar.toque_fora")
    folha, fundo = str(fora.get("folha") or "").strip().lower(), str(fora.get("fundo") or "").strip().lower()
    if not folha or not fundo or set(fora) != {"folha", "fundo"}:
        raise ConhecimentoInvalido(f"{onde}.fechar.toque_fora: declare só `folha` e `fundo` (sufixos de id)")
    if not nunca:
        raise ConhecimentoInvalido(f"{onde}: `fechar` exige `nunca` (os rótulos que ninguém toca nesta folha)")
    return (folha, fundo), nunca


#: 29.87: a folha precisa deixar ao menos isto (px) de fundo acima dela para o toque cair FORA dela.
FUNDO_MINIMO_PX = 24


def _rotulo_normal(e: UiElement) -> str:
    return " ".join(f"{e.text} {e.desc}".split()).casefold()


def proibido_na_tela(k: ConhecimentoDeTelas, tree: UiTree, elemento: UiElement) -> bool:
    """29.87 (D1 da revisão): o `elemento` leva um rótulo do `nunca` de uma regra que reconhece esta tela? Para quem
    toca sem IA por rótulo genérico (a dispensa da sessão) não tocar no que o app declarou intocável ("OK" de um aviso
    numa conta real). 29.90 (D1b): em TODOS os idiomas declarados, como o desafio (ADR-055): quem chama não sabe o
    idioma da tela, e "nunca tocar" não pode depender de acertar a tabela."""
    rotulo = _rotulo_normal(elemento)
    for idioma in k.sinais:
        regra = k.regra(classificar(k, tree, package=None, locale=idioma).tela)
        if regra is not None and rotulo in {n.casefold() for n in regra.nunca}:
            return True
    return False


def regra_de_fechar(k: ConhecimentoDeTelas, tree: UiTree, *, package: str | None) -> RegraDeTela | None:
    """29.90 (D1c da revisão): a regra com `fechar` que reconhece esta tela em QUALQUER idioma declarado, ou `None`.
    Quem fecha a folha não sabe o idioma da tela; só pela tabela padrão, a folha em português iria à receita ou ao
    ator, onde o `nunca` não protege."""
    for idioma in k.sinais:
        regra = k.regra(classificar(k, tree, package=package, locale=idioma).tela)
        if regra is not None and regra.fechar_fora is not None:
            return regra
    return None


def toque_fora_da_folha(regra: RegraDeTela, tree: UiTree) -> tuple[int, int] | None:
    """29.87: o ponto (do aparelho) que fecha a folha da `regra` sem escolher nada: no fundo escurecido, no meio da
    faixa que sobra ACIMA da folha (medido no android-13 em 05/10: a folha "Sharing posts" começa em y=260 e o fundo
    em y=48; o toque em (360, 250) fechou sem tocar em "OK"). `None` quando não há como: folha ou fundo fora da
    árvore, fundo de menos acima da folha, ou o ponto cairia num elemento com rótulo de `nunca`."""
    if regra.fechar_fora is None:
        return None
    id_da_folha, id_do_fundo = regra.fechar_fora
    folha = next((e for e in tree.elements if _sufixo(e.resource_id) == id_da_folha), None)
    fundo = next((e for e in tree.elements if _sufixo(e.resource_id) == id_do_fundo), None)
    if folha is None or fundo is None or folha.bounds[1] - fundo.bounds[1] < FUNDO_MINIMO_PX:
        return None
    x = (folha.bounds[0] + folha.bounds[2]) // 2
    y = (fundo.bounds[1] + folha.bounds[1]) // 2
    if not (fundo.bounds[0] <= x <= fundo.bounds[2]):
        return None
    proibidos = {n.casefold() for n in regra.nunca}
    if any(e.bounds[0] <= x <= e.bounds[2] and e.bounds[1] <= y <= e.bounds[3] and _rotulo_normal(e) in proibidos
           for e in tree.elements):
        return None
    return x, y


#: Uma dica é uma frase curta por tela: passar disto é o arquivo virando manual dentro do prompt do juiz.
_DICA_TEXTO_MAX = 600


def _dicas_ao_juiz(bruto: object, telas: set[str]) -> tuple[DicaAoJuiz, ...]:
    """`dicas_ao_juiz` (item 31.46). Recusa na carga: item que não é mapa, campo desconhecido, `texto` vazio ou maior que
    `_DICA_TEXTO_MAX`, `telas` com nome que o arquivo não declara. Sem o campo: nenhuma dica, o pedido de antes."""
    if bruto is None:
        return ()
    out: list[DicaAoJuiz] = []
    for i, item in enumerate(_lista(bruto, "dicas_ao_juiz")):
        onde = f"dicas_ao_juiz[{i}]"
        d = _mapa(item, onde)
        if estranhos := sorted(set(d) - {"telas", "texto"}):
            raise ConhecimentoInvalido(f"{onde}: campo desconhecido {', '.join(estranhos)}")
        texto = " ".join(str(d.get("texto") or "").split())
        if not texto or len(texto) > _DICA_TEXTO_MAX:
            raise ConhecimentoInvalido(f"{onde}: `texto` precisa ter de 1 a {_DICA_TEXTO_MAX} caracteres")
        alvo = tuple(str(t) for t in _lista(d.get("telas"), f"{onde}.telas"))
        if desconhecidas := sorted(t for t in alvo if t not in telas):
            raise ConhecimentoInvalido(f"{onde}: a tela {desconhecidas[0]!r} não está declarada em `telas`")
        out.append(DicaAoJuiz(texto=texto, telas=alvo))
    return tuple(out)


def _regioes_visuais(bruto: object, telas: set[str]) -> tuple[RegiaoVisual, ...]:
    """`leitura_visual.regioes` (item 12.5). Recusa na carga: `dentro_de` vazio (a região sem contêiner cobriria a tela
    inteira), `saidas` vazia ou com nome fora do alfabeto de saída, e tela que o arquivo não declara. A conferência
    contra o `catalogo.yaml` (a saída existir em alguma ação) é de `app_declarado/pacote.py`, que tem os dois."""
    if bruto is None:
        return ()
    bloco = _mapa(bruto, "leitura_visual")
    if estranhos := sorted(set(bloco) - {"regioes"}):
        raise ConhecimentoInvalido(f"leitura_visual: campo desconhecido {', '.join(estranhos)}")
    out: list[RegiaoVisual] = []
    for i, item in enumerate(_lista(bloco.get("regioes"), "leitura_visual.regioes")):
        onde = f"leitura_visual.regioes[{i}]"
        r = _mapa(item, onde)
        if estranhos := sorted(set(r) - {"tela", "dentro_de", "saidas", "conteudo_de_terceiros"}):
            raise ConhecimentoInvalido(f"{onde}: campo desconhecido {', '.join(estranhos)}")
        tela = str(r.get("tela") or "")
        if tela not in telas:
            raise ConhecimentoInvalido(f"{onde}: a tela {tela!r} não está declarada em `telas`")
        dentro = _textos(r.get("dentro_de"), f"{onde}.dentro_de")
        if not dentro or any(not d.strip() for d in dentro):
            raise ConhecimentoInvalido(f"{onde}: `dentro_de` não pode ser vazio (a região precisa de um contêiner)")
        saidas = tuple(str(x) for x in _lista(r.get("saidas"), f"{onde}.saidas"))
        if not saidas or any(not _NOME_DE_SAIDA.fullmatch(n) for n in saidas):
            raise ConhecimentoInvalido(f"{onde}: `saidas` precisa listar nomes de saída (a-z, 0-9 e _)")
        # Contêiner por sufixo, como `extracoes.dentro_de`; o id completo (`pacote:id/nome`) também vale e fica inteiro.
        terceiros = r.get("conteudo_de_terceiros", False)
        if not isinstance(terceiros, bool):
            raise ConhecimentoInvalido(f"{onde}: `conteudo_de_terceiros` é verdadeiro ou falso")
        out.append(RegiaoVisual(tela=tela, dentro_de=tuple(dict.fromkeys(dentro)), saidas=saidas,
                                conteudo_de_terceiros=terceiros))
    return tuple(out)


def carregar(caminho: Path) -> ConhecimentoDeTelas:
    try:
        bruto = yaml.safe_load(caminho.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:      # 29.87 (E2): YAML quebrado é conhecimento inválido, a mesma exceção para quem chama
        raise ConhecimentoInvalido(f"{caminho.name}: YAML ilegível ({exc})") from None
    return de_dados(bruto or {})


# ---------------------------------------------------------------------------------------------------- aprendidas
def regra_aprendida(dados: object) -> RegraDeTela:
    """A regra de uma tela aprendida (o `content` do item do livro), validada. Recusa tudo o que só o repositório
    declara: tipo diferente de `autenticada`, sinal de texto, extração, formulário de senha, "sem elementos" e ids por
    prefixo — e nome fora de `aprendida_*`, e menos de 2 ou mais de 4 ids exigidos."""
    r = _mapa(dados, "regra aprendida")
    if estranhos := sorted(set(r) - _CAMPOS_DA_APRENDIDA):
        raise ConhecimentoInvalido(f"regra aprendida: {', '.join(estranhos)} não se aprende (é do repositório)")
    if r.get("tipo") != "autenticada" or r.get("autenticada") is not True:
        raise ConhecimentoInvalido("regra aprendida: só `autenticada` se aprende; os outros tipos são do repositório")
    nome = str(r.get("tela") or "")
    if not nome.startswith(PREFIXO_APRENDIDA) or len(nome) == len(PREFIXO_APRENDIDA):
        raise ConhecimentoInvalido(f"regra aprendida: o nome {nome!r} precisa começar por {PREFIXO_APRENDIDA!r}")
    ids = tuple(dict.fromkeys(_textos(r.get("ids_todos"), "regra aprendida.ids_todos")))
    if not IDS_TODOS_MIN <= len(ids) <= IDS_TODOS_MAX or not all(ids):
        raise ConhecimentoInvalido(f"regra aprendida: exige de {IDS_TODOS_MIN} a {IDS_TODOS_MAX} ids em `ids_todos`")
    casa = r.get("casa", False)
    if not isinstance(casa, bool):
        raise ConhecimentoInvalido("regra aprendida: `casa` é true ou false")
    return RegraDeTela(tela=nome, tipo="autenticada", razao=str(r.get("razao") or nome), autenticada=True,
                       ids_todos=ids, origem=ORIGEM_APRENDIDA, casa=casa)


def _vale_como_aprendida(r: RegraDeTela) -> bool:
    return (r.aprendida and r.tipo == "autenticada" and r.autenticada and r.tela.startswith(PREFIXO_APRENDIDA)
            and IDS_TODOS_MIN <= len(r.ids_todos) <= IDS_TODOS_MAX and r.sinal is None and r.extracao is None
            and not r.formulario_de_senha and not r.sem_elementos and not r.ids)


def com_aprendidas(k: ConhecimentoDeTelas, regras: Iterable[RegraDeTela]) -> ConhecimentoDeTelas:
    """O conhecimento UNIDO: as declaradas, depois as aprendidas (desafio, 2FA, login, intersticial e carregando
    vencem sempre), e as aprendidas de casa acrescentadas ao estado conhecido — é o que faz `autenticada()` e
    `em_casa()` valerem para elas na sessão. Função pura: o conhecimento declarado não muda.

    Nunca derruba: regra que não vale como aprendida (tipo, campo do repositório, nome) ou que repete o nome de uma
    tela já declarada — a absorvida pelo YAML, por exemplo — fica de fora."""
    nomes = {r.tela for r in k.telas}
    novas: list[RegraDeTela] = []
    for r in regras:
        if _vale_como_aprendida(r) and r.tela not in nomes:
            nomes.add(r.tela)
            novas.append(r)
    if not novas:
        return k
    casa = tuple(dict.fromkeys((*k.estado_conhecido.telas, *(r.tela for r in novas if r.casa))))
    return replace(k, telas=(*k.telas, *novas), estado_conhecido=replace(k.estado_conhecido, telas=casa))


def fragmento_de_aprendidas(arquivo: Path, regras: Sequence[RegraDeTela], *, cabecalho: Sequence[str] = (),
                            notas: Mapping[str, Sequence[str]] | None = None) -> str:
    """A ponte para o repositório (ADR-054): o fragmento YAML das regras aprendidas, no formato deste arquivo, com
    comentários (o `cabecalho` e as `notas` de proveniência de cada tela). Sai CONFERIDO: acrescentado ao `arquivo`
    do app (as regras ao fim de `telas:`, as de casa em `estado_conhecido.telas`), ele carrega por `de_dados` e cada
    regra volta igual — ids, tipo e casa. Recusa (`ConhecimentoInvalido`) o que não volta."""
    novas = [r for r in regras if _vale_como_aprendida(r)]
    if not novas:
        raise ConhecimentoInvalido("nenhuma regra aprendida válida para exportar")
    saida = [*(f"# {linha}" for linha in cabecalho), "telas:"]
    for r in novas:
        saida.extend(f"  # {linha}" for linha in (notas or {}).get(r.tela, ()))
        corpo = yaml.safe_dump([{"tela": r.tela, "tipo": r.tipo, "autenticada": True, "ids_todos": list(r.ids_todos),
                                 "razao": r.razao}], allow_unicode=True, sort_keys=False, default_flow_style=None,
                               width=110)
        saida.extend(f"  {linha}" for linha in corpo.splitlines())
    casas = [r.tela for r in novas if r.casa]
    if casas:
        saida += ["# Telas de casa (a aba de perfil declarada estava em toda observação):", "estado_conhecido:",
                  "  telas: " + yaml.safe_dump(casas, default_flow_style=True, width=110).strip()]
    texto = "\n".join(saida) + "\n"
    _conferir_fragmento(yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {}, texto, novas)
    return texto


def _conferir_fragmento(base: object, texto: str, regras: Sequence[RegraDeTela]) -> None:
    fragmento = yaml.safe_load(texto)
    if not isinstance(base, dict) or not isinstance(fragmento, dict):
        raise ConhecimentoInvalido("o arquivo do app e o fragmento precisam ser mapas YAML")
    unido = copy.deepcopy(base)
    unido["telas"] = [*(base.get("telas") or []), *(fragmento.get("telas") or [])]
    estado = dict(base.get("estado_conhecido") or {})
    estado["telas"] = [*(estado.get("telas") or []), *((fragmento.get("estado_conhecido") or {}).get("telas") or [])]
    unido["estado_conhecido"] = estado
    k = de_dados(unido)
    for r in regras:
        lida = k.regra(r.tela)
        if (lida is None or lida.ids_todos != r.ids_todos or lida.tipo != r.tipo or not lida.autenticada
                or k.em_casa(r.tela) != r.casa):
            raise ConhecimentoInvalido(f"a tela {r.tela} não volta igual pelo carregador")


@lru_cache(maxsize=None)
def declaram_leitura_visual(base: Path) -> list[str]:
    """Os pacotes (pastas de `app/conhecimento/apps/`) cujo `telas.yaml` declara `leitura_visual.regioes` (item 12.5): são os
    apps de que o recorte de uma linha de tela pode sair para o leitor. Para o aviso de privacidade de `/api/ai`. Pasta cujo
    arquivo não carrega fica de fora (o erro de carga é de quem descobre os apps, não do aviso)."""
    if not base.is_dir():
        return []
    out: list[str] = []
    for pasta in sorted(p for p in base.iterdir() if p.is_dir()):
        try:
            k = da_pasta(pasta)
        except ConhecimentoInvalido:
            continue
        if k is not None and k.regioes_visuais:
            out.append(pasta.name)
    return out


def da_pasta(pasta: Path) -> ConhecimentoDeTelas | None:
    """O `telas.yaml` da pasta de um app, carregado uma vez por processo; `None` quando o app não declara telas (o
    detector de conta travada fica com os sinais genéricos). Arquivo inválido levanta `ConhecimentoInvalido`."""
    caminho = pasta / "telas.yaml"
    return carregar(caminho) if caminho.is_file() else None


@lru_cache(maxsize=32)
def _da_pasta_com_data(caminho: str, mtime_ns: int) -> ConhecimentoDeTelas:
    return carregar(Path(caminho))


@lru_cache(maxsize=32)
def _da_pasta_ou_aviso(caminho: str, mtime_ns: int) -> ConhecimentoDeTelas | None:
    # O inválido também fica no cache: o aviso sai uma vez por modificação do arquivo, não a cada volta (29.90, L2).
    # Só o conteúdo inválido: um `OSError` passageiro sobe sem entrar no cache (a próxima volta lê de novo).
    try:
        return carregar(Path(caminho))
    except (ConhecimentoInvalido, UnicodeDecodeError) as exc:
        log.warning("conhecimento de telas inválido em %s; segue sem ele até o arquivo mudar: %s", caminho, exc)
        return None


def da_pasta_por_data(pasta: Path) -> ConhecimentoDeTelas | None:
    """`da_pasta` lido uma vez por modificação do arquivo: para quem consulta a cada volta do laço (29.87, E2 da
    revisão). `None` sem `telas.yaml` ou com ele inválido; o inválido é avisado no log uma vez por modificação."""
    caminho = pasta / "telas.yaml"
    try:
        if not caminho.is_file():
            return None
        return _da_pasta_ou_aviso(str(caminho), caminho.stat().st_mtime_ns)
    except OSError as exc:                          # passageiro (o arquivo sumiu, travado): fora do cache
        log.warning("conhecimento de telas ilegível em %s: %s", caminho, exc)
        return None


def dicas_da_tela(pasta: Path, tree: UiTree, *, package: str | None) -> list[str]:
    """Item 31.46: as dicas do `telas.yaml` do app que está na frente, para a tela que a árvore mostra; é o que o juiz
    recebe a mais. Lista vazia (o pedido de antes) quando o app não declara dica, não é o da frente ou o arquivo não
    carrega: uma dica a menos nunca derruba a verificação. Lê o arquivo uma vez por modificação, porque roda a cada
    julgamento."""
    caminho = pasta / "telas.yaml"
    try:
        if (not package or not package.replace(".", "").replace("_", "").isalnum() or pasta.name != package
                or not caminho.is_file()):
            return []
        k = _da_pasta_com_data(str(caminho), caminho.stat().st_mtime_ns)
        if not k.dicas_ao_juiz:
            return []
        return k.dicas_para(classificar(k, tree, package=package).tela)
    except (ConhecimentoInvalido, OSError, yaml.YAMLError):
        return []
