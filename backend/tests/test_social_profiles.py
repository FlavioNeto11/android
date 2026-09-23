"""Fase 1 — perfis, credencial protegida e vínculo com aparelho.

Duas garantias que estes testes existem para proteger: a senha nunca sai do cofre em claro (nem para a API, nem
para o banco, nem para um erro de validação), e o isolamento entre perfis é imposto por restrição e por API de
repositório, não por convenção.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.db import Database
from app.events import EventBus
from app.main import create_app
from app.models import CredentialUpdate, ProfileCreate, ProfilePatch, SessionStatus
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.social.repository import SocialRepository
from app.social.service import SocialError, SocialService

from .conftest import Harness, make_config

SENHA_LUCAS = "$a=B7ee1#<b-C?S-{"
SENHA_MARIANA = "outra-senha-9!Zk#2"


def build(tmp_path: Path, *, vault: str = "ready") -> tuple[SocialService, SocialRepository, SecretStore, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    # `db_dsn` e nao `db_path`: assim o teste segue `TEST_DATABASE_URL` e roda de verdade no PostgreSQL quando a
    # suite e apontada para la. A prova presa ao ARQUIVO (ler os bytes do .sqlite3) mora agora num teste proprio,
    # abaixo, que se declara fora da corrida do outro banco — em vez de levar 13 testes junto (achado #162).
    db = Database(cfg.db_dsn)
    db.migrate()
    provider = MemoryKeyProvider() if vault == "ready" else _ProviderTravado()
    secrets = SecretStore(db, provider)
    repo = SocialRepository(db)
    svc = SocialService(repo, secrets, EventBus(db), known_instances=lambda: ["android-01", "android-02"])
    return svc, repo, secrets, db


class _ProviderTravado:
    key_id = "travado-v1"

    def available(self) -> bool:
        return True

    def key(self) -> bytes:
        from app.security.secret_store import SecretStoreLocked

        raise SecretStoreLocked("máquina trocada")


def novo(username: str, senha: str | None, instance_id: str | None = None, **over: Any) -> ProfileCreate:
    return ProfileCreate(username=username, password=senha, instance_id=instance_id,
                         first_name=over.pop("first_name", "Nome"), last_name=over.pop("last_name", "Sobrenome"),
                         **over)


# ---------------------------------------------------------------- cadastro e credencial
def test_cadastra_perfil_com_credencial_e_vinculo(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        dto = svc.create_profile(novo("mariana.costa91182", SENHA_MARIANA, "android-02", email="m@exemplo.com"))
        assert dto.username == "mariana.costa91182" and dto.instance_id == "android-02"
        # O identificador de login padrão é o E-MAIL do perfil: é ele que o Instagram sempre aceita, e entrar pelo
        # @usuário chegou a devolver "unable to log in" no aparelho real.
        assert dto.credential.configured and dto.credential.login_identifier == "m@exemplo.com"
        assert dto.session.status is SessionStatus.unknown          # sessão só existe depois de verificar
        assert secrets.get_secret(repo.credential_row(dto.id)["secret_ref"]) == SENHA_MARIANA
    finally:
        db.close()


def test_o_dto_do_perfil_nao_tem_campo_de_senha(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        dto = svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        texto = dto.model_dump_json()
        assert SENHA_LUCAS not in texto
        assert "password" not in texto and "senha" not in texto.lower()
        assert dto.credential.configured is True                    # o painel sabe que existe, não qual é
        assert dto.credential.login_identifier == "lucas.almeida9484"   # sem e-mail no perfil, cai no @usuário
    finally:
        db.close()


def test_a_senha_nao_fica_em_claro_em_lugar_nenhum_do_banco(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        despejo = ""
        for t in sorted(db.tables()):
            for row in db.query(f"SELECT * FROM {t}"):            # noqa: S608 - nomes vêm do próprio esquema
                despejo += str(dict(row))
        assert SENHA_LUCAS not in despejo
    finally:
        db.close()


def test_a_senha_nao_esta_nos_bytes_do_arquivo_do_banco(tmp_path: Path) -> None:
    """A metade da prova que e presa ao ARQUIVO: nenhum pedaco do .sqlite3 contem a senha — nem em pagina livre,
    nem no WAL, nem numa coluna que a varredura por tabela nao alcance.

    Mora num teste proprio porque nao existe arquivo no PostgreSQL. Antes, a assercao byte a byte estava dentro do
    teste geral e obrigava o ARQUIVO INTEIRO a abrir SQLite — 13 testes de perfil ficavam de fora da corrida do
    outro banco por causa de uma linha (achado #162). A varredura por tabela, que vale nos dois, ficou la em cima.
    """
    if os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("prova presa ao arquivo do SQLite; no PostgreSQL vale a varredura por tabela, acima")
    svc, repo, secrets, db = build(tmp_path)
    try:
        svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        assert SENHA_LUCAS.encode() not in Path(db.path).read_bytes()
    finally:
        db.close()


def test_trocar_a_senha_mantem_a_mesma_credencial(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        dto = svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        antes = repo.credential_row(dto.id)["secret_ref"]
        svc.set_credential(dto.id, CredentialUpdate(password="senha-nova-A1#"))
        depois = repo.credential_row(dto.id)
        assert secrets.get_secret(depois["secret_ref"]) == "senha-nova-A1#"
        assert depois["status"] == "active" and depois["failed_attempts"] == 0   # senha nova destrava a automação
        # A troca REUSA a referência: a senha anterior não pode ficar cifrada e órfã no cofre. Uma linha em `secrets`,
        # e a antiga não decifra mais para o valor antigo.
        assert antes == depois["secret_ref"]
        assert db.one("SELECT COUNT(*) n FROM secrets")["n"] == 1
        assert secrets.get_secret(antes) != SENHA_LUCAS
    finally:
        db.close()


def test_apagar_o_perfil_nao_deixa_senha_antiga_no_cofre(tmp_path: Path) -> None:
    """Depois de trocar a senha e apagar o perfil, nada sobra em `secrets` — nem a atual, nem versões anteriores."""
    svc, repo, secrets, db = build(tmp_path)
    try:
        dto = svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        svc.set_credential(dto.id, CredentialUpdate(password="senha-nova-A1#"))
        svc.set_credential(dto.id, CredentialUpdate(password="senha-nova-B2#"))
        svc.delete_profile(dto.id)
        assert db.one("SELECT COUNT(*) n FROM secrets")["n"] == 0
    finally:
        db.close()


def test_apagar_o_perfil_apaga_a_credencial_do_cofre(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        dto = svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        ref = repo.credential_row(dto.id)["secret_ref"]
        assert secrets.exists(ref)
        svc.delete_profile(dto.id)
        assert not secrets.exists(ref)                               # ciphertext some junto
        assert repo.profile_row(dto.id) is None
        assert db.one("SELECT 1 FROM device_profile_bindings WHERE profile_id=?", (dto.id,)) is None
    finally:
        db.close()


def test_cofre_travado_bloqueia_cadastro_sem_apagar_nada(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path, vault="locked")
    try:
        assert secrets.status() == "locked"
        with pytest.raises(SocialError) as exc:
            svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        assert exc.value.status == 503 and "travado" in exc.value.message
        assert repo.list_profile_ids() == []                         # nada pela metade
    finally:
        db.close()


def test_username_duplicado_e_recusado(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS))
        with pytest.raises(SocialError, match="Já existe"):
            svc.create_profile(novo("LUCAS.almeida9484", "outra"))   # maiúsculas não criam outro perfil
    finally:
        db.close()


def test_aparelho_desconhecido_e_recusado(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        with pytest.raises(SocialError, match="Aparelho desconhecido"):
            svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-99"))
    finally:
        db.close()


# ---------------------------------------------------------------- vínculo
def test_um_perfil_por_aparelho_e_um_aparelho_por_perfil(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        lucas = svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        mariana = svc.create_profile(novo("mariana.costa91182", SENHA_MARIANA, "android-02"))
        # mover Mariana para o aparelho do Lucas desvincula o Lucas: a restrição é do esquema
        svc.update_profile(mariana.id, ProfilePatch(instance_id="android-01"))
        assert svc.get_profile(mariana.id).instance_id == "android-01"
        assert svc.get_profile(lucas.id).instance_id is None
        assert repo.profile_id_for_instance("android-01") == mariana.id
    finally:
        db.close()


def test_trocar_de_aparelho_zera_a_sessao_mas_preserva_o_perfil(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        dto = svc.create_profile(novo("mariana.costa91182", SENHA_MARIANA, "android-02"))
        repo.set_session(dto.id, status=SessionStatus.session_ready, instance_id="android-02",
                         observed_username="mariana.costa91182", verified_at="2026-09-17T10:00:00Z")
        svc.update_profile(dto.id, ProfilePatch(instance_id="android-01"))
        atual = svc.get_profile(dto.id)
        assert atual.session.status is SessionStatus.unknown         # sessão não migra de aparelho
        assert atual.credential.configured                           # credencial e identidade continuam com o perfil
        assert len(repo.binding_history(dto.id)) == 2                # o rebinding fica auditável
    finally:
        db.close()


# ---------------------------------------------------------------- isolamento entre perfis
def test_todo_metodo_por_perfil_exige_profile_id() -> None:
    """Não existe método que devolva dado de perfil qualquer: a assinatura obriga a dizer de quem."""
    # A lista de exceções é explícita de propósito: método novo entra aqui só quando NÃO é dado de um perfil.
    # Persona tem identidade própria (e um dono só, garantido pelo esquema), então suas rotinas são globais.
    globais = {"create_profile", "list_profile_ids", "profile_by_username", "profile_id_for_instance",
               "create_persona", "list_personas", "persona_exists", "persona_row", "persona_owner",
               "update_persona", "delete_persona", "invalidate_sessions_of_instance", "db",
               # `localidade_da_instancia` lê `instances` — inventário do parque, não dado de perfil nenhum.
               "localidade_da_instancia"}
    # Categoria à parte, e não um nome a mais em `globais`: método que olha a FROTA INTEIRA de propósito. A regra
    # existe para conteúdo de um perfil não vazer para outro, e isto não devolve conteúdo — só agregado. Entrar
    # aqui custa duas condições, conferidas abaixo: precisa receber `exclude_profile_id` (a assinatura declara que
    # cruza perfis) e não pode devolver linha do banco. Sem isso, a lista viraria um esconderijo.
    agregados_da_frota = {"fleet_targeting"}
    por_perfil = [n for n in dir(SocialRepository)
                  if not n.startswith("_") and n not in globais and n not in agregados_da_frota]
    for nome in por_perfil:
        params = list(inspect.signature(getattr(SocialRepository, nome)).parameters)
        assert params[:2] == ["self", "profile_id"], f"{nome} não exige profile_id como primeiro argumento"
    for nome in agregados_da_frota:
        assinatura = inspect.signature(getattr(SocialRepository, nome))
        assert "exclude_profile_id" in assinatura.parameters, f"{nome} cruza perfis sem declarar de quem se exclui"
        retorno = str(assinatura.return_annotation)
        assert "Row" not in retorno and "list" not in retorno, f"{nome} devolve linha do banco, não agregado: {retorno}"


def test_consulta_com_o_perfil_errado_nao_devolve_dado_do_outro(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        lucas = svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        mariana = svc.create_profile(novo("mariana.costa91182", SENHA_MARIANA, "android-02"))
        repo.set_session(lucas.id, status=SessionStatus.session_ready, instance_id="android-01",
                         observed_username="lucas.almeida9484")

        # credencial, vínculo e sessão são estritamente por perfil
        assert repo.credential_row(lucas.id)["secret_ref"] != repo.credential_row(mariana.id)["secret_ref"]
        assert secrets.get_secret(repo.credential_row(lucas.id)["secret_ref"]) == SENHA_LUCAS
        assert secrets.get_secret(repo.credential_row(mariana.id)["secret_ref"]) == SENHA_MARIANA
        assert repo.binding_row(mariana.id)["instance_id"] == "android-02"
        assert repo.session_row(mariana.id)["status"] == SessionStatus.unknown.value
        assert repo.session_row(lucas.id)["status"] == SessionStatus.session_ready.value

        # apagar um não toca no outro
        svc.delete_profile(lucas.id)
        assert repo.profile_row(mariana.id) is not None
        assert secrets.get_secret(repo.credential_row(mariana.id)["secret_ref"]) == SENHA_MARIANA
        assert repo.session_row(mariana.id) is not None
    finally:
        db.close()


def test_tentativa_de_autenticacao_e_por_perfil(tmp_path: Path) -> None:
    svc, repo, secrets, db = build(tmp_path)
    try:
        lucas = svc.create_profile(novo("lucas.almeida9484", SENHA_LUCAS, "android-01"))
        mariana = svc.create_profile(novo("mariana.costa91182", SENHA_MARIANA, "android-02"))
        attempt = repo.start_auth_attempt(lucas.id, "android-01")
        repo.finish_auth_attempt(mariana.id, attempt, outcome="session_ready")   # perfil errado: não escreve
        assert repo.auth_attempts(lucas.id)[0]["outcome"] is None
        repo.finish_auth_attempt(lucas.id, attempt, outcome="session_ready")
        assert repo.auth_attempts(lucas.id)[0]["outcome"] == "session_ready"
        assert repo.auth_attempts(mariana.id) == []
    finally:
        db.close()


# ---------------------------------------------------------------- contrato HTTP
async def test_api_nunca_devolve_a_senha_e_o_422_nao_ecoa_o_valor(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        criado = await c.post("/api/instagram/profiles", json={
            "username": "mariana.costa91182", "first_name": "Mariana", "last_name": "Costa",
            "instance_id": "android-02", "password": SENHA_MARIANA})
        assert criado.status_code == 201, criado.text
        assert SENHA_MARIANA not in criado.text
        assert criado.json()["credential"]["configured"] is True

        pid = criado.json()["id"]
        for rota in ("/api/instagram/profiles", f"/api/instagram/profiles/{pid}"):
            r = await c.get(rota)
            assert r.status_code == 200 and SENHA_MARIANA not in r.text

        # erro de validação não pode ecoar o valor enviado
        invalido = await c.post("/api/instagram/profiles", json={"username": "!!!", "password": SENHA_MARIANA})
        assert invalido.status_code == 422 and SENHA_MARIANA not in invalido.text

        trocada = await c.put(f"/api/instagram/profiles/{pid}/credential", json={"password": "nova-senha-X9#"})
        assert trocada.status_code == 200 and "nova-senha-X9#" not in trocada.text

        apagada = await c.delete(f"/api/instagram/profiles/{pid}/credential")
        assert apagada.status_code == 200 and apagada.json()["credential"]["configured"] is False

        assert (await c.delete(f"/api/instagram/profiles/{pid}")).status_code == 204
        assert (await c.get(f"/api/instagram/profiles/{pid}")).status_code == 404


async def test_nada_da_senha_vaza_para_eventos_nem_para_o_log(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    app = create_app(harness.cfg, state=state)
    app.state.poc = state
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        await c.post("/api/instagram/profiles", json={"username": "lucas.almeida9484", "instance_id": "android-01",
                                                      "password": SENHA_LUCAS})
    eventos = "".join(str(dict(r)) for r in state.db.query("SELECT * FROM events"))
    assert SENHA_LUCAS not in eventos
    logs = Path(harness.cfg.logs_dir)
    for arquivo in logs.glob("*.log"):
        assert SENHA_LUCAS.encode() not in arquivo.read_bytes()


def test_nenhum_dto_de_resposta_tem_campo_de_credencial() -> None:
    """Varredura estrutural: se um DTO ganhar um campo de senha, este teste quebra antes de ir para a API.

    É mais forte que procurar a senha no corpo de uma resposta específica: cobre DTO que ainda nem é usado.
    """
    import inspect as _inspect
    import re

    from pydantic import BaseModel, SecretStr

    from app import models

    # Nome de campo com cara de credencial. `token` no singular; `tokens` no plural é contagem de uso de IA.
    proibido = re.compile(r"(?:^|_)(password|passwd|senha|secret|segredo|credential|credencial|api_key|token)(?:$|_)")
    # Entrada pode receber senha (o portal precisa mandá-la uma vez); saída, nunca. Distinguimos pelo tipo:
    # campo de entrada usa SecretStr, que não serializa o valor.
    for nome, obj in vars(models).items():
        if not (_inspect.isclass(obj) and issubclass(obj, BaseModel) and obj is not BaseModel):
            continue
        for campo, info in obj.model_fields.items():
            if not proibido.search(campo.lower()):
                continue
            anotacao = info.annotation
            texto = str(anotacao)
            # Campo que agrupa METADADOS (ex.: `credential: CredentialInfo`) é varrido pelo próprio laço, porque a
            # classe aninhada também está em `models`. O que não pode existir é campo ESCALAR com valor de credencial.
            aninhado = _inspect.isclass(anotacao) and issubclass(anotacao, BaseModel)
            assert aninhado or "SecretStr" in texto, f"{nome}.{campo} devolveria credencial em texto ({texto})"
            assert SecretStr is not None
