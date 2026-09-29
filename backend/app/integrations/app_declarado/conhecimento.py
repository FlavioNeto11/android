"""Conhecimento de SESSÃO de um app, como dado, e o carregador que o valida (ADR-052, fatia 3).

Um app com conta gerenciada declara em `app/conhecimento/apps/<pacote>/sessao.yaml` tudo que o login e a conferência
da conta precisam saber DELE: o rótulo nas mensagens, os ajustes padrão (tetos, cooldown, prazos), que sinais
identificam o botão de entrar e o "agora não", o que dispensa uma tela benigna, como achar a aba de perfil e de que
tela ler a conta, a tabela de desfechos depois do envio e os textos de ajuda para a pessoa. Os sinais, as telas e as
extrações citados vêm do `telas.yaml` do MESMO pacote (fatia 1) — o carregador confere cada referência.

Arquivo errado falha na CARGA, com o caminho do campo: campo desconhecido (erro de digitação que seria ignorado em
silêncio), sinal que não existe em algum idioma (o login leria a tabela daquele idioma e quebraria no meio do envio),
tela ou tipo inexistente, valor do tipo errado, texto com lacuna que o motor não preenche.

Nada aqui conhece app nenhum. A sobrescrita por instalação (`config.yaml`) não é lida aqui: quem a entrega é
`Config.ajustes_de_sessao`, e o motor (`sessao.SessaoDeclarada`) aplica por cima destes padrões a cada uso.

Item 23.6 (ADR-057, decisão 3), tudo opcional e sem mudar quem não declara: `formulario.etapa_do_usuario` (login em
etapas: o identificador numa tela com "avançar", a senha na seguinte), `conta.acesso` no lugar de `conta.aba` (a conta
aberta por um elemento fora da barra inferior, lida com um valor só) com `conta.ler_ao_entrar`, e `navegador` (a
Custom Tab do login: os pacotes de navegador, a barra de endereço de cada um e os sites de login do app).

Telas APRENDIDAS (ADR-054, fatia 5): o conhecimento da instalação entra por um fornecedor tipado
(`definir_regras_aprendidas`), que o aprendizado liga na composição — este pacote não importa `app.modules`. O padrão
não fornece nenhuma, e a sessão usa o conhecimento UNIDO (`com_as_aprendidas`) a cada chamada. O que o fornecedor
entrega fica guardado por pacote até a próxima invalidação (a cada publicação ou desligamento) ou por
`CACHE_DAS_APRENDIDAS_S`, o que vier antes: o desligamento feito por outro caminho vale em segundos, sem uma leitura
do banco a cada tela.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from functools import lru_cache
from pathlib import Path
from string import Formatter

import yaml

from ...automation import conhecimento_de_telas as telas_
from ...automation.conhecimento_de_telas import ConhecimentoDeTelas, ConhecimentoInvalido, RegraDeTela, TelaReconhecida
from ...automation.hierarchy import UiElement, UiTree
from ...planning.capabilities import CONHECIMENTO_DE_APPS
from . import formulario as geometria
from .formulario import FormularioDoUsuario, LoginForm

#: Onde mora o conhecimento de cada app: uma pasta por pacote Android, com `telas.yaml` e `sessao.yaml`. É a MESMA raiz
#: do catálogo (`planning/capabilities.CONHECIMENTO_DE_APPS`) e da descoberta (`pacote.py`): uma só, para não divergir.
PASTA_DOS_APPS = CONHECIMENTO_DE_APPS

#: Desfechos que uma regra de "depois do envio" pode declarar. `conferir_conta` lê a conta na tela e decide entre
#: sessão pronta, conta errada e incerto (motor). De propósito NÃO há `retryable` nem `session_ready`: depois que a
#: senha foi enviada, nada autoriza repetir o envio, e sucesso só com a conta lida na tela.
CONFERIR_CONTA = "conferir_conta"
DESFECHOS_DEPOIS_DO_ENVIO = frozenset({"invalid_credential", "auth_challenge", "uncertain", CONFERIR_CONTA})
#: A etapa gravada na tentativa quando a regra não nomeia outra.
ETAPA_PADRAO = "classified"
#: Os textos que o app declara e as lacunas que cada um pode ter (o motor só preenche estas).
LACUNAS_DOS_TEXTOS: dict[str, frozenset[str]] = {
    "desafio": frozenset(), "desafio_pendente": frozenset(), "senha_recusada": frozenset(),
    "credencial_recusada": frozenset({"usuario"}),
}


class SessaoInvalida(ConhecimentoInvalido):
    """O `sessao.yaml` não se sustenta: recusa na carga, antes da primeira sessão."""


@dataclass(frozen=True, slots=True)
class Ajustes:
    """Tetos e prazos do login. Os nomes são os da configuração por instalação, que pode sobrescrever cada um."""

    max_auth_attempts: int       # teto de falhas ANTES do envio (a senha não saiu) antes de exigir o intervalo
    auth_cooldown_s: int         # intervalo depois do teto
    max_logins_per_day: int      # envios de senha por conta em 24 h, com sucesso ou não (ADR-055)
    open_timeout_s: float        # teto para o app chegar ao primeiro plano depois de aberto
    settle_s: float              # espera depois de o app aparecer (e de cada toque), antes de classificar
    submit_wait_s: float         # quanto observar depois do toque em entrar (só observa; nunca reenvia)
    verify_timeout_s: float      # prazo de cada leitura da tela

    def com(self, sobrescritos: Mapping[str, int | float]) -> Ajustes:
        """Estes padrões com o que a instalação sobrescreveu. Chave que não é ajuste de sessão é ignorada."""
        campos = {k: v for k, v in sobrescritos.items() if k in _CAMPOS_INTEIROS or k in _CAMPOS_REAIS}
        return replace(self, **campos) if campos else self


_CAMPOS_INTEIROS = frozenset({"max_auth_attempts", "auth_cooldown_s", "max_logins_per_day"})
_CAMPOS_REAIS = frozenset({"open_timeout_s", "settle_s", "submit_wait_s", "verify_timeout_s"})


@dataclass(frozen=True, slots=True)
class RecusaDoIdentificador:
    """O que a tela diz quando recusa o IDENTIFICADOR na etapa do usuário ("essa conta não existe"): a senha ainda
    não saiu, então não é senha recusada — é o login que para até uma pessoa conferir o identificador."""

    sinal: str
    detalhe: str


@dataclass(frozen=True, slots=True)
class EtapaDoUsuario:
    """Login em etapas (item 23.6): o identificador numa tela, a senha na seguinte."""

    tela: str                    # a tela do `telas.yaml` (tipo `login`) em que só o identificador é pedido
    sinal_do_botao: str          # o rótulo do botão que leva à tela da senha ("avançar"): nada secreto sai no toque
    recusas: tuple[RecusaDoIdentificador, ...] = ()


@dataclass(frozen=True, slots=True)
class Formulario:
    sinal_do_botao: str          # sinal que o rótulo do botão de entrar casa por inteiro
    sinal_de_exclusao: str       # sinal que desqualifica um candidato a botão de entrar
    etapa_do_usuario: EtapaDoUsuario | None = None   # sem ela, usuário e senha na mesma tela (como sempre)


@dataclass(frozen=True, slots=True)
class Dispensa:
    intersticiais_max: int                   # teto de telas benignas dispensadas por vez: abre caminho, sem laço
    rotulos: tuple[re.Pattern[str], ...]     # rótulos de RECUSA ("pular", "agora não")
    ids: tuple[re.Pattern[str], ...]         # ids de recusa (o lado de um diálogo que não concede nada)
    sinal_de_salvar_login: str               # o "agora não" do "salvar dados de login?"


@dataclass(frozen=True, slots=True)
class AbaDePerfil:
    prefixo_de_id: str
    rotulos: tuple[str, ...]
    faixa_inferior: float


@dataclass(frozen=True, slots=True)
class AcessoAConta:
    """A conta fora da barra inferior (item 23.6): o elemento que a abre, por prefixo de id ou por rótulo."""

    ids: tuple[str, ...]                     # prefixos do final do resource-id, em minúsculas
    rotulos: tuple[re.Pattern[str], ...]     # expressões no texto ou na descrição do elemento


@dataclass(frozen=True, slots=True)
class Conta:
    extracao: str                # a extração do `telas.yaml` que diz QUEM está logado
    tela_de_perfil: str          # a tela de onde a conta é lida depois de tocar na aba (ou no acesso)
    aba: AbaDePerfil | None      # a aba da barra inferior; exatamente um entre `aba` e `acesso`
    passos_max: int              # telas dispensadas/atravessadas até o perfil: abre caminho, sem laço
    espera_s: float              # espera depois de cada toque da leitura da conta
    acesso: AcessoAConta | None = None
    #: Logo depois do envio, numa tela de casa sem a conta à vista, a conta é lida pelo acesso declarado em vez de
    #: esperar o prazo — para o app cuja tela inicial nunca mostra a conta. Sem isto, todo login bem-sucedido dele
    #: terminaria incerto (e o login automático pararia).
    ler_ao_entrar: bool = False


@dataclass(frozen=True, slots=True)
class Navegador:
    """Custom Tab (item 23.6): o login do app pode abrir no navegador. Só os pacotes daqui contam como navegador, com o
    sufixo do id da barra de endereço de cada um, e o motor só segue num site declarado — os `hosts` daqui (o site de
    login do app) e o `host` da própria conta; em qualquer outro, a pessoa assume. A senha segue a mesma regra, relida
    no instante de digitar."""

    pacotes: tuple[tuple[str, str], ...]     # (pacote do navegador, sufixo do id da barra de endereço)
    hosts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RegraDepoisDoEnvio:
    """Uma linha da tabela de desfechos depois do envio. Casa por UM critério: sinal no texto da tela, tipo da tela
    reconhecida ou nome da tela. A primeira que casar decide."""

    desfecho: str
    detalhe: str = ""
    sinal: str | None = None
    tipos: tuple[str, ...] = ()
    telas: tuple[str, ...] = ()
    etapa: str = ETAPA_PADRAO
    anexar_tela: bool = False


@dataclass(frozen=True, slots=True)
class Textos:
    desafio: str                 # o que a pessoa faz diante de um desafio (vai na sessão e no log)
    desafio_pendente: str        # detalhe padrão de uma sessão parada em desafio sem detalhe gravado
    senha_recusada: str          # por que a automação não tenta com a senha recusada
    credencial_recusada: str     # aviso no log quando a senha é recusada (`{usuario}` = a conta)


@dataclass(frozen=True, slots=True)
class ConhecimentoDeSessao:
    app: str
    versao: int
    rotulo: str
    telas: ConhecimentoDeTelas
    ajustes: Ajustes
    formulario: Formulario
    dispensa: Dispensa
    conta: Conta
    depois_do_envio: tuple[RegraDepoisDoEnvio, ...]
    textos: Textos
    navegador: Navegador | None = None

    # ------------------------------------------------------------------ leituras de tela, pelo dado
    def reconhecer(self, tree: UiTree, *, package: str | None, locale: str | None = None) -> TelaReconhecida:
        """A tela pelo `telas.yaml`, com o formulário de login detectado pela geometria e pelos sinais daqui."""
        return telas_.classificar(self.telas, tree, package=package, locale=locale,
                                  formulario=lambda t: self.formulario_de_login(t, locale))

    def formulario_de_login(self, tree: UiTree, locale: str | None) -> LoginForm | None:
        sig = self.telas.sinais_de(locale)
        return geometria.login_form(tree, entrar=sig[self.formulario.sinal_do_botao],
                                    exclusao=sig[self.formulario.sinal_de_exclusao], ignorar=self._barras())

    def _barras(self) -> geometria.Ignorados:
        """A barra de endereço de cada navegador declarado: nunca é campo do formulário (item 23.6)."""
        return self.navegador.pacotes if self.navegador is not None else ()

    def formulario_de_usuario(self, tree: UiTree, locale: str | None) -> FormularioDoUsuario | None:
        """A etapa do identificador (login em etapas), ou `None` — também quando o app não declara etapas."""
        etapa = self.formulario.etapa_do_usuario
        if etapa is None:
            return None
        sig = self.telas.sinais_de(locale)
        return geometria.identifier_form(tree, avancar=sig[etapa.sinal_do_botao],
                                         exclusao=sig[self.formulario.sinal_de_exclusao], ignorar=self._barras())

    def recusa_do_identificador(self, tree: UiTree, locale: str | None) -> str | None:
        """O detalhe da primeira recusa declarada cujo sinal está na tela depois do "avançar"."""
        etapa = self.formulario.etapa_do_usuario
        if etapa is None or not etapa.recusas:
            return None
        sig = self.telas.sinais_de(locale)
        texto = "\n".join(f"{e.text} {e.desc}".strip() for e in tree.elements if e.text or e.desc)
        return next((r.detalhe for r in etapa.recusas if sig[r.sinal].search(texto)), None)

    def barra_de_endereco(self, pacote: str | None) -> str | None:
        """O sufixo do id da barra de endereço, quando `pacote` é um navegador declarado para a Custom Tab do login."""
        if self.navegador is None or not pacote:
            return None
        return next((sufixo for p, sufixo in self.navegador.pacotes if p == pacote), None)

    def campo_de_senha(self, tree: UiTree) -> UiElement | None:
        return geometria.password_field(tree)

    def botao_de_dispensa(self, tree: UiTree) -> UiElement | None:
        return geometria.dismiss_button(tree, rotulos=self.dispensa.rotulos, ids=self.dispensa.ids)

    def botao_de_nao_salvar_login(self, tree: UiTree, locale: str | None) -> UiElement | None:
        return geometria.save_login_dismiss(
            tree, agora_nao=self.telas.sinais_de(locale)[self.dispensa.sinal_de_salvar_login])

    def aba_de_perfil(self, tree: UiTree) -> tuple[int, int] | None:
        """Onde tocar para abrir a conta: a aba de perfil da barra inferior ou, no app que a declara fora dela, o
        elemento de acesso (item 23.6). O aprendizado pergunta o mesmo ("desta tela a conta pode ser aberta?")."""
        aba, acesso = self.conta.aba, self.conta.acesso
        if acesso is not None:
            return geometria.account_opener(tree, pacote=self.app, prefixos_de_id=acesso.ids, rotulos=acesso.rotulos)
        assert aba is not None                     # a carga exige exatamente um dos dois
        return geometria.profile_tab(tree, prefixo_de_id=aba.prefixo_de_id, rotulos=aba.rotulos,
                                     faixa_inferior=aba.faixa_inferior)

    def _conta_pelo_acesso(self, tree: UiTree) -> str | None:
        """Fora da barra inferior, a conta é lida pela extração declarada, no texto ou na descrição, e só quando a tela
        mostra UM valor: o menu de contas pode listar várias, e a primeira não é a aberta."""
        ex = self.telas.extracoes[self.conta.extracao]
        valores = geometria.valores_extraidos(tree, pacote=self.app, ids=ex.ids, padrao=ex.padrao)
        return next(iter(valores)) if len(valores) == 1 else None

    def conta_no_cabecalho(self, tree: UiTree) -> str | None:
        """A conta lida SÓ pelos ids declarados — a fonte confiável de quem está logado (sem palpite pelo `@`)."""
        if self.conta.acesso is not None:
            return self._conta_pelo_acesso(tree)
        return self.telas.extrair(self.conta.extracao, tree, palpite=False)

    def conta_observada(self, tree: UiTree) -> str | None:
        """A conta pelos ids declarados e, se a extração permitir, pelo primeiro `@texto` da tela. Com o acesso fora da
        barra, sem palpite: só a leitura declarada, com um valor só."""
        if self.conta.acesso is not None:
            return self._conta_pelo_acesso(tree)
        return self.telas.extrair(self.conta.extracao, tree, palpite=True)

    def detalhe_da_etapa(self, etapa: str) -> str:
        """O detalhe fixo da regra que grava esta etapa (diagnóstico e testes procuram por ele)."""
        return next(r.detalhe for r in self.depois_do_envio if r.etapa == etapa)


# ---------------------------------------------------------------------------------------------------- carga
def _mapa(valor: object, onde: str, *, permitidos: frozenset[str],
          obrigatorios: frozenset[str] = frozenset()) -> dict[str, object]:
    if not isinstance(valor, dict):
        raise SessaoInvalida(f"{onde}: esperava um mapa")
    mapa = {str(k): v for k, v in valor.items()}
    if estranhos := sorted(set(mapa) - permitidos):
        raise SessaoInvalida(f"{onde}: campo desconhecido {', '.join(estranhos)} (use {', '.join(sorted(permitidos))})")
    if faltando := sorted(obrigatorios - set(mapa)):
        raise SessaoInvalida(f"{onde}: falta {', '.join(faltando)}")
    return mapa


def _lista(valor: object, onde: str) -> list[object]:
    if not isinstance(valor, list):
        raise SessaoInvalida(f"{onde}: esperava uma lista")
    return list(valor)


def _texto(valor: object, onde: str) -> str:
    if not isinstance(valor, str) or not valor.strip():
        raise SessaoInvalida(f"{onde}: esperava um texto não vazio")
    return valor


def _inteiro(valor: object, onde: str, *, minimo: int) -> int:
    # `bool` é `int` em Python: `true` no lugar de um número passaria calado.
    if isinstance(valor, bool) or not isinstance(valor, int) or valor < minimo:
        raise SessaoInvalida(f"{onde}: esperava um inteiro maior ou igual a {minimo}")
    return valor


def _real(valor: object, onde: str) -> float:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)) or valor <= 0:
        raise SessaoInvalida(f"{onde}: esperava um número maior que zero")
    return float(valor)


def _regex(valor: object, onde: str) -> re.Pattern[str]:
    texto = _texto(valor, onde)
    try:
        return re.compile(texto, re.IGNORECASE)
    except re.error as exc:
        raise SessaoInvalida(f"{onde}: expressão regular inválida ({exc})") from exc


def _sinal(valor: object, onde: str, telas: ConhecimentoDeTelas) -> str:
    """O sinal tem de existir em TODOS os idiomas do `telas.yaml`: o motor lê a tabela do idioma do aparelho, e um
    sinal que falta num idioma só quebraria no meio de um login naquele aparelho."""
    nome = _texto(valor, onde)
    if faltando := sorted(idioma for idioma, tabela in telas.sinais.items() if nome not in tabela):
        raise SessaoInvalida(f"{onde}: sinal {nome!r} não declarado em `telas.yaml` "
                             f"(falta em: {', '.join(faltando)})")
    return nome


def _tela(valor: object, onde: str, telas: ConhecimentoDeTelas) -> str:
    nome = _texto(valor, onde)
    if telas.regra(nome) is None:
        raise SessaoInvalida(f"{onde}: tela {nome!r} não declarada em `telas.yaml`")
    return nome


def _ajustes(valor: object) -> Ajustes:
    campos = {f.name for f in fields(Ajustes)}
    m = _mapa(valor, "ajustes", permitidos=frozenset(campos), obrigatorios=frozenset(campos))
    return Ajustes(max_auth_attempts=_inteiro(m["max_auth_attempts"], "ajustes.max_auth_attempts", minimo=1),
                   auth_cooldown_s=_inteiro(m["auth_cooldown_s"], "ajustes.auth_cooldown_s", minimo=0),
                   max_logins_per_day=_inteiro(m["max_logins_per_day"], "ajustes.max_logins_per_day", minimo=1),
                   open_timeout_s=_real(m["open_timeout_s"], "ajustes.open_timeout_s"),
                   settle_s=_real(m["settle_s"], "ajustes.settle_s"),
                   submit_wait_s=_real(m["submit_wait_s"], "ajustes.submit_wait_s"),
                   verify_timeout_s=_real(m["verify_timeout_s"], "ajustes.verify_timeout_s"))


def _regra(bruto: object, onde: str, telas: ConhecimentoDeTelas) -> RegraDepoisDoEnvio:
    m = _mapa(bruto, onde, permitidos=frozenset({"sinal", "tipos", "telas", "desfecho", "detalhe", "etapa",
                                                 "anexar_tela"}), obrigatorios=frozenset({"desfecho"}))
    criterios = [c for c in ("sinal", "tipos", "telas") if c in m]
    if len(criterios) != 1:
        raise SessaoInvalida(f"{onde}: declare exatamente um critério entre sinal, tipos e telas")
    desfecho = _texto(m["desfecho"], f"{onde}.desfecho")
    if desfecho not in DESFECHOS_DEPOIS_DO_ENVIO:
        raise SessaoInvalida(f"{onde}: desfecho {desfecho!r} fora de {sorted(DESFECHOS_DEPOIS_DO_ENVIO)}")
    if desfecho == CONFERIR_CONTA and ("detalhe" in m or "anexar_tela" in m or "etapa" in m):
        raise SessaoInvalida(f"{onde}: `{CONFERIR_CONTA}` decide o detalhe pela conta lida; tire detalhe/etapa")
    detalhe = "" if desfecho == CONFERIR_CONTA else _texto(m.get("detalhe"), f"{onde}.detalhe")
    sinal = _sinal(m["sinal"], f"{onde}.sinal", telas) if "sinal" in m else None
    tipos = tuple(_texto(t, f"{onde}.tipos") for t in _lista(m["tipos"], f"{onde}.tipos")) if "tipos" in m else ()
    if fora := sorted(set(tipos) - telas_.TIPOS):
        raise SessaoInvalida(f"{onde}: tipo {', '.join(fora)} fora de {sorted(telas_.TIPOS)}")
    nomes = tuple(_tela(t, f"{onde}.telas", telas) for t in _lista(m["telas"], f"{onde}.telas")) if "telas" in m \
        else ()
    if ("tipos" in m and not tipos) or ("telas" in m and not nomes):
        raise SessaoInvalida(f"{onde}: lista vazia não casa com tela nenhuma")
    anexar = m.get("anexar_tela", False)
    if not isinstance(anexar, bool):
        raise SessaoInvalida(f"{onde}.anexar_tela: esperava true ou false")
    etapa = _texto(m["etapa"], f"{onde}.etapa") if "etapa" in m else ETAPA_PADRAO
    return RegraDepoisDoEnvio(desfecho=desfecho, detalhe=detalhe, sinal=sinal, tipos=tipos, telas=nomes,
                              etapa=etapa, anexar_tela=anexar)


def _textos(valor: object) -> Textos:
    campos = frozenset(LACUNAS_DOS_TEXTOS)
    m = _mapa(valor, "textos", permitidos=campos, obrigatorios=campos)
    prontos: dict[str, str] = {}
    for nome, permitidas in LACUNAS_DOS_TEXTOS.items():
        texto = _texto(m[nome], f"textos.{nome}")
        try:
            lacunas = {campo for _, campo, _, _ in Formatter().parse(texto) if campo is not None}
        except ValueError as exc:
            raise SessaoInvalida(f"textos.{nome}: chaves desbalanceadas ({exc})") from exc
        if estranhas := sorted(lacunas - permitidas):
            raise SessaoInvalida(f"textos.{nome}: lacuna {', '.join(repr(x) for x in estranhas)} que o motor não "
                                 f"preenche (permitidas: {', '.join(sorted(permitidas)) or 'nenhuma'})")
        prontos[nome] = texto
    return Textos(**prontos)


def _etapa_do_usuario(valor: object, telas: ConhecimentoDeTelas) -> EtapaDoUsuario:
    """A tela da etapa tem de ser do tipo `login`: é o que a põe no caminho do login (e não no do "voltar ao estado
    conhecido", que apertaria "voltar" numa tela de entrada)."""
    onde = "formulario.etapa_do_usuario"
    e = _mapa(valor, onde, permitidos=frozenset({"tela", "sinal_do_botao", "recusas"}),
              obrigatorios=frozenset({"tela", "sinal_do_botao"}))
    tela = _tela(e["tela"], f"{onde}.tela", telas)
    regra = telas.regra(tela)
    if regra is None or regra.tipo != "login":
        raise SessaoInvalida(f"{onde}.tela: a tela {tela!r} precisa ser do tipo `login` no `telas.yaml`")
    if regra.formulario_de_senha:
        raise SessaoInvalida(f"{onde}.tela: a tela {tela!r} é a do campo de senha; a etapa do usuário é a de antes")
    recusas = []
    for i, bruto in enumerate(_lista(e.get("recusas", []), f"{onde}.recusas")):
        r = _mapa(bruto, f"{onde}.recusas[{i}]", permitidos=frozenset({"sinal", "detalhe"}),
                  obrigatorios=frozenset({"sinal", "detalhe"}))
        recusas.append(RecusaDoIdentificador(sinal=_sinal(r["sinal"], f"{onde}.recusas[{i}].sinal", telas),
                                             detalhe=_texto(r["detalhe"], f"{onde}.recusas[{i}].detalhe")))
    return EtapaDoUsuario(tela=tela, sinal_do_botao=_sinal(e["sinal_do_botao"], f"{onde}.sinal_do_botao", telas),
                          recusas=tuple(recusas))


#: Um host como a barra de endereço mostra: letras, dígitos, hífen e ponto, com pelo menos um ponto; sem esquema,
#: porta nem caminho (o `host` de uma conta segue a mesma forma).
_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")
_PACOTE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+$")


def _navegador(valor: object, app: str) -> Navegador:
    n = _mapa(valor, "navegador", permitidos=frozenset({"pacotes", "hosts"}), obrigatorios=frozenset({"pacotes"}))
    brutos = n["pacotes"]
    if not isinstance(brutos, dict) or not brutos:
        raise SessaoInvalida("navegador.pacotes: esperava um mapa não vazio (pacote do navegador: id da barra)")
    pacotes = []
    for pacote, sufixo in brutos.items():
        pacote = str(pacote).strip()
        if not _PACOTE.match(pacote):
            raise SessaoInvalida(f"navegador.pacotes: {pacote!r} não é um pacote Android")
        if pacote == app:
            raise SessaoInvalida("navegador.pacotes: o próprio app não é navegador (a tela dele já é dele)")
        pacotes.append((pacote, _texto(sufixo, f"navegador.pacotes.{pacote}").strip().lower()))
    hosts = []
    for i, h in enumerate(_lista(n.get("hosts", []), "navegador.hosts")):
        host = _texto(h, f"navegador.hosts[{i}]").strip().lower()
        if not _HOST.match(host):
            raise SessaoInvalida(f"navegador.hosts[{i}]: {host!r} não é um host (sem esquema, porta nem caminho)")
        hosts.append(host)
    return Navegador(pacotes=tuple(pacotes), hosts=tuple(hosts))


def _conta(valor: object, telas: ConhecimentoDeTelas) -> Conta:
    campos_c = frozenset({"extracao", "tela_de_perfil", "aba", "acesso", "passos_max", "espera_s", "ler_ao_entrar"})
    c = _mapa(valor, "conta", permitidos=campos_c,
              obrigatorios=frozenset({"extracao", "tela_de_perfil", "passos_max", "espera_s"}))
    extracao = _texto(c["extracao"], "conta.extracao")
    if extracao not in telas.extracoes:
        raise SessaoInvalida(f"conta.extracao: extração {extracao!r} não declarada em `telas.yaml`")
    if ("aba" in c) == ("acesso" in c):
        raise SessaoInvalida("conta: declare exatamente um entre `aba` (barra inferior) e `acesso` (fora dela)")
    aba = acesso = None
    if "aba" in c:
        campos_a = frozenset({"prefixo_de_id", "rotulos", "faixa_inferior"})
        a = _mapa(c["aba"], "conta.aba", permitidos=campos_a, obrigatorios=campos_a)
        faixa = _real(a["faixa_inferior"], "conta.aba.faixa_inferior")
        if faixa > 1:
            raise SessaoInvalida("conta.aba.faixa_inferior: é uma fração da altura, de 0 a 1")
        aba = AbaDePerfil(prefixo_de_id=_texto(a["prefixo_de_id"], "conta.aba.prefixo_de_id").lower(),
                          rotulos=tuple(_texto(r, "conta.aba.rotulos").lower()
                                        for r in _lista(a["rotulos"], "conta.aba.rotulos")),
                          faixa_inferior=faixa)
    else:
        a = _mapa(c["acesso"], "conta.acesso", permitidos=frozenset({"ids", "rotulos"}))
        ids = tuple(_texto(i, "conta.acesso.ids").strip().lower() for i in _lista(a.get("ids", []), "conta.acesso.ids"))
        rotulos = tuple(_regex(r, f"conta.acesso.rotulos[{i}]")
                        for i, r in enumerate(_lista(a.get("rotulos", []), "conta.acesso.rotulos")))
        if not ids and not rotulos:
            raise SessaoInvalida("conta.acesso: declare `ids` ou `rotulos` do elemento que abre a conta")
        acesso = AcessoAConta(ids=ids, rotulos=rotulos)
    ler = c.get("ler_ao_entrar", False)
    if not isinstance(ler, bool):
        raise SessaoInvalida("conta.ler_ao_entrar: esperava true ou false")
    return Conta(extracao=extracao, tela_de_perfil=_tela(c["tela_de_perfil"], "conta.tela_de_perfil", telas),
                 aba=aba, acesso=acesso, passos_max=_inteiro(c["passos_max"], "conta.passos_max", minimo=1),
                 espera_s=_real(c["espera_s"], "conta.espera_s"), ler_ao_entrar=ler)


def de_dados(dados: object, telas: ConhecimentoDeTelas) -> ConhecimentoDeSessao:
    """Valida e monta o conhecimento de sessão sobre o conhecimento de telas do MESMO app."""
    topo = frozenset({"app", "versao", "rotulo", "ajustes", "formulario", "dispensa", "conta", "depois_do_envio",
                      "textos", "navegador"})
    raiz = _mapa(dados, "o arquivo", permitidos=topo, obrigatorios=topo - {"versao", "navegador"})
    app = _texto(raiz["app"], "app").strip()
    if app != telas.app:
        raise SessaoInvalida(f"`app` ({app!r}) difere do `telas.yaml` ({telas.app!r})")
    versao = _inteiro(raiz.get("versao", 1), "versao", minimo=1)

    f = _mapa(raiz["formulario"], "formulario",
              permitidos=frozenset({"sinal_do_botao", "sinal_de_exclusao", "etapa_do_usuario"}),
              obrigatorios=frozenset({"sinal_do_botao", "sinal_de_exclusao"}))
    form = Formulario(sinal_do_botao=_sinal(f["sinal_do_botao"], "formulario.sinal_do_botao", telas),
                      sinal_de_exclusao=_sinal(f["sinal_de_exclusao"], "formulario.sinal_de_exclusao", telas),
                      etapa_do_usuario=_etapa_do_usuario(f["etapa_do_usuario"], telas)
                      if "etapa_do_usuario" in f else None)

    campos_d = frozenset({"intersticiais_max", "rotulos", "ids", "sinal_de_salvar_login"})
    d = _mapa(raiz["dispensa"], "dispensa", permitidos=campos_d, obrigatorios=campos_d)
    dispensa = Dispensa(
        intersticiais_max=_inteiro(d["intersticiais_max"], "dispensa.intersticiais_max", minimo=0),
        rotulos=tuple(_regex(r, f"dispensa.rotulos[{i}]") for i, r in enumerate(_lista(d["rotulos"],
                                                                                      "dispensa.rotulos"))),
        ids=tuple(_regex(r, f"dispensa.ids[{i}]") for i, r in enumerate(_lista(d["ids"], "dispensa.ids"))),
        sinal_de_salvar_login=_sinal(d["sinal_de_salvar_login"], "dispensa.sinal_de_salvar_login", telas))

    conta = _conta(raiz["conta"], telas)

    regras = tuple(_regra(r, f"depois_do_envio[{i}]", telas)
                   for i, r in enumerate(_lista(raiz["depois_do_envio"], "depois_do_envio")))
    if not regras:
        raise SessaoInvalida("depois_do_envio: sem regra nenhuma, todo envio terminaria incerto")

    return ConhecimentoDeSessao(app=app, versao=versao, rotulo=_texto(raiz["rotulo"], "rotulo").strip(), telas=telas,
                                ajustes=_ajustes(raiz["ajustes"]), formulario=form, dispensa=dispensa, conta=conta,
                                depois_do_envio=regras, textos=_textos(raiz["textos"]),
                                navegador=_navegador(raiz["navegador"], app) if "navegador" in raiz else None)


def carregar(pasta: Path) -> ConhecimentoDeSessao:
    """O conhecimento de sessão de uma pasta de app (`telas.yaml` + `sessao.yaml`)."""
    telas = telas_.carregar(pasta / "telas.yaml")
    return de_dados(yaml.safe_load((pasta / "sessao.yaml").read_text(encoding="utf-8")) or {}, telas)


@lru_cache(maxsize=None)
def do_app(pacote: str) -> ConhecimentoDeSessao:
    """O conhecimento do app deste pacote, carregado uma vez por processo. A pasta tem o nome do pacote, e o arquivo
    tem de dizer o mesmo pacote: um arquivo copiado de outro app sem ajuste é recusado."""
    k = carregar(PASTA_DOS_APPS / pacote)
    if k.app != pacote:
        raise SessaoInvalida(f"a pasta {pacote!r} traz o conhecimento de {k.app!r}")
    return k


# ---------------------------------------------------------------------------------------------------- aprendidas
log = logging.getLogger("poc.conta")

#: Quem entrega as telas aprendidas PUBLICADAS de um pacote (só as que valem no modo `on`).
FornecedorDeRegras = Callable[[str], Sequence[RegraDeTela]]
#: Quanto o que o fornecedor entregou vale sem nova leitura (além da invalidação explícita).
CACHE_DAS_APRENDIDAS_S = 30.0


def _nenhuma(_pacote: str) -> tuple[RegraDeTela, ...]:
    return ()


@dataclass(slots=True)
class _Aprendidas:
    """Estado do processo: o fornecedor ligado, a versão (sobe a cada invalidação) e o que já foi lido/unido."""

    fornecedor: FornecedorDeRegras = _nenhuma
    versao: int = 0
    lidas: dict[str, tuple[int, float, tuple[RegraDeTela, ...]]] = field(default_factory=dict)
    unidos: dict[str, tuple[ConhecimentoDeSessao, tuple[RegraDeTela, ...], ConhecimentoDeSessao]] = \
        field(default_factory=dict)


_APRENDIDAS = _Aprendidas()


def definir_regras_aprendidas(fornecedor: FornecedorDeRegras | None) -> None:
    """Liga (ou, com `None`, desliga) o fornecedor das telas aprendidas. Chamado pela composição do aprendizado e
    pelos testes, que desligam ao terminar."""
    _APRENDIDAS.fornecedor = fornecedor or _nenhuma
    invalidar_regras_aprendidas()


def invalidar_regras_aprendidas() -> None:
    """Esquece o que foi lido: a próxima sessão pergunta de novo ao fornecedor (publicação, desligamento, conflito)."""
    _APRENDIDAS.versao += 1
    _APRENDIDAS.lidas.clear()
    _APRENDIDAS.unidos.clear()


def regras_aprendidas(pacote: str) -> tuple[RegraDeTela, ...]:
    """As telas aprendidas deste pacote que valem agora. Falha do fornecedor é "nenhuma": a sessão segue como o
    repositório declara, nunca cai por causa do aprendizado."""
    agora = time.monotonic()
    lida = _APRENDIDAS.lidas.get(pacote)
    if lida is not None and lida[0] == _APRENDIDAS.versao and agora - lida[1] < CACHE_DAS_APRENDIDAS_S:
        return lida[2]
    versao = _APRENDIDAS.versao
    try:
        regras = tuple(r for r in _APRENDIDAS.fornecedor(pacote) if isinstance(r, RegraDeTela) and r.aprendida)
    except Exception:  # noqa: BLE001 - aprendizado é opcional: sem ele vale o que o repositório declara
        log.exception("telas aprendidas de %s indisponíveis (seguindo só com as declaradas)", pacote)
        regras = ()
    _APRENDIDAS.lidas[pacote] = (versao, agora, regras)
    return regras


def com_as_aprendidas(k: ConhecimentoDeSessao) -> ConhecimentoDeSessao:
    """O conhecimento de sessão com as telas aprendidas unidas às declaradas (`conhecimento_de_telas.com_aprendidas`).
    Sem aprendida, devolve `k` como está — o mesmo objeto."""
    regras = regras_aprendidas(k.app)
    if not regras:
        return k
    unido = _APRENDIDAS.unidos.get(k.app)
    if unido is not None and unido[0] is k and unido[1] == regras:
        return unido[2]
    novo = replace(k, telas=telas_.com_aprendidas(k.telas, regras))
    _APRENDIDAS.unidos[k.app] = (k, regras, novo)
    return novo
