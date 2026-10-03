# -*- coding: utf-8 -*-
"""Hostname público do portal no `config/config.yaml` da instalação (ADR-073, item 29.54): ligar, recuar e conferir.

    python scripts/portal-config.py ligar [--ensaio]     # declara o hostname (com cópia de segurança)
    python scripts/portal-config.py recuar [--ensaio]    # tira exatamente as linhas que o `ligar` pôs
    python scripts/portal-config.py conferir             # só lê: o bloco `server` está pronto para o portal?

Por que um script: são três mudanças no bloco `server` que só valem juntas (`public_hosts`, `tls_behind_proxy` e a
origem https em `allowed_origins`), e o recuo tem de tirar as mesmas linhas, nem mais nem menos. As duas pontas usam
a mesma lista (`linhas_do_portal`), então não há como uma mudar sem a outra.

O que ele NÃO faz: não reinicia o central (a mudança vale no próximo reinício da tarefa `farm-central`), não mexe no
túnel nem na Cloudflare, e não lê nem imprime o `.env`: saber se o `API_TOKEN` existe é papel do `GET /api/health`
(`exposicao_publica_incompleta`) e da prova de fora (`scripts/portal-prova-de-fora.sh`).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys

RAIZ = Path(__file__).resolve().parents[1]
HOST_PADRAO = 'dev.nvit.com.br'
ANCORA_TLS = '# tls_behind_proxy: true'
ANCORA_ORIGENS = '  allowed_origins:'


def linhas_do_bloco(host: str) -> list[str]:
    """As seis linhas que entram no bloco `server`, logo depois da âncora comentada do `tls_behind_proxy`."""
    return [
        '  # Portal público pelo túnel da Cloudflare (ADR-073, item 29.54; pedido do dono em 03/10/2026): o cloudflared',
        f'  # desta máquina entrega https://{host} em 127.0.0.1:8000 com o Host preservado. O host segue em',
        '  # loopback; sem credencial só abrem o painel estático e as rotas de sessão. Recuo: tirar o hostname daqui.',
        '  public_hosts:',
        f'    - {host}',
        '  tls_behind_proxy: true',
    ]


def linha_da_origem(host: str) -> str:
    return f'    - https://{host}'


def linhas_do_portal(host: str) -> list[str]:
    """Tudo o que o `ligar` põe e o `recuar` tira. Fonte única das duas pontas."""
    return linhas_do_bloco(host) + [linha_da_origem(host)]


def _bloco_server(linhas: list[str]) -> tuple[int, int]:
    """Índices [ini, fim) do bloco `server:` (do cabeçalho até a próxima chave de topo)."""
    ini = next((i for i, l in enumerate(linhas) if l.rstrip() == 'server:'), None)
    if ini is None:
        raise ValueError('não achei o bloco `server:` no config.yaml')
    fim = next((i for i in range(ini + 1, len(linhas)) if linhas[i].strip() and not linhas[i].startswith((' ', '#'))),
               len(linhas))
    return ini, fim


def planejar_ligar(texto: str, host: str) -> tuple[str | None, str]:
    """Devolve (texto novo ou None, mensagem). None com mensagem 'nada a fazer' é idempotência; ValueError é parada."""
    linhas = texto.split('\n')
    ini, fim = _bloco_server(linhas)
    bloco = linhas[ini:fim]

    ja_publico = any(l.strip().startswith('public_hosts:') for l in bloco)
    ja_tls = any(l.strip() == 'tls_behind_proxy: true' for l in bloco)
    ja_origem = any(l.rstrip() == linha_da_origem(host) for l in bloco)
    if ja_publico and ja_tls and ja_origem:
        return None, 'nada a fazer: o hostname público já está ligado no config.yaml'
    if ja_publico or ja_tls:
        raise ValueError('o bloco server já tem public_hosts ou tls_behind_proxy ativos de outro jeito; confira à mão')

    ancora_tls = next((i for i, l in enumerate(bloco) if l.strip() == ANCORA_TLS), None)
    ancora_origens = next((i for i, l in enumerate(bloco) if l.rstrip() == ANCORA_ORIGENS), None)
    if ancora_tls is None or ancora_origens is None:
        raise ValueError(f'não achei as âncoras ({ANCORA_TLS} / {ANCORA_ORIGENS.strip()}) no bloco server')
    # Último item da lista de origens (linhas `    - ...` logo depois de `allowed_origins:`).
    ultimo_item = ancora_origens
    for j in range(ancora_origens + 1, len(bloco)):
        if bloco[j].startswith('    - '):
            ultimo_item = j
        elif bloco[j].strip() == '' or bloco[j].startswith('    #'):
            continue
        else:
            break
    if ultimo_item == ancora_origens:
        raise ValueError('allowed_origins sem itens; confira à mão')

    novo = list(bloco)
    # Arquivo em CRLF: as linhas novas levam o mesmo fim de linha, senão o arquivo ficaria misturado.
    cr = '\r' if any(l.endswith('\r') for l in linhas) else ''
    # A origem entra primeiro: ela fica DEPOIS da âncora do TLS, e inserir antes deslocaria o índice dela.
    if ancora_origens < ancora_tls:
        raise ValueError('allowed_origins vem antes da âncora do tls_behind_proxy; confira à mão')
    if not ja_origem:
        novo.insert(ultimo_item + 1, linha_da_origem(host) + cr)
    novo[ancora_tls + 1:ancora_tls + 1] = [l + cr for l in linhas_do_bloco(host)]
    entram = len(novo) - len(bloco)
    return '\n'.join(linhas[:ini] + novo + linhas[fim:]), f'{entram} linha(s) entram no bloco server'


def planejar_recuar(texto: str, host: str) -> tuple[str | None, str]:
    linhas = texto.split('\n')
    tirar = set(linhas_do_portal(host))
    novas = [l for l in linhas if l.rstrip() not in tirar]
    saem = len(linhas) - len(novas)
    if saem == 0:
        return None, 'nada a fazer: o hostname público não está no config.yaml'
    if saem != len(tirar):
        raise ValueError(f'esperava tirar {len(tirar)} linhas e achei {saem}; confira à mão')
    return '\n'.join(novas), f'{saem} linha(s) saem do config.yaml'


def conferir(texto: str, host: str) -> list[tuple[str, bool]]:
    """Pontos do bloco `server` que o portal exige. Só leitura; nada de segredo passa por aqui."""
    import yaml  # PyYAML vem com o ambiente do backend; só o `conferir` precisa dele.

    cfg = yaml.safe_load(texto) or {}
    s = cfg.get('server') or {}
    origens = list(s.get('allowed_origins') or [])
    porta = s.get('port', 8000)
    return [
        (f'server.public_hosts tem {host}', host in list(s.get('public_hosts') or [])),
        ('server.tls_behind_proxy true', s.get('tls_behind_proxy') is True),
        (f'https://{host} em server.allowed_origins', f'https://{host}' in origens),
        ('server.host em loopback (o túnel entrega em 127.0.0.1)', str(s.get('host', '127.0.0.1')) in ('127.0.0.1', '::1', 'localhost')),
        ('server.worker_port diferente da porta do painel', s.get('worker_port', 0) != porta),
    ]


def _grava(cfg: Path, novo: str, backups: Path, rotulo: str) -> Path:
    carimbo = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    destino = backups / f'config.yaml.{rotulo}-{carimbo}'
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cfg, destino)
    cfg.write_text(novo, encoding='utf-8', newline='\n')
    return destino


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Hostname público do portal no config.yaml (ADR-073).')
    p.add_argument('acao', choices=['ligar', 'recuar', 'conferir'])
    p.add_argument('--host', default=HOST_PADRAO, help=f'hostname público (padrão: {HOST_PADRAO})')
    p.add_argument('--config', type=Path, default=RAIZ / 'config' / 'config.yaml')
    p.add_argument('--backups', type=Path, default=RAIZ / 'data' / 'backups')
    p.add_argument('--ensaio', action='store_true', help='não grava nada: só diz o que mudaria')
    a = p.parse_args(argv)

    if not a.config.exists():
        print(f'PAROU: não achei {a.config}')
        return 1
    # `read_bytes` + decode preserva as quebras de linha do arquivo (o `read_text` as normalizaria no Windows).
    texto = a.config.read_bytes().decode('utf-8')

    if a.acao == 'conferir':
        pontos = conferir(texto, a.host)
        ruins = 0
        for nome, ok in pontos:
            print('  ok    ' if ok else '  FALHOU', nome)
            ruins += 0 if ok else 1
        print('RESULTADO:', f'{len(pontos)} de {len(pontos)}' if ruins == 0 else f'{ruins} ponto(s) fora')
        return 1 if ruins else 0

    try:
        novo, mensagem = (planejar_ligar if a.acao == 'ligar' else planejar_recuar)(texto, a.host)
    except ValueError as erro:
        print('PAROU:', erro)
        return 1
    if novo is None:
        print(mensagem)
        return 0
    if a.ensaio:
        print('ENSAIO:', mensagem, '(nada foi gravado)')
        return 0
    rotulo = 'antes-portal-publico' if a.acao == 'ligar' else 'com-portal-publico'
    destino = _grava(a.config, novo, a.backups, rotulo)
    print('cópia de segurança:', destino.name)
    print(f'config.yaml atualizado ({mensagem}); vale no próximo reinício do central')
    return 0


if __name__ == '__main__':
    sys.exit(main())
