---
modelo_recomendado: Sonnet
forca_recomendada: high
---

> **Rodar em:** Sonnet · esforço high

# Tarefa 12: detalhe da persona, mais enxuto e legível

## Problema (não tratado nas rodadas anteriores)
- Foto, nome e handle aparecem **3 vezes seguidas**: cabeçalho da página, linha de chips de apps e bloco "Identidade" da aba Visão geral.
- **11 abas** na mesma faixa: Visão geral, Persona, Contas e acesso, Imagens, Aparelhos, Memória, Interações, Habilidades, Aprovações, Execuções, Configurações. A faixa rola, mas não há agrupamento nem prioridade.
- O seletor de apps ("Todos / Instagram / Outlook / Conta em outro app") fica acima das abas sem explicar que filtra o conteúdo delas.
- Campos sensíveis (religião, política) aparecem por padrão na Visão geral.
- O identificador na URL é opaco (`#/personas/ig-CVG2z6c0Dv9dBrsY`).
- O selo de estado no canto superior direito ("Conectado", "Não verificada") não explica o que fazer.

## O que fazer
1. **Cabeçalho único** da persona: avatar, nome, handle, estado, aparelho vinculado e ações principais (ex.: Abrir no aparelho, Mais ações). Remover a repetição no bloco Identidade; ali ficam só os atributos (idade, gênero, cidade, profissão, e-mail).
2. **Agrupar as abas em 5 seções** com navegação lateral (ou abas principais + submenu):
   - Visão geral
   - Perfil (Persona, Imagens, Memória)
   - Contas e dispositivos (Contas e acesso, Aparelhos)
   - Atividade (Interações, Execuções, Aprovações)
   - Avançado (Habilidades, Configurações)
   Preservar todas as rotas atuais (`…/memoria`, etc.) e redirecionar sem quebrar links.
3. Tornar claro o escopo do seletor de apps ("Mostrando: Instagram") e o que ele filtra.
4. Campos sensíveis (religião, política) atrás de um bloco recolhível "Atributos de personalidade", fechado por padrão.
5. Tornar o selo de estado acionável: "Não verificada" leva ao fluxo de verificação; "Conectado" mostra desde quando.
6. **ID legível na URL**: aceitar e gerar `#/personas/lucas-almeida` (slug por nome, com sufixo curto em caso de colisão). Manter compatibilidade com o ID antigo.
7. Em 390 px: seções como lista suspensa ou rolagem horizontal com indicador, sem cortar rótulos.

## Critérios de aceite
- Avatar/nome/handle aparecem **uma vez** na tela de Visão geral.
- No máximo 5 itens de navegação visíveis no primeiro nível.
- Todas as rotas antigas continuam funcionando (teste: abrir `#/personas/<id-antigo>/memoria`).
- URL com slug legível abre a persona correta; colisão de nomes tratada.
- Sem perda de funcionalidade; teste em 390, 768, 1024 e 1440 px.

## Contexto comum (leia antes de agir)

Projeto: **Central de Aparelhos**, portal web (SPA com rotas por hash) que controla emuladores Android e personas de IA, em português do Brasil, tema escuro, servido em `http://127.0.0.1:8000/`.

Telas atuais: Painel, Personas, Aplicativos, Execuções, Pendências, Aprendizado, Infraestrutura, Configuração, Diagnóstico. Há drawer de foco por aparelho e detalhe de persona com abas.

**Estado atual (já entregue nas rodadas anteriores, não refazer):** sidebar de navegação (ícones por padrão, tooltip `title`; gaveta com hambúrguer abaixo de 768 px), rotas por objeto (`#/personas/<id>`, `…/memoria`, `#/painel?foco=…&visao=lista`, `?q=`), redirecionamento `#/perfis` -> `#/personas`, semáforo "Ambiente em atenção" com motivos e links, números consistentes entre telas, barra de seleção fixa, drawer sobreposto (`role=dialog`, `aria-modal`, Esc fecha), tela Pendências, busca e filtros em Personas e Execuções, textos técnicos traduzidos, fonte mínima 13 px, 0 falhas de contraste AA.

**Medições da reavaliação de 01/10/2026** (use como linha de base e como critério de aceite):
- Sem rolagem horizontal da página em 1920, 1280, 1024, 768 e 390 px, nas 9 telas.
- Alvos de clique abaixo de 32 px: 5 no Painel, 6 na Infraestrutura, 0 nas demais.
- Texto de 12 px no Diagnóstico (demais telas: mínimo 13 px).
- Em 390 px, o cabeçalho ocupa cerca de 29% da altura da tela (saúde + métricas + custos empilhados).
- Textos truncados com reticências e sem tooltip: Infraestrutura 15 casos em 390 px e 6 em 768 px; casos isolados em Aplicativos e Execuções.
- Detalhe da persona: identidade repetida 3 vezes e 11 abas.

Regras de trabalho para qualquer tarefa:
- Você NÃO conhece a base de código. Comece descobrindo stack, estrutura, roteamento, estado e componentes compartilhados. Não presuma nomes de arquivos.
- Mudanças pequenas e revisáveis; não refatore o que não faz parte da tarefa.
- Não execute ações com efeito real (Executar tarefa, Resetar dados, Instalar app, apagar personas) em testes. Use dados de exemplo ou somente leitura.
- Todo texto visível em português do Brasil, sem jargão técnico cru.
- Ao terminar, teste em 1440, 1024, 768 e 390 px, rode um auditor de acessibilidade (axe ou Lighthouse) nas telas afetadas e reporte o que foi e o que não foi verificado.
- Se algo do briefing contradisser o código, siga o código e registre a divergência no relatório final.
