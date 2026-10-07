"""28.70: o cartão-pai da prova de 07/10 com os 16 critérios lidos do RELATÓRIO DO SERVIDOR, não à mão.

Fonte: `GET /api/operacoes/<id>/relatorio` da central (adendo v1.111, Jev 31.195) ou `--arquivo relatorio.json`, offline.
O contrato lido é o FINAL do adendo v1.111 (`docs/api-contract.md` da feat/operacao-latencia-por-estagio, 76d90f36): 19
critérios (os 16 do dono mais 2b, 3b e 11b); se algo mudar, o ajuste cabe em `ler_relatorio` (uma função só, que tolera
campo faltando).

Escreve na DESCRIÇÃO do cartão (nunca em comentário: o comentário pela API entra como "do dono") um bloco entre
`<!-- criterios-da-prova:inicio -->` e `<!-- criterios-da-prova:fim -->`. O resto da descrição fica intocado; sem os
marcadores, o bloco é acrescentado ao fim. Cada critério vira uma linha com ✅ (provado real), ⚠️ (implementado ou
testado em simulação) ou ⬜ (o resto), o estado e "nesta operação: sim/não/não medido".

Regra de ouro: o que vem ausente, `null` ou `"nao_medido"` NUNCA vira "sim" nem zero; vira "não medido". Relatório sem a lista
`criterios` (ou sem operação) é RECUSADO com mensagem, e a descrição do cartão não é tocada. A evidência em texto livre
não vai ao Trello (servidor de terceiro): só "com evidência" ou "sem evidência". O bloco é função só do relatório (a hora
é a `gerado_em` dele, nunca a de agora), então repetir com o mesmo relatório é "0 ações".

Uso (python do backend/.venv):
  pai_da_prova.py --operacao op-...                ensaio: lê a central e IMPRIME o bloco (não toca o Trello)
  pai_da_prova.py --arquivo relatorio.json         ensaio offline
  pai_da_prova.py ... --ler-cartao                 ensaio que também LÊ o cartão (só GET) e diz a ação: criar, substituir ou nada
  pai_da_prova.py ... --aplicar                    grava a descrição do cartão (só com o sinal da orquestradora)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(AQUI))
sys.path.insert(0, str(AQUI.parent / "canais"))

import redacao  # noqa: E402
from redacao import redigir  # noqa: E402
from resumo_laco import _sem_contato  # noqa: E402

# a chave e o token do Trello ficam no checkout central; o worktree não os tem (lidos por EnvSettings, nunca impressos)
BACKEND_CENTRAL = Path(r"C:\git\android\backend")
RAIZ_CENTRAL = BACKEND_CENTRAL.parent

CENTRAL = "http://127.0.0.1:8000"
#: cartão-pai da prova de 07/10 (código curto do link) e o quadro Execução, onde ele mora
CARTAO_PAI = "oKFVN6tG"
QUADRO_EXECUCAO = "6ac13aeda5570365d020f8e2"
INICIO = "<!-- criterios-da-prova:inicio -->"
FIM = "<!-- criterios-da-prova:fim -->"
ESPERADOS = 19
LIMITE_DESCRICAO = 16384  # o do Trello
MAX_NOME = 90
_ID = re.compile(r"^op-[A-Za-z0-9-]{1,60}$")
_CURTO = re.compile(r"^[A-Za-z0-9]{4,12}$")

ESTADOS = {
    "provado_real": ("✅", "provado real"),
    "testado_em_simulacao": ("⚠️", "testado em simulação"),
    "implementado": ("⚠️", "implementado"),
    "bloqueado": ("⬜", "bloqueado"),
    "nao_implementado": ("⬜", "não implementado"),
}
NESTA = {"sim": "sim", "nao": "não", "nao_medido": "não medido"}
NAO_MEDIDO = "não medido"
#: `operacao.status` do servidor, em português; código desconhecido fica como veio (sem sublinhado)
STATUS = {"em_curso": "em curso", "concluida": "concluída", "concluida_com_bloqueios": "concluída com bloqueios",
          "cancelada": "cancelada"}
AMBIENTES = {"real": "real", "simulado": "simulado", "nao_medido": NAO_MEDIDO}
CAMPOS_DA_CAPACIDADE = (("solicitados", "solicitados"), ("contas_existentes", "contas existentes"),
                        ("sessoes_validas", "sessões válidas"), ("contas_disponiveis", "contas disponíveis"),
                        ("concluidas", "concluídas"), ("bloqueadas", "bloqueadas"), ("em_curso", "em curso"))


class Recusa(Exception):
    """A entrada não serve para o bloco (relatório de outro formato, sem critérios, marcadores quebrados, central fora)."""


@dataclass(frozen=True)
class Criterio:
    id: str
    nome: str
    estado: str | None            # chave de ESTADOS, ou None (ausente ou desconhecido)
    nesta_operacao: str           # chave de NESTA; ausente ou desconhecido vira "nao_medido"
    com_evidencia: bool


@dataclass(frozen=True)
class Relatorio:
    gerado_em: datetime | None
    operacao: str                 # só o id curto
    status: str | None
    ambiente: str                 # chave de AMBIENTES; ausente ou desconhecido vira "nao_medido"
    capacidade: dict[str, int | None]
    identidades: dict[str, int | None]            # solicitadas, executam_hoje, deficit
    nao_executam: list[tuple[str, int]]           # (motivo, n) das identidades que não executam hoje
    custo_total_usd: float | None
    custo_teto_usd: float | None
    criterios: list[Criterio]
    ignorados: int = 0            # critérios que vieram sem id ou sem nome
    avisos: list[str] = field(default_factory=list)


# ------------------------------------------------------------------------------------------------ leitura do relatório
def _registro(v: object) -> dict:
    return v if isinstance(v, dict) else {}


def _texto(v: object) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _inteiro(v: object) -> int | None:
    """Zero que o servidor mediu é zero; ausente, null, texto ou negativo é "não medido", nunca zero."""
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _usd(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None


def _curto(operacao_id: str) -> str:
    """`op-20261006130000-abc123` vira `abc123`: o id curto basta para achar a operação e não carrega a data inteira."""
    ultimo = operacao_id.rsplit("-", 1)[-1]
    return ultimo if _CURTO.match(ultimo) else operacao_id[:8]


def _hora(v: object) -> datetime | None:
    t = _texto(v)
    if t is None:
        return None
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _criterio(v: object) -> Criterio | None:
    o = _registro(v)
    ident = str(o["id"]) if isinstance(o.get("id"), (int, str)) and not isinstance(o.get("id"), bool) else None
    nome = _texto(o.get("nome"))
    if not ident or not ident.strip() or nome is None:
        return None
    estado = o.get("estado")
    nesta = o.get("nesta_operacao")
    return Criterio(id=ident.strip(), nome=nome, estado=estado if estado in ESTADOS else None,
                    nesta_operacao=nesta if nesta in NESTA else "nao_medido",
                    com_evidencia=_texto(o.get("evidencia")) is not None)


def ler_relatorio(dados: object) -> Relatorio:
    """O ÚNICO ponto que conhece o formato do relatório do servidor (rascunho v1.111): ajustar aqui quando o adendo sair.
    Tolera campo faltando (vira "não medido"); recusa só o que impede um bloco honesto."""
    o = _registro(dados)
    op = _registro(o.get("operacao"))
    operacao_id = _texto(op.get("id"))
    if not o or operacao_id is None:
        raise Recusa("a resposta não é um relatório de operação (falta `operacao.id`); a descrição do cartão não foi tocada")
    brutos = o.get("criterios")
    if not isinstance(brutos, list):
        raise Recusa("o relatório não traz a lista `criterios` (central antes do adendo v1.111?); "
                     "a descrição do cartão não foi tocada")
    lidos = [_criterio(c) for c in brutos]
    criterios = [c for c in lidos if c is not None]
    if not criterios:
        raise Recusa("a lista `criterios` do relatório veio vazia ou ilegível; a descrição do cartão não foi tocada")
    cap = _registro(o.get("capacidade"))
    ident = _registro(o.get("identidades"))
    custo = _registro(o.get("custo"))
    faltas = [(m, n) for f in (ident.get("nao_executam") if isinstance(ident.get("nao_executam"), list) else [])
              if (m := _texto(_registro(f).get("motivo"))) is not None and (n := _inteiro(_registro(f).get("n"))) is not None]
    return Relatorio(
        gerado_em=_hora(o.get("gerado_em")), operacao=_curto(operacao_id), status=_texto(op.get("status")),
        ambiente=o.get("ambiente") if o.get("ambiente") in AMBIENTES else "nao_medido",
        capacidade={chave: _inteiro(cap.get(chave)) for chave, _ in CAMPOS_DA_CAPACIDADE},
        identidades={chave: _inteiro(ident.get(chave)) for chave in ("solicitadas", "executam_hoje", "deficit")},
        nao_executam=faltas,
        custo_total_usd=_usd(custo.get("total_usd")), custo_teto_usd=_usd(custo.get("teto_usd")),
        criterios=criterios, ignorados=len(lidos) - len(criterios))


# ------------------------------------------------------------------------------------------------------------- o bloco
def _limpo(texto: str) -> str:
    """O filtro do Trello (handle, persona, IP) mais e-mail, telefone e URL; o mesmo do resumo do Telegram (28.66)."""
    return redigir(_sem_contato(texto))


def _usd_br(v: float | None) -> str:
    return NAO_MEDIDO if v is None else "US$ " + f"{v:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def _linha_do_criterio(c: Criterio) -> str:
    icone, rotulo = ESTADOS.get(c.estado or "", ("⬜", "estado não informado"))
    nome = c.nome if len(c.nome) <= MAX_NOME else c.nome[:MAX_NOME].rsplit(" ", 1)[0] + "…"
    evidencia = "com evidência" if c.com_evidencia else "sem evidência"
    return f"- {icone} {c.id} {nome} — {rotulo} (nesta operação: {NESTA[c.nesta_operacao]}; {evidencia})"


def montar_bloco(rel: Relatorio) -> str:
    gerado = f"{rel.gerado_em:%Y-%m-%d %H:%M}Z" if rel.gerado_em else "hora não informada"
    status = STATUS.get(rel.status, rel.status.replace("_", " ")) if rel.status else NAO_MEDIDO
    num = lambda v: NAO_MEDIDO if v is None else v  # noqa: E731 - nunca zero no lugar do que não foi medido
    cap = " · ".join(f"{rotulo} {num(rel.capacidade[chave])}" for chave, rotulo in CAMPOS_DA_CAPACIDADE)
    ident = rel.identidades
    quem = (f"Identidades: {num(ident['solicitadas'])} pedidas · {num(ident['executam_hoje'])} executam hoje · "
            f"déficit {num(ident['deficit'])}")
    if rel.nao_executam:
        quem += " (" + ", ".join(f"{m} {n}" for m, n in rel.nao_executam) + ")"
    linhas = [
        "**Critérios da prova** (lidos do relatório do servidor, não à mão)",
        f"Relatório gerado em {gerado} · operação {rel.operacao} · status {status} · ambiente {AMBIENTES[rel.ambiente]}",
        f"Capacidade: {cap}",
        quem,
        f"Custo: {_usd_br(rel.custo_total_usd)} (teto {_usd_br(rel.custo_teto_usd)})",
    ]
    if len(rel.criterios) != ESPERADOS or rel.ignorados:
        extra = f"; {rel.ignorados} ilegível(is) ignorado(s)" if rel.ignorados else ""
        linhas.append(f"Atenção: esperados {ESPERADOS} critérios, vieram {len(rel.criterios)}{extra}.")
    linhas += [_linha_do_criterio(c) for c in rel.criterios]
    # defesa em profundidade: o corpo inteiro passa de novo pelo filtro (os marcadores ficam de fora, são nossos)
    corpo = _limpo("\n".join(linhas))
    return f"{INICIO}\n{corpo}\n{FIM}"


# ------------------------------------------------------------------------------------------------------- a descrição
def _intervalo(desc: str) -> tuple[int, int] | None:
    """Onde está o bloco (do início do marcador de abertura ao fim do de fechamento), ou None se não há. Marcadores
    quebrados (um só, fora de ordem, repetidos) recusam: adivinhar poderia apagar texto do dono."""
    abre, fecha = desc.count(INICIO), desc.count(FIM)
    if abre == 0 and fecha == 0:
        return None
    if abre != 1 or fecha != 1 or desc.index(FIM) < desc.index(INICIO):
        raise Recusa("os marcadores do bloco na descrição estão quebrados (um só, repetidos ou fora de ordem); "
                     "arrume à mão e rode de novo, nada foi gravado")
    return desc.index(INICIO), desc.index(FIM) + len(FIM)


def nova_descricao(desc: str, bloco: str) -> tuple[str, str]:
    """(nova descrição, ação): "criado" (bloco acrescentado ao fim), "substituido" ou "igual" (então devolve `desc` como
    veio: 0 ações). Só o trecho entre os marcadores muda; o resto, byte a byte."""
    intervalo = _intervalo(desc)
    if intervalo is None:
        base = desc.rstrip()
        nova = f"{base}\n\n{bloco}" if base else bloco
        acao = "criado"
    else:
        ini, fim = intervalo
        # o Trello pode devolver \r\n; só a quebra de linha é normalizada na comparação
        if desc[ini:fim].replace("\r\n", "\n") == bloco:
            return desc, "igual"
        nova = desc[:ini] + bloco + desc[fim:]
        acao = "substituido"
    if len(nova) > LIMITE_DESCRICAO:
        raise Recusa(f"a descrição passaria de {LIMITE_DESCRICAO} caracteres; nada foi gravado")
    return nova, acao


async def sincronizar(cl, cartao: str, bloco: str, aplicar: bool) -> str:  # noqa: ANN001 - ClienteTrello do backend
    """Lê o cartão (só GET), confere que é do quadro Execução e, com `aplicar`, grava a descrição. Devolve a ação."""
    ident, quadro = await cl.cartao(cartao)
    if quadro != QUADRO_EXECUCAO:
        raise Recusa("o cartão não está no quadro Execução; nada foi gravado")
    lido = await cl._pedir("GET", f"/1/cards/{ident}", params={"fields": "desc"})  # noqa: SLF001 - como o espelho do deploy
    desc = lido.get("desc") if isinstance(lido, dict) else None
    if not isinstance(desc, str):
        raise Recusa("o Trello não devolveu a descrição do cartão; nada foi gravado")
    nova, acao = nova_descricao(desc, bloco)
    if acao != "igual" and aplicar:
        await cl.atualizar_cartao(ident, desc=nova)
    return acao


# ----------------------------------------------------------------------------------------------------------- entrada
def ler_da_central(operacao_id: str, base: str = CENTRAL) -> dict:
    if not _ID.match(operacao_id):
        raise Recusa("id de operação inválido (esperado op-...)")
    try:
        with urllib.request.urlopen(f"{base}/api/operacoes/{operacao_id}/relatorio", timeout=15) as r:  # noqa: S310
            dados = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise Recusa(f"a central respondeu {exc.code} para o relatório (a rota é do adendo v1.111)") from exc
    except (OSError, ValueError) as exc:
        raise Recusa(f"a central não respondeu ({type(exc).__name__})") from exc
    return dados if isinstance(dados, dict) else {}


def ler_do_arquivo(caminho: Path) -> object:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Recusa(f"arquivo ilegível ({type(exc).__name__})") from exc


def _novo_cliente():  # noqa: ANN202 - o tipo vem do backend do central
    sys.path.insert(0, str(BACKEND_CENTRAL))
    from app.config import EnvSettings  # noqa: PLC0415 - só no modo de rede
    from app.modules.avisos.adapters.trello import ClienteTrello  # noqa: PLC0415
    e = EnvSettings()
    return ClienteTrello(e.trello_api_key.get_secret_value().strip(), e.trello_token.get_secret_value().strip())


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--operacao", help="id da operação (op-...), relatório lido da central")
    ap.add_argument("--arquivo", type=Path, help="JSON do relatório já lido, para ensaio offline")
    ap.add_argument("--central", default=CENTRAL, help="base da API (padrão: o loopback do central)")
    ap.add_argument("--cartao", default=CARTAO_PAI, help="código curto ou id do cartão-pai (padrão: o da prova de 07/10)")
    ap.add_argument("--ler-cartao", action="store_true", help="no ensaio, lê o cartão (só GET) para dizer a ação")
    ap.add_argument("--aplicar", action="store_true", help="grava a descrição do cartão (sem isto, só ensaio)")
    a = ap.parse_args(argv)
    if bool(a.operacao) == bool(a.arquivo):
        ap.error("informe --operacao OU --arquivo (um dos dois)")
    try:
        lido = redacao.recarregar(RAIZ_CENTRAL)  # os nomes de persona do banco do central; o worktree não o tem
        if a.aplicar and not lido:
            raise Recusa("não consegui ler os nomes a esconder (banco do central); nada foi gravado")
        dados = ler_do_arquivo(a.arquivo) if a.arquivo else ler_da_central(a.operacao, a.central)
        bloco = montar_bloco(ler_relatorio(dados))
        print(bloco)
        if not (a.aplicar or a.ler_cartao):
            print("\n[ensaio: nada foi lido do Trello nem gravado; ação: 1 (criar ou substituir o bloco na descrição do "
                  "cartão), ou 0 se ele já estiver igual; veja com --ler-cartao]")
            return 0
        acao = asyncio.run(sincronizar(_novo_cliente(), a.cartao, bloco, a.aplicar))
    except Recusa as exc:
        print(f"nada a escrever: {exc}")
        return 2
    texto = {"criado": "bloco acrescentado ao fim da descrição", "substituido": "bloco da descrição substituído",
             "igual": "bloco já está igual, 0 ações"}[acao]
    print(f"\n{texto}" + ("" if acao == "igual" or a.aplicar else " (ensaio: nada gravado)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
