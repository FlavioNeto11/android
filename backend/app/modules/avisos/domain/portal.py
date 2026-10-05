"""O contato do site público (item 28.32, para o portal do 29.77): a mensagem de um visitante que chega ao Telegram do
dono. Domínio puro: a validação de defesa, a higiene dos campos e a montagem do aviso; a fila, o canal e a rota ficam
fora daqui.

O visitante é um desconhecido da internet, e o texto dele vai parar no chat em que o dono dá ordens à ANA. Por isso a
mensagem chega rotulada e desarmada:
- o título fixo diz que ela não é verificada;
- nome, empresa e telefone viram uma linha só, para ninguém forjar uma linha rotulada (um "Espera você:" falso);
- todo campo perde os caracteres de direção (bidi) e os de largura zero, que escondem ou invertem texto na tela;
- cada linha da mensagem começa com `│ `: um "ANA:" escrito pelo visitante aparece como citação, não como fala nossa;
- endereço, domínio, IP e comando de bot (`/aprovar`) deixam de ser tocáveis (`desarmar_links`). Um toque do dono num
  `/comando` escrito pelo visitante seria uma ordem DELE à Central.

Exceção do ADR-075: os campos NÃO passam por `texto_seguro` nem pelo redator. O nome e o telefone do visitante chegam
inteiros ao dono, porque é o pedido dele (o contato serve para ele responder).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .mensagem import PRECISA_DE_VOCE, Aviso, chave_do_fato, nivel_do_tipo, titulo_do_aviso

#: O tipo do aviso (fora da rajada e do espelho do Trello; nível 1 na amarração).
TIPO_DO_CONTATO = "portal.contato"
TITULO_DO_CONTATO = titulo_do_aviso("🌐 Mensagem de visitante do site (não verificada)")
#: Os tetos que a rota valida; a função repete a defesa (contrato do 28.32).
NOME_MAX, EMPRESA_MAX, TELEFONE_MAX, MENSAGEM_MAX = 80, 80, 30, 1500
#: O marcador de citação no começo de cada linha da mensagem.
CITACAO = "│ "

CAMPO_INVALIDO = "campo_invalido"
CANAL_DESLIGADO = "canal_desligado"
FALHA_INTERNA = "falha_interna"


@dataclass(frozen=True, slots=True)
class ContatoDoPortal:
    """O contato já validado pela rota e gravado na tabela do Portal (migração 107). `contato_id` é o id dessa linha:
    o mesmo contato, o mesmo id, e por isso a mesma chave."""

    contato_id: int
    nome: str
    empresa: str | None
    telefone: str | None
    mensagem: str


@dataclass(frozen=True, slots=True)
class ContatoAvisado:
    """`enfileirado`: a linha entrou na fila (ou já estava, pela chave). Quando é `False`, `motivo` é `campo_invalido`,
    `canal_desligado` ou `falha_interna`, e nada foi gravado."""

    enfileirado: bool
    motivo: str | None = None


#: 28.34: o Telegram deixa o bot apagar uma mensagem do chat privado até 48 h depois; 1 h de folga.
JANELA_DE_APAGAR = timedelta(hours=47)
APAGADO_OK, APAGADO_EM_ENVIO, APAGADO_FALHOU = "ok", "em_envio", "falhou"


@dataclass(frozen=True, slots=True)
class ApagadoNoCanal:
    """28.34, a exclusão de um contato do site a pedido do titular (29.83). `estado`: `ok` (a fila e as respostas do
    dono terminaram; o Portal pode apagar o contato), `em_envio` (a mensagem está saindo agora: tente de novo em um
    minuto) ou `falhou` (erro de banco na fila ou nas respostas: nada de meia exclusão). `apagadas`: mensagens apagadas
    do chat agora. `a_mao`: a hora UTC (ISO) de cada mensagem que ficou para o dono apagar à mão (velha demais, canal
    desligado, recusa do Telegram, ou a que pode ter saído sem registro)."""

    estado: str
    apagadas: int = 0
    a_mao: tuple[str, ...] = ()


def da_para_apagar(hora: str | None, agora: datetime) -> bool:
    """A mensagem de `hora` ainda está dentro da janela em que o bot a apaga. Hora ilegível ou sem fuso: não."""
    if not hora:
        return False
    try:
        quando = datetime.fromisoformat(hora.replace("Z", "+00:00"))
    except ValueError:
        return False
    return quando.tzinfo is not None and agora - quando < JANELA_DE_APAGAR


#: Direção (U+202A–202E, U+2066–2069, U+200E, U+200F, U+061C) e largura zero (U+200B–200D, U+2060, U+FEFF). O resto
#: da categoria Cf (U+00AD, U+2061–2064, as tags U+E0000…) sai pela categoria, em `_sem_controle`.
_INVISIVEIS = re.compile("[\u202a-\u202e\u2066-\u2069\u200e\u200f\u061c\u200b-\u200d\u2060\ufeff]")
#: Quebras que o Unicode conhece além do `\n`: viram `\n` na mensagem e espaço nos campos de uma linha.
_QUEBRAS = re.compile("\r\n|[\r\u2028\u2029\u0085\x0b\x0c]")
#: "Brancos" fora da categoria Zs que ocupam espaço na tela: braille em branco e os preenchedores do hangul.
_PREENCHEDORES = frozenset("\u2800\u3164\u115f\u1160\uffa0")
#: O ponto ideográfico (o de largura cheia e o de meia largura o NFKC já troca): um domínio com ele também vira link.
_PONTOS = str.maketrans({"\u3002": "."})
#: O ordinal do português (`nº`, `1ª`, `2º`) passa sem o NFKC, que o trocaria por `o` e `a` (revisão do #331). O resto
#: da compatibilidade segue (`m²` vira `m2`, `™` vira `TM`).
_ORDINAIS = re.compile("([\u00aa\u00ba])")
#: Quantas marcas combinantes (Mn, Me) seguidas cada caractere base leva; as demais saem. Empilhadas (o "Zalgo"), elas
#: são desenhadas por cima do título e do `│ ` (revisão do #331). O NFKC já compõe o acento do português (`é`), e duas
#: bastam para o vietnamita. A marca sem caractere base antes dela (no começo do texto ou da linha) sai: ela se
#: apoiaria no espaço do nosso `│ ` e desenharia em cima do marcador (revisão do #335).
MARCAS_MAX = 2
_TELEFONE_PERMITIDO = re.compile(r"[^0-9+()\- ]")
_ESPACOS = re.compile(r"[ \t]+")

#: `http(s)://` vira `hxxp(s)://`, mesmo com letra colada antes (uma cirílica, p. ex.); todo outro `://` vira `[:]//`.
_HTTP = re.compile(r"(?i)h(tt)(ps?)://")
_ESQUEMA = re.compile(r"(?<!hxxp)(?<!hxxps)://")
#: o `tg:` sem `//` também abre o Telegram
_TG = re.compile(r"(?i)(?<![a-z0-9])tg:")
#: domínio solto (letras de qualquer alfabeto: um domínio com letra cirílica também vira link), com o TLD só de letras
_DOMINIO = re.compile(r"(?<![^\W_])((?:[^\W_](?:[^\W_]|-)*\.)+[^\W\d_]{2,})(?![^\W_]|-)")
_IP = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
#: comando de bot: `/aprovar` vira `⁄aprovar` (barra de fração), que o Telegram não toca. Vale depois de pontuação
#: (`ok,/status`, `(/pendencias)`); não vale no meio de palavra (`e/ou`, `50/50`) nem na barra dupla.
_COMANDO = re.compile(r"(?<![\w/])/(?=\w)")
#: menção `@usuario` (e o `@bot` de `/cmd@bot`) vira `＠usuario` (arroba de largura cheia). Desarmada ANTES do domínio:
#: o `@` de um e-mail também vira `＠`, e um `[.]` digitado pelo visitante não protege a menção.
_MENCAO = re.compile(r"@(?=[A-Za-z0-9_]{3,})")


def _nfkc(texto: str) -> str:
    """NFKC, menos nos ordinais `º` e `ª`."""
    return "".join(parte if _ORDINAIS.fullmatch(parte) else unicodedata.normalize("NFKC", parte)
                   for parte in _ORDINAIS.split(texto))


def _sem_controle(texto: str, *, quebra: str) -> str:
    """NFKC (o espaço e o ponto de largura cheia viram ASCII; os ordinais ficam), sem invisíveis nem formato (Cf); as
    quebras viram `quebra`, e todo espaço da categoria Zs, preenchedor e controle vira espaço ASCII. Assim nenhum texto
    do visitante é empurrado para o começo de uma linha da tela sem o `│ ` (revisão do #331, A1). Cada caractere base
    leva no máximo `MARCAS_MAX` marcas combinantes."""
    texto = _QUEBRAS.sub("\n", _nfkc(_INVISIVEIS.sub("", texto)).translate(_PONTOS))
    saida: list[str] = []
    marcas = MARCAS_MAX                                   # no começo, nenhuma marca solta: não há base dela
    for c in texto:
        if c == "\n":
            saida.append("\n" if quebra == "\n" else " ")
            marcas = MARCAS_MAX
            continue
        categoria = unicodedata.category(c)
        if categoria == "Cf":
            continue
        if categoria in ("Mn", "Me"):
            marcas += 1
            if marcas > MARCAS_MAX:
                continue
        else:
            marcas = 0
        saida.append(" " if categoria in ("Zs", "Cc") or c in _PREENCHEDORES else c)
    return "".join(saida)


def uma_linha(valor: str | None) -> str:
    """Nome e empresa: uma linha só, sem invisíveis nem controle, com os espaços juntados."""
    return _ESPACOS.sub(" ", _sem_controle(valor or "", quebra=" ")).strip()


def telefone_limpo(valor: str | None) -> str:
    """Só dígitos, `+ ( ) -` e espaço."""
    return _ESPACOS.sub(" ", _TELEFONE_PERMITIDO.sub("", uma_linha(valor))).strip()


def desarmar_links(texto: str) -> str:
    """Nada do visitante fica tocável no chat do dono: `https://` → `hxxps://`, outro `esquema://` → `esquema[:]//`,
    `tg:` → `tg[:]`, o ponto de domínio e de IP → `[.]` (o `www.` e o `t.me/` caem aí; o ponto ideográfico e o de largura
    cheia também), `/comando` → `⁄comando` e `@usuario` → `＠usuario`. Desarmar a mais é o lado seguro: "fim.Depois" sem
    espaço vira "fim[.]Depois"."""
    texto = _nfkc(texto).translate(_PONTOS)
    texto = _HTTP.sub(lambda m: f"hxx{m.group(2).lower()}://", texto)
    texto = _ESQUEMA.sub("[:]//", texto)
    texto = _TG.sub(lambda m: m.group(0)[:-1] + "[:]", texto)
    texto = _MENCAO.sub("\uff20", texto)
    texto = _DOMINIO.sub(lambda m: m.group(1).replace(".", "[.]"), texto)
    texto = _IP.sub(lambda m: m.group(1).replace(".", "[.]"), texto)
    return _COMANDO.sub("\u2044", texto)


def citar(mensagem: str) -> str:
    """A mensagem com as quebras dela e o marcador de citação em cada linha; linhas em branco seguidas viram uma."""
    linhas = [_ESPACOS.sub(" ", x).rstrip() for x in _sem_controle(mensagem, quebra="\n").split("\n")]
    saida: list[str] = []
    for linha in linhas:
        if not linha and saida and not saida[-1]:
            continue
        saida.append(linha)
    while saida and not saida[-1]:
        saida.pop()
    while saida and not saida[0]:
        saida.pop(0)
    return "\n".join(CITACAO + desarmar_links(x) for x in saida)


def chave_do_contato(contato_id: int) -> str:
    return chave_do_fato("portal", str(contato_id))


def motivo_de_recusa(contato: ContatoDoPortal) -> str | None:
    """A defesa repete os tetos e o obrigatório da rota: `campo_invalido`, ou `None` quando o contato serve. O nome e a
    mensagem que ficam vazios depois da higiene (só invisíveis, só controle) também não servem."""
    cid = contato.contato_id
    if isinstance(cid, bool) or not isinstance(cid, int) or cid <= 0:
        return CAMPO_INVALIDO
    campos = ((contato.nome, NOME_MAX, True), (contato.empresa, EMPRESA_MAX, False),
              (contato.telefone, TELEFONE_MAX, False), (contato.mensagem, MENSAGEM_MAX, True))
    for valor, teto, obrigatorio in campos:
        if valor is not None and not isinstance(valor, str):
            return CAMPO_INVALIDO
        if len(valor or "") > teto or (obrigatorio and not (valor or "").strip()):
            return CAMPO_INVALIDO
    if not uma_linha(contato.nome) or not citar(contato.mensagem):
        return CAMPO_INVALIDO
    return None


def corpo_do_contato(contato: ContatoDoPortal) -> str:
    """As linhas rotuladas fixas: `Nome:`, `Empresa:` e `Telefone:` (só se houver), `Mensagem:` e o texto citado."""
    linhas = [f"Nome: {desarmar_links(uma_linha(contato.nome))}"]
    empresa = desarmar_links(uma_linha(contato.empresa))
    if empresa:
        linhas.append(f"Empresa: {empresa}")
    telefone = telefone_limpo(contato.telefone)
    if telefone:
        linhas.append(f"Telefone: {telefone}")
    linhas += ["Mensagem:", citar(contato.mensagem)]
    return "\n".join(linhas)


def aviso_do_contato(contato: ContatoDoPortal) -> Aviso | None:
    """O aviso pronto para a fila, ou `None` quando o contato não serve (`motivo_de_recusa`). Sem link: o contato não
    tem página no painel."""
    if motivo_de_recusa(contato) is not None:
        return None
    return Aviso(chave=chave_do_contato(contato.contato_id), tipo=TIPO_DO_CONTATO, titulo=TITULO_DO_CONTATO,
                 corpo=corpo_do_contato(contato), link=None, nivel=PRECISA_DE_VOCE)


# ---------------------------------------------------------------------- o resumo dos tetos (pedido do 29.77)
#: Os contatos acima dos tetos da rota do Portal (20 por hora retidos, 500 por dia descartados) não viram aviso um a um:
#: o laço dela manda, no máximo uma vez por hora, só as contagens. Nenhum dado do visitante.
#: O resumo NÃO é "precisa de você" (revisão do #335): acima do limiar sai na hora como `portal.resumo`; abaixo, vai à
#: janela da rotina como `portal.resumo_rotina`, com as contagens no título, porque a rotina mostra uma linha por aviso.
TIPO_DO_RESUMO = "portal.resumo"
TIPO_DO_RESUMO_ROTINA = "portal.resumo_rotina"
TITULO_DO_RESUMO = titulo_do_aviso("🌐 Contatos do site acima do limite")
#: Com algum descartado (o teto do dia estourou) ou com tantos retidos na janela (o teto de uma hora inteira), o resumo
#: aponta o possível abuso. Combinado com a sessão do Portal. Nunca "Espera você": não há gesto que o dono faça ali, e
#: o formulário já se protege sozinho (orquestradora, 04/10 22:37Z).
LIMIAR_RETIDOS = 20
CONTAGEM_MAX, JANELA_MAX_H = 1_000_000, 24


def chave_do_resumo(agora: datetime) -> str:
    """`portal-resumo:<AAAA-MM-DDTHH>Z`: uma por hora UTC. A família não é `portal`, para a resposta do dono ter a
    própria frase (a do contato fala de um visitante)."""
    return chave_do_fato("portal-resumo", f"{agora.astimezone(timezone.utc):%Y-%m-%dT%H}Z")


def _contagem(valor: object, minimo: int, maximo: int) -> bool:
    return isinstance(valor, int) and not isinstance(valor, bool) and minimo <= valor <= maximo


def resumo_valido(retidos: object, descartados: object, janela_h: object) -> bool:
    """Inteiros (bool não conta), de 0 ao teto, a janela de 1 a 24 h, e ao menos um contato: com os dois zerados não
    há o que dizer."""
    if not (_contagem(retidos, 0, CONTAGEM_MAX) and _contagem(descartados, 0, CONTAGEM_MAX)
            and _contagem(janela_h, 1, JANELA_MAX_H)):
        return False
    return int(retidos) + int(descartados) > 0  # type: ignore[call-overload]


def acima_do_limiar(retidos: int, descartados: int) -> bool:
    return descartados > 0 or retidos >= LIMIAR_RETIDOS


def corpo_do_resumo(retidos: int, descartados: int, janela_h: int) -> str:
    """O molde do 28.31: o resultado com os números, o que é crítico e "Nada a fazer". O assunto está no título."""
    janela = "na última hora" if janela_h == 1 else f"nas últimas {janela_h} h"
    guardados = "1 contato guardado" if retidos == 1 else f"{retidos} contatos guardados"
    descarte = "1 descartado" if descartados == 1 else f"{descartados} descartados"
    if acima_do_limiar(retidos, descartados):
        critico = "Crítico: possível abuso do formulário de contato do site."
        gesto = ("Nada a fazer agora: o formulário se protege sozinho. Se quiser desligar o contato do site, diga no "
                 "chat da orquestradora.")
    else:
        critico, gesto = "Crítico: nada.", "Nada a fazer: os guardados ficam na Central, sem aviso."
    return "\n".join([f"{guardados} sem aviso e {descarte} {janela}.", critico, gesto])


def titulo_do_resumo_rotina(retidos: int, descartados: int) -> str:
    guardados = "1 guardado" if retidos == 1 else f"{retidos} guardados"
    descarte = "1 descartado" if descartados == 1 else f"{descartados} descartados"
    return titulo_do_aviso(f"🌐 Contatos do site acima do limite: {guardados}, {descarte}")


def aviso_do_resumo(retidos: int, descartados: int, janela_h: int, agora: datetime) -> Aviso | None:
    """O resumo pronto para a fila, ou `None` quando as contagens não servem (`resumo_valido`). Sem link. O nível é o
    do tipo: 2 acima do limiar (sai na hora), 3 abaixo (a rotina)."""
    if not resumo_valido(retidos, descartados, janela_h):
        return None
    corpo = corpo_do_resumo(retidos, descartados, janela_h)
    if acima_do_limiar(retidos, descartados):
        return Aviso(chave=chave_do_resumo(agora), tipo=TIPO_DO_RESUMO, titulo=TITULO_DO_RESUMO, corpo=corpo, link=None,
                     nivel=nivel_do_tipo(TIPO_DO_RESUMO))
    return Aviso(chave=chave_do_resumo(agora), tipo=TIPO_DO_RESUMO_ROTINA,
                 titulo=titulo_do_resumo_rotina(retidos, descartados), corpo=corpo, link=None,
                 nivel=nivel_do_tipo(TIPO_DO_RESUMO_ROTINA))


# ---------------------------------------------------------------------- o vigia da borda (29.97, laço do Portal)
#: O laço do Portal confere, de hora em hora, a página pública como um visitante e chama `avisar_borda_do_portal` só na
#: TRANSIÇÃO (a 1ª volta com aquele defeito, ou a N-ésima sem conseguir conferir). Contrato combinado com o Portal em
#: 05/10 04:25Z. Os dois tipos são nível 2 e saem na hora (`PARARAM_ALGO`): o site errado para visitante parou algo do
#: dono. Começam com `portal.`, então a fila nunca os agrupa (`SEM_AGRUPAR`): o corpo agrupado diria "abra a caixa de
#: Pendências", e o gesto aqui é na zona da Cloudflare. Fora do espelho do Trello e sem link.
TIPO_DA_BORDA = "portal.borda"
TIPO_DA_BORDA_SEM_CONFERIR = "portal.borda_sem_conferir"
SEM_CONFERIR = "sem_conferir"
#: 29.101 (contrato com o Portal e texto aprovado pela orquestradora, 05/10 06:56Z): a API do central respondeu sem
#: login pelo endereço público. Tipo próprio no nível 1: sai na hora e espera o dono, cujo gesto é parar o túnel. A
#: chave segue a da borda (`portal-borda:api_aberta:<dia UTC>`, sem reaviso no dia) e a resposta dele vai à
#: orquestradora pelo mesmo repasse `borda`.
TIPO_DA_BORDA_API = "portal.borda_api"
API_ABERTA = "api_aberta"
ONDE_DA_API = "api"
#: O "sem conferir" da API tem chave própria (`portal-borda:sem_conferir_api:<dia>`), separada da do site.
SEM_CONFERIR_DA_API = "sem_conferir_api"
#: O motivo que pede a frase a mais: a API respondeu com erro em vez de recusar antes da rota.
API_COM_ERRO = "api-500"
TITULO_DA_API = "🔓 Site: a API do central respondeu sem login pelo endereço público"
CORPO_DA_API = (
    "Chegou: um pedido sem login a /api/instances, pelo nome público, foi atendido.",
    "Crítico: quem estiver na internet consegue ler dados do central sem senha. Escrita não foi testada.",
    "Espera você: pare o serviço do túnel (Cloudflared) no central. Se preferir, responda a esta mensagem: a ANA "
    "repassa à orquestradora, que confere de fora e para o túnel, mas isso só funciona com uma sessão ativa.")
#: Onde o vigia achou o defeito, dito ao dono.
ONDE_DA_BORDA: dict[str, str] = {"raiz": "A página inicial do site", "painel": "O painel", "css": "O estilo do site",
                                 "js": "O script do site"}
#: O achado só sai se for host e caminho (o beacon tem token na query) ou o nome do cookie: nada de `?`, `=`, espaço.
_ACHADO = re.compile(r"[A-Za-z0-9._/-]{1,120}")
#: B1 da leitura do #381: o IP também se esconde dentro de um nome (`10.0.0.5.nip.io`, `10-0-0-5.sslip.io`) e em forma
#: curta ou decimal (`127.1`, `2130706433`). O achado sai sem nenhuma sequência de quatro números separados por `.` ou
#: `-` e sem rótulo só de dígitos; um caminho com segmento numérico também perde o detalhe (o aviso sai sem ele).
#: Releitura do #381: também `_` como separador (`10_0_0_5.nip.io`), o decimal longo e o hexadecimal
#: (`0x7f000001`). O `-` e o `_` ficam FORA da segunda alternativa, que cortaria `borda-502` e `api-404`.
_IP_NO_ACHADO = re.compile(r"\d+[._-]\d+[._-]\d+[._-]\d+|(?:^|[./])\d+(?=[./]|$)|\d{8,}|0x[0-9A-Fa-f]+")
HORAS_SEM_CONFERIR_MAX = 24 * 31
REPASSA = "Se nada mudou lá, responda a esta mensagem: a ANA repassa à orquestradora."


@dataclass(frozen=True, slots=True)
class _Borda:
    titulo: str
    chegou: str          # `{onde}` e `{achado}` (com o separador já posto, ou vazio)
    critico: str
    gesto: str


BORDA: dict[str, _Borda] = {
    "script_injetado": _Borda(
        "🌐 Site: a Cloudflare está pondo script na página",
        "{onde} chegou com um script que a página não tem{achado}.",
        "Crítico: a página promete não ter rastreador, e no painel, sem a proteção ligada, o script roda.",
        "Espera você: no painel da Cloudflare, desligue Web Analytics (RUM) na zona; se o script for de /cdn-cgi/, "
        "desligue Rocket Loader, Email Obfuscation ou Bot Fight Mode."),
    "html_transformado": _Borda(
        "🌐 Site: a borda está reescrevendo o HTML",
        "{onde} chegou reescrito pela borda (sem a marca que proíbe mudar a página, ou recomprimido).",
        "Crítico: com isso a borda volta a poder injetar script.",
        f"Espera você: no painel da Cloudflare, confira Rules > Transform Rules e Compression Rules. {REPASSA}"),
    "csp_ausente": _Borda(
        "🌐 Site: página sem a política de segurança",
        "{onde} chegou sem a política de segurança esperada.",
        "Crítico: script de fora rodaria na página.",
        f"Espera você: no painel da Cloudflare, confira se alguma Transform Rule tira cabeçalhos. {REPASSA}"),
    "cookie": _Borda(
        "🌐 Site: a página está pondo cookie",
        "{onde} chegou pondo cookie{achado}.",
        "Crítico: a página promete que não usa cookies.",
        "Espera você: no painel da Cloudflare, desligue Bot Fight Mode. Se preferir mantê-lo, responda a esta mensagem: "
        "a ANA pede à orquestradora para mudar o texto da página."),
    "versao_divergente": _Borda(
        "🌐 Site: estilo ou script fora da versão",
        "{onde} chegou com um conteúdo diferente da versão que a página pede.",
        "Crítico: visitantes veem estilo ou script velho ou alterado.",
        "Espera você: no painel da Cloudflare, ponha Caching Level em Standard (não “Ignore query string”) e desligue "
        "Auto Minify e Rocket Loader."),
    "pagina_fora": _Borda(
        "🌐 Site: a página não abre para visitante",
        "{onde} respondeu a um visitante com um desafio da Cloudflare (403 ou 503).",
        "Crítico: visitantes não veem o site.",
        "Espera você: no painel da Cloudflare, confira Security > Bots e as regras do WAF."),
}


def chave_da_borda(codigo: str, agora: datetime) -> str:
    """`portal-borda:<código>:<AAAA-MM-DD>`: um defeito que persiste dá uma mensagem por dia UTC, não uma por hora. O
    `agora` sem fuso seria lido como hora local do processo (B2 da leitura do #381): recusado."""
    if agora.utcoffset() is None:
        raise ValueError("agora sem fuso")
    return chave_do_fato("portal-borda", codigo, f"{agora.astimezone(timezone.utc):%Y-%m-%d}")


def _so_o_host(valor: object) -> object:
    return valor.strip().split("/", 1)[0] if isinstance(valor, str) else None


def _achado(valor: object, separador: str, fim: str = "") -> str:
    if not isinstance(valor, str) or not _ACHADO.fullmatch(valor.strip()) or _IP_NO_ACHADO.search(valor):
        return ""                        # fora do formato, ou um IP: nunca sai
    return f"{separador}{valor.strip()}{fim}"


def _api_sem_conferir(h: int, achado: object, agora: datetime) -> Aviso:
    """29.101, leitura do #383 (orquestradora, 05/10 07:16Z): o vigia não conseguiu conferir a API (404, 302, 429, 500,
    503 ou o desafio da borda). O site pode estar perfeito, então o texto é da API. Chave própria, para que o "sem
    conferir" do site no mesmo dia não engula o da API. No `api-500`, a API respondeu com erro em vez de recusar o
    pedido sem login antes da rota: vale olhar."""
    causa = _achado(achado, " (código: ", ")") or ""
    linhas = [f"Há {h} h o vigia não completa a conferência da API do central pelo nome público{causa}."]
    if isinstance(achado, str) and achado.strip() == API_COM_ERRO:
        linhas.append("A API devia recusar o pedido sem login antes de chegar à rota, e respondeu com erro: vale olhar.")
    linhas.append("Não espera você: a conferência segue sozinha, e uma API aberta vem em aviso próprio.")
    return Aviso(chave=chave_da_borda(SEM_CONFERIR_DA_API, agora), tipo=TIPO_DA_BORDA_SEM_CONFERIR,
                 titulo=titulo_do_aviso(f"🌐 Site: não consigo conferir a API do central há {h} h"),
                 corpo="\n".join(linhas), link=None, nivel=nivel_do_tipo(TIPO_DA_BORDA_SEM_CONFERIR))


def aviso_da_borda(codigo: object, onde: object, agora: datetime, *, achado: object = None,
                   horas_sem_conferir: object = None) -> Aviso | None:
    """O aviso pronto para a fila, ou `None` quando o código ou o lugar não são do contrato, ou falta a contagem de
    horas do `sem_conferir`. O `achado` fora do formato é omitido, sem recusa. Sem link."""
    if not isinstance(agora, datetime) or agora.utcoffset() is None or not isinstance(codigo, str):
        return None                      # sem fuso, o dia UTC dependeria do fuso do processo (B2): `campo_invalido`
    if codigo == SEM_CONFERIR:
        if not _contagem(horas_sem_conferir, 1, HORAS_SEM_CONFERIR_MAX):
            return None
        h = int(horas_sem_conferir)  # type: ignore[call-overload]
        if onde == ONDE_DA_API:
            return _api_sem_conferir(h, achado, agora)
        # B3 da leitura do #381 (decisão da orquestradora): sem conferir não é defeito visto. Nada de "Crítico"; o
        # texto diz que a conferência não completou, há quanto tempo, que pode ser o caminho e não o site, e que não
        # espera o dono. O nível 2 fica.
        # O código do vigia (`borda-502`, `tempo-esgotado`, `api-<status>`) passa pelo mesmo filtro do achado
        # (combinado com o Portal, 05/10); sem ele, a frase genérica.
        causa = _achado(achado, " (código: ", ")") or " (tempo esgotado ou erro no caminho)"
        corpo = "\n".join([f"Há {h} h o vigia não completa a conferência do site pelo nome público{causa}.",
                           "Pode ser o caminho do central até a internet, e não o site.",
                           "Não espera você: a conferência segue sozinha, e um defeito visto vem em aviso próprio."])
        return Aviso(chave=chave_da_borda(codigo, agora), tipo=TIPO_DA_BORDA_SEM_CONFERIR,
                     titulo=titulo_do_aviso(f"🌐 Site: não consigo conferir a página há {h} h"), corpo=corpo, link=None,
                     nivel=nivel_do_tipo(TIPO_DA_BORDA_SEM_CONFERIR))
    if codigo == API_ABERTA:
        # 29.101: o lugar é só a API, e o texto é fixo: o único caminho citado é `/api/instances`, e o achado do vigia
        # não entra (nada de host nem IP).
        if onde != ONDE_DA_API:
            return None
        return Aviso(chave=chave_da_borda(codigo, agora), tipo=TIPO_DA_BORDA_API, titulo=titulo_do_aviso(TITULO_DA_API),
                     corpo="\n".join(CORPO_DA_API), link=None, nivel=nivel_do_tipo(TIPO_DA_BORDA_API))
    borda = BORDA.get(codigo)
    lugar = ONDE_DA_BORDA.get(onde) if isinstance(onde, str) else None
    if borda is None or lugar is None:
        return None
    # Releitura do #381: recusado o host e caminho inteiro (uma versão no caminho, `jquery-3.6.0.min.js`), tenta só o
    # host pelo mesmo filtro, para o aviso não sair sem item nenhum.
    detalhe = (_achado(achado, " (", ")") if codigo == "cookie"
               else _achado(achado, ": ") or _achado(_so_o_host(achado), ": "))
    corpo = "\n".join([borda.chegou.format(onde=lugar, achado=detalhe), borda.critico, borda.gesto])
    return Aviso(chave=chave_da_borda(codigo, agora), tipo=TIPO_DA_BORDA, titulo=titulo_do_aviso(borda.titulo),
                 corpo=corpo, link=None, nivel=nivel_do_tipo(TIPO_DA_BORDA))
