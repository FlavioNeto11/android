#!/usr/bin/env python
"""Smoke REAL da função de produto `religar_pela_interface` no android-09 (W8, hardening; docs/handoffs/w8-diagnostico-android09.md §20.4).

Diferença para `diag-w8-uistart.py`: aquele toca no Start por um localizador próprio do script; este chama a função REAL do branch
(`app.devices.rede_aplicacao.religar_pela_interface`) através de um aparelho que só fala adb + a árvore do central. Nenhuma cópia do
algoritmo. UMA tentativa (marcador na pasta do coletor), só o android-09, SEGURO POR PADRÃO (sem --execute só mostra o plano).

Escritas possíveis no aparelho: as da própria função (`am start` da MainActivity, `input tap` no Start, HOME) e, para desligar
depois, `am start` da MainActivity + UM `input tap` no Stop achado pela mesma árvore + HOME. Sem perfil, par, política, tile,
WireGuard, dados do app nem outro aparelho. Se o tun0 persistir depois do Stop, o rollback do acionador do tile (extraordinário,
registrado) é o mesmo do diagnóstico anterior.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _carregar(nome: str, arquivo: str):
    spec = importlib.util.spec_from_file_location(nome, ROOT / "scripts" / arquivo)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)                                                  # type: ignore[union-attr]
    return mod


ui = _carregar("diag_w8_uistart_base", "diag-w8-uistart.py")
tile = ui.tile
coletor = tile.coletor
PACOTE, IID, SERIAL = tile.PACOTE, tile.IID, tile.SERIAL


def raiz_do_central(inicio: Path = ROOT) -> Path:
    """O checkout que tem o banco do central (`data/poc.sqlite3`): rodando deste worktree o `data/` não existe aqui, e o gate
    (leitura do banco) falhava por isso. Sobe os ancestrais até achar o banco; sem achar, fica onde está."""
    for d in (inicio, *inicio.parents):
        if (d / "data" / "poc.sqlite3").exists():
            return d
    return inicio


tile.ROOT = raiz_do_central()                                                     # o `sql` do tile lê `ROOT/data/poc.sqlite3`
ATIVIDADE = f"{PACOTE}/.compose.MainActivity"
MARCADOR = "smoke-produto-09.tentativa"

from app.devices import rede_aplicacao as ra  # noqa: E402  (depois do sys.path)


class AparelhoDoSmoke:
    """`AparelhoDaRede` para o android-09 pelo adb (o worker remoto da LAN aparece como um serial no adb do central) e pela
    árvore do central (`GET /api/instances/<id>/hierarchy`: a mesma de `AparelhoPeloAdb.arvore`)."""

    external = True

    def __init__(self, amb: Any) -> None:
        self.amb = amb
        self.id, self.serial = IID, SERIAL
        self.comandos: list[str] = []

    async def shell(self, comando: str, *, timeout: float = 40) -> str:
        self.comandos.append(comando)
        rc, out, err = await asyncio.to_thread(self.amb.shell, comando, timeout)
        if rc != 0:
            raise RuntimeError(f"adb shell falhou ({rc}): {err.strip()[:120]}")
        return out

    async def arvore(self) -> list[ra.NoDaTela]:
        h = await asyncio.to_thread(self.amb.hierarquia)
        return [ra.NoDaTela(texto=(e.get("text") or e.get("desc") or "").strip(), pacote=e.get("package") or "",
                            limites=tuple(e.get("bounds") or ()), clicavel=bool(e.get("clickable")),
                            habilitado=bool(e.get("enabled", True))) for e in h.get("elements", [])]

    async def elementos(self) -> list[ra.Elemento]:
        return []

    async def tocar(self, x: int, y: int) -> None:
        self.comandos.append(f"input tap {int(x)} {int(y)}")
        rc, _, err = await asyncio.to_thread(self.amb.shell, f"input tap {int(x)} {int(y)}", 20)
        if rc != 0:
            raise RuntimeError(f"input tap falhou ({rc}): {err.strip()[:120]}")

    async def reverso(self, porta: int) -> None:                                  # não usado pela função
        raise NotImplementedError

    async def desfazer_reverso(self, porta: int) -> None:
        raise NotImplementedError


def _resumo(r: ra.ReligadoPelaInterface) -> dict[str, Any]:
    return {"RESULT_CODE": r.codigo, "detalhe": r.detalhe, "SERVICE_CLASS": r.classe, "UI_TARGET_LABEL": r.rotulo,
            "UI_TARGET_METHOD": r.metodo, "DEVICE_LOCALE": r.locale, "TUN_TIME": r.tun_apos_s,
            "TUN_CREATED": bool(r.obs and r.obs.tun), "VPN_CONNECTED": bool(r.obs and r.obs.vpn_conectada),
            "religado": r.religado}


async def _desligar_pela_ui(ap: AparelhoDoSmoke, locale: str, reg: Any) -> dict[str, Any]:
    """O Stop do próprio cliente (o mecanismo normal da UI), achado pela árvore com o rótulo `stop` do locale: UM toque."""
    _, parada, _ = ra.rotulos_para(locale)
    out: dict[str, Any] = {"rotulos": list(parada)}
    await ap.shell(f"am start -n {ATIVIDADE}", timeout=30)
    fim = time.time() + 15
    botao, motivo = None, "sem leitura"
    while time.time() < fim:
        await asyncio.sleep(1.0)
        botao, motivo = ra.achar_botao(await ap.arvore(), PACOTE, parada)
        if botao is not None:
            break
    out["motivo"] = motivo
    if botao is None:
        return out
    x, y = botao.centro
    await ap.tocar(x, y)
    out["toque"] = (x, y)
    reg("toque", alvo="Stop", x=x, y=y)
    return out


def rodar(amb: Any, run: Path, reg: Any) -> dict[str, Any]:
    r: dict[str, Any] = {"iid": IID, "inicio": ui.iso(amb.agora())}
    marcador = run / MARCADOR
    if marcador.exists():
        r["parada"] = "SEGUNDA_TENTATIVA_RECUSADA"
        return r
    g = ui.precondicoes(amb, run)
    r["gate"] = {"ok": g["ok"], "falhas": g["falhas"], "base": g["base"]}
    reg("gate", ok=g["ok"], falhas=g["falhas"])
    if not g["ok"]:
        r["parada"] = "SMOKE_BLOCKED"
        return r
    ap = AparelhoDoSmoke(amb)
    marcador.write_text(ui.iso(), encoding="utf-8")                              # ANTES da chamada: não há segunda chance
    t0 = amb.agora()
    try:
        res = asyncio.run(ra.religar_pela_interface(ap, PACOTE, ATIVIDADE))      # a função REAL, os tempos padrão do produto
        r["funcao"] = _resumo(res)
        reg("funcao", **r["funcao"])
        r["chamada_s"] = round(amb.agora() - t0, 1)
        if res.obs and (res.obs.tun or res.obs.vpn_conectada):
            r["desligar"] = asyncio.run(_desligar_pela_ui(ap, res.locale, reg))
            fim = amb.agora() + ui.PARAR_S
            while amb.agora() < fim:
                amb.dormir(4.0)
                if tile.ler_leve(amb)["tun"] is False:
                    break
    except Exception as exc:  # noqa: BLE001 - o rollback roda de qualquer jeito
        r["erro"] = f"{type(exc).__name__}: {exc}"[:300]
        reg("erro", erro=r["erro"])
    finally:
        amb.shell(ui.CMD_HOME, 20)
        r["rollback"] = ui.fechar(amb, reg)
    r["comandos_do_aparelho"] = len(ap.comandos)
    r["fim"] = ui.iso(amb.agora())
    return r


def plano() -> dict[str, Any]:
    return {"instancia": IID, "modo": "plano (nenhuma chamada)",
            "passos": ["gate: precondições do tile + conectividade healthy (leitura)",
                       "UMA chamada de religar_pela_interface (função real do branch): am start, árvore, UM toque, observa",
                       "se subiu: abrir a MainActivity, achar o Stop pela árvore (rótulo do locale) e UM toque; HOME",
                       "releitura da linha de base e do central"],
            "escritas_possiveis": [f"am start -n {ATIVIDADE}", "input tap <Start>", "input tap <Stop>", ui.CMD_HOME]}


def main(argv: list[str] | None = None, amb: Any = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--instance", default=None)
    ap.add_argument("--run", default=None, help="pasta do coletor (obrigatória com --execute)")
    a = ap.parse_args(argv)
    if not a.execute:
        print(json.dumps(plano(), ensure_ascii=False, indent=2))
        return 0
    if a.instance != IID:
        print(f"recusado: só o {IID} (recebido {a.instance!r})", file=sys.stderr)
        return 2
    if not a.run or not Path(a.run).is_dir():
        print("recusado: --run <pasta do coletor> é obrigatório e precisa existir", file=sys.stderr)
        return 2
    run = Path(a.run)
    amb = amb or ui.Ambiente()
    reg = tile.Registro(run / "acionador-smoke-produto.jsonl")
    r = rodar(amb, run, reg)
    r["central_final"] = tile.central_final(amb) if "gate" in r and r["gate"]["ok"] else None
    (run / "smoke-produto-09.saida.json").write_text(json.dumps(r, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if r.get("parada") == "SEGUNDA_TENTATIVA_RECUSADA":
        return 5
    if r.get("parada") == "SMOKE_BLOCKED":
        return 3
    return 0 if r.get("rollback", {}).get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
