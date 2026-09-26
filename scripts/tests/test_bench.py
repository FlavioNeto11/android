"""scripts/bench.py: sem tocar produção. O modo leitura fala com um transporte FALSO (httpx.MockTransport) que
recusa qualquer método que não seja GET; a comparação é função pura sobre .jsonl escritos aqui; o modo simulado
roda um cenário curto com o harness (aparelho falso, provedor simulado, banco temporário)."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

_spec = importlib.util.spec_from_file_location("bench", ROOT / "scripts" / "bench.py")
bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bench)  # type: ignore[union-attr]


# ------------------------------------------------------------------------------------------ loopback
@pytest.mark.parametrize("base", ["http://127.0.0.1:8000", "http://localhost:8000/", "http://[::1]:8000",
                                  "https://127.0.0.1"])
def test_aceita_so_loopback(base: str) -> None:
    assert bench.exigir_loopback(base) == base.rstrip("/")


@pytest.mark.parametrize("base", ["http://192.168.1.19:8000", "http://exemplo.com", "http://127.0.0.1.exemplo.com",
                                  "file:///etc/passwd", "ftp://127.0.0.1", "127.0.0.1:8000"])
def test_recusa_base_que_nao_e_loopback(base: str) -> None:
    with pytest.raises(SystemExit, match="recusado"):
        bench.exigir_loopback(base)


def test_cliente_de_leitura_nao_tem_como_escrever() -> None:
    c = bench.ClienteSoLeitura("http://127.0.0.1:8000", transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    try:
        assert not any(hasattr(c, m) for m in ("post", "put", "patch", "delete", "request"))
    finally:
        c.close()


# ------------------------------------------------------------------------------------------ leitura
SEGREDOS = ("notebook-do-dono", "C:\\\\Users", "emulator-5554", "http://192.168.1.19:4723", "qa-user-01",
            "Abra o Instagram")


def _respostas() -> dict[str, Any]:
    return {
        "/api/health": {"service": "android-farm-central", "status": "degraded", "version": "0.1.0",
                        "commit": "57a155f", "migration": "041_loja_de_apps", "database": {"dialect": "sqlite"},
                        "ai": {"provider": "anthropic", "model": "claude-sonnet-5", "simulated": False,
                               "configured": True, "recipes": "replay", "flows": True, "image_policy": "auto",
                               "notice": "texto longo", "spend_today_usd": 0.42},
                        "problems": [{"code": "worker_offline", "message": "notebook-do-dono sem batida",
                                      "hint": "C:\\Users\\x"}],
                        "features": {"hibernation": True, "system_image": "system-images;android-34"}},
        "/api/settings": {"max_online_devices": 10, "boot_parallelism": 2, "capture_grid_interval_s": 5,
                          "capture_focus_interval_s": 1, "max_ai_concurrency": 4, "max_active_devices": 10},
        "/api/usage": {"groups": [
            {"role": "decide", "model": "claude-sonnet-5", "tier": 0, "calls": 30, "fresh": 1000, "cache_read": 9000,
             "cache_write": 100, "output": 500, "with_image": 20, "errors": 1, "avg_ms": 3000, "usd": 0.5},
            {"role": "decide", "model": "claude-opus-5", "tier": 1, "calls": 10, "fresh": 100, "cache_read": 0,
             "cache_write": 0, "output": 50, "with_image": 10, "errors": 0, "avg_ms": 7000, "usd": 0.3},
            {"role": "verify", "model": "claude-haiku-4-5", "tier": 0, "calls": 20, "fresh": 10, "cache_read": 0,
             "cache_write": 0, "output": 5, "with_image": 20, "errors": 0, "avg_ms": 1500, "usd": 0.1}],
            "total_usd": 0.9, "fallbacks": [], "spend_today_usd": 0.42, "objectives_with_ai": 6,
            "calls_per_objective": 10.0, "usd_per_objective": 0.15, "steps_driven_by": {"ai": 20, "recipe": 5},
            "errors_by_kind": {"timeout": 1}, "unpriced_models": [], "cache_inativo": []},
        "/api/diagnostics": {"collected_at": "2026-09-26T20:00:00.000Z",
                             "host": {"os": "Windows", "cpu": "Xeon", "cores_logical": 16, "mem_total_gb": 64,
                                      "disk_project": "C:\\Users\\dono — 100 GB livres"},
                             "tools": [{"name": "Android Emulator", "found": True, "version": "36.1.9",
                                        "path": "C:\\Users\\dono\\emulator.exe"}],
                             "sdk": {"root": "C:\\Users\\dono\\sdk", "system_images": ["system-images;android-34"],
                                     "configured_image": "system-images;android-34"},
                             "acceleration": {"usable": True, "detail": "WHPX usable", "raw": "C:\\Users\\bruto"},
                             "capacity": {"running_emulators": 3, "per_instance_gb": 3.1, "note": "texto"},
                             "measurements": [
                                 {"ts": "t", "kind": "cold", "instance_id": "android-01", "boot_seconds": 90},
                                 {"ts": "t", "kind": "cold", "instance_id": "android-02", "boot_seconds": 110},
                                 {"ts": "t", "kind": "warm", "instance_id": "android-02", "boot_seconds": 20},
                                 {"ts": "t", "kind": "hibernate", "saved": True, "save_seconds": 15},
                                 {"ts": "t", "kind": "clock", "skew_s": 2}]},
        "/api/workers": [{"id": "notebook-do-dono", "name": "notebook-do-dono", "local": False, "state": "online",
                          "connected": True, "max_slots": 4, "appium_url": "http://192.168.1.19:4723",
                          "state_detail": "notebook-do-dono ok", "resources": {"cpu_percent": 30, "ram_free_mb": 900},
                          "devices": [{"serial": "emulator-5554", "state": "online", "system_image": "android-34"}]},
                         {"id": "central", "name": "central", "local": True, "state": "online", "devices": []}],
        "/api/metrics": {"ts": "t", "cpu_percent": 55.0, "mem_total_gb": 64, "mem_available_gb": 20,
                         "mem_used_percent": 69.0, "emulators": [{"instance_id": "android-01", "pid": 1,
                                                                   "rss_mb": 3000, "cpu_percent": 20}]},
    }


def test_leitura_so_faz_get_filtra_e_registra_indisponivel(tmp_path: Path) -> None:
    vistos: list[tuple[str, str]] = []
    respostas = _respostas()

    def handler(req: httpx.Request) -> httpx.Response:
        vistos.append((req.method, req.url.path))
        assert req.method == "GET", "a bancada de leitura nunca escreve"
        if req.url.path in respostas:
            return httpx.Response(200, json=respostas[req.url.path])
        return httpx.Response(404, json={"error": {"code": "not_found"}})   # /api/desempenho no commit antigo

    caminho = bench.leitura("http://127.0.0.1:8000", dias=7, saida=tmp_path, transport=httpx.MockTransport(handler))
    assert {m for m, _ in vistos} == {"GET"}
    assert ("GET", "/api/desempenho") in vistos
    texto = caminho.read_text(encoding="utf-8")
    for s in SEGREDOS:
        assert s not in texto, f"vazou {s!r}"
    linha = json.loads(texto)
    assert linha["prova"] == "real" and linha["modo"] == "leitura"
    assert linha["fontes"]["desempenho"] == {"http": 404, "indisponivel": True}      # ausente ≠ zero
    assert linha["fontes"]["health"]["status"] == "degraded" and linha["fontes"]["health"]["http"] == 200
    assert linha["meta"]["commit_medido"] == "57a155f"
    assert linha["meta"]["emulador"]["versao"] == "36.1.9"
    assert linha["meta"]["carga"]["aparelhos_por_estado"] == {"online": 1}
    m = linha["medidas"]
    assert m["ia.chamadas"] == 60 and m["ia.usd_periodo"] == 0.9 and m["health.problemas"] == 1
    assert m["ia.avg_ms.decide"] == 4025.6                     # (29×3000 + 10×7000) / 39: média das OK, não p50
    assert m["ia.usd.decide"] == 0.8 and m["ia.tokens.cache_lido"] == 9000 and m["ia.fallback_chamadas"] == 0
    assert m["diag.boot_cold_s.n"] == 2 and m["diag.boot_warm_s.p50"] == 20.0
    assert m["diag.hibernar_salvo_s.p50"] == 15.0
    assert m["host.rss_emuladores_mb_soma"] == 3000.0
    assert caminho.with_suffix(".txt").exists()


def test_leitura_com_backend_fora_do_ar_nao_explode(tmp_path: Path) -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("recusado", request=req)

    linha = json.loads(bench.leitura("http://127.0.0.1:1", dias=1, saida=tmp_path,
                                     transport=httpx.MockTransport(handler)).read_text(encoding="utf-8"))
    assert linha["fontes"]["health"] == {"http": 0, "indisponivel": True}
    assert linha["medidas"] == {}


def test_leitura_recusa_base_remota_antes_de_qualquer_requisicao(tmp_path: Path) -> None:
    chamou = []
    with pytest.raises(SystemExit):
        bench.leitura("http://192.168.1.19:8000", dias=7, saida=tmp_path,
                      transport=httpx.MockTransport(lambda r: chamou.append(r) or httpx.Response(200)))
    assert chamou == [] and not list(tmp_path.iterdir())


# ------------------------------------------------------------------------------------------ comparar
def _jsonl(p: Path, medidas: list[dict[str, float]], *, variante: str = "v", prova: str = "simulated",
           parametros: dict[str, Any] | None = None, criterio: dict[str, Any] | None = None) -> Path:
    linhas = [{"modo": "simulado", "prova": prova, "cenario": "c", "variante": variante, "repeticao": i + 1,
               "parametros": parametros or {"x": 1}, "medidas": m,
               "meta": {"sha": "abc", "criterio_de_ganho": criterio or {"limite_rel": 0.1, "amostra_min": 3}}}
              for i, m in enumerate(medidas)]
    p.write_text("\n".join(json.dumps(ln) for ln in linhas) + "\n", encoding="utf-8")
    return p


def _item(c: dict[str, Any], medida: str) -> dict[str, Any]:
    return next(i for i in c["itens"] if i["medida"] == medida)


def test_ganho_so_com_limite_amostra_e_sem_sobreposicao(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"screencaps": v, "objetivos_ok": 1, "ia_chamadas": 13} for v in (100, 102, 98)])
    d = _jsonl(tmp_path / "d.jsonl", [{"screencaps": v, "objetivos_ok": 1, "ia_chamadas": 12.5}
                                      for v in (10, 11, 9)])
    c = bench.comparar(a, d)
    assert c["criterio"] == {"limite_rel": 0.1, "amostra_min": 3, "sobrescrito": False,
                             "gravado_na_linha_de_base": {"limite_rel": 0.1, "amostra_min": 3}}
    assert _item(c, "screencaps")["veredito"] == "ganho"
    assert _item(c, "objetivos_ok")["veredito"] == "sem diferença"
    assert _item(c, "ia_chamadas")["veredito"] == "sem diferença acima do limite"      # −3,8 % < 10 %


def test_amostra_pequena_e_exploratoria_mesmo_com_diferenca_enorme(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"screencaps": 100}, {"screencaps": 100}])
    d = _jsonl(tmp_path / "d.jsonl", [{"screencaps": 1}, {"screencaps": 1}])
    assert _item(bench.comparar(a, d), "screencaps")["veredito"] == "exploratório"
    # afrouxar a amostra na hora de comparar é possível, mas fica marcado como sobrescrito
    c = bench.comparar(a, d, amostra_min=2)
    assert c["criterio"]["sobrescrito"] is True and _item(c, "screencaps")["veredito"] == "ganho"


def test_sobreposicao_e_piora_e_neutra(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"screencaps": v, "objetivos_ok": 1, "etapas_por_recipe": 0}
                                      for v in (10, 50, 90)])
    d = _jsonl(tmp_path / "d.jsonl", [{"screencaps": v, "objetivos_ok": 0, "etapas_por_recipe": 4}
                                      for v in (5, 60, 20)])
    c = bench.comparar(a, d)
    assert _item(c, "screencaps")["veredito"] == "exploratório"              # −35 %, mas se sobrepõem
    assert _item(c, "objetivos_ok")["veredito"] == "piora"                   # menos objetivos certos nunca é ganho
    assert _item(c, "etapas_por_recipe")["veredito"] == "diferença (neutra)"


def test_incomparavel_com_parametros_ou_prova_diferentes(tmp_path: Path) -> None:
    a = _jsonl(tmp_path / "a.jsonl", [{"screencaps": 1}] * 3, parametros={"duracao_s": 6})
    d = _jsonl(tmp_path / "d.jsonl", [{"screencaps": 1}] * 3, parametros={"duracao_s": 12})
    assert bench.comparar(a, d)["itens"][0]["veredito"] == "incomparável"
    r = _jsonl(tmp_path / "r.jsonl", [{"screencaps": 1}] * 3, parametros={"duracao_s": 6}, prova="real")
    assert bench.comparar(a, r)["itens"][0]["motivo"] == "níveis de prova diferentes"


def test_veredito_com_base_zero() -> None:
    assert bench.veredito([0, 0, 0], [0, 0, 0], limite_rel=0.1, amostra_min=3, direcao="menor")[0] == "sem diferença"
    assert bench.veredito([0, 0, 0], [3, 4, 5], limite_rel=0.1, amostra_min=3, direcao="menor")[0] == "piora"


# ------------------------------------------------------------------------------------------ CLI e simulado
def test_sem_subcomando_e_o_simulado(monkeypatch: pytest.MonkeyPatch) -> None:
    chamado: dict[str, Any] = {}
    monkeypatch.setattr(bench, "simulado", lambda **kw: chamado.update(kw))
    assert bench.main([]) == 0
    assert chamado["cenarios"] == ["previa", "imagem", "receitas"] and chamado["repeticoes"] == 3
    assert chamado["limite_rel"] == 0.1 and chamado["amostra_min"] == 3


def test_cenario_previa_conta_screencaps_do_aparelho_falso() -> None:
    r = asyncio.run(bench.cenario_previa(2, 1.5, 1.0, "always"))
    assert r["parametros"]["entrada"] == "_capture_loop" and r["parametros"]["aparelhos"] == 2
    assert r["medidas"]["screencaps"] >= 2 and r["extras"]["objetivos_ativos_no_inicio"] == 0
    assert r["config"]["capture_grid_interval_s"] == 1.0


def test_cenario_imagem_auto_manda_menos_imagem_e_conclui() -> None:
    r = asyncio.run(bench.cenario_imagem("auto"))
    m = r["medidas"]
    assert m["objetivos_ok"] == 1 and m["ia_chamadas_com_imagem"] < m["ia_chamadas"]
    assert m["tokens_informados"] == 0                  # o simulado não informa tokens; proxy tem nome de proxy
    assert r["config"]["image_policy"] == "auto" and r["config"]["rich_tree_min_elements"] == 3
