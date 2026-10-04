"""28.27: a prévia da porta pelo canal (parte pura, `app.modules.avisos.domain.porta`).

Prova `simulated`: só o domínio, sem banco, fila nem Telegram. A conversa com a porta está em `test_telegram_entrada.py`
e as portas reais em `test_telegram_portas.py`.
"""
from __future__ import annotations

from app.modules.avisos.domain import porta as p
from app.modules.avisos.domain.mensagem import partes_da_aprovacao


def _redigir(texto: str) -> str:
    return texto


def _item(sid: str, selo: str = "aprovacao", **kw: object) -> dict[str, object]:
    base: dict[str, object] = {"step_id": sid, "selo": selo, "chave": f"{sid:0<64}", "titulo": "Comentar no post",
                               "alvo": "post 123", "texto": "oi, tudo bem?", "aparelho": "android-09"}
    base.update(kw)
    return base


def _sem_imagem(_item: object) -> bool:
    raise AssertionError("só o item com imagem confere imagem")


def test_ler_porta_separa_o_sim_pelo_canal_do_resto() -> None:
    previa = {"itens": [_item("a"), _item("b", selo="permitido"), _item("c", selo="na_execucao"),
                        _item("d", chave=None), _item("e", texto="x" * (p.TEXTO_MAX_NO_CANAL + 1))]}
    leitura = p.ler_porta(previa, [], _redigir, _sem_imagem)
    assert leitura.aprovar == [("a", f"{'a':0<64}")]
    assert leitura.fora_do_canal == ["d", "e"] and leitura.na_execucao == 3


def test_imagem_so_entra_conferida() -> None:
    previa = {"itens": [_item("a", tem_imagem=True, imagem_sha256="1" * 64)]}
    assert p.ler_porta(previa, [], _redigir, lambda _i: False).fora_do_canal == ["a"]
    assert p.ler_porta(previa, [], _redigir, lambda _i: True).aprovar == [("a", f"{'a':0<64}")]


def test_o_item_mostra_o_alvo_e_o_texto_pela_regra_da_aprovacao_pendente() -> None:
    """A linha do item é a mesma da mensagem de aprovação pendente (`partes_da_aprovacao`), sem corte."""
    item = _item("a", texto="y" * 900)
    previa = {"itens": [item]}
    leitura = p.ler_porta(previa, [], _redigir, _sem_imagem)
    texto = "\n\n".join(p.mensagens_da_porta(previa, "abc123", leitura, [], _redigir))
    for parte in partes_da_aprovacao("post 123", "y" * 900, [], _redigir, maximo=None):
        assert f"   {parte}" in texto
    assert "1. 🔒 Comentar no post · android-09: pede o seu sim" in texto


def test_mensagens_cortadas_sem_partir_um_bloco() -> None:
    itens = [_item(f"s{n}", texto="z" * 500) for n in range(20)]
    previa = {"itens": itens, "na_execucao": {"itens_for_each": 2}, "validade_ate": "2026-10-05T22:40:00Z"}
    leitura = p.ler_porta(previa, [], _redigir, _sem_imagem)
    mensagens = p.mensagens_da_porta(previa, "abc123", leitura, [], _redigir)
    assert len(mensagens) > 1 and all(len(m) <= p.LIMITE for m in mensagens)
    junto = "\n\n".join(mensagens)
    for n in range(1, 21):
        assert junto.count(f"\n{n}. 🔒 ") + junto.startswith(f"{n}. 🔒 ") == 1
    assert "O sim vale até 22:40Z." in mensagens[0]
    assert mensagens[-1].endswith("2 etapas nascem da coleta; o sim é pedido na execução.")


def test_linha_sem_aprovacao() -> None:
    assert p.linha_sem_aprovacao("abc123", p.LeituraDaPorta()) == (
        "Execução abc123 iniciada: o plano não tem aprovação pendente. Conto aqui quando terminar.")
    assert "2 itens vão pedir você" in p.linha_sem_aprovacao("abc123", p.LeituraDaPorta(na_execucao=2))
