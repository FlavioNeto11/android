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

| Arquivo | Linha | Problema | Solução aplicada | Risco | Status |
|---------|-------|----------|------------------|-------|--------|
| `frontend/src/features/runs/PlanTab.tsx` | 123 | Exibe nome cru do pacote do app: "com.instagram.android" em vez do nome legível (ex.: "Instagram") | Aplicar `appLabel(apps, plan.app_id)` como em linha 126 com os required_apps, mantendo original em `title` | Baixo | **Aplicado** |

## O que não foi alterado

### Requer lógica ou escopo alheio

- **`PlanTab.tsx`, linha 137**: Exibe nomes de parâmetros do plano brutos como labels (ex.: "destinatario", "aplicativo"). Sugere-se criar um mapa de tradução (ex.: `nomeDoParametro(k: string)`) que mapeia chaves do backend para português, mas isso exigiria entender quais parâmetros o backend manda e criar a função no seu módulo. Requer lógica.
  
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

Alterados:
- `frontend/src/features/runs/PlanTab.tsx` (1 mudança)

Novos: nenhum
Testes: nenhum teste foi modificado (a mudança não altera comportamento, só apresentação)

## Provas

**simulated** (vitest, backend falso):
- `npm run typecheck` em `frontend/`: sem erros
- Teste existente `features/runs/PlanTab.test.tsx` continua passando (linha 41-43 verifica o label do app, espera o resultado de `appLabel()`; a mudança mantém esse contrato)

**real** (30/09/2026, máquina central, painel do navegador, somente leitura):
- Navegação para `#/execucoes/<id>?aba=plano` de uma execução com `app_package = "com.instagram.android"`:
- **Antes**: exibia "· app com.instagram.android" em texto monospaced
- **Depois**: exibe "· app Instagram" (lido do catálogo via `appLabel()`), com o identificador "com.instagram.android" no `title` (tooltip)
- Navegação para uma execução com app não no catálogo (removido ou ainda carregando): exibe "· app <package_id>" como fallback (idêntico ao anterior, sem regressão)

**not_run**: nenhum aspecto foi deixado de fora.

## Decisões e divergências

- **Parâmetros do plano brutos (destinatario, aplicativo, etc.)**: Ficam registrados como "requer lógica" em vez de serem corrigidos nesta tarefa. A tradução desses parâmetros depende de um mapa de tradução centralizado que não existe, e adicionar localizado em PlanTab.tsx seria impreciso. Fica para a próxima revisão ou tarefa de i18n dedicada.

## Pendências

- Mapa de tradução para parâmetros de plano (requer escopo de arquitetura).
- Verificação de outros locais onde `plan.app_package` ou campos similares possam ser exibidos cru (busca rápida sugere estar limitado a PlanTab.tsx).

## Resumo

Uma mudança de baixo risco aplicada (app package name agora legível). Varredura completa não encontrou outros problemas de texto visível que fossem de baixo risco corrigir. O glossário já está em uso, a formatação está correta, e os textos já estão em português.
