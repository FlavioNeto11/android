# Auditoria de 21/09/2026 — os 181 achados, com evidência

Apêndice de [`../plano-100.md`](../plano-100.md). Os achados #1–#170 foram levantados por dez auditores lendo o
código e o estado vivo (somente leitura), e cada um passou por um **cético** que tentou refutá-lo: 95 saíram
`confirmado` e 75 saíram `parcial` — verdadeiros, mas com evidência, severidade ou tipo corrigidos; a versão
corrigida é a que está aqui. O cético corrigiu em vez de descartar, então **isto não é um selo de qualidade**:
o filtro de verdade é a consolidação feita no plano, e os pontos que contradizem o que já estava entregue
(#117, #154, #171 e o grupo do modo manutenção) foram conferidos à mão no código antes de entrar nele.

Os 11 últimos (#171–#181) vieram do crítico de completude e **não passaram por um cético**.

Há sobreposição de propósito: o mesmo defeito visto por duas dimensões aparece duas vezes, com evidências
diferentes. O plano consolida; aqui fica o material bruto para quem for executar cada item.

| Tema | Arquivo | Achados | Crítico | Alto | Médio | Baixo |
|---|---|---|---|---|---|---|
| A | [O comando ainda não é confiável de ponta a ponta](A-comando-confiavel.md) | 31 | 1 | 14 | 16 | 0 |
| B | [Um contrato só: o executor local não é um worker](B-contrato-unico.md) | 8 | 1 | 4 | 2 | 1 |
| C | [Estado verdadeiro do aparelho e visão de infraestrutura](C-estado-verdadeiro.md) | 16 | 0 | 5 | 10 | 1 |
| D | [Escalonamento e localidade de perfil](D-escalonamento-localidade.md) | 19 | 0 | 10 | 9 | 0 |
| E | [Vários backends, banco e infraestrutura de dados](E-multi-backend.md) | 17 | 0 | 1 | 13 | 3 |
| F | [Segurança](F-seguranca.md) | 19 | 0 | 3 | 9 | 7 |
| G | [Operação: o que sobe sozinho, backup, capacidade](G-operacao.md) | 15 | 0 | 5 | 6 | 4 |
| H | [Aplicativos, loja e Instagram fora do núcleo](H-apps-e-loja.md) | 15 | 0 | 4 | 9 | 2 |
| I | [Hub de IA](I-ia.md) | 12 | 0 | 2 | 5 | 5 |
| J | [Qualidade da operação do Instagram](J-instagram.md) | 9 | 0 | 1 | 6 | 2 |
| K | [Prova com infraestrutura real, testes e documentação](K-prova-testes-docs.md) | 20 | 0 | 6 | 10 | 4 |
