"""Bancada de desempenho (evolução de desempenho, frente F1): linha de base reproduzível e comparação antes × depois.

Modos (nenhum dispara ação no parque, chamada paga de IA ou adb):

  simulado (padrão)  No próprio processo, com o harness de testes (backend/tests/conftest.py): aparelho FALSO,
                     provedor simulado, banco temporário, portas a partir de 5640 e SDK apontado para uma pasta
                     que não existe (nenhum binário do adb pode ser executado). Mede CONTAGENS: screencaps,
                     chamadas de IA, etapas por receita. Prova `simulated` — não é tempo real, nem US$ real, nem
                     densidade de emuladores.
  leitura            Só GET num backend de LOOPBACK (health, settings, usage, diagnostics, workers, metrics e
                     desempenho, se existir). Prova `real`, somente leitura. Guarda números e metadados não
                     sensíveis, por lista branca: nunca texto de comando, conversa, perfil, caminho ou credencial.
  comparar A B       Antes × depois. Só declara ganho quando a diferença passa do limite E da amostra mínima
                     definidos ANTES (gravados na linha de base); senão diz "exploratório".

Uso (da raiz do repositório):
  backend/.venv/Scripts/python.exe scripts/bench.py simulado [--repeticoes 3] [--cenarios previa,imagem,receitas]
  backend/.venv/Scripts/python.exe scripts/bench.py leitura --base http://127.0.0.1:8000 [--dias 7]
  backend/.venv/Scripts/python.exe scripts/bench.py comparar data/bench/antes.jsonl data/bench/depois.jsonl

Saída: data/bench/ (ignorado pelo Git; `--saida` muda), um .jsonl (uma linha por cenário × variante × repetição)
e um .txt com o resumo curto.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
SAIDA_PADRAO = ROOT / "data" / "bench"
ESQUEMA = "bench/1"
AVISO_SIMULADO = ("CONTAGENS simuladas (prova `simulated`): aparelho falso e provedor simulado. Não é tempo real, "
                  "nem US$ real, nem densidade de emuladores.")
AVISO_LEITURA = ("Leitura real somente-GET (prova `real`, somente leitura) de um backend de loopback. Números "
                 "agregados; nada foi disparado.")
#: Critério de ganho padrão. Fica gravado na linha de base: quem compara depois usa o que foi declarado ANTES
#: de olhar o resultado, e não um limite escolhido para caber no número que saiu.
LIMITE_REL_PADRAO = 0.10
AMOSTRA_MIN_PADRAO = 3
#: Prefixos de `metricas` (contrato C5) que a bancada lê. Outras frentes os instrumentam; o que ainda não existe
#: simplesmente não aparece (ausente ≠ zero).
PREFIXOS_METRICAS = ("captura.", "codificacao.", "observacao.", "receita.", "pathfinder.", "capacidade.")
HOSTS_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


# ============================================================================================ metadados
def _git(*args: str) -> str | None:
    try:
        r = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def sistema() -> dict[str, Any]:
    ram = None
    try:
        import psutil  # noqa: PLC0415 - existe no venv do backend; fora dele, RAM fica desconhecida

        ram = round(psutil.virtual_memory().total / 2**30, 1)
    except Exception:  # noqa: BLE001
        pass
    return {"so": platform.platform(), "cpus_logicas": os.cpu_count(), "ram_total_gb": ram,
            "python": platform.python_version()}


def meta_comum() -> dict[str, Any]:
    status = _git("status", "--porcelain")
    return {"sha": _git("rev-parse", "HEAD"), "arvore_suja": None if status is None else bool(status),
            "data_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "sistema": sistema()}


def _arquivo_saida(saida: Path, modo: str, meta: dict[str, Any]) -> Path:
    saida.mkdir(parents=True, exist_ok=True)
    carimbo = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return saida / f"{modo}-{carimbo}-{(meta.get('sha') or 'semgit')[:7]}.jsonl"


def _gravar(caminho: Path, linhas: list[dict[str, Any]], resumo_txt: str) -> None:
    with caminho.open("w", encoding="utf-8") as fh:
        for ln in linhas:
            fh.write(json.dumps(ln, ensure_ascii=False, sort_keys=True) + "\n")
    caminho.with_suffix(".txt").write_text(resumo_txt, encoding="utf-8")


# ============================================================================================ simulado
def _backend_no_caminho() -> None:
    if str(BACKEND) not in sys.path:
        sys.path.insert(0, str(BACKEND))


def _importar_harness() -> Any:
    """O harness é o dos testes, importado como pacote (`tests.conftest`), com `backend/` no caminho."""
    _backend_no_caminho()
    from tests import conftest  # noqa: PLC0415
    from app.planning.simulated_provider import SimulatedProvider  # noqa: PLC0415

    class ContadorDeProvedor(conftest.CountingProvider):
        """O contador do harness mais PROXIES do tamanho da requisição. O provedor simulado não informa tokens
        (`Usage()` vazio); bytes de imagem e linhas de hierarquia são o sinal de tamanho que sobra — e ficam com
        nome de proxy, para ninguém ler como token."""

        async def decide(self, req: Any) -> Any:
            r = await super().decide(req)
            self.calls[-1].update(_tamanho(req.screen))
            return r

        async def verify(self, req: Any) -> Any:
            r = await super().verify(req)
            self.calls[-1].update(_tamanho(req.screen))
            return r

    conftest.ContadorDeProvedor = ContadorDeProvedor
    conftest.SimuladoDoBench = SimulatedProvider
    return conftest


def _tamanho(screen: Any) -> dict[str, int]:
    jpeg = getattr(screen, "jpeg", None) or b""
    linhas = list(getattr(screen, "elements", None) or [])
    return {"img_bytes": len(jpeg), "linhas": len(linhas), "chars": sum(len(x) for x in linhas)}


@contextlib.asynccontextmanager
async def _harness(n: int) -> AsyncIterator[tuple[Any, Any]]:
    """Um harness NOVO por variante: pasta, banco e contadores próprios — nada vaza de uma medição para a outra."""
    conftest = _importar_harness()
    with tempfile.TemporaryDirectory(prefix="bench-", ignore_cleanup_errors=True) as d:
        tmp = Path(d)
        # `db_dsn` explícito: sem ele, `make_config` obedeceria `TEST_DATABASE_URL` e criaria schemas no
        # PostgreSQL de teste que ninguém apagaria (a faxina é do `pytest_sessionfinish`, que aqui não roda).
        h = conftest.Harness(tmp, n, db_dsn=str(tmp / "bench.sqlite3"))
        # SDK numa pasta que não existe: qualquer caminho que escapasse do aparelho falso para o adb falharia ao
        # procurar o executável, antes de rodar qualquer coisa. A bancada não pode falar com aparelho nenhum.
        h.cfg.file.android.sdk_root = str(tmp / "sem-sdk")
        h.ai = conftest.ContadorDeProvedor(conftest.SimuladoDoBench())
        st = await h.boot()
        try:
            yield h, st
        finally:
            if h.state is not None:
                await h.state.stop()


def _config_relevante(h: Any, st: Any) -> dict[str, Any]:
    s = st.settings.get()
    ai = h.cfg.file.ai
    return {"preview_mode": getattr(s, "preview_mode", None), "capture_grid_interval_s": s.capture_grid_interval_s,
            "capture_focus_interval_s": s.capture_focus_interval_s, "max_online_devices": s.max_online_devices,
            "boot_parallelism": s.boot_parallelism, "max_ai_concurrency": s.max_ai_concurrency,
            "image_policy": ai.image_policy, "rich_tree_min_elements": ai.rich_tree_min_elements,
            "recipes": ai.recipes, "flows": ai.flows, "pathfinder_wait_s": ai.pathfinder_wait_s}


def _metricas_filtradas(snap: dict[str, Any]) -> tuple[dict[str, float], dict[str, Any]]:
    """(medidas planas por nome, detalhe por rótulo) só dos prefixos do contrato C5."""
    planas: dict[str, float] = defaultdict(float)
    detalhe: dict[str, Any] = {"contadores": [], "distribuicoes": []}
    for c in snap.get("contadores", []):
        if c["nome"].startswith(PREFIXOS_METRICAS):
            planas[f"metricas.{c['nome']}"] += c["valor"]
            detalhe["contadores"].append(c)
    for dist in snap.get("distribuicoes", []):
        if dist["nome"].startswith(PREFIXOS_METRICAS):
            planas[f"metricas.{dist['nome']}.n"] += dist["n"]
            planas[f"metricas.{dist['nome']}.soma"] += dist["soma"]
            detalhe["distribuicoes"].append(dist)
    return dict(planas), detalhe


def _medidas_de_ia(calls: list[dict[str, Any]]) -> dict[str, float]:
    m: dict[str, float] = {"ia_chamadas": len(calls), "ia_chamadas_com_imagem": 0,
                           "proxy_img_bytes": 0, "proxy_linhas_hierarquia": 0}
    for papel in ("plan", "decide", "verify"):
        m[f"ia_{papel}"] = sum(1 for c in calls if c["role"] == papel)
    for c in calls:
        m["ia_chamadas_com_imagem"] += 1 if c.get("image") else 0
        m["proxy_img_bytes"] += c.get("img_bytes", 0)
        m["proxy_linhas_hierarquia"] += c.get("linhas", 0)
    return m


def _medidas_da_execucao(h: Any, st: Any, run_id: str, instancias: list[str], calls: list[dict[str, Any]],
                         caps0: dict[str, int], ps0: dict[str, int]) -> tuple[dict[str, float], dict[str, Any]]:
    det = st.repo.run_detail(run_id)
    ok = sum(1 for o in det.objectives if o.status == "succeeded")
    driven = {r["d"]: r["n"] for r in st.db.query(
        "SELECT COALESCE(driven_by,'ai') d, COUNT(*) n FROM steps WHERE run_id=? AND status='succeeded' GROUP BY 1",
        (run_id,))}
    tokens = st.db.one("SELECT COALESCE(SUM(input_tokens + cache_read + cache_write + output_tokens), 0) t"
                       " FROM ai_calls WHERE run_id=?", (run_id,))
    m = {"objetivos": len(det.objectives), "objetivos_ok": ok,
         "screencaps": sum(h.fakes[i].calls.count("screenshot") - caps0[i] for i in instancias),
         "page_sources": sum(h.fakes[i].calls.count("page_source") - ps0[i] for i in instancias),
         **_medidas_de_ia(calls),
         # Zero por construção no simulado; está aqui para a MESMA linha servir quando houver provedor real.
         "tokens_informados": int(tokens["t"] if tokens else 0),
         "etapas_por_ai": driven.get("ai", 0), "etapas_por_recipe": driven.get("recipe", 0),
         "etapas_por_recipe_ai": driven.get("recipe+ai", 0)}
    return m, {"status_execucao": str(det.status),
               "status_objetivos": sorted(str(o.status) for o in det.objectives)}


def _contagens(h: Any, ids: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    for i in ids:
        h._factory(type("RT", (), {"id": i, "index": int(i.split("-")[-1])})())   # garante o aparelho falso
    return ({i: h.fakes[i].calls.count("screenshot") for i in ids},
            {i: h.fakes[i].calls.count("page_source") for i in ids})


async def cenario_previa(n: int, duracao_s: float, intervalo_s: float, modo: str) -> dict[str, Any]:
    """(a) Prévia SEM espectador: N aparelhos online, nenhuma execução, ninguém olhando, por T segundos.

    O harness não liga a captura sozinho (a adoção e o boot do aparelho falso voltam antes de
    `_start_online_tasks`), e `_start_online_tasks` inteiro dispararia tarefas que, fora do teste, falam com o
    aparelho. Por isso a entrada é `_capture_loop(rt)` — a mesma tarefa que a produção cria — e ela fica
    registrada em `parametros.entrada`: se a mudança de prévia mover o portão para outro lugar, o "nada mudou" é
    diagnosticável. Conta-se pelo contador do aparelho FALSO, que vale antes e depois da mudança."""
    from app.metricas import metricas  # noqa: PLC0415
    from app.models import InstanceState  # noqa: PLC0415

    async with _harness(n) as (h, st):
        st.settings.update({"capture_grid_interval_s": intervalo_s, "preview_mode": modo})
        online = [rt for rt in st.devices.devices.values() if rt.state == InstanceState.online][:n]
        ids = [rt.id for rt in online]
        ativos = st.db.scalar("SELECT COUNT(*) FROM objectives WHERE status IN ('pending','running')") or 0
        caps0, _ = _contagens(h, ids)
        seq0 = {rt.id: rt.frame_seq for rt in online}
        metricas.limpar()
        tarefas = []
        for rt in online:
            t = rt.tasks.get("capture")
            if t is None or t.done():
                rt.tasks["capture"] = t = asyncio.create_task(st.devices._capture_loop(rt), name=f"bench-{rt.id}")
            tarefas.append(t)
        await asyncio.sleep(duracao_s)
        for t in tarefas:
            t.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        caps = sum(h.fakes[i].calls.count("screenshot") - caps0[i] for i in ids)
        planas, detalhe = _metricas_filtradas(metricas.snapshot())
        return {
            "cenario": "previa_sem_espectador", "variante": f"preview_mode={modo}",
            "parametros": {"aparelhos": len(ids), "duracao_s": duracao_s, "capture_grid_interval_s": intervalo_s,
                           "entrada": "_capture_loop", "espectadores": 0, "execucoes": 0},
            "medidas": {"screencaps": caps, "frames_publicados": sum(rt.frame_seq - seq0[rt.id] for rt in online),
                        "screencaps_por_aparelho_min": round(caps / max(1, len(ids)) / (duracao_s / 60), 2),
                        **planas},
            "extras": {"objetivos_ativos_no_inicio": int(ativos), "metricas": detalhe},
            "config": _config_relevante(h, st), "carga": {"aparelhos_online": len(ids), "execucoes_simultaneas": 0},
        }


async def cenario_imagem(politica: str) -> dict[str, Any]:
    """(b) A MESMA tarefa de QA (1 aparelho) com `image_policy` auto × always. `rich_tree_min_elements=3` é o
    que o teste da alavanca usa: a árvore do aparelho falso é pequena, e com o padrão (8) `auto` nunca
    engataria — a alavanca pareceria morta por construção do dublê, não por ela."""
    from app.metricas import metricas  # noqa: PLC0415

    async with _harness(1) as (h, st):
        h.cfg.file.ai.image_policy = politica
        h.cfg.file.ai.rich_tree_min_elements = 3
        ids = ["android-01"]
        caps0, ps0 = _contagens(h, ids)
        h.ai.calls.clear()
        metricas.limpar()
        run = h.run(ids)
        await h.wait_run(run.id, timeout=120)
        medidas, extras = _medidas_da_execucao(h, st, run.id, ids, list(h.ai.calls), caps0, ps0)
        planas, detalhe = _metricas_filtradas(metricas.snapshot())
        return {"cenario": "tarefa_qa_politica_de_imagem", "variante": f"image_policy={politica}",
                "parametros": {"aparelhos": 1, "rich_tree_min_elements": 3, "tarefa": "COMMAND do harness (QA-001)"},
                "medidas": {**medidas, **planas}, "extras": {**extras, "metricas": detalhe},
                "config": _config_relevante(h, st), "carga": {"aparelhos_online": 1, "execucoes_simultaneas": 1}}


async def cenario_receitas(receitas: str, fluxos: bool) -> list[dict[str, Any]]:
    """(c) Primeira execução (aprende) × repetição (outro aparelho, mesmo comando), com receitas/fluxos ligados
    ou desligados (controle). Separa aprendizado de repetição: a primeira paga a IA inteira por construção."""
    from app.metricas import metricas  # noqa: PLC0415

    saida = []
    async with _harness(2) as (h, st):
        h.cfg.file.ai.recipes = receitas
        h.cfg.file.ai.flows = fluxos
        for fase, iid in (("primeira", "android-01"), ("repeticao", "android-02")):
            caps0, ps0 = _contagens(h, [iid])
            h.ai.calls.clear()
            metricas.limpar()
            run = h.run([iid])
            await h.wait_run(run.id, timeout=120)
            medidas, extras = _medidas_da_execucao(h, st, run.id, [iid], list(h.ai.calls), caps0, ps0)
            n_obj = max(1, medidas["objetivos"])
            medidas["ia_chamadas_por_objetivo"] = round(medidas["ia_chamadas"] / n_obj, 3)
            planas, detalhe = _metricas_filtradas(metricas.snapshot())
            saida.append({"cenario": "receitas_e_fluxos",
                          "variante": f"recipes={receitas},flows={'on' if fluxos else 'off'}:{fase}",
                          "parametros": {"aparelhos": 1, "fase": fase, "tarefa": "COMMAND do harness (QA-001)"},
                          "medidas": {**medidas, **planas}, "extras": {**extras, "metricas": detalhe},
                          "config": _config_relevante(h, st),
                          "carga": {"aparelhos_online": 2, "execucoes_simultaneas": 1}})
    return saida


async def _rodar_simulado(cenarios: list[str], repeticoes: int, n: int, duracao_s: float,
                          intervalo_s: float) -> list[dict[str, Any]]:
    _backend_no_caminho()
    linhas: list[dict[str, Any]] = []
    for rep in range(1, repeticoes + 1):
        blocos: list[dict[str, Any]] = []
        if "previa" in cenarios:
            for modo in ("on_demand", "always"):
                blocos.append(await cenario_previa(n, duracao_s, intervalo_s, modo))
        if "imagem" in cenarios:
            for politica in ("always", "auto"):
                blocos.append(await cenario_imagem(politica))
        if "receitas" in cenarios:
            blocos.extend(await cenario_receitas("replay", True))
            blocos.extend(await cenario_receitas("off", False))
        for b in blocos:
            b["repeticao"] = rep
        linhas.extend(blocos)
        print(f"  repetição {rep}/{repeticoes}: {len(blocos)} medições", flush=True)
    return linhas


def simulado(*, cenarios: list[str], repeticoes: int, aparelhos: int, duracao_s: float, intervalo_s: float,
             saida: Path, limite_rel: float, amostra_min: int) -> Path:
    logging.basicConfig(level=logging.ERROR)
    for nome in ("app", "httpx", "asyncio"):
        logging.getLogger(nome).setLevel(logging.ERROR)
    meta = meta_comum()
    print(f"bench simulado — {AVISO_SIMULADO}\n  sha={meta['sha']} suja={meta['arvore_suja']}", flush=True)
    medicoes = asyncio.run(_rodar_simulado(cenarios, repeticoes, aparelhos, duracao_s, intervalo_s))
    linhas = []
    for m in medicoes:
        linhas.append({
            "esquema": ESQUEMA, "modo": "simulado", "prova": "simulated", "aviso": AVISO_SIMULADO,
            "cenario": m["cenario"], "variante": m["variante"], "repeticao": m["repeticao"],
            "parametros": m["parametros"], "medidas": m["medidas"], "extras": m.get("extras", {}),
            "meta": {**meta, "config": m["config"], "carga": m["carga"],
                     "concorrencia": {"max_ai_concurrency": m["config"]["max_ai_concurrency"]},
                     "emulador": {"tipo": "aparelho falso (backend/tests/fake_device.py)", "versao": None,
                                  "imagem": None, "renderer": None, "aceleracao": None},
                     "repeticoes": repeticoes,
                     "criterio_de_ganho": {"limite_rel": limite_rel, "amostra_min": amostra_min}},
        })
    caminho = _arquivo_saida(saida, "simulado", meta)
    txt = resumo_texto(linhas)
    _gravar(caminho, linhas, txt)
    print(txt)
    print(f"gravado: {caminho}\n         {caminho.with_suffix('.txt')}")
    return caminho


# ============================================================================================ leitura
def exigir_loopback(base: str) -> str:
    """Recusa qualquer base que não seja desta máquina: a leitura é da produção LOCAL, nunca de outro host."""
    u = urlparse(base)
    host = (u.hostname or "").lower()
    if u.scheme not in ("http", "https") or host not in HOSTS_LOOPBACK:
        raise SystemExit(f"recusado: --base precisa ser http(s) de loopback ({', '.join(sorted(HOSTS_LOOPBACK))}); "
                         f"recebido {base!r}")
    return base.rstrip("/")


class ClienteSoLeitura:
    """Só existe `get`: não há como este cliente fazer POST/PUT/DELETE, nem por engano."""

    def __init__(self, base: str, *, transport: Any = None, timeout: float = 30.0) -> None:
        import httpx  # noqa: PLC0415

        self._http = httpx.Client(base_url=exigir_loopback(base), timeout=timeout, transport=transport)

    def get(self, caminho: str, params: dict[str, Any] | None = None, *, timeout: float | None = None) -> tuple[int, Any]:
        try:
            r = self._http.get(caminho, params=params, timeout=timeout or self._http.timeout)
        except Exception as exc:  # noqa: BLE001 - backend fora do ar é resultado, não traceback
            return 0, {"erro": type(exc).__name__}
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None

    def close(self) -> None:
        self._http.close()


def _escalares(d: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (d or {}).items() if isinstance(v, (int, float, bool, str)) or v is None}


def extrair_health(h: dict[str, Any]) -> dict[str, Any]:
    ai = h.get("ai") or {}
    return {"status": h.get("status"), "commit": h.get("commit"), "migration": h.get("migration"),
            "version": h.get("version"), "banco": (h.get("database") or {}).get("dialect"),
            "ia": {k: ai.get(k) for k in ("provider", "model", "models", "simulated", "configured", "recipes",
                                           "flows", "image_policy", "spend_today_usd", "spend_limit_day_usd",
                                           "spend_limit_run_usd", "refusal_fallback", "account_blocked")},
            "features": _escalares(h.get("features")),
            # Só os CÓDIGOS: a mensagem de um problema carrega nome de máquina, caminho e porta.
            "problemas": {"n": len(h.get("problems") or []),
                          "codigos": sorted(str(p.get("code")) for p in h.get("problems") or [])}}


def extrair_usage(u: dict[str, Any]) -> dict[str, Any]:
    campos = ("role", "model", "tier", "calls", "fresh", "cache_read", "cache_write", "output", "with_image",
              "errors", "avg_ms", "usd")
    return {"grupos": [{k: g.get(k) for k in campos} for g in u.get("groups") or []],
            "total_usd": u.get("total_usd"), "spend_today_usd": u.get("spend_today_usd"),
            "fallbacks": [{k: f.get(k) for k in ("fallback", "requested_model", "model", "calls")}
                          for f in u.get("fallbacks") or []],
            "objectives_with_ai": u.get("objectives_with_ai"), "calls_per_objective": u.get("calls_per_objective"),
            "usd_per_objective": u.get("usd_per_objective"), "steps_driven_by": u.get("steps_driven_by"),
            "errors_by_kind": u.get("errors_by_kind"), "unpriced_models": u.get("unpriced_models"),
            "cache_inativo": u.get("cache_inativo")}


def extrair_diagnostics(d: dict[str, Any]) -> dict[str, Any]:
    host = d.get("host") or {}
    return {"coletado_em": d.get("collected_at"),
            "host": {k: host.get(k) for k in ("arch", "cpu", "cores_physical", "cores_logical", "mem_total_gb",
                                               "mem_available_gb", "swap_total_gb")},
            "ferramentas": [{k: t.get(k) for k in ("name", "found", "version")} for t in d.get("tools") or []],
            "imagens": {"instaladas": (d.get("sdk") or {}).get("system_images"),
                        "configurada": (d.get("sdk") or {}).get("configured_image")},
            "aceleracao": {k: (d.get("acceleration") or {}).get(k) for k in ("usable", "detail", "hypervisor_present")},
            "capacidade": _escalares({k: v for k, v in (d.get("capacity") or {}).items() if k != "note"}),
            # A amostra de boot/hibernação que o Diagnóstico tem em CACHE (últimas <= 60 medições de qualquer tipo).
            "boot_hibernacao": _amostra_boot(d.get("measurements") or [])}


def _amostra_boot(medicoes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """O Diagnóstico espalha `{ts, kind, **data}`, e a linha de boot grava `kind` (cold/warm) DENTRO de `data`: o de
    dentro vence e a linha chega com `kind='cold'`, sem `boot`. Por isso o boot se reconhece por `boot_seconds`."""
    out = []
    for m in medicoes:
        if m.get("boot_seconds") is not None:
            out.append({"tipo": "boot", "modo": m.get("kind") if m.get("kind") in ("cold", "warm") else None,
                        "segundos": m.get("boot_seconds"), "ts": m.get("ts")})
        elif m.get("kind") == "hibernate":
            out.append({"tipo": "hibernate", "salvo": bool(m.get("saved")), "segundos": m.get("save_seconds"),
                        "ts": m.get("ts")})
    return out


def extrair_workers(ws: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    remotos = 0
    for w in ws or []:
        if not w.get("local"):
            remotos += 1
        devs = w.get("devices") or []
        por_estado: dict[str, int] = defaultdict(int)
        for dv in devs:
            por_estado[str(dv.get("state"))] += 1
        # Sem id/nome (nome de máquina), sem url do Appium, sem serial: rótulo ordinal basta para comparar.
        out.append({"rotulo": "local" if w.get("local") else f"remoto-{remotos}", "state": w.get("state"),
                    "connected": w.get("connected"), "max_slots": w.get("max_slots"), "accel": w.get("accel"),
                    "os": w.get("os"), "os_version": w.get("os_version"), "agent_version": w.get("agent_version"),
                    "agent_outdated": w.get("agent_outdated"), "transport_state": w.get("transport_state"),
                    "recursos": _escalares(w.get("resources")), "aparelhos_por_estado": dict(por_estado),
                    "imagens": sorted({str(dv.get("system_image")) for dv in devs if dv.get("system_image")})})
    return out


def extrair_metrics(m: dict[str, Any]) -> dict[str, Any]:
    emus = m.get("emulators") or []
    rss = [float(e.get("rss_mb") or 0) for e in emus]
    cpu = [float(e.get("cpu_percent") or 0) for e in emus]
    return {"ts": m.get("ts"), "cpu_percent": m.get("cpu_percent"), "mem_total_gb": m.get("mem_total_gb"),
            "mem_available_gb": m.get("mem_available_gb"), "mem_used_percent": m.get("mem_used_percent"),
            "emuladores": len(emus), "rss_emuladores_mb_soma": round(sum(rss), 1),
            "rss_emulador_mb_max": max(rss) if rss else None, "cpu_emuladores_soma": round(sum(cpu), 1)}


def extrair_desempenho(d: dict[str, Any]) -> dict[str, Any]:
    proc = d.get("processo") or {}
    return {"processo": {k: proc.get(k) for k in ("desde", "ate", "uptime_s", "contadores", "distribuicoes",
                                                  "series_descartadas")},
            "janelas_n": len(d.get("janelas") or []), "historico": d.get("historico")}


def _pct(valores: list[float], p: float) -> float | None:
    _backend_no_caminho()
    from app.metricas import percentil  # noqa: PLC0415 - a MESMA regra de percentil do backend

    return percentil(sorted(valores), p)


def medidas_da_leitura(fontes: dict[str, Any]) -> dict[str, float]:
    """Números planos (para `comparar`) tirados das fontes já filtradas. Só o que existe entra."""
    m: dict[str, float] = {}
    u = fontes.get("usage") or {}
    if u.get("http") == 200:
        grupos = u.get("grupos") or []
        m["ia.chamadas"] = sum(g.get("calls") or 0 for g in grupos)
        m["ia.erros"] = sum(g.get("errors") or 0 for g in grupos)
        if u.get("total_usd") is not None:
            m["ia.usd_periodo"] = u["total_usd"]
        if u.get("usd_per_objective") is not None:
            m["ia.usd_por_objetivo_com_ia"] = u["usd_per_objective"]
        if u.get("calls_per_objective") is not None:
            m["ia.chamadas_por_objetivo"] = u["calls_per_objective"]
        papeis: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for g in grupos:
            if g.get("calls"):
                papeis[str(g.get("role"))].append((float(g["calls"]), float(g.get("avg_ms") or 0)))
                m[f"ia.avg_ms.{g.get('role')}.{g.get('model')}.t{g.get('tier')}"] = float(g.get("avg_ms") or 0)
        for papel, pares in papeis.items():
            n = sum(c for c, _ in pares)
            m[f"ia.chamadas.{papel}"] = n
            # média ponderada pelas chamadas — é média, não p50: a GET de uso não dá distribuição
            m[f"ia.avg_ms.{papel}"] = round(sum(c * a for c, a in pares) / n, 1) if n else 0.0
        for k, v in (u.get("steps_driven_by") or {}).items():
            m[f"etapas_ok_por_{k}"] = v
    mt = fontes.get("metrics") or {}
    if mt.get("http") == 200:
        for k in ("cpu_percent", "mem_used_percent", "mem_available_gb", "emuladores", "rss_emuladores_mb_soma"):
            if mt.get(k) is not None:
                m[f"host.{k}"] = mt[k]
    h = fontes.get("health") or {}
    if h.get("http") == 200:
        m["health.problemas"] = (h.get("problemas") or {}).get("n", 0)
    dg = fontes.get("diagnostics") or {}
    if dg.get("http") == 200:
        grupos: dict[str, list[float]] = defaultdict(list)
        for b in dg.get("boot_hibernacao") or []:
            if b.get("segundos") is None:
                continue
            chave = (f"boot_{b.get('modo') or '?'}" if b["tipo"] == "boot"
                     else f"hibernar_{'salvo' if b.get('salvo') else 'nao_salvo'}")
            grupos[chave].append(float(b["segundos"]))
        for chave, vals in grupos.items():
            m[f"diag.{chave}_s.n"] = len(vals)
            m[f"diag.{chave}_s.p50"] = _pct(vals, 50)  # type: ignore[assignment]
            m[f"diag.{chave}_s.p95"] = _pct(vals, 95)  # type: ignore[assignment]
    de = fontes.get("desempenho") or {}
    hist = de.get("historico") if de.get("http") == 200 else None
    if hist:
        o = hist.get("objetivos") or {}
        for k, v in (o.get("taxas") or {}).items():
            if v is not None:
                m[f"objetivos.taxa.{k}"] = v
        for k in ("concluidos_por_hora", "usd_por_concluido", "usd_coorte"):
            if o.get(k) is not None:
                m[f"objetivos.{k}"] = o[k]
        for papel, dist in ((hist.get("ia") or {}).get("ms_por_papel") or {}).items():
            for q in ("p50", "p95"):
                if dist.get(q) is not None:
                    m[f"ia.ms.{papel}.{q}"] = dist[q]
    return {k: v for k, v in m.items() if v is not None}


def leitura(base: str, *, dias: int, saida: Path, transport: Any = None) -> Path:
    """UMA passada de GETs. Cada fonte guarda o status HTTP; 404 (rota que o commit em produção não tem) é
    registrado como indisponível — não como zero."""
    base = exigir_loopback(base)
    cliente = ClienteSoLeitura(base, transport=transport)
    fontes: dict[str, Any] = {}
    try:
        roteiro: list[tuple[str, str, dict[str, Any] | None, Callable[[Any], Any], float | None]] = [
            ("health", "/api/health", None, extrair_health, None),
            ("settings", "/api/settings", None, _escalares, None),
            ("usage", "/api/usage", {"days": dias}, extrair_usage, None),
            # Sem `refresh`: o Diagnóstico devolve o cache; só na primeira vez do processo ele coleta (lento).
            ("diagnostics", "/api/diagnostics", None, extrair_diagnostics, 120.0),
            ("workers", "/api/workers", None, extrair_workers, None),
            ("metrics", "/api/metrics", None, extrair_metrics, None),
            ("desempenho", "/api/desempenho", {"dias": dias, "janelas": 96}, extrair_desempenho, 120.0),
        ]
        for nome, caminho, params, extrair, tempo in roteiro:
            status, dados = cliente.get(caminho, params, timeout=tempo)
            # `http` e não `status`: o /api/health tem um `status` próprio (ok/degraded), que não pode sumir.
            if status == 200 and dados is not None:
                extraido = extrair(dados)
                fontes[nome] = ({"http": 200, "itens": extraido} if isinstance(extraido, list)
                                else {"http": 200, **extraido})
            else:
                fontes[nome] = {"http": status, "indisponivel": True}
    finally:
        cliente.close()
    meta = meta_comum()
    h = fontes.get("health") or {}
    st = fontes.get("settings") or {}
    feat = h.get("features") or {}
    ia = h.get("ia") or {}
    dg = fontes.get("diagnostics") or {}
    ferramentas = {t.get("name"): t.get("version") for t in dg.get("ferramentas") or []}
    workers = (fontes.get("workers") or {}).get("itens") or []
    linha = {
        "esquema": ESQUEMA, "modo": "leitura", "prova": "real", "aviso": AVISO_LEITURA,
        "cenario": "producao", "variante": "leitura", "repeticao": 1,
        "parametros": {"base": base, "dias": dias, "metodo": "GET"},
        "medidas": medidas_da_leitura(fontes), "fontes": fontes,
        "meta": {**meta, "commit_medido": h.get("commit"), "migracao_medida": h.get("migration"),
                 "config": {"preview_mode": st.get("preview_mode"), "image_policy": ia.get("image_policy"),
                            "recipes": ia.get("recipes"), "flows": ia.get("flows"),
                            "pathfinder_wait_s": feat.get("pathfinder_wait_s"),
                            "capture_grid_interval_s": st.get("capture_grid_interval_s"),
                            "capture_focus_interval_s": st.get("capture_focus_interval_s"),
                            "max_online_devices": st.get("max_online_devices"),
                            "boot_parallelism": st.get("boot_parallelism")},
                 "emulador": {"tipo": "emulador real (leitura do Diagnóstico)",
                              "versao": ferramentas.get("Android Emulator"),
                              "imagem": (dg.get("imagens") or {}).get("configurada"),
                              "renderer": None, "aceleracao": (dg.get("aceleracao") or {}).get("detail")},
                 "concorrencia": {"max_ai_concurrency": st.get("max_ai_concurrency"),
                                  "max_active_devices": st.get("max_active_devices")},
                 "carga": {"workers": len(workers),
                           "aparelhos_por_estado": _somar_estados(workers),
                           "emuladores_locais": (fontes.get("metrics") or {}).get("emuladores")}},
    }
    caminho = _arquivo_saida(saida, "leitura", meta)
    txt = resumo_texto([linha])
    _gravar(caminho, [linha], txt)
    print(txt)
    print(f"gravado: {caminho}\n         {caminho.with_suffix('.txt')}")
    return caminho


def _somar_estados(workers: list[dict[str, Any]]) -> dict[str, int]:
    tot: dict[str, int] = defaultdict(int)
    for w in workers:
        for k, v in (w.get("aparelhos_por_estado") or {}).items():
            tot[k] += v
    return dict(tot)


# ============================================================================================ resumo e comparação
def _ler(caminho: Path | str) -> list[dict[str, Any]]:
    return [json.loads(ln) for ln in Path(caminho).read_text(encoding="utf-8").splitlines() if ln.strip()]


def _agrupar(linhas: list[dict[str, Any]]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    g: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for ln in linhas:
        g[(ln.get("modo", "?"), ln["cenario"], ln["variante"])].append(ln)
    return g


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return float(v)
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def resumo_texto(linhas: list[dict[str, Any]]) -> str:
    if not linhas:
        return "(sem medições)\n"
    m0 = linhas[0]["meta"]
    out = [f"# bench {linhas[0]['modo']} — prova {linhas[0]['prova']}", linhas[0]["aviso"],
           f"sha={m0.get('sha')} suja={m0.get('arvore_suja')} data_utc={m0.get('data_utc')} "
           f"sistema={json.dumps(m0.get('sistema'), ensure_ascii=False)}"]
    if m0.get("commit_medido"):
        out.append(f"commit medido (produção)={m0['commit_medido']} migração={m0.get('migracao_medida')}")
    for (_, cen, var), grupo in sorted(_agrupar(linhas).items()):
        out.append(f"\n## {cen} · {var}  (n={len(grupo)})  parâmetros={json.dumps(grupo[0]['parametros'], ensure_ascii=False)}")
        chaves = sorted({k for ln in grupo for k in ln["medidas"]})
        for k in chaves:
            vals = [v for ln in grupo if (v := _num(ln["medidas"].get(k))) is not None]
            if not vals:
                continue
            media = sum(vals) / len(vals)
            faixa = "" if len(vals) == 1 or min(vals) == max(vals) else f"  [{min(vals):g}–{max(vals):g}]"
            out.append(f"  {k}: {media:g}{faixa}")
    return "\n".join(out) + "\n"


#: Medidas em que MAIS é melhor. O resto: menos é melhor (screencaps, chamadas, bytes, US$, latência), exceto as
#: neutras, que descrevem o mecanismo e nunca viram "ganho" sozinhas.
MAIOR_E_MELHOR = frozenset({"objetivos_ok", "objetivos.concluidos_por_hora", "objetivos.taxa.succeeded",
                            "host.mem_available_gb"})
#: Descrevem volume ou mecanismo, não resultado: mais chamadas na produção pode ser só mais trabalho feito, e
#: "mais etapas por receita" é o meio, não o fim. Aparecem na comparação, mas nunca como ganho ou piora.
NEUTRAS = frozenset({"objetivos", "objetivos.usd_coorte", "objetivos.taxa.pending", "objetivos.taxa.running",
                     "objetivos.taxa.cancelled", "ia.chamadas", "ia.usd_periodo", "host.emuladores"})
NEUTRAS_PREFIXO = ("etapas_por_", "etapas_ok_por_", "ia.chamadas.")


def _direcao(nome: str) -> str:
    if nome in MAIOR_E_MELHOR:
        return "maior"
    if nome in NEUTRAS or nome.startswith(NEUTRAS_PREFIXO) or nome.endswith(".n"):
        return "neutra"
    return "menor"


def veredito(antes: list[float], depois: list[float], *, limite_rel: float, amostra_min: int,
             direcao: str) -> tuple[str, float | None, str]:
    """(veredito, diferença relativa, motivo). Só `ganho`/`piora` quando: as duas amostras têm o mínimo, a
    diferença das médias passa do limite, e as repetições NÃO se sobrepõem. Qualquer outra coisa é exploratória
    ou sem diferença — nunca um ganho por acaso."""
    if len(antes) < amostra_min or len(depois) < amostra_min:
        return "exploratório", None, f"amostra insuficiente (antes n={len(antes)}, depois n={len(depois)}, mínimo {amostra_min})"
    ma, md = sum(antes) / len(antes), sum(depois) / len(depois)
    if ma == md:
        return "sem diferença", 0.0, "médias iguais"
    rel = (md - ma) / abs(ma) if ma != 0 else math.copysign(math.inf, md - ma)
    if abs(rel) <= limite_rel:
        return "sem diferença acima do limite", rel, f"|{rel:+.1%}| ≤ limite {limite_rel:.0%}"
    if direcao == "neutra":
        return "diferença (neutra)", rel, "medida descritiva: não vira ganho nem piora sozinha"
    subiu = md > ma
    separado = min(depois) > max(antes) if subiu else max(depois) < min(antes)
    if not separado:
        return "exploratório", rel, "passa do limite, mas as repetições de antes e depois se sobrepõem"
    melhorou = subiu == (direcao == "maior")
    return ("ganho" if melhorou else "piora"), rel, f"{rel:+.1%} além do limite {limite_rel:.0%}, sem sobreposição"


def comparar(antes: Path | str, depois: Path | str, *, limite_rel: float | None = None,
             amostra_min: int | None = None) -> dict[str, Any]:
    """Compara dois .jsonl da bancada. O critério padrão é o GRAVADO na linha de base (declarado antes do
    resultado); passar outro é possível, e fica registrado como sobrescrito."""
    la, ld = _ler(antes), _ler(depois)
    criterio = (la[0].get("meta", {}).get("criterio_de_ganho") if la else None) or {}
    lim = limite_rel if limite_rel is not None else criterio.get("limite_rel", LIMITE_REL_PADRAO)
    amin = amostra_min if amostra_min is not None else criterio.get("amostra_min", AMOSTRA_MIN_PADRAO)
    sobrescrito = (limite_rel is not None and limite_rel != criterio.get("limite_rel")) or (
        amostra_min is not None and amostra_min != criterio.get("amostra_min"))
    ga, gd = _agrupar(la), _agrupar(ld)
    itens = []
    for chave in sorted(set(ga) | set(gd)):
        a, d = ga.get(chave, []), gd.get(chave, [])
        if not a or not d:
            itens.append({"modo": chave[0], "cenario": chave[1], "variante": chave[2], "medida": "*",
                          "veredito": "incomparável", "motivo": "só existe de um lado"})
            continue
        provas = {ln.get("prova") for ln in a + d}
        params_a = {json.dumps(ln["parametros"], sort_keys=True) for ln in a}
        params_d = {json.dumps(ln["parametros"], sort_keys=True) for ln in d}
        if len(provas) > 1 or params_a != params_d:
            itens.append({"modo": chave[0], "cenario": chave[1], "variante": chave[2], "medida": "*",
                          "veredito": "incomparável",
                          "motivo": "níveis de prova diferentes" if len(provas) > 1 else "parâmetros diferentes"})
            continue
        for medida in sorted({k for ln in a + d for k in ln["medidas"]}):
            va = [v for ln in a if (v := _num(ln["medidas"].get(medida))) is not None]
            vd = [v for ln in d if (v := _num(ln["medidas"].get(medida))) is not None]
            if not va or not vd:
                itens.append({"modo": chave[0], "cenario": chave[1], "variante": chave[2], "medida": medida,
                              "veredito": "incomparável", "motivo": "medida ausente de um lado"})
                continue
            ver, rel, motivo = veredito(va, vd, limite_rel=lim, amostra_min=amin, direcao=_direcao(medida))
            itens.append({"modo": chave[0], "cenario": chave[1], "variante": chave[2], "medida": medida,
                          "antes_media": round(sum(va) / len(va), 4), "depois_media": round(sum(vd) / len(vd), 4),
                          "n_antes": len(va), "n_depois": len(vd),
                          "dif_rel": None if rel is None or not math.isfinite(rel) else round(rel, 4),
                          "veredito": ver, "motivo": motivo})
    return {"criterio": {"limite_rel": lim, "amostra_min": amin, "sobrescrito": bool(sobrescrito),
                         "gravado_na_linha_de_base": criterio or None},
            "antes": {"arquivo": str(antes), "sha": (la[0]["meta"].get("sha") if la else None)},
            "depois": {"arquivo": str(depois), "sha": (ld[0]["meta"].get("sha") if ld else None)},
            "itens": itens}


def comparacao_texto(c: dict[str, Any]) -> str:
    cr = c["criterio"]
    out = [f"# comparação — limite {cr['limite_rel']:.0%}, amostra mínima {cr['amostra_min']}"
           + (" (SOBRESCRITO na linha de comando)" if cr["sobrescrito"] else ""),
           f"antes sha={c['antes']['sha']}  depois sha={c['depois']['sha']}"]
    for it in c["itens"]:
        if it["veredito"] in ("sem diferença",):
            continue
        num = (f" {it['antes_media']:g} → {it['depois_media']:g}" if "antes_media" in it else "")
        out.append(f"- [{it['veredito']}] {it['cenario']} · {it['variante']} · {it['medida']}:{num} — {it['motivo']}")
    return "\n".join(out) + "\n"


# ============================================================================================ CLI
def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0].startswith("-"):
        argv.insert(0, "simulado")              # seguro por padrão: sem subcomando é a bancada simulada
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="modo", required=True)
    s = sub.add_parser("simulado", help="contagens com o harness (aparelho falso, provedor simulado)")
    s.add_argument("--cenarios", default="previa,imagem,receitas")
    s.add_argument("--repeticoes", type=int, default=AMOSTRA_MIN_PADRAO)
    s.add_argument("--aparelhos", type=int, default=3, help="aparelhos online no cenário de prévia")
    s.add_argument("--duracao-previa", type=float, default=6.0, help="segundos sem espectador")
    s.add_argument("--intervalo-previa", type=float, default=1.0, help="capture_grid_interval_s (mínimo 1)")
    s.add_argument("--limite", type=float, default=LIMITE_REL_PADRAO, help="diferença relativa mínima para ganho")
    s.add_argument("--amostra-min", type=int, default=AMOSTRA_MIN_PADRAO)
    s.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    lr = sub.add_parser("leitura", help="só GET num backend de loopback")
    lr.add_argument("--base", default="http://127.0.0.1:8000")
    lr.add_argument("--dias", type=int, default=7)
    lr.add_argument("--saida", type=Path, default=SAIDA_PADRAO)
    cp = sub.add_parser("comparar", help="antes × depois (.jsonl)")
    cp.add_argument("antes", type=Path)
    cp.add_argument("depois", type=Path)
    cp.add_argument("--limite", type=float, default=None)
    cp.add_argument("--amostra-min", type=int, default=None)
    a = ap.parse_args(argv)
    if a.modo == "simulado":
        cenarios = [c.strip() for c in a.cenarios.split(",") if c.strip()]
        desconhecidos = set(cenarios) - {"previa", "imagem", "receitas"}
        if desconhecidos:
            ap.error(f"cenário desconhecido: {', '.join(sorted(desconhecidos))}")
        if a.intervalo_previa < 1:
            ap.error("--intervalo-previa mínimo é 1 (limite de capture_grid_interval_s)")
        simulado(cenarios=cenarios, repeticoes=a.repeticoes, aparelhos=a.aparelhos, duracao_s=a.duracao_previa,
                 intervalo_s=a.intervalo_previa, saida=a.saida, limite_rel=a.limite, amostra_min=a.amostra_min)
        return 0
    if a.modo == "leitura":
        leitura(a.base, dias=a.dias, saida=a.saida)
        return 0
    c = comparar(a.antes, a.depois, limite_rel=a.limite, amostra_min=a.amostra_min)
    print(comparacao_texto(c))
    return 0


if __name__ == "__main__":
    t0 = time.monotonic()
    codigo = main()
    print(f"({time.monotonic() - t0:.1f} s)")
    sys.exit(codigo)
