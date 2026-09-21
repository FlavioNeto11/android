# Tema F — Segurança

19 achados. Voltar ao [índice](README.md) · [plano](../plano-100.md).

## #116 — Túnel reverso transforma a máquina do worker inteira em 'loopback' do central: API completa sem token a partir do notebook

`alto` · risco · esforço M · fase 0 · seção do pedido: 4 (confiabilidade/segurança do canal worker-central) e 3 (arquitetura distribuída) · verificação: confirmado

**O que é.** O modelo 'loopback = confiável' foi estendido, sem querer, a outra máquina. Qualquer processo local do worker pode chamar POST /api/admin/shutdown, POST /api/workers/enroll (cunhar worker falso), PUT de credencial de perfil, controle manual e leitura de perfis/e-mails — tudo sem credencial e sem Origin (chamada não-navegador não manda Origin, então o anti-CSRF de main.py:102-104 não se aplica). Comprometer um worker equivale a comprometer o central, o oposto do que a seção 4 pede. É a topologia de produção hoje, não um cenário hipotético.

**O que falta.** Fazer o caminho do worker exigir credencial mesmo por túnel: abrir um segundo listener/porta (segundo uvicorn.Server no mesmo processo, em 127.0.0.1) que sirva SÓ `/api/worker/ws`, sem isenção de loopback, e apontar o -R para ela; o agente só precisa do WebSocket autenticado, não da API REST. Atenção: corrigir apenas olhando o endereço do par NÃO resolve este caso, porque o par do túnel é 127.0.0.1 e o uvicorn (proxy_headers=True por padrão, forwarded_allow_ips=127.0.0.1) ainda aceitaria X-Forwarded-For vindo dele. Trocar o guarda do shutdown para exigir também um segredo local (arquivo em data/ lido pelo stop.ps1). Atualizar docs/worker.md retirando 'Zero porta nova em qualquer lugar'. Teste de contrato: requisição REST chegando pela porta do túnel sem token -> 401/404; WebSocket do worker pela mesma porta com credencial -> aceito.

**Evidência.** Reconferido por mim em 21/09: Get-ScheduledTask farm-tunel-192.168.1.19 tem -MapaReverso '18000:8000' nos argumentos; o ssh.exe vivo (pid 11576) carrega `-R 18000:127.0.0.1:8000` (montado em C:/git/android/scripts/worker-tunnel.ps1:96). A partir do worker (ssh somente leitura, imprimindo só o status HTTP), GET sem token em http://127.0.0.1:18000/api/workers, /api/instagram/profiles, /api/commands e /api/health -> 200 nos quatro. Motivo no código: C:/git/android/backend/app/security/access.py:61-63 isenta qualquer Host loopback e o par TCP que chega pelo túnel é 127.0.0.1; o guarda de C:/git/android/backend/app/api.py:135 (request.client.host in 127.0.0.1/::1) passa pelo mesmo motivo. O .env de produção nem define API_TOKEN (só nomes conferidos). No worker a porta 18000 escuta só em 127.0.0.1 e [::1] (netstat), e icacls C:/farm dá a BUILTIN/Users RX e criação de arquivos: o alcance é qualquer processo/usuário local do notebook, inclusive não-administrador, não a LAN. docs/worker.md:97-99 descreve este caminho como padrão, 'Zero porta nova em qualquer lugar' e 'o caminho de menor exposição'. ProfileDTO expõe email e login_identifier (backend/app/models.py:317-322). Rotas mutáveis alcançáveis pelo mesmo caminho: api.py:131 (admin/shutdown), :363 (PUT credencial de perfil), :796 (instalar app), :1118 (input manual), :1298 (workers/enroll).

## #117 — Isenção de loopback é decidida pelo cabeçalho Host, não pelo endereço do par: `Host: localhost` pula o API_TOKEN

`alto` · defeito · esforço P · fase 0 · seção do pedido: 3 (arquitetura distribuída) e 4 (confiabilidade/segurança) · verificação: confirmado

**O que é.** Com `server.host: 0.0.0.0` (opção b de docs/worker.md:101-114), qualquer cliente da rede envia `Host: localhost` (ou `test`/`testserver`, nomes de teste que ficaram no conjunto de produção) e passa sem token — HTTP e WebSocket do painel. A autenticação declarada como feita é contornável com um cabeçalho. Não é explorável hoje porque a produção escuta só em 127.0.0.1:8000, mas bloqueia qualquer saída do loopback. Precisão: POST /api/admin/shutdown usa request.client.host (api.py:135) e NÃO é afetado pelo Host forjado.

**O que falta.** Em `avaliar`, receber o endereço do par (request.client.host / websocket.client.host) e só isentar quando o PAR for loopback E o Host for loopback; manter o Host apenas para a defesa de DNS rebinding. Tirar `test`/`testserver` do conjunto de produção (injetar nos testes). Subir o uvicorn com proxy_headers=False (ou forwarded_allow_ips vazio) para X-Forwarded-For não reescrever o par. Teste novo: httpx.ASGITransport(client=('10.0.0.5', 1)) + `Host: localhost` -> 401, idem no WebSocket. Esta correção não cobre o túnel reverso (par 127.0.0.1) — ver o achado anterior.

**Evidência.** C:/git/android/backend/app/security/access.py:19 (LOOPBACK = 127.0.0.1, localhost, [::1], test, testserver) e :61-63 (`if nome in LOOPBACK: return None`), alimentado só por request.headers.get('host') em C:/git/android/backend/app/main.py:94 e websocket.headers.get('host') em api.py:1216 — request.client nunca é consultado. Executei (somente leitura) avaliar(host=..., authorization=None, publicos={'central.parque.local'}, token='t'): 'localhost', 'localhost:8000', 'test:8000' e 'testserver' -> None (passa); host declarado sem token -> 'unauthorized'; outro host -> 'forbidden_host'. Ao vivo, do MESMO par 127.0.0.1: curl com Host: localhost / test / testserver -> 200; Host: evil.example e Host: 192.168.1.10:8000 -> 403 — ou seja, só o cabeçalho decide, e os nomes de teste valem em produção. C:/git/android/backend/tests/test_autenticacao.py:106-111 e :165-168 só exercitam Host 127.0.0.1; a palavra `client=` não aparece no arquivo (nenhum teste de par remoto com Host forjado). netstat: produção escuta só em 127.0.0.1:8000 (pid 44396).

## #141 — Autenticação decide 'loopback' pelo cabeçalho Host, e o túnel reverso entrega a API inteira sem credencial a qualquer processo do worker

`alto` · defeito · esforço M · fase 0 · seção do pedido: 3/4 (arquitetura distribuída, inscrição de worker) · verificação: parcial

**O que é.** Dois problemas distintos. (1) Latente, no modo (b) de docs/worker.md (server.host=0.0.0.0 + API_TOKEN): um cliente não-navegador na rede passa sem token mandando `Host: localhost` - o portão protege de DNS rebinding, não de curl; o backend 'recusa subir sem token' e dá falsa sensação de proteção. Hoje o host vivo é 127.0.0.1, então este caminho não está exposto. (2) Vivo, no modo túnel: toda conexão que chega pelo -R tem par 127.0.0.1 de verdade e Host de loopback, então qualquer processo, job de CI ou usuário local do notebook alcança a API inteira sem credencial - criar execuções, regravar ou apagar credenciais e perfis (rotas PUT/DELETE citadas; não conferi rota que devolva o segredo) e até /api/admin/shutdown, cujo teste de par em api.py:135 também vê 127.0.0.1. Os mesmos usuários locais conseguem ler a credencial permanente do worker.

**O que falta.** (1) Isentar de token apenas quando o PAR (scope client) for loopback E o Host for loopback; teste com par externo + Host forjado, em HTTP e nos dois WebSockets. (2) Para o túnel, conferir o par NÃO resolve (ele é 127.0.0.1 de fato): abrir um listener dedicado em outra porta de loopback servindo só /api/worker/ws e apontar o -R para ele, deixando a 8000 fora do alcance do worker. (3) No Windows, gravar worker-credential.json com ACL só para SYSTEM/Administrators/usuário do serviço (icacls /inheritance:r) e corrigir a frase de docs/worker.md.

**Evidência.** backend/app/security/access.py:61-63: `nome = host_de(host); if nome in LOOPBACK: return None` - só o cabeçalho; as duas chamadas (main.py:94 no middleware HTTP e api.py:1216 no WebSocket do painel) passam apenas headers; grep 'request.client' só acha api.py:135 (shutdown). backend/tests/test_autenticacao.py simula 'de fora' apenas trocando o Host; não há teste com par não-loopback enviando Host: 127.0.0.1. Rotas de escrita ao alcance de quem passa pelo portão: POST /runs (api.py:1136), PUT e DELETE /instagram/profiles/{id}/credential (api.py:363 e :372), DELETE /instagram/profiles/{id} (:339), POST /admin/shutdown (:131). Vivo: o ssh do túnel roda com `-R 18000:127.0.0.1:8000`; do worker, GET http://127.0.0.1:18000/api/health -> 200 sem token (conferido por SSH). No worker rodam Runner.Listener/RunnerService (GitHub Actions), Docker Desktop e existem os usuários locais CodexSandboxOffline/CodexSandboxOnline. icacls C:\farm\worker-credential.json: BUILTIN\Users:(I)(RX); backend/app/worker/settings.py:111-112 só faz chmod fora do Windows, contrariando docs/worker.md:127-128 ('arquivo de permissão restrita'). config.yaml vivo: server.host=127.0.0.1.

**O que o verificador corrigiu.** Código, testes, túnel, resposta 200 sem token a partir do worker, ACL e cargas alheias no notebook: tudo conferido. Ajustes: separei o que é latente (modo b; o host vivo é 127.0.0.1) do que está exposto hoje (túnel); precisei 'credenciais de perfil' como escrita/remoção (rotas conferidas por leitura do código, sem executar POST/PUT/DELETE); e precisei o o_que_falta - checar request.client não protege o caminho do túnel, o discriminante é um listener dedicado. Severidade alta mantida: é a base de segurança da execução distribuída.

## #15 — Não há como remover um worker nem revogar/rotacionar a credencial dele, embora o documento mande fazer isso pelo painel

`médio` · defeito · esforço P · fase 9 · seção do pedido: 4 e 6 · verificação: confirmado

**O que é.** O procedimento de recuperação documentado é inexecutável: sem rota de remoção, um worker que perdeu a credencial só volta com UPDATE/DELETE direto no banco. Pior para segurança: uma credencial de worker vazada não pode ser revogada.

**O que falta.** `DELETE /api/workers/{id}` (recusa se houver comando em voo; desamarra `instances.worker_id`, derruba o link vivo) e `POST /api/workers/{id}/rotate-credential` (novo token de inscrição vinculado ao id existente, invalida o hash antigo); botões na Infraestrutura com confirmação; testes de contrato; ajustar docs/worker.md.

**Evidência.** Rotas de worker em backend/app/api.py: só GET /workers, GET /workers/{id}, POST /workers/enroll, POST /workers/{id}/maintenance (:1284-1319) e o WebSocket; frontend/src/api/client.ts:304-309 idem (workers, worker, enrollWorker, workerMaintenance). workers/registry.py:104-107: inscrição com `worker_id` já existente é recusada (`already_enrolled`), e não há método de remoção/rotação no registro. docs/worker.md:143, tabela Recuperação: `Perdi a credencial do worker -> Apague worker-credential.json, remova o worker no painel e inscreva de novo`.

## #60 — Painel não consegue autenticar fora do loopback: cliente não manda Authorization, não há tela de login, e WebSocket/<img> não podem carregar o cabeçalho

`médio` · inacabado · esforço G · fase 9 · seção do pedido: 3 / 4 · verificação: parcial

**O que é.** Com `API_TOKEN` e `server.public_hosts`, qualquer acesso ao painel por nome de rede recebe 401 em tudo (REST, WS, frames, evidências, avatares) e a interface só diz 'Tente novamente'. Hoje o painel só funciona aberto na própria máquina central (ou por túnel até o loopback dela). É limite declarado e documentado, e nenhum dos 9 aceites depende de painel remoto - por isso severidade média; mas é item pendente reconhecido ('tela de login') e a mensagem de erro atual não ajuda quem tentar.

**O que falta.** Sessão de usuário: rota de login que troca o token por cookie HttpOnly+SameSite aceito por `security.access.avaliar` em HTTP e WS (assim <img> e WebSocket autenticam sem expor o token ao JS); tela de login exibida ao primeiro 401; dicas específicas em `hintForError` para `unauthorized` e `forbidden_host`; `releaseAllLeasesOnUnload` passando pelo mesmo mecanismo; remover o endereço fixo do texto de erro de rede; testes de integração do fluxo 401 -> login -> snapshot -> WS.

**Evidência.** client.ts:184-187: os únicos cabeçalhos são Content-Type/Accept; grep -i 'authorization|bearer|token' em client.ts e ws.ts não retorna nada. ws.ts:66 `new WebSocket(wsUrl(lastEventId))` (navegador não permite cabeçalho) e backend/app/api.py:1216 só lê `websocket.headers.get("authorization")`; security/access.py `avaliar`: loopback passa sem token, host declarado em `server.public_hosts` exige Bearer, outro host é recusado. Imagens usam URL crua: DeviceCard.tsx:97 (`frameUrl`), EvidenceTab.tsx:107 e :157 (`evidenceUrl`), ProfilesPage.tsx:184 / ProfileDetail.tsx:80 (`profileAvatarUrl`). control.ts:116 faz `fetch` direto fora do cliente. `hintForError` (client.ts:100-120) não trata `unauthorized`/`forbidden_host` e fixa '127.0.0.1:8000' (:103). docs/worker.md:134-137 e docs/banco.md:180-181 admitem: 'não existe tela de login... o painel continua sendo aberto no central'.

**O que o verificador corrigiu.** Tudo o que o achado cita existe e confere; tipo 'inacabado' correto (pendência declarada). Rebaixei a severidade de alto para médio: o limite está documentado, há contorno simples (abrir no central ou túnel até 127.0.0.1, que passa sem token por desenho) e nenhum critério de aceite do pedido exige painel fora do loopback.

## #119 — Sem login, sessão nem identidade de usuário: frontend não carrega token, WebSocket do navegador não tem como autenticar e toda auditoria diz 'panel'

`médio` · inacabado · esforço G · fase 9 · seção do pedido: 6 e 7 (painel controla infraestrutura/VM remota) e 9 (aceite 2) · verificação: confirmado

**O que é.** Com API_TOKEN ligado, o painel só funciona aberto na própria máquina central. Não existe operador identificado: aprovações, releases, reset de dados e controle manual não têm 'quem'. Impede operar o parque de outra estação e qualquer trilha de auditoria.

**O que falta.** Sessão de usuário: POST /api/login (usuário/senha com hash argon2 ou, no mínimo, troca do API_TOKEN por cookie HttpOnly+SameSite=Strict de sessão), middleware aceitando cookie OU Bearer, e `/api/ws` aceitando o cookie (ou ticket de uso único via POST -> query param de vida curta). Frontend: tela de login, tratamento de 401 no client.ts e reconexão do live.ts. Gravar o usuário em commands.requested_by, aprovações e eventos.

**Evidência.** grep por authorization|bearer|api_token em C:/git/android/frontend/src (fora de testes) -> zero ocorrências; grep por 401|unauthorized -> zero; o único localStorage é de preferências (lib/storage.ts). `/api/ws` exige cabeçalho Authorization (api.py:1216-1219), que a API WebSocket do navegador não permite definir. `requested_by='panel'` fixo em C:/git/android/backend/app/api.py:1026 e padrão em commands/store.py:21; GET /api/commands ao vivo: 8 comandos, requested_by distintos = ['panel']. docs/worker.md:134-137 ('Limite honesto: não existe tela de login') e docs/banco.md:181-182 admitem a pendência.

## #120 — Nenhum TLS: API_TOKEN, credencial do worker, screenshots e evidências trafegam em claro na opção 'porta de rede'

`médio` · inacabado · esforço M · fase 9 · seção do pedido: 3 e 4 · verificação: parcial

**O que é.** O caminho (b) documentado entrega o segredo de longa duração (API_TOKEN a cada requisição; credencial do worker a cada conexão) a quem escutar a rede — realista em Wi-Fi com chave compartilhada, que é o caso do notebook. Não há exposição hoje (túnel SSH), mas worker em outra rede, que é o objetivo da seção 3, fica inviável com segurança.

**O que falta.** Lado servidor: suportar `server.tls_cert`/`server.tls_key` no config (repassados ao uvicorn) OU documentar e testar um proxy TLS (Caddy/nginx) na frente, com `conferir_exposicao` recusando subir fora do loopback sem TLS declarado. Lado agente: o mapeamento https->wss já existe; falta a opção de CA própria/ssl_context no worker.yaml (certificado privado é recusado pelo websockets.connect por padrão) e recusar `http://` para host que não seja loopback. Atualizar config/worker.example.yaml e docs/worker.md (inclusive a frase sobre NAT, que não vale para o túnel discado pelo central).

**Evidência.** C:/git/android/backend/app/main.py:123-124 cria uvicorn.Config sem ssl_certfile/ssl_keyfile; grep por ssl_|certfile|keyfile|tls|https://|wss em main.py, config.py, app/worker/*.py, docs/*.md, README.md e config/*.yaml só acha o mapeamento de esquema do agente e um link externo do README. config/worker.example.yaml:17 usa `server: http://192.168.1.10:8000`; o agente JÁ converte https->wss (backend/app/worker/agent.py:30-34), mas chama websockets.connect sem nenhuma opção de contexto SSL/CA (agent.py:156) e manda `token`/`enrollment_token` na primeira mensagem (agent.py:158-167). docs/worker.md:101-114 recomenda `host: 0.0.0.0` + API_TOKEN sem mencionar TLS nem proxy. Contexto conferido: hoje o tráfego worker<->central vai dentro do SSH (cifrado) e a produção escuta só em 127.0.0.1. O túnel é discado PELO central até o sshd do worker (scripts/worker-tunnel.ps1:110), logo um worker atrás de NAT que o central não alcança só tem a opção (b) — exatamente a sem TLS.

**O que o verificador corrigiu.** O núcleo é verdadeiro (nenhum TLS no servidor, doc da opção (b) sem TLS), mas o 'o que falta' original pedia 'agente com wss://', que já existe em agent.py:33 — corrigi para o que realmente falta (CA própria, recusa de http fora do loopback, TLS no servidor). Acrescentei o contexto de que hoje nada trafega em claro (túnel SSH) e de que a opção (b) é o único caminho para worker atrás de NAT. Severidade média mantida por ser condicional à opção (b).

## #121 — Credencial do worker não pode ser revogada nem rotacionada; o procedimento de recuperação documentado não existe

`médio` · inacabado · esforço P · fase 9 · seção do pedido: 4 (inscrição de worker) e 6 · verificação: confirmado

**O que é.** Credencial vazada ou notebook perdido: a única saída é editar a tabela `workers` à mão. Pior: quem perde o worker-credential.json fica com o worker_id travado (already_enrolled) sem caminho oficial de volta. O token_hash não tem validade nem rotação.

**O que falta.** `DELETE /api/workers/{id}` (derruba o link vivo, desvincula instances.worker_id, apaga a linha) e `POST /api/workers/{id}/rotate-credential` (invalida o hash e exige nova inscrição); botão na tela de Infraestrutura com confirmação; evento persistido; testes de contrato (worker revogado tenta conectar -> not_enrolled/bad_credential). Corrigir docs/worker.md:143 quando a ação existir.

**Evidência.** Rotas de escrita de worker em C:/git/android/backend/app/api.py: só `POST /workers/enroll` (:1298) e `POST /workers/{id}/maintenance` (:1311) — listei todas as rotas put/post/delete/patch e não há DELETE nem rotação de worker. `autenticar` recusa reinscrição de id existente com `already_enrolled` (registry.py:104-107). frontend/src/api/client.ts:304-309 só tem workers/worker/enrollWorker/workerMaintenance; grep por removeWorker|deleteWorker|revog em frontend/src -> nada; só InfraPage.tsx usa essas chamadas. docs/worker.md:143 manda 'remova o worker no painel e inscreva de novo' — ação que não existe.

## #122 — worker-credential.json fica legível por qualquer usuário local do worker (a doc diz 'permissão restrita')

`médio` · defeito · esforço P · fase 9 · seção do pedido: 4 (inscrição de worker) · verificação: confirmado

**O que é.** Quem ler o arquivo se passa pelo worker: conecta como worker-lan-01, derruba o link legítimo (attach substitui a conexão) e passa a receber despachos e devolver resultados falsos de 6 aparelhos — e, como não há revogação (achado anterior), o vazamento não tem remédio oficial. Proporção: a correção de ACL só protege contra principal NÃO administrador do worker; e hoje o mesmo atacante local já tem a API inteira pelo túnel (primeiro achado), então este ponto passa a ser o caminho relevante assim que aquele for fechado.

**O que falta.** No Windows, gravar com ACL explícita (icacls /inheritance:r /grant:r <usuário do serviço>:F e SYSTEM) ou embrulhar o valor com DPAPI reaproveitando `_dpapi` de security/secret_store.py; conferir e corrigir o arquivo já existente no notebook e a ACL de C:/farm; corrigir o comentário do código e docs/worker.md:127-128; teste no Windows que lê a ACL após write_credential.

**Evidência.** No worker (leitura via ssh, reconferida por mim): `icacls C:/farm/worker-credential.json` -> NT AUTHORITY/SYSTEM:(I)(F), BUILTIN/Administrators:(I)(F), BUILTIN/Users:(I)(RX); `icacls C:/farm` -> BUILTIN/Users:(I)(OI)(CI)(RX) mais (AD)/(WD), ou seja, a herança da pasta dá leitura (e criação de arquivos) a Users. C:/git/android/backend/app/worker/settings.py:107-112 grava o JSON em claro e só faz chmod fora do Windows, com o comentário 'no Windows a herança de ACL da pasta de trabalho é quem protege'. docs/worker.md:127-128 afirma 'um arquivo de permissão restrita no worker'.

## #123 — Chave SSH do túnel (sem passphrase) dá shell irrestrito de Administrator no worker

`médio` · risco · esforço M · fase 9 · seção do pedido: 4 e 3 · verificação: confirmado

**O que é.** Um único arquivo no central equivale a administrador do sistema operacional em cada worker. O túnel só precisa encaminhar portas; o desenho atual concede muito mais, e num parque com N workers vira movimento lateral central -> todos os hosts. A ausência de passphrase em si é necessária para a tarefa agendada subir sozinha no boot; o problema é a falta de restrição do lado do worker e o uso da conta Administrator. `accept-new` aceita às cegas a primeira chave de host de cada worker novo (TOFU).

**O que falta.** Criar no worker um usuário de serviço sem privilégio só para o túnel; autorizar a chave com `restrict,port-forwarding,permitopen="127.0.0.1:5555",...,permitlisten="127.0.0.1:18000"` e sem shell; registrar a chave de host do worker no known_hosts durante a inscrição (StrictHostKeyChecking=yes); mudar o padrão de -Usuario no script; documentar em docs/worker.md.

**Evidência.** Reconferido por mim: `ssh-keygen -y -P ''` abriu C:/Users/Administrator/.ssh/worker_ed25519 (sem passphrase; ACL local correta: Administrators, SYSTEM e o dono). Com a mesma chave executei `whoami` no worker -> conta administrator, além de icacls/netstat/Get-Content arbitrários. No worker, C:/ProgramData/ssh/administrators_authorized_keys tem uma linha cujo primeiro campo é `ssh-ed25519` (3 campos: sem prefixo de opções, logo sem restrict/permitopen/permitlisten/command=); o sshd_config ativo só tem o `Match Group administrators` padrão, sem AllowTcpForwarding/PermitOpen/ForceCommand. C:/git/android/scripts/worker-tunnel.ps1:24 usa `-Usuario Administrator` por padrão e :102 `StrictHostKeyChecking=accept-new`.

## #150 — Não há como remover ou reinscrever um worker: o procedimento de recuperação documentado é impossível

`médio` · defeito · esforço P · fase 0 · seção do pedido: 4 (inscrição de worker) / 9 (recuperação) · verificação: confirmado

**O que é.** Se o arquivo de credencial do notebook se perder (ou o disco for trocado), o worker-lan-01 não consegue voltar: não pode usar a credencial, não pode se reinscrever com o mesmo id e ninguém consegue removê-lo sem editar o banco à mão. Também não há revogação/rotação de credencial de worker comprometida - relevante porque hoje o arquivo é legível por BUILTIN\Users no notebook.

**O que falta.** DELETE /api/workers/{id} (recusando com comandos em voo, desvinculando instances.worker_id) e POST /api/workers/{id}/rotate-credential (gera nova inscrição que substitui o hash); botões na tela de Infraestrutura com confirmação; teste de contrato do fluxo 'perdi a credencial'; até lá, corrigir docs/worker.md:143 para o que de fato funciona.

**Evidência.** docs/worker.md:143: 'Perdi a credencial do worker -> apague worker-credential.json, remova o worker no painel e inscreva de novo'. Rotas de worker em backend/app/api.py: GET /workers (:1284), GET /workers/{id} (:1289), POST /workers/enroll (:1298), POST /workers/{id}/maintenance (:1311), WS /worker/ws (:1322) - nenhum DELETE nem rotação. frontend/src/api/client.ts:304-309: só workers, worker, enroll, workerMaintenance; InfraPage.tsx sem ação de remover. workers/registry.py:104-107: inscrição com worker_id já existente é recusada com 'already_enrolled'.

## #168 — Procedimento de recuperacao manda 'remover o worker no painel', mas nao existe rota, tela nem metodo para isso

`médio` · defeito · esforço P · fase 0 · seção do pedido: 4 (inscricao de worker) e recuperacao · verificação: confirmado

**O que é.** Se a credencial do notebook se perder (disco, reinstalacao), o worker-lan-01 fica trancado do lado de fora: nao da para reinscrever o mesmo id e nao da para remover o registro, a nao ser editando a tabela workers a mao. Tambem nao ha como revogar a credencial de uma maquina comprometida - o pedido lista inscricao de worker como requisito de confiabilidade, e a documentacao descreve um procedimento impossivel de seguir.

**O que falta.** DELETE /api/workers/{id} (recusa com 409 se o worker esta conectado ou tem comando aberto, a menos de force; desamarra instances.worker_id ou recusa com mensagem se houver instancias amarradas) e botao na InfraPage com Confirm - isso ja fecha o procedimento documentado. Em seguida, POST /api/workers/{id}/rotate-credential (token de reinscricao de uso unico que substitui o token_hash e derruba a conexao viva) para o caso de maquina comprometida. Testes de registry e HTTP; ajustar o docs/worker.md ao que existir.

**Evidência.** docs/worker.md:143: 'Perdi a credencial do worker | Apague worker-credential.json, remova o worker no painel e inscreva de novo'. Rotas de worker existentes (api.py:1284-1322): GET /workers, GET /workers/{id}, POST /workers/enroll, POST /workers/{id}/maintenance, WS /worker/ws - nenhum DELETE (os 7 @router.delete de api.py sao de flows, recipes, apps, perfis, credencial, personas e memoria). grep de 'DELETE FROM workers|def remove|def delete|def forget|def revoke|def rotate' em registry.py = 0; grep -i de 'remover|excluir|delete|revog' em InfraPage.tsx = 0; api/client.ts:304-309 so tem workers/worker/enrollWorker/workerMaintenance. registry.py:104-107 recusa com 'already_enrolled' qualquer token de inscricao para um worker_id que ja existe.

## #124 — /api/worker/ws aceita a conexão antes de autenticar, não passa por `avaliar` e não registra tentativa de credencial recusada

`baixo` · risco · esforço P · fase 0 · seção do pedido: 4 (inscrição/heartbeat de worker) · verificação: parcial

**O que é.** É o endpoint pensado para ficar exposto à rede, e qualquer cliente segura um socket por 30 s sem credencial, quantas vezes quiser (classe slowloris, comum a qualquer servidor HTTP sem proxy na frente). Tentativas com credencial errada não deixam rastro — o operador não fica sabendo que alguém está tentando se passar por um worker. A falta de conferência de Host/Origin não é explorável sozinha: sem credencial válida o handshake é recusado.

**O que falta.** Conferir Host (public_hosts) antes do accept; reduzir o prazo do hello (o agente monta o hello sem sondar aparelho, agent.py:56-59) e limitar conexões pendentes por IP; registrar `bad_credential`/`not_enrolled` como evento persistido com worker_id e IP, com bloqueio temporário após N falhas; baixar ws_max_size deste canal para o que o protocolo precisa; teste de contrato para cada recusa.

**Evidência.** C:/git/android/backend/app/api.py:1330 `await websocket.accept()` antes de qualquer conferência, seguido de `wait_for(receive_json, timeout=30)` (:1335); a rota nunca chama `avaliar`, ao contrário de `/ws` (:1216-1224), que recusa antes do accept e confere Origin. No ramo de recusa (:1340-1343) só há send_json(Refused)+close(4401): nenhum log nem evento persistido. CORREÇÃO ao achado original: existe limite de tamanho de mensagem — o uvicorn aplica ws_max_size=16 MiB por padrão (backend/.venv/Lib/site-packages/uvicorn/config.py:207) e main.py:123-124 não o altera. A credencial tem 32 bytes aleatórios (registry.py: secrets.token_urlsafe(32), comparada com compare_digest), então adivinhação por força bruta é inviável.

**O que o verificador corrigiu.** O accept antes da autenticação, a ausência de `avaliar` e o prazo de 30 s são reais. Mas a afirmação 'sem limite de tamanho de mensagem' é falsa (padrão de 16 MiB do uvicorn) e o argumento de 'tentativas sem freio' perde força com uma credencial de 32 bytes aleatórios. O que sobra é negação de serviço de baixo custo e falta de registro das recusas: rebaixei de 'medio' para 'baixo' e reescrevi título e descrição.

## #127 — Critério de 'tela sensível' é só campo de senha: no caminho genérico do executor, qualquer outra tela é capturada como evidência e enviada ao provedor de IA

`baixo` · risco · esforço M · fase 9 · seção do pedido: 8 (IA) e 3 (evidências em S3) · verificação: parcial

**O que é.** O envio de telas ao provedor é comportamento declarado do produto, e o código de 2FA em si é digitado por uma pessoa em controle manual, fora da captura do executor; portanto o risco hoje é menor do que o achado original sugeria. O que resta: (1) o critério depende de um único atributo da árvore e não conhece telas sensíveis sem campo de senha (tela de desafio/2FA, dados da conta, conversas de terceiros, telas da VM-loja com conta Google), que ficam gravadas em disco e vão à API externa; (2) com o catálogo de apps (E10/E11) isso passa a valer para aplicativos que ninguém analisou; (3) com E8 (S3) essas imagens saem da máquina.

**O que falta.** Estender `sensitive`: aplicar `looks_secret`/`_SECRET_SHAPES` (security/redaction.py:67-75) ao texto da árvore, permitir que cada app declare telas/resource-ids sensíveis (encaixa no registro por app de E10), reutilizar a classificação CHALLENGE/TWO_FACTOR do Instagram para marcar a observação como sensível antes de capturar, e tratar toda tela da VM-loja como sensível. No E8: bucket privado, cifragem no servidor, URL assinada de vida curta em vez de proxy aberto, e retenção aplicada também ao bucket. Ajustar o texto do aviso do health ao critério real.

**Evidência.** C:/git/android/backend/app/automation/hierarchy.py:188-189: `is_password = a.get('password') == 'true'; sensitive = sensitive or is_password` — único critério (grep por 'sensitive' no backend confirma que todos os consumidores derivam daí: devices/manager.py:917-918, taskqueue/executor.py:111, :306, :359, planning/anthropic_provider.py:238, :308, :324). executor.py:303-311 só omite a captura quando obs.sensitive. Mesmo em tela sensível, o TEXTO dos elementos segue no prompt (planning/prompts.py:280-290). O aviso do /api/health diz 'Telas com campo de senha nunca são enviadas' e também declara que screenshots e textos das telas vão para a API da Anthropic. Evidências são JPEG em claro em data/evidence, servidos por /api/evidence/{id} (api.py:1192-1201). Mitigações que existem: o módulo de autenticação do Instagram classifica CHALLENGE/TWO_FACTOR por texto e devolve ao humano (integrations/instagram/navigation.py:40-56 e :304-309; authentication.py:124-125) e o prompt manda chamar step_blocked em login/PIN/2FA/captcha (prompts.py:109) — o que implica que a imagem daquela tela já foi enviada.

**O que o verificador corrigiu.** O fato central é verdadeiro (critério único password=true, conferido em todos os consumidores). Mas a descrição original exagerava: o envio de telas à IA é declarado no próprio health, há tratamento de desafio/2FA no fluxo de autenticação do Instagram e o código de verificação é digitado por humano fora da captura. Não há segredo demonstradamente vazando hoje; é requisito de desenho para E8/E10/E11. Rebaixei de 'medio' para 'baixo' e reescrevi título e descrição.

## #128 — Redação por formato não cobre URL com credencial, Authorization Basic, `credential=`, chaves AWS nem `adb input text`; o agente do worker não usa o filtro

`baixo` · risco · esforço P · fase 9 · seção do pedido: 3 e 4 (credencial não entra em log) · verificação: confirmado

**O que é.** É a defesa secundária, mas E6 introduziu DATABASE_URL com senha e E8 vai introduzir chaves S3: os formatos novos do próprio plano não estão cobertos, e o processo do agente (que roda em outra máquina) não tem filtro nenhum.

**O que falta.** Acrescentar padrões: `scheme://user:SENHA@`, `Authorization: (Basic|Token)` (Bearer já existe em :31), `credential|credencial`, `[\w]*secret[\w]*key`, `access_key`, `input text '...'`; unificar as duas listas de palavras-chave numa constante; instalar RedactingFilter no agente do worker; teste parametrizado com cada formato.

**Evidência.** Executei `redact()` com textos fictícios (somente leitura): saíram EM CLARO `postgresql://farm:S3nh4Forte@10.0.0.5:5432/farm`, `Authorization: Basic dXNlcjpwYXNz`, `credential=abc123`, `AWS_SECRET_ACCESS_KEY=...`, `aws_access_key_id=AKIA...` e `adb shell input text MinhaSenha123`; foram mascarados `Authorization: Bearer ...`, `csrftoken=`, `x-api-key:`, `password=`, `api_token=` e `"enrollment_token"`. `_PATTERNS` (redaction.py:29-30) não tem `credential|credencial`, que só existe em `_SENSITIVE_KEY` (:48) — as duas listas divergem. C:/git/android/backend/app/worker/__main__.py:30 usa logging.basicConfig sem RedactingFilter (main.py:39-41 instala o filtro só no backend). Alcance real hoje: a DSN não chega a nenhum log (só aparece em config.py:267-273 e state.py:103; db.py não a registra) e adb.py:36-38 já tira os argumentos da mensagem de timeout — é lacuna do filtro, não vazamento em curso.

## #129 — Digitação manual sem sessão Appium põe o texto na linha de comando do adb no host (bloqueado só para a loja)

`baixo` · risco · esforço P · fase 9 · seção do pedido: 9 (aceite 3: controle manual de tela) · verificação: confirmado

**O que é.** Se o operador digitar a senha de um perfil pelo painel num aparelho sem sessão de automação (caso comum nos remotos e após falha do Appium), ela aparece no argv do adb.exe do central enquanto o comando roda. Exposição restrita a processos locais da máquina central.

**O que falta.** Enviar o texto pelo stdin de `adb shell` (sem argv) ou recusar digitação manual quando o foco está em campo password=true sem sessão Appium, com mensagem explicando; teste com aparelho falso.

**Evidência.** C:/git/android/backend/app/devices/manager.py:1037-1041: sem `rt.session.connected` (e fora dos testes), cai em `rt.adb.input_text_ascii` -> `adb shell input text '<texto>'` (devices/adb.py:152-157). O próprio comentário em manager.py:1030-1036 descreve o perigo ('visível a qualquer processo que leia argv') e só bloqueia quando `rt.store`. O docstring de `Adb.shell` (adb.py:40-41) diz que o comando 'nunca' leva texto livre — este caminho é a exceção. O conteúdo não vai para log/evento (manager.py:1044 registra só a quantidade de caracteres; adb.py:36-38 omite argumentos na mensagem de timeout).

## #130 — Rotação da chave do provedor de IA sem confirmação registrada

`baixo` · risco · esforço P · fase 0 · seção do pedido: 8 (IA, sem gasto silencioso) · verificação: parcial

**O que é.** Existe uma regra combinada com o dono — nenhuma execução paga antes de ele confirmar a rotação da chave do provedor de IA — e não há registro dessa confirmação, embora GET /api/usage mostre uso pago recente. Ou a rotação foi feita e não anotada, ou a regra caducou. Não há evidência de que a chave em uso seja a antiga.

**O que falta.** O dono confirmar no console do provedor que a chave antiga foi revogada e que o .env tem a nova; registrar a confirmação e retirar a trava das anotações. Opcional: limite de gasto na própria chave.

**Evidência.** GET /api/usage (somente leitura) mostra chamadas pagas nas últimas 24 h; nenhum documento do projeto registra a confirmação da rotação.

## #131 — Sem auditoria automatizada de dependências; Appium local com 7 vulnerabilidades moderadas

`baixo` · dívida · esforço P · fase 9 · seção do pedido: 9 (implementação real / manutenção) · verificação: confirmado

**O que é.** Impacto baixo hoje (Appium preso ao loopback), mas nada avisará quando surgir CVE em fastapi/starlette/cryptography.

**O que falta.** Workflow de CI (ou tarefa local agendada) rodando `pip-audit -r backend/requirements.txt -r backend/worker-requirements.txt` e `npm audit` em frontend e tools/appium; atualizar o Appium quando houver versão com morgan>=1.12.

**Evidência.** `npm audit --json` em C:/git/android/tools/appium (reexecutado por mim): 7 moderadas, 0 altas/críticas, raiz `morgan <1.12.0` (GHSA-jxfw-x594-9x9m, log forging) via @appium/base-driver; `npm audit --omit=dev` em frontend: 0. Python fixado (fastapi 0.141.1, starlette 1.6.0, uvicorn 0.53.0, websockets 17.1, cryptography 46.0.3, psycopg 3.3.6 em backend/requirements.txt); pip-audit não está em backend/.venv/Scripts e não existe diretório .github (sem CI). Appium escuta só em 127.0.0.1:4723 (netstat).

## #132 — docs/banco.md não orienta como proteger o PostgreSQL no cenário multi-máquina (papel de menor privilégio, bind/pg_hba, sslmode)

`baixo` · dívida · esforço P · fase 9 · seção do pedido: 3 (PostgreSQL) · verificação: parcial

**O que é.** O documento recomenda PostgreSQL justamente para o cenário multi-máquina, em que a senha do banco e todo o estado (incluindo o texto cifrado do cofre) atravessam a rede, sem dizer como proteger. O PostgreSQL nativo escutando em todas as interfaces é o padrão do instalador do Windows, está bloqueado pelo firewall e não é comprovadamente parte da aplicação — fica como observação do host, não como achado do projeto.

**O que falta.** Seção 'Segurança' em docs/banco.md: papel dedicado só com privilégios no schema do parque, `listen_addresses`/pg_hba restritos aos IPs dos backends, `sslmode=require` (ou verify-full) na DATABASE_URL, senha fora de log (ver achado de redação). Opcional no host: restringir o listen do PostgreSQL nativo a 127.0.0.1 enquanto não for usado por outra máquina.

**Evidência.** Li docs/banco.md inteiro (182 linhas): não há seção de segurança; o único exemplo de conexão é `postgresql://usuario:senha@host:5432/parque` (:12) e o roteiro de teste usa `postgres:teste` em docker (:91-97); nenhuma menção a sslmode, listen_addresses, pg_hba, TLS ou papel dedicado. Contexto do host, conferido: netstat mostra `0.0.0.0:5432 LISTENING` (postgres.exe pid 6864, instalação nativa em C:/Program Files/PostgreSQL/17); os três perfis do firewall estão ativos e nenhuma regra de entrada cobre a porta 5432 nem o postgres.exe (as regras 'Any' são presas a outros programas) — não alcançável pela rede. A produção usa SQLite (.env sem DATABASE_URL, só nomes conferidos) e não há contêiner farm-pg em execução; o vínculo dessa instalação nativa com este projeto não ficou demonstrado.

**O que o verificador corrigiu.** A lacuna da documentação é real e conferida. Já a metade sobre o PostgreSQL local em 0.0.0.0:5432 é verdadeira como medição, mas não é defeito desta aplicação: produção roda em SQLite, o serviço é uma instalação nativa do host cujo uso pelo projeto não ficou provado, e o firewall bloqueia a porta. Reescrevi o título e a descrição para o que é do projeto e deixei o listen como observação opcional.
