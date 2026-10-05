"""31.89 F1 e F6: o casamento do comando por fluxo escolhe o molde MAIS ESPECÍFICO, e a troca do valor de exemplo
por `{nome}` respeita a fronteira de palavra.

F1: antes, entre dois fluxos ativos que casavam o mesmo comando, ganhava o de mais usos ("curtir {x}" engolia
"curtir o post de {p}"). Agora a ordem é especificidade (critério do resolvedor v2, `matching.specificity`), depois
`uses`, depois `created_at`. F6: com "Ana" de exemplo, "Banana" do plano virava "B{nome}na".

Nível de prova: `simulated` (banco de teste migrado pela fábrica da suíte; nenhum aparelho, nenhuma IA).
"""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.db import Database
from app.modules.skills.domain.lifecycle import SkillState
from app.modules.skills.domain.matching import PLACEHOLDER, specificity
from app.modules.skills.domain.versions import Provenance, SourceKind
from app.taskqueue.flows import FlowStore, _sub_values

from .fake_skills import banco, documento, fluxo, perfil, repositorio


def _plano(*nomes: str) -> dict[str, object]:
    return {"summary": "Curtir " + nomes[0], "app_id": "instagram", "parameters": {n: "{" + n + "}" for n in nomes},
            "steps": [{"key": "abrir", "title": "Abrir {" + nomes[0] + "}", "goal": "abrir {" + nomes[0] + "}",
                       "postcondition": {"kind": "app_foreground", "value": "instagram", "description": "app aberto"}}],
            "planner": {"provider": "fluxo", "model": "m", "simulated": True}}


GENERICO = "curtir {x}"
ESPECIFICO = "curtir o post de {p}"
COMANDO = "curtir o post de fulano"


@pytest.fixture
def db(tmp_path: Path) -> Iterator[Database]:
    d = banco(tmp_path)
    yield d
    d.close()


def _vencedor(db: Database, comando: str = COMANDO, perfis: list[str | None] | None = None) -> str | None:
    achado = FlowStore(db).match(comando, perfis)
    return None if achado is None else str(achado[0]["id"])


# ------------------------------------------------------------------ F1: o mais específico primeiro
def test_o_especifico_vence_o_generico_mesmo_com_menos_usos(db: Database) -> None:
    fluxo(db, "f-generico", GENERICO, plano=_plano("x"), uses=50)
    fluxo(db, "f-especifico", ESPECIFICO, plano=_plano("p"), uses=1)
    # Mutação: voltar ao `ORDER BY uses DESC` puro faz `_vencedor` devolver "f-generico" e esta asserção falha.
    assert _vencedor(db) == "f-especifico"
    achado = FlowStore(db).match(COMANDO)
    assert achado is not None and achado[1].parameters == {"p": "fulano"}      # o plano também é o do específico


def test_o_especifico_vence_em_qualquer_ordem_de_criacao(db: Database) -> None:
    fluxo(db, "f-especifico", ESPECIFICO, plano=_plano("p"), uses=0, criado="2026-01-01T00:00:00+00:00")
    fluxo(db, "f-generico", GENERICO, plano=_plano("x"), uses=0, criado="2025-01-01T00:00:00+00:00")
    assert _vencedor(db) == "f-especifico"          # o genérico é mais antigo e empata em usos: ainda assim perde


def test_o_generico_ainda_casa_o_que_o_especifico_nao_cobre(db: Database) -> None:
    fluxo(db, "f-generico", GENERICO, plano=_plano("x"), uses=50)
    fluxo(db, "f-especifico", ESPECIFICO, plano=_plano("p"), uses=1)
    assert _vencedor(db, "curtir a foto de fulano") == "f-generico"


def test_mesma_especificidade_vence_o_de_mais_usos(db: Database) -> None:
    fluxo(db, "f-poucos", "curtir o post de {a}", plano=_plano("a"), uses=1)
    fluxo(db, "f-muitos", "curtir o post de {b}", plano=_plano("b"), uses=9)
    assert specificity("curtir o post de {a}") == specificity("curtir o post de {b}")
    # Mutação: trocar a ordenação por uma que ignore `uses` (ou que desfaça a estabilidade do `sorted`) quebra o
    # desempate de hoje e `_vencedor` devolve "f-poucos".
    assert _vencedor(db) == "f-muitos"


def test_empate_de_especificidade_e_usos_fica_pelo_mais_antigo(db: Database) -> None:
    fluxo(db, "f-novo", "curtir o post de {a}", plano=_plano("a"), uses=3, criado="2026-02-01T00:00:00+00:00")
    fluxo(db, "f-antigo", "curtir o post de {b}", plano=_plano("b"), uses=3, criado="2026-01-01T00:00:00+00:00")
    assert _vencedor(db) == "f-antigo"


def test_o_escopo_por_perfil_vale_antes_da_ordem(db: Database) -> None:
    perfil(db, "p1")
    perfil(db, "p2")
    fluxo(db, "f-generico", GENERICO, plano=_plano("x"), uses=1)
    fluxo(db, "f-especifico", ESPECIFICO, plano=_plano("p"), uses=1, perfis=("p1",))
    assert _vencedor(db, perfis=["p1"]) == "f-especifico"
    # p2 está fora do escopo do específico: ele não vence (nem entra); sobra o genérico.
    assert _vencedor(db, perfis=["p2"]) == "f-generico"
    assert _vencedor(db, perfis=["p1", "p2"]) == "f-generico"


def test_fluxo_que_nao_esta_ativo_nao_entra_na_ordem(db: Database) -> None:
    fluxo(db, "f-generico", GENERICO, plano=_plano("x"), uses=1)
    fluxo(db, "f-candidato", ESPECIFICO, plano=_plano("p"), uses=1, status="candidate")
    assert _vencedor(db) == "f-generico"


# ------------------------------------------------------------------ F1: paridade com o resolvedor v2
MOLDES = ["curtir {x}", "curtir o post de {p}", "curtir o post de {p} em {app}", "curtir {x} {y}",
          "curtir o post de {p} agora", "{x} o post de {y}"]


def test_a_ordem_do_fluxo_e_a_do_resolvedor_v2(tmp_path: Path) -> None:
    """Os mesmos moldes publicados como habilidades e gravados como fluxos: o primeiro que casa é o mesmo, para cada
    comando. As habilidades entram antes (publicar recusa o comando de um fluxo ativo) e os fluxos depois, com `uses`
    em ordem CONTRÁRIA à especificidade para o `uses` sozinho dar a resposta errada."""
    db = banco(tmp_path, "paridade.sqlite3")
    repo, _ = repositorio(db)
    try:
        for i, molde in enumerate(MOLDES):
            sid = f"ig.m{i}"
            v = repo.create_draft(sid, documento(sid, molde), source=Provenance(SourceKind.MANUAL), by="t")
            repo.transition(v.ref, SkillState.CANDIDATE, by="t", reason="submeter")
            repo.transition(v.ref, SkillState.VALIDATED, by="t", reason="ok", manual=True)
            repo.transition(v.ref, SkillState.PUBLISHED, by="t", reason="publicar")
        ordem_v2 = {m: i for i, m in enumerate(sorted(MOLDES, key=lambda m: (-specificity(m)[0], -specificity(m)[1])))}
        for i, molde in enumerate(MOLDES):
            # mais específico = índice menor em `ordem_v2` = MENOS usos
            fluxo(db, f"f{i}", molde, plano=_plano(*PLACEHOLDER.findall(molde)), uses=ordem_v2[molde] * 10)
        comandos = ["curtir o post de fulano", "curtir o post de fulano agora", "curtir o post de fulano em app1",
                    "curtir a foto", "curtir a b", "ver o post de fulano"]
        vistos = 0
        for comando in comandos:
            v2 = repo.candidates(comando, None)
            antigo = FlowStore(db).match(comando)
            if not v2:
                assert antigo is None, comando
                continue
            vistos += 1
            assert antigo is not None, comando
            assert antigo[0]["command_template"] == v2[0].skill.version.command_template, comando
        assert vistos >= 4                            # o teste não passa por vazio
    finally:
        db.close()


# ------------------------------------------------------------------ F6: troca por fronteira de palavra
def test_ana_nao_mexe_em_banana() -> None:
    # Mutação: voltar ao `text.replace(value, ...)` produz "B{nome}na" e esta asserção falha.
    assert _sub_values("Banana", {"nome": "Ana"}) == "Banana"
    assert _sub_values("Banana da Ana", {"nome": "Ana"}) == "Banana da {nome}"
    assert _sub_values("Anatomia e Ana", {"nome": "Ana"}) == "Anatomia e {nome}"


@pytest.mark.parametrize(("texto", "esperado"), [
    ("Ana", "{nome}"),
    ("Ana, vem", "{nome}, vem"),
    ("(Ana)", "({nome})"),
    ("curtir de Ana.", "curtir de {nome}."),
    ("Ana\nAna", "{nome}\n{nome}"),
    ("a_Ana", "a_Ana"),                       # `_` é de palavra
    ("Ana2", "Ana2"),
    ("2Ana", "2Ana"),
])
def test_valor_isolado_ou_com_pontuacao_e_trocado(texto: str, esperado: str) -> None:
    assert _sub_values(texto, {"nome": "Ana"}) == esperado


def test_valor_com_espaco() -> None:
    v = {"nome": "Maria Clara"}
    assert _sub_values("abrir Maria Clara agora", v) == "abrir {nome} agora"
    assert _sub_values("Maria Clarabela", v) == "Maria Clarabela"
    assert _sub_values("Maria Clara, Maria Clara", v) == "{nome}, {nome}"


def test_valor_que_comeca_ou_termina_com_simbolo_continua_sendo_trocado() -> None:
    assert _sub_values("seguir @fulano", {"u": "@fulano"}) == "seguir {u}"
    assert _sub_values("seguir x@fulano", {"u": "@fulano"}) == "seguir x{u}"       # o símbolo já delimita
    assert _sub_values("seguir @fulano2", {"u": "@fulano"}) == "seguir @fulano2"    # a borda direita é letra
    assert _sub_values("pagar R$ 10 hoje", {"v": "R$ 10"}) == "pagar {v} hoje"
    assert _sub_values("pagar R$ 100", {"v": "R$ 10"}) == "pagar R$ 100"
    assert _sub_values("ver #tag!", {"t": "#tag!"}) == "ver {t}"


def test_valor_com_acento() -> None:
    v = {"nome": "João"}
    assert _sub_values("curtir João e Joãozinho", v) == "curtir {nome} e Joãozinho"
    assert _sub_values("é de São João.", v) == "é de São {nome}."
    assert _sub_values("Ação", {"x": "Aç"}) == "Ação"                    # `ã` é letra: não há fronteira ali
    assert _sub_values("éJoão", v) == "éJoão"                            # letra acentuada colada antes


def test_dois_valores_em_que_um_contem_o_outro() -> None:
    v = {"curto": "Ana", "longo": "Ana Maria"}
    assert _sub_values("Ana Maria e Ana", v) == "{longo} e {curto}"       # o mais longo primeiro (ordem de antes)
    assert _sub_values("Banana Maria e Ana Maria", v) == "Banana Maria e {longo}"


def test_numero_colado_a_unidade_e_trocado_mas_nao_o_numero_maior() -> None:
    """Borda em DÍGITO exige só um não-dígito: letra vizinha pode. Mutação: com a borda de caractere de palavra também para dígito (a primeira versão do F6)
    "esperar 10min" fica sem troca e o primeiro assert falha: o plano reaproveitado com 20 diria "10min" calado."""
    v = {"n": "10"}
    assert _sub_values("esperar 10min", v) == "esperar {n}min"
    assert _sub_values("esperar 10 min", v) == "esperar {n} min"
    assert _sub_values("esperar 100", v) == "esperar 100"
    assert _sub_values("esperar 110", v) == "esperar 110"
    assert _sub_values("versão v10", v) == "versão v{n}"
    assert _sub_values("10, 100 e 10", v) == "{n}, 100 e {n}"


@pytest.mark.parametrize(("texto", "valor"), [
    ("posts", "post"), ("ana_silva", "ana"), ("fulano123", "fulano"), ("#tag2026", "tag"),
])
def test_palavra_maior_nao_perde_um_pedaco(texto: str, valor: str) -> None:
    assert _sub_values(texto, {"v": valor}) == texto


@pytest.mark.parametrize(("texto", "esperado"), [
    ('curtir "ana"', 'curtir "{v}"'),
    ("curtir 'ana'", "curtir '{v}'"),
    ("curtir ana!", "curtir {v}!"),
    ("curtir @ana", "curtir @{v}"),
    ("o post da ana's", "o post da {v}'s"),
])
def test_aspas_pontuacao_arroba_e_genitivo_seguem_trocando(texto: str, esperado: str) -> None:
    assert _sub_values(texto, {"v": "ana"}) == esperado


def test_o_marcador_posto_nao_e_reescrito_por_outro_valor() -> None:
    # "nome" é palavra inteira dentro de "{nome}"; sem proteger o marcador, o segundo valor o corromperia.
    assert _sub_values("Ana e nome", {"a": "Ana", "b": "nome"}) == "{a} e {b}"
    assert _sub_values("Ana", {"a": "Ana", "b": "a"}) == "{a}"


def test_texto_vazio_e_valor_vazio_ficam_como_estao() -> None:
    assert _sub_values(None, {"n": "Ana"}) is None
    assert _sub_values("", {"n": "Ana"}) == ""
    assert _sub_values("abc", {"n": ""}) == "abc"


# ------------------------------------------------------------------ N2: parâmetro reservado (limite conhecido)
def test_molde_com_reservado_conta_como_buraco_na_especificidade() -> None:
    """Limite CONHECIDO, fixado como está hoje (não é desejo): `specificity` conta `{instance_id}` como buraco, mas
    `_extract` o trata como texto literal do comando. O molde com reservado perde um ponto de buracos no critério
    mesmo casando só o comando que traz o literal; se um dia isso mudar, este teste muda junto, de propósito."""
    assert specificity("abrir {p} {instance_id}") == specificity("abrir {p} {q}") < specificity("abrir {x}")


def test_molde_com_reservado_perde_para_o_generico_de_menos_buracos(db: Database) -> None:
    fluxo(db, "f-reservado", "abrir {p} {instance_id}", plano=_plano("p"), uses=9)
    fluxo(db, "f-generico", "abrir {x}", plano=_plano("x"), uses=1)
    comando = "abrir fulano {instance_id}"            # os dois casam: o literal do reservado e o buraco do genérico
    assert FlowStore._extract("abrir {p} {instance_id}", comando) == {"p": "fulano"}
    assert FlowStore._extract("abrir {x}", comando) == {"x": "fulano {instance_id}"}
    assert _vencedor(db, comando) == "f-generico"
