"""O resumo diário ao dono para o Telegram.

Prova `simulated`: só a parte pura (`montar`) e as leituras com o Trello, o plano e o Telegram FALSOS; o Git é o de um
repositório temporário. Nada aqui fala com o Trello, o Telegram ou a central reais.
Rodar da raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/canais/test_resumo_diario.py`.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resumo_diario as d  # noqa: E402

#: nomes FICTÍCIOS, fora da lista de reserva da redação: só a estrutura do texto os segura
HANDLE = "zoraide.benevides.ig"
AGORA = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)  # 07:00 de Brasília


@pytest.fixture(autouse=True)
def _sem_rede_da_central(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nenhum teste fala com o central: o GET padrão falha (a leitura do custo vira "não consegui ler")."""
    def fora(_url: str) -> object:
        raise OSError("sem rede nos testes")

    monkeypatch.setattr(d, "_get_json", fora)


def _situacao(**kw: object) -> dict:
    s: dict = {"plano": {"total": 714, "implementados": 680, "parciais": 32, "bloqueados": 2},
               "deploys": [{"n": 57, "hora": "2026-10-06T00:30:00Z"}],
               "perguntas": {"n": 3, "ids": ["P-026", "P-027", "P-030"]},
               "movidos": {"Concluído": 5, "Em validação": 4}}
    s.update(kw)
    return s


def _sem_tags(html_: str) -> str:
    return re.sub(r"<[^>]+>", "", html_)


def test_o_resumo_tem_o_molde_dos_avisos_e_cabe_em_12_linhas() -> None:
    texto = d.montar(_situacao(), AGORA)
    linhas = texto.splitlines()
    assert len(linhas) <= 12 and len(texto) < d.LIMITE
    assert linhas[0] == "<b>Resumo diário da Central</b> (06/10, 07:00 de Brasília)"
    puro = _sem_tags(texto)
    assert "Plano: 714 itens, 680 implementados, 32 parciais, 2 bloqueados." in puro
    assert "Deploys em 24 h: 1 (57 às 21:30)." in puro  # 00:30Z = 21:30 de Brasília do dia anterior
    assert "Perguntas abertas: 3 (P-026, P-027, P-030)." in puro
    assert "Cartões movidos em 24 h: 9 (5 para Concluído; 4 para Em validação)." in puro
    assert "Crítico: 2 itens bloqueados no plano." in puro
    assert "Espera você: 3 perguntas no Trello (P-026, P-027, P-030)." in puro
    assert texto.count("<b>") == texto.count("</b>")


def test_sem_pendencias_diz_que_nada_espera_o_dono() -> None:
    texto = d.montar(_situacao(perguntas={"n": 0, "ids": []}, deploys=[], movidos={},
                               plano={"total": 10, "implementados": 10, "parciais": 0, "bloqueados": 0}), AGORA)
    puro = _sem_tags(texto)
    assert "Perguntas abertas: nenhuma." in puro
    assert "Deploys em 24 h: nenhum." in puro
    assert "Cartões movidos em 24 h: nenhum." in puro
    assert "Crítico: nada." in puro
    assert "Espera você: nada." in puro


def test_uma_pergunta_so_fica_no_singular() -> None:
    puro = _sem_tags(d.montar(_situacao(perguntas={"n": 1, "ids": ["P-009"]}), AGORA))
    assert "Espera você: 1 pergunta no Trello (P-009)." in puro


def test_leitura_ausente_diz_nao_consegui_ler_e_nunca_zero_nem_nada() -> None:
    texto = d.montar({"plano": None, "deploys": None, "perguntas": None, "movidos": None}, AGORA)
    puro = _sem_tags(texto)
    assert "Plano: não consegui ler o estado do plano." in puro
    assert "Deploys em 24 h: não consegui ler o CHANGELOG." in puro
    assert "Perguntas abertas: não consegui ler o Trello." in puro
    assert "Cartões movidos em 24 h: não consegui ler o Trello." in puro
    assert "Crítico: sem leitura de plano, CHANGELOG, Trello." in puro
    assert "Espera você: não consegui ler as perguntas do Trello." in puro
    assert "Espera você: nada" not in puro and "nenhum" not in puro
    assert d.montar({}, AGORA).count("não consegui ler") >= 5  # chave ausente conta como leitura que falhou


def test_so_o_movimento_falhar_nao_apaga_as_perguntas_lidas() -> None:
    puro = _sem_tags(d.montar(_situacao(movidos=None), AGORA))
    assert "Perguntas abertas: 3" in puro
    assert "Cartões movidos em 24 h: não consegui ler o Trello." in puro
    assert "sem leitura de Trello" in puro


def test_redige_handle_email_e_telefone_que_vazem_pelo_nome_da_lista() -> None:
    sit = _situacao(movidos={f"Aguardando @{HANDLE} a@exemplo.com 11 98765-4321 https://exemplo.com/x": 2})
    puro = _sem_tags(d.montar(sit, AGORA))
    for proibido in (HANDLE, "a@exemplo.com", "98765-4321", "exemplo.com/x"):
        assert proibido not in puro
    assert "<" not in re.sub(r"</?b>", "", d.montar(sit, AGORA))


def test_ids_de_pergunta_fora_do_formato_nao_entram() -> None:
    puro = _sem_tags(d.montar(_situacao(perguntas={"n": 2, "ids": ["P-026", f"@{HANDLE} disse algo"]}), AGORA))
    assert "(P-026)" in puro and HANDLE not in puro


def test_muitas_perguntas_e_listas_ficam_curtas() -> None:
    ids = [f"P-{n:03d}" for n in range(1, 21)]
    movidos = {f"Lista {n}": n for n in range(1, 9)}
    texto = d.montar(_situacao(perguntas={"n": 20, "ids": ids}, movidos=movidos), AGORA)
    puro = _sem_tags(texto)
    assert "e mais 12" in puro and "P-009" not in puro
    assert "para outras listas" in puro
    assert len(texto.splitlines()) <= 12


def test_situacao_que_nao_e_objeto_e_recusada() -> None:
    with pytest.raises(d.Recusa):
        d.montar([], AGORA)  # type: ignore[arg-type]


# ------------------------------------------------------------------------- 28.72: linha da coerência dos quadros
def _coerencia(**achados: int) -> dict:
    chaves = ("duplicados", "sem_cartao", "pergunta_incoerente", "aparelho_incoerente", "concluido_fora", "sem_lista")
    v = {k: {"n": achados.get(k, 0), "exemplos": []} for k in chaves}
    return {"ok": True, "quadros": {"execucao": 1, "programa": 1, "historico": 1}, "verificacoes": v, "nao_lidas": [],
            "total": sum(achados.values())}


def test_linha_da_coerencia_sem_achado_diz_nada_fora_do_lugar() -> None:
    texto = d.montar(_situacao(coerencia=_coerencia()), AGORA)
    assert "• <b>Coerência dos quadros:</b> nada fora do lugar." in texto
    assert len(texto.splitlines()) <= d.MAX_LINHAS and texto.count("<b>") == texto.count("</b>")
    assert "Crítico: 2 itens bloqueados no plano." in _sem_tags(texto)  # a coerência não entra em Crítico


def test_linha_da_coerencia_com_achados_conta_por_verificacao() -> None:
    puro = _sem_tags(d.montar(_situacao(coerencia=_coerencia(duplicados=2, sem_cartao=1)), AGORA))
    assert "Coerência dos quadros: 3 achados (duplicados 2, sem cartão 1)." in puro


def test_coerencia_que_falhou_ou_sem_o_modulo_diz_nao_consegui_ler_e_o_resto_segue(monkeypatch: pytest.MonkeyPatch) -> None:
    com_none = d.montar(_situacao(coerencia=None), AGORA)
    assert "Coerência dos quadros:</b> não consegui ler o Trello." in com_none
    assert "Plano: 714 itens" in _sem_tags(com_none) and "Crítico: 2 itens bloqueados no plano." in _sem_tags(com_none)
    monkeypatch.setattr(d, "secao_do_resumo", None)  # o import protegido falhou
    sem_modulo = d.montar(_situacao(coerencia=_coerencia()), AGORA)
    assert "Coerência dos quadros:</b> não consegui ler o Trello." in sem_modulo and "nada fora do lugar" not in sem_modulo
    monkeypatch.setattr(d, "secao_do_resumo", lambda _r: 1 / 0)  # a função quebra: o resumo sai igual
    assert "não consegui ler" in d.montar(_situacao(coerencia=_coerencia()), AGORA)


def test_situacao_sem_a_chave_coerencia_sai_como_antes() -> None:
    texto = d.montar(_situacao(), AGORA)
    assert "Coerência" not in texto and len(texto.splitlines()) == 7


def test_ler_coerencia_nao_levanta_e_devolve_none_quando_a_leitura_falha(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert d.ler_coerencia(None, tmp_path, AGORA) is None
    assert d.ler_coerencia(object(), tmp_path, AGORA) is None  # cliente sem `_pedir`: a auditoria devolve ok=False

    async def quebra(*_a: object, **_k: object) -> dict:
        raise RuntimeError("fora")

    monkeypatch.setattr(d, "auditar_tudo", quebra)
    assert d.ler_coerencia(object(), tmp_path, AGORA) is None
    monkeypatch.setattr(d, "auditar_tudo", None)
    assert d.ler_coerencia(object(), tmp_path, AGORA) is None


def test_ler_coerencia_devolve_o_resultado_quando_ok(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def ok(_cl: object, _raiz: Path, _agora: datetime) -> dict:
        return _coerencia(sem_lista=1)

    monkeypatch.setattr(d, "auditar_tudo", ok)
    assert d.ler_coerencia(object(), tmp_path, AGORA)["total"] == 1


# -------------------------------------------------------------------------------------------- leituras (fakes)
class FakeTrello:
    """Só os dois métodos que o resumo usa; nenhuma rede."""

    def __init__(self, cartoes: list | Exception, acoes: dict[str, list | Exception] | None = None) -> None:
        self.cartoes, self.acoes, self.chamadas = cartoes, acoes or {}, []

    async def cartoes_da_lista(self, lista: str) -> list:
        self.chamadas.append(("cartoes", lista))
        if isinstance(self.cartoes, Exception):
            raise self.cartoes
        return self.cartoes

    async def acoes_do_quadro(self, quadro: str, desde: str | None = None, filtro: str = "", limite: int = 0) -> list:
        self.chamadas.append(("acoes", quadro, desde, filtro))
        r = self.acoes.get(quadro, [])
        if isinstance(r, Exception):
            raise r
        return r


def _acao(cartao: str, para: str, quando: datetime, de: str = "lista-a", id_para: str = "lista-b") -> dict:
    return {"id": f"act-{cartao}-{quando:%H%M}", "date": quando.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "data": {"card": {"id": cartao, "name": "75.1 título que não pode sair"},
                     "listBefore": {"id": de, "name": "Antes"}, "listAfter": {"id": id_para, "name": para}}}


def test_perguntas_lidas_da_lista_certa_so_com_contagem_e_ids() -> None:
    fake = FakeTrello([{"id": "1", "name": "P-026 posso trocar @x.y por outro?", "desc": "descrição secreta"},
                       {"id": "2", "name": "Sem número nenhum", "desc": ""},
                       {"id": "3", "name": "P-027 outra"}, {"id": "4", "name": "repete P-026"}])
    q = asyncio.run(d.ler_perguntas(fake))
    assert q == {"n": 3, "ids": ["P-026", "P-027"]}  # o cartão sem P-NNN (instruções fixas da lista) não é pergunta
    assert fake.chamadas == [("cartoes", d.LISTA_PERGUNTAS)]


def test_trello_falhando_nas_perguntas_vira_none_e_o_texto_diz_nao_consegui() -> None:
    q = asyncio.run(d.ler_perguntas(FakeTrello(RuntimeError("Trello recusou (401)"))))
    assert q is None
    puro = _sem_tags(d.montar(_situacao(perguntas=q), AGORA))
    assert "não consegui ler" in puro and "Espera você: nada" not in puro


def test_movidos_contam_o_ultimo_movimento_por_cartao_na_janela_de_24h() -> None:
    h = lambda horas: AGORA - timedelta(hours=horas)  # noqa: E731
    exec_ = [_acao("c1", "Concluído", h(1)), _acao("c1", "Em validação", h(5)),  # c1: vale o mais novo
             _acao("c2", "Concluído", h(2)),
             _acao("c3", "Concluído", h(30)),  # fora da janela
             _acao("c4", "Mesma", h(3), de="x", id_para="x"),  # voltou para a mesma lista: não é movimento
             {"id": "act-comentario", "date": h(1).isoformat(), "data": {"card": {"id": "c9"}}}]  # sem listAfter
    fake = FakeTrello([], {d.QUADROS[0]: exec_, d.QUADROS[1]: [_acao("c5", "Próximas", h(2))],
                           d.QUADROS[2]: [_acao("c6", "Concluído", h(23))]})
    assert asyncio.run(d.ler_movidos(fake, AGORA)) == {"Concluído": 3, "Próximas": 1}
    chamadas = [c for c in fake.chamadas if c[0] == "acoes"]
    assert [c[1] for c in chamadas] == list(d.QUADROS)
    assert all(c[2] == "2026-10-05T10:00:00Z" and c[3] == "updateCard:idList" for c in chamadas)


def test_um_quadro_que_falha_invalida_a_contagem_inteira() -> None:
    fake = FakeTrello([], {d.QUADROS[0]: [_acao("c1", "Concluído", AGORA)], d.QUADROS[1]: RuntimeError("rede")})
    assert asyncio.run(d.ler_movidos(fake, AGORA)) is None


def test_ler_situacao_junta_as_quatro_leituras_e_so_uma_falha_nao_derruba_as_outras(tmp_path: Path,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "ler_plano", lambda raiz: {"total": 5, "implementados": 4, "parciais": 1, "bloqueados": 0})
    monkeypatch.setattr(d, "deploys_do_dia", lambda agora, raiz: [])
    fake = FakeTrello([{"id": "1", "name": "P-001 x"}], {q: RuntimeError("fora") for q in d.QUADROS})
    s = d.ler_situacao(AGORA, tmp_path, cliente=fake)
    assert s["perguntas"] == {"n": 1, "ids": ["P-001"]} and s["movidos"] is None
    assert s["plano"]["total"] == 5 and s["deploys"] == []


def test_ler_situacao_sem_credencial_do_trello_marca_as_duas_leituras_como_falhas(tmp_path: Path,
                                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "ler_plano", lambda raiz: None)
    monkeypatch.setattr(d, "deploys_do_dia", lambda agora, raiz: None)

    def sem_chave() -> None:
        raise ValueError("TRELLO_API_KEY e TRELLO_TOKEN são obrigatórios")

    monkeypatch.setattr(d, "_novo_cliente", sem_chave)
    s = d.ler_situacao(AGORA, tmp_path)
    assert s == {"plano": None, "deploys": None, "perguntas": None, "movidos": None, "coerencia": None, "custo": None}


def test_plano_conta_pelo_estado_e_pelo_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".claude" / "plano-100").mkdir(parents=True)
    estado = {"itens": {"1.1": {"status": "implemented"}, "1.2": {"status": "partial"}, "1.3": {"status": "blocked"},
                        "1.4": {"status": "blocked"}}}
    (tmp_path / ".claude" / "plano-100" / "estado.json").write_text(json.dumps(estado), encoding="utf-8")

    def check(*_a: object, **_k: object) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess([], 0, stdout="x\n7 itens no plano\nImplementados: 1\n".encode(), stderr=b"")

    monkeypatch.setattr(d.subprocess, "run", check)
    assert d.ler_plano(tmp_path) == {"total": 7, "implementados": 1, "parciais": 1, "bloqueados": 2}


def test_plano_que_o_check_nao_responde_e_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        [], 1, stdout="Interrompido: Pacotes ausentes".encode(), stderr=b""))
    assert d.ler_plano(tmp_path) is None


def _git(raiz: Path, quando: datetime, *args: str) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": quando.isoformat(), "GIT_COMMITTER_DATE": quando.isoformat()}
    subprocess.run(["git", "-C", str(raiz), "-c", "user.name=teste", "-c", "user.email=teste@exemplo.invalid", "-c",
                    "commit.gpgsign=false", *args], check=True, capture_output=True, env=env)


def test_deploys_do_dia_pela_hora_do_commit_do_changelog(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    cl = tmp_path / "CHANGELOG.md"
    cl.write_text("# Changelog\n\n## 2026-10-05 — Deploy 55 (velho)\ntexto\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    _git(tmp_path, AGORA - timedelta(hours=40), "commit", "-qm", "deploy 55")
    cl.write_text("# Changelog\n\n## 2026-10-06 — Deploy 56 (novo)\ntexto\n\n## 2026-10-05 — Deploy 55 (velho)\ntexto\n",
                  encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True, capture_output=True)
    _git(tmp_path, AGORA - timedelta(hours=3), "commit", "-qm", "deploy 56")
    achados = d.deploys_do_dia(AGORA, tmp_path)
    assert achados == [{"n": 56, "hora": "2026-10-06T07:00:00Z"}]  # o 55 tem 40 h: fora da janela
    assert "Deploys em 24 h: 1 (56 às 04:00)." in _sem_tags(d.montar(_situacao(deploys=achados), AGORA))


def test_deploys_sem_changelog_e_none(tmp_path: Path) -> None:
    assert d.deploys_do_dia(AGORA, tmp_path) is None


# ------------------------------------------------------------------------------------------------------ entrada
def test_modo_padrao_com_arquivo_imprime_o_html_e_nao_envia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "situacao.json"
    arq.write_text(json.dumps(_situacao()), encoding="utf-8")
    monkeypatch.setattr(d.subprocess, "run", lambda *a, **k: pytest.fail("não pode chamar processo nenhum"))
    monkeypatch.setattr(d.redacao, "recarregar", lambda *_: False)
    assert d.main(["--arquivo", str(arq)]) == 0
    saida = capsys.readouterr().out
    assert "Resumo diário da Central" in saida and "[ensaio: nada foi enviado]" in saida


def test_arquivo_ilegivel_e_recusa_limpa(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(d.redacao, "recarregar", lambda *_: False)
    assert d.main(["--arquivo", str(tmp_path / "nao-existe.json")]) == 2
    assert "nada a enviar: arquivo ilegível" in capsys.readouterr().out


def test_enviar_nao_vale_com_arquivo(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as e:
        d.main(["--arquivo", str(tmp_path / "x.json"), "--enviar"])
    assert e.value.code == 2


def test_enviar_chama_o_telegram_status_e_imprime_o_message_id(monkeypatch: pytest.MonkeyPatch,
                                                              capsys: pytest.CaptureFixture[str]) -> None:
    visto: dict = {}

    def falso(cmd: list[str], **_k: object) -> subprocess.CompletedProcess:
        visto["cmd"] = cmd
        visto["corpo"] = Path(cmd[-1]).read_text(encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, stdout=b"enviado message_id=4242\n", stderr=b"")

    monkeypatch.setattr(d.subprocess, "run", falso)
    monkeypatch.setattr(d.redacao, "recarregar", lambda *_: False)
    monkeypatch.setattr(d, "ler_situacao", lambda agora, raiz: _situacao())
    assert d.main(["--enviar"]) == 0
    assert Path(visto["cmd"][1]).name == "telegram_status.py" and "Resumo diário da Central" in visto["corpo"]
    assert "enviado message_id=4242" in capsys.readouterr().out


def test_envio_que_falha_nao_conta_como_enviado(monkeypatch: pytest.MonkeyPatch,
                                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(d.subprocess, "run", lambda cmd, **k: subprocess.CompletedProcess(
        cmd, 1, stdout="Telegram recusou (400)".encode(), stderr=b""))
    monkeypatch.setattr(d.redacao, "recarregar", lambda *_: False)
    monkeypatch.setattr(d, "ler_situacao", lambda agora, raiz: _situacao())
    assert d.main(["--enviar"]) == 1
    saida = capsys.readouterr().out
    assert "falhou: Telegram recusou (400)" in saida and "enviado message_id" not in saida


# ----------------------------------------------------------------------------------- 28.74: custo do dia (fakes)
def _op(sufixo: str, horas: float = 2, status: str = "concluida", fim: float | None = 1) -> dict:
    return {"id": f"op-20261006090000-{sufixo}", "status": status, "command": f"comando secreto de {HANDLE}",
            "created_at": (AGORA - timedelta(hours=horas)).isoformat(),
            "finished_at": (AGORA - timedelta(hours=fim)).isoformat() if fim is not None else None}


def _conta(nome: str, gasto: float, horas: float = 5, externo: float = 0.0, em_uso: bool = True) -> dict:
    return {"account": nome, "in_use": em_uso, "spent_since_usd": gasto, "external_usd": externo,
            "anchor_at": (AGORA - timedelta(hours=horas)).isoformat()}


class FakeCentral:
    """Só GET, em memória: respostas por caminho; `Exception` levanta. Registra as URLs pedidas."""

    def __init__(self, respostas: dict[str, object]) -> None:
        self.respostas, self.urls = respostas, []

    def __call__(self, url: str) -> object:
        self.urls.append(url)
        r = self.respostas[url.removeprefix("http://central")]
        if isinstance(r, Exception):
            raise r
        return r


def _detalhe(total: object) -> dict:
    return {"id": "x", "custo": {"pesquisa_usd": 0, "alvos_usd": total, "total_usd": total}}


def _central(operacoes: list[dict], custos: dict[str, object], contas: object) -> FakeCentral:
    r: dict[str, object] = {"/api/operacoes?limite=200": {"items": operacoes}, "/api/ai/balances": {"accounts": contas}}
    for o in operacoes:
        r[f"/api/operacoes/{o['id']}"] = custos.get(o["id"], _detalhe(None))
    return FakeCentral(r)


def test_ler_custo_por_operacao_e_total_so_com_o_custo_da_propria_operacao() -> None:
    a, b, velha = _op("aaaaaa"), _op("bbbbbb", status="em_curso", fim=None), _op("cccccc", horas=60, fim=50)
    fake = _central([a, b, velha], {a["id"]: _detalhe(0.2), b["id"]: _detalhe(0.1234), velha["id"]: _detalhe(9.0)},
                    [_conta("anthropic", 1.5, externo=0.25), _conta("gemini", 0.0, em_uso=False)])
    c = d.ler_custo(AGORA, "http://central", fake)
    assert c == {"operacoes": [{"id": "op-aaaaaa", "usd": 0.2}, {"id": "op-bbbbbb", "usd": 0.1234}],
                 "ia": {"usd": 1.75, "fora": 0}}  # a operação de 60 h, já encerrada, fica fora; a em curso entra
    assert not any(u.endswith(velha["id"]) for u in fake.urls)  # nem se lê a que está fora da janela
    texto = _sem_tags(d.montar(_situacao(custo=c), AGORA))
    assert ("Custo do dia: 2 operações, US$ 0,32 no total (op-aaaaaa US$ 0,20, op-bbbbbb US$ 0,12); "
            "chamadas de IA (livro-caixa, não é custo de execução): US$ 1,75.") in texto


def test_operacao_sem_custo_e_nao_medida_e_nunca_zero() -> None:
    a, b, c_ = _op("aaaaaa"), _op("bbbbbb"), _op("cccccc")
    fake = _central([a, b, c_], {a["id"]: _detalhe(0.5), b["id"]: {"id": "x"},  # sem o campo `custo`
                                 c_["id"]: _detalhe("0,3")}, [])  # custo de tipo errado também não é número
    c = d.ler_custo(AGORA, "http://central", fake)
    assert [o["usd"] for o in c["operacoes"]] == [0.5, None, None] and c["ia"] == {"usd": None, "fora": 0}
    texto = _sem_tags(d.montar(_situacao(custo=c), AGORA))
    assert "3 operações, US$ 0,50 no total das 1 medidas (op-aaaaaa US$ 0,50, op-bbbbbb não medido, op-cccccc não medido)" in texto
    assert "chamadas de IA (livro-caixa, não é custo de execução): não medido." in texto
    todas = _sem_tags(d.montar(_situacao(custo={"operacoes": [{"id": "op-1abc", "usd": None}], "ia": None}), AGORA))
    assert "1 operação, custo não medido (op-1abc não medido); chamadas de IA" in todas and "US$ 0,00" not in todas
    assert "chamadas de IA (livro-caixa, não é custo de execução): não consegui ler." in todas


def test_nenhuma_operacao_no_dia_diz_nenhuma_e_zero_medido_aparece_como_zero() -> None:
    fake = _central([_op("aaaaaa", horas=70, fim=60)], {}, [_conta("openai", 0.0)])
    c = d.ler_custo(AGORA, "http://central", fake)
    assert c == {"operacoes": [], "ia": {"usd": 0.0, "fora": 0}}
    texto = _sem_tags(d.montar(_situacao(custo=c), AGORA))
    assert "Custo do dia: nenhuma operação; chamadas de IA (livro-caixa, não é custo de execução): US$ 0,00." in texto


def test_ia_so_conta_ancora_do_dia_e_conta_em_uso_e_avisa_as_que_ficaram_fora() -> None:
    contas = [_conta("anthropic", 2.0), _conta("openai", 7.0, horas=40),  # âncora velha demais: não mede o dia
              {"account": "typesafe", "in_use": True, "spent_since_usd": 0.0, "anchor_at": None},  # sem âncora
              _conta("gemini", 5.0, em_uso=False)]
    c = d.ler_custo(AGORA, "http://central", _central([], {}, contas))
    assert c["ia"] == {"usd": 2.0, "fora": 2}
    assert "US$ 2,00 (2 contas sem âncora do dia)." in _sem_tags(d.montar(_situacao(custo=c), AGORA))
    sem_nenhuma = d.ler_custo(AGORA, "http://central", _central([], {}, [_conta("openai", 7.0, horas=40)]))
    assert sem_nenhuma["ia"] == {"usd": None, "fora": 1}  # nada medido: "não medido", nunca US$ 0,00


def test_leitura_falhando_diz_nao_consegui_ler_e_nao_derruba_o_resumo() -> None:
    fake = _central([], {}, [_conta("anthropic", 1.0)])
    fake.respostas["/api/operacoes?limite=200"] = OSError("fora")  # só a lista cai: a IA segue
    c = d.ler_custo(AGORA, "http://central", fake)
    assert c == {"operacoes": None, "ia": {"usd": 1.0, "fora": 0}}
    texto = d.montar(_situacao(custo=c), AGORA)
    assert "operações: não consegui ler; chamadas de IA (livro-caixa, não é custo de execução): US$ 1,00." in _sem_tags(texto)
    assert "Plano: 714 itens" in _sem_tags(texto) and "Espera você: 3 perguntas" in _sem_tags(texto)
    # as duas caem: o resultado é None e a linha diz que não leu
    assert d.ler_custo(AGORA, "http://central", FakeCentral({"/api/operacoes?limite=200": OSError("x"),
                                                             "/api/ai/balances": ValueError("y")})) is None
    assert d.ler_custo(AGORA) is None  # sem rede (o GET padrão falha): nada de zero
    assert "• <b>Custo do dia:</b> não consegui ler a central." in d.montar(_situacao(custo=None), AGORA)
    assert d.montar(_situacao(custo="lixo"), AGORA).count("Custo do dia") == 1  # formato estranho: também "não consegui ler"
    # uma operação ilegível é só "não medida"
    a = _op("aaaaaa")
    f2 = _central([a], {}, [])
    f2.respostas[f"/api/operacoes/{a['id']}"] = OSError("fora")
    assert d.ler_custo(AGORA, "http://central", f2)["operacoes"] == [{"id": "op-aaaaaa", "usd": None}]


def test_situacao_sem_a_chave_custo_sai_sem_a_linha() -> None:
    texto = d.montar(_situacao(), AGORA)
    assert "Custo do dia" not in texto and len(texto.splitlines()) == 7


def test_so_tres_operacoes_listadas_as_mais_caras_primeiro_e_o_resto_vira_e_mais_n() -> None:
    ops = [{"id": f"op-{i}", "usd": u} for i, u in enumerate([0.1, 0.9, None, 0.5, 0.3])]
    texto = _sem_tags(d.montar(_situacao(custo={"operacoes": ops, "ia": {"usd": 1.0, "fora": 0}}), AGORA))
    assert "5 operações, US$ 1,80 no total das 4 medidas (op-1 US$ 0,90, op-3 US$ 0,50, op-4 US$ 0,30, e mais 2)" in texto


def test_o_limite_de_leituras_de_operacao_marca_o_excedente_como_nao_medido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "MAX_LEITURAS_DE_OPERACAO", 2)
    ops = [_op(f"a{i:05d}") for i in range(4)]
    fake = _central(ops, {o["id"]: _detalhe(0.1) for o in ops}, [])
    c = d.ler_custo(AGORA, "http://central", fake)
    assert [o["usd"] for o in c["operacoes"]] == [0.1, 0.1, None, None]
    assert sum(1 for u in fake.urls if "/api/operacoes/op-" in u) == 2


def test_a_linha_do_custo_nao_vaza_comando_nome_nem_handle_e_cabe_nas_12_linhas() -> None:
    ops = [_op("aaaaaa"), {**_op("bbbbbb"), "assunto": HANDLE, "persona_nome": "Zoraide Benevides"}]
    fake = _central(ops, {o["id"]: _detalhe(0.4) for o in ops}, [_conta("anthropic", 1.0)])
    c = d.ler_custo(AGORA, "http://central", fake)
    # o que a leitura guarda é só id curto e número: comando, assunto e persona nunca passam
    assert set(c["operacoes"][0]) == {"id", "usd"}
    # e mesmo um id/valor hostil vindo de um arquivo de situação não vira texto livre
    hostil = {"operacoes": [{"id": f"op-1 {HANDLE}", "usd": 0.1}, {"id": "op-x9", "usd": f"{HANDLE}"}], "ia": None}
    texto = d.montar(_situacao(custo=c, coerencia=_coerencia(duplicados=1)), AGORA)
    com_hostil = d.montar(_situacao(custo=hostil, coerencia=_coerencia(duplicados=1)), AGORA)
    for t in (texto, com_hostil):
        assert HANDLE not in t and "comando secreto" not in t and "Zoraide" not in t
        assert len(t.splitlines()) <= d.MAX_LINHAS and len(t) < d.LIMITE and t.count("<b>") == t.count("</b>")
    assert len(texto.splitlines()) == 9  # as 8 de sempre (com a coerência) mais a do custo
    assert "1 operação, custo não medido (op-x9 não medido)" in _sem_tags(com_hostil)  # só a de id válido entra


def test_ler_situacao_inclui_o_custo_lido_pela_base_dada(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(d, "ler_plano", lambda raiz: None)
    monkeypatch.setattr(d, "deploys_do_dia", lambda agora, raiz: None)
    visto: list[str] = []
    monkeypatch.setattr(d, "ler_custo", lambda agora, base: visto.append(base) or {"operacoes": [], "ia": None})
    s = d.ler_situacao(AGORA, tmp_path, cliente=FakeTrello([], {}), base="http://central")
    assert s["custo"] == {"operacoes": [], "ia": None} and visto == ["http://central"]
