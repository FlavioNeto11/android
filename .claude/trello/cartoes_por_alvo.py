"""28.76: um cartão por ALVO (persona) da operação da prova de 07/10, no quadro Execução, com o estágio lido da central.

    backend/.venv/Scripts/python.exe .claude/trello/cartoes_por_alvo.py --operacao op-... [--operacao op-...] [--aplicar]
    backend/.venv/Scripts/python.exe .claude/trello/cartoes_por_alvo.py --arquivo operacao.json [--aplicar]
    backend/.venv/Scripts/python.exe .claude/trello/cartoes_por_alvo.py --operacao op-... --laco --intervalo-s 120

Modos:
  - padrão: ENSAIO de UM ciclo. Lê a(s) operação(ões) e os cartões (só GET), imprime o que faria e não escreve nada;
  - `--aplicar`: um ciclo que grava no Trello;
  - `--laco`: repete `--aplicar` até TODAS as operações pedidas estarem encerradas no mesmo ciclo (status final, sem erro de
    leitura nem de gravação), roda UM ciclo a mais (a confirmação: deve ter 0 ações) e sai. O intervalo mínimo é 120 s
    (`--intervalo-s`; valor menor é recusado antes de qualquer rede): `GET /api/operacoes/<id>` GRAVA ao ler (anota os
    estágios e pode fechar a operação), então o laço lê só as operações pedidas e no máximo a cada 120 s.
  `--laco` não vale com `--arquivo` (o arquivo não muda).

Fonte: `GET /api/operacoes/<id>` da central (loopback, sem credencial), no formato de `ServicoDeOperacoes.ler`
(`backend/app/modules/operacoes`): `status`, `finished_at`, `acao_final` e `alvos[]` com `estagio`, `estado`, `motivo`,
`parou_em`, `estagios[{estagio, em}]`, `instance_id` e `resultado.acao_final{tipo, verificada}`. NÃO depende do relatório
consolidado (corte 60). `--arquivo` aceita o mesmo JSON salvo (um ou mais `--arquivo`).

Um cartão por alvo, achado pela linha `Alvo da operação: <op-id>/P03` na descrição (a chave de idempotência, como `Aparelho:`
no 28.69), só nas listas Em execução, Em validação e Concluído do quadro Execução (cartão de item do plano, com ID no nome, e
as listas "Prova 07/10" nunca são tocados):
  - o alvo anda (pendente ou em curso) -> Em execução;
  - o alvo parou (bloqueado ou cancelado) ou concluiu SEM a ação verificada (a ação foi só preparada e espera a liberação)
    -> Em validação, com o motivo em vocabulário fixo;
  - o alvo concluiu com a ação VERIFICADA -> Concluído.
Nome: `Prova 07/10 · P03 · <estágio em palavras> (op-xxxxxx)`. Descrição: a chave, a situação, o estágio, a hora UTC da última
mudança (a do último estágio alcançado, nunca a de agora: a segunda rodada sem mudança tem 0 ações), o motivo e o próximo
passo quando parou, a ação final, se foi verificada (sim, não, não conferida, sem ação) e o aparelho como rótulo
`android-NN` (linha `Aparelho usado:`, e não `Aparelho:`, que é a chave do 28.69 e do `auditoria_dos_quadros`).

A PERSONA é sempre o rótulo `P01`, `P02`...: a posição do alvo na lista `alvos` da operação (o servidor a ordena por
`seq, profile_id`, fixos desde a criação, então o rótulo é estável DENTRO da operação; entre operações ele não identifica a
mesma pessoa). Nunca entram: nome, handle, id de persona, conta, execução, e-mail, telefone, IP, serial, comando, assunto,
fontes, parâmetros, texto de comentário, legenda ou DM, custo nem o texto livre do motivo (só o vocabulário fixo; o que o
dicionário não conhece vira "motivo não classificado"). Defesa em profundidade: nome e descrição passam por `_sem_contato`
e `redigir`.

Idempotente e sem duplicata: nome, descrição e lista só são gravados quando mudam. Chave com mais de um cartão: nada é
tocado, e é contado em `duplicados`. NUNCA comenta em cartão (o comentário entra como "do dono"): só nome, descrição e lista.
Só se mexe em cartão de alvo das operações PEDIDAS; um cartão de outra operação nunca é concluído por não estar na leitura.
Leitura que falha (de uma operação, ou dos cartões) pula o que não leu e nunca mexe em cartão por engano; num laço, não
derruba o ciclo seguinte. Alvo com `estado` desconhecido é pulado e contado em `falhas`.

Última linha impressa (a que a Canais cola no relato):
    operações <n> (encerradas <n>); alvos <n>; criar|criados <n>; atualizar|atualizados <n>; movidos <n>; duplicados <n>; falhas <n|nenhuma>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent / "canais"))

import redacao  # noqa: E402
from cartoes_de_aparelho import (  # noqa: E402
    _ID_NO_NOME,
    LISTA_CONCLUIDO,
    LISTA_EM_EXECUCAO,
    LISTA_EM_VALIDACAO,
    LISTAS,
    RAIZ_CENTRAL,
    Cartao,
    FalhaDeLeitura,
    _diferencas,
    _hora,
    _limpo,
    _novo_cliente,
    aplicar,
    ler_cartoes,
)
from resumo_rodada import _ID, _TIPO, ENCERRADAS  # noqa: E402

BASE_PADRAO = "http://127.0.0.1:8000"
INTERVALO_MINIMO_S = 120.0
PREFIXO_NOME = "Prova 07/10"
_LINHA_ALVO = re.compile(r"(?m)^Alvo da operação: (\S+)[ \t]*$")
_CURTO = re.compile(r"^[A-Za-z0-9]{4,12}$")
#: só o id do aparelho da central (`android-03`): serial (`emulator-5554`) e hostname não são rótulo
_APARELHO = re.compile(r"^android-\d{1,3}$")

EM_EXECUCAO, PAROU, CONCLUIDO = "em execução", "precisa de olhar", "concluído"
LISTA_DA_SITUACAO = {EM_EXECUCAO: LISTA_EM_EXECUCAO, PAROU: LISTA_EM_VALIDACAO, CONCLUIDO: LISTA_CONCLUIDO}

#: os estágios do dono (`domain/estagios.py::ESTAGIOS`), em palavras. O rótulo de abertura do app (`instagram_aberto`) e
#: qualquer outro desconhecido caem em `_palavras`, que nunca falha.
ESTAGIO = {
    "persona": "persona", "conta": "conta", "sessao": "sessão", "aparelho": "aparelho", "app_aberto": "app aberto",
    "target_localizado": "perfil alvo localizado", "post_localizado": "post localizado", "conteudo_lido": "conteúdo lido",
    "conhecimento_recuperado": "conhecimento recuperado", "resposta_gerada": "resposta gerada",
    "interface_de_comentario_alcancada": "interface de comentário alcançada", "acao_preparada": "ação preparada",
    "acao_executada": "ação executada", "acao_bloqueada": "ação bloqueada", "resultado_verificado": "resultado verificado",
}
#: motivo do servidor -> (texto fixo, próximo passo curto). Texto livre (o `blocked_reason` do objetivo) NÃO entra.
MOTIVO = {
    "persona inexistente": ("persona não encontrada", "conferir a persona no painel."),
    "sem conta": ("persona sem conta no app", "criar ou vincular a conta pelo painel."),
    "sem sessão": ("sem sessão válida", "conectar a conta pelo painel (Foco do aparelho)."),
    "sessão fora do aparelho principal": ("sessão fora do aparelho principal", "usar o aparelho principal da conta."),
    "aparelho indisponível": ("aparelho indisponível", "esperar o aparelho voltar ou liberar outro."),
    "teto de custo": ("teto de custo atingido", "o dono decide se eleva o teto."),
    "limite de ações executadas": ("limite de ações executadas", "o dono decide se eleva o limite."),
    "aguarda liberação": ("aguarda liberação do dono", "liberar pela Operação do painel."),
    "recusada pela política": ("recusada pela política da frota", "nenhum; a regra da frota recusou a ação."),
    "aprovação recusada": ("aprovação recusada", "nenhum; a pessoa recusou a ação."),
    "waiting_user": ("espera uma pessoa", "uma pessoa olha o aparelho pelo painel."),
    "cancelado": ("cancelado", "nenhum; a operação ou o alvo foi cancelado."),
    "ação preparada": ("ação preparada, sem executar", "liberar pela Operação do painel."),
}
MOTIVO_DA_ACAO_FINAL = ("a ação final não concluiu", "olhar a execução pelo painel.")
MOTIVO_NAO_CLASSIFICADO = ("motivo não classificado", "olhar a Operação no painel.")
VERIFICADA = ("sim", "não", "não conferida", "sem ação")


# ------------------------------------------------------------------------------------------ puro: texto do cartão
def _palavras(estagio: object) -> str:
    """O estágio em palavras; o desconhecido vira o nome com espaço no lugar do sublinhado (só se for um id simples)."""
    if not isinstance(estagio, str):
        return "estágio não informado"
    if estagio in ESTAGIO:
        return ESTAGIO[estagio]
    if estagio.endswith("_aberto"):  # a abertura do app (`instagram_aberto`): o app não entra no cartão
        return ESTAGIO["app_aberto"]
    return estagio.replace("_", " ") if re.fullmatch(r"[a-z][a-z0-9_]{0,40}", estagio) else "estágio não reconhecido"


def _motivo(situacao_bruta: str, motivo: object, sem_verificacao: bool) -> tuple[str, str]:
    """(texto fixo, próximo passo). O alvo cancelado é sempre "cancelado"; o concluído sem verificação é "ação preparada"."""
    if situacao_bruta == "cancelado":
        return MOTIVO["cancelado"]
    if situacao_bruta == "concluido" and sem_verificacao:
        return MOTIVO["ação preparada"]
    texto = " ".join(motivo.split()) if isinstance(motivo, str) else ""
    if texto in MOTIVO:
        return MOTIVO[texto]
    if texto.startswith("a ação final terminou "):
        return MOTIVO_DA_ACAO_FINAL
    return MOTIVO_NAO_CLASSIFICADO


def _ultima_mudanca(alvo: dict) -> datetime | None:
    horas = [h for e in (alvo.get("estagios") or []) if isinstance(e, dict) and (h := _hora(e.get("em"))) is not None]
    return max(horas) if horas else None


def _acao(alvo: dict) -> tuple[str | None, str]:
    """(tipo da ação final, "sim|não|não conferida|sem ação"). Só `tipo` (id de capability) e `verificada` são lidos; o texto
    do rascunho (`resultado.texto`) NUNCA."""
    res = alvo.get("resultado") if isinstance(alvo.get("resultado"), dict) else None
    af = res.get("acao_final") if res and isinstance(res.get("acao_final"), dict) else None
    if not af:
        return None, "sem ação"
    tipo = str(af.get("tipo") or "")
    tipo = tipo if _TIPO.match(tipo) else "outra"
    estagios = {e.get("estagio") for e in (alvo.get("estagios") or []) if isinstance(e, dict)}
    if af.get("verificada") is True:
        return tipo, "sim"
    if "acao_executada" in estagios or "resultado_verificado" in estagios:
        return tipo, "não conferida"  # a ação rodou, mas a verificação não fechou
    return tipo, "não"  # preparada ou bloqueada: não foi executada, logo não há o que verificar


def _situacao(alvo: dict, verificada: str) -> str | None:
    """A situação do cartão (chave de LISTA_DA_SITUACAO), ou `None` se o `estado` do alvo não se reconhece."""
    estado = alvo.get("estado")
    if estado in ("pendente", "em_curso"):
        return EM_EXECUCAO
    if estado in ("bloqueado", "cancelado"):
        return PAROU
    if estado == "concluido":
        return CONCLUIDO if verificada == "sim" else PAROU  # concluído sem a ação verificada ainda precisa de um olhar
    return None


def montar_cartao(op_id: str, n: int, alvo: dict) -> tuple[str, str, str] | None:
    """(nome, descrição, situação) do cartão do alvo nº `n` (1, 2...). `None` se o estado do alvo não se reconhece: quem chama
    pula o alvo e conta a falha. Só vocabulário fixo, rótulos e horas; ainda assim tudo passa pelo filtro do Trello."""
    tipo, verificada = _acao(alvo)
    situacao = _situacao(alvo, verificada)
    if situacao is None:
        return None
    rotulo = f"P{n:02d}"
    ultimo = alvo.get("estagio")
    parou_em = alvo.get("parou_em")
    if alvo.get("estado") == "cancelado":
        em_palavras = f"cancelado em {_palavras(parou_em or ultimo)}"
    elif situacao == PAROU and alvo.get("estado") == "bloqueado":
        em_palavras = f"parou em {_palavras(parou_em or ultimo)}"
    else:
        em_palavras = _palavras(ultimo)
    nome = f"{PREFIXO_NOME} · {rotulo} · {em_palavras} ({_curto_do_op(op_id)})"
    hora = _ultima_mudanca(alvo)
    linhas = [
        f"Alvo da operação: {op_id}/{rotulo}",
        f"Situação: {situacao}",
        f"Estágio atual: {_palavras(ultimo)}",
        f"Última mudança: {hora.astimezone(UTC).strftime('%Y-%m-%d %H:%MZ') if hora else 'não informada'}",
    ]
    if situacao == PAROU:
        motivo, passo = _motivo(str(alvo.get("estado")), alvo.get("motivo"), sem_verificacao=alvo.get("estado") == "concluido")
        linhas.append(f"Motivo: {motivo}")
        if parou_em and alvo.get("estado") in ("bloqueado", "cancelado"):
            linhas.append(f"Parou em: {_palavras(parou_em)}")
        linhas.append(f"Próximo passo: {passo}")
    linhas.append(f"Ação final: {tipo or 'nenhuma'}")
    linhas.append(f"Ação verificada: {verificada}")
    aparelho = alvo.get("instance_id")
    if isinstance(aparelho, str) and _APARELHO.fullmatch(aparelho):
        linhas.append(f"Aparelho usado: {aparelho}")
    linhas += ["", "Gerado pela Canais (28.76; a descrição é reescrita quando o estágio muda). Sem nome, handle, e-mail, "
                   "telefone, IP, serial nem texto de comentário."]
    return _limpo(nome), _limpo("\n".join(linhas)), situacao


def _curto_do_op(op_id: str) -> str:
    """`op-20261006130000-abc123` vira `op-abc123`: basta para achar a operação."""
    ultimo = op_id.rsplit("-", 1)[-1]
    return f"op-{ultimo}" if _CURTO.match(ultimo) else op_id[:12]


# ------------------------------------------------------------------------------------------ puro: plano de ações
@dataclass(frozen=True)
class AlvoDoCartao:
    chave: str                   # `op-.../P03`
    nome: str
    desc: str
    lista: str


@dataclass(frozen=True)
class Acao:
    """Compatível com `cartoes_de_aparelho.aplicar` (tipo, rotulo, lista, nome, desc, card_id, muda)."""

    tipo: str                    # criar | atualizar
    rotulo: str                  # a chave `op-.../P03`
    lista: str                   # lista de destino
    nome: str
    desc: str
    card_id: str | None = None
    muda: tuple[str, ...] = ()   # nome, descrição, lista


@dataclass
class Plano:
    acoes: list[Acao] = field(default_factory=list)
    duplicados: list[str] = field(default_factory=list)


def alvos_da_operacao(op: dict) -> tuple[list[AlvoDoCartao], list[str]]:
    """Os cartões que a operação pede (um por alvo, na ordem de `alvos`) e as falhas (alvo ilegível, estado desconhecido).
    Levanta `FalhaDeLeitura` se o JSON não é de uma operação (sem `id` válido, sem `status` ou sem a lista `alvos`)."""
    op_id = op.get("id") if isinstance(op, dict) else None
    if not isinstance(op_id, str) or not _ID.match(op_id):
        raise FalhaDeLeitura("operação sem id válido")
    alvos = op.get("alvos")
    if not isinstance(alvos, list) or not alvos or not isinstance(op.get("status"), str):
        raise FalhaDeLeitura("operação sem alvos ou sem status")
    cartoes: list[AlvoDoCartao] = []
    falhas: list[str] = []
    for n, alvo in enumerate(alvos, start=1):
        feito = montar_cartao(op_id, n, alvo) if isinstance(alvo, dict) else None
        if feito is None:
            falhas.append(f"alvo P{n:02d} de {_curto_do_op(op_id)}: estado não reconhecido")
            continue
        nome, desc, situacao = feito
        cartoes.append(AlvoDoCartao(f"{op_id}/P{n:02d}", nome, desc, LISTA_DA_SITUACAO[situacao]))
    return cartoes, falhas


def encerrada(op: dict) -> bool:
    return isinstance(op, dict) and op.get("status") in ENCERRADAS and bool(op.get("finished_at"))


def cartoes_de_alvo(cartoes: list[Cartao]) -> tuple[dict[str, Cartao], list[str]]:
    """Os cartões abertos que SÃO de alvo (linha `Alvo da operação: <chave>`, nas 3 listas, sem ID de plano no nome):
    chave -> cartão, e as chaves com mais de um cartão (esses não se tocam)."""
    achados: dict[str, list[Cartao]] = {}
    for c in cartoes:
        if c.lista not in LISTAS or _ID_NO_NOME.match(c.nome):
            continue
        m = _LINHA_ALVO.search(c.desc or "")
        if m:
            achados.setdefault(m.group(1), []).append(c)
    return ({k: cs[0] for k, cs in achados.items() if len(cs) == 1}, sorted(k for k, cs in achados.items() if len(cs) > 1))


def planejar(alvos: list[AlvoDoCartao], cartoes: list[Cartao]) -> Plano:
    """Só as chaves de `alvos` (as das operações pedidas) são consideradas: cartão de outra operação nunca é tocado."""
    existentes, duplicados = cartoes_de_alvo(cartoes)
    plano = Plano()
    for a in alvos:
        if a.chave in duplicados:
            plano.duplicados.append(a.chave)
            continue
        c = existentes.get(a.chave)
        if c is None:
            plano.acoes.append(Acao("criar", a.chave, a.lista, a.nome, a.desc))
            continue
        muda = _diferencas(c, a.nome, a.desc, a.lista)
        if muda:
            plano.acoes.append(Acao("atualizar", a.chave, a.lista, a.nome, a.desc, c.id, muda))
    return plano


def contar(plano: Plano) -> dict[str, int]:
    out = {"criar": 0, "atualizar": 0, "movidos": 0}
    for a in plano.acoes:
        out[a.tipo] += 1
        if a.tipo == "atualizar" and "lista" in a.muda:
            out["movidos"] += 1
    return out


# ------------------------------------------------------------------------------------------ um ciclo
@dataclass
class Ciclo:
    plano: Plano = field(default_factory=Plano)
    falhas: list[str] = field(default_factory=list)
    operacoes: int = 0
    encerradas: int = 0
    alvos: int = 0
    #: quantas das `falhas` são de alvo com estado desconhecido: o dado não vai mudar sozinho, então não prendem o laço
    de_alvo: int = 0

    @property
    def todas_encerradas(self) -> bool:
        """Todas as operações pedidas foram lidas NESTE ciclo e estão encerradas, e nada falhou (nem leitura nem gravação)."""
        return self.operacoes > 0 and self.encerradas == self.operacoes and len(self.falhas) == self.de_alvo


def linha_final(c: Ciclo, ensaio: bool) -> str:
    n = contar(c.plano)
    verbo = ("criar", "atualizar") if ensaio else ("criados", "atualizados")
    return (f"operações {c.operacoes} (encerradas {c.encerradas}); alvos {c.alvos}; {verbo[0]} {n['criar']}; "
            f"{verbo[1]} {n['atualizar']}; movidos {n['movidos']}; duplicados {len(c.plano.duplicados)}; "
            f"falhas {', '.join(c.falhas) if c.falhas else 'nenhuma'}")


def descrever(a: Acao, ensaio: bool) -> str:
    verbo = {"criar": "criaria" if ensaio else "criado", "atualizar": "atualizaria" if ensaio else "atualizado"}[a.tipo]
    onde = f" -> {LISTAS[a.lista]}" if a.tipo == "criar" or "lista" in a.muda else ""
    mudou = f" ({', '.join(a.muda)})" if a.muda else ""
    return _limpo(f"  {verbo}: {a.nome}{onde}{mudou}")


def rodar_ciclo(refs: list[str], ler: Callable[[str], dict], obter_cartoes: Callable[[], list[Cartao]], ensaio: bool,
                escrever: Callable[[Plano], list[str]] | None = None, imprimir: Callable[[str], None] = print) -> Ciclo:
    """Lê cada operação (a que falha é pulada e contada: seus cartões não se tocam), planeja contra os cartões e, se
    `escrever`, grava. Nunca levanta por leitura: o que falhou vira `falhas` (só o tipo do erro)."""
    ciclo = Ciclo(operacoes=len(refs))
    alvos: list[AlvoDoCartao] = []
    for ref in refs:
        try:
            op = ler(ref)
            do_op, falhas = alvos_da_operacao(op)
        except FalhaDeLeitura as erro:
            ciclo.falhas.append(f"leitura de {_rotulo_de_ref(ref)}: {erro.causa}")
            continue
        alvos += do_op
        ciclo.falhas += falhas
        ciclo.de_alvo += len(falhas)
        ciclo.encerradas += 1 if encerrada(op) else 0
    ciclo.alvos = len(alvos)
    if alvos:
        try:
            cartoes = obter_cartoes()
        except Exception as erro:  # noqa: BLE001 - só o tipo do erro sai
            ciclo.falhas.append(f"leitura do quadro: {type(erro).__name__}")
            imprimir(linha_final(ciclo, ensaio))
            return ciclo
        ciclo.plano = planejar(alvos, cartoes)
        for a in ciclo.plano.acoes:
            imprimir(descrever(a, ensaio))
        if escrever is not None:
            ciclo.falhas += escrever(ciclo.plano)
    imprimir(linha_final(ciclo, ensaio))
    return ciclo


def _rotulo_de_ref(ref: str) -> str:
    """O id da operação, ou o nome do arquivo: nunca o caminho inteiro."""
    return ref if _ID.match(ref) else Path(ref).name[:40]


# ------------------------------------------------------------------------------------------ o laço
def laco(ciclo: Callable[[], Ciclo], dormir: Callable[[float], None], intervalo_s: float,
         imprimir: Callable[[str], None] = print) -> Ciclo:
    """Repete `ciclo` a cada `intervalo_s` até todas as operações estarem encerradas num ciclo limpo, e roda UM ciclo a
    mais (a confirmação: o que a leitura reabriu ou a gravação que falhou ainda é corrigido) antes de sair. Ciclo que
    levanta não derruba o laço. Devolve o último ciclo."""
    ultimo = Ciclo(falhas=["nenhum ciclo rodou"])
    confirmando = False
    n = 0
    while True:
        n += 1
        imprimir(f"ciclo {n} ({datetime.now(UTC).strftime('%H:%MZ')})")
        try:
            ultimo = ciclo()
        except Exception as erro:  # noqa: BLE001 - o laço não morre; só o tipo do erro sai (a mensagem pode trazer URL)
            ultimo = Ciclo(falhas=[f"ciclo falhou: {type(erro).__name__}"])
            imprimir(ultimo.falhas[0])
        if confirmando:
            return ultimo
        confirmando = ultimo.todas_encerradas
        dormir(intervalo_s)


# ------------------------------------------------------------------------------------------ leitura (central ou arquivo)
def _obter_http(url: str) -> object:
    try:
        with urllib.request.urlopen(url, timeout=15) as r:  # noqa: S310 - loopback do central; só GET
            return json.loads(r.read().decode("utf-8"))
    except Exception as erro:  # noqa: BLE001 - só o tipo do erro sai (a mensagem pode trazer a URL)
        raise FalhaDeLeitura(type(erro).__name__) from None


def ler_da_central(operacao_id: str, base: str = BASE_PADRAO, obter: Callable[[str], object] | None = None) -> dict:
    """`GET /api/operacoes/<id>`. O `id` da resposta tem de ser o pedido."""
    if not _ID.match(operacao_id):
        raise FalhaDeLeitura("id de operação inválido")
    op = (obter or _obter_http)(f"{base.rstrip('/')}/api/operacoes/{operacao_id}")
    if not isinstance(op, dict) or op.get("id") != operacao_id:
        raise FalhaDeLeitura("resposta sem a operação pedida")
    return op


def ler_do_arquivo(caminho: str) -> dict:
    try:
        op = json.loads(Path(caminho).read_text(encoding="utf-8"))
    except (OSError, ValueError) as erro:
        raise FalhaDeLeitura(type(erro).__name__) from None
    if not isinstance(op, dict):
        raise FalhaDeLeitura("arquivo sem operação")
    return op


# ------------------------------------------------------------------------------------------ comando
def main(argv: list[str] | None = None, *, dormir: Callable[[float], None] = time.sleep,
         imprimir: Callable[[str], None] = print) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--operacao", action="append", default=[], metavar="op-...", help="id da operação (repetível)")
    p.add_argument("--arquivo", action="append", default=[], help="JSON de uma operação já lida (repetível; ensaio offline)")
    p.add_argument("--base", default=BASE_PADRAO, help="central (padrão: %(default)s)")
    p.add_argument("--aplicar", action="store_true", help="grava no Trello (sem isto é só ensaio)")
    p.add_argument("--laco", action="store_true", help="repete --aplicar até as operações encerrarem (+ um ciclo)")
    p.add_argument("--intervalo-s", type=float, default=INTERVALO_MINIMO_S, help="intervalo do laço (mínimo 120 s)")
    a = p.parse_args(argv)
    if bool(a.operacao) == bool(a.arquivo):
        p.error("informe --operacao op-... OU --arquivo (um dos dois)")
    if a.laco and a.arquivo:
        p.error("--laco só vale com a operação lida da central, não com --arquivo")
    if a.laco and a.intervalo_s < INTERVALO_MINIMO_S:
        p.error(f"--intervalo-s mínimo é {INTERVALO_MINIMO_S:g} s (a leitura da operação grava na central); recusado")
    aplicar_ = a.aplicar or a.laco
    ensaio = not aplicar_
    refs = list(dict.fromkeys(a.operacao or a.arquivo))
    ler: Callable[[str], dict] = (ler_do_arquivo if a.arquivo else (lambda ref: ler_da_central(ref, a.base)))
    try:
        # os nomes de persona a esconder vêm do banco do checkout central (um worktree não o tem)
        if not redacao.recarregar(RAIZ_CENTRAL) and aplicar_:
            imprimir("não consegui ler os nomes a esconder (banco do central); nada foi gravado")
            return 1
        cl = _novo_cliente() if (aplicar_ or not a.arquivo) else None
    except Exception as erro:  # noqa: BLE001 - a mensagem pode trazer chave: só o tipo
        imprimir(f"falhou: {type(erro).__name__}; nada foi gravado")
        return 1

    def obter_cartoes() -> list[Cartao]:
        return asyncio.run(ler_cartoes(cl)) if cl is not None else []

    escrever = (lambda plano: asyncio.run(aplicar(cl, plano))) if aplicar_ else None  # noqa: E731

    def um() -> Ciclo:
        return rodar_ciclo(refs, ler, obter_cartoes, ensaio, escrever, imprimir)

    if not a.laco:
        return 1 if um().falhas else 0
    imprimir(f"laço: um ciclo a cada {a.intervalo_s:g} s até as {len(refs)} operação(ões) encerrarem; Ctrl+C para parar")
    try:
        return 1 if laco(um, dormir, a.intervalo_s, imprimir).falhas else 0
    except KeyboardInterrupt:
        imprimir("laço interrompido")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
