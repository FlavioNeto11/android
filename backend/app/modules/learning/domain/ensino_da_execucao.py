"""31.221 (P-014): o ensino a partir de uma execução que deu certo. Puro: sem banco.

Medido em 07/10 (onda 1, lido em `mode=ro`): a operação planeja com ações do CATÁLOGO (`OPEN_PROFILE`, `OPEN_POST`,
`OPEN_COMMENTS`, `CREATE_COMMENT`). O 31.153 (etapas ensinadas) exclui de propósito a etapa com ação do catálogo, então
"virar etapa ensinada" nunca trocaria nada no plano. O que reaproveita a etapa do catálogo é a RECEITA pela identidade
da etapa (`template_hash`): `open_post` e `open_comments` já rodaram sem IA. A receita que a IA aprende numa execução
real nasce candidata e só vira ativa depois de `ai.recipes_promote_after` execuções que concordem (hoje 2).

Aqui a pessoa olha a execução que deu certo e promove, num gesto, as candidatas que nasceram das etapas de leitura dela
(candidate → validated → published, o caminho que o Livro já dá à pessoa). Sem tempo de aparelho e sem migração: a
receita não tem escopo por persona, então a persona que executou vai como proveniência no motivo, não como trava.

Cada etapa sai com a candidata dela ou com o motivo FECHADO de não haver o que ensinar (`Motivo`). O motivo
`caminho_nao_reproduzivel` diz quais ferramentas da IA impediram a receita (só o nome da ferramenta, nunca o argumento):
na onda 1, o `open_profile` começou por voltar (`press_back`), que não se reproduz, e não nasceu receita.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: As ferramentas que a receita não reproduz (`taskqueue.recipes.UNSAFE_TO_REPLAY`; o teste confere a igualdade). Cópia
#: porque o módulo do aprendizado não importa o executor.
NAO_REPRODUZIVEIS = frozenset({"press_back", "press_home", "drag", "type_secret", "open_url"})

#: O prefixo do motivo gravado na trilha de cada receita promovida.
PREFIXO_DO_MOTIVO = "ensino_da_execucao:"


class Motivo(StrEnum):
    """Por que a etapa não tem o que ensinar. Vocabulário fechado: a tela traduz, o teste confere."""
    EXECUCAO_SIMULADA = "execucao_simulada"          # a simulada não publica (RA-19 B)
    NAO_CONCLUIDA = "nao_concluida"                  # a etapa não terminou comprovada
    COM_EFEITO = "com_efeito"                        # efeito externo ou trava de commit: segue pela aprovação
    JA_POR_RECEITA = "ja_por_receita"                # quem conduziu já foi uma receita
    SEM_ATOR = "sem_ator"                            # nenhuma ação: não há caminho
    CAMINHO_NAO_REPRODUZIVEL = "caminho_nao_reproduzivel"
    SEM_RECEITA = "sem_receita"                      # a IA conduziu e nenhuma receita nasceu (outro motivo da loja)
    RECEITA_JA_VALE = "receita_ja_vale"              # a que nasceu dela já está ativa ou validada
    RECEITA_FORA = "receita_fora_de_circulacao"      # substituída ou em quarentena


@dataclass(frozen=True, slots=True)
class EtapaDaExecucao:
    step_id: str
    key: str
    capability: str
    status: str
    driven_by: str
    side_effect: bool
    trava: bool                       # `commit_guard` não vazio (o conteúdo nunca sai daqui)
    profile_id: str
    ferramentas: tuple[str, ...]      # as ferramentas da tentativa que deu certo, na ordem


@dataclass(frozen=True, slots=True)
class ReceitaDaEtapa:
    id: int
    status: str
    replay_ok: int


def motivo(etapa: EtapaDaExecucao, receita: ReceitaDaEtapa | None, *, simulada: bool) -> Motivo | None:
    """`None` = a etapa tem uma candidata que a pessoa pode promover; senão, o primeiro motivo, nesta ordem."""
    if simulada:
        return Motivo.EXECUCAO_SIMULADA
    if etapa.side_effect or etapa.trava:          # antes do status: a etapa com efeito nunca se ensina por aqui
        return Motivo.COM_EFEITO
    if etapa.status != "succeeded":
        return Motivo.NAO_CONCLUIDA
    if etapa.driven_by == "recipe":
        return Motivo.JA_POR_RECEITA
    if etapa.driven_by == "sem_ator":
        return Motivo.SEM_ATOR
    if receita is None:
        return Motivo.CAMINHO_NAO_REPRODUZIVEL if bloqueadoras(etapa) else Motivo.SEM_RECEITA
    if receita.status == "candidate":
        return None
    if receita.status in ("active", "validated"):
        return Motivo.RECEITA_JA_VALE
    return Motivo.RECEITA_FORA


def bloqueadoras(etapa: EtapaDaExecucao) -> list[str]:
    """As ferramentas da tentativa que a receita não reproduz, sem repetir, na ordem."""
    return [f for f in dict.fromkeys(etapa.ferramentas) if f in NAO_REPRODUZIVEIS]


def motivo_da_promocao(run_id: str, profile_id: str) -> str:
    """O motivo da trilha: a execução e a persona (o id, nunca o nome) que a percorreu."""
    return f"{PREFIXO_DO_MOTIVO}{run_id} persona:{profile_id or '-'}"


__all__ = ["NAO_REPRODUZIVEIS", "PREFIXO_DO_MOTIVO", "EtapaDaExecucao", "Motivo", "ReceitaDaEtapa", "bloqueadoras",
           "motivo", "motivo_da_promocao"]
