"""Filtro SENSATO da C3 da intenção (item 31.9; ADR-069 itens 4 e 10): lista de BLOQUEIO sobre o piso do dono.

A C3 é o comando do dono depois de `sem_destinos` e de `redact`. Desde a emenda de 03/10 (ADR-069 item 10, decisão do dono
de ~00:15Z), dado pessoal pode ir ao Jev "desde que faça sentido no filtro": nome e `@handle` de pessoa não são vazamento.
O filtro mascara o que não ajuda a escolher a habilidade e deixa o resto (`remover_entidades`):

0. o texto é normalizado (`normalizar`: NFKC, sem marca combinante nem caractere invisível, todo traço como "-"): letra
   circulada, de largura cheia ou matemática vira a letra comum, e "s<ZWSP>enha" vira "senha", que a conferência da C7 do
   consumidor pega. Letra fora do alfabeto latino em QUALQUER palavra (o "а" cirílico em "senhа", "пароль", "密码") RECUSA
   o texto: o comando é em português, e é o jeito de esconder C7 (rodada C do 31.9: a regra vale para a frase, não mais
   só para a palavra que mistura alfabetos; decisão da orquestradora de 03/10);
1. o que tem forma conhecida vira marcador fixo: o que está entre aspas (`[texto]`: o que a pessoa manda escrever), link
   e domínio, e-mail, `@handle`, telefone, número e token de letras com `_` (`[termo]`: handle sem `@`, identificador).
   A aspa que abre e não fecha leva o resto do texto para `[texto]`. Símbolo (emoji, braille, letra em quadrado negativo)
   vira `[texto]`; colado entre duas letras, recusa ("s★enha" passaria pela conferência da C7);
2. o que esconde e-mail, telefone ou documento RECUSA o texto inteiro (`None`): endereço, documento (CPF, RG, cartão...),
   e-mail por extenso ou ofuscado (`arroba`, `(at)`, `(a)`, `{dot}`, `ponto com`, `correio ponto net`, `arr0ba`,
   `a r r o b a`, `zilda at correio net`, `zilda at gmail`), sobra de `@` ou `://` e dois ou mais numerais por extenso
   SEGUIDOS (telefone, documento ou PIN ditado: "nove oito", "dez, dez"). Numeral solto vira `[numero]` ("Ze Sete e Maria
   Onze", "duas fotos e três pessoas" passam);
3. o resto passa como está: palavra comum, nome de pessoa, nome de app.

Desde a reverificação B do 31.9 (03/10, NO-GO em 97f35fac) são DUAS passadas. A recusa por forma escondida roda primeiro,
sobre o texto normalizado e ainda sem máscara (`_RECUSA_NO_ORIGINAL`): mascarar antes apagava o que ela precisava ver (o
`742` do endereço em inglês, o `0` de `arr0ba`). Depois vêm as máscaras, com o token de letras E dígitos (`_MISTO`) ANTES do
número: com o número primeiro, `limao77` virava `limao[numero]` e a palavra da senha saía inteira (15 dos 22 vazamentos de
C7). `remover_entidades_com_motivo` diz POR QUE recusou (`MotivoDoFiltro`), para a linha da sombra.

Até a emenda, o passo 3 era uma lista de PERMISSÃO (palavra fora de um vocabulário fixo virava `[termo]`, nome com
maiúscula também) com recusa do texto inteiro acima de uma proporção de desconhecidas: a remoção que falha fechada da
primeira redação do ADR-069. Medido em 03/10 sobre os 90 comandos reais de 7 dias (só leitura): as 17 recusas que não eram
C7 vinham todas da proporção, e a lista apagava cerca de 8 palavras por comando no qa-messenger. O piso não mudou.

Credencial e 2FA (C7) NÃO são tratados aqui: o consumidor recusa o pedido inteiro antes (`intencao.py`).

Funções puras: sem banco, sem rede, sem configuração.
"""
from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Callable, Iterable
from typing import Final, Literal

#: Marcadores fixos, minúsculos e entre colchetes: nenhum é palavra do vocabulário, e a conferência os ignora.
M_URL: Final = "[link]"
M_EMAIL: Final = "[email]"
M_HANDLE: Final = "[usuario]"
M_TELEFONE: Final = "[telefone]"
M_NUMERO: Final = "[numero]"
M_TERMO: Final = "[termo]"
M_TEXTO: Final = "[texto]"

_MARCADORES: Final = (M_URL, M_EMAIL, M_HANDLE, M_TELEFONE, M_NUMERO, M_TERMO, M_TEXTO)

#: Por que o filtro recusou a C3 (`decisao_fechada_sombra.motivo_privacidade`, migração 079). Vocabulário fechado.
MotivoDoFiltro = Literal["nao_texto", "vazio", "alfabetos", "simbolo_colado", "email_ofuscado", "endereco", "documento",
                         "ditado", "numerais", "sobra_de_forma"]

# ------------------------------------------------------------------ 1. troca por forma (do mais específico ao mais geral)
#: Aspas simples ASCII e crase só fora de palavra: o apóstrofo de "D'Ávila" não abre trecho. Depois do NFKC o "″" vira
#: "′′" e as aspas de largura cheia viram ASCII. Aspa que SOBRA (desbalanceada, de outro sistema, pontuação de abrir ou
#: fechar citação, colchete que não é ASCII) abre texto até o fim (`_primeira_aspa_sobrando`): na C3 o resto vira
#: `[texto]`; na C2, é cortado.
_ASPAS = re.compile(r'"[^"]*"|“[^”]*”|„[^“”]*[“”]|«[^»]*»|‹[^›]*›|‘[^’]*’|‚[^‘’]*[‘’]|「[^」]*」|『[^』]*』'
                    r"|〝[^〞〟]*[〞〟]|′+[^′]*′+|(?<!\w)'[^'\n]*'(?!\w)|`[^`]*`")
_CARACTERES_DE_ASPA: Final = frozenset("\"'`′″‴‵‶‷„‚“”‘’«»‹›「」『』〝〞〟❛❜❝❞")
_URL = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.\-]*://|www\.)\S+")
#: Domínio de topo do e-mail soletrado ou ofuscado ("correio ponto net", "dot co", "punto org").
_TLD: Final = (r"(?:com|net|org|br|io|co|gov|edu|info|biz|me|app|dev|test|pt|es|uk|us|de|fr|it|mx|ar|cl|uy|rf|online"
               r"|site|nl)")
#: Domínio de topo que vale SEM a palavra do ponto ("zilda at correio net"): só os que não são palavra comum em inglês
#: ("look at this app", "look at the site", "look at them online", "look at the info" passam).
_TLD_SEM_PONTO: Final = r"(?:com|net|org|br|gov|edu|pt|fr|mx|uy|ru|uk|jp|cn|xyz|tech|nl)"
#: Rodada H (H-1 e): o domínio de topo como PEÇA do e-mail ditado, sem ponto ("zilda em correio, net", "exemplo com br").
#: O "com" sozinho não conta: é a preposição ("mande para zilda em casa com carinho").
_TLD_PECA: Final = r"(?:com\s+(?:br|pt|ar|mx|uy|co|es|uk|au)|net|org|br|gov|edu|pt|nl|uk|xyz|tech)"
#: O e-mail inteiro (rodada E do 31.9, E-C): a parte local é a corrida sem espaço antes do "@" (RFC 5322 aceita "#", "!",
#: "'"...: "abcdef#zilda@" deixava "abcdef#" fora da máscara), sem o que abre ou separa trecho ("<", "(", vírgula); o
#: "mailto:" vem junto por estar colado, o "?subject=..." que segue o endereço também, e o domínio de topo separado por
#: espaço ("zilda@correio .net"). Desde a rodada H (H-2) também com o espaço DEPOIS do ponto ("zilda@correio. net",
#: "zilda@correio. com. br") e sem ponto ("zilda@correio net", "zilda@correio<quebra>net"; não "com", a preposição). A
#: frase que segue o e-mail com "me" minúsculo perde o "me" para a máscara ("zilda@correio.net. me avise"): sobra máscara,
#: não vazamento.
_EMAIL = re.compile(r"[^\s@<>()\[\]{}\"“”«»,;]+@[\w\-]+(?:\.[\w\-]+)*"
                    rf"(?:\s*\.\s*{_TLD}\b|\s+(?!com\b){_TLD_SEM_PONTO}\b)*(?:\?\S*)?")
#: Telefone com esquema de URI ("tel:+5511912345678"): o esquema e o "+" entram na máscara (saía "tel:+[telefone]").
_TELEFONE_URI = re.compile(r"(?i)\b(?:tel|sms|callto|facetime|whatsapp):\s*\+?\d[\d\s().\-/]*\d")
#: O `@handle` inteiro, também com hífen ("@cassia-brandao" saía `[usuario]-brandao`; decisão da orquestradora de 03/10).
#: Não termina em ponto nem hífen: o "." que fecha a frase fica.
_HANDLE = re.compile(r"@\w(?:[\w.\-]*\w)?")
#: Rodada F (F-F): a palavra soletrada, cinco ou mais letras soltas seguidas ("g i r a s s o l", "g-i-r-a-s-s-o-l"; desde a
#: rodada G também com vírgula e barra; desde a H com qualquer separador que não é letra nem algarismo, "g+i+r", "g · i"),
#: vira `[termo]`: lida junto, é a palavra inteira.
_SOLETRADO = re.compile(r"(?<![^\W\d_])[^\W\d_](?![^\W\d_])(?:[\W_]{1,3}[^\W\d_](?![^\W\d_])){4,}")
#: O domínio sem esquema: o último rótulo é domínio de topo conhecido ("exemplo.com.br", "bit.ly", "wa.me") ou vem antes de
#: um caminho ("loja.moda/promo"). Até a rodada H qualquer rótulo de 2 letras ou mais contava, e "siga maria.clara" e "p.ex."
#: saíam mutilados como `[link]` (casos 540 e 541 do corpus da H).
_TLD_DE_LINK: Final = (
    r"(?:com|net|org|edu|gov|int|mil|info|biz|name|pro|app|dev|page|site|online|store|shop|xyz|tech|club|link|live|news"
    r"|blog|art|cloud|digital|email|space|website|wiki|fun|top|vip|test"
    r"|ar|at|au|be|bo|br|ca|ch|cl|cn|co|cr|cz|de|dk|do|ec|es|eu|fi|fr|gg|gl|gt|hk|ie|il|in|io|it|jp|kr|ly|me|mx|my|nl"
    r"|no|nz|pe|ph|pl|pr|pt|py|ro|rs|ru|se|sg|sk|th|tk|to|tr|tv|tw|ua|uk|us|uy|ve|vn|ws|za)")
_DOMINIO = re.compile(rf"(?i)\b[\w\-]+(?:\.[\w\-]+)*\.(?:{_TLD_DE_LINK}\b(?:/\S*)?|[a-z]{{2,}}/\S*)\b")
_TELEFONE = re.compile(r"(?:\+?\d{1,3}[\s.\-])?\(?\d{2}\)?[\s.\-]?\d{4,5}[\s.\-]?\d{4}")
#: Qualquer número (também quebrado por UM separador entre dígitos: "12 34 56", "1.234", "11-98"). Até "pedido 12" é
#: identificador; a contagem ("curta 25 posts") perde pouco como `[numero]`.
_NUMERO = re.compile(r"\d(?:[\s.\-/]?\d)*")
#: Três ou mais algarismos por extenso em sequência, também ligados por "e", "y" ou "and" ("nove nove oito sete", "um e
#: um e um"): é telefone ou documento ditado. Recusa. Pega também "um", "uma", "uno" e "dos", que `_NUMERAIS` não conta.
_ALGARISMO_FALADO: Final = (r"(?:zero|um|uma|dois|duas|tr[eê]s|quatro|cinco|seis|meia|sete|oito|nove|one|two|three|four"
                            r"|five|six|seven|eight|nine|cero|uno|una|dos|cuatro|siete|ocho|nueve"
                            # rodada G: alemão, italiano e francês (o residual de outro idioma da rodada F, síntese item 11).
                            # Sem "sei" (italiano), que é o "sei" do português, e sem "un" (francês), que é artigo.
                            r"|null|eins|zwei|drei|vier|f(?:ü|u|ue)nf|sechs|sieben|acht|neun|due|tre|quattro|cinque|sette"
                            r"|otto|z[ée]ro|deux|trois|quatre|cinq|sept|huit|neuf"
                            # rodada H (H-1 d, lista de bloqueio): holandês, sueco, norueguês e dinamarquês. Sem "een"/"en"
                            # (artigo), "to" e "ni" (palavras do inglês e do espanhol)
                            r"|nul|twee|drie|vier|vijf|zes|zeven|negen|noll|ett|tv(?:å|a|aa)|fyra|fem|sex|sju"
                            r"|(?:å|a|aa)tta|nio|fire|seks|syv|(?:å|a|aa)tte)")
_DITADO = re.compile(rf"(?i)\b(?:{_ALGARISMO_FALADO}(?:\W+(?:e|y|and)\W+|\W+)){{2,}}{_ALGARISMO_FALADO}\b")

# ------------------------------------------------------------------ 2. recusa (não há máscara segura)
_RECUSA_EMAIL = re.compile(
    r"@|://|(?i:\bwww\b)|(?i:\barroba\b)|(?i:\bponto\s*com\b)|(?i:\bdot\s*com\b)"
    r"|(?i:[(\[{<]\s*(?:at|dot|arroba|ponto)\s*[)\]}>])")
#: Endereço: o logradouro seguido de qualquer termo (o nome da rua é dado de lugar de uma pessoa).
_RECUSA_ENDERECO = re.compile(
    r"(?i:\b(?:rua|r\.|avenida|av\.?|travessa|tv\.|alameda|al\.|pra[çc]a|rodovia|estrada|largo|viela|cep|apto|calle"
    r"|plaza|paseo|carretera)\b)"
    r"|(?i:\b(?:street|st\.|avenue|ave\.|road|rd\.|lane|blvd|boulevard|zip)\b)")
#: Rodada H (H-5): a palavra de lugar que NÃO é logradouro e é palavra comum ("a padaria do bairro", "a foto da casa", "o
#: bloco de notas", "a quadra de esportes") só é endereço com o NÚMERO logo depois ("casa 3", "quadra 5, lote 12", "bloco
#: nº 2", "quadra dez casa sete"). O logradouro continua recusando sozinho. Roda no texto COM os marcadores (o número já é
#: `[numero]`), sem acento e em casefold.
_ENDERECO_COM_NUMERO = re.compile(
    r"\b(?:bairro|barrio|casa|vila|bloco|quadra|lote|apartamento|condominio)\b"
    r"\s*[,:\-#]?\s*(?:(?:n|no|num|numero)\.?\s*)?(?:\[numero\]|(?:dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez"
    r"|onze|doze|treze|catorze|quatorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta"
    r"|sessenta|setenta|oitenta|noventa|cem|cento|mil)\b)")
#: Documento: o número que vem junto (em algarismo ou por extenso) identifica a pessoa.
_RECUSA_DOCUMENTO = re.compile(r"(?i:\b(?:cpf|cnpj|rg|cnh|passaporte|passport|ssn|dni|nif)\b)")
_SOBRA_DIGITOS = re.compile(r"\d\D{0,2}\d\D{0,2}\d")

# E-mail soletrado (rodada C do 31.9, 7 vazamentos): o "at" em outras línguas, a palavra do ponto em outras línguas, o
# domínio de topo fora da lista e o provedor sem domínio de topo.
_AT: Final = r"(?:at|chez|bei|bij)"
#: O ponto do domínio: colado ("correio.net"), com espaço ANTES ("correio . net") ou por extenso. O ponto que fecha a
#: frase ("look at this. It's") tem espaço só depois, e não conta, salvo diante de domínio de topo que não é palavra
#: ("correio. net": rodada H, H-2; "look at this. Me too" passa). "punt" é o holandês (H-1 e).
_SEP_DOMINIO: Final = (rf"(?:\.(?=\w)|\s+\.\s*|\.\s+(?={_TLD_SEM_PONTO}\b)|\s+(?:ponto|dot|punto|punkt|point|punt)\s+"
                      # rodada G (G-3): a palavra do ponto entre hífens ou sublinhados ("correio-dot-net")
                      r"|[\-_](?:ponto|dot|punto|punkt|point|punt)[\-_])")
#: Artigo ou determinante logo depois do "at": "look at the dot on the map" e "look at my point" não são e-mail.
_NAO_DOMINIO: Final = r"(?!(?:the|a|an|this|that|these|those|my|your|his|her|its|our|their)\b)"
#: Provedor de e-mail depois de "at" ou "arroba": é e-mail mesmo sem o domínio de topo ("zilda at gmail").
#: O provedor depois de um nome é e-mail ditado mesmo sem pista ("zilda no gmail", "zilda, no icloud", "zilda no live";
#: rodada E, E-B). "outlook", "live" e "terra" também são o app, a live do Instagram e palavra comum: o que vem antes
#: precisa ser nome, e não verbo nem objeto (`_NAO_DONO`: "entra no outlook", "comenta no live" e "a foto da terra"
#: passam). "correio" só com a pista de destinatário (`_PISTA_DE_EMAIL`).
_PROVEDOR_SO_DE_EMAIL: Final = (r"(?:gmail|googlemail|hotmail|outlook|yahoo|icloud|uol|bol|terra|msn|live|proton(?:mail)?"
                                r"|gmx|aol|yandex|zoho|fastmail|laposte|web\.de|mail\.ru|me\.com)")
#: O provedor que é só de e-mail, sem ser também app ou palavra comum (fora outlook, live e terra).
_PROVEDOR_SO_DE_EMAIL_E_NAO_APP: Final = (r"(?:gmail|googlemail|hotmail|yahoo|icloud|uol|bol|msn|proton(?:mail)?|gmx|aol"
                                          r"|yandex|zoho|fastmail|laposte|web\.de|mail\.ru|me\.com)")
#: O provedor que também é app ou palavra comum (o resto de `_PROVEDOR_SO_DE_EMAIL`).
_PROVEDOR_QUE_E_APP: Final = r"(?:outlook|live|terra)"
#: Rodada H (H-5): o nome logo depois de "e-mail da"/"e-mail do" é quem MANDOU a mensagem ("arquive o e-mail da newsletter
#: no outlook"), não o dono de um endereço. Lookbehind de largura fixa, um por forma.
_NAO_E_MENSAGEM: Final = "".join(
    f"(?<!{re.escape(f'{e} {p} ')})" for e in ("e-mail", "email", "e-mails", "emails", "mensagem", "mensagens")
    for p in ("da", "do", "de", "das", "dos"))
#: Antes do nome, o que diz que ele é o dono do endereço: "mande PARA zilda do outlook", "USUÁRIO zilda no gmail".
_PISTA_DE_EMAIL: Final = r"(?:para|pra|pro|usuario|usuaria|user|username|login|email|e-mail|contato|endereco)"
#: O que, no lugar do nome, não é dono de endereço: artigo, pronome e verbo ("entra no gmail", "o que chegou no gmail").
_NAO_DONO: Final = (r"(?:a|o|as|os|um|uma|e|que|mim|ele|ela|eles|elas|voce|vc|nos|todos|alguem|ninguem|isso|tudo|nada"
                    r"|la|aqui|ali|entra|entre|entrar|abre|abra|abrir|acessa|acesse|acessar|loga|logue|logar|login|conta"
                    r"|contas|caixa|inbox|email|emails|e-mail|mensagem|mensagens|pasta|lixo|spam|chegou|chegaram|veio"
                    r"|vieram|recebi|recebido|recebidos|enviado|enviados|enviei|mandei|mandou|esta|estao|tem|ficou|caiu"
                    r"|apareceu|aparece|salvo|salva|salvar|foto|fotos|arquivo|arquivos|anexo|anexos|logado|logada"
                    r"|cadastro|cadastrado|cadastrada|cadastre|crie|criar|criada|criado|sua|seu|minha|meu|dela|dele"
                    # verbos de comando e objetos do app: "comenta no live", "vai no outlook", "o post da terra"
                    r"|vai|va|olha|olhe|veja|ve|ver|procura|procure|pesquisa|pesquise|busca|busque|comenta|comente"
                    r"|curte|curta|responde|responda|posta|poste|publica|publique|manda|mande|envia|envie|escreve"
                    r"|escreva|le|leia|clica|clique|toca|toque|assiste|assista|segue|siga|fica|fique|entrou|logou|foi"
                    r"|viu|vi|abriu|comecou|comece|inicia|inicie|ta|tava|estava|aqui|post|posts|story|stories|reels"
                    r"|video|videos|comentario|comentarios|link|aviso|notificacao|convite|app|perfil"
                    r"|feed|planeta|volta|voltou|mundo"
                    # as pastas do e-mail: "a caixa de entrada do outlook" (2 dos 92 comandos reais, 03/10)
                    r"|entrada|saida|lixeira|rascunho|rascunhos|enviados|enviadas|arquivados|arquivadas|principal"
                    r"|promocoes|atualizacoes|pastas|lidos|lidas|importantes|favoritos"
                    # rodada G: o rótulo antes do provedor, que agora pode vir com hífen ou parêntese ("site - outlook")
                    r"|site|sites|aplicativo|plataforma|servico|provedor|tela|aba|pagina)")
_PROVEDOR: Final = (r"(?:gmail|googlemail|hotmail|outlook|yahoo|correio|live|icloud|uol|bol|terra|msn|proton(?:mail)?|gmx"
                    r"|aol|yandex|zoho|fastmail|laposte|web\.de|mail\.ru|me\.com)")
#: Rodada F (F-G): o provedor COLADO ao nome, sem preposição ("zilda gmail", "zilda, hotmail", "to zilda hotmail"). Além do
#: `_NAO_DONO`, o que vem antes do provedor não pode ser preposição nem verbo de usar o app ("entra no outlook", "a caixa
#: de entrada do outlook", "use o gmail", "configure outlook").
_ANTES_DO_PROVEDOR: Final = (r"(?:no|na|nos|nas|do|da|dos|das|de|em|pelo|pela|pro|pra|para|com|sem|ao|num|numa|via|by|in"
                             r"|on|at|to|the|my|your|our|his|from|with|using|into|and|or|ou|y|use|usa|usar|usando|usei"
                             r"|configure|configura|configurar|sincronize|sincroniza|sair|saia|logout|desconecte|conecte"
                             r"|conecta|conectar|baixe|baixa|instale|instala|atualize|atualiza|limpe|limpa|open|check"
                             r"|novo|nova|velho|velha|antigo|antiga|outro|outra|ultimo|ultima)")
#: Entre o nome e o provedor (ou a preposição antes dele): espaço, ou vírgula, parêntese ou hífen com espaço opcional
#: ("zilda - hotmail", "zilda (hotmail)", "zilda, no gmail"; rodada G, G-3). O dois-pontos não: é o do rótulo ("site:
#: outlook", 1 dos 122 comandos reais de 7 dias, 03/10).
_ENTRE_NOME_E_PROVEDOR: Final = r"(?:\s*[,(\-]\s*|\s+)"
@functools.lru_cache(maxsize=8)
def _palavras_dos_nomes(nomes: tuple[str, ...]) -> frozenset[str]:
    return frozenset(p for n in nomes for p in re.findall(r"[^\W_]+(?:['\-.][^\W_]+)*", sem_acento(normalizar(n)))
                     if len(p) >= 3)


#: De onde vêm os nomes dos apps da plataforma: um GANCHO, registrado na subida por quem conhece o registro de apps
#: (`taskqueue/service.py`, com `registry.nomes_e_apelidos`). O filtro não importa a camada de módulos: a descoberta de
#: apps importa módulos que importam o planejamento, e o import tardio que contornava o ciclo furava a catraca de
#: `test_arquitetura` (suíte 9). Sem registro, nenhum nome: vale só o vocabulário fixo.
_fonte_dos_apps: Callable[[], Iterable[str]] = tuple


def registrar_fonte_dos_apps(fonte: Callable[[], Iterable[str]]) -> None:
    """Liga a fonte dos nomes de app (nome, rótulo e apelidos de cada `app.yaml`). Lida a cada consulta, não guardada."""
    global _fonte_dos_apps
    _fonte_dos_apps = fonte


def nomes_dos_apps() -> frozenset[str]:
    """Os apps da PLATAFORMA como palavras do filtro, sem acento e em minúsculas ("instagram", "insta", "outlook",
    "microsoft"): nome, rótulo e apelidos de cada `app.yaml` (ADR-052: conhecimento de app é dado, não lista em Python),
    pela fonte registrada (`registrar_fonte_dos_apps`)."""
    return _palavras_dos_nomes(tuple(n for n in _fonte_dos_apps() if n))


@functools.lru_cache(maxsize=8)
def _recusas_no_original(apps: frozenset[str]) -> tuple[tuple[MotivoDoFiltro, re.Pattern[str]], ...]:
    """`_RECUSA_NO_ORIGINAL` com os nomes dos apps da plataforma no `_NAO_DONO` (ADR-052: vêm do `app.yaml`, não de uma
    lista em Python): "comenta no insta" diz o app, não o dono de um endereço."""
    nao_dono = _NAO_DONO if not apps else _NAO_DONO[:-1] + "".join("|" + re.escape(a) for a in sorted(apps)) + ")"
    return tuple((motivo, re.compile(padrao.pattern.replace(_NAO_DONO, nao_dono), padrao.flags))
                 for motivo, padrao in _RECUSA_NO_ORIGINAL)


#: A recusa que roda ANTES das máscaras (passada 1), sobre o texto sem acento, em casefold e com o algarismo trocado pela
#: letra parecida dentro de palavra (`arr0ba`). (motivo, padrão), na ordem do diagnóstico.
_RECUSA_NO_ORIGINAL: Final[tuple[tuple[MotivoDoFiltro, re.Pattern[str]], ...]] = (
    ("email_ofuscado", re.compile(
        # arroba por extenso, soletrada ou hifenizada ("a r r o b a", "a-r-r-o-b-a"); "(a)", "(a t)", "at-sign"
        r"(?<![^\W\d_])a[\W_]{0,2}r[\W_]{0,2}r[\W_]{0,2}o[\W_]{0,2}b[\W_]{0,2}a(?![^\W\d_])"
        r"|[(\[{<]\s*a\s*t?\s*[)\]}>]|\bat[\s\-]?sign\b"
        # domínio de topo depois de "ponto", "dot", "punto", "punkt" ou "punt": "correio ponto net", "dot co"
        rf"|\b(?:ponto|dot|punto|punkt|punt)\s*{_TLD}\b"
        # rodada F (F-G): "point" só diante de domínio de topo que não é palavra inglesa ("point fr"; "point it out" passa)
        rf"|\bpoint\s+{_TLD_SEM_PONTO}\b"
        # "marina at correio.net", "zilda at correio dot org", "zilda chez correio point net", "zilda at correio punkt de"
        rf"|\b\w+\s+{_AT}\s+\w+(?:{_SEP_DOMINIO}\w+)*{_SEP_DOMINIO}{_TLD}\b"
        # qualquer domínio de topo de 2 a 6 letras com o ponto colado ou por extenso: "zilda at correio dot tech"
        rf"|\b\w+\s+{_AT}\s+{_NAO_DOMINIO}\w+(?:{_SEP_DOMINIO}\w+)*"
        rf"(?:\.(?=\w)|\s+\.\s*|\s+(?:ponto|dot|punto)\s+)[a-z]{{2,6}}\b"
        # sem a palavra do ponto: "zilda at correio net", "ZILDA AT CORREIO NET"
        rf"|\b\w+\s+{_AT}\s+{_NAO_DOMINIO}\w+\s+{_TLD_SEM_PONTO}\b"
        # provedor conhecido: "zilda at gmail", "zilda arroba hotmail"
        rf"|\b\w+\s+(?:{_AT}|arroba)\s+{_PROVEDOR}\b"
        # rodada E (E-B), e-mail ditado em peças em português: "usuario zilda no gmail", "mande para zilda do outlook",
        # "mande para zilda em correio.net" e "o usuário é zilda e o domínio é correio.net"
        rf"|\b{_PISTA_DE_EMAIL}\s+(?!{_NAO_DONO}\b)(?<![\w\-])[^\W\d_]+\s+(?:no|na|do|da|de|em|pelo|pela)\s+"
        rf"(?:{_PROVEDOR}\b|\w+(?:\.\w+)*\.{_TLD}\b)"
        # rodada G (G-3): com qualquer pontuação entre o nome e a preposição, e "lá" no meio ("zilda, lá no gmail"). Desde a
        # rodada H (H-5) o provedor que também é app (outlook, live, terra) depois de "e-mail da X" é onde a MENSAGEM está
        # ("arquive o e-mail da newsletter no outlook"), salvo com o domínio de topo depois ("no outlook.com") ou com pista
        # de destinatário ("mande para o e-mail da zilda no outlook", regra abaixo)
        rf"|(?<![\w\-])(?!{_NAO_DONO}\b)[^\W\d_]+{_ENTRE_NOME_E_PROVEDOR}(?:(?:la|ali|aqui|tambem|ja)\s+)?"
        rf"(?:no|na|do|da|de|em|pelo|pela)\s+{_PROVEDOR_SO_DE_EMAIL_E_NAO_APP}\b"
        rf"|(?<![\w\-]){_NAO_E_MENSAGEM}(?!{_NAO_DONO}\b)[^\W\d_]+{_ENTRE_NOME_E_PROVEDOR}(?:(?:la|ali|aqui|tambem|ja)\s+)?"
        rf"(?:no|na|do|da|de|em|pelo|pela)\s+{_PROVEDOR_QUE_E_APP}\b"
        rf"|(?<![\w\-])(?!{_NAO_DONO}\b)[^\W\d_]+{_ENTRE_NOME_E_PROVEDOR}(?:(?:la|ali|aqui|tambem|ja)\s+)?"
        rf"(?:no|na|do|da|de|em|pelo|pela)\s+{_PROVEDOR_QUE_E_APP}(?:\.\w|\s+(?:ponto|dot|punto)\s|\s+{_TLD_SEM_PONTO}\b)"
        rf"|\b(?:para|pra|pro)\s+(?:o\s+)?e-?mail\s+d[aoe]s?\s+(?!{_NAO_DONO}\b)[^\W\d_]+\s+"
        rf"(?:no|na|do|da|de|em|pelo|pela)\s+{_PROVEDOR}\b"
        r"|\b(?:usuario|user|username|login|nome)\b[^.;!?\n]{0,40}\b(?:dominio|domain|provedor)\b"
        # rodada H (H-1 e): o e-mail com rótulo e os campos rotulados ("e-mail: zilda, provedor: gmail, terminação: com";
        # "e-mail: zilda / gmail / com"). Só com dois-pontos: "o e-mail do provedor caiu" passa
        r"|\be-?mail\s*[:=]\s*[^\W_][^;!?\n]{0,60}?\b(?:dominio|domain|provedor|terminacao|extensao|servidor|server)\b"
        rf"|\be-?mail\s*[:=]\s*[^\W_]+(?:\s*[/|,;]\s*[^\W_]+)+\s*[/|,;]\s*{_TLD}\b"
        # o e-mail ditado em peças sem ponto, com pista: "o e-mail dela é zilda em correio, net", "zilda no correio,
        # terminação net", "para zilda em exemplo com br", "para zilda correio net" (o "com" sozinho é a preposição)
        rf"|\b{_PISTA_DE_EMAIL}\s+(?:[^\W\d_]+\s+){{0,3}}?(?!{_NAO_DONO}\b)[^\W\d_]+\s+(?:em|no|na|do|da|de|pelo|pela|bij|at)"
        rf"\s+[^\W\d_]+\s*[,;:]?\s*(?:(?:terminacao|extensao|final|dominio)\s*[:=]?\s*)?{_TLD_PECA}\b"
        rf"|\b{_PISTA_DE_EMAIL}\s+(?!{_NAO_DONO}\b)[^\W\d_]+\s*,?\s*{_PROVEDOR}\s*[,.]?\s*{_TLD_PECA}\b"
        # rodada F (F-G): o nome colado ao provedor ("mande para zilda gmail", "send it to zilda hotmail", "zilda, gmail")
        rf"|(?<![\w\-])(?!(?:{_NAO_DONO}|{_ANTES_DO_PROVEDOR})\b)[^\W\d_]+{_ENTRE_NOME_E_PROVEDOR}{_PROVEDOR_SO_DE_EMAIL}\b"
        # rodada G (G-3): "at" ou "arroba" entre hífens ou sublinhados ("zilda-at-correio-net", "zilda_at_gmail_dot_com"),
        # só com provedor ou com domínio e domínio de topo depois ("@cafe_at_home" e "look-at-me" passam)
        rf"|(?<![^\W_])[^\W_]+[\-_]+(?:at|arroba|chez)[\-_]+(?:{_PROVEDOR}(?![^\W_])"
        rf"|[^\W_]+(?:[\-_.]+[^\W_]+)*?[\-_.]+{_TLD}(?![^\W_]))"
        # e o provedor antes do nome, com pista de destinatário ("mande um oi pro gmail da zilda"). Sem a pista, "abra o
        # gmail do lucas" é navegação e passa; o provedor que também é app ("manda pro Outlook da Ana a foto") também
        rf"|\b{_PISTA_DE_EMAIL}\s+(?:o\s+|a\s+)?{_PROVEDOR_SO_DE_EMAIL_E_NAO_APP}\s+(?:da|do|de)\s+(?!{_NAO_DONO}\b)"
        r"[^\W\d_]+(?![\w\-])"
        # `@` sem a parte local colada (separada por espaço ou tabulação), seguido de domínio com topo
        rf"|(?<![\w.+\-])@[\w\-]+(?:\.[\w\-]+)*\.{_TLD}\b")),
    ("endereco", re.compile(r"\bcaixa\s+postal\b|\bp\.?\s*o\.?\s+box\b|\bapartado\s+postal\b")),
    # cartão e CVV só com o número perto ("o cartão de visita do perfil" passa); título de eleitor e "ce pe efe" sempre
    ("documento", re.compile(r"\b(?:cartao|card|cvv|cvc)\b\D{0,20}\d{3}|\btitulo\s+de\s+eleitor\b|\bce\s+pe\s+efe\b"
                             # rodada F (F-H): o formato é o documento ("529.982.247-25"); os 11 algarismos seguidos só
                             # com pista de documento, porque o celular também tem 11
                             r"|(?<![\d.\-])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\d\-])"
                             r"|\b(?:documento|doc|identidade|cadastro)\b\D{0,15}\d{11}(?!\d)")),
)
#: Rodada H (carry-over da F, caso 539 do corpus): o "@" trocado por outro sinal antes de domínio com topo
#: ("zilda#correio.net", "zilda%correio.net"). Sem o leet (o "%40" de um link, que é o "@" codificado dentro da URL e que a
#: máscara de link apaga inteira, viraria "%ao"): por isso fica fora de `_RECUSA_NO_ORIGINAL`.
_ARROBA_TROCADA: Final = re.compile(
    rf"(?<![\w#])[^\W_][\w.\-]*(?:[#*&~]|%(?![0-9][0-9a-f]))[\w\-]+(?:\.[\w\-]+)*\.{_TLD}\b")
#: Endereço em inglês: número, uma a três palavras com maiúscula e o tipo de logradouro, no texto SEM casefold ("on the
#: way" não é endereço; "742 Evergreen Terrace" é).
_ENDERECO_EN: Final = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][\w'\-]*\s+){1,3}(?:Terrace|Drive|Court|Place|Way|Circle|Parkway|Highway|Square|Lane|Road"
    r"|Street|Avenue|Boulevard|Blvd|Ave|St|Rd|Dr|Ct|Pl|Ln)\b")
#: O algarismo que faz as vezes de letra dentro de uma palavra (leet). Só na passada 1 e na conferência da C7. O 5, o 7
#: e o 8 entraram na rodada C do 31.9 ("pa55word", "53nh4").
_LEET: Final = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "$": "s"})
_TOKEN_COM_LETRA: Final = re.compile(r"[\w$]*[^\W\d_][\w$]*")


def sem_leet(texto: str) -> str:
    """Troca algarismo por letra só nas palavras que já têm letra (`arr0ba`, `s3nh4`, `pa$$word`); número sozinho fica."""
    return _TOKEN_COM_LETRA.sub(lambda m: m.group().translate(_LEET), texto)
_PARENTESES_COM_DIGITO = re.compile(r"\(\s*\d")

#: Numerais por extenso (normalizados, sem acento). Cada um vira `[numero]`; dois ou mais SEGUIDOS, só com espaço, vírgula,
#: ponto, barra ou hífen entre eles ("nove oito", "dez, dez", "sete-sete"), recusam: é telefone, documento ou PIN ditado.
#: Até a rodada C do 31.9 (03/10) bastavam dois no texto, ligados por qualquer coisa, e "Ze Sete e Maria Onze" e "duas fotos
#: e três pessoas" recusavam (14 de 25 comandos comuns na sonda); a orquestradora trocou pela sequência. Ligados por "e",
#: três ou mais ainda recusam pelo `_DITADO` ("nove e oito e sete"). "um", "uma", "one", "uno" e "una" ficam de fora porque
#: são artigo, e "dos" (espanhol) porque em português é "de + os"; o `_DITADO` ainda pega "um um um" e "dos dos dos".
_NUMERAIS: Final[frozenset[str]] = frozenset("""
zero dois duas tres quatro cinco seis meia sete oito nove dez onze doze treze catorze quatorze quinze dezesseis dezasseis
dezessete dezassete dezoito dezenove dezanove vinte trinta quarenta cinquenta sessenta setenta oitenta noventa cem cento
duzentos duzentas trezentos trezentas quatrocentos quatrocentas quinhentos quinhentas seiscentos seiscentas setecentos
setecentas oitocentos oitocentas novecentos novecentas mil milhao milhoes bilhao bilhoes
two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen
twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million billion
cero cuatro siete ocho nueve diez once doce trece catorce quince dieciseis diecisiete dieciocho diecinueve veinte treinta
cuarenta cincuenta sesenta ochenta cien ciento
""".split())

# ------------------------------------------------------------------ 3. palavras
#: Palavra só de letras (com apóstrofo ou hífen dentro); `\w` sem dígito e sem `_`.
_PALAVRA = re.compile(r"[^\W\d_]+(?:['’\-][^\W\d_]+)*")
#: Só letras, sem apóstrofo nem hífen: a unidade da conferência de alfabetos.
_LETRAS = re.compile(r"[^\W\d_]+")
#: Token com letra E (dígito ou `_`), também ligado por hífen: handle sem `@`, identificador, a senha `limao77`, `kiwi-77`,
#: `lagoa-azul-3`. Vira `[termo]` INTEIRO; roda antes de `_NUMERO` (com o número primeiro, sobrava `limao[numero]`).
_MISTO = re.compile(r"(?<![\w\-])(?=[\w\-]*[^\W\d_])(?=[\w\-]*[\d_])\w+(?:-\w+)*(?![\w\-])")



# ------------------------------------------------------------------ normalização (o passo 0)
#: Somem depois do NFKC: marca combinante que sobrou (zalgo, risco sobre a letra) e caractere invisível de formato (ZWSP,
#: ZWJ, hífen suave, controle de direção). Assim "j̶o̶a̶n̶a̶" e "a<ZWSP>na" viram uma palavra só, julgada inteira.
_SOMEM: Final = frozenset({"Mn", "Mc", "Me", "Cf"})
#: Pontuação que fica no texto (o resto que não é letra, algarismo nem espaço é símbolo). A aspa ASCII que sobrevive a
#: `_primeira_aspa_sobrando` é só o apóstrofo entre letras; os colchetes são dos marcadores.
_PONTUACAO: Final = frozenset("!#$%&()*+,-./:;<=>?@[\\]^_{|}~'’¡¿")


def sem_acento(texto: str) -> str:
    """Para comparar: casefold e sem acento (NFKD sem as marcas combinantes)."""
    decomposto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


_chave = sem_acento


def normalizar(texto: str) -> str:
    """NFKC; sem marca combinante, caractere invisível de formato nem controle que não seja espaço; todo traço como "-" e
    separador de linha ou parágrafo como quebra de linha. Letra circulada, de largura cheia, matemática ou sobrescrita vira
    a letra comum. Idempotente."""
    saida: list[str] = []
    for c in unicodedata.normalize("NFKC", texto):
        cat = unicodedata.category(c)
        if cat in _SOMEM or (cat == "Cc" and not c.isspace()):
            continue
        saida.append("-" if cat == "Pd" else "\n" if cat in ("Zl", "Zp") else c)
    return unicodedata.normalize("NFKC", "".join(saida))


def _alfabeto(c: str) -> str:
    """O sistema de escrita da letra, pelo começo do nome Unicode. Hiragana, katakana e ideograma contam como um só: a
    escrita japonesa os mistura na mesma palavra."""
    primeiro = unicodedata.name(c, "?").split(" ", 1)[0]
    return "CJK" if primeiro in ("CJK", "HIRAGANA", "KATAKANA", "KATAKANA-HIRAGANA", "IDEOGRAPHIC") else primeiro


def mistura_alfabetos(texto: str) -> bool:
    """Alguma palavra mistura sistemas de escrita (o "а" cirílico em "senhа", "Јoana")? Homóglifo é o jeito de uma palavra
    passar por outra, e não há tabela de confusíveis na biblioteca padrão: quem chama RECUSA. Vale para o texto do catálogo
    (C2); o comando (C3) usa a regra mais estrita, `escrita_nao_latina`."""
    return any(len({_alfabeto(c) for c in m.group()}) > 1 for m in _LETRAS.finditer(texto))


def escrita_nao_latina(texto: str) -> bool:
    """Alguma letra do texto JÁ NORMALIZADO está fora do alfabeto latino ("a пароль do insta", "密码是", "κωδικός")? O comando
    é em português; a palavra-chave da credencial em outra escrita não cabe numa lista, e o homóglifo é o mesmo caso numa
    palavra só. Quem chama RECUSA. A letra modificadora sem equivalente ("ʼ") também conta: dentro de uma palavra, ela a
    esconde da conferência da C7. "ª" e "º" já saem do NFKC como "a" e "o"."""
    return any(_alfabeto(c) != "LATIN" for m in _LETRAS.finditer(texto) for c in m.group())


# ------------------------------------------------------------------ peças das duas passadas
def _primeira_aspa_sobrando(texto: str) -> int | None:
    """A posição da primeira aspa que sobrou depois de `_ASPAS` (desbalanceada, de outro sistema, pontuação de abrir ou
    fechar citação, colchete que não é ASCII), ou `None`. O apóstrofo entre duas letras ("D'Ávila", "d’água") não conta."""
    for i, c in enumerate(texto):
        if c in "'’" and 0 < i < len(texto) - 1 and texto[i - 1].isalpha() and texto[i + 1].isalpha():
            continue
        cat = unicodedata.category(c)
        if (c in _CARACTERES_DE_ASPA or cat in ("Pi", "Pf") or (cat in ("Ps", "Pe") and not c.isascii())
                or "QUOTATION" in unicodedata.name(c, "")):
            return i
    return None


def _formas(texto: str) -> str:
    """Link, e-mail, `@handle`, domínio, telefone, token misto e número viram marcador, do mais específico ao mais geral."""
    texto = _URL.sub(M_URL, texto)
    texto = _EMAIL.sub(M_EMAIL, texto)
    texto = _TELEFONE_URI.sub(M_TELEFONE, texto)
    texto = _HANDLE.sub(M_HANDLE, texto)
    texto = _SOLETRADO.sub(M_TERMO, texto)
    texto = _DOMINIO.sub(M_URL, texto)
    texto = _TELEFONE.sub(M_TELEFONE, texto)
    texto = _MISTO.sub(M_TERMO, texto)
    return _NUMERO.sub(M_NUMERO, texto)


def _e_simbolo(c: str) -> bool:
    return not (c.isalpha() or c.isdigit() or c.isspace() or c in _PONTUACAO)


def _simbolos(texto: str, *, recusar_colado: bool) -> str | None:
    """Cada sequência de símbolos (emoji, indicador regional, braille, letra em quadrado negativo, pontuação que não é
    ASCII) vira `[texto]`. Com `recusar_colado`, `None` se a sequência está entre duas letras ("jo❤ana"): do outro lado,
    a palavra seria lida inteira."""
    partes: list[str] = []
    i, n = 0, len(texto)
    while i < n:
        if not _e_simbolo(texto[i]):
            partes.append(texto[i])
            i += 1
            continue
        j = i
        while j < n and _e_simbolo(texto[j]):
            j += 1
        if recusar_colado and 0 < i and j < n and texto[i - 1].isalpha() and texto[j].isalpha():
            return None
        partes.append(M_TEXTO)
        i = j
    return "".join(partes)


def _sem_marcadores(texto: str) -> str:
    for m in _MARCADORES:
        texto = texto.replace(m, " ")
    return texto


def _recusa_no_original(texto: str) -> MotivoDoFiltro | None:
    """Passada 1: a forma escondida que a máscara apagaria (e-mail ofuscado, caixa postal, cartão, endereço em inglês)."""
    plano = sem_leet(sem_acento(texto))
    for motivo, padrao in _recusas_no_original(nomes_dos_apps()):
        if padrao.search(plano):
            return motivo
    if _ARROBA_TROCADA.search(sem_acento(texto)):
        return "email_ofuscado"
    return "endereco" if _ENDERECO_EN.search(texto) else None


def _recusa(marcado: str) -> MotivoDoFiltro | None:
    """O que sobrou sem os marcadores ainda tem forma que não se mascara com segurança? O motivo, ou `None`. O endereço
    com número (`_ENDERECO_COM_NUMERO`) se lê ainda com os marcadores."""
    resto = _sem_marcadores(marcado)
    if _RECUSA_EMAIL.search(resto):
        return "email_ofuscado"
    if _RECUSA_ENDERECO.search(resto) or _ENDERECO_COM_NUMERO.search(sem_acento(marcado)):
        return "endereco"
    if _RECUSA_DOCUMENTO.search(resto):
        return "documento"
    if _DITADO.search(resto):
        return "ditado"
    if _SOBRA_DIGITOS.search(resto) or _PARENTESES_COM_DIGITO.search(resto):
        return "sobra_de_forma"
    return None


def _limpar(texto: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", texto).strip()


#: O que pode ficar entre dois numerais SEGUIDOS: espaço, vírgula, ponto, barra e hífen ("e" não: `_DITADO` cuida).
_ENTRE_NUMERAIS = re.compile(r"[\s,./\-]*")


def _numerais(texto: str) -> tuple[str, int]:
    """Cada numeral por extenso vira `[numero]`; devolve o texto e a maior sequência de numerais seguidos. A palavra com
    hífen só de numerais ("sete-sete") conta cada parte; com outra palavra no meio ("meia-noite") fica como está."""
    maior = seguidos = 0
    fim_anterior: int | None = None

    def _troca(m: re.Match[str]) -> str:
        nonlocal maior, seguidos, fim_anterior
        partes = m.group().split("-")
        if not all(_chave(p) in _NUMERAIS for p in partes):
            return m.group()
        colado = fim_anterior is not None and _ENTRE_NUMERAIS.fullmatch(texto, fim_anterior, m.start()) is not None
        seguidos = (seguidos if colado else 0) + len(partes)
        maior = max(maior, seguidos)
        fim_anterior = m.end()
        return M_NUMERO

    return _PALAVRA.sub(_troca, texto), maior


# ------------------------------------------------------------------ a C3: o comando
def remover_entidades(texto: str) -> str | None:
    """O comando com o piso aplicado: forma conhecida vira marcador, e o que esconde e-mail, telefone ou documento recusa
    (`None`). Nome e palavra comum passam como estão (ADR-069 item 10). O motivo da recusa: `remover_entidades_com_motivo`.

    Devolve `None` quando: a entrada não é texto; alguma letra está fora do alfabeto latino; há forma escondida no texto
    ainda sem máscara (e-mail ofuscado ou soletrado, caixa postal, cartão, endereço em inglês); sobra símbolo colado entre
    letras; sobra forma que não se mascara com segurança (endereço, documento, e-mail por extenso, `@`, `://`); ou há dois
    ou mais numerais por extenso seguidos. Idempotente."""
    return remover_entidades_com_motivo(texto)[0]


def remover_entidades_com_motivo(texto: object) -> tuple[str | None, MotivoDoFiltro | None]:
    """`(comando limpo, None)` ou `(None, motivo)`. O texto que sobra vazio também é recusa (`vazio`)."""
    if not isinstance(texto, str):
        return None, "nao_texto"
    texto = normalizar(texto)
    if escrita_nao_latina(texto):
        return None, "alfabetos"
    trocado = _ASPAS.sub(M_TEXTO, texto)
    sobra = _primeira_aspa_sobrando(trocado)
    if sobra is not None:                        # a aspa que abre e não fecha: o resto é o texto a escrever
        trocado = f"{trocado[:sobra].rstrip()} {M_TEXTO}"
    # Passada 1, antes de qualquer máscara. O que estava entre aspas já é `[texto]`: é o que a pessoa manda ESCREVER.
    if (motivo := _recusa_no_original(trocado)) is not None:
        return None, motivo
    marcado = _simbolos(_formas(trocado), recusar_colado=True)
    if marcado is None:
        return None, "simbolo_colado"
    if (motivo := _recusa(marcado)) is not None:
        return None, motivo
    marcado, seguidos = _numerais(marcado)
    if seguidos >= 2:
        return None, "numerais"
    limpo = _limpar(marcado)
    return (limpo, None) if limpo else (None, "vazio")


# ------------------------------------------------------------------ a C2: o texto do catálogo nas opções da R2
def mascarar_catalogo(texto: str) -> str:
    """O texto do catálogo do dono (C2: nome e descrição de habilidade ou fluxo) para ir como descrição de opção da R2.

    O piso da C3 sem recusa (a opção precisa existir): as máscaras de forma (aspas, link, e-mail, handle, domínio,
    telefone, número, símbolo e token com `_`), o trecho a partir de uma aspa que sobra cortado (a legenda que o corte em
    120 caracteres deixou aberta) e numeral como `[numero]`. Nome passa (ADR-069 item 10). Texto com alfabetos misturados,
    endereço, documento ou e-mail por extenso sai vazio: quem chama põe um texto fixo."""
    if not isinstance(texto, str):
        return ""
    t = normalizar(texto)
    if mistura_alfabetos(t):
        return ""
    t = _ASPAS.sub(M_TEXTO, t)
    corte = _primeira_aspa_sobrando(t)
    if corte is not None:
        t = t[:corte]
    if _recusa_no_original(t) is not None:
        return ""
    t = _simbolos(_formas(t), recusar_colado=False) or ""
    if _recusa(t) is not None:
        return ""
    return _limpar(_numerais(t)[0])


__all__ = ["M_EMAIL", "M_HANDLE", "M_NUMERO", "M_TELEFONE", "M_TERMO", "M_TEXTO", "M_URL", "MotivoDoFiltro",
           "escrita_nao_latina", "mascarar_catalogo", "mistura_alfabetos", "normalizar", "remover_entidades", "remover_entidades_com_motivo",
           "nomes_dos_apps", "registrar_fonte_dos_apps", "sem_acento", "sem_leet"]
