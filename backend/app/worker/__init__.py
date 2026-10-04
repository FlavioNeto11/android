"""Agente do worker: o processo que roda NA máquina que hospeda os aparelhos.

Reaproveita `devices/adb.py`, `sdk.py`, `avd.py` e `emulator.py` **literalmente** — esses módulos só dependem de
`config` + stdlib/psutil, e as marcas de Windows estão isoladas em `sdk.py` (viram 0 no Linux). É o que torna a
paridade viável sem escrever uma segunda implementação do ciclo de vida do emulador.

O que o agente NÃO tem, de propósito: banco, provedor de IA, credencial de conta. O canal de entrada sensível
continua central; o worker nunca recebe senha de perfil.
"""

from ..version import agent_version as _agent_version
from ..version import codigo_do_agente as _codigo_do_agente

#: `0.1.0+<sha7>`: a versão semântica MAIS o commit que este pacote carrega. Era a constante `"0.1.0"`, e com ela
#: o central não tinha como distinguir um agente de hoje de um de três semanas atrás — os dois diziam a mesma
#: coisa. Quem calcula é `app/version.py`, a partir do `BUILD_VERSION` gravado pelo instalador (a máquina do
#: worker não é um checkout) ou do `.git` da árvore. Ver `workers/registry.py`, que compara com a do central e
#: marca `agente defasado` na Infraestrutura.
AGENT_VERSION = _agent_version()

#: Impressão do código deste pacote (sem `BUILD_VERSION` nem cache): é ela, e não a versão, que o central compara
#: para dizer `agente defasado` — um commit só de docs muda a versão e não o código (item 29.59).
AGENT_CODE = _codigo_do_agente()
