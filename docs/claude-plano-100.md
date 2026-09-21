# Executar o plano com esforço por bloco

A execução automática usa **Sonnet 5**, um processo Claude por vez e uma sessão
retomada com `--resume`. O executor passa `--effort medium|high` e o mesmo
valor no ambiente do processo filho. Isso não altera o ambiente do terminal,
as permissões ou os limites administrados da sua instalação.

O mapa em `.claude/plano-100.json` cobre os 67 IDs do plano em 19 blocos.
Mudanças no plano que acrescentem ou removam IDs invalidam o mapa, evitando
que itens sejam omitidos silenciosamente. A ordem preserva as prioridades
iniciais e adianta 6.1/6.2/6.6 para o caminho crítico; dependências e autorizações
ainda precisam ser conferidas pelo executor de código.

| Esforço | Blocos |
|---|---|
| high | Proteção/autenticação, contratos, comandos, saúde, scheduler, migrações/storage, instalação, hub de IA, acesso, supervisão e regressão |
| medium | Ajustes comuns, painel, catálogo/intervenção/loja via acesso remoto, interface/avaliação de IA, operação, capacidade e documentação |

Se um item medium exigir alteração estrutural não prevista, registre o motivo
e ajuste seu bloco no mapa antes da execução, reconciliando o estado salvo.
O executor não faz escalada para Opus ou Fable. Se o CLI informar uso de outro
modelo, ele interrompe antes da próxima chamada, para conferência da configuração.

## Começar

Na raiz de uma cópia local desta branch, use Python 3.10+ e Claude Code atualizado
e já autenticado. A primeira utilização do CLI precisa ocorrer interativamente
com `claude` para autenticação e confiança no projeto.

Confira sem consumir IA:

```powershell
python scripts/claude-plan-100.py check
python scripts/claude-plan-100.py run --dry-run
```

Inicie **no terminal normal**, inclusive o terminal integrado da IDE, fora de uma
conversa/agente Claude:

```powershell
python scripts/claude-plan-100.py run
```

O padrão é `acceptEdits`: permite editar arquivos, mas comandos de teste e rede
continuam sujeitos às permissões já configuradas. O executor nunca passa
`--dangerously-skip-permissions`. Quando houver recusa, a execução para e preserva
o checkpoint; ajuste apenas a permissão necessária na sessão interativa e retome.
Se você já usa o modo automático de permissões do Claude, pode selecionar
`--permission-mode auto`; suas políticas continuam valendo.

Para limitar cada chamada, por exemplo:

```powershell
python scripts/claude-plan-100.py run --max-budget-usd-per-call 5 --max-turns 80
```

O valor de US$ 5 é apenas exemplo de teto **por chamada**, não previsão de custo
nem orçamento total. Um bloco pode precisar de várias chamadas. O limite padrão
é três rodadas por bloco por execução; ausência de avanço, erro ou saída inválida
interrompe o processamento. Não há retentativa infinita nem aumento de esforço
depois de erro.

## Retomar e conferir

Repita o comando `run`: os itens implementados não são repetidos, e a conversa é
retomada pelo ID salvo. Um item operacionalmente bloqueado fica registrado enquanto
os demais avançam, respeitando dependências; use `--retry-blocked` depois de resolver
a decisão/ambiente. Itens parciais continuam pendentes.

A sessão, os resultados JSON e o lock ficam em `.claude/plano-100/`, fora do Git.
Cada chamada registra o esforço **solicitado**, a utilização por modelo informada
pelo CLI e seu custo informado, sem afirmar que a telemetria comprova o esforço
interno do servidor. O resumo gerado é `docs/execucao-plano-100-runner.md`.
O checkpoint humano fica em `docs/execucao-plano-100.md` e as provas reais em
`docs/relatorio-validacao.md`. O estado local serve para agendamento; o relatório
de validação continua sendo a evidência dos aceites.

Após uma interrupção, confira alterações e checkpoint antes de retomar.
Se a sessão não tiver sido salva pelo CLI, `--fresh-session` cria uma nova conversa
mantendo os itens registrados; ela reconstitui o contexto pelo checkpoint.
Se o processo morrer sem liberar o lock, confirme que nenhum executor está ativo
antes de remover `.claude/plano-100/run.lock`. Não rode outro agente editando os
mesmos arquivos durante a execução.

Reinício/deploy de produção, gastos da aplicação, desafios e decisões da seção 1
do plano continuam condicionados às autorizações registradas. Este executor
orquestra o desenvolvimento; não concede essas autorizações.

## Skills interativas

- `/plano-100`: confere a estrutura e orienta a execução externa.
- `/plano-100-medium 3.6`: executa somente o item informado, solicitando medium.
- `/plano-100-high 1.3 1.4`: executa somente os itens informados, solicitando high.

As skills são locais ao projeto e invocadas diretamente. Se a pasta
`.claude/skills/` não existia ao abrir a sessão, reabra o Claude Code após obter
a branch. Não é necessário instalar um plugin ou alterar o modelo da aplicação.

O frontmatter `effort` é documentado, mas há relatos de falha em skills encadeadas
no mesmo turno. Por isso o caminho automático usa flags do CLI a cada chamada,
sem depender de uma skill mudar o esforço de outra.

## Verificação e limites conhecidos

```powershell
python -m unittest discover -s scripts/tests -p test_claude_plan_100.py
```

Os testes cobrem o mapa, argumentos de esforço/retomada, isolamento de ambiente,
saída inválida, bloqueios, ausência de avanço, lock e persistência. A validação
do executor usa um substituto local do CLI, sem chamadas pagas ou aparelhos reais.
A integração com sua instalação autenticada do Claude Code deve ser conferida
na primeira execução; políticas da organização ainda podem limitar o modelo/esforço.
Não foi executada nenhuma fase funcional do plano ao adicionar esta estrutura.

Referências:
- [Skills e frontmatter](https://code.claude.com/docs/en/skills)
- [Modelo e esforço](https://code.claude.com/docs/en/model-config)
- [CLI e flags](https://code.claude.com/docs/en/cli-reference)
- [Saída estruturada e retomada](https://code.claude.com/docs/en/headless)
- [Relato sobre esforço em skills encadeadas](https://github.com/anthropics/claude-code/issues/65531)
