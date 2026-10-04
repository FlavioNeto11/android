"""Item 31.57: o marcador de entrega declarado no catálogo (`delivery_marks`) dispensa o PRIMEIRO julgamento.

O 31.26 (opção A) dispensou o julgamento barato só no nível `sent`, pela prova local `sent_text`. Para `delivered` e
`read` a árvore só fala se o app mostra um marcador ("Delivered", "Seen") debaixo da mensagem; agora o catálogo pode
declarar esse marcador, e com ele casado logo abaixo da bolha DESTA execução o julgamento barato é dispensado. O
rejulgamento do 17.10 continua e decide; sem ele, com a chave desligada, sem marcador declarado ou com o marcador fora
do lugar, tudo segue como antes.

31.59 (revisão do 31.57, vale também para o `sent_text` do 31.26): a bolha é reconhecida por texto IGUAL (não "contém")
e só conta se a tela tiver MAIS bolhas iguais do que a de antes do envio (a linha de base que o executor guarda no
toque do efeito). Sem linha de base, a árvore não afirma nada.

Nível de prova: `simulated` (árvores montadas com os ids das telas reais do Instagram e juízes de mentira). Nenhum app
declara `delivery_marks` ainda: se a árvore real do Instagram expõe "Seen"/"Delivered" debaixo da bolha NÃO foi medido
(UNKNOWN). A declaração no catálogo espera uma árvore real capturada, só leitura, numa janela de prova. Prova `real`:
`not_run`.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.models import DeliveryLevel
from app.planning.capabilities import CatalogoInvalido, _acao, capability_of, marcas_de_entrega
from app.planning.provider import Usage, Verdict, VerifyRequest
from app.taskqueue import executor as modulo_executor
from app.taskqueue.proofs import nivel_pelo_marcador

from .test_dm_verificador import CONTEUDO, PKG, _executor, _verificar
from .test_sent_text_dispensa_juiz import _com_modelos_diferentes, _envio_com_nivel

_TV, _ET = "android.widget.TextView", "android.widget.EditText"
MARCAS = ("delivered=Delivered", "read=Seen", "read=Visto")


def _tela(*linhas: tuple[str, str, str]) -> UiTree:
    """Linhas de cima para baixo: (classe, texto, id). Cada uma ocupa uma faixa de 80 px, como em `_conversa`."""
    corpo = "".join(f'<node class="{c}" text="{t}" resource-id="{PKG}:id/{r}" bounds="[0,{i * 80}][700,{i * 80 + 60}]"/>'
                    for i, (c, t, r) in enumerate(linhas))
    return parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")


CABECALHO = (_TV, "ana", "header_title")
BOLHA = (_TV, CONTEUDO, "direct_text_message_text_view")
CAMPO_VAZIO = (_ET, "Message…", "row_thread_composer_edittext")


def _status(texto: str) -> tuple[str, str, str]:
    return (_TV, texto, "direct_message_status_text")


def _nivel(tela: UiTree, antes: int = 0, **kw: Any) -> str | None:
    return nivel_pelo_marcador(marcas_de_entrega(MARCAS), CONTEUDO, tela, antes=antes, **kw)


# ==================================================================== a prova pela árvore
def test_marcador_logo_abaixo_da_bolha_desta_execucao_afirma_o_nivel() -> None:
    assert _nivel(_tela(CABECALHO, BOLHA, _status("Seen"), CAMPO_VAZIO)) == "read"
    assert _nivel(_tela(CABECALHO, BOLHA, _status("Delivered"), CAMPO_VAZIO)) == "delivered"
    assert _nivel(_tela(CABECALHO, BOLHA, _status("visto"), CAMPO_VAZIO)) == "read"      # normalizado


def test_seen_de_uma_mensagem_antiga_acima_da_nossa_nao_conta() -> None:
    antiga = (_TV, "mensagem de ontem", "direct_text_message_text_view")
    assert _nivel(_tela(CABECALHO, antiga, _status("Seen"), BOLHA, CAMPO_VAZIO)) is None


def test_outra_mensagem_entre_a_nossa_e_o_marcador_nao_conta() -> None:
    resposta = (_TV, "oi! tudo sim", "direct_text_message_text_view")
    assert _nivel(_tela(CABECALHO, BOLHA, resposta, _status("Seen"), CAMPO_VAZIO)) is None


def test_mesma_mensagem_repetida_vale_a_bolha_mais_baixa() -> None:
    """O mesmo texto mandado antes (outra execução) e agora: o marcador tem de estar debaixo da MAIS BAIXA."""
    assert _nivel(_tela(CABECALHO, BOLHA, _status("Seen"), BOLHA, CAMPO_VAZIO), antes=1) is None
    assert _nivel(_tela(CABECALHO, BOLHA, BOLHA, _status("Seen"), CAMPO_VAZIO), antes=1) == "read"


def test_marcador_dentro_de_uma_frase_nao_conta() -> None:
    assert _nivel(_tela(CABECALHO, BOLHA, (_TV, "Seen by 2 people yesterday", "x"), CAMPO_VAZIO)) is None


def test_pendente_falha_ou_texto_no_campo_nao_afirmam_nada() -> None:
    tela = _tela(CABECALHO, BOLHA, _status("Seen"), (_TV, "Sending…", "y"), CAMPO_VAZIO)
    assert _nivel(tela, pendentes=("Sending…",)) is None
    tela = _tela(CABECALHO, BOLHA, _status("Seen"), (_TV, "Not delivered", "y"), CAMPO_VAZIO)
    assert _nivel(tela, falhas=("Not delivered",)) is None
    assert _nivel(_tela(CABECALHO, BOLHA, _status("Seen"), (_ET, CONTEUDO, "row_thread_composer_edittext"))) is None
    assert nivel_pelo_marcador([], CONTEUDO, _tela(CABECALHO, BOLHA, _status("Seen"), CAMPO_VAZIO), antes=0) is None
    assert nivel_pelo_marcador(marcas_de_entrega(MARCAS), None, _tela(CABECALHO, BOLHA, _status("Seen")),
                               antes=0) is None


# ==================================================================== 31.59: a bolha antiga não é a nossa
def test_bolha_antiga_com_o_mesmo_texto_e_seen_nao_prova_sem_aumentar_a_contagem() -> None:
    """A1 da revisão: a última mensagem nossa tem o MESMO texto, com "Seen" embaixo; o envio de agora não virou bolha,
    mas o campo limpou. Pela árvore sozinha, a tela é igual a um envio lido. A linha de base (1 bolha igual antes) a
    separa: sem bolha nova, nem `sent_text` nem o marcador afirmam."""
    tela = _tela(CABECALHO, BOLHA, _status("Seen"), CAMPO_VAZIO)
    assert tela.sent_as_message(CONTEUDO, antes=1) is False
    assert _nivel(tela, antes=1) is None
    assert tela.sent_as_message(CONTEUDO) is None and _nivel(tela, antes=None) is None   # sem linha de base: nada


def test_bolha_que_contem_o_texto_nao_e_a_mensagem() -> None:
    """A1, segunda forma: a bolha antiga "oi, tudo bem?" CONTÉM a mensagem "oi". Igualdade, não "contém"."""
    antiga = (_TV, f"{CONTEUDO} e mais um pedaço", "direct_text_message_text_view")
    tela = _tela(CABECALHO, antiga, _status("Seen"), CAMPO_VAZIO)
    assert tela.mensagens_iguais(CONTEUDO) == 0
    assert tela.sent_as_message(CONTEUDO, antes=0) is False
    assert _nivel(tela) is None


def test_dois_marcadores_na_mesma_linha_fica_o_maior() -> None:
    corpo = (f'<node class="{_TV}" text="ana" resource-id="{PKG}:id/header_title" bounds="[0,0][700,60]"/>'
             f'<node class="{_TV}" text="{CONTEUDO}" resource-id="{PKG}:id/m" bounds="[0,80][700,140]"/>'
             f'<node class="{_TV}" text="Delivered" resource-id="{PKG}:id/s1" bounds="[0,160][300,220]"/>'
             f'<node class="{_TV}" text="Seen" resource-id="{PKG}:id/s2" bounds="[400,160][700,220]"/>')
    assert _nivel(parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")) == "read"


# ==================================================================== o catálogo
def _bruta(**troca: Any) -> dict[str, Any]:
    cap = capability_of(PKG, "SEND_MESSAGE")
    assert cap is not None
    bruta = {k: list(v) if isinstance(v, tuple) else v for k, v in dataclasses.asdict(cap).items()}
    return {**bruta, **troca}


def test_catalogo_aceita_o_marcador_na_forma_e_recusa_o_resto() -> None:
    assert _acao(_bruta(delivery_marks=list(MARCAS)), "t").delivery_marks == MARCAS
    for ruim in (["Seen"], ["lida=Seen"], ["read="], ["appeared=Visível"]):
        with pytest.raises(CatalogoInvalido, match="delivery_marks"):
            _acao(_bruta(delivery_marks=ruim), "t")
    with pytest.raises(CatalogoInvalido, match="só cabe em ação com efeito"):
        _acao(_bruta(delivery_marks=["read=Seen"], side_effect=False), "t")


def test_nenhum_app_declara_marcador_ainda() -> None:
    """Declarar sem ter visto a árvore real seria afirmar o que não foi medido (31.57: só depois da captura)."""
    cap = capability_of(PKG, "SEND_MESSAGE")
    assert cap is not None and cap.delivery_marks == ()


# ==================================================================== no executor
class _Juizes:
    """O barato e o de escalonamento, contados à parte; os dois respondem `yes` com o nível dado."""

    def __init__(self, nivel: DeliveryLevel) -> None:
        self.barato = 0
        self.escalonamento = 0
        self.nivel = nivel

    async def verify(self, req: VerifyRequest) -> tuple[Verdict, Usage]:
        if req.escalate:
            self.escalonamento += 1
        else:
            self.barato += 1
        return Verdict(satisfied="yes", evidence="[teste]", delivery_level=self.nivel), Usage()


@pytest.fixture
def com_marcas(monkeypatch: pytest.MonkeyPatch) -> None:
    original = modulo_executor.capability_of

    def com(app: str | None, key: str | None) -> Any:
        cap = original(app, key)
        return dataclasses.replace(cap, delivery_marks=MARCAS) if cap is not None and key == "SEND_MESSAGE" else cap

    monkeypatch.setattr(modulo_executor, "capability_of", com)


def _pronto(tmp_path: Path, juizes: _Juizes, tela: UiTree) -> Any:
    ex = _executor(tmp_path, [tela], juizes)  # type: ignore[arg-type]
    _com_modelos_diferentes(ex)
    return ex


VISTA = _tela(CABECALHO, BOLHA, _status("Seen"), CAMPO_VAZIO)


async def test_marcador_casado_dispensa_o_barato_e_o_rejulgamento_decide(tmp_path: Path, com_marcas: None) -> None:
    juizes = _Juizes(DeliveryLevel.read)
    ok, texto = await _verificar(_pronto(tmp_path, juizes, VISTA), _envio_com_nivel(DeliveryLevel.read))
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (0, 1)


async def test_o_rejulgamento_que_ve_nivel_menor_nao_deixa_fechar(tmp_path: Path, com_marcas: None) -> None:
    juizes = _Juizes(DeliveryLevel.sent)                       # o escalonamento não vê "lida"
    ok, _texto = await _verificar(_pronto(tmp_path, juizes, VISTA), _envio_com_nivel(DeliveryLevel.read))
    assert not ok
    assert juizes.escalonamento >= 1


async def test_marcador_abaixo_do_nivel_exigido_vai_ao_barato(tmp_path: Path, com_marcas: None) -> None:
    juizes = _Juizes(DeliveryLevel.read)
    tela = _tela(CABECALHO, BOLHA, _status("Delivered"), CAMPO_VAZIO)
    await _verificar(_pronto(tmp_path, juizes, tela), _envio_com_nivel(DeliveryLevel.read))
    assert juizes.barato >= 1


async def test_sem_marcador_na_tela_vai_ao_barato(tmp_path: Path, com_marcas: None) -> None:
    juizes = _Juizes(DeliveryLevel.delivered)
    await _verificar(_pronto(tmp_path, juizes, _tela(CABECALHO, BOLHA, CAMPO_VAZIO)),
                     _envio_com_nivel(DeliveryLevel.delivered))
    assert juizes.barato >= 1


async def test_sem_marcador_declarado_nada_muda(tmp_path: Path) -> None:
    juizes = _Juizes(DeliveryLevel.read)
    await _verificar(_pronto(tmp_path, juizes, VISTA), _envio_com_nivel(DeliveryLevel.read))
    assert juizes.barato >= 1


async def test_sem_rejulgamento_o_marcador_nao_fecha_sozinho(tmp_path: Path, com_marcas: None) -> None:
    juizes = _Juizes(DeliveryLevel.read)
    ex = _pronto(tmp_path, juizes, VISTA)
    ex.cfg.file.ai.rejudge_yes_on_side_effect = False
    ok, texto = await _verificar(ex, _envio_com_nivel(DeliveryLevel.read))
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (1, 0)


async def test_rejulgamento_com_o_mesmo_modelo_o_marcador_nao_fecha_sozinho(tmp_path: Path,
                                                                            com_marcas: None) -> None:
    """O 17.10 só confere com modelo DIFERENTE; com o mesmo nas duas funções não há segunda opinião, e o marcador não
    dispensa nada (o mesmo vale para o rejulgamento desligado, acima)."""
    juizes = _Juizes(DeliveryLevel.read)
    ex = _executor(tmp_path, [VISTA], juizes)  # type: ignore[arg-type]   # sem `_com_modelos_diferentes`
    assert ex.cfg.ai_role("verify").model == ex.cfg.ai_role("escalation").model
    ok, texto = await _verificar(ex, _envio_com_nivel(DeliveryLevel.read))
    assert ok, texto
    assert juizes.barato == 1


async def test_bolha_antiga_na_linha_de_base_vai_ao_barato(tmp_path: Path, com_marcas: None) -> None:
    """31.59 no executor: com a linha de base guardada no toque (1 bolha igual antes) e a mesma tela depois, o marcador
    não dispensa o julgamento barato."""
    juizes = _Juizes(DeliveryLevel.read)
    ex = _pronto(tmp_path, juizes, VISTA)
    ex._mensagens_antes = {k: 1 for k in ex._mensagens_antes}  # noqa: SLF001
    await _verificar(ex, _envio_com_nivel(DeliveryLevel.read))
    assert juizes.barato >= 1


async def test_chave_desligada_volta_ao_de_antes(tmp_path: Path, com_marcas: None) -> None:
    juizes = _Juizes(DeliveryLevel.read)
    ex = _pronto(tmp_path, juizes, VISTA)
    ex.cfg.file.ai.marcador_de_entrega_dispensa_primeiro_juiz = False
    ok, texto = await _verificar(ex, _envio_com_nivel(DeliveryLevel.read))
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (1, 1)


def test_a_linha_de_base_e_a_contagem_da_tela_no_toque(tmp_path: Path) -> None:
    """31.59: `_guardar_linha_de_base` (chamado no toque do efeito, em `_run_step`) guarda quantas bolhas IGUAIS a tela
    de antes tem; a etapa sem `content` não guarda nada. A chamada no ponto do toque é conferida pela leitura do código."""
    ex = _executor(tmp_path, [VISTA], _Juizes(DeliveryLevel.read))  # type: ignore[arg-type]
    ex._mensagens_antes = {}  # noqa: SLF001
    etapa = _envio_com_nivel(DeliveryLevel.read)
    ex._guardar_linha_de_base(etapa, _tela(CABECALHO, BOLHA, _status("Seen"), (_ET, CONTEUDO, "c")))  # noqa: SLF001
    assert ex._linha_de_base() == {etapa.id: 1}  # noqa: SLF001
    sem_texto = etapa.model_copy(update={"id": "outra", "bindings": {"username": "@ana"}})
    ex._guardar_linha_de_base(sem_texto, VISTA)  # noqa: SLF001
    assert "outra" not in ex._linha_de_base()  # noqa: SLF001


# ==================================================================== 31.59, revisão: R1 e R2
def test_r1_bolha_antiga_revelada_por_rolagem_com_mensagem_abaixo_nao_prova() -> None:
    """R1: no toque a bolha igual antiga estava fora da tela (linha de base 0); o envio falha, o campo limpa e a lista
    rola: a antiga entra, com mensagens mais novas abaixo. Ela não é a última mensagem: não prova."""
    mais_nova = (_TV, "uma resposta depois", "direct_text_message_text_view")
    tela = _tela(CABECALHO, BOLHA, _status("Seen"), mais_nova, CAMPO_VAZIO)
    assert tela.mensagens_iguais(CONTEUDO) == 1
    assert tela.sent_as_message(CONTEUDO, antes=0) is False
    assert _nivel(tela) is None


def test_r1_a_ultima_bolha_com_seen_e_hora_abaixo_prova() -> None:
    """O "Seen", a hora e a reação abaixo da última bolha não são mensagem: ela segue sendo a última."""
    tela = _tela(CABECALHO, BOLHA, _status("Seen"), (_TV, "22:41", "message_timestamp"), CAMPO_VAZIO)
    assert tela.sent_as_message(CONTEUDO, antes=0) is True
    assert _nivel(tela) == "read"


def test_r1_bolha_sem_id_nao_diz_o_que_e_mensagem_e_vai_ao_modelo() -> None:
    corpo = (f'<node class="{_TV}" text="{CONTEUDO}" resource-id="" bounds="[0,80][700,140]"/>'
             f'<node class="{_TV}" text="Seen" resource-id="{PKG}:id/s" bounds="[0,160][700,220]"/>'
             f'<node class="{_ET}" text="Message…" resource-id="{PKG}:id/c" bounds="[0,240][700,300]"/>')
    tela = parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")
    assert tela.sent_as_message(CONTEUDO, antes=0) is not True
    assert _nivel(tela) is None


def test_r2_balao_com_texto_e_descricao_iguais_conta_uma_vez() -> None:
    """R2: o balão (descrição = a mensagem) e o texto dentro dele são UMA bolha; re-renderizar não sobe a contagem."""
    corpo = (f'<node class="android.view.ViewGroup" content-desc="{CONTEUDO}" resource-id="{PKG}:id/message_content"'
             f' bounds="[0,80][700,200]">'
             f'<node class="{_TV}" text="{CONTEUDO}" resource-id="{PKG}:id/direct_text_message_text_view"'
             f' bounds="[20,90][680,190]"/></node>'
             f'<node class="{_ET}" text="Message…" resource-id="{PKG}:id/row_thread_composer_edittext"'
             f' bounds="[0,300][700,360]"/>')
    tela = parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")
    assert tela.mensagens_iguais(CONTEUDO) == 1
    assert tela.sent_as_message(CONTEUDO, antes=1) is False          # a mesma bolha de antes: nenhum envio
    assert tela.sent_as_message(CONTEUDO, antes=0) is True
