"""A prova do conhecimento de app que está no ar (RA-24): cada YAML de `app/conhecimento/apps/<pacote>/` com o sha256
e o hash de blob do Git, para conferir contra um commit o que o processo leu, sem abrir a máquina.

Os carregadores leem os arquivos uma vez por processo (`catalogo_do_pacote`, `conhecimento.do_app`, `descobrir`) com
`read_text`, e o texto que o parser recebe já chega em LF (novas linhas universais). Os hashes são desse texto, e não
dos bytes do disco: o checkout do central tem `core.autocrlf=true` (CRLF no disco, LF no Git), e o hash dos bytes crus
não bateria com commit nenhum. Assim `git_blob` é o de `git hash-object <arquivo>` no checkout e o de
`git rev-parse <commit>:<caminho>`; `sha256` é o mesmo texto, para quem não tem Git à mão.

`mudou_depois_do_inicio` diz se o arquivo foi gravado depois de o processo subir: aí o disco pode não ser o que está
em memória, e só um reinício alinha os dois.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import psutil

from ...planning.capabilities import CONHECIMENTO_DE_APPS
from ...util import to_iso

#: Pacote Android. Vira componente de caminho: sem esta forma, `..` sairia da pasta dos apps.
_PACOTE_ANDROID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
#: A raiz do repositório (`backend/app/conhecimento/apps` → quatro acima), para o caminho que o Git entende.
_RAIZ_DO_REPO = CONHECIMENTO_DE_APPS.parents[3]


@dataclass(frozen=True, slots=True)
class ArquivoProvado:
    nome: str
    #: Relativo à raiz do repositório quando a pasta é a do repositório (`git rev-parse <commit>:<caminho>`).
    caminho: str
    #: Tamanho no disco, como está (com o fim de linha do checkout).
    bytes: int
    fim_de_linha: str                   # "crlf", "lf" ou "misto"
    sha256: str
    git_blob: str
    modificado_em: str
    mudou_depois_do_inicio: bool


@dataclass(frozen=True, slots=True)
class ProvaDoConhecimento:
    app: str
    processo_iniciado_em: str
    arquivos: tuple[ArquivoProvado, ...]


def texto_lido(cru: bytes) -> bytes:
    """Os bytes que o carregador vê: CRLF e CR solto viram LF, como em `read_text`."""
    return cru.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def git_blob(conteudo: bytes) -> str:
    """O hash de `git hash-object`: sha1 de `blob <tamanho>\\0` seguido do conteúdo."""
    return hashlib.sha1(b"blob %d\0" % len(conteudo) + conteudo, usedforsecurity=False).hexdigest()


def _fim_de_linha(cru: bytes) -> str:
    crlf = cru.count(b"\r\n")
    lf = cru.count(b"\n") - crlf
    return "misto" if crlf and lf else "crlf" if crlf else "lf"


def _caminho(arquivo: Path) -> str:
    try:
        return arquivo.resolve().relative_to(_RAIZ_DO_REPO).as_posix()
    except ValueError:                  # pasta de fora do repositório (teste): só o nome
        return arquivo.name


def _provar(arquivo: Path, inicio: float) -> ArquivoProvado:
    cru = arquivo.read_bytes()
    lido = texto_lido(cru)
    mtime = arquivo.stat().st_mtime
    return ArquivoProvado(nome=arquivo.name, caminho=_caminho(arquivo), bytes=len(cru), fim_de_linha=_fim_de_linha(cru),
                          sha256=hashlib.sha256(lido).hexdigest(), git_blob=git_blob(lido),
                          modificado_em=to_iso(datetime.fromtimestamp(mtime, timezone.utc)),
                          mudou_depois_do_inicio=mtime > inicio)


def prova_do_pacote(pacote: str, *, inicio: float | None = None,
                    raiz: Path = CONHECIMENTO_DE_APPS) -> ProvaDoConhecimento | None:
    """Os YAML do pacote com os hashes; `None` quando o pacote não tem forma de pacote ou não declara arquivo nenhum.
    `inicio` é o instante (epoch) em que o processo subiu; sem ele, o do processo atual."""
    if not _PACOTE_ANDROID.match(pacote or ""):
        return None
    pasta = raiz / pacote
    arquivos = sorted(p for p in pasta.glob("*.yaml") if p.is_file()) if pasta.is_dir() else []
    if not arquivos:
        return None
    if inicio is None:
        inicio = psutil.Process().create_time()
    return ProvaDoConhecimento(app=pacote, processo_iniciado_em=to_iso(datetime.fromtimestamp(inicio, timezone.utc)),
                               arquivos=tuple(_provar(p, inicio) for p in arquivos))
