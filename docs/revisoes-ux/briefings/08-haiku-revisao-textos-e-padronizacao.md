---
modelo_recomendado: Haiku
forca_recomendada: medium (tarefa mecânica)
---

> **Rodar em:** Haiku · esforço medium (tarefa mecânica)

# Tarefa 08: varredura mecânica de textos e padronização

Use este briefing **depois** das tarefas 01 a 07, ou em paralelo se não houver conflito de arquivos. É trabalho repetitivo e bem delimitado.

## O que fazer
1. Varrer todos os textos visíveis da UI (labels, placeholders, tooltips, aria-labels, mensagens de erro e estados vazios) e listar em uma tabela: arquivo, linha, texto atual, problema, sugestão.
2. Procurar especificamente:
   - Termos inconsistentes: "perfil" x "persona", "aparelho" x "dispositivo" x "emulador", "execução" x "run", "bloqueio" x "bloqueada".
   - Identificadores técnicos crus visíveis ao usuário (nomes de pacote, ids de seletor, códigos de comando).
   - Textos em inglês misturados em UI em português.
   - Abreviações sem explicação e reticências que truncam frases importantes.
   - aria-labels que divergem do texto visível.
3. Aplicar **somente** as correções de baixo risco (troca de texto). Para qualquer mudança que envolva lógica, apenas liste.
4. Padronizar: datas em `dd/mm/aaaa`, hora 24 h, moeda `US$ 1.234,56` no formato pt-BR, separador decimal vírgula.

## Glossário-alvo
- **Persona** (não "perfil"), **Aparelho** (não "dispositivo"), **Execução**, **Aplicativo**, **Servidor**, **Pendência**.

## Critérios de aceite
- Tabela de achados entregue em `docs/revisao-textos.md`.
- Nenhuma alteração de lógica ou de estrutura nos arquivos.
- Diff contém só strings.

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
