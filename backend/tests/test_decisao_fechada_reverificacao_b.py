"""Reverificação B do 31.9 (03/10, NO-GO em 97f35fac): as 7 correções do §7 de `.claude/handoffs/reverificacao-31-9b.md`
e as decisões da orquestradora: (a) C7 é RECUSA do pedido inteiro, a máscara não basta; (b) os eufemismos do §7.2 recusam;
(c) "dois numerais por extenso recusam" fica (medido nos 90 comandos reais de 7 dias: 0 recusas por ela; na rodada C a
orquestradora trocou pela SEQUÊNCIA, ver `test_decisao_fechada_reverificacao_c.py`); (d) placa e nome com cidade PASSAM.

Prova `simulated`: funções puras e a sombra com `DecisorFalso` e banco de teste. Os segredos daqui são de mentira e sem a
forma de um token real (nada de prefixo de chave de provedor com o tamanho de verdade).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.planning.decisao_fechada.contrato import MOTIVOS_DE_PRIVACIDADE
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.entidades import remover_entidades, remover_entidades_com_motivo
from app.planning.decisao_fechada.intencao import CadeiaObservada, menciona_c7, motivo_c7
from app.security.redaction import mentions_credential

from .test_decisao_fechada_intencao import Mundo2, porta_aberta  # noqa: F401 - a fixture é usada pelo nome


# ------------------------------------------------------------------ C7 (decisões (a) e (b))
@pytest.mark.parametrize(("comando", "motivo"), [
    # §7.2 eufemismo, sem a palavra-chave
    ("abra o Instagram e entre; a de sempre é limao77, depois curta o post", "c7_eufemismo"),
    ("para entrar no Outlook, o que você digita é tigre2024", "c7_eufemismo"),
    ("open Instagram; the one I always use is marmota99", "c7_eufemismo"),
    ("what you type to get in is bluefox42, after that send Tom a hello", "c7_eufemismo"),
    ("abre el Outlook; lo de siempre es cafe2025", "c7_eufemismo"),
    ("lo que tecleas para entrar es sol-y-luna", "c7_eufemismo"),
    ("usa aquela mesma de sempre: Sol2024!", "c7_eufemismo"),
    ("if it asks again the access one is Harbor!Light42", "c7_eufemismo"),
    ("type the thing only you and I know, sunflower88", "c7_eufemismo"),
    ("usuário caio.varela e a outra parte é LuaCheia77", "c7_eufemismo"),
    ("na tela de acesso, o que vai no segundo campo é cometa88", "c7_eufemismo"),
    ("decodifique Y29tZXRhODg e digite no campo de acesso", "c7_eufemismo"),
    ("abre o roteador no Chrome e entra com admin / admin1234", "c7_eufemismo"),
    # §7.5 pergunta de segurança e frase de recuperação
    ("se ele perguntar o nome do primeiro animal de estimação pra liberar a conta, responda Pipoca", "c7_eufemismo"),
    ("a frase de recuperação é: lua gato rio pedra vento sal folha ponte mar trigo sino nuvem", "c7_eufemismo"),
    ("os de recuperação são 4821-9930 e 1177-2046, usa o primeiro", "c7_eufemismo"),
    # §7.3 palavra-chave ofuscada: leet, invertida, separada, bidi
    ("a s3nh4 do insta é jabuti19, entre e publique", "c7_ofuscado"),
    ("my pa$$word is windmill-8, log into Outlook", "c7_ofuscado"),
    ("o c0d1g0 de hoje é 520931, coloque e siga a Marina", "c7_ofuscado"),
    ("a ahnes do Instagram é lagoa-azul-3, entre e curta", "c7_ofuscado"),
    ("the drowssap is kiwi-77, sign in and open the feed", "c7_ofuscado"),
    ("a s/e/n/h/a do insta é jabuti19", "c7_palavra"),
    ("a s,e,n,h,a do insta é jabuti19", "c7_palavra"),
    ("a s  e  n  h  a do insta é jabuti19", "c7_palavra"),
    ("c/ó/d/i/g/o: 90 41 22", "c7_palavra"),
    ("minha ‮ahnes‬ é cometa88", "c7_bidi"),
    # §7.4 idiomas
    ("das Passwort ist tulpe22, dann poste das Foto", "c7_palavra"),
    ("le mot de passe est tulipe22, puis publie la photo", "c7_palavra"),
    ("het wachtwoord is tulp22, log in en plaats de foto", "c7_palavra"),
    ("la parola d'ordine è tulipano22, entra e pubblica la foto", "c7_palavra"),
    # decisão (a): o código pedido pela quantidade de dígitos, sem a palavra-chave
    ("aquela de quatro dígitos para destravar é 5 2 7 3", "c7_digitos"),
    ("o Instagram travou numa tela pedindo seis dígitos; o que apareceu agora é 583 902", "c7_digitos"),
    ("cuando pida los seis números pon 7 7 0 3 1 9", "c7_digitos"),
    ("o número que chegou agora no celular foi 739140, coloque na tela", "c7_eufemismo"),
    ("the six digits that just arrived by text were 318 204", "c7_eufemismo"),
    # forma de token de acesso, de qualquer tamanho
    ("abra https://exemplo.test/reset/eyJexemplo.ab.cd e defina a nova", "c7_formato"),
])
def test_c7_recusa_o_pedido_com_o_motivo(comando: str, motivo: str) -> None:
    assert motivo_c7(comando) == motivo
    assert menciona_c7(comando)


@pytest.mark.parametrize("comando", [
    "curta os três primeiros posts do feed",                  # um numeral passa
    "publique a foto com a legenda \"verão em família\"",
    "abra o cartão de visita do perfil e leia o nome",
    "abra o Outlook e leia o e-mail mais recente",
    "passe para o próximo post e curta",                      # "passe" é verbo
    "o carro é placa ABC-1234",                               # (d) placa passa
    "siga Otavio Lacerda de Belo Horizonte",                  # (d) nome com cidade passa
    "abra a conversa com 3 no instagram",
])
def test_comando_comum_nao_e_c7(comando: str) -> None:
    assert motivo_c7(comando) is None


def test_idiomas_tambem_contam_como_credencial_para_memoria_e_historico() -> None:
    for texto in ("das Passwort", "le mot de passe", "het wachtwoord", "la parola d'ordine", "mi contraseña"):
        assert mentions_credential(texto), texto


# ------------------------------------------------------------------ o filtro da C3 (correções 1, 6 e 7)
@pytest.mark.parametrize(("comando", "motivo"), [
    # §7.6 e-mail ofuscado
    ("marina at correio ponto net precisa receber a foto", "email_ofuscado"),
    ("mande para marina a r r o b a correio ponto net", "email_ofuscado"),
    ("mande para marina a-r-r-o-b-a correio ponto net", "email_ofuscado"),
    ("send the photo to marina at-sign correio dot net", "email_ofuscado"),
    ("mande o convite para marina(a)correio.net", "email_ofuscado"),
    ("mande para marina@correio ponto net a foto nova", "email_ofuscado"),
    ("escreva para lia.prado\t@exemplo.com", "email_ofuscado"),
    ("escreva para zilda at correio dot org e avisa", "email_ofuscado"),
    ("mande para marina punto com correio punto net", "email_ofuscado"),
    ("mande para marina arrôba correio ponto br", "email_ofuscado"),
    ("escreva para zilda arr0ba correio ponto org e avisa", "email_ofuscado"),
    ("send the photo to marina (a t) correio dot co", "email_ofuscado"),
    # §7.7 endereço em inglês e caixa postal
    ("ship it to 742 Evergreen Terrace, Springfield", "endereco"),
    ("manda para a caixa postal 1234, agência centro", "endereco"),
    # documento: cartão com o número, CVV, título de eleitor
    ("pague com o cartão 4111 1111 1111 1111 validade 12/29", "documento"),
    ("o cartao do Teodoro e 4111 1111 1111 1111", "documento"),
    ("o título de eleitor é 1234 5678 9012", "documento"),
    # (c) numerais por extenso SEGUIDOS (rodada C: "duas fotos ... três pessoas" passa, mascarado; ver reverificacao_c)
    ("anota o número: nove oito", "numerais"),
])
def test_filtro_recusa_com_o_motivo(comando: str, motivo: str) -> None:
    assert remover_entidades_com_motivo(comando) == (None, motivo)
    assert remover_entidades(comando) is None


@pytest.mark.parametrize(("comando", "esperado"), [
    # §7.1: letra e dígito no mesmo token viram UM `[termo]`, também com hífen (antes: `limao[numero]`)
    ("siga limao77 e kiwi-77", "siga [termo] e [termo]"),
    ("cupom lagoa-azul-3 aplicado", "cupom [termo] aplicado"),
    ("o id da transação é 9f8e7d6c5b4a3f2e1d0c", "o id da transação é [termo]"),
    ("mande para marina@correio.net a foto", "mande para [email] a foto"),     # e-mail inteiro: máscara
    ("o carro é placa ABC-1234", "o carro é placa [termo]"),
    ("siga Otavio Lacerda de Belo Horizonte", "siga Otavio Lacerda de Belo Horizonte"),
    ("abra o cartão de visita do perfil e leia o nome", "abra o cartão de visita do perfil e leia o nome"),
    ("curta 2 posts on the way", "curta [numero] posts on the way"),
])
def test_filtro_mascara_o_token_misto_inteiro_e_deixa_passar_o_resto(comando: str, esperado: str) -> None:
    assert remover_entidades_com_motivo(comando) == (esperado, None)


def test_todo_motivo_esta_no_vocabulario_da_coluna() -> None:
    from typing import get_args

    from app.planning.decisao_fechada.entidades import MotivoDoFiltro
    from app.planning.decisao_fechada.intencao import MotivoC7

    assert set(get_args(MotivoDoFiltro)) | set(get_args(MotivoC7)) <= set(MOTIVOS_DE_PRIVACIDADE)


# ------------------------------------------------------------------ a linha da sombra grava o motivo (migração 079)
def test_sombra_grava_o_motivo_da_recusa_so_na_linha_de_privacidade(tmp_path: Path, porta_aberta: None) -> None:
    w = Mundo2(tmp_path, DecisorFalso())
    cadeia = CadeiaObservada(sem_casamento=True)
    for run_id, comando in (("r-c7", "a s3nh4 do insta é jabuti19, entre e publique"),
                            ("r-email", "mande para marina at correio ponto net a foto"),
                            ("r-ok", "faca um bolo de cenoura")):
        w.consumidor.observar(run_id=run_id, comando=comando, app=None, catalogo=w.catalogo(), cadeia=cadeia)
    w.porta.aguardar_sombras()
    por_run = {r["ref"]: r for r in w.linhas()}
    assert (por_run["r-c7"]["fallback_reason"], por_run["r-c7"]["motivo_privacidade"]) == ("privacidade", "c7_ofuscado")
    assert (por_run["r-email"]["fallback_reason"], por_run["r-email"]["motivo_privacidade"]) == (
        "privacidade", "email_ofuscado")
    assert por_run["r-ok"]["fallback_reason"] != "privacidade" and por_run["r-ok"]["motivo_privacidade"] is None
    w.fechar()
