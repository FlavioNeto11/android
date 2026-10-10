"""O que um app declara sobre o próprio CADASTRO (31.310, ADR-087, adendo v1.137): `cadastro.yaml`, ao lado do `sessao.yaml`.

O motor (`cadastro.py`) não conhece app nenhum. Cada app diz, em dado, como o formulário dele se parece: as telas (reconhecidas
por texto e por resource-id), o que fazer em cada uma (tocar, preencher, ler o código do e-mail, ler a conta, parar e chamar
uma pessoa) e onde estão os campos e botões. Tudo é validado na carga, antes do primeiro cadastro; arquivo que não se
sustenta é recusado, nunca "entendido pela metade".

Formato (os textos são expressões regulares, comparadas SEM acento e sem caixa; `id` casa por SUFIXO do resource-id):

    app: com.exemplo.app          # o pacote; a pasta tem de ter o mesmo nome
    rotulo: Exemplo
    passos_max: 40                # teto de telas por execução
    espera_s: 1.0                 # espera depois de cada toque
    envio_espera_s: 10            # quanto esperar a tela mudar depois do envio do formulário
    codigo_espera_s: 90           # quanto esperar o e-mail com o código chegar na caixa da conta
    telas:                        # a PRIMEIRA que casar vale: as de parada vêm antes
      - nome: usuario_indisponivel
        sinais: ["nome de usuario.*indisponivel"]
        acao: parar
        motivo: usuario_indisponivel      # captcha | desafio | telefone | usuario_indisponivel
      - nome: formulario
        sinais: ["criar conta"]
        ids: [":id/signup_form"]          # opcional: ids que têm de existir
        acao: preencher
        campos:
          - {dado: usuario, id: ":id/username"}
          - {dado: nome, texto: "nome completo"}
          - {dado: senha, id: ":id/password", segredo: true}
        botao: {texto: "cadastrar"}
        envia: true                       # o botão ENVIA o cadastro: uma vez só
      - nome: codigo
        sinais: ["digite o codigo"]
        acao: codigo
        campo: {id: ":id/code"}
        botao: {texto: "continuar"}
      - nome: sucesso
        sinais: ["bem-vindo"]
        acao: sucesso
        conta: {id: ":id/profile_name"}   # o elemento cujo texto é o @ da conta criada

O `dado` de um campo é de um vocabulário fechado: `usuario` (o @ desejado), `nome`, `primeiro_nome`, `sobrenome`, `email` (a caixa
da conta), `nascimento_dia`, `nascimento_mes`, `nascimento_ano` (a data de nascimento da persona, em números) e `senha` (só com
`segredo: true`, e só ela é segredo: sai do cofre pelo canal sensível, nunca por aqui).

Um `Alvo` (`botao`, `campo`, `conta` e o alvo de cada campo) acha o elemento por UM ou mais destes critérios, todos juntos (31.324):
`id` (sufixo do resource-id), `texto` (regex sobre o texto ou a descrição), `classe` (a classe, `EditText`), `abaixo_do_rotulo` (regex do
RÓTULO: vale o elemento mais próximo logo abaixo dele, para o app em que o campo não tem id, texto nem descrição e o rótulo é um irmão
acima), `senha: true` (só o que o Android diz ser de senha) e `ordem` (o n-ésimo dos que casam, na ordem de leitura: de cima para baixo e,
na mesma linha, da esquerda para a direita; as rodas de uma data). Zero ou mais de um sem `ordem` = ambíguo = tela desconhecida.

Telas em duas fases (o app que pede o código ANTES de criar a conta): `dispara_codigo: true` na tela cujo botão faz o app mandar o código
(conta uma vez por execução e é o piso do `desde` do código; NÃO muda o estado da conta), `antes_do_envio: true` na tela do código que vem
antes do envio final (`envia: true`, que também pode ser uma tela `tocar`). Os dois andam juntos e cada um aparece no máximo uma vez.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ...automation.conhecimento_de_telas import ConhecimentoInvalido
from ...automation.hierarchy import UiElement, UiTree, normalizar_texto_de_tela
from ...modules.identity.domain.cadastro import PARADAS_DECLARAVEIS, Parada
from ...planning.capabilities import CONHECIMENTO_DE_APPS

PASTA_DOS_APPS = CONHECIMENTO_DE_APPS
ARQUIVO = "cadastro.yaml"

#: O vocabulário fechado do que um campo recebe. `senha` é o único segredo.
DADOS = frozenset({"usuario", "nome", "primeiro_nome", "sobrenome", "email", "senha", "nascimento_dia", "nascimento_mes",
                   "nascimento_ano"})
#: Os dados que vêm da data de nascimento da persona (dado pessoal: nunca em log, evento ou evidência).
DADOS_DE_NASCIMENTO = frozenset({"nascimento_dia", "nascimento_mes", "nascimento_ano"})
ACOES = frozenset({"tocar", "preencher", "codigo", "sucesso", "parar"})

_CHAVES_DO_ARQUIVO = frozenset({"app", "rotulo", "passos_max", "espera_s", "envio_espera_s", "codigo_espera_s", "telas"})
_CHAVES_DA_TELA = frozenset({"nome", "sinais", "ids", "acao", "campos", "campo", "botao", "envia", "motivo", "conta", "dispara_codigo",
                             "antes_do_envio"})
_CHAVES_DO_ALVO = frozenset({"id", "texto", "classe", "abaixo_do_rotulo", "ordem", "senha"})
_CHAVES_DO_CAMPO = frozenset({"dado", "segredo"}) | _CHAVES_DO_ALVO
#: Linhas de 24 px: elementos cujo centro cai na mesma faixa estão "na mesma linha" para a ordem de leitura.
_FAIXA_DA_LINHA_PX = 24


class CadastroInvalido(ConhecimentoInvalido):
    """O `cadastro.yaml` não se sustenta: recusa na carga, antes do primeiro cadastro."""


def _rotulos_de(e: UiElement) -> set[str]:
    return {normalizar_texto_de_tela(x) for x in (e.text, e.desc, f"{e.text} {e.desc}") if x and x.strip()}


def _centro_y(e: UiElement) -> float:
    return (e.bounds[1] + e.bounds[3]) / 2


def _ordem_de_leitura(e: UiElement) -> tuple[int, int]:
    """De cima para baixo, em faixas de `_FAIXA_DA_LINHA_PX`; na mesma faixa, da esquerda para a direita."""
    return int(_centro_y(e) // _FAIXA_DA_LINHA_PX), e.bounds[0]


def _se_sobrepoem_na_horizontal(a: UiElement, b: UiElement) -> bool:
    return max(a.bounds[0], b.bounds[0]) < min(a.bounds[2], b.bounds[2])


@dataclass(frozen=True, slots=True)
class Alvo:
    """Um elemento da tela. Os critérios valem JUNTOS: `id` (sufixo do resource-id), `texto` (regex sobre o rótulo normalizado),
    `classe`, `senha`, `abaixo_do_rotulo` (31.324: o app de conteúdo do servidor, cujo conteúdo vem do servidor (Bloks), não dá id, texto nem
    descrição ao campo; o rótulo é um irmão logo acima) e `ordem` (o n-ésimo dos que casam, na ordem de leitura)."""

    id: str | None = None
    texto: re.Pattern[str] | None = None
    classe: str | None = None
    abaixo_do_rotulo: re.Pattern[str] | None = None
    ordem: int | None = None
    senha: bool = False

    def casa(self, e: UiElement) -> bool:
        if self.id is not None and not (e.resource_id or "").endswith(self.id):
            return False
        if self.texto is not None and not any(self.texto.fullmatch(r) for r in _rotulos_de(e)):
            return False
        if self.classe is not None and not (e.class_name == self.classe or e.class_name.endswith("." + self.classe)):
            return False
        return not (self.senha and not e.password)

    def _logo_abaixo_do_rotulo(self, tree: UiTree, achados: list[UiElement]) -> list[UiElement]:
        """Dos que casam, o que está logo abaixo do rótulo: o de MENOR distância vertical entre os que ficam abaixo dele e se sobrepõem a
        ele na horizontal. Rótulo ausente, rótulo repetido, ninguém abaixo ou empate exato = nenhum (ambíguo, o motor para)."""
        assert self.abaixo_do_rotulo is not None
        rotulos = [e for e in tree.elements if any(self.abaixo_do_rotulo.fullmatch(r) for r in _rotulos_de(e))]
        if len(rotulos) != 1:
            return []
        rotulo = rotulos[0]
        base = _centro_y(rotulo)
        abaixo = [e for e in achados if e is not rotulo and _centro_y(e) > base and _se_sobrepoem_na_horizontal(e, rotulo)]
        if not abaixo:
            return []
        menor = min(_centro_y(e) for e in abaixo)
        proximos = [e for e in abaixo if _centro_y(e) == menor]
        return proximos if len(proximos) == 1 else []

    def unico(self, tree: UiTree, *, editavel: bool = False, clicavel: bool = False) -> UiElement | None:
        """O elemento, se houver UM só (zero ou ambíguo: `None`; o motor trata como tela desconhecida). Com `ordem`, o n-ésimo."""
        achados = [e for e in tree.elements if self.casa(e) and (not editavel or e.editable)
                   and (not clicavel or (e.clickable and e.enabled))]
        if self.abaixo_do_rotulo is not None:
            achados = self._logo_abaixo_do_rotulo(tree, achados)
        if self.ordem is not None:
            achados.sort(key=_ordem_de_leitura)
            return achados[self.ordem - 1] if len(achados) >= self.ordem else None
        return achados[0] if len(achados) == 1 else None


@dataclass(frozen=True, slots=True)
class Campo:
    dado: str
    alvo: Alvo
    segredo: bool = False


@dataclass(frozen=True, slots=True)
class TelaDeCadastro:
    nome: str
    sinais: tuple[re.Pattern[str], ...]
    ids: tuple[str, ...]
    acao: str
    campos: tuple[Campo, ...] = ()
    campo: Alvo | None = None                  # o campo do código
    botao: Alvo | None = None
    envia: bool = False
    motivo: Parada | None = None
    conta: Alvo | None = None                  # o elemento que mostra o @ da conta criada
    dispara_codigo: bool = False               # o botão faz o app MANDAR o código (31.324): uma vez por execução, não envia
    antes_do_envio: bool = False               # (`codigo`) o código vem ANTES do envio final

    def casa(self, texto: str, tree: UiTree) -> bool:
        if not all(s.search(texto) for s in self.sinais):
            return False
        return all(any((e.resource_id or "").endswith(i) for e in tree.elements) for i in self.ids)


@dataclass(frozen=True, slots=True)
class ConhecimentoDeCadastro:
    app: str
    rotulo: str
    passos_max: int
    espera_s: float
    envio_espera_s: float
    codigo_espera_s: float
    telas: tuple[TelaDeCadastro, ...]

    @property
    def dados_usados(self) -> frozenset[str]:
        """Os dados que as telas pedem: o serviço confere antes de despachar que todos existem (caixa, senha...)."""
        return frozenset(c.dado for t in self.telas for c in t.campos)

    @property
    def pede_codigo(self) -> bool:
        return any(t.acao == "codigo" for t in self.telas)

    @property
    def pede_nascimento(self) -> bool:
        """Alguma tela preenche a data de nascimento: o serviço confere antes de tocar no aparelho que a persona tem uma (e é adulta)."""
        return bool(self.dados_usados & DADOS_DE_NASCIMENTO)

    def reconhecer(self, tree: UiTree) -> TelaDeCadastro | None:
        texto = "\n".join(normalizar_texto_de_tela(f"{e.text} {e.desc}") for e in tree.elements if e.text or e.desc)
        for tela in self.telas:
            if tela.casa(texto, tree):
                return tela
        return None


# ----------------------------------------------------------------------------------------------------------- carga
def _regex(valor: object, onde: str) -> re.Pattern[str]:
    if not isinstance(valor, str) or not valor.strip():
        raise CadastroInvalido(f"{onde}: esperava uma expressão regular não vazia")
    try:
        return re.compile(normalizar_texto_de_tela(valor), re.IGNORECASE)
    except re.error as exc:
        raise CadastroInvalido(f"{onde}: expressão regular inválida ({exc})") from None


def _chaves(d: object, permitidas: frozenset[str], onde: str) -> dict[str, object]:
    if not isinstance(d, dict):
        raise CadastroInvalido(f"{onde}: esperava um mapa")
    extras = set(d) - permitidas
    if extras:
        raise CadastroInvalido(f"{onde}: chave desconhecida {sorted(map(str, extras))}")
    return d


def _alvo(d: object, onde: str) -> Alvo:
    m = _chaves(d, _CHAVES_DO_ALVO, onde)
    ident = m.get("id")
    if ident is not None and (not isinstance(ident, str) or not ident.strip()):
        raise CadastroInvalido(f"{onde}.id: esperava um sufixo de resource-id")
    texto = _regex(m["texto"], f"{onde}.texto") if m.get("texto") is not None else None
    classe = m.get("classe")
    if classe is not None and (not isinstance(classe, str) or not re.fullmatch(r"[A-Za-z_][\w.$]*", classe)):
        raise CadastroInvalido(f"{onde}.classe: esperava o nome da classe (`EditText`)")
    abaixo = _regex(m["abaixo_do_rotulo"], f"{onde}.abaixo_do_rotulo") if m.get("abaixo_do_rotulo") is not None else None
    ordem = m.get("ordem")
    if ordem is not None and (isinstance(ordem, bool) or not isinstance(ordem, int) or not 1 <= ordem <= 20):
        raise CadastroInvalido(f"{onde}.ordem: um inteiro de 1 a 20 (1 = o primeiro na ordem de leitura)")
    senha = m.get("senha", False)
    if not isinstance(senha, bool):
        raise CadastroInvalido(f"{onde}.senha: esperava true ou false")
    if ident is None and texto is None and classe is None and abaixo is None and not senha:
        raise CadastroInvalido(f"{onde}: diga `id`, `texto`, `classe`, `abaixo_do_rotulo` ou `senha` (só `ordem` casaria com qualquer coisa)")
    return Alvo(id=ident, texto=texto, classe=classe, abaixo_do_rotulo=abaixo, ordem=ordem, senha=senha)


def _campo(d: object, onde: str) -> Campo:
    m = _chaves(d, _CHAVES_DO_CAMPO, onde)
    dado = m.get("dado")
    if dado not in DADOS:
        raise CadastroInvalido(f"{onde}.dado: {dado!r} não está em {sorted(DADOS)}")
    segredo = bool(m.get("segredo", False))
    if (dado == "senha") != segredo:
        raise CadastroInvalido(f"{onde}: só `senha` é segredo, e `senha` tem de declarar `segredo: true`")
    return Campo(str(dado), _alvo({k: v for k, v in m.items() if k in _CHAVES_DO_ALVO}, onde), segredo)


def _booleano(m: dict[str, object], chave: str, onde: str) -> bool:
    v = m.get(chave, False)
    if not isinstance(v, bool):
        raise CadastroInvalido(f"{onde}.{chave}: esperava true ou false")
    return v


def _tela(d: object, indice: int) -> TelaDeCadastro:
    onde = f"telas[{indice}]"
    m = _chaves(d, _CHAVES_DA_TELA, onde)
    nome = m.get("nome")
    if not isinstance(nome, str) or not nome.strip():
        raise CadastroInvalido(f"{onde}.nome: obrigatório")
    onde = f"telas[{nome}]"
    acao = m.get("acao")
    if acao not in ACOES:
        raise CadastroInvalido(f"{onde}.acao: {acao!r} não está em {sorted(ACOES)}")
    brutos = m.get("sinais")
    if not isinstance(brutos, list) or not brutos:
        raise CadastroInvalido(f"{onde}.sinais: ao menos um (uma tela sem sinal casaria com qualquer coisa)")
    ids = m.get("ids", [])
    if not isinstance(ids, list) or not all(isinstance(i, str) and i.strip() for i in ids):
        raise CadastroInvalido(f"{onde}.ids: esperava uma lista de sufixos de resource-id")
    sinais = tuple(_regex(s, f"{onde}.sinais") for s in brutos)
    campos = tuple(_campo(c, f"{onde}.campos[{i}]") for i, c in enumerate(m.get("campos") or []))
    campo = _alvo(m["campo"], f"{onde}.campo") if m.get("campo") is not None else None
    botao = _alvo(m["botao"], f"{onde}.botao") if m.get("botao") is not None else None
    conta = _alvo(m["conta"], f"{onde}.conta") if m.get("conta") is not None else None
    envia = bool(m.get("envia", False))
    motivo: Parada | None = None
    if acao == "parar":
        try:
            motivo = Parada(str(m.get("motivo")))
        except ValueError:
            motivo = None
        if motivo is None or motivo not in PARADAS_DECLARAVEIS:
            raise CadastroInvalido(f"{onde}.motivo: um de {sorted(p.value for p in PARADAS_DECLARAVEIS)}")
    elif m.get("motivo") is not None:
        raise CadastroInvalido(f"{onde}: `motivo` só vale em `parar`")
    exigidos = {"tocar": (botao,), "preencher": (botao, campos), "codigo": (campo, botao), "sucesso": (conta,),
                "parar": ()}[str(acao)]
    if not all(exigidos):
        raise CadastroInvalido(f"{onde}: `{acao}` precisa de "
                               + {"tocar": "`botao`", "preencher": "`campos` e `botao`", "codigo": "`campo` e `botao`",
                                  "sucesso": "`conta`"}[str(acao)])
    dispara = _booleano(m, "dispara_codigo", onde)
    antes = _booleano(m, "antes_do_envio", onde)
    if envia and acao not in ("preencher", "tocar"):
        raise CadastroInvalido(f"{onde}: `envia` só vale em `preencher` ou `tocar`")
    if dispara and acao not in ("preencher", "tocar"):
        raise CadastroInvalido(f"{onde}: `dispara_codigo` só vale em `preencher` ou `tocar`")
    if dispara and envia:
        raise CadastroInvalido(f"{onde}: a mesma tela não pode `envia` e `dispara_codigo`")
    if antes and acao != "codigo":
        raise CadastroInvalido(f"{onde}: `antes_do_envio` só vale em `codigo`")
    return TelaDeCadastro(nome=nome, sinais=sinais, ids=tuple(ids), acao=str(acao), campos=campos, campo=campo,
                          botao=botao, envia=envia, motivo=motivo, conta=conta, dispara_codigo=dispara, antes_do_envio=antes)


def de_dados(dados: object) -> ConhecimentoDeCadastro:
    m = _chaves(dados, _CHAVES_DO_ARQUIVO, ARQUIVO)
    app = m.get("app")
    if not isinstance(app, str) or not app.strip():
        raise CadastroInvalido("`app`: o pacote é obrigatório")
    brutas = m.get("telas")
    if not isinstance(brutas, list) or not brutas:
        raise CadastroInvalido("`telas`: ao menos uma")
    telas = tuple(_tela(t, i) for i, t in enumerate(brutas))
    nomes = [t.nome for t in telas]
    if len(set(nomes)) != len(nomes):
        raise CadastroInvalido("`telas`: nomes repetidos")
    # O que faz o cadastro terminar e a conta ser comprovada: sem isto o motor não tem como saber que acabou.
    if sum(t.acao == "sucesso" for t in telas) != 1:
        raise CadastroInvalido("`telas`: exatamente UMA tela de `sucesso` (é dela que a conta é lida)")
    if sum(t.acao == "codigo" for t in telas) > 1:
        raise CadastroInvalido("`telas`: no máximo uma tela de `codigo`")
    formularios = [t for t in telas if t.acao == "preencher"]
    envios = [t for t in telas if t.envia]
    if len(envios) != 1:
        raise CadastroInvalido("`telas`: exatamente UM formulário com `envia: true` (o envio acontece uma vez só)")
    # O @ pode ser pedido numa tela e o envio final noutra (o app de uma pergunta por tela): basta que alguma tela o preencha.
    if not any(c.dado == "usuario" for t in formularios for c in t.campos):
        raise CadastroInvalido("`telas`: alguma tela precisa preencher o `usuario` desejado (o formulário de envio, no app de uma tela só)")
    disparos = [t for t in telas if t.dispara_codigo]
    antes_do_envio = [t for t in telas if t.antes_do_envio]
    if len(disparos) > 1 or len(antes_do_envio) > 1:
        raise CadastroInvalido("`telas`: no máximo uma tela com `dispara_codigo` e uma com `antes_do_envio`")
    if bool(disparos) != bool(antes_do_envio):
        raise CadastroInvalido("`telas`: `dispara_codigo` e `antes_do_envio` andam juntos (o piso do `desde` do código vem de um e o "
                               "outro lê o código)")
    for t in formularios:
        if sum(c.dado == "senha" for c in t.campos) > 1:
            raise CadastroInvalido(f"`telas[{t.nome}]`: um campo de senha só")
    return ConhecimentoDeCadastro(
        app=app.strip(), rotulo=str(m.get("rotulo") or app).strip(), passos_max=_inteiro(m, "passos_max", 40, 5, 200),
        espera_s=_real(m, "espera_s", 1.0, 0.0, 30.0), envio_espera_s=_real(m, "envio_espera_s", 10.0, 0.0, 120.0),
        codigo_espera_s=_real(m, "codigo_espera_s", 90.0, 0.0, 600.0), telas=telas)


def _inteiro(m: dict[str, object], chave: str, padrao: int, minimo: int, maximo: int) -> int:
    v = m.get(chave, padrao)
    if isinstance(v, bool) or not isinstance(v, int) or not minimo <= v <= maximo:
        raise CadastroInvalido(f"`{chave}`: um inteiro entre {minimo} e {maximo}")
    return v


def _real(m: dict[str, object], chave: str, padrao: float, minimo: float, maximo: float) -> float:
    v = m.get(chave, padrao)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not minimo <= float(v) <= maximo:
        raise CadastroInvalido(f"`{chave}`: um número entre {minimo} e {maximo}")
    return float(v)


def carregar(pasta: Path) -> ConhecimentoDeCadastro | None:
    """O `cadastro.yaml` de uma pasta de app; `None` quando o app não o declara."""
    arquivo = pasta / ARQUIVO
    if not arquivo.is_file():
        return None
    return de_dados(yaml.safe_load(arquivo.read_text(encoding="utf-8")) or {})


@lru_cache(maxsize=None)
def do_app(pacote: str) -> ConhecimentoDeCadastro | None:
    """O cadastro declarado do app deste pacote (uma vez por processo). A pasta tem o nome do pacote e o arquivo tem de dizer
    o mesmo pacote: um arquivo copiado de outro app sem ajuste é recusado."""
    k = carregar(PASTA_DOS_APPS / pacote)
    if k is not None and k.app != pacote:
        raise CadastroInvalido(f"a pasta {pacote!r} traz o cadastro de {k.app!r}")
    return k
