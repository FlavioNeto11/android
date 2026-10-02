"""Loja de aplicativos no painel (pedido do dono, 26/09).

O que já existia fica onde estava: canário, promoção, quarentena e rollback em `releases/service.py`, a entrega em
`AppState.distribute`, a compatibilidade em `devices/compatibilidade.py`. Este módulo junta o que faltava para a
tela parecer uma loja e o fluxo fechar de ponta a ponta:

* **para quem** distribuir — os escolhidos, N quaisquer ou todos — e a **prévia** antes de confirmar;
* a entrega dos apps **secundários** quando o aparelho liga (o Outlook num aparelho de Instagram): antes, só o app
  principal do aparelho convergia sozinho, e o resto ficava pendente até alguém pedir "instalar em todos agora";
* o **cadastro automático** do app quando chega a versão de um pacote que ninguém cadastrou (decisão do dono) —
  hoje em `AppRepository.cadastrar_se_novo` (`modules/applications`), o dono da escrita em `apps`;
* o **agregado da vitrine**: por app, quantos aparelhos em cada versão e quantos com atualização pendente;
* **todos na versão promovida** (ADR-026, decisão do dono de 26/09: "todos devem ficar atualizados sempre"):
  promover, ou voltar uma versão, faz cada aparelho que TEM o app perseguir a promovida sozinho — `convergir_o_parque`.

Nada aqui espalha um app para quem não o tem: a entrega ao ligar e a varredura só levam o que foi distribuído para
aquele aparelho ou a promovida de um app que ele já tem. Falha não é repetida às cegas: a nova tentativa, no máximo
uma por dia, é a de `aplicar_versao_promovida`.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .db import loads
from .devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from .models import AppDTO, InstanceState
from .modules.applications.infrastructure.app_repository import AppRow
from .planning.catalog import capabilities_of
from .releases.catalog import ReleaseValidationError

if TYPE_CHECKING:
    from .state import AppState

log = logging.getLogger(__name__)

#: Estados em que o app está no aparelho, ainda que sem prova recente. Contam para "quantos têm o app".
_PRESENTE = ("installed", "ready", "verifying", "version_drift", "verify_failed")
_FALHOU = ("install_failed", "verify_failed", "incompatible", "version_drift")


# ============================================================================ para quem distribuir
def alvos_da_distribuicao(state: AppState, rel: Any, *, instance_ids: list[str] | None,
                          count: int | None) -> list[Any]:
    """Os aparelhos que `distribute` vai julgar. A loja nunca entra: nela o app vem da própria Play Store.

    Sem `instance_ids` nem `count` é o parque inteiro, exatamente como antes. Aparelho escolhido que não existe (ou
    que é a loja) é recusado com o nome, em vez de sumir da lista em silêncio: quem escolheu 5 e vê 4 no resultado
    precisa saber por quê.
    """
    if instance_ids is not None and count is not None:
        raise ReleaseValidationError("Escolha os aparelhos OU a quantidade, não os dois.")
    parque = [rt for rt in state.devices.devices.values() if not rt.store]
    if instance_ids is not None:
        pedidos = list(dict.fromkeys(instance_ids))
        if not pedidos:
            raise ReleaseValidationError("Nenhum aparelho escolhido.")
        conhecidos = {rt.id: rt for rt in parque}
        fora = [i for i in pedidos if i not in conhecidos]
        if fora:
            loja = [i for i in fora if (rt := state.devices.devices.get(i)) is not None and rt.store]
            raise ReleaseValidationError(
                "Não dá para distribuir para " + ", ".join(fora) + ": "
                + ("é a loja (Play Store), a fonte do aplicativo." if loja == fora else "não é aparelho do parque."))
        return [conhecidos[i] for i in pedidos]
    if count is not None:
        return escolher_para_distribuir(state, rel, parque, count)
    return parque


def escolher_para_distribuir(state: AppState, rel: Any, parque: list[Any], count: int) -> list[Any]:
    """N aparelhos entre os que PODEM receber e ainda não estão na versão.

    A ordem favorece quem recebe mais cedo: ligado primeiro (instala já), depois desligado desta máquina (o rodízio
    liga), por último desligado de outro servidor (espera alguém ligar). Entre iguais, quem já tem o app em outra
    versão vem antes — "atualizar 3" deve atualizar quem está atrás, não instalar em quem nunca teve. Desempate pelo
    id, para a prévia e a confirmação escolherem os mesmos.
    """
    package = rel["package_name"]
    requisitos = requisitos_de_release(rel)
    candidatos: list[tuple[tuple[int, int, int, str], Any]] = []
    for rt in parque:
        if motivo_incompativel(requisitos, capacidades_de(rt), aparelho=rt.id) is not None:
            continue
        row = state.release_repo.app_state(rt.id, package)
        if row and row["installed_release_id"] == rel["id"] and row["state"] in ("ready", "installed"):
            continue
        tem_o_app = bool(row and (row["installed_release_id"] or row["observed_version_code"] is not None))
        chave = (0 if rt.state.value == "online" else 1, 1 if rt.external else 0, 0 if tem_o_app else 1, rt.id)
        candidatos.append((chave, rt))
    return [rt for _, rt in sorted(candidatos, key=lambda c: c[0])[:count]]


def previa_de_entrega(state: AppState, rt: Any, row: Any, *, eager: bool) -> dict[str, Any]:
    """O que `distribute` faria com ESTE aparelho, sem fazer. As frases são as mesmas da entrega de verdade."""
    refaz = bool(row and row["state"] in state._ENTREGA_FALHOU)
    extra = " (a entrega anterior falhou: esta é a nova tentativa)" if refaz else ""
    if rt.state.value == "online":
        return {"id": rt.id, "outcome": "would_start", "worker_id": rt.worker_id,
                "reason": "ligado: instala agora se estiver livre; ocupado, instala depois" + extra}
    if rt.external:
        motivo = (f"está em outro servidor e {rt.state.value}: ninguém aqui o liga. A versão fica marcada e "
                  "instala quando ele voltar")
    elif eager:
        motivo = f"está {rt.state.value}: o rodízio vai ligá-lo para instalar agora"
    else:
        motivo = f"está {rt.state.value}: instala quando ligar"
    return {"id": rt.id, "outcome": "pending", "worker_id": rt.worker_id, "reason": motivo + extra}


# ============================================================================ entrega sem tarefa (ao ligar e na varredura)
def objetivo_em_andamento(state: AppState, instance_id: str, *, exceto_quem_espera_a_rede: bool = False) -> bool:
    """O aparelho tem um objetivo no meio (rodando, parado esperando uma pessoa ou com desfecho INCERTO) de uma
    execução não encerrada?

    Trocar o app principal por baixo dele mataria a navegação (a mesma regra de `_app_resolver`). A consulta de
    `dispatchable_objectives` não basta: ela não vê a etapa em `retry_wait` nem o objetivo em `waiting_user`. O
    `uncertain` entra pelo mesmo motivo (revisão do PR #13): a tela dele é a evidência de que o operador precisa para
    decidir se o efeito aconteceu, e instalar e abrir o app por cima a apagaria.

    `exceto_quem_espera_a_rede`: o objetivo suspenso entre etapas pela porta da rede (`wait_reason='rede'`, item
    25.6) continua `running`, mas está esperando justamente o reinício que a convergência da rede pede — contá-lo como
    ocupado travaria os dois para sempre. Um objetivo que volta a rodar sai dessa espera (`set_objective` zera o
    `wait_reason`), e o worker ocupado é conferido à parte por quem pergunta.
    """
    return objetivo_que_segura(state, instance_id, exceto_quem_espera_a_rede=exceto_quem_espera_a_rede) is not None


def objetivo_que_segura(state: AppState, instance_id: str, *, exceto_quem_espera_a_rede: bool = False) -> str | None:
    """O id do objetivo que `objetivo_em_andamento` conta (o primeiro), para quem precisa DIZER quem segurou o
    aparelho (29.21: o reinício da rede que não saía não dizia por quê)."""
    row = state.db.one(
        "SELECT o.id FROM objectives o JOIN runs r ON r.id = o.run_id WHERE o.instance_id=?"
        " AND o.status IN ('running','waiting_user','uncertain') AND r.status NOT IN ('completed','cancelled','failed')"
        + (" AND NOT (o.status='running' AND COALESCE(o.wait_reason,'')='rede')" if exceto_quem_espera_a_rede else "")
        + " ORDER BY o.id LIMIT 1",
        (instance_id,))
    return str(row["id"]) if row is not None else None


def objetivo_esperando_a_rede(state: AppState, instance_id: str) -> str | None:
    """O id de um objetivo de execução não encerrada parado à espera da rede deste aparelho (`wait_reason='rede'`):
    `pending` (a porta de despacho o segurou) ou `running` (suspenso entre etapas, item 25.6). É quem está esperando o
    reinício que a convergência da rede pede (29.21)."""
    row = state.db.one(
        "SELECT o.id FROM objectives o JOIN runs r ON r.id = o.run_id WHERE o.instance_id=?"
        " AND o.status IN ('pending','running') AND COALESCE(o.wait_reason,'')='rede'"
        " AND r.status NOT IN ('completed','cancelled','failed') ORDER BY o.id LIMIT 1",
        (instance_id,))
    return str(row["id"]) if row is not None else None


def pendentes_ao_ligar(state: AppState, rt: Any) -> list[tuple[str, str]]:
    """(pacote, release) desejados para este aparelho e ainda não instalados — de qualquer app que ele tem.

    Os apps secundários (o Outlook, a VPN num aparelho de Instagram) não tinham caminho nenhum: nenhuma tarefa
    daquele pacote chega ali para acionar a porta. Decisão do dono (26/09): instalam quando o aparelho liga, pela
    mesma fila e sem passar na frente de tarefa. ADR-026 (26/09, "todos devem ficar atualizados sempre"): o app
    principal entra também — antes ele só era instalado pela porta do app, antes da próxima tarefa, e um aparelho
    ligado e livre ficava na versão antiga até alguém mandar trabalho. Fica de fora só enquanto o aparelho tem um
    objetivo no meio (`objetivo_em_andamento`); aí a porta do app o entrega antes do próximo.

    Mesmas travas da porta: só versão ainda entregável (instalável E promovida), compatível, em estado de entrega
    automática e sem operação aberta. Falha não entra: a nova tentativa, no máximo uma por dia, é rearmada por
    `aplicar_versao_promovida`.

    Aparelho em quarentena (conta travada logada, ADR-055) não recebe nada ao ligar: a entrega termina na prova de
    ABERTURA do app, que abriria a conta travada.
    """
    if rt.store or state.quarentena(rt.id) is not None:
        return []
    principal = state._pacote_do_aparelho(rt.id)
    no_meio: bool | None = None
    saida: list[tuple[str, str]] = []
    for row in state.db.query("SELECT * FROM device_app_state WHERE instance_id=? AND desired_release_id IS NOT NULL",
                              (rt.id,)):
        pkg, rid = row["package_name"], row["desired_release_id"]
        if rid == row["installed_release_id"] or row["pending_op"]:
            continue
        if pkg == principal:
            if no_meio is None:
                no_meio = objetivo_em_andamento(state, rt.id)
            if no_meio:
                continue
        if row["state"] not in state._ENTREGA_AUTOMATICA:
            continue
        rel = state.release_repo.release_row(rid)
        if rel is None or rel["status"] != "installable" or rel["channel"] != "promoted":
            continue
        if motivo_incompativel(requisitos_de_release(rel), capacidades_de(rt), aparelho=rt.id) is not None:
            continue
        saida.append((pkg, rid))
    return saida


async def entregar_pendentes(state: AppState, rt: Any, pendentes: list[tuple[str, str]]) -> None:
    """Instala, um por um, o que `pendentes_ao_ligar` achou. A falha de um não impede o próximo."""
    for package, release_id in pendentes:
        try:
            await state._entregar(rt, package, release_id)
        except Exception as exc:  # noqa: BLE001 - `_entregar` já gravou a falha no estado; aqui só segue para o próximo
            log.info("%s: entrega de %s ao ligar não concluiu (%s)", rt.id, package, exc)


def trabalho_ao_ligar(state: AppState, rt: Any, *, motivo: str = "ligou") -> Any:
    """O que o aparelho que acabou de ligar deve receber: a rede pedida (ADR-056, 25.4), as versões desejadas dos apps
    que ele tem e o proxy legado pedido.

    `None` = nada. Senão, uma corrotina-fábrica que roda dentro do trabalho de reobservação do aparelho. `motivo` é
    `ligou` (boot, wake, readoção depois de reinício do backend ou do worker: a rede é relida) ou `varredura` (a
    passada de 60 s: a rede só é relida quando vence `rede.deriva_s`).
    """
    from .devices.proxy import aplicar_no_aparelho, proxy_pendente  # noqa: PLC0415

    if state.quarentena(rt.id) is not None:
        # Quarentena (ADR-055): nem app nem proxy — trocar a rede por baixo de uma conta travada é mexer nela.
        return None
    entregas = pendentes_ao_ligar(state, rt)
    proxy = proxy_pendente(state, rt)
    rede = state.rede_convergencia.trabalho(rt, motivo=motivo)
    if not entregas and not proxy and rede is None:
        return None

    async def trabalho() -> None:
        if rede is not None:
            # Primeiro a rede: a tarefa que espera por ela (política exigida) espera menos, e o reinício que ela pede
            # só sai depois que este trabalho solta o aparelho.
            try:
                await rede()
            except Exception as exc:  # noqa: BLE001 - a convergência grava a falha; os apps seguem
                log.info("%s: rede ao ligar não concluiu (%s)", rt.id, exc)
        if proxy:
            try:
                await aplicar_no_aparelho(state, rt)
            except Exception as exc:  # noqa: BLE001 - `aplicar_no_aparelho` grava a falha; os apps seguem
                log.info("%s: proxy ao ligar não concluiu (%s)", rt.id, exc)
        await entregar_pendentes(state, rt, entregas)

    return trabalho


#: De quanto em quanto tempo a varredura procura aparelho LIGADO e LIVRE com entrega pendente. O gancho de "entrou
#: no ar" não alcança dois casos: o aparelho que estava ocupado na hora de distribuir (fica ligado, nunca "entra no
#: ar" de novo) e o que já estava ligado quando o backend reiniciou.
VARREDURA_S = 60.0


def convergir_ligados(state: AppState) -> list[str]:
    """Entrega o que ficou pendente em aparelho ligado e livre. Devolve os ids em que o trabalho começou.

    Não liga ninguém (isso é o "instalar agora", que é o rodízio), não passa na frente de tarefa (aparelho com
    objetivo despachável fica para depois) e não repete falha (`pendentes_ao_ligar`/`proxy_pendente` já a excluem).

    ADR-026: antes de procurar o que entregar, o aparelho adota a versão promovida de cada app que tem. Sem isso, a
    volta de versão nunca chegaria a um aparelho ligado: depois dela, quem estava na versão voltada tem desejada ==
    instalada, e não haveria nada "pendente" a encontrar.
    """
    esperando_tarefa = {o["instance_id"] for o in state.repo.dispatchable_objectives()}
    iniciados: list[str] = []
    for rt in list(state.devices.devices.values()):
        if rt.store or rt.state != InstanceState.online or rt.id in state.scheduler.workers \
                or rt.id in esperando_tarefa:
            continue
        state.adotar_promovidas(rt)
        trabalho = trabalho_ao_ligar(state, rt, motivo="varredura")
        if trabalho is not None and state.scheduler.run_device_job(rt, trabalho,
                                                                   label="entrega do que foi distribuído"):
            iniciados.append(rt.id)
    return iniciados


def convergir_o_parque(state: AppState, package: str) -> dict[str, Any]:
    """Depois de promover (ou de voltar uma versão): todo aparelho de tarefa que TEM o app persegue a promovida.

    ADR-026 (decisão do dono, 26/09: "todos devem ficar atualizados sempre"). Aqui só se grava a versão desejada
    (`aplicar_versao_promovida`) e se acorda a varredura uma vez: o ligado e livre começa a instalar agora; o ocupado
    recebe na varredura, sem passar na frente de tarefa; o desligado, quando ligar. Nenhum aparelho é ligado por
    isto — isso é o "instalar agora" (`distribute` com `eager`). O app principal do aparelho conta como "tem"
    mesmo sem linha, a regra que já valia; fora dele, quem não tem o app não o recebe.

    Devolve `{target_release_id, devices[]}`, um item por aparelho que tem o app, com `outcome` ∈ `started |
    pending | already | incompatible | kept` e o motivo — a mesma forma da resposta de `distribute`.
    """
    alvo = state.releases.promoted_release(package)
    if alvo is None or alvo.status.value != "installable":
        return {"target_release_id": None, "devices": []}
    linha = state.release_repo.release_row(alvo.id)
    parque = sorted((rt for rt in state.devices.devices.values() if not rt.store), key=lambda rt: rt.id)
    com_o_app: list[Any] = []
    for rt in parque:
        if package != state._pacote_do_aparelho(rt.id) and not state.tem_o_app(state.release_repo.app_state(rt.id,
                                                                                                         package)):
            continue
        try:
            state.aplicar_versao_promovida(rt, package)
        except Exception:  # noqa: BLE001 - um aparelho com problema não impede os outros de convergir
            log.exception("%s: falha ao adotar a versão promovida de %s", rt.id, package)
        com_o_app.append(rt)
    iniciados = set(convergir_ligados(state))
    esperando_tarefa = {o["instance_id"] for o in state.repo.dispatchable_objectives()}
    return {"target_release_id": alvo.id,
            "devices": [_desfecho_da_convergencia(state, rt, package, linha, rt.id in iniciados,
                                                  rt.id in esperando_tarefa) for rt in com_o_app]}


def _desfecho_da_convergencia(state: AppState, rt: Any, package: str, alvo: Any, iniciou: bool,
                              esperando_tarefa: bool) -> dict[str, Any]:
    """O que acontece com ESTE aparelho depois da convergência, em palavras — sem mexer em nada."""
    row = state.release_repo.app_state(rt.id, package)
    rotulo = f"{alvo['version_name']} ({alvo['version_code']})"

    def item(outcome: str, reason: str) -> dict[str, Any]:
        return {"id": rt.id, "outcome": outcome, "reason": reason, "worker_id": rt.worker_id}

    if row is not None and row["installed_release_id"] == alvo["id"] and row["state"] in ("ready", "installed"):
        return item("already", "já está nesta versão")
    if (porque := motivo_incompativel(requisitos_de_release(alvo), capacidades_de(rt), aparelho=rt.id)) is not None:
        return item("incompatible", porque)
    if (quarentena := state.quarentena(rt.id)) is not None:
        return item("kept", quarentena)
    if (fica := state.fora_da_convergencia(row, alvo)) is not None:
        instalada = state.release_no_aparelho(row)
        mesma = instalada is not None and int(instalada["version_code"]) == int(alvo["version_code"])
        return item("already" if mesma else "kept", fica)
    if row is None or row["desired_release_id"] != alvo["id"]:
        return item("kept", "fica como está: " + (f"há uma operação em andamento ({row['pending_op']})"
                                                  if row is not None and row["pending_op"] else "nada a adotar agora"))
    if row["state"] in state._ENTREGA_FALHOU:
        detalhe = row["detail"] or row["state"]
        if row["drift_kind"] == "downgrade_refused":
            return item("kept", f"o Android recusou voltar de versão preservando os dados: {detalhe}")
        return item("kept", f"a entrega anterior falhou ({detalhe}); nova tentativa automática no máximo uma vez "
                            "por dia, ou peça Distribuir de novo")
    if row["pending_op"] or row["state"] == "installing":
        return item("started", f"instalando {rotulo} agora")
    principal = package == state._pacote_do_aparelho(rt.id)
    if rt.state != InstanceState.online:
        if rt.external:
            return item("pending", f"está em outro servidor e {rt.state.value}: instala quando ele voltar")
        return item("pending", f"está {rt.state.value}: instala quando ligar")
    if esperando_tarefa:
        return item("pending", "tem tarefa esperando: " + ("a porta do app instala antes dela" if principal
                                                            else "instala na varredura, depois da tarefa"))
    if principal and objetivo_em_andamento(state, rt.id):
        return item("pending", "tem um objetivo no meio: a porta do app instala antes do próximo")
    if iniciou:
        return item("started", f"instalando {rotulo} agora")
    return item("pending", "ocupado agora: instala na varredura (60 s), quando ficar livre")


async def laco_de_convergencia(state: AppState) -> None:
    while True:
        await asyncio.sleep(VARREDURA_S)
        try:
            convergir_ligados(state)
        except Exception:  # noqa: BLE001 - a varredura nunca pode derrubar o backend
            log.exception("varredura de entregas pendentes")


# ============================================================================ o registro de apps
def app_dto(r: AppRow, state: AppState | None = None) -> AppDTO:
    promovida = None
    if state is not None:
        try:
            promovida = state.releases.promoted_release(r["package"])
        except Exception:  # noqa: BLE001 - catálogo indisponível não derruba a lista de apps; só cala a versão
            log.exception("versão promovida de %s não pôde ser lida", r["package"])
    return AppDTO(id=r["id"], name=r["name"], package=r["package"], activity=r["activity"], apk_path=r["apk_path"],
                  nav_hints=r["nav_hints"], known_selectors=loads(r["known_selectors"]), builtin=bool(r["builtin"]),
                  promoted_release_id=promovida.id if promovida else None,
                  promoted_version_name=promovida.version_name if promovida else None,
                  promoted_version_code=promovida.version_code if promovida else None,
                  category=r["category"])


def apps_list(state: AppState) -> list[AppDTO]:
    return [app_dto(r, state) for r in state.apps.listar()]


def _apps_changed(s: AppState) -> None:
    """Republica a lista de apps (`apps.updated`). Mora aqui, e não em `api.py`, porque o `state` também anuncia (o
    app que o import de versão cadastra sozinho) e o `state` não importa a API."""
    s.bus.emit("apps.updated", "Apps atualizados", data={"apps": [a.model_dump() for a in apps_list(s)]})


# ============================================================================ o agregado da vitrine
def _versao(r: Any) -> dict[str, Any]:
    return {"id": r["id"], "version_name": r["version_name"], "version_code": r["version_code"],
            "status": r["status"], "channel": r["channel"]}


def vitrine(state: AppState) -> list[dict[str, Any]]:
    """Um cartão por app cadastrado: ícone, versão promovida, aparelhos por versão e o que pede atenção.

    Tudo sai do banco, que é cache do que os aparelhos responderam (`device_app_state`). A loja fica fora das
    contagens: nela o app vem da Play Store e não é destino de distribuição.
    """
    db = state.db
    loja = state.cfg.store_id
    saida: list[dict[str, Any]] = []
    for app in db.query("SELECT * FROM apps ORDER BY builtin DESC, name"):
        pkg = app["package"]
        releases = db.query("SELECT * FROM app_releases WHERE package_name=? ORDER BY version_code DESC, imported_at DESC",
                            (pkg,))
        por_id = {r["id"]: r for r in releases}
        # A MESMA escolha que o parque persegue (ADR-026): com duas promovidas de mesmo número, o cartão mostrava a
        # importada por último, e a convergência seguia a ordem do banco — podiam ser versões diferentes.
        alvo = state.releases.promoted_release(pkg)
        promovida = por_id.get(alvo.id) if alvo is not None and alvo.status.value == "installable" else None
        com_icone = ([promovida] if promovida is not None and promovida["icon_file"] else []) + \
            [r for r in releases if r["icon_file"]]
        linhas = [r for r in db.query("SELECT * FROM device_app_state WHERE package_name=?", (pkg,))
                  if r["instance_id"] != loja]
        por_versao: dict[str, int] = {}
        outra_versao = atrasados = pendentes = instalando = falhas = com_app = 0
        for r in linhas:
            instalada = por_id.get(r["installed_release_id"]) if r["installed_release_id"] else None
            presente = r["state"] in _PRESENTE and (instalada is not None or r["observed_version_code"] is not None)
            if presente:
                com_app += 1
                if instalada is not None:
                    por_versao[instalada["id"]] = por_versao.get(instalada["id"], 0) + 1
                else:
                    outra_versao += 1                # instalada por fora do catálogo: versão sem release conhecida
                codigo = instalada["version_code"] if instalada is not None else r["observed_version_code"]
                if promovida is not None and codigo is not None and codigo < promovida["version_code"]:
                    atrasados += 1
            if r["desired_release_id"] and r["desired_release_id"] != r["installed_release_id"] \
                    and r["state"] in state._ENTREGA_AUTOMATICA and not r["pending_op"]:
                pendentes += 1
            if r["pending_op"] or r["state"] == "installing":
                instalando += 1
            if r["state"] in _FALHOU:
                falhas += 1
        atencao: list[str] = []
        esperando = [r for r in releases if r["status"] == "validated"]
        if esperando:
            atencao.append(f"{len(esperando)} versão(ões) esperando aprovação da assinatura")
        if (canario := next((r for r in releases if r["channel"] == "canary"), None)) is not None:
            atencao.append(f"{canario['version_name']} em prova em {canario['canary_instance_id'] or '?'}")
        if releases and promovida is None:
            atencao.append("nenhuma versão promovida: ainda não há o que distribuir")
        if falhas:
            atencao.append(f"{falhas} aparelho(s) com falha de entrega")
        cap = capabilities_of(pkg)
        saida.append({
            "app_id": app["id"], "name": app["name"], "package": pkg,
            "category": app["category"] if "category" in app.keys() else None,
            "builtin": bool(app["builtin"]), "has_catalog": cap.has_catalog,
            "label": (promovida["label"] if promovida is not None else None) or (releases[0]["label"] if releases else None),
            "icon_release_id": com_icone[0]["id"] if com_icone else None,
            "promoted": _versao(promovida) if promovida is not None else None,
            "latest": _versao(releases[0]) if releases else None,
            "releases": len(releases),
            "devices_with_app": com_app,
            "by_version": sorted(
                ({"release_id": rid, "version_name": por_id[rid]["version_name"],
                  "version_code": por_id[rid]["version_code"], "channel": por_id[rid]["channel"], "devices": n}
                 for rid, n in por_versao.items()),
                key=lambda v: -int(v["version_code"])),
            "other_version": outra_versao,
            "outdated": atrasados,
            "pending": pendentes,
            "installing": instalando,
            "failed": falhas,
            "attention": atencao,
        })
    return saida
