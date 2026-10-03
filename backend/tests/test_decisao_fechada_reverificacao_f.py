"""Rodada F da reverificação do 31.9 (03/10; a fase 2 da rodada E, em db45d4fd, foi NO-GO com 49 vazamentos novos): as
decisões F-A a F-H de `.claude/handoffs/reverificacao-31-9e.md` (§ Decisões da orquestradora). A estratégia mudou: barrar
pela INTENÇÃO de entrar, não só pelo valor.

- F-A: verbo de entrar (lista multilíngue) sem objeto de navegação → a sombra pula o comando (`c7_intencao_de_entrar`).
- F-B: o original é conferido com o conector; "com X" é desfeito pelo catálogo REAL (os nomes chegam ao filtro pelo 5º item
  de `dados_da_sombra`). O artigo não isenta; ',', '-', ':', '=' e '/' valem igual.
- F-C: o campo de usuário mais largo (conta, persona, nome, perfil, login, user). F-D: diminutivos e "codigo" colado.
- F-E: a pergunta de segurança. F-F: cinco letras ou mais soltas. F-G: nome + provedor sem preposição e "point". F-H: o CPF
  nu recusa como `documento`.

Prova `simulated`: funções puras, a sombra com consumidor espião e o `RunService` no harness. Os valores são de mentira.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.modules.skills.domain.intent import IntentResolution, ResolutionStatus
from app.planning.decisao_fechada.entidades import remover_entidades_com_motivo
from app.planning.decisao_fechada.intencao import motivo_c7, nomes_de_destino
from app.security.redaction import redact
from app.taskqueue.sombra_intencao import SombraDaIntencao

from .conftest import Harness
from .test_decisao_fechada_reverificacao_e import _motivo, _sem_destinos

#: As 49 entradas da síntese dos céticos da rodada E (`ceticos_rodada_e_vazamentos_novos.json`), com a classe do portão.
VAZAMENTOS_DA_RODADA_E = [
    ("entre com a conta Lucas e girassol", "C7"),
    ("entre com a conta Lucas girassol", "C7"),
    ("entre com a conta Lucas, girassol", "C7"),
    ("entre com a persona Lucas e girassol", "C7"),
    ("faça login com a conta Lucas e girassol", "C7"),
    ("entre no insta com a conta Lucas e girassol", "C7"),
    ("girassol, entra com a conta Lucas", "C7"),
    ("login com a conta Lucas e girassol", "C7"),
    ("acesse com a conta Lucas e girassol", "C7"),
    ("entre com a conta Lucas e depois girassol", "C7"),
    ("entre com a conta lucas.almeida9484 e girassol", "C7"),
    ("entre como @lucas.almeida9484 e girassol", "C7"),
    ("entre com a conta Lucas e Girassol", "C7"),
    ("abra o instagram e entre com a girassol, depois curta o post da Marina", "C7"),
    ("entre com o girassol e curta a foto", "C7"),
    ("entre usando a girassol e curta a foto da Marina", "C7"),
    ("entre, girassol, e curta o post da Marina", "C7"),
    ("e pra entrar, girassol", "C7"),
    ("e pra entrar - girassol", "C7"),
    ("pra entrar é girassol, curta o post da Marina", "C7"),
    ("entre no app do insta agora com girassol e curta o post", "C7"),
    ("digita girassol e entra, depois curte o post da Marina", "C7"),
    ("coloca girassol depois entra e curte o post da Marina", "C7"),
    ("entra. usa girassol. curta o post da Marina", "C7"),
    ("log into the app with girassol and like Marina's post", "C7"),
    ("sign into instagram with tulipa and open the feed", "C7"),
    ("faça o acesso com girassol e curta o post da Marina", "C7"),
    ("connecte-toi avec girassol et aime la photo de Marina", "C7"),
    ("inloggen met girassol en like de post van Marina", "C7"),
    ("zaloguj się z girassol i polub post Mariny", "C7"),
    ("melde dich an mit girassol und like Marinas Beitrag", "C7"),
    ("lucas e girassol, entra e curte o post da Marina", "C7"),
    ("nome lucas e girassol, entra", "C7"),
    ("conta lucas e girassol, entra", "C7"),
    ("lucas / girassol. curta o post da Marina", "C7"),
    ("a senhinha é girassol, curta o post da Marina", "C7"),
    ("o segredinho é girassol, entra e curte", "C7"),
    ("minha chavinha é girassol, curta o post da Marina", "C7"),
    ("o codigozinho que chegou é girassol, curta o post", "C7"),
    ("a palavra de sempre é girassol, curta o post da Marina", "C7"),
    ("aquela que só eu sei é girassol, entra e curte", "C7"),
    ("o nome do meu primeiro cachorro é girassol, curta o post da Marina", "C7"),
    ("g i r a s s o l, depois curta o post da Marina", "C7"),
    ("soletrando: g-i-r-a-s-s-o-l, entra e curte", "C7"),
    ("mande para zilda gmail com um oi", "email"),
    ("mande um oi para zilda, gmail", "email"),
    ("mande um oi pro gmail da zilda", "email"),
    ("send the photo to zilda hotmail", "email"),
    ("envoie à zilda gmail point fr un salut", "email"),
]


@pytest.mark.parametrize(("comando", "classe"), VAZAMENTOS_DA_RODADA_E)
def test_os_vazamentos_da_rodada_e_recusam(comando: str, classe: str) -> None:
    motivo = _motivo(comando)
    if classe == "C7":
        assert motivo is not None and motivo.startswith("c7_")
    else:
        assert motivo == "email_ofuscado"


#: Os pares conta/valor com destino REAL do catálogo (casos 410 a 421 do corpus da orquestradora).
@pytest.mark.parametrize("comando", [
    "entre com a conta Lucas e abacate", "conta Bruno, tulipa, entra", "conta André girassol",
    "entre com a conta Bruno / abacate", "entre com a conta Bruno e girassol", "conta André, abacate, entra",
    "conta Lucas tulipa", "entre com a conta André / girassol", "entre com a conta android-03 e tulipa",
    "conta android-01, abacate, entra", "conta android-05 girassol", "entre com a conta android-08 / tulipa",
])
def test_o_par_com_destino_real_recusa(comando: str) -> None:
    motivo = _motivo(comando)
    assert motivo is not None and motivo.startswith("c7_")


# ------------------------------------------------------------------ o que a rodada F NÃO pode recusar
@pytest.mark.parametrize("comando", [
    # contrastes da rodada F (casos 422 a 427): entrar com objeto de navegação ou com destino
    "entre com a conta do lucas e curta a foto da Marina", "conta do lucas: abra o feed",
    "log into the lucas profile and like", "entre no chat com a Marina e responda", "acesse a conta da Marina e leia a bio",
    "entre na conversa com qa-001 e envie oi", "entre com o Google e abra o feed",
    # "entre" preposição
    "curta as fotos postadas entre 10/05 e 12/05", "fotos postadas entre 10-05 e 12-05", "a diferença entre os dois posts",
    "fotos entre março e abril",
    # "escolha entre a Marina e a Ana" deixou de ser controle na rodada G (G-2): sem faixa nem "os"/"as", "entre" é verbo
    # provedor de e-mail que também é app, palavra comum e o campo de usuário sem valor
    "a foto da terra vista do espaço", "abra o outlook do lucas", "manda pro Outlook da Ana",
    "Abra o QA Messenger e confirme qual conta está conectada",
    # cinco letras soltas é o piso
    "a b c",
])
def test_controles_passam(comando: str) -> None:
    assert _motivo(comando) is None


def test_o_token_misto_continua_mascarado() -> None:
    """O caso 427 passa com a máscara de sempre (o corpus ainda o espera em claro)."""
    assert remover_entidades_com_motivo(redact("entre na conversa com qa-001 e envie oi") or "") == (
        "entre na conversa com [termo] e envie oi", None)


# ------------------------------------------------------------------ F-A
@pytest.mark.parametrize("comando", [
    "entra e curte", "faça login e curta", "acceda y comente", "log in and like the post", "zaloguj się i polub",
])
def test_entrar_sem_objeto_pula_a_sombra(comando: str) -> None:
    assert _motivo(comando) == "c7_intencao_de_entrar"


def test_a_intencao_de_entrar_so_vale_no_original() -> None:
    """Tirar "com a conta Lucas" deixa "entre e curta": no texto sem destinos a F-A fica desligada, e o original mostra o
    destino."""
    original = "entre com a conta Lucas e curta a foto da Marina"
    assert _sem_destinos(original) == "entre e curta a foto da Marina"
    assert motivo_c7("entre e curta a foto da Marina", intencao=False) is None
    assert motivo_c7(original, sem_destinos="entre e curta a foto da Marina") is None
    assert _motivo(original) is None


@pytest.mark.parametrize(("comando", "motivo"), [
    # "entre" depois de palavra de conteúdo segue verbo quando o que vem depois é do verbo (conector, separador, conjunção)
    ("no instagram entre com girassol e curta", "c7_login_valor"), ("depois disso entre com girassol", "c7_login_valor"),
    ("feito isso entre com: girassol", "c7_login_valor"), ("no insta entre e curte", "c7_intencao_de_entrar"),
    ("Marina entre com girassol", "c7_login_valor"),
    # diante de número só é preposição em data ou faixa
    ("no insta entre 4471 e curte", "c7_intencao_de_entrar"),
    # no começo da oração é sempre verbo: a faixa no começo recusa a sombra (custo aceito, só sombra)
    ("entre 3 e 5 fotos", "c7_intencao_de_entrar"),
])
def test_entre_verbo_depois_de_palavra_de_conteudo(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


# ------------------------------------------------------------------ F-B
@pytest.mark.parametrize("comando", [
    "entre com a girassol e curta", "entre com: girassol", "entre com - girassol", "entre com = girassol",
])
def test_o_com_seguido_de_valor_recusa(comando: str) -> None:
    assert _motivo(comando) == "c7_login_valor"


def test_os_nomes_de_destino() -> None:
    """Cada nome INTEIRO, sem o "@" (rodada G, G-4: a palavra solta de um nome de várias não é destino)."""
    nomes = nomes_de_destino(["@lucas.almeida9484", "Lucas Almeida", "android-01", "Ana da Silva", "de"])
    assert nomes == {"lucas.almeida9484", "lucas almeida", "android-01", "ana da silva"}


# ------------------------------------------------------------------ F-C a F-H
@pytest.mark.parametrize(("comando", "motivo"), [
    # F-C: o campo de usuário
    ("conta zilda girassol", "c7_par_credencial"), ("persona zilda / girassol", "c7_par_credencial"),
    ("perfil zilda, girassol, entra", "c7_par_credencial"), ("login zilda girassol", "c7_par_credencial"),
    ("user zilda girassol", "c7_par_credencial"),
    # F-D: diminutivos e "codigo" colado
    ("a senhinha é girassol", "c7_palavra"), ("a chavinha é girassol", "c7_palavra"), ("o codiguinho é 4471", "c7_palavra"),
    ("meucodigo girassol", "c7_palavra"), ("a clavecita es girassol", "c7_palavra"),
    # F-E: a pergunta de segurança
    ("a de sempre é girassol", "c7_eufemismo"), ("aquela que só eu sei é girassol", "c7_eufemismo"),
    ("o nome do meu primeiro cachorro é rex", "c7_eufemismo"), ("a que combinamos é girassol", "c7_eufemismo"),
    # F-F: soletrado (mais estrito que a especificação, que pedia [termo]: com valor de C7 em volta, recusa)
    ("g i r a s s o l", "c7_ofuscado"), ("g-i-r-a-s-s-o-l e entra", "c7_ofuscado"),
    # F-G: nome + provedor sem preposição e "point"
    ("manda pra zilda gmail", "email_ofuscado"), ("zilda hotmail", "email_ofuscado"),
    ("o e-mail é zilda point com", "email_ofuscado"), ("mande para a Ana gmail", "email_ofuscado"),
    # F-H: o CPF nu
    ("preencha 529.982.247-25 no campo do formulário", "documento"), ("documento 52998224725", "documento"),
])
def test_as_regras_da_rodada_f(comando: str, motivo: str) -> None:
    assert _motivo(comando) == motivo


@pytest.mark.parametrize(("comando", "esperado"), [
    ("procure por z-i-l-d-a no feed", "procure por [termo] no feed"),          # F-F na C3: cinco letras ou mais
    ("ligue para 11 98765-4321", "ligue para [telefone]"),                     # F-H não pega telefone
])
def test_mascaras_da_rodada_f(comando: str, esperado: str) -> None:
    assert remover_entidades_com_motivo(redact(comando) or "") == (esperado, None)


# ------------------------------------------------------------------ a ligação: os nomes do catálogo chegam ao filtro
async def test_dados_da_sombra_levam_os_nomes_do_catalogo(harness: Harness) -> None:
    """O 5º item de `dados_da_sombra`: personas, handles e aparelhos do catálogo de destinos real."""
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    dados = st.runs.dados_da_sombra(run.id)
    assert dados is not None and len(dados) == 5
    assert "android-01" in dados[4]
    assert st.runs.dados_da_intencao(run.id) == dados[:3]


def test_a_sombra_repassa_os_nomes_ao_consumidor() -> None:
    recebidos: list[dict[str, Any]] = []

    class Espiao:
        def ativo(self) -> bool:
            return True

        def observar(self, **kw: Any) -> None:
            recebidos.append(kw)

    sombra = SombraDaIntencao(Espiao(),  # type: ignore[arg-type]
                              resolver=lambda c, p: IntentResolution(status=ResolutionStatus.NO_MATCH),
                              catalogo=lambda: ())
    sombra._observar("r-1", lambda: ("entre e curta", [None], None, "entre com o Lucas e curta", ("Lucas",)))  # noqa: SLF001
    sombra._observar("r-2", lambda: ("abrir o app", [None], None))                                          # noqa: SLF001
    assert [(r["run_id"], r["original"], r["destinos"]) for r in recebidos] == [
        ("r-1", "entre com o Lucas e curta", ("Lucas",)), ("r-2", None, ())]
