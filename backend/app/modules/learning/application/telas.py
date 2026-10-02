"""Pacote A8 do ADR-054: a fatia 5 (18.8) do ADR-052 — telas aprendidas, sem IA e sem Python por app.

O caso que ela resolve: a tela de CASA de um app muda numa atualização; o "voltar" da conferência da conta sai do app,
a reabertura volta à mesma tela, e a checagem termina em "o voltar saiu do app", chamando uma pessoa. A tela
desconhecida em geral já não chama ninguém (a sessão volta ao estado conhecido); a de casa, sim.

O ciclo, todo aqui e todo determinístico:

1. COLETA (`observar_tentativa`, no fechamento de cada tentativa): etapa `succeeded` e comprovada, o app da etapa em
   primeiro plano, fora do aparelho-loja, tela não protegida (nem sensível, nem senha, nem texto de verificação) e o
   YAML do repositório dizendo "desconhecida" → sinal `tela_vista` com até 60 ids estáveis e se a aba de perfil
   DECLARADA estava na tela. Das telas declaradas guardam-se até 20 amostras cada (a prova negativa). Nunca texto.
2. CANDIDATA (`minerar`, no digest da execução): ≥ `observacoes` observações da mesma tela viram uma regra de 2 a 4
   `ids_todos`, sempre `autenticada`, de casa só se TODA observação tinha a aba.
3. VALIDAÇÃO: ≥ `observacoes` observações reais em ≥ `execucoes` execuções, zero conflito e a prova local.
4. PUBLICAÇÃO sozinha (D1: sem efeito externo, sem texto de pessoa) só no modo `telas: on`; de fábrica é `observe`:
   grava, minera e valida, mas a sessão não consome.
5. DESLIGAM a regra: o primeiro conflito (login, desafio, 2FA ou conta errada no mesmo aparelho até 2 min de um uso),
   o modo fora de `on` (o interruptor), uma pessoa pelo livro e 30 dias sem casar numa versão nova do app.
6. PONTE para o repositório: `exportar` devolve o fragmento YAML (conferido pelo carregador); quando o YAML commitado
   reconhece todas as amostras, a aprendida se aposenta como `absorvida:<commit>`.

A sessão só consome a regra pelo fornecedor que a infraestrutura liga (`ligar_telas`), e a conta continua lida só pela
tela de perfil declarada: a tela aprendida nunca confirma conta sozinha.
"""
from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from weakref import WeakKeyDictionary, ref

from app.modules.learning.application.ports import NovaEvidencia, NovoSinal, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain import telas as dominio
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, ErroDeAprendizado, NaoEncontrado, SkillState,
                                               conferir_transicao)
from app.modules.learning.domain.livro import Escopo, ItemDeAprendizado, NovoItem
from app.modules.learning.domain.modo_por_app import modo_efetivo
from app.modules.learning.domain.promocao import Decisao, Limiares, veredito_de_repeticao
from app.modules.learning.domain.vocabulario import (LivroKind, ModoDeTelas, Polaridade, Posicao, SignalKind,
                                                     SourceKind, absorvida)
from app.modules.skills.domain.document import JsonObject, JsonValue
from app.util import parse_iso, to_iso

log = logging.getLogger("poc.aprendizado")

_VIVOS = frozenset({SkillState.CANDIDATE, SkillState.VALIDATED, SkillState.PUBLISHED})
#: Desfechos da conferência da conta que são conflito para a tela usada perto deles (`Outcome` da sessão).
DESFECHOS_DE_CONFLITO = frozenset({"auth_challenge", "wrong_account"})
#: Quantos dias de observações o minerador lê, e no máximo quantas (o digest roda a cada execução assentada).
JANELA_DE_OBSERVACOES_DIAS = 30
LIMITE_DE_OBSERVACOES = 500
#: A versão do minerador, na proveniência: muda quando a regra de candidata muda.
MINERADOR = "telas/1"


@dataclass(frozen=True, slots=True)
class AjustesDeTelas:
    """O bloco `aprendizado.telas` do config, como dado (a aplicação não importa `app.config`)."""

    modo: ModoDeTelas = ModoDeTelas.OBSERVE
    observacoes: int = 3
    execucoes: int = 2
    #: `aprendizado.telas.por_app`: o modo de cada pacote que sobrescreve o global (§8.10). Vazio = o global vale.
    por_app: Mapping[str, ModoDeTelas] = field(default_factory=dict)


# ------------------------------------------------------------------ o que a infraestrutura entrega
@dataclass(frozen=True, slots=True)
class TelaDaTentativa:
    """O que a infraestrutura tirou de UMA tentativa fechada (a árvore fica com ela): ids estáveis, sem texto."""

    pacote: str
    attempt_id: str
    run_id: str
    step_id: str
    instance_id: str
    status: str
    verified: bool
    simulated: bool
    loja: bool
    em_primeiro_plano: bool
    protegida: bool
    classificada: str                # a tela que o repositório reconhece, ou `dominio.DESCONHECIDA`
    tipo: str                        # o tipo dela (login, desafio, dois_fatores...), ou `dominio.DESCONHECIDA`
    ids: tuple[str, ...]             # vazio na tela protegida
    tem_aba: bool
    versao: str | None = None
    capability: str = "*"
    step_hash: str | None = None
    objective_id: str | None = None
    profile_id: str | None = None


@dataclass(frozen=True, slots=True)
class TelaDaSessao:
    """O que UMA conferência da conta viu (`sessao.ConferenciaDaSessao`, já sem árvore)."""

    pacote: str
    instance_id: str
    profile_id: str
    desfecho: str
    tipo: str
    tela_aprendida: str | None
    desconhecida: tuple[str, ...]    # ids da tela de onde o voltar saiu sem resolver (vazio: nenhuma)
    tem_aba: bool
    tentou_login: bool


class LeituraDeTelas(Protocol):
    """O que o ciclo lê dos sinais `tela_vista` e da evidência (a infraestrutura cumpre em SQL)."""

    def observacoes(self, app: str, *, desde: str, limite: int) -> list[dominio.Observacao]:
        """As telas DESCONHECIDAS vistas desde `desde`, as mais recentes primeiro."""
        ...

    def amostras(self, app: str, *, por_tela: int) -> list[dominio.Observacao]:
        """As amostras das telas DECLARADAS, até `por_tela` de cada."""
        ...

    def por_origem(self, app: str, origens: Sequence[str]) -> list[dominio.Observacao]: ...
    def quantas_amostras(self, app: str, tela: str) -> int: ...
    def apps_da_execucao(self, run_id: str) -> list[str]: ...
    def apps_com_telas(self) -> list[str]: ...

    def usos(self, app: str, instance_id: str, *, desde: str) -> list[dominio.Uso]:
        """A evidência a favor de telas aprendidas VIVAS do app, no aparelho, desde `desde`."""
        ...

    def versoes(self, app: str, *, desde: str) -> frozenset[str]: ...
    def ultima_a_favor(self, item_ref: str) -> str | None: ...


class ConhecimentoDeclarado(Protocol):
    """O que o REPOSITÓRIO declara de um app (`telas.yaml`), e o fragmento YAML conferido pelo carregador."""

    def declaradas(self, app: str) -> dominio.Declaradas | None: ...

    def fragmento(self, app: str, itens: Sequence[ItemDeAprendizado], *, commit: str | None, agora: str) -> str: ...


def _nada() -> None:
    return None


# ------------------------------------------------------------------ o serviço
class ServicoDeTelas:
    """Coleta, minera, valida, publica (D1), desliga, exporta e absorve as telas aprendidas. É também o minerador do
    digest e o passo da curadoria (`nome`, `minerar`, `executar`). Nenhuma IA."""

    nome = "telas"

    def __init__(self, livro: LearningService, repo: RepositorioDeAprendizado, leitura: LeituraDeTelas,
                 declarado: ConhecimentoDeclarado, *, ajustes: Callable[[], AjustesDeTelas],
                 relogio: Callable[[], datetime], commit: Callable[[], str | None],
                 ao_mudar: Callable[[], None] = _nada) -> None:
        """`ao_mudar`: chamado depois de cada transição de uma tela (a sessão relê as publicadas)."""
        self._livro = livro
        self._repo = repo
        self._leitura = leitura
        self._declarado = declarado
        self._ajustes = ajustes
        self._relogio = relogio
        self._commit = commit
        self._ao_mudar = ao_mudar

    @property
    def ajustes(self) -> AjustesDeTelas:
        return self._ajustes()

    def modo_efetivo(self, pacote: str | None) -> ModoDeTelas:
        """O modo que vale para `pacote` (§8.10): o override de `por_app`, senão o global; sem pacote, o global. É a
        ÚNICA regra: coleta, publicação (D1) e o fornecedor da sessão passam por aqui."""
        a = self.ajustes
        return modo_efetivo(a.modo, a.por_app, pacote)

    def _algum_ligado(self) -> bool:
        """Falso só quando o global e todos os overrides estão em `off`: o atalho barato do digest e da curadoria."""
        a = self.ajustes
        return a.modo is not ModoDeTelas.OFF or any(m is not ModoDeTelas.OFF for m in a.por_app.values())

    # ================================================================== coleta
    def observar_tentativa(self, t: TelaDaTentativa) -> int:
        """Uma tentativa fechada: marca de conflito (tela de login, desafio ou 2FA perto de um uso) e, na etapa
        comprovada sobre tela não protegida do app, a tela vista. Devolve quantas linhas gravou."""
        if self.modo_efetivo(t.pacote) is ModoDeTelas.OFF or t.loja or not t.pacote:
            return 0
        agora = self._relogio()
        feito = 0
        if t.tipo in dominio.TIPOS_DE_CONFLITO:
            # Login, desafio e 2FA nunca são aprendidos nem amostrados: só marcam o conflito (sem ids nem texto).
            if not t.simulated:
                feito += self._conflito(t.pacote, t.instance_id, agora, motivo=f"tela de {t.tipo}",
                                        origem=f"attempt:{t.attempt_id}", run_id=t.run_id)
            return feito
        if not (t.status == "succeeded" and t.verified) or not t.em_primeiro_plano or t.protegida or not t.ids:
            return feito
        desconhecida = t.classificada == dominio.DESCONHECIDA
        if not desconhecida and self._leitura.quantas_amostras(t.pacote, t.classificada) >= dominio.AMOSTRAS_POR_TELA:
            return feito
        ids: list[JsonValue] = [*t.ids]
        gravado = self._livro.registrar_sinal(NovoSinal(
            kind=SignalKind.TELA_VISTA, source_ref=f"attempt:{t.attempt_id}", created_by=SYSTEM_ACTOR,
            polarity=Polaridade.NEUTRAL, run_id=t.run_id, objective_id=t.objective_id, step_id=t.step_id,
            attempt_id=t.attempt_id, instance_id=t.instance_id, profile_id=t.profile_id, app_package=t.pacote,
            capability=t.capability or "*", step_hash=t.step_hash, step_verified=True,
            data={"ids": ids, "classificada": t.classificada, "tem_aba_de_perfil": t.tem_aba, "versao": t.versao},
            simulated=t.simulated))
        feito += int(gravado is not None)
        if desconhecida:
            # A evidência nas telas vivas entra JÁ: é ela que marca o uso que a janela de conflito lê.
            feito += self._evidenciar(t.pacote, [dominio.Observacao(
                origem=f"attempt:{t.attempt_id}", ids=frozenset(t.ids), classificada=t.classificada,
                tem_aba=t.tem_aba, run_id=t.run_id, instance_id=t.instance_id, simulated=t.simulated,
                contexto=t.capability or "*", versao=t.versao, quando=to_iso(agora))])
        return feito

    def observar_sessao(self, s: TelaDaSessao) -> int:
        """Uma conferência da conta: o uso da tela aprendida em que ela parou (a favor, ou conflito com o desfecho) e,
        quando o voltar saiu do app sem resolver, a tela desconhecida (sinal `tela_desconhecida_chamou_pessoa`, que
        nunca promove nada: não veio de etapa comprovada)."""
        if self.modo_efetivo(s.pacote) is ModoDeTelas.OFF or not s.pacote:
            return 0
        agora = self._relogio()
        momento = to_iso(agora)
        origem = f"sessao:{s.instance_id}:{momento}"
        usada = self._item_da_tela(s.pacote, s.tela_aprendida) if s.tela_aprendida else None
        feito = 0
        if s.desfecho in DESFECHOS_DE_CONFLITO or s.tipo in dominio.TIPOS_DE_CONFLITO or s.tentou_login:
            motivo = s.desfecho if s.desfecho in DESFECHOS_DE_CONFLITO else f"tela de {s.tipo}"
            feito += self._conflito(s.pacote, s.instance_id, agora, motivo=f"conferência da conta: {motivo}",
                                    origem=origem, tambem=(usada.id,) if usada is not None else ())
        elif usada is not None and s.desfecho == "session_ready":
            feito += int(self._repo.registrar_evidencia(NovaEvidencia(
                item_ref=usada.id, stance=Posicao.FOR, origin_ref=origem, simulated=False,
                instance_id=s.instance_id, detail="a conferência da conta parou nela e leu a conta")))
        if s.desconhecida:
            ids: list[JsonValue] = [*s.desconhecida]
            gravado = self._livro.registrar_sinal(NovoSinal(
                kind=SignalKind.TELA_DESCONHECIDA_CHAMOU_PESSOA,
                source_ref=f"sessao:{s.instance_id}:{dominio.assinatura(s.desconhecida)}:{momento[:10]}",
                created_by=SYSTEM_ACTOR, polarity=Polaridade.NEGATIVE, instance_id=s.instance_id,
                profile_id=s.profile_id, app_package=s.pacote,
                data={"ids": ids, "tem_aba_de_perfil": s.tem_aba}))
            feito += int(gravado is not None)
        return feito

    # ================================================================== digest e curadoria
    def minerar(self, run_id: str) -> int:
        """Minerador do digest: os apps com tela desconhecida vista nesta execução são minerados de novo. A candidata
        que nasce aqui leva o `run_id` no nascimento: foi esta execução que a fez passar da repetição mínima, e o
        "Aprendizado desta execução" a mostra entre as candidatas geradas."""
        if not self._algum_ligado():
            return 0
        return sum(self.minerar_app(app, run_id=run_id) for app in self._leitura.apps_da_execucao(run_id)
                   if self.modo_efetivo(app) is not ModoDeTelas.OFF)

    def executar(self, agora: datetime) -> int:
        """Passo da curadoria: valida e publica o que ficou para trás, absorve e aposenta."""
        if not self._algum_ligado():
            return 0
        feito = 0
        for app in self._leitura.apps_com_telas():
            if self.modo_efetivo(app) is ModoDeTelas.OFF:
                continue
            declaradas = self._declarado.declaradas(app)
            if declaradas is None:
                continue
            amostras = self._leitura.amostras(app, por_tela=dominio.AMOSTRAS_POR_TELA)
            feito += self._absorver(app, declaradas)
            feito += self._avaliar(app, amostras)
            feito += self._aposentar(app, agora)
        return feito

    def minerar_app(self, app: str, *, run_id: str | None = None) -> int:
        """`run_id`: a execução cujo digest minera (vai só no nascimento da candidata; a validação e a publicação
        que vêm logo depois dependem de observações de várias execuções e ficam sem ele)."""
        declaradas = self._declarado.declaradas(app)
        if declaradas is None:
            return 0
        aj = self.ajustes
        desde = to_iso(self._relogio() - timedelta(days=JANELA_DE_OBSERVACOES_DIAS))
        positivas = self._leitura.observacoes(app, desde=desde, limite=LIMITE_DE_OBSERVACOES)
        amostras = self._leitura.amostras(app, por_tela=dominio.AMOSTRAS_POR_TELA)
        feito = self._evidenciar(app, positivas)
        regras = [r for _, r in self._vivas(app)]
        livres = [o for o in positivas if not any(r.casa_com(o.ids) for r in regras)]
        for candidata in dominio.propor(livres, amostras, declaradas, minimo=aj.observacoes):
            item = self._nascer(app, candidata, run_id=run_id)
            if item is not None:
                feito += 1 + self._evidenciar(app, candidata.observacoes, so=item.id)
        return feito + self._avaliar(app, amostras)

    # ================================================================== ponte para o repositório
    def exportar(self, app: str) -> str:
        """O fragmento YAML das telas validadas e publicadas do app, com a proveniência, conferido pelo carregador."""
        itens = [i for i in self._repo.itens(kind=LivroKind.TELA) if i.escopo.app == app
                 and i.state in (SkillState.VALIDATED, SkillState.PUBLISHED)]
        if not itens:
            raise NaoEncontrado(f"Nenhuma tela validada ou publicada de {app} para exportar.")
        return self._declarado.fragmento(app, itens, commit=self._commit(), agora=to_iso(self._relogio()))

    def publicadas(self, app: str) -> list[tuple[str, JsonObject]]:
        """(id, conteúdo) das telas PUBLICADAS do app: o que o fornecedor da sessão entrega no modo `on`."""
        return [(i.id, i.content) for i in self._repo.itens(kind=LivroKind.TELA, state=SkillState.PUBLISHED)
                if i.escopo.app == app]

    def deixa_um_fora(self, app: str, sem: str, *, minimo: int | None = None) -> list[dominio.Candidata]:
        """Só leitura: as candidatas que sairiam das observações reais se o repositório não tivesse a tela `sem`."""
        declaradas = self._declarado.declaradas(app)
        if declaradas is None:
            raise NaoEncontrado(f"{app} não declara telas.")
        desde = to_iso(self._relogio() - timedelta(days=JANELA_DE_OBSERVACOES_DIAS))
        observacoes = [*self._leitura.observacoes(app, desde=desde, limite=LIMITE_DE_OBSERVACOES),
                       *self._leitura.amostras(app, por_tela=dominio.AMOSTRAS_POR_TELA)]
        return dominio.deixa_um_fora(declaradas, sem, observacoes,
                                     minimo=self.ajustes.observacoes if minimo is None else minimo)

    # ================================================================== apoio
    def _vivas(self, app: str) -> list[tuple[ItemDeAprendizado, dominio.RegraAprendida]]:
        saida: list[tuple[ItemDeAprendizado, dominio.RegraAprendida]] = []
        for item in self._repo.itens(kind=LivroKind.TELA):
            if item.escopo.app != app or item.state not in _VIVOS:
                continue
            regra = dominio.regra_do_conteudo(item.content)
            if regra is None:
                log.warning("aprendizado: tela %s com conteúdo inválido (ignorada)", item.id)
                continue
            saida.append((item, regra))
        return saida

    def _item_da_tela(self, app: str, tela: str | None) -> ItemDeAprendizado | None:
        return next((item for item, regra in self._vivas(app) if regra.tela == tela), None)

    def _evidenciar(self, app: str, observacoes: Sequence[dominio.Observacao], *, so: str | None = None) -> int:
        """Cada observação que a regra de uma tela VIVA reconhece vira evidência dela (idempotente pela origem). Na
        tela de casa, a observação sem a aba de perfil é CONTRA: a casa exige a aba em toda observação."""
        feito = 0
        for item, regra in self._vivas(app):
            if so is not None and item.id != so:
                continue
            for o in observacoes:
                if not regra.casa_com(o.ids):
                    continue
                posicao = Posicao.FOR if (o.tem_aba or not regra.casa) else Posicao.AGAINST
                feito += int(self._repo.registrar_evidencia(NovaEvidencia(
                    item_ref=item.id, stance=posicao, origin_ref=o.origem, simulated=o.simulated, run_id=o.run_id,
                    instance_id=o.instance_id, app_version=o.versao,
                    detail=None if posicao is Posicao.FOR else "tela de casa vista sem a aba de perfil")))
        return feito

    def _nascer(self, app: str, c: dominio.Candidata, *, run_id: str | None = None) -> ItemDeAprendizado | None:
        versoes = Counter(o.versao for o in c.observacoes if o.versao)
        observacoes: list[JsonValue] = [o.origem for o in c.observacoes[:20]]
        execucoes: list[JsonValue] = [r for r in sorted({o.run_id for o in c.observacoes if o.run_id})[:20]]
        aparelhos: list[JsonValue] = [a for a in sorted({o.instance_id for o in c.observacoes if o.instance_id})[:20]]
        vistas: list[JsonValue] = [v for v in sorted(versoes)[:10]]
        proveniencia: JsonObject = {"minerador": MINERADOR, "observacoes": observacoes, "execucoes": execucoes,
                                    "aparelhos": aparelhos, "versoes": vistas, "contexto": c.contexto}
        casa = ", de casa" if c.regra.casa else ""
        try:
            return self._livro.propor(NovoItem(
                kind=LivroKind.TELA, escopo=Escopo(app=app), content=c.regra.conteudo(),
                summary=f"Tela aprendida {c.regra.tela} ({len(c.regra.ids_todos)} ids{casa})",
                source_kind=SourceKind.SCREEN_OBSERVATION, side_effect=False,
                app_version=versoes.most_common(1)[0][0] if versoes else None, provenance=proveniencia),
                run_id=run_id)
        except ErroDeAprendizado as exc:
            log.info("aprendizado: a candidata de tela %s não nasceu (%s)", c.regra.tela, exc)
            return None

    def _avaliar(self, app: str, amostras: Sequence[dominio.Observacao]) -> int:
        """Candidata com repetição e prova local → validada; validada no modo `on` → publicada (D1). Contradita →
        desligada."""
        aj = self.ajustes
        limiares = Limiares(n_min=aj.observacoes, execucoes_min=aj.execucoes, aparelhos_min=1, contra_max=0)
        feito = 0
        for item, regra in self._vivas(app):
            if item.state is SkillState.PUBLISHED:
                continue
            evidencias = self._repo.evidencias(item.id)
            v = veredito_de_repeticao(evidencias, limiares)
            if v.decisao is Decisao.CONTRADITA:
                feito += self._mover(item, SkillState.DISABLED,
                                     f"contradita: {v.contra} observação(ões) contra ou em conflito")
                continue
            atual: ItemDeAprendizado | None = item
            if item.state is SkillState.CANDIDATE:
                if v.decisao is not Decisao.PROMOVE:
                    continue
                positivas = self._leitura.por_origem(
                    app, [e.origin_ref for e in evidencias if e.stance is Posicao.FOR and not e.simulated])
                prova = dominio.provar(regra, positivas, amostras)
                if not prova.ok:
                    log.info("aprendizado: a tela %s não passou na prova local (%s)", regra.tela, prova.descrever())
                    continue
                if not self._mover(item, SkillState.VALIDATED,
                                   f"repetição: {v.a_favor} observações em {v.execucoes} execuções e {v.aparelhos} "
                                   f"aparelho(s); prova local: {prova.descrever()}"):
                    continue
                feito += 1
                atual = self._repo.item(item.id)
            if atual is not None and atual.state is SkillState.VALIDATED and self.modo_efetivo(app) is ModoDeTelas.ON:
                feito += self._mover(atual, SkillState.PUBLISHED,
                                     "publicação sozinha (D1): sem efeito externo, repetida e com prova local")
        return feito

    def _conflito(self, app: str, instance_id: str, quando: datetime, *, motivo: str, origem: str,
                  run_id: str | None = None, tambem: Sequence[str] = ()) -> int:
        """O PRIMEIRO conflito desliga, sem exceção de idade: login, desafio, 2FA ou conta errada no mesmo aparelho
        até `JANELA_DE_CONFLITO_S` de um uso da tela aprendida."""
        desde = to_iso(quando - timedelta(seconds=dominio.JANELA_DE_CONFLITO_S))
        usos = self._leitura.usos(app, instance_id, desde=desde)
        refs = sorted({*dominio.em_conflito(usos, instance_id=instance_id, quando=quando), *tambem})
        feito = 0
        for item_ref in refs:
            item = self._repo.item(item_ref)
            if item is None or item.kind is not LivroKind.TELA or item.state not in _VIVOS:
                continue
            self._repo.registrar_evidencia(NovaEvidencia(item_ref=item_ref, stance=Posicao.CONFLICT, origin_ref=origem,
                                                         simulated=False, run_id=run_id, instance_id=instance_id,
                                                         detail=motivo[:200]))
            feito += self._mover(item, SkillState.DISABLED,
                                 f"conflito: {motivo} no aparelho {instance_id} até {dominio.JANELA_DE_CONFLITO_S} s "
                                 "de um uso da tela", run_id=run_id)
        return feito

    def _absorver(self, app: str, declaradas: dominio.Declaradas) -> int:
        """A tela que o repositório agora reconhece em TODAS as amostras se aposenta como `absorvida:<commit>`."""
        feito = 0
        for item, _regra in self._vivas(app):
            origens = [e.origin_ref for e in self._repo.evidencias(item.id) if e.stance is Posicao.FOR]
            tela = dominio.absorvida_por(declaradas, self._leitura.por_origem(app, origens))
            if tela is None:
                continue
            para = SkillState.DEPRECATED if item.state is SkillState.PUBLISHED else SkillState.DISABLED
            feito += self._mover(item, para, f"absorvida pelo repositório: a tela '{tela}' do telas.yaml reconhece "
                                             "todas as amostras", detalhe=absorvida(self._commit() or "desconhecido"))
        return feito

    def _aposentar(self, app: str, agora: datetime) -> int:
        feito = 0
        versoes = self._leitura.versoes(app, desde=to_iso(agora - timedelta(days=dominio.DIAS_SEM_CASAR)))
        for item, _regra in self._vivas(app):
            criada = parse_iso(item.created_at)
            if item.state is not SkillState.PUBLISHED or criada is None:
                continue
            ultima = self._leitura.ultima_a_favor(item.id)
            if dominio.sem_casar(ultima_a_favor=parse_iso(ultima) if ultima else None, criada=criada,
                                 versao_do_item=item.app_version, versoes_recentes=versoes, agora=agora):
                feito += self._mover(item, SkillState.DEPRECATED,
                                     f"{dominio.DIAS_SEM_CASAR} dias sem casar numa versão nova do app")
        return feito

    def _mover(self, item: ItemDeAprendizado, para: SkillState, reason: str, *, detalhe: str | None = None,
               run_id: str | None = None) -> int:
        """Uma transição do SISTEMA, com a trilha. A promoção passa pelo livro (D1, modo e veto); o rebaixamento com
        detalhe (`absorvida:<commit>`) confere a tabela e vai ao repositório. Recusa vira log, nunca exceção."""
        try:
            if detalhe is None:
                self._livro.mudar_estado(LivroKind.TELA, item.id, para, by=SYSTEM_ACTOR, reason=reason, run_id=run_id)
            else:
                conferir_transicao(item.state, para, SYSTEM_ACTOR, side_effect=item.side_effect,
                                   human_origin=item.human_origin,
                                   modo_publica=self.modo_efetivo(item.escopo.app) is ModoDeTelas.ON)
                self._repo.transicionar_item(item, para, by=SYSTEM_ACTOR, reason=reason, detalhe=detalhe,
                                             run_id=run_id)
        except ErroDeAprendizado as exc:
            log.info("aprendizado: a tela %s não foi de %s para %s (%s)", item.id, item.state, para, exc)
            return 0
        self._ao_mudar()
        return 1


# ------------------------------------------------------------------ onde a apresentação acha o serviço
#: Por livro, e com referência FRACA também no valor: o serviço das telas guarda o livro, e um valor forte manteria a
#: chave viva para sempre (quem o mantém vivo é o próprio livro, que o tem como minerador e passo da curadoria).
_TELAS: WeakKeyDictionary[LearningService, ref[ServicoDeTelas]] = WeakKeyDictionary()


def anexar_telas(livro: LearningService, telas: ServicoDeTelas) -> None:
    _TELAS[livro] = ref(telas)


def servico_de_telas(livro: LearningService) -> ServicoDeTelas | None:
    guardado = _TELAS.get(livro)
    return guardado() if guardado is not None else None


__all__ = ["DESFECHOS_DE_CONFLITO", "AjustesDeTelas", "ConhecimentoDeclarado", "LeituraDeTelas", "ServicoDeTelas",
           "TelaDaSessao", "TelaDaTentativa", "anexar_telas", "servico_de_telas"]
