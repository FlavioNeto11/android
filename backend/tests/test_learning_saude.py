"""Saúde do item do Livro (30.4; proposta D-5 sobre o §5 de `docs/design/aprendizado-vivo.md`): dimensões medidas, UM
rótulo por regra e os motivos que o produziram (o fato e o limiar).

- domínio (`domain/saude.py`, puro): tabela de casos por rótulo, a ordem de avaliação, os limiares trocáveis e a regra
  "dimensão sem dado é `desconhecida`; incerteza nunca vira saudável";
- config: os defaults são os limiares medidos e o exemplo carrega no schema;
- HTTP: `saude` na lista e no detalhe vêm da MESMA função (o rótulo não diverge) e a memória não tem saúde.

Nível de prova: `simulated` (banco de teste, relógio fixo; nenhum aparelho, central ou IA).
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml
from fastapi import FastAPI

from app.config import LearningCfg
from app.db import Database
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.saude import (CodigoDoMotivo, Dimensao, LimiaresDeSaude, Rotulo, SinaisDeSaude,
                                               calcular)
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo, ajustes_do_config
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository

from .fake_skills import ValidadorFalso, perfil
from .fake_skills import banco as banco_migrado

S = SkillState
C = CodigoDoMotivo
AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
PACOTE = "com.instagram.android"


def _sinais(**kw: object) -> SinaisDeSaude:
    """Um item publicado saudável (20 usos, 95 %, usado ontem); cada caso troca o que o define."""
    base: dict[str, object] = {"kind": LivroKind.RECEITA, "estado": S.PUBLISHED, "agora": AGORA,
                               "criado_em": "2026-09-01T00:00:00Z", "ultimo_uso": "2026-10-01T00:00:00Z",
                               "usos": 20, "a_favor": 19, "contra": 1, "falhas_seguidas": 0,
                               "contestacoes_recentes": 0}
    base.update(kw)
    return SinaisDeSaude(**base)  # type: ignore[arg-type]


def _codigos(s: SinaisDeSaude, lim: LimiaresDeSaude = LimiaresDeSaude()) -> tuple[Rotulo, set[CodigoDoMotivo]]:
    saude = calcular(s, lim)
    assert saude is not None
    return saude.rotulo, {m.codigo for m in saude.motivos}


NUNCA = {"usos": 0, "a_favor": 0, "contra": 0, "ultimo_uso": None}

# ------------------------------------------------------------------ tabela de casos por rótulo
CASOS: list[tuple[str, SinaisDeSaude, Rotulo, set[CodigoDoMotivo]]] = [
    ("desligado", _sinais(estado=S.DISABLED, detalhe="quebrou"), Rotulo.INATIVO, {C.DESLIGADO}),
    ("aposentado", _sinais(estado=S.DEPRECATED), Rotulo.INATIVO, {C.APOSENTADO}),
    ("candidata", _sinais(estado=S.CANDIDATE, detalhe="sombra 0/2"), Rotulo.EM_PROVA, {C.AGUARDA_REPETICAO}),
    ("validada do dono", _sinais(estado=S.VALIDATED, exige_o_dono=True), Rotulo.EM_PROVA, {C.AGUARDA_O_DONO}),
    ("validada do sistema", _sinais(estado=S.VALIDATED), Rotulo.EM_PROVA, {C.VALIDADO_AGUARDA_PUBLICACAO}),
    ("2 falhas seguidas", _sinais(falhas_seguidas=2), Rotulo.DEGRADANDO, {C.FALHAS_SEGUIDAS}),
    ("eficácia < 80 % com amostra", _sinais(usos=10, a_favor=7, contra=3), Rotulo.DEGRADANDO,
     {C.EFICACIA_ABAIXO_DO_MINIMO}),
    ("contestada em 7 dias", _sinais(contestacoes_recentes=1), Rotulo.DEGRADANDO, {C.CONTESTADO_RECENTEMENTE}),
    ("publicada há 14 dias e nunca usada", _sinais(**NUNCA, estado_desde="2026-09-18T12:00:00Z"),
     Rotulo.SEM_EVIDENCIA, {C.NUNCA_USADO}),
    ("usada, sem uso há 15 dias", _sinais(ultimo_uso="2026-09-17T00:00:00Z"), Rotulo.PARADO, {C.SEM_USO_RECENTE}),
    ("usada, 4 usos", _sinais(usos=4, a_favor=4, contra=0), Rotulo.POUCA_AMOSTRA, {C.AMOSTRA_PEQUENA}),
    ("publicada há 3 dias e ainda sem uso", _sinais(**NUNCA, estado_desde="2026-09-29T12:00:00Z"),
     Rotulo.POUCA_AMOSTRA, {C.AMOSTRA_PEQUENA}),
    ("saudável", _sinais(), Rotulo.SAUDAVEL, {C.USADO_RECENTEMENTE, C.AMOSTRA_SUFICIENTE,
                                              C.EFICACIA_ACIMA_DO_MINIMO, C.SEM_CONTESTACAO_RECENTE}),
]


@pytest.mark.parametrize(("nome", "sinais", "rotulo", "motivos"), CASOS, ids=[c[0] for c in CASOS])
def test_cada_rotulo_nasce_da_sua_regra_com_os_motivos(nome: str, sinais: SinaisDeSaude, rotulo: Rotulo,
                                                       motivos: set[CodigoDoMotivo]) -> None:
    assert _codigos(sinais) == (rotulo, motivos), nome


def test_os_motivos_dizem_o_fato_e_o_limiar() -> None:
    saude = calcular(_sinais(usos=10, a_favor=7, contra=3, falhas_seguidas=3, contestacoes_recentes=2))
    assert saude is not None and saude.rotulo is Rotulo.DEGRADANDO
    por_codigo = {m.codigo: m for m in saude.motivos}
    assert [(m.valor, m.limite) for m in (por_codigo[C.FALHAS_SEGUIDAS], por_codigo[C.EFICACIA_ABAIXO_DO_MINIMO],
                                           por_codigo[C.CONTESTADO_RECENTEMENTE])] == [(3, 2), (0.7, 0.8), (2, 1)]
    assert por_codigo[C.EFICACIA_ABAIXO_DO_MINIMO].dimensao is Dimensao.EFICACIA
    parado = calcular(_sinais(ultimo_uso="2026-09-01T00:00:00Z"))
    assert parado is not None and (parado.motivos[0].valor, parado.motivos[0].limite) == (31, 14)
    inativo = calcular(_sinais(estado=S.DISABLED, detalhe="a pessoa desligou"))
    assert inativo is not None and inativo.motivos[0].detalhe == "a pessoa desligou"


# ------------------------------------------------------------------ a ordem das regras
def test_ordem_de_avaliacao() -> None:
    # inativo vence tudo; em prova vence a contestação; degradando vence "nunca usado" e "parado"
    assert _codigos(_sinais(estado=S.DISABLED, contestacoes_recentes=5, falhas_seguidas=3))[0] is Rotulo.INATIVO
    assert _codigos(_sinais(estado=S.CANDIDATE, contestacoes_recentes=5))[0] is Rotulo.EM_PROVA
    antigo = {"estado_desde": "2026-09-01T00:00:00Z"}
    assert _codigos(_sinais(**NUNCA, **antigo, contestacoes_recentes=1))[0] is Rotulo.DEGRADANDO
    assert _codigos(_sinais(ultimo_uso="2026-09-01T00:00:00Z", falhas_seguidas=2))[0] is Rotulo.DEGRADANDO


def test_fronteiras_dos_limiares() -> None:
    assert _codigos(_sinais(falhas_seguidas=1))[0] is Rotulo.SAUDAVEL                            # 1 falha não degrada
    assert _codigos(_sinais(usos=10, a_favor=8, contra=2))[0] is Rotulo.SAUDAVEL                 # 80 % passa (≥ mínimo)
    assert _codigos(_sinais(usos=4, a_favor=1, contra=3))[0] is Rotulo.POUCA_AMOSTRA            # sem amostra não condena
    assert _codigos(_sinais(usos=5, a_favor=5, contra=0))[0] is Rotulo.SAUDAVEL                  # 5 usos já valem
    assert _codigos(_sinais(ultimo_uso="2026-09-18T12:00:00Z"))[0] is Rotulo.SAUDAVEL            # 14 dias: ainda dentro
    assert _codigos(_sinais(ultimo_uso="2026-09-17T12:00:00Z"))[0] is Rotulo.PARADO              # 15 dias: parado
    assert _codigos(_sinais(**NUNCA, estado_desde="2026-09-19T12:00:00Z"))[0] is Rotulo.POUCA_AMOSTRA  # 13 dias
    assert _codigos(_sinais(**NUNCA, estado_desde="2026-09-18T12:00:00Z"))[0] is Rotulo.SEM_EVIDENCIA  # 14 dias


def test_os_limiares_sao_do_config() -> None:
    sinais = _sinais(usos=10, a_favor=7, contra=3)                              # 70 %
    assert _codigos(sinais)[0] is Rotulo.DEGRADANDO
    assert _codigos(sinais, LimiaresDeSaude(taxa_minima=0.7))[0] is Rotulo.SAUDAVEL
    assert _codigos(_sinais(falhas_seguidas=2), LimiaresDeSaude(falhas_seguidas=3))[0] is Rotulo.SAUDAVEL
    assert _codigos(_sinais(usos=4, a_favor=4, contra=0), LimiaresDeSaude(amostra_minima=4))[0] is Rotulo.SAUDAVEL
    parado = _sinais(ultimo_uso="2026-09-17T12:00:00Z")
    assert _codigos(parado, LimiaresDeSaude(sem_uso_dias=15))[0] is Rotulo.SAUDAVEL


# ------------------------------------------------------------------ dimensão sem dado é desconhecida
def test_sem_dado_a_dimensao_e_desconhecida_e_o_rotulo_nunca_e_saudavel() -> None:
    nada = SinaisDeSaude(kind=LivroKind.HABILIDADE, estado=S.PUBLISHED, agora=AGORA, criado_em="2026-09-01T00:00:00Z")
    saude = calcular(nada)
    assert saude is not None and saude.rotulo is Rotulo.INDETERMINADO
    assert [m.codigo for m in saude.motivos] == [C.USO_DESCONHECIDO]
    por_nome = {d.nome: d for d in saude.dimensoes}
    for nome in (Dimensao.USO, Dimensao.EFICACIA, Dimensao.BASE_DE_EVIDENCIA, Dimensao.VERSAO, Dimensao.CONTESTACAO,
                 Dimensao.INTERVENCAO_HUMANA):
        assert por_nome[nome].desconhecida and por_nome[nome].valor is None, nome   # nunca 0
    assert por_nome[Dimensao.FRESCOR].valor == 31                                  # medido: desde a criação


def test_incerteza_nunca_vira_saudavel() -> None:
    # usada e com amostra, mas 0 acertos + 0 falhas na evidência: a taxa é desconhecida (não 1, não 0)
    rotulo, motivos = _codigos(_sinais(a_favor=0, contra=0))
    assert rotulo is Rotulo.INDETERMINADO and motivos == {C.EFICACIA_DESCONHECIDA}
    # contestação não medida (None) também impede o "saudável"
    rotulo, motivos = _codigos(_sinais(contestacoes_recentes=None))
    assert rotulo is Rotulo.INDETERMINADO and motivos == {C.CONTESTACAO_DESCONHECIDA}
    # usada, mas sem a data do último uso; e nunca usada sem data de criação
    assert _codigos(_sinais(ultimo_uso=None))[1] == {C.USO_DESCONHECIDO}
    assert _codigos(_sinais(**NUNCA, criado_em=None))[1] == {C.IDADE_DESCONHECIDA}
    # falha seguida desconhecida (a fonte não a expõe) não degrada nem salva: segue pelas outras medidas
    assert _codigos(_sinais(falhas_seguidas=None))[0] is Rotulo.SAUDAVEL


def test_memoria_nao_tem_saude() -> None:
    assert calcular(SinaisDeSaude(kind=LivroKind.MEMORIA, estado=S.PUBLISHED, agora=AGORA)) is None


def test_item_sem_estado_conhecido_e_indeterminado() -> None:
    saude = calcular(_sinais(estado=None))
    assert saude is not None and saude.rotulo is Rotulo.INDETERMINADO
    assert [m.codigo for m in saude.motivos] == [C.ESTADO_DESCONHECIDO]


# ------------------------------------------------------------------ config
def test_defaults_do_config_sao_os_limiares_medidos() -> None:
    assert ajustes_do_config(LearningCfg()).saude == LimiaresDeSaude(14, 5, 0.8, 2, 7)
    raiz = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"
    exemplo = yaml.safe_load(raiz.read_text(encoding="utf-8"))["aprendizado"]
    assert ajustes_do_config(LearningCfg(**exemplo)).saude == LimiaresDeSaude()
    troca = ajustes_do_config(LearningCfg(saude={"amostra_minima": 8, "taxa_minima": 0.9}))   # type: ignore[arg-type]
    assert (troca.saude.amostra_minima, troca.saude.taxa_minima) == (8, 0.9)


# ------------------------------------------------------------------ HTTP: lista e detalhe pela mesma função
def _receita(db: Database, passo: str, *, status: str = "active", ok: int = 0, falhas: int = 0, seguidas: int = 0,
             ultimo_uso: str | None = None) -> int:
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": "botao"}], "commit": False}]
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at, replay_ok, replay_fail, consecutive_fail, last_used_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (PACOTE, "447", "sig", "pt/420", f"h-{passo}", passo, 1, status, json.dumps(acoes), f"r1:a:v1:{passo}",
         "2026-09-01T00:00:00Z", ok, falhas, seguidas, ultimo_uso)))


def _contra(db: Database, ref: str, quando: str, origem: str) -> None:
    db.execute("INSERT INTO learning_evidence(item_ref, stance, origin_ref, simulated, observed_at)"
               " VALUES (?,?,?,0,?)", (ref, "against", origem, quando))


class _FontesComFalhas(FontesSql):
    """Faz o que `fontes.py` passa a fazer quando o coordenador integrar o 30.6: preencher `falhas_seguidas` da receita
    com `recipes.consecutive_fail`. O teste prova que o serviço honra o campo de `EntradaDoLivro`."""

    def _com(self, e: EntradaDoLivro) -> EntradaDoLivro:
        linha = self._db.one("SELECT consecutive_fail FROM recipes WHERE id=?", (int(e.ref),))
        return replace(e, falhas_seguidas=int(linha["consecutive_fail"]) if linha else None)

    def receitas(self) -> list[EntradaDoLivro]:
        return [self._com(e) for e in super().receitas()]

    def receita(self, ref: str) -> EntradaDoLivro | None:
        e = super().receita(ref)
        return None if e is None else self._com(e)


class Mundo:
    def __init__(self, db: Database) -> None:
        self.db = db
        db.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
        usada = "2026-10-01T00:00:00Z"
        self.boa = _receita(db, "abrir", ok=19, falhas=1, ultimo_uso=usada)
        self.nunca = _receita(db, "rolar")                                    # publicada, sem uso, há 31 dias
        self.contestada = _receita(db, "curtir", ok=19, falhas=1, ultimo_uso=usada)
        self.antiga = _receita(db, "voltar", ok=19, falhas=1, ultimo_uso=usada)
        self.candidata = _receita(db, "buscar", status="candidate")
        self.esquecida = _receita(db, "salvar", ok=10, ultimo_uso="2026-09-01T00:00:00Z")
        self.pouca = _receita(db, "seguir", ok=2, ultimo_uso=usada)
        self.fraca = _receita(db, "enviar", ok=6, falhas=4, ultimo_uso=usada)
        self.quebrando = _receita(db, "postar", ok=10, seguidas=2, ultimo_uso=usada)
        _contra(db, f"receita:{self.contestada}", "2026-09-30T12:00:00Z", "run:r-recente")
        _contra(db, f"receita:{self.antiga}", "2026-09-01T12:00:00Z", "run:r-antiga")     # fora da janela de 7 dias
        perfil(db, "p1")
        db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, created_at,"
                   " updated_at) VALUES ('m0','p1','@ana','x','operator','fp0',?,?)",
                   ("2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"))
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.servico = LearningService(repo, _FontesComFalhas(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14)


@pytest.fixture
def mundo(tmp_path: Path) -> Mundo:
    db = banco_migrado(tmp_path, "saude.sqlite3")
    yield Mundo(db)
    db.close()


@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.servico)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_lista_e_detalhe_trazem_a_mesma_saude(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    lista = (await cliente.get("/api/aprendizado", params={"kind": "receita"})).json()["itens"]
    rotulo = {i["ref"]: i["saude"]["rotulo"] for i in lista}
    assert rotulo == {
        str(mundo.boa): "saudavel", str(mundo.nunca): "sem_evidencia", str(mundo.contestada): "degradando",
        str(mundo.antiga): "saudavel", str(mundo.candidata): "em_prova", str(mundo.esquecida): "parado",
        str(mundo.pouca): "pouca_amostra", str(mundo.fraca): "degradando", str(mundo.quebrando): "degradando"}
    for i in lista:                                                   # o detalhe diz o mesmo, com os mesmos motivos
        det = (await cliente.get(f"/api/aprendizado/receita/{i['ref']}")).json()["item"]["saude"]
        assert det["rotulo"] == i["saude"]["rotulo"], i["ref"]
        assert [m["codigo"] for m in det["motivos"]] == [m["codigo"] for m in i["saude"]["motivos"]]
        assert det["dimensoes"] == i["saude"]["dimensoes"]
    por_ref = {i["ref"]: i["saude"] for i in lista}
    assert [m["codigo"] for m in por_ref[str(mundo.quebrando)]["motivos"]] == ["falhas_seguidas"]
    assert [m["codigo"] for m in por_ref[str(mundo.fraca)]["motivos"]] == ["eficacia_abaixo_do_minimo"]


async def test_forma_do_json_da_saude(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    s = (await cliente.get(f"/api/aprendizado/receita/{mundo.contestada}")).json()["item"]["saude"]
    assert s["rotulo"] == "degradando"
    assert s["motivos"] == [{"codigo": "contestado_recentemente", "dimensao": "contestacao", "valor": 1, "limite": 1,
                             "detalhe": "últimos 7 dias"}]
    dims = {d["nome"]: d for d in s["dimensoes"]}
    assert list(dims) == ["uso", "eficacia", "base_de_evidencia", "frescor", "versao", "contestacao",
                          "intervencao_humana"]
    assert dims["eficacia"] == {"nome": "eficacia", "estado": "medida", "valor": 0.95, "amostra": 20,
                                "fonte": "recipes.replay_ok+replay_fail"}
    assert dims["uso"]["valor"] == 20 and dims["frescor"]["valor"] == 1
    assert dims["versao"] == {"nome": "versao", "estado": "desconhecida", "valor": None, "amostra": None,
                              "fonte": "versão do app (§7)"}
    assert dims["intervencao_humana"]["estado"] == "desconhecida"


async def test_memoria_tem_saude_nula_e_o_resto_da_lista_traz(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    assert (await cliente.get("/api/aprendizado/memoria/p1")).json()["item"]["saude"] is None
    lista = (await cliente.get("/api/aprendizado")).json()["itens"]
    assert next(i for i in lista if i["kind"] == "memoria")["saude"] is None
    assert all(i["saude"] is not None for i in lista if i["kind"] != "memoria")


async def test_item_desligado_leva_o_motivo_da_trilha_no_detalhe(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    r = await cliente.post(f"/api/aprendizado/receita/{mundo.boa}/status", json={"to": "disabled", "reason": "quebrou"})
    assert r.status_code == 200
    s = r.json()["item"]["saude"]
    assert s["rotulo"] == "inativo" and s["motivos"] == [{"codigo": "desligado", "dimensao": None, "valor": None,
                                                          "limite": None, "detalhe": "quebrou"}]
