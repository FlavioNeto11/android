"""O estado dos canais externos para a tela Canais do painel (item 32.5): só leitura, sem migração e sem config nova.

Reúne num lugar o que as três frentes já gravam (a fila de avisos da 068, o registro da conversa e do Trello da 085, os
cartões e o cursor da 087) e o que a saúde já sabe (os códigos dos problemas). A tela diz SE o canal anda, desde QUANDO e
QUANTO; nunca o QUÊ. Por isso a resposta é uma lista fechada de chaves, de números, de horas e de códigos:

- nada de título, corpo ou texto de mensagem; nada de `chat_id`, id de membro, nome, id de quadro, lista ou cartão;
- o motivo da última falha de envio é um código de lista fechada (`MOTIVOS`), derivado do `ultimo_erro` por regra. O texto
  do `ultimo_erro` não sai daqui: o erro de rede pode carregar a URL do bot, e a URL do bot é o token;
- dos problemas da saúde sai só o `code`, nunca a mensagem nem a dica (elas citam o que falta no `.env`);
- os estados vêm de vocabulário fechado: um estado que o código ainda não conhece soma em `outro`, não vira chave nova.

Um estado novo ou um código novo é uma decisão consciente (e o teste `test_canais_estado.py` trava o conjunto de chaves).
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from app.config import Config
from app.db import Database
from app.models import Problem
from app.modules.avisos.infrastructure.entrada_sql import INICIO

#: Os estados da fila (CHECK da migração 068).
ESTADOS_DA_FILA = ("pendente", "enviando", "enviado", "falhou", "incerto", "descartado")
#: Os estados do registro da conversa (085) e o `aviso` que o webhook do Trello anota (32.2).
ESTADOS_DA_ENTRADA = ("recebida", "ignorada", "recusada", "limitada", "orquestradora", "pergunta", "executando",
                      "feita", "cancelada", "falhou", "aviso")
#: Os estados do cartão (087 + o `criando` da intenção gravada antes da chamada).
ESTADOS_DO_CARTAO = ("ativo", "arquivado", "criando")
#: Onde cai o estado que o código ainda não conhece.
OUTRO = "outro"

#: Os motivos da última falha de envio. Lista fechada: a tela traduz cada um para português.
MOTIVOS = ("rede", "401", "429", "tempo_esgotado", "outro")

# As frases de `adapters/telegram.py` (`_chamar` e `_falha`). Ancoradas no começo: a descrição que o Telegram devolve vem
# DEPOIS do status e pode conter qualquer número.
_RECUSA = re.compile(r"^Telegram (?:recusou|pediu para esperar) \((\d{3})\)")


def motivo_da_falha(erro: str | None) -> str:
    """O código de `MOTIVOS` para um `ultimo_erro`. Nunca devolve o texto recebido."""
    texto = (erro or "").strip()
    if texto.startswith("tempo esgotado"):
        return "tempo_esgotado"
    if texto.startswith("falha de rede"):
        return "rede"
    achado = _RECUSA.match(texto)
    if achado is not None and achado.group(1) in ("401", "429"):
        return achado.group(1)
    return "outro"


def _por_estado(linhas: Iterable[tuple[str, int]], conhecidos: tuple[str, ...]) -> dict[str, int]:
    """Todos os estados conhecidos (zero quando não há), mais `outro` só se algum estado desconhecido apareceu."""
    saida = dict.fromkeys(conhecidos, 0)
    for estado, n in linhas:
        chave = estado if estado in saida else OUTRO
        saida[chave] = saida.get(chave, 0) + n
    return saida


def _codigos(fontes: Iterable[Callable[[], Iterable[Problem]]], *, prefixo: str = "") -> list[str]:
    """Os `code` dos problemas, sem repetir e na ordem em que as fontes os dão. Só o código sai."""
    vistos: dict[str, None] = {}
    for fonte in fontes:
        for problema in fonte():
            if problema.code.startswith(prefixo):
                vistos.setdefault(problema.code, None)
    return list(vistos)


def _tem(segredo: object) -> bool:
    return bool(segredo.get_secret_value().strip()) if hasattr(segredo, "get_secret_value") else False


class EstadoDosCanais:
    """Monta o estado dos três canais. Sem estado próprio: pode ser criado a cada chamada."""

    def __init__(self, db: Database, cfg: Config, *, problemas_do_aviso: Iterable[Callable[[], Iterable[Problem]]],
                 problemas_da_conversa: Iterable[Callable[[], Iterable[Problem]]],
                 problemas_do_trello: Iterable[Callable[[], Iterable[Problem]]]):
        self.db = db
        self.cfg = cfg
        self._do_aviso = tuple(problemas_do_aviso)
        self._da_conversa = tuple(problemas_da_conversa)
        self._do_trello = tuple(problemas_do_trello)

    # ------------------------------------------------------------------ o conjunto
    def ler(self) -> dict[str, object]:
        return {
            "gerado_em": self.db.agora_iso(),
            "aviso_telegram": self.aviso_telegram(),
            "conversa_telegram": self.conversa_telegram(),
            "trello": self.trello(),
        }

    # ------------------------------------------------------------------ o aviso pelo Telegram
    def aviso_telegram(self) -> dict[str, object]:
        env, avisos = self.cfg.env, self.cfg.file.avisos
        canal = avisos.canal
        fila = self.db.query("SELECT estado, COUNT(*) AS n FROM avisos_entregas WHERE canal=? GROUP BY estado",
                             (canal,))
        ultimo = self.db.scalar("SELECT MAX(enviado_em) FROM avisos_entregas WHERE canal=? AND estado='enviado'",
                                (canal,))
        # Falha vigente: a linha ainda em retentativa (`pendente`) ou desistida (`falhou`) com a tentativa mais recente.
        # `ultimo_erro` é zerado no envio que dá certo, então um aviso que saiu depois não conta. A hora é a do início da
        # tentativa (`iniciado_em`): não há coluna do instante da falha. `IS NOT NULL` + desempate por id: no PostgreSQL o
        # NULL ordena primeiro no DESC.
        falha = self.db.one(
            "SELECT iniciado_em, ultimo_erro FROM avisos_entregas WHERE canal=? AND ultimo_erro IS NOT NULL"
            " AND estado IN ('pendente', 'falhou') AND iniciado_em IS NOT NULL ORDER BY iniciado_em DESC, id DESC"
            " LIMIT 1", (canal,))
        return {
            "ligado": bool(avisos.enabled),
            "segredo_presente": _tem(env.telegram_bot_token) and _tem(env.telegram_chat_id),
            "fila": _por_estado(((str(r["estado"]), int(r["n"])) for r in fila), ESTADOS_DA_FILA),
            "ultimo_envio_em": str(ultimo) if ultimo else None,
            "ultima_falha": ({"em": str(falha["iniciado_em"]), "motivo": motivo_da_falha(str(falha["ultimo_erro"]))}
                             if falha is not None else None),
            "problemas": _codigos(self._do_aviso),
        }

    # ------------------------------------------------------------------ a conversa pelo Telegram
    def conversa_telegram(self) -> dict[str, object]:
        avisos = self.cfg.file.avisos
        return {
            "ligada": bool(avisos.enabled and avisos.entrada.enabled),
            "ultima_leitura_em": self._ultima_entrada("telegram"),
            "entradas": self._entradas("telegram"),
            "problemas": _codigos(self._da_conversa, prefixo="telegram_entrada_"),
        }

    # ------------------------------------------------------------------ o Trello
    def trello(self) -> dict[str, object]:
        cfg = self.cfg.file.trello
        cartoes = self.db.query("SELECT estado, COUNT(*) AS n FROM trello_cartoes GROUP BY estado")
        reconciliada = self.db.scalar("SELECT MAX(atualizado_em) FROM trello_cursor")
        return {
            "ligado": bool(cfg.enabled),
            "webhook_ligado": bool(cfg.webhook.enabled),
            "cadastro_automatico": bool(cfg.webhook.cadastro_automatico),
            "ultima_reconciliacao_em": str(reconciliada) if reconciliada else None,
            "cartoes": _por_estado(((str(r["estado"]), int(r["n"])) for r in cartoes), ESTADOS_DO_CARTAO),
            "entradas": self._entradas("trello"),
            # 28.55: comentários escritos por app em cartão de alvo desconhecido, tratados como `outro` (só a contagem)
            "comentarios_de_app_em_alvo_desconhecido": int(self.db.scalar(
                "SELECT COUNT(*) FROM canal_entradas WHERE canal='trello' AND responde_a LIKE 'alvo_desconhecido;%'") or 0),
            "problemas": _codigos(self._do_trello, prefixo="trello_"),
        }

    # ------------------------------------------------------------------ o registro da conversa
    def _entradas(self, canal: str) -> dict[str, int]:
        # A linha-marco da 1ª subida (`INICIO`) não é uma mensagem: fora da conta e da data.
        linhas = self.db.query("SELECT estado, COUNT(*) AS n FROM canal_entradas WHERE canal=? AND id_externo<>?"
                               " GROUP BY estado", (canal, INICIO))
        return _por_estado(((str(r["estado"]), int(r["n"])) for r in linhas), ESTADOS_DA_ENTRADA)

    def _ultima_entrada(self, canal: str) -> str | None:
        v = self.db.scalar("SELECT MAX(recebida_em) FROM canal_entradas WHERE canal=? AND id_externo<>?",
                           (canal, INICIO))
        return str(v) if v else None
