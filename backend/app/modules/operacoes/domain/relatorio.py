"""O relatório consolidado da operação (31.195, adendo v1.111): a mesma leitura para a Canais (28.66) e a Portal (31.197).

Montado a partir do GET da operação (`ServicoDeOperacoes.ler`) e do aprendizado da operação (v1.96), sem consulta nova
e sem IA. Os nomes seguem o relatório que a Portal montava sozinha (`frontend/src/features/operacao/relatorio.ts`).

"Não medido" nunca vira zero: número, texto ou id sem dado é `None`, e o estado de três vias é `"nao_medido"`. Nenhum @
de conta sai daqui (`sem_arroba`, como o `semArroba` da Portal).

Os 16 critérios mínimos do dono (prova30, `diagnostico.md` § 1a, com 2b, 3b e 11b: 19 linhas) vêm com dois campos:
`nesta_operacao` (o que ESTA operação mostra, lido dos dados dela) e `estado` (implementado, testado em simulação,
provado em ambiente real, bloqueado, não implementado). `provado_real` só sai quando esta operação mostrou o critério com
IA real (`ambiente == "real"`); com o provedor simulado, `testado_em_simulacao`; sem sinal na operação, o estado de base
do código.
"""
from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence

_ARROBA = re.compile(r"@[A-Za-z0-9._]+")

#: (id, nome, estado de base). A base é a do diagnóstico da prova (`prova30/diagnostico.md` § 1a, 06/10), revista no
#: código do corte 60 onde a entrega mudou o estado: 4 (memória da operação, 125), 6 (pesquisa externa, 31.157) e 8
#: (`fatos_da_operacao` no rascunho, v1.93). A operação só SOBE o estado (`_ORDEM`), nunca o rebaixa.
CRITERIOS: tuple[tuple[str, str, str], ...] = (
    ("1", "Objetivo único", "testado_em_simulacao"),
    ("2", "Selecionar 20 a 30 agentes", "testado_em_simulacao"),
    ("2b", "Recusa de conduta", "implementado"),
    ("3", "Distribuir entre aparelhos (fila por aparelho)", "provado_real"),
    ("3b", "Distribuição entre workers", "implementado"),
    ("4", "Conhecimento comum disponível", "testado_em_simulacao"),
    ("5", "Detectar necessidade de informação", "nao_implementado"),
    ("6", "Obter informação externa", "implementado"),
    ("7", "Consolidar conhecimento e evidências", "implementado"),
    ("8", "Contexto aos agentes", "implementado"),
    ("9", "Navegar até o conteúdo-alvo", "provado_real"),
    ("10", "Interpretar o conteúdo", "implementado"),
    ("11", "Ação candidata individual", "provado_real"),
    ("11b", "Barreira e estado bloqueado", "implementado"),
    ("12", "Execuções diferentes entre si", "implementado"),
    ("13", "Acompanhar centralmente", "implementado"),
    ("14", "Registrar sucessos, falhas e evidências", "provado_real"),
    ("15", "Resultado da operação", "implementado"),
    ("16", "Preservar aprendizado", "testado_em_simulacao"),
)
ESTADOS_DO_CRITERIO = ("implementado", "testado_em_simulacao", "provado_real", "bloqueado", "nao_implementado")
#: Do menos ao mais provado; o estado do critério é o maior entre a base e o que a operação mostrou.
_ORDEM = {"nao_implementado": 0, "bloqueado": 1, "implementado": 2, "testado_em_simulacao": 3, "provado_real": 4}
FONTE_DA_BASE = "diagnóstico da prova (prova30 § 1a, 06/10), revisto no código do corte 60"


def sem_arroba(texto: object) -> str | None:
    return None if texto is None else _ARROBA.sub("@[omitido]", str(texto))


def _normal(texto: str) -> str:
    return " ".join(texto.casefold().split())


def _alcancou(alvos: Sequence[Mapping[str, object]], *estagios: str) -> bool:
    return any(str(e.get("estagio")) in estagios for a in alvos for e in _lista(a.get("estagios")))


def _lista(valor: object) -> list[Mapping[str, object]]:
    return [x for x in valor if isinstance(x, Mapping)] if isinstance(valor, list) else []


def conferencia(resultado: object) -> str:
    """A mesma de `conferenciaDaAcao` da Portal: sim, não, não conferida ou sem ação final."""
    acao = resultado.get("acao_final") if isinstance(resultado, Mapping) else None
    if not isinstance(acao, Mapping):
        return "sem_acao"
    v = acao.get("verificada")
    return "sim" if v is True else "nao" if v is False else "nao_conferida"


def textos(agentes: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Os textos gerados e os repetidos (critério 12): repetido = o mesmo texto, sem caixa e sem espaço a mais."""
    lista = [{"agente": a["persona"], "texto": a["texto"]} for a in agentes if a.get("texto")]
    grupos: dict[str, dict[str, object]] = {}
    for t in lista:
        g = grupos.setdefault(_normal(str(t["texto"])), {"texto": t["texto"], "agentes": []})
        g["agentes"].append(t["agente"])  # type: ignore[attr-defined]
    repetidos = [g for g in grupos.values() if len(g["agentes"]) > 1]  # type: ignore[arg-type]
    return {"total": len(lista), "distintos": len(grupos), "repetidos": repetidos, "lista": lista}


def falhas_por_motivo(agentes: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    contagem: Counter[tuple[str, str | None]] = Counter()
    for a in agentes:
        if a.get("estado") in ("bloqueado", "cancelado"):
            contagem[(str(a.get("motivo") or "sem motivo informado"), a.get("parou_em"))] += 1  # type: ignore[index]
    return [{"motivo": m, "parou_em": p, "agentes": n}
            for (m, p), n in sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0][0]))]


def identidades(capacidade: Mapping[str, object]) -> dict[str, object]:
    """Quantas das identidades pedidas executam hoje: conta no app, sessão válida e aparelho apto (a capacidade)."""
    def n(chave: str) -> int:
        v = capacidade.get(chave)
        return int(v) if isinstance(v, int) else 0
    pedidas, contas, sessoes, aptas = (n("solicitados"), n("contas_existentes"), n("sessoes_validas"),
                                       n("contas_disponiveis"))
    faltas = [("sem conta no app", pedidas - contas), ("sem sessão válida", contas - sessoes),
              ("aparelho indisponível", sessoes - aptas)]
    return {"solicitadas": pedidas, "executam_hoje": aptas, "deficit": pedidas - aptas,
            "nao_executam": [{"motivo": m, "n": q} for m, q in faltas if q > 0]}


def latencia(alvos: Sequence[Mapping[str, object]], por_estagio: object) -> dict[str, object]:
    duracoes: list[tuple[str, int]] = []
    for a in alvos:
        lat = a.get("latencia")
        d = lat.get("duracao_ms") if isinstance(lat, Mapping) else None
        if isinstance(d, int):
            duracoes.append((str(a["profile_id"]), d))
    mais_lento = max(duracoes, key=lambda x: x[1]) if duracoes else None
    return {"por_estagio": por_estagio if isinstance(por_estagio, Mapping) else {},
            "duracao_mediana_ms": round(statistics.median(d for _, d in duracoes)) if duracoes else None,
            "mais_lento": {"profile_id": mais_lento[0], "duracao_ms": mais_lento[1]} if mais_lento else None}


def criterios(op: Mapping[str, object], agentes: Sequence[Mapping[str, object]], *, ambiente: str,
              aprendizado_disponivel: bool) -> list[dict[str, object]]:
    """Os 19 critérios com o que ESTA operação mostra. `ambiente`: `real`, `simulado` ou `nao_medido` (sem chamada)."""
    alvos = _lista(op.get("alvos"))
    cap = op.get("capacidade") if isinstance(op.get("capacidade"), Mapping) else {}
    rodaram = [a for a in alvos if a.get("run_id")]
    aparelhos = {a.get("instance_id") for a in rodaram if a.get("instance_id")}
    custo = op.get("custo") if isinstance(op.get("custo"), Mapping) else {}
    pesquisa = custo.get("pesquisa_usd") if isinstance(custo, Mapping) else None
    com_texto = [a for a in agentes if a.get("texto")]
    tx = textos(agentes)

    def sim_nao(cond: bool, medido: bool = True) -> str:
        return "nao_medido" if not medido else "sim" if cond else "nao"

    sinais: dict[str, str] = {
        "1": "sim",
        "2": sim_nao(len(alvos) >= 20),
        "2b": "nao_medido",
        "3": sim_nao(len(aparelhos) > 1, medido=bool(rodaram)),
        "3b": "nao_medido",
        "4": sim_nao(_alcancou(alvos, "conhecimento_recuperado"), medido=bool(rodaram)),
        "5": "sim" if op.get("fontes_da_pesquisa") else "nao_medido",
        "6": "sim" if op.get("fontes_da_pesquisa") or (isinstance(pesquisa, (int, float)) and pesquisa > 0)
             else "nao_medido",
        "7": "sim" if aprendizado_disponivel else "nao_medido",
        "8": sim_nao(_alcancou(alvos, "conteudo_lido", "conhecimento_recuperado"), medido=bool(rodaram)),
        "9": sim_nao(_alcancou(alvos, "target_localizado", "post_localizado"), medido=bool(rodaram)),
        "10": sim_nao(_alcancou(alvos, "conteudo_lido"), medido=bool(rodaram)),
        "11": sim_nao(bool(com_texto), medido=bool(rodaram)),
        "11b": sim_nao(_alcancou(alvos, "acao_preparada", "acao_bloqueada"), medido=bool(rodaram)),
        "12": "nao_medido" if int(tx["total"]) < 2 else sim_nao(not tx["repetidos"]),  # type: ignore[call-overload]
        "13": "sim",
        "14": sim_nao(bool(rodaram), medido=bool(alvos)),
        "15": sim_nao(bool(cap)),
        "16": "sim" if aprendizado_disponivel else "nao_medido",
    }
    saida: list[dict[str, object]] = []
    for cid, nome, base in CRITERIOS:
        sinal = sinais[cid]
        mostrado = ("provado_real" if sinal == "sim" and ambiente == "real" else
                    "testado_em_simulacao" if sinal == "sim" and ambiente == "simulado" else base)
        estado = max(base, mostrado, key=lambda e: _ORDEM[e])
        saida.append({"id": cid, "nome": nome, "estado": estado, "nesta_operacao": sinal,
                      "evidencia": f"operação {op.get('id')}" if sinal == "sim" else None})
    return saida
