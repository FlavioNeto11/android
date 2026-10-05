"""O voto do D2 (ADR-054, decisão 2 do dono): o botão "deu certo / deu errado + motivo" e o que ele muda. Puro.

Nenhuma pergunta ao fim da execução: o botão é opcional, fica em cada item e na execução, e o efeito automático só vai
no sentido SEGURO — rebaixar. Promover continua sendo da repetição e do dono (D1); o voto nunca publica nada.

| voto | item | efeito |
|---|---|---|
| errado, navegação (`MOTIVOS_DE_NAVEGACAO`) | concluído | desliga o fluxo e a receita que o item USOU ou APRENDEU; evidência contra a versão de habilidade (nunca desabilitada) e a lição exposta (2 refutações a desligam); etapas comprovadas pela tela → falso positivo do verificador |
| errado, navegação | falhou ou outro | só o relatório (a falha já está contada) |
| errado, `texto_ruim` | qualquer | evidência para a voz do perfil; nada de navegação é rebaixado |
| errado, `pediu_ajuda_a_toa` | qualquer | evidência para tela e lição na etapa onde parou |
| errado, `demorou_ou_gastou` ou `outro` | qualquer | só o relatório |
| certo | concluído | evidência a favor do que o item usou e aprendeu |
| certo | falhou ou incerto | falso negativo do verificador; o status NÃO muda (para isso existe "Confirmar concluído") |
| qualquer | execução simulada | nada: o voto é gravado com `simulated=1` e não rebaixa nada real |

O "reativar" de um clique só acompanha o desligamento do que ESTAVA publicado (`reativar_desfaz`): do candidato ou
do validado, a única volta da tabela do D1 (`→ published`) promoveria em vez de desfazer.

O falso positivo e o falso negativo não viram linha de backlog aqui: vão no próprio sinal (`step_verified` e
`data.verificador`), que é a fonte do relatório "O que mais falha" (pacote A3). E nenhum dos dois vira lição do
verificador (ADR-024).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.ciclo import EntradaInvalida, SkillState
from app.modules.learning.domain.vocabulario import (MOTIVOS_DE_NAVEGACAO, LivroKind, MotivoDoVoto, Polaridade,
                                                     Veredito)

_S = SkillState

#: Estados com transição para `disabled` na tabela do D1 (e, na receita e no fluxo, um status nativo equivalente).
#: `deprecated` (receita substituída) e o que já está `disabled` ficam como estão.
DESLIGAVEIS = frozenset({_S.CANDIDATE, _S.VALIDATED, _S.PUBLISHED})

#: "Deu errado" de navegação de pessoas DIFERENTES (ou em itens diferentes) contra a mesma lição exposta: com tantas
#: refutações, o sistema a desliga (rebaixar é automático; o veto dele vale 90 dias ou até mudar a versão do app).
REFUTACOES_PARA_DESLIGAR = 2


class Desfecho(StrEnum):
    SUCESSO = "sucesso"      # objetivo `succeeded`; execução `completed`
    FALHA = "falha"          # objetivo `failed`/`uncertain`; execução `failed`/`completed_with_issues`
    OUTRO = "outro"          # em andamento, esperando a pessoa, cancelado


_DO_OBJETIVO: Mapping[str, Desfecho] = {"succeeded": Desfecho.SUCESSO, "failed": Desfecho.FALHA,
                                        "uncertain": Desfecho.FALHA}
_DA_EXECUCAO: Mapping[str, Desfecho] = {"completed": Desfecho.SUCESSO, "failed": Desfecho.FALHA,
                                        "completed_with_issues": Desfecho.FALHA}


def desfecho(status: str, *, da_execucao: bool) -> Desfecho:
    """O desfecho do item votado pelo status dele (do objetivo, ou da execução quando o voto é dela)."""
    return (_DA_EXECUCAO if da_execucao else _DO_OBJETIVO).get(status, Desfecho.OUTRO)


class Uso(StrEnum):
    """Como o item se relaciona com o conhecimento: é o que diz se o voto o alcança."""

    USOU = "usou"              # `runs.flow_id`, `attempts.recipe_id`, a versão de habilidade executada
    APRENDEU = "aprendeu"      # `flows.source_run_id`, `recipes.learned_from_step`
    EXPOSTA = "exposta"        # lição no braço `with` (a do braço de controle não foi ao prompt: o voto não a alcança)


class AcaoDoEfeito(StrEnum):
    DESLIGAR = "desligar"                      # → disabled (fluxo `disabled`, receita `quarantined`, lição `disabled`)
    EVIDENCIA_CONTRA = "evidencia_contra"
    EVIDENCIA_A_FAVOR = "evidencia_a_favor"
    BACKLOG = "backlog"                        # falso positivo ou negativo do verificador, pelo sinal
    VOZ = "voz"                                # evidência para a voz do perfil (pacote A9)
    TELA_E_LICAO = "tela_e_licao"              # evidência para tela e lição na etapa onde parou (A7 e A8)
    RELATORIO = "relatorio"                    # só o relatório "O que mais falha"


class Verificador(StrEnum):
    FALSO_POSITIVO = "falso_positivo"          # comprovou pela tela o que a pessoa diz que deu errado
    FALSO_NEGATIVO = "falso_negativo"          # não comprovou o que a pessoa diz que deu certo


#: A referência estável de cada caso no backlog (o sucesso mascarado fica sempre no topo do relatório).
REF_NO_BACKLOG: Mapping[Verificador, str] = {Verificador.FALSO_POSITIVO: "verificacao_falso_positivo",
                                             Verificador.FALSO_NEGATIVO: "verificacao_falso_negativo"}


@dataclass(frozen=True, slots=True)
class Conhecimento:
    """Algo do livro que o item votado usou, aprendeu ou teve no prompt, com o estado de AGORA."""

    kind: LivroKind
    ref: str
    estado: SkillState | None
    uso: Uso


@dataclass(frozen=True, slots=True)
class ItemVotado:
    """O item (objetivo) ou a execução inteira, no que importa ao voto.

    `etapas_comprovadas`: etapas concluídas com prova da tela (`verified=true`); `etapas_a_mao`: concluídas pela
    confirmação de uma pessoa (`verified=false`). `perfil`: a persona do item (a voz); `etapa`: onde ele parou.
    """

    desfecho: Desfecho
    simulado: bool
    etapas_comprovadas: int
    etapas_a_mao: int
    conhecimentos: tuple[Conhecimento, ...] = ()
    perfil: str | None = None
    etapa: str | None = None

    @property
    def comprovado_pela_tela(self) -> bool | None:
        """Toda etapa concluída foi comprovada pela tela? `None` quando nenhuma concluiu (não há o que dizer).

        Uma etapa confirmada à mão tira a culpa do verificador: foi uma pessoa quem disse que estava feito."""
        if self.etapas_comprovadas + self.etapas_a_mao == 0:
            return None
        return self.etapas_a_mao == 0


@dataclass(frozen=True, slots=True)
class Efeito:
    """Uma mudança pedida pelo voto. `kind`: o tipo do livro (receita, fluxo, habilidade, lição) ou o destino
    (backlog, voz, tela, relatório). Na evidência, `de == para` (o estado não muda)."""

    acao: AcaoDoEfeito
    kind: str
    ref: str
    de: SkillState | None = None
    para: SkillState | None = None
    #: Como o item votado chegou a ele (usou, aprendeu, exposta), quando o efeito é sobre algo do livro.
    uso: Uso | None = None


@dataclass(frozen=True, slots=True)
class PlanoDoVoto:
    polaridade: Polaridade
    verificador: Verificador | None
    efeitos: tuple[Efeito, ...]


def conferir_voto(veredito: Veredito, motivo: MotivoDoVoto | None) -> None:
    """'Deu errado' exige o motivo (é o que diz o que rebaixar); 'deu certo' não leva motivo (o vocabulário é do que
    deu errado, e um motivo ali seria lido como queixa)."""
    if veredito is Veredito.ERRADO and motivo is None:
        raise EntradaInvalida("'Deu errado' precisa do motivo: é ele que diz o que rebaixar.")
    if veredito is Veredito.CERTO and motivo is not None:
        raise EntradaInvalida("O motivo é só do 'deu errado'.")


def _vivos(item: ItemVotado) -> list[Conhecimento]:
    return [c for c in item.conhecimentos if c.estado in DESLIGAVEIS]


def efeitos_do_voto(veredito: Veredito, motivo: MotivoDoVoto | None, item: ItemVotado) -> PlanoDoVoto:
    conferir_voto(veredito, motivo)
    if veredito is Veredito.CERTO or motivo is None:        # sem motivo só chega aqui o 'certo' (conferido acima)
        return _certo(item)
    polaridade = Polaridade.NEGATIVE
    if item.simulado:
        return PlanoDoVoto(polaridade, None, ())
    if motivo is MotivoDoVoto.TEXTO_RUIM:
        return PlanoDoVoto(polaridade, None, (Efeito(AcaoDoEfeito.VOZ, "voz", item.perfil or ""),))
    if motivo is MotivoDoVoto.PEDIU_AJUDA_A_TOA:
        return PlanoDoVoto(polaridade, None, (Efeito(AcaoDoEfeito.TELA_E_LICAO, "tela", item.etapa or ""),))
    relatorio = Efeito(AcaoDoEfeito.RELATORIO, "relatorio", motivo.value)
    if motivo not in MOTIVOS_DE_NAVEGACAO or item.desfecho is not Desfecho.SUCESSO:
        return PlanoDoVoto(polaridade, None, (relatorio,))
    efeitos: list[Efeito] = []
    for c in _vivos(item):
        if c.kind in (LivroKind.FLUXO, LivroKind.RECEITA):
            efeitos.append(Efeito(AcaoDoEfeito.DESLIGAR, c.kind.value, c.ref, c.estado, _S.DISABLED, c.uso))
        elif c.kind in (LivroKind.HABILIDADE, LivroKind.LICAO):
            # A habilidade tem ciclo próprio (e publicar é só de pessoa): o voto vira evidência e backlog, nunca
            # desabilita. A lição acumula refutações; quem a desliga é a contagem (`licao_refutada`).
            efeitos.append(Efeito(AcaoDoEfeito.EVIDENCIA_CONTRA, c.kind.value, c.ref, c.estado, c.estado, c.uso))
    verificador = Verificador.FALSO_POSITIVO if item.comprovado_pela_tela else None
    if verificador is not None:
        efeitos.append(Efeito(AcaoDoEfeito.BACKLOG, "backlog", REF_NO_BACKLOG[verificador]))
    efeitos.append(relatorio)
    return PlanoDoVoto(polaridade, verificador, tuple(efeitos))


def _certo(item: ItemVotado) -> PlanoDoVoto:
    polaridade = Polaridade.POSITIVE
    if item.simulado:
        return PlanoDoVoto(polaridade, None, ())
    if item.desfecho is Desfecho.FALHA:
        return PlanoDoVoto(polaridade, Verificador.FALSO_NEGATIVO,
                           (Efeito(AcaoDoEfeito.BACKLOG, "backlog", REF_NO_BACKLOG[Verificador.FALSO_NEGATIVO]),))
    if item.desfecho is not Desfecho.SUCESSO:
        return PlanoDoVoto(polaridade, None, ())
    return PlanoDoVoto(polaridade, None, tuple(
        Efeito(AcaoDoEfeito.EVIDENCIA_A_FAVOR, c.kind.value, c.ref, c.estado, c.estado, c.uso) for c in _vivos(item)))


def licao_refutada(refutacoes: int) -> bool:
    """A lição já foi refutada por votos o bastante para o sistema desligá-la?"""
    return refutacoes >= REFUTACOES_PARA_DESLIGAR


#: O começo do motivo que o voto grava na trilha ao desligar. O "Aprendizado desta execução" (`domain/aprendido.py`)
#: reconhece por ele o desligamento que veio do voto de uma pessoa, e não da execução.
_MOTIVO_DO_VOTO = "deu errado:"


def motivo_do_desligamento(reason: MotivoDoVoto | None, fonte: str) -> str:
    """O motivo, na trilha, do que o voto desligou: `deu errado: <motivo> (voto em <objective:…|run:…>)`."""
    return f"{_MOTIVO_DO_VOTO} {reason.value if reason else 'sem motivo'} (voto em {fonte})"


def veio_do_voto(motivo: str) -> bool:
    """A transição foi gravada pelo voto (`motivo_do_desligamento`)?"""
    return motivo.startswith(_MOTIVO_DO_VOTO)


def reativar_desfaz(de: SkillState | None) -> bool:
    """O "reativar" (`disabled → published`, a única volta da tabela do D1) DESFAZ o desligamento de um item que
    estava `de`?

    Só quando ele estava publicado: aí a volta devolve o estado de antes. Do `candidate` ou do `validated`, a mesma
    chamada PROMOVERIA o que nunca foi publicado: pularia a repetição do D1 e, com efeito ou texto de pessoa, a fila
    "Para aprovar" (o fluxo e a receita com efeito esperam o dono ali). Nesses casos não há desfazer: publicar o que o
    voto desligou é uma decisão do dono, não a volta de um clique.
    """
    return de is _S.PUBLISHED


__all__ = ["DESLIGAVEIS", "REFUTACOES_PARA_DESLIGAR", "REF_NO_BACKLOG", "AcaoDoEfeito", "Conhecimento", "Desfecho",
           "Efeito", "ItemVotado", "PlanoDoVoto", "Uso", "Verificador", "conferir_voto", "desfecho",
           "efeitos_do_voto", "licao_refutada", "motivo_do_desligamento", "reativar_desfaz", "veio_do_voto"]
