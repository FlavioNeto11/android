"""Valor lido entre etapas (item 24.3, ADR-058, contrato C2): a etapa que LÊ um valor na tela e a que o USA.

- A etapa declara os nomes que entrega (`PlanStep.saidas`). O ator lê cada um com `read_value`, e quem tira o valor
  é o executor, do TEXTO do elemento na árvore observada — nunca da palavra do modelo. É o princípio de
  `collect_list`: o que atravessa etapas é fato da tela, não alegação.
- A etapa seguinte o cita como `{{saida:<nome>}}` no texto, nos argumentos (`bindings`) ou nas variáveis. O despacho
  resolve a referência ANTES da porta de política (`Scheduler._work`): aprovação, limite por alvo, coordenação de
  frota e o ator enxergam o valor lido, não o molde.
- Código de verificação, senha, token ou segredo nunca é saída (D3; ADR-009, ADR-022): a triagem por formato recusa,
  a etapa para para uma pessoa, e o valor não fica gravado em lugar nenhum.

Funções puras, usadas pelo executor, pelo repositório, pelo despacho e pela expansão de `for_each`.
"""
from __future__ import annotations

import json
import re

from ..automation.hierarchy import (SUBTIPO_CODIGO, UiElement, UiTree, detectar_trava_generica,
                                    normalizar_texto_de_tela)
from ..models import SAIDA_VALOR_MAX, SAIDA_VALUE_KINDS, PlanStep
from ..security.redaction import looks_secret, mentions_credential, parece_senha_ou_codigo
from ..util import url_abrivel

#: `{{saida:<nome>}}`, o nome no alfabeto de `SAIDA_NOME_RE`. Espaço junto das chaves é tolerado (o planejador escreve
#: à mão). A chave DUPLA não colide com `{nome}` das variáveis: `TEMPLATE_RE` exige a chave colada no nome, e em
#: `{{saida:x}}` o que vem depois de `{saida` é `:` — a materialização (`_insert_steps`) deixa a referência intacta.
SAIDA_REF_RE = re.compile(r"\{\{\s*saida:([a-z][a-z0-9_]{0,39})\s*\}\}")

#: Nome da variável de receita que carrega o valor de uma saída (`rr.variables`): com ela, `distill` troca o valor
#: digitado por `{saida_<nome>}` e a receita reproduzida digita o valor DESTA execução, não o de quem aprendeu.
PREFIXO_DA_VARIAVEL = "saida_"


class LeituraInvalida(ValueError):
    """A chamada de `read_value` não pôde ler o valor (elemento inexistente, trecho fora do texto, tipo que não casa).
    É erro de chamada: o ator tenta de novo, como numa ferramenta rejeitada. Nada disto é recusa da triagem."""


# ------------------------------------------------------------------ referências
def nomes_citados(texto: str | None) -> list[str]:
    """Os nomes de saída citados num texto, na ordem, sem repetição."""
    return list(dict.fromkeys(SAIDA_REF_RE.findall(texto or "")))


def _textos_da_etapa(step: PlanStep) -> list[str]:
    post = step.postcondition
    return [step.title, step.goal, step.precondition or "", post.value, post.description, *step.commit_guard,
            *step.band_guard, *step.bindings.values(), *step.variables.values()]


def referencias(step: PlanStep) -> list[str]:
    """Os nomes que a etapa USA: em qualquer texto dela, nos argumentos e nas variáveis."""
    return list(dict.fromkeys(n for t in _textos_da_etapa(step) for n in nomes_citados(t)))


def como_texto(valor: str, tipo: str) -> str:
    """O valor como entra num texto de etapa. A lista é gravada em JSON (o tipo `list` guarda a estrutura) e entra
    no texto como itens separados por vírgula."""
    if tipo == "list":
        try:
            itens = json.loads(valor)
        except ValueError:
            return valor
        if isinstance(itens, list):
            return ", ".join(str(i) for i in itens)
    return valor


def resolver(texto: str | None, valores: dict[str, str]) -> tuple[str | None, list[str]]:
    """Troca cada `{{saida:<nome>}}` pelo valor (já em forma de texto). Devolve o texto e os nomes que FALTARAM — a
    referência sem valor fica como está: nada é inventado, e quem chamou decide parar."""
    if not texto:
        return texto, []
    faltam: list[str] = []

    def troca(m: re.Match[str]) -> str:
        nome = m.group(1)
        if nome in valores:
            return valores[nome]
        faltam.append(nome)
        return m.group(0)

    return SAIDA_REF_RE.sub(troca, texto), list(dict.fromkeys(faltam))


# ------------------------------------------------------------------ for_each: um nome por item
def sufixo_de_item(nome: str, n: int) -> str:
    """`nome` do item `n` de um bloco `for_each` (`nome_i2`): cada cópia do bloco grava a SUA saída — com o nome único
    no objetivo (`UNIQUE(objective_id, name)`), as cópias se sobrescreveriam e o relatório só teria o último item."""
    sufixo = f"_i{n}"
    return nome[:40 - len(sufixo)] + sufixo


def _renomear_texto(texto: str | None, mapa: dict[str, str]) -> str | None:
    if not texto:
        return texto
    return SAIDA_REF_RE.sub(lambda m: "{{saida:" + mapa.get(m.group(1), m.group(1)) + "}}", texto)


def _renomear(step: PlanStep, mapa: dict[str, str]) -> PlanStep:
    post = step.postcondition
    return step.model_copy(update={
        "title": _renomear_texto(step.title, mapa) or "", "goal": _renomear_texto(step.goal, mapa) or "",
        "precondition": _renomear_texto(step.precondition, mapa),
        "postcondition": post.model_copy(update={"value": _renomear_texto(post.value, mapa) or "",
                                                 "description": _renomear_texto(post.description, mapa) or ""}),
        "commit_guard": [_renomear_texto(g, mapa) or "" for g in step.commit_guard],
        "band_guard": [_renomear_texto(g, mapa) or "" for g in step.band_guard],
        "bindings": {k: _renomear_texto(v, mapa) or "" for k, v in step.bindings.items()},
        "variables": {k: _renomear_texto(v, mapa) or "" for k, v in step.variables.items()},
        "saidas": [mapa.get(s, s) for s in step.saidas]})


def renomear_para_o_item(step: PlanStep, nomes_do_bloco: set[str], n: int) -> PlanStep:
    """A cópia `n` de uma etapa de bloco `for_each`: as saídas declaradas DENTRO do bloco (e as referências a elas)
    ganham o sufixo do item. Saída declarada fora do bloco (antes da coleta) segue com o nome de sempre."""
    mapa = {nome: sufixo_de_item(nome, n) for nome in nomes_do_bloco}
    if not mapa or not (set(step.saidas) & nomes_do_bloco or set(referencias(step)) & nomes_do_bloco):
        return step
    return _renomear(step, mapa)


def sem_sufixo_de_item(step: PlanStep) -> PlanStep:
    """Para a identidade da etapa (`step_template_hash`): a referência `x_i<N>` da cópia do item N volta a ser `x`,
    senão cada item teria a sua receita e as cópias deixariam de compartilhar a da etapa-modelo."""
    idx = step.variables.get("item_index")
    if not idx:
        return step
    sufixo = f"_i{idx}"
    mapa = {n: n[:-len(sufixo)] for n in [*referencias(step), *step.saidas] if n.endswith(sufixo)}
    return _renomear(step, mapa) if mapa else step


# ------------------------------------------------------------------ leitura (fato da tela)
def limpar(texto: str) -> str:
    """Valor lido é DADO: sem chaves de molde nem caracteres de controle, espaços colapsados. Sem as chaves, um valor
    lido nunca vira referência (`{{saida:…}}`) nem variável (`{item}`) na próxima resolução."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f{}]", " ", texto or "")).strip()


def texto_do_elemento(el: UiElement) -> str:
    return limpar(el.text or el.desc or "")


def _dentro(interno: tuple[int, int, int, int], externo: tuple[int, int, int, int]) -> bool:
    return (interno[0] >= externo[0] and interno[1] >= externo[1] and interno[2] <= externo[2]
            and interno[3] <= externo[3])


_NUMERO = re.compile(r"[+-]?\d[\d.,\s]*(?:\s?(?:k|m|b|mil|mi|bi|mm))?", re.IGNORECASE)


def conferir_tipo(valor: str, tipo: str) -> str | None:
    """O valor tem a forma do tipo declarado? `None` se tem; senão, o porquê (vai para o ator e para `actions.error`).

    O porquê NUNCA cita o valor: ele ainda não passou pela triagem — um código de verificação lido com o tipo errado
    iria parar no registro da ação e no evento `action.logged` (D3)."""
    if tipo not in SAIDA_VALUE_KINDS:
        return f"tipo de saída desconhecido (use {', '.join(SAIDA_VALUE_KINDS)})"
    if tipo == "number" and not _NUMERO.fullmatch(valor):
        return "o texto lido não é um número (confira o elemento, ou recorte o número em 'value')"
    if tipo == "url" and not url_abrivel(valor):
        return "o texto lido não é um endereço http/https (confira o elemento, ou recorte o endereço em 'value')"
    return None


def ler_valor(arvore: UiTree, *, element_id: str, trecho: str | None, tipo: str) -> tuple[str, list[str], UiElement]:
    """Lê o valor na árvore: (valor gravável, partes lidas, elemento). As partes são o que a triagem confere — o
    próprio valor, ou cada item de uma lista.

    - `text`/`number`/`url`: o texto do elemento (ou a descrição, quando ele não tem texto). Com `trecho`, só aquele
      pedaço, e ele PRECISA estar no texto do elemento: é o que impede o modelo de "ler" um valor que a tela não tem.
    - `list`: os textos dos elementos DENTRO do contêiner, na ordem da tela, sem repetição; gravada em JSON.
    """
    # As mensagens de `LeituraInvalida` vão para `actions.error` e para o evento da ação ANTES da triagem: nenhuma cita
    # o texto da tela nem o que o modelo escreveu (`value`, e o `element_id` que não existe) — só o id que a tela tem.
    el = arvore.by_id(element_id)
    if el is None:
        raise LeituraInvalida("o element_id informado não existe na observação atual")
    if tipo == "list":
        if trecho:
            raise LeituraInvalida("numa lista, informe só o contêiner (element_id), sem 'value'")
        itens: list[str] = []
        for e in arvore.elements:
            if e.id == el.id or not _dentro(e.bounds, el.bounds) or e.password:
                continue
            t = texto_do_elemento(e)
            if t and t not in itens:
                itens.append(t)
        if not itens:
            raise LeituraInvalida(f"o contêiner {element_id} não tem nenhum texto dentro")
        valor = json.dumps(itens, ensure_ascii=False)
        if len(valor) > SAIDA_VALOR_MAX:
            raise LeituraInvalida(f"a lista passa de {SAIDA_VALOR_MAX} caracteres")
        return valor, itens, el
    fonte = texto_do_elemento(el)
    if not fonte:
        raise LeituraInvalida(f"o elemento {element_id} não tem texto nem descrição para ler")
    if trecho:
        pedaco = limpar(trecho)
        i = fonte.casefold().find(pedaco.casefold()) if pedaco else -1
        if i < 0:
            raise LeituraInvalida(f"o trecho informado em 'value' não está no texto do elemento {element_id} — o "
                                  "valor precisa ser lido da tela, não escrito")
        fonte = fonte[i:i + len(pedaco)]            # com a caixa da tela, não a do modelo
    if len(fonte) > SAIDA_VALOR_MAX:
        raise LeituraInvalida(f"o valor passa de {SAIDA_VALOR_MAX} caracteres; informe o trecho em 'value'")
    if (erro := conferir_tipo(fonte, tipo)) is not None:
        raise LeituraInvalida(erro)
    return fonte, [fonte], el


# ------------------------------------------------------------------ triagem (D3)
#: Um código de verificação é curto e só de dígitos (espaço, hífen e ponto separando os grupos não mudam isso).
_DIGITOS_DE_CODIGO = re.compile(r"[0-9]{4,8}")
#: Símbolo fora do alfabeto de usuário, e-mail e endereço: numa palavra única que mistura letras e dígitos, é a cara
#: de uma senha — `Joao_Silva2024` e `fulano@outlook.com` passam; `Xk9#pq2L` não.
_SIMBOLO_DE_SENHA = re.compile(r"[^\w\s.@+\-:/]")


def triagem(valor: str, *, do_elemento: str = "", da_tela: str = "", campo_de_senha: bool = False) -> str | None:
    """Por que o valor NÃO pode ser saída: "senha", "código de verificação" ou "token ou segredo"; `None` = dado comum.

    Por formato, nunca por uma lista de valores (a mesma regra de `security/redaction.py`), e conservadora de
    propósito: recusar um dado inofensivo para a etapa com o motivo, levar um código de um app a outro é o que o
    ADR-009 veda. O contexto conta: "482913" sozinho é número; na tela que fala em código de verificação, é o código.

    `do_elemento` e `da_tela` são os textos de onde o valor saiu (o contexto).

    - campo de senha → senha;
    - o próprio valor fala de código, 2FA ou verificação (o detector de `automation/hierarchy.py`) → código;
    - formato de segredo (`looks_secret`: JWT, chave, blob base64, "code: 1234", par chave=valor) → token. Vale para
      endereço: um link com trecho aleatório de 32+ caracteres (link mágico de entrada, id opaco) é recusado — o
      link que entra numa conta é credencial;
    - só dígitos (4 a 8) num elemento ou tela que fala de código ou credencial → código;
    - palavra única com cara de senha (`parece_senha_ou_codigo`) com símbolo, ou num contexto que fala de credencial
      → senha.
    """
    if campo_de_senha:
        return "senha"
    v = (valor or "").strip()
    if not v:
        return None
    if (trava := detectar_trava_generica(normalizar_texto_de_tela(v), tem_onde_digitar=True)) is not None:
        return "código de verificação" if trava.subtipo == SUBTIPO_CODIGO else "verificação da conta"
    if looks_secret(v):
        return "token ou segredo"
    contexto_da_tela = normalizar_texto_de_tela(f"{do_elemento} {da_tela}")
    fala_de_codigo = (detectar_trava_generica(contexto_da_tela, tem_onde_digitar=True) is not None
                      or mentions_credential(do_elemento))
    compacto = re.sub(r"[\s.\-]", "", v)
    if _DIGITOS_DE_CODIGO.fullmatch(compacto) and fala_de_codigo:
        return "código de verificação"
    # Só dígitos já foram decididos acima (o contexto de TELA inteira não basta para chamar um número de senha: o
    # menu "Senha" de uma tela qualquer recusaria toda contagem de seguidores). Endereço também não é senha — `?`, `=`
    # e `&` são dele; o segredo num endereço (`token=`, usuário:senha@, trecho aleatório longo) já caiu acima.
    if not compacto.isdigit() and not url_abrivel(v) and parece_senha_ou_codigo(v) and (
            _SIMBOLO_DE_SENHA.search(v) or fala_de_codigo or mentions_credential(da_tela)):
        return "senha"
    return None


def texto_da_tela(arvore: UiTree) -> str:
    """Os textos visíveis numa linha, para o contexto da triagem."""
    return " ".join(arvore.texts())


def variaveis_da_receita(saidas: dict[str, tuple[str, str]], item_index: str | None) -> dict[str, str]:
    """As saídas do objetivo como variáveis de receita (`saida_<nome>`). A do item desta cópia de `for_each` perde o
    sufixo (`x_i2` → `saida_x`): a receita aprendida no item 1 reproduz no item 2 com o valor do item 2. As saídas de
    OUTROS itens ficam de fora — com o mesmo nome base, trocariam o valor certo pelo de outro item."""
    out: dict[str, str] = {}
    sufixo = f"_i{item_index}" if item_index else None
    for nome, (valor, tipo) in saidas.items():
        base = nome
        if sufixo is not None and nome.endswith(sufixo):
            base = nome[:-len(sufixo)]
        elif sufixo is not None and re.search(r"_i\d+$", nome):
            continue                                    # a saída de outro item deste bloco
        out[PREFIXO_DA_VARIAVEL + base] = como_texto(valor, tipo)
    return out


def args_sem_valor(args: dict[str, object]) -> dict[str, object]:
    """Os argumentos de uma leitura recusada, sem o valor: o que a triagem recusou não vai para `actions.args`."""
    return {k: ("**RECUSADO**" if k == "value" and v else v) for k, v in args.items() if k != "rationale"}


_NOME_DE_SAIDA = re.compile(r"[a-z][a-z0-9_]{0,39}")


def args_da_chamada_invalida(args: dict[str, object], arvore: UiTree) -> dict[str, object]:
    """Os argumentos de um `read_value` que nem chegou à triagem (erro de chamada), no que se pode gravar.

    Nada ali foi triado, e o modelo escreve o que quiser em qualquer campo: o recorte (`value`) sai; o nome fica só
    com a forma de nome de saída, e o `element_id` só quando é um id da tela — um código colado no lugar do nome ou do
    id não vai para `actions.args` nem para o evento."""
    out: dict[str, object] = {"value_kind": args.get("value_kind")}
    nome = str(args.get("name") or "")
    out["name"] = nome if _NOME_DE_SAIDA.fullmatch(nome) else "**OMITIDO**"
    eid = str(args.get("element_id") or "")
    out["element_id"] = eid if eid and arvore.by_id(eid) is not None else "**OMITIDO**"
    if args.get("value"):
        out["value"] = "**OMITIDO**"
    return out
