"""31.334 (L1): a conta PLANEJADA ganha a caixa do parque sem o igfarm, e o cadastro guiado a usa.

O e-mail do parque é catch-all: o endereço é só o destinatário que o leitor IMAP filtra, não há caixa a provisionar. A linha de
`caixas_email` só nascia no registro do igfarm; no cadastro feito no app, `CadastroGuiado.iniciar` a cria antes de tocar no aparelho.
Prova `simulated`: banco do harness, nenhum IMAP, nenhum provedor. Sem migração; nenhum segredo (marcador no `secret_ref`).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.integrations.app_declarado.cadastro import Desfecho
from app.modules.email_do_parque.application.servico import ConfigEmail, EmailDoParque
from app.modules.identity.infrastructure import cadastro_guiado
from app.modules.identity.infrastructure.caixa_planejada import (MARCADOR_DA_CAIXA_COMPARTILHADA,
                                                                 CaixaDaContaPlanejada)
from app.social.erros import SocialError

from .conftest import Harness
from .test_cadastro_guiado import Cenario, _cenario, _iniciar

DOMINIO = "parque.exemplo.test"


def _parque(h: Harness, *dominios: str, padrao: str = DOMINIO) -> None:
    assert h.state is not None
    h.state.email_parque = EmailDoParque(ConfigEmail(dominio_padrao=padrao, allowlist=tuple(dominios), imap_configurado=False), None)


def _linha(h: Harness, aid: str) -> dict[str, Any] | None:
    return h.state.db.one("SELECT * FROM caixas_email WHERE account_id=?", (aid,))


async def test_a_conta_planejada_ganha_a_caixa_no_dominio_do_parque_e_e_idempotente(harness: Harness, tmp_path: Path,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    _parque(harness)
    cen = await _cenario(harness, tmp_path, monkeypatch, com_caixa=False)
    assert _linha(harness, cen.aid) is None
    servico = CaixaDaContaPlanejada(harness.state)
    endereco = servico.garantir(cen.pid, cen.aid)
    assert endereco.endswith("@" + DOMINIO) and endereco == endereco.lower()
    assert endereco.startswith("maria.alvaressouza")                          # nome e sobrenome da persona do harness, sem acento
    linha = _linha(harness, cen.aid)
    assert linha is not None and linha["endereco"] == endereco and linha["dominio"] == DOMINIO
    assert linha["profile_id"] == cen.pid
    assert linha["secret_ref"] == MARCADOR_DA_CAIXA_COMPARTILHADA              # a senha é a da caixa compartilhada: não há segredo aqui
    assert servico.garantir(cen.pid, cen.aid) == endereco                      # repetir devolve a mesma, sem linha nova
    assert harness.state.db.scalar("SELECT COUNT(*) FROM caixas_email WHERE account_id=?", (cen.aid,)) == 1


async def test_o_cadastro_guiado_cria_a_caixa_antes_de_despachar_e_entrega_o_endereco_ao_motor(
        harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _parque(harness)
    cen = await _cenario(harness, tmp_path, monkeypatch, com_caixa=False)
    vistos: list[Any] = []

    class MotorQueSoGuarda:
        def __init__(self, k: object, mesa: object, ciclo: object, dados: Any) -> None:
            vistos.append(dados)

        async def executar(self) -> Desfecho:
            return Desfecho(True)

    monkeypatch.setattr(cadastro_guiado, "MotorDeCadastro", MotorQueSoGuarda)
    assert _iniciar(cen)["accepted"]
    linha = _linha(harness, cen.aid)
    assert linha is not None
    await cen.despachos[-1]["factory"]()
    assert vistos and vistos[0].email == linha["endereco"]
    # o leitor do código filtra por este mesmo endereço
    from app.modules.identity.infrastructure.ponte_igfarm import ArmazemSql
    assert ArmazemSql(harness.state.db).endereco_da_conta(cen.aid) == linha["endereco"]


async def test_o_e_mail_do_perfil_no_dominio_do_parque_e_aproveitado(harness: Harness, tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    _parque(harness)
    cen = await _cenario(harness, tmp_path, monkeypatch, com_caixa=False)
    harness.state.db.execute("UPDATE instagram_profiles SET email=? WHERE id=?", (f"Maria.Sou@{DOMINIO}", cen.pid))
    assert CaixaDaContaPlanejada(harness.state).garantir(cen.pid, cen.aid) == f"maria.sou@{DOMINIO}"


async def test_o_e_mail_do_perfil_fora_do_dominio_do_parque_e_ignorado(harness: Harness, tmp_path: Path,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    _parque(harness)
    cen = await _cenario(harness, tmp_path, monkeypatch, com_caixa=False)
    harness.state.db.execute("UPDATE instagram_profiles SET email=? WHERE id=?", ("maria@gmail.com", cen.pid))
    endereco = CaixaDaContaPlanejada(harness.state).garantir(cen.pid, cen.aid)
    assert endereco.endswith("@" + DOMINIO) and "gmail" not in endereco


async def test_duas_personas_nunca_dividem_o_endereco(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _parque(harness)
    a = await _cenario(harness, tmp_path / "a", monkeypatch, com_caixa=False)
    b = await _cenario(harness, tmp_path / "b", monkeypatch, com_caixa=False)
    # a primeira fica com o e-mail do perfil; a segunda pede o MESMO e a caixa da primeira (índice global) o tira dela
    harness.state.db.execute("UPDATE instagram_profiles SET email=? WHERE id=?", (f"maria.repetida@{DOMINIO}", a.pid))
    ea = CaixaDaContaPlanejada(harness.state).garantir(a.pid, a.aid)
    harness.state.db.execute("UPDATE instagram_profiles SET email=? WHERE id=?", (f"maria.repetida@{DOMINIO}", b.pid))
    eb = CaixaDaContaPlanejada(harness.state).garantir(b.pid, b.aid)
    assert ea == f"maria.repetida@{DOMINIO}" and eb != ea and eb.endswith("@" + DOMINIO)


async def test_sem_dominio_permitido_o_cadastro_recusa_antes_de_tocar_no_aparelho(harness: Harness, tmp_path: Path,
                                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    _parque(harness, padrao="")                                                  # o ambiente sem `EMAIL_DOMINIO` (o padrão do harness)
    cen: Cenario = await _cenario(harness, tmp_path, monkeypatch, com_caixa=False)
    with pytest.raises(SocialError) as erro:
        _iniciar(cen)
    assert erro.value.code == "sem_caixa_de_email" and erro.value.status == 409
    assert cen.despachos == [] and _linha(harness, cen.aid) is None


async def test_a_caixa_do_igfarm_que_ja_existe_nao_e_tocada(harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _parque(harness)
    cen = await _cenario(harness, tmp_path, monkeypatch, com_caixa=True)         # o `_cenario` grava uma caixa como a do igfarm
    antes = _linha(harness, cen.aid)
    assert CaixaDaContaPlanejada(harness.state).garantir(cen.pid, cen.aid) == cen.endereco
    assert _linha(harness, cen.aid) == antes
