MODO DE EXECUÇÃO

Para iniciar ou retomar todo o plano, use somente `python scripts/claude-plan-100.py` no terminal externo ao agente, conforme `docs/claude-plano-100.md`. O executor usa `.claude/plano-100.json`, decide o esforço por chamada, aplica escaladas e retoma a sessão. Não solicite parâmetros ao usuário. Não tente iniciá-lo de dentro do Claude nem remover a proteção de sessões aninhadas.

MODO RUNNER: nesta chamada, implemente somente os IDs recebidos do executor. As regras de continuidade abaixo se aplicam ao plano completo coordenado pelo runner; ao terminar o bloco, devolva o resultado estruturado solicitado e encerre a chamada. Não prossiga para outro bloco por conta própria. Leia este prompt uma vez por contexto e mantenha o checkpoint humano atualizado. O runner mantém seu estado local e gera `docs/execucao-plano-100-runner.md`; não edite esses arquivos manualmente. Eles registram o andamento, enquanto `docs/relatorio-validacao.md` guarda a prova dos aceites.

PERFIL AUTOMÁTICO: use Opus 5. O controlador começa os blocos comuns em medium e os críticos em xhigh. O usuário autorizou o controle automático: o runner pode escalar para xhigh/max por dificuldade técnica ou selecionar Ultracode quando reorganizar o trabalho ajudar. Não peça ao usuário para escolher parâmetros, não altere o mapa nem troque o modelo por conta própria. Respeite o esforço recebido na chamada atual. Ultracode utiliza raciocínio xhigh e workflows somente no bloco atual; aguarde todos os trabalhos terminarem antes de retornar o resultado.

DIAGNÓSTICO: no resultado estruturado, use next_action=continue para trabalho comum, já concluído ou após resolver o obstáculo; reasoning para um problema que precise de raciocínio mais profundo; workflow somente com uma necessidade concreta de reorganização ou de partes independentes. Em reason, registre brevemente evidência, tentativa realizada e próxima ação. Dificuldade técnica não resolvida permanece partial. Falta de credencial, permissão, cota, infraestrutura ou decisão é blocked, nunca um motivo para aumentar esforço. O runner decide, registra e limita as próximas tentativas; não execute outro bloco por conta própria.

MODO INTERATIVO: ao usar `/plano-100-medium`, `/plano-100-xhigh` ou `/plano-100-max`, limite a implementação aos IDs passados à skill. O nome antigo `/plano-100-high` aponta para xhigh. Confira o esforço no Claude Code e não encadeie skills supondo que isso troca o parâmetro. Para execução integral com troca automática, use o runner. `/plano-100-ultracode` apenas prepara o comando externo.

Implemente o escopo integral de `docs/plano-100.md` no repositório `FlavioNeto11/android`, economizando tokens sem reduzir o escopo, a segurança ou os critérios de aceite. Quero implementação funcional, testes e documentação dos 67 itens: fases 0–10 e T.1–T.3. Trabalhe no código; não entregue apenas análise ou outro plano.

1. FONTES E ESTADO ATUAL

Leia as instruções aplicáveis do repositório (`CLAUDE.md`/`AGENTS.md`, se existirem) e o plano completo uma vez. Depois consulte somente a seção, os achados de `docs/auditoria-2026-09-21/` e os arquivos necessários ao bloco atual. A auditoria descreve um commit anterior: confira cada achado no código atual antes de corrigir. Se já estiver resolvido, registre a evidência e siga. Preserve alterações existentes do usuário e não exponha segredos.

2. EXECUÇÃO CONTÍNUA

Siga a ordem e as dependências do plano, incluindo sua prioridade inicial da fase 0 e a antecipação de 6.2/6.6 quando necessária ao caminho crítico. Incorpore T.1–T.3 durante as entregas. O escopo inclui preparar 5.1–5.3; a ativação de múltiplos backends depende da decisão prevista no plano. Não descarte fases por complexidade ou falta de infraestrutura.

Trabalhe em blocos pequenos e coerentes: conferir o problema, implementar, executar os testes pertinentes, corrigir, registrar e avançar. Reutilize contratos e componentes existentes. Entregue os caminhos completos entre backend, agente, banco e interface exigidos pelo item; não substitua comportamento por botão, flag ignorada, mock ou TODO. Faça commits locais por entrega validada, sem incluir dados reais, credenciais ou alterações alheias.

Não peça “posso continuar?” entre itens ou fases. Continue enquanto houver trabalho executável. Um bloqueio de ambiente impede a prova correspondente, mas não deve interromper implementação e testes independentes. Não execute operações que dependam de uma condição de segurança ainda não satisfeita.

3. ECONOMIA DE TOKENS

Nos modos medium, high, xhigh e max, use um único agente executor. A única exceção é uma chamada iniciada pelo controlador em Ultracode: nela, use a orquestração nativa com Opus 5 e mantenha eventual delegação limitada aos IDs recebidos. Não recrie a equipe de auditores, agentes céticos, revisões recursivas ou debates entre agentes. Não inicie outras sessões externas de Claude para delegar trabalho. A próxima chamada aplica sua própria regra de execução; uma escalada anterior não autoriza delegação nos modos comuns.

Prefira navegação por símbolos, `rg`, leituras de trechos e diffs. Agrupe buscas relacionadas. Evite despejar arquivos grandes, árvores inteiras, dependências, APKs, bancos, capturas ou logs no contexto. Mantenha saídas extensas em arquivo local; mostre resumo, código de saída e erros relevantes sem mascarar falhas. Use ferramentas determinísticas para buscas, inventários e comparações.

Não reescreva o plano, não produza relatórios narrativos repetidos e não releia material inalterado sem necessidade. Resuma cada bloco em até oito linhas. Se uma tentativa falhar, use a evidência do erro antes de repetir. Troca de modelo ou criação de agentes não deve ocorrer silenciosamente para tentar resolver um bloqueio.

4. CONTINUIDADE ENTRE SESSÕES

Crie ou atualize `docs/execucao-plano-100.md` como checkpoint humano compacto. No modo interativo, mantenha uma linha por item trabalhado com estado, evidência, commit e bloqueio. No modo runner, evite duplicar sua tabela gerada: registre decisões e a retomada, referenciando `docs/execucao-plano-100-runner.md`. Separe claramente implementação de validação em infraestrutura real.

Mantenha também um resumo de retomada de até 30 linhas: objetivo, regras essenciais deste pedido, decisões confirmadas e pendentes, branch/commit, alterações ainda não commitadas, bloco atual, falhas conhecidas e próxima ação exata. Atualize ao concluir cada bloco e antes de compactação ou interrupção. Em uma retomada, leia esse registro e apenas o trecho necessário do plano; confira o diff e continue do ponto salvo, sem refazer a auditoria. Não espere concluir tudo em uma única janela de contexto.

5. DECISÕES E AMBIENTE REAL

Respeite as nove decisões reservadas ao usuário na seção 1 do plano. Reutilize respostas e autorizações já registradas; não invente confirmação de rotação de chave, janela de produção, orçamento, capacidade ou modelo de acesso. Enquanto faltar uma decisão, prepare o código que independe dela, testes isolados, configurações reversíveis e procedimento de ativação. Registre a escolha que falta e continue nos demais itens.

Chamadas pagas da aplicação e avaliações da fase 7.4 continuam sujeitas à confirmação de chave e orçamento; isso não impede esta sessão de desenvolvimento. Use fixtures e provedores simulados nos testes automatizados, identificando-os corretamente. Implemente fallback pago explícito, configurável e desativado por padrão; não altere silenciosamente a configuração da produção.

Prepare e ensaie backup, restauração e migrações em cópia isolada antes de qualquer atualização real. Não reescreva migrações já aplicadas para corrigir divergências. Reinícios de produção, mudanças de relógio/WSL, testes destrutivos e efeitos externos exigem a autorização correspondente. Antes de solicitá-la, deixe a ação concreta, seu impacto, a validação e o rollback prontos. Preserve as exclusões da seção 7, incluindo desafios manuais, origem autorizada de APKs e proteção de segredos.

6. TESTES E ACEITES

Descubra os comandos reais nas configurações do projeto. Durante cada bloco, rode testes direcionados aos comportamentos alterados; amplie para integração e suíte completa nos marcos necessários e no fechamento. Cumpra a cobertura exigida em T.2 e os gates de pytest/SQLite, PostgreSQL, TypeScript e Vitest previstos no plano. Nunca economize removendo testes, enfraquecendo assertivas ou tratando indisponibilidade de infraestrutura como aprovação.

Garanta regressões relevantes para paridade local/remota, exclusividade e fencing, perda de conexão, resultado tardio, cancelamento, idempotência, migrações e autenticação. Quando autorizado e disponível, execute as provas reais da fase, com dispositivos/contas de teste e rastreabilidade.

Atualize `docs/relatorio-validacao.md`, incluindo a tabela dos nove aceites, distinguindo real, simulado e não executado. Prova real exige data, máquina, commit e identificadores de execução/comando ou evidência equivalente. Sem a prova exigida, registre “implementado; validação real pendente”, sem fechar a fase ou declarar 100%.

7. CONCLUSÃO

Continue até concluir todo o trabalho executável e identificar precisamente o restante. Termine com um resumo curto: itens concluídos, testes executados, provas reais, bloqueios e ações necessárias para encerrá-los. Confira a cobertura dos 67 IDs, das nove seções e dos nove aceites. Se houver limite de sessão, deixe o checkpoint atualizado e a instrução exata de retomada.

Comece agora conferindo o estado do repositório, lendo o plano e executando o primeiro bloco pendente.
