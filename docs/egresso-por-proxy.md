# Egresso do device pelo IP residencial da criação

**Migração:** 133 · **Contrato:** adendo v1.134 · **Data:** 09/10/2026

## Por que

O Instagram cria a conta pelo IP residencial (via igfarm). Se o device depois abre o app por **outro** IP,
dispara *checkpoint*. Fazer o device **sair pelo mesmo IP** da criação evita o checkpoint.

## Como funciona

### Perfil de proxy do egresso

Quando o igfarm registra uma conta com `proxy_url` e `ip_criacao`:

1. Um perfil de proxy `igfarm-{account_id}` é criado em `network_profiles`, com `egress_esperado` = `ip_criacao`
   (só se for IPv4 público).
2. O perfil é atribuído ao device vinculado à persona (política `exigida`).
3. O device passa a sair pelo proxy, que encadeia para o IP residencial.

### `params.egress_esperado`

O IP esperado fica em `params.egress_esperado` do perfil. A detecção que já existe (`saida_esperada`,
`saida_divergente`, `saida_confere`) compara o IP medido com o esperado:

- IP igual → `trafego_verificado`, `egress_matches = true`
- IP diferente → `parcial`, `egress_matches = false`

### `reaquecer`

A flag `contas_igfarm.reaquecer` é de **observabilidade**:

- `0` = o IP medido confere com o esperado
- `1` = o IP medido diverge (o proxy residencial pode ter mudado)

É atualizada automaticamente em cada `registrar_medicao()`. Não dispara ação automática nesta fase.

### IP residencial é best-effort

Proxies residenciais (ex. IPRoyal) usam IPs *sticky* que **podem mudar** entre sessões. O `reaquecer=1` é o sinal
de que isso aconteceu. A alternativa para IP fixo é um ISP estático.

O formato da senha do proxy IPRoyal inclui `_session-<id>_lifetime-7d`: a sessão dura 7 dias antes de trocar.

## Gatilho de vínculo

Se o vínculo persona↔device for criado **depois** do registro da conta, um gatilho (`ao_vincular_egresso`) cria e
atribui o perfil automaticamente. Sem ele, o perfil ficaria órfão. O gatilho é não-destrutivo: se o device já tem o
perfil com política que segura, não reatribui; e preserva `exigida_com_bloqueio` quando já configurado, sem rebaixar
para `exigida`.

## Limpeza

Quando a conta é retirada (`retirar_conta_bloqueada`), na ordem:

1. Desatribui o perfil do device (com confirmação de conta real)
2. Remove o perfil de `network_profiles`
3. Apaga o segredo de rastreio do cofre

## Arquivos

| Arquivo | O que faz |
|---|---|
| `backend/migrations/133_egresso_igfarm.sql` | colunas aditivas em `contas_igfarm` |
| `backend/app/devices/rede.py` | `_parse_proxy`, `criar_perfil_de_conta`, `reaquecer_da_conta`, hook em `registrar_medicao` |
| `backend/app/modules/identity/domain/ponte_igfarm.py` | `proxy_url` e `ip_criacao` em `ComandoDeRegistro` |
| `backend/app/modules/identity/infrastructure/ponte_igfarm.py` | `gravar_egresso`, `RedeSocial` adaptador |
| `backend/app/modules/identity/infrastructure/egresso.py` | gatilhos `vincular_egresso` e `limpar_egresso` (ligados em `bootstrap.py`) |
| `backend/app/modules/identity/application/ponte_igfarm.py` | fluxo de egresso no `registrar()` + auto-assign |
| `backend/app/modules/identity/presentation/schemas.py` | `ContaIgfarmBody` (+2 campos) |
| `backend/app/modules/identity/presentation/instagram.py` | passar os campos |
| `backend/app/social/service.py` | gatilho do vínculo + limpeza do egresso |
| `backend/tests/test_egresso_igfarm.py` | testes |
| `backend/tests/test_rede_saida_esperada_edicao.py` | `PUT /api/network/profiles/{id}` (31.291) |

## Trocar o IP esperado (31.291)

O `egress_esperado` do perfil é a prova de que o aparelho sai pelo IP da criação. Ele só muda por
`PUT /api/network/profiles/{id}` ({`egress_esperado`, `egress_esperado_ipv6`, `motivo`}), que valida o IP, grava e emite
`network.updated` `perfil_atualizado` com o valor de antes e o de depois. Para o perfil de uma conta do igfarm em uso, a
troca exige `motivo` (409 `egress_esperado_protegido` sem ele). Caso que motivou: no android-05 (10/10/2026) o esperado
foi trocado por fora do app para o IP medido, e a comparação deixou de provar algo. Trocar o esperado não é remédio para
proxy que rotaciona: o remédio é sessão fixa no proxy. Contrato: adendo v1.134.
