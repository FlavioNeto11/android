"""Consumidor de SOMBRA da intenção (item 31.9, ADR-069): R2 (etapa semântica) e R3 (desempate), FORA da cadeia.

O que ele faz, depois que o `_plan` de uma execução terminou: monta UM pedido `DecisaoFechada` (origem `intencao`, classe C3,
modo `shadow`) com o comando sanitizado e duas perguntas `choice`, e deixa a porta gravar a sombra (31.5). Depois casa, nas
linhas gravadas, o que a cadeia REAL resolveu. Nada do que o Jev responde volta ao caminho de trabalho: a cadeia
(`intent_resolver`, `intent_ports`) não é tocada e a sombra só observa.

- **R2** (`intencao_catalogo`): `choice` sobre o catálogo inteiro (habilidades publicadas e fluxos ativos, como ids opacos com
  descrição C2) mais `nenhuma`. Só vai com 1 a 254 entradas: truncar enviaria um catálogo incompleto e a sombra mediria o
  que o Jev NÃO viu. Decisão real: a habilidade que a cadeia resolveu (ou de que fala, quando falta parâmetro), ou `nenhuma`
  quando nada casou. Empate sem desfecho: fica vazio (ninguém decidiu ainda).
- **R3** (`intencao_desempate`): `choice` entre os candidatos que a cadeia registrou como empatados (2 ou mais). Sem decisão
  real aqui: a cadeia em empate não escolhe; o rótulo é a escolha da pessoa ou o desfecho (31.10).
- **C3**: o comando passa por `redact` e pelo filtro SENSATO de `remover_entidades` (ADR-069 item 10: dado pessoal pode ir;
  e-mail, telefone, link, `@handle`, número e o que está entre aspas viram marcador; endereço, documento, e-mail ofuscado
  e numeral ditado recusam; nome e palavra comum passam). Não depende do catálogo. Recusa = estado VAZIO, e a porta grava
  `privacidade`: a linha da sombra existe, com o motivo, e nada sai. A sombra NÃO reduz o risco: em `shadow` o corpo sai
  igual para o decisor; a única proteção é o filtro.
- **C2** (as opções da R2): nome e descrição do catálogo passam por `mascarar_catalogo` ANTES do corte em
  `_DESCRICAO_MAX` (as mesmas máscaras de forma da C3: handle, aspas, e-mail, telefone e número).
- **C7** (senha, código, 2FA, captcha, chave, PIN, em prosa ou não, também com homóglifo, letra de largura cheia ou
  separada por ponto): marcador `credencial`, e a porta recusa o pedido inteiro. Desde a reverificação B (03/10) também o
  eufemismo ("a de sempre", "o que você digita"), a pergunta de segurança, a frase de recuperação, o leet, a palavra
  invertida, o controle de direção e o código pedido pela quantidade de dígitos (`motivo_c7`): C7 é RECUSA, a máscara não
  basta. O motivo vai para a linha da sombra (`motivo_privacidade`).
- **Desligado** (padrão: `enabled=false`, consumidor `off`; ou envio não aprovado; ou C3 fora das classes): `ativo()` é falso
  e o chamador não faz NADA, nem a RESOLVE.

O `desfecho` (`casar_desfecho`) não é preenchido aqui: precisa do fim da execução, que é núcleo compartilhado, e fica para o
31.10. O módulo não importa `modules.skills` nem o serviço de fila: quem o liga (`taskqueue/sombra_intencao.py`) converte.
"""
from __future__ import annotations

import difflib
import hashlib
import logging
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from ...security.redaction import looks_secret, mentions_credential, redact
from . import privacidade
from .contrato import ID_NENHUMA, MAX_OPCOES, PedidoDeDecisao, Pergunta, pergunta_choice
from .entidades import (
    escrita_nao_latina, mascarar_catalogo, nomes_dos_apps, normalizar, remover_entidades_com_motivo, sem_acento, sem_leet,
)
from .porta import Porta, modo_efetivo
from .sombra import RepositorioDeSombra

log = logging.getLogger("poc.ai")

PERGUNTA_CATALOGO: Final = "intencao_catalogo"
PERGUNTA_DESEMPATE: Final = "intencao_desempate"

#: Entradas do catálogo que cabem em R2 (a `nenhuma` ocupa a 255ª).
MAX_CATALOGO: Final = MAX_OPCOES - 1
_DESCRICAO_MAX: Final = 200
_APP: Final = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")
#: Por que o comando é C7 (`motivo_c7`), gravado na linha da sombra (`motivo_privacidade`, migração 079).
MotivoC7 = Literal["c7_bidi", "c7_palavra", "c7_formato", "c7_alfabetos", "c7_ofuscado", "c7_eufemismo", "c7_digitos",
                   "c7_login_valor", "c7_par_credencial", "c7_intencao_de_entrar"]
_LETRAS: Final = re.compile(r"[^\W\d_]+")
#: Assunto de C7 em qualquer formato, além do que `mentions_credential` já pega: na dúvida, o pedido inteiro é recusado
#: (ADR-069: C7 nunca sai, nem em sombra). Casa no texto normalizado, sem acento e em minúsculas, com até um separador
#: entre as letras ("p.i.n", "s e n h a", "palavra-passe") e plural. "passe" sozinho NÃO entra: é o imperativo de passar
#: ("passe para o próximo post"); "pass" e "palavra passe" entram (reverificação do 31.9, 03/10).
_PALAVRAS_C7: Final = (
    "codigo", "code", "captcha", "verificacao", "verification", "autenticacao", "authentication", "autenticador",
    "authenticator", "desafio", "senha", "password", "passwd", "pwd", "pass", "passcode", "passphrase", "palavrapasse",
    "contrasena", "clave", "chave", "key", "pin", "otp", "2fa", "mfa", "twofactor", "token", "segredo", "secret",
    "secreto",
    # reverificação B (03/10): outros idiomas ("mot de passe" e "parola d'ordine" casam pelos separadores entre letras)
    "passwort", "kennwort", "wachtwoord", "motdepasse", "paroladordine",
    # rodada C (03/10): abreviação e outras línguas de escrita latina ("hasło" fica com o "ł", que não se decompõe)
    "pw", "psw", "haslo", "hasło", "parola", "losenord", "sifre", "heslo", "jelszo", "salasana", "lozinka", "adgangskode",
    # rodada E (03/10): abreviação, a flexão de "secreto" e a credencial pelo nome
    "pswd", "pword", "passw", "secreta", "credencial", "credenciais", "credential", "credenciales",
    # rodada F (F-D): o diminutivo, que a fronteira de palavra não acha ("senhinha" não contém "senha"). As formas são
    # geradas da palavra, não do texto: tirar o sufixo do texto faria "rainha" virar "ra".
    "senhinha", "senhazinha", "senhita", "chavinha", "chavezinha", "chavita", "segredinho", "segredozinho", "segredito",
    "codiguinho", "codigozinho", "codigito", "clavecita", "clavita", "contrasenita", "secretinho", "secretito",
    "pinzinho", "tokenzinho")
#: "Senha" em línguas de escrita latina (rodada E do 31.9, E-A(1): a regra do dono é "em qualquer idioma"). São 56: as da
#: especificação da orquestradora e traduções de memória, NÃO conferidas uma a uma no Wiktionary (40 fora da lista
#: anterior). Entram pela MESMA normalização do texto (sem acento, casefold, sem espaço nem hífen): "mật khẩu" pré-composto
#: ou decomposto vira "matkhau", e "kata sandi" casa com um separador entre as letras. "passe" sozinho continua fora (é o
#: imperativo de passar): só conta com verbo de entrar na frase (`_EUFEMISMO_COM_ENTRAR`).
_SENHA_TRADUCOES: Final = (
    "wagwoord", "fjalëkalim", "şifrə", "parol", "pasahitza", "pasahitz", "šifra", "contrasenya", "kodeord", "paswoord",
    "pasvorto", "parool", "salasõna", "loyniorð", "contrasinal", "modpas", "kalmar sirri", "lykilorð", "kata sandi",
    "sandi lewat", "pasfhocal", "parole", "slaptažodis", "passwuert", "tenimiafina", "kata laluan", "kupuhipa", "passord",
    "parolă", "facal-faire", "geslo", "nenosiri", "neno la siri", "açarsöz", "mật khẩu", "cyfrinair", "iphasiwedi",
    "okwuntughe", "contraseña", "lösenord", "jelszó", "hasło", "mot de passe", "palavra-passe", "salasana", "heslo",
    "lozinka", "adgangskode", "wachtwoord", "kennwort", "passwort", "passcode", "passphrase", "codice di accesso",
    "senha de acesso", "clave de acceso")
#: A palavra-chave com até UM separador qualquer entre as letras ("p.i.n", "palavra-passe", "s/enha"), ou com um a três
#: separadores entre TODAS as letras ("s/e/n/h/a", "s  e  n  h  a", "s,e,n,h,a"): reverificação B do 31.9.
#: A lista inteira na forma de comparação: sem acento, em casefold, sem espaço, hífen nem apóstrofo.
_PALAVRAS_C7_TODAS: Final = tuple(dict.fromkeys(
    re.sub(r"[\s\-'’]", "", sem_acento(normalizar(p))) for p in (*_PALAVRAS_C7, *_SENHA_TRADUCOES)))
_ASSUNTO_C7: Final = re.compile(
    r"(?<![^\W_])(?:" + "|".join(
        alt for p in _PALAVRAS_C7_TODAS
        for alt in (r"[\W_]?".join(map(re.escape, p)), r"[\W_]{1,3}".join(map(re.escape, p)))) + r")s?(?![^\W_])")
#: As palavras-chave de uma palavra só, para a conferência da palavra INVERTIDA ("ahnes", "drowssap").
_C7_INVERTIDAS: Final[frozenset[str]] = frozenset(p[::-1] for p in _PALAVRAS_C7_TODAS if len(p) >= 3)
#: A palavra-chave DENTRO de outra palavra ("novasenha", "senhanova", "senha123", "mypassword") e a de escrita sem espaço
#: entre palavras ("密码是"), que a fronteira de `_ASSUNTO_C7` não acha (rodada C do 31.9). "resenha" e "desenha(r)" não
#: entram. A escrita não latina já recusa por `escrita_nao_latina`; a lista dá o motivo certo (`c7_palavra`). Cada palavra
#: passa pela MESMA normalização do texto: o NFKD separa o sinal do katakana ("パ") e a sílaba do hangul em letras, e o
#: casefold troca o "ς" final por "σ".
_C7_DENTRO_PALAVRAS: Final = (
    "password", "passwd", "passwort", "contrasena", "kennwort", "wachtwoord", "losenord", "motdepasse",
    # rodada E: as traduções longas e sem palavra comum dentro ("novacontrasenya", "slaptazodis123")
    "contrasenya", "slaptazodis", "codigo", "adgangskode", "salasana", "paswoord", "wagwoord", "pasahitza", "contrasinal",
    "fjalekalim", "katalaluan", "katasandi", "nenosiri", "cyfrinair", "pasfhocal", "passwuert", "iphasiwedi",
    "пароль", "密码", "密碼", "口令", "パスワード", "暗証番号", "비밀번호", "암호", "κωδικός", "συνθηματικό", "סיסמה",
    "كلمة المرور", "كلمة السر", "पासवर्ड")
_C7_DENTRO: Final = re.compile(
    r"(?<!re)(?<!de)senha|" + "|".join(re.escape(sem_acento(normalizar(p))) for p in _C7_DENTRO_PALAVRAS))
#: Eufemismo de credencial, pergunta de segurança, frase de recuperação e código pedido sem a palavra-chave (reverificação B
#: do 31.9, §7.2 e §7.5; decisão (a) da orquestradora: C7 é RECUSA do pedido inteiro, a máscara não basta). Casa no texto
#: sem acento, em casefold, com espaços colapsados.
_EUFEMISMO_C7: Final = re.compile("|".join((
    # português
    r"\ba de sempre\b", r"\b(?:aquela|a|essa) mesma de (?:sempre|antes|ontem|hoje|semana passada)\b",
    r"\bo que (?:voce|vc) digita\b",
    r"\ba outra parte e\b", r"\bsegundo campo\b", r"\b(?:tela|campo) de (?:acesso|entrada)\b", r"\bcampo de baixo\b",
    r"\bnao (?:conte|conta) (?:pra|para) ninguem\b", r"\ba minha nova e\b", r"\bde recuperacao\b",
    r"\bpergunta de seguranca\b", r"\banimal de estimacao\b", r"\bnome de solteira\b",
    r"\b(?:pra|para) (?:liberar|recuperar|destravar) a conta\b", r"\bque chegou (?:agora )?(?:no celular|por sms|por mensagem)\b",
    r"\bo que apareceu no (?:app|celular)\b",
    # rodada E (E-A(2)): palavra secreta ou mágica, lema e "a de acesso", o que chegou, o que se combinou por telefone,
    # e o campo de baixo do usuário
    r"\bpalavra[\s\-]+(?:secreta|magica|chave|de acesso|de entrada)\b", r"\blema de (?:acesso|entrada)\b",
    r"\b(?:a|o|as|os|dados|codigo|chave|numero) de (?:acesso|login|entrada)(?:\s+(?:e|eh|sao)\b|\s*[:=])",
    r"\b(?:os|as|o|a) (?:numeros|numerinhos|digitos|codigos|letras|numero|codigo) que (?:chegaram|chegou|vieram|veio"
    r"|mandaram|mandou|enviaram|enviou|recebi|apareceram|apareceu)\b",
    r"\b(?:o que|aquilo que|isso que|que) (?:a gente )?combinamos (?:pelo|por|no|na|via) (?:telefone|celular|whats\w*|zap"
    r"|sms|mensagem|ligacao)\b",
    r"\b(?:embaixo|abaixo|debaixo|em baixo|logo abaixo|depois) d[oa]s? (?:campo (?:d[oa] )?)?(?:usuario|user|login"
    r"|e-?mail|nome de usuario)\b",
    # rodada F (F-E): a pergunta de segurança sem a palavra ("a palavra de sempre é", "aquela que só eu sei é", "o nome do
    # meu primeiro cachorro é"). O "é" sem acento é o "e" da conjunção: "o post de sempre e comente" também recusa.
    r"\b(?:a|o|aquela|aquele|essa|esse|minha|meu)\s+(?:\w+\s+){0,3}?(?:de sempre|que so eu sei|que eu sempre uso"
    r"|que (?:a gente )?combinamos)\s*(?:e|eh|:|=)(?:\s|$)",
    r"\bo nome d[oa] (?:meu|minha) (?:primeir[oa]|antig[oa]|prefer\w*) \w+\s*(?:e|eh|:|=)(?:\s|$)",
    # rodada G (G-2): "a palavrinha é", "a de todo dia é", o substantivo "acesso" com dois-pontos ("acesso: x") ou com
    # verbo de pôr o valor ("para acesso use x"; diante de artigo é instrução: "para acesso use o menu")
    r"\b(?:a|minha|essa|nossa|sua) palavrinha(?: magica| secreta)?\s*(?:e|eh|:|=)(?:\s|$)",
    r"\b[ao] de todo (?:santo )?dia\s*(?:e|eh|:|=)(?:\s|$)",
    r"(?<![^\W_])acesso\s*[:=]\s*[^\W_]",
    r"\bacesso,?\s+(?:use|usa|usar|digite|digita|coloque|coloca|ponha|bota|insira)\s+"
    r"(?!(?:o|a|os|as|um|uma|seu|sua|meu|minha|esse|essa|este|esta)\b)[^\W_]",
    # inglês; "the usual is" é o residual de outro idioma da rodada F (síntese, item 11, recomendação não bloqueante)
    r"\bthe usual (?:is|one)\b",
    r"\bthe one i always use\b", r"\bwhat you type to (?:get in|log in|sign in)\b", r"\bthe access one\b",
    r"\bthe thing only you and i know\b", r"\bsecond field\b", r"\brecovery (?:phrase|codes?)\b", r"\bseed(?: phrase)?\b",
    r"\bsecurity question\b", r"\bmaiden name\b", r"\barrived by (?:text|sms)\b",
    r"\b(?:a|essa|minha|sua|the|la) (?:combinacao|combination|combinacion) (?:e|eh|is|es|:|=) \w",
    r"\b(?:magic|secret) word\b", r"\bbelow the (?:username|user|login|email)\b",
    r"\bthe (?:numbers|digits|code) (?:that|which) (?:came|arrived)\b",
    # espanhol
    r"\bl[ao] de siempre\b", r"\blo que tecleas\b", r"\bcampo de abajo\b", r"\bfrase de recuperacion\b",
    r"\bpregunta de seguridad\b", r"\bpalabra (?:secreta|magica|clave)\b",
    r"\blos (?:numeros|digitos|codigos) que (?:llegaron|vinieron|mandaron)\b", r"\bdebajo del (?:usuario|login)\b",
    # usuário e senha separados por barra: "entra com admin / admin1234", "log in with x / y"
    r"\b(?:entr\w*|log\s*in|login|sign\s*in)\s+(?:com|with|con)\s+\S+\s*/\s*\S+",
    # o par com rótulo e sem verbo de entrar (rodada C): "login: lucas / girassol", "usuário lucas, acesso girassol"
    r"\b(?:login|acesso|usuario|user|conta|username)\s*[:=]\s*\S+\s*/\s*\S+",
    r"\b(?:usuario|user|login|username)\s*:?\s*\S+\s*[,;]\s*(?:acesso|senha|pass|password)\s*:?\s*\S+",
)))
#: Eufemismo que só é C7 na frase com verbo de ENTRAR sem objeto de navegação (rodada E): "a combinação é girassol, entra",
#: "passe girassol e entra". Sozinhos são palavra comum ("a combinação de cores", "passe para o próximo post").
_EUFEMISMO_COM_ENTRAR: Final = re.compile(r"\b(?:combinacao|combination|combinacion|passe|passes)\b")
#: Rodada H: eufemismos que dependem do VERBO "é" (casam nos tokens, onde o "é" chega como "eh" e não se confunde com a
#: conjunção "e": "a cidade onde nasci É girassol" recusa, "poste a foto da cidade onde nasci E marque" passa).
_EUFEMISMO_EH: Final = re.compile("|".join((
    # "a de costume é", "o de praxe é"
    r"\b[ao]s? de (?:costume|praxe|habito) (?:eh|:|=)\s",
    # "o que eu digito (depois do nome) é", "o que eu coloco é"
    r"\bo que (?:eu )?(?:digito|coloco|ponho|uso|escrevo|preencho)\b(?: \S+){0,6}? (?:eh|:|=)\s",
    # "o acesso é com girassol" (não "o acesso é com a conta do lucas")
    r"\bacesso eh com (?!(?:a|o|as|os) (?:conta|perfil|persona)\b)",
    # "pra confirmar que sou eu: girassol"
    r"\bque sou eu (?:eh|:|=)\s",
    # pergunta de segurança pelo assunto: "a cidade onde nasci é", "o nome do meu cachorro é"
    r"\b(?:cidade|lugar|hospital|rua|bairro|pais|estado) (?:onde|em que|que) (?:eu )?nasci (?:eh|:|=)\s",
    r"\bnome d[oa] (?:meu|minha|nosso|nossa) (?:\S+ ){0,2}?(?:cachorr[oa]|cao|cadel[oa]|gat[oa]|pet|bicho|peixe|passaro"
    r"|cavalo|coelho|mae|pai|avo|avoh|vo|vovo|vovoh|escola|colegio|professor|professora|time|clube|filme|livro|heroi|rua"
    r"|bairro|cidade|melhor amig[oa]|primeir[oa] (?:\S+))s? (?:eh|:|=)\s",
    # espanhol e inglês: "lo mismo de siempre es", "la misma de ayer es", "my usual (one) is", "same as always:"
    r"\bl[oa]s? mism[oa]s? de (?:siempre|ayer|antes|hoy|la semana pasada) (?:es|:|=)\s",
    r"\bmy usual(?: one)? (?:is|:|=)\s", r"\bsame (?:one )?as (?:always|usual|before|yesterday) (?:is |: |= )",
)))

# ------------------------------------------------------------------ regra ESTRUTURAL de intenção de entrar (rodadas E e F)
# Independe da lista: o verbo de entrar e um valor ligado a ele é a credencial, com ou sem a palavra. Desde a rodada F
# (F-A), o verbo de entrar SEM objeto de navegação barra sozinho (`c7_intencao_de_entrar`), sem precisar achar o valor:
# "entra e curte", "entre com a girassol", "lucas e girassol, entra". Roda sobre os tokens do texto sem acento (palavra,
# número ou um sinal de pontuação por token; o handle "lucas.almeida9484" e "connecte-toi" são um token só).
_TOKEN: Final = re.compile(r"[^\W_]+(?:['\-.][^\W_]+)*|[^\w\s]")
_ENTRAR: Final[frozenset[str]] = frozenset((
    "entrar", "entre", "entra", "entrem", "entrando", "entro", "logar", "loga", "logue", "logando", "logue-se", "login",
    "logon", "signin", "acessar", "acesse", "acessa", "acessem", "acessando", "autenticar", "autentique", "autentica",
    "anmelden", "einloggen", "connecter", "connectez", "connecte", "accedi", "accedere", "inloggen", "conectar",
    "conecte", "conecta", "ingresar", "ingresa", "ingrese",
    # rodada F (F-A): a lista multilíngue da orquestradora
    "zaloguj", "acceder", "accede", "acceda", "entrer", "entrez",
    # rodada H (H-1): romeno e indonésio
    "intra", "masuk"))
#: O verbo de entrar no passado e no particípio ("entrei com girassol", "loguei com x", "logado com"; rodada H, H-1 a). Só
#: liga um VALOR (`_login_valor`): fora dele "o lucas já está logado" e "veja se ele entrou" são pergunta de estado, e a F-A
#: (verbo sem objeto de navegação) os recusaria.
_ENTRAR_PASSADO: Final[frozenset[str]] = frozenset((
    "entrei", "entrou", "entramos", "entraram", "loguei", "logou", "logamos", "logaram", "logado", "logada", "logados",
    "logadas", "acessei", "acessou", "acessamos", "acessaram", "autentiquei", "autenticou", "conectei", "conectou",
    "logged", "signed"))
#: "entre" também é preposição ("as fotos postadas ENTRE 10/05 e 12/05", "a diferença entre os dois"): no começo da oração
#: ou depois destas palavras é sempre verbo; depois de palavra de conteúdo, `_e_preposicao` decide pelo que vem depois.
_ANTES_DO_IMPERATIVO: Final[frozenset[str]] = frozenset((
    ",", ".", ";", "!", "?", ":", "-", "(", "e", "and", "y", "ou", "mas", "depois", "entao", "ai", "agora", "ja", "so",
    "pra", "para", "favor", "pf", "pfv", "voce", "vc", "tambem", "logo", "primeiro", "then", "now", "please", "pls",
    "que", "nao", "pode", "por"))
#: Rodada G (G-2 e G-5): depois de palavra de conteúdo, "entre" só é preposição diante de FAIXA (número, hora ou data dos
#: dois lados) ou do artigo plural e do pronome. Antes, qualquer palavra que não seguisse o verbo bastava, e "no insta entre
#: girassol e curta" lia o valor como a outra ponta da preposição.
_DEPOIS_DA_PREPOSICAO: Final[frozenset[str]] = frozenset(("os", "as", "eles", "elas"))
#: O que liga as duas pontas da faixa ("entre 8 E 12", "entre 10/05 A 12/05", "between 3 AND 5").
_LIGA_FAIXA: Final[frozenset[str]] = frozenset(("e", "a", "and", "to", "-", "/", "ate", "y", "et", "und"))
#: O mês e o dia da semana por extenso contam como data ("entre março e abril", "entre segunda e sexta").
_DATA_POR_NOME: Final[frozenset[str]] = frozenset((
    "janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro",
    "dezembro", "segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo", "segunda-feira", "terca-feira",
    "quarta-feira", "quinta-feira", "sexta-feira", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday", "enero", "febrero", "marzo", "mayo", "junio", "julio", "septiembre", "octubre", "noviembre",
    "diciembre", "lunes", "martes", "miercoles", "jueves", "viernes"))


def _ponta_da_faixa(toks: list[str], k: int) -> int:
    """Onde termina a ponta da faixa que começa em `k` ("8", "8h", "08 : 00", "12 / 09", "março"); `k` se não há ponta."""
    n = len(toks)
    if k < n and toks[k] in _DATA_POR_NOME:
        return k + 1
    if k >= n or not toks[k][:1].isdigit():
        return k
    k += 1
    while k + 1 < n and toks[k] in (":", "/", ".", "h") and toks[k + 1][:1].isdigit():
        k += 2
    return k


def _e_preposicao(toks: list[str], i: int) -> bool:
    """"entre" preposição: depois de palavra de conteúdo (o "é" verbo chega como "eh", `_tokens_de`), diante de faixa
    ("entre 8 e 12", "entre 08:00 e 12:00", "entre 12/09 e 15/09", "entre março e abril") ou de "os"/"as" ("é entre os
    melhores"). "no insta entre 4471 e curte" e "no insta entre girassol e curta" seguem verbo."""
    n = len(toks)
    if toks[i] != "entre" or i == 0 or i + 1 >= n or toks[i - 1] in _ANTES_DO_IMPERATIVO:
        return False
    if toks[i + 1] in _DEPOIS_DA_PREPOSICAO:
        return True
    meio = _ponta_da_faixa(toks, i + 1)
    return meio > i + 1 and meio + 1 < n and toks[meio] in _LIGA_FAIXA and _ponta_da_faixa(toks, meio + 1) > meio + 1


#: O verbo de entrar de duas palavras ("log in", "log into", "sign in", "inicia sessão", "zaloguj się").
_ENTRAR_2: Final[frozenset[tuple[str, str]]] = frozenset((
    ("log", "in"), ("log", "on"), ("sign", "in"), ("inicia", "sessao"), ("iniciar", "sessao"), ("inicie", "sessao"),
    ("inicia", "sesion"), ("iniciar", "sesion"), ("inicie", "sesion"),
    ("log", "into"), ("sign", "into"), ("sign", "on"), ("zaloguj", "sie"),
    # rodada G: o sueco e o catalão do residual de outro idioma (síntese da F, item 11, recomendação não bloqueante)
    ("logga", "in"), ("inicia", "sessio"), ("iniciar", "sessio"),
    # rodada H (H-1): norueguês, dinamarquês, tcheco, finlandês, turco e húngaro
    ("logg", "inn"), ("log", "ind"), ("prihlas", "se"), ("prihlaste", "se"), ("kirjaudu", "sisaan"), ("giris", "yap"),
    ("lepj", "be"), ("jelentkezz", "be")))
#: O de três ("faça o acesso", "melde dich an").
_ENTRAR_3: Final[frozenset[tuple[str, str, str]]] = frozenset((
    ("faca", "o", "acesso"), ("faz", "o", "acesso"), ("fazer", "o", "acesso"), ("facam", "o", "acesso"),
    ("melde", "dich", "an"), ("meld", "dich", "an"), ("melden", "sie", "sich"), ("logg", "dich", "ein")))
#: Entre o verbo e o objeto, pulado: "entre AGORA no app", "entre POR FAVOR no insta", "entre DE NOVO no feed".
_ADVERBIOS: Final[frozenset[str]] = frozenset((
    "agora", "ja", "logo", "rapidinho", "rapido", "novamente", "entao", "ai", "por", "favor", "pf", "pfv", "pls",
    "please", "now", "again", "ahora", "so", "apenas", "tambem", "de", "novo", "depois", "then", "too",
    # rodada H: o tempo e o reforço depois do destino ("entre com o lucas HOJE", "com o lucas MESMO"), que não são o valor
    # colado ao nome (G-4)
    "hoje", "amanha", "ontem", "cedo", "tarde", "ainda", "today", "tomorrow", "hoy", "manana", "mesmo", "mesma",
    "primeiro", "antes"))
class _ComOsApps:
    """Um vocabulário fixo e genérico ("app", "conta", "perfil", "feed") mais os nomes dos apps da plataforma
    (`nomes_dos_apps`). Só responde a `in`, que é o único uso destes conjuntos."""

    __slots__ = ("_fixos",)

    def __init__(self, fixos: Iterable[str]) -> None:
        self._fixos = frozenset(fixos)

    def __contains__(self, tok: object) -> bool:
        return tok in self._fixos or tok in nomes_dos_apps()


#: Serviços de terceiros SEM pacote na plataforma (sem pasta em `app/conhecimento/apps/`), onde se entra com conta ou
#: para onde se navega. Não são conhecimento de app da plataforma, e sair da lista abriria vazamento: com o "facebook"
#: fora de `_ONDE_SE_ENTRA`, "entra no facebook com girassol" passaria (há navegação, e a F-A não pega). Os apps da
#: plataforma NÃO entram aqui: vêm do registro.
_SERVICOS_SEM_PACOTE: Final[frozenset[str]] = frozenset(("gmail", "facebook", "tiktok", "whatsapp", "twitter", "chrome"))
#: Lugar logo depois do verbo: "entre NO perfil", "entre NESSA tela" (4 dos 92 comandos reais, 03/10), "log INTO the app".
_LUGAR: Final[frozenset[str]] = frozenset((
    "no", "na", "nos", "nas", "em", "num", "numa", "ao", "aos", "in", "into", "on", "onto", "to", "at", "en", "nel",
    "nella", "sul", "dans", "im", "auf", "op", "sur", "bei", "w", "nessa", "nesse", "nesta", "neste", "naquela",
    "naquele", "nisso", "nele", "nela", "dentro", "aqui", "ali"))
#: Artigo, possessivo e "de", pulados até a palavra que importa ("com A conta DO lucas", "acesse O app").
_ARTIGOS: Final[frozenset[str]] = frozenset((
    "a", "o", "as", "os", "um", "uma", "the", "an", "el", "la", "los", "las", "le", "les", "un", "una", "meu", "minha",
    "meus", "minhas", "seu", "sua", "seus", "suas", "my", "your", "his", "her", "mi", "tu", "su", "mon", "ma", "de",
    "do", "da", "dos", "das", "of", "del"))
#: O que diz QUAL conta usar ("com a conta", "com a persona", "como @"): destino, não valor (F-B).
_DESTINO_PALAVRA: Final[frozenset[str]] = frozenset((
    "conta", "contas", "persona", "personas", "perfil", "perfis", "profile", "account", "cuenta", "compte", "konto",
    "usuario", "usuaria", "user", "username"))
#: O provedor de login ("entre com o Google"): destino, não valor.
_PROVEDOR_DE_ENTRADA: Final[frozenset[str]] = frozenset((
    "google", "facebook", "apple", "microsoft", "gmail", "github", "biometria", "digital", "rosto", "face", "faceid",
    "sms"))
#: O objeto da navegação: com artigo antes, é navegação ("acesse O APP", "acesse a CONTA da Marina"); o artigo diante de
#: outra palavra não é ("entre com a girassol", "acesse o girassol").
_OBJETO_DE_NAVEGACAO: Final = _ComOsApps((
    "perfil", "perfis", "conta", "contas", "app", "aplicativo", "feed", "site", "pagina",
    "conversa", "conversas", "chat", "tela", "aba", "menu", "botao", "link", "atalho", "navegador", "porta", "grupo",
    "story", "stories", "reels", "reel", "post", "posts", "dm", "dms", "direct", "caixa", "pasta", "inbox",
    "configuracoes", "ajustes", "mensagens", "notificacoes", "sistema", "painel", "portal", "email",
    "e-mail", "banco", "account", "cuenta", "profile", "page", "website",
    "home", "inicio", "busca", "explorar", "video", "videos", "foto", "fotos", "live", "lives", "comentarios",
    "seguidores", "seguindo", "bio", "area", "loja", "lista", "pagamento", "jogo", "modo",
    *_SERVICOS_SEM_PACOTE))
#: Depois do objeto de navegação, o conector só liga um valor se o objeto é onde se entra com credencial ("entra no insta
#: com girassol"). Na conversa, no chat ou no perfil, o "com" é a pessoa ("entre na conversa com qa-001": 12 dos 92
#: comandos reais de 7 dias, medidos em 03/10).
_ONDE_SE_ENTRA: Final = _ComOsApps((
    "app", "aplicativo", "conta", "site", "sistema", "painel", "portal", "email", "e-mail", "banco", "account", "cuenta",
    *(s for s in _SERVICOS_SEM_PACOTE if s != "chrome")))
#: Rodada H (H-1 a): o objeto da navegação que é PESSOA ou conversa. Só depois dele o conector é a pessoa e não o valor
#: ("entre na conversa com qa-001", "entre no chat com a Marina", "entre em contato com a Ana"). Depois de qualquer outro
#: objeto ("entra AQUI com", "entre no FEED com", "entre no PERFIL com"), o conector seguido de valor desconhecido é a
#: credencial; o nome do catálogo ali é destino (H-3: "entre no perfil com Lucas").
_OBJETO_PESSOA: Final[frozenset[str]] = frozenset((
    "conversa", "conversas", "chat", "chats", "dm", "dms", "direct", "grupo", "grupos", "live", "lives", "chamada",
    "chamadas", "ligacao", "videochamada", "call", "sala", "reuniao", "contato", "contatos", "papo", "thread"))
#: Quem liga o verbo ao valor ("entre COM girassol", "login USANDO x", "inloggen MET x", "zaloguj się Z x").
_CONECTORES: Final[frozenset[str]] = frozenset((
    "com", "usando", "use", "usa", "utilizando", "with", "using", "con", "mit", "avec", "met", "z", "via", "through",
    "como", "pelo", "pela", "pelos", "pelas",
    # rodada H (H-1): norueguês e dinamarquês, romeno, indonésio, tcheco e finlandês ("kirjaudu sisään TUNNUKSELLA x")
    "med", "cu", "dengan", "s", "tunnuksella"))
#: A posposição que liga o valor ANTES do verbo ("girassol ILE giriş yap", turco) e o sufixo instrumental húngaro colado
#: ao valor ("lépj be girassol-LAL"): rodada H, H-1 a.
_POSPOSICOES: Final[frozenset[str]] = frozenset(("ile", "kanssa"))
_SUFIXO_INSTRUMENTAL: Final = re.compile(r"[^\W\d_]+-[^\W\d_]?[ae]l")
#: Logo depois do verbo, o separador que liga o valor ("pra entrar: girassol", "entre - girassol", "entre, girassol").
_SEPARADORES_DE_VALOR: Final[frozenset[str]] = frozenset((":", "/", "=", ",", "-"))
#: Rodada H: o determinante da conta ("outra", "a mesma", "qualquer", "a certa"). Não é valor; no par, é o rótulo do
#: segundo ("usuario lucas; a OUTRA: girassol"), pulado como o artigo.
_DETERMINANTES: Final[frozenset[str]] = frozenset((
    "outra", "outro", "outras", "outros", "qualquer", "alguma", "algum", "nenhuma", "nenhum", "certa", "certo", "errada",
    "errado", "correta", "correto", "nova", "novo", "antiga", "antigo", "principal", "another", "other"))
#: O que, no lugar do valor, NÃO é valor: artigo, pronome, conjunção, o objeto de navegação, o provedor de entrada
#: ("entre com o Google") e o modo ("com calma"). Tudo o mais conta: na dúvida, C7 é recusa.
_NAO_VALOR: Final = _ComOsApps((
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "e", "eh", "ou", "de", "do", "da", "dos", "das", "que", "pra", "para",
    "the", "an", "my", "your", "and", "or", "el", "la", "los", "las", "un", "una", "y", "mi", "tu", "su", "le", "les",
    "meu", "minha", "seu", "sua", "nosso", "nossa", "ele", "ela", "eles", "elas", "voce", "vc", "mim", "isso", "isto",
    "esse", "essa", "este", "esta", "aquele", "aquela", "dele", "dela", "it", "this", "that", "me", "him", "her",
    "conta", "contas", "perfil", "perfis", "persona", "personas", "usuario", "user", "app", "aplicativo",
    "aparelho", "celular", "telefone", "google", "facebook", "apple", "microsoft", "gmail",
    "email", "e-mail", "sms", "biometria", "digital", "face", "rosto", "calma", "cuidado", "carinho", "pressa", "atencao",
    "jeito", "emoji", "emojis", "foto", "fotos", "video", "imagem", "texto", "legenda", "comentario", "mensagem", "link",
    "voz", "audio", "account", "profile", "phone", "cuenta", "todos", "todas", "tudo",
    # rodada H: o resultado e a medida ("logado com sucesso", "entre no feed com mais calma") e o determinante da conta
    # ("veja se está logado com OUTRA conta", "com a conta CERTA"); "entre com outra conta" continua pela F-A
    "sucesso", "exito", "success", "mais", "menos", "maior", "menor", "melhor", "pior", "muito", "pouco",
    *_DETERMINANTES))
#: O verbo de ação que segue o destino ("com a conta Lucas e CURTA a foto"): não é o valor do par (F-C). Imperativo e
#: infinitivo dos comandos do parque, em português, inglês e espanhol.
_VERBOS_DE_ACAO: Final[frozenset[str]] = frozenset((
    "curta", "curte", "curtir", "comente", "comenta", "comentar", "siga", "segue", "seguir", "abra", "abre", "abrir",
    "poste", "posta", "postar", "publique", "publica", "publicar", "mande", "manda", "mandar", "envie", "envia",
    "enviar", "veja", "ve", "ver", "olhe", "olha", "olhar", "responda", "responde", "responder", "leia", "le", "ler",
    "procure", "procura", "procurar", "busque", "busca", "buscar", "pesquise", "pesquisa", "pesquisar", "clique",
    "clica", "clicar", "toque", "toca", "tocar", "role", "rola", "rolar", "va", "vai", "ir", "volte", "volta", "voltar",
    "salve", "salva", "salvar", "compartilhe", "compartilha", "compartilhar", "reposte", "marque", "marca", "marcar",
    "edite", "edita", "editar", "apague", "apaga", "apagar", "delete", "bloqueie", "denuncie", "saia", "sai", "sair",
    "feche", "fecha", "fechar", "mostre", "mostra", "mostrar", "tire", "tira", "grave", "grava", "escreva", "escreve",
    "copie", "copia", "cole", "baixe", "baixa", "instale", "instala", "atualize", "atualiza", "configure", "ative",
    "ativa", "desative", "aceite", "aceita", "recuse", "confirme", "confirma", "troque", "troca", "mude", "muda",
    "adicione", "adiciona", "remova", "remove", "convide", "chame", "chama", "ligue", "liga", "assista", "assiste",
    "ouca", "ouve", "deixe", "deixa", "faca", "faz", "fazer", "diga", "diz", "pergunte", "pergunta", "conte", "verifique",
    "verifica", "confira", "confere", "espere", "espera", "aguarde", "repita", "repete", "continue", "continua", "pare",
    "para", "inicie", "comece", "comeca", "termine", "finalize", "like", "comment", "follow", "open", "send", "reply",
    "read", "post", "share", "save", "check", "go", "see", "look", "watch", "write", "tap", "click", "scroll", "search",
    "find", "dale", "abre", "sigue", "comenta", "mira", "envia"))
#: Fim de oração: o valor e o verbo não se ligam através dele.
_FIM_DE_ORACAO: Final[frozenset[str]] = frozenset((".", ";", "!", "?", "\n"))
#: Onde a busca do conector para: fim de oração, vírgula e conjunção ("entre e comente COM parabéns" é outro verbo).
_PARA_A_BUSCA: Final[frozenset[str]] = _FIM_DE_ORACAO | frozenset((",", "e", "and", "y", "ou", "or", "depois", "entao",
                                                                   "then"))
#: O campo de usuário ("usuário lucas e girassol", "conta lucas e girassol", "nome lucas e girassol"): o par de valores
#: depois dele é usuário e senha (F-C: conta, persona, nome, perfil, login e user, desde a rodada F).
_CAMPO_DE_USUARIO: Final[frozenset[str]] = frozenset((
    "usuario", "usuaria", "user", "username", "login", "conta", "contas", "persona", "nome", "perfil", "account",
    "profile", "cuenta", "usr"))
#: O campo que forma o par mesmo SEM separador, no fim da oração ("conta André girassol").
_CAMPO_FORTE: Final[frozenset[str]] = frozenset(("usuario", "usuaria", "user", "username", "login", "conta", "account",
                                                 "cuenta", "usr"))
#: O campo de usuário que forma o par com separador NÃO alfabético mesmo sem verbo de entrar ("usuário lucas, girassol.":
#: rodada G, G-2; "usuario lucas; a outra: girassol", "user lucas | girassol", "usr lucas, girassol": rodada H, H-1 b). Sem
#: "conta" e "perfil", que também são o lugar da navegação ("na conta lucas, comente"), nem "nome" e "persona", que listam
#: várias contas ("persona lucas; persona bruno").
_CAMPO_DE_LOGIN: Final[frozenset[str]] = frozenset(("usuario", "usuaria", "user", "username", "login", "usr"))
_SEPARADORES_DO_PAR: Final[frozenset[str]] = frozenset(("e", ",", "/", ";", "and", "y", "-", ":", "&", "+", "|", "·"))
#: Os separadores do par que não são palavra: com eles e o campo de login, o par vale sem verbo de entrar (H-1 b).
_SEPARADORES_NAO_ALFABETICOS: Final[frozenset[str]] = frozenset((",", ";", "|", ":", "/", "-", "&", "+", "·"))
_ANTES_DE_PRA_ENTRAR: Final[frozenset[str]] = frozenset((
    "use", "usa", "usar", "digite", "digita", "coloque", "coloca", "bota", "poe", "ponha", "insira", "informe", "type",
    "enter"))
#: A palavra soletrada ("g i r a s s o l", "g-i-r-a-s-s-o-l"; desde a rodada G também "g, i, r" e "g/i/r"; desde a H com
#: qualquer separador que não é letra nem algarismo: "g+i+r", "g · i · r"): cinco ou mais letras soltas seguidas (F-F). Na
#: C7 é ofuscação e recusa (o corpus espera recusa, mais estrito que a máscara da especificação); no filtro, `[termo]`.
_SOLETRADO: Final = re.compile(r"(?<![^\W\d_])[^\W\d_](?![^\W\d_])(?:[\W_]{1,3}[^\W\d_](?![^\W\d_])){4,}")
#: A palavra soletrada pelo NOME das letras (rodada G, G-2: "ge, i, erre, a, esse, esse, o, ele"), misturado ou não com a
#: letra solta: cinco ou mais, com vírgula, barra, ponto ou hífen entre eles. Só com espaço não conta ("ele e ela").
_NOMES_DAS_LETRAS: Final = (
    "dabliu|dablio|ipsilon|equis|jota|efe|gue|aga|capa|ele|eme|ene|erre|ere|esse|ese|xis|zeta|be|ce|de|fe|ge|ka|ca|pe"
    "|que|ke|te|ve|ze")
_LETRA_OU_NOME: Final = rf"(?:{_NOMES_DAS_LETRAS}|[^\W\d_])(?![^\W_])"
_SOLETRADO_POR_NOME: Final = re.compile(rf"(?<![^\W_]){_LETRA_OU_NOME}(?:\s*[^\w\s]\s*{_LETRA_OU_NOME}){{4,}}")
#: Rodada H (H-1 c): depois de um verbo de DIGITAR, a corrida curta de letras soltas ou nomes de letra. Com separador que não
#: é espaço ("g+i", "g · i", "ge, i", "x/y"), duas bastam; só com espaço, três FORTES: a letra solta que não é palavra
#: (não "a", "e", "o", "y", "u") e o nome de letra que não é palavra comum ("ge", "erre", "esse"; não "de", "que", "ele",
#: "te"). "use a e o como exemplo" e "digite que ele te ama" passam.
_DIGITAR: Final[frozenset[str]] = frozenset((
    "digite", "digita", "digitar", "use", "usa", "usar", "coloque", "coloca", "colocar", "ponha", "poe", "bota", "insira",
    "insere", "tecle", "tecla", "escreva", "escreve", "type", "enter", "write", "escribe", "escriba", "teclea", "soletro",
    "soletra", "soletre"))
_NOMES_DE_LETRA: Final[frozenset[str]] = frozenset(_NOMES_DAS_LETRAS.split("|"))
_NOMES_DE_LETRA_FORTES: Final[frozenset[str]] = frozenset((
    "dabliu", "dablio", "ipsilon", "equis", "jota", "efe", "gue", "aga", "eme", "ene", "erre", "ere", "esse", "ese", "xis",
    "zeta", "ge", "ka", "ke"))
_LETRAS_QUE_SAO_PALAVRA: Final[frozenset[str]] = frozenset(("a", "e", "o", "y", "u"))
_ANTES_DAS_LETRAS: Final[frozenset[str]] = frozenset(("as", "os", "a", "o", "letras", "letra", "iniciais", ":", "="))


def _letras_depois_de_digitar(toks: list[str]) -> bool:
    """A corrida curta de letras soltas ou nomes de letra logo depois de um verbo de digitar (H-1 c)."""
    n = len(toks)
    for v, tok in enumerate(toks):
        if tok not in _DIGITAR:
            continue
        i = v + 1
        # "digite AS LETRAS g, i"; o artigo só é pulado se o que vem depois ainda é letra ou rótulo
        while i < n - 1 and toks[i] in _ANTES_DAS_LETRAS and (
                toks[i + 1] in _ANTES_DAS_LETRAS or _e_letra(toks[i + 1])):
            i += 1
        letras = fortes = 0
        pontuacao = False
        while i < n:
            t = toks[i]
            if _e_letra(t):
                letras += 1
                fortes += t in _NOMES_DE_LETRA_FORTES or (len(t) == 1 and t not in _LETRAS_QUE_SAO_PALAVRA)
            elif not t[0].isalnum() and t != "\n" and letras and i + 1 < n and _e_letra(toks[i + 1]):
                pontuacao = True
            else:
                break
            i += 1
        if (pontuacao and letras >= 2) or fortes >= 3:
            return True
    return False


def _e_letra(tok: str) -> bool:
    return (len(tok) == 1 and tok.isalpha()) or tok in _NOMES_DE_LETRA


def _tokens(plano: str) -> list[str]:
    return _TOKEN.findall(plano)


def _tokens_de(normal: str) -> list[str]:
    """Os tokens do texto JÁ normalizado, sem acento e em casefold, com o "é" verbo como "eh" (rodada G, G-5): sem o acento
    ele vira a conjunção "e", e "a entrega é entre 8 e 12" lia "e entre" como o imperativo. Se a remoção do acento mudar a
    contagem de tokens, fica o "e" (o lado seguro: mais recusa)."""
    toks = _tokens(" ".join(sem_acento(normal).split()))
    com_acento = _tokens(" ".join(normal.casefold().split()))
    if len(com_acento) == len(toks):
        return ["eh" if a == "é" else t for t, a in zip(toks, com_acento, strict=True)]
    return toks


def _verbos_de_entrar(toks: list[str], *, passado: bool = False) -> list[tuple[int, int]]:
    """(início, fim) de cada verbo de entrar, de uma, duas ou três palavras; o hífen ("connecte-toi", "logue-se") conta
    pela primeira parte, e desde a rodada G (G-2) também o verbo de duas ou três palavras ligado por hífen ("log-in",
    "sign-in"). Com `passado`, também o passado e o particípio (`_ENTRAR_PASSADO`, rodada H)."""
    entrar = _ENTRAR | _ENTRAR_PASSADO if passado else _ENTRAR
    achados: list[tuple[int, int]] = []
    i, n = 0, len(toks)
    while i < n:
        if i + 2 < n and (toks[i], toks[i + 1], toks[i + 2]) in _ENTRAR_3:
            achados.append((i, i + 2))
            i += 3
            continue
        if i + 1 < n and (toks[i], toks[i + 1]) in _ENTRAR_2:
            achados.append((i, i + 1))
            i += 2
            continue
        partes = tuple(toks[i].split("-")) if "-" in toks[i] else ()
        if not _e_preposicao(toks, i) and (toks[i] in entrar or (partes and (
                partes[0] in entrar or partes[:2] in _ENTRAR_2 or partes[:3] in _ENTRAR_3))):
            achados.append((i, i))
        i += 1
    return achados


def _pula(toks: list[str], i: int, conjunto: frozenset[str] | tuple[str, ...]) -> int:
    while i < len(toks) and toks[i] in conjunto:
        i += 1
    return i


def _e_valor(tok: str) -> bool:
    return (bool(_TOKEN.fullmatch(tok)) and tok[0].isalnum() and tok not in _NAO_VALOR and tok not in _VERBOS_DE_ACAO
            and tok not in _ADVERBIOS)


@dataclass(frozen=True)
class _Destinos:
    """Onde o comando cita destino, por posição de token, com o fim (exclusivo) de cada menção (rodada G, G-4).

    - `cortados`: o que o extrator tirou (`sem_destinos`), a SINTAXE de destino ("com a conta Lucas", "pela Lucas", "como
      @lucas"). Vale como destino onde estiver.
    - `catalogo`: o nome INTEIRO do catálogo real solto no texto (`nomes_de_destino`). É destino depois da palavra de conta
      ou do "@" ("na conta Lucas", "com @lucas.almeida9484") e, desde a rodada H (H-3), sozinho logo depois do conector do
      verbo de entrar ("entre com o lucas", "entre como lucas", "entre no perfil com Lucas"): é o verbo central do produto.
      Nunca na posição de VALOR (G-4): o segundo do par ("entre com a conta Lucas e girassol"), o colado ao nome do usuário
      ("entre com o lucas girassol") e o conector depois de um destino ("acesse como lucas com girassol") recusam mesmo com
      uma persona "Girassol". A persona com o nome da própria senha ("entre com girassol") é residual aceito pela
      orquestradora (03/10).
    - O fim da menção faz do nome de duas palavras UMA menção ("Lucas Almeida") e de "André girassol", duas."""

    cortados: Mapping[int, int] = field(default_factory=dict)
    catalogo: Mapping[int, int] = field(default_factory=dict)

    def citado(self, i: int) -> bool:
        return i in self.cortados or i in self.catalogo

    def fim(self, i: int) -> int:
        return self.cortados.get(i) or self.catalogo.get(i) or i + 1


_SEM_DESTINOS: Final = _Destinos()


def _cortados(toks: list[str], sem_destinos: str) -> dict[int, int]:
    """As posições de `toks` (o ORIGINAL) que o `sem_destinos` tirou, cada uma com o fim do seu trecho: o destino que o
    extrator casou com o catálogo REAL (personas, handles e aparelhos, inclusive aposentados). É assim que "com a conta Lucas"
    vira destino e "com a conta girassol" não (F-B)."""
    outros = _tokens_de(normalizar(sem_destinos))
    tirados: dict[int, int] = {}
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, toks, outros, autojunk=False).get_opcodes():
        if tag in ("delete", "replace"):
            tirados.update(dict.fromkeys(range(i1, i2), i2))
    return tirados


def nomes_de_destino(nomes: Iterable[str]) -> frozenset[str]:
    """Os nomes do catálogo de destinos REAL (personas, inclusive aposentadas e bloqueadas, handles e aparelhos), cada um
    INTEIRO na forma dos tokens do filtro, separados por espaço ("lucas almeida", "lucas.almeida9484", "android-01"), sem o
    "@". Desde a rodada G (G-4) a palavra solta de um nome de várias não conta: a persona "Sol Nascente" não faz de "sol" um
    destino. O nome de uma palavra só que é palavra de ligação ou verbo fica de fora."""
    saida: set[str] = set()
    for nome in nomes:
        toks = _tokens_de(normalizar(str(nome).strip().lstrip("@")))
        if toks and (len(toks) > 1 or (len(toks[0]) >= 2 and toks[0][0].isalnum() and toks[0] not in _NAO_VALOR
                                         and toks[0] not in _ARTIGOS and toks[0] not in _CONECTORES
                                         and toks[0] not in _LUGAR and toks[0] not in _ADVERBIOS
                                         and toks[0] not in _VERBOS_DE_ACAO and toks[0] not in _ENTRAR)):
            saida.add(" ".join(toks))
    return frozenset(saida)


def _no_catalogo(toks: list[str], nomes: frozenset[str]) -> dict[int, int]:
    """Onde um nome INTEIRO de `nomes` aparece em `toks` (o mais longo primeiro), cada posição com o fim da menção."""
    if not nomes:
        return {}
    por_tamanho = sorted({len(n.split(" ")) for n in nomes}, reverse=True)
    achados: dict[int, int] = {}
    i = 0
    while i < len(toks):
        tamanho = next((t for t in por_tamanho if i + t <= len(toks) and " ".join(toks[i:i + t]) in nomes), 0)
        if tamanho:
            achados.update(dict.fromkeys(range(i, i + tamanho), i + tamanho))
            i += tamanho
        else:
            i += 1
    return achados


def _destino(toks: list[str], k: int, d: _Destinos, *, catalogo: bool = True) -> bool:
    """Em `k` (depois do conector, sem artigo) está QUAL conta usar, e não um valor: o destino que o extrator tirou, o "@"
    diante de handle do catálogo, o provedor de login ou o lugar onde se entra; ou a palavra de conta seguida do dono ("a
    conta DO lucas"), de um nome do catálogo ou de "@". "A conta girassol", com um nome que o catálogo não conhece, é valor
    (F-B). Desde a rodada G (G-1), o "@" solto não é destino: "com @zilda.prado e girassol" é o usuário de um par. Desde a
    rodada H (H-3), o nome INTEIRO do catálogo sozinho também é destino ("entre com o lucas"), salvo com `catalogo=False`:
    depois de um destino já dito, o conector seguinte é a posição de valor ("acesse como lucas com girassol", G-4)."""
    n = len(toks)
    if k >= n:
        return False
    if (k in d.cortados or (toks[k] == "@" and d.citado(k + 1)) or toks[k] in _PROVEDOR_DE_ENTRADA
            or (toks[k] in _ONDE_SE_ENTRA and toks[k] not in _DESTINO_PALAVRA) or (catalogo and k in d.catalogo)):
        return True
    # a palavra de conta antes do lugar onde se entra: "conta" é dos dois, e "com a conta girassol" é valor (F-B; até a
    # rodada H o lugar vinha primeiro e o deixava passar)
    if toks[k] in _DESTINO_PALAVRA:
        x = k + 1
        if x < n and toks[x] in ("do", "da", "dos", "das", "de", "of"):
            return True
        x = _pula(toks, x, (":", "="))
        return x >= n or d.citado(x) or toks[x] == "@" or not _e_valor(toks[x])
    return False


def _navega(toks: list[str], fim: int, d: _Destinos, *, por_conector: bool = True) -> bool:
    """O verbo de entrar que termina em `fim` tem objeto de navegação (F-A)? Lugar ("no", "nessa", "into"), artigo
    diante de objeto ("acesse o app"), o objeto direto, o destino que o extrator tirou e, com `por_conector`, o conector
    seguido de destino ("com a conta do lucas", "como @lucas", "com o Google" e, desde a H-3, "com o lucas"). O nome do
    catálogo como objeto DIRETO, sem conector, não é destino (G-4): "acesse girassol e curta" recusa mesmo com uma persona
    "Girassol"."""
    n = len(toks)
    j = _pula(toks, fim + 1, _ADVERBIOS)
    if j >= n:
        return False
    if j in d.cortados and por_conector:
        return True
    t = toks[j]
    if t in _LUGAR or t in _OBJETO_DE_NAVEGACAO:
        return True
    if t in _ARTIGOS:
        # o objeto pode vir depois de até dois nomes ("log into the lucas profile", "acesse o novo app")
        k = _pula(toks, j, _ARTIGOS)
        for m in range(k, min(k + 3, n)):
            if toks[m] in _OBJETO_DE_NAVEGACAO or m in d.cortados:
                return True
            if toks[m] in _PARA_A_BUSCA:
                break
        return False
    if por_conector and t in _CONECTORES:
        k = _pula(toks, j + 1, _ARTIGOS)
        # "pela página", "pelo link"; a palavra de conta só pelo `_destino` (o nome que o catálogo não conhece é valor)
        return _destino(toks, k, d) or (k < n and toks[k] in _OBJETO_DE_NAVEGACAO and toks[k] not in _DESTINO_PALAVRA)
    return False


def _entrar_sem_navegacao(toks: list[str], d: _Destinos = _SEM_DESTINOS) -> bool:
    """Há verbo de entrar sem objeto de navegação ("entra e curte", "girassol, entra", "entre com a girassol")?"""
    return any(not _navega(toks, fim, d) for _, fim in _verbos_de_entrar(toks))


def _objeto_e_pessoa(toks: list[str], j: int) -> bool:
    """O objeto da navegação que começa em `j` (depois do lugar e do artigo) é pessoa ou conversa (`_OBJETO_PESSOA`)?"""
    x = _pula(toks, j, _LUGAR | _ARTIGOS | _ADVERBIOS)
    return x < len(toks) and toks[x] in _OBJETO_PESSOA


def _login_valor(toks: list[str], d: _Destinos = _SEM_DESTINOS) -> bool:
    """O verbo de entrar ligado a um valor: "entre com girassol", "faça login usando x", "entra no insta com x" (até
    oito tokens entre o verbo e o conector), "pra entrar: girassol", "entre - girassol", "entre no insta: girassol" e
    "use girassol pra entrar". O artigo depois do conector não isenta ("com a girassol"); o destino sim ("com a conta
    Lucas" quando o extrator a tirou, "com o Google"): F-B.

    Rodada H (H-1 a): o conector liga o valor com ou sem objeto de navegação, salvo depois de pessoa ou conversa ("entra
    AQUI com girassol", "entre no FEED com girassol", "entre no PERFIL com girassol" recusam; "entre na conversa com
    qa-001" passa); também no passado ("entrei com girassol", "loguei com x"), com a posposição turca ("girassol ile giriş
    yap") e com o sufixo húngaro ("lépj be girassol-lal"). O nome do catálogo sozinho depois do conector é destino (H-3),
    mas não depois de outro destino ("acesse como lucas com girassol", G-4). A palavra de conta com um nome que o catálogo
    não conhece é valor ("entre no feed com a conta girassol"); o objeto de navegação não ("usando o navegador")."""
    n = len(toks)
    for ini, fim in _verbos_de_entrar(toks, passado=True):
        j = _pula(toks, fim + 1, _ADVERBIOS)
        if j < n and toks[j] in _SEPARADORES_DE_VALOR:
            k = _pula(toks, j + 1, _ARTIGOS | _ADVERBIOS)
            if k < n and k not in d.cortados and _e_valor(toks[k]):
                return True
        if (ini >= 3 and toks[ini - 1] in ("pra", "para", "to") and _e_valor(toks[ini - 2])
                and toks[ini - 3] in _ANTES_DE_PRA_ENTRAR):
            return True
        if ini >= 2 and toks[ini - 1] in _POSPOSICOES and ini - 2 not in d.cortados and _e_valor(toks[ini - 2]):
            return True
        if j < n and _SUFIXO_INSTRUMENTAL.fullmatch(toks[j]) and _e_valor(toks[j].split("-", 1)[0]):
            return True
        pessoa = _objeto_e_pessoa(toks, j)
        destino_dito = False
        for m in range(fim + 1, min(fim + 9, n)):
            if (toks[m] in _SEPARADORES_DE_VALOR and m > fim + 1 and toks[m - 1] in _ONDE_SE_ENTRA):
                k = _pula(toks, m + 1, _ARTIGOS | _ADVERBIOS)
                if k < n and k not in d.cortados and _e_valor(toks[k]):
                    return True
            if toks[m] in _PARA_A_BUSCA:
                break
            if toks[m] in _CONECTORES and (not pessoa or any(t in _ONDE_SE_ENTRA for t in toks[fim + 1:m])):
                k = _pula(toks, m + 1, _ARTIGOS | _SEPARADORES_DE_VALOR)          # "com: girassol" vale como "com girassol"
                if k >= n:
                    continue
                if _destino(toks, k, d, catalogo=not destino_dito):
                    destino_dito = True
                elif toks[k] in _DESTINO_PALAVRA or (_e_valor(toks[k]) and toks[k] not in _OBJETO_DE_NAVEGACAO):
                    return True
    return False


def _depois_do_email(toks: list[str], y: int) -> int:
    """Depois do usuário, o resto do e-mail ("lucas.almeida9484 @ outlook.com"): o "@" e o domínio não quebram o par (G-1)."""
    if y + 1 < len(toks) and toks[y] == "@" and toks[y + 1][:1].isalnum():
        return y + 2
    return y


def _fim_do_usuario(toks: list[str], k: int, d: _Destinos) -> int | None:
    """O usuário que começa em `k`, logo depois do conector do verbo de entrar (G-1): "@handle", "local@domínio" ou nome do
    catálogo. Devolve onde ele termina, ou `None`."""
    n = len(toks)
    if k >= n:
        return None
    if toks[k] == "@":
        return k + 2 if k + 1 < n and toks[k + 1][:1].isalnum() else None
    if k + 1 < n and toks[k + 1] == "@" and toks[k][:1].isalnum():
        return _depois_do_email(toks, k + 1)
    return _depois_do_email(toks, d.fim(k)) if d.citado(k) else None


def _par_depois(toks: list[str], y0: int, d: _Destinos, *, com_entrar: bool, forte: bool) -> bool:
    """Depois do primeiro valor do par (em `y0`): separador e segundo valor ("lucas E girassol", "lucas / girassol"); ou,
    com `forte`, o segundo valor colado no fim da oração ("conta André girassol"). O segundo é a posição de VALOR: o nome do
    catálogo não a isenta (G-4), só o trecho que o extrator tirou ("e pela Bruno")."""
    n = len(toks)
    if y0 >= n:
        return False
    if toks[y0] in _SEPARADORES_DO_PAR:
        y = _pula(toks, y0 + 1, _ARTIGOS | _ADVERBIOS | _DETERMINANTES | {":", "="})
        if (y < n and y not in d.cortados and _e_valor(toks[y])
                and not (toks[y0 - 1].isdigit() and toks[y].isdigit())):
            return toks[y0] == "/" or com_entrar
        return False
    return (forte and y0 not in d.cortados and _e_valor(toks[y0])
            and (y0 + 1 >= n or toks[y0 + 1] in _FIM_DE_ORACAO or toks[y0 + 1] == ","))


def _colado(toks: list[str], u: int, d: _Destinos) -> bool:
    """Depois do usuário dito pelo conector (nome do catálogo, @handle, e-mail), um valor colado: "entre com o lucas
    girassol" (rodada H; o nome do catálogo virou destino na H-3, e o que vem colado a ele é a posição de valor, G-4)."""
    return (u < len(toks) and u not in d.cortados and _e_valor(toks[u]) and toks[u] not in _LUGAR
            and toks[u] not in _CONECTORES and toks[u] not in _ARTIGOS and toks[u] not in _OBJETO_DE_NAVEGACAO
            and toks[u] not in _ENTRAR)


def _par_credencial(toks: list[str], d: _Destinos = _SEM_DESTINOS) -> bool:
    """Usuário e senha juntos (F-C):
    - a barra depois do verbo de entrar (até cinco tokens: "entre com a conta Lucas / girassol");
    - o usuário como @handle ou e-mail logo depois do conector, seguido de valor ("entre com @zilda.prado e girassol",
      "acesse com lucas@outlook.com: x"; rodada G, G-1);
    - o campo de usuário (usuário, conta, persona, nome, perfil, login, user; "como @") com dois valores: com barra
      sempre, com "e", vírgula, hífen ou dois-pontos só com verbo de entrar na frase (ou, com vírgula, depois de "usuário",
      "user" ou "login": G-2), e colados no fim da oração com o campo forte ("conta André girassol");
    - o destino que o extrator tirou ou o nome do catálogo, seguido de um valor ("entre pela Lucas e girassol");
    - no começo da oração: "lucas, girassol, entra", "lucas / girassol." e "girassol, entra com a conta Lucas".
    O verbo de ação depois do destino não é valor: "com a conta Lucas e curta a foto" passa.

    Rodada H: o valor colado ao usuário do conector ("entre com o lucas girassol"), o verbo no passado nos pares do conector
    ("entrei com o lucas e girassol") e, com o campo de login (usuário, user, login, usr), o par com qualquer separador que
    não é palavra mesmo sem verbo de entrar ("usuario lucas; a outra: girassol", "user lucas | girassol": H-1 b)."""
    n = len(toks)
    verbos = _verbos_de_entrar(toks)
    com_entrar = bool(verbos)
    for _, fim in _verbos_de_entrar(toks, passado=True):
        for j in range(fim + 1, min(fim + 6, n - 1)):
            if toks[j] in _FIM_DE_ORACAO:
                break
            if (toks[j] == "/" and _TOKEN.fullmatch(toks[j + 1]) and toks[j + 1][0].isalnum()
                    and not (toks[j - 1].isdigit() and toks[j + 1].isdigit())):    # "entre 10/05 e 12/05" é data
                return True
        for m in range(fim + 1, min(fim + 9, n)):
            if toks[m] in _PARA_A_BUSCA:
                break
            if toks[m] in _CONECTORES:
                u = _fim_do_usuario(toks, _pula(toks, m + 1, _ARTIGOS), d)
                if u is not None and (_par_depois(toks, u, d, com_entrar=True, forte=False) or _colado(toks, u, d)):
                    return True
                break
    fins = {d.fim(i) - 1 for i in (*d.cortados, *d.catalogo)}
    for k, tok in enumerate(toks):
        if tok in _CAMPO_DE_USUARIO or (tok == "como" and k + 1 < n and toks[k + 1] == "@"):
            x = _pula(toks, k + 1, (":", "=", "@", "do", "da", "de", "dos", "das", "o", "a"))
            # o primeiro do par também é valor: "qual conta ESTÁ conectada" não é par (1 dos 98 comandos reais, 03/10)
            if x < n and _e_valor(toks[x]):
                y0 = _depois_do_email(toks, d.fim(x))          # o nome de duas palavras é UMA menção ("Lucas Almeida")
                sem_verbo = tok in _CAMPO_DE_LOGIN and y0 < n and toks[y0] in _SEPARADORES_NAO_ALFABETICOS
                if _par_depois(toks, y0, d, com_entrar=com_entrar or sem_verbo, forte=tok in _CAMPO_FORTE):
                    return True
        if k in fins and _par_depois(toks, _depois_do_email(toks, k + 1), d, com_entrar=com_entrar, forte=False):
            return True
        inicio = k == 0 or toks[k - 1] in _FIM_DE_ORACAO
        if not (inicio and _e_valor(tok) and k + 2 < n) or k in d.cortados or k + 2 in d.cortados:
            continue
        if (k + 4 < n and toks[k + 1] == "," and _e_valor(toks[k + 2]) and toks[k + 3] == ","
                and any(a == k + 4 for a, _ in verbos)):
            fim = next(f for a, f in verbos if a == k + 4)
            if not _navega(toks, fim, d, por_conector=False):
                return True
        if (toks[k + 1] == "/" and _e_valor(toks[k + 2]) and not (tok.isdigit() and toks[k + 2].isdigit())
                and (k + 3 >= n or toks[k + 3] in _FIM_DE_ORACAO or toks[k + 3] == ",")):
            return True
        if toks[k + 1] == ",":
            for a, f in verbos:
                if a == k + 2 and not _navega(toks, f, d, por_conector=False):
                    return True
    return False


#: O código pedido pela quantidade de dígitos ("os seis dígitos", "aquela de quatro dígitos", "the six digits", "los seis
#: números") ou o verbo de destravar: código de verificação ou PIN sem a palavra-chave (decisão (a)).
_DIGITOS_C7: Final = re.compile(
    r"\b(?:\d|tres|quatro|cinco|seis|sete|oito|three|four|five|six|seven|eight|cuatro|siete|ocho)\s+"
    r"(?:digitos|digits|numeros|numbers|numerinhos)\b|\b(?:destravar|desbloquear|unlock)\b")
#: Um algarismo, escrito ou por extenso, para o PIN tecla a tecla.
_ALGARISMO: Final = (r"(?:\d|zero|um|dois|tres|quatro|cinco|seis|sete|oito|nove|one|two|three|four|five|six|seven|eight"
                     r"|nine|uno|dos|cuatro|siete|ocho|nueve)")
#: O PIN digitado tecla a tecla ("toque 4, depois 8, depois 2, depois 1": três ou mais algarismos depois do verbo de
#: tocar) ou fechado por "#" ("abre com 2580#"; a hashtag "#2024" passa). Rodada C do 31.9: saíam como `[numero]`.
_PIN_C7: Final = re.compile(
    rf"\b(?:toque|toca|aperte|aperta|pressione|pressiona|digite|digita|tecle|tap|press|type|pulsa|presiona|teclea)\s+"
    rf"(?:(?:no|na|o|a|em|on|the|el|en)\s+)?{_ALGARISMO}\b"
    rf"(?:\s*[,;]?\s*(?:(?:e|depois|entao|then|and|y|luego|despues)\s+)*"
    rf"(?:(?:toque|toca|aperte|tap|press|pulsa|no|na|o|on|the|el)\s+)*{_ALGARISMO}\b){{2,}}"
    r"|(?<![\w#])\d{3,8}\s?#(?![\w#])")
#: Prefixo de token de acesso, de qualquer tamanho (o `looks_secret` só pega o longo): GitHub, Slack, chave de API, JWT.
_PREFIXO_DE_TOKEN: Final = re.compile(
    r"(?<![A-Za-z0-9])(?:gh[pousr]_|github_pat_|sk-|sk_live_|pk_live_|xox[abprs]-|AKIA|ASIA|eyJ)[A-Za-z0-9]")
#: As mesmas formas sem depender da caixa (o texto que chega em minúsculas não perde a chave: rodada C do 31.9), com o
#: comprimento de verdade onde o prefixo é palavra ("asia" + 16, nunca "asiático"; "eyj" + 10).
_TOKEN_SEM_CAIXA: Final = re.compile(
    r"(?i)(?<![a-z0-9])(?:(?:akia|asia)[a-z0-9]{16}(?![a-z0-9])|eyj[a-z0-9_\-]{10,}"
    r"|(?:gh[pousr]_|github_pat_|sk_live_|pk_live_|xox[abprs]-)[a-z0-9])")
#: Controle de direção do texto (override e isolate): o jeito de escrever "senha" ao contrário na tela.
_BIDI: Final = re.compile("[‪-‮⁦-⁩]")

_INSTRUCOES_CATALOGO: Final = (
    "The state is a command written by the owner of a device fleet, in Portuguese, with names and numbers masked. Pick the "
    "catalog entry that this command asks to run, or none if no entry clearly fits.")
_INSTRUCOES_DESEMPATE: Final = (
    "The state is a command written by the owner of a device fleet, in Portuguese, with names and numbers masked. Several "
    "catalog entries match it equally. Pick the one the command asks for, or none if it cannot be told.")


@dataclass(frozen=True)
class EntradaDeCatalogo:
    """Uma habilidade publicada ou fluxo ativo. `descricao` é texto do catálogo do dono (C2)."""

    skill_id: str
    nome: str
    descricao: str = ""


@dataclass(frozen=True)
class CadeiaObservada:
    """O que a cadeia REAL fez com o comando, em ids de habilidade (sem versão).

    `resolvida`: a habilidade escolhida, ou a única de que a cadeia fala quando falta parâmetro; `sem_casamento`: nada casou
    (o planejador fica com o comando); `empatados`: as habilidades entre as quais nada decidiu, ou que a cadeia desempatou;
    `ambiguos`: quantas etapas da RESOLVE terminaram em AMBIGUOUS (RA-2: o relatório do 31.10 conta por execução)."""

    resolvida: str | None = None
    sem_casamento: bool = False
    empatados: tuple[str, ...] = ()
    ambiguos: int = 0


def id_opaco(skill_id: str) -> str:
    """Id opaco e estável da opção: o nome da habilidade nunca vai no id, e o id cabe no formato que a sombra aceita."""
    return "opt:" + hashlib.sha1(skill_id.encode("utf-8")).hexdigest()[:12]


def _descricao(e: EntradaDeCatalogo) -> str:
    # C2 é o catálogo do dono, liberado, mas o nome de fluxo legado é o resumo de um comando antigo, com o destino dentro
    # (reverificação de 03/10): `mascarar_catalogo` ANTES do corte, para o corte não deixar meia aspa nem meio handle. O
    # `redact` é a rede para um segredo que tenha ido parar numa descrição.
    texto = " ".join(f"{e.nome}: {e.descricao}".split() if e.descricao else e.nome.split())
    return mascarar_catalogo(redact(texto) or "")[:_DESCRICAO_MAX] or "(sem nome)"


def _opcoes(entradas: Sequence[EntradaDeCatalogo]) -> dict[str, str]:
    return {id_opaco(e.skill_id): _descricao(e) for e in sorted(entradas, key=lambda e: e.skill_id)}


def menciona_c7(comando: str) -> bool:
    """O comando fala de credencial, código, 2FA ou desafio (C7), em qualquer formato, ou tem cara de segredo."""
    return motivo_c7(comando) is not None


def motivo_c7(comando: str, *, sem_destinos: str | None = None, intencao: bool = True,
              destinos: frozenset[str] = frozenset()) -> MotivoC7 | None:
    """Por que o comando é C7, ou `None`. Confere o texto como veio e normalizado (NFKC, sem caractere invisível):

    - `c7_bidi`: controle de direção no texto CRU (a normalização o apaga, e com ele "ahnes" se lê "senha" na tela);
    - `c7_palavra`: a palavra-chave, inclusive separada ("s/e/n/h/a"), colada em outra ("novasenha"), abreviada ("pw") e
      em outro idioma ou escrita (`mentions_credential`, `_ASSUNTO_C7`, `_C7_DENTRO`);
    - `c7_formato`: cara de segredo (`looks_secret`: "código 123456", chave de API, base64 longo) ou prefixo de token de
      acesso de qualquer tamanho (`ghp_`, `sk-`, `AKIA`, `eyJ`), também em minúsculas (`_TOKEN_SEM_CAIXA`);
    - `c7_alfabetos`: letra fora do alfabeto latino em qualquer palavra (`escrita_nao_latina`: "пароль", o homóglifo "senhа"
      com "а" cirílico);
    - `c7_ofuscado`: a palavra-chave em leet ("s3nh4", "pa$$word", "pa55word") ou invertida ("drowssap"), a palavra
      soletrada e, desde a rodada H, a corrida curta de letras depois de verbo de digitar ("digite g+i", "g · i");
    - `c7_eufemismo`: "a de sempre", "o que você digita", "segundo campo", pergunta de segurança, frase de recuperação, o
      par de usuário e senha ("login: x / y", "usuário x, acesso y"); desde a rodada H, os que dependem do verbo "é" ("a de
      costume é", "o nome do meu cachorro é", "a cidade onde nasci é", `_EUFEMISMO_EH`);
    - `c7_digitos`: o código pedido pela quantidade de dígitos ("os seis dígitos"), "destravar" ou o PIN tecla a tecla
      ("toque 4, depois 8, depois 2", "2580#");
    - `c7_login_valor` (rodada E): o verbo de entrar ligado a um valor, sem a palavra-chave ("entre com girassol", "pra
      entrar: girassol", "entre / girassol");
    - `c7_par_credencial` (rodada E): usuário e senha juntos ("entre com a conta Lucas / girassol", "usuário lucas e
      girassol, entra", "lucas, girassol, entra");
    - `c7_intencao_de_entrar` (rodada F, F-A): o verbo de entrar SEM objeto de navegação ("entra e curte", "entre com a
      girassol"), sem precisar achar o valor.

    `sem_destinos`: o texto é o ORIGINAL e este é o mesmo comando sem os destinos (`RunService.sem_destinos`). O que o
    extrator tirou é destino do catálogo real: "com a conta Lucas" vira destino, "com a conta girassol" não (F-B).
    `intencao=False` desliga a F-A: no comando SEM destinos, "entre com a conta Lucas e curta" vira "entre e curta", e a
    F-A só vale no original, que mostra o destino. `destinos` (`nomes_de_destino`): os nomes INTEIROS do catálogo real;
    desde a rodada G (G-4) só são destino depois da palavra de conta ou do "@", nunca na posição de valor (`_Destinos`).
    """
    if _BIDI.search(comando):
        return "c7_bidi"
    normal = normalizar(comando)
    plano = " ".join(sem_acento(normal).split())
    if (mentions_credential(comando) or mentions_credential(normal) or _ASSUNTO_C7.search(plano)
            or _C7_DENTRO.search(plano)):
        return "c7_palavra"
    if (looks_secret(comando) or looks_secret(normal) or _PREFIXO_DE_TOKEN.search(normal)
            or _TOKEN_SEM_CAIXA.search(normal)):
        return "c7_formato"
    if escrita_nao_latina(normal):
        return "c7_alfabetos"
    leet = sem_leet(plano)
    if (_ASSUNTO_C7.search(leet) or _C7_DENTRO.search(leet)
            or any(m.group() in _C7_INVERTIDAS for m in _LETRAS.finditer(plano)) or _SOLETRADO.search(plano)
            or _SOLETRADO_POR_NOME.search(plano)):
        return "c7_ofuscado"
    toks = _tokens_de(normal)
    if _letras_depois_de_digitar(toks):
        return "c7_ofuscado"
    d = _Destinos(_cortados(toks, sem_destinos) if sem_destinos is not None else {}, _no_catalogo(toks, destinos))
    if (_EUFEMISMO_C7.search(plano) or _EUFEMISMO_EH.search(" ".join(toks))
            or (_EUFEMISMO_COM_ENTRAR.search(plano) and _entrar_sem_navegacao(toks, d))):
        return "c7_eufemismo"
    if _DIGITOS_C7.search(plano) or _PIN_C7.search(plano):
        return "c7_digitos"
    if _par_credencial(toks, d):
        return "c7_par_credencial"
    if _login_valor(toks, d):
        return "c7_login_valor"
    if intencao and _entrar_sem_navegacao(toks, d):
        return "c7_intencao_de_entrar"
    return None


class ConsumidorDeIntencao:
    def __init__(self, porta: Porta, repositorio: RepositorioDeSombra) -> None:
        self._porta = porta
        self._repositorio = repositorio
        self._avisou_teto = False

    def ativo(self) -> bool:
        """Falso, e então NADA é feito (nem RESOLVE, nem leitura do banco), quando: a porta não está em `shadow` para a
        intenção (padrão: `enabled=false`), o envio não está aprovado no código (`JEV_RUNTIME_SEND_APPROVED`, lido na hora)
        ou a C3 não está entre as classes efetivas (teto do código ∩ `classes_permitidas`). Desligado custa zero."""
        if not privacidade.JEV_RUNTIME_SEND_APPROVED:
            return False
        cfg = self._porta.cfg
        if modo_efetivo("shadow", cfg, "intencao") != "shadow":
            return False
        classes = privacidade.JEV_ALLOWED_CLASSES
        if cfg is not None and cfg.classes_permitidas is not None:
            classes = classes & frozenset(cfg.classes_permitidas)
        return "C3" in classes

    def pedido(self, *, run_id: str, comando: str, app: str | None, catalogo: Sequence[EntradaDeCatalogo],
               cadeia: CadeiaObservada, original: str | None = None,
               destinos: Iterable[str] = ()) -> PedidoDeDecisao | None:
        """O pedido de sombra, ou `None` se não há pergunta a fazer.

        `original` (rodada E do 31.9): o comando COM os destinos. A C7 é conferida nele também, porque o `sem_destinos`
        pode partir o par ("entre com a conta Lucas e girassol" vira "entre e girassol"). Desde a rodada F, com a forma de
        conector inteira e sabendo o que o extrator tirou (F-B); a intenção de entrar (F-A) só é conferida nele. Nada
        dele sai: o estado é montado só de `comando`. `destinos` (rodada F): os nomes do catálogo de destinos real
        (`RunService.dados_da_sombra`); nada deles sai.

        Sanitiza o comando (C3) pelo filtro sensato (`remover_entidades`). C7 no texto (senha, código, 2FA, captcha, em
        prosa ou não) marca `credencial`: a porta recusa o pedido INTEIRO e grava `privacidade`. O que esconde e-mail,
        telefone ou documento = estado vazio, que a porta também recusa."""
        perguntas: list[Pergunta] = []
        if 0 < len(catalogo) <= MAX_CATALOGO:
            perguntas.append(pergunta_choice(PERGUNTA_CATALOGO, _INSTRUCOES_CATALOGO, _opcoes(catalogo)))
        elif catalogo and not self._avisou_teto:
            self._avisou_teto = True             # uma vez por processo: a R2 some da medição enquanto o catálogo não cabe
            log.warning("decisao_fechada: catálogo de %d entradas acima do teto (%d); a R2 da intenção não vai",
                        len(catalogo), MAX_CATALOGO)
        por_id = {e.skill_id: e for e in catalogo}
        empatados = [por_id[s] for s in dict.fromkeys(cadeia.empatados) if s in por_id]
        if len(empatados) >= 2:
            perguntas.append(pergunta_choice(PERGUNTA_DESEMPATE, _INSTRUCOES_DESEMPATE, _opcoes(empatados)))
        if not perguntas:
            return None
        nomes = nomes_de_destino(destinos)
        com_original = bool(original) and original != comando
        motivo = motivo_c7(comando, intencao=not com_original, destinos=nomes)
        if motivo is None and com_original:
            motivo = motivo_c7(str(original), sem_destinos=comando, destinos=nomes)
        if motivo is not None:
            return PedidoDeDecisao(origem="intencao", classe="C3", estado={}, perguntas=tuple(perguntas), modo="shadow",
                                   run_id=run_id, ref=run_id, marcadores=frozenset({"credencial"}),
                                   motivo_privacidade=motivo)
        limpo, motivo_filtro = (remover_entidades_com_motivo(redact(comando) or "") if comando.strip()
                                else (None, "vazio"))
        estado: dict[str, str] = {}
        if limpo:                                    # `None` (forma do piso) ou vazio: estado vazio, a porta recusa
            estado["comando"] = limpo
            if app and _APP.fullmatch(app):
                estado["app"] = app
        return PedidoDeDecisao(origem="intencao", classe="C3", estado=estado, perguntas=tuple(perguntas), modo="shadow",
                               run_id=run_id, ref=run_id, motivo_privacidade=None if limpo else motivo_filtro)

    def observar(self, *, run_id: str, comando: str, app: str | None, catalogo: Sequence[EntradaDeCatalogo],
                 cadeia: CadeiaObservada, original: str | None = None, destinos: Iterable[str] = ()) -> None:
        """Bloqueante (roda numa thread, nunca no laço): consulta a porta em shadow e casa a decisão real.

        O casamento vai como `ao_registrar`: a porta o chama na MESMA thread, logo depois de gravar a linha (também na
        recusa por privacidade). Sem polling, sem espera fixa. Qualquer falha é engolida: medir nunca derruba o trabalho."""
        try:
            if not self.ativo():
                return
            pedido = self.pedido(run_id=run_id, comando=comando, app=app, catalogo=catalogo, cadeia=cadeia,
                                 original=original, destinos=destinos)
            if pedido is None:
                return
            reais = self.decisoes_reais(cadeia, {p.id for p in pedido.perguntas})

            def ao_registrar() -> None:
                if reais:
                    self._repositorio.casar_decisao_real(reais, ref=run_id)
                self._repositorio.anotar_ambiguos(cadeia.ambiguos, ref=run_id)

            self._porta.consultar(pedido, ao_registrar=ao_registrar)
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: falha na sombra da intenção")

    @staticmethod
    def decisoes_reais(cadeia: CadeiaObservada, perguntas: set[str]) -> dict[str, str]:
        """{pergunta: id opaco do que a cadeia real resolveu}. Pergunta sem decisão real conhecida fica de fora.

        Só a R2 tem decisão real aqui. A R3 (desempate) NÃO: a cadeia que termina em empate não escolhe nenhum candidato
        (`AMBIGUOUS` volta para a pessoa), e a que desempata não devolve os candidatos. O rótulo da R3 é a escolha da
        pessoa ou o desfecho, casados no 31.10 (`docs/design/jev-golden-set.md` §3)."""
        reais: dict[str, str] = {}
        if PERGUNTA_CATALOGO in perguntas:
            if cadeia.resolvida is not None:
                reais[PERGUNTA_CATALOGO] = id_opaco(cadeia.resolvida)
            elif cadeia.sem_casamento:
                reais[PERGUNTA_CATALOGO] = ID_NENHUMA
        return reais


__all__ = ["CadeiaObservada", "ConsumidorDeIntencao", "EntradaDeCatalogo", "MAX_CATALOGO", "PERGUNTA_CATALOGO",
           "PERGUNTA_DESEMPATE", "id_opaco", "nomes_de_destino"]
