"""Item 28.10, F4 — o pai consolida as observações e a memória dos filhos (`docs/design/pedidos-persistentes.md` §9).

Prova `simulated` (`arquivo::teste`): domínio puro, serviço de relatórios sobre um banco real de teste (SQLite, ou PG com
`TEST_DATABASE_URL`) e a mensagem do canal. Cobre: o pai lê só observação comprovada e memória-fato (nunca o texto livre
da execução nem a incerta); conflito mostra as duas fontes e não tem vencedor; valor igual agrupa com a contagem; filho sem
dado ou em andamento é marcado; o aviso do canal leva só contagens; sem filhos ou com a colaboração desligada o relatório é
BYTE A BYTE o de hoje.

NÃO prova: o laço real, o canal real nem um pedido pai de teste no app de teste (`not_run`, depois do deploy).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.db import Database
from app.modules.avisos.domain.mensagem import aviso_de_evento
from app.modules.pedidos.domain import consolidacao as cons
from app.modules.pedidos.domain import relatorio as rel
from app.modules.pedidos.domain.relatorio import EntradaDoRelatorio, ObservacaoVista, OcorrenciaVista
from app.modules.pedidos.infrastructure.relatorios import ServicoDeRelatorios
from app.modules.pedidos.infrastructure.repositorio_memoria import NovaObservacao, RepositorioDeMemoria
from app.modules.pedidos.infrastructure.servico import _resumo_da_consolidacao

from .test_pedidos_modelo import _banco, _ocorrencia, _pedido, _run

ATE = "2026-10-02T23:59:59Z"
T0 = "2026-10-02T10:00:05.000Z"


def _oc(oid: str, estado: str = "concluida") -> OcorrenciaVista:
    return OcorrenciaVista(oid, "2026-10-02T10:00:00Z", estado)


def _ob(oid: str, ocorrencia: str, valor: str | None, *, nome: str = "preco", alvo: str = "android-01",
        situacao: str = "observado", em: str = T0) -> ObservacaoVista:
    return ObservacaoVista(oid, ocorrencia, alvo, nome, situacao, valor, capturado_em=em)


def _filho(fid: str, papel: str | None = "pesquisador", estado: str = "encerrado", obs: tuple = (), ocs: tuple = (),
           memoria: tuple = ()) -> cons.FilhoVisto:
    return cons.FilhoVisto(fid, papel, estado, ocs or (_oc(f"o-{fid}"),), obs, memoria)


def _entrada(filhos: tuple = (), **kw) -> EntradaDoRelatorio:
    return EntradaDoRelatorio(pedido_id="pai", pedido_versao=1, periodo_ate=ATE, filhos=filhos, **kw)


# =============================================================================================== domínio
def test_so_a_observacao_comprovada_e_a_memoria_fato_entram_o_resto_fica_de_fora() -> None:
    f = _filho("f1", obs=(
        _ob("b1", "o-f1", "R$ 10"),                                    # entra
        _ob("b2", "o-f1", "R$ 99", nome="incerto", situacao="incerto"),
        _ob("b3", "o-f1", None, nome="ausente", situacao="ausente"),
        _ob("b4", "o-ruim", "R$ 77", nome="de-falha")),                # a ocorrência dela falhou
        ocs=(_oc("o-f1"), _oc("o-ruim", "falhou")),
        memoria=(cons.EntradaDeMemoria("achado", "descoberta", "o site pede cadastro"),
                 cons.EntradaDeMemoria("etapa", "progresso", "ia na 3"),
                 cons.EntradaDeMemoria("falta", "pendencia", "confirmar o CEP"),
                 cons.EntradaDeMemoria("velha", "decisao", "usar o Chrome", resolvida=True)))
    b = cons.consolidar([f])
    assert {(v["origem"], v["nome"]) for v in b["valores"]} == {("observacao", "preco"), ("memoria", "achado")}
    assert b["conflitos"] == [] and b["fontes"][0]["observacoes"] == 1 and b["fontes"][0]["memoria"] == 1
    assert "R$ 99" not in json.dumps(b) and "R$ 77" not in json.dumps(b), "incerto e falha nunca viram valor"


def test_a_leitura_do_filho_so_toca_observacao_e_memoria_nao_o_texto_livre() -> None:
    campos = {c for c in cons.FilhoVisto.__dataclass_fields__}
    assert campos == {"id", "papel", "estado", "ocorrencias", "observacoes", "memoria"}, "sem título, comando nem execução"
    f = _filho("f1", obs=(_ob("b1", "o-f1", "R$ 10"),))
    b = cons.consolidar([f])
    assert set(b["fontes"][0]) == {"filho_id", "papel", "estado", "situacao", "em_andamento", "observacoes", "memoria"}


def test_conflito_mostra_as_duas_fontes_sem_vencedor_nem_voto() -> None:
    a = _filho("fa", "pesquisador", obs=(_ob("b1", "o-fa", "R$ 10"),))
    b = _filho("fb", "checador", obs=(_ob("b2", "o-fb", "R$ 12"),))
    c = _filho("fc", "checador", obs=(_ob("b3", "o-fc", "R$ 12"),))              # 2 contra 1 NÃO decide
    r = cons.consolidar([c, a, b])
    assert r["valores"] == [] and len(r["conflitos"]) == 1
    conf = r["conflitos"][0]
    assert (conf["origem"], conf["alvo"], conf["nome"]) == ("observacao", "android-01", "preco")
    assert [(v["valor"], v["n_fontes"]) for v in conf["versoes"]] == [("R$ 10", 1), ("R$ 12", 2)]
    assert conf["versoes"][0]["fontes"] == [{"filho_id": "fa", "papel": "pesquisador"}]
    assert conf["versoes"][1]["fontes"] == [{"filho_id": "fb", "papel": "checador"}, {"filho_id": "fc", "papel": "checador"}]
    assert "vencedor" not in conf and "escolhido" not in conf
    assert r["resumo"]["conflitos"] == 1 and r["resumo"]["valores"] == 0


def test_valor_igual_de_varios_filhos_aparece_uma_vez_com_a_contagem() -> None:
    fs = [_filho(f"f{i}", obs=(_ob(f"b{i}", f"o-f{i}", "R$  10"),)) for i in range(3)]
    fs[1] = _filho("f1", obs=(_ob("b1", "o-f1", "R$ 10"),))                      # espaço a mais não diverge
    r = cons.consolidar(fs)
    assert r["conflitos"] == [] and len(r["valores"]) == 1
    assert r["valores"][0]["n_fontes"] == 3 and len(r["valores"][0]["fontes"]) == 3


def test_o_valor_mais_recente_do_proprio_filho_e_o_que_vale() -> None:
    f = _filho("f1", ocs=(_oc("o1"), _oc("o2")), obs=(_ob("b1", "o1", "R$ 10", em="2026-10-02T10:00:05.000Z"),
                                                      _ob("b2", "o2", "R$ 11", em="2026-10-02T11:00:05.000Z")))
    r = cons.consolidar([f, _filho("f2", obs=(_ob("b3", "o-f2", "R$ 11"),))])
    assert r["conflitos"] == [] and r["valores"][0]["valor"] == "R$ 11" and r["valores"][0]["n_fontes"] == 2


def test_memoria_igual_agrupa_e_diferente_conflita_pela_chave() -> None:
    m = lambda v: (cons.EntradaDeMemoria("preco-base", "descoberta", v),)        # noqa: E731
    r = cons.consolidar([_filho("a", memoria=m("10")), _filho("b", memoria=m("10")), _filho("c", memoria=m("12"))])
    assert r["valores"] == [] and r["conflitos"][0]["origem"] == "memoria" and r["conflitos"][0]["nome"] == "preco-base"


def test_filho_sem_dado_ou_em_andamento_e_marcado_sem_inventar() -> None:
    sem = _filho("fs", estado="encerrado")
    andando = _filho("fa", estado="ativo", ocs=(_oc("o1", "rodando"),), obs=(_ob("b1", "o-x", "R$ 10"),))
    r = cons.consolidar([sem, andando])
    por = {s["filho_id"]: s for s in r["fontes"]}
    assert (por["fs"]["situacao"], por["fs"]["em_andamento"]) == ("sem_dado", False)
    assert por["fa"]["em_andamento"] is True and por["fa"]["situacao"] == "sem_dado", "a ocorrência dele ainda roda"
    assert r["valores"] == [] and r["resumo"] == {"filhos": 2, "com_dado": 0, "sem_dado": 2, "em_andamento": 1,
                                                    "valores": 0, "conflitos": 0}
    nc = cons.itens_nao_cobertos(r)
    assert sorted(i["tipo"] for i in nc) == ["filho_em_andamento", "filho_sem_dado", "filho_sem_dado"]


def test_a_consolidacao_e_deterministica_em_qualquer_ordem() -> None:
    fs = [_filho(f"f{i}", obs=(_ob(f"b{i}", f"o-f{i}", f"R$ {i % 2}"),)) for i in range(4)]
    base = json.dumps(cons.consolidar(fs), sort_keys=True)
    assert json.dumps(cons.consolidar(fs[::-1]), sort_keys=True) == base


# =============================================================================================== no relatório
def test_o_relatorio_sem_filhos_e_identico_ao_de_hoje() -> None:
    ocs, obs = [_oc("o1")], [_ob("b1", "o1", "R$ 10")]
    antes = rel.serializar(rel.montar(EntradaDoRelatorio(pedido_id="pai", pedido_versao=1, periodo_ate=ATE,
                                                         ocorrencias=ocs, observacoes=obs)))
    depois = rel.montar(_entrada((), ocorrencias=ocs, observacoes=obs))
    assert "consolidacao" not in depois and rel.serializar(depois) == antes


def test_o_relatorio_do_pai_leva_o_bloco_e_o_conflito_rebaixa_a_conclusao() -> None:
    fs = (_filho("fa", obs=(_ob("b1", "o-fa", "R$ 10"),)), _filho("fb", obs=(_ob("b2", "o-fb", "R$ 12"),)))
    r = rel.montar(_entrada(fs, ocorrencias=[_oc("o1")], observacoes=[_ob("b0", "o1", "R$ 10")]))
    assert set(r["consolidacao"]) == {"fontes", "valores", "conflitos", "omitidos", "resumo"}
    assert "conflito_entre_filhos" in [i["tipo"] for i in r["nao_coberto"]]
    assert r["conclusao"]["situacao"] == "parcial", "conflito não conta como sucesso"
    assert rel.serializar(r) == rel.serializar(rel.montar(_entrada(fs[::-1], ocorrencias=[_oc("o1")],
                                                                    observacoes=[_ob("b0", "o1", "R$ 10")])))


def test_leitura_dos_filhos_que_falhou_aparece_no_nao_coberto() -> None:
    r = rel.montar(_entrada((), consolidacao_falhou=True))
    assert "consolidacao" not in r and "consolidacao_indisponivel" in [i["tipo"] for i in r["nao_coberto"]]


# =============================================================================================== o serviço, com banco
@pytest.fixture()
def banco(tmp_path: Path) -> Database:
    db = _banco(tmp_path)
    db.migrate()
    _run(db, "r1")
    _pedido(db, "pai", estado="ativo")
    _pedido(db, "fa", estado="encerrado", pai_id="pai", papel="pesquisador", titulo="Maria olha o preço")
    _pedido(db, "fb", estado="ativo", pai_id="pai", papel="checador", titulo="João confere")
    _pedido(db, "fc", estado="encerrado", pai_id="pai", papel="redator")
    for fid in ("fa", "fb", "fc"):
        _ocorrencia(db, f"o-{fid}", fid, None, "2026-10-02T12:00:00Z", f"ped:{fid}:-:2026-10-02T12:00:00Z",
                    estado="concluida")
    yield db
    db.close()


def _grava(db: Database, fid: str, valor: str, *, nome: str = "preco") -> None:
    RepositorioDeMemoria(db).inserir_observacoes([NovaObservacao(
        pedido_id=fid, pedido_versao=1, ocorrencia_id=f"o-{fid}", run_id="r1", step_id="s1", alvo="android-01", nome=nome,
        tipo="text", situacao="observado", valor=valor, fonte="Chrome / ler o preço", trecho=None, sha256="x",
        capturado_em=T0)])


def _relatorio(db: Database, ligada: bool) -> dict:
    servico = ServicoDeRelatorios(db, db.agora, colaboracao=lambda: ligada)
    linha = servico.gerar("pai", gatilho="encerramento")
    assert linha is not None
    return json.loads(linha["conteudo"])


def test_o_pai_le_observacao_e_memoria_dos_filhos_e_o_conflito_vai_ao_relatorio(banco: Database) -> None:
    _grava(banco, "fa", "R$ 10")
    _grava(banco, "fb", "R$ 12")
    # texto livre da execução: a coluna `trecho` e o título do filho NÃO podem aparecer no bloco
    banco.execute("UPDATE pedido_observacoes SET trecho='texto livre da execução' WHERE pedido_id='fa'")
    ServicoDeRelatorios(banco, banco.agora).gravar_memoria("fc", "achado", "descoberta", "o site pede cadastro")
    r = _relatorio(banco, True)
    c = r["consolidacao"]
    assert [s["filho_id"] for s in c["fontes"]] == ["fa", "fb", "fc"]
    por = {s["filho_id"]: s for s in c["fontes"]}
    assert por["fb"]["em_andamento"] is True and por["fa"]["em_andamento"] is False
    assert [v["nome"] for v in c["valores"]] == ["achado"] and c["valores"][0]["fontes"] == [
        {"filho_id": "fc", "papel": "redator"}]
    [conf] = c["conflitos"]
    assert sorted(f["filho_id"] for v in conf["versoes"] for f in v["fontes"]) == ["fa", "fb"]
    texto = json.dumps(c, ensure_ascii=False)
    assert "Maria" not in texto and "João" not in texto and "texto livre" not in texto
    assert r["conclusao"]["situacao"] == "sem_conclusao"                 # o pai não observou nada: nada inventado


def test_desligada_o_relatorio_nem_consulta_os_filhos(banco: Database) -> None:
    _grava(banco, "fa", "R$ 10")
    desligada = _relatorio(banco, False)
    assert "consolidacao" not in desligada
    banco.execute("DELETE FROM pedido_relatorios")
    sem_servico = json.loads(ServicoDeRelatorios(banco, banco.agora).gerar("pai", gatilho="encerramento")["conteudo"])
    assert sem_servico == desligada, "sem o parâmetro (como antes da F4) o relatório é o mesmo"


def test_pai_sem_filhos_ligada_ou_nao_gera_o_mesmo_relatorio(banco: Database) -> None:
    banco.execute("UPDATE pedidos SET pai_id=NULL WHERE pai_id='pai'")
    ligada = _relatorio(banco, True)
    banco.execute("DELETE FROM pedido_relatorios")
    assert ligada == _relatorio(banco, False) and "consolidacao" not in ligada


def test_falha_na_leitura_dos_filhos_nao_derruba_o_relatorio(banco: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    def quebra(self, pedido_id):                      # noqa: ANN001
        raise RuntimeError("banco indisponível")
    monkeypatch.setattr(RepositorioDeMemoria, "filhos_do_pai", quebra)
    r = _relatorio(banco, True)
    assert "consolidacao" not in r and "consolidacao_indisponivel" in [i["tipo"] for i in r["nao_coberto"]]


# =============================================================================================== o canal
def test_o_aviso_do_canal_leva_so_contagens() -> None:
    resumo = _resumo_da_consolidacao(json.dumps({"consolidacao": {"resumo": {"filhos": 3, "conflitos": 2, "valores": 1}}}))
    assert resumo == {"filhos": 3, "conflitos": 2}
    assert _resumo_da_consolidacao("{}") is None and _resumo_da_consolidacao("não é json") is None
    evento = {"aviso": {"id": "avs_1", "tipo": "relatorio_pronto", "requer_pessoa": False,
                        "mensagem": "O relatório 1 do pedido 'Maria olha o preço' está pronto. Consolidou 3 filho(s); 2 conflito(s).",
                        "dados": {"relatorio_id": "rel_1", "filhos_lidos": 3, "conflitos": 2}}}
    a = aviso_de_evento("pedido.aviso", evento, 7)
    assert a is not None and a.titulo.endswith("relatório pronto: 2 conflito(s) entre os filhos")
    saiu = f"{a.titulo} {a.corpo}"
    assert "Maria" not in saiu and "R$" not in saiu and "android" not in saiu


def test_aviso_sem_conflito_ou_de_pedido_comum_sai_como_hoje() -> None:
    for dados in ({"relatorio_id": "rel_1"}, {"relatorio_id": "rel_1", "conflitos": 0}):
        evento = {"aviso": {"id": "avs_1", "tipo": "relatorio_pronto", "requer_pessoa": False, "dados": dados}}
        a = aviso_de_evento("pedido.aviso", evento, 7)
        assert a is not None and a.corpo == "Nada a fazer." and "conflito" not in a.titulo
