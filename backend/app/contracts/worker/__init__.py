"""O contrato entre o central e o agente do worker: `protocol` (as mensagens do fio) e `verbos` (o vocabulário).

Os dois lados importam daqui. O agente (`app/worker/**`) importa SÓ daqui; o central ainda tem os nomes antigos
(`app.workers.protocol`, `app.devices.verbs`), que reexportam os mesmos objetos.
"""
