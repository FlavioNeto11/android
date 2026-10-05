"""As proteções do formulário de contato que não dependem de HTTP nem de banco (29.77, ADR-075).

**Token de tempo mínimo.** A página sai com `ts.assinatura` num campo oculto. Um robô que posta direto na rota, sem
abrir a página, não tem token; um que abre e posta no mesmo segundo chega cedo. Os dois recebem a MESMA resposta de um
contato aceito (o robô não aprende o que o barrou) e nada é gravado. Token expirado é o caso de uma pessoa de verdade
que deixou a aba aberta: esse recebe um aviso amigável para recarregar.

**Cliente pseudonimizado.** A taxa por cliente precisa de uma chave estável, mas o IP cru não entra no banco nem no
log: guarda-se um HMAC com o sal da instalação. IPv6 conta pelo /64, que é o que um provedor entrega a uma casa; sem
isso, uma única conexão trocaria de endereço a cada pedido e nunca bateria na taxa.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
from typing import Literal

VereditoDoToken = Literal["ok", "cedo", "expirado", "invalido"]


def _assinar(sal: bytes, texto: str) -> str:
    return hmac.new(sal, b"portal-token:" + texto.encode("ascii"), hashlib.sha256).hexdigest()[:32]


def emitir_token(sal: bytes, agora: float) -> str:
    ts = str(int(agora))
    return f"{ts}.{_assinar(sal, ts)}"


def conferir_token(sal: bytes, token: str, agora: float, *, minimo_s: int, maximo_s: int) -> VereditoDoToken:
    ts, _, assinatura = (token or "").partition(".")
    if not ts.isdigit() or len(ts) > 12 or not hmac.compare_digest(assinatura.encode("ascii", "replace"),
                                                                     _assinar(sal, ts).encode("ascii")):
        return "invalido"
    idade = agora - int(ts)
    if idade < minimo_s:
        return "cedo"          # inclui o token "do futuro": só sai assinado daqui, então é relógio adulterado
    if idade > maximo_s:
        return "expirado"
    return "ok"


def cliente_pseudonimo(sal: bytes, cliente: str) -> str:
    """O HMAC da chave de `cliente_de` (`ip:<endereço>`, `tunel`, `local`). Endereço IPv6 vira o /64 antes."""
    chave = cliente
    if cliente.startswith("ip:"):
        try:
            endereco = ipaddress.ip_address(cliente[3:])
        except ValueError:
            pass
        else:
            if endereco.version == 6 and endereco.ipv4_mapped is None:
                chave = "ip6:" + str(ipaddress.ip_network(f"{endereco}/64", strict=False).network_address)
            elif endereco.version == 6 and endereco.ipv4_mapped is not None:
                chave = f"ip:{endereco.ipv4_mapped}"
    return hmac.new(sal, b"portal-cliente:" + chave.encode("utf-8"), hashlib.sha256).hexdigest()[:32]
