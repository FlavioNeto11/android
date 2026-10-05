"""Item 28.19 — lote de teste não avisa um por um, e rajada do mesmo tipo sai como UM aviso agrupado.

Em 04/10, das 00:37Z às 00:38Z, o dono recebeu 11 "Uma execução parou pedindo informação" seguidos: era um lote de
medida de uma frente, sem marca. Aqui:
- a execução do sistema (prova, validação do QA e, agora, o lote `lote:<frente>:<id>`) não vira aviso;
- o primeiro aviso de um tipo sai na hora, e os seguintes da janela saem juntos, com a contagem;
- o aviso do dono segue como antes: dois seguidos continuam dois.

Prova `simulated`: canal e relógio falsos, sem rede. Banco pela fábrica configurada (SQLite, ou PostgreSQL com
`TEST_DATABASE_URL`).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from app.contracts.origem import PREFIXO_LOTE, PREFIXO_VALIDACAO, e_execucao_do_sistema
from app.db import Database
from app.events import EventBus
from app.modules.avisos.application.entrada import rotear
from app.modules.avisos.application.entrega import FalhaDeEnvio, entregar
from app.modules.avisos.domain.mensagem import CORPO_AGRUPADO, Aviso, titulo_agrupado
from app.modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from app.modules.avisos.infrastructure.servico import ServicoDeAvisos
from app.taskqueue.travas import AVISOS, Lideranca
from app.util import now

from .conftest import make_config

AQUI = "servidor-a"
ROOT = Path(__file__).resolve().parents[1]


class Relogio:
    def __init__(self) -> None:
        self.t = now()

    def __call__(self) -> datetime:
        return self.t

    def avancar(self, s: float) -> None:
        self.t += timedelta(seconds=s)


class Canal:
    """Canal falso que devolve um `message_id` crescente (o que liga o reply ao fato)."""

    def __init__(self, *falhas: FalhaDeEnvio | None) -> None:
        self.falhas = list(falhas)
        self.enviados: list[tuple[str, str, str | None]] = []

    async def enviar(self, titulo: str, corpo: str, link: str | None) -> int:
        if self.falhas:
            falha = self.falhas.pop(0)
            if falha is not None:
                raise falha
        self.enviados.append((titulo, corpo, link))
        return 1000 + len(self.enviados)


class Cena:
    def __init__(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path)
        cfg.ensure_dirs()
        self.db = Database(cfg.db_dsn)
        self.db.migrate()
        self.r = Relogio()
        self.lider = Lideranca(self.db, dono=AQUI, relogio=self.r)
        self.fila = FilaDeAvisos(self.db, relogio=self.r)
        self.canal = Canal()

    def chega(self, chave: str, tipo: str = "run.needs_input", corpo: str = "c", link: str | None = "L") -> None:
        self.fila.enfileirar(Aviso(chave=chave, tipo=tipo, titulo=f"t-{chave}", corpo=corpo, link=link))

    def volta(self, agrupar_s: float = 60.0, a_partir_de: int = 3) -> object:
        token = self.lider.tomar(AVISOS)
        assert token is not None
        return asyncio.run(entregar(self.fila, self.canal, cerca=lambda: self.lider.cercada(AVISOS, token),
                                    agora=self.r, limite=10, max_tentativas=3, backoff_s=30.0,
                                    agrupar_s=agrupar_s, agrupar_a_partir_de=a_partir_de))

    def avancar(self, s: float) -> None:
        self.r.avancar(s)


# ===================================================================== 1. a rajada
def test_rajada_de_11_vira_o_primeiro_na_hora_e_um_agrupado_com_a_contagem(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    c.chega("run:r0:needs_input")
    c.volta()
    assert [t for t, _, _ in c.canal.enviados] == ["t-run:r0:needs_input"], "o primeiro tem de sair na hora"
    for i in range(1, 11):                                           # uma a cada 4 s, como o lote das 00:37Z
        c.avancar(4)
        c.chega(f"run:r{i}:needs_input")
        c.volta()
    assert len(c.canal.enviados) == 1, "dentro da janela, o resto da rajada não sai um por um"
    c.avancar(60)
    c.volta()
    assert len(c.canal.enviados) == 2
    titulo, corpo, link = c.canal.enviados[1]
    assert titulo == "ANA: 10 execuções pararam pedindo informação"
    # 28.31: uma linha por item (até 5), "+N no painel" e o gesto; nada de corpo genérico.
    linhas = corpo.split("\n")
    assert linhas[:5] == [f"• t-run:r{i}:needs_input" for i in range(1, 6)] and linhas[5] == "+5 no painel"
    assert linhas[6].startswith("Espera você:") and CORPO_AGRUPADO not in corpo and link == "L"
    assert c.fila.contagens() == {"enviado": 11}
    # O reply ao agrupado não aponta fato nenhum: a `canal_enviadas` guarda a família do grupo.
    fatos = [r["fato"] for r in c.db.query("SELECT fato FROM canal_enviadas ORDER BY ref_mensagem")]
    assert fatos == ["run:r0:needs_input", "grupo:run.needs_input"]


def test_dois_avisos_seguidos_do_dono_continuam_dois(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    c.chega("approval:a1", tipo="approval.pending", corpo="aprovar 1")
    c.volta()
    c.avancar(10)
    c.chega("approval:a2", tipo="approval.pending", corpo="aprovar 2")
    c.volta()
    assert len(c.canal.enviados) == 1, "o segundo da janela espera o fim dela"
    c.avancar(50)
    c.volta()
    assert [corpo for _, corpo, _ in c.canal.enviados] == ["aprovar 1", "aprovar 2"], "abaixo do mínimo, sai inteiro"
    fatos = {r["fato"] for r in c.db.query("SELECT fato FROM canal_enviadas")}
    assert fatos == {"approval:a1", "approval:a2"}, "cada aprovação continua respondível pelo reply"


def test_dois_segurados_saem_um_a_um_sem_nova_espera(tmp_path: Path) -> None:
    """Com menos que o mínimo, os segurados saem na mesma volta: o segundo não espera mais uma janela por causa do
    envio do primeiro (nasceu antes dele)."""
    c = Cena(tmp_path)
    c.chega("run:a:needs_input")
    c.volta()
    c.avancar(5)
    c.chega("run:b:needs_input")
    c.avancar(5)
    c.chega("run:c:needs_input")
    c.volta()
    assert len(c.canal.enviados) == 1
    c.avancar(60)
    c.volta(a_partir_de=3)
    assert [t for t, _, _ in c.canal.enviados] == ["t-run:a:needs_input", "t-run:b:needs_input", "t-run:c:needs_input"]


def test_tipo_segurado_nao_trava_o_aviso_de_outro_tipo(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    c.chega("run:a:needs_input")
    c.volta()
    c.avancar(5)
    c.chega("run:b:needs_input")
    c.chega("session:9", tipo="session.needs_person")
    c.volta()
    assert [t for t, _, _ in c.canal.enviados] == ["t-run:a:needs_input", "t-session:9"]


def test_ja_pendentes_juntos_saem_agrupados_e_a_espera_nunca_passa_da_janela(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    for i in range(4):
        c.chega(f"run:x{i}:needs_input")
    c.volta()
    assert c.canal.enviados[0][0] == titulo_agrupado("run.needs_input", 4)
    # um gotejamento a cada 15 s: nenhuma linha espera mais que a janela
    for i in range(8):
        c.avancar(15)
        c.chega(f"run:g{i}:needs_input")
        c.volta()
    pendentes = c.db.query("SELECT criado_em FROM avisos_entregas WHERE estado='pendente'")
    for linha in pendentes:
        assert datetime.fromisoformat(str(linha["criado_em"])) >= c.r() - timedelta(seconds=60)


def test_desligado_cada_aviso_sai_sozinho_como_antes(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    for i in range(4):
        c.chega(f"run:x{i}:needs_input")
    c.volta(agrupar_s=0)
    assert len(c.canal.enviados) == 4 and all(corpo == "c" for _, corpo, _ in c.canal.enviados)


def test_falha_do_agrupado_vale_para_todas_as_linhas(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    for i in range(3):
        c.chega(f"run:x{i}:needs_input")
    c.canal.falhas = [FalhaDeEnvio("fora do ar")]
    c.volta()
    assert c.fila.contagens() == {"pendente": 3}, "o agrupado que falhou tem de voltar inteiro para a fila"
    c.avancar(31)
    c.volta()
    assert c.fila.contagens() == {"enviado": 3} and len(c.canal.enviados) == 1
    c2 = Cena(tmp_path / "b")
    for i in range(3):
        c2.chega(f"run:y{i}:needs_input")
    c2.canal.falhas = [FalhaDeEnvio("chat inexistente", definitiva=True)]
    c2.volta()
    assert c2.fila.contagens() == {"falhou": 3}


def test_reply_ao_agrupado_nao_responde_a_fato_nenhum() -> None:
    """28.19: o agrupado não diz a qual item se responde, então nada se aprova, veta ou responde por ele. Desde o 28.41
    (F1 da leitura do #380) ele também não vira texto livre (a prévia de uma execução nova): só informa."""
    from app.modules.avisos.application.entrada import SO_INFORMA_O_AGRUPADO
    for texto in ("sim", "não", "o perfil é o segundo"):
        i = rotear(texto, fato="grupo:run.needs_input")
        assert i.tipo == "desconhecida" and i.motivo == SO_INFORMA_O_AGRUPADO and i.ref is None, texto


# ===================================================================== 2. a origem
def test_lote_de_frente_e_execucao_do_sistema_e_pedido_de_pessoa_nao() -> None:
    assert e_execucao_do_sistema(None, f"{PREFIXO_LOTE}jev:latencia-01") is True
    assert e_execucao_do_sistema(None, f"{PREFIXO_VALIDACAO}p1") is True
    assert e_execucao_do_sistema("fluxo-1", None) is True
    assert e_execucao_do_sistema(None, "telegram:9001") is False, "pedido do dono pelo Telegram avisa"
    assert e_execucao_do_sistema(None, "trello:abc") is False
    assert e_execucao_do_sistema(None, "k-do-painel") is False
    assert e_execucao_do_sistema(None, None) is False


def test_o_prefixo_do_lote_mora_so_no_contrato() -> None:
    """Mudar a marca de um lado sem o outro faria o lote voltar a avisar: nenhum arquivo de `app/` escreve o literal."""
    literais = (f'"{PREFIXO_LOTE}', f"'{PREFIXO_LOTE}")
    achados = [str(p.relative_to(ROOT)) for p in (ROOT / "app").rglob("*.py")
               if p.name != "origem.py" and any(lit in p.read_text(encoding="utf-8") for lit in literais)]
    assert achados == []


def _run(banco: Database, run_id: str, chave: str) -> None:
    banco.execute("INSERT INTO runs(id, idempotency_key, command, mode, status, instance_ids, created_at)"
                  " VALUES (?,?,?,?,?,?,?)", (run_id, chave, "abrir o app", "single", "planning", "[]",
                                              now().isoformat()))


def test_execucao_de_lote_nao_vira_aviso_e_a_do_dono_vira(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    cfg.ensure_dirs()
    cfg.file.avisos.enabled = True
    from pydantic import SecretStr
    cfg.env.telegram_bot_token = SecretStr("123:fake")
    cfg.env.telegram_chat_id = SecretStr("42")
    banco = Database(cfg.db_dsn)
    banco.migrate()
    r = Relogio()
    servico = ServicoDeAvisos(cfg, EventBus(banco, origin=AQUI), FilaDeAvisos(banco, relogio=r),
                              Lideranca(banco, dono=AQUI, relogio=r), canal=Canal())
    _run(banco, "rl", f"{PREFIXO_LOTE}jev:latencia-0004-01")
    _run(banco, "rt", "telegram:9001")
    _run(banco, "rp", "painel-k-0001")

    def needs(rid: str) -> bool:
        return servico.enfileirar_evento("run.updated", {"run": {"id": rid, "status": "needs_input"}}, 1)

    assert needs("rl") is False, "a execução do lote de frente avisou o dono"
    # A aprovação que o lote abre SEGUE avisando: só o dono decide (orquestradora, 04/10 01:20Z).
    assert servico.enfileirar_evento("approval.pending", {"approval": {"id": "ap-l", "run_id": "rl"}}, 2) is True
    assert needs("rt") is True and needs("rp") is True, "pedido de pessoa (Telegram ou painel) segue avisando"


def test_o_link_do_agrupado_e_o_da_caixa_mesmo_sem_link_na_primeira_linha(tmp_path: Path) -> None:
    c = Cena(tmp_path)
    # A pausa sai na hora (28.31: parou algo do dono); o relatório pronto seria rotina e não entraria numa rajada.
    c.chega("pedido:p1", tipo="pedido.pausa_automatica", link=None)       # aviso de pedido que não pede pessoa
    c.chega("pedido:p2", tipo="pedido.pausa_automatica", link="CAIXA")
    c.chega("pedido:p3", tipo="pedido.pausa_automatica", link="CAIXA")
    c.volta()
    assert [(t, link) for t, _c, link in c.canal.enviados] == [("ANA: 3 novidades de pedidos", "CAIXA")]


def test_aviso_de_convidado_nunca_se_agrupa(tmp_path: Path) -> None:
    """28.18: o aviso do convidado novo se decide respondendo a ELE; agrupado, o reply não teria fato. Sai um a um, sem
    esperar a janela do tipo."""
    c = Cena(tmp_path)
    for i in range(4):
        c.chega(f"convidado:{700 + i}:novo", tipo="telegram.convidado_novo", link=None)
    for _ in range(4):
        c.volta()
    assert [t for t, _c, _l in c.canal.enviados] == [f"t-convidado:{700 + i}:novo" for i in range(4)]
