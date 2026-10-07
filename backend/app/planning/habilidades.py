"""31.151: o pedido parecido chega ao fluxo ensinado pelo planejador.

Medido em 06/10: 8 de 275 execuções desde 01/10 foram planejadas por fluxo ensinado, e as 8 eram lote de prova; 0 uso
real. `FlowStore.match` só casa o texto inteiro do molde, o `PlanRequest` não levava fluxo nenhum, e o `parecidos` do
31.89 F5 só sugeria no painel.

Agora, quando o comando não casa mas um fluxo ativo e no escopo PARECE (`parecidos`, com a nota mínima daqui), o
planejador recebe esses fluxos como habilidades conhecidas: a referência pública, o molde, os NOMES dos parâmetros e os
apps. O valor demonstrado nunca vai (o nome do fluxo, que pode trazê-lo, também não). O modelo produz o plano de sempre
e, se uma habilidade faz exatamente o pedido, diz qual e os valores tirados do comando. Quem decide se a escolha vale é
o código (`escolha_valida`): referência oferecida, todos os parâmetros e só eles, e cada valor presente no comando.

31.210 (P-032, decisão do dono em 07/10: "sim executa direto"): a execução pedida com `execute` cujo plano veio por
semelhança segue direto, sem parar em `planned` para a prévia. As portas de aprovação de efeito externo continuam: são
da etapa, no despacho, e não deste módulo.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

#: A nota mínima do `parecidos` para o fluxo ir ao planejador. Bem mais baixa que a da sugestão no painel (0,9): aqui
#: quem decide é o modelo, o código confere os valores, e a etapa com efeito segue pela aprovação de sempre.
#: Medido em 06/10: a paráfrase do item ("procure wifi nas configurações" × "abra as configurações e pesquise por
#: {termo}") dá 0,333, porque só "configurações" é palavra fixa parecida. A calibrar com a medida real.
NOTA_MINIMA = 0.3
#: Quantos fluxos vão ao prompt.
MAXIMO = 3

_MARCADOR = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
#: Os nomes que o APARELHO resolve na materialização (`flows.RESERVED`): nunca são oferecidos ao modelo como parâmetro.
RESERVADOS = frozenset({"account_label", "instance_id", "run_id"})
#: O molde com literal de alvo (um @, um endereço, um número longo) não vai ao prompt (revisor-segredos, 06/10): um
#: fluxo sem escopo vale para todos, e o literal de uma persona chegaria ao planejador de outra.
_LITERAL_DE_ALVO = re.compile(r"@\w|://|www\.|\d{3,}|[\w.+-]+@[\w-]+\.")


@dataclass(frozen=True, slots=True)
class HabilidadeConhecida:
    ref: str
    molde: str
    parametros: tuple[str, ...]
    apps: tuple[str, ...]
    nota: float

    def linha(self) -> str:
        params = ", ".join(self.parametros) or "nenhum"
        apps = ", ".join(self.apps) or "o do aparelho"
        return f"- ref: {self.ref} | comando-modelo: “{self.molde}” | parâmetros: {params} | apps: {apps}"


def parametros_do_molde(molde: str) -> tuple[str, ...]:
    return tuple(n for n in dict.fromkeys(_MARCADOR.findall(molde)) if n not in RESERVADOS)


def molde_oferecivel(molde: str) -> bool:
    """O molde vai ao prompt só sem literal de alvo fora dos `{nome}`."""
    return _LITERAL_DE_ALVO.search(_MARCADOR.sub(" ", molde)) is None


def bloco(habilidades: Sequence[HabilidadeConhecida]) -> str:
    """O bloco do texto de USUÁRIO (o sistema fica igual byte a byte e o cache vale), seguido de linha em branco; sem
    habilidade, nada."""
    if not habilidades:
        return ""
    linhas = "\n".join(h.linha() for h in habilidades)
    return ("<habilidades_conhecidas origem=\"fluxos ensinados e aprendidos deste parque\">\n"
            f"{linhas}\n"
            "Se UMA delas faz exatamente o que o comando pede, preencha no JSON do plano o campo "
            "\"habilidade\": {\"ref\": \"<ref>\", \"valores\": [{\"nome\": \"<parâmetro>\", \"valor\": \"<valor copiado "
            "do comando>\"}]}, com todos os parâmetros dela. Os valores saem só do comando. Se nenhuma serve, "
            "\"habilidade\": null. Produza as etapas do plano normalmente nos dois casos.\n"
            "</habilidades_conhecidas>\n\n")


@dataclass(frozen=True, slots=True)
class Escolha:
    ref: str
    valores: dict[str, str]


def escolha_do_json(raw: str) -> Escolha | None:
    """O campo `habilidade` da resposta do planejador, ou `None` (ausente ou fora do formato). Nunca levanta."""
    try:
        dados = json.loads(raw[raw.find("{"):raw.rfind("}") + 1]) if "{" in raw else None
    except ValueError:
        return None
    h = dados.get("habilidade") if isinstance(dados, dict) else None
    if not isinstance(h, dict) or not isinstance(h.get("ref"), str):
        return None
    brutos = h.get("valores") or []
    # o formato do esquema é a lista de pares `{nome, valor}`; o dicionário fica aceito para o provedor sem esquema
    pares = (brutos.items() if isinstance(brutos, dict)
             else ((p.get("nome"), p.get("valor")) for p in brutos if isinstance(p, dict)) if isinstance(brutos, list)
             else ())
    valores = {str(k): str(v) for k, v in pares if k and isinstance(v, (str, int, float))}
    return Escolha(h["ref"].strip(), valores)


def _normal(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto)
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).casefold().split())


def escolha_valida(escolha: Escolha, oferecidas: Mapping[str, HabilidadeConhecida], comando: str) -> str | None:
    """`None` quando a escolha vale; senão, o motivo (para a trilha). O valor precisa estar no comando: o modelo não
    inventa o alvo, e o valor demonstrado (que ele nunca viu) não tem como aparecer."""
    h = oferecidas.get(escolha.ref)
    if h is None:
        return "a habilidade escolhida não estava entre as oferecidas"
    if set(escolha.valores) != set(h.parametros):
        return "os valores não cobrem exatamente os parâmetros da habilidade"
    dele = _normal(comando)
    for nome, valor in escolha.valores.items():
        if not valor.strip() or _normal(valor) not in dele:
            return f"o valor de {{{nome}}} não está no comando"
    return None


__all__ = ["MAXIMO", "NOTA_MINIMA", "RESERVADOS", "Escolha", "HabilidadeConhecida", "bloco",
           "escolha_do_json", "escolha_valida", "molde_oferecivel", "parametros_do_molde"]
