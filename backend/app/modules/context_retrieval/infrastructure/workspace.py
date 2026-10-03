"""Universo de arquivos do retrieval: o que pode ser lido, buscado, indexado e (um dia) enviado.

Todo retriever local e o mapa do repositório passam por aqui, e só por aqui, para abrir arquivo. É este módulo que
garante três coisas: (1) o universo exclui o que é sensível, binário ou grande demais ANTES de qualquer busca;
(2) um caminho que sai da raiz (`..`, absoluto, unidade de disco) nunca é lido; (3) a `revision()` muda quando o
conteúdo muda, que é a chave de todos os caches.

Só stdlib. O git é usado quando existe (rápido, respeita `.gitignore`); sem git, ou se ele falhar, anda-se a árvore.
"""
from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from app.devices.sdk import sem_segredos
from app.modules.context_retrieval.domain.sensitive import SensitivePathMatcher, default_matcher

#: Pastas que nunca entram no universo quando não há git para dizer o que é do projeto.
PASTAS_IGNORADAS = frozenset({".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".mypy_cache"})

#: Extensões que são binário por definição; barra antes de abrir o arquivo.
EXTENSOES_BINARIAS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff", ".svgz", ".pdf", ".zip", ".gz", ".tgz",
    ".bz2", ".xz", ".7z", ".rar", ".jar", ".apk", ".aab", ".so", ".dll", ".exe", ".dylib", ".a", ".o", ".obj", ".class",
    ".pyc", ".pyo", ".whl", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp3", ".mp4", ".mov", ".avi", ".mkv", ".wav",
    ".ogg", ".webm", ".sqlite", ".sqlite3", ".db", ".onnx", ".pt", ".pth", ".npy", ".npz", ".parquet", ".bin", ".dat",
    ".img", ".iso", ".dmp", ".pack", ".idx",
})

_LINGUAGENS: dict[str, str] = {
    ".py": "python", ".pyi": "python", ".ts": "typescript", ".tsx": "tsx", ".js": "javascript", ".jsx": "jsx",
    ".mjs": "javascript", ".cjs": "javascript", ".sql": "sql", ".md": "markdown", ".ps1": "powershell",
    ".psm1": "powershell", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".sh": "shell",
    ".css": "css", ".html": "html", ".kt": "kotlin", ".java": "java", ".txt": "text", ".ini": "ini", ".cfg": "ini",
    ".xml": "xml", ".bat": "batch", ".cmd": "batch",
}

_TIMEOUT_GIT_S = 20.0
_BYTES_NUL = 4096
#: Sem janela de console piscando quando o central roda como tarefa agendada.
_CREATE_NO_WINDOW = 0x08000000


def normalize_scope(scope: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Prefixos de escopo limpos: `/`, sem `./` inicial, sem vazios. Vazio significa "tudo"."""
    saida: list[str] = []
    for s in scope:
        p = s.strip().replace(chr(92), "/")
        while p.startswith("./"):
            p = p[2:]
        if p:
            saida.append(p)
    return tuple(saida)


def path_in_scope(path: str, scope: tuple[str, ...]) -> bool:
    """Prefixo literal de caminho (`backend/app` casa `backend/app/x.py`); `scope` já normalizado, vazio = tudo."""
    return not scope or any(path.startswith(p) for p in scope)


#: Teto de texto guardado em memória durante um lote (`pinned()`). Passado dele, lê do disco como sempre.
_TETO_DE_TEXTOS_DO_LOTE = 96 * 1024 * 1024


class Workspace:
    """Raiz de um repositório e o universo de arquivos de texto que o retrieval pode tocar."""

    def __init__(self, root: Path, *, sensitive: SensitivePathMatcher | None = None,
                 max_file_bytes: int = 512_000) -> None:
        self.root = Path(root).resolve()
        self._sensitive = sensitive if sensitive is not None else default_matcher()
        self._max_file_bytes = max_file_bytes
        self._files: list[str] | None = None
        self._universo: frozenset[str] = frozenset()
        self._tamanhos: dict[str, int] = {}
        self._git: bool | None = None
        self._ultima_revisao: str | None = None
        self._fixa: str | None = None          # revisão congelada por `pinned()` (lote)
        self._textos: dict[str, str] = {}      # textos lidos durante o lote (só vive dentro de `pinned()`)
        self._textos_bytes = 0

    # ------------------------------------------------------------------ git
    def _git_bytes(self, *args: str) -> bytes | None:
        """Saída do git ou `None` (git ausente, erro, timeout). `GIT_OPTIONAL_LOCKS=0`: ler não pode travar o índice;
        `sem_segredos`: o git não recebe as chaves do `.env` (29.47)."""
        env = {**sem_segredos(), "GIT_OPTIONAL_LOCKS": "0"}
        flags = _CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            r = subprocess.run(["git", *args], cwd=self.root, capture_output=True, timeout=_TIMEOUT_GIT_S,
                               env=env, creationflags=flags, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout if r.returncode == 0 else None

    def _is_git(self) -> bool:
        """Só vale git quando a raiz É a raiz do repositório: pasta solta dentro de outro repo anda a árvore."""
        if self._git is None:
            out = self._git_bytes("rev-parse", "--show-toplevel")
            topo = out.decode("utf-8", errors="replace").strip() if out else ""
            self._git = bool(topo) and os.path.normcase(str(Path(topo).resolve())) == os.path.normcase(str(self.root))
        return self._git

    # ------------------------------------------------------------------ universo
    @staticmethod
    def _normalizar(path: str) -> str | None:
        """Caminho relativo limpo, ou `None` se tentar sair da raiz (traversal, absoluto, unidade de disco)."""
        p = path.replace(chr(92), "/")
        while p.startswith("./"):
            p = p[2:]
        if not p or p.startswith("/") or os.path.isabs(p) or (len(p) > 1 and p[1] == ":") or "\x00" in p:
            return None
        partes = PurePosixPath(p).parts
        if not partes or any(x == ".." for x in partes):
            return None
        return "/".join(partes)

    def _nomes_do_git(self) -> list[str] | None:
        out = self._git_bytes("ls-files", "-co", "--exclude-standard", "-z")
        if out is None:
            return None
        return [x.decode("utf-8", errors="replace") for x in out.split(b"\0") if x]

    def _nomes_da_arvore(self) -> list[str]:
        saida: list[str] = []
        raiz = str(self.root)
        for dirpath, dirnames, filenames in os.walk(raiz, followlinks=False):
            rel_dir = os.path.relpath(dirpath, raiz).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            # poda por nome ou por pasta sensível: não vale andar em `data/` só para descartar cada arquivo depois
            dirnames[:] = sorted(
                d for d in dirnames
                if d not in PASTAS_IGNORADAS
                and self._sensitive.reason(f"{rel_dir}/{d}/_" if rel_dir else f"{d}/_") != "sensitive_dir")
            saida.extend(f"{rel_dir}/{f}" if rel_dir else f for f in filenames)
        return saida

    def _candidatos(self) -> list[tuple[str, int, int]]:
        """`(caminho, tamanho, mtime_ns)` dos arquivos que passam nos filtros baratos (sem abrir o conteúdo)."""
        nomes = self._nomes_do_git() if self._is_git() else None
        if nomes is None:
            nomes = self._nomes_da_arvore()
        saida: list[tuple[str, int, int]] = []
        for bruto in sorted(set(nomes)):
            rel = self._normalizar(bruto)
            if rel is None or Path(rel).suffix.lower() in EXTENSOES_BINARIAS or self._sensitive.reason(rel):
                continue
            try:
                st = os.lstat(self.root / rel)  # lstat: link simbólico pode apontar para fora da raiz
            except OSError:
                continue  # listado pelo git mas apagado na árvore de trabalho
            if not stat.S_ISREG(st.st_mode) or st.st_size > self._max_file_bytes:
                continue
            saida.append((rel, st.st_size, st.st_mtime_ns))
        return saida

    def _parece_texto(self, rel: str) -> bool:
        try:
            with open(self.root / rel, "rb") as fh:
                return b"\0" not in fh.read(_BYTES_NUL)
        except OSError:
            return False

    def _garantir(self) -> list[str]:
        """Monta o universo se preciso e devolve a lista interna (sem copiar: `read_text` roda uma vez por arquivo)."""
        if self._files is None:
            cand = [c for c in self._candidatos() if self._parece_texto(c[0])]
            self._files = [c[0] for c in cand]
            self._universo = frozenset(self._files)
            self._tamanhos = {c[0]: c[1] for c in cand}
        return self._files

    def files(self) -> list[str]:
        """Caminhos relativos (`/`), ordenados, de arquivos de texto, pequenos e não sensíveis. Em cache até `refresh()`."""
        return list(self._garantir())

    def refresh(self) -> None:
        """Descarta o cache de arquivos; a próxima leitura refaz o universo."""
        self._files = None
        self._universo = frozenset()
        self._tamanhos = {}
        self._git = None

    def read_text(self, path: str) -> str | None:
        """Texto do arquivo (utf-8, `replace`, fim de linha `\\n`) ou `None` se estiver fora do universo ou ilegível.

        CRLF vira LF para que a numeração de linha seja a mesma aqui, no `rg` e no cliente que mostra o trecho.
        """
        rel = self._normalizar(path)
        if rel is None:
            return None
        self._garantir()
        if rel not in self._universo:
            return None
        if self._fixa is not None and rel in self._textos:
            return self._textos[rel]
        try:
            dados = (self.root / rel).read_bytes()
        except OSError:
            return None
        texto = dados.decode("utf-8", errors="replace").replace("\r\n", "\n")
        # No lote o mesmo arquivo é lido uma vez por consulta (léxico e janelas): guarda, com teto, só enquanto congelado.
        if self._fixa is not None and self._textos_bytes + len(dados) <= _TETO_DE_TEXTOS_DO_LOTE:
            self._textos[rel] = texto
            self._textos_bytes += len(dados)
        return texto

    def size_of(self, path: str) -> int | None:
        """Tamanho em bytes de um arquivo do universo (do momento em que o universo foi montado)."""
        self._garantir()
        return self._tamanhos.get(path)

    @staticmethod
    def language_of(path: str) -> str:
        nome = path.rsplit("/", 1)[-1]
        if nome.lower() in ("dockerfile", "makefile"):
            return nome.lower()
        return _LINGUAGENS.get(Path(nome).suffix.lower(), "text")

    # ------------------------------------------------------------------ revisão
    def _revisao_por_stat(self) -> str:
        h = hashlib.sha256()
        for rel, size, mtime in self._candidatos():
            h.update(f"{rel}\0{size}\0{mtime}\n".encode())
        return "fs-" + h.hexdigest()[:24]

    def _revisao_git(self) -> str | None:
        arvore = self._git_bytes("rev-parse", "HEAD^{tree}")
        status = self._git_bytes("status", "--porcelain=v1", "-z", "--untracked-files=all")
        if not arvore or status is None:
            return None  # repositório sem commit ou git falhou: cai na revisão por stat
        tree = arvore.decode("ascii", errors="replace").strip()
        if not status:
            return tree
        h = hashlib.sha256(status)
        entradas = [x.decode("utf-8", errors="replace") for x in status.split(b"\0") if x]
        i = 0
        while i < len(entradas):
            xy, caminho = entradas[i][:2], entradas[i][3:]
            i += 2 if xy[:1] in ("R", "C") else 1  # renomeado traz o caminho de origem na entrada seguinte
            try:
                st = os.lstat(self.root / caminho)
                h.update(f"\n{caminho}\0{st.st_size}\0{st.st_mtime_ns}".encode())
            except OSError:
                h.update(f"\n{caminho}\0gone".encode())
        return f"{tree}+{h.hexdigest()[:12]}"

    def corpus_digest(self) -> str:
        """Resumo do que define o CORPUS além da revisão: padrões sensíveis e teto de tamanho. Entra na chave dos índices em
        disco, para que mudar a política de caminhos nunca reaproveite um índice feito com a antiga."""
        return hashlib.sha256(f"{self._sensitive.fingerprint()}|{self._max_file_bytes}".encode()).hexdigest()[:16]

    @contextmanager
    def pinned(self) -> Iterator[str]:
        """Congela a revisão (e, com ela, o universo) durante um lote. Sem isto, cada pedido paga ~0,1 s de `git status` e
        um arquivo gerado no meio do lote mudaria a revisão e invalidaria o cache entre um item e outro."""
        antiga = self._fixa
        self._fixa = antiga if antiga is not None else self.revision()
        try:
            yield self._fixa
        finally:
            self._fixa = antiga
            if antiga is None:                  # saiu do lote mais externo: nada fica na memória
                self._textos = {}
                self._textos_bytes = 0

    def revision(self) -> str:
        """Identidade do conteúdo atual: árvore do git (`+digest` se suja) ou hash de caminho/tamanho/mtime.

        É o ÚNICO ponto que invalida o cache de arquivos: se a revisão mudou desde a última chamada, o universo é
        refeito. Quem monta o pedido deve chamá-la antes de buscar. Limite assumido: edição que preserva tamanho E
        mtime não é vista (o mesmo vale para o git `status`, que também olha o stat).
        """
        if self._fixa is not None:
            return self._fixa
        rev = self._revisao_git() if self._is_git() else None
        if rev is None:
            rev = self._revisao_por_stat()
        if rev != self._ultima_revisao:
            self._files = None
            self._universo = frozenset()
            self._tamanhos = {}
            self._ultima_revisao = rev
        return rev
