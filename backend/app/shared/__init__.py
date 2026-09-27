"""Kernel compartilhado (design §3, §4): tipos transversais sem dependência interna.

Só stdlib e o próprio `app.shared` (regra D2/D5: `shared` é raiz do grafo de contextos, ao lado de `contracts`, e
todo domínio o enxerga). Nasce estrito como `app.modules`: zero `Any` em anotação, nenhum import tardio, fora de
ciclo. O que mora hoje em `util.py`, `security/redaction.py` e `version.py` vem para cá na fase K, com shim.
"""
