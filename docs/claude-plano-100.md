# Executar o plano com um comando

Na raiz do repositório, em um terminal normal da IDE, execute:

```powershell
python scripts/claude-plan-100.py
```

O mesmo comando inicia, continua após uma interrupção e reavalia os itens
bloqueados. Não é necessário escolher modelo, esforço, bloco ou parâmetro de retomada.
Use Python 3.10+ e Claude Code atualizado, já autenticado e com o projeto confiável.
Opus 5 requer Claude Code 2.1.219+. A instalação e o login inicial continuam necessários.

## O que o controlador decide

O modelo permanece **Opus 5**, com os 67 itens distribuídos nos mesmos 19 blocos.
O mapa define o ponto de partida; o resultado de cada chamada orienta a próxima.

| Situação | Decisão automática |
|---|---|
| Trabalho comum | Começar em `medium` |
| Contratos, concorrência, segurança e outros blocos críticos | Começar em `xhigh` |
| Investigação indica dificuldade de raciocínio, ou falta de avanço técnico | Subir de `medium` para `xhigh`, depois para `max`, conforme disponibilidade |
| Diagnóstico justifica reorganização ou partes independentes | Tentar Ultracode uma vez no bloco, com raciocínio `xhigh` |
| Obstáculo resolvido e trabalho comum restante | Voltar ao esforço inicial do bloco |
| Bloco concluído | Seguir para o próximo bloco com seu próprio perfil |
| Falta de credencial, permissão, infraestrutura ou decisão | Registrar o bloqueio; não aumentar esforço por esse motivo |

Ultracode é uma forma de orquestrar workflows, não um nível acima de `max`.
Sua seleção depende do diagnóstico, não de uma sequência obrigatória após `max`.
O controlador verifica os esforços anunciados pelo CLI e evita Ultracode quando
workflows estão desativados no ambiente ou nas configurações locais conhecidas.
Se essa alternativa não estiver disponível, tenta aprofundar o raciocínio dentro
dos níveis disponíveis. Não remove restrições locais ou administradas.

A decisão vem dos campos estruturados `next_action` e `reason`, acompanhados de
estado, evidência e progresso por item. Saídas inválidas não provocam escalada.
Nos modos comuns, workflows e ferramentas de agentes ficam desativados. Em
Ultracode, o prompt limita a delegação aos IDs atuais e manda aguardar seu término.
A telemetria registra o modo solicitado; não comprova o esforço aplicado pelo servidor
nem garante que políticas da instalação permitiram executar o workflow.

## Retomada e limites

A sessão, as decisões, os resultados e o lock ficam em `.claude/plano-100/`, fora
do Git. O runner salva o próximo esforço antes de continuar. Itens implementados
não se repetem; itens bloqueados são reavaliados uma vez por nova execução do comando.
Cada bloco dispõe automaticamente de 4 a 8 chamadas por execução, conforme seu
tamanho. Não há aumento ilimitado de esforço ou repetição automática infinita.

Se um bloco esgotar suas tentativas ou não tiver uma escalada útil, ele permanece
pendente, com motivo, e o runner continua os demais blocos. As dependências e os
critérios de aceite continuam sendo conferidos pelo agente. Um resultado parcial
nunca vira implementação concluída apenas para avançar o agendamento.

Quando o CLI informa exatamente que a sessão salva não existe, o runner cria uma
nova conversa e reconstrói o contexto pelo checkpoint, preservando itens e histórico.
Essa recuperação ocorre no máximo uma vez por execução. Um limite de turnos por
chamada pode ser retomado até duas vezes com o mesmo esforço, dentro do limite do
bloco. Erros de orçamento, autenticação, permissões, saída inválida ou uso novo de
outro modelo interrompem a execução e preservam o estado. Após resolver a causa,
repita o mesmo comando, sem precisar escolher parâmetros.

Os perfis publicados anteriormente neste PR, tanto Sonnet quanto Opus com escalada
manual, são migrados automaticamente. A migração preserva sessão, itens e histórico
somente quando os perfis de origem e destino são conhecidos. Mudanças não reconhecidas
no plano, mapa ou prompt continuam exigindo reconciliação; não se apagam checkpoints.

O Claude Code informa consumo acumulado da conversa ao usar `--resume`. O runner
compara os contadores com o registro anterior para distinguir uso histórico de
Sonnet de uma nova troca de modelo. Os custos registrados são totais estimados da
sessão, não valores por chamada que possam ser somados diretamente.

O perfil usa a autenticação existente do Claude Code. Escolher Opus não escolhe a
forma de cobrança: confirme seu login na assinatura Max 20x. O runner não configura
credenciais, compra créditos ou modifica o modelo usado pela aplicação.

O modo de permissões existente permanece `acceptEdits`; as políticas do Claude
continuam valendo. Reinício/deploy de produção, gastos da aplicação e decisões
reservadas pela seção 1 do plano precisam das autorizações correspondentes.
Não execute dois runners ou agentes editando a mesma cópia ao mesmo tempo.

## Onde conferir

- `docs/execucao-plano-100-runner.md`: estado por item e decisões do controlador.
- `docs/execucao-plano-100.md`: checkpoint humano e resumo compacto de retomada.
- `docs/relatorio-validacao.md`: evidências dos aceites, separando real e simulado.
- `.claude/plano-100/state.json`: sessão, histórico e próximo esforço de cada bloco.

A skill `/plano-100` confere a estrutura localmente e fornece o comando único.
Ela não inicia outro Claude de dentro do agente. O comando `check` e a opção
`--dry-run` continuam disponíveis para inspeção sem chamadas à IA.

## Opções manuais para compatibilidade

Os atalhos `/plano-100-medium`, `/plano-100-xhigh` e `/plano-100-max` continuam
aceitando IDs. `/plano-100-high` é o nome antigo para xhigh. A skill
`/plano-100-ultracode` apenas prepara um comando externo para o bloco informado.
Eles não são necessários para a execução automática.

O CLI ainda aceita `run --block <bloco> --effort <esforço>` para forçar um perfil
em um bloco, desativando a escalada automática nessa execução. Também preserva
`--max-rounds`, `--max-turns`, `--fresh-session`, `--retry-blocked`, `--claude`,
`--permission-mode` e `--max-budget-usd-per-call` para uso avançado. Um orçamento
em dólares é um teto do CLI por chamada, não uma previsão ou teto total do plano.

## Verificação

```powershell
python -m unittest discover -s scripts/tests -p test_claude_plan_100.py
```

Os testes usam um substituto local do CLI: verificam as decisões automáticas,
os limites, a retomada, os modos avançados, as migrações e os bloqueios. A integração
com o Claude autenticado ainda precisa ser conferida na máquina do usuário.
Nenhuma fase funcional do plano é executada ao preparar ou testar este controlador.

Referências:

- [Modelo, esforço e Ultracode](https://code.claude.com/docs/en/model-config)
- [CLI e flags](https://code.claude.com/docs/en/cli-reference)
- [Workflows](https://code.claude.com/docs/en/workflows)
- [Retomada e consumo acumulado](https://code.claude.com/docs/en/headless)
- [Skills](https://code.claude.com/docs/en/skills)
