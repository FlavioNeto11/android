# P16 (teste de vazamento só em memória) e a medição de UDP da sonda de saída (android-03, medição #6)

Notas de pesquisa, 30/09/2026, repositório `C:\git\android` em `6997091` (commit que o `/api/health` do central
informa). Somente leitura no repositório e no banco; nenhum deploy, reinício ou mudança de rede. Uma sonda de UDP
somente leitura foi rodada em android-03 e android-06 (registrada na questão 4).

Convenções destas notas:

- **Fuso**: o banco (`network_measurements.measured_at`, `commands.created_at`) e a API estão em UTC (`Z`); o
  `data/logs/backend.log` está em hora local (UTC−3). Quando os dois aparecem juntos, a hora local vem marcada.
- **IP de saída**: o endereço público medido é o do dono; aqui vai mascarado como `38.211.x.x`. O achado é "os quatro
  aparelhos saem pelo mesmo IP", que o próprio sistema já registra como `aviso` na medição.
- Fontes dos dados observados: [poc.sqlite3](C:/git/android/data/poc.sqlite3) (tabelas `network_measurements`,
  `device_network`, `network_profiles`, `network_keys`, `commands`, lidas com `sqlite3.connect('file:…?mode=ro', uri=True)`),
  [backend.log](C:/git/android/data/logs/backend.log) e [backend.log.2026-09-29](C:/git/android/data/logs/backend.log.2026-09-29),
  `GET /api/health` e `GET /api/instances` do central, e a sonda de 30/09 (Questão 4).
- Rótulos: **[observado]** = dado real do banco, log ou aparelho; **[código]** = trecho lido; **[externo]** = fonte
  fora do projeto; **[inferência]** = conclusão minha, com confiança.
- Fontes locais citadas como `arquivo::função Lnn-nn`; só há número de linha no que foi lido.

---

## Questão 1 — (A) O que o projeto persiste hoje, e por que cada reinício do backend reinicia aparelhos com `exigida_com_bloqueio`

### Takeaway
O resultado do teste de vazamento vive só em `_Memoria.vazamento` (por revisão); `network_measurements.leak_blocked`
é histórico sem revisão e ninguém o relê. O reinício do backend não dispara o teste na hora: ele **arma** a próxima
`verificar`, que chega em até 6 h (fim da validade, antecipado a 90%) com o aparelho livre — e aí para o cliente VPN e
reinicia. No dia 30/09 isso custou 1 reinício ao android-03, 4 reinícios + 1 reaplicação ao android-02 e 6 reinícios
+ 2 reaplicações ao android-06, por um único reinício do backend às 02:40Z.

### Cited Findings
- [código] A memória entre passadas é um dataclass em RAM, "em memória de propósito": `vazamento: dict[int, Vazamento]`
  (rev → teste), `verificacao_pedida`, `linha_de_base`, `espera_ate`, `reinicios` — [rede_convergencia.py::_Memoria L75-96](C:/git/android/backend/app/devices/rede_convergencia.py)
- [código] Na verificação com bloqueio, o teste só é pulado se `mem.vazamento.get(rev)` existir; sem ele, roda
  `sondar_vazamento` (para o cliente), guarda em memória "mesmo sem conclusão" e, sem o túnel de volta, chama
  `_religar_pelo_boot` — [rede_convergencia.py::_verificar L664-683](C:/git/android/backend/app/devices/rede_convergencia.py)
- [código] `_religar_pelo_boot` regride a linha a `configurado` e agenda `restart`; a medição fica para depois do boot
  — [rede_convergencia.py::_religar_pelo_boot L626-640](C:/git/android/backend/app/devices/rede_convergencia.py)
- [código] O único lugar que grava o resultado no banco é a coluna `leak_blocked` de cada medição
  (`INSERT INTO network_measurements(... udp_ok, per_app, leak_blocked, detail)`), sem revisão nem versão do cliente —
  [rede.py::_registrar_medicao L873-882](C:/git/android/backend/app/devices/rede.py); esquema em
  [057_rede_por_aparelho.sql](C:/git/android/backend/migrations/057_rede_por_aparelho.sql) (`leak_blocked INTEGER CHECK (... IN (0,1))`, `udp_ok` idem, `{{PK_AUTO}}`).
- [código] Ninguém lê `leak_blocked` de volta para decidir: `apps_sem_prova` relê só `per_app` da medição de
  `verified_at` — [rede.py::apps_sem_prova L411-426](C:/git/android/backend/app/devices/rede.py)
- [código] O que dispara a remedição num `trafego_verificado` com política exigida: `_validade_pede_medicao` — vencida
  (`validade_verificacao_s`, 21 600 s) ou, fora da porta, a partir de 90 % da validade (`_ANTECEDENCIA_DA_VALIDADE = 0.1`)
  — [rede_convergencia.py L70-72, L208-216](C:/git/android/backend/app/devices/rede_convergencia.py); `verificacao_vencida` conta de `verified_at` — [rede.py L401-408](C:/git/android/backend/app/devices/rede.py)
- [código] O `conferir` depois do reinício do backend só regride (nunca refaz o teste), e reconstrói a linha de base
  por UID quando ela não existe — [rede_convergencia.py::_conferir L532-552](C:/git/android/backend/app/devices/rede_convergencia.py). O
  docstring do módulo já dizia: "um reinício do backend zera a memória e dá mais uma chance" — L28-30.
- [código] `pedir_verificacao` (POST …/verify) limpa `mem.vazamento` de propósito e avisa no `reason` que o teste
  reinicia o aparelho — [rede_convergencia.py L575-591](C:/git/android/backend/app/devices/rede_convergencia.py)
- [observado] Reinício do backend: `servidor de rede: processo 33140 encerrado (encerramento do backend)` às
  23:39:57 local (L912) e `ouvindo em 127.0.0.1:8000` às 23:40:10 local (= 02:40Z, L918) —
  [backend.log.2026-09-29](C:/git/android/data/logs/backend.log.2026-09-29). O mesmo arquivo tem 19 linhas
  `ouvindo em 127.0.0.1:8000` no dia 29/09 (a última às 23:51:27 local): "vários deploys por dia" é o regime real.
- [observado] `device_network` hoje: android-02/03/06 `trafego_verificado` (verified_at 07:08Z, 08:01Z, 07:32Z);
  android-05 `parcial` ("o bloqueio fora da VPN não foi provado", verified_at 10:31Z) — `poc.sqlite3`, tabela
  `device_network`, leitura `mode=ro`.
- [observado] Sequência do android-03 (comandos `device.network`/`restart` em `commands`, UTC): verificar 02:29:45
  → teste feito (`leak_blocked:true`, "o túnel não voltou sozinho sem boot: reinicia") → `restart` 02:30:00–02:31:15 →
  conectar 02:31:45 → verificar 02:32:45–02:33:15 = **medição #6**, `trafego_verificado`. Depois do reinício do
  backend: verificar **07:57:39** (= 02:33Z + 0,9 × 6 h) → teste refeito → `restart` 07:57:55–07:59:09 → conectar
  07:59:39 → verificar 08:00:39–08:01:09 = medição #18.
- [observado] android-02: verificar 06:52:38 (= 01:27Z de #4 + 5,4 h) → teste refeito → restart 06:52:53; conectar
  06:54:38 "o túnel não subiu depois do boot" → restart 06:54:40; 06:56:38 `failed` "não subiu depois de 2
  reinício(s)"; reaplicar 07:02:38 → restart 07:02:48 → "não subiu" → restart 07:04:40 → conectado 07:06:39 →
  medição #11 07:08:08. **4 reinícios + 1 reaplicação.**
- [observado] android-06: verificar 07:06:39 (= 01:42Z de #5 + 5,4 h) → teste refeito → restarts 07:06:55, 07:08:47,
  falha 07:10:39, reaplicar 07:16:39 → restarts 07:16:50, 07:18:40, falha 07:20:39, reaplicar 07:26:39 → restarts
  07:26:50, 07:29:16 → conectado 07:30:39 → medição #14 07:32:09. **6 reinícios + 2 reaplicações.**
- [observado] Efeito colateral visível no cartão: no instante do `force-stop` do teste, a sonda de internet da
  plataforma registrou `android-03: sem internet: DNS não responde` (04:57:53 local = 07:57:53Z, entre o `verificar`
  e o `restart`) — `backend.log` L189.
- [observado] Mesmo padrão de "túnel não subiu depois do boot" ocorreu numa aplicação nova, sem teste de vazamento
  antes (android-03, 02:18:45–02:20:45Z, dois reinícios e `failed`), e o `config.example.yaml` já prevê a "queda do
  cliente no boot" em `reinicios_max: 2` — [config/config.example.yaml L340](C:/git/android/config/config.example.yaml).
- [código] A linha do handoff P16 descreve o problema e as duas saídas propostas ao dono (persistir com migração
  nova, ou `exigida` sem bloqueio) e diz que 059+ está reservada à Fase 28 — [docs/handoffs/terceira-evolucao.md L149](C:/git/android/docs/handoffs/terceira-evolucao.md)
- [código] A reserva: "migrações a partir da 059 (056–058 estão reservadas às Fases 23–27)… conferir em todos os
  branches"; 28.1 usa a 059 (`travas`) e 28.2 a 060 — [docs/design/pedidos-persistentes.md L532-539](C:/git/android/docs/design/pedidos-persistentes.md)
- [observado] Nenhum branch local ou remoto tem `backend/migrations/059_*` a `069_*` (varredura de
  `git for-each-ref refs/heads refs/remotes` + `git ls-tree`, 30/09).
- [código] O executor aplica `sorted(MIGRATIONS_DIR.glob("*.sql"))` e registra `version` + `checksum` (sha256 do
  texto renderizado) em `schema_migrations`; troca `{{PK_AUTO}}` por dialeto e aceita blocos `-- @dialect:` —
  [db.py L43-52, L552-561, L600-626](C:/git/android/backend/app/db.py). `docs-check.py` só exige que `docs/banco.md`
  cite cada arquivo `NNN_` — [scripts/docs-check.py L27, L212-226](C:/git/android/scripts/docs-check.py). Nada exige
  numeração sem lacuna.

### Inferences
- [inferência, alta] O mecanismo exato do P16 é "reinício do backend → memória vazia → a próxima `verificar` (validade
  a 90 %, `POST …/verify`, `ligou` de um aparelho que estava fora de `trafego_verificado`) refaz o teste destrutivo".
  Num dia com vários deploys, todo aparelho `exigida_com_bloqueio` livre é reiniciado pelo menos uma vez por deploy,
  dentro de ≤ 6 h. As contas reais (Bruno em android-03, André em android-06) estão no raio.
- [inferência, média] O custo não é "um reinício": cada reinício a mais rola o dado da queda do cliente no boot
  (observada também em aplicação nova), e a cadeia teto-de-2 → `failed` → reaplicar → mais 2 multiplica. O teste
  refeito à toa é o gatilho evitável; a fragilidade do boot é outro problema (fora deste escopo).
- [inferência, alta] Só `network_measurements` não serve como prova durável: não guarda revisão, versão do cliente nem
  identidade da configuração, e a semântica de `leak_blocked` é "o que a medição sabia", inclusive `NULL` para
  "adiado".

### Gaps
- Não li `observar()`/`Observacao` em `rede_aplicacao.py` (só a descrição em `parque.md` L414 e as evidências dos
  comandos); as leituras "always-on", "lockdown=1", "regras de bloqueio ativas" e "VPN CONNECTED" vêm daí.
- Não identifiquei se existe caminho de EDIÇÃO de perfil (troca de segredo/endpoint sem recriar) que não incremente
  `desired_rev`; vi só `criar_perfil`/`remover_perfil` no índice de `rede.py` (L285, L309). Fica como ponto a confirmar
  na implementação.

---

## Questão 2 — (A) Contrato de persistência da prova de vazamento: onde, chave, invalidação, três noções distintas, concorrência e reinício no meio

### Takeaway
Guardar a prova como colunas em `device_network` (não em tabela nova), chaveada por `(desired_rev, versão do cliente
VPN)` com data, detalhe e uma bandeira `leak_pending` gravada ANTES do `force-stop`. Revisão já cobre perfil, política,
endpoint e chave (toda atribuição/reaplicação incrementa `desired_rev`); wipe/identidade já chamam `invalidar`; a
versão do cliente é a única dimensão que hoje muda sem mexer na revisão. "Prova anterior válida", "saúde atual do túnel"
e "precisa de teste destrutivo" são três perguntas diferentes e a segunda já é respondida a cada `conferir`.

### Cited Findings
- [código] `atribuir` → `_gravar_desejado` faz `rev = int(row["desired_rev"]) + 1` e grava a nova revisão (linhas vistas
  por grep: L673, L679-680); `pedir_reaplicacao` faz `desired_rev=desired_rev+1, state='pendente'` (L724, L726) —
  [rede.py](C:/git/android/backend/app/devices/rede.py)
- [código] Toda gravação de estado é CAS com a revisão lida (`WHERE instance_id=? AND desired_rev=?`, e na medição
  também `AND applied_rev=? AND state=?`); observação de revisão velha vira só `detail` — [rede.py::registrar_observacao L780-799, ::_registrar_medicao L909-916](C:/git/android/backend/app/devices/rede.py)
- [código] Wipe, reset e troca de identidade física chamam `rede_convergencia.invalidar` (`_dados_do_aparelho_perdidos`
  e `_disco_apagado`) — [state.py L577-599](C:/git/android/backend/app/state.py); `invalidar` apaga a memória e
  regride a `pendente` — [rede_convergencia.py L323-337](C:/git/android/backend/app/devices/rede_convergencia.py)
- [código] O cliente VPN é instalado pela versão PROMOVIDA na loja (`releases.promoted_release(pkg)`), fora da revisão
  da rede — [rede_convergencia.py::_garantir_cliente L364-379](C:/git/android/backend/app/devices/rede_convergencia.py)
- [código] O `conferir` (ao ligar, ao acordar, depois do reinício do backend, a cada `deriva_s` = 900 s) relê
  configuração e túnel e regride: configuração sumiu → `pendente`; túnel caído → `configurado` e reinício —
  [rede_convergencia.py::_regredir_se_derivou L554-572](C:/git/android/backend/app/devices/rede_convergencia.py). Entre etapas de um objetivo, `reler_entre_etapas` faz a mesma leitura — L708-728.
- [código] A leitura inclui `always_on_vpn_app`, `always_on_vpn_lockdown`, `tun0`, `VPN CONNECTED` e "Lockdown filtering
  rules" do `dumpsys connectivity` — [docs/dominios/parque.md L407-414](C:/git/android/docs/dominios/parque.md); a
  evidência real dos comandos traz "always-on=io.nekohasekai.sfa; lockdown=1; tun0 no ar; VPN CONNECTED no dumpsys;
  regras de bloqueio ativas" (ex.: android-03, conectar 07:59:39Z).
- [observado] Na sonda de 30/09 (uid 2000, só leitura), os dois aparelhos respondem `dumpsys connectivity | grep -i
  lockdown` → `Lockdown filtering rules:` (linhas 261 e 253), `settings get secure always_on_vpn_app` →
  `io.nekohasekai.sfa`, `always_on_vpn_lockdown` → `1`.
- [código] Só `Permission denied` prova o bloqueio; IP = vazou; qualquer outra coisa = `None`; e "parar o servidor não
  serve" porque dá `Timeout` com bloqueio ligado ou desligado — [rede_medicao.py L18-26, L157-166](C:/git/android/backend/app/devices/rede_medicao.py)
- [código] `TesteDeVazamento.cliente_parado` existe justamente para "quem chama religar o cliente de qualquer jeito"
  depois do `force-stop`, inclusive quando a saída se perdeu — [rede_medicao.py L72-78, L138-156](C:/git/android/backend/app/devices/rede_medicao.py)
- [código] Ganchos de espera existentes que o desenho deve respeitar: `_vazamento_adiado` (objetivo no meio; celular
  sem worker) — L611-624; `_elegivel` recusa aparelho com comando de ciclo de vida em voo — L140-145; `_pedir_reinicio`
  só com o aparelho livre, 24 tentativas de 5 s — L744-777.
- [externo] Android: always-on "can start a VPN service when the device boots and keep it running while the device is
  on"; com "Block connections without VPN" "the system blocks any network traffic that doesn't use the VPN"; a
  página não documenta o comportamento quando o serviço é parado — [Android Developers, VPN](https://developer.android.com/develop/connectivity/vpn)
- [código] `ALTER TABLE … ADD COLUMN` não precisa de reconstrução; a marca `@foreign_keys:off` só existe para migrações
  que RECONSTROEM tabela no SQLite — [db.py L53-59](C:/git/android/backend/app/db.py)
- [código] Nota lateral que interage com qualquer validade: `_registrar_medicao` atualiza `verified_at` em toda
  medição com IP, inclusive as que deixam a linha em `parcial` — [rede.py L887-892](C:/git/android/backend/app/devices/rede.py);
  o android-05 em `parcial` tem `verified_at` avançando a cada 11 min (medições #7 a #32).

### Inferences
**Três noções, três perguntas (proposta):**

| Noção | Pergunta | Onde mora | Quem responde |
|---|---|---|---|
| Prova anterior válida | "o bloqueio foi provado para ESTA configuração e ESTE cliente?" | colunas novas em `device_network` | `_verificar`, antes de tocar no cliente |
| Saúde atual do túnel | "agora, always-on + lockdown + tun0 + VPN CONNECTED + regras de bloqueio estão no lugar?" | leitura `observar()` a cada `conferir`/`verificar`/entre etapas | já existe; regride por deriva |
| Precisa de teste destrutivo | "não há prova para (rev, versão do cliente), ou o dono pediu, ou o aparelho é outro" | decisão da convergência | novo `_prova_de_vazamento(row)` |

**Contrato (colunas em `device_network`, mesma linha, mesmo CAS):**

| Coluna | Tipo | Significado |
|---|---|---|
| `leak_rev` | INTEGER | revisão da prova (= `desired_rev` quando foi feita) |
| `leak_client` | TEXT | versão do cliente VPN (`version_name` ou `versionCode` do `pm dump`/release promovida) na hora da prova |
| `leak_result` | INTEGER CHECK (NULL, 0, 1) | 1 = `Permission denied` (bloqueado); 0 = VAZOU; NULL = inconclusivo (nunca conta) |
| `leak_at` | TEXT | data ISO da prova |
| `leak_detail` | TEXT | o `Vazamento.texto` (≤ 500) |
| `leak_pending` | INTEGER NOT NULL DEFAULT 0 | 1 = o `force-stop` foi (ou pode ter sido) disparado e o desfecho ainda não foi gravado |

Por que colunas e não tabela: a prova é UMA por aparelho e por revisão e o padrão de gravação CAS
(`WHERE instance_id=? AND desired_rev=?`) já existe na linha; uma tabela nova exigiria índice, limpeza e mais uma
junção na porta (`motivo_de_espera` roda a cada ~1 s por objetivo). `network_measurements.leak_blocked` fica como
histórico, sem mudança.

**Chave e invalidação (o que zera a prova):**

| Evento | Como invalida | Já existe? |
|---|---|---|
| atribuição, reaplicação, troca de perfil/política/endpoint/chave | `desired_rev` sobe → `leak_rev ≠ desired_rev` | sim (`_gravar_desejado`, `pedir_reaplicacao`) |
| reinstalação/atualização do cliente VPN | `leak_client ≠ versão lida no aparelho` (ler `pm dump` no `observar`, ou a release entregue por `_garantir_cliente`) | **não**: precisa entrar na leitura |
| wipe, reset, outro aparelho físico atrás do id | `invalidar` → `pendente` + `UPDATE … SET leak_rev=NULL, leak_result=NULL, leak_pending=0` | gancho existe; falta apagar as colunas |
| troca de segredo do perfil (se houver edição) | deve incrementar `desired_rev` dos aparelhos do perfil | a confirmar (Gaps) |
| `POST …/verify` | apaga as colunas além de `mem.vazamento.clear()` | falta |
| validade por idade | `leak_at` mais velho que `rede.vazamento_validade_s` (opcional, padrão 0 = sem vencimento) | recomendação na Questão 3 |
| `leak_result = 0` (vazou) | nunca "vale": permanece `parcial` e o painel mostra VAZOU até revisão nova | regra de `_falta_para_verificar` já exige `is True` |

**Concorrência e reinício no meio do teste:**
1. Antes de chamar `sondar_vazamento`, gravar `leak_pending=1, leak_rev=rev, leak_result=NULL, leak_detail='teste em
   curso desde <iso>'` com CAS por revisão. `rede_medicao` continua sem mudar estado (docstring L28-29).
2. Depois do teste: gravar `leak_result/leak_detail/leak_at/leak_client, leak_pending=0` no mesmo CAS. Se
   `cliente_parado` e o túnel não voltou, `_religar_pelo_boot` como hoje.
3. Na readoção (backend reiniciado) com `leak_pending=1`: **não** chamar `sondar_vazamento`; a linha vai direto a
   `configurado` com evidência "teste interrompido pelo reinício do backend; religa pelo boot", agenda o reinício,
   e `leak_result` fica NULL (inconclusivo). O `conferir` de hoje já regrediria por deriva se o `tun0` estivesse
   ausente; a bandeira serve para NÃO iniciar um segundo teste destrutivo e para nunca confiar num resultado
   parcial.
4. Dois disparos concorrentes (porta + varredura): já barrados por `_elegivel` (comando exclusivo em voo) e por
   `comando_no_trabalho`; a bandeira dá a segunda trava: com `leak_pending=1` de menos de N minutos, `_acao` devolve
   `None` para `verificar`.
5. Um teste cujo `leak_pending` ficou preso (crash antes do passo 2) expira em `_ESPERA_DA_RELEITURA_S` (300 s) para
   a convergência voltar a agir, sempre pelo caminho "religar pelo boot", nunca por outro `force-stop`.

**A saúde do túnel como evidência contínua entre testes destrutivos** — recomendação: sim, com uma condição. O
`Permission denied` que o teste mede é o efeito das mesmas "Lockdown filtering rules" que o `dumpsys` lista e do
`always_on_vpn_lockdown=1` que `settings` devolve; o teste destrutivo prova, uma vez por (rev, versão do cliente), que
regra → comportamento. Depois disso, a presença das regras a cada `conferir` (15 min), ao ligar, ao acordar e entre
etapas é evidência contínua; a ausência regride por deriva (`pendente`/`configurado`) e a prova continua guardada
mas não libera nada até a linha voltar a `conectado` → `verificar` (que, com prova válida, mede sem parar o cliente).
Condição: a leitura tem de continuar sendo feita como uid 2000 e conferir o `always_on_vpn_app` = pacote do perfil;
uma regra presente para OUTRO app não vale. [inferência, alta para a lógica; média para "as regras do dumpsys são
exatamente o que gera o EPERM", porque não li o código do `ConnectivityService`.]

**Migração (proposta; número a conferir em todos os branches na hora de criar):**

Número: a reserva 059–062 é da Fase 28, que não começou e não tem arquivo em branch nenhum. Duas opções válidas
para o executor (ordena por nome; lacuna é aceita): **063** (respeita a reserva) ou **059** (renumerando a Fase 28 no
`pedidos-persistentes.md`). Recomendo 059 se o dono aprovar antes de a Fase 28 abrir arquivo, senão 063; nos dois
casos, citar em `docs/banco.md` (docs-check) e rodar `git for-each-ref` + `git ls-tree` de novo antes do commit.

```sql
-- 0NN_prova_de_vazamento.sql — a prova do teste de vazamento por aparelho (P16): durável, por revisão e versão do
-- cliente VPN, com a bandeira do teste em curso. Compatível com SQLite e PostgreSQL: ADD COLUMN sem reconstrução.
-- Sem BEGIN/COMMIT: o executor abre a transação.
ALTER TABLE device_network ADD COLUMN leak_rev     INTEGER;
ALTER TABLE device_network ADD COLUMN leak_client  TEXT;
ALTER TABLE device_network ADD COLUMN leak_result  INTEGER CHECK (leak_result IS NULL OR leak_result IN (0, 1));
ALTER TABLE device_network ADD COLUMN leak_at      TEXT;
ALTER TABLE device_network ADD COLUMN leak_detail  TEXT;
ALTER TABLE device_network ADD COLUMN leak_pending INTEGER NOT NULL DEFAULT 0
    CHECK (leak_pending IN (0, 1));
```

Os dois dialetos aceitam `ADD COLUMN` com `CHECK` e `DEFAULT` constante (SQLite exige default constante para
`NOT NULL`; PostgreSQL preenche as linhas existentes com o default). Nada de `{{PK_AUTO}}` nem `@dialect:` aqui.

**Pontos de código (touchpoints):**

| Onde | O quê |
|---|---|
| `rede_convergencia.py::_verificar` L664-683 | trocar `mem.vazamento.get(rev)` por `rede.prova_de_vazamento(st, row, versao_do_cliente)`; gravar `leak_pending=1` antes de `sondar_vazamento`; gravar o desfecho depois; manter `mem.vazamento` só como cache |
| `rede_convergencia.py::pedir_verificacao` L575-591 | além de `mem.vazamento.clear()`, `rede.apagar_prova_de_vazamento(st, iid)` |
| `rede_convergencia.py::invalidar` L323-337 | apagar as colunas junto com a regressão |
| `rede_convergencia.py::_conferir`/readoção | se `leak_pending=1`: regredir a `configurado` + reinício, sem teste |
| `rede_medicao.py::medir` L211-227 | receber `Vazamento` reconstruído da linha (bloqueado/texto/medido_em) — sem mudança de assinatura |
| `rede.py` | `prova_de_vazamento(st, row, cliente) -> Vazamento | None` (só `leak_result` NOT NULL e `leak_rev == desired_rev` e `leak_client == cliente` e dentro da validade opcional), `gravar_prova_de_vazamento` (CAS por rev), `apagar_prova_de_vazamento`; DTO `DeviceNetworkDTO` ganha `leak_result`, `leak_at`, `leak_client`, `leak_pending` |
| `rede_aplicacao.py::observar` | devolver a versão do cliente (`dumpsys package <pkg> | grep versionName`) |
| `api.py` / painel | expor `leak_*` no cartão; "bloqueio provado em <data>, cliente x.y" |
| `docs/dominios/parque.md` L498-520, `docs/banco.md`, ADR novo | contrato e decisão |

**Testes de aceite (nomes + o que afirmam, no estilo de `tests/test_rede_aplicacao.py` com `Harness`, `AparelhoFalso`,
`Reinicios`, `_preparar`, `_linha`):**

| Teste | Afirma |
|---|---|
| `test_reinicio_do_backend_com_prova_valida_nao_reinicia_o_aparelho` | após `trafego_verificado` com `leak_result=1`, criar `ConvergenciaDeRede` nova (ou `_mem.clear()`), envelhecer `verified_at` a 95 % da validade e rodar `_passo("varredura")`: `ap.paradas` não muda, nenhum `restart` novo em `reinicios.pedidos`, medição nova gravada com `leak_blocked=1` vindo da prova, estado `trafego_verificado` |
| `test_prova_de_vazamento_e_por_revisao` | `pedir_reaplicacao` → nova revisão → `ap.paradas` sobe para 2 e `leak_rev` = 2 (equivale ao atual `test_vazamento_em_cache_so_vale_para_a_mesma_revisao`, agora contra o banco) |
| `test_cliente_atualizado_pede_prova_nova` | `AparelhoFalso` passa a devolver `versionName` diferente → próxima `verificar` para o cliente de novo; `leak_client` atualizado |
| `test_wipe_e_identidade_apagam_a_prova` | `invalidar` deixa `leak_rev/leak_result/leak_at` NULL e `leak_pending=0` |
| `test_post_verify_refaz_o_teste_e_avisa` | `pedir_verificacao` apaga a prova; o `reason` continua avisando o reinício |
| `test_tunel_caido_mantem_a_protecao_e_a_prova` | `AparelhoFalso` sem `tun0` → `configurado` + reinício (deriva); `leak_result` continua 1; ao voltar a `conectado`, a `verificar` mede SEM parar o cliente |
| `test_inconclusivo_nunca_vira_aprovacao` | `leak_result=NULL` (adiado ou sonda que falhou) → `parcial` com "não foi provado"; `leak_pending=1` de reinício no meio → `configurado` + reinício, sem `force-stop` (`ap.paradas` inalterado), e a medição seguinte só verifica depois de um teste completo |
| `test_leak_pending_gravado_antes_do_force_stop` | injetar exceção dentro de `sondar_vazamento` depois do `force-stop`: a linha tem `leak_pending=1` no banco antes do desfecho e `leak_result` NULL |
| `test_vazou_fica_parcial_ate_revisao_nova` | `leak_result=0` → `parcial` "VAZOU" a cada medição, sem novo teste, até `desired_rev` subir |
| `test_migracao_0NN_nos_dois_dialetos` | abrir pela fábrica configurada (`TEST_DATABASE_URL`), conferir colunas e `CHECK` (inserir 2 em `leak_result` falha) |

### Gaps
- Como obter a versão do cliente sem custo extra: candidato é acrescentar `dumpsys package io.nekohasekai.sfa | grep
  versionName` ao comando de `observar()` — não li a função para confirmar que o comando é composto num `shell` só.
- PostgreSQL: a proposta de SQL não foi executada em nenhum dos dois bancos (P17 continua `not_run`); a aceitação
  de `ADD COLUMN … CHECK … NOT NULL DEFAULT 0` no SQLite e no PostgreSQL é raciocínio meu sobre a sintaxe, não foi
  verificada contra a documentação nem por teste. O teste `test_migracao_0NN_nos_dois_dialetos` é o que a prova.
- Não confirmei se existe edição de perfil que troque o segredo sem passar por `atribuir`.

---

## Questão 3 — (A) A reverificação periódica (validade 6 h) deve refazer o teste destrutivo? Recomendação

### Takeaway
Não. A validade de 6 h deve continuar governando a MEDIÇÃO de saída (IP, DNS, UDP, apps), que é barata e não toca
no cliente; a prova de bloqueio deve valer por (revisão, versão do cliente) sem relógio, com um vencimento opcional em
dias, padrão desligado. Entre testes destrutivos, a presença de `always_on_vpn_lockdown=1` + "Lockdown filtering
rules" lida como uid 2000 a cada conferência é evidência contínua suficiente, porque a ausência delas já regride a
linha.

### Cited Findings
- [código] O objetivo da validade é o que a deriva não vê "com o túnel no ar (IP público do servidor trocado, app
  saindo por fora)" — [docs/dominios/parque.md L553-560](C:/git/android/docs/dominios/parque.md); nada ali fala em
  reprovar o bloqueio.
- [código] O próprio código considera o teste "uma vez por revisão… repetir a cada medição reiniciaria o aparelho em
  laço" — [rede_convergencia.py L86-89, L678-681](C:/git/android/backend/app/devices/rede_convergencia.py)
- [código] A validade aceita de 300 s a 7 dias, sem 0 — [docs/dominios/parque.md L553-556](C:/git/android/docs/dominios/parque.md)
- [observado] A cada teste destrutivo real de 30/09 seguiu-se pelo menos um reinício: 11 `restart` pedidos por
  `rede` entre 06:52Z e 07:59Z (android-02: 4; android-06: 6; android-03: 1), dos quais 3 diretamente pelo teste e 8
  pela queda do cliente no boot que se seguiu.
- [observado] Todos os 7 testes destrutivos concluídos em 30/09 (que precederam as medições #3, #4, #5, #6, #11,
  #14 e #18) deram `Permission denied` — nenhum "VAZOU" em aparelho com lockdown=1 lido.
- [externo] O `udp_timeout` do NAT de UDP do sing-box é 5 min por padrão — [sing-box, UDP NAT fields](https://sing-box.sagernet.org/configuration/shared/udp-nat/)
  (irrelevante para o bloqueio; citado porque a validade de 6 h também não tem relação com nenhum tempo do
  túnel).

### Inferences
- [inferência, alta] Refazer o teste por relógio troca uma garantia que não muda sozinha (o lockdown é propriedade do
  sistema, imposta pelo `ConnectivityService`, não pelo app) por reinícios certos de aparelhos com conta real. O risco
  que um teste periódico cobriria — o lockdown ter sido desligado por alguém no aparelho — já é visto pela deriva
  (`always_on_vpn_lockdown` ≠ 1 → `configuracao_ok` falso → `pendente`).
- [inferência, média] Vale ter `rede.vazamento_validade_s` (0 = nunca vence; sugestão de ativar em 7–30 dias só em
  aparelho sem conta real) para o caso "atualização do sistema Android" que não muda revisão nem cliente; a versão da
  imagem (`ro.build.fingerprint`) pode entrar na chave em vez de relógio, se se quiser cobrir isso sem tempo.
- [inferência, alta] Separar os dois relógios evita o efeito lateral atual em que `verified_at` avança em `parcial`
  (rede.py L887-892) e confunde "última saída medida" com "última prova".

### Gaps
- Não medi quanto tempo o Android leva para restabelecer o always-on depois de um `force-stop` sem boot (as provas
  reais do projeto viram os dois: "menos de um segundo" no android-05 em 29/09 e "não voltou" no 25.1 e em 30/09);
  isto decide se o teste destrutivo poderia algum dia deixar de custar um reinício, mas não muda a recomendação.

---

## Questão 4 — (B) O que aconteceu na medição #6 do android-03: dados, sonda de hoje e árvore de diagnóstico

### Takeaway
Na #6 quem falhou foi o NTP (`UDP DNS 83 B, NTP 0 B`), não o DNS; e a #21 do android-05 mostra o espelho (`DNS 0 B,
NTP 48 B`) num túnel no ar havia horas. A sonda de hoje deu 12/12 datagramas respondidos nos dois aparelhos, com
carga baixa. A causa mais provável da #6 é um falso negativo: um datagrama só, sem repetição, com 5 s de janela, no
exato minuto em que o convidado registrava carga 9,8 → 18,8 → 15,1 em 2 vCPU. `trafego_verificado` saiu porque a
regra de estado não olha `udp_ok`.

### Cited Findings
- [observado] `network_measurements` #6 (android-03, 02:33:15Z): `udp_ok=0`, `detail` = "… DNS da VPN 172.19.0.2,
  resolve | **UDP DNS 83 B, NTP 0 B** | apps: com.instagram.android=ok (VPN 122871 B × física 122871 B); shell=ok
  (VPN 4568 B × física 4568 B) | … Permission denied …"; `leak_blocked=1`; estado gravado `trafego_verificado`.
- [observado] #21 (android-05, 08:30:09Z): `udp_ok=0`, "**UDP DNS 0 B, NTP 48 B**", túnel conectado desde 01:0xZ; a
  #20 (08:19Z) e a #22 (08:41Z) do mesmo aparelho deram 83/48. Nenhum aviso de pressão de CPU no log entre 05:20 e
  05:31 local.
- [observado] Todas as outras 30 medições (#1–#5, #7–#20, #22–#32) têm `UDP DNS 83 B, NTP 48 B`, `udp_ok=1`.
- [observado] #18 (android-03, 08:01:09Z), com o MESMO ritmo pós-teste (restart 07:57:55–07:59:09, handshake visto
  pelo servidor às 07:59:37Z, medição 90 s depois), deu 83/48.
- [observado] Carga do convidado no minuto da #6 (log local): 23:31:21 `load 9.8 em 2 vCPU`, 23:31:49 `load 18.8`,
  23:32:19 `load 15.1`, 23:32:49 `load 9.3`; medição gravada 23:33:15 — `backend.log.2026-09-29` L833-838.
- [observado] Contexto do android-03 antes: sem internet desde antes da onda ("DNS não responde", 23:17 e 23:20
  local), 3 aplicações falhas ("não baixou o perfil"), `restart` pelo painel 02:14Z, aplicação ok 02:15Z, 2 reinícios
  sem túnel (02:16, 02:18Z), `failed`, reaplicar 02:26Z, reinício, conectado 02:28Z, teste de vazamento 02:29Z,
  reinício, conectado 02:31Z, medição #6 02:33Z — `commands` + `backend.log.2026-09-29` L807-838.
- [observado] `private_dns_mode` = `null` (PDNS=null) nos dois aparelhos hoje; `DnsAddresses: [ /172.19.0.2 ]` no
  `tun0`, igual nas 32 medições.
- [código] `udp_ok = dns.udp_dns and dns.udp_ntp`; `udp_dns` = `bytes > 12`, `udp_ntp` = `bytes >= 48` —
  [rede_medicao.py L225](C:/git/android/backend/app/devices/rede_medicao.py), [sonda_rede.py L272-278](C:/git/android/backend/app/devices/sonda_rede.py)
- [código] O comando: um datagrama por perna, `(printf …; sleep 3) | timeout 5 nc -u <alvo> <porta> | wc -c`, sem
  repetição — [sonda_rede.py::comando_dns_e_udp L122-140](C:/git/android/backend/app/devices/sonda_rede.py)
- [código + inferência] A perna "DNS UDP a 8.8.4.4" **não chega a 8.8.4.4**: o perfil do cliente tem a regra
  `{"protocol": "dns", "action": "hijack-dns"}` e o `dns-remoto` (UDP a `rede.dns`, 1.1.1.1, `detour: wg-out`) —
  [rede_aplicacao.py L259-270, L291](C:/git/android/backend/app/devices/rede_aplicacao.py) — e o próprio projeto
  descreve o efeito: "o cliente o sequestra e resolve pelo túnel" — [config.example.yaml L355](C:/git/android/config/config.example.yaml).
  Que o sing-box sequestre qualquer fluxo DNS farejado independentemente do destino é conhecimento externo não
  buscado nesta pesquisa (Gaps). A
  perna NTP é UDP cru: `tun` → `final: wg-out` → servidor do central → outbound `direct` do Windows →
  time.google.com — [rede_aplicacao.py L293-297](C:/git/android/backend/app/devices/rede_aplicacao.py), [rede_servidor.py L175-182](C:/git/android/backend/app/devices/rede_servidor.py)
- [código] A regra que decide o estado nunca lê `udp_ok` (só IP, `per_app`, `leak_blocked`) —
  [rede.py::_falta_para_verificar L813-829](C:/git/android/backend/app/devices/rede.py)
- [externo] toybox `nc`: em modo cliente chama `xconnectany()` (que faz `connect()` também em UDP) e entra em
  `pollinate()`; no EOF do stdin faz `shutdown(SHUT_WR)` e **continua lendo o socket** até o `-q`/timeout —
  [toybox toys/net/netcat.c](https://raw.githubusercontent.com/landley/toybox/master/toys/net/netcat.c), [toybox lib/net.c](https://raw.githubusercontent.com/landley/toybox/master/lib/net.c)
- [observado] **Sonda somente leitura de 30/09, 10:40–10:42Z** (comando gerado por `sonda_rede.comando_dns_e_udp`,
  via `adb -s <serial> shell`, `MSYS_NO_PATHCONV=1`, uid 2000 conferido, sem toque nem app; 3 rodadas completas + 3
  rodadas com pernas cronometradas por aparelho, um pouco mais que as "3 vezes" pedidas; saída bruta em
  `scratchpad/sonda_udp.txt`):
  - android-03 (emulator-5558): uptime 2h42, load 0,10/0,54/0,44, `tun0` presente; rodadas 1–3: `UDNS=83 UNTP=48`
    (10,3–10,4 s cada); pernas separadas: `T_DNS` 5,03–5,09 s, `T_NTP` 5,06–5,07 s, sempre 83/48.
  - android-06 (emulator-5564): uptime 3h12, load 0,10/0,45/0,59; rodadas 1–3: 83/48 (10,3–10,5 s); pernas 5,04–5,07 s.
  - `PDNS=null`, `RES=1`, `DNS=DnsAddresses: [ /172.19.0.2 ]` em todas.

  Invocação: `C:\Android\Sdk\platform-tools\adb.exe -s <serial> shell "<comando>"` a partir de um script Python
  (`subprocess.run`, `MSYS_NO_PATHCONV=1`), com o comando produzido por
  `app.devices.sonda_rede.comando_dns_e_udp(host_de_resolucao="api.ipify.org", alvo_dns_udp="8.8.4.4", alvo_ntp="time.google.com")`.
  Comando exato (o `printf` dos datagramas em octal, como no código):

  ```sh
  echo U=$(id -u); D=$(dumpsys connectivity 2>/dev/null); echo "DNS=$(echo "$D" | grep -o 'InterfaceName: tun0[^}]*' | grep -o 'DnsAddresses: [[][^]]*[]]' | head -1)"; echo PDNS=$(settings get global private_dns_mode 2>/dev/null); if ping -c1 -W3 api.ipify.org 2>&1 | grep -q '^PING'; then echo RES=1; else echo RES=0; fi; echo UDNS=$( (printf '\022\064\001\000\000\001\000\000\000\000\000\000\007\145\170\141\155\160\154\145\003\143\157\155\000\000\001\000\001'; sleep 3) | timeout 5 nc -u 8.8.4.4 53 2>/dev/null | wc -c); echo UNTP=$( (printf '\033\000…(47 × \000)'; sleep 3) | timeout 5 nc -u time.google.com 123 2>/dev/null | wc -c); echo FIM=1
  ```

  Saída bruta (transcrita do `stdout`; a perna cronometrada usou os mesmos datagramas com `S=$(date +%s.%N)` antes
  e `bc` depois de cada `nc -u`, mais `cat /proc/loadavg`):

  ```text
  ===== android-03 (emulator-5558) =====
  contexto: 2000 |  07:40:35 up  2:42,  0 users,  load average: 0.10, 0.54, 0.44 | 2026-09-30T10:40:35Z | 0.10 0.54 0.44 1/1812 10895 | lrwxrwxrwx 1 root root 0 2026-09-30 07:40 /sys/class/net/tun0 -> ../../devices/virtual/net/tun0
  --- rodada 1: 10.4 s   U=2000 / DNS=DnsAddresses: [ /172.19.0.2 ] / PDNS=null / RES=1 / UDNS=83 / UNTP=48 / FIM=1
  --- rodada 2: 10.3 s   (idêntica: UDNS=83 UNTP=48)
  --- rodada 3: 10.4 s   (idêntica: UDNS=83 UNTP=48)
  --- pernas separadas, rodada 1: 10.3 s -> U=2000 | UDNS=83 T_DNS=5.089516995 | UNTP=48 T_NTP=5.071814413 | 0.13 0.49 0.42 1/1815 11048
  --- pernas separadas, rodada 2: 10.2 s -> U=2000 | UDNS=83 T_DNS=5.025765888 | UNTP=48 T_NTP=5.063664600 | 0.35 0.53 0.44 1/1818 11082
  --- pernas separadas, rodada 3: 10.2 s -> U=2000 | UDNS=83 T_DNS=5.040502889 | UNTP=48 T_NTP=5.062009838 | 0.29 0.51 0.43 2/1815 11109
  --- lockdown/always-on: "261:  Lockdown filtering rules:" / always_on_vpn_app=io.nekohasekai.sfa / always_on_vpn_lockdown=1
  ===== android-06 (emulator-5564) =====
  contexto: 2000 |  07:41:37 up  3:12,  0 users,  load average: 0.10, 0.45, 0.59 | 2026-09-30T10:41:37Z | 0.10 0.45 0.59 4/1793 12757 | lrwxrwxrwx 1 root root 0 2026-09-30 07:41 /sys/class/net/tun0 -> ../../devices/virtual/net/tun0
  --- rodada 1: 10.5 s   U=2000 / DNS=DnsAddresses: [ /172.19.0.2 ] / PDNS=null / RES=1 / UDNS=83 / UNTP=48 / FIM=1
  --- rodada 2: 10.3 s   (idêntica)
  --- rodada 3: 10.3 s   (idêntica)
  --- pernas separadas, rodada 1: 10.2 s -> U=2000 | UDNS=83 T_DNS=5.041659668 | UNTP=48 T_NTP=5.037432249 | 0.41 0.50 0.60 1/1792 12902
  --- pernas separadas, rodada 2: 10.2 s -> U=2000 | UDNS=83 T_DNS=5.049450801 | UNTP=48 T_NTP=5.059987593 | 0.35 0.49 0.59 1/1792 12930
  --- pernas separadas, rodada 3: 10.2 s -> U=2000 | UDNS=83 T_DNS=5.039462869 | UNTP=48 T_NTP=5.071237672 | 0.29 0.47 0.59 1/1792 12969
  --- lockdown/always-on: "253:  Lockdown filtering rules:" / always_on_vpn_app=io.nekohasekai.sfa / always_on_vpn_lockdown=1
  ```

  (`toybox nc` sem argumentos respondeu só `nc: bad argument count`; a ajuda não foi lida no aparelho — o comportamento
  do `-u` vem da fonte do toybox, abaixo.)
- [externo] `mtu` padrão do endpoint WireGuard do sing-box é 1408 — [sing-box, WireGuard endpoint](https://sing-box.sagernet.org/configuration/endpoint/wireguard/); o projeto usa 1408 no servidor e `tun` com 1400 no cliente — [config.example.yaml L347](C:/git/android/config/config.example.yaml), [rede_aplicacao.py L293](C:/git/android/backend/app/devices/rede_aplicacao.py)

### Inferences
**Árvore de diagnóstico para `udp_ok=false`:**

1. Qual perna? `UDNS=0` → resolvedor do sing-box (upstream 1.1.1.1 pelo túnel) não respondeu em 5 s; `UNTP=0` → UDP
   cru pelo túnel + NAT do servidor + saída do Windows não voltou em 5 s; os dois 0 → túnel/CPU/rota.
2. A outra perna passou na mesma ida? Sim → o túnel carregava UDP naquele instante; a perda é de UM fluxo.
3. Carga do convidado (`/proc/loadavg`, eventos "pressão de CPU") no minuto? Alta → falso negativo provável.
4. Repetir 3× com carga baixa dá 3/3? Sim → funcional; a falha foi transitória.
5. Persistente numa perna só → NTP: NAT/firewall/`udp_timeout` do servidor, `time.google.com` limitando; DNS:
   upstream `rede.dns` fora ou `dns-remoto` sem `detour`.
6. Persistente nas duas com IPv4 ok → regra `network: udp → reject` (cadeia com proxy HTTP: esperado) ou MTU.

**Hipóteses para a #6, por plausibilidade:**

| # | Hipótese | Evidência a favor | Contra | Confiança |
|---|---|---|---|---|
| H1 | Falso negativo: um datagrama, sem repetição, 5 s, com o convidado a load 18,8 em 2 vCPU (WireGuard em espaço de usuário no sing-box compete por CPU) | carga registrada no minuto exato; 0 B = nada chegou (não truncado); outra perna passou; 12/12 hoje com carga baixa; #21 mostra a mesma classe de perda na outra perna | não dá para reproduzir a #6 retroativamente | alta |
| H2 | (#21) Timeout do upstream de DNS do sing-box (1.1.1.1 via wg-out) numa consulta | perna DNS é resolvida pelo sing-box, não por 8.8.4.4; NTP passou na mesma ida | sem pressão logada; caso único | média |
| H3 | Reconexão recente do túnel (rota/handshake) | #6 veio 90 s depois do handshake | #18 com o mesmo ritmo passou; #21 com túnel de horas falhou | baixa |
| H4 | NAT/firewall do Windows para UDP de saída do sing-box `direct` | — | NTP passou em 31 de 32 medições e 6/6 hoje pelo mesmo caminho | muito baixa |
| H5 | MTU/fragmentação (1408/1400) | — | 48 B e 83 B, muito abaixo de qualquer MTU | muito baixa |
| H6 | Regra por UID / lockdown | — | uid 2000 é o mesmo em todas; IPv4 e Instagram ok na mesma ida | muito baixa |
| H7 | `nc -u` do toybox lê um datagrama e sai / fecha no EOF do stdin | — | `pollinate` continua lendo até o timeout; medido T≈5,0 s por perna | refutada |
| H8 | `private_dns_mode` diferente no android-03 | — | `null` nos dois hoje; `resolve` = 1 na #6 | refutada para a #6 |

**Experimento que separa falso negativo de falha funcional** (`not_run`, precisa de autorização por gerar carga):
repetir a sonda (a) 3× em repouso, (b) 3× durante uma tarefa real no aparelho ou com carga induzida
(`yes > /dev/null &` como uid 2000, sem UI) e (c) com 3 datagramas por perna dentro dos mesmos 5 s. Se (a) e (c)
passam e só (b) com 1 datagrama falha, é falso negativo por latência; se (b) e (c) falham numa perna, é o caminho
daquela perna.

**Correção barata e durável**: o `detail` deve carregar por perna os bytes, o tempo até a resposta e quantas
tentativas (ex.: `UDP DNS 83 B/1 (0,4 s), NTP 0 B/3 (5 s)`), e a sonda deve mandar 2–3 datagramas espaçados dentro
da mesma janela de 5 s (só o primeiro respondido conta). Assim o próximo `udp_ok=false` chega com o diagnóstico.

### Gaps
- Não há log do sing-box do cliente (SFA) no aparelho acessível a uid 2000 para ver se o datagrama NTP da #6 saiu
  pelo túnel; o log do servidor (`data/rede/…`, `log_max_mb`) não foi aberto nesta pesquisa.
- A carga do host (Windows) no minuto da #6 não foi lida; só a do convidado.
- A documentação do sing-box sobre a ação de rota `hijack-dns` (sequestro de qualquer fluxo DNS farejado) não foi
  buscada; a afirmação de que a perna "8.8.4.4" é respondida pelo resolvedor do sing-box apoia-se na regra do perfil e
  no comentário do `config.example.yaml`.

---

## Questão 5 — (B) O que `trafego_verificado` deve significar por política; como classificar o resultado parcial; alcance da prova por UID; medir durante reconexão; prova através de reinício/hibernação; `rede.sonda.abrir_apps`

### Takeaway
`trafego_verificado` hoje = IP público medido pelo túnel + cada app exigido `ok` por UID + (com bloqueio) prova de
`Permission denied`; UDP não entra e IPv6 preso é esperado. Proposta: manter isso como piso, tratar UDP como duas
dimensões (`udp_dns`, `udp_ntp`) interpretadas pelo perfil — exigidas só em VPN pura, "esperado falso" em cadeia com
proxy HTTP, informativo em SOCKS5 — e SÓ passar a segurar tarefa por UDP depois de a sonda ganhar repetição, senão
a #6 e a #21 teriam segurado tarefas à toa. `abrir_apps` fica `false`: a janela acumulada desde a conexão já provou o
Instagram em background nas quatro medições reais com conta.

### Cited Findings
- [código] Regras atuais de `trafego_verificado`: IP v4 ou v6 público; cada `apps_exigidos` medido e `ok`; sem app
  exigido, ao menos um app `ok`; com `exigida_com_bloqueio`, `leak_blocked is True`. IP medido com algo faltando =
  `parcial`; sem IP = estado não muda — [rede.py::_falta_para_verificar L813-829, ::registrar_medicao L832-844, L901-908](C:/git/android/backend/app/devices/rede.py)
- [código] T3 da terceira evolução: "Com proxy HTTP, UDP é bloqueado (só DNS pelo túnel); com SOCKS5, segue se o
  provedor aceitar" — [docs/design/terceira-evolucao.md L109](C:/git/android/docs/design/terceira-evolucao.md);
  no perfil, cadeia HTTP acrescenta `{"network": "udp", "action": "reject"}` e sem VPN o DNS vai em TCP pelo proxy —
  [rede_aplicacao.py L283-288](C:/git/android/backend/app/devices/rede_aplicacao.py)
- [código] "na cadeia com SOCKS5 do 25.1 o DNS seguia e o NTP se perdia; medir só o DNS esconderia isso" —
  [sonda_rede.py L100-102](C:/git/android/backend/app/devices/sonda_rede.py); "a medição viu as associações do SOCKS5
  expirarem" — [rede_aplicacao.py L255-256](C:/git/android/backend/app/devices/rede_aplicacao.py)
- [externo] sing-box: outbound SOCKS tem `network` (`tcp`/`udp`, os dois por padrão) e `udp_over_tcp`; a página do
  outbound HTTP não menciona UDP — [sing-box SOCKS outbound](https://sing-box.sagernet.org/configuration/outbound/socks/), [sing-box HTTP outbound](https://sing-box.sagernet.org/configuration/outbound/http/)
- [código] IPv6 "No route to host" é o esperado com `strict_route` ("IPv6 inalcançável em vez de vazar") —
  [rede_aplicacao.py L253](C:/git/android/backend/app/devices/rede_aplicacao.py), [parque.md L483-484](C:/git/android/docs/dominios/parque.md); as 32
  medições reais têm `egress_ipv6=NULL` com esse motivo.
- [código] Prova por UID: `ok` = delta na VPN (tipo 17) ≥ delta na física menos folga (512 B ou 2 %); `fora_da_rede`
  = VPN 0 ou física > VPN + folga; `nao_medido` = sem tráfego na janela; contador que anda para trás vira zero —
  [sonda_rede.py::Cobertura L342-374](C:/git/android/backend/app/devices/sonda_rede.py). Só `tag=0x0`, seção "UID
  stats" — L313-339. A janela dos apps é acumulada desde a conexão (`linha_de_base`), a do shell é só a passada —
  [rede_medicao.py L13-16, L176-209](C:/git/android/backend/app/devices/rede_medicao.py)
- [observado] Nas quatro medições reais com conta vinculada, o Instagram ficou `ok` sem a sonda abrir nada
  (`opened: []`): #5 android-06 360 649 B, #6 android-03 122 871 B, #14 android-06 535 206 B, #18 android-03
  45 705 B — `commands.result` das verificações e `network_measurements.per_app`.
- [código] Durante reconexão: `_observar_depois_do_boot` espera o `tun0` até `espera_tun_s` (60 s) contado do boot;
  `_verificar` relê e regride antes de medir; `medir` só roda com o túnel no ar — [rede_convergencia.py L461-470, L659-663](C:/git/android/backend/app/devices/rede_convergencia.py)
- [código] `conectar` põe `verificacao_pedida=True` para medir no próximo ponto seguro e NÃO refaz o teste de
  vazamento guardado — [rede_convergencia.py L499-503](C:/git/android/backend/app/devices/rede_convergencia.py)
- [código] Quem chama ao acordar/readotar: `vitrine.trabalho_ao_ligar` (boot, wake, readoção depois do reinício do
  backend ou do worker, varredura de 60 s) — [rede_convergencia.py L17-19](C:/git/android/backend/app/devices/rede_convergencia.py)
- [observado] Cada medição completa custa ~30 s de aparelho (comandos `verificar` de 02:32:45 a 02:33:15Z etc.);
  a perna UDP sozinha ~10 s (5 s × 2 pernas, sempre até o `timeout`, medido hoje).

### Inferences
**Semântica proposta de `trafego_verificado` por política** [inferência, alta para o piso; média para as
dimensões novas]:

| Verificação | `livre` | `exigida` (VPN pura) | `exigida` (cadeia c/ proxy HTTP) | `exigida` (SOCKS5) | `exigida_com_bloqueio` (qualquer perfil) |
|---|---|---|---|---|---|
| IPv4 público pelo `tun0` | informativo | **obrigatório** | **obrigatório** | **obrigatório** | **obrigatório** |
| IPv6 | informativo | "sem saída" aceito (strict_route) | idem | idem | idem |
| resolvedor da VPN + nome resolve | informativo | **obrigatório** (`dns_resolver` do tun0 e `RES=1`) | obrigatório | obrigatório | obrigatório |
| `udp_dns` (resolvedor do sing-box responde em UDP) | informativo | obrigatório **depois da sonda com repetição** | obrigatório (DNS via hijack segue) | obrigatório | como na política base |
| `udp_ntp` (UDP cru pelo túnel) | informativo | obrigatório **depois da sonda com repetição** | **esperado falso** (não pode segurar; painel "UDP recusado por desenho") | informativo com aviso (provedor decide) | como na política base |
| cada app exigido `ok` por UID | informativo | obrigatório | obrigatório | obrigatório | obrigatório |
| prova de bloqueio (`leak_result=1`, rev e cliente atuais) | — | — | — | — | **obrigatório** |
| saúde contínua (always-on, lockdown, regras) | — | always-on | always-on | always-on | always-on + lockdown + regras |

**Classificação do resultado parcial** (IPv4 + apps ok, UDP falho):
- Hoje: `trafego_verificado` (UDP não entra) — é o que aconteceu na #6 e na #21, sem aviso no cartão.
- Proposto, em duas fases: (1) já: gravar `udp_dns`/`udp_ntp` separados no `detail`/DTO e mostrar no cartão "UDP:
  DNS ok, NTP sem resposta (1 tentativa)" com nível `warn`, sem mudar estado; (2) depois da sonda com 2–3 datagramas:
  em VPN pura, UDP falho nas 3 tentativas → `parcial` com "UDP pelo túnel não provado", como as outras faltas; em
  cadeia HTTP, `udp_ntp` falso = esperado; em SOCKS5, aviso. Nunca deixar `udp_ok` NULL virar aprovação (hoje o
  modelo já aceita NULL como "não medido").
- Painel: um cartão com dimensões (IPv4 / IPv6 / DNS / UDP-DNS / UDP-NTP / apps / bloqueio / saúde) cada uma
  `ok` / `esperado` / `não medido` / `falhou`, e o estado derivado abaixo.

**Alcance e limites da prova por UID** [inferência, alta]:
- Prova: bytes do UID que saíram pela física foram contabilizados também na VPN (tipo 17), na janela acumulada — o
  Android reatribui ao app o tráfego que o cliente VPN encaminha, então "VPN = física" é a assinatura de cobertura.
- Limites: (a) não vê CONTEÚDO nem destino (um app pode falar com um host errado pelo túnel e continuar `ok`); (b)
  `nao_medido` para app parado ou não instalado; (c) folga de 512 B/2 % esconde vazamento pequeno (o uid 0 no 25.1
  vazou 816 B, acima da folga por pouco); (d) a janela acumulada esconde um vazamento antigo já contado como
  `fora_da_rede`? não — o contrário: uma vez visto, não some, o que é o desejado; (e) contador reiniciado (boot)
  zera a janela, e a `linha_de_base` guardada no `conectar` da revisão ancora o começo; (f) é prova do UID, não do
  processo — apps com `sharedUserId` somam.

**Como medir IPv4/IPv6/DNS/UDP durante reconexão** [inferência, média]: manter a ordem atual (relê → `tun0` no ar
→ `VPN CONNECTED` → mede), mas (1) esperar o handshake visto no servidor (`_par_no_servidor` já lê o log) ou 10–15 s
depois de `VPN CONNECTED` antes da perna UDP; (2) as pernas UDP com 2–3 datagramas na janela; (3) se IPv4 passou e
UDP não, registrar como "UDP não provado nesta passada" e remedir só UDP em `reverificar_s` (600 s) em vez de repetir
a medição inteira; (4) sem `tun0` nunca medir (já é assim).

**Preservar a prova através de reinício/hibernação sem laços de teste** [inferência, alta]: com a prova durável da
Questão 2, o `ligou`/wake/readoção segue o caminho de hoje (`conferir` regride só por deriva; `conectar` pede a
medição no próximo ponto seguro) e a `verificar` encontra `leak_result=1` para (rev, cliente) e mede sem parar o
cliente. Os laços conhecidos ficam cobertos: (a) reinício do backend (P16) — prova no banco; (b) teste interrompido —
`leak_pending`; (c) `parcial` por "adiado" com objetivo eterno (android-05 mede a cada 11 min desde 06:29Z, 26
medições sem teste) — a bandeira não resolve isto; é a espera `reverificar_s` que deveria crescer (5, 15, 45, 60 min
como `_falhou`) enquanto o motivo do adiamento for o mesmo.

**`rede.sonda.abrir_apps`** [inferência, alta]: manter `false`. Alternativas em ordem: (1) medir por UID só com a
janela acumulada e o tráfego de fundo do próprio app — já basta na prática (4/4 medições reais com Instagram `ok`
sem abrir); (2) abrir só com tarefa segurada pela porta (já implementado, `motivo == "tarefa"`; é o que a tarefa
faria, e uma vez); (3) tratar "cobertura por UID = não medida" como dimensão separada que não segura a tarefa mas
aparece no cartão — aceitável para app instalado e nunca aberto, porque a porta abre o app na primeira tarefa; (4)
`abrir_apps: true` — rejeitar: abre app de conta real fora de tarefa autorizada (ADR-056 §7) e ganha nada que (1)
e (2) não dêem.

### Gaps
- A tabela por política é proposta minha; o ADR-056 §3 define só "trafego_verificado libera a tarefa" e o T3 diz que
  UDP some com proxy HTTP — não há decisão do dono sobre UDP como critério.
- Não há medição real com cadeia de proxy (HTTP ou SOCKS5) nesta base (as 32 são VPN pura no servidor do central);
  a semântica "esperado falso" vem do desenho (`reject`) e da nota do 25.1, não de dado desta onda.
- Não confirmei no código do Android que o `netstats` reatribui ao UID do app o tráfego encaminhado pelo cliente
  VPN em todos os casos (o projeto mediu "tipo 17 = tipo 1" no 25.1 e nas 32 medições).
