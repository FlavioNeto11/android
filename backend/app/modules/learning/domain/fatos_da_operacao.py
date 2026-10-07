"""31.190: o fato confirmado da pesquisa de uma operação vira candidata do ESCRITOR no Livro. Puro: sem banco.

A memória da operação (31.157/31.158, migração 125) já decide a confiança por código: dois domínios na pesquisa, ou a
leitura do alvo (31.179), dão `confirmado`; o vencido sai do bloco pelo frescor. O que faltava era o fato sobreviver à
operação: ele morria com ela, e a próxima operação sobre o mesmo assunto pagava a pesquisa de novo.

Decisões (sem decisão do dono pendente; reversível):
* nasce `candidate`, papel `writer`, escopo do app e do ASSUNTO da operação (31.200, `scope_subject`): publicada, ela
  só vai ao texto que pede o mesmo assunto (`licoes.nivel`). O fato cujo assunto não sobra depois de canônico (ou tem
  identificador) não nasce: sem assunto, a lição iria a todo texto do app, sobre qualquer post. E só uma pessoa a
  publica: a origem está em `FONTES_HUMANAS` (trava do D1, `requires_owner`), e a esteira das lições não valida texto
  de origem humana;
* o fato com identificador de pessoa (um @, um e-mail) não vai: a pesquisa é de assunto, não de gente, e o Livro mostra
  o texto; o assunto com identificador sai da proveniência;
* só o fato de pesquisa (`pesquisa.*`, `descoberta`, `confirmado`, dentro do frescor) de operação ENCERRADA. Ficam fora:
  a leitura do alvo (`alvo.conteudo`, é daquele post e vale 6 h), as fontes (`fonte.*`, URLs) e o estado da pesquisa;
* o conteúdo é só o texto do fato, para o MESMO fato em outra operação cair no mesmo item (`content_hash`); assunto,
  domínios das fontes, frescor e uso no texto vão à proveniência, que o Livro mostra.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.licoes import LICAO_MAX_CARACTERES
from app.modules.learning.domain.livro import Escopo, NovoItem, assunto_canonico
from app.modules.learning.domain.tokens import estimar_tokens
from app.modules.learning.domain.vocabulario import LivroKind, Papel, SourceKind
from app.modules.skills.domain.document import JsonObject, JsonValue

#: O prefixo da chave do fato de pesquisa na memória da operação (`pesquisa_da_operacao._gravar`).
PREFIXO_DO_FATO = "pesquisa."
#: A chave do estado da pesquisa (progresso), que também começa com o prefixo.
CHAVE_DO_ESTADO = "pesquisa.estado"
VERSAO_DA_REGRA = "31.190-v1"
#: Um identificador de pessoa no texto: @handle ou e-mail.
_IDENTIFICADOR = re.compile(r"(?<![\w.])@[\w.]{2,}|[\w.+-]+@[\w-]+\.[\w.-]+")


@dataclass(frozen=True, slots=True)
class FatoDaOperacao:
    operacao_id: str
    chave: str
    tipo: str
    texto: str
    confianca: str
    frescor_ate: str | None
    pacote: str
    assunto: str = ""
    dominios: tuple[str, ...] = ()
    execucoes: tuple[str, ...] = ()
    usado_em: int = 0                    # alvos que receberam o fato no texto (`conhecimento_ids`)


def elegivel(f: FatoDaOperacao, agora: str) -> bool:
    return (f.chave.startswith(PREFIXO_DO_FATO) and f.chave != CHAVE_DO_ESTADO and f.tipo == "descoberta"
            and f.confianca == "confirmado" and bool(f.pacote) and bool(f.texto.strip())
            and (f.frescor_ate is None or f.frescor_ate > agora))


class MotivoDaRecusa(StrEnum):
    """Por que um fato da memória da operação não vai ao Livro (o relatório da curadoria por operação conta por ele)."""

    NAO_E_FATO = "nao_e_fato"                  # fonte, estado da pesquisa, leitura do alvo, texto vazio
    HIPOTESE = "hipotese"
    VENCIDO = "vencido"
    SEM_APP = "sem_app"
    LONGO = "longo"                            # maior que a lição cabe no prompt
    IDENTIFICADOR = "identificador"            # um @ ou um e-mail no texto
    SEM_ASSUNTO = "sem_assunto"                # 31.200: sem assunto, a lição iria a todo texto do app


def recusa(f: FatoDaOperacao, agora: str) -> MotivoDaRecusa | None:
    """O primeiro motivo por que o fato não vira candidata, ou None quando vira. A mesma régua da `candidata`."""
    texto = " ".join(f.texto.split())
    if not (f.chave.startswith(PREFIXO_DO_FATO) and f.chave != CHAVE_DO_ESTADO and f.tipo == "descoberta" and texto):
        return MotivoDaRecusa.NAO_E_FATO
    if f.confianca != "confirmado":
        return MotivoDaRecusa.HIPOTESE
    if f.frescor_ate is not None and f.frescor_ate <= agora:
        return MotivoDaRecusa.VENCIDO
    if not f.pacote:
        return MotivoDaRecusa.SEM_APP
    if len(texto) > LICAO_MAX_CARACTERES:
        return MotivoDaRecusa.LONGO
    if _IDENTIFICADOR.search(texto):
        return MotivoDaRecusa.IDENTIFICADOR
    if not assunto_canonico("" if _IDENTIFICADOR.search(f.assunto) else f.assunto[:200]):
        return MotivoDaRecusa.SEM_ASSUNTO
    return None


def vencida(provenance: JsonObject, agora: str) -> bool:
    """O item do Livro que nasceu de um fato cujo frescor passou (a curadoria por operação o relata; não o muda)."""
    frescor = provenance.get("frescor_ate")
    return isinstance(frescor, str) and bool(frescor) and frescor <= agora


def candidata(f: FatoDaOperacao, agora: str) -> NovoItem | None:
    """A candidata do escritor, ou None quando o fato não serve (`recusa` diz o motivo)."""
    if recusa(f, agora) is not None:
        return None
    texto = " ".join(f.texto.split())
    assunto = "" if _IDENTIFICADOR.search(f.assunto) else f.assunto[:200]
    escopo_do_assunto = assunto_canonico(assunto)
    execucoes = list[JsonValue](f.execucoes[:20])
    dominios = list[JsonValue](f.dominios[:10])
    return NovoItem(kind=LivroKind.LICAO, escopo=Escopo(app=f.pacote, role=Papel.WRITER.value, subject=escopo_do_assunto),
                    content={"modelo": "fato_da_operacao", "fato": texto}, summary=texto,
                    source_kind=SourceKind.FATO_DA_OPERACAO, side_effect=False,
                    provenance={"regra": VERSAO_DA_REGRA, "operacao": f.operacao_id, "chave": f.chave,
                                "assunto": assunto, "fontes": dominios, "frescor_ate": f.frescor_ate,
                                "usado_em": f.usado_em, "execucoes": execucoes},
                    tokens=estimar_tokens(texto))


def proveniencia_do_fato(content: JsonObject, provenance: JsonObject) -> JsonObject | None:
    """Adendo v1.117: a proveniência que o detalhe do Livro mostra, só do item `fato_da_operacao` e com chaves FECHADAS
    (a regra e a chave da memória ficam de fora). `confianca` é sempre "confirmado": só o fato confirmado nasce no Livro
    (`elegivel`). `None` em qualquer outra lição."""
    if content.get("modelo") != "fato_da_operacao":
        return None

    def lista(chave: str) -> list[JsonValue]:
        valor = provenance.get(chave)
        return [str(v) for v in valor if v is not None] if isinstance(valor, list) else []

    frescor = provenance.get("frescor_ate")
    usado = provenance.get("usado_em")
    return {"operacao": str(provenance.get("operacao") or ""), "assunto": str(provenance.get("assunto") or ""),
            "fontes": lista("fontes"), "frescor_ate": str(frescor) if frescor else None,
            "usado_em": usado if isinstance(usado, int) and not isinstance(usado, bool) else 0,
            "execucoes": lista("execucoes"), "confianca": "confirmado"}


def candidatas(fatos: Sequence[FatoDaOperacao], agora: str) -> list[NovoItem]:
    return [c for f in fatos if (c := candidata(f, agora)) is not None]


__all__ = ["CHAVE_DO_ESTADO", "FatoDaOperacao", "PREFIXO_DO_FATO", "VERSAO_DA_REGRA", "candidata", "candidatas",
           "elegivel"]
