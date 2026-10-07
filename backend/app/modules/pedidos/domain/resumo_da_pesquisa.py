"""31.235: o resumo da pesquisa externa de uma operação, para o painel (campo `pesquisa` de GET /api/operacoes/{id}).

Lê só o que a pesquisa deixou na memória da operação: `pesquisa.estado` (o progresso, um texto curto que a própria
pesquisa escreve), os `livro.<item>` (31.231, fatos do Livro reaproveitados) e os `pesquisa.<hash>` (fatos da pesquisa
paga), mais o custo da pesquisa que o serviço da operação já soma. Nada de texto de fato nem de URL sai daqui: dos
fatos vão só a referência, a origem, o frescor e a confiança.

Estados:
* `reaproveitada_do_livro`: o Livro cobriu o pedido, sem chamada paga;
* `paga`: a pesquisa paga rodou;
* `falhou`: a última tentativa falhou e espera para tentar de novo;
* `nao_rodou`: a operação pediu pesquisa (tem assunto), mas nada rodou ainda (desligada, teto, antes da execução).
Sem assunto, a operação não pediu pesquisa: `None`.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.modules.pedidos.domain.memoria import Entrada

CHAVE_DO_ESTADO = "pesquisa.estado"
PREFIXO_DO_LIVRO = "livro."
PREFIXO_DA_PESQUISA = "pesquisa."
#: O começo do texto de `pesquisa.estado` em cada caminho (`PesquisaDaOperacao` escreve com estes prefixos).
ESTADO_FALHOU = "a pesquisa falhou"
ESTADO_REAPROVEITADO = "reaproveitado do Livro"
MARCA_DO_CRITERIO = "critério: "


def _menor(frescores: Iterable[str | None]) -> str | None:
    vistos = [f for f in frescores if f]
    return min(vistos) if vistos else None


def resumo(entradas: Iterable[Entrada], *, pediu: bool, ligada: bool, minimo_fatos: int,
           custo_usd: float | None) -> dict[str, object] | None:
    """O campo `pesquisa`; `None` quando a operação não pediu pesquisa."""
    if not pediu:
        return None
    da_pesquisa = [e for e in entradas if e.origem == "pesquisa"]
    estado = next((e for e in da_pesquisa if e.chave == CHAVE_DO_ESTADO), None)
    do_livro = [e for e in da_pesquisa if e.chave.startswith(PREFIXO_DO_LIVRO) and e.tipo == "descoberta"]
    pagos = [e for e in da_pesquisa if e.chave.startswith(PREFIXO_DA_PESQUISA) and e.chave != CHAVE_DO_ESTADO
             and e.tipo == "descoberta"]
    texto = estado.valor if estado is not None else ""
    fatos: list[dict[str, object]] = []
    if texto.startswith(ESTADO_FALHOU):
        situacao, criterio, frescor = "falhou", texto, None
    elif texto.startswith(ESTADO_REAPROVEITADO) or (estado is None and do_livro):
        situacao = "reaproveitada_do_livro"
        # O critério de cobertura vem por extenso no fim do estado ("…; critério: ≥2 fato(s) vivos, …").
        _, marca, depois = texto.partition(MARCA_DO_CRITERIO)
        criterio = depois if marca else "o Livro cobriu o pedido"
        frescor = _menor(e.frescor_ate for e in do_livro)
        fatos = [{"item": e.chave[len(PREFIXO_DO_LIVRO):], "origem": e.origem, "frescor_ate": e.frescor_ate,
                  "confianca": e.confianca} for e in do_livro]
    elif estado is not None or pagos or (custo_usd or 0) > 0:
        situacao = "paga"
        # Com o reaproveitamento desligado (`minimo_fatos` 0), o Livro nem foi consultado: não dizer que não cobriu.
        criterio = ("o Livro não cobriu o pedido; " if minimo_fatos > 0 else "") + "pesquisa paga" + (
            f": {texto}" if texto else "")
        frescor = _menor(e.frescor_ate for e in pagos)
    else:
        situacao, frescor = "nao_rodou", None
        criterio = ("a pesquisa ainda não rodou nesta operação" if ligada
                    else "a pesquisa externa está desligada nesta instalação")
    return {"estado": situacao, "criterio": criterio, "minimo_fatos": int(minimo_fatos), "frescor_ate": frescor,
            "custo_usd": None if custo_usd is None else round(float(custo_usd), 4), "fatos": fatos}
