# Política de contexto, modelos e subagents

Lida sob demanda (o `CLAUDE.md` só aponta para cá). Objetivo: **qualidade por token**, não o menor número de tokens. A conversa é
memória operacional temporária; o repositório é a memória durável.

## 1. Modelo e esforço

| Trabalho | Modelo / esforço |
|---|---|
| Mecânico e comum (renomear, texto, varredura, formatação, relatórios) | Sonnet `medium` (ou Haiku `low` se for puramente mecânico) |
| Implementação difícil, depuração, coordenação do dia a dia | Sonnet `high` |
| Arquitetura realmente complexa, investigação excepcional, revisão independente de risco | Opus `high` |
| Opus `xhigh` | **só quando o dono pedir, para uma tarefa específica** |

Padrão persistido em `~/.claude/settings.json`: Opus `high`, Sonnet `medium`. Elevar na sessão é permitido e consciente.

## 2. Subagents (prioridade alta)

Antes de criar um: (1) precisa mesmo de agent? (2) dá para fazer direto? (3) outra frente já faz isso? (4) precisa ser paralelo?
(5) precisa de Opus? (6) precisa do contexto completo? (7) quantos turnos? **Paralelismo por necessidade, não por padrão.**

Use os agents de `.claude/agents/` (modelo, esforço e `maxTurns` já fixados):

| Agent | Quando | Modelo / esforço | maxTurns |
|---|---|---|---|
| `worker-mecanico` | trocas de texto, varredura, formatação, relatório | Haiku, low | 20 |
| `worker-investigacao` | ler, buscar, medir, comparar (sem editar) | Sonnet, medium | 30 |
| `worker-impl` | implementar com teste, depurar | Sonnet, high | 45 |
| `worker-arquitetura` | desenho difícil, revisão independente | Opus, high | 35 |

Regras do prompt de um worker: caminho do worktree, branch, arquivos de posse, critério de aceite, o que NÃO fazer, e o formato
de retorno. **Não** cole contexto longo: aponte para o arquivo (`.claude/handoff-current.md`, relatório do item). Um worker faz um
objetivo; se passar do `maxTurns`, devolve `PARTIAL` com o que falta e o coordenador reabre com escopo menor (o limite não sobe sozinho).

Formato de retorno (curto, sem transcript, sem raciocínio, sem arquivo inteiro, sem log inteiro):

```
Feito:
Evidências:
Validação:
Bloqueios:
Mudanças:
Próximo:
```

## 3. Saída de ferramentas

- **Bash:** filtrar (`grep`, `head`, `tail`, `wc`, contagem), ou mandar a saída para arquivo e ler o trecho. Teste devolve
  contagem, pass/fail e as falhas relevantes, nunca a lista dos aprovados. Log: nunca inteiro; `tail -n 40` ou `grep -n`.
  `bashOutputMaxChars` está em 12000: o excedente vira arquivo com prévia e caminho; leia o trecho que precisar.
- **Diff:** `git diff --stat` e `--name-only`; depois só o trecho.
- **Arquivos:** localizar (`Grep`/`Glob`), depois `Read` com `offset`/`limit`. Não reler inteiro sem motivo. Documentos grandes do
  produto (`docs/decisoes.md`, `api-contract.md`, `relatorio-validacao.md`, `plano-100.md`, `estado-atual.md`): `grep -n` + intervalo.
- **JSON/API:** filtrar as propriedades (`jq`/Python), não despejar a resposta.
- **Scripts:** escrever o arquivo uma vez e executar; não reenviar script grande inline a cada tentativa. Comando acima de ~60
  linhas é sinal para virar arquivo.
- **Navegador:** `get_page_text`/`read_page` antes de captura de tela; captura só do problema, pequena.

## 4. Tamanho da sessão

| Contexto | Ação |
|---|---|
| 0–250k | normal |
| 250–400k | atenção: parar de ler coisas grandes, resumir em arquivo o que for durável |
| 400–500k | **preparar handoff** (`/handoff`): atualizar `.claude/handoff-current.md` e decidir a troca |
| >500k | não continuar acumulando: **nova sessão limpa + handoff**; `autoCompactWindow` (500k) é só a rede de segurança |

Sessão acima de ~500k deve ser **substituída**, não retomada para trabalho normal. Sessão antiga é histórico (ver
`.claude/session-registry.md`): não é reativada para obter contexto.

## 5. Disciplina de resposta

Atualizações intermediárias curtas: `Feito / Validado / Pendente / Próximo`. Não repetir o plano a cada ação, não narrar cada
comando, não fazer retrospectiva sem pedido. Estado importante vai para o repositório (handoff, relatório, `estado-atual.md`).

## 6. Multissessão

Um **coordenador leve** (objetivo, estado, decisões, dependências, resultado dos workers) + workers descartáveis. Sem logs brutos,
transcripts nem raciocínio no coordenador. Estado durável em Git/docs. Antes de abrir sessão ou agent, confira
`.claude/session-registry.md` para não duplicar uma frente. Integração, testes e documentação de fechamento cabem no próprio
coordenador: um "agente integrador" separado só se justifica com carga paralela real.

`.claude/handoff-current.md`, `.claude/handoffs/` (um handoff por sessão, para sessões simultâneas) e `.claude/session-registry.md` são **locais** (excluídos em `.git/info/exclude`, não versionados): se faltarem em um clone novo, a skill `handoff` os cria.

## 7. Memória

Estado operacional temporário (hash de hoje, fila de agents, o que está rodando) **não** vira memória permanente: vai para
`.claude/handoff-current.md`. Memória é para fatos duráveis (preferências do dono, decisões, armadilhas).

## 8. Advisor

A ferramenta `advisor` encaminha a conversa inteira. Use antes de decidir uma abordagem e antes de concluir, em sessão abaixo
de ~250k; evite em laço.
