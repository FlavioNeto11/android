#!/usr/bin/env python3
"""Confere a documentação sem IA: links quebrados, CLAUDE.md, IDs do plano-100, estado.json, banco.md, decisões
e aprendizados. Python 3 puro, só leitura — não escreve nada.

Desde o 29.160 confere também o FORMATO de dois arquivos de configuração versionados, com o caminho da chave no erro:
`.claude/plano-100.json` (esquema próprio, só biblioteca padrão) e `config/config.example.yaml` (o modelo do backend,
`AppConfigFile`, é a única fonte do formato: chave desconhecida, tipo errado e bloco fora do formato). A segunda
conferência precisa de PyYAML e pydantic; sem eles ela AVISA e segue, então rode com o Python do venv do backend
para valer. O `config/config.yaml` da instalação (e o arquivo de ambiente) NUNCA é aberto por este script.

    python scripts/docs-check.py               # a partir da raiz do repo
    python scripts/docs-check.py --raiz <dir>   # para testes, com um mini-repo

Cada achado imprime uma linha `ERRO: ...` ou `AVISO: ...`. Saída 1 quando há pelo menos um ERRO. A última linha é
sempre o resumo `docs-check: X erros, Y avisos`.
"""
from __future__ import annotations

import argparse
import difflib
import json
from pathlib import Path
import re
import subprocess
import sys
import types
import typing

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
ITEM_RE = re.compile(r'(?:\d+|T)\.\d+')
PLANO_JSON = '.claude/plano-100.json'
EXEMPLO_YAML = 'config/config.example.yaml'
# Chaves que `claude-plan-100.py` e `plano-100-pacotes.py` leem (ou que o mapa documenta). Chave nova entra aqui E no
# consumidor; chave só aqui não faz nada, e é justamente o que o erro de "desconhecida" pega.
CHAVES_DO_PLANO = {'model', 'plan', 'prompt', 'batches', 'modelos'}
CHAVES_DO_LOTE = {'id', 'name', 'effort', 'items'}
ESFORCOS = {'low', 'medium', 'high', 'xhigh', 'max'}        # os mesmos de `config.Effort`
MODELOS_DO_PLANO = {'opus', 'sonnet', 'haiku'}


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


def _sugestao(nome: str, conhecidas: typing.Iterable[str]) -> str:
    parecidas = difflib.get_close_matches(str(nome), sorted(conhecidas), n=1)
    return f' (quis dizer `{parecidas[0]}`?)' if parecidas else ''


def checar_plano_json(raiz: Path, rel: Relatorio) -> bool:
    """29.160: o FORMATO de `.claude/plano-100.json`, com o caminho da chave. Devolve False quando a estrutura não
    permite as conferências de ID adiante (o erro já foi dado aqui). Ausente é válido: não é obrigação do mini-repo."""
    caminho = raiz / PLANO_JSON
    if not caminho.is_file():
        return True
    n_antes = len(rel.erros)

    def erro(onde: str, msg: str) -> None:
        rel.erro(f'{PLANO_JSON}: {onde}: {msg}' if onde else f'{PLANO_JSON}: {msg}')

    try:
        dados = ler_json(caminho)
    except json.JSONDecodeError as e:
        erro(f'linha {e.lineno}, coluna {e.colno}', f'JSON inválido ({e.msg})')
        return False
    if not isinstance(dados, dict):
        erro('', f'o arquivo deve ser um objeto, veio {type(dados).__name__}')
        return False
    for chave in sorted(set(dados) - CHAVES_DO_PLANO):
        erro(chave, 'chave desconhecida (nenhum script a lê)' + _sugestao(chave, CHAVES_DO_PLANO))
    for chave in ('model', 'plan', 'prompt'):
        if chave in dados and not (isinstance(dados[chave], str) and dados[chave].strip()):
            erro(chave, 'deve ser um texto não vazio')
    if 'plan' not in dados:
        erro('plan', 'chave obrigatória ausente (o caminho do plano; claude-plan-100.py a lê)')
    for chave in ('plan', 'prompt'):
        valor = dados.get(chave)
        if isinstance(valor, str) and valor.strip() and not (raiz / valor).is_file():
            erro(chave, f'o arquivo "{valor}" não existe')

    lotes = dados.get('batches')
    ids_do_mapa: set[str] = set()
    if lotes is None:
        erro('batches', 'chave obrigatória ausente (a lista de blocos)')
    elif not isinstance(lotes, list) or not lotes:
        erro('batches', 'deve ser uma lista não vazia de blocos')
    else:
        vistos: set[str] = set()
        for i, lote in enumerate(lotes):
            onde = f'batches[{i}]'
            if not isinstance(lote, dict):
                erro(onde, f'o bloco deve ser um objeto, veio {type(lote).__name__}')
                continue
            for chave in sorted(set(lote) - CHAVES_DO_LOTE):
                erro(f'{onde}.{chave}', 'chave desconhecida (nenhum script a lê)' + _sugestao(chave, CHAVES_DO_LOTE))
            ident = lote.get('id')
            if not (isinstance(ident, str) and ident.strip()):
                erro(f'{onde}.id', 'obrigatório, texto não vazio')
            elif ident in vistos:
                erro(f'{onde}.id', f'id de bloco repetido: "{ident}"')
            else:
                vistos.add(ident)
                onde = f'batches[{i}] ("{ident}")'
            if 'name' in lote and not isinstance(lote['name'], str):
                erro(f'{onde}.name', 'deve ser texto')
            if 'effort' in lote and lote['effort'] not in ESFORCOS:
                erro(f'{onde}.effort', f'esforço "{lote["effort"]}" fora de {sorted(ESFORCOS)}')
            itens = lote.get('items')
            if not isinstance(itens, list) or not itens:
                erro(f'{onde}.items', 'obrigatório, lista não vazia de IDs do plano')
                continue
            for j, item in enumerate(itens):
                if not (isinstance(item, str) and ITEM_RE.fullmatch(item)):
                    erro(f'{onde}.items[{j}]', 'ID fora do formato "N.M" ou "T.M" (texto, não número)')
                else:
                    ids_do_mapa.add(item)

    modelos = dados.get('modelos')
    if 'modelos' in dados:
        if not isinstance(modelos, dict):
            erro('modelos', 'deve ser um objeto {ID do plano: modelo}')
        else:
            for item, modelo in modelos.items():
                if not ITEM_RE.fullmatch(str(item)):
                    erro(f'modelos.{item}', 'a chave deve ser um ID do plano ("N.M" ou "T.M")')
                elif isinstance(lotes, list) and ids_do_mapa and item not in ids_do_mapa:
                    erro(f'modelos.{item}', 'esse ID não está em nenhum bloco (o modelo seria ignorado)')
                if modelo not in MODELOS_DO_PLANO:
                    erro(f'modelos.{item}', f'modelo "{modelo}" fora de {sorted(MODELOS_DO_PLANO)}')
    return len(rel.erros) == n_antes


def _carregar_modelo_de_config() -> tuple[typing.Any, typing.Any, str | None]:
    """(AppConfigFile, BaseModel, None) do backend DESTA árvore, ou (None, None, motivo) quando faltam dependências.
    Importa só `app.config` (definições; nenhuma leitura de arquivo da instalação: `load_config` nunca é chamado)."""
    backend = Path(__file__).resolve().parents[1] / 'backend'
    if not (backend / 'app' / 'config.py').is_file():
        return None, None, 'backend/app/config.py não existe nesta árvore'
    try:
        import yaml  # noqa: F401
        import pydantic
    except ImportError as e:
        return None, None, f'falta o pacote {e.name}'
    carregado = sys.modules.get('app')
    if carregado is not None and not str(getattr(carregado, '__file__', '')).startswith(str(backend)):
        return None, None, 'outro pacote `app` já está carregado neste processo'
    sys.path.insert(0, str(backend))
    try:
        from app.config import AppConfigFile
    except Exception as e:  # dependência do backend ausente, ou o config.py não importa: o motivo vai no aviso
        return None, None, f'{type(e).__name__} ao importar app.config ({e})'
    finally:
        sys.path.remove(str(backend))
    return AppConfigFile, pydantic.BaseModel, None


def _alvo_do_tipo(anotacao, base_model):
    """O modelo pydantic que a anotação aponta: ('modelo'|'lista'|'mapa', Classe), ou None (valor solto)."""
    origem, args = typing.get_origin(anotacao), typing.get_args(anotacao)
    if origem is typing.Annotated:
        return _alvo_do_tipo(args[0], base_model)
    if origem is typing.Union or origem is types.UnionType:
        for a in args:
            achado = _alvo_do_tipo(a, base_model)
            if achado:
                return achado
        return None
    if origem in (list, set, tuple, frozenset) and args:
        achado = _alvo_do_tipo(args[0], base_model)
        return ('lista', achado[1]) if achado and achado[0] == 'modelo' else None
    if origem is dict and len(args) == 2:
        achado = _alvo_do_tipo(args[1], base_model)
        return ('mapa', achado[1]) if achado and achado[0] == 'modelo' else None
    if isinstance(anotacao, type) and issubclass(anotacao, base_model):
        return ('modelo', anotacao)
    return None


def _nomes_aceitos(modelo) -> set[str]:
    nomes: set[str] = set()
    for nome, campo in modelo.model_fields.items():
        nomes.add(nome)
        for alias in (campo.alias, campo.validation_alias):
            if isinstance(alias, str):
                nomes.add(alias)
            elif alias is not None and hasattr(alias, 'choices'):
                nomes.update(c for c in alias.choices if isinstance(c, str))
    return nomes


def _chaves_desconhecidas(valor, modelo, base_model, caminho: str, achados: list[tuple[str, str]]) -> None:
    """Percorre o YAML pelo MODELO do backend. O pydantic ignora em silêncio a chave que o modelo não declara (só o
    bloco `portal` recusa), e é esse o erro que passava sem aviso: `instances.countt: 10` valia como padrão."""
    if not isinstance(valor, dict):
        return
    if modelo.model_config.get('extra') != 'allow':
        aceitos = _nomes_aceitos(modelo)
        for chave in valor:
            if chave not in aceitos:
                achados.append((f'{caminho}.{chave}' if caminho else str(chave),
                                'chave desconhecida (o backend a ignoraria em silêncio)' + _sugestao(chave, aceitos)))
    for nome, campo in modelo.model_fields.items():
        if nome not in valor:
            continue
        alvo = _alvo_do_tipo(campo.annotation, base_model)
        if alvo is None:
            continue
        tipo, classe = alvo
        sub = f'{caminho}.{nome}' if caminho else nome
        if tipo == 'modelo':
            _chaves_desconhecidas(valor[nome], classe, base_model, sub, achados)
        elif tipo == 'lista' and isinstance(valor[nome], list):
            for i, elemento in enumerate(valor[nome]):
                _chaves_desconhecidas(elemento, classe, base_model, f'{sub}[{i}]', achados)
        elif tipo == 'mapa' and isinstance(valor[nome], dict):
            for chave, elemento in valor[nome].items():
                _chaves_desconhecidas(elemento, classe, base_model, f'{sub}.{chave}', achados)


def checar_config_exemplo(raiz: Path, rel: Relatorio) -> None:
    """29.160: `config/config.example.yaml` contra o modelo do backend. Só o exemplo versionado é aberto: o
    `config.yaml` da instalação é por máquina, fora do Git, e este script nunca o lê. Só o CAMINHO e a regra violada
    saem na mensagem, nunca o valor lido (o erro do pydantic traz o `input`; aqui ele é descartado)."""
    caminho = raiz / EXEMPLO_YAML
    if not caminho.is_file():
        return
    modelo, base_model, motivo = _carregar_modelo_de_config()
    if modelo is None:
        rel.aviso(f'{EXEMPLO_YAML}: formato NÃO conferido ({motivo}). Rode com o Python do venv do backend: '
                  'backend\\.venv\\Scripts\\python.exe scripts\\docs-check.py')
        return
    import yaml
    from pydantic import ValidationError
    try:
        bruto = yaml.safe_load(ler(caminho))
    except yaml.YAMLError as e:
        marca = getattr(e, 'problem_mark', None)
        onde = f'linha {marca.line + 1}, coluna {marca.column + 1}' if marca else ''
        rel.erro(f'{EXEMPLO_YAML}: {onde + ": " if onde else ""}YAML inválido ({getattr(e, "problem", "erro de sintaxe")})')
        return
    if bruto is None:
        bruto = {}
    if not isinstance(bruto, dict):
        rel.erro(f'{EXEMPLO_YAML}: o arquivo deve ser um mapa de blocos, veio {type(bruto).__name__}')
        return
    desconhecidas: list[tuple[str, str]] = []
    _chaves_desconhecidas(bruto, modelo, base_model, '', desconhecidas)
    for onde, msg in desconhecidas:
        rel.erro(f'{EXEMPLO_YAML}: {onde}: {msg}')
    try:
        modelo.model_validate(bruto)
    except ValidationError as e:
        for item in e.errors():
            if item['type'] == 'extra_forbidden':     # o percurso acima já deu esta, com a sugestão
                continue
            onde = '.'.join(str(parte) for parte in item['loc']) or '(raiz)'
            rel.erro(f'{EXEMPLO_YAML}: {onde}: {item["msg"]}')


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
    # Com o formato do mapa errado, as conferências de ID abaixo quebrariam em traceback: o erro de formato já disse o quê.
    if checar_plano_json(raiz, rel):
        checar_mapa_do_plano(raiz, ids, rel)
    checar_config_exemplo(raiz, rel)
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
