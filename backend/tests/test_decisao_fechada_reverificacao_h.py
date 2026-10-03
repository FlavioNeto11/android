"""Rodada H da reverificação do 31.9 (03/10; a fase 2 da rodada G, em 9a99a8d8, teve 47 vazamentos no corpus de 579): a camada
ESTRUTURAL como segunda passada sobre as listas, ainda lista de bloqueio sobre o piso (ADR-069 item 10; decisões H-1 a H-5 da
orquestradora, `.claude/handoffs/reverificacao-31-9g.md`).

- H-1 (a): conector ou posposição com valor desconhecido depois de qualquer verbo de entrar, com ou sem objeto de navegação,
  salvo depois de pessoa ou conversa ("entra aqui com", "entre no feed com", "entre no perfil com <valor>"); o verbo no
  passado ("entrei com", "loguei com") e em norueguês, dinamarquês, romeno, indonésio, turco, tcheco, húngaro e finlandês.
- H-1 (b): o par <campo de login> <valor> <separador que não é palavra> <valor> sem verbo de entrar (";", "|", ":", "usr").
- H-1 (c): duas ou mais letras soltas ou nomes de letra depois de digite, use ou coloque, com qualquer separador ("+", "·"),
  ou três fortes só com espaço.
- H-1 (d): o telefone ditado em holandês e sueco pela lista de numerais (lista de BLOQUEIO; a corrida genérica de "palavras
  curtas desconhecidas" exigiria uma lista de permissão, que o item 10 proíbe: decisão da orquestradora).
- H-1 (e): o e-mail em peças com rótulo e sem ponto ("e-mail: X, provedor: Y", "zilda em correio, net", "zilda bij correio
  punt nl").
- H-2: o domínio de topo separado do e-mail ("zilda@correio. net" saía "[email]. net").
- H-3 (REVERTIDA em 03/10, depois do NO-GO da fase 2): fazia do nome do catálogo sozinho depois do verbo de entrar um
  DESTINO ("entre com o lucas"); preenchida a vaga, a busca do valor relaxava ("entre com o lucas hoje girassol" passava,
  1758/4779 sintéticas). Volta a postura da G: "entre com o lucas" recusa, nos dois catálogos.
- H-5: "a padaria do bairro" não é endereço (só logradouro, número e CEP) e "o e-mail da newsletter no outlook" é a mensagem.

O portão da fase 2 foi o catálogo SEM a persona "Girassol" (as três contas vivas e android-01..08); sem a H-3, os casos
recusam também com ela. Prova `simulated`: funções puras e o extrator real com catálogo de mentira. Os valores são de
mentira.
"""
from __future__ import annotations

import pytest

from app.modules.execution.application.target_extractor import CatalogoDeDestinos, PersonaNomeavel, TargetExtractor
from app.planning.decisao_fechada.entidades import remover_entidades, remover_entidades_com_motivo
from app.planning.decisao_fechada.intencao import motivo_c7, nomes_de_destino
from app.security.redaction import redact
from app.taskqueue import service as _fila  # noqa: F401  (registra a fonte dos nomes de app, como na subida)

from .test_decisao_fechada_reverificacao_e import DESTINOS

DESTINOS_COM_GIRASSOL = CatalogoDeDestinos(
    (*DESTINOS.personas, PersonaNomeavel("p-girassol", ("Girassol",), ("girassol.oficial",))), DESTINOS.aparelhos)
_CATALOGOS = {
    girassol: (TargetExtractor(cat), nomes_de_destino([*(n for p in cat.personas for n in (*p.nomes, *p.handles)),
                                                       *cat.aparelhos]))
    for girassol, cat in ((False, DESTINOS), (True, DESTINOS_COM_GIRASSOL))}


def _motivo(cru: str, *, girassol: bool = False) -> str | None:
    """Como o consumidor decide (`ConsumidorDeIntencao.pedido`): C7 no texto sem destinos e no original; depois, o filtro."""
    extrator, nomes = _CATALOGOS[girassol]
    texto = extrator.extrair(cru).command_sem_destinos
    com_original = texto != cru
    if (c7 := motivo_c7(texto, intencao=not com_original, destinos=nomes)
            or (motivo_c7(cru, sem_destinos=texto, destinos=nomes) if com_original else None)) is not None:
        return c7
    return remover_entidades_com_motivo(redact(texto) or "")[1]


def _saida(cru: str) -> str | None:
    extrator, _ = _CATALOGOS[False]
    return remover_entidades(redact(extrator.extrair(cru).command_sem_destinos) or "")


# ------------------------------------------------------------------ H-2: as entradas da fase 2 da rodada G
#: As 27 que contam (C7 19, e-mail 8) e as 13 de outro idioma (casos 493 a 532 do corpus da H), com a classe.
VAZAMENTOS_DA_RODADA_G = [
    # C7, bug (15)
    ("entra aqui com girassol e curta o post da Marina", "C7"), ("entra ali com girassol e curta o post da Marina", "C7"),
    ("entre no feed com girassol e curta o post da Marina", "C7"),
    ("entrei com girassol, agora curta o post da Marina", "C7"),
    ("loguei com girassol e deu certo, curta o post da Marina", "C7"),
    ("o nome do meu cachorro é girassol, curta o post da Marina", "C7"), ("my usual is girassol, like Marina's post", "C7"),
    ("o que eu digito depois do nome é girassol, curta o post da Marina", "C7"),
    ("o acesso é com girassol, curta o post da Marina", "C7"),
    ("lo mismo de siempre es girassol, dale like al post de Marina", "C7"),
    ("usuario lucas; a outra: girassol. curta o post da Marina", "C7"),
    ("user lucas | girassol, curta o post da Marina", "C7"),
    ("digite g+i+r+a+s+s+o+l e curta o post da Marina", "C7"),
    ("digite g · i · r · a · s · s · o · l e curta o post da Marina", "C7"),
    ("digite ge i erre a esse esse o ele e curta o post da Marina", "C7"),
    # C7, lacuna em português (4)
    ("a de costume é girassol, curta o post da Marina", "C7"), ("usr lucas, girassol. curta o post da Marina", "C7"),
    ("a cidade onde nasci é girassol, curta o post da Marina", "C7"),
    ("pra confirmar que sou eu: girassol, curta o post da Marina", "C7"),
    # e-mail (8)
    ("e-mail: zilda, provedor: gmail, terminação: com", "email"), ("e-mail: zilda, provedor: hotmail", "email"),
    ("e-mail: zilda / gmail / com", "email"), ("e-mail: zilda, provedor: correio, terminação: net", "email"),
    ("o e-mail dela é zilda em correio, net", "email"), ("o e-mail dela é zilda no correio, terminação net", "email"),
    ("mande a foto para zilda em exemplo com br", "email"), ("mande a foto para zilda correio net, hoje", "email"),
    # outro idioma (13)
    ("logg inn med girassol og lik Marinas innlegg", "C7"), ("log ind med girassol og like Marinas opslag", "C7"),
    ("intră cu girassol și apreciază postarea Marinei", "C7"), ("masuk dengan girassol lalu sukai postingan Marina", "C7"),
    ("girassol ile giriş yap ve Marina'nın gönderisini beğen", "C7"),
    ("la misma de ayer es girassol, dale like al post de Marina", "C7"), ("same as always: girassol, like Marina's post", "C7"),
    ("přihlas se s girassol a dej like na Marinin příspěvek", "C7"),
    ("lépj be girassol-lal és lájkold Marina posztját", "C7"),
    ("kirjaudu sisään tunnuksella girassol ja tykkää Marinan julkaisusta", "C7"),
    ("stuur de foto naar zilda bij correio punt nl", "email"),
    ("bel haar op nul zes een twee drie vier vijf zes zeven acht", "telefone"),
    ("ring henne på noll sju ett två tre fyra fem sex sju åtta", "telefone"),
]
def test_as_40_entradas() -> None:
    classes = [c for _, c in VAZAMENTOS_DA_RODADA_G]
    assert len(VAZAMENTOS_DA_RODADA_G) == 40
    assert (classes.count("C7"), classes.count("email"), classes.count("telefone")) == (29, 9, 2)


@pytest.mark.parametrize(("comando", "classe"), VAZAMENTOS_DA_RODADA_G)
def test_os_vazamentos_da_rodada_g_recusam(comando: str, classe: str) -> None:
    motivo = _motivo(comando)
    if classe == "C7":
        assert motivo is not None and motivo.startswith("c7_"), motivo
    elif classe == "email":
        assert motivo == "email_ofuscado", motivo
    else:
        assert motivo == "ditado", motivo


@pytest.mark.parametrize(("comando", "classe"), VAZAMENTOS_DA_RODADA_G)
def test_com_a_persona_girassol_tudo_continua_recusando(comando: str, classe: str) -> None:
    """Sem a H-3 (revertida, 03/10), a persona com o nome da própria senha não isenta mais o valor sozinho depois do
    conector ("entre no feed com girassol", "entrei com girassol"): o residual aceito da H deixou de existir."""
    motivo = _motivo(comando, girassol=True)
    assert motivo is not None and (motivo.startswith("c7_") or classe != "C7"), motivo


#: Os 6 fragmentos de domínio de topo (casos 533 a 538): o "[email]" não deixa sobrar ". net", ". com", "\nnet".
@pytest.mark.parametrize(("comando", "saida"), [
    ("mande para zilda@correio. net hoje", "mande para [email] hoje"),
    ("mande para zilda@gmail. com hoje", "mande para [email] hoje"),
    ("mande para zilda@correio. com. br hoje", "mande para [email] hoje"),
    ("mande para zilda@correio\nnet hoje", "mande para [email] hoje"),
    ("mande para zilda@correio.net ou zilda@outro. net", "mande para [email] ou [email]"),
])
def test_o_dominio_de_topo_separado_fica_no_email(comando: str, saida: str) -> None:
    assert _motivo(comando) is None
    assert _saida(comando) == saida


def test_os_campos_rotulados_recusam_mesmo_com_o_dominio_no_lugar_do_nome() -> None:
    """O 6º fragmento (caso 538): o harness o rotulou "mascara", mas é o formato de campos rotulados que a H-1 (e) manda
    recusar ("e-mail: X, provedor: Y, terminação: Z"). Fica a recusa, o lado seguro; a orquestradora reetiquetou o caso
    no corpus."""
    assert _motivo("e-mail: zilda.prado, provedor: petrobras, terminação: com br") == "email_ofuscado"


@pytest.mark.parametrize(("comando", "saida"), [
    # com o espaço só depois do ponto, só o domínio de topo que não é palavra entra no e-mail
    ("mande para zilda@correio.net. me avise", "mande para [email]. me avise"),
    ("mande para zilda@correio.net. de manhã", "mande para [email]. de manhã"),
    ("mande para zilda@correio.net. Me avise", "mande para [email]. Me avise"),
    ("mande para zilda@correio.net com carinho", "mande para [email] com carinho"),
])
def test_a_forma_conhecida_do_dominio_de_topo(comando: str, saida: str) -> None:
    assert _saida(comando) == saida


# ------------------------------------------------------------------ H-3 revertida: o nome solto volta a ser valor
@pytest.mark.parametrize("comando", [
    "entre pela conta do bruno", "entre com a conta do lucas", "log into the lucas profile and like",
])
def test_a_sintaxe_de_destino_continua_isenta(comando: str) -> None:
    for girassol in (False, True):
        assert _motivo(comando, girassol=girassol) is None, girassol


@pytest.mark.parametrize("comando", [
    "entre com o lucas e curta a foto da marina", "entre como lucas", "entre com o lucas",
    "entre no perfil com Lucas e curta a foto", "abra o insta e entre com o lucas", "entre com o lucas hoje e curta",
    "entre com o lucas no perfil da Marina e curta", "entre com o lucas mesmo",
    # a família 1 da fase 2 da H: com a H-3, tudo depois do nome passava
    "entre com o lucas hoje girassol", "entre com o lucas (girassol)", "entre com o lucas do girassol",
    "entre como lucas hoje girassol", "log in with lucas today girassol", "entra con lucas hoy girassol",
])
def test_sem_a_h3_o_nome_solto_depois_do_verbo_de_entrar_recusa(comando: str) -> None:
    """A H-3 foi revertida (NO-GO da fase 2 da rodada H, 03/10): o nome do catálogo sozinho depois do conector volta a ser a
    posição de valor, como na G. Recusa nos dois catálogos; o custo é a sombra não ver "entre com o lucas"."""
    for girassol in (False, True):
        motivo = _motivo(comando, girassol=girassol)
        assert motivo is not None and motivo.startswith("c7_"), (girassol, motivo)


@pytest.mark.parametrize("comando", [
    # G-4: o nome do catálogo na posição de valor do par, colado ao usuário ou depois de outro destino
    "entre com a conta Lucas e girassol", "entre com lucas e girassol", "entre com o lucas, senha girassol",
    "acesse como lucas com girassol", "entre com o lucas girassol e curta", "entrei com o lucas e girassol",
    "entre com a conta Lucas / girassol",
])
def test_o_valor_continua_recusando_nos_dois_catalogos(comando: str) -> None:
    for girassol in (False, True):
        motivo = _motivo(comando, girassol=girassol)
        assert motivo is not None and motivo.startswith("c7_"), (girassol, motivo)


# ------------------------------------------------------------------ H-1: as bordas
@pytest.mark.parametrize(("comando", "motivo"), [
    ("entre no perfil com girassol e curta", "c7_login_valor"), ("entre no feed com a conta girassol", "c7_login_valor"),
    # a palavra de conta com nome que o catálogo não conhece é valor (F-B); até a H o "conta" de `_ONDE_SE_ENTRA` a isentava
    ("entre com a conta girassol", "c7_login_valor"), ("entre no app com a conta girassol e curta", "c7_login_valor"),
    ("usuario lucas - girassol", "c7_par_credencial"), ("login lucas: girassol", "c7_par_credencial"),
    ("digite x · y e envie", "c7_ofuscado"), ("use g/i e curta", "c7_ofuscado"),
    ("o nome da minha mãe é girassol, curta", "c7_eufemismo"), ("o que eu coloco é girassol", "c7_eufemismo"),
])
def test_as_regras_estruturais(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


@pytest.mark.parametrize(("comando", "motivo"), [
    ("mande para o e-mail da zilda no outlook", "email_ofuscado"), ("copie o e-mail da zilda no outlook.com", "email_ofuscado"),
    ("mande para zilda#correio.net a foto", "email_ofuscado"), ("mande para zilda%correio.net a foto", "email_ofuscado"),
    ("zilda no live, manda o link do perfil", "email_ofuscado"), ("moro na casa 3 da vila", "endereco"),
    ("quadra 5, lote 12, entregue lá", "endereco"), ("moro na quadra dez casa sete lote quatro", "endereco"),
])
def test_o_piso_continua_recusando(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


# ------------------------------------------------------------------ H-5 e os controles operacionais
@pytest.mark.parametrize("comando", [
    # H-5: os dois controles operacionais recusados no 9a99a8d8 (casos 556 e 558)
    "procure o perfil da padaria do bairro e siga", "arquive o e-mail da newsletter no outlook",
    # a palavra de lugar sem número e a mensagem no app
    "poste a foto da casa", "abra o bloco de notas", "a quadra de esportes do bairro, poste",
    "leia o último e-mail do outlook e me diga o assunto",
    "abra o Outlook e responda o e-mail da Marina Prado dizendo que o relatório vai na segunda",
    # o objeto pessoa ou conversa, o estado de entrar e o modo
    "entre na conversa com qa-001 e mande oi", "entre no chat com a Marina e responda", "entre na live com a Ana",
    "entre em contato com a Ana e mande oi", "verifique se o lucas está logado", "o lucas entrou? curta a foto",
    "logado com sucesso, curta a foto", "acesse o perfil da Marina usando o navegador", "entre no feed com calma e curta",
    "entre no app e curta a foto da Marina", "acesse o perfil da Marina e curta",
    # as letras que são palavra e o nome de letra que é palavra comum
    "use a e o como exemplo", "digite a resposta e envie", "coloque o filtro e poste", "digite que ele te ama e envie",
    # o eufemismo só com o verbo "é" (a conjunção "e" não conta) e o e-mail sem domínio
    "poste a foto da cidade onde nasci e marque a Marina", "o nome do meu perfil é lucas, curta a foto",
    "o e-mail do provedor caiu, avise a Marina", "mande para zilda em casa com carinho",
    "ligue para a mãe e diga que estou bem",
    # o determinante da conta depois do verbo no passado
    "veja se está logado com outra conta", "confira se entrou com a mesma conta",
    "veja se o lucas está logado com a conta certa",
    # o link e o que parece domínio (casos 99, 540 e 541)
    "siga maria.clara no instagram", "curta o post, p.ex. o da Marina",
    "abra https://exemplo.com/unsub?u=joao.silva%40exemplo.com",
])
def test_controles_passam(comando: str) -> None:
    assert _motivo(comando) is None


@pytest.mark.parametrize(("comando", "saida"), [
    ("siga maria.clara no instagram", "siga maria.clara no instagram"),
    ("curta o post, p.ex. o da Marina", "curta o post, p.ex. o da Marina"),
    ("abra bit.ly/3xYz9Ab e curta o post", "abra [link] e curta o post"),
    ("abra https://exemplo.com/unsub?u=joao.silva%40exemplo.com", "abra [link]"),
    ("visite loja.exemplo.com.br/promo.", "visite [link]."),
    ("procure o perfil da padaria do bairro e siga", "procure o perfil da padaria do bairro e siga"),
])
def test_as_saidas_dos_controles(comando: str, saida: str) -> None:
    assert _saida(comando) == saida
