---
name: plano-100
description: Preparar a execução de docs/plano-100.md com esforço automático por bloco e retomada.
disable-model-invocation: true
model: claude-sonnet-5
effort: medium
---

Leia `docs/claude-plano-100.md`. Execute somente a conferência local
`python scripts/claude-plan-100.py check`; ela não chama a IA.

Mostre o comando `python scripts/claude-plan-100.py run` para um terminal normal
do usuário, na raiz do repositório. O executor aplica modelo/esforço a cada bloco
pelo CLI e retoma a mesma sessão. Não o inicie de dentro de uma sessão Claude;
não remova `CLAUDECODE` nem contorne a proteção contra sessões aninhadas.

Para trabalhar interativamente em um bloco, indique os atalhos
`/plano-100-high <IDs>` e `/plano-100-medium <IDs>` conforme o mapa.
Não prometa troca automática encadeando skills dentro do mesmo turno.
Este atalho prepara a execução; não implementa o plano sozinho.

