"""28.45: a foto de uma etapa só sai com o sha256 da prévia conferido, e só ao dono.

Prova `simulated`: as partes puras (`sha_da_previa`, `imagem_conferida`) e a recusa do `main`, sem banco, armazém nem
Telegram. Rodar da raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/canais/test_telegram_status.py`.
"""
from __future__ import annotations

import hashlib
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


def test_foto_nao_vai_a_convidado_e_pede_a_previa(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    legenda = tmp_path / "legenda.txt"
    legenda.write_text("Imagem da prévia.", encoding="utf-8")
    for extra in (["--chat", "123"], []):
        monkeypatch.setattr(sys, "argv", ["telegram_status.py", str(legenda), "--foto", ETAPA, *extra])
        assert t.main() == 2
    assert "nada enviado" in capsys.readouterr().out
