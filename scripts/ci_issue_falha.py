"""Frente GitHub (29.155, C2; 29.187): quando a corrida diária do CI não passa, abre ou atualiza UMA issue única; quando volta a verde, fecha.

Quem chama: `.github/workflows/ci-aviso-de-falha.yml` (gatilho `workflow_run` do workflow "CI", só do evento `schedule`,
em runner HOSPEDADO; nunca no runner próprio do dono). Também roda na mão: `python scripts/ci_issue_falha.py --run-id N --ensaio`
só LÊ o GitHub e imprime a issue que abriria (nada é escrito).

O que faz, na ordem:
  1. lê o run e os jobs pela API (`gh api`), separa os jobs que não passaram (failure, cancelled, timed_out);
  2. para cada um, lê o log do passo que falhou (`gh run view --log-failed`), ignora o despejo do contêiner de serviço
     (linhas "UNKNOWN STEP", que são o log do PostgreSQL) e guarda só os destaques e as últimas linhas;
  3. limpa cada linha POR FORMATO (cor ANSI, hora, `Authorization: ...`, `senha=...`, token do GitHub, IPv4, e-mail e
     pasta de usuário do Windows) e corta em 300 caracteres: nunca por lista de valores conhecidos;
  4. (29.187) lê o log inteiro e junta as linhas "Resumo do job" (29.184: tempo por etapa, minutos cobrados, contagem de
     testes) e lista TODOS os jobs do run com resultado e duração, para a issue dizer onde o tempo foi e o que caiu;
  5. procura uma issue ABERTA do cron (rótulo `ci`, título começando por "CI noturno"): se há, comenta nela com a noite nova
     em vez de abrir outra; se não há, abre com o rótulo `ci`. Nunca atribui ninguém nem põe rótulo de agente (a decisão de
     chamar o agente é do dono);
  6. se o run do cron terminou em SUCESSO e há issue aberta do cron, comenta que voltou a verde e FECHA a issue (só para run
     do evento `schedule`: disparo manual verde não fecha nada).

Falha ou incerteza nunca viram "tudo certo": erro do `gh` derruba o script com código 1 e a mensagem do `gh`. Run que passou
(ou foi pulado) não abre issue; só run do cron em sucesso fecha. O script não lê segredo nem `.env`; o único token é o `GH_TOKEN` do próprio workflow.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime

ROTULO = "ci"
ROTULO_COR = "d93f0b"
ROTULO_DESCRICAO = "Corrida do CI e rede da noite"
CONCLUSOES_RUINS = ("failure", "cancelled", "timed_out", "startup_failure")
LINHAS_PADRAO = 40
LIMITE_LINHA = 300
LIMITE_DESTAQUES = 15
LIMITE_RESUMOS = 20
PREFIXO_ISSUE = "CI noturno"  # título de toda issue do cron: a antiga, por noite ("CI noturno AAAA-MM-DD: ..."), e a única
LIMITE_CORPO = 40_000  # o GitHub aceita 65.536; sobra folga para o cabeçalho e as cercas

# a cor vem como ESC real ou, nos logs de job do GitHub, em notação de acento circunflexo ("^[[31m")
_ANSI = re.compile(r"(?:\x1b|\^\[)\[[0-9;]*[A-Za-z]")
# linha do log do contêiner de serviço (PostgreSQL), que o GitHub despeja no fim do job sem separar do pytest
_SERVICO_PG = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d+ UTC \[\d+\] ")
_HORA = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z\s?")
# Authorization: tudo o que vem depois do separador, até o fim da linha (Bearer, Basic, Digest, AWS4... têm vários campos)
_AUTORIZACAO = re.compile(r"(?i)\b(authorization)[\"']?\s*[=:].*$")
_BEARER_SOLTO = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
# nome com prefixo/sufixo (access_token, client_secret, db_password, AWS_SECRET_ACCESS_KEY) e com aspas (dict impresso:
# {'senha': 'x'}, "password": "x"); o valor entre aspas leva as palavras todas
_CHAVE_VALOR = re.compile(
    r"(?i)(?<![\w-])([\w-]{0,40}(?:senha|password|passwd|secret|segredo|token|api[_-]?key|apikey)[\w-]{0,40})"
    r"([\"']?\s*(?:=|:(?!:))\s*)"
    r"(?!\[oculto\])(\"[^\"]*\"|'[^']*'|\S+)"
)
_TOKEN_GITHUB = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,})\b")
_CHAVE_SK = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
_CHAVES_DE_NUVEM = re.compile(r"\b(AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|xox[abprs]-[A-Za-z0-9-]{10,})\b")
_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*$")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"\b(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}\b")
# local da parte limitado (64): sem isso a linha longa de caracteres de palavra sem "@" custa tempo quadrático
_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]+(?:\.[\w-]+)+")
_CPF = re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
_CNPJ = re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")
_TELEFONE = re.compile(r"\+\d{2}[\s-]?\(?\d{2}\)?[\s-]?\d{4,5}[\s-]?\d{4}\b")
# C:\Users\x, C:\\Users\\x (repr do Python), C:/Users/x e /c/Users/x
_PASTA_USUARIO = re.compile(r"(?i)((?:[A-Z]:|/[a-z])?[\\/]+Users[\\/]+)[^\\/\s'\"]+")
_DESTAQUE = re.compile(
    r"^(FAILED |ERROR |ERRO:|Found \d+ errors?|FAIL |.*\b\d+ (failed|errors?)\b.*\b(passed|in \d)|.*Failed Tests \d+)"
    r"|##\[error\]"
)
_RESUMO_PYTEST = re.compile(r"\b\d+ (failed|passed|error)\b.* in \d")
# linha do passo "Resumo do job" (scripts/pr_leve_resumo.py e os passos do ci.yml): "**job**: etapa 3 s · ... · soma das etapas 156 s"
_RESUMO_JOB = re.compile(r"^\*\*[^*]{1,60}\*\*: .*soma das etapas \d+ s")
_RUN_ID = re.compile(r"^\d{1,20}$")
_NOME_PERIGOSO = re.compile(r"[@#<>`\r\n\x00-\x1f]")
LIMITE_ANTES_DA_LIMPEZA = 4000  # a linha é cortada ANTES dos regex (custo limitado) e de novo depois, em LIMITE_LINHA

Gh = Callable[..., str]


def nome_seguro(nome: str) -> str:
    """Nome de job ou de passo para título e cabeçalho: sem `@` (menção), `#` (link para issue), crase, `<`/`>` e quebra."""
    return " ".join(_NOME_PERIGOSO.sub(" ", nome).split())[:120]


def limpar_linha(linha: str) -> str:
    """Tira cor e hora, esconde o que tem FORMATO de segredo ou de dado de pessoa e corta a linha."""
    texto = _ANSI.sub("", linha[:LIMITE_ANTES_DA_LIMPEZA]).rstrip("\r\n")
    texto = _HORA.sub("", texto)
    texto = _AUTORIZACAO.sub(lambda m: f"{m.group(1)}: [oculto]", texto)
    texto = _PEM.sub("[chave privada oculta]", texto)
    texto = _BEARER_SOLTO.sub(lambda m: f"{m.group(1)} [oculto]", texto)
    texto = _CHAVE_VALOR.sub(lambda m: f"{m.group(1)}{m.group(2)}[oculto]", texto)
    for formato in (_TOKEN_GITHUB, _CHAVE_SK, _JWT, _CHAVES_DE_NUVEM):
        texto = formato.sub("[oculto]", texto)
    texto = _EMAIL.sub("[email]", texto)
    texto = _CNPJ.sub("[documento]", texto)
    texto = _CPF.sub("[documento]", texto)
    texto = _TELEFONE.sub("[telefone]", texto)
    texto = _IPV4.sub("[ip]", texto)
    texto = _IPV6.sub("[ip]", texto)
    texto = _PASTA_USUARIO.sub(lambda m: f"{m.group(1)}[usuario]", texto)
    texto = texto.replace("```", "'''")  # uma cerca dentro do log fecharia o bloco da issue
    if len(texto) > LIMITE_LINHA:
        texto = texto[: LIMITE_LINHA - 1] + "…"
    return texto


def linhas_do_passo(saida_log_failed: str, job: str) -> list[tuple[str, str]]:
    """De `gh run view --log-failed` (`job<TAB>passo<TAB>linha`) devolve (passo, linha) deste job.

    Em job com contêiner de serviço o GitHub põe TUDO em "UNKNOWN STEP" (o pytest e, no fim, o log do PostgreSQL). Essas
    linhas entram, menos as do serviço (hora e "UTC [pid]"), que são ruído e às vezes milhares.
    """
    achadas: list[tuple[str, str]] = []
    for bruta in saida_log_failed.splitlines():
        partes = bruta.split("\t", 2)
        if len(partes) < 3 or partes[0] != job:
            continue
        if partes[1] == "UNKNOWN STEP" and _SERVICO_PG.match(_HORA.sub("", partes[2]).lstrip()):
            continue
        achadas.append((partes[1], partes[2]))
    return achadas


def destaques(linhas: list[str]) -> list[str]:
    """As linhas que dizem o que quebrou (FAILED, ERRO:, resumo do pytest, mypy, vitest), sem repetir."""
    vistas: list[str] = []
    for linha in linhas:
        if _DESTAQUE.search(linha) and linha not in vistas:
            vistas.append(linha)
    return vistas[-LIMITE_DESTAQUES:]


def ate_o_resumo_do_pytest(linhas: list[str]) -> list[str]:
    """Corta a lista na última linha de resumo do pytest ("2 failed, 10705 passed ... in 3318s"), se houver.

    Em job com contêiner de serviço, depois do resumo vem o despejo de inicialização e desligamento do PostgreSQL: não é
    o que quebrou, e as "últimas linhas" seriam só ele.
    """
    for i in range(len(linhas) - 1, -1, -1):
        if _RESUMO_PYTEST.search(linhas[i]):
            return linhas[: i + 1]
    return linhas


def duracao(inicio: str | None, fim: str | None) -> str:
    if not inicio or not fim:
        return "sem duração"
    try:
        a = datetime.fromisoformat(inicio.replace("Z", "+00:00"))
        b = datetime.fromisoformat(fim.replace("Z", "+00:00"))
    except ValueError:
        return "sem duração"
    minutos, segundos = divmod(max(int((b - a).total_seconds()), 0), 60)
    return f"{minutos} min {segundos:02d} s" if minutos else f"{segundos} s"


def data_da_noite(run: dict[str, object]) -> str:
    """AAAA-MM-DD (UTC) do início do run: o cron é das 05:17Z, então a data é a da noite."""
    bruto = str(run.get("run_started_at") or run.get("created_at") or "")
    if not re.match(r"^\d{4}-\d\d-\d\d", bruto):
        raise ValueError(f"run sem data legível: {bruto!r}")
    return bruto[:10]


def jobs_ruins(jobs: list[dict[str, object]]) -> list[dict[str, object]]:
    """Os que não passaram, em ordem de nome: a issue sai igual em duas leituras do mesmo run."""
    return sorted((j for j in jobs if str(j.get("conclusion")) in CONCLUSOES_RUINS), key=lambda j: str(j.get("name", "")))


def nome_curto(nome: str) -> str:
    """'backend · pytest (PostgreSQL)' vira 'pytest (PostgreSQL)': cabem mais jobs no título."""
    return nome.split(" · ", 1)[-1]


def titulo(data: str, ruins: list[dict[str, object]], conclusao_do_run: str) -> str:
    nomes = [nome_seguro(nome_curto(str(j.get("name", "?")))) for j in ruins] or [f"run {conclusao_do_run}"]
    cheio = f"{prefixo_do_dia(data)} " + ", ".join(nomes)
    while len(cheio) > 140 and len(nomes) > 1:  # corta jobs inteiros, nunca o meio de um nome
        nomes.pop()
        cheio = f"{prefixo_do_dia(data)} " + ", ".join(nomes) + f" e mais {len(ruins) - len(nomes)}"
    return cheio if len(cheio) <= 140 else cheio[:139] + "…"


def prefixo_do_dia(data: str) -> str:
    return f"CI noturno {data}:"


def passo_que_falhou(job: dict[str, object]) -> str:
    for passo in job.get("steps") or []:  # type: ignore[attr-defined]
        if isinstance(passo, dict) and str(passo.get("conclusion")) in CONCLUSOES_RUINS:
            return str(passo.get("name", "?"))
    return "sem passo marcado"


def secao_do_job(job: dict[str, object], saida_log_failed: str, n_linhas: int) -> str:
    nome = str(job.get("name", "?"))
    conclusao = str(job.get("conclusion"))
    passo = passo_que_falhou(job)
    tempo = duracao(str(job.get("started_at") or ""), str(job.get("completed_at") or ""))
    do_passo = [
        linha
        for p, linha in linhas_do_passo(saida_log_failed, nome)
        if p in (passo, "UNKNOWN STEP") or passo == "sem passo marcado"
    ]
    limpas = [limpar_linha(x) for x in do_passo]
    partes = [f"### {nome_seguro(nome)}: {conclusao} ({tempo})", "", f"Passo: `{nome_seguro(passo)}`."]
    if conclusao in ("cancelled", "timed_out"):
        partes.append("Cancelado ou sem fechar o passo (limite de tempo do job ou cancelamento): o log do passo pode vir vazio.")
    if not limpas:
        partes += ["", "Sem linhas de log para este passo (o GitHub não guarda o log de job cancelado ou ele veio vazio)."]
        return "\n".join(partes)
    altos = destaques(limpas)
    if altos:
        partes += ["", "Destaques:", "", "```", *altos, "```"]
    ultimas = ate_o_resumo_do_pytest(limpas)[-n_linhas:]
    partes += ["", f"Últimas {len(ultimas)} linhas do passo (de {len(limpas)}):", "", "```", *ultimas, "```"]
    return "\n".join(partes)


def resumos_do_log(log_inteiro: str) -> list[str]:
    """As linhas "Resumo do job" (29.184) do log inteiro do run, limpas por formato, sem repetir, na ordem em que aparecem."""
    achadas: list[str] = []
    for bruta in log_inteiro.splitlines():
        texto = _HORA.sub("", bruta.split("\t", 2)[-1]).strip()
        if _RESUMO_JOB.match(texto):
            limpa = re.sub(r"[@#<>`]", " ", limpar_linha(texto))  # sem menção, link para issue, HTML nem crase
            if limpa not in achadas:
                achadas.append(limpa)
    return achadas[:LIMITE_RESUMOS]


def secao_dos_jobs(jobs: list[dict[str, object]], resumos: list[str], aviso: str = "") -> str:
    """Todos os jobs do run (nome, resultado, duração) e, abaixo, os resumos de etapa dos que imprimiram o 29.184."""
    linhas = [
        f"- {nome_seguro(str(j.get('name', '?')))}: {j.get('conclusion')} "
        f"({duracao(str(j.get('started_at') or ''), str(j.get('completed_at') or ''))})"
        for j in sorted(jobs, key=lambda j: str(j.get("name", "")))
    ] or ["- nenhum job começou"]
    partes = ["## Jobs da corrida", "", *linhas]
    if resumos:
        partes += ["", "Resumo dos jobs (tempo por etapa, minutos cobrados, testes):", "", *[f"- {r}" for r in resumos]]
    elif aviso:
        partes += ["", f"Sem linhas de resumo: {limpar_linha(aviso)}."]
    else:
        partes += ["", "Sem linhas de resumo no log (job que não chegou ao passo de resumo, ou run de antes do 29.184)."]
    return "\n".join(partes)


def corpo(
    run: dict[str, object],
    ruins: list[dict[str, object]],
    logs: str,
    n_linhas: int,
    aviso_log: str = "",
    jobs: list[dict[str, object]] | None = None,
    log_inteiro: str = "",
    aviso_resumo: str = "",
) -> str:
    sha = str(run.get("head_sha", ""))[:7]
    abertura = (
        f"A corrida diária do CI de {data_da_noite(run)} terminou em **{run.get('conclusion')}** "
        f"([run {run.get('id')}]({run.get('html_url')}), tentativa {run.get('run_attempt', 1)}, commit `{sha}`)."
    )
    nota = (
        "Issue aberta sozinha por `.github/workflows/ci-aviso-de-falha.yml`: uma só para o cron; cada noite que continua "
        "vermelha entra como comentário aqui, e a issue fecha sozinha quando o cron volta a verde. Os logs vêm cortados e "
        "limpos por formato (`scripts/ci_issue_falha.py`); o log inteiro está no run."
    )
    if aviso_log:
        nota += (
            f"\n\n**Log indisponível:** {limpar_linha(aviso_log)}. A issue sai sem as linhas do log; o aviso termina com erro "
            "para o run dele ficar vermelho."
        )
    secoes = [secao_do_job(j, logs, n_linhas) for j in ruins] or [
        "Nenhum job terminou reprovado: o run falhou antes de começar os jobs (ver o run)."
    ]
    blocos = [abertura, nota]
    if jobs is not None:
        blocos.append(secao_dos_jobs(jobs, resumos_do_log(log_inteiro), aviso_resumo))
    texto = "\n\n".join([*blocos, "## Jobs que não passaram", *secoes])
    if len(texto) > LIMITE_CORPO:
        texto = texto[: LIMITE_CORPO - 80].rstrip() + "\n\n(corpo cortado no limite; o log inteiro está no run)"
    return texto


def gh_real(*args: str, entrada: str | None = None) -> str:
    """Chama o `gh`; qualquer erro vira RuntimeError com a mensagem dele (nada de engolir)."""
    r = subprocess.run(["gh", *args], input=entrada, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} saiu com {r.returncode}: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout


def abertas_do_cron(gh: Gh, repo: str) -> list[dict[str, object]]:
    """Issues ABERTAS do cron (rótulo `ci`, título "CI noturno ..."), a mais antiga primeiro."""
    abertas = json.loads(
        gh("issue", "list", "--repo", repo, "--label", ROTULO, "--state", "open", "--limit", "100", "--json", "number,title")
    )
    achadas = [i for i in abertas if str(i.get("title", "")).startswith(PREFIXO_ISSUE)]
    return sorted(achadas, key=lambda i: int(i["number"]))


def voltou_a_verde(repo: str, run: dict[str, object], *, ensaio: bool, gh: Gh) -> tuple[str, str | None, str | None]:
    """Run do cron em sucesso: comenta e fecha a(s) issue(s) aberta(s) do cron; sem issue aberta, não faz nada."""
    run_id = str(run.get("id"))
    if run.get("event") != "schedule":
        return "nada", f"run {run_id} verde, mas não é do cron (evento {run.get('event')}): nada fechado", None
    abertas = abertas_do_cron(gh, repo)
    if not abertas:
        return "nada", f"run {run_id} terminou em success: nada a avisar", None
    # reexecução de um run antigo em verde não pode fechar a issue de uma noite mais nova que está vermelha
    ultimo = json.loads(gh("api", f"repos/{repo}/actions/workflows/ci.yml/runs?event=schedule&per_page=1"))["workflow_runs"]
    if not ultimo or str(ultimo[0].get("id")) != run_id:
        return "nada", f"run {run_id} verde, mas não é o cron mais recente: nada fechado", None
    sha = str(run.get("head_sha", ""))[:7]
    texto = (
        f"O cron do CI de {data_da_noite(run)} voltou a **success** "
        f"([run {run_id}]({run.get('html_url')}), tentativa {run.get('run_attempt', 1)}, commit `{sha}`). "
        "Fechada sozinha por `.github/workflows/ci-aviso-de-falha.yml`; se o cron cair de novo, abre outra."
    )
    numeros = ", ".join(str(i["number"]) for i in abertas)
    if ensaio:
        return "ensaio", f"FECHARIA {numeros}\n\n{texto}", None
    for i in abertas:
        gh("issue", "close", str(i["number"]), "--repo", repo, "--comment", texto)
    return f"fechada {numeros}", texto, None


def avisar(
    repo: str, run_id: str, *, ensaio: bool, n_linhas: int = LINHAS_PADRAO, gh: Gh | None = None
) -> tuple[str, str | None, str | None]:
    """Devolve (o que fez, texto, erro ao ler o log). O que fez: 'nada', 'ensaio', 'aberta N', 'comentada N' ou 'fechada N'.

    Se o log não puder ser lido (job cancelado ou estourado pode não ter log de passo), a issue sai assim mesmo, dizendo que o
    log faltou, e o erro volta no terceiro item para o chamador terminar com código de erro: o aviso não pode sumir por isso.
    O log INTEIRO (só para as linhas de resumo do 29.184) é opcional: se faltar, a issue diz isso e o aviso segue sem erro.
    """
    gh = gh or gh_real  # resolvido na chamada, para o teste poder trocar o `gh_real` do módulo
    run = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}"))
    if run.get("name") != "CI":
        raise ValueError(f"o run {run_id} é do workflow {run.get('name')!r}, não do CI: nada foi escrito")
    conclusao = str(run.get("conclusion"))
    if conclusao == "success":
        return voltou_a_verde(repo, run, ensaio=ensaio, gh=gh)
    if conclusao not in CONCLUSOES_RUINS:
        return "nada", f"run {run_id} terminou em {conclusao}: nada a avisar", None
    jobs = json.loads(gh("api", f"repos/{repo}/actions/runs/{run_id}/jobs?per_page=100"))["jobs"]
    ruins = jobs_ruins(jobs)
    logs, erro_log = "", None
    if ruins:
        try:
            logs = gh("run", "view", run_id, "--repo", repo, "--log-failed")
        except RuntimeError as erro:
            erro_log = str(erro)
    log_inteiro, aviso_resumo = "", ""
    try:
        log_inteiro = gh("run", "view", run_id, "--repo", repo, "--log")
    except RuntimeError as erro:
        aviso_resumo = f"log inteiro indisponível: {erro}"
    data = data_da_noite(run)
    titulo_novo = titulo(data, ruins, conclusao)
    texto = corpo(run, ruins, logs, n_linhas, erro_log or "", jobs, log_inteiro, aviso_resumo)
    if ensaio:
        return "ensaio", f"TÍTULO: {titulo_novo}\n\n{texto}", erro_log
    gh("label", "create", ROTULO, "--repo", repo, "--color", ROTULO_COR, "--description", ROTULO_DESCRICAO, "--force")
    abertas = abertas_do_cron(gh, repo)
    if abertas:
        numero = int(abertas[0]["number"])  # a mais antiga: a issue única do cron
        gh("issue", "comment", str(numero), "--repo", repo, "--body-file", "-", entrada=f"Cron de {data} ainda vermelho.\n\n{texto}")
        return f"comentada {numero}", titulo_novo, erro_log
    url = gh(
        "issue", "create", "--repo", repo, "--title", titulo_novo, "--label", ROTULO, "--body-file", "-", entrada=texto
    ).strip()
    return f"aberta {url.rsplit('/', 1)[-1]}", titulo_novo, erro_log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Abre ou atualiza a issue única do cron do CI que não passou; fecha quando ele volta a verde.")
    ap.add_argument("--run-id", required=True, help="id numérico do run do CI")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""), help="dono/nome (padrão: GITHUB_REPOSITORY)")
    ap.add_argument("--ensaio", action="store_true", help="só lê e imprime a issue; não escreve nada no GitHub")
    ap.add_argument("--linhas", type=int, default=LINHAS_PADRAO, help="últimas linhas do passo que entram na issue")
    args = ap.parse_args(argv)
    for fluxo in (sys.stdout, sys.stderr):  # console do Windows em cp1252 quebraria no "·" e no "…"
        if hasattr(fluxo, "reconfigure"):
            fluxo.reconfigure(encoding="utf-8", errors="replace")
    if not _RUN_ID.fullmatch(args.run_id):
        print("erro: --run-id deve ser numérico", file=sys.stderr)
        return 1
    if not re.match(r"^[\w.-]+/[\w.-]+$", args.repo):
        print("erro: --repo ausente ou fora do formato dono/nome (GITHUB_REPOSITORY)", file=sys.stderr)
        return 1
    try:
        feito, texto, erro_log = avisar(args.repo, args.run_id, ensaio=args.ensaio, n_linhas=max(args.linhas, 5))
    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as erro:
        print(f"erro: {erro}", file=sys.stderr)
        return 1
    print(f"{feito}" + (f"\n{texto}" if texto else ""))
    if erro_log:  # a issue saiu sem o log: o run do aviso fica vermelho para alguém ver
        print(f"erro: não consegui ler o log do run: {erro_log}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
