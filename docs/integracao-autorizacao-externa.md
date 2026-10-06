# Integração com um serviço externo de autorização: protocolo do consumidor

Especificação técnica genérica do protocolo que o backend (o **consumidor**) fala com um **serviço externo de
autorização**: esquema do pedido e da resposta, autenticação, estados, validade e modos por domínio. É o documento do
item 33.2 do plano-100; os adaptadores (33.3 a 33.7) implementam pedaços dele e não o repetem.

**Estado: desenho, sem código** (prova: `not_run`). **Nenhum domínio está habilitado:** todos seguem em `local`, e o
controle local nunca afrouxa; só sai o que a troca de modo de um domínio disser. O número no fio é `"0"` (campo
`protocolo`, cabeçalho `X-Protocolo`, base `/v0/`). "Previsto na próxima versão" marca rascunho não aprovado.

## 1. Propósito e escopo

Antes de avançar sobre um recurso (gerar, planejar, despachar, tocar a tela, aprender, repetir), o consumidor
**pergunta** ao serviço. A resposta restringe ou libera; o consumidor verifica a resposta, reserva o uso e só então age.

1. **Falha fechada.** Serviço fora do ar, resposta fora do esquema, assinatura inválida ou prazo vencido: a operação
   não avança. Nenhum ramo transforma ausência de resposta em autorização.
2. **Resposta opaca.** Só estado genérico, ids opacos, resumos, prazos, host de portal e, quando houver, um artefato.
   Nenhum campo de texto livre vindo do serviço, nem no erro.
3. **Vínculo estreito.** Uma autorização vale para um consumidor, uma operação, um conjunto de recursos, um resumo de
   conteúdo e uma revisão de observação, por prazo curto, **uma vez**. Não existe autorização geral e duradoura.
4. **Parar sempre pode.** Parar, pausar, cancelar, hibernar e restrição pedida por pessoa são locais e só se registram (§ 7).
5. **A camada local de aprovação e de política por ação fica** e decide primeiro: a operação só sai se as duas
   camadas permitem. Nenhum campo do protocolo carrega teto, janela por conta, classe de risco ou aprovação local.
6. **Domínio não habilitado não autoriza:** o serviço responde `dominio_nao_habilitado` (§ 8).

**Transporte.** HTTP/1.1, JSON UTF-8. O consumidor recusa `http://` fora de loopback; fora dele, só `https://` com
certificado verificado (a autenticação protege origem e integridade, não sigilo). O worker remoto não fala com o
serviço: a autorização chega a ele no despacho do central. Tetos: pedido 64 KiB, árvore redigida e texto 256 KiB,
imagem 16 MiB, resposta 32 KiB; acima, `413` (`schema_invalido`).

| Rota | Para quê |
|---|---|
| `GET /v0/versao` | protocolos aceitos, `kid` das chaves vigentes, `agora` do serviço e `dominios_externos` deste consumidor |
| `PUT /v0/conteudos/{sha256}` · `GET ...` | upload endereçado por conteúdo (o serviço confere o resumo) · leitura de artefato que ele emitiu |
| `POST /v0/pedidos` | pedido (§ 2.1) → resposta (§ 2.2) |
| `GET /v0/decisoes/{decisao_id}` | estado atual de uma decisão |
| `POST /v0/decisoes/{id}/reserva` · `POST /v0/reservas/{id}/consumo` · `.../desfecho` | reserva e consumo antes do efeito; evidência depois (`disparado`, `nao_disparado`, `incerto`) |
| `POST /v0/decisoes/{id}/ensaio` | só em `sombra`: o efeito saiu pela autoridade local (§ 6) |
| `GET /v0/eventos?cursor=…&espera_s=25` | espera longa: lista de `{evento_id, decisao_id}` que mudaram |
| `POST /v0/restricoes` | registro de operação que só restringe (§ 7) |

Toda chamada parte do consumidor; não existe rota de entrada nele. Sem callback não há o que forjar, e um evento falso
só causaria uma consulta (`GET /v0/decisoes/{id}`), cuja resposta é verificada como qualquer outra.

## 2. Esquema do pedido e da resposta

Todo objeto tem `additionalProperties: false`; no consumidor, pydantic com `extra="forbid"`. Campo desconhecido é
`schema_invalido` e o avanço para: não se "ignora o que não se entende".

### 2.1 Pedido (`POST /v0/pedidos`)

| Campo | Tipo | Restrição e nota |
|---|---|---|
| `protocolo` | string | `"0"` |
| `pedido_id` | string | `^req_[0-9a-z]{26}$`, gerado pelo consumidor |
| `chave_de_idempotencia` | string | `^[0-9a-f]{64}$`, determinística (§ 5.1) |
| `consumidor` | objeto | `{id, componente, worker_id, versao}`; `componente` é `central` ou `worker`; `worker_id` obrigatório só se `worker` |
| `operacao` | objeto | `{dominio, tipo, ref}`; `dominio` é caminho técnico, nunca tema; `ref` é id opaco e estável |
| `acao_tecnica` | objeto | `{ferramenta, efeito_possivel, alvo, argumentos_sha256, pacote}`; `alvo` leva `{resource_id, classe, limites, tem_texto}`; os argumentos vão só por resumo JCS |
| `recursos` | lista | 1 a 16 `{tipo, id}` ordenados por `(tipo,id)`; `tipo` em `aparelho`, `execucao`, `objetivo`, `etapa`, `tentativa`, `app`, `conta`, `persona`, `imagem`, `receita`, `fluxo`, `release`, `comando`, `aprovacao` |
| `conteudo` | objeto | `{sha256, tipo, tamanho}`; `tipo` em `texto`, `imagem`, `plano`, `sequencia`, `parametros`, `nenhum`; sobe antes por `PUT` |
| `observacao` | objeto ou null | `{revisao, instancia, frame_id, capturada_em, pacote, arvore_sha256, imagem_sha256, sensivel}`; null só onde não há tela |
| `fatos_de_recurso` | lista | 0 a 16 fatos técnicos fechados por tipo (`persona`, `imagem`, `conta`), sem julgamento |
| `dados_tecnicos` | objeto | fechado; `origem_da_decisao` (`ia`, `receita`, `fluxo`, `pessoa`, `servico`) é obrigatório em `acao`, `replay` e `sessao` |
| `enviado_em` | string | RFC 3339 UTC |

Domínios: `geracao`, `planejamento`, `despacho`, `acao`, `sessao`, `aprendizagem`, `replay`, `worker`, `painel`,
`canais`, cada um com lista fechada de `operacao.tipo` (por exemplo `acao.gesto`, `despacho.iniciar_tentativa`,
`painel.sair_de_estado_protetor`). Os tipos dizem o que o consumidor quer FAZER, nunca por que seria permitido.

`observacao.revisao` é o sha256 JCS de `{instancia, geracao_do_runtime, frame_id, assinatura_da_arvore}`, com a
assinatura **completa** da árvore (a estrutural ignora texto, e uma troca de conteúdo passaria). A árvore enviada é a
redigida: todos os nós, só os atributos que o analisador lê e o valor de todo campo de senha trocado por máscara.
Credencial, valor do cofre, prompt e justificativa nunca entram no pedido.

```json
{"protocolo": "0", "pedido_id": "req_01k6x3t5w9m2c8d4f7h1j0n6pq", "chave_de_idempotencia": "<64 hex>",
 "consumidor": {"id": "central-a1", "componente": "central", "worker_id": null, "versao": "1.0.0"},
 "operacao": {"dominio": "acao", "tipo": "gesto", "ref": "act_7c2e91d4"},
 "acao_tecnica": {"ferramenta": "tap", "efeito_possivel": true, "argumentos_sha256": "<64 hex>",
                  "alvo": {"resource_id": "app:id/botao", "classe": "android.widget.Button",
                           "limites": [96, 1710, 984, 1830], "tem_texto": true}, "pacote": "com.exemplo.app"},
 "recursos": [{"tipo": "aparelho", "id": "inst_0042"}, {"tipo": "tentativa", "id": "att_a3f0"}],
 "conteudo": {"sha256": "<64 hex do vazio>", "tipo": "nenhum", "tamanho": 0},
 "observacao": {"revisao": "<64 hex>", "instancia": "inst_0042", "frame_id": "frm_88213",
                "capturada_em": "2026-10-05T16:02:11Z", "pacote": "com.exemplo.app",
                "arvore_sha256": "<64 hex>", "imagem_sha256": null, "sensivel": false},
 "fatos_de_recurso": [], "dados_tecnicos": {"origem_da_decisao": "ia"}, "enviado_em": "2026-10-05T16:02:11Z"}
```

### 2.2 Resposta

| Campo | Tipo | Presença |
|---|---|---|
| `protocolo`, `decisao_id` (`^dec_[0-9a-z]{26}$`), `pedido_id` (eco) | string | sempre |
| `estado` | `autorizado`, `recusado` ou `aguardando` | sempre |
| `conteudo_sha256`, `observacao_revisao` | hex64 ou null | sempre (eco do pedido, ou o do artefato) |
| `autorizacao`, `escopo`, `valida_ate`, `uso` | envelope JWS ≤ 4 KiB; `{consumidor, componente, dominio, tipo, ref, recursos_sha256, acao_sha256}`; RFC 3339; `reserva_e_consumo` ou `consumida_na_emissao` | só se `autorizado` |
| `artefato` | `{sha256, midia, tamanho}` ou null; `midia` em `text/plain;charset=utf-8`, `image/jpeg`, `image/png` | só se `autorizado` |
| `sequencia` | sempre null | não nulo é `schema_invalido` (sequência de ações do serviço desligada nesta versão) |
| `portal`, `consultar_apos_s` | `{host}` da lista da instalação; int 1..300 | só se `aguardando` |

A presença por estado é **validação**, não convenção: `recusado` com `artefato`, ou `aguardando` com `sequencia`, é
`schema_invalido`; `recusado` traz só os campos de "sempre". Toda string é id com padrão, hex, data, enum ou host da
lista: não há `mensagem`, `motivo`, `detalhe` nem `nota`. Em `aguardando`, o consumidor monta
`https://<host>/d/<decisao_id>` e guarda só o `decisao_id`; um caminho vindo do serviço seria um canal de texto livre
até a pessoa. Com artefato, o consumidor baixa por `GET /v0/conteudos/{sha256}`, confere o resumo e usa os bytes como
vieram (qualquer mudança depois é `conteudo_diferente`).

```json
{"protocolo": "0", "decisao_id": "dec_01k6x3t6a1b2c3d4e5f6g7h8j9", "pedido_id": "req_01k6x3t5w9m2c8d4f7h1j0n6pq",
 "estado": "autorizado", "autorizacao": "<jws>", "valida_ate": "2026-10-05T16:02:41Z", "uso": "reserva_e_consumo",
 "escopo": {"consumidor": "central-a1", "componente": "central", "dominio": "acao", "tipo": "gesto",
            "ref": "act_7c2e91d4", "recursos_sha256": "<64 hex>", "acao_sha256": "<64 hex>"},
 "conteudo_sha256": "<64 hex>", "observacao_revisao": "<64 hex>", "artefato": null, "sequencia": null,
 "portal": null, "consultar_apos_s": null}
```

Previsto na próxima versão: `acao_tecnica` ganha o par do catálogo de onde o gesto saiu e `app_da_sessao` (em
`sessao`, o app da sessão deixa de dividir o campo `pacote`); o pedido ganha `telas_declaradas` (resumo por app, que o
serviço só compara); a resposta ganha `restricoes` (§ 7). Campos novos seriam obrigatórios onde a versão disser.

## 3. Autenticação e integridade

### 3.1 Assinatura do pedido

HMAC-SHA256 de cada requisição, com segredo por consumidor e por instalação. Liga a credencial ao corpo exato e ao
instante (um token de portador vazado num log valeria para qualquer pedido); mTLS exigiria infraestrutura de
certificados que não existe para um serviço em loopback.

- Cabeçalhos: `X-Consumidor`, `X-Chave-Id` (qual segredo, para rotação), `X-Carimbo` (RFC 3339 UTC), `X-Nonce` (16
  bytes aleatórios, base64url), `X-Protocolo`, `X-Assinatura`.
- Texto assinado: `metodo \n caminho \n sha256(corpo) \n carimbo \n nonce \n consumidor`.
- O serviço recusa carimbo fora de ±30 s e nonce repetido em 10 min: `401`, `autenticacao_recusada`; o consumidor
  para e não repete a mesma requisição.
- O segredo fica no `.env` do consumidor (lido como `SecretStr`) e num arquivo da instalação do serviço, ambos fora do
  Git; nunca em `config.yaml`, log, evento ou prompt. O worker não tem segredo.

### 3.2 Envelope da autorização

Ed25519 verificado localmente **mais** reserva e consumo no serviço (§ 5). A verificação local, sem rede, é o que leva a
autorização ao worker; a consulta ao serviço dá uso único e revogação. Só assinatura deixaria reuso por duas réplicas
sem resposta. JWS compacto lido por código estrito (não biblioteca de JWT genérica, contra confusão de algoritmo):
cabeçalho exatamente `{"alg":"EdDSA","typ":"autorizacao+v0","kid":"<16 hex>"}`; outro valor ou campo a mais é
`assinatura_invalida`. Carga em JSON canônico (JCS): `v`, `jti` (o `decisao_id`), `iss`, `aud` (o `consumidor.id`, ou o
`worker_id`), `com`, `dom`, `op`, `ref`, `rec` (sha256 dos recursos), `act` (sha256 de `acao_tecnica`), `cnt` (conteúdo
ou artefato), `obs` (revisão ou null), `uso`, `iat`, `nbf`, `exp`.

O consumidor confere **todos** os itens com o que vai fazer AGORA; um falhou, o avanço para:

1. assinatura com a chave do `kid` no conjunto local (`chave_desconhecida`, `assinatura_invalida`);
2. `aud` e `com` iguais ao próprio consumidor (`consumidor_errado`);
3. `dom`, `op`, `ref`, `rec`, `act` recalculados (`escopo_diferente`);
4. `cnt` igual ao resumo do conteúdo que vai usar (`conteudo_diferente`);
5. `obs` igual à revisão corrente, relida logo antes do gesto (`observacao_diferente`);
6. `nbf - 30 s ≤ agora ≤ exp` (`ainda_nao_valida`, `expirada`);
7. `jti` ainda não usado localmente (`reuso`).

### 3.3 Chaves, relógio e rotação

- `kid` são os 16 primeiros hex do sha256 da chave pública. O conjunto de chaves públicas é um arquivo JSON por
  instalação, posto pelo responsável pela instalação e fora do Git, relido quando muda. Não vem do serviço: chave trocada por terceiro é
  exatamente o ataque.
- **Relógio.** A tolerância de 30 s vale só no início, nunca estende o fim. Se o `agora` de `GET /v0/versao` difere do
  relógio local em mais de 30 s, os domínios em `externo` ficam suspensos (`relogio_divergente`).
- **Rotação do segredo HMAC.** Chave nova no serviço com outro `chave_id`, troca no `.env`, reinício da tarefa, e só
  então a antiga sai; as duas valem durante a troca.
- **Rotação da chave de assinatura.** A nova entra nos consumidores, o serviço passa a assinar com ela, e a antiga sai
  depois do maior prazo (1 h) mais 30 s. Revogar é retirá-la do arquivo: o que ela assinou deixa de valer na hora.
  O material de chave é gerado e instalado pelo responsável pela instalação e não passa por log, evento nem prompt.

## 4. Estados e vereditos

| Estado | O que o consumidor faz |
|---|---|
| `autorizado` | verifica o envelope (§ 3.2), reserva, consome e só então age |
| `recusado` | a operação não sai; a etapa ou o pedido fecha como "recusado pelo controle externo", sem motivo |
| `aguardando` | a operação pausa; painel e canais mostram "aguardando controle externo" e o link do portal; consulta de novo após `consultar_apos_s` ou ao receber o evento |
| erro técnico | o avanço para; comportamento por código no § 8 |

```
           ┌──────────► recusado (final)
pedido ────┼──────────► aguardando ──► autorizado | recusado
           └──────────► autorizado ──► reservado ──► consumido (final)
                             │              └──► expirada (reserva caducou; final)
                             ├──► expirada (prazo; final)
                             └──► revogada (final)
```

O consumidor só vê três estados genéricos. `expirada` e `revogada` chegam como erro na reserva ou no consumo, e uma
consulta posterior devolve `recusado`. Estado final não volta: uma decisão morta nunca retorna a `autorizado`.

**Retomada depois de `aguardando`.** Ao virar `autorizado`, a decisão está presa à observação do pedido original. O
consumidor relê a tela: revisão igual, segue; revisão diferente (o caso comum depois de minutos), nova observação,
nova chave e novo pedido, que pode voltar `autorizado` na hora. O mesmo vale para conteúdo, alvo ou plano mudados.

## 5. Validade, uso único, reserva e consumo

Prazo (`exp - iat`): o serviço pode dar menos; o consumidor nunca estende.

| Domínio | Prazo | Motivo |
|---|---|---|
| `acao`, `replay`, `sessao`, `painel.entrada_manual` | 30 s | a observação envelhece em segundos |
| `worker` | 120 s | cobre a viagem pelo túnel e a fila de boot |
| `despacho` | 300 s | do despacho até a primeira observação |
| `planejamento`, `geracao`, `aprendizagem`, `canais`, demais do `painel` | até 1 h | não há tela; o vínculo é o resumo |

### 5.1 Idempotência e sequência de chamadas

`chave_de_idempotencia` é o sha256 JCS de `{consumidor, componente, dominio, tipo, ref, recursos, acao_sha256,
conteudo_sha256, observacao_revisao}`; o `ref` é estável entre reinícios (id da intenção, tentativa ou comando) e nunca
aleatório. A mesma chave devolve a MESMA decisão enquanto ela valer; contexto mudou, outra chave e outra decisão.

1. `POST /v0/pedidos` devolve `autorizado` com envelope, e o consumidor o verifica (§ 3.2).
2. `POST .../reserva` com `{chave_de_idempotencia, instancia_do_consumidor, observacao_revisao_atual, efeito_ref}`: troca
   atômica `autorizado → reservado`, devolve `reserva_id` e `reservada_ate` (20 s); senão `reuso`, `expirada`,
   `revogada` ou `observacao_diferente`.
3. O consumidor grava a intenção da ação, com `decisao_id` e `reserva_id`, **antes** do efeito.
4. `POST .../consumo` (`reservado → consumido`). **Só depois disso o efeito sai.**
5. `POST .../desfecho`: evidência; não desfaz nada.

Com `efeito_possivel=false`, o serviço pode responder `uso=consumida_na_emissao`: a decisão já nasce consumida e o
consumidor dispensa os passos 2 e 4 (a escolha é do serviço). Continua de uso único localmente e presa à observação.

### 5.2 Uso único, restauração de banco e reinício

O consumidor guarda uma tabela local de uso (`decisao_id` com chave única, `reserva_id`, domínio, tipo, `ref`, estado,
prazo; só ids e resumos): defesa em profundidade, recusa o segundo registro mesmo que o serviço erre. Duas réplicas do
backend podem pedir com a mesma chave e recebem a mesma decisão; só uma vence a reserva, a outra recebe `reuso`.

| Reinício do consumidor | O que acontece |
|---|---|
| antes da reserva | nada no serviço; na volta, nova observação, nova chave, novo pedido |
| entre reserva e consumo | a reserva caduca (20 s); a intenção fica sem consumo e fecha como `nao_disparado` |
| entre consumo e efeito, ou durante | vale a regra do executor para intenção sem desfecho (efeito possível conta como incerto); a reconciliação manda `incerto`; a decisão consumida nunca é reusada |

**Banco novo ou restaurado.** A tabela de uso e a catraca de modos (§ 6) voltam ao estado do backup. O serviço
continua conferindo o `jti` na reserva e guarda, por consumidor, os domínios que já subiram para `externo`
(`dominios_externos`); na subida e a cada 60 s, se ele diz `externo` e a catraca local diz menos, o domínio fica
**suspenso** e o painel mostra a divergência. Se o diário do agente do worker se perder, a defesa local do uso único
volta a ser só o prazo de 120 s.

## 6. Modos por domínio

| Modo | Quem decide | O que o consumidor faz com o serviço |
|---|---|---|
| `local` | o controle local | nada sai ao serviço; é o estado de todos os domínios hoje |
| `sombra` | o controle local | o pedido vai só para comparar; a resposta nunca muda o que o consumidor faz; o efeito sai sem reserva nem consumo |
| `externo` | o serviço | fluxo completo dos §§ 2 a 5; sem resposta válida, nada avança |

A **catraca** só sobe (`local → sombra → externo`): não há rota que desça, e configuração ausente com catraca em
`externo` é suspenso, nunca `local`. Subir um domínio pede a equivalência medida na sombra e a aprovação explícita da pessoa responsável; até lá o
controle local do domínio fica. Em app controlado o pedido é **por gesto**, preso à observação do instante; autorização
por efeito ou por lista de ações não existe nesta versão.

**O que a sombra compara.** Cada ida grava só dado técnico: domínio, tipo, ferramenta, `efeito_possivel`, origem da
decisão, tipo de ida (`pedido`, `upload`, `reserva`, `consumo`), latência, bytes, estado devolvido e vínculos
(`etapa_ref`, `pedido_id`, `decisao_id`, `observacao_revisao`). O veredito local se divide em dois: o da **camada que
fica** (governança, tetos e janelas; continua na frente em `externo` e, se recusar, o pedido nem sai) e o dos
**controles que saem** (as regras que o serviço assume; se recusarem, o pedido vai assim mesmo e o par entra na
comparação, senão "zero autorizações onde o local recusou" seria zero por construção). O serviço grava por `pedido_id`
o veredito que as regras portadas dariam, inclusive após `dominio_nao_habilitado`; a junção é por esse id.

- **Ensaio.** Depois de um efeito que saiu pela autoridade local, `POST .../ensaio` com `{chave_de_idempotencia,
  desfecho}` informa o livro do serviço. Melhor esforço: a falha só vira linha da sombra. Não há reserva "de ensaio".
- **Pedido-sonda.** Onde o controle local para antes de qualquer gesto, a sombra manda um pedido com
  `dados_tecnicos.sonda=true`, com o gesto já calculado ou um hipotético fixo, nunca executado. Só compara, é
  deduplicado por `(ponto de parada, arvore_sha256)`, o livro do serviço o ignora e a resposta traz só os campos de
  "sempre". Sonda num domínio que o espelho da catraca diz `externo` é `schema_invalido`.
- **Critério de troca**, todos juntos: zero casos em que o serviço autoriza onde os controles que saem recusaram ou
  mandaram aguardar (sondas incluídas); casos normativos migrados passam; domínio habilitado; cada regra com ao menos
  um par real; mínimo proposto de 7 dias e 30 pares reais por domínio; discordância permissiva lida por pessoa;
  latência dentro do orçamento (pedido de `acao` 150 ms p95, reserva e consumo 20 ms; estimativas `not_run`); restrições
  iguais dos dois lados (§ 7).

## 7. Restrições

Parar, pausar, cancelar, hibernar e restrição pedida por pessoa são **sempre locais**, sem esperar o serviço. O
consumidor os registra em `POST /v0/restricoes` (melhor esforço, com fila local) com `{tipo_de_restricao, recursos,
origem, em}`. `tipo_de_restricao` é enum técnico fechado: `parar_execucao`, `cancelar_execucao`, `pausar_execucao`,
`marcar_aguardando`, `pausa_de_reparo_do_aparelho`, `quarentena_do_aparelho`, `parar_aparelho`, `hibernar_aparelho`,
`bloquear_perfil`, `pausar_perfil`, `retirar_conta`, `revogar_excecao`, `recusar_pendencia`, `credencial_em_revisao`.
`origem` é `pessoa`, `consumidor` (mecanismo técnico local, como a pausa de reparo) ou `servico` (estado aplicado depois
de uma resposta do serviço).

1. **Nenhuma resposta do serviço apaga uma restrição local**, nem o `autorizado` de outra operação.
2. **Sair de estado protetor pede autorização:** tipo `painel.sair_de_estado_protetor`, com o recurso restrito em
   `recursos`. Se o serviço não o conhece (o registro é melhor esforço), responde `aguardando` e uma pessoa decide no
   portal: é a escolha restritiva.
3. **Restrição local ativa recusa localmente**, antes de ir ao serviço, toda operação que avança sobre o recurso.
   Contam as linhas do registro e as colunas protetoras que já existem (perfil bloqueado, quarentena, credencial em
   revisão, pausa de reparo); os estados anteriores à integração são varridos para o registro, com
   `origem=consumidor`, e enviados ao serviço.
4. **Estado protetor do serviço.** Depois da troca de um domínio, o estado que uma regra portada poria vive no serviço,
   que responde `aguardando` ou `recusado` a tudo que avança sobre o recurso; com o serviço fora do ar nada avança e
   o estado não se perde. Em `local` e `sombra`, o controle local continua pondo a sua marca.

Previsto na próxima versão: `recusado` e `aguardando` ganham `restricoes`, lista de 0 a 4 `{tipo_de_restricao,
recurso}`, sempre vazia em `autorizado`; o consumidor a aplicaria nas colunas protetoras com `origem=servico`, e resposta
sem o campo ou com tipo fora do enum seria `schema_invalido`. O consumidor receberia o TIPO, nunca o motivo.

## 8. Erros e códigos

A resposta de erro é `{"protocolo":"0","erro":"<código>"}` e nada mais. O consumidor só grava ids, estados, códigos e
resumos (o adaptador não grava corpo nem cabeçalho de erro, nem dentro de exceção). **Em todos os casos o avanço
para**; nenhum código leva a "seguir mesmo assim".

| Código | Onde nasce | Comportamento do consumidor |
|---|---|---|
| `indisponivel`, `prazo_esgotado` | conexão recusada, 5xx, sem resposta no prazo do domínio | em `externo`, a operação pausa; nova tentativa do PEDIDO com recuo (1, 2, 4… até 60 s), nunca da ação; em `sombra`, registra e o local decide |
| `autenticacao_recusada` | `401` | para; alerta técnico à pessoa responsável; não repete a mesma requisição |
| `schema_invalido` | resposta fora do esquema, campo a mais, presença errada por estado, host fora da lista, `413` | para; evento de integridade; não repete |
| `versao_incompativel` | sem versão comum | suspende os domínios em `externo`; em `sombra`, só registra |
| `assinatura_invalida`, `chave_desconhecida` | envelope, item 1 | para; evento de integridade; descarta o envelope |
| `expirada`, `ainda_nao_valida`, `revogada` | envelope item 6, reserva, consumo | para; novo pedido com observação nova, no máximo 2 vezes por ação; depois a etapa fecha |
| `escopo_diferente` | envelope item 3 | para; evento de integridade (defeito ou adulteração); sem nova tentativa |
| `conteudo_diferente` | envelope item 4 | para; novo pedido só se o conteúdo mudou por ação legítima do consumidor; senão, integridade |
| `observacao_diferente` | envelope item 5, reserva | para; nova observação e novo pedido, no máximo 2 vezes; depois a etapa fecha |
| `consumidor_errado` | envelope item 2 | para; evento de integridade |
| `reuso` | envelope item 7, reserva, tabela local | para; nunca repete com o mesmo envelope |
| `relogio_divergente` | `agora` do serviço fora de ±30 s | suspende os domínios em `externo` até o relógio voltar |
| `dominio_nao_habilitado` | pedido a domínio ainda não habilitado | em `externo`, igual a `recusado`, sem nova tentativa; em `sombra`, registra e junta pelo `pedido_id` |

**Versão e worker.** O central consulta `GET /v0/versao` na subida e a cada 60 s. Campo opcional novo no pedido mantém
a versão; campo novo na resposta exige versão nova, porque o consumidor recusa campo desconhecido. O despacho ao worker
ganha o campo `autorizacao` na versão 2 do contrato do worker; com o domínio `worker` em `externo`, o central não
despacha verbo controlado a agente de versão menor (ele ignoraria o campo em silêncio). O agente confere o envelope
(itens 1, 2, 3, 6 e 7, com `aud` igual ao seu `worker_id`); recusa vira resultado `failed` com
`data.refused = "autorizacao:<codigo>"`, sem executar nada. Verbos que só restringem seguem pela versão anterior.

## 9. O que fica fora deste documento

- As regras de tema do serviço, o classificador dele, o portal de decisão e o livro dos efeitos consumidos: o consumidor
  só recebe o veredito.
- A política por ação, os tetos, as janelas por conta e a governança das aprovações, que ficam no consumidor.
- O código dos adaptadores, as migrações e os modos nas configurações (33.3 a 33.7).
- A comparação em sombra, as trocas por domínio e a validação final (33.8), cada troca com a aprovação explícita da pessoa responsável.
- Valores reais de chave, endereço e identificadores de instalação: ficam no `.env` e na configuração da instalação.
