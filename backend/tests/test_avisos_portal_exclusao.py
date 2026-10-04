"""28.34: a exclusão de um contato do site a pedido do titular (29.83) no lado do canal: `apagar_avisos_do_portal`.

Prova `simulated`: canal falso que devolve `message_id` e apaga, sem rede. O que se prova:
- a fila primeiro e para sempre: `pendente`, `falhou` e `incerto` viram `descartado` sem corpo; sem linha, entra a lápide;
  o `avisar_contato_do_portal` de depois vira no-op e nada sai;
- `enviando` devolve `em_envio` e não mexe em nada;
- as mensagens do bot e as respostas do dono são apagadas do chat dentro de 47 h; o texto das respostas sai do banco;
- a mais velha, a de canal desligado, a que o Telegram recusa ou que falha na rede, e a que pode ter saído sem registro
  vão para `a_mao`, com a hora;
- erro de banco é `falhou`, e o log nunca leva o conteúdo.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

import pytest

from app.modules.avisos.domain import portal as p
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.util import to_iso

from .test_avisos_servico import CanalFalso, Relogio, _backend, _cfg, _volta

NOME, TELEFONE, TEXTO = "Visitante Teste", "+55 (11) 98765-4321", "Quero uma proposta para a minha empresa."
RESPOSTA = "Respondo a Visitante Teste amanhã"


class CanalQueApaga(CanalFalso):
    """Devolve um `message_id` por envio (vira `canal_enviadas`) e apaga pelo id, como o `CanalTelegram`."""

    def __init__(self, *, recusa: set[int] | None = None, quebra: set[int] | None = None) -> None:
        super().__init__()
        self.proximo = 500
        self.apagados: list[int] = []
        self.recusa = recusa or set()
        self.quebra = quebra or set()

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> int:  # type: ignore[override]
        await super().enviar(titulo, corpo, link)
        self.proximo += 1
        return self.proximo

    async def apagar(self, message_id: int) -> bool:
        if message_id in self.quebra:
            raise OSError("rede fora")
        if message_id in self.recusa:
            return False
        self.apagados.append(message_id)
        return True


def _contato(cid: int = 7) -> p.ContatoDoPortal:
    return p.ContatoDoPortal(contato_id=cid, nome=NOME, empresa=None, telefone=TELEFONE, mensagem=TEXTO)


def _apagar(servico, cid: int, agora) -> p.ApagadoNoCanal:  # type: ignore[no-untyped-def]
    return asyncio.run(servico.apagar_avisos_do_portal(cid, agora))


def _linha(db, chave: str = "portal:7") -> dict:  # type: ignore[no-untyped-def]
    r = db.one("SELECT estado, corpo, link FROM avisos_entregas WHERE chave=?", (chave,))
    return dict(r) if r is not None else {}


def _resposta_do_dono(db, r: Relogio, responde_a: str, ref: str = "900") -> None:  # type: ignore[no-untyped-def]
    EntradasDoCanal(db, relogio=r).gravar(id_externo=f"u{ref}", ordem=int(ref), tipo="mensagem", do_dono=True,
                                          ref_mensagem=ref, responde_a=responde_a, texto=RESPOSTA, tamanho=len(RESPOSTA))


def test_pendente_vira_descartado_sem_corpo_e_nada_sai_depois(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalQueApaga()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=canal)
    assert servico.avisar_contato_do_portal(_contato()).enfileirado
    assert _apagar(servico, 7, r.t) == p.ApagadoNoCanal(p.APAGADO_OK, 0, ())
    assert _linha(db) == {"estado": "descartado", "corpo": "", "link": None}
    assert servico.avisar_contato_do_portal(_contato()).enfileirado        # a chave já existe: no-op
    for _ in range(3):
        _volta(servico)
    assert canal.enviados == [] and _linha(db)["estado"] == "descartado"


def test_sem_linha_entra_a_lapide_e_o_reenvio_do_portal_vira_no_op(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalQueApaga()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=canal)
    assert _apagar(servico, 7, r.t).estado == p.APAGADO_OK
    assert _linha(db) == {"estado": "descartado", "corpo": "", "link": None}
    assert servico.avisar_contato_do_portal(_contato()).enfileirado         # o laço de reenvio no meio da exclusão
    for _ in range(3):
        _volta(servico)
    assert canal.enviados == []
    assert db.scalar("SELECT COUNT(*) FROM avisos_entregas") == 1


def test_enviado_apaga_a_mensagem_e_a_resposta_do_dono_e_tira_o_texto(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalQueApaga()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=canal)
    servico.avisar_contato_do_portal(_contato())
    _volta(servico)
    assert len(canal.enviados) == 1
    _resposta_do_dono(db, r, responde_a="501")
    _resposta_do_dono(db, r, responde_a="999", ref="901")                   # resposta a outra mensagem: fica
    r.avancar(3600)
    assert _apagar(servico, 7, r.t) == p.ApagadoNoCanal(p.APAGADO_OK, 2, ())
    assert canal.apagados == [501, 900]
    assert db.scalar("SELECT texto FROM canal_entradas WHERE ref_mensagem='900'") is None
    assert db.scalar("SELECT texto FROM canal_entradas WHERE ref_mensagem='901'") == RESPOSTA
    assert _linha(db) == {"estado": "enviado", "corpo": "", "link": None}


def test_mais_velha_que_47_horas_e_canal_desligado_vao_para_a_mao(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalQueApaga()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=canal)
    servico.avisar_contato_do_portal(_contato())
    _volta(servico)
    enviada = to_iso(r.t)
    assert _apagar(servico, 7, r.t + timedelta(hours=47, minutes=1)) == p.ApagadoNoCanal(p.APAGADO_OK, 0, (enviada,))
    assert canal.apagados == []
    desligado, db2, _ = _backend(_cfg(tmp_path / "b", ligado=False), "a", r, db=db)
    assert _apagar(desligado, 7, r.t) == p.ApagadoNoCanal(p.APAGADO_OK, 0, (enviada,))


def test_recusa_do_telegram_e_erro_de_rede_vao_para_a_mao_sem_falhar(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalQueApaga(recusa={501}, quebra={900})
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=canal)
    servico.avisar_contato_do_portal(_contato())
    _volta(servico)
    _resposta_do_dono(db, r, responde_a="501")
    resultado = _apagar(servico, 7, r.t)
    assert resultado.estado == p.APAGADO_OK and resultado.apagadas == 0 and len(resultado.a_mao) == 2


def test_enviando_devolve_em_envio_e_nao_mexe_em_nada(tmp_path: Path) -> None:
    r = Relogio()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=CanalQueApaga())
    servico.avisar_contato_do_portal(_contato())
    db.execute("UPDATE avisos_entregas SET estado='enviando', iniciado_em=? WHERE chave='portal:7'", (to_iso(r.t),))
    corpo = _linha(db)["corpo"]
    assert _apagar(servico, 7, r.t) == p.ApagadoNoCanal(p.APAGADO_EM_ENVIO)
    assert _linha(db) == {"estado": "enviando", "corpo": corpo, "link": None} and corpo


@pytest.mark.parametrize("estado", ["incerto", "falhou"])
def test_incerto_e_falhou_viram_descartado_e_o_incerto_vai_para_a_mao(tmp_path: Path, estado: str) -> None:
    r = Relogio()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=CanalQueApaga())
    servico.avisar_contato_do_portal(_contato())
    iniciado = to_iso(r.t - timedelta(minutes=5))
    db.execute("UPDATE avisos_entregas SET estado=?, iniciado_em=? WHERE chave='portal:7'", (estado, iniciado))
    resultado = _apagar(servico, 7, r.t)
    assert resultado.estado == p.APAGADO_OK
    assert resultado.a_mao == ((iniciado,) if estado == "incerto" else ())  # a incerta pode estar no chat sem registro
    assert _linha(db) == {"estado": "descartado", "corpo": "", "link": None}


def test_enviado_sem_message_id_vai_para_a_mao(tmp_path: Path) -> None:
    r = Relogio()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=CanalFalso())   # o falso antigo não devolve o id
    servico.avisar_contato_do_portal(_contato())
    _volta(servico)
    assert _apagar(servico, 7, r.t) == p.ApagadoNoCanal(p.APAGADO_OK, 0, (to_iso(r.t),))


def test_erro_de_banco_e_falhou_e_o_log_nao_leva_conteudo(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    r = Relogio()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=CanalQueApaga())
    servico.avisar_contato_do_portal(_contato())

    def quebra(fato: str) -> int:
        raise RuntimeError(f"banco fora: {NOME} {TELEFONE} {TEXTO}")

    monkeypatch.setattr(servico.fila, "tirar_texto_das_respostas", quebra)
    with caplog.at_level(logging.DEBUG, logger="poc.avisos"):
        assert _apagar(servico, 7, r.t) == p.ApagadoNoCanal(p.APAGADO_FALHOU)
    log = caplog.text + "".join(str(x.exc_info) for x in caplog.records)
    assert "7" in caplog.text and NOME not in log and "98765" not in log and TEXTO not in log


def test_so_toca_a_chave_do_contato(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalQueApaga()
    servico, db, _ = _backend(_cfg(tmp_path), "a", r, canal=canal)
    servico.avisar_contato_do_portal(_contato(7))
    servico.avisar_contato_do_portal(_contato(8))
    _apagar(servico, 7, r.t)
    assert _linha(db, "portal:8")["estado"] == "pendente" and _linha(db, "portal:8")["corpo"]


def test_janela_de_apagar() -> None:
    agora = Relogio().t
    assert p.da_para_apagar(to_iso(agora - timedelta(hours=46, minutes=59)), agora)
    assert not p.da_para_apagar(to_iso(agora - timedelta(hours=47)), agora)
    assert not p.da_para_apagar("2026-10-04T10:00:00", agora)                 # sem fuso
    assert not p.da_para_apagar(None, agora) and not p.da_para_apagar("lixo", agora)
