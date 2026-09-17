# Relatório de validação — 17/09/2026

Tudo abaixo foi medido **neste host** (é a mesma máquina onde a POC roda): Windows Server 2025 (10.0.26100),
Intel Core Ultra 9 185H (16 núcleos / 22 threads), 63,5 GB de RAM, 478 GB livres em C:, Hyper-V ativo.

> **Leitura rápida.** A plataforma inteira (painel, fila, scheduler, executor, Appium, emuladores, controle manual,
> recuperação) foi validada em emuladores Android reais. **A interpretação por IA real ainda NÃO foi exercitada**:
> não havia chave de provedor no ambiente, então as execuções nos emuladores usaram o *modo simulado* (regras
> fixas, identificado em toda parte). O provedor Anthropic está implementado e coberto por testes sem rede; falta
> apenas a validação com a chave (procedimento na seção 5).

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
≈3,5 GB reais; 10 instâncias pedem ≈36 GB livres. Um perfil leve (Android 9, ≈2 GB) reduz para ≈21 GB — basta trocar
`android.system_image` (ou um `override` por instância) no `config.yaml`.

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
simultâneas nesta máquina: liberar ≈25 GB (fechar os dois processos acima) **ou** usar a imagem Android 9.
O backend nunca deixa o host sem memória: cada boot só é aceito se couber a instância + os boots em andamento +
1,5 GB de folga; caso contrário o cartão explica o motivo e a execução informa quantas instâncias foram usadas.

## 3. Cenários de aceite

Legenda: ✅ comprovado · 🟡 comprovado só em modo simulado/sem IA real · ⛔ não testado (motivo e procedimento).

| # | Cenário | Resultado | Evidência |
|---|---|---|---|
| 1 | Comando em português → plano → tarefa completa em Android real, **com uso efetivo do modelo** | ⛔ **uso do modelo não testado** (sem chave). 🟡 O mesmo fluxo, com planejador/ator simulados, roda completo no emulador real em 18–22 s | execuções `r-…-52a931`, `r-…-a26b26`; verificador independente: 1 linha no ContentProvider com o texto e `Entregue ✓✓` |
| 2 | Mesmo comando em N instâncias e contas isoladas, simultâneas | 🟡 **3 de 10** (limite de RAM do host na hora do teste; 4ª e 5ª recusadas com motivo): 3 contas (`qa-user-01..03`), 3 sessões Appium (`systemPort` 8200–8202), 21–68 s, 1 mensagem por aparelho com `{instance_id}`/`{run_id}` próprios | `r-…-b1b757`, `r-…-689fb0`, `r-…-d5fdfa` (seção 2.2); `content query` em `emulator-5554/5556/5558` |
| 3 | App alvo trocável por configuração | 🟡 cadastro de apps (package/activity/APK/dicas/seletores), app por instância, escolha de app instalado e `open_app` restrito aos apps configurados — testados por API/testes; **um segundo app dirigido pela IA não foi exercitado** (depende da chave) | `tests/test_tools_and_api.py`, tela Configuração |
| 4 | Falha/tela inesperada em um aparelho não para os demais | ✅ emuladores reais: `android-01` com aviso inesperado (dispensado e concluído), `android-02` com sessão expirada (bloqueado *só ele*, tela de senha nunca gravada), `android-03` com falha de envio (incerto) | `r-…-07429d`: 1 sucesso + 1 bloqueio + 1 incerto; teste `test_falha_e_tela_inesperada…` |
| 5 | Usuário assume, navega e devolve sem disputa de cliques | ✅ real: login manual do `android-02` pela API de entrada (tap/texto/tecla com `lease_id` + `frame_id`), devolução e retomada só daquele item → sucesso; pela UI: "Assumir controle", toque no elemento da tela via eventos de ponteiro (`pointerdown/up` disparados no componente — não foi um clique de mouse do navegador, que a ferramenta de teste não posicionava com precisão) mapeado para o aparelho (conversa → tela inicial), "Devolver à IA". Frame antigo → `stale_frame`; lease errado → `not_controller` | plano v4 do `android-02` em `r-…-07429d`; teste `test_usuario_assume_no_ponto_seguro…` |
| 6 | Fechar/reabrir o painel preserva execução e histórico | ✅ snapshot + eventos por `last_event_id`; histórico das 6 execuções listado após recarregar; nada é reenfileirado | tela Execuções; `test_api_dedup_validacao_e_reconexao…`; teste de integração do frontend |
| 7 | Reiniciar o backend preserva a fila e reconcilia | ✅ real: `kill` do backend com 3 etapas `running`; ao subir, tentativas marcadas `interrupted`, etapas reobservadas, 3/3 concluídos, **1 mensagem por aparelho**; emuladores readotados e sessões Appium antigas encerradas por id | `scripts/test-restart-recovery.ps1`, `r-…-fc6426`; testes `test_reinicio_…` (2) |
| 8 | Falha após o toque de enviar → confirmação ou incerto, sem reenvio | ✅ testes com driver falso: erro após o efeito → reconcilia e conclui com 1 mensagem; erro sem efeito visível → `uncertain`, nenhum novo toque, retomada em lote recusa o item; timeout do driver segura o aparelho até a chamada terminar. ✅ real: `send_fail=1` no app → `uncertain`, sem reenvio | `tests/test_execution.py` (3 testes), `r-…-07429d` |
| 9 | Clique duplo e reconexões não duplicam | ✅ duplo clique real no botão **Executar** → 1 execução; mesma `idempotency_key` via HTTP → `deduplicated: true`; 8 criações simultâneas → 1 linha | `r-…-a26b26`; `tests/test_queue_core.py` |
| 10 | Relatório distingue comprovado de bloqueado/não testado | ✅ `GET /api/runs/{id}/report` e aba Relatório: sucesso comprovado × confirmado manualmente × falha × bloqueio × incerto × cancelado × não iniciado; solicitadas × utilizadas | relatórios impressos por `scripts/demo-run.ps1` |

Testes automatizados: **24** no backend (`pytest`, 1 min 45 s) e **93** no frontend (`vitest`), todos passando.

Estado deixado na máquina ao final: backend em `127.0.0.1:8000` **sem modo simulado** (barra mostra "IA não
configurada"; Planejar/Executar desabilitados com a dica; gerenciamento e controle manual ativos), `android-01…03`
online com o app de QA e sessão de automação prontos, `android-04` criado e parado, `android-05…10` sem AVD (o botão
"Criar AVD" ou `start.ps1 -StartInstances N` os cria). O contador "2 bloqueadas" da barra vem do histórico destes
testes: o item **incerto** provocado de propósito em `r-…-07429d` e um item aguardando usuário em `r-…-36181a`.

## 4. Limitações observadas

* **IA real não validada** (seção 5). O modo simulado só conhece o app de QA e serve para exercitar a plataforma.
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

## 5. Pendente: validar com o provedor real (≈10 min)

1. Edite `C:\git\android\.env`: `AI_PROVIDER=anthropic` e `ANTHROPIC_API_KEY=<sua chave>` (modelo padrão `claude-opus-5`).
2. `pwsh -File scripts\stop.ps1` e `pwsh -File scripts\start.ps1` — o selo "MODO SIMULADO" some e a barra mostra o modelo.
3. Com 1 instância online e o app de QA provisionado (`scripts\provision-qa.ps1`), rode o comando de mensagem pelo
   painel (ou `scripts\demo-run.ps1 -Instances android-01`). Esperado: plano gerado pelo modelo (aba Plano mostra
   `planner.model`), decisões com *rationale* na aba Decisões, tokens em "Por instância", 1 linha no ContentProvider.
4. Genericidade: peça algo fora da receita do simulador, p.ex. *"Abra o QA Messenger, busque o contato Arquivo e
   abra a conversa"* ou cadastre `com.android.settings` como app e peça *"abra Configurações e entre em Sobre o
   telefone"*.
5. Se a conta não aceitar o *fallback* de recusa (beta), o backend registra o aviso e segue sem ele; para desligar:
   `AI_REFUSAL_FALLBACK=false`.

## 6. Comandos usados

```powershell
pwsh -File scripts\diagnose.ps1 ; pwsh -File scripts\install-prereqs.ps1 ; pwsh -File scripts\build-qa-apk.ps1
pwsh -File scripts\start.ps1 -Simulated -NoBrowser
pwsh -File scripts\provision-qa.ps1 ; pwsh -File scripts\demo-run.ps1 -Instances android-01,android-02,android-03
pwsh -File scripts\test-restart-recovery.ps1 -Instances android-01,android-02,android-03 -Simulated
pwsh -File scripts\scale-test.ps1 -Steps "1,2,3,5,10" ; pwsh -File scripts\probe-image.ps1 -Image '<imagem>'
cd backend; .venv\Scripts\python.exe -m pytest -q        cd frontend; npm test; npm run build
```
