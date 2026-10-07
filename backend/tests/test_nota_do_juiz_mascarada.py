"""31.242: a nota do juiz gravada na evidência sai sem o usuário da conta e sem o texto da etapa.

Achado da leitura das evidências 2951 a 2956 da onda 2 (07/10, só leitura, sem copiar valor): a nota do juiz repete o
usuário da conta da persona ("@fulano said …") e o texto do comentário. O mapa do registro (31.113 F1) leva o nome, o
e-mail e o que o plano cita, mas não o usuário da conta nem o texto da etapa. Agora a nota passa pelo mapa da NOTA
(`Repository.trocas_da_nota`): o do registro, mais o usuário de cada conta da persona (com e sem a arroba) e os textos
da etapa (`content`, o alvo, a legenda) como `{chave}`. Na gravação (nota nova) e na saída (painel, API, evento: a
nota antiga também sai mascarada; o banco não é reescrito).

Nível de prova: `simulated` (banco de teste, valores sintéticos inventados; nenhum aparelho nem IA). Os asserts de
ausência usam `not in`, para a falha não imprimir nada além do valor inventado.
"""
from __future__ import annotations

import json

from .conftest import Harness
from .test_registro_mascarado_da_persona import EXIBICAO, _execucao

USUARIO = "pessoa.sintetica"          # o `username` da persona sintética do 31.113 (a conta do Instagram dela)
COMENTARIO = "texto inventado do comentario para o teste da nota"
ALVO = "perfil.de.terceiro.inventado"


def _com_texto_na_etapa(h: Harness, aid: str) -> str:
    st = h.state
    assert st is not None
    sid = aid.rsplit(":", 1)[0]
    st.db.execute("UPDATE steps SET bindings=? WHERE id=?",
                  (json.dumps({"content": COMENTARIO, "username": ALVO, "curto": "ab"}), sid))
    return sid


def _usuario_da_conta(h: Harness, run_id: str) -> str:
    """O nome da variável do usuário da conta da persona (a do 31.113 cria a conta junto)."""
    st = h.state
    assert st is not None
    pid = st.db.scalar("SELECT profile_id FROM objectives WHERE run_id=?", (run_id,))
    nomes = [n for n, v in st.repo.variaveis_da_persona(str(pid)).items() if v == USUARIO]
    assert nomes and all(n.startswith("conta_") and "_usuario" in n for n in nomes), "a persona sem a conta"
    return nomes[0]


async def test_a_nota_nova_sai_com_o_marcador_da_conta_e_da_etapa(harness: Harness) -> None:
    run_id, aid = _execucao(harness)
    sid = _com_texto_na_etapa(harness, aid)
    conta = _usuario_da_conta(harness, run_id)
    st = harness.state
    assert st is not None
    st.repo.add_evidence(run_id=run_id, instance_id="android-01", step_id=sid, attempt_id=aid, kind="screenshot",
                         note=f"@{USUARIO} said {COMENTARIO}; {USUARIO} aparece como autor; alvo @{ALVO}; {EXIBICAO}")
    nota = str(st.db.scalar("SELECT note FROM evidence WHERE run_id=?", (run_id,)))
    assert USUARIO not in nota and COMENTARIO not in nota and ALVO not in nota and EXIBICAO not in nota
    assert f"@{{{conta}}} said {{content}}" in nota and f"{{{conta}}} aparece" in nota and "@{username}" in nota
    assert "{perfil_nome_exibicao}" in nota                                         # o mapa do 31.113 continua
    evento = str(st.db.scalar("SELECT data FROM events WHERE run_id=? AND kind='evidence.added'", (run_id,)))
    assert USUARIO not in evento and COMENTARIO not in evento


async def test_a_nota_antiga_sai_mascarada_sem_reescrever_o_banco(harness: Harness) -> None:
    run_id, aid = _execucao(harness)
    sid = _com_texto_na_etapa(harness, aid)
    st = harness.state
    assert st is not None
    # a nota gravada antes do 31.242, com o valor em claro (direto no banco, como as 2951-2956)
    st.db.execute("INSERT INTO evidence(run_id, instance_id, step_id, attempt_id, ts, kind, note, path, redacted)"
                  " VALUES (?,?,?,?,?,?,?,?,?)", (run_id, "android-01", sid, aid, "2026-10-07T10:10:00.000Z",
                                                  "screenshot", f"{USUARIO} said {COMENTARIO}", None, 0))
    detalhe = st.repo.run_detail(run_id)
    assert detalhe is not None
    notas = [e.note or "" for e in detalhe.evidence]
    assert notas and all(USUARIO not in n and COMENTARIO not in n for n in notas)
    assert any("said {content}" in n for n in notas)
    assert USUARIO in str(st.db.scalar("SELECT note FROM evidence WHERE run_id=?", (run_id,)))   # o banco fica


async def test_valor_curto_ou_ja_marcado_fica_como_esta(harness: Harness) -> None:
    run_id, aid = _execucao(harness)
    sid = _com_texto_na_etapa(harness, aid)
    st = harness.state
    assert st is not None
    trocas = st.repo.trocas_da_nota(run_id, sid, aid)
    assert "ab" not in trocas                                                     # abaixo do piso do mascarador
    st.db.execute("UPDATE steps SET bindings=? WHERE id=?", (json.dumps({"content": "{perfil_nome} disse oi"}), sid))
    assert not any(v == "{content}" for v in st.repo.trocas_da_nota(run_id, sid, aid).values())
    st.repo.add_evidence(run_id=run_id, instance_id="android-01", step_id=sid, attempt_id=aid, kind="text",
                         note="nada de dado aqui: ab, tela inicial")
    assert st.db.scalar("SELECT note FROM evidence WHERE run_id=?", (run_id,)) == "nada de dado aqui: ab, tela inicial"


async def test_sem_persona_a_nota_so_leva_o_texto_da_etapa(harness: Harness) -> None:
    run_id, aid = _execucao(harness, com_persona=False)
    sid = _com_texto_na_etapa(harness, aid)
    st = harness.state
    assert st is not None
    st.repo.add_evidence(run_id=run_id, instance_id="android-01", step_id=sid, attempt_id=aid, kind="text",
                         note=f"o comentario {COMENTARIO} saiu")
    assert st.db.scalar("SELECT note FROM evidence WHERE run_id=?", (run_id,)) == "o comentario {content} saiu"
