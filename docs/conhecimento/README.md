# Base de conhecimento — como o Claude retoma contexto entre sessões

Três níveis, para caber no contexto sem perder rastreabilidade. Nível 1 é evidência; níveis 2 e 3 são o que o
Claude deve realmente carregar na maioria das tarefas.

## Nível 1 — fontes históricas (referenciadas, não lidas por padrão)

O material bruto que sustenta as decisões e os aprendizados, mas que não vale a pena reabrir a cada tarefa:

- `docs/auditoria-2026-09-21/` — os 181 achados (`#n`) da auditoria que originou `docs/plano-100.md`, datados do
  commit `f1e61b3`. Consultar só quando uma evidência cita um `#n` específico.
- Seções datadas de `docs/relatorio-validacao.md` (§1–§13) — o que foi medido em cada rodada, com `arquivo::teste`
  ou `c-…`/`r-…` de execução real.
- `docs/parque-distribuido.md` — projeto e medições da arquitetura distribuída (grande parte já consolidada em
  `docs/arquitetura.md` e `docs/dominios/parque.md`; o arquivo continua como registro histórico das medições).
- Planos de sessão do dono, em `C:\Users\Administrator\.claude\plans\*.md` — **fora deste repositório**, na máquina
  onde o Claude roda. Não clonáveis por quem só tem o Git. Ver `docs/conhecimento/fontes.md` para os cinco planos
  usados nesta rodada.
- Notas de memória do agente, em `C:\Users\Administrator\.claude\projects\C--git-android\memory\*.md` — também
  fora do repositório. São observações datadas, não estado vivo: podem estar desatualizadas em relação ao código de
  hoje.

## Nível 2 — conhecimento consolidado (o que carregar por padrão)

Os documentos principais por assunto (`docs/produto.md`, `docs/ia.md`, `docs/operacao.md`, `docs/arquitetura.md`,
`docs/dominios/*.md`), mais os dois registros desta base:

- **`docs/decisoes.md`** — um ADR por decisão de arquitetura/produto, com contexto, alternativas, escolha,
  consequências e evidência. Ler quando a tarefa toca uma área que já tem decisão tomada, para não propor de novo
  algo já decidido (ou já recusado).
- **`docs/conhecimento/aprendizados.md`** — um registro por armadilha (`K-NNN`), com sintoma, causa e o que
  funcionou. Ler quando um sintoma bate com um destes, antes de investigar do zero.

## Nível 3 — contexto operacional da tarefa atual

O que descreve **este momento**, não a história:

- **`docs/estado-atual.md`** — handoff entre sessões: o que está em andamento, o que falta, próxima ação sugerida.
- **`.claude/plano-100/pacotes/<id>.md`** (gerado por `python scripts/plano-100-pacotes.py`) — pacote autocontido
  de um item do plano-100: a linha do plano, os achados citados na íntegra, os arquivos candidatos. Não abrir o
  plano nem o apêndice quando o pacote já existe — é para isso que ele existe.
- **`.claude/plano-100/estado.json`** — o que cada item do plano-100 tem registrado (status, prova, evidência,
  conferência). Versionado no Git; só `scripts/claude-plan-100.py aplicar` deve escrever nele (oito registros de 24/09 foram escritos à mão — ver `docs/claude-plano-100.md`).

## Índice pesquisável

Uma linha por registro relevante de `docs/decisoes.md` e `docs/conhecimento/aprendizados.md`. "Aplicabilidade":
**vigente** (conferido ou decisão em pé hoje), **histórico** (relevante só como registro, sem ação vigente),
**superado** (o código mudou; ver o registro para o que substituiu).

| ID | Assunto | Módulo/área | Tipo | Data | Aplicabilidade | Onde | Relação |
|---|---|---|---|---|---|---|---|
| ADR-001 | Arquitetura do parque distribuído: worker "remota gerenciada" | arquitetura/parque | decisão | 19/09 | vigente | `docs/decisoes.md#adr-001` | fases 2, 4 |
| ADR-002 | Canal do worker: túnel reverso com listener dedicado | segurança/worker | decisão | 23/09 | vigente | `docs/decisoes.md#adr-002` | 0.3, 9.4 |
| ADR-003 | Banco: SQLite por padrão, PostgreSQL por configuração | banco | decisão | 17/09 | vigente | `docs/decisoes.md#adr-003` | 5.4 |
| ADR-004 | Segundo backend real: infraestrutura pronta, sem topologia em uso | banco/arquitetura | decisão | 22/09 | vigente | `docs/decisoes.md#adr-004` | 5.1–5.3, decisão 9 |
| ADR-005 | IA por função e depois ator local como camada de custo | IA/custo | decisão | 17/09, 24/09 | vigente; ator local superado por ADR-023 | `docs/decisoes.md#adr-005` | 7.1–7.8 |
| ADR-006 | Rodízio de N contas sobre K vagas + hibernação | parque/RAM | decisão | 17/09 | vigente | `docs/decisoes.md#adr-006` | 0.5, 4.2 |
| ADR-007 | Receitas e fluxos: a IA ensina uma vez, o software repete | IA/custo | decisão | 17/09, 24/09 | vigente | `docs/decisoes.md#adr-007` | 7.5–7.7 |
| ADR-008 | Instagram real via Play Store, sem espelho de terceiros | apps/loja | decisão | 17/09 | vigente | `docs/decisoes.md#adr-008` | fase 6 |
| ADR-009 | Desafio, 2FA, CAPTCHA e senha sempre pela pessoa | segurança/instagram | decisão | 17/09 | vigente | `docs/decisoes.md#adr-009` | 6.4 |
| ADR-010 | Comando distribuído com cerca, outbox e idempotência | worker/comandos | decisão | 21/09 | vigente | `docs/decisoes.md#adr-010` | fase 1, 5.6 |
| ADR-011 | `config.yaml`/`.env` fora do Git, por instalação | operação | decisão | 23/09 | vigente | `docs/decisoes.md#adr-011` | T.3 |
| ADR-012 | Executor do plano-100: workflow na sessão da IDE | plano-100/processo | decisão | 22/09 | vigente | `docs/decisoes.md#adr-012` | skill `preparar-tarefa` |
| ADR-013 | Fallback pago de recusa ligado por padrão (decisão 3) | IA/custo | decisão | 24/09 | vigente | `docs/decisoes.md#adr-013` | 0.10, 7.2 |
| ADR-014 | Loja remota: RDP até o worker (decisão 4) | apps/loja | decisão | 24/09 | vigente | `docs/decisoes.md#adr-014` | 6.5 |
| ADR-015 | Alvo de capacidade e limites por servidor (decisão 5) | parque/capacidade | decisão | 24/09 | vigente | `docs/decisoes.md#adr-015` | 10.3, 10.5 |
| ADR-016 | Acesso de pessoas: sessão nominal sobre token único (decisão 8) | segurança | decisão | 23/09 | vigente | `docs/decisoes.md#adr-016` | 9.1 |
| ADR-017 | Chave do provedor de IA: sem revogação (decisão 2) | IA/custo | decisão | 24/09 | vigente | `docs/decisoes.md#adr-017` | 0.10 |
| ADR-018 | Bateria de avaliação: autorizada na opção recomendada (decisão 7) | IA/custo | decisão | 25/09 | executada | `docs/decisoes.md#adr-018` | 7.4 |
| ADR-019 | Hora certa nas duas máquinas (decisão 6) | operação | decisão | 21–23/09 | vigente (divergência sem veredito) | `docs/decisoes.md#adr-019` | 0.7 |
| ADR-020 | Backup e janela de reinício de produção antes de migrar (decisão 1) | banco/operação | decisão | 21/09 | vigente | `docs/decisoes.md#adr-020` | 0.1 |
| ADR-021 | Commit direto na main, sem PR | processo | decisão | 17/09 | vigente | `docs/decisoes.md#adr-021` | skill `fechar-tarefa` |
| ADR-022 | Exclusões deliberadas de escopo | segurança/produto | decisão | 21/09 | vigente | `docs/decisoes.md#adr-022` | §7 do plano-100 |
| ADR-023 | Ator declarado no Sonnet; modelo local fora do caminho principal | IA/custo | decisão | 25/09 | vigente | `docs/decisoes.md#adr-023` | 7.1, 7.8, 7.11 |
| ADR-024 | Verificador barato com proteções, em vez de trocar o modelo | IA/custo | decisão | 25/09 | vigente | `docs/decisoes.md#adr-024` | 7.4, 7.10 |
| ADR-053 | Falhas reiteradas do Instagram: UI ocupada relê, recuperação preserva o estado, ANR com sinal próprio, alvo pela legenda, reinício a frio por interrupção | execução/parque | decisão | 28/09 | vigente | `docs/decisoes.md#adr-053` | fase 19 |
| ADR-054 | Aprendizado contínuo: livro com ciclo de vida, D1 (publica sozinho só sem efeito externo), D2 (feedback implícito + botão), lições medidas, backlog do que mais falha | aprendizado/IA | decisão | 29/09 | vigente (fundação integrada, a implantar) | `docs/decisoes.md#adr-054` | fase 20 |
| ADR-055 | Proteção de contas: conta travada para sem ser tocada, quarentena do aparelho, uma conta por alvo, DM fria com aprovação, nenhum reset com conta | perfis/Instagram/parque | decisão | 29/09 | vigente (integrado, a implantar) | `docs/decisoes.md#adr-055` | fase 21, ADR-029 |
| ADR-056 | Rede por aparelho: VPN dentro do Android com proxy encadeado, cinco estados, IP de saída medido; revisa a cláusula de rede do ADR-055 | parque/rede/segurança | decisão | 29/09 | vigente (a implementar, Fase 25) | `docs/decisoes.md#adr-056` | Fase 25, ADR-055, K-057 |
| ADR-057 | Outlook como primeiro app novo: conta por app, sessão por conta, credencial clonada no cofre | apps/contas/segurança | decisão | 29/09 | vigente (a implementar, Fase 23) | `docs/decisoes.md#adr-057` | Fase 23, 12.3, ADR-040, ADR-052 |
| ADR-058 | Comando entre aplicativos: catálogo pelo app da etapa, valor lido entre etapas | execução/IA | decisão | 29/09 | proposto (Fase 24) | `docs/decisoes.md#adr-058` | Fase 24, 12.1, ADR-009 |
| ADR-059 | Pedidos persistentes pertencem ao produto: pedido, ocorrência e execução | execução/produto | decisão | 29/09 | proposto (Fase 26) | `docs/decisoes.md#adr-059` | Fase 26 |
| ADR-061 | A prova de vazamento é da linha do aparelho: presa à revisão e ao cliente VPN, sem validade por relógio | parque/rede | decisão | 30/09 | vigente | `docs/decisoes.md#adr-061` | 29.2, ADR-056, K-065 |
| K-001 | Harness de teste usava as portas do parque real | testes | erro | 18/09 | vigente | `docs/conhecimento/aprendizados.md#k-001` | `base_console_port` |
| K-002 | Checkout apaga `config.yaml` não versionado | operação | erro | 23/09 | vigente | `docs/conhecimento/aprendizados.md#k-002` | ADR-011 |
| K-003 | "malformed database schema" após reboot | banco | erro | 23/09 | vigente | `docs/conhecimento/aprendizados.md#k-003` | `restore.ps1` |
| K-004 | Restaurar o banco regride a cerca (fencing) | worker/comandos | erro | 23/09 | vigente | `docs/conhecimento/aprendizados.md#k-004` | fencing |
| K-005 | Tarefa `farm-agente` sem gatilho de boot | operação/worker | erro | 23/09 | vigente | `docs/conhecimento/aprendizados.md#k-005` | `worker-agent.ps1` |
| K-006 | PowerShell 5.1 exige `.ps1` com BOM | scripts | erro | 23/09 | vigente | `docs/conhecimento/aprendizados.md#k-006` | — |
| K-007 | Workflow `.js` sem quebra de linha literal, em LF | plano-100 | erro | 24/09 | vigente | `docs/conhecimento/aprendizados.md#k-007` | `.claude/rules/workflows-js.md` |
| K-008 | Subagente pode recusar a tarefa delegada | plano-100 | erro | 24/09 | vigente | `docs/conhecimento/aprendizados.md#k-008` | `claude-plan-100.py::validar` |
| K-009 | `pwsh -File` com arrays chega como string | scripts | erro | 17/09 | vigente | `docs/conhecimento/aprendizados.md#k-009` | `worker-tunnel.ps1` |
| K-010 | Não fazer pipe da saída de `start.ps1` | scripts | erro | 17/09 | vigente | `docs/conhecimento/aprendizados.md#k-010` | — |
| K-011 | Bateria de avaliação parou por falta de crédito da API | IA/custo | erro | 24/09 | vigente | `docs/conhecimento/aprendizados.md#k-011` | ADR-018 |
| K-012 | `python.exe` do venv é um launcher (2 processos) | operação | erro | 17/09 | vigente | `docs/conhecimento/aprendizados.md#k-012` | `supervisor.py` |
| K-013 | `uiautomator dump` morre com sessão Appium ativa | automação | erro | 18/09 | vigente | `docs/conhecimento/aprendizados.md#k-013` | `/instances/{id}/hierarchy` |
| K-014 | Acentos em `curl -d` corrompem o JSON | ferramentas | erro | 18/09 | vigente | `docs/conhecimento/aprendizados.md#k-014` | — |
| K-015 | `cache_control` condicionado subcontava tokens | IA/custo | erro | 24/09 | vigente | `docs/conhecimento/aprendizados.md#k-015` | `anthropic_provider.py` |
| K-016 | `price_for` casava pelo primeiro prefixo, não pelo mais longo | IA/custo | erro | 24/09 | vigente | `docs/conhecimento/aprendizados.md#k-016` | `costs.py` |
| K-017 | Tag `qwen3-vl:4b` do Ollama é *thinking* e devolve vazio | IA | erro | 24/09 | vigente | `docs/conhecimento/aprendizados.md#k-017` | ADR-005 |
| K-018 | Escalonador de núcleo do Hyper-V deixava o worker lento | infraestrutura | erro | 24/09 | vigente | `docs/conhecimento/aprendizados.md#k-018` | — |
| K-019 | Imagens `android-28`/`29`/`aosp_atd` sem tradução ARM | emuladores | erro | 17/09 | vigente | `docs/conhecimento/aprendizados.md#k-019` | — |
| K-020 | Login do Instagram tocava "Entrar" nas coordenadas do teclado | automação/instagram | erro | 18/09 | vigente | `docs/conhecimento/aprendizados.md#k-020` | — |
| K-021 | `read_account` lia o autor do reel, não a conta própria | automação/instagram | erro | 18/09 | vigente | `docs/conhecimento/aprendizados.md#k-021` | `app_declarado/sessao.py::ler_conta` |
| K-022 | Receita vazia aprendida e laço "tocar → voltar" | receitas/execução | erro | 23–24/09 | vigente | `docs/conhecimento/aprendizados.md#k-022` | `recipes.py`, `executor.py` |
| K-023 | `Agent` com `isolation: remote` pode cair em worktree local | ferramentas | erro | 24/09 | vigente | `docs/conhecimento/aprendizados.md#k-023` | — |
| K-024 | Caminho do Windows em string Python vira caractere de controle | ferramentas | erro | 25/09 | vigente | `docs/conhecimento/aprendizados.md#k-024` | T.4 |
| K-047 | `dumpsys window` do Android 14 abre com a seção "LAST ANR": o primeiro `mCurrentFocus` é o congelado | automação/adb | erro | 28/09 | vigente | `docs/conhecimento/aprendizados.md#k-047` | 19.2, ADR-053 |
| K-048 | `hide_error_dialogs=1` transforma ANR em morte silenciosa do app (reason=6) | automação/adb | erro | 28/09 | vigente (o experimento com 0 foi feito: K-054, manter 1) | `docs/conhecimento/aprendizados.md#k-048` | 19.2, ADR-053 |
| K-049 | O 500 "hogging the main UI thread" é UI ocupada, não sessão morta | automação/Appium | erro | 28/09 | vigente (pendência do screencap resolvida em `6799867`) | `docs/conhecimento/aprendizados.md#k-049` | 19.1, ADR-053 |
| K-050 | Interrupção acumulada no convidado com dias no ar; `restart` devolve ~2% | parque/emuladores | erro | 28/09 | vigente | `docs/conhecimento/aprendizados.md#k-050` | 19.9, ADR-053 |
| K-051 | `farm-ci-runner` em "Ready" não quer dizer runner parado | CI/operação | erro | 28/09 | vigente | `docs/conhecimento/aprendizados.md#k-051` | 18.4 |
| K-052 | A política própria do perfil prevalece sobre o `approval_required` do catálogo | perfis/política | erro | 28/09 | vigente | `docs/conhecimento/aprendizados.md#k-052` | 19.10, ADR-053 |
| K-053 | Conta logada sem persona: `account_label` e `/personas` não dizem se há conta no aparelho; confira a tela | parque/processo | erro | 29/09 | vigente | `docs/conhecimento/aprendizados.md#k-053` | 21.2, ADR-055 |
| K-054 | `hide_error_dialogs=0` trava o aparelho no ANR do `system_server`: manter 1 | emuladores/adb | erro | 28–29/09 | vigente | `docs/conhecimento/aprendizados.md#k-054` | 21.8, K-048 |
| K-055 | NTP bloqueado com porta de origem 123: `w32time` não sincroniza, `stripchart` sim (`farm-relogio`) | operação/host | erro | 28/09 | vigente | `docs/conhecimento/aprendizados.md#k-055` | 21.7, ADR-019 |
| K-056 | Aposentar no Windows: arquivo somente-leitura do emulador (`pstore.bin`) faz o `rmtree` falhar | parque/provisionamento | erro | 29/09 | vigente | `docs/conhecimento/aprendizados.md#k-056` | 21.9 |
| K-057 | Frota coordenada sobre uma pessoa real precede os bloqueios: conduta, não disfarce | perfis/Instagram/política | erro | 29/09 | vigente; a cláusula de rede foi substituída pelo ADR-056 | `docs/conhecimento/aprendizados.md#k-057` | 21.3, ADR-055, ADR-056 |
| K-058 | Carga da IDE no central vira "aparelho doente" e dispara a escada de reparo: um trabalho pesado por vez | parque/operação/processo | erro | 29/09 | vigente | `docs/conhecimento/aprendizados.md#k-058` | 21.16, ADR-055 |
| K-059 | Apps do Google em segundo plano pesam nos convidados de 2 GB: desativar pelo preparo, lista configurável | parque/emuladores | erro | 29/09 | vigente (a relação com o irq segue aberta) | `docs/conhecimento/aprendizados.md#k-059` | 21.15, K-050 |
| K-062 | O Outlook derruba o emulador estável; no canary, o app morre numa armadilha UD2 da libhxcomm.so | apps/emuladores | erro | 29/09 | vigente | `docs/conhecimento/aprendizados.md#k-062` | 23.2, P15, ADR-057 |
| K-063 | Always-on religa o cliente VPN em menos de 1 s: teste de vazamento numa ida só ao aparelho | rede por aparelho | erro | 30/09 | vigente | `docs/conhecimento/aprendizados.md#k-063` | 25.5, ADR-056 |
| K-065 | Evidência de teste destrutivo só em memória: cada reinício do backend a perde e o teste se repete em todo o parque | rede por aparelho | erro | 30/09 | vigente | `docs/conhecimento/aprendizados.md#k-065` | 29.2, ADR-061 |
| K-066 | O always-on tenta subir a VPN uma vez por boot e falha com o convidado sem CPU; o tile do cliente religa sem reinício | rede por aparelho | erro | 30/09 | vigente | `docs/conhecimento/aprendizados.md#k-066` | 29.3, ADR-056 |
| K-064 | Dependência empacotada no tarball (`inBundle`): `npm audit fix` e `overrides` não corrigem, e o lock editado fica verde com o código vulnerável no disco | CI/dependências | erro | 30/09 | vigente | `docs/conhecimento/aprendizados.md#k-064` | 29.1 |

## Como localizar

**Por módulo/área** — filtre a coluna Módulo/área da tabela acima, ou:

```bash
grep -n "IA/custo" docs/conhecimento/README.md
```

**Por tarefa (ID do plano-100)** — grep pelo ID na coluna Relação, ou direto nos dois documentos:

```bash
grep -n "0\.10\b" docs/decisoes.md docs/conhecimento/aprendizados.md
```

**Por erro (mensagem/sintoma)** — grep no corpo de `aprendizados.md`, que tem o sintoma literal:

```bash
grep -n -i "malformed database schema" docs/conhecimento/aprendizados.md
```

**Por decisão (ADR)** — abra a seção direto pelo link da tabela-índice de `docs/decisoes.md`, ou:

```bash
grep -n "^## ADR-" docs/decisoes.md
```

## Promoção: quando um aprendizado vira regra

Um `K-NNN` é promovido para o doc principal do assunto (nível 2) ou para `.claude/rules/` quando: o problema se
repetiu, custou tempo de novo, ou vale para além deste episódio específico. Ao promover, o registro em
`aprendizados.md` **não é apagado** — ganha a marca `promovido para <destino>` na linha de Aplicabilidade, mantendo
o histórico de quando e por que a regra nasceu. Exemplo já promovido nesta rodada: K-007 → `.claude/rules/
workflows-js.md`.

## Revisão e arquivamento

Um registro cujo código mudou desde que foi escrito não é apagado — é marcado **"superado em `<commit/data>`"**,
com uma linha dizendo o que substituiu. Apagar perderia o motivo de uma decisão anterior ter existido; "superado"
preserva isso sem confundir quem lê hoje.

A skill `fechar-tarefa` inclui, como último passo, conferir se a tarefa que acabou de fechar torna algum registro
existente superado — é o gatilho normal de revisão, não uma varredura periódica separada.
