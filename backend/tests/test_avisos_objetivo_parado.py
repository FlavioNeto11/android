"""28.40: o objetivo que ENTRA em `waiting_user` pedindo a pessoa avisa o dono uma vez, de qualquer origem (a folha de
tela não reconhecida do 29.87, a falta de informação, a política, a IA indisponível, o pré-voo). A aprovação fica de
fora (já sai como `approval.pending`); o lote de uma frente cala pelo mesmo predicado do `run.updated`; o aviso de conta
da mesma execução não se repete. Junto, as linhas do `unknown` no aviso de conta (29.92) e o "esperando você" no
desfecho dos canais.

Prova `simulated` (`arquivo::teste`): o montador puro, o serviço com banco de teste e canal falso, e o desfecho com o
repositório de mentira.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.db import Database
from app.models import RunStatus
from app.modules.avisos.domain.mensagem import (
    AGORA,
    GESTO_DO_OBJETIVO,
    LINHAS_DA_CONTA_PADRAO,
    MOTIVO_DA_PARADA,
    NIVEL_POR_TIPO,
    PRECISA_DE_VOCE,
    aviso_de_evento,
    entrega_do_tipo,
    titulo_agrupado,
)
from app.modules.avisos.infrastructure.portas_da_central import PortasReais
from app.modules.avisos.infrastructure.servico import KINDS_QUE_AVISAM
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.util import now, to_iso

from .test_avisos_servico import AQUI, CanalFalso, Relogio, _backend, _cfg

PAINEL = "https://painel.exemplo/central"
RUN = "r-20261005034000-abc123"
ROOT = Path(__file__).resolve().parents[1]
REDIGIR = TriagemDeCredencial().redigir
ESPERA = "2026-10-05T03:40:00.000Z"


def _obj(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {"id": "r1:o1", "run_id": "r1", "instance_id": "android-13", "status": "waiting_user",
                               "blocked_kind": None, "finished_at": ESPERA,
                               "status_detail": "a tela pede @fulana", "needs": "entre como fulana@exemplo.com",
                               "blocked_reason": "comentar no post da @fulana"}
    base.update(kw)
    return base


def _dados(obj: dict[str, object] | None = None, **kw: object) -> dict[str, object]:
    return {"objective": obj or _obj(), **kw}


# ===================================================================== 1. o montador
def test_o_objetivo_parado_diz_onde_e_o_gesto_e_sai_na_hora_com_o_link() -> None:
    a = aviso_de_evento("objective.updated", _dados(_obj(run_id=RUN)), 7, PAINEL)
    assert a is not None
    assert a.tipo == "objective.waiting_user" and a.chave == f"objective:r1:o1:{ESPERA}"
    assert a.titulo == "ANA: ✋ O objetivo no android-13 parou esperando você"
    assert a.corpo == ("Espera você: abra a execução no painel e, no item parado, escolha Assumir controle, Tentar "
                       "novamente ou Abandonar.")
    # O objetivo parado não está na caixa de Pendências (ADR-062, D1): o link é a própria execução, e o id não vai
    # ao texto.
    assert a.link == f"{PAINEL}/#/execucoes/{RUN}" and RUN not in a.titulo + a.corpo
    assert a.nivel == PRECISA_DE_VOCE and entrega_do_tipo(a.tipo) == AGORA


@pytest.mark.parametrize("run_id", ["r1", "r-2026/../x", None, "r-20261005034000-abc123?x=1"])
def test_id_de_execucao_fora_do_formato_abre_so_a_tela(run_id: object) -> None:
    a = aviso_de_evento("objective.updated", _dados(_obj(run_id=run_id)), 7, PAINEL)
    assert a is not None and a.link == f"{PAINEL}/#/execucoes"


def test_sem_base_publica_o_aviso_sai_sem_link() -> None:
    a = aviso_de_evento("objective.updated", _dados(_obj(run_id=RUN)), 7, "ftp://painel")
    assert a is not None and a.link is None


def test_sem_aparelho_o_assunto_e_generico() -> None:
    a = aviso_de_evento("objective.updated", _dados(_obj(instance_id=None)), 7)
    assert a is not None and a.titulo == "ANA: ✋ Um objetivo parou esperando você"


@pytest.mark.parametrize("obj", [
    _obj(blocked_kind="approval"),                    # a aprovação já sai como approval.pending
    _obj(status="running", finished_at=None),
    _obj(status="failed"),
    _obj(status="uncertain"),
    _obj(finished_at=None),                           # sem a entrada na espera não há chave do fato
    _obj(id=None),
])
def test_so_a_entrada_em_waiting_user_sem_aprovacao_avisa(obj: dict[str, object]) -> None:
    assert aviso_de_evento("objective.updated", _dados(obj), 7) is None


def test_uma_espera_uma_chave_e_a_espera_seguinte_e_outra() -> None:
    """A reemissão (`emit_objective`) repete o `finished_at`: a mesma chave, e a fila (`UNIQUE (chave)`) não repete a
    mensagem. O objetivo retomado que volta a esperar tem outra entrada, outra chave."""
    a = aviso_de_evento("objective.updated", _dados(), 7)
    b = aviso_de_evento("objective.updated", _dados(), 8)
    c = aviso_de_evento("objective.updated", _dados(_obj(finished_at="2026-10-05T05:00:00.000Z")), 9)
    assert a is not None and b is not None and c is not None
    assert a.chave == b.chave != c.chave


@pytest.mark.parametrize("failure_kind", sorted(MOTIVO_DA_PARADA.keys() - {"ai", "policy"}))
def test_o_failure_kind_escolhe_o_motivo(failure_kind: str) -> None:
    a = aviso_de_evento("objective.updated", _dados(failure_kind=failure_kind), 7)
    assert a is not None and a.corpo == f"{MOTIVO_DA_PARADA[failure_kind]}\n{GESTO_DO_OBJETIVO}"


def test_sem_failure_kind_vale_o_blocked_kind_e_o_failure_kind_ganha_dele() -> None:
    ia = aviso_de_evento("objective.updated", _dados(_obj(blocked_kind="ai")), 7)
    assert ia is not None and ia.corpo.startswith(MOTIVO_DA_PARADA["ai"])
    ambos = aviso_de_evento("objective.updated", _dados(_obj(blocked_kind="policy"), failure_kind="conta_errada"), 7)
    assert ambos is not None and ambos.corpo.startswith(MOTIVO_DA_PARADA["conta_errada"])


@pytest.mark.parametrize("failure_kind", ["ui_ocupada", None, "valor_novo_qualquer"])
def test_failure_kind_fora_do_mapa_sai_so_com_o_gesto(failure_kind: str | None) -> None:
    """O 29.87 puro manda `ui_ocupada`, e antes da classificação vem nulo: o texto genérico cobre os dois."""
    a = aviso_de_evento("objective.updated", _dados(failure_kind=failure_kind), 7)
    assert a is not None and a.corpo == GESTO_DO_OBJETIVO


def test_nunca_sai_o_detalhe_o_motivo_livre_nem_o_titulo_da_etapa() -> None:
    a = aviso_de_evento("objective.updated", _dados(acao="Comentar no post da @fulana", etapa="comentar @fulana",
                                                     command="comente no post da fulana"), 7, PAINEL, redigir=REDIGIR,
                        nomes=["Fulana"])
    assert a is not None
    texto = (a.titulo + a.corpo).lower()
    assert "fulana" not in texto and "exemplo.com" not in texto and "Etapa que espera" not in a.corpo


def test_a_acao_sai_pelo_nome_do_catalogo_filtrado_ou_pela_chave() -> None:
    nome = aviso_de_evento("objective.updated", _dados(acao="SEND_MESSAGE", acao_nome="Mandar mensagem"), 7,
                           redigir=REDIGIR)
    assert nome is not None and nome.corpo.startswith("Etapa que espera: Mandar mensagem.")
    chave = aviso_de_evento("objective.updated", _dados(acao="SEND_MESSAGE"), 7)
    assert chave is not None and chave.corpo.startswith("Etapa que espera: SEND_MESSAGE.")
    sujo = aviso_de_evento("objective.updated", _dados(acao="SEND_MESSAGE", acao_nome="Mandar a Fulana"), 7,
                           redigir=REDIGIR, nomes=["Fulana"])
    assert sujo is not None and "Fulana" not in sujo.corpo


def test_o_tipo_entra_nos_que_avisam_no_nivel_e_na_rajada() -> None:
    assert "objective.updated" in KINDS_QUE_AVISAM
    assert NIVEL_POR_TIPO["objective.waiting_user"] == PRECISA_DE_VOCE
    assert titulo_agrupado("objective.waiting_user", 3) == "ANA: 3 objetivos pararam esperando você"


# ===================================================================== 2. a conta em tela não reconhecida (29.92)
def test_a_conta_em_tela_nao_reconhecida_tem_as_tres_linhas_dela() -> None:
    a = aviso_de_evento("session.needs_person", {"instance_id": "android-13", "status": "unknown", "active": True,
                                                 "detail": "tela com @fulana"}, 11, PAINEL)
    assert a is not None
    # O link abre o Foco do aparelho, onde fica o "Assumir controle" (Pendências seria um clique a mais).
    assert a.link == f"{PAINEL}/#/painel?foco=android-13"
    assert a.titulo == "ANA: 🔐 android-13: tela de app não reconhecida; a automação parou sem tocar nela"
    linhas = a.corpo.split("\n")
    assert linhas[0] == "Nada foi tentado: nem voltar, nem entrar com a senha."
    assert linhas[1] == "A conta é real; a tela pode ser um aviso ou uma verificação."
    assert linhas[2].startswith("Espera você: no painel, use Assumir controle") and "Devolver à IA" in linhas[2]
    assert "fulana" not in a.titulo + a.corpo


@pytest.mark.parametrize("status", ["auth_challenge", "wrong_account", "outro"])
def test_as_outras_situacoes_da_conta_seguem_com_as_linhas_de_sempre(status: str) -> None:
    a = aviso_de_evento("session.needs_person", {"instance_id": "android-13", "status": status, "active": True}, 11,
                        PAINEL)
    assert a is not None and a.corpo.split("\n") == LINHAS_DA_CONTA_PADRAO
    assert a.link == f"{PAINEL}/#/pendencias", "as outras situações aparecem na caixa e seguem com o link dela"


def test_aparelho_fora_do_formato_abre_so_o_painel() -> None:
    a = aviso_de_evento("session.needs_person", {"instance_id": "android 13&x=1", "status": "unknown", "active": True},
                        11, PAINEL)
    assert a is not None and a.link == f"{PAINEL}/#/painel"


def test_toda_tela_de_link_de_aviso_existe_nas_rotas_do_painel() -> None:
    """Catraca: o link de um aviso nomeia uma tela de `TELAS` (`frontend/src/lib/rotas.ts`). Em 05/10 o 28.40 quase
    mandou o dono a `#/aparelhos`, que não existe."""
    rotas = (ROOT.parent / "frontend" / "src" / "lib" / "rotas.ts").read_text(encoding="utf-8")
    bloco = re.search(r"export const TELAS = \[(.*?)\] as const", rotas, re.S)
    assert bloco is not None, "TELAS sumiu de rotas.ts: atualize esta catraca"
    telas = set(re.findall(r"'([a-z]+)'", bloco.group(1)))
    usadas = {t for arq in (ROOT / "app" / "modules" / "avisos").rglob("*.py")
              for t in re.findall(r"#/([a-z]+)", arq.read_text(encoding="utf-8"))}
    assert usadas and "pendencias" in usadas and "execucoes" in usadas and "painel" in usadas
    assert usadas <= telas, f"tela de link que o painel não conhece: {sorted(usadas - telas)}"


# ===================================================================== 3. o serviço
def _run(banco: Database, run_id: str, *, chave: str, criada: str | None = None, prova: str | None = None) -> None:
    banco.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at, prova_fluxo_id)"
                  " VALUES (?,?,?,?,?,?,?,?)", (run_id, chave, "abrir o app", "single", "running", "[]",
                                                criada or to_iso(now()), prova))


def _conta(banco: Database, aparelho: str, *, ativa: bool, ts: str, persona: str | None = "p1",
           conta: str | None = None) -> None:
    dados: dict[str, object] = {"instance_id": aparelho, "status": "auth_challenge", "active": ativa}
    if persona is not None:
        dados["profile_id"] = persona
    if conta is not None:
        dados["account_id"] = conta
    banco.execute("INSERT INTO events(ts, kind, level, instance_id, message, data) VALUES (?,?,?,?,?,?)",
                  (ts, "session.needs_person", "warn", aparelho, "conta", json.dumps(dados)))


def _objetivo(banco: Database, run_id: str, *, persona: str | None = "p1", aparelho: str = "android-13") -> None:
    banco.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, profile_id) VALUES (?,?,?,?,?,?)",
                  (f"{run_id}:o1", run_id, aparelho, "waiting_user", 1, persona))


def _parou(servico: Any, run_id: str, *, aparelho: str = "android-13", desde: str = ESPERA, **kw: object) -> bool:
    return servico.enfileirar_evento("objective.updated", _dados(
        _obj(id=f"{run_id}:o1", run_id=run_id, instance_id=aparelho, finished_at=desde), **kw), 1)


def test_o_lote_de_frente_cala_pelo_mesmo_predicado_e_a_execucao_comum_avisa(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    _run(banco, "rl", chave="lote:canais:28.40")
    _run(banco, "rp", chave="k-prova", prova="fluxo-1")
    _run(banco, "rv", chave="validacao:p1")
    _run(banco, "rc", chave="telegram:9001")
    assert _parou(servico, "rl") is False, "o objetivo parado do lote de uma frente avisou o dono"
    assert _parou(servico, "rp") is False and _parou(servico, "rv") is False
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 0
    assert _parou(servico, "rc") is True
    assert banco.scalar("SELECT tipo FROM avisos_entregas") == "objective.waiting_user"


def test_uma_mensagem_por_parada_e_a_aprovacao_nao_entra(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    _run(banco, "rc", chave="k-comum")
    assert _parou(servico, "rc") is True
    assert _parou(servico, "rc") is False, "a reemissão da mesma espera virou outra mensagem"
    assert _parou(servico, "rc", desde="2026-10-05T06:00:00.000Z") is True, "a espera seguinte não avisou"
    aprovacao = _obj(id="rc:o2", run_id="rc", blocked_kind="approval")
    assert servico.enfileirar_evento("objective.updated", _dados(aprovacao), 2) is False
    assert banco.scalar("SELECT COUNT(*) FROM avisos_entregas") == 2


def test_o_aviso_de_conta_da_mesma_execucao_cala_o_objetivo(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    criada = now()
    _run(banco, "rc", chave="k-comum", criada=to_iso(criada))
    _objetivo(banco, "rc")
    _conta(banco, "android-13", ativa=True, ts=to_iso(criada + timedelta(seconds=30)))
    assert _parou(servico, "rc") is False, "a conta já avisou: o objetivo da mesma execução repetiu a mensagem"
    assert _parou(servico, "rc", aparelho="android-12") is True, "o aviso de conta de outro aparelho calou o objetivo"


@pytest.mark.parametrize(("persona_da_conta", "persona_do_objetivo"), [
    ("p2", "p1"),          # O1 da revisão do #372: a conta de OUTRA persona no mesmo aparelho
    (None, "p1"),          # o aviso de conta sem a persona
    ("p1", None),          # o objetivo sem a persona
])
def test_so_o_aviso_de_conta_da_mesma_persona_cala_o_objetivo(tmp_path: Path, persona_da_conta: str | None,
                                                              persona_do_objetivo: str | None) -> None:
    """Na dúvida, avisa: um aviso em dobro custa menos que uma parada muda (orquestradora, 05/10 04:06Z)."""
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    criada = now()
    _run(banco, "rc", chave="k-comum", criada=to_iso(criada))
    _objetivo(banco, "rc", persona=persona_do_objetivo)
    _conta(banco, "android-13", ativa=True, ts=to_iso(criada + timedelta(seconds=30)), persona=persona_da_conta)
    assert _parou(servico, "rc") is True


def _personas_e_contas(banco: Database) -> None:
    """Duas personas no mesmo aparelho; a p1 tem conta no Instagram e no Outlook (o caso da regra das 04:09Z)."""
    agora = to_iso(now())
    for pid in ("p1", "p2"):
        banco.execute("INSERT INTO instagram_profiles(id, username, created_at, updated_at) VALUES (?,?,?,?)",
                      (pid, f"u{pid}", agora, agora))
    for cid, pid, app in (("c-ig-p1", "p1", "instagram"), ("c-ol-p1", "p1", "outlook"), ("c-ig-p2", "p2", "instagram")):
        banco.execute("INSERT INTO profile_accounts(id, profile_id, app_id, handle, status, created_at, updated_at)"
                      " VALUES (?,?,?,?,?,?,?)", (cid, pid, app, f"h{cid}", "active", agora, agora))


def _etapa(banco: Database, run_id: str, seq: int, status: str, app: str | None) -> None:
    banco.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                  " postcondition, timeout_s, max_attempts, status, capability, app_id)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (f"{run_id}:s{seq}", run_id, f"{run_id}:o1", "android-13", 1, seq, f"e{seq}", "t", "g", "{}", 180, 3,
                   status, "SEND_MESSAGE", app))


@pytest.mark.parametrize(("conta_do_aviso", "etapas", "cala"), [
    ("c-ig-p1", [("waiting_user", "instagram")], True),            # a mesma conta: o aviso de conta basta
    ("c-ol-p1", [("waiting_user", "instagram")], False),           # a mesma persona, OUTRO app no mesmo aparelho
    ("c-ig-p2", [("waiting_user", "instagram")], False),           # OUTRA persona no mesmo aparelho
    ("c-ig-p1", [("waiting_user", None)], False),                  # a etapa não declara o app: conta desconhecida
    ("c-ig-p1", [("succeeded", "outlook"), ("pending", "instagram")], True),   # porta de sessão: antes da etapa
    ("c-ol-p1", [("succeeded", "instagram"), ("pending", "outlook")], True),
    ("c-ig-p1", [("succeeded", "instagram"), ("pending", "outlook")], False),
])
def test_com_a_conta_no_aviso_so_a_mesma_conta_cala_o_objetivo(tmp_path: Path, conta_do_aviso: str,
                                                               etapas: list[tuple[str, str | None]], cala: bool) -> None:
    """O1 do #372, regra das 04:09Z: o `profile_id` sozinho calaria o objetivo do Instagram por um aviso do Outlook
    da mesma persona."""
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    _personas_e_contas(banco)
    criada = now()
    _run(banco, "rc", chave="k-comum", criada=to_iso(criada))
    _objetivo(banco, "rc")
    for seq, (status, app) in enumerate(etapas, start=1):
        _etapa(banco, "rc", seq, status, app)
    _conta(banco, "android-13", ativa=True, ts=to_iso(criada + timedelta(seconds=30)), conta=conta_do_aviso)
    assert _parou(servico, "rc") is (not cala)


@pytest.mark.parametrize(("failure_kind", "com_etapa", "cala"), [
    ("autenticacao", True, True),
    ("conta_errada", True, True),
    ("falta_informacao", True, False),        # a mesma persona, outro motivo: avisa
    ("aviso_do_app", True, False),
    (None, True, False),
    (None, False, True),                      # o bloqueio sem etapa da porta de sessão
])
def test_so_com_a_persona_no_aviso_cala_so_o_motivo_de_conta(tmp_path: Path, failure_kind: str | None, com_etapa: bool,
                                                             cala: bool) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    criada = now()
    _run(banco, "rc", chave="k-comum", criada=to_iso(criada))
    _objetivo(banco, "rc")
    if com_etapa:
        _etapa(banco, "rc", 1, "waiting_user", "instagram")
    _conta(banco, "android-13", ativa=True, ts=to_iso(criada + timedelta(seconds=30)))
    assert _parou(servico, "rc", failure_kind=failure_kind) is (not cala)


def test_o_aviso_de_conta_de_uma_execucao_anterior_nao_cala_o_objetivo_novo(tmp_path: Path) -> None:
    """O teste que a orquestradora pediu (05/10): a aproximação de "mesma execução" não pode engolir o objetivo novo."""
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    agora = now()
    _conta(banco, "android-13", ativa=True, ts=to_iso(agora - timedelta(hours=3)))   # de uma execução de antes
    _run(banco, "rc", chave="k-comum", criada=to_iso(agora))
    _objetivo(banco, "rc")                                              # a mesma persona: só a hora discrimina
    assert _parou(servico, "rc") is True


def test_a_conta_que_ja_saiu_da_espera_nao_cala_o_objetivo(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    agora = now()
    _run(banco, "rc", chave="k-comum", criada=to_iso(agora))
    _objetivo(banco, "rc")
    _conta(banco, "android-13", ativa=True, ts=to_iso(agora + timedelta(seconds=10)))
    _conta(banco, "android-13", ativa=False, ts=to_iso(agora + timedelta(seconds=20)))
    assert _parou(servico, "rc") is True


def test_o_servico_poe_a_etapa_que_espera_pelo_nome_do_catalogo(tmp_path: Path) -> None:
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    servico._redigir = REDIGIR                                         # noqa: SLF001
    servico.nome_da_capability = {"SEND_MESSAGE": "Mandar mensagem"}.get
    _run(banco, "rc", chave="k-comum")
    banco.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version) VALUES (?,?,?,?,?)",
                  ("rc:o1", "rc", "android-13", "waiting_user", 1))
    for seq, (cap, status) in enumerate([("OPEN_APP", "succeeded"), ("SEND_MESSAGE", "waiting_user")], start=1):
        banco.execute("INSERT INTO steps(id, run_id, objective_id, instance_id, plan_version, seq, key, title, goal,"
                      " postcondition, timeout_s, max_attempts, status, capability)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"rc:s{seq}", "rc", "rc:o1", "android-13", 1, seq, f"e{seq}", "mandar para @fulana",
                       "mandar para @fulana", "{}", 180, 3, status, cap))
    assert _parou(servico, "rc") is True
    corpo = str(banco.scalar("SELECT corpo FROM avisos_entregas"))
    assert corpo.startswith("Etapa que espera: Mandar mensagem.") and "fulana" not in corpo


def test_banco_fora_na_conferencia_o_aviso_sai_mesmo_assim(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Na dúvida, avisa: o dono recebe um aviso a mais, nunca perde um de pessoa."""
    servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio(), canal=CanalFalso())
    _run(banco, "rc", chave="k-comum")
    leitura = banco.one

    def one(sql: str, params: tuple[object, ...] = ()) -> object:
        if "FROM events" in sql or "FROM steps" in sql:
            raise RuntimeError("banco fora")
        return leitura(sql, params)

    monkeypatch.setattr(banco, "one", one)
    assert _parou(servico, "rc") is True


# ===================================================================== 4. o desfecho nos canais
def _portas(status: RunStatus, **contagens: int) -> PortasReais:
    counts = SimpleNamespace(**{k: contagens.get(k, 0) for k in
                                ("succeeded", "failed", "waiting_user", "uncertain", "cancelled", "running", "pending")})
    repo = SimpleNamespace(run_row=lambda _rid: {"status": status.value},
                           run_summary=lambda _row: SimpleNamespace(counts=counts, short_id="abc123", status_detail=None))
    db = SimpleNamespace(scalar=lambda *_a, **_k: None)
    return PortasReais(db=db, runs=SimpleNamespace(repo=repo), aprovacoes=None, saude=lambda: None,  # type: ignore[arg-type]
                       online=lambda: [])


def test_o_desfecho_diz_quantos_esperam_voce_e_o_gesto() -> None:
    texto = _portas(RunStatus.completed_with_issues, succeeded=1, waiting_user=2).desfecho("r1")
    assert texto == ("Execução abc123: concluída com problemas.\n"
                     "Objetivos: 1 de 3 com sucesso, 2 esperando você.\n" + GESTO_DO_OBJETIVO)


def test_sem_objetivo_esperando_o_desfecho_fica_como_era() -> None:
    texto = _portas(RunStatus.completed, succeeded=2).desfecho("r1")
    assert texto == "Execução abc123: concluída.\nObjetivos: 2 de 2 com sucesso."


def test_o_motivo_livre_do_objetivo_parado_nao_vira_evidencia(tmp_path: Path) -> None:
    """O2 da revisão do #372: o objetivo parado tem o `finished_at` mais novo, e o `status_detail` dele traz texto de
    tela ou de conta. A evidência é a do objetivo terminado; sem um, nenhuma."""
    _servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio())
    _run(banco, "r1", chave="k-comum")
    for oid, aparelho, status, detalhe, fim in (
            ("r1:o1", "android-12", "succeeded", "mensagem comprovada na tela", "2026-10-05T03:00:00.000Z"),
            ("r1:o2", "android-13", "waiting_user", "MARCADOR tela pede @fulana", "2026-10-05T03:30:00.000Z")):
        banco.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, status_detail, finished_at)"
                      " VALUES (?,?,?,?,?,?,?)", (oid, "r1", aparelho, status, 1, detalhe, fim))
    portas = _portas(RunStatus.completed_with_issues, succeeded=1, waiting_user=1)
    portas.db = banco
    texto = portas.desfecho("r1") or ""
    assert "MARCADOR" not in texto and "fulana" not in texto
    assert "Evidência: mensagem comprovada na tela" in texto
    banco.execute("UPDATE objectives SET status='waiting_user' WHERE id='r1:o1'")
    texto = portas.desfecho("r1") or ""
    assert "Evidência" not in texto and "MARCADOR" not in texto


def test_a_evidencia_e_de_objetivo_que_terminou_nos_dois_bancos(tmp_path: Path) -> None:
    """N3 da revisão do #372: na execução falha, o objetivo ainda aberto (sem `finished_at`) não é a evidência. O `DESC`
    põe `NULL` primeiro no PostgreSQL e por último no SQLite: sem o filtro, os dois bancos escolheriam diferente."""
    _servico, banco, _ = _backend(_cfg(tmp_path), AQUI, Relogio())
    _run(banco, "r1", chave="k-comum")
    for oid, aparelho, status, detalhe, fim in (
            ("r1:o1", "android-12", "failed", "a falha que terminou", "2026-10-05T03:00:00.000Z"),
            ("r1:o2", "android-13", "running", "MARCADOR ainda aberto", None)):
        banco.execute("INSERT INTO objectives(id, run_id, instance_id, status, plan_version, status_detail, finished_at)"
                      " VALUES (?,?,?,?,?,?,?)", (oid, "r1", aparelho, status, 1, detalhe, fim))
    portas = _portas(RunStatus.failed, failed=1, running=1)
    portas.db = banco
    texto = portas.desfecho("r1") or ""
    assert "Evidência: a falha que terminou" in texto and "MARCADOR" not in texto
