"""`LearningService`: o livro de aprendizado lido por inteiro, as transições com o D1, o digest de cada execução
assentada e a curadoria periódica (ADR-054).

Nenhuma chamada de IA em lugar nenhum daqui (decisão 9). O digest e a curadoria são arcabouço: cada minerador e cada
passo registrado roda isolado (uma falha vira log e contagem, nunca derruba o fim da execução nem o laço), e os
pacotes seguintes (A3–A9) só registram os seus. O que este pacote já faz de verdade é a RÉGUA DURÁVEL: o agregado
diário (`learning_daily`) recalculado por inteiro, só para dias ainda intactos — `ai_calls` morre em
`log_retention_days`, o dia agregado fica.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import partial

from app.modules.learning.application.ports import (Ajustes, FontesDoLivro, Minerador, MudancaNativa, NovoSinal,
                                                    PassoDeCuradoria, RepositorioDeAprendizado, TriagemDeTexto)
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, Actor, EntradaInvalida, NaoEncontrado,
                                               NotaComCaraDeSegredo, SkillState, TransicaoProibida,
                                               UseARotaDasHabilidades, Vetado, conferir_transicao, motivo_do_veto)
from app.modules.learning.domain.livro import (EntradaDoLivro, ItemDeAprendizado, NovoItem, Transicao, a_revisar,
                                               contagem, entrada_do_item, para_aprovar, status_nativo)
from app.modules.learning.domain.promocao import Evidencia
from app.modules.learning.domain.vocabulario import (KINDS_DE_ITEM, LivroKind, Modo, ModoDeTelas, Origem)
from app.modules.skills.domain.document import JsonValue

log = logging.getLogger("poc.aprendizado")

#: Tamanho máximo de uma nota de pessoa (o botão do D2 e a varredura).
NOTA_MAX = 500


@dataclass(frozen=True, slots=True)
class Livro:
    itens: tuple[EntradaDoLivro, ...]
    contagem: dict[str, dict[str, int]]


@dataclass(frozen=True, slots=True)
class DetalheDoLivro:
    entrada: EntradaDoLivro
    evidencias: tuple[Evidencia, ...]
    trilha: tuple[Transicao, ...]
    #: As exposições das lições (pacote A7); vazio até lá.
    exposicoes: tuple[JsonValue, ...] = ()


@dataclass(frozen=True, slots=True)
class Relatorio:
    """O que um digest ou um passo de curadoria fez: linhas por etapa e as etapas que falharam."""

    feito: dict[str, int] = field(default_factory=dict)
    falhas: tuple[str, ...] = ()
    pulado: bool = False


def dias_intactos(corte: datetime, agora: datetime) -> tuple[str, str] | None:
    """Os dias INTEIROS depois do `corte` da purga de `ai_calls`, até hoje: [desde, até) em 'AAAA-MM-DD'.

    O dia que contém o corte não entra: a purga anterior (6 h antes) já pode ter apagado o começo dele, e recalculá-lo
    agora escreveria um dia subestimado por cima de um que estava certo.
    """
    inicio = corte.replace(hour=0, minute=0, second=0, microsecond=0)
    if inicio < corte:
        inicio += timedelta(days=1)
    fim = agora.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if inicio >= fim:
        return None
    return inicio.strftime("%Y-%m-%d"), fim.strftime("%Y-%m-%d")


class LearningService:
    def __init__(self, repo: RepositorioDeAprendizado, fontes: FontesDoLivro, triagem: TriagemDeTexto, *,
                 ajustes: Callable[[], Ajustes], relogio: Callable[[], datetime],
                 retencao_de_logs_dias: Callable[[], int],
                 mineradores: Sequence[Minerador] = (), passos: Sequence[PassoDeCuradoria] = ()) -> None:
        """`retencao_de_logs_dias`: o `log_retention_days` VIGENTE (muda com o processo no ar); é o que diz até
        onde `ai_calls` ainda está inteiro."""
        self._repo = repo
        self._fontes = fontes
        self._triagem = triagem
        self._ajustes = ajustes
        self._relogio = relogio
        self._retencao_de_logs = retencao_de_logs_dias
        self._mineradores: list[Minerador] = list(mineradores)
        self._passos: list[PassoDeCuradoria] = list(passos)

    @property
    def ajustes(self) -> Ajustes:
        return self._ajustes()

    def registrar_minerador(self, minerador: Minerador) -> None:
        self._mineradores.append(minerador)

    def registrar_passo(self, passo: PassoDeCuradoria) -> None:
        self._passos.append(passo)

    # ================================================================== leitura única
    def livro(self, *, kind: LivroKind | None = None, state: SkillState | None = None, app: str | None = None,
              origem: Origem | None = None) -> Livro:
        todas = self._todas(kind)
        filtradas = tuple(e for e in todas if (state is None or e.state is state) and (app is None or e.app == app)
                          and (origem is None or e.origin is origem))
        return Livro(filtradas, contagem(filtradas))

    def _todas(self, kind: LivroKind | None) -> list[EntradaDoLivro]:
        fontes: dict[LivroKind, Callable[[], list[EntradaDoLivro]]] = {
            LivroKind.RECEITA: self._fontes.receitas, LivroKind.FLUXO: self._fontes.fluxos,
            LivroKind.HABILIDADE: self._fontes.habilidades, LivroKind.MEMORIA: self._fontes.memorias,
        }
        saida: list[EntradaDoLivro] = []
        for k, ler in fontes.items():
            if kind is None or kind is k:
                saida.extend(ler())
        if kind is None or kind in KINDS_DE_ITEM:
            saida.extend(entrada_do_item(i) for i in self._repo.itens(kind=kind if kind in KINDS_DE_ITEM else None))
        return saida

    def entrada(self, kind: LivroKind, ref: str) -> EntradaDoLivro:
        if kind in KINDS_DE_ITEM:
            item = self._repo.item(ref)
            if item is None or item.kind is not kind:
                raise NaoEncontrado(f"Não há {kind.value} '{ref}' no livro.")
            return entrada_do_item(item)
        ler: dict[LivroKind, Callable[[str], EntradaDoLivro | None]] = {
            LivroKind.RECEITA: self._fontes.receita, LivroKind.FLUXO: self._fontes.fluxo,
            LivroKind.HABILIDADE: self._fontes.habilidade, LivroKind.MEMORIA: self._fontes.memoria,
        }
        achada = ler[kind](ref)
        if achada is None:
            raise NaoEncontrado(f"Não há {kind.value} '{ref}' no livro.")
        return achada

    def detalhe(self, kind: LivroKind, ref: str) -> DetalheDoLivro:
        e = self.entrada(kind, ref)
        if kind is LivroKind.MEMORIA:
            return DetalheDoLivro(e, (), ())            # só a contagem: o conteúdo da memória nunca sai no livro
        return DetalheDoLivro(e, tuple(self._repo.evidencias(e.trail_ref)), tuple(self._repo.trilha(e.trail_ref)))

    def pendentes(self) -> tuple[EntradaDoLivro, ...]:
        """"Para aprovar": a fila do D1 (e a contagem da barra do topo)."""
        return tuple(e for e in self._todas(None) if para_aprovar(e))

    def revisar(self) -> tuple[EntradaDoLivro, ...]:
        """"Revisar": o legado ativo com efeito anterior ao D1, que nenhuma pessoa decidiu pelo livro ainda."""
        decididos = self._repo.refs_decididas_por_pessoa((LivroKind.RECEITA, LivroKind.FLUXO))
        return tuple(e for e in self._fontes.receitas() + self._fontes.fluxos() if a_revisar(e, decididos))

    # ================================================================== transições
    def _modo_publica(self, kind: LivroKind) -> bool:
        a = self.ajustes
        if kind is LivroKind.LICAO:
            return a.modo_licoes is Modo.ON
        if kind is LivroKind.TELA:
            return a.modo_telas is ModoDeTelas.ON
        if kind is LivroKind.PREFERENCIA:
            return a.modo_preferencias is Modo.ON
        if kind is LivroKind.VOZ:
            return False                                # a voz é sempre do dono (side_effect=1)
        return True                                     # receita e fluxo: os interruptores são os deles

    def mudar_estado(self, kind: LivroKind, ref: str, para: SkillState, *, by: str, reason: str,
                     run_id: str | None = None) -> EntradaDoLivro:
        """Move um item do livro, com a trilha. Pessoa ou sistema (`by='sistema'`); o D1 vale nos dois.

        Habilidade tem rota e ciclo próprios (409 com o endereço); memória segue a regra dela e não passa por aqui.
        """
        motivo = reason.strip()
        if not motivo:
            raise EntradaInvalida("Diga o motivo: aprovar, rejeitar, desligar e reativar ficam na trilha.")
        if kind is LivroKind.HABILIDADE:
            sid, _, versao = ref.rpartition("@")
            raise UseARotaDasHabilidades(
                "Habilidade tem ciclo próprio (publicar é sempre de uma pessoa): use a rota das habilidades.",
                href=f"/api/skills/{sid or ref}/versions/{versao or '?'}/status")
        if kind is LivroKind.MEMORIA:
            raise TransicaoProibida("A memória da persona segue a regra dela (fato confirmado) e fica fora do D1.")
        if kind in KINDS_DE_ITEM:
            return entrada_do_item(self._mover_item(kind, ref, para, by=by, reason=motivo, run_id=run_id))
        self._mover_nativo(self.entrada(kind, ref), para, by=by, reason=motivo, run_id=run_id)
        return self.entrada(kind, ref)

    def _mover_item(self, kind: LivroKind, ref: str, para: SkillState, *, by: str, reason: str,
                    run_id: str | None) -> ItemDeAprendizado:
        item = self._repo.item(ref)
        if item is None or item.kind is not kind:
            raise NaoEncontrado(f"Não há {kind.value} '{ref}' no livro.")
        actor = conferir_transicao(item.state, para, by, side_effect=item.side_effect,
                                   human_origin=item.human_origin, modo_publica=self._modo_publica(kind))
        if actor is Actor.SYSTEM and para in (SkillState.VALIDATED, SkillState.PUBLISHED):
            self._conferir_veto(item.content_hash, item.escopo.chave(kind), item.app_version)
        return self._repo.transicionar_item(item, para, by=by, reason=reason, run_id=run_id)

    def _mover_nativo(self, e: EntradaDoLivro, para: SkillState, *, by: str, reason: str,
                      run_id: str | None) -> None:
        if e.state is None or e.native_status is None:
            raise TransicaoProibida(f"O estado '{e.native_status}' de {e.kind.value} {e.ref} não é do livro.")
        para_status = status_nativo(e.kind, para)
        if para_status is None:
            raise TransicaoProibida(f"{e.kind.value} não tem o estado '{para.value}' (fluxo sai de circulação como "
                                    "'disabled').")
        if e.kind is LivroKind.RECEITA and e.state is SkillState.DEPRECATED:
            raise TransicaoProibida("Receita substituída não volta: a versão nova da mesma etapa é a que vale.")
        actor = conferir_transicao(e.state, para, by, side_effect=e.side_effect, human_origin=e.human_origin,
                                   modo_publica=self._modo_publica(e.kind))
        if actor is Actor.SYSTEM and para in (SkillState.VALIDATED, SkillState.PUBLISHED) and e.content_hash:
            self._conferir_veto(e.content_hash, e.scope_key, e.app_version)
        self._repo.transicionar_nativo(
            MudancaNativa(kind=e.kind, ref=e.ref, de_status=e.native_status, para_status=para_status,
                          de_estado=e.state, para_estado=para, content_hash=e.content_hash, scope_key=e.scope_key,
                          app_version=e.app_version), by=by, reason=reason, run_id=run_id)

    def _conferir_veto(self, content_hash: str, scope_key: str, app_version: str | None) -> None:
        motivo = motivo_do_veto(self._repo.desligamentos(content_hash, scope_key), agora=self._relogio(),
                                app_version=app_version)
        if motivo is not None:
            raise Vetado(f"O sistema não traz de volta este conteúdo: {motivo}.")

    # ================================================================== escrita dos mineradores
    def _recusa(self, texto: str) -> bool:
        return bool(texto) and self._triagem.recusa(texto)

    def propor(self, novo: NovoItem, *, by: str = SYSTEM_ACTOR, run_id: str | None = None) -> ItemDeAprendizado:
        """Um item novo nasce `candidate`; o mesmo conteúdo vivo no mesmo escopo não duplica (devolve o existente).

        Recusas antes de existir linha: tipo fora do livro, texto com cara de credencial (resumo ou qualquer texto do
        conteúdo) e, para o sistema, conteúdo vetado.
        """
        if novo.kind not in KINDS_DE_ITEM:
            raise EntradaInvalida(f"'{novo.kind.value}' tem casa nativa: não nasce em learning_items.")
        if any(self._recusa(t) for t in (novo.summary, *_textos(novo.content))):
            raise NotaComCaraDeSegredo("O conteúdo tem formato ou assunto de credencial: não entra no livro.")
        vivo = self._repo.item_vivo(novo)
        if vivo is not None:
            return vivo
        if by == SYSTEM_ACTOR:
            self._conferir_veto(novo.content_hash, novo.escopo.chave(novo.kind), novo.app_version)
        return self._repo.criar_item(novo, by=by, estado=SkillState.CANDIDATE, detalhe=None,
                                     reason="nascimento", run_id=run_id)

    def registrar_sinal(self, sinal: NovoSinal, *, recusar_nota: bool = False, substituir: bool = False) -> int | None:
        """Grava um sinal (idempotente por `(kind, source_ref, created_by)`).

        A nota passa pela triagem de credencial: com `recusar_nota` (o botão do D2) a nota ruim é recusa (409) e nada
        é gravado; sem ele (a varredura) o sinal é gravado sem a nota e com `note_refused=1`. A nota que fica é
        redigida e cortada em `NOTA_MAX`.
        """
        if not sinal.created_by.strip():
            raise EntradaInvalida("O sinal precisa dizer quem o deu ('sistema' ou o operador).")
        nota = (sinal.note or "").strip()
        recusada = sinal.note_refused
        if nota and self._triagem.recusa(nota):
            if recusar_nota:
                raise NotaComCaraDeSegredo("A nota tem formato ou assunto de credencial e não foi gravada.")
            nota, recusada = "", True
        limpo = self._triagem.redigir(nota)[:NOTA_MAX] if nota else None
        return self._repo.registrar_sinal(replace(sinal, note=limpo, note_refused=recusada), substituir=substituir)

    # ================================================================== digest e curadoria
    def digerir_execucao(self, run_id: str) -> Relatorio:
        """Chamado quando a execução assenta (`scheduler.on_run_settled`, em thread). Cada minerador isolado."""
        if not self.ajustes.enabled:
            return Relatorio(pulado=True)
        return _rodar({m.nome: partial(m.minerar, run_id) for m in self._mineradores}, f"digest {run_id}")

    def curar(self) -> Relatorio:
        """Um passo da curadoria periódica: a régua diária dos últimos dias e os passos registrados."""
        if not self.ajustes.enabled:
            return Relatorio(pulado=True)
        agora = self._relogio()
        etapas: dict[str, Callable[[], int]] = {"diario": lambda: self._regua_diaria(agora)}
        etapas.update({p.nome: partial(p.executar, agora) for p in self._passos})
        return _rodar(etapas, "curadoria")

    def _fronteira(self, corte_minimo: datetime) -> datetime:
        """A partir de quando `ai_calls` está INTEIRO. A purga apaga `ts < corte`; depois de uma purga, a chamada mais
        antiga que sobrou é um limite seguro (≥ o corte da última purga) — vale até quando `log_retention_days`
        AUMENTOU e o corte de agora aponta para dias que já perderam chamadas."""
        primeira = self._repo.primeira_chamada()
        if primeira is None or not primeira.purgada_antes:
            return corte_minimo
        return max(primeira.ts, corte_minimo)

    def _regua_diaria(self, agora: datetime) -> int:
        """Recalcula POR INTEIRO os `dias_recalculados` mais recentes e preenche, sem sobrescrever, os dias intactos
        que ainda não têm linha (o que ficou para trás com o processo fora do ar). Nunca toca um dia que a purga já
        pode ter mordido: o dia que contém a fronteira não entra."""
        retencao = max(1, int(self._retencao_de_logs()))
        janela = dias_intactos(self._fronteira(agora - timedelta(days=retencao)), agora)
        if janela is None:
            return 0
        desde, ate = janela
        hoje = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        recentes = max(desde, (hoje - timedelta(days=max(1, self.ajustes.dias_recalculados) - 1)).strftime("%Y-%m-%d"))
        return self._repo.recalcular_diario(recentes, ate) + self._preencher_lacunas(desde, recentes)

    def _preencher_lacunas(self, desde: str, ate: str) -> int:
        gravadas = 0
        for inicio, fim in _faltantes(desde, ate, self._repo.dias_com_diario(desde, ate)):
            gravadas += self._repo.recalcular_diario(inicio, fim)
        return gravadas

    def antes_da_purga(self, corte_da_purga: datetime) -> int:
        """Chamado pela retenção ANTES de apagar `ai_calls` com `ts < corte_da_purga`: os dias ainda intactos que a
        purga vai morder e que a curadoria não chegou a gravar (processo fora do ar, aprendizado desligado) são
        gravados agora. Só preenche lacuna — o dia já agregado quando estava inteiro não é reescrito."""
        if not self.ajustes.enabled:
            return 0
        primeira = self._repo.primeira_chamada()
        if primeira is None:
            return 0                                   # sem chamada nenhuma: a purga não tira nada da régua
        # Nunca purgado: o dia da primeira chamada está inteiro e entra; purgado: ele pode ter sido mordido.
        inicio = primeira.ts if primeira.purgada_antes else primeira.ts.replace(hour=0, minute=0, second=0,
                                                                                microsecond=0)
        janela = dias_intactos(inicio, corte_da_purga)
        return self._preencher_lacunas(*janela) if janela else 0

    def aplicar_retencao(self) -> int:
        if not self.ajustes.enabled:
            return 0
        return self._repo.aplicar_retencao(self.ajustes.retencao, self._relogio())


def _rodar(etapas: dict[str, Callable[[], int]], onde: str) -> Relatorio:
    feito: dict[str, int] = {}
    falhas: list[str] = []
    for nome, fazer in etapas.items():
        try:
            feito[nome] = int(fazer())
        except Exception:  # noqa: BLE001 - uma etapa do aprendizado nunca derruba as outras nem quem chamou
            log.exception("aprendizado: %s falhou em %s", nome, onde)
            falhas.append(nome)
    return Relatorio(feito=feito, falhas=tuple(falhas))


def _faltantes(desde: str, ate: str, existentes: frozenset[str]) -> list[tuple[str, str]]:
    """Os dias de [desde, ate) sem linha na régua, agrupados em intervalos contíguos [início, fim)."""
    saida: list[tuple[str, str]] = []
    dia, fim = datetime.strptime(desde, "%Y-%m-%d"), datetime.strptime(ate, "%Y-%m-%d")
    aberto: str | None = None
    while dia < fim:
        chave = dia.strftime("%Y-%m-%d")
        if chave in existentes and aberto is not None:
            saida.append((aberto, chave))
            aberto = None
        elif chave not in existentes and aberto is None:
            aberto = chave
        dia += timedelta(days=1)
    if aberto is not None:
        saida.append((aberto, ate))
    return saida


def _textos(valor: JsonValue) -> list[str]:
    """Todo texto de um JSON (valores e chaves), para a triagem de credencial."""
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, list):
        return [t for v in valor for t in _textos(v)]
    if isinstance(valor, dict):
        return [t for k, v in valor.items() for t in (k, *_textos(v))]
    return []


__all__ = ["DetalheDoLivro", "LearningService", "Livro", "Relatorio", "dias_intactos"]
