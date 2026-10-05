"""As respostas da pessoa às `questions` da proposta do treino (item 31.91, F2).

O `propose` recebe, opcionalmente, `{"answers": [{"question", "answer"}]}`. Aqui ficam a validação da forma, a recusa
de segredo, o acúmulo entre chamadas (guardado DENTRO da proposta da sessão, na chave `answers`) e o corte das
perguntas que a pessoa já respondeu. Nada aqui escreve em banco, log ou evento: o texto da resposta é da pessoa e
só vai ao provedor.
"""
from __future__ import annotations

from ..security.redaction import looks_secret
from .recorder import TrainingError

MAX_POR_CORPO = 8
MAX_ACUMULADAS = 16
PERGUNTA_MAX = 300
RESPOSTA_MAX = 500

Resposta = dict[str, str]       # {"question": ..., "answer": ...}


def chave_da_pergunta(pergunta: str) -> str:
    """A comparação de uma pergunta com outra: sem espaço nas pontas e sem diferença de caixa."""
    return pergunta.strip().casefold()


def _invalida(mensagem: str) -> TrainingError:
    return TrainingError("invalid_answers", mensagem, 400)


def validar_respostas(corpo: object) -> list[Resposta]:
    """Confere o corpo do `propose`. `None` (sem corpo) e `{}` valem como nenhuma resposta. Levanta
    `invalid_answers` (400) para erro de forma e `resposta_sensivel` (400) para resposta com formato de segredo."""
    if corpo is None:
        return []
    if not isinstance(corpo, dict):
        raise _invalida("O corpo tem de ser um objeto {\"answers\": [...]}.")
    extras = sorted(str(k) for k in corpo if k != "answers")
    if extras:
        raise _invalida(f"Campo não reconhecido no corpo: {', '.join(extras)}.")
    brutas = corpo.get("answers")
    if brutas is None:
        return []
    if not isinstance(brutas, list):
        raise _invalida("`answers` tem de ser uma lista de {question, answer}.")
    if len(brutas) > MAX_POR_CORPO:
        raise _invalida(f"No máximo {MAX_POR_CORPO} respostas por pedido.")
    limpas: list[Resposta] = []
    vistas: set[str] = set()
    for n, item in enumerate(brutas, 1):
        if not isinstance(item, dict) or set(item) != {"question", "answer"}:
            raise _invalida(f"A resposta {n} tem de ter só `question` e `answer`.")
        pergunta, resposta = item["question"], item["answer"]
        if not isinstance(pergunta, str) or not isinstance(resposta, str):
            raise _invalida(f"A resposta {n} tem pergunta e resposta que não são texto.")
        pergunta, resposta = pergunta.strip(), resposta.strip()
        if not 1 <= len(pergunta) <= PERGUNTA_MAX:
            raise _invalida(f"A pergunta da resposta {n} tem de ter de 1 a {PERGUNTA_MAX} caracteres.")
        if not 1 <= len(resposta) <= RESPOSTA_MAX:
            raise _invalida(f"A resposta {n} tem de ter de 1 a {RESPOSTA_MAX} caracteres.")
        chave = chave_da_pergunta(pergunta)
        if chave in vistas:
            raise _invalida(f"A pergunta da resposta {n} aparece repetida no mesmo pedido.")
        vistas.add(chave)
        limpas.append({"question": pergunta, "answer": resposta})
    # depois da forma: o formato de segredo, antes de qualquer chamada de IA (a mensagem não repete o texto)
    if any(looks_secret(r["answer"]) for r in limpas):
        raise TrainingError("resposta_sensivel", "Não escreva senha nem código aqui: a proposta não precisa disso.", 400)
    return limpas


def guardadas(proposta: object) -> list[Resposta]:
    """As respostas já guardadas na proposta da sessão (lê com tolerância: proposta antiga não tem a chave)."""
    if not isinstance(proposta, dict):
        return []
    saida: list[Resposta] = []
    for item in proposta.get("answers") or []:
        if isinstance(item, dict) and isinstance(item.get("question"), str) and isinstance(item.get("answer"), str):
            saida.append({"question": item["question"], "answer": item["answer"]})
    return saida


def acumular(anteriores: list[Resposta], novas: list[Resposta]) -> list[Resposta]:
    """Soma as novas às anteriores; a mesma pergunta (`strip().casefold()`) troca a resposta, no lugar da antiga.
    Passou de 16 pares, `invalid_answers`."""
    por_chave: dict[str, Resposta] = {chave_da_pergunta(r["question"]): r for r in anteriores}
    for r in novas:
        por_chave[chave_da_pergunta(r["question"])] = r
    if len(por_chave) > MAX_ACUMULADAS:
        raise _invalida(f"São no máximo {MAX_ACUMULADAS} respostas guardadas por treinamento.")
    return list(por_chave.values())


def sem_as_respondidas(perguntas: list[str], respostas: list[Resposta]) -> list[str]:
    """As `questions` da IA sem as que a pessoa já respondeu."""
    respondidas = {chave_da_pergunta(r["question"]) for r in respostas}
    return [q for q in perguntas if chave_da_pergunta(q) not in respondidas]
