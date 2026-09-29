"""Pacote A4 do ADR-054: o botão do D2 ("deu certo / deu errado + motivo") e os efeitos do voto — rebaixar o que o
item usou e o que aprendeu, com trilha, veto e reativação em um clique.

A ORDEM de `votar` é a garantia de "recusa não deixa rastro":
1. o vocabulário (`conferir_voto`: 'errado' exige motivo; 'certo' não leva);
2. o item (404 quando a execução não existe ou o objetivo não é dela) e o plano puro (`domain/voto.py`);
3. o sinal, com a nota pela triagem de credencial (`registrar_sinal(recusar_nota=True)`: 409 SEM gravar nada) — um
   voto por pessoa e por item (upsert por `(kind, source_ref, created_by)`);
4. só então os efeitos, cada um isolado: o que não pôde mudar (outra sessão mudou antes, guarda do fluxo) volta como
   não aplicado, e o voto continua de pé.

Quem desliga o fluxo e a receita é QUEM VOTOU: o veto passa a ser de pessoa (o sistema não traz de volta o mesmo
conteúdo), e só uma pessoa reativa — pelo `POST /api/aprendizado/{kind}/{ref}/status`, o "desfazer" da resposta.
Votar de novo troca o voto, mas não reativa nada sozinho. A lição é a exceção: quem a desliga é a contagem de
refutações (o sistema), porque um voto só não diz que a LIÇÃO atrapalhou.

A evidência de cada voto aponta para o próprio sinal (`origin_ref='signal:<id>'`): o mesmo voto repetido não conta duas
vezes, e o voto trocado deixa a evidência anterior na história do item (a evidência é registro, não estado).

O falso positivo e o falso negativo do verificador ficam no sinal (`step_verified`, `data.verificador`); é de lá que o
relatório "O que mais falha" (pacote A3) os lê. Nenhuma chamada de IA.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.modules.learning.application.ports import NovaEvidencia, NovoSinal, RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, ConflitoDeEstado, ErroDeAprendizado, NaoEncontrado,
                                               SkillState)
from app.modules.learning.domain.livro import ref_da_trilha
from app.modules.learning.domain.vocabulario import (LivroKind, MotivoDoVoto, Polaridade, Posicao, SignalKind,
                                                     Veredito)
from app.modules.learning.domain.voto import (DESLIGAVEIS, REF_NO_BACKLOG, AcaoDoEfeito, Conhecimento, Efeito,
                                              ItemVotado, PlanoDoVoto, Uso, Verificador, conferir_voto, desfecho,
                                              efeitos_do_voto, licao_refutada)
from app.modules.skills.domain.document import JsonObject
from app.util import to_iso

#: Teto de linhas da aba Sinais (a mais recente primeiro).
LIMITE_DE_SINAIS = 500


# ------------------------------------------------------------------ o que a leitura entrega
@dataclass(frozen=True, slots=True)
class RefDoItem:
    kind: LivroKind
    ref: str
    uso: Uso


@dataclass(frozen=True, slots=True)
class ItemLido:
    """O item votado como o banco o conhece: o objetivo (ou a execução inteira, sem `objective_id`), o que ele usou e
    aprendeu (só as referências; o estado de agora vem do livro) e a etapa de referência — onde parou, ou a última."""

    run_id: str
    objective_id: str | None
    status: str
    simulado: bool
    etapas_comprovadas: int
    etapas_a_mao: int
    refs: tuple[RefDoItem, ...] = ()
    instance_id: str | None = None
    profile_id: str | None = None
    app_package: str = ""
    capability: str = ""
    step_id: str | None = None
    attempt_id: str | None = None
    step_hash: str | None = None
    failure_kind: str | None = None


@dataclass(frozen=True, slots=True)
class SinalGravado:
    """Uma linha de `learning_signals`, já tipada."""

    id: int
    kind: SignalKind
    polarity: Polaridade
    verdict: str | None
    reason: str | None
    note: str | None
    note_refused: bool
    source_ref: str
    created_by: str
    run_id: str | None
    objective_id: str | None
    step_id: str | None
    attempt_id: str | None
    instance_id: str | None
    profile_id: str | None
    app_package: str
    capability: str
    step_hash: str | None
    failure_kind: str | None
    step_verified: bool | None
    data: JsonObject
    simulated: bool
    created_at: str
    updated_at: str | None


class LeituraDoVoto(Protocol):
    """A leitura do que o voto alcança e dos sinais gravados. Só LEITURA: quem escreve é o livro."""

    def item(self, run_id: str, objective_id: str | None) -> ItemLido | None:
        """`None` quando a execução não existe ou o objetivo não é dela."""
        ...

    def existe_execucao(self, run_id: str) -> bool: ...
    def sinal(self, signal_id: int) -> SinalGravado | None: ...
    def sinais_da_execucao(self, run_id: str) -> list[SinalGravado]: ...
    def sinais(self, *, desde: str, kind: SignalKind | None, app: str | None, limite: int) -> list[SinalGravado]: ...


# ------------------------------------------------------------------ o voto e a resposta
@dataclass(frozen=True, slots=True)
class Voto:
    verdict: Veredito
    reason: MotivoDoVoto | None = None
    note: str | None = None
    objective_id: str | None = None


@dataclass(frozen=True, slots=True)
class EfeitoAplicado:
    efeito: Efeito
    aplicado: bool
    erro: str | None = None
    #: Desligado por este voto: a pessoa o reativa em um clique (`→ published`, a única volta da tabela do D1).
    reativavel: bool = False


@dataclass(frozen=True, slots=True)
class ResultadoDoVoto:
    sinal: SinalGravado
    efeitos: tuple[EfeitoAplicado, ...]
    resumo: str


@dataclass(frozen=True, slots=True)
class SinaisDaExecucao:
    votos: tuple[SinalGravado, ...]
    sinais: tuple[SinalGravado, ...]


class ServicoDeFeedback:
    def __init__(self, servico: LearningService, repo: RepositorioDeAprendizado, leitura: LeituraDoVoto, *,
                 relogio: Callable[[], datetime]) -> None:
        self._servico = servico
        self._repo = repo
        self._leitura = leitura
        self._relogio = relogio

    # ================================================================== o voto
    def votar(self, run_id: str, voto: Voto, *, by: str) -> ResultadoDoVoto:
        conferir_voto(voto.verdict, voto.reason)
        lido = self._leitura.item(run_id, voto.objective_id)
        if lido is None:
            alvo = f"o objetivo '{voto.objective_id}' da execução" if voto.objective_id else "a execução"
            raise NaoEncontrado(f"Não há {alvo} '{run_id}'.")
        item = self._item_votado(lido)
        plano = efeitos_do_voto(voto.verdict, voto.reason, item)
        sid = self._servico.registrar_sinal(self._novo_sinal(lido, voto, plano, item, by), recusar_nota=True,
                                            substituir=True)
        gravado = self._leitura.sinal(sid) if sid is not None else None
        if gravado is None:
            raise ConflitoDeEstado("O voto não foi gravado (outra sessão mexeu no mesmo item); tente de novo.")
        aplicados = self._aplicar(plano, lido, voto, sid=gravado.id, by=by)
        return ResultadoDoVoto(gravado, aplicados, _resumo(voto, lido, aplicados))

    def _item_votado(self, lido: ItemLido) -> ItemVotado:
        conhecimentos: list[Conhecimento] = []
        vistos: set[tuple[LivroKind, str]] = set()
        for r in lido.refs:
            if (r.kind, r.ref) in vistos:
                continue                            # usou E aprendeu a mesma receita: um efeito só
            vistos.add((r.kind, r.ref))
            try:
                estado = self._servico.entrada(r.kind, r.ref).state
            except NaoEncontrado:
                continue                            # apagado depois da execução: não há o que rebaixar
            conhecimentos.append(Conhecimento(r.kind, r.ref, estado, r.uso))
        return ItemVotado(desfecho=desfecho(lido.status, da_execucao=lido.objective_id is None),
                          simulado=lido.simulado, etapas_comprovadas=lido.etapas_comprovadas,
                          etapas_a_mao=lido.etapas_a_mao, conhecimentos=tuple(conhecimentos),
                          perfil=lido.profile_id, etapa=lido.step_id)

    @staticmethod
    def _novo_sinal(lido: ItemLido, voto: Voto, plano: PlanoDoVoto, item: ItemVotado, by: str) -> NovoSinal:
        dados: JsonObject = {"status": lido.status, "etapas_comprovadas": lido.etapas_comprovadas,
                             "etapas_a_mao": lido.etapas_a_mao}
        if plano.verificador is not None:
            dados["verificador"] = plano.verificador.value
        return NovoSinal(
            kind=SignalKind.FEEDBACK, source_ref=_fonte(lido), created_by=by, polarity=plano.polaridade,
            verdict=voto.verdict.value, reason=voto.reason.value if voto.reason else None, note=voto.note,
            run_id=lido.run_id, objective_id=lido.objective_id, step_id=lido.step_id, attempt_id=lido.attempt_id,
            instance_id=lido.instance_id, profile_id=lido.profile_id, app_package=lido.app_package,
            capability=lido.capability, step_hash=lido.step_hash, failure_kind=lido.failure_kind,
            step_verified=item.comprovado_pela_tela, data=dados, simulated=lido.simulado)

    # ================================================================== os efeitos
    def _aplicar(self, plano: PlanoDoVoto, lido: ItemLido, voto: Voto, *, sid: int,
                 by: str) -> tuple[EfeitoAplicado, ...]:
        origem = f"signal:{sid}"
        detalhe = f"voto {voto.verdict.value}" + (f": {voto.reason.value}" if voto.reason else "")
        motivo = f"deu errado: {voto.reason.value if voto.reason else 'sem motivo'} (voto em {_fonte(lido)})"
        saida: list[EfeitoAplicado] = []
        for e in plano.efeitos:
            if e.acao is AcaoDoEfeito.DESLIGAR:
                kind = LivroKind(e.kind)
                self._evidencia(kind, e.ref, Posicao.AGAINST, origem, lido, detalhe)
                saida.append(self._desligar(e, by=by, reason=motivo, run_id=lido.run_id))
            elif e.acao in (AcaoDoEfeito.EVIDENCIA_CONTRA, AcaoDoEfeito.EVIDENCIA_A_FAVOR):
                kind = LivroKind(e.kind)
                contra = e.acao is AcaoDoEfeito.EVIDENCIA_CONTRA
                self._evidencia(kind, e.ref, Posicao.AGAINST if contra else Posicao.FOR, origem, lido, detalhe)
                saida.append(EfeitoAplicado(e, aplicado=True))
                if contra and kind is LivroKind.LICAO and (refutada := self._refutacao(e.ref, lido)) is not None:
                    saida.append(refutada)
            else:
                saida.append(EfeitoAplicado(e, aplicado=True))  # backlog, voz, tela, relatório: o sinal os carrega
        return tuple(saida)

    def _evidencia(self, kind: LivroKind, ref: str, posicao: Posicao, origem: str, lido: ItemLido,
                   detalhe: str) -> None:
        self._repo.registrar_evidencia(NovaEvidencia(
            item_ref=ref_da_trilha(kind, ref), stance=posicao, origin_ref=origem, simulated=lido.simulado,
            run_id=lido.run_id, instance_id=lido.instance_id, detail=detalhe))

    def _desligar(self, e: Efeito, *, by: str, reason: str, run_id: str) -> EfeitoAplicado:
        try:
            self._servico.mudar_estado(LivroKind(e.kind), e.ref, SkillState.DISABLED, by=by, reason=reason,
                                       run_id=run_id)
        except ErroDeAprendizado as exc:
            return EfeitoAplicado(e, aplicado=False, erro=exc.code)
        return EfeitoAplicado(e, aplicado=True, reativavel=True)

    def _refutacao(self, ref: str, lido: ItemLido) -> EfeitoAplicado | None:
        """Depois da evidência DESTE voto: com refutações o bastante (votos distintos, só reais), o sistema desliga."""
        refutacoes = sum(1 for ev in self._repo.evidencias(ref)
                         if ev.stance is Posicao.AGAINST and not ev.simulated and ev.origin_ref.startswith("signal:"))
        if not licao_refutada(refutacoes):
            return None
        try:
            atual = self._servico.entrada(LivroKind.LICAO, ref).state
        except NaoEncontrado:
            return None
        if atual not in DESLIGAVEIS:
            return None
        efeito = Efeito(AcaoDoEfeito.DESLIGAR, LivroKind.LICAO.value, ref, atual, SkillState.DISABLED, Uso.EXPOSTA)
        try:
            self._servico.mudar_estado(LivroKind.LICAO, ref, SkillState.DISABLED, by=SYSTEM_ACTOR,
                                       reason=f"{refutacoes} refutações por 'deu errado' (D2)", run_id=lido.run_id)
        except ErroDeAprendizado as exc:
            return EfeitoAplicado(efeito, aplicado=False, erro=exc.code)
        return EfeitoAplicado(efeito, aplicado=True, reativavel=True)

    # ================================================================== leitura
    def da_execucao(self, run_id: str) -> SinaisDaExecucao:
        """Os votos por item e os sinais implícitos da execução (tomada de controle, repetição, confirmação...)."""
        if not self._leitura.existe_execucao(run_id):
            raise NaoEncontrado(f"Não há a execução '{run_id}'.")
        todos = self._leitura.sinais_da_execucao(run_id)
        return SinaisDaExecucao(votos=tuple(s for s in todos if s.kind is SignalKind.FEEDBACK),
                                sinais=tuple(s for s in todos if s.kind is not SignalKind.FEEDBACK))

    def sinais(self, *, dias: int, kind: SignalKind | None = None, app: str | None = None) -> tuple[SinalGravado, ...]:
        """A aba Sinais: os dos últimos `dias`, do mais recente ao mais antigo, até `LIMITE_DE_SINAIS`."""
        desde = to_iso(self._relogio() - timedelta(days=max(1, dias)))
        return tuple(self._leitura.sinais(desde=desde, kind=kind, app=app or None, limite=LIMITE_DE_SINAIS))


# ------------------------------------------------------------------ apoio
def _fonte(lido: ItemLido) -> str:
    """O `source_ref` do voto: o objetivo, ou a execução inteira quando o voto é dela."""
    return f"objective:{lido.objective_id}" if lido.objective_id else f"run:{lido.run_id}"


_NOME_DO_TIPO = {LivroKind.FLUXO.value: "fluxo", LivroKind.RECEITA.value: "receita", LivroKind.LICAO.value: "lição",
                 LivroKind.HABILIDADE.value: "habilidade"}
_DE_ONDE = {Uso.USOU: " usado", Uso.APRENDEU: " aprendido desta execução", Uso.EXPOSTA: " exposta"}
_DE_ONDE_F = {Uso.USOU: " usada", Uso.APRENDEU: " aprendida desta execução", Uso.EXPOSTA: " exposta"}


def _nome(e: Efeito) -> str:
    feminino = e.kind in (LivroKind.RECEITA.value, LivroKind.LICAO.value)
    de_onde = (_DE_ONDE_F if feminino else _DE_ONDE).get(e.uso, "") if e.uso is not None else ""
    return f"{_NOME_DO_TIPO.get(e.kind, e.kind)}{de_onde} {e.ref}"


def _frase(a: EfeitoAplicado, lido: ItemLido) -> str:
    e = a.efeito
    if not a.aplicado:
        return f"{_nome(e)} não mudou ({a.erro})"
    if e.acao is AcaoDoEfeito.DESLIGAR:
        estado = {LivroKind.RECEITA.value: "em quarentena", LivroKind.LICAO.value: "desligada"}.get(e.kind,
                                                                                                  "desligado")
        return f"{_nome(e)} {estado} — reativar"
    if e.acao is AcaoDoEfeito.EVIDENCIA_CONTRA:
        return f"evidência contra {_nome(e)}"
    if e.acao is AcaoDoEfeito.EVIDENCIA_A_FAVOR:
        return f"evidência a favor de {_nome(e)}"
    if e.acao is AcaoDoEfeito.BACKLOG:
        if e.ref == REF_NO_BACKLOG[Verificador.FALSO_POSITIVO]:
            return "falso positivo do verificador somado a O que mais falha"
        return ("falso negativo do verificador somado a O que mais falha (o desfecho não muda: para isso existe "
                "Confirmar concluído)")
    if e.acao is AcaoDoEfeito.VOZ:
        return "somado à voz do perfil"
    if e.acao is AcaoDoEfeito.TELA_E_LICAO:
        return "somado às candidatas de tela e de lição da etapa onde parou"
    if not lido.capability:
        return "somado a O que mais falha"
    return f"somado a {lido.capability}" + (f": {lido.failure_kind}" if lido.failure_kind else "") + (
        " em O que mais falha")


def _resumo(voto: Voto, lido: ItemLido, aplicados: tuple[EfeitoAplicado, ...]) -> str:
    cabeca = "Deu certo" if voto.verdict is Veredito.CERTO else (
        f"Deu errado ({voto.reason.value if voto.reason else '-'})")
    if lido.simulado:
        return f"{cabeca}: execução simulada — o voto foi gravado e nada real foi rebaixado."
    partes = [_frase(a, lido) for a in aplicados] or ["voto gravado; nada a mudar"]
    return f"{cabeca}: " + "; ".join(partes) + "."


__all__ = ["LIMITE_DE_SINAIS", "EfeitoAplicado", "ItemLido", "LeituraDoVoto", "RefDoItem", "ResultadoDoVoto",
           "ServicoDeFeedback", "SinaisDaExecucao", "SinalGravado", "Voto"]
