"""Agente do worker: o processo que roda NA máquina que hospeda os aparelhos.

Reaproveita `devices/adb.py`, `sdk.py`, `avd.py` e `emulator.py` **literalmente** — esses módulos só dependem de
`config` + stdlib/psutil, e as marcas de Windows estão isoladas em `sdk.py` (viram 0 no Linux). É o que torna a
paridade viável sem escrever uma segunda implementação do ciclo de vida do emulador.

O que o agente NÃO tem, de propósito: banco, provedor de IA, credencial de conta. O canal de entrada sensível
continua central; o worker nunca recebe senha de perfil.
"""

AGENT_VERSION = "0.1.0"
