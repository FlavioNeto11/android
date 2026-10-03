"""A conta retirada some de `memory_items` de TODAS as personas (item 29.32, P13 da reavaliação de 03/10; ADR-068).

Antes do 29.32 a retirada reescrevia só a memória da persona que retirava a conta. Agora o @, o id e o e-mail que só
aquela conta usava viram `[conta removida]` também na memória das OUTRAS personas; o e-mail que outra conta VIVA ainda
usa fica (continua verdadeiro). `runs.command` e `actions.args` seguem como histórico (opção A). Há ainda o passe único
das contas retiradas antes (`scripts/memoria-conta-retirada.py`), com a lápide e os eventos como fonte.

Nível de prova: `simulated` (banco de teste, cofre em memória, handles e e-mails inventados). Nada real.
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.models import ProfileCreate
from app.social.contas_nossas import MARCADOR, emails_so_desta_conta, handle_vivo, registrar_lapide, sem_o_rastro
from app.social.memory import fingerprint, reescrever_memoria, reescrever_memoria_das_retiradas
from app.util import now_iso

from .test_capabilities import SENHA, build, perfil

ANA = "ana.exemplo01"
BETO = "beto.exemplo02"
CAIO = "caio.exemplo03"
EMAIL_ANA = "ana.exemplo01@correio-teste.example"
EMAIL_VIVO = "caixa.compartilhada@correio-teste.example"
RAIZ = Path(__file__).resolve().parents[2]


def _montar(tmp_path: Path) -> tuple[Any, Any, Any, str, str, str]:
    svc, repo, _pol, db = build(tmp_path)
    caio = svc.create_profile(ProfileCreate(username=CAIO, password=SENHA)).id        # sem aparelho: o app não repete no mesmo
    return svc, repo, db, perfil(svc, ANA, "android-01"), perfil(svc, BETO, "android-02"), caio


def _memoria(db: Any, pid: str, chave: str, assunto: str, conteudo: str) -> None:
    db.execute("INSERT INTO memory_items(id, profile_id, subject, content, source, fingerprint, created_at, updated_at)"
               " VALUES (?,?,?,?,?,?,?,?)", (f"m-{chave}", pid, assunto, conteudo, "operator", f"fp-{chave}", now_iso(),
                                             now_iso()))


def _mem(db: Any) -> dict[str, tuple[str, str]]:
    return {m["id"]: (m["subject"], m["content"]) for m in db.query("SELECT id, subject, content FROM memory_items")}


def _ancora(repo: Any, pid: str) -> str:
    return str(repo.conta_ancora(pid)["id"])


def _credencial(repo: Any, pid: str, conta: str, login: str) -> None:
    repo.set_account_credential(pid, conta, login_identifier=login, secret_ref=f"ref-{conta}", key_id="k")


def _evento_da_retirada(db: Any) -> str:
    return str(db.scalar("SELECT data FROM events WHERE kind='profile.account_retired'"))


# ============================================================ o casamento
def test_casamento_nao_pega_banana_nem_ana_silva_nem_o_email_dos_outros() -> None:
    texto = "banana, ana.silva, a ana.silva.dois, foo@ana.com, ana@outro.example, x.ana e a_ana; mas @Ana, ANA... e ana."
    assert sem_o_rastro(texto, "ana", None) == (
        f"banana, ana.silva, a ana.silva.dois, foo@ana.com, ana@outro.example, x.ana e a_ana; mas {MARCADOR}, "
        f"{MARCADOR}... e {MARCADOR}.")


def test_email_so_casa_o_endereco_inteiro() -> None:
    texto = f"escreva para {EMAIL_ANA.upper()}, nao para x{EMAIL_ANA}, {EMAIL_ANA}.br nem {EMAIL_ANA}!"
    assert sem_o_rastro(texto, None, None, [EMAIL_ANA]) == (
        f"escreva para {MARCADOR}, nao para x{EMAIL_ANA}, {EMAIL_ANA}.br nem {MARCADOR}!")


def test_email_sai_inteiro_antes_do_handle_que_e_a_parte_local_dele() -> None:
    assert sem_o_rastro(f"{EMAIL_ANA} e @{ANA}", ANA, None, [EMAIL_ANA]) == f"{MARCADOR} e {MARCADOR}"
    # Sem a conta ser dona do e-mail, o `ana.exemplo01@...` de outra caixa não perde a parte local.
    assert sem_o_rastro(f"{EMAIL_ANA} e @{ANA}", ANA, None) == f"{EMAIL_ANA} e {MARCADOR}"


# ============================================================ o caminho vivo: a retirada
def test_retirada_reescreve_a_memoria_de_outra_persona_e_conta_a_parte(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, caio = _montar(tmp_path)
    conta = _ancora(repo, ana)
    _memoria(db, ana, "a", ANA, f"minha conta @{ANA} ({conta})")
    _memoria(db, beto, "b", "Seguiu", f"vi @{ANA.upper()} comentar; tambem {ANA}.silva e x{ANA}y ficam")
    _memoria(db, caio, "c", f"@{ANA}", "sem rastro no conteudo")
    _memoria(db, caio, "d", "Terceiro", "falou com @alguem.de.fora")

    r = svc.retirar_conta_bloqueada(ana, conta, autor="dono")

    assert r["limpezas"]["memory_items"] == 3 and r["limpezas"]["memory_items_de_outras_personas"] == 2
    mem = _mem(db)
    assert mem["m-a"] == (MARCADOR, f"minha conta {MARCADOR} ({MARCADOR})")
    assert mem["m-b"] == ("Seguiu", f"vi {MARCADOR} comentar; tambem {ANA}.silva e x{ANA}y ficam")
    assert mem["m-c"] == (MARCADOR, "sem rastro no conteudo")
    assert mem["m-d"] == ("Terceiro", "falou com @alguem.de.fora")
    ev = json.loads(_evento_da_retirada(db))
    assert ev["limpezas"]["memory_items_de_outras_personas"] == 2 and ANA not in json.dumps(ev).lower()


def test_email_que_so_a_conta_retirada_usava_sai_da_memoria_de_todos(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    conta = _ancora(repo, ana)
    _credencial(repo, ana, conta, EMAIL_ANA)
    assert emails_so_desta_conta(db, profile_id=ana, account_id=conta, handle=ANA, ancora=True) == [EMAIL_ANA]
    _memoria(db, ana, "a", "Contato", f"o login e {EMAIL_ANA.upper()}")
    _memoria(db, beto, "b", "Contato", f"a Ana usa {EMAIL_ANA} e {EMAIL_ANA}.br")

    svc.retirar_conta_bloqueada(ana, conta, autor="dono", evidencia=f"login {EMAIL_ANA} travado")

    mem = _mem(db)
    assert mem["m-a"] == ("Contato", f"o login e {MARCADOR}")
    assert mem["m-b"] == ("Contato", f"a Ana usa {MARCADOR} e {EMAIL_ANA}.br")
    ev = _evento_da_retirada(db).lower()
    assert EMAIL_ANA not in ev and ANA not in ev


@pytest.mark.parametrize("onde_vive", ["mesma_persona", "outra_persona", "credencial_de_outra_conta"])
def test_email_que_outra_conta_viva_usa_fica_na_memoria(tmp_path: Path, onde_vive: str) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    conta = _ancora(repo, ana)
    _credencial(repo, ana, conta, EMAIL_VIVO)                 # o login do Instagram que sai é o e-mail do Outlook...
    dono = ana if onde_vive == "mesma_persona" else beto
    outlook = repo.create_account(
        dono, app_id="outlook",
        handle=EMAIL_VIVO if onde_vive != "credencial_de_outra_conta" else "outra.caixa@correio-teste.example")
    if onde_vive == "credencial_de_outra_conta":
        _credencial(repo, dono, outlook, EMAIL_VIVO)          # ...que segue vivo como login de OUTRA conta
    _memoria(db, beto, "b", "Contato", f"escreva para {EMAIL_VIVO}; conta @{ANA}")

    assert emails_so_desta_conta(db, profile_id=ana, account_id=conta, handle=ANA, ancora=True) == []
    svc.retirar_conta_bloqueada(ana, conta, autor="dono")

    assert _mem(db)["m-b"] == ("Contato", f"escreva para {EMAIL_VIVO}; conta {MARCADOR}")
    assert repo.account_row(dono, outlook) is not None        # a conta viva segue


def test_conta_de_email_retirada_leva_o_endereco_e_nao_o_arroba_da_conta_viva(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    caixa = repo.create_account(ana, app_id="outlook", handle=EMAIL_ANA)          # conta de e-mail: o handle é o endereço
    _memoria(db, beto, "b", "Contato", f"{EMAIL_ANA} e @{ANA}")

    svc.retirar_conta_bloqueada(ana, caixa, autor="dono")

    # Só o endereço sai: o @ do Instagram é de OUTRA conta, que segue viva.
    assert _mem(db)["m-b"] == ("Contato", f"{MARCADOR} e @{ANA}")


def test_a_segunda_chamada_nao_muda_nada_e_a_retirada_repetida_tambem(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    conta = _ancora(repo, ana)
    _memoria(db, beto, "b", "Seguiu", f"vi @{ANA}")
    svc.retirar_conta_bloqueada(ana, conta, autor="dono")
    antes = _mem(db)
    assert reescrever_memoria(db, profile_id=ana, handle=ANA, account_id=conta) == {
        "memory_items": 0, "memory_items_de_outras_personas": 0}
    assert svc.retirar_conta_bloqueada(ana, conta, autor="dono")["retirada"] is False
    assert _mem(db) == antes


def test_colisao_de_fingerprint_e_por_perfil_e_nao_quebra_a_transacao(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, caio = _montar(tmp_path)
    conta = _ancora(repo, ana)
    # No Beto, a lembrança reescrita colidiria com outra que já diz `[conta removida]`; no Caio só há UMA lembrança com
    # o texto de outro perfil, e o fingerprint é unique POR PERFIL: não há colisão e nada se renomeia.
    _memoria(db, beto, "b1", "Seguiu", f"vi @{ANA}")
    _memoria(db, beto, "b2", "Seguiu", f"vi {MARCADOR}")
    _memoria(db, caio, "c1", "Seguiu", f"vi @{ANA}")
    # `b2` já guarda o fingerprint do texto que `b1` passará a ter: o UPDATE de `b1` violaria o unique sem o desvio.
    db.execute("UPDATE memory_items SET fingerprint=? WHERE id='m-b2'", (fingerprint("Seguiu", f"vi {MARCADOR}"),))
    antes_b2 = db.scalar("SELECT fingerprint FROM memory_items WHERE id='m-b2'")

    r = svc.retirar_conta_bloqueada(ana, conta, autor="dono")

    assert r["limpezas"]["memory_items"] == 2
    fps = {m["id"]: m["fingerprint"] for m in db.query("SELECT id, fingerprint FROM memory_items")}
    assert fps["m-b2"] == antes_b2 and fps["m-b1"] != fps["m-b2"] and fps["m-b1"].endswith("r" + str(
        db.scalar("SELECT seq FROM memory_items WHERE id='m-b1'")))                         # o do Beto ganhou um próprio
    assert fps["m-c1"] == fingerprint("Seguiu", f"vi {MARCADOR}")                           # o do Caio é o recalculado
    assert _mem(db)["m-b1"] == _mem(db)["m-b2"] == ("Seguiu", f"vi {MARCADOR}")             # a linha não se apagou


def test_handle_em_forma_de_email_so_some_se_o_endereco_e_exclusivo_da_conta() -> None:
    texto = f"login {EMAIL_VIVO} ok"
    assert sem_o_rastro(texto, EMAIL_VIVO, "acc1", []) == texto                       # outra conta viva o usa: fica
    assert sem_o_rastro(texto, EMAIL_VIVO, "acc1", [EMAIL_VIVO]) == f"login {MARCADOR} ok"


def test_outlook_retirado_com_o_endereco_vivo_como_login_do_instagram_nao_o_apaga(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    caixa = repo.create_account(ana, app_id="outlook", handle=EMAIL_VIVO)            # o handle da conta que sai é o e-mail
    _credencial(repo, ana, _ancora(repo, ana), EMAIL_VIVO)                           # e o Instagram da persona o usa de login
    _memoria(db, beto, "b", "Contato", f"escreva para {EMAIL_VIVO} ({caixa})")

    svc.retirar_conta_bloqueada(ana, caixa, autor="dono")

    assert _mem(db)["m-b"] == ("Contato", f"escreva para {EMAIL_VIVO} ({MARCADOR})")  # o endereço fica; o id da conta sai


def test_arroba_de_conta_viva_em_outro_app_ou_de_outra_persona_nao_se_redige(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, caio = _montar(tmp_path)
    conta = _ancora(repo, ana)
    repo.create_account(beto, app_id="outlook", handle=f"@{ANA}")                    # o mesmo @, VIVO, em outro app
    _memoria(db, caio, "c", "Seguiu", f"vi @{ANA} e {conta}")

    r = svc.retirar_conta_bloqueada(ana, conta, autor="dono")

    assert _mem(db)["m-c"] == ("Seguiu", f"vi @{ANA} e {MARCADOR}")                  # o @ vivo fica, o id sai
    assert r["limpezas"]["memory_items"] == 1


def test_arroba_do_cadastro_vivo_conta_menos_o_da_propria_ancora_que_sai(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    conta = _ancora(repo, ana)
    # Conta de outro app que sai com o @ do cadastro VIVO de outra persona: não é rastro.
    assert handle_vivo(db, profile_id=ana, account_id="acc-x", handle=BETO, ancora=False) is True
    # A âncora que sai tem o @ nos dois lugares, e os dois saem: não há conta viva.
    assert handle_vivo(db, profile_id=ana, account_id=conta, handle=ANA, ancora=True) is False
    # Já uma conta de outro app com o @ da âncora da MESMA persona (viva) não o redige.
    assert handle_vivo(db, profile_id=ana, account_id="acc-x", handle=ANA, ancora=False) is True
    assert handle_vivo(db, profile_id=ana, account_id="acc-x", handle="ninguem.por.aqui", ancora=False) is False


# ============================================================ o passe das retiradas antes
def _retirada_antiga(svc: Any, repo: Any, ana: str) -> str:
    """Retira a conta e SÓ DEPOIS põe a memória: reproduz o banco de antes do 29.32 (lápide e evento, memória intacta)."""
    conta = _ancora(repo, ana)
    svc.retirar_conta_bloqueada(ana, conta, autor="dono")
    return conta


def test_passe_unico_acha_pelo_hash_da_lapide_e_pelo_id_do_evento_e_e_idempotente(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, caio = _montar(tmp_path)
    conta = _retirada_antiga(svc, repo, ana)
    _memoria(db, beto, "b", "Seguiu", f"vi @{ANA} e a conta {conta}; banana e {ANA}.silva ficam")
    _memoria(db, caio, "c", "Terceiro", "falou com @alguem.de.fora")
    _memoria(db, caio, "d", "Contato", f"escreva para {EMAIL_ANA}")
    antes = _mem(db)

    ensaio = reescrever_memoria_das_retiradas(db, escrever=False)
    assert ensaio == {"lapides": 1, "ids_de_conta": 1, "extras_aceitos": 0, "extras_recusados_por_estarem_vivos": 0,
                      "extras_recusados_por_serem_curtos": 0, "memory_items": 1}
    assert _mem(db) == antes                                                        # o ensaio só conta
    assert reescrever_memoria_das_retiradas(db)["memory_items"] == 1
    mem = _mem(db)
    assert mem["m-b"] == ("Seguiu", f"vi {MARCADOR} e a conta {MARCADOR}; banana e {ANA}.silva ficam")
    assert mem["m-c"] == antes["m-c"] and mem["m-d"] == antes["m-d"]                # o e-mail não tem fonte no banco
    assert reescrever_memoria_das_retiradas(db)["memory_items"] == 0                # idempotente


def test_passe_unico_nao_toca_o_arroba_que_voltou_a_ser_de_conta_viva(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    _retirada_antiga(svc, repo, ana)
    registrar_lapide(db, app_id="instagram", handle=BETO, profile_id=ana)          # a lápide do @ do Beto, que está VIVO
    _memoria(db, ana, "a", "Seguiu", f"vi @{BETO}")
    _memoria(db, beto, "b", "Seguiu", f"vi @{ANA}")

    r = reescrever_memoria_das_retiradas(db)

    assert r["lapides"] == 1 and r["memory_items"] == 1
    assert _mem(db)["m-a"] == ("Seguiu", f"vi @{BETO}") and _mem(db)["m-b"] == ("Seguiu", f"vi {MARCADOR}")


def test_passe_unico_com_a_lista_do_operador_cobre_o_email_e_recusa_o_que_esta_vivo(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    repo.create_account(beto, app_id="outlook", handle=EMAIL_VIVO)                 # e-mail de conta VIVA
    _memoria(db, beto, "b", "Contato", f"{EMAIL_ANA}; {EMAIL_VIVO}")

    r = reescrever_memoria_das_retiradas(db, extras=[EMAIL_ANA, EMAIL_VIVO, BETO, "  "])

    assert r["extras_aceitos"] == 1 and r["extras_recusados_por_estarem_vivos"] == 2 and r["memory_items"] == 1
    assert _mem(db)["m-b"] == ("Contato", f"{MARCADOR}; {EMAIL_VIVO}")


def test_lista_do_operador_recusa_item_curto_ou_so_de_digitos_e_conta_so_o_numero(tmp_path: Path) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    _memoria(db, beto, "b", "Seguiu", "a conta 12345 viu a ana e @ab")

    r = reescrever_memoria_das_retiradas(db, extras=["a", "@ab", "12345", "@123", " "])

    assert r["extras_recusados_por_serem_curtos"] == 4 and r["extras_aceitos"] == 0 and r["memory_items"] == 0
    assert _mem(db)["m-b"] == ("Seguiu", "a conta 12345 viu a ana e @ab")


# ============================================================ o script de operação
def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("memoria_conta_retirada", RAIZ / "scripts" / "memoria-conta-retirada.py")
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_script_ensaio_so_conta_e_aplicar_grava_sem_ecoar_texto(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    if db.dialect != "sqlite":
        pytest.skip("o script só opera SQLite local")
    _retirada_antiga(svc, repo, ana)
    _memoria(db, beto, "b", "Seguiu", f"vi @{ANA}; {EMAIL_ANA}")
    banco = Path(db.path)
    script = _script()
    monkeypatch.setattr("sys.stdin", io.StringIO(EMAIL_ANA + "\n"))

    assert script.main(["--banco", str(banco), "--lista-stdin"]) == 0
    ensaio = capsys.readouterr().out
    assert "ENSAIO" in ensaio and "memory_items=1" in ensaio and ANA not in ensaio and "correio-teste" not in ensaio
    assert _mem(db)["m-b"] == ("Seguiu", f"vi @{ANA}; {EMAIL_ANA}")                # nada foi gravado

    monkeypatch.setattr("sys.stdin", io.StringIO(EMAIL_ANA + "\n"))
    with pytest.raises(SystemExit):                                                 # sem backup que exista, recusa
        script.main(["--banco", str(banco), "--aplicar", "--lista-stdin"])
    with pytest.raises(SystemExit):
        script.main(["--banco", str(banco), "--aplicar", "--backup", str(tmp_path / "nao-existe")])
    capsys.readouterr()
    assert _mem(db)["m-b"] == ("Seguiu", f"vi @{ANA}; {EMAIL_ANA}")
    monkeypatch.setattr("sys.stdin", io.StringIO(EMAIL_ANA + chr(10)))
    assert script.main(["--banco", str(banco), "--aplicar", "--backup", str(tmp_path), "--lista-stdin"]) == 0
    aplicado = capsys.readouterr().out
    assert "APLICADO" in aplicado and "memory_items=1" in aplicado and ANA not in aplicado
    assert _mem(db)["m-b"] == ("Seguiu", f"vi {MARCADOR}; {MARCADOR}")
    assert script.main(["--banco", str(banco), "--aplicar", "--backup", str(tmp_path)]) == 0
    assert "memory_items=0" in capsys.readouterr().out                              # idempotente


def test_script_ensaio_avisa_quando_a_migracao_do_banco_diverge(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    svc, repo, db, ana, beto, _ = _montar(tmp_path)
    if db.dialect != "sqlite":
        pytest.skip("o script só opera SQLite local")
    db.execute("DELETE FROM schema_migrations WHERE version=(SELECT MAX(version) FROM schema_migrations)")

    assert _script().main(["--banco", str(Path(db.path))]) == 0

    saida = capsys.readouterr()
    assert "AVISO" in saida.err and "ENSAIO" in saida.out
