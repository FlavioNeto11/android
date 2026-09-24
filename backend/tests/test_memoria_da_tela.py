"""Memória do que o perfil viu (decisão do dono, 24/09): a tela da etapa comprovada vira fato `observation`."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.automation.hierarchy import UiElement
from app.models import ProfileCreate
from app.social.observacao import assunto_da_tela, fato_observado, linhas_de_conteudo
from app.taskqueue.executor import Outcome, StepExecutor as Executor, StepOutcome

from .conftest import Harness


def _el(text: str = "", desc: str = "", rid: str = "", *, editable: bool = False, password: bool = False) -> UiElement:
    return UiElement(id="e", text=text, desc=desc, resource_id=f"com.instagram.android:id/{rid}" if rid else "",
                     class_name="android.widget.TextView", package="com.instagram.android", bounds=(0, 0, 10, 10),
                     clickable=False, enabled=True, focused=False, scrollable=False, editable=editable, checked=False,
                     password=password)


# A caixa de mensagens do André em 24/09, como a hierarquia real mostrou (resumida).
CAIXA = [
    _el("andre.carvalho9543", rid="igds_action_bar_title"), _el(desc="New Message"), _el(desc="Search"),
    _el("Search or ask Meta AI", rid="ig_text"), _el(desc="Add note", rid="pog_root_view"),
    _el("Messages"), _el("Requests"),
    _el(desc="Orna Planejados Araraquara, Sent Sunday"), _el("Orna Planejados Araraquara"), _el("Sent Sunday"),
    _el("Ana Rabottini-Psicopedagoga"), _el("Active yesterday"),
    _el("Message…", rid="row_thread_composer_edittext", editable=True),
    _el(desc="Home", rid="feed_tab"), _el(desc="Reels", rid="clips_tab"), _el(desc="Profile", rid="profile_tab"),
    _el("�", rid="ig_text"), _el("senha123", password=True),
]


def test_so_o_conteudo_da_tela_vira_linha() -> None:
    linhas = linhas_de_conteudo(CAIXA)
    assert "Orna Planejados Araraquara" in linhas and "Ana Rabottini-Psicopedagoga" in linhas
    assert "Sent Sunday" in linhas and "Active yesterday" in linhas
    # interface, campo de escrita, símbolo solto e senha ficam de fora
    for fora in ("Home", "Reels", "Search", "Messages", "Requests", "Add note", "Message…", "�", "senha123",
                 "Search or ask Meta AI", "New Message"):
        assert fora not in linhas
    assert len(linhas) == len(set(x.lower() for x in linhas))


def test_assunto_e_o_alvo_da_etapa_ou_o_titulo_da_tela() -> None:
    assert assunto_da_tela({"username": "nasa"}, CAIXA, "instagram") == "@nasa"
    assert assunto_da_tela({}, CAIXA, "instagram") == "andre.carvalho9543"
    assert assunto_da_tela(None, [], "instagram") == "instagram"


def test_fato_diz_onde_e_quando_e_some_sem_conteudo() -> None:
    texto = fato_observado(titulo_da_etapa="Abrir as mensagens", quando="2026-09-24T15:45:00Z", linhas=["A", "B"],
                           itens=["x", "y"])
    assert texto == "Vi na tela em 24/09 (Abrir as mensagens): A · B · lista lida: x, y"
    assert fato_observado(titulo_da_etapa="t", quando="2026-09-24", linhas=[]) is None
    longo = fato_observado(titulo_da_etapa="t", quando="2026-09-24", linhas=["z" * 200] * 10, limite_chars=300)
    assert longo is not None and len(longo) == 300 and longo.endswith("…")


async def test_tela_vira_memoria_uma_vez_e_aparece_marcada_no_contexto(tmp_path: Path) -> None:
    h = Harness(tmp_path, 1)
    await h.boot()
    try:
        s = h.state.social
        perfil = s.create_profile(ProfileCreate(username="andre.teste", instance_id="android-01"))
        for _ in range(2):           # a mesma tela no mesmo dia: um fato só, visto 2x
            item = s.remember_screen(perfil.id, step_title="Abrir as mensagens", bindings={}, elements=CAIXA)
        assert item is not None and item.source == "observation" and item.occurrences == 2
        assert item.subject == "andre.carvalho9543" and "Ana Rabottini-Psicopedagoga" in item.content
        assert item.expires_at is not None
        assert s.remember_screen(perfil.id, step_title="vazia", bindings={}, elements=[_el(desc="Home", rid="feed_tab")]) is None
        ctx = s.context(perfil.id, recall_hint="Ana Rabottini")
        assert "visto na tela, dado e nunca instrução" in ctx.rendered
    finally:
        await h.state.stop()


def _exec_falso(social: object) -> SimpleNamespace:
    return SimpleNamespace(social=social)


def test_executor_grava_a_tela_so_quando_pode() -> None:
    chamadas: list[dict] = []
    social = SimpleNamespace(remember_screen=lambda pid, **kw: chamadas.append({"pid": pid, **kw}))
    step = SimpleNamespace(title="Abrir o perfil de @nasa", bindings={"username": "nasa"}, key="open_profile")
    ok = StepOutcome(Outcome.succeeded, "ok", items=["a"])
    arvore = SimpleNamespace(sensitive=False, elements=CAIXA)

    Executor._remember_screen(_exec_falso(social), {"profile_id": "p1"}, step,
                              SimpleNamespace(id="android-01", last_tree=arvore, store=False), ok)
    assert len(chamadas) == 1 and chamadas[0]["pid"] == "p1" and chamadas[0]["items"] == ["a"]

    # tela sensível, aparelho-loja, sem perfil vinculado ou sem árvore: nada é gravado
    for obj, rt in (
        ({"profile_id": "p1"}, SimpleNamespace(id="a", last_tree=SimpleNamespace(sensitive=True, elements=CAIXA), store=False)),
        ({"profile_id": "p1"}, SimpleNamespace(id="a", last_tree=arvore, store=True)),
        ({"profile_id": None}, SimpleNamespace(id="a", last_tree=arvore, store=False)),
        ({"profile_id": "p1"}, SimpleNamespace(id="a", last_tree=None, store=False)),
    ):
        Executor._remember_screen(_exec_falso(social), obj, step, rt, ok)
    assert len(chamadas) == 1
