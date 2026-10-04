"""As perguntas abertas de uma execução em `needs_input` e a pergunta que pede credencial (29.52).

Caminho comum do painel e dos canais. A resposta a uma pergunta vira comando de uma execução sucessora: vai a
`runs.command`, ao prompt do planejador e ao histórico. Uma senha ou um código respondidos ali sairiam do lugar
certo (ADR-040: a senha mora na conta da persona e a automação a digita por `type_secret`; ADR-009: o código de
verificação a pessoa digita no aparelho). Por isso a pergunta que PEDE credencial não se responde por texto, e a
resposta com cara de credencial é recusada mesmo sem saber a pergunta. Na dúvida, recusa.

As duas leituras públicas (`pergunta_sensivel_da_execucao`, `pergunta_sensivel_aberta`) são as que os canais usam
(Telegram, Trello): o vocabulário é um só, o da `TriagemDeCredencial`. Nenhuma delas devolve nem registra o texto da
pergunta ou da resposta, só o TIPO da credencial pedida.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping

from ..db import Database, loads
from ..models import RunStatus
from ..modules.learning.infrastructure.segredo import TriagemDeCredencial

#: Perguntas que são escolha de alvo, não texto: o assistente não as responde (ver `taskqueue/assistente.py`).
CAMPOS_DE_DESTINO = frozenset({"profile_id", "instance_id"})

#: A fonte única da regra (29.52): pergunta sensível e resposta recusada.
TRIAGEM = TriagemDeCredencial()

MENSAGEM_SENHA = ("Esta pergunta pede uma senha, e senha não vai na resposta: ela iria ao provedor de IA e ficaria no "
                  "histórico. Guarde-a na conta da persona (Personas → a pessoa → “Contas e acesso”), com o "
                  "consentimento, e faça o pedido de novo: a automação a digita de lá sem passar pela IA.")
MENSAGEM_CODIGO = ("Esta pergunta pede um código de verificação, e o código não vai na resposta: ele iria ao provedor "
                   "de IA e ficaria no histórico. Digite-o você mesmo no aparelho, pelo controle manual (Aparelhos → o "
                   "aparelho → “Assumir controle”), e depois faça o pedido de novo.")
MENSAGEM_FORMATO = ("A resposta parece uma senha ou um código e não vira comando nem vai à IA. A senha fica na conta "
                    "da persona (Personas → a pessoa → “Contas e acesso”); o código de verificação você digita no "
                    "aparelho, pelo controle manual.")
#: O `tipo` da recusa quando ela vem do formato da resposta, sem pergunta sensível.
TIPO_FORMATO = "formato"


def perguntas_abertas(db: Database, run: Mapping[str, object]) -> list[dict[str, object]]:
    """As perguntas abertas de uma execução em `needs_input`: as do plano (`missing`) ou, sem plano, as do evento
    mais recente com `data.questions` — o mesmo critério do painel (`perguntasDosEventos`)."""
    plano = loads(str(run["plan"]), None) if run["plan"] else None
    if isinstance(plano, dict) and plano.get("missing"):
        return [{"field": m.get("field") or "", "question": m.get("question") or "", "options": []}
                for m in plano["missing"] if isinstance(m, dict)]
    for ev in db.query("SELECT data FROM events WHERE run_id=? AND data IS NOT NULL ORDER BY id DESC", (run["id"],)):
        d = loads(str(ev["data"]), None)
        if isinstance(d, dict) and isinstance(d.get("questions"), list) and d["questions"]:
            return [q for q in d["questions"] if isinstance(q, dict)]
    return []


def tipo_sensivel(perguntas: Iterable[Mapping[str, object]]) -> str | None:
    """O tipo da primeira pergunta que pede credencial (`senha`, `2fa`, `codigo`, `token`, `credencial`), ou None. As
    de destino ficam fora: resolvem-se escolhendo alvos."""
    for q in perguntas:
        campo = str(q.get("field") or "")
        if campo in CAMPOS_DE_DESTINO:
            continue
        tipo = TRIAGEM.pergunta_sensivel(str(q.get("question") or ""), campo)
        if tipo is not None:
            return tipo
    return None


def pergunta_sensivel_da_execucao(db: Database, run_id: str) -> str | None:
    """Leitura pública (1): a pergunta aberta DESTA execução pede credencial? Devolve o tipo, ou None (execução que não
    existe ou que não espera resposta também é None: não há pergunta aberta)."""
    run = db.one("SELECT * FROM runs WHERE id=?", (run_id,))
    if run is None or run["status"] != RunStatus.needs_input.value:
        return None
    return tipo_sensivel(perguntas_abertas(db, run))


def pergunta_sensivel_aberta(db: Database, instance_ids: Iterable[str] = ()) -> str | None:
    """Leitura pública (2): existe AGORA alguma pergunta aberta que pede credencial? Para o texto livre de um canal (a
    senha mandada sem responder à pergunta) e para a caixa de novo pedido do painel. Com `instance_ids`, só as
    execuções desses aparelhos contam, mais as que ainda não têm aparelho (na dúvida, contam). As execuções em
    `needs_input` expiram em `execucao.pergunta_vence_h` horas (padrão `NEEDS_INPUT_EXPIRA_H`), o que limita a varredura."""
    filtro = set(instance_ids)
    for run in db.query("SELECT * FROM runs WHERE status=? ORDER BY created_at DESC", (RunStatus.needs_input.value,)):
        if filtro:
            aparelhos = {str(i) for i in (loads(str(run["instance_ids"]), None) or [])}
            if aparelhos and not aparelhos & filtro:
                continue
        tipo = tipo_sensivel(perguntas_abertas(db, run))
        if tipo is not None:
            return tipo
    return None


def mensagem_da_recusa(tipo: str) -> str:
    """O texto da recusa `credencial_na_resposta`, pelo tipo: aponta o canal certo, sem repetir a resposta."""
    if tipo in ("codigo", "2fa"):
        return MENSAGEM_CODIGO
    if tipo == TIPO_FORMATO:
        return MENSAGEM_FORMATO
    return MENSAGEM_SENHA


def mensagem_da_palavra_solta(tipo: str) -> str:
    """O texto da recusa de um pedido NOVO de uma palavra só com pergunta sensível aberta (a resposta mandada no lugar
    errado), pelo tipo da pergunta."""
    if tipo in ("codigo", "2fa"):
        espera = "um código de verificação"
        saida = "Digite o código no aparelho, pelo controle manual (Aparelhos → o aparelho → “Assumir controle”)."
    else:
        espera = "uma senha"
        saida = "Guarde a senha na conta da persona (Personas → a pessoa → “Contas e acesso”), com o consentimento."
    return (f"Há uma execução esperando {espera}, e um pedido de uma palavra só parece essa resposta no lugar errado: "
            f"ele iria ao provedor de IA e ficaria no histórico. {saida} Se é mesmo um pedido novo, escreva-o por "
            "extenso.")


def acrescimo(original: str, novo: str) -> str:
    """O que a resposta ACRESCENTOU ao comando. O canal manda `original + "\\n" + resposta`; o painel manda o texto que
    a IA refinou, e aí o acréscimo são as palavras que o original não tinha. Aproximado de propósito: julgar só o
    acréscimo evita recusar o comando original por uma palavra que já estava nele."""
    original, novo = original.strip(), novo.strip()
    if original and novo.startswith(original):
        return novo[len(original):].strip()
    antes = {p.casefold() for p in original.split()}
    return " ".join(p for p in novo.split() if p.casefold() not in antes)
