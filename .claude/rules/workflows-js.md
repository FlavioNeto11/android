---
paths:
  - ".claude/workflows/**"
---

# Workflows (`.js`)

- **Nenhuma literal com quebra de linha real dentro.** O diálogo de aprovação do Workflow recusa o script com
  "control characters that would be hidden in the approval dialog" se encontrar uma. Texto de várias linhas se
  monta com `[...].join(NL)` (como `REGRAS` em `.claude/workflows/plano-100.js`), nunca com crase multilinha nem
  `\n` escrito à mão por um script externo — o shell come as contrabarras, e `\n` escrito assim vira quebra de
  linha de verdade sem avisar (ver K-007 em `docs/conhecimento/aprendizados.md`).
- **Arquivo em LF**, garantido por `.gitattributes` (`.claude/workflows/*.js text eol=lf`). Editar fora do Git ou
  copiar de um ambiente Windows pode reintroduzir CRLF — confira antes de commitar.
- **O enum de status/proof do resultado espelha `scripts/claude-plan-100.py`.** Status só em
  `{implemented, partial, blocked}` (`ESTADOS`); prova só em `{real, simulated, not_run}` (`PROVAS`). Um valor fora
  disso é recusado por `validar()` no livro-razão — o workflow não precisa reimplementar a validação, mas o
  vocabulário que ele pede ao agente tem que ser exatamente este, ou a rodada inteira é perdida na hora de aplicar.
  Os sete registros com `tests`/`unit` de 24/09 foram escritos à mão, sem passar por `validar()`: são divergência
  registrada (backlog B6 em `docs/estado-atual.md`), não precedente — resultado novo usa só este vocabulário.
- Um agente pode devolver menos itens do que os solicitados (recusa ou perda de escopo) — o workflow e o
  livro-razão têm que tratar isso como erro explícito, nunca como "sucesso parcial silencioso" (ver K-008).
