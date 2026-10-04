"""Como cada fonte vira uma decisão registrada (item 28.25, o adaptador). Domínio puro: texto e dicionários entram,
`NovaDecisao` sai; nada de banco nem de relógio.

Duas fontes, e nenhuma delas importa o 28.25 (os três PRs da suíte 30 não dependem um do outro):

(a) **Os eventos de vencimento do 31.43**: `run.updated` (a pergunta sem resposta) e `objective.updated` (o objetivo que
    esperava uma execução já terminada) com `dados.vencimento = {regra, horas, desde}`. O motivo `vencido_sem_resposta`,
    quando vem, tem de ser esse; ausente, o `vencimento` basta. A forma ANTIGA do 29.50 (`dados.expirada`) NÃO é
    lida: é anterior à política e já tem a sua tela.
(b) **As transições do aprendizado feitas pela plataforma**: `learning_transitions` com `decided_by = 'plataforma'` e
    motivo `auto:<regra> v<n> — <fatos>` (com espaço e sem arroba: `x:y@z` parece usuário:senha@host e a triagem de
    credencial o recusa), também quando o motivo vem depois do prefixo `confirmado que fica: ` (a confirmação da fila
    "Revisar"). `decided_by = 'sistema'` (o D1, a repetição) NÃO é decisão da política: não entra.

O texto depois do travessão é de outra frente: passa pelo redator injetado e é cortado antes de virar `fatos`.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from app.shared.decisoes import Escalar, NovaDecisao

#: Quem assina a transição que a política tomou. O `sistema` do D1 é outra coisa (a repetição publica sem pessoa).
PLATAFORMA = "plataforma"
MOTIVO_DO_VENCIMENTO = "vencido_sem_resposta"
#: O prefixo que a confirmação de "Revisar" põe antes do motivo da plataforma (`domain/livro.CONFIRMADO_QUE_FICA`).
PREFIXO_DA_CONFIRMACAO = "confirmado que fica: "
#: O formato acordado com o Aprendizado: no começo do motivo ou logo depois de `: `.
_MOTIVO_AUTO = re.compile(r"(?:^|: )auto:(?P<regra>[a-z_]+) v(?P<versao>\d+)(?: — |$)")
_PAR = re.compile(r"\s*([a-z0-9_]{1,40})\s*[=:]\s*([^,;]{1,80})")

_DO_ITEM = {"licao": "Lição", "receita": "Receita", "fluxo": "Fluxo", "tela": "Tela", "preferencia": "Preferência",
            "habilidade": "Habilidade", "memoria": "Memória"}
#: Os tipos de nome masculino; o resto é feminino. O "(a)" era texto de máquina na tela (28.29, passeio de 04/10).
_MASCULINOS = frozenset({"fluxo"})
_O_QUE_FEZ = {"published": "publicad", "validated": "validad", "disabled": "desligad", "deprecated": "aposentad",
              "candidate": "devolvid"}
#: O tamanho do texto dos fatos (o `MAX_VALOR` do kernel é 80, com a reticência dentro).
MAX_TEXTO = 80


def texto_curto(texto: str, limite: int = MAX_TEXTO) -> str:
    """Uma linha de até `limite` caracteres, cortada na última palavra inteira e com "…" (antes cortava no meio:
    "0 falhas de reprodu"). O texto que já cabe volta igual, então a leitura de uma linha antiga também o passa aqui."""
    t = " ".join(texto.split())
    if len(t) <= limite:
        return t
    corte = t[:limite - 1]
    espaco = corte.rfind(" ")
    return (corte[:espaco] if espaco > limite // 2 else corte).rstrip(" ,;:·-") + "…"


def efeito_do_aprendizado(kind: str, para: str, *, confirmacao: bool) -> str:
    """A frase do que a plataforma fez com o item, com o gênero do tipo ("Receita publicada", "Fluxo confirmado")."""
    nome = _DO_ITEM.get(kind, "Item do aprendizado")
    fim = "o" if kind in _MASCULINOS or kind not in _DO_ITEM else "a"
    if confirmacao:
        feito = f"confirmad{fim}"
    elif para in _O_QUE_FEZ:
        feito = _O_QUE_FEZ[para] + fim + (" à prova" if para == "candidate" else "")
    else:
        feito = f"levad{fim} a {para}"
    return f"{nome} {feito} pela plataforma, sem esperar o dono."


def efeito_legivel(fila: str, efeito: str, fatos: Mapping[str, Escalar]) -> str:
    """O efeito como o painel o mostra. A decisão do aprendizado gravada antes do 28.29 traz "publicado(a)": a frase é
    refeita dos fatos (a confirmação antiga se reconhece pelo próprio texto). As outras filas já são legíveis."""
    if fila != "aprendizado":
        return efeito
    kind, para = fatos.get("kind"), fatos.get("para")
    if not isinstance(kind, str) or not isinstance(para, str):
        return efeito
    confirmacao = fatos.get("confirmacao") is True or "confirmado(a)" in efeito
    return efeito_do_aprendizado(kind, para, confirmacao=confirmacao)


def fatos_legiveis(fatos: Mapping[str, Escalar]) -> dict[str, Escalar]:
    """Os fatos como o painel os mostra: o `texto` cortado na palavra (a linha antiga vinha cortada no meio)."""
    saida = dict(fatos)
    texto = saida.get("texto")
    if isinstance(texto, str) and len(texto) >= MAX_TEXTO and not texto.endswith("…"):
        espaco = texto.rfind(" ")
        saida["texto"] = (texto[:espaco] if espaco > MAX_TEXTO // 2 else texto[:MAX_TEXTO - 1]).rstrip(" ,;:·-") + "…"
    return saida


def _numero(valor: object) -> str:
    if isinstance(valor, float) and valor.is_integer():
        valor = int(valor)
    return str(valor)


def _filho(dados: Mapping[str, object] | None, nome: str) -> Mapping[str, object]:
    v = (dados or {}).get(nome)
    return v if isinstance(v, Mapping) else {}


def _id(valor: object) -> str | None:
    if isinstance(valor, bool) or not isinstance(valor, (str, int)):
        return None
    texto = str(valor).strip()
    return texto or None


# ------------------------------------------------------------------ (a) eventos de vencimento
def decisao_de_evento(kind: str, dados: Mapping[str, object] | None, *, ts: str, run_id: str | None = None,
                      objective_id: str | None = None) -> NovaDecisao | None:
    """A decisão de um evento de vencimento, ou `None` quando o evento não é um. Idempotente pela identidade do FATO
    (`run:<id>:<regra>`), nunca pelo id do evento: o fato se repete em eventos."""
    if kind not in ("run.updated", "objective.updated"):
        return None
    venc = _filho(dados, "vencimento")
    regra = venc.get("regra")
    if not isinstance(regra, str) or not regra.strip():
        return None
    motivo = (dados or {}).get("motivo", venc.get("motivo"))
    if motivo is not None and motivo != MOTIVO_DO_VENCIMENTO:
        return None
    fatos: dict[str, Escalar] = {}
    horas, desde = venc.get("horas"), venc.get("desde")
    if isinstance(horas, (int, float)) and not isinstance(horas, bool):
        fatos["horas"] = int(horas) if float(horas).is_integer() else horas
    if isinstance(desde, str) and desde.strip():
        fatos["desde"] = desde.strip()
    if kind == "run.updated":
        ident = _id(_filho(dados, "run").get("id")) or _id(run_id)
        if ident is None:
            return None
        estado = _filho(dados, "run").get("status")
        fila, origem = "pergunta", f"run:{ident}:{regra.strip()}"
        h = f" havia {_numero(fatos['horas'])} h" if "horas" in fatos else ""
        efeito = f"A pergunta sem resposta{h} foi encerrada."
    else:
        ident = _id(_filho(dados, "objective").get("id")) or _id(objective_id)
        if ident is None:
            return None
        estado = _filho(dados, "objective").get("status")
        fila, origem = "objetivo", f"objetivo:{ident}:{regra.strip()}"
        efeito = "O objetivo que esperava uma execução já terminada foi encerrado."
        # A execução do objetivo, para o painel abrir dali (28.29); a pergunta já é a própria execução.
        execucao = _id(_filho(dados, "objective").get("run_id")) or _id(run_id)
        if execucao is not None:
            fatos["run_id"] = execucao
    if isinstance(estado, str) and estado.strip():
        fatos["estado_final"] = estado.strip()
    return NovaDecisao(fila, ident, origem, regra.strip(), efeito, fatos, ts)


# ------------------------------------------------------------------ (b) transições do aprendizado
def motivo_da_plataforma(reason: str) -> tuple[str, str, str, bool] | None:
    """`(regra, versão, texto dos fatos, é_confirmação)` do motivo `auto:<regra> v<n> — <fatos>`, com ou sem o prefixo da
    confirmação; `None` quando o motivo não é da política."""
    texto = reason.strip()
    m = _MOTIVO_AUTO.search(texto)
    if m is None:
        return None
    # `search` aceita o começo do texto ou `: `; o prefixo da confirmação é o único `: ` que o formato admite antes.
    antes = texto[:m.start()] + (": " if m.start() else "")
    confirmacao = antes.lower() == PREFIXO_DA_CONFIRMACAO
    if m.start() and not confirmacao:
        return None
    return m.group("regra"), m.group("versao"), texto[m.end():].strip(), confirmacao


def fatos_do_texto(texto: str) -> dict[str, Escalar]:
    """`usos=5, taxa=0.8` → `{usos: 5, taxa: 0.8}`. O texto livre (`classe B; app x; 10 a favor`) fica em `texto`,
    curto: não se tenta adivinhar chave de prosa."""
    saida: dict[str, Escalar] = {}
    for m in _PAR.finditer(texto):
        v = m.group(2).strip()
        saida[m.group(1)] = int(v) if re.fullmatch(r"-?\d+", v) else float(v) if re.fullmatch(r"-?\d+\.\d+", v) else v
    if not saida and texto.strip():
        saida["texto"] = texto_curto(texto)
    return saida


def decisao_de_transicao(*, transicao_id: int, item_ref: str, item_kind: str, de: str | None, para: str, reason: str,
                         decided_by: str, decided_at: str, redigir: Callable[[str], str] | None = None
                         ) -> NovaDecisao | None:
    """A decisão de uma linha de `learning_transitions`, ou `None` se ela não é da plataforma."""
    if decided_by != PLATAFORMA:
        return None
    achado = motivo_da_plataforma(reason)
    if achado is None:
        return None
    regra, versao, texto, confirmacao = achado
    if redigir is not None:
        texto = redigir(texto)
    fatos = fatos_do_texto(texto)
    fatos["kind"], fatos["para"] = item_kind, para
    if de:
        fatos["de"] = de
    if confirmacao:
        fatos["confirmacao"] = True
    efeito = efeito_do_aprendizado(item_kind, para, confirmacao=confirmacao)
    return NovaDecisao("aprendizado", item_ref, f"aprendizado:{transicao_id}", f"auto:{regra} v{versao}", efeito,
                       fatos, decided_at)
