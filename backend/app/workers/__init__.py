"""Workers: as máquinas que hospedam aparelhos.

Existe porque o túnel SSH carrega **só ADB**. Tudo que o executor local faz além de ADB — criar AVD, ligar o
processo do emulador, `emu kill`, salvar snapshot, guardar RAM, ler o log do emulador — não tinha contraparte do
outro lado. Era a causa de fundo de "os comandos não são obedecidos nos remotos": não havia nada lá para obedecer.

**O executor local é um worker como qualquer outro** (`local.LocalWorker`): o central se registra na tabela
`workers` com o `OWNER_ID`, os aparelhos desta máquina ganham `worker_id`, e o ciclo de vida sai por UM despacho
só — `Dispatch`/`Ack`/`Progress`/`Result`, com a mesma cerca e o mesmo prazo, tanto para o agente do notebook
quanto para este servidor. O que continua saindo direto daqui é o verbo de ADB puro (`devices.verbs.SO_ADB`),
igual para aparelho local e remoto, porque aquele caminho passa pelo túnel e está provado em campo.

O que o `LocalWorker` **não** é: uma cópia do `worker/executor.py`. Aquele é o executor do agente, na máquina
dele. Aqui o executor continua sendo o `DeviceManager`, que carrega o que só o central tem (monitor, Appium,
rodízio, `desired_state`, readoção). O contrato é que virou único — não o código de baixo nível dos dois lados.
"""
