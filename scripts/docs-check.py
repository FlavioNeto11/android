#!/usr/bin/env python3
"""Confere a documentação sem IA: links quebrados, CLAUDE.md, IDs do plano-100, estado.json, banco.md, decisões
e aprendizados. Python 3 puro, sem dependências, só leitura — não escreve nada.

    python scripts/docs-check.py               # a partir da raiz do repo
    python scripts/docs-check.py --raiz <dir>   # para testes, com um mini-repo

Cada achado imprime uma linha `ERRO: ...` ou `AVISO: ...`. Saída 1 quando há pelo menos um ERRO. A última linha é
sempre o resumo `docs-check: X erros, Y avisos`.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

DOC_GLOBS = ['CLAUDE.md', 'README.md', 'CHANGELOG.md', 'docs/**/*.md',
             '.claude/skills/**/SKILL.md', '.claude/rules/*.md']
LINK_RE = re.compile(r'!?\[[^\]]*\]\(([^)]+)\)')
ID_RE = re.compile(r'^\|\s*((?:\d+|T)\.\d+)\s*\|', re.M)
ROW_RE = re.compile(r'^\|\s*([^|]*?)\s*\|')
ESTADOS = {'implemented', 'partial', 'blocked'}
PROVAS = {'real', 'simulated', 'not_run'}
CONTAGEM_RE = re.compile(r'(\d+)\s+de\s+(\d+)\s+itens')
MIGRACAO_RE = re.compile(r'^(\d{3})_')
ADR_RE = re.compile(r'^##\s+(ADR-\d+)\b', re.M)
K_RE = re.compile(r'^###\s+(K-\d+)\b', re.M)


class Relatorio:
    def __init__(self) -> None:
        self.erros: list[str] = []
        self.avisos: list[str] = []

    def erro(self, msg: str) -> None:
        self.erros.append(msg)

    def aviso(self, msg: str) -> None:
        self.avisos.append(msg)

    def imprimir(self) -> int:
        for msg in self.erros:
            print(f'ERRO: {msg}')
        for msg in self.avisos:
            print(f'AVISO: {msg}')
        print(f'docs-check: {len(self.erros)} erros, {len(self.avisos)} avisos')
        return 1 if self.erros else 0


def ler(caminho: Path) -> str:
    return caminho.read_text(encoding='utf-8', errors='replace')


def ler_json(caminho: Path):
    return json.loads(ler(caminho))


def _sem_codigo(texto: str) -> list[str]:
    """Linhas do texto com blocos cercados e code spans apagados (mas a contagem de linhas preservada)."""
    linhas = texto.split('\n')
    saida = []
    em_bloco = False
    marca = ''
    for linha in linhas:
        limpa = linha.strip()
        if not em_bloco and re.match(r'^(```+|~~~+)', limpa):
            em_bloco = True
            marca = limpa[:3]
            saida.append('')
            continue
        if em_bloco:
            if limpa.startswith(marca):
                em_bloco = False
            saida.append('')
            continue
        saida.append(re.sub(r'`[^`]*`', '', linha))
    return saida


def _alvo_do_link(bruto: str) -> str | None:
    alvo = bruto.strip()
    if alvo.startswith('<') and alvo.endswith('>'):
        alvo = alvo[1:-1].strip()
    # remove título opcional: [x](caminho "título") ou [x](caminho 'título')
    partes = alvo.split(None, 1)
    if partes:
        alvo = partes[0]
    if not alvo or alvo.startswith('#'):
        return None
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', alvo) and not re.match(r'^[a-zA-Z]:[\\/]', alvo):
        # esquema (http:, https:, mailto:, etc.) — não é um caminho de arquivo local; letra-de-drive do Windows
        # ("C:/...") não conta como esquema.
        return None
    alvo = alvo.split('#', 1)[0]
    alvo = re.sub(r':\d+$', '', alvo)
    return alvo.strip() or None


def _local_nao_versionado(raiz: Path, destino: Path) -> bool:
    """O alvo é um arquivo que o Git manda ignorar (`.gitignore` ou `.git/info/exclude`)?

    O handoff da sessão (`.claude/handoff-current.md`) é local de propósito: fica em `.git/info/exclude` e por isso não
    existe num clone ou worktree novo. Um link para ele não está quebrado, só aponta para algo que cada máquina gera. A
    pergunta vai ao próprio Git em vez de uma lista no script, então vale para qualquer arquivo local declarado assim. Sem
    Git (ou fora de um repositório) a resposta é "não" e o link continua sendo cobrado.
    """
    try:
        r = subprocess.run(['git', 'check-ignore', '-q', '--', str(destino)], cwd=raiz, capture_output=True,
                           timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def checar_links(raiz: Path, arquivos: list[Path], rel: Relatorio) -> None:
    for arquivo in arquivos:
        texto = ler(arquivo)
        linhas = _sem_codigo(texto)
        historico = 'docs/auditoria-2026-09-21' in arquivo.relative_to(raiz).as_posix()
        for n, linha in enumerate(linhas, start=1):
            for m in LINK_RE.finditer(linha):
                alvo = _alvo_do_link(m.group(1))
                if alvo is None:
                    continue
                destino = (raiz / alvo.lstrip('/')) if alvo.startswith('/') else (arquivo.parent / alvo)
                if destino.exists() or _local_nao_versionado(raiz, destino):
                    continue
                msg = (f'{arquivo.relative_to(raiz).as_posix()}:{n}: link quebrado para '
                       f'"{m.group(1)}"')
                (rel.aviso if historico else rel.erro)(msg)


def checar_claude_md(raiz: Path, rel: Relatorio) -> None:
    caminho = raiz / 'CLAUDE.md'
    if not caminho.is_file():
        rel.erro('CLAUDE.md não existe.')
        return
    n = len(ler(caminho).splitlines())
    if n > 200:
        rel.erro(f'CLAUDE.md tem {n} linhas (limite: 200).')


def ids_do_plano(raiz: Path) -> list[str] | None:
    caminho = raiz / 'docs/plano-100.md'
    if not caminho.is_file():
        return None
    return ID_RE.findall(ler(caminho))


def checar_mapa_do_plano(raiz: Path, ids: list[str] | None, rel: Relatorio) -> None:
    if ids is None:
        return
    repetidos_no_plano = sorted({i for i in ids if ids.count(i) > 1})
    if repetidos_no_plano:
        rel.erro('docs/plano-100.md repete ID(s): ' + ', '.join(repetidos_no_plano))
    caminho = raiz / '.claude/plano-100.json'
    if not caminho.is_file():
        return
    config = ler_json(caminho)
    mapa = [item for lote in config.get('batches', []) for item in lote.get('items', [])]
    repetidos = sorted({i for i in mapa if mapa.count(i) > 1})
    if repetidos:
        rel.erro('.claude/plano-100.json repete ID(s): ' + ', '.join(repetidos))
    faltando = sorted(set(ids) - set(mapa))
    sobrando = sorted(set(mapa) - set(ids))
    if faltando:
        rel.erro('.claude/plano-100.json não cobre: ' + ', '.join(faltando))
    if sobrando:
        rel.erro('.claude/plano-100.json cita ID que não está no plano: ' + ', '.join(sobrando))


def checar_roadmap(raiz: Path, ids: list[str] | None, rel: Relatorio) -> None:
    caminho = raiz / 'docs/roadmap.md'
    if ids is None or not caminho.is_file():
        return
    ids_validos = set(ids)
    citados = set()
    for linha in _sem_codigo(ler(caminho)):
        m = ROW_RE.match(linha)
        if not m:
            continue
        celula = m.group(1).strip()
        if re.fullmatch(r'(?:\d+|T)\.\d+', celula):
            citados.add(celula)
    invalidos = sorted(citados - ids_validos)
    if invalidos:
        rel.erro('docs/roadmap.md cita ID fora do plano: ' + ', '.join(invalidos))


def checar_estado(raiz: Path, ids: list[str] | None, rel: Relatorio) -> dict | None:
    caminho = raiz / '.claude/plano-100/estado.json'
    if not caminho.is_file():
        return None
    estado = ler_json(caminho)
    itens = estado.get('itens', {})
    invalidos = sorted(i for i, v in itens.items()
                        if not isinstance(v, dict) or v.get('status') not in ESTADOS
                        or v.get('proof') not in PROVAS)
    if invalidos:
        rel.aviso('.claude/plano-100/estado.json com status/proof fora do vocabulário: ' + ', '.join(invalidos))
    if ids is not None:
        orfaos = sorted(set(itens) - set(ids))
        if orfaos:
            rel.aviso('.claude/plano-100/estado.json cita ID que não está no plano: ' + ', '.join(orfaos))
    return estado


def checar_relatorio_de_execucao(raiz: Path, ids: list[str] | None, estado: dict | None, rel: Relatorio) -> None:
    caminho = raiz / 'docs/execucao-plano-100-runner.md'
    if ids is None or estado is None or not caminho.is_file():
        return
    texto = ler(caminho)
    m = CONTAGEM_RE.search(texto)
    if not m:
        return
    relatado_feitos, relatado_total = int(m.group(1)), int(m.group(2))
    feitos = sum(1 for i in ids if estado.get('itens', {}).get(i, {}).get('status') == 'implemented')
    total = len(ids)
    if (relatado_feitos, relatado_total) != (feitos, total):
        rel.aviso('docs/execucao-plano-100-runner.md diz '
                  f'"{relatado_feitos} de {relatado_total} itens", mas o estado atual é "{feitos} de {total}". '
                  'Rode: python scripts/claude-plan-100.py relatorio')


def checar_banco(raiz: Path, rel: Relatorio) -> None:
    pasta = raiz / 'backend/migrations'
    caminho = raiz / 'docs/banco.md'
    if not pasta.is_dir() or not caminho.is_file():
        return
    texto = ler(caminho)
    faltando = []
    for arquivo in sorted(pasta.glob('*.sql')):
        m = MIGRACAO_RE.match(arquivo.name)
        if not m:
            continue
        numero = m.group(1)
        if not re.search(rf'(?<!\d){numero}(?!\d)', texto):
            faltando.append(arquivo.name)
    if faltando:
        rel.aviso('docs/banco.md não cita a(s) migração(ões): ' + ', '.join(faltando))


def checar_ids_unicos(raiz: Path, caminho_rel: str, padrao: re.Pattern, rel: Relatorio) -> None:
    caminho = raiz / caminho_rel
    if not caminho.is_file():
        return
    achados = padrao.findall(ler(caminho))
    repetidos = sorted({i for i in achados if achados.count(i) > 1})
    if repetidos:
        rel.erro(f'{caminho_rel} repete ID(s): ' + ', '.join(repetidos))


def coletar_arquivos_de_doc(raiz: Path) -> list[Path]:
    achados: list[Path] = []
    for padrao in DOC_GLOBS:
        achados.extend(sorted(raiz.glob(padrao)))
    vistos = set()
    unicos = []
    for caminho in achados:
        if caminho.is_file() and caminho not in vistos:
            vistos.add(caminho)
            unicos.append(caminho)
    return unicos


def rodar(raiz: Path) -> int:
    rel = Relatorio()
    checar_links(raiz, coletar_arquivos_de_doc(raiz), rel)
    checar_claude_md(raiz, rel)
    ids = ids_do_plano(raiz)
    checar_mapa_do_plano(raiz, ids, rel)
    checar_roadmap(raiz, ids, rel)
    estado = checar_estado(raiz, ids, rel)
    checar_relatorio_de_execucao(raiz, ids, estado, rel)
    checar_banco(raiz, rel)
    checar_ids_unicos(raiz, 'docs/decisoes.md', ADR_RE, rel)
    checar_ids_unicos(raiz, 'docs/conhecimento/aprendizados.md', K_RE, rel)
    return rel.imprimir()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--raiz', default='.', help='Raiz do repositório a conferir (padrão: diretório atual).')
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass
    return rodar(Path(args.raiz).resolve())


if __name__ == '__main__':
    sys.exit(main())
