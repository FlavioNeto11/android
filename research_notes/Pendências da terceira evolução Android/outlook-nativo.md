# Outlook nativo (P15): auditoria do diagnóstico "o app recusa o ambiente emulado"

Data: 30/09/2026. Máquina: central (`C:\git\android`, commit `6997091`), Windows Server 2025, GPUs NVIDIA RTX 2000 Ada e
Intel Arc. Escopo: só leitura no repositório; experimentos num AVD de diagnóstico fora do parque (`diag-outlook`,
android-34 google_apis x86_64) e leitura da Play Store no android-11. Nenhuma credencial digitada, nenhum aparelho de
produção tocado, nenhuma chamada paga de IA.

Legenda de confiança usada abaixo: **observado** (artefato ou medição feita nesta sessão), **inspecionado** (código,
APK ou dump lido nesta sessão), **externo** (fonte pública, com versão e data), **inferido** (interpretação).

Resumo em uma frase: o diagnóstico anterior acertou o mecanismo imediato (a `UD2` é proposital) e errou a causa e a
relação de causa e efeito. **O que derruba o emulador é o renderizador SwiftShader-GL do próprio emulador** (falha de
acesso em código JIT do `gles_swiftshader\libGLESv2.dll`, nas 8 quedas com dump do central), disparada pela tela de
onboarding do Outlook; **a `UD2` é um fail-fast do motor Hx depois de um assert ao carregar `sortdefault.nls`**, uma
condição de estado/tempo que só apareceu em 2 de 6 aberturas e que, no mesmo AVD e com o mesmo armazenamento, não se
repetiu hoje. Com `-gpu host` no emulador estável 37.1.11, **o Outlook 5.2635.3 abriu, mostrou o onboarding e a tela
"Add account" e o emulador não caiu** (prova `real`, abaixo).

---

## Q1. O que os artefatos provam: a queda `SIGILL/UD2` em `libhxcomm.so`

### Takeaway

A `UD2` em `libhxcomm.so+0x441762` é uma primitiva de *fail-fast* (função `noreturn` que carrega o marcador
`0xC0D1F1ED` em `rax` e três argumentos em `rbx/rcx/rdx` antes de executar `ud2`), chamada pelo tratamento de erro do
Hx com código **87** e as tags **0x1e4dd214 / 0x1e49f644**. O log em disco do Hx da mesma sessão registra um
`Assert tag 81dn3 ... sortdefault.nls ... 1: 87` imediatamente antes do `Crash`. Não há nenhuma cadeia de detecção de
emulador na biblioteca nativa; a instalação estava completa (4 splits) e os avisos de dex/CPU são benignos.

### Cited Findings

- A queda: `Fatal signal 4 (SIGILL), code 2 (ILL_ILLOPN)` na thread `Hx-Storage` (tid 4665, pid 4583), 47 ms depois de
  `appUpdates: Migration starting` (17:45:22.182 → 17:45:22.229) — **observado**,
  `scratchpad/canary-logcat.txt:42241` (Migration starting) e `:42268` (Fatal signal).
- Registradores no tombstone: `rax 00000000c0d1f1ed`, `rbx 000000001e4dd214`, `rcx 0000000000000057`,
  `rdx 000000001e49f644`, `rip = fault addr`; `#00 pc 0000000000441762 .../lib/x86_64/libhxcomm.so
  (BuildId: 2a1e45c9db77477af121b02f0d8155b69b26329d)`, 33 quadros, todos em `libhxcomm.so` até `__pthread_start` —
  **observado**, `scratchpad/canary-logcat.txt:42555-42562` e `:42593-42594`; tombstone completo (com mapas) puxado
  para `scratchpad/diag/tombstone_00` (619.603 bytes).
- O mesmo endereço num aparelho do parque (emulador 37.1.11; tela 720×1232 no bloco `device` do AppCenter, distinta dos 1080×2340 do diag-outlook; android-02 ou android-09 segundo §26.2, o log não diz qual): `traps: Hx-Storage[3169] trap invalid opcode ip:79022a65e762 ...
  in libhxcomm.so[79022a21d000+1463000]` → `0x79022a65e762 − 0x79022a21d000 = 0x441762` — **observado**,
  `scratchpad/outlook-semvk2.txt:23630`.
- Decodificação manual dos bytes em `0x441710..0x441764` (segmento `LOAD` 0 tem `vaddr == offset`, então o `pc` do
  tombstone é o deslocamento no arquivo): `push rbp; mov rbp,rsp; push rbx; and rsp,-8; sub rsp,0x38; mov [rsp+0x14],edi;
  mov [rsp+0x10],esi; mov [rsp+0xc],edx; mov eax,0xC0D1F1ED; ... mov rax,[rsp+0x30]; mov rbx,[rsp+0x28];
  mov rcx,[rsp+0x20]; mov rdx,[rsp+0x18]; ud2` — ou seja, a função só arruma os três argumentos de 32 bits nos
  registradores para o dump e executa `ud2`. Bytes: `b8 ed f1 d1 c0` (mov eax,0xC0D1F1ED) e `0f 0b` em `0x441762` —
  **inspecionado**, `lib/x86_64/libhxcomm.so` extraído de `scratchpad/outlook/split_config.x86_64.apk`
  (sha256 `73b3d2bd…6b98`, 21.938.264 bytes).
- O chamador (quadro #01, retorno `0x440e93`, tombstone mostra `0x440e92` = retorno − 1): `xor edx,edx; test r15,r15;
  jne; xor esi,esi; ...; mov esi,[r14+8]; cmp r15,1; je; mov edx,[r14+0x28]; mov edi,ebx; call 0x441710; int3` —
  o `int3` de enchimento depois do `call` confirma que o compilador tratou a função como `noreturn`. Só **4** `call`
  diretos para `0x441710` em todo o `.text` (em `0x440e8e`, `0x441707`, `0x4800bb`, `0x491797`), contra 439 `ud2`
  espalhados (asserts inline do LLVM/`__builtin_trap`) — **inspecionado**.
- Build da biblioteca: `.comment` = `Android (12285214, +pgo, +bolt, +lto, +mlgo, based on r522817b) clang version
  18.0.2 ... Linker: LLD 18.0.2`; `nativeLibVersions: {libhxcomm=16.0.20427.33800}` — **inspecionado**;
  **observado** `scratchpad/canary-logcat.txt:41941`.
- Strings da `.rodata` de `libhxcomm.so`: **zero** ocorrências de `emulat`, `goldfish`, `ranchu`, `qemu`, `sdk_gphone`,
  `Genymotion`, `ro.build`, `ro.hardware`, `ro.kernel`, `userdebug`, `test-keys`, `HRESULT`, `cpuid/AVX/SSE4`; a única
  classe do `android.os.Build` referenciada por JNI é `android/os/Build$VERSION`. Existem `StoreCorruption`,
  `storeCorruptionType`, `MigrationState`, `MigrationStatistics` — **inspecionado**.
- Log em disco do Hx da sessão que caiu (`files/hxcore.hfl`, 75.161 bytes, mtime 17:45:22.207, formato comprimido
  tipo LZ com trechos legíveis): `Assert:  tag: 81dn3 extraData6: 11 ... 7: sortdefault.nls ... 20: 1376352
  accountExplicitlyEmpty: 0 ... 1: 87 ... 3: en-US`, depois `Assert:  tag: 4s7t2`, e no fim
  `Crash{...t3iup... 2: 87 ... 1: 508163652 ... 3: en-US` seguido dos bytes `14 d2 4d 1e` (= `0x1e4dd214`, o `rbx`
  do tombstone). `508163652 = 0x1e49f644` (o `rdx`) e `87 = 0x57` (o `rcx`) — **observado**,
  `scratchpad/diag/hxcore.hfl` (deslocamentos `0xf090`, `0x112bf`, `0x12546`).
- Ligação entre o `Assert` e o `Crash`: o `Assert 81dn3` carrega `1: 87` e `2: 508179840` (= `0x1e4a3580`); o `Crash`
  carrega `2: 87` e `1: 508163652` (= `0x1e49f644`, o `rdx`), e há um `Assert 4s7t2` entre os dois. O que liga os
  registros é o código 87 propagado e a sequência no mesmo log; **as tags de cada ponto são distintas** e o `rbx`
  (`0x1e4dd214`) é a tag do próprio ponto de `Crash`, não a do assert — **observado/inferido**.
- `1376352` é exatamente o tamanho de `assets/nls.7z` no `base.apk` e de `files/data/nls.7z` no aparelho; na sessão
  que caiu, `files/data/` tinha só `nls.7z` (17:45:20.923), `locale.nls` (17:45:21.207) e `c_1252.nls` (17:45:21.399);
  **não havia `sortdefault.nls`**. Depois da abertura que deu certo hoje, `sortdefault.nls` (2.969.292 bytes, `r--`)
  passou a existir, criado às 07:49:55.632, 0,95 s antes do `Migration starting` (07:49:56.587) — **observado**,
  `adb shell ls -la --full-time` no `diag-outlook` e `scratchpad/diag/applogs/appUpdates.log`.
- Instalação completa: `pm path` lista `base.apk`, `split_config.en.apk`, `split_config.x86_64.apk`,
  `split_config.xhdpi.apk`; `dumpsys package`: `versionName=5.2635.3`, `versionCode=72635119 minSdk=30 targetSdk=36`,
  `primaryCpuAbi=x86_64`, `firstInstallTime=2026-09-29 17:37:33`, `installerPackageName=null` — **observado** no
  `diag-outlook`. O `DexPathList` e o `nativeloader` do processo enumeram os 4 APKs e as bibliotecas extraídas em
  `.../lib/x86_64` — **observado**, `scratchpad/canary-logcat.txt:41909` e `:41967`.
- Conteúdo dos APKs: `base.apk` 7.485 entradas, 16 `.dex`, 0 libs; `split_config.x86_64.apk` 22 entradas, 0 `.dex`,
  20 `.so` só em `lib/x86_64/`; `split_config.en.apk` 3 entradas; `split_config.xhdpi.apk` 403 entradas — **inspecionado**
  (`zipfile`).
- "Failed to open dex files from split_config.x86_64.apk because: Entry not found" aparece como `Suppressed:` dentro de
  um `ClassNotFoundException` de `com.microsoft.copilot.webrtcdebug.WebRTCDebugFileProvider`, e o app registra
  `falling back to default file provider` e segue — **observado**, `scratchpad/canary-logcat.txt:41966-42007`.
- "Unexpected CPU variant for x86: x86_64" é emitido pelo `zygote64`, pelo `system_server` e por todo processo da
  imagem (linhas 3446, 4262, 9485, …, 41874), não só pelo Outlook — **observado**, `scratchpad/canary-logcat.txt`.
- Guest: `ro.build.fingerprint = google/sdk_gphone64_x86_64/emu64xa:14/UE1A.230829.050/12077443:userdebug/dev-keys`,
  `ro.product.cpu.abilist = x86_64,arm64-v8a`, `ro.kernel.qemu=1`, `ro.hardware=ranchu`, `ro.hardware.egl=emulation` —
  **observado** no `diag-outlook`.
- A `UD2` **não** foi determinística nem no mesmo aparelho: das 6 aberturas registradas, 2 caíram na `UD2`
  (aparelho do parque 17:26:59, `outlook-semvk2.txt:23630`; diag-outlook 17:45:22, `canary-logcat.txt:42268`) e 4 passaram do
  armazenamento: `Store boot complete (didStorageMigrate=false)` (`outlook-semvk.txt:1348`),
  `Migration complete` + `Store boot complete (didStorageMigrate=true)` (`outlook-semvk3.txt:909-910`), a de 16:41 que
  já estava na tela de onboarding (`outlook-logcat.txt:374`) e a de hoje 07:49 no diag-outlook
  (`scratchpad/diag/applogs/appUpdates.log`) — **observado**.
- Depois da `UD2` no aparelho do parque, o Outlook reabriu em modo de recuperação (`boot cancelled by step CheckRecovery`,
  `RecoveryModeProcess`, `RecoveryMode: handling crash report analysis`) e gravou um anexo de crash de
  **2.156.975 KB** em `files/appcenter/database_large_payloads/`; o envio ao AppCenter falhou com
  `401 AppSecretForbiddenListed` — **observado**, `scratchpad/outlook-semvk2.txt:24881`, `:25046`, `:26078`, `:21842`.

### Inferences

- (alta) A `UD2` é o `Crash` do Hx: um *fail-fast* deliberado depois de um erro tratado como irrecuperável, com código
  87 e duas tags de origem. É equivalente a `__fastfail`/`RaiseFailFastException` do Office no Windows. Isso confirma
  a parte "armadilha proposital" do K-062.
- (alta) O erro que dispara o fail-fast **não é uma conferência de emulador**: nem a `.rodata` nem os imports de
  `libhxcomm.so` têm qualquer cadeia de detecção, e o mesmo binário, no mesmo AVD e sobre o mesmo `HxStore.hxd`,
  concluiu a migração hoje sem cair. O assert é sobre `sortdefault.nls` (tabela de ordenação NLS do Windows que o Hx
  precisa para índices/colação) que ainda não existia em `files/data/` quando a thread `Hx-Storage` começou a
  migração. A leitura mais provável é uma corrida entre a extração do `nls.7z` (via `libsevenzip.so`, ~3 MB para
  descomprimir) e o início da migração, que um convidado lento (SwiftShader, 4 vCPUs partilhadas com 5 emuladores de
  produção) perde. Uma leitura alternativa é que a extração de `sortdefault.nls` falhou naquela sessão; as duas levam
  ao mesmo assert e nenhuma depende de "ser emulador".
- (média) No parque, a `UD2` das 17:26 veio depois de duas mortes do processo do emulador (16:41 e 16:49; os logs não
  provam que foram no mesmo aparelho) enquanto o Outlook escrevia em `files/`: um diretório de dados rasgado (arquivos NLS ou store incompletos) produz o mesmo
  assert. No `diag-outlook`, a primeira abertura (17:41:00–17:41:08, primeira sessão do emulador) deixou
  `HxStoreMigrating.hxd` (17:41:06) e foi interrompida quando o emulador foi relançado às 17:42; a segunda abertura
  (17:45) caiu. Os dois cenários são "estado interrompido por morte externa do emulador", não recusa do app.
- (alta) Não há desencontro de ABI: a Play Store entregou o split `x86_64`, o `primaryCpuAbi` é `x86_64`, as 20
  bibliotecas nativas estão em `lib/x86_64/` e o processo as carregou de `.../lib/x86_64` (extraídas). O aviso
  "Unexpected CPU variant" é o ART reclamando do valor `x86_64` em `dalvik.vm.isa.x86_64.variant` da imagem; o
  sistema inteiro o emite. O erro de dex no split de configuração é esperado (split de configuração não tem `.dex`)
  e o app segue com o provedor padrão.
- (média) O 0x57 = 87 coincide com `ERROR_INVALID_PARAMETER` do Win32, que o Hx (porte do motor do Mail do Windows)
  costuma reaproveitar; não há confirmação no log, então fica como coincidência plausível, não como fato.

### Gaps

- O significado exato das tags Office `t3iup`/`81dn3`/`4s7t2` e do `extraData6: 11` só a Microsoft tem.
- Não deu para provar se a extração de `sortdefault.nls` estava atrasada ou falhou às 17:45: o `FileSystemLoggingCache.log`
  (8.008 bytes) é binário e não tem texto legível; o `minidump/new/<id>/log.txt` do Breakpad não foi puxado.
- O log do Hx (`.hfl`) é comprimido; só trechos foram legíveis. Um decodificador do formato daria a mensagem inteira.

---

## Q2. As quedas do processo do emulador no host (`0xc0000005`): o que os dumps mostram

### Takeaway

As 8 quedas do `qemu-system-x86_64[-headless].exe` registradas no central (29/09, 15:56–17:29) têm a **mesma
assinatura**: violação de acesso de leitura em código **fora de qualquer módulo** (heap executável, JIT), com 6–7
quadros de `C:\Android\Sdk\emulator\lib64\gles_swiftshader\libGLESv2.dll` na pilha da thread que caiu. Todas rodavam o
renderizador SwiftShader-GL (o antigo, "SwiftShader 4.0.0.1"); **nenhuma** carregou `gles_angle`. As três quedas com
logcat casado aconteceram durante a tela de onboarding do Outlook (animação Lottie), e a `UD2` do aparelho do parque (17:26:59)
**não** derrubou o emulador. Hoje, o canary 37.3.2 também sumiu ao abrir a mesma tela; com `-gpu host` (NVIDIA) o
emulador estável 37.1.11 aguentou.

### Cited Findings

- Evento 1000 (Application) no central, últimos 3 dias, só para `qemu*`: 15:56:35, 16:03:05, 16:10:29 (headless),
  16:31:36, 16:34:49, 16:41:20 (`qemu-system-x86_64.exe`, com janela), 16:49:14, 17:29:17 (headless); todos
  `c0000005`, módulo `unknown`, deslocamentos em endereços de heap (`0x1890f5f5225`, `0x2264533e8bc`, … terminando em
  `…225` ou `…8bc`), todos em `C:\Android\Sdk\emulator\...` (37.1.11); mais um `crashpad_handler.exe` `80000003`
  às 16:15:31 — **observado**, `Get-WinEvent` nesta sessão. Não há evento para `SdkBeta` (37.2.11) nem `SdkCanary`
  (37.3.2) no central.
- Minidumps WER (`%LOCALAPPDATA%\CrashDumps\qemu-system-x86_64*.dmp`, 31–45 MB cada, 8 arquivos) lidos com um parser
  `struct` próprio (`scratchpad/mdparse.py`): em **todos**, `ExceptionCode 0xc0000005`, parâmetro 0 = 0 (leitura),
  `rip` fora de todo módulo, e a pilha da thread contém `libGLESv2.dll+0x207eb9/+0x125f1/+0x207f9b/+0x208340/+0x208381/
  +0x182647/+0x2083c0` (mesmo conjunto nos 8), com `libGLES_CM.dll` e `qemu-system-x86_64*.exe` em 3 deles. Módulos do
  emulador carregados: `libgfxstream_backend.dll`, `gles_swiftshader\libGLESv2.dll`, `gles_swiftshader\libEGL.dll`,
  `gles_swiftshader\libGLES_CM.dll`, e `vulkan\vk_swiftshader.dll` (6 dumps) ou `vulkan\libvulkan_lvp.dll` (2 dumps:
  16:49 e 17:29). Nenhum dump carrega `gles_angle\*` — **inspecionado**.
- Bytes em `rip` no dump de 17:29 (`...50956.dmp`): `f3 42 0f 6f 94 08 f0 84 01 00 f3 42 0f 6f 9c 08 70 85 01 00 66 0f
  70 ca 55 66 41 0f db ce ...` (movdqu/pshufd/pand com índice `r9`), código vetorial gerado — **inspecionado**.
- Correlação com o convidado: `Displayed .../.ui.onboarding.splash.SplashActivity` às 16:41:17.274 → evento 1000
  16:41:20 (`outlook-logcat.txt:374`, última linha 16:41:18.308); Splash às 16:48:47.733 → queda 16:49:14
  (`outlook-semvk.txt:2696`, última linha 16:49:13.162, com `EGL_emulation app_time_stats avg=2976ms…8639ms` e
  `LOTTIE` antes); Splash às 17:29:12.790 → queda 17:29:17 (`outlook-semvk3.txt:1658`, última linha 17:29:15.265,
  `LOTTIE: Animation contains merge paths` às 17:29:13.683/14.465) — **observado**.
- A `UD2` do aparelho do parque às 17:26:59 (`outlook-semvk2.txt:23630`) não tem evento 1000 correspondente (nenhum entre
  16:49:14 e 17:29:17); o log segue até 17:27:25 com o Outlook em modo de recuperação — **observado**.
- No `diag-outlook` com o canary 37.3.2 e `-gpu swiftshader_indirect` (hoje, 07:48–07:5x): o Outlook subiu em segundo
  plano na inicialização, concluiu a migração (`Store boot complete (didStorageMigrate=true)`, 07:49:56), e ao
  lançar a `MainActivity` (monkey) o emulador **desapareceu em menos de 35 s**, sem evento 1000, sem dump WER e sem
  relatório em `emu-crash-37.3.2.db` (`-no-metrics`); o log do emulador termina em `Boot completed in 57226 ms` —
  **observado**, `scratchpad/diag/emu-run1.log` e saída da tarefa `b9uazs0g3`.
- `-gpu angle_indirect` no 37.1.11: `ERROR | gpuChoiceBasedOnGpuOptions: Selected GPU option 'angle_indirect' is not
  valid, switching to 'auto' mode.` → `emuglConfig_init: vulkan_mode_selected:lavapipe gles_mode_selected:swiftshader`;
  `-gpu swangle` → `gles_mode_selected:swiftshader` (o convidado reporta `OpenGL ES 3.0 SwiftShader 4.0.0.1` nos dois
  casos). `emulator -help-gpu` do 37.1.11 lista só `auto, host, software, lavapipe, swiftshader, swangle` —
  **observado**, `scratchpad/diag/emu-run2-angle.log`, `emu-run3-swangle.log`.
- `-gpu host` no 37.1.11: `emuglConfig_init: vulkan_mode_selected:host gles_mode_selected:host`, `Selecting Vulkan
  device: NVIDIA RTX 2000 Ada Generation Laptop GPU, Version: 1.4.329`; no convidado `GLES: ... (NVIDIA RTX 2000 Ada
  ...), OpenGL ES 3.1 (4.5.0 NVIDIA 596.71)`. O Outlook foi lançado às ~08:00, ficou em
  `.ui.onboarding.splash.SplashActivity` por 120 s de monitoração, depois `AddAccountActivity`; o emulador foi
  encerrado por mim às 08:03 com `emu kill`, **sem cair** — **observado**, `scratchpad/diag/emu-run4-host.log`,
  `outlook-host-gpu-splash.png`, `ui-host-gpu.xml`, `ui3.xml`.
- Relato externo com a mesma assinatura (24/09/2026): "With `-gpu swiftshader_indirect` the emulator loads the legacy
  SwiftShader GLES renderer (`emulator/lib64/gles_swiftshader/libGLESv2.dll`), which crashes the qemu host process
  with an access violation in its JIT code about a second after a cold MainActivity launch"; `swangle` também mostrou
  `gles_mode_selected:swiftshader` e voltou a cair; a solução adotada foi `debug.hwui.renderer=skiavk` no convidado
  (240/240 lançamentos) — **externo**, [Lemkinator/GetIcon PR #255](https://github.com/Lemkinator/GetIcon/pull/255) e
  [PR #258](https://github.com/Lemkinator/GetIcon/pull/258).
- Notas de versão do emulador: 36.4.9 (10/02/2026) introduziu `-gpu software` e Lavapipe como Vulkan padrão; 36.2.12
  (13/10/2025) "Fix crash on Windows that occurred when launching a vulkan app in software rendering mode"; 37.1.11
  (30/07/2026) não lista correção de renderizador — **externo**,
  [Emulator release notes](https://developer.android.com/studio/releases/emulator).
- Guia de solução de problemas do emulador recomenda trocar o renderizador (`-gpu host` / `-gpu swiftshader`) quando há
  falha de OpenGL ES — **externo**, [Troubleshoot known issues](https://developer.android.com/studio/run/emulator-troubleshooting).

### Inferences

- (alta) A morte do emulador é um defeito do renderizador SwiftShader-GL legado do emulador (código JIT do
  `gles_swiftshader\libGLESv2.dll`), disparado pelo que o Outlook desenha na tela de onboarding (Lottie/camadas de
  hardware via HWUI → tradutor GLES → SwiftShader). Não é efeito da `UD2` (que aconteceu sem queda do host) e não é
  o app "derrubando" o emulador de propósito.
- (alta) O K-062 e o P15 registram testes "com SwiftShader e `angle_indirect`" no central; os dumps e o erro
  `not valid` mostram que no 37.1.11 o `angle_indirect` não existe e cai em `auto`, que no modo `-no-window` escolhe
  SwiftShader. Ou seja, **o ANGLE nunca foi testado** no central. O teste de "esconder o Vulkan do convidado" também
  era irrelevante: o crash está no caminho GLES, não no Vulkan.
- (média) O canary 37.3.2 tem o mesmo defeito (sumiu na mesma tela); a diferença que o K-062 viu (canary "aguenta")
  é só porque naquela sessão o Outlook caiu na `UD2` antes de chegar ao onboarding.
- (alta) Trocar o renderizador resolve a queda do host: `-gpu host` provou hoje; `debug.hwui.renderer=skiavk` no
  convidado é a alternativa só-software relatada externamente e ainda não medida aqui.

### Gaps

- O 37.2.11 (beta) rodou no central (`emu-crash-37.2.11.db` criado às 17:35:27 de 29/09) mas não deixou evento 1000
  nem dump; o notebook (i7-9850H) não foi inspecionado. Nenhum registro de host disponível sustenta a afirmação
  "37.2.11 também cai" do P15.
- As buscas específicas do pedido (`Outlook 5.2635.3`, `libhxcomm.so SIGILL`, `Hx-Storage`, `com.microsoft.office.outlook
  emulator crash`, `WHPX SIGILL guest ud2`) devolveram só listagens de espelhos de APK ou nada relevante; a ausência
  de relato Outlook-específico é "não encontrado", não "pesquisado e vazio" em todos os fóruns (Microsoft Q&A e Tech
  Community não foram varridos por versão).
- Não achei um issue do Google Issue Tracker com essa assinatura exata (as buscas devolveram issues antigos de OpenGL,
  p.ex. [109717070](https://issuetracker.google.com/issues/109717070) e [393384256](https://issuetracker.google.com/issues/393384256),
  sobre outras GPUs/versões); o relato mais próximo é o PR do GitHub acima.
- A versão exata do SwiftShader-GL embutido (`4.0.0.1` é o que o convidado reporta) e se o 37.3.x já o substituiu não
  constam nas notas de versão.

---

## Q3. Requisitos do Outlook, Play Integrity e imagens `google_apis`/`userdebug`

### Takeaway

A Microsoft só exige Android 10+ (suporte a 9.x encerrado em 05/01/2026) e Google Play Services; não há texto oficial
proibindo emuladores. O Play Integrity, quando usado, devolve verdict vazio num emulador sem Play certificado, mas quem
decide o que fazer com isso é o app, e nada nos artefatos mostra o Outlook consultando integridade **antes** do
login; o que se viu foi só a ausência do broker (Authenticator/Company Portal), esperada e não fatal.

### Cited Findings

- "Outlook for Android requires Android 10.0 or later. Support for Android 9.x and earlier ended on January 5, 2026."
  A página manda limpar cache/dados do Google Play Services em caso de incompatibilidade; não menciona emulador, root
  ou certificação — **externo**, [Microsoft Support](https://support.microsoft.com/en-us/outlook/what-version-of-android-does-your-app-support).
- Play Integrity: `MEETS_VIRTUAL_INTEGRITY` só existe para Google Play Games no PC; "Empty (a blank value): ... the app
  is not running on a physical device (such as an emulator that does not pass Google Play integrity checks)"; "Your
  app's server can use the resulting payload ... to determine how best to proceed" — **externo**,
  [Play Integrity verdicts](https://developer.android.com/google/play/integrity/verdicts).
- O dex do Outlook contém `IntegrityTokenRequest`, `IntegrityService`, `ByPassPlayIntegrityOnDemand`, strings de
  detecção de root do Intune (`RootDetectionOperations`, `isRooted`) e de emulador (`goldfish`, `ranchu`,
  `google/sdk_gphone`, `Genymotion`, `isRunningOnEmulator`) — **inspecionado** (`base.apk`, busca de strings; a
  classe dona de cada string não foi mapeada).
- Na inicialização real: `MAMInfo initialized ... PolicyRequired=false`, `OneAuth.readDeviceInfo ... mode: UNKNOWN`,
  `Failed to read device mode ... Broker is not available or broker doesn't support read device info request` (não
  fatal); `TSL Error FetchRemoteConfigurations MalformedJWTException-EmptyStream` (aviso) — **observado**,
  `scratchpad/canary-logcat.txt` (17:45:20.047, 17:45:21.844-21.854, 17:45:22.227).
- Hoje, com `-gpu host`, a `AddAccountActivity` abriu com `Enter your email` (`auto_complete_input_email`),
  `Add Google account`, `Privacy and Terms` e `Continue`; o WebView carregado é `com.google.android.webview
  113.0.5672.136` — **observado**, `scratchpad/diag/ui3.xml` e logcat da sessão.
- O AppCenter do próprio Outlook reporta a imagem como `model sdk_gphone64_x86_64`, `osBuild UE1A.230829.050`,
  `osApiLevel 34`, e o Play Services da imagem é `23.18.18 (231818047)` — **observado**, `scratchpad/outlook-semvk2.txt`
  (bloco `device` do log AppCenter, 17:27:00) e anexo `outlook_crash_log_attachment.description` (base64 decodificado).

### Inferences

- (alta) Nem a inicialização nem a tela de login dependem de Play Integrity: o app chegou ao formulário de e-mail
  sem consultar nada disso. Se houver uso de Integrity, será em fluxos específicos (Copilot/compra), não no bootstrap.
- (média) A imagem `google_apis` (GMS presente, não certificada, `userdebug`, `dev-keys`) daria verdict vazio se
  consultada; para o objetivo funcional (ler a caixa de entrada) isso não é bloqueio conhecido. O Play Services
  23.18.18 (2023) da imagem é antigo e pode limitar recursos que dependam de GMS novo; não afeta o crash.
- (média) A ausência do broker (Authenticator/Company Portal) só muda o caminho de login para o WebView embutido do
  OneAuth; para conta pessoal `outlook.com` é o caminho normal.

### Gaps

- Não há documento Microsoft dizendo se o Outlook para contas pessoais bloqueia emuladores ou `userdebug`; só a
  prova empírica (tela de login abriu) responde, e o login real ainda não foi feito (23.13, autorizado, `not_run`).
- Não foi mapeado em que classe do dex ficam `isRunningOnEmulator` e os nomes `goldfish/ranchu` (provavelmente
  MSAL/OneAuth/Intune para telemetria); exigiria um parser de dex.

---

## Q4. Versões oficiais e combinação reproduzível

### Takeaway

Não há combinação "oficial" publicada (Outlook × emulador × imagem) com evidência de funcionar; a combinação que
funcionou aqui foi **Outlook 5.2635.3 + emulador 37.1.11 + android-34 google_apis x86_64 + `-gpu host`**. A Play Store
do android-11 (conta do dono) já oferece **5.2637.3 (26/09/2026)** como atualização, "Works on your device", e esse é o
único canal aceitável para trocar de versão.

### Cited Findings

- Play Store no android-11 (`google_apis_playstore`, ligado por `POST /api/instances/android-11/actions/start`,
  comando `c-20260930110336-f93a80`, `succeeded`; listagem por `POST /api/store/open-listing`): "Microsoft Outlook",
  botões `Uninstall` / `Update`, "Update available"; em "About this app": `Version 5.2637.3`, `Updated on Sep 26,
  2026`, `Released on Jan 28, 2015`, `Compatibility ... Google Sdk_gphone64_x86_64 (this device) ... Works on your
  device`, `Download Size 29.04 MB`, `Required OS Android 11 and up`. Nada foi tocado em `Update`; android-11 parado
  depois (`c-20260930111023-dc0d97`, `succeeded`) — **observado**, `scratchpad/diag/play-about5.xml`,
  `play-about6.xml`.
- A versão instalada no parque é a 5.2635.3 (`versionCode 72635119`, importada em `c-20260929184859-0fe853`) —
  **observado**, `docs/relatorio-validacao.md` §26.2 e `dumpsys package` no diag-outlook.
- Espelhos de terceiros listam 5.2635.3 (enviado em 20/09/2026) e 5.2637.3, mas o projeto não aceita APK de espelho —
  **externo** (só como data de referência), [APKMirror](https://www.apkmirror.com/apk/microsoft-corporation/outlook/microsoft-outlook-5-2635-3-release/).
- Prova real de hoje: `-gpu host` + 37.1.11 + android-34 google_apis + Outlook 5.2635.3 → onboarding e `AddAccountActivity`
  visíveis, emulador vivo por ~3,5 min até o `emu kill` — **observado** (Q2).

### Inferences

- (alta) Versões antigas do Outlook não estão disponíveis por canal oficial (a Play Store só oferece a atual); a única
  troca de versão possível é **subir** para a 5.2637.3 pelo android-11, com a conta do dono, e passar pelo fluxo de
  releases (`store/sync`, canário, promoção).
- (média) A atualização para 5.2637.3 não é necessária para o P15: o bloqueio está no renderizador do emulador, e o
  `libhxcomm` novo pode ou não ter mudado a corrida do NLS. Vale tratar como variável separada.

### Gaps

- Não há notas de versão públicas da Microsoft por build (5.2635.3 → 5.2637.3) que digam o que mudou.
- Não foi testado o Outlook no android-11 em si (imagem `google_apis_playstore`): é o aparelho da loja, não do parque.

---

## Q5. Dados que faltavam, o que foi coletado no `diag-outlook` e a matriz de experimentos

### Takeaway

Os dados que faltavam (tombstone completo, `pm path`/`dumpsys`, fingerprint, estado do diretório de dados, log do Hx,
minidumps do host) foram coletados e mudaram o diagnóstico. Três experimentos discriminantes já foram feitos (mesmo
store reabre; canary+SwiftShader morre na UI; 37.1.11+`-gpu host` sobrevive). O que resta é decidir o renderizador
para o parque (GPU do host vs. `skiavk` só-software) e medir carga, mais a validação no notebook.

### Cited Findings

- Cronologia do `diag-outlook` (AVD criado 17:35:36 de 29/09; `config.ini`: `hw.ramSize=3072`, `hw.gpu.enabled=no`,
  `hw.gpu.mode=auto`, `image.sysdir.1=system-images\android-34\google_apis\x86_64\`, `hw.cpu.ncore=4`): 1ª sessão do
  emulador ~17:35:44–17:41:5x (Outlook instalado 17:37:33, primeira abertura 17:41:00–17:41:08, deixou
  `HxStoreMigrating.hxd` às 17:41:06); 2ª sessão 17:42:00–17:50:15 (canary-logcat; abertura 17:45:18, `UD2` 17:45:22;
  `tombstone_00` 17:45:23; `tombstone_01` 17:48:34 é um `grep` do toybox que caiu em `regexec`, irrelevante) —
  **observado**, mtimes do AVD, `/data/tombstones`, `ls --full-time` no app.
- Sessão de hoje, canary 37.3.2 + `swiftshader_indirect` (07:48–07:5x): boot 57 s; Outlook subiu sozinho em segundo
  plano (`!SessionChange bg_boot, isForeground=false`), `Initiate hx` 07:49:53, `sortdefault.nls` criado 07:49:55.632,
  `Migration starting` 07:49:56.587, `Migration complete` 07:49:56.622, `Store boot complete (didStorageMigrate=true)`
  07:49:56.628, sem tombstone novo; ao abrir a UI o emulador sumiu — **observado**, `scratchpad/diag/applogs/*`,
  `hxcore-now.hfl` (1.185.583 bytes, sem `Assert`/`Crash`, com `MigrationStatistics: version: 4 ... newStoreFileSizeBytes:
  4194304`).
- Sessão 37.1.11 + `angle_indirect` (rejeitado → SwiftShader) e 37.1.11 + `swangle` (→ SwiftShader): usadas só para
  ler a seleção do renderizador; encerradas com `emu kill` sem abrir o Outlook — **observado**.
- Sessão 37.1.11 + `-gpu host` (07:5x–08:03): boot 79 s; UI do Outlook aberta por monkey; `SplashActivity` estável
  por 120 s; uma caixa "System UI isn't responding" (ANR do `com.android.systemui`, não do Outlook) dispensada com
  "Wait"; `Add account` → `AddAccountActivity` com campo de e-mail; nada digitado; `emu kill` — **observado**,
  `scratchpad/diag/outlook-host-gpu-splash.png`, `ui2.xml`, `ui3.xml`.
- Carga do host durante os testes: 5 emuladores de produção vivos (`qemu-system-x86_64-headless` ×5, 400–575 MB cada),
  RAM 64 GB com ~40 GB livres, CPU 2% antes de começar; um emulador de diagnóstico por vez — **observado**.

### Inferences

**Hipóteses de causa raiz, por evidência (da mais à menos sustentada):**

1. **Queda do host = bug do SwiftShader-GL legado do emulador na tela de onboarding do Outlook** — alta: 8/8 dumps
   com a mesma pilha, 3/3 correlações com a Splash/Lottie, `-gpu host` sobrevive, relato externo idêntico (24/09/2026).
2. **`UD2` = fail-fast do Hx por assert em `sortdefault.nls` (NLS ausente/atrasado), condição de estado/tempo** —
   alta para o mecanismo (log do Hx + registradores batem), média para "corrida da extração vs. migração" (alternativa:
   extração falhou naquela sessão). Agravada por: convidado lento (SwiftShader + host carregado) e por diretório de
   dados rasgado quando o emulador morre por fora.
3. **Recusa deliberada do ambiente emulado pelo Outlook** — baixa: nenhuma string de detecção na biblioteca nativa,
   o mesmo binário passa da migração em 4 de 6 aberturas e chega ao formulário de login.
4. **RAM insuficiente / Vulkan do convidado / hipervisor WHPX** — descartadas pelos dados: memória folgada (P11), o
   crash é no caminho GLES do host, e a `UD2` no convidado não derruba o hipervisor.
5. **Play Integrity / imagem não certificada** — sem evidência de participação antes do login; fica para o 23.13.

**Matriz de experimentos (um fator por vez; ordem sugerida):**

| # | Hipótese | Pré-requisitos | Passos | Dados | Esperado se a hipótese vale | Interpretação | Risco / custo | Parar quando |
|---|---|---|---|---|---|---|---|---|
| E1 (feito) | UD2 depende de estado/tempo, não do ambiente | `diag-outlook`, canary, mesmo store | reabrir sem `pm clear` | `appUpdates.log`, `ls files/data`, tombstones | migração conclui | **Confirmado hoje**: `Store boot complete`, `sortdefault.nls` presente | nenhum | — |
| E2 (feito) | Host cai por SwiftShader-GL, não pela `UD2` | AVD, 37.1.11 | `-gpu host`, abrir UI, 120 s | evento 1000, `dumpsys activity`, screenshot | emulador vive | **Confirmado**: Splash e `AddAccountActivity` estáveis | GPU do host ocupada; ~5 min | — |
| E3 | `skiavk` no convidado evita o SwiftShader-GL sem GPU | AVD, 37.1.11, `-gpu swiftshader_indirect` | `adb root; setprop debug.hwui.renderer skiavk; am force-stop; abrir UI`; repetir 5× | evento 1000, `dumpsys SurfaceFlinger`, frames | 5/5 sem queda | alternativa só-software para o parque e para o notebook | baixo; 15 min | 1 queda = hipótese falha |
| E4 | `-gpu host` funciona **como serviço** (sessão 0, `farm-central`) | 1 aparelho de QA (android-02 ou 09), `hw.gpu.mode=host` no `config.ini` via plataforma | subir pelo serviço, abrir Outlook, 5 min | logcat, evento 1000, uso de GPU (`nvidia-smi`), RAM | vive; GPU visível na sessão 0 | decide se o parque pode usar GPU sem janela | médio (toca o parque de QA); 20 min | queda ou renderizador `swiftshader` no log |
| E5 | Corrida do NLS: extração lenta vs. migração | AVD, `pm clear` | `pm clear`, abrir, medir `mtime` de `sortdefault.nls` vs. `Migration starting`, 5× com host carregado e 5× ocioso | `appUpdates.log`, `ls --full-time`, `hxcore.hfl` | UD2 só quando `sortdefault.nls` chega depois | fecha a causa da UD2; define margem de CPU por aparelho | baixo; 30 min | 10 aberturas |
| E6 | Store rasgado por morte externa reproduz a UD2 | AVD | abrir → `Store boot complete` → `emu kill` → reboot → abrir | tombstone, `hxcore.hfl` | UD2 ou `RecoveryMode` | mostra que quedas do host contaminam o store; motiva `pm clear` no reparo | baixo; 10 min | 3 ciclos |
| E7 | Notebook (i7-9850H, 37.2.11) tem o mesmo dump | dumps WER do notebook | ler `CrashDumps` com `mdparse.py` | pilha, módulos | `gles_swiftshader\libGLESv2.dll` na pilha | valida a generalização; o notebook tem uma NVIDIA T1000 (4 GB) disputada pelo Ollama, então `-gpu host` lá é possível mas não medido e E3 (`skiavk`) é o caminho mais seguro | nenhum; 10 min | — |
| E8 | 5.2637.3 muda algo | android-11, autorização P3 para "Update" | atualizar, `store/sync`, canário com E2/E3 aplicados | releases, canário | igual ou melhor | separa versão do app de renderizador | toca a loja; download 29 MB | — |

### Gaps

- E3 e E4 são os dois que faltam para escolher a configuração do parque; não foram feitos por tempo e porque E4 toca
  um aparelho de QA (não autorizado nesta pesquisa).
- Não medi o custo de CPU/GPU de `-gpu host` com vários emuladores simultâneos (ADR-056 e K-058 pedem cuidado com carga).
- O comportamento do `-gpu host` num serviço em sessão 0 (sem desktop) não foi verificado; o emulador de teste rodou na
  sessão interativa.

---

## Q6. Decisão: dá para rodar o Outlook nativo no emulador de forma suportada? Alternativas

### Takeaway

Sim, com evidência real de hoje: o Outlook nativo abre e chega ao login no emulador estável quando o renderizador não é
o SwiftShader-GL legado (`-gpu host` provado; `debug.hwui.renderer=skiavk` a medir). O próximo experimento decisivo é E3
(skiavk) seguido de E4 (GPU no serviço) — os dois em conjunto decidem qual dos dois vai para o
`config.ini`/`hardware-qemu.ini` dos aparelhos do parque e para o agente do notebook.

### Cited Findings

- Opções de renderizador do 37.1.11: `auto, host, software, lavapipe, swiftshader, swangle` (`-help-gpu`) —
  **observado**.
- `-gpu host` + Outlook: onboarding e formulário de e-mail visíveis, sem queda — **observado** (Q2/Q5).
- `debug.hwui.renderer=skiavk`: 240/240 lançamentos sem queda com `swiftshader_indirect`, "ranchu emulators with
  Vulkan 1.1", API 31+ — **externo**, [GetIcon PR #258](https://github.com/Lemkinator/GetIcon/pull/258).
- Microsoft Graph aceita conta pessoal: `Mail.Read` é permissão delegada consentível por conta Microsoft pessoal;
  registro do app com "Accounts in any organizational directory and personal Microsoft accounts" — **externo**,
  [Outlook mail API overview](https://learn.microsoft.com/en-us/graph/outlook-mail-concept-overview),
  [Permissions reference](https://learn.microsoft.com/en-us/graph/permissions-reference),
  [Microsoft Q&A: personal mailbox via Graph](https://learn.microsoft.com/en-us/answers/questions/1276530/acces-personal-mail-box-using-graph-api).
- O comando entre apps já provado no parque é "QA Messenger → Chrome" (24.1/24.3/24.4/24.7), e o recorte 24.9 é
  "Outlook → Instagram" — **inspecionado**, `docs/handoffs/terceira-evolucao.md:34`.

### Inferences

**Comparação de alternativas**

| Alternativa | O que preserva | O que muda | Limites de autenticação/automação | Custo | Desbloqueia |
|---|---|---|---|---|---|
| **A. Outlook nativo no emulador com renderizador diferente** (`-gpu host` no central; `skiavk` onde não há GPU) | Todo o escopo do pedido: app real, sessão por conta (23.4–23.6), `type_secret`, comando entre apps 24.9, aceite 27.2 | `hw.gpu.mode` (ou propriedade `debug.hwui.renderer`) por aparelho; possível pressão na GPU/CPU com N emuladores; no notebook a T1000 (4 GB) é disputada pelo Ollama: `-gpu host` possível mas não medido; `skiavk` é o caminho mais seguro lá | — | Baixo (config); E3/E4 ~1 h; sem gasto de API | 23.2 (promoção), 23.7, 23.8, 23.12, 23.13, 24.9, 27.2 |
| **B. Celular físico por USB como aparelho externo** | App real, fidelidade máxima, sem risco de renderizador | Novo tipo de worker/aparelho (transporte USB/TCP, `adb` remoto), hardware dedicado, política de rede por aparelho (ADR-056) não se aplica igual | Login igual ao A; a conta fica num hardware único (sem rodízio) | Alto (hardware + código de worker externo + validação) | Os mesmos do A, mas só para 1 persona por aparelho físico |
| **C. Outlook web (`outlook.live.com`) no Chrome do aparelho** | Emulador como está; leitura da caixa e comando entre apps (Chrome → Instagram) | Muda o escopo: não é o "app real"; layout mobile-web muda com frequência; sessão web separada da conta de app (23.4 modela app+site da mesma conta, então cabe no cofre) | — | Baixo; precisa de `telas.yaml` para o site | 24.9 (variante web), 27.2 parcial; **não** 23.7/23.8/23.12 como escritos |
| **D. Microsoft Graph / integração oficial** | Leitura confiável, sem tela, sem renderizador | Não é automação de app; exige registro de app no Entra, consentimento OAuth por conta (feito pela pessoa), tokens no cofre; foge do objetivo "IA opera o aparelho" | OAuth por consentimento humano; sem `type_secret`; compatível com contas pessoais `outlook.com` | Médio (código novo de integração; sem custo por chamada) | Só a parte "ler o assunto do último e-mail" do 24.9/27.2; nada de 23.x |

- (alta) A é a única que preserva o pedido do dono e tem prova real de viabilidade hoje; a decisão que falta é
  operacional (GPU do host em serviço vs. `skiavk`), não de produto.
- (média) B e C são planos de contingência: B se o parque não puder usar GPU nem `skiavk` de forma estável; C se o dono
  aceitar mudar o escopo do comando entre apps.
- (baixa) D só faz sentido como complemento de verificação (por exemplo, confirmar por API o que a IA leu na tela), não
  como substituto.

**Próximo experimento e o que decide:** E3 (`debug.hwui.renderer=skiavk` com `swiftshader_indirect`, 5 aberturas
seguidas da UI no `diag-outlook`, 37.1.11). Se 5/5 vivem, o parque inteiro (central e notebook) pode ficar em
software com uma propriedade por aparelho e o P15 fecha sem depender de GPU; se cair, fica `-gpu host` no central
(E4 para provar em serviço) e o notebook precisa de outra saída (B ou C para as personas remotas). Em paralelo, E5
define se o reparo automático do aparelho deve fazer `pm clear` do Outlook depois de uma morte externa do emulador
(previne a `UD2`/`RecoveryMode`).

### Gaps

- Não está provado que o Outlook aceite o login de conta pessoal neste ambiente (23.13 continua `not_run`, requer
  autorização e consentimento P5).
- Não está medido o impacto de `-gpu host` com 6+ emuladores simultâneos na RTX 2000 (4 GB de VRAM) nem se a sessão de
  serviço enxerga a GPU.
- `skiavk` não foi medido aqui; a evidência é externa (um repositório, 24/09/2026).
