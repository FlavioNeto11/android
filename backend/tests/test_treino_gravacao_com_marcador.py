"""31.118: a gravação SALVA guarda o marcador da persona, não o valor que a pessoa digitou.

A prova real do 31.87 (06/10, sessão de ensino no android-04) mostrou que `training_inputs.text` mantinha o dado da
persona em claro depois de a habilidade ser salva, e que `GET /api/training/{id}` o devolvia. Agora, ao salvar, a
entrada de texto cujo campo INTEIRO é um dado da persona que a habilidade usa (o marcador está no plano) passa a guardar
o marcador. A gravação aberta segue em claro, porque a proposta precisa do valor; quem precisa dele depois (refazer as
receitas, mascarar as perguntas) o lê da persona, em memória (`dado_da_persona.com_valores`). Sem migração: a coluna é
a mesma. O que não é dado ligado (outro texto, dado dentro de frase, dado que a habilidade não usa) fica como estava.

O reparo único das sessões salvas antes do 31.118 (`scripts/gravacao-com-marcador.py`, sim da orquestradora 07:52Z)
aplica a mesma regra: ensaio numa cópia por padrão; `--aplicar` exige `--backup`; idempotente; só ids e contagens.

Nível de prova: `simulated` (harness com aparelho falso; nenhuma IA).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from app.models import InstanceState
from app.training import dado_da_persona as dp
from app.training.reparo_da_gravacao import marcar_gravacoes_salvas

from .conftest import Harness
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_treino_dado_da_persona import EMAIL, PERSONA, _proposta, _sessao_com_persona
from .test_treino_previa_e_refazer_receitas import _esquecer_o_aparelho


def _textos(st, sid: str) -> list[str | None]:  # type: ignore[no-untyped-def]
    return [r["text"] for r in st.db.query("SELECT text FROM training_inputs WHERE session_id=? AND type='text'"
                                           " ORDER BY seq", (sid,))]


# ------------------------------------------------------------------ as funções puras
def test_so_o_campo_inteiro_de_um_dado_ligado_recebe_a_marca() -> None:
    entradas = [{"seq": 1, "type": "text", "text": f" {EMAIL.upper()} "}, {"seq": 2, "type": "text", "text": "oi Ana!"},
                {"seq": 3, "type": "text", "text": "Lopes"}, {"seq": 4, "type": "tap", "text": EMAIL},
                {"seq": 5, "type": "text", "text": None}]
    assert dp.marcas_das_entradas(entradas, {"perfil_email": EMAIL, "perfil_nome": "Ana"}) == [(1, "{perfil_email}")]
    assert dp.marcas_das_entradas(entradas, {}) == []                     # a habilidade não usa dado nenhum
    assert dp.marcas_das_entradas([{"seq": 1, "type": "text", "text": "Al"}], {"perfil_nome": "Al"}) == []  # curto


def test_o_valor_volta_so_em_memoria_e_a_demonstracao_reconhece_a_marca() -> None:
    marcadas = [{"seq": 1, "type": "text", "text": "{perfil_email}"}, {"seq": 2, "type": "text", "text": "{perfil_x}"},
                {"seq": 3, "type": "text", "text": "oi {perfil_email}"}]
    com = dp.com_valores(marcadas, PERSONA)
    assert [e["text"] for e in com] == [EMAIL, "{perfil_x}", "oi {perfil_email}"]   # só o campo inteiro e conhecido
    assert marcadas[0]["text"] == "{perfil_email}"                                   # a entrada original não muda
    assert dp.demonstrados(PERSONA, marcadas) == {"perfil_email": EMAIL}


# ------------------------------------------------------------------ o salvar, a leitura e o reparo
async def test_o_save_troca_o_texto_gravado_pela_marca_e_a_api_devolve_a_marca(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)
    assert _textos(st, sid) == [EMAIL]                                     # aberta: em claro, para a proposta
    await st.skills.preview(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert _textos(st, sid) == [EMAIL]                                     # a prévia não grava nada
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert _textos(st, sid) == ["{perfil_email}"]
    linha = st.db.one("SELECT has_text, text_len FROM training_inputs WHERE session_id=? AND type='text'", (sid,))
    assert linha["has_text"] == 1 and linha["text_len"] == len(EMAIL)     # o resto da linha fica como foi gravado
    async with _cliente(harness) as c:
        corpo = (await c.get(f"/api/training/{sid}")).text
    assert "{perfil_email}" in corpo and EMAIL not in corpo


async def test_o_texto_que_nao_e_dado_ligado_fica_como_estava(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)
    p = _proposta("entre no app com {email} e mande {mensagem}")
    p["parameters"][0]["example"] = "outra@exemplo.test"                   # o parâmetro não é o dado da persona
    p["steps"][0]["goal"] = "digitar o e-mail no campo"
    await st.skills.save(sid, proposal=p, profile_ids=[], group_ids=[])
    assert _textos(st, sid) == [EMAIL]                                     # a habilidade não usa {perfil_email}


async def test_o_treino_sem_persona_nao_marca(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness, persona=None)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert _textos(st, sid) == [EMAIL]


async def test_refazer_as_receitas_le_o_valor_da_persona_e_a_receita_digita_a_marca(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)
    rt = st.devices.get("android-01")
    _esquecer_o_aparelho(rt)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    assert _textos(st, sid) == ["{perfil_email}"] and st.db.scalar("SELECT COUNT(*) FROM recipes") == 0
    rt.state = InstanceState.online
    refeito = await st.skills.refazer_receitas(sid)
    (escrever,) = [s for s in refeito["steps"] if s["key"] == "escrever"]
    assert escrever["recipe"], escrever                                    # sem o valor, "não é 100 % coberto"
    acoes = st.db.scalar("SELECT actions FROM recipes")
    assert "{perfil_email}" in acoes and EMAIL not in acoes
    assert _textos(st, sid) == ["{perfil_email}"]                          # o reparo não devolve o valor ao banco


# ------------------------------------------------------------------ o reparo das sessões salvas antes do 31.118
RAIZ = Path(__file__).resolve().parents[2]


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gravacao_com_marcador", RAIZ / "scripts" / "gravacao-com-marcador.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


async def _salva_antes_do_31118(harness: Harness):  # type: ignore[no-untyped-def]
    """Uma sessão salva como antes do 31.118: o `save` de hoje marca, então o texto volta ao valor à mão."""
    st, sid = await _sessao_com_persona(harness)
    await st.skills.save(sid, proposal=_proposta(), profile_ids=[], group_ids=[])
    st.db.execute("UPDATE training_inputs SET text=? WHERE session_id=? AND type='text'", (EMAIL, sid))
    return st, sid


async def test_o_reparo_conta_sem_gravar_e_depois_marca_uma_vez(harness: Harness) -> None:
    st, sid = await _salva_antes_do_31118(harness)
    variaveis = st.repo.variaveis_da_persona
    contado = marcar_gravacoes_salvas(st.db, variaveis, escrever=False)
    assert contado == {"sessoes_lidas": 1, "sessoes_com_marca": 1, "entradas_marcadas": 1, "sessoes": [sid]}
    assert _textos(st, sid) == [EMAIL]                                     # só contou
    assert marcar_gravacoes_salvas(st.db, variaveis, escrever=True)["entradas_marcadas"] == 1
    assert _textos(st, sid) == ["{perfil_email}"]
    assert marcar_gravacoes_salvas(st.db, variaveis, escrever=True)["entradas_marcadas"] == 0   # idempotente


async def test_o_reparo_nao_toca_sessao_aberta_nem_dado_que_o_fluxo_nao_usa(harness: Harness) -> None:
    st, sid = await _sessao_com_persona(harness)                           # gravada, não salva
    assert marcar_gravacoes_salvas(st.db, st.repo.variaveis_da_persona, escrever=True)["sessoes_lidas"] == 0
    p = _proposta()
    p["parameters"][0]["example"] = "outra@exemplo.test"
    p["steps"][0]["goal"] = "digitar o e-mail no campo"
    await st.skills.save(sid, proposal=p, profile_ids=[], group_ids=[])
    r = marcar_gravacoes_salvas(st.db, st.repo.variaveis_da_persona, escrever=True)
    assert r["sessoes_lidas"] == 1 and r["entradas_marcadas"] == 0 and _textos(st, sid) == [EMAIL]


async def test_o_script_ensaia_na_copia_exige_backup_e_nao_ecoa_o_valor(harness: Harness, tmp_path: Path,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    st, sid = await _salva_antes_do_31118(harness)
    if st.db.dialect != "sqlite":
        pytest.skip("o script só opera SQLite local")
    banco, script = Path(st.db.path), _script()
    assert script.main(["--banco", str(banco)]) == 0
    ensaio = capsys.readouterr().out
    assert "ENSAIO" in ensaio and "entradas_marcadas=1" in ensaio and sid in ensaio and EMAIL not in ensaio
    assert _textos(st, sid) == [EMAIL]                                     # o original não foi tocado
    with pytest.raises(SystemExit):                                        # a confirmação exige o backup
        script.main(["--banco", str(banco), "--aplicar"])
    with pytest.raises(SystemExit):
        script.main(["--banco", str(banco), "--aplicar", "--backup", str(tmp_path / "nao-existe")])
    capsys.readouterr()
    assert _textos(st, sid) == [EMAIL]
    assert script.main(["--banco", str(banco), "--aplicar", "--backup", str(tmp_path)]) == 0
    aplicado = capsys.readouterr().out
    assert "APLICADO" in aplicado and "entradas_marcadas=1" in aplicado and EMAIL not in aplicado
    assert _textos(st, sid) == ["{perfil_email}"]
    assert script.main(["--banco", str(banco), "--aplicar", "--backup", str(tmp_path)]) == 0
    assert "entradas_marcadas=0" in capsys.readouterr().out                # idempotente
