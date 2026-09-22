---
name: plano-100-ultracode
description: Preparar um único bloco do plano-100 para execução externa em Ultracode com Opus 5.
argument-hint: "[bloco, por exemplo 1-comandos]"
disable-model-invocation: true
model: claude-opus-5
effort: medium
---

Localize o identificador literal `$ARGUMENTS` em `.claude/plano-100.json`.
Se não corresponder a um único bloco, mostre os identificadores válidos.
Leia a seção de escalada de `docs/claude-plano-100.md` e forneça o comando
`python scripts/claude-plan-100.py run --block <identificador-validado> --effort ultracode`
para o usuário executar em um terminal externo ao agente.

Este atalho só prepara o comando, com medium. Não alegue ter ativado Ultracode
por meio deste frontmatter: Ultracode é um workflow do CLI, com raciocínio xhigh.
Não inicie outro processo Claude de dentro desta sessão nem remova `CLAUDECODE`.
Não acrescente outros blocos, conceda permissões ou altere o padrão do projeto.
