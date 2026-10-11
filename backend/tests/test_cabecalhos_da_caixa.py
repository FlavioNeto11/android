"""31.336: `GET /api/instagram/contas/{id}/cabecalhos`, só os CABEÇALHOS da caixa da conta (nunca o corpo).

Serve para a pergunta "o Instagram chegou a mandar e-mail a esta conta?" (confirmação do cadastro), "alguém devolveu?" e "como o
servidor de entrada autenticou?", sem abrir mensagem nenhuma. Só leitura: `INBOX` em readonly e `BODY.PEEK[HEADER.FIELDS ...]`.

Nível de prova: `simulated` (leitor de e-mail falso e `imaplib` falso). Nenhuma IMAP real.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.main import create_app
from app.models import PersonaCreate
from app.modules.email_do_parque.adapters import imap
from app.modules.email_do_parque.adapters.imap import LeitorImap, cabecalho_de_bytes
from app.modules.email_do_parque.application.ports import CabecalhoDeMensagem
from app.modules.email_do_parque.application.servico import ConfigEmail, EmailDoParque, ErroEmailDoParque

from .conftest import Harness

DOM = "nvit.com.br"
AGORA = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
CABECALHO = (b"From: Instagram <security@mail.instagram.com>\r\n"
             b"Subject: 123456 is your Instagram code\r\n"
             b"Date: Sun, 11 Oct 2026 11:30:00 +0000\r\n"
             b"Authentication-Results: mx.hostinger.com; spf=pass smtp.mailfrom=mail.instagram.com;"
             b" dkim=pass header.d=mail.instagram.com; dmarc=pass header.from=mail.instagram.com\r\n\r\n")


# ------------------------------------------------------------------ o cabeçalho
def test_cabecalho_de_bytes_le_remetente_assunto_data_e_autenticacao() -> None:
    c = cabecalho_de_bytes(CABECALHO)
    assert c is not None
    assert c.remetente == "security@mail.instagram.com" and c.recebida_em == datetime(2026, 10, 11, 11, 30, tzinfo=timezone.utc)
    assert dict(c.autenticacao) == {"spf": "pass", "dkim": "pass", "dmarc": "pass"} and c.devolucao is False


def test_aviso_de_devolucao_e_reconhecido_e_sem_data_nao_vale() -> None:
    devolvida = cabecalho_de_bytes(b"From: Mail Delivery Subsystem <MAILER-DAEMON@x.com>\r\nSubject: Undelivered Mail\r\n"
                                   b"Date: Sun, 11 Oct 2026 11:00:00 +0000\r\n\r\n")
    assert devolvida is not None and devolvida.devolucao is True
    assert cabecalho_de_bytes(b"From: a@b.com\r\nSubject: sem data\r\n\r\n") is None


# ------------------------------------------------------------------ o IMAP: nunca o corpo
class _ImapFalso:
    comandos: list[str] = []

    def __init__(self, host: str, porta: int, timeout: int = 0) -> None:
        self.readonly: bool | None = None

    def login(self, usuario: str, senha: str) -> tuple[str, list[bytes]]:
        return "OK", [b""]

    def select(self, caixa: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.readonly = readonly
        _ImapFalso.comandos.append(f"select readonly={readonly}")
        return "OK", [b"1"]

    def search(self, *args: object) -> tuple[str, list[bytes]]:
        _ImapFalso.comandos.append("search " + " ".join(str(a) for a in args))
        return "OK", [b"1"]

    def fetch(self, numero: str, partes: str) -> tuple[str, list[object]]:
        _ImapFalso.comandos.append(f"fetch {partes}")
        return "OK", [(b"1 (BODY[HEADER.FIELDS] {1}", CABECALHO), b")"]

    def logout(self) -> None:
        return None


@pytest.mark.asyncio
async def test_o_imap_pede_so_o_cabecalho_em_modo_leitura(monkeypatch: pytest.MonkeyPatch) -> None:
    _ImapFalso.comandos = []
    monkeypatch.setattr(imap.imaplib, "IMAP4_SSL", _ImapFalso)
    achadas = await LeitorImap("h", 993, "u", "s").listar_cabecalhos(destinatario=f"a@{DOM}", desde=AGORA - timedelta(hours=5), limite=5)
    assert len(achadas) == 1
    comandos = "\n".join(_ImapFalso.comandos)
    assert "select readonly=True" in comandos
    assert "BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE AUTHENTICATION-RESULTS)]" in comandos
    for proibido in ("BODY.PEEK[])", "RFC822", "BODY[TEXT", "BODY[1"):
        assert proibido not in comandos, "o corpo nunca é pedido"


@pytest.mark.asyncio
async def test_criterio_perigoso_e_recusado_sem_abrir_conexao() -> None:
    with pytest.raises(ErroEmailDoParque) as e:
        await LeitorImap("h", 993, "u", "s").listar_cabecalhos(destinatario='a"@x', desde=AGORA, limite=1)
    assert e.value.code == "email_invalido"


# ------------------------------------------------------------------ o serviço
class _LeitorDeCabecalhos:
    def __init__(self, itens: list[CabecalhoDeMensagem]) -> None:
        self.itens = itens

    async def listar_cabecalhos(self, *, destinatario: str, desde: datetime, limite: int) -> list[CabecalhoDeMensagem]:
        return self.itens


def _c(minutos: int, assunto: str, *, devolucao: bool = False) -> CabecalhoDeMensagem:
    return CabecalhoDeMensagem(remetente="security@mail.instagram.com", assunto=assunto,
                               recebida_em=AGORA - timedelta(minutes=minutos), autenticacao=(("spf", "pass"),),
                               devolucao=devolucao)


@pytest.mark.asyncio
async def test_servico_mascara_seis_digitos_ordena_e_corta_fora_da_janela() -> None:
    leitor = _LeitorDeCabecalhos([_c(300, "antigo"), _c(30, "654321 is your code"), _c(10, "Confirm your email 111222 now")])
    svc = EmailDoParque(ConfigEmail(DOM, (DOM,), True, "instagram"), leitor, agora=lambda: AGORA)  # type: ignore[arg-type]
    r = await svc.cabecalhos(f"a@{DOM}", horas=2, limite=10)
    assert [c.assunto for c in r] == ["Confirm your email ###### now", "###### is your code"]


@pytest.mark.asyncio
async def test_sem_imap_ou_leitor_sem_cabecalhos_e_503() -> None:
    for leitor, imap_ok in ((None, False), (object(), True)):
        svc = EmailDoParque(ConfigEmail(DOM, (DOM,), imap_ok, "instagram"), leitor, agora=lambda: AGORA)  # type: ignore[arg-type]
        with pytest.raises(ErroEmailDoParque) as e:
            await svc.cabecalhos(f"a@{DOM}")
        assert e.value.code == "email_indisponivel" and e.value.status == 503


# ------------------------------------------------------------------ a rota
def _cliente(h: Harness, leitor: object | None) -> httpx.AsyncClient:
    h.state.email_parque = EmailDoParque(ConfigEmail(DOM, (DOM,), leitor is not None, "instagram"), leitor,  # type: ignore[arg-type]
                                         agora=lambda: AGORA)
    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _conta(c: httpx.AsyncClient, h: Harness) -> str:
    pid = h.state.social.create_persona(PersonaCreate(
        name="Ana Lima", first_name="Ana", last_name="Lima", birth_date="1990-05-17", locale="pt-BR")).id
    r = await c.post("/api/instagram/contas", json={
        "persona_id": pid, "dominio": DOM, "email": f"ana.lima1234@{DOM}", "email_senha": "Senha-do-email-9f3a",
        "instagram_username": "ana.ceramica", "instagram_senha": "Senha-do-insta-71cc", "igfarm_account_id": "ig-9",
        "criada_em": "2026-10-11T10:00:00Z"})
    assert r.status_code == 201, r.text
    return str(r.json()["account_id"])


@pytest.mark.asyncio
async def test_rota_devolve_so_cabecalhos_sem_corpo_nem_destinatario(harness: Harness) -> None:
    leitor = _LeitorDeCabecalhos([_c(20, "Confirm your email 424242"), _c(90, "Undelivered Mail", devolucao=True)])
    async with _cliente(harness, leitor) as c:
        conta = await _conta(c, harness)
        r = await c.get(f"/api/instagram/contas/{conta}/cabecalhos", params={"horas": 6})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["total"] == 2 and d["account_id"] == conta
        assert d["mensagens"][0]["assunto"] == "Confirm your email ######"
        assert d["mensagens"][0]["autenticacao"] == {"spf": "pass"} and d["mensagens"][1]["devolucao"] is True
        assert "424242" not in r.text and f"ana.lima1234@{DOM}" not in r.text and "corpo" not in r.text
        assert (await c.get("/api/instagram/contas/nao-existe/cabecalhos")).status_code == 404


@pytest.mark.asyncio
async def test_rota_sem_imap_e_503(harness: Harness) -> None:
    async with _cliente(harness, None) as c:
        conta = await _conta(c, harness)
        r = await c.get(f"/api/instagram/contas/{conta}/cabecalhos")
        assert r.status_code == 503 and r.json()["detail"]["code"] == "email_indisponivel"
