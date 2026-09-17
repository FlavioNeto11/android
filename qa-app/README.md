# QA Messenger (`qa-app`)

App Android mínimo, **somente para QA**, usado como alvo de uma POC de automação de UI
(Appium / UiAutomator2 dirigido por IA). Ele imita um mensageiro simples (login, lista de conversas,
conversa, perfil) e expõe um `ContentProvider` para **verificação independente** e **injeção de falhas** via `adb`.

- Java puro: **sem AndroidX, sem bibliotecas de terceiros** (bloco `dependencies {}` vazio).
- Toda a interface em português do Brasil.
- Persistência em um único banco SQLite (`qa_messenger.db`) + sessão em `SharedPreferences`
  (tudo sobrevive a reinícios do app e do emulador).

> **Aviso de segurança (intencional):** as contas `qa-user-01` … `qa-user-10` e o PIN `1234` são
> **credenciais fictícias de teste**, fixas no código. O provider `com.pocqa.messenger.provider` é
> **exportado e sem permissão** de propósito, para que o `adb shell content ...` leia/alterar o estado.
> Nunca instale este app em um aparelho com dados reais nem reutilize esse padrão em produção.

## Versões fixadas (e por quê)

| Item | Versão | Motivo |
|---|---|---|
| Android Gradle Plugin (AGP) | **9.4.0** | Último AGP estável na data do build (set/2026) segundo <https://developer.android.com/build/releases/gradle-plugin>. Suporta até a API 37; a API 34 exige AGP ≥ 8.1.1. |
| Gradle (wrapper) | **9.6.0** | A tabela de compatibilidade do AGP 9.4 indica Gradle **mínimo = padrão = 9.6.0** (tabela "Plugin version → Minimum required Gradle version" em <https://developer.android.com/build/releases/about-agp>: 9.4 → 9.6.0). O `gradle-wrapper.properties` também fixa o `distributionSha256Sum` da distribuição. |
| JDK | **21** (Microsoft OpenJDK 21.0.12) | O AGP 9.4 exige JDK ≥ 17; o JDK 21 é LTS, é o instalado na máquina de build e é suportado pelo Gradle 9.6. O bytecode do app é Java 17 (`sourceCompatibility`/`targetCompatibility`). |
| compileSdk / targetSdk | **34** | Plataforma instalada (`platforms;android-34`); dentro do intervalo suportado pelo AGP 9.4 (máx. API 37). |
| minSdk | **26** | Requisito do contrato. |
| SDK Build Tools | **36.0.0** | Mínimo/padrão exigido pelo AGP 9.4 (mesma tabela). Não é declarado no `build.gradle`: o AGP usa a sua versão padrão e a **baixa automaticamente** para o SDK se faltar (as licenças já estão aceitas). O `build-tools;35.0.0` instalado é usado só para o `aapt2 dump badging` de conferência. |

O Gradle 9.7.0 instalado em `C:\Tools\gradle-9.7.0` foi usado **apenas** para gerar o wrapper
(`gradle wrapper --gradle-version 9.6.0 --distribution-type bin --gradle-distribution-sha256-sum ...`).
Todos os builds usam `gradlew.bat`.

## Como recompilar

```powershell
pwsh -File C:\git\android\scripts\build-qa-apk.ps1            # SDK padrão: C:\Android\Sdk
pwsh -File C:\git\android\scripts\build-qa-apk.ps1 -Clean     # clean + assembleDebug (use antes de versionar o APK)
pwsh -File C:\git\android\scripts\build-qa-apk.ps1 -SdkRoot D:\Android\Sdk
```

O script define `ANDROID_HOME`/`ANDROID_SDK_ROOT` só para o processo, cria `local.properties`
(`sdk.dir=C\:\\Android\\Sdk`, ignorado pelo git) se não existir, roda
`gradlew.bat assembleDebug --no-daemon` (com `-Clean`: `gradlew.bat clean assembleDebug --no-daemon`)
e copia o APK (assinado com a chave debug) para **`qa-app\dist\qa-messenger.apk`**, imprimindo caminho,
tamanho e SHA-256.

Notas:

- `-Clean` gera um APK compacto (~70 KiB). Em builds incrementais o empacotador do AGP pode deixar espaços
  vazios no `.apk` (arquivo bem maior, porém funcionalmente idêntico).
- O APK debug é assinado com `~/.android/debug.keystore` da máquina de build, então o SHA-256 muda de máquina
  para máquina (e sempre que o código muda); o valor impresso pelo script vale para aquele arquivo.
- `app/build.gradle` usa `android { enableKotlin = false }`: no AGP 9 o "built-in Kotlin" vem ligado por padrão e,
  sem essa linha, a `kotlin-stdlib` (~2,4 MB de dex) entraria no APK mesmo sem nenhum código Kotlin
  (<https://developer.android.com/build/migrate-to-built-in-kotlin>). As únicas classes além de
  `com.pocqa.messenger.*` no APK são os *global synthetics* que o próprio D8/AGP gera em builds debug.

Conferência do APK:

```powershell
C:\Android\Sdk\build-tools\35.0.0\aapt2.exe dump badging C:\git\android\qa-app\dist\qa-messenger.apk
```

Instalação: `adb -s <serial> install -r C:\git\android\qa-app\dist\qa-messenger.apk`

## Contrato do app

- `applicationId` / `namespace`: `com.pocqa.messenger` — rótulo `QA Messenger` — `versionName` `1.0.0`
- minSdk 26, targetSdk 34, compileSdk 34
- Tema: `@android:style/Theme.Material.Light.NoActionBar` (barras de cabeçalho próprias)
- Contas fictícias: `qa-user-01` … `qa-user-10`, PIN `1234` (a conta digitada é normalizada com trim + minúsculas)

Os ids abaixo são os `resource-id` vistos pelo UiAutomator como `com.pocqa.messenger:id/<id>`.

### 1. `LoginActivity` (exportada, `singleTop`)

| id | Tipo | Texto / comportamento |
|---|---|---|
| `login_title` | TextView | `Entrar no QA Messenger` |
| `login_account` | EditText | hint `Conta (ex.: qa-user-01)` |
| `login_pin` | EditText (`numberPassword`) | hint `PIN` |
| `login_button` | Button | `Entrar` |
| `login_error` | TextView | oculto; em falha mostra `Conta ou PIN inválidos` |
| `login_notice` | TextView | oculto; mostra `Sessão expirada. Entre novamente.` quando a tela abre por reautenticação forçada |

Sucesso → `MainActivity`. **Login automático** (provisionamento de teste): extras string `account` e `pin`.
Funciona também com o app já aberto e com outra conta logada (a sessão é substituída).

### 2. `MainActivity` (LAUNCHER, exportada)

Sem sessão → abre `LoginActivity` e encerra.

| id | Tipo | Texto / comportamento |
|---|---|---|
| `account_label` | TextView | exatamente `Conta: <conta>` |
| `btn_profile` | Button | `Perfil` → `ProfileActivity` |
| `btn_logout` | Button | `Sair` → limpa a sessão e volta ao login |
| `search_input` | EditText | hint `Buscar contato`; filtra a lista ao digitar (contém, sem diferenciar maiúsculas) |
| `conversation_list` | ListView | linhas com `conversation_name` e `conversation_preview` (corpo da última mensagem ou `Sem mensagens`) |

Contatos fixos, nesta ordem: `Suporte QA`, `QA-003`, `QA-002`, `QA-001`, `Equipe Testes`, `QA-010`, `QA-011`, `Arquivo`.
Tocar em uma linha abre a `ChatActivity` do contato. (Extra: `conversation_empty` aparece quando a busca não encontra nada.)

### 3. `ChatActivity`

| id | Tipo | Texto / comportamento |
|---|---|---|
| `chat_title` | TextView | nome exato do contato (ex.: `QA-001`) |
| `chat_account` | TextView | `como <conta>` |
| `chat_back` | ImageButton | contentDescription `Voltar` |
| `message_list` | ListView | empilha de baixo para cima e rola para a mensagem mais nova; linhas com `message_text`, `message_status`, `message_time` (`HH:mm`) |
| `message_input` | EditText | hint `Mensagem` (multilinha) |
| `send_button` | Button | texto `Enviar`, contentDescription `Enviar` |

Envio: entrada vazia/só espaços → nada acontece. Caso contrário o texto (com `trim`) é gravado com status
`Enviando…`, o campo é limpo e:

1. após `send_delay_ms` (padrão **700 ms**) → `Enviada ✓` — ou `Falha no envio ✕` se `send_fail` = `1`;
2. após mais **2000 ms** → `Entregue ✓✓` — **exceto** o contato `QA-002`, que fica em `Enviada ✓` para sempre;
3. só para o contato `QA-003`, após mais **3000 ms** → `Lida ✓✓`.

Strings exatas de status: `Enviando…` (U+2026), `Enviada ✓` (U+2713), `Entregue ✓✓`, `Lida ✓✓`, `Falha no envio ✕` (U+2715).

Detalhes de implementação relevantes para os testes:

- As transições rodam na `StatusEngine`, criada pela classe `Application` (`QaApp`) com um `Handler` de nível
  de processo — **não** dependem da Activity; cada transição é gravada no banco (`status` + `updated_at`)
  com *compare-and-set*.
- `send_delay_ms` e `send_fail` são lidas **no instante do toque em Enviar**; mudar a flag depois não altera
  mensagens já em voo. A flag `send_fail` continua ligada até ser alterada.
- Reinício do processo: linhas `Enviando…` com mais de **10 s** são avançadas imediatamente pelas mesmas regras
  (aqui `send_fail` é lida no momento da recuperação); cadeias paradas em `Enviada ✓`/`Entregue ✓✓` também são retomadas.
- O provider aplica transições já vencidas antes de responder a uma consulta de `messages`.

### 4. `ProfileActivity`

| id | Tipo | Texto / comportamento |
|---|---|---|
| `profile_name` / `profile_email` / `profile_city` | EditText | hints `Nome`, `E-mail`, `Cidade` |
| `profile_notifications` | CheckBox | `Receber notificações` |
| `profile_save` | Button | `Salvar` |
| `profile_saved_banner` | TextView | oculto até salvar; depois `Perfil salvo às HH:mm` |
| `profile_back` | ImageButton | contentDescription `Voltar` |

Carrega os valores já salvos da conta logada (tabela `profile`). Os campos são gravados com `trim`.
(Extras: `profile_title` = `Perfil`, `profile_account` = nome da conta.)

### 5. Flags de injeção de falha (tabela `flags`)

Avaliadas no `onResume` de `MainActivity` e `ChatActivity` (ou seja: depois de gravar a flag, navegue, reabra
ou traga o app para frente). Ordem: `force_reauth` antes de `interstitial`.

| key | Valores | Efeito |
|---|---|---|
| `interstitial` | `0` / `1` | `1` → janela bloqueante em tela cheia (não fecha com Voltar), título `Novidades da versão` (`interstitial_title`), texto (`interstitial_body`) e um único botão `interstitial_dismiss` (`Agora não`). Dispensar grava `0`. |
| `force_reauth` | `0` / `1` | `1` → limpa a sessão, grava `0` e abre a `LoginActivity` mostrando `login_notice`. |
| `send_delay_ms` | inteiro (ms) | atraso `Enviando…` → `Enviada ✓` (padrão 700; limitado a 0–600000). |
| `send_fail` | `0` / `1` | `1` → a mensagem vai para `Falha no envio ✕` em vez de `Enviada ✓`. |

### Banco de dados

```
messages(_id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, contact TEXT, body TEXT,
         status TEXT, created_at INTEGER, updated_at INTEGER)      -- tempos em epoch ms
flags(key TEXT PRIMARY KEY, value TEXT)
profile(account TEXT PRIMARY KEY, name TEXT, email TEXT, city TEXT, notifications INTEGER, saved_at INTEGER)
```

### ContentProvider exportado

Authority `com.pocqa.messenger.provider`, `exported="true"`, **sem permissões** (app só de QA).
O usuário `shell` do adb acessa providers exportados sem precisar de `<queries>`, inclusive com targetSdk 34 e
com o app parado (o sistema sobe o processo).

| URI | Operações |
|---|---|
| `content://com.pocqa.messenger.provider/messages` | `query` (projection/selection/selectionArgs/sortOrder; padrão `_id ASC`) e `delete`. `insert`/`update` → erro (somente leitura). |
| `content://com.pocqa.messenger.provider/flags` | `query`; `insert` com `key`,`value` = **UPSERT**; `delete`; (`update` de `value` também é aceito). |
| `content://com.pocqa.messenger.provider/session` | `query` → uma linha, coluna `account` (string vazia se deslogado). |
| `content://com.pocqa.messenger.provider/profile` | `query`; (`delete` aceito como extra, para zerar dados de teste). |

## Exemplos com adb

Use sempre `-s <serial>` quando houver mais de um dispositivo. Os exemplos abaixo são para o **PowerShell**
(aspas simples por fora; as aspas duplas chegam intactas ao shell do Android).

```powershell
$adb = 'C:\Android\Sdk\platform-tools\adb.exe'; $s = 'emulator-5554'
$P = 'content://com.pocqa.messenger.provider'

# Instalar
& $adb -s $s install -r C:\git\android\qa-app\dist\qa-messenger.apk

# Login automático (conta fictícia)
& $adb -s $s shell am start -n com.pocqa.messenger/.LoginActivity --es account qa-user-03 --es pin 1234

# Sessão atual
& $adb -s $s shell content query --uri $P/session

# Mensagens: todas / filtradas / só algumas colunas
& $adb -s $s shell content query --uri $P/messages
& $adb -s $s shell "content query --uri $P/messages --projection _id:contact:body:status --where ""account='qa-user-03' AND contact='QA-001'"" --sort ""_id DESC"""

# Perfis salvos e flags atuais
& $adb -s $s shell content query --uri $P/profile
& $adb -s $s shell content query --uri $P/flags

# Ligar / desligar cada flag (insert = UPSERT)
& $adb -s $s shell content insert --uri $P/flags --bind key:s:interstitial  --bind value:s:1
& $adb -s $s shell content insert --uri $P/flags --bind key:s:interstitial  --bind value:s:0
& $adb -s $s shell content insert --uri $P/flags --bind key:s:force_reauth  --bind value:s:1
& $adb -s $s shell content insert --uri $P/flags --bind key:s:force_reauth  --bind value:s:0
& $adb -s $s shell content insert --uri $P/flags --bind key:s:send_delay_ms --bind value:s:5000
& $adb -s $s shell content insert --uri $P/flags --bind key:s:send_delay_ms --bind value:s:700
& $adb -s $s shell content insert --uri $P/flags --bind key:s:send_fail     --bind value:s:1
& $adb -s $s shell content insert --uri $P/flags --bind key:s:send_fail     --bind value:s:0

# Apagar uma flag (volta ao padrão) ou todas
& $adb -s $s shell "content delete --uri $P/flags --where ""key='send_fail'"""
& $adb -s $s shell content delete --uri $P/flags

# Zerar mensagens (todas, ou só de uma conta)
& $adb -s $s shell content delete --uri $P/messages
& $adb -s $s shell "content delete --uri $P/messages --where ""account='qa-user-03'"""

# Zerar tudo (banco + sessão)
& $adb -s $s shell pm clear com.pocqa.messenger
```

Em `bash`/`sh` os mesmos comandos funcionam trocando as aspas, por exemplo:

```sh
adb -s emulator-5554 shell "content query --uri content://com.pocqa.messenger.provider/messages --where \"contact='QA-001'\""
```

## Estrutura

```
qa-app/
  settings.gradle, build.gradle, gradle.properties, gradlew, gradlew.bat, gradle/wrapper/*
  app/build.gradle
  app/src/main/AndroidManifest.xml
  app/src/main/java/com/pocqa/messenger/   Contract, Db, Session, QaApp, StatusEngine, QaProvider,
                                           BaseActivity, LoginActivity, MainActivity, ChatActivity, ProfileActivity
  app/src/main/res/                        layouts, strings (pt-BR), cores, tema, ícone
  dist/qa-messenger.apk                    APK debug gerado pelo script (artefato pequeno, versionável)
```
