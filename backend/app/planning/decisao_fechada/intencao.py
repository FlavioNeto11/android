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

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Literal

from ...security.redaction import looks_secret, mentions_credential, redact
from . import privacidade
from .contrato import ID_NENHUMA, MAX_OPCOES, PedidoDeDecisao, Pergunta, pergunta_choice
from .entidades import (
    escrita_nao_latina, mascarar_catalogo, normalizar, remover_entidades_com_motivo, sem_acento, sem_leet,
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
                   "c7_login_valor", "c7_par_credencial"]
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
    "pswd", "pword", "passw", "secreta", "credencial", "credenciais", "credential", "credenciales")
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
    "contrasenya", "slaptazodis", "adgangskode", "salasana", "paswoord", "wagwoord", "pasahitza", "contrasinal",
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
    # inglês
    r"\bthe one i always use\b", r"\bwhat you type to (?:get in|log in|sign in)\b", r"\bthe access one\b",
    r"\bthe thing only you and i know\b", r"\bsecond field\b", r"\brecovery (?:phrase|codes?)\b", r"\bseed(?: phrase)?\b",
    r"\bsecurity question\b", r"\bmaiden name\b", r"\barrived by (?:text|sms)\b",
    r"\b(?:a|essa|minha|sua|the|la) (?:combinacao|combination|combinacion) (?:e|eh|is|es|:|=) \w",
    r"\b(?:magic|secret) word\b", r"\bbelow the (?:username|user|login|email)\b",
    r"\bthe (?:numbers|digits|code) (?:that|which) (?:came|arrived)\b",
    # espanhol
    r"\blo de siempre\b", r"\blo que tecleas\b", r"\bcampo de abajo\b", r"\bfrase de recuperacion\b",
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

# ------------------------------------------------------------------ regra ESTRUTURAL de intenção de entrar (rodada E, E-A(3))
# Independe da lista: o verbo de entrar e um valor ligado a ele é a credencial, com ou sem a palavra. Roda sobre os tokens do
# texto sem acento (palavra, número ou um sinal de pontuação por token).
_TOKEN: Final = re.compile(r"[^\W_]+(?:['\-][^\W_]+)*|[^\w\s]")
_ENTRAR: Final[frozenset[str]] = frozenset((
    "entrar", "entre", "entra", "entrem", "entrando", "entro", "logar", "loga", "logue", "logando", "logue-se", "login",
    "logon", "signin", "acessar", "acesse", "acessa", "acessem", "acessando", "autenticar", "autentique", "autentica",
    "anmelden", "einloggen", "connecter", "connectez", "connecte", "accedi", "accedere", "inloggen", "conectar",
    "conecte", "conecta", "ingresar", "ingresa", "ingrese"))
#: O verbo de entrar de duas palavras ("log in", "sign in", "inicia sessão").
_ENTRAR_2: Final[frozenset[tuple[str, str]]] = frozenset((
    ("log", "in"), ("log", "on"), ("sign", "in"), ("inicia", "sessao"), ("iniciar", "sessao"), ("inicie", "sessao"),
    ("inicia", "sesion"), ("iniciar", "sesion"), ("inicie", "sesion")))
#: Logo depois do verbo, o que faz dele navegação ("entre NO perfil", "acesse O app") e não a entrada com um valor.
_NAVEGACAO: Final[frozenset[str]] = frozenset((
    "no", "na", "nos", "nas", "em", "num", "numa", "ao", "aos", "pelo", "pela", "pelos", "pelas", "a", "o", "os", "as",
    "in", "into", "on", "to", "the", "en", "el", "la", "al", "los", "las", "nel", "nella", "sul", "dans", "le", "im", "auf",
    "seu", "sua", "meu", "minha", "perfil", "conta", "app", "aplicativo", "insta", "instagram", "feed", "site", "pagina"))
#: Depois do objeto de navegação, o conector só liga um valor se o objeto é onde se entra com credencial ("entra no insta
#: com girassol"). Na conversa, no chat ou no perfil, o "com" é a pessoa ("entre na conversa com qa-001": 12 dos 92
#: comandos reais de 7 dias, medidos em 03/10).
_ONDE_SE_ENTRA: Final[frozenset[str]] = frozenset((
    "insta", "instagram", "app", "aplicativo", "conta", "site", "sistema", "painel", "portal", "outlook", "gmail",
    "email", "e-mail", "facebook", "tiktok", "whatsapp", "twitter", "banco", "account", "cuenta"))
#: Quem liga o verbo ao valor ("entre COM girassol", "login USANDO x").
_CONECTORES: Final[frozenset[str]] = frozenset((
    "com", "usando", "use", "usa", "utilizando", "with", "using", "con", "mit", "avec"))
#: O que, depois do conector, NÃO é valor: artigo, pronome, conjunção, o objeto de navegação, o provedor de entrada
#: ("entre com o Google") e o modo ("com calma"). Tudo o mais conta: na dúvida, C7 é recusa.
_NAO_VALOR: Final[frozenset[str]] = frozenset((
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "e", "ou", "de", "do", "da", "dos", "das", "que", "pra", "para",
    "the", "an", "my", "your", "and", "or", "el", "la", "los", "las", "un", "una", "y", "mi", "tu", "su", "le", "les",
    "meu", "minha", "seu", "sua", "nosso", "nossa", "ele", "ela", "eles", "elas", "voce", "vc", "mim", "isso", "isto",
    "esse", "essa", "este", "esta", "aquele", "aquela", "dele", "dela", "it", "this", "that", "me", "him", "her",
    "conta", "contas", "perfil", "perfis", "persona", "personas", "usuario", "user", "app", "aplicativo", "insta",
    "instagram", "aparelho", "celular", "telefone", "google", "facebook", "apple", "microsoft", "outlook", "gmail",
    "email", "e-mail", "sms", "biometria", "digital", "face", "rosto", "calma", "cuidado", "carinho", "pressa", "atencao",
    "jeito", "emoji", "emojis", "foto", "fotos", "video", "imagem", "texto", "legenda", "comentario", "mensagem", "link",
    "voz", "audio", "account", "profile", "phone", "cuenta", "todos", "todas", "tudo"))
#: Fim de oração: o valor e o verbo não se ligam através dele.
_FIM_DE_ORACAO: Final[frozenset[str]] = frozenset((".", ";", "!", "?", "\n"))
#: Onde a busca do conector para: fim de oração, vírgula e conjunção ("entre e comente COM parabéns" é outro verbo).
_PARA_A_BUSCA: Final[frozenset[str]] = _FIM_DE_ORACAO | frozenset((",", "e", "and", "y", "ou", "or", "depois", "entao",
                                                                   "then"))
#: O campo de usuário ("usuário lucas e girassol"): o par de valores depois dele é usuário e senha.
_CAMPO_DE_USUARIO: Final[frozenset[str]] = frozenset(("usuario", "usuaria", "user", "username", "login"))
_SEPARADORES_DO_PAR: Final[frozenset[str]] = frozenset(("e", ",", "/", ";", "and", "y"))
_ANTES_DE_PRA_ENTRAR: Final[frozenset[str]] = frozenset((
    "use", "usa", "usar", "digite", "digita", "coloque", "coloca", "bota", "poe", "ponha", "insira", "informe", "type",
    "enter"))


def _tokens(plano: str) -> list[str]:
    return _TOKEN.findall(plano)


def _verbos_de_entrar(toks: list[str]) -> list[tuple[int, int]]:
    """(início, fim) de cada verbo de entrar, de uma ou duas palavras."""
    achados: list[tuple[int, int]] = []
    for i, tok in enumerate(toks):
        if tok in _ENTRAR:
            achados.append((i, i))
        elif i + 1 < len(toks) and (tok, toks[i + 1]) in _ENTRAR_2:
            achados.append((i, i + 1))
    return achados


def _e_valor(tok: str) -> bool:
    return bool(_TOKEN.fullmatch(tok)) and tok[0].isalnum() and tok not in _NAO_VALOR


def _entrar_sem_navegacao(toks: list[str]) -> bool:
    """Há verbo de entrar sem objeto de navegação logo depois ("entra e curte", "girassol, entra")?"""
    return any(fim + 1 >= len(toks) or toks[fim + 1] not in _NAVEGACAO for _, fim in _verbos_de_entrar(toks))


def _login_valor(toks: list[str], *, valor_apos_conector: bool = True) -> bool:
    """O verbo de entrar ligado a um valor: "entre com girassol", "faça login usando x", "entra no insta com x" (até três
    tokens entre o verbo e o conector), "pra entrar: girassol", "entre / girassol" e "use girassol pra entrar".

    `valor_apos_conector=False` deixa de fora a forma com conector: no texto ORIGINAL, o que vem depois de "com" pode ser
    o destino ("entre com a conta do Lucas") que o `sem_destinos` tira; a forma é conferida no texto sem destinos."""
    n = len(toks)
    for ini, fim in _verbos_de_entrar(toks):
        navega = fim + 1 < n and toks[fim + 1] in _NAVEGACAO
        if fim + 2 < n and toks[fim + 1] in (":", "/", "=") and _e_valor(toks[fim + 2]):
            return True
        if (ini >= 3 and toks[ini - 1] in ("pra", "para", "to") and _e_valor(toks[ini - 2])
                and toks[ini - 3] in _ANTES_DE_PRA_ENTRAR):
            return True
        if not valor_apos_conector:
            continue
        for j in range(fim + 1, min(fim + 5, n - 1)):
            if toks[j] in _PARA_A_BUSCA:
                break
            if (toks[j] in _CONECTORES and _e_valor(toks[j + 1])
                    and (not navega or any(t in _ONDE_SE_ENTRA for t in toks[fim + 1:j]))):
                return True
    return False


def _par_credencial(toks: list[str]) -> bool:
    """Usuário e senha juntos: o par com barra depois do verbo de entrar (até quatro tokens antes da barra: "entre com a
    conta Lucas / girassol"), o campo de usuário com dois valores ("usuário admin / abc123"; com "e" ou vírgula, só com
    verbo de entrar na frase) e "lucas, girassol, entra" no começo da oração."""
    n = len(toks)
    for _, fim in _verbos_de_entrar(toks):
        for j in range(fim + 1, min(fim + 6, n - 1)):
            if toks[j] in _FIM_DE_ORACAO:
                break
            if (toks[j] == "/" and _TOKEN.fullmatch(toks[j + 1]) and toks[j + 1][0].isalnum()
                    and not (toks[j - 1].isdigit() and toks[j + 1].isdigit())):    # "entre 10/05 e 12/05" é data
                return True
    com_entrar = _entrar_sem_navegacao(toks)
    for k, tok in enumerate(toks):
        if tok in _CAMPO_DE_USUARIO or (tok == "conta" and "/" in toks[k:k + 5]):
            x = k + 2 if k + 1 < n and toks[k + 1] in (":", "=") else k + 1
            if x + 2 < n and _TOKEN.fullmatch(toks[x]) and toks[x + 1] in _SEPARADORES_DO_PAR and _e_valor(toks[x + 2]):
                if toks[x + 1] == "/" or (com_entrar and _e_valor(toks[x])):
                    return True
        inicio = k == 0 or toks[k - 1] in _FIM_DE_ORACAO
        if (inicio and k + 4 < n and _e_valor(tok) and toks[k + 1] == "," and _e_valor(toks[k + 2])
                and toks[k + 3] == "," and toks[k + 4] in _ENTRAR
                and (k + 5 >= n or toks[k + 5] not in _NAVEGACAO)):
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


def motivo_c7(comando: str, *, valor_apos_conector: bool = True) -> MotivoC7 | None:
    """Por que o comando é C7, ou `None`. Confere o texto como veio e normalizado (NFKC, sem caractere invisível):

    - `c7_bidi`: controle de direção no texto CRU (a normalização o apaga, e com ele "ahnes" se lê "senha" na tela);
    - `c7_palavra`: a palavra-chave, inclusive separada ("s/e/n/h/a"), colada em outra ("novasenha"), abreviada ("pw") e
      em outro idioma ou escrita (`mentions_credential`, `_ASSUNTO_C7`, `_C7_DENTRO`);
    - `c7_formato`: cara de segredo (`looks_secret`: "código 123456", chave de API, base64 longo) ou prefixo de token de
      acesso de qualquer tamanho (`ghp_`, `sk-`, `AKIA`, `eyJ`), também em minúsculas (`_TOKEN_SEM_CAIXA`);
    - `c7_alfabetos`: letra fora do alfabeto latino em qualquer palavra (`escrita_nao_latina`: "пароль", o homóglifo "senhа"
      com "а" cirílico);
    - `c7_ofuscado`: a palavra-chave em leet ("s3nh4", "pa$$word", "pa55word") ou invertida ("drowssap");
    - `c7_eufemismo`: "a de sempre", "o que você digita", "segundo campo", pergunta de segurança, frase de recuperação, o
      par de usuário e senha ("login: x / y", "usuário x, acesso y");
    - `c7_digitos`: o código pedido pela quantidade de dígitos ("os seis dígitos"), "destravar" ou o PIN tecla a tecla
      ("toque 4, depois 8, depois 2", "2580#");
    - `c7_login_valor` (rodada E): o verbo de entrar ligado a um valor, sem a palavra-chave ("entre com girassol", "pra
      entrar: girassol", "entre / girassol");
    - `c7_par_credencial` (rodada E): usuário e senha juntos ("entre com a conta Lucas / girassol", "usuário lucas e
      girassol, entra", "lucas, girassol, entra").

    `valor_apos_conector=False`: para o texto ORIGINAL, com os destinos (ver `_login_valor`).
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
            or any(m.group() in _C7_INVERTIDAS for m in _LETRAS.finditer(plano))):
        return "c7_ofuscado"
    toks = _tokens(plano)
    if _EUFEMISMO_C7.search(plano) or (_EUFEMISMO_COM_ENTRAR.search(plano) and _entrar_sem_navegacao(toks)):
        return "c7_eufemismo"
    if _DIGITOS_C7.search(plano) or _PIN_C7.search(plano):
        return "c7_digitos"
    if _par_credencial(toks):
        return "c7_par_credencial"
    if _login_valor(toks, valor_apos_conector=valor_apos_conector):
        return "c7_login_valor"
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
               cadeia: CadeiaObservada, original: str | None = None) -> PedidoDeDecisao | None:
        """O pedido de sombra, ou `None` se não há pergunta a fazer.

        `original` (rodada E do 31.9): o comando COM os destinos. A C7 é conferida nele também, porque o `sem_destinos`
        pode partir o par ("entre com a conta Lucas / girassol" vira "entre / girassol"). Nada dele sai: o estado é
        montado só de `comando`.

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
        motivo = motivo_c7(comando)
        if motivo is None and original and original != comando:
            motivo = motivo_c7(original, valor_apos_conector=False)
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
                 cadeia: CadeiaObservada, original: str | None = None) -> None:
        """Bloqueante (roda numa thread, nunca no laço): consulta a porta em shadow e casa a decisão real.

        O casamento vai como `ao_registrar`: a porta o chama na MESMA thread, logo depois de gravar a linha (também na
        recusa por privacidade). Sem polling, sem espera fixa. Qualquer falha é engolida: medir nunca derruba o trabalho."""
        try:
            if not self.ativo():
                return
            pedido = self.pedido(run_id=run_id, comando=comando, app=app, catalogo=catalogo, cadeia=cadeia,
                                 original=original)
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
           "PERGUNTA_DESEMPATE", "id_opaco"]
