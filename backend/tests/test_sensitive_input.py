"""Fase 0A — a credencial não pode ser registrada em lugar nenhum.

O vazamento que estes testes protegem é real e foi reproduzido neste repositório: o PIN do app de QA aparecia em
texto claro em `data/logs/appium.log`, porque o Appium registra o corpo de cada requisição e o projeto o iniciava
com `--log-level info` sem mascaramento.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from typing import Any
from xml.sax.saxutils import quoteattr

import pytest

from app.automation.appium_server import LOG_FILTER_RULES
from app.automation.hierarchy import MOTIVO_LOJA, RegraDeTelaSensivel, parse_hierarchy
from app.security.redaction import MASK, RedactingFilter, redact, redact_obj
from app.security.sensitive_input import SensitiveInputChannel, SensitiveInputError, SensitiveInputUnavailable

from .conftest import Harness

SECRET = "$a=B7ee1#<b-C?S-{"          # formato realista: símbolos, sem espaço
LEAK_TEMPLATE = '[HTTP] --> POST /session/abc/execute/sync {"script":"mobile: type","args":[{"text":"VALOR"}]}'


def leak_line(secret: str = SECRET) -> str:
    return LEAK_TEMPLATE.replace("VALOR", secret)


def apply_appium_rules(line: str) -> str:
    """Aplica as regras exatamente como o Appium aplicaria (JS `String.replace`, com grupos `$1`)."""
    for rule in LOG_FILTER_RULES:
        replacement = re.sub(r"\$(\d)", r"\\\1", rule["replacer"])
        line = re.compile(rule["pattern"]).sub(replacement, line)
    return line


class FakeSecretField:
    """Campo de senha de mentira: guarda o texto e registra cada chamada do driver."""

    def __init__(self, text: str = "", *, clearable: bool = True, raise_with: str | None = None,
                 swallows: bool = False, leituras_atrasadas: int = 0):
        self.text = text
        # `leituras_atrasadas`: quantas leituras DEPOIS de digitar ainda mostram o campo vazio — a árvore de um
        # WebView que demora a refletir a máscara (página de senha da Microsoft, 30/09/2026).
        self.leituras_atrasadas = leituras_atrasadas
        self._digitou = False
        self.clearable = clearable
        self.raise_with = raise_with
        # `swallows`: o toque não focou este campo, então a digitação vai para OUTRO lugar da tela e o campo de
        # senha continua vazio. É o caso que a pós-condição do canal tem de pegar.
        self.swallows = swallows
        self.calls: list[tuple[Any, ...]] = []

    def tap(self, x: int, y: int) -> None:
        self.calls.append(("tap", x, y))

    def type_text(self, text: str, *, clear_first: bool) -> None:
        self.calls.append(("type", text, clear_first))
        if self.raise_with:
            raise RuntimeError(f"falha do driver ao digitar {self.raise_with}")
        if clear_first and self.clearable:
            self.text = ""
        if self.swallows:
            return                                       # o texto foi para outro campo: aqui não entra nada
        self.text += text
        self._digitou = self._digitou or bool(text)

    # -- o que o canal enxerga ------------------------------------------------
    def tree(self) -> Any:
        texto = self.text
        if self._digitou and self.leituras_atrasadas > 0:
            self.leituras_atrasadas -= 1
            texto = ""
        node = ('<node class="android.widget.EditText" resource-id="com.instagram.android:id/password" '
                f"text={quoteattr(texto)} password=\"true\" clickable=\"true\" enabled=\"true\" "
                'bounds="[40,300][680,380]"/>')
        return parse_hierarchy("<hierarchy>" + node + "</hierarchy>")

    async def observe(self) -> Any:
        return self.tree()

    @staticmethod
    def locate(tree: Any) -> Any:
        found = tree.find_selector("id=password")
        return found[0] if found else None

    @property
    def typed(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "type" and c[1]]


async def run_call(fn: Any, *args: Any, timeout: float = 0, label: str = "") -> Any:
    return fn(*args)


def channel(active: bool = True) -> SensitiveInputChannel:
    return SensitiveInputChannel(lambda: active)


# ---------------------------------------------------------------- canal de entrada sensível
async def test_preenche_uma_unica_vez_e_nao_registra_o_valor_nem_o_tamanho() -> None:
    field = FakeSecretField()
    receipt = await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                                   secret=lambda: SECRET)
    assert field.typed == [SECRET]                       # digitado exatamente uma vez
    assert ("type", "", True) in field.calls             # limpou antes
    assert field.calls[0][0] == "tap"                    # focou antes de limpar
    registro = json.dumps(receipt.to_dict())
    assert receipt.to_dict() == {"sensitive_input_completed": True, "field": "com.instagram.android:id/password"}
    assert SECRET not in registro and str(len(SECRET)) not in registro   # nem o valor, nem o comprimento


async def test_campo_com_resto_de_tentativa_anterior_e_limpo_antes() -> None:
    field = FakeSecretField(text=SECRET)                 # tentativa anterior preencheu e caiu antes de enviar
    await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate, secret=lambda: SECRET)
    assert field.text == SECRET                          # não ficou concatenado
    assert field.typed == [SECRET]


async def test_campo_que_nao_esvazia_aborta_sem_digitar_a_senha() -> None:
    field = FakeSecretField(text="resto", clearable=False)
    with pytest.raises(SensitiveInputError, match="não ficou vazio"):
        await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                             secret=lambda: SECRET)
    assert field.typed == []                             # a senha nunca foi digitada


async def test_senha_que_nao_chegou_ao_campo_e_denunciada(monkeypatch: Any) -> None:
    """Se o toque não focar o campo de senha, a digitação vai para outro lugar da tela — possivelmente o campo de
    usuário, em texto claro, que segue no envio. A pós-condição tem de pegar isso: campo de senha vazio depois de
    digitar é falha, não sucesso. A checagem antiga incluía `after.password`, que é SEMPRE verdadeiro aqui, e por
    isso nunca disparava."""
    import app.security.sensitive_input as canal
    monkeypatch.setattr(canal, "PAUSA_ENTRE_CONFERENCIAS_S", 0)    # as releituras não salvam o que não chegou
    field = FakeSecretField(swallows=True)
    with pytest.raises(SensitiveInputError, match="continuou vazio"):
        await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                             secret=lambda: SECRET)
    assert field.text == ""                              # o campo de senha ficou mesmo vazio


async def test_webview_que_demora_a_mostrar_a_mascara_nao_e_campo_vazio(monkeypatch: Any) -> None:
    """A página de senha da Microsoft (WebView) mostrou o campo vazio na primeira leitura depois de digitar, com a senha
    já nele (android-06, 30/09/2026). O canal relê, sem digitar de novo."""
    import app.security.sensitive_input as canal
    monkeypatch.setattr(canal, "PAUSA_ENTRE_CONFERENCIAS_S", 0)
    field = FakeSecretField(leituras_atrasadas=2)
    await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate, secret=lambda: SECRET)
    assert field.typed == [SECRET]                       # uma digitação só


async def test_erro_do_driver_nunca_propaga_o_texto_digitado() -> None:
    field = FakeSecretField(raise_with=SECRET)
    with pytest.raises(SensitiveInputError) as exc:
        await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                             secret=lambda: SECRET)
    assert SECRET not in str(exc.value)
    assert exc.value.__cause__ is None and exc.value.__context__ is None   # nada encadeado que vaze no traceback


async def test_sem_mascaramento_comprovado_o_canal_se_recusa_a_operar() -> None:
    field = FakeSecretField()
    with pytest.raises(SensitiveInputUnavailable):
        await channel(active=False).fill(call=run_call, io=field, observe=field.observe, locate=field.locate,
                                         secret=lambda: SECRET)
    assert field.calls == []                             # não tocou no aparelho


async def test_segredo_so_e_resolvido_no_ultimo_instante() -> None:
    field = FakeSecretField()
    resolved: list[int] = []

    def secret() -> str:
        resolved.append(len(field.calls))
        return SECRET

    await channel().fill(call=run_call, io=field, observe=field.observe, locate=field.locate, secret=secret)
    assert resolved == [2]        # só depois de focar e limpar; uma única resolução


# ---------------------------------------------------------------- mascaramento na origem (Appium)
def test_regras_do_appium_sao_validas_e_mascaram_o_vazamento_conhecido() -> None:
    assert json.loads(json.dumps(LOG_FILTER_RULES))      # o Appium recusa subir com regra inválida
    masked = apply_appium_rules(leak_line())
    assert SECRET not in masked and "**SECURE**" in masked


def test_envio_de_teclas_do_webdriver_tambem_e_mascarado() -> None:
    line = '{"text":"' + SECRET + '","value":["a","b"]}'
    assert SECRET not in apply_appium_rules(line)


def test_pin_do_app_de_qa_tambem_seria_mascarado() -> None:
    # o valor que hoje está em claro em data/logs/appium.log
    assert '"text":"1234"' not in apply_appium_rules(leak_line("1234"))


# ---------------------------------------------------------------- redator (camada extra)
def test_redator_cobre_os_formatos_que_carregam_segredo() -> None:
    assert SECRET not in (redact(leak_line()) or "")
    assert SECRET not in (redact('{"password": "' + SECRET + '"}') or "")
    assert SECRET not in (redact("senha=" + SECRET) or "")
    assert SECRET not in json.dumps(redact_obj({"credential": {"password": SECRET}}))
    assert redact("mensagem comum sem segredo") == "mensagem comum sem segredo"


def test_nome_de_variavel_unido_por_sublinhado_tambem_e_redigido() -> None:
    """Regressao de um vazamento real, achado medindo a redacao em vez de a ler.

    O padrao exigia `\btoken\b`, e `\b` nao existe entre `_` e a letra seguinte: os dois sao caractere de palavra.
    Resultado: `X-Auth-Token` (hifen) era redigido e `API_TOKEN` (sublinhado) saia EM CLARO — a forma que aparece em
    `.env`, em log de ambiente e em mensagem de erro de configuracao. `INSTAGRAM_CREDENTIALS_MASTER_KEY`, a chave
    mestra do cofre, tinha o mesmo problema.
    """
    for linha in (f"API_TOKEN={SECRET}", f"db_password={SECRET}", f"INSTAGRAM_CREDENTIALS_MASTER_KEY={SECRET}",
                  # O nome neutro (achado #88) e o da chave ANTERIOR, que o `rekey` le do ambiente: os tres
                  # aparecem em `.env` e em log de ambiente, e os tres tem de sair mascarados. O qualificador da
                  # segunda vem na FRENTE por causa DESTE teste: com `..._MASTER_KEY_ANTERIOR=` a chave saia em
                  # claro, porque o padrao cobre prefixo e nao sufixo: nao ha fronteira de palavra entre `Y` e `_`.
                  f"CREDENTIALS_MASTER_KEY={SECRET}", f"PREVIOUS_CREDENTIALS_MASTER_KEY={SECRET}",
                  f"DB_PASSWD: {SECRET}", f"user_secret={SECRET}", f"X-Auth-Token: {SECRET}"):
        assert SECRET not in (redact(linha) or ""), linha


def test_senha_dentro_do_dsn_do_banco_e_redigida() -> None:
    """A senha do PostgreSQL viaja no MEIO de uma URL, e `DATABASE_URL` nao contem nenhuma palavra da lista de
    chaves sensiveis — entao o padrao de par chave/valor nao a alcancava. Ela aparece onde mais doi: na mensagem
    de erro de conexao, que e o log que alguem cola num chamado de suporte (docs/banco.md, "Seguranca do banco
    entre maquinas").
    """
    dsn = f"postgresql://parque:{SECRET}@db.parque.local:5432/parque?sslmode=verify-full"
    redigido = redact(f"falha ao conectar: {dsn}") or ""
    assert SECRET not in redigido
    assert "postgresql://parque:" in redigido and "@db.parque.local:5432/parque" in redigido
    assert SECRET not in (redact(f"DATABASE_URL={dsn}") or "")
    # DSN sem senha continua legivel por inteiro, e URL comum nao e tocada.
    assert redact("postgresql://parque@db:5432/parque") == "postgresql://parque@db:5432/parque"
    assert redact("veja http://127.0.0.1:8000/api/health") == "veja http://127.0.0.1:8000/api/health"


def test_redacao_nao_mastiga_contagem_de_tokens_de_ia() -> None:
    """O outro lado do mesmo cuidado: `tokens=1234` e contagem de custo, nao segredo. A fronteira final do padrao e
    o que a preserva — sem ela, o relatorio de gasto viria todo mascarado."""
    assert redact("etapa concluida: tokens=1234, cache_read=90") == "etapa concluida: tokens=1234, cache_read=90"
    assert redact("run_id=r-20260921-aabbcc") == "run_id=r-20260921-aabbcc"


def test_filtro_de_log_redige_a_mensagem_formatada() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "senha=%s", (SECRET,), None)
    assert RedactingFilter().filter(record) is True
    assert SECRET not in record.getMessage()


# ---------------------------------------------------------------- achado #128: os formatos que faltavam
@pytest.mark.parametrize("modelo", [
    # `Authorization: Basic` e usuario:senha em base64 — nao e cifra, e codificacao. So `Bearer` era coberto.
    "Authorization: Basic VALOR",
    "authorization: token VALOR",
    # `credential` existia so na lista de NOMES DE CAMPO; no texto solto saia em claro (as duas listas divergiam).
    "credential=VALOR",
    "credencial: VALOR",
    # Chaves de nuvem: o formato que o E8 (S3) acrescenta. `secret` sozinho nao casa dentro de `AWS_SECRET_...`
    # porque nao ha fronteira de palavra entre `T` e `_`.
    "AWS_SECRET_ACCESS_KEY=VALOR",
    "aws_access_key_id=VALOR",
    "secret_key: VALOR",
    # Digitacao pela linha de comando do adb. O caminho certo agora e o stdin (devices/adb.py), mas o filtro e a
    # defesa secundaria: mensagem de erro do subprocesso, comando copiado para um chamado.
    "adb -s emulator-5554 shell input text 'VALOR'",
    "input text VALOR",
])
def test_formatos_de_credencial_que_saiam_em_claro(modelo: str) -> None:
    linha = modelo.replace("VALOR", SECRET)
    redigido = redact(linha) or ""
    assert SECRET not in redigido, redigido
    assert MASK in redigido, redigido


def test_a_lista_de_palavras_e_uma_so() -> None:
    """As duas listas divergiam em silencio, e a divergencia era invisivel: um campo chamado `credential` era
    mascarado e o texto `credential=` nao. Agora ambas saem da MESMA constante — e um `pin` que o editor gravara
    com BACKSPACE literal (0x08) no lugar da fronteira de palavra saiu junto, sem nunca ter casado com nada."""
    from app.security import redaction

    assert redaction._SENSITIVE_KEY.pattern == redaction._PALAVRAS_DE_SEGREDO
    assert "\x08" not in redaction._PALAVRAS_DE_SEGREDO
    assert redact_obj({"pin": SECRET}) == {"pin": MASK}


def test_o_agente_do_worker_tambem_instala_o_filtro() -> None:
    """O agente roda em OUTRA maquina, com `logging.basicConfig` proprio: o filtro do backend nao o alcanca.

    E ele e instalado no HANDLER, nao no logger raiz: filtro de logger so vale para o que e emitido NAQUELE
    logger, e o agente emite tudo em `poc.worker.*`, que apenas PROPAGA ate a raiz. Um `addFilter` na raiz nao
    redigiria uma linha sequer — a armadilha exata que este teste tranca.
    """
    import logging as _logging

    from app.worker.__main__ import instalar_redacao_de_log

    raiz = _logging.getLogger("teste-agente-redacao")
    handler = _logging.StreamHandler()
    raiz.handlers = [handler]
    instalar_redacao_de_log(raiz)
    assert any(isinstance(f, RedactingFilter) for f in handler.filters)

    record = _logging.LogRecord("poc.worker.x", _logging.INFO, __file__, 1, "credential=%s", (SECRET,), None)
    for f in handler.filters:
        f.filter(record)            # type: ignore[union-attr]
    assert SECRET not in record.getMessage()


# ---------------------------------------------------------------- achado #127: tela sensível além do campo de senha
def _tela(*nodes: str) -> Any:
    return "<hierarchy>" + "".join(nodes) + "</hierarchy>"


def _no(cls: str = "android.widget.TextView", *, text: str = "", rid: str = "", pkg: str = "com.exemplo.app") -> str:
    return (f'<node class="{cls}" package="{pkg}" resource-id="{rid}" text={quoteattr(text)} '
            'bounds="[0,0][100,100]"/>')


def test_tela_de_desafio_sem_campo_de_senha_tambem_e_sensivel() -> None:
    """A tela de 2FA não tem campo `password=true` nenhum — ela pede um código. Pelo critério antigo ela virava
    JPEG em `data/evidence` e imagem no corpo da requisição ao provedor de IA."""
    tela = parse_hierarchy(_tela(
        _no(text="Insira o código de segurança que enviamos"),
        _no("android.widget.EditText", rid="com.exemplo.app:id/code"),
    ))
    assert tela.sensitive is True
    assert tela.sensitive_reason and "desafio" in tela.sensitive_reason


def test_o_codigo_visivel_na_tela_de_desafio_e_mascarado() -> None:
    """O texto dos elementos continua indo ao modelo mesmo quando a imagem é omitida (prompts.py). Numa tela de
    desafio, o que está digitado no campo É o código."""
    tela = parse_hierarchy(_tela(
        _no(text="Digite o código de verificação"),
        _no("android.widget.EditText", text="418223", rid="com.exemplo.app:id/code"),
        _no(text="R$ 42,00"),                                   # não é só dígito: passa inteiro
    ))
    textos = tela.texts()
    assert "418223" not in textos and "••••" in textos
    assert "R$ 42,00" in textos


def test_menu_de_configuracoes_que_so_CITA_dois_fatores_nao_vira_sensivel() -> None:
    """O outro lado do mesmo cuidado, e o que quase transformou isto num defeito: "Autenticação de dois fatores"
    é uma LINHA DE MENU nas configurações do Instagram. Marcar aquela tela como sensível faria o executor parar a
    etapa pedindo intervenção humana no meio de uma navegação comum. Por isso o critério exige onde digitar."""
    tela = parse_hierarchy(_tela(
        _no(text="Autenticação de dois fatores", rid="com.exemplo.app:id/row_2fa"),
        _no(text="Central de contas"),
    ))
    assert tela.sensitive is False and tela.sensitive_reason is None


@pytest.mark.parametrize("frase", [
    # en / two_factor e challenge dos sinais do telas.yaml do Instagram (app/conhecimento/apps/)
    "Two-factor authentication", "Enter the security code", "Enter the confirmation code",
    "Enter the 6-digit code", "We detected an unusual login attempt", "Suspicious login attempt",
    "Confirm it's you", "Help us confirm it's you", "Verify your account",
    "Enter the code we sent to your email", "I'm not a robot", "Confirm you're human",
    # pt
    "Autenticação de dois fatores", "Código de segurança", "Código de confirmação",
    "Insira o código de 6 dígitos", "Detectamos uma tentativa de login incomum",
    "Atividade suspeita na sua conta", "Confirme que é você", "Ajude a confirmar sua identidade",
    "Verifique sua conta", "Não sou um robô", "Confirme que você é uma pessoa",
])
def test_concorda_com_o_classificador_de_desafio_do_instagram(frase: str) -> None:
    """Dois classificadores que discordam sobre a MESMA tela é pior do que ter um só.

    o conhecimento de telas do Instagram (`telas.yaml`, antes `navigation.SIGNALS`) já sabia reconhecer desafio e 2FA — mas só era consultado DEPOIS
    de a imagem ter sido capturada e enviada ao provedor. Este teste amarra as duas listas: cada frase que faz o
    Instagram dizer CHALLENGE/TWO_FACTOR tem de fazer a captura ser omitida antes.
    """
    tela = parse_hierarchy(_tela(_no(text=frase), _no("android.widget.EditText", rid="app:id/code")))
    assert tela.sensitive is True, frase
    assert tela.sensitive_reason and "desafio" in tela.sensitive_reason


@pytest.mark.parametrize("frase", ["Help us protect your account", "Ajude-nos a proteger sua conta"])
def test_pagina_da_microsoft_que_segura_a_conta_e_sensivel_mesmo_sem_campo(frase: str) -> None:
    """Item 23.8: a página da Microsoft que segura a conta até a pessoa comprovar um contato é conta travada em
    qualquer app (a captura é omitida antes de sair), como "Confirm you're human" — e sem exigir campo de texto."""
    tela = parse_hierarchy(_tela(_no(text=frase), _no("android.widget.Button", text="Next")))
    assert tela.sensitive is True, frase
    assert tela.conta_travada is not None and tela.conta_travada.subtipo == "conta_travada"


def test_toda_tela_da_vm_loja_e_sensivel() -> None:
    """Na loja, a conta Google do parque está em toda tela — não há critério de conteúdo que valha a discussão."""
    tela = parse_hierarchy(_tela(_no(text="Play Store")), sempre_sensivel=MOTIVO_LOJA)
    assert tela.sensitive is True and tela.sensitive_reason == MOTIVO_LOJA
    # Inclusive quando a hierarquia nem veio: XML quebrado não pode ser a brecha por onde a imagem sai.
    assert parse_hierarchy("<<<não é xml", sempre_sensivel=MOTIVO_LOJA).sensitive is True


def test_app_declara_a_propria_tela_sensivel() -> None:
    """O catálogo de apps traz aplicativos que ninguém analisou: o critério genérico não sabe que a tela de dados
    da conta DAQUELE app tem documento. Quem cadastrou o app sabe, e diz em `config.yaml`."""
    regra = RegraDeTelaSensivel(package="com.exemplo.app", resource_ids=(":id/cpf",), why="dados do titular")
    tela = parse_hierarchy(_tela(_no(rid="com.exemplo.app:id/cpf", text="123.456.789-00")), regras=(regra,))
    assert tela.sensitive is True and tela.sensitive_reason == "dados do titular"

    # A mesma regra NÃO vale para outro app: declaração é por app, não por id solto.
    outro = parse_hierarchy(_tela(_no(rid="com.outro:id/cpf", pkg="com.outro")), regras=(regra,))
    assert outro.sensitive is False


def test_tela_declarada_omite_a_imagem_mas_NAO_para_a_etapa() -> None:
    """A distinção que o critério novo obriga a fazer, e a regressão que ela evita.

    Enquanto "sensível" era só campo de senha, omitir a imagem e parar a etapa pedindo uma pessoa eram a mesma
    coisa. Agora não: se uma tela declarada em `sensitive_screens` (ou qualquer tela da VM-loja) parasse a etapa,
    o executor inventaria uma falha de autenticação e marcaria o perfil como `auth_required` TODA VEZ que a IA
    passasse por ali — e o autenticador automático dispararia atrás de um login que nunca foi pedido.
    """
    from app.taskqueue.executor import pede_intervencao_humana

    regra = RegraDeTelaSensivel(package="com.exemplo.app", resource_ids=(":id/cpf",), why="dados do titular")
    declarada = parse_hierarchy(_tela(_no(rid="com.exemplo.app:id/cpf", text="123.456.789-00")), regras=(regra,))
    loja = parse_hierarchy(_tela(_no(text="Play Store")), sempre_sensivel=MOTIVO_LOJA)
    desafio = parse_hierarchy(_tela(_no(text="Código de segurança"), _no("android.widget.EditText", rid="a:id/c")))
    senha = parse_hierarchy(_tela(
        _no("android.widget.EditText", rid="a:id/p").replace("bounds=", 'password="true" bounds=')))

    assert declarada.sensitive and loja.sensitive                  # a imagem não sai em nenhuma das quatro
    assert pede_intervencao_humana(declarada) is False             # ...mas só duas param a etapa
    assert pede_intervencao_humana(loja) is False
    assert pede_intervencao_humana(desafio) is True
    assert pede_intervencao_humana(senha) is True
    assert pede_intervencao_humana(parse_hierarchy(_tela(_no(text="Tela comum")))) is False


def test_regra_por_texto_vale_sem_acento_e_sem_caixa() -> None:
    regra = RegraDeTelaSensivel(texts=("dados bancarios",))
    tela = parse_hierarchy(_tela(_no(text="Dados Bancários do titular")), regras=(regra,))
    assert tela.sensitive is True


def test_campo_de_senha_continua_sendo_criterio_e_ganha_motivo() -> None:
    tela = parse_hierarchy(_tela(
        _no("android.widget.EditText", text="segredo", rid="com.exemplo.app:id/pass") .replace(
            'bounds=', 'password="true" bounds=')))
    assert tela.sensitive is True and tela.sensitive_reason == "campo de senha"
    assert "segredo" not in tela.texts()


# ---------------------------------------------------------------- achado #129: digitação manual fora do argv
class _FerramentasFalsas:
    adb = "C:/fake/adb.exe"

    @staticmethod
    def env() -> dict[str, str]:
        return {}


def test_digitacao_sem_appium_vai_pelo_stdin_e_nao_pela_linha_de_comando(monkeypatch: Any) -> None:
    """Sem sessão de automação — caso comum nos aparelhos remotos e depois de uma falha do Appium — a digitação
    manual do painel caía em `adb shell input text '<senha>'`. Enquanto o comando rodava, o texto ficava no `argv`
    do `adb.exe` DESTA máquina: qualquer processo local que leia a lista de processos o via.

    O teste é sobre ONDE o texto passa, então ele olha exatamente isso: a lista de argumentos e o stdin.
    """
    from app.devices import adb as adb_mod

    chamadas: list[dict[str, Any]] = []

    def falso_run(cmd: list[str], **kwargs: Any) -> Any:
        chamadas.append({"cmd": cmd, "kwargs": kwargs})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(adb_mod.subprocess, "run", falso_run)
    adb_mod.Adb(_FerramentasFalsas(), "emulator-5554").input_text_ascii(SECRET)

    assert len(chamadas) == 1
    cmd, kwargs = chamadas[0]["cmd"], chamadas[0]["kwargs"]
    assert cmd == ["C:/fake/adb.exe", "-s", "emulator-5554", "shell", "-T"]   # nenhum texto nos argumentos
    assert SECRET not in " ".join(cmd)
    assert "input text" not in " ".join(cmd)
    assert SECRET in kwargs["input"] and kwargs["input"].startswith("input text '")


def test_texto_nao_ascii_continua_recusado_antes_de_qualquer_processo(monkeypatch: Any) -> None:
    """A recusa vem ANTES de montar comando nenhum: `input text` do aparelho não entende o que não é ASCII, e
    tentar assim mesmo produziria digitação errada num campo de senha — o pior lugar para um erro silencioso."""
    from app.devices import adb as adb_mod

    monkeypatch.setattr(adb_mod.subprocess, "run",
                        lambda *a, **k: pytest.fail("não devia chegar a rodar o adb"))
    with pytest.raises(adb_mod.AdbError):
        adb_mod.Adb(_FerramentasFalsas(), "emulator-5554").input_text_ascii("çãé")


# ---------------------------------------------------------------- integração: nada chega ao banco nem ao painel
async def test_evento_com_segredo_nao_e_persistido_em_claro(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    state.bus.emit("log", "requisição: " + leak_line(), data={"payload": {"password": SECRET}})
    dump = "".join(str(dict(r)) for r in state.db.query("SELECT * FROM events"))
    assert SECRET not in dump


async def test_painel_informa_quando_o_mascaramento_nao_esta_comprovado(harness: Harness) -> None:
    state = harness.state
    assert state is not None
    state.appium.is_up = lambda timeout=1.0: True            # type: ignore[assignment]
    state.appium.log_masking_active = False
    codes = [p.code for p in state.health().problems]
    assert "appium_log_masking_off" in codes
    state.appium.log_masking_active = True
    assert "appium_log_masking_off" not in [p.code for p in state.health().problems]
