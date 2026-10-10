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

import hashlib
import json
import re
import unicodedata
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..automation.conhecimento_de_telas import ConhecimentoDeTelas
from ..automation.hierarchy import (SUBTIPO_CODIGO, ContaTravada, UiElement, UiTree, detectar_pedido_de_codigo,
                                    detectar_trava_generica, normalizar_texto_de_tela)
from ..devices.codificacao import recortar_jpeg
from ..models import SAIDA_VALOR_MAX, SAIDA_VALUE_KINDS, PlanStep

if TYPE_CHECKING:
    from ..planning.provider import Transcricao
from ..security.redaction import looks_secret, mentions_credential, parece_senha_ou_codigo, redact
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


class LeituraSemTexto(LeituraInvalida):
    """O elemento existe e não tem texto nem descrição: a ÚNICA falha da árvore que abre o caminho da leitura visual
    (item 12.5). Qualquer outra falha (id inexistente, trecho fora do texto, tipo errado) segue recusa comum."""


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
        raise LeituraSemTexto(f"o elemento {element_id} não tem texto nem descrição para ler")
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

#: Palavras que falam de código de verificação, em inglês, português e espanhol (já SEM acento e em minúsculas: o texto
#: passa por `normalizar_texto_de_tela`). Genéricas de propósito: nenhum nome de app, porque o que identifica o código é a
#: FORMA ("123456 is your … code", "use 482913 to confirm your identity", "é seu código"), não quem o mandou. As frases
#: "is your … code", "é seu código" e "es tu código" se reduzem à palavra `code`/`codigo`.
_PALAVRAS_DE_CODIGO = re.compile(
    r"\b(?:codes?|codigos?|codice|verification|verificacion|verificacao|verify|verifying|verificar|confirm|confirmar|"
    r"confirmacao|confirmacion|identity|identidad|identidade|security|seguridad|seguranca|login|log in|sign.?in|"
    r"entrar|iniciar sesion|ingresar|otp|2fa|passcode|one.?time|pin)\b")
#: Uma sequência de dígitos que pode ser um código, com UM separador entre dígitos (espaço, hífen, ponto, vírgula, ponto médio,
#: sublinhado, barra, espaço de largura zero). Sem dígito nem `:` colado antes (hora). O texto já vem canônico (`_canonico`).
_SEP_DE_CODIGO = r"[\s.,·_/\-​-‍⁠﻿]"
_SEQUENCIA_NUMERICA = re.compile(rf"(?<![\d:])\d(?:{_SEP_DE_CODIGO}?\d)*(?!\d)")
_SEPARADOR = re.compile(_SEP_DE_CODIGO)
#: Quantos caracteres separam o número da palavra de código quando o texto é a TELA inteira (várias linhas coladas): a
#: mesma linha, e não o resto da tela. No texto de UM elemento ou de uma linha, vale o texto todo.
_JANELA_DA_TELA = 48
#: Dígitos de um código: 4 a 8 (os de 2FA, e-mail e SMS vão de 4 a 8). Abaixo disso é contagem pequena ou ano; acima, é
#: telefone, cartão, id. É o mesmo limiar de `_DIGITOS_DE_CODIGO`.
_CODIGO_MIN, _CODIGO_MAX = 4, 8
#: Código alfanumérico curto ("G-482913", "ABC123"): sem espaço, de 4 a 10 caracteres, com letra e pelo menos 3 dígitos. É o
#: limiar da leitura visual (`forma_de_codigo`): palavra sem dígito, data ("12/10") e hora ("14h30", "14:30") não casam.
_TOKEN_CURTO = re.compile(r"\S{4,10}")
_HORA = re.compile(r"[0-9]{1,2}[h:][0-9]{2}(?:min)?|[0-9]{1,2}h")
_TOKEN_ALFANUMERICO_DIGITOS_MIN = 3


def _canonico(texto: str | None) -> str:
    """O texto como ele é LIDO: NFKC (largura total vira ASCII) e os dígitos de outros alfabetos ("٤٨٢٩١٣") em ASCII. Sem isto,
    "４８２９１３" e "٤٨٢٩١٣" passariam por texto comum ao lado de um código."""
    t = unicodedata.normalize("NFKC", texto or "")
    return "".join(str(d) if not c.isascii() and c.isdigit() and (d := unicodedata.digit(c, None)) is not None else c
                   for c in t)


def _data_plausivel(a: int, b: int) -> bool:
    return (1 <= a <= 31 and 1 <= b <= 12) or (1 <= a <= 12 and 1 <= b <= 31)


def _eh_data(grupos: list[str]) -> bool:
    """Os grupos de dígitos têm a forma de uma data ("12/10", "14.10.2026", "2026-10-02")? Dia e mês plausíveis."""
    n, tam = [int(g) for g in grupos], [len(g) for g in grupos]
    if len(grupos) == 2:
        return max(tam) <= 2 and _data_plausivel(n[0], n[1])
    if len(grupos) == 3:
        if tam[0] == 4 and 1900 <= n[0] <= 2100 and tam[1] <= 2 and tam[2] <= 2:
            return _data_plausivel(n[1], n[2])
        if tam[0] <= 2 and tam[1] <= 2 and tam[2] in (2, 4):
            return _data_plausivel(n[0], n[1])
    return False


def _numeros_de_codigo(texto: str) -> list[tuple[int, int, str]]:
    """Posição e dígitos de cada número de 4 a 8 dígitos do texto já canônico e normalizado, com separador entre grupos IGUAIS
    ("123 456", "1234-5678", "482.913"). Data ("2026-10-02", "12/10"), telefone ("555-1234"), decimal ("12.345") e hora ficam
    de fora: os grupos de um código têm o mesmo tamanho; os dessas coisas, em geral, não."""
    achados: list[tuple[int, int, str]] = []
    for m in _SEQUENCIA_NUMERICA.finditer(texto):
        bruto = m.group(0)
        grupos = _SEPARADOR.split(bruto)
        if len(grupos) == 1 or (len({len(g) for g in grupos}) == 1 and 2 <= len(grupos[0]) <= 4
                                and not _eh_data(grupos)):
            digitos = "".join(grupos)
            if _CODIGO_MIN <= len(digitos) <= _CODIGO_MAX:
                achados.append((m.start(), m.end(), digitos))
            continue
        if not re.fullmatch(r"[0-9 ]+", bruto):
            continue                                    # outro separador com grupos desiguais: data, telefone, decimal
        pos = m.start()
        for g in bruto.split(" "):                      # "123456 2 minutos": o código vem antes do resto
            if _CODIGO_MIN <= len(g) <= _CODIGO_MAX:
                achados.append((pos, pos + len(g), g))
            pos += len(g) + 1
    return achados


def forma_de_codigo(texto: str | None) -> bool:
    """O texto INTEIRO (um valor, uma linha do recorte) tem FORMA de código, sem precisar de palavra de contexto?

    1. só número, de 4 a 8 dígitos, com ou sem separadores ("482913", "482 913", "482.913", "482·913"), em qualquer alfabeto de
       dígitos; fora as datas plausíveis ("12/10", "02/10/26") — um número com separador de milhar ("1.234") também é recusado;
    2. token curto alfanumérico (4 a 10 caracteres, sem espaço, com letra e ao menos 3 dígitos: "G-482913", "ABC123"), fora a
       hora ("14h30"). Username curto com ano ("joao2024") é recusado também: o limiar é conservador, de propósito.

    É o teste da leitura visual, onde só se tem a linha (a árvore vê a tela inteira e acha o contexto)."""
    t = _canonico(texto).strip()
    if not t:
        return False
    if re.fullmatch(rf"\d(?:{_SEP_DE_CODIGO}?\d)*", t):
        grupos = _SEPARADOR.split(t)
        return _CODIGO_MIN <= len("".join(grupos)) <= _CODIGO_MAX and not _eh_data(grupos)
    return _token_alfanumerico(t)


def _token_alfanumerico(token: str) -> bool:
    """Token curto (4 a 10 caracteres, sem espaço) com letra e ao menos 3 dígitos, fora a hora: "G-482913", "ABC123"."""
    if not _TOKEN_CURTO.fullmatch(token) or not any(c.isalpha() for c in token) or _HORA.fullmatch(token.casefold()):
        return False
    return sum(c.isdigit() for c in token) >= _TOKEN_ALFANUMERICO_DIGITOS_MIN


def codigo_na_linha(texto: str | None, *, so_digitos: str | None = None, janela: int | None = None) -> bool:
    """O texto (um elemento, uma linha, um valor) tem um número de 4 a 8 dígitos (ou um código alfanumérico curto) E fala de
    código ou verificação?

    Generoso de propósito (ADR-009: levar um código de um app a outro é o que se veda; recusar um dado comum custa uma
    parada com o motivo). Sem palavra de código, "Reunião às 14h do dia 12345" passa; com a palavra e um número fora de
    4 a 8 dígitos ("login 123 vezes", "verificação de 123456789 itens") também. `so_digitos` restringe ao número que for
    exatamente este (o valor que o ator leu); `janela` (caracteres) pede a palavra PERTO do número: é o texto da tela
    inteira, onde um menu "Entrar" não torna código a contagem de seguidores do outro lado."""
    t = normalizar_texto_de_tela(_canonico(texto))
    palavras = [m.span() for m in _PALAVRAS_DE_CODIGO.finditer(t)]
    if not palavras:
        return False
    for ini, fim, digitos in _numeros_de_codigo(t):
        if so_digitos is not None and digitos != so_digitos:
            continue
        if janela is None or any(a - fim <= janela and ini - b <= janela for a, b in palavras):
            return True
    if so_digitos is not None:
        return False
    # Código alfanumérico curto na mesma linha da palavra de código ("Your code is ABC123", "G-482913 is your … code").
    for m in re.finditer(r"\S+", t):
        if _token_alfanumerico(m.group(0).strip(".,;:!?()[]\"'")) and (
                janela is None or any(a - m.end() <= janela and m.start() - b <= janela for a, b in palavras)):
            return True
    return False


def _trava_na_triagem(texto_normalizado: str, conteudo_de_terceiros: bool) -> ContaTravada | None:
    """31.328: a frase de verificação do texto, para a triagem. Em conteúdo de terceiros (a linha de uma caixa de e-mail) a frase
    de verificação HUMANA ("Confirm you're human") é o texto da mensagem e não vale como desafio. Continuam valendo: o pedido de
    CÓDIGO, procurado à parte (o detector devolve a conta travada primeiro quando as duas famílias casam e o esconderia), e um
    número de 4 a 8 dígitos junto da frase de desafio ("Confirm it's you - 482913"): na dúvida, a parada de sempre."""
    trava = detectar_trava_generica(texto_normalizado, tem_onde_digitar=True)
    if trava is None or not conteudo_de_terceiros or trava.subtipo == SUBTIPO_CODIGO:
        return trava
    if (pedido := detectar_pedido_de_codigo(texto_normalizado)) is not None:
        return pedido
    return trava if _numeros_de_codigo(texto_normalizado) else None


def triagem(valor: str, *, do_elemento: str = "", da_tela: str = "", campo_de_senha: bool = False,
            conteudo_de_terceiros: bool = False) -> str | None:
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
    - o valor tem um número de 4 a 8 dígitos e fala de código ou verificação na mesma linha (`codigo_na_linha`: "123456 is
      your … code", "use 482913 to confirm your identity") → código;
    - só dígitos (4 a 8) num elemento ou tela que fala de código ou credencial, ou cujo número aparece com a palavra de
      código na linha de origem (o elemento inteiro; na tela, a palavra a até `_JANELA_DA_TELA` caracteres) → código;
    - palavra única com cara de senha (`parece_senha_ou_codigo`) com símbolo, ou num contexto que fala de credencial
      → senha.
    """
    if campo_de_senha:
        return "senha"
    v = (valor or "").strip()
    if not v:
        return None
    if (trava := _trava_na_triagem(normalizar_texto_de_tela(v), conteudo_de_terceiros)) is not None:
        return "código de verificação" if trava.subtipo == SUBTIPO_CODIGO else "verificação da conta"
    if looks_secret(v):
        return "token ou segredo"
    # O valor que traz o número E a palavra de código na mesma linha ("123456 is your … code"): é o código.
    if codigo_na_linha(v):
        return "código de verificação"
    contexto_da_tela = normalizar_texto_de_tela(f"{do_elemento} {da_tela}")
    fala_de_codigo = (detectar_trava_generica(contexto_da_tela, tem_onde_digitar=True) is not None
                      or mentions_credential(do_elemento))
    compacto = _SEPARADOR.sub("", _canonico(v))
    if _DIGITOS_DE_CODIGO.fullmatch(compacto) and (
            fala_de_codigo or codigo_na_linha(do_elemento, so_digitos=compacto)
            or codigo_na_linha(da_tela, so_digitos=compacto, janela=_JANELA_DA_TELA)):
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


def variaveis_da_receita(saidas: dict[str, tuple[str, str]], item_index: str | None,
                         visuais: Collection[str] = ()) -> dict[str, str]:
    """As saídas do objetivo como variáveis de receita (`saida_<nome>`). A do item desta cópia de `for_each` perde o
    sufixo (`x_i2` → `saida_x`): a receita aprendida no item 1 reproduz no item 2 com o valor do item 2. As saídas de
    OUTROS itens ficam de fora — com o mesmo nome base, trocariam o valor certo pelo de outro item.

    `visuais` são os nomes lidos da IMAGEM (`origem='visual'`, item 12.5): ficam de fora. O valor que só um segundo leitor
    confirmou não pode chegar a uma reprodução COM efeito sem passar pela pessoa, e a receita reproduz sem perguntar."""
    out: dict[str, str] = {}
    sufixo = f"_i{item_index}" if item_index else None
    for nome, (valor, tipo) in saidas.items():
        if nome in visuais:
            continue
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


# ------------------------------------------------------------------ leitura visual (item 12.5, ADR-070)
#: Vocabulário FECHADO das recusas da leitura visual, na ordem das barreiras (das baratas para a cara). O ator recebe
#: SÓ o código: nem a transcrição do leitor, nem o motivo em texto livre — com eco, uma nova tentativa faria dos dois um
#: leitor só. `triagem` sai como `triagem:<motivo>`, o motivo sendo o do vocabulário de `triagem()`. `leitor_falhou` é o
#: leitor indisponível (erro do provedor), recusa como as outras.
RECUSAS_VISUAIS = ("desligado", "elemento_com_texto", "regiao_nao_declarada", "arvore_truncada", "tela_sensivel",
                   "fora_do_app", "sem_ancora", "captura_mudou", "repetida", "sem_leitor", "leitor_falhou", "ilegivel",
                   "truncado", "nao_confere", "triagem")


#: As recusas da conferência (barreiras 9 a 11) que reler a MESMA âncora na MESMA tela não muda: o leitor já viu aquele
#: recorte e disse não (29.49). `leitor_falhou` e `sem_leitor` ficam de fora: a falha é do provedor, não do recorte.
RECUSAS_DETERMINISTICAS = frozenset({"ilegivel", "truncado", "nao_confere"})


class LeituraVisualRecusada(Exception):
    """A leitura visual foi recusada. NUNCA grava valor. `codigo` é de `RECUSAS_VISUAIS`; `motivo` só existe na triagem;
    `anterior` só em `repetida`: a recusa da primeira leitura daquele par (tela, âncora), quando houve uma (29.49)."""

    def __init__(self, codigo: str, motivo: str | None = None, *, anterior: str | None = None):
        if codigo not in RECUSAS_VISUAIS:
            raise ValueError(f"código de recusa fora do vocabulário: {codigo!r}")
        super().__init__(f"{codigo}:{motivo}" if motivo else codigo)
        self.codigo = codigo
        self.motivo = motivo
        self.anterior = anterior

    @property
    def rotulo(self) -> str:
        """O que vai ao ator, à ação e ao evento: o código, e na triagem `triagem:<motivo>`."""
        return f"{self.codigo}:{self.motivo}" if self.motivo else self.codigo

    @property
    def definitiva(self) -> bool:
        """`repetida` depois de uma recusa determinística (29.49): o ator insistiu na leitura que o leitor já recusou, na
        mesma tela. Repetir de novo, ou numa nova tentativa da etapa, daria o mesmo não; a etapa termina como não lida."""
        return self.codigo == "repetida" and self.anterior in RECUSAS_DETERMINISTICAS


@dataclass(frozen=True, slots=True)
class LeituraVisual:
    """Uma leitura visual VÁLIDA: o valor (do ator, conferido), o recorte que o leitor viu e o sha256 dele."""

    valor: str
    recorte: bytes
    sha256: str
    alvo: UiElement


#: A imagem para o recorte, buscada só na barreira 6: (árvore, JPEG, largura e altura do aparelho), ou `None` se não veio.
ObterImagem = Callable[[], Awaitable[tuple[UiTree, bytes | None, int, int] | None]]
Transcrever = Callable[[bytes, dict[str, str]], Awaitable["Transcricao"]]
#: A chave de "já houve uma tentativa visual": (nome, assinatura da árvore, limites da âncora).
ChaveDeTentativa = tuple[str, str, tuple[int, int, int, int]]


def descendentes_com_texto(arvore: UiTree, ancora: UiElement) -> bool:
    """Algum elemento DENTRO dos limites da âncora (ela mesma incluída) tem texto ou descrição? Se tem, a árvore não é
    cega ali e a leitura vai pelo texto (ou recusa): a visual só existe onde a árvore provou não ter nada."""
    return any((e.text or e.desc) for e in arvore.elements if e.id == ancora.id or _dentro(e.bounds, ancora.bounds))


_PONTAS = " .,;:!?\"'“”‘’«»()[]"


def _palavras(texto: str) -> list[str]:
    """NFKC, caixa e espaços colapsados, pontuação e aspas das PONTAS fora, ACENTOS MANTIDOS ("mãe" ≠ "mae")."""
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", texto or "").casefold()).strip().strip(_PONTAS)
    return t.split(" ") if t else []


def _termina_cortado(texto: str) -> bool:
    return (texto or "").rstrip().endswith(("…", "..."))


def conferir_transcricao(valor: str, nome: str, t: Transcricao) -> None:
    """Barreiras 9 a 11: o leitor leu, não é truncado e CONCORDA com o ator. Levanta `LeituraVisualRecusada`.

    A concordância exige as duas coisas: o valor do ator, normalizado, é igual ao `campos[nome]` do leitor, normalizado
    (a regra da proposta verdade), E ele é uma sequência contígua de PALAVRAS INTEIRAS de uma das `linhas` (o acréscimo do
    juiz: contra um campo que o leitor inventou sem estar escrito na imagem).

    "Truncado" (nível 1.1, emenda de 03/10 ao ADR-070 §4): vale só para o valor do ator, o campo do leitor e a linha que
    contém o valor. A prévia do corpo na linha da caixa do Outlook SEMPRE termina em "…" (K-079), e recusar por ela
    derrubava toda leitura. A marca global `t.truncado` não diz QUAL texto está cortado: ela só é posta de lado quando
    uma linha alheia cortada a explica; sem nenhuma, o corte pode ser o do campo e a leitura é recusada.

    29.49 (recaída do K-079): a prévia pode REPETIR o assunto ("Olá! <assunto> é…"), e o valor aparece em duas linhas, a
    dele (inteira) e a prévia (cortada). Uma cópia numa linha INTEIRA prova que o valor não foi cortado: a linha do valor
    só derruba a leitura quando TODAS as linhas que o contêm terminam cortadas, e as cópias cortadas contam como linha
    alheia para explicar a marca global. O valor e o campo cortados continuam recusando."""
    if not t.legivel:
        raise LeituraVisualRecusada("ilegivel")
    campo = t.campos.get(nome)
    do_ator = _palavras(valor)
    n = len(do_ator)
    com_valor = {i for i, p in enumerate(_palavras(linha) for linha in t.linhas)
                 if n and any(p[j:j + n] == do_ator for j in range(len(p) - n + 1))}
    cortadas = {i for i, x in enumerate(t.linhas) if _termina_cortado(x)}
    inteiras_com_valor = com_valor - cortadas
    alheia_cortada = bool(cortadas - inteiras_com_valor)
    if (_termina_cortado(valor) or _termina_cortado(campo or "")
            or (com_valor and not inteiras_com_valor)
            or (t.truncado and not alheia_cortada)):
        raise LeituraVisualRecusada("truncado")
    if not do_ator or campo is None or _palavras(campo) != do_ator:
        raise LeituraVisualRecusada("nao_confere")
    if not com_valor:
        raise LeituraVisualRecusada("nao_confere")


def recortar(jpeg: bytes, largura: int, altura: int, limites: tuple[int, int, int, int]) -> bytes:
    """O recorte da âncora no JPEG da observação (`devices.codificacao.recortar_jpeg`: sem margem, a linha vizinha pode ser
    sensível). Área vazia ou JPEG ilegível → `sem_ancora`."""
    try:
        return recortar_jpeg(jpeg, largura, altura, limites)
    except ValueError as exc:
        raise LeituraVisualRecusada("sem_ancora") from exc


def ancora_sem_limites(arvore: UiTree, element_id: str, largura: int, altura: int) -> bool:
    """A âncora existe mas não tem área positiva dentro da tela (barreira 5)?"""
    a = arvore.by_id(element_id)
    if a is None:
        return True
    x1, y1, x2, y2 = max(0, a.bounds[0]), max(0, a.bounds[1]), min(largura, a.bounds[2]), min(altura, a.bounds[3])
    return x2 <= x1 or y2 <= y1


async def ler_valor_visual(*, habilitado: bool, arvore: UiTree, element_id: str, nome: str, valor_do_ator: str,
                           conhecimento: ConhecimentoDeTelas | None, tela: str | None, image_policy: str,
                           fora_do_app: str | None, largura: int, altura: int, obter_imagem: ObterImagem,
                           tentativas: set[ChaveDeTentativa], transcrever: Transcrever | None,
                           tipo_da_tela: str | None = None,
                           recusas: dict[ChaveDeTentativa, str] | None = None) -> LeituraVisual:
    """Leitura visual de UMA saída, conferida às cegas (item 12.5, ADR-070). Roda as barreiras na ordem, das baratas para a
    cara, e levanta `LeituraVisualRecusada` na primeira que falhar — nunca devolve valor sem a concordância do leitor.

    0 desligado · 1 elemento_com_texto · 2 regiao_nao_declarada · 3 arvore_truncada · 4 tela_sensivel/fora_do_app ·
    5 sem_ancora · 6 captura_mudou · 7 repetida · 8 sem_leitor · 9 ilegivel · 10 truncado · 11 nao_confere · 12 triagem.

    A barreira 1 da especificação ("a árvore tenta primeiro") é do executor: ele só chama aqui depois que `ler_valor`
    falhou com `LeituraSemTexto`. `transcrever` recebe SÓ o recorte e os nomes e descrições das saídas pedidas — o valor do
    ator, o comando e a tela inteira ficam de fora —, e a transcrição nunca sai desta função (só o valor conferido)."""
    if not habilitado:
        raise LeituraVisualRecusada("desligado")
    ancora = arvore.by_id(element_id)
    if ancora is None or descendentes_com_texto(arvore, ancora):
        raise LeituraVisualRecusada("elemento_com_texto")
    regiao = None if conhecimento is None else conhecimento.regiao_visual(tela, arvore, ancora, nome)
    if regiao is None:
        raise LeituraVisualRecusada("regiao_nao_declarada")
    de_terceiros = regiao.conteudo_de_terceiros
    if arvore.truncada:
        raise LeituraVisualRecusada("arvore_truncada")
    # A leitura NÃO serve para ler código de 2FA nem de desafio (ADR-009): tela classificada como desafio ou código, ou com
    # conta travada detectada, não chama o leitor.
    if (arvore.sensitive or image_policy == "never" or arvore.conta_travada is not None
            or tipo_da_tela in ("desafio", "dois_fatores")):
        raise LeituraVisualRecusada("tela_sensivel")
    if fora_do_app is not None:
        raise LeituraVisualRecusada("fora_do_app")
    if ancora_sem_limites(arvore, element_id, largura, altura):
        raise LeituraVisualRecusada("sem_ancora")
    # 6: a imagem e a árvore são da MESMA observação. Sem imagem na observação, uma nova é capturada e a árvore dela
    # precisa ter a mesma assinatura e a âncora os mesmos limites, senão a tela mudou entre as duas leituras.
    obtido = await obter_imagem()
    if obtido is None:
        raise LeituraVisualRecusada("captura_mudou")
    arvore2, jpeg, largura2, altura2 = obtido
    nova = arvore2.by_id(element_id)
    if (jpeg is None or arvore2.sensitive or nova is None or nova.bounds != ancora.bounds
            or arvore2.signature() != arvore.signature()):
        raise LeituraVisualRecusada("captura_mudou")
    # A chave não usa o sha do JPEG: o relógio da barra de status muda os bytes, e uma nova captura da mesma tela abriria
    # outra tentativa. Tela e âncora iguais = o mesmo par.
    chave = (nome, arvore.signature(), ancora.bounds)
    if chave in tentativas:
        raise LeituraVisualRecusada("repetida", anterior=(recusas or {}).get(chave))
    tentativas.add(chave)
    if transcrever is None:
        raise LeituraVisualRecusada("sem_leitor")
    recorte = recortar(jpeg, largura2, altura2, ancora.bounds)
    do_ator = limpar(valor_do_ator)
    t = await transcrever(recorte, {nome: nome.replace("_", " ")})
    texto = " ".join(t.linhas)
    # 12 ANTES de 9 a 11: a triagem do RECORTE inteiro roda primeiro. Com código na imagem a leitura vai SEMPRE para a pessoa
    # (como na árvore): se a conferência rodasse antes, o ator receberia `nao_confere` ou `truncado` e tentaria outra âncora
    # sem que a triagem tivesse rodado. O recorte recusado não é guardado (quem chama só o grava quando esta função devolve).
    motivo = _triagem_do_recorte(t.linhas, texto, conteudo_de_terceiros=de_terceiros)
    if motivo is not None:
        raise LeituraVisualRecusada("triagem", motivo)
    try:
        conferir_transcricao(do_ator, nome, t)
    except LeituraVisualRecusada as rec:
        if recusas is not None:
            recusas[chave] = rec.codigo     # 29.49: a releitura deste par vira `repetida` definitiva
        raise
    # Grava-se o valor do LEITOR (o que está na imagem), limpo do mesmo jeito: a concordância é no normalizado (caixa,
    # pontuação das pontas), e gravar o do ator deixaria "FLAVIO PADILHA!" passar por "Flavio Padilha". O ator concordou
    # (acima), então só a forma muda.
    valor = limpar(t.campos[nome] or "")
    motivo = triagem(valor, do_elemento=texto, da_tela=texto, conteudo_de_terceiros=de_terceiros)
    # Na leitura visual o valor com FORMA de código é recusado mesmo sem palavra de contexto: a árvore tem a tela inteira para
    # ver que "482913" está sob "código de verificação"; o recorte de uma linha só tem a linha, e um código que o ator leu
    # sozinho é exatamente o que o ADR-009 veda levar a outra etapa.
    if motivo is None and forma_de_codigo(valor):
        motivo = "código de verificação"
    if motivo is not None:
        raise LeituraVisualRecusada("triagem", motivo)
    return LeituraVisual(valor=valor, recorte=recorte, sha256=hashlib.sha256(recorte).hexdigest(), alvo=ancora)


def _triagem_do_recorte(linhas: list[str], texto: str, *, conteudo_de_terceiros: bool = False) -> str | None:
    """Por que o RECORTE inteiro não pode virar saída: qualquer LINHA com forma de código (`forma_de_codigo`: só número de 4 a 8
    dígitos, com separador ou de outro alfabeto; token alfanumérico curto), com número de código e palavra de código
    (`codigo_na_linha`) ou que a triagem comum recusa, ou a transcrição com sinal de desafio de conta. O código pode estar numa
    linha que NÃO é a do valor (o ator leu o remetente, o código é o assunto), com ou sem palavra de contexto na imagem."""
    for linha in linhas:
        if forma_de_codigo(linha) or codigo_na_linha(linha):
            return "código de verificação"
        if (m := triagem(linha, do_elemento=linha, da_tela=texto, conteudo_de_terceiros=conteudo_de_terceiros)) is not None:
            return m
    if (trava := _trava_na_triagem(normalizar_texto_de_tela(texto), conteudo_de_terceiros)) is not None:
        return "código de verificação" if trava.subtipo == SUBTIPO_CODIGO else "verificação da conta"
    return None


def razao_sem_segredo(texto: str | None) -> str:
    """O motivo de um `step_blocked` (texto do MODELO, que pode citar o que viu: um código de verificação, um segredo), no
    que se pode gravar em `steps.status_detail`, `attempts.error`, na nota da evidência e no evento (item 12.5). Redigido
    (`security.redaction.redact`) e triado: se a triagem acusar, "motivo omitido (triagem: <motivo>)"."""
    limpo = redact(texto or "") or ""
    motivo = triagem(limpo, do_elemento=limpo, da_tela=limpo)
    return limpo if motivo is None else f"motivo omitido (triagem: {motivo})"
