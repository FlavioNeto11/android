"""Testes de scripts/portal-config.py contra cópias do config.example.yaml em pasta temporária. Nenhum arquivo real
da instalação é lido ou alterado, e nada de rede.

O que protegem: (1) as âncoras que o `ligar` procura existem no exemplo versionado, de onde nasce o config.yaml de
toda instalação; (2) ligar e recuar são inversos exatos, byte a byte; (3) as duas ações são idempotentes e o ensaio
não grava."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[2]
SCRIPT = RAIZ / 'scripts' / 'portal-config.py'
SPEC = importlib.util.spec_from_file_location('portal_config', SCRIPT)
portal_config = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(portal_config)

EXEMPLO = (RAIZ / 'config' / 'config.example.yaml').read_bytes()
HOST = 'portal.exemplo.test'


def roda(tmp_path: Path, *args: str) -> tuple[int, str]:
    saida = io.StringIO()
    with redirect_stdout(saida):
        codigo = portal_config.main([*args, '--config', str(tmp_path / 'config.yaml'),
                                     '--backups', str(tmp_path / 'backups'), '--host', HOST])
    return codigo, saida.getvalue()


@pytest.fixture(params=['lf', 'crlf'])
def original(tmp_path: Path, request) -> bytes:
    """O exemplo versionado nos dois fins de linha: o config.yaml de uma instalação pode ter sido salvo no Windows."""
    lf = EXEMPLO.replace(b'\r\n', b'\n')
    conteudo = lf if request.param == 'lf' else lf.replace(b'\n', b'\r\n')
    (tmp_path / 'config.yaml').write_bytes(conteudo)
    return conteudo


def test_ensaio_nao_grava_nada(tmp_path, original):
    codigo, saida = roda(tmp_path, 'ligar', '--ensaio')
    assert codigo == 0 and 'ENSAIO' in saida and '7 linha(s)' in saida
    assert (tmp_path / 'config.yaml').read_bytes() == original
    assert not (tmp_path / 'backups').exists()


def test_ligar_conferir_e_recuar_devolvem_o_arquivo_identico(tmp_path, original):
    cfg = tmp_path / 'config.yaml'

    codigo, saida = roda(tmp_path, 'conferir')
    assert codigo == 1 and 'FALHOU' in saida  # o exemplo não nasce público

    codigo, saida = roda(tmp_path, 'ligar')
    assert codigo == 0 and 'cópia de segurança' in saida
    ligado = cfg.read_bytes()
    assert ligado != original
    # A cópia de segurança é o arquivo de antes, inteiro.
    copias = list((tmp_path / 'backups').glob('config.yaml.antes-portal-publico-*'))
    assert len(copias) == 1 and copias[0].read_bytes() == original
    # Fim de linha do arquivo preservado: nenhuma linha nova destoa.
    if b'\r\n' in original:
        assert b'\n' not in ligado.replace(b'\r\n', b'')
    else:
        assert b'\r' not in ligado

    codigo, saida = roda(tmp_path, 'conferir')
    assert codigo == 0, saida
    assert 'FALHOU' not in saida

    # Idempotente: ligar de novo não mexe.
    codigo, saida = roda(tmp_path, 'ligar')
    assert codigo == 0 and 'nada a fazer' in saida
    assert cfg.read_bytes() == ligado

    codigo, saida = roda(tmp_path, 'recuar', '--ensaio')
    assert codigo == 0 and '7 linha(s) saem' in saida
    assert cfg.read_bytes() == ligado

    codigo, saida = roda(tmp_path, 'recuar')
    assert codigo == 0
    assert cfg.read_bytes() == original  # inverso exato, byte a byte

    codigo, saida = roda(tmp_path, 'recuar')
    assert codigo == 0 and 'nada a fazer' in saida


def test_recuo_parcial_para_em_vez_de_adivinhar(tmp_path, original):
    """Se alguém mexeu à mão numa das linhas, o recuo não tira só parte: para e pede conferência."""
    cfg = tmp_path / 'config.yaml'
    assert roda(tmp_path, 'ligar')[0] == 0
    mexido = cfg.read_bytes().replace(b'  tls_behind_proxy: true', b'  tls_behind_proxy: true  # mexi', 1)
    cfg.write_bytes(mexido)
    codigo, saida = roda(tmp_path, 'recuar')
    assert codigo == 1 and 'PAROU' in saida
    assert cfg.read_bytes() == mexido


def test_ligar_para_quando_o_bloco_ja_e_publico_de_outro_jeito(tmp_path, original):
    cfg = tmp_path / 'config.yaml'
    cfg.write_bytes(original.replace(b'  # public_hosts: [parque.exemplo.local]', b'  public_hosts: [outro.exemplo.test]', 1))
    antes = cfg.read_bytes()
    assert antes != original, 'a linha comentada de public_hosts sumiu do exemplo'
    codigo, saida = roda(tmp_path, 'ligar')
    assert codigo == 1 and 'PAROU' in saida
    assert cfg.read_bytes() == antes


def test_as_duas_pontas_usam_as_mesmas_linhas():
    linhas = portal_config.linhas_do_portal(HOST)
    assert len(linhas) == len(set(linhas)) == 7
    assert f'    - {HOST}' in linhas and f'    - https://{HOST}' in linhas
