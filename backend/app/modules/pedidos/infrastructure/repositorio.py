"""SQL das três tabelas do pedido (067): pedido, gatilho e ocorrência (docs/design/pedidos-laco.md §1, §4).

Só consultas e escritas pontuais; as REGRAS (janela, sobreposição, fechamento, transições) ficam no domínio, e quem
decide chama `transicionar_*` ANTES de escrever aqui. Cada escrita de estado é um CAS (`WHERE estado = <de>`): zero
linhas afetadas é o sinal de que outro laço ou outro gesto chegou primeiro, e quem chama trata como "já feito".

**Duas escalas de instante, nunca misturadas (A5).** `previsto_para` e `proxima_em` são `formatar_instante`
(`2026-10-02T12:00:00Z`, segundo cheio) e só se comparam com `formatar_instante(agora)`. O resto do esquema (prazos,
`criada_em`, `terminada_em`, `prazo_posse`) é `to_iso` (`…:00.123Z`). Como TEXTO, dentro do mesmo segundo `Z` é maior
que `.`: `"…:00Z" <= "…:00.500Z"` é falso.

Este módulo não abre transação: quem chama envolve o que precisa ser atômico em `Lideranca.cercada` (o laço) ou
`db.tx()` (as ações da pessoa). Compatível com SQLite e PostgreSQL (`ON CONFLICT DO NOTHING` sem alvo cobre a chave e
o `UNIQUE (pedido_id, gatilho_id, previsto_para)` de uma vez).
"""
from __future__ import annotations

import secrets
import threading
from collections.abc import Sequence
from datetime import datetime, timedelta

from app.db import Database, Row
from app.modules.pedidos.domain.materializar import truncar
from app.util import parse_iso

ABERTAS = ("despachada", "rodando")                       # a execução existe e ainda não fechou
EM_ABERTO = ("prevista", "devida", "despachada", "rodando")


def novo_id(prefixo: str) -> str:
    """Id curto e gerado pelo sistema, no alfabeto da chave (`chave.py`: até 28 caracteres de `[A-Za-z0-9_-]`)."""
    return f"{prefixo}{secrets.token_hex(8)}"


def _marcas(valores: Sequence[object]) -> str:
    return ",".join("?" for _ in valores)


class RepositorioDePedidos:
    def __init__(self, db: Database):
        self.db = db
        self._local = threading.local()

    # ------------------------------------------------------------------ marcas de mudança (eventos pedido.*)
    def marcar(self, tipo: str, ident: str, *, pessoa: bool = False) -> None:
        """Anota que o pedido ou a ocorrência mudou, para quem chamar `descarregar` DEPOIS do commit publicar o evento
        (28.9). Por thread: a volta do laço e o pedido de uma rota nunca descarregam as marcas um do outro (um evento
        emitido antes do commit de quem escreveu mostraria o estado velho)."""
        marcas: list[tuple[str, str, bool]] = self._local.__dict__.setdefault("marcas", [])
        marcas.append((tipo, ident, pessoa))

    def descarregar(self) -> list[tuple[str, str, bool]]:
        marcas: list[tuple[str, str, bool]] = self._local.__dict__.get("marcas", [])
        self._local.marcas = []
        return marcas

    # ------------------------------------------------------------------ pedido
    def pedido(self, pedido_id: str) -> Row | None:
        return self.db.one("SELECT * FROM pedidos WHERE id=?", (pedido_id,))

    def pedidos_para_cuidar(self, ate: str) -> list[Row]:
        """Pedidos `ativo` cuja próxima materialização cai até `ate` (`formatar_instante`), ou que nunca a calcularam
        (`proxima_em` NULL: pedido novo, ou esgotado esperando o encerramento)."""
        return self.db.query("SELECT * FROM pedidos WHERE estado='ativo' AND (proxima_em IS NULL OR proxima_em <= ?)"
                             " ORDER BY criado_em, id", (ate,))

    def menor_proxima_em(self) -> str | None:
        return self.db.scalar("SELECT MIN(proxima_em) FROM pedidos WHERE estado='ativo'")

    def definir_proxima_em(self, pedido_id: str, versao: int, proxima_em: str | None, em: str) -> bool:
        cur = self.db.execute("UPDATE pedidos SET proxima_em=?, atualizado_em=? WHERE id=? AND versao=? AND"
                              " estado='ativo'", (proxima_em, em, pedido_id, versao))
        return (cur.rowcount or 0) == 1

    def com_orcamento_total(self) -> list[Row]:
        """Pedidos `ativo` com orçamento total (28.6): os únicos que o laço confere a cada volta."""
        return self.db.query("SELECT * FROM pedidos WHERE estado='ativo' AND orcamento_total_usd IS NOT NULL"
                             " ORDER BY criado_em, id")

    def custo_total(self, pedido_id: str) -> float:
        """US$ já gravados em todas as ocorrências do pedido (só as que FECHARAM somam: ver `mover`)."""
        return float(self.db.scalar("SELECT COALESCE(SUM(custo_usd), 0) FROM pedido_ocorrencias WHERE pedido_id=?",
                                    (pedido_id,)) or 0.0)

    def ultimos_custos(self, pedido_id: str, quantos: int) -> list[float]:
        """`custo_usd` das últimas ocorrências FECHADAS COM execução, a mais recente primeiro (a estimativa do §10)."""
        return [float(r["custo_usd"]) for r in self.db.query(
            "SELECT custo_usd FROM pedido_ocorrencias WHERE pedido_id=? AND run_id IS NOT NULL AND estado IN"
            " ('concluida','falhou','incerta','cancelada') ORDER BY terminada_em DESC, id DESC LIMIT ?",
            (pedido_id, quantos))]

    def mudar_estado_do_pedido(self, pedido_id: str, de: str, para: str, em: str, *, versao: int | None = None,
                               pausado_motivo: str | None = None, encerrado_motivo: str | None = None,
                               pessoa: bool = False) -> bool:
        sql = ("UPDATE pedidos SET estado=?, atualizado_em=?, proxima_em=NULL,"
               " pausado_motivo=COALESCE(?, pausado_motivo), encerrado_motivo=COALESCE(?, encerrado_motivo)"
               " WHERE id=? AND estado=?")
        params: list[object] = [para, em, pausado_motivo, encerrado_motivo, pedido_id, de]
        if versao is not None:
            sql += " AND versao=?"
            params.append(versao)
        mudou = (self.db.execute(sql, tuple(params)).rowcount or 0) == 1
        if mudou:
            self.marcar("pedido", pedido_id, pessoa=pessoa)
        return mudou

    # ------------------------------------------------------------------ gatilhos
    def gatilhos_ativos(self, pedido_id: str) -> list[Row]:
        return self.db.query("SELECT * FROM pedido_gatilhos WHERE pedido_id=? AND ativo=1 ORDER BY criado_em, id",
                             (pedido_id,))

    def maior_previsto(self, gatilho_id: str) -> str | None:
        return self.db.scalar("SELECT MAX(previsto_para) FROM pedido_ocorrencias WHERE gatilho_id=?", (gatilho_id,))

    def cursor(self, g: Row) -> datetime:
        """O último instante materializado do gatilho (exclusivo). Cursor perdido: `MAX(previsto_para)` do gatilho, ou
        a ativação menos 1 s (nada anterior a ela vira `perdida`). As chaves impedem duplicata em qualquer caso."""
        if g["cursor"]:
            return parse_iso(g["cursor"])
        maior = self.maior_previsto(g["id"])
        if maior:
            return parse_iso(maior)
        return truncar(parse_iso(g["criado_em"])) - timedelta(seconds=1)

    def avancar_cursor(self, gatilho_id: str, cursor: str) -> None:
        self.db.execute("UPDATE pedido_gatilhos SET cursor=? WHERE id=?", (cursor, gatilho_id))

    def inserir_gatilho(self, gatilho_id: str, pedido_id: str, tipo: str, spec: str, cursor: str | None,
                        criado_em: str) -> None:
        self.db.execute("INSERT INTO pedido_gatilhos(id, pedido_id, tipo, spec, cursor, ativo, criado_em)"
                        " VALUES (?,?,?,?,?,1,?)", (gatilho_id, pedido_id, tipo, spec, cursor, criado_em))

    def desativar_gatilhos(self, pedido_id: str) -> None:
        self.db.execute("UPDATE pedido_gatilhos SET ativo=0 WHERE pedido_id=? AND ativo=1", (pedido_id,))

    # ------------------------------------------------------------------ ocorrências: leitura
    def ocorrencia(self, ocorrencia_id: str) -> Row | None:
        return self.db.one("SELECT * FROM pedido_ocorrencias WHERE id=?", (ocorrencia_id,))

    def previstas_ate(self, gatilho_id: str, ate: str) -> list[Row]:
        """As `prevista` do gatilho cuja hora já chegou (`previsto_para <= ate`, `formatar_instante`)."""
        return self.db.query("SELECT * FROM pedido_ocorrencias WHERE gatilho_id=? AND estado='prevista'"
                             " AND previsto_para <= ? ORDER BY previsto_para", (gatilho_id, ate))

    def menor_prevista(self, pedido_id: str) -> str | None:
        return self.db.scalar("SELECT MIN(previsto_para) FROM pedido_ocorrencias WHERE pedido_id=? AND"
                              " estado='prevista'", (pedido_id,))

    def devidas(self) -> list[Row]:
        return self.db.query("SELECT * FROM pedido_ocorrencias WHERE estado='devida' ORDER BY pedido_id,"
                             " previsto_para, chave")

    def ids_das_abertas(self, pedido_id: str) -> list[str]:
        return [r["id"] for r in self.db.query(
            f"SELECT id FROM pedido_ocorrencias WHERE pedido_id=? AND estado IN ({_marcas(ABERTAS)}) ORDER BY id",
            (pedido_id, *ABERTAS))]

    def execucoes_abertas(self, pedido_id: str) -> list[str]:
        return [r["run_id"] for r in self.db.query(
            f"SELECT run_id FROM pedido_ocorrencias WHERE pedido_id=? AND estado IN ({_marcas(ABERTAS)})"
            " AND run_id IS NOT NULL", (pedido_id, *ABERTAS))]

    def quantas_em_aberto(self, pedido_id: str) -> int:
        return int(self.db.scalar(
            f"SELECT COUNT(*) FROM pedido_ocorrencias WHERE pedido_id=? AND estado IN ({_marcas(EM_ABERTO)})",
            (pedido_id, *EM_ABERTO)) or 0)

    def executadas(self, pedido_id: str) -> int:
        """D4: `max_ocorrencias` conta só as ocorrências que viraram execução (`pulada` e `perdida` sem execução não
        gastaram nada)."""
        return int(self.db.scalar("SELECT COUNT(*) FROM pedido_ocorrencias WHERE pedido_id=? AND run_id IS NOT NULL",
                                  (pedido_id,)) or 0)

    def para_fechar(self, ocorrencia_id: str | None = None) -> list[Row]:
        """Ocorrências com execução em andamento, com o que o fechamento precisa dela (`LEFT JOIN`: a purga pode ter
        apagado a linha de `runs`). `ocorrencia_id` relê só uma (depois de cancelar por prazo de início)."""
        filtro = " AND o.id=?" if ocorrencia_id is not None else ""
        return self.db.query(
            "SELECT o.*, p.estado AS pedido_estado, r.status AS run_status, r.status_detail AS run_detalhe,"
            " r.started_at AS run_iniciada, r.created_at AS run_criada, r.id AS run_existe"
            " FROM pedido_ocorrencias o JOIN pedidos p ON p.id=o.pedido_id LEFT JOIN runs r ON r.id=o.run_id"
            " WHERE o.estado IN ('despachada','rodando')" + filtro + " ORDER BY o.previsto_para, o.id",
            (ocorrencia_id,) if ocorrencia_id is not None else ())

    def objetivos(self, run_id: str) -> list[Row]:
        return self.db.query("SELECT id, status, started_at, blocked_reason FROM objectives WHERE run_id=?"
                             " ORDER BY id", (run_id,))

    def espera_da_execucao(self, run_id: str) -> str | None:
        return self.db.scalar("SELECT blocked_reason FROM objectives WHERE run_id=? AND blocked_reason IS NOT NULL"
                              " ORDER BY id LIMIT 1", (run_id,))

    def execucao_pela_chave(self, chave: str) -> Row | None:
        return self.db.one("SELECT id, status FROM runs WHERE idempotency_key=?", (chave,))

    def de_pedido_parado(self) -> list[Row]:
        """`prevista`/`devida` de pedido `pausado` ou `cancelado`: o laço as fecha (a ação da pessoa pode ter caído no
        meio)."""
        return self.db.query("SELECT o.id, o.estado, p.estado AS pedido_estado FROM pedido_ocorrencias o"
                             " JOIN pedidos p ON p.id=o.pedido_id WHERE o.estado IN ('prevista','devida')"
                             " AND p.estado IN ('pausado','cancelado') ORDER BY o.previsto_para, o.id")

    # ------------------------------------------------------------------ ocorrências: escrita
    def inserir_ocorrencia(self, *, pedido_id: str, versao: int, gatilho_id: str | None, previsto_para: str,
                           chave: str, origem: str, estado: str, motivo: str | None, token: int | None,
                           criada_em: str, terminada_em: str | None) -> bool:
        """`ON CONFLICT DO NOTHING` sem alvo: cobre a chave UNIQUE e o `UNIQUE (pedido, gatilho, instante)`. Devolve
        se a linha nasceu agora; `False` = já existia (outro laço, ou a mesma volta repetida)."""
        oid = novo_id("o")
        cur = self.db.execute(
            "INSERT INTO pedido_ocorrencias(id, pedido_id, pedido_versao, gatilho_id, previsto_para, chave, origem,"
            " estado, motivo, materializada_token, criada_em, terminada_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT DO NOTHING",
            (oid, pedido_id, versao, gatilho_id, previsto_para, chave, origem, estado, motivo, token,
             criada_em, terminada_em))
        nasceu = (cur.rowcount or 0) == 1
        if nasceu:
            self.marcar("ocorrencia", oid)
        return nasceu

    def mover(self, ocorrencia_id: str, de: str, para: str, *, motivo: str | None = None,
              iniciada_em: str | None = None, terminada_em: str | None = None,
              custo_usd: float | None = None) -> bool:
        """CAS de estado. Solta a reserva (`dono`, `prazo_posse`): uma ocorrência que mudou de estado não é mais de
        ninguém.

        `custo_usd` (28.6): o custo desta TENTATIVA, SOMADO ao que já havia, no MESMO `UPDATE` do fechamento. Atômico
        com a mudança de estado: o CAS que perde (outro laço fechou antes) não soma nada, então o custo entra uma vez
        só por fechamento; e uma queda não deixa a ocorrência fechada sem custo."""
        cur = self.db.execute(
            "UPDATE pedido_ocorrencias SET estado=?, motivo=COALESCE(?, motivo), iniciada_em=COALESCE(?, iniciada_em),"
            " terminada_em=?, custo_usd=custo_usd+?, dono=NULL, prazo_posse=NULL WHERE id=? AND estado=?",
            (para, motivo, iniciada_em, terminada_em, float(custo_usd or 0.0), ocorrencia_id, de))
        mudou = (cur.rowcount or 0) == 1
        if mudou:
            self.marcar("ocorrencia", ocorrencia_id)
        return mudou

    def reservar(self, ocorrencia_id: str, dono: str, prazo_posse: str, agora: str) -> bool:
        """Eficiência, não correção: só um laço de cada vez gasta o planejador numa ocorrência. A posse vence sozinha."""
        cur = self.db.execute(
            "UPDATE pedido_ocorrencias SET dono=?, prazo_posse=? WHERE id=? AND estado='devida'"
            " AND (dono IS NULL OR dono=? OR prazo_posse < ?)", (dono, prazo_posse, ocorrencia_id, dono, agora))
        return (cur.rowcount or 0) == 1

    def soltar_reserva(self, ocorrencia_id: str, resumo: str | None) -> None:
        self.db.execute("UPDATE pedido_ocorrencias SET dono=NULL, prazo_posse=NULL, resumo=? WHERE id=? AND"
                        " estado='devida'", (resumo, ocorrencia_id))

    def marcar_despachada(self, ocorrencia_id: str, run_id: str, tentativa: int) -> bool:
        # `terminada_em` volta a NULL: numa nova tentativa ele guardava o instante `nao_antes_de` (ver `retentar`).
        cur = self.db.execute(
            "UPDATE pedido_ocorrencias SET estado='despachada', run_id=?, tentativa=?, iniciada_em=NULL,"
            " terminada_em=NULL, dono=NULL, prazo_posse=NULL, resumo=NULL WHERE id=? AND estado='devida'",
            (run_id, tentativa, ocorrencia_id))
        mudou = (cur.rowcount or 0) == 1
        if mudou:
            self.marcar("ocorrencia", ocorrencia_id)
        return mudou

    def retentar(self, ocorrencia_id: str, de: str, *, motivo: str, nao_antes_de: str, custo_usd: float) -> bool:
        """`despachada`/`rodando` → `devida` NA MESMA LINHA, para a nova tentativa do 28.5 (§7.6): o CAS de estado, o
        custo da tentativa que falhou SOMADO (como em `mover`) e a falha como `motivo`, tudo no mesmo `UPDATE`.

        Sem coluna própria (a 067 não a tem e o 28.5 não leva migração), `terminada_em` carrega o instante `nao_antes_de`
        (`to_iso`) enquanto a ocorrência é `devida` com `tentativa > 0`: o laço só a despacha depois dele, e a janela de
        recuperação conta a partir dele. `tentativa` e `run_id` ficam: a próxima chave é `chave:t<tentativa+1>` e a
        ocorrência segue contando em `max_ocorrencias` (já virou execução)."""
        cur = self.db.execute(
            "UPDATE pedido_ocorrencias SET estado='devida', motivo=?, terminada_em=?, custo_usd=custo_usd+?,"
            " dono=NULL, prazo_posse=NULL, resumo=NULL WHERE id=? AND estado=?",
            (motivo, nao_antes_de, float(custo_usd or 0.0), ocorrencia_id, de))
        mudou = (cur.rowcount or 0) == 1
        if mudou:
            self.marcar("ocorrencia", ocorrencia_id)
        return mudou

    def efeito_possivel(self, run_id: str) -> bool:
        """Alguma ação da execução PODE ter chegado ao aparelho (`actions.effect_possible`, o que `_reconciliar` deixa
        marcado), ou ficou `intended`/`unknown` sem resultado? Qualquer tentativa de qualquer etapa."""
        return bool(self.db.scalar(
            "SELECT 1 FROM actions a JOIN attempts t ON t.id=a.attempt_id JOIN steps s ON s.id=t.step_id"
            " WHERE s.run_id=? AND (a.effect_possible=1 OR a.status IN ('intended','unknown')) LIMIT 1", (run_id,)))

    def desfechos_recentes(self, pedido_id: str, excluindo: str, limite: int) -> list[str]:
        """Estado das últimas ocorrências COM execução que fecharam (`concluida`, `falhou`, `incerta`, `cancelada`), a
        mais recente primeiro, sem `excluindo` (a que está fechando agora). Base da contagem de falhas seguidas."""
        return [r["estado"] for r in self.db.query(
            "SELECT estado FROM pedido_ocorrencias WHERE pedido_id=? AND id<>? AND run_id IS NOT NULL AND estado IN"
            " ('concluida','falhou','incerta','cancelada') ORDER BY terminada_em DESC, id DESC LIMIT ?",
            (pedido_id, excluindo, limite))]

    def gravar_resumo(self, ocorrencia_id: str, resumo: str) -> None:
        self.db.execute("UPDATE pedido_ocorrencias SET resumo=? WHERE id=?", (resumo, ocorrencia_id))

    def trocar_versao_das_abertas(self, pedido_id: str, versao: int) -> int:
        """Editar o pedido (D5/A6): as `prevista`/`devida` passam à versão nova NA MESMA LINHA. Não é transição de
        estado. As despachadas terminam na versão em que nasceram."""
        cur = self.db.execute("UPDATE pedido_ocorrencias SET pedido_versao=? WHERE pedido_id=? AND estado IN"
                              " ('prevista','devida')", (versao, pedido_id))
        return int(cur.rowcount or 0)

    def ids_prevista_devida(self, pedido_id: str, gatilho_id: str | None = None, *,
                            sem_retentativas: bool = False) -> list[Row]:
        """`sem_retentativas`: deixa de fora a `devida` que já virou execução (`tentativa > 0`, uma nova tentativa do
        28.5): ela já foi contada em `max_ocorrencias`, e pular o que o máximo "barra" não vale para ela."""
        sql = "SELECT id, estado FROM pedido_ocorrencias WHERE pedido_id=? AND estado IN ('prevista','devida')"
        if sem_retentativas:
            sql += " AND tentativa=0"
        params: list[object] = [pedido_id]
        if gatilho_id is not None:
            sql += " AND gatilho_id=?"
            params.append(gatilho_id)
        return self.db.query(sql + " ORDER BY previsto_para, id", tuple(params))
