"""31.297 (ADR-040, ADR-087): o que é CREDENCIAL ou SESSÃO num pedido em português. Lista única: a exploração (leitura e efeito) a lê
daqui e nada mais a repete. O pedido que mexe nisso nunca é explorado pela IA livre: entrar, sair, cadastrar, trocar de conta e a senha
têm mecanismo próprio (`sessao.yaml`, cadastro guiado, canal sensível `type_secret`), mesmo com o interruptor de efeito ligado e a
política liberada.

O planejador devolve o pedido "no infinitivo" só por prompt, então a lista cobre infinitivo, imperativo, substantivo, inglês e as
palavras do segredo em si, em QUALQUER posição. "entrada" não está aqui de propósito: "caixa de entrada" é leitura.

Sacrifício consciente (só recusa, lado seguro): "entre" como preposição ("mover mensagens entre pastas") e "código" ("código de
barras") também recusam; a pessoa reescreve o pedido ou faz pelo catálogo.
"""
from __future__ import annotations

#: As palavras já normalizadas (minúsculas, sem acento, só letras: "2FA" vira "fa").
FORMAS_DE_CREDENCIAL: frozenset[str] = frozenset({
    "entrar", "entre", "logar", "logue", "login", "logout", "logoff", "signin", "signout", "signup", "sair", "saia",
    "autenticar", "autentique", "autenticacao", "cadastrar", "cadastre", "cadastro", "registrar", "registre", "inscrever",
    "inscreva", "inscricao", "desconectar", "desconecte", "conectar", "conecte", "senha", "senhas", "password", "credencial",
    "credenciais", "token", "tokens", "codigo", "codigos", "fa", "mfa", "otp"})
#: "criar/adicionar/abrir/trocar (uma) conta (nova)" é cadastro ou troca de sessão (ADR-087): só com a conta logo depois do verbo,
#: para "abrir as configurações da conta" seguir sendo leitura.
VERBOS_DE_CONTA: frozenset[str] = frozenset({
    "criar", "crie", "adicionar", "adicione", "abrir", "abra", "trocar", "troque", "alternar", "alterne", "mudar", "mude"})


def e_credencial(palavras: list[str]) -> bool:
    """O pedido mexe em credencial ou sessão (entrar, sair, cadastrar, trocar de conta, senha, código, 2FA…): nunca explora.
    `palavras` já normalizadas e sem as de ligação."""
    if any(p in FORMAS_DE_CREDENCIAL for p in palavras):
        return True
    util = [p for p in palavras if p not in ("novo", "nova")]
    return any(a in VERBOS_DE_CONTA and b in ("conta", "contas") for a, b in zip(util, util[1:]))
