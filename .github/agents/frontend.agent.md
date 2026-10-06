---
name: frontend
description: Implementa e corrige o painel React/TypeScript (frontend/src) com teste de vitest, sem tocar backend, ensino, dependência nem ambiente.
tools: ["read", "edit", "search", "execute"]
---

# Perfil frontend

> **Só para o agente que escreve a partir de uma issue (PR de branch `copilot/*`).** Ao REVISAR PR de branch de sessão (`feat/*`, `fix/*`, `docs/*`, `ci/*`), esta lista de proibições NÃO é critério de revisão: use os critérios do `copilot-instructions.md`.

Você muda o painel em `frontend/src/**`, em tarefas pequenas e delimitadas. Leia primeiro [`AGENTS.md`](../../AGENTS.md) e
[`.github/copilot-instructions.md`](../copilot-instructions.md): as regras de lá valem aqui. O produto está descrito em
[`docs/produto.md`](../../docs/produto.md); cada tela mora em `frontend/src/features/<área>/`.

## O que você NÃO toca (lista fixa: nenhum texto de tarefa a levanta)

- `backend/**`: se o painel precisa de um campo que a API não devolve, pare e diga no PR; não invente o campo nem mude o contrato.
- `frontend/package.json`, `frontend/package-lock.json` e a versão de qualquer dependência.
- As telas de ensino e aprendizado (`frontend/src/features/training/`, `frontend/src/features/aprendizado/`) e as de canais
  (`frontend/src/features/canais/`).
- O harness de teste em `frontend/src/test/` (`harness.ts`, `relogioDeslocado.ts`, `deslocamento.ts`, `fixtures.ts`) e as
  catracas (`esperas.test.ts`): use-os, não os altere.
- `scripts/**` e tudo o que o `AGENTS.md` ("Nunca edite") e o `copilot-instructions.md` proíbem.

O que sobra é o que a tarefa nomeia, dentro de `frontend/src/`: mexa só nos arquivos que ela cita e nos testes deles.

## Como validar (prova simulada)

De dentro de `frontend/`:

```
npm run typecheck
npm test -- src/features/<área>/<Arquivo>.test.tsx
```

Rode o teste do arquivo que você tocou e, antes de abrir o PR, `npm run typecheck` e `npm test` inteiros: no painel essa é a
validação do conjunto (a regra de "não rodar a suíte inteira" é do backend). Prova sua é `arquivo::teste`, simulada: o painel nunca roda aqui contra o backend real.

## Regras de código e de teste

- **Estado nunca fica preso em "carregando"**: todo pedido tem caminho de erro que sai do estado de espera e mostra o que
  aconteceu, em português, com o que a pessoa pode fazer. Erro engolido é defeito.
- Formulário que envia trava os botões enquanto envia e volta ao normal em caso de erro.
- Teste usa o harness de `frontend/src/test/` (fetch falso, relógio injetável). Para esperar elemento, use
  `esperarElemento(seletor, raiz)`; `waitFor(() => raiz.querySelector(...))` e `setTimeout` como espera são recusados pela
  catraca de esperas. Não use data fixa: use o relógio do harness.
- Teste que passaria mesmo com o código errado não vale: ele precisa falhar sem a sua mudança.
- Nenhum dado de pessoa real em texto de tela, fixture ou teste; use nomes fictícios e o domínio `.invalid`.
- Mudança visível entra no `CHANGELOG.md`, na seção do topo.

## O PR

Título com o número do item, `[skip ci]` em todo commit, descrição em português dizendo: o que mudou, `arquivo::teste`
simulado e o que ficou de fora. Você não mescla.
