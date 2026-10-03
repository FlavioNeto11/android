"""O curador por IA, a camada de APLICAÇÃO (30.11, `aprendizado-vivo.md` §8.5-8.8 e §8.11): o laço que acha os
gatilhos, monta o dossiê, aplica os filtros determinísticos e o orçamento proporcional, pede o parecer à porta
`CuradorDeIA`, valida a resposta (`domain/curador.validar_saida`) e grava a revisão em `learning_reviews`.

A IA nunca decide: o parecer é REGISTRO. Nenhuma transição sai daqui (o aceite é da pessoa, `conferir_aceite`); quem
transiciona continua sendo o `ciclo.py`. Por isso o laço é separado do `PassoDeCuradoria`, que promete nunca chamar
IA, e roda sob a trava de líder do ADR-064 (a mesma `curadoria`: a tomada é idempotente por dono).

Modos (`aprendizado.curador.modo`): `off` não roda; `shadow` revisa, grava e avisa o dono (`parecer_da_ia`) quando o
item B ou C já espera por ele; `on` revisa igual, e o painel mostra o parecer pendente na fila e no detalhe, com o
aceite (o da B também em lote). Em `shadow` o parecer pendente só aparece depois da decisão da pessoa (30.17,
`domain/parecer.py`): é a concordância às cegas que decide a saída do `shadow`.

Filtros, em ordem (§8.6): modo ≠ off → (item, dossie_hash) ainda não revisado → fora do cooldown → orçamento da janela
→ prioridade. Gatilhos ligados: `nova_pendencia_do_dono`, `a_revisar`, `degradando`, `obsoleto_provavel`, `conflito`
(publicado com contradição derivada ou contestação recente) e `pedido_da_pessoa` (30.17: o sinal `pediu_revisao` dos
últimos `JANELA_DO_PEDIDO_DIAS`, ainda sem revisão depois dele; só ele pula o cooldown e, desde o 30.30, vai na frente
de todos — prioridade `PEDIDO_DA_PESSOA`, também no pico, sob o teto da hora e o orçamento da janela; a classe A segue
só com sobra). O gatilho GRAVADO continua o mais forte entre os OUTROS do item. `versao_nova` e `grupo_de_falha_acima_do_minimo` existem no vocabulário e
ainda não têm fonte.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Protocol

from app.modules.learning.application.ports import (AjustesDoCurador, CuradorDeIA, FonteDeDossies, LeituraDaJanela,
                                                    NovaRevisao, PedidoDeRevisao, RecusaDoProvedor,
                                                    RegistroDeRevisoes, TriagemDeTexto)
from app.modules.learning.domain.ciclo import ErroDeAprendizado, SkillState
from app.modules.learning.domain.curador import (OPCOES_FECHADAS, VERSAO_DO_DOSSIE, Dossie, opcoes_do_dossie,
                                                 validar_saida)
from app.modules.learning.domain.espera import Faixa
from app.modules.learning.domain.livro import EntradaDoLivro
from app.modules.learning.domain.orcamento_do_curador import (Gatilho, Janela, MotivoDoCorte, ParametrosDoOrcamento,
                                                              Pretendente, custo_maximo, estimar_custo, mais_forte,
                                                              prioridade, repartir)
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.saude import Rotulo, Saude
from app.modules.learning.domain.vocabulario import LivroKind, Modo
from app.util import parse_iso

log = logging.getLogger("poc.aprendizado")

#: O template (prompt) é do hub (30.12); aqui se grava a forma do dossiê que ele recebeu.
TEMPLATE_ID = "curador"
TEMPLATE_VERSAO = f"dossie-v{VERSAO_DO_DOSSIE}"
#: O dossiê grande demais para `c_max` é refeito com menos evidências antes de ser recusado (§8.7).
EVIDENCIAS_NO_CORTE = (10, 0)
#: `validade` da revisão que não foi à IA e não se repete até o dossiê mudar.
RECUSADA_POR_CUSTO = "recusada:custo"
RECUSADA_POR_TRIAGEM = "recusada:triagem"
#: Os tipos que o curador revisa. Memória é só contagem (o conteúdo nunca sai); habilidade tem ciclo próprio.
KINDS_REVISADOS = frozenset({LivroKind.RECEITA, LivroKind.FLUXO, LivroKind.LICAO, LivroKind.TELA, LivroKind.VOZ,
                             LivroKind.PREFERENCIA})
#: Por quantos dias um pedido de revisão de pessoa espera a sua vez (o orçamento pode adiá-lo).
JANELA_DO_PEDIDO_DIAS = 7


@dataclass(frozen=True, slots=True)
class ResultadoDaVolta:
    rodou: bool
    revisadas: tuple[str, ...] = ()             # refs com parecer válido gravado
    invalidas: tuple[str, ...] = ()             # gravadas como `invalida:<motivo>`
    recusadas: tuple[str, ...] = ()             # gravadas como `recusada:<motivo>` (custo, triagem)
    cortados: dict[str, MotivoDoCorte] = field(default_factory=dict)
    avisos: int = 0                             # eventos `parecer_da_ia` publicados
    pico: bool = False
    orcamento: float = 0.0


@dataclass(slots=True)
class _Elegivel:
    entrada: EntradaDoLivro
    gatilho: Gatilho
    dossie: Dossie
    custo: float
    #: O gatilho que decide a prioridade: o pedido de pessoa fica registrado (`gatilho`), mas não rebaixa o item
    #: publicado em conflito da prioridade 1.
    gatilho_da_prioridade: Gatilho | None = None
    #: 30.30: uma pessoa pediu a revisão; o item fura a fila (`prioridade(pedido=True)`).
    pedido: bool = False


#: Chaves do conteúdo que são identificador, hash, data ou rótulo fechado: fora da triagem de texto. `variante`: o
#: idioma e a densidade da tela da receita (`en-US/xhdpi`), que a regra de credencial recusa; sem ela na lista, 24 de 26
#: receitas da cópia do central ficavam `recusada:triagem` e o curador nunca revisava receita (ensaio do 30.17, 03/10).
_CHAVES_ESTRUTURAIS = frozenset({"tipo", "app", "app_version", "assinatura", "step_key", "step_hash", "content_hash",
                                 "versao", "estado", "ref", "id", "step_id", "run_id", "source_run_id", "last_used_at",
                                 "ferramenta", "fonte", "skill_id", "source_kind", "schema_version", "omitido",
                                 "variante"})


def _textos_livres(valor: object, chave: str = "") -> list[str]:
    if isinstance(valor, str):
        return [] if chave in _CHAVES_ESTRUTURAIS else [valor]
    if isinstance(valor, dict):
        return [t for k, v in valor.items() for t in _textos_livres(v, str(k))]
    if isinstance(valor, list):
        return [t for v in valor for t in _textos_livres(v, chave)]
    return []


def janela_do_orcamento(j: LeituraDaJanela, precos: dict[str, list[float]]) -> Janela:
    """C_W e o gasto da última hora (§8.7): o medido mais a estimativa, pelo dossiê gravado, das revisões sem
    medida. A mesma conta serve à volta do curador e à métrica do aprendizado (30.8)."""
    estimado = sum(estimar_custo(t, precos) for t in j.tamanhos_sem_medida)
    estimado_na_hora = sum(estimar_custo(t, precos) for t in j.tamanhos_sem_medida_na_hora)
    return Janela(gasto_da_operacao=j.gasto_da_operacao, custos_medidos=j.custos_medidos,
                  gasto_da_curadoria=j.gasto_medido + estimado,
                  gasto_da_ultima_hora=j.gasto_medido_na_hora + estimado_na_hora,
                  revisoes_antes_de_hoje=j.revisoes_antes_de_hoje, revisoes_de_hoje=j.revisoes_de_hoje)


class LeitorDoLivro(Protocol):
    """O que o curador lê do serviço do Livro. O `LearningService` o cumpre (tipagem estrutural)."""

    def entrada(self, kind: LivroKind, ref: str) -> EntradaDoLivro: ...
    def pendentes(self) -> tuple[EntradaDoLivro, ...]: ...
    def revisar(self) -> tuple[EntradaDoLivro, ...]: ...
    def publicados(self) -> tuple[tuple[EntradaDoLivro, Saude | None], ...]: ...
    def contradicoes(self, e: EntradaDoLivro) -> bool: ...
    def avisar_parecer(self, e: EntradaDoLivro, faixa: Faixa) -> bool: ...


class CuradorPorIA:
    nome = "curador"

    def __init__(self, livro: LeitorDoLivro, dossies: FonteDeDossies, curador: CuradorDeIA,
                 registro: RegistroDeRevisoes, triagem: TriagemDeTexto, *, ajustes: Callable[[], AjustesDoCurador],
                 precos: Callable[[], dict[str, list[float]]], relogio: Callable[[], datetime]) -> None:
        self._livro = livro
        self._dossies = dossies
        self._curador = curador
        self._registro = registro
        self._triagem = triagem
        self._ajustes = ajustes
        self._precos = precos
        self._relogio = relogio
        self._parando = threading.Event()

    def parar(self) -> None:
        """Desligamento: a volta em curso termina o item que já está no provedor e não pede o seguinte (o lote vira
        `lote_interrompido`); volta nova nem começa. Sem isto, a thread da volta seguiria chamando o hub, e gravando em
        `learning_reviews`, com o `AppState` já fechando o banco."""
        self._parando.set()

    # ------------------------------------------------------------------ uma volta (o laço assíncrono é da composição)
    @property
    def intervalo_s(self) -> int:
        return max(60, int(self._ajustes().intervalo_s))

    def uma_volta(self, lider: Callable[[], int | None]) -> ResultadoDaVolta:
        aj = self._ajustes()
        if aj.modo is Modo.OFF or self._parando.is_set():
            return ResultadoDaVolta(rodou=False)
        if lider() is None:                     # outro backend é o líder: a volta é pulada, sem erro
            return ResultadoDaVolta(rodou=False)
        agora = self._relogio()
        precos = self._precos()
        elegiveis, recusadas = self._elegiveis(aj, agora, precos)
        p = ParametrosDoOrcamento(alfa=aj.alfa, k=aj.k, janela_dias=aj.janela_dias, m_cmax=aj.m_cmax)
        janela = self._janela(agora, aj.janela_dias, precos)
        pretendentes = [Pretendente(chave=x.entrada.trail_ref, custo_estimado=x.custo, desempate=x.entrada.trail_ref,
                                    prioridade=prioridade(x.dossie.classe, x.gatilho_da_prioridade or x.gatilho,
                                                          publicado=x.entrada.state is SkillState.PUBLISHED,
                                                          pedido=x.pedido))
                        for x in elegiveis]
        partilha = repartir(pretendentes, janela, p)
        if partilha.pico:
            log.warning("aprendizado: curador com pico de entrada (%d elegíveis); só as prioridades 1 e 2 nesta volta",
                        len(pretendentes))
        por_ref = {x.entrada.trail_ref: x for x in elegiveis}
        # O dossiê caro demais que nem o corte salvou: gravado `recusada:custo`, não se repete até mudar.
        for ref in partilha.recusados_por_custo:
            x = por_ref[ref]
            if self._gravar(x, RECUSADA_POR_CUSTO, None, agora, provedor="", modelo="", simulado=False):
                recusadas.append(ref)
        revisadas: list[str] = []
        invalidas: list[str] = []
        cortados = dict(partilha.cortados)
        avisos = 0
        for i, ref in enumerate(partilha.aprovados):
            if self._parando.is_set():
                cortados.update({r: MotivoDoCorte.LOTE_INTERROMPIDO for r in partilha.aprovados[i:]})
                break
            x = por_ref[ref]
            try:
                resposta = self._curador.revisar(self._pedido(x.dossie))
            except RecusaDoProvedor as e:
                if e.kind == "budget":
                    # O hub cortou por orçamento: o resto do lote para aqui, sem nova tentativa em laço.
                    cortados.update({r: MotivoDoCorte.LOTE_INTERROMPIDO for r in partilha.aprovados[i:]})
                    log.warning("aprendizado: curador interrompido pelo orçamento do hub (%d no lote)",
                                len(partilha.aprovados) - i)
                    break
                cortados[ref] = MotivoDoCorte.ERRO_DO_PROVEDOR
                log.warning("aprendizado: curador sem resposta do provedor para %s (%s)", ref, e.kind)
                continue
            validacao = validar_saida(resposta.bruto, x.dossie, probabilidade=resposta.probabilidade)
            saida = None
            if validacao.parecer is not None:
                parecer = validacao.parecer
                if parecer.conclusao is not None and self._triagem.recusa(parecer.conclusao):
                    # O único texto livre: com cara de credencial, não entra (o parecer continua válido sem ela).
                    parecer = replace(parecer, conclusao=None)
                saida = parecer.como_dados()
            simulado = self._curador.simulado if resposta.simulado is None else resposta.simulado
            provedor = self._curador.provedor if resposta.provedor is None else resposta.provedor
            if not self._gravar(x, validacao.validade, saida, agora, provedor=provedor,
                                modelo=resposta.modelo, simulado=simulado, ai_call_id=resposta.ai_call_id,
                                usd=0.0 if simulado else (resposta.usd or 0.0)):
                continue                                # outra réplica gravou o mesmo (item, dossiê) primeiro
            if validacao.parecer is None:
                invalidas.append(ref)
                continue
            revisadas.append(ref)
            # Parecer do adaptador SIMULADO nunca vira aviso ao dono: o evento chega ao Telegram (28.14) sem marca de
            # simulado, e um parecer falso lá é pior que nenhum. Fica só o registro (`learning_reviews.simulated=1`).
            if x.dossie.classe in (ClasseDeRisco.B, ClasseDeRisco.C) and not simulado:
                avisos += int(self._livro.avisar_parecer(x.entrada, Faixa(x.dossie.classe.value)))
        if cortados:
            log.info("aprendizado: curador deixou %d item(ns) para depois: %s", len(cortados),
                     ", ".join(sorted({m.value for m in cortados.values()})))
        return ResultadoDaVolta(rodou=True, revisadas=tuple(revisadas), invalidas=tuple(invalidas),
                                recusadas=tuple(recusadas), cortados=cortados, avisos=avisos, pico=partilha.pico,
                                orcamento=partilha.orcamento)

    # ------------------------------------------------------------------ gatilhos e filtros determinísticos
    def _candidatos(self, agora: datetime) -> dict[str, tuple[EntradaDoLivro, frozenset[Gatilho]]]:
        achados: dict[str, tuple[EntradaDoLivro, set[Gatilho]]] = {}

        def achar(e: EntradaDoLivro, g: Gatilho) -> None:
            if e.kind in KINDS_REVISADOS:
                achados.setdefault(e.trail_ref, (e, set()))[1].add(g)

        for e in self._livro.pendentes():
            achar(e, Gatilho.NOVA_PENDENCIA_DO_DONO)
        for e in self._livro.revisar():
            achar(e, Gatilho.A_REVISAR)
        for e, saude in self._livro.publicados():
            rotulo = saude.rotulo if saude is not None else None
            if rotulo is Rotulo.DEGRADANDO:
                achar(e, Gatilho.DEGRADANDO)
            elif rotulo is Rotulo.OBSOLETO_PROVAVEL:
                achar(e, Gatilho.OBSOLETO_PROVAVEL)
            if self._livro.contradicoes(e):
                achar(e, Gatilho.CONFLITO)
        for p in self._registro.pedidos(agora - timedelta(days=JANELA_DO_PEDIDO_DIAS)):
            ultima = self._registro.ultima(p.item_ref)
            if ultima is not None and ultima >= p.em:
                continue                                # já revisado depois do pedido
            try:
                e = self._livro.entrada(LivroKind(p.kind), p.ref)
            except (ValueError, ErroDeAprendizado):
                continue                                # o item sumiu ou o tipo saiu do livro
            achar(e, Gatilho.PEDIDO_DA_PESSOA)
        return {ref: (e, frozenset(gs)) for ref, (e, gs) in achados.items()}

    def _elegiveis(self, aj: AjustesDoCurador, agora: datetime,
                   precos: dict[str, list[float]]) -> tuple[list[_Elegivel], list[str]]:
        elegiveis: list[_Elegivel] = []
        recusadas: list[str] = []
        corte_do_cooldown = agora - timedelta(hours=aj.cooldown_h)
        for ref, (e, gatilhos) in sorted(self._candidatos(agora).items()):
            dossie = self._dossies.dossie(e)
            if dossie is None:
                continue
            if self._registro.existe(ref, dossie.dossie_hash):
                continue                                # 1 revisão por (item, dossiê): evidência nova, hash novo
            pedido = Gatilho.PEDIDO_DA_PESSOA in gatilhos
            ultima = self._registro.ultima(ref)
            quando = parse_iso(ultima) if ultima else None
            if quando is not None and quando > corte_do_cooldown and not pedido:
                continue                                # o pedido de pessoa é o único que pula o cooldown
            if ultima is not None and self._ja_revisado_menor(e, ref):
                continue
            outros = gatilhos - {Gatilho.PEDIDO_DA_PESSOA}
            x = _Elegivel(e, mais_forte(gatilhos), dossie, estimar_custo(dossie.tamanho_em_bytes(), precos),
                          gatilho_da_prioridade=mais_forte(outros) if outros else None, pedido=pedido)
            if self._recusa_o_conteudo(dossie):
                # §8.2: o dossiê passa pela triagem antes de sair; recusa = não revisa e registra (sem o dossiê).
                if self._gravar(x, RECUSADA_POR_TRIAGEM, None, agora, provedor="", modelo="", simulado=False,
                                guardar_dossie=False):
                    recusadas.append(ref)
                continue
            elegiveis.append(x)
        self._cortar_os_caros(elegiveis, aj, precos)
        # O dossiê refeito menor tem OUTRO hash: se ele já foi revisado, o item sai aqui, antes de chamar o provedor
        # (senão cada volta pagaria uma chamada para o INSERT recusar depois).
        elegiveis = [x for x in elegiveis if not self._registro.existe(x.entrada.trail_ref, x.dossie.dossie_hash)]
        return elegiveis, recusadas

    def _ja_revisado_menor(self, e: EntradaDoLivro, ref: str) -> bool:
        """O mesmo estado já foi revisado com o dossiê CORTADO por custo (outro hash): sem isto, a volta em que o item
        fica sozinho (sem corte) o mandaria de novo à IA com o dossiê inteiro. Só para item já revisado alguma vez."""
        for n in EVIDENCIAS_NO_CORTE:
            menor = self._dossies.dossie(e, max_evidencias=n)
            if menor is not None and self._registro.existe(ref, menor.dossie_hash):
                return True
        return False

    def _recusa_o_conteudo(self, d: Dossie) -> bool:
        """A triagem de credencial nas folhas de texto do CONTEÚDO (o resto do dossiê é id, hash, data e rótulo
        fechado, montado por lista branca). Não se triagem o JSON inteiro: a regra de credencial recusa hash longo,
        data ISO e `receita:12` (medido em 02/10), e recusaria todo dossiê."""
        return any(self._triagem.recusa(t) for t in _textos_livres(d.conteudo))

    def _cortar_os_caros(self, elegiveis: list[_Elegivel], aj: AjustesDoCurador,
                         precos: dict[str, list[float]]) -> None:
        """Acima de `c_max`, o dossiê é refeito com menos evidências (§8.7). O que ainda passar é recusado por
        `repartir` (`recusada:custo`). A mediana é a das estimativas desta volta, sempre (30.30: estimativa com estimativa)."""
        if len(elegiveis) < 2:
            return
        teto = custo_maximo([x.custo for x in elegiveis], aj.m_cmax)
        for x in elegiveis:
            for n in EVIDENCIAS_NO_CORTE:
                if x.custo <= teto:
                    break
                menor = self._dossies.dossie(x.entrada, max_evidencias=n)
                if menor is None:
                    break
                x.dossie, x.custo = menor, estimar_custo(menor.tamanho_em_bytes(), precos)

    def _janela(self, agora: datetime, dias: int, precos: dict[str, list[float]]) -> Janela:
        return janela_do_orcamento(self._registro.janela(agora, dias), precos)

    # ------------------------------------------------------------------ o pedido e o registro
    @staticmethod
    def _pedido(d: Dossie) -> PedidoDeRevisao:
        opcoes = {k: list(v) for k, v in OPCOES_FECHADAS.items()}
        opcoes.update({k: list(v) for k, v in opcoes_do_dossie(d).items()})
        return PedidoDeRevisao(dossie=d.como_dados(), dossie_hash=d.dossie_hash, classe=d.classe.value,
                               opcoes=opcoes, modelo_sugerido="escalada" if d.classe is ClasseDeRisco.C else "triagem")

    def _gravar(self, x: _Elegivel, validade: str, saida: dict[str, object] | None, agora: datetime, *,
                provedor: str, modelo: str, simulado: bool, guardar_dossie: bool = True,
                ai_call_id: int | None = None, usd: float = 0.0) -> bool:
        d = x.dossie
        nova = NovaRevisao(item_ref=x.entrada.trail_ref, item_kind=x.entrada.kind.value, scope_app=x.entrada.app or "",
                           gatilho=x.gatilho.value, dossie_hash=d.dossie_hash,
                           dossie=d.como_dados() if guardar_dossie else {}, template_id=TEMPLATE_ID,
                           template_versao=TEMPLATE_VERSAO, provedor=provedor, modelo=modelo, simulated=simulado,
                           validade=validade, saida=saida, classe_de_risco=d.classe.value,
                           politica=d.risco.politica.value, ai_call_id=ai_call_id, usd=usd)
        return self._registro.gravar(nova, agora) is not None


__all__ = ["EVIDENCIAS_NO_CORTE", "JANELA_DO_PEDIDO_DIAS", "KINDS_REVISADOS", "RECUSADA_POR_CUSTO",
           "RECUSADA_POR_TRIAGEM", "TEMPLATE_ID", "TEMPLATE_VERSAO", "CuradorPorIA", "LeitorDoLivro",
           "ResultadoDaVolta"]
