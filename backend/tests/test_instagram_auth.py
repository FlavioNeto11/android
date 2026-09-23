"""Fase 2 — login automático e sessão verificada.

O marco: cadastrar o perfil, informar a senha e o aparelho, e o sistema abrir o Instagram, autenticar e confirmar a
conta — sem que ninguém digite a senha no emulador e sem que ela apareça em lugar nenhum.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.db import Database
from app.devices.manager import Observation
from app.events import EventBus
from app.integrations.instagram import navigation
from app.integrations.instagram.authentication import InstagramAuthenticator
from app.integrations.instagram.navigation import Screen
from app.integrations.instagram.reconciliation import Outcome
from app.models import ProfileCreate, SessionStatus
from app.security.secret_store import MemoryKeyProvider, SecretStore
from app.security.sensitive_input import SensitiveInputChannel
from app.social.repository import SocialRepository
from app.social.service import SocialService
from app.util import now_iso

from .conftest import make_config
from .fake_instagram import PKG, FakeInstagram

SENHA = "$a=B7ee1#<b-C?S-{"
USUARIO = "mariana.costa91182"


class FakeExecutor:
    async def run(self, fn: Any, *args: Any, timeout: float = 0, label: str = "") -> Any:
        return fn(*args)


class FakeRt:
    def __init__(self, app: FakeInstagram, instance_id: str = "android-02"):
        self.id = instance_id
        self.io = app
        self.adb = app
        self.executor = FakeExecutor()
        self.app_versions: dict[str, str] = {}


class FakeDevices:
    """Só o que o autenticador usa: garantir automação e observar a tela."""

    def __init__(self, app: FakeInstagram):
        self.app = app
        self.automation_ok = True

    async def ensure_automation(self, rt: Any) -> bool:
        return self.automation_ok

    async def observe(self, rt: Any, timeout: float = 0) -> Observation:
        tree = parse_hierarchy(self.app.page_source())
        return Observation(frame_id="f", ts="t", width=720, height=1280, jpeg=None, tree=tree,
                           package=self.app.current_package(), sensitive=tree.sensitive)


def build(tmp_path: Path, app: FakeInstagram, **conf: Any) -> tuple[InstagramAuthenticator, SocialRepository, Any, Database]:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    for k, v in conf.items():
        setattr(cfg.file.instagram, k, v)
    cfg.file.instagram.settle_s = 0.01
    cfg.file.instagram.submit_wait_s = 6
    # `db_path`, e nao `db_dsn`, de proposito: esta prova e presa ao arquivo — ela le os BYTES do banco para
    # garantir que nenhuma senha ficou em claro, e arquivo nao existe no PostgreSQL. Registrado em docs/banco.md.
    db = Database(cfg.db_path)
    db.migrate()
    bus = EventBus(db)
    secrets = SecretStore(db, MemoryKeyProvider())
    repo = SocialRepository(db)
    social = SocialService(repo, secrets, bus, known_instances=lambda: ["android-01", "android-02"])
    canal = SensitiveInputChannel(lambda: True)          # mascaramento comprovado nos testes
    auth = InstagramAuthenticator(cfg, FakeDevices(app), repo, secrets, canal, bus)
    cfg.file.instagram.open_timeout_s = 0.5
    auth.focus_poll_s = 0.01
    return auth, repo, social, db


def cadastrar(social: SocialService, *, username: str = USUARIO, senha: str = SENHA,
              instance_id: str = "android-02") -> str:
    return social.create_profile(ProfileCreate(username=username, password=senha, instance_id=instance_id,
                                               first_name="Mariana", last_name="Costa")).id


# ---------------------------------------------------------------- classificação de tela
def tela(xml: str) -> Any:
    return parse_hierarchy(xml)


def test_login_e_reconhecido_pela_estrutura_nao_pelo_id() -> None:
    app = FakeInstagram()
    c = navigation.classify(tela(app.page_source()), package=PKG, locale="en-US")
    assert c.screen is Screen.LOGIN and c.form and c.form.complete
    assert c.form.password.password                                  # âncora é o atributo, não a classe
    assert c.form.username.bounds[3] <= c.form.password.bounds[1]    # usuário fica acima da senha
    assert c.form.submit.text == "Log in"                            # "Log in with Facebook" foi descartado


def test_leitura_da_tela_separa_conteudo_de_rotulo_de_interface() -> None:
    """O que vai ao redator tem de ser o que está ESCRITO na publicação, não a barra de botões.

    Sem esse filtro, o modelo receberia "Curtir Comentar Compartilhar Início Pesquisa" como se fosse o assunto —
    e comentaria sobre a interface do Instagram."""
    xml = ('<hierarchy>'
           '<node text="Céu de outubro visto pelo Hubble, em cores reais" bounds="[0,100][720,200]"/>'
           '<node text="Curtir" bounds="[0,210][100,260]" clickable="true"/>'
           '<node text="Comentar" bounds="[110,210][240,260]" clickable="true"/>'
           '<node text="1.234" bounds="[250,210][330,260]"/>'
           '<node text="há 3 h" bounds="[340,210][420,260]"/>'
           '<node text="que foto absurda, parabéns pelo trabalho" bounds="[0,270][720,330]"/>'
           '<node text="que foto absurda, parabéns pelo trabalho" bounds="[0,340][720,400]"/>'
           '<node text="Início" bounds="[0,900][100,960]" clickable="true"/>'
           '</hierarchy>')
    lido = navigation.conteudo_visivel(tela(xml))
    assert lido == ("Céu de outubro visto pelo Hubble, em cores reais\n"
                    "que foto absurda, parabéns pelo trabalho")      # repetido entra uma vez só
    assert "Curtir" not in lido and "Início" not in lido and "1.234" not in lido


def test_comentario_lido_e_o_da_pessoa_certa_ou_e_nenhum() -> None:
    """Responder sem ler o comentário é responder no escuro. Mas atribuir a fala do vizinho a quem se responde é
    pior: sai resposta sem sentido e, quando a interação se confirma, memória falsa no nome da pessoa errada."""
    xml = ('<hierarchy>'
           '<node text="ana.paula said adorei esse lugar, fui em janeiro" bounds="[0,100][720,160]"/>'
           '<node text="joao said discordo totalmente" bounds="[0,170][720,230]"/>'
           '<node text="Ver todas as respostas" bounds="[0,240][300,290]" clickable="true"/>'
           '</hierarchy>')
    arvore = tela(xml)
    assert navigation.comentario_de(arvore, "@ana.paula") == "adorei esse lugar, fui em janeiro"
    assert navigation.comentario_de(arvore, "joao") == "discordo totalmente"
    # quem não está na tela não tem comentário lido: vazio é "escreva sem isto", nunca um palpite
    assert navigation.comentario_de(arvore, "@carla") == ""
    assert navigation.comentario_de(arvore, "") == ""


def test_tela_com_campo_de_senha_nao_devolve_nada() -> None:
    """A regra vale em todo caminho: tela sensível não vira contexto, não vira prompt, não sai daqui."""
    app = FakeInstagram()
    assert navigation.conteudo_visivel(tela(app.page_source())) == ""


def test_leitura_da_tela_tem_teto_de_tamanho() -> None:
    """Legenda de Instagram vai a 2200 caracteres: sem corte, ela empurraria persona e memória para o fim do
    prompt — e posição importa."""
    longa = "palavra " * 400
    xml = f'<hierarchy><node text="{longa.strip()}" bounds="[0,100][720,900]"/></hierarchy>'
    assert len(navigation.conteudo_visivel(tela(xml), limite=200)) <= 200


def test_challenge_e_dois_fatores_vem_antes_do_login() -> None:
    """Confundir isso faria o sistema digitar a senha numa tela de código."""
    app = FakeInstagram(screen="challenge")
    assert navigation.classify(tela(app.page_source()), package=PKG).screen is Screen.CHALLENGE
    app.screen = "two_factor"
    assert navigation.classify(tela(app.page_source()), package=PKG).screen is Screen.TWO_FACTOR


def test_botao_ambiguo_deixa_o_formulario_incompleto() -> None:
    xml = ('<hierarchy>'
           '<node class="android.widget.EditText" text="" bounds="[40,400][680,470]" clickable="true" enabled="true"/>'
           '<node class="android.widget.EditText" text="" password="true" bounds="[40,500][680,570]" clickable="true" enabled="true"/>'
           '<node class="android.widget.Button" text="Log in" bounds="[40,620][340,690]" clickable="true" enabled="true"/>'
           '<node class="android.widget.Button" text="Log in" bounds="[360,620][680,690]" clickable="true" enabled="true"/>'
           '</hierarchy>')
    form = navigation.login_form(tela(xml), "en-US")
    assert form is not None and form.submit is None      # dois candidatos = incerteza, nunca "o primeiro"
    assert not form.complete


def test_outro_app_em_primeiro_plano_nao_e_classificado() -> None:
    app = FakeInstagram()
    assert navigation.classify(tela(app.page_source()), package="com.outro.app").screen is Screen.UNKNOWN


# ---------------------------------------------------------------- o marco
async def test_conecta_autentica_e_verifica_a_conta(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.ready and r.outcome is Outcome.SESSION_READY
        assert r.observed_username == USUARIO
        assert app.account == USUARIO                                # entrou de verdade no app
        sess = repo.session_row(pid)
        assert sess["status"] == SessionStatus.session_ready.value and sess["observed_username"] == USUARIO
        assert sess["verified_at"] and repo.profile_row(pid)["last_verified_at"]
    finally:
        db.close()


async def test_abertura_a_frio_espera_o_app_antes_de_classificar(tmp_path: Path) -> None:
    """Medido no Instagram real: segundos sem janela em foco depois de abrir. Classificar nesse intervalo leria o
    launcher, e a conexão sairia incerta sem motivo real."""
    app = FakeInstagram(stored_password=SENHA, cold_start_reads=4)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.SESSION_READY, r.detail
        assert app.focus_reads >= 5                                  # esperou a primeira tela, não olhou uma vez só
    finally:
        db.close()


async def test_app_que_nao_aparece_no_prazo_nao_recebe_a_senha(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, cold_start_reads=10_000)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.UNCERTAIN and "outro app" in r.detail
        assert app.typed == [] and app.account is None               # nada digitado numa tela que não é a do app
    finally:
        db.close()


async def test_dispensa_salvar_login_e_confirma_a_conta(tmp_path: Path) -> None:
    """Depois de entrar (inclusive resolvendo um desafio), o Instagram mostra "Salvar dados de login?" — um modal que
    tapa a barra de perfil. O fluxo dispensa em "Agora não" (não salva na nuvem) e então lê a conta."""
    app = FakeInstagram(account=USUARIO, screen="save_login", stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.SESSION_READY, r.detail
        assert r.observed_username == USUARIO
        assert "tap:190,930" in app.calls and "tap:520,930" not in app.calls   # "Agora não", nunca "Salvar"
        assert repo.session_row(pid)["status"] == SessionStatus.session_ready.value
    finally:
        db.close()


async def test_toca_em_entrar_na_posicao_atual_depois_do_teclado_subir(tmp_path: Path) -> None:
    """No aparelho real, preencher os campos abre o teclado e empurra o botão Entrar para cima. Tocar na posição
    lida com o formulário vazio erra o botão: os campos ficam preenchidos e a tela não sai do login, sem erro. O
    login relê a tela depois de preencher e usa a posição ATUAL do botão."""
    app = FakeInstagram(stored_password=SENHA, keyboard_shift=True)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.SESSION_READY, r.detail       # sem a releitura, ficaria em uncertain no login
        assert app.account == USUARIO and "submit" in app.calls   # o botão foi de fato acionado
    finally:
        db.close()


async def test_a_senha_nao_aparece_em_lugar_nenhum_depois_do_login(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        assert (await auth.ensure_session(FakeRt(app), pid)).ready
        despejo = ""
        # A linha é um `dict` (o banco é neutro de dialeto): acessa por nome, nunca por posição.
        for linha in db.query("SELECT name FROM sqlite_master WHERE type='table'"):
            t = linha["name"]
            for row in db.execute(f"SELECT * FROM {t}"):             # noqa: S608 - nomes vêm do esquema
                despejo += str(dict(row))
        assert SENHA not in despejo                                   # banco, eventos e tentativas
        assert SENHA in app.typed                                     # mas chegou ao aparelho
    finally:
        db.close()


async def test_sessao_existente_e_reaproveitada_sem_digitar_senha(tmp_path: Path) -> None:
    app = FakeInstagram(account=USUARIO, screen="feed", stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.ready and not r.attempted_login
        assert app.typed == []                                        # ninguém digitou nada
        assert repo.auth_attempts(pid) == []                          # nem houve tentativa de login
    finally:
        db.close()


async def test_conta_de_outro_no_feed_nao_vira_conta_errada(tmp_path: Path) -> None:
    """O feed mostra o @ de reels e stories de outras contas. A identidade sai SÓ do cabeçalho de perfil: um @ do
    feed não pode ser lido como a conta logada — no aparelho real isso virou um 'conta errada' falso."""
    app = FakeInstagram(account=USUARIO, screen="feed", show_username_on_feed=False,
                        foreign_on_feed="kpop_glam_cam")
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.SESSION_READY, r.detail       # foi ler no perfil, não no feed
        assert r.observed_username == USUARIO                      # nunca @kpop_glam_cam
    finally:
        db.close()


async def test_conta_lida_pela_aba_de_perfil_quando_o_feed_nao_mostra(tmp_path: Path) -> None:
    app = FakeInstagram(account=USUARIO, screen="feed", show_username_on_feed=False)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.ready and app.screen == "profile"                    # abriu o perfil para confirmar
    finally:
        db.close()


# ---------------------------------------------------------------- desfechos que nunca repetem sozinhos
async def test_senha_errada_bloqueia_novas_tentativas_ate_trocar_a_senha(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password="outra-senha")
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.INVALID_CREDENTIAL
        assert repo.credential_row(pid)["status"] == "invalid"

        app.calls.clear()
        r2 = await auth.ensure_session(FakeRt(app), pid)
        assert r2.outcome is Outcome.INVALID_CREDENTIAL
        assert app.calls == []                                        # nem tocou no aparelho

        from app.models import CredentialUpdate
        social.set_credential(pid, CredentialUpdate(password="outra-senha"))
        r3 = await auth.ensure_session(FakeRt(app), pid)
        assert r3.ready                                               # senha nova destrava
    finally:
        db.close()


async def test_challenge_nao_gera_nova_tentativa_e_pede_a_pessoa(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, challenge_on_login=True)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE
        assert repo.session_row(pid)["status"] == SessionStatus.auth_challenge.value
        assert "Assuma o controle" in (repo.session_row(pid)["detail"] or "")

        antes = len(app.typed)
        r2 = await auth.ensure_session(FakeRt(app), pid)              # app continua na tela de challenge
        assert r2.outcome is Outcome.AUTH_CHALLENGE
        assert len(app.typed) == antes                                # não digitou de novo
    finally:
        db.close()


async def test_dois_fatores_tambem_espera_a_pessoa(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA, two_factor_on_login=True)
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        assert (await auth.ensure_session(FakeRt(app), pid)).outcome is Outcome.AUTH_CHALLENGE
    finally:
        db.close()


async def test_conta_errada_bloqueia_com_a_troca_automatica_desligada(tmp_path: Path) -> None:
    app = FakeInstagram(account="lucas.almeida9484", screen="feed")
    auth, repo, social, db = build(tmp_path, app, auto_switch_account=False)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.WRONG_ACCOUNT and r.observed_username == "lucas.almeida9484"
        assert repo.session_row(pid)["status"] == SessionStatus.wrong_account.value
        assert app.account == "lucas.almeida9484"                     # não deslogou ninguém
        assert app.typed == []
    finally:
        db.close()


async def test_toque_que_nao_chegou_ao_aparelho_pode_ser_repetido(tmp_path: Path) -> None:
    """Falha COMPROVADA antes de qualquer efeito é o único caso que autoriza nova tentativa."""
    app = FakeInstagram(stored_password=SENHA, submit_fault="lost")
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.RETRYABLE and app.account is None
        assert repo.credential_row(pid)["failed_attempts"] == 1
        assert (await auth.ensure_session(FakeRt(app), pid)).ready    # segunda tentativa entra
    finally:
        db.close()


async def test_teto_de_tentativas_impoe_intervalo(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app, max_auth_attempts=2, auth_cooldown_s=3600)
    try:
        pid = cadastrar(social)
        for _ in range(2):
            app.submit_fault = "lost"
            await auth.ensure_session(FakeRt(app), pid)
        cred = repo.credential_row(pid)
        assert cred["failed_attempts"] >= 2 and cred["blocked_until"]
        app.calls.clear()
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.INVALID_CREDENTIAL and "intervalo" in r.detail
        assert app.calls == []                                        # o intervalo é respeitado sem tocar no aparelho
    finally:
        db.close()


async def test_verificar_conta_nao_faz_login_em_aparelho_deslogado(tmp_path: Path) -> None:
    """"Verificar conta" promete só observar. Num aparelho deslogado ela parava na tela de login e AUTENTICAVA:
    cada clique gastava uma tentativa de login real — logo depois de um desafio, que é justo quando o painel
    sugere esse botão. Agora para na tela de login e diz o que fazer."""
    app = FakeInstagram(stored_password=SENHA)                    # deslogado, na tela de login
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid, observe_only=True)
        assert r.outcome is Outcome.UNCERTAIN and "Conectar" in r.detail
        assert app.typed == [] and app.account is None             # nada digitado, ninguém autenticado
        assert "submit" not in app.calls                           # e nenhum envio de login
        assert repo.auth_attempts(pid) == []                       # nem tentativa registrada
        assert repo.session_row(pid)["status"] == SessionStatus.auth_required.value
    finally:
        db.close()


async def test_desfecho_sem_sucesso_depois_do_envio_conta_para_o_teto(tmp_path: Path) -> None:
    """O teto é o único freio contra bloquear a conta, e ele só contava RETRYABLE. Um modo de falha que se repete
    (desafio, conta errada, tela ilegível) gerava envios de senha REAIS sem limite. Agora todo desfecho pós-envio
    que não é sucesso conta."""
    app = FakeInstagram(stored_password=SENHA, challenge_on_login=True)
    auth, repo, social, db = build(tmp_path, app, max_auth_attempts=2, auth_cooldown_s=3600)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.AUTH_CHALLENGE                 # senha enviada, desfecho não é sucesso
        assert repo.credential_row(pid)["failed_attempts"] == 1    # antes ficava em 0 e nunca travava
    finally:
        db.close()


async def test_campo_de_usuario_e_conferido_antes_de_enviar(tmp_path: Path) -> None:
    """Confere o que ficou no campo: pega limpeza que não aconteceu e conta errada ANTES do commit."""
    app = FakeInstagram(stored_password=SENHA)

    original = app.type_text

    def type_text(text: str, *, clear_first: bool) -> None:
        campo = getattr(app, "_focus", "username")
        if campo == "username":
            original("outra.coisa", clear_first=clear_first)          # o campo não fica com o valor pedido
            return
        original(text, clear_first=clear_first)

    app.type_text = type_text  # type: ignore[method-assign]
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.RETRYABLE and "campo de usuário" in r.detail
        assert "submit" not in app.calls                              # nada foi enviado
        assert app.account is None
    finally:
        db.close()


async def test_sem_credencial_nao_toca_no_aparelho(tmp_path: Path) -> None:
    app = FakeInstagram()
    auth, repo, social, db = build(tmp_path, app)
    try:
        pid = social.create_profile(ProfileCreate(username=USUARIO, password=None,
                                                  instance_id="android-02")).id
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.INVALID_CREDENTIAL and "credencial" in r.detail
        assert app.calls == []
    finally:
        db.close()


async def test_canal_sensivel_bloqueado_nao_digita_a_senha(tmp_path: Path) -> None:
    app = FakeInstagram(stored_password=SENHA)
    auth, repo, social, db = build(tmp_path, app)
    auth.sensitive = SensitiveInputChannel(lambda: False)             # mascaramento não comprovado
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.RETRYABLE and "mascaramento" in r.detail
        assert SENHA not in app.typed and app.account is None
    finally:
        db.close()


async def test_automacao_indisponivel_e_repetivel(tmp_path: Path) -> None:
    app = FakeInstagram()
    auth, repo, social, db = build(tmp_path, app)
    auth.devices.automation_ok = False                                # type: ignore[attr-defined]
    try:
        pid = cadastrar(social)
        r = await auth.ensure_session(FakeRt(app), pid)
        assert r.outcome is Outcome.RETRYABLE and "automação" in r.detail
    finally:
        db.close()


# ---------------------------------------------------------------- a porta da sessão no despacho
async def test_aparelho_sem_perfil_nao_tem_porta_de_sessao(harness: Any) -> None:
    """O QA Messenger e todo o caminho antigo seguem iguais: a porta só existe onde há perfil vinculado."""
    state = harness.state
    assert state._session_gate(state.devices.get("android-01")) is None


async def test_porta_pede_autenticacao_quando_a_sessao_nao_foi_verificada(harness: Any) -> None:
    state = harness.state
    state.appium.log_masking_active = True            # canal sensível comprovado: não é o que este teste cobre
    pid = state.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA,
                                                    instance_id="android-01")).id
    porta = state._session_gate(state.devices.get("android-01"))
    assert porta is not None
    motivo, trabalho = porta
    assert trabalho is not None                      # dá para resolver sozinho: autenticar
    assert "sessão" in motivo or "verificada" in motivo
    assert pid


async def test_porta_nao_agenda_login_automatico_com_canal_sensivel_indisponivel(harness: Any) -> None:
    """Achado #105: sem o mascaramento de log comprovado, o agendador não deve insistir digitando o usuário e
    esbarrando em SensitiveInputUnavailable a cada tick — a porta fica bloqueada com a dica do health."""
    state = harness.state
    assert state.appium.log_masking_active is False   # harness não sobe Appium de verdade (manage_appium=False)
    state.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA, instance_id="android-01"))
    motivo, trabalho = state._session_gate(state.devices.get("android-01"))
    assert trabalho is None
    assert "mascaramento" in motivo


async def test_porta_nao_insiste_quando_so_uma_pessoa_resolve(harness: Any) -> None:
    state = harness.state
    pid = state.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA,
                                                    instance_id="android-01")).id
    for estado in (SessionStatus.auth_challenge, SessionStatus.wrong_account):
        state.social_repo.set_session(pid, status=estado, instance_id="android-01", detail=f"parado em {estado.value}")
        motivo, trabalho = state._session_gate(state.devices.get("android-01"))
        assert trabalho is None                      # nenhum worker é disparado
        assert estado.value in motivo


async def test_porta_nao_tenta_com_credencial_recusada(harness: Any) -> None:
    state = harness.state
    pid = state.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA,
                                                    instance_id="android-01")).id
    state.social_repo.mark_credential(pid, status="invalid")
    motivo, trabalho = state._session_gate(state.devices.get("android-01"))
    assert trabalho is None


async def test_sessao_pronta_libera_o_despacho(harness: Any) -> None:
    state = harness.state
    pid = state.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA,
                                                    instance_id="android-01")).id
    # Verificada AGORA. A data era fixa (2026-09-17) e envelhecia junto com o calendário: desde que a sessão
    # passou a ter validade (item 3.3), uma verificação de dias atrás não libera o despacho — ela manda reler o
    # aparelho antes da tarefa. O que este teste afirma é "sessão pronta libera", e é isto que continua aqui;
    # o caso da sessão vencida está em `test_sessao_com_validade.py`.
    state.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01",
                                  observed_username=USUARIO, verified_at=now_iso())
    assert state._session_gate(state.devices.get("android-01")) is None


async def test_item_fica_bloqueado_no_painel_quando_depende_de_pessoa(harness: Any) -> None:
    """Prova ponta a ponta: com desafio pendente, a execução não roda e o painel explica o motivo."""
    state = harness.state
    pid = state.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA,
                                                    instance_id="android-01")).id
    state.social_repo.set_session(pid, status=SessionStatus.auth_challenge, instance_id="android-01",
                                  detail="O Instagram exige confirmação adicional.")
    # android-01 opera o INSTAGRAM. Desde o item 6.1 a porta de sessão é POR APP: sem amarrar o aparelho, a
    # tarefa seria de QA Messenger — e tarefa de QA num aparelho com perfil do Instagram em desafio deve mesmo
    # despachar, que é justamente o defeito corrigido ali. O que este teste guarda é o outro lado: para a tarefa
    # DAQUELE app, o desafio bloqueia o item e o painel explica o motivo.
    #
    # Só android-01: a execução deixou de aceitar seleção que mistura aplicativos (6.1 de novo), e pôr android-02
    # no Instagram também não serviria — o provedor simulado só sabe executar as etapas do QA Messenger. Que um
    # aparelho parado não segura os demais está provado em
    # test_execution.py::test_falha_e_tela_inesperada_em_um_aparelho_nao_param_os_demais.
    state.db.execute("UPDATE instances SET app_id='instagram' WHERE id='android-01'")
    state.devices.get("android-01").app_id = "instagram"
    run = harness.run(["android-01"])

    def bloqueado() -> bool:
        row = state.db.one("SELECT status FROM objectives WHERE id=?", (f"{run.id}:android-01",))
        return bool(row) and row["status"] == "waiting_user"

    await harness.wait(bloqueado, timeout=30, what="android-01 bloqueado pela porta da sessão")
    motivo = state.db.one("SELECT blocked_reason FROM objectives WHERE id=?", (f"{run.id}:android-01",))
    assert "confirmação adicional" in (motivo["blocked_reason"] or "")
    await harness.wait_run(run.id, statuses=("completed", "completed_with_issues", "failed"), timeout=90)
    assert not harness.fakes["android-01"].messages      # nada foi enviado pelo aparelho bloqueado


async def test_reset_do_aparelho_invalida_a_sessao(harness: Any) -> None:
    """Sessão é cache: wipe apaga os dados do app, então o que estava observado deixa de valer."""
    state = harness.state
    pid = state.social.create_profile(ProfileCreate(username=USUARIO, password=SENHA,
                                                    instance_id="android-01")).id
    state.social_repo.set_session(pid, status=SessionStatus.session_ready, instance_id="android-01",
                                  observed_username=USUARIO, verified_at="2026-09-17T10:00:00Z")
    state.devices._invalidate_session(state.devices.get("android-01"), "o aparelho foi resetado")
    sess = state.social_repo.session_row(pid)
    assert sess["status"] == SessionStatus.unknown.value
    assert "resetado" in (sess["detail"] or "")
    assert state.social_repo.credential_row(pid) is not None      # a credencial continua: ela é do perfil
