"""O que o Trello mostra de cada fato da Central (item 32.2, passo 3; `docs/design/trello-integracao.md`, §2 e §7.4).

O Trello é um servidor de terceiros, então o cartão leva a MENOR coisa possível: o TIPO do fato (a frase fixa de
`ROTULOS`), o id curto, o estado e o link do painel. NENHUM texto de origem entra aqui: nem o resumo de uma aprovação,
nem a pergunta de uma execução, nem o título de um pedido, onde moram nome de persona, @conta, e-mail e IP. A redação
central (`security.redaction`) só mascara segredo e não toca nesses quatro, então a garantia é a de não copiar o texto,
não a de filtrá-lo. Os builders recebem só ids, estados e números.

Puro: sem banco, rede nem relógio (a hora vem de fora).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.contracts.identidade import NOME_DA_IA
from app.modules.avisos.application.entrada import sufixo
from app.modules.avisos.domain.mensagem import ROTULOS, chave_do_fato

#: As famílias que o reconciliador mantém e ARQUIVA quando o fato some. `deploy` e `custo` ficam de fora: são histórico.
FAMILIAS_ESPELHADAS = ("approval", "run", "pedido", "livro")
PARA_LEIGO = "**Para quem não é técnico:**"
TECNICO = "**Técnico:**"
PREFIXO_DA_MARCA = "🤖 chave: "
#: O começo fixo do comentário da Central (a hora vem depois): o leitor ignora o comentário que começa assim.
PREFIXO_DA_IA = f"🤖 {NOME_DA_IA} ·"

#: Frase de desfecho por família, para o comentário que antecede o arquivamento. Fixa: o desfecho real está no painel.
DESFECHO = {"approval": "a aprovação foi decidida ou perdeu a validade",
            "run": "a execução saiu da espera",
            "pedido": "o pedido saiu do estado que pedia atenção",
            "livro": "a validação do conhecimento terminou"}
_PEDIDO = {"ativo": ("Pedido ativo", "Um pedido persistente está ativo e a Central o executa nos horários dele."),
           "pausado": ("Pedido pausado", "Um pedido persistente está pausado e não roda até alguém retomá-lo."),
           "aguardando_pessoa": ("Pedido espera você",
                                 "Um pedido persistente parou e espera uma decisão ou uma resposta sua.")}
_LIVRO = {"pendente": ("Conhecimento na fila de validação",
                       "Um conhecimento aprendido espera a execução que vai validá-lo."),
          "rodando": ("Conhecimento em validação", "Uma execução está validando um conhecimento aprendido.")}
#: O `:` fica: o item do Aprendizado é `<kind>:<ref>` (`#/aprendizado?...&item=fluxo:f1`).
_SEGURO = re.compile(r"[^A-Za-z0-9._:-]")


def hash_do_conteudo(nome: str, descricao: str) -> str:
    """A impressão do que o cartão mostra: igual = nada a atualizar. Vale para o desejado e para o cartão adotado."""
    return hashlib.sha256(f"{nome}\n{descricao}".encode()).hexdigest()


def marca_da_chave(chave: str) -> str:
    """A última linha da descrição de todo cartão do espelho: fixa, sem dado sensível (a chave é `<família>:<id>`). É o
    que deixa o espelho reconhecer o próprio cartão quando o Trello gravou e o banco não (adoção, em vez de duplicar)."""
    return f"{PREFIXO_DA_MARCA}{chave}"


def chave_da_marca(descricao: str) -> str | None:
    """A chave na última linha da descrição, ou `None` (cartão feito por pessoa, ou com a marca editada)."""
    linhas = (descricao or "").strip().splitlines()
    ultima = linhas[-1].strip() if linhas else ""
    if not ultima.startswith(PREFIXO_DA_MARCA):
        return None
    chave = ultima[len(PREFIXO_DA_MARCA):]
    return chave if chave and " " not in chave and ":" in chave else None


@dataclass(frozen=True)
class Fato:
    """O cartão desejado de um fato. `chave` = `<família>:<fato>`; o `hash` só muda quando o conteúdo muda."""

    chave: str
    nome: str
    descricao: str

    @property
    def familia(self) -> str:
        return self.chave.split(":", 1)[0]

    @property
    def hash(self) -> str:
        return hash_do_conteudo(self.nome, self.descricao)


class FontesDoEspelho(Protocol):
    """O que o espelho lê dos módulos donos (pedidos persistentes e Livro), pelas portas PÚBLICAS deles. Quem liga é o
    `state.py` (raiz de composição): o `avisos` não importa `pedidos` nem `learning`. Só ids e estados saem daqui."""

    def pedidos_abertos(self) -> list[tuple[str, str]]: ...                # (id, estado) em ativo, pausado, aguardando_pessoa
    def livro_em_validacao(self) -> list[tuple[str, str, str]]: ...        # (id, item_ref, estado) em pendente, rodando


@dataclass(frozen=True)
class LinhaDeCusto:
    """Uma conta de IA: o nome técnico dela (`anthropic`), o gasto das últimas 24 h em US$ e o saldo estimado."""

    conta: str
    gasto_usd: float
    saldo: float | None
    moeda: str
    estado: str


def link_do_painel(url_painel: str | None, caminho: str) -> str | None:
    """`<base>/<caminho>` só com base `http(s)://` (a mesma regra do aviso do Telegram); senão o cartão vai sem link."""
    base = (url_painel or "").strip()
    if not base.lower().startswith(("http://", "https://")):
        return None
    return f"{base.rstrip('/')}/{caminho}"


def _seguro(valor: str) -> str:
    return _SEGURO.sub("_", valor.strip())[:80]


def _descricao(leigo: str, tecnico: list[str], link: str | None, chave: str) -> str:
    partes = [*tecnico, f"painel: {link}"] if link else tecnico
    return (f"{PARA_LEIGO} {leigo}\n\n{TECNICO} " + " · ".join(partes)
            + f"\n\n{marca_da_chave(chave)}")


def fato_de_pendencia(tipo: str, ident: str, url_painel: str | None) -> Fato | None:
    """`aprovacao` e `pergunta` são as duas pendências de `PortasReais.pendencias()`. Tipo desconhecido: `None`."""
    if tipo == "aprovacao":
        aviso, chave, link = "approval.pending", chave_do_fato("approval", ident), link_do_painel(url_painel, "#/pendencias")
        estado = "pending"
    elif tipo == "pergunta":
        aviso, chave = "run.needs_input", chave_do_fato("run", ident, "needs_input")
        link, estado = link_do_painel(url_painel, f"#/execucoes/{_seguro(ident)}"), "needs_input"
    else:
        return None
    rotulo = ROTULOS[aviso]
    return Fato(chave, f"{rotulo} · {sufixo(ident)}",
                _descricao(f"{rotulo}. Abra o painel para ver o que é e responder.",
                           [f"tipo `{aviso}`", f"estado `{estado}`", f"id `{sufixo(ident)}`"], link, chave))


def fato_de_pedido(ident: str, estado: str, url_painel: str | None) -> Fato | None:
    if estado not in _PEDIDO:
        return None
    titulo, leigo = _PEDIDO[estado]
    return Fato(chave_do_fato("pedido", ident), f"{titulo} · {sufixo(ident)}",
                _descricao(leigo, ["tipo `pedido`", f"estado `{estado}`", f"id `{sufixo(ident)}`"],
                           link_do_painel(url_painel, f"#/pedidos/{_seguro(ident)}"), chave_do_fato("pedido", ident)))


def fato_de_validacao(ident: str, item_ref: str, estado: str, url_painel: str | None) -> Fato | None:
    """Um pedido de validação do Livro (082) vivo. O `item_ref` é id ('receita:<id>', 'fluxo:<id>'), nunca texto."""
    if estado not in _LIVRO:
        return None
    titulo, leigo = _LIVRO[estado]
    return Fato(chave_do_fato("livro", ident), f"{titulo} · {sufixo(ident)}",
                _descricao(leigo, ["tipo `livro.validacao`", f"estado `{estado}`", f"id `{sufixo(ident)}`"],
                           link_do_painel(url_painel, f"#/aprendizado?aba=aprendido&item={_seguro(item_ref)}"),
                           chave_do_fato("livro", ident)))


def fato_de_deploy(commit: str, migracao: str | None) -> Fato:
    """O marco de um deploy: um cartão por (commit, migração)."""
    curto, mig = _seguro(commit)[:8], _seguro(migracao or "") or "desconhecida"
    return Fato(chave_do_fato("deploy", curto, mig), f"Deploy · {curto}",
                _descricao("A Central foi atualizada para uma versão nova.",
                           ["tipo `deploy`", f"commit `{curto}`", f"migração `{mig}`"], None,
                           chave_do_fato("deploy", curto, mig)))


def fato_de_custo(dia: str, contas: list[LinhaDeCusto]) -> Fato:
    """O custo de IA do dia por conta. O nome da conta (`anthropic`, `openai`, `google`) é rótulo técnico, não pessoa."""
    linhas = [f"{c.conta}: gasto 24 h US$ {c.gasto_usd:.4f}"
              + (f", saldo estimado {c.saldo:.2f} {_seguro(c.moeda)} ({_seguro(c.estado)})" if c.saldo is not None else "")
              for c in sorted(contas, key=lambda c: c.conta)]
    return Fato(chave_do_fato("custo", dia), f"Custo de IA · {dia}",
                _descricao("Quanto a IA custou nas últimas 24 horas, por conta, e quanto sobra em cada uma.",
                           linhas or ["sem conta de IA em uso"], None, chave_do_fato("custo", dia)))


def prefixo_da_ia(agora: datetime) -> str:
    """O começo de TODA escrita da Central no Trello: `🤖 ANA · HH:MMZ · `. Uma constante só, para o espelho e a saída do
    leitor não divergirem, e para o leitor reconhecer (e ignorar) o que a própria Central escreveu."""
    return f"{PREFIXO_DA_IA} {agora:%H:%M}Z · "


def comentario_de_desfecho(familia: str, agora: datetime) -> str:
    return f"{prefixo_da_ia(agora)}resolvido: {DESFECHO.get(familia, 'o fato deixou de pedir atenção')}"
