"""28.45: a foto de uma etapa só sai com o sha256 da prévia conferido, e só ao dono.

Prova `simulated`: as partes puras (`sha_da_previa`, `imagem_conferida`) e a recusa do `main`, sem banco, armazém nem
Telegram. Rodar da raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/canais/test_telegram_status.py`.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import telegram_status as t  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
ETAPA = "r-20261005074912-000000:android-13:v1:create_post_1"


def _previa(sha: object) -> dict[str, object]:
    return {"itens": [{"step_id": "r-20261005074912-000000:android-13:v1:read_count_1"},
                      {"step_id": ETAPA, "imagem_sha256": sha}]}


def test_o_sha_vem_da_etapa_na_previa() -> None:
    sha = hashlib.sha256(PNG).hexdigest()
    assert t.sha_da_previa(_previa(sha), ETAPA) == sha
    with pytest.raises(t.FotoRecusada, match="não está na prévia"):
        t.sha_da_previa(_previa(sha), "outra")
    with pytest.raises(t.FotoRecusada, match="sem imagem_sha256"):
        t.sha_da_previa(_previa("5fbf3507"), ETAPA)             # só o começo não serve: o sha inteiro


def test_a_imagem_so_sai_com_o_sha_que_bate() -> None:
    sha = hashlib.sha256(PNG).hexdigest()
    lidas: list[tuple[str, str]] = []

    def ler(run_id: str, step_id: str) -> tuple[bytes, str]:
        lidas.append((run_id, step_id))
        return PNG, "application/octet-stream"

    assert t.imagem_conferida(ETAPA, sha, ler) == (PNG, "image/png")
    assert lidas == [("r-20261005074912-000000", ETAPA)]                  # a execução é o prefixo da etapa
    with pytest.raises(t.FotoRecusada, match="não bate"):
        t.imagem_conferida(ETAPA, "0" * 64, ler)


def test_sem_imagem_ou_com_bytes_que_nao_sao_imagem_nada_sai() -> None:
    with pytest.raises(t.FotoRecusada, match="sem imagem|não tem imagem"):
        t.imagem_conferida(ETAPA, "0" * 64, lambda _r, _s: None)
    texto = b"isto nao e imagem"
    with pytest.raises(t.FotoRecusada, match="não é uma imagem"):
        t.imagem_conferida(ETAPA, hashlib.sha256(texto).hexdigest(), lambda _r, _s: (texto, ""))


def test_foto_nao_vai_a_convidado(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                  capsys: pytest.CaptureFixture[str]) -> None:
    legenda = tmp_path / "legenda.txt"
    legenda.write_text("Imagem da prévia.", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["telegram_status.py", str(legenda), "--foto", ETAPA, "--chat", "123"])
    assert t.main() == 2
    assert "nada enviado" in capsys.readouterr().out


# ------------------------------------------------------------------ 28.44: --escolha e --substitui
def test_opcoes_da_escolha_viram_o_detalhe_do_fato() -> None:
    assert t.opcoes_da_escolha("1,2,3") == "1-2-3"
    assert t.opcoes_da_escolha(" A , B ,C,D ") == "A-B-C-D"
    assert t.opcoes_da_escolha("10,20") == "10-20"
    # Leitura do #412: opção é número ou UMA letra; palavra de aval e as letras S e N não servem.
    for ruim in ("1", "1,1", "1,a-b", "1,x:y", "1,,", "1,opcao-muito-longa-demais", "sim,nao", "ok,pode",
                 "publica,1", "aprovar,vetar", "S,N", "A,s", "1,2,1000"):
        with pytest.raises(ValueError):
            t.opcoes_da_escolha(ruim)


class _RepoFalso:
    def __init__(self) -> None:
        self.feitas: list[tuple[object, ...]] = []

    def registrar_enviada(self, ref: str, origem: str, *, fato: str | None = None) -> None:
        self.feitas.append(("enviada", ref, origem, fato))

    def registrar_substituta(self, nova: str, antiga: str) -> None:
        self.feitas.append(("substituta", nova, antiga))


def test_gravar_enviada_marca_a_escolha_e_a_substituta() -> None:
    repo = _RepoFalso()
    t._gravar_enviada(297, lambda: repo, escolha="1-2-3", substitui=294)
    assert repo.feitas == [("enviada", "297", "ana", "escolha:297:1-2-3"), ("substituta", "297", "294")]
    repo = _RepoFalso()
    t._gravar_enviada(298, lambda: repo)
    assert repo.feitas == [("enviada", "298", "ana", None)]                  # sem a flag, nada muda


def test_escolha_ruim_ou_a_convidado_nada_envia(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    texto = tmp_path / "pergunta.txt"
    texto.write_text("Responda 1, 2 ou 3.", encoding="utf-8")

    async def nao_envia(*_a: object, **_k: object) -> int:
        raise AssertionError("não devia enviar")

    monkeypatch.setattr(t, "_enviar", nao_envia)
    for extra in (["--escolha", "1"], ["--escolha", "1,2", "--chat", "123"], ["--substitui", "294", "--foto", ETAPA]):
        monkeypatch.setattr(sys, "argv", ["telegram_status.py", str(texto), *extra])
        assert t.main() == 2
    assert capsys.readouterr().out.count("nada enviado") == 3


# ------------------------------------------------------------------ 28.46: o envio montado, com a Central falsa
class _CanalFalso:
    def __init__(self, falha: Exception | None = None) -> None:
        self.enviadas: list[tuple[bytes, str, str, int | None]] = []
        self.falha = falha

    async def enviar_anexo(self, conteudo: bytes, mime: str, legenda: str, *, responde_a: int | None = None) -> int:
        if self.falha is not None:
            raise self.falha
        self.enviadas.append((conteudo, mime, legenda, responde_a))
        return 999


def _central(sha: str | None, *, ler=None, quebra: Exception | None = None):  # noqa: ANN001, ANN202
    def montar():  # noqa: ANN202
        if quebra is not None:
            raise quebra
        return (ler or (lambda _r, _s: (PNG, ""))), (lambda _r, _s: sha)
    return montar


def _foto(central, canal: _CanalFalso, previa: Path | None = None) -> int:  # noqa: ANN001
    return asyncio.run(t._enviar_foto("Imagem.", 302, ETAPA, previa, central=central, canal=canal))


@pytest.fixture
def gravadas(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    feitas: list[object] = []
    monkeypatch.setattr(t, "_gravar_enviada", lambda mid, *a, **k: feitas.append(mid))
    return feitas


def test_a_foto_sai_com_o_sha_da_central(gravadas: list[object], tmp_path: Path) -> None:
    sha = hashlib.sha256(PNG).hexdigest()
    canal = _CanalFalso()
    assert _foto(_central(sha), canal) == 0
    assert canal.enviadas == [(PNG, "image/png", "Imagem.", 302)] and gravadas == [999]
    # com o arquivo da prévia que bate com a Central, também sai
    arquivo = tmp_path / "porta.json"
    arquivo.write_text(json.dumps(_previa(sha)), encoding="utf-8")
    assert _foto(_central(sha), _CanalFalso(), arquivo) == 0


def test_sha_errado_nao_chama_o_envio(gravadas: list[object], tmp_path: Path,
                                      capsys: pytest.CaptureFixture[str]) -> None:
    canal = _CanalFalso()
    assert _foto(_central("0" * 64), canal) == 2                           # a Central diz outro sha que o dos bytes
    assert _foto(_central(None), canal) == 2                               # a prévia da Central não tem imagem
    arquivo = tmp_path / "porta.json"                                       # o arquivo diz outro sha que a Central
    arquivo.write_text(json.dumps(_previa("1" * 64)), encoding="utf-8")
    assert _foto(_central(hashlib.sha256(PNG).hexdigest()), canal, arquivo) == 2
    assert canal.enviadas == [] and gravadas == []
    saida = capsys.readouterr().out
    assert "não bate" in saida and "não tem imagem_sha256" in saida and "não é o que a Central mostra" in saida


def test_cada_falha_de_leitura_tem_o_seu_motivo(gravadas: list[object], capsys: pytest.CaptureFixture[str]) -> None:
    sha = hashlib.sha256(PNG).hexdigest()

    def sumiu(_r: str, _s: str) -> tuple[bytes, str]:
        raise FileNotFoundError("x")

    def quebrou(_r: str, _s: str) -> tuple[bytes, str]:
        raise RuntimeError("x")

    canal = _CanalFalso()
    assert _foto(_central(sha, ler=sumiu), canal) == 2
    assert _foto(_central(sha, ler=quebrou), canal) == 2
    assert _foto(_central(sha, quebra=RuntimeError("banco")), canal) == 2
    assert canal.enviadas == [] and gravadas == []
    saida = capsys.readouterr().out
    assert "não foi lida do armazém (FileNotFoundError)" in saida
    assert "não foi conferida (RuntimeError)" in saida and "a Central não foi lida (RuntimeError)" in saida
    assert "Traceback" not in saida


def test_sem_resposta_do_telegram_manda_conferir_o_chat(gravadas: list[object],
                                                        capsys: pytest.CaptureFixture[str]) -> None:
    sha = hashlib.sha256(PNG).hexdigest()
    assert _foto(_central(sha), _CanalFalso(t.FalhaDeEnvio("tempo esgotado"))) == 1
    assert "confira o chat antes de repetir" in capsys.readouterr().out
    assert _foto(_central(sha), _CanalFalso(t.FalhaDeEnvio("chat não encontrado", definitiva=True, status=400))) == 1
    assert "confira o chat" not in capsys.readouterr().out                 # recusa clara: não saiu
    assert gravadas == []


def test_chat_vazio_nada_envia(monkeypatch: pytest.MonkeyPatch, gravadas: list[object],
                               capsys: pytest.CaptureFixture[str]) -> None:
    from types import SimpleNamespace

    from pydantic import SecretStr
    monkeypatch.setattr(t, "EnvSettings", lambda: SimpleNamespace(telegram_chat_id=SecretStr(""),
                                                                   telegram_bot_token=SecretStr("x")))
    sha = hashlib.sha256(PNG).hexdigest()
    assert asyncio.run(t._enviar_foto("Imagem.", None, ETAPA, None, central=_central(sha))) == 2
    assert "TELEGRAM_CHAT_ID vazio" in capsys.readouterr().out and gravadas == []

def test_marca_da_escolha_que_nao_grava_sai_com_erro(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                     capsys: pytest.CaptureFixture[str]) -> None:
    """Leitura do #412: se a marca da pergunta nova não grava, a resposta solta não casa e a antiga segue aberta. O
    script sai com erro visível (3), e não com o 0 de "enviado"."""
    class _Resposta:
        status_code = 200

    class _CanalOk:
        def __init__(self, *_a: object) -> None: ...

        async def _chamar(self, *_a: object, **_k: object) -> _Resposta:
            return _Resposta()

    from types import SimpleNamespace

    from pydantic import SecretStr
    monkeypatch.setattr(t, "EnvSettings", lambda: SimpleNamespace(telegram_chat_id=SecretStr("1"),
                                                                   telegram_bot_token=SecretStr("x")))
    monkeypatch.setattr(t, "CanalTelegram", _CanalOk)
    monkeypatch.setattr(t, "_json", lambda _r: {"ok": True, "result": {"message_id": 297}})
    monkeypatch.setattr(t, "_gravar_enviada", lambda *_a, **_k: False)
    assert asyncio.run(t._enviar("Responda 1 ou 2.", None, escolha="1-2")) == 3
    assert "ERRO" in capsys.readouterr().out
    assert asyncio.run(t._enviar("Sem escolha.", None)) == 0              # sem a marca, a falha do registro só avisa
