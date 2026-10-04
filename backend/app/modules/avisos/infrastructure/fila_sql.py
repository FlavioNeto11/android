"""A fila durável dos avisos (migração 068), em SQLite e PostgreSQL.

Quem escreve aqui e com que garantia:

- `enfileirar` roda em QUALQUER backend que viu o evento. É idempotente pela chave única (`ON CONFLICT DO NOTHING`, e
  não `except IntegrityError`: no PostgreSQL o erro abortaria a transação do chamador): duas réplicas, ou o mesmo
  evento relido, produzem UMA linha.
- `reivindicar_um` e as varreduras só rodam no líder da trava `avisos` e dentro de `Lideranca.cercada`: a transição
  `pendente → enviando` confere o token NA MESMA transação. Um líder que acordou de uma pausa longa e já perdeu o
  mandato é recusado antes de marcar a linha e, portanto, antes de enviar.
- `enviando` abandonado (o processo caiu entre reivindicar e gravar o desfecho) NUNCA volta a `pendente`: a varredura o
  passa a `incerto`. O envio pode ter saído; reenviar duplicaria, e não reenviar apenas deixa de repetir o que a caixa
  do painel já mostra.

O tempo é o relógio injetado (o do banco, por padrão): comparar `proximo_envio_em` com o relógio de cada máquina
reabriria o defeito do item 5.3.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

from app.db import Database
from app.modules.avisos.application.entrega import Cerca, Entrega
from app.modules.avisos.domain.mensagem import (
    JANELA,
    JANELA_DA_ROTINA_S,
    TIPO_DA_ROTINA,
    Aviso,
    corpo_agrupado,
    corpo_da_rotina,
    entrega_do_tipo,
    titulo_agrupado,
    titulo_da_rotina,
)
from app.util import to_iso

#: Estados que já não mudam: candidatos à purga.
ESTADOS_FINAIS = ("enviado", "falhou", "incerto", "descartado")
MAX_ERRO = 300
#: Quantas linhas devidas a reivindicação olha para achar a próxima mensagem: mais que qualquer rajada real.
LIMITE_DA_VARREDURA = 500
#: Os avisos sobre quem não é o dono (28.18) saem sempre um a um: o do convidado novo se decide respondendo a ELE (o
#: agrupado não aceita resposta), e cada um leva o nome de uma pessoa diferente.
SEM_AGRUPAR = "telegram."


def _curto(texto: str) -> str:
    """O erro gravado é curto e de uma linha: ele vai para o diagnóstico, não para a tela de ninguém."""
    return " ".join(texto.split())[:MAX_ERRO]


class FilaDeAvisos:
    def __init__(self, db: Database, *, canal: str = "telegram", relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.canal = canal
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora

    def _agora(self) -> str:
        return to_iso(self.relogio())

    # ------------------------------------------------------------------ entrada
    def enfileirar(self, aviso: Aviso) -> bool:
        """Grava o aviso como `pendente`. Devolve se a linha é nova (`False` = o fato já estava na fila)."""
        cur = self.db.execute(
            "INSERT INTO avisos_entregas(chave, tipo, titulo, corpo, link, canal, criado_em) VALUES (?,?,?,?,?,?,?)"
            " ON CONFLICT (chave) DO NOTHING",
            (aviso.chave, aviso.tipo, aviso.titulo, aviso.corpo, aviso.link, self.canal, self._agora()))
        return (cur.rowcount or 0) == 1

    # ------------------------------------------------------------------ saída (líder)
    def reivindicar_um(self, *, cerca: Cerca, agrupar_s: float = 0.0, agrupar_a_partir_de: int = 3,
                       janela_s: float = JANELA_DA_ROTINA_S) -> Entrega | None:
        """A próxima mensagem devida, com as suas linhas já marcadas `enviando` e a tentativa contada, ou `None`.

        A cerca é a do mandato do líder. O `UPDATE ... WHERE estado='pendente'` confere de novo o estado: duas
        reivindicações que sobrevivessem à cerca ainda não pegariam a mesma linha.

        Rajada (28.19), com `agrupar_s` > 0. O primeiro aviso de um tipo sai na hora, como sempre. A linha do mesmo
        tipo que nasceu DEPOIS de um envio desse tipo há menos de `agrupar_s` espera o fim dessa janela. Fechada a
        janela, todas as pendentes do tipo saem juntas: com `agrupar_a_partir_de` ou mais, como UMA mensagem com a
        contagem (`titulo_agrupado`); com menos, uma a uma, sem nova espera (a linha nascida antes do último envio não
        espera de novo). A espera de uma linha nunca passa de `agrupar_s`: uma rajada de 11 vira 2 mensagens, e dois
        avisos seguidos do dono continuam dois, com o segundo atrasado no máximo `agrupar_s`.

        Rotina (28.31). A linha cujo tipo é de `janela` (`mensagem.entrega_do_tipo`: o nível 3 e o nível 2 que não parou
        nada) nunca sai sozinha nem entra numa rajada: espera até a mais velha delas fazer `janela_s`, e aí TODAS saem
        numa mensagem só, uma linha cada. O que pede o dono passa sempre na frente.
        """
        agora = self._agora()
        with cerca():
            devidas = self.db.query(
                "SELECT id, tipo, criado_em FROM avisos_entregas WHERE estado='pendente' AND canal=?"
                " AND (proximo_envio_em IS NULL OR proximo_envio_em <= ?) ORDER BY id LIMIT ?",
                (self.canal, agora, LIMITE_DA_VARREDURA))
            if not devidas:
                return None
            na_hora = [x for x in devidas if entrega_do_tipo(str(x["tipo"])) != JANELA]
            rotina = [x for x in devidas if entrega_do_tipo(str(x["tipo"])) == JANELA]
            escolhidas = self._escolher(na_hora, agrupar_s, agrupar_a_partir_de) if na_hora else []
            e_rotina = False
            if not escolhidas and rotina:
                fecha = to_iso(self.relogio() - timedelta(seconds=janela_s))
                if min(str(x["criado_em"]) for x in rotina) <= fecha:
                    escolhidas, e_rotina = [int(x["id"]) for x in rotina], True
            if not escolhidas:
                return None
            marcas = ",".join("?" * len(escolhidas))
            cur = self.db.execute(
                f"UPDATE avisos_entregas SET estado='enviando', tentativas=tentativas+1, iniciado_em=?"  # noqa: S608
                f" WHERE id IN ({marcas}) AND estado='pendente'", (agora, *escolhidas))
            if (cur.rowcount or 0) != len(escolhidas):
                # Outra reivindicação levou parte delas (não deveria passar da cerca): devolve as que eu marquei.
                self.db.execute(
                    f"UPDATE avisos_entregas SET estado='pendente', tentativas=tentativas-1, iniciado_em=NULL"  # noqa: S608
                    f" WHERE id IN ({marcas}) AND estado='enviando' AND iniciado_em=?", (*escolhidas, agora))
                return None
            linhas = self.db.query(
                f"SELECT id, chave, tipo, titulo, corpo, link, tentativas FROM avisos_entregas"  # noqa: S608
                f" WHERE id IN ({marcas}) ORDER BY id", tuple(escolhidas))
        r = linhas[0]
        if len(linhas) == 1:
            return Entrega(id=int(r["id"]), chave=str(r["chave"]), tipo=str(r["tipo"]), titulo=str(r["titulo"]),
                           corpo=str(r["corpo"] or ""), link=cast("str | None", r["link"]),
                           tentativas=int(r["tentativas"]))
        titulos = [str(x["titulo"]) for x in linhas]
        if e_rotina:
            # A rotina não pede gesto nem aceita reply: sem link (o link específico de cada item é do 28.31 F2).
            return Entrega(id=int(r["id"]), chave=str(r["chave"]), tipo=TIPO_DA_ROTINA,
                           titulo=titulo_da_rotina(len(linhas)), corpo=corpo_da_rotina(titulos), link=None,
                           tentativas=max(int(x["tentativas"]) for x in linhas), ids=tuple(int(x["id"]) for x in linhas))
        # O link do agrupado é o da CAIXA, que o corpo manda abrir: todo link da fila é `link_da_caixa` (ou nenhum, no
        # aviso de pedido que não pede pessoa), então vale o primeiro que houver no grupo, e não o da primeira linha.
        link = next((str(x["link"]) for x in linhas if x["link"]), None)
        return Entrega(id=int(r["id"]), chave=str(r["chave"]), tipo=str(r["tipo"]),
                       titulo=titulo_agrupado(str(r["tipo"]), len(linhas)), corpo=corpo_agrupado(titulos),
                       link=link, tentativas=max(int(x["tentativas"]) for x in linhas),
                       ids=tuple(int(x["id"]) for x in linhas))

    def _escolher(self, devidas: list, agrupar_s: float, a_partir_de: int) -> list[int]:  # type: ignore[type-arg]
        """Os ids da próxima mensagem: a primeira linha devida cujo tipo não está segurado, e, com o agrupamento ligado,
        as outras devidas do mesmo tipo quando passam de `a_partir_de`. Ver `reivindicar_um`."""
        if agrupar_s <= 0:
            return [int(devidas[0]["id"])]
        limite = to_iso(self.relogio() - timedelta(seconds=agrupar_s))
        segurado: dict[str, bool] = {}
        for linha in devidas:
            tipo = str(linha["tipo"])
            if tipo.startswith(SEM_AGRUPAR):
                return [int(linha["id"])]
            if tipo not in segurado:
                ultimo = self.db.one(
                    "SELECT MAX(enviado_em) AS u FROM avisos_entregas WHERE canal=? AND tipo=? AND estado='enviado'"
                    " AND enviado_em > ?", (self.canal, tipo, limite))
                u = ultimo["u"] if ultimo is not None else None
                # Segura só a linha que nasceu DEPOIS do último envio do tipo dentro da janela.
                segurado[tipo] = u is not None and str(linha["criado_em"]) > str(u)
            if segurado[tipo]:
                continue
            do_tipo = [int(x["id"]) for x in devidas if str(x["tipo"]) == tipo]
            return do_tipo if len(do_tipo) >= a_partir_de else [int(linha["id"])]
        return []

    def marcar_enviado(self, entrega_id: int, *, message_id: int | None = None, fato: str | None = None) -> None:
        """Com o `message_id`, a mensagem entra no registro do que a Central enviou pelo canal (`canal_enviadas` da
        085, item 28.15): é o que liga o reply da pessoa ao fato do aviso, e o que separa o reply à Central do reply à
        orquestradora. `fato` troca a chave da linha (o agrupado do 28.19 grava `grupo:<tipo>`, que não é fato)."""
        agora = self._agora()
        self.db.execute("UPDATE avisos_entregas SET estado='enviado', enviado_em=?, proximo_envio_em=NULL,"
                        " ultimo_erro=NULL WHERE id=? AND estado='enviando'", (agora, entrega_id))
        if message_id is not None:
            if fato is None:
                self.db.execute(
                    "INSERT INTO canal_enviadas(canal, ref_mensagem, origem, fato, aviso_id, enviada_em)"
                    " SELECT canal, ?, 'aviso', chave, id, ? FROM avisos_entregas WHERE id=?"
                    " ON CONFLICT (canal, ref_mensagem) DO NOTHING",
                    (str(int(message_id)), agora, entrega_id))
            else:
                self.db.execute(
                    "INSERT INTO canal_enviadas(canal, ref_mensagem, origem, fato, aviso_id, enviada_em)"
                    " SELECT canal, ?, 'aviso', ?, id, ? FROM avisos_entregas WHERE id=?"
                    " ON CONFLICT (canal, ref_mensagem) DO NOTHING",
                    (str(int(message_id)), fato, agora, entrega_id))

    def marcar_retentar(self, entrega_id: int, *, ate: datetime, erro: str) -> None:
        self.db.execute("UPDATE avisos_entregas SET estado='pendente', proximo_envio_em=?, ultimo_erro=?"
                        " WHERE id=? AND estado='enviando'", (to_iso(ate), _curto(erro), entrega_id))

    def marcar_falhou(self, entrega_id: int, *, erro: str) -> None:
        self.db.execute("UPDATE avisos_entregas SET estado='falhou', proximo_envio_em=NULL, ultimo_erro=?"
                        " WHERE id=? AND estado='enviando'", (_curto(erro), entrega_id))

    # ------------------------------------------------------------------ varreduras (líder, cercadas)
    def varrer_incertos(self, *, cerca: Cerca, parado_ha_s: float) -> int:
        """`enviando` há mais de `parado_ha_s` é resto de uma queda no meio do envio: vira `incerto`, sem reenvio."""
        limite = to_iso(self.relogio() - timedelta(seconds=parado_ha_s))
        with cerca():
            cur = self.db.execute(
                "UPDATE avisos_entregas SET estado='incerto', ultimo_erro=?"
                " WHERE estado='enviando' AND iniciado_em < ?",
                ("o processo parou durante o envio; pode ter saído, não foi reenviado", limite))
        return int(cur.rowcount or 0)

    def vencer(self, *, cerca: Cerca, validade_h: float) -> int:
        """`pendente` velho demais não é mais notícia (canal fora do ar por dias, segredo faltando): `descartado`."""
        limite = to_iso(self.relogio() - timedelta(hours=validade_h))
        with cerca():
            cur = self.db.execute(
                "UPDATE avisos_entregas SET estado='descartado', ultimo_erro=?"
                " WHERE estado='pendente' AND criado_em < ?",
                (f"venceu: ficou mais de {validade_h:g} h sem sair", limite))
        return int(cur.rowcount or 0)

    def purgar(self, *, cerca: Cerca, retencao_dias: float) -> int:
        limite = to_iso(self.relogio() - timedelta(days=retencao_dias))
        marcas = ",".join("?" * len(ESTADOS_FINAIS))
        with cerca():
            cur = self.db.execute(
                f"DELETE FROM avisos_entregas WHERE estado IN ({marcas}) AND criado_em < ?",  # noqa: S608
                (*ESTADOS_FINAIS, limite))
        return int(cur.rowcount or 0)

    # ------------------------------------------------------------------ leitura
    def contagens(self) -> dict[str, int]:
        linhas = self.db.query("SELECT estado, COUNT(*) AS n FROM avisos_entregas GROUP BY estado")
        return {str(r["estado"]): int(r["n"]) for r in linhas}
