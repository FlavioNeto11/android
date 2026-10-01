"""Retrieval de contexto de código: léxico + BM25 locais e um provedor semântico plugável (ADR-063).

Desligado por padrão (`context_retrieval.enabled: false`): mergear este código não muda comportamento nenhum.
Provedor semântico é detalhe de implementação (`domain/ports.py::SemanticProvider`); o envio de código a provedor
externo passa por UMA política (`domain/policy.py`) e o código privado não sai da máquina
(`PRIVATE_CODE_SEND_APPROVED = False`).
"""
from app.security.redaction import redact as _redact

from .domain.sensitive import registrar_redator as _registrar_redator

_registrar_redator(_redact)  # a política de segredo "mole" reaproveita a redação central (o domínio só vê a porta)
