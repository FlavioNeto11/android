"""O que o `AppState` guarda do portal (29.77, ADR-075): o serviço do contato, o token da página e o laço de reenvio.

A dependência da Canais (28.32) é resolvida na hora de usar, nunca na importação: `state.avisos.avisar_contato_do_portal`
e o tipo `ContatoDoPortal` podem não existir nesta base, e sem eles o contato fica guardado como `pendente`. Por isso
o `importlib` (o ponto cego declarado de `tests/test_arquitetura.py`), e não um import tardio.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import re
import time
from collections.abc import Callable

from app.config import Config
from app.db import Database
from app.modules.portal.application.contato import Avisar, AvisarResumo, ServicoDeContato, TipoDoContato
from app.modules.portal.application.exclusao import ApagarNoCanal, ServicoDeExclusao
from app.modules.portal.application.protecao import emitir_token
from app.modules.portal.application.vigia import AvisarBorda, Vigia
from app.modules.portal.adapters.borda_http import BuscarPelaBorda
from app.modules.portal.infrastructure.contatos_sql import ContatosSql
from app.security.access import publicos_de
from app.util import now

log = logging.getLogger("poc.portal")

#: Uma volta do reenvio por minuto; a retenção de 180 dias a cada hora (é faxina, não prazo).
REENVIO_S = 60
RETENCAO_S = 3600
#: A 1ª volta do vigia da borda (29.97) espera a subida assentar: não disputa com o deploy nem com o reinício.
PRIMEIRA_VOLTA_DO_VIGIA_S = 300
#: Um nome, sem esquema, porta, caminho nem espaço: é o que vira `https://<nome>/` no pedido do vigia.
_NOME_PUBLICO = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")

MODULO_DA_CANAIS = "app.modules.avisos.domain.portal"


def _tipo_da_canais() -> TipoDoContato | None:
    try:
        modulo = importlib.import_module(MODULO_DA_CANAIS)
    except ImportError:
        return None
    tipo = getattr(modulo, "ContatoDoPortal", None)
    return tipo if callable(tipo) else None


class Portal:
    def __init__(self, cfg: Config, db: Database, avisos: object) -> None:
        self.cfg = cfg
        self.repo = ContatosSql(db)

        def avisar() -> Avisar | None:
            funcao = getattr(avisos, "avisar_contato_do_portal", None)
            return funcao if callable(funcao) else None

        def avisar_resumo() -> AvisarResumo | None:
            funcao = getattr(avisos, "avisar_resumo_do_portal", None)
            return funcao if callable(funcao) else None

        def avisar_borda() -> AvisarBorda | None:
            funcao = getattr(avisos, "avisar_borda_do_portal", None)        # 29.97, escrito pela Canais
            return funcao if callable(funcao) else None

        def apagar_no_canal() -> ApagarNoCanal | None:
            funcao = getattr(avisos, "apagar_avisos_do_portal", None)      # 28.34; ausente antes dele
            return funcao if callable(funcao) else None

        self.contatos = ServicoDeContato(self.repo, limites=lambda: cfg.file.portal.limites, avisar=avisar,
                                         tipo_do_contato=_tipo_da_canais, avisar_resumo=avisar_resumo)
        # A exclusão a pedido (29.83) vale com o contato ligado ou não: a promessa da página continua depois de desligar.
        self.exclusao = ServicoDeExclusao(self.repo, apagar_no_canal=apagar_no_canal,
                                          canal_presente=lambda: avisar() is not None,
                                          buscas_por_hora=lambda: cfg.file.portal.limites.buscas_por_operador_hora,
                                          buscas_no_total=lambda: cfg.file.portal.limites.buscas_total_hora)
        self.vigia = Vigia(BuscarPelaBorda(lambda: cfg.file.portal.vigia.prazo_s), host=self.nome_do_vigia,
                           site_ligado=lambda: cfg.file.portal.site_ligado and self.site_presente,
                           csp_do_painel=lambda: cfg.file.server.csp_do_painel,
                           voltas_sem_conferir=lambda: cfg.file.portal.vigia.voltas_sem_conferir,
                           intervalo_s=lambda: cfg.file.portal.vigia.intervalo_s, avisar=avisar_borda)
        for _codigo, mensagem, _dica in self.problemas(com_contagens=False):   # o banco ainda não migrou aqui
            log.warning("portal: %s", mensagem)

    def nome_do_vigia(self) -> str | None:
        """O 1º nome de `server.public_hosts`, na ordem do config (nunca o `Host` de um pedido), ou `None`: vigia
        desligado, sem nome público, ou nome que não é só um nome (porta, esquema, caminho)."""
        if not self.cfg.file.portal.vigia.ligado:
            return None
        nome = next((h.strip().lower() for h in self.cfg.file.server.public_hosts if h.strip()), "")
        return nome if _NOME_PUBLICO.fullmatch(nome) else None

    @property
    def site_presente(self) -> bool:
        return (self.cfg.root / "site" / "index.html").is_file()

    @property
    def contato_ligado(self) -> bool:
        """A bandeira E a página que emite o token: com `site_ligado` sem a pasta `site/`, a raiz segue no painel e a
        rota do contato ficaria aberta sem página; aí o contato vale como desligado (revisão do #333, item 5)."""
        portal = self.cfg.file.portal
        return bool(portal.contato_ligado and portal.site_ligado and self.site_presente)

    def problemas(self, *, com_contagens: bool = True) -> list[tuple[str, str, str]]:
        """Os problemas do portal para o `/api/health`: `(código, mensagem, dica)`, só com nomes de configuração e
        contagens, nunca dado do visitante."""
        achados: list[tuple[str, str, str]] = []
        portal = self.cfg.file.portal
        if portal.site_ligado and not self.site_presente:
            achados.append((
                "portal_site_sem_pasta",
                "portal.site_ligado está ligado e a pasta site/ (com o index.html) não existe nesta instalação: a raiz "
                "segue no painel e o contato do site está desligado.",
                "Confira o checkout (a pasta site/ vem no Git) e reinicie o central, ou desligue portal.site_ligado."))
        if (problema := self.problema_de_ip_da_borda()) is not None:
            achados.append((
                "portal_contato_sem_ip_da_borda", problema + ".",
                "Declare server.tls_behind_proxy e server.public_hosts no config.yaml e reinicie, ou desligue "
                "portal.contato_ligado. Procedimento em docs/operacao.md, \"Site institucional na raiz\"."))
        achados += self.vigia.problemas()                 # 29.97: só o estado da última volta, sem rede
        if not com_contagens:
            return achados
        try:
            esperando, falhos = self.repo.parados(now())
        except Exception:  # noqa: BLE001 - a saúde não cai por causa de uma contagem
            log.exception("portal: contagem dos contatos parados")
            esperando, falhos = 0, 0
        if esperando or falhos:
            partes = []
            if esperando:
                partes.append(f"{esperando} contato(s) do site esperando há mais de 1 h com o aviso desligado "
                              "(canal_desligado)")
            if falhos:
                partes.append(f"{falhos} contato(s) do site descartado(s) nas últimas 24 h depois de 10 falhas da entrega "
                              "(falhas_demais)")
            achados.append((
                "portal_contatos_parados", "; ".join(partes) + ".",
                "Com canal_desligado: ligue avisos (avisos.enabled, token e chat do Telegram) e reinicie; os contatos saem "
                "na ordem. Com falhas_demais: veja o log poc.portal e o da Canais (o conteúdo já foi apagado)."))
        return achados

    def problema_de_ip_da_borda(self) -> str | None:
        """Com o contato ligado, a taxa por cliente precisa do IP da borda (`security.access.cliente_de`). Sem
        `tls_behind_proxy` ou sem o nome público, todo visitante do túnel vira a MESMA chave e a taxa de
        `por_cliente_hora` passa a valer para a internet inteira (revisão do #333, M2). Só nomes de configuração."""
        if not self.contato_ligado:
            return None
        faltas = []
        if not self.cfg.file.server.tls_behind_proxy:
            faltas.append("server.tls_behind_proxy: true")
        if not publicos_de(self.cfg):
            faltas.append("o nome público em server.public_hosts")
        if not faltas:
            return None
        return ("portal.contato_ligado sem o IP da borda: falta " + " e ".join(faltas)
                + "; a taxa por cliente vira uma só para todos os visitantes")

    def token(self) -> str:
        """O token da página. Vazio com o contato desligado: a rota nem existe, e o banco não é tocado."""
        if not self.contato_ligado:
            return ""
        try:
            return emitir_token(self.contatos.sal(), time.time())
        except Exception:  # noqa: BLE001 - sem banco a página ainda abre; o envio é que vai falhar, com aviso
            log.exception("portal: não foi possível emitir o token da página")
            return ""

    async def laco(self, lider: Callable[[], int | None]) -> None:
        """Reenvio dos não entregues e retenção, só no líder da trava `avisos` (é ele quem manda as mensagens).

        Roda mesmo com o contato desligado: a promessa dos 180 dias da página vale para o que já foi guardado, e
        desligar o formulário não pode congelar a retenção. Desligado, só o reenvio é pulado."""
        ultima_retencao = float("-inf")
        hora_do_resumo = now().strftime("%Y-%m-%dT%H")      # a 1ª volta não resume uma hora pela metade
        while True:
            await asyncio.sleep(REENVIO_S)
            if lider() is None:
                continue
            try:
                if self.contato_ligado:
                    contagem = await asyncio.to_thread(self.contatos.reenviar, now())
                    if contagem:
                        log.info("portal: reenvio %s", contagem)
                    # Uma vez por hora UTC, na virada, com a hora que acabou de fechar: na chave da Canais
                    # (`portal-resumo:<hora>`) vale a PRIMEIRA chamada da hora, então chamar a cada minuto deixaria uma
                    # contagem pequena do começo da hora esconder um pico do fim dela.
                    agora = now()
                    if agora.strftime("%Y-%m-%dT%H") != hora_do_resumo:
                        hora_do_resumo = agora.strftime("%Y-%m-%dT%H")
                        await asyncio.to_thread(self.contatos.resumir, agora)
                if time.monotonic() - ultima_retencao >= RETENCAO_S:
                    apagados = await asyncio.to_thread(self.repo.apagar_vencidos, now())
                    ultima_retencao = time.monotonic()
                    if apagados:
                        log.info("portal: %s contato(s) apagado(s) pela retenção", apagados)
            except Exception:  # noqa: BLE001 - o laço nunca derruba o processo
                log.exception("portal: volta do reenvio")

    async def laco_da_borda(self, lider: Callable[[], int | None]) -> None:
        """O vigia da borda (29.97), uma volta por `portal.vigia.intervalo_s`, só no líder da trava `avisos` (é ele
        quem avisa). A 1ª volta espera `PRIMEIRA_VOLTA_DO_VIGIA_S`; a subida e a saúde nunca esperam a borda."""
        await asyncio.sleep(PRIMEIRA_VOLTA_DO_VIGIA_S)
        while True:
            if lider() is not None and self.nome_do_vigia() is not None:
                try:
                    await asyncio.to_thread(self.vigia.volta, now())
                except Exception:  # noqa: BLE001 - o laço nunca derruba o processo
                    log.exception("portal: volta do vigia da borda")
            await asyncio.sleep(self.cfg.file.portal.vigia.intervalo_s)
