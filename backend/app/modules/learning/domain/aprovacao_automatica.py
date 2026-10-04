"""A aprovação automática por política (item 30.55; pedido do dono em 04/10: "está ficando MUITA coisa para eu aprovar
pelo portal ... a plataforma ja tem dados o suficiente pra tomar essa decisão"; desenho aprovado pela orquestradora às
16:16Z, com a régua medida no 31.42).

A plataforma decide sozinha a receita ou o fluxo que hoje espera o dono numa de duas filas:
- "Para aprovar" (`validated` segurado pela D1): o gesto é PUBLICAR;
- "Revisar" (o legado publicado com efeito que nenhuma pessoa decidiu): o gesto é CONFIRMAR QUE FICA, sem disparar
  prova nenhuma.

Decide só quando TUDO vale junto:
- a classe de AGORA é A ou B (a mais restritiva entre a do dossiê e a do parecer; sem dossiê, C);
- todo app do item é de categoria `qa` (sem conta real, sem efeito fora da máquina); app sem categoria fica de fora;
- na versão atual: ≥ 1 a favor real, 0 contra efetivo e nenhuma falha de reprodução;
- a saúde não está rebaixando (`degradando`, `obsoleto_provavel`);
- o parecer do curador não pede para rebaixar, desativar, substituir, fundir ou aposentar. Parecer ausente ou
  `pedir_evidencia` não barra: a delegação do dono cobre (é a R1 do 31.42, que bateu 5 de 5 com a decisão humana);
- não é reaprendido (30.23), não tem texto de pessoa, nenhum veto de desligamento o alcança;
- a plataforma ainda não decidiu este item: o que o dono desfez fica com ele.

Fica com o dono o resto: conta real (Instagram), credencial e desafio, classe C, o que o curador quer rebaixar ou
descartar, app sem categoria e a mudança desta régua (que muda a versão da regra).

Toda decisão vira uma linha da trilha com `decided_by = "plataforma"` e o motivo `auto:<regra> v<n> — <fatos>` (na
confirmação, depois do prefixo `confirmado que fica: `). O formato é contrato: a Canais (28.25) o lê por adaptador. Sem
`@` antes da versão: `auto:<regra>@v1` tem a forma de `usuário:senha@host`, e a triagem de credencial do livro recusava a
confirmação (medido no ensaio sobre a cópia do banco, 04/10).
Desfazer é a ação de sempre da pessoa (`published → validated`, ou desligar).

Puro: recebe os fatos já lidos; sem I/O, sem relógio.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.modules.learning.domain.curador import Decisao
from app.modules.learning.domain.politica_de_risco import ClasseDeRisco
from app.modules.learning.domain.saude import Rotulo

#: Quem decide pela régua: uma linha da trilha com este `decided_by` é da plataforma, nunca de uma pessoa. A rota não o
#: aceita como operador (`shared.costuras.autor_do_gesto`), então só este caminho o escreve.
PLATAFORMA = "plataforma"

#: Sobe quando a régua muda (a mudança é do dono): o motivo antigo continua legível pela versão.
VERSAO_DA_REGRA = 1

#: Os tipos que a régua alcança.
KINDS = ("receita", "fluxo")


class ModoDaAprovacao(StrEnum):
    OFF = "off"            # nada roda (o padrão)
    SHADOW = "shadow"      # registra o que decidiria; não decide
    ON = "on"              # decide


class Fila(StrEnum):
    PARA_APROVAR = "para_aprovar"
    REVISAR = "revisar"


class Gesto(StrEnum):
    PUBLICAR = "publicar"
    CONFIRMAR = "confirmar_que_fica"


class Regra(StrEnum):
    """O nome estável de cada regra (vai no motivo, `auto:<regra> v<n>`)."""

    QA_PARA_APROVAR = "qa_para_aprovar"
    QA_REVISAR = "qa_revisar"


_REGRA_DA_FILA = {Fila.PARA_APROVAR: Regra.QA_PARA_APROVAR, Fila.REVISAR: Regra.QA_REVISAR}
_GESTO_DA_FILA = {Fila.PARA_APROVAR: Gesto.PUBLICAR, Fila.REVISAR: Gesto.CONFIRMAR}

#: O que o curador sugere e que deixa o item com o dono (a plataforma nunca rebaixa nem descarta por esta régua).
PARECERES_CONTRA = frozenset({Decisao.REBAIXAR, Decisao.DESATIVAR, Decisao.SUBSTITUIR, Decisao.FUNDIR,
                              Decisao.POSSIVELMENTE_OBSOLETO})

#: Saúde que rebaixa (os gatilhos do D-5 e do 30.14).
SAUDES_QUE_REBAIXAM = frozenset({Rotulo.DEGRADANDO, Rotulo.OBSOLETO_PROVAVEL})


@dataclass(frozen=True, slots=True)
class ParecerParaAprovar:
    """O parecer mais recente do curador sobre o item, como a aplicação o leu."""

    id: str
    decisao: Decisao | None        # `None`: revisão sem parecer válido
    simulado: bool
    decidido: bool                 # uma pessoa já aceitou ou recusou: o parecer não pesa mais


@dataclass(frozen=True, slots=True)
class FatosDaAprovacao:
    kind: str
    ref: str
    fila: Fila | None
    apps: tuple[str, ...]          # todos os pacotes do item (o principal e os do fluxo de mais de um app)
    apps_de_teste: frozenset[str]  # os pacotes de categoria `qa`
    classe: ClasseDeRisco          # a de agora, a mais restritiva entre o dossiê e o parecer
    a_favor: int                   # reais e efetivos, da versão atual (receita: reprodução e sombra de acerto)
    contra: int                    # reais e efetivos, da versão atual (receita: a sombra que discordou)
    falhas_de_reproducao: int      # receita: `replay_fail`; fluxo: 0 (o uso que falhou já é contra)
    execucoes: int
    aparelhos: int
    saude: Rotulo | None
    parecer: ParecerParaAprovar | None
    reaprendido: bool
    texto_de_pessoa: bool
    vetado: bool
    ja_decidido: bool              # a plataforma já decidiu este item uma vez


class MotivoDeFora(StrEnum):
    """Por que a plataforma NÃO decide o item. Vocabulário fechado, na ordem em que a régua confere."""

    TIPO_FORA = "tipo_fora"
    FORA_DAS_FILAS = "fora_das_filas"
    JA_DECIDIDO = "ja_decidido_pela_plataforma"
    APP_FORA_DO_QA = "app_fora_do_qa"
    CLASSE_C = "classe_c"
    SEM_A_FAVOR = "sem_a_favor"
    EVIDENCIA_CONTRA = "evidencia_contra"
    FALHA_DE_REPRODUCAO = "falha_de_reproducao"
    SAUDE_REBAIXANDO = "saude_rebaixando"
    PARECER_CONTRA = "parecer_contra"
    REAPRENDIDO = "reaprendido"
    TEXTO_DE_PESSOA = "texto_de_pessoa"
    VETADO = "vetado"


@dataclass(frozen=True, slots=True)
class Avaliacao:
    regra: Regra | None
    gesto: Gesto | None
    motivos: tuple[MotivoDeFora, ...]

    @property
    def decide(self) -> bool:
        return self.regra is not None and not self.motivos


def avaliar(f: FatosDaAprovacao) -> Avaliacao:
    """A régua inteira. Devolve TODOS os motivos de fora (não só o primeiro): o relatório da sombra diz o que falta a
    cada item, e o teste prova que cada condição recusa sozinha."""
    if f.kind not in KINDS:
        return Avaliacao(None, None, (MotivoDeFora.TIPO_FORA,))
    if f.fila is None:
        return Avaliacao(None, None, (MotivoDeFora.FORA_DAS_FILAS,))
    motivos: list[MotivoDeFora] = []
    if f.ja_decidido:
        motivos.append(MotivoDeFora.JA_DECIDIDO)
    if not f.apps or any(a not in f.apps_de_teste for a in f.apps):
        motivos.append(MotivoDeFora.APP_FORA_DO_QA)
    if f.classe not in (ClasseDeRisco.A, ClasseDeRisco.B):
        motivos.append(MotivoDeFora.CLASSE_C)
    if f.a_favor < 1:
        motivos.append(MotivoDeFora.SEM_A_FAVOR)
    if f.contra > 0:
        motivos.append(MotivoDeFora.EVIDENCIA_CONTRA)
    if f.falhas_de_reproducao > 0:
        motivos.append(MotivoDeFora.FALHA_DE_REPRODUCAO)
    if f.saude in SAUDES_QUE_REBAIXAM:
        motivos.append(MotivoDeFora.SAUDE_REBAIXANDO)
    if parecer_que_pesa(f.parecer) in PARECERES_CONTRA:
        motivos.append(MotivoDeFora.PARECER_CONTRA)
    if f.reaprendido:
        motivos.append(MotivoDeFora.REAPRENDIDO)
    if f.texto_de_pessoa:
        motivos.append(MotivoDeFora.TEXTO_DE_PESSOA)
    if f.vetado:
        motivos.append(MotivoDeFora.VETADO)
    return Avaliacao(_REGRA_DA_FILA[f.fila], _GESTO_DA_FILA[f.fila], tuple(motivos))


def parecer_que_pesa(p: ParecerParaAprovar | None) -> Decisao | None:
    """O parecer real e ainda sem decisão de pessoa; o simulado e o já decidido não pesam."""
    if p is None or p.simulado or p.decidido:
        return None
    return p.decisao


class Acao(StrEnum):
    NADA = "nada"
    REGISTRAR = "registrar"    # marca, uma vez, o que decidiria (sombra)
    DECIDIR = "decidir"        # publica ou confirma, com a linha da plataforma na trilha


def acao(modo: ModoDaAprovacao, avaliacao: Avaliacao) -> Acao:
    if modo is ModoDaAprovacao.OFF or not avaliacao.decide:
        return Acao.NADA
    return Acao.DECIDIR if modo is ModoDaAprovacao.ON else Acao.REGISTRAR


# ------------------------------------------------------------------ o motivo na trilha (contrato com a Canais)
def motivo(f: FatosDaAprovacao, a: Avaliacao) -> str:
    """`auto:<regra> v<n> — <fatos>`: a regra que decidiu e os números em que ela se apoiou, legíveis na trilha. A receita
    conta pela fonte (reprodução e sombra), sem execução nem aparelho: aí a frase não os cita."""
    if a.regra is None:
        raise ValueError("só o item que a régua decide tem motivo")
    p = f.parecer
    pesa = parecer_que_pesa(p)
    parecer = (f"parecer {pesa.value} ({p.id})" if pesa is not None and p is not None
               else "sem parecer que pese (delegação do dono)")
    apps = ", ".join(f.apps)
    onde = f" em {f.execucoes} execuções e {f.aparelhos} aparelhos" if f.execucoes else ""
    return (f"auto:{a.regra.value} v{VERSAO_DA_REGRA} — classe {f.classe.value}; app {apps} (qa); "
            f"{f.a_favor} a favor{onde}, {f.contra} contra; "
            f"{f.falhas_de_reproducao} falhas de reprodução; saúde {f.saude.value if f.saude else 'sem rótulo'}; "
            f"{parecer}")


_MARCA = re.compile(r"(?:^|: )auto:(?P<regra>[a-z_]+) v(?P<versao>\d+)(?: — |$)")


def regra_do_motivo(reason: str) -> tuple[str, int] | None:
    """A regra e a versão de um motivo escrito pela plataforma (publicação ou confirmação), ou `None`. É o que o
    adaptador da Canais lê; a linha só conta se o `decided_by` for `PLATAFORMA`."""
    m = _MARCA.search(reason or "")
    return (m.group("regra"), int(m.group("versao"))) if m else None


__all__ = ["KINDS", "PARECERES_CONTRA", "PLATAFORMA", "SAUDES_QUE_REBAIXAM", "VERSAO_DA_REGRA", "Acao", "Avaliacao",
           "FatosDaAprovacao", "Fila", "Gesto", "ModoDaAprovacao", "MotivoDeFora", "ParecerParaAprovar", "Regra",
           "acao", "avaliar", "motivo", "parecer_que_pesa", "regra_do_motivo"]
