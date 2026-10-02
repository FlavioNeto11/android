"""O que a retirada de uma conta bloqueada pede ao aparelho: limpar os dados do app (29.27, emenda do ADR-068).

Só os TIPOS do pedido, para o domínio social descrever o que quer sem conhecer aparelho, comando nem adb: quem executa
é o central (`commands/limpeza_ao_retirar.py`), ligado no `AppState` pelo gancho `SocialService.ao_limpar_aparelhos`.

O pedido é CAPTURADO antes da retirada (a retirada troca o @ do marcador por `[conta removida]` e apaga sessões e
vínculos, e com eles a única pista de onde a conta estava logada) e nunca carrega o @: só ids, o pacote e os aparelhos.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AparelhoDaLimpeza:
    """Um aparelho onde a conta estava logada. `marcadores` são os ids (`device_locked_accounts`) ABERTOS do @ da conta
    ali — o que a limpeza resolve no fim; vazio quando só o vínculo da conta apontava para o aparelho.
    `origens` diz por onde o aparelho foi achado (`marcador`, `vinculo`; a sessão não é pista, ver `aparelhos_da_conta`), para o evento e a conferência."""

    instance_id: str
    marcadores: tuple[int, ...] = ()
    origens: tuple[str, ...] = ()


@dataclass(frozen=True)
class PedidoDeLimpeza:
    """Limpar `package` (e só ele) nos `aparelhos`, depois de a conta `account_id` da persona `profile_id` ter saído."""

    profile_id: str
    account_id: str
    app_id: str
    package: str
    aparelhos: tuple[AparelhoDaLimpeza, ...]
