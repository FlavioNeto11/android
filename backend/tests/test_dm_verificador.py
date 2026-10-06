"""Verificador de DM com critério objetivo (ADR-055, pacote dm-verificador) e confirmação manual presa a uma evidência.

19/09: o verificador por modelo (`model_judged`) errou nos dois sentidos numa bateria de DMs. Cinco falsos negativos
(os prints mostram a bolha enviada; num deles o modelo disse "apenas parte do texto" com o texto inteiro na tela) e um
falso positivo: a DM da sueli dada por ENVIADA com "Sending…" congelado debaixo da bolha. A correção é o critério
objetivo pela árvore, declarado no catálogo (dado, ADR-052) e conferido ANTES do modelo:

- "Sending…"/"Enviando…" na tela = pendente. Nunca enviada, nem pela prova local nem pelo "sim" do modelo — e o
  modelo nem é perguntado enquanto a marca estiver lá;
- a bolha com o texto E o campo de escrita da conversa na tela sem o texto = enviada, sem chamar o modelo;
- o "confirmar concluído" de uma etapa com efeito externo cita a evidência (o print) em que a pessoa se baseou, em
  vez de só uma nota livre.

Nível de prova: `simulated` — árvores montadas com os ids lidos das telas reais (`row_thread_composer_edittext`,
`direct_text_message_text_view`), um verificador de mentira que diz "sim" a tudo e o Harness (porta base 5640) para a
confirmação manual. A prova `real` (uma DM numa conta do Instagram) fica `not_run`: mandar mensagem é efeito numa
conta real de terceiros e exige autorização do dono.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.devices.manager import Observation
from app.models import Postcondition, ResolveBody, StepDTO, StepStatus
from app.modules.capabilities.domain.definition import CapabilityRef
from app.modules.capabilities.domain.verification import Observation as Leitura
from app.modules.capabilities.domain.verification import StepView, VerifyOutcome
from app.modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
from app.planning.capabilities import capability_of, local_proof_error
from app.planning.provider import Usage, Verdict, VerifyRequest
from app.taskqueue.executor import StepExecutor
from app.taskqueue.proofs import local_proof_holds, marcas_pendentes_na_tela
from app.taskqueue.service import RunError

from .conftest import Harness, make_config

PKG = "com.instagram.android"
IID = "android-01"
CONTEUDO = "oi, Ana! Vi seu post de ontem e lembrei da nossa conversa"
_TV, _ET = "android.widget.TextView", "android.widget.EditText"
ENVIO = CapabilityRef(PKG, "SEND_MESSAGE")


# ==================================================================== a tela da conversa
def _conversa(*, status: str | None = None, no_campo: str = "Message…", bolha: bool = True,
              compositor: bool = True) -> UiTree:
    """A conversa com @ana depois do toque em Send: cabeçalho, a bolha (opcional), a linha de status (opcional) e o
    compositor. O compositor vazio mostra a dica "Message…" como texto, como no app 447."""
    nos = [(_TV, "ana", "header_title")]
    if bolha:
        nos.append((_TV, CONTEUDO, "direct_text_message_text_view"))
    if status:
        nos.append((_TV, status, "direct_message_status_text"))
    if compositor:
        nos.append((_ET, no_campo, "row_thread_composer_edittext"))
    corpo = "".join(f'<node class="{c}" text="{t}" resource-id="{PKG}:id/{r}" bounds="[0,{i * 80}][700,{i * 80 + 60}]"/>'
                    for i, (c, t, r) in enumerate(nos))
    return parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")


class _Aparelho:
    """Devolve as telas na ordem, repetindo a última: é o `devices` que o `_verify` lê."""

    def __init__(self, telas: list[UiTree], frente: str = PKG) -> None:
        self.telas = telas
        self.frente = frente            # o pacote em primeiro plano que a observação diz
        self.leituras = 0

    async def observe(self, rt: object, *, timeout: float, imagem: bool) -> Observation:
        tela = self.telas[min(self.leituras, len(self.telas) - 1)]
        self.leituras += 1
        return Observation(frame_id=str(self.leituras), ts="2026-09-19T21:00:00Z", width=720, height=1280, jpeg=None,
                           tree=tela, package=self.frente, sensitive=False)

    async def completar_imagem(self, rt: object, obs: Observation, *, timeout: float, lado_max: int) -> Observation:
        return obs


class _VerificadorQueDizSim:
    """O falso positivo de 19/09: o modelo diz "sim" para a bolha na tela, com ou sem "Sending…" debaixo dela."""

    def __init__(self) -> None:
        self.chamadas = 0

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        self.chamadas += 1
        return Verdict(satisfied="yes", evidence="[teste] a mensagem aparece na conversa"), Usage()


def _executor(tmp_path: Path, telas: list[UiTree], verificador: _VerificadorQueDizSim, *,
              frente: str = PKG) -> StepExecutor:
    """Só o que `StepExecutor._verify` lê: config, aparelho, provedor, porta de capability e `_ai` (sem banco)."""
    ex = object.__new__(StepExecutor)
    ex.cfg = make_config(tmp_path, 1)
    # T2: o orçamento da verificação curto. O que se prova ("pendente até o prazo → não comprovado") não depende do
    # tamanho do prazo; com o piso padrão de 8 s cada caso pagava 8 s de relógio real.
    ai = ex.cfg.file.ai
    ai.verify_budget_min_s = ai.verify_budget_s = ai.verify_budget_patient_s = 1.5
    ex.repo = SimpleNamespace(decision=lambda *a, **k: None)  # type: ignore[assignment]
    ex.devices = _Aparelho(telas, frente)  # type: ignore[assignment]
    ex.provider = verificador  # type: ignore[assignment]
    ex.capabilities = CatalogCapabilityProvider(CatalogCapabilityRegistry(lambda _app: None))
    # 31.59: a linha de base que o `_run_step` guarda no toque do Send — a tela de antes não tinha a bolha.
    ex._mensagens_antes = {f"r-dm:{IID}:v1:send": 0}  # noqa: SLF001

    async def _ai(run_id: str, objective_id: str | None, fabrica: object, **_kw: object) -> Verdict:
        resultado, _uso = await fabrica()  # type: ignore[operator]
        return resultado  # type: ignore[no-any-return]

    ex._ai = _ai  # type: ignore[method-assign]
    return ex


def _envio(*, conteudo: str | None = CONTEUDO) -> StepDTO:
    """A etapa SEND_MESSAGE como o catálogo a monta, com o texto aprovado nos argumentos."""
    cap = capability_of(PKG, "SEND_MESSAGE")
    assert cap is not None
    bindings = {"username": "@ana", **({"content": conteudo} if conteudo else {})}
    return StepDTO(id=f"r-dm:{IID}:v1:send", run_id="r-dm", objective_id=f"r-dm:{IID}", instance_id=IID,
                   plan_version=1, seq=1, key="send", title="Enviar a mensagem para @ana", goal=cap.goal,
                   depends_on=[], side_effect=True, commit_guard=["@ana", *([conteudo] if conteudo else [])],
                   postcondition=Postcondition(kind="model_judged", value="mensagem enviada para @ana",
                                               description=cap.post_description.replace("{username}", "@ana")),
                   timeout_s=60, max_attempts=1, attempts=1, status=StepStatus.verifying, capability="SEND_MESSAGE",
                   commit_selector=cap.commit_selector, bindings=bindings)


async def _verificar(ex: StepExecutor, etapa: StepDTO) -> tuple[bool, str]:
    """A mesma chamada que o `_run_step` faz depois do toque em Send (efeito disparado: `patient`, marcas de falha)."""
    cap = capability_of(PKG, "SEND_MESSAGE")
    assert cap is not None
    ok, texto, _nivel, _obs, _nao_comprovavel = await ex._verify(  # noqa: SLF001
        SimpleNamespace(id=IID), etapa, lambda: SimpleNamespace(step_key=etapa.key, instance_id=IID),  # type: ignore[arg-type]
        "r-dm", f"r-dm:{IID}", time.monotonic() + 1.0, 5.0, patient=True, facts=[],
        failure_marks=tuple(cap.failure_marks), local_proof=cap.local_proof, capability=ENVIO, pacote=PKG)
    return ok, texto


# ==================================================================== "Sending…" é pendente, nunca enviada
async def test_sending_na_tela_e_pendente_nunca_enviada_e_o_modelo_nao_e_perguntado(tmp_path: Path) -> None:
    """A DM da sueli (19/09): a bolha na conversa, o campo de escrita limpo e "Sending…" congelado. A prova local de
    hoje (bolha + campo sem o texto) e o modelo davam a mensagem por enviada. Pendente não é enviada: a verificação
    espera o prazo inteiro sem perguntar ao modelo e devolve "não comprovado" — o efeito disparado vira incerto."""
    verificador = _VerificadorQueDizSim()
    ex = _executor(tmp_path, [_conversa(status="Sending…")], verificador)
    ok, texto = await _verificar(ex, _envio())
    assert not ok, texto
    assert verificador.chamadas == 0                           # o "sim" do modelo nunca foi pedido
    assert "Sending…" in texto and "pendente" in texto


@pytest.mark.parametrize("status", ["Sending…", "Sending...", "Enviando…", "Enviando...", "Sending… · Just now"])
def test_as_grafias_do_pendente_casam_as_marcas_do_catalogo(status: str) -> None:
    """As duas reticências (a comparação é por texto) e o aparelho em português; a marca vale dentro de um status mais
    longo. Sem marca na tela, nada é pendente."""
    cap = capability_of(PKG, "SEND_MESSAGE")
    assert cap is not None
    assert marcas_pendentes_na_tela(cap.pending_marks, _conversa(status=status))
    assert marcas_pendentes_na_tela(cap.pending_marks, _conversa()) == []
    assert marcas_pendentes_na_tela(cap.pending_marks, _conversa(status="Seen")) == []


async def test_sending_sem_conteudo_conhecido_tambem_nao_vai_ao_modelo(tmp_path: Path) -> None:
    """Sem o `content` na etapa a prova local não opina e, até aqui, o modelo julgava sozinho — e disse "sim" com
    "Sending…" na tela. A marca de pendente vale antes de qualquer julgamento."""
    verificador = _VerificadorQueDizSim()
    ex = _executor(tmp_path, [_conversa(status="Sending…")], verificador)
    ok, texto = await _verificar(ex, _envio(conteudo=None))
    assert not ok and verificador.chamadas == 0, texto


async def test_sending_que_some_fecha_pela_arvore_sem_modelo(tmp_path: Path) -> None:
    """O app costuma levar 1–2 s para sair de "Sending…": a verificação segue olhando e, quando a marca some, a bolha
    com o campo vazio comprova o envio — sem chamada de modelo."""
    verificador = _VerificadorQueDizSim()
    telas = [_conversa(status="Sending…"), _conversa(status="Sending…"), _conversa()]
    ex = _executor(tmp_path, telas, verificador)
    ok, texto = await _verificar(ex, _envio())
    assert ok, texto
    assert verificador.chamadas == 0 and "árvore local" in texto


async def test_sending_que_aparece_depois_do_sim_nao_vira_enviada(tmp_path: Path) -> None:
    """Depois do "sim" o executor assenta a tela e relê (UI otimista: a bolha aparece antes de o servidor confirmar).
    Até aqui a releitura só procurava marca de FALHA; "Sending…" nela também não pode fechar a etapa."""
    verificador = _VerificadorQueDizSim()
    ex = _executor(tmp_path, [_conversa(), _conversa(status="Sending…")], verificador)
    ok, texto = await _verificar(ex, _envio())
    assert not ok, texto
    assert "Sending…" in texto


# ==================================================================== a tela de outro app não prova a etapa
async def test_tela_de_outro_app_nao_comprova_nem_vai_ao_modelo(tmp_path: Path) -> None:
    """Item 24.7: a mesma árvore que comprova o envio (bolha + campo vazio), mas com OUTRO pacote em primeiro plano —
    duas telas de apps diferentes podem mostrar o mesmo texto. A etapa é do Instagram: nem a prova local nem o modelo
    fecham a verificação, que segue olhando até o prazo e devolve "não comprovado" dizendo qual app estava à frente."""
    verificador = _VerificadorQueDizSim()
    ex = _executor(tmp_path, [_conversa()], verificador, frente="com.pocqa.messenger")
    ok, texto = await _verificar(ex, _envio())
    assert not ok, texto
    assert verificador.chamadas == 0
    assert "com.pocqa.messenger" in texto and PKG in texto and "árvore local" not in texto


# ==================================================================== bolha + campo vazio = enviada
async def test_bolha_com_o_texto_e_campo_vazio_e_enviada_sem_chamar_o_modelo(tmp_path: Path) -> None:
    """Os cinco falsos negativos de 19/09: o print mostrava a bolha com o texto inteiro e o campo de escrita vazio.
    Isso é enviado pela árvore, e o modelo (que chegou a dizer "apenas parte do texto") nem é perguntado."""
    verificador = _VerificadorQueDizSim()
    ex = _executor(tmp_path, [_conversa()], verificador)
    ok, texto = await _verificar(ex, _envio())
    assert ok, texto
    assert verificador.chamadas == 0
    assert "árvore local" in texto and "campo de escrita" in texto


async def test_sem_o_campo_de_escrita_da_conversa_a_arvore_nao_afirma(tmp_path: Path) -> None:
    """A prova declara QUAL é o campo de escrita (o compositor da conversa). Sem ele na tela não dá para dizer que o
    campo está vazio: a árvore não afirma e quem julga é o modelo, como antes — nunca vira reprovação sozinha."""
    verificador = _VerificadorQueDizSim()
    ex = _executor(tmp_path, [_conversa(compositor=False)], verificador)
    ok, _texto = await _verificar(ex, _envio())
    assert ok and verificador.chamadas == 1


async def test_texto_ainda_no_campo_nao_e_enviado_pela_arvore(tmp_path: Path) -> None:
    """O texto ainda no compositor não saiu dele: a árvore não afirma e o modelo julga (como antes)."""
    arvore = _conversa(bolha=False, no_campo=CONTEUDO)
    cap = capability_of(PKG, "SEND_MESSAGE")
    assert cap is not None and cap.local_proof
    assert local_proof_holds(cap.local_proof, _envio(), arvore) is False


# ==================================================================== a declaração no catálogo (dado)
def test_o_catalogo_declara_o_criterio_objetivo_da_dm() -> None:
    cap = capability_of(PKG, "SEND_MESSAGE")
    assert cap is not None
    # o campo de escrita da conversa é DADO: o id lido das telas reais julgadas em 24–25/09
    assert cap.local_proof == "sent_text:id=row_thread_composer_edittext"
    assert local_proof_error(cap.local_proof) is None
    for marca in ("Sending…", "Sending...", "Enviando…", "Enviando..."):
        assert marca in cap.pending_marks, marca
    assert "Sending" in cap.post_description                   # o modelo também lê que pendente não é enviada
    # a gramática recusa o compositor vazio e o `&` (a prova é de UM campo)
    assert local_proof_error("sent_text:") and local_proof_error("sent_text:id=a&id=b")
    assert local_proof_error("sent_text") is None               # a forma antiga continua válida


async def test_a_porta_de_capability_devolve_pendente_com_sending() -> None:
    """A mesma regra pela porta `CapabilityProvider` (fase G): pendente não prova nem desmente — e nunca é `proved`."""
    prov = CatalogCapabilityProvider(CatalogCapabilityRegistry({"instagram": PKG}.get))
    etapa = StepView(node_id="send", capability=ENVIO, bindings=(("content", CONTEUDO), ("username", "@ana")),
                     mensagens_antes=0)
    pendente = await prov.verify(etapa, Leitura(screen=_conversa(status="Sending…"), package=PKG))
    assert pendente.outcome is VerifyOutcome.pending and "Sending…" in pendente.detail
    enviada = await prov.verify(etapa, Leitura(screen=_conversa(), package=PKG))
    assert enviada.outcome is VerifyOutcome.proved


# ==================================================================== confirmação manual presa à evidência
def _cenario_de_confirmacao(h: Harness, *, side_effect: bool = True, run: str = "run-c") -> tuple[str, str]:
    st = h.state
    assert st is not None
    db = st.db
    oid, sid = f"{run}:{IID}", f"{run}:{IID}:v1:send_1"
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES (?,?,'enviar','execute','running',1,'[\"android-01\"]','2026-09-19T21:00:00Z')", (run, f"k-{run}"))
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, parameters)"
               " VALUES (?,?,?,'uncertain',1,'{}')", (oid, run, IID))
    db.execute(
        "INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal, depends_on,"
        " side_effect, commit_guard, postcondition, timeout_s, max_attempts, status, capability, commit_selector,"
        " bindings) VALUES (?,?,?,?,1,1,'send_1','Enviar a mensagem para @ana','enviar','[]',?,'[\"@ana\"]',"
        "'{\"kind\":\"model_judged\",\"value\":\"x\",\"description\":\"y\"}',180,1,'uncertain',?,?,"
        "'{\"username\": \"@ana\", \"content\": \"boa noite\"}')",
        (sid, run, oid, IID, int(side_effect), "SEND_MESSAGE" if side_effect else None,
         "desc=Send" if side_effect else None))
    return oid, sid


def _print(h: Harness, run: str, step_id: str | None, *, instance: str = IID, kind: str = "screenshot",
           redacted: bool = False) -> int:
    st = h.state
    assert st is not None
    return st.runs.repo.add_evidence(run_id=run, instance_id=instance, step_id=step_id, attempt_id=None, kind=kind,
                                     note="Pós-condição NÃO comprovada: Sending… na tela",
                                     data=None if redacted else b"\xff\xd8jpeg", redacted=redacted)


async def test_confirmar_envio_exige_o_print_e_guarda_o_id_da_evidencia(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    oid, sid = _cenario_de_confirmacao(harness)
    # só nota livre não basta numa etapa com efeito externo: é o buraco de 19/09 (confirmar sem olhar o print)
    with pytest.raises(RunError) as sem_print:
        st.runs.resolve("run-c", oid, ResolveBody(resolution="confirm_done", note="vi sair no aparelho"))
    assert sem_print.value.code == "evidence_required"
    assert st.db.one("SELECT status FROM steps WHERE id=?", (sid,))["status"] == "uncertain"

    eid = _print(harness, "run-c", sid)
    st.runs.resolve("run-c", oid, ResolveBody(resolution="confirm_done", evidence_id=eid, note="bolha enviada"))
    etapa = next(s for s in st.runs.repo.run_detail("run-c").steps if s.id == sid)  # type: ignore[union-attr]
    assert etapa.status == StepStatus.succeeded
    assert etapa.result is not None and etapa.result.evidence_id == eid
    assert not etapa.result.verified                          # decisão de pessoa, não comprovação automática
    assert f"#{eid}" in (etapa.result.evidence_text or "") and "bolha enviada" in (etapa.result.evidence_text or "")


@pytest.mark.parametrize("caso", ["outra_execucao", "outro_aparelho", "nao_e_print", "print_omitido", "inexistente"])
async def test_confirmar_com_evidencia_que_nao_e_o_print_deste_item_e_recusado(harness: Harness, caso: str) -> None:
    st = harness.state
    assert st is not None
    oid, sid = _cenario_de_confirmacao(harness)
    _cenario_de_confirmacao(harness, run="run-outra")
    eid = {"outra_execucao": lambda: _print(harness, "run-outra", None),
           "outro_aparelho": lambda: _print(harness, "run-c", None, instance="android-02"),
           "nao_e_print": lambda: _print(harness, "run-c", sid, kind="verifier"),
           "print_omitido": lambda: _print(harness, "run-c", sid, redacted=True),
           "inexistente": lambda: 999_999}[caso]()
    with pytest.raises(RunError) as exc:
        st.runs.resolve("run-c", oid, ResolveBody(resolution="confirm_done", evidence_id=eid))
    assert exc.value.code == "invalid_evidence"
    assert st.db.one("SELECT status FROM steps WHERE id=?", (sid,))["status"] == "uncertain"


async def test_etapa_sem_efeito_externo_confirma_sem_print_e_guarda_quando_ha(harness: Harness) -> None:
    """Navegação parada (sem efeito externo): o print continua opcional — nada foi disparado fora da máquina."""
    st = harness.state
    assert st is not None
    oid, sid = _cenario_de_confirmacao(harness, side_effect=False)
    st.runs.resolve("run-c", oid, ResolveBody(resolution="confirm_done"))
    etapa = next(s for s in st.runs.repo.run_detail("run-c").steps if s.id == sid)  # type: ignore[union-attr]
    assert etapa.status == StepStatus.succeeded and etapa.result is not None and etapa.result.evidence_id is None
