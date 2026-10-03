"""Rodada G da reverificação do 31.9 (03/10; a fase 2 da rodada F, em 7c8f58c8, foi NO-GO com 30 achados que contam): as
decisões G-1 a G-6 de `.claude/handoffs/reverificacao-31-9f.md` (§ Decisões da orquestradora).

- G-1: o par de credencial com @handle ou e-mail no lugar do usuário ("entre com @zilda.prado e girassol"). O "@" solto não é
  destino; o "@" e o domínio não quebram o par. Com dígito no valor, o par continua RECUSA, nunca `[termo]`.
- G-2: soletração com vírgula, barra e pelo nome das letras; verbo de entrar com hífen ("log-in with"); "entre" preposição só
  diante de faixa (número, hora ou data dos dois lados) ou de "os"/"as"; eufemismos novos; "usuário X, Y." sem verbo.
- G-3: e-mail em peças com outros separadores ("zilda - hotmail", "zilda (hotmail)", "zilda, lá no gmail", "zilda-at-gmail")
  e provedores novos.
- G-4: o nome do catálogo REAL só isenta como nome INTEIRO e na posição de destino, nunca na de valor. O stub da orquestradora
  tem uma persona "Girassol" de propósito: é o caso em que a senha coincide com o nome de uma persona.
- G-5: "é entre 8 e 12", "entre 08:00 e 12:00", "é entre os melhores" passam (o "é" não vira a conjunção "e").
- G-6: a palavra-chave da C7 sem valor ("lembre de trocar a senha depois") continua recusando.

Prova `simulated`: funções puras e o extrator real com um catálogo de mentira. Os valores são de mentira.
"""
from __future__ import annotations

import pytest

from app.modules.applications.infrastructure.registry import nomes_e_apelidos
from app.modules.execution.application.target_extractor import CatalogoDeDestinos, PersonaNomeavel, TargetExtractor
from app.planning.decisao_fechada import entidades
from app.planning.decisao_fechada.entidades import nomes_dos_apps, remover_entidades_com_motivo
from app.taskqueue import service as _fila  # noqa: F401  (registra a fonte dos nomes de app, como na subida)
from app.planning.decisao_fechada.intencao import motivo_c7, nomes_de_destino
from app.security.redaction import redact

from .test_decisao_fechada_reverificacao_e import DESTINOS

#: O catálogo do harness da orquestradora na rodada G (`ataque_b.py`): as três contas vivas e a persona "Girassol", cujo nome
#: coincide com o valor de teste.
DESTINOS_G = CatalogoDeDestinos(
    (*DESTINOS.personas, PersonaNomeavel("p-girassol", ("Girassol",), ("girassol.oficial",))), DESTINOS.aparelhos)
EXTRATOR_G = TargetExtractor(DESTINOS_G)
NOMES_G = nomes_de_destino([*(n for p in DESTINOS_G.personas for n in (*p.nomes, *p.handles)), *DESTINOS_G.aparelhos])


def _motivo(cru: str) -> str | None:
    """Como o consumidor decide (`ConsumidorDeIntencao.pedido`), com o catálogo da rodada G."""
    texto = EXTRATOR_G.extrair(cru).command_sem_destinos
    com_original = texto != cru
    if (c7 := motivo_c7(texto, intencao=not com_original, destinos=NOMES_G)
            or (motivo_c7(cru, sem_destinos=texto, destinos=NOMES_G) if com_original else None)) is not None:
        return c7
    return remover_entidades_com_motivo(redact(texto) or "")[1]


def _saida(cru: str) -> str | None:
    return remover_entidades_com_motivo(redact(EXTRATOR_G.extrair(cru).command_sem_destinos) or "")[0]


#: As 37 entradas da síntese dos céticos da rodada F (`ceticos_rodada_f_vazamentos_novos.json`; casos 428 a 464 do corpus),
#: com a classe do portão e o tipo: `bug` e `lacuna-pt` contam; `outro-idioma` é o residual que não conta, corrigido aqui
#: pela recomendação não bloqueante da síntese (item 11).
VAZAMENTOS_DA_RODADA_F = [
    # G-1: o par com @handle ou e-mail no lugar do usuário (cético de método)
    ("entre com @zilda.prado e girassol e curta", "C7", "bug"),
    ("entre com @zilda e girassol", "C7", "bug"),
    ("entre com @zilda.prado, girassol", "C7", "bug"),
    ("entre com @zilda.prado: girassol", "C7", "bug"),
    ("faça login com @zilda.prado e girassol", "C7", "bug"),
    ("entre no instagram com @zilda.prado e girassol", "C7", "bug"),
    ("entre com @zilda.prado e a girassol", "C7", "bug"),
    ("entre com lucas.almeida9484@outlook.com e girassol", "C7", "bug"),
    ("entre com lucas.almeida9484@gmail.com, girassol", "C7", "bug"),
    ("entre com lucas@outlook.com e girassol", "C7", "bug"),
    ("entre com a conta zilda@correio.net e girassol", "C7", "bug"),
    # G-3: "at" com hífen (cético de método)
    ("mande para zilda-at-correio-net a foto", "email", "bug"),
    ("mande para zilda-at-gmail a foto", "email", "bug"),
    ("mande para zilda-at-correio-dot-net a foto", "email", "bug"),
    # G-2: a C7 só de letras, sem palavra-chave (cético de resultado)
    ("digite g, i, r, a, s, s, o, l e curta o post da Marina", "C7", "bug"),
    ("use g/i/r/a/s/s/o/l e curta o post da Marina", "C7", "bug"),
    ("soletro: ge, i, erre, a, esse, esse, o, ele. curta o post da Marina", "C7", "lacuna-pt"),
    ("log-in with girassol and like Marina's post", "C7", "bug"),
    ("sign-in with tulipa and open the feed", "C7", "bug"),
    ("no insta entre girassol e curta o post da Marina", "C7", "bug"),
    ("logga in med girassol och gilla Marinas inlägg", "C7", "outro-idioma"),
    ("inicia sessió amb girassol i fes m'agrada al post de la Marina", "C7", "outro-idioma"),
    ("la de siempre es girassol, dale like al post de Marina", "C7", "bug"),
    ("the usual is girassol, like Marina's post", "C7", "outro-idioma"),
    ("a palavrinha é girassol, curta o post da Marina", "C7", "lacuna-pt"),
    ("a de todo dia é girassol, curta o post da Marina", "C7", "lacuna-pt"),
    ("acesso: girassol, curta o post da Marina", "C7", "bug"),
    ("para acesso use girassol e curta o post da Marina", "C7", "lacuna-pt"),
    ("usuário lucas, girassol. curta o post da Marina", "C7", "bug"),
    # G-3: e-mail em peças com outros separadores e provedor novo (cético de resultado)
    ("mande um oi para zilda - hotmail", "email", "bug"),
    ("mande um oi para zilda (hotmail)", "email", "bug"),
    ("mande um oi para zilda, lá no gmail", "email", "bug"),
    ("mande um oi para zilda do fastmail", "email", "lacuna-pt"),
    ("envoie un salut à zilda chez laposte", "email", "outro-idioma"),
    # telefone ditado em alemão, italiano e francês (cético de resultado)
    ("ruf sie an: null eins sieben zwei drei vier fünf sechs sieben acht", "telefone", "outro-idioma"),
    ("chiamala al tre tre otto sette sei cinque quattro tre due uno", "telefone", "outro-idioma"),
    ("appelle-la au sept huit quatre deux trois cinq neuf sept", "telefone", "outro-idioma"),
]


def test_as_37_entradas_e_as_contagens() -> None:
    assert len(VAZAMENTOS_DA_RODADA_F) == 37
    tipos = [t for _, _, t in VAZAMENTOS_DA_RODADA_F]
    assert tipos.count("bug") + tipos.count("lacuna-pt") == 30 and tipos.count("outro-idioma") == 7


@pytest.mark.parametrize(("comando", "classe", "tipo"), VAZAMENTOS_DA_RODADA_F)
def test_os_vazamentos_da_rodada_f_recusam(comando: str, classe: str, tipo: str) -> None:
    motivo = _motivo(comando)
    if classe == "C7":
        assert motivo is not None and motivo.startswith("c7_"), (tipo, motivo)
    elif classe == "email":
        assert motivo == "email_ofuscado", (tipo, motivo)
    else:
        assert motivo == "ditado", (tipo, motivo)


# ------------------------------------------------------------------ G-1: o usuário como @handle ou e-mail
#: Os 12 pares da orquestradora (casos 465 a 476 do corpus).
@pytest.mark.parametrize("comando", [
    "entre com @zilda.prado e girassol e curta", "entre com lucas.almeida9484@outlook.com e girassol",
    "acesse com @zilda.prado e Girassol2024", "faça login com @bruno e tulipa42", "entre com @bruno: abacate",
    "entre com @zilda.prado / tulipa", "acesse com @lucas.almeida9484, abacate",
    "entre com bruno.ferreira9267@outlook.com e tulipa42", "faça login com zilda@correio.net: abacate",
    "entre com zilda@correio.net / girassol", "login com @andre.carvalho9543, tulipa",
    "acesse com @zilda.prado e abacate, depois curta o post",
])
def test_o_par_com_handle_ou_email_recusa(comando: str) -> None:
    motivo = _motivo(comando)
    assert motivo is not None and motivo.startswith("c7_")


@pytest.mark.parametrize("comando", [
    "acesse com @zilda.prado e Girassol2024", "entre com @zilda.prado e girassol", "entre com lucas@outlook.com e girassol",
    "entre com lucas.almeida9484@gmail.com, girassol",
])
def test_o_par_com_handle_e_par_e_nao_mascara(comando: str) -> None:
    """Regra (iv): com o usuário reconhecido, o par recusa como par; o valor com dígito nunca sai como `[termo]`."""
    assert _motivo(comando) == "c7_par_credencial"


def test_o_handle_do_catalogo_sem_valor_passa() -> None:
    """"entre com @<handle do catálogo>" é destino; o mesmo com um handle que o catálogo não conhece é intenção de entrar."""
    assert _motivo("entre com @lucas.almeida9484 e curta o post") is None
    assert _motivo("entre com @zilda.prado e curta o post") == "c7_intencao_de_entrar"


# ------------------------------------------------------------------ G-3
#: Os 8 separadores de e-mail da orquestradora (casos 477 a 484).
@pytest.mark.parametrize("comando", [
    "mande um oi para zilda - hotmail", "mande um oi para zilda (hotmail)", "mande um oi para zilda, lá no gmail",
    "mande para zilda-at-correio-net a foto", "mande para zilda-at-gmail a foto",
    "mande para zilda-at-correio-dot-net a foto", "mande um oi para zilda do fastmail",
    "mande para zilda_at_gmail_dot_com a foto",
    # os provedores novos
    "mande um oi para zilda do protonmail", "mande um oi para zilda do gmx", "mande um oi para zilda, no web.de",
    "mande um oi para zilda do yandex", "mande um oi para zilda, no mail.ru", "mande um oi para zilda do aol",
    "mande um oi para zilda, no me.com", "mande um oi para zilda do outlook.com.br", "mande um oi para zilda do zoho",
    "mande para zilda at correio-dot-net a foto",
])
def test_o_email_em_pecas_recusa(comando: str) -> None:
    assert _motivo(comando) == "email_ofuscado"


@pytest.mark.parametrize("comando", [
    # o handle com "_at_" sem provedor nem domínio de topo depois não é e-mail
    "siga @cafe_at_home e curta", "curta o post do look-at-me",
    # o que vem antes do provedor é o app ou o objeto, não o dono
    "abra o insta - gmail fica pra depois", "a foto (gmail) está salva",
    # o rótulo com dois-pontos ou hífen (1 dos 122 comandos reais de 7 dias, 03/10, tinha "site: outlook")
    "site: outlook, abra a caixa de entrada", "site - outlook, leia o último e-mail",
])
def test_controles_do_email_passam(comando: str) -> None:
    assert _motivo(comando) is None


# ------------------------------------------------------------------ G-4: o catálogo real
#: Os casos antigos do corpus que vazavam com a persona "Girassol" no catálogo: o valor igual a um nome era isento (os 29 da
#: comparação da orquestradora e os 5 que mudaram de motivo).
@pytest.mark.parametrize("comando", [
    "entre usando girassol e curta a foto da Marina", "faca login com girassol e depois curta",
    "entre com girassol e curta a foto", "entre com a conta Lucas e girassol", "entre com a conta Lucas girassol",
    "entre com a conta Lucas, girassol", "entre com a persona Lucas e girassol", "faça login com a conta Lucas e girassol",
    "entre no insta com a conta Lucas e girassol", "login com a conta Lucas e girassol", "acesse com a conta Lucas e girassol",
    "entre com a conta Lucas e depois girassol", "entre com a conta lucas.almeida9484 e girassol",
    "entre como @lucas.almeida9484 e girassol", "entre com a conta Lucas e Girassol",
    "abra o instagram e entre com a girassol, depois curta o post da Marina", "entre com o girassol e curta a foto",
    "entre usando a girassol e curta a foto da Marina", "entre no app do insta agora com girassol e curta o post",
    "log into the app with girassol and like Marina's post", "faça o acesso com girassol e curta o post da Marina",
    "connecte-toi avec girassol et aime la photo de Marina", "inloggen met girassol en like de post van Marina",
    "zaloguj się z girassol i polub post Mariny", "melde dich an mit girassol und like Marinas Beitrag",
    "lucas / girassol. curta o post da Marina", "conta André girassol", "entre com a conta Bruno e girassol",
    "conta android-05 girassol",
    "e pra entrar: girassol", "entre, girassol, e curta o post da Marina", "e pra entrar, girassol",
    "e pra entrar - girassol", "digita girassol e entra, depois curte o post da Marina",
])
def test_o_nome_do_catalogo_nao_isenta_o_valor(comando: str) -> None:
    motivo = _motivo(comando)
    assert motivo is not None and motivo.startswith("c7_")


def test_a_persona_girassol_e_o_par() -> None:
    """O teste pedido na G-4: a persona "Girassol" existe, e "entre com a conta Lucas e girassol" recusa como par."""
    assert _motivo("entre com a conta Lucas e girassol") == "c7_par_credencial"


def test_o_nome_inteiro_e_nao_a_palavra() -> None:
    """A persona "Sol Nascente" não faz de "sol" um destino: só o nome inteiro conta (o "flip" do cético de método)."""
    nomes = nomes_de_destino(["Sol Nascente"])
    assert motivo_c7("entre com a conta do lucas e sol", destinos=nomes) is not None
    assert motivo_c7("entre e sol", destinos=nomes) is not None
    assert motivo_c7("entre com a conta Sol Nascente e curta", destinos=nomes) is None


@pytest.mark.parametrize("comando", [
    # o nome do catálogo na posição de destino continua isento
    "siga a Girassol e curta a última foto", "entre com a conta Girassol e curta a foto", "entre pela Girassol e curta",
    "entre como @girassol.oficial e curta", "entre com a conta do lucas e curta a foto da Marina",
    "acesse a conta da Girassol e leia a bio",
])
def test_o_nome_do_catalogo_no_destino_passa(comando: str) -> None:
    assert _motivo(comando) is None


# ------------------------------------------------------------------ G-5 e G-2: "entre" preposição só com faixa
#: Os 6 contrastes da orquestradora (casos 485 a 490): os 4 com número saem mascarados, os 2 sem número, iguais.
@pytest.mark.parametrize(("comando", "saida"), [
    ("a entrega é entre 8 e 12, avise a Marina", "a entrega é entre [numero] e [numero], avise a Marina"),
    ("a entrega é entre 08:00 e 12:00, avise a Marina", None),
    ("as fotos postadas entre 12/09 e 15/09, curta", None),
    ("entrega entre 08:00 e 12:00, confirme com a Zilda", None),
    ("o post é entre os melhores, curta", "o post é entre os melhores, curta"),
    ("a Marina é entre as mais seguidas, siga", "a Marina é entre as mais seguidas, siga"),
])
def test_entre_preposicao_passa(comando: str, saida: str | None) -> None:
    assert _motivo(comando) is None
    if saida is not None:
        assert _saida(comando) == saida


@pytest.mark.parametrize("comando", [
    "fotos entre março e abril", "poste entre segunda e sexta", "curta os posts entre 8h e 12h",
    "a diferença entre os dois posts", "fotos postadas entre 10-05 e 12-05",
])
def test_outras_faixas_passam(comando: str) -> None:
    assert _motivo(comando) is None


@pytest.mark.parametrize(("comando", "motivo"), [
    # sem faixa depois, "entre" é verbo, mesmo depois de palavra de conteúdo (G-2)
    ("no insta entre girassol e curta o post da Marina", "c7_intencao_de_entrar"),
    ("no insta entre 4471 e curte", "c7_intencao_de_entrar"),
    ("no insta entre girassol", "c7_intencao_de_entrar"),
    # o "é" sem acento é a conjunção: "e entre" segue verbo
    ("abra o insta e entre girassol", "c7_intencao_de_entrar"),
    # custo aceito: sem faixa nem "os"/"as", a preposição vira verbo e a sombra pula o comando
    ("escolha entre a Marina e a Ana", "c7_intencao_de_entrar"),
])
def test_entre_sem_faixa_e_verbo(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


# ------------------------------------------------------------------ G-2: o resto
@pytest.mark.parametrize(("comando", "motivo"), [
    ("use g, i, r, a, s, s, o, l", "c7_ofuscado"), ("g/i/r/a/s/s/o/l", "c7_ofuscado"),
    ("ge, i, erre, a, esse, esse, o, ele", "c7_ofuscado"), ("be-a-ene-a-ene-a", "c7_ofuscado"),
    ("log-in with girassol", "c7_login_valor"), ("sign-in using tulipa", "c7_login_valor"),
    ("log-into the app and like", None),
    ("lo de siempre es girassol", "c7_eufemismo"), ("a palavrinha mágica é girassol", "c7_eufemismo"),
    ("acesso = girassol", "c7_eufemismo"), ("para acesso digite girassol", "c7_eufemismo"),
    ("user lucas, girassol", "c7_par_credencial"), ("login zilda, tulipa e curta", "c7_par_credencial"),
])
def test_as_regras_da_rodada_g(comando: str, motivo: str | None) -> None:
    assert _motivo(comando) == motivo


@pytest.mark.parametrize("comando", [
    # o campo de usuário com vírgula, sem verbo de entrar, só recusa com o valor (não com o verbo de ação)
    "usuário lucas, curta o post da Marina", "na conta lucas, comente parabéns", "veja o perfil Marina, Zilda e Ana",
    # "para acesso use" diante de artigo é instrução de navegação
    "para acesso use o menu de cima", "mande uma palavrinha de carinho para a Marina",
    # a lista de letras curta e o nome de letra sem separador
    "a, b, c", "ele e ela curtiram o post",
])
def test_controles_da_rodada_g_passam(comando: str) -> None:
    assert _motivo(comando) is None


# ------------------------------------------------------------------ G-6
@pytest.mark.parametrize("comando", [
    "lembre de trocar a senha depois e curta o post da Marina", "o código de rastreio é BR123 e curta o post",
    "o PIN fica no armário, curta a foto",
])
def test_a_palavra_c7_sem_valor_continua_recusando(comando: str) -> None:
    """G-6 (decisão da orquestradora, 03/10): o lado seguro. A palavra é o gatilho que segura o valor irreconhecível; o custo
    de utilidade é aceito e se revê se as recusas nos comandos reais passarem de 5 %."""
    motivo = _motivo(comando)
    assert motivo is not None and motivo.startswith("c7_")


# ------------------------------------------------------------------ catraca do ADR-052 por gancho (suíte 9)
def test_os_nomes_dos_apps_vem_do_gancho_que_a_fila_registra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem import tardio no filtro (`test_arquitetura::test_imports_tardios_so_diminuem`): a fila registra
    `registry.nomes_e_apelidos` na subida, e o filtro só conhece o gancho. Sem registro, nenhum nome de app."""
    assert entidades._fonte_dos_apps is nomes_e_apelidos                     # noqa: SLF001
    assert {"instagram", "insta", "outlook"} <= nomes_dos_apps()
    assert _motivo("entra no insta com girassol e curta o post da Marina") is not None
    monkeypatch.setattr(entidades, "_fonte_dos_apps", tuple)
    assert nomes_dos_apps() == frozenset()
