"""Funil de receitas medido (frente F3, adendo v0.20 C5): consulta → reprodução → retorno à IA.

Sem isto, "quanto a receita economiza" só saía por dedução de `steps.driven_by`: não havia contagem de etapa
elegível, de receita encontrada nem de por que a IA voltou. Os contadores ficam em `app.metricas` (agregado, rótulo
de conjunto fechado), nunca com id de execução ou texto de tela.
"""
from __future__ import annotations

from app.metricas import metricas
from app.taskqueue.recipes import RecipeStore, contar_retorno_ia, motivo_do_retorno

from .conftest import Harness

PKG = "com.pocqa.messenger"


def _acoes() -> list[dict[str, object]]:
    return [{"tool": "tap", "commit": False, "why": "abrir", "args": {},
             "selectors": [{"kind": "rid", "rid": "app:id/conversation_name"}]}]


async def test_consulta_separa_encontrada_ausente_e_quarentena_sem_contar_o_proprio_save(harness: Harness) -> None:
    metricas.limpar()
    store = RecipeStore(harness.state.db)                                   # type: ignore[union-attr]
    chave = {"signature": "", "variant": "en-US/xhdpi"}

    rid = store.save(package=PKG, app_version="1.0(1)", step_hash="h-abrir", step_key="abrir", actions=_acoes(),
                     learned_from="s1", **chave)
    assert rid
    # gravar pergunta "já existe ativa?" — isso NÃO é consulta de etapa e não pode inflar o funil
    assert metricas.total("receita.consulta") == 0
    assert store.save(package=PKG, app_version="1.0(1)", step_hash="h-abrir", step_key="abrir", actions=_acoes(),
                      learned_from="s2", **chave) is None
    assert metricas.total("receita.consulta") == 0

    assert store.find(PKG, "1.0(1)", "h-abrir", **chave) is not None
    assert store.find(PKG, "1.0(1)", "h-outra", **chave) is None
    assert store.find(PKG, "2.0(7)", "h-abrir", **chave) is None             # versão nova: nunca aprendida
    assert metricas.valor("receita.consulta", resultado="encontrada") == 1
    assert metricas.valor("receita.consulta", resultado="ausente") == 2

    # chave incompleta: a etapa não era elegível — não é "ausente"
    assert store.find(PKG, None, "h-abrir", **chave) is None
    assert store.find(None, "1.0(1)", "h-abrir", **chave) is None
    assert metricas.total("receita.consulta") == 3

    # reprodução: ok zera a sequência; três divergências seguidas → quarentena
    assert store.result(rid, True) is False
    assert [store.result(rid, False) for _ in range(3)] == [False, False, True]
    assert metricas.valor("receita.reproducao", resultado="ok") == 1
    assert metricas.valor("receita.reproducao", resultado="divergiu") == 3

    # em quarentena: a busca devolve nada, mas o funil diz POR QUE (aprendida e posta de lado ≠ nunca aprendida)
    assert store.find(PKG, "1.0(1)", "h-abrir", **chave) is None
    assert metricas.valor("receita.consulta", resultado="quarentena") == 1
    assert metricas.valor("receita.consulta", resultado="ausente") == 2


def test_motivo_do_retorno_e_vocabulario_fechado() -> None:
    """O texto da divergência é livre (inclui nome de parâmetro, seletor, erro de validação); o rótulo não pode ser."""
    casos = {
        "parâmetro ausente nesta execução: recipient": "parametro_ausente",
        "ação 2 (tap): alvo ausente ou ambíguo nesta tela": "alvo_ausente",
        "ações reproduzidas, mas a pós-condição não apareceu": "pos_condicao",
        "ação da receita inválida: text: parâmetro ausente": "acao_invalida",   # prefixo do executor vence
        "alvo do efeito externo: o efeito desta etapa é disparado por 'id=send'; o elemento escolhido não é ele":
            "alvo_do_efeito",
        'guarda do efeito externo: antes do efeito, estes textos precisam estar visíveis e não estão: "QA-001"':
            "guarda_do_efeito",
        "qualquer outra coisa": "outro",
        "": "outro",
        None: "outro",
    }
    for texto, esperado in casos.items():
        assert motivo_do_retorno(texto) == esperado, texto
    metricas.limpar()
    contar_retorno_ia("parâmetro ausente nesta execução: perfil")
    contar_retorno_ia("texto livre com @alguem e android-07")
    assert metricas.valor("receita.retorno_ia", motivo="parametro_ausente") == 1
    assert metricas.valor("receita.retorno_ia", motivo="outro") == 1
    series = [c["rotulos"] for c in metricas.snapshot()["contadores"] if c["nome"] == "receita.retorno_ia"]
    assert all(set(r) == {"motivo"} for r in series) and not any("android" in str(r) for r in series)


async def test_execucao_real_do_harness_passa_pelo_funil(harness: Harness) -> None:
    """Aparelho 1 aprende (consultas ausentes), aparelho 2 repete (encontradas, reproduções ok) — medido pelo
    caminho que o executor já chama, sem instrumentar o executor."""
    harness.pular_o_tempo()   # T.2: relógio virtual; o que se confere não depende de tempo real
    harness.cfg.file.ai.recipes = "replay"
    metricas.limpar()
    assert (await harness.wait_run(harness.run(["android-01"]).id)).status == "completed"
    ausentes = metricas.valor("receita.consulta", resultado="ausente")
    assert ausentes >= 3 and metricas.valor("receita.consulta", resultado="encontrada") == 0
    assert metricas.total("receita.reproducao") == 0

    assert (await harness.wait_run(harness.run(["android-02"]).id)).status == "completed"
    encontradas = metricas.valor("receita.consulta", resultado="encontrada")
    assert encontradas >= 3
    assert metricas.valor("receita.reproducao", resultado="ok") >= 3
    assert metricas.valor("receita.reproducao", resultado="divergiu") == 0
    db = harness.state.db                                                   # type: ignore[union-attr]
    por_receita = db.scalar("SELECT COUNT(*) FROM steps WHERE driven_by='recipe'")
    assert metricas.valor("receita.reproducao", resultado="ok") == por_receita   # a métrica bate com o banco
