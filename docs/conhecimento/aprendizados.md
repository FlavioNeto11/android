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

**Aplicabilidade.** **Corrigido no código em 25/09/2026, ainda não implantado** (backlog B4). O `hello` do agente
declara a maior cerca por aparelho (`Hello.fences`, lida do diário), e o central sobe a cerca de um comando ainda
`created` para acima dela antes do despacho (`CommandStore.elevar_cerca`, chamada em `api._do_action_no_worker`,
com um aviso "cerca … subiu de … para … (banco restaurado?)" no log). A correção só vale com as **duas pontas**
atualizadas: com o agente antigo o sintoma continua, e o conserto manual acima segue sendo o recurso. Prova:
`simulated` (`backend/tests/test_cerca_restaurada.py`).

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

**Aplicabilidade.** Vigente — desde o ADR-052 a leitura mora no motor genérico,
`backend/app/integrations/app_declarado/sessao.py::ler_conta`, que toca a aba de perfil declarada e só lê na tela
de perfil declarada (bloco `conta` do `sessao.yaml` do app: `aba`, `tela_de_perfil`); o comentário do bloco no
`sessao.yaml` do Instagram registra este caso.

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
idempotentes são o `input tap` do diálogo e o `cmd alarm set-time` (saíram do caminho de prontidão: K-031).

**Aplicabilidade.** Vigente.

**Fonte.** Forense de 26/09/2026 (agente.log 19:59:41 local; banco: wake `c-20260925225852-97c2e4`).

### K-029 — SQLite aceitou texto numa coluna INTEGER; só o CI de PostgreSQL acusou

**Data:** 26/09/2026 · **Área:** testes (backend), banco

**Sintoma.** O job agendado "backend · pytest (PostgreSQL)" falhou todo dia desde 23/09 com uma única falha,
`test_estimativa_de_custo_por_fluxo`: `invalid input syntax for type integer: "fast"`. Em SQLite, o mesmo teste passava.

**Causa.** O teste inseria `ai_calls` à mão com `tier='fast'`. A coluna é `INTEGER NOT NULL` (0 = modelo da
função; 1 = escalonado, migração 003). Pela afinidade de tipo, o SQLite guarda o texto sem reclamar, e o
PostgreSQL recusa. O código de produção (`Repository.add_usage`) grava o inteiro certo; o erro estava só na fixture.

**O que funcionou.** Corrigir a fixture para o valor que a produção grava (`0`) e rodar o arquivo com
`TEST_DATABASE_URL` antes do commit. O job de PostgreSQL só roda agendado, por isso a falha não apareceu no PR.

**Aplicabilidade.** Vigente. Vale para todo `INSERT` escrito à mão em teste: use os tipos da migração, não um rótulo.

**Fonte.** CI agendado de 23 a 26/09/2026 (runs 35822429319 … 36220758764).

**Recorrência (27/09).** `tests/test_habilidades_migracoes.py` (fase D da evolução arquitetural) repetiu o
`tier='action'`, escrito por um subagente que não tinha lido este registro. O CI com PostgreSQL disparado à mão
(run 36322975308) acusou antes do deploy; a correção é `793fe00`. Passar este K-029 no pacote de todo subagente que
escreva `INSERT` à mão em teste.

### K-030 — `ORDER BY` em texto segue a colação: o PostgreSQL do CI não ordena como o `sorted()`

**Data:** 26/09/2026 · **Área:** banco, testes, cofre

**Sintoma.** `test_secret_store.py::test_rekey_recifra_o_cofre_inteiro_para_a_chave_nova` falhou uma vez no job
PostgreSQL do CI (run 36256295444, commit `d4b5e21`, que nem toca o cofre): `['sec-cXUJ…'] == ['sec-VwRn…']`. Nas
outras sete corridas PostgreSQL desde 25/09 ele passou; no SQLite, sempre.

**Causa.** Não era falta de `ORDER BY`: `rekey.recifrar` já fazia `SELECT ref FROM secrets ORDER BY ref`. Mas num
`TEXT` o `ORDER BY` segue a colação do banco. O `postgres:17` do CI (Debian, `en_US.utf8`) compara sem caixa na
primeira passada e ignora `-`/`_`: `sec-c…` antes de `sec-V…`. O SQLite (`BINARY`) e o `sorted()` do Python ordenam
por ponto de código: `V` (0x56) antes de `c` (0x63). Como a ref é `sec-{token_urlsafe(16)}`, com maiúsculas,
minúsculas, `-` e `_`, a corrida passava ou não conforme o sorteio.

**O que funcionou.** Ordenar em Python a lista que é contrato (`refs = sorted(...)`, sem `ORDER BY` no SQL), como
`SecretStore.chaves_estranhas` já fazia. As três listas do relatório saem dessa iteração. O teste diferencial
(`test_rekey_relata_na_mesma_ordem_seja_qual_for_a_colacao_do_banco`) imita a colação no SQLite devolvendo as
linhas de `secrets` em ordem `casefold`: falhou antes da correção nas três listas e passa depois. `COLLATE "C"` não
serve: o SQLite não conhece essa colação.

**O que não serve.** Reproduzir no SQLite sem imitação: lá o `ORDER BY` coincide com o `sorted()` por construção
(`BINARY`), e o teste passaria antes e depois da correção. Não medido: o `postgres:17-alpine` sugerido para a
corrida local usa musl, cuja colação tende a ser por byte; se for, a corrida local também não denuncia a diferença.

**Aplicabilidade.** Vigente.

**Fonte.** CI run 36256295444 (job `backend · pytest (PostgreSQL)`); `docs/banco.md`, parágrafo sobre `ORDER BY`.

**Reincidência (26/09, evolução de desempenho):** `test_hub_de_ia.py` lia `events` sem `ORDER BY` e usava o
índice `[0]`. O CI com PostgreSQL (run 36283068748) trouxe a escalada de outra etapa primeiro. Corrigido em
`3501934` com `ORDER BY id`, também em outra consulta com índice e no helper do desbravador. Toda consulta de teste
que usa posição precisa de `ORDER BY`.

### K-031 — Efeito tardio não idempotente no portão de prontidão: o toque no diálogo e o `set-time` do relógio

**Data:** 26/09/2026 · **Área:** parque, prontidão, relógio do convidado, worker

**Sintoma.** Depois do PR #7, dois riscos e um defeito na mesma raiz. Um `AdbTimeout` mata só o cliente adb local; a
transação binder já entregue a um `system_server` congelado executa quando ele destrava. Dentro do portão havia dois
efeitos que, aplicados atrasados, fazem mal: o `input tap` do `dismiss_system_dialog` (cai na tela que estiver aberta
naquela hora) e o `cmd alarm set-time <instante absoluto>` do `sync_clock` (ATRASA o convidado pelo tempo em que
ficou preso). E o defeito: com o relógio no portão, um estouro no fallback do `sync_clock` (`adb root` →
`wait-for-device` de 20 s) fazia o wake local devolver `False` e `_boot` descartar o snapshot de um aparelho bom.

**Causa.** O contrato temporal do PR #7 vale DENTRO da tentativa. Entre tentativas no mesmo guest, o que resta de um
timeout pode cair depois de uma tentativa seguinte ter declarado o aparelho pronto — e só é inofensivo se for
idempotente. O resto do preparo é (`settings put` com constante, `svc power stayon`, `wm dismiss-keyguard`).

**O que funcionou.** Tirar os dois do portão, em vez de quarentenar o guest:

- o preparo não toca mais na tela. O diálogo que já estava lá é dispensado depois da prontidão, antes da sessão de
  automação (um `uiautomator dump` com o UiAutomator2 aberto derruba a sessão), e a confirmação do MESMO diálogo vai
  na mesma chamada do `adb shell` do toque (`dumpsys window | grep -qF '<descrição>}' && input tap`). Isso estreita a
  janela, não a elimina: uma injeção já entregue ao `system_server` ainda pode cair atrasada, agora fora do portão;
- o relógio virou condição própria do central (`conferir_relogio_do_convidado`): medir (só leitura, `date` do kernel) → acertar
  (`cmd alarm set-time`) → conferir, na entrada no ar e a cada 5 min (1 min depois de estouro ou de não convergir).
  A reconferência é o que desfaz um `set-time` que caiu atrasado. Não converge → aviso "Relógio do aparelho…" no
  cartão, sem mexer em `readiness`;
- a medida do desvio desconta a ida e volta do adb (o meio dela): um relógio certo com 6 s de adb lia −3 s e
  disparava um `set-time` à toa.

**O que não funcionou.** O `adb root` → `wait-for-device` → `date MMDDhhmm` como fallback fora do portão: com o
aparelho no ar ele reinicia o `adbd` (derruba túnel, Appium e captura) e usava a hora LOCAL do host no fuso do
convidado. Saiu; o `set-time` sozinho foi o que se mediu funcionando (−26/−31 s → −1/−2 s,
`relatorio-validacao.md` §7.3).

**Aplicabilidade.** Vigente. Prova `simulated` (`backend/tests/test_prontidao_sem_efeito_atrasado.py`); a real
(cold start e stop do android-09 com o relógio conferido) fica para depois do deploy.

**Fonte.** Revisão do PR #7 e a revisão pós-merge (26/09/2026); PR `claude/prontidao-sem-efeito-atrasado`.

### K-032 — Relógio de "uma tentativa por dia" contado em `commands`: a entrega sem tarefa não abre comando

**Data:** 26/09/2026 · **Área:** apps, loja, entrega de versão

**Sintoma.** Achado na revisão do ADR-026, antes de chegar à produção. A nova tentativa diária de uma entrega que
falhou (`AppState.aplicar_versao_promovida`) contava o "um dia depois" pelo último comando de app do aparelho
(`MAX(created_at) FROM commands WHERE verb LIKE 'app.%'`). Com a convergência para a promovida rodando na varredura
de 60 s, um aparelho com entrega falha e um comando de app de três dias atrás seria rearmado a cada passada, e a
mesma falha se repetiria a cada minuto.

**Causa.** Só quem passa por `_despachar_trabalho` (Distribuir, canário, volta) abre comando. A entrega do "entrou no
ar" e a da varredura rodam por `scheduler.run_device_job`, que não abre comando nenhum. O relógio não via essas
tentativas.

**O que funcionou.** Contar a última tentativa pelo maior entre o comando de app e a última prova de instalação
daquele pacote no aparelho (`app_release_validations`, `stage='install'`), que `install_on` grava a cada tentativa
que chega ao `adb install`. `_entregar` grava a prova da falha que acontece antes disso: perfil, compatibilidade lida
do aparelho, arquivo do catálogo. A comparação de instantes é feita em Python, na resolução do banco (ms).
`test_sempre_na_promovida.py::test_entrega_que_falhou_na_varredura_nao_se_repete_a_cada_passada` prova as duas
metades: três passadas sem tentativa nova, e uma tentativa quando a última prova tem mais de um dia.

**O que não serve.** Uma coluna nova (`delivery_attempt_at`) resolveria também, mas pediria migração e janela de
deploy (ADR-020) para um dado que a tabela de provas já tem.

**Aplicabilidade.** Vigente. Vale para qualquer "no máximo uma vez por X" que rode fora da fila de comandos: o
relógio tem de ver a tentativa pelo caminho que a executa, não por um registro que só um dos caminhos grava.

**Fonte.** ADR-026; PR `claude/sempre-na-versao-promovida`.

### K-033 — Num `git worktree`, `.git` é arquivo: a versão saía "desconhecido" e três testes reprovavam

**Data:** 26/09/2026 · **Área:** versão do código, testes, instalador do agente

**Sintoma.** A evolução de desempenho rodou várias frentes em `git worktree`. Nos worktrees, três testes reprovavam
sempre:

- `test_backup::test_commit_em_execucao_sai_do_git_sem_chamar_git`;
- `test_workers`, na parte de versão;
- `test_instalacao_do_worker::test_o_instalador_windows_grava_a_versao_derivada_do_commit`.

A versão lida era `0.1.0+desconhecido`. No checkout principal e no CI, os três passavam.

**Causa.** `version.py::commit_em_execucao` lia `.git/HEAD` como pasta. Num worktree, `.git` é um arquivo com
`gitdir: <caminho>`: o HEAD fica na pasta do worktree e as refs na pasta comum, apontada pelo arquivo `commondir`. O
instalador do agente (`worker-install.ps1`) caía no mesmo problema por outro caminho: sem `backend/.venv` no
worktree, ele pulava o Python e lia `.git\HEAD` direto.

**O que funcionou.**

- `version.py::_pastas_do_git` resolve `gitdir:` e `commondir`, e procura a ref solta nas duas pastas e a
  empacotada na comum (`7b7a641`).
- Para o instalador, o worktree de integração ganhou `backend/.venv` como junção para o venv do checkout principal:
  `New-Item -ItemType Junction`, ignorada pelo `.gitignore`.

**O que não serve.** Chamar `git rev-parse` no `version.py`: o módulo evita subprocesso de propósito, porque o `git`
pode não estar no PATH da conta do serviço.

**Aplicabilidade.** Vigente. Antes de rodar a suíte num worktree, crie as junções de `backend/.venv` e
`frontend/node_modules`. Sem isso, falhas de versão, de supervisão e de instalador são do ambiente, não do código.

**Perigo ao limpar.** A junção aponta para o venv e o `node_modules` do checkout principal, que é o de produção.
Apagar recursivamente o worktree segue a junção e apaga o venv de produção. Isso vale para `rm -rf`,
`Remove-Item -Recurse` e `git worktree remove --force` com a junção dentro. O certo é tirar primeiro só o link, com
`cmd /c rmdir "<worktree>\backend\.venv"` e `cmd /c rmdir "<worktree>\frontend\node_modules"` (o `rmdir` sem `/s` numa junção remove só o link), e depois rodar `git worktree remove`.

**Fonte.** Evolução de desempenho, 26/09 (frentes F5, F6 e F8); [`handoffs/evolucao-desempenho.md`](../handoffs/evolucao-desempenho.md).

### K-034 — O agente instalado só leva `worker/ workers/ devices/ security/`: módulo novo na raiz de `app/` quebra o worker

**Data:** 26/09/2026 · **Área:** worker, instalador do agente

**Sintoma.** Achado na F5 da evolução de desempenho, antes de chegar à produção. O primeiro desenho punha a leitura
de recursos efetivos (cgroup, PSI) em `backend/app/recursos.py`, e o agente a importaria.

**Causa.** `scripts/worker-install.ps1` (linhas 60-61) e `worker-install.sh` copiam para a máquina do worker só as
pastas `worker`, `workers`, `devices` e `security`, mais `__init__.py`, `config.py`, `util.py` e `version.py`. Um
módulo novo na raiz de `app/` passa em todos os testes do repositório e dá `ImportError` no agente instalado, onde a
árvore é parcial.

**O que funcionou.**

- O módulo foi para `backend/app/devices/recursos.py`, dentro de uma pasta que já é copiada.
- `metricas.py`, que o agente usa com import opcional, entrou na lista de arquivos dos dois instaladores
  (`7b7a641`).
- `test_instalacao_do_worker` confere a lista em modo simulado.

**Aplicabilidade.** Vigente, com outra forma desde 27/09. As três listas escritas à mão (os dois instaladores e o
aviso do `deploy.ps1`) viraram `backend/worker-manifest.txt`, e `tests/test_pacote_do_agente.py` reprova quando o
manifesto difere do fecho de import de `app.worker.*`. Import novo no agente é linha nova no manifesto.

**O mesmo erro já estava na produção, latente:** `devices/adb.py` importava `devices/conectividade.py`, que importa
`app.models`, e `models.py` não ia para o agente. O comando da sonda de rede foi para `devices/sonda_rede.py`, só
stdlib (fase B da evolução arquitetural, `a863e60`).

**Fonte.** Evolução de desempenho, 26/09 (F5); [`worker.md`](../worker.md).

### K-035 — Log do emulador lido com o processo vivo: a linha do snapshot ainda não estava no disco

**Data:** 27/09/2026 · **Área:** emuladores, `scripts/probe-image.ps1`

**Sintoma.** No piloto de renderer de 27/09, `loaded_from_snapshot` saiu `false` nos quatro braços, embora o uptime
do convidado tenha seguido do ponto salvo (147–248 s logo depois de acordar em ~4,4 s). A fase 0 de 17/09 já tinha
dado `false` em todas as linhas, inclusive nas que restauraram.

**Causa.** O script procurava `Successfully loaded snapshot` por regex no `.log.wake` enquanto o emulador ainda
estava no ar. O stdout do emulador redirecionado para arquivo só desce ao disco em blocos e na saída do processo. A
linha está nos quatro arquivos finais, e o mesmo regex a encontra depois que o emulador sai.

**O que funcionou.** Decidir pelo relógio do convidado, que não depende de o log ter descido. Num boot a frio o
kernel nasce depois do processo, então o `/proc/uptime` nunca passa do tempo de parede decorrido até a leitura.
Restaurado, ele continua do ponto salvo. O veredito é uptime > decorrido + 10 s, e uptime ilegível dá `null`. O
regex ficou registrado à parte, só como informação (`restored_by_log`).

**O que não funcionou.** Tratar a ausência da linha como "não carregou": com o processo vivo, ausência não é prova.

**Aplicabilidade.** Vigente em `scripts/probe-image.ps1` (`Test-SnapshotRestored`, conferido por
`scripts/tests/test_probe_image.py`). O backend também lê o log do emulador com o processo vivo
(`_snapshot_verdict` em `backend/app/devices/manager.py`), mas ali ausência de linha já é `None` ("o log ainda não
disse") e não vira veredito. Não foi medido se a linha chega a tempo na produção.

**Fonte.** `docs/desempenho/bancada/renderer-20260927-a90a6e1.jsonl` e os `.log.wake` do piloto;
[`relatorio-desempenho.md`](../relatorio-desempenho.md) §9.

**No backend (conferido em 27/09):** o problema não se repete. No central, o log do emulador é gravado com o processo no ar (`emulator-android-01.log` modificado durante a execução), e `_snapshot_verdict` já detectou recusa pelo log em produção em 17/09 e 24/09. Só o `probe-image.ps1`, que redireciona o stdout de outro jeito, lia antes de o texto chegar ao disco.

### K-036 — `"bash"` solto num subprocess do Windows roda o bash do WSL, não o do PATH

**Data:** 27/09/2026 · **Área:** testes dos instaladores, Windows

**Sintoma.** Na fase B da evolução arquitetural, os testes do `worker-install.sh` passaram a falhar de um jeito
estranho: o script não enxergava `C:/...` e uma pasta de trabalho do WSL apareceu dentro do worktree.

**Causa.** `subprocess.run(["bash", ...])` resolve o executável pelo `CreateProcess`, que procura em `System32`
ANTES do `PATH`. Com o WSL instalado, isso é o `System32\bash.exe`, que abre a distribuição Linux: outra
máquina, com outro sistema de arquivos. O `skipif(shutil.which("bash") is None)` conferia o bash do Git, que
não era o que rodava.

**O que funcionou.** Chamar o bash pelo caminho inteiro que o `shutil.which("bash")` devolve, o mesmo que o
`skipif` conferiu (`tests/test_instalacao_do_worker.py`, constante `BASH`).

**Aplicabilidade.** Vigente para todo teste ou script Python que chame `bash` no Windows. Não mexeu na
configuração do WSL.

**Fonte.** Fase B da evolução arquitetural, `6448a96`.

### K-037 — Receita gravava o username da tela sem a arroba como texto literal

**Data:** 27/09/2026 · **Área:** receitas (`taskqueue/recipes.py`), Instagram

**Sintoma.** Na fatia da fase G (`abra a conversa com @ana no instagram`), a receita de `OPEN_THREAD` aprendida para
`@ana`, reproduzida para `@bia`, abria a conversa da ana. A verificação recusava, como deve, mas a tentativa se perdia
e a receita caminhava para a quarentena. O teste da trilha do fluxo legado precisou rodar com `ai.recipes = off` para
não tropeçar nisso.

**Causa.** O parâmetro traz a arroba (`@ana`), e o Instagram mostra o nome sem ela (`ana`) na linha da caixa de
mensagens e no cabeçalho da conversa. `detemplate` procura o **valor** do parâmetro no texto do seletor; como `@ana`
não aparece em `ana`, o texto não virava `{username}`, e `_usable_text` o aceitava como literal curto.

**O que funcionou.** Em `_usable_text`, o texto **inteiro** igual ao valor de um parâmetro sem a arroba (três
caracteres ou mais) vira `{parâmetro}`. Na reprodução, `_match` compara com as duas grafias (`_formas`: `@bia` também
casa `bia`), a mesma regra das provas locais (`proofs.variantes_de_arroba`). Pedaço de nome ("Mariana" para `@ana`)
nunca vira parâmetro.

**O que não funcionou.** Confiar só em `detemplate`: ele acha o valor como aparece no comando, e a tela reescreve o
valor.

**Aplicabilidade.** Vigente desde `eb9ba02`, para receita aprendida depois dele. Uma receita já gravada com o nome
literal continua literal. A mesma diferença de grafia vale para qualquer parâmetro que a tela mostre de outro jeito
(com ou sem arroba, maiúsculas, acento): quem grava seletor a partir de valor de parâmetro precisa normalizar as duas
pontas.

**Fonte.** `eb9ba02`;
`backend/tests/test_recipes.py::test_seletor_com_username_sem_arroba_vira_parametro_e_reproduz_para_outra_pessoa`;
[ADR-036](../decisoes.md#adr-036--receitas-como-estratégia-de-execução).

### K-038 — PostgreSQL do CI caía por segfault no fim da suíte: catálogo acumulado de um schema por teste

**Data:** 27/09/2026 · **Área:** testes (backend), banco

**Sintoma.** O job "backend · pytest (PostgreSQL)" falhou duas vezes seguidas (run 36356203609 e a repetição), sempre
por volta dos 20 min. O log do contêiner mostrava `server process ... was terminated by signal 11: Segmentation
fault` executando um `CREATE TABLE` das migrações 042–046, e todo teste seguinte recebia "the database system is in
recovery mode". Em SQLite a suíte passava.

**Causa.** Cada teste cria um schema próprio e aplica todas as migrações nele. Os schemas só eram apagados no
`pytest_sessionfinish` (achado #163). Com ~2.400 testes e as tabelas, índices e funções de gatilho novos da 042–046,
o catálogo acumulado passou do que o servidor do contêiner aguentava. A corrida de `793fe00`, com menos testes, ainda
cabia.

**O que funcionou.** Um fixture automático apaga o schema de cada teste ao fim DELE (`tests/conftest.py`,
`_schemas_do_teste_somem_ao_fim_dele`, `1d35442`). O catálogo fica do tamanho de um teste. CI completo verde depois
(run 36359168554).

**Aplicabilidade.** Vigente. Fixture de escopo de módulo ou sessão que crie banco precisa sair desta faxina.

**Fonte.** Runs 36356203609 (duas tentativas) e 36359168554.

### K-039 — Appium órfão sobrevive ao deploy e o backend novo sobe `degraded` (credencial bloqueada)

**Data:** 27/09/2026 · **Área:** operação, `deploy.ps1`, `stop.ps1`, `supervisor.py`, `automation/appium_server.py`

**Sintoma.** Depois do deploy de `524471d`, `/api/health` ficou `degraded` com `appium_log_masking_off`
("Mascaramento de log do Appium não comprovado nesta sessão"), e o preenchimento de credencial ficou bloqueado. Os
dois deploys anteriores do dia subiram `ok`.

**Causa.** O `stop.ps1` só encerra "o Appium que ele subiu" (pelo PID registrado); o processo `node …appium` de
19:51, iniciado pelo backend anterior, não estava no registro e ficou vivo. O backend novo o encontrou na porta,
tratou como "servidor externo já em execução" (`automation/appium_server.py:82-89`) e não conseguiu provar o
mascaramento, que só é comprovado quando ele mesmo sobe o Appium com os filtros.

**O que funcionou.** `scripts/stop.ps1`, matar o `node` do Appium que sobrou e `Start-ScheduledTask farm-central`:
o backend subiu o Appium com as regras e a saúde voltou a `ok` sem problema.

**Recorrência (28/09).** Mais dois deploys (`58bfd13` e `a71e809`) subiram `degraded` do mesmo jeito, um deles com
`appium_down` e o detalhe "readotado: iniciado por este projeto (pid N)": o órfão readotado não respondia ao
`/status`. Nas três vezes a correção foi a manual acima (`Get-NetTCPConnection -LocalPort 4723 -State Listen` →
`OwningProcess`, conferir que é `node.exe`, `Stop-Process`).

**Correção no script (28/09).** O `stop.ps1` agora faz a correção manual sozinho, e o `deploy.ps1` a herda porque
para pelo `stop.ps1`. Com o backend da Farm já sem responder (com ele no ar, o Appium é dele), ele encerra o Appium
DESTE projeto que ficou na porta (`scripts/lib/appium-do-projeto.ps1`). Tem duas travas:

- a porta sai do bloco `appium:` do `config/config.yaml`, com os padrões de `AppiumCfg` quando falta;
- o processo tem de ser `node.exe` com a linha de comando em `<appium.dir>\node_modules\appium` desta árvore, o
  critério do `_own_orphan` do backend.

Qualquer outro processo na porta fica, com aviso; linha de comando ilegível (shell sem elevação) também fica. Antes
de encerrar, espera 10 s para um backend que ainda está saindo desligar o próprio Appium. Depois confere que a
porta ficou livre dele e apaga o `data\appium.pid` que apontava para o processo encerrado. `stop.ps1 -Simular` mostra
o que seria encerrado, sem encerrar nada. Com a pasta vazia, a seleção casava com qualquer `node` (achado nos
testes: `Join-Path` com um drive inexistente devolve vazio); agora isso é erro.

**Fora do deploy: correção no backend (28/09).** Sobrava o backend que morre sozinho (crash, Windows Update). O
`Supervisor.ciclo` só sobe outro, e sem pai vivo o `_matar_filhos` não alcança o Appium que o morto subiu; o backend
seguinte o readotava pelo `data/appium.pid` e, sem prova de mascaramento, subia `degraded`. Havia duas saídas: o
supervisor encerrar o órfão antes de subir, ou o `AppiumServer.start` trocá-lo. Ficou a segunda
(`_reuse_running` e `_kill_orphan`). Ela cobre todo caminho até a subida: supervisor, `start.ps1` e deploy cujo
`stop.ps1` não leu a linha de comando. Também mantém o critério e as regras num lugar só e deixa o supervisor sem
`Config` nem `psutil`. É segura por construção: `main()` liga a porta da Farm antes do lifespan, então nenhum outro
backend desta árvore está vivo usando aquele Appium. As regras:

- órfão desta árvore (PID gravado vivo, linha de comando em `<appium.dir>/node_modules/appium`) COM mascaramento
  comprovado continua readotado, sem reinício;
- SEM prova, é encerrado com os filhos e o backend sobe outro com as regras; ficam o filho emulador
  (`supervisor.e_emulador`, o mesmo critério da varredura do supervisor) e o de nome ilegível;
- antes do tiro, precisa haver Appium instalado para subir outro, e a linha de comando é conferida de novo no mesmo
  processo, porque o PID pode ter sido reciclado. Se uma trava falha, ou falta permissão para encerrar, o órfão fica
  readotado `degraded`, e o detalhe do Appium diz "não foi trocado: <motivo>";
- qualquer outro servidor na porta (outra árvore, outro programa, PID gravado que não confere) continua reutilizado
  e nunca é encerrado.

Não cobre o órfão readotado COM prova que depois para de responder (`appium_down`). Fora do deploy isso não foi visto,
e no deploy o `stop.ps1` o encerra antes.

**Aplicabilidade.** Vigente até a primeira implantação com as duas correções subir `ok` sem intervenção e, depois
dela, um backend encerrado à força (só o `python -m app.main`, filho do supervisor) voltar `ok` sozinho; aí, marcar
superado. Até lá, depois de todo deploy, conferir `problems` no health. Se voltar `appium_log_masking_off` ou
`appium_down` com "readotado", ler a saída do `stop.ps1` e o detalhe do Appium no health. Um aviso "linha de
comando ilegível" pede o deploy num shell elevado. Um aviso "não é o Appium de …" significa que a porta está com
outro programa. "Não foi trocado: <motivo>" diz por que o backend não trocou o órfão.

Prova do `stop.ps1`: `simulated`, em `scripts/tests/test_stop_appium_orfao.py` (seleção, leitura do config, `node`
de verdade encerrado e o de outra árvore poupado, carência, `stop.ps1 -Simular`). Prova do backend: `simulated`, com
`node` de verdade em `backend/tests/test_supervisao_do_central.py`. Ali, o backend morto e religado pelo supervisor
troca o órfão sem prova, o órfão com prova é readotado, o `node` de outra árvore fica, e o filho `emulator` fica vivo
enquanto o `adb` é encerrado. As travas, com `psutil` falso, estão em `test_saude_do_appium.py`. `not_run`: o
Windows (o teste com `Get-NetTCPConnection` de verdade se pula fora dele) e o central.

**Fonte.** Deploy de `524471d` em 27/09 (~22:24, horário local); deploys de `58bfd13` e `a71e809` em 28/09
([relatório de validação](../relatorio-validacao.md) §16).

### K-040 — GitHub Actions parou de iniciar jobs: limite de gasto da conta, não erro de código

**Data:** 27/09/2026 · **Área:** CI

**Sintoma.** A partir de `5541d37`, TODOS os jobs do CI (inclusive `docs` e `frontend`) terminaram `failure` em 1–2 s,
sem nenhum passo executado. `gh run view` mostrava só X em cada job.

**Causa.** A anotação do job (`gh api repos/<r>/check-runs/<id>/annotations`) dizia: "The job was not started because
recent account payments have failed or your spending limit needs to be increased". O repositório é privado, e os
muitos CIs do dia (7 jobs × ~17 min, mais os `workflow_dispatch` com PostgreSQL de ~22 min) consumiram o limite.

**O que funcionou.** Reconhecer o padrão antes de procurar defeito no código: falha simultânea de todos os jobs em
segundos, sem passos. A prova em PostgreSQL passou a `not_run` até o dono ajustar *Settings → Billing & plans*; a
suíte local em SQLite (o dialeto da produção) seguiu como portão.

**Aplicabilidade.** Vigente enquanto o limite não for ajustado. Ao voltar, disparar `gh workflow run ci.yml --ref main`
para cobrir as migrações 047–051 no PostgreSQL.

**Fonte.** Runs 36366126852, 36366144350, 36367764497, 36367772837, 36369484002.


**Atualização (28/09).** A página de uso da conta (*Settings → Billing and licensing → Usage*) mostrou "You've used
100% of your Actions budget": limite de gasto (US$ 10 cobrados em 26–27/09), não falha de pagamento; o ciclo fecha
no dia 30. Sem pagar, os jobs foram para um runner próprio na máquina central (`operacao.md` §5), que não consome
minutos; o PostgreSQL (contêiner de serviço) fica na GitHub e volta com a cota nova.
### K-041 — No Git Bash, `/` é a pasta de instalação do Git: `rm -f "$D"/*` com `$D` vazio apagou os arquivos dela

**Data:** 28/09/2026 · **Área:** ambiente, agentes

**Sintoma.** Depois de um aceite visual, `C:\Program Files\Git\` ficou sem nenhum arquivo solto (`git-bash.exe`,
`git-cmd.exe`, licença, notas de versão, desinstalador `unins000.*`). `git` e `bash` continuaram funcionando, porque
`bin`, `cmd`, `usr` e `mingw64` ficaram intactas. No mesmo dia, um worktree ganhou uma pasta chamada `C` + U+F03A
(o `:` trocado por um caractere privado) cheia de temporários do pytest.

**Causa.** Um agente rodou `D=$(…) && … ; rm -f "$D"/*`; o passo antes do `&&` falhou, `$D` ficou vazio e o `rm`
virou `rm -f /*`. No Git Bash (MSYS), `/` é a raiz da instalação do Git, não `C:\`. A pasta estranha tinha a mesma
família de causa: `test_instalacao_do_worker` chamava o `bash.exe` do WSL, que lia `C:/…` como caminho relativo.

**O que funcionou.** Nenhuma cópia de sombra existia; o reparo é o instalador da MESMA versão, que estava em
`Downloads` (decisão do dono, porque mexe em software do sistema). A pasta estranha só se apaga pelo PowerShell com
`-LiteralPath` (no Git Bash o nome aparece como `C:` e aponta para a raiz do disco). O teste passou a usar o bash do
Git. **Regra para agentes:** nunca `rm` com variável ou curinga; só caminho literal, absoluto e dentro do worktree
ou do scratchpad, conferido com `ls` antes.

### K-042 — O esquema do rascunho de persona não cabe na saída estruturada da Anthropic (uniões demais, depois gramática grande demais)

**Data:** 28/09/2026 · **Área:** IA, geração de persona

**Sintoma.** Depois do deploy de `35b3e8f`, a primeira geração de persona real (`POST /api/personas/generate`) voltou
503 `ai_error` em 0,8 s: "Schemas contains too many parameters with union types (35 … limit: 16)". Corrigido isso
(`be65bd4`: `string | null` virou `string` só no esquema enviado, 35 → 2 uniões), a segunda voltou em 1,1 s com "The
compiled grammar is too large … Simplify your tool schemas". Os testes (`simulated`) passavam nas duas vezes, porque o
transporte falso não aplica os limites da gramática.

**Causa.** O `PersonaDraft` tem mais de 80 campos aninhados (voz, visual, biografia por seção). Como saída
estruturada, vira uma gramática compilada que a API recusa por tamanho, independentemente das uniões. A recusa é na
validação do pedido, antes de gerar (sem saída cobrada).

**O que funcionou.** Para esse esquema, sem gramática: o JSON Schema vai no TEXTO do pedido, como nas ferramentas não
estritas do ator (`automation/tools.py::tool_definitions`), e a garantia é o Pydantic na leitura
(`planning/provider.py::persona_draft_from_json`, que também tira cerca de código e devolve `""` a `None`). O
provedor OpenAI já fazia assim quando o servidor não tem `json_schema` (`_json_hint`).

**Aplicabilidade.** Vigente para todo esquema grande de saída estruturada: plano, verificação e resposta social cabem
(medido: 5, 1 e poucas uniões); rascunhos ricos não. Só se prova com o provedor real: o falso não recusa.

### K-043 — `preview_start` resolve o `cwd` do `.claude/launch.json` a partir do checkout principal, não do worktree

**Data:** 28/09/2026 · **Área:** painel, aceite visual

**Sintoma.** No worktree do cabeçalho, `preview_start painel-evo2` (`cwd: frontend`) subiu o Vite de
`C:\git\android\frontend`, a produção, e as primeiras capturas mostravam o cabeçalho ANTIGO, sem erro nenhum.

**Causa.** O navegador embutido lê o `launch.json` do projeto aberto na sessão (o checkout principal) e resolve o `cwd`
relativo a ele. O `launch.json` do worktree não é consultado.

**O que funcionou.** Uma entrada com `cwd` absoluto do worktree, acrescentada só para o `preview_start` e desfeita logo
depois (`git checkout -- .claude/launch.json`), para não deixar o checkout de produção sujo. O backend simulado do
worktree precisa ter a origem do Vite (`http://127.0.0.1:5188`) em `server.allowed_origins`; sem ela o login volta
à tela de nome sem mensagem. Nas capturas por CDP, conferir se a sessão já está logada antes de digitar: o `focus()`
no primeiro `input` pega a caixa de seleção do android-01 e rola a página.

**Aplicabilidade.** Todo aceite visual feito de worktree. Confira na captura algo que só existe no código novo.
A porta 8765 pode já estar com o backend simulado de OUTRA sessão (28/09: a de crenças): confira o dono do processo
antes de parar, e use outra porta (8766 + `VITE_API_TARGET`, com a origem do Vite em `allowed_origins`).

### K-044 — Módulo novo em `app/planning/` entra no ciclo legado de imports; auto-início com trava em ref morre no StrictMode

**Data:** 28/09/2026 · **Área:** arquitetura do backend, painel

**Sintoma.** O assistente do comando (ADR-047) nasceu em `app/planning/refine.py`, importando `provider` e `prompts`,
com os três provedores importando-o dentro do método (como o `training.generalize`). Os testes do assistente
passavam; a suíte inteira reprovou em `tests/test_arquitetura.py`: o ciclo legado de `planning` "cresceu" com o
módulo novo, e os imports tardios subiriam acima do teto da catraca (`IMPORTS_TARDIOS["app.planning"]`). No painel,
o `AssistenteDoComando` com `autoIniciar` abria vazio em `npm run dev`, e o vitest passava.

**Causa.** (1) Qualquer módulo que importe algo do ciclo e seja importado por algo do ciclo passa a fazer parte dele,
e a catraca só deixa o ciclo encolher. O padrão que já funcionava é o do `persona_generation`: o prompt e o esquema no
domínio (`app/modules/<x>/domain`), que não vê `app.planning`, e os provedores importando dele NO TOPO. (2) Em
desenvolvimento o React monta, desmonta e monta de novo: a limpeza abortava o primeiro pedido e a trava `iniciou`
num `useRef` impedia o segundo.

**O que funcionou.** `modules/execution/domain/command_refinement.py` com as regras de prompt recebidas por parâmetro
(`refine_system(UNTRUSTED_RULE, CONDUCT_RULE)`), erro de parse próprio (`RefinamentoInvalido`, convertido em
`AIError` pelo provedor), apps como `AppResumo` e a redação aplicada no serviço (`normalizar(…, redact)`), sem `Any`
nas assinaturas (há catraca para `Any` também). No painel, o efeito de auto-início sem trava: dispara e aborta na
limpeza.

**Aplicabilidade.** Vigente para todo papel de IA novo: rode `tests/test_arquitetura.py` junto dos testes da
funcionalidade, não só no fim. Para efeito que dispara pedido na montagem, teste no `npm run dev`, não só no vitest.

### K-045 — O `eval_run.py` morre num `RemoteProtocolError` transitório e deixa a execução em curso órfã

**Data:** 28/09/2026 · **Área:** avaliação (Fase 17)

**Sintoma.** No braço `fase17-luna-ator-sonnet` a bateria parou depois de 5 casos com `httpx.RemoteProtocolError:
Server disconnected without sending a response`. O backend não caiu: tinha subido às 12:06 e seguia registrando
chamadas. A execução do 6º caso (`r-20260928151200-39a988`) terminou sozinha, e o custo dela entrou em `ai_calls`, mas
o caso não apareceu em `data/eval-results.jsonl`.

**Causa.** O cliente HTTP do `eval_run.py` não trata a queda transitória da conexão reaproveitada, então uma exceção
de transporte encerra a bateria inteira.

**O que funcionou.** Rodar os casos que faltavam à parte (`-Cases`), com o mesmo rótulo, e contar a execução órfã
como fora da medição.

**Aplicabilidade.** Corrigido no item 17.11 (02/10/2026): o `eval_run.py` usa `Resistente`, que repete a chamada (4 tentativas, espera crescente) em `RemoteProtocolError`, `ReadError`, `WriteError`, `ConnectError` e `ReadTimeout`; o POST de `/api/runs` repete com a mesma `idempotency_key`, e o laço de espera da execução tolera uma leitura que esgote as tentativas (a execução está viva) até o prazo, em vez de largá-la órfã. Resposta HTTP de erro não é repetida. `simulated`: `scripts/tests/test_eval_run.py`. Em bateria longa, ainda vale conferir o total de casos em `eval-results.jsonl` antes de ler o placar.

### K-046 — Voltar o `config.yaml` depois de testar um modelo novo deixa as chamadas dele sem preço, e o teto do dia infla

**Data:** 28/09/2026 · **Área:** IA, custo (Fase 17)

**Sintoma.** Depois da bateria, com o `config.yaml` original restaurado, a saúde acusou `ai_budget_day_warning`:
"US$ 8,06 de US$ 10,00 (81% do teto)". O gasto real do dia era de ~US$ 4,9.

**Causa.** As 275 chamadas ao `gpt-6-luna` da bateria ficaram em `ai_calls`. O original não declarava o preço dele, e
modelo sem preço conta pela tarifa mais cara da tabela (Opus), por regra (`planning/costs.py`). Mais um braço teria
travado a IA de todas as sessões até a meia-noite UTC.

**O que funcionou.** Declarar o preço do modelo em `ai.prices` mesmo sem papel nenhum apontando para ele: o gasto do
dia voltou a US$ 4,69.

**Aplicabilidade.** Vigente. Ao restaurar a configuração depois de qualquer teste com modelo novo, mantenha a linha de
preço dele. A regra do preço mais caro fica como está: ela existe para um fallback não cadastrado não sair de graça.

### K-047 — O `dumpsys window` do Android 14 abre com a seção "WINDOW MANAGER LAST ANR": o primeiro `mCurrentFocus` é o congelado

**Data:** 28/09/2026 · **Área:** automação, adb (ADR-053)

**Sintoma.** Nas execuções `r-20260928165254-e31953` e `r-20260928195344-02ee9e` (android-06), `Adb.current_focus`
"comprovava" o Instagram em primeiro plano com o launcher na tela. A prova de app na frente era falsa, e a IA agia
sobre uma tela que não era a que o foco dizia.

**Causa.** Depois de um ANR, o Android 14 guarda uma fotografia do window manager naquele instante e a imprime NO
COMEÇO do `dumpsys window`, na seção "WINDOW MANAGER LAST ANR" (com cópia das "display contents" depois de "Last ANR
continued"). O foco de agora só aparece depois, na seção viva. O código lia a primeira ocorrência de
`mCurrentFocus=`. Delimitar a seção pelo cabeçalho seguinte não serve: a cópia congelada também tem cabeçalho de
"display contents".

**O que funcionou.** Ler a ÚLTIMA ocorrência da chave (`devices/adb.py::_ultima`) em `current_focus`,
`system_dialog`, `ui_ready` e na confirmação do toque no diálogo (`11007f9`, pacote "anr").
Prova `simulated`: `backend/tests/test_anr_sinal_proprio.py`.

**Aplicabilidade.** Vigente para toda leitura de `dumpsys window` (e de qualquer `dumpsys` com seção histórica): nunca
o primeiro casamento. Ao investigar à mão um foco estranho, procure "LAST ANR" na saída antes de confiar na linha.

### K-048 — `hide_error_dialogs=1` transforma ANR em morte silenciosa do app (reason=6), e o launcher volta

**Data:** 28/09/2026 · **Área:** automação, adb (ADR-053)

**Sintoma.** O Instagram "sumia" no meio da etapa e o launcher voltava, sem diálogo nenhum. A IA entendia que o app não
tinha aberto e o reabria; a partida a frio (28–51 s num convidado saturado) dava outro ANR, e o laço comia o prazo:
5 mortes do Instagram na `r-20260928195344-02ee9e` e 6 na `r-20260928165254-e31953`.

**Causa.** O preparo do aparelho (`Adb.prepare_for_automation`) grava `settings put global hide_error_dialogs 1`,
para diálogo de erro não travar a automação. Com isso, todo ANR do app em primeiro plano vira morte direta do processo
("user request after error", reason=6), e quem olha a tela vê só o launcher. O motivo fica registrado apenas em
`dumpsys activity exit-info <pacote>`. O `logcat -b events` não serve de fonte: o buffer roda e a linha some.

**O que funcionou.** Ler o `exit-info` (`Adb.app_deaths`, idade medida no relógio do convidado, na mesma chamada) e
contar as mortes pela ETAPA: uma reabertura determinística sem IA; na segunda morte, a etapa falha dizendo que o app
parou de responder (ANR) com o convidado sem CPU, e o aviso vai para o aparelho (`11007f9`). `hide_error_dialogs`
continua 1. Prova `simulated`: `backend/tests/test_anr_sinal_proprio.py`.

**Aplicabilidade.** Vigente enquanto o preparo gravar `hide_error_dialogs=1`; o experimento com 0 (diálogo visível)
fica para outra rodada. Feito em 28–29/09 (K-054): com 0, o ANR do `system_server` prende o aparelho; decisão: manter 1.
"O app sumiu e voltou o launcher" num convidado lento: consulte o `exit-info` antes de reabrir.

### K-049 — O 500 "root AccessibilityNodeInfo … hogging the main UI thread" é UI ocupada, não sessão morta

**Data:** 28/09/2026 · **Área:** automação, Appium (ADR-053)

**Sintoma.** O executor recriava a sessão do Appium (`DELETE` + `POST /session`) no meio da etapa: 5 recriações =
305,6 s de 925 s na `r-20260928195344-02ee9e`, 4 = 214 s na `r-20260928165254-e31953`. Cada recriação custava 27–80 s
num convidado já saturado e o deixava pior.

**Causa.** O UiAutomator2 responde 500 com "waiting for the root AccessibilityNodeInfo … hogging the main UI thread"
(ou "no active window") quando o app segura a thread de UI: o servidor está vivo e a sessão também. O código tratava
qualquer 500 como `DriverError` de sessão.

**O que funcionou.** `DriverBusy` (subclasse de `DriverError`) para esse 500 (`910f8d6`/`1a9c2ab`, pacote "driver").
A leitura relê até 3 vezes com recuo de 4 s dentro do prazo da etapa, sem recriar a sessão; a ação com UI ocupada
fica com efeito incerto e não é repetida pelo executor; só a sessão morta de verdade recria. Na prova real de 28/09
(`r-20260928234657-bbdf3c` e `r-20260928235215-6eb84c`, android-06), a janela do `appium.log` teve 0 `POST /session`,
0 `DELETE /session` e 0 linhas de UI ocupada. Prova `simulated`: `backend/tests/test_ui_ocupada.py`.

**Aplicabilidade.** Vigente. A pendência (erro de adb no screencap da observação ainda recriava a sessão) foi
resolvida em `6799867`: `FalhaDeLeitura` relê sem recriar (item 21.10).

### K-050 — Interrupção acumulada no convidado com dias no ar (irq 21–90% ocioso) derruba as tarefas; `restart` devolve ~2%

**Data:** 28/09/2026 · **Área:** parque, emuladores (ADR-053)

**Sintoma.** O android-06 saturava durante as tarefas (load 15–35 em 2 vCPU, 48–57% da CPU em irq) com RAM sobrando;
leitura de tela de 10–45 s, ANR em série e sessões recriadas.

**Causa.** Medido em 28/09 ~21:45 UTC (real, central, adb só leitura, 10 s de `/proc/stat` com o aparelho ocioso): a
fração de CPU em interrupção (irq+softirq) acompanha o tempo no ar. android-01: 0% com 7,7 h no ar; android-06: 21%
com 68 h; android-04: 90% com 44 h. A causa do acúmulo **não está provada**.

**O que funcionou.** `restart` (reinício a frio, NÃO `reset`) pela plataforma (`c-20260928214526-3354ad` e
`c-20260928214526-abbb42`): android-06 a 2,8% e android-04 a 2,0%; "Verificar conta" do andre no android-06 em 21 s
(`c-20260928215311-3e76c5`, `session_ready`). Automatizado em `93967d0`: a sonda de saúde mede a fração entre duas
sondas e, com o aparelho ocioso acima de 15% em 3 sondas seguidas, abre um `restart` rastreável
(`requested_by='system'`), no máximo 1 a cada 6 h por aparelho; nunca a escada de reparo, que chega a `reset` e
apagaria a conta real. Prova `simulated`: `backend/tests/test_saude_do_convidado.py`.

**Aplicabilidade.** Vigente. Aparelho lento sem falta de RAM: meça o irq ocioso antes de pedir mais memória. Se o
reinício de menos de 6 h não resolver, o aviso fica no cartão, e é outra doença. Desde `e9da86e` o reinício
sai com `requested_by='saude'` e não conta como degrau da escada de reparo (ADR-055); a causa do acúmulo foi
medida em 29/09 (K-060): dois terços são o app logado rodando, um terço o tempo no ar.

### K-051 — `farm-ci-runner` em "Ready" não quer dizer runner parado: o `Runner.Listener` segue no ar

**Data:** 28/09/2026 · **Área:** CI, operação

**Sintoma.** Para validar o `93967d0` no central sem o CI disputando CPU com o parque, rodou-se
`Stop-ScheduledTask farm-ci-runner`, e a tarefa apareceu "Ready". O CI do `93967d0` rodou assim mesmo, em prioridade
ociosa, durante as provas reais.

**Causa.** O processo que a tarefa lança (`run.cmd`) termina depois de deixar o `Runner.Listener` de pé. Parar a
tarefa não encerra o Listener, e o estado "Ready" só diz que a tarefa não está rodando.

**O que funcionou.** Conferir pelo lado da GitHub: `gh api repos/FlavioNeto11/android/actions/runners` (estado e
`busy` do runner `central`). Pausar de verdade = `Stop-ScheduledTask farm-ci-runner` e encerrar os processos
`Runner.Listener` e `Runner.Worker`.

**Aplicabilidade.** Vigente. Antes de afirmar "runner pausado" num relatório, confira pela API. As provas de 28/09 à
noite (ADR-053) rodaram com o CI em prioridade ociosa, não com o runner parado.

### K-052 — A política própria do perfil prevalece sobre o `approval_required` do catálogo: comentário sem aprovação

**Data:** 28/09/2026 · **Área:** perfis, política, processo (ADR-053)

**Sintoma.** Na prova com efeito `r-20260928235215-6eb84c` (andre, android-06), o comentário foi publicado sem passar
por aprovação humana no painel, embora o dono tivesse ouvido que o texto passaria por ela.

**Causa.** A política do perfil do andre tem `CREATE_COMMENT = autonomous` com origem `own` (o perfil mudou), e a
ordem das camadas é perfil → grupo → padrão do catálogo (`social/policy.py::policy_for`). O sistema seguiu a
configuração corretamente; a promessa é que foi feita sem conferir a política do perfil.

**O que funcionou.** Antes de prometer aprovação (ou de pedir autorização descrevendo-a), ler
`GET /api/instagram/profiles/<id>/policy` e conferir `capabilities.CREATE_COMMENT` e `origin` (`own`, `group` ou
`default`); `loosened` lista as ações com política mais frouxa que o padrão.

**Aplicabilidade.** Vigente para toda prova com efeito em conta real: o pedido de autorização ao dono diz a política
efetiva de cada ação, lida da API, e não a do catálogo.

### K-053 — Conta logada sem persona: `account_label` e `/personas` não dizem se há conta no aparelho; confira a tela

**Data:** 29/09/2026 · **Área:** parque, processo, Instagram (ADR-055)

**Sintoma.** Em 28/09, das 21:36 às 21:40 (-03:00), um experimento de `hide_error_dialogs` escolheu o android-04 como
"aparelho sem persona nem conta": `account_label` `qa-user-04` e `/personas` vazio. Mandou 10 entradas por adb (toques e
arrastos) e aberturas a frio sobre a tela "Confirm you're human" do felipe.nogueira93762026, que abriram "Get support" e
o assistente da Meta. Nada foi digitado nem enviado; a tela foi fechada com BACK.

**Causa.** O rótulo era o da configuração (`qa-user-04`, do QA Messenger), não derivado de conta nenhuma. O perfil
estava `blocked` pela declaração do dono (23/09; o bloqueio automático do ADR-029 nunca disparou) e o desvínculo de
27/09 03:10Z tinha tirado a persona do aparelho, mas a sessão do Instagram continuava no disco e na tela. Nada no
sistema dizia, por aparelho, que ali estava logada uma conta travada.

**O que funcionou.** O marcador por aparelho (`device_locked_accounts`, migração 054), carregado com o android-04;
`account_label` derivado do marcador ou do vínculo (`account_label_origin`); a quarentena (ADR-055). E a regra de
processo: antes de qualquer experimento, agente ou `adb input` num aparelho com Instagram, tirar um screencap e ler a
conta logada, sem tocar; aparelho de experimento é aparelho novo e sem conta (o android-17, só com o QA Messenger,
K-054).

**Aplicabilidade.** Vigente. `account_label`, `/personas` e os vínculos dizem o que o sistema acha; a tela diz o que
está lá.

### K-054 — `hide_error_dialogs=0` trava o aparelho no ANR do `system_server`: manter 1

**Data:** 28–29/09/2026 · **Área:** emuladores, adb (ADR-055; completa o K-048)

**Sintoma.** O K-048 deixou para outra rodada a pergunta: sem o diálogo (`hide_error_dialogs=1`), o ANR do app vira
morte silenciosa; com o diálogo (0), talvez o app sobrevivesse à saturação.

**Causa.** Medido (`real`, 28–29/09, central) num aparelho novo, o android-17, sem conta e só com o QA Messenger
(`qa-app/dist/qa-messenger.apk`), saturado pelo hospedeiro (processo do emulador com afinidade de 1 CPU):

- com 0, o QA não teve ANR em 3 janelas (a hipótese de o app sobreviver ficou sem teste), mas o `system_server` teve, e
  o diálogo "Process system isn't responding" NÃO sumiu sozinho: 325 s, e ~24 min no 1º boot. BACK não fecha, "Wait"
  volta em menos de 5 s; só reiniciar resolve. É uma tela do sistema que nenhuma automação dispensa sem tocar fora do
  app;
- com 1, um ANR do QA virou morte silenciosa, detectável pelo `exit-info` (K-048);
- afinidade 1 com prioridade Idle derrubou o `system_server` (`am_crash`, "failed to set system property").

**O que funcionou.** Manter `hide_error_dialogs=1`, que o preparo do aparelho continua gravando; o sinal de ANR vem do
`exit-info`. O android-17 foi aposentado (`retired_at` 00:29:05 de 29/09; o `DELETE` esbarrou no K-056).

**Aplicabilidade.** Vigente. Não rodar 0 em aparelho de uso. A pergunta "o app sobreviveria com o diálogo?" continua
aberta e não vale o risco.

### K-055 — NTP bloqueado com porta de origem 123: o `w32time` não sincroniza, o `stripchart` sim (`farm-relogio`)

**Data:** 28/09/2026 · **Área:** operação, host (ADR-019, ADR-055)

**Sintoma.** O relógio do central estava "Free-running", +6,2 s atrás do NTP.br (C12 do ADR-053). Configurar o `w32time`
não resolvia: evento 47, "No valid response ... after 8 attempts".

**Causa.** Os pedidos do `w32time` saem pela porta de ORIGEM 123, e a rede não devolve resposta. `w32tm /stripchart`,
que sai por porta efêmera, mede normalmente.

**O que funcionou.** `scripts/sincronizar-relogio.ps1` (`b25957e`): mede com o `stripchart` (`a`, `b` e `c.st1.ntp.br`,
3 amostras cada, mediana) e, acima de 0,2 s, ajusta com `Set-Date -Adjust`. Com `-Instalar`, registra a tarefa
`farm-relogio` (SYSTEM, a cada 15 min) e põe o `w32time` em `syncfromflags:NO`, para o relógio ter um dono só. Desvio de
+6,240 s para +0,004 s em 28/09 21:25 (-03:00); log em `data\logs\relogio.log`. Mexer no relógio exige autorização
(CLAUDE.md): dada pelo dono em 28/09 ("eu autorizo tudo").

**Aplicabilidade.** Vigente no central. Relógio errado de novo: olhe o `relogio.log` e a tarefa antes de mexer no
`w32time`; `-Simular` mede sem ajustar.

### K-056 — Aposentar no Windows: o emulador deixa arquivo somente-leitura no AVD, e o `rmtree` falha

**Data:** 29/09/2026 · **Área:** parque, provisionamento (ADR-045)

**Sintoma.** Ao aposentar o android-17, o `DELETE` falhou com `avd_nao_apagado`.

**Causa.** O emulador deixa `data/misc/pstore/pstore.bin` somente-leitura na pasta do AVD; no Windows, `shutil.rmtree`
falha nele com WinError 5.

**O que funcionou.** `devices/avd.py::_apagar_arvore` (`2511b12`): `rmtree` com `onexc` (no Python abaixo de 3.12,
`onerror`) que tira o somente-leitura e tenta de novo; `_liberar_escrita` também no `.ini`. Só biblioteca padrão, porque
o módulo vai ao agente do notebook. Prova `simulated`: `backend/tests/test_provisionamento.py`.

**Aplicabilidade.** Vigente. Outro `avd_nao_apagado` com o emulador parado: procure o atributo somente-leitura antes de
suspeitar de processo segurando o arquivo.

### K-057 — Frota coordenada sobre uma pessoa real precede os bloqueios: a resposta é conduta, não disfarce

**Data:** 29/09/2026 · **Área:** perfis, Instagram, política (ADR-055)

**Sintoma.** Cinco de oito contas do Instagram bloqueadas; em três delas o sistema nunca viu o desafio.

**Causa.** Medida, sem prova do motivo do Instagram. As oito contas nasceram no mesmo dia, no mesmo host, com a mesma
imagem e o mesmo `ro.serialno`, e agiram em três ondas coordenadas sobre as mesmas pessoas: o mesmo comentário de 4
contas no mesmo post (18/09); 8 follows na mesma pessoa privada em 19m43s (19/09); 7 DMs à mesma pessoa em 8m40s com o
recado "seu marido mandou um oi", com SEND_MESSAGE `autonomous` (19/09). Nada nos dados separa as vivas das bloqueadas
(DM 5 de 5 contra 2 de 3, Fisher p = 0,375).

**O que funcionou.** Conduta como regra de código: uma conta por alvo para seguir, DM e comentário, com o excedente
recusado; DM fria sempre com aprovação; persona que não atribui fala a terceiros; o disjuntor que pausa as contas do
mesmo alvo quando uma cai; os tetos e o espaçamento do grupo "Recuperação" para as vivas. Nunca disfarce (mascarar
emulador ou rede, proxy): é evasão, e é proibida.

**Aplicabilidade.** Vigente. Um comando que mande a mesma ação a várias contas do Instagram é, por padrão, coordenação:
uma conta por alvo, e nunca duas contas no mesmo alvo dentro da janela de 30 dias. Em parte substituído pelo ADR-056
(29/09, decisão do dono): a rede por aparelho, declarada e medida, deixou de ser proibida; rotação de IP, mascarar
emulador ou identidade e resolver desafio seguem proibidos, e a conduta acima continua inteira.

### K-058 — Carga da IDE no central vira "aparelho doente" e dispara a escada de reparo: um trabalho pesado por vez

**Data:** 29/09/2026 · **Área:** parque, operação, processo (ADR-055)

**Sintoma.** Em 29/09, às 02:05:31Z, a escada de reparo pediu `restart` do android-01 (2º degrau; "o `system_server`
caiu"). Às 02:10:14Z ele falhou ("o Android subiu, mas não ficou pronto em 60 s": o preparo estourou o prazo). Às
02:15:34Z veio o `reset` (3º degrau, `c-20260929021534-6d15cd`, `requested_by` `system`), que apagou o Instagram e a
sessão do lucas.almeida9484, uma das três contas vivas.

**Causa.** Assumida pela coincidência medida: a máquina central estava saturada pelo trabalho da própria IDE em
paralelo — a suíte inteira do backend, o Docker Desktop com os testes em PostgreSQL e o boot do android-17 do
experimento de ANR (K-054). Os convidados chegaram a load 40–57 em 2 vCPU (eventos das 01:56 às 02:00Z). A sonda de
saúde não distingue convidado doente de hospedeiro sem CPU: o Android "não fica pronto" nos dois casos, e a escada
sobe de degrau. Reiniciar é o momento mais pesado de um convidado, então cada degrau piorava a máquina. O backend
estava em `7a02491` desde 01:42Z (`data\logs\backend.log.2026-09-28`, em hora local), e ali o 3º degrau com conta
vinculada ainda era o `reset`.

**O que funcionou.**

- `c359f65` (no ar às 03:55Z): nunca `reset` automático em aparelho com conta vinculada ou travada (ADR-055, item 21.6).
- `9348e9c` (no ar desde 04:17Z): com a CPU da máquina em `instances.remediation_host_cpu_max` (90%) ou mais,
  o reparo de aparelho local espera 10 min, com o aviso "Reparo adiado" no cartão, em vez de subir de degrau. A suíte
  usa 101 (desliga). Item 21.16.
- Conduta no central: um trabalho pesado por vez (suíte inteira, suíte em PostgreSQL, boot de aparelho de experimento);
  processos de teste em prioridade ociosa (um vigia da sessão os põe assim); Docker Desktop e WSL só enquanto a suíte em
  PostgreSQL roda, desligados depois. Mexer no WSL continua exigindo autorização em chat (CLAUDE.md): isto não é
  permissão permanente.

**Aplicabilidade.** Vigente. O central é, ao mesmo tempo, a bancada da IDE e o hospedeiro do parque com contas reais.
Antes de trabalho pesado nele, olhe a CPU e os aparelhos ligados com conta. Aviso "Reparo adiado" no cartão quer dizer
máquina saturada, não aparelho doente. Reativar a conta do lucas é decisão do dono (ADR-055).

### K-059 — Apps do Google em segundo plano pesam nos convidados de 2 GB: desativar pelo preparo, lista configurável

**Data:** 29/09/2026 · **Área:** parque, emuladores (ADR-055; K-050)

**Sintoma.** No android-04, 2,7 h depois de um `restart`: load 14,7, 82 MB livres, 500 MB de swap zram e `kcompactd0`
com 43% de CPU, com YouTube, YouTube Music, Gmail e Bem-estar digital subindo sozinhos. No android-06: app Google
101 MB, GMS persistente 74 MB, Android System Intelligence 37 MB, Mensagens 22 MB.

**Causa.** A imagem `google_apis` traz apps do Google que sobem em segundo plano, mesmo sem uso. Num convidado de 2 GB
eles tomam cerca de 200 MB e empurram o kernel para a compactação de memória (`kcompactd0`), que disputa CPU com o app
alvo.

**O que funcionou.** `android.desativar_apps` (`e2b54a0` + `b5036ec`): o preparo do aparelho (`prepare_for_automation`,
no boot, no wake e na readoção) roda `pm disable-user --user 0` numa lista conservadora de 13 pacotes, com as regras:

- idempotente (uma leitura primeiro; na segunda passagem, nenhuma escrita);
- reversível: o marcador `/data/local/tmp/central-apps-desativados.txt` guarda o que o preparo desativou, e o que sai
  da lista volta com `pm enable`;
- a carga recusa os protegidos (Play Store, GMS, GSF, WebView, teclado, launcher, SystemUI, Chrome, `io.appium.*`) e o
  app alvo;
- o central é o único dono da lista, inclusive nos aparelhos dos workers, preparados pelo túnel;
- a loja e o celular físico ficam fora;
- por aparelho: `instances.overrides.<id>.desativar_apps`.

Um `AdbTimeout` nesse passo não derruba a prontidão (K-031): um `pm disable-user` atrasado só deixa um app da lista
ligado. Real, 29/09, depois do deploy de `f497075`:

- evento "apps de fundo — 11 desativado(s)" no android-01 (07:38:57Z) e no android-06 (07:39:03Z): o app Google, o
  Android System Intelligence, Mensagens, YouTube, YouTube Music, Gmail, Bem-estar digital, Fotos, Maps, Agenda e Drive;
- nenhum deles rodando depois;
- `MemAvailable` do android-06 foi a 974 MB (antes, 670–830 MB) e o do android-01 a 1054 MB (antes, 730–960 MB);
- o Instagram seguiu ok: "Verificar conta" do andre no android-06 às 07:41:12Z confirmou @andre.carvalho9543
  (`c-20260929074028-e124bc`).

**Aplicabilidade.** Vigente. Aparelho lento com pouca RAM livre: confira os apps em segundo plano antes de pedir mais
memória. A relação com o acúmulo de irq (K-050) NÃO está provada. A memória liberada foi medida, mas a coleta
`data\logs\irq_convidados.csv` mostra o irq ocioso do android-06, com o Instagram em primeiro plano, subindo de ~4%
(2,7 h no ar) para ~8% (6,4 h), enquanto o android-04 no launcher ficou em 2–3%. A causa foi medida depois (K-060). O
passo dos apps só vai ao log quando fica `incerto` sob carga (prazo de 12 s): no painel não aparece.

### K-060 — Irq do convidado ocioso: dois terços são o app logado rodando, um terço o tempo no ar; o custo no host não muda

**Data:** 29/09/2026 · **Área:** parque, emuladores (ADR-053; K-050, K-059)

**Sintoma.** Com o parque ocioso, o android-06 (andre logado no Instagram, 14,6 h no ar) tinha 8,4–8,7% da CPU em
irq. O android-01 (sem o Instagram desde o reset, 10,1 h no ar) tinha 1,6–2,3%. Na medida persistida de 24 h (21.13),
a mediana era 6,1% contra 2,4%.

**Causa.** Medida em 29/09 (real, central, adb só leitura salvo onde dito; `/proc/interrupts`, `/proc/softirqs`,
`/proc/stat`, `/proc/vmstat` e a CPU do processo do emulador no Windows):

- **Não é transmissão de tela.** Nenhuma conexão estabelecida nas portas encaminhadas do UiAutomator2 (MJPEG).
- **Não é o primeiro plano.** A tecla HOME às 12:27:12Z (tela conferida antes: o perfil do próprio andre) deu 5,8%
  em 1 min e 8,4% em 5 min.
- **Dois terços são o processo do Instagram logado, em qualquer plano.** O `am force-stop` às 12:40:59Z, com o
  aparelho ocioso, não desloga e a próxima execução reabre o app. Depois dele: 5,8% em 2 min e 4,6% em 6 min. O
  tempo de sistema caiu de 7,7% para 2,0%, igual ao do android-01, e as interrupções entre CPUs (CAL) de 128/s para
  43/s. Sozinho, com o aparelho parado, o app gastava 4,5% de uma vCPU.
- **Um terço é o tempo no ar.** Sem o app, sobram ~1,8 ponto acima do android-01, com o temporizador local 1,6 vez
  maior (192/s contra 122/s). No mesmo aparelho e no launcher, o irq foi de 2,8–4,8% (6,7–9,7 h no ar) para 8,4%
  (14,8 h). O android-01 no launcher não subiu: 1,3–3,4% até 6 h e 1,6–2,3% com 10 h. É isso que o reinício a frio
  zera (K-050).
- **O host não muda.** O processo do emulador do android-06 gasta 148% de uma CPU da máquina, antes e depois do
  `force-stop`, contra 117–121% do android-01. Tem mais threads (206–237 contra 183–187) e mais handles (1619 contra
  1287).
- **Canais identificados.** `virtio23` é o `vmw_vsock_virtio_transport`, canal do adb; a taxa dele varia com as
  próprias leituras por adb (inclusive as desta medida) e não separa os aparelhos. `virtio7` é o console, e
  `virtio22` o Wi-Fi simulado.

**O que funcionou.** Nada novo no código, por decisão medida:

- parar o app sozinho não libera a máquina: a CPU do emulador no host é a mesma;
- o custo seria uma partida a frio do app em toda execução;
- a faixa que derruba tarefas (21–90%, K-050) já tem o reinício a frio automático: com o aparelho ocioso, ≥15% em 3
  sondas.

**Aplicabilidade.** Vigente. Para medir irq num convidado:

- compare com um aparelho de controle no mesmo minuto: a carga do host move os dois;
- separe por linha (`/proc/interrupts`), não só pelo total;
- descarte a taxa do vsock, que as suas próprias leituras por adb inflam;
- antes de mexer no aparelho, confira a tela e a conta (K-053).

O gasto do emulador ocioso no host (1,2–1,5 CPU por aparelho com `swiftshader_indirect`) é da frente do renderizador
([relatorio-desempenho.md](../relatorio-desempenho.md)), não do irq do convidado.

### K-061 — No PostgreSQL, erro engolido dentro de `tx()` aborta a transação e o COMMIT vira ROLLBACK calado

**Data:** 29/09/2026 · **Área:** banco, aprendizado (ADR-054, item 22.5)

**Sintoma.** Revisão do A5. O ouvinte do D1 grava a trilha dentro da transação de `FlowStore`/`RecipeStore` e engole a
falha para "nunca derrubar o save". No PostgreSQL, isso derrubava o save do mesmo jeito, e pior: em silêncio. A loja
devolvia o id de um fluxo que não fora gravado.

**Causa.** No PostgreSQL, qualquer erro numa transação a deixa abortada: as instruções seguintes são recusadas
(`current transaction is aborted`), e o COMMIT final vira ROLLBACK sem levantar nada. O `try/except` do Python não
desfaz o estado do servidor. No SQLite, a transação segue viva, e a suíte passa.

**O que funcionou.**

- `Database.savepoint()` em volta de tudo o que o bloco tocou, DENTRO do `try` que engole. Fora do `try`, o savepoint
  não vê a falha, e o RELEASE numa transação abortada derruba tudo.
- A defesa no `tx()` de fora: a transação abortada antes do COMMIT vira `TransacaoAbortada` (500), em vez de perda
  calada.
- A imitação `tests/aborto_do_postgres.embrulhar(db)`, que faz o SQLite recusar as instruções depois de um erro, pega o
  esquecimento na suíte de sempre.

**Aplicabilidade.** Vigente. Todo `except` que engole erro de SQL dentro de uma transação alheia precisa do savepoint.
Candidatos conhecidos em [banco.md](../banco.md).

### K-062 — O Outlook não recusa o emulador: quem cai é o renderizador SwiftShader-GL do host (diagnóstico de 29/09 corrigido)

**Data:** 29/09/2026, corrigido em 30/09/2026 · **Área:** apps, emuladores (ADR-057, itens 23.2 e 29.10, P15)

**Sintoma.** Ao abrir o Outlook 5.2635.3, o processo `qemu-system-x86_64[-headless].exe` cai com `0xc0000005` em código
sem módulo, 10 a 60 s depois da tela inicial, no central e no notebook. No emulador canary o app também morreu, numa
`UD2` da `libhxcomm.so`.

**O que eu concluí em 29/09, e estava errado.** "O app se recusa a rodar em ambiente emulado", com os seis itens do
Outlook bloqueados à espera de uma decisão de produto (celular físico, Outlook web ou versão nova). A conclusão saiu
de um sintoma que não tinha sido isolado: não li os minidumps do host nem conferi qual renderizador o emulador tinha
selecionado de fato.

**Causa medida (30/09).**

- **A queda do host é do renderizador.** Os 8 minidumps do central e o único do notebook têm
  `gles_swiftshader\libGLESv2.dll` na pilha; as quedas com logcat casado aconteceram na tela de abertura (a animação
  do onboarding), não no armazenamento. Reproduzido em 30/09 no AVD `diag-outlook` com o emulador 37.1.11:
  `gles_mode_selected:swiftshader`, `debug.hwui.renderer=skiagl`, queda ~31 s depois de abrir o app.
- **Com `-gpu host` o Outlook abre**: chegou à tela "Add account" e ficou estável (30/09, mesmo AVD, sessão
  interativa, `gles_mode_selected:host`).
- **A `UD2` é um fail-fast do próprio app** (motor Hx) depois de um assert sobre `sortdefault.nls`: condição de estado e
  de tempo na primeira abertura, vista em 2 de 6, e que não se repetiu sobre o mesmo armazenamento. A biblioteca não
  tem nenhuma cadeia de detecção de emulador.

**O que não funcionou (medido, um fator por vez).**

- `-gpu angle_indirect` e `-gpu swangle`: o 37.1.11 recusa o primeiro ("not valid, switching to 'auto'") e os dois
  acabam em `gles_mode_selected:swiftshader`. O ANGLE **nunca** foi testado em 29/09: argumento aceito não é
  renderizador usado.
- `-prop debug.hwui.renderer=skiavk`: o emulador aceita o argumento e a propriedade **não** muda (segue `skiagl`).
- `setprop debug.hwui.renderer skiavk` como o shell: muda, e o convidado quebra — todo processo com interface aborta
  em `VulkanManager: Assertion failed: !grExtensions.hasExtension(VK_KHR_EXTERNAL_SEMAPHORE_FD…)`, com o Vulkan do
  SwiftShader; com o do lavapipe o convidado trava. O emulador "não cai" porque nada chega a desenhar: cinco aberturas
  "sem queda" eram o convidado em laço. O relato externo de que `skiavk` resolve não vale para esta imagem.
- Esconder o driver Vulkan do convidado: irrelevante, a queda está no caminho GLES.

**Aplicabilidade.** Vigente para o Outlook 5.2635.3 com o emulador 37.1.11 e a imagem `android-34;google_apis`. Antes
de declarar que um app não roda no parque: leia o dump do processo que caiu (os módulos na pilha dizem de quem é a
queda), confira no log o que foi SELECIONADO (`emuglConfig_init: … gles_mode_selected`), e confira o estado do app
depois do experimento, não só se o emulador está vivo. O renderizador por aparelho é `instances.overrides.<id>.gpu_mode`
no `config.yaml` (e `gpu_mode` no `worker.yaml` do notebook).

### K-063 — Com always-on e bloqueio, o cliente VPN volta em menos de um segundo: o teste de vazamento é uma ida só

**Data:** 30/09/2026 · **Área:** rede por aparelho (ADR-056, item 25.5)

**Sintoma.** No android-05, o teste de vazamento em três idas ao aparelho (`force-stop`, leitura do `tun0`, sonda) deu
"Permission denied" em 1 de 3 tentativas; nas outras, o `tun0` já tinha voltado e o teste ficou inconclusivo, com um
reinício a mais.

**O que funcionou.** Um script no convidado que para o cliente, espera o `/sys/class/net/tun0` sumir em passos de 0,1 s
e dispara a sonda no mesmo instante, até 5 tentativas (`sonda_rede.comando_parar_e_sondar`, `549a297`). Só "Permission
denied" como uid 2000 prova o bloqueio; root não é coberto pelo bloqueio.

**Aplicabilidade.** Vigente. Vale para qualquer medição que dependa de a VPN estar caída com always-on ligado.
Correção de 30/09 (K-066): o "volta em menos de um segundo" foi visto em 29/09 e **não se repetiu** em 4 tentativas
de 30/09 no mesmo aparelho. Não conte com o always-on para religar o cliente depois de um `force-stop`.

### K-064 — Dependência empacotada no tarball: o `npm audit fix` diz que corrige e não corrige, e o lock editado mente

**Data:** 30/09/2026 · **Área:** CI e dependências (`tools/appium`, job `dependencias`)

**Sintoma.** A corrida agendada de 30/09 05:29Z reprovou no `npm audit --audit-level=high` do Appium:
`brace-expansion` 5.0.9 (GHSA-q2hr-2g5m-vwhr, GHSA-qhr7-859c-m2p7, GHSA-6j4f-fj2g-mc7p, alta) em
`node_modules/appium-uiautomator2-driver/node_modules/`. O relatório dizia "fix available via `npm audit fix`".

**Causa.** O `appium-uiautomator2-driver` 8.7.0 publica as dependências DENTRO do próprio tarball
(`bundleDependencies`; no lock, `"inBundle": true`). O 8.7.0 saiu em 14/09 06:30Z e o `brace-expansion` 5.0.12 em
14/09 21:59Z: a última versão do driver carrega a vulnerável, e não há versão mais nova.

**O que não funcionou (medido, numa cópia).**

- `npm audit fix` (com e sem `--package-lock-only`): `changed: 0`. Dependência empacotada não é resolvida pelo
  registro.
- `overrides` no `package.json`: o lock não muda e o aviso continua.
- Editar só o lock (a entrada aninhada como 5.0.12, sem `inBundle`): o `npm audit` fica verde e o `npm ci` deixa a
  5.0.9 no disco, porque a pasta vem de dentro do tarball do driver. É um lock que mente.

**O que funcionou.** Corrigir o arquivo instalado e fazer o lock dizer a verdade: `tools/appium/corrigir-empacotados.mjs`
roda no `postinstall` do `npm ci` e troca a cópia empacotada pela 5.0.12 da raiz (mesma versão maior; o `minimatch`
empacotado pede `^5.0.8`), e o CI instala de verdade e confere o disco (`--conferir`). Conferido: `minimatch` e `glob`
do driver funcionam com o módulo trocado, e `appium driver list --installed` lista o `uiautomator2@8.7.0`.

**Aplicabilidade.** Vigente até o driver publicar uma versão que empacote a 5.0.12 (o script avisa "nada a trocar" e a
entrada sai de `CORRECOES`). Vale para qualquer aviso em caminho com `inBundle`: o relatório do `npm audit` não
distingue, e só a leitura do disco prova a correção.

**Segunda ocorrência (30/09, 17:36Z, run 36747845045).** A `axios` 1.19.0 entrou em sete avisos (um alto,
GHSA-vh66-26gq-q6x8 e outros) horas depois do CI verde da manhã. Três cópias: a da raiz (dependência comum do
`appium` 3.7.0, que fixa `1.19.0`), resolvida por `overrides` (`"axios": "1.20.0"`; o `@appium/support` publicado
depois já fixa a 1.20.0), e duas empacotadas mais fundo, em `appium-uiautomator2-driver/node_modules/@appium/base-driver`
e `…/@appium/support`, que entraram em `CORRECOES` com `dentro_de` aninhado. O dependente fixa a versão exata, então a
troca vai além do que ele declara: aceita porque é a mesma versão maior com as mesmas dependências declaradas, e o
autor dele já publicou a 1.20.0. Conferido numa instalação limpa: audit alto sem achado (restam 4 moderados do
`morgan`, abaixo do limite do CI), disco em 1.20.0 nas três, `appium` responde `/status` e lista o
`uiautomator2@8.7.0`. Lição: o CI verde de uma manhã não protege a tarde — o aviso nasce no registro, não no commit.

### K-065 — Evidência de teste destrutivo só em memória: o reinício do backend a perde e o teste se repete no parque

**Data:** 30/09/2026 · **Área:** rede por aparelho (ADR-056, ADR-061; itens 25.5 e 29.2)

**Sintoma.** Um reinício do backend às 02:40Z de 30/09 (um deploy). Entre 06:52Z e 07:59Z, 11 reinícios de aparelho
pedidos pela rede: android-02 (4, mais 1 reaplicação), android-06 (6, mais 2 reaplicações) e android-03 (1), os dois
últimos com conta real logada. No android-05 o mesmo teste, refeito, não concluiu, e um aparelho que estava
`trafego_verificado` passou a `parcial`.

**Causa.** O desfecho do teste de vazamento era guardado em `_Memoria.vazamento`, "em memória de propósito", com a
justificativa de que perder a memória "só significa conferir de novo". Para uma conferência barata isso vale. Para um
teste que para o cliente VPN e reinicia o aparelho, não: a memória perdida virou teste refeito. O gatilho não era o
reinício em si, e sim a remedição a 90% da validade, horas depois, o que escondeu a relação de causa.

**O que não funcionou.** Tratar como "um reinício a mais por revisão": o custo real foi multiplicado pelo túnel que
não sobe no boot (item 29.3). Propor o *backfill* da prova na migração com o cliente vazio: a migração não conhece o
aparelho, e um campo vazio que "vale até a primeira leitura" é um curinga.

**O que funcionou.** A prova na linha do aparelho, presa à revisão e à instalação do cliente VPN; a intenção gravada
antes do `force-stop`; a validade só para a medição barata (ADR-061). Na transição, adoção só com o histórico e a data
do APK demonstrados, conferida numa cópia do banco antes do deploy.

**Aplicabilidade.** Vigente. Antes de guardar em memória o resultado de qualquer verificação, perguntar quanto custa
refazê-la: se custa reinício, toque em conta real ou chamada paga, o resultado é dado durável, com a chave do que ele
prova.

### K-066 — O always-on tenta subir a VPN uma vez por boot; com o convidado sem CPU ele falha, e reiniciar rola o mesmo dado

**Data:** 30/09/2026 · **Área:** rede por aparelho (ADR-056, item 29.3)

**Sintoma.** Depois de um reinício pedido pela rede, a conferência lia o aparelho sem `tun0` (always-on, bloqueio e
regras no lugar, cliente instalado) e pedia outro reinício. Em 30/09, 15 de 30 conferências depois do boot foram
assim; um teste de vazamento no android-06 custou 6 reinícios e 2 reaplicações.

**Causa.** Medida em 7 boots no android-05: o sistema chama `startAlwaysOnVpn` uma vez por boot. Com o convidado sem
CPU durante o boot (2 vCPU, carga 11 a 20, SystemUI em laço de ANR), o serviço do cliente leva mais de ~22 s para
chamar `startForeground` e é morto por ANR (3 de 5 falhas), ou sobe e para em segundos (2 de 5). Quando sobe, sobe
entre 92 e 176 s de ligado; a plataforma conferia aos 95–159 s, com espera de 60 s.

**O que não funcionou.** Reiniciar de novo (a mesma chance de falhar, e mais carga). `am force-stop` para o always-on
religar (0 de 4). Iniciar o serviço pelo shell (não exportado). Clicar num tile que já estava na barra (0 de 2).

**O que funcionou.** Esperar o `tun0` até 180 s contados do boot. E, só com a configuração valendo e o túnel
faltando, o tile de configurações rápidas do próprio cliente, adicionado na hora e clicado pelo `cmd statusbar` como o
shell, com três guardas na mesma ida: SystemUI estável, tile na barra e nenhum `tun0` (o tile é alternador). O túnel
volta em segundos, sem tocar em always-on nem em bloqueio.

**Aplicabilidade.** Vigente para o cliente sing-box 1.14.2 em Android 14. A causa de fundo é CPU do convidado no boot:
um host menos carregado falha menos. O gesto com um app em primeiro plano e nos aparelhos do notebook segue `not_run`.

**Correção (W8, 01/10/2026): o tile só valia no android-05 porque o `serviceMode` do cliente ali já era VPN.** A
recuperação da convergência deixou de ser o tile e passou a ser o Start da interface: ver K-068.

### K-067 — Working set pequeno do emulador com WHPX não prova paginação do convidado

**Data:** 01/10/2026 · **Área:** worker do notebook (`worker-lan-01`), rede por aparelho (29.9)

**Sintoma.** No W4 (30/09 22:37–22:53Z) o android-09 subiu com o perfil VPN aplicado, o túnel não apareceu e o adbd do
convidado ficou `offline` também no adb do próprio notebook; a rede pediu um segundo reinício. As instalações seguintes
no notebook levaram mais de 7 min e uma leitura depois de instalar excedeu 40 s.

**A armadilha.** A primeira leitura (01/10 10:3xZ) viu os `qemu-system` do notebook com ~4 GB privados e só 0,6–2,9 GB
de working set, o arquivo de paginação com 30 GB usados e o pico no máximo alocado, e concluiu "convidado paginado".
Comparado com o central (12:3xZ), a conclusão caiu: lá os emuladores funcionam e mostram o mesmo retrato (menos de 1 GB
residente com ~3,7 GB privados). Com WHPX, a memória do convidado não aparece no working set do processo como se
esperava. O que segue de pé: o arquivo de paginação do notebook bateu no máximo alocado (37 GB; o do central, 18 GB de
pico), e `Pages/sec` estava em 1 no momento da leitura.

**A causa de verdade (01/10, medida).** Não era memória: o comando de observação da rede contava o cabeçalho
"Lockdown filtering rules:" do `dumpsys connectivity`, que o Android 14 imprime sempre. Todo aparelho tinha "regras de
bloqueio"; com a política `exigida` (sem bloqueio) o túnel no ar nunca valia como conectado e a rede reiniciava em
cadeia. Corrigido em `658e5bb` (conta as linhas `UIDs:`); com a correção, W2–W7 passaram no android-09.

**O que fazer.** Não tirar conclusão de memória pelo working set de um emulador com WHPX, e desconfiar primeiro do
que a própria plataforma mede: o registro dizia "tun0 no ar; VPN CONNECTED" e mesmo assim "o túnel não subiu".

### K-068 — O tile do SFA não recalcula o `serviceMode`: num cliente que só importou o perfil ele inicia o ProxyService e o serviço aborta

**Data:** 01/10/2026 · **Área:** rede por aparelho (ADR-056, item 29.9, W8)

**Sintoma.** No android-09 o tile do cliente (`religar_pelo_tile`, o "mecanismo medido" do K-066) clicava com sucesso (exit 0) e
o túnel nunca subia: `ProxyService` em primeiro plano por 1–2 s, `STOP_FOREGROUND`, sem `tun0`, sem par
(`F6_TUN_NOT_CREATED_AFTER_TILE`, 4 vezes no W8 e nas duas A1). No android-05 o mesmo gesto subia o `VPNService` e o túnel.

**Causa (código do SFA 1.14.2, commit upstream `fc21909df7a3f0fc9435f3866fb6a4960711aa5f`; prova real no 09).**
`TileService.onClick` → `BoxService.start()` → `Settings.serviceClass()` (`serviceMode == VPN` → `VPNService`, senão
`ProxyService`), **sem `rebuildServiceMode()`**. Só `MainActivity.startService0` (o Start da UI) e a seleção de perfil com o
serviço rodando recalculam o modo (`Libbox.hasTunInbound(perfil selecionado)`); a importação (`create(andSelect = true)`)
seleciona o perfil sem recalcular, e o `serviceMode` nasce `NORMAL`. O `ProxyService` com perfil que tem `tun` falha no
`openTun` ("android: tun inbound requires VPN service", que só vai para a UI, não para o logcat) e se encerra. O 09 só
importou o perfil (nunca um Start pela UI): modo NORMAL. O 05 já tinha o modo VPN (origem desconhecida: provável Start da UI nas
rodadas do piloto). O always-on do boot não passa por isso (o sistema inicia o `VPNService` direto).

**O que não funcionou.** O tile no 09 (0 de 6). Culpar o netlink (`avc denied { bind } netlink_route_socket` aparece também no
05 e na VPN que funciona), o par, o endpoint ou o perfil selecionado (r2 estava certo).

**O que funcionou.** UM toque no Start da interface (`real`, android-09, 01/10 18:23Z): `VPNService`, `tun0` em < 1 s, VPN
CONNECTED, sem par. A plataforma agora religa assim (`rede_aplicacao.religar_pela_interface`): acha o `Start` PELA ÁRVORE (o
rótulo do Compose é filho não clicável de um contêiner clicável), UM toque, sucesso só com `tun0` E VPN CONNECTED, e o guard
`wrong_service_class_for_tun` quando o Start inicia o `ProxyService`. Nunca se escreve o `serviceMode` (é do SFA).

**Aplicabilidade.** Cliente sing-box (SFA) 1.14.2; reconferir o código ao trocar de versão. O Start da UI deixa o `serviceMode`
em VPN, o que torna o tile funcional naquele aparelho, mas a convergência não depende disso. O rótulo vem do locale do aparelho
(tabela `ROTULOS_DO_CLIENTE`, SFA 1.14.2: en/fa/ru/zh-CN/zh-TW; sem identificação independente de idioma, o Compose não tem `testTag`) e a
classe de serviço só vale na JANELA do Start (`UNKNOWN` sem prova): handoff W8 §20. Os boots 1/3/4 do W8 sem túnel por
always-on seguem sem causa (`BOOT_RECOVERY_ROOT_CAUSE = OPEN`).

### K-069 — Teste lento por tempo real: antes de encurtar o `sleep`, ache quem o ESPERA e se o aparelho falso envelhece pelo mesmo relógio

**Data:** 02/10/2026 · **Área:** testes (T.2, achado #164)

**Sintoma.** `test_resultado_ambiguo...` levava 63,6 s e `test_rotation.py` 20 s sem que o rodízio custasse nada.

**Causa.** Dois tempos diferentes. (1) O orçamento do verificador (`_verify`: 15 s, ou 60 s com o efeito disparado) era literal, e
o laço de sondagem de 0,05 s em `judge_wait_s` não aparece em sonda que filtra `sleep` ≥ 0,3 s: 1.200 `sleep`s curtos somam 60 s.
(2) As esperas de assentamento das ferramentas (~2,9 s por passo de mensagem) e o `wait_for` do verificador simulado, que existe
para o aparelho falso envelhecer a mensagem (`sent_after_s`/`delivered_after_s` pelo relógio real).

**O que não funcionou.** Encurtar o `sleep` e pronto: com 0,05 s o `wait_for` de 2 s vira "ainda enviando", o simulado desiste
depois de 6 voltas e o teste passa a provar outra coisa.

**O que funcionou.** Medir AGREGANDO segundos por ponto de chamada (um plugin que embrulha `asyncio.sleep`), configurar o
orçamento (`ai.verify_budget_*`) e fazer as ferramentas dormirem por `ToolContext.dormir`, com o aparelho falso lendo o mesmo
relógio (`tests/relogio_virtual.py`, `Harness.pular_o_tempo()`).

**Aplicabilidade.** Só em teste opt-in: o `time.monotonic()` de prazos de etapa segue real, e testes de corrida com
`action_delay_s` dependem da janela real. Ligar no harness inteiro pede a suíte completa.

### K-070 — Teste que espera tempo fixo enquanto o código lê processo real do host oscila de máquina para máquina

**Data:** 02/10/2026 · **Área:** testes (`test_worker_executor.py`)

**Sintoma.** `test_a_espera_na_fila_de_boot_e_dita_em_progresso` falhava neste host (e passava no CI): o recado "fila de boot" não
tinha chegado quando o teste olhava, 50 ms depois de criar as tarefas.

**Causa.** Antes do recado, cada `start` faz `avd.exists` e `_varrer` -> `_no_ar` -> `pid_do_avd`: `psutil.process_iter` e
`p.cmdline()` sobre os processos REAIS do host com "qemu"/"emulator" no nome (só leitura; 8 processos aqui), ~60 ms sob pytest.
Provado por intervenção: `process_iter` vazio passa 3/3; 0,2 s injetados falha 3/3. O aparelho falso não protegia: `_estado_falso`
trocava `estado`, mas `pid_do_avd` seguia lendo o host.

**O que não funcionou.** Aumentar o `sleep` (continua dependendo do número de processos do host) e conferir só no CI.

**O que funcionou.** Esperar o FATO (`_ate(...)` sobre o recado ou o `subidos`), isolar `pid_do_avd` junto de `estado`
(`_sem_processos_reais`, aplicado por `_estado_falso`) e um teste que injeta 0,2 s na varredura SEM o isolamento, tirando a
fotografia dos recados antes de drenar as tarefas (senão o recado chega durante a drenagem e a mutação para `sleep(0.05)` passa).

**Aplicabilidade.** Todo teste do executor que não testa a varredura; quem testa `pid_do_avd` atribui o seu depois ou usa
`processos_reais=True`. `sleep` fixo só como janela NEGATIVA (nada deve acontecer), nunca para esperar que algo aconteça.


### K-071 — Pedido em `call_later` que esgota calado: quem espera o reinício não pode pagar pela espera de 5 minutos

**Data:** 02/10/2026 · **Área:** rede por aparelho (29.21, android-05)

**Sintoma.** `exigida_com_bloqueio` acordou a frio sem `tun0`; a convergência pôs `configurado`, religou pela interface e nunca
abriu um `restart`; a execução que esperava a rede ficou presa 7 min. Nada no log nem no histórico dizia o motivo.

**Causa.** O reinício é pedido por `call_later` em memória (24 tentativas de 5 s) e o esgotamento punha `espera_ate` 300 s, que
cala a varredura e a porta. O termo que segurava o aparelho ("ocupado") não ia a lugar nenhum, então o defeito não tinha
rastro. Qual termo segurou no android-05 segue sem prova.

**O que não funcionou.** Supor o termo pelos eventos (IA liberou, `device.network` fechado, o objetivo da rede é excluído da
conta): nenhum deveria segurar, e a suposição não é evidência.

**O que funcionou.** Dizer o termo na linha (uma vez por termo) e, havendo objetivo parado em `wait_reason='rede'` no
aparelho, retentar em 30 s em vez de 300 s. Teto, agendamento e `espera_ate` seguem em memória (dívida).

**Aplicabilidade.** Qualquer espera em memória que cale um laço de decisão precisa dizer por que calou e ter uma saída curta
quando alguém depende dela.

### K-072 — Teste que afirma o contador de uma thread de servidor no instante em que o cliente recebe a resposta

**Data:** 02/10/2026 · **Área:** testes (`test_rede_aplicacao.py`, `ServidorDeUmaVez`)

**Sintoma.** `test_servidor_de_uma_vez_serve_um_get_so_no_caminho_do_token` falhava às vezes com `pytest -n 8` e passava isolado.

**Causa (hipótese descartada e causa lida no código).** A hipótese era porta fixa disputada entre os workers do xdist: não é —
`ServidorDeUmaVez` já pede a porta 0 (efêmera) e lê `porta` de volta do socket, e cada teste tem o seu servidor. A corrida é
outra: o `do_GET` grava o corpo e SÓ DEPOIS faz `entregues += 1`; o cliente volta com o corpo assim que o último byte chega, e o
teste afirmava `srv.entregues == 1` nessa hora. Se a thread do servidor perde a CPU entre o `write` e o `+= 1` (o que a carga
de 8 workers provoca), o teste lê 0. O servidor está certo (o consumidor de produção já espera o fato: `entregues < 1` em laço).

**O que não funcionou.** Trocar a porta (já era efêmera) e rodar isolado (a janela é de microssegundos sem carga).

**O que funcionou.** Esperar o FATO (`_ate_o_fato(lambda: srv.entregues == 1, ...)`) e provar a causa de forma determinística:
`_atrasar_depois_do_corpo` faz o handler esperar 0,3 s depois de cada gravação no socket (envolve `wfile` no `setup` da classe do
handler, sem tocar na produção); o teste parametrizado com esse atraso falharia sempre para quem afirma o contador logo ao
receber a resposta. Um teste à parte confirma que dois servidores ao mesmo tempo recebem portas diferentes e não se cruzam.

**Aplicabilidade.** Todo teste que lê estado mantido por uma thread do servidor sob teste (contador, fila, flag): a volta do
cliente não ordena nada em relação a ele. Espere o fato com prazo; `sleep` fixo só como janela negativa. Para provar a causa,
injete o atraso no ponto da corrida em vez de repetir até falhar.

### K-073 — Prova gravada no banco sobrevive ao processo que ela provou: compare o marco de boot, não só a validade

**Data:** 02/10/2026 · **Área:** rede por aparelho (`devices/rede.py`, `rede_convergencia.py`), item 29.22

**Sintoma.** android-05 (`exigida_com_bloqueio`) estava `trafego_verificado` pela medição #134 das 19:20. Parado e ligado a frio às
19:50, a linha continuou verificada com a MESMA medição, nenhum evento "Rede de android-05" apareceu depois do `start` e a porta
liberou a tarefa às 19:52, quando o aparelho acusava "sem internet: DNS não responde" (o túnel só subiu depois). O bloqueio evitou o
vazamento; a prova de tráfego atravessou o boot sem reverificação.

**Causa.** `verificacao_invalida` só conhecia a idade (`vencida`) e os apps. A prova mora na linha de `device_network`, que
sobrevive ao boot; o que ela provava (túnel, DNS, regras) não. Ao ligar, a convergência de um `trafego_verificado` só CONFERIA (não mede, não
grava evento).

**O que funcionou.** Guardar nada novo: comparar `verified_at` com o marco de boot que já existe (`instances.emulator_started_at`, no banco, que
sobrevive ao restart do central; `online_since_mono` para o aparelho de worker, renovado onde o agente conclui o boot) e tratar boot
mais novo que a medição como inválida na porta, igual a `vencida`, com a medição como único caminho de volta. Testar com o boot 1 ms
depois da medição velha: só uma medição nova (posterior) libera, sem mexer no marco no meio do teste.

**Aplicabilidade.** Toda prova durável atrelada a um runtime (túnel, sessão, hierarquia, classificação de tela) precisa declarar a que
geração do runtime pertence e comparar com a de agora; validade por relógio não substitui isso. Atenção ao aparelho remoto: o central só
vê o boot pelo desfecho do agente, e `_adopt` põe `online` sem passar pelo `_set_state`.

### K-076 — Versão do app tem dois formatos: a receita grava `nome(código)`, o aparelho guarda nome e código separados

**Data:** 03/10/2026 · **Área:** aprendizado (versão, saúde)

**Sintoma.** Com o 30.4/30.14 sobre uma cópia do banco do central, 78 de 160 itens do Livro saíram `obsoleto_provavel` com o
motivo `versao_fora_do_parque`, inclusive receitas reproduzindo bem no parque inteiro.

**Causa.** `recipes.app_version` é `versionName(versionCode)` (o mesmo formato de `taskqueue/scheduler.py`), e
`device_app_state` guarda `observed_version_name` e `observed_version_code` em colunas separadas. A comparação de texto exato
do 30.6 nunca casava. Os testes usavam versões sintéticas sem código (`"447"`) e passavam. A tela e a lição gravam só o nome.

**O que funcionou.** Montar as vivas no formato da receita (`versao_canonica`) e comparar a tela e a lição pelo nome
(`nome_da_versao`); teste com os valores MEDIDOS do banco.

**Aplicabilidade.** Toda comparação de versão de app entre fontes do projeto. Antes de comparar, meça o formato real das
duas colunas; dado sintético sem o formato real esconde o erro. Aceite visual com cópia do banco pega o que o teste não pega.
