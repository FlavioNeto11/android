---
paths:
  - "backend/tests/**"
  - "frontend/src/**/*.test.*"
---

# Testes

- **Durante o trabalho, rode só o arquivo ou o `-k` afetado.** Suíte inteira ~11 min (SQLite) / ~14 min
  (PostgreSQL) — guarde para antes do commit, em segundo plano (não fique ocioso esperando).
- **O harness usa `base_console_port: 5640`** (`backend/tests/conftest.py`) — confira antes de assumir isolamento
  dos emuladores reais (um `HOME` da suíte já derrubou um canário real — K-001).
- **Nunca enfraqueça nem apague um teste** para fazer a suíte passar.
- Teste multi-banco abre o banco pela fábrica configurada, nunca `Database(caminho)` direto.
- Teste de tempo real usa relógio **injetável**, não `time.sleep`/`datetime.now()` direto.
