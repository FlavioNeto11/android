# Laço de pedidos: desenho de implementação (item 28.4)

Desenho do item 28.4 da Fase 28 ("Laço de pedidos: materializar, janela, coalescer, sobreposição, despacho
idempotente por `RunService.create`, fechamento pelo gancho e pela varredura, retomada depois de reinício"). A
pesquisa e o modelo estão em [`pedidos-persistentes.md`](pedidos-persistentes.md) §6 e §7.2–7.9 (ADR-059). Aqui fica
**como** implementar, conferido no código em 02/10/2026 contra `origin/main` `2fc101ca` e contra a árvore integrada
`jev/integ-28` (main + 28.1 + 28.2, ainda não mergeada).

Convenção das referências: `main:` é código já na main; `integ:` é código do 28.1/28.2 na árvore integrada
(`.claude/worktrees/p28-integ`). **INFERRED** marca o que é dedução, não leitura do código.

## 0. O que já existe e o que o desenho encontrou no código

| Peça | Onde | O que importa para o laço |
|---|---|---|
| Trava de líder | integ: `backend/app/taskqueue/travas.py` | `Lideranca.tomar` (81–117) devolve o token do mandato ou `None`; `cercada(nome, token)` (137–142) é a transação cuja escrita só vale com o mandato vigente; `soltar_da_queda` (158–170) na partida; `TRAVAS_DOS_LACOS` (45–50) é a lista renovada |
| Uso da trava nos laços | integ: `backend/app/state.py` | `soltar_da_queda` + `_manter_travas` + `_laco_das_travas` na partida (2245–2249); `_lider(nome)` (2472–2485) devolve token ou pula a volta; `_saldos_uma_vez` faz a escrita não idempotente dentro de `cercada` (2689) |
| Modelo | integ: `backend/migrations/067_pedidos.sql` | `pedido_ocorrencias.chave` UNIQUE (97) e `UNIQUE (pedido_id, gatilho_id, previsto_para)` (114); `materializada_token`, `dono`, `prazo_posse` (108–110); `pedido_gatilhos.cursor` (85); `pedidos.proxima_em` + índice `(estado, proxima_em)` (70, 78); `runs.pedido_id/ocorrencia_id/prioridade` (120–123) |
| Estados | integ: `backend/app/modules/pedidos/domain/estados.py` | `OCORRENCIA_NASCE_EM` = prevista/devida/pulada/perdida (105); tabela da ocorrência (107–132); `transicionar_ocorrencia` exige motivo em pulada/perdida/cancelada/falhou/incerta (163–168); `transicionar_pedido` com ator (143–160) |
| Chave | integ: `backend/app/modules/pedidos/domain/chave.py` | `formatar_instante` (49–53, UTC no segundo, sufixo `Z`); `chave_da_ocorrencia` (63–80); `chave_da_tentativa` = `chave:t<n>` (83–96); ids de até 28 caracteres (41) |
| Recorrência | main: `backend/app/modules/pedidos/domain/recorrencia.py` | `proximas(regra, inicio_local, fuso, depois_de=, limite=)` estritamente depois de `depois_de` (375–389), no máximo `LIMITE_PREVIA` = 1000 por chamada (59); recalcula desde `DTSTART` a cada chamada (`ocorrencias`, 358–372) |
| Criação de execução | main: `backend/app/taskqueue/service.py` | `RunService.create` (148–208) valida tudo ANTES de `repo.create_run` (203–205); conflito de chave devolve a mesma execução (`repository.py` 165–168) |
| Gancho de fim | main: `backend/app/taskqueue/scheduler.py` | `on_run_settled` (191) só é chamado por `_settle_run` (1294–1306), e este só no `finally` do worker (1290) |
| Laço de hoje para imitar | main: `scheduler.py` | `wake()` (240–241) + `_loop` com `wait_for(self._wake.wait(), timeout=tick)` (254–267) |

Achados que mudam o desenho (cada um tem consequência adiante):

- **A1. O gancho não vê todo fim de execução.** `on_run_settled` só dispara quando um worker de aparelho termina
  (`scheduler.py:1279-1292`). Execução que falha no planejamento (`service.py`, `_plan`, `set_run_status(failed)`),
  que é cancelada antes de iniciar (`service.py:1177-1182`) ou cancelada sem worker vivo (`_finish_cancel` →
  `recompute_run`, `scheduler.py:1895-1912`) assenta sem gancho. A varredura não é só a rede de segurança da queda:
  é o caminho normal desses casos (§5).
- **A2. `RunService.create` não é uma consulta idempotente pura.** Ela confere credencial, alvos, pré-voo e IA
  configurada (`service.py:149-200`) antes de chegar ao `INSERT … ON CONFLICT` (`repository.py:159-168`). Repetida
  depois de uma queda, pode levantar `RunError` (aparelho que ficou offline, `ai_not_configured`) mesmo com a execução
  daquela chave já existindo. O laço procura a execução pela chave ANTES de chamar `create` — o mesmo padrão de
  `assistente.py:160-165` (§4).
- **A3. `RunService.create` tem de rodar na thread do laço de eventos.** `_spawn_planning` usa
  `asyncio.create_task` (`service.py:820-826`); a rota `POST /api/runs` é `async` e chama `create` direto
  (`api.py:2822-2828`). O laço de pedidos não pode mandá-la para `asyncio.to_thread`.
- **A4. `RunCreate` não leva pedido nem ocorrência**, e tem `extra="forbid"` (`models.py:1606-1628`); `create_run`
  não grava `pedido_id`/`ocorrencia_id` (`repository.py:159-163`). Precisa de um parâmetro interno (§4, D3).
- **A5. Dois formatos de instante.** `previsto_para` usa `formatar_instante` (`…T12:00:00Z`, `chave.py:49-53`); o resto
  do esquema usa `to_iso` (`…T12:00:00.123Z`, `util.py:18-19`). Comparados como TEXT dentro do mesmo segundo, `Z`
  (0x5A) é maior que `.` (0x2E): `"12:00:00Z" <= "12:00:00.500Z"` é falso. Regra: `previsto_para` e `proxima_em` só
  se comparam com `formatar_instante(agora)`; `prazo_posse` com `to_iso`, como as travas.
- **A6. Editar o pedido não pode recriar a ocorrência do mesmo instante.** A chave não tem versão
  (`chave.py:63-80`) e o `UNIQUE (pedido_id, gatilho_id, previsto_para)` também não: cancelar a `devida` da versão
  velha e inserir a da nova com `ON CONFLICT DO NOTHING` perde a nova em silêncio. "Refazer" (§7.9 do desenho) passa
  a ser atualizar a versão na mesma linha (§6, D5).

## 1. Onde o laço vive, trava, ritmo e acordar

Arquivos novos (camadas como em `modules/execution/`):

```
backend/app/modules/pedidos/
  domain/materializar.py     PURO: instantes devidos + janela + coalescência → lista de (instante, estado, origem, motivo)
  domain/sobreposicao.py     PURO: (política, autonomia, abertas, devidas) → o que despachar, o que pular
  domain/fechamento.py       PURO: (status da execução, objetivos) → estado final da ocorrência + motivo
  application/repositorio.py SQL das três tabelas (INSERT ON CONFLICT, CAS por estado e por versão)
  application/laco.py        LacoDePedidos: uma_volta(), materializar(), despachar(), fechar(), varrer()
  application/acoes.py       pausar / retomar(modo) / cancelar / editar (chamadas pela API do 28.9)
```

Ligação no `AppState` (INFERRED quanto às linhas exatas, que mudam com o merge do 28.1):

- `travas.py` ganha `PEDIDOS = "pedidos"` e ele entra em `TRAVAS_DOS_LACOS` (integ `travas.py:45-50`), para o
  `_laco_das_travas` renovar o mandato a cada 20 s (integ `state.py:2465-2470`).
- `AppState.start`, no ramo `cfg.roda_scheduler`, depois de `scheduler.start()`, de
  `runs.resume_planning_after_restart()` e de `soltar_da_queda()` (integ `state.py:2236-2249`): cria a tarefa
  `pedidos` com o laço. Só o papel com scheduler; `ROLE=api` nunca materializa.
- **Só o líder materializa, despacha e fecha.** Cada volta começa com `token = self._lider(PEDIDOS)` (integ
  `state.py:2472-2485`); `None` = volta pulada sem erro. Diferença em relação aos outros laços: o seguidor que vira
  líder age já na volta seguinte (15 s), não em minutos.
- As escritas de estado do laço (inserção das ocorrências + avanço do cursor e de `proxima_em`; o CAS
  `devida → despachada`) rodam em `lideranca.cercada(PEDIDOS, token)` (integ `travas.py:137-142`), com
  `materializada_token = token`. A correção continua vindo das chaves (§7.3 do desenho); a cerca só barra o líder
  velho que acordou de uma pausa. `RunService.create` fica FORA da cerca (abre a própria transação; `db.tx` aninha
  por profundidade, `db.py:333-337`, mas a criação não deve segurar a trava de escrita do SQLite durante a
  resolução de alvos).
- Ritmo: `pedidos.tick_s` (padrão 15 s) num `wait_for(evento.wait(), timeout=tick_s)`, como o `Scheduler._loop`
  (`scheduler.py:254-267`). `LacoDePedidos.wake()` é chamado por: criação/ativação/edição/retomada/cancelamento
  (28.9), o gancho de fim de execução (§5) e a partida. Chamado de outra thread, usa
  `loop.call_soon_threadsafe(evento.set)` (INFERRED: necessário porque `asyncio.Event` não é seguro entre threads).
- Relógio: `LacoDePedidos(relogio=db.agora)`, atributo trocável como em `Lideranca` (integ `travas.py:65-74`). Nada
  no módulo lê `datetime.now()`.
- Configuração nova em `config.example.yaml` (seção `pedidos:`): `tick_s` (15), `horizonte_s` (3600),
  `prazo_inicio_s` (3600), `lote_max` (500 linhas por gatilho por volta), `posse_s` (120). Sem migração.

Ordem de uma volta (`uma_volta`, síncrona nas partes de banco, com `create` na thread do laço):

1. `token = _lider(PEDIDOS)`; sem token, dorme.
2. `fechar()` — varredura das ocorrências `despachada`/`rodando` (§5). Primeiro, para a sobreposição do passo 4 ver
   o que já acabou.
3. `materializar()` — §2.
4. `despachar()` — §3 e §4.
5. Atualiza `proxima_em` de cada pedido tocado e calcula o próximo acordar = `min(tick_s, proxima_em - agora)`.

Cada passo pega exceção por pedido (um pedido com `spec` quebrada não para os outros) e registra no pedido
(`pausado_motivo` só quando o sistema pausa; o resto vai para evento e log).

## 2. Materialização, janela e coalescência (§7.5)

### 2.1 De onde saem os instantes

| Tipo de gatilho | `spec` (proposta; quem grava é o 28.9) | Instantes |
|---|---|---|
| `agora` | `{}` | um só: o instante da ativação, gravado no `cursor` pela ativação |
| `horario` | `{"local": "2026-10-03T09:00:00"}` | um só, localizado no fuso do pedido por `recorrencia.localizar` (`recorrencia.py:234`), com o mesmo desvio de hora inexistente |
| `recorrencia` | `{"dtstart": "2026-10-02T09:00:00", "rrule": "FREQ=DAILY"}` | `recorrencia.proximas(regra, dtstart, fuso, depois_de=cursor, limite=1000)` (`recorrencia.py:375-389`), paginado: o último devolvido vira o `depois_de` seguinte |
| `evento`, `condicao`, `persona` | — | fora do 28.4 (28.8) |

**Cursor de materialização** = `pedido_gatilhos.cursor` (067:85), texto `formatar_instante` do último instante já
inserido (exclusivo). Nasce na ativação (28.9) com o instante da ativação menos 1 s, para que nada anterior à
ativação vire `perdida`. Avança na MESMA transação cercada que insere as linhas. Se o cursor for perdido (NULL), o
laço o reconstrói como `MAX(previsto_para)` das ocorrências do gatilho, ou a ativação se não houver nenhuma: as
chaves impedem duplicata em qualquer caso.

### 2.2 Regras exatas

Entradas: `agora` (relógio do banco, truncado ao segundo), `C` (cursor), `H = horizonte_s`, `J` (janela), `coalescer`.

1. Instantes `I = {i : C < i <= agora + H}`, cortados por `fim_em` (i > `fim_em` não nasce) e pelo `COUNT`/`UNTIL`
   da regra (já aplicados pelo gerador).
2. **Janela `J`** = `pedidos.janela_recuperacao_s` se não for NULL (067:62); senão o padrão por tipo (§7.5):
   `recorrencia` com período nominal (FREQ × INTERVAL) ≥ 24 h → 1800 s; período menor → metade do período (HOURLY
   INTERVAL=n → n × 1800 s); `horario` com autonomia `agir` → 0; `horario` nas outras e `agora` → 1800 s
   (INFERRED: o §7.5 não diz o padrão de `horario` sem efeito). Com BYHOUR/BYMINUTE o período nominal é o da FREQ;
   a lacuna real pode ser menor (aceito; a pessoa pode fixar `janela_recuperacao_s`).
3. Para cada `i` em `I`, com `atraso = agora − i`:
   - `i > agora` → nasce `prevista` (origem `agenda`).
   - `atraso > J` → nasce `perdida`, motivo `fora da janela de recuperação: atraso de {atraso}s > {J}s`.
   - senão é **candidata**.
4. **Coalescência** (`coalescer = 1`, 067:63, padrão): entre as candidatas, só a de maior `i` nasce `devida`; as
   outras nascem `pulada`, motivo `coalescida na de {i_max}`. Com `coalescer = 0`, todas as candidatas nascem
   `devida` (a sobreposição, §3, decide o despacho).
5. **Origem**: `agenda` se `atraso <= 2 × tick_s`; senão `recuperacao` (INFERRED: o limiar separa o atraso normal
   do laço do atraso de queda). A chave é a mesma nos dois casos (`chave.py`, docstring 10–12).
6. As `prevista` que ficaram para trás numa volta (`previsto_para <= agora`) passam pelas mesmas regras 3–4 como
   transição: `prevista → devida | perdida | pulada` (`estados.py:110`). Coalescência vale entre linhas existentes e
   linhas novas do mesmo gatilho na mesma volta.
7. Inserção: `INSERT … ON CONFLICT (chave) DO NOTHING` (067:97) com `estado` inicial em `OCORRENCIA_NASCE_EM`
   (`estados.py:105`), `pedido_versao = pedidos.versao`, `materializada_token = token`, `motivo` quando exigido.
   O `ON CONFLICT (chave)` não cobre o `UNIQUE (pedido_id, gatilho_id, previsto_para)`; como a chave é função
   desses três campos para origens com gatilho (`chave.py:63-80`), os dois conflitam juntos. Usar
   `ON CONFLICT DO NOTHING` sem alvo para cobrir ambos (INFERRED: aceito por SQLite e PostgreSQL).
8. Lote: no máximo `lote_max` linhas por gatilho por volta; o cursor avança só até o último inserido e a volta
   seguinte continua. Uma queda de 41 dias num pedido de hora em hora são ~1000 `perdida` — registradas, nunca
   somem (§7.5); o lote só evita uma transação longa.
9. `proxima_em` = menor entre a `prevista` mais cedo e o próximo instante depois do cursor, em `formatar_instante`;
   gravado com CAS `WHERE id=? AND versao=? AND estado='ativo'` (§7.2 do desenho).

`domain/materializar.py` recebe a lista de instantes já gerada e devolve as linhas: puro, testável sem banco.

### 2.3 Encerramento por esgotamento

Quando nenhum gatilho ativo tem próximo instante (`proxima` devolve `None`, `fim_em` passou, ou `max_ocorrencias`
atingido) e não há ocorrência aberta (`prevista`, `devida`, `despachada`, `rodando`): `ativo → encerrado`, ator
`sistema`, motivo `prazo` (fim_em ou UNTIL) ou `contagem` (COUNT ou `max_ocorrencias`), via
`transicionar_pedido` (`estados.py:143-160`) e CAS `WHERE estado='ativo' AND versao=?`. Com o pedido em
`aguardando_pessoa`, nada encerra (a tabela não tem a aresta, `estados.py:20-23`); o encerramento acontece na volta
seguinte à resposta da pessoa. `max_ocorrencias`: ver D4.

## 3. Sobreposição (§7.4)

Ocorrência **aberta** = `despachada` ou `rodando` do mesmo pedido. Vocabulário da 067 (61): `pular`,
`guardar_uma`, `permitir_todas` (`estados.py:38`).

| Política | Com aberta | Sem aberta |
|---|---|---|
| `pular` | `devida → pulada`, motivo `a anterior ainda roda ({ocorrencia_id})` | despacha |
| `guardar_uma` | a `devida` mais antiga fica `devida` (guardada); toda outra `devida` → `pulada`, motivo `já há uma guardada ({id})` (INFERRED: semântica do BufferOne do Temporal, que descarta as que chegam com uma já guardada) | despacha a guardada |
| `permitir_todas` | despacha | despacha |

- Ordem: por `previsto_para` crescente, depois `chave`.
- Teto por autonomia (§7.4), conferido no despacho além da criação (28.9): `agir` só `pular`; `preparar` aceita
  `pular`/`guardar_uma`; `observar` aceita as três. Pedido incoerente (editado no banco, por exemplo) é tratado como
  `pular` e registrado em log.
- A guardada não sofre a janela de novo (ela já passou na materialização); o atraso dela fica limitado por
  `prazo_inicio_s` depois do despacho (§5.3).
- Uma tentativa nova da mesma ocorrência (`falhou → devida`, 28.5) não é sobreposição consigo mesma.
- Pedido `pausado`: as `devida` e `prevista` dele viram `pulada`, motivo `pedido pausado`; as abertas terminam.
- `domain/sobreposicao.py` é puro: recebe (política, autonomia, ids abertos, devidas ordenadas) e devolve
  `(despachar: list, pular: list[(id, motivo)])`.

## 4. Despacho idempotente por `RunService.create`

Para cada `devida` escolhida:

1. **Reserva** (eficiência, não correção): `UPDATE pedido_ocorrencias SET dono=?, prazo_posse=? WHERE id=? AND
   estado='devida' AND (dono IS NULL OR dono=? OR prazo_posse < ?)` com `to_iso(agora)` e `posse_s`. Sem linha, outro
   laço tem a posse: pula.
2. **Tentativa** `n = tentativa + 1` (067:103). A coluna só é escrita no CAS do passo 5. Queda antes dele → a volta
   seguinte calcula o MESMO `n` e a MESMA chave.
3. **Procurar antes de criar** (A2): `SELECT id, status FROM runs WHERE idempotency_key = chave_da_tentativa(chave, n)`.
   Achou → vai ao passo 5 com esse `run_id`.
4. **Criar**, na thread do laço (A3):
   `runs.create(RunCreate(command=pedido.objetivo, targets=…, device_policy=…, mode="execute",
   idempotency_key=chave_da_tentativa(chave, n)), origem=(pedido_id, ocorrencia_id))`.
   - `targets` vem de `pedidos.alvos` (foto no formato de `runs.targets`, 067:53): cada alvo
     `{instance_id, profile_id, app_id, …}` (`modules/execution/application/alvos.py:45-47`) vira um `RunTarget`
     (`profile_id`, `instance_ids=[instance_id]`, `app_id`); alvo sem persona vai em `instance_ids`. Como os alvos
     chegam explícitos, nenhum tem origem `texto` e a recusa `alvos_nao_confirmados` (`service.py:160-169`) não
     dispara (INFERRED quanto ao formato exato que o 28.9 grava).
   - `origem` é parâmetro novo, só de chamada interna, de `RunService.create` → `repo.create_run`, gravado no
     MESMO `INSERT` de `runs` (`repository.py:159-163`): `pedido_id`, `ocorrencia_id`. Atômico com a criação; a API
     pública não o expõe (D3).
   - `mode="execute"`: o planejamento termina chamando `start` sozinho (`service.py`, fim de `_plan`).
   - Execução nascida em `needs_input` por pergunta de alvo (`service.py:407-416`) conta como despachada.
   - **`RunError`** (pré-voo, `ai_not_configured`, app incompatível): a ocorrência fica `devida`, solta a reserva
     (`dono=NULL`) e grava o último motivo em `resumo`; a volta seguinte tenta de novo. Passou de
     `previsto_para + J` (J = a janela do §2.2, mínimo `tick_s`) → `devida → perdida`, motivo
     `não foi possível criar a execução: {code}: {mensagem}`. Erro inesperado (não `RunError`): mesmo tratamento,
     com log de exceção.
5. **Marcar**, cercado: `UPDATE pedido_ocorrencias SET estado='despachada', run_id=?, tentativa=?, iniciada_em=NULL,
   dono=NULL, prazo_posse=NULL WHERE id=? AND estado='devida'` depois de `transicionar_ocorrencia('devida',
   'despachada')`. Zero linhas = outro laço marcou antes; nada a fazer (a execução é a mesma, pela chave).

O que acontece em cada queda:

| Queda entre | Na volta seguinte |
|---|---|
| reserva e criação | posse vence (`posse_s`) ou é do mesmo `OWNER_ID`; mesmo `n`, mesma chave; não há execução → cria |
| criação e marca | o passo 3 acha a execução pela chave → marca `despachada` com ela, sem chamar `create` (que poderia recusar, A2) |
| marca e planejamento | `runs.resume_planning_after_restart()` (`service.py:828-842`) replaneja; nada do pedido muda |

Dois líderes ao mesmo tempo (pausa longa): os dois chegam ao passo 3/4 com a mesma chave; `create_run` devolve a
mesma execução ao segundo (`repository.py:165-168`); o CAS do passo 5 deixa um só marcar; o líder velho é recusado
pela cerca antes disso.

## 5. Fechamento: gancho e varredura

### 5.1 O que existe

O único gancho de fim de execução é `Scheduler.on_run_settled` (`scheduler.py:191`), que já está ocupado pelo
`AppState._execucao_assentada` (integ `state.py:361`, `2441-2450`) e só dispara no fim de worker (A1).

### 5.2 Desenho

- **Uma função só**, `fechar_pela_execucao(run_id)`, idempotente por CAS. Chamada (a) pelo gancho e (b) pela
  varredura.
- **Gancho (caminho rápido):** `_execucao_assentada` passa a chamar também `pedidos.ao_assentar(run_id)`, que lê
  `runs.pedido_id` e, se houver, chama `wake()` (não fecha ali dentro: o gancho roda no `finally` do worker e não
  deve escrever no banco do pedido fora da trava). Sem mudança no `Scheduler`.
- **Varredura (caminho durável e caminho normal de A1):** a cada volta, `SELECT o.*, r.status FROM
  pedido_ocorrencias o LEFT JOIN runs r ON r.id = o.run_id WHERE o.estado IN ('despachada','rodando')` (índice
  `(estado, previsto_para)`, 067:117). Latência máxima `tick_s` para o que o gancho não vê. Ligar um aviso em
  `repo.set_run_status` (`repository.py:231-243`, ponto único da troca de status) reduziria essa latência; não é
  proposto (D6).

### 5.3 Regras (domain/fechamento.py, puro)

| Execução | Ocorrência | Motivo |
|---|---|---|
| `planning`, `planned`, `needs_input` | fica `despachada` | — (needs_input → `aguardando_pessoa` do pedido é do 28.5) |
| `running`, `paused`, `cancelling` | `despachada → rodando`, `iniciada_em = runs.started_at` | — |
| `completed` | `→ concluida` | — |
| `completed_with_issues` com algum objetivo `uncertain` | `→ incerta` | `objetivo incerto: {ids}` |
| `completed_with_issues` sem `uncertain` | `→ falhou` | `parcial: {ok} de {total}` |
| `failed` | `→ falhou` | `status_detail` da execução |
| `cancelled` | `→ cancelada`, ou `→ perdida` se o cancelamento foi o do prazo de início (abaixo) | `pedido cancelado` / `execução cancelada` / `não começou em {prazo_inicio_s}s: {wait_reason}` |
| linha de `runs` ausente (purga) | `→ falhou` | `execução {id} não existe mais` |

Estados de `RunStatus`/`ObjectiveStatus`: `models.py:61-89`. `despachada → concluida/falhou/incerta` direto é
permitido (`estados.py:114-118`) para a varredura que nunca viu `rodando`. Todo fechamento grava `terminada_em`; o
custo (`custo_usd`) é do 28.6. Retentativa (`falhou → devida`) é do 28.5: no 28.4 `falhou` é fim.

**Prazo de início (§7.5, "atraso na fila"):** `despachada` com execução em `planning`/`planned`/`running` cujos
objetivos estão todos `pending`, há mais de `prazo_inicio_s` desde o despacho → `RunService.cancel(run_id)`
(`service.py:1159-1192`) e grava em `resumo` a intenção (`prazo_inicio`). O estado final só é decidido quando a
execução assenta: `cancelled` sem objetivo iniciado → `perdida`; se algum objetivo começou no meio (corrida com o
`_tick`), vale a tabela acima (`rodando → cancelada` ou `incerta`). Assim uma corrida nunca vira `perdida` com
efeito possível.

## 6. Pausa, cancelamento, edição e retomada (§7.9)

Funções de `application/acoes.py`, chamadas pela API (28.9); cada uma faz o CAS do pedido com
`transicionar_pedido` e as escritas das ocorrências na mesma transação, e chama `wake()`. A varredura completa o que
uma queda deixar no meio (por exemplo, pedido `cancelado` com ocorrência ainda `devida` → `cancelada`).

- **Pausar** (`ativo → pausado`, motivo obrigatório): `prevista`/`devida` → `pulada` (`pedido pausado`); abertas
  terminam; o laço deixa de materializar o pedido.
- **Retomar** (`pausado → ativo`, só pessoa): modo `daqui` (padrão) materializa os instantes da pausa (cursor até
  agora) como `pulada` (`pausado: retomado daqui`), pela mesma função pura do §2 com `J = 0` e estado `pulada`; modo
  `recuperar` só avança `proxima_em` e deixa o laço aplicar janela e coalescência normalmente.
- **Cancelar** (pessoa): `prevista`/`devida` → `cancelada`; para cada aberta, `RunService.cancel(run_id, por=quem)`;
  ela fecha pela varredura como `cancelada` (ou `incerta`). Nenhuma ocorrência nasce depois do CAS do estado: o laço
  só materializa `estado='ativo'`.
- **Editar** (`versao + 1`, A6): `UPDATE pedido_ocorrencias SET pedido_versao=? WHERE pedido_id=? AND estado IN
  ('prevista','devida')` (não é transição de estado); despachadas terminam na versão em que nasceram. Mudar a
  recorrência ou o horário NÃO edita o `spec`: desativa o gatilho (`ativo = 0`, as `prevista`/`devida` dele →
  `cancelada`, motivo `edição`) e cria um gatilho novo, com id novo — chaves novas, sem colisão (D5).

### Retomada depois de reinício

Nada de estado em memória é fonte de verdade. Na partida:

1. `soltar_da_queda()` já solta a trava `pedidos` do mesmo `OWNER_ID` (integ `travas.py:158-170`); a reserva de
   ocorrência do mesmo dono é readotada pela regra `dono=?` do §4 passo 1.
2. Primeira volta imediata (sem esperar `tick_s`): fecha (§5), materializa desde o cursor com janela e coalescência
   (a queda vira `perdida`/`pulada` registrada ou uma `devida` de `recuperacao`), despacha procurando a execução pela
   chave antes de criar (§4).
3. Execuções em `planning` são retomadas pelo `resume_planning_after_restart` (`service.py:828-842`), que roda antes
   do laço.

## 7. Migração

**O 28.4 não precisa de migração.** Tudo cabe na 067: cursor em `pedido_gatilhos.cursor`, reserva em
`dono`/`prazo_posse`, cerca em `materializada_token`, ligação em `runs.pedido_id`/`ocorrencia_id`; os índices
`ix_pedido_ocorrencias_estado` e `ix_pedidos_estado_proxima` servem as consultas do laço; `runs.idempotency_key` já é
UNIQUE. `prazo_inicio_s`, `horizonte_s` e `lote_max` ficam em configuração.

Se o dono quiser os prazos por pedido (D2), a 068 (reservada à Fase 28) teria só:

```sql
ALTER TABLE pedidos ADD COLUMN prazo_inicio_s INTEGER CHECK (prazo_inicio_s IS NULL OR prazo_inicio_s > 0);
CREATE INDEX ix_runs_ocorrencia ON runs(ocorrencia_id);
```

(o índice só se a tela do 28.9 listar as tentativas de uma ocorrência por `runs.ocorrencia_id`). Nenhum arquivo de
migração é criado por este desenho.

## 8. Testes e divisão em commits

Prova esperada do 28.4: `simulated` (aceite do §13: reinício no meio não duplica nem perde; duas instâncias do laço
geram uma ocorrência; perdida fora da janela registrada). `real` só no 28.12.

Relógio: o laço recebe `relogio` injetado (o `db.agora` em produção), como a `Lideranca`. O
`tests/relogio_virtual.py` existente pula o `asyncio.sleep` das ferramentas e não é relógio de parede
(`relogio_virtual.py:1-16`); os testes do laço usam um relógio de parede falso próprio (um `datetime` avançado à
mão), sem `sleep`, chamando `uma_volta()` diretamente.

| Arquivo | Tipo | O que prova |
|---|---|---|
| `tests/test_pedidos_materializar.py` | puro | janela por tipo; `perdida` com motivo; coalescer ligado/desligado; origem agenda × recuperação; corte por `fim_em`/COUNT; horizonte gera `prevista`; lote |
| `tests/test_pedidos_sobreposicao.py` | puro | as três políticas; teto por autonomia; ordem; pausa |
| `tests/test_pedidos_fechamento.py` | puro | tabela do §5.3, inclusive `despachada → concluida` direto e o prazo de início com corrida |
| `tests/test_pedidos_laco.py` | integração SQLite, `RunService` com provedor simulado | volta completa; queda simulada entre criar e marcar (exceção injetada) não duplica; `RunError` mantém `devida` e vira `perdida` após a janela; dois `LacoDePedidos` no mesmo banco → uma ocorrência e uma execução; reinício (laço novo, mesmo banco) depois de 3 h de relógio → `perdida`/`pulada` registradas e uma `recuperacao`; fechamento sem gancho (cancelada antes de iniciar, falha no planejamento) |
| o mesmo, com `TEST_DATABASE_URL` | PostgreSQL | `ON CONFLICT DO NOTHING` sem alvo e a cerca com `FOR UPDATE` |

Regras da suíte: harness em `base_console_port: 5640`; durante o trabalho só os arquivos acima; a suíte inteira no
fim, em segundo plano.

Divisão (um PR, depois do merge de 28.1 e 28.2 na main):

1. `feat(pedidos): domínio puro de materialização, sobreposição e fechamento (28.4)` — três módulos + três testes.
2. `feat(fila): origem do pedido na criação da execução (28.4)` — parâmetro interno em `RunService.create` e
   `create_run`, teste de que a API pública não aceita o campo.
3. `feat(pedidos): laço com trava de líder, despacho e varredura (28.4)` — `repositorio.py`, `laco.py`, `acoes.py`,
   ligação no `AppState`, `PEDIDOS` em `TRAVAS_DOS_LACOS`, seção `pedidos:` no `config.example.yaml`, teste de
   integração.
4. `docs(pedidos): …` — `pedidos-persistentes.md` §7.2/§7.9 com os desvios (A6, gancho só acorda),
   `dominios/execution.md`, CHANGELOG.

## 9. Riscos e decisões

Riscos:

- **R1. Custo pago por recorrência.** Cada ocorrência despachada chama o planejador. Orçamento é do 28.6; até lá, um
  pedido de hora em hora gasta sem teto. Mitigação no 28.4: ativação de pedido atrás de `pedidos.enabled` (padrão
  desligado) até o 28.6 entrar (INFERRED como necessidade; ver D1).
- **R2. Recálculo desde `DTSTART`.** `proximas` percorre desde o início a cada chamada (`recorrencia.py:358-372`):
  um HOURLY de um ano são ~8800 iterações por volta. O laço só consulta pedidos com `proxima_em <= agora + H`
  (índice 067:78); se medir caro, guardar no cursor o último nominal e recomeçar dali (mudança no 28.3).
- **R3. Despacho na thread do laço** (A3): `RunService.create` é síncrona e resolve alvos e pré-voo; muitos pedidos
  devidos ao mesmo tempo seguram o laço de eventos. Limite de `N` criações por volta (`lote_max` também aqui).
- **R4. Formato de instante** (A5): comparação errada é silenciosa; o teste puro precisa de um caso no mesmo
  segundo.
- **R5. `needs_input` e `incerta` sem `aguardando_pessoa` até o 28.5**: com `pular`, a ocorrência aberta bloqueia as
  seguintes (não há efeito duplicado); com `permitir_todas` (`observar`) seguem nascendo — aceitável só para leitura.

Decisões (todas fechadas em 02/10/2026; D1 e D4 confirmadas pelo dono, as demais tomadas pelo coordenador como o
desenho recomendou):

- **Decidido (02/10, dono) D1:** o 28.4 entra DESLIGADO (`pedidos.enabled: false`, padrão em `PedidosCfg`) até o
  orçamento do 28.6 estar na main; desligado, o laço nem sobe e este backend não toma nem renova a trava `pedidos`. A
  prova do 28.12 liga.
- **Decidido (02/10, coordenador) D2:** `prazo_inicio_s` e `horizonte_s` globais, no bloco `pedidos:` da configuração
  (com `tick_s`, `janela_padrao_s`, `lote_max`, `posse_s`). Sem migração; por pedido seria a 068 (reservada).
- **Decidido (02/10, coordenador) D3:** a ligação execução→pedido vai por parâmetro INTERNO `origem=(pedido_id,
  ocorrencia_id)` de `RunService.create` e `Repository.create_run`, gravado no mesmo `INSERT` (atômico). `RunCreate`
  segue com `extra="forbid"`: a API pública não ganhou campo (A4).
- **Decidido (02/10, dono) D4:** `max_ocorrencias` conta só as ocorrências que viraram execução (`run_id` não nulo);
  `pulada` e `perdida` sem execução não gastaram nada.
- **Decidido (02/10, coordenador) D5:** editar a recorrência ou o horário cria GATILHO NOVO (id novo) e desativa o
  velho, cujas `prevista`/`devida` viram `cancelada` (motivo `edição`); as demais ocorrências abertas mudam de versão
  NO LUGAR (mesma linha), nunca cancelar e recriar (A6). Texto do §7.9 de `pedidos-persistentes.md` ajustado; ADR-066.
- **Decidido (02/10, coordenador) D6:** aceita-se até `tick_s` (15 s) de atraso no fechamento dos casos sem worker
  (falha no planejamento, cancelamento antes de iniciar); a varredura fecha. Sem aviso em `repo.set_run_status`.
- **Decidido (02/10, coordenador) D7:** janela padrão de 1800 s para `horario` sem efeito e para `agora`,
  configurável em `pedidos.janela_padrao_s`; `horario` com autonomia `agir` fica em 0.

## 10. O que foi implementado e onde (02/10/2026, branch `feat/28-4-laco`)

Prova `simulated` (`backend/tests/test_pedidos_*.py`); `real` só no 28.12.

| Peça | Onde |
|---|---|
| Janela, coalescência, origem, segundo cheio (A5) | `modules/pedidos/domain/materializar.py` (`materializar`, `janela_padrao_s`) |
| Instantes devidos de `agora`/`horario`/`recorrencia` | `modules/pedidos/domain/gatilhos.py` |
| Sobreposição e teto por autonomia | `modules/pedidos/domain/sobreposicao.py` (`decidir`) |
| Tabela de fechamento e prazo de início | `modules/pedidos/domain/fechamento.py` (`fechar`, `prazo_de_inicio_vencido`) |
| SQL das três tabelas (CAS por estado e versão) | `modules/pedidos/application/repositorio.py` |
| O laço (`uma_volta`: fechar, materializar, despachar, agendar) | `modules/pedidos/application/laco.py` |
| Pausar, retomar, cancelar, editar | `modules/pedidos/application/acoes.py` |
| Parâmetro interno `origem` (D3) | `taskqueue/service.py` (`create`, `_criar_com_perguntas`), `taskqueue/repository.py` (`create_run`) |
| Trava `PEDIDOS` e ligação | `taskqueue/travas.py`, `state.py` (`self.pedidos`, tarefa `pedidos`, `_execucao_assentada`, `_manter_travas`) |
| Configuração | `config.py::PedidosCfg`, bloco `pedidos:` de `config/config.example.yaml` |

Desvios e escolhas do código, onde o desenho dizia INFERRED:

- O instante do despacho para o prazo de início é `runs.created_at` (a execução nasce no mesmo gesto; sem coluna nova).
- `uma_volta` é síncrona e roda direto no laço de eventos (A3); só a espera (`wait_for`) é assíncrona.
- Pedido com `spec` quebrada ou sem alvos não derruba a volta: o gatilho é registrado e ignorado, e a criação que falha
  deixa a ocorrência `devida` com o motivo em `resumo`, até a janela vencer (`perdida`).
- Ainda sem código: `evento`, `condicao` e `persona` (28.8), orçamento (28.6), retentativa e `aguardando_pessoa`
  (28.5), a API das ações (28.9). `acoes.py` não tem rota ainda.
- `test_pedidos_laco.py` cobre A1, A2, A3, A5, A6, dois líderes e a cerca, coalescência, perdidas, sobreposição,
  `max_ocorrencias`, reinício, pausa/retomada/cancelamento e o laço desligado; A4 está em `test_pedidos_origem.py`.
  PostgreSQL (`TEST_DATABASE_URL`): `not_run`.
