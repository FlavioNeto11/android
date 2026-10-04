"""A ligação das decisões automáticas (28.25) com a inversa de cada fila dona. Mora fora de `modules/` de propósito: é o
único lugar que enxerga o aprendizado E o desfazer, e `modules/decisoes` não pode importar o aprendizado (as frentes que
decidem registram pelo kernel, `app/shared/decisoes.py`; um import no outro sentido fecharia ciclo no DAG dos contextos).

Investigação (lida, sem mexer nas filas), 04/10:
- aprendizado: o ciclo do livro (`ciclo.TRANSICOES`) NÃO tem `published → validated`. O desfazer de uma decisão de
  aprendizado (30.55) é DESLIGAR o item, `published → disabled` (contrato da orquestradora, 04/10): a transição que a
  pessoa já tem na tela do Aprendizado ("desligar"), pelo mesmo serviço do livro, com a trilha e o veto de sempre (uma
  pessoa desligou, então a plataforma não redecide aquele conteúdo). Nunca se chama `published → validated`;
- pergunta: `RUN_TRANSITIONS['needs_input'] = {cancelled}` e nada volta a `needs_input` ("a pessoa reescreve o comando");
  a reabertura de `cancelled` existe só para resolver ITEM de uma execução que rodou;
- objetivo: `failed → pending` existe ("tentar novamente"), mas repete ações no aparelho; reabrir sozinho a pedido de um
  desfazer não é seguro;
- pedido: `encerrado` e `cancelado` são estados finais (`pedidos/domain/estados.py`); o laço não vence `aguardando_pessoa`
  hoje, então ainda não há produtor desta fila (só a porta).
"""
from __future__ import annotations

from collections.abc import Callable

from app.modules.decisoes.application.desfazer import (Descricao, DesfeitaPorFora, InversaDaFila, SemInversa,
                                                         SemInversaSegura)
from app.modules.decisoes.domain.decisao import Decisao
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.pareceres import ServicoDePareceres
from app.modules.learning.domain.ciclo import ErroDeAprendizado, NaoEncontrado, SkillState, TransicaoProibida
from app.modules.learning.domain.vocabulario import LivroKind

MOTIVO_PERGUNTA = ("a execução foi encerrada e uma pergunta encerrada não reabre (o estado só vai para cancelada); "
                   "para seguir, faça o pedido de novo")
MOTIVO_OBJETIVO = ("reabrir um objetivo encerrado repetiria ações no aparelho; peça de novo, ou use \"Tentar "
                   "novamente\" na execução depois de conferir")
MOTIVO_PEDIDO = "um pedido encerrado é estado final e não reabre; crie o pedido de novo"
#: O prefixo que `_mover` põe no motivo da trilha; a leitura do desligamento por outro caminho o tira.
PREFIXO_DO_MOTIVO = "desligado pelo dono: "

class InversaDoAprendizado:
    """Desligar (`published → disabled`) o item que a plataforma decidiu sozinha, pelo serviço do livro: a mesma trilha,
    com o nome do dono e o motivo dele. Vale para os tipos que a tela do Aprendizado desliga (lição, tela, preferência,
    voz, receita e fluxo); habilidade e memória têm ciclo próprio e o serviço recusa."""

    def __init__(self, servico: LearningService):
        self._servico = servico

    @staticmethod
    def _kind(d: Decisao) -> LivroKind | None:
        try:
            return LivroKind(str(d.fatos.get("kind")))
        except ValueError:
            return None

    def por_que_nao(self, decisao: Decisao) -> str | None:
        if decisao.fatos.get("para") != SkillState.PUBLISHED.value:
            return "só o que a plataforma deixou publicado pode ser desligado; as outras decisões são conferidas na própria tela"
        kind = self._kind(decisao)
        if kind is None:
            return "o tipo do item não está registrado nesta decisão"
        # 28.29: o estado de AGORA do item, e não só o da decisão (o botão aparecia para o que já tinha mudado).
        try:
            atual = self._servico.entrada(kind, self._ref(decisao, kind)).state
        except NaoEncontrado:
            return "o item não existe mais no livro de aprendizado"
        except ErroDeAprendizado:
            return None                                        # na dúvida o botão fica; o desfazer confere de novo
        if atual not in (SkillState.PUBLISHED, SkillState.DISABLED):
            return (f"o item já mudou de estado depois da decisão automática (agora: {atual.value}); "
                    "confira na tela do Aprendizado")
        return None

    def descrever(self, decisao: Decisao) -> Descricao | None:
        """O título do item no livro (28.29: a lista dizia "Fluxo confirmado" 15 vezes sem dizer qual)."""
        kind = self._kind(decisao)
        if kind is None:
            return None
        try:
            titulo = self._servico.entrada(kind, self._ref(decisao, kind)).title
        except ErroDeAprendizado:
            return None
        return Descricao(nome=" ".join(str(titulo or "").split())[:120] or None)

    def desfeita_por_fora(self, decisao: Decisao) -> DesfeitaPorFora | None:
        """O item que a plataforma publicou e alguém DESLIGOU por outro caminho (a tela do Aprendizado, a rota do livro):
        quem, quando e o motivo, da última transição para `disabled` na trilha dele."""
        kind = self._kind(decisao)
        if decisao.fatos.get("para") != SkillState.PUBLISHED.value or kind is None:
            return None
        ref = self._ref(decisao, kind)
        try:
            if self._servico.entrada(kind, ref).state is not SkillState.DISABLED:
                return None
            trilha = self._servico.detalhe(kind, ref).trilha
        except ErroDeAprendizado:
            return None
        desligadas = [t for t in trilha if t.to_state is SkillState.DISABLED]
        if not desligadas:
            return None
        t = max(desligadas, key=lambda x: x.id)
        motivo = (t.reason or "").removeprefix(PREFIXO_DO_MOTIVO).strip() or None
        return DesfeitaPorFora(por=t.decided_by, em=t.decided_at, motivo=motivo)

    @staticmethod
    def _ref(decisao: Decisao, kind: LivroKind) -> str:
        """O id que o serviço do livro entende: o `item_ref` da trilha de receita e fluxo vem como `receita:180`, e o
        serviço quer `180` (o contrato fixado pela frente Aprendizado)."""
        return decisao.item_ref.removeprefix(f"{kind.value}:")

    def _mover(self, kind: LivroKind, ref: str, por: str, texto: str) -> None:
        """Pelo MESMO caminho da rota `POST /api/aprendizado/{kind}/{ref}/status`: com o curador composto, passa antes
        pelo serviço dos pareceres (que grava também o rótulo do parecer pendente)."""
        servico = self._servico.extensao(ServicoDePareceres) or self._servico
        servico.mudar_estado(kind, ref, SkillState.DISABLED, by=por, reason=f"{PREFIXO_DO_MOTIVO}{texto}")

    def desfazer(self, decisao: Decisao, *, por: str, motivo: str | None) -> None:
        porque = self.por_que_nao(decisao)
        kind = self._kind(decisao)
        if porque is not None or kind is None:
            raise SemInversaSegura(porque or "o tipo do item não está registrado nesta decisão")
        ref = self._ref(decisao, kind)
        texto = (motivo or "").strip() or "decisão automática desfeita"
        try:
            atual = self._servico.entrada(kind, ref)
            if atual.state is SkillState.DISABLED:
                return                                         # uma pessoa já o desligou (ou o desfazer caiu antes de gravar)
            if atual.state is not SkillState.PUBLISHED:
                raise SemInversaSegura("o item já mudou de estado depois da decisão automática; confira na tela do Aprendizado")
            self._mover(kind, ref, por, texto)
        except NaoEncontrado as exc:
            raise SemInversaSegura("o item não existe mais no livro de aprendizado") from exc
        except TransicaoProibida as exc:
            # Corrida: outra sessão desligou entre a leitura e o gesto. O efeito pedido já está lá: é sucesso idempotente.
            try:
                if self._servico.entrada(kind, ref).state is SkillState.DISABLED:
                    return
            except ErroDeAprendizado:
                pass
            raise SemInversaSegura(str(exc)) from exc
        except ErroDeAprendizado as exc:
            raise SemInversaSegura(str(exc)) from exc


class SemInversaDaExecucao(SemInversa):
    """Pergunta e objetivo vencidos: sem volta segura, mas o painel abre a execução (28.29). A pergunta É a execução
    (`item_ref`); o objetivo diz a dele nos fatos ou, na linha gravada antes do 28.29, pela leitura injetada."""

    def __init__(self, motivo: str, *, run_do_objetivo: Callable[[str], str | None] | None = None):
        super().__init__(motivo)
        self._run_do_objetivo = run_do_objetivo

    def descrever(self, decisao: Decisao) -> Descricao | None:
        if decisao.fila == "pergunta":
            return Descricao(run_id=decisao.item_ref)
        run_id = decisao.fatos.get("run_id")
        if isinstance(run_id, str) and run_id:
            return Descricao(run_id=run_id)
        return Descricao(run_id=self._run_do_objetivo(decisao.item_ref)) if self._run_do_objetivo else None


def inversas_das_filas(learning: LearningService, *, run_do_objetivo: Callable[[str], str | None] | None = None
                       ) -> dict[str, InversaDaFila]:
    return {"aprendizado": InversaDoAprendizado(learning), "pergunta": SemInversaDaExecucao(MOTIVO_PERGUNTA),
            "objetivo": SemInversaDaExecucao(MOTIVO_OBJETIVO, run_do_objetivo=run_do_objetivo),
            "pedido": SemInversa(MOTIVO_PEDIDO)}
