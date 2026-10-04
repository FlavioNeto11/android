"""Resumo de 20 min ao dono pelo Telegram, montado NA HORA do envio e sem depender de a sessão estar ociosa.

Lição de 03/10: o cron de sessão só dispara com a sessão parada, e a orquestradora ficou 65 min sem enviar durante a
carga do Trello. Este laço roda em segundo plano (Bash `run_in_background`) e, a cada envio, compõe a mensagem de fontes
que ele mesmo mede, para nunca reenviar texto velho:

- `/api/health` do central (estado, commit curto, migração, problemas);
- o plano-100 (`scripts/claude-plan-100.py check` para o total e os implementados; `estado.json` para parciais e
  bloqueados);
- `.claude/handoffs/canais/situacao.json`, mantido pela sessão Canais: deploy no ar, frentes, pendências do dono e
  `mudou_extra` (o "Mudou desde a última", escrito PARA o dono: até 5 linhas inteiras, sem hash nem arquivo). Depois de
  editar, `--carimbar` põe a hora do relógio e marca até onde o eventos foi curado. Se a situação tiver mais de 40 min,
  a mensagem diz a hora em que as frentes foram conferidas;
- o `.claude/handoffs/canais/eventos.md` é canal INTERNO e nunca vai ao dono: dele só se conta quantos fatos ainda não
  foram curados ("N novidades desde HH:MMZ; detalho no próximo resumo"). Depois do envio, `mudou_extra` é esvaziado.

28.31 F3 (o molde do dono): a mensagem abre com "🙋 Precisa de você: N" e a lista (a pendência nova leva 🆕); depois
vem só o que MUDOU desde o último envio (saúde, plano e seu detalhe, frentes e parados só se mudaram). A saúde com
problema que não muda é relembrada no máximo a cada 3 h, com "segue desde HH:MMZ". Leitura que falha (plano, Trello)
mantém o retrato anterior: a volta da leitura não vira novidade. Quando nada mudou e não há pendência nova, NÃO envia:
grava `conferido_em` e espera o próximo intervalo. O retrato do último envio fica no cursor (`retrato`). Quem entra em
"Precisa de você": `docs/dominios/canais.md`.

Cadência (decisão da orquestradora, 04/10 23:14Z, regra da rotina agrupada): a ROTINA sai no máximo uma vez por
`--piso-rotina` (3600 s) desde o último envio, mesmo que algo mude a cada volta; o que muda "Precisa de você" (pendência
nova ou resolvida) e a saúde que piora (🟢 → 🟡 → 🔴) ou volta ao 🟢 (23:18Z e 23:21Z) saem na volta em que mudarem. A rotina segurada não se perde: o retrato do cursor não anda, e a volta
seguinte ao piso conta tudo o que mudou desde o último envio.

O corpo inteiro passa por `_sem_contato` (e-mail, telefone, URL) e por `redacao.redigir` (o mesmo filtro do Trello),
com os nomes relidos do banco do central a cada rodada. O envio é o `telegram_status.py`, que lê token e chat do `.env`
pelo `EnvSettings` e nunca imprime nada deles. O cursor (`resumo_cursor.json`) guarda a hora, o
`message_id` e a linha do eventos do último envio: reiniciar o laço não repete nem pula.

Uso (python do backend/.venv):
  resumo_laco.py --carimbar               carimba situacao.json (hora do relógio, eventos curados até agora)
  resumo_laco.py --ensaio                 compõe e grava `resumo_ensaio.html`, sem enviar e sem mexer no cursor
  resumo_laco.py --uma-vez                compõe e envia uma vez
  resumo_laco.py [--intervalo 1200] [--piso-rotina 3600]
                                          laço: confere a cada `intervalo`; a rotina sai no máximo a cada `piso-rotina`
  resumo_laco.py --armar --desde-linha N --ultimo-envio 2026-10-03T18:40:00Z
                                          grava o cursor inicial (passagem da caixa) e sai
"""
from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(r"C:\git\android")
#: Os dados do laço (cursor, mensagem enviada, ensaio) ficam fora do Git; o código fica em .claude/canais/.
AQUI = RAIZ / ".claude" / "handoffs" / "telegram"
SCRIPTS = Path(__file__).resolve().parent
CANAIS = RAIZ / ".claude" / "handoffs" / "canais"
EVENTOS = CANAIS / "eventos.md"
SITUACAO = CANAIS / "situacao.json"
CURSOR = AQUI / "resumo_cursor.json"
SAIDA = AQUI / "telegram_msg.html"
ENSAIO = AQUI / "resumo_ensaio.html"
PY = RAIZ / "backend" / ".venv" / "Scripts" / "python.exe"
SAUDE = "http://127.0.0.1:8000/api/health"
BRASILIA = timezone(timedelta(hours=-3))
#: o Telegram corta em 4096; deixa folga para o reenvio em texto puro
LIMITE = 3800
#: "Mudou desde a última" é curado pela Canais: no máximo 5 linhas inteiras de ~120 caracteres
MAX_MUDOU = 5
MAX_CHARS_MUDOU = 140

#: a redação do MESMO checkout deste arquivo (a do central pode ser mais velha); os nomes vêm do banco do central
sys.path.insert(0, str(SCRIPTS.parent / "trello"))
import redacao  # noqa: E402
from redacao import redigir  # noqa: E402

#: o problema de saúde que não muda é relembrado no máximo a cada 3 h (decisão da orquestradora, 04/10)
LEMBRAR_SAUDE = timedelta(hours=3)
#: os nomes de persona já foram lidos do banco nesta subida? Antes disso, nada sai (a reserva não basta)
_banco_lido = False


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _hora(dt: datetime) -> str:
    return f"{dt:%H:%M}Z ({dt.astimezone(BRASILIA):%H:%M} Brasília)"


def _ler_json(caminho: Path, padrao: dict) -> dict:
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(padrao)


def _gravar_json(caminho: Path, dado: dict) -> None:
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(json.dumps(dado, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(caminho)


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"\b(?:https?://|www\.)\S+", re.I)
#: telefone com ou sem DDI e DDD: +55 (11) 98765-4321, 11 98765 4321, 11987654321
_TELEFONE = re.compile(r"(?<![\w.])(?:\+\d{1,3}[\s.-]?)?\(?\d{2}\)?[\s.-]?\d{4,5}[\s.-]?\d{4}(?![\w.])")


def _sem_contato(texto: str) -> str:
    """O `redigir` cobre handle, persona e IP, mas deixa passar e-mail (`a@b.com` não é handle), telefone e URL comum.
    Nada disso vai ao dono pelo Telegram; o e-mail sai antes da URL para o domínio não virar link."""
    texto = _EMAIL.sub("[e-mail]", texto)
    texto = _URL.sub("[link]", texto)
    return _TELEFONE.sub("[telefone]", texto)


def _e(texto: object) -> str:
    """Redige e escapa: tudo o que é dinâmico passa por aqui antes de virar HTML do Telegram."""
    return html.escape(redigir(_sem_contato(str(texto))), quote=False)


def _cortar(texto: str, n: int) -> str:
    texto = " ".join(texto.split())
    if len(texto) <= n:
        return texto
    corte = texto[:n].rsplit(" ", 1)[0]
    return corte.rstrip(",;:·-") + "…"


def _saude(deploy: object) -> str:
    """Sem jargão quando está tudo bem; commit e migração só aparecem quando há problema."""
    try:
        with urllib.request.urlopen(SAUDE, timeout=8) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - central fora do ar também é notícia
        return "🔴 <b>A Central não respondeu</b> na hora deste resumo"
    problemas = d.get("problems") or []
    no_ar = f" · deploy {_e(deploy)} no ar" if deploy else ""
    if d.get("status") == "ok" and not problemas:
        return f"🟢 <b>Central saudável</b>{no_ar}"
    commit = str(d.get("commit") or "")[:8]
    migracao = str(d.get("migration") or "").split("_", 1)[0]
    return (f"🟡 <b>Central com {len(problemas)} problema(s)</b>{no_ar} · commit <code>{_e(commit)}</code>"
            f" · migração {_e(migracao)}")


def _plano() -> tuple[str, str]:
    total = feitos = None
    try:
        out = subprocess.run([str(PY), str(RAIZ / "scripts" / "claude-plan-100.py"), "check"], cwd=RAIZ,
                             capture_output=True, timeout=120).stdout.decode("utf-8", "replace")
        m1, m2 = re.search(r"(\d+) itens no plano", out), re.search(r"Implementados: (\d+)", out)
        total, feitos = (int(m1.group(1)), int(m2.group(1))) if m1 and m2 else (None, None)
    except (OSError, subprocess.SubprocessError):
        pass
    if total is None:
        return "", ""  # leitura falhou: `montar` mantém o plano do retrato anterior
    est = _ler_json(RAIZ / ".claude" / "plano-100" / "estado.json", {"itens": {}})["itens"]
    parciais = sum(1 for v in est.values() if v.get("status") == "partial")
    bloqueados = sum(1 for v in est.values() if v.get("status") == "blocked")
    a_fazer = max(total - feitos - parciais - bloqueados, 0)
    pct = round(100 * feitos / total) if total else 0
    return (f"<b>Plano geral: {feitos} de {total} itens concluídos ({pct} %)</b>",
            f"{parciais} parciais · {bloqueados} bloqueados · {a_fazer} a fazer")


#: As duas formas de fato no eventos.md: a linha de tabela (`04/10 12:13Z | Frente | …`) e, desde 03/10, a da
#: orquestradora (`- 16:36Z (04/10) orquestradora: …`). As duas contam, na ordem do arquivo.
_FATO_EM_LISTA = re.compile(r"^- \d{1,2}:\d{2}Z\b")
#: A linha de tabela que é fato começa pela hora, com ou sem a data ("04/10 08:14Z |" ou "18:36Z |"); cabeçalho,
#: separador `|---|` e prosa com "|" não contam (revisão do #327).
_FATO_EM_TABELA = re.compile(r"^(?:\d{1,2}/\d{1,2} )?\d{1,2}:\d{2}Z\s*\|")
_COLUNA_DA_HORA = re.compile(r"^(?:\d{1,2}/\d{1,2} )?\d{1,2}:\d{2}Z$")
_HORA_DO_FATO = re.compile(r"\b\d{1,2}:\d{2}Z\b")


def _e_fato(linha: str) -> bool:
    return bool(_FATO_EM_TABELA.match(linha) or _FATO_EM_LISTA.match(linha))


def _linhas_de_fato() -> list[str]:
    try:
        linhas = EVENTOS.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [x for x in linhas if _e_fato(x)]


def _hora_do_fato(linha: str) -> str:
    """A hora do fato, nas duas formas. Na linha de tabela vale a 1ª coluna (com a data, quando houver), e só se ela
    for uma hora; na lista, a hora logo depois do "- ". Texto livre do eventos.md nunca chega ao "N novidades desde …"
    (revisão do #327)."""
    if _FATO_EM_TABELA.match(linha):
        coluna = linha.split("|", 1)[0].strip()
        return coluna if _COLUNA_DA_HORA.match(coluna) else ""
    m = _HORA_DO_FATO.match(linha, 2) if _FATO_EM_LISTA.match(linha) else None
    return m.group(0) if m else ""


def _nao_curados(curados_ate: int) -> tuple[int, str]:
    """O eventos.md é canal interno (escrito para a Canais): nunca vai ao dono. Só se conta o que ainda não foi curado."""
    novos = _linhas_de_fato()[curados_ate:]
    if not novos:
        return 0, ""
    return len(novos), _hora_do_fato(novos[0])


#: listas do quadro Execução onde cartão parado é sinal de esquecimento (04/10: no lugar do teto do List Limits)
LISTAS_VIGIADAS = ("em_execucao", "em_validacao")
PARADO = timedelta(hours=48)


def _parados(agora: datetime) -> tuple[int, list[str]] | None:
    """Cartões das listas vigiadas sem movimento há mais de 48 h e SEM data de espera (`due`): quem espera uma data
    conhecida (11/10, 17/10…) leva o `due` no cartão e não conta. Leitura pela API do Trello com o cliente do
    `powerups.py` (autenticação no cabeçalho, nunca na URL nem impressa). `None` = a leitura falhou nesta rodada."""
    try:
        import powerups  # noqa: PLC0415 - só carrega o .env quando o resumo precisa

        listas = powerups.EST["quadros"]["execucao"]["listas"]
        vigiadas = {powerups._id(listas[k]) for k in LISTAS_VIGIADAS}
        with powerups._cliente() as c:
            r = c.get(f"/boards/{powerups.QUADRO['execucao']}/cards",
                      params={"filter": "open", "fields": "name,idList,due,dateLastActivity"})
            r.raise_for_status()
            cartoes = r.json()
    except Exception:  # noqa: BLE001 - o resumo segue sem a linha; a falha não pode derrubar o laço
        return None
    parados = []
    for k in cartoes:
        if k.get("idList") not in vigiadas or k.get("due"):
            continue
        try:
            ultima = datetime.fromisoformat(str(k.get("dateLastActivity")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if agora - ultima > PARADO:
            parados.append((ultima, str(k.get("name") or "")))
    parados.sort()
    return len(parados), [nome for _, nome in parados[:3]]


def ler_estado(agora: datetime, ja_contado: int = 0) -> dict:
    """O retrato de agora, medido na hora (saúde, plano, Trello) e lido da situação curada pela Canais. É a única parte
    com leitura de fora; `montar` é pura. `ja_contado` é a linha do eventos até onde o último envio já contou: o que
    ele disse ("N novidades") não se repete no próximo."""
    sit = _ler_json(SITUACAO, {})
    titulo, detalhe = _plano()
    parados = _parados(agora)
    n, desde = _nao_curados(max(int(sit.get("eventos_curados_ate", 0)), ja_contado))
    return {
        "saude": _saude(sit.get("deploy_no_ar")),
        "plano": titulo, "plano_detalhe": detalhe,
        "frentes": {str(f.get("nome", "")): str(f.get("linha", "")) for f in (sit.get("frentes") or [])},
        "parados": None if parados is None else list(parados[1]), "n_parados": None if parados is None else parados[0],
        "pendencias": [str(p) for p in (sit.get("pendencias") or [])],
        "mudou": [str(x) for x in (sit.get("mudou_extra") or [])][:MAX_MUDOU],
        "nao_curados": n, "nao_curados_desde": desde,
        "eventos_linha": len(_linhas_de_fato()),
    }


#: O que fica no cursor para o próximo resumo dizer só o que mudou (28.31 F3).
CHAVES_DO_RETRATO = ("saude", "plano", "plano_detalhe", "frentes", "parados", "pendencias")


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _de_iso(valor: object) -> datetime | None:
    """A hora com fuso, ou `None`: a sem fuso (sem o "Z") não se compara com o relógio e derrubaria o laço."""
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else None


def _retrato(estado: dict, ant: dict) -> dict:
    retrato = {k: estado.get(k) for k in CHAVES_DO_RETRATO}
    # Leitura que falhou (`None` no Trello, plano vazio) não apaga o que se sabia: sem isso, a volta da leitura mostraria
    # todo cartão parado como novo e repetiria o plano.
    if estado.get("parados") is None:
        retrato["parados"] = ant.get("parados")
    if not estado.get("plano"):
        retrato["plano"], retrato["plano_detalhe"] = ant.get("plano"), ant.get("plano_detalhe")
    return retrato


def _linha_da_saude(saude: str, ant: dict, agora: datetime) -> tuple[str | None, dict]:
    """A saúde entra quando MUDA. O problema que dura sem mudar volta no máximo a cada `LEMBRAR_SAUDE`, com a hora em
    que começou. Devolve a linha (ou `None`) e o `desde` e `lembrada_em` que vão para o retrato."""
    if saude != ant.get("saude"):
        return saude, {"saude_desde": _iso(agora), "saude_lembrada_em": _iso(agora)}
    marcas = {"saude_desde": ant.get("saude_desde"), "saude_lembrada_em": ant.get("saude_lembrada_em")}
    if saude.startswith("🟢"):
        return None, marcas
    lembrada = _de_iso(marcas["saude_lembrada_em"])
    if lembrada is not None and agora - lembrada < LEMBRAR_SAUDE:
        return None, marcas
    desde = _de_iso(marcas["saude_desde"])
    linha = saude + (f" · segue desde {desde:%H:%M}Z" if desde else "")
    return linha, {**marcas, "saude_lembrada_em": _iso(agora)}


def montar(estado: dict, anterior: dict | None, agora: datetime, desde: str | None = None) -> tuple[str | None, dict]:
    """28.31 F3, molde do dono: abre com "Precisa de você" e a lista; depois só o que MUDOU desde o último envio
    (saúde, plano, frentes e parados só se mudaram; a saúde com problema sempre, porque é o crítico); e devolve `None`
    quando nada mudou e não há pendência nova. Pura: recebe o retrato de agora e o do último envio."""
    ant = anterior or {}
    pend = estado.get("pendencias") or []
    pend_antes = set(ant.get("pendencias") or [])
    novas = [p for p in pend if p not in pend_antes]
    resolvidas = len(pend_antes - set(pend)) if anterior is not None else 0

    mudou: list[str] = []
    saude = str(estado.get("saude") or "")
    marcas_da_saude: dict = {}
    if saude:
        linha, marcas_da_saude = _linha_da_saude(saude, ant, agora)
        if linha:
            mudou.append(linha)
    detalhe = estado.get("plano_detalhe")
    if estado.get("plano") and (estado.get("plano") != ant.get("plano") or detalhe != ant.get("plano_detalhe")):
        mudou.append(str(estado["plano"]) + (f" · {detalhe}" if detalhe else ""))
    frentes_antes = ant.get("frentes") or {}
    for nome, linha in (estado.get("frentes") or {}).items():
        if frentes_antes.get(nome) != linha:
            mudou.append(f"▪️ <b>{_e(nome)}</b> · {_e(linha)}")
    parados = estado.get("parados")
    if parados is not None:
        novos_parados = [x for x in parados if x not in set(ant.get("parados") or [])]
        if novos_parados:
            mudou.append(f"⏳ {estado.get('n_parados')} cartão(ões) parado(s) há mais de 48 h, sem data de espera; novos:")
            mudou += [f"▪️ {_e(_cortar(x, MAX_CHARS_MUDOU))}" for x in novos_parados]
    # "Mudou" escrito pela Canais (`mudou_extra`); o laço não inventa texto.
    mudou += [f"▪️ {_e(_cortar(x, MAX_CHARS_MUDOU))}" for x in estado.get("mudou") or []]
    n = int(estado.get("nao_curados") or 0)
    if n:
        mudou.append(f"▪️ {n} {'novidade' if n == 1 else 'novidades'} desde {_e(estado.get('nao_curados_desde'))};"
                     " detalho no próximo resumo")
    if resolvidas:
        mudou.append(f"✔️ {resolvidas} {'pendência sua saiu' if resolvidas == 1 else 'pendências suas saíram'} da lista")

    retrato = {**_retrato(estado, ant), **marcas_da_saude}
    if anterior is not None and not novas and not mudou:
        return None, retrato

    partes = [f"<b>📊 ANA · Resumo das {_hora(agora)}</b>", ""]
    if pend:
        partes.append(f"<b>🙋 Precisa de você: {len(pend)}</b>")
        partes += [f"▪️ {'🆕 ' if p in novas and anterior is not None else ''}{_e(p)}" for p in pend]
    else:
        partes.append("<b>🙋 Nada espera você agora.</b>")
    if mudou:
        # Sem retrato anterior (o 1º envio do F3), não há "desde": é o retrato inteiro.
        partes += ["", f"<b>Mudou desde {_e(desde)}</b>" if desde and anterior is not None else "<b>Como está</b>"]
        partes += mudou
    texto = "\n".join(partes)
    if len(texto) > LIMITE:
        texto = texto[:LIMITE].rsplit("\n", 1)[0] + "\n<i>(cortado)</i>"
    return texto, retrato


def _desde(cursor: dict) -> str | None:
    try:
        return f"{datetime.fromisoformat(str(cursor.get('enviado_em')).replace('Z', '+00:00')):%H:%M}Z"
    except ValueError:
        return None


_GRAVIDADE = {"🟢": 0, "🟡": 1, "🔴": 2}


def _gravidade(retrato: dict | None) -> int:
    """🟢 0 < 🟡 1 < 🔴 2. Sem retrato anterior conta como 🟢: a Central já ruim no primeiro envio é uma piora a contar.
    Texto que não começa por nenhum dos três conta como o pior: na dúvida, o dono fica sabendo."""
    if retrato is None:
        return 0
    saude = str(retrato.get("saude") or "🟢")
    return next((g for marca, g in _GRAVIDADE.items() if saude.startswith(marca)), max(_GRAVIDADE.values()))


def seguro_ate(retrato: dict, anterior: dict | None, enviado_em: str | None, agora: datetime,
               piso_s: int) -> datetime | None:
    """A cadência: até quando a rotina fica segura pelo piso, ou `None` (sai já). Furam o piso, na volta em que mudam:
    "Precisa de você" (pendência nova ou resolvida) e a saúde cuja gravidade SOBE (🟢 → 🟡 → 🔴) ou volta ao 🟢, uma
    vez por transição (o retrato só anda no envio). A que desce sem chegar ao 🟢 (🔴 → 🟡) e a que segue igual, mesmo
    com outro texto, ficam com o piso. Sem envio anterior válido, sai. Pura."""
    if set(retrato.get("pendencias") or []) != set((anterior or {}).get("pendencias") or []):
        return None
    agora_g, antes_g = _gravidade(retrato), _gravidade(anterior)
    if agora_g > antes_g or (agora_g == 0 and antes_g > 0):
        return None
    ultimo = _de_iso(enviado_em)
    if ultimo is None:
        return None
    ate = ultimo + timedelta(seconds=piso_s)
    return ate if agora < ate else None


def pode_enviar(retrato: dict, anterior: dict | None, enviado_em: str | None, agora: datetime, piso_s: int) -> bool:
    return seguro_ate(retrato, anterior, enviado_em, agora, piso_s) is None


def compor(cursor: dict, agora: datetime) -> tuple[str | None, dict | None, int]:
    """O texto a enviar (ou `None`: nada mudou), o retrato para o cursor e até onde o eventos foi contado. Retrato `None`:
    os nomes de persona ainda não foram lidos do banco nesta subida, e nada sai (nem se grava)."""
    global _banco_lido  # noqa: PLW0603 - o estado da subida do laço
    if redacao.recarregar(RAIZ):  # persona criada depois de o laço subir também sai
        _banco_lido = True
    elif not _banco_lido:
        print(f"{agora:%H:%M:%S}Z banco do central não lido nesta subida: sem envio", flush=True)
        return None, None, int(cursor.get("eventos_linha") or 0)
    else:  # falha transitória: a redação segue com a lista da última leitura inteira
        print(f"{agora:%H:%M:%S}Z aviso: banco do central não lido; vale a lista da última leitura", flush=True)
    estado = ler_estado(agora, int(cursor.get("eventos_linha") or 0))
    texto, retrato = montar(estado, cursor.get("retrato"), agora, _desde(cursor))
    return texto, retrato, int(estado["eventos_linha"])


def enviar(texto: str) -> int | None:
    SAIDA.write_text(texto, encoding="utf-8")
    r = subprocess.run([str(PY), str(SCRIPTS / "telegram_status.py"), str(SAIDA)], cwd=RAIZ, capture_output=True,
                       timeout=90)
    saida = r.stdout.decode("utf-8", "replace").strip()
    m = re.search(r"message_id=(\d+)", saida)
    if r.returncode == 0 and m:
        return int(m.group(1))
    # a saída do telegram_status já é limpa (nunca leva token nem chat); só a primeira linha
    print(f"{_agora():%H:%M:%S}Z falhou: {saida.splitlines()[0] if saida else r.returncode}", flush=True)
    return None


def rodada(cursor: dict, piso_s: int = 0) -> dict | None:
    """Uma volta. Nada mudou, ou só rotina antes do piso: não envia; grava só `conferido_em`, e o próximo resumo segue
    contando do último envio."""
    agora = _agora()
    texto, retrato, linhas = compor(cursor, agora)
    if retrato is None:
        return None  # sem os nomes do banco: o laço tenta de novo em 2 min, sem marcar a rodada
    segura = texto is not None and not pode_enviar(retrato, cursor.get("retrato"), cursor.get("enviado_em"), agora,
                                                   piso_s)
    if texto is None or segura:
        novo = {**cursor, "conferido_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ")}
        _gravar_json(CURSOR, novo)
        print(f"{agora:%H:%M:%S}Z {'só rotina antes do piso' if segura else 'nada mudou'}: sem envio", flush=True)
        return novo
    mid = enviar(texto)
    if mid is None:
        return None
    novo = {"enviado_em": agora.strftime("%Y-%m-%dT%H:%M:%SZ"), "message_id": mid, "eventos_linha": linhas,
            "retrato": retrato}
    _gravar_json(CURSOR, novo)
    # o que foi contado já foi dito: o próximo resumo só traz o que a Canais curar depois deste envio
    sit = _ler_json(SITUACAO, {})
    if sit.get("mudou_extra"):
        sit["mudou_extra"] = []
        _gravar_json(SITUACAO, sit)
    print(f"{agora:%H:%M:%S}Z enviado message_id={mid} ({len(texto)} chars)", flush=True)
    return novo


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ensaio", action="store_true")
    ap.add_argument("--uma-vez", action="store_true")
    ap.add_argument("--intervalo", type=int, default=1200)
    ap.add_argument("--piso-rotina", type=int, default=3600,
                    help="a rotina sai no máximo uma vez a cada N s; o que muda Precisa de você sai já")
    ap.add_argument("--armar", action="store_true")
    ap.add_argument("--desde-linha", type=int, default=None)
    ap.add_argument("--ultimo-envio", default=None)
    ap.add_argument("--carimbar", action="store_true",
                    help="depois de editar situacao.json: hora do relógio em atualizado_em e eventos curados até agora")
    args = ap.parse_args()
    cursor = _ler_json(CURSOR, {"eventos_linha": 0})

    if args.armar:
        if args.desde_linha is None or not args.ultimo_envio:
            print("--armar pede --desde-linha e --ultimo-envio")
            return 2
        if _de_iso(args.ultimo_envio) is None:
            print(f"--ultimo-envio {args.ultimo_envio!r}: use a hora UTC com o Z, como 2026-10-04T23:10:48Z")
            return 2
        _gravar_json(CURSOR, {"enviado_em": args.ultimo_envio, "message_id": None,
                              "eventos_linha": args.desde_linha})
        print(f"cursor armado: linha {args.desde_linha}, último envio {args.ultimo_envio}")
        return 0
    if args.carimbar:
        sit = _ler_json(SITUACAO, {})
        sit["atualizado_em"] = _agora().strftime("%Y-%m-%dT%H:%M:%SZ")
        sit["eventos_curados_ate"] = len(_linhas_de_fato())
        _gravar_json(SITUACAO, sit)
        print(f"situação carimbada às {sit['atualizado_em']}, eventos curados até {sit['eventos_curados_ate']}")
        return 0
    if args.ensaio:
        texto, retrato, linhas = compor(cursor, _agora())
        if retrato is None:
            print("ensaio: banco do central não lido; o laço NÃO enviaria")
            return 1
        if texto is None:
            print("ensaio: nada mudou desde o último envio; o laço NÃO enviaria")
            return 0
        ENSAIO.write_text(texto, encoding="utf-8")
        print(f"ensaio gravado ({len(texto)} chars, eventos até a linha {linhas}); nada enviado")
        ate = seguro_ate(retrato, cursor.get("retrato"), cursor.get("enviado_em"), _agora(), args.piso_rotina)
        if ate is not None:
            print(f"ensaio: só rotina; o laço seguraria pelo piso até {ate:%H:%M}Z")
        return 0
    if args.uma_vez:
        return 0 if rodada(cursor, args.piso_rotina) else 1

    print(f"{_agora():%H:%M:%S}Z laço do resumo ligado, a cada {args.intervalo} s, rotina no máximo a cada "
          f"{args.piso_rotina} s", flush=True)
    while True:
        # A última volta, com ou sem envio: a que não enviou (nada mudou) também espera o intervalo inteiro.
        horas = [h for h in (_de_iso(cursor.get(k)) for k in ("enviado_em", "conferido_em")) if h is not None]
        ultimo = max(horas) if horas else _agora() - timedelta(seconds=args.intervalo)
        espera = (ultimo + timedelta(seconds=args.intervalo) - _agora()).total_seconds()
        if espera > 0:
            time.sleep(min(espera, 60))
            cursor = _ler_json(CURSOR, cursor)  # o cursor pode ser rearmado por fora
            continue
        novo = rodada(cursor, args.piso_rotina)
        if novo is None:
            time.sleep(120)  # falha de envio ou banco não lido: tenta de novo em 2 min, sem pular a rodada
            continue
        cursor = novo


if __name__ == "__main__":
    raise SystemExit(main())
