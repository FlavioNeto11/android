"""30.60: CREATE_POST endurecido (revisão do deploy 29, achados 1, 2, 3, 5 e 6, mais N1, N3 e N4).

- a prova por contagem confere o PRÓPRIO perfil (`count_gt` com guarda) e não fecha o efeito sem o modelo;
- a galeria fica só com a imagem da etapa, e a indexação é conferida no MediaStore;
- a persona do objetivo precisa estar vinculada ao aparelho;
- a aprovação reaproveitada compara a imagem (N1), a edição só grava se a decisão venceu (N3), e publicar passa por
  uma pessoa mesmo com perfil autônomo (N4).

Tudo `simulated`: aparelho falso, `adb` por dublê de `_run`, banco SQLite (ou o PG da suíte).
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.devices import adb as adb_mod
from app.devices.adb import Adb, AdbError
from app.models import Postcondition, StepDTO, StepStatus
from app.modules.capabilities.domain.definition import CapabilityRef
from app.planning.capabilities import capability_of, load_catalog
from app.planning.provider import Usage, Verdict, VerifyRequest
from app.taskqueue.executor import Outcome
from app.taskqueue.proofs import local_proof_holds

from .fake_instagram import FakeInstagram
from .test_capabilities import IG, build, perfil
from .test_create_post import _despachar, _executor, _Imagens

PKG = "com.instagram.android"
IID = "android-01"
CONTA = "tadeu.quintela4821"
CONTAGEM = "id=profile_header_familiar_post_count_value"


def _perfil(contagem: str, titulo: str | None = CONTA, *, postando: bool = False) -> UiTree:
    nos = [f'<node class="android.widget.TextView" text="{contagem}" resource-id="{PKG}:id/'
           'profile_header_familiar_post_count_value" bounds="[0,100][100,150]"/>']
    if titulo is not None:
        nos.append(f'<node class="android.widget.TextView" text="{titulo}" resource-id="{PKG}:id/action_bar_title"'
                   ' bounds="[0,0][400,60]"/>')
    if postando:
        nos.append('<node class="android.widget.TextView" text="Posting…" bounds="[0,200][400,250]"/>')
    return parse_hierarchy("<hierarchy>" + "".join(nos) + "</hierarchy>")


def _etapa(**bindings: str) -> Any:
    return SimpleNamespace(bindings=dict(bindings), card_guard=[], band_guard=[])


# =========================================================================== achado 1: a prova do próprio perfil
def test_o_catalogo_declara_a_guarda_do_proprio_perfil_e_as_marcas() -> None:
    cap = capability_of(PKG, "CREATE_POST")
    assert cap is not None and cap.risk == "high"
    assert cap.local_proof.startswith("count_gt:posts_antes:") and "{account_label}" in cap.local_proof
    assert any("Posting" in m for m in cap.pending_marks)
    assert any("post" in m.lower() for m in cap.failure_marks)


@pytest.mark.parametrize("titulo, conta, esperado", [
    (CONTA, CONTA, True),                     # o próprio perfil, contagem maior
    ("outra.pessoa", CONTA, None),            # perfil de outra conta: não afirma (cai para o modelo), nunca reprova
    (None, CONTA, None),                      # sem o título na tela
    (CONTA, None, None),                      # conta esperada desconhecida: variável sem valor
])
def test_count_gt_com_guarda_so_afirma_no_proprio_perfil(titulo: str | None, conta: str | None,
                                                          esperado: bool | None) -> None:
    prova = capability_of(PKG, "CREATE_POST").local_proof            # type: ignore[union-attr]
    argumentos = {"posts_antes": "12", **({"account_label": conta} if conta else {})}
    assert local_proof_holds(prova, _etapa(**argumentos), _perfil("13", titulo)) is esperado


def test_a_guarda_nao_vira_reprovacao_quando_a_contagem_nao_subiu() -> None:
    prova = f"count_gt:posts_antes:{CONTAGEM}&id=action_bar_title|text=={{account_label}}"
    assert local_proof_holds(prova, _etapa(posts_antes="13", account_label=CONTA), _perfil("13")) is False


class _Aparelho:
    def __init__(self, telas: list[UiTree]) -> None:
        self.telas, self.leituras = telas, 0

    async def observe(self, rt: object, *, timeout: float, imagem: bool) -> Any:
        from app.devices.manager import Observation
        tela = self.telas[min(self.leituras, len(self.telas) - 1)]
        self.leituras += 1
        return Observation(frame_id=str(self.leituras), ts="2026-10-04T18:00:00Z", width=720, height=1280,
                           jpeg=None, tree=tela, package=PKG, sensitive=False)

    async def completar_imagem(self, rt: object, obs: Any, *, timeout: float, lado_max: int) -> Any:
        return obs


class _Verificador:
    def __init__(self, resposta: str = "yes") -> None:
        self.chamadas: list[VerifyRequest] = []
        self.resposta = resposta

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        self.chamadas.append(req)
        return Verdict(satisfied=self.resposta, evidence="[teste] o post aparece no perfil"), Usage()


def _executor_de_verificacao(tmp_path: Path, telas: list[UiTree], verificador: _Verificador) -> Any:
    from app.modules.capabilities.infrastructure.catalog_provider import CatalogCapabilityProvider
    from app.modules.capabilities.infrastructure.catalog_registry import CatalogCapabilityRegistry
    from app.taskqueue.executor import StepExecutor

    from .conftest import make_config

    ex = object.__new__(StepExecutor)
    ex.cfg = make_config(tmp_path, 1)
    ai = ex.cfg.file.ai
    ai.verify_budget_min_s = ai.verify_budget_s = ai.verify_budget_patient_s = 1.5
    ai.rejudge_yes_on_side_effect = False            # isola o ponto: a prova local não fecha sem o modelo
    ex.repo = SimpleNamespace(decision=lambda *a, **k: None)
    ex.devices = _Aparelho(telas)
    ex.provider = verificador
    ex.capabilities = CatalogCapabilityProvider(CatalogCapabilityRegistry(lambda _app: None))
    ex._mensagens_antes = {}                          # 31.59: sem linha de base; quem prova envio a declara

    async def _ai(run_id: str, objective_id: str | None, fabrica: Any, **_kw: Any) -> Verdict:
        resultado, _uso = await fabrica()
        return resultado  # type: ignore[no-any-return]

    ex._ai = _ai
    return ex



def _publicacao() -> StepDTO:
    cap = capability_of(PKG, "CREATE_POST")
    assert cap is not None
    return StepDTO(id=f"r-p:{IID}:v1:post", run_id="r-p", objective_id=f"r-p:{IID}", instance_id=IID, plan_version=1,
                   seq=3, key="post", title="Publicar", goal=cap.goal, depends_on=[], side_effect=True,
                   commit_guard=[], postcondition=Postcondition(kind="model_judged", value="publicado",
                                                                description=cap.post_description),
                   timeout_s=60, max_attempts=1, attempts=1, status=StepStatus.verifying, capability="CREATE_POST",
                   commit_selector=cap.commit_selector,
                   bindings={"posts_antes": "12", "image_id": "img-ok", "content": "Fim de tarde"})


async def _verificar(ex: Any, etapa: StepDTO) -> tuple[bool, str]:
    cap = capability_of(PKG, "CREATE_POST")
    assert cap is not None
    ok, texto, *_ = await ex._verify(
        SimpleNamespace(id=IID), etapa,
        lambda: SimpleNamespace(step_key=etapa.key, instance_id=IID, account_label=CONTA),
        "r-p", f"r-p:{IID}", time.monotonic() + 1.0, 5.0, patient=True, facts=[],
        failure_marks=tuple(cap.failure_marks), local_proof=cap.local_proof,
        capability=CapabilityRef(PKG, "CREATE_POST"), pacote=PKG)
    return ok, texto


async def test_a_contagem_do_proprio_perfil_vira_fato_e_o_modelo_confere(tmp_path: Path) -> None:
    verificador = _Verificador("yes")
    ex = _executor_de_verificacao(tmp_path, [_perfil("13")], verificador)
    ok, texto = await _verificar(ex, _publicacao())
    assert ok, texto
    assert len(verificador.chamadas) == 1                       # antes: atalho, zero chamadas
    assert any("prova local" in f for f in (verificador.chamadas[0].facts or []))
    assert "sem IA" not in texto


async def test_o_modelo_dizendo_nao_mantem_a_publicacao_sem_prova(tmp_path: Path) -> None:
    verificador = _Verificador("no")
    ex = _executor_de_verificacao(tmp_path, [_perfil("13")], verificador)
    ok, _texto = await _verificar(ex, _publicacao())
    assert not ok


async def test_posting_na_tela_nunca_fecha_como_publicado(tmp_path: Path) -> None:
    verificador = _Verificador("yes")
    ex = _executor_de_verificacao(tmp_path, [_perfil("13", postando=True)], verificador)
    ok, _texto = await _verificar(ex, _publicacao())
    assert not ok


async def test_a_dm_continua_com_o_atalho_do_sent_text(tmp_path: Path) -> None:
    """SEND_MESSAGE também é `risk: high`, mas o critério dela é objetivo (ADR-055): o 30.60 não o tira."""
    from .test_dm_verificador import _conversa, _envio
    from .test_dm_verificador import _verificar as verificar_dm

    verificador = _Verificador("yes")
    ex = _executor_de_verificacao(tmp_path, [_conversa()], verificador)
    ex._mensagens_antes = {_envio().id: 0}           # 31.59: a tela do toque não tinha a bolha
    ok, texto = await verificar_dm(ex, _envio())
    assert ok, texto
    assert verificador.chamadas == []


# ============================================================================ achado 6: a persona do aparelho
def test_persona_sem_vinculo_com_o_aparelho_e_recusada_sem_push() -> None:
    aparelho = FakeInstagram()
    ex, rt, repo = _executor(_Imagens(), aparelho)
    ex.social = SimpleNamespace(repo=SimpleNamespace(binding=lambda pid, iid: None))
    desfecho = _despachar(ex, rt, "img-ok")
    assert desfecho.outcome == Outcome.failed and "não está vinculada" in (desfecho.detail or "")
    assert aparelho.midias_na_galeria == [] and not repo.transicoes


def test_vinculo_secundario_basta() -> None:
    """android-13 é também do Ravenna: pertencer ao aparelho basta, não precisa ser a única persona dele."""
    aparelho = FakeInstagram()
    ex, rt, _repo = _executor(_Imagens(), aparelho)
    vistos: list[tuple[str, str]] = []
    ex.social = SimpleNamespace(repo=SimpleNamespace(binding=lambda pid, iid: vistos.append((pid, iid)) or {"id": 1}))
    desfecho = _despachar(ex, rt, "img-ok")
    assert desfecho.outcome == Outcome.succeeded and vistos == [("p1", "android-02")]


# ============================================================== achados 2, 3 e 5: a galeria pelo `Adb` de verdade
class _AdbDeMentira:
    """Dublê do `_run` do `Adb`: grava cada comando e responde o `ls` e o `content query` como o aparelho."""

    def __init__(self, *, sobra: list[str] | None = None, indexa: bool = True, push_ok: bool = True) -> None:
        self.comandos: list[str] = []
        self.sobra, self.indexa, self.push_ok = sobra or [], indexa, push_ok
        self.pasta: list[str] = []

    def __call__(self, args: list[str], *, timeout: float = 30, **_kw: Any) -> Any:
        cmd = " ".join(args)
        self.comandos.append(cmd)
        saida, codigo = "", 0
        if args[0] == "push":
            codigo = 0 if self.push_ok else 1
            if self.push_ok:
                self.pasta.append(args[2].rsplit("/", 1)[1])
        elif args[0] == "shell" and args[1].startswith("rm -f"):
            self.pasta = []                                # a limpeza da nossa pasta
        elif args[0] == "shell" and args[1].startswith("ls "):
            saida = "\n".join([*self.sobra, *self.pasta])
        elif args[0] == "shell" and args[1].startswith("content query"):
            saida = "Row: 0 _id=42" if self.indexa else "No result found."
        return SimpleNamespace(returncode=codigo, stdout=saida, stderr="")


def _adb(monkeypatch: pytest.MonkeyPatch, dublê: _AdbDeMentira) -> Adb:
    a = Adb.__new__(Adb)
    a.serial = "emulator-5554"
    monkeypatch.setattr(a, "_run", dublê)
    monkeypatch.setattr(adb_mod.time, "sleep", lambda _s: None)
    return a


def test_a_galeria_e_limpa_antes_do_push_e_a_indexacao_e_conferida(monkeypatch: pytest.MonkeyPatch) -> None:
    dublê = _AdbDeMentira()
    caminho = _adb(monkeypatch, dublê).enviar_midia_para_galeria("C:/tmp/x.jpg", "img_abc")
    assert caminho == "/sdcard/Pictures/Central/img_abc.jpg"
    ordem = [c.split(" ")[0] if not c.startswith("shell") else c.split(" ")[1] for c in dublê.comandos]
    assert ordem == ["mkdir", "content", "rm", "push", "ls", "am", "content"]
    assert "content delete" in dublê.comandos[1] and "/Pictures/Central/img_" in dublê.comandos[1]
    assert "/Pictures/Central/img_abc.jpg" in dublê.comandos[-1]


def test_sobra_na_pasta_impede_a_etapa(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um arquivo que a limpeza não tirou (outro nome) fica na pasta: a etapa falha antes de o editor abrir."""
    dublê = _AdbDeMentira(sobra=["foto_antiga.jpg"])
    with pytest.raises(AdbError, match="só com a imagem da etapa"):
        _adb(monkeypatch, dublê).enviar_midia_para_galeria("C:/tmp/x.jpg", "img_abc")
    assert not any(c.startswith("shell am broadcast") for c in dublê.comandos)


def test_sem_indexacao_a_etapa_falha(monkeypatch: pytest.MonkeyPatch) -> None:
    dublê = _AdbDeMentira(indexa=False)
    with pytest.raises(AdbError, match="não a indexou"):
        _adb(monkeypatch, dublê).enviar_midia_para_galeria("C:/tmp/x.jpg", "img_abc")
    assert sum(c.startswith("shell content query") for c in dublê.comandos) == 5


def test_push_que_falha_nao_chega_a_conferir(monkeypatch: pytest.MonkeyPatch) -> None:
    dublê = _AdbDeMentira(push_ok=False)
    with pytest.raises(AdbError, match="push"):
        _adb(monkeypatch, dublê).enviar_midia_para_galeria("C:/tmp/x.jpg", "img_abc")


# ============================================================================= N1 e N3: a aprovação e a imagem
def _cenario(tmp_path: Path) -> Any:
    from .fake_skills import banco

    db = banco(tmp_path, "aprovacao.sqlite3")
    db.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, simulated, instance_ids, created_at)"
               " VALUES ('r-1', 'k-1', 'publicar', 'execute', 'running', 0, '[\"android-01\"]',"
               " '2026-10-04T15:00:00Z')")
    db.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version)"
               " VALUES ('r-1:o', 'r-1', 'android-01', 'running', 2)")
    return db


def _etapa_no_banco(db: Any, sid: str, versao: int, imagem: str) -> None:
    db.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
               " side_effect, postcondition, timeout_s, max_attempts, status, bindings)"
               " VALUES (?, 'r-1', 'r-1:o', 'android-01', ?, 1, 'post', 't', 'g', 1, '{}', 60, 1, 'pending', ?)",
               (sid, versao, f'{{"image_id": "{imagem}", "content": "Fim de tarde"}}'))


@pytest.mark.parametrize("imagem_nova, reaproveita", [("img-1", True), ("img-2", False)])
def test_a_revisao_so_reaproveita_a_aprovacao_com_a_mesma_imagem(tmp_path: Path, imagem_nova: str,
                                                                  reaproveita: bool) -> None:
    from app.social.approvals import ApprovalStore

    db = _cenario(tmp_path)
    try:
        _etapa_no_banco(db, "r-1:v1:post", 1, "img-1")
        _etapa_no_banco(db, "r-1:v2:post", 2, imagem_nova)
        store = ApprovalStore(db)
        pedido = store.open(profile_id=None, capability="CREATE_POST", summary="Publicar", step_id="r-1:v1:post",
                            content="Fim de tarde")
        store.decide(pedido.id, status="approved")
        achada = store.acompanhar_revisao("r-1:v2:post", profile_id=None,
                                          acao=load_catalog("com.instagram.android").get("CREATE_POST"), target=None,
                                          content="Fim de tarde", disparou=lambda _s: False)
        assert (achada is not None) is reaproveita
    finally:
        db.close()


def test_a_edicao_que_perde_a_corrida_nao_grava_o_texto(tmp_path: Path) -> None:
    from app.social.approvals import ApprovalService, ApprovalStore
    from app.social.service import SocialError

    db = _cenario(tmp_path)
    try:
        _etapa_no_banco(db, "r-1:v2:post", 2, "img-1")
        store = ApprovalStore(db)
        pedido = store.open(profile_id=None, capability="CREATE_POST", summary="Publicar", step_id="r-1:v2:post",
                            content="Fim de tarde")
        foto = store.get(pedido.id)                       # quem edita leu `pending`...
        store.decide(pedido.id, status="approved")        # ...e outra pessoa aprovou antes
        servico = ApprovalService(store, SimpleNamespace(db=db))
        servico.store = SimpleNamespace(get=lambda _id: foto, decide=store.decide)
        with pytest.raises(SocialError):
            servico.decide(pedido.id, "edit", content="Outro texto")
        assert "Outro texto" not in (db.scalar("SELECT bindings FROM steps WHERE id='r-1:v2:post'") or "")
    finally:
        db.close()


# ===================================================================================== N4: o piso de publicar
def test_perfil_autonomo_nao_publica_sem_aprovacao(tmp_path: Path) -> None:
    svc, repo, policies, _db = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"capabilities": {"CREATE_POST": "autonomous"},'
                                                   ' "limits": {"cooldown_between_external_actions_s": 0}}'})
    post = load_catalog(IG).get("CREATE_POST")
    assert policies.policy_for(pid, post) == "autonomous"
    veredito = policies.check(pid, post, run_id="run-n4")
    assert veredito.allowed and veredito.needs_approval and "30.60" in veredito.reason


def test_so_a_instalacao_afrouxa_o_piso_de_publicar(tmp_path: Path) -> None:
    from app.social.policy import PolicyEngine

    svc, repo, _policies, _db = build(tmp_path)
    pid = perfil(svc)
    repo.update_profile(pid, {"automation_policy": '{"capabilities": {"CREATE_POST": "autonomous"},'
                                                   ' "limits": {"cooldown_between_external_actions_s": 0}}'})
    policies = PolicyEngine(repo, settings_getter=lambda: SimpleNamespace(publicar_sem_aprovacao=True))
    veredito = policies.check(pid, load_catalog(IG).get("CREATE_POST"), run_id="run-n4b")
    assert veredito.allowed and not veredito.needs_approval
