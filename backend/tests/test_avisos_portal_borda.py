"""29.97, lado da Canais: o aviso do vigia da borda do site (laço do Portal). Contrato combinado com o Portal em 05/10
04:25Z: `avisar_borda_do_portal(codigo, onde, agora, *, achado=None, horas_sem_conferir=None) -> ContatoAvisado`, uma
mensagem por código e dia UTC, nível 2 que sai na hora, sem agrupar, sem link e fora do Trello; a resposta do dono vai à
orquestradora como recado, nunca vira pedido.

Prova `simulated` (`arquivo::teste`): o montador puro e o serviço com banco de teste e canal falso.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.modules.avisos.application.entrada import rotear
from app.modules.avisos.domain import portal as p
from app.modules.avisos.domain.mensagem import AGORA, ALGO_FALHOU, ROTULOS, entrega_do_tipo, nivel_do_tipo
from app.modules.avisos.infrastructure.entrada import RESPOSTA_DO_REPASSE

from .test_avisos_servico import CanalFalso, Relogio, _backend, _cfg, _volta

AGORA_UTC = datetime(2026, 10, 5, 4, 30, tzinfo=timezone.utc)
BEACON = "static.cloudflareinsights.com/beacon.min.js"


def _linhas(db) -> list[dict]:  # type: ignore[no-untyped-def]
    return [dict(r) for r in db.query("SELECT chave, tipo, estado FROM avisos_entregas ORDER BY id")]


# ===================================================================== 1. o montador
@pytest.mark.parametrize("codigo", sorted(p.BORDA))
def test_cada_codigo_tem_assunto_critico_e_gesto_no_molde(codigo: str) -> None:
    a = p.aviso_da_borda(codigo, "raiz", AGORA_UTC)
    assert a is not None
    assert a.tipo == p.TIPO_DA_BORDA and a.chave == f"portal-borda:{codigo}:2026-10-05"
    assert a.titulo == "ANA: " + p.BORDA[codigo].titulo and a.link is None
    linhas = a.corpo.split("\n")
    assert len(linhas) == 3 and linhas[0].startswith("A página inicial do site ")
    assert linhas[1].startswith("Crítico: ") and linhas[2].startswith("Espera você: ")


def test_o_script_sai_com_host_e_caminho_e_o_cookie_com_o_nome() -> None:
    a = p.aviso_da_borda("script_injetado", "painel", AGORA_UTC, achado=BEACON)
    assert a is not None and a.corpo.startswith(f"O painel chegou com um script que a página não tem: {BEACON}.")
    c = p.aviso_da_borda("cookie", "raiz", AGORA_UTC, achado="__cf_bm")
    assert c is not None and c.corpo.startswith("A página inicial do site chegou pondo cookie (__cf_bm).")


@pytest.mark.parametrize(("achado", "sobra"), [
    # Releitura do #381: recusado o achado inteiro, sai só o host se ele passar no filtro (aqui, o do beacon).
    (f"{BEACON}?token=abc", ": static.cloudflareinsights.com"),
    ("__cf_bm=valor", ""), ("a b", ""), ("x" * 121, ""), (42, ""), ("", "")])
def test_achado_fora_do_formato_some_sem_recusar(achado: object, sobra: str) -> None:
    """O beacon tem token na query, e o cookie tem valor: só sai host e caminho, ou o nome, e nunca a query."""
    a = p.aviso_da_borda("script_injetado", "raiz", AGORA_UTC, achado=achado)
    assert a is not None
    assert a.corpo.startswith(f"A página inicial do site chegou com um script que a página não tem{sobra}.")
    assert "token" not in a.corpo and "valor" not in a.corpo


def test_sem_conferir_diz_ha_quantas_horas() -> None:
    a = p.aviso_da_borda(p.SEM_CONFERIR, "raiz", AGORA_UTC, horas_sem_conferir=3)
    assert a is not None and a.tipo == p.TIPO_DA_BORDA_SEM_CONFERIR
    assert a.titulo == "ANA: 🌐 Site: não consigo conferir a página há 3 h"
    assert a.chave == "portal-borda:sem_conferir:2026-10-05"
    # B3 da leitura do #381: sem conferir não é defeito visto; nada de "Crítico" e não espera o dono.
    assert "Crítico" not in a.corpo and "Espera você" not in a.corpo
    assert a.corpo.split("\n")[1] == "Pode ser o caminho do central até a internet, e não o site."
    assert a.corpo.split("\n")[2].startswith("Não espera você:")


@pytest.mark.parametrize(("codigo", "onde", "horas"), [
    ("outro", "raiz", None), ("script_injetado", "pagina", None), ("script_injetado", None, None),
    (p.SEM_CONFERIR, "raiz", None), (p.SEM_CONFERIR, "raiz", 0), (p.SEM_CONFERIR, "raiz", True),
    (None, "raiz", None)])
def test_fora_do_contrato_nao_ha_aviso(codigo: object, onde: object, horas: object) -> None:
    assert p.aviso_da_borda(codigo, onde, AGORA_UTC, horas_sem_conferir=horas) is None


def test_um_por_codigo_por_dia_utc() -> None:
    hoje = p.chave_da_borda("cookie", AGORA_UTC)
    assert p.chave_da_borda("cookie", AGORA_UTC + timedelta(hours=19)) == hoje       # 23:30Z, o mesmo dia
    assert p.chave_da_borda("cookie", AGORA_UTC + timedelta(hours=20)) != hoje       # 00:30Z do dia seguinte
    assert p.chave_da_borda("csp_ausente", AGORA_UTC) != hoje


def test_nivel_2_sai_na_hora_sem_agrupar_e_fora_do_trello() -> None:
    for tipo in (p.TIPO_DA_BORDA, p.TIPO_DA_BORDA_SEM_CONFERIR):
        assert nivel_do_tipo(tipo) == ALGO_FALHOU and entrega_do_tipo(tipo) == AGORA
        assert tipo.startswith("portal.")             # `SEM_AGRUPAR`: o corpo agrupado falaria da caixa de Pendências
        assert tipo not in ROTULOS                    # o espelho do Trello só cria cartão de tipo com rótulo


@pytest.mark.parametrize("achado", ["10.0.0.5", "10.0.0.5/beacon.js", "192.168.1.20",
                                    # B1 da leitura do #381: o IP dentro do nome, em forma curta e em decimal
                                    "10.0.0.5.nip.io/beacon.js", "cdn/10.0.0.5./x", "10-0-0-5.sslip.io", "127.1",
                                    "2130706433"])
def test_o_texto_nunca_leva_ip(achado: str) -> None:
    for codigo in ("script_injetado", "cookie"):
        a = p.aviso_da_borda(codigo, "raiz", AGORA_UTC, achado=achado)
        assert a is not None and achado not in a.titulo + a.corpo


# ===================================================================== 2. a resposta do dono
@pytest.mark.parametrize("texto", ["sim", "não mudei nada na zona", "desliguei o RUM"])
def test_a_resposta_ao_aviso_vai_a_orquestradora_e_nunca_vira_pedido(texto: str) -> None:
    i = rotear(texto, fato="portal-borda:script_injetado:2026-10-05")
    assert i.tipo == "orquestradora" and i.repasse == "borda"
    assert i.texto is not None and texto in i.texto and "script_injetado" in i.texto
    assert RESPOSTA_DO_REPASSE["borda"].startswith("Recado sobre o site guardado para a orquestradora")


# ===================================================================== 3. o serviço
def test_enfileira_uma_vez_por_dia_e_sai_na_hora(tmp_path: Path) -> None:
    canal = CanalFalso()
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=canal)
    ok = p.ContatoAvisado(True, None)
    assert servico.avisar_borda_do_portal("script_injetado", "raiz", AGORA_UTC, achado=BEACON) == ok
    assert servico.avisar_borda_do_portal("script_injetado", "raiz", AGORA_UTC + timedelta(hours=1)) == ok
    assert servico.avisar_borda_do_portal("cookie", "raiz", AGORA_UTC, achado="__cf_bm") == ok
    assert [(x["chave"], x["tipo"]) for x in _linhas(db)] == [
        ("portal-borda:script_injetado:2026-10-05", p.TIPO_DA_BORDA), ("portal-borda:cookie:2026-10-05", p.TIPO_DA_BORDA)]
    for _ in range(3):
        _volta(servico)
    # Uma mensagem por código, nunca agrupadas: cada uma com o seu gesto.
    assert [t for t, _c, _l in canal.enviados] == ["ANA: " + p.BORDA["script_injetado"].titulo,
                                                   "ANA: " + p.BORDA["cookie"].titulo]
    assert all(link is None for _t, _c, link in canal.enviados)


def test_recusas_e_falha_sem_gravar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())
    invalido = p.ContatoAvisado(False, p.CAMPO_INVALIDO)
    assert servico.avisar_borda_do_portal("outro", "raiz", AGORA_UTC) == invalido
    assert servico.avisar_borda_do_portal(p.SEM_CONFERIR, "raiz", AGORA_UTC) == invalido
    desligado, db2, _ = _backend(_cfg(tmp_path / "b", ligado=False), "a", Relogio())
    assert desligado.avisar_borda_do_portal("cookie", "raiz", AGORA_UTC) == p.ContatoAvisado(False, p.CANAL_DESLIGADO)
    # B2 da leitura do #381: o `agora` sem fuso faria o dia UTC da chave depender do fuso do processo.
    assert servico.avisar_borda_do_portal("cookie", "raiz", AGORA_UTC.replace(tzinfo=None)) == invalido
    assert _linhas(db) == [] and _linhas(db2) == []

    def quebra(aviso: object) -> bool:
        raise RuntimeError("banco fora")

    monkeypatch.setattr(servico.fila, "enfileirar", quebra)
    assert servico.avisar_borda_do_portal("cookie", "raiz", AGORA_UTC) == p.ContatoAvisado(False, p.FALHA_INTERNA)


def test_agora_sem_fuso_e_recusado() -> None:
    """B2 da leitura do #381: o Python lê o `datetime` ingênuo como hora local do processo."""
    ingenuo = AGORA_UTC.replace(tzinfo=None)
    assert p.aviso_da_borda("script_injetado", "raiz", ingenuo, achado=BEACON) is None
    assert p.aviso_da_borda(p.SEM_CONFERIR, "raiz", ingenuo, horas_sem_conferir=3) is None
    with pytest.raises(ValueError, match="sem fuso"):
        p.chave_da_borda("cookie", ingenuo)


@pytest.mark.parametrize("achado", ["10.0.0.5/beacon.js", "10.0.0.5.nip.io/x", "a b", None])
def test_achado_recusado_nao_cala_o_aviso(tmp_path: Path, achado: object) -> None:
    """Pergunta da orquestradora (05/10 06:51Z): o achado que o filtro recusa (o host do script é um IP) some do texto,
    e o aviso de script injetado SAI assim mesmo; um `campo_invalido` aqui calaria um defeito visto."""
    servico, db, _ = _backend(_cfg(tmp_path), "a", Relogio(), canal=CanalFalso())
    assert servico.avisar_borda_do_portal("script_injetado", "painel", AGORA_UTC, achado=achado) == p.ContatoAvisado(
        True, None)
    assert len(_linhas(db)) == 1


@pytest.mark.parametrize(("achado", "causa"), [
    ("borda-502", " (código: borda-502)."), ("tempo-esgotado", " (código: tempo-esgotado)."),
    ("api-500", " (código: api-500)."), (None, " (tempo esgotado ou erro no caminho)."),
    ("10.0.0.5", " (tempo esgotado ou erro no caminho).")])
def test_sem_conferir_diz_o_codigo_do_vigia_pelo_filtro(achado: object, causa: str) -> None:
    a = p.aviso_da_borda(p.SEM_CONFERIR, "raiz", AGORA_UTC, achado=achado, horas_sem_conferir=2)
    assert a is not None and a.corpo.split("\n")[0].endswith(causa)


# ===================================================================== 4. as notas da releitura do #381
@pytest.mark.parametrize("achado", ["10_0_0_5.nip.io/x", "cdn-2130706433.io/y", "cdn/x_3232235777/y", "0x7f000001/x"])
def test_ip_com_sublinhado_decimal_longo_e_hexadecimal_nao_sai(achado: str) -> None:
    a = p.aviso_da_borda("script_injetado", "raiz", AGORA_UTC, achado=achado)
    assert a is not None
    for pedaco in ("10_0_0_5", "2130706433", "3232235777", "0x7f000001"):
        assert pedaco not in a.titulo + a.corpo


@pytest.mark.parametrize("codigo", ["borda-502", "api-404", "tempo-esgotado"])
def test_o_codigo_do_vigia_continua_passando(codigo: str) -> None:
    a = p.aviso_da_borda(p.SEM_CONFERIR, "raiz", AGORA_UTC, achado=codigo, horas_sem_conferir=1)
    assert a is not None and f"(código: {codigo})" in a.corpo


def test_achado_recusado_pela_versao_sai_so_com_o_host() -> None:
    a = p.aviso_da_borda("script_injetado", "painel", AGORA_UTC, achado="cdn.exemplo.com/jquery-3.6.0.min.js")
    assert a is not None and ": cdn.exemplo.com." in a.corpo and "3.6.0" not in a.corpo


def test_fuso_que_nao_diz_o_deslocamento_e_recusado() -> None:
    from datetime import tzinfo

    class SemDeslocamento(tzinfo):
        def utcoffset(self, dt: datetime | None) -> timedelta | None:
            return None

    agora = AGORA_UTC.replace(tzinfo=SemDeslocamento())
    assert agora.tzinfo is not None
    assert p.aviso_da_borda("cookie", "raiz", agora, achado="__cf_bm") is None
    with pytest.raises(ValueError, match="sem fuso"):
        p.chave_da_borda("cookie", agora)
