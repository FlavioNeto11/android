"""Item 28.7 (parte 2) — o relatório determinístico e o registro das observações no fechamento da ocorrência.

Prova `simulated` (`arquivo::teste`): domínio puro, e o laço de verdade (28.4) em SQLite com `RunService` real, relógio falso e
planejamento desligado (a execução fica em `planning` e o teste a leva ao estado que quer). Provedor de IA: nenhum; o resumidor
do teste é falso e conta quantas vezes foi chamado. NADA aqui prova o ambiente real (o 28.12 liga o laço no central).

Cobre: mesmas entradas, mesmo relatório (em qualquer ordem); incerto, falha e ausência nunca viram conclusão; o "não coberto" lista
perdidas, puladas, incertas, que falharam, canceladas, em aberto e as lacunas; a observação é gravada no fechamento, na mesma
transação, reentrante e sobrevive à purga; o relatório de encerramento sai uma vez, no encerramento e no cancelamento; o resumo
por IA é opcional e nunca substitui o conteúdo.
"""
from __future__ import annotations

import json
import random
from dataclasses import replace

import pytest

from app.config import PedidosCfg
from app.modules.pedidos.domain import relatorio as rel
from app.modules.pedidos.domain.relatorio import EntradaDoRelatorio, ObservacaoVista, OcorrenciaVista
from app.modules.pedidos.domain.resumo import ResumoDeIA, SemResumo
from app.modules.pedidos.infrastructure.laco import LacoDePedidos
from app.modules.pedidos.infrastructure.repositorio_memoria import RepositorioDeMemoria
from app.taskqueue.travas import Lideranca
from app.util import to_iso

from .conftest import Harness
from .test_pedidos_laco import _agora, _assentar, _ocs, _runs, h  # noqa: F401 - `h` é a fixture
from .test_travas import Relogio

ATE = "2026-10-02T23:59:59Z"


def _oc(oid: str, hora: int, estado: str = "concluida", motivo: str | None = None, custo: float = 0.0) -> OcorrenciaVista:
    return OcorrenciaVista(oid, f"2026-10-02T{hora:02d}:00:00Z", estado, motivo, custo)


def _ob(oid: str, ocorrencia: str, hora: int, valor: str | None = "R$ 10", *, nome: str = "preco", alvo: str = "android-01",
        situacao: str = "observado") -> ObservacaoVista:
    return ObservacaoVista(oid, ocorrencia, alvo, nome, situacao, valor, fonte="Chrome / ler o preço",
                           capturado_em=f"2026-10-02T{hora:02d}:00:05.000Z", sha256="abc" if valor else None)


def _entrada(ocs: list[OcorrenciaVista], obs: list[ObservacaoVista], **kw) -> EntradaDoRelatorio:
    return EntradaDoRelatorio(pedido_id="p1", pedido_versao=1, periodo_ate=ATE, ocorrencias=ocs, observacoes=obs, **kw)


def _tipos(itens: list) -> list[str]:
    return [i["tipo"] for i in itens]


# ===================================================================== 1. domínio puro
def test_mesmas_entradas_mesmo_relatorio_em_qualquer_ordem() -> None:
    ocs = [_oc("o1", 8), _oc("o2", 9, "perdida", "janela vencida"), _oc("o3", 10), _oc("o4", 11, "pulada", "pedido pausado")]
    obs = [_ob("b1", "o1", 8, "R$ 10"), _ob("b3", "o3", 10, "R$ 12"), _ob("b3b", "o3", 10, "ok", nome="estoque")]
    base = rel.serializar(rel.montar(_entrada(ocs, obs, criterios=["abaixo de R$ 11"], pendencias=["confirmar o CEP"])))
    for semente in range(8):
        o2, b2 = ocs[:], obs[:]
        random.Random(semente).shuffle(o2)
        random.Random(semente).shuffle(b2)
        assert rel.serializar(rel.montar(_entrada(o2, b2, criterios=["abaixo de R$ 11"], pendencias=["confirmar o CEP"]))) == base
    assert "gerado" not in base, "o instante de geração mora na linha da tabela, nunca no conteúdo"


def test_o_relatorio_so_tem_as_tres_secoes_e_a_conclusao_diz_o_alcance() -> None:
    r = rel.montar(_entrada([_oc("o1", 8)], [_ob("b1", "o1", 8)]))
    assert {"observado", "conclusao", "nao_coberto"} <= set(r)
    assert "amostra coletada" in r["conclusao"]["alcance"]                              # type: ignore[index]
    [x] = r["observado"]                                                                # type: ignore[misc]
    assert (x["fonte"], x["capturado_em"], x["valor"]) == ("Chrome / ler o preço", "2026-10-02T08:00:05.000Z", "R$ 10")


def test_conclusao_so_com_o_que_foi_observado_e_comprovado() -> None:
    ocs = [_oc("o1", 8), _oc("o2", 9), _oc("o3", 10)]
    obs = [_ob("b1", "o1", 8, "R$ 10"), _ob("b2", "o2", 9, "R$ 12"), _ob("b3", "o3", 10, "R$ 12")]
    c = rel.montar(_entrada(ocs, obs))["conclusao"]                                     # type: ignore[index]
    assert c["situacao"] == "sustentada"                                                # nada ficou de fora
    assert _tipos(c["itens"]) == ["amostra", "ultimo_valor", "variacao"]
    amostra, ultimo, variacao = c["itens"]
    assert "Na amostra coletada, 3 de 3" in amostra["texto"]
    assert (ultimo["valor"], ultimo["observacoes"]) == ("R$ 12", 3)
    assert (variacao["mudou"], variacao["anterior"], variacao["atual"]) == (False, "R$ 12", "R$ 12")
    assert "mudou" in variacao["texto"] and "amostra" in variacao["texto"]


def test_sem_observacao_a_conclusao_e_vazia_nunca_nada_mudou() -> None:
    r = rel.montar(_entrada([_oc("o1", 8)], []))
    assert r["conclusao"]["itens"] == [] and r["conclusao"]["situacao"] == "sem_conclusao"      # type: ignore[index]
    assert "sem_observacao" in _tipos(r["nao_coberto"])                                 # type: ignore[arg-type]
    vazio = rel.montar(_entrada([], []))
    assert vazio["conclusao"]["situacao"] == "sem_conclusao"                            # type: ignore[index]
    assert _tipos(vazio["nao_coberto"]) == ["periodo_sem_ocorrencias"]                  # type: ignore[arg-type]


@pytest.mark.parametrize("estado", ["incerta", "falhou", "cancelada", "perdida", "pulada"])
def test_incerto_falha_e_ausencia_nunca_viram_conclusao(estado: str) -> None:
    """Mesmo que a observação tenha sido gravada `observado` por engano, a ocorrência que não concluiu a rebaixa."""
    for situacao in ("incerto", "observado"):
        r = rel.montar(_entrada([_oc("o1", 8, estado, "motivo")], [_ob("b1", "o1", 8, "R$ 10", situacao=situacao)]))
        assert r["observado"] == [] and r["conclusao"]["itens"] == []                   # type: ignore[index]
        assert r["conclusao"]["situacao"] == "sem_conclusao"                            # type: ignore[index]
        tipos = _tipos(r["nao_coberto"])                                                # type: ignore[arg-type]
        assert f"ocorrencia_{estado}" in tipos and "valor_incerto" in tipos and "lacuna" in tipos


def test_valor_ausente_numa_ocorrencia_concluida_e_sem_valor_nao_observado() -> None:
    ausente = ObservacaoVista("b1", "o1", "", "resultado", "ausente", None, tipo="resultado", trecho="sem etapa de leitura",
                              capturado_em="2026-10-02T08:00:05.000Z")
    r = rel.montar(_entrada([_oc("o1", 8)], [ausente]))
    assert r["observado"] == [] and r["conclusao"]["itens"] == []                       # type: ignore[index]
    [item] = [i for i in r["nao_coberto"] if i["tipo"] == "sem_valor"]                  # type: ignore[attr-defined]
    assert "sem etapa de leitura" in item["texto"]


def test_nao_coberto_lista_perdidas_puladas_incertas_falhas_canceladas_em_aberto_e_lacunas() -> None:
    ocs = [_oc("o1", 6), _oc("o2", 7, "perdida", "janela vencida"), _oc("o3", 8, "pulada", "pedido pausado"),
           _oc("o4", 9), _oc("o5", 10, "incerta", "objetivo incerto: x"), _oc("o6", 11, "falhou", "parcial: 1 de 2"),
           _oc("o7", 12), _oc("o8", 13, "cancelada", "pedido cancelado"), _oc("o9", 14, "rodando"),
           _oc("o10", 15, "prevista")]
    obs = [_ob(f"b{n}", o, h) for n, o, h in ((1, "o1", 6), (4, "o4", 9), (7, "o7", 12))]
    nc = rel.montar(_entrada(ocs, obs))["nao_coberto"]                                  # type: ignore[assignment]
    por_tipo = {t: [i for i in nc if i["tipo"] == t] for t in set(_tipos(nc))}          # type: ignore[arg-type]
    for estado, oid in (("perdida", "o2"), ("pulada", "o3"), ("incerta", "o5"), ("falhou", "o6"), ("cancelada", "o8")):
        [i] = por_tipo[f"ocorrencia_{estado}"]
        assert i["ocorrencia_id"] == oid and i["motivo"], estado
    assert sorted(i["ocorrencia_id"] for i in por_tipo["ocorrencia_em_aberto"]) == ["o10", "o9"]
    lacunas = sorted((i["de"], i["ate"], i["ocorrencias"]) for i in por_tipo["lacuna"])
    assert lacunas == [("2026-10-02T07:00:00Z", "2026-10-02T08:00:00Z", 2), ("2026-10-02T10:00:00Z", "2026-10-02T11:00:00Z", 2),
                       ("2026-10-02T13:00:00Z", "2026-10-02T15:00:00Z", 3)]


def test_periodo_filtra_as_ocorrencias_e_a_conta_de_custo_e_da_amostra() -> None:
    ocs = [_oc("o1", 6, custo=0.01), _oc("o2", 8, custo=0.02), _oc("o3", 10, custo=0.04)]
    obs = [_ob("b1", "o1", 6), _ob("b2", "o2", 8), _ob("b3", "o3", 10)]
    r = rel.montar(replace(_entrada(ocs, obs), periodo_de="2026-10-02T07:00:00Z", periodo_ate="2026-10-02T09:00:00Z"))
    assert r["ocorrencias"] == {"total": 1, "por_estado": {"concluida": 1}}
    assert [x["id"] for x in r["observado"]] == ["b2"] and r["custo_usd"] == 0.02       # type: ignore[attr-defined]
    assert r["periodo"] == {"de": "2026-10-02T07:00:00Z", "ate": "2026-10-02T09:00:00Z"}


def test_criterios_e_pendencias_vao_para_o_nao_coberto_porque_nada_os_verifica_em_estrutura() -> None:
    r = rel.montar(_entrada([_oc("o1", 8)], [_ob("b1", "o1", 8)], criterios=["preço abaixo de R$ 11"],
                            pendencias=["confirmar o CEP"]))
    tipos = _tipos(r["nao_coberto"])                                                    # type: ignore[arg-type]
    assert "criterio_nao_avaliado" in tipos and "pendencia" in tipos
    assert r["conclusao"]["situacao"] == "parcial"                                      # type: ignore[index]


def test_observado_passa_do_teto_e_o_relatorio_diz_quantas_ficaram_de_fora() -> None:
    n = rel.MAX_OBSERVADO + 5
    ocs = [OcorrenciaVista(f"o{i:04d}", f"2026-10-02T00:{i // 60:02d}:{i % 60:02d}Z", "concluida") for i in range(n)]
    obs = [ObservacaoVista(f"b{i:04d}", f"o{i:04d}", "", "preco", "observado", str(i),
                           capturado_em=f"2026-10-02T00:{i // 60:02d}:{i % 60:02d}.000Z") for i in range(n)]
    r = rel.montar(_entrada(ocs, obs))
    assert len(r["observado"]) == rel.MAX_OBSERVADO and r["observado"][0]["id"] == "b0005"   # type: ignore[index,arg-type]
    [t] = [i for i in r["nao_coberto"] if i["tipo"] == "observado_truncado"]            # type: ignore[attr-defined]
    assert t["omitidas"] == 5


def test_o_texto_longo_de_motivo_e_cortado() -> None:
    r = rel.montar(_entrada([_oc("o1", 8, "falhou", "x" * 500)], []))
    [i] = [i for i in r["nao_coberto"] if i["tipo"] == "ocorrencia_falhou"]             # type: ignore[attr-defined]
    assert len(i["motivo"]) <= 160


# ===================================================================== 2. o laço grava as observações
def _leitura(db, run_id: str, nome: str, valor: str, *, kind: str = "text", alvo: str = "android-01",
             app: str | None = None) -> None:
    """O que uma etapa de leitura deixa (056): objetivo, etapa e saída, direto no banco do teste."""
    oid, sid = f"{run_id}:{alvo}", f"{run_id}:{alvo}:v1:ler_{nome}"
    if not db.one("SELECT id FROM objectives WHERE id=?", (oid,)):
        db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,1)",
                   (oid, run_id, alvo, "succeeded"))
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, postcondition,"
               " timeout_s, max_attempts, status) VALUES (?,?,?,?,1,1,?,?,?,'{}',60,1,'succeeded')",
               (sid, run_id, oid, alvo, f"ler_{nome}", f"Ler {nome}", "ler"))
    db.execute("INSERT INTO step_outputs(id, run_id, objective_id, step_id, name, value, value_kind, app_id, created_at)"
               " VALUES (?,?,?,?,?,?,?,?,?)", (f"{oid}:{nome}", run_id, oid, sid, nome, valor, kind, app,
                                               "2026-10-02T12:00:30.000Z"))


def _laco_com(h: Harness, r: Relogio, *, resumidor=None, **cfg) -> LacoDePedidos:
    return LacoDePedidos(h.state.db, h.state.runs, Lideranca(h.state.db, dono="laco-a", relogio=r),
                         PedidosCfg(enabled=True, prazo_inicio_s=604_800, **cfg), relogio=r, resumidor=resumidor)


async def test_o_fechamento_grava_as_observacoes_a_fonte_na_memoria_e_a_purga_nao_as_apaga(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _leitura(db, run["id"], "preco", "R$ 3.499,00", app=None)
    _leitura(db, run["id"], "link", "https://loja.exemplo/p/1", kind="url")
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    [oc] = _ocs(db)
    assert oc["estado"] == "concluida"
    obs = {o["nome"]: o for o in RepositorioDeMemoria(db).todas_as_observacoes("ped1")}
    assert set(obs) == {"preco", "link"}
    p = obs["preco"]
    assert (p["situacao"], p["valor"], p["alvo"], p["ocorrencia_id"], p["run_id"]) == (
        "observado", "R$ 3.499,00", "android-01", oc["id"], run["id"])
    assert p["fonte"] == "Ler preco" and p["sha256"] and len(p["sha256"]) == 64 and obs["link"]["tipo"] == "url"
    memoria = {e.chave: e for e in RepositorioDeMemoria(db).entradas("ped1")}
    assert set(memoria) == {"fonte:preco:android-01", "fonte:link:android-01"} and memoria["fonte:preco:android-01"].tipo == "fonte"
    assert json.loads(memoria["fonte:preco:android-01"].valor)["captura"] == p["sha256"][:16]
    laco.uma_volta()
    assert len(RepositorioDeMemoria(db).todas_as_observacoes("ped1")) == 2, "outra volta não duplica"
    db.execute("DELETE FROM runs WHERE id=?", (run["id"],))                             # a purga da execução
    assert {o["nome"] for o in RepositorioDeMemoria(db).todas_as_observacoes("ped1")} == {"preco", "link"}


async def test_ocorrencia_que_falhou_guarda_o_valor_como_incerto_e_o_relatorio_nao_o_conclui(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _leitura(db, run["id"], "preco", "R$ 10")
    _assentar(db, run["id"], "failed", "a etapa seguinte falhou")
    laco.uma_volta()
    [o] = RepositorioDeMemoria(db).todas_as_observacoes("ped1")
    assert (o["situacao"], o["valor"], o["trecho"]) == ("incerto", "R$ 10", "a etapa seguinte falhou")
    assert RepositorioDeMemoria(db).entradas("ped1") == [], "fonte só se guarda quando o valor foi comprovado"
    conteudo = json.loads(db.one("SELECT conteudo FROM pedido_relatorios WHERE pedido_id='ped1'")["conteudo"])
    assert conteudo["observado"] == [] and conteudo["conclusao"]["situacao"] == "sem_conclusao"
    assert {"ocorrencia_falhou", "valor_incerto"} <= {i["tipo"] for i in conteudo["nao_coberto"]}


async def test_sem_saida_estruturada_grava_o_resultado_com_valor_ausente(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    [o] = RepositorioDeMemoria(db).todas_as_observacoes("ped1")
    assert (o["nome"], o["tipo"], o["situacao"], o["valor"], o["sha256"]) == ("resultado", "resultado", "ausente", None, None)
    assert o["trecho"] == "ocorrência concluida"
    conteudo = json.loads(db.one("SELECT conteudo FROM pedido_relatorios WHERE pedido_id='ped1'")["conteudo"])
    assert conteudo["observado"] == [] and "sem_valor" in {i["tipo"] for i in conteudo["nao_coberto"]}


async def test_execucao_ja_purgada_no_fechamento_ainda_registra_o_resultado(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    db.execute("DELETE FROM runs WHERE id=?", (run["id"],))
    laco.uma_volta()
    [oc] = _ocs(db)
    # 28.5: a execução que sumiu é efeito possível (não se sabe o que ela fez): fecha `incerta`, nunca `falhou`.
    assert oc["estado"] == "incerta" and "não existe mais" in oc["motivo"]
    [o] = RepositorioDeMemoria(db).todas_as_observacoes("ped1")
    assert (o["situacao"], o["valor"]) == ("ausente", None) and "não existe mais" in o["trecho"]


async def test_valor_com_formato_de_codigo_de_verificacao_e_recusado_e_nunca_gravado(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _leitura(db, run["id"], "assunto", "Seu código de verificação é 482913")
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    [o] = RepositorioDeMemoria(db).todas_as_observacoes("ped1")
    assert (o["situacao"], o["valor"], o["sha256"]) == ("ausente", None, None) and "482913" not in (o["trecho"] or "")
    assert "482913" not in json.dumps([dict(x) for x in db.query("SELECT * FROM pedido_relatorios")])


# ===================================================================== 3. o relatório de encerramento
async def test_encerrar_gera_o_relatorio_final_uma_vez_e_com_o_que_foi_observado(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _leitura(db, run["id"], "preco", "R$ 3.499,00")
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    assert db.one("SELECT estado FROM pedidos WHERE id='ped1'")["estado"] == "encerrado"
    [linha] = db.query("SELECT * FROM pedido_relatorios WHERE pedido_id='ped1'")
    assert (linha["gatilho"], linha["sequencia"], linha["gerado_por"], linha["pedido_versao"]) == (
        "encerramento", 1, "deterministico", 1)
    assert linha["resumo_texto"] is None and linha["custo_usd"] == 0
    conteudo = json.loads(linha["conteudo"])
    assert [x["valor"] for x in conteudo["observado"]] == ["R$ 3.499,00"]
    assert conteudo["conclusao"]["situacao"] == "sustentada" and conteudo["ocorrencias"]["por_estado"] == {"concluida": 1}
    from app.modules.pedidos.domain.relatorio import sha256_de
    assert linha["sha256"] == sha256_de(linha["conteudo"])
    laco.uma_volta()
    assert db.one("SELECT COUNT(*) AS n FROM pedido_relatorios")["n"] == 1, "voltas seguintes não geram outro"
    again = laco.relatorios.gerar("ped1", gatilho="encerramento")
    assert again["id"] == linha["id"], "gerar de novo devolve o de encerramento que já existe"


async def test_sob_demanda_acumula_sequencia_e_mesmas_entradas_dao_o_mesmo_conteudo(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _leitura(db, run["id"], "preco", "R$ 10")
    _assentar(db, run["id"], "completed")
    laco.uma_volta()                                                                    # fecha, encerra: relatório 1
    a = laco.relatorios.gerar("ped1", ate="2026-10-02T23:00:00Z")
    b = laco.relatorios.gerar("ped1", ate="2026-10-02T23:00:00Z")
    assert (a["sequencia"], b["sequencia"], a["gatilho"]) == (2, 3, "sob_demanda")
    assert a["conteudo"] == b["conteudo"] and a["sha256"] == b["sha256"]
    assert laco.relatorios.gerar("nao-existe") is None
    assert [x["sequencia"] for x in RepositorioDeMemoria(db).relatorios("ped1")] == [3, 2, 1]
    assert [x["sequencia"] for x in RepositorioDeMemoria(db).relatorios("ped1", limite=1, antes_de=3)] == [2]


async def test_cancelar_tambem_encerra_e_gera_o_relatorio_com_o_que_ainda_roda_em_aberto(h: Harness) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    _agora(db, "ped1", r)
    laco.uma_volta()                                                                    # despachada: a execução existe
    laco.acoes.cancelar("ped1")
    [linha] = db.query("SELECT * FROM pedido_relatorios WHERE pedido_id='ped1'")
    assert linha["gatilho"] == "encerramento"
    conteudo = json.loads(linha["conteudo"])
    assert conteudo["conclusao"]["situacao"] == "sem_conclusao"
    assert "ocorrencia_em_aberto" in {i["tipo"] for i in conteudo["nao_coberto"]}


async def test_pedido_que_nao_existe_ou_com_relatorio_quebrado_nao_impede_o_encerramento(h: Harness, monkeypatch) -> None:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r)
    monkeypatch.setattr(laco.relatorios, "preparar_relatorio", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quebrou")))
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _assentar(db, run["id"], "completed")
    assert laco.uma_volta().erros == 0
    assert db.one("SELECT estado FROM pedidos WHERE id='ped1'")["estado"] == "encerrado"
    assert db.one("SELECT COUNT(*) AS n FROM pedido_relatorios")["n"] == 0, "sem relatório; sai depois, sob demanda"


# ===================================================================== 4. resumo por IA opcional
class _Resumidor:
    def __init__(self, resposta: ResumoDeIA | Exception | None) -> None:
        self.chamadas = 0
        self.resposta = resposta

    def resumir(self, relatorio, *, teto_usd: float):
        self.chamadas += 1
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta


async def _encerrado_com(h: Harness, resumidor: _Resumidor | None, **cfg) -> dict:
    r, db = Relogio(), h.state.db
    laco = _laco_com(h, r, resumidor=resumidor, **cfg)
    _agora(db, "ped1", r)
    laco.uma_volta()
    [run] = _runs(db)
    _leitura(db, run["id"], "preco", "R$ 10")
    _assentar(db, run["id"], "completed")
    laco.uma_volta()
    [linha] = db.query("SELECT * FROM pedido_relatorios WHERE pedido_id='ped1'")
    return dict(linha)


async def test_resumo_ia_desligado_de_fabrica_nunca_chama_o_resumidor(h: Harness) -> None:
    quem = _Resumidor(ResumoDeIA("resumo pago", "plan", 0.02))
    linha = await _encerrado_com(h, quem)                                               # `resumo_ia` falso
    assert quem.chamadas == 0 and linha["resumo_texto"] is None and linha["custo_usd"] == 0
    assert PedidosCfg().resumo_ia is False and SemResumo().resumir({}, teto_usd=1.0) is None


async def test_resumo_ia_ligado_sem_resumidor_tambem_nao_chama_nada(h: Harness) -> None:
    linha = await _encerrado_com(h, None, resumo_ia=True)
    assert linha["resumo_texto"] is None and linha["gerado_por"] == "deterministico"


async def test_resumo_ia_ligado_vai_ao_lado_do_conteudo_e_nunca_no_lugar_dele(h: Harness) -> None:
    quem = _Resumidor(ResumoDeIA("  O preço ficou em R$ 10.  ", "plan", 0.02))
    com = await _encerrado_com(h, quem, resumo_ia=True, resumo_ia_teto_usd=0.1)
    assert quem.chamadas == 1 and com["resumo_texto"] == "O preço ficou em R$ 10." and com["resumo_por"] == "plan"
    assert com["custo_usd"] == pytest.approx(0.02) and com["gerado_por"] == "deterministico"
    assert json.loads(com["conteudo"])["conclusao"]["situacao"] == "sustentada", "o conteúdo é o determinístico"


async def test_resumo_que_falha_nao_derruba_o_relatorio_deterministico(h: Harness) -> None:
    quem = _Resumidor(RuntimeError("provedor fora"))
    linha = await _encerrado_com(h, quem, resumo_ia=True)
    assert quem.chamadas == 1 and linha["resumo_texto"] is None and json.loads(linha["conteudo"])["observado"]
