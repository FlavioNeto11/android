"""A versão do código que ESTE processo carregou — uma só definição, usada pelo central e pelo agente.

Por que existe: `AGENT_VERSION = "0.1.0"` era uma constante escrita à mão, e a máquina do worker não é um
checkout (`C:\\farm\\agent` chega por cópia). Ou seja, o central não tinha como saber se o agente do outro lado
roda o código de hoje ou o de três semanas atrás: os dois se apresentam como `0.1.0`. Aqui a versão passa a ser
`0.1.0+<sha7>` — semântica MAIS o commit —, e o central compara a do worker com a dele e marca `agente defasado`.

Três fontes, nesta ordem, e nenhuma delas chama `git`:

1. **`app/BUILD_VERSION`**, gravado pelo empacotador (`scripts/worker-install.ps1` / `worker-install.sh`) no
   momento em que o agente é copiado para a máquina do worker. É a única fonte que existe lá.
2. **`.git` da árvore**, lido como dois arquivos de texto. É o caso do central e de quem roda do checkout.
3. **`VERSION` sozinha**, com o sufixo `+desconhecido`, para que a resposta nunca seja uma exceção nem um
   `None` que alguém compare com outro `None` e conclua que está tudo igual.

Sem subprocesso de propósito: `git` pode não estar no PATH da conta que roda o serviço, e uma versão que falha
por causa disso troca uma resposta útil por um erro.
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

#: Versão semântica do projeto. Muda quando o contrato muda, na mão.
VERSION = "0.1.0"

#: Nome do arquivo que o empacotador grava dentro do pacote do agente, ao lado deste módulo.
ARQUIVO_DE_BUILD = "BUILD_VERSION"

#: O que sai quando nem o arquivo de build nem o `.git` respondem. Texto e não `None`: `None == None` diria
#: "mesma versão" para dois lados que não fazem ideia de qual código estão rodando.
DESCONHECIDO = f"{VERSION}+desconhecido"


def _pastas_do_git(raiz: Path) -> tuple[Path, Path]:
    """(pasta do HEAD, pasta comum das refs). Num checkout comum as duas são `.git`. Num `git worktree`, `.git` é um
    ARQUIVO `gitdir: <caminho>`: o HEAD fica na pasta do worktree e as refs na pasta comum (arquivo `commondir`).
    Sem isto, rodar a partir de um worktree dava versão `desconhecido` e reprovava os testes de versão ali."""
    git = raiz / ".git"
    try:
        if git.is_file():
            alvo = git.read_text(encoding="utf-8").strip()
            if alvo.startswith("gitdir:"):
                git = Path(alvo.partition(":")[2].strip())
                git = git if git.is_absolute() else (raiz / git).resolve()
    except OSError:
        return git, git
    try:
        comum = Path((git / "commondir").read_text(encoding="utf-8").strip())
        comum = comum if comum.is_absolute() else (git / comum).resolve()
    except OSError:
        comum = git
    return git, comum


@lru_cache(maxsize=4)
def commit_em_execucao(raiz: Path) -> str | None:
    """O commit que ESTE processo carregou, lido do `.git` — sem chamar `git`.

    Existe porque `version` é uma constante no código e não respondia a pergunta que o deploy faz: *este processo é
    o código novo?* O parque rodou por um dia um backend anterior às migrações 016/017 e nada no `/api/health`
    dizia isso. Cacheado porque o commit não muda enquanto o processo vive — trocar o código exige reiniciar.

    Sem subprocesso de propósito: `git` pode não estar no PATH da conta que roda o serviço, e um `/api/health` que
    falha por causa disso troca uma resposta útil por um erro. Ler dois arquivos de texto sempre funciona.
    """
    git, comum = _pastas_do_git(raiz)
    try:
        cabeca = (git / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not cabeca.startswith("ref:"):
        return cabeca[:40] or None            # HEAD destacado: o próprio sha
    ref = cabeca.partition(":")[2].strip()
    for pasta in dict.fromkeys((git, comum)):   # ref solta: na pasta do worktree ou na comum
        try:
            return (pasta / ref).read_text(encoding="utf-8").strip()[:40] or None
        except OSError:
            pass
    try:                                       # ref empacotada (`git gc` move refs para packed-refs)
        for linha in (comum / "packed-refs").read_text(encoding="utf-8").splitlines():
            sha, _, nome = linha.partition(" ")
            if nome.strip() == ref:
                return sha.strip()[:40] or None
    except OSError:
        pass
    return None


def _ler_build(pacote: Path) -> str | None:
    """A versão gravada pelo empacotador, se houver. Uma linha, sem espaço em volta, no máximo 40 caracteres."""
    try:
        texto = (pacote / ARQUIVO_DE_BUILD).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    primeira = texto.splitlines()[0].strip() if texto else ""
    return primeira[:40] or None


@lru_cache(maxsize=4)
def agent_version(pacote: Path | None = None) -> str:
    """`0.1.0+<sha7>` — a versão que o agente declara e a que o central espera dele.

    `pacote` é a pasta `app/` (o padrão é a deste módulo); os testes passam outra para exercitar cada fonte.
    """
    base = pacote or Path(__file__).resolve().parent
    if (build := _ler_build(base)) is not None:
        return build
    # `app/` → `backend/` → raiz da árvore, que é onde fica o `.git`.
    if (sha := commit_em_execucao(base.parent.parent)) is not None:
        return f"{VERSION}+{sha[:7]}"
    return DESCONHECIDO


#: O manifesto do pacote do agente, ao lado de `app/` num checkout (`backend/worker-manifest.txt`). Na máquina do
#: worker ele não existe: lá `app/` JÁ É o pacote, montado pelo instalador exatamente com estas entradas.
ARQUIVO_DO_MANIFESTO = "worker-manifest.txt"


def entradas_do_manifesto(caminho: Path) -> list[str]:
    """As entradas, na ordem do arquivo: a mesma leitura dos dois instaladores (`#` comenta, linha vazia some)."""
    entradas: list[str] = []
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        if entrada := linha.split("#", 1)[0].strip():
            entradas.append(entrada)
    return entradas


def _fica_fora(relativo: Path) -> bool:
    """O que não é código do pacote: o selo de build (muda a cada cópia sem o código mudar) e o cache do Python."""
    return relativo.as_posix() == ARQUIVO_DE_BUILD or "__pycache__" in relativo.parts or relativo.suffix == ".pyc"


def _arquivos_do_pacote(pacote: Path) -> list[str]:
    """Os arquivos do pacote do agente, relativos a `app/`, com barra normal e em ordem."""
    manifesto = pacote.parent / ARQUIVO_DO_MANIFESTO
    if manifesto.is_file():
        raizes = [pacote / e.rstrip("/") for e in entradas_do_manifesto(manifesto)]
    else:
        raizes = [pacote]
    vistos: set[str] = set()
    for raiz in raizes:
        candidatos = raiz.rglob("*") if raiz.is_dir() else [raiz]
        for p in candidatos:
            if p.is_file() and not _fica_fora(rel := p.relative_to(pacote)):
                vistos.add(rel.as_posix())
    return sorted(vistos)


@lru_cache(maxsize=4)
def codigo_do_agente(pacote: Path | None = None) -> str | None:
    """Impressão (sha256, 16 hex) do CÓDIGO que o agente roda: o conteúdo do pacote, não o commit da árvore.

    Existe porque a versão (`0.1.0+<sha7>`) muda a cada commit, inclusive os que só mexem em docs ou no plano: todo
    reinício do central depois de um commit assim acendia `agente defasado` com o mesmo código dos dois lados
    (item 29.59). As duas pontas chegam ao mesmo conjunto de arquivos por caminhos diferentes: no checkout, pelo
    manifesto; no worker, varrendo `app/`, que o instalador montou só com o manifesto. `BUILD_VERSION` e
    `__pycache__` ficam fora, e `\\r\\n` vira `\\n` antes do hash, para um checkout com fim de linha do Windows não
    parecer outro código. `None` quando não há o que ler: quem compara cai na regra antiga, pela versão.
    """
    base = pacote or Path(__file__).resolve().parent
    h = hashlib.sha256()
    try:
        arquivos = _arquivos_do_pacote(base)
        for rel in arquivos:
            h.update(rel.encode("utf-8") + b"\0")
            h.update((base / rel).read_bytes().replace(b"\r\n", b"\n") + b"\0")
    except OSError:
        return None
    return h.hexdigest()[:16] if arquivos else None
