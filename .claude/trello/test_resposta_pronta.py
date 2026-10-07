"""28.71/28.77: resposta do dono por app ou web -> comando pronto -> registro direto, sem confirmação (fakes, sem rede nem banco real).

Prova `simulated`: banco e Trello são fakes; nada escreve em cartão de verdade.
Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_resposta_pronta.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import registrar_resposta as rr  # noqa: E402
import resposta_pronta as rp  # noqa: E402


def entrada(id_: int, canal: str, texto: str, responde_a: str | None = None, tipo: str = "mensagem",
            alvo: str | None = None, quando: str = "2026-10-07T01:10:00+00:00") -> dict[str, object]:
    return {"id": id_, "canal": canal, "tipo": tipo, "responde_a": responde_a, "alvo": alvo, "texto": texto,
            "estado": "recebida", "recebida_em": quando}


def app(id_: int, p: str, texto: str = "sim, pode") -> dict[str, object]:
    return entrada(id_, "trello", texto, f"pergunta:{p};autoria=app_do_dono")


def tg(id_: int, texto: str, **kw: object) -> dict[str, object]:
    return entrada(id_, "telegram", texto, **kw)  # type: ignore[arg-type]


def cartao(p: str, id_: str, desc: str = "Corpo", lista: str = rp.LISTA_PERGUNTAS) -> dict[str, object]:
    return {"id": id_, "name": f"{p}. Regra de teste", "desc": desc, "idList": lista}


ABERTOS = [cartao("P-033", "c33"), cartao("P-034", "c34"), cartao("P-035", "c35")]


class FakeTrello:
    """Cartões por id; escreve só na memória. `_pedir`, `atualizar_cartao` e `criar_cartao` como o ClienteTrello."""

    def __init__(self, cartoes: list[dict[str, object]], decisoes: list[str] | None = None) -> None:
        self.cartoes = {str(c["id"]): dict(c, shortLink="s" + str(c["id"])) for c in cartoes}
        self.decisoes = list(decisoes or [])
        self.escritas = 0
        self.comentarios = 0

    async def cartoes_da_lista(self, lista: str) -> list[dict[str, object]]:
        return [c for c in self.cartoes.values() if c["idList"] == lista]

    async def _pedir(self, metodo: str, caminho: str, params=None, corpo=None):  # noqa: ANN001, ANN202
        if metodo == "GET" and caminho.startswith("/1/cards/"):
            return self.cartoes[caminho.rsplit("/", 1)[1]]
        if metodo == "GET" and caminho == f"/1/lists/{rr.LISTA_DECISOES}/cards":
            return [{"name": n} for n in self.decisoes]
        if metodo == "PUT" and caminho.startswith("/1/cards/"):
            self.escritas += 1
            c = self.cartoes.get(caminho.rsplit("/", 1)[1])
            if c is not None and corpo:
                c["idList"], c["name"] = corpo["idList"], corpo["name"]
            return {}
        raise AssertionError(f"chamada inesperada: {metodo} {caminho}")

    async def atualizar_cartao(self, cid: str, desc: str | None = None, nome: str | None = None):  # noqa: ANN202
        self.escritas += 1
        if desc is not None:
            self.cartoes[cid]["desc"] = desc
        if nome is not None:
            self.cartoes[cid]["name"] = nome
        return {}

    async def criar_cartao(self, lista: str, nome: str, desc: str):  # noqa: ANN202
        self.escritas += 1
        self.decisoes.append(nome)
        return {"id": "nova", "shortUrl": "https://trello.com/c/nova"}


class FakeDb:
    def __init__(self, linhas: list[dict[str, object]]) -> None:
        self.linhas, self.sql = linhas, ""

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, object]]:
        self.sql = sql
        return [r for r in self.linhas if int(r["id"]) > params[0]]  # type: ignore[arg-type]


def plano(entradas, abertos=ABERTOS, respondidos=()):  # noqa: ANN001, ANN201
    return rp.planejar(entradas, list(abertos), list(respondidos))

# ---------------------------------------------------------------- classificação do texto (28.77)
@pytest.mark.parametrize("texto", ["sim", "Sim!", "não", "nao", "Não.", "ok", "OK 👍", "siga a recomendação",
                                   "pode seguir a recomendação", "Pode seguir a recomendação.", "sim, pode", "pode",
                                   "A", "A fica", "opção B", "Letra c", "B fica"])
def test_textos_claros(texto: str) -> None:
    assert rp.classificar(texto, "P-033") == ("clara", "")


@pytest.mark.parametrize("texto,motivo", [
    ("", "vazio"), ("   ", "vazio"), (None, "vazio"),
    ("talvez", "curto"), ("depois eu vejo", "curto"), ("hmm", "curto"),
    ("sim?", "pergunta"), ("pode ser a A ou a B?", "pergunta"),
    ("sim, e também a P-034", "mais de uma"), ("ok para a P-034", "mais de uma"), ("P-033 e P-034: sim", "mais de uma"),
    ("sim nao", "juntos"), ("não siga a recomendação", "juntos"),
])
def test_textos_ambiguos(texto: str | None, motivo: str) -> None:
    classe, porque = rp.classificar(texto, "P-033")
    assert classe == "ambigua" and porque


def test_texto_longo_livre_e_curto_com_ressalva_sao_livres_nao_ambiguos() -> None:
    assert rp.classificar("A segunda pasta só depois de eu conferir a primeira, combinado", "P-033")[0] == "livre"
    assert rp.classificar("sim, mas só depois de conferir", "P-033")[0] == "livre"
    assert rp.classificar("sim, mas troca a regra de orçamento para dez dólares por dia no total", "P-033")[0] == "livre"


def test_a_mesma_pergunta_citada_no_texto_nao_e_ambigua() -> None:
    assert rp.classificar("sim, P-033", "P-033")[0] == "livre"


# ---------------------------------------------------------------- ligação
def test_liga_a_resposta_ao_cartao_certo_e_ja_esta_pronta() -> None:
    [p] = plano([app(10, "P-034", "A fica")])
    assert p.cartao and p.cartao["id"] == "c34" and p.pergunta == "P-034" and p.situacao == "aberta"
    assert p.quando == "07/10 01:10Z" and p.pronta and p.classe == "clara"      # sem confirmação: pronta de saída
    assert "--cartao c34" in rp.comando(p) and "--entrada 10" in rp.comando(p) and "--canal trello" in rp.comando(p)
    assert "--quando '07/10 01:10Z'" in rp.comando(p) and "A fica" in rp.comando(p)
    assert "--confirmacao" not in rp.comando(p) and "pronta para registrar" in rp.linha(p)


def test_so_resposta_do_dono_em_cartao_de_pergunta_conta() -> None:
    ent = [entrada(11, "trello", "x", "fato:abc"), entrada(12, "telegram", "x", "pergunta:P-033;autoria=app_do_dono"),
           entrada(13, "trello", "sim", "pergunta:P-033;autoria=app"), entrada(14, "trello", "sim",
                                                                           "pergunta:P-033;autoria=nao_confirmada")]
    assert plano(ent) == []


def test_autoria_e_so_informacao_app_web_ou_ausente_valem() -> None:
    for resp, esperado in (("pergunta:P-033;autoria=app_do_dono", "app_do_dono"), ("pergunta:P-033;autoria=digitado", "digitado"),
                           ("pergunta:P-033", ""), ("pergunta:P-033;autoria=", "")):
        [p] = plano([entrada(10, "trello", "sim", resp)])
        assert p.pronta and p.autoria == esperado
        assert ("--autoria" in rp.comando(p)) == bool(esperado)


def test_resposta_vazia_nao_some_em_silencio() -> None:
    [p] = plano([app(10, "P-033", "   ")])
    assert p.classe == "ambigua" and not p.pronta and "ambígua" in rp.linha(p) and "texto vazio" in rp.linha(p)


def test_sem_cartao_aberto() -> None:
    [p] = plano([app(10, "P-099")])
    assert p.situacao == "sem_cartao" and p.cartao is None and not p.pronta
    assert "sem cartão" in rp.linha(p) and "comando" not in "\n".join(rp.relatorio([p], aplicando=False))


def test_pergunta_ja_respondida_e_ignorada() -> None:
    ja = [cartao("P-033", "c33", lista=rr.LISTA_RESPONDIDAS)]
    p = plano([app(10, "P-033")], abertos=[], respondidos=ja)[0]
    assert p.situacao == "ja_respondida" and not p.pronta and "já está em Perguntas respondidas" in rp.linha(p)
    assert "comando" not in "\n".join(rp.relatorio([p], aplicando=False))


def test_duas_respostas_a_mesma_pergunta_vale_a_mais_recente() -> None:
    a, b = plano([app(10, "P-033", "A"), app(11, "P-033", "B")])
    assert a.substituida_por == 11 and not a.pronta and b.pronta
    # a mais recente ambígua não é salva pela anterior: volta como pergunta nova
    a, b = plano([app(10, "P-033", "sim"), app(11, "P-033", "talvez")])
    assert not a.pronta and not b.pronta and b.classe == "ambigua"


# ---------------------------------------------------------------- sem confirmação (28.77)
def test_nao_existe_mais_confirmacao_em_bloco() -> None:
    assert not hasattr(rp, "eh_confirmacao") and not hasattr(rp, "confirmacoes_do_telegram")
    # o "ok" do Telegram, anterior ou posterior, não muda nada: a resposta do app já estava pronta
    for ent in ([app(10, "P-033"), tg(11, "ok")], [tg(9, "ok"), app(10, "P-033")], [app(10, "P-033")]):
        [p] = plano(ent)
        assert p.pronta and p.situacao == "aberta"
    with pytest.raises(SystemExit):
        rp.main(["--base", "1", "--ok-no-chat", "ok"])


# ---------------------------------------------------------------- aplicar
def test_aplica_direto_sem_confirmacao_so_as_prontas_uma_por_uma() -> None:
    fake = FakeTrello(ABERTOS)
    ps = plano([app(10, "P-033", "A fica"), app(11, "P-034", "B"), app(12, "P-035", "talvez"), app(13, "P-099")])
    asyncio.run(rp.aplicar(fake, ps))
    assert [p.resultado for p in ps] == ["registrada", "registrada", "", ""]
    c33, c34, c35 = fake.cartoes["c33"], fake.cartoes["c34"], fake.cartoes["c35"]
    assert str(c33["desc"]).startswith("**RESPOSTA DO DONO (07/10 01:10Z, entrada 10") and '"A fica"' in str(c33["desc"])
    assert "digitada por ele no Trello (app ou web); autoria: app_do_dono" in str(c33["desc"])
    assert "Confirmada" not in str(c33["desc"]) and "em bloco" not in str(c33["desc"]) and "conferida" not in str(c33["desc"])
    assert c33["idList"] == rr.LISTA_RESPONDIDAS and c34["idList"] == rr.LISTA_RESPONDIDAS
    assert c35["idList"] == rp.LISTA_PERGUNTAS and c35["desc"] == "Corpo"          # a ambígua ficou como estava
    assert sorted(fake.decisoes) == ["⚖️ P-033. Regra de teste · respondida em 07/10",
                                     "⚖️ P-034. Regra de teste · respondida em 07/10"]
    assert fake.comentarios == 0
    texto = "\n".join(rp.relatorio(ps, aplicando=True))
    assert texto.count("-> registrada") == 2 and "ambígua (texto curto" in texto and "voltar como pergunta nova" in texto
    assert "2 pronta(s)" in texto and "1 ambígua(s)" in texto and "aguardando ok" not in texto


def test_ambigua_nunca_chama_registrar(monkeypatch: pytest.MonkeyPatch) -> None:
    chamadas: list[object] = []

    async def espia(*a: object, **k: object) -> str:
        chamadas.append(k)
        return "registrada"
    monkeypatch.setattr(rr, "registrar", espia)
    fake = FakeTrello(ABERTOS)
    ps = plano([app(10, "P-033", "talvez"), app(11, "P-034", "sim e a P-035?"), app(12, "P-035", "   ")])
    asyncio.run(rp.aplicar(fake, ps))
    assert chamadas == [] and fake.escritas == 0
    assert all("voltar como pergunta nova" in rp.linha(p) for p in ps)


def test_texto_livre_e_registrado_literal_e_marcado_para_a_canais() -> None:
    fake = FakeTrello(ABERTOS)
    livre = "A segunda pasta só depois de eu conferir a primeira, combinado"
    ps = plano([app(10, "P-033", livre)])
    asyncio.run(rp.aplicar(fake, ps))
    assert ps[0].classe == "livre" and ps[0].resultado == "registrada"
    assert f'"{livre}"' in str(fake.cartoes["c33"]["desc"])                        # literal, com a condição dele
    texto = "\n".join(rp.relatorio(ps, aplicando=True))
    assert "texto livre: ler (Canais)" in texto and "1 de texto livre" in texto


def test_idempotencia_repetir_nao_reescreve_nem_duplica() -> None:
    entradas = [app(10, "P-033", "A fica")]
    fake = FakeTrello(ABERTOS)
    asyncio.run(rp.aplicar(fake, plano(entradas)))
    desc, decisoes = fake.cartoes["c33"]["desc"], list(fake.decisoes)
    # execução que caiu no meio: bloco e decisão feitos, o cartão ainda na lista de perguntas -> só falta mover
    fake.cartoes["c33"].update(idList=rp.LISTA_PERGUNTAS, name="P-033. Regra de teste")
    antes = fake.escritas
    ps2 = plano(entradas, abertos=[fake.cartoes["c33"]])
    asyncio.run(rp.aplicar(fake, ps2))
    assert fake.cartoes["c33"]["desc"] == desc and fake.decisoes == decisoes
    assert ps2[0].resultado == "registrada" and fake.escritas == antes + 1          # só o "mover" que faltava
    # tudo no lugar e o cartão ainda aberto na lista (nome já com a data): nada a fazer
    fake.cartoes["c33"]["name"] = "P-033. Regra de teste" + rr.MARCA_DATA + "07/10"
    fake.cartoes["c33"]["idList"] = rr.LISTA_RESPONDIDAS
    antes = fake.escritas
    ps3 = plano(entradas, abertos=[dict(fake.cartoes["c33"], idList=rp.LISTA_PERGUNTAS)])
    asyncio.run(rp.aplicar(fake, ps3))
    assert ps3[0].resultado == "ja_registrada" and fake.escritas == antes
    assert "-> já registrada" in "\n".join(rp.relatorio(ps3, aplicando=True))


def test_falha_numa_pendente_nao_derruba_as_outras_e_nao_vaza_a_mensagem() -> None:
    fake = FakeTrello(ABERTOS)
    del fake.cartoes["c33"]                              # o GET do cartão falha (KeyError com o id dentro)
    ps = plano([app(10, "P-033"), app(11, "P-034")], abertos=ABERTOS)
    asyncio.run(rp.aplicar(fake, ps))
    assert ps[0].resultado == "faltou (KeyError)" and ps[1].resultado == "registrada"
    assert "c33" not in "\n".join(rp.relatorio(ps, aplicando=True))

# ---------------------------------------------------------------- saída
def test_saida_corta_em_80_e_redige_handle_email_telefone() -> None:
    literal = "A fica, mas fale com @fulana_teste e use fulana@exemplo.com ou +55 11 91234-5678 " + "x" * 200
    [p] = plano([app(10, "P-033", literal)])
    saida = "\n".join(rp.relatorio([p], aplicando=False))
    assert "@fulana_teste" not in saida and "fulana@exemplo.com" not in saida and "91234-5678" not in saida
    assert "x" * 60 not in saida and "…" in saida
    assert len(rp.trecho(literal)) <= rp.LIMITE_LITERAL_NA_SAIDA
    assert "comando:" in saida and "--confirmacao" not in saida


def test_comando_com_aspas_e_barra_vai_entre_aspas_simples() -> None:
    [p] = plano([app(10, "P-033", "diz 'sim' e \"talvez\"")])
    c = rp.comando(p)
    assert "'\"'\"'" in c or "'sim'" not in c               # a aspa simples do dono não fecha a citação do shell


def test_sem_respostas() -> None:
    assert rp.relatorio([], aplicando=False) == ["nenhuma resposta do dono em cartão de pergunta nas entradas novas"]


# ---------------------------------------------------------------- leitura e CLI
def test_leitura_filtra_pela_base_e_so_le() -> None:
    db = FakeDb([app(5, "P-033"), app(10, "P-034")])
    r = rp.ler_entradas(db, 5)
    assert [e["id"] for e in r] == [10]
    assert db.sql.lstrip().upper().startswith("SELECT") and "do_dono=1" in db.sql


def test_quando_de_converte_para_utc() -> None:
    assert rp.quando_de("2026-10-06T22:38:00-03:00") == "07/10 01:38Z"
    assert rp.quando_de("2026-10-06T19:38:00Z") == "06/10 19:38Z"
    assert rp.quando_de("2026-10-06T19:38:00") == "06/10 19:38Z"
    assert rp.quando_de("lixo") == ""


def test_main_exige_base_e_o_padrao_e_ensaio(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit):
        rp.main([])
    vistos: list[bool] = []

    async def falso(args):  # noqa: ANN001, ANN202
        vistos.append(args.aplicar)
        return 0
    monkeypatch.setattr(rp, "_principal", falso)
    assert rp.main(["--base", "3"]) == 0 and vistos == [False]
