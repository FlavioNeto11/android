"""A conversa de volta pelos canais externos (item 28.15, ADR-071): o dono fala com a Central como no painel.

O contrato comum dos canais (`docs/design/canais-externos.md`) separa duas metades:
- a TRADUÇÃO, própria de cada canal, do que chegou numa `Recebida` (aqui, a update do Telegram: `_ler_update`; o
  Trello do 32.2 traduz a action dele) e a `SaidaDaConversa` dele (responder, apagar, botões);
- a parte COMUM (`registrar` e `tratar_pendentes`): identidade, dedupe, credencial, limite, gramática e as portas do
  painel. Ela não conhece Telegram: o mesmo comando vindo de outro canal passa pelas mesmas políticas.

A parte comum é a `ConversaDoCanal` (não conhece Telegram; `registrar(r, saida=None)` grava sem falar, para o canal
que só recebe, como o webhook do Trello, e `tratar_pendentes(saida)` responde depois). O `LeitorDoTelegram` é o laço
abaixo sobre ela; `ServicoDeEntrada` é o nome de antes, com a mesma assinatura.

O laço do Telegram:
1. só no líder da trava `avisos`, e só com `avisos.enabled` + `avisos.entrada.enabled`;
2. `getUpdates` com o offset do banco (`MAX(ordem) + 1`), em long-poll. Na 1ª subida (canal sem nenhuma linha) o que o
   Telegram guardou antes é DESCARTADO, não tratado: um "/aprovar" ou um "sim" de ontem não pode executar;
3. grava cada update ANTES de tratar (uma que não grava vira `falhou` sem texto, para o offset andar). A de outro chat
   ou outra pessoa, a longa demais, a que parece credencial e a resposta a uma pergunta que pede credencial são gravadas
   SEM o texto e não são tratadas; as com cara de credencial ainda são apagadas do chat quando o Telegram deixa;
4. trata as `recebida` por regra fixa (`application/entrada.rotear`), com o operador `telegram:dono` no ContextVar
   da sessão. `quem()` e `autor_do_gesto()` o leem, então a auditoria e o sinal contam como gesto de PESSOA;
5. conta na thread o desfecho das execuções que a conversa criou.

Anexos (item 28.24, F1): foto, PDF e texto que o DONO manda são baixados pelo canal (`SaidaComAnexos.baixar_anexo`, com
teto de bytes), conferidos pelo conteúdo e guardados no `ArmazemDeAnexos` pelo sha256; a legenda vale como texto. O que
não serve é recusado com o motivo dito ao dono. O convidado nunca tem anexo baixado. A saída (`enviar_anexo`,
`enviar_conteudo`) só manda o que está no armazém, por id ou sha256, ou o que o produto gerou: nunca um caminho livre.
Nenhum conteúdo de anexo é lido por IA aqui (a leitura é da F2).

Toda ação passa por `PortasDaCentral`, que chama os MESMOS serviços das rotas do painel: a prévia de alvos é
obrigatória (botão Executar, decisão (c)), e approval_required, o pré-voo, a rede e os tetos valem iguais. Nada aqui
chama IA.

409 no `getUpdates` é outro consumidor do bot (a caixa provisória da orquestradora até a troca): a entrada espera
`espera_conflito_s` e vira problema na saúde, sem disputar.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol

from app.config import Config
from app.models import RUN_TERMINAL, Problem
from app.modules.avisos.adapters.telegram import CanalTelegram, ConflitoDeConsumidor
from app.modules.avisos.application.entrada import (
    AJUDA,
    RESPOSTA_IDENTIDADE,
    SAUDACAO,
    Intencao,
    casar_ref,
    rotear,
    texto_para_o_extrator,
)
from app.modules.avisos.application.entrega import FalhaDeEnvio
from app.modules.avisos.domain.anexos import (
    ROTULO,
    AnexoGrandeDemais,
    AnexoRecebido,
    normalizar_mime,
    tamanho_legivel,
)
from app.modules.avisos.domain.porta import (
    itens_da_previa,
    ler_porta,
    linha_sem_aprovacao,
    marca_do_retrato,
    mensagens_da_porta,
)
from app.modules.avisos.domain.privacidade import PERSONA_OCULTA as PERSONA_OCULTA  # reexport (28.28)
from app.modules.avisos.domain.privacidade import sem_nome_de_persona as sem_nome_de_persona
from app.modules.avisos.infrastructure.anexos import AnexoJaResolvido, AnexoRecusado, ArmazemDeAnexos
from app.modules.avisos.infrastructure.convidados import ConvidadosDoTelegram
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.security.sessions import OPERADOR
from app.taskqueue.travas import AVISOS
from app.util import parse_iso

log = logging.getLogger("poc.avisos.entrada")

#: Quem age pela conversa, na auditoria (decisão (b)): legível e sem o chat_id.
OPERADOR_DO_TELEGRAM = "telegram:dono"
#: Estados de uma execução que já iniciou e ainda não acabou: a mensagem ao dono nunca diz "não iniciei" (28.36).
EM_ANDAMENTO = ("running", "paused")
#: Estados finais: o desfecho de sempre (`portas.desfecho`) é a linha ao dono, e nenhuma outra (28.36).
TERMINAIS = frozenset(s.value for s in RUN_TERMINAL)
#: Em `needs_input` a execução para até alguém responder; pelo Telegram não se responde, então o texto aponta o painel.
RESPONDA_NO_PAINEL = "espera uma resposta sua: responda no painel para ela seguir"


def _texto_da_recusa(curta: str, estado: str | None, motivo: str) -> str:
    """O que o dono lê quando a Central recusou iniciar, pelo estado lido depois (28.36, 28.38)."""
    if estado in EM_ANDAMENTO:
        return f"A execução {curta} já estava em andamento: {motivo}"
    if estado == "cancelling":       # podia estar rodando: "não iniciei" seria falso
        return f"A execução {curta} está sendo cancelada: {motivo}"
    if estado == "needs_input":
        return f"A execução {curta} {RESPONDA_NO_PAINEL} ({motivo})."
    return f"Não iniciei a execução {curta}: {motivo}"


def _texto_do_erro(curta: str, estado: str | None) -> str:
    """O que o dono lê quando um erro interno veio num caminho de início e a execução já saiu de `planned` (28.36,
    28.38). Em `planning` ela de fato ainda não começou."""
    erro = "houve um erro interno ao iniciar (está no log da Central)"
    if estado in EM_ANDAMENTO:
        return (f"A execução {curta} está em andamento; houve um erro interno logo depois do início (está no log da "
                "Central). Conto aqui quando terminar.")
    if estado == "cancelling":
        return f"A execução {curta} está sendo cancelada; {erro}. Conto aqui o desfecho."
    if estado == "needs_input":
        return f"A execução {curta} {RESPONDA_NO_PAINEL}; {erro}. Conto aqui o desfecho."
    return (f"A execução {curta} ainda não começou, depois de um erro interno ao iniciar (está no log da Central). "
            "Conto aqui o desfecho.")
#: O que o dono ouve quando a mensagem vai à orquestradora (28.28), pelo motivo do repasse. Nunca o texto do extrator do
#: painel: "Diga onde ou por quem" não responde a uma pergunta.
RESPOSTA_DO_REPASSE = {
    "comando": "Recado guardado para a orquestradora (não executado).",
    "reply": "Recado guardado para a orquestradora (não executado).",
    "pergunta": "Recebi sua pergunta: a resposta vem por aqui, em resposta a esta mensagem.",
    "continuacao": "Recebi, junto com a mensagem anterior: a resposta vem por aqui, em resposta a esta mensagem.",
    "sem_destino": ("Não sei se isso é um pedido para um aparelho ou uma pergunta para mim. Se é pergunta, já repassei: a "
                    "ANA responde por aqui. Se é pedido, mande de novo dizendo o aparelho (ex.: \"no android-12\")."),
    # 28.30: o comentário do dono num cartão e o sim ou o não dele no Telegram.
    "comentario": "Recebi o seu comentário. Pedi a sua confirmação no Telegram: o sim de lá é que vale.",
    "comentario_sim": "Confirmado: repassei à orquestradora, que age e responde no cartão.",
    "comentario_nao": "Entendido: o comentário fica sem efeito. Avisei a orquestradora.",
}
#: 28.30: as respostas do dono ao pedido de confirmação de um comentário seu no Trello.
REPASSES_DO_COMENTARIO = ("comentario_sim", "comentario_nao")
RESPOSTA_COMENTARIO_MUDOU = "O comentário mudou depois do pedido: nada foi repassado. Comente de novo no cartão."
RESPOSTA_COMENTARIO_APAGADO = "O comentário foi apagado depois do pedido: nada foi repassado."
RESPOSTA_COMENTARIO_SEM_CONFERIR = ("Não consegui conferir o comentário no Trello agora: nada foi repassado. Responda sim "
                                    "de novo daqui a pouco.")


class ConferenciaDeComentario(Protocol):
    """28.30: o que a conversa do Telegram precisa do Trello para o sim do dono valer só para o comentário que ele viu.
    `conferir(action)` devolve (`igual` | `mudou` | `apagado` | `sem_conferir` | `desconhecido`, texto gravado)."""

    async def conferir(self, action: str) -> tuple[str, str | None]: ...


#: 28.30 e a entrada 1256 de 04/10: o recado que a Central não conseguiu tratar não fica mudo. Frase fixa, sem eco.
RESPOSTA_FALHA_INTERNA = ("Não consegui tratar este recado por um erro aqui dentro; ele ficou guardado e a orquestradora vai "
                          "olhar. /ajuda mostra os comandos.")
#: A credencial é recusada sem guardar e apagada do canal quando ele deixa (contrato dos canais, §7); a resposta nunca
#: ecoa o texto.
RESPOSTA_CREDENCIAL = ("Isso parece senha ou código: não guardei, não repassei e apaguei a mensagem do chat. Senha "
                       "fica na conta da persona; grave pelo painel.")
RESPOSTA_CREDENCIAL_SEM_APAGAR = ("Isso parece senha ou código: não guardei e não repassei, mas não consegui apagar a "
                                  "mensagem daqui. Apague-a do chat. Senha fica na conta da persona; grave pelo painel.")
#: A resposta a uma execução que pergunta por senha, código, 2FA ou token é credencial qualquer que seja a forma do texto
#: (ADR-040: credencial só pelo canal sensível). Decide pelo contexto, então "kiwi2024!" também cai aqui.
_ORIENTACAO = "A senha fica guardada na conta da persona; o código de verificação se digita no aparelho."
RESPOSTA_PERGUNTA_CREDENCIAL = ("Essa execução pergunta por senha, código ou verificação: não guardei, não repassei e "
                                "apaguei a mensagem do chat. " + _ORIENTACAO)
#: O curto recusado pode ter sido um pedido de verdade: a resposta diz como mandá-lo.
_SE_ERA_PEDIDO = " Se era um pedido, escreva com mais detalhe (ou use /para <aparelho> <objetivo>)."
RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR = ("Essa execução pergunta por senha, código ou verificação: não guardei e não "
                                           "repassei, mas não consegui apagar a mensagem daqui. Apague-a do chat. "
                                           + _ORIENTACAO)
RESPOSTA_VENCIDA = "Esta prévia venceu; mande o pedido de novo."
RESPOSTA_PRESA = ("A criação deste pedido foi interrompida antes de terminar. Confira no painel se a execução existe e, "
                  "se não, mande o pedido de novo.")
RESPOSTA_PRESA_NA_PORTA = ("A aprovação deste plano foi interrompida antes de terminar: nada foi aprovado nem iniciado, "
                           "e a execução foi cancelada. Mande o pedido de novo.")
#: 28.27 (decisão da orquestradora, 04/10 22:59Z): o botão de um retrato velho não aprova o novo.
RESPOSTA_PREVIA_MUDOU = "Essa prévia mudou: nada foi aprovado. Use a prévia nova, a mais recente desta conversa."
RESPOSTA_PREVIA_NAO_SAIU = ("Não consegui mandar a prévia do plano {curta}: nada foi aprovado nem iniciado, e a "
                            "execução foi cancelada. Mande o pedido de novo.")
RESPOSTA_ANTIGAS = ("{quantas} há mais de {min} min (a Central estava fora do ar) e não {foi} tratada{s}: nada foi "
                   "aprovado, vetado nem executado por {ela}. Mande de novo o que ainda valer.")
RESPOSTA_LONGA = "Mensagem longa demais para a conversa (limite de {n} caracteres): não guardei. Use o painel."
#: Anexos (28.24). O motivo da recusa vai ao dono em português simples e nunca ecoa o conteúdo nem o nome do arquivo.
#: Quanto um anexo `pendente` espera antes de a volta seguinte retomá-lo (o download normal leva segundos): passou disso,
#: a Central caiu entre gravar a mensagem e baixar o arquivo.
ANEXO_PRESO_S = 60.0
RESPOSTA_ANEXO_OK = "Recebi {rotulo} ({tamanho}). Guardei na Central (anexo {id})."
RESPOSTA_ANEXO_RECUSADO = "Não guardei o anexo: {motivo}"
MOTIVO_TIPO_FORA = "esse tipo de arquivo não é aceito. Aceito imagem (JPEG, PNG ou WEBP), PDF e texto."
_MOTIVO_GRANDE = "o arquivo é grande demais (o limite é {max})."
MOTIVO_ANEXOS_DESLIGADOS = "os anexos estão desligados na Central."
MOTIVO_SEM_ANEXO_NO_CANAL = "este canal não baixa anexos."
MOTIVO_NAO_REGISTROU = "não consegui registrar o anexo agora. Mande de novo."
MOTIVO_NAO_BAIXOU = "não consegui baixar o arquivo do canal. Mande de novo."
#: O mime genérico não é declaração de tipo: vale o que o conteúdo mostrar.
_MIME_GENERICO = "application/octet-stream"
#: Os campos da mensagem do Telegram que são anexo mas a Central não guarda, e o motivo dito ao dono.
_ANEXOS_RECUSADOS = {
    "voice": ("voz", "mensagem de voz não é aceita."),
    "audio": ("audio", "áudio não é aceito."),
    "video": ("video", "vídeo não é aceito."),
    "video_note": ("video", "vídeo não é aceito."),
    "animation": ("animacao", "animação (GIF) não é aceita."),
    "sticker": ("figurinha", "figurinha não é aceita."),
}
#: Só dígitos (com espaço ou hífen entre eles), de 4 a 8: o formato de um código de verificação.
_CODIGO = re.compile(r"[\s-]*(?:\d[\s-]?){4,8}[\s-]*")
#: Com uma pergunta de senha aberta, o texto livre (ou o recado) com até isto de palavras é tratado como a senha.
MAX_PALAVRAS_SENSIVEL = 3
#: A causa provável de cada recusa definitiva do `getUpdates` (E7), para a saúde orientar sem mandar trocar o token
#: quando o problema é outro. O 404 da Bot API é o bot que ela não acha pelo token (token malformado ou de outro bot).
_CAUSA_DA_RECUSA = {
    400: "pedido inválido ao getUpdates (parâmetro ou offset); não é o token",
    401: "token revogado ou trocado no BotFather",
    403: "o bot foi bloqueado ou removido da conversa",
    404: "token malformado ou de um bot que não existe mais",
}
#: Pausa entre voltas quando a entrada está desligada ou sem liderança (o long-poll é a espera de verdade).
OCIOSO_S = 15.0
#: Quanto tempo uma linha pode ficar em `executando` sem execução criada antes de ser dada como interrompida.
PRESA_S = 300.0
#: A linha ficou `feita` e a execução voltou (ou ficou) em `planned` sem ninguém iniciar nem cancelar: o `planning` de uma
#: prévia termina em `planned`. Ninguém mais a inicia pelo canal (a linha já não tem botão), e esquecida ela trava o
#: despacho do aprendizado. Depois disto (bem acima do `ttl_previa_s`, para o dono poder iniciá-la no painel), é
#: cancelada, e o desfecho "cancelada" fecha a linha (28.38).
PLANO_ESQUECIDO_S = 3600.0
#: As fases da linha do Executar (28.27), na `previa`: o plano sendo feito (o vigia espera `planned`) e a prévia da
#: porta mostrada (o "Executar (aprova N)" manda os pares do retrato).
FASE_PLANEJANDO = "planejando"
FASE_PORTA = "porta"
#: Espera padrão depois de uma falha de rede ou 5xx na leitura (o 429 manda a dele; o definitivo, `espera_conflito_s`).
ESPERA_FALHA_S = 5.0
Linha = dict[str, object]


#: O código do erro do caminho comum (`RunError`, 409) quando a resposta a uma pergunta é credencial. É final.
CREDENCIAL_NA_RESPOSTA = "credencial_na_resposta"


class RecusaDaCentral(Exception):
    """O serviço do painel recusou (o pré-voo, a aprovação já decidida, a execução que não espera mais resposta). A
    mensagem é a mesma que o painel mostraria, e volta à conversa como texto. `codigo` é o do erro do serviço, quando há."""

    def __init__(self, mensagem: str, codigo: str | None = None):
        super().__init__(mensagem)
        self.codigo = codigo


class PlanoMudou(RecusaDaCentral):
    """O 409 `plano_mudou` do "Executar (aprova N)" (30.61): algum item não é mais o que o dono viu e NADA foi gravado.
    `previa` é a prévia nova da porta, que volta ao dono (28.27)."""

    def __init__(self, mensagem: str, previa: dict[str, object], mudaram: list[dict[str, object]]):
        super().__init__(mensagem, "plano_mudou")
        self.previa = previa
        self.mudaram = mudaram


@dataclass(frozen=True)
class Previa:
    """A prévia de alvos do painel, resumida para uma linha e para o botão Executar."""

    alvos: list[dict[str, object]]
    perguntas: list[str]
    comando: str
    avisos: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pendencia:
    tipo: str          # 'aprovacao' | 'pergunta'
    ident: str
    resumo: str


@dataclass(frozen=True)
class Captura:
    """A tela de um aparelho (28.24, exceção (a)): a imagem, ou o motivo, em português simples, de não haver."""

    conteudo: bytes | None = None
    mime: str = "image/jpeg"
    motivo: str | None = None


class PortasDaCentral(Protocol):
    """O que a conversa pode fazer, pelos MESMOS serviços das rotas do painel."""

    def status(self) -> str: ...
    def pendencias(self) -> list[Pendencia]: ...
    def aprovacoes_pendentes(self) -> list[str]: ...
    def execucoes_esperando(self) -> list[str]: ...
    def decidir(self, approval_id: str, verbo: str, nota: str | None = None) -> str: ...
    def responder(self, run_id: str, texto: str) -> tuple[str, str]: ...
    def previa(self, texto: str, instance_ids: list[str] | None = None) -> Previa: ...
    def criar(self, texto: str, alvos: list[dict[str, object]], chave: str, modo: str = "execute") -> tuple[str, str]:
        """A execução do pedido, pelo MESMO `RunService.create` da rota. `modo="plan"` (28.27): para em `planned`, e quem
        inicia é o gesto do dono na prévia da porta (ou o vigia, quando nada pede o sim dele)."""
        ...
    def estado_da_execucao(self, run_id: str) -> str | None:
        """O `status` da execução, ou `None` quando ela não existe."""
        ...
    def execucao_da_chave(self, chave: str) -> str | None:
        """O id da execução criada com esta chave de idempotência, ou `None` (a linha presa antes de gravar o `run_id`)."""
        ...
    def porta(self, run_id: str) -> dict[str, object]:
        """A prévia da porta (30.61, `porta_do_plano.previa_da_porta`): a recusa sobe como `RecusaDaCentral`."""
        ...
    def aprovar_plano(self, run_id: str, aprovar: list[tuple[str, str]], *,
                      vista_em: str | None = None) -> dict[str, object]:
        """"Aprovar N e iniciar" (30.61, `porta_do_plano.aprovar_plano`), com os pares `(step_id, chave)` que o dono VIU
        e o `vista_em` da prévia mostrada (G1b: sem ele, com par a aprovar, a Central recusa como `plano_mudou`).
        O 409 `plano_mudou` sobe como `PlanoMudou`, com a prévia nova; o resto, como `RecusaDaCentral`."""
        ...
    def iniciar(self, run_id: str) -> None:
        """Inicia a execução `planned` sem a porta (a prévia dela não pôde ser lida): a porta decide no despacho."""
        ...
    def cancelar(self, run_id: str, *, gesto: bool = True) -> None:
        """Só a execução `planned`. `gesto=False`: ninguém fez o gesto (a faxina do plano esquecido, 28.38), e o sinal
        `cancelou_execucao` não é gravado."""
        ...
    def imagem_da_etapa(self, run_id: str, step_id: str) -> tuple[bytes, str] | None:
        """Os bytes e o mime da imagem que a etapa vai publicar (`image_id`), ou `None`. Quem chama confere o sha256."""
        ...
    def online(self) -> list[str]: ...
    async def captura(self, instance_id: str) -> Captura:
        """A tela atual do aparelho pela MESMA prévia do painel (tela sensível não sai). Não executa nada nele."""
        ...
    async def ler_anexo(self, anexo_id: int) -> str:
        """A IA descreve a imagem que o dono mandou (28.24, F3): o texto para o dono, com o custo. A recusa (teto, falha da
        IA, gasto barrado) sobe como `RecusaDaCentral`, com a frase para o dono."""
        ...
    def desfecho(self, run_id: str) -> str | None: ...
    def pergunta_sensivel(self, ref: str | None) -> str | None:
        """O tipo da credencial que a pergunta aberta pede (`senha`, `2fa`, `codigo`, `token`, `credencial`), ou None.
        Com `ref` (id inteiro ou o fim dele), a da execução indicada; sem, alguma aberta agora (a palavra solta). Uma
        porta só, sobre as leituras públicas do caminho comum (29.52, `taskqueue/perguntas.py`): o canal não tem
        vocabulário próprio. Erro de leitura sobe: quem chama recusa na dúvida."""
        ...


@dataclass(frozen=True)
class Recebida:
    """O que chegou por um canal, já traduzido e ANTES da gramática (o contrato comum, §1). As referências são texto:
    o Telegram numera, o Trello não. `do_dono` é a identidade conferida pela tradução (o chat configurado; o membro
    dono do quadro); quem não é o dono é gravado sem texto e não é tratado."""

    id_externo: str                    # a chave do dedupe no canal: o `update_id`; o id da action
    ordem: int | None                  # a ordem da fonte quando ela numera (o offset do Telegram); senão None
    tipo: str                          # 'mensagem' | 'botao' | 'outro' (o Trello traz os dele)
    do_dono: bool
    texto: str
    ref_mensagem: str | None = None    # a mensagem da pessoa (no botão, a do bot que levava o teclado)
    responde_a: str | None = None      # a mensagem do canal a que ela responde (o fato do aviso)
    botao_id: str | None = None        # o toque num botão, para o canal tirar o relógio (`answerCallbackQuery`)
    #: Quando a pessoa escreveu (epoch, do canal). O toque num botão não traz hora própria: a idade da prévia o protege.
    escrita_em: float | None = None
    #: O chat de onde veio, se é conversa privada e a identidade que o canal dá de quem escreveu (28.18: o caminho do
    #: convidado). Só o Telegram preenche; o Trello não usa.
    chat: str | None = None
    privado: bool = False
    perfil: Mapping[str, object] | None = field(default=None, compare=False)
    #: Os anexos da mensagem (28.24), já traduzidos e ainda NÃO baixados. A legenda vem em `texto`.
    anexos: tuple[AnexoRecebido, ...] = ()


class SaidaDaConversa(Protocol):
    """O que a parte comum pede ao canal para responder. Cada canal cumpre a sua: `SaidaDoTelegram` aqui; o Trello
    comenta no cartão e não apaga conteúdo do dono (`apagar` devolve False e a resposta pede que ele apague)."""

    async def responder(self, texto: str, *, responde_a: str | None = None,
                        botoes: list[tuple[str, str]] | None = None) -> str | None: ...
    async def confirmar_botao(self, botao_id: str) -> None: ...
    async def tirar_botoes(self, ref_mensagem: str) -> None: ...
    async def apagar(self, ref_mensagem: str) -> bool: ...


class SaidaComAnexos(SaidaDaConversa, Protocol):
    """A saída que também baixa e envia arquivo (28.24). O canal que não a cumpre (o Trello, hoje) recusa o anexo com o
    motivo, sem erro. `baixar_anexo` levanta `AnexoGrandeDemais` acima do teto e `FalhaDeEnvio` na falha do canal, sempre
    sem endereço nem token na mensagem."""

    async def baixar_anexo(self, ref: str, max_bytes: int) -> bytes: ...
    async def enviar_anexo(self, conteudo: bytes, mime: str, legenda: str = "", *,
                           responde_a: str | None = None) -> str | None: ...


class SaidaDoTelegram:
    """`SaidaDaConversa` sobre o `CanalTelegram`: só converte as referências (texto na parte comum, inteiro na Bot API)."""

    def __init__(self, canal: CanalTelegram):
        self.canal = canal

    async def baixar_anexo(self, ref: str, max_bytes: int) -> bytes:
        return await self.canal.baixar_anexo(ref, max_bytes)

    async def enviar_anexo(self, conteudo: bytes, mime: str, legenda: str = "", *,
                           responde_a: str | None = None) -> str | None:
        mid = await self.canal.enviar_anexo(conteudo, mime, legenda, responde_a=_num(responde_a))
        return str(mid) if mid is not None else None

    async def responder(self, texto: str, *, responde_a: str | None = None,
                        botoes: list[tuple[str, str]] | None = None) -> str | None:
        mid = await self.canal.responder(texto, responde_a=_num(responde_a), botoes=botoes)
        return str(mid) if mid is not None else None

    async def confirmar_botao(self, botao_id: str) -> None:
        await self.canal.confirmar_botao(botao_id)

    async def tirar_botoes(self, ref_mensagem: str) -> None:
        mid = _num(ref_mensagem)
        if mid is not None:
            await self.canal.tirar_botoes(mid)

    async def apagar(self, ref_mensagem: str) -> bool:
        mid = _num(ref_mensagem)
        return mid is not None and await self.canal.apagar(mid)


def parece_codigo(texto: str) -> bool:
    return bool(_CODIGO.fullmatch(texto))


def _parece_id_curto(palavra: str) -> bool:
    """Um pedaço de id curto demais para a `casar_ref` (1 a 3 caracteres de letra e número, com pelo menos um dígito):
    "a1f" é id incompleto; "ok" e "sim" são começo de nota."""
    p = palavra.strip().lower()
    return 0 < len(p) < 4 and p.isalnum() and any(c.isdigit() for c in p)


def _int(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _num(v: object) -> int | None:
    """A referência em texto do canal de volta a inteiro (o `message_id` da Bot API)."""
    if isinstance(v, str) and v.lstrip("-").isdigit():
        return int(v)
    return _int(v)


def _texto(v: object) -> str | None:
    return str(v) if v is not None else None


def _mapa(v: object) -> Mapping[str, object] | None:
    return v if isinstance(v, Mapping) else None


def _json(bruto: object) -> dict[str, object]:
    if not isinstance(bruto, str) or not bruto:
        return {}
    try:
        v = json.loads(bruto)
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def _ler_update(u: Mapping[str, object], chat_do_dono: str) -> Recebida | None:
    """A TRADUÇÃO do Telegram: uma update vira `Recebida`. A identidade é o chat privado configurado, com o mesmo
    `from.id` (só o dono; qualquer outro é gravado sem texto e sem resposta)."""
    update_id = _int(u.get("update_id"))
    if update_id is None:
        return None
    membro = _mapa(u.get("my_chat_member"))
    if membro is not None:
        # 28.18 (C-11): o bot posto ou tirado de um chat. O texto é o status novo do bot (`member`, `left`, `kicked`...).
        mchat = _mapa(membro.get("chat")) or {}
        novo = _mapa(membro.get("new_chat_member")) or {}
        return Recebida(id_externo=str(update_id), ordem=update_id, tipo="membro", do_dono=False,
                        texto=str(novo.get("status") or ""),
                        chat=str(mchat.get("id")) if mchat.get("id") is not None else None,
                        privado=mchat.get("type") == "private")
    msg, cb = _mapa(u.get("message")), _mapa(u.get("callback_query"))
    tipo = "mensagem" if msg is not None else "botao" if cb is not None else "outro"
    de = _mapa((cb if cb is not None else msg or {}).get("from"))     # quem apertou o botão / escreveu
    if cb is not None:
        msg = _mapa(cb.get("message"))      # no botão, a mensagem do bot que levava o teclado
    chat = _mapa(msg.get("chat")) if msg is not None else None
    chat_id = str(chat.get("id")) if chat and chat.get("id") is not None else ""
    autor = str(de.get("id")) if de and de.get("id") is not None else ""
    # Dono = conversa PRIVADA com o chat configurado E o autor é esse mesmo id. Num grupo ou canal o chat.id configurado
    # pode até coincidir, mas qualquer membro escreve nele: só o `from.id` identifica a pessoa.
    privado = chat is not None and chat.get("type") == "private"
    # A legenda (`caption`) de foto e arquivo vale como o texto da mensagem (28.24).
    texto = (cb.get("data") if cb is not None
             else (msg.get("text") or msg.get("caption")) if msg is not None else None) or ""
    responde = _mapa(msg.get("reply_to_message")) if msg is not None and cb is None else None
    return Recebida(id_externo=str(update_id), ordem=update_id, tipo=tipo,
                    do_dono=bool(chat_do_dono) and privado and chat_id == chat_do_dono and autor == chat_do_dono,
                    texto=str(texto),
                    ref_mensagem=_texto(_int(msg.get("message_id"))) if msg is not None else None,
                    responde_a=_texto(_int(responde.get("message_id"))) if responde is not None else None,
                    botao_id=str(cb.get("id")) if cb is not None and cb.get("id") is not None else None,
                    # no botão, `message.date` é a hora da mensagem do BOT, não a do toque: fica sem hora
                    escrita_em=float(d) if cb is None and msg is not None and (d := _int(msg.get("date"))) else None,
                    chat=chat_id or None,
                    # Privado de verdade: o chat é a própria pessoa (no Telegram, chat.id == from.id na conversa a dois).
                    privado=privado and bool(autor) and chat_id == autor,
                    perfil=dict(de) if de else None,
                    anexos=_anexos_da_mensagem(msg) if msg is not None and cb is None else ())


def _anexos_da_mensagem(msg: Mapping[str, object]) -> tuple[AnexoRecebido, ...]:
    """Os anexos de uma mensagem do Telegram. Da foto fica o MAIOR tamanho (a lista vem em várias resoluções). O nome do
    arquivo (`file_name`) nunca é lido. Voz, áudio, vídeo, animação e figurinha viram anexo já recusado, com o motivo."""
    achados: list[AnexoRecebido] = []
    fotos = msg.get("photo")
    if isinstance(fotos, list):
        candidatas = [f for f in fotos if isinstance(f, Mapping) and isinstance(f.get("file_id"), str)]
        if candidatas:
            melhor = max(candidatas, key=lambda f: (_int(f.get("file_size")) or 0,
                                                   (_int(f.get("width")) or 0) * (_int(f.get("height")) or 0)))
            achados.append(AnexoRecebido("imagem", "image/jpeg", _int(melhor.get("file_size")), str(melhor["file_id"])))
    doc = _mapa(msg.get("document"))
    # Uma animação (GIF) chega com o campo `document` também: não conta duas vezes.
    if doc is not None and isinstance(doc.get("file_id"), str) and msg.get("animation") is None:
        mime = doc.get("mime_type")
        achados.append(AnexoRecebido("documento", mime if isinstance(mime, str) else None, _int(doc.get("file_size")),
                                     str(doc["file_id"])))
    for campo, (tipo, motivo) in _ANEXOS_RECUSADOS.items():
        if msg.get(campo) is not None:
            achados.append(AnexoRecebido(tipo, recusa=motivo))
    return tuple(achados)


class ConversaDoCanal:
    """A parte COMUM aos canais (contrato dos canais, §1): `registrar` e `tratar_pendentes`. Não conhece Telegram: fala
    com o canal só pela `SaidaDaConversa` que recebe em cada chamada. Quem lê o canal (o `LeitorDoTelegram`; o do Trello
    no 32.2) traduz o que chegou em `Recebida` e chama a conversa."""

    def __init__(self, cfg: Config, repo: EntradasDoCanal, portas: PortasDaCentral, *, recusa: Callable[[str], bool],
                 redigir: Callable[[str], str], operador: str = OPERADOR_DO_TELEGRAM,
                 relogio: Callable[[], float] | None = None, anexos: ArmazemDeAnexos | None = None):
        self.cfg = cfg
        self.repo = repo
        self.portas = portas
        #: Onde os anexos do dono são guardados (28.24). Sem ele, todo anexo é recusado com o motivo.
        self.anexos = anexos
        self._recusa = recusa
        self._redigir = redigir
        #: Quem age, como VALOR (`telegram:dono` hoje; `trello:<id>` no 32.2): vai ao ContextVar da sessão.
        self.operador = operador
        self._limitada_avisada = 0.0
        #: hora de parede (epoch), para comparar com a hora em que a pessoa escreveu; injetável nos testes
        self._agora = relogio or time.time
        #: mensagens antigas desta volta (escritas com a Central fora): o dono é avisado uma vez no fim da volta
        self._antigas = 0
        #: O 429 pediu para esperar: o reenvio do desfecho não sai antes desta hora (epoch), mesmo com voltas no meio.
        self._desfecho_espera_ate = 0.0
        #: O lote dos desfechos gira (28.39): a próxima volta começa depois desta linha (0 = do começo).
        self._desfecho_depois_de = 0
        #: O "sim" ou o "não" do dono a quem chegou (28.18): (chat, autorizar) → o que responder ao dono. Só o leitor do
        #: Telegram com os convidados ligados o põe; sem ele, a resposta diz que o caminho está desligado.
        self.decidir_convidado: Callable[[str, bool], Awaitable[str]] | None = None
        #: 28.30: a conferência do comentário no Trello antes de o sim do dono valer (`AppState` liga; sem ela, repassa).
        self.comentarios: ConferenciaDeComentario | None = None

    def _idade_max_s(self) -> float:
        """Quanto uma mensagem pode ter de idade para ser tratada (E2). O canal que tem a regra dele (o Trello:
        `trello.idade_max_s`) sobrepõe; o padrão é o da conversa do Telegram."""
        return self.cfg.file.avisos.entrada.idade_max_s

    def gravar_sem_texto(self, r: Recebida, estado: str) -> bool:
        """A linha da update que não é da conversa do dono (o convidado, 28.18): sem texto, já no estado final. Devolve se
        a linha é nova."""
        return self._linha(r, None, estado)

    def gravar_falha(self, r: Recebida, erro: str) -> bool:
        """O leitor não conseguiu registrar a update: o mínimo (sem texto) como `falhou`, para a ordem andar."""
        return self._linha(r, None, "falhou", erro)

    async def enviar_ajuda_inicial(self, saida: SaidaDaConversa) -> None:
        """A ajuda vai uma vez só por canal, na primeira volta em que o canal responde."""
        if not self.repo.ajuda_ja_enviada():
            await self._enviar(saida, SAUDACAO + AJUDA, origem="ajuda")

    async def avisar_antigas(self, saida: SaidaDaConversa) -> None:
        """Avisa o dono, uma vez, das mensagens escritas com a Central fora do ar que `registrar` descartou desde o
        último aviso (E2). O leitor chama no fim da volta, depois de registrar o lote."""
        if not self._antigas:
            return
        n, self._antigas = self._antigas, 0
        minutos = round(self._idade_max_s() / 60)
        texto = RESPOSTA_ANTIGAS.format(
            quantas="1 mensagem foi escrita" if n == 1 else f"{n} mensagens foram escritas", min=minutos,
            foi="foi" if n == 1 else "foram", s="" if n == 1 else "s", ela="ela" if n == 1 else "elas")
        await self._enviar(saida, texto, origem="resposta")

    # ------------------------------------------------------------------ a parte comum aos canais
    async def tratar_pendentes(self, saida: SaidaDaConversa) -> None:
        """Trata as `recebida` do canal e conta na conversa o desfecho das execuções que ela criou. Antes, responde as
        recusas de credencial que `registrar(r, None)` gravou sem poder falar (o canal que só grava, como o webhook do
        Trello): cada uma ganha a resposta uma vez só (depois dela há uma `canal_enviadas` com o seu `entrada_id`)."""
        await self._reparar_presas(saida)
        await self._responder_recusas_sem_resposta(saida)
        await self._retomar_anexos(saida)
        for linha in self.repo.a_tratar():
            await self._tratar(saida, linha)
        await self._ver_planos(saida)
        await self._contar_desfechos(saida)

    async def _reparar_presas(self, saida: SaidaDaConversa) -> None:
        """Uma queda entre marcar `executando` e criar a execução deixaria a linha ali para sempre (o botão já foi
        consumido). Passada a idade, vira `falhou` e o dono é avisado de que o pedido não se completou. O cancelamento de
        uma execução que ficou para trás é do dono (o Executar foi dele): o operador do canal vale aqui."""
        token = OPERADOR.set(self.operador)
        try:
            await self._reparar_presas_como_dono(saida)
        finally:
            OPERADOR.reset(token)

    async def _reparar_presas_como_dono(self, saida: SaidaDaConversa) -> None:
        for linha in self.repo.presas_em_execucao(PRESA_S):
            if self.repo.marcar(self._id(linha), "falhou", erro="interrompida antes de criar a execução",
                                de=("executando",)):
                log.warning("telegram: mensagem %s presa em executando; marcada como falhou", linha.get("id"))
                # 28.27: a queda pode ter vindo DEPOIS de criar a execução só de plano; esquecida em `planned`, ela
                # trava o despacho do aprendizado. A chave de idempotência é a do Executar.
                try:
                    perdida = self.portas.execucao_da_chave(f"{self.repo.canal}:{linha.get('id_externo')}")
                except Exception:  # noqa: BLE001 - sem a leitura, a faxina das execuções a encerra
                    log.exception("telegram: execução da mensagem presa %s", linha.get("id"))
                    perdida = None
                if perdida:
                    self._cancelar_plano(perdida)         # recuperação da queda: não é gesto do dono (28.39)
                await self._responder(saida, linha, RESPOSTA_PRESA)
        for linha in self.repo.presas_na_porta(PRESA_S):
            await self._recuperar_da_porta(saida, linha)

    async def _recuperar_da_porta(self, saida: SaidaDaConversa, linha: Linha) -> None:
        """Revisão do #336 (A2): a queda veio entre o "Executar (aprova N)" e o desfecho. Se a execução ainda está em
        `planned`, nada foi aprovado: cancela e avisa. Se já seguiu, o desfecho de sempre a conta."""
        oid, run_id = self._id(linha), str(linha["run_id"])
        try:
            estado = self.portas.estado_da_execucao(run_id)
        except Exception:  # noqa: BLE001 - tenta de novo na próxima volta
            log.exception("telegram: estado da execução %s presa na porta", run_id)
            return
        if estado == "planned":
            if self.repo.marcar(oid, "falhou", erro="interrompida na porta do plano", run_id=run_id, de=("executando",)):
                self._cancelar_plano(run_id)
                await self._responder(saida, linha, RESPOSTA_PRESA_NA_PORTA)
            return
        self.repo.marcar(oid, "feita", run_id=run_id, de=("executando",))

    async def _responder_recusas_sem_resposta(self, saida: SaidaDaConversa) -> None:
        """A recusa de credencial gravada sem saída não respondeu nem apagou: responde agora, com o texto de quem NÃO
        apagou a mensagem (ela segue no canal). Só as dos últimos `idade_max_s` (se o envio falha, a próxima volta tenta
        de novo, mas não para sempre) e só as de credencial ou de pergunta sensível: a recusa por tamanho
        (`RESPOSTA_LONGA`) gravada sem saída fica sem resposta, porque não há credencial a avisar e o texto nem foi
        guardado."""
        for linha in self.repo.recusadas_sem_resposta(self._idade_max_s()):
            ident = self._id(linha)
            pergunta = "pergunta" in str(linha.get("erro") or "")
            texto = RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR if pergunta else RESPOSTA_CREDENCIAL_SEM_APAGAR
            await self._enviar(saida, texto, origem="resposta", responde_a=_texto(linha.get("ref_mensagem")),
                               entrada_id=ident)

    def _linha(self, r: Recebida, texto: str | None, estado: str = "recebida", erro: str | None = None) -> bool:
        return self.repo.gravar(id_externo=r.id_externo, ordem=r.ordem, tipo=r.tipo, do_dono=r.do_dono,
                                ref_mensagem=r.ref_mensagem, responde_a=r.responde_a, texto=texto,
                                tamanho=len(r.texto), estado=estado, erro=erro)

    async def registrar(self, r: Recebida, saida: SaidaDaConversa | None = None) -> None:
        """Grava o que chegou, já com as políticas que não esperam a gramática: quem não é o dono, o longo demais e o
        que parece credencial ficam SEM o texto e não são tratados. Releitura (mesmo `id_externo`) não faz nada.

        Sem `saida` (o canal que só grava, como o webhook do Trello, 32.2 §8.5) valem as MESMAS políticas e a mesma
        gravação, mas nada sai: não apaga a mensagem, não responde e não confirma botão. A recusa de credencial fica
        gravada `recusada` sem texto, e o `tratar_pendentes(saida)` seguinte a responde. A recusa por tamanho gravada
        sem saída fica sem resposta."""
        if not r.do_dono or r.tipo == "outro":
            self._linha(r, None, "ignorada")
            return
        if r.botao_id is not None and saida is not None:
            try:
                await saida.confirmar_botao(r.botao_id)                 # tira o relógio do botão; não decide nada
            except FalhaDeEnvio:
                pass
        limite = self.cfg.file.avisos.entrada.max_chars
        if len(r.texto) > limite:
            if r.tipo == "mensagem" and self._parece_segredo(r.texto):
                # Longa e com cara de senha (uma chave, um bloco): além de recusada, sai do chat como a curta.
                await self._recusar_credencial(saida, r, "parece credencial ou código", RESPOSTA_CREDENCIAL,
                                               RESPOSTA_CREDENCIAL_SEM_APAGAR)
            elif self._linha(r, None, "recusada", "mensagem longa demais") and saida is not None:
                await self._enviar(saida, RESPOSTA_LONGA.format(n=limite), origem="resposta", responde_a=r.ref_mensagem)
            return
        if r.tipo == "mensagem" and self._parece_segredo(r.texto):
            await self._recusar_credencial(saida, r, "parece credencial ou código", RESPOSTA_CREDENCIAL,
                                           RESPOSTA_CREDENCIAL_SEM_APAGAR)
            return
        por_contexto = self._responde_a_pergunta_de_credencial(r) if r.tipo == "mensagem" else None
        if por_contexto is not None:
            extra = _SE_ERA_PEDIDO if por_contexto == "curta" else ""
            await self._recusar_credencial(saida, r, "a pergunta da execução pede credencial",
                                           RESPOSTA_PERGUNTA_CREDENCIAL + extra,
                                           RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR + extra)
            return
        if r.escrita_em is not None and self._agora() - r.escrita_em > self._idade_max_s():
            # E2: o Telegram guarda até 24 h, e a Central fora por horas traria um "/aprovar" ou um "sim" velho que
            # executaria sem ninguém olhar. Vale em TODA subida, não só na 1ª (o descarte do histórico é só a 1ª).
            if self._linha(r, None, "ignorada", "antiga: escrita com a Central fora do ar"):
                self._antigas += 1
            return
        with self.repo.db.tx():
            # A mensagem e os anexos pendentes entram JUNTOS: uma queda depois disto deixa o `pendente` para a volta
            # seguinte retomar, e antes disto a update não foi confirmada e o Telegram a entrega de novo.
            nova = self._linha(r, r.texto)
            triados = self._triar_anexos(saida, r) if nova and r.anexos and r.tipo == "mensagem" else None
        if triados is not None:
            # Só a linha NOVA: a releitura da mesma update não baixa nem responde de novo. A recusa de texto acima já
            # saiu antes, então uma legenda com cara de credencial nunca chega a baixar o arquivo.
            ident, respostas, pendentes = triados
            for linha in pendentes:
                respostas.append(await self._baixar(saida, linha))
            if saida is not None:
                await self._enviar(saida, "\n".join(respostas), origem="resposta", responde_a=r.ref_mensagem,
                                   entrada_id=ident)

    # ------------------------------------------------------------------ anexos (28.24)
    def _anexo_recusado(self, a: AnexoRecebido, motivo: str, ident: int | None) -> str:
        if self.anexos is not None:
            self.anexos.recusar(motivo, entrada_id=ident, tamanho=a.tamanho or 0)
        return RESPOSTA_ANEXO_RECUSADO.format(motivo=motivo)

    def _triar_anexos(self, saida: SaidaDaConversa | None,
                      r: Recebida) -> tuple[int | None, list[str], list[dict[str, object]]]:
        """Decide, SEM rede, o que cada anexo do DONO pode ser: recusado já (desligado, tipo fora da lista, grande demais
        pelo declarado, canal que não baixa) ou `pendente`, a baixar. Roda na transação da mensagem. Devolve o id da
        mensagem, as respostas das recusas e as linhas `pendente`."""
        ident = self.repo.id_de(r.id_externo)
        cfg = self.cfg.file.avisos.entrada.anexos
        respostas: list[str] = []
        pendentes: list[dict[str, object]] = []
        for a in r.anexos:
            declarado = normalizar_mime(a.mime)
            if declarado == _MIME_GENERICO:
                declarado = None
            motivo: str | None = None
            if not cfg.enabled:
                motivo = MOTIVO_ANEXOS_DESLIGADOS
            elif a.recusa:
                motivo = a.recusa
            elif declarado is not None and declarado not in cfg.tipos:
                motivo = MOTIVO_TIPO_FORA
            elif a.tamanho is not None and a.tamanho > cfg.max_bytes:
                motivo = _MOTIVO_GRANDE.format(max=tamanho_legivel(cfg.max_bytes))
            elif self.anexos is None or a.ref is None or not hasattr(saida, "baixar_anexo"):
                motivo = MOTIVO_SEM_ANEXO_NO_CANAL
            elif ident is None:
                motivo = MOTIVO_NAO_REGISTROU                       # a mensagem não está gravada: sem ela o pendente não tem dono
            if motivo is not None or self.anexos is None or ident is None or a.ref is None:
                respostas.append(self._anexo_recusado(a, motivo or MOTIVO_NAO_REGISTROU, ident))
            else:
                pendentes.append(self.anexos.pendente(a.ref, entrada_id=ident, mime_declarado=declarado,
                                                      tamanho=a.tamanho or 0))
        return ident, respostas, pendentes

    def _armazem(self) -> ArmazemDeAnexos:
        """O armazém, ou `AnexoRecusado` (que as conversas já dizem ao dono): checagem explícita, que o `python -O` não remove."""
        if self.anexos is None:
            raise AnexoRecusado("Os anexos não estão disponíveis na Central.")
        return self.anexos

    def _recusa_pendente(self, linha: Mapping[str, object], motivo: str) -> str:
        self._armazem().recusar(motivo, linha_id=self._id(linha), tamanho=int(str(linha.get("bytes") or 0)))
        return RESPOSTA_ANEXO_RECUSADO.format(motivo=motivo)

    async def _baixar(self, saida: SaidaDaConversa | None, linha: Mapping[str, object]) -> str:
        """Baixa e guarda o anexo `pendente`. Sempre RESOLVE a linha (`guardado` ou `recusado`, com o motivo) e devolve o
        que dizer ao dono: a falha no download nunca fica calada nem deixa a linha pendente. Uma linha que outro líder já
        resolveu não derruba a conversa: a resposta é o resultado que ficou gravado."""
        try:
            return await self._baixar_pendente(saida, linha)
        except AnexoJaResolvido as ja:
            log.warning("anexos: o pendente %s já estava resolvido; digo o resultado gravado", linha.get("id"))
            return self._texto_do_resolvido(ja.linha)
        except AnexoRecusado as recusa:                                   # sem armazém (`_armazem`)
            return RESPOSTA_ANEXO_RECUSADO.format(motivo=recusa.motivo)

    @staticmethod
    def _texto_do_resolvido(linha: Mapping[str, object] | None) -> str:
        if linha is not None and linha.get("estado") == "guardado":
            return RESPOSTA_ANEXO_OK.format(rotulo=ROTULO.get(str(linha.get("mime")), "o arquivo"),
                                            tamanho=tamanho_legivel(int(str(linha.get("bytes") or 0))), id=linha.get("id"))
        motivo = _texto(linha.get("motivo_recusa")) if linha is not None else None
        return RESPOSTA_ANEXO_RECUSADO.format(motivo=motivo or "o anexo já foi tratado antes.")

    async def _baixar_pendente(self, saida: SaidaDaConversa | None, linha: Mapping[str, object]) -> str:
        armazem = self._armazem()
        cfg = self.cfg.file.avisos.entrada.anexos
        baixar = getattr(saida, "baixar_anexo", None)
        ref = str(linha.get("ref_externa") or "")
        if baixar is None or not ref:
            return self._recusa_pendente(linha, MOTIVO_SEM_ANEXO_NO_CANAL)
        try:
            conteudo = await baixar(ref, cfg.max_bytes)
        except AnexoGrandeDemais:
            return self._recusa_pendente(linha, _MOTIVO_GRANDE.format(max=tamanho_legivel(cfg.max_bytes)))
        except FalhaDeEnvio as falha:
            log.warning("telegram: anexo não baixado (%s)", falha.motivo)       # o motivo do canal já vem sem URL nem token
            return self._recusa_pendente(linha, MOTIVO_NAO_BAIXOU)
        try:
            guardada = armazem.guardar(conteudo, tipos=cfg.tipos, max_bytes=cfg.max_bytes,
                                       mime_declarado=_texto(linha.get("mime_declarado")), linha_id=self._id(linha))
        except AnexoRecusado as recusa:
            return self._recusa_pendente(linha, recusa.motivo)
        return RESPOSTA_ANEXO_OK.format(rotulo=ROTULO.get(str(guardada["mime"]), "o arquivo"),
                                        tamanho=tamanho_legivel(int(str(guardada["bytes"]))), id=guardada["id"])

    async def _retomar_anexos(self, saida: SaidaDaConversa) -> None:
        """A Central caiu entre gravar a mensagem do dono e baixar o anexo: a linha ficou `pendente`. Tenta baixar UMA vez
        (a referência do canal costuma valer por horas) e conta ao dono o resultado, como na hora; se o download falha, a
        linha vira `recusado` (não fica tentando) e a resposta já pede o reenvio."""
        if self.anexos is None:
            return
        for linha in self.anexos.a_retomar(ANEXO_PRESO_S):
            texto = await self._baixar(saida, linha)
            entrada = self.repo.linha(int(str(linha["entrada_id"]))) if linha.get("entrada_id") is not None else None
            await self._enviar(saida, texto, origem="resposta", entrada_id=int(str(entrada["id"])) if entrada else None,
                               responde_a=_texto(entrada.get("ref_mensagem")) if entrada else None)

    # ------------------------------------------------------------------ anexos na saída (28.24)
    async def enviar_anexo(self, saida: SaidaDaConversa, referencia: int | str | Path, legenda: str = "", *,
                           responde_a: str | None = None, entrada_id: int | None = None) -> str | None:
        """Manda ao dono um arquivo que JÁ está no armazém: `referencia` é o id do anexo, o sha256 de um anexo guardado ou
        um caminho dentro de `data/anexos`. Um caminho de fora levanta `CaminhoForaDoArmazem`; o tipo e o tamanho são
        conferidos de novo pelo conteúdo. A legenda passa pela redação de credencial; quem chama garante que ela não
        traz nome de persona, conta, e-mail, telefone nem IP (regra do dono para texto de mensagem e de cartão)."""
        cfg = self._cfg_do_envio(saida)
        conteudo, mime, sha = self._armazem().conteudo_de(referencia, tipos=cfg.tipos, max_bytes=cfg.max_bytes)
        return await self._despachar(saida, conteudo, mime, sha, legenda, responde_a, entrada_id)

    async def enviar_conteudo(self, saida: SaidaDaConversa, conteudo: bytes, legenda: str = "", *,
                              mime_declarado: str | None = None, responde_a: str | None = None,
                              entrada_id: int | None = None) -> str | None:
        """Manda ao dono um arquivo que o PRODUTO gerou (uma captura de tela do aparelho, p. ex.). O conteúdo é conferido
        como o recebido (tipo da lista pela assinatura, teto) e fica guardado em `data/anexos` com a retenção do 28.16."""
        cfg = self._cfg_do_envio(saida)
        mime = self._armazem().verificar(conteudo, tipos=cfg.tipos, max_bytes=cfg.max_bytes, mime_declarado=mime_declarado)
        return await self._despachar(saida, conteudo, mime, None, legenda, responde_a, entrada_id)

    def _cfg_do_envio(self, saida: SaidaDaConversa):  # noqa: ANN202 - o modelo de config
        cfg = self.cfg.file.avisos.entrada.anexos
        if not cfg.enabled:
            raise AnexoRecusado("Os anexos estão desligados na Central.")
        if self.anexos is None or not hasattr(saida, "enviar_anexo"):
            raise AnexoRecusado("Este canal não envia anexos.")
        return cfg

    async def _despachar(self, saida: SaidaDaConversa, conteudo: bytes, mime: str, sha: str | None, legenda: str,
                         responde_a: str | None, entrada_id: int | None) -> str | None:
        armazem = self._armazem()
        enviada = await saida.enviar_anexo(conteudo, mime, self._redigir(legenda),  # type: ignore[attr-defined]
                                           responde_a=responde_a)
        self.repo.registrar_enviada(enviada, "anexo", entrada_id=entrada_id)
        # Daqui em diante o arquivo JÁ está no chat do dono: uma falha ao guardar o rastro (erro de disco, banco) não pode
        # virar "não enviei" para quem chamou. Registra o que deu, diz a verdade no log e devolve o envio.
        try:
            if sha is None:
                # O conteúdo que o produto gerou ainda não está no armazém: guarda agora, já enviado (a linha é `saida`).
                armazem.guardar(conteudo, tipos=(mime,), max_bytes=len(conteudo), entrada_id=entrada_id, direcao="saida")
            else:
                armazem.registrar_saida(sha, mime, len(conteudo), entrada_id=entrada_id)
        except Exception as erro:  # noqa: BLE001 - o envio já aconteceu; o rastro é o que falhou
            motivo = erro.motivo if isinstance(erro, AnexoRecusado) else type(erro).__name__
            log.error("anexos: enviei o arquivo (mensagem %s), mas não consegui guardar o rastro dele (%s)", enviada, motivo)
        return enviada

    async def _recusar_credencial(self, saida: SaidaDaConversa | None, r: Recebida, motivo: str, ok: str,
                                  sem_apagar: str) -> None:
        """Grava SEM o texto (só o tamanho), apaga a mensagem do chat quando o canal deixa e responde sem ecoar nada.
        Sem `saida` só grava: a resposta fica para o `tratar_pendentes`."""
        if not self._linha(r, None, "recusada", motivo):
            return                                                      # releitura: já recusada (e apagada) antes
        if saida is None:
            return
        apagou = False
        if r.ref_mensagem is not None:
            try:
                apagou = await saida.apagar(r.ref_mensagem)
            except FalhaDeEnvio:
                apagou = False
        ident = self.repo.id_de(r.id_externo)
        if ident is not None:
            self.repo.marcar(ident, "recusada",
                             erro=f"{motivo}; " + ("apagada do chat" if apagou else "não apagada do chat"))
        await self._enviar(saida, ok if apagou else sem_apagar, origem="resposta",
                           responde_a=None if apagou else r.ref_mensagem, entrada_id=ident)

    async def _recusar_ja_gravada(self, saida: SaidaDaConversa, linha: Linha) -> None:
        """A linha já estava gravada com o texto (a triagem do canal não pegou, o serviço comum pegou): tira o texto,
        apaga a mensagem do chat quando dá, marca `recusada` e responde sem ecoar nada."""
        ident = self._id(linha)
        self.repo.apagar_texto(ident)
        apagou = False
        mid = _texto(linha.get("ref_mensagem"))
        if mid is not None:
            try:
                apagou = await saida.apagar(mid)
            except FalhaDeEnvio:
                apagou = False
        self.repo.marcar(ident, "recusada", erro="a Central recusou a resposta como credencial; "
                         + ("apagada do chat" if apagou else "não apagada do chat"), de=("recebida", "pergunta"))
        await self._enviar(saida, RESPOSTA_PERGUNTA_CREDENCIAL if apagou else RESPOSTA_PERGUNTA_CREDENCIAL_SEM_APAGAR,
                           origem="resposta", responde_a=None if apagou else mid, entrada_id=ident)

    def _responde_a_pergunta_de_credencial(self, r: Recebida) -> str | None:
        """É uma RESPOSTA (reply ao aviso de pergunta ou `/responder <id> <texto>`) a uma execução cuja pergunta pede
        senha, código, 2FA, token ou verificação? Decide pelo contexto, não pela forma do texto: "contexto" quando é a
        resposta a essa pergunta, "curta" quando é texto curto com ela aberta (pode ter sido um pedido), None quando
        passa. Na dúvida (a pergunta não pôde ser lida), recusa: o dono responde pelo painel."""
        i = self._intencao({"texto": r.texto, "responde_a": r.responde_a})
        if i.repasse in REPASSES_DO_COMENTARIO:
            # 28.30 (revisão da #314): o "sim" ao pedido de confirmação de um comentário é curto, mas é resposta a um fato
            # que não pede credencial. Sem isto, com uma pergunta de senha aberta, ele era recusado e apagado como senha.
            return None
        if i.tipo in ("livre", "orquestradora"):
            # E6: o texto livre E o recado à orquestradora (reply a mensagem que a Central não mandou, ou `/orq`) são
            # repassados com o texto; com uma pergunta de senha aberta, o curto é tratado como a senha.
            return "curta" if self._curta_com_pergunta_sensivel(i.texto) else None
        if i.tipo == "desconhecida" and r.texto.strip().lower().startswith("/responder"):
            # "/responder kiwi2024" sem id não vira resposta, mas a linha guardaria o texto
            return "curta" if self._curta_com_pergunta_sensivel(r.texto.strip()[len("/responder"):]) else None
        if i.tipo != "responder" or not i.ref:
            return None
        try:
            # O vocabulário é o do caminho comum (29.52), não uma lista do canal.
            return "contexto" if self.portas.pergunta_sensivel(i.ref) is not None else None
        except Exception as exc:  # noqa: BLE001 - sem ler a pergunta não há como afirmar que a resposta é inofensiva
            log.warning("telegram: pergunta da execução não lida (%s); a resposta é recusada por precaução",
                        type(exc).__name__)
            return "contexto"

    def _curta_com_pergunta_sensivel(self, texto: str) -> bool:
        """A senha digitada como mensagem NORMAL (sem reply ao aviso e sem `/responder <id>`) enquanto uma execução
        espera credencial. Um pedido de verdade é uma frase; uma senha é curta, e às vezes com espaço ("kiwi 2024", E6
        da revisão): até `MAX_PALAVRAS_SENSIVEL` palavras é recusado, e a resposta pede o pedido com mais detalhe. O que
        a triagem reconhece já foi recusado antes, com ou sem pergunta aberta. Sem poder ler as perguntas, falha
        fechada: o curto é recusado."""
        if not texto.split() or len(texto.split()) > MAX_PALAVRAS_SENSIVEL:
            return False
        try:
            return self.portas.pergunta_sensivel(None) is not None
        except Exception as exc:  # noqa: BLE001 - sem ler as perguntas, a palavra só é tratada como credencial
            log.warning("telegram: perguntas abertas não lidas (%s); a palavra solta é recusada por precaução",
                        type(exc).__name__)
            return True

    def _parece_segredo(self, texto: str) -> bool:
        if self._recusa(texto) or parece_codigo(texto):
            return True
        partes = texto.strip().split(maxsplit=2)          # "/responder <id> 123456": a resposta é o que importa
        return len(partes) == 3 and partes[0].lower().startswith("/responder") and parece_codigo(partes[2])

    # ------------------------------------------------------------------ tratar
    async def _tratar(self, saida: SaidaDaConversa, linha: Linha) -> None:
        token = OPERADOR.set(self.operador)
        try:
            if linha["tipo"] == "botao":
                await self._botao(saida, linha)
                return
            cfg = self.cfg.file.avisos.entrada
            if self.repo.do_dono_na_janela(60, ate_id=self._id(linha)) > cfg.limite_por_min:
                self.repo.marcar(self._id(linha), "limitada", de=("recebida",))
                if time.monotonic() - self._limitada_avisada > 60:
                    self._limitada_avisada = time.monotonic()
                    await self._responder(saida, linha, f"Mais de {cfg.limite_por_min} mensagens por minuto: esta não "
                                                        "foi tratada. Espere um pouco e mande de novo.")
                return
            await self._agir(saida, linha, self._intencao(linha))
        except RecusaDaCentral as recusa:
            texto = self._redigir(str(recusa))[:500]
            self.repo.marcar(self._id(linha), "falhou", erro="recusada pela Central", resposta=texto,
                             de=("recebida", "pergunta"))
            await self._responder(saida, linha, texto)
        except Exception as exc:  # uma mensagem ruim não para a conversa
            log.exception("telegram: mensagem %s", linha.get("id"))
            if self.repo.marcar(self._id(linha), "falhou", erro=type(exc).__name__, de=("recebida",)):
                # 28.30 (entrada 1256): a falha não fica muda. O reply a esta resposta segue à orquestradora (a
                # continuação do 28.28 leva o texto da linha `falhou`). Se nem a resposta sai, só o log.
                try:
                    await self._responder(saida, linha, RESPOSTA_FALHA_INTERNA)
                except Exception:  # noqa: BLE001 - a resposta da falha não pode derrubar a conversa
                    log.exception("telegram: a resposta da falha da mensagem %s não saiu", linha.get("id"))
        finally:
            OPERADOR.reset(token)

    @staticmethod
    def _id(linha: Mapping[str, object]) -> int:
        return int(str(linha["id"]))

    def _intencao(self, linha: Linha) -> Intencao:
        texto = str(linha.get("texto") or "")
        responde_a = _texto(linha.get("responde_a"))
        enviada = self.repo.enviada(responde_a) if responde_a is not None else None
        # Regra do CANAL (decisão (e)): reply a uma mensagem do bot que a Central não mandou é da orquestradora; reply
        # a uma mensagem da própria pessoa não é reply ao bot. A gramática comum só conhece o `/orq`.
        if responde_a is not None and enviada is None and not self.repo.da_pessoa(responde_a) and texto.strip():
            return Intencao("orquestradora", texto=texto.strip(), repasse="reply")
        fato = enviada.get("fato") if enviada is not None else None
        if enviada is None and responde_a is not None and self.anexos is not None:
            fato = self._fato_do_anexo(responde_a)
        # Reply a uma resposta NOSSA sem fato (28.28): continua a conversa da mensagem que a gerou. Se aquela foi
        # repassada à orquestradora (ou falhou), esta vai junto, com o texto de antes, e não vira pedido novo ("no
        # trello" depois de uma pergunta virava "Diga onde ou por quem").
        if enviada is not None and not fato and enviada.get("entrada_id") is not None and texto.strip():
            antes = self.repo.linha(int(str(enviada["entrada_id"])))
            if antes is not None and str(antes.get("estado")) in ("orquestradora", "falhou") and antes.get("texto"):
                return Intencao("orquestradora", texto=f"{str(antes['texto']).strip()} — {texto.strip()}",
                                repasse="continuacao")
        return rotear(texto, fato=str(fato) if fato else None)

    def _fato_do_anexo(self, responde_a: str) -> str | None:
        """O reply do dono a uma foto dele vira o fato `anexo:<id>` (a 1ª imagem GUARDADA da mensagem respondida); sem foto
        guardada ali, nada muda e a mensagem segue a gramática comum (28.24, F3)."""
        entrada = self.repo.entrada_da_pessoa(responde_a)
        if entrada is None or self.anexos is None:
            return None
        for a in self.anexos.da_entrada(entrada):
            if a.get("estado") == "guardado" and str(a.get("mime") or "").startswith("image/"):
                return f"anexo:{a['id']}"
        return None

    async def _agir(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        if i.tipo == "vazia":
            self.repo.marcar(self._id(linha), "ignorada", intencao=i.tipo, de=("recebida",))
        elif i.tipo == "orquestradora" and i.repasse in REPASSES_DO_COMENTARIO:
            await self._repassar_comentario(saida, linha, i)
        elif i.tipo == "orquestradora":
            await self._repassar(saida, linha, i)
        elif i.tipo == "ajuda":
            await self._feita(saida, linha, i, AJUDA)
        elif i.tipo == "identidade":
            await self._feita(saida, linha, i, RESPOSTA_IDENTIDADE)
        elif i.tipo in ("autorizar_convidado", "recusar_convidado"):
            if self.decidir_convidado is None or not i.ref:
                texto = "Os convidados do Telegram estão desligados: nada mudou."
            else:
                texto = await self.decidir_convidado(i.ref, i.tipo == "autorizar_convidado")
            await self._feita(saida, linha, i, texto, alvo=f"convidado:{i.ref}" if i.ref else None)
        elif i.tipo == "captura":
            await self._captura(saida, linha, i)
        elif i.tipo == "ler_anexo":
            await self._ler_anexo(saida, linha, i)
        elif i.tipo == "desconhecida":
            await self._feita(saida, linha, i, f"{i.motivo or 'Não entendi.'} /ajuda mostra os comandos.")
        elif i.tipo == "status":
            await self._feita(saida, linha, i, self.portas.status())
        elif i.tipo == "pendencias":
            await self._feita(saida, linha, i, self._texto_pendencias())
        elif i.tipo in ("aprovar", "vetar"):
            await self._decidir(saida, linha, i)
        elif i.tipo == "responder":
            await self._responder_pergunta(saida, linha, i)
        elif i.tipo == "para":
            await self._previa(saida, linha, i, texto_para_o_extrator(i.alvo or "", i.texto), None)
        elif i.tipo == "livre":
            try:
                await self._previa(saida, linha, i, i.texto, None)
            except RecusaDaCentral:
                # 28.28: texto livre que a prévia recusa (quase sempre "sem destino") não vira falha com o texto do
                # extrator do painel; vai à orquestradora, e o dono ouve as duas saídas.
                await self._repassar(saida, linha, Intencao("orquestradora", texto=i.texto, repasse="sem_destino"))

    def _nomes_de_persona(self) -> list[str]:
        """Os nomes que não saem pelo canal. A porta é opcional (as portas de teste antigas não a têm); erro de leitura
        não derruba a resposta, mas fica no log."""
        ler = getattr(self.portas, "nomes_de_persona", None)
        if ler is None:
            return []
        try:
            return list(ler())
        except Exception:  # noqa: BLE001 - a resposta sai mesmo assim; o nome do exemplo é o único risco
            log.exception("telegram: nomes de persona indisponíveis")
            return []

    async def _repassar(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        """A mensagem fica guardada para a orquestradora (estado `orquestradora`), que responde; a Canais entrega a
        resposta em reply à mensagem do dono (o caminho inteiro em docs/dominios/canais.md, 28.28)."""
        self.repo.marcar(self._id(linha), "orquestradora", intencao=i.tipo, destino="orquestradora",
                         previa={"repasse": i.repasse or "comando", "texto": i.texto},
                         de=("recebida", "pergunta"))
        await self._responder(saida, linha, RESPOSTA_DO_REPASSE.get(i.repasse or "", RESPOSTA_DO_REPASSE["comando"]))

    async def _repassar_comentario(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        """O sim ou o não ao pedido de confirmação de um comentário (28.30). O sim relê o comentário no Trello: se mudou,
        sumiu ou não deu para conferir, nada é repassado. O repasse leva o texto gravado do comentário, não só os ids."""
        estado, gravado = ("desconhecido", None)
        if self.comentarios is not None and i.ref:
            estado, gravado = await self.comentarios.conferir(i.ref)
        recusa = {"mudou": RESPOSTA_COMENTARIO_MUDOU, "apagado": RESPOSTA_COMENTARIO_APAGADO,
                  "sem_conferir": RESPOSTA_COMENTARIO_SEM_CONFERIR}.get(estado) if i.repasse == "comentario_sim" else None
        if recusa is not None:
            self.repo.marcar(self._id(linha), "feita", intencao=i.tipo, destino="central", resposta=recusa,
                             previa={"repasse": i.repasse, "texto": i.texto, "conferencia": estado},
                             de=("recebida", "pergunta"))
            await self._responder(saida, linha, recusa)
            return
        texto = f"{i.texto} Comentário: «{gravado}»" if gravado else i.texto
        await self._repassar(saida, linha, replace(i, texto=texto))

    async def _feita(self, saida: SaidaDaConversa, linha: Linha, i: Intencao, texto: str, *, alvo: str | None = None,
                     run_id: str | None = None) -> None:
        self.repo.marcar(self._id(linha), "feita", intencao=i.tipo, destino="central", alvo=alvo, run_id=run_id,
                         resposta=self._redigir(texto), de=("recebida", "pergunta", "executando"))
        await self._responder(saida, linha, texto)

    async def _captura(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        """A tela do aparelho de volta ao DONO (exceção (a) do dono, 04/10 15:17Z). Só chega aqui a linha do dono; o convidado
        não tem comando. A legenda leva só o id do aparelho. O arquivo passa pelo mesmo caminho de toda saída de anexo
        (tipo e tamanho conferidos, guardado em `data/anexos`, retenção do 28.16)."""
        alvo = i.alvo or ""
        captura = await self.portas.captura(alvo)
        alvo_do_fato = f"aparelho:{alvo}"
        if captura.conteudo is None:
            await self._feita(saida, linha, i, captura.motivo or f"Não consegui a captura do {alvo}.", alvo=alvo_do_fato)
            return
        try:
            await self.enviar_conteudo(saida, captura.conteudo, f"Captura do {alvo}", mime_declarado=captura.mime,
                                       responde_a=_texto(linha.get("ref_mensagem")), entrada_id=self._id(linha))
        except AnexoRecusado as recusa:
            await self._feita(saida, linha, i, f"Não enviei a captura: {recusa.motivo}", alvo=alvo_do_fato)
        except FalhaDeEnvio as falha:
            log.warning("telegram: captura não enviada (%s)", falha.motivo)
            await self._feita(saida, linha, i, "Tirei a captura, mas o canal não aceitou o arquivo. Tente de novo.",
                              alvo=alvo_do_fato)
        else:
            self.repo.marcar(self._id(linha), "feita", intencao=i.tipo, destino="central", alvo=alvo_do_fato,
                             resposta="captura enviada", de=("recebida", "pergunta", "executando"))

    async def _ler_anexo(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        """A IA descreve a foto do dono (28.24, F3). Sem Executar: o gasto é pequeno e tem teto por imagem; a descrição fica
        gravada e a segunda leitura não paga. A recusa vira uma frase ao dono (nada é gravado como lido)."""
        alvo = f"anexo:{i.ref}"
        try:
            texto = await self.portas.ler_anexo(int(i.ref or 0))
        except RecusaDaCentral as recusa:
            await self._feita(saida, linha, i, str(recusa)[:500], alvo=alvo)
            return
        await self._feita(saida, linha, i, texto, alvo=alvo)

    def _texto_pendencias(self) -> str:
        itens = self.portas.pendencias()
        if not itens:
            return "Nada espera você agora."
        linhas = [f"{len(itens)} esperando você:"]
        for p in itens[:15]:
            rotulo = "aprovar" if p.tipo == "aprovacao" else "responder"
            linhas.append(f"- {p.ident[-6:]} ({rotulo}): {self._redigir(p.resumo)[:200]}")
        if len(itens) > 15:
            linhas.append(f"... e mais {len(itens) - 15} no painel.")
        linhas.append("Decida com /aprovar <id>, /vetar <id> ou /responder <id> <texto>, ou responda ao aviso.")
        return "\n".join(linhas)

    def _outros_ids(self) -> list[str]:
        """Os ids que um id digitado pode estar indicando além das aprovações pendentes: as aprovações em qualquer
        estado (`ids_de_aprovacoes`, se a porta tiver) e as execuções esperando resposta. Erro de leitura não decide nada
        a mais: devolve o que conseguiu."""
        ids: list[str] = []
        todas = getattr(self.portas, "ids_de_aprovacoes", None)
        for ler in ((todas,) if callable(todas) else ()) + (self.portas.execucoes_esperando,):
            try:
                ids.extend(ler())
            except Exception:  # noqa: BLE001 - na dúvida, a conferência fica com as pendentes
                log.exception("conversa: falha ao ler os ids para conferir o id digitado")
        return ids

    @staticmethod
    def _um_id(ref: str, ids: list[str], o_que: str) -> tuple[str | None, str]:
        achados = casar_ref(ref, ids)
        if not achados:
            return None, f"Não achei {o_que} com o id {ref}. /pendencias mostra os ids."
        if len(achados) > 1:
            return None, f"O id {ref} serve para mais de um ({', '.join(a[-8:] for a in achados)}): use mais caracteres."
        return achados[0], ""

    async def _decidir(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        pendentes = self.portas.aprovacoes_pendentes()
        aid, erro = self._um_id(i.ref or "", pendentes, "aprovação pendente")
        if aid is None:
            await self._feita(saida, linha, i, erro)
            return
        nota = i.texto or None
        if i.ref_digitado:
            # Reply a um aviso E um id digitado (28.26): se o id é de OUTRA pendência, a pessoa pode estar decidindo a
            # errada; nada se decide e a resposta diz qual é qual. Se é o mesmo item, segue, e o id sai da nota.
            # Palavra que não casa com nenhuma pendência é só o começo da nota, como antes.
            # A revisão da suíte 31 (04/10): o id de aprovação já decidida ou vencida, ou de execução esperando
            # resposta, também é "outro item"; e um pedaço curto com dígito (menos de 4) é id incompleto, não nota.
            digitados = casar_ref(i.ref_digitado, pendentes + self._outros_ids())
            if not digitados and _parece_id_curto(i.ref_digitado):
                await self._feita(saida, linha, i, (
                    f"O id {i.ref_digitado} é curto demais para eu saber qual item é (use 4 ou mais caracteres, como a "
                    "/pendencias mostra): nada foi decidido. Para decidir o aviso respondido, responda só com sim ou não."))
                return
            if digitados and aid not in digitados:
                outro = digitados[0][-6:] if len(digitados) == 1 else "mais de um item"
                await self._feita(saida, linha, i, (
                    f"O aviso respondido é do item {aid[-6:]} e o id {i.ref_digitado} é de outro ({outro}): "
                    f"nada foi decidido. Responda ao aviso só com sim ou não, ou mande /{i.tipo} {i.ref_digitado} "
                    "sem responder ao aviso."))
                return
            if digitados:
                nota = i.texto.split(maxsplit=1)[1] if len(i.texto.split(maxsplit=1)) > 1 else None
        texto = self.portas.decidir(aid, "approve" if i.tipo == "aprovar" else "reject", nota)
        await self._feita(saida, linha, i, texto, alvo=f"approval:{aid}")

    async def _responder_pergunta(self, saida: SaidaDaConversa, linha: Linha, i: Intencao) -> None:
        rid, erro = self._um_id(i.ref or "", self.portas.execucoes_esperando(), "execução esperando resposta")
        if rid is None:
            await self._feita(saida, linha, i, erro)
            return
        try:
            nova, curta = self.portas.responder(rid, i.texto)
        except RecusaDaCentral as recusa:
            if recusa.codigo != CREDENCIAL_NA_RESPOSTA:
                raise
            # O caminho comum viu credencial onde o canal não viu (a forma do texto, p. ex.): final, sem nova tentativa.
            await self._recusar_ja_gravada(saida, linha)
            return
        if self._em_planejamento(nova):
            # A execução respondida era só de plano (a do Executar pelo canal, 28.27): a sucessora também para em
            # `planned`, e o vigia mostra a porta dela. Sem isto, ela ficaria parada sem ninguém para iniciar.
            self.repo.marcar(self._id(linha), "executando", intencao=i.tipo, destino="central", alvo=f"run:{rid}",
                             run_id=nova, previa={"fase": FASE_PLANEJANDO, "curta": curta},
                             de=("recebida", "pergunta"))
            await self._responder(saida, linha, f"Respondida: a execução segue em {curta}. Confiro as travas do plano "
                                                "e conto aqui.")
            return
        await self._feita(saida, linha, i, f"Respondida: a execução segue em {curta}. Conto aqui quando terminar.",
                          alvo=f"run:{rid}", run_id=nova)

    def _em_planejamento(self, run_id: str) -> bool:
        try:
            return self.portas.estado_da_execucao(run_id) in ("planning", "planned")
        except Exception:  # noqa: BLE001 - na dúvida, o desfecho de sempre
            log.exception("telegram: estado da execução %s", run_id)
            return False

    # ------------------------------------------------------------------ prévia e botões
    async def _previa(self, saida: SaidaDaConversa, linha: Linha, i: Intencao, texto: str,
                      instance_ids: list[str] | None) -> None:
        ident = self._id(linha)
        p = self.portas.previa(texto, instance_ids)
        if p.perguntas or not p.alvos:
            online = self.portas.online()[:8]
            pergunta = " ".join(p.perguntas) or "Para qual aparelho?"
            if not online:
                await self._feita(saida, linha, i, f"{pergunta} Nenhum aparelho online agora; tente pelo painel.")
                return
            self.repo.marcar(ident, "pergunta", intencao=i.tipo, destino="central", previa={"texto": texto},
                             de=("recebida", "pergunta"))
            await self._responder(saida, linha, pergunta, origem="previa",
                                  botoes=[(a, f"a:{ident}:{a}") for a in online])
            return
        resumo = ", ".join(str(a.get("instance_id") or "a escolher")
                           + (f" · {a.get('profile_id')}" if a.get("profile_id") else "") for a in p.alvos)
        avisos = f"\n{' '.join(p.avisos)}" if p.avisos else ""
        self.repo.marcar(ident, "pergunta", intencao=i.tipo, destino="central",
                         previa={"texto": texto, "alvos": p.alvos}, de=("recebida", "pergunta"))
        await self._responder(saida, linha, f"Prévia: {resumo}\nPedido: {self._redigir(p.comando)[:300]}{avisos}",
                              origem="previa", botoes=[("Executar", f"x:{ident}"), ("Cancelar", f"c:{ident}")])

    async def _botao(self, saida: SaidaDaConversa, linha: Linha) -> None:
        """O toque num botão da prévia. A linha do botão só registra; quem muda é a linha da mensagem original, e só
        a partir de `pergunta` (o segundo toque perde no `UPDATE ... WHERE estado='pergunta'`)."""
        acao, _, resto = str(linha.get("texto") or "").partition(":")
        alvo_id, _, extra = resto.partition(":")
        original = self.repo.linha(int(alvo_id)) if alvo_id.isdigit() else None
        self.repo.marcar(self._id(linha), "feita", intencao=f"botao:{acao}", destino="central",
                         alvo=f"mensagem:{alvo_id}", de=("recebida",))
        mid = _texto(linha.get("ref_mensagem"))
        if mid is not None:
            try:
                await saida.tirar_botoes(mid)             # só a tela: a garantia é o estado da linha original
            except FalhaDeEnvio:
                pass
        if original is None or original.get("estado") != "pergunta":
            await self._responder(saida, linha, "Esta prévia já foi tratada.")
            return
        previa = _json(original.get("previa"))
        i = Intencao(str(original.get("intencao") or "livre"))
        oid = self._id(original)
        if acao == "c":
            if self.repo.marcar(oid, "cancelada", de=("pergunta",)):
                if previa.get("fase") == FASE_PORTA and previa.get("run_id"):
                    self._cancelar_plano(str(previa["run_id"]), gesto=True)    # o Cancelar é o gesto do dono
                await self._responder(saida, original, "Cancelado: nada foi executado.")
        elif acao == "p" and previa.get("fase") == FASE_PORTA:
            if not extra or extra != previa.get("marca"):
                # O botão de uma prévia velha (antes do `plano_mudou`): o sim é da mensagem que mostrou ESTE retrato.
                await self._responder(saida, original, RESPOSTA_PREVIA_MUDOU)
                return
            await self._executar_aprovando(saida, original, i, previa)
        elif acao == "a" and extra and previa.get("fase") != FASE_PORTA:
            await self._previa(saida, original, i, str(previa.get("texto") or ""), [extra])
        elif acao == "x" and isinstance(previa.get("alvos"), list) and previa.get("fase") != FASE_PORTA:
            if self.repo.idade_s(original) > self.cfg.file.avisos.entrada.ttl_previa_s:
                # O pedido pode já não valer (o aparelho mudou, a aprovação passou): quem manda de novo vê a prévia atual.
                if self.repo.marcar(oid, "cancelada", erro="prévia venceu", de=("pergunta",)):
                    await self._responder(saida, original, RESPOSTA_VENCIDA)
                return
            if not self.repo.marcar(oid, "executando", de=("pergunta",)):
                return
            alvos = [a for a in previa["alvos"] if isinstance(a, dict)]  # type: ignore[union-attr]
            try:
                # 28.27: só o plano. O vigia lê a porta quando ele fica pronto: nada pede o sim do dono, inicia; senão,
                # a prévia da porta e o "Executar (aprova N)".
                run_id, curta = self.portas.criar(str(previa.get("texto") or ""), alvos,
                                                  f"{self.repo.canal}:{original['id_externo']}", modo="plan")
            except RecusaDaCentral as recusa:
                self.repo.marcar(oid, "falhou", erro="recusada pela Central", de=("executando",))
                await self._responder(saida, original, f"Não criei a execução: {self._redigir(str(recusa))[:300]}")
                return
            except Exception:
                log.exception("telegram: criar a execução da mensagem %s", oid)
                self.repo.marcar(oid, "falhou", erro="erro interno ao criar", de=("executando",))
                await self._responder(saida, original, "Não criei a execução: erro interno (está no log da Central).")
                return
            self.repo.marcar(oid, "executando", run_id=run_id, previa={**previa, "fase": FASE_PLANEJANDO, "curta": curta},
                             de=("executando",))

    # ------------------------------------------------------------------ a porta do plano (28.27)
    async def _ver_planos(self, saida: SaidaDaConversa) -> None:
        """O vigia das execuções que o Executar criou só de plano. Pronto o plano, lê a porta; a execução que seguiu ou
        parou sem ela (pergunta, falha, cancelada por fora) vai ao desfecho de sempre. O gesto de iniciar é do dono (foi
        o Executar dele): o operador do canal vale aqui como em `_tratar`."""
        token = OPERADOR.set(self.operador)
        try:
            await self._ver_planos_como_dono(saida)
        finally:
            OPERADOR.reset(token)

    async def _ver_planos_como_dono(self, saida: SaidaDaConversa) -> None:
        for linha in self.repo.planejando():
            try:
                await self._ver_um_plano(saida, linha)
            except Exception:  # noqa: BLE001 - uma linha ruim não cala as outras da volta (revisão do #336, A1)
                log.exception("telegram: vigia da porta, mensagem %s", linha.get("id"))
                run_id = str(linha.get("run_id") or "")
                await self._erro_ao_iniciar(saida, linha, ("executando", "pergunta"), run_id, run_id[-6:],
                                            "erro interno no vigia da porta")

    async def _abandonar_porta(self, saida: SaidaDaConversa, linha: Linha, de: tuple[str, ...], erro: str,
                               texto: str) -> None:
        """A linha vira `falhou`, a execução só de plano é cancelada (se ainda está em `planned`) e o dono é avisado
        uma vez: quem perde o `UPDATE ... WHERE estado IN` não repete o aviso."""
        run_id = str(linha.get("run_id") or "")
        if not self.repo.marcar(self._id(linha), "falhou", erro=erro, run_id=run_id or None, de=de):
            return
        self._cancelar_plano(run_id)
        try:
            await self._responder(saida, linha, texto)
        except Exception:  # noqa: BLE001 - o aviso é uma tentativa só; a linha já diz `falhou`
            log.exception("telegram: aviso do abandono da porta, mensagem %s", linha.get("id"))

    async def _ver_um_plano(self, saida: SaidaDaConversa, linha: Linha) -> None:
        previa = _json(linha.get("previa"))
        if previa.get("fase") != FASE_PLANEJANDO:
            return
        oid, run_id = self._id(linha), str(linha["run_id"])
        curta = str(previa.get("curta") or run_id[-6:])
        i = Intencao(str(linha.get("intencao") or "livre"))
        try:
            estado = self.portas.estado_da_execucao(run_id)
        except Exception:  # noqa: BLE001 - tenta de novo na próxima volta
            log.exception("telegram: estado da execução %s", run_id)
            return
        if estado == "planning":
            return
        if estado is None:
            if self.repo.marcar(oid, "falhou", erro="execução não encontrada", de=("executando",)):
                await self._responder(saida, linha, f"Não achei a execução {curta}: nada foi iniciado.")
            return
        if estado != "planned":
            self.repo.marcar(oid, "feita", run_id=run_id, de=("executando",))
            return
        try:
            previa_da_porta = self.portas.porta(run_id)
        except Exception as exc:  # noqa: BLE001 - sem a prévia, a porta decide no despacho, como antes do 28.27
            log.warning("telegram: prévia da porta da execução %s indisponível (%s)", run_id, type(exc).__name__)
            await self._iniciar_sem_porta(saida, linha, i, run_id, curta)
            return
        await self._mostrar_porta(saida, linha, i, run_id, curta, previa_da_porta, de=("executando",))

    async def _iniciar_sem_porta(self, saida: SaidaDaConversa, linha: Linha, i: Intencao, run_id: str,
                                 curta: str) -> None:
        try:
            self.portas.iniciar(run_id)
        except RecusaDaCentral as recusa:
            # Outro gesto pode ter iniciado antes: o `_recusa_na_porta` relê o estado e não diz "não iniciei" (28.36).
            await self._recusa_na_porta(saida, linha, ("executando", "pergunta"), run_id, curta, recusa)
            return
        except Exception:
            # Revisão do #336 (A1): sem isto a exceção subia do vigia, a linha ficava em `planejando` e calava as
            # linhas seguintes da volta.
            log.exception("telegram: iniciar sem a porta a execução %s", run_id)
            await self._erro_ao_iniciar(saida, linha, ("executando", "pergunta"), run_id, curta,
                                        "erro interno ao iniciar")
            return
        await self._feita(saida, linha, i, f"Execução {curta} iniciada; as travas se decidem na execução (a prévia "
                                           "delas não pôde ser lida agora). Conto aqui quando terminar.", run_id=run_id)

    async def _mostrar_porta(self, saida: SaidaDaConversa, linha: Linha, i: Intencao, run_id: str, curta: str,
                             previa_da_porta: Mapping[str, object], *, de: tuple[str, ...], mudou: int = 0) -> None:
        """N = 0 (nada aprovável pelo canal): inicia com `aprovar=[]` e manda uma linha. N > 0: a prévia da porta, os
        botões e as imagens; a linha volta a `pergunta` com o retrato do que o dono viu (os pares que o Executar manda).
        `mudou`: quantos itens o 409 `plano_mudou` apontou (0 na primeira prévia)."""
        nomes = self._nomes_de_persona()
        imagens: dict[str, tuple[bytes, str]] = {}

        def conferida(item: Mapping[str, object]) -> bool:
            sid, sha = str(item.get("step_id") or ""), item.get("imagem_sha256")
            if not sid or not isinstance(sha, str) or not sha:
                return False
            try:
                imagem = self.portas.imagem_da_etapa(run_id, sid)
            except Exception:  # noqa: BLE001 - sem a imagem, o item fica fora do canal
                log.exception("telegram: imagem da etapa %s", sid)
                return False
            if imagem is None or hashlib.sha256(imagem[0]).hexdigest() != sha:
                return False
            imagens[sid] = imagem
            return True

        leitura = ler_porta(previa_da_porta, nomes, self._redigir, conferida)
        oid = self._id(linha)
        if not leitura.aprovar:
            try:
                self.portas.aprovar_plano(run_id, [])
            except RecusaDaCentral as recusa:
                await self._recusa_na_porta(saida, linha, de, run_id, curta, recusa)
                return
            except Exception:
                # Sem isto a linha ficaria em `executando` e o vigia tentaria de novo a cada volta, para sempre.
                log.exception("telegram: iniciar o plano da execução %s", run_id)
                await self._erro_ao_iniciar(saida, linha, de, run_id, curta, "erro interno ao iniciar o plano")
                return
            await self._feita(saida, linha, i, linha_sem_aprovacao(curta, leitura, plano_mudou=bool(mudou)),
                              run_id=run_id)
            return
        pares = [[sid, chave] for sid, chave in leitura.aprovar]
        retrato = {**_json(linha.get("previa")), "fase": FASE_PORTA, "run_id": run_id, "curta": curta,
                   "hash_do_plano": previa_da_porta.get("hash_do_plano"),
                   "validade_ate": previa_da_porta.get("validade_ate"), "aprovar": pares,
                   "vista_em": previa_da_porta.get("vista_em"),          # G1b: o instante da prévia que o dono viu
                   "marca": marca_do_retrato(previa_da_porta.get("hash_do_plano"), pares)}
        if not self.repo.marcar(oid, "pergunta", run_id=run_id, previa=retrato, de=de):
            return
        mensagens = mensagens_da_porta(previa_da_porta, curta, leitura, nomes, self._redigir)
        n = len(leitura.aprovar)
        try:
            if mudou:
                await self._responder(saida, linha, f"O plano mudou em {mudou} {'item' if mudou == 1 else 'itens'} "
                                                    "desde a prévia: nada foi gravado. Segue a prévia nova.",
                                      exigir=True)
            for k, texto in enumerate(mensagens, 1):
                # O botão leva a marca DESTE retrato: o de uma prévia velha não aprova o novo (decisão da orquestradora,
                # 04/10 22:59Z).
                botoes = [(f"Executar (aprova {n})", f"p:{oid}:{retrato['marca']}"), ("Cancelar", f"c:{oid}")] \
                    if k == len(mensagens) else None
                await self._responder(saida, linha, texto, origem="previa", botoes=botoes, exigir=True)
        except Exception as falha:  # noqa: BLE001 - sem a prévia inteira, o dono não tem o que aprovar (revisão do #336)
            log.warning("telegram: a prévia da porta da execução %s não saiu (%s)", run_id, type(falha).__name__)
            await self._abandonar_porta(saida, {**linha, "run_id": run_id}, ("pergunta",), "a prévia da porta não saiu",
                                        RESPOSTA_PREVIA_NAO_SAIU.format(curta=curta))
            return
        no_sim = {sid for sid, _ in leitura.aprovar}
        for numero, item in enumerate(itens_da_previa(previa_da_porta), 1):
            sid = str(item.get("step_id"))
            imagem = imagens.get(sid) if sid in no_sim else None     # só a do item que o dono aprova por aqui
            if imagem is None:
                continue
            try:
                await self.enviar_conteudo(saida, imagem[0], f"item {numero}", mime_declarado=imagem[1],
                                           responde_a=_texto(linha.get("ref_mensagem")), entrada_id=oid)
            except (AnexoRecusado, FalhaDeEnvio) as falha:
                log.warning("telegram: imagem do item %s não saiu (%s)", numero, type(falha).__name__)

    async def _recusa_na_porta(self, saida: SaidaDaConversa, linha: Linha, de: tuple[str, ...], run_id: str,
                               curta: str, recusa: RecusaDaCentral) -> None:
        """A Central recusou iniciar. Se a execução ainda está em `planned`, a porta é abandonada (e a execução,
        cancelada); se já seguiu (`invalid_state`: outro gesto a iniciou), o desfecho de sempre a conta. Nunca "não
        iniciei" quando a execução está em andamento (28.36)."""
        try:
            estado = self.portas.estado_da_execucao(run_id)
        except Exception:  # noqa: BLE001 - na dúvida, abandona; o cancelamento confere o estado de novo
            estado = None
        motivo = self._redigir(str(recusa))[:300]
        texto = _texto_da_recusa(curta, estado, motivo)
        if estado not in ("planned", None):
            await self._seguiu(saida, linha, de, run_id, estado, texto)
            return
        await self._abandonar_porta(saida, {**linha, "run_id": run_id}, de, "recusada pela Central", texto)

    async def _erro_ao_iniciar(self, saida: SaidaDaConversa, linha: Linha, de: tuple[str, ...], run_id: str,
                               curta: str, erro: str) -> None:
        """Erro interno (não recusa) num caminho que inicia a execução. Ele pode vir DEPOIS do compare-and-set do início
        (pré-voo, objetivos, agendador): a execução já roda, e dizer "não iniciei" seria falso. Fora de `planned`, a
        linha fica feita e o desfecho de sempre a conta; em `planned` (ou sem leitura), a porta é abandonada (28.36)."""
        try:
            estado = self.portas.estado_da_execucao(run_id) if run_id else None
        except Exception:  # noqa: BLE001 - na dúvida, abandona; o cancelamento confere o estado de novo
            estado = None
        if estado not in ("planned", None):
            await self._seguiu(saida, linha, de, run_id, estado, _texto_do_erro(curta, estado))
            return
        await self._abandonar_porta(saida, {**linha, "run_id": run_id} if run_id else linha, de, erro,
                                    "Não iniciei a execução: erro interno (está no log da Central).")

    async def _seguiu(self, saida: SaidaDaConversa, linha: Linha, de: tuple[str, ...], run_id: str, estado: str,
                      texto: str) -> None:
        """A execução saiu de `planned` por outro caminho: a linha fica feita, e o desfecho de sempre a conta. Já
        TERMINAL, o desfecho é a única linha ao dono (sem isto ele leria duas: esta e a do desfecho, na volta seguinte).
        O envio não sobe: no vigia, um erro aqui calaria as linhas seguintes da volta (revisão do #346, E1)."""
        if not self.repo.marcar(self._id(linha), "feita", run_id=run_id, de=de) or estado in TERMINAIS:
            return
        try:
            await self._responder(saida, linha, texto)
        except Exception:  # noqa: BLE001 - a linha já diz `feita`; o desfecho ainda conta o fim
            log.exception("telegram: aviso da execução que seguiu, mensagem %s", linha.get("id"))

    async def _executar_aprovando(self, saida: SaidaDaConversa, original: Linha, i: Intencao,
                                  previa: Mapping[str, object]) -> None:
        """O "Executar (aprova N)": manda ao `aprovar_plano` exatamente os pares do retrato. Vencida (o TTL da prévia ou a
        validade da porta), cancela a execução `planned`. Plano mudado: nada foi gravado, e sai a prévia nova."""
        oid, run_id = self._id(original), str(previa.get("run_id") or "")
        curta = str(previa.get("curta") or run_id[-6:])
        validade = parse_iso(str(previa.get("validade_ate") or ""))
        vencida = self.repo.idade_s(original) > self.cfg.file.avisos.entrada.ttl_previa_s or (
            validade is not None and validade <= self.repo.relogio())
        if vencida:
            if self.repo.marcar(oid, "cancelada", erro="prévia venceu", de=("pergunta",)):
                self._cancelar_plano(run_id)
                await self._responder(saida, original, RESPOSTA_VENCIDA)
            return
        if not self.repo.marcar(oid, "executando", de=("pergunta",)):
            return
        bruto = previa.get("aprovar")
        aprovar = [(str(p[0]), str(p[1])) for p in bruto if isinstance(p, list) and len(p) == 2] \
            if isinstance(bruto, list) else []
        try:
            resposta = self.portas.aprovar_plano(run_id, aprovar,
                                                 vista_em=str(previa.get("vista_em") or "") or None)
        except PlanoMudou as mudou:
            # Plano novo sem sim pendente (N = 0) inicia como todo N = 0, numa linha que diz que mudou (decisão da
            # orquestradora, 04/10 22:59Z); com sim pendente, sai a prévia nova, com o botão da marca nova.
            await self._mostrar_porta(saida, original, i, run_id, curta, mudou.previa, de=("executando",),
                                      mudou=max(1, len(mudou.mudaram)))
            return
        except RecusaDaCentral as recusa:
            await self._recusa_na_porta(saida, original, ("executando",), run_id, curta, recusa)
            return
        except Exception:
            log.exception("telegram: aprovar o plano da execução %s", run_id)
            await self._erro_ao_iniciar(saida, original, ("executando",), run_id, curta, "erro interno ao aprovar o plano")
            return
        sins = resposta.get("aprovacoes")
        n = len(sins) if isinstance(sins, list) else len(aprovar)
        ate = parse_iso(str(resposta.get("validade_ate") or ""))
        validos = f", válidos até {ate:%H:%M}Z" if ate is not None else ""
        await self._feita(saida, original, i, f"Execução {curta} iniciada; {n} {'sim gravado' if n == 1 else 'sins gravados'}"
                                              f"{validos}. Conto aqui quando terminar.", run_id=run_id)

    def _cancelar_plano(self, run_id: str, *, gesto: bool = False) -> None:
        """Todo caminho que abandona a porta (Cancelar, prévia vencida, recusa, falha, linha presa) cancela a execução
        só de plano: esquecida em `planned`, ela trava o despacho do aprendizado. Só a que ainda está em `planned`: a
        recusa `invalid_state` pode ser de um plano que já foi iniciado, e esse segue. A leitura aqui só poupa a chamada;
        quem decide é o `cancelar` da porta, condicionado a `planned` num `UPDATE` só, porque outro gesto (o "Aprovar e
        iniciar" do painel) pode iniciar a execução entre a leitura e o cancelamento.

        `gesto`: só o botão Cancelar do dono. A prévia vencida, a recusa, o erro interno, a prévia que não saiu, a linha
        presa e o plano esquecido são consequência ou faxina, e o sinal `cancelou_execucao` não sai em nome de ninguém
        (28.38, F4; 28.39)."""
        if not run_id:
            return
        try:
            if self.portas.estado_da_execucao(run_id) != "planned":
                return
            self.portas.cancelar(run_id, gesto=gesto)
        except Exception:  # noqa: BLE001 - a execução `planned` não tem efeito; a faxina das execuções a encerra
            log.exception("telegram: cancelar a execução %s da prévia cancelada", run_id)

    # ------------------------------------------------------------------ desfecho na thread
    async def _contar_desfechos(self, saida: SaidaDaConversa) -> None:
        """O desfecho só fica marcado quando saiu, ou quando tentar de novo não adianta (`FalhaDeEnvio.definitiva`): na
        execução terminal ele é a ÚNICA linha ao dono (28.36), e marcado sem sair o dono não saberia do fim (28.38). A
        falha passageira para a volta (o canal está fora) e a linha tenta de novo na seguinte, nunca antes do que o 429
        pediu (`espera_s`). Sem teto de tentativas: no máximo uma falha por volta, e a linha `feita` sai pela faxina da
        retenção (28.16)."""
        if self._agora() < self._desfecho_espera_ate:
            return
        # O lote gira (28.39): com 20 linhas antigas de execução longa à frente, as novas esperariam para sempre. O cursor
        # só anda quando a volta trata o lote inteiro; a falha passageira (o `return` abaixo) repete o mesmo lote.
        linhas = self.repo.esperando_desfecho(depois_de=self._desfecho_depois_de)
        if not linhas and self._desfecho_depois_de:
            linhas = self.repo.esperando_desfecho()
        for linha in linhas:
            run_id = str(linha["run_id"])
            texto = self.portas.desfecho(run_id)
            if texto is None:
                # A linha fica `feita` ainda em `planning`: antes de 1 h de linha, a execução não pode estar 1 h em
                # `planned`. Só então se lê o estado (a volta não relê as 20 execuções em curso a cada vez).
                if self.repo.idade_s(linha) > PLANO_ESQUECIDO_S:
                    self._plano_esquecido(linha, run_id)
                continue
            if self.repo.desfecho_ja_enviado(self._id(linha)):
                # Saiu e ficou registrado, mas a marca não gravou (o banco caiu entre os dois): não repete (revisão do
                # #358, F2). O tempo esgotado DEPOIS de o canal aceitar não deixa registro e ainda pode repetir: a
                # troca é "pelo menos uma vez", porque na execução terminal esta é a única linha ao dono.
                self.repo.marcar_desfecho(self._id(linha))
                continue
            try:
                await self._responder(saida, linha, self._texto_do_desfecho(texto), origem="resultado", exigir=True)
            except FalhaDeEnvio as falha:
                if not falha.definitiva:
                    if falha.espera_s:
                        self._desfecho_espera_ate = self._agora() + falha.espera_s
                    return
                log.warning("telegram: desfecho da mensagem %s não sai nunca (%s)", linha.get("id"), falha.motivo)
            self.repo.marcar_desfecho(self._id(linha))
        self._desfecho_depois_de = self._id(linhas[-1]) if len(linhas) >= self.repo.LOTE_DESFECHO else 0

    def _plano_esquecido(self, linha: Linha, run_id: str) -> None:
        """A hora conta de quando a conversa VIU a execução em `planned` (revisão do #358, F1): `runs` não guarda a hora
        da transição, e a linha ficou `feita` ainda em `planning`. A primeira vista fica NA LINHA (28.39, G1): em memória,
        um processo que reinicia, ou um líder da trava `avisos` que alterna, a cada menos de 1 h, nunca chegaria à hora.

        O cancelamento sai SEM gesto (F4): ninguém o fez, e o sinal `cancelou_execucao` não sai em nome de ninguém."""
        try:
            estado = self.portas.estado_da_execucao(run_id)
        except Exception:  # noqa: BLE001 - sem a leitura, tenta na volta seguinte
            log.exception("telegram: estado da execução %s sem desfecho", run_id)
            return
        oid = self._id(linha)
        if estado != "planned":
            self.repo.esquecer_vista_em_planned(oid)
            return
        desde = self.repo.vista_em_planned(oid)          # grava a primeira vista, se ainda não há
        if (self.repo.relogio() - desde).total_seconds() <= PLANO_ESQUECIDO_S:
            return
        log.info("telegram: execução %s esquecida em planned; cancelada pela faxina do canal (28.38)", run_id)
        self._cancelar_plano(run_id)                      # sem gesto; o desfecho "cancelada" sai na volta seguinte

    def _texto_do_desfecho(self, texto: str) -> str:
        return self._redigir(texto)

    # ------------------------------------------------------------------ saída
    async def _responder(self, saida: SaidaDaConversa, linha: Mapping[str, object], texto: str, *,
                         origem: str = "resposta", botoes: list[tuple[str, str]] | None = None,
                         exigir: bool = False) -> None:
        await self._enviar(saida, texto, origem=origem, responde_a=_texto(linha.get("ref_mensagem")), botoes=botoes,
                           entrada_id=self._id(linha), exigir=exigir)

    async def _enviar(self, saida: SaidaDaConversa, texto: str, *, origem: str, responde_a: str | None = None,
                      botoes: list[tuple[str, str]] | None = None, entrada_id: int | None = None,
                      exigir: bool = False) -> None:
        """`exigir`: a falha do envio sobe (a prévia da porta, 28.27: sem ela inteira, o dono não tem o que aprovar);
        sem ele, só o log, como sempre."""
        texto = sem_nome_de_persona(texto, self._nomes_de_persona())
        try:
            enviada = await saida.responder(texto, responde_a=responde_a, botoes=botoes)
        except FalhaDeEnvio as falha:
            log.warning("telegram: resposta não saiu (%s)", falha.motivo)
            if exigir:
                raise
            return
        self.repo.registrar_enviada(enviada, origem, entrada_id=entrada_id)


class LeitorDoTelegram:
    """O laço do `getUpdates` (o offset, o 409, o descarte do histórico, a recusa do Telegram) sobre uma
    `ConversaDoCanal`. `registrar` e `tratar_pendentes` ficam aqui com a assinatura antiga (a saída primeiro) para quem
    já os chamava; a conversa tem a nova (`registrar(r, saida=None)`)."""

    def __init__(self, cfg: Config, repo: EntradasDoCanal, portas: PortasDaCentral, *,
                 lider: Callable[[str], int | None], recusa: Callable[[str], bool], redigir: Callable[[str], str],
                 canal: CanalTelegram | None = None, chat_id: str | None = None,
                 dormir: Callable[[float], Awaitable[None]] | None = None, operador: str = OPERADOR_DO_TELEGRAM,
                 relogio: Callable[[], float] | None = None, convidados: ConvidadosDoTelegram | None = None,
                 anexos: ArmazemDeAnexos | None = None):
        self.cfg = cfg
        self.repo = repo
        self.portas = portas
        self.conversa = ConversaDoCanal(cfg, repo, portas, recusa=recusa, redigir=redigir, operador=operador,
                                        relogio=relogio, anexos=anexos)
        #: Quem fala com o bot e não é o dono (28.18). `None` ou desligado: gravado sem texto e sem resposta (28.15).
        self.convidados = convidados
        self._lider = lider
        self._canal = canal
        self._chat_injetado = chat_id
        self._dormir = dormir or asyncio.sleep
        self._conflito_desde: float | None = None
        #: O Telegram recusou a leitura de vez (400/401/403/404): o motivo, já redigido, e o status que o explica.
        self._recusada: str | None = None
        self._recusada_status: int | None = None

    # A identidade e o relógio de parede moram na conversa; o leitor os expõe para quem os trocava aqui (os testes).
    @property
    def operador(self) -> str:
        return self.conversa.operador

    @operador.setter
    def operador(self, valor: str) -> None:
        self.conversa.operador = valor

    @property
    def _agora(self) -> Callable[[], float]:
        return self.conversa._agora   # noqa: SLF001 - o mesmo relógio da regra de idade

    @_agora.setter
    def _agora(self, valor: Callable[[], float]) -> None:
        self.conversa._agora = valor  # noqa: SLF001

    async def registrar(self, saida: SaidaDaConversa, r: Recebida) -> None:
        await self.conversa.registrar(r, saida)

    async def tratar_pendentes(self, saida: SaidaDaConversa) -> None:
        await self.conversa.tratar_pendentes(saida)

    # ------------------------------------------------------------------ configuração e saúde
    @property
    def ligada(self) -> bool:
        a = self.cfg.file.avisos
        return bool(a.enabled and a.entrada.enabled)

    def _segredos(self) -> tuple[str, str]:
        env = self.cfg.env
        token = env.telegram_bot_token.get_secret_value() if env.telegram_bot_token else ""
        chat = env.telegram_chat_id.get_secret_value() if env.telegram_chat_id else ""
        return token.strip(), (self._chat_injetado or chat).strip()

    def canal(self) -> CanalTelegram | None:
        if not self.ligada:
            return None
        token, chat = self._segredos()
        if self._canal is not None:
            return self._canal if chat else None
        if not token or not chat:
            return None
        return CanalTelegram(token, chat, timeout_s=self.cfg.file.avisos.timeout_s)

    def problemas(self) -> list[Problem]:
        if not self.ligada:
            return []
        achados: list[Problem] = []
        if self._conflito_desde is not None:
            achados.append(Problem(
                code="telegram_entrada_conflito",
                message="A conversa pelo Telegram está parada: outro processo lê o mesmo bot (409 no getUpdates).",
                hint="Só um consumidor por bot, e um webhook ativo no bot também dá 409 (confira com getWebhookInfo; "
                     "deleteWebhook o desfaz). Pare a outra leitura (a caixa provisória da orquestradora, outra réplica) "
                     "ou desligue avisos.entrada.enabled; a Central tenta de novo sozinha a cada minuto. "
                     "`python scripts/avisos-telegram.py descobrir` mostra o que o bot enxerga (rode-o com a entrada "
                     "desligada: ele também lê o getUpdates)."))
        if self._recusada is not None:
            # E7: cada status tem a sua causa; "token revogado" para um 400 mandava o dono trocar o token à toa.
            invalido = self._recusada_status == 400
            causa = _CAUSA_DA_RECUSA.get(self._recusada_status or 0, "recusa não prevista do Telegram")
            o_que_fazer = ("O token não é a causa: veja o log da Central (poc.avisos.entrada) " if invalido else
                           "Confira TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID no .env (docs/operacao.md §15) ")
            achados.append(Problem(
                code="telegram_entrada_pedido_invalido" if invalido else "telegram_entrada_recusada",
                message=f"A conversa pelo Telegram está parada: o Telegram recusou a leitura do bot ({self._recusada}).",
                hint=f"Causa provável: {causa}. {o_que_fazer}ou desligue avisos.entrada.enabled; a Central tenta de "
                     "novo sozinha a cada minuto."))
        return achados

    # ------------------------------------------------------------------ uma volta
    async def uma_volta(self) -> int:
        """Recebe, grava e trata. Devolve quantas updates chegaram (0 quando pulou)."""
        canal = self.canal()
        if canal is None or self._lider(AVISOS) is None:
            return 0
        cfg = self.cfg.file.avisos.entrada
        try:
            if self.repo.canal_vazio():
                await self._descartar_historico(canal)
            brutas = await canal.receber(self.repo.proximo_offset(), espera_s=cfg.long_poll_s)
        except ConflitoDeConsumidor:
            if self._conflito_desde is None:
                log.warning("telegram: outro consumidor do bot (409); a entrada espera")
                self._conflito_desde = time.monotonic()
            await self._dormir(cfg.espera_conflito_s)
            return 0
        self._conflito_desde = None
        self._recusada = None
        self._recusada_status = None
        if self._lider(AVISOS) is None:
            return 0        # perdeu a liderança no long-poll: nada gravado = nada confirmado; o novo líder relê
        saida = SaidaDoTelegram(canal)
        await self.conversa.enviar_ajuda_inicial(saida)
        chat = self._segredos()[1]
        envio, apagar = self._envio_ao_convidado(canal)
        if self.convidados is not None and self.convidados.ligado:
            convidados = self.convidados
            self.conversa.decidir_convidado = lambda c, sim: convidados.decidir(c, sim, envio)
        else:
            self.conversa.decidir_convidado = None
        for bruta in brutas:
            r = _ler_update(bruta, chat)
            if r is None:
                continue
            try:
                if self.convidados is not None and self.convidados.trata(r.tipo, r.chat, r.privado, r.do_dono, chat):
                    # A linha vai SEM texto (o dedupe e o offset), e só a linha nova é tratada: a update relida não
                    # repete a apresentação nem o aviso ao dono.
                    if self.conversa.gravar_sem_texto(r, "convidado"):
                        await self.convidados.tratar(
                            tipo=r.tipo, chat=r.chat or "", privado=r.privado, texto=r.texto,
                            perfil=dict(r.perfil) if r.perfil else None, update_id=r.id_externo,
                            ref_mensagem=r.ref_mensagem, enviar=envio, apagar=apagar, com_anexo=bool(r.anexos))
                    continue
                await self.conversa.registrar(r, saida)
            except Exception as exc:  # noqa: BLE001 - uma update ruim não pode travar a conversa (o offset teria de andar)
                # Sem a linha o offset não anda e a MESMA update voltaria a cada volta, para sempre. Registra o mínimo
                # (sem texto) como `falhou`; só o id vai ao log, porque a exceção do banco pode trazer o texto.
                log.error("telegram: update %s não pôde ser registrada (%s)", r.id_externo, type(exc).__name__)
                self.conversa.gravar_falha(r, f"erro ao registrar ({type(exc).__name__})")
        await self.conversa.avisar_antigas(saida)
        await self.conversa.tratar_pendentes(saida)
        return len(brutas)

    @staticmethod
    def _envio_ao_convidado(canal: CanalTelegram) -> tuple[Callable[[str, str], Awaitable[str | None]],
                                                          Callable[[str, str], Awaitable[bool]]]:
        """A saída ao chat do CONVIDADO (28.18): o mesmo bot, outro `chat_id`. Nunca se usa para o dono."""
        async def enviar(chat_id: str, texto: str) -> str | None:
            mid = await canal.responder(texto, chat_id=chat_id)
            return str(mid) if mid is not None else None

        async def apagar(chat_id: str, ref: str) -> bool:
            mid = _num(ref)
            return mid is not None and await canal.apagar(mid, chat_id=chat_id)

        return enviar, apagar

    async def _descartar_historico(self, canal: CanalTelegram) -> None:
        """A 1ª subida do canal (nenhuma linha): o que o Telegram guardou (até 24 h) é histórico, e um "/aprovar" ou um
        "sim" antigo não pode executar. `offset=-1` devolve só a última update e faz o Telegram esquecer as anteriores; a
        linha-marco grava a ordem dela, então o offset passa dela, e tira o canal de "vazio" para a 1ª mensagem de
        verdade não ser descartada por engano."""
        velhas = await canal.receber(-1, espera_s=0)
        ordens = [o for o in (_int(u.get("update_id")) for u in velhas) if o is not None]
        self.repo.gravar_inicio(max(ordens) if ordens else None)
        log.info("telegram: 1ª subida do canal; o histórico do chat foi descartado (%s)",
                 f"até a update {max(ordens)}" if ordens else "fila vazia")

    # ------------------------------------------------------------------ laço
    def _esperar_apos(self, falha: FalhaDeEnvio) -> float:
        """Quanto esperar depois que a LEITURA falhou. 429: o que o Telegram pediu. Definitiva (400/401/403/404, cada
        um com a sua causa em `_CAUSA_DA_RECUSA`): vira problema na saúde e espera como o 409, sem martelar a Bot API a cada 5 s. O resto
        (rede, 5xx) tenta de novo logo."""
        if falha.definitiva:
            if self._recusada is None:
                log.error("telegram: o Telegram recusou a leitura do bot (%s)", falha.motivo)
            self._recusada = falha.motivo
            self._recusada_status = falha.status
            return max(self.cfg.file.avisos.entrada.espera_conflito_s, ESPERA_FALHA_S)
        log.warning("telegram: entrada (%s)", falha.motivo)
        return max(falha.espera_s, 1.0) if falha.espera_s is not None else ESPERA_FALHA_S

    async def laco(self) -> None:
        while True:
            try:
                # O long-poll é a espera; sem canal (segredo faltando) ou sem liderança, a volta não espera nada.
                if not self.ligada or (await self.uma_volta() == 0
                                       and (self.canal() is None or self._lider(AVISOS) is None)):
                    await self._dormir(OCIOSO_S)
            except asyncio.CancelledError:
                raise
            except FalhaDeEnvio as falha:
                await self._dormir(self._esperar_apos(falha))
            except Exception:  # a conversa nunca derruba o processo
                log.exception("telegram: laço da entrada")
                await self._dormir(5)


class ServicoDeEntrada(LeitorDoTelegram):
    """O nome de antes da separação (28.15): o leitor do Telegram com a conversa por dentro, mesma assinatura."""
