# Auditoria de tokens do ambiente Claude Code (01/10/2026)

**Data da auditoria: 2026-10-01.** Os números abaixo são uma análise pontual dessa data, registro histórico da otimização,
e **não** a configuração operacional atual (a vigente está em `.claude/politica-de-contexto.md`, `.claude/agents/` e nas settings).

Escopo: ecossistema Claude Code deste projeto. Nada do produto foi tocado. Números de sessões vêm de agregação em streaming dos
JSONL (nenhum conteúdo copiado); "tok" = chars/3,5, imagens contadas à parte (~1,5k tok cada). Versão em uso: Claude Code
**2.1.284** (`AppData\Roaming\Claude\claude-code\2.1.284\claude.exe`; a 2.1.281 também está instalada). As chaves de
configuração abaixo foram **conferidas no esquema embutido no binário** antes de serem usadas.

## Causa raiz

O custo vem de **histórico de conversa (Messages)** em sessões coordenadoras que viveram dias (29/09 16:50Z a 01/10 14:xxZ, 2.400
a 2.600 turnos do assistente, 600 a 1.000 chamadas de Bash cada) sem um teto de compactação, e do **custo próprio dos subagents**
(cada um com contexto novo). Não são o CLAUDE.md, as skills, a memória nem os MCPs (somam ~35k de ~1M).

## Achados

### A1. Resultados de ferramentas são ~50% do histórico
- PROBLEMA: Bash e Read devolvem saída inteira ao contexto.
- EVIDÊNCIA: coordenação (6e66d7f8): `tool_result` 2.108k chars (~600k tok); Bash 1.469k chars em 1.033 chamadas (média 1,4 KB);
  Read 354k chars em 58 chamadas, 10 resultados acima de 20k chars (97k a 133k chars cada: documentos lidos inteiros). Sessão
  Github (38fdd156): Bash 697k chars em 610 chamadas; Read 181k.
- IMPACTO: ALTO. Cada token entra uma vez e é relido (cache) em todos os turnos seguintes.
- CORREÇÃO: `bashOutputMaxChars` 30000 → 12000 (saída excedente vai para arquivo com prévia e caminho); regra de leitura por
  intervalo; testes devolvem só contagem e falhas.
- RISCO: baixo. Quem precisar do resto lê o arquivo indicado.

### A2. Entradas do assistente são 32% a 40% do histórico
- PROBLEMA: scripts inteiros e documentos enviados dentro de comandos (heredoc/inline) e mensagens longas a outras sessões.
- EVIDÊNCIA: `assistant_tool_input` 1.662k chars (~475k tok) na coordenação (39,5%) e 999k chars (~286k tok) na Github (32,4%).
- IMPACTO: ALTO.
- CORREÇÃO: política de escrever o arquivo uma vez e executá-lo; comandos curtos; handoffs por arquivo, não por mensagem longa.
- RISCO: baixo.

### A3. Sem teto de compactação
- PROBLEMA: `autoCompactWindow` ausente (modo "auto"); sessões chegaram a 805k e 831k de 1M.
- EVIDÊNCIA: `/context` das duas sessões coordenadoras; texto do binário: "The actual threshold is the minimum of this setting
  and your model's maximum context window".
- IMPACTO: ALTO. Cada turno relê o histórico inteiro (99% de cache hit barateia, não zera).
- CORREÇÃO: `autoCompactWindow: 500000` como rede de segurança, e política de handoff a partir de 400k.
- RISCO: médio-baixo. A compactação perde detalhe; por isso o handoff em arquivo vem antes (ver `handoff-current.md`).

### A4. Opus com esforço `xhigh` como padrão
- PROBLEMA: `~/.claude/settings.json` tinha `modelSettings.claude-opus-5-5.effortLevel = "xhigh"`.
- IMPACTO: ALTO (pensamento e tokens de saída, em todo trabalho que cai no Opus, inclusive subagents sem esforço próprio).
- CORREÇÃO: Opus `high`; Sonnet `medium` como padrão persistido; `xhigh` só por pedido explícito (sem `maxEffortLevel`, que o
  impediria também quando pedido).
- RISCO: baixo. A sessão pode elevar o esforço na hora.

### A5. Subagents sem definição própria
- PROBLEMA: não havia `.claude/agents/`; todo `Agent` virava `general-purpose` com ferramentas completas, sem `maxTurns` e com
  esforço herdado.
- EVIDÊNCIA: ~25 agents na orquestração de UX/UI, de ~110k a ~500k tokens cada (task notifications); um de "correções finais"
  com 67 usos de ferramenta e 171k tokens. Retornos ao coordenador foram pequenos (16 a 22 retornos, 45k a 52k chars): o custo
  está **dentro** do agent, não no que volta.
- IMPACTO: ALTO (~33% do uso).
- CORREÇÃO: quatro agents em `.claude/agents/` com modelo, esforço e `maxTurns` fixos e retorno condensado.
- RISCO: baixo. `maxTurns` pode cortar uma tarefa longa; o agent devolve "Bloqueios" e o coordenador reabre.

### A6. Navegador: imagens e páginas
- EVIDÊNCIA: sessão Github: 55 imagens e 344k chars de texto em `browser_batch`/`computer` (Chrome).
- CORREÇÃO: preferir `get_page_text`/`read_page` a captura; captura só do que for problema (regra na política).
- RISCO: baixo.

### A7. Documentos de handoff gigantes lidos inteiros
- EVIDÊNCIA: `docs/estado-atual.md` 52 KB (~13k tok), `docs/handoffs/pendencias-evolucao3.md` 61 KB, `docs/decisoes.md` 281 KB,
  `docs/relatorio-validacao.md` 202 KB, `docs/api-contract.md` 212 KB. A skill `retomar` mandava ler `estado-atual.md` inteiro.
- CORREÇÃO: `retomar` lê `.claude/handoff-current.md` (curto) e só o topo de `estado-atual.md`. A compactação dos documentos do
  produto é recomendação (não feita: são docs do produto, fora do escopo).
- RISCO: baixo.

### A8. Advisor encaminha a conversa inteira
- EVIDÊNCIA: `advisorModel: "fable"`; a ferramenta `advisor` envia todo o histórico a cada chamada.
- IMPACTO: MÉDIO em sessões longas.
- CORREÇÃO: política (poucas chamadas, antes de decidir e antes de concluir, e só em sessão abaixo de ~250k). Mantido.
- RISCO: nenhum.

### A9. Coordenadores duplicados
- EVIDÊNCIA: três sessões coordenadoras longas (Orquestração 509k, Github 832k, Coordenação 806k) mantendo o mesmo estado em
  paralelo, trocando mensagens longas.
- CORREÇÃO: um coordenador leve + workers descartáveis; `session-registry.md`; estado em `handoff-current.md`.

### A10. Prompt suggestions
- PROBLEMA: sugestão de próximo prompt é uma chamada extra por turno sobre o contexto em cache.
- CORREÇÃO: `promptSuggestionEnabled: false`.
- RISCO: baixo (perde só a conveniência).

## Verificado e NÃO culpado

| Item | Tamanho | Observação |
|---|---|---|
| `CLAUDE.md` | 132 linhas, 10 KB (~2,5k tok) | enxuto; ganhou 14 linhas apontando a política |
| Skills do projeto | 9 arquivos, ~11 KB | 4 aposentadas já com `disable-model-invocation` |
| Memória | ~7k tok | útil; o estado operacional temporário vai para o handoff, não para a memória |
| MCP carregados | ~15k tok | o restante (~73k) é deferred e só carrega por busca; Tool Search funcionando |
| MCP duplicados | `mcp__claude-in-chrome` e `mcp__Claude_Browser` fazem papéis parecidos | candidatos de baixo uso: Trello, computer-use, mcp-registry (conectores da conta, não do repositório). **Não removidos.** |
| `cleanupPeriodDays`, histórico em `~/.claude/projects` | GBs em disco | disco não é contexto; não apagado |

## Chaves de configuração confirmadas no binário 2.1.284

`modelSettings.<modelo>.effortLevel` (low/medium/high/xhigh), `modelSettings.<modelo>.maxEffortLevel`, `maxEffortLevel`,
`autoCompactWindow`, `bashOutputMaxChars` (padrão 30000, limites 4000 a 128000), `taskOutputMaxChars`,
`promptSuggestionEnabled`, `cleanupPeriodDays`, `advisorModel`; frontmatter de agent: `model`, `effort`, `maxTurns`,
`omitClaudeMd`, `tools`, `disallowedTools`, `background`, `isolation`.
Limite honesto: não existe como validar as chaves rodando uma sessão aninhada (`CLAUDE.md` proíbe). Validei sintaxe JSON e
nomes/tipos contra o esquema do binário; valores inválidos seriam ignorados pelo leitor, não quebrariam o app.

## Mudanças que NÃO foram feitas

- Não removi MCP, skills nem memória (sem evidência de ganho que compense a perda).
- Não mudei `maxEffortLevel` (impediria `xhigh` até quando pedido).
- Não compactei os documentos do produto (`docs/**`).
- Não apaguei histórico, sessões, worktrees nem branches.
- Não toquei em `settings.local.json` (16 KB de permissões acumuladas, ignorado pelo Git; não vai ao modelo).
