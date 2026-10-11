"""Regras puras da ponte android ⇄ igfarm: o formato do @, o texto do pedido do @ ao modelo e os valores que a ponte passa
entre as camadas (a ficha da pessoa, a sugestão persistente, o resultado). Sem banco, sem rede, sem relógio."""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

#: Validade da reserva (`reservar=true`): a persona sai das outras chamadas por este tempo e volta sozinha.
TTL_RESERVA_HORAS = 24
#: O que o Instagram aceita num @ (e o que a ponte aceita sugerir): 3 a 30 caracteres de `[a-z0-9._]`.
_USERNAME = re.compile(r"^[a-z0-9._]{3,30}$")
#: A máscara fixa que a resposta da API 2 mostra no lugar das senhas (nunca o valor).
SENHAS_MASCARADAS = "••••"
#: Teto de pedidos ao modelo por @ (a primeira + as repetições com o que foi recusado).
TENTATIVAS_DE_USERNAME = 3

SYSTEM_DO_USERNAME = (
    "Você sugere nomes de usuário (@) de rede social para uma pessoa fictícia brasileira. Responda SOMENTE com o nome de "
    "usuário, sem @, sem aspas e sem explicação: de 3 a 30 caracteres, apenas letras minúsculas sem acento, números, "
    "ponto e sublinhado, sem ponto no começo ou no fim e sem dois pontos seguidos. Soe como uma conta de gente de "
    "verdade (hobby, profissão, apelido, cidade), não como um nome completo seguido de números aleatórios.")


def normalizar_username(texto: str | None) -> str:
    """O @ como a ponte o guarda e compara: sem espaços nas pontas, sem `@`, em minúsculas."""
    return (texto or "").strip().lstrip("@").strip().lower()


def username_valido(username: str) -> bool:
    return bool(_USERNAME.match(username)) and not username.startswith(".") and not username.endswith(".") \
        and ".." not in username


def username_do_modelo(texto: str) -> str:
    """O primeiro @ plausível na resposta do modelo (ele pode embrulhar em aspas, crase, `@` ou dar uma linha de
    explicação): a primeira palavra da primeira linha não vazia, normalizada. A validade é de `username_valido`."""
    for linha in (texto or "").splitlines():
        limpo = linha.strip().strip("`'\"*_ ")
        if limpo:
            return normalizar_username(limpo.split()[0].strip("`'\"*,;:"))
    return ""


@dataclass(frozen=True)
class FichaDaPessoa:
    """O que a ponte lê de uma pessoa (nunca a conta, a credencial ou a sessão)."""
    persona_id: str
    nome: str
    primeiro_nome: str
    sobrenome: str
    nome_exibicao: str
    birth_date: str | None
    idade: int | None
    genero: str | None
    biografia: dict[str, object]
    visual: dict[str, object]
    resumo: str | None
    profissao: str | None = None
    cidade: str | None = None
    interesses: tuple[str, ...] = ()


def pedido_do_username(ficha: FichaDaPessoa, recusados: Sequence[str]) -> str:
    """O texto do pedido: o que a pessoa faz e gosta, nunca a conta nem o e-mail. `recusados` são os @ que já voltaram
    inválidos ou tomados nesta sugestão — o modelo não deve repeti-los."""
    linhas = [f"Pessoa: {ficha.nome_exibicao or ficha.nome}"]
    if ficha.profissao:
        linhas.append(f"Profissão: {ficha.profissao}")
    if ficha.cidade:
        linhas.append(f"Cidade: {ficha.cidade}")
    if ficha.interesses:
        linhas.append("Gostos: " + ", ".join(ficha.interesses[:5]))
    if ficha.resumo:
        linhas.append(f"Resumo: {ficha.resumo[:300]}")
    if recusados:
        linhas.append("Não sirvam (já usados ou inválidos): " + ", ".join(recusados))
    linhas.append("Sugira um nome de usuário.")
    return "\n".join(linhas)


@dataclass(frozen=True)
class Sugestao:
    """A sugestão persistente de uma pessoa (e-mail + @ + imagem); `reservada_em` preenchida = reserva vigente."""
    persona_id: str
    email: str
    username: str
    imagem_id: str | None = None
    reservada_em: str | None = None
    expira_em: str | None = None


@dataclass(frozen=True)
class ImagemDaPessoa:
    id: str
    url: str


@dataclass(frozen=True)
class PersonaPendente:
    ficha: FichaDaPessoa
    email_sugerido: str
    username_sugerido: str
    imagem: ImagemDaPessoa | None
    imagem_pendente: bool
    #: Só para o evento de reserva: a imagem pedida não saiu (recusa, falha). Nunca vai na resposta.
    imagem_erro: str | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ComandoDeRegistro:
    """O corpo da API 2, já sem tipos de transporte. As senhas são o texto aberto (`SecretStr` já aberto na borda)."""
    persona_id: str
    dominio: str
    email: str
    email_senha: str
    instagram_username: str
    instagram_senha: str
    igfarm_account_id: str
    criada_em: str
    por: str
    proxy_url: str | None = None
    ip_criacao: str | None = None


@dataclass(frozen=True)
class EgressoDoDevice:
    """O que a ponte fez com a saída de UM aparelho vinculado à persona: `atribuido` (perfil pedido agora),
    `ja_atribuido` (o aparelho já o tinha: nada mudou) ou `pendente_confirmacao` (há conta real de OUTRA persona
    no aparelho: só a pessoa confirma, e o `motivo` diz por quê)."""
    instance_id: str
    estado: str
    motivo: str = ""


@dataclass(frozen=True)
class ContaRegistrada:
    persona_id: str
    account_id: str
    igfarm_account_id: str
    email: str
    instagram_username: str
    criada_em: str
    registrada_em: str
    idempotente: bool
    proxy_secret_ref: str | None = None
    egresso: tuple[EgressoDoDevice, ...] = ()


@dataclass(frozen=True)
class CodigoDaConta:
    codigo: str
    recebido_em: str
    remetente: str


@dataclass(frozen=True)
class ContatoDaConta:
    """Uma tentativa de login no app (31.333): quando começou, quantos minutos depois da criação no igfarm e o desfecho.
    `detalhe` é o texto que o motor de sessão já grava (sem senha, sem texto de tela, identificador mascarado)."""

    iniciado_em: str
    minutos_desde_a_criacao: float | None
    desfecho: str
    etapa: str
    detalhe: str


@dataclass(frozen=True)
class CicloDaConta:
    """O que aconteceu com uma conta que o igfarm criou: criada, registrada, cada contato com o app e retirada.
    Só dado que a ponte já tinha; nenhum segredo (sem senha, proxy nem IP)."""

    account_id: str
    #: `None` na conta criada no app (o igfarm não a conhece).
    igfarm_account_id: str | None
    #: Quando a conta passou a existir: a criação no igfarm, ou a confirmação do cadastro no app. `None` enquanto a conta do app
    #: ainda não foi confirmada.
    criada_em: str | None
    #: O registro da ponte (conta do igfarm) ou o planejamento da conta (conta do app); `None` quando só restam os rastros.
    registrada_em: str | None
    #: A conta segue na persona (`ativa`) ou o @ está na lápide (`retirada`, 29.23).
    estado: str
    retirada_em: str | None
    contatos: tuple[ContatoDaConta, ...] = field(default_factory=tuple)
    #: Minutos entre a criação e o PRIMEIRO contato com o app; `None` enquanto não houve contato.
    minutos_ate_o_primeiro_contato: float | None = None
    #: Desfecho do último contato; `None` sem contato.
    ultimo_desfecho: str | None = None
    #: `igfarm` (criada pela API do igfarm e registrada pela ponte) ou `app` (planejada e cadastrada no app, 31.341).
    origem: str = "igfarm"
    #: De onde contam os minutos dos contatos: `criacao` (a conta existe) ou `planejamento` (a conta do app ainda não foi confirmada).
    referencia: str = "criacao"


def minutos_entre(inicio: str | None, fim: str | None) -> float | None:
    """Minutos entre dois instantes ISO 8601 (com `Z` ou `+00:00`); `None` se algum faltar ou não for data."""
    from datetime import datetime, timezone
    try:
        a = datetime.fromisoformat(str(inicio).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(fim).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return round((b - a).total_seconds() / 60.0, 1)
