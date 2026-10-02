"""Pacote A7 do ADR-054: as lições medidas — nascem por contraste, são escolhidas com teto por papel, vão ao prompt com
braço de controle e saem pelo efeito medido. Nenhuma chamada de IA em lugar nenhum daqui (`ia_resumos_por_dia: 0`).

O ciclo, de ponta a ponta:

1. NASCIMENTO (digest da execução assentada, sem IA): o contraste falha→sucesso comprovado na mesma etapa vira
   candidata do ATOR; o defeito do plano seguido de plano que comprovou a mesma ação vira candidata do PLANEJADOR. O
   texto sai de modelo fechado (`domain/licoes.py`); a mesma impressão que reaparece só soma evidência. A curadoria
   também transforma a nota de um "deu errado" (D2) em candidata `human_origin` — só o dono a valida e publica;
2. VALIDAÇÃO por repetição: a mesma impressão em 2 execuções reais distintas → `validated`;
3. PUBLICAÇÃO (D1): sem efeito externo e com `aprendizado.licoes.modo: on`, o sistema publica na `fila_de_prova`; a
   curadoria abre UMA prova por (app, ação, papel). Com efeito externo ou texto de pessoa, fica em "Para aprovar";
4. CONSUMO: uma vez por tentativa (ator) ou por planejamento (planejador), a costura `licoes_para` (A2) chama
   `licoes_para` daqui: escopo e ordem, o teto do papel sobre todas as elegíveis, o braço de cada uma e a exposição
   gravada NO ATO (braço e tokens; 0 no controle). No modo `shadow` nada vai ao prompt nem é gravado como exposição;
5. MEDIDA: o digest preenche o desfecho de cada exposição (status final, chamadas de IA pela tentativa, US$,
   segundos) antes da purga de `ai_calls` — só em execução REAL; a curadoria dá o veredito (≥8 por braço) e aplica:
   "ajuda" fica (com 10% de controle), "atrapalha" desliga, "neutra" aposenta; e aposenta o resto (sem exposição há
   60 dias, 2 refutações humanas, absorvida), devolve à prova na versão nova do app e propõe `promover_licao`.

A lição é só contexto: nunca entra no verificador, nunca muda guarda, política, desfecho nem custo máximo (o executor
continua impondo `commit_guard`, `card_guard` e `band_guard` — a lição que contradisser uma guarda perde para ela).
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol

from app.modules.learning.application.ports import NovaEvidencia, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, ConflitoDeEstado, ErroDeAprendizado,
                                               NotaComCaraDeSegredo, SkillState, Vetado)
from app.modules.learning.domain.efeito import (DETALHE_DO_EFEITO, SEM_EXPOSICAO_DIAS, Efeito, NovaExposicao,
                                                VereditoDeEfeito, abrir_provas, aposentadoria, propor_promocao,
                                                veredito_de_efeito, volta_a_prova)
from app.modules.learning.domain.licoes import (LIMIARES_DA_LICAO, Contraste, ContrasteDoPlano, Escolha, NotaDeFeedback,
                                                Pedido, Recusa, bloco_de_licoes, escolher, licao_de_contraste,
                                                licao_de_nota, licao_do_planejador)
from app.modules.learning.domain.livro import ItemDeAprendizado, NovoItem
from app.modules.learning.domain.modo_por_app import modo_efetivo
from app.modules.learning.domain.promocao import Decisao, veredito_de_repeticao
from app.modules.learning.domain.tokens import TETOS_DE_FABRICA, Teto, estimar_tokens
from app.modules.learning.domain.vocabulario import Braco, DetalheDeEstado, LivroKind, Modo, Papel, Posicao
from app.util import parse_iso, to_iso

log = logging.getLogger("poc.aprendizado")

#: Quantos dias de notas de "deu errado" a curadoria revisita a cada passo (idempotente: o item vivo não duplica).
NOTAS_DIAS = 2
#: Execuções assentadas há até estes dias com exposição sem desfecho: a curadoria preenche o que o digest perdeu
#: (processo reiniciado no meio). Bem abaixo de `log_retention_days`: `ai_calls` ainda está inteiro.
PREENCHER_DIAS = 3


@dataclass(frozen=True, slots=True)
class AjustesDeLicoes:
    """O bloco `aprendizado.licoes` do config (o modo mora em `Ajustes.modo_licoes`)."""

    ator: Teto = TETOS_DE_FABRICA[Papel.ACTOR]
    planejador: Teto = TETOS_DE_FABRICA[Papel.PLANNER]
    minimo_por_braco: int = 8
    maximo_por_braco: int = 20
    holdout_publicada: float = 0.1
    #: `aprendizado.licoes.por_app`: o modo de cada pacote que sobrescreve o global (§8.10). Vazio = o global vale.
    por_app: Mapping[str, Modo] = field(default_factory=dict)

    def teto(self, papel: Papel) -> Teto:
        return self.planejador if papel is Papel.PLANNER else self.ator


class Contador(Protocol):
    """As métricas do processo (`app/metricas.py`), que a aplicação não enxerga direto."""

    def __call__(self, nome: str, n: float = 1, **rotulos: str) -> None: ...


def _sem_metrica(nome: str, n: float = 1, **rotulos: str) -> None:
    return None


class RepositorioDeLicoes(Protocol):
    """O que as lições leem e gravam além do livro: a execução (contrastes e desfechos), as exposições, a versão do
    app no parque e o backlog."""

    def contrastes(self, run_id: str) -> list[Contraste]: ...
    def contrastes_do_plano(self, run_id: str) -> list[ContrasteDoPlano]: ...
    def notas(self, desde: str) -> list[NotaDeFeedback]: ...
    def publicadas(self, papel: Papel, app: str) -> list[ItemDeAprendizado]: ...
    def expor(self, exposicoes: Sequence[NovaExposicao], agora: str) -> int: ...
    def preencher(self, run_id: str, agora: str) -> int: ...
    def execucoes_a_preencher(self, desde: str) -> list[str]: ...
    def ultima_exposicao(self, item_id: str) -> str | None: ...
    def versao_atual(self, app: str) -> str | None: ...
    def absorvida(self, item_id: str) -> str | None: ...
    def propor_promocao(self, item: ItemDeAprendizado, agora: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class Previa:
    """O que iria ao prompt para (app, ação, papel), sem unidade (sem braço), sem gravar nada e sem IA."""

    app: str
    papel: Papel
    acao: str
    etapa: str
    modo: Modo
    teto: Teto
    escolha: Escolha
    bloco: str

    @property
    def tokens_do_bloco(self) -> int:
        return estimar_tokens(self.bloco)


class _Minerador:
    def __init__(self, nome: str, fazer: Callable[[str], int]) -> None:
        self.nome = nome
        self._fazer = fazer

    def minerar(self, run_id: str) -> int:
        return self._fazer(run_id)


class _Passo:
    def __init__(self, nome: str, fazer: Callable[[datetime], int]) -> None:
        self.nome = nome
        self._fazer = fazer

    def executar(self, agora: datetime) -> int:
        return self._fazer(agora)


class ServicoDeLicoes:
    def __init__(self, servico: LearningService, livro: RepositorioDeAprendizado, repo: RepositorioDeLicoes, *,
                 ajustes: Callable[[], AjustesDeLicoes], relogio: Callable[[], datetime],
                 contar: Contador = _sem_metrica) -> None:
        self._servico = servico
        self._livro = livro
        self._repo = repo
        self._ajustes = ajustes
        self._relogio = relogio
        self._contar = contar

    @property
    def modo(self) -> Modo:
        a = self._servico.ajustes
        return a.modo_licoes if a.enabled else Modo.OFF

    def modo_efetivo(self, pacote: str | None) -> Modo:
        """O modo que vale para `pacote` (§8.10): o override de `por_app`, senão o global. `enabled: false` vence
        tudo. Sem pacote, o global. É a ÚNICA regra: coleta, publicação e consumo passam por aqui."""
        a = self._servico.ajustes
        if not a.enabled:
            return Modo.OFF
        return modo_efetivo(a.modo_licoes, self._ajustes().por_app, pacote)

    def _algum_ligado(self) -> bool:
        """Falso só quando o global e todos os overrides estão em `off` (ou o aprendizado todo está desligado): é o
        atalho barato que poupa a leitura do digest e da curadoria."""
        a = self._servico.ajustes
        if not a.enabled:
            return False
        return a.modo_licoes is not Modo.OFF or any(m is not Modo.OFF for m in self._ajustes().por_app.values())
    # ================================================================== registro no digest e na curadoria
    def mineradores(self) -> tuple[_Minerador, ...]:
        return (_Minerador("licoes.exposicoes", self.preencher),
                _Minerador("licoes.contraste", self.minerar_contrastes),
                _Minerador("licoes.plano", self.minerar_plano))

    def passo(self) -> _Passo:
        return _Passo("licoes", self.curar)

    # ================================================================== consumo (o caminho quente)
    def licoes_para(self, pedido: Pedido) -> list[str]:
        """Uma vez por tentativa (ator) ou por planejamento: as lições que vão ao prompt, com a exposição gravada no
        ato. Leitura indexada das publicadas do app e um INSERT idempotente — nada de IA, nada de varrer o livro."""
        modo = self.modo_efetivo(pedido.app)
        if modo is Modo.OFF or not pedido.app:
            return []
        ajustes = self._ajustes()
        escolha = escolher(self._repo.publicadas(pedido.papel, pedido.app), pedido, ajustes.teto(pedido.papel),
                           holdout_publicada=ajustes.holdout_publicada)
        papel = pedido.papel.value
        if not escolha.escolhidas and not escolha.cortadas:
            return []
        if modo is not Modo.ON:
            self._contar("licao.sombra", len(escolha.textos), papel=papel)   # o que iria, sem ir
            return []
        if escolha.cortadas:
            self._contar("licao.cortada", escolha.cortadas, papel=papel)
        if not escolha.escolhidas:
            return []
        self._repo.expor([NovaExposicao(item_id=e.item.id, unit_id=pedido.unidade, role=papel,
                                        arm=e.braco or Braco.WITH, tokens=e.tokens, run_id=pedido.run_id,
                                        objective_id=pedido.objective_id, app_package=pedido.app,
                                        capability=pedido.capability, simulated=pedido.simulated)
                          for e in escolha.escolhidas], to_iso(self._relogio()))
        for e in escolha.escolhidas:
            self._contar("licao.exposta", 1, papel=papel, braco=(e.braco or Braco.WITH).value)
        return list(escolha.textos)

    def previa(self, *, app: str, papel: Papel, acao: str = "", etapa: str = "") -> Previa:
        """`GET /api/aprendizado/licoes/previa`: o bloco EXATO (todas as que cabem, como se estivessem no braço
        `with`) e os tokens. No planejador, a ação e a etapa não entram (a lição dele é do app)."""
        ator = papel is Papel.ACTOR
        pedido = Pedido(papel=papel, unidade="previa", run_id="", app=app,
                        capability=(acao or ("*" if etapa else "")) if ator else "",
                        step_hash=etapa if ator else "", simulated=True)
        teto = self._ajustes().teto(papel)
        escolha = escolher(self._repo.publicadas(papel, app), pedido, teto,
                           holdout_publicada=self._ajustes().holdout_publicada, sortear=False)
        return Previa(app=app, papel=papel, acao=pedido.capability, etapa=pedido.step_hash, modo=self.modo_efetivo(app), teto=teto,
                      escolha=escolha, bloco=bloco_de_licoes(list(escolha.textos)))

    # ================================================================== digest
    def preencher(self, run_id: str) -> int:
        """O desfecho das exposições desta execução, antes da purga de `ai_calls` (só execução real)."""
        return self._repo.preencher(run_id, to_iso(self._relogio()))

    def minerar_contrastes(self, run_id: str) -> int:
        if not self._algum_ligado():
            return 0
        feitos = 0
        for c in self._repo.contrastes(run_id):
            if self.modo_efetivo(c.app) is Modo.OFF:
                continue
            proposta = licao_de_contraste(c)
            if isinstance(proposta, Recusa):
                self._recusa(proposta.motivo.value)
                continue
            feitos += self._registrar(proposta, origem=f"attempt:{c.tentativa_boa}", run_id=c.run_id,
                                      instance_id=c.instance_id, app_version=c.app_version,
                                      detalhe=f"contraste {c.tipo.value}")
        return feitos

    def minerar_plano(self, run_id: str) -> int:
        if not self._algum_ligado():
            return 0
        feitos = 0
        for c in self._repo.contrastes_do_plano(run_id):
            if self.modo_efetivo(c.app) is Modo.OFF:
                continue
            proposta = licao_do_planejador(c)
            if isinstance(proposta, Recusa):
                self._recusa(proposta.motivo.value)
                continue
            for d in c.defeitos:                         # cada execução com o defeito é uma repetição
                feitos += self._registrar(proposta, origem=f"step:{d.step_id}", run_id=d.run_id,
                                          instance_id=d.instance_id, app_version=c.app_version,
                                          detalhe=f"defeito do plano; comprovou em {c.execucao_que_comprovou}")
        return feitos

    def _recusa(self, motivo: str) -> None:
        self._contar("aprendizado.recusa", 1, motivo=motivo, tipo=LivroKind.LICAO.value)

    def _registrar(self, novo: NovoItem, *, origem: str, run_id: str | None, instance_id: str | None,
                   app_version: str | None, detalhe: str, simulated: bool = False) -> int:
        """A candidata (ou a viva de mesma impressão) e a evidência a favor desta observação; depois, a promoção."""
        try:
            item = self._servico.propor(novo, run_id=run_id)
        except Vetado:
            self._recusa("vetada")
            return 0
        except NotaComCaraDeSegredo:
            self._recusa("segredo")
            return 0
        except ConflitoDeEstado:
            vivo = self._livro.item_vivo(novo)          # outra réplica criou no mesmo instante: soma nela
            if vivo is None:
                return 0
            item = vivo
        nova = self._livro.registrar_evidencia(NovaEvidencia(
            item_ref=item.id, stance=Posicao.FOR, origin_ref=origem, simulated=simulated, run_id=run_id,
            instance_id=instance_id, app_version=app_version, detail=detalhe))
        atual = self._livro.item(item.id)
        if atual is not None:
            self._avaliar(atual, run_id=run_id)
        return int(nova)

    # ================================================================== promoção (D1)
    def _mover(self, item: ItemDeAprendizado, para: SkillState, reason: str, *, detalhe: str | None = None,
               run_id: str | None = None) -> ItemDeAprendizado | None:
        try:
            self._servico.mudar_estado(LivroKind.LICAO, item.id, para, by=SYSTEM_ACTOR, reason=reason, run_id=run_id,
                                       detalhe=detalhe)
        except ErroDeAprendizado as exc:
            log.info("aprendizado: lição %s não foi a %s (%s: %s)", item.id, para.value, exc.code, exc)
            return None
        return self._livro.item(item.id)

    def _detalhar(self, item: ItemDeAprendizado, detalhe: str, reason: str, *,
                  app_version: str | None = None) -> ItemDeAprendizado | None:
        try:
            return self._livro.mudar_detalhe(item, detalhe, by=SYSTEM_ACTOR, reason=reason, app_version=app_version)
        except ErroDeAprendizado as exc:
            log.info("aprendizado: lição %s não foi a %s (%s)", item.id, detalhe, exc.code)
            return None

    def _avaliar(self, item: ItemDeAprendizado, *, run_id: str | None = None) -> None:
        """Candidata → validada pela repetição; validada sem efeito nem texto de pessoa → publicada na fila de prova
        (só no modo `on`). O que exige o dono para em `validated` ("Para aprovar"); texto de pessoa nem é validado
        pelo sistema."""
        if item.kind is not LivroKind.LICAO:
            return
        modo = self.modo_efetivo(item.escopo.app)
        if modo is Modo.OFF:
            return
        atual: ItemDeAprendizado | None = item
        if item.state is SkillState.CANDIDATE and not item.human_origin:
            v = veredito_de_repeticao(self._livro.evidencias(item.id), LIMIARES_DA_LICAO)
            if v.decisao is Decisao.CONTRADITA:
                self._mover(item, SkillState.DISABLED, f"contradita: {v.contra} observação(ões) contra",
                            detalhe=DetalheDeEstado.CONTRADITA.value, run_id=run_id)
                return
            atual = (self._mover(item, SkillState.VALIDATED,
                                 f"repetição: {v.execucoes} execuções reais, {v.a_favor} observações", run_id=run_id)
                     if v.decisao is Decisao.PROMOVE else None)
        if (atual is not None and atual.state is SkillState.VALIDATED and not atual.requires_owner
                and modo is Modo.ON):
            self._mover(atual, SkillState.PUBLISHED, "sem efeito externo e repetida (D1): entra na fila de prova",
                        detalhe=DetalheDeEstado.FILA_DE_PROVA.value, run_id=run_id)

    # ================================================================== curadoria
    def curar(self, agora: datetime) -> int:
        """Um passo da curadoria (a cada `aprendizado.curadoria_s`), idempotente. No modo `off` só preenche o que o
        digest deixou para trás; no `shadow` minera, valida e mede o que já existe, mas não publica nem aposenta por
        falta de exposição (sem prompt não há exposição)."""
        feitos = self._preencher_pendentes(agora)
        if not self._algum_ligado():
            return feitos
        feitos += self._notas(agora)
        licoes = self._livro.itens(kind=LivroKind.LICAO)
        for item in licoes:
            if item.state in (SkillState.CANDIDATE, SkillState.VALIDATED):
                self._avaliar(item)
        feitos += self._aposentar(agora)
        feitos += self._vereditos()
        feitos += self._abrir_provas()
        feitos += self._propor(agora)
        return feitos

    def _preencher_pendentes(self, agora: datetime) -> int:
        desde = to_iso(agora - timedelta(days=PREENCHER_DIAS))
        return sum(self._repo.preencher(run_id, to_iso(agora)) for run_id in self._repo.execucoes_a_preencher(desde))

    def _notas(self, agora: datetime) -> int:
        feitos = 0
        for nota in self._repo.notas(to_iso(agora - timedelta(days=NOTAS_DIAS))):
            if self.modo_efetivo(nota.app) is Modo.OFF:
                continue
            proposta = licao_de_nota(nota)
            if isinstance(proposta, Recusa):
                self._recusa(proposta.motivo.value)
                continue
            feitos += self._registrar(proposta, origem=f"signal:{nota.signal_id}", run_id=nota.run_id,
                                      instance_id=nota.instance_id, app_version=None, detalhe="nota de 'deu errado'",
                                      simulated=nota.simulated)
        return feitos

    def _publicadas(self) -> list[ItemDeAprendizado]:
        """As publicadas dos apps que não estão em `off`: o app desligado não se mede nem se aposenta (nada gravado
        sobre ele muda enquanto o dono não o religa)."""
        return [i for i in self._livro.itens(kind=LivroKind.LICAO, state=SkillState.PUBLISHED)
                if self.modo_efetivo(i.escopo.app) is not Modo.OFF]

    def _aposentar(self, agora: datetime) -> int:
        feitos = 0
        for item in self._publicadas():
            refutacoes = sum(1 for e in self._livro.evidencias(item.id)
                             if e.stance is Posicao.AGAINST and not e.simulated and e.origin_ref.startswith("signal:"))
            ultima = self._repo.ultima_exposicao(item.id)
            saida = aposentadoria(detalhe=item.state_detail, desde=_instante(item.state_at), agora=agora,
                                  ultima_exposicao=_instante(ultima), refutacoes=refutacoes,
                                  absorvida_em=self._repo.absorvida(item.id),
                                  sem_exposicao_dias=(SEM_EXPOSICAO_DIAS if self.modo_efetivo(item.escopo.app) is Modo.ON
                                                        else None))
            if saida is not None:
                feitos += int(self._mover(item, saida.para, saida.motivo, detalhe=saida.detalhe) is not None)
                continue
            versao = self._repo.versao_atual(item.escopo.app) if item.escopo.app else None
            if volta_a_prova(item.state_detail, item.app_version, versao):
                feitos += int(self._detalhar(item, DetalheDeEstado.FILA_DE_PROVA.value,
                                             f"versão nova do app ({item.app_version} → {versao}): a medida volta a "
                                             "valer do zero", app_version=versao) is not None)
        return feitos

    def _vereditos(self) -> int:
        ajustes = self._ajustes()
        feitos = 0
        for item in self._publicadas():
            if item.state_detail != DetalheDeEstado.EM_PROVA.value:
                continue
            v = veredito_de_efeito(self._livro.exposicoes(item.id, desde=item.state_at, limite=100_000),
                                   minimo=ajustes.minimo_por_braco, maximo=ajustes.maximo_por_braco)
            if v.efeito is None:
                continue
            detalhe = DETALHE_DO_EFEITO[v.efeito].value
            if v.efeito is Efeito.AJUDA:
                feitos += int(self._detalhar(item, detalhe, v.resumo()) is not None)
            else:                                        # atrapalha desliga, neutra aposenta
                para = SkillState.DISABLED if v.efeito is Efeito.ATRAPALHA else SkillState.DEPRECATED
                feitos += int(self._mover(item, para, v.resumo(), detalhe=detalhe) is not None)
        return feitos

    def _abrir_provas(self) -> int:
        feitos = 0
        for item, detalhe in abrir_provas(self._publicadas()):
            motivo = ("a prova do escopo abriu" if detalhe is DetalheDeEstado.EM_PROVA
                      else "espera a prova da lição que está em prova neste escopo")
            feitos += int(self._detalhar(item, detalhe.value, motivo) is not None)
        return feitos

    def _propor(self, agora: datetime) -> int:
        agora_iso = to_iso(agora)
        return sum(int(self._repo.propor_promocao(item, agora_iso)) for item in self._publicadas()
                   if propor_promocao(item.state_detail, _instante(item.state_at), agora))

    # ================================================================== leitura para o painel
    def medida(self, item: ItemDeAprendizado) -> VereditoDeEfeito | None:
        """A medida da prova em curso ("faltam N" enquanto não há 8 por braço); `None` fora de prova."""
        if item.state is not SkillState.PUBLISHED or item.state_detail != DetalheDeEstado.EM_PROVA.value:
            return None
        ajustes = self._ajustes()
        return veredito_de_efeito(self._livro.exposicoes(item.id, desde=item.state_at, limite=100_000),
                                  minimo=ajustes.minimo_por_braco, maximo=ajustes.maximo_por_braco)


def _instante(iso: str | None) -> datetime | None:
    try:
        return parse_iso(iso)
    except ValueError:
        return None


__all__ = ["NOTAS_DIAS", "PREENCHER_DIAS", "AjustesDeLicoes", "Contador", "Previa", "RepositorioDeLicoes",
           "ServicoDeLicoes"]
