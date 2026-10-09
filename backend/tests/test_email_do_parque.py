"""E-mail do parque: geração de endereço, allowlist, leitura do código (leitor falso) e o adaptador IMAP (cliente
falso, sem rede). Prova `simulated`: nada aqui toca a caixa real."""
from __future__ import annotations

import imaplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import pytest
from pydantic import SecretStr

from app.config import EnvSettings
from app.modules.email_do_parque.adapters import imap as imap_mod
from app.modules.email_do_parque.adapters.imap import LeitorImap, construir_email_do_parque, mensagem_de_bytes
from app.modules.email_do_parque.application.ports import Mensagem
from app.modules.email_do_parque.application.servico import ConfigEmail, EmailDoParque, ErroEmailDoParque
from app.modules.email_do_parque.domain.endereco import dominio_do_endereco, endereco_valido, gerar_endereco

AGORA = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
SENHA = "s3nh4-secreta-da-caixa"
ALVO = "ana.lima1234@nvit.com.br"


# ----------------------------------------------------------------------------------------- domínio
def _gerar(pid: str = "p1", nome: str = "João", sobre: str = "da Silva", dom: str = "nvit.com.br",
           existentes: tuple[str, ...] = ()) -> str:
    return gerar_endereco(persona_id=pid, primeiro_nome=nome, sobrenome=sobre, dominio=dom, existentes=existentes)


def test_endereco_normaliza_acento_e_caracteres_e_leva_sufixo_de_4_digitos() -> None:
    end = _gerar(nome="João", sobre="D'Ávila  Müller-Souza", dom="NVIT.com.br")
    local, dominio = end.split("@")
    assert dominio == "nvit.com.br"
    assert local[:-4] == "joao.davilamullersouza" and local[-4:].isdigit()
    assert endereco_valido(end) and dominio_do_endereco(end) == "nvit.com.br"


def test_endereco_e_deterministico_e_muda_com_a_persona() -> None:
    assert _gerar("p1") == _gerar("p1")
    assert _gerar("p1") != _gerar("p2")


def test_endereco_retenta_ate_ficar_unico_sem_olhar_caixa() -> None:
    primeiro = _gerar()
    segundo = _gerar(existentes=(primeiro.upper(),))
    assert segundo != primeiro
    terceiro = _gerar(existentes=(primeiro, segundo.upper()))
    assert terceiro not in (primeiro, segundo)


def test_nomes_vazios_viram_pessoa_e_sem_pontos_estranhos() -> None:
    assert _gerar(nome="", sobre="").startswith("pessoa") and ".." not in _gerar(nome="", sobre="")
    local = _gerar(nome="  ", sobre="...").split("@")[0]
    assert local.startswith("pessoa") and ".." not in local and not local.startswith(".")
    assert "." not in _gerar(nome="Ana", sobre="").split("@")[0]


def test_parte_local_nunca_passa_de_64() -> None:
    local = _gerar(nome="a" * 80, sobre="b" * 80).split("@")[0]
    assert len(local) <= 64 and not local.endswith(".")


def test_dominio_invalido_e_enderecos_invalidos() -> None:
    with pytest.raises(ValueError):
        _gerar(dom="sem-ponto")
    for ruim in ("", "sem-arroba", "@nvit.com.br", "a b@nvit.com.br", "a@@x.com", "a@nvit"):
        assert not endereco_valido(ruim)
        with pytest.raises(ValueError):
            dominio_do_endereco(ruim)


# ----------------------------------------------------------------------------------------- serviço
class LeitorFalso:
    def __init__(self, mensagens: list[Mensagem]) -> None:
        self.mensagens = mensagens
        self.chamadas: list[tuple[str, str, datetime]] = []

    async def buscar(self, *, destinatario: str, remetente: str, desde: datetime) -> list[Mensagem]:
        self.chamadas.append((destinatario, remetente, desde))
        return list(self.mensagens)


def _msg(para: str, quando: datetime, assunto: str = "", corpo: str = "",
         de: str = "security@mail.instagram.com") -> Mensagem:
    return Mensagem(remetente=de, destinatarios=(para,), assunto=assunto, corpo=corpo, recebida_em=quando)


def _servico(mensagens: list[Mensagem] | None = None, allowlist: tuple[str, ...] = ("nvit.com.br",),
             padrao: str = "nvit.com.br") -> EmailDoParque:
    leitor = None if mensagens is None else LeitorFalso(mensagens)
    return EmailDoParque(ConfigEmail(padrao, allowlist, imap_configurado=leitor is not None,
                                     remetente_codigo="instagram"), leitor,
                         agora=lambda: AGORA)


def test_allowlist_ok_fora_e_vazia() -> None:
    s = _servico(allowlist=("nvit.com.br", "Outro.com.br"))
    assert s.validar_dominio("  NVIT.com.br ") == "nvit.com.br"
    assert s.dominios_permitidos == ("nvit.com.br", "outro.com.br")
    for ruim in ("gmail.com", "", "  "):
        with pytest.raises(ErroEmailDoParque) as e:
            s.validar_dominio(ruim)
        assert e.value.code == "dominio_nao_permitido" and e.value.status == 422
    vazia = _servico(allowlist=())                       # vazia = só o domínio padrão
    assert vazia.dominios_permitidos == ("nvit.com.br",) and vazia.validar_dominio("nvit.com.br")
    nenhum = _servico(allowlist=(), padrao="")           # sem nada, nenhum domínio vale
    assert nenhum.dominios_permitidos == ()
    with pytest.raises(ErroEmailDoParque):
        nenhum.validar_dominio("nvit.com.br")


def test_servico_gera_so_em_dominio_permitido() -> None:
    s = _servico()
    end = s.gerar_endereco(persona_id="p1", primeiro_nome="Ana", sobrenome="Lima", dominio="NVIT.com.br",
                           existentes=())
    assert end.endswith("@nvit.com.br") and end.startswith("ana.lima")
    with pytest.raises(ErroEmailDoParque) as e:
        s.gerar_endereco(persona_id="p1", primeiro_nome="Ana", sobrenome="Lima", dominio="gmail.com", existentes=())
    assert e.value.code == "dominio_nao_permitido"


def test_confere_dominio() -> None:
    s = _servico()
    s.confere_dominio(ALVO, "NVIT.com.br")
    with pytest.raises(ErroEmailDoParque) as e:
        s.confere_dominio(ALVO, "outro.com.br")
    assert e.value.code == "dominio_divergente" and e.value.status == 422
    with pytest.raises(ErroEmailDoParque) as e2:
        s.confere_dominio("isso-nao-e-email", "nvit.com.br")
    assert e2.value.code == "email_invalido"


async def test_codigo_recente_acha_no_assunto_e_no_corpo() -> None:
    s = _servico([_msg(ALVO, AGORA - timedelta(minutes=2), assunto="123456 is your Instagram code")])
    achado = await s.codigo_recente(ALVO.upper())
    assert achado is not None and achado.codigo == "123456" and achado.recebido_em == AGORA - timedelta(minutes=2)
    assert achado.remetente == "security@mail.instagram.com"
    s2 = _servico([_msg(ALVO, AGORA, assunto="Confirme", corpo="Use o código 654321 para entrar. Ref 1234567")])
    achado2 = await s2.codigo_recente(ALVO)
    assert achado2 is not None and achado2.codigo == "654321"


async def test_a_mensagem_mais_recente_vence_e_o_leitor_recebe_a_janela() -> None:
    s = _servico([_msg(ALVO, AGORA - timedelta(minutes=20), assunto="111111 code"),
                  _msg(ALVO, AGORA - timedelta(minutes=1), assunto="222222 code"),
                  _msg(ALVO, AGORA - timedelta(minutes=10), assunto="333333 code")])
    achado = await s.codigo_recente(ALVO)
    assert achado is not None and achado.codigo == "222222"
    assert isinstance(s.leitor, LeitorFalso)
    assert s.leitor.chamadas[0] == (ALVO, "instagram", AGORA - timedelta(minutes=30))


async def test_a_mais_recente_sem_codigo_cai_para_a_anterior_com_codigo() -> None:
    s = _servico([_msg(ALVO, AGORA - timedelta(minutes=1), assunto="Bem-vinda", corpo="sem número"),
                  _msg(ALVO, AGORA - timedelta(minutes=5), assunto="444444 code")])
    achado = await s.codigo_recente(ALVO)
    assert achado is not None and achado.codigo == "444444"


async def test_ignora_outro_destinatario_remetente_e_mensagem_fora_da_janela() -> None:
    s = _servico([_msg("bia.souza9999@nvit.com.br", AGORA, assunto="999999 code"),
                  _msg(ALVO, AGORA, assunto="888888 code", de="promo@loja.com"),
                  _msg(ALVO, AGORA - timedelta(minutes=45), assunto="777777 code")])
    assert await s.codigo_recente(ALVO) is None
    alargada = await s.codigo_recente(ALVO, janela_min=60)               # o de promo@ continua de fora
    assert alargada is not None and alargada.codigo == "777777"
    loja = await s.codigo_recente(ALVO, "loja")
    assert loja is not None and loja.codigo == "888888"


async def test_sem_mensagem_devolve_none() -> None:
    assert await _servico([]).codigo_recente(ALVO) is None


async def test_sem_leitor_ou_imap_nao_configurado_e_email_indisponivel() -> None:
    for s in (_servico(None), EmailDoParque(ConfigEmail("nvit.com.br", (), False), LeitorFalso([]))):
        with pytest.raises(ErroEmailDoParque) as e:
            await s.codigo_recente(ALVO)
        assert e.value.code == "email_indisponivel" and e.value.status == 503


# ----------------------------------------------------------------------------------------- adaptador IMAP
def _rfc822(*, assunto: str = "123456 is your Instagram code", corpo: str = "Seu código: 123456",
            data: str = "Fri, 09 Oct 2026 11:58:00 +0000", html: bool = False) -> bytes:
    m = EmailMessage()
    m["From"] = "Instagram <security@mail.instagram.com>"
    m["To"] = ALVO
    m["Subject"] = assunto
    if data:
        m["Date"] = data
    m["X-Original-To"] = ALVO
    if html:
        m.set_content("<html><body style='color:#262626'><p>Código <b>123456</b></p></body></html>", subtype="html")
    else:
        m.set_content(corpo)
    return bytes(m)


def test_parser_decodifica_cabecalhos_e_corpo() -> None:
    msg = mensagem_de_bytes(_rfc822(assunto="Seu código é 654321 — confirmação"))
    assert msg is not None
    assert msg.remetente == "security@mail.instagram.com" and msg.destinatarios == (ALVO,)
    assert "654321" in msg.assunto and "Seu código: 123456" in msg.corpo
    assert msg.recebida_em == datetime(2026, 10, 9, 11, 58, tzinfo=timezone.utc)


def test_parser_converte_fuso_e_usa_html_so_sem_texto_puro() -> None:
    msg = mensagem_de_bytes(_rfc822(data="Fri, 09 Oct 2026 08:58:00 -0300", html=True))
    assert msg is not None and msg.recebida_em == datetime(2026, 10, 9, 11, 58, tzinfo=timezone.utc)
    assert "123456" in msg.corpo and "262626" not in msg.corpo       # a cor de estilo não vira "código"


def test_parser_sem_data_valida_descarta_a_mensagem() -> None:
    assert mensagem_de_bytes(_rfc822(data="")) is None
    assert mensagem_de_bytes(_rfc822(data="ontem de tarde")) is None


class ClienteImapFalso:
    """Troca `imaplib.IMAP4_SSL`: grava o que o adaptador pediu e devolve mensagens RFC822 prontas."""
    ultimo: "ClienteImapFalso | None" = None
    mensagens: list[bytes] = []
    falha_no_login: Exception | None = None

    def __init__(self, host: str, porta: int, timeout: float | None = None) -> None:
        self.args, self.timeout = (host, porta), timeout
        self.chamadas: list[tuple[object, ...]] = []
        self.logout_feito = False
        type(self).ultimo = self

    def login(self, usuario: str, senha: str) -> tuple[str, list[bytes]]:
        self.chamadas.append(("login", usuario, senha))
        if self.falha_no_login:
            raise self.falha_no_login
        return "OK", [b"ok"]

    def select(self, caixa: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.chamadas.append(("select", caixa, readonly))
        return "OK", [b"1"]

    def search(self, charset: object, *criterios: str) -> tuple[str, list[bytes]]:
        self.chamadas.append(("search", charset, criterios))
        return "OK", [b" ".join(str(i + 1).encode() for i in range(len(self.mensagens)))]

    def fetch(self, numero: str, partes: str) -> tuple[str, list[object]]:
        self.chamadas.append(("fetch", numero, partes))
        return "OK", [(numero.encode() + b" (BODY[] {1}", self.mensagens[int(numero) - 1]), b")"]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logout_feito = True
        return "BYE", []


@pytest.fixture
def imap_falso(monkeypatch: pytest.MonkeyPatch) -> type[ClienteImapFalso]:
    ClienteImapFalso.ultimo, ClienteImapFalso.mensagens, ClienteImapFalso.falha_no_login = None, [], None
    monkeypatch.setattr(imap_mod.imaplib, "IMAP4_SSL", ClienteImapFalso)
    return ClienteImapFalso


async def test_leitor_imap_busca_so_leitura_ordena_e_limita_a_10(imap_falso: type[ClienteImapFalso]) -> None:
    imap_falso.mensagens = [_rfc822(assunto=f"{100000 + i} code", data=f"Fri, 09 Oct 2026 11:{i:02d}:00 +0000")
                            for i in range(12)]
    leitor = LeitorImap("imap.hostinger.com", 993, "contato@nvit.com.br", SENHA)
    achadas = await leitor.buscar(destinatario=ALVO, remetente="instagram", desde=AGORA)
    c = imap_falso.ultimo
    assert c is not None and c.args == ("imap.hostinger.com", 993) and c.timeout == 15 and c.logout_feito
    assert ("select", "INBOX", True) in c.chamadas
    busca = next(x for x in c.chamadas if x[0] == "search")
    assert busca[2] == ("SINCE", "09-Oct-2026", "TO", f'"{ALVO}"', "FROM", '"instagram"')
    buscas = [x for x in c.chamadas if x[0] == "fetch"]
    assert len(buscas) == 10 and all(b[2] == "(BODY.PEEK[])" for b in buscas)
    assert len(achadas) == 10
    assert [m.recebida_em for m in achadas] == sorted((m.recebida_em for m in achadas), reverse=True)
    assert achadas[0].assunto == "100011 code"


async def test_leitor_imap_alimenta_o_servico_de_ponta_a_ponta(imap_falso: type[ClienteImapFalso]) -> None:
    imap_falso.mensagens = [_rfc822(assunto="555555 is your Instagram code")]
    s = construir_email_do_parque(dominio="nvit.com.br", allowlist=("nvit.com.br",), host="imap.hostinger.com",
                                  porta=993, usuario="contato@nvit.com.br", senha=SENHA, remetente_codigo="instagram")
    s._agora = lambda: AGORA
    achado = await s.codigo_recente(ALVO)
    assert achado is not None and achado.codigo == "555555"
    sem_remetente = construir_email_do_parque(dominio="nvit.com.br", allowlist=("nvit.com.br",),
                                              host="imap.hostinger.com", porta=993, usuario="contato@nvit.com.br",
                                              senha=SENHA)
    with pytest.raises(ErroEmailDoParque) as e:                        # sem remetente a caixa compartilhada vazaria
        await sem_remetente.codigo_recente(ALVO)
    assert e.value.code == "remetente_nao_configurado"


@pytest.mark.parametrize("falha", [imaplib.IMAP4.error(f"[AUTHENTICATIONFAILED] {SENHA}"),
                                   OSError(f"timeout com {SENHA}"), TimeoutError(SENHA)])
async def test_erro_de_rede_ou_login_vira_503_sem_vazar_a_senha(imap_falso: type[ClienteImapFalso],
                                                                falha: Exception) -> None:
    imap_falso.falha_no_login = falha
    leitor = LeitorImap("imap.hostinger.com", 993, "contato@nvit.com.br", SENHA)
    with pytest.raises(ErroEmailDoParque) as e:
        await leitor.buscar(destinatario=ALVO, remetente="instagram", desde=AGORA)
    assert (e.value.code, e.value.status) == ("email_indisponivel", 503)
    assert SENHA not in str(e.value) and SENHA not in e.value.message and SENHA not in repr(leitor)
    assert e.value.__cause__ is None and e.value.__suppress_context__
    assert imap_falso.ultimo is not None and imap_falso.ultimo.logout_feito


async def test_criterio_com_aspas_ou_quebra_de_linha_nao_chega_ao_servidor(
        imap_falso: type[ClienteImapFalso]) -> None:
    leitor = LeitorImap("h", 993, "u", SENHA)
    for ruim in ('a"b@nvit.com.br', "a\r\nb@nvit.com.br"):
        with pytest.raises(ErroEmailDoParque):
            await leitor.buscar(destinatario=ruim, remetente="instagram", desde=AGORA)
    assert imap_falso.ultimo is None


# ----------------------------------------------------------------------------------------- configuração
def test_construir_sem_credencial_deixa_o_leitor_none() -> None:
    for faltando in ({"host": ""}, {"usuario": ""}, {"senha": ""}):
        args = {"dominio": "nvit.com.br", "allowlist": (), "host": "h", "porta": 993, "usuario": "u", "senha": "s",
                **faltando}
        s = construir_email_do_parque(**args)  # type: ignore[arg-type]
        assert s.leitor is None and not s.config.imap_configurado
        assert s.validar_dominio("nvit.com.br") == "nvit.com.br"     # gerar/validar segue funcionando


def test_env_settings_email(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("EMAIL_DOMINIO", "EMAIL_IMAP_HOST", "EMAIL_IMAP_PORT", "EMAIL_IMAP_USER", "EMAIL_IMAP_PASS",
                 "EMAIL_ALLOWLIST_DOMINIOS"):
        monkeypatch.delenv(nome, raising=False)
    padrao = EnvSettings(_env_file=None)  # type: ignore[call-arg]
    assert padrao.email_imap_port == 993 and padrao.email_imap_pass is None and padrao.email_allowlist() == ()
    monkeypatch.setenv("EMAIL_IMAP_PORT", "")
    monkeypatch.setenv("EMAIL_IMAP_PASS", SENHA)
    monkeypatch.setenv("EMAIL_ALLOWLIST_DOMINIOS", " NVIT.com.br,, outro.com.br ,nvit.com.br")
    env = EnvSettings(_env_file=None)  # type: ignore[call-arg]
    assert env.email_imap_port == 993 and isinstance(env.email_imap_pass, SecretStr)
    assert env.email_allowlist() == ("nvit.com.br", "outro.com.br")
    assert SENHA not in repr(env) and env.chave("EMAIL_IMAP_PASS") == SENHA
