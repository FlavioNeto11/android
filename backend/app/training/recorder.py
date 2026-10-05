"""Gravação do modo treinamento (item 13.1).

Enquanto uma sessão está aberta num aparelho, cada entrada manual do Foco (toque, toque longo, arraste, texto, tecla)
e cada "abrir app" viram uma linha em `training_inputs` — com o ELEMENTO que estava sob o dedo (resource-id, texto,
descrição, e quais combinações o identificam sozinhas naquela tela) e umas poucas linhas do conteúdo da tela. É o
que permite à IA, depois, entender "abriu a conversa com a Ana" em vez de "tocou em (540, 812)".

Regras que valem desde a gravação (a senha não tem caminho para cá):
- texto digitado em tela sensível, em campo de senha, ou que parece segredo NÃO é gravado — só `has_text` e o
  tamanho; o elemento de senha também não entra como alvo (`_safe_target` descarta);
- uma sessão por aparelho, e só com o controle na mão da pessoa; devolver o controle encerra a gravação.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..db import dumps, loads
from ..security.redaction import looks_secret, mentions_credential, parece_codigo, parece_senha_ou_codigo
from ..social.observacao import linhas_de_conteudo
from ..util import new_token, now_iso

log = logging.getLogger(__name__)

TITULO_IDS = ("action_bar_title", "igds_action_bar_title", "header_title", "title_text_view", "toolbar_title")


def _parece_segredo(texto: str | None) -> bool:
    """Os filtros de segredo da gravação, juntos: formato de credencial, fala de credencial, senha/código e código solto
    de 4 a 8 dígitos (com espaço ou hífen: "123 456", "8845-12"; na dúvida, recusa)."""
    return bool(texto) and (looks_secret(texto) or mentions_credential(texto) or parece_senha_ou_codigo(texto or "")
                            or parece_codigo(texto))


def _classe_de_campo_de_texto(class_name: str | None) -> bool:
    """`EditText`, `AutoCompleteTextView`, `TextInputEditText` e variantes: o filho do alvo não traz a chave `editable`."""
    c = class_name or ""
    return "EditText" in c or "AutoCompleteTextView" in c


#: Que campos do alvo cada seletor `unique` usa (o espelho de `recipes._combo`, sem o import tardio).
_CAMPOS_DO_SELETOR = {"rid+text": ("resource_id", "text"), "rid+desc": ("resource_id", "desc"), "rid": ("resource_id",),
                      "desc": ("desc",), "text": ("text",)}


def _alvo_sem_segredo(alvo: dict | None, sensivel: bool = False) -> dict | None:
    """31.82 (b): o alvo gravado leva `text`/`desc` do elemento tocado. Num campo editável o `text` é o CONTEÚDO do campo
    (o que a pessoa já digitou), que não identifica o campo: sai (`resource_id`, `desc` de rótulo e classe ficam). Em
    qualquer alvo, `text`/`desc` que casem com os filtros de segredo saem. Os seletores `unique` que dependiam do campo
    removido saem junto (a destilação, em `_combo`, também os ignoraria). Campo editável NUNCA guarda `text`, tenha ou
    não outro identificador: o conteúdo é o que alguém digitou e não serve de seletor. Sem `resource_id` nem `desc`
    o alvo fica sem seletor e a etapa não vira receita (a IA conduz). Os `filhos` seguem a mesma regra: um filho cuja
    classe é de campo de texto também perde o `text`. Em tela `sensivel` (31.82 item 4) `text` e `desc` saem sempre, do
    alvo e dos filhos, junto com os `unique` que dependiam deles: ficam `resource_id`, `class_name` e o estrutural."""
    if alvo is None:
        return None
    limpo = dict(alvo)
    if limpo.get("editable") or _classe_de_campo_de_texto(limpo.get("class_name")):
        limpo["text"] = ""
    for campo in ("text", "desc"):
        if sensivel or _parece_segredo(limpo.get(campo)):
            limpo[campo] = ""
    if "unique" in limpo:
        limpo["unique"] = [k for k in limpo["unique"] or [] if all(limpo.get(c) for c in _CAMPOS_DO_SELETOR.get(k, ("?",)))]
    if limpo.get("filhos"):
        limpo["filhos"] = [f for f in (_alvo_sem_segredo(dict(f), sensivel) for f in limpo["filhos"]) if f and f.get("unique")]
    return limpo


class TrainingError(Exception):
    def __init__(self, code: str, message: str, status: int = 409):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def titulo_da_tela(tree: Any) -> str | None:
    for e in getattr(tree, "elements", []) or []:
        rid = (e.resource_id or "").rsplit("/", 1)[-1]
        if rid in TITULO_IDS and e.text:
            return e.text.strip()[:120]
    return None


class TrainingRecorder:
    def __init__(self, db: Any, bus: Any, devices: Any,
                 personas_do_aparelho: Callable[[str, str | None], list[str]]):
        self.db = db
        self.bus = bus
        self.devices = devices
        #: `(aparelho, app) -> personas vinculadas` (N:N, migração 051). Com `app`, só as que servem àquele app.
        self._personas_do_aparelho = personas_do_aparelho

    # ------------------------------------------------------------------ sessão
    def start(self, instance_id: str, *, intent: str, lease_id: str | None, app_id: str | None = None,
              operator: str | None = None, profile_id: str | None = None) -> dict[str, Any]:
        """`profile_id`: a persona escolhida pela pessoa; sem ela, a que o aparelho tem sozinho (ou nenhuma)."""
        rt = self.devices.get(instance_id)
        intent = (intent or "").strip()
        if not intent:
            raise TrainingError("invalid_intent", "Diga o que você vai ensinar (ex.: responder a DM de um cliente).", 400)
        if getattr(rt, "store", False):
            raise TrainingError("store_device", "A loja (Play Store) não é aparelho de treinamento.", 400)
        if str(getattr(rt.control, "value", rt.control)) != "user" or not lease_id or rt.lease_id != lease_id:
            raise TrainingError("control_required", "Assuma o controle do aparelho no Foco antes de gravar.", 409)
        ativa = self.active_for(instance_id)
        if ativa and getattr(rt, "training_session_id", None) == ativa:
            raise TrainingError("already_recording", "Já há um treinamento sendo gravado neste aparelho.", 409)
        if ativa:
            # 31.80: a linha diz "recording" mas o aparelho não a está gravando (o `record` sairia calado): órfã. Recusar
            # trancava o aparelho até alguém descartar à mão; encerra como `recorded` (as entradas valem) e segue.
            self._encerrar_orfa(ativa, "a gravação anterior estava sem gravador ativo")
        if app_id and self.db.one("SELECT id FROM apps WHERE id=?", (app_id,)) is None:
            raise TrainingError("unknown_app", f"Aplicativo '{app_id}' não está cadastrado.", 400)
        profile_id = self._persona_da_gravacao(instance_id, app_id, profile_id)
        sid = f"trn-{new_token()}"
        agora = now_iso()
        self.db.execute("INSERT INTO training_sessions(id, instance_id, profile_id, app_id, intent, status, operator,"
                        " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (sid, instance_id, profile_id, app_id, intent[:400],
                         "recording", operator, agora, agora))
        rt.training_session_id = sid
        self.bus.emit("log", f"{instance_id}: treinamento iniciado — {intent[:80]}", instance_id=instance_id,
                      data={"training_session_id": sid})
        return self.get(sid)

    def _persona_da_gravacao(self, instance_id: str, app_id: str | None, escolhida: str | None) -> str | None:
        """A persona da gravação: a escolhida pela pessoa (precisa estar vinculada ao aparelho), ou a ÚNICA que o
        aparelho tem para o app. Duas sem escolha → recusa: gravar sem persona perderia de quem é o ensino, e
        escolher uma delas seria adivinhar (N:N, migração 051)."""
        if escolhida is not None:
            if escolhida not in self._personas_do_aparelho(instance_id, None):
                raise TrainingError("persona_nao_vinculada",
                                    f"A persona escolhida não está vinculada a {instance_id}.", 409)
            return escolhida
        ids = list(dict.fromkeys(self._personas_do_aparelho(instance_id, app_id)))
        if len(ids) > 1:
            raise TrainingError("persona_ambigua",
                                f"{instance_id} tem {len(ids)} personas para este app: diga de qual é o ensino.", 409)
        return ids[0] if ids else None

    def active_for(self, instance_id: str) -> str | None:
        return self.db.scalar("SELECT id FROM training_sessions WHERE instance_id=? AND status='recording'",
                              (instance_id,))

    def stop(self, session_id: str, *, discard: bool = False) -> dict[str, Any]:
        s = self._row(session_id)
        if s["status"] == "recording":
            status = "discarded" if discard else "recorded"
            agora = now_iso()
            self.db.execute("UPDATE training_sessions SET status=?, finished_at=?, updated_at=? WHERE id=?",
                            (status, agora, agora, session_id))
            rt = self.devices.devices.get(s["instance_id"])
            if rt is not None and getattr(rt, "training_session_id", None) == session_id:
                rt.training_session_id = None
            self.bus.emit("log", f"{s['instance_id']}: treinamento {'descartado' if discard else 'encerrado'}",
                          instance_id=s["instance_id"], data={"training_session_id": session_id})
        elif discard and s["status"] not in ("saved",):
            self.db.execute("UPDATE training_sessions SET status='discarded', updated_at=? WHERE id=?",
                            (now_iso(), session_id))
        return self.get(session_id)

    def _encerrar_orfa(self, session_id: str, motivo: str) -> None:
        s = self._row(session_id)
        agora = now_iso()
        self.db.execute("UPDATE training_sessions SET status='recorded', finished_at=?, updated_at=? "
                        "WHERE id=? AND status='recording'", (agora, agora, session_id))
        self.bus.emit("log", f"{s['instance_id']}: gravação do treinamento encerrada — {motivo}; as entradas já gravadas "
                      "foram mantidas", level="warn", instance_id=s["instance_id"],
                      data={"training_session_id": session_id})

    def reconcile_after_restart(self) -> int:
        """31.80: na subida do processo dono dos aparelhos (`rt.training_session_id` nasce None), toda sessão
        `recording` ficou órfã: nada mais grava, mas o painel seguiria mostrando "Gravando" e `start` recusaria outra.
        Vira `recorded` (as entradas já gravadas valem). Nunca religa a gravação: gravar sem a pessoa saber é pior."""
        ids = [r["id"] for r in self.db.query("SELECT id FROM training_sessions WHERE status='recording'")]
        for sid in ids:
            self._encerrar_orfa(sid, "encerrada pelo reinício do backend")
        return len(ids)

    def stop_for_instance(self, instance_id: str) -> None:
        """Devolver o controle encerra a gravação: sem a pessoa no aparelho não há o que gravar."""
        sid = self.active_for(instance_id)
        if sid:
            self.stop(sid)

    # ------------------------------------------------------------------ entradas
    def record(self, rt: Any, entrada: dict[str, Any], tree: Any | None) -> None:
        """Grava UMA entrada. Chamado pelo gerenciador de aparelhos depois que a entrada foi executada."""
        sid = getattr(rt, "training_session_id", None)
        if not sid:
            return
        from ..taskqueue.executor import _safe_target  # noqa: PLC0415 - mesma regra de alvo das receitas
        tipo = entrada["type"]
        sensivel = bool(tree is not None and tree.sensitive)
        alvo = None
        if tree is not None and tipo in ("tap", "long_press") and entrada.get("x") is not None:
            alvo = _alvo_sem_segredo(_safe_target(tree.at(int(entrada["x"]), int(entrada["y"])), tree), sensivel)
        texto = entrada.get("text")
        tem_texto = bool(texto)
        if texto is not None:
            foco = next((e for e in (tree.elements if tree is not None else []) if e.focused), None)
            # 31.82 (a): só se guarda o que foi digitado com um campo editável, que não é de senha, em foco na árvore.
            # Sem árvore (leitura falhou ou estourou o prazo), sem foco (senha revelada, WebView, foco ainda não
            # refletido) ou com foco em quem não é campo, não se sabe se era senha, e as heurísticas deixam passar senha
            # curta ou só minúscula: fica só `has_text` e o tamanho.
            if (tree is None or sensivel or foco is None or not foco.editable or foco.password
                    or _parece_segredo(texto)):
                texto = None                     # a pessoa digitou algo que não pode ser guardado
        seq = int(self.db.scalar("SELECT COALESCE(MAX(seq), 0) FROM training_inputs WHERE session_id=?", (sid,)) or 0) + 1
        pacote = next((p for p in (tree.packages if tree is not None else []) if p != "com.android.systemui"), None)
        titulo = titulo_da_tela(tree) if tree is not None and not sensivel else None
        if _parece_segredo(titulo):
            titulo = None                        # 31.82 (c): a tela com "Seu código é 123456" não vira título
        linhas = ([ln for ln in linhas_de_conteudo(tree.elements, limite_linhas=24) if not _parece_segredo(ln)][:8]
                  if tree is not None and not sensivel else None)
        self.db.execute(
            "INSERT INTO training_inputs(session_id, seq, ts, type, x, y, x2, y2, key_name, text, has_text, text_len,"
            " package, app_id, target, screen_title, screen_lines, sensitive) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, seq, now_iso(), tipo, entrada.get("x"), entrada.get("y"), entrada.get("x2"), entrada.get("y2"),
             entrada.get("key"), texto, int(tem_texto), len(entrada.get("text") or "") if tem_texto else None,
             pacote, entrada.get("app_id"), dumps(alvo) if alvo else None,
             titulo, dumps(linhas) if linhas is not None else None,
             int(sensivel)))
        self.db.execute("UPDATE training_sessions SET updated_at=? WHERE id=?", (now_iso(), sid))
        self.bus.emit("training.input", f"{rt.id}: treinamento — entrada {seq} ({tipo})", instance_id=rt.id,
                      data={"training_session_id": sid, "seq": seq})

    # ------------------------------------------------------------------ leitura
    def _row(self, session_id: str) -> Any:
        s = self.db.one("SELECT * FROM training_sessions WHERE id=?", (session_id,))
        if s is None:
            raise TrainingError("not_found", "Treinamento não encontrado.", 404)
        return s

    def inputs(self, session_id: str) -> list[dict[str, Any]]:
        saida = []
        for r in self.db.query("SELECT * FROM training_inputs WHERE session_id=? ORDER BY seq", (session_id,)):
            d = dict(r)
            d["target"] = loads(d["target"])
            d["screen_lines"] = loads(d["screen_lines"], []) or []
            d["has_text"] = bool(d["has_text"])
            d["sensitive"] = bool(d["sensitive"])
            saida.append(d)
        return saida

    def get(self, session_id: str) -> dict[str, Any]:
        s = dict(self._row(session_id))
        s["proposal"] = loads(s["proposal"])
        s["inputs"] = self.inputs(session_id)
        return s

    def list(self, *, instance_id: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        sql, args = "SELECT * FROM training_sessions", []
        if instance_id:
            sql += " WHERE instance_id=?"
            args.append(instance_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        saida = []
        for r in self.db.query(sql, tuple(args)):
            d = dict(r)
            d["proposal"] = loads(d["proposal"])
            d["input_count"] = int(self.db.scalar("SELECT COUNT(*) FROM training_inputs WHERE session_id=?", (r["id"],)) or 0)
            saida.append(d)
        return saida
