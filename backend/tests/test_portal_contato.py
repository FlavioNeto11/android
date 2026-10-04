"""`POST /api/portal/contato` (29.77, ADR-075): isca, token, validação, taxa, tetos, gravação antes do aviso e reenvio.

Prova `simulated`: o harness de sempre e uma Canais FALSA no lugar de `avisar_contato_do_portal` (o contrato
`portal.contato` do 28.32; o código dela não está nesta base). Nomes e telefones fictícios; nada vai a Telegram.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
import pytest
from pydantic import SecretStr

from app.main import create_app
from app.modules.portal.application.protecao import cliente_pseudonimo, emitir_token
from app.modules.portal.infrastructure.contatos_sql import RETENCAO_DIAS
from app.util import now

from .conftest import Harness

PUBLICO = "dev.nvit.com.br"
SEGREDO = "tk-portal-contato-93ad0e"          # token de teste, não existe fora daqui
PAR_DO_TUNEL = ("127.0.0.1", 41234)
ROTA = "/api/portal/contato"
TELEFONE = "+55 (00) 0000-0009"               # fictício
IP = "203.0.113.7"                             # TEST-NET-3 (RFC 5737)


@dataclass(frozen=True)
class ContatoFalso:
    contato_id: int
    nome: str
    empresa: str | None
    telefone: str | None
    mensagem: str


@dataclass(frozen=True)
class AvisadoFalso:
    enfileirado: bool
    motivo: str | None


class CanaisFalsa:
    def __init__(self, resposta: AvisadoFalso = AvisadoFalso(True, None)) -> None:
        self.resposta = resposta
        self.recebidos: list[ContatoFalso] = []

    def __call__(self, contato: ContatoFalso) -> AvisadoFalso:
        self.recebidos.append(contato)
        return self.resposta


def _ligar(h: Harness, monkeypatch: pytest.MonkeyPatch, canais: CanaisFalsa | None = None, *,
           ligado: bool = True) -> CanaisFalsa | None:
    h.cfg.file.portal.contato_ligado = ligado
    h.cfg.file.server.public_hosts = [PUBLICO]
    h.cfg.file.server.tls_behind_proxy = True
    h.cfg.env.api_token = SecretStr(SEGREDO)
    h.cfg.file.server.allowed_origins = [*h.cfg.file.server.allowed_origins, f"https://{PUBLICO}"]
    assert h.state is not None
    if canais is not None:
        monkeypatch.setattr(h.state.avisos, "avisar_contato_do_portal", canais, raising=False)
        monkeypatch.setattr(h.state.portal.contatos, "_tipo", lambda: ContatoFalso)
    return canais


def _cliente(h: Harness, *, ip: str = IP, origem: str | None = f"https://{PUBLICO}") -> httpx.AsyncClient:
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    cab = {"cf-connecting-ip": ip}
    if origem:
        cab["Origin"] = origem
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=PAR_DO_TUNEL),
                             base_url=f"http://{PUBLICO}", headers=cab)


def _token(h: Harness, idade_s: float = 10) -> str:
    assert h.state is not None
    return emitir_token(h.state.portal.contatos.sal(), time.time() - idade_s)


def _corpo(h: Harness, **muda: object) -> dict[str, object]:
    corpo: dict[str, object] = {"nome": "Visitante Fictício", "empresa": "Empresa Fictícia", "telefone": TELEFONE,
                                "mensagem": "Quero conhecer a ANA.\nPodem ligar à tarde?", "consentimento": True,
                                "site": "", "token": _token(h)}
    corpo.update(muda)
    return corpo


def _linhas(h: Harness) -> list[dict[str, object]]:
    assert h.state is not None
    return [dict(r) for r in h.state.db.query("SELECT * FROM portal_contatos ORDER BY id")]


# ---------------------------------------------------------------- bandeira e portão
async def test_desligado_e_404_e_o_resto_da_api_segue_fechado(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _ligar(harness, monkeypatch, ligado=False)
    async with _cliente(harness) as c:
        # Literal de propósito: `test_cobertura_de_rotas` lê o caminho do AST, não resolve a constante.
        assert (await c.post("/api/portal/contato", json=_corpo(harness))).status_code == 404
        assert (await c.get(ROTA)).status_code == 401          # só POST ganha a exceção do portão
        assert (await c.post("/api/portal/outra", json={})).status_code == 401
    assert _linhas(harness) == []


async def test_origem_de_fora_e_host_estranho_seguem_recusados(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    canais = _ligar(harness, monkeypatch, CanaisFalsa())
    async with _cliente(harness, origem="https://golpe.example") as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 403
    app = create_app(harness.cfg, state=harness.state)
    app.state.poc = harness.state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=PAR_DO_TUNEL),
                                 base_url="http://outro.example") as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 403
    assert canais is not None and canais.recebidos == [] and _linhas(harness) == []


# ---------------------------------------------------------------- caminho feliz
async def test_aceito_grava_antes_e_entrega_a_canais(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                     caplog: pytest.LogCaptureFixture) -> None:
    canais = _ligar(harness, monkeypatch, CanaisFalsa())
    caplog.set_level(logging.DEBUG)
    async with _cliente(harness) as c:
        r = await c.post(ROTA, json=_corpo(harness))
    assert r.status_code == 202 and r.json() == {"ok": True} and r.headers["cache-control"] == "no-store"
    [linha] = _linhas(harness)
    assert linha["estado"] == "entregue" and linha["tentativas"] == 1 and linha["telefone"] == TELEFONE
    assert linha["cliente_hash"] != IP and IP not in str(linha)
    assert canais is not None
    [enviado] = canais.recebidos
    assert enviado == ContatoFalso(int(str(linha["id"])), "Visitante Fictício", "Empresa Fictícia", TELEFONE,
                                   "Quero conhecer a ANA.\nPodem ligar à tarde?")
    assert TELEFONE not in caplog.text and IP not in caplog.text and "Visitante" not in caplog.text


# ---------------------------------------------------------------- robôs: a mesma resposta, nada gravado
@pytest.mark.parametrize("muda", [{"site": "http://spam.example"}, {"token": ""}, {"token": "123.abc"}])
async def test_isca_e_token_falso_respondem_igual_e_nao_gravam(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                               muda: dict[str, object]) -> None:
    canais = _ligar(harness, monkeypatch, CanaisFalsa())
    async with _cliente(harness) as c:
        r = await c.post(ROTA, json=_corpo(harness, **muda))
    assert r.status_code == 202 and r.json() == {"ok": True}
    assert _linhas(harness) == [] and canais is not None and canais.recebidos == []


async def test_token_cedo_e_robo_e_expirado_e_pessoa(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _ligar(harness, monkeypatch, CanaisFalsa())
    async with _cliente(harness) as c:
        cedo = await c.post(ROTA, json=_corpo(harness, token=_token(harness, idade_s=0)))
        velho = await c.post(ROTA, json=_corpo(harness, token=_token(harness, idade_s=3 * 86_400)))
    assert cedo.status_code == 202
    assert velho.status_code == 400 and velho.json()["detail"]["code"] == "token_expirado"
    assert _linhas(harness) == []


# ---------------------------------------------------------------- validação
async def test_campos_e_consentimento(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _ligar(harness, monkeypatch, CanaisFalsa())
    async with _cliente(harness) as c:
        for muda, campo in (({"nome": "  "}, "nome"), ({"nome": "x" * 81}, "nome"), ({"telefone": "abc"}, "telefone"),
                            ({"telefone": "123"}, "telefone"), ({"mensagem": "m" * 1501}, "mensagem"),
                            ({"empresa": ["lista"]}, "empresa")):
            r = await c.post(ROTA, json=_corpo(harness, **muda))
            assert r.status_code == 422 and campo in r.json()["detail"]["campos"], muda
        r = await c.post(ROTA, json=_corpo(harness, consentimento=False))
        assert r.status_code == 422 and r.json()["detail"]["code"] == "consentimento_ausente"
        assert (await c.post(ROTA, json=["não", "é", "objeto"])).status_code == 422
    assert _linhas(harness) == []


async def test_415_sem_json_e_413_acima_do_teto(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _ligar(harness, monkeypatch, CanaisFalsa())
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, data={"nome": "x"})).status_code == 415
        grande = _corpo(harness, mensagem="m" * 10_000)
        assert (await c.post(ROTA, json=grande)).status_code == 413
        assert (await c.post(ROTA, content=b"{nao e json", headers={"Content-Type": "application/json"})).status_code == 422
    assert _linhas(harness) == []


async def test_controle_de_caractere_sai_e_quebra_so_na_mensagem(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    canais = _ligar(harness, monkeypatch, CanaisFalsa())
    async with _cliente(harness) as c:
        r = await c.post(ROTA, json=_corpo(harness, nome="Nome\nANA: falso\x07", mensagem="linha 1\r\nlinha 2\x00"))
    assert r.status_code == 202
    assert canais is not None
    assert canais.recebidos[0].nome == "NomeANA: falso" and canais.recebidos[0].mensagem == "linha 1\nlinha 2"


# ---------------------------------------------------------------- taxa e tetos
async def test_taxa_por_cliente_e_por_hora(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _ligar(harness, monkeypatch, CanaisFalsa())
    async with _cliente(harness) as c:
        for _ in range(3):
            assert (await c.post(ROTA, json=_corpo(harness))).status_code == 202
        r = await c.post(ROTA, json=_corpo(harness))
        assert r.status_code == 429 and "telefones" in r.json()["detail"]["message"]
    async with _cliente(harness, ip="198.51.100.20") as outro:
        assert (await outro.post(ROTA, json=_corpo(harness))).status_code == 202
    assert len(_linhas(harness)) == 4


def test_ipv6_conta_pelo_64() -> None:
    sal = b"s" * 32
    assert cliente_pseudonimo(sal, "ip:2001:db8:1:2::a") == cliente_pseudonimo(sal, "ip:2001:db8:1:2:ffff::1")
    assert cliente_pseudonimo(sal, "ip:2001:db8:1:2::a") != cliente_pseudonimo(sal, "ip:2001:db8:1:3::a")
    assert cliente_pseudonimo(sal, "ip:::ffff:203.0.113.7") == cliente_pseudonimo(sal, "ip:203.0.113.7")


async def test_teto_diario_descarta_sem_guardar_o_conteudo(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    canais = _ligar(harness, monkeypatch, CanaisFalsa())
    harness.cfg.file.portal.limites.guardados_dia = 10
    assert harness.state is not None
    for i in range(10):
        harness.state.portal.repo.gravar(nome="n", empresa="", telefone="00000000", mensagem="m",
                                         cliente_hash=f"outro-{i}", agora=now())
    async with _cliente(harness) as c:
        r = await c.post(ROTA, json=_corpo(harness))
    assert r.status_code == 429
    ultima = _linhas(harness)[-1]
    assert ultima["estado"] == "descartado" and ultima["motivo"] == "teto_diario"
    assert ultima["nome"] == ultima["telefone"] == ultima["mensagem"] == ""
    assert canais is not None and canais.recebidos == []


async def test_teto_por_hora_retem_e_o_reenvio_entrega_depois(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    canais = _ligar(harness, monkeypatch, CanaisFalsa())
    harness.cfg.file.portal.limites.telegram_hora = 1
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 202
    async with _cliente(harness, ip="198.51.100.21") as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 202
    assert [l["estado"] for l in _linhas(harness)] == ["entregue", "retido"]
    assert harness.state is not None and canais is not None
    assert harness.state.portal.contatos.reenviar(now()) == {"retido": 1}       # a janela ainda está cheia
    assert harness.state.portal.contatos.reenviar(now() + timedelta(hours=1, minutes=1)) == {"entregue": 1}
    assert [l["estado"] for l in _linhas(harness)] == ["entregue", "entregue"] and len(canais.recebidos) == 2


# ---------------------------------------------------------------- canal desligado ou ausente: guarda e reenvia
async def test_canal_desligado_guarda_e_o_reenvio_usa_o_mesmo_id(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    canais = _ligar(harness, monkeypatch, CanaisFalsa(AvisadoFalso(False, "canal_desligado")))
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 202
    [linha] = _linhas(harness)
    assert linha["estado"] == "pendente" and linha["motivo"] == "canal_desligado"
    assert canais is not None and harness.state is not None
    canais.resposta = AvisadoFalso(True, None)
    assert harness.state.portal.contatos.reenviar(now()) == {"entregue": 1}
    assert [c.contato_id for c in canais.recebidos] == [linha["id"], linha["id"]]
    assert _linhas(harness)[0]["tentativas"] == 1          # `canal_desligado` é espera, não conta como falha


async def test_sem_o_codigo_da_canais_o_contato_fica_pendente(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    _ligar(harness, monkeypatch)
    assert harness.state is not None
    monkeypatch.setattr(harness.state.portal.contatos, "_tipo", lambda: None)
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 202
    [linha] = _linhas(harness)
    assert linha["estado"] == "pendente" and linha["motivo"] == "canal_ausente"


async def test_falha_da_canais_nao_perde_o_contato(harness: Harness, monkeypatch: pytest.MonkeyPatch,
                                                   caplog: pytest.LogCaptureFixture) -> None:
    def explode(contato: ContatoFalso) -> object:
        raise RuntimeError(f"fora do ar ao mandar {contato.telefone}")       # a mensagem cita o dado do visitante

    _ligar(harness, monkeypatch, CanaisFalsa())
    assert harness.state is not None
    monkeypatch.setattr(harness.state.avisos, "avisar_contato_do_portal", explode, raising=False)
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 202
    [linha] = _linhas(harness)
    assert linha["estado"] == "pendente" and linha["motivo"] == "falha_interna"
    assert "RuntimeError" in caplog.text and TELEFONE not in caplog.text


# ---------------------------------------------------------------- retenção
def test_retencao_apaga_so_o_que_passou_do_prazo(harness: Harness) -> None:
    assert harness.state is not None
    repo = harness.state.portal.repo
    repo.gravar(nome="velho", empresa="", telefone="00000000", mensagem="m", cliente_hash="a",
                agora=now() - timedelta(days=RETENCAO_DIAS + 1))
    repo.gravar(nome="novo", empresa="", telefone="00000000", mensagem="m", cliente_hash="b", agora=now())
    assert repo.apagar_vencidos(now()) == 1
    assert [l["nome"] for l in _linhas(harness)] == ["novo"]


def test_sal_nasce_uma_vez_e_fica(harness: Harness) -> None:
    assert harness.state is not None
    repo = harness.state.portal.repo
    assert repo.sal() == repo.sal() and len(repo.sal()) == 32


# ---------------------------------------------------------------- contra a Canais REAL (28.32, #331)
async def test_canal_desligado_na_canais_real_fica_pendente(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem falso: o tipo e a função vêm de `app.modules.avisos` (resolvidos na hora de usar) e o aviso está desligado
    no harness."""
    _ligar(harness, monkeypatch)
    harness.cfg.file.avisos.enabled = False
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 202
    [linha] = _linhas(harness)
    assert linha["estado"] == "pendente" and linha["motivo"] == "canal_desligado" and linha["tentativas"] == 0


async def test_canais_real_enfileira_uma_vez_pela_chave(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_avisos_servico import CanalFalso

    _ligar(harness, monkeypatch)
    assert harness.state is not None
    harness.cfg.file.avisos.enabled = True
    monkeypatch.setattr(harness.state.avisos, "_canal", CanalFalso())
    async with _cliente(harness) as c:
        assert (await c.post(ROTA, json=_corpo(harness))).status_code == 202
    [linha] = _linhas(harness)
    assert linha["estado"] == "entregue"
    fila = [dict(r) for r in harness.state.db.query(
        "SELECT chave, tipo FROM avisos_entregas WHERE tipo='portal.contato' ORDER BY chave")]
    assert fila == [{"chave": f"portal:{linha['id']}", "tipo": "portal.contato"}]
    # O reenvio do mesmo id (o laço depois de uma resposta perdida) não duplica a mensagem.
    resultado = harness.state.portal.contatos.entregar(
        int(str(linha["id"])), {"nome": "Visitante Fictício", "empresa": "", "telefone": TELEFONE, "mensagem": "x"}, now())
    assert resultado == "entregue"
    assert harness.state.db.scalar("SELECT COUNT(*) AS n FROM avisos_entregas WHERE tipo='portal.contato'") == 1


# ---------------------------------------------------------------- revisão do #333
def test_vinte_contatos_que_quebram_a_canais_nao_prendem_o_seguinte(harness: Harness,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """A2: 20 linhas cuja entrega levanta, depois uma boa. Pela ordem `tentativas, id` a boa sai na 2ª volta, e as
    ruins viram `descartado` (`falhas_demais`) depois de `FALHAS_MAX` falhas."""
    from app.modules.portal.application.contato import FALHAS_MAX

    canais = CanaisFalsa()

    def seletiva(contato: ContatoFalso) -> AvisadoFalso:
        if contato.mensagem == "quebra":
            raise RuntimeError("conteúdo que a Canais não aceita")
        return canais(contato)

    _ligar(harness, monkeypatch, canais)
    assert harness.state is not None
    monkeypatch.setattr(harness.state.avisos, "avisar_contato_do_portal", seletiva, raising=False)
    repo = harness.state.portal.repo
    for i in range(20):
        repo.gravar(nome="n", empresa="", telefone="00000000", mensagem="quebra", cliente_hash=f"c{i}", agora=now())
    boa = repo.gravar(nome="n", empresa="", telefone="00000000", mensagem="boa", cliente_hash="c-boa", agora=now())
    servico = harness.state.portal.contatos
    assert servico.reenviar(now()) == {"pendente": 20}                  # a boa não coube no lote de 20
    assert servico.reenviar(now()) == {"entregue": 1, "pendente": 19}   # e sai na volta seguinte, à frente das ruins
    assert [c.contato_id for c in canais.recebidos] == [boa]
    for _ in range(FALHAS_MAX + 1):
        servico.reenviar(now())
    ruins = [l for l in _linhas(harness) if l["id"] != boa]
    assert {(l["estado"], l["motivo"]) for l in ruins} == {("descartado", "falhas_demais")}


def test_saude_avisa_contato_ligado_sem_ip_da_borda(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """M2: sem `tls_behind_proxy`, todo visitante do túnel vira a mesma chave da taxa."""
    _ligar(harness, monkeypatch, CanaisFalsa())
    assert harness.state is not None

    def problema() -> object:
        assert harness.state is not None
        return next((p for p in harness.state.health().problems if p.code == "portal_contato_sem_ip_da_borda"), None)

    assert problema() is None
    harness.cfg.file.server.tls_behind_proxy = False
    achado = problema()
    assert achado is not None and "tls_behind_proxy" in achado.message      # type: ignore[attr-defined]
    harness.cfg.file.portal.contato_ligado = False
    assert problema() is None


def test_contato_sem_o_site_e_recusado_na_subida() -> None:
    """M3: os dois ligam juntos; a combinação recusada é a que aceitaria (202) sem página que emita o token."""
    from pydantic import ValidationError

    from app.config import PortalCfg

    with pytest.raises(ValidationError, match="contato_ligado exige portal.site_ligado"):
        PortalCfg(contato_ligado=True)
    assert PortalCfg(site_ligado=True, contato_ligado=True).contato_ligado
    assert PortalCfg(site_ligado=True).site_ligado
