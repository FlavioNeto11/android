"""A regra "conta vinculada é conta real logada" (ADR-055) num lugar só.

Um aparelho com vínculo ATIVO de persona (`device_profile_bindings.active = 1`) é tratado como aparelho de conta real
até prova em contrário. Quem usa a regra:
- o reparo em escada e a validação de entrega no agendador (`taskqueue/scheduler.py`);
- os candidatos da validação do aprendizado (`modules/learning/infrastructure/ligar_validacao.py`);
- o diagnóstico da árvore do 31.52, que recusa gravar a tela de um aparelho assim (`taskqueue/executor.py`).

Antes, cada um tinha a sua cópia do SQL. O `real_account` da rede (`devices/rede.py`) lê os vínculos pelo repositório
social porque precisa dos nomes; a regra é a mesma (`SocialRepository.profiles_of_instance` sem app).

Fica no kernel (`app.shared`) porque fila, aprendizado e executor precisam dela, e o kernel não enxerga ninguém: recebe
só quem consulta o banco.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol


class _Consulta(Protocol):
    """O pedaço do `Database` que a regra usa."""

    def one(self, sql: str, params: tuple[object, ...] = ...) -> Mapping[str, object] | None: ...

    def query(self, sql: str, params: tuple[object, ...] = ...) -> Sequence[Mapping[str, object]]: ...


def tem_vinculo_ativo(db: _Consulta, instance_id: str) -> bool:
    """O aparelho tem vínculo ativo de persona, isto é, conta real logada até prova em contrário."""
    return db.one("SELECT 1 FROM device_profile_bindings WHERE instance_id=? AND active=1 LIMIT 1",
                  (instance_id,)) is not None


def aparelhos_com_vinculo_ativo(db: _Consulta) -> set[str]:
    """Todos os aparelhos com vínculo ativo de persona, numa consulta só."""
    return {str(r["instance_id"]) for r in
            db.query("SELECT DISTINCT instance_id FROM device_profile_bindings WHERE active=1", ())}


__all__ = ["aparelhos_com_vinculo_ativo", "tem_vinculo_ativo"]
