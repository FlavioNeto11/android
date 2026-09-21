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
from dataclasses import dataclass, field
from typing import Any

from ..db import loads
from ..models import InteractionStatus, MemoryItemDTO
from ..security.redaction import looks_secret, mentions_credential
from ..util import now_iso
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
                 expires_at: str | None = None) -> MemoryItemDTO:
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
            interaction_id=interaction_id, importance=importance, confidence=confidence, expires_at=expires_at)
        return self._dto(self.repo.memory_row(profile_id, memory_id))

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
                    importance=float(c.get("importance", 0.5)), confidence=float(c.get("confidence", 0.5))))
            except (MemoryRefused, ValueError, TypeError) as exc:
                log.info("candidato a memória recusado (%s): %s", profile_id, exc)
        return aprendidos

    def forget(self, profile_id: str, memory_id: str) -> bool:
        return self.repo.delete_memory(profile_id, memory_id)

    def purge_expired(self, profile_id: str) -> int:
        return self.repo.purge_expired_memories(profile_id)

    # ------------------------------------------------------------------ leitura
    def list(self, profile_id: str, *, subject: str | None = None, limit: int = 100,
             include_expired: bool = False) -> list[MemoryItemDTO]:
        subject = normalize_subject(subject) if subject else None
        return [self._dto(r) for r in self.repo.list_memories(profile_id, subject=subject, limit=limit,
                                                              include_expired=include_expired)]

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
            created_at=row["created_at"], updated_at=row["updated_at"], last_used_at=row["last_used_at"])
