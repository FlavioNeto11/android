"""Ensaio de pacote de app (item 12.3): rodar o MOTOR DE SESSÃO real contra um aparelho de mentira descrito em YAML.

Quem declara um app novo (`telas.yaml` + `sessao.yaml`) precisava escrever à mão um dublê de aparelho em Python para ver o
login funcionar (o `FakeCorreio` de `test_sessao_declarada.py`). Aqui o dublê também é dado: o `ensaio.yaml` da pasta do app
descreve as telas, o que cada toque faz e os cenários esperados, e este módulo roda o `SessaoDeclarada` de verdade (banco,
cofre e canal sensível reais, em pasta temporária) e confere o desfecho. É ferramenta de AUTORIA e de teste, nunca roda no
processo da central, e nada dele toca aparelho nem conta reais.

Formato do `ensaio.yaml` (todas as chaves são conferidas; o que sobra é erro, com o caminho):

    app: com.exemplo.email            # o pacote: tem de ser o da pasta
    usuario: ana.correio              # o @ da conta de ensaio
    senha: senha-de-ensaio            # FICTÍCIA; vai ao cofre de ensaio e só ao campo de senha
    inicial: entrada                  # tela do app deslogado
    inicial_logado: caixa             # tela do app já logado (omitido = a mesma de `inicial`)
    telas:
      entrada:
        - {classe: EditText, id: campo_usuario, foco: usuario, clicavel: true}
        - {classe: EditText, id: campo_senha, foco: senha, senha: true, clicavel: true}
        - {classe: Button, texto: Entrar, id: botao_entrar, clicavel: true, ao_tocar: enviar}
        - {classe: TextView, texto: Senha incorreta., se: erro}
      caixa:
        - {classe: TextView, texto: "{conta}", id: account_name}
        - {classe: View, id: message_list}
    envio:                            # o que o toque em `ao_tocar: enviar` faz, na ordem; vale o primeiro `se` verdadeiro
      - {se: senha_errada, vai_para: entrada, erro: true}
      - {se: senha_certa, vai_para: caixa, entra_na_conta: true}
    cenarios:
      - nome: login feliz
        espera: {desfecho: session_ready, usuario_observado: ana.correio}
      - nome: senha recusada
        aparelho: {senha_aceita: outra-coisa}        # o app aceita OUTRA senha
        espera: {desfecho: invalid_credential}

Nó: `classe` (nome curto do widget Android), `id`, `texto` (aceita `{conta}` = quem está logado e `{usuario}`/`{senha}` não: o
valor digitado só aparece no nó com `foco`), `desc`, `clicavel`, `senha` (campo de senha), `foco` (nome do campo que ele
edita), `ao_tocar` (`enviar`, `ir:<tela>` ou `abrir:<tela>`), `se` (`erro`: só existe com o último envio recusado) e `rodape`
(posição na faixa inferior). `ir` troca de tela; `abrir` também, e é o que a aba da conta usa.

`espera` por cenário: `desfecho` (um `Outcome`), `usuario_observado` (opcional), `digitou_a_senha` (opcional, padrão:
verdadeiro só em `session_ready` e `invalid_credential`), `nunca_digitou_a_senha` (atalho de `digitou_a_senha: false`) e `sessao`
(opcional: o `SessionStatus` que ficou gravado para a conta, p.ex. `unknown` quando o app parou numa tela que ninguém conhece:
a conta fica à espera de uma pessoa, e o ator não é chamado).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

import yaml
from pydantic import SecretStr

from app.db import Database
from app.events import EventBus
from app.integrations.app_declarado import conhecimento
from app.integrations.app_declarado.sessao import Outcome, SessaoDeclarada
from app.models import ProfileAccountCreate
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService

from .conftest import make_config
from .test_instagram_auth import FakeDevices, FakeRt, cadastrar

ARQUIVO = "ensaio.yaml"
_RAIZ = {"app", "usuario", "senha", "inicial", "inicial_logado", "telas", "envio", "cenarios"}
_NO = {"classe", "id", "texto", "desc", "clicavel", "senha", "foco", "ao_tocar", "se", "rodape"}
_ENVIO = {"se", "vai_para", "erro", "entra_na_conta"}
_CENARIO = {"nome", "aparelho", "envio", "espera"}
_APARELHO = {"senha_aceita", "conta", "tela"}
_ESPERA = {"desfecho", "usuario_observado", "digitou_a_senha", "nunca_digitou_a_senha", "sessao"}
_SE_DO_ENVIO = {"senha_certa", "senha_errada"}
#: Painel de 720x1280 do dublê: nós empilhados de cima para baixo; os de `rodape` ficam na faixa inferior.
_LARGURA, _ALTURA_DO_NO, _PASSO, _TOPO, _RODAPE_Y = 720, 70, 90, 60, 1180


class RoteiroInvalido(Exception):
    """O `ensaio.yaml` não se sustenta (chave desconhecida, tela que não existe, cenário sem espera...)."""


def _mapa(v: object, onde: str, permitidas: set[str]) -> dict[str, Any]:
    if not isinstance(v, dict):
        raise RoteiroInvalido(f"{onde}: esperava um mapa")
    extras = sorted(str(k) for k in v if k not in permitidas)
    if extras:
        raise RoteiroInvalido(f"{onde}: campo desconhecido {', '.join(extras)} (aceitos: {', '.join(sorted(permitidas))})")
    return v


def _lista(v: object, onde: str) -> list[Any]:
    if not isinstance(v, list) or not v:
        raise RoteiroInvalido(f"{onde}: esperava uma lista não vazia")
    return v


def _texto(v: object, onde: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise RoteiroInvalido(f"{onde}: esperava um texto")
    return v


@dataclass(frozen=True)
class Cenario:
    nome: str
    aparelho: dict[str, Any]
    envio: list[dict[str, Any]] | None
    espera: dict[str, Any]


@dataclass(frozen=True)
class Roteiro:
    pasta: Path
    app: str
    usuario: str
    senha: str
    inicial: str
    inicial_logado: str
    telas: dict[str, list[dict[str, Any]]]
    envio: list[dict[str, Any]]
    cenarios: list[Cenario]


def _regras_de_envio(v: object, onde: str, telas: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    regras: list[dict[str, Any]] = []
    for i, bruta in enumerate(_lista(v, onde)):
        r = _mapa(bruta, f"{onde}[{i}]", _ENVIO)
        if r.get("se") not in _SE_DO_ENVIO:
            raise RoteiroInvalido(f"{onde}[{i}].se: use {' ou '.join(sorted(_SE_DO_ENVIO))}")
        if r.get("vai_para") not in telas:
            raise RoteiroInvalido(f"{onde}[{i}].vai_para: a tela {r.get('vai_para')!r} não existe em `telas`")
        regras.append(r)
    return regras


def de_dados(dados: object, pasta: Path) -> Roteiro:
    raiz = _mapa(dados, str(pasta / ARQUIVO), _RAIZ)
    app = _texto(raiz.get("app"), "app")
    if app != pasta.name:
        raise RoteiroInvalido(f"{pasta / ARQUIVO}: o roteiro é de {app!r}, mas a pasta é {pasta.name!r}")
    telas: dict[str, list[dict[str, Any]]] = {}
    brutas = raiz.get("telas")
    if not isinstance(brutas, dict) or not brutas:
        raise RoteiroInvalido("telas: esperava um mapa de telas não vazio")
    for nome, nos in brutas.items():
        telas[str(nome)] = [_mapa(n, f"telas.{nome}[{i}]", _NO) for i, n in enumerate(_lista(nos, f"telas.{nome}"))]
    inicial = _texto(raiz.get("inicial"), "inicial")
    inicial_logado = str(raiz.get("inicial_logado") or inicial)
    for chave, valor in (("inicial", inicial), ("inicial_logado", inicial_logado)):
        if valor not in telas:
            raise RoteiroInvalido(f"{chave}: a tela {valor!r} não existe em `telas`")
    for nome, nos in telas.items():
        for i, n in enumerate(nos):
            acao = str(n.get("ao_tocar") or "")
            if acao and acao != "enviar" and acao.split(":", 1)[-1] not in telas:
                raise RoteiroInvalido(f"telas.{nome}[{i}].ao_tocar: a tela de {acao!r} não existe em `telas`")
            if acao and not acao.startswith(("ir:", "abrir:")) and acao != "enviar":
                raise RoteiroInvalido(f"telas.{nome}[{i}].ao_tocar: use `enviar`, `ir:<tela>` ou `abrir:<tela>`")
    envio = _regras_de_envio(raiz.get("envio"), "envio", telas)
    cenarios: list[Cenario] = []
    nomes: set[str] = set()
    for i, bruto in enumerate(_lista(raiz.get("cenarios"), "cenarios")):
        c = _mapa(bruto, f"cenarios[{i}]", _CENARIO)
        nome = _texto(c.get("nome"), f"cenarios[{i}].nome")
        if nome in nomes:
            raise RoteiroInvalido(f"cenarios[{i}].nome: {nome!r} repetido")
        nomes.add(nome)
        aparelho = _mapa(c.get("aparelho") or {}, f"cenarios[{i}].aparelho", _APARELHO)
        if "tela" in aparelho and aparelho["tela"] not in telas:
            raise RoteiroInvalido(f"cenarios[{i}].aparelho.tela: a tela {aparelho['tela']!r} não existe em `telas`")
        espera = _mapa(c.get("espera"), f"cenarios[{i}].espera", _ESPERA)
        if espera.get("desfecho") not in {o.value for o in Outcome}:
            raise RoteiroInvalido(f"cenarios[{i}].espera.desfecho: use {', '.join(sorted(o.value for o in Outcome))}")
        regras = _regras_de_envio(c["envio"], f"cenarios[{i}].envio", telas) if "envio" in c else None
        cenarios.append(Cenario(nome, aparelho, regras, espera))
    return Roteiro(pasta, app, _texto(raiz.get("usuario"), "usuario"), _texto(raiz.get("senha"), "senha"), inicial,
                   inicial_logado, telas, envio, cenarios)


def carregar_roteiro(pasta: Path) -> Roteiro:
    return de_dados(yaml.safe_load((pasta / ARQUIVO).read_text(encoding="utf-8")) or {}, pasta)


def pastas_com_roteiro(raiz: Path) -> list[Path]:
    return sorted(p.parent for p in raiz.glob(f"*/{ARQUIVO}"))


# ------------------------------------------------------------------------------------------------ o aparelho de mentira
@dataclass
class _No:
    cls: str
    bounds: tuple[int, int, int, int]
    text: str = ""
    rid: str = ""
    desc: str = ""
    clickable: bool = False
    password: bool = False
    spec: dict[str, Any] = field(default_factory=dict)


@dataclass
class AparelhoRoteirizado:
    """A superfície que o motor de sessão usa de um aparelho (`FakeCorreio`, mas guiada pelo roteiro)."""

    roteiro: Roteiro
    senha_aceita: str
    envio: list[dict[str, Any]]
    conta: str | None = None
    tela: str = ""
    erro: bool = False
    digitados: list[str] = field(default_factory=list)          # tudo o que chegou por `type_text` (a senha também)
    calls: list[str] = field(default_factory=list)
    valores: dict[str, str] = field(default_factory=dict)
    _foco: str = ""
    _nos: list[_No] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tela = self.tela or (self.roteiro.inicial_logado if self.conta else self.roteiro.inicial)

    # --- superfície do driver
    def current_package(self) -> str | None:
        return self.roteiro.app

    def current_focus(self) -> tuple[str | None, str | None]:
        return self.roteiro.app, ".Principal"

    def getprop(self, name: str) -> str:
        return "pt-BR" if name == "ro.product.locale" else ""

    def start_app(self, package: str, activity: str | None = None) -> None:
        # Abrir o app volta à tela de casa; uma tela fora das duas de casa (desafio, novidade) sobrevive, como no aparelho.
        if self.tela in (self.roteiro.inicial, self.roteiro.inicial_logado) or self.tela in ("",):
            self.tela = self.roteiro.inicial_logado if self.conta else self.roteiro.inicial

    def _montar(self) -> list[_No]:
        nos: list[_No] = []
        y = _TOPO
        abas = 0
        for spec in self.roteiro.telas[self.tela]:
            if spec.get("se") == "erro" and not self.erro:
                continue
            foco = str(spec.get("foco") or "")
            texto = str(spec.get("texto") or "").replace("{conta}", self.conta or "")
            if foco:
                valor = self.valores.get(foco, "")
                texto = ("••••" if valor else "") if spec.get("senha") else valor
            if spec.get("rodape"):
                x0 = 20 + 360 * abas                              # as abas da faixa inferior ficam lado a lado
                abas += 1
                caixa = (x0, _RODAPE_Y, x0 + 320, _RODAPE_Y + 80)
            else:
                caixa = (40, y, _LARGURA - 40, y + _ALTURA_DO_NO)
                y += _PASSO
            nos.append(_No("android.widget." + str(spec.get("classe") or "View"), caixa, texto, str(spec.get("id") or ""),
                           str(spec.get("desc") or ""), bool(spec.get("clicavel")), bool(spec.get("senha")), spec))
        return nos

    def page_source(self) -> str:
        self._nos = self._montar()
        pacote = self.roteiro.app
        linhas = "".join(
            f'<node class={quoteattr(n.cls)} package="{pacote}" text={quoteattr(n.text)} '
            f'resource-id={quoteattr((pacote + ":id/" + n.rid) if n.rid else "")} content-desc={quoteattr(n.desc)} '
            f'clickable="{str(n.clickable).lower()}" enabled="true" focused="false" '
            f'password="{str(n.password).lower()}" scrollable="false" '
            f'bounds="[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]" />' for n in self._nos)
        return f'<hierarchy rotation="0">{linhas}</hierarchy>'

    def tap(self, x: int, y: int) -> None:
        self.calls.append(f"tap:{x},{y}")
        alvo = next((n for n in self._nos if n.clickable and n.bounds[0] <= x <= n.bounds[2]
                     and n.bounds[1] <= y <= n.bounds[3]), None)
        if alvo is None:
            return
        foco = str(alvo.spec.get("foco") or "")
        if foco:
            self._foco = foco
        acao = str(alvo.spec.get("ao_tocar") or "")
        if acao == "enviar":
            self._enviar()
        elif acao:
            self.tela = acao.split(":", 1)[1]

    def _enviar(self) -> None:
        self.calls.append("enviar")
        certa = self.valores.get("senha", "") == self.senha_aceita
        for regra in self.envio:
            if (regra["se"] == "senha_certa") != certa:
                continue
            self.erro = bool(regra.get("erro"))
            if regra.get("erro"):
                self.valores["senha"] = ""
            if regra.get("entra_na_conta"):
                self.conta = self.valores.get("usuario", "").lstrip("@")
                self.valores = {}
            self.tela = str(regra["vai_para"])
            return

    def type_text(self, text: str, *, clear_first: bool) -> None:
        if text:
            self.digitados.append(text)
        if self._foco:
            antes = "" if clear_first else self.valores.get(self._foco, "")
            self.valores[self._foco] = antes + text

    def press_key(self, key: str) -> None:
        self.calls.append(f"key:{key}")


# ------------------------------------------------------------------------------------------------ a execução
@dataclass
class Resultado:
    cenario: str
    desfecho: str
    detalhe: str
    usuario_observado: str | None
    digitou_a_senha: bool
    problemas: list[str]


async def ensaiar(roteiro: Roteiro, cenario: Cenario, tmp: Path) -> Resultado:
    """Roda UM cenário: monta banco e cofre de ensaio, uma persona com a conta do app (senha fictícia consentida) e chama
    `ensure_session`. Os `problemas` dizem onde o desfecho ou o que foi digitado não bate com `espera`."""
    k = conhecimento.carregar(roteiro.pasta)
    cfg = make_config(tmp)
    cfg.ensure_dirs()
    db = Database(cfg.db_dsn)
    try:
        db.migrate()
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('ensaio', 'App de ensaio', ?, 0)", (roteiro.app,))
        bus = EventBus(db)
        secrets = SecretStore(db, MemoryKeyProvider())
        repo = SocialRepository(db)
        social = SocialService(repo, secrets, bus, known_instances=lambda: ["android-01", "android-02"])
        aparelho = AparelhoRoteirizado(
            roteiro, str(cenario.aparelho.get("senha_aceita", roteiro.senha)),
            cenario.envio if cenario.envio is not None else roteiro.envio,
            conta=cenario.aparelho.get("conta"), tela=str(cenario.aparelho.get("tela") or ""))
        sessao = SessaoDeclarada(k, cfg, FakeDevices(aparelho), repo, secrets,  # type: ignore[arg-type]
                                 SensitiveInputChannel(lambda: True), bus)
        sessao.focus_poll_s = 0.01
        pid = cadastrar(social, username="ensaio.ancora", senha="ancora-de-ensaio", instance_id="android-02")
        conta_id = social.add_account(pid, ProfileAccountCreate(app_id="ensaio", handle=roteiro.usuario,
                                                                password=SecretStr(roteiro.senha), consent=True)).id
        r = await sessao.ensure_session(FakeRt(aparelho), pid)             # type: ignore[arg-type]
        linha = repo.account_session_row(pid, conta_id, "android-02")
        status = str(linha["status"]) if linha is not None else None
    finally:
        db.close()
    digitou = roteiro.senha in aparelho.digitados
    espera = cenario.espera
    problemas: list[str] = []
    if r.outcome.value != espera["desfecho"]:
        problemas.append(f"desfecho {r.outcome.value!r}, esperado {espera['desfecho']!r} ({r.detail})")
    if "usuario_observado" in espera and r.observed_username != espera["usuario_observado"]:
        problemas.append(f"usuário observado {r.observed_username!r}, esperado {espera['usuario_observado']!r}")
    quer_senha = espera.get("digitou_a_senha")
    if espera.get("nunca_digitou_a_senha"):
        quer_senha = False
    if quer_senha is None:
        quer_senha = espera["desfecho"] in (Outcome.SESSION_READY.value, Outcome.INVALID_CREDENTIAL.value)
    if "sessao" in espera and status != espera["sessao"]:
        problemas.append(f"sessão gravada {status!r}, esperado {espera['sessao']!r}")
    if digitou != bool(quer_senha):
        problemas.append("a senha " + ("foi digitada" if digitou else "não foi digitada") + f", esperado o contrário")
    return Resultado(cenario.nome, r.outcome.value, str(r.detail or ""), r.observed_username, digitou, problemas)
