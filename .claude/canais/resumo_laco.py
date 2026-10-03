"""Resumo de 20 min ao dono pelo Telegram, montado NA HORA do envio e sem depender de a sessão estar ociosa.

Lição de 03/10: o cron de sessão só dispara com a sessão parada, e a orquestradora ficou 65 min sem enviar durante a
carga do Trello. Este laço roda em segundo plano (Bash `run_in_background`) e, a cada envio, compõe a mensagem de fontes
que ele mesmo mede, para nunca reenviar texto velho:

- `/api/health` do central (estado, commit curto, migração, problemas);
- o plano-100 (`scripts/claude-plan-100.py check` para o total e os implementados; `estado.json` para parciais e
  bloqueados);
- `.claude/handoffs/canais/situacao.json`, mantido pela sessão Canais: deploy no ar, frentes, pendências do dono e
  `mudou_extra` (o "Mudou desde a última", escrito PARA o dono: até 5 linhas inteiras, sem hash nem arquivo). Depois de
  editar, `--carimbar` põe a hora do relógio e marca até onde o eventos foi curado. Se a situação tiver mais de 40 min,
  a mensagem diz a hora em que as frentes foram conferidas;
- o `.claude/handoffs/canais/eventos.md` é canal INTERNO e nunca vai ao dono: dele só se conta quantos fatos ainda não
  foram curados ("N novidades desde HH:MMZ; detalho no próximo resumo"). Depois do envio, `mudou_extra` é esvaziado.

O corpo inteiro passa por `redacao.redigir` (o mesmo filtro do Trello) e o envio é o `telegram_status.py`, que lê token
e chat do `.env` pelo `EnvSettings` e nunca imprime nada deles. O cursor (`resumo_cursor.json`) guarda a hora, o
`message_id` e a linha do eventos do último envio: reiniciar o laço não repete nem pula.

Uso (python do backend/.venv):
  resumo_laco.py --carimbar               carimba situacao.json (hora do relógio, eventos curados até agora)
  resumo_laco.py --ensaio                 compõe e grava `resumo_ensaio.html`, sem enviar e sem mexer no cursor
  resumo_laco.py --uma-vez                compõe e envia uma vez
  resumo_laco.py [--intervalo 1200]       laço: envia quando o último envio tiver `intervalo` segundos
  resumo_laco.py --armar --desde-linha N --ultimo-envio 2026-10-03T18:40:00Z
                                          grava o cursor inicial (passagem da caixa) e sai
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(r"C:\git\android")
#: Os dados do laço (cursor, mensagem enviada, ensaio) ficam fora do Git; o código fica em .claude/canais/.
AQUI = RAIZ / ".claude" / "handoffs" / "telegram"
SCRIPTS = Path(__file__).resolve().parent
CANAIS = RAIZ / ".claude" / "handoffs" / "canais"
EVENTOS = CANAIS / "eventos.md"
SITUACAO = CANAIS / "situacao.json"
CURSOR = AQUI / "resumo_cursor.json"
SAIDA = AQUI / "telegram_msg.html"
ENSAIO = AQUI / "resumo_ensaio.html"
PY = RAIZ / "backend" / ".venv" / "Scripts" / "python.exe"
SAUDE = "http://127.0.0.1:8000/api/health"
BRASILIA = timezone(timedelta(hours=-3))
#: o Telegram corta em 4096; deixa folga para o reenvio em texto puro
LIMITE = 3800
#: "Mudou desde a última" é curado pela Canais: no máximo 5 linhas inteiras de ~120 caracteres
MAX_MUDOU = 5
MAX_CHARS_MUDOU = 140

sys.path.insert(0, str(RAIZ / ".claude" / "trello"))
from redacao import redigir  # noqa: E402


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _hora(dt: datetime) -> str:
    return f"{dt:%H:%M}Z ({dt.astimezone(BRASILIA):%H:%M} Brasília)"


def _ler_json(caminho: Path, padrao: dict) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(padrao)


def _gravar_json(caminho: Path, dado: dict) -> None:
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(dado, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(caminho)


def _e(texto: object) -> str:
    """Redige e escapa: tudo o que é dinâmico passa por aqui antes de virar HTML do Telegram."""
    return html.escape(redigir(str(texto)), quote=False)


def _cortar(texto: str, n: int) -> str:
    texto = " ".join(texto.split())
    if len(texto) <= n:
        return texto
    corte = texto[:n].rsplit(" ", 1)[0]
    return corte.rstrip(",;:·-") + "…"


def _saude(deploy: object) -> str:
    """Sem jargão quando está tudo bem; commit e migração só aparecem quando há problema."""
    try:
        with urllib.request.urlopen(SAUDE, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - central fora do ar também é notícia
        return "🔴 <b>A Central não respondeu</b> na hora deste resumo"
    problemas = d.get("problems") or []
    no_ar = f" · deploy {_e(deploy)} no ar" if deploy else ""
    if d.get("status") == "ok" and not problemas:
        return f"🟢 <b>Central saudável</b>{no_ar}"
    commit = str(d.get("commit") or "")[:8]
    migracao = str(d.get("migration") or "").split("_", 1)[0]
    return (f"🟡 <b>Central com {len(problemas)} problema(s)</b>{no_ar} · commit <code>{_e(commit)}</code>"
            f" · migração {_e(migracao)}")


def _plano() -> tuple[str, str]:
    total = feitos = None
    try:
        out = subprocess.run([str(PY), str(RAIZ / "scripts" / "claude-plan-100.py"), "check"], cwd=RAIZ,
                             capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
        m1, m2 = re.search(r"(\d+) itens no plano", out), re.search(r"Implementados: (\d+)", out)
        total, feitos = (int(m1.group(1)), int(m2.group(1))) if m1 and m2 else (None, None)
    except (OSError, subprocess.SubprocessError):
        pass
    if total is None:
        return "<b>Plano geral:</b> leitura falhou nesta rodada", ""
    est = _ler_json(RAIZ / ".claude" / "plano-100" / "estado.json", {"itens": {}})["itens"]
    parciais = sum(1 for v in est.values() if v.get("status") == "partial")
    bloqueados = sum(1 for v in est.values() if v.get("status") == "blocked")
    a_fazer = max(total - feitos - parciais - bloqueados, 0)
    pct = round(100 * feitos / total) if total else 0
    return (f"<b>Plano geral: {feitos} de {total} itens concluídos ({pct} %)</b>",
            f"{parciais} parciais · {bloqueados} bloqueados · {a_fazer} a fazer")


def _linhas_de_fato() -> list[str]:
    try:
        linhas = EVENTOS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [x for x in linhas if x.strip() and not x.startswith("#") and "|" in x]


def _nao_curados(curados_ate: int) -> tuple[int, str]:
    """O eventos.md é canal interno (escrito para a Canais): nunca vai ao dono. Só se conta o que ainda não foi curado."""
    novos = _linhas_de_fato()[curados_ate:]
    if not novos:
        return 0, ""
    return len(novos), novos[0].split("|", 1)[0].strip()


def compor(cursor: dict, agora: datetime) -> tuple[str, int]:
    sit = _ler_json(SITUACAO, {})
    titulo, detalhe = _plano()
    partes = [f"<b>📊 ANA · IA Gerente de Operações da Central · {_hora(agora)}</b>", "", _saude(sit.get("deploy_no_ar")), "", titulo]
    if detalhe:
        partes.append(detalhe)

    frentes = sit.get("frentes") or []
    if frentes:
        partes += ["", "<b>Frentes</b>"]
        partes += [f"▪️ <b>{_e(f.get('nome', ''))}</b> · {_e(f.get('linha', ''))}" for f in frentes]
        conferido = sit.get("atualizado_em")
        try:
            quando = datetime.fromisoformat(str(conferido).replace("Z", "+00:00"))
            if agora - quando > timedelta(minutes=40):
                partes.append(f"<i>Frentes conferidas às {quando:%H:%M}Z; o que mudou depois está abaixo.</i>")
        except ValueError:
            pass

    # "Mudou" é só o que a Canais escreveu para o dono (`mudou_extra`); o laço não inventa texto.
    mudou = [f"▪️ {_e(_cortar(str(x), MAX_CHARS_MUDOU))}" for x in (sit.get("mudou_extra") or [])][:MAX_MUDOU]
    n, desde = _nao_curados(int(sit.get("eventos_curados_ate", 0)))
    partes += ["", "<b>Mudou desde a última</b>"]
    partes += mudou
    if n:
        partes.append(f"▪️ {n} {'novidade' if n == 1 else 'novidades'} desde {_e(desde)}; detalho no próximo resumo")
    if not mudou and not n:
        partes.append("▪️ nada novo nas frentes")
    total_linhas = len(_linhas_de_fato())

    pend = sit.get("pendencias") or []
    partes += ["", "<b>🙋 Pendências suas</b>"]
    partes += [f"▪️ {_e(p)}" for p in pend] if pend else ["▪️ nenhuma agora"]

    texto = "\n".join(partes)
    if len(texto) > LIMITE:
        texto = texto[:LIMITE].rsplit("\n", 1)[0] + "\n<i>(cortado)</i>"
    return texto, total_linhas


def enviar(texto: str) -> int | None:
    SAIDA.write_text(texto, encoding="utf-8")
    r = subprocess.run([str(PY), str(SCRIPTS / "telegram_status.py"), str(SAIDA)], cwd=RAIZ, capture_output=True,
                       timeout=90)
    saida = r.stdout.decode("utf-8", "replace").strip()
    m = re.search(r"message_id=(\d+)", saida)
    if r.returncode == 0 and m:
        return int(m.group(1))
    # a saída do telegram_status já é limpa (nunca leva token nem chat); só a primeira linha
    print(f"{_agora():%H:%M:%S}Z falhou: {saida.splitlines()[0] if saida else r.returncode}", flush=True)
    return None


def rodada(cursor: dict) -> dict | None:
    agora = _agora()
    texto, linhas = compor(cursor, agora)
    mid = enviar(texto)
    if mid is None:
        return None
    novo = {"enviado_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ"), "message_id": mid, "eventos_linha": linhas}
    _gravar_json(CURSOR, novo)
    # o que foi contado já foi dito: o próximo resumo só traz o que a Canais curar depois deste envio
    sit = _ler_json(SITUACAO, {})
    if sit.get("mudou_extra"):
        sit["mudou_extra"] = []
        _gravar_json(SITUACAO, sit)
    print(f"{agora:%H:%M:%S}Z enviado message_id={mid} ({len(texto)} chars)", flush=True)
    return novo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ensaio", action="store_true")
    ap.add_argument("--uma-vez", action="store_true")
    ap.add_argument("--intervalo", type=int, default=1200)
    ap.add_argument("--armar", action="store_true")
    ap.add_argument("--desde-linha", type=int, default=None)
    ap.add_argument("--ultimo-envio", default=None)
    ap.add_argument("--carimbar", action="store_true",
                    help="depois de editar situacao.json: hora do relógio em atualizado_em e eventos curados até agora")
    args = ap.parse_args()
    cursor = _ler_json(CURSOR, {"eventos_linha": 0})

    if args.armar:
        if args.desde_linha is None or not args.ultimo_envio:
            print("--armar pede --desde-linha e --ultimo-envio")
            return 2
        _gravar_json(CURSOR, {"enviado_em": args.ultimo_envio, "message_id": None,
                              "eventos_linha": args.desde_linha})
        print(f"cursor armado: linha {args.desde_linha}, último envio {args.ultimo_envio}")
        return 0
    if args.carimbar:
        sit = _ler_json(SITUACAO, {})
        sit["atualizado_em"] = _agora().strftime("%Y-%m-%dT%H:%M:%SZ")
        sit["eventos_curados_ate"] = len(_linhas_de_fato())
        _gravar_json(SITUACAO, sit)
        print(f"situação carimbada às {sit['atualizado_em']}, eventos curados até {sit['eventos_curados_ate']}")
        return 0
    if args.ensaio:
        texto, linhas = compor(cursor, _agora())
        ENSAIO.write_text(texto, encoding="utf-8")
        print(f"ensaio gravado ({len(texto)} chars, eventos até a linha {linhas}); nada enviado")
        return 0
    if args.uma_vez:
        return 0 if rodada(cursor) else 1

    print(f"{_agora():%H:%M:%S}Z laço do resumo ligado, a cada {args.intervalo} s", flush=True)
    while True:
        try:
            ultimo = datetime.fromisoformat(str(cursor.get("enviado_em")).replace("Z", "+00:00"))
        except ValueError:
            ultimo = _agora() - timedelta(seconds=args.intervalo)
        espera = (ultimo + timedelta(seconds=args.intervalo) - _agora()).total_seconds()
        if espera > 0:
            time.sleep(min(espera, 60))
            cursor = _ler_json(CURSOR, cursor)  # o cursor pode ser rearmado por fora
            continue
        novo = rodada(cursor)
        if novo is None:
            time.sleep(120)  # falha de envio: tenta de novo em 2 min, sem pular a rodada
            continue
        cursor = novo


if __name__ == "__main__":
    raise SystemExit(main())
