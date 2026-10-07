"""28.69: um cartão por aparelho com conta real. Dados FICTÍCIOS (nomes, handles, e-mail e IP inventados), Trello e central FALSOS.

Prova `simulated`: nenhum teste fala com a central real nem com o Trello; o cliente é um fake em memória e `comentar` falha o
teste se for chamado. O modo `--aplicar` real fica `not_run`.
Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_cartoes_de_aparelho.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import cartoes_de_aparelho as C  # noqa: E402
from cartoes_de_aparelho import (  # noqa: E402
    ERRO,
    LISTA_CONCLUIDO,
    LISTA_EM_EXECUCAO,
    LISTA_EM_VALIDACAO,
    PAUSA,
    PRONTA,
    SEM_CONTA,
    VENCIDA,
    Cartao,
    FalhaDeLeitura,
    estado_do_aparelho,
)

AGORA = datetime(2026, 10, 6, 15, 0, tzinfo=UTC)
DESDE = "2026-10-06T12:00:00Z"
#: identidade fictícia, fora da lista de reserva da redação: só o código que NUNCA lê estes campos os segura
NOME = "Zoraide Benevides"
HANDLE = "zoraide.benevides.ig"
EMAIL = "zoraide.benevides@exemplo.invalid"
IP = "10.77.3.9"
SERIAL = "emulator-5654"
MOTIVO_LIVRE = f"experimento da {NOME} em {IP}, fale com {EMAIL}"
PROIBIDOS = (NOME, HANDLE, EMAIL, IP, SERIAL, "Benevides", "experimento", "Zoraide")


def sessao(status: str = "session_ready", *, stale: bool = False, cap: bool = False, desde: str | None = DESDE) -> dict:
    return {"status": status, "stale": stale, "unknown_at_cap": cap, "status_since": desde, "verified_at": desde,
            "instance_id": "x", "observed_username": HANDLE, "detail": f"visto como {HANDLE} em {IP}"}


def persona(sess: dict | None) -> dict:
    return {"profile_id": "p1", "username": HANDLE, "display_name": NOME, "name": NOME, "session": sess}


def inst(ident: str, **extra) -> dict:
    base = {"id": ident, "state": "online", "serial": SERIAL, "account_label": f"@{HANDLE}", "account_evidence": NOME,
            "locked_account": None, "repair_pause": None, "attention": f"{NOME} {EMAIL}", "worker_id": None}
    base.update(extra)
    return base


# ------------------------------------------------------------------------------------------ Trello falso
class TrelloFalso:
    """O quadro Execução em memória. `comentar` e `arquivar_cartao` falham o teste: o espelho só escreve descrição."""

    def __init__(self, cartoes: list[Cartao] | None = None) -> None:
        self.cartoes = {c.id: c for c in (cartoes or [])}
        self.chamadas: list[tuple] = []
        self._n = 0

    async def _pedir(self, metodo, caminho, *, params=None, corpo=None):  # noqa: ANN001
        assert metodo == "GET" and caminho.endswith("/cards")
        return [{"id": c.id, "name": c.nome, "desc": c.desc, "idList": c.lista} for c in self.cartoes.values()]

    async def criar_cartao(self, lista, nome, desc):  # noqa: ANN001
        self._n += 1
        cid = f"novo{self._n}"
        self.cartoes[cid] = Cartao(cid, nome, desc, lista)
        self.chamadas.append(("criar", cid))
        return {"id": cid}

    async def atualizar_cartao(self, card, *, nome=None, desc=None, lista=None):  # noqa: ANN001
        c = self.cartoes[card]
        self.cartoes[card] = Cartao(card, nome if nome is not None else c.nome, desc if desc is not None else c.desc,
                                    lista if lista is not None else c.lista)
        self.chamadas.append(("atualizar", card))
        return {"id": card}

    async def comentar(self, *a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("o espelho de aparelho NUNCA comenta em cartão")

    arquivar_cartao = comentar


def rodar(trello: TrelloFalso, instancias: list[dict], personas: dict, *, agora: datetime = AGORA,
          aplicar: bool = True, falhas: list[str] | None = None) -> tuple[C.Plano, list[str], list[str]]:
    saida: list[str] = []
    cartoes = asyncio.run(C.ler_cartoes(trello))
    escrever = (lambda plano: asyncio.run(C.aplicar(trello, plano))) if aplicar else None  # noqa: E731
    plano, f = C.principal(instancias, personas, falhas or [], cartoes, agora, not aplicar, escrever, saida.append)
    return plano, f, saida


PARQUE = [inst("android-01"), inst("android-02"), inst("android-03"), inst("android-04")]
PERSONAS = {"android-01": [persona(sessao())],
            "android-02": [persona(sessao("auth_required"))],
            "android-03": [],
            "android-04": [persona(sessao(stale=True))]}


# ------------------------------------------------------------------------------------------ o estado do aparelho
@pytest.mark.parametrize(("sess", "estado", "motivo"), [
    (sessao(), PRONTA, "sessão verificada"),
    (sessao(stale=True), VENCIDA, "verificação antiga"),
    (sessao("auth_required"), ERRO, "login necessário"),
    (sessao("auth_challenge"), ERRO, "desafio do app"),
    (sessao("wrong_account"), ERRO, "conta errada no aparelho"),
    (sessao("needs_person"), ERRO, "precisa de uma pessoa"),
    (sessao("unknown", cap=True), ERRO, "precisa de uma pessoa"),
    (sessao("unknown"), ERRO, "sessão não verificada"),
    (None, ERRO, "sessão não verificada"),
    (sessao("algo_novo"), ERRO, "sessão em estado desconhecido"),
])
def test_estado_da_sessao_em_vocabulario_fixo(sess, estado, motivo):
    e = estado_do_aparelho(inst("android-01"), [persona(sess)])
    assert (e.estado, e.motivo) == (estado, motivo)
    assert e.motivo in C.PASSO


def test_desde_vem_da_sessao_e_cai_no_verified_at():
    e = estado_do_aparelho(inst("android-01"), [persona(sessao())])
    assert e.desde == datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    s = sessao(desde=None)
    s["verified_at"] = "2026-10-05T08:30:00+00:00"
    assert estado_do_aparelho(inst("android-01"), [persona(s)]).desde == datetime(2026, 10, 5, 8, 30, tzinfo=UTC)
    s["verified_at"] = "lixo"
    assert estado_do_aparelho(inst("android-01"), [persona(s)]).desde is None


def test_precedencia_conta_travada_pausa_erro_e_pior_sessao():
    pausa = {"until": "2026-10-06T16:00:00Z", "since": "2026-10-06T13:00:00Z", "reason": MOTIVO_LIVRE, "by": NOME,
             "remaining_s": 3600}
    assert estado_do_aparelho(inst("android-01", locked_account=HANDLE), None).motivo == "conta travada"
    assert estado_do_aparelho(inst("android-01", locked_account=HANDLE), []).estado == ERRO
    e = estado_do_aparelho(inst("android-01", repair_pause=pausa), [persona(sessao("auth_required"))])
    assert (e.estado, e.motivo, e.desde) == (PAUSA, "pausa de reparo", datetime(2026, 10, 6, 13, 0, tzinfo=UTC))
    assert estado_do_aparelho(inst("android-01", state="error"), [persona(sessao())]).motivo == "aparelho com erro"
    # dois vínculos: vale a pior sessão, e no empate a mais antiga
    e = estado_do_aparelho(inst("android-01"), [persona(sessao()), persona(sessao("wrong_account", desde="2026-10-06T10:00:00Z")),
                                                persona(sessao("wrong_account", desde="2026-10-06T09:00:00Z"))])
    assert (e.motivo, e.desde) == ("conta errada no aparelho", datetime(2026, 10, 6, 9, 0, tzinfo=UTC))


def test_sem_vinculo_nao_e_conta_real_e_sem_leitura_nao_se_sabe():
    assert estado_do_aparelho(inst("android-03"), []).estado == SEM_CONTA
    assert estado_do_aparelho(inst("android-03"), None) is None
    # pausa de reparo num aparelho sem conta real não faz dele um aparelho com conta
    assert estado_do_aparelho(inst("android-03", repair_pause={"since": DESDE}), []).estado == SEM_CONTA


def test_idade_so_na_saida():
    assert C.idade(AGORA - timedelta(seconds=20), AGORA) == "há menos de 1 min"
    assert C.idade(AGORA - timedelta(minutes=7), AGORA) == "há 7 min"
    assert C.idade(AGORA - timedelta(hours=3), AGORA) == "há 3 h"
    assert C.idade(AGORA - timedelta(days=2), AGORA) == "há 2 d"
    assert C.idade(None, AGORA) == "tempo não informado"


# ------------------------------------------------------------------------------------------ o texto do cartão
def test_texto_do_cartao_tem_so_rotulo_estado_motivo_desde_e_passo():
    e = estado_do_aparelho(inst("android-01"), [persona(sessao("auth_required"))])
    nome, desc = C.montar_cartao("android-01", e)
    assert nome == "android-01 · sessão com erro"
    assert desc.splitlines()[:5] == ["Aparelho: android-01", "Estado da sessão: com erro", "Motivo: login necessário",
                                     "Desde: 2026-10-06 12:00Z", f"Próximo passo: {C.PASSO['login necessário']}"]
    assert C.montar_cartao("android-01", estado_do_aparelho(inst("android-01"), [persona(sessao())]))[0] == \
        "android-01 · sessão pronta"
    pausa = estado_do_aparelho(inst("android-01", repair_pause={"since": DESDE}), [persona(sessao())])
    assert C.montar_cartao("android-01", pausa)[0] == "android-01 · em pausa de reparo"
    assert "Desde: não informado" in C.montar_cartao("android-01", C.Estado(ERRO, "conta travada"))[1]


def test_texto_passa_pelo_filtro_do_trello(monkeypatch):
    visto: list[str] = []
    monkeypatch.setattr(C, "redigir", lambda t: (visto.append(t), t.replace("android-01", "XX-01"))[1])
    nome, desc = C.montar_cartao("android-01", C.Estado(PRONTA, "sessão verificada"))
    assert len(visto) == 2 and "XX-01" in nome and "XX-01" in desc
    monkeypatch.undo()
    # e o filtro de contato (e-mail, telefone, link) vem antes
    assert C._limpo(f"fale com {EMAIL} ou 11 91234-5678 em https://x.invalid/a") == \
        "fale com [e-mail] ou [telefone] em [link]"
    assert "[ip]" in C._limpo(f"host {IP}")


# ------------------------------------------------------------------------------------------ criação e idempotência
def test_criacao_cada_aparelho_com_conta_na_lista_certa_e_o_sem_conta_ignorado():
    t = TrelloFalso()
    plano, falhas, saida = rodar(t, PARQUE, PERSONAS)
    assert not falhas and C.contar(plano) == {"criar": 3, "atualizar": 0, "concluir": 0}
    por_rotulo = {C._LINHA_APARELHO.search(c.desc).group(1): c for c in t.cartoes.values()}
    assert set(por_rotulo) == {"android-01", "android-02", "android-04"}      # o android-03 não tem conta real
    assert por_rotulo["android-01"].lista == LISTA_EM_EXECUCAO
    assert por_rotulo["android-02"].lista == LISTA_EM_VALIDACAO
    assert por_rotulo["android-04"].lista == LISTA_EM_VALIDACAO
    assert por_rotulo["android-04"].nome == "android-04 · sessão vencida"
    assert saida[-1] == "aparelhos 4; com conta real 3; criados 3; atualizados 0; concluídos 0; duplicados 0; falhas nenhuma"


def test_segunda_execucao_tem_zero_acoes():
    t = TrelloFalso()
    rodar(t, PARQUE, PERSONAS)
    depois = len(t.chamadas)
    plano, falhas, saida = rodar(t, PARQUE, PERSONAS, agora=AGORA + timedelta(hours=5))    # a hora passa, o texto não muda
    assert plano.acoes == [] and not falhas and len(t.chamadas) == depois and len(t.cartoes) == 3
    assert saida == ["aparelhos 4; com conta real 3; criados 0; atualizados 0; concluídos 0; duplicados 0; falhas nenhuma"]


def test_ensaio_nao_grava_e_imprime_as_acoes():
    t = TrelloFalso()
    plano, _, saida = rodar(t, PARQUE, PERSONAS, aplicar=False)
    assert len(plano.acoes) == 3 and t.cartoes == {} and t.chamadas == []
    assert saida[0] == "  criaria: android-01 · sessão pronta → Em execução; há 3 h"
    assert saida[-1].startswith("aparelhos 4; com conta real 3; criar 3; atualizar 0; concluir 0;")


def test_mudanca_de_estado_move_o_cartao_sem_duplicar():
    t = TrelloFalso()
    rodar(t, PARQUE, PERSONAS)
    id_01 = next(c.id for c in t.cartoes.values() if c.nome.startswith("android-01"))
    personas = dict(PERSONAS, **{"android-01": [persona(sessao("auth_challenge", desde="2026-10-06T14:30:00Z"))]})
    plano, _, saida = rodar(t, PARQUE, personas)
    assert [(a.tipo, a.rotulo, a.muda) for a in plano.acoes] == [("atualizar", "android-01", ("nome", "descrição", "lista"))]
    c = t.cartoes[id_01]
    assert (c.lista, c.nome) == (LISTA_EM_VALIDACAO, "android-01 · sessão com erro") and len(t.cartoes) == 3
    assert "Motivo: desafio do app" in c.desc and "Desde: 2026-10-06 14:30Z" in c.desc
    assert "→ Em validação (nome, descrição, lista)" in saida[0]
    # e a volta: pronta de novo → Em execução
    plano, _, _ = rodar(t, PARQUE, PERSONAS)
    assert t.cartoes[id_01].lista == LISTA_EM_EXECUCAO and len(plano.acoes) == 1 and len(t.cartoes) == 3


def test_so_o_que_mudou_e_gravado():
    t = TrelloFalso()
    rodar(t, PARQUE, PERSONAS)
    # só o "desde" muda (mesma sessão, nova verificação): nome e lista ficam, só a descrição vai
    personas = dict(PERSONAS, **{"android-01": [persona(sessao(desde="2026-10-06T14:45:00Z"))]})
    plano, _, _ = rodar(t, PARQUE, personas)
    assert [a.muda for a in plano.acoes] == [("descrição",)]


def test_aparelho_que_perde_a_conta_real_vai_para_concluido_e_a_segunda_rodada_nao_faz_nada():
    t = TrelloFalso()
    rodar(t, PARQUE, PERSONAS)
    personas = dict(PERSONAS, **{"android-02": []})
    plano, _, _ = rodar(t, PARQUE, personas)
    assert [(a.tipo, a.rotulo) for a in plano.acoes] == [("concluir", "android-02")]
    c = next(c for c in t.cartoes.values() if "Aparelho: android-02" in c.desc)
    assert c.lista == LISTA_CONCLUIDO and c.nome == "android-02 · sem conta real"
    assert rodar(t, PARQUE, personas)[0].acoes == []
    # se a conta volta, o MESMO cartão volta para a lista viva (sem duplicata)
    plano, _, _ = rodar(t, PARQUE, PERSONAS)
    assert [a.tipo for a in plano.acoes] == ["atualizar"] and len(t.cartoes) == 3


def test_aparelho_que_a_central_nao_lista_mais_vai_para_concluido():
    t = TrelloFalso()
    rodar(t, PARQUE, PERSONAS)
    plano, _, _ = rodar(t, PARQUE[:1], {"android-01": PERSONAS["android-01"]})
    assert sorted(a.rotulo for a in plano.acoes) == ["android-02", "android-04"] and {a.tipo for a in plano.acoes} == {"concluir"}
    assert {c.lista for c in t.cartoes.values()} == {LISTA_EM_EXECUCAO, LISTA_CONCLUIDO}


# ------------------------------------------------------------------------------------------ redação e proibições
def test_nada_de_identidade_nos_cartoes_nem_comentario():
    pausa = {"until": "2026-10-06T16:00:00Z", "since": DESDE, "reason": MOTIVO_LIVRE, "by": NOME, "remaining_s": 60}
    parque = [inst("android-01", repair_pause=pausa), inst("android-02", locked_account=HANDLE), *PARQUE[2:]]
    t = TrelloFalso()
    rodar(t, parque, PERSONAS)       # `comentar` e `arquivar_cartao` levantam: sair daqui já prova que não foram chamados
    texto = "\n".join(c.nome + "\n" + c.desc for c in t.cartoes.values())
    assert "android-01 · em pausa de reparo" in texto and "Motivo: conta travada" in texto
    for proibido in PROIBIDOS:
        assert proibido not in texto
    assert "@" not in texto
    assert all(a == "criar" for a, _ in t.chamadas)


def test_rotulo_fora_do_formato_e_pulado_e_contado():
    parque = [inst("android-01"), inst(f"{NOME} 1"), inst("")]
    t = TrelloFalso()
    plano, falhas, _ = rodar(t, parque, {"android-01": PERSONAS["android-01"]})
    assert len(plano.acoes) == 1 and falhas == ["rótulo fora do formato"] * 2
    assert NOME not in "\n".join(c.nome + c.desc for c in t.cartoes.values())


def test_cartao_de_item_do_plano_nunca_casa_com_cartao_de_aparelho():
    plano_item = Cartao("p1", "28.69 · item do plano", "Aparelho: android-01\nmenciona o aparelho", LISTA_EM_EXECUCAO)
    outra_lista = Cartao("p2", "android-01 · algo", "Aparelho: android-01", "lista-de-outro-quadro")
    t = TrelloFalso([plano_item, outra_lista])
    plano, _, _ = rodar(t, [inst("android-01")], {"android-01": PERSONAS["android-01"]})
    assert [a.tipo for a in plano.acoes] == ["criar"]
    assert t.cartoes["p1"] == plano_item and t.cartoes["p2"] == outra_lista


def test_duplicado_nao_e_tocado_nem_ganha_mais_um():
    a = Cartao("d1", "android-01 · velho", "Aparelho: android-01", LISTA_EM_EXECUCAO)
    b = Cartao("d2", "android-01 · outro velho", "Aparelho: android-01\nx", LISTA_EM_VALIDACAO)
    t = TrelloFalso([a, b])
    plano, _, saida = rodar(t, [inst("android-01")], {"android-01": PERSONAS["android-01"]})
    assert plano.acoes == [] and plano.duplicados == ["android-01"] and t.chamadas == []
    assert saida[-1].endswith("duplicados 1; falhas nenhuma")


def test_leitura_que_falhou_pula_o_aparelho_e_nunca_conclui_o_cartao():
    t = TrelloFalso()
    rodar(t, PARQUE, PERSONAS)
    personas = {"android-01": PERSONAS["android-01"], "android-02": None, "android-03": [], "android-04": PERSONAS["android-04"]}
    plano, falhas, saida = rodar(t, PARQUE, personas, falhas=["personas de android-02"])
    assert plano.acoes == [] and falhas == ["personas de android-02"]
    assert saida[-1].endswith("falhas personas de android-02")
    assert next(c for c in t.cartoes.values() if "android-02" in c.nome).lista == LISTA_EM_VALIDACAO


def test_central_sem_aparelho_recusa_em_vez_de_concluir_tudo():
    t = TrelloFalso()
    rodar(t, PARQUE, PERSONAS)
    with pytest.raises(FalhaDeLeitura):
        rodar(t, [], {})
    assert len(t.cartoes) == 3 and all(c.lista != LISTA_CONCLUIDO for c in t.cartoes.values())


def test_aplicar_para_no_primeiro_erro_e_a_repeticao_faz_so_o_resto():
    class Cai(TrelloFalso):
        async def criar_cartao(self, lista, nome, desc):  # noqa: ANN001
            if len(self.cartoes) == 1:
                raise RuntimeError(f"401 token=segredo123 em https://api.invalid/{HANDLE}")
            return await super().criar_cartao(lista, nome, desc)

    t = Cai()
    plano, falhas, _ = rodar(t, PARQUE, PERSONAS)
    assert len(t.cartoes) == 1 and falhas == ["criar de android-02: RuntimeError"]
    assert "segredo123" not in "".join(falhas) and HANDLE not in "".join(falhas)
    t.__class__ = TrelloFalso                  # a rede voltou
    plano, falhas, _ = rodar(t, PARQUE, PERSONAS)
    assert C.contar(plano)["criar"] == 2 and not falhas and len(t.cartoes) == 3


# ------------------------------------------------------------------------------------------ leitura
def test_ler_da_central_le_a_lista_e_as_personas_de_cada_aparelho():
    pedidos: list[str] = []

    def obter(url: str):
        pedidos.append(url)
        if url.endswith("/api/instances"):
            return PARQUE[:2]
        if url.endswith("/android-02/personas"):
            raise FalhaDeLeitura("URLError")
        return [persona(sessao())]

    instancias, personas, falhas = C.ler_da_central("http://central.invalid/", obter)
    assert [i["id"] for i in instancias] == ["android-01", "android-02"]
    assert personas["android-01"] and personas["android-02"] is None and falhas == ["personas de android-02"]
    assert pedidos[0] == "http://central.invalid/api/instances"
    estados, _ = C.estados_dos_aparelhos(instancias, personas)
    assert estados["android-01"].estado == PRONTA and estados["android-02"] is None
    with pytest.raises(FalhaDeLeitura):
        C.ler_da_central("http://central.invalid", lambda url: {"nao": "lista"})


def test_ler_do_arquivo_aceita_lista_com_personas_dentro_ou_dicionario(tmp_path):
    dentro = tmp_path / "a.json"
    dentro.write_text(json.dumps([{**PARQUE[0], "personas": [persona(sessao())]}, PARQUE[2]]), encoding="utf-8")
    inst1, pers1, _ = C.ler_do_arquivo(dentro)
    assert len(inst1) == 2 and pers1["android-01"] and pers1["android-03"] == []
    fora = tmp_path / "b.json"
    fora.write_text(json.dumps({"instances": PARQUE[:2], "personas": {"android-02": [persona(sessao("needs_person"))]}}),
                    encoding="utf-8")
    _, pers2, _ = C.ler_do_arquivo(fora)
    assert pers2["android-01"] == [] and pers2["android-02"][0]["session"]["status"] == "needs_person"
    with pytest.raises(FalhaDeLeitura):
        C.ler_do_arquivo(tmp_path / "nao-existe.json")
    ruim = tmp_path / "c.json"
    ruim.write_text("{}", encoding="utf-8")
    with pytest.raises(FalhaDeLeitura):
        C.ler_do_arquivo(ruim)


def test_comando_em_ensaio_com_arquivo_nao_usa_trello_nem_rede(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C.redacao, "recarregar", lambda raiz=None: True)
    monkeypatch.setattr(C, "_novo_cliente", lambda: pytest.fail("o ensaio com --arquivo não toca o Trello"))
    arq = tmp_path / "instancias.json"
    arq.write_text(json.dumps({"instances": PARQUE, "personas": {k: v for k, v in PERSONAS.items()}}), encoding="utf-8")
    assert C.main(["--arquivo", str(arq)]) == 0
    linhas = capsys.readouterr().out.splitlines()
    assert linhas[-1].startswith("aparelhos 4; com conta real 3; criar 3;") and len(linhas) == 4
    for proibido in PROIBIDOS:
        assert proibido not in "\n".join(linhas)


def test_aplicar_se_recusa_sem_os_nomes_a_esconder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C.redacao, "recarregar", lambda raiz=None: False)
    monkeypatch.setattr(C, "_novo_cliente", lambda: pytest.fail("não pode chegar ao Trello"))
    arq = tmp_path / "i.json"
    arq.write_text(json.dumps(PARQUE), encoding="utf-8")
    assert C.main(["--arquivo", str(arq), "--aplicar"]) == 1
    assert "nada foi gravado" in capsys.readouterr().out
