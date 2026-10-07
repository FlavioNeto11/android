"""31.239: o marcador de "comentário publicado" do Instagram, como dado no catálogo (`comentario:{account_label}`).

Material (onda 2, 07/10, evidências 2951 a 2956, lidas só em leitura e sem copiar texto): as evidências são capturas,
sem árvore guardada. As notas do juiz descrevem a linha: o autor num elemento, o texto noutro, a hora e "Edit" depois de
publicado; logo depois do toque, "Posting…", e o juiz já dava por publicado (2952 e 2955). A forma "autor said texto" é
a que a lista anuncia (`COLLECT_COMMENTS`, `leitura.comentario` do app).

O que estes testes protegem:
* a prova: o texto desta etapa num elemento não editável com o autor (a conta da etapa) na mesma faixa, ou a linha
  "autor said texto", comprova; sem o comentário, com o texto só no campo de escrita, com o autor de outra linha ou
  com outra pessoa, não; sem `content` ou sem conta, não afirma;
* o catálogo: CREATE_COMMENT declara `comentario:{account_label}` e as marcas de pendente ("Posting…"); a gramática
  recusa a forma mal escrita; a porta do catálogo não comprova com "Posting…" na tela;
* a verificação: com a prova, o primeiro julgamento sai e o rejulgamento decide; sem ela, o juiz barato julga; a prova
  nunca fecha o efeito sozinha (sem rejulgamento, o juiz barato julga como antes).

Nível de prova: `simulated` (árvores montadas e verificador de mentira). `real`: a 1ª operação de comentário depois do
deploy.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.models import Postcondition, StepDTO, StepStatus
from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.domain.verification import Observation as Leitura
from app.modules.capabilities.domain.verification import StepView, VerifyOutcome
from app.modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from app.planning.capabilities import capability_of, local_proof_error
from app.taskqueue.proofs import local_proof_holds

from .test_dm_verificador import _executor
from .test_sent_text_dispensa_juiz import _com_modelos_diferentes, _Juizes

PKG = "com.instagram.android"
IID = "android-01"
CONTA = "conta.teste01"
TEXTO = "que vista bonita, dá vontade de olhar de novo com calma"
_TV, _ET = "android.widget.TextView", "android.widget.EditText"
COMENTAR = CapabilityRef(PKG, "CREATE_COMMENT")


def _lista(linhas: list[tuple[str, str, str]], *, campo: str = "Add a comment…") -> UiTree:
    """A folha de comentários: (classe, texto, id) por linha, cada uma numa faixa de 70 px, e o campo de escrita."""
    nos = [(_TV, "Comments", "title_text_view"), *linhas, (_ET, campo, "layout_comment_thread_edittext")]
    corpo = "".join(f'<node class="{c}" text="{t}" resource-id="{PKG}:id/{r}" bounds="[0,{i * 70}][700,{i * 70 + 50}]"/>'
                    for i, (c, t, r) in enumerate(nos))
    return parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")


def _publicado(autor: str = CONTA, *, marca: str = "1 second ago") -> UiTree:
    return _lista([(_TV, "outra.pessoa", "row_comment_textview_username"), (_TV, "primeira linha antiga", "c1"),
                   (_TV, autor, "row_comment_textview_username"), (_TV, TEXTO, "row_comment_textview_comment"),
                   (_TV, marca, "row_comment_textview_time_ago")])


def _etapa(**kw: str) -> SimpleNamespace:
    return SimpleNamespace(bindings={"content": TEXTO, "account_label": CONTA, **kw})


def test_a_prova_do_comentario_publicado() -> None:
    prova = "comentario:{account_label}"
    assert local_proof_holds(prova, _etapa(), _publicado()) is True
    falada = _lista([(_TV, f"{CONTA} said {TEXTO}", "row_comment_container")])
    assert local_proof_holds(prova, _etapa(), falada) is True                        # a linha "autor said texto"
    assert local_proof_holds(prova, _etapa(), _publicado("outra.pessoa")) is False   # o texto de outra pessoa
    assert local_proof_holds(prova, _etapa(), _lista([], campo=TEXTO)) is False      # só no campo de escrita
    longe = _lista([(_TV, CONTA, "row_comment_textview_username"), *[(_TV, f"linha {i}", f"c{i}") for i in range(4)],
                    (_TV, TEXTO, "row_comment_textview_comment")])
    assert local_proof_holds(prova, _etapa(), longe) is False                        # o autor é de outra linha
    assert local_proof_holds(prova, _etapa(content=""), _publicado()) is None        # sem texto, não afirma
    assert local_proof_holds(prova, SimpleNamespace(bindings={"content": TEXTO}), _publicado()) is None   # sem conta
    assert local_proof_holds(prova, _etapa(account_label=f"@{CONTA}"), _publicado()) is True   # a arroba é notação


def test_o_catalogo_e_a_gramatica() -> None:
    cap = capability_of(PKG, "CREATE_COMMENT")
    assert cap is not None and cap.local_proof == "comentario:{account_label}"
    assert "Posting…" in cap.pending_marks
    assert local_proof_error("comentario:{account_label}") is None
    assert local_proof_error("comentario:") is not None and local_proof_error("comentario:a&b") is not None


async def _pela_porta(tela: UiTree) -> VerifyOutcome:
    porta = CatalogCapabilityProvider(CatalogCapabilityRegistry(lambda _app: None))
    vista = StepView(node_id="comment_1", capability=COMENTAR, bindings=(("content", TEXTO), ("account_label", CONTA)),
                     band_guard=(), mensagens_antes=None, required_delivery_level=None)
    return (await porta.verify(vista, Leitura(tela, PKG))).outcome


async def test_a_porta_nao_comprova_com_posting() -> None:
    assert await _pela_porta(_publicado()) is VerifyOutcome.proved
    assert await _pela_porta(_publicado(marca="Posting…")) is not VerifyOutcome.proved


def _comentar() -> StepDTO:
    cap = capability_of(PKG, "CREATE_COMMENT")
    assert cap is not None
    return StepDTO(id=f"r-c:{IID}:v1:comment_1", run_id="r-c", objective_id=f"r-c:{IID}", instance_id=IID,
                   plan_version=1, seq=1, key="comment_1", title="Comentar na publicação", goal=cap.goal,
                   depends_on=[], side_effect=True, commit_guard=[TEXTO],
                   postcondition=Postcondition(kind="model_judged", value=cap.post_value,
                                               description=cap.post_description),
                   timeout_s=60, max_attempts=1, attempts=1, status=StepStatus.verifying, capability="CREATE_COMMENT",
                   commit_selector=cap.commit_selector, bindings={"content": TEXTO})


async def _verificar(tmp_path: Path, juizes: _Juizes, tela: UiTree, *, rejulga: bool = True) -> tuple[bool, str]:
    ex = _executor(tmp_path, [tela], juizes)  # type: ignore[arg-type]
    _com_modelos_diferentes(ex)
    ex.cfg.file.ai.rejudge_yes_on_side_effect = rejulga
    ex.cfg.file.ai.rejulgamento_dispensado_por_app = False          # aqui só o 31.239; a dispensa é o 31.238
    etapa = _comentar()
    ok, texto, *_ = await ex._verify(  # noqa: SLF001
        SimpleNamespace(id=IID), etapa, lambda: SimpleNamespace(step_key=etapa.key, instance_id=IID,  # type: ignore[arg-type]
                                                                account_label=CONTA),
        "r-c", f"r-c:{IID}", time.monotonic() + 1.0, 5.0, patient=True, facts=[], failure_marks=(),
        local_proof="comentario:{account_label}", capability=COMENTAR, pacote=PKG)
    return ok, texto


async def test_com_a_prova_o_primeiro_juiz_sai_e_o_rejulgamento_decide(tmp_path: Path) -> None:
    juizes = _Juizes()
    ok, texto = await _verificar(tmp_path, juizes, _publicado())
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (0, 1)


async def test_sem_a_prova_o_juiz_barato_julga(tmp_path: Path) -> None:
    juizes = _Juizes()
    await _verificar(tmp_path, juizes, _publicado("outra.pessoa"))
    assert juizes.barato >= 1


async def test_sem_rejulgamento_a_prova_nao_fecha_sozinha(tmp_path: Path) -> None:
    juizes = _Juizes()
    ok, texto = await _verificar(tmp_path, juizes, _publicado(), rejulga=False)
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (1, 0)


async def test_com_posting_na_tela_nada_comprova(tmp_path: Path) -> None:
    # ADR-055 vale agora para o comentário: "Posting…" é efeito a caminho; nem a prova nem o modelo são consultados, e
    # sem a marca sair no prazo a etapa não é dada por feita (antes, o juiz dava por publicado: 2952 e 2955).
    juizes = _Juizes()
    ok, texto = await _verificar(tmp_path, juizes, _publicado(marca="Posting…"))
    assert not ok and "pendente" in texto
    assert (juizes.barato, juizes.escalonamento) == (0, 0)


def test_a_linha_de_base_do_toque() -> None:
    # 31.59 aplicado ao comentário: o comentário igual e antigo da própria conta, já na tela no toque, não prova este.
    prova = "comentario:{account_label}"
    def com_base(n: int) -> SimpleNamespace:
        return SimpleNamespace(bindings={"content": TEXTO, "account_label": CONTA}, mensagens_antes=n)
    assert local_proof_holds(prova, com_base(0), _publicado()) is True
    assert local_proof_holds(prova, com_base(1), _publicado()) is False              # o mesmo de antes do toque
    dois = _lista([(_TV, CONTA, "u"), (_TV, TEXTO, "c"), (_TV, CONTA, "u"), (_TV, TEXTO, "c")])
    assert local_proof_holds(prova, com_base(1), dois) is True                       # um a mais que no toque
    falada = _lista([(_TV, f"{CONTA} said {TEXTO}", "row_comment_container")])
    assert local_proof_holds(prova, com_base(0), falada) is False                    # sem o texto contável, o juiz decide
