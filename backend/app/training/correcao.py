"""31.149 (P-014 b): a correção ensinada volta ao comando que falhou. Puro: sem banco, sem aparelho.

Medido em 06/10: das 6 sessões de correção, 2 viraram fluxo, os dois desligados e com 0 usos, e o molde salvo era
diferente do comando da execução que falhou (2 de 2). Na vez seguinte, o planejador livre planejava de novo e podia
repetir a falha, porque a receita ensinada morava na chave da etapa da PROPOSTA, que o plano daquele comando não tem.

Agora, além das receitas de sempre, o `save` de uma sessão de correção grava a demonstração na chave da etapa que
FALHOU (o `template_hash` dela e a chave dela). A próxima execução do mesmo comando acha a correção nessa etapa.

Quais etapas ensinadas são a correção, nesta ordem:
    1. a etapa ensinada com a MESMA chave da que falhou;
    2. senão, a única etapa ensinada;
    3. senão, todas, em sequência (a demonstração começou na tela da falha: o caminho inteiro é o que leva à
       pós-condição que faltou).

Não liga quando:
    - há efeito (na etapa que falhou ou numa ensinada): a pós-condição confere o caminho, não o commit;
    - uma etapa escolhida não virou ação reproduzível;
    - a receita usaria um nome que a execução que falhou não tem.

Os parâmetros da proposta são renomeados para os do objetivo que falhou pelo valor (sem o @ da frente e sem caixa, a
régua do 31.87 e do 31.165). Os marcadores da persona ficam como estão.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from ..taskqueue.flows import trocar_valores_por_nomes

#: Um marcador `{nome}` nas ações (o `{{saida:…}}` de saída entre etapas não casa: tem `:`).
MARCADOR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _mesmo_valor(v: str) -> str:
    return v.strip().lstrip("@").casefold()


def escolhidas(chaves_ensinadas: Sequence[str], chave_que_falhou: str) -> list[int]:
    """Os índices das etapas ensinadas que são a correção (regra no topo do módulo)."""
    iguais = [i for i, k in enumerate(chaves_ensinadas) if k == chave_que_falhou]
    if iguais:
        return iguais[:1]
    return list(range(len(chaves_ensinadas)))


def renomear(acoes: list[dict[str, object]], exemplos: Mapping[str, str],
             parametros_da_falha: Mapping[str, str]) -> list[dict[str, object]]:
    """`{param_da_proposta}` → `{param_do_objetivo_que_falhou}` quando os valores são o mesmo."""
    por_valor = {_mesmo_valor(v): n for n, v in parametros_da_falha.items() if isinstance(v, str) and _mesmo_valor(v)}
    trocas = {n: por_valor[_mesmo_valor(v)] for n, v in exemplos.items()
              if isinstance(v, str) and _mesmo_valor(v) in por_valor and por_valor[_mesmo_valor(v)] != n}
    if not trocas:
        return acoes
    texto = MARCADOR.sub(lambda m: "{" + trocas.get(m.group(1), m.group(1)) + "}", json.dumps(acoes, ensure_ascii=False))
    novas: list[dict[str, object]] = json.loads(texto)
    return novas


def faltam(acoes: list[dict[str, object]], disponiveis: set[str]) -> list[str]:
    """Os nomes que a receita usaria e a execução que falhou não tem (só os nomes, nunca valores)."""
    usados = set(MARCADOR.findall(json.dumps(acoes, ensure_ascii=False)))
    return sorted(usados - disponiveis)


def molde_do_comando(comando: str, valores: Mapping[str, str]) -> str:
    """O comando da execução que falhou com os valores trocados pelos nomes (o mais longo primeiro): é o que a revisão
    mostra em "esta correção vale para o comando <molde>". A troca tem a borda do fluxo-modelo (31.96)."""
    validos = {n: v for n, v in valores.items() if isinstance(v, str) and len(v.strip()) >= 3 and "{" not in v}
    return " ".join((trocar_valores_por_nomes(comando, validos) or comando).split())
