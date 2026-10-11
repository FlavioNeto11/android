"""Item 12.5 (ADR-070) — o papel `leitura`, o contrato `transcribe`, o dado do app e a migração 078.

Os requisitos do hub de IA (dona da camada de IA) viram critério de aceite aqui, todos `simulated` (provedores e banco
falsos, sem chamada paga):

- sem herança: sem `ai.roles.leitura` com provider e model explícitos o papel fica DESLIGADO, fora de `AI_ROLES` e dos perfis;
- validação na partida: exige visão, recusa o mesmo modelo do `decide` e do `escalation` (base e perfis), recusa
  `fallback_provider` e `refusal_fallback`, o simulado passa;
- parse estrito da transcrição: chave extra recusa, só os campos pedidos entram, o excesso corta e marca `truncado`, e JSON
  inválido é erro (`AIError`), nunca "ilegível";
- gasto: `origem='leitura'`, os tetos do pedido, da execução e do dia valem, fatia própria opcional;
- o aviso de `/api/ai` nomeia o provedor, o modelo e os apps que declaram a região;
- o recorte tem teto (nunca a tela inteira);
- o `telas.yaml`: recusa `dentro_de` vazio, saída fora do catálogo, tela que não existe;
- a migração 078: as linhas ficam `arvore` e o CHECK recusa outra origem.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image
from pydantic import ValidationError

from app.automation.conhecimento_de_telas import ConhecimentoInvalido, de_dados, declaram_leitura_visual
from app.config import AI_ROLES, AiProfileCfg, AppConfigFile
from app.db import Database
from app.devices.codificacao import (RECORTE_ALTURA_MAX_FRACAO, RECORTE_ALTURA_MAX_PX, RECORTE_BYTES_MAX,
                                     recortar_jpeg)
from app.integrations.app_declarado import pacote as pacote_declarado
from app.models import AiStatus
from app.planning.capabilities import CONHECIMENTO_DE_APPS, Capability, CapabilityCatalog
from app.planning.anthropic_provider import AnthropicProvider
from app.planning.openai_provider import OpenAICompatProvider
from app.planning.provider import (AIError, LeituraRequest, Transcricao, TRANSCRICAO_LINHAS_MAX, TRANSCRICAO_TEXTO_MAX,
                                   Usage, modelo_do_papel_leitura, transcricao_from_json)
from app.planning.routing import RoutingProvider
from app.planning.simulated_provider import SimulatedProvider
from app.taskqueue.repository import Repository

from .conftest import Harness, _dsn_de_teste
from .test_hub_de_ia import FakeProvider, FakeRepo, com_hub, roteador
from .test_origem_e_rubrica_de_ia import ProvedorDeTodosOsUsos, _banco, _gasta

OPENAI = {"kind": "openai", "base_url": "http://leitor.exemplo/v1", "api_key_env": "OPENAI_API_KEY_LEITOR"}
PROVEDORES = {"anthropic": {"kind": "anthropic"}, "oa": OPENAI}
MODELOS = {"leitor-x": {"vision": True, "tools": False, "structured_output": "json_object", "thinking": False,
                        "effort": False}}
PRECOS = {"leitor-x": [1.0, 0.1, 1.0, 5.0]}
LEITURA = {"provider": "oa", "model": "leitor-x"}
BRUTO = {"linhas": ["a", "b"], "campos": [{"nome": "assunto", "valor": "b"}], "legivel": True, "truncado": False}


def _hub(tmp: Path, **kw: Any) -> Any:
    return com_hub(tmp, providers=kw.pop("providers", PROVEDORES), models=kw.pop("models", MODELOS),
                   roles=kw.pop("roles", {"leitura": LEITURA}))


# ====================================================================== (a) sem herança
def test_sem_bloco_o_papel_fica_desligado_e_fora_de_ai_roles_e_dos_perfis(tmp_path: Path) -> None:
    cfg = com_hub(tmp_path, roles={})
    assert "leitura" not in AI_ROLES and "leitura" not in cfg.ai_roles()
    assert cfg.ai_leitura() is None
    with pytest.raises(KeyError):
        cfg.ai_role("leitura")                        # nunca cai em `ai_model_for` nem no provedor do .env
    r = RoutingProvider(cfg)
    assert "leitura" not in r.roles
    assert [linha.role for linha in r.status().roles] == list(AI_ROLES)
    with pytest.raises(AIError) as e:
        asyncio.run(r.transcribe(LeituraRequest(recorte=b"x", saidas={"assunto": "assunto"})))
    assert e.value.kind == "not_configured"


def test_o_bloco_exige_provider_e_model_explicitos_e_nao_herda_nada() -> None:
    for bloco in ({}, {"provider": "oa"}, {"model": "leitor-x"}):
        with pytest.raises(ValidationError, match="leitura"):
            AppConfigFile.model_validate({"ai": {"providers": {"oa": OPENAI}, "models": MODELOS,
                                                 "roles": {"leitura": bloco}}})
    ok = AppConfigFile.model_validate({"ai": {"providers": {"oa": OPENAI}, "models": MODELOS,
                                              "roles": {"leitura": LEITURA}}})
    assert ok.ai.roles["leitura"].model == "leitor-x"


def test_perfil_nao_aceita_leitura() -> None:
    with pytest.raises(ValidationError, match="leitura"):
        AppConfigFile.model_validate({"ai": {"providers": {"oa": OPENAI}, "models": MODELOS,
                                             "profiles": {"canario": {"roles": {"leitura": LEITURA}}}}})


def test_o_papel_resolvido_e_independente_do_env_e_nao_cai_em_fallback_de_recusa(tmp_path: Path) -> None:
    cfg = _hub(tmp_path)
    cfg.env.ai_refusal_fallback = True                # o padrão do .env NÃO vale para o leitor
    r = cfg.ai_leitura()
    assert (r.provider, r.kind, r.model, r.refusal_fallback, r.fallback_provider) == ("oa", "openai", "leitor-x", False, None)


# ====================================================================== (b) (c) a validação na partida
def test_recusa_do_fallback_provider_e_do_refusal_fallback() -> None:
    for extra, trecho in (({"fallback_provider": "anthropic"}, "fallback_provider"),
                          ({"refusal_fallback": False}, "refusal_fallback"),
                          ({"refusal_fallback": True}, "refusal_fallback")):
        with pytest.raises(ValidationError, match=trecho):
            AppConfigFile.model_validate({"ai": {"providers": PROVEDORES, "models": MODELOS,
                                                 "roles": {"leitura": {**LEITURA, **extra}}}})


def test_recusa_o_mesmo_modelo_do_decide_ou_do_escalation_e_a_mensagem_diz_a_linha(tmp_path: Path) -> None:
    for papel in ("decide", "escalation"):
        cfg = _hub(tmp_path, models={**MODELOS}, roles={"leitura": LEITURA,
                                                         papel: {"provider": "oa", "model": "leitor-x"}})
        with pytest.raises(ValueError, match=rf"ai\.roles\.leitura\.model.*{papel}"):
            RoutingProvider(cfg)
    # o ator vindo do .env (sem `ai.roles`) também conta
    cfg = _hub(tmp_path)
    cfg.env.ai_model_actor = "leitor-x"
    with pytest.raises(ValueError, match="decide"):
        RoutingProvider(cfg)


def test_recusa_tambem_o_modelo_de_qualquer_perfil(tmp_path: Path) -> None:
    cfg = _hub(tmp_path)
    cfg.file.ai.profiles = {"canario": AiProfileCfg.model_validate(
        {"roles": {"decide": {"provider": "oa", "model": "leitor-x"}}})}
    with pytest.raises(ValueError, match=r"ai\.profiles\.canario\.roles\.decide"):
        RoutingProvider(cfg)


def test_recusa_modelo_sem_visao_e_o_simulado_passa(tmp_path: Path) -> None:
    cego = {"leitor-x": {"vision": False, "tools": False, "structured_output": "json_object", "thinking": False,
                         "effort": False}}
    with pytest.raises(ValueError, match="sem visão"):
        RoutingProvider(_hub(tmp_path, models=cego))
    sim = com_hub(tmp_path, providers={"sim": {"kind": "simulated"}}, roles={"leitura": {"provider": "sim", "model": "x"}})
    assert RoutingProvider(sim).roles["leitura"].kind == "simulated"


def test_recusa_modelo_nao_declarado_em_ai_models_e_a_mensagem_diz_a_linha(tmp_path: Path) -> None:
    # sem a linha, `ModelCaps()` presumiria visão: o leitor exige modelo DECLARADO com `vision: true`
    outro = {"outro-modelo": MODELOS["leitor-x"]}
    with pytest.raises(ValueError, match=r"não está declarado em ai\.models.*ai\.models\.leitor-x.*vision: true"):
        RoutingProvider(_hub(tmp_path, models=outro))
    RoutingProvider(_hub(tmp_path))                                  # declarado com visão: passa


def test_o_mesmo_modelo_escrito_de_duas_formas_e_um_so(tmp_path: Path) -> None:
    # caixa, prefixo de gateway e sufixo de data não fazem outro modelo (como o `model_caps` já trata)
    for escrito in ("OpenAI/Leitor-X-20261001", "leitor-x-20261001", "LEITOR-X", "vendor/leitor-x"):
        cfg = _hub(tmp_path, roles={"leitura": {"provider": "oa", "model": escrito},
                                    "escalation": {"provider": "oa", "model": "leitor-x"}})
        with pytest.raises(ValueError, match=r"ai\.roles\.leitura\.model.*escalation"):
            RoutingProvider(cfg)
    # e a declaração em `ai.models` também se acha pelo nome normalizado
    RoutingProvider(_hub(tmp_path, roles={"leitura": {"provider": "oa", "model": "OpenAI/Leitor-X-20261001"}}))


def test_o_exemplo_de_configuracao_documenta_o_papel_comentado() -> None:
    exemplo = (Path(__file__).resolve().parents[2] / "config" / "config.example.yaml").read_text(encoding="utf-8")
    assert "leitura_visual:" in exemplo and "enabled: false" in exemplo
    assert "#   leitura: {provider: openai" in exemplo and "claude-haiku-4-5" in exemplo
    assert "\n  roles:\n    leitura" not in exemplo       # nada ligado por padrão


# ====================================================================== (e) parse estrito
def test_chave_extra_ou_formato_errado_recusa_a_resposta_inteira() -> None:
    pedidas = ["assunto"]
    assert transcricao_from_json(json.dumps(BRUTO), pedidas).campos == {"assunto": "b"}
    for ruim in ({**BRUTO, "extra": 1},
                 {**BRUTO, "campos": [{"nome": "assunto", "valor": "b", "x": 1}]},
                 {k: v for k, v in BRUTO.items() if k != "legivel"},
                 {**BRUTO, "linhas": "texto"}):
        with pytest.raises(AIError) as e:
            transcricao_from_json(json.dumps(ruim), pedidas)
        assert e.value.kind == "invalid_output"


def test_resposta_invalida_nao_leva_o_texto_do_modelo_ao_log_nem_com_traceback(caplog: pytest.LogCaptureFixture) -> None:
    # a `ValidationError` do pydantic traz `input_value=` com o que o modelo escreveu; o executor loga com exc_info=True
    segredo = "codigo 482913"
    ruins = (json.dumps({**BRUTO, "extra": segredo}),
             json.dumps({**BRUTO, "campos": [{"nome": "assunto", "valor": "b", "x": segredo}]}),
             json.dumps({**BRUTO, "linhas": segredo}),
             segredo)                                                # nem JSON é
    log = logging.getLogger("teste.leitura")
    with caplog.at_level(logging.DEBUG):
        for bruto in ruins:
            try:
                transcricao_from_json(bruto, ["assunto"])
            except AIError as exc:
                assert exc.__cause__ is None and exc.__context__ is None
                log.warning("leitura falhou: %s", exc, exc_info=True)
    assert len(caplog.records) == len(ruins) and "Traceback" in caplog.text
    assert "482913" not in caplog.text and all("482913" not in (r.exc_text or "") for r in caplog.records)


def test_d5_a_falha_do_parse_nao_guarda_a_validation_error_nem_em_context() -> None:
    import traceback

    for bruto in (json.dumps({**BRUTO, "extra": "codigo 482913"}), "codigo 482913",
                  json.dumps({**BRUTO, "linhas": "codigo 482913"})):
        with pytest.raises(AIError) as e:
            transcricao_from_json(bruto, ["assunto"])
        assert e.value.__cause__ is None and e.value.__context__ is None
        assert "482913" not in repr(e.value) and "482913" not in "".join(traceback.format_exception(e.value))


def test_campo_pedido_repetido_com_valores_diferentes_recusa_como_chave_extra() -> None:
    def com(*pares: tuple[str, str | None]) -> str:
        return json.dumps({**BRUTO, "campos": [{"nome": n, "valor": v} for n, v in pares]})

    with pytest.raises(AIError) as e:
        transcricao_from_json(com(("assunto", "b"), ("assunto", "outro")), ["assunto"])
    assert e.value.kind == "invalid_output" and "outro" not in str(e.value)
    with pytest.raises(AIError):
        transcricao_from_json(com(("assunto", "b"), ("assunto", None)), ["assunto"])
    # o mesmo valor repetido não contradiz; e repetir um nome que ninguém pediu é ruído que o filtro já descarta
    assert transcricao_from_json(com(("assunto", "b"), ("assunto", "b")), ["assunto"]).campos == {"assunto": "b"}
    assert transcricao_from_json(com(("assunto", "b"), ("x", "1"), ("x", "2")), ["assunto"]).campos == {"assunto": "b"}


def test_json_invalido_e_erro_nunca_ilegivel_e_ilegivel_so_quando_o_modelo_diz() -> None:
    for lixo in ("", "não é json", "[]", "null", "```json\n{\n```"):
        with pytest.raises(AIError) as e:
            transcricao_from_json(lixo, ["assunto"])
        assert e.value.kind == "invalid_output"
    cego = {**BRUTO, "legivel": False, "linhas": [], "campos": []}
    assert transcricao_from_json(json.dumps(cego), ["assunto"]).legivel is False
    assert transcricao_from_json("```json\n" + json.dumps(BRUTO) + "\n```", ["assunto"]).legivel is True


def test_so_os_campos_pedidos_entram_e_o_pedido_que_nao_veio_fica_nulo() -> None:
    bruto = {**BRUTO, "campos": [{"nome": "assunto", "valor": "b"}, {"nome": "outro", "valor": "inventado"}]}
    t = transcricao_from_json(json.dumps(bruto), ["assunto", "remetente"])
    assert t.campos == {"assunto": "b", "remetente": None}


def test_o_excesso_corta_e_marca_truncado() -> None:
    longa = "x" * (TRANSCRICAO_TEXTO_MAX + 50)
    bruto = {**BRUTO, "linhas": [longa] + ["l"] * (TRANSCRICAO_LINHAS_MAX + 3),
             "campos": [{"nome": "assunto", "valor": longa}]}
    t = transcricao_from_json(json.dumps(bruto), ["assunto"])
    assert t.truncado and len(t.linhas) == TRANSCRICAO_LINHAS_MAX
    assert all(len(x) <= TRANSCRICAO_TEXTO_MAX for x in t.linhas) and len(t.campos["assunto"] or "") == TRANSCRICAO_TEXTO_MAX


def test_o_pedido_valida_os_nomes_e_o_teto() -> None:
    LeituraRequest(recorte=b"x", saidas={"remetente": "r"})
    for ruim in ({}, {"Remetente": "r"}, {"1x": "r"}, {"a-b": "r"}, {f"n{i}": "r" for i in range(21)}):
        with pytest.raises(ValueError):
            LeituraRequest(recorte=b"x", saidas=ruim)


def test_a_transcricao_nao_se_imprime_por_acidente() -> None:
    t = Transcricao(linhas=["Your code is 482913"], campos={"assunto": "Your code is 482913"})
    assert "482913" not in repr(t) and "482913" not in str(t)


# ====================================================================== (d) gasto: origem, tetos, fatia
class Leitor(ProvedorDeTodosOsUsos):
    async def transcribe(self, req: Any) -> Any:
        self.calls.append("leitura")
        return Transcricao(linhas=["a"], campos={"assunto": "a"}), Usage(calls=1, role="leitura", model=self.model,
                                                                         provider=self.name)


def _hub_leitura(tmp: Path, db: Database, *, teto_dia: float = 0.0, teto_execucao: float = 0.0,
                 limites: dict[str, Any] | None = None) -> tuple[RoutingProvider, FakeRepo]:
    cfg = _hub(tmp)
    cfg.file.ai.prices = dict(PRECOS)
    for chave, valor in (limites or {}).items():
        setattr(cfg.file.ai.limits, chave, valor)
    r = roteador(cfg, {"plan": ProvedorDeTodosOsUsos("anthropic", "claude-opus-5")})
    rr = r.roles["leitura"]
    r._por_chave[(rr.provider, rr.kind, rr.model, rr.timeout_s, rr.max_retries, rr.refusal_fallback)] = Leitor(  # noqa: SLF001
        "oa", "leitor-x")
    repo = FakeRepo(db)
    repo.teto_usd_da_execucao = lambda run_id: None                    # type: ignore[method-assign]
    r.attach(repo=repo, settings_getter=lambda: SimpleNamespace(ai_max_usd_per_run=teto_execucao,
                                                                ai_max_usd_per_day=teto_dia))
    return r, repo


def _le(r: RoutingProvider, run_id: str | None = "r1") -> Usage:
    _, usage = asyncio.run(r.transcribe(LeituraRequest(recorte=b"x", saidas={"assunto": "a"}, run_id=run_id)))
    return usage


def test_a_chamada_grava_role_e_origem_leitura(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    r, _ = _hub_leitura(tmp_path, db)
    usage = _le(r)
    assert (usage.role, usage.origem) == ("leitura", "leitura")
    (tmp_path / "ev").mkdir()
    Repository(db, __import__("app.events", fromlist=["EventBus"]).EventBus(db), tmp_path / "ev").add_usage("r1", None, usage)
    linha = db.one("SELECT role, origem, run_id FROM ai_calls")
    assert (linha["role"], linha["origem"], linha["run_id"]) == ("leitura", "leitura", "r1")
    db.close()


def test_valem_os_tetos_da_execucao_e_do_dia_com_o_run_id(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    db.execute("INSERT INTO ai_calls(ts, run_id, role, model, input_tokens, output_tokens, origem)"
               " VALUES (?,?,?,?,?,?,?)", (__import__("app.planning.costs", fromlist=["x"]).day_start_iso(), "r1", "decide",
                                            "leitor-x", 3_000_000, 0, "execucao"))                 # US$ 3,00 em r1
    r, _ = _hub_leitura(tmp_path, db, teto_execucao=2.0)
    with pytest.raises(AIError) as e:
        _le(r, "r1")
    assert (e.value.kind, e.value.motivo) == ("budget", "execucao")
    r, _ = _hub_leitura(tmp_path, db, teto_dia=2.0)
    with pytest.raises(AIError) as e:
        _le(r, "r1")
    assert e.value.motivo == "dia"
    db.close()


def test_fatia_da_leitura_e_opcional_e_so_barra_a_propria_origem(tmp_path: Path) -> None:
    db = _banco(tmp_path)
    _gasta(db, "leitura", 3_000_000)                                  # US$ 3,00 com o preço de teste do leitor
    db.execute("UPDATE ai_calls SET model='leitor-x'")
    r, _ = _hub_leitura(tmp_path, db, teto_dia=100.0)                 # padrão 0: sem fatia
    _le(r)
    r, _ = _hub_leitura(tmp_path, db, teto_dia=100.0, limites={"leitura_max_usd_per_day": 1.0})
    with pytest.raises(AIError) as e:
        _le(r)
    assert (e.value.kind, e.value.motivo) == ("budget", "fatia_leitura")
    # a fatia estourada da leitura não barra o planejador
    asyncio.run(r._call("plan", None, lambda p: p.plan(None), origem="ensino"))               # noqa: SLF001
    db.close()


# ====================================================================== (h) o aviso de /api/ai
def test_o_aviso_nomeia_provedor_modelo_e_os_apps_que_declaram_a_regiao(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY_LEITOR", "chave-que-nunca-aparece")
    cfg = _hub(tmp_path)
    cfg.file.ai.leitura_visual.enabled = True
    r = RoutingProvider(cfg)
    r.providers["decide"] = SimpleNamespace(status=lambda: AiStatus(
        provider="anthropic", model="m", configured=True, simulated=False, sends_data_externally=True, notice="base"),
        configured=True, simulated=False)
    st = r.status()
    linha = next(x for x in st.roles if x.role == "leitura")
    assert (linha.provider, linha.model, linha.sends_data_externally, linha.vision) == ("oa", "leitor-x", True, True)
    assert st.models["leitura"] == "leitor-x"
    assert "Leitura visual (ligada)" in st.notice and "oa" in st.notice and "leitor-x" in st.notice
    # o rótulo do DADO do app (`AppDefinition.label`), não o pacote cru
    assert "Outlook" in st.notice and "com.microsoft.office.outlook" not in st.notice and "terceiros" in st.notice
    # Polimento do Chrome do deploy 12: a preposição contraída ("a o provedor" saía no painel).
    assert "é enviado ao provedor “oa”" in st.notice and "a o provedor" not in st.notice
    assert "chave-que-nunca-aparece" not in st.model_dump_json() and st.sends_data_externally
    assert declaram_leitura_visual(CONHECIMENTO_DE_APPS) == ["com.microsoft.office.outlook"]


# ====================================================================== (i) o simulado é determinístico
def test_o_leitor_simulado_e_deterministico() -> None:
    sim = SimulatedProvider()
    req = LeituraRequest(recorte=b"x", saidas={"assunto": "a"})
    t0, u0 = asyncio.run(sim.transcribe(req))
    assert (t0.legivel, t0.campos, u0.calls) == (False, {"assunto": None}, 0)      # nada programado: nunca inventa
    sim.leitura = Transcricao(linhas=["x"], campos={"assunto": "x"})
    assert asyncio.run(sim.transcribe(req))[0] == asyncio.run(sim.transcribe(req))[0] == sim.leitura
    sim.leitura = lambda r: Transcricao(linhas=list(r.saidas), campos={n: n for n in r.saidas})
    assert asyncio.run(sim.transcribe(req))[0].campos == {"assunto": "assunto"}


# ====================================================================== A5: a leitura nunca cai no modelo do ator
def test_a_leitura_exige_o_modelo_explicito_do_papel_em_todos_os_provedores(tmp_path: Path) -> None:
    cfg = _hub(tmp_path)
    assert modelo_do_papel_leitura(cfg, cfg.ai_role("leitura")) == "leitor-x"
    assert modelo_do_papel_leitura(cfg, None) == "leitor-x"           # provedor único: o de `ai.roles.leitura`
    req = LeituraRequest(recorte=b"x", saidas={"assunto": "a"})
    # a instância é a do ATOR (o hub compartilha instâncias por chave): recusa, sem rede e sem cair em `self.model`
    for prov in (AnthropicProvider(cfg, cfg.ai_role("decide")), OpenAICompatProvider(cfg, cfg.ai_role("decide"))):
        with pytest.raises(AIError) as e:
            asyncio.run(prov.transcribe(req))
        assert e.value.kind == "not_configured"
    # provedor único sem `ai.roles.leitura`: também recusa (antes caía no modelo do `.env`)
    sem = com_hub(tmp_path, providers=PROVEDORES, models=MODELOS, roles={})
    with pytest.raises(AIError) as e:
        asyncio.run(AnthropicProvider(sem).transcribe(req))
    assert e.value.kind == "not_configured"


# ====================================================================== (g) o recorte tem teto
def _jpeg(tamanho: tuple[int, int] = (576, 1024), ruido: bool = False) -> bytes:
    img = Image.new("RGB", tamanho, (30, 40, 50))
    if ruido:
        import random
        random.seed(1)
        img.putdata([(random.randrange(256), random.randrange(256), random.randrange(256))
                     for _ in range(tamanho[0] * tamanho[1])])
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


def test_o_recorte_e_uma_linha_nunca_a_tela_inteira() -> None:
    assert Image.open(io.BytesIO(recortar_jpeg(_jpeg(), 720, 1280, (0, 323, 720, 485)))).size == (576, 130)
    # a tela toda, mais da metade, e DUAS linhas e meia (324 px = 259 na imagem > 0,2 da altura): é mais de uma linha
    for limites in ((0, 0, 720, 1280), (0, 100, 720, 900), (0, 300, 720, 624)):
        with pytest.raises(ValueError):
            recortar_jpeg(_jpeg(), 720, 1280, limites)
    with pytest.raises(ValueError):
        recortar_jpeg(_jpeg(), 720, 1280, (10, 10, 10, 10))
    with pytest.raises(ValueError):
        recortar_jpeg(b"nao e jpeg", 720, 1280, (0, 0, 720, 100))


def test_o_recorte_respeita_o_teto_de_bytes() -> None:
    saida = recortar_jpeg(_jpeg((1440, 2560), ruido=True), 1440, 2560, (0, 600, 1440, 880))
    assert len(saida) <= RECORTE_BYTES_MAX


def test_o_teto_absoluto_em_pixels_vale_para_a_imagem_maior_que_o_padrao() -> None:
    # 360 px de uma imagem de 2560 são 0,14 da altura (passaria pela fração), mas passam de 320 px: mais de duas linhas
    with pytest.raises(ValueError, match="grande demais"):
        recortar_jpeg(_jpeg((1440, 2560)), 1440, 2560, (0, 600, 1440, 960))
    assert RECORTE_ALTURA_MAX_FRACAO <= 0.2 and RECORTE_ALTURA_MAX_PX == 320
    # a linha do teste (162 px no aparelho) cabe em qualquer tamanho de imagem do padrão
    assert Image.open(io.BytesIO(recortar_jpeg(_jpeg((720, 1280)), 720, 1280, (0, 323, 720, 485)))).size == (720, 162)


# ====================================================================== o dado do app (`telas.yaml`)
def _telas(**regiao: Any) -> dict[str, Any]:
    bloco = {"tela": "caixa", "dentro_de": ["lista"], "saidas": ["assunto"], **regiao}
    return {"app": "com.x.y", "idioma_padrao": "en", "sinais": {"en": {"n": "nunca"}},
            "telas": [{"tela": "caixa", "tipo": "autenticada", "autenticada": True, "ids": ["lista"]}],
            "estado_conhecido": {"telas": ["caixa"]}, "leitura_visual": {"regioes": [bloco]}}


def test_o_carregador_recusa_regiao_mal_formada() -> None:
    assert de_dados(_telas()).regioes_visuais[0].saidas == ("assunto",)
    for ruim, trecho in (({"dentro_de": []}, "dentro_de"), ({"dentro_de": [" "]}, "dentro_de"),
                         ({"saidas": []}, "saidas"), ({"saidas": ["Assunto!"]}, "saidas"),
                         ({"tela": "outra"}, "não está declarada"), ({"extra": 1}, "campo desconhecido")):
        with pytest.raises(ConhecimentoInvalido, match=trecho):
            de_dados(_telas(**ruim))
    sem = _telas()
    del sem["leitura_visual"]
    assert de_dados(sem).regioes_visuais == ()


def test_saida_da_regiao_precisa_existir_em_alguma_acao_do_catalogo(tmp_path: Path) -> None:
    import yaml
    (tmp_path / "telas.yaml").write_text(yaml.safe_dump(_telas(saidas=["assunto", "fantasma"])), encoding="utf-8")
    cap = Capability(key="A", title="a", goal="g", post_kind="model_judged", post_value="v", post_description="d",
                     saidas=("assunto",), max_attempts=1)
    with pytest.raises(pacote_declarado.PacoteInvalido, match="fantasma"):
        pacote_declarado._conferir_regioes_visuais(tmp_path, CapabilityCatalog("com.x.y", [cap]))   # noqa: SLF001
    with pytest.raises(pacote_declarado.PacoteInvalido):
        pacote_declarado._conferir_regioes_visuais(tmp_path, None)                                  # noqa: SLF001
    (tmp_path / "telas.yaml").write_text(yaml.safe_dump(_telas()), encoding="utf-8")
    pacote_declarado._conferir_regioes_visuais(tmp_path, CapabilityCatalog("com.x.y", [cap]))       # noqa: SLF001


def test_o_dado_do_outlook_declara_a_regiao_da_caixa_e_o_pacote_carrega() -> None:
    manifestos = {m.definition.package: m for m in pacote_declarado.descobrir()}
    assert "com.microsoft.office.outlook" in manifestos            # a conferência contra o catálogo passou na carga
    from app.automation import conhecimento_de_telas as t
    k = t.da_pasta(CONHECIMENTO_DE_APPS / "com.microsoft.office.outlook")
    # 31.339: a caixa e as pastas de sistema (a lista e o mesmo componente) declaram a mesma região
    assert [r.tela for r in k.regioes_visuais] == ["caixa_de_entrada", "pasta_de_email"]
    for r in k.regioes_visuais:
        assert (r.dentro_de, r.saidas) == (("com.microsoft.office.outlook:id/conversation_list",), ("remetente", "assunto"))


# ====================================================================== migração 078
async def test_078_as_linhas_ficam_arvore_e_o_check_recusa_outra_origem(harness: Harness) -> None:
    run = harness.run(["android-01"], command="Abra o QA Messenger.")
    await harness.wait_run(run.id, timeout=60)
    db, repo = harness.state.db, harness.state.repo                                      # type: ignore[union-attr]
    step = db.one("SELECT id, objective_id FROM steps WHERE run_id=? LIMIT 1", (run.id,))
    repo.save_step_output(step["id"], "antigo", "valor")                                # como antes da 078
    linha = db.one("SELECT origem, leitor, frame_sha256, evidence_id FROM step_outputs WHERE name='antigo'")
    assert (linha["origem"], linha["leitor"], linha["frame_sha256"], linha["evidence_id"]) == ("arvore", None, None, None)
    repo.save_step_output(step["id"], "visto", "v", origem="visual", leitor="oa/leitor-x", frame_sha256="a" * 64, evidence_id=7)
    assert repo.saidas_visuais(step["objective_id"]) == {"visto"}
    with pytest.raises(ValueError):                                                      # o repositório recusa antes do banco
        repo.save_step_output(step["id"], "x", "v", origem="visual")
    with pytest.raises(ValueError):
        repo.save_step_output(step["id"], "y", "v", leitor="oa/leitor-x")
    with pytest.raises(Exception):                                                       # e o CHECK do banco recusa por baixo
        db.execute("UPDATE step_outputs SET origem='inventada' WHERE name='antigo'")
    # K-084: `version` é TEXT ("078_…"). `version=78` nunca casava no SQLite (a asserção passava vazia) e quebrava na PG.
    row = db.one("SELECT checksum FROM schema_migrations WHERE version LIKE ?", ("078%",)) if _tem_coluna(db) else None
    assert row is not None and len(row["checksum"]) == 64                                # o sha256 da 078 foi gravado


def _tem_coluna(db: Any) -> bool:
    try:
        db.one("SELECT checksum FROM schema_migrations LIMIT 1")
        return True
    except Exception:                                                                    # noqa: BLE001
        return False
