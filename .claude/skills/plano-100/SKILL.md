---
name: plano-100
description: Preparar a execução integral do plano-100 com um comando, esforço adaptativo e retomada automática.
disable-model-invocation: true
model: claude-opus-5
effort: medium
---

Leia `docs/claude-plano-100.md`. Execute somente a conferência local
`python scripts/claude-plan-100.py check`; ela não chama a IA.

Mostre somente o comando `python scripts/claude-plan-100.py` para um terminal normal
do usuário, na raiz do repositório. Ele inicia ou retoma o plano, escolhe medium/xhigh,
escala para max por dificuldade ou Ultracode por reorganização e volta ao perfil
normal quando o obstáculo é resolvido. Não peça parâmetros, blocos ou esforços.
Não o inicie de dentro de uma sessão Claude;
não remova `CLAUDECODE` nem contorne a proteção contra sessões aninhadas.

Não prometa troca automática encadeando skills dentro do mesmo turno.
Este atalho prepara a execução; não implementa o plano sozinho.
Os atalhos manuais existem para uso avançado, somente quando pedidos explicitamente.
Explique apenas que bloqueios reais e limites de chamadas ficam registrados;
credenciais, permissões e decisões operacionais do plano continuam necessárias.
