"""Rodada E da reverificação do 31.9 (03/10, NO-GO em 963f9d7b): as decisões E-A a E-C de
`.claude/handoffs/reverificacao-31-9d.md` (§ Decisões da orquestradora).

- E-A: a C7 sem palavra-chave da lista. Agora entram as traduções de "senha" em escrita latina, os eufemismos novos e a
  regra ESTRUTURAL de intenção de entrar (`c7_login_valor`, `c7_par_credencial`), conferida também no comando original.
- E-B: e-mail ditado em peças em português ("zilda no gmail", "o usuário é zilda e o domínio é correio.net") recusa.
- E-C: a máscara do e-mail engole a parte local inteira, o `mailto:`, o `?subject=` e o domínio de topo solto; a do
  telefone engole o `tel:`.

Prova `simulated`: funções puras e a sombra com `DecisorFalso` e banco de teste. Os valores são de mentira.
"""
from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any

import pytest

from app.modules.execution.application.target_extractor import CatalogoDeDestinos, PersonaNomeavel, TargetExtractor
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.entidades import remover_entidades_com_motivo
from app.planning.decisao_fechada.intencao import CadeiaObservada, motivo_c7, nomes_de_destino
from app.security.redaction import redact

from .conftest import Harness
from .test_decisao_fechada_intencao import Mundo2, porta_aberta  # noqa: F401 - a fixture é usada pelo nome

#: O catálogo de destinos do harness da orquestradora (as três contas vivas).
DESTINOS = CatalogoDeDestinos((
    PersonaNomeavel("p-lucas", ("Lucas", "Lucas Almeida", "Lucas Almeida"), ("lucas.almeida9484",)),
    PersonaNomeavel("p-bruno", ("Bruno", "Bruno Ferreira", "Bruno Ferreira"), ("bruno.ferreira9267",)),
    PersonaNomeavel("p-andre", ("André", "André Carvalho", "André Carvalho"), ("andre.carvalho9543",)),
), tuple(f"android-{i:02d}" for i in range(1, 9)))
EXTRATOR = TargetExtractor(DESTINOS)
#: Os nomes do catálogo como chegam ao filtro (rodada F): o que `RunService.dados_da_sombra` passa no 5º item.
NOMES = nomes_de_destino([*(n for p in DESTINOS.personas for n in (*p.nomes, *p.handles)), *DESTINOS.aparelhos])


def _sem_destinos(cru: str) -> str:
    return EXTRATOR.extrair(cru).command_sem_destinos


def _motivo(cru: str) -> str | None:
    """Como o consumidor decide: C7 no texto sem destinos e no original (rodada F: a intenção de entrar só no original, que
    mostra o destino, e os nomes do catálogo valem como destino); depois `redact` e o filtro da C3."""
    texto = _sem_destinos(cru)
    com_original = texto != cru
    if (c7 := motivo_c7(texto, intencao=not com_original, destinos=NOMES)
            or (motivo_c7(cru, sem_destinos=texto, destinos=NOMES) if com_original else None)) is not None:
        return c7
    return remover_entidades_com_motivo(redact(texto) or "")[1]


#: Os 25 vazamentos da rodada D (§3.1 e §3.2), com o motivo que recusa agora.
VAZAMENTOS_DA_RODADA_D = [
    # palavra-chave variante ou formato chave-valor
    ("a palavra secreta e girassol, entra e curte", "c7_palavra"),
    ("pswd: girassol e entra", "c7_palavra"),
    ("e pra entrar: girassol", "c7_login_valor"),
    ("the magic word is girassol, log in and like", "c7_eufemismo"),
    ("o lema de acesso e girassol", "c7_eufemismo"),
    ("a de acesso e girassol", "c7_eufemismo"),
    ("a combinacao e girassol, entra e curte", "c7_eufemismo"),
    # "senha" em outra língua de escrita latina
    ("entre no insta, contrasenya girassol, e curta a foto", "c7_palavra"),
    ("a parool do insta e girassol, entra e curte", "c7_palavra"),
    ("kata sandi girassol, depois abre o feed", "c7_palavra"),
    ("mật khẩu girassol, entra e segue a Marina", "c7_palavra"),
    (unicodedata.normalize("NFD", "mật khẩu girassol, entra e segue a Marina"), "c7_palavra"),       # decomposto
    ("nenosiri girassol, entra e segue a Marina", "c7_palavra"),
    ("slaptazodis girassol, entra", "c7_palavra"),
    # verbo de entrar com valor, par de usuário e senha
    ("entre usando girassol e curta a foto da Marina", "c7_login_valor"),
    ("faca login com girassol e depois curta", "c7_login_valor"),
    ("entre com girassol e curta a foto", "c7_login_valor"),
    ("usuario lucas e girassol, entra", "c7_par_credencial"),
    ("lucas, girassol, entra e curte", "c7_par_credencial"),
    ("digita girassol embaixo do usuario", "c7_eufemismo"),
    ("entre com a conta Lucas / girassol", "c7_par_credencial"),    # sem destinos: "entre / girassol"
    ("entre com a conta admin / girassol", "c7_par_credencial"),
    # e-mail ditado em peças em português
    ("usuario zilda no gmail, manda a foto pra ela", "email_ofuscado"),
    ("mande para zilda no hotmail", "email_ofuscado"),
    ("mande para zilda do outlook", "email_ofuscado"),
]

#: A C7 que a rodada D mandava MASCARADA: agora recusa (C7 é recusa do pedido inteiro).
C7_QUE_IA_MASCARADO = [
    ("a mesma de ontem e limao77, entra", "c7_eufemismo"),
    ("los numeros que llegaron al movil fueron 7319, ponlos y sigue a Rodrigo", "c7_eufemismo"),
    ("entre na conta; aquilo que combinamos pelo telefone ontem e 7730, usa quando ele pedir", "c7_eufemismo"),
    ("entre com o usuário admin / abc123", "c7_par_credencial"),
]


@pytest.mark.parametrize(("comando", "motivo"), VAZAMENTOS_DA_RODADA_D + C7_QUE_IA_MASCARADO)
def test_os_vazamentos_da_rodada_d_recusam(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


def test_o_par_partido_pelo_sem_destinos_e_visto_no_original() -> None:
    """E-A(4): "entre com a conta Lucas / girassol" vira "entre / girassol" sem destinos; os dois recusam."""
    assert _sem_destinos("entre com a conta Lucas / girassol") == "entre / girassol"
    assert motivo_c7("entre / girassol") == "c7_par_credencial"
    assert motivo_c7("entre com a conta Lucas / girassol", sem_destinos="entre / girassol") == "c7_par_credencial"


def test_o_consumidor_confere_o_original_e_nao_o_envia(tmp_path: Path, porta_aberta: None) -> None:
    """Com o texto sem destinos limpo e o original com o par, a sombra recusa; o original nunca entra no estado."""
    decisor = DecisorFalso()
    w = Mundo2(tmp_path, decisor)
    cadeia = CadeiaObservada(sem_casamento=True)
    # o par atravessando o destino: tirar "com a conta Lucas" deixa "entre e girassol", e só o original mostra o par
    par = "entre com a conta Lucas e girassol, curta a foto da Marina"
    assert _sem_destinos(par) == "entre e girassol, curta a foto da Marina"
    w.consumidor.observar(run_id="r-par", comando=_sem_destinos(par), app=None, catalogo=w.catalogo(), cadeia=cadeia,
                          original=par, destinos=sorted(NOMES))
    # o original com "com <destino>" não recusa, e o que vai ao decisor é o texto sem destinos, nunca o original
    destino = "entre com a conta Lucas e curta a foto da Marina"
    w.consumidor.observar(run_id="r-destino", comando=_sem_destinos(destino), app=None, catalogo=w.catalogo(),
                          cadeia=cadeia, original=destino, destinos=sorted(NOMES))
    w.porta.aguardar_sombras()
    por_run = {r["ref"]: (r["fallback_reason"], r["motivo_privacidade"]) for r in w.linhas()}
    assert por_run["r-par"] == ("privacidade", "c7_par_credencial")
    assert [c.estado for c in decisor.chamadas] == [{"comando": "entre e curta a foto da Marina"}]
    w.fechar()


# ------------------------------------------------------------------ controles: o que as regras novas NÃO podem recusar
@pytest.mark.parametrize("comando", [
    # verbo de entrar com objeto de navegação, ou "com" que é a pessoa, o modo ou o provedor de entrada
    "entre no perfil da Marina e curta", "acesse o perfil da Ana e curta a última foto",
    "entre na conversa com qa-001 e mande oi", "entre no chat com a Marina", "entre com o lucas e curta",
    "entre com o Google e abra o feed", "entre no perfil com calma e curta", "entre com lucas e curta",
    "faça login no app e abra o feed", "log in to the app and like the post", "inicie sessão no app",
    "acesse a conta e curta", "entre na conta do lucas e curta a foto da marina", "use o lucas pra curtir a foto da marina",
    "pesquise por girassol e entre no primeiro perfil", "curta as fotos postadas entre 10/05 e 12/05",
    # eufemismo que só é C7 com verbo de entrar sem navegação
    "a combinação de cores ficou boa", "passe para o próximo post", "siga o usuario marina e curta",
    # provedor de e-mail que também é app, live do Instagram ou palavra comum
    "entra no outlook e lê o e-mail", "comenta no live da Marina", "abra o instagram e entre no live da marina",
    "mande um e-mail no outlook para a equipe", "abra a caixa de entrada do outlook e leia o último e-mail",
    "vai no outlook e responde", "a foto da terra vista do espaço", "o post do girassol",
])
def test_controles_passam(comando: str) -> None:
    assert _motivo(comando) is None


def test_o_nome_do_catalogo_desfaz_o_com() -> None:
    """Rodada F (F-B): o filtro recebe os nomes do catálogo real. "entre com lucas" é destino; "entre com girassol", que tem a
    mesma forma, é valor. Sem o catálogo, os dois recusam (o desvio declarado da rodada E)."""
    assert _motivo("entre com lucas e curta") is None
    assert _motivo("entre com girassol e curta") == "c7_login_valor"
    assert motivo_c7("entre com lucas e curta") == "c7_login_valor"


@pytest.mark.parametrize("comando", ["abre o insta, entra e curte", "entre e comente com parabéns"])
def test_entrar_sem_objeto_de_navegacao_pula_a_sombra(comando: str) -> None:
    """Rodada F (F-A): eram controles da rodada E. O verbo de entrar sem objeto de navegação faz a sombra pular o comando; é
    o custo de utilidade aceito (nos 98 comandos reais de 7 dias, a F-A sozinha pulou 0)."""
    assert _motivo(comando) == "c7_intencao_de_entrar"


@pytest.mark.parametrize(("comando", "motivo"), [
    # E-A(1): "senha" em mais línguas de escrita latina, também colada e com acento
    ("lykilorð girassol", "c7_palavra"), ("cyfrinair girassol", "c7_palavra"), ("geslo girassol", "c7_palavra"),
    ("açarsöz girassol", "c7_palavra"), ("kata laluan girassol", "c7_palavra"), ("novacontrasenya girassol", "c7_palavra"),
    # E-A(2): credencial pelo nome e eufemismos
    ("a credencial é girassol", "c7_palavra"), ("palavra mágica: girassol", "c7_eufemismo"),
    ("os números que chegaram são 4471", "c7_eufemismo"), ("a combinação é tulipa", "c7_eufemismo"),
    ("passe girassol e entra", "c7_eufemismo"),
    # E-A(3): a regra estrutural
    ("entra no insta com girassol", "c7_login_valor"), ("acesse o app usando tulipa", "c7_login_valor"),
    ("use girassol pra entrar", "c7_login_valor"), ("sign in with tulipa and open the feed", "c7_login_valor"),
    ("usuario: lucas / girassol", "c7_eufemismo"), ("user lucas, girassol, log in", "c7_par_credencial"),
])
def test_formas_novas_da_c7(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


# ------------------------------------------------------------------ E-B: e-mail em peças em português
@pytest.mark.parametrize("comando", [
    "zilda no gmail", "zilda, no icloud", "zilda no live, manda o link do perfil", "zilda do yahoo",
    "mande para zilda em correio.net", "o usuário é zilda e o domínio é correio.net", "usuario zilda no hotmail",
])
def test_email_em_pecas_em_portugues_recusa(comando: str) -> None:
    assert _motivo(comando) == "email_ofuscado"


# ------------------------------------------------------------------ E-C: a máscara inteira
@pytest.mark.parametrize(("comando", "esperado"), [
    ("mande para abcdef#zilda@correio.net hoje", "mande para [email] hoje"),
    ("escreva para o'brien@correio.net", "escreva para [email]"),
    ("mande para zilda@correio .net amanha", "mande para [email] amanha"),
    ("escreva para mailto:joao.silva@exemplo.com?subject=oi", "escreva para [email]"),
    ("escreva para MAILTO:JOAO.SILVA@EXEMPLO.COM?subject=oi", "escreva para [email]"),
    ("liga no tel:+5511912345678 agora", "liga no [telefone] agora"),
    ("mande para Zilda Prado <zilda@correio.net>, hoje", "mande para Zilda Prado <[email]>, hoje"),
    ("cole a linha Zilda Prado,zilda@correio.net e salva", "cole a linha Zilda Prado,[email] e salva"),
])
def test_mascara_engole_o_endereco_inteiro(comando: str, esperado: str) -> None:
    assert remover_entidades_com_motivo(redact(comando) or "") == (esperado, None)


# ------------------------------------------------------------------ a ligação: o original chega à sombra
async def test_a_sombra_recebe_tambem_o_comando_original(harness: Harness) -> None:
    """`dados_da_sombra` leva o comando original como 4º item; `dados_da_intencao` (o do 30.25) segue com 3."""
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    st.runs.sem_destinos = lambda c: "LIMPO:" + c                              # type: ignore[method-assign]
    dados = st.runs.dados_da_sombra(run.id)
    assert dados is not None and dados[0] == "LIMPO:abra o aplicativo de configuracoes"
    assert dados[3] == "abra o aplicativo de configuracoes"
    assert st.runs.dados_da_intencao(run.id) == dados[:3]
    recebidos: list[tuple[Any, ...]] = []

    class SombraEspia:
        def ativo(self) -> bool:
            return True

        def agendar(self, run_id: str, ler: Any) -> None:
            recebidos.append(ler())

        def cancelar(self) -> None:
            pass

    st.runs.sombra_intencao = SombraEspia()                                    # type: ignore[assignment]
    st.runs._intencao_em_sombra(run.id)                                        # noqa: SLF001
    assert recebidos == [dados]
