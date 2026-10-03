"""Rodada C da reverificação do 31.9 (03/10, NO-GO em 8e1d7a9c): os 5 bloqueantes e os importantes do §7 de
`.claude/handoffs/reverificacao-31-9c.md`, com as decisões da orquestradora para a rodada D: "alfabetos misturados" vale
para a FRASE (mais a lista de palavras-chave em outras escritas); numerais por extenso só recusam SEGUIDOS; `@handle` com
hífen vira `[usuario]` inteiro.

Os 27 vazamentos da rodada C entram pelo caminho de produção: `sem_destinos` (o `TargetExtractor`, que devolvia o texto
normalizado quando o comprimento mudava) e depois o consumidor da sombra. Prova `simulated`: funções puras e a sombra com
`DecisorFalso` e banco de teste. Os segredos são de mentira; a chave com a forma de verdade é montada em tempo de execução,
para o literal não existir no código.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.modules.execution.application.target_extractor import CatalogoDeDestinos, PersonaNomeavel, TargetExtractor
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.entidades import remover_entidades_com_motivo
from app.planning.decisao_fechada.intencao import CadeiaObservada, motivo_c7
from app.security.redaction import redact

from .test_decisao_fechada_intencao import Mundo2, porta_aberta  # noqa: F401 - a fixture é usada pelo nome

SEM_DESTINOS = TargetExtractor(CatalogoDeDestinos())
#: A forma de uma chave de acesso de provedor (prefixo + 16), montada aqui para o literal não existir no código.
CHAVE = "AKIA" + "EXEMPLO" * 2 + "AB"


def _sem_destinos(cru: str) -> str:
    return SEM_DESTINOS.extrair(cru).command_sem_destinos


def _motivo(cru: str) -> str | None:
    """O motivo da recusa pelo caminho de produção (sem_destinos -> C7 -> redact -> filtro), ou `None` se passa."""
    texto = _sem_destinos(cru)
    if (c7 := motivo_c7(texto)) is not None:
        return c7
    return remover_entidades_com_motivo(redact(texto) or "")[1]


#: Os 27 vazamentos confirmados da rodada C (`casos-d.json`, n=241 a 267), com o motivo que recusa agora.
VAZAMENTOS_DA_RODADA_C = [
    # bloqueante 2: a palavra-chave em outra escrita ou idioma (a escrita não latina recusa de qualquer jeito)
    ("a пароль do insta é girassol, depois curta o post", "c7_palavra"),
    ("密码是 girassol, entre no Instagram", "c7_palavra"),
    ("パスワード girassol para entrar", "c7_palavra"),
    ("비밀번호 girassol e entre", "c7_palavra"),
    ("κωδικός girassol para o login", "c7_palavra"),
    ("סיסמה girassol e entra", "c7_palavra"),
    ("hasło girassol", "c7_palavra"),
    ("parola girassol", "c7_palavra"),
    # bloqueante 3: colada, abreviada, em leet
    ("a novasenha é girassol", "c7_palavra"),
    ("a senhanova é girassol", "c7_palavra"),
    ("pw: girassol e entre", "c7_palavra"),
    ("psw girassol depois abre o feed", "c7_palavra"),
    ("pa55word girassol", "c7_ofuscado"),
    ("53nh4 girassol", "c7_ofuscado"),
    # bloqueante 4: o par sem verbo de entrar
    ("login: lucas / girassol", "c7_eufemismo"),
    ("usuário lucas, acesso girassol, entra", "c7_eufemismo"),
    # bloqueante 5: e-mail soletrado
    ("manda para zilda at correio net hoje", "email_ofuscado"),
    ("ZILDA AT CORREIO NET", "email_ofuscado"),
    ("manda para zilda at correio dot xyz", "email_ofuscado"),
    ("manda para zilda at correio dot tech", "email_ofuscado"),
    ("manda para zilda at correio punkt de", "email_ofuscado"),
    ("manda para zilda chez correio point net", "email_ofuscado"),
    ("manda para zilda at gmail", "email_ofuscado"),
    # bloqueante 1: o `sem_destinos` normalizava ("ﬁ", acento decomposto, "ß") e a caixa se perdia
    (f"conﬁrme com {CHAVE} no app", "c7_formato"),
    (f"abra o app e cole {CHAVE} no campo de cima, pode ser que né rapido", "c7_formato"),
    ("mande flores para 742 Evergreen Terrace e avise, é urgente", "endereco"),
    ("Straße: 742 Evergreen Terrace, manda flores", "endereco"),
]


@pytest.mark.parametrize(("comando", "motivo"), VAZAMENTOS_DA_RODADA_C)
def test_os_27_vazamentos_da_rodada_c_recusam_pelo_caminho_de_producao(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


def test_os_27_nao_chegam_ao_decisor_e_a_linha_grava_o_motivo(tmp_path: Path, porta_aberta: None) -> None:
    decisor = DecisorFalso()
    w = Mundo2(tmp_path, decisor)
    cadeia = CadeiaObservada(sem_casamento=True)
    for i, (comando, _) in enumerate(VAZAMENTOS_DA_RODADA_C):
        w.consumidor.observar(run_id=f"r-{i}", comando=_sem_destinos(comando), app=None, catalogo=w.catalogo(),
                              cadeia=cadeia)
    w.porta.aguardar_sombras()
    assert decisor.chamadas == []
    por_run = {r["ref"]: (r["fallback_reason"], r["motivo_privacidade"]) for r in w.linhas()}
    assert por_run == {f"r-{i}": ("privacidade", motivo) for i, (_, motivo) in enumerate(VAZAMENTOS_DA_RODADA_C)}
    w.fechar()


# ------------------------------------------------------------------ controles: o que as regras novas NÃO podem recusar
@pytest.mark.parametrize("comando", [
    # "senha" dentro de outra palavra, menos "resenha" e "desenha(r)"
    "escreva uma resenha do filme e poste", "desenhe um gato e mande para a Bia", "redesenhar o banner do perfil",
    "veja o desenho e comente", "o senhor Antônio pediu para curtir",
    # "at" + palavra comum em inglês não é e-mail; o ponto que fecha a frase não é o do domínio
    "look at this app and like the first post", "look at the site and then open the feed", "look at them online",
    "look at the info on the profile", "look at the dot on the map", "stare at the photo. It is great",
    "Look at this. Me too", "read the newest email in Outlook",
    # verbo de tocar com um ou dois números, lista numerada, hashtag, prefixo de chave que é palavra
    "toque no botão de curtir 2 vezes", "aperte o 3 e depois volte", "curta os posts 1, 2 e 3", "poste com #2024 e #tbt",
    "siga o perfil asiatico de culinária", "poste à meia-noite",
    # rótulo sem o par
    "faça login no Instagram", "user lucas quer curtir o post", "conta: lucas", "passe para o próximo post",
])
def test_controles_passam(comando: str) -> None:
    assert _motivo(comando) is None


# ------------------------------------------------------------------ importantes do §7
@pytest.mark.parametrize(("comando", "esperado"), [
    # numeral solto vira `[numero]` (antes, dois no texto recusavam: n=135 e n=205)
    ("mande para Ze Sete e Maria Onze", "mande para Ze [numero] e Maria [numero]"),
    ("publique duas fotos do almoço e marque três pessoas da equipe na legenda",
     "publique [numero] fotos do almoço e marque [numero] pessoas da equipe na legenda"),
    # `@handle` com hífen inteiro (n=119 saía `[usuario]-brandao`); o ponto final fica fora
    ("siga @cassia-brandao no instagram", "siga [usuario] no instagram"),
    ("mande a foto para @renilda.fragoso.", "mande a foto para [usuario]."),
])
def test_numeral_solto_e_handle_com_hifen_mascaram(comando: str, esperado: str) -> None:
    assert remover_entidades_com_motivo(comando) == (esperado, None)


@pytest.mark.parametrize("comando", ["anota: nove oito", "dez, dez", "sete-sete e pronto", "nove / oito"])
def test_numerais_seguidos_recusam(comando: str) -> None:
    assert remover_entidades_com_motivo(comando) == (None, "numerais")


@pytest.mark.parametrize("comando", [
    "na tela que apareceu toque 4, depois 8, depois 2, depois 1, aí abre o feed",                         # n=223
    "no QA Messenger manda pra Dalva Siqueira que o portão da garagem abre com 2580# e que pode entrar",  # n=183
    "digite o 1, o 9 e o 3",
    "toque quatro, depois oito, depois dois",
])
def test_pin_tecla_a_tecla_e_com_cerquilha_e_c7(comando: str) -> None:
    assert motivo_c7(comando) == "c7_digitos"


def test_chave_em_minusculas_e_c7_e_asiatico_nao() -> None:
    assert motivo_c7(f"cole {CHAVE.lower()} no campo") == "c7_formato"
    assert motivo_c7("cole eyj" + "exemplo" * 2 + " no app") == "c7_formato"
    assert motivo_c7("siga o perfil asiatico e curta") is None


@pytest.mark.parametrize(("comando", "motivo"), [
    ("escreva para marina@почта.рф sobre o post", "c7_alfabetos"),   # n=45: antes `[email]`; agora a frase recusa
    ("mande oi para Олег", "c7_alfabetos"),
    ("zilda arroba hotmail", "email_ofuscado"),
    ("manda para zilda at correio . net", "email_ofuscado"),
])
def test_escrita_nao_latina_e_email_soletrado(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


def test_sem_destinos_recorta_do_original_e_nao_normaliza() -> None:
    """Bloqueante 1: com "ﬁ", "ß" ou acento decomposto, o `_normal` muda o comprimento, e o extractor devolvia o texto
    normalizado (minúsculas, sem acento): a chave e o endereço em inglês perdiam a caixa e passavam pelo filtro."""
    x = TargetExtractor(CatalogoDeDestinos(personas=(PersonaNomeavel("p-lucas", ("Lucas",), ()),),
                                           aparelhos=("android-01",)))
    assert x.extrair(f"com a persona Lucas, conﬁrme com {CHAVE} no app").command_sem_destinos == (
        f"conﬁrme com {CHAVE} no app")
    assert x.extrair("com a persona Lucas, curta o post da José no android-01").command_sem_destinos == (
        "curta o post da José")
    assert _sem_destinos("Straße: 742 Evergreen Terrace, manda flores") == "Straße: 742 Evergreen Terrace, manda flores"
    assert _sem_destinos(f"conﬁrme com {CHAVE} no app") == f"conﬁrme com {CHAVE} no app"
