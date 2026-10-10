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
* **O efeito (31.297, ADR-091).** Com `limits.exploracao_efeito_ligada` o verbo de efeito também explora: a etapa leva
  `side_effect=True`, a chave é `explorar_<verbo canônico>_<objeto…>` (sinônimos viram um verbo só) e a PORTA 13.2 a julga
  como a ação do catálogo, por uma ação sintética (`capability_da_exploracao`) cuja política o dono configura no perfil pela
  chave específica ou pela genérica `explorar_efeito`. Sem a política liberar, a etapa pede aprovação antes de agir.
* **O molde.** O título e o objetivo da etapa de uma execução levam o pedido (a exploração precisa dele). O molde que
  vai a outras personas é refeito só com a chave (`molde_da_exploracao`): o pedido pode ter carregado um nome.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum

from ..contracts.credencial_e_sessao import FORMAS_DE_CREDENCIAL, e_credencial
from ..models import PlanStep, Postcondition
from .capabilities import Capability

PREFIXO = "explorar_"
#: 31.297: a chave de política que vale para TODA exploração de efeito do app quando o perfil não escolheu uma específica.
CHAVE_GENERICA_DE_EFEITO = "explorar_efeito"

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
#: 31.297: o sinônimo → o verbo canônico de efeito que vai na chave ("mandar" e "enviar" são a mesma política do dono). O que
#: não está aqui fica como está (`_EFEITO` é a lista de quais verbos são de efeito; a tabela só junta os que dizem o mesmo).
_EFEITO_CANONICO: dict[str, str] = {
    "mandar": "enviar", "encaminhar": "enviar", "responder": "enviar", "postar": "publicar", "compartilhar": "publicar",
    "excluir": "apagar", "deletar": "apagar", "remover": "apagar", "esvaziar": "apagar", "limpar": "apagar",
    "mudar": "alterar", "editar": "alterar", "atualizar": "alterar", "trocar": "alterar", "configurar": "alterar",
    "ativar": "alterar", "desativar": "alterar", "cadastrar": "criar", "registrar": "criar", "adicionar": "criar",
    "assinar": "comprar", "pagar": "comprar", "arquivar": "mover", "reservar": "agendar", "aprovar": "aceitar",
    "confirmar": "aceitar",
}
#: Os verbos canônicos que podem aparecer na chave de um efeito (os que a tabela não junta ficam como são). Credencial e sessão
#: (`contracts/credencial_e_sessao.py`, lista única) ficam de fora: não têm política, porque nunca exploram.
VERBOS_DE_EFEITO = frozenset(_EFEITO_CANONICO.get(v, v) for v in _EFEITO) - FORMAS_DE_CREDENCIAL
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
    de_credencial: bool = False  # 31.297: entrar/sair/cadastrar: nunca explora, nem com o interruptor de efeito ligado


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
    de_efeito = next((p for p in palavras[:2] if p in _EFEITO), None)
    credencial = e_credencial(palavras)
    if credencial and de_efeito is None:
        # credencial e sessão em QUALQUER posição ("fazer login", "redefinir a senha") contam como efeito, e esse efeito nunca explora
        de_efeito = next((p for p in palavras if p in FORMAS_DE_CREDENCIAL), "credencial")
    if de_efeito is not None:
        # 31.297: a chave do efeito tem a forma da de leitura (`explorar_<verbo>_<objeto…>`), com o verbo canônico: é por ela
        # que a política do dono casa com o pedido. Sem objeto reconhecido leva o sufixo de letras (o dono usa a genérica).
        verbo_efeito = _EFEITO_CANONICO.get(de_efeito, de_efeito)
        # Palavras inteiras: a chave cortada no meio de um objeto não casaria com a que o dono configurou.
        chave_efeito = PREFIXO + verbo_efeito
        for parte in (*objetos, *(() if objetos else (_sufixo(pedido),))):
            if len(chave_efeito) + 1 + len(parte) <= 40:
                chave_efeito += "_" + parte
        return Exploracao(Destino.EFEITO, verbo_efeito, objetos, chave_efeito, False, credencial)
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
    frase = _frase_da_chave(e.chave)
    if e.destino is Destino.EFEITO:
        return _passo_de_efeito(pedido, e, frase, app_id=app_id, nome_do_app=nome_do_app)
    ordem = ("Só leitura e navegação: não envie, publique, siga, curta, comente, apague, salve nem altere nada, e não "
             "digite senha. Se chegar a um ponto que exigiria isso, pare e diga o que viu.")
    return PlanStep(
        key=e.chave, title=f"Explorar o {nome_do_app}: {pedido}"[:200],
        goal=(f"O catálogo de ações do {nome_do_app} não cobre este pedido: {pedido}. Descubra como fazê-lo pela "
              f"interface e comprove o resultado na tela. {ordem}"),
        # O `value` entra no hash da etapa e é IGUAL ao do molde (`molde_da_exploracao`): a receita aprendida aqui é achada
        # pelo molde de outra execução. O pedido (que pode ter nome) vai só na `description`, que o hash não lê.
        postcondition=Postcondition(kind="model_judged", value=f"a tela mostra: {frase}",
                                    description=f"O pedido '{pedido}' foi atendido: a tela mostra o resultado, sem "
                                                "nenhuma alteração feita."),
        side_effect=False, app_id=app_id, exploratoria=True, timeout_s=300, max_attempts=1)


def _passo_de_efeito(pedido: str, e: Exploracao, frase: str, *, app_id: str | None, nome_do_app: str) -> PlanStep:
    """31.297: a etapa de EFEITO. A porta 13.2 a julga antes de o aparelho ser tocado (`capability_da_exploracao`); o que o
    executor faz depois é o do caminho livre com efeito (guarda do commit, não repetir, comprovar). A ordem no objetivo
    limita o efeito ao pedido e mantém a regra da credencial (ADR-040): senha nunca é digitada aqui."""
    ordem = ("Faça SÓ o que o pedido manda e mais nada: não toque em outro efeito, não envie nem publique o que não foi "
             "pedido e não digite senha. Se a tela pedir a senha, um código ou uma confirmação de identidade, pare e diga o "
             "que viu.")
    return PlanStep(
        key=e.chave, title=f"Explorar o {nome_do_app} (com efeito): {pedido}"[:200],
        goal=(f"O catálogo de ações do {nome_do_app} não cobre este pedido: {pedido}. Descubra como fazê-lo pela "
              f"interface e comprove na tela que foi feito. {ordem}"),
        postcondition=Postcondition(kind="model_judged", value=f"a tela mostra: {frase}",
                                    description=f"O pedido '{pedido}' foi feito: a tela mostra o resultado."),
        side_effect=True, app_id=app_id, exploratoria=True, timeout_s=300, max_attempts=1)


def capability_da_exploracao(chave: str, *, titulo: str | None = None) -> Capability:
    """31.297: a ação SINTÉTICA da etapa exploratória de efeito, para a política do perfil e a aprovação a tratarem como as
    do catálogo. Padrão `approval_required` e risco alto: sem o dono liberar, a etapa pede o sim antes de tocar no aparelho.
    Não tem texto gerado (o efeito é o da tela), nem balde de limite, nem contraparte: o que o perfil controla é a política."""
    frase = _frase_da_chave(chave)
    return Capability(
        key=chave, title=titulo or f"Explorar com efeito: {frase}", goal=f"Fazer pela interface: {frase}",
        post_kind="model_judged", post_value=f"a tela mostra: {frase}", post_description=f"O pedido foi feito: {frase}.",
        side_effect=True, risk="high", default_policy="approval_required", internal=True, timeout_s=300, max_attempts=1)


def e_exploracao_de_efeito(passo_key: str, exploratoria: bool, side_effect: bool) -> bool:
    """A etapa que o SISTEMA montou como exploração de efeito (31.297): marcada exploratória, com efeito e com a chave
    `explorar_…`. O plano que o modelo escreve não ganha a porta sintética só por trazer `exploratoria`."""
    return bool(exploratoria and side_effect and passo_key.startswith(PREFIXO))


def chaves_da_politica(chave: str) -> tuple[str, ...]:
    """Da mais específica à mais geral: a chave da exploração e, só para a exploração de efeito, a genérica."""
    if chave.startswith(PREFIXO) and chave != CHAVE_GENERICA_DE_EFEITO and chave.split("_")[1] in VERBOS_DE_EFEITO:
        return (chave, CHAVE_GENERICA_DE_EFEITO)
    return (chave,)


def chave_de_politica_valida(chave: str) -> bool:
    """O que o dono pode configurar: a genérica ou `explorar_<verbo de efeito>[_<objeto do vocabulário>…]`. O sufixo de
    letras do pedido sem objeto NÃO é configurável (ele não sabe qual é): para esse caso existe a genérica."""
    if chave == CHAVE_GENERICA_DE_EFEITO:
        return True
    partes = chave.split("_")
    return (len(partes) >= 2 and partes[0] == PREFIXO.rstrip("_") and partes[1] in VERBOS_DE_EFEITO
            and len(partes) - 2 <= _MAX_OBJETOS and all(p in OBJETOS for p in partes[2:]) and bool(_ALVO_DO_NOME.match(chave)))


def molde_da_exploracao(passo: PlanStep) -> PlanStep | None:
    """O molde da etapa descoberta para OUTRAS execuções, refeito só com a chave (vocabulário fechado): `None` se a
    chave não for oferecível. Mantém o `kind` da pós-condição e a chave, então a receita (que casa por chave e, nas
    `model_judged`, também pelo hash genérico sem o texto da pós-condição) continua sendo achada."""
    if passo.side_effect or not chave_oferecivel(passo.key):
        return None               # 31.297: o efeito descoberto nunca é oferecido a outra execução; a política julga cada vez
    frase = _frase_da_chave(passo.key)
    ordem = "Só leitura e navegação: não altere nada."
    return passo.model_copy(update={
        "title": f"Explorar: {frase}", "goal": f"Chegar na tela do pedido ({frase}) e comprovar. {ordem}",
        "postcondition": passo.postcondition.model_copy(update={
            "value": f"a tela mostra: {frase}", "description": f"A tela mostra o pedido: {frase}."}),
        "precondition": None, "commit_guard": [], "bindings": {}, "saidas": [], "variables": {}})


__all__ = ["CHAVE_GENERICA_DE_EFEITO", "Destino", "Exploracao", "FORMAS_DE_CREDENCIAL", "OBJETOS", "PREFIXO", "VERBOS_DE_EFEITO", "VOCABULARIO",
           "capability_da_exploracao", "chave_de_politica_valida", "e_exploracao_de_efeito", "chave_oferecivel", "chaves_da_politica", "classificar", "e_credencial",
           "molde_da_exploracao", "passo_da_exploracao"]
