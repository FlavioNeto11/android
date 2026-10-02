"""Prova INDEPENDENTE de que o repositório real é público (`RepositoryVisibilityVerifier`), para o GitHub.

Por que existe: `repository_class: public` no YAML é só uma declaração. Um repositório privado marcado "public" por engano
mandaria código a um serviço remoto. Aqui a verdade vem do repositório de fato:

1. lê os remotos configurados (`git remote -v`, que já aplica `insteadOf`) e canoniza cada um para `github.com/dono/repo`;
2. pergunta ao GitHub, SEM credencial nenhuma, se cada um é público (`GET /repos/{dono}/{repo}` anônimo: um repositório
   privado responde 404 a quem não tem acesso, então um 200 com `"private": false` já é a prova);
3. só `PUBLIC` se TODOS os remotos forem prova de público. Qualquer outra coisa é `UNKNOWN` e bloqueia o envio: remoto ausente,
   host que não é github.com, URL fora do formato, 404, 301 (repositório renomeado), 403 (limite de uso), erro de rede,
   resposta que não diz `private: false`, nome na resposta diferente do pedido.

Não presume público porque o `git clone` funciona sem senha, nem pelo nome. Nenhuma variável de ambiente nem token é enviado:
o cliente HTTP é anônimo de propósito (e `trust_env=False`, para nem herdar proxy ou credencial do ambiente).

Só é construído e chamado quando o provedor é REMOTE e a configuração já pede o envio de repositório público; retrieval
local, shadow local e provedores fake/local nunca chegam aqui (nenhuma rede, nenhum `git`).

Cache curto da prova: só `PUBLIC`, com TTL (padrão 15 min), na memória e em `visibility.json` (para o endpoint de status ler
sem rede). A chave é a identidade canônica do conjunto de remotos: trocar o `origin` muda a chave e a prova anterior não vale.
`UNKNOWN` nunca vira `PUBLIC` e não é gravado em disco; um resultado não público só é lembrado por segundos (para um laço de
pedidos não bater no GitHub a cada item).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path

import httpx

from ..domain.ports import Visibility

API = "https://api.github.com"
TTL_PUBLICO_S = 900.0
TTL_NEGATIVO_S = 60.0
TIMEOUT_S = 5.0
_NOME = r"[A-Za-z0-9_.-]+"
_FORMAS = (
    re.compile(rf"^https://(?:[^/@\s]+@)?github\.com/({_NOME})/({_NOME}?)(?:\.git)?/?$", re.IGNORECASE),
    re.compile(rf"^(?:ssh://[^/@\s]+@github\.com/|[^/@\s]+@github\.com:)({_NOME})/({_NOME}?)(?:\.git)?/?$",
               re.IGNORECASE),
    re.compile(rf"^git://github\.com/({_NOME})/({_NOME}?)(?:\.git)?/?$", re.IGNORECASE),
)

RemotesReader = Callable[[], Sequence[str]]


def canonizar_remoto(url: str) -> str | None:
    """`github.com/dono/repo` (minúsculo, sem `.git`, sem credencial) ou `None` se não for uma URL do GitHub que se reconheça."""
    bruto = (url or "").strip()
    for forma in _FORMAS:
        m = forma.match(bruto)
        if m:
            dono, repo = m.group(1).lower(), m.group(2).lower()
            if dono in (".", "..") or repo in (".", ".."):
                return None
            return f"github.com/{dono}/{repo}"
    return None


def ler_remotos_do_git(raiz: Path) -> list[str]:
    """URLs de todos os remotos (busca e envio) do repositório em `raiz`. Vazio se não houver, ou se o git falhar."""
    try:
        r = subprocess.run(["git", "remote", "-v"], cwd=raiz, capture_output=True, timeout=10, check=False,
                           env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    urls: list[str] = []
    for linha in r.stdout.decode("utf-8", errors="replace").splitlines():
        partes = linha.split()
        if len(partes) >= 2:
            urls.append(partes[1])
    return urls


class GitHubVisibilityVerifier:
    """`RepositoryVisibilityVerifier` para repositórios cujos remotos são todos do github.com."""

    def __init__(self, raiz: Path, *, store_dir: Path | None = None, transport: httpx.BaseTransport | None = None,
                 clock: Callable[[], float] = time.time, remotes: RemotesReader | None = None,
                 ttl_publico_s: float = TTL_PUBLICO_S, ttl_negativo_s: float = TTL_NEGATIVO_S,
                 timeout_s: float = TIMEOUT_S) -> None:
        self._raiz = raiz
        self._store = store_dir
        self._transport = transport
        self._clock = clock
        self._remotes = remotes if remotes is not None else (lambda: ler_remotos_do_git(raiz))
        self._ttl_publico = ttl_publico_s
        self._ttl_negativo = ttl_negativo_s
        self._timeout = timeout_s
        self._memoria: dict[str, tuple[Visibility, float]] = {}
        self.requisicoes = 0          # quantas idas ao GitHub esta instância fez (diagnóstico e teste)

    # ------------------------------------------------------------------ identidade
    def _identidade(self) -> list[str] | None:
        """Repositórios canônicos de TODOS os remotos, ordenados e sem repetição; `None` se algum não for reconhecível."""
        try:
            urls = list(self._remotes())
        except Exception:  # noqa: BLE001 - não conseguir ler os remotos é "não provado"
            return None
        if not urls:
            return None
        ids: set[str] = set()
        for u in urls:
            c = canonizar_remoto(u)
            if c is None:
                return None
            ids.add(c)
        return sorted(ids)

    @staticmethod
    def _chave(ids: list[str]) -> str:
        return hashlib.sha256("\n".join(ids).encode("utf-8")).hexdigest()[:32]

    # ------------------------------------------------------------------ API
    def peek(self) -> Visibility:
        ids = self._identidade()
        if ids is None:
            return Visibility.UNKNOWN
        return self._lembrado(self._chave(ids)) or Visibility.UNKNOWN

    def verify(self) -> Visibility:
        ids = self._identidade()
        if ids is None:
            return Visibility.UNKNOWN
        chave = self._chave(ids)
        lembrado = self._lembrado(chave)
        if lembrado is not None:
            return lembrado
        resultado = Visibility.PUBLIC
        for repo in ids:
            if self._consultar(repo) is not Visibility.PUBLIC:
                resultado = Visibility.UNKNOWN
                break
        self._guardar(chave, resultado)
        return resultado

    # ------------------------------------------------------------------ rede
    def _consultar(self, repo: str) -> Visibility:
        """`github.com/dono/repo` → `PUBLIC` só com 200 e `private: false` no repositório pedido. Anônimo, sem redirecionar."""
        _, dono, nome = repo.split("/")
        cabecalhos = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
                      "User-Agent": "central-de-aparelhos-context-retrieval"}
        self.requisicoes += 1
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout, follow_redirects=False,
                              trust_env=False) as cliente:
                r = cliente.get(f"{API}/repos/{dono}/{nome}", headers=cabecalhos)
        except Exception:  # noqa: BLE001 - rede, TLS, tempo: nada disso prova público
            return Visibility.UNKNOWN
        if r.status_code != 200:
            return Visibility.UNKNOWN
        try:
            corpo = r.json()
        except ValueError:
            return Visibility.UNKNOWN
        if not isinstance(corpo, dict) or corpo.get("private") is not False:
            return Visibility.UNKNOWN
        if "visibility" in corpo and corpo["visibility"] != "public":
            return Visibility.UNKNOWN
        nome_completo = corpo.get("full_name")
        if not isinstance(nome_completo, str) or nome_completo.lower() != f"{dono}/{nome}":
            return Visibility.UNKNOWN
        return Visibility.PUBLIC

    # ------------------------------------------------------------------ cache da prova
    def _lembrado(self, chave: str) -> Visibility | None:
        agora = self._clock()
        item = self._memoria.get(chave)
        if item is not None:
            visibilidade, ate = item
            if agora < ate:
                return visibilidade
            del self._memoria[chave]
        if self._store is None:
            return None
        try:
            dados = json.loads((self._store / "visibility.json").read_text(encoding="utf-8"))
            registro = dados[chave]
            verificado_em = float(registro["verified_at"])
            if registro["visibility"] == Visibility.PUBLIC.value and 0 <= agora - verificado_em < self._ttl_publico:
                self._memoria[chave] = (Visibility.PUBLIC, verificado_em + self._ttl_publico)
                return Visibility.PUBLIC
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None  # ausente, corrompido ou de outra forma: não há prova
        return None

    def _guardar(self, chave: str, visibilidade: Visibility) -> None:
        agora = self._clock()
        if visibilidade is not Visibility.PUBLIC:
            self._memoria[chave] = (visibilidade, agora + self._ttl_negativo)
            return
        self._memoria[chave] = (visibilidade, agora + self._ttl_publico)
        if self._store is None:
            return
        try:
            self._store.mkdir(parents=True, exist_ok=True)
            arq = self._store / "visibility.json"
            try:
                dados = json.loads(arq.read_text(encoding="utf-8"))
                if not isinstance(dados, dict):
                    dados = {}
            except (OSError, ValueError):
                dados = {}
            dados = {k: v for k, v in dados.items()
                     if isinstance(v, dict) and 0 <= agora - float(v.get("verified_at", 0)) < self._ttl_publico}
            dados[chave] = {"visibility": Visibility.PUBLIC.value, "verified_at": agora}
            tmp = arq.with_name(f"{arq.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
            try:
                tmp.write_text(json.dumps(dados), encoding="utf-8")
                os.replace(tmp, arq)
            finally:
                tmp.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError):
            return
