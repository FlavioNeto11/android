"""Redação central de texto sensível.

Esta é uma defesa **secundária**, por desenho. A defesa principal é impedir que o segredo seja registrado na
origem: o canal de entrada sensível não passa pelo caminho normal de digitação, então não existe argumento de ação
para preencher, e o Appium sobe com mascaramento de toda digitação.

Aqui a redação é feita por FORMATO, nunca por uma lista de valores em texto claro: manter um registro dos segredos
ativos para comparar por substring criaria mais uma cópia do segredo em memória, exatamente o que se quer evitar.
"""
from __future__ import annotations

import re
from typing import Any

MASK = "**REDACTED**"

#: As palavras que, num par chave/valor, já entregam que o valor é segredo. Constante ÚNICA de propósito: até aqui
#: havia duas listas (`_PATTERNS` e `_SENSITIVE_KEY`) que divergiam em silêncio — `credential` existia numa e não
#: na outra, e por isso `credential=abc123` saía em claro no texto enquanto um campo chamado `credential` era
#: mascarado. Uma lista só é o que impede a próxima divergência.
#:
#: `access[_-]?key(?:[_-]?id)?` e `secret[_-]?access[_-]?key` não são redundância de `secret`: `\b` não existe
#: entre `_` e letra, então `secret` NÃO casa dentro de `AWS_SECRET_ACCESS_KEY=` (o `=` vem depois de `KEY`, não
#: de `SECRET`). Chave de nuvem é o formato que o E8 (S3) acrescenta ao projeto — entra agora, não depois.
#:
#: `pre[_-]?shared[_-]?key|psk` (ADR-056 §5, item 25.3): a chave pré-compartilhada do WireGuard. `PrivateKey = …`
#: já caía em `private[_-]?key`; `PresharedKey = …` e o `"pre_shared_key"` do sing-box saíam em claro, porque
#: nenhuma palavra da lista aparece neles (`key` sozinho não está, de propósito: `PublicKey` não é segredo).
_PALAVRAS_DE_SEGREDO = (
    r"password|passwd|senha|pin|secret|segredo|token|api[_-]?key|master[_-]?key|credential|credencial|"
    r"secret[_-]?access[_-]?key|access[_-]?key(?:[_-]?id)?|secret[_-]?key|private[_-]?key|"
    r"pre[_-]?shared[_-]?key|psk"
)

# Cada padrão captura o prefixo (grupo 1) e substitui o que vem depois. São formatos que carregam segredo por
# natureza; nenhum deles depende de conhecer o valor.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # corpo de requisição do Appium: {"script":"mobile: type","args":[{"text":"<segredo>"}]}
    (re.compile(r'("script"\s*:\s*"mobile:\s*type"\s*,\s*"args"\s*:\s*\[\s*\{[^}]*?"text"\s*:\s*")[^"]*'), r"\1" + MASK),
    # envio de teclas do WebDriver: {"text":"<segredo>","value":[...]}
    (re.compile(r'("text"\s*:\s*")[^"]*("\s*,\s*"value"\s*:\s*\[)[^\]]*(\])'), r"\1" + MASK + r'\2"' + MASK + r'"\3'),
    # pares chave/valor evidentes, em JSON ou em texto solto
    # O `[\w.\-]*` antes da palavra-chave não é enfeite. Sem ele, `API_TOKEN=...`, `db_password=...` e
    # `INSTAGRAM_CREDENTIALS_MASTER_KEY=...` saíam EM CLARO: `\b` não casa entre `_` e a letra seguinte (os dois são
    # caractere de palavra), então `\btoken\b` simplesmente não existe dentro de `API_TOKEN`. Nome com hífen
    # (`X-Auth-Token`) já passava, o que tornava a falha ainda menos visível. Descoberto medindo, não lendo.
    (re.compile(r'((?:"|\b)[\w.\-]*(?:' + _PALAVRAS_DE_SEGREDO + r')'
                r'(?:"|\b)\s*[:=]\s*"?)(?![,}\s])[^"\s,}]+', re.IGNORECASE), r"\1" + MASK),
    # Cabeçalho de autorização em QUALQUER esquema. Era só `Bearer`, e `Authorization: Basic dXNlcjpwYXNz` é
    # usuário e senha em base64 — não é cifra, é codificação: quem lê o log tem a senha.
    (re.compile(r"(Authorization\s*:\s*(?:Bearer|Basic|Token|ApiKey|Digest)\s+)\S+", re.IGNORECASE), r"\1" + MASK),
    # `adb shell input text '<senha>'`. Este texto NÃO deveria chegar a um log (a digitação manual passa pelo
    # stdin, ver devices/adb.py), mas o filtro é a defesa secundária justamente para o caminho que ninguém previu:
    # uma mensagem de erro do subprocesso, um `repr` de argumentos, um comando copiado para um chamado.
    (re.compile(r"(\binput\s+text\s+)(?:'[^']*'|\"[^\"]*\"|\S+)", re.IGNORECASE), r"\1" + MASK),
    # senha dentro de um DSN: postgresql://usuario:<segredo>@host:5432/parque. O padrão de pares chave/valor
    # acima NÃO pega este caso — `DATABASE_URL` não contém nenhuma das palavras da lista, e a senha viaja no meio
    # de uma URL, não depois de um `=`. Com PostgreSQL entre máquinas (docs/banco.md), esse DSN aparece em
    # mensagem de erro de conexão — justamente o log que alguém cola num chamado de suporte.
    (re.compile(r"\b(postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)(://[^:/?#\s@]+:)[^@\s]+(@)",
                re.IGNORECASE), r"\1\2" + MASK + r"\3"),
    # O mesmo formato num endereço web: `https://usuario:<senha>@portal/`. Com a automação abrindo sites
    # (ADR-025), é o jeito de "entrar com a senha" que alguém escreveria direto no comando.
    # `socks5://usuario:<senha>@proxy:1080` (ADR-056 §5, item 25.3): é assim que um proxy autenticado aparece na
    # configuração do cliente VPN e na mensagem de erro dele. O usuário fica legível (diz QUAL conta do provedor),
    # a senha não.
    (re.compile(r"\b((?:https?|socks(?:4a?|5h?))://[^:/?#\s@]+:)[^@\s/]+(@)", re.IGNORECASE), r"\1" + MASK + r"\2"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"), MASK),
    # Chave privada em PEM (29.154): `type chave.pem` num comando remoto a despejaria na saída. Vale o bloco inteiro e
    # também o CORTADO (sem o `END`): a saída de um comando é truncada, e a metade de uma chave ainda é segredo.
    (re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.DOTALL),
     MASK),
)

# ---------------------------------------------------------------- chave do WireGuard sem rótulo (item 25.3)
# Uma chave do WireGuard são 32 bytes em base64: 43 caracteres e um `=`, e o último antes do `=` só pode ser um
# destes 16 (os 4 bits que sobram são zero). Fora de contexto, 44 caracteres de base64 são qualquer coisa (hash,
# id); por isso a regra só vale quando o texto FALA de WireGuard — `wg set wg0 peer … preshared-key …`, uma linha
# de log do cliente, um bloco `[Peer]` colado num chamado. Com rótulo (`PrivateKey =`, `"private_key":`) o par
# chave/valor acima já mascara; esta regra é para a chave que aparece solta.
_CHAVE_WG = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{42}[AEIMQUYcgkosw048]=(?![A-Za-z0-9+/=])")
_CONTEXTO_WG = re.compile(r"wireguard|wg-quick|\bwg\d*\b|\[(?:interface|peer)\]|preshared|private[_-]?key|"
                          r"com\.wireguard", re.IGNORECASE)
# A chave PÚBLICA do par não é segredo, e é justamente a que o painel e o diagnóstico precisam mostrar (qual peer?).
# Também é o que os `params` de um perfil podem carregar: mascará-la faria o cadastro recusar um WireGuard legítimo
# (`rede.NetworkProfileInput._params` recusa o que a redação mudaria). Rótulo imediatamente antes do valor, nas
# formas de configuração (`PublicKey = `) e de JSON (`"public_key": "`).
_ROTULO_PUBLICO = re.compile(r"public[_-]?key\"?\s*[:=]\s*\"?$", re.IGNORECASE)


def _mascarar_chave_wg(texto: str) -> str:
    if not _CONTEXTO_WG.search(texto):
        return texto

    def troca(m: re.Match[str]) -> str:
        # Só o trecho da MESMA linha antes da chave decide o rótulo: `PublicKey` numa linha não protege a chave da
        # linha seguinte.
        inicio_da_linha = texto.rfind("\n", 0, m.start()) + 1
        return m.group(0) if _ROTULO_PUBLICO.search(texto[inicio_da_linha:m.start()]) else MASK

    return _CHAVE_WG.sub(troca, texto)


def redact(text: str | None) -> str | None:
    """Aplica todos os padrões conhecidos. Devolve o próprio valor quando não há nada a mascarar."""
    if not text:
        return text
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return _mascarar_chave_wg(out)


# Nome de chave que, sozinho, já basta para mascarar o valor: numa estrutura o valor chega isolado, sem o contexto
# textual que os padroes acima usam. MESMA lista dos pares chave/valor: uma constante so. Antes desta
# unificacao havia aqui um 'pin' delimitado por BACKSPACE literal (0x08) em vez da borda de palavra: a alternativa
# nunca casava, e uma chave chamada `pin` passava inteira.
_SENSITIVE_KEY = re.compile(_PALAVRAS_DE_SEGREDO, re.IGNORECASE)


def chave_sensivel(nome: object) -> bool:
    """O nome de campo/chave já diz que o valor é segredo? Mesma lista dos pares chave/valor."""
    return bool(_SENSITIVE_KEY.search(str(nome)))


def redact_obj(value: Any) -> Any:
    """Versão recursiva para estruturas que vão para evento, DTO ou log."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: (MASK if isinstance(k, str) and _SENSITIVE_KEY.search(k) and not isinstance(v, (dict, list, tuple))
                    else redact_obj(v))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return type(value)(redact_obj(v) for v in value)
    return value


# Formatos que NÃO podem virar lembrança nem histórico, mesmo sem um nome de campo por perto: código de verificação
# ditado numa conversa, chave de API, sequência longa sem espaço com cara de token.
_SECRET_SHAPES: tuple[re.Pattern[str], ...] = (
    # "o código é 123456", "code: 8421", "seu pin de acesso 9931" — palavra-chave e dígitos a poucos caracteres
    re.compile(r"\b(?:c[oó]digo|code|pin|otp|2fa|verifica[çc][aã]o|verification|senha|password)\b.{0,16}?"
               r"\b[0-9]{4,8}\b", re.IGNORECASE),
    re.compile(r"\b[0-9]{4,8}\b.{0,16}?\b(?:c[oó]digo|code|pin|otp)\b", re.IGNORECASE),
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\b(?:eyJ[A-Za-z0-9_\-]{10,}|gh[pousr]_[A-Za-z0-9]{20,})\b"),        # JWT, token do GitHub
    re.compile(r"(?<![\w/])[A-Za-z0-9+/]{32,}={0,2}(?![\w/])"),                       # blobs base64 longos
)


# Menção a credencial. Mais amplo que `looks_secret`: aqui basta o ASSUNTO, mesmo sem um valor reconhecível.
# Existe porque memória e histórico voltam ao modelo depois; uma frase com "minha senha é X" não pode ser guardada
# só porque X não tem cara de segredo.
_CREDENCIAL = re.compile(
    r"\b(senha|password|passwd|credencial|credential|token|api[ _-]?key|otp|2fa|pin|"
    # outros idiomas (reverificação B do 31.9, 03/10): só AMPLIA o que se recusa guardar
    r"passwort|kennwort|wachtwoord|mot de passe|parola d['’]ordine|contrase[nñ]a|"
    r"c[oó]digo de (?:verifica[çc][aã]o|acesso|seguran[çc]a|confirma[çc][aã]o))\b", re.IGNORECASE)


def mentions_credential(text: str | None) -> bool:
    """Verdadeiro quando o texto FALA de credencial, código ou token — com ou sem o valor junto."""
    return bool(text) and bool(_CREDENCIAL.search(text or ""))


# ---------------------------------------------------------------- linha de comando com credencial (29.154)
# O operador NÃO digita segredo no comando remoto (ADR-040: senha só pelo canal sensível). `redact` mascara pares
# `chave=valor`, mas não cobre os jeitos de linha de comando que levam a senha como argumento solto; estes, sim.
_LINHA_COM_CREDENCIAL: tuple[re.Pattern[str], ...] = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bnet\s+use\b[^\n]*?/user:",                    # net use \host /user:u senha
    r"\bcurl\b[^\n]*?(?:\s-u\s+\S+|--user\s+\S+)",    # curl -u usuario:senha
    r"\bConvertTo-SecureString\b",                    # senha em texto no PowerShell
    r"-AsPlainText\b",
    r"\bsshpass\b",
    r"\bplink\b[^\n]*?\s-pw\b",
    r"\bcmdkey\b[^\n]*?/pass\b",
    r"\bnet\s+user\b[^\n]*?\s/add\b",                 # criar conta e definir senha
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----",
))


def linha_de_comando_suspeita(linha: str | None) -> bool:
    """A linha do comando remoto leva (ou fala de) credencial? Recusa-se ANTES de gravar ou despachar: a linha crua
    nunca pode ir ao diário do agente, a evento ou a log. Mais rígida que `looks_secret` de propósito: uma linha
    legítima de manutenção quase nunca precisa da palavra "senha" ou "token"."""
    if not linha:
        return False
    if looks_secret(linha) or mentions_credential(linha):
        return True
    return any(p.search(linha) for p in _LINHA_COM_CREDENCIAL)


def cortar_saida(texto: str, max_bytes: int) -> tuple[str, bool]:
    """Guarda o começo e o fim de uma saída que passa de `max_bytes` (em UTF-8). Chamar DEPOIS de `redact`: o corte não
    pode partir um segredo ao meio (comando remoto, 29.154). Devolve o texto e se houve corte."""
    bruto = texto.encode("utf-8")
    if len(bruto) <= max_bytes:
        return texto, False
    metade = max(max_bytes // 2 - 48, 16)
    inicio = bruto[:metade].decode("utf-8", errors="ignore")
    fim = bruto[-metade:].decode("utf-8", errors="ignore")
    return f"{inicio}\n[... {len(bruto) - 2 * metade} bytes cortados ...]\n{fim}", True


def looks_secret(text: str | None) -> bool:
    """Verdadeiro quando o texto tem FORMATO de segredo. Usado para recusar memória e histórico, não para mascarar.

    É deliberadamente conservador: recusar um fato inofensivo custa pouco; guardar um código de verificação numa
    lembrança que depois vai ao modelo custa caro.
    """
    if not text:
        return False
    if redact(text) != text:                      # já bate num padrão conhecido de credencial
        return True
    return any(p.search(text) for p in _SECRET_SHAPES)


class RedactingFilter:
    """Filtro de logging: redige a mensagem já formatada, antes de ela chegar a qualquer handler."""

    def filter(self, record: Any) -> bool:  # noqa: A003 - assinatura da stdlib
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 - log nunca pode derrubar a aplicação
            return True
        clean = redact(text)
        if clean != text:
            record.msg = clean
            record.args = ()
        return True


def parece_senha_ou_codigo(texto: str) -> bool:
    """Mais rígido que `looks_secret`, de propósito: numa gravação, a pessoa digita a senha de verdade (entrar no
    Outlook é o primeiro uso óbvio) e nem sempre o campo se declara de senha. Palavra única de 8+ caracteres que
    mistura três tipos (minúscula, maiúscula, dígito, símbolo), ou só dígitos de 6 a 8 (código de verificação),
    não é gravada. Uma frase normal passa; a IA recebe só "digitou N caracteres" no lugar do resto."""
    t = (texto or "").strip()
    if not t or any(c.isspace() for c in t):
        return False
    if t.isdigit() and 6 <= len(t) <= 8:
        return True
    tipos = sum((any(c.islower() for c in t), any(c.isupper() for c in t), any(c.isdigit() for c in t),
                 any(not c.isalnum() and c not in "._-@" for c in t)))   # . _ - @ são de usuário/e-mail
    return len(t) >= 8 and tipos >= 3


#: Só dígitos (com espaço ou hífen entre eles), de 4 a 8: o formato de um código de verificação.
_CODIGO_SOLTO = re.compile(r"[\s-]*(?:\d[\s-]?){4,8}[\s-]*")


def parece_codigo(texto: str | None) -> bool:
    """O texto INTEIRO é um código de 4 a 8 dígitos ("884512", "884 512", "8845-12"). Mais largo que
    `parece_senha_ou_codigo` (6 a 8, sem espaço) porque julga uma RESPOSTA solta, em que o código chega sozinho e
    às vezes partido (29.52; é a regra do canal do 28.15). Na dúvida, recusa: um "2024" respondido solto cai aqui."""
    return bool(texto) and bool(_CODIGO_SOLTO.fullmatch(texto or ""))


#: A palavra que anuncia um código de verificação numa linha de tela ou de SMS (31.82). Palavra inteira, sem diferenciar
#: maiúsculas ("pin" não pega "spinner").
_PALAVRA_DE_CODIGO = re.compile(
    r"\b(?:code|c[oó]digo|codigo|verify|verification|verifica[çc][aã]o|verificar|senha|password|pin|otp|token"
    r"|confirma[çc][aã]o|confirmation)\b", re.IGNORECASE)
#: 4 a 8 dígitos, com UM espaço ou hífen no meio (o prefixo curto "G-" fica de fora: o hífen já é fronteira de palavra).
_NUMERO_DE_CODIGO = re.compile(r"(?<!\w)(?:\d{4,8}|\d{2,6}[ -]\d{2,6})(?!\w)")
_DISTANCIA_DO_CODIGO = 40


def parece_linha_com_codigo(linha: str | None) -> bool:
    """A linha de tela FALA de código e traz o número perto: 4 a 8 dígitos (aceita um espaço ou hífen no meio e prefixo
    curto como "G-123456") a até 40 caracteres, antes ou depois, de uma palavra como code, código, verify, senha, pin,
    otp ou token. "G-123456 is your Google verification code" e "Use 123 456 to verify your Instagram account." caem;
    "Recife 2024", "Pedido 48213 entregue" e "há 5 min" não (sem a palavra). Julga linhas de tela, título e rótulo de
    alvo gravados; o texto digitado tem a regra do foco."""
    t = linha or ""
    if not t:
        return False
    palavras = [m.span() for m in _PALAVRA_DE_CODIGO.finditer(t)]
    if not palavras:
        return False
    for m in _NUMERO_DE_CODIGO.finditer(t):
        if not 4 <= sum(c.isdigit() for c in m.group()) <= 8:
            continue
        for ini, fim in palavras:
            distancia = ini - m.end() if ini >= m.end() else m.start() - fim
            if distancia <= _DISTANCIA_DO_CODIGO:
                return True
    return False
