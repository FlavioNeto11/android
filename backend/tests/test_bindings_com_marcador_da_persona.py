"""31.113 F3: os `bindings` da etapa guardam o marcador da persona; a porta decide com o valor de AGORA.

A linha (e o `plan_versions`) guarda `{perfil_*}`; `Repository.bindings_da_etapa` é o leitor ÚNICO da porta, da política
e da chave da aprovação: resolve a persona lida na hora, em memória, sem gravar. A chave sai do valor, igual na prévia e
na execução; a persona trocada depois do sim muda a chave e a porta pergunta de novo. `argumentos_da_acao` e
`tem_variavel` ficam como estão (parecer da Ferramentas, 06/10 04:21Z): o dado ausente ou vazio deixa o marcador, e a
chave falha fechado. O pedido de aprovação guarda o marcador (alvo e texto pela máscara reversível, só com a volta
exata; resumo pela máscara do registro); a tela do painel recebe o valor de agora, e o canal recebe o marcador.

Nível de prova: `simulated` (harness, catálogo do Instagram, banco de teste, valores sintéticos). Nada real.
"""
from __future__ import annotations

import ast
import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from app.models import ProfileCreate
from app.planning.capabilities import capability_of, contraparte
from app.porta_do_plano import AprovarPlanoBody, ItemAprovado, aprovar_plano, previa_da_porta, previa_para_o_canal
from app.taskqueue.dado_da_persona import resolver_argumentos, resolver_texto
from app.util import now, now_iso, to_iso

from .test_executor_honra_o_plano import _aprovado_no_plano
from .test_capabilities import IG
from .test_perfil_bloqueado_e_capacidades import _cliente
from .test_porta_do_plano import ALVO, DM, _gate, _plano, _por_chave, _sem_iniciar

NOME, OUTRO = "Zelda", "Odete"
MOLDE = "Oi, aqui é {perfil_nome}!"
DM_MOLDE = {**DM, "content": MOLDE}


def _persona(state: Any, nome: str = NOME, *, pid: str | None = None) -> str:
    pid = pid or str(state.db.scalar("SELECT profile_id FROM objectives WHERE id='run-p:android-01'"))
    state.social_repo.update_profile(pid, {"first_name": nome})
    return pid


def _aprovado_com_molde(state: Any) -> dict[str, Any]:
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM_MOLDE}])
    _persona(state)
    item = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    assert item["selo"] == "aprovacao" and item["chave"], item
    aprovar_plano(state, "run-p", AprovarPlanoBody(vista_em=now_iso(), aprovar=[
        ItemAprovado(step_id=item["step_id"], chave=item["chave"])]), por="flavio")
    return item


# ------------------------------------------------------------------ o resolvedor (condição 1 da Ferramentas)
@pytest.mark.parametrize("variaveis", [{}, {"perfil_nome": ""}, {"perfil_nome": "   "}, {"perfil_sobrenome": NOME}])
def test_dado_ausente_ou_vazio_deixa_o_marcador_nunca_vazio(variaveis: dict[str, str]) -> None:
    assert resolver_texto(MOLDE, variaveis) == MOLDE
    assert resolver_argumentos({"content": MOLDE, "n": 3}, variaveis) == {"content": MOLDE, "n": 3}
    assert resolver_texto(MOLDE, {"perfil_nome": NOME}) == f"Oi, aqui é {NOME}!"
    assert resolver_texto("{perfil_alvo} e {item}", {"perfil_alvo": "x", "item": "y"}) == "{perfil_alvo} e {item}"


async def test_argumento_da_persona_vazio_nao_fecha_a_chave_no_plano(harness: Any) -> None:
    state = harness.state
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM_MOLDE}])
    _persona(state, "")
    item = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    assert item["chave"] is None and item["selo"] != "aprovacao"               # o marcador sobra: falha fechado
    _persona(state)
    assert _por_chave(previa_da_porta(state, "run-p"))["dm"]["chave"]


# ------------------------------------------------------------------ a chave sobre o valor, igual nos dois lados
async def test_a_linha_guarda_o_marcador_e_a_chave_da_previa_vale_na_execucao(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    item = _aprovado_com_molde(state)
    assert item["texto"] == f"Oi, aqui é {NOME}!"                                 # a tela do painel: o valor de agora
    pedido = state.db.one("SELECT * FROM pending_approvals WHERE origem='plano'")
    assert pedido["generated_content"] == MOLDE and NOME not in json.dumps(dict(pedido), default=str)
    assert json.loads(state.db.scalar("SELECT bindings FROM steps WHERE key='dm'"))["content"] == MOLDE
    assert await _gate(state, "dm") is None                                      # a execução honra o sim
    assert [r["origem"] for r in state.db.query("SELECT origem FROM pending_approvals")] == ["plano"]


async def test_a_persona_trocada_depois_do_sim_faz_a_porta_perguntar_de_novo(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _aprovado_com_molde(state)
    _persona(state, OUTRO)
    veredito = await _gate(state, "dm")
    assert veredito is not None and not veredito.allowed and veredito.policy == "approval_required"
    linhas = {r["origem"]: r for r in state.db.query("SELECT * FROM pending_approvals")}
    assert linhas["plano"]["status"] == "expired" and "a chave divergiu" in linhas["plano"]["decided_note"]
    novo = linhas["execucao"]
    assert novo["generated_content"] == MOLDE                                    # o pedido guarda o marcador
    assert OUTRO not in json.dumps(dict(novo), default=str) and NOME not in json.dumps(dict(novo), default=str)
    eventos = " ".join(str(r["data"]) for r in state.db.query("SELECT data FROM events WHERE kind='approval.pending'"))
    assert MOLDE in eventos and OUTRO not in eventos
    async with _cliente(harness) as c:                                           # a tela: o valor de AGORA
        [na_tela] = (await c.get("/api/approvals", params={"run_id": "run-p", "status": "pending"})).json()
    assert na_tela["content"] == f"Oi, aqui é {OUTRO}!" and na_tela["generated_content"] == f"Oi, aqui é {OUTRO}!"


# ------------------------------------------------------------------ frota e repetição (as regras sem chave)
async def test_o_mesmo_marcador_em_duas_personas_nao_e_o_mesmo_alvo(harness: Any) -> None:
    """`_mesmo_pedido_noutras_contas`: cada irmã se resolve com a persona do SEU objetivo. Com o marcador cru, as duas
    linhas teriam o mesmo `{perfil_nome}` e pareceriam o mesmo alvo."""
    state = harness.state
    dm = {**DM, "username": "{perfil_nome}"}
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": dm}])
    _persona(state)
    outra = state.social.create_profile(ProfileCreate(username="pessoa.dois", instance_id="android-02")).id
    _persona(state, OUTRO, pid=outra)
    state.db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters, profile_id)"
                     " VALUES ('run-p:android-02','run-p','android-02','pending',1,'{}',?)", (outra,))
    state.db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, bindings)"
        " VALUES ('run-p:android-02:v1:dm','run-p','run-p:android-02','android-02',1,1,'dm','dm','x','[]',1,'[]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'pending','SEND_MESSAGE',?)",
        (json.dumps(dm),))
    cap = capability_of(IG, "SEND_MESSAGE")
    assert cap is not None and cap.limit_bucket
    obj = state.db.one("SELECT * FROM objectives WHERE id='run-p:android-01'")
    meu, alvo = str(obj["profile_id"]), contraparte(cap, {"username": NOME})
    assert alvo
    assert state._mesmo_pedido_noutras_contas(obj, cap, meu, alvo) == []                    # noqa: SLF001
    _persona(state, NOME, pid=outra)                                       # agora as duas falam com a mesma pessoa
    irmas = state._mesmo_pedido_noutras_contas(obj, cap, meu, alvo)                       # noqa: SLF001
    assert [o for o, _a, _d in irmas] == ["run-p:android-02"]


@pytest.mark.parametrize("gravado", [MOLDE, f"Oi, aqui é {NOME}!"], ids=["pedido_com_marcador", "pedido_antigo"])
async def test_a_repeticao_se_acha_com_o_marcador_e_com_o_valor_antigo(harness: Any, monkeypatch: Any,
                                                                       gravado: str) -> None:
    """O pedido de outra execução, aprovado depois do sim, com a mesma DM: gravado com o marcador (F3) ou com o valor
    (antes da F3), a repetição se acha. Com o marcador cru, `MOLDE` ≠ o texto de agora e ela deixaria de se achar."""
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM_MOLDE}])
    pid = _persona(state)
    outro = state.approvals.open(profile_id=pid, capability="SEND_MESSAGE", summary="dm de outra execução",
                                 target=ALVO, content=gravado, run_id="r-outra")
    state.db.execute("UPDATE pending_approvals SET created_at=? WHERE id=?",
                     (to_iso(now() - timedelta(minutes=5)), outro.id))
    item = _por_chave(previa_da_porta(state, "run-p"))["dm"]
    aprovar_plano(state, "run-p", AprovarPlanoBody(vista_em=now_iso(), aprovar=[
        ItemAprovado(step_id=item["step_id"], chave=item["chave"])]), por="flavio")
    state.db.execute("UPDATE pending_approvals SET status='approved', decided_at=? WHERE id=?",
                     (to_iso(now() + timedelta(seconds=1)), outro.id))
    veredito = await _gate(state, "dm")
    assert veredito is not None and veredito.policy == "approval_required"
    plano = state.db.one("SELECT status, decided_note FROM pending_approvals WHERE origem='plano'")
    assert plano["status"] == "expired" and "a repetição surgiu depois do sim" in plano["decided_note"]


# ------------------------------------------------------------------ o canal leva o marcador (condição 4)
async def test_a_previa_do_canal_leva_o_marcador_e_a_do_painel_o_valor(harness: Any) -> None:
    state = harness.state
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": {**DM_MOLDE, "username": "{perfil_nome}"}}])
    _persona(state)
    painel = previa_da_porta(state, "run-p")
    canal = previa_para_o_canal(state, painel)
    assert _por_chave(painel)["dm"]["texto"] == f"Oi, aqui é {NOME}!"
    item = _por_chave(canal)["dm"]
    assert item["texto"] == MOLDE and item["alvo"] == "{perfil_nome}"
    assert item["objeto_alvo"] == {"username": "{perfil_nome}"}
    assert NOME not in json.dumps(canal, default=str) and NOME.lower() not in json.dumps(canal, default=str)
    assert item["chave"] == _por_chave(painel)["dm"]["chave"]                     # o gesto do canal casa com a chave


# ------------------------------------------------------------------ máscara reversível (rascunho, edição do dono)
async def test_a_mascara_reversivel_so_grava_o_marcador_com_a_volta_exata(harness: Any) -> None:
    state = harness.state
    _plano(state, [{"key": "dm", "cap": "SEND_MESSAGE", "bindings": DM}])
    _persona(state)
    oid = "run-p:android-01"
    assert state.repo.texto_reversivel(f"Tchau, {NOME}.", oid) == "Tchau, {perfil_nome}."
    assert state.repo.texto_reversivel(f"Tchau, {NOME.upper()}.", oid) == f"Tchau, {NOME.upper()}."   # outra caixa
    assert state.repo.texto_reversivel(f"{{perfil_nome}} e {NOME}", oid) == f"{{perfil_nome}} e {NOME}"  # volta inexata
    assert state.repo.texto_reversivel("Zeldaria", oid) == "Zeldaria"


async def test_a_edicao_do_dono_guarda_o_marcador_e_a_tela_mostra_o_valor(harness: Any, monkeypatch: Any) -> None:
    state = harness.state
    _sem_iniciar(state, monkeypatch)
    _aprovado_no_plano(state, {**DM, "content": "oi"})
    _persona(state)
    state.db.execute("UPDATE pending_approvals SET status='expired'")
    state.db.execute("UPDATE objectives SET status='waiting_user', blocked_kind='approval'")   # a porta parou nele
    pedido = state.approvals.open(profile_id=str(state.db.scalar("SELECT profile_id FROM objectives")),
                                  capability="SEND_MESSAGE", summary="dm", target=ALVO, content="oi", run_id="run-p",
                                  objective_id="run-p:android-01", step_id="run-p:android-01:v1:dm")
    feito = state.approval_service.decide(pedido.id, "edit", content=f"Oi, aqui é {NOME}!")
    linha = state.db.one("SELECT approved_content FROM pending_approvals WHERE id=?", (pedido.id,))
    assert linha["approved_content"] == MOLDE and feito["content"] == MOLDE
    etapa = state.db.one("SELECT bindings, commit_guard FROM steps WHERE key='dm'")
    assert json.loads(etapa["bindings"])["content"] == MOLDE and NOME not in etapa["commit_guard"]
    assert state.approval_service.na_tela(feito)["content"] == f"Oi, aqui é {NOME}!"


# ------------------------------------------------------------------ o cache da F1 percebe o perfil trocado
async def test_o_registro_mascara_o_nome_novo_depois_da_troca_no_perfil(harness: Any) -> None:
    from .test_registro_mascarado_da_persona import _execucao
    run_id, aid = _execucao(harness)
    state = harness.state
    sid = aid.rsplit(":", 1)[0]
    state.repo.decision("antes da troca", run_id=run_id, instance_id="android-01", step_id=sid)   # aquece o cache
    pid = str(state.db.scalar("SELECT profile_id FROM objectives WHERE run_id=?", (run_id,)))
    state.social_repo.update_profile(pid, {"first_name": "Quiteria", "display_name": "Quiteria Nova"})
    state.repo.decision("o ator digitou Quiteria Nova", run_id=run_id, instance_id="android-01", step_id=sid)
    eventos = " ".join(str(r["message"]) for r in state.db.query("SELECT message FROM events WHERE run_id=?", (run_id,)))
    assert "Quiteria" not in eventos and "{perfil_nome_exibicao}" in eventos


# ------------------------------------------------------------------ varredura do leitor único (condição 2)
#: O `loads(<linha>["bindings"])` que pode ficar, e por quê. Fora destes, a leitura crua da porta, da política ou da
#: aprovação seria o marcador comparado com o valor: a chave falharia fechado, mas a frota e a repetição falhariam ABERTO.
_PERMITIDOS = {
    "app/porta_do_plano.py": {"_com_texto"},             # monta a LINHA com o texto editado; quem a lê é o helper
    "app/social/repository.py": {"saidas_da_acao", "etapas_em_curso_da_acao", "pedidos_da_acao"},  # resolvem logo após
    # escrevem a linha (o texto; 31.260 b: a legenda do post em foco, só se vazia); o "não repita" com o marcador
    "app/social/approvals.py": {"definir_texto", "fixar_post_em_foco", "textos_irmaos"},
    "app/state.py": set(),
    "app/gates.py": set(),                               # os portões moram aqui desde o 15.15 F5a
}


def _leituras_cruas(caminho: Path) -> list[tuple[str, int]]:
    arvore = ast.parse(caminho.read_text(encoding="utf-8"))
    achados: list[tuple[str, int]] = []

    def visitar(no: ast.AST, funcao: str) -> None:
        for filho in ast.iter_child_nodes(no):
            nome = filho.name if isinstance(filho, (ast.FunctionDef, ast.AsyncFunctionDef)) else funcao
            if isinstance(filho, ast.Call) and getattr(filho.func, "id", None) == "loads" and filho.args:
                alvo = filho.args[0]
                if isinstance(alvo, ast.Subscript) and isinstance(alvo.slice, ast.Constant) \
                        and alvo.slice.value == "bindings":
                    achados.append((nome, filho.lineno))
            visitar(filho, nome)

    visitar(arvore, "<módulo>")
    return achados


def test_a_porta_le_os_bindings_so_pelo_leitor_unico() -> None:
    raiz = Path(__file__).resolve().parents[1]
    fora = [f"{arquivo}:{linha} ({funcao})" for arquivo, permitidos in _PERMITIDOS.items()
            for funcao, linha in _leituras_cruas(raiz / arquivo) if funcao not in permitidos]
    assert not fora, "leitura crua de `bindings` fora de `Repository.bindings_da_etapa`: " + ", ".join(fora)
    texto = (raiz / "app/gates.py").read_text(encoding="utf-8")      # onde os portões moram desde o 15.15 F5a (antes, `state.py`)
    assert len(re.findall(r"bindings_da_etapa\(", texto)) >= 10
