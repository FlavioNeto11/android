# Prompt padrão do agente de carga (Sonnet, general-purpose, em segundo plano)

Substitua `<LOTES>` pelos caminhos dos lotes e `<MAPA>` pelo caminho do mapa de saída.

```
Você cria cartões no Trello do dono (workspace "Central de Aparelhos") a partir de lotes JSON já prontos. Trabalhe em português.

FERRAMENTAS: carregue os MCP do Trello com UMA única ToolSearch: query "+trello", max_results 15. Use só essas ferramentas e Read/Write.

LOTES (nesta ordem): <LOTES>
Cada item tem: id, nome, desc (markdown com os marcadores {{NAO_TECNICO}} e {{POR_QUE}}), lista (ARI), etiquetas (ARIs), status, quando, frente, fase.

ANTES de criar: trelloReadCard action=list_by_list na lista de destino e pule os ids cujo nome já comece com "<id> ·".

PARA CADA CARTÃO: (1) substitua {{NAO_TECNICO}} por 1–2 frases em português simples dizendo o que a funcionalidade faz para quem usa a Central de Aparelhos (plataforma que opera emuladores Android com IA; Instagram e Outlook como apps reais), sem jargão, sem nome de arquivo, sem sigla não explicada; substitua {{POR_QUE}} por 1 frase com o benefício ou o risco evitado; baseie-se SÓ no requisito, achados, prova e registro do CHANGELOG do próprio item; se for puramente interno, escreva "Ajuste interno de <área> que não muda o que a pessoa vê; garante <benefício>"; NUNCA invente funcionalidade; NUNCA inclua segredo, nome de pessoa real, e-mail, telefone, senha ou texto de comando. (2) trelloWriteCard create (listId=lista, name=nome, desc). (3) attach_label para cada etiqueta. (4) se status == "implemented": mark_done. (5) guarde id → {card, url}.

ERROS: tente 1 vez; depois pule e registre em "falhas". Sem ferramentas do Trello ou falha de autenticação: pare e devolva como bloqueio.

NO FINAL: escreva <MAPA> no formato {"criados": {"<id>": {"card": "...", "url": "..."}}, "falhas": [...], "pulados": [...]} e devolva: Feito / Evidências (contagens) / Falhas / Caminho do mapa. Não leia outros arquivos do repositório.
```
