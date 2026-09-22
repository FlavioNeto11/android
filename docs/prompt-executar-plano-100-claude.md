MODO DE EXECUÇÃO

Este texto descreve o pedido e os limites do trabalho. Quem executa é a sessão da IDE, pelo workflow
`.claude/workflows/plano-100.js`: um agente por grupo de arquivos, com o modelo e o esforço que vêm do pacote de
cada item. Ver `docs/claude-plano-100.md`. O antigo runner externo (`python scripts/claude-plan-100.py run`, com
`claude -p` em subprocesso) foi aposentado em 22/09 — não há executável `claude` nesta máquina, e o próprio runner
se recusava a rodar de dentro do agente.

Cada agente recebe um PACOTE auto-contido (`.claude/plano-100/pacotes/<id>.md`) com a linha do plano e os achados
na íntegra. Trabalhando um item, não abra `docs/plano-100.md` nem `docs/auditoria-2026-09-21/`: são 500 KB e o
pacote já traz o que interessa. Abra o código. Ao terminar o grupo, devolva o resultado estruturado e encerre; não
siga para outro grupo por conta própria. O registro fica em `.claude/plano-100/estado.json` e em
`docs/execucao-plano-100-runner.md`, gerados por `scripts/claude-plan-100.py aplicar` — não os edite à mão.
`docs/relatorio-validacao.md` continua guardando a prova dos aceites.

MODELO E ESFORÇO: não são escolha do agente nem do usuário a cada chamada. Saem de
`.claude/plano-100/pacotes/indice.json` — Opus para concorrência, protocolo, segurança, esquema de banco e itens de
porte G; Sonnet para o restante; Haiku para texto e configuração. Para mudar, ajuste `modelos` em
`.claude/plano-100.json`, não o prompt.

COMMIT: o agente não comita e não dá push. Quem olha o diff e commita é a sessão que orquestra.

Implemente o escopo integral de `docs/plano-100.md` no repositório `FlavioNeto11/android`, economizando tokens sem reduzir o escopo, a segurança ou os critérios de aceite. Quero implementação funcional, testes e documentação dos 67 itens: fases 0–10 e T.1–T.3. Trabalhe no código; não entregue apenas análise ou outro plano.

1. FONTES E ESTADO ATUAL

Leia as instruções aplicáveis do repositório (`CLAUDE.md`/`AGENTS.md`, se existirem) e o plano completo uma vez. Depois consulte somente a seção, os achados de `docs/auditoria-2026-09-21/` e os arquivos necessários ao bloco atual. A auditoria descreve um commit anterior: confira cada achado no código atual antes de corrigir. Se já estiver resolvido, registre a evidência e siga. Preserve alterações existentes do usuário e não exponha segredos.

2. EXECUÇÃO CONTÍNUA

Siga a ordem e as dependências do plano, incluindo sua prioridade inicial da fase 0 e a antecipação de 6.2/6.6 quando necessária ao caminho crítico. Incorpore T.1–T.3 durante as entregas. O escopo inclui preparar 5.1–5.3; a ativação de múltiplos backends depende da decisão prevista no plano. Não descarte fases por complexidade ou falta de infraestrutura.

Trabalhe em blocos pequenos e coerentes: conferir o problema, implementar, executar os testes pertinentes, corrigir, registrar e avançar. Reutilize contratos e componentes existentes. Entregue os caminhos completos entre backend, agente, banco e interface exigidos pelo item; não substitua comportamento por botão, flag ignorada, mock ou TODO. Faça commits locais por entrega validada, sem incluir dados reais, credenciais ou alterações alheias.

Não peça “posso continuar?” entre itens ou fases. Continue enquanto houver trabalho executável. Um bloqueio de ambiente impede a prova correspondente, mas não deve interromper implementação e testes independentes. Não execute operações que dependam de uma condição de segurança ainda não satisfeita.

3. ECONOMIA DE TOKENS

Não recrie a equipe de auditores, agentes céticos, revisões recursivas ou debates entre agentes: a auditoria já foi feita e está no apêndice. Dentro do seu grupo, trabalhe sozinho — a divisão do trabalho já foi feita pela fila, e subdividir de novo só multiplica leitura do mesmo arquivo. Não inicie sessões Claude externas.

Prefira navegação por símbolos, `rg`, leituras de trechos e diffs. Agrupe buscas relacionadas. Evite despejar arquivos grandes, árvores inteiras, dependências, APKs, bancos, capturas ou logs no contexto. Mantenha saídas extensas em arquivo local; mostre resumo, código de saída e erros relevantes sem mascarar falhas. Use ferramentas determinísticas para buscas, inventários e comparações.

Não reescreva o plano, não produza relatórios narrativos repetidos e não releia material inalterado sem necessidade. Resuma cada bloco em até oito linhas. Se uma tentativa falhar, use a evidência do erro antes de repetir. Troca de modelo ou criação de agentes não deve ocorrer silenciosamente para tentar resolver um bloqueio.

4. CONTINUIDADE ENTRE SESSÕES

Não duplique o registro gerado: `docs/execucao-plano-100-runner.md` e `.claude/plano-100/estado.json` saem de `scripts/claude-plan-100.py aplicar`. O que você devolve no resultado estruturado é o que entra ali — uma linha por ID, com estado, prova, evidência (arquivo:linha), arquivos alterados e o comando de teste que você de fato rodou, com o que ele respondeu. Separe implementação de validação em infraestrutura real.

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
