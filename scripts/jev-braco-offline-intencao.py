"""Braço offline do Jev (31.11, M2 do roteiro), parte R2 e R3: a intenção (C3), só sobre os comandos que a sombra da
intenção JÁ mandou ao Jev (ADR-069 item 21), com a mesma amostra em inglês e em português (D-J7).

- **Acompanhamento.** Nenhum número daqui vale para GO e nada é ligado: o `on` sai do relatório do 31.10 sobre a sombra
  (ADR-069 item 6). O que este braço responde cedo: a resposta do lote repete a da sombra? E o idioma da instrução muda
  a resposta?
- **Só o que já saiu, com a igualdade do texto provada.** `app.taskqueue.lote_intencao` remonta cada caso pelo código do
  runtime e o classifica:
  - `c`: o hash do estado redigido bate com o `estado_hash` da linha (31.22, migração 086). Só o `c` vai ao Jev.
  - `b`: linha sem hash (anterior à 086) que passaria na salvaguarda "b". Só CONTADO, nunca enviado (decisão da
    orquestradora, 03/10 19:33Z): identidade de arquivos não prova igualdade do estado, só o hash prova.
  - O resto fica de fora, contado pelo motivo.
- **A salvaguarda "b" do código:** `--commits-da-janela` (o commit de cada deploy da janela) e, para cada um,
  `git diff --quiet <commit> -- ARQUIVOS_DO_FILTRO` contra a árvore que roda o lote. Qualquer diferença, commit ausente
  ou erro do `git` é falha fechada.
- **Duas línguas por caso, intercaladas** (inglês e depois português, caso a caso): o teto, se acabar, corta as duas no
  mesmo ponto. Depois de cada envio, o hash que a porta registrou tem de ser o do caso; diferente, a rodada para.
- **`--seco` é o padrão:** passa cada pedido pela porta com um decisor que não toca rede e conta o que sairia. Enviar
  exige `--enviar --teto USD` (no máximo `TETO_MAX_USD`) e ao menos `MIN_COMANDOS_REAIS` casos enviáveis.
- **O custo fica no JSON.** O banco abre só para leitura: as chamadas NÃO viram linha em `ai_calls` (ADR-051).

Cada linha da saída leva `run_id`, app, pergunta, idioma, salvaguarda, a escolha opaca, a maior probabilidade, o
fallback, a decisão real e a escolha da sombra viva, o rótulo e a fonte. Nunca o comando nem o estado.

Uso, a partir da raiz do checkout (o `--enviar`, do checkout central, que tem a chave):

    backend/.venv/Scripts/python.exe scripts/jev-braco-offline-intencao.py [--db data/poc.sqlite3 | --dsn ...]
        [--desde 2026-10-03T15:29:51Z] [--commits-da-janela c8304e85,d5a1c3a9,1c54a7bb] [--config config/config.yaml]
        [--json saida.json] [--md saida.md] [--enviar --teto 0.05]
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.config import DecisaoFechadaCfg, load_config  # noqa: E402
from app.planning.decisao_fechada.decisores import Decisor  # noqa: E402
from app.planning.decisao_fechada.intencao import PERGUNTA_CATALOGO, id_opaco  # noqa: E402
from app.planning.decisao_fechada.porta import TIMEOUT_SHADOW_S, Porta, RegistroDeDecisao  # noqa: E402
from app.taskqueue.lote_intencao import (DESDE_ITEM_21, CasoDaIntencao, LeituraDoLote, LoteOffline,  # noqa: E402
                                         SalvaguardaB, consumidor_do_lote, em_portugues, ler_lote)

_spec = importlib.util.spec_from_file_location("jev_braco_offline", RAIZ / "scripts" / "jev-braco-offline.py")
braco = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = braco
_spec.loader.exec_module(braco)  # type: ignore[union-attr]
rel = braco.rel

#: O mínimo de comandos reais enviáveis para a rodada paga (orquestradora, 03/10): abaixo disto, só o seco.
MIN_COMANDOS_REAIS: Final = 10
#: Só o caso `c` vai ao Jev (orquestradora, 03/10 19:33Z): toda linha que sai tem a igualdade do texto provada pelo
#: hash. O `b` é calculado e relatado (quantas passariam), sem liberar envio.
SALVAGUARDA_QUE_ENVIA: Final = "c"
#: O que decide o texto do estado da intenção, conferido pela salvaguarda "b": os 5 arquivos aprovados pela orquestradora
#: (03/10) e o que a leitura do código de 03/10 ~19:25Z achou além deles (a leitura da execução e o comando sem destinos
#: em `service.py`, o `alvos.py` de que o extrator depende e os nomes de app do filtro, no registro e nos `app.yaml`).
ARQUIVOS_DO_FILTRO: Final = (
    "backend/app/planning/decisao_fechada/intencao.py",
    "backend/app/planning/decisao_fechada/entidades.py",
    "backend/app/planning/decisao_fechada/privacidade.py",
    "backend/app/security/redaction.py",
    "backend/app/modules/execution/application/target_extractor.py",
    "backend/app/modules/execution/application/alvos.py",
    "backend/app/taskqueue/service.py",
    "backend/app/modules/applications/infrastructure/registry.py",
    "backend/app/conhecimento/apps",
)
#: A porta do lote: só a intenção, em sombra, só C3 (o item 21 do ADR-069). O YAML só estreita o código.
CFG_DA_INTENCAO: Final = DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"}, classes_permitidas=["C3"],
                                           decisor="jev")
IDIOMAS: Final = ("en", "pt")
AVISO: Final = "acompanhamento; nenhum número aqui vale para GO (rótulos = {n})"


# ------------------------------------------------------------------ salvaguarda "b": o código do filtro
def codigo_igual(raiz: Path, commits: Sequence[str]) -> tuple[bool, dict[str, str]]:
    """(igual em todos, {commit: igual | diferente | erro}). Sem commit nenhum, falso: nada a conferir não prova nada."""
    detalhe: dict[str, str] = {}
    for c in commits:
        try:
            r = subprocess.run(["git", "-C", str(raiz), "diff", "--quiet", c, "--", *ARQUIVOS_DO_FILTRO],
                               capture_output=True, timeout=60, check=False)
            detalhe[c] = {0: "igual", 1: "diferente"}.get(r.returncode, "erro")
        except (OSError, subprocess.SubprocessError):
            detalhe[c] = "erro"
    return bool(commits) and all(v == "igual" for v in detalhe.values()), detalhe


# ------------------------------------------------------------------ casos, rótulos e rodada
def _com_parametro(db: Any) -> set[str]:
    """Os fluxos com `{parâmetro}` (golden set §3: um acerto neles ainda pede o parâmetro), pelo id opaco da opção."""
    return {id_opaco(f"flow:{r['id']}") for r in db.query("SELECT id, command_template FROM flows")
            if "{" in str(r["command_template"] or "")}


def rotulos(db: Any, casos: Sequence[CasoDaIntencao]) -> dict[tuple[str, str], tuple[str | None, str | None]]:
    """{(run_id, pergunta): (rótulo, fonte)} pelo relatório do 31.10. A R2 usa a linha VIVA (o desfecho lê a decisão real
    dela); a R3, sem decisão real, só a escolha da pessoa."""
    fonte = rel.RotulosSql(db)
    saida: dict[tuple[str, str], tuple[str | None, str | None]] = {}
    for caso in casos:
        vivas = {str(linha["pergunta_id"]): linha for linha in caso.linhas}
        for p in caso.pedido.perguntas:
            linha = vivas.get(p.id) or {"run_id": caso.run_id, "pergunta_id": p.id, "decisao_real": None}
            saida[(caso.run_id, p.id)] = rel._rotulo_da_intencao(db, fonte, linha)
    return saida


def rodar(casos: Sequence[CasoDaIntencao], decisor: Decisor
          ) -> tuple[dict[tuple[str, str], RegistroDeDecisao | None], str | None]:
    """Caso a caso, inglês e depois português, pela porta em `shadow`, esperando cada sombra. Para no primeiro `orcamento`
    (teto) e no primeiro hash registrado diferente do do caso (o texto que saiu não seria o da sombra)."""
    registros: list[RegistroDeDecisao] = []
    porta = Porta(decisor, cfg=CFG_DA_INTENCAO, observador=registros.append)
    saida: dict[tuple[str, str], RegistroDeDecisao | None] = {}
    interrompido = None
    try:
        for caso in casos:
            for idioma in IDIOMAS:
                antes = len(registros)
                porta.consultar(caso.pedido if idioma == "en" else em_portugues(caso.pedido))
                porta.aguardar_sombras(timeout_s=TIMEOUT_SHADOW_S + 5.0)
                novo = registros[antes:]
                saida[(caso.run_id, idioma)] = novo[0] if novo else None
                if novo and novo[0].estado_hash != caso.estado_hash:
                    interrompido = "hash_no_envio"
                elif novo and novo[0].resultado.fallback_reason == "orcamento":
                    interrompido = "teto"
                if interrompido:
                    return saida, interrompido
    finally:
        porta.encerrar()
    return saida, interrompido


# ------------------------------------------------------------------ relatório
def _linha(caso: CasoDaIntencao, pergunta: str, registro: RegistroDeDecisao | None) -> dict[str, Any]:
    """A pergunta na forma da linha de sombra que o relatório do 31.10 lê; decisão real e ambíguos vêm da linha viva."""
    viva = next((linha for linha in caso.linhas if linha["pergunta_id"] == pergunta), None)
    base = {"pergunta_id": pergunta, "run_id": caso.run_id, "ts": caso.ts, "motivo_privacidade": None,
            "decisao_real": viva["decisao_real"] if viva else None, "ambiguos": viva["ambiguos"] if viva else None}
    if registro is None:
        return {**base, "escolha": None, "fallback_reason": "sem_registro", "confianca": None, "probabilidades": None}
    resposta = registro.resultado.respostas.get(pergunta)
    motivo = registro.resultado.fallback_reason or (resposta.fallback_reason if resposta else "parse")
    return {**base, "escolha": resposta.escolha if resposta else None, "fallback_reason": motivo,
            "motivo_privacidade": registro.motivo_privacidade,
            "confianca": resposta.confianca if resposta else None,
            "probabilidades": json.dumps(dict(resposta.probabilidades)) if resposta and resposta.probabilidades else None}


def _iguais(pares: Sequence[tuple[Any, Any]]) -> dict[str, Any]:
    validos = [(a, b) for a, b in pares if a is not None and b is not None]
    return {"comparaveis": len(validos), "iguais": sum(1 for a, b in validos if a == b),
            "taxa": rel._taxa(sum(1 for a, b in validos if a == b), len(validos))}


def montar(leitura: LeituraDoLote, enviaveis: Sequence[CasoDaIntencao],
           registros: Mapping[tuple[str, str], RegistroDeDecisao | None], rotulos_: Mapping[tuple[str, str], Any], *,
           agora: datetime, enviado: bool, interrompido: str | None, teto: Any, pedidos_secos: int | None,
           com_parametro: set[str], salvaguarda_b: Mapping[str, Any]) -> dict[str, Any]:
    por_idioma: dict[str, dict[str, list[dict[str, Any]]]] = {i: defaultdict(list) for i in IDIOMAS}
    linhas_saida: list[dict[str, Any]] = []
    en_pt: list[tuple[Any, Any]] = []
    en_pt_maior: list[tuple[Any, Any]] = []
    lote_sombra: list[tuple[Any, Any]] = []
    lote_sombra_maior: list[tuple[Any, Any]] = []
    for caso in enviaveis:
        for p in caso.pedido.perguntas:
            rotulo, fonte = rotulos_.get((caso.run_id, p.id), (None, None))
            viva = next((linha for linha in caso.linhas if linha["pergunta_id"] == p.id), None)
            por = {i: _linha(caso, p.id, registros.get((caso.run_id, i))) for i in IDIOMAS}
            for idioma, linha in por.items():
                por_idioma[idioma][caso.app].append({"linha": linha, "rotulo": rotulo, "fonte": fonte})
                linhas_saida.append({
                    "run_id": caso.run_id, "app": caso.app, "pergunta": p.id, "idioma": idioma,
                    "salvaguarda": caso.salvaguarda, "escolha": linha["escolha"], "maior": braco._maior(linha),
                    "probabilidades": linha["probabilidades"], "fallback": linha["fallback_reason"],
                    "real": linha["decisao_real"], "sombra": viva["escolha"] if viva else None,
                    "rotulo": rotulo, "fonte": fonte})
            en_pt.append((por["en"]["escolha"], por["pt"]["escolha"]))
            en_pt_maior.append((braco._maior(por["en"]), braco._maior(por["pt"])))
            if viva is not None:
                lote_sombra.append((por["en"]["escolha"], viva["escolha"]))
                lote_sombra_maior.append((braco._maior(por["en"]), braco._maior(viva)))
    idiomas = {i: {app: rel._estrato_da_intencao(app, itens, agora, com_parametro) for app, itens in sorted(apps.items())}
               for i, apps in por_idioma.items()}
    for estratos in idiomas.values():
        for e in estratos.values():
            e["modo"] = "off (o braço offline não liga nada)"
    custo: dict[str, Any] = {"nivel": "PROVED (medido pelo transporte)" if enviado else "not_run (--seco)"}
    if teto is not None:
        ms = [c.ms for c in teto.chamadas if c.ok]
        custo.update({"teto_usd": teto.teto_usd, "usd": round(teto.gasto_usd, 6), "chamadas": len(teto.chamadas),
                      "ok": sum(1 for c in teto.chamadas if c.ok),
                      "falhas": dict(Counter(str(c.motivo) for c in teto.chamadas if not c.ok)),
                      "tokens": sum(c.tokens_entrada + c.tokens_saida for c in teto.chamadas),
                      "ms": rel._percentis(ms), "modelos": sorted({c.modelo for c in teto.chamadas}),
                      "em_ai_calls": False})
    n_rotulos = sum(1 for v in rotulos_.values() if v[0])
    return {
        "aviso": AVISO.format(n=n_rotulos), "consumidor": "intencao", "classe": "C3", "enviado": enviado,
        "interrompido": interrompido,
        "casos": {"lidos": len(leitura.casos), "por_salvaguarda": dict(Counter(c.salvaguarda for c in leitura.casos)),
                  "enviaveis": len(enviaveis), "fora": dict(leitura.fora)},
        "salvaguarda_b": {**salvaguarda_b, "libera_envio": False, "ultima_remocao": leitura.ultima_remocao,
                          "horizonte_dos_eventos": leitura.horizonte_dos_eventos},
        "pedidos_secos": pedidos_secos,
        "idiomas": idiomas,
        "en_x_pt": {"escolha": _iguais(en_pt), "maior": _iguais(en_pt_maior),
                    "nivel": "INFERRED (mesma amostra; a maior probabilidade é sem limiar)"},
        "lote_x_sombra": {"escolha": _iguais(lote_sombra), "maior": _iguais(lote_sombra_maior),
                          "nota": "o lote em inglês contra a linha viva da MESMA execução: estabilidade do Jev"},
        "custo": custo, "linhas": linhas_saida,
        "nota": ("a R3 do lote é a RESOLVE de hoje sobre o catálogo de hoje (o empate não é gravado na sombra); "
                 "concordância com a sombra é acompanhamento, nunca GO"),
    }


def em_markdown(r: Mapping[str, Any]) -> str:
    c, b = r["casos"], r["salvaguarda_b"]
    linhas = [f"# Braço offline do Jev — intenção ({r['classe']}), R2 e R3", "", f"**{r['aviso']}**", "",
              f"- Enviado: {'sim' if r['enviado'] else 'não (--seco)'}; interrompido: {r['interrompido'] or 'não'}.",
              f"- Casos lidos {c['lidos']} {c['por_salvaguarda'] or ''}; enviáveis {c['enviaveis']} (só `c`, o hash);"
              f" fora {c['fora'] or '—'}.",
              f"- Salvaguarda b: código igual {b['codigo_igual']} {b['commits'] or '(sem commits)'}; última remoção"
              f" {b['ultima_remocao'] or '—'}; eventos desde {b['horizonte_dos_eventos'] or '—'}; só relatada,"
              f" não libera envio.",
              f"- Inglês × português: escolha {r['en_x_pt']['escolha']}, maior {r['en_x_pt']['maior']}.",
              f"- Lote (inglês) × sombra viva: escolha {r['lote_x_sombra']['escolha']},"
              f" maior {r['lote_x_sombra']['maior']}."]
    for idioma, estratos in r["idiomas"].items():
        for app, e in estratos.items():
            m = e["medidas"]
            linhas += ["", f"## {idioma} · {app}", "",
                       f"- R2 {m['pedidos_r2']}, R3 {m['pedidos_r3']}, respondidos {m['respondidos']}, cobertura"
                       f" {m['cobertura']}; fallbacks {m['fallbacks'] or '—'}.",
                       f"- Rótulos {m['rotulos']} → {e['veredito']} ({'; '.join(e['falhou']) or 'todos os critérios'})."]
    custo = r["custo"]
    linhas += ["", "## Custo", "", f"- {custo['nivel']}"
               + (f": US$ {custo['usd']} de {custo['teto_usd']}, {custo['chamadas']} chamadas ({custo['ok']} ok),"
                  f" tokens {custo['tokens']}, ms p50 {custo['ms']['p50']} / p95 {custo['ms']['p95']}."
                  if "usd" in custo else ".")]
    if "usd" in custo:
        linhas.append(f"- Estas {custo['chamadas']} chamadas NÃO estão em `ai_calls` (o lote abre o banco só para"
                      f" leitura): o total explica a diferença no livro-caixa da conta do Jev (ADR-051).")
    return "\n".join(linhas) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--db", default=str(RAIZ / "data" / "poc.sqlite3"))
    p.add_argument("--dsn", help="PostgreSQL; no lugar de --db")
    p.add_argument("--desde", default=DESDE_ITEM_21, help="ISO-8601 UTC; padrão e mínimo: o item 21 do ADR-069")
    p.add_argument("--commits-da-janela", default="", help="os commits de cada deploy da janela, separados por vírgula")
    p.add_argument("--config", help="o config.yaml de onde vêm `skills.enabled` e `ai.flows`; padrão: o da instalação")
    p.add_argument("--enviar", action="store_true", help="chama o Jev de verdade (exige --teto)")
    p.add_argument("--teto", type=float, help=f"teto da rodada em US$ (no máximo {braco.TETO_MAX_USD})")
    p.add_argument("--json", help="arquivo do JSON; padrão: a tela")
    p.add_argument("--md", help="arquivo do Markdown")
    args = p.parse_args(argv)
    if args.desde < DESDE_ITEM_21:
        raise SystemExit(f"--desde antes do item 21 do ADR-069 ({DESDE_ITEM_21}): o lote não reenvia o que é de antes")
    teto = None
    if args.enviar:
        if args.teto is None or not 0 < args.teto <= braco.TETO_MAX_USD:
            raise SystemExit(f"--enviar exige --teto entre 0 e {braco.TETO_MAX_USD} (US$)")
        teto = braco.Teto(args.teto)
    commits = [c.strip() for c in args.commits_da_janela.split(",") if c.strip()]
    igual, detalhe = codigo_igual(RAIZ, commits)
    arquivo = load_config(args.config).file
    db = rel._abrir(args)
    try:
        lote = LoteOffline(db, skills_ligadas=arquivo.skills.enabled, fluxos_ligados=arquivo.ai.flows)
        seco = braco.DecisorSeco()
        leitura = ler_lote(lote, consumidor_do_lote(Porta(seco, cfg=CFG_DA_INTENCAO), db), desde=args.desde,
                           salvaguarda_b=SalvaguardaB(codigo_igual=igual))
        enviaveis = [c for c in leitura.casos if c.salvaguarda == SALVAGUARDA_QUE_ENVIA]
        rotulos_ = rotulos(db, enviaveis)
        com_parametro = _com_parametro(db)
    finally:
        db.close()
    if teto is not None and len(enviaveis) < MIN_COMANDOS_REAIS:
        raise SystemExit(f"{len(enviaveis)} casos enviáveis, menos que {MIN_COMANDOS_REAIS}: nada foi enviado")
    decisor = braco.decisor_real(teto) if teto is not None else seco
    registros, interrompido = rodar(enviaveis, decisor)
    rel_ = montar(leitura, enviaveis, registros, rotulos_, agora=datetime.now(UTC), enviado=teto is not None,
                  interrompido=interrompido, teto=teto, pedidos_secos=len(seco.pedidos) if teto is None else None,
                  com_parametro=com_parametro,
                  salvaguarda_b={"codigo_igual": igual, "commits": detalhe, "arquivos": list(ARQUIVOS_DO_FILTRO)})
    texto = json.dumps(rel_, ensure_ascii=False, indent=1, default=str)
    if args.json:
        Path(args.json).write_text(texto, encoding="utf-8")
    else:
        print(texto)
    if args.md:
        Path(args.md).write_text(em_markdown(rel_), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
