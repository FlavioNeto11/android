"""Pacote A5 do ADR-054: o D1 nos conhecimentos NATIVOS — fluxo, receita e habilidade continuam donos do próprio
conteúdo; o livro decide quando o sistema pode publicar e guarda a trilha.

- **Fluxo.** O aprendido de execução nasce `candidate` (inerte: `FlowStore.match` só casa o ativo) e é validado por
  SOMBRA, de custo zero, no digest: a execução seguinte do mesmo comando já chamou o planejador (o candidato não age),
  e o plano dela é comparado ao do candidato — sequência de ação (capability, ou a identidade da etapa livre), efeito
  externo, tipo da pós-condição e parâmetros. `aprendizado.fluxo.concordancias` execuções reais concordantes além da
  que o gerou validam; sem etapa de efeito, o sistema publica; com efeito, ele para em `validated` e vai para "Para
  aprovar". Duas discordâncias reais desligam (rebaixar é automático).
- **Receita.** A promoção em sombra do `RecipeStore` para em `validated` quando há ação `commit` (lá mesmo, na loja);
  aqui ficam o veto (o caminho que uma PESSOA desligou não volta pelo sistema) e a trilha das mudanças da loja.
- **Evidência inválida (30.23).** O que foi desligado por ela só é barrado de renascer da MESMA execução: outra
  execução real ensina de novo (o fluxo na mesma linha, a receita numa versão nova), e o reaprendido para em
  `validated` (a loja da receita pergunta `receita_reaprendida`; a sombra do fluxo, a entrada do livro) e espera o dono.
  A evidência da execução invalidada não conta na sombra.
- **Habilidade.** O primeiro escritor real de `skill_validation_results` (043): toda execução de versão grava a
  observação — `proof=real` só de execução real — num caso `device` da própria versão, criado sozinho. A execução
  `completed` com etapa confirmada à mão (`verified=false`) vira `uncertain`, nunca `passed`. O sistema pode fazer
  `candidate → validated` quando os casos passam; publicar continua sendo de uma pessoa (o ciclo das habilidades é
  mais estrito que o D1).

Nenhuma IA em lugar nenhum daqui, e só evidência real (`runs.simulated=0`) conta para validar ou desligar: a execução
simulada deixa a sua linha em `learning_evidence` (serve a teste) e não muda nada. As transições do sistema passam
por `LearningService.mudar_estado(by='sistema')` — o D1 do domínio e a segunda camada do repositório valem aqui.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.modules.learning.application.ports import NovaEvidencia, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, Actor, ErroDeAprendizado, SkillState, actor_of,
                                               motivo_do_veto)
from app.modules.learning.domain.evidencia_invalida import Renascimento, reaprendizado, run_invalidada
from app.modules.learning.domain.livro import escopo_do_fluxo, ref_da_trilha
from app.modules.learning.domain.promocao import Decisao, Evidencia, Limiares, veredito_de_repeticao
from app.modules.learning.domain.vocabulario import LivroKind, Posicao
from app.modules.skills.domain.matching import bind_template_parameters, extract_parameters
from app.modules.skills.domain.refs import is_legacy_skill_id
from app.modules.skills.domain.validation import Outcome, Proof

log = logging.getLogger("poc.aprendizado")

#: A linha do tempo da execução: `(texto, run_id)`. Cada transição do sistema vira uma decisão na execução que a causou.
Decidir = Callable[[str, str], None]

#: Discordâncias reais que desligam um fluxo em prova.
DISCORDANCIAS_QUE_DESLIGAM = 2


# ------------------------------------------------------------------ o plano como assinatura comparável
@dataclass(frozen=True, slots=True)
class PassoAssinado:
    """Uma etapa reduzida ao que decide se dois planos fazem o mesmo caminho (título e objetivo ficam de fora: o
    planejador os reescreve a cada vez)."""

    acao: str            # a capability do catálogo, ou `etapa:<identidade da etapa-modelo>` na etapa livre
    side_effect: bool
    pos: str             # o tipo da pós-condição


@dataclass(frozen=True, slots=True)
class AssinaturaDoPlano:
    passos: tuple[PassoAssinado, ...]
    #: Os parâmetros do plano, sem os reservados (`instance_id`, `run_id`...). No plano-modelo, `{nome}` é o valor que
    #: vem do comando; o resto é valor fixo, que a execução comparada precisa repetir.
    parametros: Mapping[str, str]


def comparar(candidato: AssinaturaDoPlano, execucao: AssinaturaDoPlano) -> str | None:
    """`None` quando a execução fez o plano do candidato; senão, o porquê (sem valor de parâmetro nem texto de tela:
    vai para `learning_evidence.detail`)."""
    if len(candidato.passos) != len(execucao.passos):
        return f"{len(execucao.passos)} etapa(s) no plano, {len(candidato.passos)} no candidato"
    for i, (esperado, feito) in enumerate(zip(candidato.passos, execucao.passos), start=1):
        if esperado.acao != feito.acao:
            return f"etapa {i}: outra ação"
        if esperado.side_effect != feito.side_effect:
            return f"etapa {i}: efeito externo diferente"
        if esperado.pos != feito.pos:
            return f"etapa {i}: outra pós-condição ({feito.pos} × {esperado.pos})"
    if set(candidato.parametros) != set(execucao.parametros):
        return "outros parâmetros"
    for nome, valor in candidato.parametros.items():
        if valor != "{" + nome + "}" and execucao.parametros.get(nome) != valor:
            return f"valor fixo de '{nome}' diferente"
    return None


def marca_do_conteudo(content_hash: str) -> str:
    """O prefixo de `learning_evidence.detail` que amarra a evidência ao CONTEÚDO comparado. A linha do fluxo refutado
    renasce com outro plano (mesmo `fluxo:<id>`); sem a marca, as discordâncias da encarnação anterior desligariam a
    nova no primeiro digest. É texto fixo, não horário: dois registros no mesmo milissegundo não confundem nada."""
    return f"[{content_hash[:12]}]"


# ------------------------------------------------------------------ o que a leitura entrega
@dataclass(frozen=True, slots=True)
class ExecucaoAssentada:
    run_id: str
    comando: str
    simulada: bool
    aparelho: str | None
    #: `None` quando a execução não serve de comparação: não terminou `completed`, reaproveitou fluxo ou habilidade
    #: (o planejador não foi chamado), tem etapa confirmada à mão, ou o plano não é legível.
    assinatura: AssinaturaDoPlano | None


@dataclass(frozen=True, slots=True)
class FluxoEmProva:
    """Um fluxo `candidate` ou `validated` aprendido de execução (o de treino é da pessoa e não passa por aqui)."""

    id: str
    estado: SkillState
    comando_modelo: str
    assinatura: AssinaturaDoPlano
    efeito: bool
    content_hash: str
    nasceu_de: str | None           # `flows.source_run_id`: a execução que gerou ESTA encarnação


@dataclass(frozen=True, slots=True)
class ExecucaoDeHabilidade:
    run_id: str
    skill_id: str
    versao: int
    status: str
    simulada: bool
    aparelho: str | None
    #: Execução `completed` com alguma etapa `succeeded` sem prova da tela ("Confirmar concluído", `verified=false`),
    #: em qualquer versão do plano: a mesma regra que tira a execução da sombra dos fluxos (`flows.confirmada_a_mao`).
    #: Sempre `False` fora de `completed` — só o sucesso depende disto.
    confirmada_a_mao: bool


@dataclass(frozen=True, slots=True)
class ObservacaoDeHabilidade:
    skill_id: str
    versao: int
    run_id: str
    prova: Proof
    desfecho: Outcome
    aparelho: str | None
    detalhe: str


# ------------------------------------------------------------------ portas
class LeituraNativa(Protocol):
    def execucao(self, run_id: str) -> ExecucaoAssentada | None: ...
    def fluxos_em_prova(self) -> list[FluxoEmProva]: ...
    def execucao_de_habilidade(self, run_id: str) -> ExecucaoDeHabilidade | None: ...


class PortaDeValidacao(Protocol):
    """A escrita em `skill_validation_results` pela porta das habilidades (o livro nunca escreve nas tabelas delas)."""

    def registrar(self, observacao: ObservacaoDeHabilidade) -> bool:
        """Grava a observação (idempotente pela execução). `False`: já estava gravada, ou a versão não recebe prova."""
        ...

    def validar(self, skill_id: str, versao: int, motivo: str) -> bool:
        """`candidate → validated` pelo sistema, se os casos da versão passam. `False` sem mudar nada no resto."""
        ...


# ------------------------------------------------------------------ o D1 do lado das lojas
class D1Nativo:
    """O que as lojas (`FlowStore`, `RecipeStore`) perguntam ao livro antes de escrever: com que status o fluxo nasce e
    se o caminho está vetado. Leitura pura da trilha; nada aqui escreve."""

    def __init__(self, repo: RepositorioDeAprendizado, *, com_prova: Callable[[], bool],
                 relogio: Callable[[], datetime], execucao_real: Callable[[str], bool] = lambda _run: False) -> None:
        """`com_prova`: `aprendizado.fluxo.com_prova` VIGENTE (lido a cada nascimento; `False` = o modo anterior).
        `execucao_real(run_id)`: a execução existe e não é simulada (`runs.simulated=0`). Sem o leitor, nenhuma é real,
        e o que a evidência inválida desligou não renasce (o lado seguro)."""
        self._repo = repo
        self._com_prova = com_prova
        self._relogio = relogio
        self._execucao_real = execucao_real

    def _renascimento(self, run_id: str | None) -> Renascimento | None:
        return Renascimento(run_id=run_id, real=bool(self._execucao_real(run_id))) if run_id else None

    def fluxo_ao_nascer(self, match_key: str, content_hash: str | None, reaproveita: str | None,
                        run_id: str | None = None) -> str | None:
        """O status do fluxo que nasce da execução `run_id`, ou `None` (não aprende).

        - reaproveitar a linha desligada só quando quem a desligou foi o SISTEMA (a sombra refutou), ou quando ela foi
          desligada por EVIDÊNCIA INVÁLIDA de outra execução e esta é real (30.23): o que uma pessoa desligou — pelo
          livro, pela rota antiga (`PUT /api/flows`, que também grava a trilha) ou numa linha de antes da trilha, sem
          linha nenhuma — fica desligado;
        - o conteúdo vetado (`motivo_do_veto`: desligado por pessoa, pelo sistema há menos de 90 dias, ou por evidência
          inválida da mesma execução) não volta;
        - `com_prova` desligado é o modo anterior: comando novo nasce ativo, e nada é reaprendido.
        """
        renasce = self._renascimento(run_id)
        if reaproveita is not None and not self._pode_reaproveitar(reaproveita, renasce):
            return None
        if content_hash is not None and motivo_do_veto(
                self._repo.desligamentos(content_hash, escopo_do_fluxo(match_key)), agora=self._relogio(),
                app_version=None, renascimento=renasce) is not None:
            return None
        if not self._com_prova():
            return None if reaproveita is not None else "active"
        return "candidate"

    def receita_vetada(self, content_hash: str, scope_key: str, app_version: str | None,
                       run_id: str | None = None) -> bool:
        """O caminho da receita que uma PESSOA desligou não volta pelo sistema (nem nascendo, nem pela sombra).

        Só a decisão de pessoa conta aqui: a quarentena do sistema (3 falhas seguidas) sempre deixou a etapa ser
        reaprendida, e a receita nova volta a provar-se em sombra antes de agir. A evidência inválida (30.23) conta
        venha de quem vier, e barra só a mesma execução (`run_id`, a da etapa de que a receita é aprendida).
        """
        historico = [d for d in self._repo.desligamentos(content_hash, scope_key)
                     if actor_of(d.decided_by) is Actor.PERSON or run_invalidada(d.reason) is not None]
        return motivo_do_veto(historico, agora=self._relogio(), app_version=app_version,
                              renascimento=self._renascimento(run_id)) is not None

    def receita_reaprendida(self, scope_key: str, recipe_id: int) -> bool:
        """30.23: a receita (re)nascida no escopo de uma evidência inválida espera o dono — a promoção em sombra da
        loja para em `validated` (classe B forçada), mesmo sem ação de efeito externo."""
        return reaprendizado(self._repo.trilha_do_escopo(scope_key),
                             ref_da_trilha(LivroKind.RECEITA, str(recipe_id))) is not None

    def _pode_reaproveitar(self, flow_id: str, renasce: Renascimento | None) -> bool:
        trilha = self._repo.trilha(ref_da_trilha(LivroKind.FLUXO, flow_id))
        if not trilha:
            return False
        ultima = trilha[-1]
        if ultima.to_state is not SkillState.DISABLED:
            return False
        run = run_invalidada(ultima.reason)
        if run is not None:
            return renasce is not None and renasce.real and renasce.run_id != run
        return actor_of(ultima.decided_by) is Actor.SYSTEM


# ------------------------------------------------------------------ a sombra dos fluxos (minerador do digest)
class SombraDosFluxos:
    """Minerador do digest: a evidência de cada fluxo em prova e as transições do D1 que ela decide."""

    nome = "fluxos_d1"

    def __init__(self, servico: LearningService, repo: RepositorioDeAprendizado, leitura: LeituraNativa, *,
                 concordancias: Callable[[], int], decidir: Decidir | None = None) -> None:
        self._servico = servico
        self._repo = repo
        self._leitura = leitura
        self._concordancias = concordancias
        self._decidir = decidir

    def minerar(self, run_id: str) -> int:
        execucao = self._leitura.execucao(run_id)
        if execucao is None:
            return 0
        gravadas = 0
        for fluxo in self._leitura.fluxos_em_prova():
            posicao = self._posicao(fluxo, execucao)
            if posicao is None:
                continue
            stance, detalhe = posicao
            nova = self._repo.registrar_evidencia(NovaEvidencia(
                item_ref=ref_da_trilha(LivroKind.FLUXO, fluxo.id), stance=stance, origin_ref=f"run:{run_id}",
                simulated=execucao.simulada, run_id=run_id, instance_id=execucao.aparelho,
                detail=f"{marca_do_conteudo(fluxo.content_hash)} {detalhe}"))
            if not nova:
                continue                                  # o digest desta execução já passou por aqui
            gravadas += 1
            if fluxo.nasceu_de == run_id:
                self._anunciar(run_id, self._texto_do_nascimento(fluxo, execucao.simulada))
            try:
                self._avaliar(fluxo, run_id)
            except ErroDeAprendizado as exc:            # veto, conflito, D1: um fluxo não para os outros
                log.info("aprendizado: fluxo %s segue como está: %s", fluxo.id, exc)
        return gravadas

    def _posicao(self, fluxo: FluxoEmProva, execucao: ExecucaoAssentada) -> tuple[Posicao, str] | None:
        if fluxo.nasceu_de == execucao.run_id:
            return Posicao.FOR, "a execução que o gerou"
        if execucao.assinatura is None or not _casa(fluxo, execucao.comando):
            return None
        motivo = comparar(fluxo.assinatura, execucao.assinatura)
        return (Posicao.FOR, "mesmo plano") if motivo is None else (Posicao.AGAINST, motivo)

    def _avaliar(self, fluxo: FluxoEmProva, run_id: str) -> None:
        marca = marca_do_conteudo(fluxo.content_hash)
        ref = ref_da_trilha(LivroKind.FLUXO, fluxo.id)
        # 30.23: a execução marcada como evidência inválida não prova nada, nem para a encarnação nova da mesma linha
        invalidas = {r for t in self._repo.trilha(ref) if (r := run_invalidada(t.reason)) is not None}
        evidencias = [e for e in self._repo.evidencias(ref)
                      if (e.detail or "").startswith(marca) and e.run_id not in invalidas]
        exigidas = 1 + max(0, int(self._concordancias()))
        v = veredito_de_repeticao(evidencias, Limiares(n_min=exigidas, execucoes_min=exigidas, aparelhos_min=1,
                                                       contra_max=DISCORDANCIAS_QUE_DESLIGAM - 1))
        if v.decisao is Decisao.CONTRADITA:
            self._servico.mudar_estado(
                LivroKind.FLUXO, fluxo.id, SkillState.DISABLED, by=SYSTEM_ACTOR, run_id=run_id,
                reason=f"sombra: o plano da IA divergiu em {v.contra} execuções reais ({_ultimo_motivo(evidencias)})")
            self._anunciar(run_id, f"Fluxo “{fluxo.id}” desligado pelo sistema: o plano que a IA fez para este comando "
                                   f"divergiu dele em {v.contra} execuções reais. O próximo plano comprovado é "
                                   "aprendido de novo, na mesma linha")
            return
        if v.decisao is not Decisao.PROMOVE or fluxo.estado is not SkillState.CANDIDATE:
            return
        prova = f"{v.execucoes} execuções reais com o mesmo plano"
        self._servico.mudar_estado(LivroKind.FLUXO, fluxo.id, SkillState.VALIDATED, by=SYSTEM_ACTOR, run_id=run_id,
                                   reason=f"sombra: {prova}")
        if fluxo.efeito:
            self._anunciar(run_id, f"Fluxo “{fluxo.id}” validado ({prova}), mas tem etapa de efeito externo: só o "
                                   "dono o publica (Aprendizado › Para aprovar). Até lá, a IA segue planejando")
            return
        reaprendido = self._servico.entrada(LivroKind.FLUXO, fluxo.id).reaprendido
        if reaprendido is not None:
            self._anunciar(run_id, f"Fluxo “{fluxo.id}” validado ({prova}), mas foi reaprendido depois de uma "
                                   f"evidência inválida (a execução {reaprendido.run_invalidada} terminou como sucesso "
                                   "sem comprovar o que fez): só o dono o publica (Aprendizado › Para aprovar). Até "
                                   "lá, a IA segue planejando")
            return
        self._servico.mudar_estado(LivroKind.FLUXO, fluxo.id, SkillState.PUBLISHED, by=SYSTEM_ACTOR, run_id=run_id,
                                   reason=f"D1: sem efeito externo, {prova}")
        self._anunciar(run_id, f"Fluxo “{fluxo.id}” publicado pelo sistema (D1: sem efeito externo, {prova}): "
                               "comandos iguais passam a reaproveitar o plano sem chamar o planejador")

    def _texto_do_nascimento(self, fluxo: FluxoEmProva, simulada: bool) -> str:
        """O anúncio do fluxo que nasce candidato, e o ÚNICO com o aprendizado ligado: o `_learn_flow` do scheduler
        se cala nesse caso (`texto_do_fluxo_salvo`) e só fala do que nasce ativo ou quando o digest não roda."""
        inicio = (f"Fluxo “{fluxo.id}” aprendido como candidato (D1): ainda não é reaproveitado — a IA segue "
                  "planejando este comando e o plano novo é comparado com ele")
        # A simulada deixa evidência, mas `veredito_de_repeticao` só conta a real: a que o gerou não entra, e faltam
        # as `1 + concordancias` reais inteiras (antes dizia "só uma pessoa o publica", e as reais o publicavam).
        n = max(0, int(self._concordancias())) + (1 if simulada else 0)
        if simulada:
            inicio += ". Execução simulada não é prova (a que o gerou não conta)"
        quando = f"depois de mais {n} execução(ões) real(is) com o mesmo plano" if n else "já com esta execução"
        if fluxo.efeito:
            return inicio + f"; {quando} ele fica validado, e o dono o publica (tem etapa de efeito externo)"
        if self._servico.entrada(LivroKind.FLUXO, fluxo.id).reaprendido is not None:
            return inicio + (f"; {quando} ele fica validado, e o dono o publica (reaprendido depois de uma evidência "
                             "inválida)")
        return inicio + f"; {quando} o sistema o publica (sem efeito externo)"

    def _anunciar(self, run_id: str, texto: str) -> None:
        if self._decidir is not None:
            try:
                self._decidir(texto, run_id)
            except Exception:  # noqa: BLE001 - a linha do tempo informa; nunca desfaz uma transição já gravada
                log.exception("aprendizado: decisão na execução %s", run_id)


def _casa(fluxo: FluxoEmProva, comando: str) -> bool:
    """A mesma regra de `FlowStore.match`: o comando casa o modelo e dá valor a todo `{nome}` do plano."""
    valores = extract_parameters(fluxo.comando_modelo, comando)
    return valores is not None and bind_template_parameters(fluxo.assinatura.parametros, valores) is not None


def _ultimo_motivo(evidencias: Sequence[Evidencia]) -> str:
    """O porquê da divergência mais recente (as evidências vêm da mais nova para a mais antiga), sem a marca."""
    for e in evidencias:
        if e.stance is Posicao.AGAINST and not e.simulated and e.detail:
            return e.detail.split(" ", 1)[-1]
    return "planos diferentes"


# ------------------------------------------------------------------ habilidades: o escritor real de 043
#: Desfecho da execução → resultado do caso. Cancelada não é veredito sobre a versão.
_DESFECHO: Mapping[str, Outcome] = {"completed": Outcome.PASSED, "completed_with_issues": Outcome.UNCERTAIN,
                                    "failed": Outcome.FAILED}


class ValidacaoPorExecucao:
    """Minerador do digest: toda execução de versão de habilidade vira observação em `skill_validation_results`."""

    nome = "habilidades_d1"

    def __init__(self, leitura: LeituraNativa, porta: PortaDeValidacao, *, decidir: Decidir | None = None) -> None:
        self._leitura = leitura
        self._porta = porta
        self._decidir = decidir

    def minerar(self, run_id: str) -> int:
        e = self._leitura.execucao_de_habilidade(run_id)
        if e is None or is_legacy_skill_id(e.skill_id):
            return 0                                     # fluxo legado não é versão de `skill_versions`
        desfecho = _DESFECHO.get(e.status)
        if desfecho is None:
            return 0
        detalhe = f"execução {e.status} ({'simulada' if e.simulada else 'real'})"
        if desfecho is Outcome.PASSED and e.confirmada_a_mao:
            # "Confirmar concluído" fecha a execução `completed`, mas a tela não comprovou a etapa: é a decisão de uma
            # pessoa, não prova do caminho. Incerteza nunca conta como sucesso — nem valida a versão.
            desfecho = Outcome.UNCERTAIN
            detalhe += " com etapa confirmada à mão (sem prova da tela)"
        observacao = ObservacaoDeHabilidade(
            skill_id=e.skill_id, versao=e.versao, run_id=run_id, prova=Proof.SIMULATED if e.simulada else Proof.REAL,
            desfecho=desfecho, aparelho=e.aparelho, detalhe=f"{detalhe}, gravada pelo digest do aprendizado")
        if not self._porta.registrar(observacao):
            return 0
        if desfecho is Outcome.PASSED and self._porta.validar(
                e.skill_id, e.versao, f"casos de validação aprovados (execução {run_id})"):
            if self._decidir is not None:
                try:
                    self._decidir(f"Habilidade {e.skill_id}@{e.versao} validada pelo sistema: os casos de validação "
                                  "passaram. Publicar continua sendo decisão de uma pessoa", run_id)
                except Exception:  # noqa: BLE001 - a linha do tempo informa; a validação já está gravada
                    log.exception("aprendizado: decisão na execução %s", run_id)
        return 1


__all__ = ["AssinaturaDoPlano", "D1Nativo", "DISCORDANCIAS_QUE_DESLIGAM", "Decidir", "ExecucaoAssentada",
           "ExecucaoDeHabilidade", "FluxoEmProva", "LeituraNativa", "ObservacaoDeHabilidade", "PassoAssinado",
           "PortaDeValidacao", "SombraDosFluxos", "ValidacaoPorExecucao", "comparar", "marca_do_conteudo"]
