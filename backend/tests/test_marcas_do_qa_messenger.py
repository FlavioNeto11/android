"""31.250: as marcas de entrega do QA Messenger, como dado fora do catálogo, dispensam o PRIMEIRO julgamento.

O 31.250 de origem (a discordância do rejulgamento virar lição do juiz barato) foi recusado: o ADR-024 deixa o
verificador fora das lições. O que entrou no lugar fecha o erro medido com prova local. Medido em 07/10 (só leitura):
as 12 discordâncias do rejulgamento por nível da janela eram todas do QA Messenger, em etapas com nível `sent`; o barato
dizia que o nível não estava provado, o forte dizia que sim. O app é nosso e mostra o estado debaixo da bolha
(`message_status`: "Enviando…", "Enviada ✓", "Entregue ✓✓", "Lida ✓✓"). Com a marca declarada em
`conhecimento/apps/com.pocqa.messenger/entrega.yaml`, a árvore prova o nível e o rejulgamento confere (travas do 31.57).

O QA Messenger continua SEM catálogo: a porta do 13.2 segue não recusando a etapa livre com efeito, e a lista de apps
ofertada ao planejador é a mesma. A etapa livre não tem `content` nos argumentos; o texto é o do último `type_text` dela.

Nível de prova: `simulated` (árvores montadas com os ids do `row_message.xml` e juízes de mentira). `real`: `not_run`
até a 1ª execução do QA Messenger com nível `sent` depois do deploy em que
`verificacao.primeiro_juiz_dispensado{prova=marcador:sent}` contar.
"""
from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.automation.hierarchy import UiTree, parse_hierarchy
from app.devices.apps_de_fundo import apps_declarados
from app.metricas import metricas
from app.models import DeliveryLevel, Postcondition, StepDTO, StepStatus
from app.planning.capabilities import capability_of, efeito_fora_do_catalogo, load_catalog, marcas_de_entrega
from app.planning.entrega_declarada import EntregaInvalida, carregar_entrega, entrega_do_pacote
from app.planning.provider import AppContext
from app.taskqueue.executor import StepExecutor
from app.taskqueue.proofs import nivel_pelo_marcador
from app.taskqueue.service import RunService

from .test_dm_verificador import _executor
from .test_marcador_de_entrega import _Juizes
from .test_sent_text_dispensa_juiz import _com_modelos_diferentes

QA = "com.pocqa.messenger"
IID = "android-01"
TEXTO = "mensagem inventada de teste"
_TV, _ET, _BT = "android.widget.TextView", "android.widget.EditText", "android.widget.Button"


def _conversa(status: str | None, *, no_campo: str = "") -> UiTree:
    """A conversa do QA Messenger como o `row_message.xml` a desenha: a bolha (`message_text`) e, na linha logo abaixo,
    o estado (`message_status`) ao lado da hora (`message_time`); embaixo, o campo e o botão de enviar."""
    nos = [(_TV, "QA-001", "chat_title", "[0,0][720,80]"),
           (_TV, TEXTO, "message_text", "[200,200][700,260]")]
    if status is not None:
        nos += [(_TV, status, "message_status", "[400,270][560,300]"), (_TV, "12:00", "message_time", "[580,270][700,300]")]
    nos += [(_ET, no_campo, "message_input", "[0,1100][560,1180]"), (_BT, "Enviar", "send_button", "[580,1100][720,1180]")]
    corpo = "".join(f'<node class="{c}" text="{t}" resource-id="{QA}:id/{r}" bounds="{b}"/>' for c, t, r, b in nos)
    return parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")


def _nivel(tela: UiTree, antes: int | None = 0) -> str | None:
    entrega = entrega_do_pacote(QA)
    assert entrega is not None
    return nivel_pelo_marcador(marcas_de_entrega(entrega.delivery_marks), TEXTO, tela, antes=antes,
                               pendentes=entrega.pending_marks, falhas=entrega.failure_marks)


# ==================================================================== o dado e a prova pela árvore
def test_o_arquivo_do_app_declara_os_tres_niveis_com_os_textos_do_app() -> None:
    entrega = entrega_do_pacote(QA)
    assert entrega is not None and entrega.app == QA
    assert {n for n, _t in marcas_de_entrega(entrega.delivery_marks)} == {"sent", "delivered", "read"}
    assert entrega.pending_marks and entrega.failure_marks
    assert entrega_do_pacote("com.instagram.android") is None          # quem tem catálogo declara na ação
    assert entrega_do_pacote("com.microsoft.office.outlook") is None
    assert entrega_do_pacote("nao e pacote") is None


def test_a_marca_debaixo_da_bolha_desta_execucao_afirma_o_nivel() -> None:
    assert _nivel(_conversa("Enviada ✓")) == "sent"
    assert _nivel(_conversa("Entregue ✓✓")) == "delivered"
    assert _nivel(_conversa("Lida ✓✓")) == "read"


def test_pendente_falha_sem_marca_ou_sem_linha_de_base_nao_afirmam_nada() -> None:
    assert _nivel(_conversa("Enviando…")) is None
    assert _nivel(_conversa("Falha no envio ✕")) is None
    assert _nivel(_conversa(None)) is None
    assert _nivel(_conversa("Entregue ✓✓"), antes=None) is None         # 31.59: sem a tela do toque, o juiz
    assert _nivel(_conversa("Entregue ✓✓"), antes=1) is None            # a bolha já estava lá antes do envio
    assert _nivel(_conversa("Entregue ✓✓", no_campo=TEXTO)) is None     # o texto ainda no campo: não saiu


def test_arquivo_invalido_e_recusado_na_carga(tmp_path: Path) -> None:
    def escreve(texto: str) -> Path:
        caminho = tmp_path / "entrega.yaml"
        caminho.write_text(texto, encoding="utf-8")
        return caminho

    for ruim in ("app: x\ndelivery_marks: [seen=Visto]\n", "app: x\ndelivery_marks: []\n",
                 "app: x\ndelivery_marks: [sent=Ok]\noutra: 1\n", "- lista\n", "app: x\ndelivery_marks: sent=Ok\n"):
        with pytest.raises(EntregaInvalida):
            carregar_entrega(escreve(ruim))
    pasta = tmp_path / "com.exemplo.outro"
    pasta.mkdir()
    (pasta / "entrega.yaml").write_text("app: com.exemplo.diferente\ndelivery_marks: [sent=Ok]\n", encoding="utf-8")
    with pytest.raises(EntregaInvalida):
        entrega_do_pacote("com.exemplo.outro", tmp_path)


# ==================================================================== o planejamento não muda
def test_o_qa_messenger_continua_sem_catalogo_e_a_oferta_ao_planejador_e_a_mesma() -> None:
    """O pedido da orquestradora: a lista de capacidades ofertadas ao planejador fica idêntica. Sem catálogo, a porta do
    13.2 não recusa a etapa livre com efeito, e `_catalogos` devolve o mesmo que antes (plano livre, todos os apps)."""
    assert load_catalog(QA) is None
    assert capability_of(QA, "SEND_MESSAGE") is None
    assert efeito_fora_do_catalogo(True, None, QA) is None
    assert QA not in apps_declarados()                                  # a pasta sem `app.yaml` não é app declarado
    qa = AppContext(id="qa-messenger", name="QA Messenger", package=QA, activity=None, nav_hints=None,
                    known_selectors=None)
    assert RunService._catalogos("mande oi para QA-001", [qa], [{"app_id": "qa-messenger"}]) == (None, {}, [qa], QA)


# ==================================================================== no executor
def _livre(nivel: DeliveryLevel = DeliveryLevel.sent, *, capability: str | None = None) -> StepDTO:
    """A etapa do QA Messenger como o plano livre a monta: sem capability e sem `content` nos argumentos."""
    return StepDTO(id=f"r-qa:{IID}:v1:send", run_id="r-qa", objective_id=f"r-qa:{IID}", instance_id=IID,
                   plan_version=1, seq=1, key="send", title="Enviar a mensagem", goal="enviar a mensagem",
                   depends_on=[], side_effect=True, commit_guard=[],
                   postcondition=Postcondition(kind="model_judged", value="a mensagem aparece como balão",
                                               description="a mensagem saiu do campo", required_delivery_level=nivel),
                   timeout_s=60, max_attempts=1, attempts=1, status=StepStatus.verifying, capability=capability,
                   bindings={})


def _pronto(tmp_path: Path, juizes: _Juizes, tela: UiTree, *, digitado: str | None = TEXTO) -> StepExecutor:
    ex = _executor(tmp_path, [tela], juizes, frente=QA)  # type: ignore[arg-type]
    _com_modelos_diferentes(ex)
    etapa = _livre()
    ex._mensagens_antes = {etapa.id: 0}  # noqa: SLF001
    ex._textos_digitados = {etapa.id: digitado} if digitado else {}  # noqa: SLF001
    return ex


async def _verificar(ex: StepExecutor, etapa: StepDTO) -> tuple[bool, str]:
    ok, texto, _nivel, _obs, _nao = await ex._verify(  # noqa: SLF001
        SimpleNamespace(id=IID), etapa, lambda: SimpleNamespace(step_key=etapa.key, instance_id=IID),  # type: ignore[arg-type]
        "r-qa", f"r-qa:{IID}", time.monotonic() + 1.0, 5.0, patient=True, facts=[], failure_marks=(),
        local_proof=None, capability=None, pacote=QA)
    return ok, texto


async def test_marca_casada_na_etapa_livre_dispensa_o_barato_e_o_rejulgamento_decide(tmp_path: Path) -> None:
    metricas.limpar()
    juizes = _Juizes(DeliveryLevel.sent)
    ok, texto = await _verificar(_pronto(tmp_path, juizes, _conversa("Enviada ✓")), _livre())
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (0, 1)
    assert metricas.valor("verificacao.primeiro_juiz_dispensado", prova="marcador:sent") == 1


async def test_enviada_nao_atende_entregue_e_vai_ao_barato(tmp_path: Path) -> None:
    juizes = _Juizes(DeliveryLevel.delivered)
    await _verificar(_pronto(tmp_path, juizes, _conversa("Enviada ✓")), _livre(DeliveryLevel.delivered))
    assert juizes.barato >= 1


async def test_entregue_atende_entregue(tmp_path: Path) -> None:
    juizes = _Juizes(DeliveryLevel.delivered)
    ok, texto = await _verificar(_pronto(tmp_path, juizes, _conversa("Entregue ✓✓")), _livre(DeliveryLevel.delivered))
    assert ok, texto
    assert (juizes.barato, juizes.escalonamento) == (0, 1)


async def test_sem_texto_digitado_ou_com_enviando_vai_ao_barato(tmp_path: Path) -> None:
    juizes = _Juizes(DeliveryLevel.sent)
    await _verificar(_pronto(tmp_path, juizes, _conversa("Enviada ✓"), digitado=None), _livre())
    assert juizes.barato >= 1
    juizes = _Juizes(DeliveryLevel.sent)
    await _verificar(_pronto(tmp_path, juizes, _conversa("Enviando…")), _livre())
    assert juizes.barato >= 1                     # a marca não afirma; o juiz decide, como antes (a pendente do
                                                  # catálogo, ADR-055, não vale aqui: o escopo não muda a espera)


async def test_chave_desligada_volta_ao_de_antes(tmp_path: Path) -> None:
    juizes = _Juizes(DeliveryLevel.sent)
    ex = _pronto(tmp_path, juizes, _conversa("Enviada ✓"))
    ex.cfg.file.ai.marcador_de_entrega_dispensa_primeiro_juiz = False
    ok, texto = await _verificar(ex, _livre())
    assert ok, texto
    assert juizes.barato == 1


def test_o_texto_digitado_so_vale_na_etapa_livre(tmp_path: Path) -> None:
    """A linha de base do 31.59 na etapa livre usa o texto digitado; na etapa com capability, só o `content`."""
    ex = _executor(tmp_path, [_conversa(None)], _Juizes(DeliveryLevel.sent), frente=QA)  # type: ignore[arg-type]
    ex._mensagens_antes = {}  # noqa: SLF001
    livre, com_acao = _livre(), _livre(capability="SEND_MESSAGE").model_copy(update={"id": "outra"})
    ex._textos_digitados = {livre.id: TEXTO, com_acao.id: TEXTO}  # noqa: SLF001
    ex._guardar_linha_de_base(livre, _conversa("Lida ✓✓"))  # noqa: SLF001
    ex._guardar_linha_de_base(com_acao, _conversa("Lida ✓✓"))  # noqa: SLF001
    assert ex._linha_de_base() == {livre.id: 1}  # noqa: SLF001
