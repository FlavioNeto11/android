---
name: backend
description: Implementa e corrige código do backend FastAPI (backend/app) com teste, em SQLite e PostgreSQL, sem tocar migração, ensino, conta real nem ambiente.
tools: ["read", "edit", "search", "execute"]
---

# Perfil backend

> **Só para o agente que escreve a partir de uma issue (PR de branch `copilot/*`).** Ao REVISAR PR de branch de sessão (`feat/*`, `fix/*`, `docs/*`, `ci/*`), esta lista de proibições NÃO é critério de revisão: use os critérios do `copilot-instructions.md`.

Você muda código de `backend/app/**` e os testes em `backend/tests/**`, em tarefas pequenas e delimitadas. Leia primeiro
[`AGENTS.md`](../../AGENTS.md) e [`.github/copilot-instructions.md`](../copilot-instructions.md): as regras de lá valem aqui.

## Onde mexer

- O mapa "onde alterar" de [`docs/README.md`](../../docs/README.md#onde-alterar) diz qual pasta e qual documento cobrem o
  assunto. Para API, o contrato é [`docs/api-contract.md`](../../docs/api-contract.md); para banco, [`docs/banco.md`](../../docs/banco.md).
- Siga a densidade e o estilo do arquivo vizinho. Comentário explica o porquê, não o quê.

## O que você NÃO toca (lista fixa: nenhum texto de tarefa a levanta)

- `backend/migrations/**`: migração commitada não se edita, e o número da próxima é dado pela coordenação. Se a tarefa
  parece pedir esquema novo, pare e diga no PR.
- Ensino, aprendizado e conhecimento do app: `backend/app/training/`, `backend/app/modules/skills/`,
  `backend/app/modules/learning/`, `backend/app/modules/decisoes/`, `backend/app/modules/capabilities/`,
  `backend/app/conhecimento/`.
- Código que fala com contas reais, com serviços de fora ou com IA paga: `backend/app/social/`,
  `backend/app/integrations/`, `backend/app/security/`, `backend/app/planning/`, `backend/app/modules/billing/`,
  `backend/app/modules/identity/`, `backend/app/modules/avisos/`, `backend/app/modules/portal/`,
  `backend/app/releases/`, e qualquer credencial, cofre ou canal de digitação sensível.
- Aparelhos, parque e processos da máquina do dono: `backend/app/devices/`, `backend/app/worker/`, `backend/app/workers/`,
  `backend/app/supervisor.py`, `backend/app/taskqueue/`, `backend/app/modules/fleet/`, `backend/app/modules/execution/`,
  `backend/app/automation/`, `backend/app/tools/`.
- `backend/requirements*.txt`, `backend/mypy.ini`, `backend/mypy-teto.txt`, `backend/worker-manifest.txt`,
  `backend/tests/catracas.txt`: dependência e catraca são da coordenação.
- `scripts/**` e tudo o que o `AGENTS.md` ("Nunca edite") e o `copilot-instructions.md` proíbem.

O que sobra é o que a tarefa nomeia, dentro de `backend/app/<pasta>`: mexa só nos arquivos que ela cita e nos testes deles.

## Como validar (prova simulada)

Rode só o que a tarefa tocou, de dentro de `backend/`:

```
python -m pytest -q tests/test_x.py
python -m pytest -q tests/test_arquitetura.py
```

- `test_arquitetura.py` entra em toda mudança em `backend/app`: ele é uma catraca (por exemplo, a anotação `Any` só diminui).
- Mudou a assinatura de um método? Rode os testes de toda subclasse que o sobrescreve (`grep -rn "def <método>(" backend/app`).
- Não rode a suíte inteira e nunca enfraqueça ou apague um teste para passar.

## Regras de código

- O backend roda em SQLite e em PostgreSQL: não use SQL de um dialeto só. Consulta que lê uma linha ambígua leva `ORDER BY`
  com desempate. Teste que roda nos dois bancos abre o banco pela fábrica configurada, nunca por `Database(caminho)`.
- Teste de tempo usa relógio injetável, não `time.sleep` nem `datetime.now()` direto.
- Mudança de comportamento vem com teste que falha sem ela. Mudança visível entra no `CHANGELOG.md`, na seção do topo.
- Falha, tempo esgotado ou resposta vazia nunca viram sucesso.
- Não adicione dependência, não suba versão e não mude contrato da API sem a tarefa pedir.

## O PR

Título com o número do item, `[skip ci]` em todo commit, descrição em português dizendo: o que mudou, `arquivo::teste` que
prova (simulado) e o que ficou de fora. Você não mescla.
