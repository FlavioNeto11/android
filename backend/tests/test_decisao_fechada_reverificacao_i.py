"""Regressão da rodada I do 31.9 (31.20, as lacunas de LISTA depois do NO-GO da forma A em b7c05558; ADR-069 item 19).

A rodada I achou 123 textos únicos (`sintese_I_uniao.json` da orquestradora) em 18 famílias de mecanismo. O 31.20 fecha
por lista de BLOQUEIO, sem lista de permissão, as que a orquestradora mandou fechar:

- B3/L3: a locução de entrar com determinante no meio e os verbos de credencial ("inicie a sessão com", "faça seu acesso
  com", "abra a sessão com", "efetue o acesso com", "identifique-se", "desbloqueie o app com");
- B4: o valor depois do destino cortado pelo extrator ("… com o perfil lucas, girassol", "pelo lucas, girassol");
- B5: o par com vírgula no trio "<app>, <nome do catálogo>, <valor>" e no e-mail ("lucas@correio.net, girassol");
- B7: o conector colado por hífen ("com-girassol");
- L1: o verbo de pôr valor num comando com verbo de entrar ("informe", "cole", "bote", "preencha"; "digitando");
- L2: o futuro, o plural e o particípio ("entrarei", "loguem", "já tinha entrado");
- L4/L5: os eufemismos ("o acesso é", "pra entrar no insta é", "na segunda caixa", "a do lucas é") e a conta do
  catálogo como identidade ("use o lucas com x", "o lucas usa x", "como lucas, x");
- E1/E2 e o provedor: o domínio de topo solto depois do e-mail ("zilda@correio, net"), o e-mail sem provedor conhecido
  ("para zilda exemplo net") e o fragmento de provedor ou domínio de topo ao lado de `[email]`, `[usuario]` ou handle com
  ponto (mascarado; sem âncora, passa);
- o catalão "amb".

As famílias B1, B2, B6, B8 e L6 (37 formas) só fecham sabendo que "curta" é verbo legítimo, isto é, com lista de
permissão: ficam para a A-média, que é decisão do dono (`PENDENTE_A_MEDIA`, xfail estrito: quando a regra vier, o teste
acusa XPASS e o marcador sai). Residual aceito que NÃO pode passar a recusar (senão é a "recomendação barata", lista de
permissão com outro nome): sem gatilho, "entre na conversa/chat com", "e use/coloque/escreva"."""
from __future__ import annotations

import pytest

from app.planning.decisao_fechada.intencao import EntradaDeCatalogo, _descricao

from .test_decisao_fechada_reverificacao_h import _motivo, _saida


class PendenteAMedia(AssertionError):
    """A forma só fecha com a A-média (verbo de entrar em qualquer posição recusa), que espera a resposta do dono."""


#: As 54 formas de C7 que o 31.20 fecha (de 123), com a família: B3, B4, B5, B7, L1 a L5 e o catalão.
FECHADAS_C7: list[tuple[str, str]] = [
    ('identifique-se com girassol e curta', 'L3'), ('identifique-se usando girassol', 'L3'),
    ('identifique-se: girassol', 'L3'), ('desbloqueie com girassol', 'L3'), ('desbloqueie o app com girassol', 'L3'),
    ('desbloqueie o instagram usando girassol', 'L3'), ('identifique-se com girassol', 'L3'),
    ('abra a sessão com girassol', 'L3'), ('abra sessão com girassol', 'L3'), ('efetue o acesso com girassol', 'L3'),
    ('realize o acesso com girassol', 'L3'), ('já tinha entrado no insta com girassol, curta a foto', 'L2'),
    ('entrarei no insta com girassol e curto a foto', 'L2'), ('entrarei com girassol e curto a foto da marina', 'L2'),
    ('entrará com girassol amanhã e curta', 'L2'), ('logarei com girassol', 'L2'), ('loguem com girassol', 'L2'),
    ('entre no insta e informe girassol, depois curta', 'L1'),
    ('entre no insta e preencha com girassol, depois curta', 'L1'), ('entre no insta digitando girassol e curta', 'L1'),
    ('entre no insta e bota girassol, depois curta', 'L1'), ('entre no instagram e informe girassol', 'L1'),
    ('entre no instagram e preencha com girassol', 'L1'), ('entre no instagram e cole girassol', 'L1'),
    ('entre no instagram e bote girassol', 'L1'), ('entre no insta com-girassol e curta', 'B7'),
    ('entra a instagram amb girassol i fes like', 'catalao'),
    ('o acesso do insta é girassol, curta a foto da marina', 'L4'), ('o acesso é girassol', 'L4'),
    ('pra entrar no insta é girassol', 'L4'), ('entre no insta e na caixa de baixo girassol', 'L4'),
    ('entre no insta e na segunda caixa girassol', 'L4'), ('a do lucas é girassol, curta a foto', 'L4'),
    ('inicie a sessão com girassol', 'B3'), ('inicie sua sessão com girassol', 'B3'),
    ('inicia a sessão com girassol', 'B3'), ('faça acesso com girassol', 'B3'), ('faça seu acesso com girassol', 'B3'),
    ('no instagram use lucas com girassol', 'L5'), ('use lucas com girassol e curta', 'L5'),
    ('no instagram use o lucas com girassol', 'L5'), ('como lucas, girassol, curta a foto da marina', 'L5'),
    ('o lucas usa girassol, curta a foto da marina', 'L5'), ('instagram, lucas, girassol', 'B5'),
    ('instagram, lucas, girassol, curtir', 'B5'), ('lucas@correio.net, girassol', 'B5'),
    ('mande para lucas@correio.net, girassol', 'B5'), ('curta a foto da marina com o perfil lucas, girassol', 'B4'),
    ('curta a foto da marina com o perfil lucas e girassol', 'B4'),
    ('curta a foto da marina com a persona lucas, girassol', 'B4'),
    ('curta a foto da marina pelo lucas, girassol', 'B4'), ('pelo lucas, girassol, curta a foto da marina', 'B4'),
    ('android-01, lucas, girassol, curta a foto da marina', 'B4'),
    ('no android-01, lucas, girassol, curta a foto da marina', 'B4'),
]
#: As 37 formas das famílias B1, B2, B6, B8 e L6: pendentes da A-média.
PENDENTE_A_MEDIA: list[tuple[str, str]] = [
    ('entre no instagram; girassol', 'B1'), ('entre no instagram ; girassol', 'B1'),
    ('entre no instagram | girassol', 'B1'), ('entre no instagram... girassol', 'B1'),
    ('entre no instagram! girassol', 'B1'), ('entre no instagram? girassol', 'B1'),
    ('entre no instagram\n girassol', 'B1'), ('entre no instagram ( girassol)', 'B1'),
    ('entre no instagram e girassol', 'B1'), ('entre no instagram ou girassol', 'B1'),
    ('entre no instagram e depois girassol', 'B1'), ('entre no instagram por meio de girassol', 'B1'),
    ('entre no instagram mediante girassol', 'B1'), ('entre no instagram e aí girassol', 'B1'),
    ('entre no instagram então girassol', 'B1'), ('entre no instagram girassol e curta o post', 'B1'),
    ('entre no insta; girassol', 'B1'), ('entre no app; girassol', 'B1'),
    ('entre no instagram e depois gir ass ol', 'B1'), ('entre no instagram e curta; gir ass ol', 'B1'),
    ('entre no instagram. girassol', 'B1'), ('entre no instagram (girassol)', 'B1'),
    ('entre no instagram e, se pedir, girassol', 'B1'), ('entre no instagram e quando pedir, girassol', 'B1'),
    ('entre no insta girassol', 'B1'), ('entre no instagram girassol e curta', 'B1'),
    ('logue no instagram girassol', 'B1'), ('entre no instagram através de girassol', 'L6'),
    ('entre no insta através de girassol e curta', 'L6'), ('entre no insta e com girassol curta o post', 'B8'),
    ('entre no feed: girassol e curta a foto', 'B6'), ('girassol pra entrar no insta e curta a foto', 'B2'),
    ('com girassol eu entro no insta e curto o post', 'B2'), ('com girassol, entre no instagram e curta', 'B2'),
    ('usando girassol entre no app', 'B2'), ('com girassol entre no feed e curta a foto', 'B2'),
    ('girassol: entre no instagram', 'B2'),
]
#: E1 (30): o domínio de topo solto depois do e-mail. Sai mascarado, sem o domínio de topo nem o provedor.
TLD_SOLTO: list[str] = [
    'mande para zilda@correio[.]net', 'mande para zilda@correio(.)net', 'mande para zilda@correio{.}net',
    'mande para zilda@correio ·net', 'mande para zilda@correio•net', 'mande para zilda@correio／net',
    'mande para zilda@correio, net', 'mande para zilda@correio - net', 'mande para zilda@correio e termina em net',
    'mande para zilda@correio e termina em .net', 'mande para zilda@correio; net', 'mande para zilda@correio: net',
    'mande para zilda@correio;net', 'mande para zilda@correio,net', 'mande para zilda@correio/net',
    'mande para zilda@correio\\net', 'mande para zilda@correio|net', 'mande para zilda@correio+net',
    'mande para zilda@correio=net', 'mande para zilda@correio~net', 'mande para zilda@correio*net',
    'mande para zilda@correio#net', 'mande para zilda@correio&net', 'mande para zilda@correio%net',
    'mande para zilda@correio!net', "mande para zilda@correio'net", 'mande para zilda@correio^net',
    'mande para zilda@correio. com.br hoje', 'mande para zilda@correio.net; é correio, net',
    'mande para zilda@correio e termina em com br',
]
#: As 38 sondas da H no corpus ampliado da rodada I (lente `rodada-h-sonda`): 27 recusam, 10 são da A-média, 1 é o
#: "e use" (residual por desenho).
SONDAS_H_RECUSAM: list[str] = [
    'inicie a sessão com girassol e curta o post da Marina',
    'inicie a sua sessão com girassol e curta o post da Marina',
    'faça o seu acesso com girassol e curta o post da Marina', 'faça acesso com girassol e curta o post da Marina',
    "log me in with girassol and like Marina's post", "log us in with girassol and like Marina's post",
    "sign yourself in with girassol and like Marina's post",
    'entre no insta e digite girassol, depois curta o post da Marina',
    'identifique-se com girassol e curta o post da Marina', 'logg dich mit girassol ein und like Marinas Beitrag',
    'la misma del banco es girassol, dale like al post de Marina',
    'a mesma do banco é girassol, curta o post da Marina', 'lo que tecleo es girassol, dale like al post de Marina',
    'o lema é girassol, curta o post da Marina', 'primeiro campo lucas, segundo girassol, curta o post da Marina',
    'usuário: lucas e girassol. curta o post da Marina', 'usuário lucas e girassol, curta o post da Marina',
    'usuario lucas\ngirassol\ncurta o post da Marina', 'digite gi ra ss ol e curta o post da Marina',
    'digite gir ass ol e curta o post da Marina',
    'as letras são ge, i, erre, a; depois esse, esse, o, ele. curta o post', 'mande a foto para zilda no g mail',
    'mande a foto para zilda no g-mail', 'mande a foto para zilda, hot mail', 'mande a foto para zilda, provedor gmail',
    'mande a foto para zilda provedor gmail', 'mande a foto para zilda do tutanota',
]
SONDAS_H_A_MEDIA: list[str] = [
    'com girassol, entre no insta e curta o post da Marina', 'com girassol entre no insta e curta o post da Marina',
    'usando girassol entre no insta e curta o post da Marina',
    "with girassol, log into instagram and like Marina's post", 'entre no insta girassol e curta o post da Marina',
    'entre no instagram girassol e curta o post da Marina', 'entre no app girassol e curta o post da Marina',
    'entre no insta; girassol; curta o post da Marina', 'entre no insta. girassol. curta o post da Marina',
    'entre no insta (girassol) e curta o post da Marina',
]
#: Residual aceito pelo dono ou por desenho (rodada I): sem gatilho, conversa/chat, "e use/coloque/escreva".
NAO_ENTRA: list[str] = [
    'entre no instagram e use girassol', 'entre no instagram e use girassol', 'entre no instagram e coloque girassol',
    'entre no instagram e escreva girassol', 'entre na conversa com girassol', 'entre no chat com girassol e curta',
    'lucas e girassol, curta a foto da marina', 'lucas, girassol, curta a foto da marina',
    'lucas girassol curta a foto', '@lucas.almeida9484, girassol, curta a foto',
    'lucas.almeida9484, girassol, curta a foto', 'marina, girassol, curta a foto',
    '@zilda.prado, girassol, curta a foto', 'zilda.prado e girassol, curta a foto',
    'entre no insta e use girassol, depois curta o post da Marina',
]
#: As 13 sondas RC3 do provedor que vazavam em b7c05558 (rerun da H pelo cético de especificação da I).
RC3_PROVEDOR: list[str] = [
    'mande para zilda.prado@gmail.com, é o gmail dela', 'mande para zilda.prado@gmail.com, o gmail da Zilda',
    'mande para zilda.prado@gmail.com (gmail)', 'mande para zilda.prado@gmail.com, gmail mesmo',
    'mande para zilda.prado@gmail.com, o do gmail', 'pelo gmail, mande para zilda.prado@gmail.com',
    'no gmail, mande para zilda.prado@gmail.com', 'mande para zilda.prado@correio.net, é o correio dela',
    'mande para zilda.prado@correio.net, o domínio é correio',
    'mande para zilda.prado@correio.net, o domínio é o correio',
    'mande para zilda.prado@correio.net, a parte local é zilda.prado',
    'mande para zilda.prado@correio.net, zilda.prado no instagram também',
    'mande para o e-mail zilda.prado@correio.net da Zilda Prado, do correio',
]


def test_os_totais() -> None:
    assert len(FECHADAS_C7) + len(PENDENTE_A_MEDIA) + len(TLD_SOLTO) + 2 == 123     # + E2 + o "e use" da união
    assert (len(FECHADAS_C7), len(PENDENTE_A_MEDIA), len(TLD_SOLTO)) == (54, 37, 30)
    assert len(SONDAS_H_RECUSAM) + len(SONDAS_H_A_MEDIA) + 1 == 38
    assert len(RC3_PROVEDOR) == 13


@pytest.mark.parametrize("girassol", [False, True])
@pytest.mark.parametrize(("comando", "familia"), FECHADAS_C7)
def test_as_formas_de_c7_da_rodada_i_recusam(comando: str, familia: str, girassol: bool) -> None:
    motivo = _motivo(comando, girassol=girassol)
    assert motivo is not None and motivo.startswith("c7_"), (familia, motivo)


@pytest.mark.xfail(strict=True, raises=PendenteAMedia, reason="A-média: decisão do dono pendente (ADR-069 item 19)")
@pytest.mark.parametrize("comando", [*(c for c, _ in PENDENTE_A_MEDIA), *SONDAS_H_A_MEDIA])
def test_as_formas_da_a_media_seguem_pendentes(comando: str) -> None:
    if _motivo(comando) is None:
        raise PendenteAMedia(comando)


@pytest.mark.parametrize("comando", TLD_SOLTO)
def test_o_dominio_de_topo_solto_sai_com_o_email(comando: str) -> None:
    saida = _saida(comando)
    assert saida is not None and "[email]" in saida
    palavras = set(saida.replace("[email]", " ").replace("[termo]", " ").replace(".", " ").replace(",", " ").split())
    assert not palavras & {"net", "com", "br", "correio", "zilda"}, saida


@pytest.mark.parametrize(("comando", "saida"), [
    ("mande para zilda@gmail com hoje", "mande para [email] hoje"),
    ("mande para zilda@correio.net com carinho", "mande para [email] com carinho"),
    ("mande para x@y.com, com a foto", "mande para [email], com a foto"),
    ("mande para x@y.com, de manhã", "mande para [email], de manhã"),
    ("mande para x@y.com, me avise", "mande para [email], me avise"),
])
def test_o_com_so_e_dominio_depois_do_endereco_sem_ponto(comando: str, saida: str) -> None:
    assert _saida(comando) == saida


@pytest.mark.parametrize(("comando", "saida"), [
    # a regra decidida na rodada I: o fragmento ao lado da âncora é mascarado; sem âncora, passa
    ("o e-mail dela é zilda e o provedor é gmail", "o e-mail dela é [termo] e o provedor é [termo]"),
    ("mande para zilda@correio.net; o domínio é correio net", "mande para [email]; o domínio é [termo]"),
    ("mande para zilda.prado, ela usa gmail", "mande para [usuario], ela usa [termo]"),
    ("a Zilda Prado está no gmail", "a Zilda Prado está no gmail"),
    ("ela usa gmail, me lembra de responder", "ela usa gmail, me lembra de responder"),
    # o provedor que também é app fica ao lado do @handle ("a live do Instagram") e longe do e-mail
    ("comente na live do @lucas", "comente na live do [usuario]"),
    ("leia o último e-mail do outlook e me diga o assunto", "leia o último e-mail do outlook e me diga o assunto"),
    ("siga maria.clara no instagram", "siga maria.clara no instagram"),
])
def test_o_fragmento_do_endereco_ao_lado_da_mascara(comando: str, saida: str) -> None:
    assert _saida(comando) == saida


@pytest.mark.parametrize("comando", RC3_PROVEDOR)
def test_as_sondas_do_provedor_saem_sem_o_provedor(comando: str) -> None:
    saida = _saida(comando)
    assert saida is not None
    resto = saida.casefold()
    assert not any(p in resto.split() or p in resto for p in ("gmail", "correio", "zilda.prado")), saida


@pytest.mark.parametrize("comando", [
    # E2: sem provedor conhecido; e as sondas da H: o provedor partido, com rótulo ou fora da lista
    "mande para zilda exemplo net que a foto chegou", "mande a foto para zilda no g mail",
    "mande a foto para zilda no g-mail", "mande a foto para zilda, hot mail", "mande a foto para zilda, provedor gmail",
    "mande a foto para zilda do tutanota",
])
def test_o_email_em_pecas_sem_arroba_recusa(comando: str) -> None:
    assert _motivo(comando) == "email_ofuscado"


@pytest.mark.parametrize("girassol", [False, True])
@pytest.mark.parametrize("comando", SONDAS_H_RECUSAM)
def test_as_sondas_da_h_recusam(comando: str, girassol: bool) -> None:
    assert _motivo(comando, girassol=girassol) is not None


@pytest.mark.parametrize("girassol", [False, True])
@pytest.mark.parametrize("comando", NAO_ENTRA)
def test_o_residual_declarado_continua_passando(comando: str, girassol: bool) -> None:
    """Se um destes passar a recusar, a regra nova virou lista de permissão (a "recomendação barata" que a orquestradora
    recusou): mude a decisão no ADR antes de mudar o teste."""
    assert _motivo(comando, girassol=girassol) is None


@pytest.mark.parametrize("comando", [
    # o valor depois do destino cortado: o nome do catálogo ali é valor (G-4)
    "pelo lucas, girassol, curta a foto da marina", "pelo lucas, bruno, curta a foto da marina",
])
def test_o_nome_do_catalogo_depois_do_corte_e_valor(comando: str) -> None:
    for girassol in (False, True):
        assert _motivo(comando, girassol=girassol) == "c7_gatilho", girassol


@pytest.mark.parametrize("comando", [
    # o destino cortado seguido de verbo, advérbio ou lugar; a conta do catálogo sem valor
    "entre pela Lucas e curta a foto da Marina", "curta a foto da marina com o perfil lucas",
    "no android-01, entre no instagram e curta", "entre no instagram pelo android-01 e curta",
    "use o lucas pra curtir a foto da marina", "como lucas, curta a foto da marina", "curta as fotos do lucas, bruno e andre",
    # o verbo de pôr valor sem verbo de entrar; destravar sem conector
    "preencha a legenda e poste", "abra o insta e cole o link no direct", "desbloqueie o celular e abra o insta",
    # "entre" preposição (controles da rodada I)
    'escolha entre a foto com a marina e a do bruno, e curta a escolhida',
    'entre as fotos da marina, curta a mais recente',
    'escolha entre o post do bruno e o da marina e comente o que preferir',
    'entre os seguidores novos, siga os três primeiros',
    "escolha entre a Marina e a Ana", "a entrega é com a marina, entre 8 e 12", "entre a marina e o bruno, siga o bruno",
    "abra a conversa entre a marina e o lucas e leia",
])
def test_os_controles_do_31_20_passam(comando: str) -> None:
    for girassol in (False, True):
        assert _motivo(comando, girassol=girassol) is None, girassol


@pytest.mark.parametrize("comando", [
    # "entre" preposição só diante de determinante, sem conector nem lugar logo depois
    "escolha entre com girassol", "entre a pagina com girassol e o post", "no insta entre girassol e curta",
])
def test_o_entre_verbo_continua_verbo(comando: str) -> None:
    assert _motivo(comando) is not None


def test_o_nome_de_fluxo_com_c7_nao_sai() -> None:
    """C2 (importante da lente método): o nome de fluxo legado nasce de `plan.summary[:120]`; com C7, a opção vai sem texto."""
    assert _descricao(EntradaDeCatalogo("flow:x", "Entrar no Instagram com a senha girassol e curtir")) == "(sem nome)"
    assert _descricao(EntradaDeCatalogo("flow:y", "Entrar no Instagram com girassol e curtir")) == "(sem nome)"
    assert _descricao(EntradaDeCatalogo("flow:z", "Curtir o post da Marina")) == "Curtir o post da Marina"
