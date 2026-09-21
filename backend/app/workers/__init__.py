"""Workers: as máquinas que hospedam aparelhos.

Existe porque o túnel SSH carrega **só ADB**. Tudo que o executor local faz além de ADB — criar AVD, ligar o
processo do emulador, `emu kill`, salvar snapshot, guardar RAM, ler o log do emulador — não tinha contraparte do
outro lado. Era a causa de fundo de "os comandos não são obedecidos nos remotos": não havia nada lá para obedecer.

**O executor local AINDA NÃO é um worker.** O plano previa um `LocalWorker` embrulhando o `DeviceManager` atrás do
mesmo contrato, para não existirem duas implementações das mesmas operações — e ele não foi feito. Hoje há dois
caminhos (`api._do_action` chama o `DeviceManager` direto; `_do_action_no_worker` fala o protocolo), com semântica
diferente: o `start` local responde `succeeded` antes de o aparelho ligar, o remoto espera o boot. Esta docstring
dizia o contrário, e a auditoria de 21/09 pegou (docs/plano-100.md, fase 2).
"""
