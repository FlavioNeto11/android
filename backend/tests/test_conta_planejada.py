"""31.281 (ADR-087, adendo v1.132): a conta planejada, o cofre e o ciclo de provisionamento.

Prova `simulated`: harness na porta 5640, provedor de conta SIMULADO (nenhuma conta real em nenhum serviço). A senha
gerada é lida do cofre só dentro do teste, para provar que existe e que não aparece em nenhum outro lugar.
"""
from __future__ import annotations

import json
import re
import secrets as pysecrets

import httpx
import pytest
from pydantic import SecretStr

from app.models import ProfileAccountCreate, ProfileCreate
from app.modules.identity.domain import provisionamento as prov
from app.modules.identity.domain.provisionamento import Estado, Evento
from app.security.gerador_de_senha import TAMANHO_MAX, TAMANHO_MIN, gerar_senha

from .conftest import Harness

OUTLOOK = "outlook-sim"
DONO = "painel:teste"


def _outlook(h: Harness) -> None:
    if not h.state.db.scalar("SELECT id FROM apps WHERE id=?", (OUTLOOK,)):
        h.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES (?,?,?,?,0)",
                           (OUTLOOK, "Outlook (simulado)", "com.exemplo.outlook.sim", "Main"))


def _persona(h: Harness, **kw: object) -> str:
    nome = f"persona.{pysecrets.token_hex(3)}"
    return h.state.social.create_profile(ProfileCreate(username=nome, first_name="Maria", last_name="Álvares Souza",
                                                       birth_date="1994-03-12", **kw)).id  # type: ignore[arg-type]


def _api(h: Harness) -> httpx.AsyncClient:
    from app.main import create_app

    app = create_app(h.cfg, state=h.state)
    app.state.poc = h.state
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _base(pid: str) -> str:
    return f"/api/instagram/profiles/{pid}/accounts"


async def _planejar(c: httpx.AsyncClient, pid: str, **corpo: object) -> dict:
    r = await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json={"app_id": OUTLOOK, **corpo})
    assert r.status_code in (200, 201), r.text
    return r.json()


async def _preparar(c: httpx.AsyncClient, pid: str, aid: str, **corpo: object) -> httpx.Response:
    return await c.post(f"/api/instagram/profiles/{pid}/accounts/{aid}/credential/prepare",
                        json={"consent": True, **corpo})


async def _evento(c: httpx.AsyncClient, pid: str, aid: str, evento: str, esperado: str, **extra: object) -> httpx.Response:
    return await c.post(f"/api/instagram/profiles/{pid}/accounts/{aid}/provisioning",
                        json={"evento": evento, "estado_esperado": esperado, **extra})


def _senha_do_cofre(h: Harness, aid: str) -> str:
    ref = h.state.db.scalar("SELECT secret_ref FROM account_credentials WHERE account_id=?", (aid,))
    return h.state.social.secrets.get_secret(ref)


def _varrer(h: Harness, valor: str) -> None:
    """O valor não está em nenhuma tabela de texto, nem em evento: só cifrado no cofre."""
    for tabela in ("events", "profile_accounts", "account_credentials", "account_sessions", "memory_items", "runs",
                   "ai_calls", "actions"):
        for linha in h.state.db.query(f"SELECT * FROM {tabela}"):                  # noqa: S608 - nome fixo do teste
            assert valor not in json.dumps(dict(linha), default=str), tabela


# ---------------------------------------------------------------- domínio puro
def test_gerador_traz_as_quatro_classes_e_respeita_o_tamanho() -> None:
    for n in (TAMANHO_MIN, 20, TAMANHO_MAX):
        s = gerar_senha(n)
        assert len(s) == n
        assert re.search(r"[A-Z]", s) and re.search(r"[a-z]", s) and re.search(r"\d", s) and re.search(r"[^A-Za-z0-9]", s)
    assert len({gerar_senha() for _ in range(50)}) == 50
    for ruim in (TAMANHO_MIN - 1, TAMANHO_MAX + 1):
        with pytest.raises(ValueError):
            gerar_senha(ruim)


def test_maquina_de_estados_o_caminho_feliz_e_a_falha_com_retomada() -> None:
    a = Estado.CREDENCIAL_PREPARADA
    r = prov.aplicar(Evento.INICIAR_CADASTRO, atual=a, esperado=a)
    assert r.estado is Estado.AGUARDANDO_CADASTRO_EXTERNO and r.mudou
    r = prov.aplicar(Evento.ENVIADO, atual=r.estado, esperado=r.estado)
    r = prov.aplicar(Evento.FALHAR, atual=r.estado, esperado=r.estado)
    assert r.estado is Estado.FALHA and r.resume_state is Estado.AGUARDANDO_VERIFICACAO
    r = prov.aplicar(Evento.RETOMAR, atual=Estado.FALHA, esperado=Estado.FALHA, resume_state=r.resume_state)
    assert r.estado is Estado.AGUARDANDO_VERIFICACAO
    r = prov.aplicar(Evento.CONFIRMAR, atual=r.estado, esperado=r.estado)
    assert r.estado is Estado.CONFIRMADA


def test_maquina_de_estados_recusas_e_idempotencia() -> None:
    with pytest.raises(prov.EstadoInesperado) as e:
        prov.aplicar(Evento.ENVIADO, atual=Estado.PLANEJADA, esperado=Estado.AGUARDANDO_CADASTRO_EXTERNO)
    assert e.value.atual is Estado.PLANEJADA
    with pytest.raises(prov.TransicaoInvalida):
        prov.aplicar(Evento.CONFIRMAR, atual=Estado.PLANEJADA, esperado=Estado.PLANEJADA)
    with pytest.raises(prov.TransicaoInvalida):
        prov.aplicar(Evento.CANCELAR, atual=Estado.CONFIRMADA, esperado=Estado.CONFIRMADA)
    with pytest.raises(prov.TransicaoInvalida):
        prov.aplicar(Evento.RETOMAR, atual=Estado.PLANEJADA, esperado=Estado.PLANEJADA)
    # repetir o evento no estado de destino: sem efeito, mesmo com o `esperado` velho
    r = prov.aplicar(Evento.ENVIADO, atual=Estado.AGUARDANDO_VERIFICACAO, esperado=Estado.AGUARDANDO_CADASTRO_EXTERNO)
    assert not r.mudou and r.estado is Estado.AGUARDANDO_VERIFICACAO
    r = prov.aplicar(Evento.CONFIRMAR, atual=Estado.CONFIRMADA, esperado=Estado.AGUARDANDO_VERIFICACAO)
    assert not r.mudou
    # a retomada repetida (o estado já voltou ao guardado)
    r = prov.aplicar(Evento.RETOMAR, atual=Estado.AGUARDANDO_VERIFICACAO, esperado=Estado.FALHA,
                     resume_state=Estado.AGUARDANDO_VERIFICACAO)
    assert not r.mudou


def test_eventos_aceitos_por_estado() -> None:
    assert prov.eventos_aceitos(Estado.PLANEJADA) == ["falhar", "cancelar"]
    assert prov.eventos_aceitos(Estado.CREDENCIAL_PREPARADA) == ["iniciar_cadastro", "falhar", "cancelar"]
    assert prov.eventos_aceitos(Estado.FALHA) == ["retomar", "cancelar"]
    assert prov.eventos_aceitos(Estado.CONFIRMADA) == []


# ---------------------------------------------------------------- planejar
async def test_planejar_nasce_planejada_sem_handle_e_repetir_e_idempotente(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json={"app_id": OUTLOOK, "desired_handle": "maria.souza94"})
        assert r.status_code == 201
        conta = r.json()
        assert conta["handle"] == "" and conta["provisioning"]["state"] == "planejada"
        assert conta["provisioning"]["desired_handle"] == "maria.souza94" and not conta["provisioning"]["authenticated"]
        assert conta["provisioning"]["actions"] == ["falhar", "cancelar"] and not conta["credential_configured"]
        # repetir devolve a mesma linha (200); outro desejado só edita
        r2 = await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json={"app_id": OUTLOOK, "desired_handle": "maria.s94"})
        assert r2.status_code == 200 and r2.json()["id"] == conta["id"]
        assert r2.json()["provisioning"]["desired_handle"] == "maria.s94"
        assert harness.state.db.scalar("SELECT COUNT(*) FROM profile_accounts WHERE profile_id=? AND app_id=?",
                                       (pid, OUTLOOK)) == 1
        # PATCH edita o desejado
        p = await c.patch(f"{_base(pid)}/{conta['id']}", json={"desired_handle": "maria.souza.94"})
        assert p.status_code == 200 and p.json()["provisioning"]["desired_handle"] == "maria.souza.94"
        assert (await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json={"app_id": OUTLOOK, "desired_handle": "a b"})).status_code == 422


async def test_planejar_recusa_app_desconhecido_e_conta_ja_confirmada(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    harness.state.social.add_account(pid, ProfileAccountCreate(app_id=OUTLOOK, handle="ja.existe@exemplo.test"), by=DONO)
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json={"app_id": OUTLOOK})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "duplicate_account"
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json={"app_id": "nao-existe"})
        assert r.status_code == 400
        conta = (await c.get(_base(pid))).json()[0]
        assert conta["provisioning"]["state"] == "confirmada" and conta["provisioning"]["evidence"] is None


async def test_sugestoes_locais_sem_ia_e_sem_as_ocupadas(harness: Harness) -> None:
    _outlook(harness)
    pid, outra = _persona(harness), _persona(harness)
    async with _api(harness) as c:
        r = await c.get(f"/api/instagram/profiles/{pid}/accounts/handle-suggestions", params={"app_id": OUTLOOK})
        assert r.status_code == 200
        sugestoes = r.json()["suggestions"]
        handles = [s["handle"] for s in sugestoes]
        assert handles[0] == "maria.alvaressouza" and "maria.alvaressouza94" in handles     # nome, sobrenome sem acento, ano
        assert {s["source"] for s in sugestoes} <= {"persona_nome", "persona_dados", "alternativa"}
        assert all(re.match(r"^[a-z0-9._]{3,}$", h) for h in handles) and len(handles) == len(set(handles))
        ocupada = handles[0]
        await _planejar(c, outra, desired_handle=ocupada)
        de_novo = [s["handle"] for s in (await c.get(f"/api/instagram/profiles/{pid}/accounts/handle-suggestions",
                                                     params={"app_id": OUTLOOK})).json()["suggestions"]]
        assert ocupada not in de_novo


# ---------------------------------------------------------------- credencial
async def test_gerar_guarda_no_cofre_com_consentimento_e_nunca_devolve_a_senha(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        conta = await _planejar(c, pid, desired_handle="maria.souza94")
        r = await _preparar(c, pid, conta["id"], modo="gerar", tamanho=24)
        assert r.status_code == 200, r.text
        corpo = r.json()
        assert corpo["provisioning"]["state"] == "credencial_preparada" and corpo["credential_configured"]
        assert corpo["credential"]["consent_at"] and corpo["credential"]["login_identifier"] == "maria.souza94"
        senha = _senha_do_cofre(harness, conta["id"])
        assert len(senha) == 24
        assert senha not in r.text and senha not in json.dumps((await c.get(_base(pid))).json())
        _varrer(harness, senha)


async def test_preparar_exige_consentimento_e_o_modo_certo(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        conta = await _planejar(c, pid)
        aid = conta["id"]
        r = await c.post(f"{_base(pid)}/{aid}/credential/prepare", json={"modo": "gerar", "consent": False})
        assert r.status_code == 409 and r.json()["detail"]["code"] == "consentimento_de_credencial"
        valor = "Tst-" + pysecrets.token_urlsafe(9)
        for corpo in ({"modo": "gerar", "password": valor}, {"modo": "digitar"},
                      {"modo": "reutilizar"}, {"modo": "digitar", "password": valor, "clonar_de": "x"}):
            r = await _preparar(c, pid, aid, **corpo)
            assert r.status_code == 422 and r.json()["detail"]["code"] == "credencial_modo_invalido", corpo
        assert harness.state.db.scalar("SELECT COUNT(*) FROM account_credentials WHERE account_id=?", (aid,)) == 0


async def test_digitar_e_trocar_so_com_substituir_e_o_422_nao_devolve_a_senha(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    primeira, segunda = "Tst-" + pysecrets.token_urlsafe(9), "Tst-" + pysecrets.token_urlsafe(9)
    async with _api(harness) as c:
        aid = (await _planejar(c, pid, desired_handle="maria.souza94"))["id"]
        r = await _preparar(c, pid, aid, modo="digitar", password=primeira)
        assert r.status_code == 200 and r.json()["provisioning"]["state"] == "credencial_preparada"
        assert _senha_do_cofre(harness, aid) == primeira
        r = await _preparar(c, pid, aid, modo="digitar", password=segunda)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "credencial_ja_preparada"
        assert _senha_do_cofre(harness, aid) == primeira
        r = await _preparar(c, pid, aid, modo="digitar", password=segunda, substituir=True)
        assert r.status_code == 200 and _senha_do_cofre(harness, aid) == segunda
        ruim = await c.post(f"{_base(pid)}/{aid}/credential/prepare", json={"modo": "digitar", "consent": True,
                                                                            "password": segunda, "tamanho": 4})
        assert ruim.status_code == 422 and segunda not in ruim.text
        _varrer(harness, primeira)
        _varrer(harness, segunda)


async def test_reutilizar_so_por_escolha_expressa_e_so_da_mesma_persona(harness: Harness) -> None:
    _outlook(harness)
    pid, outra = _persona(harness), _persona(harness)
    senha = "Tst-" + pysecrets.token_urlsafe(9)
    origem = harness.state.social.add_account(
        pid, ProfileAccountCreate(app_id=OUTLOOK, handle="velha@exemplo.test", password=SecretStr(senha), consent=True),
        by=DONO).id
    harness.state.db.execute("INSERT INTO apps(id, name, package, activity, builtin) VALUES ('outro-app','Outro',"
                             "'com.exemplo.outro','Main',0)")
    async with _api(harness) as c:
        r = await c.post(f"/api/instagram/profiles/{pid}/accounts/planned", json={"app_id": "outro-app", "desired_handle": "maria.souza94"})
        aid = r.json()["id"]
        r = await _preparar(c, pid, aid, modo="reutilizar", clonar_de=origem)
        assert r.status_code == 200, r.text
        assert r.json()["provisioning"]["state"] == "credencial_preparada" and r.json()["credential"]["consent_at"]
        assert _senha_do_cofre(harness, aid) == senha
        ref_a = harness.state.db.scalar("SELECT secret_ref FROM account_credentials WHERE account_id=?", (aid,))
        ref_o = harness.state.db.scalar("SELECT secret_ref FROM account_credentials WHERE account_id=?", (origem,))
        assert ref_a != ref_o                                  # entrada própria no cofre
        # outra persona: 409; a própria conta: 409
        alheia = (await c.post(f"/api/instagram/profiles/{outra}/accounts/planned", json={"app_id": OUTLOOK})).json()["id"]
        r = await _preparar(c, outra, alheia, modo="reutilizar", clonar_de=origem)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "credencial_de_outra_persona"
        r = await _preparar(c, pid, aid, modo="reutilizar", clonar_de=aid, substituir=True)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "clonar_de_si_mesma"


async def test_put_credential_numa_conta_planejada_avanca_o_estado_e_apagar_volta(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = (await _planejar(c, pid))["id"]
        r = await c.put(f"{_base(pid)}/{aid}/credential", json={"password": "Tst-" + pysecrets.token_urlsafe(9),
                                                                 "consent": True})
        assert r.status_code == 200 and r.json()["provisioning"]["state"] == "credencial_preparada"
        r = await c.delete(f"{_base(pid)}/{aid}/credential")
        assert r.status_code == 200 and r.json()["provisioning"]["state"] == "planejada"


# ---------------------------------------------------------------- o ciclo
async def _ate(c: httpx.AsyncClient, h: Harness, pid: str, estado: str, **plano: object) -> str:
    """Leva uma conta nova até `estado` pelo caminho feliz e devolve o id."""
    aid = (await _planejar(c, pid, **plano))["id"]
    if estado == "planejada":
        return aid
    assert (await _preparar(c, pid, aid, modo="gerar")).status_code == 200
    seq = [("iniciar_cadastro", "credencial_preparada", "aguardando_cadastro_externo"),
           ("enviado", "aguardando_cadastro_externo", "aguardando_verificacao")]
    atual = "credencial_preparada"
    for evento, de, para in seq:
        if atual == estado:
            break
        r = await _evento(c, pid, aid, evento, de)
        assert r.status_code == 200 and r.json()["provisioning"]["state"] == para, r.text
        atual = para
    return aid


async def test_ciclo_completo_confirma_por_marcacao_declarada(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = await _ate(c, harness, pid, "aguardando_verificacao", desired_handle="maria.souza94")
        # sem evidência não confirma
        r = await _evento(c, pid, aid, "confirmar", "aguardando_verificacao")
        assert r.status_code == 422 and r.json()["detail"]["code"] == "sem_evidencia"
        r = await _evento(c, pid, aid, "confirmar", "aguardando_verificacao", evidencia={"tipo": "declarada"})
        assert r.status_code == 422
        r = await _evento(c, pid, aid, "confirmar", "aguardando_verificacao",
                          evidencia={"tipo": "declarada", "handle_confirmado": "maria.souza94@exemplo.test"})
        assert r.status_code == 200, r.text
        conta = r.json()
        assert conta["provisioning"]["state"] == "confirmada" and conta["handle"] == "maria.souza94@exemplo.test"
        assert conta["provisioning"]["confirmed_at"] and conta["provisioning"]["actions"] == []
        assert conta["provisioning"]["evidence"]["kind"] == "declarada"
        assert conta["credential"]["login_identifier"] == "maria.souza94@exemplo.test"
        # repetir é 200 sem efeito
        r2 = await _evento(c, pid, aid, "confirmar", "aguardando_verificacao",
                           evidencia={"tipo": "declarada", "handle_confirmado": "outro@exemplo.test"})
        assert r2.status_code == 200 and r2.json()["handle"] == "maria.souza94@exemplo.test"
        # depois de confirmada: nada de editar o desejado nem de preparar a credencial
        assert (await c.patch(f"{_base(pid)}/{aid}", json={"desired_handle": "x.y.z"})).status_code == 409
        r3 = await _preparar(c, pid, aid, modo="gerar", substituir=True)
        assert r3.status_code == 409 and r3.json()["detail"]["code"] == "estado_nao_permite_credencial"


async def test_confirmar_por_sessao_observada_exige_o_usuario_desejado(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = await _ate(c, harness, pid, "aguardando_verificacao", desired_handle="maria.souza94")
        evidencia = {"tipo": "sessao", "sessao_id": "android-01"}
        r = await _evento(c, pid, aid, "confirmar", "aguardando_verificacao", evidencia=evidencia)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "evidencia_nao_confere"      # sem sessão
        harness.state.db.execute(
            "INSERT INTO account_sessions(account_id, instance_id, status, observed_handle, verified_at, updated_at)"
            " VALUES (?,?,?,?,?,?)", (aid, "android-01", "session_ready", "@Outro.Usuario", "2026-10-10T00:00:00Z",
                                      "2026-10-10T00:00:00Z"))
        r = await _evento(c, pid, aid, "confirmar", "aguardando_verificacao", evidencia=evidencia)
        assert r.status_code == 409 and r.json()["detail"]["code"] == "evidencia_nao_confere"      # outro usuário
        harness.state.db.execute("UPDATE account_sessions SET observed_handle=? WHERE account_id=?",
                                 ("@Maria.Souza94", aid))
        r = await _evento(c, pid, aid, "confirmar", "aguardando_verificacao", evidencia=evidencia)
        assert r.status_code == 200, r.text
        conta = r.json()
        assert conta["provisioning"]["state"] == "confirmada" and conta["handle"] == "Maria.Souza94"
        assert conta["provisioning"]["evidence"] == {"kind": "sessao", "ref": "android-01"}


async def test_comparar_e_trocar_transicao_invalida_e_pre_condicoes(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = await _ate(c, harness, pid, "planejada")
        r = await _evento(c, pid, aid, "iniciar_cadastro", "planejada")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "transicao_invalida"
        r = await _evento(c, pid, aid, "enviado", "credencial_preparada")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "estado_inesperado"
        assert r.json()["detail"]["details"]["estado_atual"] == "planejada"
        # credencial sem consentimento (clonada) não inicia o cadastro
        assert (await _preparar(c, pid, aid, modo="gerar")).status_code == 200
        harness.state.db.execute("UPDATE account_credentials SET consent_at=NULL, consent_by=NULL WHERE account_id=?", (aid,))
        r = await _evento(c, pid, aid, "iniciar_cadastro", "credencial_preparada")
        assert r.status_code == 409 and r.json()["detail"]["code"] == "sem_consentimento"
        assert (await _evento(c, pid, aid, "nao_existe", "planejada")).status_code == 422


async def test_falha_guarda_para_onde_voltar_e_retomar_nao_reinicia(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = await _ate(c, harness, pid, "aguardando_verificacao")
        senha = _senha_do_cofre(harness, aid)
        r = await _evento(c, pid, aid, "falhar", "aguardando_verificacao", motivo="o código do e-mail não chegou")
        assert r.status_code == 200
        prov_info = r.json()["provisioning"]
        assert prov_info["state"] == "falha" and prov_info["resume_state"] == "aguardando_verificacao"
        assert prov_info["detail"] == "o código do e-mail não chegou" and prov_info["actions"] == ["retomar", "cancelar"]
        assert (await _evento(c, pid, aid, "falhar", "falha")).status_code == 200            # repetir
        r = await _evento(c, pid, aid, "retomar", "falha")
        assert r.status_code == 200 and r.json()["provisioning"]["state"] == "aguardando_verificacao"
        assert r.json()["provisioning"]["resume_state"] is None
        assert (await _evento(c, pid, aid, "retomar", "falha")).status_code == 200            # repeteco
        assert _senha_do_cofre(harness, aid) == senha                                          # nada se perdeu


async def test_cancelar_remove_a_conta_e_a_senha_so_se_ninguem_mais_a_usa(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = await _ate(c, harness, pid, "credencial_preparada")
        ref = harness.state.db.scalar("SELECT secret_ref FROM account_credentials WHERE account_id=?", (aid,))
        r = await _evento(c, pid, aid, "cancelar", "credencial_preparada")
        assert r.status_code == 200 and r.json() == {"removida": True, "credencial_removida": True}
        assert harness.state.db.scalar("SELECT COUNT(*) FROM profile_accounts WHERE id=?", (aid,)) == 0
        assert not harness.state.social.secrets.exists(ref)
        assert (await _evento(c, pid, aid, "cancelar", "credencial_preparada")).status_code == 404


async def test_a_trilha_do_ciclo_nao_tem_endereco_nem_senha(harness: Harness) -> None:
    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = await _ate(c, harness, pid, "aguardando_verificacao", desired_handle="maria.unica.94")
        senha = _senha_do_cofre(harness, aid)
        await _evento(c, pid, aid, "confirmar", "aguardando_verificacao",
                      evidencia={"tipo": "declarada", "handle_confirmado": "maria.confirmada.94@exemplo.test"})
    eventos = [dict(r) for r in harness.state.db.query("SELECT * FROM events WHERE kind='identity.conta.provisionamento'")]
    assert [e["kind"] for e in eventos] and len(eventos) >= 5
    texto = json.dumps(eventos, default=str)
    for proibido in ("maria.unica.94", "maria.confirmada.94", senha):
        assert proibido not in texto
    _varrer(harness, senha)


# ---------------------------------------------------------------- consumidores: a conta planejada não é conta real
async def test_dados_ao_plano_trazem_o_usuario_desejado_e_a_senha_sigilosa_da_conta_planejada(harness: Harness) -> None:
    from app.modules.identity.domain.available_data import account_name, resolve_secret, typable_secret_for

    _outlook(harness)
    pid = _persona(harness)
    async with _api(harness) as c:
        aid = (await _planejar(c, pid, desired_handle="maria.souza94"))["id"]
        sem_senha = list(harness.state.runs.dados.accounts_of_profile(pid))
        assert next(x for x in sem_senha if x.account_id == aid).handle == "maria.souza94"
        assert typable_secret_for(sem_senha, "com.exemplo.outlook.sim").sem_senha_guardada
        await _preparar(c, pid, aid, modo="gerar")
    contas = list(harness.state.runs.dados.accounts_of_profile(pid))
    planejada = next(x for x in contas if x.account_id == aid)
    assert planejada.handle == "maria.souza94" and planejada.has_credential and planejada.consent_at
    assert typable_secret_for(contas, "com.exemplo.outlook.sim").refusal is None
    assert resolve_secret(contas, account_name(OUTLOOK, None, "senha")).refusal is None


async def test_conta_planejada_nao_serve_a_pedido_nem_conta_como_sessao_pronta(harness: Harness) -> None:
    from app.models import PersonaDeviceBody

    _outlook(harness)
    pid = _persona(harness)
    instance = next(iter(harness.state.devices.devices))
    harness.state.social.bind_device(pid, PersonaDeviceBody(instance_id=instance))
    async with _api(harness) as c:
        aid = (await _planejar(c, pid, desired_handle="maria.souza94"))["id"]
    assert not harness.state.runs._mundo([OUTLOOK]).serve(pid, instance, [OUTLOOK])        # noqa: SLF001
    harness.state.db.execute(
        "INSERT INTO account_sessions(account_id, instance_id, status, observed_handle, verified_at, updated_at)"
        " VALUES (?,?,?,?,?,?)", (aid, instance, "session_ready", "maria.souza94", "2026-10-10T00:00:00Z",
                                  "2026-10-10T00:00:00Z"))
    assert (pid, instance) not in harness.state.runs._mundo([OUTLOOK]).sessoes_prontas     # noqa: SLF001
    # confirmada, a mesma conta passa a servir e a sessão pronta passa a contar
    harness.state.db.execute("UPDATE profile_accounts SET provisioning_state='confirmada' WHERE id=?", (aid,))
    mundo = harness.state.runs._mundo([OUTLOOK])                                           # noqa: SLF001
    assert mundo.serve(pid, instance, [OUTLOOK]) and (pid, instance) in mundo.sessoes_prontas


async def test_conectar_verificar_e_sair_recusam_conta_nao_confirmada(harness: Harness) -> None:
    from app.models import PersonaDeviceBody

    _outlook(harness)
    pid = _persona(harness)
    harness.state.social.bind_device(pid, PersonaDeviceBody(instance_id=next(iter(harness.state.devices.devices))))
    async with _api(harness) as c:
        aid = (await _planejar(c, pid, desired_handle="maria.souza94"))["id"]
        for caminho in ("connect", "verify", "logout"):
            r = await c.post(f"{_base(pid)}/{aid}/session/{caminho}")
            assert r.status_code == 409 and r.json()["detail"]["code"] == "conta_nao_confirmada", (caminho, r.text)


async def test_conta_da_ponte_igfarm_nasce_confirmada_com_evidencia(harness: Harness) -> None:
    from app.modules.identity.infrastructure.ponte_igfarm import ArmazemSql

    pid = _persona(harness)
    conta = harness.state.db.scalar("SELECT id FROM profile_accounts WHERE profile_id=? LIMIT 1", (pid,))
    if conta is None:
        pytest.skip("a persona do harness nasce sem conta âncora; coberto em test_egresso_igfarm")
    harness.state.db.execute("UPDATE profile_accounts SET provisioning_state='planejada' WHERE id=?", (conta,))
    ArmazemSql(harness.state.db).gravar_conta_igfarm(account_id=conta, persona_id=pid, igfarm_account_id="ig-123",
                                                      username="maria.souza94", criada_em="2026-10-09T10:00:00Z",
                                                      agora="2026-10-10T00:00:00Z")
    linha = harness.state.db.one("SELECT * FROM profile_accounts WHERE id=?", (conta,))
    assert linha["provisioning_state"] == "confirmada" and linha["desired_handle"] == "maria.souza94"
    assert json.loads(linha["confirmation_evidence"]) == {"kind": "igfarm", "ref": "ig-123"}
