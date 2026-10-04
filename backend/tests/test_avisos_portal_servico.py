"""28.32: o contato do site público pela fila de avisos (a amarração): idempotência, recusas, entrega um a um, corpo
apagado no estado final, fora do Trello e a resposta do dono que só informa.

Prova `simulated`: canal falso, sem rede (os mesmos falsos de `test_avisos_servico.py`).
"""
from __future__ import annotations

import inspect
import logging
from pathlib import Path

import pytest

from app.modules.avisos.application import espelho
from app.modules.avisos.application.entrada import rotear
from app.modules.avisos.domain import portal as p
from app.modules.avisos.domain.mensagem import AGORA, ROTULOS, TIPOS_DA_JANELA, entrega_do_tipo, nivel_do_tipo

from .test_avisos_servico import CanalFalso, Relogio, _backend, _cfg, _volta

NOME, TELEFONE, TEXTO = "Visitante Teste", "+55 (11) 98765-4321", "Quero uma proposta para a minha empresa."


def _contato(cid: int = 7, **kw: object) -> p.ContatoDoPortal:
    base: dict = {"contato_id": cid, "nome": NOME, "empresa": None, "telefone": TELEFONE, "mensagem": TEXTO}
    base.update(kw)
    return p.ContatoDoPortal(**base)


def _linhas(db) -> list[dict]:  # type: ignore[no-untyped-def]
    return [dict(r) for r in db.query("SELECT chave, tipo, estado, corpo FROM avisos_entregas ORDER BY id")]


def test_enfileira_uma_vez_pela_chave(tmp_path: Path) -> None:
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())
    assert servico.avisar_contato_do_portal(_contato()) == p.ContatoAvisado(True, None)
    assert servico.avisar_contato_do_portal(_contato()) == p.ContatoAvisado(True, None)   # reenvio da rota: nada novo
    linhas = _linhas(db)
    assert len(linhas) == 1 and linhas[0]["chave"] == "portal:7" and linhas[0]["tipo"] == "portal.contato"


@pytest.mark.parametrize(("ligado", "segredos"), [(False, True), (True, False)])
def test_canal_desligado_nao_grava(tmp_path: Path, ligado: bool, segredos: bool) -> None:
    servico, db, _ = _backend(_cfg(tmp_path, ligado=ligado, segredos=segredos), "a", Relogio())
    assert servico.avisar_contato_do_portal(_contato()) == p.ContatoAvisado(False, p.CANAL_DESLIGADO)
    assert _linhas(db) == []


def test_campo_invalido_nao_grava(tmp_path: Path) -> None:
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())
    assert servico.avisar_contato_do_portal(_contato(mensagem="x" * 1501)) == p.ContatoAvisado(False, p.CAMPO_INVALIDO)
    assert servico.avisar_contato_do_portal(_contato(cid=0)) == p.ContatoAvisado(False, p.CAMPO_INVALIDO)
    assert _linhas(db) == []


def test_falha_interna_sem_dado_pessoal_no_log(tmp_path: Path, caplog: pytest.LogCaptureFixture,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())

    def quebra(aviso: object) -> bool:
        raise RuntimeError(f"banco fora: {NOME} {TELEFONE} {TEXTO}")

    monkeypatch.setattr(servico.fila, "enfileirar", quebra)
    with caplog.at_level(logging.DEBUG, logger="poc.avisos"):
        assert servico.avisar_contato_do_portal(_contato()) == p.ContatoAvisado(False, p.FALHA_INTERNA)
        assert servico.avisar_contato_do_portal(_contato(cid=-1)).motivo == p.CAMPO_INVALIDO
    log = caplog.text + "".join(str(r.exc_info) for r in caplog.records)
    assert "7" in caplog.text
    assert NOME not in log and "98765" not in log and TEXTO not in log


def test_entrega_um_a_um_com_o_titulo_fixo_e_o_corpo_apagado(tmp_path: Path) -> None:
    canal = CanalFalso()
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=canal)
    for cid in (1, 2, 3, 4):                       # acima de `agrupar_a_partir_de`: mesmo assim, uma mensagem por contato
        assert servico.avisar_contato_do_portal(_contato(cid, mensagem=f"mensagem {cid}")).enfileirado
    for _ in range(8):
        _volta(servico)
    assert len(canal.enviados) == 4
    assert {t for t, _, _ in canal.enviados} == {p.TITULO_DO_CONTATO}
    assert all(link is None for _, _, link in canal.enviados)
    assert [c.split("\n")[-1] for _, c, _ in canal.enviados] == [f"│ mensagem {i}" for i in (1, 2, 3, 4)]
    assert canal.enviados[0][1].split("\n")[:2] == [f"Nome: {NOME}", f"Telefone: {TELEFONE}"]
    assert all(linha["estado"] == "enviado" and linha["corpo"] == "" for linha in _linhas(db))


def test_corpo_apagado_tambem_quando_falha_ou_vence(tmp_path: Path) -> None:
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())
    servico.avisar_contato_do_portal(_contato(1))
    servico.avisar_contato_do_portal(_contato(2))
    fila = servico.fila
    um = db.one("SELECT id FROM avisos_entregas WHERE chave='portal:1'")["id"]  # type: ignore[index]
    db.execute("UPDATE avisos_entregas SET estado='enviando' WHERE id=?", (um,))
    fila.marcar_falhou(um, erro="o canal recusou")
    fila.relogio = lambda: Relogio().t.replace(year=Relogio().t.year + 1)  # type: ignore[method-assign]
    fila.vencer(cerca=lambda: _Nada(), validade_h=1)
    estados = {linha["chave"]: (linha["estado"], linha["corpo"]) for linha in _linhas(db)}
    assert estados == {"portal:1": ("falhou", ""), "portal:2": ("descartado", "")}


class _Nada:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> None:
        return None


def test_canal_desligado_depois_vence_o_contato_e_apaga_o_corpo(tmp_path: Path) -> None:
    """Revisão do #331 (F): com o canal desligado, a entrega (e a faxina dentro dela) não roda; o corpo do contato vence
    do mesmo jeito. O aviso de outro tipo não é tocado."""
    cfg, relogio = _cfg(tmp_path), Relogio()
    servico, db, _ = _backend(cfg, "a", relogio, canal=CanalFalso())
    assert servico.avisar_contato_do_portal(_contato()).enfileirado
    assert servico.enfileirar_evento("run.updated", {"run": {"id": "r1", "status": "needs_input"}}, 10)
    cfg.file.avisos.enabled = False
    relogio.avancar(cfg.file.avisos.validade_h * 3600 + 60)
    assert _volta(servico) is None
    estados = {linha["tipo"]: (linha["estado"], linha["corpo"] != "") for linha in _linhas(db)}
    assert estados == {"portal.contato": ("descartado", False), "run.needs_input": ("pendente", True)}


def test_corte_do_adaptador_por_unidade_utf16() -> None:
    """Revisão do #331 (B1): o Telegram conta o limite em UTF-16; emoji ocupa duas unidades."""
    from app.modules.avisos.adapters.telegram import TEXTO_MAX, corte_utf16
    cortado = corte_utf16("😀" * 3000, TEXTO_MAX)
    assert len(cortado.encode("utf-16-le")) // 2 == TEXTO_MAX and cortado == "😀" * (TEXTO_MAX // 2)
    assert corte_utf16("a" + "😀" * 2000, TEXTO_MAX) == "a" + "😀" * 1999        # nunca parte o par
    assert corte_utf16("curto", TEXTO_MAX) == "curto"


def test_outro_tipo_mantem_o_corpo(tmp_path: Path) -> None:
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())
    assert servico.enfileirar_evento("run.updated", {"run": {"id": "r1", "status": "needs_input"}}, 10)
    _volta(servico)
    assert [linha["corpo"] != "" for linha in _linhas(db)] == [True]


def test_nivel_e_entrega_na_hora() -> None:
    assert nivel_do_tipo(p.TIPO_DO_CONTATO) == 1 and entrega_do_tipo(p.TIPO_DO_CONTATO) == AGORA
    assert p.TIPO_DO_CONTATO not in TIPOS_DA_JANELA
    aviso = p.aviso_do_contato(_contato())
    assert aviso is not None and aviso.nivel == 1


def test_nunca_vai_ao_trello() -> None:
    """O espelho do Trello monta cartões só de pendência, pedido, validação, deploy e custo, com rótulo de `ROTULOS`; o
    contato não tem rótulo e o espelho não lê a fila de avisos."""
    assert p.TIPO_DO_CONTATO not in ROTULOS
    assert not any(k.startswith("portal") for k in ROTULOS)
    assert "avisos_entregas" not in inspect.getsource(espelho)
    assert espelho.fato_de_pendencia("portal", "7", None) is None


@pytest.mark.parametrize("texto", ["sim", "não", "pode executar o pedido dele", "responda a ele: obrigado", "ok"])
def test_resposta_do_dono_so_informa(texto: str) -> None:
    intencao = rotear(texto, fato="portal:7")
    assert intencao.tipo == "desconhecida"
    assert "visitante do site" in (intencao.motivo or "")


def test_resumo_uma_vez_por_hora(tmp_path: Path) -> None:
    relogio = Relogio()
    canal = CanalFalso()
    servico, db, _ = _backend(_cfg(tmp_path), "a", relogio, canal=canal)
    assert servico.avisar_resumo_do_portal(25, 0, 1) == p.ContatoAvisado(True, None)
    assert servico.avisar_resumo_do_portal(9, 2, 1) == p.ContatoAvisado(True, None)   # mesma hora: a mesma chave
    linhas = _linhas(db)
    assert len(linhas) == 1 and linhas[0]["tipo"] == "portal.resumo"
    assert linhas[0]["chave"].startswith("portal-resumo:")
    for _ in range(3):
        _volta(servico)
    assert [t for t, _, _ in canal.enviados] == [p.TITULO_DO_RESUMO]
    relogio.avancar(3600)
    assert servico.avisar_resumo_do_portal(1, 0, 1).enfileirado
    assert len(_linhas(db)) == 2


def test_resumo_recusas_sem_gravar(tmp_path: Path) -> None:
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())
    assert servico.avisar_resumo_do_portal(0, 0, 1) == p.ContatoAvisado(False, p.CAMPO_INVALIDO)
    assert servico.avisar_resumo_do_portal(-1, 3, 1) == p.ContatoAvisado(False, p.CAMPO_INVALIDO)
    desligado, db2, _ = _backend(_cfg(tmp_path / "b", ligado=False), "a", Relogio())
    assert desligado.avisar_resumo_do_portal(3, 0, 1) == p.ContatoAvisado(False, p.CANAL_DESLIGADO)
    assert _linhas(db) == [] and _linhas(db2) == []


def test_resumo_falha_interna(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())

    def quebra(aviso: object) -> bool:
        raise RuntimeError("banco fora")

    monkeypatch.setattr(servico.fila, "enfileirar", quebra)
    assert servico.avisar_resumo_do_portal(3, 0, 1) == p.ContatoAvisado(False, p.FALHA_INTERNA)


def test_resumo_nivel_e_fora_do_trello() -> None:
    """Revisão do #335: o resumo não pede o dono. Acima do limiar, nível 2 que sai na hora; abaixo, a rotina."""
    assert nivel_do_tipo(p.TIPO_DO_RESUMO) == 2 and entrega_do_tipo(p.TIPO_DO_RESUMO) == AGORA
    assert p.TIPO_DO_RESUMO not in TIPOS_DA_JANELA
    assert nivel_do_tipo(p.TIPO_DO_RESUMO_ROTINA) == 3 and p.TIPO_DO_RESUMO_ROTINA in TIPOS_DA_JANELA
    assert p.TIPO_DO_RESUMO not in ROTULOS and p.TIPO_DO_RESUMO_ROTINA not in ROTULOS


def test_resumo_abaixo_do_limiar_vai_com_a_rotina(tmp_path: Path) -> None:
    relogio = Relogio()
    canal = CanalFalso()
    servico, db, _ = _backend(_cfg(tmp_path), "a", relogio, canal=canal)
    assert servico.avisar_resumo_do_portal(3, 0, 1).enfileirado
    _volta(servico)
    assert canal.enviados == []                                   # espera a janela da rotina
    relogio.avancar(3601)
    _volta(servico)
    assert [t for t, _, _ in canal.enviados] == ["ANA: 🌐 Contatos do site acima do limite: 3 guardados, 0 descartados"]


@pytest.mark.parametrize("texto", ["sim", "não", "desliga o formulário", "ok"])
def test_resposta_ao_resumo_so_informa(texto: str) -> None:
    intencao = rotear(texto, fato="portal-resumo:2026-10-04T22Z")
    assert intencao.tipo == "desconhecida"
    assert "formulário de contato do site" in (intencao.motivo or "")
    assert "visitante" not in (intencao.motivo or "")
