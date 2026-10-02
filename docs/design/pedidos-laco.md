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
| SQL das três tabelas (CAS por estado e versão) | `modules/pedidos/infrastructure/repositorio.py` |
| O laço (`uma_volta`: fechar, materializar, despachar, agendar) | `modules/pedidos/infrastructure/laco.py` |
| Pausar, retomar, cancelar, editar | `modules/pedidos/infrastructure/acoes.py` |
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

## 11. 28.6 — o que foi feito (02/10/2026, branch `feat/28-6-orcamento-prioridade`)

Prova `simulated` (`backend/tests/test_pedidos_orcamento.py`, relógio falso, provedor e `ai_calls` à mão); `real` só no
28.12. PostgreSQL (`TEST_DATABASE_URL`): `not_run`. Sem migração (a 067 já trazia as colunas) e sem ADR novo. Com isto a
condição da D1 (o orçamento do 28.6 na `main`) está cumprida; LIGAR o laço (`pedidos.enabled`) continua sendo o gesto do 28.12.

| Peça | Onde |
|---|---|
| Regras puras: estimativa (mediana das últimas 5), motivo de esgotamento, quantas cabem, teto da execução | `modules/pedidos/domain/orcamento.py` |
| Custo gravado no fechamento, SOMADO no mesmo `UPDATE` do CAS de estado | `repositorio.py::mover(custo_usd=…)`, `laco.py::_fechar_uma`, `_custo_da_tentativa` |
| Retenção que não leva `ai_calls` de execução de ocorrência aberta | `state.py::_purgar_demais_tabelas` |
| Orçamento total: pula o que não cabe e encerra com `encerrado_motivo='orcamento'` | `laco.py::_orcamentos`, `_conferir_orcamento`; `repositorio.py::com_orcamento_total`, `custo_total`, `ultimos_custos` |
| Teto por ocorrência NA execução | `taskqueue/repository.py::teto_usd_da_execucao`, `planning/routing.py::_budget` |
| Saldo (ADR-051) adia | `infrastructure/saldo.py::motivo_de_adiamento`, `laco.py::_motivo_de_saldo`, `_adiar`; `PedidosCfg.saldo_minimo_usd` |
| `runs.prioridade` | `taskqueue/repository.py` (`create_run(prioridade=)`, `dispatchable_objectives`), `taskqueue/service.py::create(prioridade=)`, `laco.py::_prioridade` |

Decisões e limites:

- **Custo antes da purga.** `uma_volta` lê o custo (`costs.spent_usd`, a conta do painel de uso e do teto, injetada em
  `AppState` como `custo_da_execucao`) e o soma a `custo_usd` no MESMO `UPDATE` do fechamento terminal: CAS perdido não
  soma, queda não deixa ocorrência fechada sem custo, e as tentativas do 28.5 acumulam. A retenção apaga `ai_calls` por
  `ts` (14 dias); a única janela de perda era uma ocorrência aberta há mais que isso (rara, mas possível com `needs_input`).
  Escolhi o menor conserto: o `DELETE` de `ai_calls` não leva a chamada cuja execução pertence a ocorrência
  `despachada`/`rodando`. Não se grava custo parcial antes do fechamento (somaria em dobro). `runs` não é purgada por
  nenhuma retenção (só `ai_calls`, `events`, evidências): a linha ausente do §5.3 segue sendo defesa.
- **LIMITE CONHECIDO: o excesso máximo de orçamento é o custo de UMA ocorrência aberta, limitado pelo teto da execução**
  (o custo só entra no total quando a ocorrência fecha). Fixado em
  `test_pedidos_orcamento.py::test_limite_conhecido_o_excesso_do_orcamento_e_no_maximo_o_custo_de_uma_ocorrencia_aberta`.
- **O custo em andamento não conta no total até a ocorrência fechar.** O excesso possível é o de uma ocorrência aberta por
  vez (sobreposição `pular`/`guardar_uma`); com `permitir_todas` (só `observar`), `quantas_cabem` limita a volta pela
  estimativa. O que passa disso o teto da execução barra (abaixo).
- **Estimativa** = mediana do `custo_usd` das últimas 5 ocorrências fechadas COM execução e custo > 0; sem histórico, o
  `orcamento_ocorrencia_usd`; sem os dois, 0 (a primeira sai). Restante do total menor que a estimativa (ou <= 0) =
  sem orçamento: o que ainda não virou execução vira `pulada` (`orçamento: …`) e, sem execução aberta, o pedido encerra com
  `orcamento`; com uma aberta, espera ela fechar (o custo real decide). `_orcamentos` roda DEPOIS de materializar e ANTES de
  despachar, para a `devida` nascida na mesma volta também ser barrada. Pedido sem `orcamento_total_usd` nunca entra no
  caminho: comportamento idêntico ao do 28.4.
- **Teto por ocorrência na execução.** `AIRouter._budget` (que já barra `ai_max_usd_per_run`/`per_day` ANTES de gastar)
  pergunta ao repositório o teto do pedido: o menor entre `orcamento_ocorrencia_usd − custo das tentativas anteriores` e
  `orcamento_total_usd − gasto fechado do pedido`. Estourado, `AIError(kind="budget")`, como o teto global. Não há campo
  novo em `runs` nem mudança no executor; a leitura é uma consulta pela chave primária da execução, e a execução que não é
  de pedido recebe `None` e não muda. Limite: a checagem é por chamada, então a chamada em voo pode passar do teto pelo
  custo dela.
- **Saldo adia, não falha, não perde.** Uma leitura por volta (`saldos.estado`, o mesmo serviço de `GET /api/ai/balances`; sem
  chamada paga), só quando há `devida`. Adia se uma conta que paga alguma função de IA está `bloqueia` (bloqueio do dono ou
  crédito esgotado) ou, com `pedidos.saldo_minimo_usd > 0`, com saldo estimado abaixo do mínimo; conta sem leitura não adia.
  A ocorrência fica `devida`, sem reserva e sem tentativa gasta, com `resumo = "adiada: …"` (regravado só quando muda).
  **Segue o §10 de `pedidos-persistentes.md`** ("fora da janela, `perdida`"), por decisão do coordenador (02/10, revisando a
  primeira versão, que deixava a adiada `devida` para sempre): dentro de `previsto_para + max(janela, tick_s)` a ocorrência
  segue `devida` ("adiada: ..."); passou disso, `_adiar` a leva a `perdida` com o motivo `adiada por saldo além da janela: ...`
  (`_limite_da_janela`, o mesmo cálculo de `_criacao_falhou`). O pedido segue vivo, sem execução criada. Teste:
  `test_pedidos_orcamento.py::test_saldo_baixo_alem_da_janela_vira_perdida_com_o_motivo_do_saldo`.
- **Prioridade.** `dispatchable_objectives` ordena `r.prioridade DESC, r.created_at, o.instance_id`: prioridade 0 (todo o
  legado) mantém a ordem de antes. `RunService.create(prioridade=)` e `create_run(prioridade=)` são parâmetros INTERNOS,
  como `origem`; `RunCreate` segue com `extra="forbid"`. A 067 não deu campo de prioridade ao pedido: o laço grava
  `PRIORIDADE_PADRAO = 0` por `_prioridade(p)`, a costura para quando o pedido ganhar o campo (o §10 do desenho quer a
  interativa à frente da de fundo, o que exigirá valor de fundo abaixo de 0 ou interativa acima de 0 e é decisão do 28.9).


## 12. 28.7 — o que foi feito (02/10/2026, branch `feat/28-7-memoria-relatorio`)

Prova `simulated` (`backend/tests/test_pedidos_memoria.py`, `test_pedidos_relatorio.py`); `real` só no 28.12. Migração
**070** `pedidos_memoria` (a 069 é de outra frente; ver `docs/banco.md`).

| Peça | Onde |
|---|---|
| Tabelas `pedido_memoria`, `pedido_observacoes`, `pedido_relatorios` | `backend/migrations/070_pedidos_memoria.sql` |
| Memória versionada (valor igual não sobe a versão, segredo recusado, pendência resolvível, `compactar` sem IA) | `modules/pedidos/domain/memoria.py` |
| Como uma leitura vira observação (`observado` / `incerto` / `ausente`, recusa de credencial, teto) | `modules/pedidos/domain/observacao.py` |
| Relatório determinístico: observado, conclusão, não coberto | `modules/pedidos/domain/relatorio.py` (`montar`, `serializar`, `sha256_de`) |
| Ponto de extensão do resumo por IA (`ResumidorDeRelatorio`, `SemResumo`) | `modules/pedidos/domain/resumo.py` |
| SQL das três tabelas | `modules/pedidos/infrastructure/repositorio_memoria.py` |
| Serviço: observações do fechamento, memória, `preparar`/`gravar`/`gerar` o relatório | `modules/pedidos/infrastructure/relatorios.py` |
| Ligação ao laço e ao cancelamento | `laco.py` (`_fechar_uma`, `_observar`, `_agendar_pedido`, `_relatorio_final`), `acoes.py` (`cancelar`) |
| Configuração | `config.py::PedidosCfg` (`resumo_ia: false`, `resumo_ia_teto_usd`), bloco `pedidos:` do exemplo |

**Observação no fechamento.** Ao fechar uma ocorrência (`_fechar_uma`), o laço lê as saídas da execução (`step_outputs`, 056, o
"valor lido entre etapas" do 12.3) ANTES de a purga apagá-la e grava as observações na MESMA transação cercada do `mover`: ou a
ocorrência fecha com as observações dela, ou nada muda e a varredura repete (`UNIQUE (ocorrencia_id, alvo, nome)` + `ON CONFLICT
DO NOTHING`). Valor lido numa ocorrência `concluida` é `observado`; em qualquer outro fim (`falhou`, `incerta`, `cancelada`…) é
`incerto`; sem saída estruturada (ou execução já purgada) grava UMA observação `resultado` com valor ausente e o motivo do
fechamento em `trecho`. O valor com formato de credencial ou de código de verificação (ADR-009) é recusado e vira `ausente`. Quando
o valor é comprovado, a memória guarda `fonte:<nome>[:<alvo>]` com os 16 primeiros caracteres do `sha256` (a base do espaçamento
adaptativo do §8.2; o código que espaça é do 28.5/28.6). Se ler a saída falhar, a ocorrência fecha sem observação e o relatório a
lista como `sem_observacao`.

**Relatório.** Três blocos que não se misturam. *Observado*: só valor com fonte e instante de ocorrência `concluida` E observação
gravada `observado` (o domínio rebaixa o resto a incerto, mesmo que a linha diga o contrário). *Conclusão*: contagem da amostra,
último valor e variação entre as duas últimas observações comprovadas, sempre com o alcance ("na amostra coletada"); sem observação
a conclusão é VAZIA (`sem_conclusao`), nunca "nada mudou", e só é `sustentada` quando não há nenhum item em "não coberto".
*Não coberto*: ocorrências perdidas, puladas, incertas, que falharam, canceladas e em aberto, valores incertos, ocorrência
concluída sem valor ou sem observação, lacunas (sequência de ocorrências sem observação comprovada), pendências abertas da
memória e cada critério de sucesso (os critérios são texto livre no contrato 28.9; nenhum é verificável em estrutura nesta
versão, então todos ficam como `criterio_nao_avaliado`). Mesmas entradas, mesmo `conteudo`, byte a byte (listas ordenadas,
sem relógio; o instante de geração mora em `pedido_relatorios.gerado_em`); o `sha256` do conteúdo vai na linha. Tetos: 500 itens
em "observado" e 500 em "não coberto", com um item que diz quantos ficaram de fora.

**Quando sai.** Sob demanda (`ServicoDeRelatorios.gerar`, `gatilho = sob_demanda`, sequência crescente; a rota é do 28.9); no
encerramento do laço (`_agendar_pedido`, atômico com a mudança de estado, `gatilho = encerramento`); e no cancelamento
(`AcoesDePedidos.cancelar`, logo depois do commit do cancelamento, de modo que a execução ainda em curso aparece como em aberto).
O relatório de encerramento é UM por pedido (índice único parcial). Falha ao montá-lo nunca impede o encerramento ou o
cancelamento: o relatório sai depois, sob demanda. O gatilho `periodo` (diário, semanal) existe no vocabulário mas ainda não tem
agendador: é do 28.6/28.9.

**Resumo por IA.** Só o ponto de extensão: `ResumidorDeRelatorio.resumir(relatorio, teto_usd)`, `SemResumo` por padrão e
`pedidos.resumo_ia: false`. Mesmo ligado, sem um resumidor injetado em `LacoDePedidos(resumidor=...)` nada é chamado; com ele, o
texto vai em `resumo_texto`/`resumo_por`/`custo_usd` ao lado do conteúdo, nunca no lugar nem na "conclusão", e falha do resumidor
não derruba o relatório. Nenhuma chamada paga existe nesta entrega.

Não feito (fora do 28.7): rotas `GET /api/pedidos/{id}/relatorios` e `/observacoes` e os campos `memoria`, `relatorios_recentes`,
`observacoes_recentes` do `PedidoView` (28.9; os repositórios `relatorios` e `observacoes` já paginam); leitura da memória no plano
da ocorrência (`memoria_para_o_plano` está pronto, o 28.5 o usa); espaçamento adaptativo do §8.2 (usa o `sha256`); agendador do
relatório por período; aviso `relatorio_pronto` (28.11).

## 13. 28.5 — o que foi feito (02/10/2026, branch `feat/28-5-tentativas-efeito`)

Prova `simulated` (`backend/tests/test_pedidos_tentativas.py` para o domínio puro, `test_pedidos_retentativa.py` para o laço em
SQLite, relógio falso e `RunService` de verdade com o planejamento desligado); `real` só no 28.12. PostgreSQL
(`TEST_DATABASE_URL`): `not_run`. Sem migração e sem ADR. Na mesma entrega, os dois acréscimos do coordenador ao 28.6 (§11):
a adiada por saldo que vira `perdida` e o limite conhecido do orçamento.

| Peça | Onde |
|---|---|
| Decisão (repetir, `incerta`, definitiva), atraso exponencial com teto, falhas seguidas, `AvisoDTO` | `modules/pedidos/domain/tentativas.py` |
| Fechamento com a decisão; `falhou → devida` na mesma linha; `incerta` → pedido `aguardando_pessoa`; pausa; avisos | `laco.py` (`_fechar_uma`, `_decidir_tentativa`, `_retentar`, `_efeitos_no_pedido`, `_emitir`) |
| `retentar` (CAS + custo + instante), `efeito_possivel`, `desfechos_recentes` | `repositorio.py` |
| Espera da nova tentativa no despacho; janela contada do atraso | `laco.py` (`_em_espera`, `_limite_da_janela`) |
| `pedido.aviso` no barramento (o 28.11 já o assina) | `state.py` (`avisar=` do `LacoDePedidos`) |
| Configuração | `config.py::PedidosCfg` (`max_tentativas`, `falhas_para_pausar`, `retentativa_base_s`, `retentativa_teto_s`), bloco `pedidos:` do exemplo |

**Regras.**

- **Nova tentativa** só quando a execução `falhou` (status `failed` ou `completed_with_issues` sem objetivo `uncertain`) SEM
  ação com efeito possível: `actions.effect_possible = 1` OU `status IN ('intended','unknown')` em qualquer tentativa de
  qualquer etapa (o que `Scheduler._reconciliar` deixa marcado), e a execução PURGADA conta como efeito possível (o que não se
  sabe não é seguro repetir). `max_tentativas` é o TOTAL de execuções por ocorrência (2 = a primeira e uma repetição); o atraso
  é `min(retentativa_teto_s, retentativa_base_s * 2**(n-1))` (60 s e 900 s por padrão). `incerta` (etapa `uncertain`) nunca repete.
- **Falha com efeito possível é `incerta`, não `falhou`** (decisão do coordenador, 02/10; a primeira versão a deixava `falhou`).
  Se o efeito pode ter acontecido, o mundo está incerto, e um `falhou` definitivo deixaria a PRÓXIMA ocorrência refazê-lo (um
  segundo envio); falha ou incerteza nunca contam como sucesso. O laço sobe o fechamento `falhou` para `incerta` (motivo
  "…; efeito externo possível: só se verifica, sem nova tentativa", `domain/tentativas.decidir` → `ACAO_INCERTA`), qualquer que
  seja o estado do pedido; o pedido `ativo` vai a `aguardando_pessoa` com o aviso `ocorrencia_incerta`, igual à `incerta` que o
  `fechar` já decidia (objetivo `uncertain`). A execução PURGADA também (não se sabe o que ela fez). Só fica `falhou`, e só
  então pode repetir, a execução que COMPROVADAMENTE não produziu efeito: falhou antes de qualquer ação com efeito (nenhuma
  ação, ou só leitura como `observe_screen`, `effect_possible = 0`), ou o driver provou que nada chegou ao aparelho (ação
  registrada `failed` com `effect_possible = 0`, que o executor grava quando `DriverError.effect_possible` é falso). Não há
  outra "verificação que prova ausência" no código: é esta a prova. CONSEQUÊNCIA: `tap`, `long_press`, `drag` e `type_text` são
  `EFFECT_CAPABLE` e gravam `effect_possible = 1` até quando deram certo, inclusive em etapa de navegação (sem `side_effect`);
  logo toda falha DEPOIS de um toque desses vira `incerta`, e a nova tentativa fica para as falhas antes do primeiro toque
  (planejamento, aparelho indisponível, leitura) e para as provadas sem efeito. Restringir o predicado às ações de commit
  (`actions.side_effect = 1`) é uma linha em `RepositorioDePedidos.efeito_possivel`. DECISÃO (coordenador, 02/10): MANTER o
  predicado largo ("na dúvida, `incerta`"); com os pedidos desligados o custo só aparece no 28.12. PARÂMETRO A REVISITAR com
  números no 28.12: a contagem de ocorrências `incerta` cuja falha veio depois de toque SEM `side_effect`. Se for alta,
  restringir às ações de commit passa a ser decisão com dado, não suposição.
  Testes: `test_pedidos_retentativa.py` (efeito → `incerta`, `aguardando_pessoa` e aviso, sem nova tentativa; antes de qualquer
  ação com efeito → repete; driver provou ausência → repete e esgotada vira `falhou`; `unknown`/`intended`; purgada; pedido pausado).
- **A nova tentativa é a MESMA linha.** `retentar` faz `despachada|rodando → devida` num só `UPDATE` (CAS de estado, custo da
  tentativa somado, motivo = a falha), sem coluna nova: `terminada_em` carrega o instante `nao_antes_de` enquanto a ocorrência
  é `devida` com `tentativa > 0` (`marcar_despachada` o zera). `tentativa` e `run_id` ficam; o despacho usa `n = tentativa+1`
  e `chave:t<n>`, e a execução da chave é PROCURADA antes de criar (A2): duas voltas, dois líderes ou queda no meio não criam
  duas tentativas (teste). A aresta `falhou → devida` é validada em duas chamadas de `transicionar_ocorrencia`
  (`rodando → falhou`, `falhou → devida`) embora o banco só grave a segunda.
- **A repetição passa por tudo o que o despacho passa:** sobreposição (não é aberta consigo mesma), orçamento (`_orcamentos`,
  `quantas_cabem`) e saldo (`_adiar`, com a janela contada do `nao_antes_de`, depois `perdida`). Antes de repetir, a decisão
  confere o orçamento: sem verba para outra tentativa (total ou teto da ocorrência), a falha é definitiva e o motivo diz o
  orçamento, em vez de deixá-la `devida` para ser `pulada`. `max_ocorrencias` conta a ocorrência uma vez (já tem `run_id`) e
  não barra a repetição; `_agendar_pedido` também não a pula (`sem_retentativas`).
- **`incerta` → pedido `aguardando_pessoa`** (ator `sistema`, só se o pedido está `ativo`; o CAS `WHERE estado='ativo'` fecha a
  corrida com a pessoa), na MESMA transação do fechamento da ocorrência, mais o aviso `ocorrencia_incerta` (`requer_pessoa`).
  Quem resolve é a pessoa (`aguardando_pessoa → ativo`, ação do 28.9).
- **Falhas seguidas.** Conta OCORRÊNCIAS finais `falhou` (a que falhou e foi repetida só conta se a última tentativa também
  falhar); `concluida` e `incerta` zeram, `cancelada` não conta nem zera. Ao atingir `pedidos.pausa_por_falha` (coluna do
  pedido) o laço pausa o pedido (ator `sistema`, `pausado_motivo = "N falhas seguidas"`) e emite `pausa_automatica`
  (informativo). O laço só marca o estado: as prevista/devida do pedido pausado são `puladas` por `_fechar_dos_parados` na volta
  seguinte.
- **Avisos.** `pedido.aviso` com `{aviso: AvisoDTO}` (contrato 28.9), id determinístico (`<ocorrencia>:ocorrencia_incerta`,
  `<pedido>:pausa_automatica:<ocorrencia>`) para o canal de fora deduplicar. Emitido DEPOIS da transação; falha ao emitir é
  registrada e nunca desfaz o estado. Não há tabela `pedido_avisos` ainda (28.9): o `id` do `AvisoDTO` é sintético até lá.
- **Efeito externo, a segunda cerca** (`CONTAM`, política social): não foi tocada. Uma nova tentativa só existe quando a
  anterior NÃO teve efeito possível, e cada execução passa pela política como qualquer comando; não há teste novo sobre ela aqui.

**Divergências e limites.**

- O prompt pedia `max_tentativas` e `falhas_para_pausar` no bloco `pedidos` da configuração, mas a 067 já tem as colunas
  `pedidos.max_tentativas` (padrão 2) e `pedidos.pausa_por_falha` (padrão 3), NOT NULL. Vale a coluna do pedido; os dois valores
  da configuração são o padrão global (usado se a coluna vier nula e o que a criação do 28.9 deve gravar). `retentativa_base_s`
  e `retentativa_teto_s` só existem na configuração.
- **A espera da nova tentativa mora em `terminada_em`** (a 067 não tem coluna para ela e o 28.5 não leva migração): enquanto a
  ocorrência é `devida` com `tentativa > 0`, `terminada_em` é o instante `nao_antes_de`, que o despacho respeita (`_em_espera`) e
  de onde a janela de recuperação passa a contar (`_limite_da_janela`); `marcar_despachada` o zera. É reaproveitar uma coluna
  fora do sentido (uma `devida` não terminou nada); o custo é essa convenção, documentada aqui e no docstring de
  `RepositorioDePedidos.retentar`, e a saída limpa é uma coluna `proxima_tentativa_em` numa migração futura. Além disso, a
  nova tentativa sai até `tick_s` (15 s) DEPOIS do instante, porque `_espera` olha só a próxima materialização e não o
  `nao_antes_de`; aceito como a D6.
- Os testes do 28.4 inserem o pedido com `max_tentativas=1` (`test_pedidos_laco._pedido`): medem o fechamento, e uma falha sem
  efeito agora ganharia uma repetição. O padrão de verdade (2) é exercido em `test_pedidos_retentativa.py`.
- Não feito (fora do 28.5): `needs_input` → `aguardando_pessoa` (`fechamento.py` o deixa para depois; ainda sem código), as rotas
  de resolver a `incerta` e a tabela de avisos (28.9).

## 14. 28.8: gatilhos de evento, condição e persona (02/10/2026, branch `feat/28-8-gatilhos-evento`)

Desenho do §7.8 de `pedidos-persistentes.md`, escrito antes do código. A 067 já aceita os três tipos (`pedido_gatilhos.tipo`),
as três origens (`pedido_ocorrencias.origem`) e o `cursor TEXT`. O que pede migração é o aviso: o CHECK de
`pedido_avisos.tipo` (072) é fechado e não tem `eventos_perdidos` nem `condicao_atendida` (§14.5).

### 14.1 Cursor: um texto, três formatos

| tipo | `cursor` | quem lê |
|---|---|---|
| `agora`, `horario`, `recorrencia`, `persona` | `formatar_instante` (último instante materializado) | `repo.cursor(g)` |
| `evento` | `ev:<events.id>` (último evento lido, inclusive) | `gatilhos_dinamicos.cursor_de_evento` |
| `condicao` | `cond:<0\|1>:<ocorrencia>` (último veredito e quem o deu) | `gatilhos_dinamicos.cursor_de_condicao` |

`repo.cursor(g)` só é chamado para os tipos de instante. O laço ramifica por tipo ANTES de ler o cursor, em
`_materializar_gatilho`, `_agendar_pedido` e `_pular_o_da_pausa`. Um `ev:`/`cond:` lido como instante quebraria ali.

### 14.2 Evento

- **Spec:** `{"kinds": ["run.finished", ...], "niveis": ["warn", "error"]?}`. São de 1 a 10 kinds, cada um em
  `^[a-z][a-z0-9_.]{0,63}$`, e a criação recusa:
  - o que está em `events.EPHEMERAL_KINDS` (nunca persistido: o gatilho nunca dispararia);
  - o que começa com `pedido.` (o pedido se dispararia com os próprios eventos).
- **Laço fechado, segunda cerca:** o evento de uma execução DO PRÓPRIO pedido não conta
  (`events.run_id IN (SELECT id FROM runs WHERE pedido_id=?)` fica de fora). Sem isso, um gatilho sobre `run.*`
  dispararia com a execução que ele mesmo criou.
- **Linha de base:** na ativação (`_ativar_na_transacao`) e na retomada, o cursor vira `ev:<MAX(events.id)>`. Antes
  disso, só se o cursor estiver NULL. O histórico anterior à ativação nunca dispara. Na retomada `daqui`, o que
  aconteceu durante a pausa também não dispara (pausa = não observar). Na retomada `recuperar`, o cursor fica onde
  estava e os eventos da pausa coalescem numa ocorrência.
- **Uma volta, no máximo uma ocorrência por gatilho:** os eventos novos que casam (`id > cursor`, até
  `lote_eventos`) coalescem numa ocorrência `devida`, de origem `evento`. `previsto_para` é o instante da volta, no
  segundo. O motivo traz só contagem, kinds e o intervalo de ids, nunca a `message` nem o `data` do evento (podem
  ter texto de terceiro).
  - O cursor avança na MESMA transação da inserção e só se a linha nasceu ou já existia com a mesma chave.
  - Duas voltas no mesmo segundo colidem no `UNIQUE (pedido, gatilho, previsto_para)`. Nesse caso a segunda não
    avança o cursor, e a volta seguinte, já em outro segundo, pega os mesmos eventos. Nada se perde nem duplica.
- **Buraco da retenção:** se `cursor < MIN(events.id) - 1`, os eventos entre os dois foram purgados sem ser lidos.
  O laço então:
  1. grava o fato (aviso `eventos_perdidos` com a faixa de ids, §14.5, e a memória `pendencia` `evento.buraco`);
  2. leva o cursor a `MIN(events.id) - 1`;
  3. **não dispara nada pelo buraco.**

  Os eventos que existem depois do buraco seguem a regra normal. A reconciliação "pela tabela dona do fato"
  (§7.8) fica para quando um consumidor concreto disser qual tabela é essa: um kind genérico não tem dona única.
  - Falso positivo possível: um id pulado pela sequência (rollback), exatamente na fronteira da purga, gera aviso
    sem perda real.
  - Erra do lado seguro: avisa a mais, nunca dispara a mais.
- **Margem e lote:** o laço só lê eventos com `ts <= agora - 5 s` (`MARGEM_DOS_EVENTOS`). No PostgreSQL, um id menor pode
  ficar visível DEPOIS de um maior (a sequência não segue a ordem do COMMIT), e o cursor não pode passar por cima dele.
  Lê no máximo 500 por volta. Sem casar nada, o cursor vai ao teto lido: sem isso, a purga dos eventos que não casam
  pareceria um buraco.
- **Piso:** um evento novo dentro do piso da autonomia (`piso_observar_s` ou `piso_agir_s`), contado da última
  ocorrência do gatilho, espera sem mover o cursor; quando o piso passa, os que esperaram coalescem numa ocorrência.
  É o mesmo piso que a prévia aplica à recorrência.
- **Log que volta:** se o cursor passa do `MAX(events.id)` (banco restaurado de backup: a sequência regride), o laço
  refaz a base em `MAX`, grava a memória `pendencia` `evento.base.<gatilho>` e não dispara nada pelo intervalo. Sem
  isso, o gatilho emudeceria sem sinal.
- **Memória recusada não trava o gatilho:** `_lembrar` registra no log a `MemoriaInvalida`, e o cursor avança assim
  mesmo.
- **PostgreSQL:** a margem de 5 s é o que protege o cursor da ordem de COMMIT. Uma transação que segure o INSERT do
  evento por mais tempo que isso perde o evento sem buraco. O SQL novo só rodou em SQLite (o PostgreSQL de teste do
  projeto sobe por Docker). UNKNOWN até a suíte em PostgreSQL.
- **Ritmo:** o pedido só com gatilho de evento fica com `proxima_em` NULL, e `pedidos_para_cuidar` o devolve em toda
  volta (`tick_s`). O pedido com recorrência e evento tem `proxima_em` da recorrência. Por isso os gatilhos de evento
  são lidos num passo próprio (`_avaliar_eventos`, o passo 4 do §7.2), sobre todos os pedidos `ativo`, e não dentro de
  `_materializar_pedido`.
- **Limites do pedido:** `max_ocorrencias`, `fim_em` e o orçamento valem como em qualquer ocorrência (despacho e
  agenda já os aplicam). Passado o `fim_em`, eventos não materializam. Evento não esgota sozinho: o pedido só com
  evento encerra por `fim_em` ou `max_ocorrencias`, ou cancelado.

### 14.3 Persona ("volto quando fizer sentido")

- **Spec:** `{"intervalo_min_s": N, "intervalo_max_s": M}`, com `300 <= N <= M <= 30 dias`.
- **Instantes:** a primeira visita é a ativação (como `agora`). Cada visita seguinte só nasce quando a anterior FECHOU,
  em `terminada_em + intervalo`:
  - a proposta é a observação `proxima_visita_s` (tipo `number`, `situacao='observado'`) que a ocorrência anterior
    gravou. É o canal das `saidas` da execução (12.4), que o fechamento do 28.7 já transforma em observação;
  - sem proposta (ou proposta `incerto`/`ausente`), vale `intervalo_max_s`;
  - a proposta é presa a `[intervalo_min_s, intervalo_max_s]` e a `fim_em`.
- **Rotulagem:** origem `persona` e a chave determinística de sempre (`ped:<pedido>:<gatilho>:<instante>`). O cursor é
  o de instante, e o caminho é o mesmo de `_materializar_gatilho` (janela, coalescência, perdida).
- **Orçamento:** o despacho já barra a ocorrência sem verba (28.6). A persona não fura o teto: propor uma visita mais
  cedo só antecipa dentro do intervalo, e a visita custa o mesmo que qualquer ocorrência.
- **Limite honesto:** nenhum plano de hoje emite `proxima_visita_s`, e a proposta só existe se o objetivo pedir essa
  saída. Na prática, esta fatia visita a cada `intervalo_max_s` até um plano aprender a propor.

### 14.4 Condição

- **Spec:** `{"observacao": "<nome>", "op": "<|<=|>|>=|==|!=|mudou", "valor": <número ou texto>}`.
  - `mudou` não leva `valor`: compara o `sha256` com o da observação anterior de mesmo nome.
  - Os ops de ordem exigem número dos dois lados.
- **Quando:** no passo 4 de cada volta, sobre a observação mais nova do nome (de qualquer ocorrência do pedido) que ainda
  não deu veredito. Não custa execução nem IA. É durável: a observação já está gravada, e uma queda no meio só adia o
  veredito para a volta seguinte. Avaliar dentro de `_fechar_uma` perderia o veredito numa queda logo depois do fechamento.
- **Vários alvos:** cada alvo da ocorrência é avaliado contra a observação de mesmo alvo da ocorrência anterior (a do
  `mudou`). O veredito é verdadeiro se algum alvo é verdadeiro, falso se algum é falso e nenhum verdadeiro, e nenhum nos
  outros casos.
- **Avaliação:** só observação `situacao='observado'` com tipo compatível. `incerto` e `ausente` não dão veredito
  (o cursor não muda), e o predicado largo do 28.5 não vira fato.
- **Disparo por borda, não por nível:** só a passagem de falso para verdadeiro gera o aviso `condicao_atendida`
  (§14.5) e a memória `descoberta` `condicao.<gatilho>`. Verdadeiro seguido de verdadeiro não repete. O cursor
  `cond:<0|1>:<ocorrencia>` guarda o último veredito, e repetir o fechamento (reentrante) não avisa duas vezes:
  - a chave do aviso é `condicao_atendida:<gatilho>:<ocorrencia>`;
  - a ocorrência que já deu o veredito é ignorada.
- **A condição não cria ocorrência.** "Quando cair abaixo de X, faça Y" seria efeito disparado por observação, e o
  §12.2 só pede o aviso; comprar é proibido em qualquer grau. A origem `condicao` da 067 fica sem uso nesta fatia.

### 14.5 Avisos e migração

- `eventos_perdidos` (warn) e `condicao_atendida` (warn), os dois com `requer_pessoa=0`.
  - Chaves: `eventos_perdidos:<gatilho>:<ate_id>` e `condicao_atendida:<gatilho>:<ocorrencia>`.
  - O texto do aviso não leva valor observado de terceiro: só o nome da observação, o operador e o limiar que a própria
    pessoa escreveu. O título do pedido entra por função, nunca por `str.format`, porque um `{` no título derrubaria o
    aviso.
- **Migração `076_pedido_avisos_gatilhos.sql`** (número dado pela coordenação em 02/10; 074 e 075 entram antes):
  - SQLite: reconstrói `pedido_avisos` com o CHECK ampliado (molde da 047). A FK para `pedidos` com CASCADE e o
    índice `ix_pedido_avisos_pedido` ficam; nada aponta para a tabela;
  - PostgreSQL: `DROP CONSTRAINT pedido_avisos_tipo_check` / `ADD CONSTRAINT`.
- O laço grava a memória E emite o aviso pelo ponto único (`CaixaDeAvisos.registrar`, dedupe pela chave). `avisos.TIPOS`
  é conferido contra o CHECK da 076 em `test_pedidos_avisos.py`.

### 14.6 Edição e encerramento

- `PATCH` (`acoes.editar`) continua trocando só os gatilhos de `agora`, `horario` e `recorrencia`. Os três novos ficam
  como foram criados; para mudá-los, cancela-se e cria-se outro pedido. Pedir a troca por um deles é
  `422 gatilho_nao_suportado`, com `campo` `gatilhos[0].tipo`.
- `_agendar_pedido`: passado `fim_em`, evento e persona contam como esgotados (motivo `prazo`), e o pedido encerra. Antes
  disso, o pedido segue vivo. A condição não segura o pedido sozinha: com a recorrência esgotada, o pedido encerra mesmo
  que a condição exista.

### 14.7 O que foi feito (02/10/2026, branch `feat/28-8-gatilhos-evento`)

- **Domínio:** `domain/gatilhos_dinamicos.py` (puro), com validação da `spec`, cursores, `buraco`, `proxima_visita`,
  `avaliar`, `disparou` e `descrever`.
- **Prévia:** `normalizar_gatilho` aceita os três, com os códigos `gatilho_invalido` e `condicao_sem_observacao` e o
  piso da persona. `_datas` dá à persona a data da ativação.
- **Laço:** o passo `_gatilhos_dinamicos`, entre materializar e orçamentos; `_agendar_pedido` passou a tratar `fim_em`.
- **Linha de base:** `RepositorioDePedidos.base_dos_eventos` é chamado na ativação e na retomada `daqui`.
- **Prova `simulated`:** `backend/tests/test_pedidos_gatilhos_dinamicos.py` (23 testes). O aceite "cursor abaixo do menor
  evento gera aviso de buraco, não disparo" está em `test_buraco_e_condicao_viram_aviso_gravado_uma_vez` (com a 076) e
  em `test_buraco_da_retencao_registra_e_nao_dispara`. A migração está em `test_pedidos_avisos.py` (reconstrução sem
  perder aviso, tipos novos aceitos, cascata e índice). PostgreSQL e `real`: `not_run`.
- **Ocorrência de evento pulada pela sobreposição:** o motivo do pulo se anexa à faixa de eventos (`…; eventos: N
  evento(s) …`), sem substituí-la.

### 14.8 Tela

- A criação pelo painel (`NovoPedido.tsx`) não oferece os tipos novos nesta fatia: o 28.8 é API.
- O que renderiza é o `gatilhos_resumo` na lista e no detalhe. `descrever_gatilho` ganha texto para os três tipos:
  - "Quando acontecer: run.finished";
  - "Avisa quando preco_total < 3500";
  - "A persona volta entre 1 h e 1 dia".

  Isso é conferido no navegador a 1366 e 375 px, contra o backend simulado.
