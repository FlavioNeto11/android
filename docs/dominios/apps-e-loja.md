# Domínio: apps, releases e loja

Como um aplicativo Android chega ao parque, com que confiança de assinatura, e como o painel decide "para onde
distribuir". Para o contrato HTTP das rotas citadas, ver [`../api-contract.md`](../api-contract.md); para o
banco, [`../banco.md`](../banco.md).

## Duas coisas chamadas "catálogo" — não confundir

1. **Registro de apps** (`backend/app/modules/applications/infrastructure/registry.py`, desde a fase K1; o nome
   antigo `planning/catalog/__init__.py` é shim com os mesmos objetos) — o que um app SABE fazer e declara sobre si:
   o manifesto `AppDefinition` ([abaixo](#manifesto-de-app-fase-k1)). `AppCapabilities` é o mesmo tipo pelo nome
   antigo. App não registrado cai no "caminho livre" (`domain/definition.py::neutral`): sem catálogo de ações, sem
   sessão determinística, sem exigir perfil. `capabilities_of(package)` nunca lança nem devolve `None`.
   `registered()` lista todos (usado pela UI para oferecer escolha de app em vez de assumir Instagram).
   `package_of_provider(provider)` faz a pergunta inversa (qual pacote provê um tipo de conta; sem chamador no
   núcleo desde o ADR-052), e `pacote_ancora()` diz qual é o app da conta da persona. Rotas:
   `GET /api/app-catalog` (`api.py::app_catalog`), `GET /api/capabilities?package=` (`api.py::list_capabilities`,
   pacote obrigatório — antes tinha Instagram por omissão e vazava capabilities erradas para outro app).
2. **Catálogo de releases** (`backend/app/releases/catalog.py`) — descoberta/validação/armazenamento de
   conjuntos de APK (não tem relação com ações/capabilities). `discover`/`group_loose`/`_extract_container`
   (`.apks`/`.xapk`/`.apkm`); `validate()` exige pacote único, `versionCode` único, assinatura única, exatamente
   um `base.apk`; `store()` grava em `<pacote>/<versionCode>-<hash de conteúdo>/`; `extract_icon()` fica FORA de
   `store()` de propósito — ícone não participa de `app_release_files` nem do hash do conjunto.

Migração `031_catalogo_visual.sql` só faz `ALTER TABLE app_releases ADD COLUMN label`/`icon_file` (não cria
tabela) — rótulo e ícone lidos do `base.apk` (`aapt2 dump badging`); nulo = release importada antes da migração
ou APK sem rótulo/ícone servível.

## Manifesto de app (fase K1)

Tudo o que o núcleo sabe de um app, ele sabe pelo manifesto que o app registra. É a fase K1 da evolução arquitetural
([design](../design/evolucao-arquitetural.md) §2.5 e §16;
[ADR-039](../decisoes.md#adr-039--manifesto-de-app-e-registro-de-sessionprovider)). Código: `0b7950e`, `99d851b`,
`40def91`, `01d68b5` e `15dfded`, integrados em `f06e34a`, mais a correção `3fbe9df`. Caminhos relativos a
`backend/app/`.

**O que é.**

- **`AppDefinition`** (`modules/applications/domain/definition.py`): só dado, imutável.
  - `package`, `name`, `label` (o nome nas mensagens do despacho, no lugar do "Instagram" fixo);
  - `session_provider`: o **tipo** do provedor de sessão de conta. `None` = sem conta gerenciada;
  - `needs_profile`, `requires_internet`, `has_catalog` (derivado do catálogo recebido, nunca declarado à mão);
  - `text_kinds` (capability → tipo de texto que ela escreve na voz do perfil) e `conversation_reads` (capability de
    leitura → tipo da interação de entrada). Moravam em `state.py` como `_TIPO_DE_TEXTO` e `_LEITURA_DE_CONVERSA`.
    Consultas: `AppDefinition.text_kind` e `AppDefinition.conversation_read`.
- **`AppManifest`** (`modules/applications/infrastructure/registry.py`): a definição mais as peças com comportamento,
  que o domínio não enxerga (regra D2):
  - `catalog`: o `CapabilityCatalog` do app;
  - `screen`: um `ScreenReader` (`visible_content`, `comment_of`, `message_of`), que o rascunho usa para falar do que
    está na tela;
  - `session`: a fábrica do provedor de sessão (`SessionProviderFactory`, em
    `modules/identity/infrastructure/sessions.py`), que recebe as dependências da composição (`SessionDeps`) e devolve
    um `SessionProvider` ([perfis](perfis-e-instagram.md#sessionprovider-e-o-registro-por-pacote-fase-k1)).

**Um app é dado (ADR-052).** Zero Python por app: o manifesto de cada app é montado de uma pasta
`app/conhecimento/apps/<pacote>/` pelo descobridor `integrations/app_declarado/pacote.py::descobrir`, com os motores
genéricos — classificação de tela e volta ao estado conhecido em `automation/conhecimento_de_telas.py`, leitura para
o rascunho em `automation/leitura_de_tela.py::LeituraDeclarada` (o `ScreenReader`), catálogo em
`planning/capabilities.py::carregar_catalogo`, login em `integrations/app_declarado/sessao.py::SessaoDeclarada` (o
`SessionProvider`) ([design](../design/conhecimento-de-app.md);
[perfis](perfis-e-instagram.md#o-instagram-como-dado-adr-052)). Desde a fatia 4, o app âncora do perfil (onde
vivem a credencial e a sessão da persona) vem do registro (`registry.py::pacote_ancora`, o app cujo `app.yaml` diz
`ancora_do_perfil: true`; no máximo um, e dois é erro), no lugar de `package_of_provider("instagram")`, e os links de
perfil vêm de `links_de_perfil` do `app.yaml`.

**Como acrescentar um app, sem Python e sem tocar no núcleo.** Criar a pasta `app/conhecimento/apps/<pacote>/`
(o nome é o pacote Android) com:

1. `app.yaml` (obrigatório): `app` (o mesmo pacote da pasta), `nome`, `rotulo`, `provedor_de_sessao` (só com conta
   gerenciada), `precisa_de_perfil`, `precisa_de_internet`, `ancora_do_perfil`, `links_de_perfil`, `tipos_de_texto`,
   `leituras_de_conversa`, `leitura` e `renderizador_recusado` (campo fora desta lista é recusado:
   `pacote.py::_CAMPOS`);
2. `catalogo.yaml`, se o app tem ações: `app`, `contract_version` e `acoes`;
3. `telas.yaml` + `sessao.yaml`, se o app tem conta gerenciada: telas e estado conhecido, login declarado.
   `provedor_de_sessao` e `sessao.yaml` vêm juntos ou não vêm (`pacote.py::manifesto_da_pasta` recusa um sem o
   outro).

Arquivo errado falha na carga, com o caminho do campo, e derruba a descoberta inteira: um pacote pela metade seria
pior que nenhum. `register_manifest(manifesto)` continua valendo para teste e extensão (o QA, abaixo).

O registro confere na entrada (`registry.py::register`):

- fábrica de sessão sem `session_provider` declarado é recusada (`ValueError`): a porta de sessão e a invalidação
  responderiam coisas diferentes para o mesmo app;
- catálogo de outro pacote é recusado;
- registrar de novo substitui; `unregister` tira.

Daí em diante, o núcleo pergunta ao registro, sem `if` por app:

| Quem pergunta | O quê | Onde |
|---|---|---|
| porta de sessão do despacho | o provedor do pacote do item | `state.py::AppState._session_gate` → `SessionProviders.for_package` |
| invalidação ao mexer no disco de um app | o app tem provedor? | `state.py::AppState._sessao_apos_mudanca_de_app` → `SessionProviders.has` |
| "tela contradiz a sessão" no executor | o app tem provedor? | `taskqueue/executor.py::StepExecutor._sessao_desmentida` → `session_provider_of` |
| rótulo do app no bloqueio do ADR-029 e no aviso de invalidação | `label` | `state.py::AppState._sessao_desmentida`, `::_invalidate_sessions` |
| rascunho na voz do perfil | `text_kind` e o `ScreenReader` | `state.py::AppState._draft_gate` |
| leitura de conversa vira histórico | `conversation_read` | `state.py::AppState._registrar_leitura` |
| "login automático" no painel | o app tem provedor? | `apps_overview.py`, `social/service.py::SocialService._account_dto` |
| sessão pelos recursos (fase H) | o manifesto diz que tem login automático; o registro dá o provedor | `modules/execution/infrastructure/providers.py::tem_provedor_de_sessao`, `command_bus.py` (correção `3fbe9df`) |

**Embutidos.** `registry.py::_BUILTINS` guarda pares (módulo relativo, função) de **descobridores**, importados na
primeira consulta (`_importar`, o único `import_module` do backend). Hoje há um: `integrations/app_declarado/pacote.py::descobrir`,
que devolve o manifesto de cada pasta de `app/conhecimento/apps/`. `_descobrir_embutidos` os guarda em `_EMBUTIDOS`
(para voltarem depois de um `unregister`) e registra os que ninguém registrou à mão. O pacote vem do `app.yaml` de
cada pasta. Por importação preguiçosa, e não no topo: o descobridor importa o carregador do catálogo e o motor de
sessão, que perguntam a este registro (ciclo).

**O Instagram é um pacote de dado descoberto** (`app/conhecimento/apps/com.instagram.android/`,
[perfis](perfis-e-instagram.md#o-instagram-como-dado-adr-052)), montado por `pacote.py::manifesto_da_pasta`:

- definição (`app.yaml`, por `pacote.py::definicao_de_dados`): `provedor_de_sessao: instagram`,
  `precisa_de_perfil`, `precisa_de_internet`, `rotulo: Instagram`, os mapas `tipos_de_texto` (`CREATE_COMMENT`,
  `REPLY_COMMENT`, `SEND_MESSAGE`) e `leituras_de_conversa` (`READ_MESSAGES`);
- catálogo: `catalogo.yaml` (23 ações, `contract_version: 1`), por `planning/capabilities.py::catalogo_do_pacote`;
- leitura de tela: a seção `leitura` do `app.yaml`, por `automation/leitura_de_tela.py::LeituraDeclarada`;
- sessão: `telas.yaml` + `sessao.yaml`, por `pacote.py::fabrica_de_sessao`, que monta o motor genérico
  `integrations/app_declarado/sessao.py::SessaoDeclarada` com o conhecimento do app.

**O Outlook é o segundo pacote de dado** (`app/conhecimento/apps/com.microsoft.office.outlook/`, item 23.8,
[perfis](perfis-e-instagram.md#o-outlook-como-dado-item-238)): `provedor_de_sessao: microsoft` sem ser âncora,
`telas.yaml` + `sessao.yaml` com o login em etapas da conta Microsoft, e um `catalogo.yaml` SÓ DE LEITURA (item
12.3: `OPEN_MAIL_INBOX`, `COLLECT_MAIL_HEADERS` e `SEARCH_MAIL`, nenhuma com efeito). Enviar, responder e abrir uma
mensagem (que a marca como lida) ficam fora: uma etapa com efeito no Outlook sem ação dele é recusada pela porta de
política (item 13.2, `manual_only`), e etapa livre num app com catálogo vira pergunta no planejador (ADR-058).

**Valor entre etapas pelo catálogo (ADR-065).** Com catálogo o Outlook deixou de ser app de etapa livre, então a ação
de catálogo passou a poder entregar um valor lido (o `read_value` da etapa livre, item 24.3) a outra etapa, sem
Python por app: a ação declara no `catalogo.yaml` os nomes que PODE entregar (`saidas: [remetente, assunto]` em
`OPEN_MAIL_INBOX` e `SEARCH_MAIL`); o planejador entre apps diz, por etapa, quais usa (`saidas` da etapa, só nomes
da lista; fora dela, ou numa ação sem `saidas`, vira pergunta e o plano sai sem etapas); a etapa seguinte os cita como
`{{saida:<nome>}}`. Quem lê é o executor, do texto do elemento na tela, com a triagem de segredo de sempre: código de
verificação, senha e token nunca são saída (por isso não há nome `codigo` e o cenário "ler o código no Outlook" segue
com a pessoa). A coleta não declara `saidas` (a lista vai pelo `for_each`). Etapa de catálogo com `saidas` não usa
receita (ler é decisão sobre a tela da vez); sem `saidas` na etapa, tudo segue como antes. O que ele declara depois da senha é
suposição, marcada nos arquivos.

**O QA Messenger é a prova de extensibilidade, e só em teste.** `backend/tests/fake_dois_apps.py::manifesto_do_qa`
registra o QA por `register_manifest()` com três capabilities (`QA_OPEN_CHAT`, `QA_COMPOSE`, `QA_SEND_MESSAGE`) e um
provedor de sessão (`SessaoDoQa`), e o tira no fim. Não é embutido de propósito: com catálogo, o QA mudaria
`_policy_gate` e `_mistura_de_apps` da suíte inteira. Em produção, o QA segue no caminho livre.

Provas (`simulated`, harness na porta 5640, sem aparelho, conta ou IA real):

- `backend/tests/test_app_novo_pelo_manifesto.py::test_app_novo_entra_so_pelo_registro_com_provedor_e_catalogo`: a
  porta de sessão chama o provedor do pacote (IG → `SessaoDeclarada`; QA → `SessaoDoQa`, fabricado com as
  dependências da composição), a invalidação passa a valer para o QA, e fora do registro ele volta ao caminho livre;
- `::test_skill_do_qa_compila_e_executa_pelo_caminho_de_skills` e
  `::test_processo_cross_app_instagram_e_qa_num_aparelho_so` ([runtime](../skill-runtime.md#processo-cross-app-fase-k1));
- `backend/tests/test_apps_fora_do_nucleo.py`: nenhuma comparação com `"instagram"` no núcleo, por AST, com a lista
  de exceções vazia; `app/integrations/` só tem o motor genérico (`test_integracoes_so_tem_o_motor_generico`); o
  texto "instagram" no código só desce (`TEXTO_LEGADO`, sem contar nomes históricos como `instagram_profiles` e o
  prefixo `/api/instagram/`);
- `backend/tests/test_pacote_declarado.py`: um app de e-mail fictício, só em dado, entra no registro com catálogo,
  leitura e login; o Instagram é descoberto da pasta real. Também `test_catalogo_como_dado.py`,
  `test_sessao_declarada.py` e `test_conhecimento_de_telas.py` (ADR-052);
- `backend/tests/test_dubles_cumprem_as_portas.py`: o `SessaoDoQa` e o `SessaoDeclarada` com o conhecimento do
  Instagram contra `SessionProvider`.

O Outlook pela pasta real: `simulated` (`backend/tests/test_outlook_declarado.py`); no aparelho, `not_run` (29.12).
Um app real novo pelo manifesto além desses: `not_run` (o item 12.3 do plano-100 espera a escolha do dono).

## Releases: ciclo de vida

Dois eixos independentes em `backend/app/models.py`:

- **`ReleaseState`** (`imported, inspected, validated, installable, invalid, incompatible`) — "este ARQUIVO pode
  ser instalado?" (integridade/assinatura/compatibilidade).
- **`ReleaseChannel`** (`candidate, canary, promoted, quarantined, rolled_back`) — "esta VERSÃO já provou que
  funciona?".

Confiança de assinatura é PROGRESSIVA: a primeira release de um pacote entra `validated`; só vira `installable`
quando o operador aprova a assinatura explicitamente (`approve_signature`, `releases/service.py::approve_signature`)
— depois disso, qualquer release do mesmo pacote com assinatura DIFERENTE é bloqueada automaticamente
(`invalid`). O sistema não vira autoridade de procedência, mas também não aceita em silêncio um APK de outra
origem numa atualização futura.

Canário → promoção → quarentena → rollback (`releases/service.py`, função `ReleaseService`):

- `start_canary(rt, release_id, installer)` — recusa se o pacote já tem release `promoted` (é preciso
  colocar em quarentena antes de trocar); marca `channel=canary` e `canary_instance_id`.
- `promote(release_id, note=None)` — exige `channel==canary` **e** prova registrada de `install` **e**
  `launch` em `app_release_validations` (migração `011_release_lifecycle.sql`) para aquele
  `canary_instance_id`, ambas `ok` — nunca promove por "lembrança de quem clicou".
- `quarantine(release_id, reason=None)` — bloqueia instalação sem apagar nada.
- `promoted_release(package_name)` — a MAIOR versão entre as `promoted` (podem coexistir várias). No empate de
  `version_code`, a promovida por último (`channel_at`) e depois o maior id, ordenado em Python (K-030): é a versão
  que o parque inteiro persegue (ADR-026), e ela não pode alternar entre duas.
- `rollback(rt, package, installer, preserve=True, note=None)` — `preserve=True` tenta `install -r -d` (o
  Android pode recusar downgrade); `preserve=False` desinstala antes (apaga sessão/dados do app) — a API nunca
  escolhe o caminho destrutivo sozinha, exige `confirm_reinstall` no corpo.
- `ensure_builtin_release(...)` — bootstrap do app de QA embutido no boot, pulando assinatura e canário só para
  `builtin=True`.

Rotas: `GET /api/releases`, `POST /api/releases/import`, `POST /api/releases/upload`,
`POST /api/releases/{id}/approve-signature`, `POST /api/releases/{id}/lifecycle` (verbo único no corpo:
`promote|quarantine|canary|rollback|distribute`; `canary`/`rollback` exigem `instance_id`), `GET
/api/releases/{id}/targets` (usa `motivo_incompativel`, ver Compatibilidade abaixo) — todas em
`backend/app/api.py` (linhas 1041–1230, ver [`../api-contract.md`](../api-contract.md) para os adendos).

## A loja (Play Store) como fonte

Não há módulo `Store` dedicado — a lógica vive em `ReleaseService` e nas rotas de `api.py`. Um aparelho marcado
`store` (`instances.store`) é o ÚNICO lugar onde a Play Store roda de verdade; nunca é destino de trabalho nem
de distribuição (recusado tanto em `taskqueue/service.py::create` quanto no rodízio, `scheduler.py::_rotate`
via `rodiziavel()`).

- `GET /api/store?package=` — compara `store_version_code` observado vs `catalog_version_code` vs
  `fleet_target_release_id` (`api.py:1267-1276`; pacote obrigatório, "a loja não assume um app por omissão").
- `POST /api/store/open-listing` — abre a página do app na Play Store da loja; instalar/atualizar é toque do
  USUÁRIO, nunca automatizado pelo backend.
- `POST /api/store/sync` (202) — `ReleaseService::sync_from_store`: copia por `adb pull` os APKs que o usuário
  JÁ instalou pela Play Store (nada é baixado da rede pelo backend); se o conjunto já está catalogado
  (mesmos splits), devolve `outcome: "unchanged"`; senão importa com `source_type="store"`.

Rota exige a loja já `online` (não liga sozinha — evitaria devolver 202 que falha em silêncio).

## Distribuição

Dois mecanismos com o mesmo verbo, em subsistemas diferentes — não confundir:

**(a) Distribuir uma EXECUÇÃO entre servidores** (item 10.5, ver também
[`parque.md`](parque.md#limites-por-servidor-item-105)) — `DistributeSpec` (`models.py`: `count` 1–64,
`app_id`) dentro de `RunCreate.distribute`; é mutuamente exclusivo com `instance_ids`/`profile_ids`
(validador `_um_dos_dois`). `RunService.create` resolve `distribute` em `instance_ids` chamando
`taskqueue/service.py::_distribuir`, que usa `Scheduler.candidatos_do_app` + `Scheduler.servidores()` +
`balanceamento.distribuir()`; recusa loja como alvo, recusa mistura de apps com catálogo, aplica o mesmo pré-voo
de compatibilidade que qualquer execução. `POST /api/runs/distribution` (corpo `{count, app_id?, command?}`)
(`api.py:2429-2434`, `previa_de_distribuicao`) mostra quem SERIA escolhido agora, sem criar nada — é a prévia
que o painel usa antes de confirmar.

**(b) Distribuir uma RELEASE ao parque** (`eager`) — `AppState.distribute(release_id, eager=False)`
(`backend/app/state.py:1021+`): exige `status=="installable"` e `channel=="promoted"`; por aparelho não-loja,
grava `desired_release_id` e despacha `app.distribute` se online, ou deixa `pending` se desligado.
`eager=True` ("instalar em todos agora") faz os aparelhos pendentes virarem demanda do rodízio
(liga dentro das vagas em vez de esperar a próxima tarefa qualquer). Acionado por
`POST /api/releases/{id}/lifecycle` com verbo `distribute` e `eager` no corpo.

### Para quem, prévia e apps secundários (loja de apps, 26/09)

- **Para quem.** `distribute` aceita `instance_ids` (os escolhidos) ou `count` (N aparelhos, escolhidos por
  `vitrine.escolher_para_distribuir`: compatíveis e fora da versão; ligado antes de desligado, desta máquina antes
  de outra, quem já tem o app antes de quem nunca teve, e por último o id). Sem os dois, o parque inteiro. Quem não
  é escolhido não tem versão desejada gravada.
- **Prévia.** Com `dry_run`, é o mesmo julgamento aparelho por aparelho (`vitrine.previa_de_entrega`), sem gravar,
  instalar, abrir comando nem acordar o rodízio. O painel confirma com os MESMOS ids da prévia.
- **Apps secundários.** Antes, só o app principal do aparelho (`instances.app_id`) convergia sozinho: a porta do app
  instala antes da tarefa, e `aplicar_versao_promovida` o adota ao ligar. Um Outlook distribuído para um aparelho
  de Instagram desligado ficava pendente para sempre, sem "instalar agora", porque nenhuma tarefa do Outlook chega
  ali. Agora, quando o aparelho entra no ar, `_reobservar_se_velho` chama `vitrine.trabalho_ao_ligar`, que instala
  no mesmo trabalho de reobservação. Um segundo `run_device_job` seria recusado. Vale a mesma trava da porta: versão
  entregável, compatível, em estado de entrega automática e sem operação aberta. Falha não entra: a nova tentativa,
  no máximo uma por dia, é rearmada por `aplicar_versao_promovida` (ADR-026). Desde o ADR-026, o app principal
  entra nesse mesmo trabalho, exceto com um objetivo no meio (`vitrine.objetivo_em_andamento`).
- **Ligado e livre.** Dois casos o gancho de "entrou no ar" não alcança: o aparelho que estava ocupado na hora de
  distribuir e o que já estava ligado quando o backend reiniciou. Para eles, `vitrine.laco_de_convergencia` roda a
  cada 60 s (só no hospedeiro) e chama `convergir_ligados`, que entrega o mesmo `trabalho_ao_ligar` a aparelho ligado
  e livre. Ela não liga ninguém (isso é o "instalar agora"), pula aparelho com objetivo esperando para não passar na
  frente de tarefa, e não repete falha.

## Loja de aplicativos no painel (26/09)

Menu Aplicativos, aba **Loja** (`frontend/src/features/loja/`): uma vitrine com um cartão por app cadastrado
(`GET /api/app-store`, `backend/app/vitrine.py::vitrine`). O cartão mostra o ícone do APK, a versão promovida,
quantos aparelhos têm o app e a "atualização para N" (quem está numa versão menor que a promovida). A página do app
reúne:

- a nova versão, pelo envio do APK/XAPK que o dono fornece ou pela Play Store da loja;
- as versões com o ciclo de vida (aprovar a assinatura, provar, promover, quarentena e distribuir, este só na
  promovida mais nova);
- o diálogo **Distribuir** (todos / N / escolher, com prévia obrigatória);
- a faixa **Atualizar para X**, que abre o diálogo com os atrasados já marcados;
- a tabela de aparelhos, ao vivo, com a volta de versão em lote.

**Cadastro.** Com categoria (lista fixa do dono: social, mensagens, e-mail, rede (VPN/proxy), utilitário, QA;
migração 041). Uma versão de pacote não cadastrado cadastra o app sozinha (`vitrine.cadastrar_app_se_novo`,
chamado por `ReleaseService._import_one`). Cadastrar não instala nada, e a IA opera o app novo pelo "caminho livre"
até existir um catálogo de ações para ele.

**Volta em lote e "substituída".** A volta em lote é a mesma volta por aparelho, repetida. O `rollback` existente
marca a versão de onde o aparelho saiu como `rolled_back` para o PARQUE inteiro, e ela deixa de ser a promovida.
Decisão do dono (26/09, ADR-026): continua assim, e o parque vai junto. No fim do trabalho da volta,
`vitrine.convergir_o_parque` leva os outros aparelhos que estão na versão voltada para a promovida anterior, e o
diálogo avisa disso. Ver "Todos na versão promovida" abaixo.

## Todos na versão promovida (ADR-026, 26/09)

Decisão do dono: "todos devem ficar atualizados sempre". A versão promovida é o estado desejado de TODO aparelho de
tarefa que tem o app, principal ou secundário. Ninguém precisa clicar em "Distribuir" para atualizar.

- **Quem tem o app.** Linha em `device_app_state` com `installed_release_id` ou `observed_version_code`, ou com
  versão desejada gravada (`AppState.tem_o_app`). O app principal do aparelho conta mesmo sem linha, a regra de antes.
  Fora disso, nada é instalado em quem não tem o app: espalhar é `distribute`.
- **Promover** (`verb: promote`) chama `vitrine.convergir_o_parque`. Ele grava a desejada em quem tem o app
  (`aplicar_versao_promovida(rt, pacote)`) e roda a varredura uma vez (`convergir_ligados`). O ligado e livre instala
  já; o ocupado, na varredura de 60 s, sem passar na frente de tarefa; o desligado, quando ligar. Nenhum aparelho é
  ligado. A resposta traz `target_release_id` e `devices[]` (`started | pending | already | incompatible | kept`).
- **Entrar no ar e varredura.** `AppState.adotar_promovidas` roda para o app principal e para cada app que o
  aparelho tem, no gancho de "entrou no ar" e em cada passada de `convergir_ligados`. A entrega segue pelas vias de
  sempre (`trabalho_ao_ligar`, porta do app). Limite conhecido: um objetivo em `waiting_user` de execução ainda não
  encerrada (inclusive `completed_with_issues`, que pode ser retomada) conta como "no meio", e o app principal
  daquele aparelho não é entregue pela varredura até a execução ser retomada ou cancelada. A porta do app entrega
  antes da próxima tarefa, como antes.
- **Voltar.** Rebaixar da versão `rolled_back` vai com `-d`, preservando os dados (`AppState._entregar`). Recusa
  do Android vira `install_failed` + `downgrade_refused` e não se repete sozinha. O parque só volta junto se a
  versão anterior ainda for promovida (o caso comum: promover não despromove a anterior); sem promovida nenhuma,
  cada aparelho fica onde está. O desejo que apontava para uma
  versão que não pode mais ser entregue se realinha, em vez de bloquear a tarefa.
- **Fica onde está** (`AppState.fora_da_convergencia`): quem tem uma versão MAIS NOVA que ninguém voltou (o canário
  em prova, o app atualizado por fora do catálogo) e quem já está numa promovida de mesmo número (os dois builds
  1.0.0/1 do app de QA na produção). A **quarentena** para de espalhar a versão, mas não rebaixa quem está nela: isso
  é a volta.
- **Falha.** Nova tentativa automática no máximo uma vez por dia. O relógio é a última tentativa (comando de app ou
  prova de instalação em `app_release_validations`): a entrega sem tarefa não abre comando (K-032).
- **O secundário não fica na frente.** Depois da prova de abertura de um app que não é o principal do aparelho,
  `ReleaseService._recolher_se_secundario` volta à tela inicial e faz `am force-stop` do pacote
  (`AppInstaller.recolher`). A prova vale do mesmo jeito, e o convidado de 1,5 GB recupera a memória. Sem isso, o app
  de QA distribuído ao android-01 (26/09) ficou na frente e o "Abrir app" do Instagram esperou 90 s em vão, duas
  vezes. O "Abrir app" (`DeviceManager.open_app`) também volta à tela inicial antes do `am start` quando outro pacote
  está em foco.

## Proxy do aparelho (26/09)

`backend/app/devices/proxy.py`, aba **Proxy**. Um proxy nomeado (`proxy_profiles`: nome, host, porta) é pedido
para os aparelhos escolhidos (`device_proxy_state`, desejado × observado).

- O ligado recebe agora, como comando `device.proxy`, e o desligado quando ligar, no mesmo trabalho dos apps
  secundários. Ligado e ocupado recebe pela varredura quando fica livre.
- O alvo é explícito: os aparelhos, ou `all: true` para o parque inteiro. Nunca é inferido pela falta de lista.
  Um proxy fora do ar derruba a internet de todas as contas de uma vez.
- O mecanismo é o proxy global do Android: `settings put global http_proxy host:porta`, e `:0` para tirar.
- `applied` só quando a releitura devolve o pedido. **Isso prova a configuração, não o tráfego**: app que ignora o
  proxy do sistema sai direto.
- Sem autenticação: o Android não tem esse campo, e a senha seria segredo.
- A loja fica de fora, e falha não se repete sozinha.
- Rebaixado pela rede por aparelho (ADR-056): a visão `GET /api/network/devices` lê este proxy como `configurado`
  **no máximo**, nunca como tráfego verificado. Perfis com segredo, atribuição e estados medidos estão em
  [parque § Rede por aparelho](parque.md#rede-por-aparelho-adr-056-fase-25).

## Compatibilidade

`backend/app/devices/compatibilidade.py` — ponto único "este app roda neste aparelho?", usado no pré-voo de
execução, em `release_targets` e em `distribute()`:

- `Requisitos` (de uma release: `min_sdk`, `abis`, `requires_gms`; do `app.yaml` do app: `renderizador_recusado`).
- `Capacidades` (do aparelho: `device_kind`, `system_image`, `api_level`, `abis`, `play_store`, `renderizador`).
- `motivo_incompativel(req, cap, aparelho=None)` — quatro checagens em ordem: nível de API, ABI, GMS e renderizador
  do emulador. **Regra central: capacidade desconhecida (`None`) nunca vira recusa** — quem não declarou nada passa;
  a prova real continua sendo a instalação em si. O renderizador é a exceção, descrita abaixo.

### Renderizador recusado pelo app (item 29.11)

`renderizador_recusado` é uma chave opcional do `app.yaml`: a lista dos renderizadores do emulador em que o app
derruba o emulador. Os valores aceitos são os que o emulador seleciona de fato, `host` e `swiftshader` (o apelido
`swiftshader_indirect` vale por `swiftshader`); valor desconhecido, ou a lista com os dois, é recusado na carga. O
Outlook declara `renderizador_recusado: [swiftshader]`, com a medição de 30/09/2026 no comentário
([parque § Renderizador do emulador](parque.md#renderizador-do-emulador-item-2911)).

O que a plataforma compara é o renderizador **selecionado** pelo emulador quando ele é conhecido (`renderer.gles` do
aparelho); sem ele, o **configurado** (`gpu_mode`). Com o renderizador recusado, o app não é instalado nem aberto
naquele aparelho, e a frase diz o motivo e o que configurar ("Outlook derruba o emulador com o renderizador
SwiftShader, que é o deste aparelho; configure `gpu_mode: host` neste aparelho e reinicie-o"). Quando o pedido era
`host` e o emulador caiu para o SwiftShader, a frase diz isso e manda ler o log, em vez de mandar configurar o que já
está configurado.

| Onde | Como recusa |
|---|---|
| Canário (`start_canary`, `POST /releases/{id}/lifecycle`) | 409 `app_incompativel` antes de aceitar; o canal da versão não muda |
| Instalação (`install_on`, `POST /instances/{id}/app/install`) | 409 `app_incompativel`; nenhum estado gravado |
| "Abrir app" e "Instalar" do aparelho (`POST /instances/{id}/actions/open_app` e `install_apk`, e o lote) | 409 `app_incompativel` no pré-voo do verbo (`despacho._precheck`); a recusa fica no histórico do aparelho |
| Volta de versão (`rollback`) | a mesma recusa: ela instala e abre o app |
| Distribuição e prévia (`distribute`, `dry_run`, "N aparelhos") | `outcome: incompatible` com o motivo; a versão desejada não é gravada |
| Destinos (`GET /releases/{id}/targets`) e convergência | `compatible: false`; o aparelho não adota a promovida |
| Entrega automática (`_entregar`) | recusa sem virar `install_failed` |
| Pré-voo e porta do app da tarefa (`_app_preflight`, `_app_resolver`) | `app_incompativel` antes de agendar; no despacho, o objetivo espera uma pessoa |

**Renderizador desconhecido é recusa**, só para o app que tem a chave: a "prova pela instalação" aqui seria a queda
do processo do emulador, com a sessão de quem estiver logado nele. Acontece no aparelho de um worker cujo agente
ainda não declara o renderizador; a frase diz "renderizador desconhecido" e pede para atualizar o agente. Aparelho
físico ou contêiner não tem renderizador de emulador, e o requisito não se aplica. App sem a chave não muda em nada.

Prova: `simulated`, `backend/tests/test_renderizador.py`. O canário do Outlook no android-07 com `gpu_mode: host` é
`real` (30/09/2026, antes desta recusa existir); a recusa no ambiente central é `not_run`.

## Capacidades — implementação e validação

| Capacidade | Implementação | Validação | Origem |
|---|---|---|---|
| Catálogo visual (rótulo/ícone extraídos do APK) | implementado | simulada (`proof: simulated`) | `releases/inspector.py`, `releases/catalog.py`, migração 031; plano-100 id 6.3 |
| Ciclo de vida de release (canário→promoção→quarentena→rollback) | implementado | automatizada (`tests/test_release_lifecycle.py`) — ver também relatorio-validacao.md §9 (o que está exercitado em teste × o que não está) | `releases/service.py`; [`../relatorio-validacao.md`](../relatorio-validacao.md) §9 |
| Loja como fonte (sync do que o usuário instalou) | implementado | automatizada (`tests/test_store_sync.py`) + ambiente real em 18/09 | `releases/service.py::sync_from_store`; relatorio-validacao.md §10 |
| Loja num worker remoto | implementado | simulada (`proof: simulated`) | plano-100 id 6.5 (decisão 4, opção a, 24/09) |
| Distribuição entre servidores por carga (10.5) | implementado | automatizada (`tests/test_limites_por_servidor.py`, 15 casos); **nunca com dois workers reais** | `taskqueue/service.py`, `taskqueue/balanceamento.py`; plano-100 id 10.5; relatorio-validacao.md §13 aceite 5 |
| Distribuição de release ao parque (`eager`) | implementado | automatizada (`tests/test_distribute.py`) | `state.py::distribute` |
| Compatibilidade app × aparelho | implementado | automatizada (`tests/test_capacidades_declaradas.py`) | `devices/compatibilidade.py` |
| Renderizador recusado pelo app (`renderizador_recusado`) | implementado | `simulated` (`tests/test_renderizador.py`); recusa no ambiente central `not_run` | `devices/compatibilidade.py`, `integrations/app_declarado/pacote.py`; plano-100 id 29.11 |
| Distribuição por alvo (escolhidos / N) com prévia | implementado | simulada em SQLite (`tests/test_loja_de_apps.py`); PostgreSQL e parque real **não executados** | `state.py::distribute`, `vitrine.py` |
| App secundário instala ao ligar | implementado | simulada (`tests/test_loja_de_apps.py`, com controle negativo) | `vitrine.py::trabalho_ao_ligar` |
| Cadastro automático do app no import | implementado | simulada (`tests/test_loja_de_apps.py`) | `vitrine.py::cadastrar_app_se_novo` |
| Vitrine (`/api/app-store`) e tela Loja | implementado | simulada (`tests/test_loja_de_apps.py`, `frontend/src/features/loja/LojaPage.test.tsx`) + navegador contra o harness (porta 8765, aparelhos falsos) em 26/09 | `vitrine.py`, `features/loja/` |
| Proxy do aparelho | implementado | simulada (`tests/test_loja_de_apps.py`); **não executada** em emulador real | `devices/proxy.py` |
| Manifesto de app (`AppDefinition` + `AppManifest`) e registro por `register_manifest` | implementado | `simulated` (`tests/test_app_novo_pelo_manifesto.py`, `tests/test_registro_de_apps.py`, `tests/test_apps_fora_do_nucleo.py`); PostgreSQL e app real novo `not_run` | `modules/applications/`; fase K1, [ADR-039](../decisoes.md#adr-039--manifesto-de-app-e-registro-de-sessionprovider) |
| App como dado: manifesto descoberto de `app/conhecimento/apps/<pacote>/`, motores genéricos, Instagram sem Python | implementado | `simulated` (`tests/test_pacote_declarado.py`, `tests/test_sessao_declarada.py`, `tests/test_catalogo_como_dado.py`, `tests/test_conhecimento_de_telas.py`, `tests/test_apps_fora_do_nucleo.py`, `tests/test_instagram_auth.py`); aparelho e conta reais `not_run` | `integrations/app_declarado/`, `app/conhecimento/apps/com.instagram.android/`; ADR-052, [design](../design/conhecimento-de-app.md) |
| Entrega pelo catálogo de releases num worker remoto | implementado | **ambiente real para o Instagram**: conjunto 447 (base + config.xhdpi) instalado nos seis remotos em 23/09 21:02–21:05 (plano-100 6.6, `proof: real`); app que **não** é o Instagram pelo catálogo num remoto (aceite 4) continua não exercitado | estado.json 6.6; relatorio-validacao.md §13 aceite 4 (registro de 23/09, anterior à prova do 6.6) |

Backlog:

- App de QA embutido ainda não migrado automaticamente para o fluxo de release; `apps` e `app_releases`
  continuam duas tabelas (blocker registrado no plano-100 id 6.3).
- Provar entrega pelo catálogo (canário→promover→distribuir) com progresso no painel num worker remoto — hoje
  só provado localmente e nos remotos com instalação direta (relatorio-validacao.md §13, aceite 4).
