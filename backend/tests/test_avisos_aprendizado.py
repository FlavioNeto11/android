"""Item 28.14 — o `learning.needs_person` (30.21) no aviso fora do painel e a chave de deduplicação comum.

Prova `simulated`: o evento é o que `AvisoDeEspera.como_dados` monta (o payload real do Livro), canal e relógio falsos,
sem Telegram. Combinado com a frente Aprendizado em 02/10: só a entrada na espera avisa, só a faixa C por padrão, a
saída é no-op, `desde` identifica a espera e nenhum identificador do item vai para fora.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.modules.avisos.domain.mensagem import CAMINHO_DA_CAIXA, aviso_de_evento, chave_do_fato
from app.modules.learning.domain.espera import AvisoDeEspera, Faixa

from .test_avisos_servico import AQUI, CanalFalso, Relogio, _backend, _cfg

PAINEL = "http://painel.local:8000"


def _dados(*, faixa: Faixa = Faixa.C, aguardando: bool = True, desde: str = "2026-10-02T21:00:00+00:00",
           ref: str = "rec-42") -> dict[str, object]:
    aviso = AvisoDeEspera(kind="recipe", ref=ref, app="com.qa.messenger", faixa=faixa, aguardando=aguardando,
                          motivo="efeito_externo" if aguardando else "decidido", desde=desde)
    return aviso.como_dados()


def test_entrada_na_faixa_c_vira_aviso_sem_identificador_do_item() -> None:
    aviso = aviso_de_evento("learning.needs_person", _dados(), 5, PAINEL)
    assert aviso is not None
    assert aviso.tipo == "learning.needs_person"
    assert aviso.chave == "learning:recipe:rec-42:2026-10-02T21:00:00+00:00"
    assert aviso.link == PAINEL + "/" + CAMINHO_DA_CAIXA
    texto = " ".join((aviso.titulo, aviso.corpo, aviso.link or ""))
    for proibido in ("rec-42", "recipe", "com.qa.messenger", "efeito_externo", "/aprendizado"):
        assert proibido not in texto, f"{proibido!r} foi para fora na mensagem"


def test_saida_da_espera_nao_avisa_e_faixa_b_so_com_a_config() -> None:
    assert aviso_de_evento("learning.needs_person", _dados(aguardando=False), 6, PAINEL) is None
    assert aviso_de_evento("learning.needs_person", _dados(faixa=Faixa.B), 7, PAINEL) is None
    com_b = aviso_de_evento("learning.needs_person", _dados(faixa=Faixa.B), 7, PAINEL, frozenset({"B", "C"}))
    assert com_b is not None and com_b.chave.startswith("learning:")


def test_payload_incompleto_nao_vira_aviso() -> None:
    for falta in ("kind", "ref", "desde"):
        dados = _dados()
        dados.pop(falta)
        assert aviso_de_evento("learning.needs_person", dados, 8, PAINEL) is None, falta


def test_chave_comum_por_familia_para_os_eventos_que_avisam() -> None:
    chaves = {
        aviso.chave.split(":", 1)[0]
        for aviso in (
            aviso_de_evento("approval.pending", {"approval": {"id": "ap1"}}, 1),
            aviso_de_evento("run.updated", {"run": {"id": "r1", "status": "needs_input"}}, 2),
            aviso_de_evento("session.needs_person", {"active": True}, 3),
            aviso_de_evento("pedido.aviso", {"aviso": {"id": "av1", "tipo": "pergunta", "requer_pessoa": True}}, 4),
            aviso_de_evento("learning.needs_person", _dados(), 5),
        ) if aviso is not None
    }
    assert chaves == {"approval", "run", "session", "pedido", "learning"}
    assert chave_do_fato("run", "r1", "needs_input") == "run:r1:needs_input"


def test_mesma_espera_reemitida_sai_uma_vez_e_reentrada_sai_de_novo(tmp_path: Path) -> None:
    r, canal = Relogio(), CanalFalso()
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, r, canal=canal)
    assert servico.enfileirar_evento("learning.needs_person", _dados(), 10) is True
    assert servico.enfileirar_evento("learning.needs_person", _dados(), 11) is False, "a mesma espera virou duas"
    assert servico.enfileirar_evento("learning.needs_person", _dados(aguardando=False), 12) is False
    assert servico.enfileirar_evento("learning.needs_person", _dados(desde="2026-10-02T22:00:00+00:00"), 13) is True
    asyncio.run(servico.entregar_uma_vez())
    asyncio.run(servico.entregar_uma_vez())
    assert [t for t, _c, _l in canal.enviados] == ["ANA: Um conhecimento aprendido espera a sua revisão"] * 2
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 2


def test_faixa_b_ligada_pela_config_do_servico(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    servico, _banco, _ = _backend(cfg, AQUI, Relogio(), canal=CanalFalso())
    assert servico.enfileirar_evento("learning.needs_person", _dados(faixa=Faixa.B), 20) is False
    cfg.file.avisos.aprendizado_faixas = ["B", "C"]
    assert servico.enfileirar_evento("learning.needs_person", _dados(faixa=Faixa.B), 21) is True
