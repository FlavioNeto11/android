---
paths:
  - "backend/app/security/**"
  - "backend/app/integrations/**"
  - "scripts/**"
  - "config/**"
---

# Segredos e mundo real

- **Senha nunca em log, prompt, evidência, captura, resposta, memória, fixture ou Git.** O canal sensível
  (`SensitiveInputChannel`) vai do cofre direto ao driver, fora do caminho normal de digitação (ADR-009).
- **Desafio, 2FA e CAPTCHA são sempre resolvidos pela pessoa**, nunca automatizados, mesmo sob pedido direto.
- **Script `[P]` toca o parque/produção de verdade** (reiniciar aparelho, relógio da máquina, migrar banco real,
  chamada paga de IA) — pedir **autorização explícita antes** de rodar.
- **Nunca ler `.env`**; confirme chave configurada por `GET /api/ai`/`/api/health`, não pelo conteúdo do arquivo.
- `config/config.yaml` e `.env` ficam **fora do Git**, por instalação (ADR-011).
- Redação de log é por **formato** (`Authorization: Bearer …`, `senha=…`), nunca por lista de valores conhecidos.
