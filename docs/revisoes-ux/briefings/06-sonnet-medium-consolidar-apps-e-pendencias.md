---
modelo_recomendado: Sonnet
forca_recomendada: medium
---

> **Rodar em:** Sonnet · esforço medium

# Tarefa 06: consolidar Aplicativos x Configuração e caixa única de pendências

## Problema
- **Gestão de apps duplicada**: a tela Aplicativos (abas Loja, Por app, Versões e instalação, Rede, Proxy (legado)) e Configuração > Aplicativos têm ambos "Novo aplicativo". O usuário não sabe onde cadastrar.
- "Proxy (legado)" aparece como aba de primeiro nível.
- Vocabulário de loja sem explicação: "promovida", "sem versão promovida", "fora do catálogo", "pendente(s)", "nenhuma versão promovida: ainda não há o que distribuir".
- **Aprovações espalhadas**: Aprendizado > Para aprovar, aba Aprovações dentro da persona, execuções bloqueadas aguardando usuário.
- Aprendizado usa jargão interno ("rebaixar") e um aviso longo em laranja.

## O que fazer
1. Definir **um único lugar** para criar/editar cadastro de app. Sugestão: Aplicativos (catálogo e distribuição) é o dono; Configuração > Aplicativos vira somente leitura com link "Gerenciar em Aplicativos". Remova o botão duplicado.
2. Rebaixar "Proxy (legado)": mover para dentro de Rede ou Configuração avançada, com selo "descontinuado" e texto explicando quando usar.
3. Adicionar **legenda/tooltip** para cada termo da loja e uma ação direta nos estados vazios (ex.: "Sem versão promovida" com botão **Promover versão**).
4. **Caixa de pendências única**: uma tela ou painel que lista tudo que depende do usuário (aprovações de aprendizado, aprovações de persona, execuções bloqueadas aguardando resposta), com origem, idade e ação primária. Contador no menu principal. As telas de origem passam a apontar para ela.
5. Reescrever textos de Aprendizado em linguagem simples; trocar "Rebaixar" por um termo claro ("Voltar a pedir aprovação") ou explicar em tooltip; encurtar o aviso laranja para 1 linha com "saiba mais".
6. Não remover funcionalidades; apenas reorganizar e redirecionar.

## Critérios de aceite
- Existe apenas um botão "Novo aplicativo".
- Todos os itens pendentes aparecem na caixa única; o contador do menu bate com a lista.
- Nenhum termo de loja sem explicação acessível.
- Links antigos (`#/configuracao` aba Aplicativos) continuam funcionando.

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
