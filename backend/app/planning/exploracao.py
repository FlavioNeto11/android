"""31.273 (ADR-084): o pedido fora do catálogo do app explora em vez de recusar. Puro: sem banco, sem IA.

O planejador com catálogo já devolve `fora_do_catalogo` (QUAL app e O QUE foi pedido, no infinitivo) quando nenhuma
ação cobre o pedido. Em vez de zerar o plano com a frase da recusa, o sistema monta UMA etapa livre de exploração
(`PlanStep.exploratoria`), de leitura e navegação, e o executor age como em qualquer etapa livre, dentro dos tetos.

Três regras que moram aqui, todas de vocabulário FECHADO (nada do que o modelo escreveu vira nome sem passar por elas):

* **O que explora.** Verbo de efeito (enviar, publicar, seguir, apagar, mudar…) segue a recusa de antes: efeito externo
  sem ação do catálogo passaria por fora da política e dos limites do perfil (porta do item 13.2, `efeito_fora_do_catalogo`),
  e a exploração não a contorna. Verbo de leitura ou navegação explora; verbo que não está em nenhuma lista também
  explora, mas com a ordem escrita no objetivo de não mudar nada.
* **A chave estável.** `explorar_<verbo>_<objeto…>` só com palavras do vocabulário (sem dígito, sem nome, sem valor): a
  mesma exploração pedida de outro jeito dá a mesma chave, e a chave pode ir ao prompt de todas as personas do app
  (`etapas_ensinadas.EtapaEnsinada.linha`). Sem objeto reconhecido a chave leva um sufixo de letras (hash do pedido),
  fica única e NUNCA é oferecida (o sufixo não é vocabulário).
* **O molde.** O título e o objetivo da etapa de uma execução levam o pedido (a exploração precisa dele). O molde que
  vai a outras personas é refeito só com a chave (`molde_da_exploracao`): o pedido pode ter carregado um nome.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from ..models import PlanStep, Postcondition

PREFIXO = "explorar_"

#: Verbo → forma da chave. O que o modelo diz em poucas palavras, no infinitivo ("ver a caixa de lixo eletrônico").
_LEITURA: dict[str, str] = {
    "abrir": "abrir", "ver": "ver", "ler": "ler", "olhar": "ver", "visualizar": "ver", "exibir": "ver", "mostrar": "ver",
    "buscar": "buscar", "procurar": "buscar", "pesquisar": "buscar", "achar": "buscar", "encontrar": "buscar",
    "localizar": "buscar", "listar": "listar", "consultar": "consultar", "conferir": "conferir", "checar": "conferir",
    "verificar": "conferir", "navegar": "navegar", "acessar": "abrir", "ir": "navegar", "descobrir": "buscar",
    "explorar": "navegar", "contar": "contar", "identificar": "buscar",
}
#: Verbos de efeito externo (ou que mudam o estado do app/da conta): seguem recusados, com o motivo dito.
_EFEITO = frozenset({
    "enviar", "mandar", "publicar", "postar", "seguir", "curtir", "comentar", "responder", "apagar", "excluir", "deletar",
    "remover", "alterar", "mudar", "editar", "salvar", "comprar", "pagar", "assinar", "cancelar", "compartilhar",
    "encaminhar", "bloquear", "desbloquear", "denunciar", "instalar", "desinstalar", "configurar", "ativar", "desativar",
    "criar", "cadastrar", "registrar", "adicionar", "marcar", "arquivar", "mover", "transferir", "reservar", "agendar",
    "escrever", "digitar", "preencher", "aceitar", "recusar", "confirmar", "aprovar", "doar", "votar", "anexar", "trocar",
    "atualizar", "sair", "entrar", "logar", "autenticar", "desfazer", "restaurar", "esvaziar", "limpar", "reiniciar",
    "resetar", "parar", "deixar",
})
#: Palavras de lugar e de coisa do app que podem entrar na chave. Sem acento, em minúsculas.
OBJETOS = frozenset({
    "caixa", "entrada", "saida", "lixeira", "lixo", "spam", "eletronico", "configuracoes", "ajustes", "perfil", "perfis",
    "mensagem", "mensagens", "conversa", "conversas", "notificacao", "notificacoes", "busca", "pasta", "pastas", "email",
    "emails", "conta", "contas", "historico", "favoritos", "salvos", "feed", "contatos", "contato", "calendario",
    "agenda", "ajuda", "idioma", "tema", "privacidade", "seguranca", "rascunhos", "enviados", "arquivados",
    "arquivo", "arquivos", "lista", "ultimo", "ultima", "primeiro", "primeira", "recentes", "recente", "novos", "novas",
    "inicio", "principal", "menu", "tela", "aba", "abas", "cartoes", "pagamentos", "assinaturas", "dispositivos",
    "sessoes", "atividade", "anexos", "etiquetas", "marcadores", "importantes", "nao", "lidas", "lidos", "lida", "lido",
    "mail", "post", "posts", "stories", "reels", "comentarios", "seguidores", "seguindo", "curtidas", "explorar", "descoberta",
    "pedidos", "carrinho", "ofertas", "resultados", "resultado", "tarefas", "notas", "fotos", "videos", "documentos",
    "versao", "bateria", "rede", "wifi", "bluetooth", "som", "tela", "brilho", "armazenamento", "apps", "aplicativos",
})
#: Os verbos que entram na chave (as formas da tabela) também são vocabulário.
_VERBOS_DA_CHAVE = frozenset(_LEITURA.values())
VOCABULARIO = frozenset(_VERBOS_DA_CHAVE | OBJETOS | {"explorar"})
_STOP = frozenset({"a", "o", "as", "os", "de", "do", "da", "dos", "das", "no", "na", "nos", "nas", "em", "um", "uma",
                   "e", "ou", "para", "por", "com", "que", "meu", "minha", "seu", "sua", "ao", "aos", "à", "às"})
_MAX_OBJETOS = 3
_ALVO_DO_NOME = re.compile(r"^explorar_[a-z]+(?:_[a-z]+)*$")


class Destino(Enum):
    EXPLORAR = "explorar"
    EFEITO = "efeito"


@dataclass(frozen=True, slots=True)
class Exploracao:
    destino: Destino
    verbo: str                   # a forma da chave ("" quando o verbo não está em lista)
    objetos: tuple[str, ...]     # só vocabulário
    chave: str                   # `explorar_…`, válida como `PlanStep.key`
    de_leitura: bool             # o verbo é de leitura/navegação conhecido (senão, a ordem de não mudar nada vai no objetivo)


def _normal(texto: str) -> str:
    sem = "".join(c for c in unicodedata.normalize("NFKD", texto.casefold()) if not unicodedata.combining(c))
    return sem


def _palavras(texto: str) -> list[str]:
    return re.findall(r"[a-z]+", _normal(texto))


def _sufixo(pedido: str) -> str:
    """Letras (nunca dígito) derivadas do pedido: a chave sem objeto reconhecido continua única por pedido."""
    n = int(hashlib.sha1(_normal(pedido).encode()).hexdigest()[:10], 16)
    letras = []
    for _ in range(6):
        n, resto = divmod(n, 26)
        letras.append(chr(ord("a") + resto))
    return "".join(letras)


def classificar(pedido: str) -> Exploracao:
    """A decisão e a chave estável para o `pedido` que o planejador devolveu em `fora_do_catalogo`."""
    palavras = [p for p in _palavras(pedido) if p not in _STOP]
    verbo_cru = palavras[0] if palavras else ""
    resto = palavras[1:] if palavras else []
    objetos = tuple(dict.fromkeys(p for p in resto if p in OBJETOS))[:_MAX_OBJETOS]
    if verbo_cru in _EFEITO or any(p in _EFEITO for p in palavras[:2]):
        return Exploracao(Destino.EFEITO, "", objetos, PREFIXO + (verbo_cru or "efeito"), False)
    verbo = _LEITURA.get(verbo_cru, "")
    corpo = [verbo or "navegar", *(objetos or (_sufixo(pedido),))]
    chave = (PREFIXO + "_".join(corpo))[:40].rstrip("_")
    return Exploracao(Destino.EXPLORAR, verbo, objetos, chave, bool(verbo))


def chave_oferecivel(chave: str) -> bool:
    """A chave só com vocabulário: nada de sufixo de hash, nome ou valor. Só ela pode ir ao prompt de outras personas."""
    if not _ALVO_DO_NOME.match(chave):
        return False
    return all(p in VOCABULARIO for p in chave.split("_"))


def _frase_da_chave(chave: str) -> str:
    return " ".join(chave.removeprefix(PREFIXO).split("_"))


def passo_da_exploracao(pedido: str, e: Exploracao, *, app_id: str | None, nome_do_app: str) -> PlanStep:
    """A etapa livre, só de leitura e navegação, para o `pedido`. O pedido (que pode ter nome) vai só no objetivo e na
    pós-condição DESTA execução; o molde oferecido às outras personas sai de `molde_da_exploracao`."""
    pedido = " ".join(pedido.split())[:200]
    ordem = ("Só leitura e navegação: não envie, publique, siga, curta, comente, apague, salve nem altere nada, e não "
             "digite senha. Se chegar a um ponto que exigiria isso, pare e diga o que viu.")
    return PlanStep(
        key=e.chave, title=f"Explorar o {nome_do_app}: {pedido}"[:200],
        goal=(f"O catálogo de ações do {nome_do_app} não cobre este pedido: {pedido}. Descubra como fazê-lo pela "
              f"interface e comprove o resultado na tela. {ordem}"),
        postcondition=Postcondition(kind="model_judged", value=f"a tela mostra o que foi pedido: {pedido}"[:300],
                                    description=f"O pedido '{pedido}' foi atendido: a tela mostra o resultado, sem "
                                                "nenhuma alteração feita."),
        side_effect=False, app_id=app_id, exploratoria=True, timeout_s=300, max_attempts=1)


def molde_da_exploracao(passo: PlanStep) -> PlanStep | None:
    """O molde da etapa descoberta para OUTRAS execuções, refeito só com a chave (vocabulário fechado): `None` se a
    chave não for oferecível. Mantém o `kind` da pós-condição e a chave, então a receita (que casa por chave e, nas
    `model_judged`, também pelo hash genérico sem o texto da pós-condição) continua sendo achada."""
    if not chave_oferecivel(passo.key):
        return None
    frase = _frase_da_chave(passo.key)
    ordem = "Só leitura e navegação: não altere nada."
    return passo.model_copy(update={
        "title": f"Explorar: {frase}", "goal": f"Chegar na tela do pedido ({frase}) e comprovar. {ordem}",
        "postcondition": passo.postcondition.model_copy(update={
            "value": f"a tela mostra: {frase}", "description": f"A tela mostra o pedido: {frase}."}),
        "precondition": None, "commit_guard": [], "bindings": {}, "saidas": [], "variables": {}})


__all__ = ["Destino", "Exploracao", "OBJETOS", "PREFIXO", "VOCABULARIO", "chave_oferecivel", "classificar",
           "molde_da_exploracao", "passo_da_exploracao"]
