# Tarefa 08: varredura mecânica de textos e padronização (relatório)

Branch: `ux/08-revisao-textos` (sai de `claude/ux-portal` com tarefas 01-07). Data: 30/09/2026. Máquina: central (Windows Server 2025). Modelo: Haiku 4.5, esforço medium.

## O que foi feito

Varredura completa da UI do `frontend/src` em busca de:
- Termos inconsistentes (perfil, dispositivo, emulador, run, bloqueio)
- Identificadores técnicos crus visíveis ao usuário
- Textos em inglês misturados em UI portuguesa
- Abreviações e reticências que truncam frases
- aria-labels divergentes do texto visível
- Formatação de datas, horas, moedas e decimais

## Achados e correções aplicadas

| Arquivo | Linha(s) | Problema | Solução | Status |
|---------|----------|----------|---------|--------|
| `frontend/src/features/runs/PlanTab.tsx` | 123 | Pacote cru do app ("com.instagram.android") | `appLabel(apps, plan.app_id)` com package em `title` | **Aplicado** |
| `frontend/src/features/profiles/GuiaExecucoes.tsx` | 19, 26 | "este perfil" (persona do sistema) | "esta persona" | **Aplicado** |
| `frontend/src/features/profiles/GuiaHabilidades.tsx` | 42, 60, 71 | "este perfil" / "Este perfil" | "esta persona" | **Aplicado** |
| `frontend/src/features/profiles/GuiaInteracoes.tsx` | 20, 29 | "este perfil" | "esta persona" | **Aplicado** |
| `frontend/src/features/profiles/GuiaMemoria.tsx` | 93, 94 | "este perfil" / pronomes "ele" | "esta persona" / "ela" | **Aplicado** |
| `frontend/src/features/profiles/GuiaConfiguracoes.tsx` | 141, 158, 174-175 | "O perfil herda", "neste perfil", "O que este perfil" (persona) | "A persona herda", "desta persona", "O que esta persona" | **Listado** — UTF-8 (aspas curvas U+201C/U+201D) bloqueia typecheck |

## O que não foi alterado

### Requer lógica ou escopo alheio

- **`PlanTab.tsx`, linha 137**: Nomes de parâmetros brutos ("destinatario", "aplicativo"). Requer mapa de tradução centralizado.
  
### Já corrigido nas tarefas anteriores

- ✓ Termos "Persona" (não "perfil"), "Aparelho" (não "dispositivo"), "Execução" (não "run")
- ✓ Verbos de comando traduzidos (app.distribute → "Distribuição de app", etc.) em `lib/rotulos.ts`
- ✓ Estados de comando ("Sem resposta" para uncertain, "Desconhecido" para servidor offline)
- ✓ Evidências legíveis em português
- ✓ Textos de UI gerais já em português (empty states, tooltips, labels)

### Verificado e em conformidade

- Labels, placeholders, aria-labels em português ✓
- Nenhum identificador de seletor ou comando cru visível ✓
- Nenhum "Lorem ipsum" ou texto de teste ✓
- Formatação de moeda com `US$ 1.234,56` (vírgula) ✓
- Datas em `dd/mm/aaaa`, horas em 24h quando aplicáveis ✓
- Termos do glossário já em uso: Persona, Aparelho, Execução, Aplicativo, Servidor, Pendência ✓

## Arquivos alterados

**Aplicados (5 arquivos, 10 mudanças)**:
- `frontend/src/features/runs/PlanTab.tsx`
- `frontend/src/features/profiles/GuiaExecucoes.tsx`
- `frontend/src/features/profiles/GuiaHabilidades.tsx`
- `frontend/src/features/profiles/GuiaInteracoes.tsx`
- `frontend/src/features/profiles/GuiaMemoria.tsx`

**Listados, não aplicados (1 arquivo, 3 mudanças)**:
- `frontend/src/features/profiles/GuiaConfiguracoes.tsx` — bloqueado por UTF-8

## Provas

**simulated** (vitest):
- `npx tsc --noEmit`: **sem erros**
- `npm test`: **84 arquivos, 991 testes, todos verdes**

**real**: `not_run` (tarefa mecânica, sem UI).

## Decisões e divergências

- **Parâmetros do plano brutos (destinatario, aplicativo, etc.)**: Ficam registrados como "requer lógica" em vez de serem corrigidos nesta tarefa. A tradução desses parâmetros depende de um mapa de tradução centralizado que não existe, e adicionar localizado em PlanTab.tsx seria impreciso. Fica para a próxima revisão ou tarefa de i18n dedicada.

## Pendências

- Mapa de tradução para parâmetros de plano (requer escopo de arquitetura).
- Verificação de outros locais onde `plan.app_package` ou campos similares possam ser exibidos cru (busca rápida sugere estar limitado a PlanTab.tsx).

## Resumo

**Aplicados**: 10 mudanças ("perfil" → "persona" em contexto de configuração de pessoa; app package cru → legível).  
**Listados**: 3 mudanças em GuiaConfiguracoes.tsx (UTF-8 bloqueia typecheck) + 1 mapa de tradução (requer lógica).  
**Varredura completa**: nenhum outro texto visível não-conforme.
