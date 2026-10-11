"""31.338: depois de reabrir o app de frio, `voltar_ao_estado_conhecido` espera a tela deixar de ser desconhecida antes de decidir.

Prova real do P-043 (11/10/2026, Outlook no `android-01`, deploy 74): o app estava fora, o preparo da exploração fez
[reabrir, voltar]; a tela de abertura do app ainda era desconhecida, o "voltar" dela TIROU o Outlook da frente e a IA começou
pela tela inicial do Android, então a exploração não ancorou (a destilação recusou a partida desconhecida, 31.327).

Agora a tela é relida até `leituras_apos_reabrir` vezes enquanto o app está na frente e a tela é desconhecida; o resto do
comportamento (voltar até `voltar_max`, nada de voltar em login/desafio) não muda.

Nível de prova: `simulated` (árvores e aparelho falsos). `real`: `not_run`.
"""
from __future__ import annotations

from typing import Any

from app.automation import conhecimento_de_telas as telas

from .test_conhecimento_de_telas import _EMAIL, _tela

PKG = "com.exemplo.email"
LANCADOR = "com.android.launcher3"
IDS = {"caixa": "message_list", "mensagem": "reply_button", "passagem": "splash_logo_desconhecido"}


def _arvore(nome: str) -> Any:
    if nome == "lancador":
        return _tela(("android.widget.TextView", "icon", "Apps"), pacote=LANCADOR)
    return _tela(("android.widget.View", IDS[nome], ""), pacote=PKG)


class _Aparelho:
    """Uma fila de telas que o app mostra, uma por observação, e o que `voltar` e `reabrir` fazem com ela."""

    def __init__(self, k: Any, inicial: list[str], depois_de_reabrir: list[str]) -> None:
        self.k = k
        self.fila = list(inicial)
        self.apos_reabrir = list(depois_de_reabrir)
        self.gestos: list[str] = []
        self.observacoes = 0

    async def observar(self) -> tuple[Any, str | None]:
        self.observacoes += 1
        nome = self.fila[0] if len(self.fila) == 1 else self.fila.pop(0)
        arv = _arvore(nome)
        return arv, next(iter(arv.packages))

    async def voltar(self) -> None:
        self.gestos.append("voltar")
        self.fila = ["lancador"] if self.fila[0] in ("passagem", "caixa") else ["caixa"]

    async def reabrir(self) -> None:
        self.gestos.append("reabrir")
        self.fila = list(self.apos_reabrir)

    async def ir(self, **kw: Any) -> Any:
        k = self.k
        return await telas.voltar_ao_estado_conhecido(
            k, observar=self.observar, voltar=self.voltar, reabrir=self.reabrir,
            reconhecer=lambda t, p: telas.classificar(k, t, package=p),
            espera_apos_reabrir_s=0.0, **kw)


def _k() -> Any:
    return telas.de_dados(_EMAIL)


async def test_o_app_aberto_de_frio_nao_leva_voltar_na_tela_de_passagem() -> None:
    ap = _Aparelho(_k(), ["lancador"], ["passagem", "passagem", "caixa"])
    _t, _p, final, passos = await ap.ir()
    assert passos == ["reabrir"] and ap.gestos == ["reabrir"]
    assert final.tela == "caixa" and not final.outro_app


async def test_a_espera_e_so_de_leitura_e_tem_limite() -> None:
    """Uma tela que NUNCA vira conhecida não prende o preparo: 3 leituras e segue como antes (voltar até o limite)."""
    ap = _Aparelho(_k(), ["lancador"], ["passagem"])
    _t, _p, final, passos = await ap.ir(leituras_apos_reabrir=3)
    assert passos[0] == "reabrir" and passos.count("reabrir") == 1 and "voltar" in passos
    # 1 (lançador) + 1 (logo depois do reabrir) + 3 leituras de espera
    assert ap.observacoes >= 1 + 1 + 3


async def test_tela_conhecida_depois_do_reabrir_nao_espera_nada() -> None:
    ap = _Aparelho(_k(), ["lancador"], ["mensagem"])
    ap.fila = ["lancador"]
    antes = ap.observacoes
    _t, _p, final, passos = await ap.ir(leituras_apos_reabrir=3)
    assert passos[:2] == ["reabrir", "voltar"]                         # a mensagem é conhecida: volta como sempre
    assert ap.observacoes - antes <= 4


async def test_sem_leituras_extras_o_comportamento_e_o_anterior() -> None:
    ap = _Aparelho(_k(), ["lancador"], ["passagem", "caixa"])
    _t, _p, final, passos = await ap.ir(leituras_apos_reabrir=0)
    assert passos[:2] == ["reabrir", "voltar"]                         # o defeito que o P-043 mostrou, se a espera for desligada


async def test_o_app_que_ja_esta_na_caixa_nao_reabre_nem_espera() -> None:
    ap = _Aparelho(_k(), ["caixa"], [])
    _t, _p, final, passos = await ap.ir()
    assert passos == [] and ap.gestos == [] and ap.observacoes == 1


async def test_outro_app_ainda_na_frente_depois_do_reabrir_devolve_a_tela_como_esta() -> None:
    ap = _Aparelho(_k(), ["lancador"], ["lancador"])
    _t, _p, final, passos = await ap.ir()
    assert passos == ["reabrir"] and final.outro_app
