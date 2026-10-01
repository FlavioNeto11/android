# Memória do notebook (worker-lan-01) e o W4 da rede — diagnóstico de 01/10/2026

Pedido do dono (via sessão de orquestração, 01/10): preparar o diagnóstico e a mudança recomendada para a memória do
notebook, que o K-067 apontou como causa do W4 (29.9). **Nenhuma configuração do Windows foi alterada.** Tudo abaixo foi
lido por SSH, só leitura, e comparado com o central.

## O que foi medido

| | Notebook `WIN-EDHUOCQJ6JJ` | Central `WIN-7S2UASNLFOP` |
|---|---|---|
| Acelerador do emulador | WHPX (Hyper-V presente) | WHPX (Hyper-V presente) |
| RAM livre | 32,6 GB (10:3xZ) e 36,1 GB (12:3xZ) de 63,7 GB | — |
| Arquivo de paginação | gerenciado pelo sistema; 30,4 GB usados de 37,1 GB, **pico no máximo alocado** | 9,3 GB usados de 38,9 GB (pico 18,0) |
| Paginação agora | `Pages/sec` = 1 (sem atividade), lista modificada 72 MB | — |
| Compressão de memória / combinação de páginas | desligadas | — |
| Emuladores no ar | 3, cada um com ~4,2 GB privados e **0,57–0,68 GB** de working set | 3, cada um com ~3,6–3,9 GB privados e **0,62–0,95 GB** de working set |
| Configuração dos AVDs (`worker.yaml`) | `ram_mb: 3072`, `cores: 2`, `-lowram`, snapshot | — |

## O que isso prova, e o que NÃO prova

- **Corrigido em relação ao K-067:** o working set pequeno do `qemu-system` **não prova paginação do convidado**. No
  central, onde os aparelhos funcionam bem, os emuladores mostram o mesmo retrato (menos de 1 GB residente com ~3,7 GB
  privados): com WHPX a memória do convidado não é contada no working set do processo do jeito que se esperava. A frase
  "o convidado estava paginado" foi uma inferência sem prova e foi retirada.
- **O que segue medido:** no notebook o arquivo de paginação chegou ao máximo alocado (pico 37 GB) e está com 30 GB
  usados; no central, 9 GB. Algo no notebook comprometeu muita memória em algum momento. Não se sabe o quê nem quando.
- **O que segue sem causa:** no W4 (30/09 22:37–22:53Z) o cliente VPN não subiu no android-09 e o adbd caiu. O servidor
  do central não viu par novo. A causa não está medida.

## Recomendação

1. **Não mudar o Windows do notebook agora.** Não há evidência de que o arquivo de paginação, a compressão ou a
   configuração de memória causem o defeito; mudar às cegas troca um problema conhecido por outro.
2. **Antes de repetir o W4, medir durante a repetição** (só leitura, no notebook, a cada 10 s enquanto o android-09
   sobe com a VPN): `Get-Counter '\Memory\Pages/sec','\Memory\Committed Bytes','\Memory\Commit Limit'`, o CPU dos
   `qemu-system` e `adb devices` no próprio notebook. Se a paginação disparar ou o compromisso chegar ao limite no
   instante em que o adbd cai, aí sim a mudança é de memória.
3. **Mitigação reversível — APLICADA em 01/10 ~12:38Z por decisão do dono (`max_slots` efetivo 3):** menos emuladores ao mesmo tempo no notebook (hoje o limite é 6
   vagas). Pelo painel (Infraestrutura › servidor › limites) ou pela API, `max_slots` 3. Desfazer: voltar para 6. Não
   mexe no Windows.
4. **Se a medição do item 2 apontar paginação**, a mudança mais simples e reversível é fixar o arquivo de paginação,
   que hoje é gerenciado pelo sistema e bateu no máximo alocado. Num PowerShell de administrador no notebook:
   - aplicar: `$cs = Get-CimInstance Win32_ComputerSystem; Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$false}; New-CimInstance -ClassName Win32_PageFileSetting -Property @{Name='C:\pagefile.sys'; InitialSize=[uint32]40960; MaximumSize=[uint32]65536}`
   - conferir: `Get-CimInstance Win32_PageFileSetting; Get-CimInstance Win32_PageFileUsage`
   - desfazer: `Get-CimInstance Win32_PageFileSetting | Remove-CimInstance; $cs = Get-CimInstance Win32_ComputerSystem; Set-CimInstance -InputObject $cs -Property @{AutomaticManagedPagefile=$true}`
   - vale depois de reiniciar o notebook.

## Risco para os aparelhos 09–15

Todos são de QA, sem conta. Reiniciar o notebook derruba os emuladores (o agente religa a tarefa no boot; os aparelhos
voltam pelo painel) e corta o túnel do worker por alguns minutos. A mitigação do item 3 só limita quantos sobem ao
mesmo tempo.
