# Plano de correção: portal Central de Aparelhos (rodada 2)

Cada arquivo `NN-*.md` é um briefing autocontido para **uma sessão separada** do Claude, com modelo e força indicados no topo. Entregue o arquivo inteiro (já inclui o contexto e as medições). Uma tarefa por sessão; revise o diff e o relatório antes de seguir.

## Ordem recomendada

| Ordem | Arquivo | Modelo | Força | Objetivo |
| --- | --- | --- | --- | --- |
| 10 | `10-sonnet-medium-cabecalho-mobile.md` | Sonnet | medium | Cabeçalho compacto no celular |
| 11 | `11-sonnet-medium-truncamento-e-tooltips.md` | Sonnet | medium | Texto truncado sem tooltip |
| 12 | `12-sonnet-high-detalhe-da-persona.md` | Sonnet | high | Detalhe da persona |
| 13 | `13-sonnet-medium-acessibilidade-residual.md` | Sonnet | medium | Acessibilidade residual |
| 14 | `14-sonnet-medium-execucao-e-pendencias.md` | Sonnet | medium | Execução e contagem de pendências |
| 15 | `15-opus-high-revalidacao-final.md` | Opus | high | Revalidação final |

## Dependências e coordenação

- **10, 11, 13 e 14** são independentes e podem rodar em paralelo em branches separados. Cuidado: 10 e 13 mexem no cabeçalho/sidebar (rode 10 antes de 13 se houver conflito).
- **12** (detalhe da persona) é a maior; rode isolada, em sessão própria, e revise com atenção às rotas antigas.
- **13** inclui a sidebar expandida por padrão; combine com 10 para o cabeçalho.
- **15** por último, sempre. Se achar regressões, abra tarefas pequenas e reexecute só o que falhou.

## Por que esses modelos

- Opus apenas na revalidação (15), onde o custo de deixar passar uma regressão é alto.
- Sonnet high na 12, que mexe em estrutura, rotas e compatibilidade de URL.
- Sonnet medium nas demais: mudanças de UI delimitadas com critério mensurável.

## Linha de base medida (01/10/2026)

- Sem rolagem horizontal em nenhuma largura. Contraste AA: 0 falhas.
- Alvos < 32 px: 5 (Painel) e 6 (Infraestrutura). Fonte 12 px no Diagnóstico.
- Cabeçalho em 390 px: ~245 px de altura (~29% da tela).
- Elipse sem tooltip: 15 casos na Infraestrutura em 390 px.
- Detalhe da persona: 3 repetições de identidade e 11 abas.

