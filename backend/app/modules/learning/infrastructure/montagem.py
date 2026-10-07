"""Composição do aprendizado: o que o `AppState` chama para ter um `LearningService` pronto.

Aqui (e só aqui) o bloco `aprendizado:` do config vira `Ajustes`, a guarda do fluxo vira uma função sobre o
repositório das habilidades, e os pacotes seguintes ligam as suas partes (`ligar_*`). Cada leitura do config é
feita A CADA uso: o arquivo muda com o processo no ar.

O D1 dos conhecimentos nativos (A5, `ligar_nativos`) precisa de mais que o serviço: o repositório (evidência e
trilha), o banco, as habilidades e as DUAS lojas do scheduler (`FlowStore`, `RecipeStore`), onde a política e o
ouvinte entram. Sem as lojas (testes do livro isolado), só os mineradores do digest são ligados.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from functools import partial

from app.config import PROJECT_ROOT, BacklogCfg, Config, LearningCfg
from app.db import Database
from app.modules.applications.infrastructure import registry
from app.modules.learning.application.apps import VisaoPorApp
from app.modules.learning.application.falhas import ServicoDeFalhas
from app.modules.learning.application.metricas import ServicoDeMetricas
from app.modules.learning.application.nativos import Decidir
from app.modules.learning.application.ports import Ajustes, CuradorDeIA, Retencao
from app.modules.learning.application.resultado_posterior import GravadorDoResultadoPosterior
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.backlog import RegrasDoBacklog
from app.modules.learning.domain.camada import ModosDeRuntime
from app.modules.learning.domain.saude import LimiaresDeSaude
from app.modules.learning.domain.vocabulario import Modo, ModoDeTelas
from app.modules.learning.infrastructure import (ligar_aprovacao_automatica, ligar_autopublicacao, ligar_costuras,
                                                 ligar_curador, ligar_licoes, ligar_nativos, ligar_obsolescencia, ligar_telas,
                                                 ligar_voz)
from app.modules.learning.infrastructure.declarados import DeclaradosDoRegistro, LojaSql
from app.modules.learning.infrastructure.ensinado_sql import LeitorDoEnsinadoSql
from app.modules.learning.infrastructure.eventos import (Barramento, EventosNoBarramento, RiscoDoRegistro,
                                                         TitulosDoRegistro)
from app.modules.learning.infrastructure.fatos_da_operacao_sql import FatosDaOperacaoParaOLivro
from app.modules.learning.infrastructure.fontes import FontesSql
from app.modules.learning.infrastructure.metricas_sql import FontesDeMetricasSql
from app.modules.learning.infrastructure.relatorio_sql import FontesDeFalhaSql, SqlBacklogRepository
from app.modules.learning.infrastructure.resultado_posterior_sql import ResultadoPosteriorSql
from app.modules.learning.infrastructure.risco_do_conteudo import RiscoDoConteudo
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sombra_da_quarentena_sql import SombraDaQuarentena
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.infrastructure.sql_repository import SqlSkillRepository
from app.taskqueue.aproveitamento import aproveitamento
from app.taskqueue.flows import FlowStore, id_do_fluxo, ref_publica_do_fluxo
from app.taskqueue.recipes import RecipeStore
from app.util import now
from app.version import commit_em_execucao


def ajustes_do_config(cfg: LearningCfg) -> Ajustes:
    r = cfg.retencao
    return Ajustes(enabled=cfg.enabled, curadoria_s=cfg.curadoria_s, modo_licoes=Modo(cfg.licoes.modo),
                   modo_telas=ModoDeTelas(cfg.telas.modo), modo_voz=Modo(cfg.voz.modo),
                   modo_preferencias=Modo(cfg.preferencias.modo),
                   por_licoes={p: Modo(m) for p, m in cfg.licoes.por_app.items()},
                   por_telas={p: ModoDeTelas(m) for p, m in cfg.telas.por_app.items()},
                   retencao=Retencao(sinais_dias=r.sinais_dias, feedback_dias=r.feedback_dias,
                                     exposicoes_dias=r.exposicoes_dias, evidencias_por_item=r.evidencias_por_item,
                                     diario_dias=r.diario_dias,
                                     candidata_sem_evidencia_dias=r.candidata_sem_evidencia_dias),
                   saude=LimiaresDeSaude(sem_uso_dias=cfg.saude.sem_uso_dias,
                                         amostra_minima=cfg.saude.amostra_minima, taxa_minima=cfg.saude.taxa_minima,
                                         falhas_seguidas=cfg.saude.falhas_seguidas,
                                         contestacao_dias=cfg.saude.contestacao_dias))


class GuardaDoFluxo:
    """As guardas que `PUT /api/flows/{id}` já aplicava, agora também no livro: religar um fluxo adotado por
    habilidade publicada, ou cujo comando outra habilidade publicou, deixaria o mesmo comando vivo nos dois lugares."""

    def __init__(self, db: Database, habilidades: SqlSkillRepository) -> None:
        self._db = db
        self._habilidades = habilidades

    def __call__(self, flow_id: str) -> str | None:
        adotante = self._habilidades.published_adopter(flow_id)
        if adotante is not None:
            return (f"O fluxo foi adotado pela habilidade {adotante.ref}, que está publicada: religá-lo deixaria o "
                    "mesmo comando vivo nos dois lugares. Desfaça a adoção para voltar ao fluxo.")
        linha = self._db.one("SELECT match_key FROM flows WHERE id=?", (flow_id,))
        chave = linha["match_key"] if linha else None
        if isinstance(chave, str) and (outra := self._habilidades.published_with_command(chave)) is not None:
            return (f"A habilidade {outra.ref} está publicada com o mesmo comando: religar o fluxo deixaria o comando "
                    "vivo nos dois lugares. Desabilite a habilidade antes.")
        return None


def regras_do_backlog(cfg: BacklogCfg) -> RegrasDoBacklog:
    return RegrasDoBacklog(minimo_ocorrencias=cfg.minimo_ocorrencias, pessoa_usd=cfg.pessoa_usd,
                           aparelho_usd_min=cfg.aparelho_usd_min, prova_minimo=cfg.prova_minimo,
                           prova_fator=cfg.prova_fator)


def pacotes_do_registro() -> list[str]:
    """Os pacotes do registro de apps (embutidos inclusive): é por onde o `app_id` de um fluxo ou habilidade, que já é
    um pacote, resolve sem passar pela tabela `apps` (30.2)."""
    return [d.package for d in registry.registered()]


def modos_de_runtime(cfg: object) -> ModosDeRuntime:
    """Os modos que moram fora do bloco `aprendizado:` (`ai.recipes`, `ai.flows`, `skills.enabled`), para a camada de
    uso. A montagem do livro só recebe o `LearningCfg`, então quem lê é a apresentação, com o `cfg` do processo; sem ele
    (ou de outra forma), tudo vem desconhecido e a camada sai `desconhecida`, nunca um palpite."""
    if not isinstance(cfg, Config):
        return ModosDeRuntime()
    return ModosDeRuntime(receitas=cfg.file.ai.recipes, fluxos=cfg.file.ai.flows, habilidades=cfg.file.skills.enabled)


def montar_aprendizado(db: Database, *, config: Callable[[], LearningCfg], retencao_de_logs_dias: Callable[[], int],
                       precos: Callable[[], dict[str, list[float]]], habilidades: SqlSkillRepository | None = None,
                       fluxos: FlowStore | None = None, receitas: RecipeStore | None = None,
                       decidir: Decidir | None = None, relogio: Callable[[], datetime] = now,
                       commit: Callable[[], str | None] | None = None,
                       eventos: Barramento | None = None,
                       curador_de_ia: CuradorDeIA | None = None) -> LearningService:
    """`fluxos`/`receitas`: as lojas do scheduler, que passam a nascer e mudar de status com o D1 e a trilha.
    `decidir(texto, run_id)`: a linha do tempo da execução (cada transição do sistema vira uma decisão nela).
    `commit`: o commit que este processo carregou — o MESMO que o `/api/health` mostra; a prova da correção do
    backlog o registra ao começar. Sem ele, lido do `.git` da raiz do projeto (sem chamar `git`).
    `eventos`: o barramento do central (`EventBus`): com ele, o livro publica `learning.needs_person` (30.21). Sem ele,
    nada é publicado. `curador_de_ia`: o adaptador do curador por IA (30.11); sem ele, o simulado (o do hub: 30.12)."""
    repo = SqlLearningRepository(db, guarda_do_fluxo=GuardaDoFluxo(db, habilidades) if habilidades else None,
                                 precos=precos)
    risco = RiscoDoRegistro()
    fontes = FontesSql(db, pacotes_do_registro=pacotes_do_registro)
    # 30.33: o aviso `learning.needs_person` lê a receita e o fluxo pelo mesmo leitor do dossiê do curador.
    lido = RiscoDoConteudo(db, risco)
    # 30.83: o fluxo sai do central pela referência pública (o `ref` e o `href` do aviso de espera)
    porta = EventosNoBarramento(eventos, partial(ref_publica_do_fluxo, db)) if eventos is not None else None
    servico = LearningService(repo, fontes,
                              TriagemDeCredencial(), ajustes=lambda: ajustes_do_config(config()),
                              relogio=relogio, retencao_de_logs_dias=retencao_de_logs_dias,
                              eventos=porta, catalogo_de_risco=risco, titulos=TitulosDoRegistro(),
                              risco_do_nativo=lambda e: lido.do_nativo(e, fontes.conteudo(e.kind, e.ref)),
                              ensinado=porta, leitor_do_ensinado=LeitorDoEnsinadoSql(db),
                              isolar_o_aviso=db.savepoint,    # 30.80 B
                              id_do_fluxo=partial(id_do_fluxo, db))   # 30.83
    # Pacote A3: o que mais falha e o backlog. A apresentação o acha pelo tipo; a curadoria roda o passo dele.
    falhas = ServicoDeFalhas(FontesDeFalhaSql(db, precos=precos), SqlBacklogRepository(db), repo,
                             TriagemDeCredencial(), regras=lambda: regras_do_backlog(config().backlog),
                             relogio=relogio, commit=commit or partial(commit_em_execucao, PROJECT_ROOT),
                             contagem_do_livro=lambda: servico.livro().contagem)
    servico.anexar(falhas)
    servico.anexar(VisaoPorApp(servico, DeclaradosDoRegistro(), LojaSql(db)))     # 30.1: a visão por app
    servico.registrar_passo(falhas)
    for ligar in (ligar_costuras.ligar, ligar_voz.ligar):
        ligar(servico)
    ligar_nativos.ligar(servico, repo, db, concordancias=lambda: config().fluxo.concordancias,
                        com_prova=lambda: config().fluxo.com_prova, simulada_publica=lambda: config().simulada_publica,
                        habilidades=habilidades, fluxos=fluxos,
                        receitas=receitas, decidir=decidir, relogio=relogio)
    # Pacote A7: as lições medidas (fornecedor da costura, mineradores do digest e passo da curadoria).
    ligar_licoes.ligar(servico, repo, db, config=lambda: config().licoes, precos=precos, relogio=relogio)
    # A8 (fatia 5): as telas aprendidas precisam do repositório e do banco, e penduram-se nas extensões das costuras.
    ligar_telas.ligar(servico, repo, db, config=lambda: config().telas, relogio=relogio)
    # 30.14: o rótulo `obsoleto_provavel` e o rebaixamento `catalogo_sem_efeito` (passo da curadoria, sem IA).
    ligar_obsolescencia.ligar(servico, repo, db, fontes=FontesSql(db, pacotes_do_registro=pacotes_do_registro))
    # 31.190: o fato confirmado da pesquisa de uma operação encerrada vira candidata do escritor (passo, sem IA).
    servico.registrar_passo(FatosDaOperacaoParaOLivro(servico, repo, db))
    # 31.202: o rendimento da receita ensinada sugere, em SOMBRA, liberar ou prender de volta (passo, sem IA; nada se
    # aplica). Precisa da régua da loja de receitas (30.81); sem a loja, o passo não entra.
    if receitas is not None:
        servico.registrar_passo(SombraDaQuarentena(servico, db, liberada=receitas.liberada_fora_do_ensino))
    # 30.35: o desfecho medido das revisões do curador 14 dias depois (o rótulo 2 do Jev; passo da curadoria, sem IA).
    servico.registrar_passo(GravadorDoResultadoPosterior(ResultadoPosteriorSql(db), lambda: servico.ajustes.saude))
    # 30.11: o curador por IA (laço próprio sob a trava de líder; `off` de fábrica; adaptador simulado até o 30.12).
    ligar_curador.ligar(servico, repo, db, TriagemDeCredencial(), config=lambda: config().curador, precos=precos,
                        relogio=relogio, catalogo=risco, curador_de_ia=curador_de_ia)
    # 30.34: a autopublicação do fluxo B em sombra (laço próprio sob a trava de líder; `off` de fábrica).
    auto = ligar_autopublicacao.ligar(servico, repo, db, config=lambda: config().autopublicacao, relogio=relogio,
                                      catalogo=risco)
    # 30.55: a aprovação automática por política (laço próprio sob a trava de líder; `off` de fábrica).
    ligar_aprovacao_automatica.ligar(servico, repo, db, config=lambda: config().aprovacao_automatica, relogio=relogio,
                                     catalogo=risco)
    # 30.8: as métricas (só leitura); a economia é a do aproveitamento, reaproveitada, e o orçamento, o do curador.
    servico.anexar(ServicoDeMetricas(servico, FontesDeMetricasSql(db, precos=precos),
                                     aproveitamento=partial(_aproveitamento, db),
                                     curador=lambda: ligar_curador.ajustes_do_curador(config().curador),
                                     autopublicacao=auto.relatorio, precos=precos, relogio=relogio))
    return servico


def _aproveitamento(db: Database, dias: int, agora: datetime) -> dict[str, object]:
    return aproveitamento(db, dias=dias, agora=agora)
