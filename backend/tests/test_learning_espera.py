"""30.21: o evento `learning.needs_person` ("conhecimento aguardando a pessoa", `aprendizado-vivo.md` §8.11).

Tudo `simulated`: barramento falso (e, uma vez, o `EventBus` de verdade sobre SQLite, só para provar que o tipo é
persistido). Nenhum aparelho, nenhuma conta, nenhuma IA.
"""
from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.db import Database
from app.events import EPHEMERAL_KINDS, EventBus
from app.modules.applications.infrastructure import registry
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, SkillState
from app.modules.learning.domain.espera import (CAMPOS_DO_PAYLOAD, AvisoDeEspera, Faixa, FatosDoCatalogo,
                                                MotivoDeEntrada, MotivoDeSaida, classificar_espera, href_do_item,
                                                motivo_de_saida)
from app.modules.learning.domain.livro import Escopo, NovoItem
from app.modules.learning.domain.vocabulario import LivroKind, SourceKind
from app.modules.learning.infrastructure.eventos import TIPO_DO_EVENTO, EventosNoBarramento, RiscoDoRegistro
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.ligar_nativos import OuvinteD1DasReceitas, TrilhaDasLojas
from app.modules.learning.infrastructure.montagem import GuardaDoFluxo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.taskqueue.recipes import MudancaDaReceita

from .fake_skills import TS, ValidadorFalso
from .fake_skills import banco as banco_migrado

S = SkillState
PACOTE = "com.instagram.android"
AGORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
PESSOA = "painel"
TEXTO_DA_PESSOA = "texto unico escrito pela pessoa sobre o botao"        # NUNCA pode aparecer num evento


@dataclass
class BarramentoFalso:
    emitidos: list[tuple[str, str, str, dict[str, object]]] = field(default_factory=list)

    def emit(self, kind: str, message: str, *, level: str = "info", instance_id: str | None = None,
             data: dict[str, object] | None = None) -> object:
        self.emitidos.append((kind, message, level, dict(data or {})))
        return None

    @property
    def dados(self) -> list[dict[str, object]]:
        return [d for _, _, _, d in self.emitidos]


class CatalogoFalso:
    """`CatalogoDeRisco`: o app tem catálogo e cada capability tem os fatos dados."""

    def __init__(self, fatos: dict[str, FatosDoCatalogo] | None = None, *, tem: bool = True) -> None:
        self._fatos = fatos or {}
        self._tem = tem

    def tem_catalogo(self, app: str) -> bool:
        return self._tem

    def da_capability(self, app: str, capability: str) -> FatosDoCatalogo | None:
        return self._fatos.get(capability)


def _receita(db: Database, *, status: str, commit: bool, passo: str) -> int:
    acoes = [{"tool": "tap", "args": {}, "selectors": [{"rid": "botao"}], "commit": commit, "why": "a IA explicou"}]
    return int(db.inserted_id(
        "INSERT INTO recipes(app_package, app_version, app_signature, variant, step_hash, step_key, version, status,"
        " actions, learned_from_step, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (PACOTE, "447", "sig", "pt/420", f"h-{passo}", passo, 1, status, json.dumps(acoes), f"r1:a:v1:{passo}", TS)))


class Mundo:
    def __init__(self, db: Database, catalogo: CatalogoFalso | None = None) -> None:
        self.db = db
        self.barramento = BarramentoFalso()
        habilidades = SqlSkillRepository(db, ValidadorFalso())
        self.repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
        self.servico = LearningService(self.repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes,
                                       relogio=lambda: AGORA, retencao_de_logs_dias=lambda: 14,
                                       eventos=EventosNoBarramento(self.barramento), catalogo_de_risco=catalogo)

    def item(self, *, efeito: bool, fonte: SourceKind, capability: str = "OPEN_POST",
             sufixo: str = "") -> NovoItem:
        return NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=PACOTE, capability=capability + sufixo, role="actor"),
                        content={"modelo": "alvo_ausente", "alvo": TEXTO_DA_PESSOA + sufixo},
                        summary=f"{TEXTO_DA_PESSOA}{sufixo}", source_kind=fonte, side_effect=efeito, app_version="447")


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco_migrado(tmp_path, "espera.sqlite3")
    d.execute("INSERT INTO apps(id, name, package, builtin) VALUES ('instagram','Instagram',?,0)", (PACOTE,))
    yield d
    d.close()


# ------------------------------------------------------------------ domínio: a faixa
def test_faixa_c_alto_risco_sessao_e_envio() -> None:
    assert classificar_espera(side_effect=True, human_origin=False, tem_catalogo=True,
                              catalogo=FatosDoCatalogo(risco="high")) == (Faixa.C, MotivoDeEntrada.ALTO_RISCO)
    assert classificar_espera(side_effect=True, human_origin=False, tem_catalogo=True,
                              catalogo=FatosDoCatalogo(politica="manual_only")) == (Faixa.C, MotivoDeEntrada.ALTO_RISCO)
    assert classificar_espera(side_effect=True, human_origin=False, tem_catalogo=True,
                              catalogo=FatosDoCatalogo(precisa_rascunho=True)) == (Faixa.C, MotivoDeEntrada.ALTO_RISCO)
    # sessão e autenticação vencem tudo, mesmo sem efeito e sem catálogo
    assert classificar_espera(side_effect=False, human_origin=False, tem_catalogo=False,
                              sessao_ou_autenticacao=True) == (Faixa.C, MotivoDeEntrada.SESSAO_OU_AUTENTICACAO)


def test_faixa_b_efeito_medio_commit_sem_catalogo_e_origem_humana() -> None:
    assert classificar_espera(side_effect=True, human_origin=False, tem_catalogo=True,
                              catalogo=FatosDoCatalogo(risco="medium", efeito_externo=True)
                              ) == (Faixa.B, MotivoDeEntrada.EFEITO_EXTERNO)
    assert classificar_espera(side_effect=True, human_origin=False, tem_catalogo=True) == (
        Faixa.B, MotivoDeEntrada.EFEITO_EXTERNO)                    # commit com catálogo e sem fatos da etapa
    assert classificar_espera(side_effect=True, human_origin=False, tem_catalogo=False) == (
        Faixa.B, MotivoDeEntrada.COMMIT_SEM_CATALOGO)                # B, confirmado pelo dono em 03/10
    assert classificar_espera(side_effect=False, human_origin=True, tem_catalogo=True) == (
        Faixa.B, MotivoDeEntrada.TEXTO_DE_PESSOA)                   # D-2: origem humana sem efeito
    assert classificar_espera(side_effect=False, human_origin=False, tem_catalogo=True) is None   # não espera ninguém


def test_motivo_de_saida() -> None:
    assert motivo_de_saida(por_sistema=False, para_aposentado=True) is MotivoDeSaida.DECIDIDO_POR_PESSOA
    assert motivo_de_saida(por_sistema=True, para_aposentado=False) is MotivoDeSaida.REBAIXADO_PELO_SISTEMA
    assert motivo_de_saida(por_sistema=True, para_aposentado=True) is MotivoDeSaida.SUBSTITUIDO


def test_o_vocabulario_do_payload_e_o_da_especificacao() -> None:
    assert {m.value for m in MotivoDeEntrada} == {"efeito_externo", "texto_de_pessoa", "commit_sem_catalogo",
                                                  "alto_risco", "sessao_ou_autenticacao", "parecer_da_ia", "reaprendido"}
    assert {m.value for m in MotivoDeSaida} == {"decidido_por_pessoa", "rebaixado_pelo_sistema", "substituido"}
    assert href_do_item("receita", "100") == "#/aprendizado?aba=aprendido&item=receita:100"


# ------------------------------------------------------------------ serviço: entra, sai, não repete
def test_item_de_pessoa_entra_e_sai_da_espera(db: Database) -> None:
    m = Mundo(db)
    criado = m.servico.propor(m.item(efeito=False, fonte=SourceKind.MANUAL), by=PESSOA)
    assert len(m.barramento.emitidos) == 1
    tipo, mensagem, nivel, dados = m.barramento.emitidos[0]
    assert tipo == TIPO_DO_EVENTO == "learning.needs_person" and tipo not in EPHEMERAL_KINDS
    assert dados == {"kind": "licao", "ref": criado.id, "app": PACOTE, "faixa": "B", "aguardando": True,
                     "motivo": "texto_de_pessoa", "href": f"#/aprendizado?aba=aprendido&item=licao:{criado.id}",
                     "desde": "2026-10-02T12:00:00.000Z"}
    assert nivel == "info" and criado.id in mensagem

    # a pessoa rejeita: sai da espera, com o `desde` da entrada
    m.servico.mudar_estado(LivroKind.LICAO, criado.id, S.DISABLED, by=PESSOA, reason="não serve")
    assert len(m.barramento.emitidos) == 2
    saida = m.barramento.dados[1]
    assert saida["aguardando"] is False and saida["motivo"] == "decidido_por_pessoa"
    assert saida["faixa"] == "B" and saida["desde"] == dados["desde"] and saida["ref"] == criado.id


def test_mover_entre_estados_que_nao_mudam_a_espera_nao_publica(db: Database) -> None:
    m = Mundo(db)
    # lição de sistema, sem efeito e sem texto de pessoa: nunca espera ninguém
    livre = m.servico.propor(m.item(efeito=False, fonte=SourceKind.RECOVERY, sufixo="-a"))
    m.servico.mudar_estado(LivroKind.LICAO, livre.id, S.VALIDATED, by=PESSOA, reason="valido")
    m.servico.mudar_estado(LivroKind.LICAO, livre.id, S.PUBLISHED, by=PESSOA, reason="publico")
    assert m.barramento.emitidos == []
    # texto de pessoa: candidata espera; validar (por pessoa) e depois publicar... validated + requires_owner ainda espera
    texto = m.servico.propor(m.item(efeito=False, fonte=SourceKind.MANUAL, sufixo="-b"), by=PESSOA)
    assert [d["aguardando"] for d in m.barramento.dados] == [True]
    m.servico.mudar_estado(LivroKind.LICAO, texto.id, S.VALIDATED, by=PESSOA, reason="validei")
    assert [d["aguardando"] for d in m.barramento.dados] == [True]       # candidate -> validated: segue esperando
    m.servico.mudar_estado(LivroKind.LICAO, texto.id, S.PUBLISHED, by=PESSOA, reason="aprovei")
    assert [d["aguardando"] for d in m.barramento.dados] == [True, False]


def test_nascimento_repetido_do_mesmo_conteudo_nao_publica_de_novo(db: Database) -> None:
    m = Mundo(db)
    novo = m.item(efeito=False, fonte=SourceKind.MANUAL)
    m.servico.propor(novo, by=PESSOA)
    m.servico.propor(novo, by=PESSOA)                       # o mesmo conteúdo vivo no mesmo escopo: devolve o existente
    assert len(m.barramento.emitidos) == 1


def test_item_com_efeito_espera_depois_de_validado_pelo_sistema_e_sai_ao_ser_aprovado(db: Database) -> None:
    m = Mundo(db, CatalogoFalso({"OPEN_POST": FatosDoCatalogo(risco="medium", efeito_externo=True)}))
    item = m.servico.propor(m.item(efeito=True, fonte=SourceKind.RECOVERY))
    assert m.barramento.emitidos == []                      # candidata de sistema: ainda não espera o dono
    m.servico.mudar_estado(LivroKind.LICAO, item.id, S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
    assert m.barramento.dados[-1]["aguardando"] is True
    assert (m.barramento.dados[-1]["faixa"], m.barramento.dados[-1]["motivo"]) == ("B", "efeito_externo")
    m.servico.mudar_estado(LivroKind.LICAO, item.id, S.PUBLISHED, by=PESSOA, reason="aprovo")
    assert m.barramento.dados[-1]["aguardando"] is False
    assert m.barramento.dados[-1]["motivo"] == "decidido_por_pessoa"
    assert len(m.barramento.emitidos) == 2


def test_rebaixado_pelo_sistema(db: Database) -> None:
    m = Mundo(db)
    item = m.servico.propor(m.item(efeito=True, fonte=SourceKind.RECOVERY))
    m.servico.mudar_estado(LivroKind.LICAO, item.id, S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
    m.servico.mudar_estado(LivroKind.LICAO, item.id, S.DISABLED, by=SYSTEM_ACTOR, reason="refutado pela medida")
    assert [d["aguardando"] for d in m.barramento.dados] == [True, False]
    assert m.barramento.dados[0]["motivo"] == "commit_sem_catalogo"   # sem porta de catálogo: nenhum catálogo conhecido
    assert m.barramento.dados[1]["motivo"] == "rebaixado_pelo_sistema"


# ------------------------------------------------------------------ faixa B x C no serviço
def test_faixa_c_alto_risco_e_nivel_de_aviso(db: Database) -> None:
    catalogo = CatalogoFalso({"DM_SEND": FatosDoCatalogo(risco="high", politica="manual_only", precisa_rascunho=True,
                                                         efeito_externo=True),
                              "OPEN_POST": FatosDoCatalogo(risco="medium", efeito_externo=True)})
    m = Mundo(db, catalogo)
    c = m.servico.propor(m.item(efeito=True, fonte=SourceKind.RECOVERY, capability="DM_SEND"))
    b = m.servico.propor(m.item(efeito=True, fonte=SourceKind.RECOVERY, capability="OPEN_POST"))
    for i in (c, b):
        m.servico.mudar_estado(LivroKind.LICAO, i.id, S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
    por_ref = {d["ref"]: d for d in m.barramento.dados}
    assert (por_ref[c.id]["faixa"], por_ref[c.id]["motivo"]) == ("C", "alto_risco")
    assert (por_ref[b.id]["faixa"], por_ref[b.id]["motivo"]) == ("B", "efeito_externo")
    niveis = {d["ref"]: n for _, _, n, d in m.barramento.emitidos}
    assert niveis[c.id] == "warn" and niveis[b.id] == "info"


def test_item_nascido_de_sessao_desconhecida_e_faixa_c(db: Database) -> None:
    m = Mundo(db, CatalogoFalso())
    item = m.servico.propor(m.item(efeito=True, fonte=SourceKind.SESSION_UNKNOWN))
    m.servico.mudar_estado(LivroKind.LICAO, item.id, S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
    assert (m.barramento.dados[-1]["faixa"], m.barramento.dados[-1]["motivo"]) == ("C", "sessao_ou_autenticacao")


# ------------------------------------------------------------------ fontes nativas
def test_receita_com_commit_entra_pela_transicao_do_livro_e_sai_pela_pessoa(db: Database) -> None:
    m = Mundo(db, CatalogoFalso(tem=False))
    rid = _receita(db, status="candidate", commit=True, passo="curtir")
    m.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
    assert m.barramento.dados == [{"kind": "receita", "ref": str(rid), "app": PACOTE, "faixa": "B", "aguardando": True,
                                   "motivo": "commit_sem_catalogo", "href": f"#/aprendizado?aba=aprendido&item=receita:{rid}",
                                   "desde": "2026-10-02T12:00:00.000Z"}]
    m.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.PUBLISHED, by=PESSOA, reason="aprovo")
    assert m.barramento.dados[-1]["aguardando"] is False and m.barramento.dados[-1]["motivo"] == "decidido_por_pessoa"
    assert len(m.barramento.emitidos) == 2


def test_mudanca_da_propria_loja_de_receitas_publica_uma_vez(db: Database) -> None:
    """A promoção em sombra do `RecipeStore` para em `validated` com `commit`: o ouvinte da loja avisa o livro."""
    m = Mundo(db, CatalogoFalso())
    rid = _receita(db, status="validated", commit=True, passo="comentar")      # a loja já gravou o novo status
    ouvinte = OuvinteD1DasReceitas(None, TrilhaDasLojas(db), db, m.servico.avisar_mudanca_nativa)   # type: ignore[arg-type]
    ouvinte.mudou(MudancaDaReceita(recipe_id=rid, de="candidate", para="validated", motivo="sombra", por=SYSTEM_ACTOR))
    assert [d["aguardando"] for d in m.barramento.dados] == [True]
    # a mesma mudança chegando de novo (loja e livro) não repete
    ouvinte.mudou(MudancaDaReceita(recipe_id=rid, de="candidate", para="validated", motivo="sombra", por=SYSTEM_ACTOR))
    m.servico.avisar_mudanca_nativa(LivroKind.RECEITA, str(rid), "candidate", "validated", by=SYSTEM_ACTOR)
    assert len(m.barramento.emitidos) == 1
    # e a saída feita pela loja (a receita foi para quarentena) é avisada uma vez
    db.execute("UPDATE recipes SET status='quarantined' WHERE id=?", (rid,))
    m.servico.avisar_mudanca_nativa(LivroKind.RECEITA, str(rid), "validated", "quarantined", by=SYSTEM_ACTOR)
    m.servico.avisar_mudanca_nativa(LivroKind.RECEITA, str(rid), "validated", "quarantined", by=SYSTEM_ACTOR)
    assert [d["aguardando"] for d in m.barramento.dados] == [True, False]
    assert m.barramento.dados[-1]["motivo"] == "rebaixado_pelo_sistema"


def test_receita_sem_efeito_nunca_espera(db: Database) -> None:
    m = Mundo(db)
    rid = _receita(db, status="candidate", commit=False, passo="abrir")
    m.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
    assert m.barramento.emitidos == []


# ------------------------------------------------------------------ o payload não carrega conteúdo
def test_payload_so_tem_os_campos_da_lista_e_nada_do_conteudo(db: Database) -> None:
    m = Mundo(db, CatalogoFalso({"OPEN_POST": FatosDoCatalogo(risco="high")}))
    pessoa = m.servico.propor(m.item(efeito=False, fonte=SourceKind.MANUAL, sufixo="-p"), by=PESSOA)
    efeito = m.servico.propor(m.item(efeito=True, fonte=SourceKind.RECOVERY, sufixo="-e"))
    m.servico.mudar_estado(LivroKind.LICAO, efeito.id, S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
    m.servico.mudar_estado(LivroKind.LICAO, pessoa.id, S.DISABLED, by=PESSOA, reason="nota secreta do dono")
    rid = _receita(db, status="candidate", commit=True, passo="curtir")
    m.servico.mudar_estado(LivroKind.RECEITA, str(rid), S.VALIDATED, by=SYSTEM_ACTOR, reason="prova")
    assert len(m.barramento.emitidos) >= 4
    for _, mensagem, _, dados in m.barramento.emitidos:
        assert set(dados) == CAMPOS_DO_PAYLOAD, set(dados) ^ CAMPOS_DO_PAYLOAD
        texto = json.dumps(dados, ensure_ascii=False) + mensagem
        for proibido in (TEXTO_DA_PESSOA, "nota secreta", "alvo_ausente", "botao", "a IA explicou", "selectors"):
            assert proibido not in texto, proibido


def test_aviso_so_monta_os_campos_da_lista() -> None:
    aviso = AvisoDeEspera(kind="licao", ref="li-1", app="", faixa=Faixa.C, aguardando=True, motivo="alto_risco",
                          desde="2026-10-02T12:00:00.000Z")
    assert set(aviso.como_dados()) == CAMPOS_DO_PAYLOAD
    assert aviso.nivel == "warn" and "licao li-1" in aviso.mensagem()


# ------------------------------------------------------------------ a porta e o barramento
def test_falha_da_porta_nao_derruba_a_transicao(db: Database) -> None:
    class Quebrada:
        def esperando_a_pessoa(self, aviso: AvisoDeEspera) -> None:
            raise RuntimeError("barramento fora")

    habilidades = SqlSkillRepository(db, ValidadorFalso())
    repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
    servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=lambda: AGORA,
                              retencao_de_logs_dias=lambda: 14, eventos=Quebrada())
    item = servico.propor(Mundo(db).item(efeito=False, fonte=SourceKind.MANUAL), by=PESSOA)
    assert servico.entrada(LivroKind.LICAO, item.id).state is S.CANDIDATE          # o item nasceu mesmo assim


def test_sem_porta_nada_e_publicado(db: Database) -> None:
    habilidades = SqlSkillRepository(db, ValidadorFalso())
    repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
    servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=lambda: AGORA,
                              retencao_de_logs_dias=lambda: 14)
    servico.propor(Mundo(db).item(efeito=False, fonte=SourceKind.MANUAL), by=PESSOA)   # não levanta


def test_evento_e_persistido_no_barramento_real(db: Database) -> None:
    bus = EventBus(db, origin="teste")
    habilidades = SqlSkillRepository(db, ValidadorFalso())
    repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades), precos=dict)
    servico = LearningService(repo, FontesSql(db), TriagemDeCredencial(), ajustes=Ajustes, relogio=lambda: AGORA,
                              retencao_de_logs_dias=lambda: 14, eventos=EventosNoBarramento(bus))
    item = servico.propor(Mundo(db).item(efeito=False, fonte=SourceKind.MANUAL), by=PESSOA)
    linhas = db.query("SELECT kind, level, instance_id, data FROM events WHERE kind=?", (TIPO_DO_EVENTO,))
    assert len(linhas) == 1 and linhas[0]["instance_id"] is None
    assert json.loads(linhas[0]["data"])["ref"] == item.id


def test_montagem_liga_o_barramento_e_o_catalogo_do_registro(db: Database) -> None:
    from app.config import LearningCfg
    from app.modules.learning.infrastructure.montagem import montar_aprendizado

    bus = BarramentoFalso()
    cfg = LearningCfg()
    livro = montar_aprendizado(db, config=lambda: cfg, retencao_de_logs_dias=lambda: 14, precos=dict,
                               relogio=lambda: AGORA, commit=lambda: "abc", eventos=bus)
    novo = Mundo(db).item(efeito=False, fonte=SourceKind.MANUAL)
    livro.propor(novo, by=PESSOA)
    assert [d["aguardando"] for d in bus.dados] == [True]
    sem = montar_aprendizado(db, config=lambda: cfg, retencao_de_logs_dias=lambda: 14, precos=dict,
                             relogio=lambda: AGORA, commit=lambda: "abc")
    sem.propor(Mundo(db).item(efeito=False, fonte=SourceKind.MANUAL, sufixo="-x"), by=PESSOA)
    assert len(bus.emitidos) == 1                                       # sem barramento, nada vai para ele


def test_risco_do_registro_le_so_fatos_do_catalogo() -> None:
    risco = RiscoDoRegistro()
    assert risco.tem_catalogo(PACOTE) and not risco.tem_catalogo("com.nao.existe")
    desconhecida = risco.da_capability(PACOTE, "NAO_EXISTE")
    assert desconhecida is None and risco.da_capability("com.nao.existe", "X") is None
    catalogo = registry.get(PACOTE)
    assert catalogo is not None
    fatos = [risco.da_capability(PACOTE, c.key) for c in catalogo.capabilities]
    assert any(f is not None and f.risco == "high" for f in fatos)
    assert all(isinstance(f, FatosDoCatalogo) for f in fatos)
