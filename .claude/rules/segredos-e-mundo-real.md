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
- **Script `[P]` toca o parque ou o ambiente central** (reiniciar aparelho, migrar o banco do ambiente central).
  O ambiente central é de desenvolvimento e validação do dono, ainda não é produção (`CLAUDE.md`): validar ali é
  permitido. **Autorização explícita antes** só para chamada paga de IA além do pontual, ação com efeito externo
  em conta real de terceiros, reset de aparelho com conta real logada, relógio, túnel e WSL.
- **Nunca ler `.env`**; confirme chave configurada por `GET /api/ai`/`/api/health`, não pelo conteúdo do arquivo.
- `config/config.yaml` e `.env` ficam **fora do Git**, por instalação (ADR-011).
- Redação de log é por **formato** (`Authorization: Bearer …`, `senha=…`), nunca por lista de valores conhecidos.
