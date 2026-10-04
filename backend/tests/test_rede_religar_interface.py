"""W8 (01/10/2026): religar o túnel do cliente VPN pelo Start da INTERFACE, não pelo tile. Prova `simulated` (aparelho falso).

Fonte de verdade, SFA 1.14.2 (versionCode 739), commit upstream fc21909df7a3f0fc9435f3866fb6a4960711aa5f:

  TileService.onClick → toggleService → BoxService.start() → Settings.serviceClass()   (NÃO chama rebuildServiceMode)
  Settings.serviceClass(): serviceMode == VPN → VPNService; qualquer outro valor → ProxyService (o padrão é NORMAL)
  MainActivity.startService() → startService0(): rebuildServiceMode() → Libbox.hasTunInbound(perfil selecionado)
                                                  → serviceMode → serviceClass() → startForegroundService
  ProfileManager.create(andSelect = true) (a importação): seleciona o perfil e NÃO recalcula o serviceMode
  ProxyService + perfil com `tun` → openTun lança "android: tun inbound requires VPN service" → o serviço se encerra

Prova real (não repetida aqui): android-09, 01/10 18:23Z, UM Start da UI → VPNService, tun0 em < 1 s, VPN CONNECTED; o tile no 09
iniciava o ProxyService (A1 e segunda A1); no android-05 o tile subia o VPNService porque o serviceMode já era VPN.

O que estes testes protegem: o stale do 09 (tile → ProxyService → sem tun0; Start da UI → rebuild → VPNService), nenhum toque
arbitrário, falha fechada, o guard D (ProxyService num plano com TUN NÃO é recuperação), sem fallback para o tile, foco devolvido.
"""
from __future__ import annotations

import pytest

from app.devices.rede_aplicacao import (CLASSE_AMBAS, CLASSE_DESCONHECIDA, CLASSE_NENHUMA, CLASSE_PROXY, CLASSE_VPN,
                                        ROTULOS_DO_CLIENTE, classe_na_janela, idioma_do_recurso, locale_do_aparelho,
                                        rotulos_para, CLASSE_ERRADA_PARA_TUN, FOCO_NAO_E_O_CLIENTE, INTERFACE_MOSTRA_STOP, JA_HA_TUN,
                                        ABERTURA_FALHOU, RELIGADO, START_NAO_PROVADO, TUN_NAO_SUBIU, VPN_NAO_CONECTADA,
                                        SFA_COMMIT_DE_REFERENCIA, AparelhoPeloAdb, NoDaTela, RedeAplicacaoError,
                                        achar_botao, religar_pela_interface, religar_pelo_tile)

from .test_rede_aplicacao import PKG, AparelhoFalso

ATIVIDADE = "io.nekohasekai.sfa/.compose.MainActivity"
TILE = "io.nekohasekai.sfa/.bg.TileService"
RAPIDO = {"espera_s": 0, "prazo_do_botao_s": 0.05, "pausa_s": 0.01}


def _aparelho(**kw) -> AparelhoFalso:
    return AparelhoFalso(primeira_execucao=False, **kw)


def _escreveu_config(ap: AparelhoFalso) -> bool:
    return any(c.startswith(("settings put", "settings delete", "am force-stop", "cmd appops")) for c in ap.comandos)


def test_a_fonte_de_verdade_upstream_esta_registrada() -> None:
    assert SFA_COMMIT_DE_REFERENCIA == "fc21909df7a3f0fc9435f3866fb6a4960711aa5f"


# ---------------------------------------------------------------------------------------------------- CASO 1 e 2
async def test_caso1_stale_do_09_o_tile_inicia_o_proxyservice_e_o_start_da_ui_religa_sem_restart() -> None:
    ap = _aparelho(modo_vpn=False, ui_religa=True, tile_religa=True, lockdown="0")
    # o que o 09 fazia: tile → ProxyService → sem tun0 (reproduz o F6_TUN_NOT_CREATED_AFTER_TILE)
    obs_tile, _ = await religar_pelo_tile(ap, PKG, TILE, espera_s=0)
    assert obs_tile is None and ap.fp == 1 and ap.fv == 0 and not ap.tun
    # o caminho novo: Start da UI → rebuild → VPNService → tun0 + CONNECTED
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and r.codigo == RELIGADO and r.obs.tun and r.obs.vpn_conectada
    assert ap.modo_vpn and ap.fv == 1 and ap.fp == 1                                  # o ProxyService foi só o do tile
    assert ap.toques_ui == [(632, 984)] and ap.starts_na_ui == 1                      # UM toque, no centro do rótulo
    assert not _escreveu_config(ap) and ap.foco == "launcher" and ap.homes == 1       # sem restart, foco devolvido


async def test_caso2_modo_ja_vpn_o_start_da_ui_sobe_o_vpnservice() -> None:
    ap = _aparelho(modo_vpn=True, ui_religa=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and ap.fv == 1 and ap.fp == 0 and "sem reinício" in r.detalhe
    assert "classe do serviço não pôde ser observada" not in r.detalhe


# ---------------------------------------------------------------------------------------------------- CASO 3
async def test_caso3_com_tun0_no_ar_nao_abre_o_app_nem_toca() -> None:
    ap = _aparelho(ui_religa=True, tun=True, vpn=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == JA_HA_TUN and not r.religado
    assert ap.starts_na_ui == 0 and ap.toques_ui == [] and not ap.app_aberto and ap.tun and ap.vpn   # a VPN não foi derrubada
    assert not any(c.startswith("am start") for c in ap.comandos)


# ---------------------------------------------------------------------------------------------------- CASO 4 e 5
@pytest.mark.parametrize("tela", ["nada", "start_ambiguo", "start_sem_conteiner", "start_desabilitado"])
async def test_caso4_start_nao_provado_falha_fechada_sem_nenhum_toque(tela: str) -> None:
    ap = _aparelho(ui_religa=True, tela_ui=tela)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == START_NAO_PROVADO and not r.religado and "nenhum toque" in r.detalhe
    assert ap.toques_ui == [] and ap.starts_na_ui == 0 and not ap.tun
    assert ap.homes == 1 and ap.foco == "launcher"                                    # abriu, então devolve o foco


async def test_caso5_start_de_outro_pacote_nao_e_tocado() -> None:
    ap = _aparelho(ui_religa=True, tela_ui="outro_pacote")
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == START_NAO_PROVADO and "em outro pacote: não é tocado" in r.detalhe
    assert ap.toques_ui == [] and not ap.tun


async def test_interface_que_mostra_stop_sem_tun0_nao_alterna_as_cegas() -> None:
    ap = _aparelho(ui_religa=True, tela_ui="stop")
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == INTERFACE_MOSTRA_STOP and ap.toques_ui == []


async def test_foco_fora_do_cliente_nao_toca() -> None:
    class ForaDoFoco(AparelhoFalso):
        async def shell(self, comando: str, *, timeout: float = 40) -> str:
            saida = await super().shell(comando, timeout=timeout)
            if comando.startswith("am start -n "):
                self.foco = "com.android.permissioncontroller/.GrantPermissionsActivity"   # um diálogo tomou a frente
            return saida

    ap = ForaDoFoco(primeira_execucao=False, ui_religa=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == FOCO_NAO_E_O_CLIENTE and ap.toques_ui == [] and ap.homes == 0   # o foco não era nosso: HOME não é enviado


# ---------------------------------------------------------------------------------------------------- CASO 6 e 7 (guard D)
async def test_caso6_start_que_inicia_o_proxyservice_nao_e_recuperacao() -> None:
    ap = _aparelho(modo_vpn=False, ui_inicia_proxy=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == CLASSE_ERRADA_PARA_TUN and r.obs is None and not r.religado
    assert "ProxyService" in r.detalhe and "NÃO concluída" in r.detalhe
    assert ap.starts_na_ui == 1 and ap.fp == 1 and ap.fv == 0 and not ap.tun and ap.cliques_no_tile == 0


async def test_guard_d_vale_mesmo_com_tun0_e_connected_no_ar_por_outro_caminho() -> None:
    """O ProxyService iniciado por ESTE Start não é recuperação, ainda que um tun0 apareça (o always-on em paralelo)."""
    class TunPorOutroCaminho(AparelhoFalso):
        def _start_da_ui(self) -> None:
            super()._start_da_ui()
            self.tun = self.vpn = True

    ap = TunPorOutroCaminho(primeira_execucao=False, modo_vpn=False, ui_inicia_proxy=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == CLASSE_ERRADA_PARA_TUN and not r.religado and r.obs is None and ap.fp == 1 and ap.fv == 0


async def test_caso7_tun0_sem_vpn_connected_nao_e_sucesso() -> None:
    ap = _aparelho(ui_tun_sem_vpn=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == VPN_NAO_CONECTADA and not r.religado and r.obs is None
    assert ap.tun and not ap.vpn


async def test_start_que_nao_faz_nada_e_timeout_generico_com_os_servicos_no_motivo() -> None:
    ap = _aparelho()                                                                  # nem ProxyService nem VPNService
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == TUN_NAO_SUBIU and not r.religado and ap.starts_na_ui == 1 and ap.toques_ui == [(632, 984)]


# ---------------------------------------------------------------------------------------------------- CASO 8
async def test_caso8_app_que_nao_abre_nao_cai_no_tile() -> None:
    ap = _aparelho(abre_app_falha=True, tile_religa=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == ABERTURA_FALHOU and not r.religado
    assert ap.cliques_no_tile == 0 and ap.toques_ui == [] and not any("statusbar" in c for c in ap.comandos)
    assert ap.homes == 0                                                              # nada foi aberto: nada a devolver


async def test_excecao_na_abertura_vira_codigo_sem_tile() -> None:
    class Cai(AparelhoFalso):
        async def shell(self, comando: str, *, timeout: float = 40) -> str:
            if comando.startswith("am start -n "):
                raise RuntimeError("adb shell falhou (255)")
            return await super().shell(comando, timeout=timeout)

    ap = Cai(primeira_execucao=False, tile_religa=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == ABERTURA_FALHOU and ap.cliques_no_tile == 0


# ---------------------------------------------------------------------------------------------------- CASO 9 (worker remoto)
async def test_caso9_aparelho_de_worker_remoto_tem_a_mesma_semantica() -> None:
    remoto = AparelhoFalso(id="android-02", serial="emulator-5642", external=True, primeira_execucao=False,
                           modo_vpn=False, ui_religa=True)
    r = await religar_pela_interface(remoto, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and remoto.toques_ui == [(632, 984)] and remoto.homes == 1
    sem_botao = AparelhoFalso(id="android-02", external=True, primeira_execucao=False, tela_ui="nada")
    assert (await religar_pela_interface(sem_botao, PKG, ATIVIDADE, **RAPIDO)).codigo == START_NAO_PROVADO


async def test_caso9_a_porta_do_aparelho_real_entrega_a_arvore_completa_e_o_toque() -> None:
    """`AparelhoPeloAdb.arvore` (local e remoto usam o mesmo): inclui os nós SEM texto (o contêiner clicável do Compose)."""
    from types import SimpleNamespace as NS

    def el(texto: str, pacote: str, b: tuple[int, int, int, int], clicavel: bool):
        return NS(text=texto, desc="", package=pacote, bounds=b, clickable=clicavel, enabled=True)

    elementos = [el("", PKG, (576, 928, 688, 1040), True), el("Start", PKG, (608, 960, 656, 1008), False)]

    class Dispositivos:
        async def hierarchy(self, rt):
            return NS(elements=elementos)

    for externo in (False, True):
        rt = NS(id="android-02", serial="s", external=externo)
        ap = AparelhoPeloAdb(NS(devices=Dispositivos()), rt)
        nos = await ap.arvore()
        assert len(nos) == 2 and nos[0].texto == "" and nos[0].clicavel and nos[0].limites == (576, 928, 688, 1040)
        botao, _ = achar_botao(nos, PKG, "Start")
        assert botao is not None and botao.centro == (632, 984)


# ---------------------------------------------------------------------------------------------------- CASO 10 (foco, nada aberto)
async def test_caso10_nao_deixa_foco_comando_nem_estado_artificial_aberto() -> None:
    for kw in ({"ui_religa": True}, {"ui_inicia_proxy": True}, {"tela_ui": "nada"}, {"ui_tun_sem_vpn": True}):
        ap = _aparelho(**kw)
        await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
        assert ap.foco == "launcher" and ap.homes == 1, kw
        assert not _escreveu_config(ap) and not any("statusbar" in c for c in ap.comandos), kw
        assert len(ap.toques_ui) <= 1, kw                                              # nunca mais de UM toque


async def test_entradas_invalidas_sao_recusadas_sem_tocar_no_aparelho() -> None:
    ap = _aparelho(ui_religa=True)
    for pacote, atividade in (("io.nekohasekai.sfa; reboot", ATIVIDADE), (PKG, "pkg/.Main; reboot"), (PKG, ""), (PKG, "semclasse")):
        with pytest.raises(RedeAplicacaoError):
            await religar_pela_interface(ap, pacote, atividade, **RAPIDO)
    assert ap.comandos == [] and ap.toques_ui == []


# ---------------------------------------------------------------------------------------------------- o localizador
def _no(texto: str, b: tuple[int, int, int, int], clicavel: bool = False, pacote: str = PKG, habilitado: bool = True) -> NoDaTela:
    return NoDaTela(texto, pacote, b, clicavel, habilitado)


def test_localizador_prova_o_conteiner_clicavel_do_rotulo_nao_clicavel() -> None:
    real = [_no("", (576, 928, 688, 1040), True), _no("Start", (608, 960, 656, 1008))]      # a árvore real do android-09
    botao, _ = achar_botao(real, PKG, "start")
    assert botao is not None and botao.centro == (632, 984)
    # um clicável que NÃO contém o rótulo não conta; um que cobre a tela inteira também não
    assert achar_botao([_no("", (0, 0, 5, 5), True), _no("Start", (608, 960, 656, 1008))], PKG, "Start")[0] is None
    assert achar_botao([_no("", (0, 0, 720, 1280), True), _no("Start", (608, 960, 656, 1008))], PKG, "Start")[0] is None
    # o menor contêiner vence
    dois = [_no("", (0, 800, 720, 1100), True), _no("", (576, 928, 688, 1040), True), _no("Start", (608, 960, 656, 1008))]
    assert achar_botao(dois, PKG, "Start")[0] is not None


def test_localizador_recusa_outro_pacote_repetido_e_desabilitado() -> None:
    assert achar_botao([_no("", (576, 928, 688, 1040), True, "x.y"), _no("Start", (608, 960, 656, 1008), pacote="x.y")],
                       PKG, "Start")[0] is None
    assert achar_botao([_no("", (576, 928, 688, 1040), True), _no("Start", (608, 960, 656, 1008)),
                        _no("Start", (10, 960, 60, 1008))], PKG, "Start")[0] is None
    assert achar_botao([_no("", (576, 928, 688, 1040), True), _no("Start", (608, 960, 656, 1008), habilitado=False)],
                       PKG, "Start")[0] is None
    assert achar_botao([_no("", (576, 928, 688, 1040), True, habilitado=False), _no("Start", (608, 960, 656, 1008))],
                       PKG, "Start")[0] is None
    assert achar_botao([], PKG, "Start")[0] is None


# ====================================================================================================== locale (W8, hardening)
# Os rótulos vêm do SFA 1.14.2 (commit fc21909): R.string.action_start e R.string.stop em
# app/src/main/res/values/strings.xml (padrão, inglês), values-fa, values-ru-rRU, values-zh-rCN, values-zh-rTW.
def test_a_tabela_de_rotulos_e_a_do_sfa_1_14_2() -> None:
    assert ROTULOS_DO_CLIENTE == {"en": ("Start", "Stop"), "fa": ("شروع", "توقف"), "ru": ("Начать", "Остановить"),
                                  "zh-CN": ("启动", "停止"), "zh-TW": ("啟動", "停止")}


@pytest.mark.parametrize("locale,idioma", [
    ("en-US", "en"), ("pt-BR", "en"), ("de-DE", "en"), ("ru-RU", "ru"), ("ru", "ru"), ("fa-IR", "fa"), ("fa", "fa"),
    ("zh-CN", "zh-CN"), ("zh_CN", "zh-CN"), ("zh", "zh-CN"), ("zh-Hans-CN", "zh-CN"), ("zh-SG", "zh-CN"),
    ("zh-TW", "zh-TW"), ("zh-HK", "zh-TW"), ("zh-Hant", "zh-TW"), ("zh-Hant-TW", "zh-TW"), ("", None), ("null", None)])
def test_o_idioma_do_recurso_segue_a_resolucao_do_android(locale: str, idioma: str | None) -> None:
    assert idioma_do_recurso(locale) == idioma


def test_locale_do_aparelho_usa_a_primeira_fonte_legivel() -> None:
    assert locale_do_aparelho({"L1": "ru-RU", "L2": "en-US", "L3": "pt-BR"}) == "ru-RU"
    assert locale_do_aparelho({"L1": "", "L2": "null", "L3": "en-US"}) == "en-US"
    assert locale_do_aparelho({"L1": "", "L2": "zh-TW", "L3": "en-US"}) == "zh-TW"
    assert locale_do_aparelho({}) == ""


def test_locale_lido_aceita_so_o_idioma_dele_e_ilegivel_aceita_a_uniao() -> None:
    assert rotulos_para("zh-TW") == (("啟動",), ("停止",), "zh-TW")
    inicio, parada, idioma = rotulos_para("")
    assert idioma is None and set(inicio) == {"Start", "شروع", "Начать", "启动", "啟動"} and "Stop" in parada


@pytest.mark.parametrize("locale,rotulo_start,rotulo_stop", [
    ("en-US", "Start", "Stop"),                          # inglês
    ("pt-BR", "Start", "Stop"),                          # sem recurso traduzido: o Android usa o padrão
    ("zh-TW", "啟動", "停止"),                            # traduzido pelo SFA 1.14.2 (values-zh-rTW)
    ("zh-CN", "启动", "停止"),
    ("ru-RU", "Начать", "Остановить"),
    ("fa-IR", "شروع", "توقف"),
])
async def test_locale_o_rotulo_do_idioma_do_aparelho_e_tocado_uma_vez(locale: str, rotulo_start: str, rotulo_stop: str) -> None:
    ap = _aparelho(locale=locale, rotulo_start=rotulo_start, rotulo_stop=rotulo_stop, ui_religa=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and r.rotulo == rotulo_start and r.locale == locale and ap.toques_ui == [(632, 984)]
    assert "action_start" in r.metodo
    # sem tun0, a interface mostra o Stop do MESMO idioma: reconhece e não alterna
    parado = _aparelho(locale=locale, rotulo_start=rotulo_start, rotulo_stop=rotulo_stop, tela_ui="stop", ui_religa=True)
    r = await religar_pela_interface(parado, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == INTERFACE_MOSTRA_STOP and parado.toques_ui == []


async def test_locale_rotulo_de_outro_idioma_nao_e_tocado_quando_o_locale_e_conhecido() -> None:
    ap = _aparelho(locale="zh-CN", rotulo_start="啟動", ui_religa=True)                # o rótulo é o do zh-TW
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == START_NAO_PROVADO and ap.toques_ui == []


async def test_locale_ilegivel_aceita_um_rotulo_da_tabela_e_continua_fail_closed() -> None:
    ap = _aparelho(locale="", rotulo_start="شروع", ui_religa=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and ap.toques_ui == [(632, 984)]
    desconhecido = _aparelho(locale="", rotulo_start="Iniciar", ui_religa=True)
    r = await religar_pela_interface(desconhecido, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == START_NAO_PROVADO and desconhecido.toques_ui == []


@pytest.mark.parametrize("locale", ["en-US", "zh-TW", "ru-RU", ""])
async def test_locale_label_desconhecido_nenhum_toque(locale: str) -> None:
    ap = _aparelho(locale=locale, rotulo_start="Iniciar", ui_religa=True)               # o português NÃO está no SFA 1.14.2
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == START_NAO_PROVADO and ap.toques_ui == [] and ap.starts_na_ui == 0 and not ap.tun


@pytest.mark.parametrize("locale,rotulo", [("en-US", "Start"), ("zh-TW", "啟動"), ("ru-RU", "Начать")])
async def test_locale_elemento_semelhante_de_outro_pacote_nenhum_toque(locale: str, rotulo: str) -> None:
    ap = _aparelho(locale=locale, rotulo_start=rotulo, ui_religa=True, tela_ui="outro_pacote")
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == START_NAO_PROVADO and ap.toques_ui == []


def test_localizador_aceita_varios_rotulos_mas_exige_um_so_no_pacote() -> None:
    cont = NoDaTela("", PKG, (576, 928, 688, 1040), True)
    assert achar_botao([cont, NoDaTela("啟動", PKG, (608, 960, 656, 1008), False)], PKG, ("Start", "啟動"))[0] is not None
    dois = [cont, NoDaTela("Start", PKG, (608, 960, 656, 1008), False), NoDaTela("啟動", PKG, (640, 960, 650, 1008), False)]
    assert achar_botao(dois, PKG, ("Start", "啟動"))[0] is None                            # dois rótulos distintos: ambíguo


# ====================================================================================================== guard: a JANELA do Start
BASE = "10-01 15:00:10.000"


def _janela(**kv: str) -> dict[str, str]:
    return {"OLD": "10-01 14:00:00.000", "EVP": "0", "EVV": "0", **kv}


def test_classe_na_janela_so_conta_eventos_da_janela() -> None:
    assert classe_na_janela(BASE, _janela(EVV="1"))[0] == CLASSE_VPN
    assert classe_na_janela(BASE, _janela(EVP="1"))[0] == CLASSE_PROXY
    assert classe_na_janela(BASE, _janela(EVP="1", EVV="2"))[0] == CLASSE_AMBAS
    assert classe_na_janela(BASE, _janela())[0] == CLASSE_NENHUMA


@pytest.mark.parametrize("base,janela", [
    ("", _janela(EVP="1")),                                            # baseline ilegível
    ("ontem", _janela(EVP="1")),
    (BASE, _janela(OLD="", EVP="1")),                                  # hora do buffer ilegível
    (BASE, _janela(OLD="10-01 15:30:00.000", EVP="1")),                # o buffer girou: o mais antigo é posterior ao baseline
    (BASE, {"OLD": "10-01 14:00:00.000", "EVP": "", "EVV": ""}),       # as contagens não vieram
    (BASE, {"OLD": "10-01 14:00:00.000", "EVP": "x", "EVV": "1"}),
])
def test_classe_na_janela_sem_prova_e_unknown_nunca_uma_conclusao(base: str, janela: dict[str, str]) -> None:
    classe, porque = classe_na_janela(base, janela)
    assert classe == CLASSE_DESCONHECIDA and porque


async def test_eventos_historicos_do_buffer_nao_contam_na_janela_deste_start() -> None:
    ap = _aparelho(ui_religa=True)
    ap.semear("ProxyService", 3)                                       # o tile do 09 de ontem: o contador cumulativo os veria
    ap.semear("VPNService", 1)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and r.classe == CLASSE_VPN and ap.fp == 3 and ap.fv == 2


async def test_historico_de_proxyservice_sem_evento_novo_nao_vira_guard() -> None:
    ap = _aparelho()                                                   # o Start não inicia nada
    ap.semear("ProxyService", 5)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == TUN_NAO_SUBIU and r.classe == CLASSE_NENHUMA


async def test_buffer_rotacionado_a_classe_e_unknown_e_nada_e_inventado_sobre_o_proxyservice() -> None:
    ap = _aparelho(ui_religa=True, buffer_girou=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and r.classe == CLASSE_DESCONHECIDA and "ProxyService" not in r.detalhe
    proxy = _aparelho(ui_inicia_proxy=True, buffer_girou=True)                          # não se prova: sem guard, sem sucesso
    r = await religar_pela_interface(proxy, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == TUN_NAO_SUBIU and r.classe == CLASSE_DESCONHECIDA and not r.religado


async def test_proxyservice_novo_na_janela_e_o_guard_e_vpnservice_novo_e_sucesso() -> None:
    ap = _aparelho(ui_inicia_proxy=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == CLASSE_ERRADA_PARA_TUN and r.classe == CLASSE_PROXY
    ok = _aparelho(ui_religa=True)
    r = await religar_pela_interface(ok, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and r.classe == CLASSE_VPN and r.tun_apos_s is not None


async def test_ambos_por_corrida_nao_e_proxyservice_sem_vpnservice() -> None:
    com_tun = _aparelho(ui_corrida=True, ui_religa=True)
    r = await religar_pela_interface(com_tun, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and r.classe == CLASSE_AMBAS
    sem_tun = _aparelho(ui_corrida=True)
    r = await religar_pela_interface(sem_tun, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == TUN_NAO_SUBIU and r.classe == CLASSE_AMBAS


async def test_classe_nao_observavel_com_tun_e_connected_nao_afirma_nada_sobre_o_proxyservice() -> None:
    ap = _aparelho(ui_religa=True, janela_sem_leitura=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and r.classe == CLASSE_DESCONHECIDA and "ProxyService" not in r.detalhe
    proxy = _aparelho(ui_inicia_proxy=True, janela_sem_leitura=True)
    r = await religar_pela_interface(proxy, PKG, ATIVIDADE, **RAPIDO)
    assert r.codigo == TUN_NAO_SUBIU and r.classe == CLASSE_DESCONHECIDA


async def test_comando_da_janela_que_falha_vira_unknown() -> None:
    class JanelaCai(AparelhoFalso):
        async def shell(self, comando: str, *, timeout: float = 40) -> str:
            if "OLD=" in comando and "-T '" in comando:
                raise RuntimeError("adb shell falhou (1)")
            return await super().shell(comando, timeout=timeout)

    ap = JanelaCai(primeira_execucao=False, ui_religa=True)
    r = await religar_pela_interface(ap, PKG, ATIVIDADE, **RAPIDO)
    assert r.religado and r.classe == CLASSE_DESCONHECIDA


def test_os_comandos_do_aparelho_usam_o_baseline_do_aparelho_e_recusam_entrada_ruim() -> None:
    from app.devices.rede_aplicacao import comando_da_janela_do_start, comando_do_estado_da_interface

    estado = comando_do_estado_da_interface(PKG)
    assert "BASE=$(date '+%m-%d %H:%M:%S.%N'" in estado and "persist.sys.locale" in estado and "system_locales" in estado
    janela = comando_da_janela_do_start(PKG, BASE)
    assert f"-T '{BASE}'" in janela and "logcat -b events -d -v time" in janela and "OLD=" in janela
    for ruim in ("", "ontem", "10-01 15:00:10", "10-01 15:00:10.000'; reboot; '"):
        with pytest.raises(RedeAplicacaoError):
            comando_da_janela_do_start(PKG, ruim)


async def test_leitura_da_rede_usa_o_prazo_da_config_e_a_folga_da_fila() -> None:
    """29.75: o prazo das leituras da sonda era 45 s fixos (+10 da fila = "rede do aparelho excedeu 55s", 6 vezes em 7
    dias, ao ligar sob carga). `timeout=None` lê `rede.sonda.prazo_leitura_s`; um prazo explícito continua valendo."""
    from types import SimpleNamespace as NS

    from app.config import RedeSondaCfg
    from app.devices.rede_aplicacao import FOLGA_DA_FILA_S

    pedidos: list[tuple[float, float]] = []

    class Fila:
        async def run(self, chamada, *, timeout, label):
            pedidos.append((chamada.keywords["timeout"], timeout))
            assert label == "rede do aparelho"
            return "ok"

    rt = NS(id="android-06", serial="s", external=False, executor=Fila(), adb=NS(shell=lambda *a, **k: "ok"))
    st = NS(cfg=NS(file=NS(rede=NS(sonda=RedeSondaCfg()))))
    ap = AparelhoPeloAdb(st, rt)
    assert await ap.shell("cat /proc/net/route", timeout=None) == "ok"
    assert await ap.shell("getprop", timeout=12) == "ok"
    assert pedidos == [(90.0, 90.0 + FOLGA_DA_FILA_S), (12, 12 + FOLGA_DA_FILA_S)]
    assert RedeSondaCfg().prazo_leitura_s == 90
    with pytest.raises(ValueError):
        RedeSondaCfg(prazo_leitura_s=5)
