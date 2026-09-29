"""Pacote A9 do ADR-054 (a preencher, opcional): preferências como sugestão pré-preenchida (respostas a
`needs_input` e escolhas no desambiguador). Nunca respondem sozinhas por ação com efeito; a porta da desambiguação
mora em `skills.application` e o livro a implementa (DAG `learning → skills`)."""
from __future__ import annotations
