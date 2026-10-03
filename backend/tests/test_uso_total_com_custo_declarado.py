"""I1 da validação do deploy 7: o `total_usd` de `/api/usage` na mesma base das peças por conta.

No central (03/10, só leitura no banco), as peças "US$ Anthropic + OpenAI + Gemini" somavam US$ 16,60 e o total dizia 16,34. A
diferença, constante em duas leituras, eram as 5 imagens da persona da semana (`gpt-image-2`, US$ 0,2683 DECLARADOS na
linha): `saldos.gasto_usd_por_conta` e `costs.usd_por` contam o custo declarado, e o total só fazia tokens × preço.

Prova `simulated`: linhas de `ai_calls` de mentira no banco do harness.
"""
from __future__ import annotations

import httpx

from app.main import create_app
from app.util import now, to_iso

from .conftest import Harness


async def test_o_total_inclui_o_custo_declarado_e_bate_com_as_contas(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    st.cfg.file.ai.prices.update({"claude-sonnet-5": [2.0, 0.2, 2.5, 10.0]})
    st.cfg.file.ai.prices.pop("gpt-image-2", None)                            # a imagem não tem preço por token
    agora = to_iso(now())
    st.db.execute("INSERT INTO ai_calls(ts, run_id, role, model, provider, input_tokens, output_tokens)"
                  " VALUES (?,?,?,?,?,?,?)", (agora, "r-a", "decide", "claude-sonnet-5", "anthropic", 1_000_000, 0))
    for usd in (0.05, 0.07):
        st.db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, output_tokens, usd)"
                      " VALUES (?,?,?,?,?,?,?)", (agora, "image", "gpt-image-2", "openai", 0, 0, usd))
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        u = (await c.get("/api/usage", params={"days": 7})).json()
    assert u["by_account"] == {"anthropic": 2.0, "openai": 0.12}
    assert u["total_usd"] == round(sum(u["by_account"].values()), 4) == 2.12
    assert "gpt-image-2" not in u["unpriced_models"]                         # coberta pelo custo declarado
    imagem = next(g for g in u["groups"] if g["model"] == "gpt-image-2")
    assert imagem["usd"] == 0.12 and imagem["calls"] == 2
    assert not {"declaradas", "usd_declarado", "p_fresh"} & set(imagem)      # as colunas de conta não vazam


async def test_sem_preco_e_sem_custo_declarado_segue_parcial(harness: Harness) -> None:
    st = harness.state
    assert st is not None
    st.db.execute("INSERT INTO ai_calls(ts, role, model, provider, input_tokens, output_tokens)"
                  " VALUES (?,?,?,?,?,?)", (to_iso(now()), "decide", "modelo-sem-preco", "anthropic", 1000, 10))
    app = create_app(harness.cfg, state=st)
    app.state.poc = st
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        u = (await c.get("/api/usage", params={"days": 7})).json()
    assert "modelo-sem-preco" in u["unpriced_models"]
    assert next(g for g in u["groups"] if g["model"] == "modelo-sem-preco")["usd"] is None
