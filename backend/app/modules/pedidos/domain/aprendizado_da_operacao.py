"""O aprendizado de UMA operação, nas 10 perguntas do dono (prova30 A3, extensão do 31.157). Puro: sem banco nem relógio.

A infraestrutura (`infrastructure/aprendizado_da_operacao.py`) junta, pelas execuções da operação, o que o Livro, a
memória da persona e a memória da operação guardaram, e entrega cada coisa como um `Item` com origem, evidência,
confiança e frescor. Aqui ficam só as regras que respondem às perguntas: quem é de quê, o que é reutilizável, o que se
revisa. Uma régua só de confiança: a da memória da operação (`confirmado`/`hipotese`, `memoria.CONFIANCAS`); o estado do
Livro e a confiança 0–1 da memória da persona são traduzidos para ela por `confianca_do_livro` e
`confianca_da_persona`, e o valor original fica ao lado, em `estado`.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

from app.modules.pedidos.domain.memoria import CONFIANCAS

#: As 10 perguntas, na ordem do dono. A chave é o contrato (adendo do 31.162); o título é o texto da tela.
PERGUNTAS: tuple[tuple[str, str], ...] = (
    ("plataforma_aprendeu", "O que a plataforma aprendeu"),
    ("persona_aprendeu", "O que uma persona aprendeu"),
    ("do_app", "O que pertence ao app"),
    ("do_processo", "O que pertence ao processo"),
    ("conhecimento_geral", "O que é conhecimento geral"),
    ("fontes_externas", "O que veio de fontes externas"),
    ("fontes_que_sustentam", "Quais fontes sustentam um conhecimento"),
    ("reutilizavel", "O que é reutilizável"),
    ("revisar_ou_descartar", "O que revisar ou descartar"),
    ("falhas_que_geraram_aprendizado", "Quais falhas geraram aprendizado"),
)

#: Escopo de cada item: do app (como a tela funciona: receita, tela, lição sem ação), do processo (a sequência: fluxo,
#: lição de uma ação ou etapa), da persona (memória e interação dela), da operação (fatos, fontes e leituras comuns) e
#: falha (o que deu errado e virou sinal ou transição).
ESCOPOS = ("app", "processo", "persona", "operacao", "falha")

#: Estados do Livro que valem como `confirmado`; os de `REVISAR_NO_LIVRO` pedem revisão; o resto (`candidate`,
#: `superseded`) é `hipotese` sem pedir nada.
CONFIRMADOS_NO_LIVRO = frozenset({"active", "published", "validated"})
REVISAR_NO_LIVRO = frozenset({"quarantined", "disabled", "deprecated", "candidate"})
#: A memória da persona guarda confiança 0–1; daqui para cima ela conta como confirmada (a mesma régua do contexto
#: social, que só injeta o que passou de 0,7).
PERSONA_CONFIRMADA = 0.7

#: O que este relatório não responde, e por quê (como o "não coberto" do relatório do pedido).
NAO_COBERTO: tuple[tuple[str, str], ...] = (
    ("conhecimento_geral", "A promoção de um fato da operação a conhecimento geral do Livro (curadoria) não existe: "
                           "aqui aparecem os fatos confirmados da operação, que são candidatos."),
    ("pedido_relatorios", "A operação não é um pedido: o relatório periódico do pedido não se aplica a ela."),
    ("persona_aprendeu", "Só a memória que a persona aprendeu de uma interação liga-se à execução; a memória de "
                         "observação de tela não guarda a execução e não aparece aqui."),
    ("falhas_que_geraram_aprendizado", "O backlog de falhas não guarda a execução: a ligação com a operação é "
                                       "inferida por app, ação e tipo de falha, e vem marcada `inferida`."),
)


#: Tipos que são registro do que aconteceu, não conhecimento a reutilizar.
NAO_E_CONHECIMENTO = frozenset({"falha", "interacao", "observacao", "registro"})


@dataclass(frozen=True)
class Item:
    ref: str                                  # `receita:12`, `fluxo:f-…`, `licao:…`, `fato:<chave>`, `sinal:…`
    tipo: str                                 # receita | fluxo | licao | tela | memoria | interacao | fato | fonte | registro | observacao | falha
    escopo: str                               # um de ESCOPOS
    resumo: str                               # já redigido e cortado pela infraestrutura
    origem: str                               # quem afirmou: execucao, ensino, recovery, operador, leitura, pesquisa, …
    confianca: str                            # confirmado | hipotese
    estado: str = ""                          # o valor original (estado do Livro, situação da observação, confiança 0–1)
    evidencia: tuple[str, ...] = ()           # run ids, ids de observação, urls
    persona: str | None = None                # profile_id; None = da operação inteira
    observado_em: str | None = None
    frescor_ate: str | None = None
    a_favor: int = 0
    contra: int = 0
    inferida: bool = False

    def vale(self, agora: str) -> bool:
        return self.frescor_ate is None or self.frescor_ate > agora

    def como_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["evidencia"] = list(self.evidencia)
        return d


def confianca_do_livro(estado: str) -> str:
    return "confirmado" if estado in CONFIRMADOS_NO_LIVRO else "hipotese"


def confianca_da_persona(valor: float | None) -> str:
    return "confirmado" if valor is not None and valor >= PERSONA_CONFIRMADA else "hipotese"


def reutilizavel(item: Item, agora: str) -> bool:
    """Confirmado, dentro do frescor e sem evidência contra; no Livro, com evidência a favor desta operação."""
    if item.tipo in NAO_E_CONHECIMENTO or item.confianca != "confirmado" or not item.vale(agora):
        return False
    if item.contra:
        return False
    return item.a_favor > 0 if item.escopo in ("app", "processo") else True


def a_revisar(item: Item, agora: str) -> str | None:
    """Por que revisar ou descartar (o motivo, para a tela), ou None."""
    if item.contra:
        return f"{item.contra} evidência(s) contra nesta operação"
    if item.tipo == "observacao" and item.estado == "incerto":
        return "leitura incerta: difere da leitura da operação"
    if not item.vale(agora) and item.tipo not in NAO_E_CONHECIMENTO:
        return "vencido: passou do frescor"
    if item.escopo in ("app", "processo") and item.estado in REVISAR_NO_LIVRO:
        return f"no Livro em {item.estado}"
    if item.confianca == "hipotese" and item.tipo in ("fato", "memoria"):
        return "hipótese: não confirmada"
    return None


def _sustentacao(itens: Sequence[Item]) -> list[dict[str, object]]:
    """Cada conhecimento (fato da operação, item do Livro, memória da persona) com o que o sustenta. A evidência de um
    fato é o id da observação; a fonte que cita essa mesma observação dá o título e a URL."""
    fonte_da_obs = {e: f for f in itens if f.tipo == "fonte" for e in f.evidencia}
    saida: list[dict[str, object]] = []
    for i in itens:
        if i.tipo in NAO_E_CONHECIMENTO or i.tipo == "fonte" or not i.evidencia:
            continue
        saida.append({"ref": i.ref, "confianca": i.confianca,
                      "fontes": [{"ref": fonte_da_obs[e].ref, "resumo": fonte_da_obs[e].resumo, "observacao": e}
                                 if e in fonte_da_obs else {"ref": e} for e in i.evidencia]})
    return saida


def responder(itens: Sequence[Item], *, agora: str, persona: str | None = None) -> dict[str, object]:
    """As 10 respostas. Com `persona`, fica o que é dela mais o que é da operação inteira (`persona: null`)."""
    for i in itens:
        if i.confianca not in CONFIANCAS or i.escopo not in ESCOPOS:
            raise ValueError(f"item fora do vocabulário: {i.ref}")
    vistos = [i for i in itens if persona is None or i.persona in (None, persona)]

    def lista(cond: Iterable[Item]) -> list[dict[str, object]]:
        return [i.como_dict() for i in cond]

    revisar = [{**i.como_dict(), "motivo": m} for i in vistos if (m := a_revisar(i, agora))]
    respostas: dict[str, object] = {
        "plataforma_aprendeu": lista(i for i in vistos if i.escopo in ("app", "processo")),
        "persona_aprendeu": lista(i for i in vistos if i.escopo == "persona"),
        "do_app": lista(i for i in vistos if i.escopo == "app"),
        "do_processo": lista(i for i in vistos if i.escopo == "processo"),
        "conhecimento_geral": lista(i for i in vistos if i.tipo == "fato" and i.confianca == "confirmado"
                                    and i.vale(agora)),
        "fontes_externas": lista(i for i in vistos if i.tipo == "fonte" and i.origem == "pesquisa"),
        "fontes_que_sustentam": _sustentacao(vistos),
        "reutilizavel": lista(i for i in vistos if reutilizavel(i, agora)),
        "revisar_ou_descartar": revisar,
        "falhas_que_geraram_aprendizado": lista(i for i in vistos if i.escopo == "falha"),
    }
    return {
        "perguntas": [{"chave": k, "titulo": t, "itens": respostas[k]} for k, t in PERGUNTAS],
        "contagem": {k: len(v) if isinstance(v, list) else 0 for k, v in respostas.items()},
        "nao_coberto": [{"chave": k, "motivo": m} for k, m in NAO_COBERTO],
        "persona": persona,
    }
