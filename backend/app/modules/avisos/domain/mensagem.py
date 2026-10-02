"""O aviso fora do painel (item 28.11): o que um evento do barramento vira numa mensagem de celular.

O aviso externo é ESPELHO da caixa de Pendências (`#/pendencias`, ADR-062), não um conceito novo: só os eventos que
hoje viram pendência (mais o `pedido.aviso` do 28.9) geram mensagem. E a mensagem é a MENOR possível: o TIPO do
fato e o link da caixa. Nada de nome de persona, conta, trecho de mensagem ou dado de terceiro — o Telegram é um
serviço de fora, e o que o dono precisa saber é "tem coisa esperando você", não o conteúdo. Quem quer o detalhe
abre o link.

Domínio puro: recebe tipo, dados e id do evento como valores simples; não importa banco, rede nem `app.models`.

O item "Para aprovar" do Aprendizado entra pelo `learning.needs_person` (30.21; item 28.14): só a ENTRADA na espera
avisa, e por padrão só a faixa C (item a item). A faixa B é aprovação em lote e fica na caixa, para não virar um aviso
por receita; ligá-la é `avisos.aprendizado_faixas`.

Chave de deduplicação comum (28.14): `<família>:<identidade do fato>`, montada só por `chave_do_fato`. Famílias:
`approval`, `run`, `session`, `pedido` e `learning`. A identidade é a do FATO, nunca a do evento quando o fato se
repete em eventos.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: A caixa que o aviso espelha. O caminho é o da rota do painel (`frontend/src/lib/rotas.ts`).
CAMINHO_DA_CAIXA = "#/pendencias"

#: tipo do aviso → frase curta. Texto fixo no código: nenhum campo do evento entra aqui.
ROTULOS: dict[str, str] = {
    "approval.pending": "Aprovação aguardando a sua decisão",
    "run.needs_input": "Uma execução parou pedindo informação",
    "session.needs_person": "Uma conta pede intervenção humana",
    # `pedido.aviso` (28.9): o `tipo` do aviso do pedido escolhe a frase. Tipo desconhecido cai no genérico.
    "pedido.pausa_automatica": "Um pedido foi pausado automaticamente",
    "pedido.orcamento_80": "Um pedido usou 80% do orçamento",
    "pedido.orcamento_esgotado": "Um pedido esgotou o orçamento",
    "pedido.ocorrencia_perdida": "Uma ocorrência de pedido foi perdida",
    "pedido.relatorio_pronto": "Um relatório de pedido ficou pronto",
    "pedido.encerramento": "Um pedido foi encerrado",
    "pedido.aprovacao_pendente": "Um pedido espera a sua aprovação",
    "pedido.pergunta": "Um pedido tem uma pergunta para você",
    "pedido.ocorrencia_incerta": "Uma ocorrência de pedido terminou incerta",
    # 28.14: o Livro pôs um conhecimento na espera da pessoa. Nada de kind, ref, app ou motivo na mensagem.
    "learning.needs_person": "Um conhecimento aprendido espera a sua revisão",
}
#: Faixas do aprendizado que avisam fora do painel quando `avisos.aprendizado_faixas` não diz outra coisa.
FAIXAS_DO_APRENDIZADO_PADRAO: frozenset[str] = frozenset({"C"})
ROTULO_GENERICO_DE_PEDIDO = "Um pedido tem novidade"
#: O que cada aviso diz depois do título: uma instrução, nunca um dado.
CORPO_PADRAO = "Abra a caixa de Pendências do painel para ver."


@dataclass(frozen=True)
class Aviso:
    """Uma mensagem a entregar. `chave` é a identidade do FATO: o mesmo fato nunca vira duas mensagens."""

    chave: str
    tipo: str
    titulo: str
    corpo: str
    link: str | None


def link_da_caixa(url_painel: str | None) -> str | None:
    """`<base>/#/pendencias`, ou `None` sem base pública configurada (aí a mensagem leva só o texto).

    Só `http(s)://` vale: a base vem de configuração e vai para um serviço de fora; outro esquema é erro de digitação
    ou pior, e a mensagem sai sem link em vez de com um link torto.
    """
    base = (url_painel or "").strip()
    if not base.lower().startswith(("http://", "https://")):
        return None
    return base.rstrip("/") + "/" + CAMINHO_DA_CAIXA


def _texto(valor: object) -> str | None:
    """Id curto e seguro para compor a chave: só texto ou inteiro, nunca estrutura."""
    if isinstance(valor, bool) or not isinstance(valor, (str, int)):
        return None
    texto = str(valor).strip()
    return texto or None


def chave_do_fato(familia: str, *partes: str) -> str:
    """A chave de deduplicação dos avisos (28.14): `<família>:<parte>[:<parte>…]`. Um fato, uma chave, em qualquer
    réplica; os quatro eventos que avisam (e o `learning.needs_person`) passam por aqui."""
    return ":".join((familia, *partes))


def _filho(dados: Mapping[str, object] | None, nome: str) -> Mapping[str, object]:
    valor = (dados or {}).get(nome)
    return valor if isinstance(valor, Mapping) else {}


def aviso_de_evento(kind: str, dados: Mapping[str, object] | None, evento_id: int | None,
                    url_painel: str | None = None,
                    faixas_do_aprendizado: frozenset[str] = FAIXAS_DO_APRENDIZADO_PADRAO) -> Aviso | None:
    """O aviso que o evento merece, ou `None` quando ele não é espelho da caixa de Pendências.

    A chave é por FATO e não por evento, onde o fato se repete em eventos (`run.updated` sai a cada mudança da
    execução; uma só vez ela PAROU pedindo informação). Onde o evento é o próprio fato (`session.needs_person` já só
    sai na transição), a chave é o id do evento, que é o mesmo nas duas réplicas.
    """
    link = link_da_caixa(url_painel)
    pendencia = True
    tipo: str
    chave: str
    if kind == "approval.pending":
        ident = _texto(_filho(dados, "approval").get("id")) or (f"evento-{evento_id}" if evento_id else None)
        if ident is None:
            return None
        tipo, chave = "approval.pending", chave_do_fato("approval", ident)
    elif kind == "run.updated":
        run = _filho(dados, "run")
        ident = _texto(run.get("id"))
        if run.get("status") != "needs_input" or ident is None:
            return None
        tipo, chave = "run.needs_input", chave_do_fato("run", ident, "needs_input")
    elif kind == "session.needs_person":
        # `active` falso é o "deixou de precisar": não é pendência nova.
        if not (dados or {}).get("active") or not evento_id:
            return None
        tipo, chave = "session.needs_person", chave_do_fato("session", str(evento_id))
    elif kind == "pedido.aviso":
        aviso = _filho(dados, "aviso")
        ident = _texto(aviso.get("id")) or (f"evento-{evento_id}" if evento_id else None)
        if ident is None:
            return None
        sub = _texto(aviso.get("tipo")) or "desconhecido"
        tipo, chave = f"pedido.{sub}", chave_do_fato("pedido", ident)
        # Aviso que NÃO pede pessoa não é pendência: não há o que abrir na caixa, então a mensagem vai sem link.
        if not aviso.get("requer_pessoa"):
            link, pendencia = None, False
    elif kind == "learning.needs_person":
        # Só a ENTRADA na espera é notícia: a saída (`aguardando` falso) não manda nada, e uma saída sem a entrada
        # correspondente (reinício do processo do Livro) é no-op. `desde` é a hora da transição e se repete na
        # reemissão da mesma espera, então a mesma espera nunca vira duas mensagens e uma reentrada vira outra.
        d = dados or {}
        partes = [_texto(d.get(c)) for c in ("kind", "ref", "desde")]
        if d.get("aguardando") is not True or _texto(d.get("faixa")) not in faixas_do_aprendizado or None in partes:
            return None
        tipo, chave = "learning.needs_person", chave_do_fato("learning", *[x for x in partes if x is not None])
    else:
        return None
    return Aviso(chave=chave, tipo=tipo, titulo="Central de Aparelhos: " + ROTULOS.get(tipo, ROTULO_GENERICO_DE_PEDIDO),
                 corpo=CORPO_PADRAO if pendencia else "", link=link)


def texto_da_mensagem(titulo: str, corpo: str, link: str | None) -> str:
    """O texto que vai ao serviço de mensagens: título, corpo (se houver) e link (se houver), um por linha."""
    return "\n".join(parte for parte in (titulo, corpo, link) if parte)
