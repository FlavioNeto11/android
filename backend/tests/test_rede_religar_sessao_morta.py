"""Start pela interface (PR #17) × hierarquia que recria a sessão UiAutomator2 morta (PR #19).

O caminho do Start lê a árvore por `AparelhoPeloAdb.arvore` → `DeviceManager.hierarchy` (a que o PR #19 mudou) e toca por
`rt.adb.tap` (adb `input tap`, FORA da sessão UiAutomator2). Simulado (aparelho e driver falsos): prova que a leitura recupera
a sessão morta e que o toque não passa pela sessão; não prova o Appium real (o smoke real do PR #19 e o do §20.4 provam cada um o seu)."""
from __future__ import annotations

import pytest

from app.devices.rede_aplicacao import AparelhoPeloAdb

from .conftest import Harness


async def test_a_arvore_do_start_recupera_a_sessao_morta_e_o_toque_vai_pelo_adb(harness: Harness,
                                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    d = harness.state.devices
    rt, fake = d.get("android-01"), harness.fakes["android-01"]
    recriacoes: list[str] = []
    original = d.invalidate_automation
    monkeypatch.setattr(d, "invalidate_automation", lambda r, why: (recriacoes.append(why), original(r, why))[1])
    toques: list[tuple[int, int]] = []
    monkeypatch.setattr(rt.adb, "tap", lambda x, y: toques.append((x, y)))
    ap = AparelhoPeloAdb(harness.state, rt)

    fake.session_lost_reads = 1                                     # a sessão "pronta" morreu por baixo
    nos = await ap.arvore()
    assert nos and fake.session_lost_reads == 0                     # UMA leitura devolveu a árvore (a releitura é interna)
    assert len(recriacoes) == 1

    alvo = next(n for n in nos if n.clicavel)
    x1, y1, x2, y2 = alvo.limites
    await ap.tocar((x1 + x2) // 2, (y1 + y2) // 2)
    assert toques == [((x1 + x2) // 2, (y1 + y2) // 2)]             # o toque é do adb, com a coordenada da leitura que valeu
    assert len(recriacoes) == 1                                     # tocar não abre nem fecha sessão

    nos2 = await ap.arvore()                                        # a leitura seguinte reaproveita a sessão recriada
    assert nos2 and len(recriacoes) == 1
