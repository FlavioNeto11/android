"""A prévia da porta pelo canal (item 28.27, parte do 30.61): o que o dono lê no Telegram antes do "Executar (aprova N)".

Puro: recebe a prévia que `porta_do_plano.previa_da_porta` devolveu (o MESMO dicionário do `GET /runs/{id}/porta`) e
monta o texto. Selo, motivo e chave vêm de lá, nunca de conta própria do canal (R1 do desenho).

O que o dono vê de cada item segue a regra da mensagem de aprovação pendente (`mensagem.partes_da_aprovacao`): o alvo
redigido e o texto pelo `texto_livre`, aqui por extenso. O rótulo da persona (`@usuario`) não sai, como não sai na
aprovação pendente. Item `aprovacao` cujo texto os filtros mudariam (`texto_mostravel`) ou cuja imagem não pôde ser
conferida fica FORA do sim pelo canal: o dono não veria o que aprova. Ele pede o dono no painel ou na execução.

Texto puro (R2): o adaptador manda sem `parse_mode`, e o texto do item chega ao dono como é. Se um dia esta mensagem
usar HTML ou Markdown, o texto do item tem de ser escapado aqui.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime

from .mensagem import Redigir, partes_da_aprovacao, texto_mostravel
from .privacidade import texto_livre

APROVACAO, NA_EXECUCAO = "aprovacao", "na_execucao"
#: selo → (emoji, como o dono lê)
SELOS: dict[str, tuple[str, str]] = {
    "permitido": ("✅", "segue sem parar"),
    APROVACAO: ("🔒", "pede o seu sim"),
    "adiado": ("⏳", "espera a hora"),
    "recusado": ("⛔", "não acontece"),
    NA_EXECUCAO: ("🕓", "decide-se na execução"),
}
#: o corte por mensagem: o Telegram aceita 4096; a folga é do prefixo da IA e do reenvio
LIMITE = 3800
FORA_DO_CANAL = "pede você no painel ou na execução: não dá para mostrar aqui por inteiro"
#: texto maior que isto não cabe num bloco de uma mensagem: o dono não o veria inteiro, e o item fica fora do canal
TEXTO_MAX_NO_CANAL = 3000


@dataclass(frozen=True)
class LeituraDaPorta:
    """O que o canal tira da prévia: os pares `(step_id, chave)` que o Executar aprova (o retrato do que o dono VIU), os
    itens `aprovacao` fora do canal e quantos itens vão pedir o dono na execução."""

    aprovar: list[tuple[str, str]] = field(default_factory=list)
    fora_do_canal: list[str] = field(default_factory=list)
    na_execucao: int = 0


def itens_da_previa(previa: Mapping[str, object]) -> list[dict[str, object]]:
    itens = previa.get("itens")
    return [i for i in itens if isinstance(i, dict)] if isinstance(itens, list) else []


def ler_porta(previa: Mapping[str, object], nomes: Iterable[str], redigir: Redigir,
              imagem_conferida: Callable[[Mapping[str, object]], bool]) -> LeituraDaPorta:
    """Separa o que o Executar do canal aprova. `imagem_conferida(item)`: a imagem do item foi lida e o sha256 dos bytes
    bate com o `imagem_sha256` da porta (só é chamada para o item que tem imagem)."""
    nomes = list(nomes)
    aprovar: list[tuple[str, str]] = []
    fora: list[str] = []
    na_execucao = 0
    for numero, item in enumerate(itens_da_previa(previa), 1):
        selo, chave, sid = item.get("selo"), item.get("chave"), str(item.get("step_id") or "")
        if selo == NA_EXECUCAO:
            na_execucao += 1
            continue
        if selo != APROVACAO:
            continue
        texto = item.get("texto")
        mostravel = not isinstance(texto, str) or not texto.strip() or (
            len(texto) <= TEXTO_MAX_NO_CANAL and texto_mostravel(texto, nomes, redigir))
        # O bloco inteiro tem de caber numa mensagem: cortado com "…", o dono não leria tudo o que aprova (revisão do
        # 28.27, B). A imagem só é conferida (e só sai) para o item que fica no sim pelo canal (A).
        cabe = mostravel and len(_bloco(numero, item, False, nomes, redigir)) <= LIMITE
        imagem_ok = cabe and (not item.get("tem_imagem") or imagem_conferida(item))
        if isinstance(chave, str) and chave and sid and imagem_ok:
            aprovar.append((sid, chave))
        else:
            fora.append(sid)
    return LeituraDaPorta(aprovar, fora, na_execucao + len(fora))


def _hora(iso: object) -> str | None:
    try:
        return f"{datetime.fromisoformat(str(iso).replace('Z', '+00:00')):%H:%M}Z" if iso else None
    except ValueError:
        return None


def marca_do_retrato(hash_do_plano: object, pares: list[list[str]]) -> str:
    """8 caracteres que identificam O retrato que uma prévia mostrou: o hash do plano e os pares que o botão aprova. O
    `plano_mudou` pode vir sem o hash mudar (a política mudou um selo, a imagem trocou), por isso entram os pares."""
    bruto = json.dumps([str(hash_do_plano or ""), pares], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(bruto.encode("utf-8")).hexdigest()[:8]


def linha_sem_aprovacao(curta: str, leitura: LeituraDaPorta, *, plano_mudou: bool = False) -> str:
    """A linha só do caminho N = 0 (decisão da orquestradora, 04/10 21:58Z): a execução já foi iniciada. Depois de um
    `plano_mudou`, a linha diz que o plano mudou e que nenhum item pede mais o sim (decisão de 04/10 22:59Z)."""
    k = leitura.na_execucao
    if plano_mudou:
        resto = (f" {'1 item vai pedir' if k == 1 else f'{k} itens vão pedir'} você na execução." if k else "")
        return (f"O plano mudou desde a prévia e nenhum item pede o seu sim agora: execução {curta} iniciada.{resto} "
                "Conto aqui quando terminar.")
    if not k:
        return f"Execução {curta} iniciada: o plano não tem aprovação pendente. Conto aqui quando terminar."
    itens = "1 item vai pedir" if k == 1 else f"{k} itens vão pedir"
    return (f"Execução {curta} iniciada: o plano não tem aprovação pendente agora; {itens} você na execução. Conto aqui "
            "quando terminar.")


def _bloco(n: int, item: Mapping[str, object], fora: bool, nomes: list[str], redigir: Redigir) -> str:
    emoji, leitura = SELOS.get(str(item.get("selo")), ("•", str(item.get("selo") or "")))
    titulo = texto_livre(str(item.get("titulo") or item.get("acao") or "etapa"), nomes, redigir).strip()
    aparelho = texto_livre(str(item.get("aparelho") or ""), nomes, redigir).strip()
    onde = f" · {aparelho}" if aparelho else ""
    linhas = [f"{n}. {emoji} {titulo}{onde}: {FORA_DO_CANAL if fora else leitura}"]
    motivo = texto_livre(str(item.get("motivo") or ""), nomes, redigir).strip()
    if motivo:
        linhas.append(f"   Por quê: {motivo}")
    texto = item.get("texto")
    linhas += [f"   {p}" for p in partes_da_aprovacao(str(item["alvo"]) if item.get("alvo") else None,
                                                   texto if isinstance(texto, str) else None, nomes, redigir,
                                                   maximo=None)]
    if item.get("texto_na_execucao"):
        linhas.append("   O texto é escrito na execução.")
    if item.get("tem_imagem"):
        # Só a imagem do item que pede o sim pelo canal é enviada (conferida pelo sha256); a dos outros fica no painel.
        enviada = not fora and item.get("selo") == APROVACAO
        linhas.append(f"   Imagem: segue abaixo, como \"item {n}\"." if enviada else "   Imagem: no painel.")
    if item.get("selo") == "adiado" and _hora(item.get("retry_at")):
        linhas.append(f"   Segue a partir de {_hora(item.get('retry_at'))}.")
    return "\n".join(linhas)


def mensagens_da_porta(previa: Mapping[str, object], curta: str, leitura: LeituraDaPorta, nomes: Iterable[str],
                       redigir: Redigir, *, limite: int = LIMITE) -> list[str]:
    """O cabeçalho, um bloco por item (na ordem do plano) e o rodapé, cortados em mensagens de até `limite` caracteres
    sem partir um bloco. Os botões vão na última (quem chama)."""
    nomes = list(nomes)
    itens = itens_da_previa(previa)
    contagem: dict[str, int] = {}
    for item in itens:
        contagem[str(item.get("selo"))] = contagem.get(str(item.get("selo")), 0) + 1
    n = len(leitura.aprovar)
    cabeca = [f"Plano {curta}: {n} {'item pede' if n == 1 else 'itens pedem'} o seu sim antes de começar."]
    resumo = " · ".join(f"{SELOS[s][0]} {contagem[s]} {SELOS[s][1]}" for s in SELOS if contagem.get(s))
    if resumo:
        cabeca.append(resumo)
    if leitura.fora_do_canal:
        k = len(leitura.fora_do_canal)
        cabeca.append(f"{k} {'item não pode' if k == 1 else 'itens não podem'} ser mostrado{'s' if k > 1 else ''} aqui "
                      f"por inteiro; {'ele pede' if k == 1 else 'eles pedem'} você no painel ou na execução.")
    validade = _hora(previa.get("validade_ate"))
    if validade:
        cabeca.append(f"O sim vale até {validade}.")
    fora = set(leitura.fora_do_canal)
    blocos = [_bloco(i, item, str(item.get("step_id")) in fora, nomes, redigir) for i, item in enumerate(itens, 1)]
    na_exec = previa.get("na_execucao") if isinstance(previa.get("na_execucao"), Mapping) else {}
    rodape = ["Desafio, 2FA e CAPTCHA seguem com você na execução."]
    for_each = int(na_exec.get("itens_for_each") or 0) if isinstance(na_exec, Mapping) else 0
    if for_each:
        rodape.append(f"{for_each} {'etapa nasce' if for_each == 1 else 'etapas nascem'} da coleta; o sim é pedido na "
                      "execução.")
    if previa.get("parcial") or previa.get("total"):
        rodape.append("A trava de algum item não pôde ser calculada agora: ele se decide na execução.")
    pedacos = ["\n".join(cabeca), *blocos, "\n".join(rodape)]
    mensagens: list[str] = []
    atual = ""
    for pedaco in pedacos:
        if len(pedaco) > limite:
            pedaco = pedaco[:limite - 1] + "…"            # um bloco só maior que a mensagem: cortado, nunca partido
        candidato = f"{atual}\n\n{pedaco}" if atual else pedaco
        if len(candidato) > limite and atual:
            mensagens.append(atual)
            atual = pedaco
        else:
            atual = candidato
    if atual:
        mensagens.append(atual)
    return mensagens
