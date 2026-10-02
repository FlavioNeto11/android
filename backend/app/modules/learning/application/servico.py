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
from datetime import UTC, datetime, timedelta
from functools import partial

from app.modules.learning.application.espera import AvisadorDeEspera
from app.modules.learning.application.obsolescencia import ContextoDeObsolescencia, LeitorDeObsolescencia
from app.modules.learning.application.ports import (Ajustes, CatalogoDeRisco, FontesDoLivro, Minerador, MudancaNativa,
                                                    NovoSinal, PassoDeCuradoria, PortaDeEventos,
                                                    RepositorioDeAprendizado, TriagemDeTexto)
from app.modules.learning.domain import relacoes as rel
from app.modules.learning.domain.ciclo import (SYSTEM_ACTOR, Actor, EntradaInvalida, NaoEncontrado,
                                               NotaComCaraDeSegredo, SkillState, TransicaoProibida,
                                               UseARotaDasHabilidades, Vetado, caminho_da_pessoa, conferir_transicao,
                                               motivo_do_veto)
from app.modules.learning.domain.conteudo import capability_unica, licao_legivel, tela_legivel
from app.modules.learning.domain.efeito import exposicao_json
from app.modules.learning.domain.livro import (EntradaDoLivro, ItemDeAprendizado, NovoItem, Transicao, a_revisar,
                                               contagem, entrada_do_item, estado_nativo, para_aprovar, status_nativo)
from app.modules.learning.domain.modo_por_app import modo_efetivo
from app.modules.learning.domain.promocao import Evidencia
from app.modules.learning.domain.saude import Saude, SinaisDeSaude, calcular
from app.modules.learning.domain.versao import quadro_da_tela, quadro_independente
from app.modules.learning.domain.vocabulario import (KINDS_DE_ITEM, LivroKind, Modo, ModoDeTelas, Origem, Posicao,
                                                     SourceKind)
from app.modules.skills.domain.document import JsonObject, JsonValue
from app.util import parse_iso

log = logging.getLogger("poc.aprendizado")

#: Tamanho máximo de uma nota de pessoa (o botão do D2 e a varredura).
NOTA_MAX = 500
#: Exposições de uma lição que o detalhe do livro mostra (as mais recentes).
EXPOSICOES_NO_DETALHE = 50


def _tema_do_item(kind: LivroKind, conteudo: JsonObject | None) -> str | None:
    """O que separa dois itens de mesmo `scope_key` (ver `rel.Parente`): o nome da regra de tela. Os outros tipos de
    item não têm critério seguro e ficam `None` (sem contradição derivada)."""
    nome = (conteudo or {}).get("tela") if kind is LivroKind.TELA else None
    return nome if isinstance(nome, str) and nome else None


@dataclass(frozen=True, slots=True)
class Livro:
    itens: tuple[EntradaDoLivro, ...]
    contagem: dict[str, dict[str, int]]
    #: A saúde de cada item (30.4), pela ref da trilha (`EntradaDoLivro.trail_ref`); a memória não tem. É a MESMA
    #: função do detalhe, então a lista e o detalhe nunca discordam do rótulo.
    saudes: dict[str, Saude] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DetalheDoLivro:
    entrada: EntradaDoLivro
    evidencias: tuple[Evidencia, ...]
    trilha: tuple[Transicao, ...]
    #: As exposições de uma lição (pacote A7): braço, tokens e desfecho de cada unidade; vazio nos outros tipos.
    exposicoes: tuple[JsonValue, ...] = ()
    #: O conteúdo legível do item (30.3): o que a receita, o fluxo, a habilidade, a lição ou a tela FAZEM, só do que
    #: já está no banco (`domain/conteudo.py`). `None` na memória (só a contagem sai) e na voz e preferência (texto
    #: de pessoa).
    conteudo: JsonObject | None = None
    #: A saúde do item (30.4); `None` na memória.
    saude: Saude | None = None
    #: O estado de versão do item (30.6, `domain/versao.py`, §7): em que versões foi validado, quais estão vivas no
    #: parque e o estado por versão. Sempre presente; o que não se sabe é `desconhecido`, nunca inventado.
    versao: JsonObject | None = None
    #: As relações derivadas do item (30.7, `domain/relacoes.py`, §6): substitui, substituída por, derivado de,
    #: absorvida e contradiz, montadas na leitura, sem tabela de arestas. Vazio quando nada se deriva.
    relacoes: tuple[JsonObject, ...] = ()


@dataclass(frozen=True, slots=True)
class Relatorio:
    """O que um digest ou um passo de curadoria fez: linhas por etapa e as etapas que falharam."""

    feito: dict[str, int] = field(default_factory=dict)
    falhas: tuple[str, ...] = ()
    pulado: bool = False


def _quando(texto: str) -> datetime | None:
    """A data de uma evidência (`…Z`) como `datetime` UTC; texto que não é data vira `None` (não conta como recente)."""
    try:
        d = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


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
                 mineradores: Sequence[Minerador] = (), passos: Sequence[PassoDeCuradoria] = (),
                 eventos: PortaDeEventos | None = None, catalogo_de_risco: CatalogoDeRisco | None = None) -> None:
        """`retencao_de_logs_dias`: o `log_retention_days` VIGENTE (muda com o processo no ar); é o que diz até
        onde `ai_calls` ainda está inteiro. `eventos`: a porta do `learning.needs_person` (30.21; sem ela, nada é
        publicado); `catalogo_de_risco`: os fatos do catálogo do app para a faixa B ou C."""
        self._repo = repo
        self._fontes = fontes
        self._triagem = triagem
        self._ajustes = ajustes
        self._relogio = relogio
        self._retencao_de_logs = retencao_de_logs_dias
        self._mineradores: list[Minerador] = list(mineradores)
        self._passos: list[PassoDeCuradoria] = list(passos)
        self._extensoes: list[object] = []
        self._espera = AvisadorDeEspera(eventos, catalogo_de_risco, relogio)

    @property
    def ajustes(self) -> Ajustes:
        return self._ajustes()

    def registrar_minerador(self, minerador: Minerador) -> None:
        self._mineradores.append(minerador)

    def registrar_passo(self, passo: PassoDeCuradoria) -> None:
        self._passos.append(passo)

    def anexar(self, extensao: object) -> None:
        """O serviço de um pacote seguinte (A3–A9), pendurado aqui pela composição: a apresentação o acha pelo tipo
        (`extensao`), sem o `AppState` conhecer cada um."""
        self._extensoes.append(extensao)

    def extensao[T](self, tipo: type[T]) -> T | None:
        for e in self._extensoes:
            if isinstance(e, tipo):
                return e
        return None

    # ================================================================== leitura única
    def livro(self, *, kind: LivroKind | None = None, state: SkillState | None = None, app: str | None = None,
              origem: Origem | None = None) -> Livro:
        todas = self._todas(kind)
        filtradas = tuple(e for e in todas if (state is None or e.state is state) and (app is None or e.app == app)
                          and (origem is None or e.origin is origem))
        return Livro(filtradas, contagem(filtradas), self.saudes(filtradas))

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
            return DetalheDoLivro(e, (), (), versao=quadro_independente())   # só a contagem: o conteúdo nunca sai
        exposicoes: tuple[JsonValue, ...] = ()
        if kind is LivroKind.LICAO:                     # as mais recentes: o braço e o desfecho de cada unidade
            todas = self._repo.exposicoes(e.ref, limite=100_000)
            exposicoes = tuple(exposicao_json(x) for x in todas[-EXPOSICOES_NO_DETALHE:])
        evidencias = tuple(self._repo.evidencias(e.trail_ref))
        trilha = tuple(self._repo.trilha(e.trail_ref))
        conteudo = self._conteudo(kind, e.ref)
        return DetalheDoLivro(e, evidencias, trilha, exposicoes, conteudo, saude=self.saude_de(e, evidencias, trilha),
                              versao=self._versao(e, evidencias), relacoes=self._relacoes(e, conteudo))

    # ================================================================== saúde (30.4)
    def saudes(self, entradas: Sequence[EntradaDoLivro]) -> dict[str, Saude]:
        """A saúde de cada entrada, pela ref da trilha. A evidência só é lida dos PUBLICADOS (os outros estados decidem
        o rótulo pelo estado), com o mesmo limite do detalhe: o rótulo é o mesmo nos dois."""
        saida: dict[str, Saude] = {}
        contexto = self._contexto_de_obsolescencia()             # os lotes são lidos uma vez para a lista inteira
        for e in entradas:
            evidencias = self._repo.evidencias(e.trail_ref) if e.state is SkillState.PUBLISHED else ()
            saude = self.saude_de(e, tuple(evidencias), contexto=contexto)
            if saude is not None:
                saida[e.trail_ref] = saude
        return saida

    def capabilities(self, entradas: Sequence[EntradaDoLivro]) -> dict[str, str | None]:
        """A capability de cada entrada, pela ref da trilha, para a hierarquia App → Capability → Item do painel. Em
        lote (uma leitura por tipo, nunca por linha), como `saudes`. Receita: a derivação do detalhe; lição e tela
        (itens): a `scope_capability` do item. Fluxo, habilidade, memória e o resto: `None` (o fluxo é um comando
        inteiro). O que não se sabe é `None`; nunca palpite."""
        refs_de_receita = [e.ref for e in entradas if e.kind is LivroKind.RECEITA]
        refs_de_item = [e.ref for e in entradas if e.kind in KINDS_DE_ITEM]
        da_receita = self._fontes.capabilities_das_receitas(refs_de_receita) if refs_de_receita else {}
        do_item = self._repo.capabilities_dos_itens(refs_de_item) if refs_de_item else {}
        saida: dict[str, str | None] = {}
        for e in entradas:
            if e.kind is LivroKind.RECEITA:
                saida[e.trail_ref] = da_receita.get(e.ref)
            elif e.kind in KINDS_DE_ITEM:
                saida[e.trail_ref] = capability_unica(do_item.get(e.ref))
            else:
                saida[e.trail_ref] = None
        return saida

    def _contexto_de_obsolescencia(self) -> ContextoDeObsolescencia | None:
        leitor = self.extensao(LeitorDeObsolescencia)
        return leitor.contexto() if leitor is not None else None

    def saude_de(self, e: EntradaDoLivro, evidencias: tuple[Evidencia, ...], trilha: tuple[Transicao, ...] = (), *,
                 contexto: ContextoDeObsolescencia | None = None) -> Saude | None:
        """A ÚNICA fonte do cálculo de saúde (30.4): lê os sinais do que o livro já tem e entrega a `domain/saude.py`.
        O que o livro não expõe (item sem contador, intervenção humana) vai como `None` e a dimensão sai
        `desconhecida`; nunca como zero. Os sinais de obsolescência (30.14) só existem com o leitor pendurado pela
        composição e só para o publicado; a tela leva o quadro de versão que o detalhe também mostra."""
        agora = self._relogio()
        if contexto is None:
            contexto = self._contexto_de_obsolescencia()
        obsolescencia = None
        if contexto is not None and e.state is SkillState.PUBLISHED:
            versao = self._versao(e, evidencias) if e.kind is LivroKind.TELA else None
            obsolescencia = contexto.sinais(e, versao=versao)
        if e.kind in (LivroKind.RECEITA, *KINDS_DE_ITEM):
            a_favor, contra = e.a_favor, e.contra          # contadores da própria fonte (sobrevivem à retenção)
        elif e.kind is LivroKind.FLUXO:                    # o fluxo não tem contador de acerto: só as evidências
            a_favor = sum(1 for x in evidencias if x.stance is Posicao.FOR)
            contra = sum(1 for x in evidencias if x.stance is not Posicao.FOR)
        else:
            a_favor = contra = None
        recentes: int | None = None
        if e.kind is not LivroKind.HABILIDADE and e.state is SkillState.PUBLISHED:
            corte = agora - timedelta(days=self.ajustes.saude.contestacao_dias)
            recentes = sum(1 for x in evidencias
                           if x.stance is not Posicao.FOR and (q := _quando(x.observed_at)) is not None and q >= corte)
        motivo = trilha[-1].reason if trilha and e.state in (SkillState.DEPRECATED, SkillState.DISABLED) else None
        return calcular(SinaisDeSaude(
            kind=e.kind, estado=e.state, agora=agora, criado_em=e.created_at, estado_desde=e.state_at,
            ultimo_uso=e.last_used_at, usos=e.uses, a_favor=a_favor, contra=contra, exige_o_dono=e.requires_owner,
            detalhe=motivo or e.detail, falhas_seguidas=e.falhas_seguidas, contestacoes_recentes=recentes,
            obsolescencia=obsolescencia),
            self.ajustes.saude)

    def _relacoes(self, e: EntradaDoLivro, conteudo: JsonObject | None) -> tuple[JsonObject, ...]:
        """O `relacoes` do detalhe (30.7): da leitura que o `conteudo` já fez (vizinhas da receita, `parent_version`),
        dos itens (`parent_id`, `absorvida:`) e das entradas do mesmo tipo (contradição). Só o que tem fonte."""
        achadas: list[JsonObject] = []
        tema: str | None = ""                       # receita, fluxo e habilidade: o `scope_key` já é exato
        if e.kind is LivroKind.RECEITA:
            achadas += rel.da_receita(conteudo)
        elif e.kind is LivroKind.HABILIDADE:
            skill_id, versao = (conteudo or {}).get("skill_id"), (conteudo or {}).get("versao")
            sucessoras = (self._fontes.sucessoras_da_habilidade(skill_id, versao)
                          if isinstance(skill_id, str) and isinstance(versao, int) else [])
            achadas += rel.da_habilidade(conteudo, sucessoras)
        elif e.kind in KINDS_DE_ITEM:
            tema = _tema_do_item(e.kind, conteudo)
            item = self._repo.item(e.ref)
            irmaos = self._repo.itens(kind=e.kind)
            pai = self._repo.item(item.parent_id) if item is not None and item.parent_id else None
            achadas += rel.do_item(parent_id=item.parent_id if item is not None else None,
                                   pai=entrada_do_item(pai) if pai is not None else None,
                                   filhos=[entrada_do_item(i) for i in irmaos if i.parent_id == e.ref])
            achadas += rel.absorvida(e, tema)
            if tema is not None and e.state in rel.VIVOS:
                achadas += rel.contradiz(e, tema, [rel.Parente(entrada_do_item(i), _tema_do_item(e.kind, i.content))
                                                   for i in irmaos])
        if e.kind is LivroKind.RECEITA and e.state in rel.VIVOS:
            # o `scope_key` da receita é a chave exata da etapa, e a versão nova já aposenta as vivas da chave: duas
            # vivas com caminho diferente é anomalia. Fluxo (`match_key` único) e habilidade (as versões da mesma
            # habilidade têm o mesmo comando) não têm critério seguro: ficam sem contradição derivada.
            achadas += rel.contradiz(e, tema, [rel.Parente(o, "") for o in self._todas(e.kind)])
        return tuple(rel.ordenar(achadas))

    def _versao(self, e: EntradaDoLivro, evidencias: Sequence[Evidencia]) -> JsonObject:
        """O `versao` do detalhe (30.6): a receita pela chave nas versões vivas; a tela pela regra `sem_casar`; os
        demais tipos não dependem de versão (§7)."""
        if e.kind is LivroKind.RECEITA:
            return self._fontes.versao(e.kind, e.ref) or quadro_independente() | {"estado": "desconhecido"}
        if e.kind is not LivroKind.TELA:
            return quadro_independente()
        favor = [t for t in (parse_iso(x.observed_at) for x in evidencias if x.stance is Posicao.FOR) if t]
        return quadro_da_tela(app=e.app, app_version=e.app_version, vivas=self._fontes.vivas(e.app) if e.app else (),
                              ultima_a_favor=max(favor, default=None), criada=parse_iso(e.created_at or ""),
                              agora=self._relogio())

    def _conteudo(self, kind: LivroKind, ref: str) -> JsonObject | None:
        """O `conteudo` do detalhe (30.3): das fontes nativas, pela fonte; lição e tela, do `content` do item. Voz e
        preferência são texto de pessoa e ficam sem conteúdo aqui (o título já é o que o detalhe expõe)."""
        if kind in (LivroKind.RECEITA, LivroKind.FLUXO, LivroKind.HABILIDADE):
            return self._fontes.conteudo(kind, ref)
        item = self._repo.item(ref) if kind in (LivroKind.LICAO, LivroKind.TELA) else None
        if item is None:
            return None
        if kind is LivroKind.TELA:
            return tela_legivel(item.content)
        return licao_legivel(item.content, texto=item.summary, app=item.escopo.app,
                             capability=item.escopo.capability, step_hash=item.escopo.step_hash,
                             role=item.escopo.role, tokens=item.tokens)

    def pendentes(self) -> tuple[EntradaDoLivro, ...]:
        """"Para aprovar": a fila do D1 (e a contagem da barra do topo)."""
        return tuple(e for e in self._todas(None) if para_aprovar(e))

    def revisar(self) -> tuple[EntradaDoLivro, ...]:
        """"Revisar": o legado ativo com efeito anterior ao D1, que nenhuma pessoa decidiu pelo livro ainda."""
        decididos = self._repo.refs_decididas_por_pessoa((LivroKind.RECEITA, LivroKind.FLUXO))
        return tuple(e for e in self._fontes.receitas() + self._fontes.fluxos() if a_revisar(e, decididos))

    # ================================================================== transições
    def _modo_publica(self, kind: LivroKind, app: str = "") -> bool:
        """O modo do TIPO (e, em lição e tela, o do PACOTE do item, §8.10) deixa o sistema publicar sozinho?"""
        a = self.ajustes
        if kind is LivroKind.LICAO:
            return modo_efetivo(a.modo_licoes, a.por_licoes, app) is Modo.ON
        if kind is LivroKind.TELA:
            return modo_efetivo(a.modo_telas, a.por_telas, app) is ModoDeTelas.ON
        if kind is LivroKind.PREFERENCIA:
            return a.modo_preferencias is Modo.ON
        if kind is LivroKind.VOZ:
            return False                                # a voz é sempre do dono (side_effect=1)
        return True                                     # receita e fluxo: os interruptores são os deles

    def contexto_de_publicacao(self, e: EntradaDoLivro) -> tuple[bool, str | None]:
        """O que a rota precisa para dizer por que o SISTEMA não publica `e`: `(modo_publica, veto)`. O modo é o do
        tipo e do pacote dele (`_modo_publica`); o veto é o `motivo_do_veto` do conteúdo no escopo, ou `None`. Só
        leitura: a mesma conta de `_mover_item`/`_mover_nativo`, sem mover nada (item sem conteúdo não tem veto)."""
        veto: str | None = None
        if e.content_hash:
            veto = motivo_do_veto(self._repo.desligamentos(e.content_hash, e.scope_key), agora=self._relogio(),
                                  app_version=e.app_version)
        return self._modo_publica(e.kind, e.app), veto

    def mudar_estado(self, kind: LivroKind, ref: str, para: SkillState, *, by: str, reason: str,
                     run_id: str | None = None, detalhe: str | None = None) -> EntradaDoLivro:
        """Move um item do livro, com a trilha. Pessoa ou sistema (`by='sistema'`); o D1 vale nos dois.

        Habilidade tem rota e ciclo próprios (409 com o endereço); memória segue a regra dela e não passa por aqui.
        `detalhe`: o `state_detail` que o item passa a ter (só nos itens do livro; ex.: a lição publicada entra na
        `fila_de_prova`, a desligada pela medida leva `medida:atrapalha`). Sem ele, o detalhe é limpo.
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
            return entrada_do_item(self._mover_item(kind, ref, para, by=by, reason=motivo, run_id=run_id,
                                                    detalhe=detalhe))
        self._mover_nativo(self.entrada(kind, ref), para, by=by, reason=motivo, run_id=run_id)
        return self.entrada(kind, ref)

    def mudar_status_nativo(self, kind: LivroKind, ref: str, status: str, *, by: str,
                            reason: str) -> EntradaDoLivro:
        """O status NATIVO pedido por uma rota legada (`PUT /api/flows/{id}` com `active|disabled`, `PUT
        /api/recipes/{id}` com `active|quarantined`), levado pelo MESMO caminho do livro: trilha, veto e guardas.

        O vocabulário da rota é o da fonte, e o mapa é o de sempre (`estado_nativo`: `active`→`published`,
        `disabled`/`quarantined`→`disabled`). Já estar lá não é transição (nada na trilha); o fluxo em prova que a
        pessoa liga passa por `validated` (`caminho_da_pessoa`). Cada passo é um `mudar_estado`: relê o item e confere
        de novo, então quem chama dentro de uma transação tem os passos e as trilhas juntos, ou nenhum.
        """
        if kind not in (LivroKind.RECEITA, LivroKind.FLUXO):
            raise EntradaInvalida(f"{kind.value} não tem status nativo movido por rota legada.")
        para = estado_nativo(kind, status)
        if para is None:
            raise EntradaInvalida(f"'{status}' não é um status de {kind.value}.")
        atual = self.entrada(kind, ref)
        if atual.state is None:
            raise TransicaoProibida(f"O estado '{atual.native_status}' de {kind.value} {ref} não é do livro.")
        for passo in caminho_da_pessoa(atual.state, para):
            atual = self.mudar_estado(kind, ref, passo, by=by, reason=reason)
        return atual

    def _mover_item(self, kind: LivroKind, ref: str, para: SkillState, *, by: str, reason: str,
                    run_id: str | None, detalhe: str | None = None) -> ItemDeAprendizado:
        item = self._repo.item(ref)
        if item is None or item.kind is not kind:
            raise NaoEncontrado(f"Não há {kind.value} '{ref}' no livro.")
        actor = conferir_transicao(item.state, para, by, side_effect=item.side_effect,
                                   human_origin=item.human_origin, modo_publica=self._modo_publica(kind, item.escopo.app))
        if actor is Actor.SYSTEM and para in (SkillState.VALIDATED, SkillState.PUBLISHED):
            self._conferir_veto(item.content_hash, item.escopo.chave(kind), item.app_version)
        novo = self._repo.transicionar_item(item, para, by=by, reason=reason, detalhe=detalhe, run_id=run_id)
        self.avisar_item(item, novo, by=by)
        return novo

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
                                   modo_publica=self._modo_publica(e.kind, e.app))
        if actor is Actor.SYSTEM and para in (SkillState.VALIDATED, SkillState.PUBLISHED) and e.content_hash:
            self._conferir_veto(e.content_hash, e.scope_key, e.app_version)
        self._repo.transicionar_nativo(
            MudancaNativa(kind=e.kind, ref=e.ref, de_status=e.native_status, para_status=para_status,
                          de_estado=e.state, para_estado=para, content_hash=e.content_hash, scope_key=e.scope_key,
                          app_version=e.app_version), by=by, reason=reason, run_id=run_id)
        self._espera.mudou_sem_falhar(e, replace(e, state=para, native_status=para_status),
                                      por_sistema=by == SYSTEM_ACTOR)

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
        criado = self._repo.criar_item(novo, by=by, estado=SkillState.CANDIDATE, detalhe=None,
                                       reason="nascimento", run_id=run_id)
        self.avisar_item(None, criado, by=by)
        return criado

    # ================================================================== o evento `learning.needs_person` (30.21)
    def avisar_item(self, antes: ItemDeAprendizado | None, depois: ItemDeAprendizado, *, by: str) -> None:
        """Chamado depois de um item de `learning_items` nascer (`antes=None`) ou mudar de estado: publica a entrada na
        espera do dono ou a saída dela (§8.11). Quem muda o estado SEM passar por `mudar_estado` (a tela absorvida,
        que confere a tabela e vai ao repositório) chama isto. Nunca levanta."""
        self._espera.mudou_sem_falhar(entrada_do_item(antes) if antes is not None else None, entrada_do_item(depois),
                                      por_sistema=by == SYSTEM_ACTOR, capability=depois.escopo.capability,
                                      sessao_ou_autenticacao=depois.source_kind is SourceKind.SESSION_UNKNOWN)

    def avisar_mudanca_nativa(self, kind: LivroKind, ref: str, de_status: str | None, para_status: str, *,
                              by: str) -> None:
        """A mudança de status que a LOJA da receita ou do fluxo fez (nascimento, prova, quarentena, substituição): ela
        não passa por `mudar_estado`. Lê a entrada já gravada e compara com o status anterior (`None` = nasceu). PROPAGA
        a falha: a loja chama isto DENTRO da transação dela, num `savepoint` próprio (22.5) que precisa vê-la."""
        if kind not in (LivroKind.RECEITA, LivroKind.FLUXO):
            return
        depois = self._fontes.receita(ref) if kind is LivroKind.RECEITA else self._fontes.fluxo(ref)
        if depois is None:
            return
        antes = None
        if de_status is not None:
            antes = replace(depois, state=estado_nativo(kind, de_status), native_status=de_status)
        self._espera.mudou(antes, depois, por_sistema=by == SYSTEM_ACTOR)

    def registrar_sinal(self, sinal: NovoSinal, *, recusar_nota: bool = False, substituir: bool = False,
                        um_por_evento: bool = False) -> int | None:
        """Grava um sinal (idempotente por `(kind, source_ref, created_by)`; com `um_por_evento`, o gesto de uma
        pessoa, por `(kind, source_ref)`: o primeiro autor fica — ver o repositório).

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
        return self._repo.registrar_sinal(replace(sinal, note=limpo, note_refused=recusada), substituir=substituir,
                                          um_por_evento=um_por_evento)

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
