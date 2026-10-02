"""Esquecer uma conta no livro de aprendizado (item 29.23, frente Aprendizado): a conta bloqueada sai da plataforma
como se não existisse, e a persona continua.

O que faz: reescreve o rastro TEXTUAL da conta (o `@handle`, com e sem `@`, e o `account_id`) para o marcador
`[conta removida]` nas colunas de texto livre das tabelas `learning_*`. O que NÃO faz, de propósito:
- não apaga linha: a trilha de auditoria (transições, revisões, evidências) continua inteira, só sem a conta;
- não toca `content_hash`, `dossie_hash` nem coluna de id/chave (`profile_id`, `scope_*`, `instance_id`, `source_ref`,
  `cluster_key`...): o hash é a identidade do conteúdo (a unicidade do livro e o veto dependem dele) e continua
  valendo para o conteúdo que existia; uma chave reescrita quebraria o vínculo entre as tabelas;
- não toca receitas, fluxos, versões de habilidade nem `memory_items` (este é da frente Android do mesmo item).

Contrato com quem chama (interface fechada com a frente Android):
- roda DENTRO da transação do chamador: usa o mesmo `db`, não abre transação, não faz commit nem rollback e não faz
  I/O fora do banco. Se o chamador desfaz a transação, nada do que esta função fez fica;
- devolve `{tabela: linhas_alteradas}` para toda tabela varrida (inclusive 0), sem nenhum texto da conta — nem no
  retorno nem em log;
- idempotente: a segunda chamada devolve zeros.

Casamento seguro: o `LIKE` só PRÉ-FILTRA no banco (caro de errar nos dois dialetos: o SQLite ignora maiúscula, o
PostgreSQL não; por isso `LOWER` dos dois lados) e a troca é por regex em Python, com fronteira de palavra. Sem ela,
o handle `ana` viraria "b[conta removida]na" em "banana". O handle do Instagram tem letras, dígitos, `_` e `.`, então
`.`/`_`/dígito colados ao handle o prolongam (`ana.silva` e `ana_silva` NÃO são `ana`), e o `@` colado antes
(`foo@ana.com`) também; já o ponto de fim de frase ("segui a ana.") não prolonga nada, porque o Instagram não aceita
handle terminado em ponto.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from app.db import Database

MARCADOR = "[conta removida]"

#: Caractere de escape do `LIKE` (o `\` seria ambíguo entre o SQLite e o PostgreSQL com literais de texto).
_ESCAPE = "!"


@dataclass(frozen=True)
class _Tabela:
    nome: str
    colunas: tuple[str, ...]
    #: coluna -> colunas que, com ela, formam um índice único: a troca que a faria colidir é pulada (o rastro fica,
    #: mas a transação do chamador não quebra por um `IntegrityError` no meio do esquecimento).
    unicas: tuple[tuple[str, tuple[str, ...]], ...] = ()


#: As colunas de TEXTO LIVRE (JSON em texto conta como texto), conferidas nas migrações 055 e 069. Fora de propósito:
#: vocabulários fechados (`kind`, `state`, `stance`, `failure_kind`...), ids e chaves (ver o docstring do módulo),
#: e `learning_exposures`/`learning_daily`, que não têm texto livre.
_TABELAS: tuple[_Tabela, ...] = (
    _Tabela("learning_items", ("content", "summary", "provenance")),
    _Tabela("learning_evidence", ("detail", "origin_ref"),
            unicas=(("origin_ref", ("item_ref", "stance")),)),
    _Tabela("learning_transitions", ("reason",)),
    _Tabela("learning_backlog", ("title", "failure_screen", "notes", "baseline", "verification")),
    _Tabela("learning_reviews", ("dossie", "saida", "validade", "override_motivo", "resultado_posterior")),
    _Tabela("learning_signals", ("note", "data")),
)


def _escapar_like(texto: str) -> str:
    return (texto.replace(_ESCAPE, _ESCAPE * 2).replace("%", _ESCAPE + "%").replace("_", _ESCAPE + "_"))


def _agulhas_e_trocadores(handle: str, account_id: str) -> tuple[list[str], list[Callable[[str], str]]]:
    """As pistas para o `LIKE` (minúsculas) e as trocas por regex, uma por pista."""
    agulhas: list[str] = []
    trocas: list[Callable[[str], str]] = []
    nome = (handle or "").strip().lstrip("@").strip()
    if nome:
        agulhas.append(nome.lower())
        # `@` opcional na frente; nada de letra/dígito/`_`/`@` antes, nem `palavra.` (seria `x.ana`); depois, nada de
        # letra/dígito/`_` nem `.palavra` (seria `ana.silva`), mas o ponto final de frase passa.
        rx = re.compile(rf"(?<![\w@])(?<!\w\.)@?{re.escape(nome)}(?!\w|\.\w)", re.IGNORECASE)
        trocas.append(lambda t, rx=rx: rx.sub(lambda _m: MARCADOR, t))
    ident = (account_id or "").strip()
    if ident:
        agulhas.append(ident.lower())
        rx_id = re.compile(rf"(?<![\w-]){re.escape(ident)}(?![\w-])")
        trocas.append(lambda t, rx_id=rx_id: rx_id.sub(lambda _m: MARCADOR, t))
    return agulhas, trocas


def _reescrever(texto: str, trocas: list[Callable[[str], str]]) -> str:
    """Troca fora dos marcadores que já existem: um handle como `conta` casaria com o próprio `[conta removida]` e a
    segunda chamada o aninharia (idempotência)."""
    partes = texto.split(MARCADOR)
    for troca in trocas:
        partes = [troca(p) for p in partes]
    return MARCADOR.join(partes)


def esquecer_conta(db: Database, *, profile_id: str, account_id: str, handle: str, app_id: str) -> dict[str, int]:
    """Reescreve o rastro textual de `handle`/`account_id` nas tabelas `learning_*` (ver o docstring do módulo).

    `profile_id` e `app_id` fazem parte da assinatura combinada com a frente Android, mas NÃO restringem a varredura:
    o rastro de uma conta pode estar em item de qualquer escopo, e esquecer só no escopo da persona deixaria o resto.
    """
    _ = profile_id, app_id
    resultado = {t.nome: 0 for t in _TABELAS}
    agulhas, trocas = _agulhas_e_trocadores(handle, account_id)
    if not agulhas:
        return resultado
    for tabela in _TABELAS:
        resultado[tabela.nome] = _varrer(db, tabela, agulhas, trocas)
    return resultado


def _varrer(db: Database, tabela: _Tabela, agulhas: list[str], trocas: list[Callable[[str], str]]) -> int:
    extras = sorted({c for _, cols in tabela.unicas for c in cols})
    selecionadas = ["id", *tabela.colunas, *[c for c in extras if c not in tabela.colunas]]
    filtros: list[str] = []
    params: list[str] = []
    for coluna in tabela.colunas:
        for agulha in agulhas:
            filtros.append(f"LOWER({coluna}) LIKE ? ESCAPE '{_ESCAPE}'")
            params.append(f"%{_escapar_like(agulha)}%")
    linhas = db.query(f"SELECT {', '.join(selecionadas)} FROM {tabela.nome} WHERE {' OR '.join(filtros)}",
                      tuple(params))
    unicas = dict(tabela.unicas)
    alteradas = 0
    for linha in linhas:
        novos: dict[str, str] = {}
        for coluna in tabela.colunas:
            antigo = linha[coluna]
            if not isinstance(antigo, str) or not antigo:
                continue
            novo = _reescrever(antigo, trocas)
            if novo == antigo:
                continue
            chaves = unicas.get(coluna)
            if chaves and db.one(
                    f"SELECT 1 FROM {tabela.nome} WHERE id <> ? AND {coluna} = ? AND "
                    + " AND ".join(f"{c} = ?" for c in chaves),
                    (linha["id"], novo, *[linha[c] for c in chaves])):
                continue
            novos[coluna] = novo
        if novos:
            db.execute(f"UPDATE {tabela.nome} SET {', '.join(f'{c} = ?' for c in novos)} WHERE id = ?",
                       (*novos.values(), linha["id"]))
            alteradas += 1
    return alteradas
