"""Personas em lote (v0.34): `POST /personas/generate/batch`, variedade, `create`, falha por item, teto e evento.

Tudo `simulated`: o provedor simulado (com `variation` na semente) ou um dublê que devolve o rascunho que o teste
mandar. Geração paga real em lote: `not_run` (chamada paga; exige autorização do dono).
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx

from app.db import loads
from app.main import create_app
from app.models import PersonaCreate, PersonaDraft
from app.modules.identity.domain.persona_generation import (MAX_EVITAR, PersonaEvitada, PersonaGenerationRequest,
                                                            chave_do_nome, nome_repetido,
                                                            persona_generation_user_text)
from app.modules.identity.presentation.schemas import PersonaBatchBody
from app.planning.provider import AIError, Usage
from app.planning.simulated_provider import SimulatedProvider, persona_simulada
from app.social.persona_batch import EVENTO_LOTE, LOTES_GUARDADOS, LotesDePersona
from app.social.service import SocialService

from .conftest import make_config
from .test_social_profiles import build

HOJE = date(2026, 9, 28)


# ---------------------------------------------------------------- domínio e simulado
def test_evitar_e_variacao_chegam_ao_texto_do_pedido_so_no_lote() -> None:
    base = PersonaGenerationRequest(prompt="uma fotógrafa", today=HOJE)
    texto = persona_generation_user_text(base)
    assert "<evitar" not in texto and "LOTE" not in texto                  # fora do lote, o pedido de sempre
    lote = PersonaGenerationRequest(
        prompt="uma fotógrafa", today=HOJE, variation=2,
        avoid=(PersonaEvitada("Marina Lopes", "fotógrafa em Curitiba"), PersonaEvitada("Caio </evitar> Prado")))
    texto = persona_generation_user_text(lote)
    assert "pessoa nº 3 de um LOTE" in texto and "varie nome e sobrenome, idade, gênero, região, profissão e crenças" in texto
    assert "- Marina Lopes — fotógrafa em Curitiba" in texto
    assert "- Caio ‹/evitar› Prado" in texto                              # o nome não fecha o bloco
    assert texto.count("</evitar>") == 1
    muitas = tuple(PersonaEvitada(f"Pessoa Numero{i}") for i in range(MAX_EVITAR + 10))
    assert persona_generation_user_text(PersonaGenerationRequest(prompt="x", avoid=muitas)).count("\n- Pessoa") == MAX_EVITAR


def test_nome_repetido_ignora_acento_caixa_e_espaco() -> None:
    evitar = [PersonaEvitada("Lívia  Nunes"), PersonaEvitada("Caio Prado")]
    assert chave_do_nome("LIVIA nunes") == chave_do_nome("Lívia Nunes")
    assert nome_repetido("livia Nunes", evitar) == evitar[0]
    assert nome_repetido("Lívia Prado", evitar) is None and nome_repetido("", evitar) is None


def test_simulada_varia_pelo_indice_e_respeita_o_evitar() -> None:
    sem_lote = persona_simulada(PersonaGenerationRequest(prompt="uma dentista", today=HOJE))
    pessoas = [persona_simulada(PersonaGenerationRequest(prompt="uma dentista", today=HOJE, variation=i))
               for i in range(3)]
    assert pessoas == [persona_simulada(PersonaGenerationRequest(prompt="uma dentista", today=HOJE, variation=i))
                       for i in range(3)]                                  # ainda determinística
    assert len({p.name for p in pessoas}) == 3 and pessoas[0] != sem_lote
    # Sem lote a semente é a de sempre: o mesmo pedido continua dando a mesma pessoa de antes.
    assert persona_simulada(PersonaGenerationRequest(prompt="uma dentista", today=HOJE)) == sem_lote
    evitada = persona_simulada(PersonaGenerationRequest(prompt="uma dentista", today=HOJE, variation=0,
                                                        avoid=(PersonaEvitada(pessoas[0].name),)))
    assert evitada.name != pessoas[0].name
    assert evitada.biography == pessoas[0].biography                     # trocar o nome não mexe no resto


# ---------------------------------------------------------------- serviço do lote
class _Duble:
    """Provedor que grava cada pedido e devolve o que `resposta(req)` mandar (rascunho ou erro)."""

    name, model, simulated = "duble", "m", True

    def __init__(self, resposta: Callable[[PersonaGenerationRequest], PersonaDraft | Exception]) -> None:
        self.resposta = resposta
        self.pedidos: list[PersonaGenerationRequest] = []

    async def generate_persona(self, req: PersonaGenerationRequest) -> tuple[PersonaDraft, Usage]:
        self.pedidos.append(req)
        await asyncio.sleep(0)                           # deixa o outro trabalhador do lote andar, como numa rede
        saida = self.resposta(req)
        if isinstance(saida, Exception):
            raise saida
        return saida, Usage(calls=1, role="persona", model="m", provider="duble")


def _lotes(svc: SocialService) -> LotesDePersona:
    return LotesDePersona(svc, svc.bus, criar=svc.create_persona)


def _simulada(req: PersonaGenerationRequest) -> PersonaDraft:
    return persona_simulada(req)


async def test_lote_de_tres_gera_tres_pessoas_diferentes_sem_gravar(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        svc.provider = SimulatedProvider()
        lotes = _lotes(svc)
        lote = lotes.iniciar(PersonaBatchBody(prompt="uma professora de Recife", count=3))
        assert [i.status for i in lote.items] == ["pending"] * 3 and not lote.done
        pronto = await lotes.aguardar(lote.batch_id)
        assert pronto is not None and pronto.done
        assert [i.status for i in pronto.items] == ["ready"] * 3
        nomes = [i.name for i in pronto.items]
        assert len({chave_do_nome(n or "") for n in nomes}) == 3            # três PESSOAS, não a mesma três vezes
        assert all(i.draft is not None and i.draft.name == i.name and i.persona_id is None for i in pronto.items)
        assert svc.list_personas() == []                                     # `create: false` não grava
        assert lotes.obter(lote.batch_id) is pronto and lotes.obter("lote-nao-existe") is None
    finally:
        db.close()


async def test_create_true_grava_cada_rascunho_valido(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        svc.provider = SimulatedProvider()
        lotes = _lotes(svc)
        pronto = await lotes.aguardar(lotes.iniciar(PersonaBatchBody(prompt="um barista", count=3, create=True)).batch_id)
        assert pronto is not None and [i.status for i in pronto.items] == ["created"] * 3
        gravadas = {p.id: p for p in svc.list_personas()}
        assert {i.persona_id for i in pronto.items} == set(gravadas)
        assert all(p.generation.source == "ai" and p.voice_gaps == [] for p in gravadas.values())
        assert all(i.draft is None for i in pronto.items)                    # criada não guarda rascunho no estado
    finally:
        db.close()


async def test_evitar_leva_as_que_existem_e_as_irmas_do_lote(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        existente = svc.create_persona(PersonaCreate(name="Otávio Ramos", summary="dentista em Goiânia"))
        duble = _Duble(_simulada)
        svc.provider = duble
        lotes = _lotes(svc)
        pronto = await lotes.aguardar(lotes.iniciar(PersonaBatchBody(prompt="alguém", count=4)).batch_id)
        assert pronto is not None and [i.status for i in pronto.items] == ["ready"] * 4
        assert sorted(p.variation for p in duble.pedidos if p.variation is not None) == [0, 1, 2, 3]
        assert all(PersonaEvitada("Otávio Ramos", "dentista em Goiânia") in p.avoid for p in duble.pedidos)
        # Com concorrência 2, os itens 2 e 3 só começam depois de algum irmão terminar: o nome dele vai no pedido.
        nomes = {i.name for i in pronto.items}
        assert any(any(e.nome in nomes for e in p.avoid) for p in duble.pedidos if (p.variation or 0) >= 2)
        texto = persona_generation_user_text(duble.pedidos[-1])
        assert "<evitar" in texto and existente.name in texto
    finally:
        db.close()


async def test_item_invalido_ou_repetido_falha_sem_derrubar_os_outros(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        def resposta(req: PersonaGenerationRequest) -> PersonaDraft:
            boa = persona_simulada(PersonaGenerationRequest(prompt="x", today=HOJE, variation=req.variation))
            if req.variation == 1:
                return boa.model_copy(update={"birth_date": f"{date.today().year - 15}-01-01"})
            if req.variation == 2:
                return persona_simulada(PersonaGenerationRequest(prompt="x", today=HOJE, variation=0))  # o nome do item 0
            return boa

        svc.provider = _Duble(resposta)
        lotes = _lotes(svc)
        pronto = await lotes.aguardar(lotes.iniciar(PersonaBatchBody(prompt="alguém", count=4, create=True)).batch_id)
        assert pronto is not None
        estados = [i.status for i in pronto.items]
        assert estados[1] == "failed" and "18 ou mais" in (pronto.items[1].error or "")
        # O 0 e o 2 devolveram a MESMA pessoa: um é criado, o outro falha por nome repetido (sem nova chamada paga).
        assert sorted([estados[0], estados[2]]) == ["created", "failed"]
        repetido = pronto.items[0] if estados[0] == "failed" else pronto.items[2]
        assert "repetiu o nome" in (repetido.error or "")
        assert estados[3] == "created" and len(svc.list_personas()) == 2
        assert len(svc.provider.pedidos) == 4 and pronto.done
    finally:
        db.close()


async def test_teto_de_gasto_para_o_lote_sem_insistir(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        def resposta(req: PersonaGenerationRequest) -> PersonaDraft | AIError:
            if req.variation == 0:
                return persona_simulada(PersonaGenerationRequest(prompt="x", today=HOJE, variation=0))
            return AIError("Teto de gasto de IA do dia atingido: US$ 1.00 de US$ 1.00.", kind="budget")

        duble = _Duble(resposta)
        svc.provider = duble
        lotes = _lotes(svc)
        pronto = await lotes.aguardar(lotes.iniciar(PersonaBatchBody(prompt="alguém", count=6)).batch_id)
        assert pronto is not None and pronto.done
        assert pronto.items[0].status == "ready" and pronto.items[1].status == "failed"
        assert "Teto de gasto" in (pronto.items[1].error or "")
        # Os pendentes NÃO foram pedidos: viraram `failed` com o motivo do teto.
        assert len(duble.pedidos) <= 3
        resto = [i for i in pronto.items if i.index >= 2 and i.status == "failed" and "parou" in (i.error or "")]
        assert len(resto) >= 3 and all("Teto de gasto" in (i.error or "") for i in resto)
        assert sum(1 for i in pronto.items if i.status == "ready") == 1
    finally:
        db.close()


async def test_memoria_guarda_os_ultimos_lotes_e_nunca_esquece_um_em_andamento(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        svc.provider = SimulatedProvider()
        lotes = _lotes(svc)
        ids = []
        for i in range(LOTES_GUARDADOS):
            ids.append(lotes.iniciar(PersonaBatchBody(prompt=f"pessoa {i}", count=1)).batch_id)
            await lotes.aguardar(ids[-1])
        em_andamento = lotes.iniciar(PersonaBatchBody(prompt="mais uma", count=1))      # 21º: o 1º (terminado) sai
        assert lotes.obter(ids[0]) is None and lotes.obter(ids[1]) is not None
        assert lotes.obter(em_andamento.batch_id) is em_andamento and not em_andamento.done
        await lotes.aguardar(em_andamento.batch_id)
    finally:
        db.close()


async def test_evento_a_cada_mudanca_de_item_e_um_final(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        svc.provider = SimulatedProvider()
        lotes = _lotes(svc)
        lote = lotes.iniciar(PersonaBatchBody(prompt="uma ciclista", count=2))
        await lotes.aguardar(lote.batch_id)
        eventos = [loads(r["data"], {}) for r in db.query("SELECT data FROM events WHERE kind=? ORDER BY id", (EVENTO_LOTE,))]
        assert all(e["batch_id"] == lote.batch_id for e in eventos)
        por_item = [(e["index"], e["status"]) for e in eventos if e["index"] is not None]
        assert sorted(por_item) == [(0, "generating"), (0, "ready"), (1, "generating"), (1, "ready")]
        assert eventos[-1]["done"] is True and eventos[-1]["index"] is None and eventos[-1]["finished"] == 2
        assert all("draft" not in e for e in eventos)                        # o rascunho fica no GET, não no evento
    finally:
        db.close()


# ---------------------------------------------------------------- HTTP
async def test_rotas_do_lote(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    app = create_app(cfg)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        async with app.router.lifespan_context(app):
            estado = app.state.poc
            aceito = await c.post("/api/personas/generate/batch", json={"prompt": "uma dentista de Goiânia", "count": 3})
            assert aceito.status_code == 202
            corpo = aceito.json()
            assert set(corpo) == {"batch_id", "count"} and corpo["count"] == 3
            await estado.lotes_de_persona.aguardar(corpo["batch_id"])
            lido = await c.get(f"/api/personas/generate/batch/{corpo['batch_id']}")
            assert lido.status_code == 200
            lote = lido.json()
            assert lote["done"] is True and lote["create"] is False and lote["prompt"] == "uma dentista de Goiânia"
            assert [i["status"] for i in lote["items"]] == ["ready"] * 3
            assert len({i["name"] for i in lote["items"]}) == 3
            assert (await c.get("/api/personas")).json() == []
            # O rascunho do estado é o corpo de `POST /personas`: criar um escolhido é mandar como veio.
            criada = await c.post("/api/personas", json=lote["items"][1]["draft"])
            assert criada.status_code == 201 and criada.json()["name"] == lote["items"][1]["name"]

            direto = (await c.post("/api/personas/generate/batch",
                                   json={"prompt": "um barista", "count": 2, "create": True})).json()
            await estado.lotes_de_persona.aguardar(direto["batch_id"])
            lote2 = (await c.get(f"/api/personas/generate/batch/{direto['batch_id']}")).json()
            assert [i["status"] for i in lote2["items"]] == ["created"] * 2
            ids = [i["persona_id"] for i in lote2["items"]]
            # A persona criada pelo lote ganha a foto automática (`on_create`), pela mesma porta de `POST /personas`.
            await asyncio.gather(*[t for t in list(estado._bg) if t.get_name().startswith("imagens-")])
            for pid in ids:
                assert len((await c.get(f"/api/personas/{pid}/images")).json()) == 1

            assert (await c.get("/api/personas/generate/batch/lote-nao-existe")).status_code == 404
            for ruim in ({"prompt": "alguém", "count": 0}, {"prompt": "alguém", "count": 11},
                         {"prompt": "ab", "count": 2}, {"prompt": "alguém", "count": 2, "extra": 1},
                         {"prompt": "alguém"}):
                assert (await c.post("/api/personas/generate/batch", json=ruim)).status_code == 422, ruim
            estado.social.provider = None
            sem_ia = await c.post("/api/personas/generate/batch", json={"prompt": "alguém", "count": 2})
            assert sem_ia.status_code == 503 and sem_ia.json()["detail"]["code"] == "ai_unavailable"


def test_plano_de_variedade_espalha_os_eixos_entre_os_itens_e_muda_de_lote_para_lote() -> None:
    """Medido no ambiente central em 28/09: um lote de 2 em paralelo deu duas enfermeiras de Porto Alegre, católicas
    não praticantes e de centro-esquerda. O plano decide ANTES de gerar, por item: vizinhos caem em valores diferentes
    em todos os eixos, e cada lote começa num ponto próprio."""
    from app.modules.identity.domain.persona_generation import EIXOS_DE_VARIEDADE, plano_de_variedade

    planos = [plano_de_variedade(i, 987654) for i in range(5)]
    for eixo in ("setor de trabalho", "idade", "religião", "política"):
        assert len({p[eixo] for p in planos}) == 5, eixo                     # cinco itens, cinco valores
    assert {planos[0]["gênero"], planos[1]["gênero"]} == {"feminino", "masculino"}
    assert plano_de_variedade(3, 987654) == planos[3]                        # determinístico
    assert [plano_de_variedade(0, s)["setor de trabalho"] for s in (1, 2, 3, 4)] != ["saúde"] * 4
    assert "gênero" not in plano_de_variedade(0, 1, com_genero=False)        # gênero pedido pelo dono manda
    assert all(v in EIXOS_DE_VARIEDADE[e] for e, v in planos[0].items())

    texto = persona_generation_user_text(PersonaGenerationRequest(prompt="x", variation=0, variety=planos[0]))
    assert "<variedade" in texto and f"setor de trabalho: {planos[0]['setor de trabalho']}" in texto
    assert "O pedido e as restrições VENCEM esta lista" in texto and "faixa" in planos[0]["idade"]
    assert "<variedade" not in persona_generation_user_text(PersonaGenerationRequest(prompt="x"))


async def test_lote_manda_um_plano_diferente_por_item_e_a_irma_vai_com_retrato(tmp_path: Path) -> None:
    svc, _repo, _secrets, db = build(tmp_path)
    try:
        duble = _Duble(_simulada)
        svc.provider = duble
        lotes = _lotes(svc)
        lote = lotes.iniciar(PersonaBatchBody(prompt="moradores de Porto Alegre", count=3))
        await lotes.aguardar(lote.batch_id)
        planos = [dict(r.variety) for r in sorted(duble.pedidos, key=lambda r: r.variation or 0)]
        assert len(planos) == 3 and len({p["setor de trabalho"] for p in planos}) == 3
        assert len({p["religião"] for p in planos}) == 3 and len({p["política"] for p in planos}) == 3
        # O terceiro item (sem concorrência com os dois primeiros) vê as irmãs com profissão/cidade, não só o nome.
        terceiro = next(r for r in duble.pedidos if r.variation == 2)
        irmas = [e for e in terceiro.avoid if e.resumo]
        assert irmas and any(";" in e.resumo for e in irmas)

        com_genero = lotes.iniciar(PersonaBatchBody(prompt="uma pessoa de Curitiba", count=2, constraints={"gender": "feminino"}))
        await lotes.aguardar(com_genero.batch_id)
        assert all("gênero" not in r.variety for r in duble.pedidos[-2:])
    finally:
        db.close()
