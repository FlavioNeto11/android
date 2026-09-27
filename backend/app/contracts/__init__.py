"""Contratos: o que dois lados que NÃO compartilham processo precisam concordar (o fio, os nomes, as marcas).

Regra, cobrada por `tests/test_arquitetura.py`: daqui só se importa stdlib fora de infraestrutura, `pydantic` e o
próprio `app.contracts` — sem I/O, sem `psutil`, sem `websockets`, sem import tardio, sem `Any` em anotação. É o que
deixa o pacote ir inteiro para a máquina do worker (`backend/worker-manifest.txt`) sem arrastar o central.
"""
