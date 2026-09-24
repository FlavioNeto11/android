# Domínio: apps, releases e loja

Como um aplicativo Android chega ao parque, com que confiança de assinatura, e como o painel decide "para onde
distribuir". Para o contrato HTTP das rotas citadas, ver [`../api-contract.md`](../api-contract.md); para o
banco, [`../banco.md`](../banco.md).

## Duas coisas chamadas "catálogo" — não confundir

1. **Registro de capabilities por app** (`backend/app/planning/catalog/__init__.py`) — o que um app SABE fazer:
   `AppCapabilities` (`package`, `name`, `has_catalog`, `session_provider`, `needs_profile`, `label`).
   `_BUILTINS` só registra `com.instagram.android` hoje; app não registrado cai no "caminho livre"
   (`_neutro()`): sem catálogo de ações, sem sessão determinística, sem exigir perfil. `capabilities_of(package)`
   nunca lança nem devolve `None`. `registered()` lista todos (usado pela UI para oferecer escolha de app em vez
   de assumir Instagram). `package_of_provider(provider)` faz a pergunta inversa (qual pacote provê um tipo de
   conta). Rotas: `GET /api/app-catalog` (`api.py:864-873`), `GET /api/capabilities?package=` (`api.py:876-889`,
   pacote obrigatório — antes tinha Instagram por omissão e vazava capabilities erradas para outro app).
2. **Catálogo de releases** (`backend/app/releases/catalog.py`) — descoberta/validação/armazenamento de
   conjuntos de APK (não tem relação com ações/capabilities). `discover`/`group_loose`/`_extract_container`
   (`.apks`/`.xapk`/`.apkm`); `validate()` exige pacote único, `versionCode` único, assinatura única, exatamente
   um `base.apk`; `store()` grava em `<pacote>/<versionCode>-<hash de conteúdo>/`; `extract_icon()` fica FORA de
   `store()` de propósito — ícone não participa de `app_release_files` nem do hash do conjunto.

Migração `031_catalogo_visual.sql` só faz `ALTER TABLE app_releases ADD COLUMN label`/`icon_file` (não cria
tabela) — rótulo e ícone lidos do `base.apk` (`aapt2 dump badging`); nulo = release importada antes da migração
ou APK sem rótulo/ícone servível.

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
- `promoted_release(package_name)` — a MAIOR versão entre as `promoted` (podem coexistir várias).
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
de compatibilidade que qualquer execução. `GET /api/runs/distribution?count=&app_id=`
(`api.py:2429-2434`, `previa_de_distribuicao`) mostra quem SERIA escolhido agora, sem criar nada — é a prévia
que o painel usa antes de confirmar.

**(b) Distribuir uma RELEASE ao parque** (`eager`) — `AppState.distribute(release_id, eager=False)`
(`backend/app/state.py:1021+`): exige `status=="installable"` e `channel=="promoted"`; por aparelho não-loja,
grava `desired_release_id` e despacha `app.distribute` se online, ou deixa `pending` se desligado.
`eager=True` ("instalar em todos agora") faz os aparelhos pendentes virarem demanda do rodízio
(liga dentro das vagas em vez de esperar a próxima tarefa qualquer). Acionado por
`POST /api/releases/{id}/lifecycle` com verbo `distribute` e `eager` no corpo.

## Compatibilidade

`backend/app/devices/compatibilidade.py` — ponto único "este app roda neste aparelho?", usado no pré-voo de
execução, em `release_targets` e em `distribute()`:

- `Requisitos` (de uma release: `min_sdk`, `abis`, `requires_gms`).
- `Capacidades` (do aparelho: `device_kind`, `system_image`, `api_level`, `abis`, `play_store`).
- `motivo_incompativel(req, cap, aparelho=None)` — três checagens em ordem: nível de API, ABI, GMS. **Regra
  central: capacidade desconhecida (`None`) nunca vira recusa** — quem não declarou nada passa; a prova real
  continua sendo a instalação em si.

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
| Entrega pelo catálogo de releases num worker remoto | implementado | **ambiente real para o Instagram**: conjunto 447 (base + config.xhdpi) instalado nos seis remotos em 23/09 21:02–21:05 (plano-100 6.6, `proof: real`); app que **não** é o Instagram pelo catálogo num remoto (aceite 4) continua não exercitado | estado.json 6.6; relatorio-validacao.md §13 aceite 4 (registro de 23/09, anterior à prova do 6.6) |

Backlog:

- App de QA embutido ainda não migrado automaticamente para o fluxo de release; `apps` e `app_releases`
  continuam duas tabelas (blocker registrado no plano-100 id 6.3).
- Provar entrega pelo catálogo (canário→promover→distribuir) com progresso no painel num worker remoto — hoje
  só provado localmente e nos remotos com instalação direta (relatorio-validacao.md §13, aceite 4).
