"""Sombra da intenção (item 31.9, ADR-069): remoção de entidades que falha fechada, o consumidor R2/R3 e o enxerto no `_plan`.

Prova `simulated`: `DecisorFalso`, banco de teste e a RESOLVE de verdade sobre habilidades de teste. Nada toca rede, chave ou a
TypeSafe, e o envio continua fechado no código (`JEV_RUNTIME_SEND_APPROVED = False`): os testes que precisam de uma porta
aberta a abrem com `monkeypatch`, e um deles prova que, fechada, o decisor não é chamado.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.config import DecisaoFechadaCfg
from app.modules.skills.domain.intent import IntentResolution, ResolutionStatus
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import (
    ID_NENHUMA, PedidoDeDecisao, RespostaDeDecisao, ResultadoDeDecisao, pergunta_choice,
)
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.entidades import remover_entidades
from app.planning.decisao_fechada.intencao import (
    PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE, CadeiaObservada, ConsumidorDeIntencao, EntradaDeCatalogo, id_opaco,
)
from app.planning.decisao_fechada.porta import Porta
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra
from app.taskqueue.sombra_intencao import SombraDaIntencao, cadeia_de, catalogo_de

from .conftest import Harness
from .fake_skills import banco
from .test_habilidades_na_execucao import ABRIR, Mundo
from .test_intencao_resolucao import doc_abrir, publicar_doc

CFG_SHADOW = DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"})


# ================================================================== 1. remover_entidades
@pytest.mark.parametrize("texto, esperado", [
    ("abra o instagram e curta o primeiro post", "abra o instagram e curta o primeiro post"),
    ("curta 25 posts do feed", "curta 25 posts do feed"),                         # 2 dígitos não são número de contato
    ("curta o post de Maria Silva", "curta o post de [nome]"),
    ("Maria, abra o aplicativo", "[nome], abra o aplicativo"),
    ("Abra o app. Depois mande para Joana", "Abra o app. Depois mande para [nome]"),
    ("mande para @fulano.silva agora", "mande para [usuario] agora"),
    ("escreva para fulano@exemplo.com hoje", "escreva para [email] hoje"),
    ("ligue para (11) 99999-9999 agora", "ligue para [telefone] agora"),
    ("ligue para +55 11 98888-7777", "ligue para [telefone]"),
    ("curta 2500 posts", "curta [numero] posts"),
    ("veja https://exemplo.com/a?b=1 e www.exemplo.org", "veja [link] e [link]"),
    ("abra instagram.com/fulano", "abra [link]"),
    ('comente "adorei o post" no feed', "comente [texto] no feed"),
    ("segunda, curta o post", "segunda, curta o post"),
])
def test_remover_entidades_troca_por_marcador_fixo(texto: str, esperado: str) -> None:
    assert remover_entidades(texto) == esperado


@pytest.mark.parametrize("texto", [
    "fale com fulano @ exemplo",                      # arroba solta: nenhum detector de handle a pega, a conferência sim
    "meu email e fulano arroba exemplo ponto com",    # e-mail por extenso
    "siga fulano dot com",
    "abra o app com o codigo 1 2 3",                  # dígitos separados que somam 3
    "ligue 1-2-3",
    "acesse exemplo.com.br/fulano",                   # sem esquema
])
def test_remover_entidades_falha_fechada_quando_sobra_indicio(texto: str) -> None:
    resultado = remover_entidades(texto)
    # Ou virou marcadores (e entao nada sobrou) ou recusou: nunca devolve o texto com o indicio.
    if resultado is not None:
        resto = resultado
        for m in ("[link]", "[numero]", "[usuario]", "[email]", "[telefone]", "[nome]", "[texto]"):
            resto = resto.replace(m, " ")
        assert "@" not in resto and not any(c.isdigit() for c in resto) and ".com" not in resto


@pytest.mark.parametrize("texto", [
    "fale com fulano @ exemplo",
    "meu email e fulano arroba exemplo ponto com",
    "siga fulano dot com",
])
def test_remover_entidades_devolve_none_nos_casos_que_devem_recusar(texto: str) -> None:
    assert remover_entidades(texto) is None


def test_remover_entidades_nao_aceita_entrada_que_nao_e_texto() -> None:
    assert remover_entidades(None) is None                         # type: ignore[arg-type]
    assert remover_entidades(123) is None                          # type: ignore[arg-type]


def test_marcadores_nao_disparam_a_conferencia_e_o_resultado_e_estavel() -> None:
    primeiro = remover_entidades("curta o post de Ana Souza e mande para @ana.souza fulano@exemplo.com 5551234")
    assert primeiro is not None
    assert "Ana" not in primeiro and "@" not in primeiro and "5551234" not in primeiro
    assert remover_entidades(primeiro) == primeiro                 # idempotente: o que já está limpo continua limpo


def test_nome_proprio_em_minusculas_e_primeiro_termo_sem_virgula_sao_o_limite_conhecido() -> None:
    """O que a heurística NÃO pega fica documentado em `entidades.py`; por isso a classe só vale em sombra."""
    assert remover_entidades("joana curtiu isso") == "joana curtiu isso"


# ================================================================== 2. consumidor (banco de teste e a RESOLVE de verdade)
class Mundo2:
    """Duas habilidades que empatam em "abra a conversa com 3 no instagram" e a sombra sobre um banco de teste."""

    def __init__(self, tmp: Path, decisor: DecisorFalso, cfg: DecisaoFechadaCfg | None = CFG_SHADOW) -> None:
        self.db = banco(tmp, "intencao.sqlite3")
        self.m = Mundo(self.db)
        self.m.publicar(ABRIR)
        publicar_doc(self.m, doc_abrir("ig.abrir_numero", "abra a conversa com {n} no instagram", "n", tipo="integer"))
        self.sombra = RepositorioDeSombra(self.db)
        self.decisor = decisor
        self.porta = Porta(decisor, cfg=cfg, observador=observador_de_sombra(self.sombra))
        self.consumidor = ConsumidorDeIntencao(self.porta, self.sombra, espera_s=5.0)

    def catalogo(self) -> list[EntradaDeCatalogo]:
        return catalogo_de(lambda estado: self.m.registro.list(state=estado), self.m.registro.definition,
                           skills_ligadas=True, fluxos_ligados=False)

    def linhas(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM decisao_fechada_sombra ORDER BY id")]

    def fechar(self) -> None:
        self.porta.aguardar_sombras()
        self.db.close()


@pytest.fixture
def porta_aberta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)


def test_catalogo_traz_as_publicadas_com_nome_do_dono_e_cadeia_registra_o_empate(tmp_path: Path) -> None:
    w = Mundo2(tmp_path, DecisorFalso())
    ids = {e.skill_id for e in w.catalogo()}
    assert ids == {"ig.abrir_conversa", "ig.abrir_numero"}
    assert all(e.nome for e in w.catalogo())
    cadeia = cadeia_de(w.m.planejador.resolve_intent("abra a conversa com 3 no instagram", None))
    assert cadeia.resolvida is None and not cadeia.sem_casamento and set(cadeia.empatados) == ids
    unica = cadeia_de(w.m.planejador.resolve_intent("abra a conversa com @ana no instagram", None))
    assert unica.resolvida == "ig.abrir_conversa" and not unica.sem_casamento
    nada = cadeia_de(w.m.planejador.resolve_intent("faca um bolo de cenoura", None))
    assert nada.sem_casamento and nada.resolvida is None and nada.empatados == ()
    w.fechar()


def test_sombra_grava_as_duas_perguntas_e_casa_a_decisao_real_sem_inventar_a_do_empate(
        tmp_path: Path, porta_aberta: None) -> None:
    ids = {e: id_opaco(e) for e in ("ig.abrir_conversa", "ig.abrir_numero")}
    falso = DecisorFalso({
        PERGUNTA_CATALOGO: RespostaDeDecisao(escolha=ids["ig.abrir_conversa"], confianca=0.95),
        PERGUNTA_DESEMPATE: RespostaDeDecisao(escolha=ids["ig.abrir_numero"], confianca=0.9)})
    w = Mundo2(tmp_path, falso)
    cadeia = cadeia_de(w.m.planejador.resolve_intent("abra a conversa com 3 no instagram", None))
    w.consumidor.observar(run_id="run-1", comando="abra a conversa com 3 no instagram", app="instagram",
                          catalogo=w.catalogo(), cadeia=cadeia)
    w.porta.aguardar_sombras()
    assert len(falso.chamadas) == 1                                    # UMA chamada, fan-out de duas perguntas
    pedido = falso.chamadas[0]
    assert pedido.origem == "intencao" and pedido.classe == "C3" and pedido.modo == "shadow"
    assert set(pedido.estado) == {"comando", "app"} and pedido.estado["app"] == "instagram"
    por_id = {p.id: p for p in pedido.perguntas}
    assert set(por_id) == {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    assert set(por_id[PERGUNTA_CATALOGO].opcoes) == {*ids.values(), ID_NENHUMA}
    assert set(por_id[PERGUNTA_DESEMPATE].opcoes) == {*ids.values(), ID_NENHUMA}
    assert all(o == ID_NENHUMA or o.startswith("opt:") and "ig" not in o for o in por_id[PERGUNTA_CATALOGO].opcoes)
    linhas = {r["pergunta_id"]: r for r in w.linhas()}
    assert set(linhas) == {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    assert linhas[PERGUNTA_CATALOGO]["escolha"] == ids["ig.abrir_conversa"] and linhas[PERGUNTA_CATALOGO]["modo"] == "shadow"
    assert all(r["origem"] == "intencao" and r["classe"] == "C3" and r["ref"] == "run-1" for r in linhas.values())
    # a cadeia ficou num empate sem desfecho: não há decisão real a casar (a pessoa decide depois)
    assert all(r["decisao_real"] is None for r in linhas.values())
    w.fechar()


def test_sombra_casa_a_decisao_real_da_habilidade_resolvida_e_de_nenhuma(tmp_path: Path, porta_aberta: None) -> None:
    falso = DecisorFalso({PERGUNTA_CATALOGO: RespostaDeDecisao(escolha=id_opaco("ig.abrir_conversa"), confianca=0.95)})
    w = Mundo2(tmp_path, falso)
    resolvida = cadeia_de(w.m.planejador.resolve_intent("abra a conversa com @ana no instagram", None))
    w.consumidor.observar(run_id="run-resolvida", comando="abra a conversa com @ana no instagram", app=None,
                          catalogo=w.catalogo(), cadeia=resolvida)
    nada = cadeia_de(w.m.planejador.resolve_intent("faca um bolo de cenoura", None))
    w.consumidor.observar(run_id="run-nada", comando="faca um bolo de cenoura", app=None, catalogo=w.catalogo(), cadeia=nada)
    w.porta.aguardar_sombras()
    por_run = {r["ref"]: r for r in w.linhas()}
    assert set(por_run) == {"run-resolvida", "run-nada"}                  # sem empate: só a pergunta do catálogo
    assert por_run["run-resolvida"]["decisao_real"] == id_opaco("ig.abrir_conversa")
    assert por_run["run-nada"]["decisao_real"] == ID_NENHUMA
    assert por_run["run-nada"]["desfecho"] is None                        # o desfecho fica para o 31.10
    w.fechar()


def test_decisao_real_do_desempate_so_quando_a_cadeia_escolheu_um_dos_empatados() -> None:
    a, b = "ig.abrir_conversa", "ig.abrir_numero"
    perguntas = {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    reais = ConsumidorDeIntencao.decisoes_reais(CadeiaObservada(resolvida=a, empatados=(a, b)), perguntas)
    assert reais == {PERGUNTA_CATALOGO: id_opaco(a), PERGUNTA_DESEMPATE: id_opaco(a)}
    assert ConsumidorDeIntencao.decisoes_reais(CadeiaObservada(empatados=(a, b)), perguntas) == {}
    assert ConsumidorDeIntencao.decisoes_reais(CadeiaObservada(resolvida="outra", empatados=(a, b)), perguntas) == {
        PERGUNTA_CATALOGO: id_opaco("outra")}


def test_comando_com_entidade_residual_nao_sai_e_a_sombra_registra_privacidade(tmp_path: Path, porta_aberta: None) -> None:
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso)
    cadeia = CadeiaObservada(sem_casamento=True)
    w.consumidor.observar(run_id="run-priv", comando="fale com fulano @ exemplo", app=None, catalogo=w.catalogo(),
                          cadeia=cadeia)
    w.porta.aguardar_sombras()
    assert falso.chamadas == []                                          # o pedido NÃO saiu
    linhas = w.linhas()
    assert len(linhas) == 1 and linhas[0]["fallback_reason"] == "privacidade" and linhas[0]["escolha"] is None
    assert linhas[0]["decisao_real"] == ID_NENHUMA                       # o caminho atual segue medido
    w.fechar()


def test_estado_que_sai_nao_tem_entidade_nem_segredo(tmp_path: Path, porta_aberta: None) -> None:
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso)
    w.consumidor.observar(run_id="run-e", comando="curta o post de Maria Silva @maria.s fulano@exemplo.com 987654321",
                          app=None, catalogo=w.catalogo(), cadeia=CadeiaObservada(sem_casamento=True))
    w.porta.aguardar_sombras()
    assert len(falso.chamadas) == 1
    comando = falso.chamadas[0].estado["comando"]
    assert comando == "curta o post de [nome] [usuario] [email] [numero]"
    w.fechar()


def test_c3_fora_da_intencao_ou_em_on_e_recusada_pela_porta(tmp_path: Path, porta_aberta: None) -> None:
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow", "intencao": "on"})
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso, cfg)
    pergunta = pergunta_choice("q1", "Pick one.", {"opt:a": "A"})
    outra = PedidoDeDecisao(origem="curador", classe="C3", estado={"licao": "x"}, perguntas=(pergunta,), modo="shadow",
                            ref="r-curador")
    w.porta.consultar(outra)
    em_on = PedidoDeDecisao(origem="intencao", classe="C3", estado={"comando": "abra o app"}, perguntas=(pergunta,),
                            modo="on", ref="r-on")
    w.porta.consultar(em_on)                    # o consumidor `intencao` está em `on` na config, e o pedido pede `on`
    w.porta.aguardar_sombras()
    assert falso.chamadas == []
    motivos = {r["ref"]: r["fallback_reason"] for r in w.linhas()}
    assert motivos == {"r-curador": "privacidade", "r-on": "privacidade"}
    assert privacidade.validar(PedidoDeDecisao(origem="curador", classe="C3", estado={"licao": "x"},
                                               perguntas=(pergunta,), modo="shadow", ref="r")).motivo \
        in ("c3_so_intencao_em_shadow", "campo_fora_da_lista")
    assert privacidade.validar(em_on).motivo == "c3_so_intencao_em_shadow"
    w.fechar()


def test_consumidor_intencao_em_on_na_config_continua_em_shadow(tmp_path: Path, porta_aberta: None) -> None:
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "on"})
    falso = DecisorFalso({PERGUNTA_CATALOGO: RespostaDeDecisao(escolha=id_opaco("ig.abrir_conversa"), confianca=0.95)})
    w = Mundo2(tmp_path, falso, cfg)
    w.consumidor.observar(run_id="run-on", comando="abra a conversa com @ana", app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(resolvida="ig.abrir_conversa"))
    w.porta.aguardar_sombras()
    assert [r["modo"] for r in w.linhas()] == ["shadow"]                 # a sombra da 31.9 nunca vira `on`
    w.fechar()


@pytest.mark.parametrize("cfg", [None, DecisaoFechadaCfg(), DecisaoFechadaCfg(enabled=True),
                                 DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "off"}),
                                 DecisaoFechadaCfg(enabled=False, consumidores={"intencao": "shadow"})])
def test_padrao_desligado_zero_chamadas_e_zero_linhas(tmp_path: Path, porta_aberta: None,
                                                      cfg: DecisaoFechadaCfg | None) -> None:
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso, cfg)
    assert not w.consumidor.ativo()
    w.consumidor.observar(run_id="run-off", comando="abra a conversa com @ana", app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(resolvida="ig.abrir_conversa"))
    w.porta.aguardar_sombras()
    assert falso.chamadas == [] and w.linhas() == []
    w.fechar()


def test_com_o_envio_fechado_no_codigo_o_decisor_nao_e_chamado(tmp_path: Path) -> None:
    assert privacidade.JEV_RUNTIME_SEND_APPROVED is False               # sem monkeypatch: o padrão do código
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso)
    w.consumidor.observar(run_id="run-fechado", comando="abra a conversa com @ana", app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(resolvida="ig.abrir_conversa"))
    w.porta.aguardar_sombras()
    assert falso.chamadas == []
    assert [r["fallback_reason"] for r in w.linhas()] == ["privacidade"]
    w.fechar()


def test_catalogo_vazio_ou_acima_do_teto_nao_manda_r2(tmp_path: Path, porta_aberta: None) -> None:
    w = Mundo2(tmp_path, DecisorFalso())
    cadeia = CadeiaObservada(sem_casamento=True)
    assert w.consumidor.pedido(run_id="r", comando="abra o app", app=None, catalogo=[], cadeia=cadeia) is None
    grande = [EntradaDeCatalogo(f"flow:f{i}", f"Fluxo {i}") for i in range(255)]
    assert w.consumidor.pedido(run_id="r", comando="abra o app", app=None, catalogo=grande, cadeia=cadeia) is None
    limite = grande[:254]
    pedido = w.consumidor.pedido(run_id="r", comando="abra o app", app=None, catalogo=limite, cadeia=cadeia)
    assert pedido is not None and len(pedido.perguntas[0].opcoes) == 255   # 254 + `nenhuma`
    w.fechar()


# ================================================================== 3. o `_plan` não espera a sombra
class DecisorQueEspera(DecisorFalso):
    """Segura a resposta até o teste liberar: prova que o plano termina antes da sombra, sem depender de relógio."""

    def __init__(self, respostas: dict[str, RespostaDeDecisao]) -> None:
        super().__init__(respostas)
        self.liberar = threading.Event()

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        self.chamadas.append(pedido)
        self.liberar.wait(20.0)
        return ResultadoDeDecisao({p.id: self.respostas.get(p.id, RespostaDeDecisao(fallback_reason="desligado"))
                                   for p in pedido.perguntas})


async def test_plan_termina_sem_esperar_o_decisor_lento_e_a_sombra_casa_depois(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    decisor = DecisorQueEspera({PERGUNTA_CATALOGO: RespostaDeDecisao(escolha=id_opaco("ig.abrir_conversa"), confianca=0.95)})
    st.decisao_fechada.decisor = decisor
    st.decisao_fechada.cfg = CFG_SHADOW
    catalogo = [EntradaDeCatalogo("ig.abrir_conversa", "Abrir conversa", "Abre a conversa com uma pessoa"),
                EntradaDeCatalogo("ig.curtir", "Curtir", "")]
    st.runs.sombra_intencao = SombraDaIntencao(
        ConsumidorDeIntencao(st.decisao_fechada, st.decisao_sombra, espera_s=10.0),
        resolver=lambda c, p: IntentResolution(ResolutionStatus.NO_MATCH), catalogo=lambda: catalogo)
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    t0 = time.monotonic()
    detalhe = await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    assert time.monotonic() - t0 < 10.0
    # o plano ACABOU enquanto o decisor segue segurado: o `_plan` não esperou a sombra
    await harness.wait(lambda: len(decisor.chamadas) == 1, 10.0, "a sombra da intenção chamar o decisor")
    assert not decisor.liberar.is_set() and detalhe is not None
    assert st.decisao_sombra._db.scalar("SELECT COUNT(*) FROM decisao_fechada_sombra") == 0   # nada gravado ainda
    decisor.liberar.set()
    await st.runs.sombra_intencao.aguardar()
    st.decisao_fechada.aguardar_sombras()
    await harness.wait(lambda: st.db.scalar("SELECT COUNT(*) FROM decisao_fechada_sombra WHERE decisao_real IS NOT NULL")
                       == 1, 10.0, "a decisão real ser casada")
    linha = st.db.one("SELECT * FROM decisao_fechada_sombra")
    assert linha["pergunta_id"] == PERGUNTA_CATALOGO and linha["ref"] == run.id and linha["origem"] == "intencao"
    assert linha["decisao_real"] == ID_NENHUMA                            # NO_MATCH: o planejador ficou com o comando
    assert linha["escolha"] == id_opaco("ig.abrir_conversa")


async def test_plan_com_a_config_padrao_nao_chama_ninguem(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    decisor = DecisorFalso()
    st.decisao_fechada.decisor = decisor                  # a config continua a padrão (`enabled=false`)
    assert st.runs.sombra_intencao is not None and not st.runs.sombra_intencao.ativo()
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    await st.runs.sombra_intencao.aguardar()
    st.decisao_fechada.aguardar_sombras()
    assert decisor.chamadas == [] and st.db.scalar("SELECT COUNT(*) FROM decisao_fechada_sombra") == 0
