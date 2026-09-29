"""Telas aprendidas: a fatia 5 do ADR-052 (item 18.8), pacote A8 do ADR-054.

- o MOTOR: a regra aprendida entra depois das declaradas (desafio, 2FA, login, intersticial e carregando vencem
  sempre), é pulada em tela protegida, exige TODOS os `ids_todos` e só é `autenticada`; `com_aprendidas` faz
  `autenticada()` e `em_casa()` valerem;
- a SESSÃO: a tela de casa que mudou, aprendida com a aba declarada, evita o laço "o voltar saiu do app" e a conta
  continua lida só pela tela de perfil declarada; sem a aba, ela não entra em casa;
- o CICLO: observação em etapa comprovada, candidata, prova local e negativa, publicação sozinha só no modo `on`,
  evidência simulada que não publica, o primeiro conflito que desliga, a exportação YAML que volta pelo carregador e a
  absorção pelo repositório; a candidata nasce com o `run_id` do digest, e ela e o desligamento pelo conflito
  aparecem no "Aprendizado desta execução" (`GET /api/runs/{id}/feedback`) da execução certa;
- o DEIXA-UM-FORA com as telas do dublê (sem `feed` → candidata de casa; sem `thread` → o compositor da conversa) e o
  app de e-mail declarado só em dado aprendendo uma tela nova.

Nível de prova: `simulated` (banco de teste, aparelhos falsos, nenhuma IA). O consumo real da tela de casa fica
`not_run` até um app mudar de fato; o deixa-um-fora sobre observações REAIS é `scripts/aprendizado-telas.py`.
"""
from __future__ import annotations

import importlib.util
import json
import weakref
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from xml.sax.saxutils import quoteattr

import httpx
import pytest
import yaml
from fastapi import FastAPI

from app.automation import conhecimento_de_telas as telas
from app.automation.hierarchy import RegraDeTelaSensivel, UiTree, parse_hierarchy
from app.config import TelasAprendidasCfg
from app.db import Database
from app.events import EventBus
from app.integrations.app_declarado import conhecimento
from app.integrations.app_declarado.sessao import ConferenciaDaSessao, Outcome, SessaoDeclarada
from app.modules.learning.application.aprendido import AprendizadoDaExecucao
from app.modules.learning.application.ports import Ajustes
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.telas import ServicoDeTelas, TelaDaTentativa
from app.modules.learning.domain import telas as dominio
from app.modules.learning.domain.aprendido import Grupo
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.promocao import Decisao, Limiares, veredito_de_repeticao
from app.modules.learning.domain.vocabulario import LivroKind, ModoDeTelas, Posicao
from app.modules.learning.infrastructure import ligar_telas
from app.modules.learning.infrastructure.aprendido_sql import LeituraDoAprendidoSql
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.ligar_costuras import costuras_do_livro
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.learning.presentation.router import router as learning_router
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService
from app.taskqueue.costuras import FechamentoDeTentativa
from app.util import to_iso

from . import pacote_instagram as ig
from . import test_sensitive_input as entrada_sensivel
from .conftest import make_config
from .fake_instagram import PKG, FakeInstagram
from .fake_skills import banco as banco_migrado
from .test_instagram_auth import FakeDevices, FakeRt, cadastrar
from .test_sessao_declarada import (CORREIO, SENHA_DO_CORREIO, SESSAO_DO_CORREIO, TELAS_DO_CORREIO,
                                    USUARIO_DO_CORREIO, FakeCorreio, _gravar_correio, _No)

S = SkillState
AGORA = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
RAIZ = Path(__file__).resolve().parents[2]
#: Ids da tela de casa do correio DEPOIS de uma atualização: o `telas.yaml` dele não os conhece.
CASA_NOVA = ("mail_header_v2", "mail_list_v2")
#: A tela nova que o correio ganhou (uma pasta), vista em etapas comprovadas.
PASTA_NOVA = ("folder_header", "folder_list", "compose_fab")


# ==================================================================== árvores
def _xml(nos: list[_No], pacote: str = CORREIO) -> UiTree:
    linhas = "".join(
        f'<node class={quoteattr(n.cls)} package="{pacote}" text={quoteattr(n.text)} '
        f'resource-id={quoteattr((pacote + ":id/" + n.rid) if n.rid else "")} content-desc={quoteattr(n.desc)} '
        f'clickable="{str(n.clickable).lower()}" enabled="true" focused="false" password="{str(n.password).lower()}" '
        f'scrollable="false" bounds="[{n.bounds[0]},{n.bounds[1]}][{n.bounds[2]},{n.bounds[3]}]" />' for n in nos)
    return parse_hierarchy(f'<hierarchy rotation="0">{linhas}</hierarchy>')


def _barra() -> list[_No]:
    return [_No("android.widget.ImageView", (20, 1180, 340, 1260), desc="Caixa", rid="inbox_tab", clickable=True),
            _No("android.widget.ImageView", (380, 1180, 700, 1260), desc="Conta", rid="conta_tab", clickable=True,
                acao="conta")]


def _tela(*ids: str, aba: bool = True, textos: tuple[str, ...] = (), senha: bool = False) -> UiTree:
    nos = [_No("android.view.View", (0, 60 + i * 70, 720, 120 + i * 70), rid=rid) for i, rid in enumerate(ids)]
    nos += [_No("android.widget.TextView", (40, 700 + i * 60, 680, 750 + i * 60), text=t) for i, t in enumerate(textos)]
    if senha:
        nos.append(_No("android.widget.EditText", (40, 1000, 680, 1060), rid="campo", password=True, clickable=True))
    return _xml([*nos, *(_barra() if aba else [])])


def _conta() -> UiTree:
    """A tela da conta do correio (declarada): cabeçalho, a conta e a barra."""
    return _xml([_No("android.view.View", (0, 40, 720, 300), rid="account_header"),
                 _No("android.widget.TextView", (40, 120, 400, 180), text=USUARIO_DO_CORREIO, rid="account_name"),
                 *_barra()])


def _aprendida(*ids: str, casa: bool = False, nome: str = "aprendida_teste_abc123") -> telas.RegraDeTela:
    return telas.regra_aprendida({"tela": nome, "tipo": "autenticada", "autenticada": True, "ids_todos": list(ids),
                                  "casa": casa, "razao": "teste"})


def _correio() -> conhecimento.ConhecimentoDeSessao:
    """O conhecimento do correio (só dado), montado de novo a cada chamada."""
    return conhecimento.de_dados(yaml.safe_load(yaml.safe_dump(SESSAO_DO_CORREIO)),
                                 telas.de_dados(yaml.safe_load(yaml.safe_dump(TELAS_DO_CORREIO))))


# ==================================================================== o motor
def test_a_regra_aprendida_nunca_vence_desafio_2fa_login_intersticial_ou_carregando() -> None:
    """Os ids da regra aprendida estão em todas as telas abaixo — e cada uma continua com o tratamento dela."""
    extra = ("mail_x", "mail_y")
    k = replace(ig.SESSAO, telas=telas.com_aprendidas(ig.CONHECIMENTO, [_aprendida(*extra, casa=True)]))

    def com_extra(app: FakeInstagram) -> UiTree:
        base = app.page_source().replace("</hierarchy>", "")
        nos = "".join(f'<node class="android.view.View" package="{PKG}" text="" resource-id="{PKG}:id/{i}" '
                      'content-desc="" clickable="false" enabled="true" focused="false" password="false" '
                      f'scrollable="false" bounds="[0,{10 + n * 5}][10,{14 + n * 5}]" />' for n, i in enumerate(extra))
        return parse_hierarchy(base + nos + "</hierarchy>")

    for tela, esperada in (("challenge", "challenge"), ("two_factor", "two_factor"), ("login", "login"),
                           ("save_login", "save_login_prompt")):
        r = k.reconhecer(com_extra(FakeInstagram(screen=tela)), package=PKG, locale="en-US")
        assert r.tela == esperada, (tela, r.tela)
    assert k.reconhecer(parse_hierarchy('<hierarchy rotation="0"></hierarchy>'), package=PKG).tela == "loading"
    # Controle: os mesmos ids numa tela sem nada declarado viram a aprendida.
    assert k.reconhecer(_xml([_No("android.view.View", (0, 0, 9, 9), rid=i) for i in extra], PKG),
                        package=PKG).tela == "aprendida_teste_abc123"


@pytest.mark.parametrize("protegida", ["senha", "codigo_sem_campo", "conta_travada", "sensivel_do_parque"])
def test_a_regra_aprendida_e_pulada_em_tela_protegida(protegida: str) -> None:
    k = telas.com_aprendidas(_correio().telas, [_aprendida(*PASTA_NOVA[:2])])
    assert telas.classificar(k, _tela(*PASTA_NOVA[:2]), package=CORREIO).tela == "aprendida_teste_abc123"  # controle
    if protegida == "senha":
        arvore = _tela(*PASTA_NOVA[:2], senha=True)
    elif protegida == "codigo_sem_campo":
        arvore = _tela(*PASTA_NOVA[:2], textos=("Enter the code we sent to your e-mail",))
    elif protegida == "conta_travada":
        arvore = _tela(*PASTA_NOVA[:2], textos=("Confirm you're human to use your account",))
    else:
        xml = _tela(*PASTA_NOVA[:2], textos=("CPF 123",))
        regra = RegraDeTelaSensivel(package=CORREIO, texts=("cpf",))
        arvore = parse_hierarchy(_arvore_xml(xml), regras=(regra,))
        assert arvore.sensitive
    assert telas.tela_protegida(arvore)
    r = telas.classificar(k, arvore, package=CORREIO)
    assert r.tela != "aprendida_teste_abc123", protegida


def _arvore_xml(tree: UiTree) -> str:
    """De volta a XML (para reparsear com as regras de tela sensível do parque)."""
    return "<hierarchy>" + "".join(
        f'<node class={quoteattr(e.class_name)} package="{e.package}" text={quoteattr(e.text)} '
        f'resource-id={quoteattr(e.resource_id)} content-desc={quoteattr(e.desc)} clickable="false" enabled="true" '
        f'focused="false" password="false" scrollable="false" bounds="[{e.bounds[0]},{e.bounds[1]}]'
        f'[{e.bounds[2]},{e.bounds[3]}]" />' for e in tree.elements) + "</hierarchy>"


def test_ids_todos_exige_todos_e_por_sufixo_exato() -> None:
    k = telas.com_aprendidas(_correio().telas, [_aprendida("folder_header", "folder_list")])
    assert telas.classificar(k, _tela("folder_header", "folder_list"), package=CORREIO).tela.startswith("aprendida_")
    assert telas.classificar(k, _tela("folder_header"), package=CORREIO).tela == telas.DESCONHECIDA
    # Prefixo NÃO basta (o `ids` do repositório casa por prefixo; o aprendido, não).
    assert telas.classificar(k, _tela("folder_header_x", "folder_list_x"), package=CORREIO).tela == telas.DESCONHECIDA


@pytest.mark.parametrize(("mexe", "trecho"), [
    ({"tipo": "login"}, "autenticada"),
    ({"tipo": "desafio"}, "autenticada"),
    ({"autenticada": False}, "autenticada"),
    ({"sinal": "entrar"}, "não se aprende"),
    ({"extracao": "conta"}, "não se aprende"),
    ({"formulario_de_senha": True}, "não se aprende"),
    ({"ids": ["folder"]}, "não se aprende"),
    ({"tela": "caixa"}, "precisa começar"),
    ({"ids_todos": ["so_um"]}, "exige de 2 a 4"),
    ({"ids_todos": ["a1", "b1", "c1", "d1", "e1"]}, "exige de 2 a 4"),
    ({"casa": "sim"}, "true ou false"),
])
def test_regra_aprendida_recusa_o_que_e_do_repositorio(mexe: dict[str, object], trecho: str) -> None:
    dados: dict[str, object] = {"tela": "aprendida_x_abc123", "tipo": "autenticada", "autenticada": True,
                                "ids_todos": ["folder_header", "folder_list"], "casa": False, "razao": "x"}
    with pytest.raises(telas.ConhecimentoInvalido, match=trecho):
        telas.regra_aprendida({**dados, **mexe})


def test_com_aprendidas_faz_autenticada_e_em_casa_valerem() -> None:
    k = _correio().telas
    casa = _aprendida(*CASA_NOVA, casa=True, nome="aprendida_casa_000001")
    fora = _aprendida(*PASTA_NOVA[:2], nome="aprendida_pasta_000002")
    repetida = _aprendida("x_a", "x_b", nome="aprendida_casa_000001")          # nome já usado: fica de fora
    unido = telas.com_aprendidas(k, [casa, fora, repetida])
    assert unido.autenticada("aprendida_casa_000001") and unido.em_casa("aprendida_casa_000001")
    assert unido.autenticada("aprendida_pasta_000002") and not unido.em_casa("aprendida_pasta_000002")
    assert [r.tela for r in unido.telas][:len(k.telas)] == [r.tela for r in k.telas]   # declaradas primeiro
    assert unido.regra("aprendida_casa_000001").ids_todos == CASA_NOVA
    # Pura: o declarado não mudou, e sem aprendida volta o MESMO objeto.
    assert not k.em_casa("aprendida_casa_000001") and telas.com_aprendidas(k, []) is k
    # Declarada nunca entra por aqui (a regra do repositório só vem do arquivo).
    assert telas.com_aprendidas(k, [telas.RegraDeTela("aprendida_z_1", "desafio", "x", ids_todos=("a_x", "b_x"),
                                                      origem=telas.ORIGEM_APRENDIDA)]) is k


# ==================================================================== o ciclo
@dataclass
class Mundo:
    db: Database
    repo: SqlLearningRepository
    livro: LearningService
    telas: ServicoDeTelas
    cfg: TelasAprendidasCfg
    pasta: Path
    relogio: list[datetime]

    def andar(self, **quanto: float) -> None:
        self.relogio[0] += timedelta(**quanto)

    def fechar(self, f: FechamentoDeTentativa) -> None:
        costuras_do_livro(self.livro, self.db).ao_fechar_tentativa(f)

    def itens(self) -> list[Any]:
        return self.repo.itens(kind=LivroKind.TELA)

    def sinais(self, kind: str = "tela_vista") -> list[dict[str, Any]]:
        return self.db.query("SELECT * FROM learning_signals WHERE kind=? ORDER BY id", (kind,))


@pytest.fixture
def mundo(tmp_path: Path) -> Iterator[Mundo]:
    pasta = tmp_path / "apps"
    _gravar_correio(pasta / CORREIO)
    db = banco_migrado(tmp_path, "telas.sqlite3")
    relogio = [AGORA]
    cfg = TelasAprendidasCfg(modo="on")
    repo = SqlLearningRepository(db, clock=lambda: to_iso(relogio[0]))
    livro = LearningService(repo, FontesSql(db), TriagemDeCredencial(),
                            ajustes=lambda: Ajustes(modo_telas=ModoDeTelas(cfg.modo)), relogio=lambda: relogio[0],
                            retencao_de_logs_dias=lambda: 14)
    servico = ligar_telas.ligar(livro, repo, db, config=lambda: cfg, relogio=lambda: relogio[0],
                                commit=lambda: "abc1234", pasta=pasta)
    yield Mundo(db, repo, livro, servico, cfg, pasta, relogio)
    db.close()


def _fechamento(tree: UiTree | None, *, run: str, n: int, instance: str = "android-02", status: str = "succeeded",
                verified: bool = True, simulated: bool = False, loja: bool = False, pacote: str = CORREIO,
                capability: str = "ABRIR_PASTA") -> FechamentoDeTentativa:
    return FechamentoDeTentativa(
        attempt_id=f"{run}-a{n}", step_id=f"{run}-s{n}", run_id=run, objective_id=f"{run}-o", instance_id=instance,
        profile_id=None, app_package=pacote, capability=capability, template_hash="h1", status=status,
        verified=verified, arvore=tree, loja=loja, simulated=simulated)


def _aprender(m: Mundo, ids: tuple[str, ...], *, aba: bool = True, simulated: bool = False,
              capability: str = "ABRIR_PASTA") -> None:
    """3 observações da tela em 2 execuções, uma amostra da tela da conta (declarada) e o digest."""
    for run, n in (("r1", 1), ("r1", 2), ("r2", 3)):
        m.fechar(_fechamento(_tela(*ids, aba=aba), run=run, n=n, simulated=simulated, capability=capability))
    m.fechar(_fechamento(_conta(), run="r1", n=9, simulated=simulated))
    assert m.livro.digerir_execucao("r2").falhas == ()


def test_o_app_de_email_declarado_so_em_dado_aprende_uma_tela_nova(mundo: Mundo) -> None:
    """De ponta a ponta, pelas costuras: etapas comprovadas → sinais → candidata → validada → publicada (modo `on`) →
    o fornecedor da sessão entrega a regra, e o correio (nenhuma linha de Python dele) classifica a tela nova."""
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    assert item.state is SkillState.PUBLISHED and not item.side_effect and not item.human_origin
    assert item.content["tipo"] == "autenticada" and item.content["casa"] is True
    assert set(item.content["ids_todos"]) == set(PASTA_NOVA)          # a barra de abas saiu: a conta também a tem
    assert (item.evidence_for, item.distinct_runs, item.distinct_devices) == (3, 2, 1)
    assert [t.to_state for t in mundo.repo.trilha(item.id)] == [S.CANDIDATE, S.VALIDATED, S.PUBLISHED]
    assert {t.decided_by for t in mundo.repo.trilha(item.id)} == {"sistema"}
    # A sessão consome pelo fornecedor ligado na composição.
    unido = conhecimento.com_as_aprendidas(_correio())
    r = unido.reconhecer(_tela(*PASTA_NOVA), package=CORREIO)
    assert r.tela == item.content["tela"] and unido.telas.autenticada(r.tela) and unido.telas.em_casa(r.tela)
    # Nenhum texto de tela no que se gravou: só ids, a tela reconhecida, a aba e a versão.
    for s in mundo.sinais():
        assert set(json.loads(s["data"])) == {"ids", "classificada", "tem_aba_de_perfil", "versao"}
        assert USUARIO_DO_CORREIO not in s["data"]


def test_no_modo_observe_valida_mas_nao_publica_nem_a_sessao_consome(mundo: Mundo) -> None:
    mundo.cfg.modo = "observe"
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    assert item.state is SkillState.VALIDATED
    assert [t.to_state for t in mundo.repo.trilha(item.id)] == [S.CANDIDATE, S.VALIDATED]   # nem tentou publicar
    assert conhecimento.regras_aprendidas(CORREIO) == ()
    # Ligar o modo: a curadoria publica o que já estava validado.
    mundo.cfg.modo = "on"
    assert mundo.livro.curar().falhas == ()
    assert mundo.repo.item(item.id).state is SkillState.PUBLISHED
    assert [r.tela for r in conhecimento.regras_aprendidas(CORREIO)] == [item.content["tela"]]
    # O interruptor: fora do `on`, a sessão para de consumir na hora (a invalidação vem a cada publicação).
    mundo.cfg.modo = "off"
    conhecimento.invalidar_regras_aprendidas()
    assert conhecimento.regras_aprendidas(CORREIO) == ()


def test_evidencia_simulada_nao_publica(mundo: Mundo) -> None:
    _aprender(mundo, PASTA_NOVA, simulated=True)
    (item,) = mundo.itens()
    assert item.state is SkillState.CANDIDATE and item.evidence_for == 0     # os contadores só contam o real
    evidencias = mundo.repo.evidencias(item.id)
    assert len(evidencias) == 3 and all(e.simulated for e in evidencias)
    # As duas camadas, cada uma sozinha: a repetição não conta o simulado, e a prova local também não.
    assert veredito_de_repeticao(evidencias, Limiares(n_min=3, execucoes_min=2)).decisao is Decisao.ESPERA
    regra = dominio.regra_do_conteudo(item.content)
    assert regra is not None
    positivas = ligar_telas.LeituraDeTelasSql(mundo.db).por_origem(CORREIO, [e.origin_ref for e in evidencias])
    assert len(positivas) == 3 and not dominio.provar(regra, positivas, []).ok
    assert conhecimento.regras_aprendidas(CORREIO) == ()


def test_so_etapa_comprovada_sobre_tela_do_app_nao_protegida_e_fora_da_loja_e_observada(mundo: Mundo) -> None:
    casos = [
        _fechamento(_tela(*PASTA_NOVA), run="r1", n=1, status="failed", verified=False),
        _fechamento(_tela(*PASTA_NOVA), run="r1", n=2, verified=False),
        _fechamento(_tela(*PASTA_NOVA), run="r1", n=3, loja=True),
        _fechamento(_tela(*PASTA_NOVA, senha=True), run="r1", n=4),
        _fechamento(_tela(*PASTA_NOVA, textos=("Confirme sua identidade",)), run="r1", n=5),
        _fechamento(_xml([_No("android.view.View", (0, 0, 9, 9), rid=i) for i in PASTA_NOVA], "com.outro.app"),
                    run="r1", n=6),
        _fechamento(None, run="r1", n=7),
        _fechamento(_tela(*PASTA_NOVA), run="r1", n=8, pacote="com.sem.conhecimento"),
    ]
    for f in casos:
        mundo.fechar(f)
    assert mundo.sinais() == []
    # O serviço confere de novo o que a infraestrutura já filtra: com ids na mão, etapa não comprovada não entra.
    base = TelaDaTentativa(pacote=CORREIO, attempt_id="x1", run_id="r9", step_id="s9", instance_id="android-02",
                           status="succeeded", verified=True, simulated=False, loja=False, em_primeiro_plano=True,
                           protegida=False, classificada=dominio.DESCONHECIDA, tipo=dominio.DESCONHECIDA,
                           ids=PASTA_NOVA, tem_aba=True)
    for mexida in ({"status": "failed"}, {"verified": False}, {"loja": True}, {"protegida": True},
                   {"em_primeiro_plano": False}, {"tipo": "login"}, {"tipo": "desafio", "classificada": "desafio"}):
        assert mundo.telas.observar_tentativa(replace(base, **mexida)) == 0, mexida
    assert mundo.sinais() == []
    assert mundo.telas.observar_tentativa(base) == 1                  # controle: a mesma, comprovada, entra
    mundo.cfg.modo = "off"
    mundo.fechar(_fechamento(_tela(*PASTA_NOVA), run="r1", n=10))
    assert mundo.telas.observar_tentativa(replace(base, attempt_id="x2")) == 0
    assert len(mundo.sinais()) == 1


def test_amostras_das_telas_declaradas_tem_teto(mundo: Mundo) -> None:
    for n in range(dominio.AMOSTRAS_POR_TELA + 5):
        mundo.fechar(_fechamento(_conta(), run="r1", n=n))
    assert len(mundo.sinais()) == dominio.AMOSTRAS_POR_TELA


def test_prova_local_e_prova_negativa() -> None:
    def obs(origem: str, *ids: str, classificada: str = dominio.DESCONHECIDA,
            simulated: bool = False) -> dominio.Observacao:
        return dominio.Observacao(origem=origem, ids=frozenset(ids), classificada=classificada, tem_aba=True,
                                  simulated=simulated)

    regra = dominio.RegraAprendida("aprendida_x_1", ("folder_header", "folder_list"), casa=False, razao="x")
    positivas = [obs("a1", "folder_header", "folder_list", "z"), obs("a2", "folder_header", "folder_list")]
    caixa = obs("a3", "message_list", "inbox_tab", classificada="caixa")
    assert dominio.provar(regra, positivas, [caixa]).ok
    # Uma amostra positiva que ela não reconhece: a regra não reclassifica tudo.
    assert not dominio.provar(regra, [*positivas, obs("a4", "folder_header")], [caixa]).ok
    # Negativa: ela casaria uma tela que o repositório já reconhece.
    conta = obs("a5", "folder_header", "folder_list", "account_header", classificada="conta")
    prova = dominio.provar(regra, positivas, [caixa, conta])
    assert not prova.ok and prova.declaradas_casadas == ("a5",)
    # Só o simulado não prova nada.
    assert not dominio.provar(regra, [obs("a6", "folder_header", "folder_list", simulated=True)], []).ok


#: As frases de desafio e 2FA que `test_sensitive_input` amarra ao classificador (a MESMA lista, não uma cópia).
FRASES_DE_VERIFICACAO: list[str] = list(
    entrada_sensivel.test_concorda_com_o_classificador_de_desafio_do_instagram.pytestmark[0].args[1])


def test_a_regra_nao_casa_as_arvores_de_desafio_2fa_e_senha_da_entrada_sensivel() -> None:
    """As árvores de `test_sensitive_input` (cada frase de desafio e 2FA, com e sem campo, e a de senha), com os ids
    da regra aprendida acrescentados: a aprendida nunca casa — num app que nem declara desafio."""
    pacote = "com.exemplo.app"
    k = telas.com_aprendidas(telas.de_dados({
        "app": pacote, "idioma_padrao": "pt", "sinais": {"pt": {}},
        "telas": [{"tela": "inicio", "tipo": "autenticada", "autenticada": True, "ids": ["inicio"]}],
        "estado_conhecido": {"telas": ["inicio"]}}), [_aprendida("folder_header", "folder_list")])
    ids = [entrada_sensivel._no(rid=f"{pacote}:id/{i}", pkg=pacote) for i in ("folder_header", "folder_list")]
    assert telas.classificar(k, parse_hierarchy(entrada_sensivel._tela(*ids)), package=pacote).tela == \
        "aprendida_teste_abc123"                                                          # controle
    campo = entrada_sensivel._no("android.widget.EditText", rid=f"{pacote}:id/code", pkg=pacote)
    senha = campo.replace("bounds=", 'password="true" bounds=')
    arvores = [entrada_sensivel._tela(entrada_sensivel._no(text=f, pkg=pacote), *extra, *ids)
               for f in FRASES_DE_VERIFICACAO for extra in ((campo,), ())]
    arvores.append(entrada_sensivel._tela(senha, *ids))
    assert len(arvores) == 2 * len(FRASES_DE_VERIFICACAO) + 1 > 40
    for xml in arvores:
        arvore = parse_hierarchy(xml)
        assert telas.tela_protegida(arvore), xml
        assert telas.classificar(k, arvore, package=pacote).tela != "aprendida_teste_abc123", xml


def test_o_primeiro_conflito_desliga(mundo: Mundo) -> None:
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    login = parse_hierarchy(FakeCorreio(tela="entrada").page_source())
    # Longe de qualquer uso: login no mesmo aparelho não é conflito.
    mundo.andar(minutes=10)
    mundo.fechar(_fechamento(login, run="r3", n=1, status="failed", verified=False))
    assert mundo.repo.item(item.id).state is SkillState.PUBLISHED
    # Um uso (etapa comprovada sobre a tela aprendida), e 60 s depois um login em OUTRO aparelho: nada.
    mundo.fechar(_fechamento(_tela(*PASTA_NOVA), run="r3", n=2))
    mundo.andar(seconds=60)
    mundo.fechar(_fechamento(login, run="r3", n=3, instance="android-03", status="failed", verified=False))
    assert mundo.repo.item(item.id).state is SkillState.PUBLISHED
    # 30 s depois, no MESMO aparelho: o primeiro conflito desliga, sem exceção de idade.
    mundo.andar(seconds=30)
    mundo.fechar(_fechamento(login, run="r3", n=4, status="failed", verified=False))
    atual = mundo.repo.item(item.id)
    assert atual.state is SkillState.DISABLED
    assert any(e.stance is Posicao.CONFLICT for e in mundo.repo.evidencias(item.id))
    assert "conflito" in mundo.repo.trilha(item.id)[-1].reason
    assert conhecimento.regras_aprendidas(CORREIO) == ()                # a sessão para de consumir na hora


def _execucao(db: Database, run_id: str) -> None:
    """A linha de `runs` que o "Aprendizado desta execução" exige (a coleta e o digest das telas não a leem)."""
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,?,?,?,?,?,?,?)", (run_id, run_id, "abrir a pasta", "execute", "completed", 0,
                                             '["android-02"]', to_iso(AGORA)))


def test_a_tela_nascida_no_digest_e_a_desligada_no_conflito_aparecem_no_aprendizado_da_execucao(mundo: Mundo) -> None:
    """Pelo minerador REAL (sinais `tela_vista` → digest de r2): a tela nasce com o `run_id` de r2 e aparece entre as
    candidatas do "Aprendizado desta execução" de r2 — não na de r1, que só a observou. O primeiro conflito, em r3,
    a desliga: no bloco de r3 ela entra no mesmo grupo como tela que já existia, desligada nesta execução."""
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    trilha = mundo.repo.trilha(item.id)
    assert [(t.to_state, t.run_id) for t in trilha] == [(S.CANDIDATE, "r2"), (S.VALIDATED, None),
                                                        (S.PUBLISHED, None)]
    for run_id in ("r1", "r2", "r3"):
        _execucao(mundo.db, run_id)
    bloco = AprendizadoDaExecucao(mundo.livro, LeituraDoAprendidoSql(mundo.db), TriagemDeCredencial())
    [nascida] = [i for i in bloco.da_execucao("r2") if i.grupo is Grupo.CANDIDATA]
    assert (nascida.kind, nascida.ref, nascida.estado, nascida.titulo) == (
        LivroKind.TELA, item.id, SkillState.PUBLISHED, item.summary)
    assert nascida.papel == "tela, evidência a favor"
    assert not any(i.ref == item.id for i in bloco.da_execucao("r1"))       # r1 só reforçou: fica de fora
    # O conflito de r3 (um uso e um login no mesmo aparelho, 30 s depois).
    login = parse_hierarchy(FakeCorreio(tela="entrada").page_source())
    mundo.andar(minutes=10)
    mundo.fechar(_fechamento(_tela(*PASTA_NOVA), run="r3", n=2))
    mundo.andar(seconds=30)
    mundo.fechar(_fechamento(login, run="r3", n=4, status="failed", verified=False))
    assert mundo.repo.item(item.id).state is SkillState.DISABLED
    assert mundo.repo.trilha(item.id)[-1].run_id == "r3"
    [desligada] = [i for i in bloco.da_execucao("r3") if i.grupo is Grupo.CANDIDATA]
    assert (desligada.ref, desligada.estado) == (item.id, SkillState.DISABLED)
    # O uso de r3 (a favor) e o conflito que a desligou, pelo sistema: "nesta execução".
    assert desligada.papel == ("tela que já existia, desligada nesta execução, evidência a favor, "
                               "evidência em conflito")
    # E r2 continua dizendo que a tela nasceu dela, agora com o estado de hoje.
    [de_r2] = [i for i in bloco.da_execucao("r2") if i.grupo is Grupo.CANDIDATA]
    assert (de_r2.papel, de_r2.estado) == ("tela, evidência a favor", SkillState.DISABLED)


def test_o_uso_e_o_instante_da_observacao_e_nao_o_do_digest(mundo: Mundo) -> None:
    """A candidata nasce no digest, horas depois das observações: a evidência dela tem a hora do digest, mas o USO
    foi quando a tela foi vista. Um login 60 s depois do digest não é conflito com uma tela vista 2 h antes."""
    for run, n in (("r1", 1), ("r1", 2), ("r2", 3)):
        mundo.fechar(_fechamento(_tela(*PASTA_NOVA), run=run, n=n))
    mundo.fechar(_fechamento(_conta(), run="r1", n=9))
    mundo.andar(hours=2)
    mundo.livro.digerir_execucao("r2")
    (item,) = mundo.itens()
    assert item.state is SkillState.PUBLISHED
    mundo.andar(seconds=60)
    login = parse_hierarchy(FakeCorreio(tela="entrada").page_source())
    mundo.fechar(_fechamento(login, run="r3", n=1, status="failed", verified=False))
    assert mundo.repo.item(item.id).state is SkillState.PUBLISHED
    assert not any(e.stance is Posicao.CONFLICT for e in mundo.repo.evidencias(item.id))


def test_conta_errada_na_conferencia_depois_do_uso_desliga(mundo: Mundo) -> None:
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    observador = ligar_telas.ObservadorDaSessaoDoLivro(weakref.ref(mundo.telas),
                                                        ligar_telas.ConhecimentoDoRepositorio(mundo.pasta))
    base = ConferenciaDaSessao(pacote=CORREIO, instance_id="android-02", profile_id="p1", desfecho="session_ready",
                               tipo_da_tela="autenticada", tela_aprendida=item.content["tela"], desconhecida=None,
                               tentou_login=False)
    mundo.andar(minutes=10)
    observador.ao_conferir(base)                                        # parou nela e leu a conta: a favor
    assert mundo.repo.item(item.id).state is SkillState.PUBLISHED
    assert any(e.origin_ref.startswith("sessao:") for e in mundo.repo.evidencias(item.id))
    mundo.andar(seconds=40)
    observador.ao_conferir(replace(base, desfecho="wrong_account", tela_aprendida=None))
    assert mundo.repo.item(item.id).state is SkillState.DISABLED


def test_o_export_volta_pelo_de_dados_e_a_absorcao_aposenta(mundo: Mundo) -> None:
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    texto = mundo.telas.exportar(CORREIO)
    assert item.id in texto and "abc1234" in texto                    # a proveniência vai em comentário
    fragmento = yaml.safe_load(texto)
    (regra,) = fragmento["telas"]
    assert regra["tela"] == item.content["tela"] and regra["ids_todos"] == item.content["ids_todos"]
    assert fragmento["estado_conhecido"]["telas"] == [item.content["tela"]]
    # O fragmento acrescentado ao arquivo carrega pelo MESMO carregador, e a regra volta igual.
    arquivo = mundo.pasta / CORREIO / "telas.yaml"
    dados = yaml.safe_load(arquivo.read_text(encoding="utf-8"))
    dados["telas"].extend(fragmento["telas"])
    dados["estado_conhecido"]["telas"].extend(fragmento["estado_conhecido"]["telas"])
    k = telas.de_dados(dados)
    lida = k.regra(item.content["tela"])
    assert lida is not None and not lida.aprendida and list(lida.ids_todos) == item.content["ids_todos"]
    assert k.em_casa(lida.tela)
    # Commitado (aqui: escrito) e implantado (um processo novo lê o arquivo): a curadoria aposenta como absorvida.
    arquivo.write_text(yaml.safe_dump(dados, allow_unicode=True), encoding="utf-8")
    novo = ligar_telas.ligar(mundo.livro, mundo.repo, mundo.db, config=lambda: mundo.cfg,
                             relogio=lambda: mundo.relogio[0], commit=lambda: "def5678", pasta=mundo.pasta)
    assert novo.executar(mundo.relogio[0]) == 1
    atual = mundo.repo.item(item.id)
    assert atual.state is SkillState.DEPRECATED and atual.state_detail == "absorvida:def5678"
    # E o nome já declarado não se exporta de novo nem entra pela união (o repositório venceu).
    with pytest.raises(Exception, match="Nenhuma tela"):
        novo.exportar(CORREIO)


def test_trinta_dias_sem_casar_numa_versao_nova_aposenta(mundo: Mundo) -> None:
    mundo.db.execute("INSERT INTO device_app_state(instance_id, package_name, observed_version_name) VALUES (?,?,?)",
                     ("android-02", CORREIO, "1.0"))
    _aprender(mundo, PASTA_NOVA)
    (item,) = mundo.itens()
    assert item.state is SkillState.PUBLISHED and item.app_version == "1.0"   # a versão vem do aparelho, não da tela
    mundo.andar(days=31)
    assert mundo.telas.executar(mundo.relogio[0]) == 0                  # sem versão nova, sem casar não basta
    mundo.db.execute("UPDATE device_app_state SET observed_version_name='2.0' WHERE instance_id='android-02'")
    assert mundo.telas.executar(mundo.relogio[0]) == 1
    atual = mundo.repo.item(item.id)
    assert atual.state is SkillState.DEPRECATED and "30 dias" in mundo.repo.trilha(item.id)[-1].reason


def test_sem_a_aba_a_candidata_nao_e_de_casa(mundo: Mundo) -> None:
    _aprender(mundo, PASTA_NOVA, aba=False)
    (item,) = mundo.itens()
    assert item.state is SkillState.PUBLISHED and item.content["casa"] is False
    unido = conhecimento.com_as_aprendidas(_correio())
    assert unido.telas.autenticada(item.content["tela"]) and not unido.telas.em_casa(item.content["tela"])


def test_casa_vista_sem_a_aba_e_evidencia_contra(mundo: Mundo) -> None:
    mundo.cfg.modo = "observe"
    for run, n in (("r1", 1), ("r1", 2)):
        mundo.fechar(_fechamento(_tela(*PASTA_NOVA), run=run, n=n))
    mundo.fechar(_fechamento(_tela(*PASTA_NOVA), run="r2", n=3))
    mundo.fechar(_fechamento(_conta(), run="r1", n=9))
    mundo.livro.digerir_execucao("r2")
    (item,) = mundo.itens()
    assert item.content["casa"] is True and item.state is SkillState.VALIDATED
    mundo.fechar(_fechamento(_tela(*PASTA_NOVA, aba=False), run="r4", n=1))
    assert any(e.stance is Posicao.AGAINST for e in mundo.repo.evidencias(item.id))


# ==================================================================== a sessão
@dataclass
class CorreioDeCasaNova(FakeCorreio):
    """O correio depois de uma atualização: a caixa (a tela de casa) tem ids que o `telas.yaml` não conhece, e o
    "voltar" dela sai do app — é a raiz. Reabrir volta à mesma caixa."""

    fora: bool = False

    def current_package(self) -> str | None:
        return "com.android.launcher3" if self.fora else CORREIO

    def current_focus(self) -> tuple[str | None, str | None]:
        return self.current_package(), ".Principal"

    def start_app(self, package: str, activity: str | None = None) -> None:
        self.fora = False
        super().start_app(package, activity)

    def press_key(self, key: str) -> None:
        super().press_key(key)
        if key == "back" and self.tela == "caixa":
            self.fora = True

    def _montar(self) -> list[_No]:
        if self.fora:
            return [_No("android.widget.TextView", (40, 900, 200, 1000), text="Correio")]
        if self.tela == "caixa":
            return [_No("android.view.View", (0, 40, 720, 110), rid=CASA_NOVA[0]),
                    _No("androidx.recyclerview.widget.RecyclerView", (0, 130, 720, 1150), rid=CASA_NOVA[1]),
                    *_barra()]
        return super()._montar()


@pytest.fixture
def sessao_do_correio(mundo: Mundo, tmp_path: Path) -> Iterator[tuple[SessaoDeclarada, SocialRepository, str]]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    bus = EventBus(mundo.db)
    secrets = SecretStore(mundo.db, MemoryKeyProvider())
    repo = SocialRepository(mundo.db)
    social = SocialService(repo, secrets, bus, known_instances=lambda: ["android-01", "android-02"])
    k = conhecimento.carregar(mundo.pasta / CORREIO)
    app = CorreioDeCasaNova(conta=USUARIO_DO_CORREIO, tela="caixa")
    sessao = SessaoDeclarada(k, cfg, FakeDevices(app), repo, secrets,  # type: ignore[arg-type]
                             SensitiveInputChannel(lambda: True), bus)
    sessao.focus_poll_s = 0.01
    pid = cadastrar(social, username=USUARIO_DO_CORREIO, senha=SENHA_DO_CORREIO)
    yield sessao, repo, pid


async def test_tela_de_casa_aprendida_evita_o_laco_e_le_a_conta_pela_tela_de_perfil_declarada(
        mundo: Mundo, sessao_do_correio: tuple[SessaoDeclarada, SocialRepository, str]) -> None:
    sessao, repo, pid = sessao_do_correio
    app = sessao.devices.app  # type: ignore[attr-defined]
    # Antes de aprender: o voltar sai do app, a reabertura volta à mesma tela, e a conferência não confirma.
    r = await sessao.ensure_session(FakeRt(app), pid)                    # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "o voltar saiu do app" in r.detail, r.detail
    assert "key:back" in app.calls and app.typed == []
    (chamou,) = mundo.sinais("tela_desconhecida_chamou_pessoa")          # a tela de antes do voltar, só com ids
    assert set(yaml.safe_load(chamou["data"])["ids"]) >= set(CASA_NOVA) and USUARIO_DO_CORREIO not in chamou["data"]
    # As etapas comprovadas na caixa nova (com a aba declarada) ensinam a tela de casa.
    caixa_nova = parse_hierarchy(CorreioDeCasaNova(conta=USUARIO_DO_CORREIO, tela="caixa").page_source())
    for run, n in (("r1", 1), ("r1", 2), ("r2", 3)):
        mundo.fechar(_fechamento(caixa_nova, run=run, n=n))
    mundo.fechar(_fechamento(_conta(), run="r1", n=9))
    mundo.livro.digerir_execucao("r2")
    (item,) = mundo.itens()
    assert item.state is SkillState.PUBLISHED and item.content["casa"] is True
    # Depois: a conferência para na casa aprendida, toca a aba DECLARADA e lê a conta na tela de perfil declarada.
    app.calls.clear()
    app.fora, app.tela = False, "caixa"
    r = await sessao.ensure_session(FakeRt(app), pid)                    # type: ignore[arg-type]
    assert r.outcome is Outcome.SESSION_READY and r.observed_username == USUARIO_DO_CORREIO, r.detail
    assert "key:back" not in app.calls and app.tela == "conta" and app.typed == []
    # O uso foi a favor da tela (a conferência leu a conta nela).
    assert any(e.origin_ref.startswith("sessao:") and e.stance is Posicao.FOR
               for e in mundo.repo.evidencias(item.id))


async def test_sem_a_aba_a_regra_nao_entra_em_casa_e_o_laco_continua(
        mundo: Mundo, sessao_do_correio: tuple[SessaoDeclarada, SocialRepository, str]) -> None:
    """Aprendida só como `autenticada`, fora de casa, a tela não resolve: a conferência volta dela e sai do app."""
    sessao, repo, pid = sessao_do_correio
    app = sessao.devices.app  # type: ignore[attr-defined]
    _aprender(mundo, CASA_NOVA, aba=False)
    (item,) = mundo.itens()
    assert item.state is SkillState.PUBLISHED and item.content["casa"] is False
    r = await sessao.ensure_session(FakeRt(app), pid)                    # type: ignore[arg-type]
    assert r.outcome is Outcome.UNCERTAIN and "o voltar saiu do app" in r.detail, r.detail
    assert "key:back" in app.calls


# ==================================================================== o deixa-um-fora
def _observacoes_do_duble() -> list[dominio.Observacao]:
    """As telas do dublê do app real, 3 amostras de cada, como o observador as guardaria (só ids). A conversa varia
    de uma para outra (com uma ou com várias mensagens): o item de lista repetido sai dos ids."""
    saida: list[dominio.Observacao] = []
    for i, conversa in enumerate(("ana", "bia", "ana")):
        for tela, kw in (("feed", {"show_username_on_feed": False}), ("profile", {}), ("inbox", {}),
                         ("thread", {"thread_with": conversa})):
            arvore = parse_hierarchy(FakeInstagram(account="mariana", screen=tela, **kw).page_source())
            saida.append(dominio.Observacao(
                origem=f"attempt:{tela}-{i}", ids=frozenset(dominio.estaveis(telas.sufixos(arvore, PKG))),
                classificada=ig.reconhecer(arvore, package=PKG).tela,
                tem_aba=ig.SESSAO.aba_de_perfil(arvore) is not None, run_id=f"r{i}", instance_id="android-02"))
    return saida


def test_deixa_um_fora_com_as_telas_do_duble() -> None:
    declaradas = ligar_telas.ConhecimentoDoRepositorio().declaradas(PKG)
    assert declaradas is not None
    observacoes = _observacoes_do_duble()
    feeds = [o for o in observacoes if o.origem.startswith("attempt:feed")]
    assert len(feeds) == 3 and {o.classificada for o in feeds} == {"feed"}
    # Sem `feed` (nem no estado conhecido): uma candidata de CASA com ids do feed, que nenhuma outra tela tem.
    sem_feed = dominio.deixa_um_fora(declaradas, "feed", observacoes)
    casa = [c for c in sem_feed if c.regra.casa and all(set(c.regra.ids_todos) <= o.ids for o in feeds)]
    assert casa, [(c.regra.tela, c.regra.ids_todos) for c in sem_feed]
    outras = [o for o in observacoes if not o.origem.startswith("attempt:feed")]
    assert not any(set(casa[0].regra.ids_todos) <= o.ids for o in outras)
    # Sem `thread`: a candidata traz o compositor da conversa (`row_thread_composer…`), e não é de casa.
    (conversa,) = dominio.deixa_um_fora(declaradas, "thread", observacoes)
    assert any(i.startswith("row_thread_composer") for i in conversa.regra.ids_todos)
    assert not conversa.regra.casa and len(conversa.observacoes) == 3
    # Com o arquivo inteiro, nada a propor: o repositório já reconhece tudo.
    assert dominio.propor([o for o in observacoes if o.classificada == dominio.DESCONHECIDA],
                          [o for o in observacoes if o.classificada != dominio.DESCONHECIDA], declaradas) == []


def test_o_script_faz_o_deixa_um_fora_so_lendo(mundo: Mundo, capsys: pytest.CaptureFixture[str]) -> None:
    """O script do central, sobre o banco (aqui o de teste), com a pasta do correio: sem a `caixa`, a candidata."""
    for i in range(3):
        mundo.fechar(_fechamento(_tela("message_list", "compose_fab"), run=f"r{i}", n=i))
    for i in range(3):
        mundo.fechar(_fechamento(_conta(), run=f"r{i}", n=10 + i))
    antes = mundo.db.one("SELECT COUNT(*) AS n FROM learning_signals")
    espec = importlib.util.spec_from_file_location("aprendizado_telas", RAIZ / "scripts" / "aprendizado-telas.py")
    assert espec is not None and espec.loader is not None
    script = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(script)
    assert script.principal(["--dry-run", "--sem-regra", "caixa", "--app", CORREIO, "--pasta", str(mundo.pasta),
                             "--json"], db=mundo.db) == 0
    (linha,) = json.loads(capsys.readouterr().out)
    (candidata,) = linha["candidatas"]
    assert candidata["casa"] is True and set(candidata["ids_todos"]) <= {"message_list", "compose_fab"}
    assert mundo.db.one("SELECT COUNT(*) AS n FROM learning_signals") == antes and mundo.itens() == []


# ==================================================================== a rota
@pytest.fixture
async def cliente(mundo: Mundo) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(learning_router)
    app.state.poc = SimpleNamespace(learning=mundo.livro)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def test_rota_de_exportacao(mundo: Mundo, cliente: httpx.AsyncClient) -> None:
    vazio = await cliente.get(f"/api/aprendizado/export?kind=tela&app={CORREIO}")
    assert vazio.status_code == 404 and vazio.json()["detail"]["code"] == "not_found"
    _aprender(mundo, PASTA_NOVA)
    r = await cliente.get(f"/api/aprendizado/export?kind=tela&app={CORREIO}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/yaml")
    (item,) = mundo.itens()
    assert yaml.safe_load(r.text)["telas"][0]["tela"] == item.content["tela"]
    assert (await cliente.get(f"/api/aprendizado/export?app={CORREIO}")).status_code == 200    # kind=tela é o padrão
    outro = await cliente.get(f"/api/aprendizado/export?kind=licao&app={CORREIO}")
    assert outro.status_code == 422 and outro.json()["detail"]["code"] == "invalid"
    assert (await cliente.get("/api/aprendizado/export?kind=tela")).status_code == 422          # falta o app
    # A rota específica vem antes da genérica do livro (`{kind}/{ref}` casaria com ela).
    assert (await cliente.get("/api/aprendizado/tela/" + item.id)).json()["item"]["kind"] == "tela"
