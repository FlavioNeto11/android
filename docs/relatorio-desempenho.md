# Relatório de desempenho e capacidade

Linha de base, cenários, resultados e decisões da evolução de desempenho pedida pelo dono em 26/09/2026. Os
contratos estão no adendo v0.20 de [`api-contract.md`](api-contract.md). A coordenação, os arquivos reservados e a
retomada estão em [`handoffs/evolucao-desempenho.md`](handoffs/evolucao-desempenho.md). Este relatório não repete os
documentos de cada área: aponta para eles.

**Prova.** Cada número traz o nível dele, e os três não se misturam:

- `real`: data, máquina e commit;
- `simulated`: `arquivo::teste` ou comando, com aparelho ou provedor falso;
- `not_run`.

Contagem simulada não é tempo real, nem US$ real, nem densidade de emuladores. Percentual de cenários diferentes
não se soma.

## 1. Ambiente e método

- **Central:** Windows Server 2025 Datacenter, 22 CPUs lógicas, 63,5 GB de RAM, Python 3.13.15.
- **Emulador e imagem:** emulador 37.1.11, `system-images;android-34;google_apis;x86_64`, WHPX,
  `-gpu swiftshader_indirect`.
- **Produção medida:** commit `57a155f`, migração 041, com `image_policy: auto`, `recipes: replay`, `flows: true`,
  hibernação ligada, captura da grade a 5 s e do foco a 1 s, `max_online_devices: 4`, `boot_parallelism: 2`.
  Lido em `GET /api/health` e `GET /api/settings`.
- **Worker remoto:** notebook Windows, pelo túnel SSH. Não há máquina Linux com KVM nem uma segunda máquina.

**Bancada** (`scripts/bench.py`, seguro por padrão; ver [`operacao.md`](operacao.md) §12):

- `bench.py simulado` usa o harness de testes: aparelho falso, provedor simulado, banco temporário e o SDK
  apontando para uma pasta que não existe, de modo que nenhum `adb` roda.
  - Cenários: prévia sem espectador (N aparelhos por T segundos); tarefa de QA com `image_policy` `always` × `auto`;
    primeira execução × repetição, com e sem receitas e flows.
  - O limite e a amostra mínima ficam gravados na linha de base: 10 % e n ≥ 3.
- `bench.py leitura --base http://127.0.0.1:8000` faz só GET em loopback e grava números agregados, nunca texto de
  comando nem dado de pessoa.
- `bench.py comparar antes.jsonl depois.jsonl` só declara ganho acima do limite e com amostra mínima, sem
  sobreposição entre repetições; fora disso o veredito é "exploratório".
- Todo arquivo registra SHA, se a árvore estava suja, data UTC, sistema e a configuração não sensível.

**Métricas novas no produto:**

- `backend/app/metricas.py`: contadores e distribuições em memória, com teto de séries e amostra fixa para
  p50/p95. Grava UMA linha por janela de 15 min em `measurements` (`kind='metricas'`).
- `GET /api/desempenho`: o acumulado do processo e as janelas. Com `?dias=N`, acrescenta o `historico`
  (`desempenho.resumo`), com p50/p95/n por entidade: objetivos, etapas, ações, `ai_calls`, comandos e boot. As
  taxas de sucesso, falha, incerto, espera humana e cancelamento vêm separadas, e intervalos sobrepostos não se
  somam.

## 2. Linha de base

### 2.1 Produção, somente leitura (`real`, 26/09/2026 22:12 UTC, `57a155f`, 7 dias)

Dado bruto agregado: [`desempenho/bancada/leitura-20260926T221211Z-21b98a1.jsonl`](desempenho/bancada/leitura-20260926T221211Z-21b98a1.jsonl).

| Medida | Valor |
|---|---|
| Custo de IA no período | US$ 11,38; US$ 0,165 por objetivo com IA |
| Custo por papel | decide US$ 7,93 (70 %), plan 2,75 (24 %), verify 0,63, social 0,07 |
| Chamadas | 718 no total, 9,3 por objetivo: decide 473, verify 161, plan 72, social 12 |
| Latência média (`avg_ms`) | plan 12,3 s, decide 3,1 s, verify 3,0 s. **Não há p50/p95 por papel nas GETs que existiam** |
| Fallback | 89 decisões pediram o ator local (qwen3) e foram respondidas pelo Sonnet 5 |
| Erros de IA | 16, dos quais 11 de cobrança (`billing`) |
| Tokens | novo 3,16 M; cache lido 2,02 M; cache gravado 0,22 M; saída 0,19 M |
| Etapas concluídas | 177 por IA, 106 por receita, 20 receita + IA |
| Boot a frio (Diagnóstico) | p50 151 s, p95 472 s (n = 22) |
| Acordar do snapshot | p50 22,7 s, p95 59 s (n = 16) |
| Hibernar | salvo: p50 2,4 s (n = 16); não salvo: cerca de 302 s (n = 3) |
| Host no instante da leitura | CPU 32,7 %; RAM 53,7 %; 3 emuladores somando 4,7 GB de RSS |

Cobertura de receitas por app, lida por GET em `/api/flows/cobertura` pela F3 (`real`):

- Instagram: 7 de 26 etapas com receita, em 9 fluxos;
- QA Messenger: 57 de 80, em 13 fluxos;
- Configurações do Android: 0 de 4.

Das reproduções de receita, 16 % voltaram à IA (22 `recipe+ai` em 7 dias).

### 2.2 Simulada (`simulated`, `bench.py simulado`, 3 repetições idênticas, código de `a0f251a`)

Dado bruto: [`desempenho/bancada/simulado-20260926T221402Z-21b98a1.jsonl`](desempenho/bancada/simulado-20260926T221402Z-21b98a1.jsonl);
depois: [`simulado-20260927T000948Z-815e35c.jsonl`](desempenho/bancada/simulado-20260927T000948Z-815e35c.jsonl).

| Cenário | Medida | Valor |
|---|---|---|
| Prévia, 3 aparelhos, 6 s, sem espectador | screencaps | 18 em `on_demand` e 18 em `always` (a bandeira ainda não fazia efeito) |
| Tarefa de QA, `image_policy` `always` → `auto` | chamadas com imagem | 12 → 3 |
| O mesmo cenário | screencaps | 16 → 16: o executor capturava mesmo sem enviar |
| Receitas + flows | chamadas de IA | 13 na primeira execução, 5 na repetição (plano 0, 4 de 6 etapas por receita); com o controle desligado, 13 e 13 |

## 3. Onde o tempo e o custo estão

**Custo, a parcela dominante (medida):** o ator (`decide`) responde por 70 % do gasto.

Pela tabela de preços padrão do código, a entrada nova sem cache do `decide` daria cerca de 51 % do total e a
saída do `plan` cerca de 17 %. Isso é **estimativa**, porque os preços de produção não foram lidos. As alavancas
proporcionais são:

- receita em vez de IA, que dá 13 → 5 chamadas por objetivo na repetição simulada;
- menos imagem por decisão;
- cache do prompt do ator.

**Tempo, o que se sabe (medido):**

- o boot a frio (p50 151 s, p95 472 s) é 6 a 20 vezes o acordar do snapshot (22,7 s) — a hibernação é a maior
  alavanca de latência que já existe;
- o `plan` leva 12 s em média.

Duração de objetivo, espera em fila e latência de comando **não saíam de nenhuma GET**. Passam a sair de
`GET /api/desempenho?dias=N` depois do deploy (`not_run` até lá).

**Trabalho desnecessário confirmado no código:**

- **Prévia:** 5 s por aparelho ligado, com ou sem espectador. Com 4 aparelhos no central são 48 screencaps por
  minuto; cada um é um PNG pelo ADB e duas codificações JPEG.
- **Imagem:** o `observe()` capturava e codificava antes de a política decidir se precisava dela, e a imagem ainda
  era recodificada uma terceira vez.
- **Worker remoto:** o screencap atravessava o túnel pelo ADB do central mesmo com `appium: local`.

O custo real de CPU e rede disso **não foi medido** (hipótese): exige o parque ligado e o `GET /api/desempenho`
depois do deploy.

**Hipóteses do pedido.** O veredito de cada uma contra o código está no
[checkpoint](handoffs/evolucao-desempenho.md) § Reconhecimento. As dez hipóteses do pedido se confirmaram.

**Refutadas ou só em parte verdadeiras** (achados da própria investigação):

- **"Resultado tardio reescreve o estado atual do aparelho"**: não era defeito. `_resultado_tardio` só registra o
  desfecho do comando, e `aplicar_desfecho_remoto` roda só no caminho em voo, sob o `op_lock`. Ficou um teste de
  guarda (`5e21a97`).
- **"Se o líder do desbravador falhar, os demais esperam até estourar"**: só metade é verdade. Quando o aparelho
  do líder saía do ar, os demais já eram soltos. Os defeitos reais eram outros:
  - a vida do líder medida pelo aparelho, e não pelo objetivo;
  - a corrida no re-despacho;
  - a espera invisível;
  - o agrupamento só por execução.

  Os quatro foram corrigidos na F3.
- **"Receita divergida continua escalando"** (comentário do `config.py`): o código nunca fez isso. O comentário
  foi corrigido, e escalar ficou como decisão do dono.

**Não medido nesta rodada:**

- CPU, rede e latência reais da prévia e da observação sob demanda;
- densidade de emuladores;
- troca de renderer.

Ver a seção 8.

## 4. O que mudou, por frente

A coluna "reversão" diz como voltar atrás sem novo deploy, quando isso é possível.

| Frente | Mudança | Onde | Reversão |
|---|---|---|---|
| F1 | Métricas agregadas, `GET /api/desempenho?dias=N`, `desempenho.resumo`, bancada, `eval_run.py` seguro sem `--yes` | `metricas.py`, `desempenho.py`, `api.py`, `scripts/bench.py`, `scripts/eval_run.py` | Só leitura; nada a reverter |
| F2 | Tela sensível fora da prévia: frame marcador sem imagem, `/frame` 404 `sensitive_screen`, captura pausada durante `type_secret`, VM-loja sempre marcador | `devices/manager.py`, `api.py`, `security/sensitive_input.py`, painel | A VM-loja volta a aparecer tirando `rt.store` de `_previa_sensivel` (decisão do dono) |
| F2 | Prévia sob demanda: `watch` por conexão (grade visível e foco, TTL de 5 a 60 s); painel antigo = grade em tudo; `paused`; um screencap serve a todos os espectadores; controle manual conta como foco; a grade não segura a hibernação | `devices/manager.py`, `devices/stream.py`, `api.py` (`/ws`), painel (`store/live.ts`, `api/ws.ts`, `DeviceCard.tsx`) | `PUT /api/settings {"preview_mode": "always"}`, sem reinício |
| F2 | Observação com a árvore primeiro: a imagem só quando a política, o julgamento, a evidência ou a divergência pedem; o PNG é decodificado uma vez e só se gera a codificação consumida; o login do Instagram lê só a árvore | `devices/manager.py` (`observe`, `completar_imagem`, `imagem_tardia`), `taskqueue/executor.py`, `integrations/instagram/authentication.py` | `ai.image_policy: always` no `config.yaml` volta a mandar imagem em toda decisão (exige reinício) |
| F3 | Funil de receitas (`receita.consulta`, `reproducao`, `retorno_ia`); desbravador visível (`wait_reason=pathfinder`), medido, liberado quando o líder falha, agrupado por compatibilidade do app; `aproveitamento` em `GET /api/flows/cobertura` | `taskqueue/recipes.py`, `scheduler.py`, `aproveitamento.py`, `social/capacidades.py` | Medição; o desbravador segue sob `ai.pathfinder_wait_s` (0 = desligado) |
| F5 | Recursos efetivos (cgroup v1/v2, `cpu.max`/cpuset, PSI) e reserva de RAM por boot no worker, tomada antes de qualquer `await` e liberada em qualquer desfecho; admissão do central com `mem_available`/`reserved_mb` e **recusa explicada com batida velha**; custo por imagem pelo perfil; exemplos sem `ram_mb: 1536` | `devices/recursos.py`, `worker/executor.py`, `worker/agent.py`, `workers/registry.py`, `workers/protocol.py`, `config/*.example.yaml` | O agente antigo segue funcionando (campos opcionais); o `config.yaml` de produção não mudou |
| F6 | Imagem do central (multi-stage, sem root) e compose de validação isolado (PostgreSQL e Ollama opcionais), com testes estáticos das invariantes | `deploy/`, `.dockerignore`, `main.py` (`CONTAINER_LISTEN_HOST`, recusado no Windows) | Não altera a instalação atual |
| F7 | Decisões sobre alternativas e matriz de executores (seção 6) | este relatório | — |
| F4 (fase A) | Transporte e posse: o NATS endereça a réplica hospedeira e aplica `ack_wait` (660 s, `in_progress`); a cerca é serializada por aparelho na transação; reentrega depois do `result_ack` não reexecuta (`fence_not_newer`); resultado tardio não reescreve o aparelho; `accepted_features` negociado no `welcome` | `commands/transport.py`, `commands/store.py`, `worker/agent.py`, `worker/diario.py`, `workers/registry.py` | Compatível nos dois sentidos; o NATS continua atrás da bandeira |
| F4 (fase B) | A imagem é capturada **na origem** para worker que anuncia e tem aceita `observe_local`. O agente faz o screencap pelo ADB local e codifica com `devices/codificacao.py`, e só o JPEG reduzido atravessa o túnel, por um WebSocket de mídia próprio (`/api/worker/midia`, token de uso único), fora do canal de comando. A hierarquia continua pelo Appium, que já roda na origem com `appium: local`: `uiautomator dump` derrubaria a sessão UiAutomator2. Entraram também a reserva de RAM no central (`_recusa_por_capacidade`, gravada antes de qualquer `await`) e o fechamento da entrega repetida do mesmo comando | `worker/observacao.py`, `workers/captura.py`, `api.py` (canal do worker), `devices/manager.py`, `worker-requirements.txt` (+Pillow) | Agente sem Pillow ou antigo não anuncia a feature e segue pelo ADB do central |

**Decisão do coordenador na F3.** A receita divergida **não** passou a escalar de modelo. O código nunca fez isso,
apesar de um comentário que prometia; o comentário foi corrigido e o teste
`test_receita_divergida_escala.py` fixa a decisão.

A escalada é uma decisão pendente do dono. O custo de ligar seria de 22 etapas por semana decidindo no modelo de
escalonamento, contra um tier 1 do `decide` que hoje já soma 102 chamadas e US$ 3,28. O que foi aplicado é só a
contagem `receita.retorno_ia`.

## 5. Resultados antes × depois

Tudo `simulated`: `bench.py comparar` de `21b98a1` (antes) contra `fe4b3eb` (depois da F2, F3 e F5), com limite de
10 % e n = 3. Repetido em `815e35c` (com a F4 e as correções da revisão), com os mesmos ganhos; o único outro
movimento foi de até 3 % num cenário de controle, abaixo do limite. Os arquivos estão em
[`desempenho/bancada/`](desempenho/bancada/comparacao-final.txt).

Validação no SHA integrado `21515a4`: suíte do backend com 1607 aprovados e 1 falha de ambiente; vitest 476/476;
`scripts/tests` 142.

| Cenário | Medida | Antes | Depois | Veredito |
|---|---|---|---|---|
| Prévia sem espectador, `on_demand` | screencaps em 6 s, 3 aparelhos | 18 | **0** (18 evitadas) | ganho |
| Prévia sem espectador, `always` | screencaps | 18 | 18 | igual; a volta atrás preserva o antigo |
| Tarefa de QA, `image_policy: auto` | screencaps | 16 | **9** (−44 %) | ganho |
| Repetição com receitas + flows | screencaps | 16 | **11** (−31 %) | ganho |
| Todas | chamadas de IA, objetivos concluídos | 13/5 e 1 de 1 | iguais | sem regressão |

**Aparelho remoto** (F4 fase B, `simulated`): com `observe_local`, o screencap PNG (o [`worker.md`](worker.md) registra cerca
de 673 KB em ~250 ms pelo túnel) é trocado pelo JPEG já no tamanho pedido. O ganho de rede real não foi medido
(`not_run`), porque depende de atualizar o agente do notebook.

**O que isso não prova:** CPU, rede, latência ou US$ reais, e quantos aparelhos cabem. O ganho real depende de
quantos aparelhos ficam ligados sem espectador e de quantas decisões dispensam imagem com a árvore de produção.

Para medir depois do deploy, sem gasto nem ação: `GET /api/desempenho` mostra `captura.evitada` por motivo e
`captura.total` por origem, e `bench.py leitura --dias 7`, repetido uma semana depois, compara com a seção 2.1.

## 6. Alternativas: executores, runtime Android, NATS e Kubernetes (F7)

A F7 foi só leitura, e os fatos externos vêm da documentação oficial citada no fim da seção. Das citações, duas
foram reconferidas pelo coordenador em 26/09:

- o `-gpu swiftshader_indirect` está obsoleto desde o emulador 36.4.9 ([Android
  Emulator](https://developer.android.com/studio/run/emulator-acceleration));
- o Docker Desktop não é suportado em versões servidor do Windows ([Docker
  Docs](https://docs.docker.com/desktop/setup/install/windows-install/)).

<!-- F7:INICIO (texto da frente F7, 26/09/2026) -->
### 6.0 Decisões por alternativa

| Alternativa | Decisão | Motivo | Gatilho para reabrir |
|---|---|---|---|
| Emulador nativo no Windows (WHPX), o de hoje | **adotado** | Medido aqui: `google_apis` com `-lowram` e 2048 MB custa cerca de 2,7 GB, e o aparelho acorda do snapshot em 14 a 18 s. Tem tradução ARM e GMS | — |
| Troca do renderer `swiftshader_indirect` | **piloto** (exige autorização) | A documentação oficial marca o modo como obsoleto desde o emulador 36.4.9, e o parque roda 37.1.11 com ele | A autorização para reiniciar um aparelho de teste; ou uma versão do emulador que remova o modo |
| Emulador nativo em Linux+KVM (worker 10.4) | **adiado** | Não há máquina Linux com KVM | Uma máquina Linux com KVM disponível |
| Emulador em contêiner com KVM | **adiado** | Só funciona em Linux com KVM; o Docker Desktop não é suportado no Windows Server; o suporte a snapshot não está documentado | O mesmo host Linux, e o braço A1 do protocolo (§2.4) já medido |
| Redroid | **rejeitado** para o parque atual | Exige kernel com binder (o WSL daqui não tem); não traz GMS nem Play Store, que só entrariam por pacote de terceiro; não tem snapshot documentado; a identidade do aparelho muda | Host Linux com binder, **e** um app-alvo que dispense GMS e Play Store (o de QA, por exemplo), **e** falta de densidade medida no emulador |
| API oficial do Instagram | **adiado**, sem piloto executável | Só atende conta profissional, e o repositório não registra o tipo das contas. Falta um app Meta. Cobre poucas operações do catálogo e não inicia DM, que é o uso principal | O dono fornece conta profissional, app Meta e autorização (§1.7) |
| Automação web do Instagram | **rejeitado** | Nova sessão e novo login fora do Android, sem ganho funcional sobre o executor atual. | Nenhum, dentro das invariantes |
| API privada do Instagram | **rejeitado** | Imita o app para falar com endpoints não públicos | Nenhum |
| Navegador de desktop para sites (login do ADR-025) | **adiado** | Existe uma única execução como demanda (`r-20260926161438-22d65f`); a regra do dono pede um segundo executor concreto antes de qualquer abstração | Uma tarefa de site recorrente, sem dependência de celular, com volume declarado pelo dono |
| NATS JetStream | **adiado**; o WebSocket (transporte local) continua | Há um só processo de controle; o outbox já dá durabilidade; o código NATS tem dois defeitos latentes (§3.1 e §3.2) | Dois processos de controle em produção, **e** o achado #27 resolvido, **e** os dois defeitos corrigidos e testados |
| Kubernetes | **rejeitado** | Os nós são Windows (sem device plugin nem GPU); o estado vive em memória (#27); o posicionamento dos aparelhos já é do agendador da aplicação | Pelo menos 3 hosts Linux com KVM, **e** o #27 resolvido, **e** necessidade medida de failover dos serviços centrais |
| Docker nos serviços do central | fora do escopo da F7 (é da F6) | — | Nota: o Docker Desktop não é suportado em Windows Server [DOCKER-win]; ver §2.2, achado 3 |

---

### 1. Matriz de executor por operação

#### 1.1 Regras do dono que valem para a matriz

1. **Sem troca de executor depois de efeito incerto.** Se o executor A tentou um efeito e o desfecho ficou
   incerto, a reconciliação é feita pelo próprio A (ler o estado) antes de qualquer nova tentativa. O executor B
   nunca repete o efeito "para garantir". O executor de cada etapa com efeito é decidido **antes** do efeito.
2. **A sessão do Android não vai para o navegador nem para a API.** Nenhum token ou cookie é exportado. Cada
   executor faz a própria autenticação, pelo canal próprio.
3. **Abstração só com um segundo executor concreto.** Não se cria interface nem adaptador vazio; o primeiro
   executor alternativo nasce como código concreto de uma operação.

Invariantes do projeto que cortam alternativas: segredo nunca em log, prompt ou Git.

#### 1.2 Operações usadas de fato

Fonte: `backend/app/planning/catalog/instagram.py` (igual nos dois commits).

- **Sessão (resolvida por código, `internal`):** `AUTHENTICATE_INSTAGRAM` e `VERIFY_ACCOUNT` usam
  `integrations/instagram/authentication.py` e `verification.py`. `LOGOUT` é `manual_only`.
- **Navegação, sem efeito:** `OPEN_FEED`, `OPEN_PROFILE`, `OPEN_POST`, `OPEN_COMMENTS`, `OPEN_INBOX`,
  `OPEN_THREAD`, `OPEN_FOLLOW_REQUESTS`.
- **Leitura:** `READ_MESSAGES`, `COLLECT_THREADS`, `COLLECT_COMMENTS`.
- **Efeito externo:** `LIKE_POST`, `UNLIKE_POST`, `LIKE_COMMENT`, `CREATE_COMMENT`, `REPLY_COMMENT`,
  `SEND_MESSAGE`, `FOLLOW`, `UNFOLLOW`, `ACCEPT_FOLLOW_REQUEST`, `DECLINE_FOLLOW_REQUEST`.
- **Publicar post, reel ou story: não está no catálogo.** Entra na matriz só porque a API oficial o cobre.
- **App de QA** (`qa-app/`, `com.pocqa.messenger`): login com conta fictícia, lista de conversas, conversa,
  perfil. A verificação independente é o `ContentProvider` exportado, lido por `adb shell content`.
- **Login com credencial em site pelo Chrome do Android** (ADR-025): `open_url` só abre endereço escrito pela
  pessoa, e `type_secret(name, element_id)` digita pelo `SensitiveInputChannel`, só em campo de senha e só no host
  da URL do comando.

**O que o repositório não registra:** se as contas das personas são pessoais ou profissionais (Business ou
Creator). Nenhum documento de `docs/` nem o modelo de perfil trazem esse dado. Como a API oficial só atende conta
profissional, a elegibilidade depende de uma **decisão ou informação do dono**. Não é um fato que eu possa afirmar.

**Uso real, medido pelo projeto:** o uso principal do dono é DM. Em 20/09 houve 29 interações, todas de saída,
com `SEND_MESSAGE` sempre iniciando a conversa (`docs/relatorio-validacao.md` §12.2).

#### 1.3 Executor Android (o caminho atual)

| Operação | Suporte real | Autenticação | Limitações | Custo | Pós-condição (como se comprova) | Estado no repositório |
|---|---|---|---|---|---|---|
| `AUTHENTICATE_INSTAGRAM` e `VERIFY_ACCOUNT` | sim, por código determinístico | credencial do perfil no cofre, digitada pelo canal sensível | — | um aparelho (cerca de 2,7 GB de RAM no host), sem IA | `verification.read_account()` lê a conta pela aba de perfil | implementado; perfis `session_ready` em produção |
| `OPEN_*` | sim | a sessão do app | sinais de tela são en-US; seletores medidos no app 447 | aparelho + IA (ou receita) | `element_present`, `local_proof` ou `model_judged` | implementado |
| `READ_MESSAGES`, `COLLECT_THREADS`, `COLLECT_COMMENTS` | sim, em qualquer conversa ou post que a conta veja | a sessão do app | até 20 itens por coleta (`collect_limit`) | aparelho + IA | `items_collected` | implementado |
| `LIKE_POST`, `UNLIKE_POST`, `LIKE_COMMENT` | sim | a sessão do app | limite `likes` da política | aparelho + IA | `local_proof` pelo seletor `desc==Liked` (na faixa do autor, no caso do comentário) | implementado |
| `CREATE_COMMENT`, `REPLY_COMMENT` | sim, em post próprio **ou alheio** | a sessão do app | aprovação por padrão; `REPLY_COMMENT` ainda sem prova em aparelho (roadmap 8.3) | aparelho + IA + rascunho | `model_judged` (o comentário aparece atribuído à conta) | implementado; 8.3 pendente de autorização |
| `SEND_MESSAGE` | sim, **inclusive iniciando conversa** | a sessão do app | aprovação por padrão; `failure_marks` | aparelho + IA + rascunho | `local_proof: sent_text` (o texto sai do campo e vira mensagem) | implementado; em uso real |
| `FOLLOW`, `UNFOLLOW`, `ACCEPT_FOLLOW_REQUEST` e `DECLINE_FOLLOW_REQUEST` | sim | a sessão do app | `UNFOLLOW` é `manual_only`; os demais pedem aprovação | aparelho + IA | `model_judged` (o texto do botão ou da linha) | implementado |
| Publicar | **não** | — | fora do catálogo | — | — | não implementado |
| App de QA | sim | conta fictícia `qa-user-NN` | só serve para QA | aparelho + IA | `ContentProvider` via `adb shell content` | implementado; aceite 2 |
| Login em site pelo Chrome | sim | credencial da execução (ADR-025), com consentimento | digitação só no host da URL do comando | aparelho + IA | a IA julga a tela seguinte ao login | implementado (ADR-025, 26/09) |

#### 1.4 API oficial (Instagram Platform, da Meta)

As páginas lidas descrevem duas configurações:

- **Instagram API com login do Instagram:** token de usuário do Instagram em `graph.instagram.com`; escopos
  `instagram_business_*`; dispensa Página do Facebook.
- **Instagram API com login do Facebook:** exige conta profissional ligada a uma Página do Facebook; a mensagem
  passa pela Messenger Platform.

As duas servem **só contas profissionais** (Business ou Creator). A página de visão geral não menciona conta
pessoal. [IG-platform] [IG-overview]

O acesso tem dois níveis. O Standard Access basta para contas que o próprio desenvolvedor possui ou administra, sem
App Review. O Advanced Access, para contas de terceiros, exige App Review e verificação da empresa. [IG-review]
[IG-overview]

Os limites gerais são uma cota diária de chamadas proporcional às impressões da conta (4800 × impressões em 24 h),
2 chamadas por segundo por conta na Conversations API e 100 por segundo na Send API (texto). [IG-overview]

| Operação | Suporte real na API | Autenticação e permissão | Limitações | Custo | Pós-condição | Estado no repositório |
|---|---|---|---|---|---|---|
| Sessão (`AUTHENTICATE`, `VERIFY`, `LOGOUT`) | não equivale: é OAuth, não sessão de app | OAuth da própria conta, com `instagram_business_basic` | um token por conta, e decidir onde guardá-lo; o ADR-025 cobre credencial digitada, não token OAuth | nenhum aparelho | leitura do perfil do dono do token | não implementado |
| `OPEN_*` | não se aplica: não há interface | — | — | — | — | — |
| `COLLECT_COMMENTS` | **parcial:** só nas mídias da própria conta [IG-comments] | `instagram_business_manage_comments` | post alheio fica fora; a doc recomenda webhooks para não bater no limite | não encontrei preço por chamada nas páginas lidas (**não verificado**) | a resposta da própria leitura | não implementado |
| `REPLY_COMMENT` | **parcial:** responder comentário em mídia própria [IG-comments] | `instagram_business_manage_comments` | só em mídia própria; a "resposta privada" (DM ao autor do comentário) permite uma mensagem, em até 7 dias [IG-private] | idem | ler as respostas do comentário pela API (mesmo executor) | não implementado |
| `CREATE_COMMENT` | **parcial:** só em mídia própria (moderação) [IG-comments] | idem | comentar em post alheio não aparece no conjunto documentado | idem | idem | não implementado |
| `READ_MESSAGES`, `COLLECT_THREADS`, `OPEN_INBOX` | **parcial:** conversas da conta profissional [IG-msg] | `instagram_business_manage_messages` | 2 chamadas por segundo; conversa em Requests parada há mais de 30 dias fica indisponível; sem grupo [IG-msg] [IG-overview] | idem | a resposta da leitura | não implementado |
| `SEND_MESSAGE` | **parcial:** só **responde** a quem escreveu antes, dentro de 24 h; uma etiqueta de agente humano estende a janela (o prazo exato não foi verificado) [IG-msg] | `instagram_business_manage_messages` | **não inicia conversa**, que é o uso principal medido; sem grupo; aviso de bot onde a lei exige [IG-msg] | idem | a mensagem aparece na conversa lida pela API | não implementado |
| `LIKE_*`, `UNLIKE_POST`, `FOLLOW`, `UNFOLLOW` e pedidos de seguir | **ausentes** do conjunto de recursos documentado (comentários, publicação, insights, menções, mensagens) [IG-login] [IG-overview] | — | — | — | — | — |
| Publicar | **sim:** imagem (só JPEG), vídeo, reel, story e carrossel de até 10 itens; 100 posts por API em 24 h, consultável em `content_publishing_limit` [IG-publish] | `instagram_business_content_publish` | sem filtros nem shopping tags [IG-publish] | idem | ler a mídia publicada | fora do catálogo; não implementado |
| App de QA | não existe API: o app é do projeto e o `ContentProvider` é oráculo, não executor | — | — | — | — | — |
| Login em site | depende de cada site; o portal do ADR-025 (CETESB MTR) **não verificado** | — | — | — | — | — |

#### 1.5 Automação web

| Operação | Suporte real | Autenticação | Limitações | Custo | Pós-condição | Estado no repositório | Decisão |
|---|---|---|---|---|---|---|---|
| Qualquer operação do Instagram por `instagram.com` num navegador de desktop | tecnicamente, parte do catálogo | **novo login** (a regra 2 proíbe reaproveitar a sessão do Android), portanto nova sessão e novo "aparelho" para a plataforma | — | navegador no central, não medido | DOM ou screenshot da página | não implementado | **rejeitado** |
| API privada (bibliotecas que imitam o app móvel) | — | imitam assinatura e aparelho do app oficial | fora do escopo do projeto | — | — | não implementado | **rejeitado** |
| Login em site com credencial fornecida (ADR-025) num navegador de desktop | viável em tese; não testado | a mesma credencial da execução, digitada pelo canal sensível; a sessão não vem do Android | — | navegador no central, não medido | DOM ou screenshot da página seguinte ao login | não implementado | **adiado:** não há segundo caso concreto além de `r-20260926161438-22d65f` |

#### 1.6 Equivalência funcional (resumo)

| Operação | Android | API oficial | Web |
|---|---|---|---|
| Sessão | sim | outra coisa (OAuth) | rejeitado |
| Ler DM | sim | parcial (conta profissional) | rejeitado |
| Enviar DM iniciando a conversa | **sim** | **não** | rejeitado |
| Responder DM dentro de 24 h | sim | sim (conta profissional) | rejeitado |
| Ler e responder comentário em post **próprio** | sim | sim | rejeitado |
| Comentar ou responder em post **alheio** | sim | não | rejeitado |
| Curtir, seguir, pedidos de seguir | sim | não | rejeitado |
| Publicar | não (fora do catálogo) | sim | rejeitado |
| App de QA | sim | não se aplica | não se aplica |
| Login em site | sim | depende do site | adiado |

#### 1.7 Existe piloto executável agora?

**Não.** Das três condições, só a primeira está atendida:

- **Documentação oficial:** existe, e foi lida acima.
- **Ambiente:** falta. Não se sabe se alguma conta é profissional, não há app na Meta, não há decisão sobre onde
  guardar o token OAuth (o ADR-025 cobre credencial digitada, não token) e não há endpoint HTTPS público para
  webhook. O webhook é dispensável no piloto: a leitura por consulta basta.
- **Autorização:** falta. Conta real exige autorização explícita em chat, e criar o app ou aceitar os termos da
  Meta é ato do dono.

**Recorte mínimo, caso o dono queira o piloto.** A lista abaixo é o que ele teria de providenciar e o que seria
feito.

1. Uma conta do Instagram **profissional** que seja do dono. Converter uma persona em conta profissional muda o
   perfil público, então a decisão é dele.
2. Um app Meta em modo de desenvolvimento, com essa conta com papel no app: Standard Access, sem App Review
   [IG-review]. Escopos mínimos: `instagram_business_basic` e `instagram_business_manage_comments`.
3. Uma decisão registrada em ADR: o token OAuth vai para o cofre (`SecretStore`), nunca para log, prompt ou Git, e
   nunca vem do Android.
4. A execução, feita como **um script concreto** e sem interface de executor:
   - **Passo A, sem efeito:** listar os comentários de um post próprio pela API e comparar com o
     `COLLECT_COMMENTS` do Android no mesmo post. As duas leituras são permitidas porque não têm efeito.
   - **Passo B, com efeito:** `REPLY_COMMENT` pela API num post próprio. É a mesma operação que o roadmap 8.3 quer
     provar no aparelho. A pós-condição é lida pela API, com as respostas do comentário. Um timeout ou erro 5xx
     vira `uncertain` e se reconcilia por essa mesma leitura; **o Android nunca repete o efeito**.
5. O aceite: uma resposta `succeeded`, com evidência e ids registrados como prova `real`.

**O que o piloto não resolveria:** curtir, seguir, pedidos de seguir, comentar em post alheio e **DM iniciada pela
persona**, que é o uso principal medido. Mesmo aprovada, a API ficaria restrita a atendimento de entrada numa conta
profissional. Por isso a recomendação é **adiar** até o dono dizer que esse é um caso de uso dele.

---

### 2. Runtime Android

#### 2.1 O que o produto usa (medido no repositório)

- **Emulador e imagem:** emulador 37.1.11, `system-images;android-34;google_apis;x86_64`, aceleração WHPX,
  `-gpu swiftshader_indirect` sem janela (`relatorio-validacao.md` §1).
- **Perfis de RAM** (`backend/app/devices/perfis.py`):

  | Imagem | RAM do AVD | Flags | Custo real medido |
  |---|---|---|---|
  | `google_apis` | 2048 MB | `-lowram` | cerca de 2,7 GB |
  | `google_apis_playstore` (a loja) | 4096 MB | — | cerca de 5,2 GB |
  | `default` | 1536 MB | `-lowram` | cerca de 2,4 GB |

- **Tradução ARM e GMS:** existem na android-34 `google_apis` (o `abilist` inclui `arm64-v8a`) e faltam na
  android-28 `default` e na android-34 `aosp_atd` (§7.1).
- **Hibernação:** salvar leva de 1,2 a 1,6 s; acordar até ficar pronto para automação leva de 14 a 18 s, contra 63 a
  197 s a frio. Sem `-lowram`, a android-34 ignora o snapshot (§7.3).
- **Contêiner:** "Redroid não foi tentado: o kernel do WSL desta máquina não tem `binder`"
  (`relatorio-validacao.md:380`, medido pelo projeto).
- **Máquinas:** o central é Windows Server 2025 Datacenter e o worker é um notebook Windows. Não há Linux com KVM.

#### 2.2 Comparação das três alternativas

| Aspecto | (a) Emulador nativo, perfil e aceleração corretos | (b) O mesmo emulador em contêiner com KVM | (c) Redroid |
|---|---|---|---|
| SO e virtualização | Windows: WHPX é o recomendado. O AEHD sai de cena em 31/12/2026, e o HAXM foi descontinuado [AND-accel]. Linux: KVM [AND-accel] | **Só Linux**, com KVM no host. O Docker Desktop no Windows e no macOS não é suportado, e o projeto é experimental [GOO-cont] | Linux com os módulos `binder_linux` e `ashmem_linux` (ou memfd) [REDROID]. No WSL, só com kernel compilado com binder e binderfs e configurado no `.wslconfig` [REDROID-wsl] |
| Aninhamento | A doc do emulador diz que não roda acelerado dentro de outra VM, nem de VirtualBox, VMware ou Docker [AND-accel] | O repositório de contêiner aceita nuvem com virtualização aninhada [GOO-cont]; as duas fontes divergem, e o que vale é o `/dev/kvm` do host Linux | Contêiner sem VM: compartilha o kernel do host |
| GPU e render | `-gpu host`, ou modos por software. `swiftshader_indirect` e `guest` estão **obsoletos desde a 36.4.9** [AND-accel] | GPU NVIDIA opcional, pelo NVIDIA Container Toolkit [GOO-cont] | `host` ou `guest` (software), escolhidos por parâmetro de boot [REDROID] |
| Play Store, GMS e login Google | Imagem `google_apis` traz Play services; `google_apis_playstore` traz Play Store, sem root [AND-avd] | Imagens `google_apis` e `google_apis_playstore`, API 30 a 35 [GOO-cont] | **Sem GMS nem Play Store.** Entrariam por Open GApps, MicroG ou MindTheGapps, montados na imagem [REDROID]. São binários de terceiro, em conflito com o ADR-008 e o ADR-022. A certificação do aparelho é **não verificada** |
| Tradução ARM | Medida: sim, na android-34 `google_apis` | A mesma imagem, então presumivelmente igual (**não verificado**) | As imagens prontas trazem `libndk_translation` [REDROID] |
| Instagram | Em produção | Não testado | Não testado. |
| Instalação | `adb install` pelo túnel; loja com Play Store | `adb connect` na porta 5555 [GOO-cont] | `adb` na porta 5555, que não deve ser exposta em rede pública [REDROID] |
| Snapshot e hibernação | Sim, medido. A doc diz que snapshot não é confiável com renderização por software e sugere Hardware ou Automatic [AND-snap]. O parque usa software e funciona com `-lowram`, mas esse é o limite de validade da medição | Não documentado [GOO-cont]: **não verificado** | Não documentado. A persistência é por volume em `/data` [REDROID], então parar e subir é um boot, não uma retomada da RAM |
| Invalidação de snapshot | Muda a versão do emulador, a imagem ou a configuração do AVD, e o próximo boot é a frio [AND-snap] | Igual, somada à troca da imagem do contêiner | — |
| Persistência de dados e sessão | O disco do AVD; o reset apaga a sessão | Volume do contêiner (detalhes **não verificados**) | Volume em `/data` [REDROID] |
| Evidência e retomada | Screenshot e árvore via ADB e UiAutomator2, já no produto | Iguais por `adb connect` (hipótese) | Iguais por `adb` (hipótese); sem snapshot, a retomada é um boot |
| **Neste ambiente** | É o que roda | **Impossível:** não há Linux com KVM, e o Docker Desktop não é suportado em Windows Server [DOCKER-win] | **Impossível sem autorização:** exigiria recompilar o kernel do WSL e mexer no `.wslconfig`, ambos reservados ao dono pelo CLAUDE.md; e o WSL2 já é uma VM |

**Achados colaterais para o dono e para a F5** (leitura da doc oficial, `not_run` aqui):

1. O parque usa `-gpu swiftshader_indirect`, que a página oficial marca como obsoleto desde o emulador 36.4.9
   [AND-accel]. As alternativas de software listadas são `swiftshader`, `swangle` e `lavapipe`; a de hardware é
   `host`. Trocar o renderer muda a configuração que o snapshot enxerga, e pode invalidar os snapshots e mudar a
   RAM, então exige uma medição (piloto, §2.4, braço A0′).
2. O projeto já mediu `-gpu host` na android-30: economiza cerca de 0,3 GB por instância, e o screenshot funciona
   (§7.2). A hibernação com `-gpu host` não foi medida.
3. O relatório §2.2 registra o Docker Desktop e o WSL abertos no central, com cerca de 10,7 GB. A página oficial
   diz que o Docker Desktop não é suportado em versões servidor do Windows [DOCKER-win]. Isso vale também para a F6.

#### 2.3 O que dá para concluir neste ambiente

Só a decisão documental. Não há host Linux com KVM (item 10.4 bloqueado), não há segunda máquina para o aceite 5 e
o WSL do central não tem binder (medido). Nenhum número de (b) ou (c) pode ser produzido aqui: a prova é
`not_run`. O que se pode medir aqui, com autorização para reiniciar um aparelho de teste, é o braço A0′ (o
renderer).

#### 2.4 Protocolo de comparação (executável quando houver hardware)

**Braços.** Cada um muda **uma** coisa em relação ao anterior:

| Braço | Configuração | O que isola |
|---|---|---|
| A0 | Emulador nativo, Windows e WHPX, perfil atual (android-34 `google_apis`, 2048 MB, `-lowram`, `swiftshader_indirect`) | a linha de base |
| A0′ | A0 com o renderer trocado (`swiftshader`, `swangle` ou `host`) | só o renderer |
| A1 | Emulador nativo em Linux e KVM, mesma imagem e perfil | o SO e o hipervisor |
| A2 | A1 dentro de contêiner (os scripts de contêiner do Google), no **mesmo host** | **só o contêiner**: é o único delta atribuível ao Docker |
| A3 | Redroid no mesmo host Linux | imagem, kernel e render juntos: a comparação é "Redroid como um todo" |

**Controles fixos.**

- A mesma revisão de imagem (menos no A3), a mesma versão do emulador, 720×1280 a 320 dpi, 2 vCPUs e 2048 MB.
- As mesmas versões de Appium, UiAutomator2 e adb.
- O mesmo app: **QA Messenger primeiro**, porque não tem efeito externo. O Instagram só entra com autorização.
- O mesmo fluxo fixo de QA (login, abrir conversa, enviar e verificar pelo `ContentProvider`).
- O host sem outras cargas, com a lista de processos registrada.

**Métricas.**

- **Boot a frio:** até `sys.boot_completed` e até ficar pronto para automação (launcher mais sessão do UiAutomator2).
- **Retomada:** hibernar e acordar; no A3, parar e subir o contêiner.
- **RAM no host por aparelho:** o working set do qemu, ou `memory.current` do cgroup no contêiner, 45 s após o boot
  e durante o fluxo.
- **CPU no host:** por aparelho, ocioso e durante o fluxo.
- **Latência de ação:** do toque à mudança da árvore, e do pedido de screenshot à imagem, em p50 e p95.
- **Taxa de sucesso comprovado:** etapas com pós-condição comprovada sobre o total.
- **Densidade:** quantos aparelhos simultâneos cabem antes de o p95 de ação dobrar ou o sucesso cair.
- **Instalação:** o tempo de instalar o APK de QA (71 KB) e, com autorização, o do Instagram (cerca de 243 MB).
- **Snapshot:** tempo de salvar e tamanho em disco.

**Passos.**

1. Registrar as versões: SO, kernel, emulador, imagem, driver de GPU e versão do Docker.
2. Fazer um boot de aquecimento e descartá-lo.
3. Fazer 5 boots a frio por braço.
4. Fazer 5 ciclos de hibernar e acordar por braço.
5. Rodar 30 vezes o fluxo de QA por aparelho, com 1, 2, 4 e N aparelhos.
6. Guardar a saída bruta em arquivo e resumir em p50 e p95.
7. Registrar a prova como `real`: data, máquina, commit e ids das execuções.

**Critérios de adoção e rejeição.**

- **A0′ é adotado** se o snapshot continuar funcionando (acordar em até 1,5 vez o A0), a RAM não piorar mais de 5%
  e o sucesso ficar igual. Caso contrário, o parque segue com o A0 até o emulador remover o modo, e reabre então.
- **A1 (worker Linux) é adotado** se ganhar pelo menos 20% de aparelhos por GB, ou acordar mais rápido, com o mesmo
  sucesso e o snapshot funcionando.
- **A2 é adotado sobre o A1** só se não piorar a RAM em mais de 5%, nem o boot e o acordar em mais de 10%, se mantiver
  o sucesso e o snapshot funcionar, **e** se houver um ganho operacional declarado (provisionamento reproduzível).
  Sem snapshot, é rejeitado, porque a hibernação é parte do produto (ADR-006).
- **A3 é rejeitado** se exigir binário de terceiro (GApps) ou contêiner privilegiado além de binder e
  `/dev/kvm`, ou se o sucesso comprovado cair. Só é considerado com app-alvo sem GMS e ganho de densidade de pelo
  menos 50%, porque perde a hibernação.

**O que NÃO atribuir ao Docker.** Nenhuma destas diferenças é efeito do contêiner:

- a troca de SO (Windows para Linux);
- a troca de hipervisor (WHPX para KVM);
- a troca de renderer (`swiftshader_indirect` para outro, ou GPU do host);
- a troca de imagem (`google_apis` para Redroid ou AOSP) e de kernel;
- a troca de versão do emulador;
- `-lowram` e `hw.ramSize`;
- o caminho do ADB (túnel ou local);
- a carga do host e a versão do Appium.

Só o delta A1 para A2, no mesmo host, mede o Docker.

---

### 3. NATS e Kubernetes

#### 3.1 O defeito relatado: **confirmado** por leitura (não exercitado)

A publicação e a assinatura usam **espaços de nome diferentes**: a publicação usa o id do *worker*, e a assinatura
usa o `OWNER_ID` da *réplica*.

| Onde | `1104d50` (produção) | `a0f251a` | O que faz |
|---|---|---|---|
| Publicação | `transport.py:135-136` | igual | `destino = envelope.get("worker_id") or self.owner_id`, publicado em `comandos.<destino>` |
| Origem do `worker_id` do envelope | `api.py:2087` | `api.py:2100` | vem da linha do outbox |
| Gravação da linha do outbox | `api.py:2141` e `2147` | `api.py:2154` e `2160` | `worker_id=(rt.worker_id if remoto else None)`, com `remoto = _para_worker(rt, action)` |
| Assinatura | `transport.py:98-99` e `127-128` | igual | assina **só** `comandos.<owner_id>`, com o durável `poc-<owner_id>` |
| Worker local | `workers/local.py:59` e `137`; `state.py:215` | `workers/local.py:59` e `137`; `state.py:219` | o aparelho do central tem `worker_id = OWNER_ID`, e por isso **coincide por acaso** |

**Consequência** (derivada da leitura; `not_run`). Com `COMMAND_TRANSPORT=nats`, os verbos de ciclo de vida
(`create`, `start`, `stop`, `hibernate`, `wake`, `restart`, `reset`) de aparelho num **worker remoto** vão para
`comandos.<id-do-worker-remoto>`, e ninguém assina esse assunto:

- nenhum backend assina outro assunto que não o próprio;
- o agente (`backend/app/worker/`) não importa `nats`: o único uso de `nats` no código é `transport.py`.

O stream `comandos.*` guarda a mensagem, porque a retenção padrão é Limits [NATS-streams]. O outbox a marca como
`sent`, o comando fica em `created` e o aparelho fica **trancado** por comando aberto (`store.py:158`,
`open_for_instance`) até o próximo reinício. No reinício, o comando vira `failed` com o motivo "reiniciou antes de
despachar" (`store.py:201`). O efeito dessa afirmação é verdadeiro, porque nada foi executado; a causa registrada é
enganosa.

Os verbos só-ADB (`install_apk`, `open_app` e as teclas) não são afetados: a linha do outbox deles tem
`worker_id=None`, e o destino cai no próprio `owner_id`.

**O nome certo não basta.** Executar um comando remoto exige o processo que **segura o WebSocket daquele worker**:
`registry.py:635-637` levanta `worker_offline` sem o link local. A chave de roteamento correta é a réplica que
hospeda o aparelho (`instances.hosted_by`, ou quem segura o link), não o `worker_id`. A doc repete a inconsistência:
`docs/parque-distribuido.md:449` diz "`comandos.<worker_id>` … consumido pela réplica que hospeda".

**Impacto hoje:** nenhum. Segundo a doc, a bandeira está desligada. Não conferi o `.env`, porque a leitura dele é
proibida. A cobertura de teste é só de construção (`test_outbox_de_comandos.py:188-192`); não há teste de
roteamento.

#### 3.2 Segundo defeito latente: o prazo de ack declarado não é aplicado

`ack_wait_s=300.0` é guardado (`transport.py:88` e `93`), mas nunca usado: a assinatura passa `config=None`
(`transport.py:127-129`). O consumidor fica então com o padrão do servidor, **AckWait de 30 s**, e **MaxDeliver -1**
(sem limite de reentregas) [NATS-ack].

Os prazos dos verbos chegam a 600 s: `start` 540, `restart` e `reset` 600, `hibernate` 400
(`devices/verbs.py:30-32`). Mesmo os 300 s pretendidos seriam curtos. Um `start` remoto seria reentregue a cada
30 s enquanto roda. A doc oficial descreve exatamente isso: sem ack de progresso, o servidor presume o worker
parado e entrega de novo [NATS-ack].

**Guardas que seguram o efeito duplo na maior parte dos casos** (leitura):

- no caminho remoto, `_do_action_no_worker` só despacha a partir de `created` (`api.py:1773` em `1104d50`);
- no caminho local, entrar em `running` duas vezes cai em `InvalidCommandTransition` (`api.py:1905`).

**Hipótese** (leitura, não exercitada): se houver pedido de cancelamento no instante da reentrega, os dois ramos
chamam `_fechar_cancelado` com "nada foi executado" ou "nada foi enviado" (`api.py:1777` e `1909` em `1104d50`),
enquanto a primeira entrega **ainda executa**. A transição `running` → `cancel_requested` → `cancelled` é permitida
(`states.py:25` e `27`). Seria um `cancelled` falso, que é exatamente o que o projeto proíbe.

**Direção da correção**, para a F4 na onda 2 ("assunto do NATS"). Não editei nada.

1. **Assunto pelo hospedeiro:** `comandos.<hosted_by do aparelho>`, ou o `owner_id` quando for nulo, gravado no
   outbox junto com o `worker_id`.
2. **Prazo explícito:** AckWait de pelo menos o maior `PRAZO_POR_VERBO` mais uma margem, **ou** ack de progresso
   (`+WPI`) periódico enquanto o handler roda [NATS-ack]. MaxDeliver também limitado.
3. **Deduplicação:** `Nats-Msg-Id = command_id` na publicação. A janela de duplicata de 2 min [NATS-streams] absorve
   a republicação do outbox.
4. **Retenção:** WorkQueue ou `max_age` no stream, para não acumular mensagem sem consumidor.
5. **Testes simulados de roteamento:** worker local, worker remoto e aparelho de outra réplica; reentrega com
   cancelamento pedido.

#### 3.3 Decisão sobre o NATS

**Adiado.** O WebSocket (`LocalTransport`) continua sendo o transporte.

- **Por que agora não.** Há um só processo de controle (`ROLE=all`) e um worker remoto pelo túnel. O outbox no
  banco já dá durabilidade à ordem (item 5.6). O efeito único vem da máquina de estados e do diário do agente, não
  do broker. Ligar o NATS acrescentaria um serviço a operar no Windows sem nenhum consumidor que precise dele, e
  com os dois defeitos acima.
- **Condições objetivas para ativar** (todas):
  1. dois ou mais processos de controle em produção, e um deles tem de mandar em aparelho hospedado pelo outro
     (`ROLE=api` separado de `ROLE=scheduler`, ou um segundo backend com aparelhos próprios);
  2. o achado #27 resolvido: o link do worker e o controle manual deixam de viver só em memória, ou o roteamento
     garante chegar a quem segura o link;
  3. os defeitos de §3.1 e §3.2 corrigidos, com teste simulado.
- **O que validar antes de virar a bandeira:**
  - **roteamento dono↔worker:** local, remoto e outra réplica;
  - **consumidores:** um durável por réplica; o que acontece com a réplica fora do ar;
  - **ack e prazo:** o comando de 600 s sem reentrega espúria;
  - **reentrega:** matar o processo no meio de um `reset` e confirmar que não há segundo wipe e que o desfecho é
    verdadeiro (`uncertain` ou `succeeded`);
  - **indisponibilidade:** broker fora na partida (falha alta) e no meio da operação (o outbox segura, e o laço
    republica a cada 15 s).
- **O aceite:** o já documentado, um comando real atravessando o broker até um aparelho, com o `Result` voltando.
  **Ack do broker não é efeito no Android:** só o `Result` do worker e a pós-condição fecham o comando.

#### 3.4 Decisão sobre o Kubernetes

**Rejeitado.**

- **Os nós são Windows.** O plano de controle é só Linux. Nó Windows não tem device plugin nem agendamento de GPU,
  só isolamento por processo, e não aceita contêiner privilegiado (a saída é HostProcess) [K8S-win]. Não há como
  entregar WHPX ou GPU a um pod; rodar o emulador em contêiner Windows é **não verificado** e, pela doc do emulador,
  inviável dentro de VM ou Docker [AND-accel].
- **O estado vive em memória** (achado #27, `banco.md` §"Pendências honestas"): `WorkerRegistry.live`, o controle
  manual, os frames e o barramento. Réplicas ou reagendamentos de pod perderiam links e leases, e um backend não
  pode compartilhar aparelho com outro.
- **Autoridade dupla.** O agendador do Kubernetes posiciona pods por recurso; o da aplicação decide rodízio,
  hibernação, `hosted_by` e cercas. Um pod de aparelho movido pelo Kubernetes é um boot a frio com o snapshot
  perdido. Volume local prende o pod ao nó, e o dado fica indisponível se o nó cair [K8S-local]. O device plugin
  resolveria o anúncio de `/dev/kvm` só em Linux [K8S-dev], não a autoridade sobre o aparelho.
- **Escala.** São duas máquinas e cerca de 10 a 15 aparelhos. Serviço do Windows, tarefa agendada e supervisor já
  cobrem o ciclo de vida.

**Gatilho objetivo para reabrir:** pelo menos 3 hosts Linux com KVM no parque, **e** o #27 resolvido (estado no
banco), **e** uma necessidade medida de failover automático dos serviços centrais (horas por mês de recuperação
manual acima de um teto que o dono fixar). Mesmo então, o Kubernetes orquestraria os serviços centrais; o
posicionamento de aparelhos continuaria com o agendador da aplicação.

---

### 4. O que depende do dono

1. **Conta profissional:** dizer se alguma conta pode ser profissional e, se quiser o piloto da API, providenciar
   conta própria, app Meta e consentimento OAuth. Criar conta e aceitar termos é ato dele.
2. **Onde guarda o token OAuth:** decidir por um novo ADR, porque o ADR-025 cobre só credencial digitada.
3. **Teste do renderer:** autorizar a reinicialização de um aparelho de teste para o braço A0′ (renderer
   obsoleto). A mudança pode invalidar snapshots.
4. **Hardware:** providenciar um host Linux com KVM (item 10.4), pré-requisito de A1, A2 e A3. Autorizar qualquer
   mudança no WSL ou no `.wslconfig`.
5. **Docker Desktop no central:** confirmar se ele continua no Windows Server, onde não é suportado, porque isso
   afeta a F6.
6. **Casos futuros:** declarar se há tarefa de site recorrente (executor web) ou necessidade de dois processos de
   controle (NATS).

### Fontes

Documentação oficial acessada em 26/09/2026. As frases foram resumidas; nenhuma citação passa de 15 palavras.

- [IG-platform] https://developers.facebook.com/docs/instagram-platform
- [IG-overview] https://developers.facebook.com/docs/instagram-platform/overview
- [IG-login] https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login
- [IG-publish] https://developers.facebook.com/docs/instagram-platform/content-publishing
- [IG-msg] https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/messaging-api
- [IG-comments] https://developers.facebook.com/docs/instagram-platform/comment-moderation
- [IG-private] https://developers.facebook.com/docs/instagram-platform/private-replies
- [IG-review] https://developers.facebook.com/docs/instagram-platform/app-review
- [AND-accel] https://developer.android.com/studio/run/emulator-acceleration
- [AND-snap] https://developer.android.com/studio/run/emulator-snapshots
- [AND-avd] https://developer.android.com/studio/run/managing-avds
- [GOO-cont] https://github.com/google/android-emulator-container-scripts (fora da lista de domínios do
  coordenador, mas é o repositório oficial do Google para o emulador em contêiner)
- [DOCKER-win] https://docs.docker.com/desktop/setup/install/windows-install/
- [REDROID] https://github.com/remote-android/redroid-doc
- [REDROID-wsl] https://github.com/remote-android/redroid-doc/blob/master/deploy/wsl.md
- [NATS-ack] https://docs.nats.io/learn/jetstream/acknowledgment
- [NATS-streams] https://docs.nats.io/nats-concepts/jetstream/streams
- [K8S-win] https://kubernetes.io/docs/concepts/windows/intro/
- [K8S-dev] https://kubernetes.io/docs/concepts/extend-kubernetes/compute-storage-net/device-plugins/
- [K8S-local] https://kubernetes.io/docs/concepts/storage/volumes/#local

**Não verificado:**

- o prazo exato da etiqueta de agente humano;
- o preço por chamada da API da Meta;
- os detalhes da Messenger Platform para Instagram (a página acessada era só um índice);
- a certificação Play e a integridade de aparelho Redroid;
- o snapshot em contêiner;
- a API do portal CETESB MTR;
- a automação de navegador de desktop (nenhuma doc consultada).
<!-- F7:FIM -->

## 7. Como ativar, acompanhar e reverter

- **Ativação.** Feita em 27/09 (seção 9): central e agente em `a90a6e1`. Para repetir num deploy futuro, o
  procedimento é:
  - o central pelo `deploy.ps1`, sem migração nova;
  - o agente do worker pelo `worker-install.ps1`, que muda o hash e dá `agent_outdated` até a atualização.

  A prévia sob demanda já vem ligada (`preview_mode: on_demand`). O painel antigo em cache continua funcionando
  como antes até a aba recarregar.
- **Acompanhamento:**
  - `GET /api/desempenho`: `captura.total` e `captura.evitada` por motivo, `observacao.ms`, `codificacao.ms`,
    `receita.*`, `pathfinder.*` e `capacidade.reserva`;
  - `GET /api/desempenho?dias=7` para o histórico p50/p95;
  - `GET /api/flows/cobertura`, campo `aproveitamento`;
  - no painel, os selos "Prévia suspensa" e "Tela sensível — prévia oculta", e o motivo "aguardando outro aparelho
    aprender o caminho".
- **Reversão:**
  - `preview_mode: always` por `PUT /api/settings`, sem reinício;
  - `image_policy: always` no `config.yaml`, com reinício;
  - o deploy anterior pelo procedimento de [`operacao.md`](operacao.md) §6. Não há migração a desfazer.

## 8. Pendências externas e o que não foi medido

Tudo o que dependia de autorização foi feito em 27/09 (seção 9). O que sobra:

- **Não medido:**
  - CPU do host antes e depois da prévia sob demanda, porque o `/api/metrics` dá só um retrato instantâneo;
  - densidade de emuladores, porque exige carregar o parque até saturar, e isso não se faz em produção;
  - runtimes em Linux, porque não há máquina.
- **Acompanhar em uma semana:**
  - `bench.py leitura --dias 7` contra a leitura de 27/09;
  - `receita.retorno_ia`, até n ≥ 30, para reavaliar a escalada.

## 9. Provas reais depois do deploy (27/09/2026, central `WIN-7S2UASNLFOP`, autorizadas pelo dono)

**Deploy.**
- Central implantado em `a90a6e1` por `scripts/deploy.ps1`, em 27/09 ~01:35 UTC (26/09 22:35 no horário local):
  backup `data/backups/20260926-223449`, `dist` recompilado, migração 041 (nenhuma nova).
- `GET /api/health` `ok` com `problems: []`, e `preview_mode: on_demand`.
- `GET /api/desempenho` responde 200 (antes era 404), e a porta 8010 escuta.
- O agente do notebook (`worker-lan-01`) foi atualizado por `worker-install.ps1 -Origem C:\farm\origem-a90a6e1`, que instalou o
  Pillow. Ficou em `0.1.0+a90a6e1`, sem `agent_outdated`, e o `worker.yaml` tem backup.

**Prévia sob demanda (prova `real`).** Medido em `GET /api/desempenho`, com 3 aparelhos locais no ar:

| Situação | Intervalo | Capturas de prévia | Evitadas |
|---|---|---|---|
| Nenhum painel aberto | 144 s depois da subida | 0 | 72 |
| Painel aberto no navegador, 2 aparelhos visíveis e 1 fora da tela | 30 s | 9 | 6, exatamente as do aparelho fora da tela |
| Painel fechado | 30 s | 0 | 26; os 4 aparelhos ligados em `paused` |

Pelo laço antigo, seriam 12 screencaps por minuto por aparelho ligado, com ou sem espectador.

**Imagem capturada na origem do worker (prova `real`).**
- android-09 (remoto) ligado pelo `start` `c-20260927013854-73d323`, `succeeded`, e aberto no Foco.
- A captura saiu pelo `observe_local`: métricas com `via=worker`, e o log diz "screencap (na origem)".
- Mesmo aparelho e mesma hora:

  | Caminho | Tamanho | Tempo |
  |---|---|---|
  | Worker, JPEG | p50 41 KB, p95 51 KB | p50 1,39 s (n = 4) |
  | ADB do central pelo túnel, PNG | ~696 KB | 1,66–2,12 s (n = 5) |

  São cerca de **17 vezes menos bytes pelo túnel**. O tempo é exploratório, porque a amostra é pequena.
- A primeira captura estourou 25 s: convidado recém-ligado, com load 19,9. Voltou sozinha pelo recuo.
- `capacidade.reserva{concedida, worker=worker-lan-01}` chegou ao central pela batida.
- O `stop` `c-20260927014228-f7a629` terminou `succeeded`, pelo agente novo.

**Histórico real de 7 dias** (`bench.py leitura --dias 7`, `desempenho/bancada/leitura-20260927T014241Z-a90a6e1.*`).
Primeiros p50/p95 reais:

| Medida | p50 | p95 |
|---|---|---|
| `decide` | 2,9 s | 5,1 s |
| `plan` | 10,4 s | 23,4 s |
| `verify` | 2,9 s | 4,3 s |
| Etapa por receita | 5,5 s | 10,8 s |
| Etapa por IA | 10,2 s | 63,6 s |
| Etapa receita + IA | 14,5 s | 74,3 s |

- Objetivos: 49 % concluídos, 17 % falhos, 29 % esperando uma pessoa, 1 % incertos.
- Custo: US$ 0,236 por objetivo concluído, com falhas e tentativas incluídas.

**B21: RAM do convidado (prova `real`).**
- **Causa encontrada:** o `config.yaml` já pedia 2048 MB desde 25/09, mas o `config.ini` de android-01, 02, 03, 04,
  07 e 08 ficou em 1536. A RAM só é reaplicada quando o aparelho liga, e android-01 e android-04 estavam no ar desde
  antes da correção.
- **Solução:** reiniciar a frio pelos comandos da plataforma, sem mudar configuração:
  - android-04: `stop` `c-20260927014414-b732ea` e `start` `c-20260927014438-778a66`, em 78 s;
  - android-01: `stop` `c-20260927014957-ac89a1` e `start` `c-20260927015010-f8cb8a`, em 207 s.
- **Critério definido antes:** MemAvailable > 400 MB, screencap p50 < 1,5 s, shell < 1 s e load1 < 4 depois de
  assentar. O primeiro critério falava em swap 0, mas o swap do Android é zram (RAM comprimida) e aparece até no
  controle de 2048 MB, por isso não serve.

| Aparelho | MemTotal | MemAvailable | Screencap | Shell |
|---|---|---|---|---|
| android-01 antes | 1,5 GB | 324 MB | 2,4–2,9 s | 162 ms |
| android-01 depois | 2,0 GB | 741 MB | 0,95–1,9 s (p50 1,08) | 104 ms |
| android-04 antes | 1,5 GB | 424 MB | 1,0–1,4 s | 196 ms |
| android-04 depois | 2,0 GB | 877 MB | 0,28–0,68 s | 75 ms |
| android-06, controle em 2048 | 2,0 GB | 768 MB | 0,6–1,0 s | 84 ms |

Dados em `desempenho/bancada/b21-antes-20260927.txt` e `b21-depois-20260927.txt`. Os aparelhos parados com 1536 (02,
03, 07 e 08) passam a 2048 no próximo `start`, porque a assinatura de hardware muda e o snapshot antigo é descartado
uma vez.

**Achado em conta real.** O `open_app` no android-04 (`c-20260927014708-f36d2c`) terminou `uncertain`.
- O `open_app` foi disparado como a prova de abertura do B21, supondo que o app do aparelho fosse o de QA. O `app_id` do android-04 é `instagram`, então foi um toque em conta real além do que o B21 pedia.
- Não dá para afirmar se o reinício influiu.
- Nada foi tocado na tela.
- A sessão do android-01 ficou `unknown`, como estava antes, e ninguém abriu o Instagram nele.

**Piloto do renderer, A0′ (prova `real`, `probe-image.ps1`).** AVD temporário, mesma imagem do parque, 2048 MB com
`-lowram`, porta 5600. Dados em `desempenho/bancada/renderer-20260927-a90a6e1.jsonl`.

| `-gpu` | Boot a frio | RAM privada | Acordar | Tela 45 s depois do boot |
|---|---|---|---|---|
| `swiftshader_indirect` (A0, atual) | 102 s | 3,32 GB | 4,3 s | tela inicial normal |
| `swiftshader` | 108 s | 3,37 GB | 4,3 s | **"System UI isn't responding"** |
| `swangle` | 204 s | 3,44 GB | 4,4 s, e sem rede depois | **"Process system isn't responding"** |
| `host` (GPU) | 110 s | 3,05 GB | 4,4 s | **"System UI isn't responding"** |

- Todos restauraram o snapshot: o uptime seguiu do ponto salvo (147–248 s logo depois de acordar em ~4,4 s). O
  `loaded_from_snapshot: false` gravado no JSONL é falso negativo do script, não do emulador.
  - **Causa** ([K-035](conhecimento/aprendizados.md)): o regex lia o `.log.wake` com o emulador ainda no ar, e o stdout
    redirecionado para arquivo só desce ao disco em blocos e na saída. Os quatro `.log.wake` têm a linha
    `Successfully loaded snapshot 'poc_hib'` no arquivo final, e o mesmo regex a encontra (releitura de 27/09).
  - **Correção:** `loaded_from_snapshot` passou a ser o veredito do uptime. Restaurado quando o uptime lido depois de
    acordar passa do tempo de parede decorrido até essa leitura mais 10 s de margem, porque num boot a frio o kernel
    nasce depois do processo. Uptime ilegível dá `null`. O JSON registra os dois sinais: `restored_by_uptime` (com
    `wake_elapsed_at_uptime_s` e `uptime_margin_s`) e `restored_by_log`, este só informativo.
  - Rejulgadas pela regra nova, as quatro linhas do piloto dão `true`, e a fase 0 de 17/09 separa o único boot a frio
    ([`relatorio-validacao.md`](relatorio-validacao.md) §7.3). Prova `simulated`:
    `scripts/tests/test_probe_image.py`. O probe corrigido não rodou (`not_run`: cria AVD e consome RAM do host de
    produção). O JSONL fica como foi gravado.
- **Decisão:** manter `swiftshader_indirect`. Os três alternativos reprovaram no critério "tela funcionando", e um
  diálogo de travamento da interface quebra a automação.
- **Reabre quando** uma versão nova do emulador remover o modo atual, ou mudar o comportamento. É o mesmo protocolo,
  com n ≥ 3 por braço.
- O `host` economizou 8 % de RAM, mas travou a interface e disputaria a GPU com o Ollama.

**Contêiner (prova `real` em Linux; o central continua nativo).** O workflow `.github/workflows/conteiner.yml`, run
36287055919 sobre `a9c1512`, passou em tudo:
- build com o commit;
- healthcheck `healthy` e `saude.py --pronto`;
- pela porta publicada, 401 sem token, e identidade e commit certos com o token;
- o volume sobreviveu a `down`/`up`;
- o contêiner roda sem privilégio e com o disco raiz somente leitura.

A primeira corrida (36286980773) reprovou no passo que chamava a API sem token. O comportamento estava certo e o
passo foi corrigido.

**Decisão sobre Docker e WSL no central:** não ligar.
- O Docker Desktop não é suportado em Windows Server.
- Custaria cerca de 10 GB da RAM dos emuladores.
- O engine religaria contêineres de outro projeto desta máquina: o `cartorio-api-1` publica `0.0.0.0:8000`.
- A validação do empacotamento fica no CI, a cada mudança do que entra na imagem e uma vez por semana.

**Escalar receita divergida (decisão do coordenador, com a delegação do dono de 27/09): não escalar.**
- **Critério definido antes da conta:** escalar só se as etapas `recipe+ai` concluírem 10 pontos ou mais abaixo das
  só de IA, com n ≥ 30, ou tiverem o dobro de repetições ou laços.
- **Medido em 7 dias:** `recipe+ai` concluiu 20 de 22 (91 %), contra 95 % só de IA. A diferença é uma etapa só, e as
  tentativas por etapa têm p95 = 1.
- **Custo de ligar:** US$ 0,0304 por decisão no Opus 5.5, contra US$ 0,0148 no Sonnet. Com 2 a 4 decisões por etapa
  divergida, seriam US$ 0,69 a 1,37 a mais por semana, 6 a 12 % do gasto.
- **Reabre quando** `receita.retorno_ia` e o histórico por `driven_by` juntarem n ≥ 30 com o critério atendido.
