"""Loja de aplicativos no painel (pedido do dono, 26/09).

O que já existia fica onde estava: canário, promoção, quarentena e rollback em `releases/service.py`, a entrega em
`AppState.distribute`, a compatibilidade em `devices/compatibilidade.py`. Este módulo junta o que faltava para a
tela parecer uma loja e o fluxo fechar de ponta a ponta:

* **para quem** distribuir — os escolhidos, N quaisquer ou todos — e a **prévia** antes de confirmar;
* a entrega dos apps **secundários** quando o aparelho liga (o Outlook num aparelho de Instagram): antes, só o app
  principal do aparelho convergia sozinho, e o resto ficava pendente até alguém pedir "instalar em todos agora";
* o **cadastro automático** do app quando chega a versão de um pacote que ninguém cadastrou (decisão do dono);
* o **agregado da vitrine**: por app, quantos aparelhos em cada versão e quantos com atualização pendente.

Nada aqui instala por conta própria algo que uma pessoa não pediu: a entrega ao ligar só leva o que foi distribuído
para aquele aparelho, e falha não é repetida sozinha — a mesma regra de `_ENTREGA_FALHOU`.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from typing import TYPE_CHECKING, Any

from .devices.compatibilidade import capacidades_de, motivo_incompativel, requisitos_de_release
from .models import InstanceState
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


# ============================================================================ entrega dos apps secundários
def pendentes_ao_ligar(state: AppState, rt: Any) -> list[tuple[str, str]]:
    """(pacote, release) distribuídos para este aparelho e ainda não instalados — fora o app principal dele.

    O app principal (`instances.app_id`) já tem caminho próprio: a porta do app instala antes da tarefa, e
    `aplicar_versao_promovida` o adota ao ligar. Os outros (o Outlook, a VPN num aparelho de Instagram) não tinham
    nenhum: nenhuma tarefa daquele pacote chega ali para acionar a porta. Decisão do dono (26/09): instalam quando
    o aparelho liga, pela mesma fila e sem passar na frente de tarefa.

    Mesmas travas da porta: só versão ainda entregável (instalável E promovida), compatível, em estado de entrega
    automática e sem operação aberta. Falha não entra — quem repete é uma pessoa.
    """
    if rt.store:
        return []
    principal = state._pacote_do_aparelho(rt.id)
    saida: list[tuple[str, str]] = []
    for row in state.db.query("SELECT * FROM device_app_state WHERE instance_id=? AND desired_release_id IS NOT NULL",
                              (rt.id,)):
        pkg, rid = row["package_name"], row["desired_release_id"]
        if pkg == principal or rid == row["installed_release_id"] or row["pending_op"]:
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


def trabalho_ao_ligar(state: AppState, rt: Any) -> Any:
    """O que o aparelho que acabou de ligar deve receber: apps secundários distribuídos e o proxy pedido.

    `None` = nada. Senão, uma corrotina-fábrica que roda dentro do trabalho de reobservação do aparelho.
    """
    from .devices.proxy import aplicar_no_aparelho, proxy_pendente  # noqa: PLC0415

    entregas = pendentes_ao_ligar(state, rt)
    proxy = proxy_pendente(state, rt)
    if not entregas and not proxy:
        return None

    async def trabalho() -> None:
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
    """
    esperando_tarefa = {o["instance_id"] for o in state.repo.dispatchable_objectives()}
    iniciados: list[str] = []
    for rt in list(state.devices.devices.values()):
        if rt.store or rt.state != InstanceState.online or rt.id in state.scheduler.workers \
                or rt.id in esperando_tarefa:
            continue
        trabalho = trabalho_ao_ligar(state, rt)
        if trabalho is not None and state.scheduler.run_device_job(rt, trabalho,
                                                                   label="entrega do que foi distribuído"):
            iniciados.append(rt.id)
    return iniciados


async def laco_de_convergencia(state: AppState) -> None:
    while True:
        await asyncio.sleep(VARREDURA_S)
        try:
            convergir_ligados(state)
        except Exception:  # noqa: BLE001 - a varredura nunca pode derrubar o backend
            log.exception("varredura de entregas pendentes")


# ============================================================================ cadastro
def novo_id_de_app(db: Any, nome: str, *, tabela: str = "apps") -> str:
    """Id legível e único a partir do nome: "Configurações" → "configuracoes", "Outlook" → "outlook".

    `tabela` é constante do código (`apps` ou `proxy_profiles`), nunca entrada de quem chama a API."""
    plain = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "-", plain.lower()).strip("-") or "app"
    app_id, n = base, 2
    while db.one(f"SELECT id FROM {tabela} WHERE id=?", (app_id,)):
        app_id, n = f"{base}-{n}", n + 1
    return app_id


def cadastrar_app_se_novo(db: Any, package: str, rotulo: str | None) -> str | None:
    """Cadastra o app de um pacote que chegou por versão (upload, pasta ou loja) sem estar no registro.

    Decisão do dono (26/09): a vitrine nunca esconde uma versão importada. O app nasce com o rótulo lido do APK e
    SEM categoria — quem opera ajusta depois. Devolve o id criado, ou `None` quando o pacote já estava cadastrado.
    """
    if db.one("SELECT id FROM apps WHERE package=?", (package,)):
        return None
    nome = (rotulo or "").strip()[:80] or package
    app_id = novo_id_de_app(db, nome)
    db.execute("INSERT INTO apps(id, name, package, builtin) VALUES (?,?,?,0)", (app_id, nome, package))
    return app_id


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
        promovida = next((r for r in releases if r["channel"] == "promoted" and r["status"] == "installable"), None)
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
