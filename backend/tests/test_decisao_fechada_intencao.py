"""Sombra da intenção (item 31.9, ADR-069): remoção de entidades que falha fechada, o consumidor R2/R3 e o enxerto no `_plan`.

Prova `simulated`: `DecisorFalso`, banco de teste e a RESOLVE de verdade sobre habilidades de teste. Nada toca rede, chave ou a
TypeSafe, e o envio continua fechado no código (`JEV_RUNTIME_SEND_APPROVED = False`): os testes que precisam de uma porta
aberta a abrem com `monkeypatch`, e um deles prova que, fechada, o decisor não é chamado.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from app.config import DecisaoFechadaCfg
from app.modules.skills.domain.intent import IntentResolution, ResolutionStatus
from app.planning.decisao_fechada import privacidade
from app.planning.decisao_fechada.contrato import (
    ID_NENHUMA, PedidoDeDecisao, RespostaDeDecisao, ResultadoDeDecisao, pergunta_choice,
)
from app.planning.decisao_fechada.decisores import DecisorFalso
from app.planning.decisao_fechada.entidades import mascarar_catalogo, normalizar, remover_entidades, vocabulario_de
from app.planning.decisao_fechada.intencao import (
    PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE, CadeiaObservada, ConsumidorDeIntencao, EntradaDeCatalogo, id_opaco,
    menciona_c7,
)
from app.planning.decisao_fechada.porta import Porta
from app.planning.decisao_fechada.sombra import RepositorioDeSombra, observador_de_sombra
from app.taskqueue.sombra_intencao import SombraDaIntencao, cadeia_de, catalogo_de

from .conftest import Harness
from .fake_skills import banco
from .test_habilidades_na_execucao import ABRIR, Mundo
from .test_intencao_resolucao import doc_abrir, publicar_doc

CFG_SHADOW = DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"})


# ================================================================== 1. remover_entidades
@pytest.mark.parametrize("texto, esperado", [
    ("abra o app e curta o primeiro post", "abra o app e curta o primeiro post"),
    ("abra o instagram e curta o primeiro post", "abra o [termo] e curta o primeiro post"),    # app sai só pelo vocabulário
    ("curta 25 posts do feed", "curta [numero] posts do feed"),                   # todo número vira marcador
    ("curta o post de Maria Silva", "curta o post de [termo]"),
    ("Maria, abra o aplicativo", "[termo], abra o aplicativo"),
    ("Abra o app. Depois mande para Joana", "Abra o app. Depois mande para [termo]"),
    ("mande para @fulano.silva agora", "mande para [usuario] agora"),
    ("escreva para fulano@exemplo.com hoje", "escreva para [email] hoje"),
    ("ligue para (11) 99999-9999 agora", "ligue para [telefone] agora"),
    ("ligue para +55 11 98888-7777", "ligue para [telefone]"),
    ("curta 2500 posts", "curta [numero] posts"),
    ("veja https://exemplo.com/a?b=1 e www.exemplo.org", "veja [link] e [link]"),
    ("abra instagram.com/fulano", "abra [link]"),
    ('comente "adorei o post" no feed', "comente [texto] no feed"),
    ("poste 'bom dia pessoal' no feed", "poste [texto] no feed"),          # aspas simples ASCII
    ("escreva `oi tudo bem` agora", "escreva [texto] agora"),
    ("fale com D'Ávila agora", "fale com [termo] agora"),                 # o apóstrofo não abre trecho
    ("segunda, curta o post", "segunda, curta o post"),
])
def test_remover_entidades_troca_por_marcador_fixo(texto: str, esperado: str) -> None:
    assert remover_entidades(texto) == esperado


@pytest.mark.parametrize("texto", [
    "fale com fulano @ exemplo",                      # arroba solta: nenhum detector de handle a pega, a conferência sim
    "meu email e fulano arroba exemplo ponto com",    # e-mail por extenso
    "siga fulano dot com",
    "abra o app com o codigo 1 2 3",                  # dígitos separados que somam 3
    "ligue 1-2-3",
    "acesse exemplo.com.br/fulano",                   # sem esquema
])
def test_remover_entidades_falha_fechada_quando_sobra_indicio(texto: str) -> None:
    resultado = remover_entidades(texto)
    # Ou virou marcadores (e entao nada sobrou) ou recusou: nunca devolve o texto com o indicio.
    if resultado is not None:
        resto = resultado
        for m in ("[link]", "[numero]", "[usuario]", "[email]", "[telefone]", "[termo]", "[texto]"):
            resto = resto.replace(m, " ")
        assert "@" not in resto and not any(c.isdigit() for c in resto) and ".com" not in resto


@pytest.mark.parametrize("texto", [
    "fale com fulano @ exemplo",
    "meu email e fulano arroba exemplo ponto com",
    "siga fulano dot com",
])
def test_remover_entidades_devolve_none_nos_casos_que_devem_recusar(texto: str) -> None:
    assert remover_entidades(texto) is None


def test_remover_entidades_nao_aceita_entrada_que_nao_e_texto() -> None:
    assert remover_entidades(None) is None                         # type: ignore[arg-type]
    assert remover_entidades(123) is None                          # type: ignore[arg-type]


def test_marcadores_nao_disparam_a_conferencia_e_o_resultado_e_estavel() -> None:
    primeiro = remover_entidades("curta o post de Ana Souza e mande para @ana.souza fulano@exemplo.com 5551234")
    assert primeiro is not None
    assert "Ana" not in primeiro and "@" not in primeiro and "5551234" not in primeiro
    assert remover_entidades(primeiro) == primeiro                 # idempotente: o que já está limpo continua limpo


#: Os vazamentos medidos pela revisão independente (`.claude/handoffs/revisao-31-9.md`, achados 1, 3 e os importantes) e o
#: que NÃO pode sobrar de cada um. Com a lista de permissão, cada texto ou recusa (None) ou sai sem o termo.
_VAZAMENTOS = [
    ("joana curtiu isso", ["joana"]),
    ("mande para joana silva", ["joana", "silva"]),
    ("fale com joão da silva", ["joão", "silva"]),
    ("Joana curtiu isso", ["Joana"]),
    ("Joana: abra o app", ["Joana"]),
    ("Joana! abra o app", ["Joana"]),
    ("abra o app\nJoana curtiu", ["Joana"]),
    ("send a message to john", ["john"]),
    ("o número dela é nove nove oito sete", ["nove nove oito"]),
    ("pedido 12", ["12"]),
    ("rua das flores 12", ["flores", "12"]),
    ("moro na rua augusta numero cento e vinte", ["augusta"]),
    ("siga joana_silva99", ["joana", "silva99"]),
    ("joana(at)gmail(dot)com", ["joana", "gmail"]),
    ("Mande para o Dr. Silva o relatório", ["Silva"]),
    ("Mande o arquivo para: Joana", ["Joana"]),
    ("Para: Joana. Mande o arquivo", ["Joana"]),
    ("mande para Łukasz", ["Łukasz"]),
    ("mande para Иван agora", ["Иван"]),
    ("envie para jOANA", ["OANA"]),
    ("comente ❤ no post da joana silva", ["joana"]),                 # o TargetExtractor em minúsculas (achado 3)
    ("abra o chrome e procure strasse da joana souza", ["joana", "souza", "strasse"]),
    ("distribua: curta o post com a persona lucas", ["lucas"]),     # o nome da persona em minúscula
    ("moro na Rua Augusta 12", ["Augusta", "12"]),
    ("ligue para (11) 9 8765-4321", ["11", "8765"]),
    # reverificação de 03/10 (`.claude/handoffs/reverificacao-31-9.md`), causas 2 a 6: nome dentro da lista fixa, numeral
    # por extenso, aspa de outro sistema, símbolo, homóglifo e e-mail ofuscado com chaves
    ("send a message to Uma", ["uma"]),                              # na lista fixa: só a regra da maiúscula segura
    ("mande para Do Van Minh", ["do ", "van", "minh"]),
    ("envie a foto para Edite", ["edite"]),
    ("abra a conversa com ali", ["ali"]),
    ("mande o relatorio para o Conte", ["conte"]),
    ("message Page about the post", ["page"]),
    ("send the file to Price", ["price"]),
    ("mande para Abril", ["abril"]),
    ("abra a conversa com Domingo", ["domingo"]),
    ("mande o arquivo para Bruno Dias", ["bruno", "dias"]),
    ("o telefone dela é noventa e nove, oitenta e sete", ["nove", "sete"]),
    ("ligue para dez dez dez dez", ["dez"]),
    ("llama al nueve ocho siete seis", ["nueve", "ocho"]),
    ("o cpf dela é um e dois e tres e quatro", ["dois", "tres"]),
    ("um e um e um", ["um e um"]),
    ("moro na quadra dez casa sete lote quatro", ["dez", "sete"]),
    ("fica na Vila Madalena casa nove", ["madalena", "nove"]),
    ("entregue na Calle Mayor 5, Madrid", ["mayor", "madrid"]),
    ("meu CPF: 123.456.789-09", ["123", "789"]),
    ("joana {at} gmail {dot} com", ["joana", "gmail"]),
    ("comente „bom dia a todos“ no post", ["dia a todos"]),
    ("comente 「parabéns pelo post」", ["pelo post"]),
    ("comente ‹adorei o novo post›", ["o novo post"]),
    ("comente ″curta todos os posts″", ["curta todos"]),
    ('comente "adorei a foto no feed', ["a foto"]),                  # aspa que sobra
    ("comente “bom dia“ no post", ["bom dia"]),
    ("mande ❤ para 🇯🇴🇦🇳🇦", ["🇯🇴🇦🇳🇦"]),
    ("mande para Ⓙⓞⓐⓝⓐ", ["joana"]),                                 # o NFKC faz da letra circulada a comum
    ("envie para ⠚⠕⠁⠝⠁", ["⠚⠕⠁⠝⠁"]),
    ("mande para 🅹🅾🅰🅽🅰 agora", ["🅹🅾🅰🅽🅰"]),
    ("mande para ｊｏａｎａ", ["joana"]),                                 # largura cheia
    ("curta o post da a​na", ["ana", "a​na"]),               # invisível no meio do nome
    ("curta o post da j̶o̶a̶n̶a̶", ["joana", "j̶"]),                          # marca combinante
    ("mande para Јoana", ["oana"]),                                   # "Ј" cirílico: alfabetos misturados
    ("mande para jo❤ana", ["jo", "ana"]),                            # símbolo colado entre letras
]


@pytest.mark.parametrize("texto, proibidos", _VAZAMENTOS)
def test_lista_de_permissao_nao_deixa_nome_nem_identificador_passar(texto: str, proibidos: list[str]) -> None:
    resultado = remover_entidades(texto)
    if resultado is not None:
        for termo in proibidos:
            assert termo.casefold() not in resultado.casefold(), (texto, resultado)
        assert remover_entidades(resultado) == resultado                    # idempotente


def test_vocabulario_de_nomes_de_app_entra_na_lista_permitida_e_o_limiar_recusa() -> None:
    assert remover_entidades("abra o qamessenger agora") == "abra o [termo] agora"
    assert remover_entidades("abra o qamessenger agora", vocabulario=vocabulario_de(["QA Messenger: QAMessenger"])) == \
        "abra o qamessenger agora"
    # nome de app não é lista fixa (ADR-052): sai pelo id do app e pelos rótulos do registro, que o consumidor soma
    assert remover_entidades("abra o instagram e curta o post", vocabulario=vocabulario_de(["com.instagram.android"])) == \
        "abra o instagram e curta o post"
    # metade ou mais desconhecida: o que sobra é quase só máscara, e o texto inteiro não sai
    assert remover_entidades("joana pedro marcos ana") is None
    assert remover_entidades("curta joana") == "curta [termo]"             # 1 de 2: no limite, sai mascarado


def test_regra_da_maiuscula_mascara_nome_que_e_palavra_comum_e_poupa_nome_de_app_e_comeco_de_frase() -> None:
    apps = vocabulario_de(["Outlook", "Microsoft Outlook"])
    assert remover_entidades("abra o Outlook e leia o e-mail mais recente", vocabulario=apps) == \
        "abra o Outlook e leia o e-mail mais recente"
    assert remover_entidades("abra o Outlook agora") == "abra o [termo] agora"           # sem o registro, é só um nome
    assert remover_entidades("send a message to Uma") == "send a message to [termo]"
    assert remover_entidades("send a message to uma") == "send a message to uma"   # minúscula: risco residual do dono
    assert remover_entidades("Uma foto. Do feed, toque no post") == "Uma foto. Do feed, toque no post"   # começo de frase
    # o comando todo em caixa alta não diz nada pela caixa (e a 2ª passada decide igual: idempotente)
    assert remover_entidades("ABRA O APP E CURTA O POST DA JOANA") == "ABRA O APP E CURTA O POST DA [termo]"


@pytest.mark.parametrize("texto, esperado", [
    ("curta dois posts", "curta [numero] posts"),                        # UM numeral: marcador
    ("curta dois posts e comente três", None),                          # dois ou mais: telefone, documento ou PIN ditado
    ("espere meia hora", "espere [numero] hora"),
    ("abra o e-mail", "abra o e-mail"),                                  # "e-mail" vale como "email"; "a-na" não vale
    ("curta o post da a-na", "curta o post da [termo]"),
])
def test_numeral_e_palavra_com_hifen(texto: str, esperado: str | None) -> None:
    assert remover_entidades(texto) == esperado


def test_normalizar_e_idempotente_e_tira_o_invisivel() -> None:
    for t in ("ｓｅｎｈａ", "a​na", "j̶o̶a̶n̶a̶", "Ⓙⓞⓐⓝⓐ", "D´Ávila", "linha outra", "—traço—"):
        n = normalizar(t)
        assert normalizar(n) == n
    assert normalizar("ｓｅｎｈａ") == "senha" and normalizar("a​na") == "ana" and normalizar("j̶o̶a̶n̶a̶") == "joana"
    assert normalizar("—traço—") == "-traço-" and normalizar("linha outra") == "linha\noutra"


@pytest.mark.parametrize("texto, proibidos", [
    ("Abrir o perfil @nasa e curtir o post", ["@", "nasa"]),
    ('Abrir o post cuja legenda contém "Ainda sobre Setembro Amarelo', ["ainda", "setembro", "amarelo"]),   # aspa aberta
    ("Abrir o perfil de @flavio.neto.11, segui-lo e enviar uma DM", ["flavio", "neto", "11", "@"]),
    ("Enviar mensagem para Joana Silva no QA Messenger", ["joana", "silva"]),           # maiúscula fora do começo
    ("Comentar 'With your powers combined' no post", ["powers", "combined"]),
    ("Abrir o perfil 🇯🇴🇦🇳🇦 e curtir", ["🇯🇴"]),
])
def test_mascarar_catalogo_tira_handle_aspas_e_nome_de_terceiro(texto: str, proibidos: list[str]) -> None:
    saida = mascarar_catalogo(texto, isentas=vocabulario_de(["Instagram", "com.pocqa.messenger"]))
    for termo in proibidos:
        assert termo.casefold() not in saida.casefold(), (texto, saida)
    assert mascarar_catalogo(saida, isentas=vocabulario_de(["Instagram", "com.pocqa.messenger"])) == saida


def test_mascarar_catalogo_nao_recusa_mas_esvazia_o_que_nao_mascara() -> None:
    assert mascarar_catalogo("Abrir o Instagram e curtir o primeiro post", isentas=vocabulario_de(["Instagram"])) == \
        "Abrir o Instagram e curtir o primeiro post"
    assert mascarar_catalogo("Abrir as Configurações do Android") == "Abrir as Configurações do [termo]"
    assert mascarar_catalogo("Enviar o endereço Rua Augusta 1500 para o contato") == ""
    assert mascarar_catalogo("Abrir o perfil da Јoana") == ""                    # alfabetos misturados
    assert mascarar_catalogo("Curtir os três primeiros posts") == "Curtir os [numero] primeiros posts"
    assert mascarar_catalogo(None) == ""                                            # type: ignore[arg-type]


# ================================================================== 2. consumidor (banco de teste e a RESOLVE de verdade)
class Mundo2:
    """Duas habilidades que empatam em "abra a conversa com 3 no instagram" e a sombra sobre um banco de teste."""

    def __init__(self, tmp: Path, decisor: DecisorFalso, cfg: DecisaoFechadaCfg | None = CFG_SHADOW) -> None:
        self.db = banco(tmp, "intencao.sqlite3")
        self.m = Mundo(self.db)
        self.m.publicar(ABRIR)
        publicar_doc(self.m, doc_abrir("ig.abrir_numero", "abra a conversa com {n} no instagram", "n", tipo="integer"))
        self.sombra = RepositorioDeSombra(self.db)
        self.decisor = decisor
        self.porta = Porta(decisor, cfg=cfg, observador=observador_de_sombra(self.sombra))
        self.consumidor = ConsumidorDeIntencao(self.porta, self.sombra)

    def catalogo(self) -> list[EntradaDeCatalogo]:
        return catalogo_de(lambda estado: self.m.registro.list(state=estado), self.m.registro.definition,
                           skills_ligadas=True, fluxos_ligados=False)

    def linhas(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.db.query("SELECT * FROM decisao_fechada_sombra ORDER BY id")]

    def fechar(self) -> None:
        self.porta.aguardar_sombras()
        self.db.close()


@pytest.fixture
def porta_aberta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)


def test_catalogo_traz_as_publicadas_com_nome_do_dono_e_cadeia_registra_o_empate(tmp_path: Path) -> None:
    w = Mundo2(tmp_path, DecisorFalso())
    ids = {e.skill_id for e in w.catalogo()}
    assert ids == {"ig.abrir_conversa", "ig.abrir_numero"}
    assert all(e.nome for e in w.catalogo())
    cadeia = cadeia_de(w.m.planejador.resolve_intent("abra a conversa com 3 no instagram", None))
    assert cadeia.resolvida is None and not cadeia.sem_casamento and set(cadeia.empatados) == ids
    unica = cadeia_de(w.m.planejador.resolve_intent("abra a conversa com @ana no instagram", None))
    assert unica.resolvida == "ig.abrir_conversa" and not unica.sem_casamento
    nada = cadeia_de(w.m.planejador.resolve_intent("faca um bolo de cenoura", None))
    assert nada.sem_casamento and nada.resolvida is None and nada.empatados == ()
    w.fechar()


def test_sombra_grava_as_duas_perguntas_e_casa_a_decisao_real_sem_inventar_a_do_empate(
        tmp_path: Path, porta_aberta: None) -> None:
    ids = {e: id_opaco(e) for e in ("ig.abrir_conversa", "ig.abrir_numero")}
    falso = DecisorFalso({
        PERGUNTA_CATALOGO: RespostaDeDecisao(escolha=ids["ig.abrir_conversa"], confianca=0.95),
        PERGUNTA_DESEMPATE: RespostaDeDecisao(escolha=ids["ig.abrir_numero"], confianca=0.9)})
    w = Mundo2(tmp_path, falso)
    cadeia = cadeia_de(w.m.planejador.resolve_intent("abra a conversa com 3 no instagram", None))
    w.consumidor.observar(run_id="run-1", comando="abra a conversa com 3 no instagram", app="instagram",
                          catalogo=w.catalogo(), cadeia=cadeia)
    w.porta.aguardar_sombras()
    assert len(falso.chamadas) == 1                                    # UMA chamada, fan-out de duas perguntas
    pedido = falso.chamadas[0]
    assert pedido.origem == "intencao" and pedido.classe == "C3" and pedido.modo == "shadow"
    assert set(pedido.estado) == {"comando", "app"} and pedido.estado["app"] == "instagram"
    por_id = {p.id: p for p in pedido.perguntas}
    assert set(por_id) == {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    assert set(por_id[PERGUNTA_CATALOGO].opcoes) == {*ids.values(), ID_NENHUMA}
    assert set(por_id[PERGUNTA_DESEMPATE].opcoes) == {*ids.values(), ID_NENHUMA}
    assert all(o == ID_NENHUMA or o.startswith("opt:") and "ig" not in o for o in por_id[PERGUNTA_CATALOGO].opcoes)
    linhas = {r["pergunta_id"]: r for r in w.linhas()}
    assert set(linhas) == {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    assert linhas[PERGUNTA_CATALOGO]["escolha"] == ids["ig.abrir_conversa"] and linhas[PERGUNTA_CATALOGO]["modo"] == "shadow"
    assert all(r["origem"] == "intencao" and r["classe"] == "C3" and r["ref"] == "run-1" for r in linhas.values())
    # a cadeia ficou num empate sem desfecho: não há decisão real a casar (a pessoa decide depois)
    assert all(r["decisao_real"] is None for r in linhas.values())
    # RA-2: as etapas AMBIGUOUS da RESOLVE ficam contadas em toda linha da execução
    assert cadeia.ambiguos >= 1 and all(r["ambiguos"] == cadeia.ambiguos for r in linhas.values())
    w.fechar()


def test_ambiguos_da_resolve_ficam_contados_so_nas_linhas_da_intencao(tmp_path: Path, porta_aberta: None) -> None:
    """RA-2: a contagem de `StageOutcome.AMBIGUOUS` por execução, também na recusa por privacidade; só número, só intenção."""
    w = Mundo2(tmp_path, DecisorFalso())
    nada = cadeia_de(w.m.planejador.resolve_intent("faca um bolo de cenoura", None))
    assert nada.ambiguos == 0
    w.consumidor.observar(run_id="run-0", comando="faca um bolo de cenoura", app=None, catalogo=w.catalogo(), cadeia=nada)
    w.consumidor.observar(run_id="run-priv", comando="fale com fulano @ exemplo", app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(sem_casamento=True, ambiguos=2))
    w.porta.aguardar_sombras()
    por_run = {r["ref"]: r for r in w.linhas()}
    assert por_run["run-0"]["ambiguos"] == 0
    assert por_run["run-priv"]["fallback_reason"] == "privacidade" and por_run["run-priv"]["ambiguos"] == 2
    assert w.sombra.anotar_ambiguos(5, ref="run-0") == 0                 # já anotada: não sobrescreve
    for ruim in (-1, True, 1.5, "2"):
        with pytest.raises(ValueError):
            w.sombra.anotar_ambiguos(ruim, ref="run-0")                  # type: ignore[arg-type]
    w.fechar()


def test_sombra_casa_a_decisao_real_da_habilidade_resolvida_e_de_nenhuma(tmp_path: Path, porta_aberta: None) -> None:
    falso = DecisorFalso({PERGUNTA_CATALOGO: RespostaDeDecisao(escolha=id_opaco("ig.abrir_conversa"), confianca=0.95)})
    w = Mundo2(tmp_path, falso)
    resolvida = cadeia_de(w.m.planejador.resolve_intent("abra a conversa com @ana no instagram", None))
    w.consumidor.observar(run_id="run-resolvida", comando="abra a conversa com @ana no instagram", app=None,
                          catalogo=w.catalogo(), cadeia=resolvida)
    nada = cadeia_de(w.m.planejador.resolve_intent("faca um bolo de cenoura", None))
    w.consumidor.observar(run_id="run-nada", comando="faca um bolo de cenoura", app=None, catalogo=w.catalogo(), cadeia=nada)
    w.porta.aguardar_sombras()
    por_run = {r["ref"]: r for r in w.linhas()}
    assert set(por_run) == {"run-resolvida", "run-nada"}                  # sem empate: só a pergunta do catálogo
    assert por_run["run-resolvida"]["decisao_real"] == id_opaco("ig.abrir_conversa")
    assert por_run["run-nada"]["decisao_real"] == ID_NENHUMA
    assert por_run["run-nada"]["desfecho"] is None                        # o desfecho fica para o 31.10
    w.fechar()


def test_a_r3_nao_tem_decisao_real_na_sombra_so_a_r2() -> None:
    """A cadeia em empate não escolhe (AMBIGUOUS volta para a pessoa) e a que desempata não devolve os candidatos: a R3 é
    rotulada pela escolha da pessoa ou pelo desfecho, no 31.10 (revisão, achado da R3 inalcançável)."""
    a, b = "ig.abrir_conversa", "ig.abrir_numero"
    perguntas = {PERGUNTA_CATALOGO, PERGUNTA_DESEMPATE}
    reais = ConsumidorDeIntencao.decisoes_reais(CadeiaObservada(resolvida=a, empatados=(a, b)), perguntas)
    assert reais == {PERGUNTA_CATALOGO: id_opaco(a)}
    assert ConsumidorDeIntencao.decisoes_reais(CadeiaObservada(empatados=(a, b)), perguntas) == {}
    assert ConsumidorDeIntencao.decisoes_reais(CadeiaObservada(resolvida="outra", empatados=(a, b)), perguntas) == {
        PERGUNTA_CATALOGO: id_opaco("outra")}


def test_comando_com_entidade_residual_nao_sai_e_a_sombra_registra_privacidade(tmp_path: Path, porta_aberta: None) -> None:
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso)
    cadeia = CadeiaObservada(sem_casamento=True)
    w.consumidor.observar(run_id="run-priv", comando="fale com fulano @ exemplo", app=None, catalogo=w.catalogo(),
                          cadeia=cadeia)
    w.porta.aguardar_sombras()
    assert falso.chamadas == []                                          # o pedido NÃO saiu
    linhas = w.linhas()
    assert len(linhas) == 1 and linhas[0]["fallback_reason"] == "privacidade" and linhas[0]["escolha"] is None
    assert linhas[0]["decisao_real"] == ID_NENHUMA                       # o caminho atual segue medido
    w.fechar()


def test_estado_que_sai_nao_tem_entidade_nem_segredo(tmp_path: Path, porta_aberta: None) -> None:
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso)
    w.consumidor.observar(run_id="run-e", comando="curta o post de Maria Silva @maria.s fulano@exemplo.com 987654321",
                          app=None, catalogo=w.catalogo(), cadeia=CadeiaObservada(sem_casamento=True))
    w.porta.aguardar_sombras()
    assert len(falso.chamadas) == 1
    comando = falso.chamadas[0].estado["comando"]
    assert comando == "curta o post de [termo] [usuario] [email] [numero]"
    w.fechar()


def test_c3_fora_da_intencao_ou_em_on_e_recusada_pela_porta(tmp_path: Path, porta_aberta: None) -> None:
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"curador": "shadow", "intencao": "on"})
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso, cfg)
    pergunta = pergunta_choice("q1", "Pick one.", {"opt:a": "A"})
    outra = PedidoDeDecisao(origem="curador", classe="C3", estado={"licao": "x"}, perguntas=(pergunta,), modo="shadow",
                            ref="r-curador")
    w.porta.consultar(outra)
    em_on = PedidoDeDecisao(origem="intencao", classe="C3", estado={"comando": "abra o app"}, perguntas=(pergunta,),
                            modo="on", ref="r-on")
    w.porta.consultar(em_on)                    # o consumidor `intencao` está em `on` na config, e o pedido pede `on`
    w.porta.aguardar_sombras()
    assert falso.chamadas == []
    motivos = {r["ref"]: r["fallback_reason"] for r in w.linhas()}
    assert motivos == {"r-curador": "privacidade", "r-on": "privacidade"}
    assert privacidade.validar(PedidoDeDecisao(origem="curador", classe="C3", estado={"licao": "x"},
                                               perguntas=(pergunta,), modo="shadow", ref="r")).motivo \
        in ("c3_so_intencao_em_shadow", "campo_fora_da_lista")
    assert privacidade.validar(em_on).motivo == "c3_so_intencao_em_shadow"
    w.fechar()


def test_consumidor_intencao_em_on_na_config_continua_em_shadow(tmp_path: Path, porta_aberta: None) -> None:
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "on"})
    falso = DecisorFalso({PERGUNTA_CATALOGO: RespostaDeDecisao(escolha=id_opaco("ig.abrir_conversa"), confianca=0.95)})
    w = Mundo2(tmp_path, falso, cfg)
    w.consumidor.observar(run_id="run-on", comando="abra a conversa com @ana", app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(resolvida="ig.abrir_conversa"))
    w.porta.aguardar_sombras()
    assert [r["modo"] for r in w.linhas()] == ["shadow"]                 # a sombra da 31.9 nunca vira `on`
    w.fechar()


@pytest.mark.parametrize("cfg", [None, DecisaoFechadaCfg(), DecisaoFechadaCfg(enabled=True),
                                 DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "off"}),
                                 DecisaoFechadaCfg(enabled=False, consumidores={"intencao": "shadow"})])
def test_padrao_desligado_zero_chamadas_e_zero_linhas(tmp_path: Path, porta_aberta: None,
                                                      cfg: DecisaoFechadaCfg | None) -> None:
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso, cfg)
    assert not w.consumidor.ativo()
    w.consumidor.observar(run_id="run-off", comando="abra a conversa com @ana", app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(resolvida="ig.abrir_conversa"))
    w.porta.aguardar_sombras()
    assert falso.chamadas == [] and w.linhas() == []
    w.fechar()


def test_com_o_envio_fechado_no_codigo_nada_e_feito(tmp_path: Path) -> None:
    """Desligado custa ZERO (revisão, núcleo): com `JEV_RUNTIME_SEND_APPROVED=False` o consumidor nem monta o pedido, e
    não sobra nem a linha de recusa (antes gravava `privacidade`)."""
    assert privacidade.JEV_RUNTIME_SEND_APPROVED is False               # sem monkeypatch: o padrão do código
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso)
    assert not w.consumidor.ativo()
    w.consumidor.observar(run_id="run-fechado", comando="abra a conversa com @ana", app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(resolvida="ig.abrir_conversa"))
    w.porta.aguardar_sombras()
    assert falso.chamadas == [] and w.linhas() == []
    w.fechar()


def test_c3_fora_das_classes_permitidas_desliga_a_sombra(tmp_path: Path, porta_aberta: None) -> None:
    cfg = DecisaoFechadaCfg(enabled=True, consumidores={"intencao": "shadow"}, classes_permitidas=["C0", "C1", "C2"])
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso, cfg)
    assert not w.consumidor.ativo()
    w.consumidor.observar(run_id="run-c3", comando="abra a conversa com @ana", app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(resolvida="ig.abrir_conversa"))
    w.porta.aguardar_sombras()
    assert falso.chamadas == [] and w.linhas() == []
    w.fechar()


@pytest.mark.parametrize("comando", [
    "a senha é hunter2",
    "Entre no Outlook, a senha do e-mail é batatafrita",
    "minha senha é correcthorsebattery",
    "o 2fa é 123456",
    "o código de verificação é quatro dois",
    "o codigo e 4242 e depois abra o app",
    "resolva o captcha e entre",
    "use o token abc",
])
def test_c7_em_prosa_nunca_sai_e_a_sombra_registra_privacidade(tmp_path: Path, porta_aberta: None, comando: str) -> None:
    """Achado 2 da revisão: credencial, código e 2FA escritos em prosa marcam `credencial` e a porta recusa o pedido
    inteiro (zero chamadas), com a linha `privacidade`."""
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso)
    pedido = w.consumidor.pedido(run_id="run-c7", comando=comando, app=None, catalogo=w.catalogo(),
                                 cadeia=CadeiaObservada(sem_casamento=True))
    assert pedido is not None and pedido.marcadores == frozenset({"credencial"}) and pedido.estado == {}
    w.consumidor.observar(run_id="run-c7", comando=comando, app=None, catalogo=w.catalogo(),
                          cadeia=CadeiaObservada(sem_casamento=True))
    w.porta.aguardar_sombras()
    assert falso.chamadas == []
    assert [r["fallback_reason"] for r in w.linhas()] == ["privacidade"]
    w.fechar()


def test_nome_de_terceiro_no_comando_social_sai_mascarado(tmp_path: Path, porta_aberta: None) -> None:
    """O catálogo social é C2 (ADR-069; a exclusão social/persona da revisão foi REFUTADA). O nome de terceiro dentro do
    comando social é que não pode sair: a lista de permissão o troca, em qualquer caixa."""
    falso = DecisorFalso()
    w = Mundo2(tmp_path, falso)
    for i, comando in enumerate(("siga a joana no instagram", "comente que lindo no post da joana",
                                 "Comente ❤️ no post da Joana Silva")):
        w.consumidor.observar(run_id=f"run-s{i}", comando=comando, app="com.instagram.android", catalogo=w.catalogo(),
                              cadeia=CadeiaObservada(sem_casamento=True))
    w.porta.aguardar_sombras()
    enviados = [c.estado.get("comando", "") for c in falso.chamadas]
    assert enviados and all("joana" not in e.casefold() and "silva" not in e.casefold() for e in enviados), enviados
    w.fechar()


def test_catalogo_vazio_ou_acima_do_teto_nao_manda_r2(tmp_path: Path, porta_aberta: None) -> None:
    w = Mundo2(tmp_path, DecisorFalso())
    cadeia = CadeiaObservada(sem_casamento=True)
    assert w.consumidor.pedido(run_id="r", comando="abra o app", app=None, catalogo=[], cadeia=cadeia) is None
    grande = [EntradaDeCatalogo(f"flow:f{i}", f"Fluxo {i}") for i in range(255)]
    assert w.consumidor.pedido(run_id="r", comando="abra o app", app=None, catalogo=grande, cadeia=cadeia) is None
    limite = grande[:254]
    pedido = w.consumidor.pedido(run_id="r", comando="abra o app", app=None, catalogo=limite, cadeia=cadeia)
    assert pedido is not None and len(pedido.perguntas[0].opcoes) == 255   # 254 + `nenhuma`
    w.fechar()


def test_texto_do_catalogo_nao_entra_no_vocabulario_da_c3(tmp_path: Path) -> None:
    """Reverificação de 03/10, causa 1: o nome de fluxo legado é o resumo de um comando antigo, com o destino dentro, e
    liberava o destino de volta. Um fluxo com "@fulano" ou "Joana Silva" no nome não libera nem um nem outro, e nome de
    persona nunca entra no vocabulário."""
    w = Mundo2(tmp_path, DecisorFalso())
    catalogo = [*w.catalogo(),
                EntradaDeCatalogo("flow:x", "Enviar mensagem para Joana Silva no QA Messenger"),
                EntradaDeCatalogo("flow:y", "Abrir o perfil de @flavio.neto.11 e seguir"),
                EntradaDeCatalogo("flow:z", "Curtir o post da persona Lucas", "lucas fornalhaskate")]
    cadeia = CadeiaObservada(sem_casamento=True)
    for comando, proibidos in (("curta o post da joana silva", ["joana", "silva"]),
                               ("fale com flavio neto sobre o post", ["flavio", "neto"]),
                               ("siga fulano e fornalhaskate", ["fulano", "fornalhaskate"]),
                               ("curta o post do lucas", ["lucas"])):
        pedido = w.consumidor.pedido(run_id="r", comando=comando, app="com.pocqa.messenger", catalogo=catalogo,
                                     cadeia=cadeia)
        assert pedido is not None
        enviado = pedido.estado.get("comando", "")
        assert all(p not in enviado.casefold() for p in proibidos), (comando, enviado)
    w.fechar()


def test_opcoes_da_r2_saem_mascaradas_antes_do_corte(tmp_path: Path) -> None:
    """O corte em `_DESCRICAO_MAX` vem DEPOIS da máscara: o corte não deixa meio handle nem meia aspa, e o @handle e a
    legenda entre aspas do nome de um fluxo não vão nas opções (reverificação, achado à parte)."""
    w = Mundo2(tmp_path, DecisorFalso())
    longo = "Abrir " + "o feed e " * 20 + "o perfil @anarabottinipsicopedagoga"       # o "@" no 195: cruza o 200
    aspa = "Abrir " + "o feed e " * 20 + 'comentar "Ainda sobre Setembro Amarelo de novo" no post'   # a aspa no 195
    assert longo.index("@") == 195 and aspa.index('"') == 195
    catalogo = [EntradaDeCatalogo("flow:a", longo), EntradaDeCatalogo("flow:b", aspa),
                EntradaDeCatalogo("flow:c", "Abrir o perfil @nasa no Instagram"),
                EntradaDeCatalogo("flow:d", "Enviar mensagem para Joana Silva")]
    pedido = w.consumidor.pedido(run_id="r", comando="abra o app", app="com.instagram.android", catalogo=catalogo,
                                 cadeia=CadeiaObservada(sem_casamento=True))
    assert pedido is not None
    descricoes = " | ".join(pedido.perguntas[0].opcoes.values()).casefold()
    for proibido in ("@", "anara", '"', "aind", "nasa", "setembro", "amarelo", "joana", "silva"):
        assert proibido not in descricoes, (proibido, descricoes)
    assert "instagram" in descricoes                                       # o nome de app (id do app) fica
    w.fechar()


def test_nomes_de_app_do_registro_isentam_a_maiuscula_e_falha_so_mascara_mais(tmp_path: Path) -> None:
    w = Mundo2(tmp_path, DecisorFalso())
    cadeia = CadeiaObservada(sem_casamento=True)
    com_registro = ConsumidorDeIntencao(w.porta, w.sombra, nomes_de_app=lambda: ["Outlook", "Microsoft Outlook"])
    pedido = com_registro.pedido(run_id="r", comando="abra o Outlook e leia o e-mail", app=None, catalogo=w.catalogo(),
                                 cadeia=cadeia)
    assert pedido is not None and pedido.estado["comando"] == "abra o Outlook e leia o e-mail"

    def quebra() -> list[str]:
        raise RuntimeError("registro indisponível")

    sem_registro = ConsumidorDeIntencao(w.porta, w.sombra, nomes_de_app=quebra)
    pedido = sem_registro.pedido(run_id="r", comando="abra o Outlook e leia o e-mail", app=None, catalogo=w.catalogo(),
                                 cadeia=cadeia)
    assert pedido is not None and pedido.estado["comando"] == "abra o [termo] e leia o e-mail"
    w.fechar()


@pytest.mark.parametrize("comando", [
    "minha chave é abra o feed", "pass: nove e oito e sete e seis", "o PIN é 4821", "o P.I.N. é sete e um e dois",
    "minha senhа é nove e oito", "mi contraseña es rosa1234", "la clave es nueve ocho siete seis",
    "my passcode is one two three four", "a palavra-passe é nove", "a palavra passe é nove", "pwd = 9876",
    "two-factor code: four four two one", "the passphrase is post page read", "ｓｅｎｈａ: abra o app",
    "a sen​ha é abra", "a s e n h a é abra", "as senhas do app", "o mfa chegou", "o segredo é abra o feed",
    "use the api key", "o código chegou por SMS",
])
def test_c7_pega_palavra_chave_em_qualquer_formato(comando: str) -> None:
    assert menciona_c7(comando), comando


@pytest.mark.parametrize("comando", [
    "passe para o próximo post", "abra o app e curta o primeiro post", "compare os dois posts", "abra o pinterest",
    "toque no teclado", "escreva bom dia no chat",
])
def test_c7_nao_dispara_em_comando_comum(comando: str) -> None:
    assert not menciona_c7(comando), comando


async def test_estado_liga_os_nomes_de_app_do_registro(harness: Harness) -> None:
    """O vocabulário extra da C3 são os rótulos e nomes do registro (ADR-052), nunca o texto do catálogo."""
    nomes = harness.state.runs.sombra_intencao._consumidor._nomes_de_app()     # type: ignore[union-attr]  # noqa: SLF001
    assert {"Outlook", "Microsoft Outlook"} <= set(nomes)


# ================================================================== 3. o `_plan` não espera a sombra
class DecisorQueEspera(DecisorFalso):
    """Segura a resposta até o teste liberar: prova que o plano termina antes da sombra, sem depender de relógio."""

    def __init__(self, respostas: dict[str, RespostaDeDecisao]) -> None:
        super().__init__(respostas)
        self.liberar = threading.Event()

    def decidir(self, pedido: PedidoDeDecisao, timeout_s: float) -> ResultadoDeDecisao:
        self.chamadas.append(pedido)
        self.liberar.wait(20.0)
        return ResultadoDeDecisao({p.id: self.respostas.get(p.id, RespostaDeDecisao(fallback_reason="desligado"))
                                   for p in pedido.perguntas})


async def test_plan_termina_sem_esperar_o_decisor_lento_e_a_sombra_casa_depois(
        harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    decisor = DecisorQueEspera({PERGUNTA_CATALOGO: RespostaDeDecisao(escolha=id_opaco("ig.abrir_conversa"), confianca=0.95)})
    st.decisao_fechada.decisor = decisor
    st.decisao_fechada.cfg = CFG_SHADOW
    catalogo = [EntradaDeCatalogo("ig.abrir_conversa", "Abrir conversa", "Abre a conversa com uma pessoa"),
                EntradaDeCatalogo("ig.curtir", "Curtir", "")]
    st.runs.sombra_intencao = SombraDaIntencao(
        ConsumidorDeIntencao(st.decisao_fechada, st.decisao_sombra),
        resolver=lambda c, p: IntentResolution(ResolutionStatus.NO_MATCH), catalogo=lambda: catalogo)
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    t0 = time.monotonic()
    detalhe = await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    assert time.monotonic() - t0 < 10.0
    # o plano ACABOU enquanto o decisor segue segurado: o `_plan` não esperou a sombra
    await harness.wait(lambda: len(decisor.chamadas) == 1, 10.0, "a sombra da intenção chamar o decisor")
    assert not decisor.liberar.is_set() and detalhe is not None
    assert st.decisao_sombra._db.scalar("SELECT COUNT(*) FROM decisao_fechada_sombra") == 0   # nada gravado ainda
    decisor.liberar.set()
    await st.runs.sombra_intencao.aguardar()
    st.decisao_fechada.aguardar_sombras()
    await harness.wait(lambda: st.db.scalar("SELECT COUNT(*) FROM decisao_fechada_sombra WHERE decisao_real IS NOT NULL")
                       == 1, 10.0, "a decisão real ser casada")
    linha = st.db.one("SELECT * FROM decisao_fechada_sombra")
    assert linha["pergunta_id"] == PERGUNTA_CATALOGO and linha["ref"] == run.id and linha["origem"] == "intencao"
    assert linha["decisao_real"] == ID_NENHUMA                            # NO_MATCH: o planejador ficou com o comando
    assert linha["escolha"] == id_opaco("ig.abrir_conversa")


async def test_plan_com_a_config_padrao_nao_chama_ninguem(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    st = harness.state
    assert st is not None
    monkeypatch.setattr(privacidade, "JEV_RUNTIME_SEND_APPROVED", True)
    decisor = DecisorFalso()
    st.decisao_fechada.decisor = decisor                  # a config continua a padrão (`enabled=false`)
    assert st.runs.sombra_intencao is not None and not st.runs.sombra_intencao.ativo()
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    await st.runs.sombra_intencao.aguardar()
    st.decisao_fechada.aguardar_sombras()
    assert decisor.chamadas == [] and st.db.scalar("SELECT COUNT(*) FROM decisao_fechada_sombra") == 0


# ------------------------------------------------------------------ desligamento (pedidos da frente Android, 02/10)
async def test_plano_cancelado_nao_agenda_a_sombra(harness: Harness) -> None:
    """O `stop()` do central cancela a tarefa do plano: o callback não pode ler o banco nem agendar sombra nessa hora."""
    import asyncio  # noqa: PLC0415

    chamados: list[str] = []
    runs = harness.state.runs
    runs._intencao_em_sombra = chamados.append                                # type: ignore[method-assign]  # noqa: SLF001
    cancelada: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    cancelada.cancel()
    runs._depois_do_plano(cancelada, "r-cancelada")                            # noqa: SLF001
    concluida: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    concluida.set_result(None)
    runs._depois_do_plano(concluida, "r-ok")                                   # noqa: SLF001
    assert chamados == ["r-ok"]


async def test_cancelar_derruba_as_sombras_soltas() -> None:
    import asyncio  # noqa: PLC0415

    class Consumidor:
        def ativo(self) -> bool:
            return True

        def observar(self, **_: Any) -> None:
            time.sleep(0.3)

    sombra = SombraDaIntencao(Consumidor(),  # type: ignore[arg-type]
                              resolver=lambda c, p: IntentResolution(status=ResolutionStatus.NO_MATCH),
                              catalogo=lambda: ())
    sombra.agendar("r-1", lambda: ("abrir o app", [None], None))
    [solta] = list(sombra._soltas)                                            # noqa: SLF001
    sombra.cancelar()
    await asyncio.wait([solta], timeout=2)
    assert solta.cancelled()
    await sombra.aguardar()
    assert not sombra._soltas                                                 # noqa: SLF001


async def test_a_sombra_recebe_o_comando_sem_destinos(harness: Harness) -> None:
    """ADR-069, C3: `sem_destinos` antes do `redact` e das entidades, em todo caminho de `_perfis_da_execucao`."""
    st = harness.state
    assert st is not None
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    recebidos: list[str] = []

    class SombraEspia:
        def ativo(self) -> bool:
            return True

        def agendar(self, run_id: str, ler: Any) -> None:
            dados = ler()                                    # na vida real, numa thread; aqui, na hora
            recebidos.append(dados[0])

        def cancelar(self) -> None:
            pass

    st.runs.sombra_intencao = SombraEspia()                                    # type: ignore[assignment]
    st.runs.sem_destinos = lambda c: "LIMPO:" + c                              # type: ignore[method-assign]
    st.runs._intencao_em_sombra(run.id)                                        # noqa: SLF001
    assert recebidos == ["LIMPO:abra o aplicativo de configuracoes"]


# ------------------------------------------------------------------ núcleo (revisão independente, 02/10)
async def test_plano_com_excecao_ou_recusado_pelo_provedor_nao_vira_sombra(harness: Harness) -> None:
    import asyncio  # noqa: PLC0415

    chamados: list[str] = []
    runs = harness.state.runs
    runs._intencao_em_sombra = chamados.append                                # type: ignore[method-assign]  # noqa: SLF001
    laco = asyncio.get_running_loop()
    com_erro: asyncio.Future[None] = laco.create_future()
    com_erro.set_exception(RuntimeError("plano quebrou"))
    runs._depois_do_plano(com_erro, "r-erro")                                  # noqa: SLF001
    com_erro.exception()                                                       # consumida: sem aviso de exceção perdida
    recusado: asyncio.Future[None] = laco.create_future()
    recusado.set_result(None)
    runs._sem_sombra.add("r-recusado")                                         # noqa: SLF001 - o ramo `refusal` do _plan
    runs._depois_do_plano(recusado, "r-recusado")                              # noqa: SLF001
    assert chamados == [] and "r-recusado" not in runs._sem_sombra              # noqa: SLF001


async def test_execucao_que_falhou_ou_foi_cancelada_nao_e_lida_pela_sombra(harness: Harness) -> None:
    st = harness.state
    run = harness.run(["android-01"], command="abra o aplicativo de configuracoes", mode="plan")
    await harness.wait_run(run.id, statuses=("planned", "needs_input", "failed"), timeout=20.0)
    assert st.runs._dados_da_sombra(run.id) is not None                       # noqa: SLF001
    for status in ("failed", "cancelled"):
        st.db.execute("UPDATE runs SET status=? WHERE id=?", (status, run.id))
        assert st.runs._dados_da_sombra(run.id) is None                       # noqa: SLF001
    assert st.runs._dados_da_sombra("nao-existe") is None                     # noqa: SLF001


async def test_desligada_a_sombra_nao_le_nem_resolve_nada(harness: Harness) -> None:
    """Desligado custa ZERO: nem a leitura da execução nem a RESOLVE acontecem (o `ler` nunca é chamado)."""
    st = harness.state
    lidos: list[str] = []
    resolvidos: list[str] = []
    st.runs.sombra_intencao = SombraDaIntencao(
        ConsumidorDeIntencao(st.decisao_fechada, st.decisao_sombra),
        resolver=lambda c, p: resolvidos.append(c) or IntentResolution(ResolutionStatus.NO_MATCH),  # type: ignore[func-returns-value]
        catalogo=lambda: ())
    st.runs._dados_da_sombra = lambda run_id: lidos.append(run_id)             # type: ignore[method-assign]  # noqa: SLF001
    st.runs._intencao_em_sombra("r-qualquer")                                  # noqa: SLF001
    await st.runs.sombra_intencao.aguardar()
    assert lidos == [] and resolvidos == []
