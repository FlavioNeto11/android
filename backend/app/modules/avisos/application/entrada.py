"""A gramática dos comandos que chegam por um canal de conversa (item 28.15, ADR-071): texto → intenção, por regra
fixa e sem IA.

É pura e comum aos canais: hoje o Telegram (28.15), depois o Trello (32.2). Nada de canal mora aqui. O adaptador de
cada canal traduz o que chegou (update, comentário) em texto + o FATO a que ele se refere (o aviso respondido, o
cartão comentado), e a resposta de volta. Quem decide se a mensagem é para a orquestradora pelo jeito do canal (o
reply a uma mensagem que a Central não mandou) também é o canal; aqui só existe o `/orq`.

O roteador não executa nada; ele só diz o que a mensagem pede. Quem age é o serviço de entrada, pelos MESMOS
serviços das rotas do painel.

Sem fato (a mensagem solta):
- `/ajuda` (e `/start`), `/status` (e `/estado`), `/pendencias`;
- `/aprovar <id> [nota]`, `/vetar <id> [nota]`, `/responder <id> <texto>`. O `<id>` é o id inteiro ou o fim dele,
  como a `/pendencias` mostra;
- `/para <aparelho|persona> <objetivo>`, ou "para o X: <objetivo>";
- texto livre: um objetivo, com o destino tirado do texto pelo extrator do painel;
- `/orq <texto>`: recado para a orquestradora, guardado e não executado;
- `/captura <aparelho>`, ou "captura do android-12" (28.24, exceção (a) do dono): a tela do aparelho volta como imagem, só
  ao chat do dono. Não executa nada no aparelho;
- `/ler` (ou "leia", "o que tem nessa imagem") em RESPOSTA (reply) a uma foto do dono (28.24, F3): a IA descreve a imagem, uma
  vez só (a descrição fica gravada) e com teto em dólar; sem reply a um anexo, a resposta explica o formato. Não pede
  Executar: o gasto é pequeno e tem teto. O fato `anexo:<id>` é montado pelo canal (o anexo guardado da mensagem respondida);
  qualquer outra resposta a uma foto segue a gramática comum;
- "quem é você?", "você é uma IA?" (e `/quem`): a pergunta pela identidade (28.17). Quem responde é a ANA, que diz
  que é IA; a frase precisa ser SÓ a pergunta, para um pedido que começa parecido seguir como texto livre.

Com fato (a resposta a um aviso, o comentário num cartão), o id é o do fato e não se escreve:
- aprovação: "sim" ou `/aprovar [nota]` aprova; "não" ou `/vetar [nota]` veta;
- execução que espera resposta: o texto, ou `/responder <texto>`, é a resposta;
- convidado novo no Telegram (28.18, `convidado:<chat>:novo`): "sim" autoriza a pessoa, "não" recusa. Qualquer outro
  aviso sobre convidado (a mensagem dele, o bot num grupo) só informa: a resposta do dono a ele NÃO vira pedido.

A triagem de credencial também não mora aqui: o domínio e a aplicação não enxergam `app.security`. O serviço a faz
antes de gravar o texto.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from app.contracts.identidade import APRESENTACAO_DA_IA

#: O que cada intenção é. `desconhecida` responde com a ajuda; `vazia` não responde.
INTENCOES = frozenset({"ajuda", "identidade", "status", "pendencias", "aprovar", "vetar", "responder", "para", "livre",
                       "orquestradora", "desconhecida", "vazia", "autorizar_convidado", "recusar_convidado", "captura",
                       "ler_anexo"})

AJUDA = (
    "Comandos da Central:\n"
    "/status: o parque e o que está rodando\n"
    "/pendencias: o que espera você (aprovações e perguntas)\n"
    "/aprovar <id> e /vetar <id> [nota]: decide uma aprovação\n"
    "/responder <id> <texto>: responde à pergunta de uma execução\n"
    "/para <aparelho ou persona> <objetivo>: um pedido com destino\n"
    "/captura <aparelho>: a tela do aparelho, como imagem (também: \"captura do android-12\")\n"
    "/ler: respondendo a uma foto sua, a IA descreve a imagem (uma vez só, com teto de gasto; também: \"leia\")\n"
    "Texto livre também é um pedido; antes de rodar, a Central mostra a prévia e espera Executar.\n"
    "Foto, PDF e texto (arquivo .txt) ficam guardados na Central; a legenda vale como mensagem.\n"
    "Respondendo a um aviso, o id é o dele: \"sim\" aprova, \"não\" veta, e o texto responde a uma pergunta.\n"
    "Senha e código não passam por aqui: grave no painel.")

#: A primeira mensagem do canal (28.17): a apresentação inteira uma vez; depois, só o nome.
SAUDACAO = f"Oi! Sou a {APRESENTACAO_DA_IA}, e agora atendo por aqui.\n"
#: ANA é IA e diz isso se perguntarem (decisão do dono, 03/10).
RESPOSTA_IDENTIDADE = f"Sou a {APRESENTACAO_DA_IA}: uma IA, não uma pessoa. /ajuda mostra o que eu faço por aqui."

_SIM = frozenset({"sim", "s", "aprovar", "aprova", "aprovo", "ok", "pode", "confirmo", "\U0001f44d"})
_NAO = frozenset({"nao", "n", "vetar", "veta", "vetado", "rejeitar", "rejeita", "recusar", "recuso", "\U0001f44e"})
#: A pergunta pela identidade, já sem acento e em minúsculas, de ponta a ponta: "quem é você?", "você é uma IA?",
#: "é um robô?", "qual é o seu nome?". Ancorada nas duas pontas de propósito: "você é capaz de postar…" é pedido.
_QUEM_E = re.compile(
    r"^(?:(?:oi|ola|e ai)[\s,!.]+)?(?:"
    r"quem (?:e|eh) (?:voce|vc|tu)|"
    r"(?:voce|vc|tu)? ?e (?:uma? )?(?:ia|robo|bot|pessoa|humana|humano|gente de verdade|de verdade)|"
    r"(?:qual (?:e )?)?(?:o )?seu nome|como (?:voce|vc) se chama"
    r")\s*[?!.]*$")
_ANDROID = re.compile(r"^android-\d+$", re.IGNORECASE)
#: "leia", "ler essa imagem", "descreva a foto", "o que tem nessa imagem?": já sem acento, de ponta a ponta, e só quando a
#: mensagem é reply a um anexo do dono (a frase solta, sem anexo, segue como texto livre).
_LER = re.compile(r"^(?:(?:por favor )?(?:le|leia|ler|descreva|descreve)(?: (?:essa|esta|a|ela))?(?: (?:imagem|foto|print))?"
                  r"|o que (?:tem|ha|aparece|esta) (?:nessa|nesta|na) (?:imagem|foto|print)(?: ai)?)\s*[?!.]*$")
#: "captura do android-12", "me manda um print do android-12", "screenshot de tela do android-3": já sem acento, de ponta a
#: ponta. Só com o id do aparelho; "captura do app do Pedro" segue como pedido de texto livre.
_CAPTURA = re.compile(r"^(?:(?:me )?(?:manda|mande|envia|envie|tira|tire|pega|pegue) )?(?:a |uma? )?"
                      r"(?:captura|print|screenshot)(?: de tela)? (?:do |da |de )?(?P<alvo>android-\d+)\s*[?!.]*$")
#: Pergunta solta ao bot (28.28): termina em "?" ou começa por palavra de pergunta, já sem acento. Não é pedido de
#: aparelho: "porque tem tanta coisa represada em validação?" virava texto livre, a prévia pedia destino e o dono
#: recebia "Diga onde ou por quem". Na dúvida entre pergunta e pedido, repassar é melhor que recusar (orquestradora).
_PERGUNTA_INICIO = re.compile(
    r"^(?:(?:oi|ola|ana|e ai)[\s,!.]+)?(?:por ?que|pq|o ?que|oq|quando|como|quanto|quantos|quantas|cade|qual|quais|onde|"
    r"quem|sera que|tem como|ja|esta|estao|existe|existem)\b")
#: O que faz da frase um pedido para um aparelho ou uma persona, mesmo terminando em "?": "pode abrir o QA no
#: android-12?" é pedido.
_CITA_DESTINO = re.compile(r"\bandroid-\d+\b|@\w|\bpersona\b")


def _eh_pergunta(normal: str) -> bool:
    if _CITA_DESTINO.search(normal):
        return False
    return normal.rstrip().endswith("?") or bool(_PERGUNTA_INICIO.match(normal))


#: "para o X: objetivo", "para a X: objetivo", "para X: objetivo" (os dois-pontos são o que separa o destino).
_PARA_LIVRE = re.compile(r"^\s*para\s+(?:o\s+|a\s+)?(?P<alvo>[^:\n]{1,60}?)\s*:\s*(?P<objetivo>\S.*)$",
                         re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True, slots=True)
class Intencao:
    tipo: str
    #: O id (inteiro ou o fim dele) da aprovação ou da execução. Com fato, o id do fato.
    ref: str | None = None
    #: O objetivo, a resposta, a nota do veto/aprovação, o recado ou o texto livre.
    texto: str = ""
    #: O destino do `/para`, como a pessoa escreveu.
    alvo: str | None = None
    #: O que dizer quando o formato não serve (só em `desconhecida`).
    motivo: str | None = None
    #: `/aprovar <x>` ou `/vetar <x>` em REPLY a um aviso: a primeira palavra digitada. Quem decide confere se ela é o
    #: id de OUTRA pendência (28.26); se for, nada se decide. Sem isso o reply decidia o aviso e o id virava nota.
    ref_digitado: str | None = None
    #: Por que uma mensagem foi à orquestradora (28.28): `comando` (`/orq`), `reply` (ao bot que a Central não mandou),
    #: `pergunta` (pergunta solta do dono), `continuacao` (reply a uma resposta nossa de um repasse) ou `sem_destino`
    #: (texto livre que a prévia recusou). Decide a resposta ao dono; o repasse é o mesmo.
    repasse: str | None = None


@dataclass(frozen=True, slots=True)
class Fato:
    """A que a mensagem se refere: `approval:<id>` ou `run:<id>:needs_input` (a chave do aviso, `avisos_entregas`)."""

    tipo: str
    ident: str
    detalhe: str = ""

    @classmethod
    def de(cls, chave: str | None) -> Fato | None:
        if not chave:
            return None
        tipo, _, resto = chave.partition(":")
        ident, _, detalhe = resto.partition(":")
        return cls(tipo, ident, detalhe) if tipo and ident else None

    @property
    def aprovacao(self) -> bool:
        return self.tipo == "approval"

    @property
    def pergunta(self) -> bool:
        return self.tipo == "run" and self.detalhe == "needs_input"

    @property
    def anexo(self) -> bool:
        """O anexo do dono a que a mensagem responde (28.24, F3): a identidade é o `canal_anexos.id`."""
        return self.tipo == "anexo"

    @property
    def comentario(self) -> bool:
        """O pedido de confirmação de um comentário do dono num cartão do Trello (28.30): `comentario:<action>:<card>`."""
        return self.tipo == "comentario"

    @property
    def teto_de_comentarios(self) -> bool:
        """O aviso de que o teto por hora segurou os pedidos de confirmação (28.30): `comentario-teto:<hora>`."""
        return self.tipo == "comentario-teto"

    @property
    def convidado(self) -> bool:
        """O aviso sobre quem não é o dono (28.18). `detalhe == "novo"` é o único que se decide."""
        return self.tipo == "convidado"

    @property
    def objetivo_parado(self) -> bool:
        """O aviso do objetivo parado (28.40): `objective:<id>:<entrada na espera>`. Só informa; o gesto é no painel."""
        return self.tipo == "objective"

    @property
    def conta(self) -> bool:
        """O aviso da conta que pede a pessoa: `session:<id do evento>`. Só informa; o gesto é no painel."""
        return self.tipo == "session"

    @property
    def resumo_do_portal(self) -> bool:
        """O resumo dos contatos do site acima dos tetos (28.32): `portal-resumo:<hora>`. Só informa."""
        return self.tipo == "portal-resumo"

    @property
    def portal(self) -> bool:
        """A mensagem de um visitante do site (28.32): `portal:<contato_id>`. Só informa."""
        return self.tipo == "portal"


#: A resposta a um aviso que só informa e se resolve pelo link dele (28.41).
SO_INFORMA_PELO_LINK = "Este aviso só informa: para resolver, toque no link dele."


def _sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).lower()


def _comando(texto: str) -> tuple[str, str]:
    """`/cmd@NomeDoBot resto` → (`cmd`, `resto`). O `@Nome` é o que o Telegram põe nos grupos."""
    cabeca, _, resto = texto.strip().partition(" ")
    return _sem_acento(cabeca[1:].split("@", 1)[0]), resto.strip()


def _palavra(texto: str) -> str:
    return _sem_acento(texto.strip().strip(".!").strip())


def rotear(texto: str | None, *, fato: str | Fato | None = None) -> Intencao:
    """A intenção de uma mensagem do operador. `fato`: a chave do aviso respondido ou do cartão comentado."""
    t = (texto or "").strip()
    if not t:
        return Intencao("vazia")
    f = fato if isinstance(fato, Fato) else Fato.de(fato)
    if t.startswith("/"):
        return _rotear_comando(t, f)
    if f is not None:
        por_fato = _rotear_resposta(t, f)
        if por_fato is not None:
            return por_fato
    normal = " ".join(_sem_acento(t).split())
    captura = _CAPTURA.match(normal)
    if captura:
        return Intencao("captura", alvo=captura.group("alvo"))
    if _QUEM_E.match(normal):
        return Intencao("identidade")
    m = _PARA_LIVRE.match(t)
    if m:
        return Intencao("para", alvo=m.group("alvo").strip(), texto=m.group("objetivo").strip())
    if _eh_pergunta(normal):
        return Intencao("orquestradora", texto=t, repasse="pergunta")
    return Intencao("livre", texto=t)


def _rotear_comando(t: str, f: Fato | None) -> Intencao:
    cmd, resto = _comando(t)
    if cmd in ("ajuda", "start", "help"):
        return Intencao("ajuda")
    if cmd in ("quem", "ana"):
        return Intencao("identidade")
    if cmd in ("captura", "print", "screenshot"):
        alvo = resto.split()[0] if resto.split() else ""
        if not _ANDROID.match(alvo):
            return Intencao("desconhecida", motivo="Formato: /captura android-NN (o id do aparelho).")
        return Intencao("captura", alvo=alvo.lower())
    if cmd == "ler":
        if f is not None and f.anexo:
            return Intencao("ler_anexo", ref=f.ident)
        return Intencao("desconhecida", motivo="Responda (reply) à foto que você mandou com /ler.")
    if cmd in ("status", "estado"):
        return Intencao("status")
    if cmd == "pendencias":
        return Intencao("pendencias")
    if cmd == "orq":
        return Intencao("orquestradora", texto=resto, repasse="comando")
    if cmd in ("aprovar", "vetar"):
        if f is not None and f.aprovacao:
            return Intencao(cmd, ref=f.ident, texto=resto, ref_digitado=resto.split()[0] if resto.split() else None)
        ref, _, nota = resto.partition(" ")
        if not ref:
            return Intencao("desconhecida", motivo=f"Falta o id: /{cmd} <id> (a /pendencias mostra os ids).")
        return Intencao(cmd, ref=ref, texto=nota.strip())
    if cmd == "responder":
        if f is not None and f.pergunta:
            if not resto:
                return Intencao("desconhecida", motivo="Formato: /responder <texto>.")
            return Intencao("responder", ref=f.ident, texto=resto)
        ref, _, resposta = resto.partition(" ")
        if not ref or not resposta.strip():
            return Intencao("desconhecida", motivo="Formato: /responder <id> <texto>.")
        return Intencao("responder", ref=ref, texto=resposta.strip())
    if cmd == "para":
        alvo, _, objetivo = resto.partition(" ")
        if not alvo or not objetivo.strip():
            return Intencao("desconhecida", motivo="Formato: /para <aparelho ou persona> <objetivo>.")
        return Intencao("para", alvo=alvo.strip(), texto=objetivo.strip())
    return Intencao("desconhecida", motivo=f"Não conheço /{cmd}.")


def _rotear_resposta(t: str, f: Fato) -> Intencao | None:
    if f.tipo == "vencimento":
        # 31.50: o lembrete só avisa. Sem este ramo, a resposta a ele cairia no texto livre e viraria pedido.
        # A chave não diz se o lembrete é de aprovação, de pergunta ou de objetivo parado, e o objetivo não está na
        # caixa de Pendências (revisão do #372): a frase manda ao link do próprio lembrete, que é o lugar certo.
        return Intencao("desconhecida", motivo="Este lembrete só avisa: para resolver, toque no link dele ou responda "
                                               "ao aviso original.")
    if f.objetivo_parado or f.conta:
        # 28.41 (R1 da leitura do #372): nada se responde a esses avisos; o gesto é no aparelho, pelo link. Sem este
        # ramo, a resposta cairia no texto livre e poderia virar a prévia de uma execução NOVA.
        return Intencao("desconhecida", motivo=SO_INFORMA_PELO_LINK)
    if f.anexo:
        # Só o pedido de leitura é do anexo; "sim", um objetivo ou qualquer outra frase seguem a gramática comum (None).
        return Intencao("ler_anexo", ref=f.ident) if _LER.match(" ".join(_sem_acento(t).split())) else None
    if f.portal:
        # 28.32: o texto é de um desconhecido da internet. A resposta do dono a ele nunca vira pedido, execução, cartão,
        # aprovação nem chamada de IA, e não vai ao visitante.
        return Intencao("desconhecida", motivo="Esta mensagem é de um visitante do site e só informa: nada foi executado, "
                                               "e a sua resposta não vai a ele. Para falar com ele, use o contato que "
                                               "ele deixou.")
    if f.resumo_do_portal:
        # Só contagens: sem este ramo, um "sim" a ele cairia no texto livre e viraria pedido.
        return Intencao("desconhecida", motivo="Este aviso só informa: nada foi executado, e a resposta a ele não liga "
                                               "nem desliga o formulário de contato do site.")
    if f.teto_de_comentarios:
        # Revisão da #314: o aviso do teto só informa. Sem este ramo, um "sim" a ele cairia no texto livre e viraria
        # pedido.
        return Intencao("desconhecida", motivo="Este aviso só informa: nada foi executado. Para confirmar um comentário, "
                                               "responda à mensagem de confirmação dele ou comente no cartão.")
    if f.convidado:
        # Sem este ramo, o "sim" do dono ao aviso do convidado cairia no texto livre e viraria PEDIDO (28.18).
        if f.detalhe != "novo":
            return Intencao("desconhecida", motivo="Este aviso só informa: nada foi executado, e a resposta não vai à "
                                                   "pessoa. Para atender o pedido dela, peça pelo painel.")
        palavra = _palavra(t)
        if palavra in _SIM:
            return Intencao("autorizar_convidado", ref=f.ident)
        if palavra in _NAO:
            return Intencao("recusar_convidado", ref=f.ident)
        return Intencao("desconhecida", motivo="Para autorizar quem chegou, responda \"sim\" ou \"não\".")
    if f.comentario:
        # 28.30: o sim ou o não do dono ao comentário que ele fez num cartão. Nada se executa aqui: a decisão vai à
        # orquestradora, que age e responde no cartão. Sem este ramo o "sim" cairia no texto livre e viraria pedido.
        palavra = _palavra(t)
        if palavra in _SIM or palavra in _NAO:
            sim = palavra in _SIM
            return Intencao("orquestradora", ref=f.ident, repasse="comentario_sim" if sim else "comentario_nao",
                            texto=(f"O dono {'CONFIRMOU (sim)' if sim else 'NEGOU (não)'} o comentário {f.ident} do cartão "
                                   f"{f.detalhe} do Trello."))
        return Intencao("desconhecida", motivo="Para confirmar o seu comentário no cartão, responda \"sim\" ou \"não\".")
    if f.aprovacao:
        palavra = _palavra(t)
        if palavra in _SIM:
            return Intencao("aprovar", ref=f.ident)
        if palavra in _NAO:
            return Intencao("vetar", ref=f.ident)
        return Intencao("desconhecida", motivo="Para decidir esta aprovação, responda \"sim\" ou \"não\".")
    if f.pergunta:
        return Intencao("responder", ref=f.ident, texto=t)
    return None


def texto_para_o_extrator(alvo: str, objetivo: str) -> str:
    """O `/para` vira uma frase que o extrator de destinos do painel reconhece (`TargetExtractor`): "no android-09",
    "como @fulano", "com a persona Ana". Assim o destino passa pelo MESMO casamento com o catálogo, e um nome que
    não casa com ninguém volta como pergunta, sem adivinhação."""
    a = alvo.strip()
    if _ANDROID.match(a):
        return f"{objetivo} no {a.lower()}"
    if a.startswith("@"):
        return f"{objetivo} como {a}"
    return f"{objetivo} com a persona {a}"


def casar_ref(ref: str, ids: list[str]) -> list[str]:
    """Os ids que o `ref` indica: o id inteiro, ou o FIM dele (4+ caracteres), como a `/pendencias` mostra."""
    r = ref.strip().lower()
    if not r:
        return []
    exatos = [i for i in ids if i.lower() == r]
    if exatos:
        return exatos
    if len(r) < 4:
        return []
    return [i for i in ids if i.lower().endswith(r)]


def sufixo(ident: str, n: int = 6) -> str:
    """O fim do id que a `/pendencias` mostra (`r-20261002181523-4985a1` → `4985a1`)."""
    return ident[-n:]
