"""Seleção de chunks da etapa B (J13, ADR-063): rodízio entre os candidatos, janelas escolhidas pela pergunta, 1º candidato em dobro.

`simulated` (arquivos sintéticos). A medição que escolheu esta regra, nas 30 perguntas do golden do poetry, está em
`docs/dominios/context-retrieval.md` e se reproduz de graça com `scripts/context-retrieval-chunk-eval.py`.
"""
from __future__ import annotations

from pathlib import Path

from app.modules.context_retrieval.infrastructure.chunker import Chunker, termos_da_pergunta
from app.modules.context_retrieval.infrastructure.workspace import Workspace


def _repo(tmp_path: Path, arquivos: dict[str, int], *, marca: dict[str, tuple[int, str]] | None = None) -> Chunker:
    """Cada arquivo tem N linhas `x`; `marca` põe um texto numa linha (para a pontuação lexical achar a janela dela)."""
    raiz = tmp_path / "r"
    raiz.mkdir()
    for nome, n in arquivos.items():
        linhas = [f"x{i}" for i in range(1, n + 1)]
        if marca and nome in marca:
            linhas[marca[nome][0] - 1] = marca[nome][1]
        (raiz / nome).write_text("\n".join(linhas) + "\n", encoding="utf-8")
    return Chunker(Workspace(raiz), chunk_lines=10, overlap=0)


def _por_arquivo(chunks) -> dict[str, int]:
    saida: dict[str, int] = {}
    for c in chunks:
        saida[c.path] = saida.get(c.path, 0) + 1
    return saida


def test_sem_query_o_corte_e_o_original_do_inicio_arquivo_por_arquivo(tmp_path: Path) -> None:
    c = _repo(tmp_path, {"a.py": 100, "b.py": 100})
    chunks = c.chunks_for(["a.py", "b.py"], max_chunks=4, max_bytes=100_000)
    assert [(x.path, x.start_line) for x in chunks] == [("a.py", 1), ("a.py", 11), ("a.py", 21), ("a.py", 31)]


def test_com_query_o_teto_e_repartido_e_o_primeiro_leva_o_dobro(tmp_path: Path) -> None:
    c = _repo(tmp_path, {"a.py": 100, "b.py": 100, "c.py": 100})
    chunks = c.chunks_for(["a.py", "b.py", "c.py"], max_chunks=8, max_bytes=100_000, query="zzz inexistente")
    # voltas: a,a,b,c | a,a,b,c  -> a:4, b:2, c:2
    assert _por_arquivo(chunks) == {"a.py": 4, "b.py": 2, "c.py": 2}


def test_o_arquivo_que_o_modelo_poe_em_segundo_nao_fica_sem_chunk(tmp_path: Path) -> None:
    """O H25 do J12: com o corte do início, o 1º candidato esgotava o teto e o 2º não recebia nada."""
    c = _repo(tmp_path, {"a.py": 1000, "b.py": 1000})
    antes = c.chunks_for(["a.py", "b.py"], max_chunks=16, max_bytes=1_000_000)
    depois = c.chunks_for(["a.py", "b.py"], max_chunks=16, max_bytes=1_000_000, query="qualquer coisa")
    assert "b.py" not in _por_arquivo(antes) and _por_arquivo(depois)["b.py"] >= 5


def test_a_janela_com_o_termo_da_pergunta_vem_primeiro_mesmo_no_fim_do_arquivo(tmp_path: Path) -> None:
    c = _repo(tmp_path, {"a.py": 1000}, marca={"a.py": (487, "class LazyWheelOverHTTP:")})
    chunks = c.chunks_for(["a.py"], max_chunks=3, max_bytes=100_000, query="Where is `LazyWheelOverHTTP` defined?")
    assert any(x.start_line <= 487 <= x.end_line for x in chunks)
    original = c.chunks_for(["a.py"], max_chunks=3, max_bytes=100_000)
    assert not any(x.start_line <= 487 <= x.end_line for x in original)        # o corte antigo nunca chegaria lá


def test_teto_de_bytes_continua_duro_e_a_selecao_e_deterministica(tmp_path: Path) -> None:
    c = _repo(tmp_path, {"a.py": 200, "b.py": 200})
    a = c.chunks_for(["a.py", "b.py"], max_chunks=99, max_bytes=120, query="x1")
    b = c.chunks_for(["a.py", "b.py"], max_chunks=99, max_bytes=120, query="x1")
    assert a == b and sum(len(x.text.encode()) for x in a) <= 120 and a


def test_repetido_nao_duplica_e_arquivo_inexistente_nao_gera_chunk(tmp_path: Path) -> None:
    c = _repo(tmp_path, {"a.py": 30})
    chunks = c.chunks_for(["a.py", "a.py", "nao/existe.py"], max_chunks=99, max_bytes=100_000, query="x")
    assert set(_por_arquivo(chunks)) == {"a.py"} and len(chunks) == 3


def test_termos_quebram_snake_e_camel_e_tiram_palavras_vazias() -> None:
    t = termos_da_pergunta("Where is `LazyWheelOverHTTP` defined in the solve_version?")
    assert "lazywheeloverhttp" in t and "lazy" in t and "wheel" in t and "solve" in t and "version" in t
    assert "where" not in t and "the" not in t
