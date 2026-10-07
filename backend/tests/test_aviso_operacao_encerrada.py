"""Item 28.62 — o aviso do fim da operação com N agentes (prova de capacidade de 07/10).

Prova `simulated`: o `data` é o do evento `operacao.encerrada` (`operacao_id`, `status`, `capacidade`, `custo`), sem
Telegram. É rotina (a janela os junta, sem cartão no Trello), um aviso por operação (a chave é o id dela), com contagens,
os motivos de parada em palavras e o custo em dólar. Nunca sai o comando, handle de conta nem nome de persona.
`real`: `not_run` (a orquestradora confere na rodada de 07/10).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.modules.avisos.domain.mensagem import (JANELA, ROTINA, ROTULOS, ROTULOS_AGRUPADOS, TIPOS_DA_JANELA,
                                                aviso_de_evento, entrega_do_tipo, titulo_agrupado)
from app.modules.avisos.infrastructure.servico import KINDS_QUE_AVISAM

from app.modules.operacoes.infrastructure.servico import AlvoPedido

from .conftest import Harness
from .test_avisos_servico import AQUI, CanalFalso, Relogio, _backend, _cfg
from .test_operacoes import _alvo, _conta, _persona, _pedido, _servico

PAINEL = "http://painel.local:8000"
TIPO = "operacao.encerrada"
OP = "op-20261007120000-a1b2c3"


def _sem_filtro(texto: str) -> str:
    return texto


def _dados(status: str = "concluida", *, solicitados: int = 5, concluidas: int = 5, bloqueadas: int = 0,
           em_curso: int = 0, motivos: dict[str, int] | None = None, custo: object = None,
           operacao_id: object = OP) -> dict[str, object]:
    dados: dict[str, object] = {
        "operacao_id": operacao_id, "status": status,
        "capacidade": {"solicitados": solicitados, "contas_existentes": solicitados, "sessoes_validas": solicitados,
                       "contas_disponiveis": solicitados, "concluidas": concluidas, "bloqueadas": bloqueadas,
                       "em_curso": em_curso, "motivos": motivos or {}}}
    if custo is not None:
        dados["custo"] = custo
    return dados


def _aviso(dados: dict[str, object], **kw: Any) -> Any:
    kw.setdefault("redigir", _sem_filtro)
    return aviso_de_evento("operacao.encerrada", dados, 7, PAINEL, **kw)


def test_o_tipo_e_rotina_na_janela_assinado_e_com_rotulo_e_plural() -> None:
    assert "operacao.encerrada" in KINDS_QUE_AVISAM
    aviso = _aviso(_dados())
    assert aviso is not None
    assert (aviso.tipo, aviso.nivel, entrega_do_tipo(TIPO)) == (TIPO, ROTINA, JANELA)
    assert TIPO in TIPOS_DA_JANELA and TIPO in ROTULOS and TIPO in ROTULOS_AGRUPADOS
    assert "3 operações foram encerradas" in titulo_agrupado(TIPO, 3)


def test_concluida_com_custo_diz_placar_custo_e_critico_nada() -> None:
    aviso = _aviso(_dados(custo={"pesquisa_usd": 0.4, "alvos_usd": 0.83, "total_usd": 1.23}))
    assert aviso is not None
    assert aviso.titulo.endswith("✅ Operação encerrada: 5 de 5 agentes concluídos")
    linhas = aviso.corpo.splitlines()
    assert linhas[0] == "Solicitados: 5; concluídos: 5; bloqueados: 0; em curso: 0."
    assert "Custo: US$ 1.23 (pesquisa externa US$ 0.40; agentes US$ 0.83)." in linhas
    assert "Crítico: nada." in linhas
    assert not any(x.startswith("Paradas") for x in linhas)
    assert aviso.link == PAINEL + "/#/operacoes"


def test_parcial_com_bloqueios_diz_os_motivos_mais_comuns_e_o_critico() -> None:
    motivos = {"teto de custo": 2, "sem conta": 1, "sem sessão": 1, "aparelho indisponível": 1, "aguarda liberação": 1}
    aviso = _aviso(_dados("concluida_com_bloqueios", solicitados=10, concluidas=4, bloqueadas=6, motivos=motivos,
                          custo={"pesquisa_usd": 0, "alvos_usd": 0.5, "total_usd": 0.5}))
    assert aviso is not None
    assert aviso.titulo.endswith("⚠️ Operação encerrada: 4 de 10 agentes concluídos")
    linhas = aviso.corpo.splitlines()
    assert "Paradas: 2 por teto de custo; 1 por aguarda liberação; 1 por aparelho indisponível; 2 por outros motivos." in linhas
    assert "Crítico: 6 alvos bloqueados; veja a tela Operação." in linhas
    assert "Custo: US$ 0.50 (pesquisa externa menos de US$ 0.01; agentes US$ 0.50)." in linhas


def test_motivo_que_o_filtro_mudaria_nao_sai_e_vira_outros() -> None:
    def redator(texto: str) -> str:
        return texto.replace("zoe", "[nome]")
    aviso = _aviso(_dados("concluida_com_bloqueios", concluidas=3, bloqueadas=2, motivos={"zoe sem conta": 2}),
                   redigir=redator)
    assert aviso is not None
    assert "zoe" not in aviso.corpo
    assert "Paradas: 2 sem motivo dito (veja o painel)." in aviso.corpo.splitlines()


def test_cancelada_avisa_com_assunto_de_cancelamento() -> None:
    aviso = _aviso(_dados("cancelada", solicitados=4, concluidas=1, bloqueadas=1, em_curso=2, motivos={"sem conta": 1}))
    assert aviso is not None
    assert aviso.titulo.endswith("🛑 Operação cancelada: 1 de 4 agentes concluídos")
    assert "Solicitados: 4; concluídos: 1; bloqueados: 1; em curso: 2." in aviso.corpo.splitlines()


@pytest.mark.parametrize("custo", [None, {}, {"total_usd": None}, "texto", {"total_usd": "x"}])
def test_sem_custo_no_dado_o_aviso_sai_sem_a_linha_de_custo(custo: object) -> None:
    aviso = _aviso(_dados(custo=custo))
    assert aviso is not None
    assert "Custo" not in aviso.corpo and "Crítico: nada." in aviso.corpo


def test_custo_so_com_o_total_sai_sem_a_quebra() -> None:
    aviso = _aviso(_dados(custo={"total_usd": 2}))
    assert aviso is not None
    assert "Custo: US$ 2.00." in aviso.corpo.splitlines()


def test_a_chave_e_a_do_fato_e_a_reemissao_nao_duplica() -> None:
    um, outro = _aviso(_dados()), _aviso(_dados("cancelada"))
    reemitido = aviso_de_evento("operacao.encerrada", _dados(), 99, PAINEL, redigir=_sem_filtro)
    assert um is not None and outro is not None and reemitido is not None
    assert um.chave == reemitido.chave == outro.chave == f"operacao:{OP}"
    assert _aviso(_dados(operacao_id="op-20261007120001-ffffff")).chave != um.chave  # type: ignore[union-attr]


@pytest.mark.parametrize("ident", [None, "", "op-1", "../x", 12, "op-20261007120000-ZZZZZZ"])
def test_id_de_operacao_fora_do_formato_nao_avisa(ident: object) -> None:
    assert _aviso(_dados(operacao_id=ident)) is None


def test_o_texto_nao_leva_comando_handle_nem_nome_e_o_evento_cru_nao_vaza() -> None:
    dados = _dados("concluida_com_bloqueios", concluidas=1, bloqueadas=1, motivos={"sem conta": 1},
                   custo={"total_usd": 0.1})
    dados["command"] = "comente no post de @zoe.dev dizendo oi"
    dados["alvos"] = [{"profile_id": "Zoe", "handle": "@zoe.dev", "motivo": "conta de @zoe.dev"}]
    aviso = _aviso(dados, nomes=["Zoe"])
    assert aviso is not None
    texto = f"{aviso.titulo}\n{aviso.corpo}"
    for proibido in ("comente", "@", "zoe", "Zoe", "oi", OP):
        assert proibido not in texto, f"{proibido!r} foi para fora"


def test_sem_redator_os_motivos_nao_saem_mas_o_resto_sim() -> None:
    aviso = aviso_de_evento("operacao.encerrada", _dados("concluida_com_bloqueios", concluidas=1, bloqueadas=1,
                                                         motivos={"sem conta": 1}), 1, PAINEL)
    assert aviso is not None
    assert "Paradas: 1 sem motivo dito (veja o painel)." in aviso.corpo.splitlines()


def test_sem_base_publica_o_aviso_sai_sem_link() -> None:
    aviso = aviso_de_evento("operacao.encerrada", _dados(), 1, None, redigir=_sem_filtro)
    assert aviso is not None and aviso.link is None


def test_o_servico_assina_o_tipo_e_uma_operacao_e_uma_mensagem(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    dados = _dados(custo={"pesquisa_usd": 0.1, "alvos_usd": 0.2, "total_usd": 0.3})
    assert servico.enfileirar_evento("operacao.encerrada", dados, 1) is True
    assert servico.enfileirar_evento("operacao.encerrada", dados, 2) is False, "a reemissão virou outra mensagem"
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 1
    assert banco.scalar("SELECT tipo FROM avisos_entregas") == TIPO
    assert entrega_do_tipo(str(banco.scalar("SELECT tipo FROM avisos_entregas"))) == JANELA


@pytest.mark.asyncio
async def test_o_fim_da_operacao_emite_o_evento_com_o_custo(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    pid = _persona(harness, "Eva", "android-02")
    _conta(harness, pid, "qa-user-03", sessao_em="android-02")
    s = _servico(harness)
    op = s.criar(_pedido([AlvoPedido(pid)], chave="teste-op-aviso-fim"))
    assert _alvo(op, pid)["run_id"]
    s.cancelar(op["id"])
    eventos = [e for e in st.bus.since(0) if e.kind == "operacao.encerrada" and (e.data or {}).get("operacao_id") == op["id"]]
    assert len(eventos) == 1
    custo = (eventos[0].data or {}).get("custo")
    assert isinstance(custo, dict) and set(custo) == {"pesquisa_usd", "alvos_usd", "total_usd"}
    aviso = aviso_de_evento("operacao.encerrada", eventos[0].data, eventos[0].id, PAINEL, redigir=_sem_filtro)
    assert aviso is not None and "cancelada" in aviso.titulo and aviso.chave == f"operacao:{op['id']}"
