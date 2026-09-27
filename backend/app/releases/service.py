"""Orquestra a importação de releases: descobrir na inbox, validar, catalogar e registrar.

Confiança na assinatura é progressiva e explícita. A primeira release de um pacote entra como `validated` e só vira
`installable` quando o operador aprova a assinatura de propósito. Depois disso, qualquer release com assinatura
diferente é bloqueada automaticamente — o sistema não vira autoridade de procedência, mas também não aceita em
silêncio um APK de outra origem numa atualização futura.
"""
from __future__ import annotations

import asyncio
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..config import Config
from ..events import EventBus
from ..models import ReleaseChannel, ReleaseDTO, ReleaseState
from ..modules.applications.infrastructure.app_repository import AppRepository
from ..storage import Storage
from ..util import now_iso
from . import catalog
from .catalog import InstalacaoIncerta, ReleaseValidationError  # `InstalacaoIncerta` reexportada: ver catalog.py
from .inspector import ApkInspector, sha256_of
from .repository import ReleaseRepository

log = logging.getLogger("poc.releases")

# Nome de arquivo aceito ao copiar um pacote de um aparelho: `base.apk`, `split_config.x86_64.apk`…
_NOME_DE_APK = re.compile(r"^[A-Za-z0-9_.\-]+\.apk$")


#: Erros de TRANSPORTE: o comando pode ter chegado, pode ter terminado, e a resposta é que não voltou. Só estes
#: viram incerteza; erro do `pm` (assinatura, ABI, espaço) é falha de verdade e continua sendo falha.
def _e_transporte(exc: BaseException) -> bool:
    from ..automation.driver import DriverTimeout, DriverUnavailable
    from ..devices.adb import AdbTimeout

    return isinstance(exc, (DriverTimeout, DriverUnavailable, AdbTimeout, asyncio.TimeoutError, TimeoutError))


@dataclass(slots=True)
class ImportOutcome:
    """Resultado por conjunto encontrado na inbox. Conjunto rejeitado não some: fica lá com o motivo registrado."""

    label: str
    ok: bool
    release_id: str | None = None
    package_name: str | None = None
    version_name: str | None = None
    version_code: int | None = None
    status: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return {"label": self.label, "ok": self.ok, "release_id": self.release_id, "package": self.package_name,
                "version_name": self.version_name, "version_code": self.version_code, "status": self.status,
                "reason": self.reason}


class ReleaseService:
    def __init__(self, cfg: Config, repo: ReleaseRepository, inspector: ApkInspector, bus: EventBus,
                 catalog_storage: Storage | None = None):
        self.cfg = cfg
        self.repo = repo
        self.inspector = inspector
        self.bus = bus
        #: Catálogo COMPARTILHADO de APK (item 5.7, achado #89). `None` = só o disco local, que é o certo com um
        #: backend só. Com storage compartilhado, o arquivo importado numa máquina é publicado nele e baixado
        #: pela outra na hora de instalar — sem isso, `app_releases` aponta para arquivos que só existem em quem
        #: importou, e o segundo backend vê a release como `installable` e falha ao instalar.
        self.catalog_storage = catalog_storage
        #: Chamado quando o import cadastra sozinho o app de um pacote novo (loja de apps, 26/09): quem monta o
        #: estado liga aqui o anúncio `apps.updated`, para o cartão aparecer na vitrine sem recarregar.
        self.ao_cadastrar_app: Any = None
        #: `instance_id -> pacote do app principal` (ou `None`). Ligado pelo AppState. Sem ele (os testes isolados de
        #: release), a prova de abertura termina como sempre terminou: com o app conferido na frente.
        self.pacote_principal_de: Any = None

    # ------------------------------------------------------------------ importação
    def import_inbox(self, *, source_reference: str | None = None, expected_package: str | None = None,
                     keep_source: bool = False) -> list[ImportOutcome]:
        """Varre `apks/inbox/`, valida cada conjunto e cataloga os aprovados."""
        if not self.inspector.available():
            raise ReleaseValidationError(
                "aapt2/apksigner não encontrados no Android SDK; sem eles não dá para inspecionar um APK.")
        found = catalog.discover(self.cfg.apk_inbox)
        candidates = found.sets + catalog.group_loose(found.loose, self.inspector)
        results: list[ImportOutcome] = []
        for candidate in candidates:
            try:
                results.append(self._import_one(candidate, source_reference=source_reference,
                                                expected_package=expected_package, keep_source=keep_source))
            except ReleaseValidationError as exc:
                results.append(ImportOutcome(label=candidate.label, ok=False, reason=str(exc)))
                self.bus.emit("log", f"Importação recusada ({candidate.label}): {exc}", level="warn")
            finally:
                catalog.cleanup(candidate)
        if not results:
            self.bus.emit("log", f"Nenhum APK encontrado em {self.cfg.file.paths.apk_inbox}.", level="warn")
        return results

    def import_dir(self, folder: Path, *, source_type: str, source_reference: str | None,
                   expected_package: str | None) -> ImportOutcome:
        """Importa UMA subpasta da inbox como um conjunto só, sem tocar no resto da inbox.

        `import_inbox` varre tudo o que estiver lá. Quem busca da loja não pode, de carona, importar arquivos que o
        usuário largou soltos por outro motivo — nem apagá-los.
        """
        if not self.inspector.available():
            raise ReleaseValidationError(
                "aapt2/apksigner não encontrados no Android SDK; sem eles não dá para inspecionar um APK.")
        files = sorted(p for p in folder.rglob("*.apk") if p.is_file())
        # Loja de apps (26/09): o dono também fornece `.xapk`/`.apks`/`.apkm` pelo painel. Contêiner vem SOZINHO —
        # misturado com `.apk` solto, não há como saber qual dos dois é o conjunto que ele quis mandar.
        conteineres = sorted(p for p in folder.iterdir()
                             if p.is_file() and p.suffix.lower() in catalog.CONTAINER_SUFFIXES)
        if conteineres and (files or len(conteineres) > 1):
            raise ReleaseValidationError("Mande o contêiner (.xapk/.apks/.apkm) sozinho, sem outros arquivos junto.")
        if not files and not conteineres:
            raise ReleaseValidationError("Nenhum APK foi copiado do aparelho.")
        # O contêiner é extraído para uma pasta temporária; quem chamou (upload) apaga a pasta de origem. Limpar pela
        # regra da inbox (`_clear_source`) apagaria um arquivo de MESMO NOME largado na inbox por outro motivo.
        try:
            candidate = (catalog._extract_container(conteineres[0]) if conteineres
                         else catalog.CandidateSet(label=folder.name, files=files))
        except ReleaseValidationError as exc:
            # Contêiner ilegível ou vazio é recusa de VALIDAÇÃO como qualquer outra: vira desfecho com o motivo, não
            # exceção atravessando a rota — o painel mostra "o arquivo foi recusado" pelo mesmo caminho de sempre.
            self.bus.emit("log", f"Importação recusada ({conteineres[0].name}): {exc}", level="warn")
            return ImportOutcome(label=conteineres[0].name, ok=False, reason=str(exc))
        try:
            return self._import_one(candidate, source_reference=source_reference, expected_package=expected_package,
                                    keep_source=bool(conteineres), source_type=source_type)
        except ReleaseValidationError as exc:
            self.bus.emit("log", f"Importação recusada ({candidate.label}): {exc}", level="warn")
            return ImportOutcome(label=candidate.label, ok=False, reason=str(exc))
        finally:
            catalog.cleanup(candidate)

    def import_file(self, path: Path, *, expected_package: str, source_type: str,
                    source_reference: str | None) -> ImportOutcome:
        """Importa UM `.apk` solto pelo caminho, com a origem preservada — ver `ensure_builtin_release`, hoje o
        único chamador.

        `import_dir`/`import_inbox` apagam a origem ao final: a inbox é descartável por definição, e um conjunto
        copiado do aparelho também. Aqui a origem pode ser um arquivo VERSIONADO no repositório (o APK do app de
        QA embutido) — apagá-lo seria efeito colateral grave de uma conveniência de subida. `keep_source=True`
        é por isso fixo, não um parâmetro: nenhum chamador futuro deve poder esquecer disso.
        """
        if not self.inspector.available():
            raise ReleaseValidationError(
                "aapt2/apksigner não encontrados no Android SDK; sem eles não dá para inspecionar um APK.")
        candidate = catalog.CandidateSet(label=path.name, files=[path])
        try:
            return self._import_one(candidate, source_reference=source_reference, expected_package=expected_package,
                                    keep_source=True, source_type=source_type)
        except ReleaseValidationError as exc:
            self.bus.emit("log", f"Importação recusada ({candidate.label}): {exc}", level="warn")
            return ImportOutcome(label=candidate.label, ok=False, reason=str(exc))
        finally:
            catalog.cleanup(candidate)

    def _import_one(self, candidate: catalog.CandidateSet, *, source_reference: str | None,
                    expected_package: str | None, keep_source: bool, source_type: str = "inbox") -> ImportOutcome:
        release = catalog.validate(candidate, self.inspector, expected_package=expected_package)
        status, detail = self._signature_verdict(release.package_name, release.signature_sha256)
        target = catalog.store(release, self.cfg.apk_catalog)
        meta = catalog.metadata(release)
        catalog_dir = str(target.relative_to(self.cfg.root))
        self._publicar_no_catalogo(catalog_dir, target, meta["files"])
        # O ícone é extraído DEPOIS do conjunto estar no lugar: ele mora na mesma pasta imutável, mas fora da
        # lista de arquivos instaláveis — ver `catalog.extract_icon`.
        icon_file = catalog.extract_icon(release, target)
        if icon_file:
            self._publicar_icone(catalog_dir, target / icon_file)
        self.repo.save_release(
            release_id=release.release_id, package_name=release.package_name, version_name=release.version_name,
            version_code=release.version_code, artifact_type=release.artifact_type,
            signature_sha256=release.signature_sha256, min_sdk=release.min_sdk, target_sdk=release.target_sdk,
            abis=release.abis, catalog_dir=catalog_dir, source_type=source_type,
            source_reference=source_reference, status=status, detail=detail, files=meta["files"],
            requires_gms=release.requires_gms, label=release.label, icon_file=icon_file)
        self._cadastrar_se_novo(release.package_name, release.label)
        if not keep_source:
            self._clear_source(candidate)
        self.bus.emit("log", f"Release importada: {release.package_name} {release.version_name} "
                             f"({release.version_code}) — {status.value}",
                      data={"release_id": release.release_id, "status": status.value})
        return ImportOutcome(label=candidate.label, ok=True, release_id=release.release_id,
                             package_name=release.package_name, version_name=release.version_name,
                             version_code=release.version_code, status=status.value, reason=detail)

    def _cadastrar_se_novo(self, package: str, rotulo: str | None) -> None:
        """Decisão do dono (26/09): versão de um pacote que ninguém cadastrou cadastra o app sozinha — a vitrine
        nunca esconde uma versão importada. Falhar aqui não desfaz o import: a versão já está no catálogo."""
        try:
            criado = AppRepository(self.repo.db).cadastrar_se_novo(package, rotulo)
        except Exception:  # noqa: BLE001
            log.exception("cadastro automático do app %s falhou", package)
            return
        if criado:
            self.bus.emit("log", f"Aplicativo {rotulo or package} cadastrado sozinho ao importar a primeira versão "
                                 f"de {package}; escolha a categoria dele na loja.", data={"app_id": criado})
            if self.ao_cadastrar_app is not None:
                self.ao_cadastrar_app()

    def _signature_verdict(self, package_name: str, signature: str) -> tuple[ReleaseState, str | None]:
        trusted = self.repo.trusted_signer(package_name)
        if trusted is None:
            return ReleaseState.validated, ("Assinatura ainda não aprovada para este pacote. "
                                            "Aprove-a explicitamente para liberar a instalação.")
        if trusted.lower() != signature.lower():
            return ReleaseState.invalid, ("Assinatura diferente da aprovada para este pacote; instalação bloqueada "
                                          "até nova aprovação explícita.")
        return ReleaseState.installable, None

    def _clear_source(self, candidate: catalog.CandidateSet) -> None:
        """O conteúdo já está no catálogo imutável; o original sai da inbox para não ser reimportado sem querer."""
        if candidate.container or candidate.temp_dir:
            source = self.cfg.apk_inbox / candidate.label
        else:
            roots = {p.parent for p in candidate.files}
            source = next(iter(roots)) if len(roots) == 1 and next(iter(roots)) != self.cfg.apk_inbox else None  # type: ignore[assignment]
            if source is None:
                for p in candidate.files:
                    p.unlink(missing_ok=True)
                return
        try:
            if source.is_dir():
                shutil.rmtree(source, ignore_errors=True)
            else:
                source.unlink(missing_ok=True)
        except OSError:
            log.warning("não foi possível limpar a inbox: %s", candidate.label)

    # ------------------------------------------------------------------ confiança e consulta
    def approve_signature(self, release_id: str, *, note: str | None = None) -> ReleaseDTO:
        """Aprovação explícita do operador. Promove as releases do pacote que casam com esta assinatura."""
        row = self.repo.release_row(release_id)
        if row is None:
            raise ReleaseValidationError("Release não encontrada.")
        self.repo.approve_signer(row["package_name"], row["signature_sha256"], note)
        for other in self.repo.list_releases(row["package_name"]):
            if other.signature_sha256.lower() == row["signature_sha256"].lower():
                if other.status in (ReleaseState.validated, ReleaseState.invalid):
                    self.repo.set_status(other.id, ReleaseState.installable, None)
            elif other.status == ReleaseState.installable:
                self.repo.set_status(other.id, ReleaseState.invalid,
                                     "Assinatura diferente da aprovada para este pacote.")
        self.bus.emit("log", f"Assinatura aprovada para {row['package_name']}: {row['signature_sha256'][:16]}…",
                      level="warn", data={"package": row["package_name"]})
        return self.repo.release_dto(self.repo.release_row(release_id))  # type: ignore[arg-type]

    def list_releases(self, package_name: str | None = None) -> list[ReleaseDTO]:
        return self.repo.list_releases(package_name)

    def files_for_install(self, release_id: str) -> tuple[ReleaseDTO, list[Path]]:
        """Caminhos do conjunto, conferindo o hash de cada arquivo: artefato validado é imutável, e uma troca
        silenciosa no disco tem de virar erro, não instalação."""
        row = self.repo.release_row(release_id)
        if row is None:
            raise ReleaseValidationError("Release não encontrada.")
        base = self.cfg.path(row["catalog_dir"])
        paths: list[Path] = []
        for f in self.repo.files_of(release_id):
            path = base / f["file_name"]
            if not path.is_file():
                # Ainda não está aqui: tenta o catálogo compartilhado. O hash é conferido logo abaixo, no MESMO
                # ponto de sempre — o que vem do storage não ganha confiança extra por ter vindo de lá.
                self._baixar_do_catalogo(row["catalog_dir"], f["file_name"], base)
            if not path.is_file():
                # NÃO é adulteração, e a diferença importa: adulterado marca a release como `invalid` e exige
                # gente; ausente aqui é um arquivo que está em OUTRA máquina e continua íntegro onde está. A
                # release segue `installable`, porque ela é.
                raise ReleaseValidationError(
                    f"Arquivo ausente NESTE servidor: {f['file_name']}. O catálogo de APK "
                    f"({row['catalog_dir']}) é local, e esta release foi importada em outra máquina — ou a "
                    "pasta foi restaurada sem os arquivos. O artefato NÃO está adulterado. Ver docs/banco.md "
                    "(catálogo de APK entre servidores).")
            digest, _ = sha256_of(path)
            if digest.lower() != f["sha256"].lower():
                self.repo.set_status(release_id, ReleaseState.invalid, "Artefato adulterado: o hash mudou no disco.")
                raise ReleaseValidationError(f"Artefato adulterado: {f['file_name']} não confere com o hash registrado.")
            paths.append(path)
        return self.repo.release_dto(row), paths

    def _publicar_no_catalogo(self, catalog_dir: str, pasta: Path, arquivos: list[dict[str, Any]]) -> None:
        """Manda o conjunto recém-importado para o catálogo compartilhado, se houver um."""
        if self.catalog_storage is None:
            return
        for f in arquivos:
            origem = pasta / f["file_name"]
            if not origem.is_file():
                continue
            try:
                self.catalog_storage.put(f"{catalog_dir}/{f['file_name']}", origem.read_bytes(),
                                         content_type="application/vnd.android.package-archive")
            except Exception:  # noqa: BLE001 - publicar é acréscimo: o arquivo local já está catalogado
                log.exception("catálogo compartilhado: falha ao publicar %s", f["file_name"])

    def _publicar_icone(self, catalog_dir: str, origem: Path) -> None:
        """O ícone segue o mesmo caminho do APK: sem isto, o segundo backend mostraria cartão sem imagem para
        toda release importada na outra máquina."""
        if self.catalog_storage is None or not origem.is_file():
            return
        tipo = "image/webp" if origem.suffix.lower() == ".webp" else "image/png"
        try:
            self.catalog_storage.put(f"{catalog_dir}/{origem.name}", origem.read_bytes(), content_type=tipo)
        except Exception:  # noqa: BLE001 - publicar é acréscimo: o ícone local já está no catálogo
            log.exception("catálogo compartilhado: falha ao publicar o ícone %s", origem.name)

    def icon_bytes(self, release_id: str) -> tuple[bytes, str] | None:
        """Bytes do ícone da release e o tipo MIME, ou `None` se ela não tem ícone servível.

        Mesma regra de `files_for_install`: o que não está NESTE servidor é buscado no catálogo compartilhado e
        fica em cache local. Diferença: aqui a ausência não é erro — um cartão sem imagem continua legível.
        """
        row = self.repo.release_row(release_id)
        if row is None or not row["icon_file"]:
            return None
        nome = PurePosixPath(row["icon_file"]).name
        if nome not in catalog.ICONE_ACEITO.values():
            return None                    # nome vindo do banco nunca vira caminho livre de leitura de arquivo
        base = self.cfg.path(row["catalog_dir"])
        caminho = base / nome
        if not caminho.is_file():
            self._baixar_do_catalogo(row["catalog_dir"], nome, base)
        if not caminho.is_file():
            return None
        return caminho.read_bytes(), ("image/webp" if nome.endswith(".webp") else "image/png")

    def _baixar_do_catalogo(self, catalog_dir: str, file_name: str, base: Path) -> None:
        """Traz o arquivo do catálogo compartilhado para o cache local. O hash é conferido por quem chamou."""
        if self.catalog_storage is None:
            return
        try:
            dados = self.catalog_storage.get(f"{catalog_dir}/{file_name}")
        except Exception:  # noqa: BLE001 - storage fora do ar vira "ausente neste servidor", com a mensagem certa
            log.exception("catálogo compartilhado: falha ao buscar %s", file_name)
            return
        if dados is None:
            return
        base.mkdir(parents=True, exist_ok=True)
        (base / file_name).write_bytes(dados)
        log.info("catálogo compartilhado: %s baixado para o cache local", file_name)

    # ------------------------------------------------------------------ instalação com estado observado
    async def install_on(self, rt: Any, release_id: str, installer: Any, *, allow_downgrade: bool = False,
                         select_for_device: bool = True, operation: str | None = None,
                         session_reason: str | None = None) -> dict:
        """Instala a release no aparelho e só a considera pronta depois de ler o estado DO APARELHO e abrir o app.

        Sequência: conferir hash -> conferir compatibilidade -> **guardar para onde voltar** -> marcar operação
        pendente -> instalar -> observar -> comparar com o esperado -> abrir e ver se o processo sobrevive ->
        **invalidar a sessão**. Código de retorno zero do ADB não é prova.

        Atualizar, reinstalar e voltar de versão são a MESMA operação daqui de dentro: o que muda é o rótulo, o
        `-d` e o que se conta a quem chamou. Um caminho separado de upgrade duplicaria justamente a parte
        delicada — o snapshot do alvo de rollback e a invalidação da sessão.
        """
        from ..devices.installer import compatibility, drift_of, select_splits
        from ..models import InstalledAppState

        dto, todos = self.files_for_install(release_id)
        if dto.status != ReleaseState.installable:
            raise ReleaseValidationError(
                f"A release está em '{dto.status.value}' e não pode ser instalada. {dto.detail or ''}".strip())
        if dto.channel is ReleaseChannel.quarantined:
            raise ReleaseValidationError(
                "Esta versão está em quarentena: ela já falhou a prova num aparelho. "
                f"{dto.channel_detail or ''} Para tentar de novo, coloque-a em canário de propósito.".strip())

        package = dto.package_name
        try:
            profile = await installer.profile(rt)
        except Exception as exc:  # noqa: BLE001 - ler o perfil é o PRIMEIRO passo: aqui nada tocou o disco ainda
            if not _e_transporte(exc):
                raise
            # Nada foi instalado, e mesmo assim o invólucro da porta do app gravava `install_failed` — a entrega
            # ficava presa esperando uma pessoa por causa de um timeout de leitura.
            raise self._deixar_para_reler(rt, package, exc, etapa="a leitura do perfil do aparelho") from exc
        compat = compatibility(min_sdk=dto.min_sdk, abis=dto.supported_abis, profile=profile)
        if compat.blocked:
            self.repo.upsert_app_state(rt.id, package, state=InstalledAppState.incompatible.value,
                                       desired_release_id=release_id, detail=compat.reason, pending_op=None)
            raise ReleaseValidationError(f"Aparelho incompatível com esta release: {compat.reason}.")

        # O que havia ANTES, lido antes de qualquer escrita: é daqui que sai o alvo do rollback. Se a linha fosse
        # sobrescrita primeiro, o alvo sumiria exatamente quando passa a ser necessário.
        antes = self.repo.app_state(rt.id, package)
        anterior = antes["installed_release_id"] if antes else None
        op = operation or self._operation_kind(antes, dto)

        splits = [f.split_name for f in dto.files if f.role == "split" and f.split_name]
        escolha = select_splits(splits, profile) if select_for_device else None
        if escolha and escolha.filtered:
            manter = set(escolha.chosen)
            por_nome = {f.file_name: f for f in dto.files}
            paths = [p for p in todos
                     if (meta := por_nome.get(p.name)) is not None
                     and (meta.role == "base" or meta.split_name in manter)]
            expected_splits = ["base"] + escolha.chosen
            self.bus.emit("log", f"{rt.id}: {escolha.reason} ({', '.join(escolha.skipped)})", instance_id=rt.id)
        else:
            paths, expected_splits = todos, ["base"] + splits
        self._avisar_densidade(rt, package, expected_splits, profile)

        # Para onde voltar DEPOIS desta instalação — calculado do estado anterior, mas só gravado quando o disco
        # mudar de fato. Gravar antes faria uma instalação malsucedida apagar o alvo do rollback justamente quando
        # ele passa a ser necessário: a tentativa de voltar que falha viraria o novo "anterior" de si mesma.
        proximo_anterior = (anterior if anterior != release_id
                            else (antes["previous_release_id"] if antes else None))
        self.repo.upsert_app_state(rt.id, package, desired_release_id=release_id, pending_op="install",
                                   pending_op_at=now_iso(), state=InstalledAppState.installing.value,
                                   detail=compat.reason, drift_kind=None, last_operation=op)
        self.bus.emit("log", f"{rt.id}: {self._verbo(op)} {package} {dto.version_name} ({dto.version_code})…",
                      instance_id=rt.id)
        ja_observado = None
        try:
            await installer.install(rt, paths=paths, allow_downgrade=allow_downgrade)
        except Exception as exc:  # noqa: BLE001 - a falha tem de ficar registrada, não deixar o estado preso
            if _e_transporte(exc):
                # Timeout do adb NÃO é prova de que a instalação falhou: o `pm` pode ter concluído depois que a
                # leitura desistiu. Antes de decretar `install_failed` (estado pegajoso que só sai com
                # "Distribuir de novo", o que REINSTALA), pergunta-se ao aparelho o que aconteceu.
                ja_observado = await self._reler_depois_do_timeout(rt, package, installer, dto, exc)
            else:
                self.repo.upsert_app_state(rt.id, package, state=InstalledAppState.install_failed.value,
                                           pending_op=None, pending_op_at=None, detail=str(exc)[:300])
                self._prova(dto, rt.id, stage="install", ok=False, detail=str(exc)[:300])
                self.bus.emit("log", f"{rt.id}: instalação de {package} falhou — {exc}", level="error",
                              instance_id=rt.id)
                raise

        # O disco mudou: agora sim o alvo do rollback é a release que estava aqui até um instante atrás.
        # `expected_splits` é gravado junto: sem a escolha registrada, a verificação seguinte cobraria os splits de
        # outra configuração — que de propósito não foram instalados — e acusaria divergência para sempre.
        self.repo.upsert_app_state(rt.id, package, pending_op="verify", state=InstalledAppState.verifying.value,
                                   previous_release_id=proximo_anterior, expected_splits=expected_splits)
        # E a sessão observada deixa de valer JÁ — não no fim do caminho feliz. Uma divergência de versão ou uma
        # sonda de abertura que falha interrompem o resto do método, e a sessão não pode continuar dizendo
        # "verificada" sobre um app que já foi substituído no disco.
        self._app_mudou(rt.id, package, session_reason or self._motivo_de_sessao(op, antes, dto))
        if ja_observado is not None:
            observed = ja_observado                 # a releitura do timeout já respondeu; não se lê duas vezes
        else:
            try:
                observed = await installer.inspect(rt, package)
            except Exception as exc:  # noqa: BLE001 - a leitura DEPOIS da instalação é o caso do achado #73
                if not _e_transporte(exc):
                    raise
                # O disco já mudou e a leitura não respondeu. Dizer `install_failed` aqui seria a mentira
                # documentada em campo: app instalado e funcionando, painel exigindo reinstalar.
                raise self._deixar_para_reler(rt, package, exc, etapa="a leitura logo depois da instalação") from exc
        state, drift, detail = drift_of(observed, expected_version_code=dto.version_code,
                                        expected_splits=expected_splits)
        common = {
            "observed_version_name": observed.version_name, "observed_version_code": observed.version_code,
            "observed_splits": observed.splits, "first_install_time": observed.first_install_time,
            "last_update_time": observed.last_update_time, "pending_op": None, "pending_op_at": None,
        }
        if state is not InstalledAppState.installed:
            self.repo.upsert_app_state(rt.id, package, state=state.value, drift_kind=drift, detail=detail, **common)
            self._prova(dto, rt.id, stage="install", ok=False, detail=detail)
            raise ReleaseValidationError(detail or "A instalação não pôde ser comprovada no aparelho.")
        self._prova(dto, rt.id, stage="install", ok=True, detail=f"versionCode {observed.version_code} no aparelho")

        try:
            ok, why = await installer.launch_probe(rt, package)
        except Exception as exc:  # noqa: BLE001 - o app JÁ está instalado; só a prova de abertura não respondeu
            if not _e_transporte(exc):
                raise
            raise self._deixar_para_reler(rt, package, exc, etapa="a prova de abertura do app") from exc
        await self._recolher_se_secundario(rt, package, installer)
        final = InstalledAppState.ready if ok else InstalledAppState.verify_failed
        self.repo.upsert_app_state(rt.id, package, state=final.value, installed_release_id=release_id if ok else None,
                                   verified_at=now_iso() if ok else None, drift_kind=None, detail=why, **common)
        self._prova(dto, rt.id, stage="launch", ok=ok, detail=why)
        if ok and compat.verdict == "uncertain":
            self.bus.emit("log", f"{rt.id}: {package} roda com ABI traduzida ({compat.translated_abi}) — confirmado "
                                 "abrindo o app", instance_id=rt.id)
        self.bus.emit("log", f"{rt.id}: {package} {observed.version_name} ({observed.version_code}) — {why}",
                      level="info" if ok else "warn", instance_id=rt.id)
        if not ok:
            raise ReleaseValidationError(f"O app foi instalado mas não passou na prova de abertura: {why}.")
        return self.repo.app_state_dto(self.repo.app_state(rt.id, package)).model_dump(mode="json")

    # ------------------------------------------------------------------ apoio da instalação
    async def _recolher_se_secundario(self, rt: Any, package: str, installer: Any) -> None:
        """Depois da prova de abertura de um app que NÃO é o principal do aparelho: tela inicial e `force-stop`.

        Medido na produção em 26/09: o app de QA distribuído ao android-01 (aparelho de conta Instagram) ficou em
        primeiro plano depois da prova de abertura, e dois "Abrir app" do Instagram seguidos terminaram `uncertain`
        ("não apareceu em primeiro plano em 90 s"); logo depois de um HOME, o Instagram abriu na hora. A prova já
        valeu (o app abriu e ficou na frente); o que sobra é devolver o aparelho como estava, com o processo parado
        — o que também libera memória num convidado de 1,5 GB. O app principal fica como sempre ficou: aberto é
        exatamente onde a próxima tarefa dele começa. Recolher é arrumação: falhar aqui não desfaz a prova.
        """
        if self.pacote_principal_de is None:
            return
        try:
            if self.pacote_principal_de(rt.id) == package:
                return
            await installer.recolher(rt, package)
        except Exception as exc:  # noqa: BLE001 - arrumar a tela nunca derruba uma instalação comprovada
            log.info("%s: não foi possível recolher %s depois da prova de abertura (%s)", rt.id, package, exc)

    @staticmethod
    def _operation_kind(antes: Any, dto: ReleaseDTO) -> str:
        """Rótulo do que está acontecendo com o disco, decidido pelo que o aparelho já tinha."""
        from ..models import InstalledAppState

        if antes is None or antes["state"] == InstalledAppState.missing.value:
            return "install"
        if antes["installed_release_id"] == dto.id:
            return "reinstall"
        atual = antes["observed_version_code"]
        if atual is None:
            return "install"
        if dto.version_code > atual:
            return "upgrade"
        if dto.version_code < atual:
            return "downgrade"
        return "reinstall"

    @staticmethod
    def _verbo(op: str) -> str:
        return {"install": "instalando", "reinstall": "reinstalando", "upgrade": "atualizando",
                "downgrade": "voltando a versão de", "rollback": "revertendo para"}.get(op, "instalando")

    @staticmethod
    def _motivo_de_sessao(op: str, antes: Any, dto: ReleaseDTO) -> str:
        """A matriz de invalidação em uma frase, para ficar no detalhe da sessão e no painel."""
        de = (antes["observed_version_code"] if antes else None) or "?"
        if op in ("upgrade", "downgrade"):
            para = "atualizado" if op == "upgrade" else "revertido"
            return (f"o aplicativo foi {para} ({de} → {dto.version_code}); a sessão precisa ser observada de novo "
                    "antes de qualquer pedido de senha")
        if op == "rollback":
            return (f"o aplicativo voltou para a versão {dto.version_code}; a sessão precisa ser observada de novo "
                    "antes de qualquer pedido de senha")
        if op == "reinstall":
            return "o aplicativo foi reinstalado; a sessão precisa ser observada de novo"
        return "o aplicativo foi instalado neste aparelho"

    def _avisar_densidade(self, rt: Any, package: str, escolhidos: list[str], profile: Any) -> None:
        """Aparelho de outra densidade recebe o split que existe — e agora fica sabendo.

        A regra do seletor é conservadora de propósito: se nenhum split de densidade serve, entram todos (melhor
        instalar demais do que instalar um app sem recursos). O app funciona, com os recursos reescalados. O que
        faltava era dizer isso: a ABI incompatível já é recusada com motivo, a densidade instalava calada.
        """
        from ..devices.installer import config_kind

        faixas = [alvo for nome in escolhidos if (kind := config_kind(nome)[0]) == "density"
                  and (alvo := config_kind(nome)[1])]
        if not faixas or getattr(profile, "density_bucket", None) in faixas:
            return
        self.bus.emit("log", f"{rt.id}: este conjunto de {package} traz recursos de {', '.join(faixas)} e a tela "
                             f"deste aparelho é {profile.density_bucket} ({profile.density} dpi) — o app instala e "
                             "roda, com os recursos reescalados", level="warn", instance_id=rt.id)

    # ------------------------------------------------------------------ incerteza com saída
    def _deixar_para_reler(self, rt: Any, package: str, exc: BaseException, *, etapa: str) -> InstalacaoIncerta:
        """Grava "ainda vai ser relido" e devolve a exceção a levantar. Nunca afirma sucesso nem falha.

        `verifying` com `pending_op=None` é exatamente o que a releitura automática procura (na entrada no ar do
        aparelho e no start do backend): é o estado que tem saída sozinho, ao contrário de `install_failed`.
        """
        from ..models import InstalledAppState

        motivo = f"{etapa} não respondeu ({exc}); o estado será relido do aparelho antes de qualquer decisão"
        self.repo.upsert_app_state(rt.id, package, state=InstalledAppState.verifying.value,
                                   pending_op=None, pending_op_at=None, detail=motivo[:300])
        self.bus.emit("log", f"{rt.id}: {package} — resultado incerto: {motivo}", level="warn", instance_id=rt.id)
        return InstalacaoIncerta(motivo)

    async def _reler_depois_do_timeout(self, rt: Any, package: str, installer: Any, dto: ReleaseDTO,
                                       exc: BaseException) -> Any:
        """O adb não respondeu durante a instalação. Pergunta ao APARELHO o que de fato aconteceu.

        Três desfechos, todos honestos: a versão esperada está lá (a instalação valeu — segue o caminho normal de
        verificação); o aparelho respondeu e a versão NÃO está lá (falha de verdade, registrada como tal); ou nem
        a releitura respondeu (incerto, e com saída).
        """
        from ..models import InstalledAppState

        try:
            observed = await installer.inspect(rt, package)
        except Exception as leitura:  # noqa: BLE001 - nem a releitura respondeu: o desfecho é desconhecido
            raise self._deixar_para_reler(rt, package, exc, etapa="a instalação") from leitura
        if observed.present and observed.version_code == dto.version_code:
            self.bus.emit("log", f"{rt.id}: a instalação de {package} excedeu o tempo de resposta, mas o aparelho "
                                 f"mostra a versão {observed.version_name} ({observed.version_code}) instalada — "
                                 "seguindo para a verificação", level="warn", instance_id=rt.id)
            return observed
        detalhe = (f"{exc} — a releitura do aparelho não encontrou a versão esperada "
                   f"(lá está: {observed.version_code if observed.present else 'nada'})")
        self.repo.upsert_app_state(rt.id, package, state=InstalledAppState.install_failed.value,
                                   pending_op=None, pending_op_at=None, detail=detalhe[:300])
        self._prova(dto, rt.id, stage="install", ok=False, detail=detalhe[:300])
        self.bus.emit("log", f"{rt.id}: instalação de {package} falhou — {detalhe}", level="error", instance_id=rt.id)
        raise ReleaseValidationError(detalhe)

    def _app_mudou(self, instance_id: str, package: str, motivo: str) -> None:
        """Gancho preenchido pelo AppState. Sem ele, nada acontece — é assim que os testes de release seguem
        isolados do domínio social.

        O PACOTE vai junto porque mexer no disco de um app não diz nada sobre a sessão de outro: instalar o QA
        Messenger marcava a sessão do Instagram como não verificada, com o motivo "o aplicativo foi instalado
        neste aparelho", forçando reobservação e poluindo o painel. Quem decide se aquele pacote tem sessão a
        invalidar é o registro de aplicativos, do outro lado do gancho.
        """
        hook = getattr(self, "on_app_changed", None)
        if hook is None:
            return
        try:
            hook(instance_id, package, motivo)
        except Exception:  # noqa: BLE001 - invalidar sessão nunca pode derrubar a instalação
            log.exception("%s: falha ao invalidar a sessão depois de mexer no app", instance_id)

    def _prova(self, dto: ReleaseDTO, instance_id: str, *, stage: str, ok: bool, detail: str | None) -> None:
        """Registra a observação e, se esta versão está em prova de canário, decide a quarentena na hora."""
        self.repo.record_validation(dto.id, instance_id, stage=stage, ok=ok, detail=detail)
        if ok or dto.channel is not ReleaseChannel.canary or instance_id != dto.canary_instance_id:
            return
        self.repo.set_channel(dto.id, ReleaseChannel.quarantined,
                              detail=f"o canário {instance_id} falhou em '{stage}': {detail or 'sem detalhe'}")
        self.bus.emit("log", f"{dto.package_name} {dto.version_name} ({dto.version_code}) foi para a quarentena: "
                             f"o canário {instance_id} falhou em '{stage}'.", level="error", instance_id=instance_id)

    def reconcile_after_restart(self) -> int:
        """Operação interrompida por queda do backend nunca é repetida às cegas: vira 'precisa verificar'."""
        rows = self.repo.pending_operations()
        for r in rows:
            self.repo.upsert_app_state(
                r["instance_id"], r["package_name"], pending_op=None, pending_op_at=None,
                state="verifying", detail="Operação interrompida por reinício; o estado será relido do aparelho.")
        if rows:
            self.bus.emit("log", f"{len(rows)} instalação(ões) interrompida(s) por reinício: estado será reobservado.",
                          level="warn")
        return len(rows)

    async def verify_on(self, rt: Any, package: str, installer: Any) -> dict:
        """Relê o estado do aparelho e compara com o que o banco esperava. É assim que a divergência aparece."""
        from ..devices.installer import drift_of
        from ..models import InstalledAppState

        from ..db import loads

        row = self.repo.app_state(rt.id, package)
        expected_code: int | None = None
        expected_splits: list[str] = []
        release_id = (row["installed_release_id"] or row["desired_release_id"]) if row else None
        if release_id and (rel := self.repo.release_row(release_id)) is not None:
            expected_code = rel["version_code"]
            # O que foi de fato escolhido para ESTE aparelho manda. A lista completa do conjunto só vale quando
            # não houve escolha registrada — instalação anterior à filtragem, ou conjunto instalado inteiro.
            expected_splits = loads(row["expected_splits"], []) or (
                ["base"] + [f["split_name"] for f in self.repo.files_of(release_id)
                            if f["role"] == "split" and f["split_name"]])
        observed = await installer.inspect(rt, package)
        state, drift, detail = drift_of(observed, expected_version_code=expected_code,
                                        expected_splits=expected_splits or None)
        if state is InstalledAppState.installed and release_id:
            state = InstalledAppState.ready
        self.repo.upsert_app_state(
            rt.id, package, state=state.value, drift_kind=drift, detail=detail,
            observed_version_name=observed.version_name, observed_version_code=observed.version_code,
            observed_splits=observed.splits, first_install_time=observed.first_install_time,
            last_update_time=observed.last_update_time, pending_op=None, pending_op_at=None,
            # A leitura do aparelho ACONTECEU, qualquer que seja o resultado: "ausente" observado agora é tão
            # verificado quanto "pronto". Com `None` aqui, o android-06 recém-resetado aparecia "verificado nunca"
            # logo depois de "Verificar app no aparelho" — indistinguível de nunca inspecionado (25/09/2026).
            verified_at=now_iso())
        if drift:
            self.bus.emit("log", f"{rt.id}: divergência no app {package} — {detail}", level="warn", instance_id=rt.id)
        return self.repo.app_state_dto(self.repo.app_state(rt.id, package)).model_dump(mode="json")

    # ================================================================== a loja como fonte
    async def sync_from_store(self, rt: Any, package: str, installer: Any) -> dict:
        """Copia da loja o pacote que o USUÁRIO instalou pela Play Store e o põe no catálogo.

        A loja é a fonte oficial: o app chegou lá pela Play Store, com a conta do próprio usuário. Daqui em diante é o
        pipeline de sempre — inspeção pelo conteúdo, catálogo imutável, assinatura aprovada de propósito, canário.
        Nada é baixado da rede por este método: ele só copia, por adb, o que o Android já tem em disco.

        Os arquivos vão para uma SUBPASTA da inbox, que é importada sozinha: o que o usuário tiver largado solto na
        inbox por outro motivo não é importado de carona, nem apagado.
        """
        from ..models import InstalledAppState

        observed = await installer.inspect(rt, package)
        # O que a loja tem instalado fica registrado mesmo quando não há nada para copiar: é assim que o painel sabe
        # comparar "versão na loja" com "versão no catálogo". Sem release associada — a loja é fonte, não destino.
        self.repo.upsert_app_state(
            rt.id, package,
            state=(InstalledAppState.installed if observed.present else InstalledAppState.missing).value,
            observed_version_name=observed.version_name, observed_version_code=observed.version_code,
            observed_splits=observed.splits, first_install_time=observed.first_install_time,
            last_update_time=observed.last_update_time, pending_op=None, pending_op_at=None, drift_kind=None,
            detail="instalado pela Play Store" if observed.present else "ainda não instalado pela Play Store")
        if not observed.present or not observed.paths:
            raise ReleaseValidationError(
                f"{package} não está instalado na loja ({rt.id}). Abra a página dele na Play Store desse aparelho e "
                "toque em Instalar.")

        ja = self._ja_catalogada(package, observed.version_code, observed.splits)
        if ja is not None:
            self.bus.emit("log", f"{rt.id}: a loja tem {package} {observed.version_name} ({observed.version_code}), "
                                 "que já está no catálogo — nada a copiar.", instance_id=rt.id)
            return {"outcome": "unchanged", "release_id": ja["id"], "package": package,
                    "version_name": observed.version_name, "version_code": observed.version_code}

        pasta = self.cfg.apk_inbox / f"loja-{package}-{observed.version_code}"
        shutil.rmtree(pasta, ignore_errors=True)                     # sobra de uma cópia interrompida
        pasta.mkdir(parents=True, exist_ok=True)
        self.bus.emit("log", f"{rt.id}: copiando {package} {observed.version_name} ({observed.version_code}) da loja "
                             f"— {len(observed.paths)} arquivo(s)…", instance_id=rt.id)
        try:
            for remoto in observed.paths:
                nome = PurePosixPath(remoto).name
                if not _NOME_DE_APK.match(nome):
                    raise ReleaseValidationError("O aparelho devolveu um nome de arquivo inesperado para o pacote.")
                destino = str(pasta / nome)
                await rt.executor.run(lambda r=remoto, d=destino: rt.adb.pull(r, d, timeout=600),
                                      timeout=660, label="copiar APK da loja")
            outcome = await asyncio.to_thread(
                self.import_dir, pasta, source_type="store", expected_package=package,
                # Gerado, nunca texto livre: a conta usada na loja não tem por que aparecer aqui.
                source_reference=f"Play Store via {rt.id} em {now_iso()[:10]}")
        finally:
            shutil.rmtree(pasta, ignore_errors=True)                 # importada ou recusada, a cópia não fica na inbox
        return {"outcome": "imported" if outcome.ok else "rejected", **outcome.to_dict()}

    def _ja_catalogada(self, package: str, version_code: int | None, splits: list[str]) -> Any | None:
        """A MESMA versão com os MESMOS splits já está no catálogo? Então copiar de novo seria só trabalho."""
        if version_code is None:
            return None
        esperados = {s for s in splits if s and s != "base"}
        for row in self.repo.db.query("SELECT * FROM app_releases WHERE package_name=? AND version_code=?",
                                      (package, version_code)):
            tem = {f["split_name"] for f in self.repo.files_of(row["id"]) if f["role"] == "split" and f["split_name"]}
            if tem == esperados:
                return row
        return None

    def store_status(self, store_id: str | None, package: str) -> dict:
        """Loja × catálogo, para o painel e a CLI decidirem se há versão nova a buscar."""
        row = self.repo.app_state(store_id, package) if store_id else None
        na_loja = row["observed_version_code"] if row else None
        catalogo = self.repo.db.scalar("SELECT MAX(version_code) FROM app_releases WHERE package_name=?", (package,))
        alvo = self.promoted_release(package)
        return {"instance_id": store_id, "package": package,
                "store_version_code": na_loja, "store_version_name": row["observed_version_name"] if row else None,
                "catalog_version_code": catalogo,
                "update_available": bool(na_loja is not None and (catalogo is None or na_loja > catalogo)),
                "fleet_target_release_id": alvo.id if alvo else None,
                "fleet_target_version_code": alvo.version_code if alvo else None}

    # ================================================================== canário, promoção, quarentena e rollback
    async def start_canary(self, rt: Any, release_id: str, installer: Any) -> dict:
        """Põe a versão em prova num aparelho só. Falhar aqui manda para a quarentena sozinho.

        Colocar em canário é também o caminho de VOLTA da quarentena: quem já falhou não é instalado por engano,
        mas uma pessoa pode mandar tentar de novo de propósito, e essa decisão fica registrada.
        """
        row = self._row(release_id)
        antes = ReleaseChannel(row["channel"])
        if antes is ReleaseChannel.promoted:
            # Senão a prova num segundo aparelho rebaixaria uma versão que já tinha provado — e ela sumiria de
            # `promoted_release`. Querendo provar de novo, a ordem é quarentena primeiro, de propósito.
            raise ReleaseValidationError(
                "Esta versão já foi promovida: instale-a normalmente. Para prová-la outra vez, coloque-a em "
                "quarentena antes — assim a decisão de desfazer a promoção fica explícita.")
        self.repo.set_channel(release_id, ReleaseChannel.canary, canary_instance_id=rt.id,
                              detail=(f"em prova em {rt.id}" if antes is not ReleaseChannel.quarantined else
                                      f"nova prova em {rt.id}, de propósito, depois da quarentena"))
        if antes is ReleaseChannel.quarantined:
            self.bus.emit("log", f"{row['package_name']} {row['version_name']} saiu da quarentena para nova prova em "
                                 f"{rt.id} — decisão explícita de quem opera.", level="warn", instance_id=rt.id)
        return await self.install_on(rt, release_id, installer)

    def promote(self, release_id: str, *, note: str | None = None) -> ReleaseDTO:
        """Promove com base em PROVA REGISTRADA, nunca na lembrança de quem clicou."""
        row = self._row(release_id)
        if ReleaseChannel(row["channel"]) is not ReleaseChannel.canary:
            raise ReleaseValidationError(
                f"Só promove quem está em canário; esta release está em '{row['channel']}'.")
        alvo = row["canary_instance_id"]
        if not alvo:
            raise ReleaseValidationError("Esta release não registrou em qual aparelho o canário rodou.")
        for stage, nome in (("install", "instalar"), ("launch", "abrir")):
            prova = self.repo.last_validation(release_id, alvo, stage)
            if prova is None:
                raise ReleaseValidationError(f"Falta a prova de {nome} em {alvo}; rode o canário antes de promover.")
            if not prova["ok"]:
                raise ReleaseValidationError(
                    f"A última prova de {nome} em {alvo} falhou: {prova['detail'] or 'sem detalhe'}.")
        self.repo.set_channel(release_id, ReleaseChannel.promoted,
                              detail=note or f"provada em {alvo}: instalou e abriu")
        self.bus.emit("log", f"{row['package_name']} {row['version_name']} ({row['version_code']}) promovida — "
                             f"provou em {alvo}.", data={"release_id": release_id})
        return self.repo.release_dto(self._row(release_id))

    def quarantine(self, release_id: str, *, reason: str | None = None) -> ReleaseDTO:
        """Bloqueia a instalação desta versão. Não apaga arquivo nem prova: só impede instalar de novo sem decisão."""
        row = self._row(release_id)
        self.repo.set_channel(release_id, ReleaseChannel.quarantined,
                              detail=reason or "colocada em quarentena por quem opera")
        self.bus.emit("log", f"{row['package_name']} {row['version_name']} ({row['version_code']}) em quarentena: "
                             f"{reason or 'decisão de quem opera'}.", level="warn", data={"release_id": release_id})
        return self.repo.release_dto(self._row(release_id))

    def promoted_release(self, package_name: str) -> ReleaseDTO | None:
        """A versão desejada para o parque: a MAIOR entre as promovidas.

        Promovida quer dizer "provou que abre", não "é a única válida" — por isso duas versões podem estar
        promovidas ao mesmo tempo, e é a maior que serve de alvo. Empate de `version_code` tem desempate estável
        (`releases_of_channel`): o parque converge para esta versão sozinho (ADR-026) e não pode alternar entre duas.
        """
        promovidas = self.repo.releases_of_channel(package_name, ReleaseChannel.promoted)
        return self.repo.release_dto(promovidas[0]) if promovidas else None

    def ensure_builtin_release(self, *, package_name: str, apk_path: Path, app_label: str) -> ImportOutcome | None:
        """Bootstrap do app embutido de QA (`apps[].builtin: true` + `apps[].apk_path`) na subida do backend.

        Item 6.3 (#83) unificou os dois caminhos de instalação: `install_apk` deixou de fazer `adb install` de
        `apps.apk_path` e passou a exigir a release PROMOVIDA do pacote. O QA Messenger era o único app com
        `apk_path` e nunca tinha sido importado como release — o botão "Instalar" nele passou a recusar com
        "nenhuma versão promovida". Este método existe só para fechar essa lacuna na primeira subida.

        Gatilho: NENHUMA release do pacote (nem `validated`, nem `invalid`, nem uma que o dono já quarentenou —
        se já existe alguma, quem decide o resto é gente, não a subida do backend). Idempotente por definição:
        na segunda subida a lista de `list_releases(package_name)` já não está vazia e este método não faz nada,
        mesmo que o arquivo do APK tenha sumido do disco nesse meio-tempo.

        Duas portas que o pipeline normal deixa fechadas de propósito são abertas aqui, e SÓ aqui:
        - assinatura: `_signature_verdict` marca toda release nova como `validated` até aprovação humana
          explícita. A assinatura do app de QA é a de depuração do projeto — nunca vai ganhar essa aprovação, e
          não precisa: é o binário versionado no repositório, com hash conferido a cada instalação.
        - canário: `promote()` exige prova de instalar e abrir NUM APARELHO. Não existe aparelho nenhum de pé
          no instante em que o backend sobe — a prova aqui é o próprio pipeline de importação (hash, assinatura
          consistente, compatibilidade), registrada no motivo da promoção.
        As duas portas valem SÓ para o app chamado com `builtin=True` por este método; um APK que o dono
        importe pela tela de Aplicativos continua exigindo a aprovação de assinatura e o canário de sempre —
        relaxar isso ali seria aceitar em silêncio um APK de origem desconhecida.

        Falha aqui nunca impede a subida: é conveniência de primeira subida (arquivo ausente, SDK sem
        aapt2/apksigner, conjunto inválido), não pré-condição de arranque. Quem chama decide se loga e segue.
        """
        if self.repo.list_releases(package_name):
            return None
        if not apk_path.is_file():
            self.bus.emit("log", f"{app_label}: APK embutido não encontrado em {apk_path} — 'Instalar' vai "
                                 "recusar até alguém importar e promover uma versão dele manualmente.",
                          level="warn")
            return None
        outcome = self.import_file(apk_path, expected_package=package_name, source_type="builtin",
                                   source_reference=f"builtin:{package_name}")
        if not outcome.ok or not outcome.release_id:
            self.bus.emit("log", f"{app_label}: importação do APK embutido falhou: {outcome.reason}", level="warn")
            return outcome
        row = self._row(outcome.release_id)
        if row["status"] != ReleaseState.installable.value:
            self.approve_signature(outcome.release_id,
                                   note="app de QA embutido: assinatura de depuração aprovada automaticamente "
                                        "na subida (builtin=true) — nunca vale para um APK que o dono importe")
        self.repo.set_channel(outcome.release_id, ReleaseChannel.promoted,
                              detail="app de QA embutido: promovido na subida sem canário de aparelho, porque "
                                     "nenhum aparelho existe nesse instante (builtin=true)")
        self.bus.emit("log", f"{app_label}: release embutida importada e promovida na subida "
                             f"({outcome.version_name} {outcome.version_code}).",
                      data={"release_id": outcome.release_id, "package": package_name})
        return outcome

    async def rollback(self, rt: Any, package: str, installer: Any, *, preserve: bool = True,
                       note: str | None = None) -> dict:
        """Volta o aparelho para a versão anterior.

        Dois caminhos, ambos explícitos. Com `preserve`, tenta `install -r -d`, que o Android pode recusar — e a
        recusa vira `DowngradeRefused`, que quem chamou tem de resolver. Sem `preserve`, desinstala antes: funciona
        sempre e **apaga os dados do app, inclusive a sessão**. A API nunca escolhe o segundo caminho sozinha.
        """
        from ..devices.installer import DowngradeRefused
        from ..models import InstalledAppState

        estado = self.repo.app_state(rt.id, package)
        alvo = estado["previous_release_id"] if estado else None
        if not alvo:
            raise ReleaseValidationError(
                f"{rt.id} não tem versão anterior registrada para {package}; não há para onde voltar.")
        if self.repo.release_row(alvo) is None:
            raise ReleaseValidationError("A versão anterior não está mais no catálogo.")
        atual = estado["installed_release_id"]

        if not preserve:
            apagou = ("o aplicativo foi desinstalado e reinstalado para voltar de versão; os dados do app, e com "
                      "eles a sessão, foram apagados")
            self.bus.emit("log", f"{rt.id}: desinstalando {package} antes de voltar — os dados do app serão apagados.",
                          level="warn", instance_id=rt.id)
            # Entre desinstalar e instalar existe uma janela em que o aparelho não tem o app e o banco ainda diz
            # que tem. Marcar a operação pendente é o que faz a reconciliação de partida enxergar essa janela se o
            # backend cair no meio — sem isso, a linha ficaria "pronta" para sempre sobre um aparelho vazio.
            self.repo.upsert_app_state(rt.id, package, pending_op="uninstall", pending_op_at=now_iso(),
                                       state=InstalledAppState.installing.value, last_operation="uninstall",
                                       detail="desinstalando para voltar de versão")
            await installer.uninstall(rt, package)
            # O motivo é dito agora e repetido no fim: entre uma coisa e outra a sessão realmente não existe, e se a
            # reinstalação falhar no meio é essa a explicação que tem de ficar no painel.
            self._app_mudou(rt.id, package, apagou)
            resultado = await self.install_on(rt, alvo, installer, operation="rollback", session_reason=apagou)
            return self._marcar_substituida(resultado, atual, alvo, rt.id, note)
        try:
            resultado = await self.install_on(rt, alvo, installer, allow_downgrade=True, operation="rollback")
            return self._marcar_substituida(resultado, atual, alvo, rt.id, note)
        except DowngradeRefused as exc:
            # Fica nomeado no estado do aparelho para o painel poder oferecer o caminho destrutivo com o aviso
            # certo. Repetir a mesma tentativa não mudaria nada: quem decide é uma pessoa.
            self.repo.upsert_app_state(rt.id, package, drift_kind="downgrade_refused",
                                       detail=f"Voltar preservando os dados foi recusado pelo aparelho: {exc} "
                                              "Reinstalar resolve, mas apaga a sessão.")
            self.bus.emit("log", f"{rt.id}: o Android recusou voltar {package} preservando os dados — {exc}",
                          level="warn", instance_id=rt.id)
            raise

    def _marcar_substituida(self, resultado: dict, atual: str | None, alvo: str, instance_id: str,
                            note: str | None) -> dict:
        """Só depois de a volta acontecer. Marcar antes deixaria a versão anunciada como "substituída" enquanto
        continua instalada — que é exatamente o estado em que o Android recusa o downgrade."""
        if atual and atual != alvo:
            self.repo.set_channel(atual, ReleaseChannel.rolled_back,
                                  detail=note or f"substituída em {instance_id} pela versão anterior")
        return resultado

    def _row(self, release_id: str) -> Any:
        row = self.repo.release_row(release_id)
        if row is None:
            raise ReleaseValidationError("Release não encontrada.")
        return row
