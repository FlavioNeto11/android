"""O adaptador SIMULADO da porta `CuradorDeIA` (30.11): determinístico, sem rede, sem modelo, sem custo. Serve aos
testes e ao modo `shadow` sem gasto até existir o adaptador do hub de IA (30.12, frente Jev). Toda revisão dele é
gravada com `simulated = 1` e `provedor = 'simulado'`: nunca conta como prova real nem como parecer de IA.

A regra é só para exercitar o caminho inteiro (pedido, validação, registro, aviso): mais evidência contra que a favor
→ `observar` com a causa `evidencia_contraditoria`; senão `manter`. Cita o próprio item e as evidências do dossiê, que
é o que a validação exige. Os botões (`inventar_citacao`, `orcamento_esgotado`, `conclusao`) existem para os testes
provocarem a resposta inválida, o corte do hub e a triagem da conclusão sem remendo por fora.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.modules.learning.application.ports import PedidoDeRevisao, RecusaDoProvedor, RespostaDeRevisao
from app.modules.skills.domain.document import JsonObject

MODELO_SIMULADO = "simulado-curador-v1"


@dataclass(slots=True)
class CuradorSimulado:
    provedor: str = "simulado"
    simulado: bool = True
    inventar_citacao: bool = False
    orcamento_esgotado: bool = False
    conclusao: str | None = None
    pedidos: list[PedidoDeRevisao] = field(default_factory=list)

    def revisar(self, pedido: PedidoDeRevisao) -> RespostaDeRevisao:
        if self.orcamento_esgotado:
            raise RecusaDoProvedor("orçamento do curador esgotado (simulado)", kind="budget")
        self.pedidos.append(pedido)
        d = pedido.dossie
        item = d.get("item")
        item_id = item.get("id") if isinstance(item, dict) else None
        evidencias = d.get("evidencias")
        lista = evidencias.get("lista") if isinstance(evidencias, dict) else None
        lista = lista if isinstance(lista, list) else []
        contra = sum(1 for e in lista if isinstance(e, dict) and e.get("posicao") != "for")
        citadas = [str(item_id)] if isinstance(item_id, str) else []
        citadas += [str(e["id"]) for e in lista[:3] if isinstance(e, dict) and isinstance(e.get("id"), str)]
        if self.inventar_citacao:
            citadas.append("ev:999999999")
        bruto: JsonObject = {"decisao": "observar" if contra > len(lista) - contra else "manter",
                             "evidencias_citadas": list(citadas), "faixa": pedido.classe,
                             "causa": "evidencia_contraditoria" if contra > len(lista) - contra else "reproduz_bem"}
        if self.conclusao is not None:
            bruto["conclusao"] = self.conclusao
        # Sem probabilidade medida: o simulado não é um `choice`; a confiança fica sem medida.
        return RespostaDeRevisao(bruto=bruto, probabilidade=None, modelo=MODELO_SIMULADO, usd=None, ai_call_id=None)


__all__ = ["MODELO_SIMULADO", "CuradorSimulado"]
