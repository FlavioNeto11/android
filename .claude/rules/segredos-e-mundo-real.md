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
- **A automação digita a credencial que a pessoa guardou** (ADR-040, que substitui em parte o ADR-025): só pela
  credencial da conta da persona, com consentimento por conta, e só por `type_secret`, no app e no site daquela
  conta. A execução não carrega credencial. Nunca credencial dentro do texto do comando, lida da tela ou inventada.
- **Desafio, 2FA com código não fornecido e CAPTCHA são resolvidos pela pessoa**; nada de evasão de antibot.
- **Script `[P]` toca o parque/produção de verdade** (reiniciar aparelho, relógio da máquina, migrar banco real,
  chamada paga de IA) — pedir **autorização explícita antes** de rodar.
- **Nunca ler `.env`**; confirme chave configurada por `GET /api/ai`/`/api/health`, não pelo conteúdo do arquivo.
- `config/config.yaml` e `.env` ficam **fora do Git**, por instalação (ADR-011).
- Redação de log é por **formato** (`Authorization: Bearer …`, `senha=…`), nunca por lista de valores conhecidos.
