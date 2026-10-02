#!/usr/bin/env python3
"""Ablação A × A+B do retrieval semântico (J12, ADR-063): vale pagar a etapa B? Medida por REGIÃO, além do arquivo.

Pergunta: a etapa B (chunks -> regiões) entrega, sobre a etapa A sozinha (mapa -> arquivos), o suficiente para justificar
os tokens e o orçamento que pede? A métrica de arquivo (hit@k) não vê isso, porque a B não muda o ranking de arquivos; o que
ela muda é QUANTO CÓDIGO o consumidor precisa ler. Por pergunta, com UMA execução (a A e a B no mesmo pedido, custo medido
por etapa pelo próprio provedor):

- A sozinha: os candidatos que o Jev escolheu no mapa, hit@1/3/5 por arquivo e as linhas que o consumidor leria (arquivos
  inteiros dos 5 primeiros);
- A+B: as regiões da B, se a região esperada do gabarito cai em alguma (sobreposição e fração coberta) e as linhas lidas;
- alcançabilidade: a região esperada estava dentro dos chunks que a B recebeu? (o chunker corta janelas do INÍCIO de cada
  arquivo, na ordem dos candidatos, até o teto de chunks; uma região fora dos chunks enviados é inalcançável para a B);
- a linha de base local (BM25/léxico, grátis): arquivo e região do gabarito.

Cobertura da etapa A: arquivos de `src/poetry/` no checkout contra as entradas do mapa que realmente vão ao provedor.

Só `python-poetry/poetry` no SHA fixado (mesmas travas do smoke: checkout íntegro, origem pública, nunca este repositório),
no máximo 6 perguntas e 12 chamadas, ZERO nova tentativa. `PRIVATE_CODE_SEND_APPROVED` segue False. Os ajustes de orçamento
abaixo valem SÓ nesta execução (configuração de teste); os padrões do produto não mudam. A chave vem do `EnvSettings`
(`--env` aponta o arquivo; este script nunca a lê, imprime ou grava).

    --analise   grátis, sem rede: cobertura da A, alcançabilidade e tamanho estimado do payload da B
    --run       as chamadas reais (precisa de --env)
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'backend'))

SMOKE = RAIZ / 'scripts' / 'context-retrieval-public-smoke.py'
ESCOPO = ('src/poetry/',)


def _smoke():
    espec = importlib.util.spec_from_file_location('smoke_publico', SMOKE)
    modulo = importlib.util.module_from_spec(espec)
    espec.loader.exec_module(modulo)
    return modulo


def sobrepoe(a: tuple[int, int], b: tuple[int, int]) -> int:
    """Linhas em comum entre dois intervalos fechados."""
    return max(0, min(a[1], b[1]) - max(a[0], b[0]) + 1)


def acerto_de_regiao(regioes: list[tuple[str, int, int]], esperadas: list[list]) -> dict:
    """Sobreposição com alguma região esperada e a fração das linhas esperadas que ficou coberta (união das regiões)."""
    esperado = sum(e[2] - e[1] + 1 for e in esperadas)
    coberto = 0
    for caminho, ini, fim in esperadas:
        linhas = set()
        for p, s, e in regioes:
            if p == caminho:
                linhas |= set(range(max(ini, s), min(fim, e) + 1))
        coberto += len(linhas)
    return {'hit': coberto > 0, 'fracao_coberta': round(coberto / esperado, 3) if esperado else None}


def linhas_do_arquivo(checkout: Path, caminho: str) -> int:
    try:
        return len((checkout / caminho).read_text(encoding='utf-8').splitlines())
    except OSError:
        return 0


def alcancavel(chunks: list[tuple[str, int, int]], esperadas: list[list]) -> bool:
    return any(sobrepoe((e[1], e[2]), (c[1], c[2])) > 0 and c[0] == e[0] for e in esperadas for c in chunks)


def cobertura_da_a(checkout: Path, limites) -> dict:
    """Quantos arquivos de `src/poetry/` o mapa tem e quantos vão ao provedor depois do corte em entradas e em bytes."""
    from app.modules.context_retrieval.infrastructure.repomap import RepoMapProvider
    from app.modules.context_retrieval.infrastructure.workspace import Workspace
    from app.modules.context_retrieval.domain.model import RepoMap

    ws = Workspace(checkout.resolve())
    mapa = RepoMapProvider(ws, cache_dir=None).repo_map()
    no_escopo = [e for e in mapa.entries if e.path.startswith(ESCOPO)]
    usados, enviadas = 0, 0
    for e in no_escopo:
        if enviadas >= limites.max_map_files:
            break
        tam = len(RepoMap(mapa.revision, (e,)).render().encode('utf-8')) + 1
        if usados + tam > limites.max_bytes:
            break
        usados += tam
        enviadas += 1
    tracked = subprocess.run(['git', 'ls-files', 'src/poetry'], cwd=checkout, capture_output=True, text=True,
                             check=True).stdout.split()
    py = [t for t in tracked if t.endswith('.py')]
    return {'arquivos_py_rastreados_em_src_poetry': len(py), 'entradas_do_mapa_no_escopo': len(no_escopo),
            'entradas_enviadas_a_etapa_a': enviadas, 'bytes_do_mapa_enviado': usados, 'teto_de_bytes': limites.max_bytes,
            'mapa_cortado_por_bytes': enviadas < len(no_escopo), 'revisao_do_mapa': mapa.revision}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--checkout', type=Path, required=True)
    ap.add_argument('--golden', type=Path, required=True)
    ap.add_argument('--analise', action='store_true', help='grátis, sem rede')
    ap.add_argument('--run', action='store_true', help='as chamadas reais')
    ap.add_argument('--env', default=None, help='arquivo .env que o EnvSettings carrega (não é lido por este script)')
    ap.add_argument('--max-calls', type=int, default=12)
    ap.add_argument('--max-candidate-files', type=int, default=None, help='só nesta execução (padrão do produto: 8)')
    ap.add_argument('--max-chunks', type=int, default=None, help='só nesta execução (padrão do produto: 24)')
    ap.add_argument('--max-input-tokens', type=int, default=None, help='só nesta execução (padrão do produto: 24000)')
    ap.add_argument('--out', type=Path, default=None)
    args = ap.parse_args()
    if not (args.analise or args.run):
        raise SystemExit('use --analise (grátis) ou --run (paga)')
    smoke = _smoke()
    smoke.verificar_checkout(args.checkout)
    if not 1 <= args.max_calls <= smoke.MAX_CHAMADAS:
        raise SystemExit(f'--max-calls deve ficar entre 1 e {smoke.MAX_CHAMADAS}')
    golden = json.loads(args.golden.read_text(encoding='utf-8'))
    perguntas = smoke.escolher(golden)                 # as mesmas 6 do smoke, pela regra fixa, sem olhar resultado

    from app.config import EnvSettings, load_config
    from app.modules.context_retrieval.domain.model import RetrievalMode
    from app.modules.context_retrieval.adapters.jev import JevSemanticProvider
    from app.modules.context_retrieval.infrastructure.chunker import Chunker
    from app.modules.context_retrieval.infrastructure.workspace import Workspace
    from app.modules.context_retrieval.wiring import ambiente_do_provedor, build_service
    import httpx

    env = EnvSettings(_env_file=args.env) if args.env else EnvSettings()
    cfg = load_config(env=env)
    c = cfg.file.context_retrieval
    c.enabled = True
    c.top_k = 5
    c.semantic.provider = 'jev'
    c.semantic.repository_class = 'public'
    c.semantic.allow_public = True
    c.semantic.max_calls = 2
    c.semantic.max_calls_per_session = args.max_calls
    c.semantic.max_cost_usd = 0.05
    c.semantic.max_map_files = 400
    if args.max_candidate_files is not None:
        c.semantic.max_candidate_files = args.max_candidate_files
    if args.max_chunks is not None:
        c.semantic.max_chunks = args.max_chunks
    if args.max_input_tokens is not None:
        c.semantic.max_input_tokens = args.max_input_tokens
    s = c.semantic
    from app.modules.context_retrieval.domain.model import PayloadLimits
    limites = PayloadLimits(max_map_files=s.max_map_files, max_candidate_files=s.max_candidate_files,
                            max_chunks=s.max_chunks, max_bytes=s.max_bytes)
    config_do_teste = {'max_candidate_files': s.max_candidate_files, 'max_chunks': s.max_chunks,
                       'max_bytes': s.max_bytes, 'max_input_tokens': s.max_input_tokens, 'max_calls': args.max_calls,
                       'padroes_do_produto': {'max_candidate_files': 8, 'max_chunks': 24, 'max_bytes': 48000,
                                              'max_input_tokens': 24000}}

    with tempfile.TemporaryDirectory() as dados:
        cfg.file.paths.data_dir = dados
        ws = Workspace(args.checkout.resolve())
        chunker = Chunker(ws)
        local = build_service(cfg, root=args.checkout, mode=RetrievalMode.LOCAL_ONLY)

        # ---- grátis: cobertura da A, alcançabilidade e a linha de base local (nenhuma rede)
        gratis: list[dict] = []
        for p in perguntas:
            esperadas = p['expected_regions']
            sozinho = chunker.chunks_for([e for e in p['expected_files']], max_chunks=s.max_chunks, max_bytes=s.max_bytes)
            loc = local.gather(p['question'], scope=ESCOPO)
            arq = [f.path for f in loc.files]
            reg = [(r.path, r.start_line, r.end_line) for r in loc.regions]
            top5_local = arq[:5]
            # payload que a B teria se os candidatos fossem os 5 primeiros do local (proxy, não são os da A)
            proxy = chunker.chunks_for(top5_local, max_chunks=s.max_chunks, max_bytes=s.max_bytes)
            gratis.append({
                'id': p['id'], 'group': p['group'],
                'esperada_alcancavel_se_o_arquivo_for_o_1o_candidato': alcancavel([(x.path, x.start_line, x.end_line) for x in sozinho], esperadas),
                'local_hit_at_3': smoke.esperado_em(arq, p['expected_files'], 3),
                'local_hit_at_5': smoke.esperado_em(arq, p['expected_files'], 5),
                'local_regiao': acerto_de_regiao(reg, esperadas), 'local_regioes': len(reg),
                'payload_b_proxy_tokens_estimados': sum(len(x.text.encode('utf-8')) for x in proxy) // 4})
        saida: dict = {'checkout_sha': smoke.SHA, 'config_do_teste': config_do_teste,
                       'cobertura_etapa_a': cobertura_da_a(args.checkout, limites), 'gratis': gratis}
        if not args.run:
            texto = json.dumps(saida, ensure_ascii=False, indent=1)
            if args.out:
                args.out.write_text(texto + '\n', encoding='utf-8')
            print(texto)
            return 0

        # ---- paga: uma execução por pergunta; o gravador separa o que a A e a B devolveram
        chamadas: list[dict] = []

        class Contador(httpx.BaseTransport):
            def __init__(self) -> None:
                self._real = httpx.HTTPTransport()

            def handle_request(self, request: httpx.Request) -> httpx.Response:
                if len(chamadas) >= args.max_calls:
                    raise httpx.ConnectError('limite de chamadas da ablação')
                t0 = time.perf_counter()
                resposta = self._real.handle_request(request)
                chamadas.append({'status': resposta.status_code, 'ms': round((time.perf_counter() - t0) * 1000, 1)})
                return resposta

        class Gravador:
            """Delega ao provedor real e guarda o que a A e a B devolveram e o que a B recebeu."""

            def __init__(self, real) -> None:
                self._r = real
                self.limpar()

            def limpar(self) -> None:
                self.a = self.b = self.chunks = self.uso_a = self.uso_b = None

            def __getattr__(self, nome):
                return getattr(self._r, nome)

            def select_files(self, query, mapa, **kw):
                r = self._r.select_files(query, mapa, **kw)
                self.a, self.uso_a = [c.path for c in r.choices], r.usage
                return r

            def select_regions(self, query, chunks, **kw):
                self.chunks = [(c.path, c.start_line, c.end_line) for c in chunks]
                r = self._r.select_regions(query, chunks, **kw)
                self.b, self.uso_b = [(c.path, c.start_line, c.end_line) for c in r.choices], r.usage
                return r

        real = JevSemanticProvider(model=s.model, env=ambiente_do_provedor(cfg), transport=Contador())
        grav = Gravador(real)
        servico = build_service(cfg, root=args.checkout, mode=RetrievalMode.HYBRID, provider=grav)
        linhas: list[dict] = []
        with servico.session():
            for p, g in zip(perguntas, gratis):
                antes, esperadas = len(chamadas), p['expected_regions']
                grav.limpar()
                pack = servico.gather(p['question'], scope=ESCOPO)
                assert pack is not None
                sem = pack.metadata.get('semantic', {})
                a = grav.a or []
                b = grav.b or []
                cands = a[:5]
                linhas_a_ler = sum(linhas_do_arquivo(args.checkout, f) for f in cands)
                linhas_b_ler = sum(e - i + 1 for _, i, e in b[:5])
                uso_a, uso_b = grav.uso_a, grav.uso_b
                linhas.append({
                    'id': p['id'], 'group': p['group'], 'http_calls': len(chamadas) - antes,
                    'http_statuses': [x['status'] for x in chamadas[antes:]], 'fallback_used': pack.metadata['fallback_used'],
                    'a': {'candidatos': a, 'hit_at_1': smoke.esperado_em(a, p['expected_files'], 1),
                          'hit_at_3': smoke.esperado_em(a, p['expected_files'], 3),
                          'hit_at_5': smoke.esperado_em(a, p['expected_files'], 5),
                          'linhas_que_o_consumidor_leria': linhas_a_ler,
                          'input_tokens': uso_a.input_tokens if uso_a else None,
                          'cost_usd': uso_a.cost_usd if uso_a else None, 'files_considered': sem.get('files_considered')},
                    'b': {'rodou': grav.b is not None, 'motivo_se_nao_rodou': sem.get('stage_b_reason'),
                          'chunks_enviados': len(grav.chunks or []),
                          'regiao_esperada_estava_nos_chunks': alcancavel(grav.chunks or [], esperadas) if grav.chunks else None,
                          'regioes': b, 'regiao': acerto_de_regiao(b[:5], esperadas) if b else None,
                          'linhas_que_o_consumidor_leria': linhas_b_ler if b else None,
                          'input_tokens': uso_b.input_tokens if uso_b else None,
                          'cost_usd': uso_b.cost_usd if uso_b else None},
                    'hibrido_final': {'hit_at_3': smoke.esperado_em([f.path for f in pack.files], p['expected_files'], 3),
                                      'regioes': len(pack.regions)},
                    'local': {k: v for k, v in g.items() if k.startswith('local_')}})
        saida.update({'RUN': 'EXECUTED', 'network_calls': len(chamadas), 'statuses': sorted({x['status'] for x in chamadas}),
                      'cost_usd_total': round(sum((l['a']['cost_usd'] or 0) + (l['b']['cost_usd'] or 0) for l in linhas), 6),
                      'cases': linhas})
    texto = json.dumps(saida, ensure_ascii=False, indent=1)
    if args.out:
        args.out.write_text(texto + '\n', encoding='utf-8')
    print(texto)
    return 0


if __name__ == '__main__':
    sys.exit(main())
