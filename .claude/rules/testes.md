---
paths:
  - "backend/tests/**"
  - "frontend/src/**/*.test.*"
---

# Testes

- **Durante o trabalho, rode só o arquivo ou o `-k` afetado.** Suíte inteira ~36 min em série / ~5 a 6 min com `-n 8` (SQLite) / ~14 min
  (PostgreSQL) — guarde para antes do commit, em segundo plano (não fique ocioso esperando).
- **Suíte inteira em paralelo: `pytest -q -n 8`** (pytest-xdist; ~5:06 contra ~36 min (2178 s) em série, mesmo resultado; `docs/operacao.md` §4). **Uma suíte completa por vez na máquina, entre todas as sessões**: confira se já há `python -m pytest` rodando antes de disparar.
- **Prioridade Idle na suíte** (o parque divide a máquina; carga alta já derrubou convidado com conta real, ADR-053): no PowerShell,
  `(Get-Process -Id $PID).PriorityClass='Idle'; & .venv\Scripts\python.exe -m pytest -q -n 8` — os workers do xdist herdam a classe.
  `cmd /c start /low` deu "Access is denied" no shell de uma sessão e funcionou em outra (medido em 02/10/2026): use a forma do PowerShell, que vale em todas.
- **O harness usa `base_console_port: 5640`** (`backend/tests/conftest.py`) — confira antes de assumir isolamento
  dos emuladores reais (um `HOME` da suíte já derrubou um canário real — K-001).
- **PostgreSQL na suíte = PG dirigido** (29.53, `docs/banco.md`, "O portão de PostgreSQL de uma suíte"): só os
  arquivos que o lote toca, `-n 8`, Idle, depois de o farm-pg aceitar conexão. A suíte inteira em PG roda só em janela
  sem aparelho com conta subindo, e nunca colada num deploy.
- **Nunca enfraqueça nem apague um teste** para fazer a suíte passar.
- Teste multi-banco abre o banco pela fábrica configurada, nunca `Database(caminho)` direto.
- Teste de tempo real usa relógio **injetável**, não `time.sleep`/`datetime.now()` direto.
