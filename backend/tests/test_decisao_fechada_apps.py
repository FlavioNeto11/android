"""Sombra dos apps do comando (R5, item 31.13, ADR-069 item 21): um `noul` por app cadastrado, travado no código.

O que cada bloco prova:

- **Privacidade:** a origem `apps` manda SÓ o `comando` (opção A da orquestradora, 03/10: o `app` da execução é o app
  principal do plano, o rótulo da R5), a C3 só em `shadow`, e o estado é subconjunto estrito do da intenção, com o MESMO
  comando (a mesma C7 e o mesmo filtro).
- **Perguntas:** id opaco por app, nome (C2) só na instrução e nos critérios, mascarado e sem C7; teto de apps.
- **Travado:** com `R5_LIBERADA` falso nada se monta nem se lê, qualquer que seja o YAML; a transparência não anuncia a R5.
- **Ligação:** o controle é a leitura de apps citados sobre o comando ORIGINAL; a sombra grava uma linha por app e casa
  `sim`/`nao`; abaixo do limiar é sem resposta.

Prova `simulated`: decisor falso, banco de teste e o harness com o provedor simulado. Chamada real ao Jev: `not_run`.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from app.config import DecisaoFechadaCfg
from app.modules.skills.domain.intent import IntentResolution, ResolutionStatus
from app.planning.decisao_fechada import privacidade, transparencia
from app.planning.decisao_fechada.apps import (
    MAX_APPS, NAO, SIM, AppDeclarado, ConsumidorDeApps, decisoes_reais, id_da_pergunta, nome_enviavel, pedido_dos_apps,
    pergunta_do_app, perguntas_dos_apps,
)
from app.planning.decisao_fechada.contrato import PedidoDeDecisao, RespostaDeDecisao
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.intencao import (
    PERGUNTA_CATALOGO, CadeiaObservada, ConsumidorDeIntencao, EntradaDeCatalogo,
)
from app.planning.decisao_fechada.porta import Porta, hash_do_estado
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra
from app.taskqueue.sombra_intencao import SombraDaIntencao, apps_de

from .conftest import Harness
from .fake_skills import banco

CFG_APPS = DecisaoFechadaCfg(enabled=True, consumidores={"apps": "shadow"})  # type: ignore[arg-type]
CFG_OS_DOIS = DecisaoFechadaCfg(enabled=True, consumidores={"apps": "shadow", "intencao": "shadow"})  # type: ignore[arg-type]
OUTLOOK = AppDeclarado("outlook", ("Microsoft Outlook", "Outlook"))
INSTAGRAM = AppDeclarado("instagram", ("Instagram",))
CATALOGO = [EntradaDeCatalogo("ig.curtir", "Curtir", "Curte o post")]


@pytest.fixture
def repo(tmp_path: Path) -> Any:
    db = banco(tmp_path, "r5.sqlite3")
    yield RepositorioDeSombra(db)
    db.close()


@pytest.fixture
def r5_liberada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "R5_LIBERADA", True)
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)


def _noul(p: float) -> RespostaDeDecisao:
    """Como o decisor real devolve o `noul` (`decisores._resposta_noul`): sempre `sim`, com a probabilidade como confiança."""
    return RespostaDeDecisao(escolha=SIM, probabilidades={SIM: p}, confianca=p)


# ================================================================== privacidade
def test_lista_exata_da_origem_apps_e_c3_so_em_shadow() -> None:
    assert privacidade.CAMPOS_POR_ORIGEM["apps"] == frozenset({"comando"})
    assert "apps" in privacidade.C3_ORIGENS and privacidade.C3_MODOS == frozenset({"shadow"})
    assert privacidade.R5_LIBERADA is False                       # travada de fábrica até o GO do 31.10
    pedido = pedido_dos_apps(run_id="r1", perguntas=perguntas_dos_apps([OUTLOOK]), comando="leia o meu e-mail")
    assert dict(pedido.estado) == {"comando": "leia o meu e-mail"} and pedido.origem == "apps" and pedido.classe == "C3"
    assert privacidade.validar(pedido).permitido
    assert privacidade.validar(replace(pedido, modo="on")).motivo == "c3_so_intencao_em_shadow"
    com_app = replace(pedido, estado={**pedido.estado, "app": "outlook"})
    assert privacidade.validar(com_app).motivo == "campo_fora_da_lista"


@pytest.mark.parametrize("comando, original", [
    ("leia o meu e-mail e responda para [destino]", "leia o meu e-mail e responda para @ana"),
    ("mande 3 fotos para ana@exemplo.com", None),
    ("curta o post", "curta o post"),
    ("entre com a senha girassol", None),                       # C7: os dois recusam o pedido inteiro
])
def test_estado_da_r5_e_subconjunto_estrito_do_da_intencao_com_o_mesmo_comando(
        comando: str, original: str | None, repo: RepositorioDeSombra) -> None:
    falsa = Porta(DecisorFalso(), cfg=CFG_OS_DOIS)
    intencao = ConsumidorDeIntencao(falsa, repo).pedido(
        run_id="r1", comando=comando, app="instagram", catalogo=CATALOGO, cadeia=CadeiaObservada(), original=original)
    r5 = pedido_dos_apps(run_id="r1", perguntas=perguntas_dos_apps([OUTLOOK]), comando=comando, original=original)
    assert intencao is not None
    assert r5.estado.get("comando") == intencao.estado.get("comando")
    assert set(r5.estado) <= set(intencao.estado) and "app" not in r5.estado
    assert (r5.marcadores, r5.motivo_privacidade) == (intencao.marcadores, intencao.motivo_privacidade)
    if intencao.estado:                                         # sem C7: a intenção leva o app, a R5 não
        assert set(intencao.estado) == {"comando", "app"}
        assert hash_do_estado({"comando": intencao.estado["comando"]}) == hash_do_estado(r5.estado)


# ================================================================== perguntas
def test_uma_pergunta_noul_por_app_com_id_opaco_e_o_nome_so_no_texto() -> None:
    [instagram, outlook] = perguntas_dos_apps([OUTLOOK, INSTAGRAM, OUTLOOK])     # ordem de id, sem repetir
    assert (instagram.id, outlook.id) == (id_da_pergunta("instagram"), id_da_pergunta("outlook"))
    assert outlook.id.startswith("app:") and len(outlook.id) == 16 and "outlook" not in outlook.id
    assert outlook.tipo == "noul" and outlook.limiar == 0.85
    assert "Microsoft Outlook / Outlook" in outlook.instrucoes and set(outlook.opcoes) == {"true", "false"}
    assert "Microsoft Outlook / Outlook" in outlook.opcoes["true"] and "without" in outlook.opcoes["false"]
    pt = pergunta_do_app(OUTLOOK, pt=True)
    assert pt is not None and pt.id == outlook.id and pt.instrucoes.startswith("O estado é um comando")
    assert "exige o app Microsoft Outlook / Outlook" in pt.opcoes["true"]


def test_nome_do_app_e_mascarado_e_nome_c7_ou_vazio_fica_de_fora() -> None:
    assert nome_enviavel(AppDeclarado("x", ("Fotos do @fulano",))) == "Fotos do [usuario]"
    assert nome_enviavel(AppDeclarado("cofre", ("Gerenciador de senhas",))) is None
    assert nome_enviavel(AppDeclarado("vazio", ("  ",))) is None
    assert [p.id for p in perguntas_dos_apps([AppDeclarado("cofre", ("Gerenciador de senhas",)), INSTAGRAM])] == [
        id_da_pergunta("instagram")]


def test_acima_do_teto_de_apps_a_r5_nao_vai() -> None:
    muitos = [AppDeclarado(f"app{i}", (f"App {chr(65 + i)}",)) for i in range(MAX_APPS + 1)]
    assert perguntas_dos_apps(muitos) == () and len(perguntas_dos_apps(muitos[:MAX_APPS])) == MAX_APPS


def test_decisao_real_e_sim_para_o_citado_e_nao_para_os_demais() -> None:
    assert decisoes_reais([OUTLOOK, INSTAGRAM], ["outlook"]) == {
        id_da_pergunta("outlook"): SIM, id_da_pergunta("instagram"): NAO}


# ================================================================== travado
def test_travada_no_codigo_nao_monta_nada_mesmo_com_o_yaml_ligado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    decisor = DecisorFalso()
    db = banco(tmp_path, "r5.sqlite3")
    consumidor = ConsumidorDeApps(Porta(decisor, cfg=CFG_APPS), RepositorioDeSombra(db))
    assert not consumidor.ativo()
    consumidor.observar(run_id="r1", comando="leia o meu e-mail", apps=[OUTLOOK], citados=[])
    assert decisor.chamadas == []
    db.close()


@pytest.mark.parametrize("cfg, aprovado, ativo", [
    (CFG_APPS, True, True),
    (None, True, False),                                                                    # porta desligada
    (DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"}), True, False),     # type: ignore[arg-type]
    (DecisaoFechadaCfg(enabled=True, consumidores={"apps": "shadow"},                        # type: ignore[arg-type]
                       classes_permitidas=["C0", "C1", "C2"]), True, False),
    (CFG_APPS, False, False),                                                               # envio fechado
])
def test_destravada_vale_o_mesmo_que_a_intencao(monkeypatch: pytest.MonkeyPatch, cfg: DecisaoFechadaCfg | None,
                                                 aprovado: bool, ativo: bool, repo: RepositorioDeSombra) -> None:
    monkeypatch.setattr(privacidade, "R5_LIBERADA", True)
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", aprovado)
    porta = Porta(DecisorFalso(), cfg=cfg)
    assert ConsumidorDeApps(porta, repo).ativo() is ativo


def test_transparencia_nao_anuncia_a_r5_travada(monkeypatch: pytest.MonkeyPatch) -> None:
    assert transparencia.consumidores_ativos(CFG_APPS) == {}
    assert transparencia.status(CFG_APPS, chave_configurada=True) is None
    monkeypatch.setattr(privacidade, "R5_LIBERADA", True)
    assert transparencia.consumidores_ativos(CFG_APPS) == {"apps": "shadow"}
    saidas = transparencia.o_que_sai(CFG_APPS)
    assert "nomes e descrições do catálogo do dono" in saidas and any("comando do dono" in s for s in saidas)
    assert "apps em sombra" in (transparencia.aviso(CFG_APPS, chave_configurada=True) or "")


# ================================================================== sombra gravada
def test_sombra_grava_uma_linha_por_app_e_casa_sim_e_nao(tmp_path: Path, r5_liberada: None) -> None:
    decisor = DecisorFalso({id_da_pergunta("outlook"): _noul(0.93), id_da_pergunta("instagram"): _noul(0.4)})
    db = banco(tmp_path, "r5.sqlite3")
    sombra = RepositorioDeSombra(db)
    porta = Porta(decisor, cfg=CFG_APPS, observador=observador_de_sombra(sombra))
    ConsumidorDeApps(porta, sombra).observar(run_id="r1", comando="leia o meu e-mail", apps=[OUTLOOK, INSTAGRAM],
                                             citados=["instagram"])
    porta.aguardar_sombras()
    [pedido] = decisor.chamadas
    assert set(pedido.estado) == {"comando"} and pedido.origem == "apps"
    linhas = {r["pergunta_id"]: dict(r) for r in db.query("SELECT * FROM decisao_fechada_sombra")}
    outlook, instagram = linhas[id_da_pergunta("outlook")], linhas[id_da_pergunta("instagram")]
    # o Jev pegou a paráfrase ("meu e-mail" → Outlook) que a regex perdeu: sim acima do limiar, real `nao`
    assert (outlook["escolha"], outlook["fallback_reason"], outlook["decisao_real"]) == (SIM, None, NAO)
    # abaixo do limiar é SEM resposta, nunca `nao` (B7)
    assert (instagram["escolha"], instagram["fallback_reason"], instagram["decisao_real"]) == (
        None, "abaixo_do_limiar", SIM)
    assert {r["origem"] for r in linhas.values()} == {"apps"} and {r["ref"] for r in linhas.values()} == {"r1"}
    db.close()


class _Lista:
    def __init__(self, linhas: Sequence[dict[str, object]]) -> None:
        self.linhas = linhas
        self.lidas = 0

    def __call__(self) -> Sequence[dict[str, object]]:
        self.lidas += 1
        return self.linhas


CADASTRO = [{"id": "instagram", "name": "Instagram", "package": "com.instagram.android"},
            {"id": "outlook", "name": "Microsoft Outlook", "package": "com.microsoft.office.outlook"}]


def test_ligada_e_travada_a_sombra_nao_le_o_cadastro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    db = banco(tmp_path, "r5.sqlite3")
    porta = Porta(DecisorFalso(), cfg=CFG_APPS)
    sombra = SombraDaIntencao(ConsumidorDeIntencao(porta, RepositorioDeSombra(db)),
                              resolver=lambda c, p: IntentResolution(ResolutionStatus.NO_MATCH), catalogo=list)
    lista = _Lista(CADASTRO)
    sombra.ligar_apps(porta, RepositorioDeSombra(db), lista)
    assert not sombra.ativo()
    sombra._observar("r1", lambda: ("leia o meu e-mail", [None], None, "leia o meu e-mail", ()))  # noqa: SLF001
    assert lista.lidas == 0
    db.close()


def test_controle_e_a_regex_sobre_o_comando_original_e_a_intencao_desligada_nao_resolve(
        tmp_path: Path, r5_liberada: None) -> None:
    db = banco(tmp_path, "r5.sqlite3")
    decisor = DecisorFalso()
    repo = RepositorioDeSombra(db)
    porta = Porta(decisor, cfg=CFG_APPS, observador=observador_de_sombra(repo))
    resolvidos: list[str] = []

    def resolver(c: str, p: Any) -> IntentResolution:
        resolvidos.append(c)
        return IntentResolution(ResolutionStatus.NO_MATCH)

    sombra = SombraDaIntencao(ConsumidorDeIntencao(porta, repo), resolver=resolver, catalogo=list)
    sombra.ligar_apps(porta, repo, _Lista(CADASTRO))
    assert sombra.ativo()
    # o original cita o Outlook e o comando entregue ao Jev não: o controle lê o ORIGINAL, como o roteamento real
    sombra._observar("r1", lambda: ("curta o post no Instagram", [None], "instagram",          # noqa: SLF001
                                    "curta o post no Instagram e mande pelo Outlook", ()))
    porta.aguardar_sombras()
    assert resolvidos == []                                       # a intenção está desligada: nem a RESOLVE
    [pedido] = decisor.chamadas
    assert dict(pedido.estado) == {"comando": "curta o post no Instagram"}
    reais = {r["pergunta_id"]: r["decisao_real"] for r in db.query("SELECT * FROM decisao_fechada_sombra")}
    assert reais == {id_da_pergunta("instagram"): SIM, id_da_pergunta("outlook"): SIM}
    db.close()


def test_apps_de_le_so_id_nome_e_pacote() -> None:
    [app] = apps_de([{"id": "outlook", "name": "Microsoft Outlook", "package": "com.microsoft.office.outlook",
                      "known_selectors": "{nao e json"}, {"id": "", "name": "sem id"}])
    assert (app.id, app.name, app.package, app.known_selectors) == ("outlook", "Microsoft Outlook",
                                                                    "com.microsoft.office.outlook", None)


# ================================================================== composição
async def test_composicao_liga_a_r5_travada_e_o_plano_de_fabrica_nao_chama_ninguem(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    sombra = st.runs.sombra_intencao
    assert sombra is not None and sombra._apps is not None and not sombra.ativo()   # noqa: SLF001


async def test_destravada_no_harness_a_sombra_dos_apps_grava_depois_do_plano(
        harness: Harness, r5_liberada: None) -> None:
    st = harness.state
    assert st is not None
    respostas = {id_da_pergunta("qa-messenger"): _noul(0.9), id_da_pergunta("instagram"): _noul(0.2)}
    decisor = DecisorFalso(respostas)
    st.decisao_fechada.decisor = decisor
    st.decisao_fechada.cfg = CFG_APPS
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    assert st.runs.sombra_intencao is not None
    await st.runs.sombra_intencao.aguardar()
    st.decisao_fechada.aguardar_sombras()
    [pedido] = decisor.chamadas
    assert isinstance(pedido, PedidoDeDecisao) and set(pedido.estado) == {"comando"} and pedido.ref == run.id
    linhas = {r["pergunta_id"]: dict(r) for r in st.db.query("SELECT * FROM decisao_fechada_sombra")}
    assert set(linhas) == set(respostas) and {r["decisao_real"] for r in linhas.values()} == {NAO}
    assert linhas[id_da_pergunta("qa-messenger")]["escolha"] == SIM
    assert linhas[id_da_pergunta("instagram")]["fallback_reason"] == "abaixo_do_limiar"
    assert PERGUNTA_CATALOGO not in linhas                                     # a intenção segue desligada
