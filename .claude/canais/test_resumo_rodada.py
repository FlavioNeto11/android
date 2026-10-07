"""28.66: o resumo consolidado da rodada de operação para o Telegram do dono.

Prova `simulated`: só a parte pura (`montar`) e os adaptadores com a central, o Telegram e o `urllib` FALSOS. Nada aqui
fala com a central real, o Telegram ou o Trello.
Rodar da raiz: `backend/.venv/Scripts/python.exe -m pytest -q .claude/canais/test_resumo_rodada.py`.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import resumo_rodada as r  # noqa: E402

#: nomes FICTÍCIOS, fora da lista de reserva da redação: só a estrutura do texto os segura
NOME = "Zoraide Benevides"
HANDLE = "zoraide.benevides.ig"
OUTRO = "Quintiliano Albuquerque"


def _alvo(estagio: str = "acao_preparada", estado: str = "concluido", motivo: str | None = None, *, nome: str = NOME,
          conta: str | None = HANDLE, estagios: tuple[str, ...] = ("persona", "conta", "sessao"),
          acao: dict | None = None, texto: str = "Comentário que a persona escreveria sobre o post") -> dict:
    return {"profile_id": "prof-123456", "persona_nome": nome, "app_id": "instagram", "account_id": "acc-1",
            "conta": conta, "instance_id": "android-07", "run_id": "run-aaaa", "estagio": estagio, "estado": estado,
            "motivo": motivo, "estagios": [{"estagio": e, "em": "2026-10-06T13:10:00.000Z"} for e in estagios],
            "resultado": {"texto": texto, "conhecimento_ids": [], "evidencia_id": None, "custo_usd": 0.05,
                          "acao_final": acao or {"tipo": "comment_post", "verificada": False, "evidencia_id": None}},
            "custo_usd": 0.05}


def _op(**kw: object) -> dict:
    op: dict = {
        "id": "op-20261006130000-abc123", "command": f"comente nos posts de @{HANDLE} e mande e-mail a x@exemplo.com",
        "app_id": "instagram", "acao_final": "preparar", "max_usd": 5.0, "assunto": f"assunto de {NOME}",
        "fontes": ["https://exemplo.com/fonte"], "parametros": {"username": HANDLE},
        "status": "concluida_com_bloqueios", "created_at": "2026-10-06T13:00:00.000Z",
        "finished_at": "2026-10-06T14:12:30.000Z",
        "capacidade": {"solicitados": 30, "contas_existentes": 24, "sessoes_validas": 18, "contas_disponiveis": 15,
                       "concluidas": 12, "bloqueadas": 14, "em_curso": 0,
                       "motivos": {"sem sessão": 9, "aguarda liberação": 5}},
        "alvos": [_alvo(), _alvo(estagio="sessao", estado="bloqueado", motivo="sem sessão", nome=OUTRO, conta="quinti_ig",
                                 acao=None)],
        "custo": {"pesquisa_usd": 0.1, "alvos_usd": 1.2, "total_usd": 1.3}}
    op.update(kw)
    return op


def _sem_tags(html_: str) -> str:
    return re.sub(r"<[^>]+>", "", html_)


def test_o_resumo_tem_o_molde_dos_avisos_e_cabe_em_12_linhas() -> None:
    texto = r.montar(_op())
    linhas = texto.splitlines()
    assert 5 <= len(linhas) <= r.MAX_LINHAS
    assert linhas[0].startswith("<b>Rodada de operação concluída com bloqueios</b>")
    plano = _sem_tags(texto)
    assert "30 solicitados, 24 com conta, 18 com sessão, 12 concluídos, 14 bloqueados" in plano
    assert "9 sem sessão; 5 aguarda liberação" in plano
    assert "Custo US$ 1,30 de um teto de US$ 5,00, duração 1 h 12 min" in plano
    assert "<b>Crítico:</b> nada." in texto
    assert "<b>Espera você:</b> o sim de 1 ação(ões) preparada(s)" in texto


def test_nome_handle_comando_e_texto_do_comentario_nao_aparecem() -> None:
    op = _op()
    # o nome e o handle também entram no motivo (texto livre da central): saem dali
    op["capacidade"]["motivos"] = {f"conta de {NOME} restrita": 3, f"@{HANDLE} fora do ar": 1, "sem sessão": 2}
    op["alvos"][0]["motivo"] = f"{OUTRO} sem acesso"
    texto = r.montar(op, painel=None)
    for proibido in (NOME, "Zoraide", "Benevides", HANDLE, OUTRO, "Quintiliano", "quinti_ig", "exemplo.com",
                     "Comentário que a persona", "prof-123456", "android-07", "run-aaaa", "@"):
        assert proibido.lower() not in texto.lower(), proibido
    assert "http" not in texto
    assert "[persona]" in texto  # o motivo foi lido sem o nome, não descartado


def test_nada_de_contato_nem_ip_no_motivo() -> None:
    op = _op()
    op["capacidade"]["motivos"] = {"falha com ana@exemplo.com em 10.0.0.5": 1,
                                   "ligue +55 (11) 98765-4321 ou veja https://x.com/y": 1}
    texto = _sem_tags(r.montar(op))
    for proibido in ("ana@exemplo.com", "10.0.0.5", "98765-4321", "https://", "x.com"):
        assert proibido not in texto, proibido


def test_so_o_link_do_painel_e_depois_da_redacao() -> None:
    texto = r.montar(_op(), painel="https://painel.exemplo.org/central/")
    assert re.findall(r"https?://[^\s\"<]+", texto) == ["https://painel.exemplo.org/central/#/operacoes"]
    assert texto.splitlines()[-1].startswith('<a href="https://painel.exemplo.org/central/#/operacoes">')
    # base inválida (esquema estranho, espaço, aspas, usuário) não vira link
    for ruim in ("javascript:alert(1)", "https://a b.com", 'https://a.com/"x', "https://u@a.com", "", None):
        assert "<a " not in r.montar(_op(), painel=ruim)


def test_acoes_por_tipo_so_contam_a_verificada() -> None:
    op = _op(acao_final="executar")
    ok = {"tipo": "comment_post", "verificada": True, "evidencia_id": "ev-1"}
    op["alvos"] = [_alvo("resultado_verificado", estagios=("acao_executada", "resultado_verificado"), acao=ok),
                   _alvo("resultado_verificado", estagios=("acao_executada", "resultado_verificado"), acao=ok),
                   _alvo("acao_executada", estagios=("acao_executada",),
                         acao={"tipo": "like_post", "verificada": False, "evidencia_id": None}),
                   _alvo("acao_preparada")]
    plano = _sem_tags(r.montar(op))
    assert "2 comment_post (verificada)" in plano
    assert "1 executada(s) sem verificação" in plano and "1 preparada(s), sem executar" in plano
    assert "like_post" not in plano  # a não verificada não vira "feita" nem entra por tipo
    assert "executou a ação" in plano


def test_tipo_de_acao_fora_do_formato_vira_outra() -> None:
    op = _op()
    op["alvos"] = [_alvo("resultado_verificado", acao={"tipo": f"comentar {NOME}", "verificada": True})]
    texto = r.montar(op)
    assert "outra (verificada)" in texto and "Zoraide" not in texto


def test_critico_tem_cancelada_teto_e_aparelho() -> None:
    op = _op(status="cancelada")
    op["capacidade"]["motivos"] = {"teto de custo": 2, "aparelho indisponível": 3}
    op["custo"]["total_usd"] = 5.2
    critico = next(l for l in r.montar(op).splitlines() if "Crítico" in l)
    assert "cancelada" in critico and "teto de custo" in critico and "3 alvo(s) sem aparelho" in critico


def test_sem_bloqueio_nem_acao_diz_nada_e_nenhuma() -> None:
    op = _op(status="concluida")
    op["capacidade"].update(bloqueadas=0, motivos={}, concluidas=30)
    op["alvos"] = [_alvo("resultado_verificado", acao={"tipo": "x", "verificada": True})]
    op["alvos"][0]["resultado"] = None
    texto = r.montar(op)
    assert "Bloqueios por motivo" not in texto and "<b>Ações:</b> nenhuma." in texto
    assert "<b>Espera você:</b> nada." in texto


def test_muitos_motivos_viram_outros() -> None:
    op = _op()
    op["capacidade"]["motivos"] = {f"motivo {i}": i for i in range(1, 8)}
    plano = _sem_tags(r.montar(op))
    assert "7 motivo 7; 6 motivo 6; 5 motivo 5; 4 motivo 4; 6 outros" in plano


def test_operacao_em_curso_ou_formato_errado_e_recusado() -> None:
    with pytest.raises(r.Recusa, match="não está encerrada"):
        r.montar(_op(status="em_curso", finished_at=None))
    with pytest.raises(r.Recusa, match="não está encerrada"):
        r.montar(_op(finished_at=None))
    with pytest.raises(r.Recusa, match="falta `capacidade`"):
        r.montar({"status": "concluida"})
    with pytest.raises(r.Recusa):
        r.montar([])  # type: ignore[arg-type]


def test_a_entrada_nao_e_alterada() -> None:
    op = _op()
    antes = copy.deepcopy(op)
    r.montar(op)
    assert op == antes


# ------------------------------------------------------------------------------------------------ entrada e envio
class _Resposta:
    def __init__(self, corpo: bytes) -> None:
        self._c = corpo

    def read(self) -> bytes:
        return self._c

    def __enter__(self) -> "_Resposta":
        return self

    def __exit__(self, *a: object) -> None:
        return None


def test_leitura_da_central_usa_a_url_da_operacao_e_recusa_id_estranho(monkeypatch: pytest.MonkeyPatch) -> None:
    vistas: list[str] = []

    def falso(url: str, timeout: int = 0) -> _Resposta:
        vistas.append(url)
        return _Resposta(json.dumps(_op()).encode())

    monkeypatch.setattr(r.urllib.request, "urlopen", falso)
    assert r.ler_da_central("op-20261006130000-abc123")["status"] == "concluida_com_bloqueios"
    assert vistas == ["http://127.0.0.1:8000/api/operacoes/op-20261006130000-abc123"]
    for ruim in ("../../api/health", "op-1/cancelar", "xyz", "op-a b"):
        with pytest.raises(r.Recusa):
            r.ler_da_central(ruim)
    assert len(vistas) == 1


def test_central_fora_do_ar_e_recusa_limpa(monkeypatch: pytest.MonkeyPatch) -> None:
    def cai(*a: object, **k: object) -> None:
        raise OSError("conexão recusada")

    monkeypatch.setattr(r.urllib.request, "urlopen", cai)
    with pytest.raises(r.Recusa, match="não respondeu"):
        r.ler_da_central("op-20261006130000-abc123")


def test_modo_padrao_imprime_o_html_e_nao_envia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    arq = tmp_path / "operacao.json"
    arq.write_text(json.dumps(_op()), encoding="utf-8")

    def proibido(*a: object, **k: object) -> None:
        raise AssertionError("o ensaio não pode chamar o Telegram")

    monkeypatch.setattr(r.subprocess, "run", proibido)
    monkeypatch.setattr(r, "painel_do_config", lambda: None)
    monkeypatch.setattr(r.redacao, "recarregar", lambda *_: False)
    assert r.main(["--arquivo", str(arq)]) == 0
    saida = capsys.readouterr().out
    assert "<b>Rodada de operação concluída com bloqueios</b>" in saida and "ensaio: nada foi enviado" in saida


def test_enviar_chama_o_telegram_status_com_arquivo_e_imprime_o_message_id(monkeypatch: pytest.MonkeyPatch,
                                                                           capsys: pytest.CaptureFixture[str]) -> None:
    chamadas: list[list[str]] = []
    conteudo: list[str] = []

    def falso(cmd: list[str], **k: object) -> subprocess.CompletedProcess[bytes]:
        chamadas.append(cmd)
        conteudo.append(Path(cmd[2]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(cmd, 0, b"enviado HTML (400 chars) message_id=4321\n", b"")

    monkeypatch.setattr(r.subprocess, "run", falso)
    monkeypatch.setattr(r.urllib.request, "urlopen", lambda *a, **k: _Resposta(json.dumps(_op()).encode()))
    monkeypatch.setattr(r, "painel_do_config", lambda: None)
    monkeypatch.setattr(r.redacao, "recarregar", lambda *_: False)
    assert r.main(["op-20261006130000-abc123", "--enviar"]) == 0
    assert capsys.readouterr().out.strip() == "enviado message_id=4321"
    assert len(chamadas) == 1 and chamadas[0][1].endswith("telegram_status.py")
    assert "Rodada de operação" in conteudo[0] and NOME not in conteudo[0]
    assert not Path(chamadas[0][2]).exists()  # o arquivo temporário não fica


def test_envio_que_falha_nao_conta_como_enviado(monkeypatch: pytest.MonkeyPatch,
                                                capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(r.subprocess, "run",
                        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, "Falhou: sem token\n".encode(), b""))
    assert r.enviar("<b>x</b>") is None
    assert "falhou: Falhou: sem token" in capsys.readouterr().out


def test_enviar_recusa_operacao_em_curso_sem_chamar_o_telegram(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: pytest.fail("não pode enviar"))
    monkeypatch.setattr(r.urllib.request, "urlopen",
                        lambda *a, **k: _Resposta(json.dumps(_op(status="em_curso", finished_at=None)).encode()))
    monkeypatch.setattr(r.redacao, "recarregar", lambda *_: False)
    assert r.main(["op-20261006130000-abc123", "--enviar"]) == 2
    assert "nada a enviar" in capsys.readouterr().out


def test_argumentos_exigem_um_so_dos_dois_e_enviar_nao_vale_com_arquivo(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        r.main([])
    with pytest.raises(SystemExit):
        r.main(["op-20261006130000-abc123", "--arquivo", str(tmp_path / "x.json")])
    with pytest.raises(SystemExit):
        r.main(["--arquivo", str(tmp_path / "x.json"), "--enviar"])
