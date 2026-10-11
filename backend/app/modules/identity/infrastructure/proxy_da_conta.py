"""O proxy sticky da conta PLANEJADA (31.337): o perfil `igfarm-<conta>` nasce no planejamento, antes do primeiro toque do cadastro no
app, e não no registro do igfarm. O igfarm só ENTREGA o `proxy_url`; o IP de criação é a primeira medição dentro da janela do
cadastro (`ConvergenciaDeRede.medir_para_o_cadastro`)."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.integrations.app_declarado.cadastro import ESTADOS_QUE_COMECAM
from app.modules.identity.application.ponte_igfarm import ErroDaPonte
from app.modules.identity.domain.ponte_igfarm import EgressoDoDevice
from app.modules.identity.infrastructure.ponte_igfarm import compor_ponte_igfarm
from app.social.provisionamento import estado_da_linha
from app.social.service import SocialError

if TYPE_CHECKING:
    from app.state import AppState


def planejar_proxy_da_conta(s: AppState, profile_id: str, account_id: str,
                            proxy_url: str) -> tuple[str, tuple[EgressoDoDevice, ...]]:
    """Cria (idempotente) o perfil de proxy da conta e o atribui aos aparelhos da persona. Só para conta que ainda vai se
    cadastrar: depois do cadastro o egresso é o da conta criada e o caminho é o registro. O `proxy_url` (com a senha do proxy)
    entra no cofre do perfil e nunca volta."""
    s.social.get_account(profile_id, account_id)                                   # 404 se não existe
    linha = s.social_repo.account_row(profile_id, account_id)
    assert linha is not None
    estado = estado_da_linha(linha)
    if estado not in ESTADOS_QUE_COMECAM:
        raise SocialError("estado_inesperado", f"A conta está em '{estado.value}': o proxy do cadastro só se planeja antes dele "
                          "(credencial_preparada, aguardando_cadastro_externo ou aguardando_verificacao).", 409,
                          {"estado_atual": estado.value})
    try:
        return compor_ponte_igfarm(s).planejar_proxy(profile_id, account_id, proxy_url)
    except ErroDaPonte as exc:
        raise SocialError(exc.code, exc.message, exc.status) from None
