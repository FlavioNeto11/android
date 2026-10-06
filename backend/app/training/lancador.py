"""31.139: a gravação que começa no LANÇADOR e entra no app vira `open_app` na receita.

Achado da leitura dos fluxos ensinados (06/10): 2 de 9 começam na tela inicial do aparelho, com o arraste que abre a
gaveta e o toque no ícone do app. A receita guardava esse toque no layout do lançador (a 198 nunca reproduziu: o
ícone muda de lugar de um aparelho para outro), e a outra ficou sem receita na 1ª etapa. O caminho até o app não é o
que a pessoa ensinou; é só a abertura. O 31.121 (`partida.com_abertura`) cobre a gravação que começou DENTRO do app;
este cobre a que começou no lançador.

Só funções puras sobre as entradas gravadas, para a destilação: a gravação não muda.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def e_lancador(pacote: object) -> bool:
    """O pacote é o lançador (a tela inicial do aparelho): a mesma regra de `partida.pacotes_vizinhos`."""
    return isinstance(pacote, str) and "launcher" in pacote


def abertura_pelo_lancador(vivas: Sequence[Mapping[str, object]], pacote: str | None) -> list[int]:
    """Os `seq` do começo da gravação feito NO LANÇADOR (abrir a gaveta, tocar no ícone) quando a entrada logo depois
    já está no app da sessão. `vivas`: as entradas não descartadas, em ordem. Fora disso (começou em outro lugar, o
    lançador levou a outro app, a gravação acabou no lançador, sem o app da sessão), `[]`."""
    if not pacote:
        return []
    trecho: list[int] = []
    for e in vivas:
        if e_lancador(e.get("package")) and e.get("type") != "open_app":
            trecho.append(int(str(e["seq"])))
            continue
        return trecho if trecho and e.get("package") == pacote else []
    return []


def sem_o_lancador(entradas: Sequence[Mapping[str, object]], trecho: Sequence[int], primeira: Mapping[str, object] | None,
                   app_id: str | None, pacote: str | None) -> list[dict[str, object]]:
    """As entradas da etapa sem o trecho do lançador (`abertura_pelo_lancador`); a etapa que contém a 1ª entrada da
    gravação ganha `open_app` do app no lugar dele (`seq` 0, não vai ao banco, como em `partida.com_abertura`). Sem
    trecho, iguais."""
    copia = [dict(e) for e in entradas]
    if not trecho or not (app_id and pacote and primeira):
        return copia
    fora = set(trecho)
    restantes = [e for e in copia if int(str(e.get("seq", -1))) not in fora]
    if not any(e.get("seq") == primeira.get("seq") for e in entradas):
        return restantes
    return [{"seq": 0, "type": "open_app", "app_id": app_id, "package": pacote}, *restantes]


__all__ = ["abertura_pelo_lancador", "e_lancador", "sem_o_lancador"]
