"""31.111 F4: o diagnóstico da etapa que falhou (30.13) pré-preenche o ensino que nasce dela.

O ensino a partir de uma falha (31.111 F1-F3) abre a sessão já ligada à tentativa que falhou. Aqui a causa provável,
determinística, vira o que a pessoa lê ao começar: a intenção sugerida e a pergunta do que mostrar. Nenhuma IA: a causa
`indeterminada` é dita como "não deu para saber", e a única chamada de IA continua sendo a proposta do próprio ensino.

Só funções puras sobre o `Diagnostico`; quem lê a tentativa é a infraestrutura (`FontesDeFalhaSql.chave_da_tentativa`).
"""
from __future__ import annotations

from app.modules.learning.domain.diagnostico import Diagnostico
from app.modules.learning.domain.vocabulario import CausaProvavel

#: Em poucas palavras, para a intenção sugerida ("Corrigir a etapa «…»: <isto>").
ROTULO: dict[CausaProvavel, str] = {
    CausaProvavel.TETO_DE_IA: "a IA gastou o orçamento da etapa",
    CausaProvavel.PROVEDOR_DE_IA: "a IA estava indisponível ou recusou",
    CausaProvavel.SESSAO_OU_AUTENTICACAO: "a conta ou o login atrapalhou",
    CausaProvavel.APARELHO: "o aparelho falhou, não o caminho",
    CausaProvavel.PLANO: "o plano ligou mal as etapas",
    CausaProvavel.INFORMACAO_DA_PESSOA: "faltou um dado de quem pediu",
    CausaProvavel.CATALOGO_RECUSOU: "a guarda da ação não foi atendida",
    CausaProvavel.VERIFICADOR: "a comprovação não bateu com a tela",
    CausaProvavel.RECEITA_DIVERGIU: "a receita gravada não serve mais",
    CausaProvavel.VERSAO_NOVA: "o app mudou de versão",
    CausaProvavel.LICAO_ATRAPALHA: "uma lição parece atrapalhar",
    CausaProvavel.TELA_DESCONHECIDA: "a etapa parou numa tela desconhecida",
    CausaProvavel.FALTA_CONHECIMENTO: "só a IA tentou, sem receita nem lição",
    CausaProvavel.INDETERMINADA: "a causa não ficou clara",
}

#: O que a pessoa responde ou mostra ao corrigir.
PERGUNTA: dict[CausaProvavel, str] = {
    CausaProvavel.TETO_DE_IA: "Mostre o caminho curto, toque a toque, para a etapa não depender da IA.",
    CausaProvavel.PROVEDOR_DE_IA: "Se o caminho é sempre o mesmo, grave-o: a receita conduz sem a IA.",
    CausaProvavel.SESSAO_OU_AUTENTICACAO: "Confira se o aparelho está na conta certa e mostre o que a etapa devia fazer.",
    CausaProvavel.APARELHO: "O app abre bem neste aparelho? Se abrir, mostre a etapa do começo.",
    CausaProvavel.PLANO: "Mostre a ordem certa dos passos desta etapa.",
    CausaProvavel.INFORMACAO_DA_PESSOA: "Que dado faltou, e onde ele entra na tela?",
    CausaProvavel.CATALOGO_RECUSOU: "Mostre a tela em que a ação pode acontecer.",
    CausaProvavel.VERIFICADOR: "O que na tela mostra que a etapa deu certo?",
    CausaProvavel.RECEITA_DIVERGIU: "Mostre o caminho de hoje, toque a toque.",
    CausaProvavel.VERSAO_NOVA: "Mostre o caminho nesta versão do app.",
    CausaProvavel.LICAO_ATRAPALHA: "Mostre o caminho certo desta etapa.",
    CausaProvavel.TELA_DESCONHECIDA: "Que tela é essa, e como se sai dela?",
    CausaProvavel.FALTA_CONHECIMENTO: "Mostre o caminho uma vez.",
    CausaProvavel.INDETERMINADA: "O que a etapa devia ter feito nesta tela?",
}


#: 31.116 (adendo v1.82): a etapa em `waiting_user` não falhou, PAROU esperando a pessoa (a IA concluiu que não dá e o
#: executor não aceitou). A pergunta diz o que ensinar para ela seguir, em vez da pergunta da causa de uma falha.
PERGUNTA_ESPERANDO = ("A etapa não falhou: parou esperando você. Mostre, a partir desta tela, o que fazer para ela "
                      "seguir.")


def pergunta_da_etapa(status: str, diagnostico: dict[str, object] | None) -> str | None:
    """A pergunta da sugestão do ensino pelo ESTADO da etapa: `waiting_user` tem a própria; nos outros (falhou ou ficou
    incerta), a do diagnóstico, ou nenhuma quando ele não veio."""
    if status == "waiting_user":
        return PERGUNTA_ESPERANDO
    pergunta = diagnostico.get("pergunta") if diagnostico else None
    return pergunta if isinstance(pergunta, str) else None


def para_o_ensino(d: Diagnostico) -> dict[str, object]:
    """`origin.diagnostico` da sessão: a causa, os fatos que a sustentam, a proposta do 30.13 (se houver), o rótulo
    curto e a pergunta. `amostra` é 1 (a própria tentativa) ou 0 (só o tipo decidiu)."""
    return {"causa": d.causa.value, "rotulo": ROTULO[d.causa], "pergunta": PERGUNTA[d.causa],
            "fatos": [{"codigo": f.codigo, "valor": f.valor} for f in d.fatos],
            "proposta": ({"tipo": d.proposta.tipo.value, "alvo": d.proposta.alvo} if d.proposta else None),
            "amostra": d.amostra}


def intencao_sugerida(titulo: str, diagnostico: dict[str, object] | None) -> str:
    """A intenção que a sessão recebe quando a pessoa não escreveu a dela: o padrão do F1, mais a causa provável quando
    ela é conhecida (a `indeterminada` não acrescenta nada à intenção; a pergunta já diz o que mostrar)."""
    base = f"Corrigir a etapa «{titulo}»"
    rotulo = diagnostico.get("rotulo") if diagnostico else None
    conhecida = diagnostico is not None and diagnostico.get("causa") != CausaProvavel.INDETERMINADA.value
    return (f"{base}: {rotulo}" if conhecida and isinstance(rotulo, str) and rotulo else base)[:400]


__all__ = ["PERGUNTA", "PERGUNTA_ESPERANDO", "ROTULO", "intencao_sugerida", "para_o_ensino", "pergunta_da_etapa"]
