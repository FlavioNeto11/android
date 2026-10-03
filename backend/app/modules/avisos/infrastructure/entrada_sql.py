"""O registro da conversa pelos canais externos (migração 085, item 28.15), em SQLite e PostgreSQL.

Um repositório por canal (`canal='telegram'` hoje; o Trello do 32.2 abre o seu com `canal='trello'`), sobre as
MESMAS tabelas (`docs/design/canais-externos.md`, §6):

- `canal_entradas` é o dedupe, por `(canal, id_externo)`. No Telegram é também a fonte do offset: a update é gravada
  ANTES de o `getUpdates` seguinte confirmá-la (`offset = MAX(ordem) + 1`). Uma queda entre receber e gravar faz o
  Telegram reentregar, e a chave única faz a releitura cair no `ON CONFLICT DO NOTHING`. Linha `recebida` que sobrou
  de uma queda no meio do tratamento é tratada de novo na volta seguinte; a ação dela é idempotente (a chave
  `<canal>:<id_externo>` na criação, o estado na aprovação e na resposta).
- `canal_enviadas` é o que a Central mandou pelo canal. É o que liga o reply ao fato do aviso, e o que separa o reply
  à Central do reply à orquestradora.

As referências do canal são texto (o Telegram numera, o Trello não). Nada de chat_id aqui (a v1 aceita só o do
`.env`); o texto do que não veio do dono e o que parece credencial nem chegam a ser gravados (`texto` NULL).
"""
from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from app.db import Database
from app.util import parse_iso, to_iso

MAX_CURTO = 1000
MAX_ERRO = 300
#: A linha-marco da 1ª subida (B1): o `id_externo` não colide com um `update_id` (só dígitos).
INICIO = "inicio"


def _curto(texto: str | None, n: int = MAX_CURTO) -> str | None:
    return None if texto is None else texto.strip()[:n]


class EntradasDoCanal:
    def __init__(self, db: Database, *, canal: str = "telegram", relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.canal = canal
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora

    def _agora(self) -> str:
        return to_iso(self.relogio())

    # ------------------------------------------------------------------ entrada
    def proximo_offset(self) -> int:
        """O `offset` do próximo `getUpdates`: tudo abaixo dele já está gravado. Sem nenhuma `ordem` gravada: 0. Quem
        sobe com o canal vazio descarta antes o histórico do Telegram (`gravar_inicio`), então o 0 não o traz de volta."""
        ultimo = self.db.scalar("SELECT MAX(ordem) FROM canal_entradas WHERE canal=?", (self.canal,))
        return int(ultimo) + 1 if ultimo is not None else 0

    def canal_vazio(self) -> bool:
        """Nenhuma linha do canal: é a 1ª subida. O que o Telegram guardou até aqui (até 24 h) é histórico, não pedido."""
        return self.db.one("SELECT 1 AS x FROM canal_entradas WHERE canal=? LIMIT 1", (self.canal,)) is None

    def gravar_inicio(self, ultima_ordem: int | None) -> None:
        """Marca a 1ª subida e fixa o offset logo depois da última update descartada (`ultima_ordem`; `None` = fila
        vazia, e o offset segue 0). É a linha que tira o canal de "vazio": sem ela, a 1ª mensagem de verdade, chegando
        entre duas voltas com a fila ainda vazia, seria descartada como histórico."""
        self.gravar(id_externo=INICIO, ordem=ultima_ordem, tipo="outro", do_dono=False, ref_mensagem=None,
                    responde_a=None, texto=None, tamanho=0, estado="ignorada", erro="descartada na 1ª subida do canal")

    def gravar(self, *, id_externo: str, ordem: int | None, tipo: str, do_dono: bool, ref_mensagem: str | None,
               responde_a: str | None, texto: str | None, tamanho: int, estado: str = "recebida",
               erro: str | None = None) -> bool:
        """Grava o que chegou. Devolve se a linha é nova. `estado` final já na gravação para o que não se trata (não
        veio do dono, credencial): o texto destes nunca é gravado, então não há o que tratar depois."""
        agora = self._agora()
        tratada = None if estado == "recebida" else agora
        cur = self.db.execute(
            "INSERT INTO canal_entradas(canal, id_externo, ordem, tipo, do_dono, ref_mensagem, responde_a, texto,"
            " tamanho, estado, erro, recebida_em, tratada_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (canal, id_externo) DO NOTHING",
            (self.canal, id_externo, ordem, tipo, 1 if do_dono else 0, ref_mensagem, responde_a, texto, int(tamanho),
             estado, _curto(erro, MAX_ERRO), agora, tratada))
        return (cur.rowcount or 0) == 1

    def a_tratar(self, limite: int = 50) -> list[dict[str, object]]:
        """As linhas `recebida` do canal, na ordem em que chegaram (inclusive as que uma queda deixou no meio)."""
        return [dict(r) for r in self.db.query(
            "SELECT * FROM canal_entradas WHERE canal=? AND estado='recebida' ORDER BY id LIMIT ?",
            (self.canal, limite))]

    def id_de(self, id_externo: str) -> int | None:
        """O id da linha do que chegou com `id_externo` neste canal (a chave é única no canal)."""
        v = self.db.scalar("SELECT id FROM canal_entradas WHERE canal=? AND id_externo=?", (self.canal, id_externo))
        return int(v) if v is not None else None

    def linha(self, ident: int) -> dict[str, object] | None:
        r = self.db.one("SELECT * FROM canal_entradas WHERE id=? AND canal=?", (int(ident), self.canal))
        return dict(r) if r is not None else None

    def marcar(self, ident: int, estado: str, *, intencao: str | None = None, destino: str | None = None,
               alvo: str | None = None, previa: Mapping[str, object] | None = None, run_id: str | None = None,
               resposta: str | None = None, erro: str | None = None, de: tuple[str, ...] = ()) -> bool:
        """Muda o estado (e o que a ação deixou). `de`: só muda se o estado atual for um destes (o segundo toque no
        mesmo botão perde aqui). Campos `None` não apagam o que já estava gravado."""
        sets = ["estado=?", "tratada_em=?"]
        args: list[object] = [estado, self._agora()]
        for coluna, valor in (("intencao", intencao), ("destino", destino), ("alvo", alvo), ("run_id", run_id),
                              ("resposta", _curto(resposta)), ("erro", _curto(erro, MAX_ERRO)),
                              ("previa", json.dumps(previa, ensure_ascii=False) if previa is not None else None)):
            if valor is not None:
                sets.append(f"{coluna}=?")
                args.append(valor)
        sql = f"UPDATE canal_entradas SET {', '.join(sets)} WHERE id=? AND canal=?"  # colunas fixas acima
        args.extend((int(ident), self.canal))
        if de:
            sql += f" AND estado IN ({','.join('?' * len(de))})"
            args.extend(de)
        return (self.db.execute(sql, tuple(args)).rowcount or 0) == 1

    def idade_s(self, linha: Mapping[str, object]) -> float:
        """Há quantos segundos a linha mudou de estado pela última vez (`tratada_em`; sem ele, desde que chegou)."""
        desde = parse_iso(str(linha.get("tratada_em") or linha.get("recebida_em") or ""))
        return (self.relogio() - desde).total_seconds() if desde is not None else 0.0

    def presas_em_execucao(self, idade_s: float) -> list[dict[str, object]]:
        """Linhas que ficaram em `executando` sem `run_id`: a queda foi entre marcar o Executar e criar a execução. Nada
        as destrava sozinho (o botão já perdeu o `WHERE estado='pergunta'`)."""
        limite = to_iso(self.relogio() - timedelta(seconds=idade_s))
        return [dict(r) for r in self.db.query(
            "SELECT * FROM canal_entradas WHERE canal=? AND estado='executando' AND run_id IS NULL AND tratada_em < ?"
            " ORDER BY id LIMIT 20", (self.canal, limite))]

    def apagar_texto(self, ident: int) -> None:
        """Tira o texto de uma linha (a credencial que só foi reconhecida depois de gravada). Fica o `tamanho`."""
        self.db.execute("UPDATE canal_entradas SET texto=NULL, previa=NULL WHERE id=? AND canal=?", (int(ident), self.canal))

    def do_dono_na_janela(self, segundos: float, *, ate_id: int) -> int:
        """Mensagens do dono nos últimos `segundos`, até a linha `ate_id` inclusive (o limite de taxa). O lote inteiro
        é gravado antes de tratar: sem o `ate_id`, as primeiras de um lote grande seriam contadas com as que vieram
        depois delas."""
        desde = to_iso(self.relogio() - timedelta(seconds=segundos))
        return int(self.db.scalar(
            "SELECT COUNT(*) FROM canal_entradas WHERE canal=? AND do_dono=1 AND tipo='mensagem' AND recebida_em >= ?"
            " AND id <= ?", (self.canal, desde, int(ate_id))) or 0)

    # ------------------------------------------------------------------ o que a Central mandou
    def registrar_enviada(self, ref_mensagem: str | None, origem: str, *, fato: str | None = None,
                          entrada_id: int | None = None) -> None:
        if ref_mensagem is None:
            return
        self.db.execute(
            "INSERT INTO canal_enviadas(canal, ref_mensagem, origem, fato, entrada_id, enviada_em)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT (canal, ref_mensagem) DO NOTHING",
            (self.canal, ref_mensagem, origem, fato, entrada_id, self._agora()))

    def enviada(self, ref_mensagem: str) -> dict[str, object] | None:
        r = self.db.one("SELECT * FROM canal_enviadas WHERE canal=? AND ref_mensagem=?", (self.canal, ref_mensagem))
        return dict(r) if r is not None else None

    def da_pessoa(self, ref_mensagem: str) -> bool:
        """`ref_mensagem` é de uma mensagem que a PESSOA mandou (reply a ela não é reply ao bot)."""
        return self.db.one(
            "SELECT 1 AS x FROM canal_entradas WHERE canal=? AND ref_mensagem=? AND tipo='mensagem' AND do_dono=1",
            (self.canal, ref_mensagem)) is not None

    def ajuda_ja_enviada(self) -> bool:
        return self.db.one("SELECT 1 AS x FROM canal_enviadas WHERE canal=? AND origem='ajuda' LIMIT 1",
                           (self.canal,)) is not None

    # ------------------------------------------------------------------ desfecho na conversa
    def esperando_desfecho(self, limite: int = 20) -> list[dict[str, object]]:
        return [dict(r) for r in self.db.query(
            "SELECT id, ref_mensagem, run_id FROM canal_entradas WHERE canal=? AND run_id IS NOT NULL"
            " AND resultado_em IS NULL AND estado='feita' ORDER BY id LIMIT ?", (self.canal, limite))]

    def marcar_desfecho(self, ident: int) -> None:
        self.db.execute("UPDATE canal_entradas SET resultado_em=? WHERE id=? AND canal=?",
                        (self._agora(), int(ident), self.canal))

    # ------------------------------------------------------------------ leitura (saúde e orquestradora)
    def contagens(self) -> dict[str, int]:
        linhas = self.db.query("SELECT estado, COUNT(*) AS n FROM canal_entradas WHERE canal=? GROUP BY estado",
                               (self.canal,))
        return {str(r["estado"]): int(r["n"]) for r in linhas}
