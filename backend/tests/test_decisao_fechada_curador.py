"""31.8: triagem do curador do Livro em SOMBRA (R1, ADR-069; `planning/decisao_fechada/curador.py`).

O que se prova, tudo `simulated` (DecisorFalso, banco de teste; `JEV_RUNTIME_SEND_APPROVED`, aberto desde o 31.17, é
fixado por `monkeypatch` em cada teste que depende dele):

- o estado é C0: só os campos nomeados, cada um rótulo fechado ou contagem; conteúdo, app, capability, ids, datas e
  texto de pessoa nunca saem, e a lista do consumidor é a MESMA da privacidade;
- só lição e receita (F1); memória, fluxo, tela, voz e preferência não vão;
- o parecer do curador principal volta INTACTO, e a falha dele sobe sem sombra;
- a sombra grava a escolha do Jev e NÃO casa decisão real: a do parecer só vale depois de `validar_saida`, e o relatório
  do 31.10 a lê de `learning_reviews` (`decisao_real_da_triagem`: válido, não simulado, dentro da régua; I2);
- com a config padrão, ou com o envio fechado no código, o decisor nunca é chamado.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from app.config import DecisaoFechadaCfg
from app.db import Database
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import ID_NENHUMA, RespostaDeDecisao
from app.planning.decisao_fechada.curador import (CAMPOS, CAMPOS_V2, OPCOES, PERGUNTA_TRIAGEM, TRIAGEM_DO_PARECER,
                                                  CuradorComTriagemEmSombra, TriagemDoCurador, decisao_real_da_triagem,
                                                  estado_do_dossie)
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.porta import Porta
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra

from .conftest import Harness, _dsn_de_teste

TEXTO_DA_PESSOA = "texto unico escrito pela pessoa sobre o botao"
HASH = "a" * 64


def _dossie(kind: str = "licao", **extra: object) -> dict[str, object]:
    d: dict[str, object] = {
        "versao_do_dossie": 1,
        "item": {"id": "item:licao:L1", "kind": kind, "ref": "L1", "app": "com.instagram.android",
                 "capability": "OPEN_POST", "app_version": "447", "estado": "candidate", "origem": "manual",
                 "side_effect": False, "human_origin": True, "criado_em": "2026-10-02T12:00:00.000Z"},
        "risco": {"classe": "B", "politica": "dono_em_lote", "motivo": None, "razoes": ["texto_de_pessoa"],
                  "fatos": {"side_effect": False}},
        "conteudo": {"modelo": "alvo_ausente", "alvo": TEXTO_DA_PESSOA},
        "evidencias": {"total": 3, "incluidas": 3, "lista": [
            {"id": "ev:1", "posicao": "for", "origin_ref": "run:r1", "run_id": "r1", "aparelho": "android-01",
             "app_version": "447", "simulated": True, "em": "2026-10-02T12:00:00.000Z"},
            {"id": "ev:2", "posicao": "against", "origin_ref": "run:r2", "run_id": "r2", "aparelho": "android-02",
             "app_version": "447", "simulated": False, "em": "2026-10-02T12:00:00.000Z"},
            {"id": "ev:3", "posicao": "for", "origin_ref": "run:r3", "run_id": "r3", "aparelho": "android-01",
             "app_version": "447", "simulated": False, "em": "2026-10-02T12:00:00.000Z"}]},
        "falhas": [{"id": "falha:1", "falha": "alvo_ausente", "ocorrencias": 4, "estado": "aberto"}],
        "votos": [], "intervencoes": [{"id": "int:1", "tipo": "tomada", "em": "x", "run_id": "r9"}],
        "execucoes": ["run:r1", "run:r2"], "saude": {"rotulo": "degradando", "motivos": [], "dimensoes": []},
        "citaveis": ["item:licao:L1", "ev:1", "ev:2", "ev:3"],
    }
    d.update(extra)
    return d


@dataclass
class Pedido:
    dossie: dict[str, object]
    dossie_hash: str = HASH


@dataclass
class Resposta:
    bruto: dict[str, object]
    simulado: bool | None = None                 # por resposta (`RespostaDeRevisao.simulado`); None = vale o do adaptador


@dataclass
class CuradorFalso:
    provedor: str = "anthropic"
    simulado: bool = False
    decisao: object = "observar"
    erro: Exception | None = None
    simulado_da_resposta: bool | None = None
    pedidos: list[Pedido] = field(default_factory=list)

    def revisar(self, pedido: Pedido) -> Resposta:
        self.pedidos.append(pedido)
        if self.erro is not None:
            raise self.erro
        return Resposta({"decisao": self.decisao, "evidencias_citadas": ["ev:2"]}, self.simulado_da_resposta)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    d = Database(_dsn_de_teste() or tmp_path / "curador-sombra.sqlite3")
    d.migrate()
    return d


@pytest.fixture
def porta_aberta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)


def _montar(db: Database, decisor: DecisorFalso, cfg: DecisaoFechadaCfg | None = None
            ) -> tuple[TriagemDoCurador, Porta, RepositorioDeSombra]:
    repo = RepositorioDeSombra(db)
    porta = Porta(decisor, cfg=cfg if cfg is not None else DecisaoFechadaCfg(enabled=True,
                                                                             consumidores={"curador": "shadow"}),
                  observador=observador_de_sombra(repo))
    return TriagemDoCurador(porta), porta, repo


# ------------------------------------------------------------------ o estado
def test_estado_e_c0_por_campos_nomeados_sem_conteudo_nem_identificador() -> None:
    e = estado_do_dossie(_dossie())
    assert set(e) <= CAMPOS and e == {
        "kind": "licao", "estado": "candidate", "origem": "manual", "side_effect": "nao", "human_origin": "sim",
        "classe_de_risco": "B", "politica": "dono_em_lote", "saude": "degradando",
        "evidencias_total": "3", "evidencias_a_favor": "2", "evidencias_contra": "1", "evidencias_simuladas": "1",
        "falhas": "1", "falhas_ocorrencias": "4", "votos": "0", "intervencoes": "1", "execucoes": "2"}
    texto = json.dumps(e)
    for proibido in (TEXTO_DA_PESSOA, "alvo_ausente", "com.instagram", "OPEN_POST", "L1", "ev:", "run:", "android-0",
                     "2026-", "447"):
        assert proibido not in texto


def test_rotulo_que_nao_e_vocabulario_nao_sai() -> None:
    d = _dossie()
    d["item"] = {**d["item"], "estado": "Texto Livre De Alguém", "origem": "x" * 60}   # type: ignore[dict-item]
    e = estado_do_dossie(d)
    assert "estado" not in e and "origem" not in e


def test_a_lista_do_consumidor_e_a_da_privacidade() -> None:
    assert privacidade.CAMPOS_POR_ORIGEM["curador"] == CAMPOS_V2      # 31.11: v1 + os campos de sinal do v2


@pytest.mark.parametrize("kind", ["memoria", "fluxo", "tela", "voz", "preferencia", "habilidade"])
def test_fora_de_f1_nao_ha_pedido(kind: str) -> None:
    assert TriagemDoCurador.pedido(_dossie(kind), HASH) is None


def test_pedido_c0_em_shadow_com_choice_e_nenhuma() -> None:
    p = TriagemDoCurador.pedido(_dossie("receita"), HASH)
    assert p is not None and (p.origem, p.classe, p.modo, p.ref) == ("curador", "C0", "shadow", HASH)
    [q] = p.perguntas
    assert q.id == PERGUNTA_TRIAGEM and q.tipo == "choice" and ID_NENHUMA in q.opcoes
    assert set(q.opcoes) - {ID_NENHUMA} == set(TRIAGEM_DO_PARECER.values())


# ------------------------------------------------------------------ o decorador e a sombra
def test_parecer_volta_intacto_e_a_sombra_grava_sem_decisao_real(db: Database, porta_aberta: None) -> None:
    decisor = DecisorFalso({PERGUNTA_TRIAGEM: RespostaDeDecisao(escolha="opt:rebaixar",
                                                                probabilidades={"opt:rebaixar": 0.9}, confianca=0.9)})
    triagem, porta, _ = _montar(db, decisor)
    interno = CuradorFalso(decisao="observar")
    curador = CuradorComTriagemEmSombra(interno, triagem)
    resposta = curador.revisar(Pedido(_dossie()))
    assert resposta.bruto == {"decisao": "observar", "evidencias_citadas": ["ev:2"]}
    assert (curador.provedor, curador.simulado) == ("anthropic", False)
    porta.aguardar_sombras()
    [chamada] = decisor.chamadas
    assert dict(chamada.estado) == estado_do_dossie(_dossie())
    [linha] = db.query("SELECT * FROM decisao_fechada_sombra")
    assert (linha["origem"], linha["classe"], linha["pergunta_id"], linha["ref"]) == ("curador", "C0",
                                                                                   PERGUNTA_TRIAGEM, HASH)
    # I2: o parecer ainda não passou por `validar_saida` (roda DEPOIS do `revisar`): nada é casado na hora; o relatório
    # do 31.10 lê a decisão real de `learning_reviews` pelo `ref` (o `dossie_hash`)
    assert (linha["escolha"], linha["decisao_real"]) == ("opt:rebaixar", None)
    assert TEXTO_DA_PESSOA not in json.dumps([dict(r) for r in db.query("SELECT * FROM decisao_fechada_sombra")])


@pytest.mark.parametrize("decisao", ["aprovar", "observar", "inventada", None])
def test_nenhum_parecer_casa_na_hora_nem_o_simulado(db: Database, porta_aberta: None, decisao: object) -> None:
    decisor = DecisorFalso({PERGUNTA_TRIAGEM: RespostaDeDecisao(escolha="opt:manter",
                                                                probabilidades={"opt:manter": 0.9}, confianca=0.9)})
    triagem, porta, _ = _montar(db, decisor)
    curador = CuradorComTriagemEmSombra(CuradorFalso(decisao=decisao, simulado_da_resposta=True), triagem)
    curador.revisar(Pedido(_dossie()))
    porta.aguardar_sombras()
    [linha] = db.query("SELECT escolha, decisao_real FROM decisao_fechada_sombra")
    assert (linha["escolha"], linha["decisao_real"]) == ("opt:manter", None)


def test_falha_do_curador_principal_sobe_sem_sombra(db: Database, porta_aberta: None) -> None:
    decisor = DecisorFalso()
    triagem, porta, _ = _montar(db, decisor)
    curador = CuradorComTriagemEmSombra(CuradorFalso(erro=RuntimeError("provedor fora")), triagem)
    with pytest.raises(RuntimeError):
        curador.revisar(Pedido(_dossie()))
    porta.aguardar_sombras()
    assert decisor.chamadas == [] and db.query("SELECT id FROM decisao_fechada_sombra") == []


def test_memoria_nao_vai_mas_o_parecer_volta(db: Database, porta_aberta: None) -> None:
    decisor = DecisorFalso()
    triagem, porta, _ = _montar(db, decisor)
    resposta = CuradorComTriagemEmSombra(CuradorFalso(), triagem).revisar(Pedido(_dossie("memoria")))
    porta.aguardar_sombras()
    assert resposta.bruto["decisao"] == "observar" and decisor.chamadas == []


@pytest.mark.parametrize("cfg", [None, DecisaoFechadaCfg(), DecisaoFechadaCfg(enabled=True),
                                 DecisaoFechadaCfg(enabled=True, consumidores={"curador": "off"}),
                                 DecisaoFechadaCfg(enabled=False, consumidores={"curador": "shadow"})])
def test_config_padrao_ou_desligada_nao_chama_ninguem(db: Database, porta_aberta: None,
                                                      cfg: DecisaoFechadaCfg | None) -> None:
    decisor = DecisorFalso()
    repo = RepositorioDeSombra(db)
    porta = Porta(decisor, cfg=cfg, observador=observador_de_sombra(repo))
    triagem = TriagemDoCurador(porta)
    assert not triagem.ativo()
    CuradorComTriagemEmSombra(CuradorFalso(), triagem).revisar(Pedido(_dossie()))
    porta.aguardar_sombras()
    assert decisor.chamadas == [] and db.query("SELECT id FROM decisao_fechada_sombra") == []


def test_com_o_envio_fechado_no_codigo_o_decisor_nao_e_chamado(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """`JEV_RUNTIME_SEND_APPROVED` fechado (aberto de fábrica desde o 31.17): a triagem nem monta o pedido."""
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", False)
    decisor = DecisorFalso()
    triagem, porta, _ = _montar(db, decisor)
    CuradorComTriagemEmSombra(CuradorFalso(), triagem).revisar(Pedido(_dossie()))
    porta.aguardar_sombras()
    assert decisor.chamadas == []
    linhas = db.query("SELECT fallback_reason, escolha FROM decisao_fechada_sombra")
    assert [(r["fallback_reason"], r["escolha"]) for r in linhas] in ([], [("privacidade", None)])


async def test_o_appstate_embrulha_o_curador_do_hub_com_a_triagem(harness: Harness) -> None:
    """Ligação (suíte 5): o aprendizado recebe o curador do hub (30.12) embrulhado pela triagem em sombra, e a triagem usa
    a porta do próprio `AppState`. De fábrica, inerte: nada é consultado."""
    st = harness.state
    laco = next(x for x in st.learning.lacos if x.nome == "curador")
    curador = laco.curador._curador                                            # type: ignore[attr-defined]  # noqa: SLF001
    assert isinstance(curador, CuradorComTriagemEmSombra)
    assert curador._interno is st._curador_do_hub                              # noqa: SLF001
    assert curador._triagem is st._triagem_do_curador                           # noqa: SLF001
    assert st._triagem_do_curador._porta is st.decisao_fechada                  # noqa: SLF001
    assert not st._triagem_do_curador.ativo()


def test_porta_encerrada_a_triagem_nao_grava_nada(db: Database, porta_aberta: None) -> None:
    """Desligamento (I1): sem casamento, a triagem não tem thread; depois de `Porta.encerrar` nada novo é consultado nem
    gravado, e o parecer do curador principal continua voltando."""
    decisor = DecisorFalso()
    triagem, porta, _ = _montar(db, decisor)
    porta.encerrar()
    resposta = CuradorComTriagemEmSombra(CuradorFalso(), triagem).revisar(Pedido(_dossie()))
    porta.aguardar_sombras()
    assert resposta.bruto["decisao"] == "observar"
    assert decisor.chamadas == [] and db.query("SELECT id FROM decisao_fechada_sombra") == []


# ------------------------------------------------------------------ a decisão real (I2): só parecer VÁLIDO, real e da régua
@pytest.mark.parametrize("decisao, real", list(TRIAGEM_DO_PARECER.items()))
def test_parecer_valido_e_real_vira_decisao_da_regua(decisao: str, real: str) -> None:
    assert decisao_real_da_triagem(decisao, validade="ok", simulado=0) == real and real in OPCOES


@pytest.mark.parametrize("validade", ["invalida:citacao", "invalida:alvo", "recusada:custo", "", None])
def test_parecer_invalido_ou_recusado_nao_e_decisao_real(validade: object) -> None:
    assert decisao_real_da_triagem("rebaixar", validade=validade, simulado=0) is None


@pytest.mark.parametrize("simulado", [1, True])
def test_parecer_simulado_nao_e_decisao_real(simulado: object) -> None:
    assert decisao_real_da_triagem("rebaixar", validade="ok", simulado=simulado) is None


@pytest.mark.parametrize("decisao", ["aprovar", "possivelmente_obsoleto", "substituir", "fundir", "inventada", ["manter"],
                                     7, None, {"id": "manter"}, "MANTER", ""])
def test_parecer_sem_par_na_regua_nao_e_decisao_real(decisao: object) -> None:
    assert decisao_real_da_triagem(decisao, validade="ok", simulado=0) is None
