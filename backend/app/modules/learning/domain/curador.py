"""O curador por IA, a parte de DOMÍNIO (item 30.10, `aprendizado-vivo.md` §8.2-8.3): o dossiê de fatos que a IA lê e
o contrato da resposta dela. Puro: recebe linhas já lidas e devolve JSON; quem lê o banco, chama a IA, triagem o texto
e grava em `learning_reviews` é a aplicação (30.11). A classe de risco vem de `politica_de_risco.py`.

Três regras mandam aqui:
- **Só fatos, e só os que podem sair.** O dossiê é montado sem IA, por LISTA BRANCA de campos: nunca valor de
  parâmetro, texto digitado, texto de tela (o `texto`/`desc` de um seletor), texto de pessoa (o texto da lição, o
  comando do fluxo, o motivo livre de uma transição, a nota de um voto) nem título ou resumo do item. Item de sessão e
  autenticação não leva conteúdo nenhum (§8.4, linha C).
- **Cada fato tem id citável e estável** (`ev:`, `run:`, `tr:`, `voto:`, `sinal:`, `fk-`, `<kind>:<ref>`, e as seções
  `item`, `risco`, `conteudo`, `saude`, `versao`, `politica`). A resposta só pode citar o que está no dossiê.
- **`dossie_hash` é estável**: sha256 do JSON canônico, com as listas em ordem canônica (não a de chegada) e nada que
  dependa do relógio (a idade sai de `criado_em`, quem lê calcula). É a chave de idempotência (item, dossie_hash) da
  069: dossiê igual, revisão não se repete.

A validação da saída é determinística e fecha o contrato: a resposta é escolha entre RÓTULOS FECHADOS (decisão, faixa,
causa, riscos, inconsistências, falta), citação só de id do dossiê, `alvo` só de item relacionado presente, confiança
derivada da probabilidade medida pelo adaptador (nunca número dito pela IA), e um único texto livre, a `conclusao`,
curta e opcional. Nenhum campo além desses. A resposta inválida é gravada assim (`invalida:<motivo>`) e não gera ação.
A IA nunca decide: o parecer válido é recomendação (B) ou apoio (C); o aceite é da pessoa
(`politica_de_risco.conferir_aceite`).
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from app.modules.learning.domain.politica_de_risco import (ClasseDeRisco, Classificacao, FatosDeRisco, Razao,
                                                           classificar)
from app.modules.skills.domain.document import JsonObject, JsonValue, content_hash

#: Muda quando a FORMA do dossiê muda: um dossiê de forma nova tem hash novo e o item pode ser revisado de novo.
VERSAO_DO_DOSSIE = 1
#: Quantas evidências vão ao dossiê (§8.2, "até N, padrão 30"): as mais recentes. O total vai junto.
MAX_EVIDENCIAS = 30


# ------------------------------------------------------------------ os fatos (já lidos pela infraestrutura)
@dataclass(frozen=True, slots=True)
class AppDoItem:
    """Um app do fluxo multi-app (30.33-C), pelo id do registro (o vocabulário de `conteudo.etapas[].app` e de
    `conteudo.apps`) e pelo pacote (o de `item.app`). `principal`: o app onde rodam as etapas sem app próprio."""

    id: str
    pacote: str
    principal: bool = False


#: O que o `principal` quer dizer, no próprio dossiê do item multi-app (o curador leu o principal como divergência).
PRINCIPAL_DO_ITEM = "o app das etapas sem app próprio (conteudo.etapas[].app nulo); item.app é o pacote dele"
#: 30.36: as duas notas só entram quando valem (o dossiê do resto mantém as chaves e o `dossie_hash`).
FORMA_DA_EVIDENCIA = ("posicao `forma`: a execução fez o caminho do item e só reescreveu a forma (a pós-condição de "
                      "uma etapa sem efeito, ou um parâmetro fora da ação); não conta contra nem a favor")
#: 30.39: só no dossiê da receita. O contador acumulado não é evidência citável; a lista datada é.
CONTADORES_DA_RECEITA = ("`conteudo.uso` (`replay_ok`, `replay_fail`) são contadores acumulados SEM data, aparelho, versão "
                         "do app nem marca de real ou simulado; incluem o histórico de antes da evidência datada e também "
                         "o que já está na `lista`. A evidência citável é a `lista` (uma linha por execução e posição, "
                         "`for` = a receita levou a etapa até o fim, `against` = divergiu ou a etapa não terminou por "
                         "ela); a retenção guarda só as mais recentes, e `total` conta o que ficou")
#: 30.39: só com `conteudo.sombra.shadow_total == 0`. A sombra só corre com a receita candidata (a IA decide a etapa e
#: a receita é apenas comparada) ou com `ai.recipes: shadow`; com `ai.recipes: replay` a receita ativa ou validada
#: conduz a etapa e não volta à sombra; a concordância simulada não conta para a candidata.
SOMBRA_DA_RECEITA = ("sem amostra de sombra (`shadow_total` 0): a comparação com a IA só corre enquanto a receita é "
                     "candidata ou com `ai.recipes: shadow`; com `ai.recipes: replay` a receita ativa ou validada "
                     "conduz a etapa e a comparação com a IA não roda de novo para ela, e a concordância de execução "
                     "simulada não conta. A evidência é a `lista` datada; `sombra` não é falta que a validação "
                     "consiga produzir para esta receita")
SEM_CAMINHO_DA_RECEITA = ("variante sem caminho: o plano do fluxo ativo do comando de origem não chega a esta etapa "
                          "(outra variante dela é a que roda), e a validação não tem o que executar; sugerir aposentar")


@dataclass(frozen=True, slots=True)
class IdentidadeDoItem:
    """O item analisado. Sem `title`/`summary`: podem ser texto de pessoa."""

    kind: str
    ref: str
    app: str = ""
    capability: str = ""
    app_version: str | None = None
    estado: str | None = None
    origem: str = ""                        # `Origem` do livro: execucao | treino | pessoa | ensino | sistema
    side_effect: bool = False
    human_origin: bool = False
    criado_em: str | None = None
    #: Só no fluxo de MAIS DE UM app (30.33-C), na ordem do plano; vazio no resto. Vazio, o dossiê sai com as mesmas
    #: chaves de antes e o mesmo `dossie_hash` (nenhuma revisão nova por esta mudança fora dos multi-app).
    apps: tuple[AppDoItem, ...] = ()
    #: 30.36: a receita que a validação achou sem caminho (o plano do fluxo ativo não chega à etapa dela). A marca é
    #: para o curador sugerir aposentar; ninguém aposenta sozinho (o parecer é registro, o aceite é da pessoa).
    sem_caminho: bool = False

    @property
    def id_citavel(self) -> str:
        return f"{self.kind}:{self.ref}"


@dataclass(frozen=True, slots=True)
class Evidencia:
    """Uma linha de `learning_evidence`, sem o `detail` (texto livre)."""

    id: int
    posicao: str                            # for | against | conflict | forma (30.36)
    origin_ref: str                         # 'attempt:<id>' | 'step:<id>' | 'signal:<id>' | 'run:<id>'
    em: str                                 # `observed_at`, ISO
    run_id: str | None = None
    aparelho: str | None = None
    app_version: str | None = None
    simulated: bool = False

    @property
    def id_citavel(self) -> str:
        return f"ev:{self.id}"


@dataclass(frozen=True, slots=True)
class PassoDaTrilha:
    """Uma linha de `learning_transitions`, sem o `reason` (texto livre, pode ser da pessoa) e sem a sessão de quem
    decidiu (só se foi pessoa)."""

    id: int
    para: str
    em: str
    de: str | None = None
    por_pessoa: bool = False
    run_id: str | None = None

    @property
    def id_citavel(self) -> str:
        return f"tr:{self.id}"


@dataclass(frozen=True, slots=True)
class Relacao:
    """Um item relacionado (§6: predecessor, substituta...). É o único `alvo` possível de `substituir`/`fundir`."""

    tipo: str
    kind: str
    ref: str
    estado: str | None = None

    @property
    def id_citavel(self) -> str:
        return f"{self.kind}:{self.ref}"


@dataclass(frozen=True, slots=True)
class GrupoDeFalha:
    """Um grupo do backlog com o mesmo app × capability (§9)."""

    id: str                                 # 'fk-…'
    falha: str                              # `FailureKind`
    ocorrencias: int
    estado: str

    @property
    def id_citavel(self) -> str:
        return self.id


@dataclass(frozen=True, slots=True)
class Voto:
    """Um voto D2 da etapa, sem a nota (texto de pessoa)."""

    id: int
    veredito: str                           # certo | errado
    motivo: str | None = None               # `MotivoDoVoto`

    @property
    def id_citavel(self) -> str:
        return f"voto:{self.id}"


@dataclass(frozen=True, slots=True)
class Intervencao:
    """Um sinal de intervenção humana (`SignalKind`): tomou controle, cancelou, respondeu..."""

    id: int
    tipo: str
    em: str
    run_id: str | None = None

    @property
    def id_citavel(self) -> str:
        return f"sinal:{self.id}"


# ------------------------------------------------------------------ o conteúdo, por lista branca
def _so(origem: JsonValue, campos: Iterable[str]) -> JsonObject:
    if not isinstance(origem, dict):
        return {}
    return {c: origem[c] for c in campos if c in origem}


def _lista_de(origem: JsonValue) -> list[JsonValue]:
    return origem if isinstance(origem, list) else []


def _acao(a: JsonValue) -> JsonObject:
    saida = _so(a, ("indice", "ferramenta", "commit", "parametros", "segredo", "digita", "pacote", "duracao_ms",
                    "rolagem"))
    if isinstance(a, dict):
        # Do seletor, só o tipo e o `rid` (id de interface); `texto` e `desc` são texto de tela.
        saida["alvo"] = [_so(s, ("tipo", "rid")) for s in _lista_de(a.get("alvo"))]
        coleta = a.get("coleta")
        if isinstance(coleta, dict):
            saida["coleta"] = _so(coleta, ("exclusoes",))
    return saida


def _etapa(e: JsonValue) -> JsonObject:
    saida = _so(e, ("indice", "chave", "app", "capability", "efeito", "parametros", "segredo"))
    pos = e.get("pos_condicao") if isinstance(e, dict) else None
    saida["pos_condicao"] = _so(pos, ("tipo",)) if isinstance(pos, dict) else None
    return saida


def conteudo_do_dossie(legivel: JsonValue, *, sessao_ou_autenticacao: bool = False) -> JsonObject:
    """O conteúdo legível do §4 (`domain/conteudo.py`) reduzido ao que pode ir à IA. `conteudo.py` já tirou valores e
    nomes sigilosos; aqui saem também o texto de tela e o texto de pessoa. Tipo desconhecido: só o tipo."""
    tipo = legivel.get("tipo") if isinstance(legivel, dict) else None
    if not isinstance(legivel, dict) or not isinstance(tipo, str):
        return {"tipo": None}
    if sessao_ou_autenticacao:
        return {"tipo": tipo, "omitido": "sessao_ou_autenticacao"}
    if tipo == "receita":
        origem = legivel.get("origem")
        return {"tipo": tipo,
                "identidade": _so(legivel.get("identidade"), ("app", "app_version", "assinatura", "variante",
                                                              "step_key", "step_hash", "versao", "estado")),
                "acoes": [_acao(a) for a in _lista_de(legivel.get("acoes"))],
                "efeito": _so(legivel.get("efeito"), ("externo", "acoes_commit")),
                "capability": _so(legivel.get("capability"), ("nomes", "ambigua", "fonte")) or None,
                "origem": _so(origem, ("tipo", "ref", "step_id", "run_id")),
                "uso": _so(legivel.get("uso"), ("replay_ok", "replay_fail", "consecutive_fail", "last_used_at")),
                "sombra": _so(legivel.get("sombra"), ("shadow_agree", "shadow_total")),
                "substitui": _so(legivel.get("substitui"), ("id", "versao", "estado")) or None,
                "substituida_por": _so(legivel.get("substituida_por"), ("id", "versao", "estado")) or None}
    if tipo == "fluxo":
        # Sem `nome` nem `comando_modelo` (o pedido da pessoa), sem `alvo` (seletor) e sem a descrição da pós-condição.
        # Os apps vão (ids do catálogo, não texto): sem eles, o comando que atravessa apps (12.1) parece rodar todo no
        # app principal, e o curador julgaria "ler no Outlook" num fluxo do Instagram como incoerente.
        return {"tipo": tipo, "origem": _so(legivel.get("origem"), ("tipo", "source_run_id")),
                "app": legivel.get("app") if isinstance(legivel.get("app"), str) else None,
                "apps": [a for a in _lista_de(legivel.get("apps")) if isinstance(a, str)],
                "etapas": [_etapa(e) for e in _lista_de(legivel.get("etapas"))],
                "efeito": _so(legivel.get("efeito"), ("externo", "etapas_com_efeito"))}
    if tipo == "habilidade":
        return {"tipo": tipo, **_so(legivel, ("skill_id", "versao", "schema_version", "estado", "source_kind",
                                              "parent_version", "content_hash", "parametros", "total_de_nos"))}
    if tipo == "licao":
        # Sem o `texto` da lição (pode ser da pessoa) e sem o `valor` do alvo.
        alvo = legivel.get("alvo")
        return {"tipo": tipo, **_so(legivel, ("modelo", "acao", "escopo", "tokens")),
                "alvo": _so(alvo, ("tipo",)) if isinstance(alvo, dict) else None}
    if tipo == "tela":
        # Sem a `razao` (texto); os ids de interface só da tela que não exige autenticação.
        ids = [i for i in _lista_de(legivel.get("ids_todos")) if isinstance(i, str)]
        autenticada = legivel.get("autenticada") is True
        return {"tipo": tipo, **_so(legivel, ("tela", "casa", "autenticada")),
                "ids_todos": [] if autenticada else [i for i in sorted(ids)], "total_de_ids": len(ids)}
    return {"tipo": tipo}


# ------------------------------------------------------------------ o dossiê
SECOES_CITAVEIS = ("item", "risco", "conteudo", "saude", "versao", "politica")


@dataclass(frozen=True, slots=True)
class Dossie:
    item: IdentidadeDoItem
    fatos_de_risco: FatosDeRisco
    risco: Classificacao
    conteudo: JsonObject
    evidencias: tuple[Evidencia, ...] = ()
    evidencias_total: int = 0
    trilha: tuple[PassoDaTrilha, ...] = ()
    relacoes: tuple[Relacao, ...] = ()
    falhas: tuple[GrupoDeFalha, ...] = ()
    votos: tuple[Voto, ...] = ()
    intervencoes: tuple[Intervencao, ...] = ()
    execucoes: tuple[str, ...] = ()
    saude: JsonObject | None = None
    versao: JsonObject | None = None
    politica_vigente: JsonObject | None = None
    _citaveis: frozenset[str] = field(default=frozenset(), repr=False)

    @property
    def classe(self) -> ClasseDeRisco:
        return self.risco.classe

    @property
    def citaveis(self) -> frozenset[str]:
        return self._citaveis

    @property
    def alvos_possiveis(self) -> frozenset[str]:
        """Os itens que `substituir`/`fundir` podem apontar: só os relacionados, nunca o próprio item."""
        return frozenset(r.id_citavel for r in self.relacoes) - {self.item.id_citavel}

    def como_dados(self) -> JsonObject:
        i = self.item
        item: JsonObject = {"id": i.id_citavel, "kind": i.kind, "ref": i.ref, "app": i.app, "capability": i.capability,
                            "app_version": i.app_version, "estado": i.estado, "origem": i.origem,
                            "side_effect": i.side_effect, "human_origin": i.human_origin, "criado_em": i.criado_em}
        if i.apps:
            item["apps"] = [{"id": a.id, "pacote": a.pacote, "principal": a.principal} for a in i.apps]
            item["principal_e"] = PRINCIPAL_DO_ITEM
        if i.sem_caminho:
            item["sem_caminho"] = SEM_CAMINHO_DA_RECEITA
        evidencias: JsonObject = {"total": self.evidencias_total, "incluidas": len(self.evidencias),
                                  "lista": [{"id": e.id_citavel, "posicao": e.posicao, "origin_ref": e.origin_ref,
                                             "run_id": e.run_id, "aparelho": e.aparelho, "app_version": e.app_version,
                                             "simulated": e.simulated, "em": e.em} for e in self.evidencias]}
        if any(e.posicao == "forma" for e in self.evidencias):
            evidencias["forma_e"] = FORMA_DA_EVIDENCIA
        if i.kind == "receita":
            evidencias["contadores_e"] = CONTADORES_DA_RECEITA
            if (self.conteudo.get("sombra") or {}).get("shadow_total") == 0:
                evidencias["sombra_e"] = SOMBRA_DA_RECEITA
        return {
            "versao_do_dossie": VERSAO_DO_DOSSIE,
            "item": item,
            "risco": {**self.risco.como_dados(), "fatos": self.fatos_de_risco.como_dados()},
            "conteudo": self.conteudo,
            "evidencias": evidencias,
            "trilha": [{"id": t.id_citavel, "de": t.de, "para": t.para, "por_pessoa": t.por_pessoa, "em": t.em,
                        "run_id": t.run_id} for t in self.trilha],
            "relacoes": [{"id": r.id_citavel, "tipo": r.tipo, "estado": r.estado} for r in self.relacoes],
            "falhas": [{"id": f.id_citavel, "falha": f.falha, "ocorrencias": f.ocorrencias, "estado": f.estado}
                       for f in self.falhas],
            "votos": [{"id": v.id_citavel, "veredito": v.veredito, "motivo": v.motivo} for v in self.votos],
            "intervencoes": [{"id": n.id_citavel, "tipo": n.tipo, "em": n.em, "run_id": n.run_id}
                             for n in self.intervencoes],
            "execucoes": [f"run:{r}" for r in self.execucoes],
            "saude": self.saude,
            "versao": self.versao,
            "politica": self.politica_vigente,
            "citaveis": [c for c in sorted(self._citaveis)],
        }

    @property
    def dossie_hash(self) -> str:
        return content_hash(self.como_dados())

    def tamanho_em_bytes(self) -> int:
        """Para o corte por custo do 30.11 (§8.7): menos evidências, dossiê menor."""
        return len(json.dumps(self.como_dados(), ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _recentes_primeiro(evidencias: Iterable[Evidencia]) -> list[Evidencia]:
    return sorted(evidencias, key=lambda e: (e.em, e.id), reverse=True)


def montar_dossie(item: IdentidadeDoItem, fatos_de_risco: FatosDeRisco, conteudo_legivel: JsonValue, *,
                  evidencias: Iterable[Evidencia] = (), trilha: Iterable[PassoDaTrilha] = (),
                  relacoes: Iterable[Relacao] = (), falhas: Iterable[GrupoDeFalha] = (), votos: Iterable[Voto] = (),
                  intervencoes: Iterable[Intervencao] = (), execucoes: Iterable[str] = (),
                  saude: JsonObject | None = None, versao: JsonObject | None = None,
                  politica_vigente: JsonObject | None = None, max_evidencias: int = MAX_EVIDENCIAS) -> Dossie:
    """O dossiê do item, sem IA. A classe de risco é calculada aqui (nunca recebida pronta), para o dossiê e a política
    não divergirem. A ordem de chegada das listas não importa: tudo sai em ordem canônica."""
    risco = classificar(fatos_de_risco)
    todas = _recentes_primeiro(evidencias)
    incluidas = tuple(todas[:max(0, max_evidencias)])
    trilha_t = tuple(sorted(trilha, key=lambda t: t.id))
    relacoes_t = tuple(sorted(set(relacoes), key=lambda r: (r.tipo, r.kind, r.ref)))
    falhas_t = tuple(sorted(falhas, key=lambda f: f.id))
    votos_t = tuple(sorted(votos, key=lambda v: v.id))
    intervencoes_t = tuple(sorted(intervencoes, key=lambda n: n.id))
    runs = {r for r in execucoes if r}
    runs |= {e.run_id for e in incluidas if e.run_id}
    runs |= {t.run_id for t in trilha_t if t.run_id}
    runs |= {n.run_id for n in intervencoes_t if n.run_id}
    sessao = Razao.SESSAO_OU_AUTENTICACAO in risco.razoes
    citaveis = {item.id_citavel, "item", "risco", "conteudo"}
    citaveis |= {"saude"} if saude is not None else set()
    citaveis |= {"versao"} if versao is not None else set()
    citaveis |= {"politica"} if politica_vigente is not None else set()
    for grupo in (incluidas, trilha_t, relacoes_t, falhas_t, votos_t, intervencoes_t):
        citaveis |= {x.id_citavel for x in grupo}
    citaveis |= {f"run:{r}" for r in runs}
    return Dossie(item=item, fatos_de_risco=fatos_de_risco, risco=risco,
                  conteudo=conteudo_do_dossie(conteudo_legivel, sessao_ou_autenticacao=sessao),
                  evidencias=incluidas, evidencias_total=len(todas), trilha=trilha_t, relacoes=relacoes_t,
                  falhas=falhas_t, votos=votos_t, intervencoes=intervencoes_t, execucoes=tuple(sorted(runs)),
                  saude=saude, versao=versao, politica_vigente=politica_vigente, _citaveis=frozenset(citaveis))


# ------------------------------------------------------------------ o contrato de saída (§8.3, em rótulos fechados)
# A resposta é, sempre que dá, uma ESCOLHA entre rótulos fechados (orientação da coordenação, 02/10): um adaptador de
# `choice` (o provedor Jev, com probabilidade sobre um conjunto fechado) monta as opções de `OPCOES_FECHADAS` e, para
# `alvo` e `evidencias_citadas`, de `opcoes_do_dossie`. O único texto livre é a `conclusao`, curta, opcional e fora da
# decisão. A confiança nunca é número dado pela IA: vem da probabilidade da escolha medida pelo adaptador ou, sem ela,
# de um rótulo categórico. Nenhum prompt mora aqui: o template é do hub.
class Decisao(StrEnum):
    APROVAR = "aprovar"
    OBSERVAR = "observar"
    PEDIR_EVIDENCIA = "pedir_evidencia"
    REBAIXAR = "rebaixar"
    DESATIVAR = "desativar"
    SUBSTITUIR = "substituir"
    FUNDIR = "fundir"
    POSSIVELMENTE_OBSOLETO = "possivelmente_obsoleto"
    MANTER = "manter"


class Confianca(StrEnum):
    """Categórica, não percentual: sem número inventado."""

    BAIXA = "baixa"
    MEDIA = "media"
    ALTA = "alta"


class Causa(StrEnum):
    """Por que a decisão: a causa principal que o parecer aponta."""

    REPRODUZ_BEM = "reproduz_bem"
    FALHA_RECORRENTE = "falha_recorrente"
    EVIDENCIA_CONTRADITORIA = "evidencia_contraditoria"
    EVIDENCIA_INSUFICIENTE = "evidencia_insuficiente"
    VERSAO_NOVA_DO_APP = "versao_nova_do_app"
    SUBSTITUIDO_POR_OUTRO = "substituido_por_outro"
    DUPLICADO = "duplicado"
    INTERVENCAO_HUMANA = "intervencao_humana"
    RISCO_DO_EFEITO = "risco_do_efeito"
    OUTRA = "outra"


class RiscoApontado(StrEnum):
    EFEITO_EXTERNO = "efeito_externo"
    IRREVERSIVEL = "irreversivel"
    ALVO_ERRADO = "alvo_errado"
    CONTA_OU_SESSAO = "conta_ou_sessao"
    TEXTO_DE_PESSOA = "texto_de_pessoa"
    VERSAO_INCOMPATIVEL = "versao_incompativel"
    CUSTO = "custo"


class Inconsistencia(StrEnum):
    EVIDENCIA_A_FAVOR_E_CONTRA = "evidencia_a_favor_e_contra"
    VOTO_CONTRA_REPRODUCAO = "voto_contra_reproducao"
    CATALOGO_DIVERGE_DO_CONTEUDO = "catalogo_diverge_do_conteudo"
    VERSAO_DIVERGENTE = "versao_divergente"
    SAUDE_DIVERGE_DO_ESTADO = "saude_diverge_do_estado"


class Falta(StrEnum):
    """O que faltaria para decidir com mais segurança."""

    REPRODUCAO_EM_OUTRO_APARELHO = "reproducao_em_outro_aparelho"
    REPRODUCAO_NA_VERSAO_VIVA = "reproducao_na_versao_viva"
    EXECUCAO_REAL = "execucao_real"
    SOMBRA = "sombra"
    VOTO_DA_PESSOA = "voto_da_pessoa"
    DECISAO_DA_PESSOA = "decisao_da_pessoa"


class MotivoDeInvalidade(StrEnum):
    """O `<motivo>` de `learning_reviews.validade = 'invalida:<motivo>'`. Vocabulário fechado: o texto da IA nunca
    entra na coluna."""

    JSON_INVALIDO = "json_invalido"
    NAO_E_OBJETO = "nao_e_objeto"
    CAMPO_EXTRA = "campo_extra"
    CAMPO_AUSENTE = "campo_ausente"
    DECISAO_FORA_DO_VOCABULARIO = "decisao_fora_do_vocabulario"
    ROTULO_FORA_DO_VOCABULARIO = "rotulo_fora_do_vocabulario"  # faixa, causa, riscos, inconsistências, falta
    CONFIANCA_INVALIDA = "confianca_invalida"
    PROBABILIDADE_INVALIDA = "probabilidade_invalida"   # a do adaptador, fora de [0, 1]
    CONCLUSAO_INVALIDA = "conclusao_invalida"
    CITACAO_INVALIDA = "citacao_invalida"               # `evidencias_citadas` não é lista de texto
    CITACAO_DESCONHECIDA = "citacao_desconhecida"       # id que não está no dossiê (inventado)
    SEM_CITACAO = "sem_citacao"
    ALVO_AUSENTE = "alvo_ausente"
    ALVO_DESCONHECIDO = "alvo_desconhecido"
    ALVO_INDEVIDO = "alvo_indevido"


#: Os rótulos permitidos de cada campo fechado: o que um adaptador de `choice` oferece. `riscos`, `inconsistencias` e
#: `falta` são escolha múltipla do mesmo conjunto.
OPCOES_FECHADAS: Mapping[str, tuple[str, ...]] = {
    "decisao": tuple(d.value for d in Decisao),
    "faixa": tuple(c.value for c in ClasseDeRisco),
    "causa": tuple(c.value for c in Causa),
    "riscos": tuple(r.value for r in RiscoApontado),
    "inconsistencias": tuple(i.value for i in Inconsistencia),
    "falta": tuple(f.value for f in Falta),
    "confianca": tuple(c.value for c in Confianca),
}
CAMPOS_OBRIGATORIOS = frozenset({"decisao", "evidencias_citadas"})
CAMPOS_DA_SAIDA = CAMPOS_OBRIGATORIOS | {"alvo", "faixa", "causa", "confianca", "conclusao", "riscos",
                                         "inconsistencias", "falta"}
DECISOES_COM_ALVO = frozenset({Decisao.SUBSTITUIR, Decisao.FUNDIR})
LIMITE_DA_CONCLUSAO = 300
#: Probabilidade da escolha → confiança: abaixo de 0,60 é baixa, a partir de 0,85 é alta. Escolha de desenho do 30.10,
#: a recalibrar com o `shadow` (§8.9).
LIMIARES_DE_CONFIANCA = (0.60, 0.85)
_ORDEM_DA_CLASSE = {ClasseDeRisco.A: 0, ClasseDeRisco.B: 1, ClasseDeRisco.C: 2}


def opcoes_do_dossie(dossie: Dossie) -> dict[str, tuple[str, ...]]:
    """As opções que dependem do item: os ids citáveis e os alvos possíveis, em ordem estável."""
    return {"evidencias_citadas": tuple(sorted(dossie.citaveis)), "alvo": tuple(sorted(dossie.alvos_possiveis))}


def confianca_da_probabilidade(probabilidade: float) -> Confianca:
    baixa, alta = LIMIARES_DE_CONFIANCA
    if probabilidade < baixa:
        return Confianca.BAIXA
    return Confianca.ALTA if probabilidade >= alta else Confianca.MEDIA


@dataclass(frozen=True, slots=True)
class Parecer:
    decisao: Decisao
    evidencias_citadas: tuple[str, ...]
    confianca: Confianca | None = None      # derivada da probabilidade; sem ela, o rótulo; sem os dois, sem medida
    probabilidade: float | None = None      # a da escolha da decisão, medida pelo adaptador (nunca dita pela IA)
    alvo: str | None = None
    faixa: ClasseDeRisco | None = None      # a classe que a IA vê; nunca afrouxa a da política (`faixa_efetiva`)
    causa: Causa | None = None
    riscos: tuple[RiscoApontado, ...] = ()
    inconsistencias: tuple[Inconsistencia, ...] = ()
    falta: tuple[Falta, ...] = ()
    conclusao: str | None = None            # o único texto livre; não entra na decisão

    def faixa_efetiva(self, da_politica: ClasseDeRisco) -> ClasseDeRisco:
        """Vale a mais restritiva entre a da política e a que a IA apontou, exceto na A: lá a IA nunca muda o resultado
        da regra determinística, e a faixa que ela aponte fica só no registro."""
        if da_politica is ClasseDeRisco.A or self.faixa is None:
            return da_politica
        if _ORDEM_DA_CLASSE[self.faixa] <= _ORDEM_DA_CLASSE[da_politica]:
            return da_politica
        return self.faixa

    def como_dados(self) -> JsonObject:
        """O que vai a `learning_reviews.saida`: a forma do §8.3, já normalizada."""
        return {"decisao": self.decisao.value, "alvo": self.alvo,
                "faixa": None if self.faixa is None else self.faixa.value,
                "causa": None if self.causa is None else self.causa.value,
                "confianca": None if self.confianca is None else self.confianca.value,
                "probabilidade": self.probabilidade,
                "evidencias_citadas": [c for c in self.evidencias_citadas],
                "riscos": [r.value for r in self.riscos],
                "inconsistencias": [i.value for i in self.inconsistencias],
                "falta": [f.value for f in self.falta], "conclusao": self.conclusao}


@dataclass(frozen=True, slots=True)
class Validacao:
    parecer: Parecer | None
    motivo: MotivoDeInvalidade | None = None

    @property
    def ok(self) -> bool:
        return self.parecer is not None

    @property
    def validade(self) -> str:
        return "ok" if self.motivo is None else f"invalida:{self.motivo.value}"


class _Invalida(Exception):
    def __init__(self, motivo: MotivoDeInvalidade) -> None:
        self.motivo = motivo


def _rotulo[E: StrEnum](tipo: type[E], valor: object, motivo: MotivoDeInvalidade) -> E | None:
    if valor is None:
        return None
    if not isinstance(valor, str):
        raise _Invalida(motivo)
    try:
        return tipo(valor)
    except ValueError:
        raise _Invalida(motivo) from None


def _rotulos[E: StrEnum](tipo: type[E], valor: object) -> tuple[E, ...]:
    if valor is None:
        return ()
    if not isinstance(valor, list):
        raise _Invalida(MotivoDeInvalidade.ROTULO_FORA_DO_VOCABULARIO)
    saida: list[E] = []
    for v in valor:
        r = _rotulo(tipo, v, MotivoDeInvalidade.ROTULO_FORA_DO_VOCABULARIO)
        if r is not None and r not in saida:
            saida.append(r)
    return tuple(saida)


def _probabilidade(p: float | None) -> float | None:
    if p is None:
        return None
    if isinstance(p, bool) or not isinstance(p, (int, float)) or not 0.0 <= float(p) <= 1.0:
        raise _Invalida(MotivoDeInvalidade.PROBABILIDADE_INVALIDA)
    return float(p)


def validar_saida(bruto: str | Mapping[str, object], dossie: Dossie, *,
                  probabilidade: float | None = None) -> Validacao:
    """A resposta da IA contra o contrato do §8.3 e contra o dossiê que ela leu. Determinística; a primeira falha
    decide o motivo. `probabilidade`: a da escolha da decisão, quando o adaptador a mede (choice); dela sai a
    confiança, e o rótulo `confianca` da resposta só vale sem ela."""
    try:
        return Validacao(_parecer(bruto, dossie, probabilidade))
    except _Invalida as e:
        return Validacao(None, e.motivo)


def _parecer(bruto: str | Mapping[str, object], dossie: Dossie, probabilidade: float | None) -> Parecer:
    if isinstance(bruto, str):
        try:
            dados: object = json.loads(bruto)
        except ValueError:
            raise _Invalida(MotivoDeInvalidade.JSON_INVALIDO) from None
    else:
        dados = bruto
    if not isinstance(dados, Mapping):
        raise _Invalida(MotivoDeInvalidade.NAO_E_OBJETO)
    chaves = set(dados)
    if chaves - CAMPOS_DA_SAIDA:
        raise _Invalida(MotivoDeInvalidade.CAMPO_EXTRA)
    if CAMPOS_OBRIGATORIOS - chaves:
        raise _Invalida(MotivoDeInvalidade.CAMPO_AUSENTE)
    decisao = _rotulo(Decisao, dados["decisao"], MotivoDeInvalidade.DECISAO_FORA_DO_VOCABULARIO)
    if decisao is None:
        raise _Invalida(MotivoDeInvalidade.DECISAO_FORA_DO_VOCABULARIO)
    faixa = _rotulo(ClasseDeRisco, dados.get("faixa"), MotivoDeInvalidade.ROTULO_FORA_DO_VOCABULARIO)
    causa = _rotulo(Causa, dados.get("causa"), MotivoDeInvalidade.ROTULO_FORA_DO_VOCABULARIO)
    riscos = _rotulos(RiscoApontado, dados.get("riscos"))
    inconsistencias = _rotulos(Inconsistencia, dados.get("inconsistencias"))
    falta = _rotulos(Falta, dados.get("falta"))
    rotulo = _rotulo(Confianca, dados.get("confianca"), MotivoDeInvalidade.CONFIANCA_INVALIDA)
    p = _probabilidade(probabilidade)
    confianca = confianca_da_probabilidade(p) if p is not None else rotulo
    conclusao = dados.get("conclusao")
    if conclusao is not None and (not isinstance(conclusao, str) or not conclusao.strip()
                                  or len(conclusao) > LIMITE_DA_CONCLUSAO):
        raise _Invalida(MotivoDeInvalidade.CONCLUSAO_INVALIDA)
    citadas = dados["evidencias_citadas"]
    if not isinstance(citadas, list) or not all(isinstance(c, str) for c in citadas):
        raise _Invalida(MotivoDeInvalidade.CITACAO_INVALIDA)
    citadas_t = tuple(dict.fromkeys(citadas))
    if any(c not in dossie.citaveis for c in citadas_t):
        raise _Invalida(MotivoDeInvalidade.CITACAO_DESCONHECIDA)
    if not citadas_t and decisao is not Decisao.MANTER:
        raise _Invalida(MotivoDeInvalidade.SEM_CITACAO)
    alvo = dados.get("alvo")
    if decisao in DECISOES_COM_ALVO:
        if alvo is None:
            raise _Invalida(MotivoDeInvalidade.ALVO_AUSENTE)
        if not isinstance(alvo, str) or alvo not in dossie.alvos_possiveis:
            raise _Invalida(MotivoDeInvalidade.ALVO_DESCONHECIDO)
    elif alvo is not None:
        raise _Invalida(MotivoDeInvalidade.ALVO_INDEVIDO)
    return Parecer(decisao=decisao, evidencias_citadas=citadas_t, confianca=confianca, probabilidade=p,
                   alvo=alvo if isinstance(alvo, str) else None, faixa=faixa, causa=causa, riscos=riscos,
                   inconsistencias=inconsistencias, falta=falta, conclusao=conclusao)


__all__ = ["CAMPOS_DA_SAIDA", "CAMPOS_OBRIGATORIOS", "DECISOES_COM_ALVO", "LIMIARES_DE_CONFIANCA",
           "CONTADORES_DA_RECEITA", "FORMA_DA_EVIDENCIA", "LIMITE_DA_CONCLUSAO", "MAX_EVIDENCIAS", "OPCOES_FECHADAS", "PRINCIPAL_DO_ITEM",
           "SECOES_CITAVEIS", "SEM_CAMINHO_DA_RECEITA",
           "SOMBRA_DA_RECEITA", "VERSAO_DO_DOSSIE", "AppDoItem", "Causa",
           "Confianca", "Decisao",
           "Dossie", "Evidencia", "Falta", "GrupoDeFalha", "IdentidadeDoItem", "Inconsistencia", "Intervencao",
           "MotivoDeInvalidade", "Parecer", "PassoDaTrilha", "Relacao", "RiscoApontado", "Validacao", "Voto",
           "confianca_da_probabilidade", "conteudo_do_dossie", "montar_dossie", "opcoes_do_dossie", "validar_saida"]
