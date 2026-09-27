"""`CommandBus` sobre o despacho de hoje (`commands/despacho.py`): cerca, outbox e diário, sem terceiro canal (R10).

Cada verbo vai pelo caminho que já existe para ele, e só por ele:

* `start`, `wake`, `stop`, `hibernate` → `despacho.pedir_ciclo_de_vida` (pré-voo, outbox, o worker que hospeda): o
  pedido do rodízio;
* `app.install` → `pedir_trabalho_de_app` → `AppState._entregar` (a promovida; `-d` ao sair de versão voltada): a
  entrega da porta do app;
* `app.verify` → `pedir_trabalho_de_app` → `ReleaseService.verify_on` (relê o `pm`): a rota de verificação;
* `session.connect` → `pedir_trabalho_de_app` → `ensure_session(automatic=True)` (cofre, canal sensível): a porta
  de sessão;
* `session.verify` → `pedir_trabalho_de_app` → `ensure_session(observe_only=True)` (só relê a tela): a porta de
  sessão.

Antes de gravar qualquer linha, recusa o que o despacho recusaria DEPOIS de gravar: aparelho ocupado (outro trabalho
na fila dele ou outro comando em voo), em manutenção, fora do ar para trabalho de app, sem o verbo declarado. Assim uma
segunda passada do `apply` sobre um aparelho ocupado não enche o histórico de `rejected` — "um aparelho, uma
operação" vale antes do comando existir, não só no pré-voo.

`reconcile` fecha `uncertain` pelo mesmo caminho da sonda (`commands/reconciler.py`): transição conferida pela
máquina de estados + evento para o painel.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING

from app.commands import despacho
from app.commands.despacho import APP_COMMAND_VERBS, VERBOS_EXCLUSIVOS, DespachoRecusado
from app.commands.states import InvalidCommandTransition
from app.commands.store import publicar_comando
from app.db import Row
from app.models import CommandState, InstanceState
from app.modules.execution.infrastructure.providers import tem_provedor_de_sessao
from app.shared.commands import CommandBus, CommandRef, CommandStatus, RunRef, sequence_of
from app.shared.resources import known

if TYPE_CHECKING:  # pragma: no cover - só para o verificador de tipos
    from app.devices.manager import DeviceRuntime
    from app.state import AppState

#: Os verbos de ciclo de vida que o central pede sozinho (`pedir_ciclo_de_vida`). `restart`/`reset` ficam de fora:
#: são da escada de reparo, e nenhum recurso os pede.
CICLO_DE_VIDA = frozenset({"start", "wake", "stop", "hibernate"})
#: Os verbos de trabalho que os recursos pedem.
TRABALHO = frozenset({"app.install", "app.verify", "session.connect", "session.verify"})
#: "Um aparelho, uma operação": qualquer um destes em voo segura o próximo pedido, antes de gravar.
_OCUPAM = VERBOS_EXCLUSIVOS | APP_COMMAND_VERBS


def _texto(valor: object) -> str | None:
    return None if valor is None else str(valor)


def _ref(row: Row, *, deduplicated: bool = False) -> CommandRef:
    return CommandRef(instance_id=str(row["instance_id"]), verb=str(row["verb"]), command_id=str(row["id"]),
                      status=known(CommandStatus, row["state"]), idempotency_key=_texto(row["idempotency_key"]),
                      created_at=_texto(row["created_at"]), deduplicated=deduplicated, reason=_texto(row["reason"]))


class _Recusa(Exception):
    """O pedido não pode seguir, e nada foi gravado."""


class DespachoCommandBus:
    def __init__(self, s: AppState) -> None:
        self._s = s

    # ------------------------------------------------------------------ leitura
    def _por_chave(self, chave: str) -> Row | None:
        return self._s.db.one("SELECT * FROM commands WHERE idempotency_key=?", (chave,))

    def _com_prefixo(self, instance_id: str, verb: str, key_prefix: str, *, so_incertos: bool) -> list[Row]:
        # `substr`, e não `LIKE`: id de app pode ter `_`, que no `LIKE` casa qualquer caractere. O `CAST` é pelo
        # PostgreSQL, que não resolve `substr(text, int, bigint)` (o mesmo motivo de `CommandStore._travar_cerca`).
        marca = key_prefix + "#"
        sql = ("SELECT * FROM commands WHERE instance_id=? AND verb=?"
               " AND substr(idempotency_key, 1, CAST(? AS INTEGER))=?")
        params: tuple[object, ...] = (instance_id, verb, len(marca), marca)
        if so_incertos:
            sql += " AND state=?"
            params += (CommandState.uncertain.value,)
        return self._s.db.query(sql, params)

    def latest(self, instance_id: str, verb: str, key_prefix: str) -> CommandRef | None:
        linhas = self._com_prefixo(instance_id, verb, key_prefix, so_incertos=False)
        if not linhas:
            return None
        return _ref(max(linhas, key=lambda r: sequence_of(_texto(r["idempotency_key"]))))

    def unsettled(self, instance_id: str, verb: str, key_prefix: str) -> list[CommandRef]:
        linhas = self._com_prefixo(instance_id, verb, key_prefix, so_incertos=True)
        return [_ref(r) for r in sorted(linhas, key=lambda r: sequence_of(_texto(r["idempotency_key"])))]

    def hosts(self, instance_id: str) -> bool:
        """`so_meu` (`Repository.so_meu`): `hosted_by` nulo, ou linha ausente, continua sendo meu — é o banco anterior
        à 027 e o da produção com um backend só."""
        dono = self._s.db.scalar("SELECT hosted_by FROM instances WHERE id=?", (instance_id,))
        return dono is None or dono == self._s.cfg.owner_id

    # ------------------------------------------------------------------ escrita
    def settle(self, command_id: str, *, proof: str) -> CommandRef:
        s = self._s
        linha = s.commands.get(command_id)
        if linha is None:
            raise KeyError(f"comando desconhecido: {command_id}")
        if linha["state"] != CommandState.uncertain.value:
            return _ref(linha)                  # outro caminho já fechou (a sonda, uma pessoa): vale o que está lá
        try:
            novo = s.commands.transition(command_id, CommandState.succeeded, reason=proof)
        except (InvalidCommandTransition, KeyError):
            return _ref(s.commands.get(command_id) or linha)
        publicar_comando(s.bus, novo)
        s.bus.emit("log", f"{novo['instance_id']}: o comando {command_id} ({novo['verb']}) estava incerto e foi "
                          "fechado pela verificação do recurso.", instance_id=novo["instance_id"])
        return _ref(novo)

    def request(self, instance_id: str, verb: str, params: Mapping[str, str], *, requested_by: str,
                run_ref: RunRef | None, idempotency_key: str, reason: str) -> CommandRef:
        if verb not in CICLO_DE_VIDA | TRABALHO:
            raise ValueError(f"'{verb}' não tem caminho no canal de comandos dos recursos")
        if (existente := self._por_chave(idempotency_key)) is not None:
            return _ref(existente, deduplicated=True)
        s = self._s
        try:
            rt = self._aparelho(instance_id, verb)
            if verb in CICLO_DE_VIDA:
                despacho.pedir_ciclo_de_vida(s, instance_id, verb, reason, requested_by=requested_by,
                                             idempotency_key=idempotency_key)
            else:
                fabrica, rotulo, parametros = self._trabalho(rt, verb, params, run_ref)
                despacho.pedir_trabalho_de_app(s, rt, verb, fabrica, label=rotulo, params=parametros,
                                               idempotency_key=idempotency_key, requested_by=requested_by)
        except _Recusa as exc:
            return CommandRef(instance_id=instance_id, verb=verb, reason=str(exc))
        except DespachoRecusado as exc:
            return CommandRef(instance_id=instance_id, verb=verb, reason=exc.message)
        linha = self._por_chave(idempotency_key)
        if linha is None:
            return CommandRef(instance_id=instance_id, verb=verb,
                              reason=f"nenhum worker conectado declara '{verb}' em {instance_id}")
        return _ref(linha)

    def _aparelho(self, instance_id: str, verb: str) -> DeviceRuntime:
        """O runtime, ou `_Recusa` com o motivo — as recusas que o despacho só faria depois de gravar o comando."""
        s = self._s
        rt = s.devices.devices.get(instance_id)
        if rt is None:
            raise _Recusa(f"{instance_id} não está neste backend")
        if rt.store:
            raise _Recusa(f"{instance_id} é o aparelho-loja (Play Store): ele não executa tarefas")
        if rt.id in s.scheduler.workers:
            raise _Recusa(f"{instance_id} está ocupado com outro trabalho; o pedido espera a próxima passada")
        if (aberto := s.commands.open_for_instance(instance_id, verbs=set(_OCUPAM))) is not None:
            raise _Recusa(f"{instance_id} já tem o comando '{aberto['verb']}' em andamento ({aberto['id']}, "
                          f"{aberto['state']})")
        if rt.worker_id and (manutencao := s.workers.motivo_manutencao(rt.worker_id)) is not None:
            raise _Recusa(manutencao)
        if verb in CICLO_DE_VIDA and verb not in (rt.worker_verbs or []):
            raise _Recusa(f"nenhum worker conectado declara '{verb}' em {instance_id}")
        if verb in TRABALHO and rt.state != InstanceState.online:
            # A rota HTTP recusa igual (409 `not_online`): instalar num aparelho fora do ar terminaria em
            # `install_failed`, que não se repete sozinho.
            raise _Recusa(f"{instance_id} precisa estar online (está '{rt.state.value}')")
        return rt

    def _trabalho(self, rt: DeviceRuntime, verb: str, params: Mapping[str, str],
                  run_ref: RunRef | None) -> tuple[Callable[[], Awaitable[object]], str, dict[str, object]]:
        s = self._s
        trilha: dict[str, object] = ({"run_id": run_ref.run_id, "objective_id": run_ref.objective_id}
                                     if run_ref is not None else {})
        if verb in ("app.install", "app.verify"):
            pacote = params.get("package")
            if not pacote:
                raise _Recusa("o pedido não diz o pacote")
            if verb == "app.verify":
                return (lambda: s.releases.verify_on(rt, pacote, s.installer), "verificação do app (recurso)",
                        {"package": pacote, **trilha})
            release_id = params.get("release_id")
            if not release_id:
                raise _Recusa("o pedido não diz a versão")
            despacho.conferir_release_para(s, rt, release_id)          # 404/409 da rota, sem tocar em nada
            # `_entregar` é a entrega da porta do app: a falha vira estado (não se repete a cada passada) e a volta
            # de uma versão abandonada vai com `-d`. É o mecanismo que §11 nomeia; não há outro público.
            return (lambda: s._entregar(rt, pacote, release_id),  # noqa: SLF001
                    "instalação da versão promovida (recurso)", {"package": pacote, "release_id": release_id, **trilha})
        perfil, app_id = params.get("profile_id"), params.get("app_id")
        if not perfil or not app_id:
            raise _Recusa("o pedido não diz o perfil e o app")
        pacote_do_app = s.db.scalar("SELECT package FROM apps WHERE id=?", (app_id,))
        if not isinstance(pacote_do_app, str) or not tem_provedor_de_sessao(pacote_do_app):
            raise _Recusa(f"o app '{app_id}' não tem login automático")
        if verb == "session.verify":
            return (lambda: s.instagram.ensure_session(rt, perfil, observe_only=True),
                    "verificação da sessão (recurso)", {"profile_id": perfil, **trilha})
        if not s.sensitive_input.available():
            # A mesma recusa da porta de sessão (achado #105): sem o canal sensível comprovado, o login iria digitar o
            # usuário e parar na senha.
            raise _Recusa("o canal de preenchimento de credencial está indisponível (mascaramento de log do Appium "
                          "não comprovado)")
        return (lambda: s.instagram.ensure_session(rt, perfil, automatic=True), "conexão da sessão (recurso)",
                {"profile_id": perfil, **trilha})



def command_bus(s: AppState) -> CommandBus:
    """O canal do backend `s`. Tipado pela porta: é o que faz o mypy conferir que `DespachoCommandBus` a cumpre."""
    return DespachoCommandBus(s)
