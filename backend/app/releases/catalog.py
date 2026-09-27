"""Descoberta, validação e catálogo imutável de releases.

Fluxo: o usuário larga arquivos em `apks/inbox/`; aqui eles são agrupados em conjuntos, inspecionados, validados e
movidos para um diretório cujo nome vem do próprio conteúdo. O nome que o usuário deu ao arquivo não decide nada —
nem o pacote, nem a versão, nem o caminho de destino.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from ..util import now_iso
from .inspector import ApkInfo, ApkInspectionError, ApkInspector

log = logging.getLogger("poc.releases")

# Contêineres que são ZIP com APKs dentro. Não são equivalentes ao bundle original: sem bundletool não se lê o
# descritor oficial, então o conjunto extraído nasce marcado como não verificado.
CONTAINER_SUFFIXES = (".apks", ".xapk", ".apkm")
MAX_FILES_PER_SET = 32
#: Teto do que um contêiner (.xapk/.apks/.apkm) pode ocupar DEPOIS de extraído. O upload aceita até 512 MB
#: compactados; APK quase não comprime, então um contêiner legítimo extrai para perto disso. 2 GiB dá folga para
#: apps grandes e barra a bomba de zip.
MAX_CONTAINER_EXTRACTED_BYTES = 2 * 1024 ** 3
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class ReleaseValidationError(RuntimeError):
    """O conjunto não forma uma release instalável. A mensagem explica o que está errado."""


class InstalacaoIncerta(RuntimeError):
    """A operação de app terminou sem que se saiba o efeito — e NÃO é falha.

    Existe porque "queda de conexão não significa que a ação falhou" valia para o comando de aparelho e não valia
    para o pipeline de aplicativo: um timeout do adb, inclusive numa leitura DEPOIS de uma instalação
    bem-sucedida, era gravado como `install_failed` — estado pegajoso que exigia "Distribuir de novo", o que
    reinstala. Aconteceu em campo nos remotos: app instalado e funcionando, painel dizendo que falhou.

    Quem levanta isto deixa a linha em `verifying` SEM operação pendente, que é a forma de dizer "o aparelho
    ainda vai ser relido" — e a releitura automática (na entrada no ar e no start) resolve para
    `ready`/`version_drift`/`missing`.

    Mora aqui, ao lado de `ReleaseValidationError`, e não em `service.py` (que a reexporta): o despacho de comandos
    (`commands/despacho.py`) precisa dela, e importar `releases.service` de lá fecharia um ciclo em execução pela
    vitrine (`service` → `vitrine` → `devices.proxy` → `despacho` → `service`).
    """


@dataclass(slots=True)
class CandidateSet:
    """Um conjunto de arquivos que se propõe a ser uma release. Ainda não foi validado."""

    label: str                    # o que apareceu na inbox (só para mensagem; não decide nada)
    files: list[Path]
    container: bool = False       # veio de .apks/.xapk/.apkm
    temp_dir: Path | None = None  # extração temporária, removida depois de catalogar


@dataclass(slots=True)
class ValidatedRelease:
    """Conjunto aprovado: metadados já conferidos e consistentes entre todos os arquivos."""

    package_name: str
    version_code: int
    version_name: str
    signature_sha256: str
    artifact_type: str
    min_sdk: int | None
    target_sdk: int | None
    abis: list[str]
    #: Algum arquivo do conjunto declara depender de Google Play Services. Fica no conjunto (e não só no base)
    #: porque um split de recursos pode trazer a declaração; o que importa é o conjunto exigir GMS.
    requires_gms: bool
    parts: list[ApkInfo]
    set_hash: str
    #: Nome e ícone vêm do base.apk: é ele que declara a aplicação. Um split de recursos não tem rótulo próprio.
    label: str | None = None
    icon_entry: str | None = None

    @property
    def release_id(self) -> str:
        return f"{self.package_name}-{self.version_code}-{self.set_hash[:12]}"

    @property
    def dir_name(self) -> str:
        return f"{self.version_code}-{self.set_hash[:12]}"


@dataclass(slots=True)
class Discovery:
    """O que foi encontrado na inbox. Os soltos ainda não estão agrupados: isso exige inspecionar."""

    sets: list[CandidateSet]
    loose: list[Path]


def discover(inbox: Path) -> Discovery:
    """Uma pasta é um conjunto; um contêiner é extraído para um conjunto; `.apk` solto fica para agrupar depois."""
    if not inbox.is_dir():
        return Discovery(sets=[], loose=[])
    sets: list[CandidateSet] = []
    loose: list[Path] = []
    for entry in sorted(inbox.iterdir()):
        if entry.is_file() and entry.suffix.lower() == ".apk":
            loose.append(entry)
        elif entry.is_file() and entry.suffix.lower() in CONTAINER_SUFFIXES:
            sets.append(_extract_container(entry))
        elif entry.is_dir():
            files = sorted(p for p in entry.rglob("*.apk") if p.is_file())
            if files:
                sets.append(CandidateSet(label=entry.name, files=files))
    return Discovery(sets=sets, loose=loose)


def group_loose(paths: list[Path], inspector: ApkInspector) -> list[CandidateSet]:
    """Agrupa `.apk` soltos pelo que o PACOTE diz: mesmo pacote, mesma versão e mesma assinatura formam um conjunto.

    É o caso natural de quem larga `base.apk` e os `config.*.apk` juntos na pasta. O agrupamento não olha o nome do
    arquivo — dois arquivos de versões diferentes viram duas releases, mesmo que tenham nomes parecidos.
    """
    groups: dict[tuple[str, int, str], list[Path]] = {}
    out: list[CandidateSet] = []
    for path in sorted(paths):
        try:
            info = inspector.inspect(path)
        except ApkInspectionError:
            out.append(CandidateSet(label=path.name, files=[path]))     # deixa a validação reportar o motivo
            continue
        groups.setdefault((info.package_name, info.version_code, info.signature_sha256), []).append(path)
    for (package, version_code, _), files in groups.items():
        label = files[0].name if len(files) == 1 else f"{package} {version_code} ({len(files)} arquivos)"
        out.append(CandidateSet(label=label, files=sorted(files)))
    return out


def _extract_container(path: Path) -> CandidateSet:
    """Extrai os `.apk` de um contêiner ZIP. Ignora caminho embutido no arquivo (defesa contra path traversal):
    cada membro vai para o diretório temporário com um nome próprio e seguro.

    Desde a loja de apps (26/09) o contêiner também chega pelo upload do painel, e o limite do upload só vale para o
    tamanho COMPACTADO. Uma "bomba de zip" de poucos MB encheria o disco temporário antes da inspeção recusar; por
    isso o total extraído tem teto, conferido pelo tamanho declarado de cada membro. O `zipfile` não lê além do que
    o cabeçalho declara (cabeçalho que mente dá erro de CRC → "ilegível"); a contagem dos bytes copiados é a segunda
    linha, para não depender só disso. APK já é compactado por dentro: um contêiner legítimo quase não cresce."""
    temp = Path(tempfile.mkdtemp(prefix="apkset-"))
    files: list[Path] = []
    try:
        with zipfile.ZipFile(path) as zf:
            infos = [i for i in zf.infolist() if i.filename.lower().endswith(".apk") and not i.is_dir()]
            if len(infos) > MAX_FILES_PER_SET:
                raise ReleaseValidationError(f"{path.name}: contêiner com {len(infos)} APKs (limite {MAX_FILES_PER_SET}).")
            declarado = sum(i.file_size for i in infos)
            if declarado > MAX_CONTAINER_EXTRACTED_BYTES:
                raise ReleaseValidationError(
                    f"{path.name}: o conteúdo extraído passaria de {MAX_CONTAINER_EXTRACTED_BYTES // 2**20} MB.")
            extraido = 0
            for info in infos:
                safe = Path(info.filename).name
                if not SAFE_NAME.match(safe) or (temp / safe).exists():
                    safe = f"part{len(files) + 1}.apk"                # nome estranho ou repetido não sobrescreve
                target = temp / safe
                with zf.open(info) as src, open(target, "wb") as dst:
                    while bloco := src.read(1 << 20):
                        extraido += len(bloco)
                        if extraido > MAX_CONTAINER_EXTRACTED_BYTES:
                            raise ReleaseValidationError(
                                f"{path.name}: o conteúdo extraído passou de {MAX_CONTAINER_EXTRACTED_BYTES // 2**20} MB.")
                        dst.write(bloco)
                files.append(target)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(temp, ignore_errors=True)
        raise ReleaseValidationError(f"{path.name}: contêiner ilegível.") from exc
    except BaseException:
        # Qualquer outra recusa ou erro no meio (limite, disco cheio): a extração parcial não fica no disco.
        shutil.rmtree(temp, ignore_errors=True)
        raise
    if not files:
        shutil.rmtree(temp, ignore_errors=True)
        raise ReleaseValidationError(f"{path.name}: contêiner sem nenhum APK dentro.")
    return CandidateSet(label=path.name, files=files, container=True, temp_dir=temp)


def validate(candidate: CandidateSet, inspector: ApkInspector, *, expected_package: str | None = None) -> ValidatedRelease:
    """Inspeciona cada arquivo e exige consistência. Qualquer inconsistência reprova o conjunto INTEIRO: instalar
    parte de um conjunto de splits deixaria o aparelho num estado que ninguém consegue descrever."""
    if not candidate.files:
        raise ReleaseValidationError(f"{candidate.label}: nenhum APK encontrado.")
    if len(candidate.files) > MAX_FILES_PER_SET:
        raise ReleaseValidationError(f"{candidate.label}: {len(candidate.files)} arquivos (limite {MAX_FILES_PER_SET}).")

    parts: list[ApkInfo] = []
    for path in candidate.files:
        try:
            parts.append(inspector.inspect(path))
        except ApkInspectionError as exc:
            raise ReleaseValidationError(str(exc)) from None

    packages = {p.package_name for p in parts}
    if len(packages) > 1:
        raise ReleaseValidationError(f"{candidate.label}: o conjunto mistura pacotes ({', '.join(sorted(packages))}).")
    package = parts[0].package_name
    if expected_package and package != expected_package:
        raise ReleaseValidationError(f"{candidate.label}: o pacote é {package}, e o esperado era {expected_package}.")

    codes = {p.version_code for p in parts}
    if len(codes) > 1:
        raise ReleaseValidationError(f"{candidate.label}: versionCode divergente entre os arquivos "
                                     f"({', '.join(str(c) for c in sorted(codes))}).")
    signatures = {p.signature_sha256 for p in parts}
    if len(signatures) > 1:
        raise ReleaseValidationError(f"{candidate.label}: os arquivos têm assinaturas diferentes; não são do mesmo conjunto.")

    bases = [p for p in parts if p.is_base]
    if len(bases) != 1:
        raise ReleaseValidationError(f"{candidate.label}: o conjunto precisa de exatamente um base.apk "
                                     f"(encontrados {len(bases)}).")
    splits = [p.split_name for p in parts if not p.is_base]
    duplicated = {s for s in splits if splits.count(s) > 1}
    if duplicated:
        raise ReleaseValidationError(f"{candidate.label}: split repetido ({', '.join(sorted(duplicated))}).")

    base = bases[0]
    if len(parts) == 1:
        artifact_type = "single"
    else:
        artifact_type = "unverified_split_set" if candidate.container else "split_set"
    abis = sorted({abi for p in parts for abi in p.abis})
    return ValidatedRelease(
        package_name=package,
        version_code=base.version_code,
        version_name=base.version_name,
        signature_sha256=base.signature_sha256,
        artifact_type=artifact_type,
        min_sdk=base.min_sdk,
        target_sdk=base.target_sdk,
        abis=abis,
        requires_gms=any(p.requires_gms for p in parts),
        parts=sorted(parts, key=lambda p: (not p.is_base, p.split_name or "")),
        set_hash=set_hash(parts),
        label=base.label,
        icon_entry=base.icon_entry,
    )


def set_hash(parts: list[ApkInfo]) -> str:
    """Identidade do conjunto pelo conteúdo: muda se qualquer arquivo mudar, e não depende de nome nem de ordem."""
    digest = hashlib.sha256()
    for line in sorted(f"{p.role}:{p.split_name or ''}:{p.sha256}" for p in parts):
        digest.update(line.encode())
    return digest.hexdigest()


def store(release: ValidatedRelease, catalog_root: Path) -> Path:
    """Copia o conjunto para o diretório imutável e grava o metadata gerado pelo backend.

    O caminho vem do conteúdo (`<pacote>/<versionCode>-<hash do conjunto>`), nunca do nome que o usuário deu. Se o
    diretório já existe com o mesmo hash, o conteúdo é idêntico por definição e nada é reescrito.
    """
    target = catalog_root / release.package_name / release.dir_name
    if target.exists() and (target / "metadata.json").is_file():
        return target
    staging = target.with_name(target.name + ".parcial")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        for part in release.parts:
            name = "base.apk" if part.is_base else f"{_safe_split(part.split_name)}.apk"
            shutil.copy2(part.path, staging / name)
        (staging / "metadata.json").write_text(json.dumps(metadata(release), ensure_ascii=False, indent=2),
                                               encoding="utf-8")
        shutil.rmtree(target, ignore_errors=True)
        staging.replace(target)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


#: Formatos de ícone que um navegador abre sozinho. `.xml` (ícone adaptativo) já foi descartado na inspeção; o que
#: sobra fora desta lista é raro o bastante para não valer um conversor no caminho da importação.
ICONE_ACEITO = {".png": "icon.png", ".webp": "icon.webp"}
#: Teto do ícone extraído. Um `ic_launcher` de 512×512 não passa de algumas dezenas de KB; qualquer coisa muito
#: maior é entrada errada ou ZIP malicioso, e descompactá-la em memória seria o problema, não o ícone perdido.
ICONE_MAX_BYTES = 2 * 1024 * 1024


def extract_icon(release: ValidatedRelease, target: Path) -> str | None:
    """Extrai o ícone do launcher do base.apk para a pasta da release. Devolve o nome do arquivo, ou `None`.

    Fica FORA de `store()` de propósito: o ícone é enfeite de catálogo, não faz parte do conjunto instalável.
    Se ele entrasse em `app_release_files`, `files_for_install` mandaria um PNG para o `install-multiple` — e a
    instalação inteira falharia por causa de uma imagem.
    """
    if not release.icon_entry:
        return None
    nome = ICONE_ACEITO.get(PurePosixPath(release.icon_entry).suffix.lower())
    if nome is None:
        return None
    base = next((p for p in release.parts if p.is_base), None)
    if base is None or not base.path.is_file():
        return None
    destino = target / nome
    if destino.is_file():
        return nome                       # reimportação do mesmo conjunto: o conteúdo é o mesmo por definição
    try:
        with zipfile.ZipFile(base.path) as z:
            info = z.getinfo(release.icon_entry)
            if info.file_size > ICONE_MAX_BYTES:
                log.warning("ícone de %s ignorado: %d bytes", release.package_name, info.file_size)
                return None
            dados = z.read(release.icon_entry)
    except (OSError, KeyError, zipfile.BadZipFile, ValueError):
        # Ícone é acréscimo: um APK sem a entrada declarada continua sendo uma release válida e instalável.
        log.warning("não foi possível extrair o ícone de %s", release.package_name, exc_info=True)
        return None
    try:
        target.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(dados)
    except OSError:
        log.warning("não foi possível gravar o ícone de %s", release.package_name, exc_info=True)
        return None
    return nome


def _safe_split(split_name: str | None) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", split_name or "split")
    return cleaned[:80] or "split"


def metadata(release: ValidatedRelease) -> dict:
    """O metadata é SEMPRE gerado a partir da inspeção; nunca lido de um arquivo que veio junto."""
    return {
        "package": release.package_name,
        "versionName": release.version_name,
        "versionCode": release.version_code,
        "artifactType": release.artifact_type,
        "signatureSha256": release.signature_sha256,
        "minSdk": release.min_sdk,
        "targetSdk": release.target_sdk,
        "abis": release.abis,
        "label": release.label,
        "icon": release.icon_entry,
        "setSha256": release.set_hash,
        "generatedAt": now_iso(),
        "files": [
            {
                "name": "base.apk" if p.is_base else f"{_safe_split(p.split_name)}.apk",
                "role": p.role,
                "split": p.split_name,
                "sha256": p.sha256,
                "sizeBytes": p.size_bytes,
                "abis": p.abis,
                "locales": p.locales,
                "densities": p.densities,
            }
            for p in release.parts
        ],
    }


def cleanup(candidate: CandidateSet) -> None:
    if candidate.temp_dir:
        shutil.rmtree(candidate.temp_dir, ignore_errors=True)
