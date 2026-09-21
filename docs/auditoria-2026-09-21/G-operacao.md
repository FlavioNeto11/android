# Tema G — Operação: o que sobe sozinho, backup, capacidade

15 achados. Voltar ao [índice](README.md) · [plano](../plano-100.md).

## #13 — Worker em outra rede não é possível hoje: o túnel (ADB e o reverso da API) é aberto pelo CENTRAL; não existe o lado do worker

`alto` · inacabado · esforço G · fase 4 · seção do pedido: 3, 4 e 9 (pendência declarada: worker em outra rede) · verificação: parcial

**O que é.** O canal de controle é iniciado pelo worker (WebSocket), mas o plano de dados (ADB) e até o acesso do agente à API dependem de o central conseguir fazer SSH para dentro do worker — impossível atrás de NAT alheio, que é a restrição de origem do pedido. O plano já declara `outra rede` como não exercitado; o ponto aqui é que a peça do lado do worker nem existe, e docs/worker.md a descreve como o caminho padrão que atravessa NAT.

**O que falta.** (1) Script/serviço do lado do worker (`worker-tunnel` invertido, Windows e Linux) que abre `ssh -R <porta>:127.0.0.1:<adb>` para um sshd do central com conta restrita (só port-forward) e `-L` para a API; alocação de portas por worker no central. (2) Corrigir docs/worker.md sobre quem abre o túnel hoje. (3) TLS (ou túnel obrigatório) para a opção (b) e conferência de host (`avaliar()`) no `/api/worker/ws` antes do `accept()`. (4) Ensaio real com o notebook em outra rede (4G/hotspot) medindo latência de ADB e a execução do QA Messenger.

**Evidência.** Get-ScheduledTask neste servidor: `farm-tunel-192.168.1.19` Running, argumentos `-Worker 192.168.1.19 ... -Mapa '15555:5555,...,15565:5565' -MapaReverso '18000:8000'` — o `ssh` parte do central para o worker (scripts/worker-tunnel.ps1:23-36, :90-110: `-L` e `-R` na mesma conexão iniciada daqui). `git ls-files | grep -i worker`: não há script para o worker abrir `-R` das portas de ADB até um sshd do central. docs/worker.md:97-99 descreve o túnel reverso como `o único que funciona quando o worker está atrás de NAT que você não controla` — falso enquanto for o central quem precisa alcançar o sshd do worker; a decisão 2 do dono em docs/parque-distribuido.md:18 é `túnel reverso iniciado pelo worker`, e :158-159/:323 admitem que isso não foi feito nem testado. netstat: backend só em 127.0.0.1:8000; `.env` não tem `API_TOKEN` nem `DATABASE_URL` (conferi só os nomes), logo a opção (b) nunca rodou em produção. `grep -i 'ssl|tls|https|wss' backend/app/main.py backend/app/config.py docs/worker.md` = vazio: seguindo a documentação da opção (b), a credencial permanente do worker trafega em claro. `/api/worker/ws` (api.py:1322-1346) autentica o worker pela primeira mensagem, mas faz `accept()` em :1330 antes de qualquer conferência e não passa por `avaliar()` (host/API_TOKEN), que só é chamado em main.py:94 e api.py:1216.

**O que o verificador corrigiu.** Conferi a tarefa agendada (Get-ScheduledTask), li worker-tunnel.ps1 inteiro, docs/worker.md:93-122, main.py:86-105, api.py:1205-1225 e :1322-1350, os nomes de variável do .env e o netstat. Precisão corrigida: `/api/worker/ws` não é aberto sem autenticação — ele autentica o worker na primeira mensagem (api.py:1340-1346); o que ele pula é o portão de host/API_TOKEN e o `accept()` vem antes de qualquer checagem. Acrescentei que docs/parque-distribuido.md já admite a lacuna, o que confirma o tipo `inacabado`; a severidade alta se sustenta porque `redes que não controlamos` é objetivo declarado (parque-distribuido.md:11-12,18).

## #14 — Não existe instalador nem serviço do agente (Windows ou systemd), nem procedimento de implantação/atualização; versão de protocolo só tem teto

`alto` · inacabado · esforço G · fase 10 · seção do pedido: 3 e 9 · verificação: parcial

**O que é.** O pedido (decisão 4 do parque e seção 9: implementação real) inclui instalador/agente que registre a máquina e rode como serviço. Hoje cadastrar um servidor é manual e sem roteiro completo; em Linux nada foi escrito nem testado (o worker real é Windows). Emuladores iniciados pelo agente são filhos do processo do agente e não são destacados: sem serviço definido, não se sabe se sobrevivem ao reiniciar/atualizar o agente. Não há como saber pelo painel que um agente está desatualizado.

**O que falta.** (1) `scripts/worker-install.ps1` (venv + worker-requirements.txt + worker.yaml + tarefa agendada S4U com gatilho de boot e reinício) e equivalente `worker-install.sh` + `farm-worker.service` (Restart=always). (2) Empacotar o agente (wheel/zip só com app.worker, app.workers, app.devices, app.config) e documentar a atualização; central mostra `agente defasado` comparando `agent_version`, e define protocolo mínimo. (3) Garantir que emuladores sobrevivam ao reinício do agente (processo destacado) e testar. (4) Corrigir o exemplo `avd_name` e os padrões de caminho por SO. (5) Validar o roteiro numa máquina limpa Windows e numa Linux.

**Evidência.** `git ls-files | grep -i -E 'systemd|\.service|worker|install'`: só scripts/worker-avd.ps1, worker-emulator.ps1 (registra tarefa do EMULADOR sem gatilho de boot — linhas 57-68: `Register-ScheduledTask` sem `-Trigger`), worker-tunnel.ps1 (roda no central), install-prereqs.ps1 (SDK) e backend/worker-requirements.txt; nenhum script registra `python -m app.worker` como tarefa agendada e não há unidade systemd. docs/worker.md passos 2-5 não dizem como o código do agente chega à máquina (`grep -n -i 'worker-requirements|git clone|venv' docs/worker.md` = vazio) e o passo 5 (:70-75) só descreve o serviço em prosa. backend/app/worker/__init__.py: `AGENT_VERSION = '0.1.0'` fixo; workers/registry.py:100-102 só recusa `hello.protocol > PROTOCOL_VERSION` (sem versão mínima, sem aviso de agente defasado); `VERBS` (executor.py:30) não tem atualização. backend/app/worker/settings.py:50-51 tem padrões Windows (`C:\Android\Sdk`, `C:\farm`); config/worker.example.yaml:66 sugere `avd_name: ""` para aparelho não gerido, que falha em settings.py:30 (`min_length=1`). O emulador é criado com `NO_WINDOW | NEW_GROUP` (devices/emulator.py:56-58), não destacado do processo/job do agente. Decisões do dono em docs/parque-distribuido.md:16,20: workers mistos Windows/Linux e `instalador/agente que registre a máquina`.

**O que o verificador corrigiu.** Refiz o `git ls-files`, li worker-emulator.ps1:47-78, docs/worker.md inteiro, worker/__init__.py, __main__.py, settings.py:25-64, registry.py:94-118 e worker.example.yaml. Correções de evidência: o exemplo inválido está em worker.example.yaml:66 (não :68) e o registro da tarefa sem gatilho está em worker-emulator.ps1:57-68. Acrescentei o fato que sustenta a dúvida sobre os emuladores (emulator.py:58 usa só NEW_GROUP, sem destacar) e que `backend/worker-requirements.txt` existe mas não é citado em doc alguma. O conteúdo do achado é verdadeiro.

## #136 — Depois de um reboot do servidor central só o túnel volta: backend (e com ele Appium e scheduler) não sobe sozinho e não há supervisor

`alto` · risco · esforço P · fase 10 · seção do pedido: 4 (confiabilidade) / aceite 7 · verificação: confirmado

**O que é.** O boot de hoje provou que o túnel volta; o backend só existe porque alguém o iniciou à mão numa sessão interativa. Logoff, reboot (Windows Update) ou crash derrubam API, scheduler e Appium até intervenção humana, com o agente do worker tentando reconectar no vazio. A fila persiste, mas a retomada depende de uma pessoa. Atenção à interação com o achado do Appium readotado: um supervisor que só reinicie o backend deixaria a saúde 'degraded' e o preenchimento de credencial bloqueado a cada queda.

**O que falta.** `scripts/start.ps1 -Instalar` versionado registrando o backend como tarefa AtStartup (S4U, RestartCount, no molde de worker-tunnel.ps1:68-74) ou serviço; watchdog de /api/health que reinicie o processo; documentar a ordem de subida (túnel -> backend, que sobe o Appium) e o que o scheduler religa sozinho (auto_start_devices); executar e registrar um reboot real do central com fila carregada (aceite 7 em campo, agora com comandos/workers/posse).

**Evidência.** Get-ScheduledTask no central: a única tarefa do projeto é 'farm-tunel-192.168.1.19' (MSFT_TaskBootTrigger, S4U, RestartCount 999); nenhuma tarefa cita start.ps1/app.main; pasta Startup vazia; chaves Run sem nada do projeto; Get-Service só acha postgresql-x64-17. Backend = PID 15688 (lançador do venv, o que está em data/backend.pid) -> 44396, `python -m app.main`, SessionId=1 (sessão interativa do console, logon 08:59), criado 21/09 16:05:24, processo pai 26032 já inexistente; iniciado por Start-Process em scripts/start.ps1:36-37. Boot do central 21/09 08:59:03 e log do túnel '2026-09-21 08:59:24 iniciando'. grep Register-ScheduledTask em scripts/: só worker-tunnel.ps1 e worker-emulator.ps1. O plano aprovado registra 'Central: backend desligado... nada sobe automaticamente aqui'.

## #137 — No worker, agente e emuladores não voltam após reboot; o serviço do agente não está versionado e o deploy é cópia manual

`alto` · risco · esforço M · fase 10 · seção do pedido: 4 (inscrição, reconexão) / 9 aceite 6 · verificação: confirmado

**O que é.** docs/worker.md passo 5 manda 'deixar como serviço', mas o repositório não entrega o script que faz isso nem a unidade systemd. Reiniciar o notebook deixa os 6 aparelhos e o agente fora até alguém entrar por SSH; se o processo do agente morrer, a tarefa não o relança (RestartCount=0). O central não tem como saber se o agente roda código velho (versão constante, cópia manual). O token de inscrição, já gasto e de uso único, segue exposto na linha de comando e no script - impacto baixo, mas é lixo que confunde a recuperação.

**O que falta.** Versionar scripts/worker-agent.ps1 -Instalar (tarefa AtStartup + RestartCount, sem --enroll após a inscrição, log com rotação) e unidade systemd; dar gatilho de boot às tarefas de emulador ou, melhor, o agente religar os aparelhos pelo estado desejado recebido do central (depende de gravar desired_state no caminho remoto); corrigir docs/worker.md:74 e :38; procedimento de atualização do agente (pacote versionado, agent_version derivado do commit, alerta na Infraestrutura quando divergir do central); executar um reboot real do worker e registrar a reconciliação (aceite 6).

**Evidência.** SSH somente leitura no worker (192.168.1.19): tarefas 'farm-agente' e 'farm-emulador-worker-01..06' com ZERO gatilhos (enumerando $_.Triggers e ignorando nulos) e RestartCount=0; último boot do worker 14/09 19:04, anterior aos arquivos de C:\farm (19/09 e 21/09): reboot nunca exercitado. scripts/worker-emulator.ps1:67 chama Register-ScheduledTask sem -Trigger, embora docs/worker.md:74 diga 'disparada no boot (o padrão de scripts/worker-emulator.ps1)'. C:\farm\run-agente.ps1, agente-tarefa.ps1 e reiniciar-agente.ps1 existem só no worker (`git ls-files | grep -i 'agente|systemd|.service'` = vazio). run-agente.ps1 e a linha de comando do processo mantêm `--enroll <token>` (valor mascarado na conferência) e redirecionam com `*>` para agente.log. C:\farm\agent não é checkout git (Test-Path .git = False); AGENT_VERSION = '0.1.0' constante (backend/app/worker/__init__.py:11). Worker roda Python 3.12.10; docs/worker.md:38 pede 3.13.

## #145 — Não existe procedimento de backup/restauração: banco em WAL, cofre preso por DPAPI a esta máquina e 64 GB de AVDs com as sessões

`alto` · inacabado · esforço M · fase 0 · seção do pedido: 3 (persistência) / 9 (migração, configuração e recuperação) · verificação: confirmado

**O que é.** Perder o disco ou trocar de servidor hoje significa: histórico de execuções perdido se a cópia não for consistente, as 8 credenciais do cofre irrecuperáveis (a chave não abre em outra máquina/usuário) e todos os logins refeitos à mão - inclusive a conta Google da VM-loja com 2FA e os perfis do Instagram, para os quais aparelho novo significa desafio de verificação. (memory_items está vazio hoje, então a memória social ainda não está em jogo.)

**O que falta.** scripts/backup.ps1: cópia consistente do SQLite com o backend no ar (sqlite3 .backup / VACUUM INTO) ou pg_dump; exportação da chave mestra para EnvKeyProvider guardada fora da máquina (ou recadastro documentado); cópia de config/ e .env sem imprimir; política para AVDs (snapshot a frio do userdata da loja e dos perfis, no central e no worker); scripts/restore.ps1 + ensaio real de restauração em pasta limpa registrado em docs; agendamento diário e retenção.

**Evidência.** `git ls-files | xargs grep -il 'backup|restaur|restore'`: nenhum script ou procedimento (os acertos são usos incidentais da palavra); única menção operacional é docs/banco.md:4 'backup é copiar'. data/: poc.sqlite3 58 MB + poc.sqlite3-wal 4,1 MB vivos (copiar só o .sqlite3 perde o WAL); data/credentials.key (262 B) embrulhada por DPAPI (security/secret_store.py:78-107, 'presa a este usuário e a esta máquina'); .env não define INSTAGRAM_CREDENTIALS_MASTER_KEY (só nomes conferidos); tabela secrets = 8 linhas, instagram_profiles = 8; data/avd = 64 GB (logins do Instagram e a conta Google da loja android-11) e C:\farm\avd no worker, sem cópia. O plano aprovado promete 'procedimento escrito, não descoberto na hora'.

## #17 — Cada batida do worker grava um evento `worker.updated` persistido: 57% do log de eventos desde a conexão são batidas

`médio` · risco · esforço P · fase 10 · seção do pedido: 6 · verificação: confirmado

**O que é.** Com um worker são ~8.640 linhas/dia só de batida; com 8 workers o limite de 5.000 do replay estoura em menos de 2 horas de desconexão do navegador, forçando resync do painel e enterrando os eventos úteis (comandos, estados) — inclusive a única linha onde o desfecho de um comando sobrevive hoje. Também escreve no SQLite a cada 10 s por worker.

**O que falta.** Emitir `worker.updated` persistido só quando mudar algo observável (estado, conectado, manutenção, inventário de aparelhos); mandar recursos (CPU/RAM/disco) como evento efêmero tipo `metrics`; limpar as linhas redundantes já gravadas; teste garantindo que N batidas iguais geram 1 evento.

**Evidência.** workers/registry.py:158-164 (`on_heartbeat` -> `on_change`) -> state.py:170-179 (`bus.emit('worker.updated', ...)`); backend/app/events.py:19 `EPHEMERAL_KINDS` não inclui `worker.updated`, então events.py:51-57 persiste cada um. Banco (leitura, mode=ro): 1471 eventos `worker.updated` entre 17:10Z e 21:22Z de 21/09, de 2573 eventos no período (57%), com apenas 2 textos distintos (`worker Notebook da LAN: online`). Replay do painel: api.py:1233 `count_since(last) > 5000 -> resync`; o painel guarda só os últimos 300 eventos (frontend/src/store/reducer.ts:55).

## #36 — Não há backup do banco nem do cofre

`médio` · risco · esforço P · fase 0 · seção do pedido: 3/4 (persistência, recuperação) · verificação: parcial

**O que é.** Copiar só o arquivo .sqlite3 com WAL ativo e backend rodando pode produzir cópia inconsistente ou sem as últimas transações (o tamanho do -wal por si não prova páginas pendentes, mas a cópia a quente sem a API de backup continua insegura). Não há rotina, retenção de cópias, equivalente para PostgreSQL, nem instrução de que o banco sem a chave do cofre não restaura credenciais — e a chave atual, por ser DPAPI, não restaura em outra máquina nem com cópia. Perder o disco do central hoje perde perfis, memória social, receitas e histórico.

**O que falta.** scripts/backup.ps1: SQLite via API de backup/`VACUUM INTO` (consistente com o backend no ar), PostgreSQL via pg_dump; incluir config.yaml e data/credentials.key, deixando escrito que a chave DPAPI só serve na mesma máquina/usuário — para recuperação em outra máquina, adotar INSTAGRAM_CREDENTIALS_MASTER_KEY guardada fora do host ou aceitar o recadastro; rotação N dias; tarefa agendada; scripts/restore.ps1 e um teste de restauração documentado em docs/banco.md.

**Evidência.** grep -i 'backup|pg_dump|VACUUM INTO|.backup(' em scripts/, docs/, README e backend/app: só docs/banco.md:4 ('backup é copiar') e o backupCount do log (main.py:35). db.py:83 liga WAL; AO VIVO: data/poc.sqlite3 58 MB + poc.sqlite3-wal 4,1 MB sendo escrito com o backend no ar. A chave do cofre fica em data/credentials.key (262 bytes), fora do banco e embrulhada por DPAPI — só abre com o mesmo usuário na mesma máquina (secret_store.py:78-107); INSTAGRAM_CREDENTIALS_MASTER_KEY não está no .env. Get-ScheduledTask: a única tarefa deste projeto é `farm-tunel-192.168.1.19` (a outra, 'device-farmer k8s tunnel', é de outro projeto).

**O que o verificador corrigiu.** A ausência é real e conferida. Corrigi o tipo de 'inacabado' para 'risco': backup nunca foi planejado (não está no plano nem nas 9 seções; decorre de 'persistência/recuperação'). Corrigi também a inferência de que o -wal de 4,1 MB estaria 'não checkpointado' (o tamanho do arquivo não prova isso) e acrescentei a restrição do DPAPI, que muda o que o backup precisa conter.

## #38 — Central e agente não são serviços supervisionados; recuperação de credencial do worker documentada mas inexistente

`médio` · inacabado · esforço M · fase 10 · seção do pedido: 4 (inscrição de worker) / aceite 7 · verificação: parcial

**O que é.** Toda a reconciliação de reinício (aceite 7) só acontece se algo religar o processo: hoje, reboot do central ou do notebook deixa backend/agente fora do ar até alguém subir na mão (o plano já registrava 'nada sobe automaticamente aqui'). E um worker que perdeu o arquivo de credencial não tem como voltar sem editar o banco, porque não há rota nem botão para remover/rotacionar a credencial — a documentação instrui um passo que não existe.

**O que falta.** scripts/install-central-service.ps1 e scripts/worker-agent.ps1 (tarefa agendada S4U no boot com reinício em falha, no molde de worker-emulator.ps1; unidade systemd para Linux); DELETE /api/workers/{id} e POST /api/workers/{id}/rotate-credential (recusando se houver comando aberto) + botão na Infraestrutura + testes de contrato; corrigir docs/worker.md:143.

**Evidência.** grep Register-ScheduledTask em scripts/: só worker-emulator.ps1 e worker-tunnel.ps1; start.ps1 (59 linhas) não tem auto-início nem watchdog; grep 'app.worker' em scripts/: nada (docs/worker.md:70-75 deixa a criação do serviço manual). Get-ScheduledTask ao vivo: deste projeto só `farm-tunel-192.168.1.19`; o backend de produção é um `python -m app.main` iniciado à mão em 21/09 16:05. docs/worker.md:143 manda 'remova o worker no painel', mas as rotas de worker são só GET/enroll/maintenance (api.py:1284,1289,1298,1311), o cliente do frontend só tem workers/worker/enrollWorker/maintenance (client.ts:304-309) e registry.py:104-107 recusa nova inscrição com `already_enrolled`. Plano E4 (linha 124) previa 'tarefa agendada no Windows / unidade systemd no Linux'.

**O que o verificador corrigiu.** Achado verdadeiro e conferido em código, documentação e estado do host. Única correção: esforço de 'P' para 'M' — são dois scripts de serviço, unidade systemd, duas rotas novas com teste, botão na UI e ajuste de documentação.

## #138 — Tarefa do túnel aponta para o caminho versionado do PowerShell da Store: a próxima atualização quebra o túnel no boot

`médio` · risco · esforço P · fase 10 · seção do pedido: 4 (confiabilidade) · verificação: parcial

**O que é.** O pacote MSIX do PowerShell muda de pasta a cada versão. Quando a Store atualizar, a ação registrada deixa de existir e no próximo boot o túnel não sobe (RestartCount não ajuda quando o executável sumiu): os 6 aparelhos remotos e o canal reverso do agente caem juntos, e o painel só mostra 'worker offline'. O túnel único é ponto único de falha de ADB e de controle.

**O que falta.** Instalar o PowerShell 7 por MSI/winget no central (o caminho estável 'C:\Program Files\PowerShell\7' hoje NÃO existe aqui) ou reescrever o laço para rodar em powershell.exe 5.1; em worker-tunnel.ps1 -Instalar, recusar/avisar quando (Get-Command pwsh).Source estiver sob WindowsApps; checagem em /api/health ou diagnostics de que a tarefa do túnel existe e que o executável da ação ainda existe.

**Evidência.** Get-ScheduledTask 'farm-tunel-192.168.1.19': Execute = C:\Program Files\WindowsApps\Microsoft.PowerShell_7.6.6.0_x64__8wekyb3d8bbwe\pwsh.exe (mesmo caminho na linha de comando do pwsh pai do ssh). Get-AppxPackage Microsoft.PowerShell: Version 7.6.6.0 com esse InstallLocation. Origem: scripts/worker-tunnel.ps1:48 `$exe = (Get-Command pwsh ...).Source`. No central, Test-Path 'C:\Program Files\PowerShell\7\pwsh.exe' = False (não há instalação MSI); no worker o pwsh está nesse caminho estável. O mesmo ssh carrega os seis -L de ADB e o `-R 18000:127.0.0.1:8000` do agente.

**O que o verificador corrigiu.** Fato confirmado na tarefa, no pacote e no script. Corrigi o o_que_falta: a sugestão original de 'preferir C:\Program Files\PowerShell\7\pwsh.exe' não funciona no central sem antes instalar o MSI, porque esse caminho não existe nesta máquina.

## #146 — RAM do central: cabe 1 emulador agora; o WSL pode tomar 56 de 63,5 GB e '10 instâncias' nunca passou de 3-4 simultâneas

`médio` · risco · esforço M · fase 10 · seção do pedido: 5 (escalonamento por RAM/CPU) / aceite 5 · verificação: confirmado

**O que é.** A capacidade local depende de uma carga alheia que pode crescer até 56 GB a qualquer momento; o central não reserva nem alerta. Com 0 emuladores ligados já só cabe 1. O rodízio mitiga (N contas sobre K vagas), mas o K efetivo hoje é 1-3, e a soma com o worker esbarra no teto fixo de 10 no código (4 locais + 6 remotos). '10 instâncias simultâneas' segue não demonstrado em nenhuma combinação.

**O que falta.** Decidir e escrever o alvo (K locais + M remotos); limitar o WSL (.wslconfig) ou mover os contêineres alheios; problema em /api/health quando additional_instances_that_fit < max_online_devices; trocar os tetos Field(le=10) por contagem por worker; alinhar README/config.yaml com o valor vivo; rodar um scale-test do parque inteiro (locais + remotos) e registrar.

**Evidência.** GET /api/diagnostics: mem_total_gb 63.5, mem_available_gb 10.1, capacity.additional_instances_that_fit=1, estimated_max_simultaneous=1, running_emulators=0; Win32_OperatingSystem agora: 5,3 GB de memória física livre. Get-Process vmmemWSL = 31,5 GB; `docker ps`: 14 contêineres gpv-* de outro projeto + farm-pg-teste. C:\Users\Administrator\.wslconfig: memory=56GB. data/scale-test-results.json: alvo 5 -> 3 online (2,9 GB livres); rotation-test-results.json: pico de 4. GET /api/settings: max_online_devices=3, enquanto config/config.yaml:139 diz 4 e README.md:249 'Só 4 aparelhos ficam ligados por vez'. backend/app/config.py:122 e :144 travam max_active/max_online em Field(le=10) (reconhecido em config.yaml:51).

## #180 — Worker Linux nunca rodou, e o agente não destaca o emulador do próprio processo fora do Windows

`médio` · não provado · esforço M · fase 10 · seção do pedido: Seção 2 (workers sob um contrato) + Seção 3 (Kubernetes não é pré-requisito; workers heterogêneos) + Aceite 5 · verificação: não verificado por cético

**O que é.** O suporte a Linux é só intenção: o código do agente é quase todo portátil (extensões de binário tratadas em sdk.py), mas nunca foi executado fora do Windows, e há pelo menos um ponto em que o comportamento muda por leitura do código: no Windows o emulador nasce num grupo de processos novo; no Linux ele herda a sessão/grupo do agente. Sob systemd (KillMode padrão = control-group), reiniciar ou atualizar o serviço do agente derruba todos os emuladores junto — exatamente o acoplamento que o projeto mediu e evitou no Windows ('processo em sessão SSH morre no logout').

**O que falta.** `start_process` com `start_new_session=(os.name != "nt")`; unidade systemd de exemplo versionada (com `KillMode=process`) e instalador Linux (SDK + KVM + agente); padrões de `sdk_root`/`work_dir` por SO; conferência de `/dev/kvm` no `hello`. Depois, uma rodada real num Linux com KVM: create → start → stop → hibernate → wake → reset pelo painel, RAM por aparelho medida (fecha a lacuna C do documento) e o resultado registrado como 'infraestrutura real'.

**Evidência.** docs/parque-distribuido.md:16 — decisão de entrada: 'Workers mistos (Windows e Linux)'; :129 — 'Não sobrou nenhuma medição … em Linux+KVM'. GET /api/workers: o único worker é `os: windows`. devices/sdk.py:10-11 — `NO_WINDOW`/`NEW_GROUP` = `getattr(subprocess, …, 0)`, isto é, 0 fora do Windows; devices/emulator.py:56-58 — `Popen(..., creationflags=NO_WINDOW | NEW_GROUP, close_fds=True)` sem `start_new_session=True`. docs/worker.md:75 e worker/__main__.py:3 mandam rodar como 'unidade systemd com Restart=always', mas `git ls-files | grep -i -E "systemd|\.service"` = vazio. worker/settings.py:50-51 — padrões `C:\Android\Sdk` e `C:\farm`.

## #39 — Retenção só cobre `events` e `evidence`; as demais tabelas crescem sem limite

`baixo` · dívida · esforço P · fase 10 · seção do pedido: 3/4 (persistência) · verificação: parcial

**O que é.** Não é urgente no volume atual, mas o ritmo (~15 MB/dia com poucas execuções) indica que com 6-15 aparelhos operando continuamente o banco passa de GBs em meses; tokens de inscrição usados/vencidos ficam para sempre; e o SQLite não devolve espaço sem VACUUM.

**O que falta.** Política por tabela em LimitsCfg (ex.: runs finalizadas > N dias -> apagar em cascata steps/attempts/actions/ai_calls agregando custo num resumo; commands terminais > N dias; worker_enrollments usados/vencidos > 7 dias); VACUUM/auto_vacuum periódico no SQLite; métrica de tamanho do banco no /health.

**Evidência.** state.py:590-610: o laço só chama bus.purge_older_than (events.py:115-119) e apaga evidence de runs antigas; grep 'DELETE FROM' em backend/app: nenhum DELETE para commands, ai_calls, attempts, actions, steps, runs, measurements, worker_enrollments (os demais DELETE são de exclusão explícita por rota: flows, recipes, apps, secrets, perfis, personas, memory_items). Retenção padrão de 14 dias para log e evidência (config.py:139-140). AO VIVO (leitura somente-leitura): 58 MB em 4 dias de uso (primeiro evento 17/09, último 21/09): events 62.053, actions 1.720, steps 1.221, evidence 987, ai_calls 935, attempts 882; worker_enrollments: 1 linha, já usada, sem expurgo.

**O que o verificador corrigiu.** Verdadeiro no essencial e com números reconferidos. Removi uma afirmação sem sustentação: o auditor dizia que, após a purga de evidence, 'attempts/actions continuam apontando para arquivos apagados' — no esquema (001_init.sql:111-171) é a tabela evidence que referencia a tentativa, não o contrário, e nenhum resultado de etapa guarda id de evidência; não há ponteiro pendurado. 'divida/baixo' correto.

## #143 — Cada batida de worker vira evento persistido: ~8,6 mil linhas/dia por worker, e só events/evidence têm purga

`baixo` · risco · esforço P · fase 10 · seção do pedido: 3 (persistência) / 6 (dado velho) · verificação: parcial

**O que é.** Com 14 dias de retenção são ~120 mil linhas (~160 MB) por worker só de batidas, cada uma custando UPDATE+SELECT+INSERT, e o ruído empurra os eventos úteis para fora da janela de 5000 do replay (uma aba parada por ~14 h já cai em resync só pelas batidas de um worker). Com 5-10 workers vira o maior escritor do banco. commands, ai_calls, measurements e attempts não têm purga, mas os volumes reais são pequenos (8, 935, 222 e 882 linhas em 5 dias). Correção ao achado original: o replay NÃO 'carrega tudo' - há teto de 5000 e resync.

**O que falta.** Persistir 'worker.updated' só em transição (online/offline/degraded/manutenção, mudança de inventário) e transmitir recursos como evento efêmero coalescido; o mesmo para a falha repetida de sessão (não gravar evento idêntico a cada tentativa); purga para commands/measurements/ai_calls antigos; VACUUM/auto_vacuum periódico no SQLite; métrica de tamanho do banco e de data/evidence em /api/diagnostics.

**Evidência.** backend/app/events.py:19 EPHEMERAL_KINDS = {frame, metrics, health.updated, apps.updated, settings.updated} - não inclui 'worker.updated'; workers/registry.py:158-164 (on_heartbeat -> on_change) -> state.py:170-179 (_publish_worker -> bus.emit); HEARTBEAT_S = 10. Banco vivo: 1455 eventos 'worker.updated' entre 17:10Z e 21:19Z para UM worker (1 a cada ~10,3 s), média 1336 bytes; 'instance.updated' 1555 hoje, dos quais 309/315/334 vêm do laço de falha de sessão de android-09/10/12. events: 62.091 linhas em 5 dias; poc.sqlite3 = 58 MB + WAL 4,1 MB. Purga: grep 'DELETE FROM' só acha events (events.py:117) e evidence (state.py:601) no _retention_loop (state.py:590-611); sem VACUUM. O replay do WebSocket é LIMITADO: api.py:1233 manda 'resync' acima de 5000 pendentes e events.py:104 tem limit=5000.

**O que o verificador corrigiu.** Mecanismo e números conferidos no código e no banco. Duas correções: o replay do WebSocket tem teto de 5000 com resync (a frase 'carrega tudo' era falsa) e a severidade caiu de médio para baixo - com um worker são ~11 MB/dia limitados pela retenção de 14 dias, sem dano funcional hoje; o ganho é de higiene e de preparo para vários workers.

## #144 — Rotação de log cobre só backend.log: appium.log, backend.err.log, agente.log e sobras de sonda crescem ou se perdem

`baixo` · risco · esforço P · fase 10 · seção do pedido: 6 (logs e evidências na visão de infraestrutura) · verificação: parcial

**O que é.** Num processo de longa duração o appium.log cresce sem teto até o próximo start do Appium, o err.log duplica tudo sem retenção, e o histórico do agente do worker é apagado justamente quando ele reinicia - que é quando se precisa dele para diagnosticar queda/reconexão. A tela de Infraestrutura promete 'logs' e não há caminho para ver o log do agente a partir do central.

**O que falta.** Rotação do appium.log por tamanho em execução (ou reinício programado do Appium ocioso); não duplicar o log no stderr quando não há console (ou RotatingFileHandler no lugar do redirect); retenção para probe-*, emulator-*.log e data/avd-probe; no agente, logging.handlers.RotatingFileHandler em C:\farm\logs em vez de `*>`; envio das últimas linhas do log do agente ao central sob demanda.

**Evidência.** backend/app/automation/appium_server.py:98 e :136-146: _rotate_log só roda quando o backend INICIA o Appium e o arquivo passa de 8 MB; appium.log tem 3,0 MB em ~4,5 h (laço de falha de sessão) e appium.log.1 9,5 MB. scripts/start.ps1:36-37 redireciona stderr para backend.err.log (duplicata do StreamHandler de main.py:37-38): 413 KB em ~2,3 h, truncado só em restart. data/logs: 111 arquivos (33 MB), dos quais 73 são probe-* de 17/09; data/avd-probe = 3,4 GB de sobra do probe-image. No worker (SSH leitura): run-agente.ps1 usa `*>` (sobrescreve agente.log a cada partida) e emulator-worker-0X.log não tem rotação. _retention_loop (state.py:590-611) só trata events, evidence e appium.log.1.

**O que o verificador corrigiu.** Verdadeiro; corrigi números e linhas: são 73 arquivos probe-* (não ~20), start.ps1:36-37 (não 38-39) e tamanhos atuais. Severidade baixa adequada.

## #181 — `worker-tunnel.ps1 -Instalar` derruba os laços de túnel de TODOS os workers, não só o do worker que está sendo instalado

`baixo` · defeito · esforço P · fase 10 · seção do pedido: Aceite 5 (dois workers) + Seção 4 (confiabilidade do transporte) · verificação: não verificado por cético

**O que é.** Ao cadastrar o segundo servidor (pré-requisito do aceite 5), instalar o túnel dele mata o laço de reconexão do primeiro. O `ssh` do primeiro continua vivo até a próxima oscilação de rede; quando cair, quem o levantaria já não existe (depende de o Agendador reexecutar a tarefa pela política de reinício, em até 1 min, o que não foi exercitado). É pequeno, mas é o tipo de defeito que só aparece no dia da demonstração com dois workers.

**O que falta.** Filtrar o encerramento do laço pelo worker (`-match 'worker-tunnel\.ps1' -and -match [regex]::Escape($Worker)`), e validar no `-Instalar` que nenhuma porta local do `-Mapa` já está em uso por outro túnel/tarefa `farm-tunel-*` (ou gerar o mapa a partir de `instances.external` do config, em vez de digitá-lo). Exercitar com dois destinos (o segundo pode ser o próprio central via 127.0.0.1) e registrar.

**Evidência.** scripts/worker-tunnel.ps1:55-59 — o encerramento do `ssh.exe` antigo é filtrado por `[regex]::Escape($Worker)`; já :61-65 mata qualquer `pwsh/powershell` cuja linha de comando case com `worker-tunnel\.ps1`, sem filtrar por `$Worker`. A tarefa é nomeada por worker (`$tarefa = "farm-tunel-$Worker"`, linha 41), ou seja, vários workers são o uso previsto. As portas locais vêm de `-Mapa` digitado à mão (linha 34), sem alocação central; colisão => `ExitOnForwardFailure=yes` (linha 96) e o túnel novo não sobe.
