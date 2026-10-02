#!/usr/bin/env python3
"""Monta um pacote de trabalho auto-contido por item do plano. Determinístico: nenhuma chamada de IA.

Existe por causa de uma conta. Quem for implementar o item 1.3 precisa da linha do plano, dos achados que a
justificam e dos arquivos citados nas evidências — cerca de 4 KB. Sem isto, precisaria abrir `docs/plano-100.md`
(34 KB) e caçar os achados dentro de `docs/auditoria-2026-09-21/` (467 KB). Repetido pelos 67 itens, é a diferença
entre ~70 mil e ~1,5 milhão de tokens só para se orientar — antes de ler uma linha do código que vai mudar.

    python scripts/plano-100-pacotes.py            # gera .claude/plano-100/pacotes/
    python scripts/plano-100-pacotes.py --conferir # só confere que plano e apêndice continuam casando
    python scripts/plano-100-pacotes.py --contexto # EXPERIMENTAL, opt-in: acrescenta sugestões do retrieval de contexto

`--contexto` consulta `backend/app/modules/context_retrieval` (ADR-063) pela API Python, com um único serviço para todos os
itens (índice BM25 em disco por revisão, nada reconstruído por item), e só escreve algo quando o retrieval está
ligado na configuração (`context_retrieval.enabled`) ou quando `--contexto-modo` o pede de forma explícita. Sem a flag
a saída é byte a byte a de sempre; com o retrieval desligado, também. As sugestões dependem da revisão do código e por
isso NÃO se commitam os pacotes gerados com `--contexto`.

Por padrão a consulta é restrita ao CÓDIGO (`backend/app/`, `frontend/src/`, `scripts/`): sem o escopo a documentação ocupa o topo (a medição
local de 02/10 foi de 17,5% para 72,5% de acerto no top 3 só por restringir). `--contexto-escopo PREFIXO` (repetível) troca o padrão e
`--contexto-sem-escopo` consulta o repositório inteiro. O padrão vale SÓ para este consumidor; o serviço e a API não mudam.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import sys
import time

RAIZ = Path(__file__).resolve().parents[1]
PLANO = Path('docs/plano-100.md')
APENDICE = Path('docs/auditoria-2026-09-21')
DESTINO = Path('.claude/plano-100/pacotes')

#: O tamanho é P/M/G, ou `—` quando a linha não é trabalho meu: é uma decisão reservada ao dono.
ITEM = re.compile(r'^\|\s*((?:\d+|T)\.\d+)\s*\|\s*(.+?)\s*\|\s*([^|]*?)\s*\|\s*([PMG]|—)\s*\|\s*$', re.M)
FASE = re.compile(r'^### (Fase \d+|Transversal) — (.+?)(?: ·|$)', re.M)
FECHA = re.compile(r'^\*\*Fecha quando[^:]*:\*\*\s*(.+?)(?=\n\n|\n###|\n---)', re.M | re.S)
ACHADO = re.compile(r'^## #(\d+) — (.+?)$', re.M)
TITULO = re.compile(r'^\*\*(.+?)\.?\*\*\s*(.*)$', re.S)
#: Caminho de arquivo do projeto citado numa evidência, com ou sem `C:/git/android/` na frente e com ou sem `:linha`.
CAMINHO = re.compile(r'(?:[A-Za-z]:[\\/]git[\\/]android[\\/])?'
                     r'((?:backend|frontend|scripts|docs|config|qa-app|tools)[\w./\\-]*?'
                     r'\.(?:py|ts|tsx|js|sql|md|yaml|yml|ps1|json|java|css))(?::\d+)?')

#: Itens que mexem em concorrência, protocolo, segurança ou esquema de banco. Erro aqui não aparece em teste feliz;
#: aparece em produção, em corrida, meses depois. É onde o modelo caro se paga.
DELICADOS = {'0.1', '0.2', '0.3', '1.1', '1.3', '1.4', '1.5', '1.6', '1.7', '2.1', '2.2',
             '3.1', '3.2', '4.2', '4.4', '5.1', '5.2', '5.3', '5.4', '5.5', '5.6', '6.1', '6.2', '9.1', '9.4'}
#: Itens que são texto, tabela ou configuração: o trabalho é redigir com cuidado, não projetar.
REDACAO = {'0.10', '10.2', '10.3', 'T.1', 'T.3', '8.1'}
#: Itens cujo trabalho encosta no mundo real: reiniciar produção, mexer no parque vivo, gastar com o provedor,
#: alterar configuração da máquina. O código e os testes seguem sem autorização; o ATO, não. Sem este aviso no
#: pacote, um agente diligente reinicia a produção achando que está cumprindo o item.
AUTORIZACAO = {
    '0.1': 'reiniciar a produção e migrar o banco real',
    '0.7': 'reiniciar emuladores do parque vivo e alterar o relógio das máquinas',
    '6.6': 'instalar o conjunto real do Instagram num aparelho do worker',
    '7.4': 'chamadas pagas ao provedor de IA (bateria de avaliação)',
    '8.4': 'operar contas reais do Instagram em aparelho remoto',
    '10.3': 'alterar o teto de memória do WSL desta máquina',
    'T.1': 'executar as provas de aceite em infraestrutura real',
    # Terceira evolução (Fases 23–27, item 23.1): os itens [A] do plano, com o ato real que cada um exige.
    '23.2': ('instalar o Outlook pela Play Store no aparelho-loja (login Google e "Instalar" são do dono), rodar o '
             'canário num aparelho de QA e promover a versão'),
    '23.7': 'observar as telas do Outlook no canário com a pessoa no Foco (sem digitar)',
    '23.11': ('cadastrar as contas Outlook reais com o endereço conferido pelo dono, vincular, clonar a credencial no '
              'cofre e registrar o consentimento'),
    '23.12': 'distribuir o Outlook aos aparelhos do parque, locais e remotos, em lotes',
    '23.13': ('login e leitura da caixa de entrada em conta real do Outlook, só em aparelho com a rede validada; '
              'chamada paga de IA pontual'),
    '24.9': 'chamada paga de IA e login/leitura em conta real (comando Outlook → Instagram só de leitura)',
    '25.1': 'instalar e medir o cliente VPN (WireGuard e sing-box) num aparelho de QA do parque',
    '25.7': 'aplicar a rede nos aparelhos do worker pelo túnel, sem mudar a rota do host nem o túnel',
    '25.9': 'trocar a rede de aparelhos do parque; conta real só com autorização por aparelho',
    '25.10': 'adquirir o cliente VPN pela Play Store com a conta do dono, promover e distribuir ao parque',
    '27.2': ('chamada paga de IA e login/leitura em conta real (Outlook e Instagram no mesmo aparelho, rede '
             'trafego_verificado)'),
    '28.12': 'prova real de um pedido recorrente no central, com chamada paga pontual de IA',
    # Pendências da terceira evolução (Fase 29): o ato que cada item [A] exige e quem o faz.
    '29.7': ('contratar os dois servidores do piloto e gerar as chaves dos clientes (dono); atribuir a rede em dois '
             'aparelhos de QA sem conta real'),
    '29.9': ('criar a regra do Firewall do Windows no central num PowerShell de administrador e a reserva DHCP no '
             'roteador (dono); a plataforma só lê o firewall'),
    '29.12': ('promover e distribuir o Outlook ao parque; "Atualizar" na Play Store do aparelho-loja, se for preciso, '
              'é do dono'),
    '29.13': ('consentimento por conta Outlook (dono), login e leitura em conta real, chamada paga de IA e o e-mail de '
              'teste enviado pelo dono'),
    '29.14': 'ligar o Docker/WSL no central para a suíte em PostgreSQL, ou o job do CI depois do limite de gasto',
    '29.19': ('decidir a escala e contratar os IPv4 (dono); trocar a saída de aparelho com conta real só com '
              'autorização por aparelho'),
}
#: Teto de itens por agente. Não é estética: um agente com oito itens e vinte arquivos perde o fio, e quando erra
#: leva junto tudo o que já tinha feito. Pedaços do mesmo grupo correm em SEQUÊNCIA, então não há conflito.
ITENS_POR_AGENTE = 3


def ler(caminho: Path) -> str:
    return (RAIZ / caminho).read_text(encoding='utf-8')


def achados_do_apendice() -> dict[str, dict]:
    """`#n` -> {titulo, tema, corpo}. O apêndice é a fonte; o plano só aponta para ele."""
    tudo: dict[str, dict] = {}
    for arquivo in sorted((RAIZ / APENDICE).glob('*.md')):
        if arquivo.name == 'README.md':
            continue
        texto = arquivo.read_text(encoding='utf-8')
        marcas = list(ACHADO.finditer(texto))
        for atual, seguinte in zip(marcas, marcas[1:] + [None]):
            fim = seguinte.start() if seguinte else len(texto)
            tudo[atual.group(1)] = {'titulo': atual.group(2).strip(), 'tema': arquivo.stem,
                                    'corpo': texto[atual.end():fim].strip()}
    return tudo


def fases_do_plano(plano: str) -> list[tuple[int, str, str]]:
    """(posição, nome, aceite) por fase, na ordem do documento."""
    saida = []
    marcas = list(FASE.finditer(plano))
    for atual, seguinte in zip(marcas, marcas[1:] + [None]):
        fim = seguinte.start() if seguinte else len(plano)
        trecho = plano[atual.start():fim]
        fecha = FECHA.search(trecho)
        saida.append((atual.start(), f'{atual.group(1)} — {atual.group(2)}'.strip(),
                      ' '.join(fecha.group(1).split()) if fecha else ''))
    return saida


def sugerir(item_id: str, tamanho: str, corpo: str) -> tuple[str, str]:
    """(modelo, esforço). Um único modelo para 67 itens é desperdício num sentido e risco no outro.

    `decisao` não é um modelo: é a marca de que a linha pede uma escolha do dono (chave, orçamento, janela de
    produção). Nenhum agente deve "implementar" isso — quem tentar vai inventar uma autorização que não existe.
    """
    if tamanho == '—':
        return 'decisao', 'nenhum'
    if item_id in DELICADOS or tamanho == 'G':
        return 'opus', 'xhigh'
    if item_id in REDACAO:
        return 'haiku' if tamanho == 'P' else 'sonnet', 'medium'
    return 'sonnet', 'high' if tamanho == 'M' else 'medium'


def montar() -> tuple[list[dict], dict[str, dict]]:
    plano = ler(PLANO)
    apendice = achados_do_apendice()
    fases = fases_do_plano(plano)
    itens, faltando = [], set()
    for achado_no_plano in ITEM.finditer(plano):
        item_id, corpo, referencias, tamanho = achado_no_plano.groups()
        nome_fase, aceite = next(((n, a) for p, n, a in reversed(fases) if p < achado_no_plano.start()), ('', ''))
        refs = re.findall(r'#(\d+)', referencias)
        faltando |= {r for r in refs if r not in apendice}
        titulo = TITULO.match(corpo)
        arquivos = []
        for texto in [corpo] + [apendice[r]['corpo'] for r in refs if r in apendice]:
            # Fora dependências de terceiros: uma evidência que cita `uvicorn/config.py` está explicando um
            # comportamento, não pedindo que alguém edite um pacote instalado — e o palpite não pode sugerir isso.
            arquivos += [c.replace('\\', '/') for c in CAMINHO.findall(texto)
                         if '.venv/' not in c.replace('\\', '/') and 'node_modules/' not in c.replace('\\', '/')]
        modelo, esforco = sugerir(item_id, tamanho, corpo)
        itens.append({'id': item_id, 'titulo': (titulo.group(1) if titulo else corpo)[:110],
                      'fase': nome_fase, 'aceite': aceite, 'tamanho': tamanho, 'corpo': corpo,
                      'achados': refs, 'arquivos': sorted(dict.fromkeys(arquivos)),
                      'modelo': modelo, 'esforco': esforco})
    if faltando:
        raise SystemExit('Achados citados no plano e ausentes do apêndice: ' + ', '.join(sorted(faltando)))
    return itens, apendice


def escrever(itens: list[dict], apendice: dict[str, dict], sugerir_contexto=None) -> None:
    destino = RAIZ / DESTINO
    destino.mkdir(parents=True, exist_ok=True)
    for antigo in destino.glob('*.md'):
        antigo.unlink()
    for item in itens:
        linhas = [f"# Item {item['id']} — {item['titulo']}", '',
                  f"**{item['fase']}** · tamanho no plano: {item['tamanho']} · "
                  f"achados: {', '.join('#' + r for r in item['achados']) or '—'}", '']
        if item['id'] in AUTORIZACAO:
            linhas += [f"> **Parte deste item exige autorização do dono: {AUTORIZACAO[item['id']]}.** Implemente o",
                       '> código, os testes e o procedimento; deixe a ação real pronta para ser executada e marque a',
                       '> prova como `not_run`, dizendo qual autorização falta. Não execute o ato por conta própria.', '']
        if item['modelo'] == 'decisao':
            linhas += ['> **Esta linha é uma decisão do dono, não trabalho de implementação.** Nenhum agente a',
                       '> executa. O estado correto dela é `blocked`, com o motivo apontando a decisão que falta.', '']
        linhas += [
                  'Este pacote é auto-contido: ele já traz a linha do plano e o texto integral dos achados que a',
                  'justificam. **Não abra `docs/plano-100.md` nem o apêndice** para trabalhar neste item — abra o',
                  'código. A auditoria descreve o commit `f1e61b3`: confira cada ponto no código de hoje antes de',
                  'mexer, e se já estiver resolvido registre a evidência e siga.', '',
                  '## O trabalho', '', item['corpo'], '']
        if item['aceite']:
            linhas += ['## Como a fase fecha (contexto, não é o escopo deste item)', '', item['aceite'], '']
        if item['arquivos']:
            linhas += ['## Arquivos citados nas evidências', '',
                       *(f'- `{caminho}`' for caminho in item['arquivos']), '']
        if sugerir_contexto is not None:
            sugestoes = sugerir_contexto(item)
            if sugestoes:
                linhas += ['## Sugestões de contexto (retrieval local; palpite, não é evidência)', '', *sugestoes, '']
        if item['achados']:
            linhas += ['## Achados, na íntegra', '']
            for referencia in item['achados']:
                achado = apendice[referencia]
                linhas += [f"### #{referencia} — {achado['titulo']}", '', achado['corpo'], '']
        gravar(destino / f"{item['id']}.md", '\n'.join(linhas))

    indice = {item['id']: {chave: item[chave] for chave in
                           ('titulo', 'fase', 'tamanho', 'achados', 'arquivos', 'modelo', 'esforco')}
              for item in itens}
    for item_id, dados in indice.items():
        dados['pacote'] = str(DESTINO / f'{item_id}.md').replace('\\', '/')
        dados['bytes'] = (destino / f'{item_id}.md').stat().st_size
    gravar(destino / 'indice.json', json.dumps(indice, ensure_ascii=False, indent=1) + '\n')


#: Escopo padrão das sugestões: onde mora o código que um item do plano muda. Igual ao `ESCOPO_CODIGO` de `context-retrieval-local-eval.py`.
ESCOPO_DO_CONTEXTO = ('backend/app/', 'frontend/src/', 'scripts/')


class SugestorDeContexto:
    """`item -> linhas` pela API Python do retrieval de contexto, com UM serviço para o lote inteiro. Nunca levanta.

    O serviço (e com ele o `Workspace`, a revisão, o índice BM25, o mapa, o cache e o orçamento de sessão) é montado uma vez,
    na primeira consulta, e reaproveitado em todos os itens; `sessao()` congela a revisão enquanto o lote roda. Sem
    subprocesso por item. Falha ao importar o backend, retrieval desligado e erro na consulta dão a MESMA resposta:
    nenhuma linha; o retrieval é auxílio do pacote, não requisito dele.
    """

    def __init__(self, modo: str | None = None, *, escopo: tuple[str, ...] = ESCOPO_DO_CONTEXTO, servico=None,
                 fabrica=None) -> None:
        self.modo = modo
        self.escopo = tuple(escopo)
        self._servico = servico
        self._fabrica = fabrica or self._fabrica_real
        self._pronto = servico is not None
        self._avisou = False
        self.tempos_ms: list[float] = []

    def _montar(self):
        if self._pronto:
            return self._servico
        self._pronto = True
        try:
            self._servico = self._fabrica()
        except Exception as erro:  # noqa: BLE001 - falta de dependência do backend não derruba a geração
            self._servico = None
            self._avisar(f'o retrieval de contexto não carregou ({type(erro).__name__})')
        return self._servico

    def _fabrica_real(self):
        backend = str(RAIZ / 'backend')
        if backend not in sys.path:
            sys.path.insert(0, backend)
        from app.config import load_config
        from app.modules.context_retrieval.domain.model import RetrievalMode
        from app.modules.context_retrieval.wiring import build_service
        return build_service(load_config(), mode=RetrievalMode(self.modo) if self.modo else None)

    def _avisar(self, motivo: str) -> None:
        if not self._avisou:
            print(f'Aviso: {motivo}; os pacotes saem sem sugestões.', file=sys.stderr)
            self._avisou = True

    def sessao(self):
        servico = self._montar()
        return servico.session() if servico is not None else contextlib.nullcontext()

    def __call__(self, item: dict) -> list[str]:
        servico = self._montar()
        if servico is None or not getattr(servico, 'enabled', False):
            return []
        pergunta = f"{item['titulo']}. {item['corpo']}"[:600]
        inicio = time.perf_counter()
        try:
            pack = servico.gather(pergunta, scope=self.escopo)
        except Exception as erro:  # noqa: BLE001
            self._avisar(f'a consulta ao retrieval falhou ({type(erro).__name__})')
            return []
        self.tempos_ms.append((time.perf_counter() - inicio) * 1000)
        if pack is None:
            return []
        linhas = [f'- `{f.path}`' for f in pack.files]
        linhas += [f'- `{r.path}:{r.start_line}-{r.end_line}`' for r in pack.regions]
        origem = f'origem {pack.origin}, revisão {pack.revision[:12]}'
        return [f'_{origem}_', ''] + linhas if linhas else []


def escopo_do_contexto(args) -> tuple[str, ...]:
    """`--contexto-sem-escopo` = repositório inteiro; `--contexto-escopo` (repetível) = os prefixos pedidos; senão o padrão de código."""
    if args.contexto_sem_escopo:
        return ()
    return tuple(args.contexto_escopo or ESCOPO_DO_CONTEXTO)


def gravar(caminho: Path, conteudo: str) -> None:
    temporario = caminho.with_suffix(caminho.suffix + '.tmp')
    temporario.write_text(conteudo, encoding='utf-8')
    os.replace(temporario, caminho)


ORDEM_MODELO = {'decisao': 0, 'haiku': 1, 'sonnet': 2, 'opus': 3}
ORDEM_ESFORCO = {'nenhum': 0, 'medium': 1, 'high': 2, 'xhigh': 3}
CONFIG = Path('.claude/plano-100.json')


def fila(indice: dict[str, dict], bloco: str | None = None) -> list[dict]:
    """Blocos do mapa, cada um repartido em GRUPOS de itens que compartilham arquivo.

    O agrupamento resolve dois problemas com uma conta só. Itens que tocam o mesmo arquivo num mesmo agente: o
    arquivo é lido uma vez, não uma vez por item. E grupos com conjuntos de arquivos disjuntos podem correr em
    paralelo sem que dois agentes editem o mesmo arquivo — que é o jeito conhecido de perder trabalho.

    O modelo e o esforço do grupo são os do item mais exigente dele: dentro de um grupo não dá para variar.
    """
    config = json.loads(ler(CONFIG))
    forcado = config.get('modelos', {})
    saida = []
    for lote in config['batches']:
        if bloco and lote['id'] != bloco:
            continue
        itens = [i for i in lote['items'] if i in indice]
        pai = {i: i for i in itens}

        def raiz(x: str) -> str:
            while pai[x] != x:
                pai[x] = pai[pai[x]]
                x = pai[x]
            return x

        for a in itens:
            for b in itens:
                if a < b and set(indice[a]['arquivos']) & set(indice[b]['arquivos']):
                    pai[raiz(a)] = raiz(b)
        grupos: dict[str, list[str]] = {}
        for i in itens:
            grupos.setdefault(raiz(i), []).append(i)
        for chave, membros in grupos.items():
            membros.sort(key=lambda i: [int(p) if p.isdigit() else p for p in i.split('.')])
            arquivos = sorted({a for i in membros for a in indice[i]['arquivos']})
            partes = [membros[n:n + ITENS_POR_AGENTE] for n in range(0, len(membros), ITENS_POR_AGENTE)]
            for passo, parte in enumerate(partes):
                modelos = [forcado.get(i, indice[i]['modelo']) for i in parte]
                saida.append({
                    'bloco': lote['id'], 'componente': f"{lote['id']}:{membros[0]}", 'passo': passo,
                    'grupo': f"{lote['id']}:{parte[0]}" + (f'+{len(parte) - 1}' if len(parte) > 1 else ''),
                    'modelo': max(modelos, key=lambda m: ORDEM_MODELO[m]),
                    'esforco': max((indice[i]['esforco'] for i in parte), key=lambda e: ORDEM_ESFORCO[e]),
                    # Arquivos do COMPONENTE inteiro: é o conjunto que não pode ser tocado por outro agente em
                    # paralelo, mesmo que este pedaço só mexa em parte dele.
                    'arquivos': arquivos,
                    'autorizacao': {i: AUTORIZACAO[i] for i in parte if i in AUTORIZACAO},
                    'itens': [{'id': i, 'titulo': indice[i]['titulo'], 'pacote': indice[i]['pacote'],
                               'modelo': forcado.get(i, indice[i]['modelo'])} for i in parte]})
    return saida


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--conferir', action='store_true', help='Só conferir plano x apêndice, sem gravar nada.')
    parser.add_argument('--fila', action='store_true', help='Imprimir a fila de execução (JSON) em vez do resumo.')
    parser.add_argument('--bloco', help='Limitar a fila a um bloco do mapa.')
    parser.add_argument('--contexto', action='store_true',
                        help='EXPERIMENTAL: acrescenta sugestões do retrieval de contexto (só se estiver ligado).')
    parser.add_argument('--contexto-escopo', action='append', metavar='PREFIXO',
                        help='Prefixo de caminho das sugestões (repetível); troca o escopo padrão de código.')
    parser.add_argument('--contexto-sem-escopo', action='store_true',
                        help='Sugestões no repositório inteiro, sem restringir ao código.')
    parser.add_argument('--contexto-modo', choices=['local_only', 'shadow', 'hybrid'],
                        help='Pede um modo do retrieval explicitamente, mesmo com ele desligado na configuração.')
    args = parser.parse_args(argv)
    itens, apendice = montar()
    # `--fila` só gera se ainda não houver pacote. Regenerar apaga e reescreve o diretório inteiro, e pedir a fila
    # de um bloco enquanto agentes de outro estão lendo os pacotes deles tiraria os arquivos debaixo deles.
    if not args.conferir and not (args.fila and (RAIZ / DESTINO / 'indice.json').is_file()):
        escopo = escopo_do_contexto(args)
        sugestor = (SugestorDeContexto(args.contexto_modo, escopo=escopo)
                    if (args.contexto or args.contexto_modo) else None)
        if sugestor is None:
            escrever(itens, apendice)
        else:
            with sugestor.sessao():
                escrever(itens, apendice, sugestor)
    if args.fila:
        indice = json.loads((RAIZ / DESTINO / 'indice.json').read_text(encoding='utf-8'))
        print(json.dumps(fila(indice, args.bloco), ensure_ascii=False, indent=1))
        return 0

    total = sum((RAIZ / DESTINO / f"{i['id']}.md").stat().st_size for i in itens) if not args.conferir else 0
    plano_bytes = len(ler(PLANO).encode('utf-8'))
    apendice_bytes = sum(a.stat().st_size for a in (RAIZ / APENDICE).glob('*.md'))
    print(f'{len(itens)} itens, {len(apendice)} achados no apêndice.')
    if args.conferir:
        print('Plano e apêndice casam: todo #n citado existe.')
        return 0
    maior = max(itens, key=lambda i: (RAIZ / DESTINO / f"{i['id']}.md").stat().st_size)
    print(f'Pacotes: {total // 1024} KB no total, média {total // len(itens)} B, '
          f"maior {item_kb(maior)} ({maior['id']}).")
    print(f'Sem pacote, cada item precisaria de {(plano_bytes + apendice_bytes) // 1024} KB '
          f'(plano + apêndice) para se orientar; com pacote, {total // len(itens) // 1024 or 1} KB.')
    por_modelo: dict[str, int] = {}
    for item in itens:
        por_modelo[item['modelo']] = por_modelo.get(item['modelo'], 0) + 1
    print('Modelo sugerido: ' + ', '.join(f'{m} {n}' for m, n in sorted(por_modelo.items())))
    return 0


def item_kb(item: dict) -> str:
    return f"{(RAIZ / DESTINO / f'{item['id']}.md').stat().st_size // 1024} KB"


if __name__ == '__main__':
    sys.exit(main())
