"""RA-24: a versão do formato de `telas.yaml` e `sessao.yaml` é conferida na carga (como a do catálogo), e
`GET /api/apps/{pacote}/conhecimento` prova o conhecimento no ar com hashes iguais aos do Git."""
from __future__ import annotations

import copy
import hashlib
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.automation import conhecimento_de_telas as telas
from app.integrations.app_declarado import conhecimento
from app.integrations.app_declarado.conhecimento import SessaoInvalida, do_app
from app.integrations.app_declarado.prova import git_blob, prova_do_pacote
from app.planning.capabilities import CONHECIMENTO_DE_APPS

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente

PACOTES = sorted(p.name for p in CONHECIMENTO_DE_APPS.iterdir() if (p / "app.yaml").is_file())
IG = "com.instagram.android"


def _yaml(pacote: str, nome: str) -> Any:
    return yaml.safe_load((CONHECIMENTO_DE_APPS / pacote / nome).read_text(encoding="utf-8"))


# ------------------------------------------------------------------------------------------------- versão do formato
@pytest.mark.parametrize("pacote", PACOTES)
def test_os_telas_yaml_do_repositorio_declaram_uma_versao_entendida(pacote: str) -> None:
    assert telas.de_dados(_yaml(pacote, "telas.yaml")).versao in telas.VERSOES_DE_TELAS


@pytest.mark.parametrize("versao", [2, 0, 1.0, True, "1"])
def test_telas_yaml_de_versao_nao_entendida_e_recusado_na_carga(versao: object) -> None:
    dados = copy.deepcopy(_yaml(IG, "telas.yaml"))
    dados["versao"] = versao
    with pytest.raises(telas.ConhecimentoInvalido, match="`versao`"):
        telas.de_dados(dados)


def test_telas_yaml_sem_versao_continua_sendo_a_1() -> None:
    dados = copy.deepcopy(_yaml(IG, "telas.yaml"))
    dados.pop("versao", None)
    assert telas.de_dados(dados).versao == 1


def test_sessao_yaml_de_versao_nao_entendida_e_recusado_na_carga() -> None:
    base = do_app(IG).telas
    dados = copy.deepcopy(_yaml(IG, "sessao.yaml"))
    assert conhecimento.de_dados(dados, base).versao == 1  # controle: sem a mexida, passa
    dados["versao"] = 2
    with pytest.raises(SessaoInvalida, match="`versao` 2 não é entendida"):
        conhecimento.de_dados(dados, base)


# ------------------------------------------------------------------------------------------------------- os hashes
def test_o_hash_e_o_do_texto_lido_e_nao_o_dos_bytes_do_disco(tmp_path: Path) -> None:
    """CRLF (checkout com autocrlf) e LF dão o MESMO hash: é o texto que o carregador lê e o que o Git guarda."""
    for nome, cru in (("com.ex.crlf", b"app: com.ex.crlf\r\nversao: 1\r\n"), ("com.ex.lf", b"app: com.ex.crlf\nversao: 1\n")):
        (tmp_path / nome).mkdir()
        (tmp_path / nome / "telas.yaml").write_bytes(cru)
    crlf = prova_do_pacote("com.ex.crlf", raiz=tmp_path, inicio=time.time() + 60)
    lf = prova_do_pacote("com.ex.lf", raiz=tmp_path, inicio=time.time() + 60)
    assert crlf is not None and lf is not None
    a, b = crlf.arquivos[0], lf.arquivos[0]
    assert (a.fim_de_linha, b.fim_de_linha) == ("crlf", "lf") and a.bytes == b.bytes + 2
    texto = b"app: com.ex.crlf\nversao: 1\n"
    assert a.sha256 == b.sha256 == hashlib.sha256(texto).hexdigest()
    assert a.git_blob == b.git_blob == git_blob(texto)
    # a fórmula de `git hash-object` para um conteúdo conhecido: o blob vazio (`printf '' | git hash-object --stdin`)
    assert git_blob(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert not a.mudou_depois_do_inicio and a.caminho == "telas.yaml"


def test_arquivo_gravado_depois_do_inicio_do_processo_e_apontado(tmp_path: Path) -> None:
    (tmp_path / "com.ex.app").mkdir()
    (tmp_path / "com.ex.app" / "app.yaml").write_text("app: com.ex.app\n", encoding="utf-8")
    prova = prova_do_pacote("com.ex.app", raiz=tmp_path, inicio=time.time() - 3600)
    assert prova is not None and prova.arquivos[0].mudou_depois_do_inicio


@pytest.mark.parametrize("pacote", ["..", "com..x", "com/x", "semponto", "", "com.ex.nao_existe"])
def test_pacote_sem_forma_ou_sem_pasta_nao_tem_prova(tmp_path: Path, pacote: str) -> None:
    (tmp_path / "semponto").mkdir()
    (tmp_path / "semponto" / "app.yaml").write_text("x: 1\n", encoding="utf-8")
    assert prova_do_pacote(pacote, raiz=tmp_path, inicio=0.0) is None


@pytest.mark.skipif(shutil.which("git") is None, reason="sem git nesta máquina")
@pytest.mark.parametrize("pacote", PACOTES)
def test_git_blob_bate_com_git_hash_object_no_checkout(pacote: str) -> None:
    """O aceite do RA-24: o hash da rota é o de `git hash-object <arquivo>` no checkout (que aplica o mesmo
    `autocrlf` do commit). Num arquivo sem mudança local, é também o de `git rev-parse HEAD:<caminho>`."""
    prova = prova_do_pacote(pacote)
    assert prova is not None and {a.nome for a in prova.arquivos} >= {"app.yaml", "telas.yaml"}
    raiz = CONHECIMENTO_DE_APPS.parents[3]
    for a in prova.arquivos:
        assert a.caminho == f"backend/app/conhecimento/apps/{pacote}/{a.nome}"
        esperado = subprocess.run(["git", "hash-object", a.caminho], cwd=raiz, capture_output=True, text=True,
                                  check=True).stdout.strip()
        assert a.git_blob == esperado, a.caminho


@pytest.mark.skipif(shutil.which("git") is None, reason="sem git nesta máquina")
def test_os_yaml_do_conhecimento_estao_em_lf_no_indice_do_git() -> None:
    """A borda do hash (revisão da Android): `git_blob` normaliza CRLF→LF, então um YAML que entrasse no índice COM CRLF
    teria o blob do commit diferente do da rota. Hoje todos estão `i/lf` (`git ls-files --eol`); este teste segura."""
    raiz = CONHECIMENTO_DE_APPS.parents[3]
    saida = subprocess.run(["git", "ls-files", "--eol", "backend/app/conhecimento/apps"], cwd=raiz, capture_output=True,
                           text=True, check=True).stdout.splitlines()
    yamls = [linha for linha in saida if linha.rstrip().endswith(".yaml")]
    assert yamls and all(linha.split()[0] in ("i/lf", "i/none") for linha in yamls), "\n".join(yamls)


# -------------------------------------------------------------------------------------------------------- a rota
@pytest.mark.asyncio
async def test_rota_do_conhecimento_traz_os_yaml_com_os_hashes(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        async with _cliente(h) as c:
            r = await c.get(f"/api/apps/{IG}/conhecimento")
            assert r.status_code == 200, r.text
            corpo = r.json()
            assert corpo["app"] == IG and corpo["processo_iniciado_em"].endswith("Z")
            nomes = [a["nome"] for a in corpo["arquivos"]]
            assert nomes == sorted(nomes) and {"app.yaml", "catalogo.yaml", "sessao.yaml", "telas.yaml"} <= set(nomes)
            for a in corpo["arquivos"]:
                texto = (CONHECIMENTO_DE_APPS / IG / a["nome"]).read_text(encoding="utf-8").encode("utf-8")
                assert a["sha256"] == hashlib.sha256(texto).hexdigest() and a["git_blob"] == git_blob(texto)
            assert (await c.get("/api/apps/com.ex.nao_existe/conhecimento")).status_code == 404
            assert (await c.get("/api/apps/..%2Fsegredo/conhecimento")).status_code == 404
    finally:
        assert h.state is not None
        await h.state.stop()
