"""Relatório do pedido: DETERMINÍSTICO, a partir das observações e das ocorrências (docs/design/pedidos-persistentes.md §6.6).

Três blocos que não se misturam:

    observado    fatos com fonte e instante: o que uma ocorrência CONCLUÍDA leu e que o fechamento comprovou;
    conclusão    só o que as observações sustentam, dita como inferência da AMOSTRA (nunca da população);
    não coberto  o que ficou de fora e o dono precisa saber: ocorrências perdidas, puladas, incertas, que falharam, foram
                 canceladas ou ainda estão em aberto; valores incertos ou ausentes; lacunas de período; pendências;
                 critérios sem verificação estruturada.

Falha ou incerteza nunca contam como sucesso: uma observação só entra em "observado" (e portanto só pode sustentar
conclusão) se a ocorrência dela terminou `concluida` E a observação foi gravada `observado` COM valor. Qualquer outra
combinação é rebaixada a incerta e vai para "não coberto" (defesa em profundidade: a infraestrutura já grava assim, mas o
relatório não confia nisso). Sem observação, a conclusão é VAZIA (`sem_conclusao`), nunca "nada mudou".

Determinismo: mesmas entradas, mesmo relatório, byte a byte em `serializar`. Nada de relógio (o período chega pronto), nada
de conjunto iterado, toda lista ordenada por chave fixa, ordem de entrada irrelevante, e o instante de geração fica FORA do
conteúdo (mora na linha de `pedido_relatorios`).

Puro: stdlib. Os instantes chegam como TEXTO no formato canônico de `chave.formatar_instante` para `previsto_para` e para o
período (a mesma escala, comparável como texto), e como ISO para `capturado_em`.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.modules.pedidos.domain import consolidacao as dominio_consolidacao
from app.modules.pedidos.domain.vistas import EM_ABERTO, NAO_COBREM, ObservacaoVista, OcorrenciaVista

VERSAO_DO_FORMATO = 1
#: Quanto cabe em "observado" e em "não coberto"; o excesso vira um item que diz quanto ficou de fora.
MAX_OBSERVADO = 500
MAX_NAO_COBERTO = 500
MAX_TEXTO = 200
ALCANCE = ("Vale só para as ocorrências e observações listadas neste relatório; não generaliza para além da amostra "
           "coletada.")

@dataclass(frozen=True)
class EntradaDoRelatorio:
    pedido_id: str
    pedido_versao: int
    periodo_ate: str
    periodo_de: str | None = None
    criterios: Sequence[str] = ()
    ocorrencias: Sequence[OcorrenciaVista] = ()
    observacoes: Sequence[ObservacaoVista] = ()
    pendencias: Sequence[str] = ()     # os valores das pendências ABERTAS da memória (§8.1)
    #: 28.10 F4: os filhos diretos do pedido (`domain/consolidacao.FilhoVisto`). Vazio = nada de `consolidacao` no relatório,
    #: que sai idêntico ao de antes. `consolidacao_falhou` diz que os filhos existem mas a leitura deles falhou.
    filhos: Sequence[object] = ()
    consolidacao_falhou: bool = False


@dataclass
class _Itens:
    observado: list[dict[str, object]] = field(default_factory=list)
    conclusao: list[dict[str, object]] = field(default_factory=list)
    nao_coberto: list[dict[str, object]] = field(default_factory=list)


def _curto(texto: str | None, limite: int = MAX_TEXTO) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= limite else t[: limite - 1] + "…"


def montar(entrada: EntradaDoRelatorio) -> dict[str, object]:
    """O relatório como estrutura JSON. `serializar` e `sha256_de` dão a forma canônica."""
    no_periodo = sorted((o for o in entrada.ocorrencias if _dentro(o.previsto_para, entrada.periodo_de, entrada.periodo_ate)),
                        key=lambda o: (o.previsto_para, o.id))
    por_id = {o.id: o for o in no_periodo}
    itens = _Itens()

    obs_validas = sorted((x for x in entrada.observacoes if x.ocorrencia_id in por_id),
                         key=lambda x: (x.capturado_em, x.ocorrencia_id, x.alvo, x.nome, x.id))
    comprovadas: list[ObservacaoVista] = []
    for x in obs_validas:
        oc = por_id[x.ocorrencia_id]
        efetiva = _situacao(x, oc)
        if efetiva == "observado":
            comprovadas.append(x)
        elif efetiva == "incerto":
            itens.nao_coberto.append({
                "tipo": "valor_incerto", "ocorrencia_id": oc.id, "previsto_para": oc.previsto_para, "alvo": x.alvo,
                "nome": x.nome, "valor": x.valor, "fonte": _curto(x.fonte, 80), "capturado_em": x.capturado_em,
                "texto": (f"valor de '{x.nome}' lido numa ocorrência {oc.estado}: não comprovado, não entra na conclusão")})
        elif oc.estado == "concluida":          # ausente: só importa quando a ocorrência diz que deu certo
            itens.nao_coberto.append({
                "tipo": "sem_valor", "ocorrencia_id": oc.id, "previsto_para": oc.previsto_para, "alvo": x.alvo,
                "nome": x.nome,
                "texto": f"ocorrência concluída sem valor lido para '{x.nome}'" + (f": {_curto(x.trecho, 120)}" if x.trecho else "")})

    com_observacao = {x.ocorrencia_id for x in obs_validas}
    for o in no_periodo:
        if o.estado == "concluida" and o.id not in com_observacao:
            itens.nao_coberto.append({"tipo": "sem_observacao", "ocorrencia_id": o.id, "previsto_para": o.previsto_para,
                                      "texto": "ocorrência concluída sem observação registrada"})
        elif o.estado in NAO_COBREM or o.estado in EM_ABERTO:
            em_aberto = o.estado in EM_ABERTO
            itens.nao_coberto.append({
                "tipo": "ocorrencia_em_aberto" if em_aberto else f"ocorrencia_{o.estado}", "ocorrencia_id": o.id,
                "previsto_para": o.previsto_para, "estado": o.estado, "motivo": _curto(o.motivo, 160) or None,
                "texto": ("ocorrência ainda não terminou" if em_aberto else f"ocorrência {o.estado}")
                         + (f": {_curto(o.motivo, 160)}" if o.motivo else "")})

    _lacunas(no_periodo, itens, entrada)
    consolidacao = None
    if entrada.filhos:
        consolidacao = dominio_consolidacao.consolidar(entrada.filhos)        # type: ignore[arg-type]
        itens.nao_coberto.extend(dominio_consolidacao.itens_nao_cobertos(consolidacao))
    elif entrada.consolidacao_falhou:
        itens.nao_coberto.append({"tipo": "consolidacao_indisponivel",
                                  "texto": "a leitura das observações e da memória dos filhos falhou: sem consolidação"})
    for c in entrada.criterios:
        itens.nao_coberto.append({"tipo": "criterio_nao_avaliado", "criterio": _curto(c),
                                  "texto": "critério sem verificação estruturada nesta versão: só o fechamento da execução o atesta"})
    for p in sorted(entrada.pendencias):
        itens.nao_coberto.append({"tipo": "pendencia", "texto": f"pendência aberta: {_curto(p)}"})

    omitidas = max(0, len(comprovadas) - MAX_OBSERVADO)
    mostradas = comprovadas[omitidas:]           # as mais recentes: a lista está em ordem de instante
    if omitidas:
        itens.nao_coberto.append({"tipo": "observado_truncado", "omitidas": omitidas,
                                  "texto": f"{omitidas} observação(ões) mais antiga(s) ficaram fora desta lista"})
    for x in mostradas:
        oc = por_id[x.ocorrencia_id]
        itens.observado.append({
            "id": x.id, "ocorrencia_id": oc.id, "previsto_para": oc.previsto_para, "alvo": x.alvo, "nome": x.nome,
            "tipo": x.tipo, "valor": x.valor, "fonte": _curto(x.fonte, 80), "capturado_em": x.capturado_em,
            "sha256": x.sha256})

    _concluir(no_periodo, comprovadas, itens)

    nao_coberto = sorted(itens.nao_coberto, key=lambda i: (str(i.get("previsto_para", "")), str(i["tipo"]),
                                                            str(i.get("ocorrencia_id", "")), str(i.get("alvo", "")),
                                                            str(i.get("nome", "")), str(i.get("texto", ""))))
    if len(nao_coberto) > MAX_NAO_COBERTO:
        resto = len(nao_coberto) - MAX_NAO_COBERTO
        nao_coberto = nao_coberto[:MAX_NAO_COBERTO] + [
            {"tipo": "nao_coberto_truncado", "omitidas": resto, "texto": f"mais {resto} item(ns) não coberto(s) omitido(s)"}]
    conclusao = itens.conclusao
    situacao = "sem_conclusao" if not conclusao else ("parcial" if nao_coberto else "sustentada")
    por_estado: dict[str, int] = {}
    for o in no_periodo:
        por_estado[o.estado] = por_estado.get(o.estado, 0) + 1
    custo = 0.0
    for o in no_periodo:                          # já ordenadas: a soma de ponto flutuante não depende da entrada
        custo += float(o.custo_usd or 0.0)
    saida: dict[str, object] = {
        "formato": VERSAO_DO_FORMATO,
        "pedido_id": entrada.pedido_id,
        "pedido_versao": entrada.pedido_versao,
        "periodo": {"de": entrada.periodo_de, "ate": entrada.periodo_ate},
        "ocorrencias": {"total": len(no_periodo), "por_estado": {k: por_estado[k] for k in sorted(por_estado)}},
        "observado": itens.observado,
        "conclusao": {"situacao": situacao, "alcance": ALCANCE, "itens": conclusao},
        "nao_coberto": nao_coberto,
        "custo_usd": round(custo, 6),
    }
    if consolidacao is not None:                    # a chave só existe com filhos: o relatório sem filhos não muda
        saida["consolidacao"] = consolidacao
    return saida


def serializar(relatorio: Mapping[str, object]) -> str:
    """A forma canônica: chaves ordenadas, sem espaço, UTF-8 legível. É o que se grava e o que se compara."""
    return json.dumps(relatorio, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def sha256_de(conteudo: str) -> str:
    return hashlib.sha256(conteudo.encode("utf-8")).hexdigest()


def _dentro(previsto: str, de: str | None, ate: str) -> bool:
    return (de is None or previsto >= de) and previsto <= ate


def _situacao(x: ObservacaoVista, oc: OcorrenciaVista) -> str:
    """Como a observação conta, DEPOIS da defesa em profundidade: `observado` exige valor E ocorrência concluída."""
    if x.valor is None or x.situacao == "ausente":
        return "ausente"
    if x.situacao == "observado" and oc.estado == "concluida":
        return "observado"
    return "incerto"


def _lacunas(no_periodo: Sequence[OcorrenciaVista], itens: _Itens, entrada: EntradaDoRelatorio) -> None:
    """Sequências de ocorrências seguidas que NÃO cobriram o período (qualquer estado menos `concluida`)."""
    if not no_periodo:
        itens.nao_coberto.append({"tipo": "periodo_sem_ocorrencias",
                                  "texto": "nenhuma ocorrência prevista no período: nada foi observado"})
        return
    corrida: list[OcorrenciaVista] = []

    def fechar() -> None:
        if corrida:
            itens.nao_coberto.append({
                "tipo": "lacuna", "de": corrida[0].previsto_para, "ate": corrida[-1].previsto_para,
                "previsto_para": corrida[0].previsto_para, "ocorrencias": len(corrida),
                "texto": f"lacuna: {len(corrida)} ocorrência(s) seguida(s) sem observação comprovada"})
            corrida.clear()

    for o in no_periodo:
        if o.estado == "concluida":
            fechar()
        else:
            corrida.append(o)
    fechar()


def _concluir(no_periodo: Sequence[OcorrenciaVista], comprovadas: Sequence[ObservacaoVista], itens: _Itens) -> None:
    """A conclusão: só contagem da amostra e o que os valores comprovados dizem; nada além delas."""
    if not comprovadas:
        return
    cobertas = sorted({x.ocorrencia_id for x in comprovadas})
    itens.conclusao.append({
        "tipo": "amostra", "ocorrencias_com_valor": len(cobertas), "ocorrencias_no_periodo": len(no_periodo),
        "base": [x.id for x in comprovadas][:50],
        "texto": (f"Na amostra coletada, {len(cobertas)} de {len(no_periodo)} ocorrência(s) do período terminaram "
                  f"concluídas com valor observado.")})
    grupos: dict[tuple[str, str], list[ObservacaoVista]] = {}
    for x in comprovadas:                          # `comprovadas` já está em ordem de instante
        grupos.setdefault((x.alvo, x.nome), []).append(x)
    for (alvo, nome) in sorted(grupos):
        g = grupos[(alvo, nome)]
        ultimo = g[-1]
        itens.conclusao.append({
            "tipo": "ultimo_valor", "alvo": alvo, "nome": nome, "valor": ultimo.valor,
            "capturado_em": ultimo.capturado_em, "observacoes": len(g), "base": [ultimo.id],
            "texto": (f"Último valor observado de '{nome}'" + (f" em {alvo}" if alvo else "")
                      + f", entre {len(g)} observação(ões) comprovada(s) da amostra.")})
        if len(g) >= 2:
            anterior = g[-2]
            mudou = anterior.valor != ultimo.valor
            itens.conclusao.append({
                "tipo": "variacao", "alvo": alvo, "nome": nome, "mudou": mudou, "anterior": anterior.valor,
                "atual": ultimo.valor, "base": [anterior.id, ultimo.id],
                "texto": (f"Na amostra coletada, o valor de '{nome}' " + ("mudou" if mudou else "não mudou")
                          + " entre as duas últimas observações comprovadas.")})
