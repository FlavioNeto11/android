"""Anexar ao cartão do Trello a imagem que o DONO mandou (item 28.24, exceção (b) do dono, 04/10 15:17Z).

Não existe, hoje, caminho que crie cartão a partir de uma mensagem do dono: os cartões nascem do espelho do plano
(`infrastructure/espelho.py`) e o leitor do Trello só traz comandos DO Trello. Por isso o ponto de uso é explícito: a
rota `POST /api/canais/anexos/{id}/trello` (atrás do login) chama esta função com o cartão que a pessoa indicou.

O que pode ir ao cartão (a regra do dono é estreita):
- só o anexo que o DONO mandou: `direcao = 'entrada'`, `estado = 'guardado'`, e a mensagem de origem (`canal_entradas`)
  com `do_dono = 1`. Nunca o de convidado (nem é baixado), nunca o da saída (a captura de aparelho tem a exceção (a), que
  é só para o chat do dono);
- só em cartão de um quadro configurado (`trello.quadros`): o cartão é confirmado pela API antes de anexar;
- o nome do arquivo no cartão é neutro (`anexo-<sha>.<ext>`), o conteúdo é conferido de novo pelo que está no disco
  (tipo da lista, sha256 igual) e a chave e o token do Trello nunca saem do cliente (cabeçalho; nada em log nem erro).
"""
from __future__ import annotations

import logging
import re

from app.config import Config
from app.db import Database
from app.modules.avisos.adapters.trello import ClienteTrello
from app.modules.avisos.domain.anexos import EXTENSAO
from app.modules.avisos.infrastructure.anexos import AnexoRecusado, ArmazemDeAnexos, CaminhoForaDoArmazem

log = logging.getLogger("poc.avisos.anexos")

#: O id de cartão do Trello: 24 hexadecimais. Qualquer outra forma nem chega à API.
CARTAO = re.compile(r"[0-9a-f]{24}")


class AnexoNaoPodeIrAoCartao(Exception):
    """A regra do dono (ou a configuração) não deixa. `motivo` é português simples; `codigo` é o do erro da rota."""

    def __init__(self, codigo: str, motivo: str, status: int = 409):
        super().__init__(motivo)
        self.codigo, self.motivo, self.status = codigo, motivo, status


def conferir_origem(db: Database, anexo: dict[str, object] | None) -> dict[str, object]:
    """O anexo só serve se foi o DONO quem mandou. Devolve a linha; senão levanta `AnexoNaoPodeIrAoCartao`."""
    if anexo is None:
        raise AnexoNaoPodeIrAoCartao("anexo_desconhecido", "Não há anexo com esse id.", 404)
    entrada = None
    if anexo.get("direcao") == "entrada" and anexo.get("entrada_id") is not None:
        entrada = db.one("SELECT do_dono, tipo FROM canal_entradas WHERE id=? AND canal=?",
                         (int(str(anexo["entrada_id"])), anexo.get("canal")))
    if anexo.get("direcao") != "entrada" or entrada is None or int(entrada["do_dono"]) != 1 or entrada["tipo"] != "mensagem":
        raise AnexoNaoPodeIrAoCartao("anexo_nao_permitido", "Só a imagem que o dono mandou pelo canal pode ir ao cartão.")
    if anexo.get("estado") != "guardado":
        raise AnexoNaoPodeIrAoCartao("anexo_sem_arquivo", "Este anexo não tem arquivo guardado (recusado ou apagado).")
    return anexo


async def anexar_ao_cartao(db: Database, cfg: Config, armazem: ArmazemDeAnexos, cliente: ClienteTrello | None,
                           anexo_id: int, card: str) -> dict[str, object]:
    """Anexa o arquivo ao cartão `card` e devolve `{anexo_id, card, trello_anexo}`. Levanta `AnexoNaoPodeIrAoCartao`
    (regra) ou `FalhaDoTrello` (a API; a mensagem já vem sem chave nem token)."""
    anexo = conferir_origem(db, armazem.linha(anexo_id))
    if not CARTAO.fullmatch(card):
        raise AnexoNaoPodeIrAoCartao("cartao_invalido", "O id do cartão do Trello tem 24 caracteres (letras a-f e dígitos).", 422)
    if cliente is None:
        raise AnexoNaoPodeIrAoCartao("trello_desligado", "O Trello está desligado ou sem chave e token no .env.", 503)
    cfg_a = cfg.file.avisos.entrada.anexos
    try:
        conteudo, mime, sha = armazem.conteudo_de(anexo_id, tipos=cfg_a.tipos, max_bytes=cfg_a.max_bytes)
    except (CaminhoForaDoArmazem, AnexoRecusado) as exc:
        raise AnexoNaoPodeIrAoCartao("anexo_sem_arquivo", getattr(exc, "motivo", None) or str(exc)) from None
    quadro = await cliente.quadro_do_cartao(card)
    if quadro not in cfg.file.trello.quadros:
        raise AnexoNaoPodeIrAoCartao("cartao_fora_dos_quadros", "Esse cartão não é de um quadro que a Central espelha.")
    nome = f"anexo-{sha[:12]}.{EXTENSAO[mime]}"
    ident = await cliente.anexar_arquivo(card, conteudo, mime, nome)
    log.info("trello: anexo %s foi para um cartão (a pedido, pela rota)", anexo_id)         # só ids da Central, nunca o cartão
    return {"anexo_id": anexo_id, "card": card, "trello_anexo": ident}
