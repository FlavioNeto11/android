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
