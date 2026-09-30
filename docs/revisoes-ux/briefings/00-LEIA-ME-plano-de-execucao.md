# Plano de execução: melhorias do portal Central de Aparelhos

Cada arquivo `NN-*.md` é um briefing autocontido para rodar em **uma sessão separada** do Claude, com o modelo e a força indicados no topo. Entregue ao Claude o arquivo inteiro (ele já inclui o contexto). Faça uma tarefa por sessão e revise o resultado antes de passar à próxima.

## Ordem recomendada

| Ordem | Arquivo | Modelo | Força | Objetivo |
| --- | --- | --- | --- | --- |
| 01 | `01-opus-high-rotas-e-menu.md` | Opus | high (use xhigh só para planejar, se a base for grande) | Menu e rotas por objeto |
| 02 | `02-opus-high-numeros-e-saude-do-ambiente.md` | Opus | high | Números e saúde do ambiente |
| 03 | `03-sonnet-high-barra-de-acao-e-drawer.md` | Sonnet | high | Barra de ação e drawer |
| 04 | `04-sonnet-medium-textos-tecnicos-e-cards.md` | Sonnet | medium | Textos técnicos e cards |
| 05 | `05-opus-high-busca-filtros-e-tabela.md` | Opus | high | Busca, filtros e tabela |
| 06 | `06-sonnet-medium-consolidar-apps-e-pendencias.md` | Sonnet | medium | Consolidar apps e pendências |
| 07 | `07-sonnet-medium-tipografia-contraste-acessibilidade.md` | Sonnet | medium | Tipografia e acessibilidade |
| 08 | `08-haiku-revisao-textos-e-padronizacao.md` | Haiku | medium (tarefa mecânica) | Varredura de textos (mecânica) |
| 09 | `09-opus-high-revisao-final-e-testes-visuais.md` | Opus | high | Revisão final e testes |

## Dependências

- 01 e 02 primeiro: destravam as demais (rotas e fonte única de números).
- 03 a 07 podem rodar em paralelo em branches separados, desde que não editem os mesmos componentes ao mesmo tempo. Se houver conflito, rode 03 antes de 04, e 05 antes de 06.
- 08 só depois de 04 e 06 (para não reescrever texto que ainda vai mudar).
- 09 por último, sempre.

## Como usar

1. Abra uma sessão com o modelo e a força indicados.
2. Cole o conteúdo do arquivo (ou anexe-o) e peça: "Execute este briefing."
3. Revise o diff e o relatório final da sessão. Só então siga para o próximo arquivo.
4. Guarde os relatórios em `docs/` para a revisão final (09).

## Observação

Os briefings partem de uma avaliação visual do portal e não conhecem o código. Por isso cada um manda primeiro descobrir a stack e não presumir nomes de arquivos.
