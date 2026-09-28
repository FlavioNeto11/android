"""`TargetExtractor` em tabela de frases (onda C; design persona-e-parque §7.5, risco R13). Pura: sem banco, sem IA.

Positivas: "com a persona X", "como @user", "pelo/pela X", "peça para o/a X", "no(s) aparelho(s) Y e Z", `android-NN`;
casam contra nome, nome de exibição e @ das personas e contra os ids dos aparelhos; o trecho sai do comando.
Negativas: nome sem padrão ("mande mensagem para o André": o André é o DESTINATÁRIO), nome fora do catálogo, "pelo
Instagram", @ de terceiro — nenhuma dica e o texto inteiro.
"""
from __future__ import annotations

import pytest

from app.modules.execution.application.target_extractor import (CatalogoDeDestinos, PersonaNomeavel,
                                                                TargetExtractor)

CATALOGO = CatalogoDeDestinos(
    personas=(
        PersonaNomeavel("p-andre", ("André", "André Carvalho"), ("andre.carvalho",)),
        PersonaNomeavel("p-andre-s", ("André", "André Souza"), ()),
        PersonaNomeavel("p-lucas", ("Lucas", "Lucas Almeida"), ("lucas.almeida9484",)),
        PersonaNomeavel("p-mariana", ("Mariana", "Mari Costa"), ("mariana.costa91182",)),
    ),
    aparelhos=("android-01", "android-02", "android-03", "android-11"),
)
EXTRATOR = TargetExtractor(CATALOGO)

# (frase, personas citadas [ids por menção], aparelhos citados, comando sem destinos)
POSITIVAS: list[tuple[str, list[tuple[str, ...]], list[str], str]] = [
    ("Peça para o André Carvalho curtir a última foto da @nasa", [("p-andre",)], [],
     "curtir a última foto da @nasa"),
    ("peça pro Lucas abrir o Instagram", [("p-lucas",)], [], "abrir o Instagram"),
    ("Curta a foto da @nasa com a persona Mariana", [("p-mariana",)], [], "Curta a foto da @nasa"),
    ("responda as DMs como @lucas.almeida9484", [("p-lucas",)], [], "responda as DMs"),
    ("Curta a foto, com a persona Lucas.", [("p-lucas",)], [], "Curta a foto."),     # o ponto final é fronteira
    ("responda as DMs como @lucas.almeida9484.", [("p-lucas",)], [], "responda as DMs."),
    ("abra o app no android-02.", [], ["android-02"], "abra o app."),
    ("pela Mari Costa, responda o último comentário", [("p-mariana",)], [], "responda o último comentário"),
    ("pelo @andre.carvalho abra o feed", [("p-andre",)], [], "abra o feed"),
    ("pelo andre.carvalho abra o feed", [("p-andre",)], [], "abra o feed"),
    ("abra o Chrome no android-03", [], ["android-03"], "abra o Chrome"),
    ("abra o app nos aparelhos android-01 e android-02", [], ["android-01", "android-02"], "abra o app"),
    ("abra o app no aparelho android-11", [], ["android-11"], "abra o app"),
    ("curta a foto nos aparelhos android-01, android-02 e android-03, com a persona Lucas", [("p-lucas",)],
     ["android-01", "android-02", "android-03"], "curta a foto"),
    # Dois "André": a menção leva os dois ids (quem resolve pergunta); o nome completo desfaz.
    ("pelo André, curta a foto", [("p-andre", "p-andre-s")], [], "curta a foto"),
    ("pelo André Souza, curta a foto", [("p-andre-s",)], [], "curta a foto"),
    # Sem acento e em minúsculas é a mesma citação.
    ("peca para o andre carvalho abrir o app", [("p-andre",)], [], "abrir o app"),
]


@pytest.mark.parametrize("frase, personas, aparelhos, sem_destinos", POSITIVAS, ids=[p[0] for p in POSITIVAS])
def test_frases_com_destino(frase: str, personas: list[tuple[str, ...]], aparelhos: list[str],
                            sem_destinos: str) -> None:
    r = EXTRATOR.extrair(frase)
    assert [m.ids for m in r.dicas.personas] == personas
    assert [i for m in r.dicas.aparelhos for i in m.ids] == aparelhos
    assert r.command_sem_destinos == sem_destinos


NEGATIVAS = [
    "mande mensagem para o André dizendo oi",        # destinatário, não destino
    "responda o Lucas com um oi",
    "abra o app pelo Instagram",                     # "pelo" + algo que não é persona
    "curta a foto como @nasa",                       # @ de terceiro
    "com a persona Zeca curta a foto",               # nome fora do catálogo
    "abra o app no android-99",                      # aparelho que não existe
    "Lucas Almeida vai gostar desta foto",           # nome solto, sem padrão
    "curta a foto do andre.carvalho",                # @ sem "como"/"pelo"
]


@pytest.mark.parametrize("frase", NEGATIVAS)
def test_frases_sem_destino_deixam_o_texto_inteiro(frase: str) -> None:
    r = EXTRATOR.extrair(frase)
    assert r.dicas.vazias
    assert r.command_sem_destinos == frase


def test_nome_colado_em_outra_palavra_nao_casa() -> None:
    """"pelo Lucasfilm" não é o Lucas: a citação termina em fronteira de palavra."""
    assert EXTRATOR.extrair("assista pelo Lucasfilm").dicas.vazias


def test_catalogo_vazio_nao_extrai_nada() -> None:
    r = TargetExtractor(CatalogoDeDestinos()).extrair("peça para o André abrir o app no android-01")
    assert r.dicas.vazias and r.command_sem_destinos == "peça para o André abrir o app no android-01"
