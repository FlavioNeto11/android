"""Memória do que o perfil VIU — decisão do dono em 24/09: tudo o que o robô coleta na tela vira memória.

Até aqui a memória só crescia por dois caminhos: o operador ensinando à mão e `learn_from`, que aprende o que a
CONTRAPARTE afirmou numa interação confirmada. Navegação (abrir perfil, publicação, comentários, caixa de
mensagens) não deixava rastro nenhum, e o perfil "esquecia" tudo o que tinha visto.

Aqui a tela em que uma etapa foi COMPROVADA vira um fato de origem `observation`:

- só o conteúdo: rótulos de navegação, botões e campos de escrita ficam de fora (senão cada fato seria "Home ·
  Reels · Search" e o orçamento de memória do prompt afogaria no ruído);
- sem IA nenhuma: é a árvore que o executor já tinha em mãos, então não custa chamada nem crédito;
- procedência explícita: o fato diz "vi na tela", nunca "a pessoa me disse" — e o contexto o marca como dado,
  não instrução, porque o texto é de terceiros;
- tela sensível e aparelho-loja nunca chegam aqui (quem chama confere), e o `remember` ainda recusa qualquer
  coisa que pareça segredo — a senha continua sem caminho para a memória.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import Any

# Rótulos de interface (pt/en) que aparecem em toda tela e não dizem nada sobre o que foi visto.
_RUIDO = {
    "home", "reels", "search", "search and explore", "explore", "profile", "message", "messages", "requests",
    "new message", "back", "voltar", "audio call", "video call", "camera", "gallery", "stickers", "more", "follow",
    "following", "follow back", "requested", "message…", "message...", "see all", "see translation", "add note",
    "your note", "profile picture", "try sharing a song...", "search or ask meta ai",
    "tap and hold to react", "swipe up to turn on disappearing messages", "voice message, press and hold to record",
    "view profile", "like", "liked", "comment", "share", "send", "save", "reply", "options", "close", "cancel",
    "início", "pesquisar", "perfil", "mensagens", "seguir", "seguindo", "curtir", "comentar", "compartilhar",
    "enviar", "salvar", "responder", "fechar", "cancelar", "ver tudo", "carregando…", "loading…",
}
# resource-id (sufixo) de barras de abas e do compositor: são interface, não conteúdo.
_ID_DE_INTERFACE = re.compile(r"(_tab|_button[a-z_]*|composer[a-z_]*|edittext|action_bar_button[a-z_]*|"
                              r"navigation[a-z_]*|toolbar[a-z_]*|search[a-z_]*)$")
_SO_SIMBOLO = re.compile(r"^[\W_]+$", re.UNICODE)
_ESPACOS = re.compile(r"\s+")


def linhas_de_conteudo(elementos: Iterable[Any], *, limite_linhas: int = 24) -> list[str]:
    """Os textos da tela que são CONTEÚDO, em ordem de leitura, sem repetição.

    `desc` que repete `text` sai uma vez só; campo editável, senha, rótulo de navegação e símbolo solto ficam de
    fora. O limite existe porque uma lista rolada pode ter centenas de linhas e o fato precisa caber no prompt.
    """
    vistos: set[str] = set()
    out: list[str] = []
    for e in elementos:
        if getattr(e, "password", False) or getattr(e, "editable", False):
            continue
        rid = (getattr(e, "resource_id", "") or "").rsplit("/", 1)[-1]
        if rid and _ID_DE_INTERFACE.search(rid):
            continue
        for bruto in (getattr(e, "text", "") or "", getattr(e, "desc", "") or ""):
            t = _ESPACOS.sub(" ", bruto).strip()
            chave = t.lower()
            if (len(t) < 2 or chave in vistos or chave in _RUIDO or chave.endswith(" profile picture")
                    or _SO_SIMBOLO.match(t)):
                continue
            vistos.add(chave)
            out.append(t[:200])
            if len(out) >= limite_linhas:
                return out
    return out


def assunto_da_tela(bindings: dict[str, Any] | None, elementos: Sequence[Any], fallback: str) -> str:
    """De quem a tela fala: o @ que a etapa mirou; sem ele, o título da tela; sem título, o app."""
    for chave in ("username", "counterparty", "target"):
        v = str((bindings or {}).get(chave) or "").strip()
        if v:
            return v if v.startswith("@") else f"@{v.lstrip('@')}"
    for e in elementos:
        rid = (getattr(e, "resource_id", "") or "").rsplit("/", 1)[-1]
        if rid in ("action_bar_title", "igds_action_bar_title", "header_title", "title_text_view") and e.text:
            return e.text.strip()[:80]
    return fallback


def fato_observado(*, titulo_da_etapa: str, quando: str, linhas: Sequence[str], itens: Sequence[str] | None = None,
                   limite_chars: int = 900) -> str | None:
    """O texto do fato: onde (a etapa), quando (o dia) e o que estava na tela. `None` se não sobrou conteúdo.

    O dia (e não a hora) entra no texto de propósito: a mesma tela vista duas vezes no mesmo dia vira o MESMO
    fato (o `remember` junta pela impressão digital e soma `occurrences`), em vez de dez linhas iguais.
    """
    partes = list(linhas)
    if itens:
        partes.append("lista lida: " + ", ".join(str(i) for i in itens[:40]))
    if not partes:
        return None
    dia = f"{quando[8:10]}/{quando[5:7]}" if len(quando) >= 10 else quando
    corpo = " · ".join(partes)
    texto = f"Vi na tela em {dia} ({titulo_da_etapa}): {corpo}"
    return texto if len(texto) <= limite_chars else texto[: limite_chars - 1] + "…"


def prefixo_do_dia(quando: str) -> str:
    dia = f"{quando[8:10]}/{quando[5:7]}" if len(quando) >= 10 else quando
    return f"Vi na tela em {dia} ("


def partes_do_fato(texto: str) -> set[str]:
    """O que um fato observado já registrado viu, em partes comparáveis (a última pode ter sido cortada em "…")."""
    corpo = texto.split("): ", 1)[1] if "): " in texto else texto
    return {p.strip().rstrip("…").strip().lower() for p in corpo.split(" · ") if p.strip()}
