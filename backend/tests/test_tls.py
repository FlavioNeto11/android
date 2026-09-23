"""TLS na opção "porta de rede" (item 9.2, achado #120).

O que estava em jogo: o caminho (b) documentado — worker falando DIRETO com a porta de rede do central, único
caminho possível para um worker atrás de NAT que o central não alcança — entregava, em claro, o `API_TOKEN` a
cada requisição, a credencial permanente do worker a cada conexão, o cookie de sessão do painel e todo
screenshot. Rede Wi-Fi com chave compartilhada é rede legível por quem tem a chave.

Estes testes travam as duas pontas: o central se recusa a subir para a rede sem TLS, e o agente se recusa a
mandar a credencial dele por `http://` para qualquer endereço que não seja esta máquina.
"""
from __future__ import annotations

import ssl

import pytest
from pydantic import SecretStr, ValidationError

from app.main import conferir_exposicao, opcoes_do_uvicorn
from app.worker.agent import ws_url
from app.worker.settings import WorkerSettings

from .conftest import Harness

SEGREDO = "tk-parque-5b7e93af02c1d64"      # token de teste, não existe fora daqui


def _expor(h: Harness) -> None:
    h.cfg.file.server.host = "0.0.0.0"                       # noqa: S104 - é exatamente o cenário sob teste
    h.cfg.file.server.public_hosts = ["parque.local"]
    h.cfg.env.api_token = SecretStr(SEGREDO)


def _certificado(tmp_path, h: Harness) -> None:  # type: ignore[no-untyped-def]
    """Cert e chave como ARQUIVOS: `conferir_exposicao` só confere que existem — quem os lê é o uvicorn."""
    cert, chave = tmp_path / "parque.crt", tmp_path / "parque.key"
    cert.write_text("-- certificado de teste, não é um PEM válido --", encoding="utf-8")
    chave.write_text("-- chave de teste --", encoding="utf-8")
    h.cfg.file.server.tls_cert, h.cfg.file.server.tls_key = str(cert), str(chave)


# ---------------------------------------------------------------- o portão de subida
def test_nao_sobe_em_endereco_de_rede_sem_tls(harness: Harness) -> None:
    """A terceira condição, ao lado de API_TOKEN e public_hosts: o que sai desta máquina vai cifrado."""
    _expor(harness)
    with pytest.raises(SystemExit) as e:
        conferir_exposicao(harness.cfg)
    assert "TLS" in str(e.value)
    assert "tls_behind_proxy" in str(e.value)


def test_proxy_tls_declarado_basta(harness: Harness) -> None:
    """Terminar TLS num Caddy/nginx na frente é legítimo — o que não vale é subir sem nenhum dos dois."""
    _expor(harness)
    harness.cfg.file.server.tls_behind_proxy = True
    conferir_exposicao(harness.cfg)                          # não levanta
    assert harness.cfg.tls_ativo is True
    assert harness.cfg.tls_direto is None                    # o processo continua em HTTP; quem cifra é o proxy


def test_certificado_proprio_basta_quando_nao_ha_canal_de_tunel(tmp_path, harness: Harness) -> None:  # type: ignore[no-untyped-def]
    _expor(harness)
    _certificado(tmp_path, harness)
    harness.cfg.file.server.worker_port = 0
    conferir_exposicao(harness.cfg)                          # não levanta
    assert harness.cfg.tls_direto == (harness.cfg.file.server.tls_cert, harness.cfg.file.server.tls_key)


def test_certificado_proprio_com_o_canal_do_tunel_ligado_e_recusado(tmp_path, harness: Harness) -> None:  # type: ignore[no-untyped-def]
    """O listener do túnel é OUTRO socket do MESMO uvicorn, e TLS é do transporte: ligar o certificado faria
    127.0.0.1:<worker_port> passar a exigir `wss://` do agente, com um certificado emitido para o nome público.
    Falharia longe de quem editou o arquivo — então falha aqui, na subida, com o que fazer."""
    _expor(harness)
    _certificado(tmp_path, harness)
    harness.cfg.file.server.worker_port = 8010
    with pytest.raises(SystemExit) as e:
        conferir_exposicao(harness.cfg)
    assert "worker_port" in str(e.value)


def test_meio_certificado_e_recusado(tmp_path, harness: Harness) -> None:  # type: ignore[no-untyped-def]
    """Com só um dos dois o uvicorn sobe em HTTP simples e ninguém percebe."""
    _expor(harness)
    _certificado(tmp_path, harness)
    harness.cfg.file.server.tls_key = None
    with pytest.raises(SystemExit) as e:
        conferir_exposicao(harness.cfg)
    assert "tls_key" in str(e.value)


def test_certificado_apontando_para_arquivo_inexistente_e_recusado(tmp_path, harness: Harness) -> None:  # type: ignore[no-untyped-def]
    _expor(harness)
    _certificado(tmp_path, harness)
    harness.cfg.file.server.worker_port = 0
    harness.cfg.file.server.tls_cert = str(tmp_path / "nao-existe.crt")
    with pytest.raises(SystemExit) as e:
        conferir_exposicao(harness.cfg)
    assert "tls_cert" in str(e.value)


def test_loopback_continua_subindo_sem_tls(harness: Harness) -> None:
    """O caminho de quem roda tudo numa máquina não pode ter ficado mais difícil: HTTP no loopback é o desenho."""
    conferir_exposicao(harness.cfg)                          # não levanta
    assert harness.cfg.tls_ativo is False


# ---------------------------------------------------------------- o que chega ao uvicorn
def test_o_certificado_declarado_chega_ao_uvicorn(tmp_path, harness: Harness) -> None:  # type: ignore[no-untyped-def]
    """Sem porta nenhuma aberta: o que se confere é o argumento, que é onde o defeito estava (`main.py` criava
    `uvicorn.Config` sem `ssl_certfile`/`ssl_keyfile`, e não havia como declará-los)."""
    sem_tls = opcoes_do_uvicorn(harness.cfg)
    assert "ssl_certfile" not in sem_tls and "ssl_keyfile" not in sem_tls

    _certificado(tmp_path, harness)
    com_tls = opcoes_do_uvicorn(harness.cfg)
    assert com_tls["ssl_certfile"] == harness.cfg.file.server.tls_cert
    assert com_tls["ssl_keyfile"] == harness.cfg.file.server.tls_key
    # E o que já valia continua valendo: nada de confiar em X-Forwarded-For, nem com proxy TLS na frente.
    harness.cfg.file.server.tls_behind_proxy = True
    assert opcoes_do_uvicorn(harness.cfg)["proxy_headers"] is False
    assert opcoes_do_uvicorn(harness.cfg)["forwarded_allow_ips"] == []


# ---------------------------------------------------------------- o lado do agente
def test_o_agente_recusa_mandar_a_credencial_em_claro_pela_rede(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """`config/worker.example.yaml` mandava `server: http://192.168.1.10:8000` — e é essa conexão que carrega a
    credencial permanente deste worker, a cada reconexão."""
    with pytest.raises(ValidationError) as e:
        WorkerSettings(worker_id="w-teste", name="worker", work_dir=str(tmp_path),
                       server="http://192.168.1.10:8000", devices=[])
    assert "https://" in str(e.value)


def test_o_agente_continua_aceitando_http_no_loopback(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """É o alvo do túnel SSH (`-R 18000:127.0.0.1:8000`), e ali o tráfego já vai cifrado pelo túnel."""
    s = WorkerSettings(worker_id="w-teste", name="worker", work_dir=str(tmp_path),
                       server="http://127.0.0.1:18000", devices=[])
    assert s.ssl_context() is None
    assert ws_url(s.server) == "ws://127.0.0.1:18000/api/worker/ws"


def test_https_vira_wss_com_verificacao_ligada(tmp_path) -> None:  # type: ignore[no-untyped-def]
    s = WorkerSettings(worker_id="w-teste", name="worker", work_dir=str(tmp_path),
                       server="https://parque.local:8000", devices=[])
    assert ws_url(s.server) == "wss://parque.local:8000/api/worker/ws"
    ctx = s.ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.verify_mode is ssl.CERT_REQUIRED and ctx.check_hostname is True


def _ca_de_teste(destino) -> str:  # type: ignore[no-untyped-def]
    """Uma CA auto-assinada de mentira, gerada na hora. Nada dela sai deste diretório temporário."""
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    chave = ec.generate_private_key(ec.SECP256R1())
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA de teste do parque")])
    agora = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(chave.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(agora - timedelta(days=1))
            .not_valid_after(agora + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(chave, hashes.SHA256()))
    destino.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return str(destino)


def test_ca_propria_e_aceita_e_arquivo_ausente_e_recusado(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Certificado assinado por CA própria — o caso de um parque doméstico — é recusado por padrão pelo
    `websockets.connect`, corretamente. `ca_file` é o jeito de confiar NAQUELA autoridade, sem desligar nada."""
    s = WorkerSettings(worker_id="w-teste", name="worker", work_dir=str(tmp_path),
                       server="https://parque.local:8000", ca_file=_ca_de_teste(tmp_path / "parque-ca.pem"),
                       devices=[])
    ctx = s.ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is True                        # confiar na CA não é deixar de conferir o nome
    assert any("CA de teste do parque" in str(c) for c in ctx.get_ca_certs(binary_form=False))

    s.ca_file = str(tmp_path / "nao-existe.pem")
    with pytest.raises(ValueError, match="ca_file"):
        s.ssl_context()


def test_o_ping_do_protocolo_websocket_e_explicito(harness: Harness) -> None:
    """São os padrões do uvicorn — escritos para o próximo a depurar "Reconectando ao backend" achar os três prazos
    (protocolo 20+20 s aqui; aplicação 20+10 s em `ws.ts`; ressincronização por fila em `api.py`) sem adivinhar."""
    opcoes = opcoes_do_uvicorn(harness.cfg)
    assert opcoes["ws_ping_interval"] == 20.0 and opcoes["ws_ping_timeout"] == 20.0
