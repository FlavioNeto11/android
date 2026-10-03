"""Item 32.2 (2/6) — a config `trello:` (desligada de fábrica), os segredos no `.env`, a saúde e a migração 087.

Prova `simulated`: SQLite por padrão; com `TEST_DATABASE_URL` a mesma fábrica abre o PostgreSQL.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr, ValidationError

from app.config import AppConfigFile, Config, EnvSettings, TrelloCfg, load_config
from app.db import INTEGRITY_ERRORS, Database
from app.modules.avisos.infrastructure.trello_saude import problemas_do_trello

from .conftest import make_config

RAIZ = Path(__file__).resolve().parents[2]
EXEMPLO = RAIZ / "config" / "config.example.yaml"


def _cfg(tmp_path: Path) -> Config:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    return cfg


# --------------------------------------------------------------------- padrões
def test_tudo_desligado_e_vazio_de_fabrica() -> None:
    t = AppConfigFile().trello
    assert t.enabled is False and t.comando_livre is False and t.responder_convidados is False
    assert t.quadros == [] and t.listas == {} and t.membros_autorizados == [] and t.membro_dono == ""
    assert (t.espelho_s, t.reconciliar_s, t.idade_max_s) == (60.0, 60.0, 900.0)
    assert t.webhook.enabled is False and t.webhook.callback_url is None and t.webhook.max_bytes == 262144


def test_os_padroes_nao_sao_compartilhados_entre_instancias() -> None:
    a, b = TrelloCfg(), TrelloCfg()
    a.quadros.append("q1")
    a.listas["aprovado"] = "l1"
    assert b.quadros == [] and b.listas == {}


def test_a_config_aceita_o_bloco_e_barra_o_absurdo() -> None:
    t = AppConfigFile.model_validate({"trello": {
        "enabled": True, "quadros": ["q1"], "listas": {"central_automatico": "l0", "aprovado": "l1", "vetado": "l2"},
        "membro_dono": "m1", "reconciliar_s": 300, "webhook": {"enabled": True, "callback_url": "https://x.test/h"},
    }}).trello
    assert t.enabled and t.listas["vetado"] == "l2" and t.reconciliar_s == 300 and t.webhook.enabled
    for ruim in ({"espelho_s": 0}, {"reconciliar_s": 5}, {"idade_max_s": 10}, {"webhook": {"max_bytes": 10}}):
        with pytest.raises(ValidationError):
            AppConfigFile.model_validate({"trello": ruim})


def test_listas_so_aceita_os_papeis_do_vocabulario() -> None:
    ok = {"central_automatico": "l0", "aprovado": "l1", "vetado": "l2"}
    assert AppConfigFile.model_validate({"trello": {"listas": ok}}).trello.listas == ok
    assert AppConfigFile.model_validate({"trello": {"listas": {"vetado": "l2"}}}).trello.listas == {"vetado": "l2"}
    with pytest.raises(ValidationError) as e:
        AppConfigFile.model_validate({"trello": {"listas": {"aprovdo": "l1"}}})   # o erro de digitação não passa em silêncio
    assert "aprovdo" in str(e.value)
    with pytest.raises(ValidationError):
        AppConfigFile.model_validate({"trello": {"listas": {"aprovado": "l1", "em_validacao": "l9"}}})


def test_o_exemplo_traz_o_bloco_desligado_e_carrega() -> None:
    bruto = yaml.safe_load(EXEMPLO.read_text(encoding="utf-8"))
    assert bruto["trello"] == {"enabled": False}, "só o `enabled: false` está ativo; o resto vem comentado"
    assert load_config(EXEMPLO).file.trello.enabled is False
    texto = EXEMPLO.read_text(encoding="utf-8")
    for chave in ("quadros", "listas", "central_automatico", "membro_dono", "espelho_s", "reconciliar_s", "comando_livre",
                  "idade_max_s", "membros_autorizados", "responder_convidados", "webhook", "callback_url", "max_bytes"):
        assert chave in texto.split("trello:", 1)[1].split("provisioning:", 1)[0], chave


# --------------------------------------------------------------------- segredos
def test_os_segredos_vem_do_ambiente_com_nome_fixo_e_nao_aparecem_no_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRELLO_API_KEY", "chave-falsa")
    monkeypatch.setenv("TRELLO_TOKEN", "token-falso")
    monkeypatch.setenv("TRELLO_API_SECRET", "segredo-falso")
    env = EnvSettings(_env_file=None)
    assert env.trello_api_key and env.trello_api_key.get_secret_value() == "chave-falsa"
    assert env.trello_token and env.trello_token.get_secret_value() == "token-falso"
    assert env.trello_api_secret and env.trello_api_secret.get_secret_value() == "segredo-falso"
    mostrado = repr(env)
    assert "chave-falsa" not in mostrado and "token-falso" not in mostrado and "segredo-falso" not in mostrado


def test_sem_o_ambiente_os_segredos_sao_none(monkeypatch: pytest.MonkeyPatch) -> None:
    for nome in ("TRELLO_API_KEY", "TRELLO_TOKEN", "TRELLO_API_SECRET"):
        monkeypatch.delenv(nome, raising=False)
    env = EnvSettings(_env_file=None)
    assert env.trello_api_key is None and env.trello_token is None and env.trello_api_secret is None


# --------------------------------------------------------------------- saúde
def test_desligado_nao_e_problema_mesmo_sem_segredo(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    assert problemas_do_trello(cfg) == []


def test_trello_sem_segredo_aparece_e_some(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.file.trello.enabled = True
    cfg.env.trello_api_key = cfg.env.trello_token = None
    (p,) = problemas_do_trello(cfg)
    assert p.code == "trello_sem_segredo" and "TRELLO_API_KEY" in p.message and "TRELLO_TOKEN" in p.message
    cfg.env.trello_api_key = SecretStr("chave-falsa")
    (p,) = problemas_do_trello(cfg)
    assert "TRELLO_TOKEN" in p.message and "TRELLO_API_KEY" not in p.message
    cfg.env.trello_token = SecretStr("   ")                       # só espaços não é token
    assert [p.code for p in problemas_do_trello(cfg)] == ["trello_sem_segredo"]
    cfg.env.trello_token = SecretStr("token-falso")
    assert problemas_do_trello(cfg) == []


def test_o_problema_nunca_leva_valor(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.file.trello.enabled = True
    cfg.env.trello_api_key = SecretStr("chave-falsa")
    cfg.file.trello.webhook.enabled = True
    cfg.file.trello.webhook.callback_url = None
    cfg.env.trello_api_secret = SecretStr("segredo-falso")
    for p in problemas_do_trello(cfg):
        assert "chave-falsa" not in p.message + p.hint and "segredo-falso" not in p.message + p.hint


def test_webhook_sem_segredo_aparece_e_some(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.file.trello.webhook.enabled = True
    cfg.env.trello_api_secret = None
    (p,) = problemas_do_trello(cfg)                               # `trello.enabled` desligado: o webhook é independente
    assert p.code == "trello_webhook_sem_segredo"
    assert "TRELLO_API_SECRET" in p.message and "callback_url" in p.message
    cfg.env.trello_api_secret = SecretStr("segredo-falso")
    (p,) = problemas_do_trello(cfg)
    assert "callback_url" in p.message and "TRELLO_API_SECRET" not in p.message
    cfg.file.trello.webhook.callback_url = "https://central.exemplo.test/api/canais/trello/webhook"
    assert problemas_do_trello(cfg) == []
    cfg.file.trello.webhook.enabled = False
    cfg.env.trello_api_secret = None
    assert problemas_do_trello(cfg) == []


def test_os_dois_problemas_juntos(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    cfg.file.trello.enabled = True
    cfg.file.trello.webhook.enabled = True
    cfg.env.trello_api_key = cfg.env.trello_token = cfg.env.trello_api_secret = None
    assert [p.code for p in problemas_do_trello(cfg)] == ["trello_sem_segredo", "trello_webhook_sem_segredo"]


# --------------------------------------------------------------------- migração 087
def _banco(tmp_path: Path) -> Database:
    db = Database(_cfg(tmp_path).db_dsn)          # a fábrica configurada, nunca `Database(caminho)` direto
    db.migrate()
    return db


def test_a_087_cria_as_tabelas_e_registra_a_impressao(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        assert {"chave", "card_id", "quadro", "lista", "hash", "estado", "criado_em", "atualizado_em"} \
            == set(db.columns("trello_cartoes"))
        assert {"quadro", "ultima_action", "ultima_data", "atualizado_em"} == set(db.columns("trello_cursor"))
        linha = db.one("SELECT checksum FROM schema_migrations WHERE version=?", ("087_trello",))
        assert linha is not None and linha["checksum"] and len(linha["checksum"]) == 64
        assert db.divergencias() == [] and db.migrate() == [], "reaplicar não muda nada"
    finally:
        db.close()


def test_a_087_nao_toca_nas_tabelas_da_085(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    try:
        assert db.columns("canal_entradas") and db.columns("canal_enviadas")
    finally:
        db.close()


def test_card_id_e_unico_e_a_chave_e_a_primaria(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    sql = ("INSERT INTO trello_cartoes(chave, card_id, quadro, lista, hash, estado, criado_em, atualizado_em) "
           "VALUES (?,?,?,?,?,?,?,?)")
    agora = "2026-10-03T21:00:00Z"
    try:
        db.execute(sql, ("approval:1", "c1", "q1", "l1", "h1", "ativo", agora, agora))
        with pytest.raises(INTEGRITY_ERRORS):                      # mesma chave
            db.execute(sql, ("approval:1", "c2", "q1", "l1", "h1", "ativo", agora, agora))
        with pytest.raises(INTEGRITY_ERRORS):                      # mesmo cartão para outro fato
            db.execute(sql, ("approval:2", "c1", "q1", "l1", "h1", "ativo", agora, agora))
        db.execute(sql, ("approval:3", "c3", "q1", None, None, "arquivado", agora, agora))   # lista/hash nulos valem
        db.execute("INSERT INTO trello_cursor(quadro, ultima_action, ultima_data, atualizado_em) VALUES (?,?,?,?)",
                   ("q1", "a1", agora, agora))
        db.execute("INSERT INTO trello_cursor(quadro, atualizado_em) VALUES (?,?)", ("q2", agora))
        with pytest.raises(INTEGRITY_ERRORS):
            db.execute("INSERT INTO trello_cursor(quadro, atualizado_em) VALUES (?,?)", ("q2", agora))
        assert db.one("SELECT COUNT(*) AS n FROM trello_cartoes")["n"] == 2
    finally:
        db.close()
