"""O estado das contas que um comando usa (31.282, ADR-087, adendo v1.132): o que o refinador sabe ANTES de perguntar.

O refinador não pergunta "a senha já está guardada ou será definida?" porque o sistema já sabe: para cada par (persona
escolhida, app de conta do comando) lê o estado da conta no banco. Daqui saem duas coisas:

- as linhas de estado que vão ao prompt (`RefineRequest.contas`): só o estado, nunca valor de segredo nem endereço;
- as ações estruturadas (`AcaoDeConta`) do pedido, para o painel: o par cuja credencial não está pronta (`sem_conta`,
  `planejada` ou `falha`) vira um item com as ações possíveis, por id, sem campo de texto livre.

Só leitura: não grava nem toca o cofre. Quem prepara a credencial é `social/provisionamento.py`, chamado pelo painel.
"""
from __future__ import annotations

from collections.abc import Sequence

from ..db import Database
from ..modules.execution.domain.command_refinement import (ACAO_ABRIR_CONTAS, ACAO_CONTINUAR, ACAO_PREPARAR_CREDENCIAL,
                                                           ACAO_USAR_EXISTENTE, AcaoDeConta, ContaReutilizavel)

SEM_CONTA = "sem_conta"
#: Os estados em que a credencial ainda não está pronta e o painel tem o que fazer.
ESTADOS_PENDENTES = frozenset({"planejada", "falha"})
#: Quantos pares entram no prompt e na lista (o teto de alvos do refinador é 20; cada par é uma linha).
MAX_PARES = 40


def _nome_da_persona(db: Database, profile_id: str) -> str:
    r = db.one("SELECT display_name, first_name, last_name, username FROM instagram_profiles WHERE id=?", (profile_id,))
    if r is None:
        return profile_id
    completo = " ".join(x for x in (r["first_name"], r["last_name"]) if x)
    return str(r["display_name"] or completo or r["username"] or profile_id)


def _reutilizaveis(db: Database, profile_id: str, excluida: str | None) -> list[ContaReutilizavel]:
    """As outras contas DESTA persona que já têm senha guardada: de lá o painel pode reutilizar, por escolha expressa."""
    linhas = db.query(
        "SELECT a.id, a.app_id, ap.name AS app_nome FROM profile_accounts a"
        " JOIN account_credentials c ON c.account_id = a.id LEFT JOIN apps ap ON ap.id = a.app_id"
        " WHERE a.profile_id=? AND a.id<>? ORDER BY a.created_at, a.id", (profile_id, excluida or ""))
    return [ContaReutilizavel(account_id=str(r["id"]), app_id=str(r["app_id"]), app_nome=str(r["app_nome"] or ""))
            for r in linhas]


def contas_do_comando(db: Database, profile_ids: Sequence[str], app_ids: Sequence[str],
                      nomes_dos_apps: dict[str, str]) -> tuple[list[str], list[AcaoDeConta]]:
    """(linhas de estado para o prompt, ações pendentes) dos pares (persona, app de conta) do comando."""
    linhas: list[str] = []
    acoes: list[AcaoDeConta] = []
    for pid in list(dict.fromkeys(profile_ids)):
        nome = _nome_da_persona(db, pid)
        for app_id in dict.fromkeys(app_ids):
            if len(linhas) >= MAX_PARES:
                return linhas, acoes
            app_nome = nomes_dos_apps.get(app_id, app_id)
            conta = db.one("SELECT id, provisioning_state FROM profile_accounts WHERE profile_id=? AND app_id=?"
                           " AND host IS NULL", (pid, app_id))
            estado = str(conta["provisioning_state"] or "confirmada") if conta is not None else SEM_CONTA
            linhas.append(f"{nome}, {app_nome}: {estado}")
            if estado != SEM_CONTA and estado not in ESTADOS_PENDENTES:
                continue
            reutilizaveis = _reutilizaveis(db, pid, str(conta["id"]) if conta is not None else None)
            oferecidas = [ACAO_PREPARAR_CREDENCIAL, ACAO_ABRIR_CONTAS]
            if reutilizaveis:
                oferecidas.append(ACAO_USAR_EXISTENTE)
            oferecidas.append(ACAO_CONTINUAR)
            acoes.append(AcaoDeConta(persona_id=pid, persona_nome=nome, app_id=app_id, app_nome=app_nome, host=None,
                                     estado=estado, acoes=oferecidas, reutilizavel_de=reutilizaveis))
    return linhas, acoes
