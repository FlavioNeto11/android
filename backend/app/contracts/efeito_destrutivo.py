"""31.325 (ADR-091): quais verbos de EFEITO são DESTRUTIVOS (perdem dado ou dinheiro, ou não se desfazem). Lista única: a exploração de
efeito a lê daqui para decidir a política padrão e o alcance da genérica `explorar_efeito`. O dono manda "executar sem pedir aprovação por
request, salvo ação destrutiva": o destrutivo mantém `approval_required` e NÃO herda a genérica; só uma escolha dele sobre o verbo
(`explorar_apagar`) ou sobre o pedido (`explorar_apagar_pasta`) o libera.

São os verbos canônicos da chave (`planning/exploracao._EFEITO_CANONICO`: excluir, deletar, remover, esvaziar e limpar já viram `apagar`;
pagar, assinar e doar viram `comprar`). Entrar, sair, desconectar e cadastrar não estão aqui: nunca exploram (`credencial_e_sessao`).
"""
from __future__ import annotations

VERBOS_DESTRUTIVOS: frozenset[str] = frozenset({"apagar", "comprar", "transferir", "encerrar", "desinstalar", "resetar"})


def e_verbo_destrutivo(verbo: str) -> bool:
    """`verbo`: o canônico da chave (`explorar_<verbo>_…`)."""
    return verbo in VERBOS_DESTRUTIVOS
