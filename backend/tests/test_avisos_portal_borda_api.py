"""29.101, lado da Canais: a API do central aberta sem login pelo endereço público, o oitavo código do vigia da borda
(`api_aberta`, `onde=api`). Contrato com o Portal e texto aprovado pela orquestradora em 05/10 06:56Z: tipo próprio
`portal.borda_api` no nível 1 (sai na hora e espera o dono), a chave da borda por dia UTC, sem link, fora do Trello, e a
resposta do dono vai à orquestradora pelo repasse `borda`.

Prova `simulated` (`arquivo::teste`): o montador puro e o serviço com banco de teste e canal falso.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.avisos.application.entrada import rotear
from app.modules.avisos.domain import portal as p
from app.modules.avisos.domain.mensagem import AGORA, PRECISA_DE_VOCE, ROTULOS, entrega_do_tipo, nivel_do_tipo

from .test_avisos_servico import CanalFalso, Relogio, _backend, _cfg, _volta

AGORA_UTC = datetime(2026, 10, 5, 4, 30, tzinfo=timezone.utc)


def _linhas(db) -> list[dict]:  # type: ignore[no-untyped-def]
    return [dict(r) for r in db.query("SELECT chave, tipo, estado FROM avisos_entregas ORDER BY id")]


# ===================================================================== 1. o montador
def test_o_aviso_da_api_aberta_no_texto_aprovado() -> None:
    a = p.aviso_da_borda(p.API_ABERTA, p.ONDE_DA_API, AGORA_UTC, achado="/api/instances")
    assert a is not None and a.tipo == p.TIPO_DA_BORDA_API and a.link is None
    assert a.chave == "portal-borda:api_aberta:2026-10-05"
    assert a.titulo == "ANA: 🔓 Site: a API do central respondeu sem login pelo endereço público"
    assert a.corpo.split("\n") == [
        "Chegou: um pedido sem login a /api/instances, pelo nome público, foi atendido.",
        "Crítico: quem estiver na internet consegue ler dados do central sem senha. Escrita não foi testada.",
        "Espera você: pare o serviço do túnel (Cloudflared) no central. Se preferir, responda a esta mensagem: a ANA "
        "repassa à orquestradora, que confere de fora e para o túnel, mas isso só funciona com uma sessão ativa."]


@pytest.mark.parametrize("achado", [None, "/api/instances", "10.0.0.5", "dev.exemplo.com.br/api/instances"])
def test_o_achado_nao_entra_no_texto(achado: object) -> None:
    """O único caminho citado é `/api/instances`: nada de host nem IP, venha o que vier no achado."""
    a = p.aviso_da_borda(p.API_ABERTA, p.ONDE_DA_API, AGORA_UTC, achado=achado)
    assert a is not None and a.corpo == "\n".join(p.CORPO_DA_API)
    assert not re.search(r"\d+\.\d+\.\d+\.\d+|https?:|exemplo", a.titulo + a.corpo)


@pytest.mark.parametrize(("codigo", "onde"), [
    (p.API_ABERTA, "raiz"), (p.API_ABERTA, None), ("script_injetado", p.ONDE_DA_API), ("cookie", p.ONDE_DA_API)])
def test_api_so_com_api(codigo: str, onde: object) -> None:
    """O `onde=api` é só do `api_aberta`, e o `api_aberta` só vale com ele: fora disso, `campo_invalido`."""
    assert p.aviso_da_borda(codigo, onde, AGORA_UTC) is None


def test_nivel_1_sai_na_hora_sem_agrupar_e_fora_do_trello() -> None:
    assert nivel_do_tipo(p.TIPO_DA_BORDA_API) == PRECISA_DE_VOCE and entrega_do_tipo(p.TIPO_DA_BORDA_API) == AGORA
    assert p.TIPO_DA_BORDA_API.startswith("portal.")   # `SEM_AGRUPAR`, e o corpo some no estado final
    assert p.TIPO_DA_BORDA_API not in ROTULOS          # o espelho do Trello só cria cartão de tipo com rótulo


def test_a_resposta_vai_a_orquestradora() -> None:
    i = rotear("parei o túnel", fato="portal-borda:api_aberta:2026-10-05")
    assert i.tipo == "orquestradora" and i.repasse == "borda" and i.texto is not None and "api_aberta" in i.texto


# ===================================================================== 2. o serviço
def test_enfileira_uma_vez_por_dia_e_sai_na_hora(tmp_path: Path) -> None:
    canal = CanalFalso()
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=canal)
    ok = p.ContatoAvisado(True, None)
    for horas in (0, 1, 5):
        assert servico.avisar_borda_do_portal(p.API_ABERTA, p.ONDE_DA_API, AGORA_UTC + timedelta(hours=horas),
                                              achado="/api/instances") == ok
    assert [(x["chave"], x["tipo"]) for x in _linhas(db)] == [("portal-borda:api_aberta:2026-10-05", p.TIPO_DA_BORDA_API)]
    _volta(servico)
    assert [t for t, _c, _l in canal.enviados] == ["ANA: " + p.TITULO_DA_API]
    assert servico.avisar_borda_do_portal(p.API_ABERTA, "raiz", AGORA_UTC) == p.ContatoAvisado(False, p.CAMPO_INVALIDO)


# ===================================================================== 3. a API sem conferir (leitura do #383)
@pytest.mark.parametrize(("achado", "olhar"), [("api-404", False), ("api-desafio", False), ("api-500", True),
                                               (None, False), ("10.0.0.5", False)])
def test_a_api_sem_conferir_fala_da_api_e_nao_espera(achado: object, olhar: bool) -> None:
    """Orquestradora, 05/10 07:16Z: o vigia que não confere a API manda `sem_conferir` com `onde=api`; o texto é da API
    (o site pode estar perfeito), diz há quantas horas e o motivo, não espera o dono, e no `api-500` pede um olhar."""
    a = p.aviso_da_borda(p.SEM_CONFERIR, p.ONDE_DA_API, AGORA_UTC, achado=achado, horas_sem_conferir=2)
    assert a is not None and a.tipo == p.TIPO_DA_BORDA_SEM_CONFERIR and a.link is None
    assert a.titulo == "ANA: 🌐 Site: não consigo conferir a API do central há 2 h"
    linhas = a.corpo.split("\n")
    causa = f" (código: {achado})" if achado in ("api-404", "api-desafio", "api-500") else ""
    assert linhas[0] == f"Há 2 h o vigia não completa a conferência da API do central pelo nome público{causa}."
    assert ("vale olhar" in a.corpo) is olhar
    assert linhas[-1].startswith("Não espera você:") and "Crítico" not in a.corpo and "10.0.0.5" not in a.corpo


def test_a_api_sem_conferir_tem_chave_propria_no_dia() -> None:
    """O "sem conferir" do site no mesmo dia não engole o da API, e vice-versa."""
    site = p.aviso_da_borda(p.SEM_CONFERIR, "raiz", AGORA_UTC, horas_sem_conferir=1)
    api = p.aviso_da_borda(p.SEM_CONFERIR, p.ONDE_DA_API, AGORA_UTC, horas_sem_conferir=1)
    assert site is not None and api is not None
    assert (site.chave, api.chave) == ("portal-borda:sem_conferir:2026-10-05", "portal-borda:sem_conferir_api:2026-10-05")
