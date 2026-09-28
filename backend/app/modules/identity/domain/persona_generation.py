"""Geração de persona por IA: o pedido, o prompt e as regras do rascunho — sem provedor, sem banco.

Quem chama o modelo é `planning/*_provider.py` (pelo papel `social`); quem grava é `social/service.py`, depois de
validar. O que mora aqui é o que os dois precisam ter IGUAL: o texto do pedido, o formato esperado e o que faz um
rascunho ser recusado (menor de idade, nome que não é nome, voz ou biografia incompletas). O nome da persona e o
que já existe dela entram no pedido; nenhuma credencial, memória ou tela entra nunca.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

from app.modules.identity.domain.persona import (BIOGRAFIA_MINIMA, CONDUTA_DAS_CRENCAS, MAIORIDADE, idade_em,
                                                 lacunas_da_biografia, nome_ficticio_plausivel)
from app.util import sem_marcacao

#: Faixa etária pedida ao modelo quando o dono não diz. Adulto por regra (`MAIORIDADE`), e longe da borda.
IDADE_MINIMA_GERADA = 21
IDADE_MAXIMA_GERADA = 60
#: Teto de saída do rascunho, o MESMO nos dois provedores pagos. Era 6000; com as crenças ricas (ADR-048) o JSON
#: cresce, e no Anthropic o raciocínio adaptativo (`thinking`) gasta do mesmo teto — rascunho truncado é
#: `max_tokens`, erro e chamada paga perdida. O teto só limita: paga-se o que o modelo de fato escreve.
MAX_TOKENS_DO_RASCUNHO = 10000
#: Quantas pessoas o bloco `<evitar>` lista, e com que tamanho de resumo. As personas que já existem podem ser
#: muitas; o bloco vai em TODA geração do lote, e o que importa ao modelo é o nome e o jeito de cada uma, não a
#: biografia inteira. As mais recentes entram primeiro (quem chama ordena), e o excedente fica de fora.
MAX_EVITAR = 40
RESUMO_EVITAR_CHARS = 120


@dataclass(frozen=True, slots=True)
class PersonaEvitada:
    """Uma pessoa que o rascunho NÃO pode repetir (lote): uma que já existe ou uma já gerada no mesmo lote."""

    nome: str
    resumo: str = ""


@dataclass(frozen=True, slots=True)
class PersonaGenerationRequest:
    """O pedido de uma persona nova (`existing=None`) ou do enriquecimento de uma existente (`existing` = o que
    já está preenchido, que o modelo deve manter e completar).

    Num LOTE (`POST /personas/generate/batch`), `avoid` lista quem não repetir (as que já existem e as já geradas
    no lote) e `variation` é o índice do item: as pessoas do mesmo pedido precisam sair diferentes, e o simulado
    usa o índice na semente. Fora do lote os dois ficam vazios e o texto do pedido é o de sempre."""

    prompt: str
    locale: str = "pt-BR"
    #: Restrições curtas e explícitas do dono: `{"gender": "feminino", "city": "Curitiba", "age": "30-35"}`.
    constraints: Mapping[str, str] = field(default_factory=dict)
    existing: Mapping[str, object] | None = None
    today: date | None = None
    avoid: tuple[PersonaEvitada, ...] = ()
    variation: int | None = None


_NAO_LETRA = re.compile(r"[^\w\s]+", re.UNICODE)
_BRANCOS = re.compile(r"\s+")


def chave_do_nome(nome: str) -> str:
    """Dois nomes que só diferem em acento, caixa, pontuação ou espaço são o MESMO nome para quem lê a lista."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", nome or "") if unicodedata.category(c) != "Mn")
    return _BRANCOS.sub(" ", _NAO_LETRA.sub(" ", sem_acento.casefold())).strip()


def nome_repetido(nome: str, evitar: Iterable[PersonaEvitada]) -> PersonaEvitada | None:
    """A pessoa de `evitar` que tem este nome (normalizado), ou `None`. É a conferência do lote DEPOIS da geração:
    o modelo é instruído a variar, e aqui se confere — duas personas do mesmo lote com o mesmo nome não servem."""
    chave = chave_do_nome(nome)
    if not chave:
        return None
    return next((p for p in evitar if chave_do_nome(p.nome) == chave), None)


PERSONA_GENERATION_SYSTEM = (
    "Você cria PERSONAS FICTÍCIAS para contas de redes sociais operadas por um sistema de automação.\n"
    "Regras, sem exceção:\n"
    "1. A pessoa é INVENTADA: nunca use nome, história ou traços de pessoa real, pública ou privada. Nome e "
    "sobrenome plausíveis para o idioma/região pedidos, com pelo menos duas palavras.\n"
    "2. ADULTA: `birth_date` em formato YYYY-MM-DD, com idade entre "
    f"{IDADE_MINIMA_GERADA} e {IDADE_MAXIMA_GERADA} anos na data de hoje informada, salvo faixa pedida.\n"
    "3. Nada de senha, código, token, número de documento, telefone, e-mail ou endereço exato em campo nenhum.\n"
    "4. Preencha TODOS os campos de voz (`traits`) com texto concreto e curto: personalidade, tom, formalidade, "
    "tamanho típico, uso de emojis, gírias, humor, interesses, estilo em mensagem direta, estilo em comentário, com "
    "quem já conhece, com desconhecidos, exemplos de frases, expressões comuns e expressões proibidas.\n"
    "5. Biografia coerente com a voz: onde nasceu, onde mora (cidade, estado, país), profissão e formação, estado "
    "civil, hobbies, preferências e o que não gosta.\n"
    "6. Crenças (`biography.beliefs`) RICAS e coerentes com a biografia (região, idade, profissão, família, "
    "história): `religion` com afiliação, prática, o que pratica, peso na vida, como aparece na fala, valores, temas "
    "que evita e resumo; `politics` com orientação no espectro, engajamento, pautas com a posição dela (curtas), "
    "como fala de política, de onde se informa (por TIPO de veículo), valores e resumo. Plausíveis e VARIADAS entre "
    "pessoas: não repita sempre a mesma religião nem o mesmo ponto do espectro; 'sem religião', 'apolitica' e "
    "'nao_declara' também são respostas legítimas. Nenhum partido, candidato, líder religioso ou figura pública "
    "pelo nome. Conduta que valerá quando ela escrever: " + CONDUTA_DAS_CRENCAS + "\n"
    "7. `visual`: aparência, estilo visual e cenário típico de foto, descritos como para um fotógrafo, sem nomes.\n"
    "8. `summary` é uma frase de apresentação; `persona_prompt` são instruções curtas de escrita na voz dela.\n"
    "9. Ao ENRIQUECER uma persona existente, mantenha exatamente o que já está preenchido e complete só o vazio "
    "(uma crença que só tem resumo é detalhada de acordo com ele).\n"
    "Responda SOMENTE com o objeto JSON do formato pedido, em português do Brasil salvo outro idioma pedido."
)


def persona_generation_user_text(req: PersonaGenerationRequest) -> str:
    """O pedido como o modelo o recebe. O texto do dono entra marcado como pedido; o que já existe, como dado."""
    hoje = (req.today or date.today()).isoformat()
    partes = [f"Data de hoje: {hoje}. Idioma e região: {sem_marcacao(req.locale, limite=20)}.",
              "<pedido>\n" + sem_marcacao(req.prompt, limite=2000) + "\n</pedido>"]
    if req.constraints:
        linhas = "\n".join(f"- {sem_marcacao(str(k), limite=40)}: {sem_marcacao(str(v), limite=200)}"
                           for k, v in req.constraints.items())
        partes.append("<restricoes>\n" + linhas + "\n</restricoes>")
    if req.existing is not None:
        partes.append("<persona_existente formato=\"json\" regra=\"manter o que está preenchido; completar só o vazio\">\n"
                      + sem_marcacao(json.dumps(req.existing, ensure_ascii=False, sort_keys=True), limite=8000)
                      + "\n</persona_existente>")
    if req.variation is not None:
        # O lote pede N pessoas pelo MESMO texto: sem esta linha, o modelo tende a devolver a mesma pessoa N vezes.
        # A variedade fica DENTRO do pedido — restrição dada (gênero, cidade, idade) continua valendo para todas.
        partes.append(f"Esta é a pessoa nº {req.variation + 1} de um LOTE gerado a partir do mesmo pedido. Ela precisa "
                      "ser outra pessoa: varie nome e sobrenome, idade, gênero, região, profissão e crenças em "
                      "relação às outras do lote e às do bloco <evitar>, sempre dentro do que o pedido e as "
                      "restrições pedem. Prefira um nome menos óbvio: as outras do lote são geradas em paralelo.")
    if req.avoid:
        linhas = "\n".join(
            "- " + sem_marcacao(p.nome, limite=80)
            + (f" — {sem_marcacao(p.resumo, limite=RESUMO_EVITAR_CHARS)}" if (p.resumo or "").strip() else "")
            for p in req.avoid[:MAX_EVITAR])
        partes.append("<evitar regra=\"não repita nome nem a mesma pessoa; estas já existem\">\n" + linhas
                      + "\n</evitar>")
    return "\n\n".join(partes)


def problemas_do_rascunho(*, nome: str, birth_date: str | None, lacunas_de_voz: Sequence[str],
                          biography: Mapping[str, object], hoje: date,
                          minimo: Sequence[str] = BIOGRAFIA_MINIMA) -> list[str]:
    """Por que um rascunho NÃO pode virar persona. Lista vazia = aceito. Cada item é uma frase para a tela."""
    problemas: list[str] = []
    if not nome_ficticio_plausivel(nome):
        problemas.append("o nome precisa ter nome e sobrenome, só com letras")
    idade = idade_em(birth_date, hoje)
    if idade is None:
        problemas.append("falta a data de nascimento (YYYY-MM-DD) ou ela é inválida")
    elif idade < MAIORIDADE:
        problemas.append(f"a persona tem {idade} anos; só se aceita pessoa com {MAIORIDADE} ou mais")
    if lacunas_de_voz:
        problemas.append("faltam campos de voz: " + ", ".join(lacunas_de_voz))
    if (faltam := lacunas_da_biografia(biography, minimo)):
        problemas.append("faltam campos da biografia: " + ", ".join(faltam))
    return problemas


def preencher_vazios(atual: Mapping[str, object], novo: Mapping[str, object]) -> dict[str, object]:
    """Enriquecimento: só o que está VAZIO em `atual` recebe o valor de `novo`; o que já existe não muda.
    Dicionários aninhados são visitados chave a chave; lista vazia conta como vazio. Por isso uma crença ausente,
    `null` ou `{}` é completada inteira, e uma crença parcial (a v1 que só virou `summary`) ganha o que falta sem
    perder o resumo (ADR-048)."""
    saida: dict[str, object] = dict(atual)
    for chave, valor in novo.items():
        existente = saida.get(chave)
        if isinstance(valor, Mapping):
            base = existente if isinstance(existente, Mapping) else {}
            mesclado = preencher_vazios(base, valor)
            if mesclado:
                saida[chave] = mesclado
        elif existente is None or existente == "" or existente == []:
            if valor not in (None, "", []):
                saida[chave] = valor
    return saida


def textos_de(valor: object) -> list[str]:
    """Todo texto dentro de um rascunho (recursivo): é sobre eles que se pergunta "tem formato de segredo?"."""
    if isinstance(valor, str):
        return [valor]
    if isinstance(valor, Mapping):
        return [t for v in valor.values() for t in textos_de(v)]
    if isinstance(valor, (list, tuple)):
        return [t for v in valor for t in textos_de(v)]
    return []

