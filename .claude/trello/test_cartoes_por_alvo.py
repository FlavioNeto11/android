"""28.76: um cartão por alvo da operação da prova. Dados FICTÍCIOS (nomes, handles, e-mail e IP inventados), central e Trello FALSOS.

Prova `simulated`: nenhum teste fala com a central real nem com o Trello; a operação é um JSON fabricado no formato de
`ServicoDeOperacoes.ler`, o cliente do Trello é um fake em memória e `comentar` falha o teste se for chamado. O modo
`--aplicar` e o laço reais ficam `not_run` até a rodada de 07/10 (10:00Z).
Rodar: backend/.venv/Scripts/python.exe -m pytest -q .claude/trello/test_cartoes_por_alvo.py
"""
from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import cartoes_de_aparelho as AP  # noqa: E402
import cartoes_por_alvo as C  # noqa: E402
from cartoes_de_aparelho import (  # noqa: E402
    LISTA_CONCLUIDO,
    LISTA_EM_EXECUCAO,
    LISTA_EM_VALIDACAO,
    Cartao,
    FalhaDeLeitura,
)

OP = "op-20261007100000-abc123"
OP2 = "op-20261007110000-def456"
#: identidade fictícia, fora da lista de reserva da redação: só o código que NUNCA lê estes campos os segura
NOME = "Zoraide Benevides"
HANDLE = "zoraide.benevides.ig"
EMAIL = "zoraide.benevides@exemplo.invalid"
IP = "10.77.3.9"
SERIAL = "emulator-5654"
TEXTO = "Comentario secreto sobre a Zoraide: ligue para 11 98888-7777"
ASSUNTO = "assunto-reservado-da-operacao"
FONTE = "https://fonte-reservada.exemplo.invalid/materia"
MOTIVO_LIVRE = f"o app mostrou a tela da {NOME} em {IP} e pediu {EMAIL}"
PROIBIDOS = (NOME, HANDLE, EMAIL, IP, SERIAL, "Benevides", "Zoraide", "Comentario secreto", "98888", ASSUNTO, FONTE,
             "profile-zr", "acct-zr", "run-zr", "comando reservado", "experimento")


def est(*pares: tuple[str, str]) -> list[dict]:
    return [{"estagio": e, "em": h} for e, h in pares]


def alvo(estagio: str, estado: str, *, motivo: str | None = None, parou_em: str | None = None, estagios: list | None = None,
         acao: dict | None = None, instance: str | None = "android-03", n: int = 1) -> dict:
    """Um alvo no formato de `ServicoDeOperacoes.ler`, com TODOS os campos de identidade preenchidos (e nunca lidos)."""
    return {"profile_id": f"profile-zr-{n}", "persona_nome": NOME, "app_id": "instagram", "account_id": f"acct-zr-{n}",
            "conta": f"@{HANDLE}", "instance_id": instance, "run_id": f"run-zr-{n}", "estagio": estagio, "estado": estado,
            "motivo": motivo, "parou_em": parou_em,
            "estagios": estagios if estagios is not None else est(("persona", "2026-10-07T10:00:00Z")),
            "resultado": None if acao is None else {"texto": TEXTO, "conhecimento_ids": [], "evidencia_id": "ev-1",
                                                    "custo_usd": 0.05, "acao_final": acao},
            "custo_usd": 0.05}


def operacao(alvos: list[dict], *, op_id: str = OP, status: str = "em_curso", finished: str | None = None,
             acao_final: str = "executar") -> dict:
    return {"id": op_id, "command": "comando reservado", "app_id": "instagram", "acao_final": acao_final, "max_usd": 2.0,
            "assunto": ASSUNTO, "fontes": [FONTE], "parametros": {"x": HANDLE}, "status": status,
            "created_at": "2026-10-07T10:00:00Z", "finished_at": finished,
            "capacidade": {"solicitados": len(alvos)}, "alvos": alvos, "custo": {"total_usd": 0.1}}


def alvo_andando(n: int = 1) -> dict:
    return alvo("resposta_gerada", "em_curso", n=n, estagios=est(("persona", "2026-10-07T10:00:00Z"),
                                                                 ("resposta_gerada", "2026-10-07T10:04:30Z")))


def alvo_parado(n: int = 2) -> dict:
    return alvo("conta", "bloqueado", motivo="sem sessão", parou_em="sessao", n=n, instance=None,
                estagios=est(("persona", "2026-10-07T10:00:00Z"), ("conta", "2026-10-07T10:00:00Z")))


def alvo_concluido(n: int = 3) -> dict:
    return alvo("resultado_verificado", "concluido", n=n, acao={"tipo": "comment_post", "verificada": True, "evidencia_id": "e"},
                estagios=est(("persona", "2026-10-07T10:00:00Z"), ("acao_executada", "2026-10-07T10:09:00Z"),
                             ("resultado_verificado", "2026-10-07T10:09:30Z")))


def alvo_preparado(n: int = 4) -> dict:
    return alvo("acao_preparada", "concluido", n=n, acao={"tipo": "comment_post", "verificada": False, "evidencia_id": None},
                estagios=est(("persona", "2026-10-07T10:00:00Z"), ("acao_preparada", "2026-10-07T10:06:00Z")))


def op4() -> dict:
    return operacao([alvo_andando(1), alvo_parado(2), alvo_concluido(3), alvo_preparado(4)])


class TrelloFalso:
    """O quadro Execução em memória. `comentar` e `arquivar_cartao` falham o teste: o espelho só escreve nome, descrição e lista."""

    def __init__(self, cartoes: list[Cartao] | None = None, *, falha_em: str | None = None) -> None:
        self.cartoes = {c.id: c for c in (cartoes or [])}
        self.chamadas: list[tuple] = []
        self.falha_em = falha_em
        self._n = 0

    async def _pedir(self, metodo, caminho, *, params=None, corpo=None):  # noqa: ANN001
        assert metodo == "GET" and caminho.endswith("/cards")
        return [{"id": c.id, "name": c.nome, "desc": c.desc, "idList": c.lista} for c in self.cartoes.values()]

    async def criar_cartao(self, lista, nome, desc):  # noqa: ANN001
        if self.falha_em == "criar":
            raise RuntimeError(f"falha com {NOME}")
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
        raise AssertionError("o espelho por alvo NUNCA comenta em cartão")

    arquivar_cartao = comentar


def ciclo(trello: TrelloFalso, ops: list[dict], *, aplicar: bool = True) -> tuple[C.Ciclo, list[str]]:
    saida: list[str] = []
    por_id = {o["id"]: o for o in ops}
    escrever = (lambda plano: asyncio.run(AP.aplicar(trello, plano))) if aplicar else None  # noqa: E731
    c = C.rodar_ciclo(list(por_id), lambda ref: copy.deepcopy(por_id[ref]), lambda: asyncio.run(AP.ler_cartoes(trello)),
                      not aplicar, escrever, saida.append)
    return c, saida


def por_chave(t: TrelloFalso) -> dict[str, Cartao]:
    return {C._LINHA_ALVO.search(c.desc).group(1): c for c in t.cartoes.values() if C._LINHA_ALVO.search(c.desc)}


# ------------------------------------------------------------------------------------------ criação e listas
def test_um_cartao_por_alvo_na_lista_certa_com_nome_e_chave():
    t = TrelloFalso()
    c, _ = ciclo(t, [op4()])
    cs = por_chave(t)
    assert sorted(cs) == [f"{OP}/P01", f"{OP}/P02", f"{OP}/P03", f"{OP}/P04"]
    assert c.plano.duplicados == [] and len(t.cartoes) == 4 and all(a == "criar" for a, _ in t.chamadas)
    assert cs[f"{OP}/P01"].lista == LISTA_EM_EXECUCAO
    assert cs[f"{OP}/P02"].lista == LISTA_EM_VALIDACAO          # parou com motivo
    assert cs[f"{OP}/P03"].lista == LISTA_CONCLUIDO             # ação verificada
    assert cs[f"{OP}/P04"].lista == LISTA_EM_VALIDACAO          # só preparou: espera liberação
    assert cs[f"{OP}/P01"].nome == "Prova 07/10 · P01 · resposta gerada (op-abc123)"
    assert cs[f"{OP}/P02"].nome == "Prova 07/10 · P02 · parou em sessão (op-abc123)"
    assert cs[f"{OP}/P03"].nome == "Prova 07/10 · P03 · resultado verificado (op-abc123)"


def test_descricao_tem_chave_estagio_hora_utc_motivo_acao_e_aparelho():
    t = TrelloFalso()
    ciclo(t, [op4()])
    cs = por_chave(t)
    d1, d2, d3, d4 = (cs[f"{OP}/P0{i}"].desc for i in (1, 2, 3, 4))
    assert d1.startswith(f"Alvo da operação: {OP}/P01\nSituação: em execução\nEstágio atual: resposta gerada\n"
                         "Última mudança: 2026-10-07 10:04Z\n")
    assert "Aparelho usado: android-03" in d1 and "Ação final: nenhuma" in d1 and "Ação verificada: sem ação" in d1
    assert "Motivo:" not in d1                                   # só quando parou
    assert "Motivo: sem sessão válida" in d2 and "Parou em: sessão" in d2 and "Próximo passo: conectar a conta" in d2
    assert "Aparelho usado:" not in d2                           # sem aparelho: a linha some
    assert "Situação: concluído" in d3 and "Ação final: comment_post" in d3 and "Ação verificada: sim" in d3
    assert "Motivo: ação preparada, sem executar" in d4 and "Ação verificada: não" in d4


def test_acao_verificada_sim_nao_nao_conferida_e_sem_acao():
    executada = alvo("acao_executada", "em_curso", acao={"tipo": "comment_post", "verificada": False},
                     estagios=est(("persona", "2026-10-07T10:00:00Z"), ("acao_executada", "2026-10-07T10:09:00Z")))
    bloqueada = alvo("acao_bloqueada", "bloqueado", motivo="a ação final terminou failed", parou_em="acao_bloqueada",
                     acao={"tipo": "comment_post", "verificada": False},
                     estagios=est(("persona", "2026-10-07T10:00:00Z"), ("acao_bloqueada", "2026-10-07T10:09:00Z")))
    assert C._acao(alvo_concluido()) == ("comment_post", "sim")
    assert C._acao(executada) == ("comment_post", "não conferida")
    assert C._acao(bloqueada) == ("comment_post", "não")
    assert C._acao(alvo_preparado()) == ("comment_post", "não")
    assert C._acao(alvo_andando()) == (None, "sem ação")
    nome, desc, _ = C.montar_cartao(OP, 1, bloqueada)
    assert "ação bloqueada" in nome and "Motivo: a ação final não concluiu" in desc


def test_tipo_da_acao_fora_do_formato_vira_outra():
    a = alvo_concluido()
    a["resultado"]["acao_final"]["tipo"] = f"Comentar como {NOME}"
    _, desc, _ = C.montar_cartao(OP, 1, a)
    assert "Ação final: outra" in desc and "Zoraide" not in desc


# ------------------------------------------------------------------------------------------ idempotência e mudança
def test_segundo_ciclo_sem_mudanca_tem_zero_acoes():
    t = TrelloFalso()
    ciclo(t, [op4()])
    c, saida = ciclo(t, [op4()])
    assert c.plano.acoes == [] and len(t.cartoes) == 4 and len(t.chamadas) == 4
    assert saida[-1] == "operações 1 (encerradas 0); alvos 4; criados 0; atualizados 0; movidos 0; duplicados 0; falhas nenhuma"


def test_a_hora_da_descricao_nao_e_a_de_agora(monkeypatch):
    t = TrelloFalso()
    ciclo(t, [op4()])
    antes = {k: (c.nome, c.desc) for k, c in por_chave(t).items()}
    monkeypatch.setattr(C, "datetime", type("D", (), {"now": staticmethod(lambda tz=None: pytest.fail("não usa a hora de agora"))}))
    ciclo(t, [op4()])
    assert {k: (c.nome, c.desc) for k, c in por_chave(t).items()} == antes


def test_estagio_que_muda_atualiza_nome_e_descricao_sem_mover_nem_duplicar():
    t = TrelloFalso()
    ciclo(t, [op4()])
    op = op4()
    op["alvos"][0] = alvo("interface_de_comentario_alcancada", "em_curso",
                          estagios=est(("persona", "2026-10-07T10:00:00Z"),
                                       ("interface_de_comentario_alcancada", "2026-10-07T10:07:00Z")))
    c, saida = ciclo(t, [op])
    assert [(a.tipo, a.muda) for a in c.plano.acoes] == [("atualizar", ("nome", "descrição"))]
    novo = por_chave(t)[f"{OP}/P01"]
    assert novo.nome == "Prova 07/10 · P01 · interface de comentário alcançada (op-abc123)"
    assert "Última mudança: 2026-10-07 10:07Z" in novo.desc and novo.lista == LISTA_EM_EXECUCAO and len(t.cartoes) == 4
    assert any("atualizaria" not in linha and "atualizado" in linha and "(nome, descrição)" in linha for linha in saida)
    assert "movidos 0" in saida[-1]


def test_concluido_move_para_concluido_e_a_segunda_rodada_nao_faz_nada():
    t = TrelloFalso()
    ciclo(t, [operacao([alvo_andando(1)])])
    assert por_chave(t)[f"{OP}/P01"].lista == LISTA_EM_EXECUCAO
    concluida = operacao([alvo_concluido(1)], status="concluida", finished="2026-10-07T10:10:00Z")
    c, saida = ciclo(t, [concluida])
    assert [(a.tipo, a.muda) for a in c.plano.acoes] == [("atualizar", ("nome", "descrição", "lista"))]
    assert por_chave(t)[f"{OP}/P01"].lista == LISTA_CONCLUIDO and len(t.cartoes) == 1
    assert "movidos 1" in saida[-1] and "encerradas 1" in saida[-1]
    c2, _ = ciclo(t, [concluida])
    assert c2.plano.acoes == []


def test_parou_com_motivo_move_para_em_validacao_e_voltar_a_andar_move_de_volta():
    t = TrelloFalso()
    ciclo(t, [operacao([alvo_andando(1)])])
    parado = alvo("resposta_gerada", "bloqueado", motivo="aguarda liberação", parou_em="acao_executada", n=1,
                  estagios=est(("persona", "2026-10-07T10:00:00Z"), ("resposta_gerada", "2026-10-07T10:04:30Z")))
    ciclo(t, [operacao([parado])])
    c = por_chave(t)[f"{OP}/P01"]
    assert c.lista == LISTA_EM_VALIDACAO and "Motivo: aguarda liberação do dono" in c.desc
    assert "Próximo passo: liberar pela Operação do painel." in c.desc
    ciclo(t, [operacao([alvo_andando(1)])])
    assert por_chave(t)[f"{OP}/P01"].lista == LISTA_EM_EXECUCAO and len(t.cartoes) == 1


def test_alvo_cancelado_vai_para_em_validacao_com_motivo_fixo():
    cancelado = alvo("sessao", "cancelado", motivo=MOTIVO_LIVRE, parou_em="aparelho")
    nome, desc, situacao = C.montar_cartao(OP, 1, cancelado)
    assert situacao == C.PAROU and "cancelado em aparelho" in nome and "Motivo: cancelado" in desc
    assert "o app mostrou" not in desc


def test_motivo_livre_do_servidor_vira_nao_classificado_e_nunca_vai_ao_cartao():
    livre = alvo("conta", "bloqueado", motivo=MOTIVO_LIVRE, parou_em="sessao")
    nome, desc, _ = C.montar_cartao(OP, 1, livre)
    assert "Motivo: motivo não classificado" in desc and "Próximo passo: olhar a Operação no painel." in desc
    for proibido in (NOME, IP, EMAIL, "o app mostrou"):
        assert proibido not in nome + desc


@pytest.mark.parametrize(("motivo", "esperado"), [
    ("sem conta", "persona sem conta no app"), ("sem sessão", "sem sessão válida"),
    ("aparelho indisponível", "aparelho indisponível"), ("teto de custo", "teto de custo atingido"),
    ("limite de ações executadas", "limite de ações executadas"), ("recusada pela política", "recusada pela política da frota"),
    ("aprovação recusada", "aprovação recusada"), ("waiting_user", "espera uma pessoa"), (None, "motivo não classificado")])
def test_vocabulario_fixo_dos_motivos(motivo, esperado):
    _, desc, _ = C.montar_cartao(OP, 1, alvo("conta", "bloqueado", motivo=motivo, parou_em="sessao"))
    assert f"Motivo: {esperado}" in desc


def test_estagio_desconhecido_e_a_abertura_do_app_nunca_falham():
    assert C._palavras("instagram_aberto") == "app aberto"
    assert C._palavras("novo_estagio_x") == "novo estagio x"
    assert C._palavras(f"@{HANDLE} fez algo") == "estágio não reconhecido" and C._palavras(None) == "estágio não informado"


# ------------------------------------------------------------------------------------------ redação e proibições
def test_nada_de_identidade_nos_cartoes_nem_na_saida_nem_comentario():
    t = TrelloFalso()
    op = operacao([alvo_andando(1), alvo("conta", "bloqueado", motivo=MOTIVO_LIVRE, parou_em="sessao", n=2),
                   alvo_concluido(3), alvo_preparado(4)])
    op["alvos"][1]["instance_id"] = SERIAL          # serial não é rótulo android-NN: a linha do aparelho some
    _, saida = ciclo(t, [op])                        # `comentar` e `arquivar_cartao` levantam se forem chamados
    texto = "\n".join([c.nome + "\n" + c.desc for c in t.cartoes.values()] + saida)
    for proibido in PROIBIDOS:
        assert proibido not in texto
    assert "@" not in texto and "android-03" in texto and SERIAL not in texto
    assert all(a == "criar" for a, _ in t.chamadas)


def test_texto_passa_pelo_filtro_do_trello(monkeypatch):
    monkeypatch.setattr(C, "_limpo", lambda texto: texto.replace("Situação", "[filtrado]"))
    _, desc, _ = C.montar_cartao(OP, 1, alvo_andando())
    assert "[filtrado]: em execução" in desc


def test_a_linha_do_alvo_nao_e_lida_como_cartao_de_aparelho_do_28_69():
    t = TrelloFalso()
    ciclo(t, [operacao([alvo_andando(1)])])
    desc = next(iter(t.cartoes.values())).desc
    assert "Aparelho usado: android-03" in desc and AP._LINHA_APARELHO.search(desc) is None
    achados, duplicados = AP.cartoes_de_aparelho(list(t.cartoes.values()))
    assert achados == {} and duplicados == []


# ------------------------------------------------------------------------------------------ escopo dos cartões
def test_cartao_de_plano_de_outra_operacao_e_de_outra_lista_nunca_sao_tocados():
    plano_item = Cartao("p1", "28.76 · item do plano", f"Alvo da operação: {OP}/P01", LISTA_EM_EXECUCAO)
    outra_op = Cartao("o2", "Prova 07/10 · P01 · app aberto (op-def456)", f"Alvo da operação: {OP2}/P01\nvelho",
                      LISTA_EM_VALIDACAO)
    prova = Cartao("pr", "Prova 07/10 · pai", f"Alvo da operação: {OP}/P01", "lista-prova-07-10")
    t = TrelloFalso([plano_item, outra_op, prova])
    c, _ = ciclo(t, [operacao([alvo_andando(1)])])
    assert [a.tipo for a in c.plano.acoes] == ["criar"]
    assert t.cartoes["p1"] == plano_item and t.cartoes["o2"] == outra_op and t.cartoes["pr"] == prova


def test_chave_com_mais_de_um_cartao_nao_e_tocada_nem_ganha_outro():
    desc = f"Alvo da operação: {OP}/P01\nvelho"
    t = TrelloFalso([Cartao("a", "Prova 07/10 · P01 · x (op-abc123)", desc, LISTA_EM_EXECUCAO),
                     Cartao("b", "Prova 07/10 · P01 · y (op-abc123)", desc, LISTA_EM_VALIDACAO)])
    c, saida = ciclo(t, [operacao([alvo_andando(1), alvo_parado(2)])])
    assert c.plano.duplicados == [f"{OP}/P01"] and [a.rotulo for a in c.plano.acoes] == [f"{OP}/P02"]
    assert len(t.cartoes) == 3 and t.cartoes["a"].desc == desc and "duplicados 1" in saida[-1]


def test_duas_operacoes_no_mesmo_ciclo_tem_cartoes_separados():
    t = TrelloFalso()
    c, saida = ciclo(t, [operacao([alvo_andando(1)]), operacao([alvo_andando(1)], op_id=OP2)])
    assert sorted(por_chave(t)) == [f"{OP}/P01", f"{OP2}/P01"] and "operações 2 (encerradas 0); alvos 2; criados 2" in saida[-1]
    assert por_chave(t)[f"{OP2}/P01"].nome.endswith("(op-def456)")


def test_ensaio_nao_grava_e_imprime_as_acoes():
    t = TrelloFalso()
    c, saida = ciclo(t, [op4()], aplicar=False)
    assert t.cartoes == {} and t.chamadas == [] and len(c.plano.acoes) == 4
    assert saida[0].startswith("  criaria: Prova 07/10 · P01 · resposta gerada (op-abc123) -> Em execução")
    assert saida[-1].startswith("operações 1 (encerradas 0); alvos 4; criar 4; atualizar 0; movidos 0;")


def test_aplicar_para_no_primeiro_erro_e_a_repeticao_faz_so_o_resto():
    t = TrelloFalso(falha_em="criar")
    c, saida = ciclo(t, [op4()])
    assert c.falhas == [f"criar de {OP}/P01: RuntimeError"] and t.cartoes == {}
    assert NOME not in "\n".join(saida) and "falhas criar de" in saida[-1]
    t.falha_em = None
    c2, _ = ciclo(t, [op4()])
    assert c2.falhas == [] and len(t.cartoes) == 4


# ------------------------------------------------------------------------------------------ leitura que falha
def test_leitura_de_uma_operacao_que_falha_pula_so_ela_e_nao_mexe_nos_cartoes_dela():
    t = TrelloFalso()
    ciclo(t, [operacao([alvo_andando(1)])])
    antes = dict(t.cartoes)

    def ler(ref: str) -> dict:
        if ref == OP:
            raise FalhaDeLeitura("URLError")
        return operacao([alvo_andando(1)], op_id=OP2)

    saida: list[str] = []
    c = C.rodar_ciclo([OP, OP2], ler, lambda: asyncio.run(AP.ler_cartoes(t)), False,
                      lambda plano: asyncio.run(AP.aplicar(t, plano)), saida.append)
    assert c.falhas == [f"leitura de {OP}: URLError"] and not c.todas_encerradas
    assert [a.rotulo for a in c.plano.acoes] == [f"{OP2}/P01"] and all(t.cartoes[k] == v for k, v in antes.items())
    assert "falhas leitura de op-20261007100000-abc123: URLError" in saida[-1]


def test_operacao_sem_alvos_ou_de_outro_formato_e_recusada_sem_acoes():
    for ruim in ({"id": OP, "status": "em_curso", "alvos": []}, {"id": OP, "status": "em_curso"}, {"alvos": [{}], "status": "x"},
                 {"id": "qualquer", "status": "x", "alvos": [{}]}, []):
        c = C.rodar_ciclo(["x"], lambda ref, r=ruim: r, lambda: pytest.fail("sem alvo não lê o quadro"), True,
                          imprimir=lambda _: None)
        assert c.plano.acoes == [] and len(c.falhas) == 1


def test_leitura_do_quadro_que_falha_nao_grava_nada():
    def quebrado() -> list[Cartao]:
        raise OSError(f"sem rede para {NOME}")

    saida: list[str] = []
    c = C.rodar_ciclo([OP], lambda ref: op4(), quebrado, False, lambda p: pytest.fail("não grava"), saida.append)
    assert c.falhas == ["leitura do quadro: OSError"] and NOME not in "\n".join(saida)


def test_estado_desconhecido_do_alvo_e_pulado_e_contado_mas_nao_prende_o_laco():
    t = TrelloFalso()
    ruim = alvo("conta", "estado-que-nao-existe")
    op = operacao([alvo_concluido(1), ruim], status="concluida_com_bloqueios", finished="2026-10-07T10:10:00Z")
    c, _ = ciclo(t, [op])
    assert len(t.cartoes) == 1 and c.falhas == [f"alvo P02 de op-abc123: estado não reconhecido"] and c.todas_encerradas


def test_ler_da_central_valida_o_id_e_confere_a_resposta():
    visto: list[str] = []

    def obter(url: str) -> object:
        visto.append(url)
        return operacao([alvo_andando()])

    assert C.ler_da_central(OP, "http://x:1/", obter)["id"] == OP and visto == [f"http://x:1/api/operacoes/{OP}"]
    with pytest.raises(FalhaDeLeitura):
        C.ler_da_central("../etc", obter=obter)
    with pytest.raises(FalhaDeLeitura):
        C.ler_da_central(OP2, obter=obter)                       # a resposta é de outra operação
    with pytest.raises(FalhaDeLeitura):
        C.ler_da_central(OP, obter=lambda url: [])


def test_ler_do_arquivo(tmp_path):
    arq = tmp_path / "op.json"
    arq.write_text(json.dumps(op4()), encoding="utf-8")
    assert C.ler_do_arquivo(str(arq))["id"] == OP
    arq.write_text("[]", encoding="utf-8")
    with pytest.raises(FalhaDeLeitura):
        C.ler_do_arquivo(str(arq))
    with pytest.raises(FalhaDeLeitura):
        C.ler_do_arquivo(str(tmp_path / "nao-existe.json"))


# ------------------------------------------------------------------------------------------ o laço
def _ciclos(resultados: list[C.Ciclo | Exception]):
    fila = list(resultados)
    chamadas: list[int] = []

    def um() -> C.Ciclo:
        chamadas.append(1)
        r = fila.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    return um, chamadas


def _c(enc: int, de: int = 1, falhas: list[str] | None = None) -> C.Ciclo:
    return C.Ciclo(operacoes=de, encerradas=enc, falhas=falhas or [])


def test_laco_so_sai_com_todas_encerradas_e_roda_um_ciclo_a_mais():
    um, chamadas = _ciclos([_c(0), _c(0), _c(1), _c(1)])
    dormiu: list[float] = []
    ultimo = C.laco(um, dormiu.append, 120, lambda _: None)
    assert len(chamadas) == 4 and dormiu == [120, 120, 120] and ultimo.todas_encerradas   # 2 em curso, 1 que encerrou, 1 final


def test_laco_com_duas_operacoes_espera_as_duas():
    um, chamadas = _ciclos([_c(1, 2), _c(2, 2), _c(2, 2)])
    C.laco(um, lambda s: None, 120, lambda _: None)
    assert len(chamadas) == 3


def test_laco_nao_sai_com_falha_de_leitura_e_nao_morre_com_ciclo_que_levanta():
    um, chamadas = _ciclos([_c(1, 1, ["leitura de x: URLError"]), RuntimeError(f"quebrou com {NOME}"), _c(1), _c(1)])
    saida: list[str] = []
    ultimo = C.laco(um, lambda s: None, 120, saida.append)
    assert len(chamadas) == 4 and ultimo.todas_encerradas
    assert "ciclo falhou: RuntimeError" in saida and NOME not in "\n".join(saida)


def test_laco_com_gravacao_que_falhou_nao_conta_como_encerrado():
    um, chamadas = _ciclos([_c(1, 1, ["criar de x: OSError"]), _c(1), _c(1)])
    C.laco(um, lambda s: None, 120, lambda _: None)
    assert len(chamadas) == 3                                   # o 1º não vale: 2º encerra, 3º é a confirmação


def test_intervalo_menor_que_120_e_recusado_antes_de_qualquer_rede(monkeypatch, capsys):
    monkeypatch.setattr(C, "_obter_http", lambda url: pytest.fail("não pode ler a central"))
    monkeypatch.setattr(C, "_novo_cliente", lambda: pytest.fail("não pode chegar ao Trello"))
    monkeypatch.setattr(C.redacao, "recarregar", lambda raiz=None: pytest.fail("nem recarregar nomes"))
    with pytest.raises(SystemExit) as saiu:
        C.main(["--operacao", OP, "--laco", "--intervalo-s", "119"], dormir=lambda s: pytest.fail("não dorme"))
    assert saiu.value.code == 2
    assert "mínimo é 120" in capsys.readouterr().err


def test_laco_nao_vale_com_arquivo_nem_sem_entrada(tmp_path, capsys):
    arq = tmp_path / "o.json"
    arq.write_text(json.dumps(op4()), encoding="utf-8")
    for argv in (["--arquivo", str(arq), "--laco"], [], ["--operacao", OP, "--arquivo", str(arq)]):
        with pytest.raises(SystemExit) as saiu:
            C.main(argv)
        assert saiu.value.code == 2
    capsys.readouterr()


# ------------------------------------------------------------------------------------------ o comando
def test_comando_em_ensaio_com_arquivo_nao_usa_trello_nem_rede(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C.redacao, "recarregar", lambda raiz=None: True)
    monkeypatch.setattr(C, "_novo_cliente", lambda: pytest.fail("o ensaio com --arquivo não toca o Trello"))
    monkeypatch.setattr(C, "_obter_http", lambda url: pytest.fail("o ensaio com --arquivo não toca a central"))
    arq = tmp_path / "operacao.json"
    arq.write_text(json.dumps(op4()), encoding="utf-8")
    assert C.main(["--arquivo", str(arq)]) == 0
    linhas = capsys.readouterr().out.splitlines()
    assert linhas[-1].startswith("operações 1 (encerradas 0); alvos 4; criar 4;") and len(linhas) == 5
    for proibido in PROIBIDOS:
        assert proibido not in "\n".join(linhas)


def test_aplicar_se_recusa_sem_os_nomes_a_esconder(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(C.redacao, "recarregar", lambda raiz=None: False)
    monkeypatch.setattr(C, "_novo_cliente", lambda: pytest.fail("não pode chegar ao Trello"))
    arq = tmp_path / "o.json"
    arq.write_text(json.dumps(op4()), encoding="utf-8")
    assert C.main(["--arquivo", str(arq), "--aplicar"]) == 1
    assert "nada foi gravado" in capsys.readouterr().out


def test_comando_do_laco_ponta_a_ponta_com_central_e_trello_falsos(monkeypatch, capsys):
    """Duas leituras em curso, a terceira encerra, a quarta é a confirmação (0 ações); o Trello só recebe criar/atualizar."""
    t = TrelloFalso()
    monkeypatch.setattr(C.redacao, "recarregar", lambda raiz=None: True)
    monkeypatch.setattr(C, "_novo_cliente", lambda: t)
    leituras = [operacao([alvo_andando(1)]), operacao([alvo_andando(1)]),
                operacao([alvo_concluido(1)], status="concluida", finished="2026-10-07T10:10:00Z")]
    lidas: list[str] = []

    def obter(url: str) -> object:
        lidas.append(url)
        return copy.deepcopy(leituras[min(len(lidas), 3) - 1])

    monkeypatch.setattr(C, "_obter_http", obter)
    dormiu: list[float] = []
    assert C.main(["--operacao", OP, "--laco", "--intervalo-s", "120"], dormir=dormiu.append) == 0
    saida = capsys.readouterr().out
    assert len(lidas) == 4 and dormiu == [120, 120, 120] and set(lidas) == {f"http://127.0.0.1:8000/api/operacoes/{OP}"}
    assert por_chave(t)[f"{OP}/P01"].lista == LISTA_CONCLUIDO and [a for a, _ in t.chamadas] == ["criar", "atualizar"]
    assert saida.strip().splitlines()[-1].endswith("falhas nenhuma") and "criados 0; atualizados 0" in saida.splitlines()[-1]
    for proibido in PROIBIDOS:
        assert proibido not in saida
