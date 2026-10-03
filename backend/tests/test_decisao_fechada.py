"""Porta `DecisaoFechada` (item 31.4, ADR-069): contrato, privacidade que falha fechada, modos e recurso ao caminho atual.

Prova `simulated`: só `DecisorNulo` e `DecisorFalso`. Nada aqui toca rede, chave ou a TypeSafe; o teste de cliente único
prova por varredura que SÓ o adaptador de retrieval conhece o host. O interruptor do envio está aberto desde o 31.17
(`JEV_RUNTIME_SEND_APPROVED = True`); o que nasce fechado é o YAML (`enabled: false`, decisor nulo). Cada teste fixa o
interruptor de que precisa com `monkeypatch`, só nele, e o fechado continua provado (é o que desliga tudo).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from app.config import AiCfg, AppConfigFile, DecisaoFechadaCfg
from app.planning import decisao_fechada as df
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import (
    ID_NENHUMA, FalhaDeDecisao, PedidoDeDecisao, Pergunta, RespostaDeDecisao, pergunta_choice,
)
from app.planning.decisao_fechada.porta import Porta, RegistroDeDecisao, modo_efetivo

SEGREDO = "sk-ant-api03-" + "A" * 40  # forma de chave, fabricada: não é credencial de ninguém

OPCOES = {"a": "primeira", "b": "segunda"}


def _pedido(**kw: object) -> PedidoDeDecisao:
    base: dict[str, object] = dict(
        origem="curador", classe="C1", estado={"licao": "texto fechado"}, modo="on",
        perguntas=(pergunta_choice("q1", "Pick one.", OPCOES, limiar=0.8),))
    base.update(kw)
    return PedidoDeDecisao(**base)  # type: ignore[arg-type]


def _cfg(**consumidores: str) -> DecisaoFechadaCfg:
    return DecisaoFechadaCfg(enabled=True, consumidores=consumidores)  # type: ignore[arg-type]


@pytest.fixture
def aberta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Abre o envio SÓ neste teste e dá campos às origens: o padrão de produção é tudo fechado."""
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    for o in privacidade.ORIGENS:
        monkeypatch.setitem(privacidade.CAMPOS_POR_ORIGEM, o, frozenset({"licao", "comando", "nota"}))


def _resp(escolha: str | None = "a", conf: float | None = 0.95, *, prob: float | None = None,
          **kw: object) -> RespostaDeDecisao:
    """`prob` é a probabilidade devolvida da escolha, o que o limiar mede no `choice` (31.19); sem ela, igual à confiança."""
    p = prob if prob is not None else (conf if conf is not None else 0.95)
    outra = "b" if escolha != "b" else "a"
    probs = {escolha: p, outra: round(1 - p, 4)} if escolha is not None else {"a": 0.95, "b": 0.05}
    return RespostaDeDecisao(escolha=escolha, probabilidades=probs, confianca=conf,
                             **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------- contrato
def test_envio_aberto_no_codigo_desde_o_31_17_e_o_yaml_de_fabrica_nao_chama_ninguem() -> None:
    assert privacidade.JEV_RUNTIME_SEND_APPROVED is True   # 31.17, a sombra do 31.10 (ADR-069 itens 9 e 15)
    assert df.JEV_RUNTIME_SEND_APPROVED is True
    assert privacidade.JEV_ALLOWED_CLASSES == frozenset({"C0", "C1", "C2", "C3"})  # ADR-069 item 4
    fabrica = DecisaoFechadaCfg()
    assert (fabrica.enabled, fabrica.consumidores, fabrica.decisor) == (False, {}, "nulo")
    falso = df.DecisorFalso({"q1": _resp()})
    res = Porta(falso, cfg=fabrica).consultar(_pedido())
    assert falso.chamadas == [] and res.fallback_reason == "desligado"   # aberto no código, desligado no YAML: nada sai


def test_choice_sempre_leva_a_nenhuma_e_respeita_o_teto_de_255() -> None:
    p = pergunta_choice("q", "Pick.", OPCOES)
    assert ID_NENHUMA in p.opcoes and set(p.opcoes) == {"a", "b", ID_NENHUMA}
    with pytest.raises(ValueError):
        Pergunta("q", "choice", "Pick.", OPCOES)  # choice cru, sem nenhuma
    with pytest.raises(ValueError):
        pergunta_choice("q", "Pick.", {ID_NENHUMA: "colisao"})
    pergunta_choice("q", "Pick.", {f"o{i}": "x" for i in range(df.MAX_OPCOES - 1)})  # 254 + nenhuma = 255
    with pytest.raises(ValueError):
        pergunta_choice("q", "Pick.", {f"o{i}": "x" for i in range(df.MAX_OPCOES)})


def test_fallback_nunca_conta_como_acerto() -> None:
    assert _resp().valida
    assert not _resp(fallback_reason="rede").valida
    assert not _resp(escolha=ID_NENHUMA).valida  # abster-se não é acerto
    assert not _resp(escolha=None).valida


# ---------------------------------------------------------------- privacidade (simulated)
def test_envio_fechado_recusa_tudo_e_o_decisor_nunca_e_chamado(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", False)   # o interruptor de código vence o YAML
    falso = df.DecisorFalso({"q1": _resp()})
    res = Porta(falso, cfg=_cfg(curador="on")).consultar(_pedido())
    assert falso.chamadas == []
    assert res.fallback_reason == "privacidade" and res.respostas["q1"].escolha is None
    assert privacidade.validar(_pedido()).motivo == "envio_nao_aprovado"


@pytest.mark.parametrize("marcador", ["tela_sensivel", "tela_protegida", "aparelho_loja", "segredo", "credencial",
                                      "desafio"])
def test_c7_recusa_o_pedido_inteiro_em_on_e_em_shadow(aberta: None, marcador: str) -> None:
    for modo in ("on", "shadow"):
        falso = df.DecisorFalso({"q1": _resp()})
        p = Porta(falso, cfg=_cfg(curador="on"))
        res = p.consultar(_pedido(modo=modo, marcadores=frozenset({marcador})))
        p.aguardar_sombras()
        assert res.fallback_reason in ("privacidade", "desligado") and falso.chamadas == []
    assert privacidade.validar(_pedido(marcadores=frozenset({marcador}))).motivo == "c7"


def test_marcador_desconhecido_tambem_recusa(aberta: None) -> None:
    v = privacidade.validar(_pedido(marcadores=frozenset({"inventado"})))
    assert not v.permitido and v.motivo == "marcador_desconhecido"


def test_social_e_persona_ficam_fora_dj5(aberta: None) -> None:
    assert privacidade.validar(_pedido(marcadores=frozenset({"social_persona"}))).motivo == "social_persona"
    # uma origem nova ligada a social/persona não está no vocabulário fechado: recusada
    assert privacidade.validar(_pedido(origem="persona")).motivo == "origem_desconhecida"
    assert privacidade.validar(_pedido(origem="social")).motivo == "origem_desconhecida"


def test_classe_nao_liberada_recusa(aberta: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_ALLOWED_CLASSES", frozenset({"C0"}))
    assert privacidade.validar(_pedido(classe="C0")).permitido
    assert privacidade.validar(_pedido(classe="C1")).motivo == "classe_nao_liberada"
    monkeypatch.setattr(privacidade, "JEV_ALLOWED_CLASSES", frozenset())  # estado original da porta, antes do ADR-069
    assert privacidade.validar(_pedido(classe="C0")).motivo == "classe_nao_liberada"
    # C4 em diante nem existe: recusa, mesmo que alguém a ponha no conjunto liberado
    monkeypatch.setattr(privacidade, "JEV_ALLOWED_CLASSES", frozenset({"C4", "C7"}))
    assert privacidade.validar(_pedido(classe="C4")).motivo == "classe_desconhecida"  # type: ignore[arg-type]


def test_c3_so_na_intencao_e_nos_apps_e_so_em_shadow(aberta: None) -> None:
    # 31.13: os apps do comando (R5) entram na C3, em sombra (ADR-069 item 21); o runtime segue travado por `R5_LIBERADA`
    for c3 in ("intencao", "apps"):
        assert privacidade.validar(_pedido(origem=c3, classe="C3", modo="shadow")).permitido
        assert privacidade.validar(_pedido(origem=c3, classe="C3", modo="on")).motivo == "c3_so_intencao_em_shadow"
    for outra in ("curador", "desempate"):
        assert privacidade.validar(_pedido(origem=outra, classe="C3", modo="shadow")).motivo == "c3_so_intencao_em_shadow"
    # C0, C1 e C2 valem para todos os consumidores
    for c in ("C0", "C1", "C2"):
        assert all(privacidade.validar(_pedido(origem=o, classe=c)).permitido for o in privacidade.ORIGENS)


def test_o_yaml_so_restringe_nunca_libera(aberta: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_ALLOWED_CLASSES", frozenset({"C0"}))
    assert privacidade.validar(_pedido(classe="C1"), classes_yaml=frozenset({"C0", "C1"})).motivo == "classe_nao_liberada"
    monkeypatch.setattr(privacidade, "JEV_ALLOWED_CLASSES", frozenset({"C0", "C1"}))
    assert privacidade.validar(_pedido(classe="C1"), classes_yaml=frozenset({"C0"})).motivo == "classe_nao_liberada"
    assert privacidade.validar(_pedido(classe="C1"), classes_yaml=frozenset({"C0", "C1"})).permitido
    # pela porta: o YAML estreita
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "on"}, classes_permitidas=["C0"])
    falso = df.DecisorFalso({"q1": _resp()})
    assert Porta(falso, cfg=cfg).consultar(_pedido(classe="C1")).fallback_reason == "privacidade" and not falso.chamadas


def test_campo_fora_da_lista_da_origem_recusa(aberta: None) -> None:
    assert privacidade.validar(_pedido(estado={"licao": "x", "legenda_de_terceiro": "y"})).motivo == "campo_fora_da_lista"
    assert privacidade.validar(_pedido(estado={})).motivo == "pedido_vazio"


def test_toda_string_do_corpo_passa_por_redact(aberta: None) -> None:
    falso = df.DecisorFalso({"q1": _resp()})
    perg = pergunta_choice("q1", f"Pick. token {SEGREDO}", {"a": f"primeira {SEGREDO}", "b": "segunda"}, limiar=0.8)
    Porta(falso, cfg=_cfg(curador="on")).consultar(_pedido(estado={"licao": f"usa {SEGREDO} aqui"}, perguntas=(perg,)))
    enviado = falso.chamadas[0]
    corpo = repr(enviado.estado) + repr([(p.instrucoes, p.opcoes) for p in enviado.perguntas])
    assert SEGREDO not in corpo and "**REDACTED**" in corpo


# ---------------------------------------------------------------- modos
def test_off_e_o_padrao_e_nao_faz_nada(aberta: None) -> None:
    falso = df.DecisorFalso({"q1": _resp()})
    assert Porta(falso).consultar(_pedido()).fallback_reason == "desligado"  # sem config
    assert Porta(falso, cfg=DecisaoFechadaCfg(consumidores={"curador": "on"})).consultar(_pedido()).fallback_reason == \
        "desligado"  # enabled=false vence um consumidor em `on`
    assert Porta(falso, cfg=_cfg()).consultar(_pedido()).fallback_reason == "desligado"  # consumidor ausente = off
    assert Porta(falso, cfg=_cfg(curador="on")).consultar(_pedido(modo="off")).fallback_reason == "desligado"
    assert falso.chamadas == []
    assert DecisaoFechadaCfg().enabled is False and DecisaoFechadaCfg().consumidores == {}
    assert AppConfigFile().ai.decisao_fechada == DecisaoFechadaCfg() and isinstance(AiCfg().decisao_fechada, DecisaoFechadaCfg)


def test_modo_efetivo_e_o_menor_entre_pedido_e_consumidor() -> None:
    assert modo_efetivo("on", _cfg(curador="shadow"), "curador") == "shadow"
    assert modo_efetivo("shadow", _cfg(curador="on"), "curador") == "shadow"
    assert modo_efetivo("on", _cfg(curador="on"), "curador") == "on"
    assert modo_efetivo("on", _cfg(curador="on"), "apps") == "off"
    assert modo_efetivo("on", None, "curador") == "off"


def test_decisor_nulo_nunca_decide(aberta: None) -> None:
    res = df.construir_porta(_cfg(curador="on")).consultar(_pedido())
    assert res.respostas["q1"].fallback_reason == "desligado" and res.respostas["q1"].escolha is None


def test_on_devolve_a_escolha_e_o_fan_out_e_uma_chamada(aberta: None) -> None:
    perguntas = (pergunta_choice("q1", "Pick.", OPCOES, 0.8), pergunta_choice("q2", "Pick.", OPCOES, 0.8),
                 Pergunta("q3", "noul", "Is it new?", {}, 0.7))
    falso = df.DecisorFalso({"q1": _resp("a"), "q2": _resp("b", 0.9),
                             "q3": RespostaDeDecisao(escolha="sim", probabilidades={"sim": 0.9}, confianca=0.9)},
                            custo_tokens=1200, custo_usd=0.00005)
    res = Porta(falso, cfg=_cfg(curador="on")).consultar(_pedido(perguntas=perguntas))
    assert len(falso.chamadas) == 1 and len(falso.chamadas[0].perguntas) == 3  # um estado, N perguntas, uma chamada
    assert [res.respostas[i].escolha for i in ("q1", "q2", "q3")] == ["a", "b", "sim"]
    assert res.tokens == 1200 and res.usd == pytest.approx(0.00005)
    assert all(r.tokens == 0 and r.usd == 0.0 for r in res.respostas.values())  # custo só no resultado: ninguém soma 2x


@pytest.mark.parametrize("falha", ["401", "422", "429", "529", "rede", "parse"])
def test_falha_do_decisor_vira_fallback_com_motivo_fechado(aberta: None, falha: str) -> None:
    res = Porta(df.DecisorFalso(falha=FalhaDeDecisao(falha)), cfg=_cfg(curador="on")).consultar(_pedido())  # type: ignore[arg-type]
    assert res.respostas["q1"].fallback_reason == falha and res.respostas["q1"].escolha is None


def test_excecao_inesperada_do_decisor_vira_rede(aberta: None) -> None:
    class Quebrado:
        def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> df.ResultadoDeDecisao:
            raise RuntimeError("corpo secreto que nao pode vazar")

    res = Porta(Quebrado(), cfg=_cfg(curador="on")).consultar(_pedido())
    assert res.respostas["q1"].fallback_reason == "rede" and "secreto" not in repr(res)


def test_escolha_fora_das_opcoes_e_unknown_choice(aberta: None) -> None:
    falso = df.DecisorFalso({"q1": _resp("inventada")})
    r = Porta(falso, cfg=_cfg(curador="on")).consultar(_pedido()).respostas["q1"]
    assert r.fallback_reason == "unknown_choice" and r.escolha is None and not r.valida


def test_abaixo_do_limiar_e_fallback_e_nunca_acerto(aberta: None) -> None:
    for conf in (0.5, None):
        falso = df.DecisorFalso({"q1": _resp("a", conf)})
        r = Porta(falso, cfg=_cfg(curador="on")).consultar(_pedido()).respostas["q1"]
        assert r.fallback_reason == "abaixo_do_limiar" and r.escolha is None and not r.valida


def test_nenhuma_e_resposta_valida_sem_fallback_mas_nao_e_acerto(aberta: None) -> None:
    falso = df.DecisorFalso({"q1": _resp(ID_NENHUMA, 0.97)})
    r = Porta(falso, cfg=_cfg(curador="on")).consultar(_pedido()).respostas["q1"]
    assert r.fallback_reason is None and r.escolha == ID_NENHUMA and not r.valida


def test_limiar_do_choice_mede_a_probabilidade_devolvida_da_escolha(aberta: None) -> None:
    """31.19: o contrato manda medir a probabilidade devolvida; a confiança fica na resposta (e na sombra), sem decidir."""
    casos = (
        (_resp("a", 0.5, prob=0.9), None),                     # confiança baixa, probabilidade alta: vale
        (_resp("a", 0.95, prob=0.6), "abaixo_do_limiar"),      # o caso real de 03/10 com a confiança invertida
        (RespostaDeDecisao(escolha="a", probabilidades={"a": 0.86, "b": 0.9}, confianca=0.95), "abaixo_do_limiar"),
        (RespostaDeDecisao(escolha="a", probabilidades={"b": 0.1}, confianca=0.95), "abaixo_do_limiar"),
        (RespostaDeDecisao(escolha="a", probabilidades={}, confianca=0.95), "abaixo_do_limiar"),
        (_resp(ID_NENHUMA, 0.5, prob=0.9), None),              # abster-se com probabilidade alta é resposta
    )
    for resposta, motivo in casos:
        r = Porta(df.DecisorFalso({"q1": resposta}), cfg=_cfg(curador="on")).consultar(_pedido()).respostas["q1"]
        assert r.fallback_reason == motivo, resposta
        assert r.confianca == resposta.confianca                     # a confiança segue na resposta, para a sombra
        assert (r.escolha is None) == (motivo is not None)
    # `noul` (sem produtor) segue na confiança
    noul = (Pergunta("q3", "noul", "Is it new?", {}, 0.7),)
    r = Porta(df.DecisorFalso({"q3": RespostaDeDecisao(escolha="sim", probabilidades={"sim": 0.2}, confianca=0.9)}),
              cfg=_cfg(curador="on")).consultar(_pedido(perguntas=noul)).respostas["q3"]
    assert r.fallback_reason is None and r.escolha == "sim"


def test_on_tem_timeout_de_1s_sem_retentativa(aberta: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.planning.decisao_fechada import porta as porta_mod
    monkeypatch.setattr(porta_mod, "TIMEOUT_ON_S", 0.05)  # o valor de produção é 1 s; aqui só para não esperar
    assert porta_mod.TIMEOUT_SHADOW_S == 5.0
    falso = df.DecisorFalso({"q1": _resp()}, atraso_s=0.4)
    t0 = time.perf_counter()
    res = Porta(falso, cfg=_cfg(curador="on")).consultar(_pedido())
    assert time.perf_counter() - t0 < 0.3
    assert res.respostas["q1"].fallback_reason == "rede" and len(falso.chamadas) == 1
    time.sleep(0.5)
    assert len(falso.chamadas) == 1  # sem retentativa


def test_constantes_de_timeout_sao_as_do_adr() -> None:
    from app.planning.decisao_fechada import porta as porta_mod
    assert porta_mod.TIMEOUT_ON_S == 1.0 and porta_mod.TIMEOUT_SHADOW_S == 5.0


def test_shadow_roda_fora_do_caminho_critico_e_nada_usa_a_resposta(aberta: None) -> None:
    vistos: list[RegistroDeDecisao] = []
    falso = df.DecisorFalso({"q1": _resp()}, atraso_s=0.2)
    porta = Porta(falso, cfg=_cfg(curador="shadow"), observador=vistos.append)
    t0 = time.perf_counter()
    res = porta.consultar(_pedido(modo="shadow", run_id="r1", step_id="s1", ref="x"))
    assert time.perf_counter() - t0 < 0.15  # não esperou o decisor
    assert res.fallback_reason == "desligado" and res.respostas["q1"].escolha is None  # nada usa a sombra
    porta.aguardar_sombras()
    assert len(falso.chamadas) == 1 and len(vistos) == 1
    reg = vistos[0]
    assert reg.modo == "shadow" and (reg.run_id, reg.step_id, reg.ref) == ("r1", "s1", "x")
    assert reg.resultado.respostas["q1"].escolha == "a"
    assert not hasattr(reg, "estado") and "texto fechado" not in repr(reg)  # o registro não leva o estado


def test_shadow_obedece_as_mesmas_classes_que_o_on(aberta: None) -> None:
    falso = df.DecisorFalso({"q1": _resp()})
    vistos: list[RegistroDeDecisao] = []
    porta = Porta(falso, cfg=_cfg(curador="shadow"), observador=vistos.append)
    porta.consultar(_pedido(modo="shadow", marcadores=frozenset({"credencial"})))
    porta.consultar(_pedido(modo="shadow", origem="curador", classe="C3"))  # C3 só na intenção
    porta.aguardar_sombras()
    assert falso.chamadas == []
    assert [r.resultado.fallback_reason for r in vistos] == ["privacidade", "privacidade"]


def test_shadow_com_falha_do_decisor_e_registrada_com_motivo(aberta: None) -> None:
    vistos: list[RegistroDeDecisao] = []
    porta = Porta(df.DecisorFalso(falha=FalhaDeDecisao("429")), cfg=_cfg(curador="shadow"), observador=vistos.append)
    porta.consultar(_pedido(modo="shadow"))
    porta.aguardar_sombras()
    assert vistos[0].resultado.respostas["q1"].fallback_reason == "429"


def test_observador_que_falha_nao_derruba_o_trabalho(aberta: None) -> None:
    def ruim(_: RegistroDeDecisao) -> None:
        raise RuntimeError("x")

    res = Porta(df.DecisorFalso({"q1": _resp()}), cfg=_cfg(curador="on"), observador=ruim).consultar(_pedido())
    assert res.respostas["q1"].escolha == "a"


def test_config_rejeita_origem_e_modo_fora_do_vocabulario() -> None:
    with pytest.raises(ValueError):
        DecisaoFechadaCfg(consumidores={"persona": "on"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DecisaoFechadaCfg(consumidores={"curador": "ligado"})  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DecisaoFechadaCfg(classes_permitidas=["C4"])  # type: ignore[list-item]


# ---------------------------------------------------------------- cliente único
APP = Path(__file__).resolve().parents[1] / "app"
ADAPTADOR = APP / "modules" / "context_retrieval" / "adapters" / "jev.py"


def _arquivos_com(trecho: str) -> set[Path]:
    return {p for p in APP.rglob("*.py") if trecho in p.read_text(encoding="utf-8", errors="ignore")}


def test_cliente_unico_so_o_adaptador_de_retrieval_conhece_o_host_da_typesafe() -> None:
    assert _arquivos_com("typesafe.ai") == {ADAPTADOR}
    assert _arquivos_com("api.typesafe") == {ADAPTADOR}
    # nem a URL base do adaptador é importada por quem não for o próprio adaptador
    assert _arquivos_com("DEFAULT_BASE_URL") <= {ADAPTADOR}


def test_a_porta_nao_importa_cliente_http() -> None:
    for p in (APP / "planning" / "decisao_fechada").glob("*.py"):
        texto = p.read_text(encoding="utf-8")
        assert "import httpx" not in texto and "import requests" not in texto and "TYPESAFE" not in texto


# ---------------------------------------------------------------- desligamento (I1): espera com prazo e sem foto
class _DecisorComPortao(df.DecisorFalso):
    """Segura a primeira chamada num portão: a sombra fica "em curso" até o teste liberar (sem `sleep` longo)."""

    def __init__(self, *a: object, **kw: object) -> None:
        super().__init__(*a, **kw)  # type: ignore[arg-type]
        import threading  # noqa: PLC0415
        self.iniciou = threading.Event()
        self.portao = threading.Event()

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float):  # type: ignore[no-untyped-def]
        self.iniciou.set()
        self.portao.wait(5.0)
        return super().decidir(pedido, timeout_s)


def test_aguardar_sombras_inclui_a_agendada_depois_da_primeira_olhada(aberta: None) -> None:
    """Uma thread da intenção ou do curador ainda chamando a porta agenda a sombra DEPOIS de o desligamento olhar a lista:
    a espera repete até esvaziar, em vez de tirar uma foto só."""
    import threading  # noqa: PLC0415
    vistos: list[RegistroDeDecisao] = []
    decisor = _DecisorComPortao({"q1": _resp()})
    porta = Porta(decisor, cfg=_cfg(curador="shadow"), observador=vistos.append)
    porta.consultar(_pedido(modo="shadow", ref="um"))

    def atrasada() -> None:
        decisor.iniciou.wait(5.0)
        porta.consultar(_pedido(modo="shadow", ref="dois"))     # agendada com a espera já em curso
        decisor.portao.set()

    t = threading.Thread(target=atrasada)
    t.start()
    porta.aguardar_sombras(5.0)
    t.join(5.0)
    assert sorted(r.ref or "" for r in vistos) == ["dois", "um"]


def test_aguardar_sombras_tem_um_prazo_total_e_nao_esquece_o_que_ficou(aberta: None) -> None:
    vistos: list[RegistroDeDecisao] = []
    decisor = _DecisorComPortao({"q1": _resp()})
    porta = Porta(decisor, cfg=_cfg(curador="shadow"), observador=vistos.append)
    porta.consultar(_pedido(modo="shadow"))
    decisor.iniciou.wait(5.0)
    t0 = time.monotonic()
    porta.aguardar_sombras(0.2)                                  # a sombra segue no portão: estoura o prazo
    assert 0.15 <= time.monotonic() - t0 < 2.0 and vistos == []
    decisor.portao.set()
    porta.aguardar_sombras(5.0)                                  # não foi esquecida: a segunda espera a alcança
    assert len(vistos) == 1


def test_porta_encerrada_nao_aceita_sombra_nova_mas_a_ja_agendada_termina(aberta: None) -> None:
    vistos: list[RegistroDeDecisao] = []
    decisor = _DecisorComPortao({"q1": _resp()})
    porta = Porta(decisor, cfg=_cfg(curador="shadow"), observador=vistos.append)
    porta.consultar(_pedido(modo="shadow", ref="antes"))
    decisor.iniciou.wait(5.0)
    porta.encerrar()
    res = porta.consultar(_pedido(modo="shadow", ref="depois"))
    assert res.fallback_reason == "desligado"
    porta.encerrar()                                             # idempotente
    decisor.portao.set()
    porta.aguardar_sombras(5.0)
    assert [r.ref for r in vistos] == ["antes"] and len(decisor.chamadas) == 1
