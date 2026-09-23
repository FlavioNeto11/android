# Relatório de validação — 17/09/2026

Tudo abaixo foi medido **neste host** (é a mesma máquina onde a POC roda): Windows Server 2025 (10.0.26100),
Intel Core Ultra 9 185H (16 núcleos / 22 threads), 63,5 GB de RAM, 478 GB livres em C:, Hyper-V ativo.

> **Leitura rápida.** A plataforma inteira (painel, fila, scheduler, executor, Appium, emuladores, controle manual,
> recuperação) foi validada em emuladores Android reais — primeiro em *modo simulado* (sem chave) e, depois que a
> chave foi colocada no `.env`, **com o modelo real (`claude-opus-5`) planejando, agindo e verificando**: mensagem em
> 1 e em 3 aparelhos em paralelo, formulário, um segundo app nunca visto (Configurações do Android), falhas
> injetadas, controle manual + retomada e `kill` do backend no meio da execução. Resultados na seção 5.
> O limite que permanece é de hardware: **3 de 10** instâncias simultâneas com a carga atual do host (seção 2).

## 1. Versões e ambiente

| Componente | Versão | Como foi obtido |
|---|---|---|
| Android Emulator / platform-tools | 37.1.11 / 37.0.1 | `scripts/install-prereqs.ps1` (cmdline-tools 15859902, SHA-256 conferido com a página oficial) |
| Imagem | `system-images;android-34;google_apis;x86_64` (rev. 14) | idem |
| Aceleração | **WHPX** — `emulator -accel-check` → `WHPX(10.0.26100) is installed and usable` | Hyper-V + `HypervisorPlatform: Enabled` (DISM), `hypervisorlaunchtype Auto` |
| Appium / UiAutomator2 driver / servidor UiA2 | 3.7.0 / 8.7.0 / 10.6.6 | `tools/appium/package.json` (fixado) |
| Python / FastAPI / Pydantic / anthropic SDK | 3.13.15 / 0.141.1 / 2.13.5 / 1.6.0 | `backend/requirements.txt` (lock do `uv pip compile`) |
| Node / React / Vite / TypeScript | 24.19 / 19.3.0 / 8.3.0 / 7.0.2 | `frontend/package-lock.json` |
| APK de QA | AGP 9.4.0, Gradle wrapper 9.6.0, JDK 21, compile/target 34, min 26 — 71 KB | `scripts/build-qa-apk.ps1` |

Flags do emulador: `-no-window -no-audio -no-boot-anim -no-snapshot -gpu swiftshader_indirect -accel on`
(sessão de servidor, sem GPU do host). Pós-boot o backend espera o launcher (antes disso o screenshot sai preto),
desliga animações, mantém a tela ligada, remove keyguard, oculta diálogos de ANR e o teclado virtual.

## 2. Capacidade medida

### 2.1 Custo real de cada imagem (`scripts/probe-image.ps1`, 1536 MB solicitados, 2 núcleos, 720×1280)

| Imagem | RAM efetiva imposta pelo emulador | Working set do qemu | Bytes privados | 1º boot |
|---|---|---|---|---|
| android-34 · google_apis (padrão) | **2560 MB** ("Increasing RAM size") | 3,4 GB (3,5–3,8 GB em uso) | 3,7 GB | 180 s (1º) / 54–71 s (seguintes) |
| android-34 · default (AOSP) | 2560 MB | 3,39 GB | 3,80 GB | 74 s |
| android-34 · aosp_atd | 2560 MB | 2,78 GB | 3,32 GB | 68 s |
| android-30 · google_apis | 2048 MB | 2,91 GB | 3,34 GB | 77 s |
| android-29 · google_apis | 2048 MB | 2,79 GB | 3,14 GB | 58 s |
| **android-28 · default (Android 9)** | **1536 MB respeitados** | **1,97 GB** | **2,21 GB** | 85 s |

Conclusão: o emulador 37.x ignora `hw.ramSize` abaixo do piso da imagem. Com Android 14 cada instância custa
≈3,5 GB reais; 10 instâncias pedem ≈36 GB livres.
**Correção (seção 7):** a imagem Android 9 é leve, mas **não serve** para os apps-alvo — medido: ela não tem tradução
ARM nem Google Play Services. O caminho que funcionou foi a flag `-lowram` (o emulador passa a respeitar `ram_mb`)
somada ao rodízio com hibernação.

### 2.2 Escala nesta máquina, com a carga atual do host

No momento dos testes o host tinha só **≈11–12 GB disponíveis**: um processo `python main.py --port 8188` (≈13 GB)
e o Docker Desktop/WSL (≈10,7 GB) estavam abertos, e a orientação recebida foi "medir com o que há", sem parar nada.

<!-- ESCALA:INICIO -->
Teste gradual `scripts\scale-test.ps1 -Steps "1,2,3,5,10"` (imagem Android 14, 2 núcleos, 720×1280, headless,
WHPX; provedor **simulado**). Em cada degrau: boot das que faltam → provisiona o app de QA → roda o comando de
mensagem em todas as online → mede. Dados brutos: `data/scale-test-results.json` e `data/logs/scale-test.log`.

| Degrau | Online | Boot a frio (cada) | RSS por emulador | RAM disponível no host (antes → depois da execução) | CPU do host | Execução do comando |
|---|---|---|---|---|---|---|
| 1 | **1** | 70,8 s | 3,67 GB | 11,1 → 11,1 GB (82 % em uso) | 13 → 19 % | `r-…-184c9e` · 1/1 sucesso comprovado · 18 s |
| 2 | **2** | 73,9 s | 3,58–3,69 GB | 7,6 → 7,2 GB (88 %) | 24 → 25 % | `r-…-449e7c` · 2/2 sucesso comprovado · 48 s |
| 3 | **3** | 78,1 s | 3,45–3,94 GB | 3,6 → 3,5 GB (94 %) | 37 → 40 % | `r-…-689fb0` · 3/3 sucesso comprovado · 68 s |
| 5 | **3** (04 e 05 recusadas) | — | 3,70–3,99 GB | 2,9 → 3,6 GB (95 %) | 44 → 29 % | `r-…-d5fdfa` · 3/3 sucesso comprovado · 40 s · *solicitadas 3, utilizadas 3* |
| 10 | não executado | | | | | o script para no primeiro degrau não sustentado |

Motivo literal da recusa (cartão do `android-04` e do `android-05`):
> Capacidade do host atingida: 3565 MB disponíveis; esta instância precisa de ≈3700 MB e o host deve manter
> 1500 MB livres. 3 instância(s) online. Libere memória no host ou use uma imagem mais leve.

**Capacidade medida: 3 instâncias simultâneas** com a carga atual do host (≈3,7 GB reais cada). A CPU não foi o
limite (≤ 44 % com 3 aparelhos executando); o limite é memória. O mesmo fluxo com 3 aparelhos variou de 21 a 68 s
entre execuções (possível pressão de memória com o host a 94 % de uso; 18 s com 1 aparelho).
Verificador independente (ContentProvider do app de QA, via `adb`): cada aparelho tem exatamente 1 mensagem por
execução, com o seu `{instance_id}` e o `{run_id}` daquela execução, todas `Entregue ✓✓`.

**Persistência entre reinícios (real):** `android-02` e `android-03` tinham sido parados e foram religados por este
teste — voltaram com o APK instalado, a conta `qa-user-02`/`qa-user-03` ativa e as mensagens das execuções
anteriores à parada ainda na conversa (`content query` no `emulator-5556`: linhas de `r-…-b1b757` e `r-…-07429d`
com `created_at` anterior à parada) — userdata preservado.
**Reset explícito (real):** `reset` sem `confirm` → HTTP 409; com `confirm=true` o `android-03` reiniciou com
`-wipe-data` (pronto em 191 s), sem o pacote de QA e sem dados; `android-01`/`02` permaneceram intactos (8 e 5
mensagens). Depois o `android-03` foi reprovisionado.
<!-- ESCALA:FIM -->

A configuração continua com 10 instâncias (`android-01…10`, portas e AVDs reservados). Para chegar a 10
simultâneas nesta máquina: liberar RAM no host **ou** — o que passou a ser o padrão — atender as 10 contas em rodízio sobre poucas vagas (seção 7).
O backend nunca deixa o host sem memória: cada boot só é aceito se couber a instância + os boots em andamento +
1,5 GB de folga; caso contrário o cartão explica o motivo e a execução informa quantas instâncias foram usadas.

## 3. Cenários de aceite

Legenda: ✅ comprovado · 🟡 comprovado só em modo simulado/sem IA real · ⛔ não testado (motivo e procedimento).

| # | Cenário | Resultado | Evidência |
|---|---|---|---|
| 1 | Comando em português → plano → tarefa completa em Android real, **com uso efetivo do modelo** | ✅ **modelo real**: plano de 6–7 etapas gerado por `claude-opus-5`, 11–12 chamadas ao modelo por aparelho (decisões + verificações por visão); 83 s de ponta a ponta em 1 aparelho. Disparado também pelo botão **Executar** do painel (`r-…-04ffcb`) | `r-…-d0a4e0`, `r-…-04ffcb`; verificador independente: 1 linha no ContentProvider com o texto e `Entregue ✓✓` |
| 2 | Mesmo comando em N instâncias e contas isoladas, simultâneas | ✅ com o modelo real em **3 de 10** (`r-…-f5e5da`, 92 s; `r-…-04ffcb`, 72 s): 3/3 com 1 mensagem por aparelho. 🟡 10 simultâneas não couberam (limite de RAM do host na hora do teste; 4ª e 5ª recusadas com motivo): 3 contas (`qa-user-01..03`), 3 sessões Appium (`systemPort` 8200–8202), 21–68 s, 1 mensagem por aparelho com `{instance_id}`/`{run_id}` próprios | `r-…-b1b757`, `r-…-689fb0`, `r-…-d5fdfa` (seção 2.2); `content query` em `emulator-5554/5556/5558` |
| 3 | App alvo trocável por configuração | ✅ cadastrei `com.android.settings` pela API (só nome, package e uma dica) e pedi "role até *About emulated device*, entre e confirme *Android version*": 2/2 aparelhos, a IA rolou, trocou `scroll` por `drag` quando a lista não avançava e reportou **Android 14**. Nenhuma linha de código conhece esse app | `r-…-6352dd`; `tests/test_tools_and_api.py`, tela Configuração |
| 4 | Falha/tela inesperada em um aparelho não para os demais | ✅ **com o modelo real** (`r-…-e7f67d`): `android-01` dispensou o aviso e concluiu; `android-02` bloqueado só ele na tela de senha (1 chamada de IA, captura omitida); `android-03` viu "Falha no envio ✕", declarou que não devia reenviar → incerto. Antes, em modo simulado: `android-01` com aviso inesperado (dispensado e concluído), `android-02` com sessão expirada (bloqueado *só ele*, tela de senha nunca gravada), `android-03` com falha de envio (incerto) | `r-…-07429d`: 1 sucesso + 1 bloqueio + 1 incerto; teste `test_falha_e_tela_inesperada…` |
| 5 | Usuário assume, navega e devolve sem disputa de cliques | ✅ **com o modelo real**: no `android-02` bloqueado, `scripts\qa-manual-login.ps1` assumiu o controle pela API do painel (lease + `frame_id` novo a cada entrada), digitou conta/PIN fictícios, devolveu; "Retomar" só daquele item → a IA reobservou e concluiu 6/6 em 70 s (plano v2, 1 mensagem). Antes, em modo simulado: login manual do `android-02` pela API de entrada (tap/texto/tecla com `lease_id` + `frame_id`), devolução e retomada só daquele item → sucesso; pela UI: "Assumir controle", toque no elemento da tela via eventos de ponteiro (`pointerdown/up` disparados no componente — não foi um clique de mouse do navegador, que a ferramenta de teste não posicionava com precisão) mapeado para o aparelho (conversa → tela inicial), "Devolver à IA". Frame antigo → `stale_frame`; lease errado → `not_controller` | plano v4 do `android-02` em `r-…-07429d`; teste `test_usuario_assume_no_ponto_seguro…` |
| 6 | Fechar/reabrir o painel preserva execução e histórico | ✅ snapshot + eventos por `last_event_id`; histórico das 6 execuções listado após recarregar; nada é reenfileirado | tela Execuções; `test_api_dedup_validacao_e_reconexao…`; teste de integração do frontend |
| 7 | Reiniciar o backend preserva a fila e reconcilia | ✅ **com o modelo real** (`r-…-fb81e9`): `kill` aos 58 s com 9 etapas concluídas, 3 `running` e 6 pendentes; ao subir, 3 tentativas `interrupted`, reobservação e 3/3 concluídos, 1 mensagem por aparelho, 1 execução no banco. Antes, em modo simulado: `kill` do backend com 3 etapas `running`; ao subir, tentativas marcadas `interrupted`, etapas reobservadas, 3/3 concluídos, **1 mensagem por aparelho**; emuladores readotados e sessões Appium antigas encerradas por id | `scripts/test-restart-recovery.ps1`, `r-…-fc6426`; testes `test_reinicio_…` (2) |
| 8 | Falha após o toque de enviar → confirmação ou incerto, sem reenvio | ✅ testes com driver falso: erro após o efeito → reconcilia e conclui com 1 mensagem; erro sem efeito visível → `uncertain`, nenhum novo toque, retomada em lote recusa o item; timeout do driver segura o aparelho até a chamada terminar. ✅ real: `send_fail=1` no app → `uncertain`, sem reenvio | `tests/test_execution.py` (3 testes), `r-…-07429d` |
| 9 | Clique duplo e reconexões não duplicam | ✅ duplo clique real no botão **Executar** → 1 execução; mesma `idempotency_key` via HTTP → `deduplicated: true`; 8 criações simultâneas → 1 linha | `r-…-a26b26`; `tests/test_queue_core.py` |
| 10 | Relatório distingue comprovado de bloqueado/não testado | ✅ `GET /api/runs/{id}/report` e aba Relatório: sucesso comprovado × confirmado manualmente × falha × bloqueio × incerto × cancelado × não iniciado; solicitadas × utilizadas | relatórios impressos por `scripts/demo-run.ps1` |

Testes automatizados: **47** no backend (`pytest`, ≈7 min com emuladores ligados) e **155** no frontend (`vitest`), todos passando.

Estado deixado na máquina ao final: backend em `127.0.0.1:8000` com o provedor real ativo (a barra mostra
`claude-opus-5`), `android-01…03` online com o app de QA, contas logadas e sessão de automação prontas, falhas
injetadas desligadas, `android-04` criado e parado, `android-05…10` sem AVD (o botão "Criar AVD" ou
`start.ps1 -StartInstances N` os cria). O app "Configurações do Android" ficou cadastrado em Configuração → Aplicativos.
O contador de bloqueadas/incertas da barra vem do histórico destes testes (itens **incertos** provocados de propósito
em `r-…-07429d` e `r-…-e7f67d`, os bloqueios do campo inexistente em `r-…-032297` e um item antigo em `r-…-36181a`).

## 4. Limitações observadas

* **Custo/latência da IA**: ≈12 chamadas e ≈80 mil tokens de entrada por aparelho por comando (seção 5); cada etapa custa pelo menos 2 decisões (agir + declarar concluída) — a declaração do modelo é mantida de propósito, porque pós-condições fracas seriam satisfeitas cedo demais. O modo simulado só conhece o app de QA.
* **RAM**: 3,5 GB por instância com Android 14; 3 instâncias simultâneas couberam com a carga atual do host.
* `hw.ramSize` abaixo do piso da imagem é ignorado pelo emulador; boot a frio de 54–150 s conforme a concorrência.
* Screenshots por ADB (`screencap`) levam ≈0,5 s; a grade captura a cada 5 s e o aparelho em foco a cada 1 s.
* Em etapa com efeito externo, qualquer dúvida vira `uncertain` (inclusive uma falha de envio visível na tela):
  é conservador de propósito — quem decide repetir é o usuário.
* A guarda contra duplo envio depende de a IA marcar a ação como *commit* (há uma rede de segurança por vocabulário
  do elemento: enviar/send/confirmar/pagar…). Exatamente-uma-vez dentro de um app de terceiros não é garantível.
* O `sdkmanager` avisa que está obsoleto em favor do novo *Android CLI*; continua funcionando.
* Encerrar o backend com `kill` deixa o Appium rodando; o próximo start o **readota** (PID gravado em
  `data/appium.pid` + linha de comando conferida), fecha as sessões antigas, remove `adb forward` órfãos e o
  `stop.ps1` seguinte o encerra (testado). Um Appium externo na mesma porta é reutilizado e nunca encerrado.
* Quando o plano volta com **informação faltante** (`missing`), não há um endpoint de "responder à pergunta": o
  painel mostra a pergunta e o usuário reenvia o comando já com o dado. Bloqueios *durante* a execução (login,
  confirmação) têm fluxo próprio: assumir controle → devolver → "Retomar".
* A saída estruturada estrita do provedor não transporta `pattern`/limites numéricos do schema; o backend revalida
  tudo com Pydantic. Chaves de etapa fora do padrão (`Open-App`) são normalizadas; outras violações viram
  "Plano inválido" (erro visível na execução, nada é executado).
* O reset (`-wipe-data`) apaga também o APK e a sessão; é preciso reinstalar/relogar (para o app de QA:
  `scripts\provision-qa.ps1`). O primeiro boot após o reset leva ≈3 min.

## 5. Validação com o provedor real (`claude-opus-5`, chave do usuário no `.env`)

A chave nunca foi exibida, registrada em log, gravada no banco ou copiada: só foi carregada em memória pelo backend e por uma sonda temporária fora do repositório (que usou o mesmo carregador para testar quais definições de ferramentas a API aceita). Varredura final por prefixo de chave em `data/logs`, no SQLite e no frontend compilado: 0 ocorrências.
Screenshots e textos das telas do app de QA/Configurações foram enviados à API da Anthropic (avisado no painel);
a tela de senha **não** foi enviada nem gravada.

| Execução | Comando (resumo) | Aparelhos | Resultado | Tempo | Verificador independente |
|---|---|---|---|---|---|
| `r-…-d0a4e0` | mensagem para QA-001 | 1 | 1/1 sucesso comprovado (`delivered`) | 83 s | 1 linha, `Entregue ✓✓` |
| `r-…-f5e5da` | mesma, em paralelo | 3 | 3/3 sucesso comprovado | 92 s | 1 linha por aparelho, cada uma com seu `{instance_id}`/`{run_id}` |
| `r-…-032297` | Perfil: nome + **"recado"** (campo que não existe) | 2 | 2 **bloqueados** — a IA explicou que a tela só tem Nome, E-mail, Cidade e Notificações, e não inventou | 70 s | `/profile` sem alteração |
| `r-…-30e8d1` | Perfil: Nome + Cidade, salvar e confirmar | 2 | 2/2 sucesso comprovado | 104 s | `/profile`: `name=Robo android-0N`, `city=Cidade <run_id>` |
| `r-…-6352dd` | **outro app**: Configurações → About emulated device → Android version | 2 | 2/2 — versão reportada: 14 | 125 s | — (leitura de tela) |
| `r-…-5589cd` | mensagem — era para ter falhas injetadas, mas meu comando `adb` estava com aspas erradas e as falhas **não** entraram: é só mais uma execução normal | 3 | 3/3 sucesso comprovado | 100 s | 1 linha por aparelho |
| `r-…-e7f67d` | mensagem, com 3 falhas injetadas | 3 | 1 sucesso · 1 bloqueio (senha) · 1 incerto (falha de envio, sem reenvio); após login manual + "Retomar": 2 sucessos · 1 incerto | 94 s (+70 s) | 5554: 1 `Entregue` · 5556: 0 → 1 após retomada · 5558: 1 `Falha no envio ✕` (nenhuma 2ª linha) |
| `r-…-fb81e9` | mensagem para QA-003 com `kill` do backend aos 58 s | 3 | 3/3 após reconciliação | — | 1 linha por aparelho |
| `r-…-650357` | comando ambíguo (dois destinatários e dois textos, por um erro meu de digitação no painel) | 10 pedidas | **Precisa de informações**: a IA perguntou qual destinatário e qual texto valem; nada foi executado; depois cancelei essa execução para não ficar como pendência | — | — |
| `r-…-04ffcb` | pelo botão **Executar** do painel: buscar "Suporte QA" e enviar "Chamado POC …" | 3 | 3/3 sucesso comprovado | 72 s | 1 linha por aparelho para `Suporte QA` |
| `r-…-86b5fa` | só **Planejar** (conferir o plano após reforçar o prompt do planejador); cancelada em seguida | 1 | plano com pós-condições que distinguem antes/depois | — | — |
| `r-…-69a485` | mensagem para QA-001, **executada com o prompt reforçado** | 1 | 1/1 sucesso comprovado; `fill_message` comprovada por seletor composto (`id=message_input` + texto) e `send_message` julgada por visão | 91 s | 1 linha, `Entregue ✓✓` |

**Consumo medido**: ≈12 chamadas ao modelo e ≈80 mil tokens de entrada por aparelho por comando de 6–7 etapas
(≈1,7 mil de saída). Cada decisão leva ≈6,8 mil tokens: 4,3 mil são o prefixo fixo (ferramentas + instruções), que
passou a vir do *prompt cache* (`cache_lido=4344` no log), e ≈2,5 mil são a tela (imagem + hierarquia + histórico).
Total desta validação: 271 chamadas, ≈1,79 M tokens de entrada e ≈39 mil de saída (mais os planos).

**Defeitos que só o modelo real revelou — corrigidos e cobertos por teste:**
1. `strict_schema` removia a *propriedade* `title` do schema (confundida com o metadado `title` do JSON Schema) → o
   plano voltava sem `title` e era rejeitado ("Plano inválido", `r-…-b033c5`).
2. A API recusou as 14 ferramentas como estritas (**"Schema is too complex"**, `r-…-ab0bec`). Medido: 6 estritas
   passam, 8 não. Ficaram estritas as que causam efeito ou encerram a etapa (`tap`, `long_press`, `drag`,
   `type_text`, `step_done`, `step_blocked`); as demais usam o mesmo schema sem a gramática. Toda chamada continua
   revalidada por Pydantic antes de executar; se a API recusar de novo, o provedor segue sem `strict` sozinho.
3. O planejador combinou pós-condição `text_visible` com nível de entrega exigido — o texto já aparece no campo
   *antes* do envio. Agora, sempre que há nível exigido, o verificador com visão julga a tela também; e o prompt do
   planejador exige pós-condições que distingam o estado final do anterior — o plano seguinte veio com
   `id=message_input|text=…` e `model_judged` e **foi executado com sucesso** (`r-…-69a485`).
4. Menores: id do app com acentos (`configura-es` → `configuracoes`), cache de prompt ligado, log de uso por chamada.

**Não exercitado com o modelo real:** recusa do provedor/fallback (não ocorreu nenhuma recusa), estouro de orçamento
de IA, e apps de terceiros com login real (dependem de contas suas). Para desligar o *fallback* de recusa (beta):
`AI_REFUSAL_FALLBACK=false`.

## 6. Comandos usados

```powershell
pwsh -File scripts\diagnose.ps1 ; pwsh -File scripts\install-prereqs.ps1 ; pwsh -File scripts\build-qa-apk.ps1
pwsh -File scripts\start.ps1 -Simulated -NoBrowser
pwsh -File scripts\provision-qa.ps1 ; pwsh -File scripts\demo-run.ps1 -Instances android-01,android-02,android-03
pwsh -File scripts\test-restart-recovery.ps1 -Instances android-01,android-02,android-03 -Simulated
pwsh -File scripts\scale-test.ps1 -Steps "1,2,3,5,10" ; pwsh -File scripts\probe-image.ps1 -Image '<imagem>'
# com a chave no .env (provedor real):
pwsh -File scripts\start.ps1 -NoBrowser ; pwsh -File scripts\demo-run.ps1 -Instances android-01,android-02,android-03
pwsh -File scripts\demo-run.ps1 -Instances android-01,android-03 -Command 'Abra o app Configurações do Android, role até "About emulated device"…'
pwsh -File scripts\qa-manual-login.ps1 -Instance android-02 ; pwsh -File scripts\test-restart-recovery.ps1 -Instances android-01,android-02,android-03 -KillAfterSec 58
cd backend; .venv\Scripts\python.exe -m pytest -q        cd frontend; npm test; npm run build
```

## 7. Otimização de RAM e de custo de IA (17/09/2026, segunda rodada)

Pedido: atender 10+ contas em apps como Instagram/WhatsApp/Facebook/TikTok gastando menos RAM e menos IA. Decisões
do usuário: rodízio sob demanda, emulador oficial otimizado primeiro, tarefas majoritariamente repetitivas, Anthropic
com modelo por função. **Nenhuma execução paga foi feita nesta rodada**: a chave da API foi exposta no chat e a
validação com o provedor real fica para depois da troca (procedimento em 7.5). Tudo abaixo foi medido em emuladores
reais com o provedor *simulado* (que exercita a plataforma inteira, menos o modelo) e por testes automatizados.

### 7.1 Imagem × apps-alvo (`scripts/probe-image.ps1`, agora com ABIs, GMS, flags e snapshot)

| Imagem | Tradução ARM (`abilist`) | Google Play Services | Serve para apps arm64? |
|---|---|---|---|
| android-28 · default (Android 9) | não (`x86_64,x86`) | não | **não** — a recomendação anterior de "perfil leve Android 9" estava errada para estes apps |
| android-29 · google_apis | não | sim | não |
| android-34 · aosp_atd | não (`x86_64`) | não | não |
| android-30 · google_apis | **sim** (`x86_64,x86,arm64-v8a,armeabi-v7a,armeabi`) | sim | sim |
| android-34 · google_apis (padrão) | **sim** | sim | sim |

Os APKs reais não foram instalados (não há APKs em `apks/` e eu não baixo binários de terceiros): rode
`scripts\probe-image.ps1 -Image '<imagem>' -Apk <seus.apk>` para confirmar instalação, ABI usada e abertura.

### 7.2 RAM por instância (working set do qemu, 45 s após o boot, headless, 2 núcleos)

| Configuração | RAM do guest | Working set | Observação |
|---|---|---|---|
| android-34 · google_apis · 720p (antes) | 2560 MB impostos | 3,43 GB | "Increasing RAM size to 2560MB" |
| android-34 · google_apis · **`-lowram`** · 1536 MB | **1470 MB respeitados** | **2,33–2,41 GB** | adotado; ≈2,9 GB com app + automação em uso |
| android-34 · `-lowram` · 2048 MB · `-gpu host` | 1974 MB | 2,67 GB | |
| android-30 · google_apis · 720p | 2048 MB impostos | 3,02 GB | |
| android-30 · 540×960 (sem `-lowram`) | 2048 MB | 3,06 GB | resolução sozinha não reduz |
| android-30 · `-gpu host` | 2048 MB | 2,75 GB | GPU do host (RTX 2000 Ada) poupa ≈0,3 GB; screenshot OK |
| android-30 · `-lowram` · 1536 MB | 1477 MB | 2,50–2,57 GB | |
| android-30 · `-lowram` · 1280 MB | 1225 MB | 1,98 GB | |
| android-30 · `-lowram` · 1024 MB · `-gpu host` | 975 MB | 1,62 GB | mínimo medido; 1 GB é apertado para apps sociais |

Achado: **`-lowram` é o que faz o emulador respeitar `hw.ramSize`**. Sobra ≈0,9–1,4 GB de sobrecarga do próprio
qemu/renderizador por instância, que nenhuma flag testada removeu. Efeito colateral visto uma vez: num aparelho
recém-religado após reset (primeiro boot, 197 s, 390 MB livres no guest) o app ficou com a tela preta sem reagir a
toques; reabrir o app resolveu — a recuperação automática agora encerra o app antes de refazer o plano.

### 7.3 Hibernação por snapshot (WHPX, headless, emulador 37.1.11)

| Configuração | Salvar | Disco | Acordar (`boot_completed`) | Carregou? |
|---|---|---|---|---|
| android-30 · 2048 MB | 1,4 s | 1,87 GB | 7 s | sim |
| android-30 · `-lowram` 1536 | 1,2 s | 1,42 GB | 5 s | sim |
| android-34 · `-lowram` 1536 | 1,2 s | 1,46 GB | 4 s | sim |
| android-34 · 2560 MB (sem `-lowram`) | 3,3 s | 2,41 GB | 118 s | **não** — o emulador ignorou o snapshot e fez boot a frio |

No produto (instâncias reais `android-01/02`): hibernar 1,5–1,6 s; **acordar até ficar pronto para automação 14–18 s**
(contra 63–197 s de boot a frio). Relógio do guest após acordar: −26 a −31 s na sonda (às vezes se corrige sozinho
em segundos, às vezes não) → o backend acerta o relógio ao acordar (medido depois: −1/−2 s). Rede e adb OK. Histórico
do app preservado (15 mensagens antigas no `android-01` após hibernar/acordar). O snapshot é de **uso único**: o
flag é zerado antes de todo spawn; reset, boot a frio e mudança de hardware o descartam; se o emulador ignorar o
snapshot, o backend percebe pelo log em 20 s, segue como boot a frio e deixa de hibernar aquele AVD.

### 7.4 Rodízio em emuladores reais: 10 contas sobre 4 vagas (`scripts/rotation-test.ps1`)

Perfil: Android 14 · google_apis · `-lowram` · 1536 MB · hibernação ligada · receitas em `replay` · provedor
**simulado** (o custo de IA medido aqui é zero por construção; o que se mede é RAM, tempo e a mecânica).

| Rodada | Contas com sucesso comprovado | Tempo do lote | Pico de aparelhos ligados | RSS dos emuladores no pico | RAM livre mínima do host | Etapas por receita / receita+IA / IA |
|---|---|---|---|---|---|---|
| 1 (com preparo; 7 AVDs recém-criados) | 9/10 — a 10ª bloqueou por erro transitório ao abrir a sessão de automação logo após acordar (corrigido: 3 tentativas) | 669 s | 4 | 11,4 GB | 17,7 GB | 51 / 3 / 0 |
| 2 (regime) | **10/10** | **292 s** | 4 | 11,3 GB | 16,9 GB | 54 / 3 / 3 |
| 3 (regime, 100 % das acordadas por snapshot) | **10/10** | 423 s | 4 | 11,1 GB | 16,1 GB | 53 / 4 / 3 |

* Antes: 3 instâncias simultâneas ocupavam ≈11 GB e era o teto. Agora **10 contas cabem nos mesmos ≈11 GB**, porque só
  4 ficam ligadas por vez (≈2,8 GB cada em uso). Com mais RAM livre basta subir `max_online_devices`.
* Acordar de snapshot: mediana **16,4 s** (13,4–25,4 s, n=8) · boot a frio: mediana 73 s (n=4) e 104 s com 7 AVDs novos
  bootando em paralelo · hibernar: mediana 1,9 s (24 de 24 snapshots salvos) · relógio após acordar: 0 a −1 s.
* Verificador independente (ContentProvider do app de QA, nos aparelhos que estavam ligados ao final): exatamente
  1 mensagem desta execução por conta.
* Defeitos que este teste revelou e que foram corrigidos: (a) snapshot tirado na **1ª sessão** de um AVD recém-criado
  ou resetado nunca carrega ("The emulator hardware cannot load snapshot": `initPath` e partição de dados diferem) →
  nessa sessão o aparelho só desliga; (b) reescrever `disk.dataPartition.size=4G` a cada boot encolhia o valor que o
  emulador tinha aumentado e mudava o hardware entre sessões → o valor maior é preservado; (c) a detecção "o snapshot
  não carregou" agora lê o veredito explícito do log do emulador (com a máquina carregada a linha demora); (d) app com
  tela preta após boot pesado → recuperação encerra o app; (e) um aparelho com problema próprio não põe mais em
  quarentena uma receita que funciona nos demais.

### 7.5 Custo de IA: o que mudou e o que falta medir

Implementado e coberto por testes (contagem feita **no provedor**, não no banco):

| Alavanca | Efeito esperado | Evidência disponível |
|---|---|---|
| Receitas (`ai.recipes: replay`) | etapa repetida = 0 decisões de IA | teste: 2º aparelho faz 0 decisões nas etapas aprendidas; em emulador real ≈91 % das etapas rodaram por receita (158 de 174 nas 3 rodadas) |
| Fluxos (`ai.flows`) | comando repetido = 0 chamadas ao planejador | teste: mesmo comando com outro contato/texto reaproveita o plano |
| Depois do toque de efeito externo, vai direto à verificação | −1 chamada por etapa de envio | teste: 1 decisão na etapa `send_message` (antes 2) |
| Verificação por visão só rejulga quando a tela muda (teto 2) | até −3 chamadas por etapa julgada | teste com "enviando…" preso por 4 s |
| `expect_done` (ação + conclusão na mesma chamada) | ≈−1 chamada por etapa comum | unidade; **depende do modelo real usar o campo** |
| Imagem sob demanda + hierarquia priorizada (90 linhas) | menos tokens novos por chamada | teste: maioria das decisões sem imagem; árvore local completa; senha nunca escapa |
| `screenshot_max_side: 768` (**candidato, não adotado**: padrão segue 1280) | ≈−700 tokens por chamada que ainda leva imagem | a revisão final achou um defeito aqui: a imagem era reduzida, mas os limites dos elementos iam em pixels do aparelho, e um x,y tirado deles cairia ≈67 % fora do alvo. Corrigido (limites e x,y no mesmo espaço; teste de ida e volta por `resolve_point`). Fica para a bateria decidir se a legibilidade dos textos pequenos se mantém |
| Modelo por função + escalonamento | ator/verificador em Sonnet 5 (US$ 2/10) ou Haiku 4.5 (US$ 1/5) em vez de Opus 5 (US$ 5/25) | teste sem rede: modelo certo por função, nível 1 em etapa com efeito/nova tentativa, parâmetros recusados por um modelo são desligados só para ele |
| Uso por chamada (`ai_calls`) + `/api/usage` + `scripts/usage-report.ps1` | custo visível por função/modelo/execução | teste de API |

**Não medido (exige o provedor real):** taxa de sucesso e US$ por aparelho-comando com Sonnet 5/Haiku 4.5 no ator, se
`expect_done` e `need_image` são bem usados pelo modelo, se o prefixo (≈4,3 k tokens) entra no cache do Haiku, e o
comportamento das receitas aprendidas com planos do planejador real. Referência de antes: ≈12 chamadas, ≈80 k tokens
de entrada, ≈US$ 0,22–0,44 por aparelho-comando em Opus 5.

**Em aberto, dito sem rodeio:** o plano era manter cada alavanca "só se o sucesso não cair", e isso **não pôde ser
medido** sem execuções pagas. Mesmo assim `config.yaml` já liga `image_policy: auto`, 90 linhas de hierarquia,
`verify_max_model_calls: 2`, `recipes: replay`, `flows: true` e o desbravador — validados em emulador real **apenas com
o provedor simulado**. A cadeia planejador real → fluxo → receita nunca rodou. Se preferir começar pelo comportamento
já validado com a IA real (seção 5), ponha os valores entre parênteses de `config.yaml` e religue uma alavanca por vez.
A lista de parâmetros que o Haiku 4.5 recusa (`thinking`, `effort`) é suposição até `probe-models.py` confirmar; se
estiver errada, o provedor aprende no primeiro 400 e segue.

**Procedimento (≈20 min, depois de trocar a chave exposta):**
1. Revogue a chave antiga no console da Anthropic, gere outra e ponha só no `.env`. Acrescente
   `AI_MODEL_ACTOR=claude-sonnet-5` e `AI_MODEL_VERIFIER=claude-haiku-4-5` (ou deixe vazio para comparar com Opus).
2. `pwsh -File scripts\stop.ps1 ; pwsh -File scripts\start.ps1 -NoBrowser`
3. `backend\.venv\Scripts\python.exe scripts\probe-models.py --yes` — o que cada modelo aceita e se o cache pega.
4. `pwsh -File scripts\eval-run.ps1 -Label opus-tudo -Yes` com `recipes: off` (linha de base) e depois
   `-Label sonnet-haiku+receitas` com a configuração atual. Mantém-se o que **não perder casos**; o resto volta ao
   valor antigo anotado em `config.yaml`. `scripts\usage-report.ps1` dá o US$ por função.

**Defeito de operação achado e corrigido no fechamento:** num `stop.ps1`, o backend fechou a porta mas o processo
**não saiu** (ficou vivo, sem porta, com as tarefas rodando) e o `start.ps1` seguinte criou um segundo backend sobre o
mesmo banco. Não reproduzi a causa exata (3 encerramentos seguintes saíram limpos em 4–5 s, inclusive com um cliente
WebSocket conectado), então o encerramento ficou à prova disso por construção: `timeout_graceful_shutdown=10` no
servidor, fechamento das sessões em paralelo e com prazo, o Appium e o banco são fechados mesmo se uma fase falhar,
saída forçada do processo 60 s depois do pedido de encerramento, e o `stop.ps1` agora espera o **processo** sair (o
`python.exe` do venv é um lançador; o interpretador é filho dele) e só então finaliza o que restar — sempre conferindo
que a linha de comando é a do backend. Observação para quem automatiza: `start.ps1` com a saída redirecionada
(`| Select-Object`, `*> arquivo`) fica preso até o backend morrer, porque o filho herda o *handle*; rode-o sem pipe.

### 7.5.1 Execução c4da09 ("bom dia para todos os contatos") — 0 de 2, ≈US$ 0,82
* **Causa:** o planejador criou `list_contacts` com pós-condição de *processo* ("a lista foi percorrida"); o
  verificador só via a tela final e nunca comprovava. A "recuperação" reexecuta o mesmo plano → 4 falhas idênticas por
  aparelho. Mais adiante o plano ainda tinha uma mega-etapa "repetir para os demais" (vários envios numa etapa).
* **Corrigido (testado com o provedor simulado; `tests/test_plan_defect.py`):**
  1. planejador: pós-condição = estado de UMA tela; um alvo por etapa com efeito; alvos nomeados viram sequências
     próprias; conjunto só conhecido pela tela ("todos os contatos") → `needs_input` pedindo a lista;
  2. veredito `unprovable` = defeito do plano: falha na 1ª verificação, sem nova tentativa nem reexecução do plano, e
     os aparelhos que ainda não começaram ficam retidos (`waiting_user`) sem gastar IA;
  3. `scroll` devolve `changed`/`at_end` (conteúdo da área rolada, medido no aparelho) e o verificador recebe os fatos
     registrados pelo executor — nunca a alegação do ator.
* **Não medido com o planejador real** (sem execuções pagas): casos `msg-todos-os-contatos` e
  `msg-dois-contatos-nomeados` entraram em `config/eval-set.yaml`.

### 7.5.2 Repetição sobre listas lidas da tela (coleta + `for_each`)
* **Plano:** uma etapa de coleta (`postcondition.kind = items_collected`) seguida de etapas-MODELO consecutivas com
  `for_each=<chave da coleta>` e `{item}` no texto. O limite de etapas vale para o modelo, não para as cópias.
* **Coleta determinística:** ferramenta `collect_list(element_id, item_selector, exclude)` — o EXECUTOR volta ao topo,
  lê e rola até o conteúdo parar de mudar (`at_end`), sem repetição. É fato medido no aparelho: 0 chamadas ao
  verificador. Lista vazia → nova tentativa; acima de `limits.for_each_max_items` (25) → bloqueia, nunca trunca.
* **Expansão:** ao comprovar a coleta, o scheduler cria a versão seguinte do plano com uma cópia do bloco por item
  (`open_conversation_i1…`), cada cópia com UMA ação de efeito, guarda de commit e "nunca reenviar" próprios. Não há
  dependência entre itens. As cópias compartilham a identidade da etapa-modelo → **a receita aprendida no 1º item
  serve aos demais** (e aos outros aparelhos); `{item}` entra como variável nos seletores.
* **Falha parcial:** item que falha (sem efeito disparado) pula só o resto dele; os demais seguem. No fim o objetivo
  fica `failed` com "N de M itens concluídos; falharam: …" — nunca sucesso — e "Tentar novamente" refaz só os que
  falharam, sem reenviar aos outros. Efeito disparado e não comprovado continua sendo `uncertain` (para tudo).
* **Recuperação** enxerga o plano já expandido e a expansão não consome a revisão automática. O prazo do objetivo
  cresce 240 s por item. Itens lidos da tela são DADOS: saneados (sem chaves/controle, 80 caracteres) e o ator é
  avisado de que `item` é só o nome do alvo.
* **Medido (provedor simulado, `tests/test_for_each.py`):** 5 contatos → 5 mensagens, uma por contato; do 2º contato
  em diante 0 decisões de IA; 2º aparelho roda a coleta e todo o bloco por receita; item com falha → 4 de 5 e
  retomada refaz só o que faltou. **Não medido com o planejador real.**
* **Medido em emulador real (provedor simulado, app de QA de verdade, execução `bc66a4`):** 2 de 2 aparelhos,
  8 contatos cada (a coleta achou "Suporte QA" no topo da lista, que o "inventário" da c4da09 teria perdido), **8
  mensagens por aparelho, 0 repetidas** pelo verificador independente (ContentProvider), 291 s, 64 de 70 etapas por
  receita. Achado no caminho e corrigido: a coleta deixava a lista rolada no fim e o 1º contato não era achado — agora
  ela devolve a lista ao topo.

### 7.6 Limitações e próximos passos
* `-lowram` marca o aparelho como de pouca memória; apps podem reduzir recursos. Se algum app-alvo sofrer, use
  `ram_mb: 2048` (≈2,7 GB reais) ou tire a flag (perde a economia e, na imagem Android 14, a hibernação).
* Tempo por conta no rodízio ≈30–40 s, dominado por acordar (≈16 s), abrir a sessão UiAutomator2 e observar a tela a
  cada ação. Próximo ganho barato: não capturar screenshot enquanto a etapa roda por receita (só na verificação).
* Aparelho externo (`instances.external`) foi implementado mas **não testado com celular físico**.
* Android em contêiner (Redroid) não foi tentado: o kernel do WSL desta máquina não tem `binder`
  (`CONFIG_ANDROID_BINDER_IPC is not set`); exigiria uma VM Linux no Hyper-V. Com o rodízio, a RAM deixou de ser o
  gargalo para 10 contas, então ficou como experimento opcional.
* Uma pasta temporária de sonda ficou em `data\avd-probe\` (alguns GB): pode ser apagada à mão.
* Emulador não tem SIM: SMS real e verificação de número pedem aparelho físico ou API. Multi-conta em emulador pode
  ser bloqueada pelas plataformas; o projeto não implementa evasão de detecção.

## 8. Domínio Instagram: o que está provado e o que não está (17/09/2026, terceira rodada)

Esta seção cobre as fases 0A–5 do plano do Instagram. Ela separa, de propósito, **o que foi medido num aparelho
real** do **que só foi exercitado em teste automatizado** — a diferença importa para decidir o que ainda pode
falhar quando o app de verdade entrar.

### 8.1 Medido no host e no emulador

| O quê | Como foi medido | Resultado |
|---|---|---|
| Vazamento de senha no log do Appium | `data\logs\appium.log` antes da correção | O PIN `1234` do app de QA aparecia **3 vezes em texto claro**, entre 102 digitações registradas |
| Fim do vazamento na origem | Appium subido com regra de mascaramento e digitação de um marcador conhecido | O texto digitado aparece como `**SECURE**`; o backend recusa operar se a regra não estiver ativa |
| APK como artefato de primeira classe | APK do QA renomeado para `instagram-latest-v999.apk`, importado pela pasta | Pacote, versão (`versionCode=1`), splits e assinatura vieram do **arquivo**, não do nome; instalou, abriu e o estado foi relido do aparelho |
| Cofre de credenciais | Cadastro pelo portal e varredura completa | A senha não aparece em resposta, banco, ciphertext legível, arquivo de chave, `backend.log` nem `appium.log`; apagar o perfil apaga o segredo |
| Portal mínimo | Navegador real | Aba Perfis, senha mascarada, sessão "não verificada", diálogo de cadastro com "Salvar e conectar" |

### 8.2 Exercitado em teste automatizado (não em aparelho real)

Máquina de estados do login (sessão pronta, credencial inválida, desafio, 2FA, conta errada, toque perdido, teto de
tentativas), persona/memória/histórico com isolamento entre dois perfis, catálogo de capabilities e composição do
plano, commit por seletor declarado, guarda de linha, limites por perfil, aprovação com três verbos e a porta de
política no despacho. São **213 testes de backend e 173 de frontend**, com um Instagram de mentira como fixture.

### 8.3 O que ainda não foi provado

* **O app real.** Sem o APK do Instagram em `apks/inbox`, os seletores do catálogo (`desc=Send`, `text=Follow`, …)
  são a melhor leitura da variante `en-US` — e só valem de fato depois de rodar contra o app instalado. É a
  pendência de maior risco, e por isso ela está cedo no plano.
* **O aceite de nível 1 ponta a ponta** (importar release → instalar → cadastrar perfil → autenticar → verificar
  `@mariana.costa91182` → `SESSION_READY`) depende do item acima e da troca da senha exposta no chat.
* **O aceite de nível 2** (uma interação social real entre as duas contas, com persona, memória e efeito único)
  depende de observar o efeito confirmado numa conta real. A geração **já está ligada ao motor** desde 18/09: o
  texto nasce em `draft_response`, por perfil, na porta de política — ver 8.4.
* **Contêiner de bundle** (`.apks`, `.apkm`, `.xapk`) entra como conjunto **não verificado** e só perde o rótulo
  depois de instalar e abrir; `bundletool` não está instalado nesta máquina.

### 8.4 Cada perfil escreve com a própria voz — medido em execução real (18/09/2026)

O defeito: na execução `r-20260918181035-7bfa38`, quatro perfis com personas opostas comentaram **a mesma frase,
byte a byte** (`"O secretário faz um trabalho excelente!"`), porque o texto vinha congelado no plano — um plano só
para N aparelhos, copiado igual por `materialize`.

A correção tem quatro partes: o planejador grava a **intenção** (`content_brief`) em vez do texto; a porta de
política gera o texto **por perfil** antes de digitar, com persona, memória e relacionamento; quem gera recebe a
lista do que **não pode repetir** (irmãos da mesma execução e textos anteriores do próprio perfil), com uma
segunda tentativa quando a saída repete assim mesmo; e os N rascunhos aparecem juntos na aba **Textos** da
execução, para leitura e decisão em lote.

Medido na execução `r-20260918224233-987905` (modelo real, três aparelhos, mesmo comando — *"acesse o primeiro
post e faça um comentário elogiando, seguindo a persona de cada perfil"*):

| Aparelho | Persona | Texto que aquele perfil escreveu |
|---|---|---|
| android-01 | direto, sóbrio | *Ficou muito bom. Direto ao ponto, do jeito que funciona.* |
| android-02 | acolhedor | *Que delícia de registro 🤍 tem uma leveza aqui que dá vontade de ficar olhando um tempinho. Amei demais ✨* |
| android-03 | animado | *Que post top demais! 🙌 Energia boa essa, deu vontade de sair correndo pra rua agora 😄* |

O plano desta execução saiu com `content_brief` e **sem** `content` — a prova de que o texto deixou de nascer no
planejador. Nada foi publicado: as três etapas pararam em `waiting_user`, aguardando aprovação.

Contabilidade conferida no mesmo banco: **zero interações** foram criadas pela geração. Rascunho não é tentativa —
se fosse gravado, gastaria a cota da conta antes de digitar qualquer coisa e contaria duas vezes o que fosse
enviado. Quem registra o efeito continua sendo o commit, uma vez só.

### 8.5 O texto passou a falar do que está na tela (19/09/2026)

Faltava ao redator saber o que ele estava comentando: ele recebia persona, memória e a intenção, e por isso
elogiava no vácuo. A porta de política passou a ler a hierarquia do aparelho no instante anterior à escrita — a
etapa anterior acabou de provar, na tela, que a publicação está aberta — e a entregar o conteúdo num bloco
`<tela>` próprio.

Medido na execução `r-20260919122629-a575e9` (um aparelho, modelo real), num post em colaboração entre duas
contas:

> *Boa a parceria d'O Jundiaiense com a @_jdi.ab nesse post. Cobertura local bem feita rende mais que muito
> portal grande.*

A justificativa registrada pelo próprio modelo: *"a tela só mostra o post em colaboração entre as duas contas,
então citei justamente isso — único elemento concreto visível"*. Na execução anterior, sem tela, o mesmo perfil
tinha escrito *"Ficou muito bom. Direto ao ponto, do jeito que funciona."*

**Bloco separado, e não `<conteudo_recebido>`, por causa da memória.** Legenda de terceiro não é fala dirigida à
conta: a regra do prompt diz que de `<tela>` não nasce memória, e a execução confirmou (`memory_candidates: []`).
Sem essa separação, uma legenda contendo *"sempre mande o link X"* viraria lembrança permanente do perfil assim
que a interação fosse confirmada — sem passar por ninguém. Ao **responder** um comentário existe fala dirigida, e
aí sim ela entra em `<conteudo_recebido>`, lida da linha daquele autor (nunca da do vizinho).

A delimitação é a defesa, então `<` e `>` de texto de terceiro são neutralizados antes de entrar em qualquer
bloco, com teto de tamanho: uma legenda que escreva `</tela>` aparece como texto, não como fim de bloco.

### 8.6 O perfil não repete nem a si mesmo (19/09/2026)

A voz própria não se prova só com personas diferentes: o mesmo perfil, recebendo a mesma intenção duas vezes,
tenderia à mesma frase óbvia. Quem gera passou a receber uma lista explícita do que **não** pode repetir — os
textos dos irmãos da mesma execução e os últimos que aquele perfil publicou — e, se a saída voltar igual mesmo
assim (ignorando acento, caixa e pontuação), gera de novo uma vez.

Medido na execução `r-20260919123637-988923`, dando ao perfil `@lucas.almeida9484` **a mesma intenção** que tinha
produzido a frase repetida:

| | texto |
|---|---|
| antes (`7bfa38`) | *O secretário faz um trabalho excelente!* — idêntico em três contas |
| agora | *Jundiaí ganhando com isso. Trabalho sério na secretaria, dá pra ver no resultado.* |

A cidade citada não veio do comando: veio da tela (`Jundiaí, São Paulo, Brazil`), o que mostra as duas peças
funcionando juntas.

### 8.7 O que a revisão adversarial encontrou (19/09/2026)

Cinco revisores independentes leram o conjunto da mudança por dimensões diferentes (integridade da aprovação,
injeção de prompt, concorrência e custo, dados e migração, interface). Cada achado foi entregue a um cético
encarregado de **derrubá-lo** lendo o código; na dúvida, o veredito era "não é real". Dos 35 levantados,
**21 se confirmaram** e 14 caíram — entre os que caíram, um "limite de 50 na lista" inalcançável (o teto real é
uma aprovação por aparelho) e uma suposta corrida de respostas fora de ordem, impossível num backend de
processo único.

**Ressalva de processo, que vale registrar.** Os revisores trabalharam no mesmo diretório e escreveram
arquivos de reprodução próprios; três deles (`test_tmp_repro_recovery_draft.py`, `test_zz_repro_tmp.py`,
`test_aprovacao_orfa_repro.py`) entraram em commits meus por um `git add -A`, e uma correção de produção de um
deles (a guarda `blocked_kind IS NOT 'approval'`) entrou junto, sem revisão. Os três arquivos foram removidos
depois, cada um em seu commit; a correção foi mantida, reconhecida na mensagem do commit e coberta por um teste
escrito para ela. O mecanismo é o `git add -A` com agentes trabalhando no mesmo diretório — o remédio é
adicionar por caminho.

Os confirmados, todos corrigidos, com teste que falha sem a correção nos que dava para provar assim:

| O que estava errado | Por que importava |
|---|---|
| A porta regenerava o texto depois da aprovação | A pessoa aprovava uma frase e o aparelho digitava outra |
| …e a guarda não cobria rascunhos anteriores à coluna | Eram justamente os que estavam esperando decisão |
| **Aparelho sem perfil publicava texto inventado** | Sem perfil a porta saía cedo. Isso era inofensivo enquanto o texto vinha congelado; sem ele, o ator inventava a frase e publicava — sem rascunho, sem aprovação, sem guarda |
| **Ler a tela podia prender o aparelho 12 minutos** | O teto de 15 s cancelava a subida da sessão no meio, e o aparelho ficava "starting" para sempre |
| **A anti-repetição se anulava na frota** | Oito aparelhos liam juntos a lista de irmãos vazia e voltavam com a mesma frase: o defeito original, de volta |
| **Revisão de plano deixava duas aprovações iguais** | Editar a antiga gravava o texto numa etapa morta, e o aparelho digitava a outra |
| **Rejeitar não era definitivo** | A recuperação recriava o alvo recusado, com outro texto e nova aprovação |
| `<intencao>` e `<tarefa>` aceitavam marcação de terceiro | Numa repetição sobre lista, `{item}` vem da tela: um comentário hostil fechava o bloco que o prompt declara ser a única autoridade |
| Memória e histórico entravam crus no prompt | É o caminho **durável**: uma injeção feita uma vez valeria para todas as gerações seguintes daquele perfil |
| Candidato a memória era aceito sem fala dirigida | Regra de prompt é pedido, não garantia — legenda de terceiro viraria fato permanente do perfil |
| Escrever corria fora do orçamento de IA | Sem limite de simultâneas, sem teto conferido antes de gastar e sem as três tentativas |
| O histórico próprio sumia da lista anti-repetição | O limite era aplicado antes de filtrar: algumas curtidas recebidas bastavam para o perfil repetir o de ontem |
| Aprovar com texto era aceito calado | Ia para `approved_content`, voltava no 200, e o aparelho digitava outro |
| Quebra de linha deixava duas guardas contraditórias | Commit rejeitado até a etapa morrer, depois de aprovada, sem pista |
| Caixa esvaziada virava "aprovar o texto original" | O aparelho digitaria exatamente a frase que a pessoa apagou, sem sinal na tela |
| Trocar de aba apagava as edições; ação sem texto ganhava caixa | Perder oito textos reescritos; e o que fosse digitado numa ação sem texto viraria guarda impossível, matando a etapa |

Cinco deles eram **regressões abertas por esta própria série** — o preço de tirar o texto do plano — e é por isso
que a revisão valeu mais do que os testes que eu já tinha escrito: nenhum deles olhava para o que acontece
quando oito aparelhos entram na porta ao mesmo tempo, ou quando o aparelho não tem perfil.

**A correção de uma delas nasceu larga demais, e a suíte pegou.** "Rejeitar não era definitivo" foi corrigido
tratando toda etapa `cancelled` como fronteira decidida — mas `cancelled` tem duas origens. Além da recusa de
quem aprova, `_skip_failed_item` cancela o resto de um item que falhou, e essa precisa voltar: "Tentar
novamente" passa pelo mesmo `recovery_steps`. Com a fronteira larga, o contato que falhava nunca mais recebia a
mensagem, nem quando a pessoa mandava tentar de novo (`test_item_que_falha_nao_trava_os_demais_e_nunca_vira_sucesso`,
1 falha em 340 na execução completa da suíte). A fronteira passou a ser só a recusa, reconhecida por
`MOTIVO_REJEICAO` — que deixou de ser frase de tela e virou vocabulário, montado pelo próprio
`cancel_target_steps`. O teste novo cobre as duas metades.

**Quais correções foram provadas desligando-as.** Não bastava o teste passar: a guarda de "aparelho sem
perfil", a expiração da aprovação na revisão de plano, a fila de escrita por execução, a marca de rascunho
fechado, a porta de `for_step` e o roteamento pelo orçamento de IA foram desligadas uma a uma, com o teste
correspondente falhando pelo motivo esperado antes de voltarem. A fila de escrita não tinha teste nenhum até
aqui — só a execução em série é que a fazia parecer correta.

**O que a fila custa.** Dentro de uma execução, escrever virou um de cada vez. Com oito aparelhos e alguns
segundos por geração, o último espera os outros escreverem antes de digitar. É o preço de `<nao_repita>` existir
de verdade: a lista é lida do banco, e em paralelo todos a leem vazia. Execuções diferentes seguem em paralelo.

**O primeiro deles** merece registro à parte: a porta é atravessada de novo quando o objetivo é retomado — e
retomar é o que aprovar faz. Sem marca de "rascunho fechado", a segunda passagem gerava outro texto por cima do
que a pessoa tinha lido e aprovado; a aprovação liberava a etapa e o aparelho digitava uma frase que ninguém
viu. O teste que trava isso atravessa o gate duas vezes com uma edição aprovada no meio, e falha sem a guarda.

### 8.8 A aprovação que chegou tarde demais (19/09/2026)

O defeito mais sério desta série não apareceu em revisão nenhuma: apareceu no banco real, depois que alguém
decidiu as aprovações pendentes.

Às 13:12–13:13 as **seis aprovações** pendentes foram decididas no portal (sem nota, uma a cada 9–28 s). Os três
aparelhos estavam online, e o scheduler chegou a assumir cada um deles — "IA assumiu o aparelho". Mesmo assim
**nada foi publicado**: **cinco** etapas `CREATE_COMMENT` morreram no mesmo segundo em que o objetivo voltou à
fila, com *"Tempo total do objetivo esgotado"*. (A sexta, do `android-08`, era de uma execução já cancelada e o
objetivo foi direto para `cancelled` — caso diferente, abaixo.)

A causa: `objective_timeout_s` (900 s, 15 min) conta desde `started_at`, e o tempo parado esperando uma pessoa
decidir nunca era creditado. `paused_s` existe exatamente para isso e já cobria o tempo represado por limite de
perfil — a espera humana ficou de fora. Na prática, **qualquer aprovação decidida mais de 15 minutos depois do
início do objetivo era aceita e descartada em silêncio.** É o oposto do que os três verbos prometem: a decisão
da pessoa não mudava nada.

Como os rascunhos daquelas execuções já tinham sido aprovados e as etapas foram canceladas, aqueles seis textos
não são mais recuperáveis: "Tentar novamente" recria as etapas numa versão nova do plano, sem `draft_meta`, e
cada aparelho **escreve um texto novo**, que precisa de nova aprovação. Carregar um rascunho aprovado através
de uma revisão de plano é uma decisão de projeto em aberto, não feita aqui.

Junto veio uma frase que mentia: ao matar o objetivo, as etapas abertas eram canceladas sempre com *"etapa
anterior falhou"* — inclusive quando nenhuma etapa tinha falhado e o que estourou foi o prazo. É o que as cinco
etapas do banco real dizem até hoje. Passou a registrar o motivo real.

**Um defeito menor apareceu no mesmo lote, não corrigido aqui:** a sexta aprovação foi oferecida para decisão
mesmo pertencendo a uma execução já cancelada. Decidi-la não causou dano (o objetivo foi direto para
`cancelled`), mas ela não deveria estar na lista: cancelar uma execução cancela as etapas abertas e não expira
os pedidos de aprovação pendentes, como `revise_plan` passou a fazer. `ApprovalStore.expire_for_objective`
existe e hoje não tem nenhum chamador.

### 8.9 O que ainda não foi exercitado num aparelho

* **Responder um comentário** (`REPLY_COMMENT`). A leitura da fala do autor (`comentario_de`) está coberta por
  teste, inclusive no caso em que ela se recusa a adivinhar, mas nenhuma execução real chegou a esse caminho.
* **`memory_items` continua em zero** no banco real, e continuará até um efeito de resposta a comentário ser
  confirmado: comentar uma publicação não gera candidato nenhum, por desenho.
* **Nada foi publicado.** As seis aprovações das execuções de validação foram decididas em 19/09 às 13:12, mas
  nenhuma chegou à tela do aparelho — ver 8.8. Não há nenhum comentário desta série publicado no Instagram.

**Repetido com as oito contas (19/09/2026).** A mesma mensagem recebida (*"acabei de ver seu último post, ficou
muito bom!"*) foi apresentada aos 8 perfis pela rota de prévia da persona — que não toca em tela nem grava
interação. Saíram **8 respostas distintas**, cada uma reconhecível pela persona declarada:

| Perfil | Tom declarado | Trecho do que escreveu |
|---|---|---|
| @lucas.almeida9484 | direto e sóbrio, sem firula | *valeu, Thi. saiu como eu queria dessa vez.* (minúsculas, sem emoji) |
| @mariana.costa91182 | acolhedor e caloroso | *Aaah que bom que você gostou! 🤍 … Faz o meu dia ✨* |
| @felipe.nogueira93762026 | analítico, pergunta de volta | *achei que tinha ficado meio denso demais, tipo log de stack trace… teve alguma parte que te pegou mais?* |
| @juliana.mendes9056 | firme, frases curtas que fecham ideia | *Obrigada, Thiago. Fico feliz que tenha feito sentido para você.* |
| @thiago.moreira4827 | descontraído, academia | *jurava que ia ficar torto igual meu agachamento kkkk 🏋🏾* |

O contraste entre a primeira linha e a última é o que o defeito original apagava: mesma pergunta, mesma tarefa,
oito vozes. Uma quarta capability que escreva sem declarar o tipo do texto sairia com voz de mensagem privada num
comentário público — isso agora é erro de teste (`test_toda_capability_que_escreve_declara_o_tipo_do_texto`).

## 9. Ciclo de vida de release (Fase 6) — 18/09/2026

### 9.1 O que está exercitado em teste

Canário num aparelho só; quarentena automática quando o canário não instala ou não abre; promoção que **exige a
última prova de cada etapa registrada e bem-sucedida** no aparelho do canário; dois aparelhos em versões diferentes
sem um mexer no outro; rollback preservando os dados; rollback recusado pelo Android que **não destrói nada** e fica
nomeado (`drift_kind='downgrade_refused'`); rollback destrutivo que desinstala antes; e a matriz de invalidação
ligada de fato — instalar, atualizar, reverter ou reinstalar leva a sessão para `unknown`, e a verificação seguinte
termina em `session_ready` **sem digitar nada** quando o Instagram preservou o login.

Dois defeitos reais apareceram só quando o teste juntou release e sessão no mesmo banco, e foram corrigidos:

1. **O alvo do rollback era destruído pela tentativa que falhava.** `previous_release_id` era gravado antes da
   instalação; um rollback recusado pelo Android virava o "anterior" de si mesmo, e a segunda tentativa reinstalava
   a versão errada. Agora o valor é lido antes e gravado só depois de o disco mudar.
2. **O motivo da invalidação ficava congelado no primeiro evento.** Como a invalidação pulava sessões já `unknown`,
   o painel continuava dizendo "o app foi atualizado" depois de o app ter sido desinstalado e reinstalado. Agora o
   motivo é reescrito sempre; o contador de retorno é que continua contando só quem mudou de estado.
3. **Filtrar splits quebraria toda verificação seguinte.** `verify_on` montava o conjunto esperado a partir de
   *todos* os arquivos da release. Depois de uma instalação filtrada, ele acusaria `split_mismatch` por um split
   que de propósito não foi instalado — e a porta do app bloquearia o aparelho por uma decisão nossa. A escolha
   passou a ser gravada em `device_app_state.expected_splits` e é ela que a verificação usa.
4. **A sessão só era invalidada no fim do caminho feliz.** Uma divergência de versão interrompia o método antes
   disso, e a sessão continuava dizendo "verificada" sobre um app já substituído no disco. O gancho passou para o
   instante em que o `install` retorna.
5. **A janela entre desinstalar e reinstalar não era reconciliável.** O rollback destrutivo desinstalava sem marcar
   operação pendente; uma queda no meio deixaria a linha como "pronta" sobre um aparelho sem o app, e a
   reconciliação de partida (que só olha `pending_op`) nunca a veria.

### 9.2 O que **não** está provado

* **Escolha de splits por densidade, ABI e idioma.** `select_splits` é função pura, com testes sobre nomes
  sintéticos (`config.xhdpi`, `config.en`, `config.arm64_v8a`, splits de funcionalidade, nomes desconhecidos) e as
  regras são conservadoras: o que não é reconhecido nunca é descartado, e se nenhum candidato de uma categoria
  serve, entram todos. **Isso não foi exercitado contra o conjunto real do Instagram.** Enquanto o APK real não
  passar por aqui, tratar como não comprovado — `select_for_device=False` instala o conjunto inteiro e é a saída se
  algo der errado.
* **O comportamento real do downgrade.** Que `adb install -r -d` funcione, seja recusado, ou exija desinstalar
  varia conforme o build do Android. O fake cobre os dois desfechos porque o sistema não pode depender de ter dado
  sorte; qual deles acontece no emulador desta máquina, com o APK real, ainda não foi medido.
* **Promoção não instala nada.** `promoted` quer dizer "provou que abre", e `promoted_release()` devolve a maior
  versão promovida. Não existe implantação automática no parque: cada aparelho só muda quando alguém pede.

## 10. A loja (Play Store) como fonte do aplicativo — 18/09/2026

Topologia: um emulador extra, com imagem Play Store e logado na conta Google do usuário, serve só de repositório.
O Instagram é instalado ali pela loja oficial; o backend copia o conjunto desse aparelho, passa pelo pipeline de
release que já existia e distribui ao parque. O sistema continua sem baixar APK de lugar nenhum.

### 10.1 Exercitado em teste automatizado

Declaração da loja e validação dos overrides na carga (chave errada deixou de ser ignorada em silêncio); o papel
`store` — nunca recebe tarefa, perfil nem ação em lote, nunca é despejada e conta como vaga, não abre sessão de
automação, e o painel recusa TEXTO nela; `adb pull` restrito a APK instalado (armazenamento do usuário, banco do
app, travessia e injeção cobertos); busca que cataloga com origem `store`, não copia nada quando a versão já está no
catálogo e importa só a própria subpasta; distribuição que exige versão promovida, instala já nos ligados e deixa
pendente nos desligados; a porta do app instalando ANTES da tarefa; entrega que falha sem virar laço; objetivo em
andamento sem troca de app; e "instalar em todos agora" percorrendo um parque de 3 com **pico de 1 aparelho
ligado**. Backend: 293 testes; frontend: 201 — os dois medidos também a partir de um checkout limpo.

Dois defeitos antigos apareceram no caminho e foram corrigidos:

1. **"Voltar" apagava o perfil.** Em Perfis, `remover()` testava o objeto devolvido por `confirm` (`{confirmed,
   note}`, sempre verdadeiro) em vez de `confirmed`. Desistir no diálogo apagava o perfil e a credencial do mesmo
   jeito. Nenhum teste pegava porque nenhum renderizava o host do diálogo — o fluxo de remover nunca tinha rodado em
   teste.
2. **Override com chave errada era ignorado.** `instance_android` usa `model_copy(update=)`, que não valida nada:
   `hibernacao: false` não fazia efeito e o aparelho subia com o padrão, sem aviso.

### 10.2 O que **só se descobre medindo**

Nada disto roda em fixture. Em ordem de risco (as respostas medidas estão na §10.3):

1. **Qual ABI a Play Store entrega a um AVD x86_64.** Se vier só `arm64-v8a`, a compatibilidade fica `uncertain` e
   quem decide é a sonda de abertura do canário.
2. **Se a janela do emulador aparece** com o backend iniciado por `Start-Process -WindowStyle Hidden`.
   `CREATE_NO_WINDOW` só suprime console, então deve aparecer; se não, o plano B é `scripts\loja-janela.ps1`.
3. **`adb pull` de `/data/app` numa imagem de loja** (build `user`, sem `adb root`). Funciona para apps comuns.
4. **O Instagram num emulador SEM Play Store:** se abre e faz login em `google_apis`, o que a Play Integrity faz, e
   se módulos sob demanda do bundle fazem falta. É para isso que o canário existe: descobre-se em 1 aparelho.
5. **Os splits reais** — primeira prova de `select_splits` fora de nomes sintéticos (pendência da §9.2).
6. RAM real da imagem de loja e o valor de `PlayStore.enabled` num AVD criado por linha de comando.

O que não é do sistema fazer, e ele não faz: digitar a conta Google, resolver verificação em duas etapas, tocar em
Instalar na Play Store e aceitar licenças. São ações da pessoa, na janela do emulador.

### 10.3 Medido na operação real (18/09/2026)

Loja `android-11` (android-34 · `google_apis_playstore` · x86_64), login e instalação feitos pelo usuário na janela
do emulador; o resto pelo portal e pela API.

| Pergunta (§10.2) | Resposta medida |
|---|---|
| 1. ABI entregue pela loja | **x86_64** (`native-code: 'x86_64'`). Sem tradução de arm64: compatibilidade `ok`, não `uncertain`. |
| 2. A janela aparece com o backend oculto | **Aparece.** O plano B (`loja-janela.ps1`) não foi necessário. |
| 3. `adb pull` de `/data/app` em build `user` | **Funciona**, sem root. |
| 4. Instagram em `google_apis` (sem Play Store) | **Abre** até a tela de entrada (Bloks, em inglês). O classificador reconhece o formulário real — usuário, senha (`password=true`) e "Log in" — sem ajuste de seletor. O campo de usuário vem preenchido com o número falso do emulador; o fluxo já limpa e confere antes de digitar a senha. Login ainda não exercitado. |
| 5. Splits reais | `base` (237,8 MB) + `config.xhdpi` (5,3 MB). `select_splits` manteve os dois num aparelho xhdpi; esperado = observado, sem falso desvio. |
| 6. RAM e `PlayStore.enabled` | Loja: **4,0 GB de working set** (4,8 GB privados), acima dos 3,7 GB estimados — o override passou a 4100. Aparelhos do parque: 2,3–2,8 GB. O `avdmanager` grava `PlayStore.enabled=no` mesmo com imagem de loja; o `apply_hardware` força `yes`. |

Versão obtida: Instagram **447.0.0.55.81** (versionCode 385311929), minSdk 28, targetSdk 36, origem `store`.

Quatro defeitos apareceram na operação real — nenhum deles alcançável por fixture:

1. **Assinatura com rotação de chave (APK Signature Scheme v3.1).** O `apksigner` do Instagram lista um signatário
   por faixa de SDK (`Signer (minSdkVersion=…, maxSdkVersion=…) certificate SHA-256 digest`) e ainda um *Source
   Stamp* da loja. O inspetor só conhecia `Signer #1` e recusou a busca ("não foi possível ler a assinatura"). Agora a
   identidade é a do signatário da faixa mais nova — a que os aparelhos atuais verificam — e o Source Stamp nunca é
   tomado por signatário (senão a Play Store viraria "dona" de todo app vindo dela).
2. **Sonda de abertura com falso negativo.** O primeiro canário instalou certo e foi para a quarentena na prova de
   abertura: `foco: (None, None)`. O Instagram não tinha falhado — o Android registrou o primeiro quadro **25,4 s**
   depois de abrir (`wm_activity_launch_time … 25449`), e a sonda olhava uma única vez, aos 6 s. Medido em seguida,
   a frio: ~8 s sem janela em foco, a tela principal, ~2 s de novo sem foco na troca para a tela de entrada, e a
   tela de entrada. A sonda agora amostra até um prazo de 90 s, trata foco nulo como transição, espera o launcher
   sair da frente e só falha cedo quando o app **apareceu e depois saiu** do primeiro plano. O `(None, None)` da
   mensagem era um segundo defeito: `current_focus` devolve tupla, os aparelhos de mentira devolviam texto, e a
   comparação funcionava por coincidência — os de mentira agora seguem o contrato real.
3. **"Aprovar assinatura" não sumia depois de aprovada** (portal). Virou selo "Assinatura aprovada".
4. **A suíte de testes apertava HOME no emulador real.** A segunda prova, já com a sonda nova, falhou de outro
   jeito: o app abriu, e 20 s depois o launcher voltou à frente por uma tecla HOME. Não era o Instagram nem a
   sonda — a suíte rodava em paralelo, o harness usava as portas padrão (o "android-01" dos testes era o
   `emulator-5554` ligado na máquina), e o teste de ação em lote mandava `home` por `quick_key`, que ia pelo adb real
   em vez do aparelho falso. Confirmado rodando só aquele teste e vendo o HOME chegar ao emulador. Correção dupla:
   `quick_key` passa pelo mesmo caminho da entrada manual, e o harness usa portas a partir de 5640 — comando que
   escape do aparelho falso cai num serial inexistente. A primeira prova também tinha levado um HOME desses, 40 s
   depois de abrir, pelo mesmo motivo.

Com as correções, o terceiro canário em android-01 passou: instalou e o app chegou ao primeiro plano em 18 s e
permaneceu. A versão foi promovida com essa prova e distribuída com "instalar em todos agora".

**Distribuição medida (10 aparelhos, 4 vagas, uma delas ocupada pela loja):** 9 de 10 prontos em ~11 min, três
por vez, com a prova de abertura em cada um (15–18 s até o primeiro plano). O rodízio acordou e hibernou os
aparelhos sozinho. **android-02 falhou** de um jeito que só aparece no parque: o snapshot foi recusado, o boot a frio
levou 109 s e, 35 s depois de ficar online, ler o perfil do aparelho (`getprop` da ABI) passou de 30 s — sobrecarga
pós-boot, antes de qualquer mudança no disco. A regra anti-laço fez o que devia: `install_failed` nomeado, nenhuma
nova tentativa sozinha. Pedir a distribuição de novo (a nova tentativa explícita do desenho) rearmou só ele, que
instalou e passou na prova em 15 s. Resultado: **10 de 10 em `ready`**, `expected_splits` = `observed_splits`.

Operação: logo depois do primeiro boot da loja, "Abrir página na loja" deixou a Play Store em branco — os serviços
Google ainda se preparavam. Fechar e reabrir a Play Store pelo launcher resolveu; vale esperar alguns minutos depois
do primeiro login antes de abrir a página do app.

## 11. Item 7.4 — Bateria de avaliação (23/09/2026)

Continuação da seção 7.5: aqui a condição que faltava ("linha de base × configuração atual") ainda **não foi
cumprida** — exige execuções pagas com o provedor real, que esta chamada não fez (achado #98: parte deste item
exige autorização e orçamento do dono). O que ficou pronto, sem gastar nada:

**Código e teste, exercitados de verdade:**
- `min_cache_tokens` (achado #100) agora é DECLARADO por modelo em `ai.models` (`config.py`, `config.yaml`):
  Opus 5 = 512, Sonnet 5/Opus 4.8 = 1024, Haiku 4.5 = 4096 (platform.claude.com/docs, "prompt caching" — não é
  monótono entre gerações). O provedor já respeitava o campo (`anthropic_provider.py:151`); só faltava a
  declaração. Prova: `test_ponto_de_cache_respeita_o_minimo_declarado_por_modelo` (o `cache_control` não entra
  no pedido abaixo do mínimo do modelo alvo) e `test_capacidade_desconhecida_e_conservadora` (valor lido por
  `Config.model_caps`). **Conclusão do achado (prefixo do verificador ≈ 1 mil tokens, abaixo dos 4096 do
  Haiku):** o `cache_control` no verificador em Haiku 4.5 é hoje inerte — cada verificação paga 100% da
  entrada. Decisão registrada (não é preciso mover o prefixo estável: só compensaria acima de ~4096 tokens, e o
  Haiku sem cache ainda sai mais barato que Opus com cache neste tamanho de entrada).
- Achado #99: teste de ORÇAMENTO POR TOKENS de ponta a ponta (`test_orcamento_de_tokens_da_execucao_bloqueia_
  chamada_seguinte_e_aparece_no_painel`, `backend/tests/test_estados_de_ia.py`), com provedor simulado — sem
  rede, sem aparelho. Revelou um defeito real: o teto de tokens (e o teto de chamadas por objetivo) recusava a
  chamada ANTES do laço de tentativas e nunca virava linha em `ai_calls` — `/api/usage` não mostrava NADA sobre
  o estouro (`errors_by_kind` ficava vazio), embora a etapa já tivesse parado por causa dele. Corrigido em
  `executor.py` (`_registrar_orcamento_estourado`): a recusa por orçamento agora grava a mesma linha de erro que
  qualquer outra falha de IA, sem custo (0 tokens, calls=1 "tentativa recusada"). Os demais pontos do achado
  (recusa não consome tentativa em decidir/verificar/planejar/social, fallback de recusa vira `usage`) já
  estavam cobertos por `test_estados_de_ia.py` e `test_hub_de_ia.py` (itens 7.2/7.3) antes desta chamada.
- Achado #98: `config/eval-set.yaml` ganhou os dois primeiros casos de Instagram (`ig-abrir-perfil`,
  `ig-abrir-conversa`) — navegação sem efeito externo (sem seguir, curtir, comentar ou mandar mensagem), para
  cobrir o uso real dos modelos baratos (100% Instagram desde a troca; a bateria era 100% QA Messenger). Exigem
  uma instância já amarrada ao Instagram com conta de TESTE (`UPDATE instances SET app_id='instagram'`) — não
  rodados: sem essa instância disponível nesta chamada, e é decisão do dono qual conta serve de teste.
- `scripts/probe-models.py` ganhou `--repeticoes` (mais chamadas por função, revela repetição de erro além do
  cache) e `--sem-fallback` (desliga o endpoint beta `fallbacks="default"`), para comparar a taxa de HTTP 500 do
  verificador em Haiku 4.5 (~10% na noite da troca, achado #98) com e sem o beta — ainda não rodado (gasto real).
- `scripts/eval_rejudge.py` (novo) + `scripts/eval-rejudge.ps1`: rejulga com Opus 5, por imagem, as capturas de
  verificação que o Haiku já julgou (lidas de `evidence`, sem tocar o parque) e mede a concordância dos
  veredictos — pronto para rodar, não executado (gasta tokens de verdade por imagem; a lógica de extração do
  veredito do Haiku e de montagem do contexto tem teste próprio, sem rede: `scripts/tests/test_eval_rejudge.py`).

**Bloqueado por decisão/orçamento do dono, não por dificuldade técnica** (rule 6 desta chamada: sem chamada paga
ao provedor real, sem tocar o backend de produção ou o parque de emuladores):
1. `probe-models.py --yes` (com e sem `--sem-fallback`) — o que cada modelo aceita e se o 500 do Haiku se repete.
2. `eval-run.ps1 -Label opus-tudo -Yes` (linha de base) × `-Label sonnet-haiku+receitas -Yes` (configuração
   atual) — a comparação "sucesso comprovado × US$ por caso" que a seção 7.5 já apontava como não medida.
3. `eval_rejudge.py --yes` sobre as capturas reais guardadas desde a troca (achado cita 56).
4. Com o resultado dos três acima: decidir a alavanca (manter Sonnet/Haiku ou voltar a Opus, por função) e
   corrigir o comentário de `.env.example:11-12` — hoje ele diz "AINDA NÃO MEDIDA", e continua sendo verdade.


## 12. Fase 8 — voz da persona e memória de DM (23/09/2026)

Dois itens do plano-100: **8.1 (personas)** e **8.2 (memória de DM)**. O que mudou no código, o que está
provado por teste e o que continua dependendo de uma decisão ou de um gasto do dono.

### 12.1 A voz voltou a ser da persona (item 8.1)

* **O briefing não manda mais no tom.** `PLANNER_CAPABILITY_SYSTEM` pedia em `content_brief` "o que dizer, o tom
  e o que não dizer", e nas execuções `7cfa59` e `c49187` o planejador escreveu *"tom cordial e breve"* sem que o
  comando pedisse — o mesmo tom para os oito aparelhos. Agora o briefing é só CONTEÚDO, e tom só entra quando o
  próprio comando pedir um.
* **Desempate explícito.** `SOCIAL_SYSTEM` ganhou a regra que faltava: em conflito entre `<intencao>` e a
  persona sobre tom, humor, formalidade, tamanho ou emoji, **vale a persona**.
* **Mensagem simples deixou de virar crônica da tela.** A instrução "Fale do que está aí: cite o que se vê"
  valia para qualquer texto com tela à vista — foi o que transformou *"falando boa tarde"* em mensagens de 135 e
  159 caracteres descrevendo a página do destinatário. Agora ela é de COMENTÁRIO; em DM o bloco `<tela>` entra
  como contexto, com instrução de não descrever a tela.
* **O portal avisa quando falta voz.** `PersonaDTO.voice_gaps` é calculado da mesma lista de traços que vai ao
  prompt (`models.PERSONA_VOICE_TRAITS`), e a aba Persona mostra um aviso com o que falta — **e os campos para
  preencher**, que antes não existiam na tela (só tom, formalidade, tamanho e emoji eram editáveis).
* **A prévia aceita intenção.** `POST /api/personas/{id}/preview` só aceitava `incoming` (responder). Passou a
  aceitar `brief`, `screen` e os tipos `dm_initiate`/`post_comment`: é o que permite comparar as oito personas
  sob a MESMA intenção sem gastar uma execução em aparelho.

**Não feito, por decisão/gasto do dono:**

1. **Preencher as oito personas.** A proposta de voz está em `scripts/personas-voz.json` (os oito campos vazios
   em todas: gírias, estilo em DM, estilo em comentário, com conhecidos, com desconhecidos, expressões comuns,
   expressões proibidas e exemplos), e `scripts/personas_completar.py` aplica **só o que estiver vazio**, casando
   pelo nome da persona. Sem `--aplicar` ele não escreve nada. O conteúdo é proposta: quem aprova a voz de cada
   conta é o dono. O arquivo é validado contra o modelo da API por teste
   (`tests/test_social_dm.py::test_proposta_de_voz_cobre_os_oito_campos_e_usa_nomes_que_existem`).
2. **A prova antes/depois com a mesma intenção nos 8 perfis.**
   `python scripts/personas_completar.py --prova "dar boa tarde"` roda a mesma intenção em todas as personas pela
   rota de prévia (não publica, não grava interação, não toca aparelho) e imprime a tabela pronta para colar
   aqui. **Não rodada:** é uma chamada paga de IA por persona, e o backend de produção não foi tocado nesta
   chamada. O procedimento é: rodar → `--aplicar` → rodar de novo → colar as duas tabelas nesta seção.

### 12.2 O perfil finalmente ouve (item 8.2)

O uso real do dono é DM, e nesse caminho o perfil nunca ouvia: `SEND_MESSAGE` era sempre `dm_initiate`, sem
`incoming`; nenhuma interação de entrada era gravada; o rascunho ia sem chave de conversa, então
`<resumo_da_conversa>` nunca chegava a quem escrevia; e os candidatos a memória eram descartados em código por
falta de fala dirigida. Resultado medido em 20/09: `memory` vazia nos oito perfis e **29 interações, 100% de
saída**.

Agora, os quatro elos:

1. **Ler a conversa grava o que a outra pessoa disse.** A etapa `READ_MESSAGES` entrega os itens lidos ao
   domínio social (`scheduler.on_items_collected` → `state._registrar_leitura` → `social.record_inbound`), que
   grava `dm_received`/`inbound`/`confirmed` com `thread_key=dm:@alvo`. Três filtros: o que **este** perfil
   escreveu não entra como fala dela, a mesma fala não entra duas vezes (a conversa é relida a cada execução) e
   sem alvo não se grava nada. `COLLECT_THREADS` fica de fora: ela levanta **nomes** de conversa, não mensagens.
2. **Mandar mensagem num fio com fala pendente vira RESPONDER.** `_draft_gate` busca a fala da contraparte em
   duas fontes, nesta ordem: o que está escrito na conversa aberta com atribuição de autor
   (`navigation.mensagem_de`, que devolve vazio quando não há certeza) e a última fala gravada e ainda não
   respondida (`social.last_incoming`). Havendo uma, o tipo passa a `dm_reply` com `incoming` — e é daí que sai
   memória. Não havendo, continua `dm_initiate`, como antes.
3. **Resumo de conversa de verdade.** `_nota_de_conversa` era contagem; passou a ser extrativo: quem disse o
   quê, na ordem, só do que ficou ALÉM das seis interações que o contexto já mostra inteiras. Extrativo de
   propósito — um resumo por modelo seria uma chamada paga por mensagem enviada, e poderia afirmar o que ninguém
   disse.
4. **Limite não conta o que a conta recebeu.** Com histórico de entrada passando a existir, as contagens de
   limite (`policy.check`) filtram `direction='outbound'`: a caixa de entrada não consome a cota de envio.

O ciclo inteiro — ler, ouvir, responder com `incoming` e fio, confirmar o efeito, aprender e reencontrar na
execução seguinte — está coberto sem aparelho em
`tests/test_social_dm.py::test_ler_a_conversa_e_depois_escrever_vira_RESPOSTA_e_ensina_o_perfil`.

**Não feito, por depender do parque e de conta real** (aceite de nível 2, §8.3): a conversa real entre duas
contas do parque, com memória aprendida numa execução e reutilizada na seguinte. Nenhum emulador foi ligado e
nenhuma conta foi operada nesta chamada. Com o código acima, o caminho existe; falta rodá-lo.

**O que ainda não foi conferido contra o app real.** Os padrões de atribuição de `navigation.mensagem_de`
(`"fulano said …"`, `"Message from fulano: …"` e as formas em português) foram escritos a partir do que
`comentario_de` já casava e das traduções do app — **nenhuma árvore de acessibilidade de uma conversa de DM real
foi lida**: as evidências guardadas em `data/evidence` são só capturas `.jpg`, e o `screen_seen` dos rascunhos
antigos vem truncado em 400 caracteres e sem estrutura. Por isso a função devolve vazio quando não tem certeza, e
o caminho que os testes provam ponta a ponta é o outro: `READ_MESSAGES` → histórico de entrada →
`last_incoming`. Se a atribuição da bolha não casar no aparelho, nada quebra — só se perde a fala **da tela**,
e o fio gravado continua valendo.

> **Correção de 8.9.** A frase *"`memory_items` continua em zero … e continuará até um efeito de resposta a
> comentário ser confirmado"* deixou de valer: com a leitura da conversa gravando entrada e o envio virando
> `dm_reply`, o caminho de **mensagem direta** também ensina. O número no banco real continua zero até uma
> execução de verdade acontecer.
