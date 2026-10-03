"""28.15: o contrato comum dos canais (`docs/design/canais-externos.md`, §9). O mesmo comando, vindo de `telegram` ou de
`trello`, passa pelas MESMAS políticas: identidade, dedupe, credencial, gramática com fato e as portas do painel.

Cada canal só traduz o que chegou numa `Recebida` e cumpre a `SaidaDaConversa` dele; a parte comum (`registrar` e
`tratar_pendentes`) não conhece canal. Aqui a tradução é pulada (a `Recebida` entra pronta) e a saída é falsa: o
Trello não apaga conteúdo do dono, então a resposta à credencial pede que ele apague.

Prova `simulated`: portas falsas (as mesmas de `test_telegram_entrada.py`) e banco de teste; nenhuma rede.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.db import Database
from app.modules.avisos.infrastructure.entrada import (
    RESPOSTA_CREDENCIAL,
    RESPOSTA_CREDENCIAL_SEM_APAGAR,
    Recebida,
    ServicoDeEntrada,
)
from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial

from .conftest import make_config
from .test_telegram_entrada import PortasFalsas

pytestmark = pytest.mark.asyncio

CANAIS = ("telegram", "trello")


class SaidaFalsa:
    """A saída de um canal: guarda o que a Central respondeu. `pode_apagar` é a diferença entre os canais (§7)."""

    def __init__(self, *, pode_apagar: bool) -> None:
        self.pode_apagar = pode_apagar
        self.enviadas: list[tuple[str, str, str | None]] = []
        self.apagadas: list[str] = []

    async def responder(self, texto: str, *, responde_a: str | None = None,
                        botoes: list[tuple[str, str]] | None = None) -> str | None:
        ref = f"saida-{len(self.enviadas) + 1}"
        self.enviadas.append((ref, texto, responde_a))
        return ref

    async def confirmar_botao(self, botao_id: str) -> None:
        return None

    async def tirar_botoes(self, ref_mensagem: str) -> None:
        return None

    async def apagar(self, ref_mensagem: str) -> bool:
        self.apagadas.append(ref_mensagem)
        return self.pode_apagar

    def textos(self) -> list[str]:
        return [t for _, t, _ in self.enviadas]


class Canal:
    def __init__(self, tmp_path: Path, canal: str, db: Database | None = None) -> None:
        cfg = make_config(tmp_path)
        cfg.ensure_dirs()
        if db is None:
            db = Database(cfg.db_dsn)
            db.migrate()
        self.db = db
        self.canal = canal
        self.repo = EntradasDoCanal(db, canal=canal)
        self.portas = PortasFalsas()
        self.saida = SaidaFalsa(pode_apagar=canal == "telegram")
        triagem = TriagemDeCredencial()
        self.servico = ServicoDeEntrada(cfg, self.repo, self.portas, lider=lambda _n: 1, recusa=triagem.recusa,
                                        redigir=triagem.redigir, operador=f"{canal}:dono")

    async def chega(self, id_externo: str, texto: str, *, do_dono: bool = True, responde_a: str | None = None) -> None:
        await self.servico.registrar(self.saida, Recebida(
            id_externo=id_externo, ordem=None, tipo="mensagem", do_dono=do_dono, texto=texto,
            ref_mensagem=f"m-{id_externo}", responde_a=responde_a))
        await self.servico.tratar_pendentes(self.saida)

    def linha(self, id_externo: str) -> dict[str, object]:
        ident = self.repo.id_de(id_externo)
        assert ident is not None
        linha = self.repo.linha(ident)
        assert linha is not None
        return linha


@pytest.mark.parametrize("canal", CANAIS)
async def test_quem_nao_e_o_dono_fica_sem_texto_e_nada_executa(tmp_path: Path, canal: str) -> None:
    c = Canal(tmp_path, canal)
    await c.chega("a1", "/aprovar aa11", do_dono=False)
    linha = c.linha("a1")
    assert (linha["canal"], linha["do_dono"], linha["texto"], linha["estado"]) == (canal, 0, None, "ignorada")
    assert c.portas.nomes() == [] and c.saida.enviadas == []


@pytest.mark.parametrize("canal", CANAIS)
async def test_o_mesmo_id_externo_executa_uma_vez(tmp_path: Path, canal: str) -> None:
    c = Canal(tmp_path, canal)
    await c.chega("a1", "/status")
    await c.chega("a1", "/status")
    assert c.db.scalar("SELECT COUNT(*) FROM canal_entradas WHERE canal=? AND id_externo='a1'", (canal,)) == 1
    assert c.portas.nomes() == ["status"]


async def test_o_dedupe_e_por_canal(tmp_path: Path) -> None:
    """O mesmo `id_externo` em dois canais são duas coisas diferentes: a chave é `(canal, id_externo)`."""
    tg = Canal(tmp_path, "telegram")
    tr = Canal(tmp_path, "trello", db=tg.db)
    await tg.chega("5", "/status")
    await tr.chega("5", "/status")
    assert tg.db.scalar("SELECT COUNT(*) FROM canal_entradas WHERE id_externo='5'") == 2
    assert tg.portas.nomes() == ["status"] and tr.portas.nomes() == ["status"]
    assert tg.repo.contagens() == {"feita": 1} and tr.repo.contagens() == {"feita": 1}


@pytest.mark.parametrize("canal", CANAIS)
async def test_credencial_e_recusada_sem_guardar_e_o_canal_apaga_quando_pode(tmp_path: Path, canal: str) -> None:
    c = Canal(tmp_path, canal)
    await c.chega("a1", "minha senha é Abc!2345xyz")
    linha = c.linha("a1")
    assert (linha["estado"], linha["texto"]) == ("recusada", None)
    assert c.saida.apagadas == ["m-a1"] and c.portas.nomes() == []
    esperada = RESPOSTA_CREDENCIAL if canal == "telegram" else RESPOSTA_CREDENCIAL_SEM_APAGAR
    assert c.saida.textos() == [esperada]
    assert "Abc!2345xyz" not in " ".join(c.saida.textos())


@pytest.mark.parametrize("canal", CANAIS)
async def test_resposta_ao_fato_decide_pelo_servico_do_painel_com_o_operador_do_canal(tmp_path: Path,
                                                                                      canal: str) -> None:
    """O fato é a chave do aviso que a Central mandou por aquele canal (o reply no Telegram, o cartão no Trello)."""
    c = Canal(tmp_path, canal)
    c.repo.registrar_enviada("aviso-1", "aviso", fato="approval:apr-0000aa11")
    await c.chega("a1", "/vetar o tom ficou agressivo", responde_a="aviso-1")
    assert c.portas.chamadas[-1] == ("decidir", ("apr-0000aa11", "reject", "o tom ficou agressivo"), f"{canal}:dono")
    assert c.linha("a1")["alvo"] == "approval:apr-0000aa11"
    # O aviso de um canal não é fato no outro.
    outro = Canal(tmp_path / "outro", "trello" if canal == "telegram" else "telegram", db=c.db)
    assert outro.repo.enviada("aviso-1") is None


@pytest.mark.parametrize("canal", CANAIS)
async def test_fora_da_gramatica_recebe_a_ajuda_e_nada_executa(tmp_path: Path, canal: str) -> None:
    c = Canal(tmp_path, canal)
    await c.chega("a1", "/apagartudo agora")
    assert c.portas.nomes() == []
    assert c.linha("a1")["estado"] == "feita" and "/ajuda" in c.saida.textos()[-1]
