"""`scripts/jev-braco-offline.py`: o braço offline do Jev (31.11), parte R1 (curador), num banco sintético.

Prova `simulated`: banco SQLite temporário migrado (como o teste do relatório do 31.10), revisões do curador montadas
aqui e um transporte FALSO no lugar do Jev (nada de rede). Confere: o `--seco` não chama o decisor de verdade; só os
`kind` de F1 viram caso; a porta do runtime é o caminho (privacidade, limiar, fallback); o teto para a rodada antes do
POST; as recusas do `main`; o aviso de acompanhamento; o sinal por estado; e que a saída não leva dossiê nem `item_ref`.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.db import Database  # noqa: E402
from app.modules.context_retrieval.domain.model import ProviderUsage  # noqa: E402
from app.planning.decisao_fechada.decisores import DecisorJev  # noqa: E402

_spec = importlib.util.spec_from_file_location("jev_braco_offline", ROOT / "scripts" / "jev-braco-offline.py")
braco = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = braco          # o `@dataclass` do script procura o próprio módulo em `sys.modules`
_spec.loader.exec_module(braco)  # type: ignore[union-attr]

AGORA = datetime(2026, 10, 10, 12, tzinfo=UTC)
SEGREDO = "girassol-do-dossie"          # texto do item que NUNCA pode aparecer na saída


def _dossie(kind: str, *, contra: int = 0, a_favor: int = 1, estado: str = "published",
            saude: str | None = None) -> dict[str, Any]:
    lista = [{"posicao": "for"}] * a_favor + [{"posicao": "against"}] * contra
    return {"item": {"kind": kind, "estado": estado, "origem": "aprendido", "nome": SEGREDO},
            "risco": {"classe": "A", "politica": "auto"},
            "evidencias": {"total": len(lista), "lista": lista}, "licao": SEGREDO,
            **({"saude": {"rotulo": saude, "motivos": []}} if saude else {})}


class Banco:
    def __init__(self, tmp: Path) -> None:
        self.caminho = tmp / "braco.sqlite3"
        self.db = Database(self.caminho)
        self.db.migrate()
        self.db.execute("PRAGMA foreign_keys=OFF")
        self._n = 0

    def revisao(self, *, kind: str = "receita", contra: int = 0, a_favor: int = 1, decisao: str = "observar",
                saude: str | None = None,
                validade: str = "ok", simulated: int = 0, criado: str = "2026-10-05T09:00:00Z") -> str:
        self._n += 1
        self.db.execute(
            "INSERT INTO learning_reviews(id, created_at, item_ref, item_kind, gatilho, dossie_hash, dossie, template_id,"
            " template_versao, simulated, saida, validade) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"lr-{self._n}", criado, f"{kind}:item-{self._n}-{SEGREDO}", kind, "teste", f"h{self._n:03d}",
             json.dumps(_dossie(kind, contra=contra, a_favor=a_favor, saude=saude)), "curador", "v1", simulated,
             json.dumps({"decisao": decisao}), validade))
        return f"h{self._n:03d}"

    def transicao(self, item_ref: str, to_state: str, *, por: str, de: str = "published") -> None:
        self.db.execute("INSERT INTO learning_transitions(item_ref, item_kind, from_state, to_state, reason, decided_by,"
                        " decided_at) VALUES (?,?,?,?,?,?,?)",
                        (item_ref, "receita", de, to_state, "teste", por, "2026-10-06T00:00:00Z"))


class TransporteFalso:
    """No lugar do Jev: escolhe pela contagem de evidência contra (o "sinal" do estado) e mede um custo fixo."""

    model = "jev-falso"

    def __init__(self, *, usd: float = 0.00005, maior: float = 0.9) -> None:
        self.usd = usd
        self.maior = maior
        self.estados: list[dict[str, object]] = []

    def consultar(self, estado: Mapping[str, object], perguntas: Mapping[str, Mapping[str, object]], *,
                  timeout_s: float) -> tuple[Mapping[str, object], ProviderUsage]:
        self.estados.append(dict(estado))
        escolha = "opt:rebaixar" if int(str(estado.get("evidencias_contra", "0"))) > 0 else "opt:manter"
        resto = round((1 - self.maior) / 3, 4)
        probs = {o: (self.maior if o == escolha else resto)
                 for o in ("opt:manter", "opt:revisar", "opt:rebaixar", "opt:descartar")}
        resposta = {pid: {"type": "choice", "choice": escolha, "probabilities": probs, "confidence": self.maior}
                    for pid in perguntas}
        return resposta, ProviderUsage(input_tokens=100, output_tokens=5, cost_usd=self.usd, latency_ms=400.0)


def _real(transporte: TransporteFalso, teto: float) -> tuple[DecisorJev, Any]:
    t = braco.Teto(teto)
    return DecisorJev(transporte, conferir_gasto=t.conferir, registrar=t.registrar), t


@pytest.fixture
def banco(tmp_path: Path) -> Banco:
    return Banco(tmp_path)


def _casos(b: Banco, dono: frozenset[str] = frozenset()) -> tuple[list[Any], Any]:
    return braco.casos_do_curador(b.db, desde=None, autores_dono=dono)


def test_so_f1_vira_caso_e_o_fluxo_fica_de_fora(banco: Banco) -> None:
    banco.revisao(kind="receita")
    banco.revisao(kind="licao")
    banco.revisao(kind="fluxo")
    banco.revisao(kind="fluxo")
    casos, fora = _casos(banco)
    assert sorted(c.kind for c in casos) == ["licao", "receita"]
    assert fora == {"fluxo": 2}


def test_seco_passa_pela_porta_e_nao_chama_o_jev(banco: Banco) -> None:
    for _ in range(3):
        banco.revisao()
    casos, fora = _casos(banco)
    seco = braco.DecisorSeco()
    registros, interrompido = braco.rodar(casos, seco)
    assert len(seco.pedidos) == 3 and interrompido is None
    assert all(r is not None and r.resultado.fallback_reason == "desligado" for r in registros)
    r = braco.montar(casos, registros, fora=fora, agora=AGORA, enviado=False, interrompido=None, teto=None,
                     pedidos_secos=len(seco.pedidos))
    assert r["custo"]["nivel"].startswith("not_run")
    assert r["estratos"]["receita"]["medidas"]["concordancia_da_maior_com_o_curador"] is None


def test_o_pedido_que_sai_e_o_c0_redigido_da_porta(banco: Banco) -> None:
    banco.revisao(contra=2)
    casos, _ = _casos(banco)
    transporte = TransporteFalso()
    decisor, _ = _real(transporte, 0.05)
    braco.rodar(casos, decisor)
    assert len(transporte.estados) == 1
    enviado = transporte.estados[0]
    assert enviado["evidencias_contra"] == "2" and enviado["kind"] == "receita"
    assert SEGREDO not in json.dumps(enviado)


def test_sinal_por_estado_e_estabilidade(banco: Banco) -> None:
    banco.revisao(contra=0)
    banco.revisao(contra=0)
    banco.revisao(contra=3)
    casos, fora = _casos(banco)
    transporte = TransporteFalso()
    decisor, teto = _real(transporte, 0.05)
    registros, _ = braco.rodar(casos, decisor)
    r = braco.montar(casos, registros, fora=fora, agora=AGORA, enviado=True, interrompido=None, teto=teto,
                     pedidos_secos=None)
    sinal = r["estratos"]["receita"]["sinal"]
    assert sinal["estados_com_resposta"] == 2
    assert sinal["respostas_distintas_entre_estados"] == 2
    assert sinal["estados_instaveis"] == []
    medidas = r["estratos"]["receita"]["medidas"]
    assert medidas["respondidos"] == 3 and medidas["cobertura"] == 1.0
    assert r["custo"]["chamadas"] == 3 and r["custo"]["usd"] == pytest.approx(0.00015)
    assert r["custo"]["em_ai_calls"] is False
    md = braco.em_markdown(r)
    assert "**Sinal (receita):** 2 estados C0 distintos, 2 com resposta → 2 respostas distintas" in md
    assert "a entrada separa ao menos parte dos casos" in md
    assert "Estas 3 chamadas NÃO estão em `ai_calls`" in md and "O controle é a regra local gratuita" in md


def test_sinal_de_uma_resposta_so(banco: Banco) -> None:
    banco.revisao(contra=0)
    banco.revisao(contra=0, a_favor=4)
    casos, fora = _casos(banco)
    decisor, teto = _real(TransporteFalso(), 0.05)
    registros, _ = braco.rodar(casos, decisor)
    r = braco.montar(casos, registros, fora=fora, agora=AGORA, enviado=True, interrompido=None, teto=teto,
                     pedidos_secos=None)
    assert "2 estados C0 distintos, 2 com resposta → 1 respostas distintas" in braco.em_markdown(r)
    assert "a entrada NÃO separa os casos" in braco.em_markdown(r)


def test_abaixo_do_limiar_conta_na_grade_e_na_maior(banco: Banco) -> None:
    banco.revisao(contra=0, decisao="manter")
    casos, fora = _casos(banco)
    decisor, teto = _real(TransporteFalso(maior=0.6), 0.05)
    registros, _ = braco.rodar(casos, decisor)
    r = braco.montar(casos, registros, fora=fora, agora=AGORA, enviado=True, interrompido=None, teto=teto,
                     pedidos_secos=None)
    medidas = r["estratos"]["receita"]["medidas"]
    assert medidas["respondidos"] == 0 and medidas["fallbacks"] == {"abaixo_do_limiar": 1}
    assert medidas["cobertura_por_limiar"]["0.50"]["maior_probabilidade"] == 1.0
    assert medidas["cobertura_por_limiar"]["0.85"]["maior_probabilidade"] == 0.0
    assert medidas["concordancia_da_maior_com_o_curador"] == 1.0      # a maior é `manter`, o curador disse `manter`
    assert r["linhas"][0]["escolha"] is None and r["linhas"][0]["maior"] == "opt:manter"


def test_o_teto_para_a_rodada_antes_do_post(banco: Banco) -> None:
    for _ in range(5):
        banco.revisao()
    casos, fora = _casos(banco)
    transporte = TransporteFalso(usd=0.0006)
    # Antes de cada POST: gasto + reserva (0,001) ≤ 0,002. 1ª: 0 + 0,001; 2ª: 0,0006 + 0,001; 3ª: 0,0012 + 0,001 > 0,002.
    decisor, teto = _real(transporte, 0.002)
    registros, interrompido = braco.rodar(casos, decisor)
    assert interrompido == "teto"
    assert len(transporte.estados) == 2 and len(registros) == 3
    assert registros[-1] is not None and registros[-1].resultado.fallback_reason == "orcamento"
    assert teto.gasto_usd <= 0.002


def test_aviso_sem_amostra_e_rotulo_do_dono(banco: Banco) -> None:
    banco.revisao()
    banco.transicao(f"receita:item-1-{SEGREDO}", "disabled", por="Flavio")
    casos, fora = _casos(banco, frozenset({"Flavio"}))
    assert casos[0].rotulo == "descartar" and casos[0].fonte == "dono"
    r = braco.montar(casos, [None], fora=fora, agora=AGORA, enviado=False, interrompido=None, teto=None,
                     pedidos_secos=0)
    assert r["aviso"] == "acompanhamento; nenhum número aqui vale para GO (rótulos 1 e 2 = 1)"
    assert r["estratos"]["receita"]["veredito"] == "sem amostra"
    assert r["estratos"]["receita"]["modo"].startswith("off")


def test_regra_da_saude_e_um_segundo_controle_so_de_acompanhamento(banco: Banco, tmp_path: Path) -> None:
    """2º controle (orquestradora, 03/10 ~19:40Z): o rótulo de saúde do C0 pelo `CONTROLE_DA_SAUDE`. Não muda o controle
    pré-registrado (contagens de evidência) nem o veredito; rótulo fora do mapa fica sem resposta."""
    banco.revisao(saude="pouca_amostra", decisao="pedir_evidencia")
    banco.revisao(saude="saudavel", decisao="manter")
    banco.revisao(saude="parado", decisao="manter")
    banco.revisao(saude="indeterminado", decisao="observar")
    banco.revisao(decisao="observar")                                       # sem saúde no dossiê
    casos, fora = _casos(banco)
    assert [c.controle_saude for c in casos] == ["opt:revisar", "opt:manter", "opt:revisar", None, None]
    assert all(c.controle == "opt:manter" for c in casos)                 # o pré-registrado não mudou
    r = braco.montar(casos, [None] * len(casos), fora=fora, agora=AGORA, enviado=False, interrompido=None, teto=None,
                     pedidos_secos=0)
    m = r["estratos"]["receita"]["medidas"]
    assert m["concordancia_do_controle_da_saude_com_o_curador"] == braco.rel._taxa(2, 3)   # 2 de 3 com resposta
    assert m["controle_da_saude_sem_resposta"] == 2
    assert [l["controle_saude"] for l in r["linhas"]][:2] == ["opt:revisar", "opt:manter"]
    assert "regra da saúde" in braco.em_markdown(r)


def test_a_saida_nao_leva_dossie_nem_item_ref(banco: Banco, tmp_path: Path) -> None:
    banco.revisao(contra=1)
    banco.revisao(kind="fluxo")
    saida, md = tmp_path / "s.json", tmp_path / "s.md"
    assert braco.main(["--db", str(banco.caminho), "--json", str(saida), "--md", str(md)]) == 0
    texto = saida.read_text(encoding="utf-8") + md.read_text(encoding="utf-8")
    assert SEGREDO not in texto and "item-1" not in texto
    dados = json.loads(saida.read_text(encoding="utf-8"))
    assert dados["enviado"] is False and dados["pedidos_secos"] == 1 and dados["fora_de_f1"] == {"fluxo": 1}
    assert md.read_text(encoding="utf-8").splitlines()[2].startswith("**acompanhamento; nenhum número")


def test_estado_v1_por_padrao_e_v2_so_quando_pedido(banco: Banco, tmp_path: Path) -> None:
    from app.planning.decisao_fechada.curador import CAMPOS, CAMPOS_DE_SINAL, CAMPOS_V2  # noqa: PLC0415
    banco.revisao(contra=1)
    v1, _ = _casos(banco)
    assert set(v1[0].pedido.estado) <= CAMPOS and not set(v1[0].pedido.estado) & CAMPOS_DE_SINAL
    v2, _ = braco.casos_do_curador(banco.db, desde=None, autores_dono=frozenset(), versao_do_estado="v2")
    estado = dict(v2[0].pedido.estado)
    assert set(estado) <= CAMPOS_V2 and estado["evidencia_a_favor_idade"] == "nunca"   # a evidência sem data
    assert SEGREDO not in json.dumps(estado)
    saida, md = tmp_path / "v2.json", tmp_path / "v2.md"
    assert braco.main(["--db", str(banco.caminho), "--estado", "v2", "--json", str(saida), "--md", str(md)]) == 0
    assert json.loads(saida.read_text(encoding="utf-8"))["estado"] == "v2"
    assert "(C0, estado v2)" in md.read_text(encoding="utf-8").splitlines()[0]
    with pytest.raises(SystemExit):
        braco.main(["--db", str(banco.caminho), "--estado", "v3"])


def test_31_55_as_variantes_da_pergunta_so_mudam_o_pedido_offline(banco: Banco, tmp_path: Path) -> None:
    """31.55: `--pergunta p1|p2` troca as instruções e as opções SÓ no pedido do braço offline. P1 diz quando `review`
    cabe; P2 tira o `review`; as duas mantêm o `nenhuma` e o mesmo id da pergunta. A sombra do runtime não muda."""
    from app.planning.decisao_fechada import curador  # noqa: PLC0415
    from app.planning.decisao_fechada.contrato import ID_NENHUMA  # noqa: PLC0415
    banco.revisao()
    padrao, _ = _casos(banco)
    [p_runtime] = padrao[0].pedido.perguntas
    assert p_runtime.instrucoes == curador._INSTRUCOES == braco._INSTRUCOES_DO_RUNTIME  # noqa: SLF001
    p1, _ = braco.casos_do_curador(banco.db, desde=None, autores_dono=frozenset(), pergunta="p1")
    p2, _ = braco.casos_do_curador(banco.db, desde=None, autores_dono=frozenset(), pergunta="p2")
    [q1], [q2] = p1[0].pedido.perguntas, p2[0].pedido.perguntas
    assert q1.id == q2.id == curador.PERGUNTA_TRIAGEM and q1.limiar == q2.limiar == 0.85
    assert "Keep is the default" in q1.instrucoes and set(q1.opcoes) == set(p_runtime.opcoes)
    assert q2.instrucoes == p_runtime.instrucoes and set(q2.opcoes) == set(p_runtime.opcoes) - {"opt:revisar"}
    assert ID_NENHUMA in q1.opcoes and ID_NENHUMA in q2.opcoes
    assert p1[0].pedido.estado == padrao[0].pedido.estado            # a entrada é a mesma; só a pergunta muda
    # seco pela porta (nada de rede) e a saída diz qual pergunta foi
    seco = braco.DecisorSeco()
    registros, interrompido = braco.rodar(p2, seco)
    assert interrompido is None and len(seco.pedidos) == 1
    saida = tmp_path / "p2.json"
    assert braco.main(["--db", str(banco.caminho), "--pergunta", "p2", "--json", str(saida)]) == 0
    assert json.loads(saida.read_text(encoding="utf-8"))["pergunta"] == "p2"
    with pytest.raises(SystemExit):
        braco.main(["--db", str(banco.caminho), "--pergunta", "p3"])


@pytest.mark.parametrize("argv, msg", [
    (["--enviar"], "--teto"),
    (["--enviar", "--teto", "0.06"], "--teto"),
    (["--enviar", "--teto", "0"], "--teto"),
    (["--consumidor", "intencao"], "fora deste braço"),
    (["--consumidor", "apps"], "fora deste braço"),
])
def test_recusas_do_main(banco: Banco, argv: list[str], msg: str) -> None:
    with pytest.raises(SystemExit, match=msg):
        braco.main(["--db", str(banco.caminho), *argv])


def test_sem_chave_o_enviar_recusa_antes_de_comecar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(braco, "EnvSettings", lambda: SimpleNamespace(typesafe_api_key=None))
    with pytest.raises(SystemExit, match="chave do Jev"):
        braco.decisor_real(braco.Teto(0.01))


def test_o_banco_abre_so_para_leitura(banco: Banco) -> None:
    db = braco.rel._abrir(SimpleNamespace(dsn=None, db=str(banco.caminho)))
    try:
        with pytest.raises(Exception, match="readonly|read-only|query_only|attempt to write"):
            db.execute("DELETE FROM learning_reviews")
    finally:
        db.close()
