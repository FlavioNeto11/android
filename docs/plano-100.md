# O que falta para a aplicação cumprir o que se propõe

Auditoria de 21/09/2026 sobre o commit `f1e61b3`, e o plano que sai dela.

**Como foi levantado.** Dez auditores leram o código e o estado vivo do parque (somente leitura: nada foi alterado,
nenhum aparelho ligado ou desligado), um por dimensão — paridade remota, confiabilidade, escalonamento, frontend,
apps e loja, IA, operação do Instagram, segurança, ambiente, qualidade. Cada achado passou por um cético que tentou
refutá-lo no código; um crítico de completude procurou o que ninguém tinha olhado. Saíram **181 achados**, com
sobreposição entre dimensões, que aqui viram **67 itens de trabalho** em 11 fases e uma frente transversal. O material bruto, com
`arquivo:linha` e o trabalho concreto de cada ponto, está em [`auditoria-2026-09-21/`](auditoria-2026-09-21/README.md);
os `#n` deste documento apontam para lá.

"100%" aqui tem definição: as **9 seções** e os **9 aceites** do pedido de execução distribuída, mais o que a
aplicação já prometia antes dele (IA que opera e comprova, Instagram com persona e aprovação, custo sob controle).

> **Nota de 24/09/2026.** O plano nasceu com 67 itens; pedidos do dono acrescentaram 10.5, 11.1–11.10, 12.1–12.3 e
> 13.1–13.3, e hoje são **88 IDs** (todos sob a Fase 11 na tabela). Este arquivo é o **requisito** de cada item. O estado
> vigente está em [`execucao-plano-100-runner.md`](execucao-plano-100-runner.md) (gerado do livro-razão), a prova em
> [`relatorio-validacao.md`](relatorio-validacao.md) e o que falta, por tipo de pendência, em [`roadmap.md`](roadmap.md).

---

## 1. Decisões que são suas

Nada abaixo eu resolvo sozinho. Estão no topo porque várias fases dependem delas.

1. **Janela para reiniciar a produção.** O backend que serve o parque é **anterior** às duas últimas entregas: as
   migrações 016 e 017 nunca rodaram sobre os dados reais, e a posse de etapa e a autenticação só existiram em teste
   (#140). O próximo reinício — planejado ou não — aplica tudo isso sem cópia prévia do banco. Fase 0.1.
2. **Chave do provedor de IA.** Há uma regra combinada (nada pago antes de você confirmar a rotação da chave) e
   nenhum registro da confirmação, com uso pago recente (#130). Confirme no console e eu retiro a trava.
3. **Fallback pago de recusa: ligado ou desligado por padrão?** Hoje está ligado, inclusive em produção, sem aviso na
   execução (#92). Seu pedido diz "sem fallback pago silencioso". Recomendo **desligar** até a fase 7.2 torná-lo
   visível e configurável por função.
4. **VM da loja num worker remoto.** Ou (a) área de trabalho remota até o worker, fora da plataforma; ou (b) liberar
   texto na loja por um canal com o mesmo regime de segredo do cofre (#74). Recomendo (a) agora; (b) só se loja remota
   virar necessidade real.
5. **Alvo de capacidade.** Quantos aparelhos locais + quantos remotos ao mesmo tempo. Hoje o central comporta 1 a 3
   (o WSL pode tomar 56 dos 63,5 GB), o worker 6, e o código tem teto fixo de 10 com parque de 15 (#146, #48).
6. **Hora certa nas duas máquinas.** Central e worker estavam ~97 s fora um do outro (#142), e o lease de posse
   compara esses relógios. É configuração de sistema: eu passo os comandos do `w32time`, você executa. O mesmo vale
   para o teto de memória do WSL (`.wslconfig`).
7. **Gasto com a bateria de avaliação.** Os modelos baratos por função estão em produção sem a comparação contra a
   linha de base que o próprio projeto pôs como condição (#98). Rodá-la custa crédito.
8. **Modelo de acesso de pessoas.** Um operador com senha única, ou usuários nomeados com auditoria (#119).
9. **Vai existir um segundo backend de verdade?** Se não, as partes 5.1–5.3 podem esperar e a documentação passa a
   dizer "um backend só" — é trabalho grande que só se paga com a topologia em uso.

---

## 2. O retrato: os 9 aceites

Seu pedido termina com "diferencie o que foi testado com infraestrutura real do que foi validado com simulação".
Esse registro não existia (#160). Este é o primeiro.

| # | Aceite | Provado com infraestrutura real | Só simulado / não feito | Fecha na fase |
|---|---|---|---|---|
| 1 | Mesma operação em local e remoto | `stop` remoto (android-15, 21/09) | `start` remoto terminou `uncertain`; hibernate, wake, restart, reset e create **nunca rodaram** no worker (#3); e o `start` **local** responde `succeeded` em 2 ms, antes de o aparelho ligar (#155) | 1 e 2 |
| 2 | Workflow completo remoto acompanhado pelo painel | 19/09: 6 de 6 remotos, fluxo de **leitura**, quase todo por receita | nenhuma etapa com efeito externo, nenhuma verificação por modelo, nenhuma imagem; anterior ao worker e aos comandos (#175). **Hoje 3 dos 4 remotos ligados não abrem sessão de automação** (#1) | 0.7, 3.1, T.1 |
| 3 | Controle manual de tela remoto | — | nunca exercitado | 3.6, T.1 |
| 4 | Instalar e abrir app diferente do Instagram | QA Messenger em aparelho local | em remoto, por release: não feito; o conjunto real do Instagram nunca foi instalado num remoto (#86) | 6.2, 6.6 |
| 5 | Distribuição entre **dois** workers | — | **impossível hoje**: o central não é um worker (`LocalWorker` só existia numa docstring, #154) e há um notebook só | 2.1, 4.2 |
| 6 | Queda e reconexão de worker com reconciliação | o túnel reconecta em 5 s | **não atendido**: a queda do socket **cancela** o trabalho no agente e o resultado se perde (#20) | 1.4 |
| 7 | Reinício de API/scheduler sem perder tarefa | reinícios reais, antes de comandos e workers | com fila carregada e as tabelas novas: só `Harness.crash()` | 0.1, T.1 |
| 8 | Cancelamento, duplicada e incerto sem repetir efeito | idempotência (teste e real) | cancelamento **não existe** de ponta a ponta (#23); `uncertain` é beco sem saída (#21); timeout de adb vira `failed` (#174) | 1.4–1.7 |
| 9 | Bloqueio de execução concorrente no aparelho | etapa × etapa, num processo | comando × comando e comando × IA em remoto: **nada serializa** (#4); entre backends: nada (#28) | 1.3, 5.1 |

### E as 9 seções

| Seção do pedido | Estado | Onde fecha |
|---|---|---|
| 1. Comando remoto obedecido e rastreado | a entidade existe e o `stop` remoto é honesto; o resto do caminho não (tema A) | fases 0, 1 |
| 2. Um contrato, capacidades, recusa antes de agendar | recusa antes de agendar: feito; contrato único: **não** (tema B) | fase 2 |
| 3. Arquitetura distribuída, PostgreSQL, fila, S3 | PostgreSQL roda nos testes; produção segue SQLite; fila e S3 não começados (tema E) | fase 5 |
| 4. Inscrição, heartbeat, lease, fencing, ACK, cancelamento, manutenção | inscrição e heartbeat: feitos; ACK não persiste, cerca não é imposta, cancelamento não existe, **manutenção não suspende nada** | fases 0, 1 |
| 5. Escalonamento por recursos e localidade de perfil | **não existe**: o objetivo nasce pregado num `instance_id` escolhido à mão (tema D) | fase 4 |
| 6. Visão de infraestrutura | existe; falta a volta (da tarefa para onde rodou), logs, evidências, filas, e o estado do aparelho remoto engana (tema C) | fases 3, 4 |
| 7. Catálogo de apps, loja, Instagram fora do núcleo | não começado (E10, E11) (tema H) | fase 6 |
| 8. IA desacoplada, sem fallback pago silencioso | não começado (E12); e o fallback pago está ligado e invisível (tema I) | fases 0, 7 |
| 9. Implementação com prova real | ver a tabela acima | todas, registradas em T.1 |

---

## 3. O que a auditoria desmentiu do que eu já tinha entregue

Melhor dito aqui do que descoberto por você.

- **"A interface para de mentir" (E1)** vale para o `stop` remoto. No caminho **local**, `start`, `restart`, `reset` e
  `create` respondem `succeeded` antes de o aparelho ligar — se a guarda de RAM recusar o boot, o toast verde já saiu
  (#155). E para comando remoto o ACK nunca é gravado e `running` é marcado **antes** de o comando ser enviado (#24).
- **Modo manutenção (E5)** é uma bandeira que nenhum caminho de produção consulta: o botão existe, a atribuição
  continua (#9, #22, #42, #156).
- **`LocalWorker` (E4)** só existia numa docstring que afirmava o contrário (#154). Corrigida junto com este plano.
- **A autenticação de hoje tem um desvio**: a isenção de loopback é decidida pelo cabeçalho `Host`, não pelo endereço
  de quem chama — com `server.host` em endereço de rede, `Host: localhost` passa sem token (#117). **Não é explorável
  na configuração atual** (a produção escuta só em 127.0.0.1), mas anula o recurso no dia em que for usado. E o túnel
  reverso, que eu recomendei como "menor superfície", entrega a API REST inteira sem credencial a qualquer processo
  do notebook do worker (#116) — essa é a topologia de produção hoje.
- **"Dois backends não se atropelam"** era exagero meu: só a **etapa** tem dono (#171). Na prova em processo real eu
  vi o segundo backend bloquear o objetivo que não era dele e li como esperado. Docstring e `docs/banco.md`
  corrigidos junto com este plano.
- **PostgreSQL**: 43 testes continuam presos ao SQLite (#162), e ao portar as migrações eu **reescrevi arquivos já
  aplicados** — o esquema de produção da 008 já difere do que o mesmo arquivo gera num banco novo, e nada detecta (#169).

---

## 4. O que está errado hoje, por tema

| Tema | Achados | Crítico | Alto | Em uma frase |
|---|---|---|---|---|
| [A — Comando confiável](auditoria-2026-09-21/A-comando-confiavel.md) | 31 | 1 | 14 | A queda do socket cancela o trabalho; ACK, cancelamento, saída do `uncertain` e exclusividade por aparelho não existem; o caminho local mente no `start`; instalar/canário/loja e sessão de perfil ficaram fora do contrato |
| [B — Contrato único](auditoria-2026-09-21/B-contrato-unico.md) | 8 | 1 | 4 | O executor local não é um worker; capacidades são só verbos; Appium por worker é só declaração |
| [C — Estado verdadeiro](auditoria-2026-09-21/C-estado-verdadeiro.md) | 16 | 0 | 5 | Aparelho com o Android morto por dentro segue `online`; estado de app e sessão presos ao id lógico viram dado velho; a saúde fica congelada no painel |
| [D — Escalonamento](auditoria-2026-09-21/D-escalonamento-localidade.md) | 19 | 0 | 10 | Não há escalonamento; slots e recursos do worker são só exibição; o rodízio não alcança remotos; perfil sem localidade (E9) |
| [E — Vários backends e dados](auditoria-2026-09-21/E-multi-backend.md) | 17 | 0 | 1 | Só a etapa tem dono; sem outbox/fila (E7); evidências só em disco (E8); PostgreSQL sem reconexão e sem ferramenta de migração de dados |
| [F — Segurança](auditoria-2026-09-21/F-seguranca.md) | 19 | 0 | 3 | Desvio do token por `Host`; túnel reverso sem credencial; sem login nem TLS; credencial de worker não revogável |
| [G — Operação](auditoria-2026-09-21/G-operacao.md) | 15 | 0 | 5 | Depois de um reboot só o túnel volta; não há backup; nada supervisiona backend nem agente |
| [H — Apps e loja](auditoria-2026-09-21/H-apps-e-loja.md) | 15 | 0 | 4 | Catálogo visual não existe (E11); Instagram segue no núcleo (E10); a VM da loja não pode ser remota |
| [I — IA](auditoria-2026-09-21/I-ia.md) | 12 | 0 | 2 | Conta sem crédito queima tentativas sem disjuntor; hub (E12) não existe; fallback pago ligado e invisível |
| [J — Instagram](auditoria-2026-09-21/J-instagram.md) | 9 | 0 | 1 | DM com mais de 80 caracteres vira `incerto`; memória social nunca aprendeu nada |
| [K — Prova, testes, docs](auditoria-2026-09-21/K-prova-testes-docs.md) | 20 | 0 | 6 | Sem registro dos aceites; agente sem nenhum teste; sem CI; README anterior a tudo isso |

Três pontos que mudam o retrato **hoje**, antes de qualquer fase:

- **Três dos quatro aparelhos remotos ligados estão inoperantes e o painel diz `online`** (#1). Em android-09 e
  android-10 o `system_server` do Android morreu (serviços `activity`/`package` inexistentes, no ar há 1–2 dias, carga
  10–14); android-12 está sob carga 9–18 e o Appium Settings não sobe. O Appium falha a cada 90 s há horas, sem
  `attention`, sem escalar. Só android-15 aceita IA. O mesmo defeito vale para emulador local (android-03 acumulou 235
  falhas iguais na madrugada).
- **Sobre as personas** (sua reclamação de que os perfis não usavam a personalidade): a persona **chega** ao prompt e
  governa o texto — está medido em aparelho. O que sobra é qualidade (#107): o planejador injeta tom no briefing e
  concorre com o da persona; o bloco `<tela>` transforma um cumprimento em mensagem longa; e as 8 personas reais têm
  8 dos 15 campos de voz vazios, então o modelo tem pouco com que diferenciar oito vozes. E a mensagem mais longa é
  justamente o que dispara o defeito do verificador (#102).
- **O próximo reinício da produção é um deploy não ensaiado** (#140), sem backup (#145).

---

## 5. O plano

Tamanhos: **P** até meio dia de trabalho, **M** um a dois dias, **G** três a cinco. Cada fase termina com uma prova
em infraestrutura real registrada em `docs/relatorio-validacao.md` (item T.1) — sem isso a fase não fecha.

**Caminho crítico para os 9 aceites:** fases 0 → 1 → 2 → 3 → 4, mais 6.2 e 6.6. As fases 5, 7 e 9 completam as
seções 3, 8 e a segurança; 8 e 10 podem ser intercaladas a qualquer momento depois da 0.

### Fase 0 — Estancar e proteger · 10 itens (19 achados: 14 P, 5 M)

Barato, e tudo aqui ou protege dado, ou fecha buraco, ou destrava o uso de hoje.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 0.1 | **Backup e deploy.** `scripts/backup.ps1` (cópia consistente com o backend no ar; o `.sqlite3` sozinho perde o WAL), exportação/recadastro documentado da chave do cofre (presa por DPAPI a esta máquina), procedimento parar→copiar→migrar→conferir. Ensaiar 016/017 numa cópia do banco real e **então reiniciar a produção no código atual** | #145 #36 #140 | M |
| 0.2 | **Fechar o desvio do token.** `avaliar` recebe o endereço do par e só isenta quando par **e** `Host` são loopback; `test`/`testserver` saem do conjunto de produção; uvicorn sem `proxy_headers`; teste com cliente de outro IP mandando `Host: localhost` → 401, em HTTP e WebSocket | #117 #141 | P |
| 0.3 | **Túnel reverso com credencial.** Listener dedicado que serve só `/api/worker/ws`, sem isenção de loopback; o `-R` passa a apontar para ele; `shutdown` exige segredo local; o WebSocket do worker limita e registra tentativa recusada | #116 #124 | M |
| 0.4 | **Manutenção que suspende.** `aceita_trabalho()` consultado no despacho de comando e no scheduler | #9 #22 #42 #156 | P |
| 0.5 | **DM longa comprovada.** Conferência determinística pela árvore local (texto presente + campo vazio + sem marca de falha) antes de chamar o modelo; não cortar em 80 o elemento que casa com o conteúdo; regressão com 81 e 300 caracteres | #102 | P |
| 0.6 | **Disjuntor de conta de IA.** Classificar cobrança/credencial; à primeira ocorrência represar a etapa sem gastar tentativa, pausar a execução, e acusar no `/api/health` e na aba IA | #90 | M |
| 0.7 | **Recuperar o parque remoto.** Reiniciar android-09 e android-10 pelo worker; investigar a carga de android-12; hora certa nas duas máquinas (decisão 6) | #1 #142 | P |
| 0.8 | **CI.** pytest (SQLite) + `tsc` + vitest a cada push; corrida em PostgreSQL agendada; a suíte apaga os schemas que cria (hoje 1.608 schemas e 1,9 GB no banco de teste) | #161 #163 | M |
| 0.9 | **Remover e reinscrever worker.** A documentação manda "remover o worker no painel" e não existe rota, tela nem método | #168 #150 | P |
| 0.10 | **Suas decisões 2 e 3** (chave, fallback) | #130 #92 | — |

**Fecha quando:** a produção roda o commit atual com backup feito; `Host: localhost` vindo de outro IP dá 401; REST
pelo túnel sem token é recusado; worker em manutenção não recebe comando; uma DM de 300 caracteres fecha `succeeded`
num aparelho real.

### Fase 1 — O comando diz a verdade nos dois caminhos · 9 itens (28 achados: 8 P, 19 M, 1 G)

É o coração do seu pedido. A ordem importa: primeiro a máquina de estados fica verdadeira e o aparelho ganha trava —
a trava é pré-requisito do diário de reconexão.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 1.1 | **Marcas de tempo verdadeiras.** `dispatched` só depois do envio; `acked` persistido quando o worker confirma; `running` no primeiro progresso; `worker_id` gravado no despacho. Numa queda passa a ser possível separar "falhou com certeza" de "incerto" | #24 #7 #148 | M |
| 1.2 | **Caminho local honesto.** `start`/`restart`/`reset` esperam o boot: online → `succeeded`, recusa da guarda de RAM → `failed` com o motivo, prazo → `uncertain`; `create` propaga o erro do AVD; regra única para hibernar sem snapshot | #155 | M |
| 1.3 | **Um aparelho, uma operação.** Pré-voo recusa (409 `device_busy`) quando há comando aberto; trava por aparelho no agente; o agente guarda a maior cerca por aparelho e recusa a menor; cerca obrigatória no resultado; índice único parcial em etapas ativas por aparelho; socket antigo que termina não derruba o link novo; botão bloqueado enquanto há comando aberto, lote com chave de idempotência | #4 #28 #31 #57 #25 #125 | M |
| 1.4 | **Queda de conexão não é falha.** O agente **não cancela** ao perder o socket; diário local de resultados até o central confirmar; `inflight` no `Hello` e `result_ack`; o central localiza o comando no **banco** e aceita o resultado tardio pela transição `uncertain → succeeded/failed`, que já existe | #20 #6 | G |
| 1.5 | **`uncertain` com saída.** Reconciliação automática por sonda do estado real (o aparelho está ligado? então o `start` deu certo) e ação humana de confirmar/descartar; o agente deduplica por `command_id` | #21 #149 #5 | M |
| 1.6 | **Cancelamento de ponta a ponta.** Rota, registro, agente, interface — hoje existe no protocolo e na máquina de estados, e nada o dispara | #23 #157 | M |
| 1.7 | **O remoto produz os mesmos efeitos no central.** `desired_state`; `hibernated` e snapshot (hoje o painel oferece "Iniciar" a frio depois de hibernar); `reset` invalida sessão, estado de app e evidência de conta; adoção imediata no `start`; `wake` recusa sem snapshot e só diz `from_snapshot` quando comprova; timeout de adb → `uncertain`; `open_app` confere que abriu; o prazo do `start` não conta a fila de boot | #2 #46 #134 #173 #174 #50 | M |
| 1.8 | **O painel acompanha por evento.** Sai o teto de 210 s (o backend dá 540–600 s, então o desfecho real de um `start` remoto nunca chegava); trilha criado→enviado→recebido→iniciado→concluído no cartão e no foco; histórico de comandos | #55 #8 #56 | M |
| 1.9 | **Testes do agente e do canal.** Hoje zero — e já escondiam o defeito do ACK. Agente falso de contrato; chamadas bloqueantes fora do laço de eventos | #158 #37 | M |

**Fecha quando (aceites 1, 6 e 8, em real):** `stop`, `start`, `hibernate`, `wake`, `restart` e `reset` em um aparelho
local e um remoto com a mesma trilha de estados; derrubar o túnel no meio de um `start` remoto e ver o comando fechar
sozinho depois da reconexão; cancelar um `start` em andamento; dois comandos simultâneos no mesmo aparelho — o segundo
recusado.

### Fase 2 — Um contrato só: o central vira worker · 3 itens (8 achados: 1 P, 4 M, 3 G)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 2.1 | **`LocalWorker`.** O central se registra na tabela `workers` (id = `OWNER_ID`, recursos via psutil, batida local), as instâncias locais ganham `worker_id`, e `_do_action` passa a ter **um ramo só**. `worker/executor.py` vira o núcleo comum, em vez de duas implementações de start/stop/hibernate/reset. Teste de contrato parametrizado: o mesmo cenário contra o `LocalWorker` e contra o agente | #154 #11 #29 #147 #52 | G |
| 2.2 | **Capacidades declaradas.** Aparelho e worker declaram imagem, nível de API, ABI, Play Store/GMS, hibernação, Appium local ou central; o pré-voo usa a declaração do worker, não a configuração do central; `appium: local` passa a valer | #178 #12 | M |
| 2.3 | **Log do emulador remoto** | #18 | P |

**Fecha quando (aceite 5):** dois workers reais — o central e o notebook — recebendo trabalho pelo mesmo contrato, com
slots e manutenção valendo para os dois.

### Fase 3 — Estado verdadeiro do aparelho · 6 itens (16 achados: 5 P, 10 M, 1 G)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 3.1 | **Saúde do convidado.** Sonda além de `boot_completed` (`service check activity` + interface pronta); depois de N falhas seguidas de sessão de automação o estado vira `error` com `attention` — vale para local e remoto; com `desired_state=online`, `restart` automático pelo worker, com teto e registro | #1 #112 #133 | M |
| 3.2 | **Estado de app e sessão com identidade física.** Gravar o serial; invalidar no reset e na troca do aparelho por baixo do id (android-09/10 mostram Instagram `ready` herdado de quando eram emuladores locais); reobservar ao ficar online se o dado for velho; mostrar a idade; botão "Verificar"; versão promovida como **estado desejado** — aparelho que entra depois recebe | #76 #111 #75 | M |
| 3.3 | **Sessão com validade.** Desafio ou login surgido no meio da execução atualiza o perfil (hoje o painel mostra "Conectado" para conta presa num desafio, e o login automático nunca dispara) | #103 | M |
| 3.4 | **Saúde ao vivo.** `health.updated` emitido e tratado; o mascaramento do Appium deixa de dar falso negativo depois de reiniciar só o backend (é o `degraded` de hoje, e bloqueia o preenchimento de senha com a proteção de fato ativa) | #65 #118 #139 #105 | M |
| 3.5 | **O túnel como componente.** Hoje a queda dele aparece como seis aparelhos "sem ADB" e um worker "sem batida", sem causa | #179 | M |
| 3.6 | **Cartão, foco e Infraestrutura honestos.** Em que servidor o aparelho está; "desligado de propósito" ≠ "sem conexão ADB"; o foco não oferece verbo que o aparelho não suporta; saem os textos que mandam a controles inexistentes; entram logs, evidências, filas, perfis e apps por servidor, disco, capacidades | #61 #62 #64 #63 | G |

**Fecha quando:** matar o `system_server` de um remoto faz o painel acusar em minutos e remediar sozinho; resetar um
aparelho derruba o estado de app e a sessão; controle manual de tela num remoto registrado (aceite 3).

### Fase 4 — Escalonamento e localidade · 6 itens (16 achados: 3 P, 4 M, 9 G)

A maior em trabalho novo: a seção 5 do pedido praticamente não existe.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 4.1 | **A execução registra onde rodou.** `worker_id`, serial e backend em objetivo, etapa, relatório e telas. Hoje um relatório antigo de "android-09" não diz se foi o emulador local de 17/09 ou o aparelho do notebook de 19/09 — os dois existem no banco com o mesmo id | #176 | M |
| 4.2 | **Scheduler ciente de worker.** Slots e recursos do heartbeat entram na decisão (`degraded` passa a existir); manutenção e offline = esperar, não bloquear na hora; o rodízio liga e desliga aparelho remoto **pelo worker**; os tetos fixos de 10 viram contagem por worker | #10 #40 #41 #43 #135 #48 | G |
| 4.3 | **Pré-voo na criação da execução.** App instalado ou entregável, estado do aparelho, worker disponível — recusa com código e ação para resolver, antes de agendar | #51 | M |
| 4.4 | **Localidade de perfil (E9).** O perfil registra o worker e o aparelho físico onde os dados vivem; comportamento definido quando aquele worker está offline (esperar, ou reautenticação explícita em outro); visível na tela de Perfis | #45 #69 | G |
| 4.5 | **Servidor e aparelho novos sem editar YAML.** Hoje exige mexer no `config.yaml`, reinstalar o túnel e reiniciar o central; o inventário vive em três lugares sem conferência. Inclui o túnel aberto **pelo worker**, que é o que permite worker em outra rede | #16 #151 #47 #13 | G |
| 4.6 | **"Instalar em todos agora"** promete ligar remoto parado e nunca encerra | #44 #84 | P |

**Fecha quando (aceites 2 e 5):** uma tarefa para remoto desligado liga o aparelho pelo worker, executa e o desliga;
dois workers recebem carga conforme slots; workflow com efeito externo em remoto acompanhado pelo painel.

### Fase 5 — Dois backends de verdade, fila e storage · 7 itens (15 achados: 1 P, 11 M, 3 G)

Depende da sua decisão 9 para 5.1–5.3.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 5.1 | **Hospedeiro e papéis.** `instances.hosted_by`; despacho, rodízio, início de execução e reconciliações de boot só atuam no que este backend hospeda — objetivo alheio é ignorado, nunca bloqueado; `ROLE=api\|scheduler\|all` | #171 #26 #27 | G |
| 5.2 | **Limite de IA global** por lease no banco (o de boot continua por processo, de propósito) | #35 #94 | M |
| 5.3 | **Guarda de relógio.** O backend recusa subir como segundo dono se divergir do relógio do banco; o agente reporta o desvio; cerca também na escrita | #32 | M |
| 5.4 | **PostgreSQL de produção.** Reconexão e pool; `/health` olha o banco; ferramenta de migração de dados SQLite→PostgreSQL **com conferência**; hash por migração com alerta quando arquivo aplicado muda; migração 018 que converge o esquema antigo da 008; testes próprios do divisor de SQL; os 43 testes presos ao SQLite | #33 #34 #162 #169 | M |
| 5.5 | **Cofre entre backends.** Hoje um segundo backend gera chave própria e se diz `ready`; `key_id` que identifique a chave; nome neutro para a chave mestra | #126 #88 | M |
| 5.6 | **Outbox + NATS JetStream (E7)**, atrás de bandeira, com o WebSocket padrão até um comando real atravessar; `EventBus` entre réplicas | #30 | G |
| 5.7 | **Storage de evidências e de APKs (E8).** Disco ou S3-compatível; a escrita sai do laço de eventos; a retenção para de apagar linha de arquivo que pode estar em outra máquina | #172 #89 | M |

**Fecha quando (aceite 7 em campo):** dois backends vivos no mesmo PostgreSQL, cada um com seus aparelhos; matar um no
meio de uma etapa com aparelho real e ver o outro adotar; reinício com fila carregada.

### Fase 6 — Aplicativos, loja e Instagram fora do núcleo · 6 itens (19 achados: 1 P, 12 M, 6 G)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 6.1 | **Registro de apps.** Catálogo por pacote no lugar do `if package == "com.instagram.android"`; porta de sessão por app (hoje tarefa de QA Messenger passa pela porta do Instagram, e instalar **qualquer** pacote invalida a sessão do Instagram); defaults neutros; fluxos declaram os apps de que precisam; execução que mistura apps não perde política, limite e aprovação | #78 #79 #80 #81 #70 #77 | G |
| 6.2 | **Instalar, canário, rollback, distribuir, verificar, buscar da loja e conectar/verificar/sair como comandos** — com `uncertain` e releitura antes de decretar `install_failed` (hoje um timeout numa leitura **depois** de instalação bem-sucedida vira falha pegajosa); instalação interrompida por reinício sai de `verifying` | #73 #113 #58 #59 #85 | G |
| 6.3 | **Catálogo visual (E11).** Nome, ícone, origem, versão; escolha de destinos; progresso e falha por aparelho; upload; um caminho de instalação só | #66 #82 #83 | G |
| 6.4 | **Fila "Aguardando intervenção".** Perfil + aparelho + motivo + idade, com botão que assume o controle na tela certa, local ou remota; devolver o controle dispara a reobservação (a mensagem de hoje promete isso e o código não faz). Ler código de verificação **continua manual** | #106 | M |
| 6.5 | **VM da loja remota** — conforme a decisão 4; e a interface passa a dizer que a conta Google de uma VM não instala em outra | #67 #74 | G |
| 6.6 | **Prova:** o conjunto real do Instagram (base de 238 MB + split, `install-multiple`) num remoto; `select_splits` com conjunto real | #86 #87 | M |

**Fecha quando (aceite 4):** um app que não é o Instagram instalado e aberto num remoto pelo catálogo, com progresso
por aparelho e o desfecho real na tela.

### Fase 7 — Hub de IA · 4 itens (11 achados: 3 P, 7 M, 1 G)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 7.1 | **Provedor compatível com OpenAI** — desde 24/09 o alvo é **Ollama nativo no Windows** (qwen3-vl na RTX 2000 Ada de 8 GB), não vLLM em WSL+Docker; capacidade por modelo **declarada** (hoje é aprendida por erro 400 e reaprendida a cada reinício); provedor por função; piso de conteúdo (decisão local cujo alvo não existe na tela não age: escala) | #91 #97 | G |
| 7.2 | **Fallback explícito por função**, visível na linha do tempo e com preço cadastrado; timeouts e concorrência por função; teto de gasto em US$ por execução e por dia | #92 #96 #95 | M |
| 7.3 | **A interface distingue** aguardando aparelho / vaga de IA / resposta do modelo / recusa / pessoa; chamada com erro deixa de ser gravada como modelo `(erro)` | #93 #68 #101 | M |
| 7.4 | **Bateria de avaliação** (decisão 7): linha de base contra a configuração atual; rejulgar com o modelo caro as 56 capturas que o verificador barato já julgou — barato, sem aparelho e sem efeito externo; entender os ~10 % de HTTP 500 do verificador; cache de prompt do verificador | #98 #99 #100 | M |
| 7.5 | **A IA ensina, o software executa — custo por chamada** (plano de 24/09; medido em 7 dias: US$ 0,40 e 13,8 chamadas por aparelho-comando; 39 % das decisões no Opus por escalonamento; cache inerte no Sonnet/Haiku): `cache_control` sempre; Opus 5.5 com preço certo (`price_for` exato) e teto diário; escalonar por **risco** (`by_risk`); **provas locais** no catálogo do Instagram (`local_proof` por seletor, `==` exato, faixa por `band_guard`; OPEN_POST/OPEN_COMMENTS determinísticos; OPEN_FEED corrigido). Medido: Instagram 2/3 → 3/3, US$ 0,20 → 0,08 por caso | — | G |
| 7.6 | **Dieta do contexto do ator** (item 5 do plano de 24/09; ~11 k tokens de entrada por decisão): (a) `step_block(ctx, for_actor=True)` sem "Próximas etapas" no ator (verificador e planejador continuam recebendo); (b) `ctx_for()` no executor manda só os parâmetros da etapa (`cap.bindings + optional_bindings` ∪ `step.variables`; plano livre mantém tudo); (c) histórico comprimido sem chamada: linhas `REJEITADA`/`FALHOU`/`(executor)` + últimas N (`ai.actor_history_lines`, padrão 6); (d) `UiTree.prompt_lines(..., boost=)`: +6 para elemento cujo texto/desc casa `guard_variants` de `step.bindings`, `commit_selector` ou `known_selectors`, +3 vizinhos na faixa; teto 60 no exemplo; (e) `screenshot_max_side` 768 no exemplo. Cada alavanca com teste que inspeciona o texto do usuário (padrão `test_openai_provider.py`). Alvo: −30 % de `fresh/calls` em `decide` sem perder caso | — | M |
| 7.7 | **Estimativa de custo antes de rodar** (item 6; do Osintgram "quanto vai custar?"): `GET /api/flows/cobertura` ganha `estimated_usd` por fluxo = etapas sem receita × custo mediano por etapa só-IA (por papel, `ai_calls` dos últimos 7 dias) + verificações previstas × custo mediano de `verify`; sem histórico → `null` ("sem base"). Painel: na tela de comando, quando o comando casa um fluxo, "estimativa: US$ X por aparelho · N etapas sem IA" ao lado de Executar. Testes: backend em `test_perfil_bloqueado_e_capacidades.py` (fixture com `ai_calls` semeadas) e vitest do componente | — | P |
| 7.8 | **Modelo local (Ollama) preparado no código, sem ligar** (item 7, opcional; go/no-go do dono): (1) piso de conteúdo — com tier 0 em provedor local, decisão cujo `element_id` não existe em `obs.tree` não age: `history.append` e a próxima decisão sobe para tier 1; (2) `ProviderCfg.extra_body` opcional repassado ao corpo do `/chat/completions` (Ollama: `options.num_ctx`); (3) `ai.models` do exemplo ganha `qwen3-vl:4b` comentado (`structured_output: json_object`, sem thinking/effort) e o bloco `providers/roles` comentado passa a citar Ollama nativo (`http://127.0.0.1:11434/v1`) em vez de vLLM+WSL; (4) `scripts/ollama-local.ps1` que só confere ambiente (GPU, `ollama`, modelo baixado) e imprime o bloco de config — nada de instalar ou baixar sozinho. Testes: `test_openai_provider.py` (`extra_body`, piso), `test_hub_de_ia.py` (fallback por `invalid_output`). Sem chamada paga | — | M |
| 7.9 | **Aviso de IA sem contradição** (backlog B1 da documentação, 24/09): com ator local e as demais funções externas, `/api/ai` e `/api/health` abriam o aviso com "os dados NÃO saem desta máquina" (frase do provedor do ator) enquanto o agregado `sends_data_externally` dizia `true` — as duas verdadeiras, lidas juntas parecendo contradição; o aviso passa a dizer de qual função é a primeira frase | pedido do dono | P |
| 7.10 | **Verificador barato com duas proteções** (backlog B14, bateria de 25/09): o Haiku 4.5 errou para os dois lados no rejulgamento (6 falsos positivos e 9 falsos negativos em 56 capturas) e parou uma execução recusando "Entregue" onde bastava "Enviada". Em vez de trocar o modelo da função (o dobro do custo por verificação, sem medida de que resolve), (1) recusa com nível de entrega que já atende ao exigido é rejulgada UMA vez pelo modelo de escalonamento, e (2) "sim" sobre tela sem nenhum elemento na hierarquia não conta como prova | pedido do dono | P |
| 7.11 | **Saúde acusa IA em fallback** (backlog B15, bateria de 25/09): com o Ollama fora do ar as 89 decisões foram para o fallback declarado e `/api/health` dizia `ok`; a saúde passa a listar, por função, as chamadas dos últimos 30 min que o provedor principal não atendeu. Junto, o ator volta a ser declarado no Sonnet (ADR-023) | pedido do dono | P |

### Fase 8 — Qualidade da operação do Instagram · 4 itens (8 achados: 2 P, 5 M, 1 G)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 8.1 | **Personas.** Briefing do planejador sem tom (em conflito, vale o da persona); separar "mensagem simples" de "comentar o que está na tela"; completar as 8 personas (exemplos, expressões, estilo em DM e em comentário) e avisar no portal quando faltarem campos; prova antes/depois com a mesma intenção nos 8 perfis | #107 | M |
| 8.2 | **Memória de DM.** Extrair a fala da contraparte, gravar interações de entrada, resumo de conversa de verdade; conversa real entre duas contas do parque com memória reutilizada na execução seguinte | #108 | G |
| 8.3 | **Sinais e limites.** "Confirm you're human" na tabela do classificador; teto diário e coordenação entre contas para o mesmo alvo; aprovação pendente expira quando a execução é cancelada; responder comentário em aparelho real; troca de conta: implementar ou retirar a opção | #104 #114 #109 #115 | M |
| 8.4 | **Instagram num remoto.** Os 8 perfis vivem em emuladores locais e os remotos nem têm o app | #53 #110 | M |

### Fase 9 — Acesso de pessoas e endurecimento · 5 itens (12 achados: 7 P, 3 M, 2 G)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 9.1 | **Login, sessão e identidade** (decisão 8): cookie de sessão, WebSocket e imagens autenticados, auditoria com o nome de quem fez (hoje tudo diz `panel`) | #119 #60 | G |
| 9.2 | **TLS** na opção "porta de rede"; como proteger o PostgreSQL entre máquinas | #120 #132 | M |
| 9.3 | **Credencial de worker** revogável e rotacionável; arquivo com permissão restrita de verdade | #121 #15 #122 | P |
| 9.4 | **Túnel com usuário restrito.** Hoje a chave, sem senha, dá shell de Administrator no worker | #123 | M |
| 9.5 | **Dado sensível.** "Tela sensível" além de campo de senha (hoje qualquer outra tela vira evidência e vai ao provedor de IA); redação de URL com credencial, `Authorization: Basic`, chaves de nuvem; texto manual fora da linha de comando do adb; auditoria automática de dependências | #127 #128 #129 #131 | M |

### Fase 10 — Operação contínua · 4 itens (12 achados: 7 P, 4 M, 1 G)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 10.1 | **Tudo volta sozinho.** Backend do central como tarefa supervisionada (depende de 3.4, senão cada reinício deixa a saúde `degraded`); agente e emuladores do worker pelo estado desejado; instalador versionado (Windows e systemd); versão do agente derivada do commit com alerta de divergência; a tarefa do túnel aponta para um caminho versionado do PowerShell da Store, e `-Instalar` derruba os túneis de **todos** os workers | #136 #137 #38 #14 #138 #181 | M |
| 10.2 | **Crescimento sob controle.** A batida do worker sai do log persistido (57 % dos eventos); retenção para todas as tabelas; rotação dos demais logs | #17 #143 #39 #144 | P |
| 10.3 | **Capacidade** (decisão 5): alvo escrito, alerta quando não cabe, teste de escala do parque inteiro | #146 | M |
| 10.4 | **Worker Linux** — nunca rodou; o agente não destaca o emulador do próprio processo fora do Windows | #180 | M |
| 10.5 | **Limites por servidor e distribuição** (pedido de 24/09): a tela Limites separa o que é do parque (teto geral, IA, tempos) do que é de cada máquina — vagas, boots em paralelo, teto de "trabalhando" e piso de RAM, num cartão por servidor com a carga ao vivo; o notebook muda pelo painel (mensagem `limits` ao agente, sem SSH no `worker.yaml`); o agendador respeita o teto de cada máquina; "Distribuir entre servidores" no painel de comando escolhe N aparelhos do app pela carga relativa de cada máquina, com prévia | — | M |
| 10.6 | **Teto de boots coerente** (backlog B2/B3 da documentação, 24/09): o painel (`ServerLimitsPatch`) e o `config.yaml` aceitam `boot_parallelism` até 10 e a mensagem `limits` do protocolo prometia até 16; os três passam a ter o mesmo teto, com teste que impede a divergência; o comentário do protocolo diz quando `limits` é enviado de fato (primeira batida da conexão) | pedido do dono | P |

### Fase 11 — Usabilidade do painel · 16 itens: 11.1–11.10, e 12.x/13.x acrescentados em 24/09 (varredura visual de 24/09 em 1440, 1024 e 375 px)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 11.1 | **Rolagem e casca responsiva.** `body { min-width: 1024px }` (styles/base.css) fazia o documento medir 1024 px num viewport de 375 e, com o app em `100dvh`, sobrava fundo preto abaixo; casca passa a funcionar de 360 a 2400 px (barra de navegação rolável na horizontal abaixo de 900 px, métricas do topo compactas abaixo de 1200). `main` continua o ÚNICO rolador vertical da página. Execuções: a lista de execuções é um rolador aninhado (`ul.runList` 601 px de altura com 5 558 de conteúdo, dentro de um cartão `sticky` de 724 px) — só tem rolagem própria em telas largas, com `overscroll-behavior: contain`; em telas estreitas empilha e rola com a página | — | M |
| 11.2 | **Respiro e cabeçalho das páginas.** Infraestrutura e Aplicativos renderizam sem o recuo lateral das outras páginas (título colado na borda em 1440 px); todas as páginas usam o mesmo invólucro e cabeçalho (título, texto de apoio, ações à direita) | — | P |
| 11.3 | **Tabelas largas e texto miúdo.** `RecordTable` sem contêiner de rolagem: "Medições de capacidade" (Diagnóstico) termina em x=1622 num viewport de 1440; "Receitas aprendidas" (Configuração) em x=1359 num de 1024 — contêiner com `overflow-x: auto`, primeira coluna fixa e cabeçalho fixo dentro dele; colunas com nome cru em inglês (Kind, Instance id, Clock skew before after s, Online after, Image, Saved, Save seconds) ganham rótulo em português; menor corpo de texto passa de 10,5 px para 12 px | — | M |
| 11.4 | **Painel e Execuções sem sobreposição.** A barra de ação em massa (`bulkDock`, `sticky` no rodapé) cobre os botões do rodapé dos cartões (medido: Iniciar/Abrir sob a barra) — reservar o espaço dela enquanto estiver visível; no cabeçalho do cartão de aparelho remoto, o selo "Notebook da LAN" espreme o selo de estado a 12 px (android-09…15) — o cabeçalho quebra em duas linhas ou o servidor vai para a linha de metadados; no detalhe de execução, as abas (Decisões, Relatório) saem da tela em 1024 px — faixa de abas rolável | — | M |
| 11.5 | **Menos trabalho repetitivo.** Diagnóstico mede 17 458 px e Configuração 9 220 px de altura: sumário com âncoras no topo e seções recolhíveis, com o estado lembrado no navegador; Painel: seleção rápida por estado (online, parados, hibernados, com erro) e por servidor, filtros lembrados; Execuções: "Repetir" (mesmo comando e mesmos aparelhos, chave de idempotência nova); campo de comando com os últimos comandos usados | — | M |
| 11.6 | **Perfil como uma pessoa, não como formulário** (pedido do dono em 24/09: "linhas do tempo, algo que signifique a montagem de uma persona visualmente"). Só frontend (`features/profiles/ProfileDetail.tsx` e componentes novos em `features/profiles/`), sem dependência nova — SVG/CSS com os tokens do tema; os formulários de edição continuam existindo atrás de um botão **Editar** (nada perde função). (1) **Visão geral = cartão de identidade**: avatar grande, @usuário, nome, persona, selos (sessão, situação, aparelho/servidor), a "assinatura de voz" em miniatura (três réguas: formalidade, tamanho, emoji), interesses como etiquetas, números do perfil (interações por tipo, fração sem IA de `GET /instagram/profiles/{id}/capacidades`, último contato) e uma **mini linha do tempo** com as 8 interações mais recentes. (2) **Persona = montagem**: bloco "Quem é" (resumo + personalidade em destaque tipográfico), **réguas visuais** para formalidade (informal·neutro·formal), tamanho (curta·média·longa) e emoji (nunca·raro·moderado·muito) com o valor marcado, interesses em etiquetas, **"Diz" × "Nunca diz"** em duas colunas coloridas (common_phrases / forbidden_phrases), **exemplos como balões de conversa**, "Com conhecidos" × "Com estranhos" lado a lado, "Em DM" × "Em comentário" lado a lado, aparência/estilo visual quando houver, e o **medidor de completude** (voice_gaps viram "falta preencher: …"); o teste de voz (prévia) fica num painel lateral/recolhível. (3) **Memória = pessoas e fatos**: agrupada por `subject` (a pessoa/tema) em cartões com avatar de iniciais, fatos como itens com barra de importância e selo de confiança, "visto N vezes" e "usado há X"; ordenada por importância × recência; "Ensinar um fato" vira um botão que abre o formulário. (4) **Interações = linha do tempo vertical** agrupada por dia (Hoje, Ontem, data), um ícone e cor por tipo (dm_sent, dm_received, comment_replied, post_liked, followed…), direção (entrada/saída), contraparte, trecho do texto recebido/enviado como balão, selo de status; filtro por tipo em etiquetas no topo. (5) **Habilidades = mapa de caminhos**: cada fluxo como uma trilha de etapas (bolinhas coloridas: receita = verde, parte receita = âmbar, IA = neutra) com vezes executado e última vez; um anel/barra da fração sem IA; interações confirmadas por tipo em barras horizontais. Testes vitest para cada aba (renderiza a partir de fixtures: réguas marcam o valor certo, Diz/Nunca diz, agrupamento por dia, agrupamento da memória por assunto, estado vazio de cada aba) | — | M |
| 11.7 | **Diagnóstico prático** (pedido do dono em 24/09: "a tela de diagnóstico está pouquíssimo prática"; a página media 17 458 px de altura com JSON cru). Só frontend (`features/diagnostics/`). (1) **Topo = painel de decisão** em uma linha de azulejos com semáforo: Saúde (ok/degradado + nº de problemas), Custo de IA hoje × teto diário (barra), Aparelhos online × vagas × estimativa de capacidade, Máquina (CPU, RAM livre, disco livre em medidores), Aceleração (disponível/não); cada azulejo clicável rola até o detalhe. (2) **Problemas primeiro**: lista com o problema, a dica (`hint`) e o que fazer, antes de qualquer tabela; vazio = uma linha "nenhum problema". (3) **Custo de IA** como está (UsageWeekCard), mais visível. (4) **Medições de capacidade** viram gráfico (SVG): tempo de boot por medição e memória livre, com a tabela completa recolhida atrás de "ver tabela"; rótulos em português. (5) **Detalhes técnicos** (Máquina, Aceleração, Ferramentas, Capacidade, Outros dados) recolhidos por padrão em seções `Disclosure` com o estado lembrado no navegador (localStorage com try/catch), e um **sumário com âncoras** no topo. (6) Eventos recentes com filtro por nível (erro/aviso/info) e no máximo 50 linhas visíveis com "ver mais". Testes vitest: azulejos a partir de fixtures, problemas antes das tabelas, seções recolhidas por padrão e lembradas | — | M |
| 11.8 | **Configurações do perfil sem formulário gigante** (pedido do dono em 24/09). Só frontend (`AbaConfiguracoes` em `features/profiles/ProfileDetail.tsx`; peças novas em `features/profiles/`). (1) As ~20 ações do catálogo agrupadas por natureza — Sessão, Navegação, Leitura, **Efeito externo** — cada grupo recolhível, cada ação numa linha com ícone, selo de risco (baixo/médio/alto) e a política como **controle segmentado** de três botões (Sozinho · Com aprovação · Só manual) em vez de `<select>`; selo "diferente do padrão" com ação "voltar ao padrão"; ações em lote por grupo ("tudo com aprovação"). (2) Limites (curtidas, comentários, seguir, DMs por hora/dia) como **cartões com medidor**: uso de hoje (contado das interações confirmadas do perfil, `GET /instagram/profiles/{id}/interactions`) × limite, cor por proximidade do teto, edição do número no próprio cartão. (3) Resumo no topo em uma linha: "N ações sozinho · N com aprovação · N só manual". Mesma API de hoje (`setPolicy`); testes vitest do agrupamento, do controle segmentado e do medidor | — | M |
| 11.9 | **Instâncias e contas legível** (pedido do dono em 24/09: "a tela está ruim"; hoje é uma tabela de 15 linhas com três seletores e um campo por linha, `features/settings/InstancesSection.tsx`). Só frontend. (1) **Agrupada por servidor** (Este servidor · Notebook da LAN · …) em grade de cartões compactos: id, ponto de estado, app com ícone, rótulo da conta, **conta observada no app com indicador de conferência** (bate ✓ / diverge ⚠ / não observada), perfil do Instagram vinculado com avatar (`api.listProfiles`). (2) **Edição sob demanda**: clique no cartão abre um painel lateral/popover com servidor, app e rótulo — nada de 45 controles sempre abertos; os rascunhos e o "salvar alterações (N)" de hoje continuam valendo. (3) **Filtros em etiquetas** no topo: por servidor, por app, "só divergências", "sem perfil"; a barra de aplicar app em lote continua. Testes vitest: agrupamento por servidor, indicador de divergência, edição pelo painel preserva o fluxo de rascunho/salvar | — | M |
| 11.10 | **Grupos de acesso** (pedido do dono em 24/09: "cada persona tem o próprio perfil de acesso; criar grupos que se atribuem a quantos bots quiser, e o acesso próprio alterado deliberadamente sobrepõe o do grupo"). Migração 036 (`policy_groups` + `instagram_profiles.policy_group_id`); resolução em `social/policy.py`: escolha própria → grupo → padrão do catálogo/`DEFAULT_LIMITS`; `null` no PUT da política = herdar; rotas `/instagram/policy-groups` e `/instagram/policy-defaults`. Frontend: seção Grupos de acesso na tela de Perfis (cartões + editor com o mesmo visual da aba Configurações e escolha de membros), seletor de grupo e origem por ação (próprio · do grupo · padrão) na aba Configurações do perfil, grupo no cartão e no cadastro do perfil. | pedido do dono | M |
| 12.1 | **Perfil com contas em vários apps — estrutura** (pedido do dono em 24/09: perfis não podem ficar presos a um app; conhecimento por app; comando pode englobar mais de um app). Migração 037: `profile_accounts` (uma conta por perfil e app; backfill da conta Instagram), `account_credentials` (senha de outros apps no cofre), `app_id` em `memory_items`/`social_interactions`/`pending_approvals`/`steps`, `runs.app_ids`. `PlanStep.app_id` e contexto de app POR ETAPA no scheduler/executor (receitas, catálogo, sessão e memória da etapa usam o app dela); planejador pode declarar o app de cada etapa; coordenação de frota por (app, alvo). A tabela `instagram_profiles` fica (renomear quebraria ~30 pontos); o perfil vira identidade na API/UI. | pedido do dono | G |
| 12.2 | **Perfil e Aplicativos por app — telas** (depende de 12.1). Perfil com seletor de app acima das abas (Memória, Interações, Habilidades, Configurações filtradas) e aba Contas (uma por app, com sessão e senha própria); "Abrir app" vira menu com os apps instalados no aparelho (manda `app_id`); menu Aplicativos centrado no app: cartão por app e página do app com contas, aparelhos, execuções e rastros, custo de IA, receitas/fluxos e versões. | pedido do dono | G |
| 12.3 | **Apps novos operando de verdade** (Outlook, TikTok, Facebook…). Depois de 12.1/12.2 eles são operáveis pela IA livre e o login é feito pela pessoa assumindo o aparelho; login determinístico, catálogo de ações e classificador de telas são trabalho POR APP, com decisão do dono sobre qual vem primeiro. | pedido do dono | G |
| 13.1 | **Modo treinamento — gravação** (pedido do dono em 24/09: ensinar as personas fazendo, com a IA mapeando o processo, não só gravando passos). Migração 038: `training_sessions` e `training_inputs` (sem FK para `attempts`); no Foco, com o controle na mão da pessoa, cada entrada (toque, texto, deslize, tecla, abrir app) é gravada com o ELEMENTO tocado (`UiTree.at`, seletores únicos) e a tela antes; texto em campo de senha, tela sensível e o que parece segredo nunca são gravados; soltar o controle encerra a gravação. | pedido do dono | M |
| 13.2 | **Modo treinamento — generalização assistida** (depende de 13.1). Nova função de IA `generalize` (modelo do planejador; simulado nos testes) lê a gravação e propõe: comando com parâmetros, etapas com objetivo/pós-condição verificável/app/ação do catálogo, e entradas descartadas (erro, vai-e-volta). Salvar vira FLUXO (`learn_from_plan`) + RECEITAS por etapa (`distill_training`), com escopo por perfis/grupos (`flow_scope`); `match` respeita o escopo. Etapa com efeito num app com catálogo exige ação do catálogo (senão a política seria contornada) — e o portão de política passa a recusar efeito sem ação num app com catálogo. | pedido do dono | G |
| 13.3 | **Modo treinamento — telas** (depende de 13.2). Barra de gravação no Foco (intenção, entradas ao vivo, concluir), revisão da proposta antes de salvar (parâmetros, etapas, descartes, ação por etapa, perfis/grupos que recebem) e habilidades treinadas na aba Habilidades do perfil. | pedido do dono | M |

### Fase 14 — Desempenho e capacidade · 10 itens (pedido do dono de 26/09/2026; ADR-027, ADR-028)

Coordenação multiagente sobre `1104d50` (frentes F1–F8). Relatório: [`relatorio-desempenho.md`](relatorio-desempenho.md); checkpoint: [`handoffs/evolucao-desempenho.md`](handoffs/evolucao-desempenho.md). Contratos: adendo v0.20 de `api-contract.md`.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 14.1 | **Medição agregada e bancada** (F1). `backend/app/metricas.py` (contadores e distribuições em memória com teto de séries, janela de 15 min em `measurements`), `GET /api/desempenho` com `?dias=N` (`backend/app/desempenho.py`: p50/p95/n por entidade, taxas separadas de sucesso/falha/incerto/espera/cancelamento), `scripts/bench.py` (simulado, leitura só-GET, comparar com limite e amostra declarados) e `scripts/eval_run.py` seguro sem `--yes` | pedido do dono (26/09) | M |
| 14.2 | **Prévia sob demanda e tela sensível fora da prévia** (F2). Interesse por conexão (`watch` com TTL, grade visível e foco), `paused`, uma captura para vários espectadores, painel antigo = grade em todos, controle manual conta como foco, grade não segura hibernação, `preview_mode` como volta atrás; frame marcador sem imagem, `/frame` 404 `sensitive_screen`, captura pausada durante `type_secret`, VM-loja sempre oculta (`backend/app/devices/manager.py`, `frontend/src/store/live.ts`) | pedido do dono (26/09); defeito H6 do reconhecimento | G |
| 14.3 | **Observação com a árvore primeiro** (F2). Imagem só quando política, julgamento, evidência ou divergência pedem; PNG decodificado uma vez; publicação e observação conferem tela sensível e geração no instante de publicar; login do Instagram só com árvore (`backend/app/taskqueue/executor.py`, `backend/app/devices/manager.py`) | pedido do dono (26/09) | M |
| 14.4 | **Receitas: funil medido e desbravador** (F3). `receita.consulta/reproducao/retorno_ia` por tentativa, `aproveitamento` em `GET /api/flows/cobertura` (`backend/app/taskqueue/aproveitamento.py`), desbravador visível (`wait_reason: pathfinder`), medido, agrupado por compatibilidade, solto na falha do líder; receita divergida continua sem escalar (escalar é decisão do dono) | pedido do dono (26/09) | M |
| 14.5 | **Recursos efetivos, reserva de RAM e admissão conservadora** (F5). `backend/app/devices/recursos.py` (cgroup v1/v2, PSI), reserva por boot no worker antes de qualquer await e órfã enquanto o processo pode viver, vagas contam boot admitido, admissão com `reserved_mb` e recusa explicada com batida velha, métrica do agente na batida, exemplos sem `ram_mb: 1536` | pedido do dono (26/09); B21 | M |
| 14.6 | **Transporte e posse** (F4 fase A). NATS endereça a réplica hospedeira com `ack_wait` aplicado; cerca serializada por aparelho na transação (sem UNIQUE); reentrega depois do `result_ack` não reexecuta (`fence_not_newer`); resultado tardio não reescreve o aparelho; `accepted_features` negociado no `welcome` (`backend/app/commands/transport.py`, `backend/app/commands/store.py`, `backend/app/worker/agent.py`) | pedido do dono (26/09) | M |
| 14.7 | **Imagem na origem do worker e reserva central** (F4 fase B). Screencap reduzido no agente para quem aceitou `observe_local`, mídia fora do WebSocket de comando, mesmas guardas de tela sensível e geração; reserva de RAM no `DeviceManager._recusa_por_capacidade`; corrida de `_do_action_no_worker` | pedido do dono (26/09) | G |
| 14.8 | **Contêiner dos serviços** (F6). `deploy/central.Dockerfile`, `deploy/compose.yaml` (central `ROLE=all` uma réplica, PostgreSQL e Ollama opcionais), sem privilégio, portas só em 127.0.0.1, testes estáticos; build e subida `not_run` (Docker Desktop não suportado em Windows Server) | pedido do dono (26/09) | M |
| 14.9 | **Decisões de runtime, executor e orquestração** (F7, ADR-028). Matriz de executor por operação (Android × API oficial × web), emulador nativo adotado, troca de renderer como piloto, contêiner/Linux adiados, Redroid e Kubernetes rejeitados, NATS adiado; protocolo de comparação pronto | pedido do dono (26/09) | P |
| 14.10 | **Prova real no parque**. Depois do deploy autorizado: `GET /api/desempenho` com `captura.evitada` e `captura.total` por origem, `bench.py leitura --dias 7` comparado à linha de base de 26/09, piloto A0′ do renderer num aparelho de teste | pedido do dono (26/09) | — |

**Fecha quando:** 14.1–14.9 com prova `simulated` registrada e 14.10 com prova `real` (data, máquina, commit) depois do deploy autorizado — captura evitada medida no parque e p50/p95 lidos da produção.

### Fase 15 — Evolução arquitetural e skills · 15 itens (pedido do dono de 27/09/2026; ADR-030…039)

Monólito modular incremental e plataforma de skills. Design: [`design/evolucao-arquitetural.md`](design/evolucao-arquitetural.md). Provas reais de 27/09 em [`relatorio-validacao.md`](relatorio-validacao.md) §14.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 15.1 | **Regras de dependência verificadas e tipagem gradual** (fase A). `backend/tests/test_arquitetura.py` por AST (camadas puras, infraestrutura só onde já morava, fecho do agente, zero ciclo de topo, catracas de import tardio e `Any`, contextos em DAG); mypy estrito em `app.contracts`/`app.modules`/`app.shared` pelo job `backend-tipos` (ADR-030) | pedido do dono (27/09) | M |
| 15.2 | **Despacho de comandos fora da API e `AppRepository`** (fase A). `commands/despacho.py`, recusa `DespachoRecusado` traduzida na borda; ciclo `api ↔ state` desfeito; `modules/applications` é o único que escreve em `apps` | pedido do dono (27/09) | M |
| 15.3 | **Contratos do worker e manifesto único do agente** (fase B, ADR-031). `app/contracts/worker`, esquema congelado `18285a7c65c51551`, `backend/worker-manifest.txt` lido pelos dois instaladores e pelo deploy, sonda de rede sem `models` no agente | pedido do dono (27/09) | M |
| 15.4 | **Capabilities e prova local de conversa aberta** (fases C e G0, ADR-032). `CapabilityDefinition`, `CatalogCapabilityProvider.verify`, gramática `&` na prova local e `OPEN_THREAD` exigindo o compositor | pedido do dono (27/09) | M |
| 15.5 | **Skills versionadas** (fase D, ADR-034). `SkillDefinition/SkillVersion`, transições explícitas, congelamento ao sair de `draft`, registro com dois backends, `skills.enabled`; migrações 042–046 | pedido do dono (27/09) | G |
| 15.6 | **DSL `automation/v1alpha1` e compilador** (fase E, ADR-033). Contrato Pydantic com esquema congelado, IR, 45 códigos `E_*`, baixa para o `Plan` atual, nunca gera nem executa Python | pedido do dono (27/09) | G |
| 15.7 | **Ensino v2** (fase F). Sessão com fontes, candidata com perguntas, credencial recusada, rascunho transacional, rotas e painel atrás de `features.skills` | pedido do dono (27/09) | G |
| 15.8 | **Fatia vertical "abrir conversa no Instagram"** (fase G). Registro → compilador → `Plan` → executor → prova local, trilha da 045, receita, composição | pedido do dono (27/09) | G |
| 15.9 | **Recursos declarativos e `PlanReport`** (fase H, ADR-035). Leitura, `diff`, `plan`, `apply`/`verify`/`reconcile` pelos mecanismos existentes (não ligados no ciclo), `plan_report` no `mode=plan` | pedido do dono (27/09) | G |
| 15.10 | **Resolução de intenção** (fase I). `IntentResolver` em cadeia, parâmetros tipados, ambiguidade vira pergunta, `POST /api/skills/resolve` | pedido do dono (27/09) | M |
| 15.11 | **Conversão de fluxos legados** (fase J, ADR-037). Descompilador `Plan` → DSL com ida e volta, adotar/desfazer, bateria de equivalência, painel | pedido do dono (27/09) | M |
| 15.12 | **Manifesto de app e `SessionProvider`** (fase K1, ADR-039). `AppDefinition`, registro por pacote, núcleo sem comparação com `"instagram"`, app de QA e processo cross-app só pelo manifesto | pedido do dono (27/09) | M |
| 15.13 | **`models.py` fatiado e máquinas de estado de execução** (fase K2, ADR-038). 29 corpos na apresentação de cada contexto; tabelas de transição de execução, objetivo, etapa e tentativa conferindo e registrando | pedido do dono (27/09) | M |
| 15.14 | **Implantação e provas reais** (27/09, autorizado pelo dono). Ensaio de 042–046 na cópia, deploy, agente do notebook pelo manifesto, fatia real, `mode=plan` real, ensino com IA real | pedido do dono (27/09) | M |
| 15.15 | **K restante**. Cluster de apps, portões e saúde de `state.py`; `bootstrap`; routers por contexto; `adapters/ai|android`; identidade de receita por capability; impor as máquinas de estado; ligar o `apply` no ciclo; conversão dos fluxos reais que não convertem | pedido do dono (27/09) | G |

### Fase 16 — Segunda evolução: persona, contas, imagens, N:N, roteamento, provisionamento e painel · 12 itens (pedido do dono de 27/09/2026; ADR-040…046)

Design: [`design/persona-e-parque.md`](design/persona-e-parque.md). Os itens seguem os treze pontos do pedido (§17 do design).

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 16.1 | **Contrato de página e Configuração em largura total** (item 1; ADR-046). `Page/PageHeader/PageSection/TableWrap/AutoGrid`, faixas por `@container page`, Configuração sem `pageNarrow` | pedido do dono (27/09) | M |
| 16.2 | **A credencial vem da conta da persona** (item 2; ADR-040). `RunCreate.credentials` fora (422), dados disponíveis por nome, `type_secret` pela conta do objetivo com consentimento; campo "Senha para a automação" removido do painel | pedido do dono (27/09) | M |
| 16.3 | **Persona gerada por prompt** (item 3). `POST /api/personas/generate` (rascunho validado, pago), `enrich`, cadastro por prompt ou manual no painel | pedido do dono (27/09) | M |
| 16.4 | **Imagens da persona** (item 4; ADR-042). `persona_images` (048), receita determinística com eixos de variação, gerador simulado e OpenAI atrás de chave, galeria no painel | pedido do dono (27/09) | G |
| 16.5 | **Modelo rico da persona e migração dos dados** (item 5; ADR-041). A persona é a linha de `instagram_profiles` (047), `personas` dobrada, biografia/visual/geração, crenças guardadas e não enviadas | pedido do dono (27/09) | G |
| 16.6 | **Conta única: Autenticação + Contas** (item 6; ADR-040). `account_credentials` com estado e consentimento, `host`, `account_sessions` por (conta, aparelho) (049); guia "Contas e acesso" | pedido do dono (27/09) | G |
| 16.7 | **Persona N:N aparelho e relação visível nos dois sentidos** (itens 7 e 8; ADR-043). Migração 051, vínculo por app com principal, D2-a; rotas de vínculo; guia Aparelhos da persona e personas no Foco e na Infraestrutura | pedido do dono (27/09) | G |
| 16.8 | **Roteamento por persona e destinos no texto** (item 9; ADR-044). `targets`, `device_policy`, `resolver_alvos`, `TargetExtractor`, prévia `POST /api/runs/targets/resolve`, modo "Por persona" no Comando | pedido do dono (27/09) | G |
| 16.9 | **Provisionamento de aparelho pela plataforma** (item 10; ADR-045). `POST/DELETE /api/instances` local (050), `max_devices`, "Criar aparelho"/"Aposentar" na Infraestrutura | pedido do dono (27/09) | M |
| 16.10 | **Foco em seções com hierarquia** (item 11; ADR-046). `FocusSection`s na ordem identidade → saúde → servidor → tarefa → personas → contas → apps → ações | pedido do dono (27/09) | M |
| 16.11 | **Ações rápidas e interação manual reorganizadas** (item 12; ADR-046). `focusActionGroups` puro, controle manual só com o controle na mão, Zona de perigo separada | pedido do dono (27/09) | M |
| 16.12 | **Coerência de ponta a ponta e implantação** (item 13). Ensaio 047–051 no backup mais recente, suíte completa, deploy, agente do notebook, validações reais possíveis | pedido do dono (27/09) | M |
| 16.13 | **Crenças ricas da persona vão ao modelo** (pedido do dono de 28/09; ADR-048). Religião e política como objetos ricos (biografia v2 normalizada na leitura), no bloco `<persona>` com regra de conduta, na geração por prompt e em dois cartões no painel | pedido do dono (28/09) | M |
| 16.14 | **Personas em lote e operações em lote** (pedido do dono de 28/09; adendo v0.34). Geração de 1 a 10 em segundo plano com plano de variedade, criar direto ou revisar, custo antes; barra de ações na lista (fotos, completar, grupo, bloquear/reativar, apagar) | pedido do dono (28/09) | M |

### Fase 17 — Custo de inferência por provedor e imagem real · 11 itens (pedido do dono de 28/09/2026; ADR-049)

Pesquisa: [`pesquisa-provedores-ia-2026-09-28.md`](pesquisa-provedores-ia-2026-09-28.md). Plano, links e critérios de aceite: [`plano-provedores-ia-2026-09-28.md`](plano-provedores-ia-2026-09-28.md). Meta do dono: o menor custo possível sem perder qualidade, medido.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 17.1 | **Chave e parâmetros por modelo no provedor compatível com OpenAI.** `api_key_env` resolvido pelo `.env` (`EnvSettings.chave`; antes só `os.environ`, 401 garantido), `ai.models.<m>.max_tokens_field` e `extra_body` por modelo (`reasoning_effort: none` no gpt-6-luna) | pesquisa de 28/09 | P |
| 17.2 | **Rejulgamento offline com candidato** (`eval_rejudge.py --sobrepor`): as mesmas capturas do Opus, falso positivo e negativo, sem aparelho e sem escrever no `config.yaml` | pesquisa de 28/09 | P |
| 17.3 | **`gpt-image-2` na imagem da persona**, custo pelo `usage` × `price_per_mtok`; o `gpt-image-1-mini` sai da API em 01/12/2026 | pesquisa de 28/09 | P |
| 17.4 | **Candidatos declarados** (`config.example.yaml`: capacidade e preço de gpt-6-luna, Gemini 3.1 Flash-Lite e deepseek-flash; `.env.example`; `docs/ia.md` §13) | pesquisa de 28/09 | P |
| 17.5 | **Medição real** (autorizada em 28/09): rejulgamento offline, teste de rosto, bateria base × braços (luna, Flash-Lite, escalonamento no Sonnet), só casos do app de QA nos braços | plano §Etapa 2 | M |
| 17.6 | **Adoção** (autorizada em 28/09 se TODOS os critérios passarem): `config.yaml` do central com o vencedor e `fallback_provider: anthropic`, ADR, relatório e uma semana de acompanhamento | plano §Etapa 3 | P |
| 17.7 | **Perfil de IA por execução e canário** (`ai.profiles` + `RunCreate.ai_profile` + `eval_run.py --profile`): A/B sem reiniciar o central | segunda onda | M |
| 17.8 | **Flex para trabalho offline** (geração de persona, rejulgamento) com `service_tier: flex` | segunda onda | P |
| 17.10 | **Cascata para ator barato**: `step_blocked` do tier 0 sobe ao tier 1 antes de `waiting_user`, e o "sim" do verificador barato em etapa com efeito externo é rejulgado (como no B14). Pré-condição para medir de novo o gpt-6-luna como ator | bateria de 28/09 (relatório §18) | M |
| 17.11 | **`eval_run.py` resiste a queda transitória** (`RemoteProtocolError`/`ReadError`: repete a leitura em vez de abandonar a execução em curso; K-045) | bateria de 28/09 | P |
| 17.9 | **Trilhas paradas com gatilho**: Alibaba qwen3-vl-flash, DeepSeek (dados na China), GUI-Owl local em 8 GB, destilação por LoRA | gatilhos no plano | G |

### Fase 18 — Conhecimento de app como dado e execução medida · 8 itens (pedido do dono de 28/09/2026; ADR-052)

Design: [`design/conhecimento-de-app.md`](design/conhecimento-de-app.md). Origem: a execução `r-20260928165254-e31953` e a pergunta do dono sobre operar o Outlook como o Instagram. Meta: zero Python por app.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 18.1 | **Digitação com conferência** (`automation/tools.py`): relê o campo, completa só o que faltou, não duplica, não aperta Enter com texto incompleto e devolve o que entrou | e31953 | P |
| 18.2 | **Fatia 1: telas como dado** (`automation/conhecimento_de_telas.py` + `telas.yaml` do pacote do app): Instagram idêntico, mais conversa, post, comentários e busca; a checagem de sessão volta ao estado conhecido antes de chamar pessoa; catraca contra sinais no Python | e31953, pedido do dono | M |
| 18.3 | **Projeção e orçamento por ação** (`taskqueue/projecao.py`): mediana e p90 de chamadas, tempo e US$ por (app, ação) no histórico; projeção no plano e em `GET /api/runs/{id}/projection`; aviso acima do p90; parada acima de `max(p90 × 2, p90 + 4)` | e31953 | M |
| 18.4 | **CI e parque na mesma máquina**: runner com prioridade ociosa, espera o parque ocioso, vitest com 3 workers (feito pela sessão Evolução) | e31953 | P |
| 18.5 | **Fatia 2: catálogo de ações como dado** (`catalogo.yaml`, carregador, `contract_version` real) e registro que descobre os pacotes em `app/conhecimento/apps/*/` (`integrations/app_declarado/pacote.py`) — some `planning/catalog/instagram.py` | pedido do dono (28/09) | G |
| 18.6 | **Fatia 3: fluxo de sessão declarativo** (login, dispensa, observar depois de enviar, conta errada) com uma máquina de estados no núcleo (`SessaoDeclarada`) e o `sessao.yaml` do app — some `integrations/instagram/` | pedido do dono (28/09) | G |
| 18.7 | **Fatia 4: app âncora do perfil pelo registro** (`ancora_do_perfil`, `pacote_ancora()` no lugar de `package_of_provider("instagram")`), bloco `contas:` no lugar de `instagram:` no `config.yaml`, links de perfil como dado; catraca do texto "instagram" no código de `app/` | pedido do dono (28/09) | M |
| 18.8 | **Fatia 5: conhecimento aprendido** (tela ou ação nova vira candidata com proveniência, validada e publicada como as skills; absorvido pelo 20.9, ADR-054) | proposta ao dono | G |
| 18.9 | **Nomes históricos sem app**: a tabela `instagram_profiles`/`instagram_credentials`, o prefixo de rota `/api/instagram/…` e o painel que os usa passam a nomes do perfil (migração nova, versão de contrato com rota antiga como apelido); revisar se `dm_*`/`comment_*` e os baldes do núcleo social são vocabulário de app ou de rede social | integração das fatias 2–4 (28/09) | G |

### Fase 19 — Falhas reiteradas do Instagram: diagnóstico medido e correções · 10 itens (pedido do dono de 28/09/2026; ADR-053)

Origem: as execuções `r-20260928165254-e31953` e `r-20260928195344-02ee9e` e a queixa do dono de que os planos de melhoria não funcionavam. Diagnóstico (causas C1–C12), correções e provas: [relatório §21](relatorio-validacao.md). Implantado em `93967d0`.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 19.1 | **UI ocupada não é sessão morta** (`automation/driver.py::DriverBusy`): a leitura relê com recuo sem recriar a sessão do Appium; ação com UI ocupada não se repete (efeito incerto); swipe sem pausa depois de encostar; scroll em faixa estreita usa a área rolável maior, e a sobreposição aberta volta sem IA (K-049) | C2, C6 (e31953, 02ee9e) | M |
| 19.2 | **ANR com sinal próprio e foco vivo** (`Adb.app_deaths` pelo `exit-info`; foco da seção viva, não da "LAST ANR"; K-047, K-048): `open_app` espera o foco; uma reabertura sem IA por etapa e, na segunda morte, falha com o motivo; prazo vencido vira `step_deadline` | C3, C9, C11 | M |
| 19.3 | **Prévia fora da fila do aparelho**: com a IA no controle, o painel recebe o frame da observação da IA (`FRAME_MAX_AGE_IA_S`, adendo v0.36); `drain` só da etapa; relógio não é acertado com objetivo em execução; aviso de pressão diz CPU ou RAM | C7, C12 | M |
| 19.4 | **Recuperação que preserva estado**: não faz force-stop do app vivo em primeiro plano; retoma da tela atual; atravessa o efeito comprovado sem repeti-lo (um LIKE repetido descurte); herda `commit_guard`, `bindings` e `draft_meta`; falha com motivo se a projeção não cabe no tempo restante | C4 | M |
| 19.5 | **Digitação atômica**: texto definido de uma vez (set text do UiAutomator2), `mobile: type` só como alternativa em pedaços; `AutoCompleteTextView` conta como campo; `typed_chars` é o que está no campo; `verified=false` sem campo identificado; regra nova de mascaramento do log do Appium | C5 | M |
| 19.6 | **Porta de sessão com contador vencido**: teto gravado antes do boot do emulador gera uma releitura `observe_only` por janela; launcher ou outro app na frente não soma `unknown_streak` e vai para o aviso do aparelho; "Verificar conta" não soma acima do teto | C8 | P |
| 19.7 | **Identidade do alvo pela legenda**: `caption_contains` em OPEN_POST (a pós-condição exige a legenda), `card_guard` em LIKE_POST, OPEN_COMMENTS e CREATE_COMMENT, `card_control` no balão dos comentários; sem a legenda na tela, `step_blocked`; tudo no `catalogo.yaml` | C10 | M |
| 19.8 | **Aprender só com prova e orquestrador**: `learn_from_run` só com todas as etapas `verified=true`, templatizando `bindings`, `band_guard` e `success_criteria`; fila do orquestrador só `running`/`paused`; saúde do aparelho nas sugestões | C12 | P |
| 19.9 | **Reinício a frio por interrupção acumulada** (K-050): fração irq+softirq entre duas sondas de saúde; aparelho ocioso acima de 15% em 3 sondas seguidas → `restart` rastreável, no máximo 1 a cada 6 h, nunca a escada de reparo | C1 (medido em 28/09) | P |
| 19.10 | **Prova com efeito (curtir e comentar)** num post que não seja o da e31953 (já curtido; repetir descurtiria). Dependia de autorização do dono, dada em chat em 28/09; feita em `r-20260928235215-6eb84c` (relatório §21) | pedido do dono (28/09) | P |

### Fase 20 — Aprendizado contínuo · 10 itens (pedido do dono de 28/09/2026; ADR-054)

Origem: o pedido do dono de 28/09 para o sistema aprender o tempo todo, com o que dá certo, o que dá errado e o feedback
de quem monitora, e evoluir a plataforma pelos erros da própria IA. Desenho sintetizado de três propostas avaliadas por
três juízes; D1 (publicação sozinha só sem efeito externo) e D2 (feedback implícito com botão opcional) adotadas pelas
recomendações em 29/09. Os itens 20.1–20.10 são os pacotes A0–A9 do desenho; o 20.9 absorve o 18.8. Provas: [relatório
§22 e §23](relatorio-validacao.md). Fundação (20.2) implantada com `c359f65` (29/09 ~03:55Z); A2–A9 (20.3–20.10)
integrados em `f497075` e implantados em 29/09 ~07:38Z, com lições em `shadow` e telas em `observe` até a prova real.
Domínio: [dominios/aprendizado.md](dominios/aprendizado.md).

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 20.1 | **Documentação (A0)**: ADR-054, adendo v0.37 do contrato, banco (055) e esta fase (29/09, madrugada); depois da leva A2–A9, `docs/dominios/aprendizado.md`, `ia.md` §15, `produto.md`, a fatia 5 em `design/conhecimento-de-app.md`, o adendo v0.38, K-058/K-059 e o relatório §23 | desenho do ADR-054 | P |
| 20.2 | **Fundação (A1)**: migração 055 (sete tabelas `learning_*`, `attempts.failure_kind`/`failure_screen`, `steps.failure_kind`); `app/modules/learning` com o ciclo D1 (`requires_owner = side_effect OR human_origin`, conferido no domínio e no `UPDATE`/`INSERT` do repositório), vocabulários fechados, classificador de falha com camada e "onde alterar", livro unido e promoção só por evidência real; `GET /api/aprendizado`, `/pendentes`, `/revisar`, `/{kind}/{ref}` e `POST …/status`; digest e curadoria sem IA; janela efetiva da projeção e régua diária durável. Integrado em `c359f65` | pedido do dono (28/09); `ai_calls` purgado em 14 dias sob janela de 30 | G |
| 20.3 | **Costuras (A2)** nos arquivos quentes, com dono único (`taskqueue/costuras.py`, no-op sem o livro): `failure_kind` gravado em `finish_attempt`, na reconciliação e na etapa; `ao_fechar_tentativa`; lições pedidas uma vez por tentativa e por planejamento por um seam só (`licoes_para`); `ao_resolver`/`ao_repetir`; tomada de controle (só ids) e respostas do assistente (campo + sha256) como sinais. Entregue em `fa7349e`; ficaram `failure_screen` sem escritor, o operador fora do sinal (`panel`), `takeover_gravar` e `cancelou_execucao` | desenho | M |
| 20.4 | **O que mais falha (A3)**: relatório e backlog por app, ação, tipo de falha e tela (US$ perdido + minutos + intervenções; falso positivo do verificador no topo), prova da correção (≥10 tentativas elegíveis com taxa ≤50% da base → `fixed`; reincidência nas últimas 2 × `prova_minimo` elegíveis), `GET /api/aprendizado/falhas`, `scripts/aprendizado-backlog.py` e top 5 na skill `retomar`. Entregue em `2adae8c` + `a5d2e97` (real: 19 grupos no central em 29/09); falta o top 5 na skill `retomar` | desenho; e31953 e 02ee9e | M |
| 20.5 | **Feedback D2 (A4)**: "deu certo / deu errado + motivo" sem modal e sem pergunta; "deu errado" por navegação rebaixa o que o item usou e aprendeu, com `desfazer` só para o que estava publicado; nota com cara de credencial → 409 `note_looks_secret`; `POST` e `GET /api/runs/{id}/feedback` e `GET /api/aprendizado/sinais`. Entregue em `9c40288` + `faf397e` | decisão D2 do dono | M |
| 20.6 | **D1 nos conhecimentos nativos (A5)**: fluxo nasce `candidate` e é publicado por concordância (reaproveitando a linha não ativa da `match_key`); receita e fluxo com efeito param em `validated`; o legado com efeito fica em "Revisar", com um gesto "confirmado" que só grava na trilha; escritor real de `skill_validation_results` (etapa confirmada à mão = `uncertain`). Entregue em `48783e8` + `aab27b8`; faltam o gesto "confirmado", a trilha em `PUT /api/flows` e `PUT /api/recipes` e o texto de `scheduler._learn_flow` para o candidato | decisão D1 do dono; revisão do A1 | G |
| 20.7 | **Painel Aprendizado (A6)**: abas Para aprovar (com Revisar e aprovação em lote), Aprendido, O que mais falha e Sinais; botão D2 em cada item; "Aprendizado desta execução"; contagem na barra do topo; rótulo da janela efetiva da projeção. Entregue em `c3c3760` + `86d9223`; o bloco `aprendizado` que "Aprendizado desta execução" lê não tem emissor no backend, "Revisar" só rebaixa, e o painel não lê a projeção (o rótulo da janela efetiva não tem onde aparecer) | desenho | G |
| 20.8 | **Lições medidas (A7)**: por contraste, com modelos fechados; `DecisionRequest.lessons` (120 tokens, 3 lições) e `PlanRequest.lessons` (150, 3), nunca no verificador; braço de controle por etapa, veredito com 8 unidades por braço, aposentadoria; nunca de autenticação, desafio, 2FA, CAPTCHA ou infraestrutura; `GET /api/aprendizado/licoes/previa`. Entregue em `9c016bb` + `ebfc643` + `3cd1e9f`; em `shadow` até a prova real | desenho | G |
| 20.9 | **Telas aprendidas, fatia 5 (A8; absorve o 18.8)**: tela desconhecida em etapa comprovada vira candidata com `ids_todos`; publicação sozinha com ≥3 observações em ≥2 execuções e prova local; `ConhecimentoDeTelas` unido na sessão (`com_aprendidas`, só `autenticada`, nunca em tela sensível); `GET /api/aprendizado/export` e absorção pelo YAML; `scripts/aprendizado-telas.py`. Entregue em `43600f1` + `476c3be`; em `observe` até a prova real | 18.8; ADR-052 | G |
| 20.10 | **Voz e preferências (A9, opcional)**: a voz da persona pelas aprovações editadas, sempre publicada pelo dono; preferência só como sugestão pré-preenchida, nunca resposta automática para ação com efeito, e decisão sozinha só nas versões da habilidade conferidas. Entregue em `9607f4d` + `0e971b9` + `0663738`; modos `off` de fábrica; o painel ainda não consome as sugestões | desenho | M |

### Fase 21 — Proteção de contas e pendências da rodada · 16 itens (pedido do dono de 28–29/09/2026; ADR-055)

Origem: cinco das oito contas do Instagram bloqueadas, a regra do dono de 29/09 ("toda vez que para nessa tela de
confirmar se você é humano é uma confirmação que a conta está bloqueada") e o pedido de 28/09 de resolver tudo o que
ficou em aberto (as pendências dos revisores do ADR-053). Diagnóstico, correções e provas: [relatório
§22 e §23](relatorio-validacao.md). Implantados: 21.6 (a parte do `e9da86e`), 21.7 e 21.10–21.14 (`7a02491`); 21.1–21.5,
21.6 (a parte da quarentena), 21.8 e 21.9 com `c359f65` (+ `2511b12`), em 29/09 ~03:55Z; 21.16 com `9348e9c`, em
29/09 ~04:17Z; 21.15 (os apps de segundo plano) com `f497075`, em 29/09 ~07:38Z.

| Item | O que | Achados | Tam. |
|---|---|---|---|
| 21.1 | **Detector único de conta travada** (`hierarchy.py`, `conhecimento_de_telas.py::detectar_conta_travada`): união dos idiomas, texto normalizado (apóstrofo tipográfico), conta travada sem exigir campo; roda depois de cada observação, antes do ANR, da receita e do ator, e na sessão; desfecho `auth_challenge` com subtipo (`conta_travada` bloqueia; `codigo` e `verificacao` pedem pessoa); `StepBlocked.kind="challenge"`; nada toca | contas bloqueadas (29/09), decisões (a) e (b) | M |
| 21.2 | **Quarentena do aparelho e marcador do android-04** (migração 054): `device_locked_accounts` por aparelho, que sobrevive ao desvínculo; só `stop`/`hibernate` sem `confirm_locked_account`; 409 `aparelho_em_quarentena` no vínculo e no cadastro; fora de entrega, escada, irq e scale-test; `locked_account_on_device` na saúde; `blocked_at`/`blocked_origin` e evento `profile.status`; `account_label` derivado | android-04 com o felipe no desafio e sem vínculo | G |
| 21.3 | **Frota por alvo**: `post_author`/`counterparty` em curtida e comentário; todos os baldes, janela de 30 dias e aprovações em aberto; uma conta por alvo em seguir, DM e comentário, com recusa; DM fria sempre com aprovação; mesmo pedido a várias contas só no objetivo de menor id; trava de fala atribuída a terceiro | ondas de 18–19/09, decisão (d) | M |
| 21.4 | **Disjuntor e conduta de login**: `_stop_reason` lê o status do perfil e o marcador; perfil bloqueado pausa as contas do mesmo alvo (48 h); nunca senha para `blocked`/`disabled`; credencial em `review` depois de 1 envio sem sucesso; `max_logins_per_day` | juliana (6 envios em 4h25), decisão (e) | M |
| 21.5 | **Verificador de DM**: `pending_marks` ("Sending…" = pendente), prova local `sent_text:<seletor>`, `confirm_done` com `evidence_id` (422 `evidence_required`/`invalid_evidence`) e o painel citando o print | evidências 942, 959–961, 968, 969 | M |
| 21.6 | **Escada sem reset com conta**: reinício por irq e religar da reconciliação fora da contagem (`requested_by` `saude`/`reconciliacao`, `e9da86e`); com vínculo ou marcador, o 3º degrau é "Precisa do dono" + `stop` (quarentena) | reset do andre em 24/09, decisão (c) | P |
| 21.7 | **Relógio do host** (K-055): `scripts/sincronizar-relogio.ps1` pelo `stripchart` e a tarefa `farm-relogio` (SYSTEM, 15 min), `w32time` sem sincronização própria; +6,240 s → +0,004 s | C12 do ADR-053 | P |
| 21.8 | **`hide_error_dialogs`: experimento e decisão** (K-054): com 0, o ANR do `system_server` prende o aparelho num diálogo que não some; com 1, morte silenciosa detectável pelo `exit-info`. Decisão: manter 1 | C3 do ADR-053, K-048 | P |
| 21.9 | **Aposentar com arquivo somente-leitura** (K-056): `rmtree` que libera o `pstore.bin` do emulador (`2511b12`) | android-17 (29/09) | P |
| 21.10 | **Leitura robusta** (`6799867`): `FalhaDeLeitura` relê sem recriar a sessão; `sessao_perdida()` reconhece a instrumentação morta; `card_guard` só fecha pela prova local; a evidência cita a legenda; métrica `automacao.leitura_falhou` | pendências do ADR-053 | M |
| 21.11 | **Aprovação acompanha a etapa revisada** (`caca1cd`): a decisão é reapontada para a etapa nova quando perfil, ação, alvo e texto são os mesmos e o efeito não disparou; "Tentar novamente" herda os textos | pendências do ADR-053 | M |
| 21.12 | **Legenda herdada** (`d63fcd9`): `inherited_bindings` declarado no catálogo (`caption_contains` em LIKE_POST, OPEN_COMMENTS e CREATE_COMMENT); `card_control` como lista | pendências do ADR-053 | M |
| 21.13 | **irq persistido** (`d121f76`): `measurements(kind='irq')` a cada sonda de saúde; `GET /api/desempenho?irq_horas=&irq_aparelho=` com o bloco `interrupcoes` | pendências do ADR-053 | P |
| 21.14 | **Receitas candidatas** (`01c4dc1`): receita da IA nasce `candidate`, comparada em sombra por execução da etapa, promovida com `ai.recipes_promote_after` (2) concordâncias; painel "Candidata" | desenho do aprendizado | M |
| 21.15 | **Causa do acúmulo de irq e apps em segundo plano**: os apps em segundo plano estão ENTREGUES — `android.desativar_apps` (`e2b54a0` + `b5036ec`) desativa 13 apps do Google no preparo, reversível e com os protegidos recusados na carga; real em 29/09: 11 desativados no android-01 e no android-06, `MemAvailable` de 670–960 MB para 974–1054 MB, Instagram ok (K-059). A causa do irq está MEDIDA (29/09, K-060): cerca de dois terços do excesso ocioso são o processo do Instagram logado rodando, em qualquer plano (HOME não mudou nada; `force-stop` levou o android-06 de 8,4% a 4,6%, e o tempo de sistema e as interrupções entre CPUs ao nível do aparelho de controle), e um terço é o tempo no ar (temporizador local 1,6x), que o reinício a frio zera. A CPU do emulador no host não muda com o app parado (148% antes e depois), então não há mecanismo novo: a faixa que derruba tarefas segue com o reinício automático. `virtio23` é o vsock do adb | K-050, K-059, K-060 | M |
| 21.16 | **O reparo espera a máquina aliviar** (`9348e9c`, no ar desde 29/09 ~04:17Z): com a CPU do central em `instances.remediation_host_cpu_max` (90%) ou mais, o reparo de aparelho local adia 10 min, com aviso no cartão, em vez de subir de degrau (101 desliga; a suíte usa 101). Resposta ao `reset` do android-01, que apagou a sessão do lucas com a máquina saturada pela IDE (29/09, 02:15Z) | incidente de 29/09 (K-058) | P |

### Transversal — prova, testes e documentação · 3 itens (17 achados: 7 P, 9 M, 1 G)

| Item | O que | Achados | Tam. |
|---|---|---|---|
| T.1 | **`relatorio-validacao.md` §11: a tabela dos 9 aceites** — real (id de execução/comando, data, máquina), simulado (arquivo::teste), não feito — atualizada ao fim de cada fase | #160 #19 #3 #49 #159 #175 #71 | M |
| T.2 | **Testes.** Relógio injetável (a suíte depende de tempo real); ciclo de vida real do emulador exercitado; 38 das 85 rotas sem teste HTTP; eventos `command.updated`/`worker.updated` e a tela de Infraestrutura sem teste | #164 #165 #166 #167 | G |
| T.3 | **Documentação e configuração de exemplo.** O README descreve a POC de uma máquina; o `config.yaml` versionado é o retrato da produção, e uma instalação nova nasce `degraded` com seis aparelhos fantasmas | #152 #170 #72 #54 #153 #177 | M |
| T.4 | **CI verde de novo** (backlog B13, 24/09): o CI falhava desde pelo menos `bfffb0d` por ambiente, não por comportamento — cofre sem chave mestra fora do Windows (harness passa a usar chave de ambiente falsa e fixa fora do Windows), testes de scripts PowerShell do Windows rodando no pwsh do Linux (restritos ao Windows), `apksigner` novo com formato `V2 Signer:` que o inspetor não lia (defeito real, corrigido), o mock de frame do Foco usando o `Blob` do jsdom, que o `undici` do Node 22 do CI não aceita, testes de saúde que dependiam de SDK/KVM do host e `cryptography` 46.0.3 com vulnerabilidades (50.0.0) | pedido do dono | M |

---

## 6. O que não dá para provar com o hardware de hoje

- **Worker em outra rede**: precisa de uma máquina fora da LAN. O item 4.5 entrega o mecanismo; a prova fica pendente.
- **Worker Linux**: precisa de uma máquina Linux com KVM.
- **Dez aparelhos simultâneos**: o central comporta 1 a 3 com a carga atual; depende da decisão 5.
- **Dois backends disputando aparelhos reais**: possível usando o notebook como segundo backend, mas só depois de 5.1.

## 7. O que continua fora, por decisão

- Ler ou digitar código de verificação, resolver CAPTCHA, desafio ou 2FA: **manual, sempre**. O plano melhora a
  operação em volta (6.4), não automatiza o desafio.
- Evasão de detecção de emulador ou de antibot.
- APK de espelho de terceiros: só Play Store com a sua conta, ou arquivo que você fornecer; `apks/` fora do Git.
- Senha nunca em resposta, log, evento, evidência, captura, prompt, memória, fixture ou Git.

## 8. Por onde eu começaria

Fase 0 inteira, nesta ordem: **0.1** (backup e reinício da produção — é o que protege os dados e põe em uso o que já
foi entregue), **0.2 e 0.3** (o desvio de autenticação é meu e é pequeno), **0.7** (o parque remoto está três quartos
inoperante e ninguém vê), **0.5** (destrava as DMs de hoje), depois o resto. Em seguida a Fase 1 — é ela que faz o
pedido original ser verdade nos dois caminhos.
