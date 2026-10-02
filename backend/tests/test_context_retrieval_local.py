"""Infraestrutura local do retrieval de contexto: Workspace, léxico, BM25, mapa do repositório e chunker.

Tudo em `tmp_path`, sem rede e sem depender do cwd. O `rg` é opcional: o fallback Python é testado sempre e só se
compara com o `rg` quando ele existe. Os "segredos" das fixtures são valores obviamente falsos montados em runtime.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.modules.context_retrieval.domain.model import RETRIEVAL_VERSION, ContextSelection, RetrievalRequest
from app.modules.context_retrieval.domain.ports import ChunkSource, ContextRetriever, MapSource
from app.modules.context_retrieval.infrastructure import lexical as lexical_mod
from app.modules.context_retrieval.infrastructure.bm25 import BM25Retriever
from app.modules.context_retrieval.infrastructure.chunker import Chunker
from app.modules.context_retrieval.infrastructure.lexical import LexicalRetriever
from app.modules.context_retrieval.infrastructure.repomap import RepoMapProvider, build_repo_map
from app.modules.context_retrieval.infrastructure.workspace import Workspace

TEM_GIT = shutil.which("git") is not None
TEM_RG = shutil.which("rg") is not None

SCHEDULER = '''"""Escalonador que distribui tarefas entre os aparelhos do parque.

Segunda linha do docstring que não pode aparecer no resumo.
"""
from __future__ import annotations


def pick_device_for_task(task, devices):
    """Escolhe o aparelho menos ocupado."""
    corpo_secreto_do_escalonador = sorted(devices)
    return corpo_secreto_do_escalonador[0]


class Scheduler:
    """Docstring de classe.
na coluna zero de propósito
"""

    def enqueue(self, task):
        return task

    async def drain(self):
        return None


def release_device(device):
    return device
'''

BILLING = '''"""Calcula a fatura mensal da assinatura."""


def compute_invoice_total(items):
    """Soma os itens e cobra a assinatura mensal."""
    return sum(items)
'''

GUIDE = "# Guia de uso\n\nTexto de introdução.\n\n```bash\n# comentário dentro de bloco\n```\n\n## Instalação do agente\n\nPasso a passo.\n"

APP_TS = ("export interface Props { id: string }\nexport type Mode = 'a' | 'b'\n"
          "export function renderDashboard(props: Props) {\n  const interno = 1\n  return interno\n}\n"
          "export const API_BASE = '/api'\nclass Painel {}\n")

SQL = "CREATE TABLE devices (\n  id INTEGER PRIMARY KEY\n);\nCREATE INDEX idx_devices_id ON devices(id);\n"

PS1 = "function Start-Farm {\n  Write-Host 'x'\n}\n"


def _montar(raiz: Path) -> Path:
    arquivos: dict[str, str | bytes] = {
        "backend/app/scheduler.py": SCHEDULER,
        "backend/app/billing.py": BILLING,
        "docs/guide.md": GUIDE,
        "frontend/src/app.ts": APP_TS,
        "db/001_init.sql": SQL,
        "scripts/run.ps1": PS1,
        ".env": "FAKE_VAR=valor-falso\n",
        "data/x.json": '{"a": 1}\n',
        "config/config.yaml": "chave: pick_device_for_task\n",
        "certs/server.pem": "FAKE-PEM pick_device_for_task\n",
        "big.txt": "pick_device_for_task\n" * 40_000,  # ~800 KB
        "assets/blob.dat": b"\x00\x01\x02pick_device_for_task",
        "assets/logo.png": b"\x89PNG\r\n\x1a\n",
        "bin/semext": b"abc\x00pick_device_for_task",
    }
    for rel, conteudo in arquivos.items():
        alvo = raiz / rel
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_bytes(conteudo if isinstance(conteudo, bytes) else conteudo.encode("utf-8"))
    return raiz


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return _montar(tmp_path / "repo")


@pytest.fixture()
def ws(repo: Path) -> Workspace:
    return Workspace(repo)


def _req(query: str, root: Path, **kw: object) -> RetrievalRequest:
    return RetrievalRequest(query=query, root=root, revision="x", **kw)  # type: ignore[arg-type]


def _git(raiz: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.email=t@t.invalid", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
                   cwd=raiz, check=True, capture_output=True)


# ====================================================================== Workspace
def test_universo_exclui_sensivel_binario_e_grande(ws: Workspace) -> None:
    arquivos = ws.files()
    assert arquivos == sorted(arquivos)
    assert "backend/app/scheduler.py" in arquivos and "docs/guide.md" in arquivos
    for fora in (".env", "data/x.json", "config/config.yaml", "certs/server.pem", "big.txt", "assets/blob.dat",
                 "assets/logo.png", "bin/semext"):
        assert fora not in arquivos, fora
    # e o que não está no universo não se lê, mesmo pedindo pelo nome
    assert ws.read_text(".env") is None and ws.read_text("big.txt") is None and ws.read_text("bin/semext") is None


def test_max_file_bytes_configuravel(repo: Path) -> None:
    assert "docs/guide.md" not in Workspace(repo, max_file_bytes=10).files()


def test_path_traversal_e_absoluto_recusados(ws: Workspace, repo: Path) -> None:
    segredo = repo.parent / "fora.txt"
    segredo.write_text("fora da raiz", encoding="utf-8")
    assert ws.read_text("../fora.txt") is None
    assert ws.read_text("backend/../../fora.txt") is None
    assert ws.read_text(str(segredo)) is None
    assert ws.read_text(chr(92).join(["..", "fora.txt"])) is None
    assert ws.read_text("/etc/passwd") is None
    assert ws.read_text("C:/Windows/win.ini") is None
    assert ws.read_text("nao/existe.py") is None
    assert ws.read_text("backend/app/billing.py") == BILLING  # o caminho legítimo segue funcionando


def test_read_text_normaliza_crlf(repo: Path) -> None:
    (repo / "docs" / "crlf.md").write_bytes(b"a\r\nb\r\n")
    assert Workspace(repo).read_text("docs/crlf.md") == "a\nb\n"


def test_revision_estavel_muda_com_edicao_e_arquivo_novo(ws: Workspace, repo: Path) -> None:
    r0 = ws.revision()
    assert ws.revision() == r0
    assert Workspace(repo).revision() == r0  # instância nova, mesma árvore
    (repo / "backend/app/billing.py").write_text(BILLING + "# edição\n", encoding="utf-8")
    r1 = ws.revision()
    assert r1 != r0
    (repo / "docs/novo.md").write_text("# novo\n", encoding="utf-8")
    r2 = ws.revision()
    assert r2 not in (r0, r1)
    assert "docs/novo.md" in ws.files()  # a mudança de revisão invalidou o cache de arquivos


def test_arquivo_ignorado_nao_entra_e_nao_move_a_revisao(ws: Workspace, repo: Path) -> None:
    r0 = ws.revision()
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__/x.pyc").write_bytes(b"\x00")
    (repo / "node_modules/pkg").mkdir(parents=True)
    (repo / "node_modules/pkg/i.js").write_text("pick_device_for_task", encoding="utf-8")
    assert ws.revision() == r0
    assert all(not f.startswith(("node_modules/", "__pycache__/")) for f in ws.files())


def test_refresh_refaz_o_universo(ws: Workspace, repo: Path) -> None:
    assert "docs/outro.md" not in ws.files()
    (repo / "docs/outro.md").write_text("# outro\n", encoding="utf-8")
    assert "docs/outro.md" not in ws.files()  # em cache
    ws.refresh()
    assert "docs/outro.md" in ws.files()


def test_language_of_e_size_of(ws: Workspace) -> None:
    assert ws.language_of("a/b.py") == "python" and ws.language_of("x.tsx") == "tsx"
    assert ws.language_of("x.desconhecida") == "text" and ws.language_of("Dockerfile") == "dockerfile"
    assert ws.size_of("backend/app/billing.py") == len(BILLING.encode()) and ws.size_of("nao-existe") is None


@pytest.mark.skipif(not TEM_GIT, reason="git ausente")
def test_workspace_em_repo_git(tmp_path: Path) -> None:
    raiz = _montar(tmp_path / "gitrepo")
    (raiz / ".gitignore").write_text("ignorado.txt\n", encoding="utf-8")
    (raiz / "ignorado.txt").write_text("pick_device_for_task\n", encoding="utf-8")
    _git(raiz, "init", "-q")
    _git(raiz, "add", "-A")
    _git(raiz, "commit", "-q", "-m", "inicial")
    w = Workspace(raiz)
    arq = w.files()
    assert "ignorado.txt" not in arq and ".env" not in arq and "backend/app/scheduler.py" in arq
    limpa = w.revision()
    assert len(limpa) == 40 and "+" not in limpa  # árvore limpa = hash de árvore puro
    assert w.revision() == limpa
    # edição de arquivo versionado: suja, com sufixo, e muda a cada nova edição
    (raiz / "backend/app/billing.py").write_text(BILLING + "# a\n", encoding="utf-8")
    suja = w.revision()
    assert suja.startswith(limpa + "+") and suja != limpa
    (raiz / "backend/app/billing.py").write_text(BILLING + "# a mais longa\n", encoding="utf-8")
    assert w.revision() not in (limpa, suja)
    # arquivo novo não versionado entra no universo
    (raiz / "docs/novo.md").write_text("# novo\n", encoding="utf-8")
    w.revision()
    assert "docs/novo.md" in w.files()
    # versionado e apagado na árvore de trabalho sai do universo
    (raiz / "docs/guide.md").unlink()
    w.revision()
    assert "docs/guide.md" not in w.files()


@pytest.mark.skipif(not TEM_GIT, reason="git ausente")
def test_pasta_dentro_de_outro_repo_nao_usa_o_git_do_pai(tmp_path: Path) -> None:
    pai = tmp_path / "pai"
    pai.mkdir()
    _git(pai, "init", "-q")
    filho = _montar(pai / "filho")
    w = Workspace(filho)
    assert "backend/app/billing.py" in w.files()  # caminhos relativos ao filho, não ao repo pai
    assert w.revision().startswith("fs-")


# ====================================================================== Léxico
def test_lexical_identificador_exato_acha_o_arquivo(ws: Workspace, repo: Path) -> None:
    r = LexicalRetriever(ws, use_ripgrep=False)
    sel = r.retrieve(_req("onde fica `pick_device_for_task`?", repo))
    assert isinstance(r, ContextRetriever) and r.name == "lexical"
    assert [h.path for h in sel.selected_files] == ["backend/app/scheduler.py"]
    hit = sel.selected_files[0]
    assert hit.source == "lexical" and hit.matched_terms == 1 and hit.score == 1000 + 1
    assert sel.source == "lexical" and sel.metadata == {"terms": 1, "files_considered": len(ws.files()), "engine": "python"}
    reg = sel.selected_regions[0]
    assert reg.path == "backend/app/scheduler.py" and reg.start_line <= 8 <= reg.end_line
    assert "pick_device_for_task" in (reg.text or "")
    # sensível, binário e grande NÃO aparecem mesmo contendo o termo
    assert all(h.path not in ("config/config.yaml", "certs/server.pem", "big.txt", "assets/blob.dat")
               for h in sel.selected_files)


def test_lexical_ranking_distintos_depois_ocorrencias_depois_caminho(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    (raiz / "src").mkdir(parents=True)
    (raiz / "src/a_dois.py").write_text("alfa_x = 1\nbeta_y = 2\n", encoding="utf-8")
    (raiz / "src/b_um_muitos.py").write_text("alfa_x\nalfa_x\nalfa_x\n", encoding="utf-8")
    (raiz / "src/c_um.py").write_text("alfa_x\n", encoding="utf-8")
    (raiz / "src/a_um.py").write_text("alfa_x\n", encoding="utf-8")
    sel = LexicalRetriever(Workspace(raiz), use_ripgrep=False).retrieve(_req("`alfa_x` e `beta_y`", raiz))
    assert [h.path for h in sel.selected_files] == ["src/a_dois.py", "src/b_um_muitos.py", "src/a_um.py", "src/c_um.py"]
    assert [h.score for h in sel.selected_files] == [2002, 1003, 1001, 1001]


def test_lexical_scope_e_top_k(ws: Workspace, repo: Path) -> None:
    (repo / "docs/mais.md").write_text("pick_device_for_task em docs\n", encoding="utf-8")
    ws.revision()
    r = LexicalRetriever(ws, use_ripgrep=False)
    todos = [h.path for h in r.retrieve(_req("`pick_device_for_task`", repo)).selected_files]
    assert set(todos) == {"backend/app/scheduler.py", "docs/mais.md"}
    so_docs = r.retrieve(_req("`pick_device_for_task`", repo, scope=("docs/",)))
    assert [h.path for h in so_docs.selected_files] == ["docs/mais.md"]
    assert [x.path for x in so_docs.selected_regions] == ["docs/mais.md"]
    assert len(r.retrieve(_req("`pick_device_for_task`", repo, top_k=1)).selected_files) == 1
    assert r.retrieve(_req("`pick_device_for_task`", repo, scope=("nao/existe",))).selected_files == ()


def test_lexical_sem_termos_e_sem_casamento(ws: Workspace, repo: Path) -> None:
    r = LexicalRetriever(ws, use_ripgrep=False)
    vazio = r.retrieve(_req("o que é isso?", repo))  # só stopwords e palavras curtas
    assert vazio.selected_files == () and vazio.selected_regions == ()
    assert vazio.warnings == ("no_terms",) and vazio.metadata["terms"] == 0 and vazio.source == "lexical"
    nada = r.retrieve(_req("`simbolo_que_nao_existe_em_lugar_nenhum`", repo))
    assert nada.selected_files == () and nada.warnings == ("no_matches",)


def test_lexical_palavras_sem_identificador_com_e_sem_acento(ws: Workspace, repo: Path) -> None:
    r = LexicalRetriever(ws, use_ripgrep=False)
    for pergunta in ("como fazer a instalação do agente", "como fazer a instalacao do agente"):
        sel = r.retrieve(_req(pergunta, repo))
        assert sel.selected_files and sel.selected_files[0].path == "docs/guide.md", pergunta
    assert sel.metadata["terms"] >= 2


def test_lexical_nao_vaza_termos_na_metadata(ws: Workspace, repo: Path) -> None:
    sel = LexicalRetriever(ws, use_ripgrep=False).retrieve(_req("`pick_device_for_task`", repo))
    assert "pick_device_for_task" not in json.dumps(sel.metadata)
    assert set(sel.metadata) == {"terms", "files_considered", "engine"}


def test_lexical_janelas_mescladas_e_limitadas(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    raiz.mkdir()
    linhas = [f"linha {i}" for i in range(1, 61)]
    for i in (10, 12, 14, 40, 55):
        linhas[i - 1] = "marca_unica aqui"
    (raiz / "f.txt").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    sel = LexicalRetriever(Workspace(raiz), use_ripgrep=False, window_lines=7).retrieve(_req("`marca_unica`", raiz))
    spans = [(r.start_line, r.end_line) for r in sel.selected_regions]
    # 10 e 12 cabem na janela de 10; 14 sobrepõe e funde (7..17); 40 e 55 ficam à parte (3 janelas no máximo)
    assert spans == [(7, 17), (37, 43), (52, 58)]
    assert sel.selected_files[0].regions == sel.selected_regions
    limitado = LexicalRetriever(Workspace(raiz), use_ripgrep=False, window_lines=7,
                                max_windows_per_file=2).retrieve(_req("`marca_unica`", raiz))
    assert [(r.start_line, r.end_line) for r in limitado.selected_regions] == [(7, 17), (37, 43)]


def test_lexical_fallback_quando_rg_falha(ws: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lexical_mod.shutil, "which", lambda _n: "rg-fantasma")
    r = LexicalRetriever(ws)
    monkeypatch.setattr(r, "_rg_linhas", lambda *_a, **_k: None)  # rg quebrou: o resultado tem de vir do Python
    sel = r.retrieve(_req("`pick_device_for_task`", repo))
    assert [h.path for h in sel.selected_files] == ["backend/app/scheduler.py"]
    assert sel.metadata["engine"] == "python"


def test_lexical_sem_rg_no_path_usa_python(ws: Workspace, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lexical_mod.shutil, "which", lambda _n: None)
    sel = LexicalRetriever(ws).retrieve(_req("`pick_device_for_task`", repo))
    assert sel.metadata["engine"] == "python" and sel.selected_files


@pytest.mark.skipif(not TEM_RG, reason="rg ausente")
@pytest.mark.parametrize("pergunta", ["`pick_device_for_task`", "`Scheduler` e `release_device`",
                                      "fatura mensal assinatura", "`nao_existe_xyz`"])
def test_lexical_python_igual_ao_rg(ws: Workspace, repo: Path, pergunta: str) -> None:
    py = LexicalRetriever(ws, use_ripgrep=False).retrieve(_req(pergunta, repo))
    rg = LexicalRetriever(ws, use_ripgrep=True).retrieve(_req(pergunta, repo))
    assert rg.metadata["engine"] == "rg" and py.metadata["engine"] == "python"
    assert [(h.path, h.score, h.matched_terms) for h in py.selected_files] == \
           [(h.path, h.score, h.matched_terms) for h in rg.selected_files]
    assert [x.key() for x in py.selected_regions] == [x.key() for x in rg.selected_regions]


@pytest.mark.skipif(not TEM_RG, reason="rg ausente")
def test_rg_so_varre_o_universo_do_workspace(ws: Workspace, repo: Path) -> None:
    sel = LexicalRetriever(ws, use_ripgrep=True).retrieve(_req("`pick_device_for_task`", repo))
    assert [h.path for h in sel.selected_files] == ["backend/app/scheduler.py"]


@pytest.mark.skipif(not TEM_RG, reason="rg ausente")
def test_rg_em_lotes_com_muitos_arquivos(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    (raiz / "d").mkdir(parents=True)
    for i in range(900):  # caminhos longos o bastante para passar de um lote só na linha de comando
        (raiz / "d" / f"arquivo_com_nome_bem_comprido_para_estourar_o_lote_{i:04d}.txt").write_text(
            "agulha_no_palheiro\n" if i in (3, 899) else "nada\n", encoding="utf-8")
    w = Workspace(raiz)
    py = LexicalRetriever(w, use_ripgrep=False).retrieve(_req("`agulha_no_palheiro`", raiz))
    rg = LexicalRetriever(w, use_ripgrep=True).retrieve(_req("`agulha_no_palheiro`", raiz))
    assert len(rg.selected_files) == 2 and rg.metadata["engine"] == "rg"
    assert [h.path for h in rg.selected_files] == [h.path for h in py.selected_files]


# ====================================================================== BM25
def test_bm25_consulta_semantica_sem_identificador(ws: Workspace, repo: Path) -> None:
    r = BM25Retriever(ws)
    assert isinstance(r, ContextRetriever) and r.name == "bm25"
    sel = r.retrieve(_req("como cobrar a fatura mensal da assinatura", repo))
    assert sel.selected_files[0].path == "backend/app/billing.py"
    assert sel.source == "bm25" and sel.selected_files[0].source == "bm25" and sel.selected_files[0].score > 0
    assert sel.selected_files[0].matched_terms >= 3
    reg = sel.selected_regions[0]
    assert reg.path == "backend/app/billing.py" and reg.start_line == 1
    assert sel.metadata["files_considered"] == len(ws.files()) and "tokens" in sel.metadata


def test_bm25_acha_por_distribuicao_e_por_caminho_e_sem_acento(ws: Workspace, repo: Path) -> None:
    r = BM25Retriever(ws)
    assert r.retrieve(_req("distribuir tarefas entre aparelhos escalonador", repo)).selected_files[0].path \
        == "backend/app/scheduler.py"
    # o nome do arquivo conta: "billing" só aparece no caminho de uma das pastas
    assert r.retrieve(_req("billing", repo)).selected_files[0].path == "backend/app/billing.py"
    assert r.retrieve(_req("instalacao agente passo", repo)).selected_files[0].path == "docs/guide.md"


def test_bm25_quebra_camel_e_snake_mantendo_composto(ws: Workspace, repo: Path) -> None:
    r = BM25Retriever(ws)
    # `renderDashboard` e `release_device` casam pelas partes ("dashboard", "device") e pelo composto
    assert r.retrieve(_req("dashboard", repo)).selected_files[0].path == "frontend/src/app.ts"
    assert r.retrieve(_req("release device", repo)).selected_files[0].path == "backend/app/scheduler.py"
    assert r.retrieve(_req("renderDashboard", repo)).selected_files[0].path == "frontend/src/app.ts"


def test_bm25_nao_indexa_sensivel_nem_binario(ws: Workspace, repo: Path) -> None:
    sel = BM25Retriever(ws).retrieve(_req("pick_device_for_task pick device task", repo, top_k=20))
    assert all(h.path not in ("config/config.yaml", "certs/server.pem", "big.txt", "assets/blob.dat", ".env")
               for h in sel.selected_files)


def test_bm25_indice_reaproveitado_e_refeito_apos_edicao(ws: Workspace, repo: Path) -> None:
    r = BM25Retriever(ws)
    s1 = r.retrieve(_req("fatura mensal", repo))
    s2 = r.retrieve(_req("escalonador aparelhos", repo))
    assert r.index_builds == 1 and s1.metadata["index_reused"] is False and s2.metadata["index_reused"] is True
    (repo / "docs/faturamento.md").write_text("# Fatura\n\nA fatura mensal do cliente é cobrada aqui, fatura fatura.\n",
                                              encoding="utf-8")
    s3 = r.retrieve(_req("fatura mensal", repo))
    assert r.index_builds == 2 and s3.metadata["index_reused"] is False
    assert "docs/faturamento.md" in [h.path for h in s3.selected_files]  # o arquivo novo entrou no índice
    r.retrieve(_req("fatura mensal", repo))
    assert r.index_builds == 2


def test_bm25_deterministico_e_desempate_por_caminho(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    raiz.mkdir()
    for nome in ("z.txt", "a.txt", "m.txt"):
        (raiz / nome).write_text("planeta gigante gasoso\n", encoding="utf-8")
    w = Workspace(raiz)
    s1 = BM25Retriever(w).retrieve(_req("planeta gigante", raiz, top_k=3))
    s2 = BM25Retriever(Workspace(raiz)).retrieve(_req("planeta gigante", raiz, top_k=3))
    assert [h.path for h in s1.selected_files] == ["a.txt", "m.txt", "z.txt"]
    assert [(h.path, h.score) for h in s1.selected_files] == [(h.path, h.score) for h in s2.selected_files]
    assert [x.key() for x in s1.selected_regions] == [x.key() for x in s2.selected_regions]


def test_bm25_scope_top_k_e_vazios(ws: Workspace, repo: Path) -> None:
    r = BM25Retriever(ws)
    assert [h.path for h in r.retrieve(_req("aparelho", repo, scope=("docs/",), top_k=5)).selected_files
            if not h.path.startswith("docs/")] == []
    assert len(r.retrieve(_req("device aparelho tarefa fatura", repo, top_k=2)).selected_files) <= 2
    sem = r.retrieve(_req("o que é isso", repo))  # só stopwords/curtas
    assert sem.selected_files == () and sem.warnings == ("no_tokens",) and sem.source == "bm25"
    nada = r.retrieve(_req("zzzxqwyk", repo))
    assert nada.selected_files == () and nada.warnings == ("no_matches",)


def test_bm25_janela_e_a_de_maior_densidade(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    raiz.mkdir()
    linhas = ["ruido"] * 50
    linhas[29], linhas[30], linhas[31] = "cobranca mensal", "fatura mensal cobranca", "assinatura fatura"
    linhas[1] = "fatura"  # um casamento isolado no começo não deve ganhar da região densa
    (raiz / "alvo.txt").write_text("\n".join(linhas) + "\n", encoding="utf-8")
    sel = BM25Retriever(Workspace(raiz), window_lines=6).retrieve(_req("fatura mensal cobranca assinatura", raiz))
    reg = sel.selected_regions[0]
    assert reg.start_line <= 30 and reg.end_line >= 32 and reg.end_line - reg.start_line + 1 == 6


# ====================================================================== Mapa do repositório
def test_repo_map_simbolos_sem_corpo(ws: Workspace) -> None:
    mapa = build_repo_map(ws)
    por = {e.path: e for e in mapa.entries}
    assert [e.path for e in mapa.entries] == sorted(por)
    assert mapa.revision == ws.revision()
    sched = por["backend/app/scheduler.py"]
    assert sched.language == "python" and sched.size == len(SCHEDULER.encode())
    assert sched.symbols == ("pick_device_for_task", "Scheduler", "Scheduler.enqueue", "Scheduler.drain",
                             "release_device")
    assert sched.summary == "Escalonador que distribui tarefas entre os aparelhos do parque."
    assert por["backend/app/billing.py"].summary == "Calcula a fatura mensal da assinatura."
    assert por["frontend/src/app.ts"].symbols == ("Props", "Mode", "renderDashboard", "API_BASE", "Painel")
    assert por["db/001_init.sql"].symbols == ("TABLE devices", "INDEX idx_devices_id")
    assert por["docs/guide.md"].symbols == ("Guia de uso", "Instalação do agente")  # sem o `#` do bloco de código
    assert por["scripts/run.ps1"].symbols == ("Start-Farm",)
    assert por["backend/app/scheduler.py"].language == "python" and por["docs/guide.md"].language == "markdown"
    # nada de corpo, comentário nem sensível no texto renderizado
    saida = mapa.render()
    assert "corpo_secreto_do_escalonador" not in saida and "Segunda linha" not in saida
    assert ".env" not in saida and "server.pem" not in saida and "config.yaml" not in saida


def test_repo_map_limite_de_simbolos_dedupe_e_resumo_truncado(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    raiz.mkdir()
    corpo = '"""' + "x" * 200 + '"""\n' + "".join(f"def f{i}():\n    pass\n" for i in range(30)) + "def f1():\n    pass\n"
    (raiz / "m.py").write_text(corpo, encoding="utf-8")
    (raiz / "sem_doc.py").write_text("import os\n\n\ndef a():\n    pass\n", encoding="utf-8")
    por = {e.path: e for e in build_repo_map(Workspace(raiz), max_symbols=5).entries}
    assert por["m.py"].symbols == ("f0", "f1", "f2", "f3", "f4")
    assert len(por["m.py"].summary) == 80 and por["m.py"].summary.endswith("…")
    assert por["sem_doc.py"].summary == "" and por["sem_doc.py"].symbols == ("a",)


def test_repo_map_provider_cache_hit_miss_e_invalidacao(ws: Workspace, repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    p1 = RepoMapProvider(ws, cache)
    _fonte: MapSource = p1  # conformidade estrutural (a porta não é runtime_checkable)
    m1 = p1.repo_map()
    assert p1.last_source == "built"
    arquivo = cache / f"repomap-{ws.revision()}-v{RETRIEVAL_VERSION}.json"
    assert arquivo.exists() and not list(cache.glob("*.tmp"))
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    assert dados["revision"] == ws.revision() and dados["version"] == RETRIEVAL_VERSION
    assert [e["path"] for e in dados["entries"]] == sorted(e["path"] for e in dados["entries"])
    assert "corpo_secreto_do_escalonador" not in arquivo.read_text(encoding="utf-8")
    assert p1.repo_map() is m1 and p1.last_source == "memory"
    p2 = RepoMapProvider(Workspace(repo), cache)  # processo novo: vem do disco, igual ao construído
    assert p2.repo_map() == m1 and p2.last_source == "disk"
    (repo / "docs/novo.md").write_text("# Novo título\n", encoding="utf-8")  # revisão nova: miss
    p3 = RepoMapProvider(Workspace(repo), cache)
    m3 = p3.repo_map()
    assert p3.last_source == "built" and m3.revision != m1.revision
    assert "docs/novo.md" in [e.path for e in m3.entries]
    assert p1.repo_map().revision == m3.revision and p1.last_source == "disk"  # a memória respeita a revisão (e acha o disco do p3)


def test_repo_map_cache_versao_diferente_nao_e_reaproveitada(ws: Workspace, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    RepoMapProvider(ws, cache).repo_map()
    arquivo = next(cache.glob("repomap-*.json"))
    dados = json.loads(arquivo.read_text(encoding="utf-8"))
    dados["version"] = "versao-antiga"
    arquivo.write_text(json.dumps(dados), encoding="utf-8")
    p = RepoMapProvider(Workspace(ws.root), cache)
    p.repo_map()
    assert p.last_source == "built"


@pytest.mark.parametrize("lixo", ["{ isso nao e json", "[]", '{"revision": 1}', "", '{"entries": 5}'])
def test_repo_map_cache_corrompido_reconstroi_sem_falhar(ws: Workspace, tmp_path: Path, lixo: str) -> None:
    cache = tmp_path / "cache"
    ref = RepoMapProvider(ws, cache).repo_map()
    next(cache.glob("repomap-*.json")).write_text(lixo, encoding="utf-8")
    p = RepoMapProvider(Workspace(ws.root), cache)
    assert p.repo_map() == ref and p.last_source == "built"
    p4 = RepoMapProvider(Workspace(ws.root), cache)  # e o cache foi regravado são
    assert p4.repo_map() == ref and p4.last_source == "disk"


def test_repo_map_limpa_caches_de_revisoes_antigas(ws: Workspace, repo: Path, tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    for i in range(6):  # revisões antigas pré-existentes (inclusive de outra versão do retrieval)
        (cache / f"repomap-antiga{i}-v0.json").write_text("{}", encoding="utf-8")
    RepoMapProvider(ws, cache).repo_map()
    restantes = sorted(p.name for p in cache.glob("repomap-*.json"))
    assert len(restantes) == 3
    assert f"repomap-{ws.revision()}-v{RETRIEVAL_VERSION}.json" in restantes  # o atual nunca é apagado


def test_repo_map_sem_cache_dir_funciona(ws: Workspace) -> None:
    p = RepoMapProvider(ws)
    assert p.repo_map().entries and p.last_source == "built"
    assert p.repo_map().entries and p.last_source == "memory"


# ====================================================================== Chunker
def test_chunker_janelas_com_overlap_e_ordem(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    raiz.mkdir()
    (raiz / "a.txt").write_text("\n".join(f"a{i}" for i in range(1, 11)) + "\n", encoding="utf-8")
    (raiz / "b.txt").write_text("b1\nb2\n", encoding="utf-8")
    c = Chunker(Workspace(raiz), chunk_lines=4, overlap=1)
    _fonte: ChunkSource = c  # conformidade estrutural (a porta não é runtime_checkable)
    chunks = c.chunks_for(["b.txt", "a.txt"], max_chunks=99, max_bytes=10_000)
    assert [(x.path, x.start_line, x.end_line) for x in chunks] == \
        [("b.txt", 1, 2), ("a.txt", 1, 4), ("a.txt", 4, 7), ("a.txt", 7, 10)]
    assert chunks[0].text == "b1\nb2" and chunks[2].text == "a4\na5\na6\na7"
    assert chunks[1].region(with_text=True).text == "a1\na2\na3\na4"
    assert c.chunks_for(["a.txt", "a.txt"], max_chunks=99, max_bytes=10_000) == chunks[1:]  # repetido não duplica


def test_chunker_limites_de_chunks_e_de_bytes(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    raiz.mkdir()
    (raiz / "a.txt").write_text("\n".join("x" * 9 for _ in range(20)) + "\n", encoding="utf-8")  # 9B por linha
    c = Chunker(Workspace(raiz), chunk_lines=2, overlap=0)  # cada chunk: 9+1+9 = 19 bytes
    por_chunks = c.chunks_for(["a.txt"], max_chunks=3, max_bytes=10_000)
    assert len(por_chunks) == 3 and [x.start_line for x in por_chunks] == [1, 3, 5]
    por_bytes = c.chunks_for(["a.txt"], max_chunks=99, max_bytes=40)
    assert len(por_bytes) == 2 and sum(len(x.text.encode()) for x in por_bytes) <= 40  # o 3º estouraria: não entra
    assert c.chunks_for(["a.txt"], max_chunks=0, max_bytes=10_000) == []
    assert c.chunks_for(["a.txt"], max_chunks=99, max_bytes=5) == []


def test_chunker_so_dos_caminhos_pedidos_e_do_universo(ws: Workspace) -> None:
    c = Chunker(ws)
    chunks = c.chunks_for(["docs/guide.md", ".env", "big.txt", "config/config.yaml", "../x", "assets/blob.dat",
                           "nao/existe.py"], max_chunks=50, max_bytes=100_000)
    assert {x.path for x in chunks} == {"docs/guide.md"}
    assert c.chunks_for([], max_chunks=5, max_bytes=1000) == []
    unico = c.chunks_for(["backend/app/billing.py"], max_chunks=50, max_bytes=100_000)
    assert [(x.start_line, x.end_line) for x in unico] == [(1, len(BILLING.split("\n")) - 1)]
    assert unico[0].text == BILLING.rstrip("\n")


def test_chunker_overlap_maior_que_janela_nao_trava(tmp_path: Path) -> None:
    raiz = tmp_path / "r"
    raiz.mkdir()
    (raiz / "a.txt").write_text("\n".join(f"l{i}" for i in range(1, 8)) + "\n", encoding="utf-8")
    chunks = Chunker(Workspace(raiz), chunk_lines=3, overlap=9).chunks_for(["a.txt"], max_chunks=50, max_bytes=10_000)
    assert chunks and chunks[-1].end_line == 7 and [x.start_line for x in chunks] == [1, 2, 3, 4, 5]


def test_selecao_vazia_helper_do_dominio(ws: Workspace, repo: Path) -> None:
    assert isinstance(LexicalRetriever(ws, use_ripgrep=False).retrieve(_req("", repo)), ContextSelection)
