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
import re
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
from app.modules.learning.domain.prova import MotivoDaInvalida, detalhe_da_invalida
from app.modules.learning.domain.promocao import (ORIGEM_DO_USO, Decisao, Evidencia, Limiares, contrarias,
                                                  veredito_de_repeticao)
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
    planejador os reescreve a cada vez).

    30.36: o CAMINHO é a ação, o app e o efeito; a pós-condição (`pos`, `pos_valor`) é a FORMA, que o planejador
    reescreve a cada plano ("app em primeiro plano" × "lista de conversas visível", medido no P4 de 03/10). Na etapa de
    efeito externo ela é a prova de entrega e volta a ser caminho."""

    acao: str            # a capability do catálogo, ou `etapa:<identidade da etapa-modelo sem a pós-condição>`
    side_effect: bool
    pos: str             # o tipo da pós-condição
    pos_valor: str = ""  # na etapa livre, o valor da pós-condição com os nomes no lugar dos valores (na capability, "")
    app: str | None = None   # o app em que a etapa roda (o dela ou o do plano); `None`: não se sabe, não compara


@dataclass(frozen=True, slots=True)
class AssinaturaDoPlano:
    passos: tuple[PassoAssinado, ...]
    #: Os parâmetros do plano, sem os reservados (`instance_id`, `run_id`...). No plano-modelo, `{nome}` é o valor que
    #: vem do comando; o resto é valor fixo, que a execução comparada precisa repetir.
    parametros: Mapping[str, str]
    #: 30.36: os parâmetros que alguma etapa usa para AGIR (objetivo, pré-condição, argumentos e guardas do efeito). O
    #: que só aparece na pós-condição, ou em lugar nenhum, é forma. `None`: não se sabe, e todos contam.
    na_acao: frozenset[str] | None = None


@dataclass(frozen=True, slots=True)
class Divergencia:
    """Por que a execução não fez o plano do candidato, sem valor de parâmetro nem texto de tela (vai para
    `learning_evidence.detail`). `forma` (30.36): o caminho é o mesmo e só a redação mudou — a pós-condição de uma etapa
    sem efeito externo, ou um parâmetro que nenhuma etapa usa para agir. Não é evidência contra nem a favor do fluxo."""

    detalhe: str
    forma: bool = False

    def __str__(self) -> str:
        return self.detalhe


def comparar(candidato: AssinaturaDoPlano, execucao: AssinaturaDoPlano) -> Divergencia | None:
    """`None` quando a execução fez o plano do candidato; senão, o porquê. Qualquer diferença de caminho vence as de
    forma: só sai `forma` quando nada além dela mudou. O que era o mesmo plano antes do 30.36 continua sendo (a forma só
    tira linhas do "contra", nunca do "a favor")."""
    if len(candidato.passos) != len(execucao.passos):
        return Divergencia(f"{len(execucao.passos)} etapa(s) no plano, {len(candidato.passos)} no candidato")
    formas: list[str] = []
    for i, (esperado, feito) in enumerate(zip(candidato.passos, execucao.passos), start=1):
        if esperado.acao != feito.acao or (esperado.app and feito.app and esperado.app != feito.app):
            return Divergencia(f"etapa {i}: outra ação")
        if esperado.side_effect != feito.side_effect:
            return Divergencia(f"etapa {i}: efeito externo diferente")
        if (esperado.pos, esperado.pos_valor) == (feito.pos, feito.pos_valor):
            continue
        if esperado.side_effect:                       # a prova de entrega do efeito é caminho, não redação
            return Divergencia(f"etapa {i}: outra pós-condição ({feito.pos} × {esperado.pos})")
        formas.append(f"etapa {i}: pós-condição reescrita ({feito.pos} × {esperado.pos})")
    so_num_lado = set(candidato.parametros) ^ set(execucao.parametros)
    if so_num_lado:
        if any(_na_acao(nome, candidato, execucao) for nome in so_num_lado):
            return Divergencia("outros parâmetros")
        formas.append("parâmetro fora da ação: " + ", ".join(sorted(so_num_lado)))
    for nome, valor in candidato.parametros.items():
        if nome in so_num_lado or valor == "{" + nome + "}" or execucao.parametros.get(nome) == valor:
            continue
        if _na_acao(nome, candidato, execucao):
            return Divergencia(f"valor fixo de '{nome}' diferente")
        formas.append(f"valor fixo de '{nome}' fora da ação")
    if not formas:
        return None
    # o detalhe cabe nos 200 caracteres da coluna, com a marca do conteúdo na frente
    return Divergencia("; ".join(formas[:2]) + (f" (+{len(formas) - 2})" if len(formas) > 2 else ""), forma=True)


def _na_acao(nome: str, *assinaturas: AssinaturaDoPlano) -> bool:
    """O parâmetro serve para agir em algum dos dois planos? Na dúvida (`na_acao` desconhecido), serve."""
    return any(a.na_acao is None or nome in a.na_acao for a in assinaturas)


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
    #: (o planejador não foi chamado), tem etapa confirmada à mão, o plano não é legível, ou é uma prova (30.37).
    assinatura: AssinaturaDoPlano | None
    #: 30.37: a EXECUÇÃO DE PROVA de um fluxo (a validação pelo próprio fluxo); `None` em toda execução comum.
    prova: ProvaDaExecucao | None = None
    #: 30.51: a execução comum que USOU o fluxo ativo (`runs.flow_id`), lida pela regra da prova: só `FOR` ou `AGAINST`
    #: (`domain.prova.evidencia_de_uso`); `None` sem fluxo, na prova, e quando a execução não diz nada do fluxo.
    uso: ProvaDaExecucao | None = None


@dataclass(frozen=True, slots=True)
class ProvaDaExecucao:
    """O desfecho da execução de prova, lido das etapas dela (30.37) pela regra única `domain.prova.veredito_da_prova`
    (30.42). `posicao`: `FOR` com todas as etapas comprovadas (`succeeded`, nenhuma pulada) e sem efeito repetido;
    `AGAINST` com uma etapa que AGIU e reprovou na própria pós-condição (`failed` sem erro de IA); `INVALIDA` quando a
    prova não diz nada sobre o fluxo (efeito repetido, ponto de partida, ator sem ação; o `detalhe` já vem no formato
    `invalida:<motivo> — texto`); `None` para infra (aparelho, teto, cancelamento pelo sistema, erro de IA) e para plano
    revisado: não conta nem a favor nem contra."""

    fluxo_id: str
    content_hash: str                       # o do fluxo de agora: a marca da evidência (`marca_do_conteudo`)
    posicao: Posicao | None
    detalhe: str


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
@dataclass(frozen=True, slots=True)
class ForDeProva:
    """Uma linha `for` de fluxo, de uma EXECUÇÃO DE PROVA, que ainda não tem a `invalida` da mesma origem ao lado (30.42)."""

    item_ref: str
    origin_ref: str
    run_id: str
    detail: str | None
    simulada: bool
    aparelho: str | None


@dataclass(frozen=True, slots=True)
class InvalidaARevalidar:
    """30.53: uma `invalida:efeito_repetido` que a regra de hoje desfaz (`domain.prova.conferencia_revalida`)."""

    item_ref: str
    origin_ref: str
    run_id: str
    simulada: bool
    aparelho: str | None
    marca: str | None                       # a da própria linha `invalida` (a mesma encarnação do conteúdo)
    copias: int
    esperadas: int


@dataclass(frozen=True, slots=True)
class ContraGravado:
    """Uma linha `against` de fluxo, de uma execução, que ainda não tem a `forma` da mesma origem ao lado (30.36)."""

    item_ref: str
    origin_ref: str
    run_id: str
    detail: str | None


class LeituraNativa(Protocol):
    def execucao(self, run_id: str) -> ExecucaoAssentada | None: ...
    def fluxos_em_prova(self) -> list[FluxoEmProva]: ...
    def execucao_de_habilidade(self, run_id: str) -> ExecucaoDeHabilidade | None: ...
    def contra_de_fluxos(self) -> list[ContraGravado]: ...
    def fors_de_prova(self) -> list[ForDeProva]: ...
    def efeito_repetido_da_execucao(self, run_id: str) -> int | None: ...
    def invalidas_a_revalidar(self) -> list[InvalidaARevalidar]: ...
    #: 30.74: a versão do app do fluxo (o principal) observada NESTE aparelho (`device_app_state`), só se o app não pode
    #: ter mudado depois do início da execução; `None` sem leitura ou na dúvida.
    def versao_do_fluxo_no_aparelho(self, fluxo_id: str, aparelho: str | None, run_id: str) -> str | None: ...


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
                 relogio: Callable[[], datetime], execucao_real: Callable[[str], bool] = lambda _run: False,
                 simulada_publica: Callable[[], bool] = lambda: False) -> None:
        """`com_prova`: `aprendizado.fluxo.com_prova` VIGENTE (lido a cada nascimento; `False` = o modo anterior).
        `execucao_real(run_id)`: a execução existe e não é simulada (`runs.simulated=0`). Sem o leitor, nenhuma é real,
        e o que a evidência inválida desligou não renasce (o lado seguro)."""
        self._repo = repo
        self._com_prova = com_prova
        self._relogio = relogio
        self._execucao_real = execucao_real
        #: RA-19 B: `aprendizado.simulada_publica` VIGENTE (`True` = o modo anterior, só da suíte).
        self._simulada_publica = simulada_publica

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
            if reaproveita is not None:
                return None
            # RA-19 B: sem a prova, o comando novo nasce ativo, menos o que uma execução simulada ensinou: esse nasce
            # candidato e só sobe com evidência real (a sombra só conta execução real). Sem `run_id`, o modo anterior.
            simulada = run_id is not None and not self._execucao_real(run_id) and not self._simulada_publica()
            return "candidate" if simulada else "active"
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
        if execucao.prova is not None:
            return self._minerar_prova(execucao, execucao.prova)
        if execucao.uso is not None:
            return self._minerar_uso(execucao, execucao.uso)
        gravadas = 0
        for fluxo in self._leitura.fluxos_em_prova():
            posicao = self._posicao(fluxo, execucao)
            if posicao is None:
                continue
            stance, detalhe = posicao
            nova = self._repo.registrar_evidencia(NovaEvidencia(
                item_ref=ref_da_trilha(LivroKind.FLUXO, fluxo.id), stance=stance, origin_ref=f"run:{run_id}",
                simulated=execucao.simulada, run_id=run_id, instance_id=execucao.aparelho,
                app_version=self._leitura.versao_do_fluxo_no_aparelho(fluxo.id, execucao.aparelho, run_id),
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

    def _minerar_prova(self, execucao: ExecucaoAssentada, prova: ProvaDaExecucao) -> int:
        """30.37: a evidência da prova é UMA linha, do fluxo provado, pelas etapas dela; nenhuma sombra (o plano é o do
        próprio fluxo, não o do planejador). Vale para o candidato e para o ativo (K-086: a execução que usava o fluxo
        ativo não deixava evidência). O D1 (`_avaliar`) só para quem ainda está em prova no livro."""
        if prova.posicao is None:
            return 0
        nova = self._repo.registrar_evidencia(NovaEvidencia(
            item_ref=ref_da_trilha(LivroKind.FLUXO, prova.fluxo_id), stance=prova.posicao,
            origin_ref=f"run:{execucao.run_id}", simulated=execucao.simulada, run_id=execucao.run_id,
            instance_id=execucao.aparelho,
            app_version=self._leitura.versao_do_fluxo_no_aparelho(prova.fluxo_id, execucao.aparelho, execucao.run_id),
            detail=f"{marca_do_conteudo(prova.content_hash)} {prova.detalhe}"))
        if not nova:
            return 0                                      # o digest desta execução já passou por aqui
        em_prova = next((f for f in self._leitura.fluxos_em_prova() if f.id == prova.fluxo_id), None)
        if em_prova is not None:
            try:
                self._avaliar(em_prova, execucao.run_id)
            except ErroDeAprendizado as exc:            # veto, conflito, D1
                log.info("aprendizado: fluxo %s segue como está: %s", prova.fluxo_id, exc)
        return 1

    def _minerar_uso(self, execucao: ExecucaoAssentada, uso: ProvaDaExecucao) -> int:
        """30.51: a execução comum que usou o fluxo ativo deixa UMA linha, de origem própria (`uso:<run_id>`), a favor
        ou contra pelas etapas. Nenhum D1 aqui: o fluxo usado é o ativo, e quem o rebaixa é a saúde (D-5) e o curador.
        A mesma execução nunca conta duas vezes: se o item já tem `for`/`against` dela (de outra origem), nada é
        gravado."""
        ref = ref_da_trilha(LivroKind.FLUXO, uso.fluxo_id)
        if uso.posicao is None or any(e.run_id == execucao.run_id and e.stance in (Posicao.FOR, Posicao.AGAINST)
                                      and e.origin_ref != f"{ORIGEM_DO_USO}{execucao.run_id}"
                                      for e in self._repo.evidencias(ref)):
            return 0
        nova = self._repo.registrar_evidencia(NovaEvidencia(
            item_ref=ref, stance=uso.posicao, origin_ref=f"{ORIGEM_DO_USO}{execucao.run_id}",
            simulated=execucao.simulada, run_id=execucao.run_id, instance_id=execucao.aparelho,
            app_version=self._leitura.versao_do_fluxo_no_aparelho(uso.fluxo_id, execucao.aparelho, execucao.run_id),
            detail=f"{marca_do_conteudo(uso.content_hash)} {uso.detalhe}"))
        return 1 if nova else 0                           # sem `nova`: o digest desta execução já passou por aqui

    def _posicao(self, fluxo: FluxoEmProva, execucao: ExecucaoAssentada) -> tuple[Posicao, str] | None:
        if fluxo.nasceu_de == execucao.run_id:
            return Posicao.FOR, "a execução que o gerou"
        if execucao.assinatura is None or not _casa(fluxo, execucao.comando):
            return None
        divergencia = comparar(fluxo.assinatura, execucao.assinatura)
        if divergencia is None:
            return Posicao.FOR, "mesmo plano"
        # 30.36: só a forma mudou — fica na trilha, fora do "duas discordâncias desligam" e sem validar o fluxo
        return (Posicao.FORMA if divergencia.forma else Posicao.AGAINST), str(divergencia)

    def _avaliar(self, fluxo: FluxoEmProva, run_id: str) -> None:
        marca = marca_do_conteudo(fluxo.content_hash)
        ref = ref_da_trilha(LivroKind.FLUXO, fluxo.id)
        # 30.23: a execução marcada como evidência inválida não prova nada, nem para a encarnação nova da mesma linha
        trilha = self._repo.trilha(ref)
        invalidas = {r for t in trilha if (r := run_invalidada(t.reason)) is not None}
        # 30.31: devolvido à prova por uma pessoa, o fluxo prova-se DE NOVO — só conta a evidência a partir da volta
        # (a favor e contra; a de antes foi o que a pessoa pôs em dúvida ao desligá-lo).
        desde = max((t.decided_at for t in trilha
                     if t.from_state is SkillState.DISABLED and t.to_state is SkillState.CANDIDATE), default="")
        evidencias = [e for e in self._repo.evidencias(ref)
                      if (e.detail or "").startswith(marca) and e.run_id not in invalidas and e.observed_at >= desde]
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


# ------------------------------------------------------------------ a reclassificação da forma (passo da curadoria)
class ReclassificacaoDaForma:
    """30.36: o `against` que a sombra gravou ANTES da regra da forma, num fluxo ainda em prova, é recomparado com o
    comparador de hoje; se só a forma mudou, ganha ao lado a linha `forma` da MESMA origem, que o tira do contra
    (`promocao.efetivas`). O log só cresce: nada se apaga nem se reescreve. Cada reclassificação vira uma decisão na
    linha do tempo da execução reclassificada.

    Só o que dá para recomparar com honestidade: o fluxo ainda em prova (candidate ou validated), a linha da encarnação
    atual (a marca do conteúdo) e a execução ainda legível como comparação (`LeituraNativa.execucao`, a mesma régua do
    digest). Sem transição: um `against` só não desliga nem segura a promoção (o limite é `DISCORDANCIAS_QUE_DESLIGAM`);
    quem chega a dois já saiu da prova. Idempotente pelo índice único (item, origem, posição). Nunca chama IA."""

    nome = "forma_dos_fluxos"

    def __init__(self, repo: RepositorioDeAprendizado, leitura: LeituraNativa, *, decidir: Decidir | None = None) -> None:
        self._repo = repo
        self._leitura = leitura
        self._decidir = decidir

    def executar(self, agora: datetime) -> int:
        contras = self._leitura.contra_de_fluxos()
        if not contras:
            return 0
        em_prova = {ref_da_trilha(LivroKind.FLUXO, f.id): f for f in self._leitura.fluxos_em_prova()}
        n = 0
        for c in contras:
            fluxo = em_prova.get(c.item_ref)
            marca = marca_do_conteudo(fluxo.content_hash) if fluxo is not None else ""
            if fluxo is None or not (c.detail or "").startswith(marca):
                continue                              # saiu da prova, ou a linha é de outra encarnação
            execucao = self._leitura.execucao(c.run_id)
            if execucao is None or execucao.assinatura is None:
                continue
            divergencia = comparar(fluxo.assinatura, execucao.assinatura)
            if divergencia is None or not divergencia.forma:
                continue
            if not self._repo.registrar_evidencia(NovaEvidencia(
                    item_ref=c.item_ref, stance=Posicao.FORMA, origin_ref=c.origin_ref, simulated=execucao.simulada,
                    run_id=c.run_id, instance_id=execucao.aparelho, detail=f"{marca} reclassificada: {divergencia}")):
                continue
            n += 1
            self._anunciar(c.run_id, f"Fluxo “{fluxo.id}”: a divergência desta execução era só de forma ({divergencia})"
                                     " e deixou de contar contra ele (30.36)")
        return n

    def _anunciar(self, run_id: str, texto: str) -> None:
        if self._decidir is not None:
            try:
                self._decidir(texto, run_id)
            except Exception:  # noqa: BLE001 - a linha do tempo informa; a evidência já foi gravada
                log.exception("aprendizado: decisão na execução %s", run_id)


# ------------------------------------------------------------------ o efeito duplicado (passo da curadoria)
_MARCA = re.compile(r"^\[([0-9a-f]{6,})\]")


class ReclassificacaoDoEfeitoDuplicado:
    """30.42: o `for` que uma execução de PROVA deixou antes da regra do efeito repetido (a `5f2de5`, a mensagem enviada
    duas vezes, virou evidência a favor) é recomparado com a regra de hoje (`domain.prova.efeito_repetido`, a mesma do
    veredito); se o efeito saiu mais de uma vez, ganha ao lado a linha `invalida` da MESMA origem, e
    `promocao.efetivas` tira o `for` das contagens. O log só cresce: nada se apaga nem se reescreve, e nenhum UPDATE.

    Vale para o fluxo em qualquer estado (a marca do conteúdo vem da própria linha `for`, então a `invalida` entra na
    mesma encarnação). Sem transição: o que o `for` já tiver feito pelo estado do fluxo fica, e quem decide de novo é a
    próxima evidência. Idempotente pelo índice único (item, origem, posição). Nunca chama IA."""

    nome = "efeito_duplicado_das_provas"

    def __init__(self, repo: RepositorioDeAprendizado, leitura: LeituraNativa, *, decidir: Decidir | None = None) -> None:
        self._repo = repo
        self._leitura = leitura
        self._decidir = decidir

    def executar(self, agora: datetime) -> int:
        n = 0
        for f in self._leitura.fors_de_prova():
            copias = self._leitura.efeito_repetido_da_execucao(f.run_id)
            if copias is None:
                continue
            achada = _MARCA.match(f.detail or "")
            detalhe = detalhe_da_invalida(MotivoDaInvalida.EFEITO_REPETIDO,
                                          f"o efeito saiu {copias} vezes (reclassificada)",
                                          marca=achada.group(1) if achada else None)
            if not self._repo.registrar_evidencia(NovaEvidencia(
                    item_ref=f.item_ref, stance=Posicao.INVALIDA, origin_ref=f.origin_ref, simulated=f.simulada,
                    run_id=f.run_id, instance_id=f.aparelho, detail=detalhe)):
                continue
            n += 1
            self._anunciar(f.run_id, f"Fluxo “{f.item_ref.split(':', 1)[-1]}”: o efeito saiu {copias} vezes nesta "
                                     "prova; ela deixou de valer a favor dele (30.42)")
        return n

    def _anunciar(self, run_id: str, texto: str) -> None:
        if self._decidir is not None:
            try:
                self._decidir(texto, run_id)
            except Exception:  # noqa: BLE001 - a linha do tempo informa; a evidência já foi gravada
                log.exception("aprendizado: decisão na execução %s", run_id)


class RevalidacaoDaConferencia:
    """30.53: a `invalida:efeito_repetido` que a conferência do QA de antes do 30.53 gravou ao contar as mensagens de um
    `for_each` (duas mensagens a dois contatos viravam "o efeito saiu 2 vezes") ganha ao lado a `revalidada` da MESMA
    origem, que só a neutraliza (`promocao.efetivas`). Nenhum `for` nasce aqui: a favor só nasce de prova que fechou
    certo. O `steps.result` fica como estava; a correção fica na trilha (a linha e a decisão na execução). Vale para o
    fluxo e para a receita da mesma execução. Idempotente pelo índice único. Nunca chama IA."""

    nome = "revalidacao_da_conferencia"

    def __init__(self, repo: RepositorioDeAprendizado, leitura: LeituraNativa, *, decidir: Decidir | None = None) -> None:
        self._repo = repo
        self._leitura = leitura
        self._decidir = decidir

    def executar(self, agora: datetime) -> int:
        n = 0
        for x in self._leitura.invalidas_a_revalidar():
            texto = (f"revalidada: a conferência do QA contou {x.copias} mensagens para {x.esperadas} etapas de efeito "
                     "comprovadas, uma por item (30.53); a inválida desta execução não vale")
            if not self._repo.registrar_evidencia(NovaEvidencia(
                    item_ref=x.item_ref, stance=Posicao.REVALIDADA, origin_ref=x.origin_ref, simulated=x.simulada,
                    run_id=x.run_id, instance_id=x.aparelho, detail=f"{x.marca} {texto}" if x.marca else texto)):
                continue
            n += 1
            if self._decidir is not None:
                try:
                    self._decidir(f"Aprendizado: a inválida de “{x.item_ref}” nesta execução foi desfeita: a conferência "
                                  f"do QA viu {x.copias} mensagens para {x.esperadas} etapas de efeito (30.53)", x.run_id)
                except Exception:   # a linha do tempo informa; a evidência já foi gravada
                    log.exception("aprendizado: decisão na execução %s", x.run_id)
        return n


def _casa(fluxo: FluxoEmProva, comando: str) -> bool:
    """A mesma regra de `FlowStore.match`: o comando casa o modelo e dá valor a todo `{nome}` do plano."""
    valores = extract_parameters(fluxo.comando_modelo, comando)
    return valores is not None and bind_template_parameters(fluxo.assinatura.parametros, valores) is not None


def _ultimo_motivo(evidencias: Sequence[Evidencia]) -> str:
    """O porquê da divergência mais recente (as evidências vêm da mais nova para a mais antiga), sem a marca."""
    for e in contrarias(evidencias):
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


__all__ = ["AssinaturaDoPlano", "D1Nativo", "DISCORDANCIAS_QUE_DESLIGAM", "Decidir", "Divergencia", "ExecucaoAssentada",
           "ExecucaoDeHabilidade", "FluxoEmProva", "ForDeProva", "LeituraNativa", "ObservacaoDeHabilidade", "PassoAssinado",
           "PortaDeValidacao", "ProvaDaExecucao", "ReclassificacaoDaForma", "ReclassificacaoDoEfeitoDuplicado",
           "SombraDosFluxos", "ValidacaoPorExecucao",
           "comparar", "marca_do_conteudo"]
