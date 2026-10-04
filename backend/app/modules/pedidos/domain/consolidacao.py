"""Consolidação do pai (item 28.10, F4; docs/design/pedidos-persistentes.md §9): o relatório do pai lê o que os filhos
OBSERVARAM e GUARDARAM, nunca o texto livre das execuções deles.

Puro: stdlib. Duas fontes por filho, ambas estruturadas e já gravadas:

    observações  as de `pedido_observacoes` que o relatório de qualquer pedido aceita como fato: gravadas `observado`, COM
                 valor, numa ocorrência `concluida` (a mesma defesa em profundidade de `domain/relatorio.py`: falha e
                 incerteza nunca contam). Por (alvo, nome) vale a MAIS RECENTE do filho;
    memória      as entradas `descoberta`, `decisao` e `fonte` (fatos confirmados). `progresso` e `pendencia` são estado
                 do trabalho do próprio filho, não um valor a comparar, e ficam de fora; a resolvida também.

Conflito: dois ou mais valores DIFERENTES para a mesma chave (origem, alvo, nome) vindos de filhos diferentes. Não há voto,
maioria nem desempate: o relatório mostra todas as versões, cada uma com os filhos que a trouxeram. Valor igual vindo de
vários filhos aparece UMA vez, com a contagem das fontes.

Quem fala é o filho (id e papel). Nunca nome de persona, conta, aparelho de conta, e-mail, telefone, IP ou o texto do
comando: o título do filho nem entra. O `alvo` da observação é o id do aparelho (como no `observado` do pai), e só o painel e
a API do dono veem o relatório inteiro; o aviso que sai pelos canais leva SÓ as contagens de `resumo`.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.modules.pedidos.domain.colaboracao import ESTADOS_TERMINAIS
from app.modules.pedidos.domain.vistas import EM_ABERTO, ObservacaoVista, OcorrenciaVista

#: Os tipos de memória que são FATO a comparar entre filhos (o vocabulário é o de `domain/memoria.TIPOS`).
TIPOS_DE_MEMORIA = ("descoberta", "decisao", "fonte")
#: Quanto cabe em `valores` e em `conflitos`; o excesso vira um item que diz quanto ficou de fora.
MAX_VALORES = 200
MAX_CONFLITOS = 100
MAX_TEXTO = 300

ORIGEM_OBSERVACAO = "observacao"
ORIGEM_MEMORIA = "memoria"


@dataclass(frozen=True)
class EntradaDeMemoria:
    """O que a consolidação precisa de uma entrada de memória (`domain.memoria.Entrada` sem a versão)."""
    chave: str
    tipo: str
    valor: str
    resolvida: bool = False


@dataclass(frozen=True)
class FilhoVisto:
    id: str
    papel: str | None
    estado: str                                   # o estado do PEDIDO filho
    ocorrencias: Sequence[OcorrenciaVista] = ()
    observacoes: Sequence[ObservacaoVista] = ()
    memoria: Sequence[EntradaDeMemoria] = ()


@dataclass
class _Chave:
    origem: str
    alvo: str
    nome: str
    tipo: str
    #: valor normalizado -> (valor como foi lido pela primeira fonte, [(filho_id, papel)])
    versoes: dict[str, tuple[str, list[tuple[str, str | None]]]] = field(default_factory=dict)


def _norma(texto: str) -> str:
    return " ".join(texto.split())


def _curto(texto: str, limite: int = MAX_TEXTO) -> str:
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def _fonte(filho_id: str, papel: str | None) -> dict[str, object]:
    return {"filho_id": filho_id, "papel": papel}


def _observacoes_do_filho(f: FilhoVisto) -> dict[tuple[str, str], ObservacaoVista]:
    """A observação comprovada MAIS RECENTE do filho por (alvo, nome)."""
    estado_de = {o.id: o.estado for o in f.ocorrencias}
    melhores: dict[tuple[str, str], ObservacaoVista] = {}
    for x in sorted(f.observacoes, key=lambda x: (x.capturado_em, x.id)):
        if x.valor is None or x.situacao != "observado" or estado_de.get(x.ocorrencia_id) != "concluida":
            continue
        melhores[(x.alvo, x.nome)] = x
    return melhores


def _memoria_do_filho(f: FilhoVisto) -> list[EntradaDeMemoria]:
    return [e for e in f.memoria if e.tipo in TIPOS_DE_MEMORIA and not e.resolvida and e.valor.strip()]


def consolidar(filhos: Sequence[FilhoVisto]) -> dict[str, object]:
    """O bloco `consolidacao` do relatório do pai. Determinístico: mesma entrada, mesmo bloco, qualquer que seja a ordem."""
    chaves: dict[tuple[str, str, str], _Chave] = {}
    fontes: list[dict[str, object]] = []
    for f in sorted(filhos, key=lambda f: f.id):
        obs = _observacoes_do_filho(f)
        mem = _memoria_do_filho(f)
        for (alvo, nome), x in obs.items():
            c = chaves.setdefault((ORIGEM_OBSERVACAO, alvo, nome), _Chave(ORIGEM_OBSERVACAO, alvo, nome, x.tipo))
            _acrescentar(c, x.valor or "", f)
        for e in mem:
            c = chaves.setdefault((ORIGEM_MEMORIA, "", e.chave), _Chave(ORIGEM_MEMORIA, "", e.chave, e.tipo))
            _acrescentar(c, e.valor, f)
        com_dado = bool(obs or mem)
        em_andamento = f.estado not in ESTADOS_TERMINAIS or any(o.estado in EM_ABERTO for o in f.ocorrencias)
        fontes.append({"filho_id": f.id, "papel": f.papel, "estado": f.estado,
                       "situacao": "com_dado" if com_dado else "sem_dado", "em_andamento": em_andamento,
                       "observacoes": len(obs), "memoria": len(mem)})

    valores: list[dict[str, object]] = []
    conflitos: list[dict[str, object]] = []
    for chave in sorted(chaves):
        c = chaves[chave]
        versoes = [(v, quem) for _, (v, quem) in sorted(c.versoes.items())]
        if len(versoes) == 1:
            valor, quem = versoes[0]
            valores.append({"origem": c.origem, "alvo": c.alvo, "nome": c.nome, "tipo": c.tipo, "valor": _curto(valor),
                            "fontes": [_fonte(*q) for q in quem], "n_fontes": len(quem)})
        else:
            conflitos.append({"origem": c.origem, "alvo": c.alvo, "nome": c.nome, "tipo": c.tipo,
                              "versoes": [{"valor": _curto(v), "fontes": [_fonte(*q) for q in quem], "n_fontes": len(quem)}
                                          for v, quem in versoes]})

    omitidos_v = max(0, len(valores) - MAX_VALORES)
    omitidos_c = max(0, len(conflitos) - MAX_CONFLITOS)
    resumo = {
        "filhos": len(fontes),
        "com_dado": sum(1 for s in fontes if s["situacao"] == "com_dado"),
        "sem_dado": sum(1 for s in fontes if s["situacao"] == "sem_dado"),
        "em_andamento": sum(1 for s in fontes if s["em_andamento"]),
        "valores": len(valores),
        "conflitos": len(conflitos),
    }
    return {"fontes": fontes, "valores": valores[:MAX_VALORES], "conflitos": conflitos[:MAX_CONFLITOS],
            "omitidos": {"valores": omitidos_v, "conflitos": omitidos_c}, "resumo": resumo}


def _acrescentar(c: _Chave, valor: str, f: FilhoVisto) -> None:
    norma = _norma(valor)
    if norma not in c.versoes:
        c.versoes[norma] = (valor, [])
    c.versoes[norma][1].append((f.id, f.papel))


def itens_nao_cobertos(bloco: Mapping[str, object]) -> list[dict[str, object]]:
    """O que o bloco deixa de dizer, para o "não coberto" do relatório do pai: conflito, filho sem dado e filho em andamento.
    O texto cita só id e papel do filho e a chave do valor, nunca o valor (o valor está no bloco)."""
    itens: list[dict[str, object]] = []
    for c in bloco.get("conflitos", ()):                                           # type: ignore[attr-defined]
        itens.append({"tipo": "conflito_entre_filhos", "origem": c["origem"], "alvo": c["alvo"], "nome": c["nome"],
                      "texto": f"filhos divergem em '{c['nome']}': {len(c['versoes'])} valores diferentes, sem vencedor"})
    for s in bloco.get("fontes", ()):                                              # type: ignore[attr-defined]
        quem = f"filho {s['filho_id']}" + (f" ({s['papel']})" if s["papel"] else "")
        if s["em_andamento"]:
            itens.append({"tipo": "filho_em_andamento", "filho_id": s["filho_id"],
                          "texto": f"{quem} ainda está em andamento: o que ele trouxe até aqui pode mudar"})
        if s["situacao"] == "sem_dado":
            itens.append({"tipo": "filho_sem_dado", "filho_id": s["filho_id"],
                          "texto": f"{quem} não trouxe observação nem memória comprovada"})
    return itens
