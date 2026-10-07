# Controles T2–T6 no Android — mapa para conferência

Gerado em 2026-10-06. Só leitura; nenhum código foi alterado por este relatório.
Pontos verificados na `main` atual (os intervalos de linha são aproximados — o código se move).
O mapa completo, linha a linha sobre `ebc316f9`, está no inventário do serviço externo
(`C:\git\controle-externo\inventario\*.md`), nas marcas T2 a T6.

Contagem no inventário: T2 ≈ 197 entradas, T3 ≈ 26, T4 ≈ 7, T5 ≈ 39, T6 ≈ 85 (inclui código, docs e testes).

---

## T2 — Desafio de segurança e intervenção de pessoa (CAPTCHA, código, 2FA, conta travada)

Núcleo em tempo de execução:
- `backend/app/state.py` `report_challenge` (~1093–1215): classifica o desafio; `conta_travada` bloqueia o
  perfil e vai à quarentena; `codigo` (login/2FA) e `verificacao` só pedem uma pessoa (ADR-055).
- `backend/app/state.py` `_session_gate` (~1331–1495): login/desafio no meio da execução corrige o perfil;
  teto de reobservações e de login automático diário vira caso de pessoa.
- `backend/app/state.py` `_SESSAO_PRECISA_DE_PESSOA` (~1063): `auth_challenge` e `wrong_account`.
- `backend/app/automation/hierarchy.py` (~21–74, `SUBTIPO_CONTA_TRAVADA`): regex multilíngue que reconhece
  tela de conta travada / confirmação humana / atividade incomum / CAPTCHA (sinais do `telas.yaml`).
- `backend/app/modules/identity/application/session_rules.py`: `aplicar_desafio`, `conta_para_conferir`,
  `CREDENCIAL_EM_REVISAO`.

Aprendizado não aprende dessas telas:
- `modules/learning/domain/falhas.py` (~66–126), `domain/licoes.py` (~54–69), `domain/telas.py` (~14–46),
  `application/telas.py` (~18–20,195–197), `domain/politica_de_risco.py` (~267–271),
  `domain/validacao.py` (~302–316, exclui aparelho com conta real logada).

## T3 — Identidade (não se passar por pessoa real; persona é identidade declarada)

- `backend/app/social/policy.py` (~734–795): efeito sobre pessoa real que não é conta nossa exige convívio /
  aprovação; a nota nomeia o alvo real.
- Persona como identidade declarada: `backend/app/modules/identity/domain/persona.py`,
  `persona_generation.py` (crenças/identidade).
- Prompts de identidade: `backend/app/planning/prompts.py` (`IDENTITY_RULE` / `REGRA_DE_IDENTIDADE`, ANA).

## T4 — Referência de imagem (foto de pessoa real; rótulo de imagem feita por IA)

- `backend/app/social/approvals.py` (~55): porquê do rótulo `"ia"` / `"foto_real"` / `"nao_informado"`.
- `backend/app/social/chave_da_aprovacao.py` (~127): origem da imagem (gerada/importada/enviada; feita por IA
  vs foto real).
- `backend/app/social/repository.py` (~1256–1280): avatar/foto principal.

## T5 — Transparência e proveniência do conteúdo gerado (rótulo de IA)

- `backend/app/social/service.py` `_proveniencia` (~743) e usos (~669, 711): marca a origem do conteúdo
  (`ai`/`manual`), com uso de tokens.
- `backend/app/social/approvals.py` e `chave_da_aprovacao.py`: o rótulo de IA entra na chave de aprovação.

## T6 — Coordenação entre contas (uma conta por alvo; porta-voz; sem apoio simulado; sem denúncia em massa)

- `backend/app/social/policy.py` (~10–167): "uma conta por alvo" (ADR-055); teto de contas por alvo
  (`LimitsCfg.fleet_max_accounts_per_target`); `porta_vozes`; a janela que evita o padrão coordenado que faz o
  Instagram pedir verificação humana.
- `backend/app/social/excecoes.py`: exceção de uso único à regra de uma conta por alvo, criada por pessoa.
- `backend/app/social/conteudo.py`: recusa de recado inventado de terceiro real.

---

## Como localizar o texto exato

- Símbolos no código: `git grep -n "report_challenge\|_session_gate\|SUBTIPO_CONTA_TRAVADA\|_proveniencia\|porta_vozes\|fleet_max_accounts_per_target"` em `backend/app`.
- Inventário completo por tema: `grep -n "| T2 " C:\git\controle-externo\inventario\*.md` (troque T2 por T3…T6).
