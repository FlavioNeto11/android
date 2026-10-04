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

from collections.abc import Callable, Iterator
from contextlib import contextmanager

from app.modules.decisoes.application.desfazer import (Descricao, DesfeitaPorFora, InversaDaFila, SemInversa,
                                                         SemInversaSegura)
from app.modules.decisoes.domain.decisao import Decisao
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.pareceres import ServicoDePareceres
from app.modules.learning.domain.ciclo import ErroDeAprendizado, NaoEncontrado, SkillState, TransicaoProibida
from app.modules.learning.domain.vocabulario import LivroKind
from app.shared.costuras import PLATAFORMA, SISTEMA
from app.util import parse_iso

MOTIVO_PERGUNTA = ("a execução foi encerrada e uma pergunta encerrada não reabre (o estado só vai para cancelada); "
                   "para seguir, faça o pedido de novo")
MOTIVO_OBJETIVO = ("reabrir um objetivo encerrado repetiria ações no aparelho; peça de novo, ou use \"Tentar "
                   "novamente\" na execução depois de conferir")
MOTIVO_PEDIDO = "um pedido encerrado é estado final e não reabre; crie o pedido de novo"
#: O prefixo que `_mover` põe no motivo da trilha; a leitura do desligamento por outro caminho o tira.
PREFIXO_DO_MOTIVO = "desligado pelo dono: "
#: 28.33 (achado 5 do 28.29): quem desliga sem ser pessoa. Desligamento por um deles é OUTRA decisão automática, não o
#: desfazer desta.
AUTORES_AUTOMATICOS = frozenset({SISTEMA, PLATAFORMA})
MOTIVO_DESLIGADO_POR_REGRA = ("o item foi desligado depois por uma regra automática, não por uma pessoa; confira na tela "
                              "do Aprendizado")


def _de_pessoa(autor: object) -> bool:
    return bool(autor) and str(autor) not in AUTORES_AUTOMATICOS


def _transicao_da_decisao(decisao: Decisao) -> int:
    """O id da transição da plataforma que a decisão registrou (`origem_ref = aprendizado:<id>`, do adaptador). Só o que
    veio DEPOIS dela pode desfazê-la; sem o id legível, 0 (vale a trilha inteira, como antes do 28.33)."""
    _, _, resto = decisao.origem_ref.partition("aprendizado:")
    return int(resto) if resto.isdigit() else 0


def _mudou_depois(instante: object, referencia: str) -> bool:
    """O estado do item mudou DEPOIS da decisão (`state_at` > `decidida_em`, os dois em ISO). Na dúvida (data ausente ou
    ilegível), conta como mudou: a trilha é lida e conferida, o que é o lado seguro."""
    try:
        a, b = parse_iso(str(instante or "")), parse_iso(referencia)
    except ValueError:
        return True
    return a is None or b is None or a > b

class InversaDoAprendizado:
    """Desligar (`published → disabled`) o item que a plataforma decidiu sozinha, pelo serviço do livro: a mesma trilha,
    com o nome do dono e o motivo dele. Vale para os tipos que a tela do Aprendizado desliga (lição, tela, preferência,
    voz, receita e fluxo); habilidade e memória têm ciclo próprio e o serviço recusa."""

    def __init__(self, servico: LearningService):
        self._servico = servico
        #: A leitura do livro reaproveitada dentro de UMA listagem (`memorizado`); `None` fora dela. A listagem é
        #: síncrona dentro do handler (nenhum `await` no meio), então duas requisições não dividem o mesmo memo.
        self._memo: dict[tuple[LivroKind, str], object] | None = None

    @contextmanager
    def memorizado(self) -> Iterator[None]:
        """Revisão do 28.29 (achado 4): na listagem, `por_que_nao`, `descrever` e `desfeita_por_fora` perguntavam o mesmo
        item ao livro, 3 a 4 leituras por decisão e até 200 decisões por GET. Aqui, uma leitura por item. O desfazer
        não passa por aqui: ele lê o estado de agora."""
        anterior, self._memo = self._memo, {}
        try:
            yield
        finally:
            self._memo = anterior

    def _entrada(self, kind: LivroKind, ref: str):  # noqa: ANN202 - o tipo é o do serviço do livro
        if self._memo is None:
            return self._servico.entrada(kind, ref)
        chave = (kind, ref)
        if chave not in self._memo:
            try:
                self._memo[chave] = self._servico.entrada(kind, ref)
            except ErroDeAprendizado as exc:
                self._memo[chave] = exc
        valor = self._memo[chave]
        if isinstance(valor, ErroDeAprendizado):
            raise valor
        return valor

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
            atual = self._entrada(kind, self._ref(decisao, kind)).state
        except NaoEncontrado:
            return "o item não existe mais no livro de aprendizado"
        except ErroDeAprendizado:
            return None                                        # na dúvida o botão fica; o desfazer confere de novo
        if atual not in (SkillState.PUBLISHED, SkillState.DISABLED):
            return (f"o item já mudou de estado depois da decisão automática (agora: {atual.value}); "
                    "confira na tela do Aprendizado")
        if atual is SkillState.DISABLED:
            # 28.33 (achado 5): desligado por uma regra automática não tem o que desligar, e o desfazer não pode
            # registrar como dele o que outra decisão fez. Desligado por pessoa segue sem motivo (o desfazer é
            # idempotente). A trilha só é lida para o item desligado.
            try:
                ultimo = self._ultimo_desligamento(kind, self._ref(decisao, kind))
            except ErroDeAprendizado:
                return None                                    # na dúvida o botão fica; o desfazer confere de novo
            if ultimo is not None and not _de_pessoa(ultimo.decided_by):
                return MOTIVO_DESLIGADO_POR_REGRA
        return None

    def _ultimo_desligamento(self, kind: LivroKind, ref: str):  # noqa: ANN202 - a transição do livro
        desligadas = [t for t in self._servico.detalhe(kind, ref).trilha if t.to_state is SkillState.DISABLED]
        return max(desligadas, key=lambda x: x.id) if desligadas else None

    def descrever(self, decisao: Decisao) -> Descricao | None:
        """O título do item no livro (28.29: a lista dizia "Fluxo confirmado" 15 vezes sem dizer qual)."""
        kind = self._kind(decisao)
        if kind is None:
            return None
        try:
            titulo = self._entrada(kind, self._ref(decisao, kind)).title
        except ErroDeAprendizado:
            return None
        return Descricao(nome=" ".join(str(titulo or "").split())[:120] or None)

    def desfeita_por_fora(self, decisao: Decisao) -> DesfeitaPorFora | None:
        """O item que a plataforma publicou e uma PESSOA desligou depois por outro caminho (a tela do Aprendizado, a rota
        do livro): quem, quando e o motivo, da última dessas transições para `disabled`.

        28.33 (achados 5 e 6 do 28.29): o desligamento por regra automática (`sistema`, `plataforma`) não é o desfazer
        desta decisão; e o item que a pessoa desligou e depois religou à mão segue desfeito (a publicação de agora é
        dela). A trilha só é lida quando o estado mudou depois da decisão (`state_at`), para a listagem não pagar a
        leitura de cada item publicado e intocado."""
        kind = self._kind(decisao)
        if decisao.fatos.get("para") != SkillState.PUBLISHED.value or kind is None:
            return None
        ref = self._ref(decisao, kind)
        try:
            entrada = self._entrada(kind, ref)
            if entrada.state not in (SkillState.DISABLED, SkillState.PUBLISHED):
                return None
            if entrada.state is SkillState.PUBLISHED and not _mudou_depois(entrada.state_at, decisao.decidida_em):
                return None                                    # nada mudou desde a publicação da plataforma
            trilha = self._servico.detalhe(kind, ref).trilha
        except ErroDeAprendizado:
            return None
        depois_de = _transicao_da_decisao(decisao)
        desligadas = [t for t in trilha if t.to_state is SkillState.DISABLED and _de_pessoa(t.decided_by)
                      and t.id > depois_de]
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
