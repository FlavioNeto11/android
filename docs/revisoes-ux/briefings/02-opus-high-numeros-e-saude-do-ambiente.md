---
modelo_recomendado: Opus
forca_recomendada: high
---

> **Rodar em:** Opus · esforço high

# Tarefa 02: fonte única de números e semáforo de saúde do ambiente

## Problema
Os mesmos indicadores aparecem com valores diferentes:
- "5/15 online" e "1 de 14 selecionados" usam totais diferentes (15 vs 14).
- O aria-label do filtro diz "9 paradas" e o texto visível "10 paradas".
- Infraestrutura mostra "Vagas ocupadas: 5 de 4" (acima da capacidade) sem alerta.
- "bloqueadas" mostrou 15 na tela principal e 5 em outra renderização, e o rótulo não diz o que está bloqueado (personas? aparelhos?). Nas Personas contei 5 "Bloqueada pela plataforma".
- O indicador **"Ambiente OK"** fica verde mesmo com o servidor "Notebook da LAN" fora do ar e 6 aparelhos em estado "Desconhecido".
- As mesmas métricas (CPU, RAM, vagas, saúde, custo de IA) se repetem no cabeçalho, no popover "Ambiente OK", em Infraestrutura e em Diagnóstico.

## O que fazer
1. Mapear onde cada número é calculado. Criar **uma única camada de seleção/cálculo** (selectors ou store) que todas as telas consomem. Documentar a definição de cada métrica em comentário curto: total de aparelhos, online, paradas, desconhecidos, vagas (capacidade x ocupadas), selecionados, personas bloqueadas.
2. Definir regra de totais: se existir aparelho de servidor inalcançável, ele conta como "desconhecido", não como "parado", e aparece separado.
3. Rótulos explícitos e clicáveis: "15 personas bloqueadas" leva à lista de Personas já filtrada por bloqueadas. Idem para aparelhos desconhecidos.
4. Semáforo de saúde global com três estados: **OK**, **Atenção** (algum servidor fora do ar, aparelhos desconhecidos, ocupação acima da capacidade, saldo de IA baixo) e **Crítico**. O popover lista os motivos e cada motivo leva à tela relevante.
5. Ocupação acima da capacidade ("5 de 4") deve ficar destacada e explicar o porquê, ou ser corrigida se for erro de cálculo.
6. Definir a tela "dona" de cada tema e reduzir duplicação: cabeçalho mostra só resumo; Infraestrutura é a dona de servidores e vagas; Diagnóstico é o dono de custo de IA e ferramentas do SDK. Nas demais telas, link em vez de cópia.
7. Corrigir aria-labels para coincidirem com o texto visível.

## Critérios de aceite
- Uma métrica tem o mesmo valor em todas as telas (teste com servidor da LAN offline).
- "Ambiente" mostra Atenção com o motivo quando o Notebook da LAN está fora do ar.
- Contadores clicáveis levam à lista filtrada correspondente.
- Testes unitários dos cálculos de contagem cobrindo: servidor offline, ocupação acima da capacidade, seleção parcial.

## Entregáveis
Código, testes, tabela final "métrica -> definição -> onde aparece" e lista de divergências encontradas no backend.

## Contexto comum (leia antes de agir)

Projeto: **Central de Aparelhos**, portal web (SPA com rotas por hash, ex.: `#/painel`, `#/perfis`) que controla emuladores Android e personas de IA (WhatsApp/Instagram etc.) em um servidor central e workers na LAN. Idioma da UI: português do Brasil. Tema escuro. Servido em `http://127.0.0.1:8000/`.

Telas: Painel, Personas, Aplicativos, Execuções, Aprendizado, Infraestrutura, Configuração, Diagnóstico. Há ainda um drawer de foco por aparelho, um detalhe de persona com 11 abas e popovers no topo (modelo de IA, saúde do ambiente, sessão).

Origem: avaliação de UX/UI feita em 30/09/2026 testando 1920, 1440, 1280, 1024, 768 e 390 px. Meta: o portal precisa parecer uma plataforma profissional.

Regras de trabalho para qualquer tarefa:
- Você NÃO conhece a base de código ainda. Comece descobrindo stack, estrutura de pastas, roteamento, gerenciamento de estado e como o front consome o backend. Não presuma nomes de arquivos.
- Faça mudanças pequenas e revisáveis. Não refatore o que não faz parte da tarefa.
- Não execute ações com efeito real no ambiente (Executar tarefa, Resetar dados, Instalar app, apagar personas) durante testes. Use dados de exemplo ou modo somente leitura.
- Todo texto visível ao usuário fica em português do Brasil, sem jargão técnico cru.
- Ao terminar, teste visualmente em 1440, 1024 e 390 px e reporte o que foi verificado e o que não foi.
- Se algo do briefing contradisser o código, siga o código e registre a divergência no relatório final.
