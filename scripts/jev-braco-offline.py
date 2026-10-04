"""Braço offline do Jev (31.11, M2 do roteiro): as mesmas decisões fechadas da sombra, rodadas FORA do caminho sobre
casos que já estão no banco, para medir cedo o que a sombra mediria. Parte R1: a triagem do curador (C0, dado F1).

- **Acompanhamento.** Enquanto os rótulos 1 e 2 do golden set (§2) não chegam ao mínimo, nenhum número daqui vale para
  GO. E mesmo com rótulos, o braço não liga nada: o `on` sai do relatório do 31.10 sobre a sombra (ADR-069 item 6).
- **O caminho é o do runtime** (decisão da orquestradora, 03/10 ~18:40Z; golden set §2): a `Porta` em `shadow` com o
  `DecisorJev` e o transporte do adaptador de retrieval, sem o adaptador MIT e sem dependência nova. Privacidade,
  redação, prazo e conferência do limiar são os da porta; o pedido é o da `TriagemDoCurador`.
- **`--seco` é o padrão.** Monta os pedidos e passa cada um pela porta com um decisor que não toca rede: conta o que
  sairia e o que a privacidade recusaria. Enviar exige `--enviar --teto USD`, com o teto no máximo `TETO_MAX_USD`
  (condição da orquestradora para a janela). Antes de cada POST, o `DecisorJev` confere o teto (`conferir_gasto`), com
  uma reserva por chamada: estourado, é `orcamento`, nada sai e a rodada para. O teto vale enquanto uma chamada custar
  menos que a reserva (a medida de 03/10 é 1/20 dela).
- **O custo fica no JSON** (`custo.chamadas`). O banco abre SÓ PARA LEITURA (o mesmo `_SoLeitura` do relatório do
  31.10), então as chamadas daqui NÃO viram linha em `ai_calls`: o livro-caixa do ADR-051 não as vê, e o total vai no
  registro do estado do item.
- **A chave** vem do `EnvSettings` do checkout que roda o script (o `.env` da raiz dele), pelo mesmo `Mapping` do
  retrieval (`wiring._AmbienteDoProvedor`), lida só na hora do POST. Num worktree sem `.env`, `--enviar` recusa antes
  de começar. Nada aqui imprime a chave.

`--estado v2` (31.11, branch do sinal): o pedido leva também os campos de SINAL do curador (`curador.CAMPOS_DE_SINAL`:
versão viva, uso, idade da evidência a favor, códigos fechados de saúde e de risco, trilha), com as faixas de idade
contadas a partir da hora da REVISÃO, para o caso ser reprodutível. A sombra do runtime segue no `v1`
(`curador.ESTADO_DA_SOMBRA`) até esta medição mostrar respostas distintas.

O que a saída mostra por `kind`: o estrato do golden set (as mesmas contas do relatório do 31.10, reaproveitadas) e o
SINAL da entrada, a pergunta do 31.19: os estados C0 distintos ganham respostas distintas, e o mesmo estado repetido
ganha a mesma resposta? Cada caso leva `dossie_hash`, `kind`, um hash do estado, a escolha opaca, as probabilidades, o
fallback, o controle, a decisão real e o rótulo. Nunca o dossiê, o `item_ref` ou o estado em si.

Uso, a partir da raiz do checkout:

    backend/.venv/Scripts/python.exe scripts/jev-braco-offline.py [--db data/poc.sqlite3 | --dsn postgresql://...]
        [--desde 2026-10-03T00:00:00Z] [--autor-dono NOME ...] [--json saida.json] [--md saida.md]
        [--enviar --teto 0.05]
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "backend"))

from app.config import DecisaoFechadaCfg, EnvSettings  # noqa: E402
from app.modules.context_retrieval.adapters.jev import JevSemanticProvider  # noqa: E402
from app.modules.context_retrieval.wiring import _AmbienteDoProvedor  # noqa: E402
from app.planning.decisao_fechada.contrato import PedidoDeDecisao, ResultadoDeDecisao, resultado_de_fallback  # noqa: E402
from app.planning.decisao_fechada.curador import (  # noqa: E402
    ESTADO_DA_SOMBRA, VERSOES_DO_ESTADO, TriagemDoCurador, decisao_real_da_triagem,
)
from app.planning.decisao_fechada.decisores import ChamadaAoJev, Decisor, DecisorJev  # noqa: E402
from app.planning.decisao_fechada.porta import TIMEOUT_SHADOW_S, Porta, RegistroDeDecisao  # noqa: E402

_spec = importlib.util.spec_from_file_location("jev_relatorio_31_10", RAIZ / "scripts" / "jev-relatorio-31-10.py")
rel = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rel)  # type: ignore[union-attr]

#: Condição da orquestradora para a janela do braço offline (03/10): teto de US$ 0,05 no total.
TETO_MAX_USD: Final = 0.05
#: Folga antes de cada POST: a chamada só sai se o gasto somado mais esta reserva cabe no teto. Medido na leitura
#: preliminar de 03/10: US$ 0,000051 por chamada; a reserva é ~20 vezes isso.
RESERVA_POR_CHAMADA_USD: Final = 0.001
#: Consumidores que este braço roda. A R1 (curador, C0) não depende da emenda do item 4. A R2 e a R3 (C3, item 21 do
#: ADR-069) ficam no script irmão `jev-braco-offline-intencao.py`; a R5 entra com o 31.13.
CONSUMIDORES_LIBERADOS: Final = ("curador",)
#: A porta do braço: só o consumidor da rodada, em sombra, só C0 e C1. O YAML só estreita o código (ADR-069 item 3).
CFG_DO_BRACO: Final = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow"},
                                        classes_permitidas=["C0", "C1"], decisor="jev")
AVISO: Final = "acompanhamento; nenhum número aqui vale para GO (rótulos 1 e 2 = {n})"
#: 2º controle, "regra da saúde" (orquestradora, 03/10 ~19:40Z; golden set §2): só acompanhamento, fora do GO, e o
#: controle pré-registrado não muda. Por quê: a Aprendizado leu os 29 pareceres da R1 e o curador segue o rótulo de
#: saúde do dossiê, não as contagens cruas (pouca amostra, em prova ou nunca usado → pedir evidência; saudável →
#: manter; parado ou degradando → observar). Na triagem, pedir evidência e observar são `revisar`. Rótulo fora do mapa
#: (inativo, obsoleto provável, indeterminado ou ausente) não tem resposta de controle.
CONTROLE_DA_SAUDE: Final = {"saudavel": "manter", "pouca_amostra": "revisar", "em_prova": "revisar",
                            "sem_evidencia": "revisar", "parado": "revisar", "degradando": "revisar"}


@dataclass(frozen=True)
class Caso:
    revisao_id: str
    dossie_hash: str
    kind: str
    criado_em: str
    pedido: PedidoDeDecisao
    estado_json: str          # só para contar estados distintos; nunca sai na saída
    rotulo: str | None
    fonte: str | None
    real: str | None
    controle: str
    controle_saude: str | None = None


# ------------------------------------------------------------------ decisores do braço
class DecisorSeco:
    """O `--seco`: recebe o pedido JÁ redigido pela porta e devolve `desligado`, sem rede. Conta o que sairia."""

    def __init__(self) -> None:
        self.pedidos: list[PedidoDeDecisao] = []

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        self.pedidos.append(pedido)
        return resultado_de_fallback(pedido, "desligado")


class Teto:
    """A conferência de gasto do `DecisorJev` no braço: o teto da rodada, somado pelas chamadas registradas."""

    def __init__(self, teto_usd: float) -> None:
        self.teto_usd = teto_usd
        self.gasto_usd = 0.0
        self.chamadas: list[ChamadaAoJev] = []

    def conferir(self, pedido: PedidoDeDecisao) -> None:
        if self.gasto_usd + RESERVA_POR_CHAMADA_USD > self.teto_usd:
            raise RuntimeError("teto do braço offline")   # o DecisorJev transforma em `orcamento` e nada sai

    def registrar(self, chamada: ChamadaAoJev) -> None:
        self.chamadas.append(chamada)
        self.gasto_usd += float(chamada.usd or 0.0)


def decisor_real(teto: Teto) -> DecisorJev:
    """O `DecisorJev` do runtime (`state._decisor_da_porta`), com a chave do `.env` deste checkout e o teto do braço."""
    transporte = JevSemanticProvider(env=_AmbienteDoProvedor(EnvSettings()))
    if not transporte.available()[0]:
        raise SystemExit("chave do Jev não configurada neste checkout: rode o --enviar a partir do checkout central")
    return DecisorJev(transporte, conferir_gasto=teto.conferir, registrar=teto.registrar)


# ------------------------------------------------------------------ casos
def _hash_curto(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:12]


def casos_do_curador(db: Any, *, desde: str | None, autores_dono: frozenset[str],
                     versao_do_estado: str = ESTADO_DA_SOMBRA,
                     confirmacoes: Mapping[str, Mapping[str, str]] | None = None) -> tuple[list[Caso], Counter[str]]:
    """Os casos da R1: uma revisão do curador por caso, só os `kind` de F1 (`TriagemDoCurador.pedido`). O rótulo é o
    do relatório do 31.10, contado a partir da revisão (não há linha de sombra no braço offline)."""
    sql = "SELECT * FROM learning_reviews WHERE template_id=?" + (" AND created_at>=?" if desde else "")
    revisoes = db.query(sql + " ORDER BY created_at, id", (rel.TEMPLATE_DO_CURADOR, *((desde,) if desde else ())))
    casos: list[Caso] = []
    fora: Counter[str] = Counter()
    for r in revisoes:
        try:
            dossie = json.loads(r["dossie"] or "{}")
        except ValueError:
            dossie = {}
        pedido = (TriagemDoCurador.pedido(dossie, str(r["dossie_hash"]), versao_do_estado=versao_do_estado,
                                          agora=rel._ts(str(r["created_at"])))
                  if isinstance(dossie, dict) else None)
        if pedido is None:
            fora[str(r["item_kind"])] += 1
            continue
        rotulo, fonte = rel._rotulo_do_curador(db, r, str(r["created_at"]), autores_dono, confirmacoes)
        try:
            parecer = json.loads(r["saida"]).get("decisao") if r["saida"] else None
        except (ValueError, AttributeError):
            parecer = None
        casos.append(Caso(
            revisao_id=str(r["id"]), dossie_hash=str(r["dossie_hash"]), kind=str(r["item_kind"]),
            criado_em=str(r["created_at"]), pedido=pedido,
            estado_json=json.dumps(dict(pedido.estado), sort_keys=True, ensure_ascii=False),
            rotulo=rotulo, fonte=fonte,
            real=decisao_real_da_triagem(parecer, validade=r["validade"], simulado=r["simulated"]),
            controle=rel._controle_do_curador(r), controle_saude=controle_da_saude(r)))
    return casos, fora


def controle_da_saude(revisao: Mapping[str, Any]) -> str | None:
    """O 2º controle (acompanhamento): o rótulo de saúde do C0 pelo `CONTROLE_DA_SAUDE`, ou `None` fora do mapa."""
    destino = CONTROLE_DA_SAUDE.get(str(rel._estado_c0(revisao).get("saude") or ""))
    return rel._opt(destino) if destino else None


# ------------------------------------------------------------------ rodada
def rodar(casos: Sequence[Caso], decisor: Decisor) -> tuple[list[RegistroDeDecisao | None], str | None]:
    """Um caso por vez pela porta em `shadow`, esperando cada sombra: a ordem e o teto ficam previsíveis. Para no
    primeiro `orcamento` (o teto acabou: os seguintes também não sairiam). Devolve o registro de cada caso, na ordem."""
    registros: list[RegistroDeDecisao] = []
    porta = Porta(decisor, cfg=CFG_DO_BRACO, observador=registros.append)
    saida: list[RegistroDeDecisao | None] = []
    interrompido = None
    try:
        for caso in casos:
            antes = len(registros)
            porta.consultar(caso.pedido)
            porta.aguardar_sombras(timeout_s=TIMEOUT_SHADOW_S + 5.0)
            novo = registros[antes:]
            saida.append(novo[0] if novo else None)
            if novo and novo[0].resultado.fallback_reason == "orcamento":
                interrompido = "teto"
                break
    finally:
        porta.encerrar()
    return saida, interrompido


def _linha(caso: Caso, registro: RegistroDeDecisao | None) -> dict[str, Any]:
    """O caso na forma da linha de sombra que o relatório do 31.10 lê (`escolha`, `fallback_reason`, `confianca`,
    `probabilidades` em JSON, `ts`): assim as contas do estrato são as MESMAS."""
    if registro is None:
        return {"escolha": None, "fallback_reason": "sem_registro", "confianca": None, "probabilidades": None,
                "ts": caso.criado_em}
    resposta = next(iter(registro.resultado.respostas.values()), None)
    motivo = registro.resultado.fallback_reason or (resposta.fallback_reason if resposta else "parse")
    return {"escolha": resposta.escolha if resposta else None, "fallback_reason": motivo,
            "confianca": resposta.confianca if resposta else None,
            "probabilidades": json.dumps(dict(resposta.probabilidades)) if resposta and resposta.probabilidades else None,
            "ts": caso.criado_em}


def _maior(linha: Mapping[str, Any]) -> str | None:
    """A opção de maior probabilidade, com ou sem limiar: o que o Jev "acha" mesmo quando a porta recusa."""
    try:
        probs = json.loads(linha["probabilidades"] or "{}")
    except (ValueError, TypeError):
        return None
    validas = {k: float(v) for k, v in probs.items() if isinstance(v, (int, float))} if isinstance(probs, dict) else {}
    return max(sorted(validas), key=lambda k: validas[k]) if validas else None


def _sinal(itens: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """A pergunta do 31.19: a entrada carrega sinal? Por estado C0: a resposta de maior probabilidade e se ela varia
    entre casos do MESMO estado (estabilidade)."""
    por_estado: dict[str, Counter[str]] = defaultdict(Counter)
    for i in itens:
        maior = _maior(i["linha"])
        if maior is not None:
            por_estado[_hash_curto(i["estado"])][maior] += 1
    modas = {e: c.most_common(1)[0][0] for e, c in por_estado.items()}
    return {
        "estados_com_resposta": len(por_estado),
        "respostas_distintas_entre_estados": len(set(modas.values())),
        "estados_instaveis": sorted(e for e, c in por_estado.items() if len(c) > 1),
        "por_estado": {e: dict(c) for e, c in sorted(por_estado.items())},
        "nivel": "INFERRED (resposta de maior probabilidade, sem limiar)",
    }


def montar(casos: Sequence[Caso], registros: Sequence[RegistroDeDecisao | None], *, fora: Mapping[str, int],
           versao_do_estado: str = ESTADO_DA_SOMBRA,
           agora: datetime, enviado: bool, interrompido: str | None, teto: Teto | None,
           pedidos_secos: int | None) -> dict[str, Any]:
    por_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    linhas_saida: list[dict[str, Any]] = []
    for caso, registro in zip(casos, registros, strict=False):
        linha = _linha(caso, registro)
        item = {"linha": linha, "rotulo": caso.rotulo, "fonte": caso.fonte, "real": caso.real,
                "estado": caso.estado_json, "controle": caso.controle, "controle_saude": caso.controle_saude}
        por_kind[caso.kind].append(item)
        linhas_saida.append({
            "dossie_hash": caso.dossie_hash, "kind": caso.kind, "estado": _hash_curto(caso.estado_json),
            "escolha": linha["escolha"], "maior": _maior(linha), "probabilidades": linha["probabilidades"],
            "confianca": linha["confianca"], "fallback": linha["fallback_reason"], "controle": caso.controle,
            "controle_saude": caso.controle_saude,
            "real": caso.real, "rotulo": caso.rotulo, "fonte": caso.fonte})
    rotulos = sum(1 for c in casos if c.rotulo)
    estratos = {}
    for kind, itens in sorted(por_kind.items()):
        estrato = rel._estrato_do_curador(itens, agora)
        estrato["modo"] = "off (o braço offline não liga nada)"
        com_real = [i for i in itens if i["real"]]
        com_maior = [i for i in com_real if _maior(i["linha"]) is not None]   # sem probabilidade não há o que comparar
        estrato["medidas"]["concordancia_da_maior_com_o_curador"] = rel._taxa(
            sum(1 for i in com_maior if _maior(i["linha"]) == i["real"]), len(com_maior))
        estrato["medidas"]["concordancia_do_controle_com_o_curador"] = rel._taxa(
            sum(1 for i in com_real if i["controle"] == i["real"]), len(com_real))
        com_saude = [i for i in com_real if i["controle_saude"]]           # rótulo fora do mapa não conta
        estrato["medidas"]["concordancia_do_controle_da_saude_com_o_curador"] = rel._taxa(
            sum(1 for i in com_saude if i["controle_saude"] == i["real"]), len(com_saude))
        estrato["medidas"]["controle_da_saude_sem_resposta"] = len(com_real) - len(com_saude)
        estrato["sinal"] = _sinal(itens)
        estratos[kind] = estrato
    custo: dict[str, Any] = {"nivel": "PROVED (medido pelo transporte)" if enviado else "not_run (--seco)"}
    if teto is not None:
        ms = [c.ms for c in teto.chamadas if c.ok]
        custo.update({"teto_usd": teto.teto_usd, "usd": round(teto.gasto_usd, 6),
                      "chamadas": len(teto.chamadas), "ok": sum(1 for c in teto.chamadas if c.ok),
                      "falhas": dict(Counter(str(c.motivo) for c in teto.chamadas if not c.ok)),
                      "tokens": sum(c.tokens_entrada + c.tokens_saida for c in teto.chamadas),
                      "ms": rel._percentis(ms), "modelos": sorted({c.modelo for c in teto.chamadas}),
                      "em_ai_calls": False})
    return {
        "aviso": AVISO.format(n=rotulos),
        "consumidor": "curador", "classe": "C0", "estado": versao_do_estado, "enviado": enviado,
        "interrompido": interrompido,
        "casos": len(casos), "rodados": sum(1 for r in registros if r is not None), "fora_de_f1": dict(fora),
        "pedidos_secos": pedidos_secos, "estratos": estratos, "custo": custo, "linhas": linhas_saida,
        "nota": "concordância com o curador principal é acompanhamento, nunca GO sozinha (golden set §2)",
    }


#: O que o braço de controle mede (golden set §2), para quem lê a concordância dele com o curador.
CONTROLE_EM_UMA_LINHA: Final = (
    "O controle é a regra local gratuita do golden set (mais evidência contra que a favor → `revisar`, senão `manter`); "
    "a concordância dele com o curador mede quanto o parecer do curador se explica só pelas contagens de evidência. "
    "O 2º controle, só de acompanhamento, é a regra da saúde (`CONTROLE_DA_SAUDE`): quanto do parecer se explica só "
    "pelo rótulo de saúde do dossiê.")


def _resposta_do_sinal(m: Mapping[str, Any], s: Mapping[str, Any]) -> str:
    """A pergunta do 31.19 respondida numa frase: estados distintos × respostas distintas, e os instáveis."""
    if not s["estados_com_resposta"]:
        return "sem resposta do Jev: nada a dizer sobre o sinal"
    veredito = ("a entrada NÃO separa os casos (uma resposta só)" if s["respostas_distintas_entre_estados"] <= 1
                else "a entrada separa ao menos parte dos casos")
    return (f"{m['estados_distintos']} estados C0 distintos, {s['estados_com_resposta']} com resposta →"
            f" {s['respostas_distintas_entre_estados']} respostas distintas (maior probabilidade); estados instáveis"
            f" (respostas diferentes no MESMO estado): {len(s['estados_instaveis'])}. {veredito}.")


def em_markdown(r: Mapping[str, Any]) -> str:
    linhas = [f"# Braço offline do Jev — {r['consumidor']} ({r['classe']}, estado {r['estado']})", "",
              f"**{r['aviso']}**", ""]
    linhas += [f"**Sinal ({kind}):** {_resposta_do_sinal(e['medidas'], e['sinal'])}"
               for kind, e in r["estratos"].items()]
    linhas += ["", f"- Enviado: {'sim' if r['enviado'] else 'não (--seco)'}; casos {r['casos']}, rodados {r['rodados']};"
               f" fora de F1 {r['fora_de_f1'] or '—'}; interrompido: {r['interrompido'] or 'não'}.",
               f"- {CONTROLE_EM_UMA_LINHA}"]
    for kind, e in r["estratos"].items():
        m, s = e["medidas"], e["sinal"]
        linhas += ["", f"## {kind}", "",
                   f"- Pedidos {m['pedidos']}, respondidos {m['respondidos']}, cobertura {m['cobertura']};"
                   f" fallbacks {m['fallbacks'] or '—'}.",
                   f"- Rótulos {m['rotulos']} → {e['veredito']} ({'; '.join(e['falhou']) or 'todos os critérios'}).",
                   f"- Estados C0 distintos {m['estados_distintos']}; com resposta {s['estados_com_resposta']};"
                   f" respostas distintas entre estados {s['respostas_distintas_entre_estados']};"
                   f" estados instáveis {len(s['estados_instaveis'])}.",
                   f"- Concordância com o curador (acompanhamento): escolha {m['concordancia_com_o_curador']},"
                   f" maior probabilidade {m['concordancia_da_maior_com_o_curador']},"
                   f" controle {m['concordancia_do_controle_com_o_curador']},"
                   f" regra da saúde {m['concordancia_do_controle_da_saude_com_o_curador']}"
                   f" (sem resposta {m['controle_da_saude_sem_resposta']}).",
                   "- Cobertura por limiar (maior probabilidade): "
                   + ", ".join(f"{k}: {v['maior_probabilidade']}" for k, v in m["cobertura_por_limiar"].items()) + "."]
    c = r["custo"]
    linhas += ["", "## Custo", "", f"- {c['nivel']}"
               + (f": US$ {c['usd']} de {c['teto_usd']}, {c['chamadas']} chamadas ({c['ok']} ok), tokens {c['tokens']},"
                  f" ms p50 {c['ms']['p50']} / p95 {c['ms']['p95']}." if "usd" in c else ".")]
    if "usd" in c:
        linhas.append(f"- Estas {c['chamadas']} chamadas NÃO estão em `ai_calls` (o braço abre o banco só para leitura):"
                      f" o total de US$ {c['usd']} explica a diferença no livro-caixa da conta do Jev (ADR-051).")
    return "\n".join(linhas) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--db", default=str(RAIZ / "data" / "poc.sqlite3"))
    p.add_argument("--dsn", help="PostgreSQL; no lugar de --db")
    p.add_argument("--consumidor", default="curador", help="hoje só `curador` (R1)")
    p.add_argument("--estado", default=ESTADO_DA_SOMBRA, choices=VERSOES_DO_ESTADO,
                   help="a versão do estado do curador (o v2 leva os campos de sinal; a sombra do runtime segue no v1)")
    p.add_argument("--desde", help="ISO-8601 UTC; padrão: todas as revisões ainda no banco")
    p.add_argument("--autor-dono", action="append", default=[], metavar="NOME",
                   help="o `decided_by` do dono no rótulo 1 (repetível); sem ele, o rótulo 1 fica desligado")
    p.add_argument("--rotulos-em-bloco", metavar="ARQ",
                   help="31.11: o JSON dos rótulos confirmados pelo dono em bloco (fonte à parte, fora do veredito)")
    p.add_argument("--enviar", action="store_true", help="chama o Jev de verdade (exige --teto)")
    p.add_argument("--teto", type=float, help=f"teto da rodada em US$ (no máximo {TETO_MAX_USD})")
    p.add_argument("--json", help="arquivo do JSON; padrão: a tela")
    p.add_argument("--md", help="arquivo do Markdown")
    args = p.parse_args(argv)
    if args.consumidor not in CONSUMIDORES_LIBERADOS:
        raise SystemExit(f"consumidor fora deste braço: {args.consumidor} (liberados: {', '.join(CONSUMIDORES_LIBERADOS)})")
    teto: Teto | None = None
    if args.enviar:
        if args.teto is None or not 0 < args.teto <= TETO_MAX_USD:
            raise SystemExit(f"--enviar exige --teto entre 0 e {TETO_MAX_USD} (US$)")
        teto = Teto(args.teto)
    confirmacoes = rel.ler_confirmacoes(args.rotulos_em_bloco)
    db = rel._abrir(args)
    try:
        casos, fora = casos_do_curador(db, desde=args.desde, versao_do_estado=args.estado,
                                       autores_dono=frozenset(n.strip() for n in args.autor_dono if n.strip()),
                                       confirmacoes=confirmacoes)
    finally:
        db.close()
    seco = DecisorSeco() if teto is None else None
    decisor: Decisor = seco if seco is not None else decisor_real(teto)  # type: ignore[arg-type]
    registros, interrompido = rodar(casos, decisor)
    rel_ = montar(casos, registros, fora=fora, versao_do_estado=args.estado, agora=datetime.now(UTC),
                  enviado=teto is not None,
                  interrompido=interrompido, teto=teto, pedidos_secos=len(seco.pedidos) if seco is not None else None)
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
