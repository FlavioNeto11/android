"""Workers: as máquinas que hospedam aparelhos.

Existe porque o túnel SSH carrega **só ADB**. Tudo que o executor local faz além de ADB — criar AVD, ligar o
processo do emulador, `emu kill`, salvar snapshot, guardar RAM, ler o log do emulador — não tinha contraparte do
outro lado. Era a causa de fundo de "os comandos não são obedecidos nos remotos": não havia nada lá para obedecer.

O executor local é um worker como qualquer outro (`LocalWorker`), para não existirem duas implementações das
mesmas operações.
"""
