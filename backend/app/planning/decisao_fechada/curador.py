"""Triagem do curador do Livro em SOMBRA (item 31.8, R1 do roteiro, ADR-069): o Jev escolhe entre `manter`, `revisar`,
`rebaixar` e `descartar` (mais `nenhuma`) para o mesmo item que o curador principal (30.11/30.12) acabou de revisar.

- **Só sombra.** O parecer do curador principal é devolvido intacto; a triagem vai à porta em `shadow` (assíncrona, fora
  do caminho) e nada do que o Jev responde volta ao Livro. O GO para ligar `on` exige os limiares pré-registrados do golden
  set (31.7); sem eles, esta sombra só registra.
- **Dado F1, classe C0.** O estado são METADADOS e CONTAGENS do dossiê, por campos nomeados (`CAMPOS`): tipo, estado,
  origem, efeito, origem humana, classe e política de risco, contagens de evidência, falha, voto, intervenção e execução, e
  o rótulo de saúde quando é um rótulo. Nada de conteúdo (nem a lição "de texto fechado": fica para quando houver a lista
  dos campos fechados da lição), de app, de capability, de id ou de data. Só `licao` e `receita` (memória fora; fluxo é
  C2, F2).
- **Decisão real = o parecer do curador principal que VALEU**, mapeado (`TRIAGEM_DO_PARECER`), lido do registro
  (`learning_reviews` do mesmo `dossie_hash`, `validade = 'ok'`, `simulated = 0`) pelo relatório do 31.10, com
  `decisao_real_da_triagem`: a validade só existe depois do `revisar` (I2 da revisão do 31.9). Sem voto da pessoa, isto
  mede CONCORDÂNCIA com o curador, não acerto (roteiro R1); o rótulo de acerto é o desfecho posterior do item.

O módulo não importa o aprendizado: o pedido do curador é lido por forma (`PedidoDoCurador`), a resposta passa intacta,
e o decorador cumpre a porta `CuradorDeIA` por estrutura.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Final, Generic, Protocol, TypeVar

from . import privacidade
from .contrato import PedidoDeDecisao, pergunta_choice
from .porta import Porta, modo_efetivo

log = logging.getLogger("poc.ai")

PERGUNTA_TRIAGEM: Final = "curador_triagem"
KINDS_F1: Final[frozenset[str]] = frozenset({"licao", "receita"})
OPCOES: Final[dict[str, str]] = {
    "opt:manter": "keep: the item works as it is",
    "opt:revisar": "review: a person should look at it",
    "opt:rebaixar": "demote: lower its stage, it is not reliable now",
    "opt:descartar": "discard: it should leave the book",
}
#: O parecer do curador principal (`learning/domain/curador.Decisao`) na régua da triagem: o mapeamento fixo combinado com a
#: frente Aprendizado (resposta ao 31.7). `aprovar`, `possivelmente_obsoleto`, `substituir` e `fundir` não têm par na régua
#: grossa (alvo, ou mudança de estágio que a triagem não diz): ficam fora da comparação, sem decisão real casada.
TRIAGEM_DO_PARECER: Final[Mapping[str, str]] = {
    "manter": "opt:manter",
    "observar": "opt:revisar", "pedir_evidencia": "opt:revisar",
    "rebaixar": "opt:rebaixar",
    "desativar": "opt:descartar",
}
_INSTRUCOES: Final = (
    "The state describes one item that a device-automation platform learned by itself (a lesson or a recipe), only as "
    "counts and categories. Pick what should happen to it, or none if the facts do not tell.")
#: Campos que saem (CAMPOS_POR_ORIGEM["curador"]). Todo valor é um rótulo curto de vocabulário fechado ou um número.
CAMPOS: Final[frozenset[str]] = frozenset({
    "kind", "estado", "origem", "side_effect", "human_origin", "classe_de_risco", "politica",
    "evidencias_total", "evidencias_a_favor", "evidencias_contra", "evidencias_simuladas",
    "falhas", "falhas_ocorrencias", "votos", "intervencoes", "execucoes", "saude"})
_ROTULO_MAX: Final = 40
#: As posições da evidência que contam como contra no estado (31.25); as outras (`for`, `forma`, `invalida`) não.
POSICOES_CONTRA: Final[frozenset[str]] = frozenset({"against", "conflict"})

#: A versão do estado que a SOMBRA do runtime manda. O `v2` (os campos de sinal abaixo) só vira o da sombra depois de o
#: braço offline (31.11) medir, nos mesmos casos, respostas distintas entre estados distintos; sem sinal, ele não entra
#: (decisão da orquestradora, 03/10). Até lá, só o braço pede o `v2` (`TriagemDoCurador.pedido(..., versao_do_estado=)`).
ESTADO_DA_SOMBRA: Final = "v1"
VERSOES_DO_ESTADO: Final = ("v1", "v2")
#: Os campos de SINAL do `v2`. Na rodada real da R1 (31.11, 03/10 18:53Z), os 9 estados `v1` distintos deram a MESMA
#: resposta (`revisar`), e a regra de controle disse `manter` nos 29 casos: as contagens de evidência não explicam o
#: parecer do curador. Estes vêm de outras seções do dossiê (versão viva, uso, idade da evidência, saúde, risco, trilha).
#: Todo valor é contagem, `sim`/`nao`, faixa de idade ou código de vocabulário fechado: nunca texto, nome, id, data ou
#: número de versão.
CAMPOS_DE_SINAL: Final[frozenset[str]] = frozenset({
    "versao_estado", "versao_vivas", "versao_nao_testadas", "versao_viva_comprovada",
    "uso_ok", "uso_falhas", "uso_falhas_seguidas", "uso_idade", "evidencia_a_favor_idade",
    "saude_motivos", "risco_razoes", "trilha_transicoes", "trilha_por_pessoa", "trilha_ultimo_destino"})
#: Os vocabulários fechados do aprendizado que podem sair, repetidos aqui de propósito: o módulo não importa o
#: aprendizado, e o que sai é decidido neste arquivo. O teste confere que cada conjunto é EXATAMENTE o enum de lá
#: (`EstadoDeVersao`, `CodigoDoMotivo`, `Razao`, `SkillState`): um código novo lá não sai aqui sem um diff que se veja.
ESTADOS_DE_VERSAO: Final[frozenset[str]] = frozenset({
    "comprovado", "desconhecido", "em_prova", "falhando", "incompativel", "independente", "nao_testado", "superseded",
    "versao_aposentada"})
MOTIVOS_DE_SAUDE: Final[frozenset[str]] = frozenset({
    "absorvida", "aguarda_o_dono", "aguarda_repeticao", "amostra_pequena", "amostra_suficiente", "aposentado",
    "contestacao_desconhecida", "contestado_recentemente", "desligado", "efeito_sem_respaldo_no_catalogo",
    "eficacia_abaixo_do_minimo", "eficacia_acima_do_minimo", "eficacia_desconhecida", "estado_desconhecido",
    "falhas_seguidas", "idade_desconhecida", "nunca_usado", "sem_contestacao_recente", "sem_uso_recente",
    "substituta_viva", "usado_recentemente", "uso_desconhecido", "validado_aguarda_publicacao", "versao_fora_do_parque",
    "versao_viva_sem_reproducao"})
RAZOES_DE_RISCO: Final[frozenset[str]] = frozenset({
    "commit_fora_do_catalogo", "commit_sem_catalogo", "commit_sem_fatos_da_etapa", "efeito_declarado",
    "familia_de_alto_risco", "politica_manual", "reaprendido_de_evidencia_invalida", "risco_alto", "risco_medio",
    "sessao_ou_autenticacao", "texto_de_pessoa", "texto_para_outra_pessoa"})
ESTADOS_DO_ITEM: Final[frozenset[str]] = frozenset({"draft", "candidate", "validated", "published", "deprecated",
                                                    "disabled"})
#: As faixas de idade, em dias contra `agora`; `nunca` é "não há".
FAIXAS_DE_IDADE: Final = ("hoje", "ate_7d", "ate_30d", "mais_30d", "nunca")
#: Um conjunto de códigos vira UM valor (`a+b`); o conjunto vazio é `nenhum`.
NENHUM_CODIGO: Final = "nenhum"


class PedidoDoCurador(Protocol):
    @property
    def dossie(self) -> Mapping[str, object]: ...
    @property
    def dossie_hash(self) -> str: ...


# O pedido e a resposta são genéricos para o embrulho devolver EXATAMENTE o tipo do curador que ele decora (a porta
# `CuradorDeIA` do aprendizado pede `RespostaDeRevisao`, não a forma mais larga lida aqui).
Pedido = TypeVar("Pedido", bound=PedidoDoCurador, contravariant=True)
Resposta = TypeVar("Resposta", covariant=True)


class CuradorInterno(Protocol[Pedido, Resposta]):
    @property
    def provedor(self) -> str: ...
    @property
    def simulado(self) -> bool: ...

    def revisar(self, pedido: Pedido) -> Resposta: ...


def _rotulo(valor: object) -> str | None:
    """Só rótulo de vocabulário (minúsculas, dígitos, `_`, `-`) e curto; qualquer outra coisa não sai."""
    if isinstance(valor, bool):
        return "sim" if valor else "nao"
    if isinstance(valor, str) and 0 < len(valor) <= _ROTULO_MAX and all(c.islower() or c.isdigit() or c in "_-"
                                                                        for c in valor):
        return valor
    return None


def _lista(dossie: Mapping[str, object], chave: str) -> list[Mapping[str, object]]:
    valor = dossie.get(chave)
    return [x for x in valor if isinstance(x, Mapping)] if isinstance(valor, list) else []


def estado_do_dossie(dossie: Mapping[str, object]) -> dict[str, str]:
    """O estado C0 do item: só os `CAMPOS`, cada um rótulo fechado ou contagem. Campo sem valor confiável fica de fora."""
    item = dossie.get("item")
    item = item if isinstance(item, Mapping) else {}
    risco = dossie.get("risco")
    risco = risco if isinstance(risco, Mapping) else {}
    evid = dossie.get("evidencias")
    evid = evid if isinstance(evid, Mapping) else {}
    lista = [e for e in evid.get("lista", []) if isinstance(e, Mapping)] if isinstance(evid.get("lista"), list) else []
    falhas = _lista(dossie, "falhas")
    saude = dossie.get("saude")
    candidatos: dict[str, object] = {
        "kind": item.get("kind"), "estado": item.get("estado"), "origem": item.get("origem"),
        "side_effect": item.get("side_effect"), "human_origin": item.get("human_origin"),
        "politica": risco.get("politica"),
        "saude": saude.get("rotulo") if isinstance(saude, Mapping) else None,
    }
    estado = {k: r for k, v in candidatos.items() if (r := _rotulo(v)) is not None}
    if risco.get("classe") in ("A", "B", "C"):                 # a classe é maiúscula no vocabulário do aprendizado
        estado["classe_de_risco"] = str(risco["classe"])
    total = evid.get("total")
    contagens = {
        "evidencias_total": total if isinstance(total, int) and not isinstance(total, bool) else len(lista),
        "evidencias_a_favor": sum(1 for e in lista if e.get("posicao") == "for"),
        # 31.25: contra é só `against` e `conflict`. A `forma` (30.36) e a `invalida` (30.42) ficam na lista e no total,
        # mas não dizem nada contra o item; contá-las aqui mandava ao Jev um contra que o Livro não conta.
        "evidencias_contra": sum(1 for e in lista if e.get("posicao") in POSICOES_CONTRA),
        "evidencias_simuladas": sum(1 for e in lista if e.get("simulated") is True),
        "falhas": len(falhas),
        "falhas_ocorrencias": sum(o for f in falhas if isinstance(o := f.get("ocorrencias"), int)
                                  and not isinstance(o, bool)),
        "votos": len(_lista(dossie, "votos")),
        "intervencoes": len(_lista(dossie, "intervencoes")),
        "execucoes": len(dossie["execucoes"]) if isinstance(dossie.get("execucoes"), list) else 0,
    }
    estado.update({k: str(v) for k, v in contagens.items()})
    return estado


#: Os campos que a privacidade aceita do curador: os do `v1` e os de sinal do `v2` (31.11).
CAMPOS_V2: Final[frozenset[str]] = CAMPOS | CAMPOS_DE_SINAL


def _contagem(valor: object) -> str | None:
    return str(valor) if isinstance(valor, int) and not isinstance(valor, bool) and valor >= 0 else None


def _quando(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        quando = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return quando if quando.tzinfo is not None else quando.replace(tzinfo=UTC)


def _faixa(quando: datetime | None, agora: datetime) -> str:
    if quando is None:
        return "nunca"
    dias = (agora - quando).total_seconds() / 86400
    return "hoje" if dias < 1 else "ate_7d" if dias <= 7 else "ate_30d" if dias <= 30 else "mais_30d"


def _conjunto(codigos: list[object], vocabulario: frozenset[str]) -> str:
    """Os códigos do vocabulário, sem repetição e em ordem, num valor só. Código fora do vocabulário some: não sai."""
    dentro = sorted({c for c in codigos if isinstance(c, str) and c in vocabulario})
    return "+".join(dentro) if dentro else NENHUM_CODIGO


def estado_do_dossie_v2(dossie: Mapping[str, object], *, agora: datetime) -> dict[str, str]:
    """O `v1` mais os `CAMPOS_DE_SINAL`. `agora` é a referência das faixas de idade: no braço offline, a hora da revisão
    (o caso fica reprodutível); no runtime, a hora do pedido. Campo sem valor confiável fica de fora, como no `v1`."""
    estado = estado_do_dossie(dossie)
    versao = dossie.get("versao")
    if isinstance(versao, Mapping):
        if versao.get("estado") in ESTADOS_DE_VERSAO:
            estado["versao_estado"] = str(versao["estado"])
        for campo, chave in (("versao_vivas", "vivas"), ("versao_nao_testadas", "nao_testada_em")):
            valor = versao.get(chave)
            if isinstance(valor, list):
                estado[campo] = str(len(valor))
        por_versao = versao.get("por_versao")
        if isinstance(por_versao, list):
            comprovada = any(isinstance(p, Mapping) and p.get("viva") is True and p.get("estado") == "comprovado"
                             for p in por_versao)
            estado["versao_viva_comprovada"] = "sim" if comprovada else "nao"
    conteudo = dossie.get("conteudo")
    uso = conteudo.get("uso") if isinstance(conteudo, Mapping) else None
    if isinstance(uso, Mapping):
        for campo, chave in (("uso_ok", "replay_ok"), ("uso_falhas", "replay_fail"),
                             ("uso_falhas_seguidas", "consecutive_fail")):
            if (n := _contagem(uso.get(chave))) is not None:
                estado[campo] = n
        ultimo_uso = uso.get("last_used_at")
        if ultimo_uso is None or _quando(ultimo_uso) is not None:      # data ilegível: o campo fica de fora
            estado["uso_idade"] = _faixa(_quando(ultimo_uso), agora)
    evid = dossie.get("evidencias")
    lista = evid.get("lista") if isinstance(evid, Mapping) else None
    if isinstance(lista, list):
        a_favor = [q for e in lista if isinstance(e, Mapping) and e.get("posicao") == "for"
                   and (q := _quando(e.get("em"))) is not None]
        estado["evidencia_a_favor_idade"] = _faixa(max(a_favor) if a_favor else None, agora)
    saude = dossie.get("saude")
    motivos = saude.get("motivos") if isinstance(saude, Mapping) else None
    if isinstance(motivos, list):
        estado["saude_motivos"] = _conjunto([m.get("codigo") for m in motivos if isinstance(m, Mapping)],
                                            MOTIVOS_DE_SAUDE)
    risco = dossie.get("risco")
    razoes = risco.get("razoes") if isinstance(risco, Mapping) else None
    if isinstance(razoes, list):
        estado["risco_razoes"] = _conjunto(list(razoes), RAZOES_DE_RISCO)
    trilha = dossie.get("trilha")
    if isinstance(trilha, list):
        passos = [p for p in trilha if isinstance(p, Mapping)]
        estado["trilha_transicoes"] = str(len(passos))
        estado["trilha_por_pessoa"] = "sim" if any(p.get("por_pessoa") is True for p in passos) else "nao"
        datados = [(q, p) for p in passos if (q := _quando(p.get("em"))) is not None]
        if datados:
            ultimo = max(datados, key=lambda x: x[0])[1].get("para")
            if ultimo in ESTADOS_DO_ITEM:
                estado["trilha_ultimo_destino"] = str(ultimo)
    return estado


class TriagemDoCurador:
    """O consumidor: monta o pedido C0 e o entrega à porta em sombra. Inerte com a config padrão (`ativo()` falso).

    NÃO casa a decisão real (I2 da revisão do 31.9): o parecer do curador principal só vale depois de `validar_saida`, que
    o aprendizado roda DEPOIS do `revisar`, e pode ser simulado. A decisão real sai do registro no relatório do 31.10:
    `learning_reviews` do mesmo `dossie_hash` (o `ref` da linha da sombra), por `decisao_real_da_triagem`. Sem casamento,
    não há thread nem espera própria: no desligamento basta a porta (`Porta.encerrar` e `aguardar_sombras`)."""

    def __init__(self, porta: Porta) -> None:
        self._porta = porta

    def ativo(self) -> bool:
        """Como `ConsumidorDeIntencao.ativo`: com o envio fechado no código, NADA é feito (nem recusa gravada). A porta
        recusaria por privacidade e mediria a recusa, mas o que se mede aqui é o Jev respondendo, e ele não responde com o
        interruptor fechado. Aberto desde o 31.17: vale a config (`curador: shadow` com a porta ligada)."""
        return privacidade.JEV_RUNTIME_SEND_APPROVED and modo_efetivo("shadow", self._porta.cfg, "curador") == "shadow"

    @staticmethod
    def pedido(dossie: Mapping[str, object], dossie_hash: str, *, versao_do_estado: str = ESTADO_DA_SOMBRA,
               agora: datetime | None = None) -> PedidoDeDecisao | None:
        """O pedido de sombra do item, ou `None` fora de F1 (memória, fluxo, tela, voz, preferência)."""
        item = dossie.get("item")
        kind = item.get("kind") if isinstance(item, Mapping) else None
        if kind not in KINDS_F1:
            return None
        if versao_do_estado not in VERSOES_DO_ESTADO:
            raise ValueError(f"versão do estado desconhecida: {versao_do_estado}")
        estado = (estado_do_dossie(dossie) if versao_do_estado == "v1"
                  else estado_do_dossie_v2(dossie, agora=agora or datetime.now(UTC)))
        return PedidoDeDecisao(origem="curador", classe="C0", estado=estado,
                               perguntas=(pergunta_choice(PERGUNTA_TRIAGEM, _INSTRUCOES, OPCOES),), modo="shadow",
                               ref=dossie_hash)

    def observar(self, dossie: Mapping[str, object], dossie_hash: str) -> None:
        """Entrega a sombra à porta (em `shadow` ela agenda e volta na hora). Nunca levanta: medir nunca derruba o curador."""
        try:
            if not self.ativo():
                return
            pedido = self.pedido(dossie, dossie_hash)
            if pedido is not None:
                self._porta.consultar(pedido)
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: falha na sombra da triagem do curador")


def decisao_real_da_triagem(decisao: object, *, validade: object, simulado: object) -> str | None:
    """A decisão real da R1, para o relatório do 31.10 sobre a linha de `learning_reviews` do mesmo `dossie_hash`: o
    parecer do curador principal que VALEU (`validade == 'ok'`) e não veio de provedor simulado, na régua da triagem
    (`TRIAGEM_DO_PARECER`). Parecer inválido ou recusado, simulado, ou sem par na régua grossa: `None` (a sombra só
    registrou a escolha do Jev; não há com o que comparar)."""
    if validade != "ok" or simulado or not isinstance(decisao, str):
        return None
    real = TRIAGEM_DO_PARECER.get(decisao)
    return real if real in OPCOES else None


class CuradorComTriagemEmSombra(Generic[Pedido, Resposta]):
    """Decora o curador principal (porta `CuradorDeIA` do aprendizado): devolve o parecer DELE, intacto, e só depois
    entrega o item à triagem em sombra. Falha do curador principal sobe como antes, sem sombra."""

    def __init__(self, interno: CuradorInterno[Pedido, Resposta], triagem: TriagemDoCurador) -> None:
        self._interno = interno
        self._triagem = triagem

    @property
    def provedor(self) -> str:
        return self._interno.provedor

    @property
    def simulado(self) -> bool:
        return self._interno.simulado

    def revisar(self, pedido: Pedido) -> Resposta:
        resposta = self._interno.revisar(pedido)
        self._triagem.observar(pedido.dossie, pedido.dossie_hash)
        return resposta


__all__ = ["CAMPOS", "CAMPOS_DE_SINAL", "CAMPOS_V2", "ESTADO_DA_SOMBRA", "KINDS_F1", "OPCOES", "PERGUNTA_TRIAGEM",
           "POSICOES_CONTRA",
           "TRIAGEM_DO_PARECER", "VERSOES_DO_ESTADO", "CuradorComTriagemEmSombra", "TriagemDoCurador",
           "decisao_real_da_triagem", "estado_do_dossie", "estado_do_dossie_v2"]
