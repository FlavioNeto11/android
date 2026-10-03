"""Item 24.3 — valor lido entre etapas (ADR-058, contrato C2): `read_value`, `{{saida:<nome>}}` e a triagem (D3).

O que se prova aqui:
- a triagem por formato recusa código de verificação, senha e token e deixa passar o dado comum (@nome, e-mail,
  número sem contexto de código);
- `ler_valor` tira o valor do TEXTO do elemento — um trecho que a tela não tem é recusado;
- a referência cria a dependência na materialização, e o bloco `for_each` dá um nome por item sem perder a receita
  compartilhada;
- ponta a ponta no QA Messenger falso: a etapa lê o contato, a seguinte abre a conversa COM o valor lido (resolvido na
  linha antes da porta de política), a mensagem vai para ele, e o relatório traz o valor com a origem (etapa e app);
  `step_done` sem a leitura é recusado;
- o código na tela PARA a etapa (`waiting_user`) e a leitura não o grava: tabela de saídas, argumentos e
  justificativa da ação, eventos, evidências (a tela em si segue as regras de sempre, que não são deste item);
- referência sem etapa que a leia, ou para a frente, é defeito do plano (sem tentativa gasta); a produtora anterior
  ainda aberta faz esperar com o motivo; a recuperação depois de uma falha reaproveita o valor gravado, sem reler.

Nível de prova: `simulated` (provedor por regras, aparelho falso, banco de teste).
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.automation.hierarchy import parse_hierarchy
from app.automation.tools import TOOLS, ReadValue, ToolContext, execute_tool, validate_call
from app.automation.driver import DriverError
from app.models import PlanStep, Postcondition
from app.planning.provider import Decision, Usage
from app.taskqueue.foreach import expand
from app.taskqueue.recipes import para_hash, step_template_hash
from app.taskqueue.repository import _dependencias_das_saidas
from app.taskqueue.saidas import (LeituraInvalida, args_da_chamada_invalida, como_texto, ler_valor, referencias,
                                  resolver, sem_sufixo_de_item, triagem, variaveis_da_receita)

from . import fake_device
from .conftest import Harness

CODIGO = "482913"


def _post(kind: str, value: str, description: str = "d") -> Postcondition:
    return Postcondition(kind=kind, value=value, description=description)  # type: ignore[arg-type]


# ================================================================== triagem (D3)
@pytest.mark.parametrize(("valor", "contexto", "motivo"), [
    (CODIGO, {"do_elemento": f"Seu código de verificação é {CODIGO}"}, "código de verificação"),
    ("482 913", {"da_tela": "Use o código de segurança abaixo para entrar"}, "código de verificação"),
    ("Your verification code", {}, "código de verificação"),
    ("Confirm you're human to use your account", {}, "verificação da conta"),
    ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0", {}, "token ou segredo"),
    ("token=abc123def456", {}, "token ou segredo"),
    ("Xk9#pq2Lm", {}, "senha"),
    ("Kp7mQ2xz", {"do_elemento": "Sua senha temporária: Kp7mQ2xz"}, "senha"),
    ("qualquer coisa", {"campo_de_senha": True}, "senha"),
    # link com trecho aleatório longo: pode ser o link mágico que entra na conta — conservador de propósito
    ("https://exemplo.test/entrar/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", {}, "token ou segredo"),
])
def test_triagem_recusa_codigo_senha_e_token(valor: str, contexto: dict[str, Any], motivo: str) -> None:
    assert triagem(valor, **contexto) == motivo


@pytest.mark.parametrize(("valor", "contexto"), [
    (f"{CODIGO} is your Instagram code", {}),                         # a linha do e-mail, como valor
    (CODIGO, {"do_elemento": f"{CODIGO} is your Instagram code"}),      # o valor e a linha de origem
    (CODIGO, {"da_tela": f"Remetente {CODIGO} is your Instagram code Hoje"}),
    (f"Use {CODIGO} to confirm your identity", {}),
    (f"Seu código é {CODIGO[:3]} {CODIGO[3:]}", {}),
    (f"{CODIGO} es tu código de acceso", {}),
    ("G-482913 is your Google verification code", {}),               # os formatos que já eram pegos continuam
    ("Use o código 482913 para entrar", {}),
])
def test_triagem_acusa_email_de_codigo_pelo_valor_e_pela_linha_de_origem(valor: str, contexto: dict[str, Any]) -> None:
    # qualquer recusa serve ao ADR-009; o motivo exato varia (a forma "<número> … código" também é de `looks_secret`)
    assert triagem(valor, **contexto) in ("código de verificação", "token ou segredo")


@pytest.mark.parametrize("valor", ["Reunião às 14h do dia 12345", "Entrar no grupo 123", "Login feito 123456789 vezes",
                                   "Pedido 4821 confirmado", "Data 2026-10-02 login", "Versão 12.345 do código"])
def test_triagem_nao_acusa_texto_comum_com_numero_nem_palavra_de_codigo_sem_numero_de_codigo(valor: str) -> None:
    # limiar: número de 4 a 8 dígitos (grupos iguais com espaço ou hífen) E uma palavra de código/verificação na mesma
    # linha. Sem a palavra, com 3 ou 9+ dígitos, ou data/hora/decimal, é dado comum.
    assert triagem(valor) is None


def test_triagem_o_numero_so_conta_com_a_palavra_de_codigo_perto_na_tela_inteira() -> None:
    longe = "Entrar " + "x" * 120 + " 12345 seguidores"
    assert triagem("12345", da_tela=longe) is None                    # o menu "Entrar" não faz da contagem um código
    assert triagem("12345", da_tela="Código 12345 seguidores") == "código de verificação"


@pytest.mark.parametrize("valor", ["Your code is 482.913", "Seu código é 482.913", "Your code is 482,913",
                                   "Your code is ４８２９１３", "Seu código é ٤٨٢٩١٣", "Your code is 482\u200b913",
                                   "Your code is G-482913", "Your code is ABC123", "Use 482 913 to log in"])
def test_d1_d2_triagem_da_arvore_acusa_codigo_com_separador_unicode_e_alfanumerico(valor: str) -> None:
    assert triagem(valor) in ("código de verificação", "token ou segredo")


@pytest.mark.parametrize("valor", ["Versão 12.345 do código", "Data 12/10 login", "Login em 12/10/2026 às 14h30",
                                   "Seu código tem 6 dígitos", "Entrar no grupo 123"])
def test_d1_a_triagem_mais_restritiva_nao_pega_data_hora_nem_decimal(valor: str) -> None:
    assert triagem(valor) is None


def test_receita_nao_recebe_saida_lida_da_imagem() -> None:
    lidas = {"remetente": ("Maria", "text"), "assunto": ("Oi", "text"), "assunto_i2": ("Oi B", "text")}
    assert variaveis_da_receita(lidas, None, {"remetente"}) == {"saida_assunto": "Oi", "saida_assunto_i2": "Oi B"}
    # o nome é o gravado (a cópia do item leva o sufixo): a visual do item 2 sai, e a do item 1 já saía por ser de outro item
    assert variaveis_da_receita(lidas, "2", {"assunto_i2"}) == {"saida_remetente": "Maria", "saida_assunto": "Oi"}
    assert variaveis_da_receita(lidas, None) == {"saida_remetente": "Maria", "saida_assunto": "Oi",
                                                 "saida_assunto_i2": "Oi B"}              # sem visuais, como antes


@pytest.mark.parametrize("valor", ["@ciclano", "Joao_Silva2024", "fulano.silva@outlook.com", CODIGO, "1.234",
                                   "Reunião de sexta às 10h", "https://www.instagram.com/ciclano/",
                                   "https://www.instagram.com/reel/C1a2B3c4D5e/?igsh=MWQ1ZGUxMzBkMA=="])
def test_triagem_deixa_passar_dado_comum(valor: str) -> None:
    # O número sozinho é número: sem a tela falar de código, "482913" pode ser contagem ou pedido.
    assert triagem(valor, do_elemento=valor, da_tela="Caixa de entrada Assunto Remetente") is None


# ================================================================== leitura (fato da tela)
def _tela() -> Any:
    nos = [
        ('android.widget.TextView', 'subject', 'Veja o perfil @ciclano hoje', '[0,100][720,160]'),
        ('android.widget.TextView', 'followers', '1.234', '[0,200][300,240]'),
        ('android.widget.ListView', 'list', '', '[0,300][720,700]'),
        ('android.widget.TextView', 'row', 'Ana', '[0,300][720,360]'),
        ('android.widget.TextView', 'row', 'Bia', '[0,360][720,420]'),
        ('android.widget.TextView', 'row', 'Ana', '[0,420][720,480]'),
    ]
    corpo = "".join(f'<node class="{c}" resource-id="app:id/{r}" text="{t}" bounds="{b}"/>' for c, r, t, b in nos)
    return parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")


def test_ler_valor_tira_da_arvore_e_recusa_o_que_a_tela_nao_tem() -> None:
    tela = _tela()
    assunto = tela.find_selector("id=subject")[0].id
    valor, partes, el = ler_valor(tela, element_id=assunto, trecho=None, tipo="text")
    assert valor == "Veja o perfil @ciclano hoje" and partes == [valor] and el.id == assunto
    # o recorte vem com a caixa da TELA, não a do modelo
    assert ler_valor(tela, element_id=assunto, trecho="@CICLANO", tipo="text")[0] == "@ciclano"
    with pytest.raises(LeituraInvalida, match="não está no texto"):
        ler_valor(tela, element_id=assunto, trecho="@outro", tipo="text")        # o modelo não escreve o valor
    with pytest.raises(LeituraInvalida, match="não existe"):
        ler_valor(tela, element_id="e999", trecho=None, tipo="text")
    with pytest.raises(LeituraInvalida, match="não é um número"):
        ler_valor(tela, element_id=assunto, trecho=None, tipo="number")
    seguidores = tela.find_selector("id=followers")[0].id
    assert ler_valor(tela, element_id=seguidores, trecho=None, tipo="number")[0] == "1.234"
    lista = tela.find_selector("id=list")[0].id
    valor, partes, _ = ler_valor(tela, element_id=lista, trecho=None, tipo="list")
    assert json.loads(valor) == ["Ana", "Bia"] == partes and como_texto(valor, "list") == "Ana, Bia"


JWT = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.c2lnbmF0dXJhZGFtZW50ZXNlY3JldGE"


def test_erro_de_leitura_nao_cita_o_valor() -> None:
    """A mensagem de `LeituraInvalida` vai para `actions.error` e para o evento ANTES da triagem: um código lido com o
    tipo errado, um JWT tomado por endereço ou um trecho escrito pelo modelo não podem aparecer nela."""
    nos = [('android.widget.TextView', 'code', f'Seu codigo de verificacao e {CODIGO}', '[0,100][720,160]'),
           ('android.widget.TextView', 'jwt', JWT, '[0,200][720,240]')]
    corpo = "".join(f'<node class="{c}" resource-id="app:id/{r}" text="{t}" bounds="{b}"/>' for c, r, t, b in nos)
    tela = parse_hierarchy(f"<hierarchy>{corpo}</hierarchy>")
    codigo, jwt = tela.find_selector("id=code")[0].id, tela.find_selector("id=jwt")[0].id
    casos = [dict(element_id=codigo, trecho=None, tipo="number"), dict(element_id=jwt, trecho=None, tipo="url"),
             dict(element_id=codigo, trecho="482914", tipo="text"), dict(element_id=CODIGO, trecho=None, tipo="text")]
    for caso in casos:
        with pytest.raises(LeituraInvalida) as exc:
            ler_valor(tela, **caso)                                            # type: ignore[arg-type]
        for segredo in (CODIGO, "482914", JWT, JWT[:20]):
            assert segredo not in str(exc.value), (caso, str(exc.value))
    # os argumentos de uma chamada inválida, no que se grava: sem recorte, sem nome ou id que não tenham forma de nome
    bruto = {"rationale": f"o codigo e {CODIGO}", "name": CODIGO, "element_id": CODIGO, "value": "482914",
             "value_kind": "number"}
    gravavel = args_da_chamada_invalida(bruto, tela)
    assert gravavel == {"value_kind": "number", "name": "**OMITIDO**", "element_id": "**OMITIDO**",
                        "value": "**OMITIDO**"}
    assert args_da_chamada_invalida({"name": "contato", "element_id": codigo, "value_kind": "text"}, tela) == {
        "value_kind": "text", "name": "contato", "element_id": codigo}


async def test_read_value_e_do_executor_e_nao_e_estrito() -> None:
    assert "read_value" in TOOLS
    args = validate_call("read_value", {"rationale": "r", "name": "perfil", "element_id": "e1", "value": None,
                                        "value_kind": "text"})
    assert isinstance(args, ReadValue)

    async def call(fn: Any, *a: Any) -> Any:
        return fn(*a)

    ctx = ToolContext(io=None, call=call, tree=_tela(), width=720, height=1280, image_scale=1.0,  # type: ignore[arg-type]
                      app_package=None, app_activity=None)
    with pytest.raises(DriverError) as exc:           # caminho novo sem a conferência do executor: recusa
        await execute_tool(ctx, "read_value", args)
    assert exc.value.effect_possible is False


# ================================================================== referência, dependência e for_each
def test_referencia_resolve_e_diz_o_que_falta() -> None:
    passo = PlanStep(key="abrir", title="Abrir {{saida:perfil}}", goal="g", bindings={"username": "{{ saida:perfil }}"},
                     postcondition=_post("text_visible", "{{saida:perfil}}"))
    assert referencias(passo) == ["perfil"]
    assert resolver("Abrir {{saida:perfil}} e {{saida:outro}}", {"perfil": "@ciclano"}) == (
        "Abrir @ciclano e {{saida:outro}}", ["outro"])


def test_referencia_cria_dependencia_so_da_produtora_anterior() -> None:
    le = PlanStep(key="ler", title="t", goal="g", saidas=["perfil"], postcondition=_post("text_visible", "x"))
    usa = PlanStep(key="usar", title="Abrir {{saida:perfil}}", goal="g", postcondition=_post("text_visible", "x"))
    antes = PlanStep(key="antes", title="{{saida:perfil}}", goal="g", postcondition=_post("text_visible", "x"))
    out = {s.key: s for s in _dependencias_das_saidas([antes, le, usa])}
    assert out["usar"].depends_on == ["ler"]
    assert out["antes"].depends_on == []                   # para a frente travaria `promote`: não entra


def test_for_each_da_nome_por_item_e_as_copias_seguem_com_a_mesma_receita() -> None:
    passos = [
        PlanStep(key="coleta", title="c", goal="c", postcondition=_post("items_collected", "x")),
        PlanStep(key="ler", title="Ler {item}", goal="g", depends_on=["coleta"], for_each="coleta", saidas=["assunto"],
                 postcondition=_post("text_visible", "{item}")),
        PlanStep(key="usar", title="Usar {{saida:assunto}}", goal="g", depends_on=["ler"], for_each="coleta",
                 postcondition=_post("text_visible", "{{saida:assunto}}")),
    ]
    out = {s.key: s for s in expand(passos, {"coleta": ["A", "B"]})}
    assert out["ler_i1"].saidas == ["assunto_i1"] and out["ler_i2"].saidas == ["assunto_i2"]
    assert out["usar_i2"].postcondition.value == "{{saida:assunto_i2}}"
    hashes = {step_template_hash(para_hash(sem_sufixo_de_item(out[k]), {})) for k in ("usar_i1", "usar_i2")}
    assert hashes == {step_template_hash(passos[2])}        # uma receita para todas as cópias
    # a variável de receita do item é a do bloco: a receita do item 1 reproduz com o valor do item 2
    lidas = {"assunto_i1": ("Oi A", "text"), "assunto_i2": ("Oi B", "text"), "geral": ("x", "text")}
    assert variaveis_da_receita(lidas, "2") == {"saida_assunto": "Oi B", "saida_geral": "x"}


# ================================================================== ponta a ponta (QA Messenger falso)
LIDO = "QA-002"


def _plano_com_leitura(inner: Any, *, contato_de: str = "contato", max_tentativas_da_conversa: int | None = None,
                       dependencia_explicita: bool = False, leitura_no_fim: bool = False,
                       max_tentativas_da_leitura: int = 3) -> Any:
    """O plano simulado de "enviar mensagem", com uma etapa que LÊ o contato na lista e as seguintes usando o valor.
    `recipient` das etapas vira `{{saida:<contato_de>}}` pelas variáveis (a cadeia `{recipient}` → referência →
    valor), e a etapa que abre a conversa só depende explicitamente da leitura com `dependencia_explicita`: sem ela,
    a dependência é a que a referência cria. `leitura_no_fim` põe a leitura DEPOIS de quem a usa (referência para a
    frente)."""
    plan0 = inner.plan

    async def plan(req: Any) -> Any:
        p, u = await plan0(req)
        ref = "{{saida:" + contato_de + "}}"
        ler = PlanStep(key="read_contact", title="Ler o contato da lista", goal="Ler o nome do contato na lista.",
                       depends_on=["confirm_account"], saidas=["contato"], max_attempts=max_tentativas_da_leitura,
                       postcondition=_post("element_present", "id=conversation_list", "A lista está visível."))
        passos = []
        for s in p.steps:
            if s.key in ("open_conversation", "compose_message", "send_message"):
                extra: dict[str, Any] = {"variables": {"recipient": ref}}
                if s.key == "open_conversation" and max_tentativas_da_conversa is not None:
                    extra["max_attempts"] = max_tentativas_da_conversa
                if s.key == "open_conversation" and dependencia_explicita:
                    extra["depends_on"] = ["read_contact"]
                s = s.model_copy(update=extra)
            passos.append(s)
            if s.key == "confirm_account" and not leitura_no_fim:
                passos.append(ler)
        if leitura_no_fim:
            passos.append(ler)
        return p.model_copy(update={"steps": passos}), u

    return plan


def _decide_leitura(inner: Any, seen: dict[str, Any], *, alvo: str = LIDO, trecho: str | None = None) -> Any:
    decide0 = inner.decide

    async def decide(req: Any) -> Any:
        if req.ctx.step_key != "read_contact":
            return await decide0(req)
        seen["historico"] = list(req.history)
        seen["decisoes"] = seen.get("decisoes", 0) + 1
        if seen["decisoes"] == 1:                          # conclui sem ler: o executor recusa
            return Decision(tool="step_done", args={"rationale": "lista visível", "evidence": "lista",
                                                    "delivery_level": None}), Usage()
        el = next(e for e in req.screen.tree.elements if e.text == alvo)
        return Decision(tool="read_value", args={"rationale": "o contato a usar", "name": "contato",
                                                 "element_id": el.id, "value": trecho, "value_kind": "text"}), Usage()

    return decide


async def test_valor_lido_numa_etapa_e_usado_na_seguinte(harness: Harness) -> None:
    harness.cfg.file.ai.recipes = "replay"                  # com receitas ligadas: a leitura fica fora delas
    inner = harness.ai.inner
    seen: dict[str, Any] = {}
    inner.plan, inner.decide = _plano_com_leitura(inner), _decide_leitura(inner, seen)
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id, timeout=60)).status == "completed"
    db = harness.state.db                                                     # type: ignore[union-attr]
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))

    # a mensagem foi para o contato LIDO, não para o do comando (QA-001)
    assert [m.contact for m in harness.fakes["android-01"].messages] == [LIDO]
    assert harness.state.repo.step_outputs(obj["id"]) == {"contato": LIDO}               # type: ignore[union-attr]
    saida = db.one("SELECT * FROM step_outputs WHERE objective_id=?", (obj["id"],))
    assert saida["value_kind"] == "text" and saida["step_id"].endswith(":read_contact") and saida["app_id"]

    # a referência virou o valor NA LINHA (é o que a porta de política e o painel leem), e a dependência veio dela
    conversa = db.one("SELECT * FROM steps WHERE objective_id=? AND key='open_conversation'", (obj["id"],))
    assert LIDO in conversa["title"] and "{{saida" not in conversa["title"]
    assert json.loads(conversa["variables"]) == {"recipient": LIDO}
    assert "read_contact" in json.loads(conversa["depends_on"])
    envio = db.one("SELECT commit_guard FROM steps WHERE objective_id=? AND key='send_message'", (obj["id"],))
    assert LIDO in json.loads(envio["commit_guard"])

    # step_done sem ler foi recusado; o ator soube o nome a ler pela linha do executor
    rejeitada = db.one("SELECT * FROM actions WHERE tool='step_done' AND status='rejected' AND attempt_id LIKE ?",
                       ("%:read_contact:%",))
    assert rejeitada is not None and rejeitada["error"] == "valor da etapa ainda não lido"
    assert any(h.startswith("(executor) esta etapa entrega") and "'contato'" in h for h in seen["historico"])
    lida = db.one("SELECT * FROM actions WHERE tool='read_value' AND status='done'")
    assert json.loads(lida["result"])["name"] == "contato"

    # receitas: a leitura não vira receita (ler é decisão sobre a tela da vez) e a trilha diz que a IA conduziu; a
    # receita de quem USA o valor não guarda o valor lido — reproduzida, ela abriria o contato de outra execução
    leitura = db.one("SELECT driven_by FROM steps WHERE objective_id=? AND key='read_contact'", (obj["id"],))
    assert leitura["driven_by"] == "ai"
    assert db.scalar("SELECT COUNT(*) FROM recipes WHERE step_key='read_contact'") == 0
    receitas = db.query("SELECT actions FROM recipes WHERE step_key IN ('open_conversation', 'send_message')")
    reproduzivel = [json.dumps([{k: a.get(k) for k in ("args", "selectors")} for a in json.loads(r["actions"])])
                    for r in receitas]                      # `why` é só a justificativa, para quem lê
    assert reproduzivel and all(LIDO not in r for r in reproduzivel), reproduzivel

    # o relatório traz o valor com a origem
    rel = harness.state.runs.report(run.id)                                     # type: ignore[union-attr]
    [valor] = rel["per_instance"][0]["values_read"]
    app_nome = db.scalar("SELECT name FROM apps WHERE id=?", (saida["app_id"],))
    assert valor["name"] == "contato" and valor["value"] == LIDO and valor["value_kind"] == "text"
    assert valor["step_title"] == "Ler o contato da lista" and valor["app"] == app_nome
    assert "## Valores lidos entre etapas" in rel["markdown"] and f"contato = {LIDO}" in rel["markdown"]


async def test_codigo_na_tela_para_a_etapa_e_a_leitura_nao_o_grava(harness: Harness,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    contato = f"Código de verificação: {CODIGO}"
    monkeypatch.setattr(fake_device, "CONTACTS", [contato, *fake_device.CONTACTS])
    inner = harness.ai.inner
    seen: dict[str, Any] = {"decisoes": 1}                  # direto à leitura
    inner.plan, inner.decide = _plano_com_leitura(inner), _decide_leitura(inner, seen, alvo=contato, trecho=CODIGO)
    run = harness.run(["android-01"])
    db = harness.state.db                                                     # type: ignore[union-attr]

    def parado() -> bool:                                   # o objetivo nasce depois do planejamento
        o = db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))
        return o is not None and o["status"] in ("waiting_user", "failed", "succeeded")

    await harness.wait(parado, 60, "objetivo parado na leitura")
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["status"] == "waiting_user" and "código de verificação" in obj["blocked_reason"]
    assert "ADR-009" in obj["blocked_reason"] and obj["needs"]
    passo = db.one("SELECT * FROM steps WHERE objective_id=? AND key='read_contact'", (obj["id"],))
    assert passo["status"] == "waiting_user"
    assert db.scalar("SELECT COUNT(*) FROM step_outputs") == 0
    assert not harness.fakes["android-01"].messages
    acao = db.one("SELECT * FROM actions WHERE tool='read_value'")
    assert acao["status"] == "rejected" and json.loads(acao["args"])["value"] == "**RECUSADO**"
    assert acao["rationale"] is None
    # a leitura não deixou o valor em registro nenhum: ações, tentativas, evidências, eventos (nesta execução o ator
    # não usou outra ferramenta sobre o texto; o que a TELA mostra segue as regras de sempre)
    for tabela, colunas in (("actions", "args, result, error, rationale"), ("attempts", "error, observed_result"),
                            ("evidence", "note"), ("events", "message, data")):
        for linha in db.query(f"SELECT {colunas} FROM {tabela}"):
            assert CODIGO not in " ".join(str(v) for v in dict(linha).values()), (tabela, dict(linha))
    # e a etapa seguinte nunca começou
    conversa = db.one("SELECT * FROM steps WHERE objective_id=? AND key='open_conversation'", (obj["id"],))
    assert conversa["attempts"] == 0 and "{{saida:contato}}" in conversa["title"]


async def test_chamada_de_leitura_invalida_nao_grava_o_texto_da_tela_nem_o_do_modelo(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    """Erro de CHAMADA (tipo errado, trecho que a tela não tem, nome ou id escritos à mão) não passa pela triagem: o
    texto do elemento e o que o modelo escreveu não podem ir para `actions` nem para o evento `action.logged`."""
    contato = f"Seu codigo de verificacao e {CODIGO}"
    monkeypatch.setattr(fake_device, "CONTACTS", [contato, *fake_device.CONTACTS])
    inner = harness.ai.inner
    chamadas = 0

    async def decide(req: Any) -> Any:
        nonlocal chamadas
        if req.ctx.step_key != "read_contact":
            return await decide0(req)
        el = next(e for e in req.screen.tree.elements if e.text == contato)
        variantes = [
            {"name": "contato", "element_id": el.id, "value": None, "value_kind": "number"},     # tipo errado
            {"name": "contato", "element_id": el.id, "value": "482914", "value_kind": "text"},   # trecho inventado
            {"name": CODIGO, "element_id": el.id, "value": None, "value_kind": "text"},         # nome = o código
            {"name": "contato", "element_id": CODIGO, "value": None, "value_kind": "text"},     # id = o código
        ]
        args = variantes[chamadas % len(variantes)]
        chamadas += 1
        return Decision(tool="read_value", args={"rationale": f"o valor e {CODIGO}", **args}), Usage()

    decide0 = inner.decide
    inner.plan, inner.decide = _plano_com_leitura(inner, max_tentativas_da_leitura=1), decide
    run = harness.run(["android-01"])
    db = harness.state.db                                                     # type: ignore[union-attr]

    def parado() -> bool:
        o = db.one("SELECT status FROM objectives WHERE run_id=?", (run.id,))
        return o is not None and o["status"] in ("waiting_user", "failed", "succeeded")

    await harness.wait(parado, 60, "objetivo parado na leitura")
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["status"] != "succeeded" and chamadas >= 4
    acoes = db.query("SELECT * FROM actions WHERE tool='read_value'")
    assert acoes and all(a["status"] == "rejected" and a["rationale"] is None for a in acoes)
    assert db.scalar("SELECT COUNT(*) FROM step_outputs") == 0
    for tabela, colunas in (("actions", "args, result, error, rationale"), ("attempts", "error, observed_result"),
                            ("evidence", "note"), ("events", "message, data"), ("objectives", "blocked_reason, needs"), ("steps", "title, goal, bindings, variables")):
        for linha in db.query(f"SELECT {colunas} FROM {tabela}"):
            texto = " ".join(str(v) for v in dict(linha).values())
            assert CODIGO not in texto and "482914" not in texto, (tabela, dict(linha))


async def test_referencia_sem_etapa_que_a_leia_e_defeito_do_plano(harness: Harness) -> None:
    inner = harness.ai.inner
    seen: dict[str, Any] = {"decisoes": 1}
    inner.plan = _plano_com_leitura(inner, contato_de="inexistente")
    inner.decide = _decide_leitura(inner, seen)
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, timeout=60)
    db = harness.state.db                                                     # type: ignore[union-attr]
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["status"] == "failed" and "defeito do plano" in obj["status_detail"]
    assert "'inexistente'" in obj["status_detail"] and "nada foi inventado" in obj["status_detail"]
    conversa = db.one("SELECT * FROM steps WHERE objective_id=? AND key='open_conversation'", (obj["id"],))
    assert conversa["attempts"] == 0                        # nenhuma tentativa gasta com o molde no lugar do valor
    assert not harness.fakes["android-01"].messages


async def test_recuperacao_reaproveita_o_valor_sem_reler(harness: Harness) -> None:
    harness.cfg.file.ai.cascade_blocked_to_tier1 = False    # o bloqueio forçado aqui é o que o teste exercita: a cascata do 17.10 o absorveria no tier 1
    inner = harness.ai.inner
    seen: dict[str, Any] = {"decisoes": 1}
    # A dependência EXPLÍCITA da leitura é o caso que a recuperação refaria: sem o 24.3, "refazer a navegação" levava
    # a etapa de leitura comprovada de volta ao plano, e ela lia de novo.
    inner.plan = _plano_com_leitura(inner, max_tentativas_da_conversa=1, dependencia_explicita=True)
    decide1 = _decide_leitura(inner, seen)
    falhou = {"n": 0}

    async def decide(req: Any) -> Any:
        if req.ctx.step_key == "open_conversation" and falhou["n"] == 0:
            falhou["n"] += 1                                  # a 1ª versão da etapa falha de vez (1 tentativa)
            return Decision(tool="step_blocked", args={"rationale": "r", "kind": "other", "reason": "falha simulada",
                                                       "needs_user": False}), Usage()
        return await decide1(req)

    inner.decide = decide
    run = harness.run(["android-01"])
    assert (await harness.wait_run(run.id, timeout=90)).status == "completed"
    db = harness.state.db                                                     # type: ignore[union-attr]
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["plan_version"] >= 2                         # houve replano
    # a leitura comprovada foi atravessada: uma só versão dela, uma só leitura, e o valor reaproveitado na v2
    assert db.scalar("SELECT COUNT(*) FROM steps WHERE objective_id=? AND key='read_contact'", (obj["id"],)) == 1
    assert db.scalar("SELECT COUNT(*) FROM actions WHERE tool='read_value' AND status='done'") == 1
    v2 = db.one("SELECT * FROM steps WHERE objective_id=? AND key='open_conversation' AND plan_version=?",
                (obj["id"], obj["plan_version"]))
    assert v2["status"] == "succeeded" and LIDO in v2["title"]
    assert [m.contact for m in harness.fakes["android-01"].messages] == [LIDO]


async def test_referencia_para_a_frente_e_defeito_do_plano_e_nao_espera_para_sempre(harness: Harness) -> None:
    # A leitura vem DEPOIS de quem a usa: esperar por ela seria parar para sempre (a etapa anterior, pronta, é sempre
    # a escolhida). É defeito do plano, sem tentativa gasta.
    inner = harness.ai.inner
    inner.plan = _plano_com_leitura(inner, leitura_no_fim=True)
    inner.decide = _decide_leitura(inner, {"decisoes": 1})
    run = harness.run(["android-01"])
    await harness.wait_run(run.id, timeout=60)
    db = harness.state.db                                                     # type: ignore[union-attr]
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    assert obj["status"] == "failed" and "defeito do plano" in obj["status_detail"]
    assert "'Ler o contato da lista' vem depois dela" in obj["status_detail"]
    for chave in ("open_conversation", "read_contact"):
        assert db.scalar("SELECT attempts FROM steps WHERE objective_id=? AND key=?", (obj["id"], chave)) == 0


async def test_produtora_anterior_ainda_aberta_faz_a_etapa_esperar_com_o_motivo(harness: Harness) -> None:
    # O ramo "espera" de `_saida_ausente`: a etapa que lê o valor vem ANTES e ainda não concluiu (o plano não as ligou
    # pela ordem). Com a dependência implícita da materialização, o despacho não chega aqui pelo caminho comum; o
    # ramo é conferido direto, sobre um plano materializado e não executado.
    inner = harness.ai.inner
    inner.plan = _plano_com_leitura(inner)
    run = harness.run(["android-01"], mode="plan")
    db = harness.state.db                                                     # type: ignore[union-attr]

    def materializado() -> bool:
        o = db.one("SELECT id FROM objectives WHERE run_id=?", (run.id,))
        return o is not None and db.one("SELECT id FROM steps WHERE objective_id=? AND key='open_conversation'",
                                        (o["id"],)) is not None

    await harness.wait(materializado, 30, "plano materializado")
    obj = db.one("SELECT * FROM objectives WHERE run_id=?", (run.id,))
    conversa = db.one("SELECT * FROM steps WHERE objective_id=? AND key='open_conversation'", (obj["id"],))
    assert harness.state.repo.resolver_saidas(conversa["id"])[1] == ["contato"]     # type: ignore[union-attr]
    harness.state.scheduler._saida_ausente(obj, conversa, ["contato"])             # type: ignore[union-attr]  # noqa: SLF001
    obj = db.one("SELECT * FROM objectives WHERE id=?", (obj["id"],))
    assert obj["status"] == "waiting_user"
    assert "'Ler o contato da lista' ainda não leu" in obj["blocked_reason"] and obj["needs"]
    assert db.scalar("SELECT COUNT(*) FROM step_outputs") == 0

