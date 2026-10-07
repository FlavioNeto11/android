"""28.66: UM resumo consolidado de uma rodada de operação (31.154), para o Telegram do dono.

Em vez de um aviso por alvo, a rodada inteira vira uma mensagem curta (HTML do Telegram, até 12 linhas, no molde dos
avisos de deploy: assunto, resultado com números, `Crítico:` e `Espera você:`). A fonte é o JSON de uma operação
ENCERRADA, como a central o devolve em `GET /api/operacoes/<id>` (`ServicoDeOperacoes.ler`) ou de um arquivo salvo
(`--arquivo`), para ensaiar sem a central.

O corpo é montado SÓ de números, vocabulário fixo e do motivo agrupado (`capacidade.motivos`). Nunca entram: nome ou
@handle de persona ou de conta, id de persona, aparelho ou execução, o comando, o assunto, as fontes, os parâmetros nem
o texto de comentário, legenda ou DM (`alvos[].resultado.texto`). Defesa em profundidade, porque o motivo é texto livre:
os nomes e handles que o próprio JSON traz (`persona_nome`, `conta`) são tirados do motivo, e o corpo inteiro passa por
`_sem_contato` (e-mail, telefone, URL) e por `redacao.redigir` (o filtro do Trello, com os nomes relidos do banco do
central). O único link que sai é o do painel, o do `avisos.url_painel` do config (ou `--painel`), posto DEPOIS da redação.

Uso (python do backend/.venv):
  resumo_rodada.py <operacao_id>              ensaio: lê a central e IMPRIME o HTML (não envia nada)
  resumo_rodada.py --arquivo operacao.json    ensaio offline, sem central
  resumo_rodada.py <id> --enviar              grava um arquivo temporário, chama `telegram_status.py` e imprime o
                                              message_id (só com o sinal da orquestradora; recusa operação em curso)
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

#: o central (config, banco dos nomes, python do backend): como o `resumo_laco.py`, não o checkout onde este arquivo está
RAIZ = Path(r"C:\git\android")
SCRIPTS = Path(__file__).resolve().parent
PY = RAIZ / "backend" / ".venv" / "Scripts" / "python.exe"
CENTRAL = "http://127.0.0.1:8000"
MAX_LINHAS = 12
#: o Telegram corta em 4096; o resumo é curto, mas o motivo é texto livre
LIMITE = 3800
MAX_MOTIVOS = 4
MAX_CHARS_MOTIVO = 60
ENCERRADAS = {"concluida", "concluida_com_bloqueios", "cancelada"}
#: o id que a central gera (`op-AAAAMMDDHHMMSS-xxxxxx`); nada fora disso vai para a URL
_ID = re.compile(r"^op-[A-Za-z0-9-]{1,60}$")
#: o `tipo` da ação é o id de uma capability (`comment_post`); fora desse formato vira "outra"
_TIPO = re.compile(r"^[a-z][a-z0-9_.-]{0,39}$")

sys.path.insert(0, str(SCRIPTS.parent / "trello"))
sys.path.insert(0, str(SCRIPTS))
import redacao  # noqa: E402
from redacao import redigir  # noqa: E402
from resumo_laco import _sem_contato  # noqa: E402


class Recusa(Exception):
    """A entrada não serve para um resumo (operação em curso, JSON de outro formato, central fora do ar)."""


def _e(texto: object) -> str:
    """Redige e escapa: tudo o que é dinâmico passa por aqui antes de virar HTML do Telegram."""
    return html.escape(redigir(_sem_contato(str(texto))), quote=False)


def _num(valor: object) -> int:
    try:
        return max(0, int(valor))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _usd(valor: object) -> str:
    try:
        return f"US$ {float(valor):.2f}".replace(".", ",")  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""


def _hora(valor: object) -> datetime | None:
    if not isinstance(valor, str) or not valor:
        return None
    try:
        dt = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _duracao(op: dict) -> str:
    ini, fim = _hora(op.get("created_at")), _hora(op.get("finished_at"))
    if ini is None or fim is None or fim < ini:
        return ""
    s = int((fim - ini).total_seconds())
    h, resto = divmod(s, 3600)
    m, seg = divmod(resto, 60)
    if h:
        return f"{h} h {m:02d} min"
    return f"{m} min" if m else f"{seg} s"


def _nomes_do_json(op: dict) -> list[str]:
    """Os nomes e handles que o PRÓPRIO JSON traz; o motivo (texto livre) é lido sem eles. Parte solta do nome só com 4+
    letras, para não rasgar as palavras comuns."""
    nomes: set[str] = set()
    for a in op.get("alvos") or []:
        if not isinstance(a, dict):
            continue
        for campo in ("persona_nome", "conta"):
            v = a.get(campo)
            if isinstance(v, str) and v.strip():
                v = v.strip().lstrip("@")
                nomes.add(v)
                if campo == "persona_nome":
                    nomes.update(p for p in re.split(r"\s+", v) if len(p) >= 4)
    return sorted(nomes, key=len, reverse=True)


def _sem_nomes(texto: str, nomes: list[str]) -> str:
    for n in nomes:
        texto = re.sub(rf"@?(?<!\w){re.escape(n)}(?!\w)", "[persona]", texto, flags=re.I)
    return texto


def _motivo(texto: str, nomes: list[str]) -> str:
    texto = " ".join(_sem_nomes(texto, nomes).split())
    return texto if len(texto) <= MAX_CHARS_MOTIVO else texto[:MAX_CHARS_MOTIVO].rsplit(" ", 1)[0].rstrip(",;:·-") + "…"


def _contagens(op: dict) -> dict:
    """Só números do JSON: capacidade (a da central, lida do mesmo instante), ações por tipo e as preparadas à espera."""
    cap = op.get("capacidade") if isinstance(op.get("capacidade"), dict) else {}
    alvos = [a for a in (op.get("alvos") or []) if isinstance(a, dict)]
    executadas: Counter[str] = Counter()
    preparadas = sem_verificar = 0
    for a in alvos:
        estagios = {e.get("estagio") for e in (a.get("estagios") or []) if isinstance(e, dict)}
        acao = ((a.get("resultado") or {}).get("acao_final") or {}) if isinstance(a.get("resultado"), dict) else {}
        tipo = str(acao.get("tipo") or "")
        tipo = tipo if _TIPO.match(tipo) else "outra"
        if acao.get("verificada") is True:
            executadas[tipo] += 1
        elif estagios & {"acao_executada", "resultado_verificado"} and acao:
            sem_verificar += 1  # a ação rodou, mas a verificação não fechou: não conta como feita
        elif a.get("estagio") == "acao_preparada" or a.get("motivo") == "aguarda liberação":
            if a.get("estado") != "cancelado":
                preparadas += 1
    motivos = cap.get("motivos") if isinstance(cap.get("motivos"), dict) else {}
    return {"solicitados": _num(cap.get("solicitados") if cap else len(alvos)),
            "contas": _num(cap.get("contas_existentes")), "sessoes": _num(cap.get("sessoes_validas")),
            "concluidas": _num(cap.get("concluidas")), "bloqueadas": _num(cap.get("bloqueadas")),
            "em_curso": _num(cap.get("em_curso")), "motivos": {str(k): _num(v) for k, v in motivos.items()},
            "executadas": dict(executadas), "sem_verificar": sem_verificar, "preparadas": preparadas}


def _lista(contagem: dict[str, int], rotulo: Callable[[str], str]) -> str:
    itens = sorted(contagem.items(), key=lambda kv: (-kv[1], kv[0]))
    partes = [f"{n} {rotulo(k)}" for k, n in itens[:MAX_MOTIVOS]]
    resto = sum(n for _, n in itens[MAX_MOTIVOS:])
    if resto:
        partes.append(f"{resto} outros")
    return "; ".join(partes)


def _link_do_painel(base: str | None) -> str:
    """O painel do dono (`avisos.url_painel`), só `https://` ou `http://` sem usuário, sem espaço e sem aspas."""
    base = (base or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s\"'<>@]+$", base):
        return ""
    return f'<a href="{html.escape(base, quote=True)}/#/operacoes">Abrir a Operação no painel</a>'


def montar(op: dict, *, painel: str | None = None) -> str:
    """O HTML do Telegram. Levanta `Recusa` se o JSON não é de uma operação encerrada."""
    if not isinstance(op, dict) or not isinstance(op.get("capacidade"), dict):
        raise Recusa("o JSON não é uma operação da central (falta `capacidade`)")
    status = str(op.get("status") or "")
    if status not in ENCERRADAS or not op.get("finished_at"):
        raise Recusa(f"a operação não está encerrada (status {status or 'ausente'}); nada a resumir")
    c = _contagens(op)
    nomes = _nomes_do_json(op)
    custo = op.get("custo") if isinstance(op.get("custo"), dict) else {}
    teto, total = op.get("max_usd"), custo.get("total_usd")

    titulo = {"concluida": "concluída", "concluida_com_bloqueios": "concluída com bloqueios",
              "cancelada": "cancelada"}[status]
    app = _e(op.get("app_id") or "app")
    modo = "só preparou o texto" if op.get("acao_final") == "preparar" else "executou a ação"
    linhas = [f"<b>Rodada de operação {titulo}</b> ({app}; {modo})"]
    linhas.append(f"• <b>Resultado:</b> {c['solicitados']} solicitados, {c['contas']} com conta, {c['sessoes']} com "
                  f"sessão, {c['concluidas']} concluídos, {c['bloqueadas']} bloqueados"
                  + (f", {c['em_curso']} ainda em curso" if c["em_curso"] else "") + ".")
    if c["motivos"]:
        motivos = {_motivo(k, nomes): n for k, n in c["motivos"].items()}
        linhas.append("• <b>Bloqueios por motivo:</b> " + _e(_lista(motivos, lambda k: k)) + ".")
    acoes = []
    if c["executadas"]:
        acoes.append(_e(_lista(c["executadas"], lambda k: f"{k} (verificada)")))
    if c["sem_verificar"]:
        acoes.append(f"{c['sem_verificar']} executada(s) sem verificação")
    if c["preparadas"]:
        acoes.append(f"{c['preparadas']} preparada(s), sem executar")
    linhas.append("• <b>Ações:</b> " + ("; ".join(acoes) if acoes else "nenhuma") + ".")
    extra = []
    if _usd(total):
        extra.append(f"custo {_usd(total)}" + (f" de um teto de {_usd(teto)}" if _usd(teto) else ""))
    if _duracao(op):
        extra.append(f"duração {_duracao(op)}")
    if extra:
        junto = ", ".join(extra)
        linhas.append("• " + junto[0].upper() + junto[1:] + ".")

    criticos = []
    if status == "cancelada":
        criticos.append("a operação foi cancelada")
    if c["motivos"].get("teto de custo") or (isinstance(total, (int, float)) and isinstance(teto, (int, float))
                                             and teto > 0 and total >= teto):
        criticos.append("o teto de custo foi atingido")
    if c["motivos"].get("aparelho indisponível"):
        criticos.append(f"{c['motivos']['aparelho indisponível']} alvo(s) sem aparelho disponível")
    linhas.append("• <b>Crítico:</b> " + (_e("; ".join(criticos)) if criticos else "nada") + ".")
    espera = c["preparadas"]
    linhas.append("• <b>Espera você:</b> " + (f"o sim de {espera} ação(ões) preparada(s), pela Operação do painel"
                                              if espera else "nada") + ".")
    corpo = redigir(_sem_contato("\n".join(linhas)))  # defesa em profundidade, sobre o corpo inteiro
    link = _link_do_painel(painel)
    if link:
        corpo += "\n" + link  # o ÚNICO link do texto, depois da redação
    if len(corpo.splitlines()) > MAX_LINHAS or len(corpo) > LIMITE:
        raise Recusa("o resumo passou do limite de linhas ou de tamanho")
    return corpo


# ---------------------------------------------------------------------------------------------------------- entrada
def ler_da_central(operacao_id: str, base: str = CENTRAL) -> dict:
    if not _ID.match(operacao_id):
        raise Recusa("id de operação inválido (esperado op-...)")
    try:
        with urllib.request.urlopen(f"{base}/api/operacoes/{operacao_id}", timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise Recusa(f"a central respondeu {exc.code} para a operação") from exc
    except (OSError, ValueError) as exc:
        raise Recusa(f"a central não respondeu ({type(exc).__name__})") from exc


def ler_do_arquivo(caminho: Path) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Recusa(f"arquivo ilegível ({type(exc).__name__})") from exc


def painel_do_config() -> str | None:
    """`avisos.url_painel` do config do central, pelo `url_painel.py`; nada de mais do arquivo é lido ou impresso."""
    try:
        import url_painel
        valor = url_painel._valor_carregado((RAIZ / "config" / "config.yaml").read_text(encoding="utf-8"))  # noqa: SLF001
    except Exception:  # noqa: BLE001 - sem config, sem link: o resumo vale igual
        return None
    return valor if isinstance(valor, str) else None


def enviar(texto: str) -> int | None:
    """Manda pelo `telegram_status.py` (token e chat só no `.env` dele). Devolve o message_id, ou None se falhar."""
    with tempfile.TemporaryDirectory() as pasta:
        arquivo = Path(pasta) / "resumo_rodada.html"
        arquivo.write_text(texto, encoding="utf-8")
        exe = PY if PY.exists() else Path(sys.executable)
        r = subprocess.run([str(exe), str(SCRIPTS / "telegram_status.py"), str(arquivo)], cwd=RAIZ, capture_output=True,
                           timeout=90)
    saida = r.stdout.decode("utf-8", "replace").strip()
    m = re.search(r"message_id=(\d+)", saida)
    if r.returncode == 0 and m:
        return int(m.group(1))
    print(f"falhou: {saida.splitlines()[0] if saida else r.returncode}")  # a saída do telegram_status já é limpa
    return None


def main(argv: list[str] | None = None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("operacao_id", nargs="?", help="id da operação (op-...), lido da central")
    ap.add_argument("--arquivo", type=Path, help="JSON de uma operação já lida, para ensaio offline")
    ap.add_argument("--central", default=CENTRAL, help="base da API (padrão: o loopback do central)")
    ap.add_argument("--painel", default=None, help="base do painel para o link (padrão: avisos.url_painel do config)")
    ap.add_argument("--enviar", action="store_true", help="envia ao Telegram do dono (só com o sinal da orquestradora)")
    a = ap.parse_args(argv)
    if bool(a.operacao_id) == bool(a.arquivo):
        ap.error("informe o id da operação OU --arquivo (um dos dois)")
    if a.enviar and a.arquivo:
        ap.error("--enviar só vale com a operação lida da central, não com --arquivo")
    try:
        redacao.recarregar(RAIZ)  # os nomes de persona atuais, do banco do central (sem ele, valem os da reserva)
        op = ler_do_arquivo(a.arquivo) if a.arquivo else ler_da_central(a.operacao_id, a.central)
        texto = montar(op, painel=a.painel if a.painel is not None else painel_do_config())
    except Recusa as exc:
        print(f"nada a enviar: {exc}")
        return 2
    if not a.enviar:
        print(texto)
        print("\n[ensaio: nada foi enviado]")
        return 0
    mid = enviar(texto)
    if mid is None:
        return 1
    print(f"enviado message_id={mid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
