"""Storage de evidências: disco ou S3-compatível, com o mesmo contrato (item 5.7, achados #172 e #89).

O defeito que estes testes travam: `evidence.path` era um caminho relativo ao DISCO do processo que executou a
etapa. Numa máquina funciona. Com dois backends no mesmo banco — topologia que a posse de etapa já admite — a
evidência gravada por A respondia 404 em B, e a retenção de B apagava do banco compartilhado as linhas cujos
arquivos ficaram órfãos no disco de A: o arquivo continuava lá e a prova sumia.

O teste de contrato roda nos DOIS back-ends. O de S3 usa um cliente falso em memória, e não um MinIO: a
diferença que importa aqui é a semântica de chave/prefixo, não a rede — e amarrar a suíte a um contêiner
deixaria o teste de fora justamente de quem só tem uma máquina.
"""
from __future__ import annotations

import asyncio
import itertools
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.main import create_app
from app.storage import (DISK, S3, DiskStorage, S3Storage, StorageError, build_storage, normalizar_chave,
                         put_async)
from .conftest import Harness


class FakeS3:
    """O pedaço de API do S3 que o `S3Storage` usa, em memória. Sem rede, sem contêiner, mesma semântica."""

    def __init__(self) -> None:
        self.objetos: dict[str, tuple[bytes, str]] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, ContentType: str) -> dict[str, Any]:  # noqa: N803
        del Bucket
        self.objetos[Key] = (Body, ContentType)
        return {}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        del Bucket
        if Key not in self.objetos:
            raise _NoSuchKey()
        corpo, tipo = self.objetos[Key]
        return {"Body": _Corpo(corpo), "ContentType": tipo}

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:  # noqa: N803
        del Bucket
        if Key not in self.objetos:
            raise _NoSuchKey()
        return {}

    def list_objects_v2(self, *, Bucket: str, Prefix: str, **kw: Any) -> dict[str, Any]:  # noqa: N803
        del Bucket, kw
        return {"Contents": [{"Key": k} for k in self.objetos if k.startswith(Prefix)], "IsTruncated": False}

    def delete_objects(self, *, Bucket: str, Delete: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
        del Bucket
        for o in Delete["Objects"]:
            self.objetos.pop(o["Key"], None)
        return {}

    def generate_presigned_url(self, op: str, *, Params: dict[str, Any], ExpiresIn: int) -> str:  # noqa: N803
        return f"https://bucket.exemplo/{Params['Key']}?op={op}&exp={ExpiresIn}"


class _Corpo:
    def __init__(self, dados: bytes) -> None:
        self._d = dados

    def read(self) -> bytes:
        return self._d


class _NoSuchKey(Exception):
    response = {"Error": {"Code": "NoSuchKey"}}


_CONTADOR = itertools.count()


def _run(s: Any, *, finished: str | None = None) -> str:
    """Uma linha de `runs` e nada mais. Criar a execução pelo serviço dispararia o executor, e o que estes
    testes precisam da execução é só o id e a data de término que a retenção lê."""
    rid = f"r-teste-{next(_CONTADOR)}"
    s.db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at,"
                 " finished_at) VALUES (?,?,?,?,?,?,?,?)",
                 (rid, rid, "teste", "execute", "completed", '["android-01"]', "2000-01-01T00:00:00Z", finished))
    return rid


@pytest.fixture(params=[DISK, S3])
def armazem(request: Any, tmp_path: Path) -> Any:
    if request.param == DISK:
        return DiskStorage(tmp_path / "evid")
    return S3Storage("balde-de-teste", client=FakeS3())


# ====================================================================== contrato, nos dois back-ends


def test_grava_le_e_confere_existencia(armazem: Any) -> None:
    chave = armazem.put("r-1/android-01/tela.jpg", b"\xff\xd8bytes", content_type="image/jpeg")
    assert chave == "r-1/android-01/tela.jpg", "a chave devolvida é a normalizada, e é ela que vai para o banco"
    assert armazem.get(chave) == b"\xff\xd8bytes"
    assert armazem.exists(chave) is True
    assert armazem.get("r-1/android-01/nao-existe.jpg") is None
    assert armazem.exists("r-1/android-01/nao-existe.jpg") is False


def test_apagar_por_prefixo_leva_a_execucao_inteira(armazem: Any) -> None:
    armazem.put("r-2/android-01/a.jpg", b"a")
    armazem.put("r-2/android-02/b.jpg", b"b")
    armazem.put("r-3/android-01/c.jpg", b"c")
    assert armazem.delete_prefix("r-2") == 2
    assert armazem.get("r-2/android-01/a.jpg") is None
    assert armazem.get("r-3/android-01/c.jpg") == b"c", "a retenção de uma execução não encosta na outra"


def test_um_dos_dois_serve_o_arquivo(armazem: Any) -> None:
    """Disco tem caminho local; bucket tem URL. Nenhum dos dois pode ficar sem nenhum dos dois."""
    chave = armazem.put("r-4/android-01/tela.jpg", b"x")
    assert (armazem.local_path(chave) is not None) or (armazem.url(chave) is not None)
    assert b"".join(armazem.stream(chave)) == b"x"


def test_chave_de_travessia_e_recusada(armazem: Any) -> None:
    """`evidence.path` vem do banco — e o banco é COMPARTILHADO entre réplicas. "Veio do banco" nunca foi o
    mesmo que "é seguro abrir": a travessia é recusada na interface, num lugar só."""
    for ruim in ("../../etc/senhas", "r-1/../../fora.jpg", "", "r-1/tela.jpg; rm -rf", "r-1/tela 2.jpg"):
        with pytest.raises(StorageError):
            armazem.put(ruim, b"x")
    # Barra à esquerda não é travessia: some na normalização e a chave continua dentro da raiz.
    assert armazem.put("/absoluto.jpg", b"x") == "absoluto.jpg"


async def test_a_escrita_sai_do_laco_de_eventos(armazem: Any) -> None:
    """`put_async` é o que impede a captura de tela de travar o scheduler quando o destino é a rede."""
    laco = asyncio.get_running_loop()
    lento = []

    original = armazem.put

    def put_marcando(chave: str, dados: bytes, **kw: Any) -> str:
        # Se isto rodasse no laço, `get_running_loop` acharia o laço; numa thread, levanta.
        try:
            asyncio.get_running_loop()
            lento.append("no laço")
        except RuntimeError:
            lento.append("fora do laço")
        return original(chave, dados, **kw)

    armazem.put = put_marcando
    await put_async(armazem, "r-5/android-01/tela.jpg", b"x", content_type="image/jpeg")
    assert lento == ["fora do laço"]
    assert laco.is_running()


# ====================================================================== chave


def test_a_chave_do_windows_continua_valendo() -> None:
    """As 976 linhas já gravadas têm barra invertida no `path`. Elas têm de abrir sem migração de dados."""
    assert normalizar_chave(r"r-1\android-01\tela.jpg") == "r-1/android-01/tela.jpg"
    assert normalizar_chave("r-1/android-01/tela.jpg") == "r-1/android-01/tela.jpg"


def test_disco_le_a_chave_antiga_com_barra_invertida(tmp_path: Path) -> None:
    d = DiskStorage(tmp_path)
    d.put("r-1/android-01/tela.jpg", b"antiga")
    assert d.get(r"r-1\android-01\tela.jpg") == b"antiga"


def test_backend_desconhecido_falha_alto(tmp_path: Path) -> None:
    assert build_storage(DISK, evidence_dir=tmp_path).name == DISK
    with pytest.raises(StorageError, match="S3_BUCKET"):
        build_storage(S3, evidence_dir=tmp_path)
    with pytest.raises(StorageError, match="desconhecido"):
        build_storage("disquete", evidence_dir=tmp_path)


# ====================================================================== integração com o repositório


async def test_evidencia_registra_onde_esta_e_quem_gravou(harness: Harness) -> None:
    s = harness.state
    eid = await s.repo.add_evidence_async(run_id=_run(s), instance_id="android-01", step_id=None,
                                          attempt_id=None, kind="screenshot", note="tela", data=b"\xff\xd8x")
    linha = s.db.one("SELECT * FROM evidence WHERE id=?", (eid,))
    assert linha["storage"] == DISK and linha["stored_by"] == s.cfg.owner_id
    assert "/" in linha["path"] and "\\" not in linha["path"], "a chave é sempre com barra normal"
    assert s.storage.get(linha["path"]) == b"\xff\xd8x"


async def test_evidencia_omitida_nao_ganha_dono_nem_back_end(harness: Harness) -> None:
    """Tela sensível não vira arquivo; sem arquivo, `storage`/`stored_by` não têm o que dizer."""
    s = harness.state
    eid = await s.repo.add_evidence_async(run_id=_run(s), instance_id="android-01", step_id=None,
                                          attempt_id=None, kind="screenshot", note="sensível", redacted=True)
    linha = s.db.one("SELECT * FROM evidence WHERE id=?", (eid,))
    assert linha["path"] is None and linha["storage"] is None and linha["stored_by"] is None


# ====================================================================== retenção entre réplicas


async def test_retencao_nao_apaga_a_linha_da_evidencia_que_esta_no_disco_da_outra_replica(harness: Harness) -> None:
    """O achado #172, ponto a ponto: B apagava do banco a prova de uma execução cujo arquivo estava em A."""
    s = harness.state
    rid = _run(s, finished="2000-01-01T00:00:00Z")
    minha = await s.repo.add_evidence_async(run_id=rid, instance_id="android-01", step_id=None, attempt_id=None,
                                            kind="screenshot", note="minha", data=b"minha")
    alheia = await s.repo.add_evidence_async(run_id=rid, instance_id="android-01", step_id=None, attempt_id=None,
                                             kind="screenshot", note="alheia", data=b"alheia")
    s.db.execute("UPDATE evidence SET stored_by=? WHERE id=?", ("outro-backend", alheia))
    s.db.execute("UPDATE evidence SET ts=? WHERE run_id=?", ("2000-01-01T00:00:00Z", rid))

    limpos = s._apagar_evidencias_vencidas("2020-01-01T00:00:00Z")
    assert limpos == [rid]
    assert s.db.one("SELECT * FROM evidence WHERE id=?", (minha,)) is None, "a minha vence aqui"
    assert s.db.one("SELECT * FROM evidence WHERE id=?", (alheia,)) is not None, (
        "a do disco do outro backend vence LÁ, junto com o arquivo — apagar só a linha é perder a prova")


async def test_retencao_apaga_pelo_storage_e_nao_pela_pasta(tmp_path: Path) -> None:
    """Com `EVIDENCE_STORAGE=s3`, o `shutil.rmtree` antigo não apagava nada e a linha sumia assim mesmo."""
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        s.storage = S3Storage("balde", client=FakeS3())
        s.repo.storage = s.storage
        rid = _run(s, finished="2000-01-01T00:00:00Z")
        eid = await s.repo.add_evidence_async(run_id=rid, instance_id="android-01", step_id=None, attempt_id=None,
                                              kind="screenshot", note="no bucket", data=b"no bucket")
        linha = s.db.one("SELECT * FROM evidence WHERE id=?", (eid,))
        assert linha["storage"] == S3
        assert s.storage.exists(linha["path"]) is True

        s.db.execute("UPDATE evidence SET ts=? WHERE run_id=?", ("2000-01-01T00:00:00Z", rid))
        assert s._apagar_evidencias_vencidas("2020-01-01T00:00:00Z") == [rid]
        assert s.storage.exists(linha["path"]) is False, "o arquivo sai do bucket, não de uma pasta que não existe"
        assert s.db.one("SELECT * FROM evidence WHERE id=?", (eid,)) is None
    finally:
        if h.state is not None:
            await h.state.stop()


# ====================================================================== a rota


async def test_a_rota_serve_pela_interface_e_diz_quando_o_arquivo_esta_em_outro_servidor(harness: Harness) -> None:
    s = harness.state
    eid = await s.repo.add_evidence_async(run_id=_run(s), instance_id="android-01", step_id=None,
                                          attempt_id=None, kind="screenshot", note="tela", data=b"\xff\xd8x")
    app = create_app(s.cfg, state=s)
    app.state.poc = s
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/evidence/{eid}")
        assert r.status_code == 200 and r.content == b"\xff\xd8x"

        s.db.execute("UPDATE evidence SET stored_by=? WHERE id=?", ("outro-backend", eid))
        r = await c.get(f"/api/evidence/{eid}")
        assert r.status_code == 404
        # O 404 mudo mandava o operador procurar defeito na retenção. Agora ele diz onde o arquivo está.
        assert r.json()["detail"]["code"] == "em_outro_servidor"
        assert "outro-backend" in r.json()["detail"]["message"]


# ====================================================================== catálogo de APK (#89)


def _release_no_catalogo(s: Any, tmp_path: Path) -> tuple[str, Path, str]:
    """Uma release catalogada, com o arquivo no disco — o estado normal de quem importou aqui."""
    import hashlib

    from app.models import ReleaseState

    pasta = tmp_path / "apks" / "com.exemplo" / "1-abcd"
    pasta.mkdir(parents=True, exist_ok=True)
    conteudo = b"PK\x03\x04apk-de-teste"
    (pasta / "base.apk").write_bytes(conteudo)
    sha = hashlib.sha256(conteudo).hexdigest()
    rel = "apks/com.exemplo/1-abcd"
    s.release_repo.save_release(
        release_id="rel-teste", package_name="com.exemplo", version_name="1.0", version_code=1,
        artifact_type="single", signature_sha256="a" * 64, min_sdk=24, target_sdk=34, abis=[],
        catalog_dir=rel, source_type="inbox", source_reference=None,
        status=ReleaseState.installable, detail=None,
        files=[{"role": "base", "split": None, "name": "base.apk", "sha256": sha, "sizeBytes": len(conteudo)}],
        requires_gms=False)
    return rel, pasta, sha


async def test_arquivo_ausente_diz_outro_servidor_e_nao_chama_de_adulterado(tmp_path: Path) -> None:
    """O achado #89: "arquivo ausente" e "artefato adulterado" são coisas diferentes, e só uma delas invalida
    a release. A mensagem antiga mandava o operador procurar sabotagem onde havia só outra máquina."""
    from app.models import ReleaseState
    from app.releases.catalog import ReleaseValidationError

    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        rel, pasta, _ = _release_no_catalogo(s, s.cfg.root)
        (pasta / "base.apk").unlink()                     # o arquivo está na OUTRA máquina
        with pytest.raises(ReleaseValidationError) as erro:
            s.releases.files_for_install("rel-teste")
        assert "NESTE servidor" in str(erro.value)
        assert "NÃO está adulterado" in str(erro.value)
        assert s.release_repo.release_row("rel-teste")["status"] == ReleaseState.installable.value, (
            "ausente não invalida a release: ela continua íntegra onde está")
        del rel
    finally:
        if h.state is not None:
            await h.state.stop()


async def test_catalogo_compartilhado_baixa_o_apk_e_confere_o_hash(tmp_path: Path) -> None:
    """Com storage compartilhado, o segundo backend BAIXA o arquivo em vez de falhar — e confere o sha256."""
    from app.releases.catalog import ReleaseValidationError

    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state
        rel, pasta, _ = _release_no_catalogo(s, s.cfg.root)
        compartilhado = S3Storage("balde", client=FakeS3())
        s.releases.catalog_storage = compartilhado
        compartilhado.put(f"{rel}/base.apk", (pasta / "base.apk").read_bytes())
        (pasta / "base.apk").unlink()                     # como se este backend nunca tivesse importado

        _, caminhos = s.releases.files_for_install("rel-teste")
        assert caminhos[0].is_file() and caminhos[0].read_bytes() == b"PK\x03\x04apk-de-teste"

        # E o que vem do storage NÃO ganha confiança extra: hash diferente continua sendo adulteração.
        caminhos[0].unlink()
        compartilhado.put(f"{rel}/base.apk", b"outro-binario")
        with pytest.raises(ReleaseValidationError, match="adulterado"):
            s.releases.files_for_install("rel-teste")
    finally:
        if h.state is not None:
            await h.state.stop()


# ====================================================================== a linha manda, não a configuração


async def test_linha_antiga_em_disco_continua_sendo_servida_do_disco_depois_de_migrar_para_s3(harness: Harness) -> None:
    """Num parque que ligou o S3 no meio do caminho, as linhas antigas continuam em disco. Servi-las pelo
    bucket devolveria uma URL pré-assinada de um objeto que não existe: um 307 para um 404."""
    s = harness.state
    eid = await s.repo.add_evidence_async(run_id=_run(s), instance_id="android-01", step_id=None,
                                          attempt_id=None, kind="screenshot", note="antiga", data=b"\xff\xd8antiga")
    assert s.db.one("SELECT storage FROM evidence WHERE id=?", (eid,))["storage"] == DISK

    s.storage = S3Storage("balde", client=FakeS3())          # o backend migrou; a linha antiga não
    app = create_app(s.cfg, state=s)
    app.state.poc = s
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/evidence/{eid}", follow_redirects=False)
    assert r.status_code == 200 and r.content == b"\xff\xd8antiga"


async def test_evidencia_de_back_end_nao_configurado_nao_e_servida_nem_apagada(harness: Harness) -> None:
    """A regra é a mesma da retenção entre donos: só apaga a linha quem consegue apagar o arquivo. Sem isto, uma
    réplica ainda em disco apagava do banco as linhas do bucket e deixava os objetos órfãos lá."""
    s = harness.state
    rid = _run(s, finished="2000-01-01T00:00:00Z")
    eid = await s.repo.add_evidence_async(run_id=rid, instance_id="android-01", step_id=None, attempt_id=None,
                                          kind="screenshot", note="no bucket", data=b"x")
    s.db.execute("UPDATE evidence SET storage=?, ts=? WHERE id=?", (S3, "2000-01-01T00:00:00Z", eid))

    app = create_app(s.cfg, state=s)
    app.state.poc = s
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        r = await c.get(f"/api/evidence/{eid}", follow_redirects=False)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "storage_nao_configurado"

    assert s._apagar_evidencias_vencidas("2020-01-01T00:00:00Z") == []
    assert s.db.one("SELECT * FROM evidence WHERE id=?", (eid,)) is not None, (
        "apagar a linha sem poder apagar o objeto deixaria o bucket órfão e sumiria com a prova")
