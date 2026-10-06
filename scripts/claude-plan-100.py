#!/usr/bin/env python3
"""Livro-razão do plano-100: confere o mapa, registra o que foi feito e gera o relatório. Sem IA, sem subprocesso.

**O que mudou, e por quê.** Este arquivo já foi um executor: ele abria `claude -p` em subprocesso, um por bloco,
com sessão retomada e escalada automática de esforço. Nada disso funciona no aplicativo Claude Code, e não é um
detalhe de configuração:

- ele se recusa a rodar dentro do agente por desenho (`CLAUDECODE=1`), para não abrir sessão aninhada;
- não existe executável `claude` nesta máquina: o aplicativo não publica um CLI no PATH;
- e mesmo que existisse, `--resume` numa conversa só para 67 itens fica CARO em vez de barato — pela metade do
  plano, toda chamada carrega a conversa inteira das anteriores.

O executor agora é a própria sessão da IDE, pelo workflow `.claude/workflows/plano-100.js`:
um agente por grupo de arquivos, com o modelo e o esforço que o item merece. Ver `docs/claude-plano-100.md`.

    python scripts/plano-100-pacotes.py --fila --bloco 0-ajustes   # a fila que o workflow recebe
    python scripts/claude-plan-100.py check                        # mapa, pacotes e pendências
    python scripts/claude-plan-100.py aplicar resultado.json       # registra o que o workflow devolveu
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

RAIZ = Path(__file__).resolve().parents[1]
CONFIG = Path('.claude/plano-100.json')
INDICE = Path('.claude/plano-100/pacotes/indice.json')
ESTADO = Path('.claude/plano-100/estado.json')
RELATORIO = Path('docs/execucao-plano-100-runner.md')
ESTADOS = {'implemented', 'partial', 'blocked'}
PROVAS = {'real', 'simulated', 'not_run'}


class ErroDoPlano(Exception):
    pass


EXPLICACAO_DO_RUN = """`run` não existe mais, e não é um bug: o transporte antigo não funciona neste ambiente.

Ele abria `claude -p` em subprocesso. Aqui não há executável `claude` no PATH (o aplicativo Claude Code não
publica um), e o próprio executor se recusava a rodar de dentro do agente para não abrir sessão aninhada.

O executor agora é a sessão da IDE. Para rodar um bloco, peça ao Claude nesta sessão, ou faça à mão:

  1. python scripts/plano-100-pacotes.py --fila --bloco 0-ajustes   > fila.json
  2. no Claude: Workflow({name: 'plano-100', args: {bloco: '0-ajustes', fila: <conteúdo de fila.json>}})
  3. salve o retorno em resultado.json
  4. python scripts/claude-plan-100.py aplicar resultado.json

`check` mostra o que falta. Detalhes em docs/claude-plano-100.md."""


def ler_json(caminho: Path):
    return json.loads((RAIZ / caminho).read_text(encoding='utf-8'))


def gravar_json(caminho: Path, valor) -> None:
    destino = RAIZ / caminho
    destino.parent.mkdir(parents=True, exist_ok=True)
    temporario = destino.with_suffix(destino.suffix + '.tmp')
    temporario.write_text(json.dumps(valor, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    os.replace(temporario, destino)


def carregar() -> tuple[dict, dict, list[str]]:
    """Configuração, índice de pacotes e os IDs na ordem do plano — conferindo que os três concordam."""
    config = ler_json(CONFIG)
    plano = (RAIZ / config['plan']).read_text(encoding='utf-8')
    ids = re.findall(r'^\|\s*((?:\d+|T)\.\d+)\s*\|', plano, re.M)
    if len(ids) != len(set(ids)):
        raise ErroDoPlano('O plano tem ID repetido.')
    do_mapa = [item for lote in config['batches'] for item in lote['items']]
    if len(do_mapa) != len(set(do_mapa)) or set(do_mapa) != set(ids):
        sobra, falta = set(do_mapa) - set(ids), set(ids) - set(do_mapa)
        raise ErroDoPlano(f'O mapa de blocos não casa com o plano. Sobrando: {sorted(sobra) or "—"}; '
                          f'faltando: {sorted(falta) or "—"}.')
    if not (RAIZ / INDICE).is_file():
        raise ErroDoPlano('Pacotes ausentes. Rode: python scripts/plano-100-pacotes.py')
    indice = ler_json(INDICE)
    if set(indice) != set(ids):
        raise ErroDoPlano('Os pacotes estão velhos em relação ao plano. Rode o gerador de novo.')
    return config, indice, ids


def estado_atual() -> dict:
    if (RAIZ / ESTADO).is_file():
        return ler_json(ESTADO)
    return {'versao': 1, 'itens': {}, 'rodadas': []}


def validar(resultado: dict, ids_validos: set[str]) -> list[dict]:
    """O que o workflow devolveu só entra no livro se estiver completo. Um `implemented` sem evidência é
    exatamente o tipo de mentira que este plano existe para tirar do projeto."""
    grupos = resultado.get('resultados') if isinstance(resultado, dict) else None
    if not isinstance(grupos, list):
        raise ErroDoPlano('Esperava o objeto devolvido pelo workflow, com a lista `resultados`.')
    linhas = []
    for grupo in grupos:
        if grupo.get('erro'):
            print(f"  grupo {grupo.get('grupo', '?')} sem resultado: {grupo['erro']}", file=sys.stderr)
            continue
        # Um grupo que devolve menos linhas do que os IDs pedidos é o agente recusando ou se perdendo no escopo.
        # Aconteceu na primeira rodada real; registrar por cima disso esconderia o item para sempre.
        entregues = {item.get('id') for item in grupo.get('items', [])}
        ausentes = [i for i in grupo.get('solicitados', []) if i not in entregues]
        if ausentes:
            raise ErroDoPlano(f"O grupo {grupo.get('grupo', '?')} não trouxe linha para: {', '.join(ausentes)}.")
        conferencia = {c['id']: c for c in (grupo.get('conferencia') or {}).get('itens', [])}
        for item in grupo.get('items', []):
            if item.get('id') not in ids_validos:
                raise ErroDoPlano(f'ID desconhecido no resultado: {item.get("id")!r}')
            if item.get('status') not in ESTADOS or item.get('proof') not in PROVAS:
                raise ErroDoPlano(f'{item["id"]}: estado ou prova inválidos.')
            if item['status'] == 'implemented' and not (item.get('evidence') or '').strip():
                raise ErroDoPlano(f'{item["id"]}: implementado sem evidência.')
            if item['status'] == 'blocked' and not (item.get('blocker') or '').strip():
                raise ErroDoPlano(f'{item["id"]}: bloqueado sem motivo.')
            if item['proof'] != 'not_run' and not (item.get('evidence') or '').strip():
                raise ErroDoPlano(f'{item["id"]}: prova sem evidência.')
            checada = conferencia.get(item['id'])
            linhas.append({**item, 'grupo': grupo.get('grupo', ''), 'modelo': grupo.get('modelo', ''),
                           'esforco': grupo.get('esforco', ''),
                           'conferido': None if checada is None else bool(checada.get('confere')),
                           'conferencia': '' if checada is None else checada.get('motivo', '')})
    if not linhas:
        raise ErroDoPlano('O resultado não trouxe nenhum item aplicável.')
    return linhas


def aplicar(caminho: Path, indice: dict, ids: list[str]) -> int:
    resultado = json.loads(Path(caminho).read_text(encoding='utf-8'))
    linhas = validar(resultado, set(ids))
    estado = estado_atual()
    agora = datetime.now(timezone.utc).isoformat(timespec='seconds')
    for linha in linhas:
        estado['itens'][linha['id']] = {**linha, 'quando': agora}
    estado['rodadas'].append({'quando': agora, 'itens': [linha['id'] for linha in linhas],
                              'grupos': sorted({linha['grupo'] for linha in linhas})})
    gravar_json(ESTADO, estado)
    relatorio(indice, ids, estado)
    duvidosos = [linha['id'] for linha in linhas if linha['conferido'] is False]
    print(f'{len(linhas)} item(ns) registrado(s). Relatório: {RELATORIO}')
    print('Trello: rode .claude/trello/reconciliar.py para conferir os cartões dos três quadros com este estado (C-28).')
    if duvidosos:
        print('A conferência questionou: ' + ', '.join(duvidosos) + '. Olhe o diff antes de commitar.')
    return 2 if duvidosos else 0


def relatorio(indice: dict, ids: list[str], estado: dict) -> None:
    def celula(valor, teto: int = 0) -> str:
        texto = str(valor).replace('|', '\\|').replace('\n', ' ').strip()
        # A evidência de um item grande passa de mil caracteres. Numa célula de tabela isso deixa o relatório
        # ilegível justamente para quem precisa conferir; o texto inteiro fica em `.claude/plano-100/estado.json`.
        return texto if not teto or len(texto) <= teto else texto[:teto - 1].rstrip() + '…'

    feitos = sum(1 for i in ids if estado['itens'].get(i, {}).get('status') == 'implemented')
    linhas = ['# Execução do plano-100', '',
              f'{feitos} de {len(ids)} itens implementados. Gerado por `scripts/claude-plan-100.py` a partir do que',
              'o workflow devolveu; a prova dos aceites continua em `relatorio-validacao.md`.',
              '**Implementado não quer dizer aceite provado** — a coluna Prova é que diz isso.', '',
              '| Item | Estado | Prova | Modelo | Conferência | Evidência | Bloqueio |',
              '|---|---|---|---|---|---|---|']
    for item_id in ids:
        registro = estado['itens'].get(item_id, {})
        conferido = registro.get('conferido')
        linhas.append('| ' + ' | '.join((
            celula(item_id), celula(registro.get('status', 'pendente')), celula(registro.get('proof', '—')),
            celula(registro.get('modelo', '—')),
            celula('—' if conferido is None else ('ok' if conferido else '**questionada**')),
            celula(registro.get('evidence', ''), 220), celula(registro.get('blocker', ''), 160))) + ' |')
    pendentes = [i for i in ids if estado['itens'].get(i, {}).get('status') != 'implemented']
    linhas += ['', f'Pendentes ({len(pendentes)}): ' + (', '.join(pendentes) if pendentes else 'nenhum.'), '',
               'A evidência aparece resumida acima; o texto integral de cada item, com os testes que foram de fato',
               'executados, está em `.claude/plano-100/estado.json` (versionado; só `aplicar` escreve nele).', '']
    destino = RAIZ / RELATORIO
    temporario = destino.with_suffix('.tmp')
    temporario.write_text('\n'.join(linhas), encoding='utf-8')
    os.replace(temporario, destino)


def check(config: dict, indice: dict, ids: list[str]) -> int:
    estado = estado_atual()
    print(f"{len(ids)} itens no plano, {len(config['batches'])} blocos no mapa, pacotes gerados.")
    pendentes = [i for i in ids if estado['itens'].get(i, {}).get('status') != 'implemented']
    feitos = len(ids) - len(pendentes)
    print(f'Implementados: {feitos}. Pendentes: {len(pendentes)}.')
    por_modelo: dict[str, list[str]] = {}
    for item_id in pendentes:
        por_modelo.setdefault(indice[item_id]['modelo'], []).append(item_id)
    for modelo in sorted(por_modelo, key=lambda m: -len(por_modelo[m])):
        print(f'  {modelo:<8} {len(por_modelo[modelo]):>2} item(ns)')
    print('Executor: workflow `plano-100` nesta sessão. Este script não chama IA.')
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('acao', choices=('check', 'aplicar', 'relatorio', 'run'), nargs='?', default='check')
    parser.add_argument('arquivo', nargs='?', help='Resultado do workflow, para `aplicar`.')
    args = parser.parse_args(argv)
    if args.acao == 'run':
        # `run` era o comando principal. Quem vier da documentação antiga vai digitar isto; melhor explicar do que
        # devolver um erro de argumento e deixar a pessoa procurando.
        print(EXPLICACAO_DO_RUN, file=sys.stderr)
        return 1
    try:
        config, indice, ids = carregar()
        if args.acao == 'aplicar':
            if not args.arquivo:
                raise ErroDoPlano('Informe o arquivo com o resultado do workflow.')
            return aplicar(Path(args.arquivo), indice, ids)
        if args.acao == 'relatorio':
            relatorio(indice, ids, estado_atual())
            print('Relatório regravado: ' + str(RELATORIO))
            return 0
        return check(config, indice, ids)
    except (ErroDoPlano, OSError, ValueError, KeyError) as erro:
        print('Interrompido: ' + str(erro), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
