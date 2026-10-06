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
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.modules.learning.domain.ensino_da_falha import pergunta_da_etapa

from ..db import dumps, loads
from ..security.redaction import looks_secret, mentions_credential, parece_codigo, parece_linha_com_codigo, parece_senha_ou_codigo
from ..social.observacao import linhas_de_conteudo
from ..util import new_token, now_iso
from . import dado_da_persona
from .origem import OrigemDaFalha, OrigemRecusada, contexto_da_falha, origem_da_falha, origin_da_linha

if TYPE_CHECKING:
    from ..automation.hierarchy import UiTree

log = logging.getLogger(__name__)

TITULO_IDS = ("action_bar_title", "igds_action_bar_title", "header_title", "title_text_view", "toolbar_title")


def _parece_segredo(texto: str | None) -> bool:
    """Os filtros de segredo da gravação, juntos: formato de credencial, fala de credencial, senha/código e código solto
    de 4 a 8 dígitos (com espaço ou hífen: "123 456", "8845-12"; na dúvida, recusa)."""
    return bool(texto) and (looks_secret(texto) or mentions_credential(texto) or parece_senha_ou_codigo(texto or "")
                            or parece_codigo(texto))


_UM_DIGITO = re.compile(r"\d")
#: A tecla do teclado telefônico: um dígito e até 4 letras maiúsculas, com separador opcional ("2,ABC", "2 ABC", "2ABC").
#: As letras de cada dígito do teclado telefônico: "5G", "4K", "2FA" não são teclas; "5 JKL" é.
_LETRAS_DA_TECLA = {"2": "ABC", "3": "DEF", "4": "GHI", "5": "JKL", "6": "MNO", "7": "PQRS", "8": "TUV", "9": "WXYZ", "0": "+"}
_TECLA_TELEFONICA = re.compile(r"(\d)[ ,.\-]?([A-Z]{1,4}|\+)")
_RID_TERMINA_EM_DIGITO = re.compile(r"\d$")
#: Os nomes de um teclado ou padrão de bloqueio desenhado num View só (o alvo é o teclado inteiro: a posição do toque É o
#: dígito). Só estes; casam por PEDAÇO do nome (`spinner` não é `pin`).
_TERMOS_DE_TECLADO = frozenset({
    "pin", "passcode", "keypad", "numpad", "pinpad", "lockpattern", "patternview", "pincode", "pinview", "pinentry",
    "pinlock", "numberpad", "patternlock", "lockview", "dialpad"})
_PEDACOS_DE_NOME = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+")


def _nome_de_teclado(nome: str | None) -> bool:
    """`pin_pad`, `PinKeypadView`, `lock_pattern_view`: separa por `_`, `.`, `-` e por caixa (camelCase) e compara os
    pedaços, e os pares vizinhos juntos, com `_TERMOS_DE_TECLADO`."""
    pedacos = [m.group().lower() for m in _PEDACOS_DE_NOME.finditer(nome or "")]
    candidatos = set(pedacos) | {a + b for a, b in zip(pedacos, pedacos[1:], strict=False)}
    return bool(candidatos & _TERMOS_DE_TECLADO)
_ENFEITE_DE_TOKEN = ".,;:!?()[]{}\"'"


def _parece_segredo_de_tela(texto: str | None) -> bool:
    """O que vem da TELA (linha, título, rótulo de alvo): além dos filtros do texto, `parece_codigo` por token ("Use
    4821 para entrar") e a linha que fala de código com o número perto ("G-123456 is your ... verification code").
    Over-filtra de propósito ("Recife 2024" cai pelo token): na dúvida, recusa."""
    if not texto:
        return False
    if _parece_segredo(texto) or parece_linha_com_codigo(texto):
        return True
    return any(parece_codigo(t.strip(_ENFEITE_DE_TOKEN)) for t in texto.split())


#: 31.97: o dígito por extenso (pt e en) como rótulo de tecla. Só vale DENTRO de um contêiner de teclado: fora dele, um
#: botão "Um" ou "One" é um botão comum.
_DIGITO_POR_EXTENSO = frozenset({
    "zero", "um", "uma", "dois", "duas", "três", "tres", "quatro", "cinco", "seis", "sete", "oito", "nove",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"})


def _rotulo_de_tecla(texto: str | None, em_teclado: bool = False) -> bool:
    t = (texto or "").strip()
    if _UM_DIGITO.fullmatch(t):
        return True
    if em_teclado and t.casefold() in _DIGITO_POR_EXTENSO:
        return True
    m = _TECLA_TELEFONICA.fullmatch(t)
    return bool(m) and _LETRAS_DA_TECLA.get(m.group(1)) == m.group(2)


def _e_tecla_de_teclado_numerico(alvo: dict | None, em_teclado: bool = False) -> bool:
    """31.94: o toque numa tecla de PIN desenhada na tela. Tirar o rótulo não basta: o `resource_id` (`key4`, `digit_4`,
    `btn4`) e o x/y de cada toque num teclado fixo SÃO o dígito. É tecla quando o elemento tocado (ou um filho rotulado
    dele) tem rótulo (`text` OU `desc`) de um dígito ou de tecla telefônica, ou quando NÃO tem rótulo e o `resource_id` termina
    em dígito, ou ainda quando o `resource_id`/classe nomeia um teclado desenhado num View só (`pin_pad`, `PinKeypadView`).
    Dígito por extenso ("um", "one") só entra com `em_teclado` (31.97: o ponto está dentro de um contêiner de teclado,
    ver `_ponto_em_teclado`); fora dele é botão comum.
    `android:id/button1` (OK/Cancelar de todo diálogo) termina em dígito mas tem rótulo: não é tecla. Campo editável não
    entra pelo rótulo (o `text` dele é o conteúdo, não o nome)."""
    if not alvo:
        return False
    rotulos = [] if alvo.get("editable") else [alvo.get("text"), alvo.get("desc")]
    for filho in alvo.get("filhos") or []:
        if not (filho.get("editable") or _classe_de_campo_de_texto(filho.get("class_name"))):
            rotulos += [filho.get("text"), filho.get("desc")]
    if any(_rotulo_de_tecla(r, em_teclado) for r in rotulos):
        return True
    if any((r or "").strip() for r in rotulos):
        return False
    rid = (alvo.get("resource_id") or "").rsplit("/", 1)[-1]
    # Sem rótulo: o teclado desenhado num View só (rid ou classe com `pin`, `keypad`...) ou a tecla cujo id termina em dígito.
    if _nome_de_teclado(rid) or _nome_de_teclado((alvo.get("class_name") or "").rsplit(".", 1)[-1]):
        return True
    return not alvo.get("editable") and bool(_RID_TERMINA_EM_DIGITO.search(rid))


def _ponto_em_teclado(tree: UiTree, x: int, y: int) -> bool:
    """31.97: o ponto (x, y) cai dentro de um elemento cujo `resource_id` ou classe nomeia um teclado/padrão de bloqueio
    (a mesma `_nome_de_teclado` do toque), seja ele o próprio alvo ou um ancestral por área. O padrão de bloqueio se
    desenha ARRASTANDO: a origem do arraste sobre a vista do padrão (ou sobre o teclado) é o primeiro ponto do segredo."""
    for e in tree.elements:
        if e.bounds[0] <= x <= e.bounds[2] and e.bounds[1] <= y <= e.bounds[3]:
            if _nome_de_teclado((e.resource_id or "").rsplit("/", 1)[-1]) or _nome_de_teclado(
                    (e.class_name or "").rsplit(".", 1)[-1]):
                return True
    return False


def _tem_id_estrutural(alvo: dict | None) -> bool:
    """O alvo (ou um filho rotulado dele) tem `resource_id`: a receita ainda o acha sem coordenada."""
    return bool(alvo) and bool(alvo.get("resource_id") or any(f.get("resource_id") for f in alvo.get("filhos") or []))


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
    não outro identificador: o conteúdo é o que alguém digitou e não serve de seletor. Texto ou rótulo de um dígito só (tecla de PIN desenhada) sai em qualquer tela. Sem `resource_id` nem `desc`
    o alvo fica sem seletor e a etapa não vira receita (a IA conduz). Os `filhos` seguem a mesma regra: um filho cuja
    classe é de campo de texto também perde o `text`. Em tela `sensivel` (31.82 item 4) `text` e `desc` saem sempre, do
    alvo e dos filhos, junto com os `unique` que dependiam deles: ficam `resource_id`, `class_name` e o estrutural."""
    if alvo is None:
        return None
    limpo = dict(alvo)
    if limpo.get("editable") or _classe_de_campo_de_texto(limpo.get("class_name")):
        limpo["text"] = ""
    for campo in ("text", "desc"):
        valor = limpo.get(campo)
        # S1: o PIN num teclado DESENHADO na tela é uma sequência de alvos "4", "8"...: um dígito só não se guarda.
        if (sensivel or _parece_segredo_de_tela(valor) or (isinstance(valor, str) and _UM_DIGITO.fullmatch(valor.strip()))):
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
                 personas_do_aparelho: Callable[[str, str | None], list[str]], owner_id: str | None = None,
                 variaveis_da_persona: Callable[[str | None], dict[str, str]] | None = None):
        self.db = db
        self.bus = bus
        self.devices = devices
        #: O backend dono dos aparelhos deste processo (`instances.hosted_by`): a reconciliação da partida só fecha o que
        #: é dele, e não a gravação VIVA de um aparelho que outra réplica hospeda.
        self.owner_id = owner_id
        #: `(aparelho, app) -> personas vinculadas` (N:N, migração 051). Com `app`, só as que servem àquele app.
        self._personas_do_aparelho = personas_do_aparelho
        #: 31.111 F4: `attempt_id -> origin.diagnostico` (o diagnóstico do 30.13 em linguagem de gente). Quem liga é o
        #: `AppState`, depois de montar o aprendizado; sem ele, a sessão sai com `diagnostico: null`.
        self.diagnostico_da_falha: Callable[[str], dict[str, object] | None] | None = None
        #: 31.112: `persona -> {perfil_…: valor}` (só o não sigiloso), para mascarar as perguntas da proposta na leitura.
        self._variaveis_da_persona = variaveis_da_persona

    # ------------------------------------------------------------------ sessão
    def start(self, instance_id: str, *, intent: str, lease_id: str | None, app_id: str | None = None,
              operator: str | None = None, profile_id: str | None = None,
              origem: OrigemDaFalha | None = None) -> dict[str, Any]:
        """`profile_id`: a persona escolhida pela pessoa; sem ela, a que o aparelho tem sozinho (ou nenhuma). `origem`
        (31.111 F1): a etapa que falhou e deu origem ao ensino; só rotula a sessão, não muda nenhuma trava."""
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
                        " created_at, updated_at, origin_run_id, origin_step_id, origin_attempt_id)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (sid, instance_id, profile_id, app_id, intent[:400], "recording", operator, agora, agora,
                         origem.run_id if origem else None, origem.step_id if origem else None,
                         origem.attempt_id if origem else None))
        rt.training_session_id = sid
        self.bus.emit("log", f"{instance_id}: treinamento iniciado — {intent[:80]}", instance_id=instance_id,
                      data={"training_session_id": sid})
        return self.get(sid)

    def origem_da_falha(self, run_id: str, step_id: str) -> OrigemDaFalha:
        """31.111 F1: a etapa que falhou, lida do banco; recusa (404/409) como o resto do treino."""
        try:
            return origem_da_falha(self.db, run_id, step_id)
        except OrigemRecusada as exc:
            raise TrainingError(exc.code, exc.message, exc.status) from exc

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

    def _gravando_com_controle(self, instance_id: str, session_id: str) -> bool:
        """31.92: a gravação é VIVA quando o aparelho a está gravando (`rt.training_session_id`) e há uma pessoa no
        controle (lease de usuário). Sem gravador ativo, sem aparelho neste processo ou sem controle (devolvido,
        expirado, reinício) ela é órfã: não há quem a esteja ensinando, e qualquer pessoa autenticada a encerra."""
        rt = self.devices.devices.get(instance_id)
        return bool(rt is not None and getattr(rt, "training_session_id", None) == session_id
                    and str(getattr(rt.control, "value", rt.control)) == "user" and rt.lease_id)

    def _exigir_o_controle(self, instance_id: str, lease_id: str | None, discard: bool) -> None:
        """Mesma conferência do `start`: o lease é o ATUAL do controle de usuário do aparelho. Recusa sem tocar em nada
        (a gravação segue). Desde o 29.143 o lease tem dono: outra pessoa que clica "Assumir" recebe 409
        `controlled_by_other`, não o lease, e a tomada explícita troca o lease e encerra esta gravação antes."""
        rt = self.devices.devices.get(instance_id)
        if not lease_id or rt is None or rt.lease_id != lease_id:
            acao = "descarta" if discard else "encerra"
            raise TrainingError("control_required", f"Só quem está com o controle do aparelho {acao} esta gravação.", 409)

    def _hospedada_em_outro_servidor(self, instance_id: str) -> bool:
        """Duas réplicas: o aparelho tem dono (`instances.hosted_by`) e não é este processo. A gravação VIVA dele não é
        órfã: o gravador está lá, e daqui não dá para conferir o lease. Sem dono carimbado, ou sem `owner_id` neste
        processo, vale o tratamento de sempre."""
        if not self.owner_id:
            return False
        dono = self.db.scalar("SELECT hosted_by FROM instances WHERE id=?", (instance_id,))
        return bool(dono) and dono != self.owner_id

    def stop(self, session_id: str, *, discard: bool = False, lease_id: str | None = None,
             por_sistema: bool = False) -> dict[str, Any]:
        """Falha fechado (31.92): por padrão a chamada é de uma PESSOA e, numa gravação VIVA, `lease_id` precisa ser o do
        controle atual (senão 409 `control_required`). Aparelho hospedado por outra réplica: 409
        `gravacao_em_outro_servidor`. Só o chamador do sistema (devolução do controle) diz `por_sistema=True` e dispensa a
        conferência. A gravação órfã e o descarte de sessão que não está gravando nunca pedem controle."""
        s = self._row(session_id)
        if not por_sistema and s["status"] == "recording":
            if self._hospedada_em_outro_servidor(s["instance_id"]):
                raise TrainingError("gravacao_em_outro_servidor",
                                    "Esta gravação está em outro servidor; encerre por lá.", 409)
            if self._gravando_com_controle(s["instance_id"], session_id):
                self._exigir_o_controle(s["instance_id"], lease_id, discard)
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

    def desfazer_a_ultima(self, session_id: str, *, lease_id: str | None, seq: int | None = None) -> dict[str, object]:
        """31.90-D: tira da gravação VIVA a última entrada (o toque errado), sem descartar a sessão inteira. Só quem está
        com o controle do aparelho desfaz (o mesmo lease do `start` e do `stop`); a gravação que já parou se corrige na
        revisão, e a órfã não tem quem esteja ensinando. O aparelho NÃO volta: a entrada sai só da gravação.

        `seq` (opcional): o número da entrada que a pessoa viu como última. Se outra entrada chegou entre o que ela viu e
        o pedido, recusa com 409 `entrada_mudou` em vez de apagar a nova. Lê e apaga na mesma transação: a entrada que
        o gravador grava depois disso recebe o próximo número a partir do que ficou."""
        s = self._row(session_id)
        if s["status"] != "recording":
            raise TrainingError("nao_esta_gravando", "Só a gravação em andamento desfaz a última entrada; depois de "
                                                      "parar, corrija na revisão.", 409)
        if self._hospedada_em_outro_servidor(s["instance_id"]):
            raise TrainingError("gravacao_em_outro_servidor", "Esta gravação está em outro servidor; desfaça por lá.",
                                409)
        if not self._gravando_com_controle(s["instance_id"], session_id):
            raise TrainingError("control_required", "Só quem está com o controle do aparelho desfaz a última entrada.",
                                409)
        rt = self.devices.devices.get(s["instance_id"])
        if not lease_id or rt is None or rt.lease_id != lease_id:
            raise TrainingError("control_required", "Só quem está com o controle do aparelho desfaz a última entrada.",
                                409)
        with self.db.tx():
            # N1 da leitura: o status se confere de novo DENTRO da transação, e pela escrita: o UPDATE condicional trava a
            # linha da sessão (no PostgreSQL, um `stop` concorrente espera este commit), e a gravação que parou entre a
            # conferência de cima e aqui não perde entrada.
            viva = self.db.execute("UPDATE training_sessions SET updated_at=? WHERE id=? AND status='recording'",
                                   (now_iso(), session_id))
            if (viva.rowcount or 0) != 1:
                raise TrainingError("nao_esta_gravando", "Só a gravação em andamento desfaz a última entrada; depois de "
                                                          "parar, corrija na revisão.", 409)
            ultima = self.db.one("SELECT seq, type FROM training_inputs WHERE session_id=? ORDER BY seq DESC LIMIT 1",
                                 (session_id,))
            if ultima is None:
                raise TrainingError("sem_entrada", "Não há entrada para desfazer nesta gravação.", 409)
            if seq is not None and int(ultima["seq"]) != seq:
                raise TrainingError("entrada_mudou", f"A última entrada agora é a {ultima['seq']}, não a {seq}; "
                                                     "confira antes de desfazer.", 409)
            self.db.execute("DELETE FROM training_inputs WHERE session_id=? AND seq=?", (session_id, ultima["seq"]))
        desfeita = {"seq": int(ultima["seq"]), "type": str(ultima["type"])}
        self.bus.emit("training.input.undone", f"{s['instance_id']}: treinamento — entrada {desfeita['seq']} desfeita "
                                               f"({desfeita['type']})", instance_id=s["instance_id"],
                      data={"training_session_id": session_id, **desfeita})
        return {**self.get(session_id), "undone": desfeita}

    def _encerrar_orfa(self, session_id: str, motivo: str) -> None:
        s = self._row(session_id)
        agora = now_iso()
        cur = self.db.execute("UPDATE training_sessions SET status='recorded', finished_at=?, updated_at=? "
                              "WHERE id=? AND status='recording'", (agora, agora, session_id))
        if getattr(cur, "rowcount", 1) == 0:
            return                               # outra partida já a encerrou: sem log em dobro
        self.bus.emit("log", f"{s['instance_id']}: gravação do treinamento encerrada — {motivo}; as entradas já gravadas "
                      "foram mantidas", level="warn", instance_id=s["instance_id"],
                      data={"training_session_id": session_id})

    def reconcile_after_restart(self) -> int:
        """31.80: na subida do processo dono dos aparelhos (`rt.training_session_id` nasce None), toda sessão
        `recording` ficou órfã: nada mais grava, mas o painel seguiria mostrando "Gravando" e `start` recusaria outra.
        Vira `recorded` (as entradas já gravadas valem). Nunca religa a gravação: gravar sem a pessoa saber é pior."""
        sql, params = "SELECT t.id FROM training_sessions t WHERE t.status='recording'", ()
        if self.owner_id:
            # O mesmo padrão de `commands/store.py` e `releases/repository.py`: com duas réplicas, a partida desta não
            # encerra a gravação viva de um aparelho que OUTRA hospeda (instances.hosted_by).
            sql += (" AND NOT EXISTS (SELECT 1 FROM instances i WHERE i.id=t.instance_id"
                    " AND i.hosted_by IS NOT NULL AND i.hosted_by<>?)")
            params = (self.owner_id,)
        ids = [r["id"] for r in self.db.query(sql, params)]
        for sid in ids:
            self._encerrar_orfa(sid, "encerrada pelo reinício do backend")
        return len(ids)

    def stop_for_instance(self, instance_id: str, *, motivo: str | None = None) -> None:
        """Devolver o controle encerra a gravação: sem a pessoa no aparelho não há o que gravar. `motivo` (29.143, a
        tomada explícita) vai ao log: a gravação fica `recorded`, nem salva nem descartada, e quem ensinava decide na
        revisão."""
        sid = self.active_for(instance_id)
        if sid:
            self.stop(sid, por_sistema=True)     # o sistema encerra: sem conferência de controle
            if motivo:
                self.bus.emit("log", f"{instance_id}: gravação do treinamento {motivo}; as entradas já gravadas foram "
                              "mantidas para a revisão", level="warn", instance_id=instance_id,
                              data={"training_session_id": sid})

    # ------------------------------------------------------------------ entradas
    def record(self, rt: Any, entrada: dict[str, Any], tree: Any | None) -> None:
        """Grava UMA entrada. Chamado pelo gerenciador de aparelhos depois que a entrada foi executada."""
        sid = getattr(rt, "training_session_id", None)
        if not sid:
            return
        from ..taskqueue.executor import _safe_target  # noqa: PLC0415 - mesma regra de alvo das receitas
        tipo = entrada["type"]
        if tipo == "text" and entrada.get("clear_first"):    # 31.84: sem coluna nova, `key_name` marca o texto enviado limpando o campo
            entrada = {**entrada, "key": "clear_first"}
        sensivel = bool(tree is not None and tree.sensitive)
        alvo = None
        x, y = entrada.get("x"), entrada.get("y")
        x2, y2 = entrada.get("x2"), entrada.get("y2")
        # A coluna `sensitive` quer dizer "tela sensível OU entrada que não se guarda" (31.94/31.97): uma só coluna para os
        # dois sentidos até a migração futura do ensino separá-los.
        marcada = sensivel
        em_teclado = tree is not None and x is not None and _ponto_em_teclado(tree, int(x), int(y))
        if tree is not None and tipo in ("tap", "long_press") and x is not None:
            bruto = _safe_target(tree.at(int(x), int(y)), tree)
            if _e_tecla_de_teclado_numerico(bruto, em_teclado) or (sensivel and not _tem_id_estrutural(bruto)):
                # 31.94: teclado de PIN desenhado, ou toque em tela sensível sem identificador estrutural: sem alvo e sem
                # x/y (num teclado fixo, a coordenada é o dígito). A receita não nasce disso (coordenada solta).
                x = y = None
                marcada = True
            else:
                alvo = _alvo_sem_segredo(bruto, sensivel)
        if tipo in ("tap", "long_press") and x is not None and not (alvo and (alvo.get("unique") or alvo.get("filhos"))):
            # 31.94 (geral): sem seletor utilizável (alvo None, ou sem `unique` e sem filhos) a coordenada não vira receita e,
            # num teclado desenhado num View só (Flutter, SurfaceView), é o dígito. Não é segredo conhecido: sem `sensitive`.
            x = y = None
        if tipo == "swipe" and x is not None:
            # 31.97: o padrão de bloqueio se DESENHA arrastando: começo e fim do arraste são o segredo. Pela ORIGEM: origem
            # dentro de contêiner de teclado ou de padrão de bloqueio, ou tela sensível, sai sem as quatro coordenadas e
            # marcada. A regra de tecla isolada (rótulo de um dígito, id terminado em dígito) é só do toque: arraste não
            # aperta tecla, e rolar a partir de um dia "5" ou de `item1` é rolagem comum. Sem árvore (leitura falhou) não
            # se sabe onde começou: segue o toque, que também perde a coordenada sem árvore, e fica sem marca. A rolagem
            # comum guarda tudo, tenha o ponto seletor ou não: rolar é a entrada mais comum e a coordenada não é segredo.
            if tree is None:
                x = y = x2 = y2 = None
            elif sensivel or em_teclado:
                x = y = x2 = y2 = None
                marcada = True
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
        if _parece_segredo_de_tela(titulo):
            titulo = None                        # 31.82 (c): a tela com "Seu código é 123456" não vira título
        linhas = ([ln for ln in linhas_de_conteudo(tree.elements, limite_linhas=24) if not _parece_segredo_de_tela(ln)][:8]
                  if tree is not None and not sensivel else None)
        self.db.execute(
            "INSERT INTO training_inputs(session_id, seq, ts, type, x, y, x2, y2, key_name, text, has_text, text_len,"
            " package, app_id, target, screen_title, screen_lines, sensitive) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, seq, now_iso(), tipo, x, y, x2, y2,
             entrada.get("key"), texto, int(tem_texto), len(entrada.get("text") or "") if tem_texto else None,
             pacote, entrada.get("app_id"), dumps(alvo) if alvo else None,
             titulo, dumps(linhas) if linhas is not None else None,
             int(marcada)))
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
        s["origin"] = origin_da_linha(self.db, s)
        if s["origin"]:                       # 31.111 F2: o contexto só na leitura de UMA sessão (a lista fica leve)
            s["origin"]["context"] = contexto_da_falha(self.db, s["origin"]["run_id"], s["origin"]["step_id"],
                                                       s["origin"]["attempt_id"])
            # 31.111 F4: a causa provável (30.13) da tentativa que falhou, com a pergunta do que mostrar. Sem IA.
            aid = s["origin"]["attempt_id"]
            diagnostico = (self.diagnostico_da_falha(str(aid))
                           if aid and self.diagnostico_da_falha is not None else None)
            if diagnostico is not None:
                # 31.116 (v1.82): a pergunta é a do ESTADO da etapa, a mesma do `ensino-sugerido` (waiting_user tem a dela)
                etapa = self.db.one("SELECT status FROM steps WHERE id=?", (s["origin"]["step_id"],))
                diagnostico = {**diagnostico,
                               "pergunta": pergunta_da_etapa(str(etapa["status"]) if etapa else "", diagnostico)}
            s["origin"]["diagnostico"] = diagnostico
        s["inputs"] = self.inputs(session_id)
        s["proposal"] = self._proposta_mascarada(loads(s["proposal"]), s.get("profile_id"), s["inputs"])
        return s

    def _proposta_mascarada(self, proposta: object, profile_id: str | None,
                            entradas: list[dict[str, object]]) -> object:
        """31.112: a pergunta da IA guardada (de antes do 31.112, ou de um caminho que não passou pela troca) sai com o
        marcador também na LEITURA, pela mesma regra do 31.87 F2 (o dado que a pessoa digitou inteiro)."""
        if not isinstance(proposta, dict) or self._variaveis_da_persona is None or not (
                proposta.get("questions") or proposta.get("answers")):
            return proposta
        persona = dado_da_persona.demonstrados(self._variaveis_da_persona(profile_id), entradas)
        return dado_da_persona.nas_perguntas(proposta, persona)

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
            d["origin"] = origin_da_linha(self.db, d)
            d["proposal"] = loads(d["proposal"])
            if isinstance(d["proposal"], dict) and (d["proposal"].get("questions") or d["proposal"].get("answers")):
                d["proposal"] = self._proposta_mascarada(d["proposal"], d.get("profile_id"), self.inputs(r["id"]))
            d["input_count"] = int(self.db.scalar("SELECT COUNT(*) FROM training_inputs WHERE session_id=?", (r["id"],)) or 0)
            saida.append(d)
        return saida
