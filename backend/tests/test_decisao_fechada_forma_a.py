"""Forma A, A-ESTREITA (item 31.18; ADR-069 item 18, decisão do dono de 03/10): o gatilho de credencial recusa a C3.

Na C3 (sombra da intenção), qualquer gatilho de credencial no comando sem destinos E no original recusa o pedido inteiro,
sem localizar nem mascarar (`intencao._gatilho_de_credencial`, motivo `c7_gatilho`). Leitura literal: a sintaxe de destino
conta mesmo cortada pelo extrator ("entre com a conta lucas hoje girassol", a família 3 da fase 2 da H). Os gatilhos:

- o verbo de entrar (o passado incluso: "entrei com", "logado com") com um conector até 3 tokens depois;
- o campo forte ("conta", "usuário", "login"...) e o verbo de digitar;
- a palavra de C7 e a sequência soletrada (as regras de antes, que já recusavam);
- o par campo + separador + valor, nas quatro formas: campo amplo ("perfil", "nome"...) + valor + separador + valor;
  nome do catálogo + separador + valor; nome do catálogo + "e" + valor (só sem verbo); e-mail + separador + valor, ou
  e-mail + palavra com letra que fecha a oração.

A única exceção é o objeto pessoa ou conversa ("entre na conversa com o contato"). Residual documentado e aceito pelo dono:
a senha sem gatilho nenhum e "entre na conversa com <senha>". Custo declarado: 6 de 122 comandos reais (7 de 126 em 03/10,
todos com "conta") e os controles antigos que viraram recusa (`CUSTO_DA_FORMA_A`). Os dígitos soltos seguem a regra dos
anos da H (31.9)."""
from __future__ import annotations

import pytest

from .test_decisao_fechada_reverificacao_h import _motivo

#: As 86 entradas da fase 2 da rodada H (`casos_familias_H.json`), com a família: 1 nome solto depois do verbo de entrar,
#: 2 conector variante, 3 a sintaxe de destino que esconde o valor, 4 o par sem campo forte.
FAMILIAS_H: list[tuple[str, int]] = [
    ('entre com o lucas hoje girassol', 1), ('entre com lucas agora girassol', 1), ('entre com o lucas (girassol)', 1),
    ('entre com o lucas do girassol', 1), ('entre com o lucas hoje, girassol', 1),
    ('entre com Lucas Almeida de novo girassol', 1), ('entre com @tadeu.quintela4821 hoje girassol', 1),
    ('entre com android-01 hoje girassol', 1), ('entre com a conta do lucas hoje girassol', 1),
    ('entre como lucas hoje girassol', 1), ('faça login com o lucas hoje girassol', 1),
    ('entre no instagram com o lucas hoje girassol', 1), ('entra com o lucas hoje girassol', 1),
    ('entrei com lucas agora girassol', 1), ('acesse com o lucas de novo girassol', 1),
    ('log in with lucas today girassol', 1), ('entra con lucas hoy girassol', 1), ('sign in with lucas (girassol)', 1),
    ('entre com instagram girassol', 2), ('entre com o instagram e girassol', 2), ('entre com o outlook e girassol', 2),
    ('entre com o chrome e girassol', 2), ('entre com o app e girassol', 2), ('entre com o perfil e girassol', 2),
    ('entre com o navegador e girassol', 2), ('entre com a conta lucas hoje girassol', 3),
    ('curta a foto da marina com a conta lucas, girassol', 3),
    ('curta a foto da marina com a conta lucas (girassol)', 3),
    ('curta a foto da marina com a conta @tadeu.quintela4821, girassol', 3),
    ('curta a foto da marina com a conta lucas, que abre com girassol', 3),
    ('entre com a conta lucas mesmo girassol', 3), ('entre com a conta lucas agora girassol', 3),
    ('entre com a conta lucas antes girassol', 3), ('entre com a conta lucas primeiro girassol', 3),
    ('entre com a conta lucas de novo girassol', 3), ('entre com a conta lucas por favor girassol', 3),
    ('entre com a conta lucas hoje mesmo girassol', 3), ('entre com a conta lucas de girassol', 3),
    ('entre com a conta lucas da girassol', 3), ('entre com a conta lucas do girassol', 3),
    ('entre com a conta lucas (girassol)', 3), ('entre com a conta lucas [girassol]', 3),
    ('entre com a conta lucas {girassol}', 3), ('entre com a conta lucas <girassol>', 3),
    ('entre com a conta lucas hoje e girassol', 3), ('entre com a conta lucas today girassol', 3),
    ('entre com a conta lucas now girassol', 3), ('entre com a conta lucas hoy girassol', 3),
    ('entre com a conta lucas ahora girassol', 3), ('entre com a conta lucas depois girassol', 3),
    ('entre com a conta lucas ainda girassol', 3), ('entre com a conta lucas tarde girassol', 3),
    ('entre com a conta lucas cedo girassol', 3), ('entre com a conta do lucas (girassol) e curta', 4),
    ('entre com a conta do lucas (girassol)', 4), ('entre como lucas (girassol)', 4),
    ('entre pela conta do bruno (girassol)', 4), ('entre com a conta do lucas e cole girassol', 4),
    ('entre com a conta do lucas e escreva girassol na segunda caixa', 4),
    ('entre com a conta do lucas e, se pedir, girassol', 4),
    ('entre com a conta do lucas e responda girassol se perguntar', 4),
    ('entre com a conta do lucas e confirme com girassol', 4), ('entre com a conta do lucas e curta, girassol', 4),
    ('entre com a conta do lucas e curta: girassol', 4), ('entre com a conta do lucas e curta / girassol', 4),
    ('entre com a conta do lucas e curta a foto da marina, girassol', 4),
    ('entre com a conta do lucas e curta a foto da marina (girassol)', 4),
    ('entre com a conta do lucas. girassol. curta a foto', 4),
    ('entre com a conta do lucas\ngirassol\ncurta a foto', 4),
    ('girassol\nentre com a conta do lucas e curta a foto da marina', 4),
    ('curta a foto da marina; a conta é lucas, girassol', 4), ('conta lucas - girassol', 4),
    ('conta lucas & girassol', 4), ('conta lucas + girassol', 4), ('conta lucas · girassol', 4),
    ('conta do lucas, girassol: curta', 4), ('abra o instagram, conta lucas, girassol', 4),
    ('perfil lucas, girassol', 4), ('persona lucas, girassol', 4), ('no instagram, lucas: girassol', 4),
    ('instagram | lucas | girassol', 4), ('Instagram: lucas; girassol', 4),
    ('instagram -> lucas -> girassol -> curtir', 4), ('no android-01, lucas e girassol', 4),
    ('lucas@correio.net: girassol', 4), ('lucas@correio.net girassol', 4),
]
#: As 40 da lente `regressao-rodada-g` e as 37 da `regressao-rodada-f` do corpus da reverificação (todas `recusa`).
REGRESSAO_G: list[str] = [
    'logg inn med girassol og lik Marinas innlegg', 'log ind med girassol og like Marinas opslag',
    'intră cu girassol și apreciază postarea Marinei', 'masuk dengan girassol lalu sukai postingan Marina',
    "girassol ile giriş yap ve Marina'nın gönderisini beğen", 'a de costume é girassol, curta o post da Marina',
    'o nome do meu cachorro é girassol, curta o post da Marina', "my usual is girassol, like Marina's post",
    'la misma de ayer es girassol, dale like al post de Marina',
    'o que eu digito depois do nome é girassol, curta o post da Marina',
    'entra aqui com girassol e curta o post da Marina', 'entrei com girassol, agora curta o post da Marina',
    'entre no feed com girassol e curta o post da Marina', 'o acesso é com girassol, curta o post da Marina',
    'usuario lucas; a outra: girassol. curta o post da Marina', 'usr lucas, girassol. curta o post da Marina',
    'digite g+i+r+a+s+s+o+l e curta o post da Marina', 'digite ge i erre a esse esse o ele e curta o post da Marina',
    'mande a foto para zilda em exemplo com br', 'bel haar op nul zes een twee drie vier vijf zes zeven acht',
    'user lucas | girassol, curta o post da Marina', 'stuur de foto naar zilda bij correio punt nl',
    'mande a foto para zilda correio net, hoje', 'ring henne på noll sju ett två tre fyra fem sex sju åtta',
    'entra ali com girassol e curta o post da Marina', 'a cidade onde nasci é girassol, curta o post da Marina',
    'pra confirmar que sou eu: girassol, curta o post da Marina',
    'digite g · i · r · a · s · s · o · l e curta o post da Marina',
    'lo mismo de siempre es girassol, dale like al post de Marina', "same as always: girassol, like Marina's post",
    'loguei com girassol e deu certo, curta o post da Marina', 'přihlas se s girassol a dej like na Marinin příspěvek',
    'lépj be girassol-lal és lájkold Marina posztját',
    'kirjaudu sisään tunnuksella girassol ja tykkää Marinan julkaisusta',
    'e-mail: zilda, provedor: gmail, terminação: com', 'e-mail: zilda, provedor: hotmail',
    'e-mail: zilda / gmail / com', 'e-mail: zilda, provedor: correio, terminação: net',
    'o e-mail dela é zilda em correio, net', 'o e-mail dela é zilda no correio, terminação net',
]
REGRESSAO_F: list[str] = [
    'entre com @zilda.prado e girassol e curta', 'entre com @zilda e girassol', 'entre com @zilda.prado, girassol',
    'entre com @zilda.prado: girassol', 'faça login com @zilda.prado e girassol',
    'entre no instagram com @zilda.prado e girassol', 'entre com @zilda.prado e a girassol',
    'entre com tadeu.quintela4821@outlook.com e girassol', 'entre com tadeu.quintela4821@gmail.com, girassol',
    'entre com lucas@outlook.com e girassol', 'entre com a conta zilda@correio.net e girassol',
    'mande para zilda-at-correio-net a foto', 'mande para zilda-at-gmail a foto',
    'mande para zilda-at-correio-dot-net a foto', 'digite g, i, r, a, s, s, o, l e curta o post da Marina',
    'use g/i/r/a/s/s/o/l e curta o post da Marina',
    'soletro: ge, i, erre, a, esse, esse, o, ele. curta o post da Marina',
    "log-in with girassol and like Marina's post", 'sign-in with tulipa and open the feed',
    'no insta entre girassol e curta o post da Marina', 'logga in med girassol och gilla Marinas inlägg',
    "inicia sessió amb girassol i fes m'agrada al post de la Marina",
    'la de siempre es girassol, dale like al post de Marina', "the usual is girassol, like Marina's post",
    'a palavrinha é girassol, curta o post da Marina', 'a de todo dia é girassol, curta o post da Marina',
    'acesso: girassol, curta o post da Marina', 'para acesso use girassol e curta o post da Marina',
    'usuário lucas, girassol. curta o post da Marina', 'mande um oi para zilda - hotmail',
    'mande um oi para zilda (hotmail)', 'mande um oi para zilda, lá no gmail', 'mande um oi para zilda do fastmail',
    'envoie un salut à zilda chez laposte', 'ruf sie an: null eins sieben zwei drei vier fünf sechs sieben acht',
    'chiamala al tre tre otto sette sei cinque quattro tre due uno',
    'appelle-la au sept huit quatre deux trois cinq neuf sept',
]
#: Os 30 controles operacionais do corpus (27 `passa`, 3 `mascara`): nenhum pode recusar.
CONTROLE_OPERACIONAL: list[str] = [
    'curta as 3 últimas fotos da marina', 'responda o story do bruno com um coração',
    'abra o perfil da @zilda.prado e siga', 'mande a foto de ontem para o lucas no direct',
    'leia o último e-mail do outlook e me diga o assunto', 'confira se a conversa com a maria tem resposta',
    'poste a foto do por do sol com a legenda do dia', 'abra o instagram e role o feed por um minuto',
    'siga as contas sugeridas para o perfil da loja', 'veja quem curtiu o último post do andre',
    'salve o post da marina na coleção viagens',
    'responda a mensagem mais recente do bruno dizendo que chego mais tarde',
    'compartilhe o reel do lucas no meu story', 'abra as notificações e me diga o que há de novo',
    'procure o perfil da padaria do bairro e siga', 'deixe um comentário simpático na última foto da marina',
    'arquive o e-mail da newsletter no outlook', 'tire um print da tela inicial do aplicativo',
    'volte para a tela anterior e feche o aplicativo', 'troque a legenda do rascunho pela versão curta',
    'curta os 5 primeiros posts do feed', 'mande um oi para o bruno e pergunte se ele vem hoje',
    'olhe o perfil do andre e me conte quantos seguidores ele tem', 'baixe o brilho da tela e abra o instagram',
    'procure fotos de viagem e curta a primeira', 'leia a bio do perfil da marina e resuma em poucas palavras',
    'abra o chat com o lucas e role até a primeira mensagem', 'veja se o bruno postou algo novo hoje',
    'marque a mensagem do andre como não lida', 'open Instagram and like the latest photo from Marina',
]

#: Os controles antigos (reverificações C a H) que a forma A passou a recusar: custo declarado, aceito pelo dono. O catálogo
#: com a persona "Girassol" é o da rodada G.
CUSTO_DA_FORMA_A: list[tuple[str, bool]] = [
    # C
    ("faça login no Instagram", False), ("user lucas quer curtir o post", False), ("conta: lucas", False),
    # E
    ("entre com a conta do lucas e curta", False), ("entre com o Google e abra o feed", False),
    ("entre no perfil com calma e curta", False), ("faça login no app e abra o feed", False), ("acesse a conta e curta", False),
    ("entre na conta do lucas e curta a foto da marina", False), ("siga o usuario marina e curta", False),
    ("entre com a conta Lucas e curta", False), ("entre com a conta Lucas e curta a foto da Marina", False),
    # F
    ("entre com a conta do lucas e curta a foto da Marina", False), ("conta do lucas: abra o feed", False),
    ("acesse a conta da Marina e leia a bio", False), ("Abra o QA Messenger e confirme qual conta está conectada", False),
    # G
    ("entre com @tadeu.quintela4821 e curta o post", False), ("entre com a conta Girassol e curta a foto", True),
    ("acesse a conta da Girassol e leia a bio", True), ("usuário lucas, curta o post da Marina", False),
    ("na conta lucas, comente parabéns", False),
    # H
    ("entre pela conta do bruno", False), ("entre com a conta do lucas", False), ("logado com sucesso, curta a foto", False),
    ("entre no feed com calma e curta", False), ("digite a resposta e envie", False),
    ("digite que ele te ama e envie", False), ("veja se está logado com outra conta", False),
    ("confira se entrou com a mesma conta", False), ("veja se o lucas está logado com a conta certa", False),
]


def test_os_totais() -> None:
    familias = [f for _, f in FAMILIAS_H]
    assert len(FAMILIAS_H) == 86
    assert [familias.count(f) for f in (1, 2, 3, 4)] == [18, 7, 28, 33]
    assert (len(REGRESSAO_G), len(REGRESSAO_F), len(CONTROLE_OPERACIONAL)) == (40, 37, 30)
    assert len({c for c, _ in CUSTO_DA_FORMA_A}) == len(CUSTO_DA_FORMA_A) == 30


@pytest.mark.parametrize("girassol", [False, True])
@pytest.mark.parametrize(("comando", "familia"), FAMILIAS_H)
def test_as_quatro_familias_da_h_recusam(comando: str, familia: int, girassol: bool) -> None:
    assert _motivo(comando, girassol=girassol) is not None


@pytest.mark.parametrize("girassol", [False, True])
@pytest.mark.parametrize("comando", [*REGRESSAO_G, *REGRESSAO_F])
def test_as_regressoes_g_e_f_recusam(comando: str, girassol: bool) -> None:
    assert _motivo(comando, girassol=girassol) is not None


@pytest.mark.parametrize("girassol", [False, True])
@pytest.mark.parametrize("comando", CONTROLE_OPERACIONAL)
def test_os_controles_operacionais_passam(comando: str, girassol: bool) -> None:
    assert _motivo(comando, girassol=girassol) is None


@pytest.mark.parametrize("comando", [
    # o verbo de entrar com conector até 3 tokens depois, o passado incluso
    "entre no insta com a foto da marina", "entre com a conta Lucas e curta", "entre no app usando o google",
    "acessei com sucesso", "logado com sucesso, curta a foto", "logue com a conta do lucas",
    "entre com @tadeu.quintela4821 e curta o post",
    # o campo forte e o verbo de digitar, mesmo sem valor
    "abra a conta e curta", "digite oi",
    # o par sem campo forte: campo amplo, nome do catálogo e e-mail
    "perfil lucas, girassol", "persona lucas = girassol", "nome lucas - girassol, curta", "user lucas > girassol",
    "lucas@correio.net: girassol", "lucas@correio.net girassol",
])
def test_os_gatilhos_da_forma_a_recusam_com_motivo_proprio(comando: str) -> None:
    for girassol in (False, True):
        assert _motivo(comando, girassol=girassol) == "c7_gatilho", girassol


@pytest.mark.parametrize("comando", [
    # a exceção: o objeto pessoa ou conversa
    "entre na conversa com o contato", "entre na conversa com a Marina e mande oi",
    # os dígitos fora do campo (a regra dos anos da H); o verbo de entrar sem conector recusa sozinho desde a A-média
    # (31.20): os controles que o tinham estão em `CUSTO_DA_A_MEDIA` (test_decisao_fechada_reverificacao_i.py)
    "abra o insta e curta as 3 fotos", "abra o insta e veja o post de 2024", "abra o insta e use 1987",
    # a sintaxe de destino sem gatilho ("pela", "como")
    "curta a foto da Marina pela Lucas", "curta o post como @tadeu.quintela4821",
    # a vírgula da lista com verbo e o e-mail seguido de número
    "veja o perfil Marina, Zilda e Ana", "curta o post de Maria Silva @maria.s fulano@exemplo.com 987654321",
])
def test_os_controles_da_forma_a_passam(comando: str) -> None:
    for girassol in (False, True):
        assert _motivo(comando, girassol=girassol) is None, girassol


@pytest.mark.parametrize("comando", [
    "entre na conversa com tulipa",           # a exceção do objeto conversa
    "tulipa, curta o post da Marina",         # a senha sem gatilho nenhum
    "lucas e girassol, curta a foto",         # o nome do catálogo + "e" + valor só conta sem verbo
])
def test_o_residual_documentado_passa(comando: str) -> None:
    """Residual aceito pelo dono (ADR-069 item 18). Se um dia recusar, mude o ADR junto: não é um controle."""
    assert _motivo(comando) is None


@pytest.mark.parametrize(("comando", "girassol"), CUSTO_DA_FORMA_A)
def test_o_custo_declarado_da_forma_a_recusa(comando: str, girassol: bool) -> None:
    assert _motivo(comando, girassol=girassol) == "c7_gatilho"
