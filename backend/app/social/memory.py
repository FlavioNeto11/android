"""Memória por perfil: o que o sistema aprendeu sobre as pessoas com quem conversa.

Três regras governam este módulo:

1. **Isolamento.** Toda operação exige `profile_id`. O índice de texto completo é um só para todos os perfis; quem
   separa é o filtro na junção, que vive dentro do repositório — não existe consulta sem perfil para chamar errado.
2. **Só fato confirmado vira memória.** Interação com falha, incerteza ou cancelamento não ensina nada: o que se
   guardaria seria uma suposição, e suposição errada envenena todas as conversas seguintes daquele perfil.
3. **Segredo nunca é memorizado.** Senha, token e código de verificação são recusados por FORMATO, antes de a
   lembrança existir. Um código que virasse memória voltaria ao modelo em toda conversa futura.
"""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..db import Database, Row, loads
from ..models import InteractionStatus, MemoryItemDTO
from ..security.redaction import looks_secret, mentions_credential
from ..util import now_iso
from .contas_nossas import hash_do_handle, parece_email, previa_do_rastro, sem_o_rastro
from .repository import SocialRepository

log = logging.getLogger("poc.social.memory")

# Aproximação de tokens por caractere. Não precisa ser exata: serve para não estourar o contexto do modelo, e erra
# para o lado seguro (superestima) em português.
CARACTERES_POR_TOKEN = 3.6

# Pesos da recuperação. Relevância manda, mas um fato importante e recente não some por não bater no texto.
PESO_RELEVANCIA = 0.45
PESO_IMPORTANCIA = 0.25
PESO_CONFIANCA = 0.15
PESO_RECENCIA = 0.15

_ESPACOS = re.compile(r"\s+")
_PONTUACAO_BORDA = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
_TOKEN = re.compile(r"[0-9A-Za-zÀ-ÿ_]{2,}", re.UNICODE)
_PARADAS = {"a", "as", "o", "os", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "um", "uma", "que",
            "para", "com", "por", "the", "and", "for", "you", "your", "are", "was", "with", "this", "that"}


class MemoryRefused(RuntimeError):
    """A lembrança foi recusada de propósito. Não é erro de execução: é a guarda funcionando."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


@dataclass(slots=True)
class Recall:
    items: list[MemoryItemDTO] = field(default_factory=list)
    dropped: int = 0                     # quantas ficaram de fora pelo teto de tokens
    estimated_tokens: int = 0


def _app_de(row: Any) -> str | None:
    """`app_id` da linha (migração 037) — `None` numa linha que não tem a coluna ou é fato geral."""
    try:
        return row["app_id"]
    except (KeyError, IndexError):
        return None


def estimate_tokens(text: str) -> int:
    return int(len(text) / CARACTERES_POR_TOKEN) + 1 if text else 0


def normalize_subject(subject: str) -> str:
    """`@Ana`, `ana` e `Ana ` são o mesmo assunto. Sem isto, o mesmo fato viraria três lembranças."""
    s = (subject or "").strip().lower()
    return f"@{s.lstrip('@')}" if s.startswith("@") else s


def _sem_acento(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def fingerprint(subject: str, content: str) -> str:
    """Assunto + conteúdo normalizados. É o que faz "Ana treina para a maratona." e "ana treina para a maratona"
    serem o MESMO fato, em vez de duas lembranças quase iguais competindo pelo espaço do contexto."""
    base = f"{normalize_subject(subject)}|{_PONTUACAO_BORDA.sub('', _ESPACOS.sub(' ', _sem_acento(content).lower()))}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:32]


def termos_de_busca(text: str, *, limite: int = 12) -> list[str]:
    """Extrai os termos de um texto livre — inclusive vindo da tela do app.

    Devolve TERMOS, não uma expressão de índice. Antes devolvia `"a" OR "b"`, que é sintaxe do FTS5, e isso viajava
    até o repositório: no PostgreSQL o `plainto_tsquery` lia aquilo como texto comum, juntava com E e incluía a
    palavra literal "or" — a busca não encontrava nada, sem erro. Montar a expressão é trabalho de quem conhece o
    índice, e quem conhece é o repositório.

    O filtro de `_TOKEN` é o que mantém texto hostil inofensivo: só sai daqui o que é palavra.
    """
    termos = [t for t in _TOKEN.findall(text or "") if t.lower() not in _PARADAS][:limite]
    return list(dict.fromkeys(termos))


class MemoryStore:
    def __init__(self, repo: SocialRepository):
        self.repo = repo

    # ------------------------------------------------------------------ escrita
    def remember(self, profile_id: str, *, subject: str, content: str, source: str = "interaction",
                 interaction_id: str | None = None, importance: float = 0.5, confidence: float = 0.5,
                 expires_at: str | None = None, app_id: str | None = None) -> MemoryItemDTO:
        subject = normalize_subject(subject)
        content = _ESPACOS.sub(" ", (content or "").strip())
        if not subject or not content:
            raise MemoryRefused("assunto e conteúdo são obrigatórios")
        # Memória é mais rigorosa que o redator: uma lembrança volta ao modelo em TODA conversa futura daquele
        # perfil, então basta a menção a credencial para recusar. Recusar um fato inofensivo não custa nada.
        junto = f"{subject} {content}"
        if looks_secret(junto) or mentions_credential(junto):
            raise MemoryRefused("o conteúdo fala de credencial, código ou token e não pode virar memória")
        fp = fingerprint(subject, content)
        existente = self.repo.memory_by_fingerprint(profile_id, fp)
        if existente is not None:
            self.repo.merge_memory(profile_id, existente["id"], importance=importance, confidence=confidence,
                                   interaction_id=interaction_id, expires_at=expires_at)
            return self._dto(self.repo.memory_row(profile_id, existente["id"]))
        memory_id = self.repo.insert_memory(
            profile_id, subject=subject, content=content, source=source, fingerprint=fp,
            interaction_id=interaction_id, importance=importance, confidence=confidence, expires_at=expires_at,
            app_id=app_id)
        return self._dto(self.repo.memory_row(profile_id, memory_id))

    def absorb_observation(self, profile_id: str, *, subject: str, content: str, prefix: str,
                           expires_at: str | None, app_id: str | None = None) -> MemoryItemDTO:
        """Grava uma tela vista SEM repetir o que já se sabe dela hoje (achado da simulação de 24/09).

        Etapas seguidas na mesma tela (abrir a conversa → escrever → enviar → conferir) viam quase a mesma coisa,
        cada vez com uma linha a mais: duas execuções deixavam 11 fatos quase iguais e os 8 lugares de memória do
        prompt enchiam de cópias. Agora, no mesmo dia e assunto: tela que não traz nada novo só conta "visto Nx";
        tela que CONTÉM fatos anteriores toma o lugar do primeiro deles (mesmo id) e os demais somem.
        """
        from .observacao import partes_do_fato  # noqa: PLC0415
        subject = normalize_subject(subject)
        content = _ESPACOS.sub(" ", (content or "").strip())
        junto = f"{subject} {content}"
        if not subject or not content:
            raise MemoryRefused("assunto e conteúdo são obrigatórios")
        if looks_secret(junto) or mentions_credential(junto):
            raise MemoryRefused("o conteúdo fala de credencial, código ou token e não pode virar memória")
        novas = partes_do_fato(content)
        de_hoje = [r for r in self.repo.list_memories(profile_id, subject=subject, limit=200)
                   if r["source"] == "observation" and r["content"].startswith(prefix)
                   and _app_de(r) == app_id]                  # a mesma tela em outro app é outro fato
        for r in de_hoje:
            if novas <= partes_do_fato(r["content"]):
                self.repo.merge_memory(profile_id, r["id"], importance=0.3, confidence=0.7, interaction_id=None,
                                       expires_at=expires_at)
                return self._dto(self.repo.memory_row(profile_id, r["id"]))
        contidos = [r for r in de_hoje if partes_do_fato(r["content"]) < novas]
        if contidos:
            alvo, *resto = contidos
            self.repo.replace_memory_content(profile_id, alvo["id"], content=content,
                                             fingerprint=fingerprint(subject, content), expires_at=expires_at)
            for r in resto:
                self.repo.delete_memory(profile_id, r["id"])
            return self._dto(self.repo.memory_row(profile_id, alvo["id"]))
        return self.remember(profile_id, subject=subject, content=content, source="observation", importance=0.3,
                             confidence=0.7, expires_at=expires_at, app_id=app_id)

    def learn_from(self, profile_id: str, interaction_id: str) -> list[MemoryItemDTO]:
        """Interação CONFIRMADA → candidatos → dedup/merge → memória. Qualquer outro estado não ensina nada."""
        row = self.repo.interaction_row(profile_id, interaction_id)
        if row is None or row["status"] != InteractionStatus.confirmed.value:
            return []
        candidatos = loads(row["metadata"], {}).get("memory_candidates") or []
        aprendidos: list[MemoryItemDTO] = []
        for c in candidatos:
            if not isinstance(c, dict):
                continue
            try:
                aprendidos.append(self.remember(
                    profile_id, subject=str(c.get("subject") or row["counterparty"] or "geral"),
                    content=str(c.get("content") or ""), source="interaction", interaction_id=interaction_id,
                    importance=float(c.get("importance", 0.5)), confidence=float(c.get("confidence", 0.5)),
                    app_id=_app_de(row)))
            except (MemoryRefused, ValueError, TypeError) as exc:
                log.info("candidato a memória recusado (%s): %s", profile_id, exc)
        return aprendidos

    def forget(self, profile_id: str, memory_id: str) -> bool:
        return self.repo.delete_memory(profile_id, memory_id)

    def purge_expired(self, profile_id: str) -> int:
        return self.repo.purge_expired_memories(profile_id)

    # ------------------------------------------------------------------ leitura
    def list(self, profile_id: str, *, subject: str | None = None, limit: int = 100,
             include_expired: bool = False, app_id: str | None = None) -> list[MemoryItemDTO]:
        subject = normalize_subject(subject) if subject else None
        return [self._dto(r) for r in self.repo.list_memories(profile_id, subject=subject, limit=limit,
                                                              include_expired=include_expired, app_id=app_id)]

    def recall(self, profile_id: str, *, query: str = "", subject: str | None = None, limit: int = 8,
               token_budget: int = 400, touch: bool = True) -> Recall:
        """Recupera por relevância, importância, confiança e recência, respeitando um TETO de tokens.

        O teto existe porque memória cresce sem limite e contexto não: sem ele, um perfil com meses de conversa
        empurraria o resto do prompt para fora. O que não couber fica registrado em `dropped`.
        """
        agora = now_iso()
        candidatos: dict[str, Any] = {}
        ranks: dict[str, float] = {}
        termos = termos_de_busca(query)
        if termos:
            achados = self.repo.search_memories(profile_id, termos, limit=max(limit * 6, 30), now=agora)
            for r in achados:
                candidatos[r["id"]] = r
                # `relevancia` já vem normalizada em 0..1, com 1 = mais relevante. Antes a normalização era AQUI,
                # dividindo pelo `min()` das notas — certo para o `bm25()` do SQLite (negativo, menor é melhor) e
                # errado para o `ts_rank` do PostgreSQL (positivo, maior é melhor), onde achatava tudo em 1.0 e
                # apagava a ordenação. Quem sabe qual banco respondeu é o repositório; a conta mora lá.
                ranks[r["id"]] = r["relevancia"]
        if subject:
            for r in self.repo.list_memories(profile_id, subject=normalize_subject(subject),
                                             limit=max(limit * 3, 20), now=agora):
                candidatos.setdefault(r["id"], r)
        for r in self.repo.list_memories(profile_id, limit=max(limit * 3, 20), now=agora):
            candidatos.setdefault(r["id"], r)

        pontuados = sorted(candidatos.values(),
                           key=lambda r: (-self._score(r, ranks.get(r["id"], 0.0), agora), r["id"]))
        escolhidos: list[MemoryItemDTO] = []
        gastos = 0
        descartados = 0
        for r in pontuados:
            if len(escolhidos) >= limit:
                descartados += 1
                continue
            custo = estimate_tokens(f"{r['subject']}: {r['content']}")
            if gastos + custo > token_budget:
                descartados += 1
                continue
            gastos += custo
            escolhidos.append(self._dto(r))
        if touch and escolhidos:
            self.repo.touch_memories(profile_id, [m.id for m in escolhidos])
        return Recall(items=escolhidos, dropped=descartados, estimated_tokens=gastos)

    # ------------------------------------------------------------------ interno
    def _score(self, row: Any, relevancia: float, agora: str) -> float:
        recencia = 1.0 if (row["updated_at"] or "") >= agora[:10] else 0.5 if (row["updated_at"] or "")[:7] == agora[:7] else 0.2
        repeticao = min(0.1, 0.02 * (int(row["occurrences"] or 1) - 1))
        return (PESO_RELEVANCIA * relevancia + PESO_IMPORTANCIA * float(row["importance"] or 0.0)
                + PESO_CONFIANCA * float(row["confidence"] or 0.0) + PESO_RECENCIA * recencia + repeticao)

    @staticmethod
    def _dto(row: Any) -> MemoryItemDTO:
        return MemoryItemDTO(
            id=row["id"], profile_id=row["profile_id"], subject=row["subject"], content=row["content"],
            source=row["source"], interaction_id=row["interaction_id"], importance=row["importance"],
            confidence=row["confidence"], occurrences=row["occurrences"], expires_at=row["expires_at"],
            created_at=row["created_at"], updated_at=row["updated_at"], last_used_at=row["last_used_at"],
            app_id=_app_de(row))


# ------------------------------------------------------------------ retirada da conta (29.23 e 29.32, ADR-068)
_COLUNAS_DA_MEMORIA = "seq, profile_id, subject, content, fingerprint"


def _regravar(db: Database, m: Row, assunto: str | None, conteudo: str | None) -> None:
    """Grava o texto novo da lembrança. O `fingerprint` (unique por perfil) é recalculado; se colidir com outra
    lembrança DO MESMO perfil, a linha guarda um fingerprint próprio em vez de ser apagada ou mesclada."""
    fp = fingerprint(str(assunto), str(conteudo))
    if db.one("SELECT 1 FROM memory_items WHERE profile_id=? AND fingerprint=? AND seq<>?",
              (m["profile_id"], fp, m["seq"])) is not None:
        fp = f"{fp[:24]}r{m['seq']}"
    db.execute("UPDATE memory_items SET subject=?, content=?, fingerprint=? WHERE seq=?",
               (assunto, conteudo, fp, m["seq"]))


def reescrever_memoria(db: Database, *, profile_id: str, handle: str | None, account_id: str | None,
                       emails: Sequence[str] = ()) -> dict[str, int]:
    """`memory_items` de TODAS as personas: `subject` e `content` passam a dizer `[conta removida]` onde diziam o @
    da conta que sai (com e sem `@`, sem diferenciar caixa), o id dela ou os e-mails que a identificam (29.32: outra
    persona também pode ter lembrado do @ que nós retiramos; `emails` já vem filtrado de quem outra conta VIVA usa,
    ver `contas_nossas.emails_so_desta_conta`). A linha FICA: a persona sobrevive e lembra do que viveu.

    `profile_id` é a persona que retira: só separa, na contagem, as lembranças das OUTRAS. Roda na transação de quem
    chama (sem commit nem rollback) e é idempotente: texto já reescrito não casa mais. Devolve só contagens."""

    previas = previa_do_rastro(handle, account_id, emails)
    contagem = {"memory_items": 0, "memory_items_de_outras_personas": 0}
    if not previas:
        return contagem
    casa = " OR ".join(f"lower({c}) LIKE ?" for c in ("subject", "content") for _ in previas)
    args = tuple(f"%{p}%" for _ in ("subject", "content") for p in previas)
    for m in db.query(f"SELECT {_COLUNAS_DA_MEMORIA} FROM memory_items WHERE {casa}", args):
        assunto = sem_o_rastro(m["subject"], handle, account_id, emails)
        conteudo = sem_o_rastro(m["content"], handle, account_id, emails)
        if assunto == m["subject"] and conteudo == m["content"]:
            continue
        _regravar(db, m, assunto, conteudo)
        contagem["memory_items"] += 1
        if m["profile_id"] != profile_id:
            contagem["memory_items_de_outras_personas"] += 1
    return contagem


# ------------------------------------------------------------------ retroativo das retiradas antes do 29.32
_TOKEN = re.compile(r"@?[\w.]+")


def _contas_vivas_hash(db: Database) -> set[str]:
    """Os hashes dos @ que HOJE são de conta viva (conta ou cadastro de persona): uma lápide cujo @ voltou a existir
    não é rastro, é a conta nova."""
    vivos = [r["handle"] for r in db.query("SELECT handle FROM profile_accounts")]
    vivos += [r["username"] for r in db.query("SELECT username FROM instagram_profiles")]
    return {h for h in (hash_do_handle(v) for v in vivos) if h}


def _sem_os_rastros(texto: str | None, handles: Sequence[str], ids: Sequence[str],
                    emails: Sequence[str]) -> str | None:
    for handle in handles:
        texto = sem_o_rastro(texto, handle, None)
    for conta in ids:
        texto = sem_o_rastro(texto, None, conta)
    return sem_o_rastro(texto, None, None, emails)


def reescrever_memoria_das_retiradas(db: Database, *, extras: Sequence[str] = (),
                                     escrever: bool = True) -> dict[str, int]:
    """Passe ÚNICO e idempotente para as contas retiradas ANTES de a retirada reescrever a memória de todas as
    personas (29.32). A fonte confiável são as lápides (`contas_retiradas`, só o HASH do @) e os ids de conta dos
    eventos `profile.account_retired`: o @ é achado no texto da memória por hash (cada palavra do texto é comparada
    com a lápide; o texto da conta nunca é lido de lugar nenhum) e trocado pelo mesmo `sem_o_rastro` da retirada.

    O e-mail da conta retirada NÃO está em lugar nenhum do banco (a conta e a credencial já saíram): só entra pela
    lista `extras` do operador (e-mails ou @, um por item; o que for e-mail de conta VIVA é recusado e contado).

    `escrever=False` é o ensaio: conta o que mudaria sem gravar. Roda na transação de quem chama e devolve SÓ
    contagens (nenhum texto de conta, nem no retorno nem em log)."""

    lapides = {str(r["handle_sha256"]) for r in db.query("SELECT handle_sha256 FROM contas_retiradas")}
    vivas = _contas_vivas_hash(db)
    lapides -= vivas
    ids: list[str] = []
    for ev in db.query("SELECT data FROM events WHERE kind='profile.account_retired'"):
        dado = loads(ev["data"], {})
        if isinstance(dado, dict) and isinstance(dado.get("account_id"), str) and dado["account_id"]:
            ids.append(dado["account_id"])
    ids = list(dict.fromkeys(ids))
    emails_vivos = {str(r["h"]).lower() for r in db.query(
        "SELECT handle AS h FROM profile_accounts UNION SELECT login_identifier FROM account_credentials"
        " UNION SELECT login_identifier FROM instagram_credentials")}
    emails: list[str] = []
    arrobas: list[str] = []
    recusados = 0
    for bruto in extras:
        item = str(bruto or "").strip()
        if not item:
            continue
        if parece_email(item):
            if item.lower() in emails_vivos:
                recusados += 1
            elif item.lower() not in emails:
                emails.append(item.lower())
        elif hash_do_handle(item) not in vivas:
            arrobas.append(item)
        else:
            recusados += 1
    contagem = {"lapides": len(lapides), "ids_de_conta": len(ids), "extras_aceitos": len(emails) + len(arrobas),
                "extras_recusados_por_estarem_vivos": recusados, "memory_items": 0}
    for m in db.query(f"SELECT {_COLUNAS_DA_MEMORIA} FROM memory_items"):
        achados = list(arrobas)
        for campo in (m["subject"], m["content"]):
            for pedaco in _TOKEN.finditer(campo or ""):
                palavra = pedaco.group(0).lstrip("@").rstrip(".")
                if palavra and palavra not in achados and hash_do_handle(palavra) in lapides:
                    achados.append(palavra)

        assunto = _sem_os_rastros(m["subject"], achados, ids, emails)
        conteudo = _sem_os_rastros(m["content"], achados, ids, emails)
        if assunto == m["subject"] and conteudo == m["content"]:
            continue
        if escrever:
            _regravar(db, m, assunto, conteudo)
        contagem["memory_items"] += 1
    return contagem
