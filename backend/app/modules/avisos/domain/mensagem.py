"""O aviso fora do painel (item 28.11): o que um evento do barramento vira numa mensagem de celular.

O aviso externo é ESPELHO da caixa de Pendências (`#/pendencias`, ADR-062), não um conceito novo: só os eventos que
hoje viram pendência (mais o `pedido.aviso` do 28.9) geram mensagem. Nada de nome de persona, conta, e-mail, telefone,
IP, texto de comando ou dado de terceiro: o Telegram é um serviço de fora.

Molde (28.31, queixa do dono de 04/10 19:10Z: "mensagens genéricas e sem relevância"). Até 5 linhas:

    1. assunto e resultado, com número quando houver ("Pedido «Preço do Pi» pausado: 3 falhas seguidas");
    2. o que é crítico ("As próximas ocorrências não rodam até você retomar.");
    3. "Espera você: <o gesto>" ou "Nada a fazer.";
    4. o link.

Três níveis (`NIVEL_POR_TIPO`), e o nível DECIDE a entrega (`entrega_do_tipo`):

    1 precisa de você agora  aprovação, pergunta, conta pedindo pessoa, ocorrência incerta, convidado, lembrete de
                             vencimento (31.50): sai na hora.
    2 algo falhou            pausa e orçamento esgotado PARARAM algo do dono e saem na hora; ocorrência perdida e
                             eventos perdidos não pararam nada e vão à janela.
    3 rotina                 relatório, encerramento, 80% do orçamento, condição atendida, aprendizado: nunca saem
                             sozinhos; a fila os junta numa mensagem por janela (`JANELA_DA_ROTINA_S`), uma linha cada.

O rótulo do pedido é texto da pessoa: só sai se NENHUM filtro mudaria nada nele (`privacidade.texto_seguro`); senão,
e na falta do redator, vira "Pedido #<6 do id>" (decisão (a) da orquestradora, 04/10 19:22Z). O texto livre (a
pergunta da execução, o resumo da aprovação) passa por `privacidade.texto_livre`. A aprovação mostra o alvo, como já
mostrava (ADR-071 (d)); é a única exceção.

Domínio puro: recebe tipo, dados e id do evento como valores simples; não importa banco, rede nem `app.models`.

O item "Para aprovar" do Aprendizado entra pelo `learning.needs_person` (30.21; item 28.14): só a ENTRADA na espera
avisa, e por padrão só a faixa C (item a item); ligar a faixa B é `avisos.aprendizado_faixas`.

Rajada (28.19): vários avisos do mesmo tipo em sequência saem como UMA mensagem com a contagem e uma linha por item
(`corpo_agrupado`); a regra de quando agrupar é da fila (`fila_sql.reivindicar_um`).

Chave de deduplicação comum (28.14): `<família>:<identidade do fato>`, montada só por `chave_do_fato`. Famílias:
`approval`, `run`, `session`, `pedido` e `learning`. A identidade é a do FATO, nunca a do evento quando o fato se
repete em eventos.

Com a conversa de volta ligada (28.15, decisão (d) do ADR-071), a aprovação e a pergunta levam o CONTEÚDO, para o
dono responder ali mesmo, cortado em 500 caracteres e sem captura de tela. Sem a conversa, a mensagem diz onde
responder.

Quem fala é a ANA (28.17, decisão do dono de 03/10): todo título começa com `titulo_do_aviso`, que lê o nome da
constante de contrato (`app.contracts.identidade`). Só texto: nenhuma regra de entrega ou de resposta olha o título.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from app.contracts.identidade import NOME_DA_IA
from app.modules.avisos.domain.privacidade import texto_livre, texto_seguro

#: O começo de todo título de aviso (28.17): quem fala é a ANA, não "a Central de Aparelhos".
PREFIXO_DO_TITULO = f"{NOME_DA_IA}: "
#: A caixa que o aviso espelha. O caminho é o da rota do painel (`frontend/src/lib/rotas.ts`).
CAMINHO_DA_CAIXA = "#/pendencias"

#: Os três níveis (28.31). O número só ordena; a entrega sai de `entrega_do_tipo`.
PRECISA_DE_VOCE, ALGO_FALHOU, ROTINA = 1, 2, 3
AGORA, JANELA = "agora", "janela"
#: tipo do aviso → nível. Tipo fora do mapa (os avisos montados à mão: convidados, decisões) é nível 1: sai na hora,
#: como sempre saiu. O `decisoes.resumo` já é a mensagem de uma janela (28.25): juntá-lo à rotina perderia o conteúdo.
NIVEL_POR_TIPO: dict[str, int] = {
    "approval.pending": PRECISA_DE_VOCE,
    "run.needs_input": PRECISA_DE_VOCE,
    "session.needs_person": PRECISA_DE_VOCE,
    "pedido.aprovacao_pendente": PRECISA_DE_VOCE,
    "pedido.pergunta": PRECISA_DE_VOCE,
    "pedido.ocorrencia_incerta": PRECISA_DE_VOCE,
    "trello.comentario": PRECISA_DE_VOCE,          # 28.30: o pedido de confirmação de um comentário do dono
    "pendencia.vence_em": PRECISA_DE_VOCE,
    "portal.contato": PRECISA_DE_VOCE,             # 28.32: a mensagem de um visitante do site; sai na hora, sem rajada
    "pedido.pausa_automatica": ALGO_FALHOU,
    "pedido.orcamento_esgotado": ALGO_FALHOU,
    "pedido.ocorrencia_perdida": ALGO_FALHOU,
    "pedido.eventos_perdidos": ALGO_FALHOU,
    "pedido.orcamento_80": ROTINA,
    "pedido.relatorio_pronto": ROTINA,
    "pedido.encerramento": ROTINA,
    "pedido.condicao_atendida": ROTINA,
    "learning.needs_person": ROTINA,
    "trello.teto_de_comentarios": ROTINA,          # 28.30: o teto por hora segurou os pedidos; nada a fazer
    # 28.32: os contatos do site acima dos tetos, só contagens, um por hora. Não pede o dono (revisão do #335): acima
    # do limiar, o formulário segurou contatos que seriam dele, e sai na hora; abaixo, vai com a rotina.
    "portal.resumo": ALGO_FALHOU,
    "portal.resumo_rotina": ROTINA,
}
#: Os de nível 2 que PARARAM algo do dono: saem na hora. O resto do nível 2 vai à janela, com a rotina.
PARARAM_ALGO = frozenset({"pedido.pausa_automatica", "pedido.orcamento_esgotado", "portal.resumo"})
#: O aviso de um pedido do LOTE de uma frente (28.31 F2a): o tipo ganha este prefixo e vai sempre à janela, qualquer que
#: seja o nível, porque a prova da frente não é notícia para o dono. Duas exceções saem na hora: a aprovação, porque só o
#: dono decide (orquestradora, 04/10 01:20Z, a mesma regra das execuções de lote), e a ocorrência incerta, porque efeito
#: incerto em conta real é crítico mesmo num lote (revisão da #312, 04/10 20:27Z).
PREFIXO_DE_LOTE = "pedido.lote."
LOTE_NA_HORA = frozenset({"aprovacao_pendente", "ocorrencia_incerta"})
#: Os tipos que esperam a janela, para a fila separá-los já na consulta (a entrega é do tipo).
TIPOS_DA_JANELA: frozenset[str] = frozenset(t for t, n in NIVEL_POR_TIPO.items() if n != PRECISA_DE_VOCE
                                            and t not in PARARAM_ALGO)
#: Quanto a rotina espera, contada do aviso mais velho dela, antes de sair numa mensagem só. Fica no código (decisão
#: (f): sem chave nova de config nesta rodada).
JANELA_DA_ROTINA_S = 3600.0

#: tipo do aviso → frase curta. Texto fixo no código: nenhum campo do evento entra aqui. É também o nome do cartão do
#: espelho do Trello (`application/espelho.py`): mudar a frase muda o cartão.
ROTULOS: dict[str, str] = {
    "approval.pending": "Aprovação aguardando a sua decisão",
    "pendencia.vence_em": "Uma pendência vence em breve",
    "run.needs_input": "Uma execução parou pedindo informação",
    "session.needs_person": "Uma conta pede intervenção humana",
    "pedido.pausa_automatica": "Um pedido foi pausado automaticamente",
    "pedido.orcamento_80": "Um pedido usou 80% do orçamento",
    "pedido.orcamento_esgotado": "Um pedido esgotou o orçamento",
    "pedido.ocorrencia_perdida": "Uma ocorrência de pedido foi perdida",
    "pedido.relatorio_pronto": "Um relatório de pedido ficou pronto",
    "pedido.encerramento": "Um pedido foi encerrado",
    "pedido.aprovacao_pendente": "Um pedido espera a sua aprovação",
    "pedido.pergunta": "Um pedido tem uma pergunta para você",
    "pedido.ocorrencia_incerta": "Uma ocorrência de pedido terminou incerta",
    "learning.needs_person": "Um conhecimento aprendido espera a sua revisão",
}
#: Faixas do aprendizado que avisam fora do painel quando `avisos.aprendizado_faixas` não diz outra coisa.
FAIXAS_DO_APRENDIZADO_PADRAO: frozenset[str] = frozenset({"C"})
ROTULO_GENERICO_DE_PEDIDO = "Um pedido tem novidade"
#: O teto do conteúdo do fato na mensagem (decisão (d)): o resto fica no painel.
CONTEUDO_MAX = 500
#: O rótulo do pedido é curto: o que passa disso é cortado na palavra.
ROTULO_MAX = 60
COMO_DECIDIR = "Espera você: responda a esta mensagem com sim ou não (ou /vetar <motivo>)."
COMO_RESPONDER = "Espera você: responda a esta mensagem com a resposta."
NADA_A_FAZER = "Nada a fazer."
#: A frase fixa da conta que pede pessoa, pelo `status` da sessão (decisão (d)): o `detail` cru nunca sai.
SITUACAO_DA_CONTA: dict[str, str] = {
    "auth_challenge": "a conta pediu uma verificação (desafio)",
    "wrong_account": "o aparelho está numa conta diferente da esperada",
}
#: O motivo do encerramento (`pedidos/domain/estados.MOTIVOS_DE_ENCERRAMENTO`) dito em português.
MOTIVO_DO_ENCERRAMENTO: dict[str, str] = {
    "prazo": "chegou ao prazo final",
    "contagem": "fez todas as ocorrências pedidas",
    "orcamento": "o orçamento acabou",
    "abandonado": "ficou sem uso e foi encerrado",
    "pai": "o pedido principal foi encerrado",
}

Redigir = Callable[[str], str]


@dataclass(frozen=True)
class Aviso:
    """Uma mensagem a entregar. `chave` é a identidade do FATO: o mesmo fato nunca vira duas mensagens. `nivel` é o
    da tabela (1 a 3); a entrega a fila tira do tipo (`entrega_do_tipo`), então os avisos montados à mão não mudam."""

    chave: str
    tipo: str
    titulo: str
    corpo: str
    link: str | None
    nivel: int = PRECISA_DE_VOCE


def nivel_do_tipo(tipo: str) -> int:
    if tipo.startswith(PREFIXO_DE_LOTE):
        return ROTINA
    return NIVEL_POR_TIPO.get(tipo, PRECISA_DE_VOCE)


def entrega_do_tipo(tipo: str) -> str:
    """`agora` ou `janela`. É o tipo que decide, e não o aviso: a linha da fila guarda só o tipo."""
    if tipo.startswith(PREFIXO_DE_LOTE):
        return JANELA
    if nivel_do_tipo(tipo) == PRECISA_DE_VOCE or tipo in PARARAM_ALGO:
        return AGORA
    return JANELA


def titulo_do_aviso(frase: str) -> str:
    """O título de um aviso: o nome da IA e a frase fixa. O único lugar que monta o prefixo."""
    return PREFIXO_DO_TITULO + frase


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


def _numero(valor: object) -> float | None:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        return None
    return float(valor)


def _hora(valor: object) -> str | None:
    """"HH:MMZ" de um instante ISO em UTC ("2026-10-04T14:02:11.000Z"), ou `None`. A hora é a do relógio da Central."""
    texto = _texto(valor)
    if not texto or len(texto) < 16 or texto[10] not in "T " or texto[13] != ":":
        return None
    return f"{texto[11:16]}Z"


def chave_do_fato(familia: str, *partes: str) -> str:
    """A chave de deduplicação dos avisos (28.14): `<família>:<parte>[:<parte>…]`. Um fato, uma chave, em qualquer
    réplica; os quatro eventos que avisam (e o `learning.needs_person`) passam por aqui."""
    return ":".join((familia, *partes))


def _filho(dados: Mapping[str, object] | None, nome: str) -> Mapping[str, object]:
    valor = (dados or {}).get(nome)
    return valor if isinstance(valor, Mapping) else {}


def _cortar(texto: str, maximo: int) -> str:
    if len(texto) <= maximo:
        return texto
    corte = texto[:maximo - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (corte or texto[:maximo - 1]) + "…"


def _conteudo(partes: list[str | None], nomes: Iterable[str], redigir: Redigir) -> str | None:
    texto = "\n".join(p for p in partes if p)
    if not texto:
        return None
    return _cortar(texto_livre(texto, nomes, redigir).strip(), CONTEUDO_MAX) or None


def nome_do_pedido(aviso: Mapping[str, object], nomes: Iterable[str], redigir: Redigir | None) -> str:
    """"Pedido «<rótulo>»" ou "Pedido #<6 do id>". O rótulo só sai quando o pedido foi criado PELO DONO (o texto é dele,
    no chat dele) E passa por todos os filtros sem mudar nada. Nome de terceiro que não é persona não se detecta por
    regra; por isso o pedido de convidado, de frente (`lote:`) ou de IA sai sempre pelo id curto (decisão da
    orquestradora, 04/10 20:06Z). O pedido ainda não guarda quem o criou de forma confiável (`criado_por` vazio): até o
    28.31 F2 pôr `criado_pelo_dono` no aviso, todo pedido sai pelo id curto."""
    pelo_dono = aviso.get("criado_pelo_dono") is True
    seguro = texto_seguro(_texto(aviso.get("pedido_titulo")), nomes, redigir) if pelo_dono else None
    if seguro:
        return f"Pedido «{_cortar(seguro, ROTULO_MAX)}»"
    curto = (_texto(aviso.get("pedido_id")) or "").removeprefix("ped_")[:6]
    return f"Pedido #{curto}" if curto else "Um pedido"


def _do_pedido(sub: str, aviso: Mapping[str, object], nomes: Iterable[str],
               redigir: Redigir | None) -> tuple[str, list[str]]:
    """O assunto (1ª linha) e as linhas seguintes de cada aviso de pedido, só com o que já está no evento."""
    nome = nome_do_pedido(aviso, nomes, redigir)
    dados = _filho(aviso, "dados")
    if sub == "pausa_automatica":
        falhas = _numero(dados.get("falhas_seguidas"))
        motivo = texto_seguro(_texto(dados.get("motivo")), nomes, redigir)
        porque = f": {int(falhas)} falhas seguidas" if falhas else (f": {_cortar(motivo, 80)}" if motivo else "")
        return f"⏸️ {nome} pausado{porque}", ["As próximas ocorrências não rodam até você retomar.",
                                               "Espera você: veja a falha no painel e retome ou cancele o pedido."]
    if sub == "orcamento_80":
        gasto, total = _numero(dados.get("gasto_usd")), _numero(dados.get("orcamento_total_usd"))
        quanto = f" (US$ {gasto:.2f} de US$ {total:.2f})" if gasto is not None and total else ""
        return f"💰 {nome} usou 80% do orçamento{quanto}", [
            "Nada a fazer; para ir além do teto, suba o orçamento no pedido."]
    if sub == "orcamento_esgotado":
        return f"💰 {nome} encerrado: o orçamento acabou", [
            "Não roda mais ocorrências.", "Nada a fazer, a não ser que queira continuar: aí, crie o pedido de novo."]
    if sub == "ocorrencia_perdida":
        quando = _texto(dados.get("previsto_para"))
        return f"⏭️ {nome}: a ocorrência {'de ' + quando + ' ' if quando else ''}não rodou", [
            "Nada a fazer: as próximas seguem a agenda."]
    if sub == "relatorio_pronto":
        seq = _numero(dados.get("sequencia"))
        final = " final" if _texto(dados.get("gatilho")) == "encerramento" else ""
        # 28.10 F4: o relatório consolidado de um pai diz SÓ quantos conflitos há; o conteúdo e os filhos ficam no painel.
        conflitos = _numero(dados.get("conflitos"))
        extra = f": {int(conflitos)} conflito(s) entre os filhos" if conflitos else ""
        return f"📄 {nome}: relatório{final}{' ' + str(int(seq)) if seq else ''} pronto{extra}", [NADA_A_FAZER]
    if sub == "encerramento":
        motivo = MOTIVO_DO_ENCERRAMENTO.get(_texto(dados.get("motivo")) or "", "foi encerrado")
        return f"✅ {nome} encerrado: {motivo}", [NADA_A_FAZER]
    if sub == "aprovacao_pendente":
        return f"🙋 {nome} espera a sua aprovação", ["O pedido não segue sem a sua decisão.",
                                                      "Espera você: decida na caixa de Pendências."]
    if sub == "pergunta":
        return f"❓ {nome} tem uma pergunta para você", ["O pedido não segue sem a sua resposta.",
                                                          "Espera você: responda na caixa de Pendências."]
    if sub == "ocorrencia_incerta":
        tentativa = _numero(dados.get("tentativa"))
        return (f"⚠️ {nome}: uma ocorrência terminou incerta"
                f"{f' (tentativa {int(tentativa)})' if tentativa else ''}"), [
            "O pedido fica parado até você resolver.",
            "Espera você: confira no painel se o efeito aconteceu e resolva na caixa de Pendências."]
    if sub == "eventos_perdidos":
        return f"⚠️ {nome}: parte dos eventos acompanhados foi apagada antes de ser lida", [
            "Nada foi disparado por eles. Nada a fazer."]
    if sub == "condicao_atendida":
        return f"🔔 {nome}: a condição acompanhada foi atendida", [NADA_A_FAZER]
    return f"{nome} tem novidade", ["Veja no painel."]


def aviso_de_evento(kind: str, dados: Mapping[str, object] | None, evento_id: int | None,
                    url_painel: str | None = None,
                    faixas_do_aprendizado: frozenset[str] = FAIXAS_DO_APRENDIZADO_PADRAO,
                    redigir: Redigir | None = None, *, nomes: Sequence[str] = (),
                    conversa: bool | None = None) -> Aviso | None:
    """O aviso que o evento merece, ou `None` quando ele não é espelho da caixa de Pendências.

    A chave é por FATO e não por evento, onde o fato se repete em eventos (`run.updated` sai a cada mudança da
    execução; uma só vez ela PAROU pedindo informação). Onde o evento é o próprio fato (`session.needs_person` já só
    sai na transição), a chave é o id do evento, que é o mesmo nas duas réplicas.

    `redigir` e `nomes` são os filtros (28.31): sem o redator, o rótulo do pedido vira a reserva. `conversa` (a
    conversa de volta ligada) põe no corpo o conteúdo da aprovação e da pergunta; omitida, vale "há redator", o
    contrato de antes.
    """
    if conversa is None:
        conversa = redigir is not None
    link = link_da_caixa(url_painel)
    linhas: list[str]
    assunto: str
    tipo: str
    chave: str
    if kind == "approval.pending":
        a = _filho(dados, "approval")
        ident = _texto(a.get("id")) or (f"evento-{evento_id}" if evento_id else None)
        if ident is None:
            return None
        tipo, chave = "approval.pending", chave_do_fato("approval", ident)
        assunto = "🔒 " + ROTULOS[tipo]
        linhas = ["A etapa não segue sem a sua decisão.", "Espera você: decida na caixa de Pendências."]
        if conversa and redigir is not None:
            # A exceção do alvo (ADR-071 (d)): ele sai redigido, mas sem o filtro de contato e de persona.
            alvo, texto = _texto(a.get("target")), _texto(a.get("content"))
            resumo = _conteudo([_texto(a.get("summary"))], nomes, redigir)
            detalhe = " · ".join(x for x in (f"Alvo: {redigir(alvo).strip()}" if alvo else None,
                                             f"Texto: “{_conteudo([texto], nomes, redigir)}”" if texto else None) if x)
            linhas = [x for x in (resumo, _cortar(detalhe, CONTEUDO_MAX) if detalhe else None) if x] + [COMO_DECIDIR]
    elif kind == "run.updated":
        run = _filho(dados, "run")
        ident = _texto(run.get("id"))
        if run.get("status") != "needs_input" or ident is None:
            return None
        tipo, chave = "run.needs_input", chave_do_fato("run", ident, "needs_input")
        # A execução se diz pela hora e pelo aparelho (decisão da orquestradora, 04/10 20:04Z): o id não diz nada ao
        # dono, e o comando nunca sai. Sem hora legível, "Uma execução".
        lista = run.get("instance_ids")
        aparelhos = [x for x in lista if isinstance(x, str)] if isinstance(lista, list) else []
        onde = f" no {aparelhos[0]}" if len(aparelhos) == 1 else ""
        hora = _hora(run.get("started_at")) or _hora(run.get("created_at"))
        assunto = f"❓ {f'A execução das {hora}' if hora else 'Uma execução'}{onde} parou com uma pergunta"
        linhas = ["Ela não segue sem a sua resposta.", "Espera você: responda na caixa de Pendências."]
        if conversa and redigir is not None:
            pergunta = _conteudo([_texto(run.get("status_detail"))], nomes, redigir)
            linhas = ([f"Pergunta: {pergunta}"] if pergunta else []) + [COMO_RESPONDER]
    elif kind == "session.needs_person":
        # `active` falso é o "deixou de precisar": não é pendência nova.
        if not (dados or {}).get("active") or not evento_id:
            return None
        tipo, chave = "session.needs_person", chave_do_fato("session", str(evento_id))
        d = dados or {}
        aparelho = _texto(d.get("instance_id"))
        situacao = SITUACAO_DA_CONTA.get(_texto(d.get("status")) or "", "uma conta pede intervenção humana")
        assunto = f"🔐 {aparelho}: {situacao}" if aparelho else f"🔐 {situacao[0].upper()}{situacao[1:]}"
        linhas = ["Nada é tentado na tela até alguém resolver.", "Espera você: resolva no aparelho pelo painel."]
    elif kind == "pedido.aviso":
        aviso = _filho(dados, "aviso")
        ident = _texto(aviso.get("id")) or (f"evento-{evento_id}" if evento_id else None)
        if ident is None:
            return None
        sub = _texto(aviso.get("tipo")) or "desconhecido"
        de_lote = aviso.get("de_lote") is True and sub not in LOTE_NA_HORA
        tipo, chave = (f"{PREFIXO_DE_LOTE}{sub}" if de_lote else f"pedido.{sub}"), chave_do_fato("pedido", ident)
        assunto, linhas = _do_pedido(sub, aviso, nomes, redigir)
        # Aviso que NÃO pede pessoa não é pendência: não há o que abrir na caixa, então a mensagem vai sem link.
        if not aviso.get("requer_pessoa"):
            link = None
    elif kind == "pendencia.vence_em":
        # 31.50 (Jev): o lembrete 2 h antes de vencer. A chave é a do produtor (`vencimento:lembrete:<id>:<entrada na
        # espera>`): uma por espera. Diz o que vence, onde e quando, e o que acontece; nunca o comando nem o título da
        # etapa (o produtor nem os manda). A `acao` é a capability da etapa (`SEND_MESSAGE`); o serviço põe ao lado o
        # NOME dela no catálogo (`acao_nome`), que é o que sai. A `etapa` não é usada.
        d = dados or {}
        chave_do_produtor = _texto(d.get("chave"))
        if chave_do_produtor is None:
            return None
        tipo, chave = "pendencia.vence_em", chave_do_produtor
        o_que = _texto(d.get("o_que")) or ""
        sujeito = SUJEITO_DO_VENCIMENTO.get(o_que, "Uma pendência")
        lista = d.get("aparelhos")
        aparelhos = [x for x in lista if isinstance(x, str)] if isinstance(lista, list) else []
        aparelho = _texto(d.get("aparelho")) or (aparelhos[0] if len(aparelhos) == 1 else None)
        acao = _texto(d.get("acao"))
        nome_da_acao = texto_seguro(_texto(d.get("acao_nome")), nomes, redigir) if redigir is not None else None
        etapa = nome_da_acao or (acao if acao and _CHAVE_DE_CATALOGO.match(acao) else None)
        hora = _hora(d.get("vence_em"))
        # O tempo relativo na frente (orquestradora, 20:47Z): só "22:30Z" pode ser lido como hora local. O produtor
        # avisa entre 2 h e 1 h 50 antes (volta de 10 min), por isso "em até".
        assunto = (f"⏳ {sujeito}{f' no {aparelho}' if aparelho else ''} vence em até 2 h"
                   f"{f' ({hora})' if hora else ''}")
        acontece = _texto(d.get("acontece_se_vencer")) or "cancelado pelo sistema"
        linhas = [x for x in (
            f"Etapa que espera: {etapa}." if etapa else None,
            f"Se vencer: {acontece}.",
            "Espera você: decida na caixa de Pendências antes disso." if o_que == "aprovacao" else
            "Espera você: responda na caixa de Pendências antes disso.") if x]
    elif kind == "learning.needs_person":
        # Só a ENTRADA na espera é notícia: a saída (`aguardando` falso) não manda nada, e uma saída sem a entrada
        # correspondente (reinício do processo do Livro) é no-op. `desde` é a hora da transição e se repete na
        # reemissão da mesma espera, então a mesma espera nunca vira duas mensagens e uma reentrada vira outra.
        d = dados or {}
        partes = [_texto(d.get(c)) for c in ("kind", "ref", "desde")]
        if d.get("aguardando") is not True or _texto(d.get("faixa")) not in faixas_do_aprendizado or None in partes:
            return None
        tipo, chave = "learning.needs_person", chave_do_fato("learning", *[x for x in partes if x is not None])
        assunto = "📚 " + ROTULOS[tipo]
        linhas = ["Espera você, sem pressa: revise na aba Aprendizado."]
    else:
        return None
    return Aviso(chave=chave, tipo=tipo, titulo=titulo_do_aviso(assunto), corpo="\n".join(linhas), link=link,
                 nivel=nivel_do_tipo(tipo))


#: 31.50: o que vence, pelo `o_que` do lembrete.
SUJEITO_DO_VENCIMENTO = {"aprovacao": "A aprovação", "objetivo": "O objetivo parado", "execucao": "A pergunta da execução"}
#: A capability que sai no lembrete quando o catálogo não tem nome para ela: só a chave (`SEND_MESSAGE`, como o produtor
#: manda `steps.capability`), nunca texto livre.
_CHAVE_DE_CATALOGO = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{1,60}$")


#: O aviso AGRUPADO (28.19): uma rajada do mesmo tipo vira uma mensagem com a contagem. Texto fixo, sem dado do fato;
#: `{n}` é o único campo. Tipo sem plural próprio cai no genérico.
ROTULOS_AGRUPADOS: dict[str, str] = {
    "approval.pending": "{n} aprovações aguardando a sua decisão",
    "run.needs_input": "{n} execuções pararam pedindo informação",
    "session.needs_person": "{n} contas pedem intervenção humana",
    "learning.needs_person": "{n} conhecimentos aprendidos esperam a sua revisão",
    "pendencia.vence_em": "{n} pendências vencem nas próximas 2 h",
}
ROTULO_AGRUPADO_DE_PEDIDO = "{n} novidades de pedidos"
ROTULO_AGRUPADO_GENERICO = "{n} avisos seguidos do mesmo tipo"
#: O agrupado não aceita resposta por reply: quem decide abre a caixa. Sem itens, é o corpo inteiro.
CORPO_AGRUPADO = "Chegaram em sequência. Abra a caixa de Pendências do painel para ver cada um."
#: Quantos itens a mensagem agrupada lista, um por linha (28.31); o resto vira "+N no painel".
ITENS_NO_AGRUPADO = 5
ITENS_NA_ROTINA = 8
#: A família do fato de uma mensagem agrupada em `canal_enviadas`: reply a ela não responde a nenhum fato.
FAMILIA_DO_GRUPO = "grupo"
#: O tipo da mensagem da janela de rotina (28.31): grava `grupo:rotina` em `canal_enviadas`.
TIPO_DA_ROTINA = "rotina"


def titulo_agrupado(tipo: str, n: int) -> str:
    modelo = ROTULOS_AGRUPADOS.get(tipo) or (ROTULO_AGRUPADO_DE_PEDIDO if tipo.startswith("pedido.")
                                             else ROTULO_AGRUPADO_GENERICO)
    return titulo_do_aviso(modelo.format(n=n))


def _itens(titulos: Sequence[str], maximo: int) -> list[str]:
    linhas = ["• " + t.removeprefix(PREFIXO_DO_TITULO) for t in titulos[:maximo]]
    if len(titulos) > maximo:
        linhas.append(f"+{len(titulos) - maximo} no painel")
    return linhas


def corpo_agrupado(titulos: Sequence[str]) -> str:
    """Uma linha por item (o assunto de cada aviso, que já passou pelos filtros), até `ITENS_NO_AGRUPADO`, e o gesto."""
    if not titulos:
        return CORPO_AGRUPADO
    return "\n".join([*_itens(titulos, ITENS_NO_AGRUPADO),
                      "Espera você: abra a caixa de Pendências do painel para responder a cada um."])


def titulo_da_rotina(n: int) -> str:
    return titulo_do_aviso(f"📋 Rotina: {n} novidades desde a última mensagem")


def corpo_da_rotina(titulos: Sequence[str]) -> str:
    """A janela de rotina (nível 3 e o nível 2 que não parou nada): um item por linha e nenhum gesto obrigatório."""
    return "\n".join([*_itens(titulos, ITENS_NA_ROTINA), "Nada urgente: é rotina."])


def texto_da_mensagem(titulo: str, corpo: str, link: str | None) -> str:
    """O texto que vai ao serviço de mensagens: título, corpo (se houver) e link (se houver), um por linha."""
    return "\n".join(parte for parte in (titulo, corpo, link) if parte)
