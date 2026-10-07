# Pendências de fechamento da terceira evolução: CI, painel, contagem do plano-100, artefatos e saúde

Data da pesquisa: 2026-09-30, ~10:30–10:50Z, máquina central (`C:\git\android`, `main` = `6997091`, limpo).
Método: leitura de repositório e de logs do `gh`, leituras GET na API viva (`http://127.0.0.1:8000`), banco SQLite
aberto **somente leitura** (`file:...?mode=ro`), inventário do sistema de arquivos e inspeção visual do painel no
navegador embutido. **Única chamada não-GET:** `POST /api/login` com o nome de operador `inspecao-somente-leitura`
(portão do painel; no loopback não pede token; grava uma linha em `panel_sessions` e um evento de log "entrou no
painel" — `backend/app/api.py:237-271`). Nenhum botão de ação foi clicado; nada foi apagado; nenhum arquivo de
`data/rede/piloto/segredos/` foi aberto. "Observado" = lido diretamente; "inferido" = conclusão minha a partir do
observado.

## 1a. Resultado do CI para o commit publicado (6997091) e a corrida agendada seguinte

### Takeaway
O push de `6997091` (run 36661736944) terminou **success** em 38m26s com os 6 jobs do runner próprio verdes e o job
PostgreSQL pulado por condição (só `schedule`/`workflow_dispatch`). A corrida **agendada** de 05:29Z (run
36673606572) terminou **failure** por dois motivos novos e independentes do código: o job PostgreSQL não iniciou
(cobrança do GitHub) e o `npm audit` do Appium passou a acusar 1 vulnerabilidade **high** (`brace-expansion`) que
não existia às 02:51Z.

### Cited Findings
- Lista de runs (observado, `gh run list --limit 10`): `36673606572` schedule **failure** 38m32s (05:29:02Z);
  `36661736944` push `6997091` **success** 38m26s (02:51:14Z); `36661508532` push `e26a469` **cancelled** 3m07s
  (02:48:13Z, cancelado pela concorrência `cancel-in-progress` do push seguinte, `.github/workflows/ci.yml:49-53`).
- Jobs do run 36661736944 (observado, `gh run view 36661736944`): worker · instala só o agente e importa 35s;
  frontend · typecheck + vitest 2m02s; docs · docs-check e livro-razão 42s; backend · mypy (gradual) 2m00s; backend ·
  pytest (SQLite) **31m35s**; dependências · pip-audit + npm audit 1m14s; **backend · pytest (PostgreSQL) — "-"
  (skipped)**.
- Condição do skip (observado, `.github/workflows/ci.yml:76`): `if: github.event_name == 'schedule' ||
  github.event_name == 'workflow_dispatch'`; o job roda em `ubuntu-latest` (linha 74) com serviço `postgres:17`,
  "fica SEMPRE na GitHub" (comentário nas linhas 33-38).
- Anotação no job mypy do run verde (observado): "Process completed with exit code 1" no passo `python -m mypy`
  (legado), que tem `continue-on-error: true` (`ci.yml:183-184`); o passo bloqueante (`-p app.contracts -p
  app.modules -p app.shared`, linha 181) passou. O job aparece ✓.
- Anotações de cache em todos os jobs do runner próprio (observado): `Failed to save: "C:\Program failed with
  error: The process 'C:\Program Files\Git\usr\bin\tar.exe' failed with exit code 2` (actions/cache no Windows com
  espaço no caminho); não reprova o job. Também "Node.js 20 is deprecated" (actions v4/v5) em todos.
- Run agendado 36673606572 (observado, `gh run view`): mypy ✓ 2m11s; **PostgreSQL ✗ em 2s** com a anotação "The job
  was not started because recent account payments have failed or your spending limit needs to be increased";
  **dependências ✗ 1m30s** no passo `npm audit (Appium)`; frontend ✓ 2m01s; SQLite ✓ 31m23s; worker ✓ 38s; docs ✓ 36s.
- Log do passo que falhou (observado, `gh run view 36673606572 --job 109753703026 --log`): `brace-expansion 4.0.0 -
  5.0.11 · Severity: high` (GHSA-q2hr-2g5m-vwhr, GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p), caminho
  `node_modules/appium-uiautomator2-driver/node_modules/brace-expansion`, "fix available via `npm audit fix`"; mais
  `morgan <=1.12.0` moderate (já conhecido, `ci.yml:143-147`). Total "5 vulnerabilities (4 moderate, 1 high)"; o
  passo `npm audit --audit-level=high` saiu com código 1. pip-audit: "No known vulnerabilities found"; frontend:
  "found 0 vulnerabilities".
- Mesmo passo no run do push às 02:51Z (observado): "4 moderate severity vulnerabilities" e nenhuma high — o
  advisory high entrou no índice do npm entre 02:51Z e 05:32Z de 30/09.
- Comentário do fluxo sobre o PostgreSQL só na GitHub após 1º/10 (observado, memória do projeto `ci-runner-proprio`
  e `docs/handoffs/terceira-evolucao.md:150`, P17: "a suíte em PostgreSQL depende de ligar Docker/WSL (autorização) ou
  do CI depois de 1º/10").

### Inferences
- O commit publicado está verde no que o CI consegue provar hoje (SQLite, tipos, frontend, docs, agente,
  auditoria); a paridade PostgreSQL das migrações 056–058 continua `not_run` (P17) e o run agendado confirma que a
  GitHub ainda recusa o job pago em 30/09.
- A reprovação de `dependências` no agendado vai se repetir **no próximo push**, porque o job roda "a cada push E na
  corrida diária" (`ci.yml:139`): o gate `--audit-level=high` foi desenhado para deixar passar só moderadas. Saída
  provável: `npm audit fix` em `tools/appium` (o log diz que há correção sem `--force`) ou fixar `brace-expansion`
  via `overrides`; é mudança pequena, mas é código, e a auditoria de dependências é o portão do achado #131.
- O aviso de cache (`tar.exe` exit 2) é ruído do runner Windows; a única consequência é o cache não salvar
  (jobs mais lentos), não afeta o resultado.

### Gaps
- Não medi se `npm audit fix` resolve sem subir o Appium de versão (o log só diz "fix available"); precisa de
  execução local em `tools/appium`, fora do escopo somente-leitura.
- Não há como saber pelo `gh` quando o limite de gasto da conta GitHub será renovado; o projeto assume 1º/10.

## 1b. Inspeção visual do painel (aba Rede e Persona › Contas e acesso) contra o backend vivo

### Takeaway
As duas telas renderizam sem erro de console com os dados reais: a aba Rede mostra os 4 aparelhos com VPN
(3 em `Tráfego verificado`, android-05 em **`Parcial`** — um estado parcial real, medido às 07:42Z), o aviso de saída
compartilhada em todos, o servidor "no ar (4 pares)" e o firewall sem regra com o comando do dono; a aba Contas de
André mostra a conta Outlook "Não verificada · login pela pessoa (Foco) · app não verificado no aparelho" com a
senha guardada **sem consentimento**. Lacunas: a tabela de aparelhos é apertada já a 800 px (IP quebra em duas
linhas, texto por app quebra no meio da palavra) e a 375 px só é usável por rolagem horizontal interna do cartão;
a linha de proxy legado do android-01 aparece como "—" (valor nulo); a conta Outlook recebe o prefixo "@ " antes
de um e-mail.

### Cited Findings
- Rotas: a visão é escolhida pelo hash (`#/aplicativos`, `#/perfis`; `frontend/src/store/ui.ts:14-19,54-72`); a aba
  Rede é estado local do `AppsPage` (`frontend/src/features/apps/AppsPage.tsx:37,57-63,81`), sem URL própria; a aba
  "Contas e acesso" idem (`frontend/src/features/profiles/ProfileDetail.tsx:64`). Ou seja, não há link direto para
  a aba: é preciso clicar.
- Portão de entrada (observado): a primeira tela é "Seu nome … Este backend está sendo aberto da própria máquina:
  não é preciso chave de acesso" → `POST /api/login` (`frontend/src/api/client.ts:442-443`; servidor em
  `backend/app/api.py:237-271`, grava `panel_sessions`, `backend/app/security/sessions.py:97-104`).
- **Aba Rede, texto lido (`get_page_text`, observado):**
  - Cabeçalho "O que cada estado prova" com a frase do ADR-056 §3 ("Testar e Reaplicar só REGISTRAM o pedido (202)").
  - Perfis: 1 perfil `central-wireguard · vpn · wireguard · 10.0.2.2:51820`; formulário de criação com "Criar —
    indisponível: Preencha nome, host e porta"; "Segredo — opcional, nunca reexibido".
  - "Atribuir rede em lote": prévia obrigatória; "Ver prévia" e "Aplicar" desabilitados sem escolha; tabela de
    seleção com 14 aparelhos (android-01…10, 12…15) e a coluna "Conta real" (@lucas…, @bruno…, @andre…). **android-11
    não aparece** (é a loja).
  - Tabela "Aparelhos" (colunas: Aparelho · VPN · Proxy · Política · Estado · IP de saída · Última verificação · Erro /
    pendência · Ações): android-02 `Tráfego verificado`, 38.211.146.161, "mesma saída que android-03, android-05,
    android-06", "DNS 172.19.0.2 · UDP ok · vazamento bloqueado", "com.android.shell: pelo túnel", 30/09 04:08:08
    (hora local), "medição #11 … saída e apps provados"; android-03 idem com "com.instagram.android: pelo túnel",
    05:01:09, medição #18; android-06 idem, 04:32:09, medição #14; **android-05 `Parcial` — "aguardando a medição de
    dentro do aparelho" · "vazamento não medido" · 07:42:11 · "medição #33 … o bloqueio fora da VPN não foi
    provado"**; android-01 "sem rede pedida · não medido · nunca", Proxy "—"; android-04 (stopped) com o texto de
    quarentena em vermelho na coluna Erro / pendência ("… @felipe.nogueira9376… travada e logada nele desde
    2026-09-29T00:36:00.000Z (declarado (dono); ADR-055) …"); android-07…15 "sem rede pedida"; Testar/Reaplicar
    "indisponível: Atribua um perfil antes" nos sem perfil e habilitados nos 4 com VPN (não clicados).
  - Legenda: "aviso de IP repetido" e "aparelho com conta real vinculada".
  - "Servidor do central": "no ar (4 par(es)) · WireGuard UDP 51820, túnel 10.66.0.0/24 · aparelhos de outra
    máquina: nenhum · Endereço na LAN: 192.168.1.81 · **firewall sem regra** — firewall ligado no perfil Public com
    entrada padrão Block e nenhuma regra que permita UDP 51820 ao sing-box (lido em 2026-09-30T10:44:23.697Z)" e o
    comando `New-NetFirewallRule … -Protocol UDP -LocalPort 51820 -Program '…\sing-box.exe' -RemoteAddress
    LocalSubnet -Profile Public` para o dono. O botão "Conferir firewall" existe; a leitura já veio no carregamento.
- API por trás (observado, `GET /api/network/devices`): android-01 tem `legacy_proxy = {proxy_id: null, name: null,
  value: null, state: "applied", observed_value: ":0", verified_at: "2026-09-26T18:57:02.262Z", detail:
  "configuração lida de volta do aparelho"}` → o painel mostra "—" na coluna Proxy (o legado só é lido, nunca
  escrito: `AppsPage.tsx:45-50`). Os 4 com VPN: `policy: exigida_com_bloqueio`, `desired_rev = applied_rev = 1`,
  `egress_shared_with` com os outros 3, `egress_ipv6: null`.
- Exclusão da loja e da quarentena é regra do backend (observado, `backend/app/devices/rede.py:463-467`: "A loja e
  o aparelho em quarentena não aparecem: nada toca neles (ADR-055, ADR-056 §7)"); na tabela de estado o android-04
  aparece porque a lista de `/network/devices` inclui a `restriction`.
- Polling (observado): a página chama `GET /api/network/profiles` + `GET /api/network/devices` a cada 4 s enquanto
  visível (`RedePage.tsx:101`, `useIntervaloVisivel(carregar, 4000, …)`); a lista de rede do navegador registrou
  ~15 pares em poucos minutos.
- **Console:** `read_console_messages` → "No console logs" nas duas telas (observado). **Rede:** só GETs 200 além do
  `POST /api/login` inicial; dois `GET /api/runs/r-20260928155247-f55c04…` abortados (`net::ERR_ABORTED`) e refeitos
  com 200 (cancelamento de efeito do React ao trocar de visão, inferido).
- **Largura 800 px** (largura do painel embutido; captura observada): a tabela "Aparelhos" cabe sem rolagem, mas a
  coluna IP de saída quebra `38.211.1 / 46.161`, "central-wireguard" quebra em 4 linhas e o texto por app quebra
  em `com.instagr / am.android`; o ID do worker `WIN-7S2UASNLFOP` quebra em 3 linhas.
- **375 px** (`resize_window` mobile, observado): `document.documentElement.scrollWidth = 375` (sem rolagem
  horizontal da página). A tabela "Aparelhos" mede 655 px dentro de um contêiner de 309 px com `overflow-x: auto`
  (medido por JS): rola horizontalmente dentro do cartão; visíveis sem rolar só Aparelho · VPN · Proxy · Política ·
  início de Estado ("Tráfego verif", "sem rede pedid" cortados); "android-01" quebra em "android- / 01". A barra
  de abas e a navegação principal ("Execuç…") também rolam horizontalmente. A tabela de seleção em lote (309 px)
  cabe inteira.
- **Persona André (`ig-KW1uWMsISqStNXbU`), aba "Contas e acesso (2)"** (observado): cabeçalho "André Carvalho ·
  @andre.carvalho9543 · android-06 · Conectado"; filtro "Apps desta persona: Todos | Instagram | Outlook | + Conta
  em outro app". Cartão Instagram: "Conectado · login automático · conectada · conferida há 9 h 03 min", "Conta
  observada na tela: @andre.carvalho9543", botões Reconectar / Verificar conta / Sair da conta; "Senha do
  Instagram — Guardada cifrada e nunca exibida … Identificador de login gravado: <e-mail outlook.com da persona,
  não reproduzido>"; "Consentimento dado em 25/09, 18:56:47 por migração 049"; bloco "Usar a senha de outra conta
  desta persona" listando "Outlook — <e-mail>". **Cartão Outlook:** identificador = o e-mail outlook.com;
  selos "Não verificada · login pela pessoa (Foco) · app não verificado no aparelho"; "Não se sabe se o Outlook
  está instalado em android-06: o aparelho nunca foi inspecionado. Verifique o app antes de conectar."; botões
  "Entrei" / "Saí" / lixeira; caixa "Autorizo a automação a digitar esta senha, só no app/site desta conta"
  (desmarcada); "Autorizar sem redigitar — indisponível: Marque a autorização primeiro"; **"Senha guardada sem
  consentimento: ninguém a digita até você autorizar."**; "Apagar senha" habilitado (não clicado).
- Identificador exibido com o prefixo "@ " no cartão Outlook (observado na captura: "@ <e-mail>"); no cartão
  Instagram o mesmo prefixo precede o usuário. Coerente com a API: `credential.configured=true, consent_at nulo`
  (`.claude/plano-100/estado.json`, item 23.11, `evidence`).
- 375 px na aba Contas (observado): `scrollWidth 375`, cartões empilham, botões quebram em duas linhas
  (Reconectar / Verificar conta / Sair da conta), nada cortado.
- Critérios de aceite (observado, `docs/plano-100.md:582`, 25.8): "por aparelho, perfis VPN e proxy, desejado ×
  observado, conexão, IP medido, última verificação, erro ou pendência; prévia em lote, edição individual, testar e
  reaplicar; segredo nunca exibido". `docs/plano-100.md:533`, 23.10: "sem `ehInstagram`; 'usar a senha de outra conta
  desta persona'; política de ações por app (`GuiaContas.tsx`, `PolicyGroups.tsx`, `social/service.py`)".

### Inferences
- **25.8 × tela:** todos os campos do aceite aparecem (perfis, política, desejado × observado via `desired_rev`/
  `applied_rev` e Estado, IP medido, última verificação, erro/pendência, prévia em lote, edição individual "um
  único aparelho marcado", Testar/Reaplicar, segredo nunca exibido). Lacunas de apresentação, não de conteúdo:
  (a) "desejado × observado" está implícito (o painel não mostra `desired_rev`/`applied_rev`, só o Estado; quando
  divergirem, o usuário verá "pendente" no Estado — não há dado real para confirmar hoje); (b) a coluna "Proxy"
  não distingue "sem proxy" de "proxy legado lido do aparelho" (android-01 tem `legacy_proxy.state=applied` com
  valor vazio e mostra "—"); (c) legibilidade da tabela a ≤ 800 px.
- **23.10 × tela:** "usar a senha de outra conta desta persona" existe nos dois cartões e a UI é neutra por app
  (o Outlook tem "Entrei/Saí" em vez de "Reconectar" porque é "login pela pessoa (Foco)"). O prefixo "@ " antes
  de um e-mail sugere um resíduo de vocabulário do Instagram no rótulo do identificador (não confirmei a linha
  de código: `GuiaContas.tsx:389` só mostra o "@" da conta observada; o prefixo do cabeçalho do cartão vem de
  outro componente que não localizei).
- O estado `Parcial` do android-05 às 07:42Z (medição #33: "o bloqueio fora da VPN não foi provado") é o
  comportamento descrito no P16 (`docs/handoffs/terceira-evolucao.md:149`): após o reinício do backend (deploy de
  02:51Z, `started_at` do sing-box `2026-09-30T02:51:37Z`) o teste de vazamento em memória se perdeu e a
  primeira verificação refaz o teste; o android-05 é o único que ainda não fechou o ciclo.
- Login pelo painel gerou uma linha em `panel_sessions` e um evento "inspecao-somente-leitura entrou no painel";
  nada mais foi escrito por esta inspeção.

### Gaps
- **Estados não presentes nos dados reais:** `configurado`, `conectado` (sem tráfego verificado), `erro` de
  aplicação, `pendente` (desired_rev > applied_rev), proxy atribuído, aparelho remoto (`remote: true`) e
  `egress_ipv6` medido. **Inspeção proposta (não executada):** (1) `vitest` já cobre variantes sintéticas em
  `frontend/src/features/rede/RedePage.test.tsx` — conferir quais estados o teste monta e olhar a renderização
  com `npm run dev` apontando para um backend `scripts/start.ps1 -Simulated` (harness em `base_console_port
  5640`), onde `POST /api/network/assign` num aparelho falso produz `pendente` → `configurado` → `erro` sem tocar
  o parque; (2) para `erro` real sem custo: atribuir um perfil com host inválido a um aparelho de QA parado
  (android-07) e observar a coluna Erro / pendência — requer POST e autorização, por isso fica descrito.
- Não verifiquei a aba Rede em largura intermediária (tablet 768 px) nem em tema claro.
- Não localizei o componente que imprime o cabeçalho "@ <identificador>" no cartão da conta.

## 2. Reconciliação da contagem dos 43 itens das Fases 23–27 e a "revisão de assinatura"

### Takeaway
Pelo `estado.json` (o mecanismo oficial), as Fases 23–27 somam **43 itens: 34 `implemented` (11 `real`, 13
`simulated`, 10 `not_run`), 3 `partial` (todos `real`) e 6 `blocked` (1 `real` — 23.2 —, 5 `not_run`), todos os 6
bloqueios com o mesmo motivo P15 (Outlook não roda no parque)**. A expressão "revisão de assinatura" não existe em
nenhum documento do repositório; o único item que fala de assinatura é o 23.2, cuja assinatura está **aprovada**
(`app_trusted_signers`) e cujo bloqueio é o canário, não a assinatura. O `check` do mecanismo está **interrompido**
porque o índice de pacotes está velho (Fases 14–28 sem pacote), o que impede a leitura oficial do estado até se
regenerar o índice.

### Cited Findings
- `python scripts/claude-plan-100.py check` (observado): sai com "Interrompido: Os pacotes estão velhos em relação
  ao plano. Rode o gerador de novo." (`scripts/claude-plan-100.py:82-86`: o índice `.claude/plano-100/pacotes/
  indice.json` tem 93 IDs, o plano tem 253 com T.1–T.4; faltam no índice todos os IDs de 14.1 a 28.13). Último
  commit do índice: `ef99388`; do plano: idem (`git log`). Regenerar é `python scripts/plano-100-pacotes.py`
  (escreve na árvore, por isso não fiz).
- Tabela do estado, itens 23.x–27.x (observado, `.claude/plano-100/estado.json`, campos `status`/`proof`):
  - Fase 23 (13): 23.1 impl/sim; **23.2 blocked/real**; 23.3 impl/sim; 23.4 impl/sim; 23.5 impl/sim; 23.6
    impl/sim; 23.7 blocked/not_run; 23.8 blocked/not_run; 23.9 impl/real; 23.10 impl/sim; 23.11 partial/real;
    23.12 blocked/not_run; 23.13 blocked/not_run. → 6 sim, 1 real, 1 partial-real, 1 blocked-real, 4 blocked-not_run.
  - Fase 24 (9): 24.1 real; 24.2 sim; 24.3 real; 24.4 real; 24.5 sim; 24.6 sim; 24.7 real; 24.8 sim; 24.9
    partial/real. → 4 real, 4 sim, 1 partial.
  - Fase 25 (10): 25.1 real; 25.2 real; 25.3 real; 25.4 real; 25.5 real; 25.6 sim; 25.7 sim; 25.8 sim; 25.9
    partial/real; 25.10 real. → 6 real, 3 sim, 1 partial.
  - Fase 26 (8): 26.1–26.8 impl/not_run (desenho, `docs/design/pedidos-persistentes.md` §4–§13).
  - Fase 27 (3): 27.1 impl/not_run; 27.2 blocked/not_run; 27.3 impl/not_run.
  - Totais: `{'implemented': 34, 'partial': 3, 'blocked': 6}`; por prova `(implemented, not_run): 10, (implemented,
    simulated): 13, (implemented, real): 11, (partial, real): 3, (blocked, real): 1, (blocked, not_run): 5`.
- Bloqueios (observado, campo `blocker`): 23.7, 23.8, 23.12, 23.13, 27.2 = "P15: o Outlook 5.2635.3 não roda no
  parque emulado (armadilha UD2 na libhxcomm.so; emulador 37.1.11/37.2.11 cai, 37.3.2 sobrevive e o app morre).
  Saídas do dono: celular físico por USB, Outlook web no Chrome ou versão nova do app."; 23.2 = "P15: o Outlook não
  roda no parque emulado; saídas do dono: celular físico, Outlook web no Chrome ou versão nova do app".
- 23.2 `evidence` (observado): "Outlook 5.2635.3 importado da loja (c-20260929184859-0fe853, depois da correção do
  inspetor em 45bd8ea) e **assinatura aprovada**; canário no android-02 (c-20260929185249-440edc) e no android-09
  (c-20260929194525-da36f8) falharam na abertura e a versão está em quarentena".
- `grep -rn -i "revis[ãa]o de assinatura|signature review" docs .claude/plano-100 CHANGELOG.md` → **nenhuma
  ocorrência** (observado). O runner (`docs/execucao-plano-100-runner.md:203`) repete o texto do 23.2.
- Release Outlook (observado, `GET /api/releases`): `id com.microsoft.office.outlook-72635119-293cc771fcd5`,
  `status: installable`, `channel: quarantined`, `channel_at 2026-09-29T19:47:20Z`, `channel_detail: "o canário
  android-09 falhou em 'launch': o app apareceu, mas não se firmou em primeiro plano (foco: nenhuma janela)"`,
  `canary_instance_id android-09`, `source_type store`, 4 validações (install ok / launch falhou em android-02 e
  android-09), `devices: []`, `signature_sha256` começa por `cc66de496bc6`.
- `app_trusted_signers` (observado, SQLite ro): `com.microsoft.office.outlook` com sha256 `cc66de496bc6…`
  (mesmo prefixo da release), `approved_at 2026-09-29T18:49:26.775Z`, nota "primeira versão do Outlook pela Play
  Store com a conta do dono (android-11); item 23.2, autorização de 29/09". Também WireGuard e sing-box aprovados
  às 17:48Z (item 25.1).
- 12.3 (observado, `estado.json`): `status: pending`, `proof: not_run`, `evidence: "Aguardando decisão do dono sobre
  qual app ganha login determinístico/catálogo primeiro; hoje Outlook/TikTok/Facebook operam pela IA livre com
  login feito pela pessoa no Foco."`, sem `quando`, sem `conferido`. `docs/claude-plano-100.md:22-27`: 12.3 foi
  escrito **à mão** com estado `pending`, fora do vocabulário do `aplicar` (`implemented/partial/blocked`;
  `real/simulated/not_run`), mantido como histórico; "Trabalho feito fora da esteira deve ser registrado por um
  `resultado.json` e `aplicar`". `docs/plano-100.md:514`: Fase 23 "realiza o 12.3"; `docs/handoffs/
  terceira-evolucao.md:49`: "12.3: o Outlook é o primeiro app novo (o próprio pedido)". `docs/estado-atual.md:120`:
  "o 12.3 segue com o dono".
- Vocabulário aceito por `aplicar` (observado, `scripts/claude-plan-100.py:35-36`): `ESTADOS = {implemented,
  partial, blocked}`, `PROVAS = {real, simulated, not_run}`; `docs/claude-plano-100.md:116`: "`aplicar` recusa o
  registro de `implemented` sem evidência, `blocked` sem motivo e prova sem evidência".

### Inferences
- A contagem do resumo anterior ("11 real, 13 simulated, 9 desenho, 3 partial, 6 blocked by Outlook, 1 blocked by
  'revisão de assinatura'" = 43) está **quase certa nos números e errada na leitura**: 11 + 13 + 3 + 6 = 33; os 10
  restantes são os `implemented/not_run` (8 de desenho da Fase 26 + 27.1 revisão + 27.3 fechamento), não 9. O "1
  blocked by revisão de assinatura" é uma contagem dupla do 23.2 (que já está entre os 6 bloqueados por P15) ou uma
  leitura equivocada da sua evidência "assinatura aprovada". Não há item bloqueado por assinatura: a assinatura do
  Outlook foi aprovada às 18:49Z de 29/09 e o que bloqueia é o canário (launch falhou) → quarentena → P15.
- 12.3 continua `pending` porque nunca passou pelo `aplicar` e depende de decisão do dono. Com o Outlook escolhido
  na terceira evolução (handoff:49) e o suporte pronto (23.3–23.6, 23.9–23.11), o registro coerente pelo mecanismo
  seria um `resultado.json` com `12.3: status=blocked, proof=not_run, blocker="P15 …"` (o mesmo dos 23.x), ou
  `partial/real` citando 23.9/23.11 (contas Outlook criadas com senha clonada) — a escolha entre os dois é do
  dono/IDE; ambos tiram o estado do vocabulário inválido `pending`. Fechar como `implemented` exige o 23.13 real.
- Itens **só `simulated`** e o aceite real que falta a cada um (inferido do `blocker`/`evidence`):
  - 23.1 (autorizações no gerador de pacotes): confirmar que `plano-100-pacotes.py` regenera os pacotes 23–28 com o
    aviso; hoje o índice está velho, então o efeito real nunca foi visto.
  - 23.3 (conhecimento do Outlook em YAML): abrir o app num aparelho e conferir âncora/telas — P15.
  - 23.4, 23.5, 23.6 (sessão declarada, login por app): login real do Outlook — P15.
  - 23.10 (painel de contas por app): `blocker` diz "precisa de um segundo app com catalog.yaml (Outlook não tem)
    e deploy"; a inspeção visual desta pesquisa confirma a tela com dados reais (pode virar prova `real` do painel,
    não do fluxo de login).
  - 24.2, 24.5, 24.6, 24.8 (política por etapa, roteamento por conjunto de apps, Comando sem escolher app,
    `PlanStep.app_id`): as 3 execuções reais de 30/09 (bd5c5a, 12d329, 13ec70) exercitaram parte disso; o que falta
    é o recorte Outlook → Instagram (24.9, P15).
  - 25.6 (porta de rede entre etapas): "depends on 25.4/25.5 running on a real device" — o dado real existe (4
    aparelhos `trafego_verificado`), falta uma execução real que atravesse a porta com `rede.sonda.abrir_apps`
    ligado (P16 diz que voltou a `false`).
  - 25.7 (aparelhos do notebook pela LAN): falta a regra de firewall do dono — o painel mostra "firewall sem regra"
    e o comando exato (observado).
  - 25.8 (painel Rede): a inspeção desta pesquisa com dados reais pode ser registrada como prova `real` da
    renderização; o que falta são os estados ausentes (ver 1b, Gaps).

### Gaps
- Sem regenerar o índice de pacotes, `check`/`relatorio` não emitem a contagem oficial; a tabela acima é leitura
  direta do `estado.json` com o mesmo vocabulário. Regenerar é uma escrita na árvore (`.claude/plano-100/pacotes/`)
  e ficou fora do escopo.
- Não encontrei a origem textual de "revisão de assinatura"; a hipótese 23.2 é a única compatível.

## 3. Inventário dos artefatos de diagnóstico e plano de limpeza reversível

### Takeaway
Os artefatos do P15 ocupam ~2,1 GB de emuladores (SdkBeta 37.2.11 e SdkCanary 37.3.2, com junções para o SDK
principal), ~4,4 GB da imagem android-36 e ~10 GB dos dois AVDs `diag-outlook*`; o piloto de rede (`data/rede/
piloto/`, 8 arquivos em `segredos/`) não é referenciado pelo servidor em produção, que usa chaves próprias geradas
na migração 058 (`network_keys`, servidor criado às 23:51Z de 29/09 vs. par do piloto às 14:49Z). Nada disso está
no Git (`data/` e `apks/` ignorados). O que **não** pode ser apagado: `data/rede/sing-box-1.14.2-windows-amd64/`
(binário em uso, `config.py:646`), `data/rede/servidor/` (config e PID do processo vivo), o `config.yaml`, as
chaves em `network_keys`/cofre e os backups `data/backups/config.yaml.antes-*`.

### Cited Findings
- **C:\Android\SdkBeta** (observado, `Get-ChildItem`/`Get-Item .LinkType`): criado 2026-09-29 17:35; `emulator/`
  1069,7 MB, `Pkg.Revision=37.2.11`; `platform-tools`, `platforms`, `system-images` são **Junction** →
  `C:\Android\Sdk\...`; `licenses/`, `.temp/`, `.knownPackages` vazios/pequenos.
- **C:\Android\SdkCanary** (observado): criado 17:41; `emulator/` 1079,7 MB, `Pkg.Revision=37.3.2`; mesmas 3
  junções para o SDK principal. SDK principal: `emulator` `Pkg.Revision=37.1.11`.
- **Imagem android-36** (observado): `C:\Android\Sdk\system-images\android-36\google_apis\x86_64` 4373 MB, mod.
  2026-09-29 (as demais imagens são de 17–18/09). A imagem do parque é `system-images;android-34;google_apis;x86_64`
  (`GET /api/health` → `features.system_image`).
- **AVDs de diagnóstico** (observado, `%USERPROFILE%\.android\avd`): só existem `diag-outlook.ini` e
  `diag-outlook-36.ini` nessa pasta (os AVDs do parque ficam em `android.avd_home` do `config.yaml`, valor não
  reproduzido). `diag-outlook.avd` 4958,3 MB (config: android-34 google_apis x86_64, ram 3072, 4 cores, data 10G,
  PlayStore no; `userdata-qemu.img.qcow2` 1787,6 MB; `emulator-user.ini` 17:50); `diag-outlook-36.avd` 5003,6 MB
  (android-36, `userdata-qemu.img.qcow2` 1828,8 MB; último toque 17:58).
- **data/rede/** (observado, 123,1 MB): `sing-box-1.14.2-windows-amd64.zip` 32 MB (14:47) e a pasta extraída
  (`sing-box.exe` 80 MB, `libcronet.dll` 9 MB); `servidor/` = `servidor.json` 1,7 KB (23:51), `servidor.log`
  4,3 MB (crescendo, 07:40), `servidor.pid`, `servidor.stdout` — pasta do processo vivo (`rede_servidor.py:17-18`,
  `state.py:323`); `piloto/` = `gerar_config.py`, `servir_uma_vez.py`, `netstats_uid.py`, `tela.sh`, `tela_api.sh`,
  `servidor.log` 287 KB, `servidor.stdout`, `http-efemero.log`, `servidor.inicio`, `medicao-25.1.md` 21 KB e
  `segredos/` com 8 arquivos (`cli-sfa.keypair`, `cli-wg.keypair`, `perfil-sfa-proxy.json`, `perfil-sfa.json`,
  `piloto-wg.conf`, `proxy.senha`, `servidor.json`, `servidor.keypair`; 0–1,6 KB; todos 14:49–14:53 de 29/09).
  **Nenhum arquivo de `segredos/` foi aberto.**
- `.gitignore` (observado): `data/` e `apks/` ignorados; `docs/handoffs/terceira-evolucao.md:198-211` chama o piloto
  e o binário de "fora do Git".
- **Referências no código** (observado, `grep`): `backend/app/config.py:646` `binario: str =
  "data/rede/sing-box-1.14.2-windows-amd64/sing-box.exe"`; `rede_servidor.py:4` cita `data/rede/piloto/` só como
  histórico ("provou o caminho"); `rede_aplicacao.py:250` e `sonda_rede.py:314` citam `gerar_config.py`/
  `netstats_uid.py` do piloto como origem da ideia, em docstrings. Nenhum caminho de `piloto/` é lido em tempo de
  execução.
- **Chaves em produção** (observado): `network_keys` tem 5 linhas — `servidor` (kind `servidor`, pública `HJR570…`,
  `created_at 2026-09-29T23:51:09.117Z`) e android-05 (23:51:09Z), android-02 (00:49Z), android-06 (01:32Z),
  android-03 (01:48Z); `GET /api/network/server` devolve `server_public_key HJR570…` e os 4 `peers` com as mesmas
  públicas (`D+VtcO…`, `MuX92s…`, `3zCXj3…`, `Cd+okC…`), `running: true, pid 21776, started_at 2026-09-30T02:51:37Z,
  in_sync: true, signature ab82064daf07, proxy: null, proxy_users: [], remote_peers: []`. O docstring de
  `rede_servidor.py:8-10`: "A chave do aparelho é gerada pela plataforma (`segredo_de_rede.gerar_chave_wireguard`,
  migração 058) … o servidor tem a dele".
- **Sobras no android-05** (observado, `data/rede/piloto/medicao-25.1.md:150-154,203` e `docs/handoffs/
  terceira-evolucao.md:39-42`): "os dois apps instalados; os perfis do piloto dentro deles (chaves do piloto); a
  preferência de controle remoto do WireGuard; o relatório de falha do SFA em `/sdcard/Android/data/
  io.nekohasekai.sfa/files/crash_reports/2026-09-29T18-29-04/` (configuration.json) com a chave do piloto. Não foi
  apagado: descarte as chaves do piloto"; handoff: "chaves do piloto descartáveis; apagar o arquivo na próxima
  provisão". O túnel "piloto" foi empurrado por `adb root` + `adb push piloto-wg.conf …/com.wireguard.android/
  files/piloto.conf` (medicao:179). Não acordei nem consultei o aparelho.
- **Worktrees e branches** (observado, `git worktree list`; `git branch --merged origin/main`): 26 worktrees em
  `.claude/worktrees/` (evo3 em `6997091` = `claude/evolucao3`; nenhum `evo3-b1/b2/c1/c2/d1/d3` como worktree);
  branches `claude/evo3-b1, -b2, -c1, -c2, -d1, -d3, claude/evolucao3, claude/evolucao3-plano` **todos merged** em
  `origin/main`; os 63 branches `claude/*` locais estão todos na lista `--merged`. Worktrees antigos (f22-*, falha-*,
  fase22*, etc.) também apontam para branches merged.
- **config.yaml × backups** (observado, diff só de chaves): `config.yaml.antes-teste-vulkan-20260929` (16:04) e
  `config.yaml.antes-endpoint-lan-20260929` (21:41) são **byte a byte idênticos** (`cmp`); o `config.yaml` atual
  difere deles em 10 linhas = o bloco novo `rede: { servidor: { endpoint_lan }, sonda: { abrir_apps } }` com o
  comentário do 25.7 (`config.yaml:322-329`). Backups mais antigos: antes-fase17 (27/09), antes-est-4000 (26/09),
  antes-ram-perfil (25/09), antes-sonnet (25/09), antes-ollama (24/09).

### Inferences
- **As chaves do piloto não são as do servidor em produção.** Evidência: o par do piloto foi gerado às 14:49Z de
  29/09 (`segredos/servidor.keypair`), o servidor da plataforma às 23:51:09Z pelo cofre (`network_keys.servidor`),
  e o código gera as próprias chaves. Não comparei bytes porque isso exigiria abrir `segredos/`; a comparação
  segura, se quiserem certeza, é `wg pubkey < segredos/servidor.keypair` (ou o campo público do arquivo) contra
  `server_public_key` de `GET /api/network/server`, feita pelo dono.
- **Ordem de limpeza reversível (proposta, nada executado):**
  1. **Manter (não apagar):** `data/rede/sing-box-1.14.2-windows-amd64/` (binário configurado); `data/rede/servidor/`
     (processo vivo; o backend recria a config, mas apagar com o processo no ar corrompe a assinatura/PID);
     `config/config.yaml`; `data/backups/config.yaml.antes-*` (são a única cópia fora do Git); banco `data/poc.sqlite3`
     e cofre; `C:\Android\Sdk` inteiro; o worktree `evo3` até o dono decidir.
  2. **Arquivar (mover, não apagar):** `data/rede/piloto/` inteiro → `data/arquivo/rede-piloto-20260929/` (o
     `medicao-25.1.md` é evidência citada no plano; `segredos/` vai junto, sem abrir; ~320 KB). Reversível com
     `Move-Item` de volta. Se o dono preferir destruir as chaves do piloto (recomendado pelo próprio relatório:
     "descarte as chaves do piloto"), apagar **só** `segredos/` depois de arquivar o restante — mas antes de apagar,
     limpar as sobras no android-05 (item 4), senão a chave continua viva no aparelho.
  3. **Apagar (recuperável por download; ~2,1 GB):** `C:\Android\SdkBeta` e `C:\Android\SdkCanary`. Cuidado com as
     junções: remover primeiro os links com `rmdir` (cmd) ou `Remove-Item -LiteralPath <link>` **sem** `-Recurse`
     (`[IO.Directory]::Delete(path)` também remove o ponto de junção sem seguir), depois `Remove-Item -Recurse`
     na pasta restante. **Nunca** `Remove-Item -Recurse` direto na raiz `SdkBeta` (K-041, regra de memória: já
     apagou `C:\Program Files\Git`). O emulador beta/canary só serve ao P15; se o dono quiser reabrir o
     diagnóstico, o canary volta por `sdkmanager --channel=3 emulator` em um SDK à parte.
  4. **Apagar (recuperável por recriação; ~10 GB):** AVDs `diag-outlook` e `diag-outlook-36` — `emulator -list-avds`
     confirma o nome, `avdmanager delete avd -n diag-outlook` (e `-36`) remove `.avd` e `.ini`. Não são do parque
     (não estão em `android.avd_home`; a pasta do usuário só tem esses dois).
  5. **Apagar (recuperável por download; 4,4 GB):** imagem `system-images;android-36;google_apis;x86_64`
     (`sdkmanager --uninstall "system-images;android-36;google_apis;x86_64"`), só depois do passo 4 (o AVD
     `diag-outlook-36` a referencia) e só se nenhum AVD do parque usar android-36 (o parque usa android-34).
  6. **Apagar (recuperável por re-download; 32 MB):** `data/rede/sing-box-1.14.2-windows-amd64.zip` — o extraído é
     o que roda; o zip só serve para conferir o SHA-256 outra vez.
  7. **Git:** `git worktree remove .claude/worktrees/evo3` (branch merged; as junções `backend/.venv` e
     `frontend/node_modules` dentro dele devem sair com `rmdir` do link antes, "nunca apagar recursivo",
     handoff:196-198) e `git branch -d claude/evo3-b1 …-d3 claude/evolucao3 claude/evolucao3-plano` (todos
     merged, `-d` recusa se não for). Os outros 20+ worktrees antigos estão na mesma situação e são candidatos
     na mesma regra; os branches `claude/*` remotos, se existirem, ficam com o dono.
  8. **Aparelho android-05 (ação no parque, precisa de autorização e do aparelho acordado):** apagar
     `/sdcard/Android/data/io.nekohasekai.sfa/files/crash_reports/2026-09-29T18-29-04/` e o túnel "piloto" do
     WireGuard (`/data/data/com.wireguard.android/files/piloto.conf`, via `adb root`), ou simplesmente deixar a
     próxima provisão/reset fazer isso ("apagar o arquivo na próxima provisão", handoff:42). O android-05 hoje está
     `online`, `Parcial` na rede; a limpeza deve esperar ele fechar em `trafego_verificado` para não confundir a
     medição.
- Nada acima toca `config.yaml`: o bloco `rede:` é a única diferença em relação aos dois backups de 29/09 e é
  necessário ao 25.7.

### Gaps
- Não medi o tamanho de `.claude/worktrees/*` (26 pastas com junções para venv/node_modules; o `du` seguiria as
  junções e inflaria o número).
- Não confirmei, no aparelho, se os perfis do piloto ainda existem no WireGuard/SFA do android-05 (regra: não
  acordar nem consultar o aparelho); a fonte é o relatório de 29/09.
- Não sei se o dono quer manter o canary para o "Outlook web no Chrome" (saída sugerida do P15) — o canary não é
  necessário para isso (Chrome roda no 37.1.11).

## 4. Saúde: `degraded` por `ai_balance_low` e custo das provas pagas restantes

### Takeaway
`degraded` **não é defeito**: é o patamar informativo do livro-caixa (ADR-051) — o saldo estimado da Anthropic
(US$ 2,46) está abaixo do aviso (US$ 3,00) e acima do bloqueio (US$ 0,50); o `status` só vira `error` para
`sdk_missing`, `no_acceleration` ou `database_down`. As três execuções reais de 30/09 custaram **US$ 0,5607**
(35 chamadas; recomputado dos tokens com `ai.prices`, igual ao `spent_since_usd` 0,560749 do livro-caixa); o
`usd` das linhas é NULL por desenho (só chamadas cobradas por unidade, como imagem, gravam `usd`). Estimativa
para as provas restantes: 24.9 (Outlook→Instagram) ≈ US$ 0,6–1,0; 23.13 (login por perfil, 3 personas) ≈ US$
1,5–2,5; 27.2 (aceite integrado) ≈ US$ 0,7–1,0 — juntas US$ 2,8–4,5, **acima da margem utilizável de US$ 1,96**
(2,46 − 0,50) sem recarga; e todas dependem do P15.

### Cited Findings
- `GET /api/health` (observado): `status: degraded`, `commit 69970916a3…`, `migration 058_chaves_de_rede`,
  `database sqlite reachable`, `appium running (pid 52704)`, `sdk found C:\Android\Sdk`, `problems: [{code:
  "ai_balance_low", message: "Anthropic (Claude Console): Saldo estimado US$ 2,46, abaixo do aviso (US$ 3,00).
  Inclui US$ 0.09 gastos fora da plataforma (relatório do provedor).", hint: "Usada por: plan, decide, verify,
  escalation, social. Recarregue antes de chegar ao limite de bloqueio."}]`; modelos por função: plan/escalation
  `claude-opus-5-5`, decide/social `claude-sonnet-5`, verify `claude-haiku-4-5`; imagem `openai gpt-image-2`
  US$ 0,055/imagem.
- Composição (observado, `backend/app/state.py:2877`): `hard = {"sdk_missing", "no_acceleration", "database_down"}`;
  `status = "error" if any(p.code in hard …) else ("degraded" if problems else "ok")`. Problemas de saldo
  (`state.py:2589-2616`): só conta **em uso** vira problema; `blocked/exhausted` → `ai_balance_blocked`; `low` →
  `ai_balance_low`; `unknown` → `ai_balance_unknown`; `stale` → `ai_balance_stale`.
- ADR-051 (observado, `docs/decisoes.md:2549-2600`): "saldo = âncora − consumo desde a âncora"; Anthropic pelo
  `usage_report/messages` de hora em hora precificado por `ai.prices` ("validado em 28/09 contra `ai_calls`:
  5,4946 × 5,4693 US$ em 6 h"); "`warn_below` gera aviso: problema `ai_balance_low` e chip amarelo; `block_below`
  barra a IA daquela conta ANTES de gastar"; limites: bloqueio US$ 0,50, aviso US$ 3 na Anthropic ("queimou ~US$
  1/h nas baterias de 28/09"), US$ 2 na OpenAI, R$ 10 no Gemini.
- `GET /api/ai/balances` (observado): Anthropic `anchor_balance 3.1177` (`anchor_at 2026-09-29T17:46:19Z`,
  `fechamento diário`), `spent_since_usd 0.560749`, `estimated_balance 2.4643`, `warn_below 3.0`, `block_below
  0.5`, `provider_usd 0.653364`, `external_usd 0.092615`, `reconciled_at 2026-09-30T10:36:58Z`, `reconcile_error
  null`, `state low`, `stale false`, `in_use true`, `admin_key_configured true`. OpenAI: âncora 8.0885 (29/09
  16:22Z), gasto 0, `provider_usd 0.05421`, `state ok`. Gemini: R$ 29,37 (= US$ 5,65 a 5,2), `in_use false`,
  `admin_key_configured false`. `blocked: []`.
- Snapshots (observado, SQLite ro, `ai_balance_snapshots` anthropic): id 7 `2026-09-29T17:46:19Z` 3,1177
  `fechamento`; id 4 `2026-09-28T17:43:42Z` 4,53 `console` ("lido no Claude Console pela IDE"); id 1 `2026-09-28
  T16:16:49Z` 5,19 `console`.
- **Linhas de `ai_calls` das 3 execuções** (observado, SQLite ro; colunas `role, model, tier, calls, input_tokens,
  cache_read, cache_write, output_tokens, with_image, ok, usd`):
  - `r-20260930023442-bd5c5a`: decide opus-5-5 tier 1 ×2 (7 965 in, 8 450 cache_read, 8 450 cache_write, 523 out,
    2 c/ imagem); decide sonnet-5 ×15 (46 029 / 119 252 / 8 518 / 2 625, 9 c/ imagem); plan opus-5-5 ×1 (1 946 /
    0 / 6 546 / 2 007); verify haiku-4-5-20251001 ×3 (11 440 / 0 / 0 / 527). **Todos `usd = NULL`.**
  - `r-20260930023809-12d329`: plan opus-5-5 ×1 (1 943 / 6 546 / 0 / 1 956).
  - `r-20260930023901-13ec70`: decide opus-5-5 ×1 (3 256 / 8 450 / 0 / 266); decide sonnet-5 ×9 (30 018 / 76 662 /
    0 / 1 409); plan opus-5-5 ×1 (1 944 / 6 546 / 0 / 1 974); verify haiku ×2 (8 123 / 0 / 0 / 385).
  - Totais: 35 chamadas, 112 664 in, 225 906 cache_read, 23 514 cache_write, 11 672 out, `SUM(usd) = NULL`, ts
    02:35:04Z–02:41:08Z.
- Em toda a tabela (observado): 2 360 linhas, `SUM(usd) = 0.21454`, 2 355 com `usd NULL`; as únicas com `usd`
  são do provedor `openai` (imagens: 0,16033 em 28/09, 0,05421 em 29/09) e 1 `simulated` = 0.
- **Por que `usd` é NULL** (observado): o INSERT de `ai_calls` no executor não preenche `usd`
  (`backend/app/taskqueue/repository.py:811-820`, 21 colunas sem `usd`); só o gerador de imagem grava `usd`
  (`modules/identity/infrastructure/persona_images.py:141`). `planning/costs.py:80-81`: "`usd` (migração 048) é o
  custo DECLARADO de uma chamada cobrada por unidade — imagem — e vale no lugar dos tokens daquela linha; onde é
  nulo, a conta continua sendo tokens × preço do modelo". `planning/saldos.py:177-196` (`gasto_usd_por_conta`) soma
  `costs.row_usd(prices, linha)` para as linhas com `usd NULL` mais `usd_declarado`; `price_for` casa o prefixo mais
  longo (`costs.py:21-29`), por isso `claude-haiku-4-5-20251001` usa a tarifa de `claude-haiku-4-5`.
- **Tabela de preços** (observado, `config/config.example.yaml:229-234`; idêntica no `config.yaml` para os modelos
  usados): `claude-opus-5-5: [4.0, 0.2, 5.0, 20.0]`, `claude-sonnet-5: [2.0, 0.2, 2.5, 10.0]`, `claude-haiku-4-5:
  [1.0, 0.1, 1.25, 5.0]` (US$/M tokens: entrada, cache lido, cache gravado, saída). `GET /api/settings` não expõe
  `ai.prices` (só limites: `ai_max_usd_per_run`, `ai_max_usd_per_day`, …).
- **Recomputação** (observado, script desta pesquisa sobre os tokens acima): bd5c5a = US$ 0,3444 (decide opus 0,0863
  + decide sonnet 0,1635 + plan 0,0807 + verify 0,0141); 12d329 = 0,0482; 13ec70 = 0,1681 (0,0200 + 0,0895 + 0,0486
  + 0,0100). **Total 0,5607** = `spent_since_usd 0.560749` do livro-caixa (diferença de arredondamento).
- Evidência do 24.9 no estado (observado): "Custo total dessas provas: ~US$ 0,56 no livro-caixa da Anthropic
  (autorização até US$ 1,50)".

### Inferences
- `degraded` aqui é **informação de limiar**, não defeito: a conciliação está fresca (10:36Z, sem erro), nenhuma
  conta bloqueada, e a mensagem manda recarregar. A saúde só volta a `ok` com uma recarga registrada (âncora nova
  ≥ US$ 3) ou com o dono baixando `warn_below` em `ai_billing_accounts` — o segundo é maquiagem.
- O `usd` NULL "enquanto o livro-caixa contou US$ 0,56" não é inconsistência: são dois caminhos por desenho —
  tokens × `ai.prices` para chamadas de texto/visão; `usd` declarado só para cobrança por unidade. A conciliação
  com o relatório do provedor (`provider_usd 0.653364` desde a âncora) adiciona `external_usd 0.092615` de gasto
  fora da plataforma (`conciliacao.py:99-113`), o que explica o saldo 3,1177 − 0,5607 − 0,0926 = 2,4643.
- **Perfil de custo por execução real** (dos 3 runs): plano ≈ US$ 0,05–0,08 (1 chamada Opus com ~2 k in/2 k out);
  cada etapa executada ≈ 4–5 chamadas Sonnet com imagem + 1 verify Haiku ≈ US$ 0,04–0,06; escalonamento Opus na
  etapa ≈ US$ 0,02–0,04 cada. Uma execução completa de 4 etapas ≈ US$ 0,35; abortada no plano ≈ US$ 0,05;
  reiniciada no meio ≈ US$ 0,17.
- **Orçamento por prova (estimativa, sem assumir autorizações anteriores; cada uma precisa de autorização nova):**
  - **24.9 Outlook → Instagram só de leitura, 3 cenários** (indisponível / interrupção / retomada): espelho dos 3
    runs de 30/09 (US$ 0,56), com telas do Outlook mais densas e possivelmente mais etapas → **US$ 0,6–1,0**.
    Precondição: P15 resolvido (celular físico por USB, Outlook web no Chrome ou versão nova).
  - Precondições: P15, consentimento por conta no painel (hoje `consent_at` nulo nas 3), rede validada (25.9) no
    aparelho.
  - **27.2 aceite integrado** (mesmo aparelho, rede `trafego_verificado`, Outlook + Instagram com a conta certa e
    um comando entre os dois): a rede não custa IA; 1 login Outlook + 1 comando entre apps ≈ 0,35 + 0,35 → **US$
    0,7–1,0**. Pode reaproveitar 1 login do 23.13 se for na mesma sessão.
  - **Soma: US$ 2,8–4,5.** Saldo estimado US$ 2,46; bloqueio em 0,50 → margem utilizável ≈ US$ 1,96. Cabe **uma**
    prova (24.9 ou 27.2) sem recarga; as três exigem recarga (a partir de US$ 3 cobre e ainda tira o `degraded`).
    O teto configurado por execução/dia (`ai_max_usd_per_run`, `ai_max_usd_per_day`, valores em `GET /api/settings`)
    também precisa acomodar cada bateria.
- Nenhuma dessas provas é executável hoje, independentemente de saldo: todas dependem do Outlook abrir num
  aparelho (P15), que é decisão do dono.

### Gaps
- Tetos vigentes (observado, `GET /api/settings`): `ai_max_usd_per_run 15.0`, `ai_max_usd_per_day 10.0`,
  `ai_max_calls_per_objective 60`, `ai_max_tokens_per_run 3 000 000`, `max_ai_concurrency 4` — nenhum limita as
  provas acima; o limite efetivo é o saldo (bloqueio em US$ 0,50).
- O custo do Outlook em telas reais é extrapolado do QA Messenger/Chrome; sem uma execução, a densidade de telas
  do Outlook (e o número de etapas até a caixa de entrada) é chute com fator 1,5–2.
- O gasto "fora da plataforma" (US$ 0,09) não é atribuído a nada nesta pesquisa; o provedor só informa o total.
