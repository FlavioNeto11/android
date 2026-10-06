"""O fio entre o central e o agente do worker, congelado (evolução arquitetural, fase 0 do relatório 04).

Nada de Python atravessa a rede: as duas máquinas compartilham só JSON. Mudar o protocolo de LUGAR (pacote
`contracts`, reexportação, manifesto do instalador) não pode mudar o fio, e o que prova isso é o esquema JSON dos
modelos continuar o mesmo, byte a byte. Sem este teste, uma restrição afrouxada ou um campo sem padrão passaria em
toda a suíte — que roda central e agente do MESMO commit — e só quebraria no agente de campo, que está em outro.

Mudou o esquema de propósito? Então o hash muda junto, com a razão no commit, depois do checklist de
`CHECKLIST_DE_MUDANCA`. O hash por tipo existe para a falha dizer QUAL mensagem mudou, sem ninguém ter de
reconstruir o JSON do commit anterior para comparar.
"""
from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import BaseModel

import app.contracts.worker.protocol as protocolo
import app.contracts.worker.verbos as verbos
import app.devices.verbs as verbos_do_central
import app.workers.protocol as protocolo_antigo

#: Receita do relatório 04 §2.2, medida em 82b1057 (pydantic 2.13.5): esquema das 13 mensagens de
#: `UPSTREAM`/`DOWNSTREAM`, por chave de tipo, mais `EnvioDeMidia` (o canal de mídia, que não tem `type`). O mesmo
#: valor antes e depois de o protocolo mudar de `app/workers/` para `app/contracts/worker/`.
#:
#: 29.11 (30/09/2026): `hello` e `heartbeat` mudaram de propósito — `WorkerDevice` ganhou `gpu_mode`, `gpu_gles` e
#: `gpu_vulkan` (o renderizador do emulador, que só o agente lê do log dele). Checklist: (1) aditivo, os três com
#: padrão `None`, e o que o agente de campo RECEBE não mudou; (2) nenhuma restrição afrouxada, os campos não têm
#: nenhuma; (3) `EnvioDeMidia` intocado; (4) sem subir versão nem feature: central antigo ignora (`extra="ignore"`),
#: agente antigo não manda e o central fica com "não se sabe"; (5) não é mudança de forma do pydantic. Eram
#: `18285a7c65c51551`, `04c2ae7aaf3a47f9` (heartbeat) e `9b76556e5c2ac878` (hello).
#:
#: A9 (02/10/2026): `heartbeat` mudou de propósito — ganhou `sent_at` (relógio local do agente na saída da batida), com
#: o qual o central re-mede o desvio de relógio a cada batida. Checklist: (1) aditivo, padrão `None`; agente antigo não
#: manda e o central cai no `clock_offset_s` da conexão; (2) nenhuma restrição afrouxada (campo livre, sem validação:
#: ilegível vira "sem medida nova"); (3) `EnvioDeMidia` intocado; (4) sem subir versão nem feature: central antigo
#: ignora (`extra="ignore"`) e segue com `clock_offset_s`; (5) não é mudança de forma do pydantic. Eram
#: `074980b4f9d7d9df` (total) e `a4445ce3107bff89` (heartbeat).
#:
#: 29.59 (04/10/2026): `hello` mudou de propósito — ganhou `agent_code`, a impressão do código do pacote do agente,
#: que o central compara no lugar da versão para não acender `agente defasado` por commit só de docs. Checklist: (1)
#: aditivo, padrão `None`; agente antigo não manda e o central cai na comparação de versão; (2) nenhuma restrição
#: afrouxada; (3) `EnvioDeMidia` intocado; (4) sem subir versão nem feature: central antigo ignora (`extra="ignore"`);
#: (5) não é mudança de forma do pydantic. Eram `f93b70d6c866d979` (total) e `e83b5bba0247b782` (hello).
#:
#: 29.154 (06/10/2026): ganharam cinco mensagens NOVAS, todas da feature negociada `remote_exec` (comando remoto, ADR-079):
#: `exec`, `exec_cancel`, `exec_result_ack` (central → agente) e `exec_ack`, `exec_result` (agente → central). Checklist: (1)
#: aditivo, nenhuma mensagem existente mudou de esquema (os hashes dos outros tipos são os de antes); (2) nenhuma
#: restrição afrouxada; (3) `EnvioDeMidia` intocado; (4) SIM, é feature negociada (`FEATURE_COMANDO_REMOTO`), sem subir
#: `PROTOCOL_VERSION`: o agente só a anuncia com `comando_remoto: true` e o central só a aceita com o interruptor dele e o
#: do worker ligados; agente antigo ignora o tipo desconhecido e central antigo não a aceita; (5) não é mudança de forma
#: do pydantic. Era `d840bd78f67ef1a8` (total).
ESQUEMA_CONGELADO = "cfb85c95fbc75962"
HASH_POR_TIPO = {
    "EnvioDeMidia": "fdb1207c5b4f42e4",
    "ack": "33e561638726df19",
    "cancel": "4b3f1e8c98e90ec3",
    "dispatch": "51d36366c2fd424c",
    "exec": "f07cc64397e98fd4",
    "exec_ack": "06ff00bef2f5eaf4",
    "exec_cancel": "95eb91a731facffa",
    "exec_result": "9c8c86f03c0c4e20",
    "exec_result_ack": "d498155dfa903a3a",
    "heartbeat": "eb41cf4a6d0337ca",
    "hello": "4fe30e1ea9192cc9",
    "limits": "6d4b495c5750b4bc",
    "observe_image": "26eb5e83e5a347f4",
    "observe_result": "34513cb6edfbd781",
    "progress": "72dd66c2d7ba6cb6",
    "refused": "e5cbdb3e2658242e",
    "result": "b98b93f0810cdbd6",
    "result_ack": "ac60af7165c2e669",
    "welcome": "d33c05b31b7825e4",
}

#: As constantes que os dois lados comparam ou mandam pelo fio. Uma marca de texto trocada num lado só (a
#: `MARCA_DE_FILA`, por exemplo) não muda esquema nenhum e quebra o adiamento de prazo do central em silêncio.
CONSTANTES_CONGELADAS: dict[str, object] = {
    "PROTOCOL_VERSION": 1,
    "PROTOCOL_MIN": 1,
    "FEATURE_RESERVA_DE_BOOT": "boot_reservations",
    "FEATURE_OBSERVACAO_LOCAL": "observe_local",
    "FEATURE_COMANDO_REMOTO": "remote_exec",
    "EXEC_TIMEOUT_MAX_S": 600.0,
    "EXEC_SAIDA_MAX_BYTES": 64 * 1024,
    "MARCA_DE_FILA": "na fila de boot",
    "RECUSA_CERCA_NAO_MAIOR": "fence_not_newer",
    "MIDIA_MAX_BYTES": 8 * 1024 * 1024,
    "MIDIA_CABECALHO_MAX": 16 * 1024,
    "PARTES_DE_MIDIA": ("cheia", "miniatura", "modelo"),
    "LADO_MAX_ACEITO": 10_000,
    "PRAZO_MAX_OBSERVACAO_S": 120.0,
}

CHECKLIST_DE_MUDANCA = """
O fio entre o central e o agente mudou. Antes de atualizar o valor registrado, responda (C7, docs/worker.md):
  1. É aditivo? Campo novo tem padrão, e o agente de campo (outro commit) continua validando o que recebe?
  2. Afrouxa uma restrição que o lado ANTIGO valida? (ex.: `Limits.boot_parallelism le=10`: o agente valida
     `limits` e `dispatch` sem proteção, e um `ValidationError` derruba o canal a cada batida.)
  3. Mexe em `EnvioDeMidia`? Ele é `extra="forbid"`: campo novo do agente é recusado por central antigo.
  4. Precisa subir `PROTOCOL_VERSION`/`PROTOCOL_MIN`, ou de uma feature negociada no `welcome`?
  5. Foi só uma atualização do pydantic mudando a forma do JSON Schema, sem mudar o fio? Diga isso no commit.
Depois atualize ESQUEMA_CONGELADO e HASH_POR_TIPO com a razão na mensagem do commit.
"""


def _hash(dados: object) -> str:
    return hashlib.sha256(json.dumps(dados, sort_keys=True).encode()).hexdigest()[:16]


def _esquemas() -> dict[str, dict[str, object]]:
    modelos: dict[str, type[BaseModel]] = {**protocolo.UPSTREAM, **protocolo.DOWNSTREAM}
    esq = {nome: modelos[nome].model_json_schema() for nome in sorted(modelos)}
    esq["EnvioDeMidia"] = protocolo.EnvioDeMidia.model_json_schema()
    return esq


def test_o_esquema_do_fio_e_o_congelado() -> None:
    esq = _esquemas()
    por_tipo = {nome: _hash(e) for nome, e in esq.items()}
    mudaram = sorted(n for n in set(por_tipo) | set(HASH_POR_TIPO) if por_tipo.get(n) != HASH_POR_TIPO.get(n))
    assert not mudaram, f"mensagens com esquema diferente do congelado: {mudaram}\n{CHECKLIST_DE_MUDANCA}"
    assert _hash(esq) == ESQUEMA_CONGELADO, CHECKLIST_DE_MUDANCA


def test_as_tabelas_de_despacho_nao_trocam_tipo_de_mao() -> None:
    """Uma mensagem que migra de `UPSTREAM` para `DOWNSTREAM` (ou some) muda quem a valida, sem mudar esquema."""
    assert sorted(protocolo.UPSTREAM) == ["ack", "exec_ack", "exec_result", "heartbeat", "hello", "observe_result",
                                          "progress", "result"]
    assert sorted(protocolo.DOWNSTREAM) == ["cancel", "dispatch", "exec", "exec_cancel", "exec_result_ack", "limits",
                                            "observe_image", "refused", "result_ack", "welcome"]
    for nome, modelo in {**protocolo.UPSTREAM, **protocolo.DOWNSTREAM}.items():
        assert modelo.model_fields["type"].default == nome, nome


def test_as_constantes_do_fio_sao_as_congeladas() -> None:
    atuais = {nome: getattr(protocolo, nome) for nome in CONSTANTES_CONGELADAS}
    diferentes = {n: (atuais[n], v) for n, v in CONSTANTES_CONGELADAS.items() if atuais[n] != v}
    assert not diferentes, f"constante do fio mudou (atual, congelada): {diferentes}\n{CHECKLIST_DE_MUDANCA}"


def test_os_modelos_toleram_campo_desconhecido_menos_o_envio_de_midia() -> None:
    """`extra="ignore"` é o que deixa um lado mais novo mandar campo que o mais velho não conhece. A exceção é
    deliberada e conhecida: `EnvioDeMidia` recusa campo novo (relatório 04 §2.1)."""
    modelos = {**protocolo.UPSTREAM, **protocolo.DOWNSTREAM, "EnvioDeMidia": protocolo.EnvioDeMidia,
               "WorkerDevice": protocolo.WorkerDevice, "WorkerResources": protocolo.WorkerResources,
               "ContadorAgregado": protocolo.ContadorAgregado}
    extras = {nome: m.model_config.get("extra") for nome, m in modelos.items()}
    assert extras.pop("EnvioDeMidia") == "forbid"
    assert set(extras.values()) == {"ignore"}, extras


# ---------------------------------------------------------------- nomes antigos: os mesmos objetos
def _publicos(mod: object) -> list[str]:
    return sorted(n for n in vars(mod) if not n.startswith("_") and n != "annotations")


@pytest.mark.parametrize("nome", protocolo_antigo.__all__)
def test_o_protocolo_antigo_reexporta_o_mesmo_objeto(nome: str) -> None:
    """Identidade, não cópia: `api._tratar_mensagem_do_worker` despacha por `isinstance(msg, Heartbeat)`, e uma
    classe redefinida em `app.workers.protocol` faria a mensagem não cair em ramo nenhum, sem erro nenhum."""
    assert getattr(protocolo_antigo, nome) is getattr(protocolo, nome)


def test_o_protocolo_antigo_so_reexporta() -> None:
    assert _publicos(protocolo_antigo) == sorted(protocolo_antigo.__all__), "definição nova no módulo antigo"
    assert set(protocolo_antigo.__all__) <= set(_publicos(protocolo))
    msg = protocolo_antigo.parse_upstream({"type": "heartbeat"})
    assert isinstance(msg, protocolo.Heartbeat) and type(msg) is protocolo_antigo.Heartbeat


@pytest.mark.parametrize("nome", _publicos(verbos))
def test_o_vocabulario_de_verbos_do_central_e_o_do_contrato(nome: str) -> None:
    assert getattr(verbos_do_central, nome) is getattr(verbos, nome)
