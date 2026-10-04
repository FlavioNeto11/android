---
name: preparar-tarefa
description: 'Preparar tarefa antes de implementar: aceite, decisões do dono, aprendizados, se toca o mundo real. Use antes de item do plano-100 ou mudança média/grande.'
argument-hint: <ID do plano-100, ex. 1.3, ou descrição da tarefa>
---

## Gatilho

Usuário pede para começar um item do plano-100 (ex.: "faça o 1.3"), ou qualquer tarefa de escopo não trivial.

## Entradas

`$ARGUMENTS` — um ID do plano-100 (formato `N.N` ou `T.N`) ou uma descrição livre da tarefa.

## Contexto mínimo

- **ID do plano-100 com pacote gerado:** ler só `.claude/plano-100/pacotes/<id>.md` — é autocontido (linha do
  plano, achados citados na íntegra, arquivos candidatos). Sem pacote: `python scripts/plano-100-pacotes.py` e
  então ler o pacote; ou, para só a linha, `grep -n "^| <id> |" docs/plano-100.md`.
- `grep -n "<id ou assunto>" docs/decisoes.md` — decisões do dono já tomadas na área, para não redecidir nem
  contradizer uma escolha registrada.
- `grep -n -i "<área>" docs/conhecimento/aprendizados.md` — armadilhas já conhecidas.
- Arquivos candidatos: os que o pacote já lista, ou os que as buscas acima apontam.

**Não ler:** `docs/plano-100.md` inteiro nem `docs/auditoria-2026-09-21/`, a menos que o pacote cite um achado
específico cujo texto completo seja necessário.

## Passos

1. Confirmar o critério de aceite (linha do plano ou "Fecha quando" do pacote/fase).
2. Checar decisões e aprendizados relacionados.
3. Checar se o item toca o mundo real — produção, parque físico, relógio das máquinas, chamada paga de IA, conta
   real do Instagram. Pacotes de itens assim trazem o aviso de autorização explícito; se a tarefa não é um item do
   plano-100 mas tem o mesmo risco, aplicar o mesmo cuidado. Se sim, **pedir autorização ao usuário antes de agir**
   (ver `.claude/rules/segredos-e-mundo-real.md`).
4. Se o item é do plano-100 e o dono pediu execução pela esteira (não implementação direta nesta sessão):
   encaminhar para a skill `plano-100`, sem duplicar o trabalho dela aqui.
5. Listar arquivos e testes direcionados prováveis.

## Saídas

Resumo curto: critério de aceite, decisões/aprendizados relevantes, arquivos/testes alvo, e se falta autorização —
antes de qualquer edição de código.

## Critério de conclusão

O resumo foi apresentado; se autorização é necessária, foi pedida e a resposta aguardada antes de editar qualquer
arquivo de implementação.
