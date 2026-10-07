"""29.205: tendência da latência por etapa do funil e por etapa do deploy, corte a corte, a partir do que já fica em disco.

Fontes (só leitura; não gera carga, não chama IA, não toca o parque):
  - run.txt do funil, no formato novo do `scripts/funil.ps1` (linhas `ETAPA ... dur_s=N status=...`) e no antigo do funil da Android
    (`N nome ini=HH:MM:SSZ`, duração = distância até o ini da etapa seguinte; a última usa o `in Ns` do resumo do pytest, se houver);
  - `data/deploys.jsonl` (`etapas_s` e `duracao_s` de cada deploy).

Uso:
  python scripts/funil-tendencia.py <run.txt|glob>... [--deploys data/deploys.jsonl] [--limite 0.15] [--piso-funil-s 60] [--piso-deploy-s 5]
                                    [--saida tendencia.md] [--falhar-se-piora]

Regressão = o último corte passou da MEDIANA dos anteriores em mais de `--limite` (15 %) e também em mais que o piso absoluto de segundos
(ruído de etapa curta não conta). Um corte com etapa FALHA não entra na mediana (a falha encurta ou alonga a etapa por outro motivo).
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import statistics
import sys
from pathlib import Path

ETAPAS = ("scripts", "sqlite", "frontend", "catracas", "mypy", "pg")
_PALAVRAS = (("scripts", "scripts"), ("sqlite", "sqlite"), ("frontend", "frontend"), ("catracas", "catracas"), ("mypy", "mypy"), ("pg", "pg"), ("postgres", "pg"))


def normalizar_etapa(texto: str) -> str:
    baixo = texto.lower()
    for palavra, chave in _PALAVRAS:
        if palavra in baixo:
            return chave
    return baixo.split()[0] if baixo.split() else "?"


def _campos(linha: str) -> dict[str, str]:
    return {k: (a or b) for k, a, b in re.findall(r'(\w+)=(?:"([^"]*)"|(\S+))', linha)}


def _segundos(hora: str) -> int:
    h, m, s = (hora.rstrip("Z").split(":") + ["0", "0"])[:3]
    return int(h) * 3600 + int(m) * 60 + int(s)


def etiqueta_do_corte(caminho: Path) -> tuple[tuple[int, str], str]:
    """`funilcorte59-run.txt` -> ((59, ''), '59'); `funilcorte57b-run.txt` -> ((57, 'b'), '57b'). Sem número: ordem pelo nome."""
    m = re.search(r"(\d+)([a-z]?)-run", caminho.name)
    if not m:
        return (0, caminho.stem), caminho.stem
    return (int(m.group(1)), m.group(2)), m.group(1) + m.group(2)


def ler_run(caminho: Path) -> dict[str, dict]:
    """{etapa: {"dur_s": float, "falhou": bool}} de UM run.txt (novo ou antigo)."""
    texto = caminho.read_text(encoding="utf-8-sig", errors="replace")
    saida: dict[str, dict] = {}
    novas = [ln for ln in texto.splitlines() if ln.startswith("ETAPA ")]
    if novas:
        for ln in novas:
            c = _campos(ln)
            if "dur_s" not in c:
                continue
            chave = normalizar_etapa(c.get("chave") or c.get("nome", "?"))
            saida[chave] = {"dur_s": float(c["dur_s"]), "falhou": c.get("status", "ok") != "ok"}
        return saida
    # formato antigo: "N nome ini=HH:MM:SSZ" seguido das linhas de saída da etapa
    marcas: list[tuple[str, int, int]] = []   # (chave, ini_s, indice_da_linha)
    linhas = texto.splitlines()
    for i, ln in enumerate(linhas):
        m = re.match(r"^\s*\d+\s+(.+?)\s+ini=(\d{2}:\d{2}(?::\d{2})?Z?)\s*$", ln)
        if m:
            marcas.append((normalizar_etapa(m.group(1)), _segundos(m.group(2)), i))
    for k, (chave, ini, i) in enumerate(marcas):
        fim_i = marcas[k + 1][2] if k + 1 < len(marcas) else len(linhas)
        bloco = "\n".join(linhas[i + 1:fim_i])
        dur = None
        if k + 1 < len(marcas):
            dur = (marcas[k + 1][1] - ini) % 86400
        else:
            m = re.findall(r"\bin (\d+(?:\.\d+)?)s\b", bloco)
            dur = float(m[-1]) if m else None
        if dur is None:
            continue
        falhou = bool(re.search(r"^FAILED |\b\d+ failed\b", bloco, re.M))
        saida[chave] = {"dur_s": float(dur), "falhou": falhou}
    return saida


def ler_deploys(caminho: Path) -> list[dict]:
    saida = []
    for ln in caminho.read_text(encoding="utf-8-sig").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            d = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if d.get("resultado") != "ok" or not isinstance(d.get("etapas_s"), dict):
            continue
        saida.append({"rotulo": (d.get("ts_utc", "?")[5:16].replace("T", " ") + " " + str(d.get("commit_depois", ""))[:7]).strip(),
                      "duracao_s": d.get("duracao_s"), **{k: v for k, v in d["etapas_s"].items() if isinstance(v, (int, float))}})
    return saida


def regressoes(linhas: list[dict], colunas: list[str], limite: float, piso_s: float) -> list[dict]:
    """Última linha contra a mediana das anteriores (que tenham a coluna e não tenham falhado)."""
    if len(linhas) < 2:
        return []
    ultima, anteriores = linhas[-1], linhas[:-1]
    achados = []
    for col in colunas:
        v = ultima.get(col)
        base = [x[col] for x in anteriores if isinstance(x.get(col), (int, float)) and col not in x.get("_falhou", ())]
        if not isinstance(v, (int, float)) or not base:
            continue
        mediana = statistics.median(base)
        if mediana > 0 and v > mediana * (1 + limite) and (v - mediana) > piso_s:
            achados.append({"coluna": col, "rotulo": ultima["rotulo"], "valor_s": v, "mediana_s": mediana, "pct": (v / mediana - 1) * 100, "amostras": len(base)})
    return achados


def _celula_min(v, falhou: bool = False) -> str:
    if not isinstance(v, (int, float)):
        return "—"
    return f"{v / 60:.1f}" + ("*" if falhou else "")


def tabela_md(titulo: str, linhas: list[dict], colunas: list[str], achados: list[dict], unidade_min: bool, piso_s: float, limite: float) -> str:
    if not linhas:
        return f"## {titulo}\n\nsem dados.\n"
    un = "min" if unidade_min else "s"
    cab = "| corte | " + " | ".join(colunas) + " |"
    out = [f"## {titulo} ({un})", "", cab, "|---|" + "---:|" * len(colunas)]
    for ln in linhas:
        cel = []
        for c in colunas:
            v = ln.get(c)
            cel.append(_celula_min(v, c in ln.get("_falhou", ())) if unidade_min else (f"{v:.1f}" if isinstance(v, (int, float)) else "—"))
        out.append(f"| {ln['rotulo']} | " + " | ".join(cel) + " |")
    out += ["", f"`*` = etapa falhou (fora da mediana). Regressão: último corte > mediana dos anteriores +{limite * 100:.0f} % e +{piso_s:.0f} s.", ""]
    if achados:
        out.append("**Regressões:**")
        for a in achados:
            out.append(f"- `{a['coluna']}` em {a['rotulo']}: {a['valor_s']:.1f} s contra mediana {a['mediana_s']:.1f} s de {a['amostras']} cortes (+{a['pct']:.0f} %)")
    else:
        out.append("**Sem regressão** no último corte.")
    return "\n".join(out) + "\n"


def montar(runs: list[Path], deploys: Path | None, limite: float, piso_funil_s: float, piso_deploy_s: float) -> tuple[str, list[dict]]:
    cortes = sorted(((etiqueta_do_corte(p), p) for p in runs), key=lambda t: t[0][0])
    linhas_f: list[dict] = []
    for (_, rotulo), p in cortes:
        etapas = ler_run(p)
        if not etapas:
            continue
        linha = {"rotulo": rotulo, "_falhou": tuple(k for k, v in etapas.items() if v["falhou"])}
        linha.update({k: v["dur_s"] for k, v in etapas.items()})
        linha["total"] = sum(v["dur_s"] for v in etapas.values())
        linhas_f.append(linha)
    cols_f = [c for c in ETAPAS if any(c in ln for ln in linhas_f)] + (["total"] if linhas_f else [])
    ach_f = regressoes(linhas_f, cols_f, limite, piso_funil_s)
    partes = [tabela_md("Funil por etapa", linhas_f, cols_f, ach_f, True, piso_funil_s, limite)]
    ach_d: list[dict] = []
    if deploys is not None and deploys.exists():
        linhas_d = ler_deploys(deploys)[-12:]
        cols_d = []
        for ln in linhas_d:
            for k in ln:
                if k not in ("rotulo", "_falhou") and k not in cols_d:
                    cols_d.append(k)
        cols_d = [c for c in cols_d if c != "duracao_s"] + (["duracao_s"] if any("duracao_s" in ln for ln in linhas_d) else [])
        ach_d = regressoes(linhas_d, cols_d, limite, piso_deploy_s)
        partes.append(tabela_md("Deploy por etapa (últimos 12 ok)", linhas_d, cols_d, ach_d, False, piso_deploy_s, limite))
    return "\n".join(partes), ach_f + ach_d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("runs", nargs="*", help="run.txt ou glob (o shell do Windows não expande: o script expande)")
    ap.add_argument("--deploys", default="")
    ap.add_argument("--limite", type=float, default=0.15)
    ap.add_argument("--piso-funil-s", type=float, default=60.0)
    ap.add_argument("--piso-deploy-s", type=float, default=5.0)
    ap.add_argument("--saida", default="")
    ap.add_argument("--falhar-se-piora", action="store_true")
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    arquivos: list[Path] = []
    for padrao in a.runs:
        achados = glob.glob(padrao)
        arquivos += [Path(x) for x in (achados or [padrao]) if Path(x).is_file()]
    md, regress = montar(arquivos, Path(a.deploys) if a.deploys else None, a.limite, a.piso_funil_s, a.piso_deploy_s)
    print(md)
    if a.saida:
        Path(a.saida).write_text(md, encoding="utf-8")
    return 1 if (regress and a.falhar_se_piora) else 0


if __name__ == "__main__":
    sys.exit(main())
