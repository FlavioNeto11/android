# Aprendizados

Armadilhas que custaram tempo, com o que funcionou e o que não funcionou — para não serem redescobertas. Uma
entrada por armadilha, `### K-NNN — título`. "Aplicabilidade" diz **vigente** (conferido no código de hoje) ou
**superado em `<commit/data>`** (o código mudou e o aprendizado não se aplica mais tal como descrito — mantido pelo
histórico, não como instrução atual). Nenhuma entrada cita nome de conta, e-mail ou o nome do dono.

Quando um aprendizado vira regra (repetido, custou tempo de novo, vale para além deste projeto), ele é promovido
para o doc principal do assunto ou para `.claude/rules/`, e o texto aqui ganha "promovido para `<destino>`" — ver
`docs/conhecimento/README.md`.

---

### K-001 — O harness de teste usava as portas do parque real

**Data:** 18/09/2026 · **Área:** testes (backend)

**Sintoma.** Um `HOME` disparado pela suíte derrubou um canário em execução no parque de verdade.

**Causa.** `make_config` do harness de teste construía instâncias com as **mesmas** portas de console ADB do parque
real (`5554+`), então um comando emitido "isolado" em teste podia alcançar um emulador de produção.

**O que funcionou.** `base_console_port: 5640` no `make_config` do harness — uma faixa de porta que nenhum emulador
real usa.

**O que não funcionou.** Assumir que a suíte era isolada dos emuladores "porque roda em processo próprio" — a
isolação de processo não isola a porta TCP que o adb usa para falar com o console do emulador.

**Aplicabilidade.** Vigente — `backend/tests/conftest.py:48` ainda declara `base_console_port: 5640`.

**Fonte.** Memória `poc-instagram-dominio.md`.

---

### K-002 — `config.yaml`/`.env` fora do Git: checkout entre commits apaga o de produção

**Data:** 23/09/2026 · **Área:** operação, deploy

**Sintoma.** Backend subiu em produção com `worker_port: 0` e zero aparelhos remotos, sem nenhum aviso — o
`/api/health` confere commit e migração, não o conteúdo do que foi carregado.

**Causa.** `config/config.yaml` deixou de ser rastreado pelo Git num commit; um `git checkout`/fast-forward de um
commit onde ainda era rastreado para um onde não é **apaga** o arquivo da árvore de trabalho, e `start.ps1`
recriava do `config.example.yaml`.

**O que funcionou.** `start.ps1` agora PARA quando há `data/poc.sqlite3` mas falta `config`/`.env`, em vez de
recriar do exemplo, e aponta para a cópia mais recente em `data/backups/`; `backup.ps1` passou a guardar `config/`.

**O que não funcionou.** Confiar que "commit + migração corretos" no `/health` significa configuração correta — os
dois são independentes.

**Aplicabilidade.** Vigente — ver ADR-011 em `docs/decisoes.md`.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`; commit `09c040f`.

---

### K-003 — "malformed database schema" depois de reboot, por `-wal` velho

**Data:** 23/09/2026, noite · **Área:** banco (SQLite)

**Sintoma.** `data/poc.sqlite3` acordou com "malformed database schema" depois do reboot dos dois notebooks.

**Causa.** Um arquivo `-wal` antigo ao lado de um `.sqlite3` recopiado por fora do fluxo de backup/restore (cópia
direta, sem o backend parado e sem `integrity_check`) produz esse estado.

**O que funcionou.** Restaurar com `scripts/restore.ps1 -De data/backups/<carimbo> -Confirmar` (backend **parado**;
ele move `poc.sqlite3`/`-wal`/`-shm` para `data/substituido-<carimbo>` e confere integridade antes de trocar).

**O que não funcionou.** Copiar o `poc.sqlite3` do backup por cima à mão — além de não limpar o `-wal` velho, o
arquivo certo fica na **raiz** do backup, não em `data/` dentro dele.

**Aplicabilidade.** Vigente.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`.

---

### K-004 — Restaurar o banco regride a cerca, e o agente recusa `start`

**Data:** 23/09/2026 · **Área:** worker, comandos (fencing)

**Sintoma.** Depois de restaurar um backup, `start` de aparelhos remotos ficava `failed` com "cerca N é anterior à
última executada (M)", e o aparelho ficava `stopped` sem reparo automático (só `error` reentra sozinho).

**Causa.** `commands.fence` é `MAX(fence) + 1` por aparelho; restaurar um banco mais antigo volta o `MAX` para trás,
mas o **agente** guarda a maior cerca já executada em disco (no diário) e a mantém depois do restore — o central
emite cerca menor que a que o agente já viu.

**O que funcionou.** Subir manualmente o `fence` do último comando do aparelho no SQLite até o valor M que o agente
citou no erro, e reemitir o comando.

**O que não funcionou.** Nada automático — não existe reparo para este caso hoje.

**Aplicabilidade.** Vigente. Candidato a correção de código registrado na própria memória: o `hello` do agente
poderia informar a maior cerca por aparelho e o central pular direto para ela — **não implementado**.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`.

---

### K-005 — Tarefa agendada sem gatilho de boot não volta sozinha

**Data:** 23/09/2026, noite · **Área:** operação (worker)

**Sintoma.** Depois de reiniciar o notebook do worker, o agente não voltou sozinho.

**Causa.** A tarefa `farm-agente`, registrada por um script antigo (`agente-tarefa.ps1`), tinha **zero gatilhos** —
nada a disparava no boot.

**O que funcionou.** Reinstalar com `scripts/worker-agent.ps1 -Instalar`, que registra `AtStartup`,
`RestartCount 999` e log com rotação.

**O que não funcionou.** Assumir que qualquer tarefa agendada existente cobre o boot — é preciso conferir o
gatilho, não só a existência da tarefa.

**Aplicabilidade.** Vigente. `docs/worker.md` (seção "Serviço") documenta o script correto.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`.

---

### K-006 — PowerShell 5.1 exige `.ps1` com BOM UTF-8

**Data:** 23/09/2026 · **Área:** scripts (PowerShell)

**Sintoma.** `scripts/worker-agent.ps1`, copiado sem BOM, quebrava o parser no travessão (`—`) usado no texto do
script quando executado pelo PowerShell 5.1 (o interpretador padrão do Windows Server sem atualização).

**Causa.** PowerShell 5.1 assume a página de código do sistema para `.ps1` sem BOM; caracteres fora de ASCII (como
`—`) corrompem no parse. PowerShell 7+ (`pwsh`) não tem esse problema.

**O que funcionou.** Gravar/copiar o arquivo com BOM UTF-8.

**O que não funcionou.** Copiar o arquivo com uma ferramenta que grava UTF-8 sem BOM (comum em `scp`/editores
Unix-first).

**Aplicabilidade.** Vigente — é uma característica do PowerShell 5.1, não algo que o projeto corrige de uma vez.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`.

---

### K-007 — Nenhuma literal do workflow `.js` pode ter quebra de linha real dentro

**Data:** 24/09/2026 · **Área:** `.claude/workflows/plano-100.js`

**Sintoma.** O diálogo de aprovação do Workflow recusava o script com "control characters that would be hidden in
the approval dialog".

**Causa.** Uma string literal de várias linhas com quebra de linha **de verdade** dentro (não escapada) no arquivo.
Editar o arquivo por script agrava o problema: o shell come as contrabarras e um `\n` escrito num script vira quebra
de linha de verdade sem avisar.

**O que funcionou.** Montar texto de várias linhas com `[...].join(NL)` (como em `REGRAS` no workflow); manter o
arquivo em LF (`.gitattributes`: `.claude/workflows/*.js text eol=lf`).

**O que não funcionou.** Editar o `.js` com heredoc/`sed` sem conferir o resultado byte a byte.

**Aplicabilidade.** Vigente — `.gitattributes` ainda declara a regra; promovido para
`.claude/rules/workflows-js.md`.

**Fonte.** `docs/claude-plano-100.md` ("Duas armadilhas"); `.gitattributes`.

---

### K-008 — Um subagente pode recusar a tarefa delegada

**Data:** 24/09/2026 · **Área:** orquestração (plano-100)

**Sintoma.** Na primeira execução real da esteira, um subagente leu o pedido da conversa (que tinha outro assunto)
no próprio contexto, concluiu que esse pedido prevalecia sobre a tarefa que o workflow lhe deu, devolveu
`items: []` com uma explicação — e não alterou uma linha de código. O item desapareceu do registro em silêncio.

**Causa.** Nada no prompt do agente afirmava, de forma explícita, que a delegação da esteira **era** a tarefa
autorizada daquela chamada.

**O que funcionou.** O prompt passou a afirmar isso explicitamente, com "uma linha por ID é obrigatória"; o
workflow e `scripts/claude-plan-100.py` (`validar`) passaram a **recusar** resultado que não cubra os IDs pedidos —
`ausentes` levanta `ErroDoPlano`.

**O que não funcionou.** Confiar que "o subagente vai fazer o que foi pedido" sem uma verificação que barra a
aplicação de um resultado incompleto.

**Aplicabilidade.** Vigente — `scripts/claude-plan-100.py::validar` ainda levanta erro quando `ausentes` não é
vazio.

**Fonte.** `docs/claude-plano-100.md` ("Duas armadilhas").

---

### K-009 — `pwsh -File script.ps1 -Param a,b,c` não vira array

**Data:** 17/09/2026, reconfirmado em 19/09/2026 · **Área:** scripts (PowerShell)

**Sintoma.** `worker-tunnel.ps1 -Portas 1,2` subiu a tarefa encaminhando a string `"1555515557"` em vez de duas
portas.

**Causa.** Passar uma lista separada por vírgula como argumento de linha de comando (via `pwsh -File`, não dentro
do próprio PowerShell) não faz o binding de parâmetro convertê-la em array — chega como uma string só.

**O que funcionou.** Passar como texto único (`-Mapa "15555:5555,15557:5557"`) e fazer o parse (`-split ','`)
**dentro** do script.

**O que não funcionou.** Declarar o parâmetro como `[string[]]` e confiar na conversão automática — só funciona
quando o script é chamado de dentro de uma sessão PowerShell, não via `-File` a partir de outro processo.

**Aplicabilidade.** Vigente — `scripts/worker-tunnel.ps1` usa `-split ','` (linhas 98, 275) para os mapas de porta,
não array tipado.

**Fonte.** Memória `host-android-poc-gotchas.md`, `poc-instagram-dominio.md`; `docs/parque-distribuido.md`.

---

### K-010 — Não fazer pipe da saída de `start.ps1`

**Data:** 17/09/2026 · **Área:** scripts (PowerShell)

**Sintoma.** `scripts/start.ps1 | Select-Object ...` nunca termina.

**Causa.** O backend filho, iniciado dentro do script, herda o pipe do processo pai; o comando de pipeline espera o
filho fechar a saída, e o backend fica no ar de propósito.

**O que funcionou.** Rodar `start.ps1` sem pipe, e inspecionar `/api/health` ou os logs separadamente.

**Aplicabilidade.** Vigente — natureza do problema (herança de handle em processo filho de longa duração) não
depende de versão de código.

**Fonte.** Memória `host-android-poc-gotchas.md`.

---

### K-011 — Bateria de avaliação parou por falta de crédito da API

**Data:** 24/09/2026 · **Área:** custo de IA, operação

**Sintoma.** A bateria pós-otimização (item 7.4) parou no 3º caso de QA com HTTP 400 de billing.

**Causa.** O saldo da conta da API Anthropic (distinto do crédito de sessão da IDE) ficou em ~US$ 3,3 depois do
gasto do dia (US$ 6,07) — sem disjuntor que parasse a bateria **antes** de a conta zerar, o erro só apareceu como
falha de chamada individual.

**O que funcionou.** O disjuntor de conta (item 0.6, anterior a este episódio) evita queimar tentativa numa etapa
específica ao detectar erro de cobrança/credencial — mas não impede que uma bateria programada para N casos pare no
meio por falta de saldo.

**O que não funcionou.** Rodar uma bateria de avaliação sem confirmar o saldo disponível antes — o resultado
parcial é **inválido** e precisa ser repetido do zero quando o saldo for recarregado, não retomado do ponto onde
parou.

**Aplicabilidade.** Vigente — não há mecanismo que impeça a repetição deste episódio; é processo, não código.

**Fonte.** Memória `poc-otimizacao-custo-ram.md`, `creditos-dev-vs-api.md`.

---

### K-012 — `python.exe` do venv no Windows é um launcher (dois processos)

**Data:** 17/09/2026 · **Área:** operação (backend)

**Sintoma.** Depois de iniciar o backend, apareciam **dois** processos `app.main`; matar um derrubava o outro.

**Causa.** O `python.exe` dentro de `backend/.venv/Scripts/` no Windows é um launcher que reexecuta o interpretador
real num processo filho — comum a ambientes virtuais no Windows, não específico deste projeto.

**O que funcionou.** Tratar os dois PIDs como uma unidade (matar pelo supervisor, não por PID isolado);
`supervisor._matar_filhos` mata `children(recursive=True)`.

**Aplicabilidade.** Vigente — característica da plataforma, não do código do projeto.

**Fonte.** Memória `host-android-poc-gotchas.md`.

---

### K-013 — `uiautomator dump` morre com sessão Appium ativa

**Data:** 18/09/2026 · **Área:** automação (Android)

**Sintoma.** Rodar `adb shell uiautomator dump` para inspecionar a árvore de tela, com uma sessão Appium aberta no
mesmo aparelho, retorna código 137 (morto).

**Causa.** Appium já mantém o próprio canal de UiAutomator2 aberto no aparelho; um segundo processo `uiautomator`
concorrente é encerrado.

**O que funcionou.** Usar `GET /api/instances/{id}/hierarchy` (que já lê pela sessão Appium existente) em vez de
`adb shell uiautomator dump` direto quando há sessão de automação ativa.

**Aplicabilidade.** Vigente — a rota `GET /instances/{instance_id}/hierarchy`
(`backend/app/api.py:2341`) continua sendo o caminho recomendado.

**Fonte.** Memória `poc-instagram-dominio.md`.

---

### K-014 — Acentos em `curl -d` pelo Bash corrompem o JSON

**Data:** 18/09/2026 · **Área:** ferramentas de linha de comando

**Sintoma.** Corpo JSON com acentos (português) enviado via `curl -d '{"texto":"não"}'` chegava corrompido no
servidor.

**Causa.** A forma como o Bash passa a string do `-d` para o `curl` não preserva a codificação UTF-8 de forma
confiável em todos os ambientes.

**O que funcionou.** Mandar o corpo por arquivo (`curl --data-binary @arquivo.json`) e conferir a resposta antes de
fazer polling.

**Aplicabilidade.** Vigente — é comportamento de shell/curl, não do projeto.

**Fonte.** Memória `poc-instagram-dominio.md`.

---

### K-015 — `cache_control` condicionado subcontava tokens e desativava o cache

**Data:** 24/09/2026 · **Área:** custo de IA

**Sintoma.** 46 decisões em 24/09 saíram com `cache_read = cache_write = 0` e o dobro de tokens de entrada por
decisão no Sonnet/Haiku.

**Causa.** `anthropic_provider._kwargs` só marcava `cache_control` quando `len(system)//4 >= min_cache_tokens` —
uma estimativa que contava só o texto do `system` e ignorava as `tools`, subcontando o prefixo real (~6 091 tokens
medidos) contra os mínimos de cache do Sonnet 5 (1024) e do Haiku 4.5 (4096). O Opus (mínimo 512) continuava
cacheando porque o mínimo dele era baixo o bastante para a conta errada ainda passar.

**O que funcionou.** Marcar `cache_control` **sempre**, sem condição — a API ignora em silêncio um ponto de cache
abaixo do mínimo do modelo, sem erro e sem custo extra; não há motivo para a aplicação tentar adivinhar o tamanho.

**O que não funcionou.** Qualquer heurística de tamanho calculada do lado do cliente — o modelo certo é "sempre
marcar, deixar a API decidir".

**Aplicabilidade.** Vigente — `backend/app/planning/anthropic_provider.py:156` marca `cache_control` sem condição.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`; `docs/relatorio-validacao.md` §11.

---

### K-016 — `price_for` casava pelo primeiro prefixo, não pelo mais longo

**Data:** 24/09/2026 · **Área:** custo de IA

**Sintoma.** Com `claude-opus-5` cadastrado em `ai.prices` antes de `claude-opus-5-5`, chamadas ao modelo
`claude-opus-5-5` eram cobradas pela tarifa de `claude-opus-5` (mais barata e errada).

**Causa.** `price_for` casava pelo primeiro prefixo que desse `startswith`, na ordem de inserção do dicionário —
`opus-5` "engolia" `opus-5-5`.

**O que funcionou.** Casar por igualdade exata primeiro; se não houver, pelo prefixo **mais longo** entre os
candidatos (`max(candidatos, key=len)`).

**Aplicabilidade.** Vigente — `backend/app/planning/costs.py::price_for` faz exatamente essa ordem.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`.

---

### K-017 — A tag `qwen3-vl:4b` do Ollama é a variante *thinking* e devolve conteúdo vazio

**Data:** 24/09/2026 · **Área:** IA (ator local)

**Sintoma.** Chamadas ao ator local com o modelo `qwen3-vl:4b` voltavam com `content` vazio, gastando toda a saída
em raciocínio interno.

**Causa.** A tag `qwen3-vl:4b` (sem sufixo) é a variante *thinking* do modelo; `think: false` e `reasoning_effort`
são **ignorados** por ela — a tag correta para uso direto de conteúdo é a variante `-instruct`.

**O que funcionou.** Usar sempre `qwen3-vl:4b-instruct` (ou `-instruct-16k`, com `num_ctx` maior via Modelfile).

**Aplicabilidade.** Vigente — `config/config.yaml` (fora do Git) referencia a tag `-instruct` em produção.

**Fonte.** Memória `ollama-ator-local.md`.

---

### K-018 — Escalonador de núcleo do Hyper-V deixava o notebook do worker lento

**Data:** 24/09/2026 · **Área:** infraestrutura (worker)

**Sintoma.** O notebook da LAN (192.168.1.19) ficava consistentemente lento com os emuladores ligados — boot de 34
minutos para 6 aparelhos.

**Causa.** O Hyper-V estava com o escalonador de CPU em modo **core** (`0x3`), que reserva núcleos inteiros por VM
em vez de compartilhar tempo de CPU de forma mais fina entre os emuladores.

**O que funcionou.** `bcdedit /set hypervisorschedulertype classic` + reinício. Medido: emulador ocioso caiu de
1,6–2,2 núcleo para 0,18–0,23; 6 aparelhos passaram a caber em ~2 dos 12 núcleos (CPU 23%); boot de 6 aparelhos caiu
de 34 min para menos de 8 min.

**O que não funcionou.** Adicionar RAM ou reduzir o número de aparelhos simultâneos — o gargalo era o escalonador,
não a quantidade de recurso.

**Aplicabilidade.** Vigente, mas é configuração de máquina, não código — se o notebook voltar a ficar lento,
conferir o evento Hyper-V-Hypervisor id 2 (precisa ser `0x2`, não `0x3`).

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`.

---

### K-019 — Imagens `android-28`/`29` e `aosp_atd` não têm tradução ARM

**Data:** 17/09/2026 · **Área:** emuladores (imagens de sistema)

**Sintoma.** Apps sociais (compilados só para ARM) não abrem ou travam nessas imagens.

**Causa.** Essas imagens de sistema x86_64 não incluem a camada de tradução de instrução ARM→x86 que apps sociais
normalmente exigem — diferente de imagens mais novas (`google_apis` API 33+), que a incluem.

**O que funcionou.** Restringir o parque de contas sociais a imagens com tradução ARM confirmada (medido com
`scripts/probe-image.ps1`).

**Aplicabilidade.** Vigente — é característica da imagem do sistema Android, não algo que o código do projeto
mude.

**Fonte.** Memória `poc-otimizacao-custo-ram.md`.

---

### K-020 — Login do Instagram tocava "Entrar" nas coordenadas do formulário vazio

**Data:** 18/09/2026 · **Área:** automação (Instagram)

**Sintoma.** O fluxo de login parava sem erro, como se estivesse bloqueado pelo Instagram — mas o app nunca recebia
o toque em "Entrar".

**Causa.** Ao preencher usuário e senha, o teclado virtual sobe e empurra o botão "Entrar" para cima; o código
tocava na posição lida **antes** de o teclado aparecer, e o toque caía no vão onde o botão costumava estar.

**O que funcionou.** Reler a tela e usar a posição **atual** do botão antes de tocar, depois do preenchimento.

**Aplicabilidade.** Vigente — não há registro de regressão desde a correção (commits `d7075cd`/`f90433c` de
18/09).

**Fonte.** Memória `poc-instagram-dominio.md`.

---

### K-021 — `read_account` lia o autor do reel em foco, não a conta própria

**Data:** 18/09/2026 · **Área:** automação (Instagram)

**Sintoma.** O sistema acusava "conta errada" numa conta que na verdade estava certa.

**Causa.** O campo de cabeçalho (`action_bar_title`) usado para ler o usuário logado é o **mesmo campo** que, no
feed, mostra o autor do reel em foco (ex.: um `@` de outra pessoa) — ler a tela onde o fluxo "caiu" em vez de
navegar deliberadamente até a aba de perfil pegava esse valor errado.

**O que funcionou.** `read_account` passou a navegar explicitamente até a aba de perfil **antes** de ler — só ali o
cabeçalho é garantidamente a conta própria.

**Aplicabilidade.** Vigente — `backend/app/integrations/instagram/verification.py::read_account` documenta e
implementa essa navegação antes da leitura.

**Fonte.** Memória `poc-instagram-dominio.md`.

---

### K-022 — Receita com zero ações e o laço "tocar → voltar"

**Data:** 23–24/09/2026 · **Área:** receitas, execução

**Sintoma.** (a) Uma receita vazia era aprendida quando a IA dizia "pronto" com o aparelho já no estado final —
replicá-la em outra tela derrubava a etapa. (b) Em execuções seguintes, a IA entrava num laço de duas ações
(tocar → voltar) sem perceber que não progredia.

**Causa.** (a) `distill` não recusava uma sequência de ações vazia. (b) Não havia detecção de ciclo por
**estrutura** da tela (só por conteúdo, que muda mesmo sem progresso real).

**O que funcionou.** (a) `distill`/`distill_training` recusam quando `not out` (nenhuma ação de efeito
sobrevive ao filtro). (b) `ciclo_sem_progresso` detecta o laço de duas ações pela assinatura **estrutural** da tela
(`UiTree.signature(estrutural=True)`, sem texto), e a tentativa seguinte recebe no histórico o erro da anterior.

**Aplicabilidade.** Vigente — `backend/app/taskqueue/recipes.py:234` (`if not out`) e
`backend/app/taskqueue/executor.py:1214` (`ciclo_sem_progresso`) confirmados no código atual.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`.

---

### K-023 — `Agent` com `isolation: remote` não necessariamente roda na nuvem

**Data:** 24/09/2026 · **Área:** orquestração, ferramentas do agente

**Sintoma.** Uma chamada à ferramenta `Agent` com `isolation: remote`, esperando rodar num ambiente de nuvem
separado (e portanto consumir cota de nuvem, não a da sessão local), caiu num **worktree local** — gastando cota
local da IDE em vez da cota remota esperada.

**Causa.** A disponibilidade de execução remota depende de uma sessão remota explícita já existir; sem isso,
`isolation: remote` degrada silenciosamente para um worktree local, sem aviso de que a expectativa de custo mudou.

**O que funcionou.** Conferir, depois de disparar um agente com `isolation: remote`, se ele de fato está numa
sessão remota antes de assumir que o gasto será de cota de nuvem — e preferir a esteira do plano-100 (workflow
Sonnet por grupo de arquivos) quando o objetivo é justamente poupar a cota local da IDE.

**Aplicabilidade.** Vigente — é comportamento da ferramenta, não algo que o projeto controle.

**Fonte.** Memória `config-nao-versionado-e-agente-do-worker.md`.

---

### K-024 — Caminho do Windows dentro de string Python vira caractere de controle

**Data:** 25/09/2026 · **Área:** edição de arquivos por script

**Sintoma.** Um documento editado por script ficou com `backend<CR>equirements.txt`: a barra e o `r` de
`backend\requirements.txt` sumiram e viraram um retorno de carro solto. Aconteceu três vezes na mesma
sessão (a terceira ao escrever este registro). `\S` e `\.` só geram `SyntaxWarning`, mas `\r`, `\n`, `\t` e `\b`
trocam o texto em silêncio.

**Causa.** String Python comum (não *raw*) interpreta as sequências de escape. Caminho do Windows escrito à mão
dentro de um heredoc que gera Python cai nisso.

**O que funcionou.** Para texto com caminho do Windows, usar string *raw* (`r"..."`), montar a barra com
`chr(92)`, ou editar pela ferramenta Edit. Depois de editar por script, procurar CR solto:
`python -c "b=open(p,'rb').read(); print(13 in b.replace(bytes([13,10]), bytes()))"` — com bytes
por código, sem escape nenhum.

**Aplicabilidade.** Vigente.

**Fonte.** Sessão de 24–25/09 (documentação e T.4); correções em `docs/operacao.md` e `docs/estado-atual.md`.

### K-025 — O emulador usa só o 1º DNS IPv4 do host, sem fallback

**Data:** 25/09/2026 · **Área:** parque, rede dos emuladores

**Sintoma.** android-06 `online`, "pronto", tela ao vivo — e nenhum nome resolvia; o Instagram dizia "An unexpected
error occurred" no login. Os outros aparelhos pareciam sãos.

**Causa.** O DHCP do roteador entrega `1.178.36.77` (morto) antes de `8.8.8.8`. O Windows contorna com fallback; o
slirp do emulador (37.1.11) pega só o primeiro DNS IPv4 (`IPv4 server found: 1.178.36.77` no log de todo boot) e
ignora os IPv6, então o `10.0.2.3` da rede móvel morre em TODOS os AVDs. O Wi-Fi virtual (netsim, daemon próprio com
`host_dns` e fallback IPv6) mascarava; no android-06 a `AndroidWifi` estava `PERMANENTLY_DISABLED`. Os `fec0::ffff:*`
do adaptador do WSL não entram na lista.

**O que funcionou.** `android.dns_servers` (por máquina) → `-dns-server a,b`, com os servidores testados antes por
`Resolve-DnsName <host> -Server <ip>`. Com ele o log não tem mais `IPv4 server found`, a rede MOBILE passa a
`VALIDATED` e o resolver zera os `-110`. E `connectivity` separada de `online`: "adb responde" não prova internet.

**Aplicabilidade.** Vigente. O worker da LAN ainda sem `dns_servers`.

**Fonte.** Validação runtime do PR #4 (`docs/handoffs/android-device-persona-runtime.md`).

### K-026 — Snapshot restaurado com o Android congelado passava como "acordou"

**Data:** 25/09/2026 · **Área:** parque, ciclo de vida, worker

**Sintoma.** Wake do android-09 no worker-lan-01: comando `succeeded` em 50 s, aparelho `online` — e `service
check`, `dumpsys`, `screencap` travando, depois até `getprop`/`date`, também pelo adb LOCAL do worker (não era o
túnel). O cartão ficou `online` sem aviso por 8+ min, com internet `unknown`.

**Causa.** Três lacunas juntas: (1) o boot era dado por pronto com adb `device` + `boot_completed`, que o snapshot
restaura como `1` mesmo com o framework congelado; o preparo falhando era só aviso; (2) a primeira sonda de saúde
MUDA era lida como "não sei, então vivo" e o aparelho entrava no ar; (3) as sondas só rodavam com a fila do
aparelho vazia, e a captura estourando 25 s em série mais a sessão do Appium falhando a mantinham cheia.

**O que funcionou.** Degrau ANDROID_RESPONSIVE (`framework_alive`, que passa pelo binder) antes de `online` e antes
de o worker fechar `start`/`wake`; `readiness` visível no DTO; trilha própria (`rt.sonda`) para as sondas. A causa
do congelamento em si (restauração no notebook) está em aberto; evidência em
`C:\farm\evidencia\android-09-wake-travado-20260925`.

**Aplicabilidade.** Vigente.

**Fonte.** Validação runtime de 25/09/2026 (hibernação remota); PR de prontidão.

### K-027 — Health de outro serviço na mesma porta segurava a subida da Farm

**Data:** 26/09/2026 · **Área:** operação, supervisor, deploy

**Sintoma.** `deploy.ps1` abortou: "a tarefa farm-central subiu, mas /api/health não respondeu em 120 s". O
`supervisor.log` repetia "já há um backend respondendo nesta porta e ele não é meu". A produção ficou fora do ar.

**Causa.** O container `cartorio-api-1` (outro projeto, Docker Desktop) publica `0.0.0.0:8000`. O backend da Farm
escuta em `127.0.0.1:8000` e ganha o tráfego enquanto está no ar; parado, o `127.0.0.1:8000` cai no listener do
Docker, que responde `404 {"detail":"Not Found"}`. `saude_responde` tratava QUALQUER `HTTPError` como vivo.

**O que funcionou.** Identidade estável no health (`service`), reconhecimento legado estrito do esquema antigo,
e a mesma pergunta nos scripts. Enquanto o PR não estava implantado, a Farm voltou por `scripts\start.ps1`
destacado (o `start.ps1` usa `Invoke-RestMethod`, que lança no 404 — por acaso, a decisão certa).

**Aplicabilidade.** Vigente.

**Fonte.** Deploy de 26/09/2026 ~01:33 UTC; PR de identidade do backend.

### K-028 — `service check` não prova o framework: o wake congelado passaria pelo portão do PR #5

**Data:** 26/09/2026 · **Área:** parque, prontidão, worker

**Sintoma.** Análise forense do wake do android-09 (25/09): o `prepare_for_automation` do worker ficou 40 s sem
resposta (`adb shell excedeu 40s`, agente.log) e o wake fechou `succeeded` 0,3 s depois; a captura de tela nunca
respondeu depois do restore; `service check` só travou ~4 min depois.

**Causa.** `AdbTimeout` é subclasse de `AdbError` e o preparo que ESTOURA o prazo recebia a mesma semântica do erro
rápido (aviso). E `framework_alive` (`service check`) pergunta ao `servicemanager`, processo separado: prova serviços
registrados, não que o `system_server` atende nem que o SurfaceFlinger produz quadros.

**O que funcionou.** Escada de três leituras só leitura e baratas (`devices/prontidao.py`, 0,1-0,4 s num Android
saudável): `service check` → `settings get global window_animation_scale` → `screencap > /dev/null`. Pronto só com
as três; worker e central usam a mesma função; orçamento por rodada cortado pelo prazo de boot/wake. E o contrato é
TEMPORAL: os três precisam responder DEPOIS do último sinal de não-resposta — preparo estourado depois de uma sonda
positiva invalida a prontidão (achado na revisão do PR: readoção e adoção externa sondavam antes do preparo). E
"rodada nova depois do timeout" não basta: `AdbTimeout` encerra só o cliente adb local (o efeito segue no aparelho —
o próprio `adb.py` já dizia isso) e `drain` prova só o fim da thread local; a forense viu 3 s de recuperação parcial
logo depois do timeout. Estouro de prazo numa operação com efeito (preparo, `sync_clock`) deixa a TENTATIVA não
pronta. Já erro RÁPIDO: `Adb.shell` levanta `AdbError` para qualquer saída não-zero, `device offline` inclusive —
"erro rápido = benigno" não se sustenta; depois de uma prontidão positiva, ele manda observar de novo. Limite: isso
vale DENTRO da tentativa. Entre tentativas no mesmo guest, um efeito tardio do timeout ainda pode cair depois; os não
idempotentes são o `input tap` do diálogo e o `cmd alarm set-time` (tarefa separada: tirá-los do caminho de
prontidão antes de pensar em quarentena).

**Aplicabilidade.** Vigente.

**Fonte.** Forense de 26/09/2026 (agente.log 19:59:41 local; banco: wake `c-20260925225852-97c2e4`).

