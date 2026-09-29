"""Pacote A9 do ADR-054: preferências como SUGESTÃO pré-preenchida — nunca resposta automática para ação com efeito.

Duas origens, as duas sem IA e lidas do que a pessoa já faz:

- **resposta a uma pergunta** (`needs_input`): o sinal `respondeu_pergunta` do A2 guarda o campo e o sha256 do comando
  respondido — nunca o valor. A mesma resposta pelo menos 3 vezes, para o mesmo campo, no mesmo MODELO de comando (o
  comando que perguntou, normalizado) e no mesmo perfil, sem nenhuma resposta diferente, vira candidata; só então o
  valor é relido da execução sucessora e passa pela triagem de credencial. É texto de pessoa (`human_origin=1`): quem
  valida e publica é o dono (D1);
- **escolha no desambiguador**: o empate entre habilidades vira pergunta, e a pessoa responde reescrevendo o comando.
  A escolha é a habilidade que a sucessora resolveu, com a VERSÃO que ela rodou (`runs.skill_version`); a varredura a
  grava como `escolheu_habilidade` (o evento com os candidatos morre com a retenção dos eventos). Não é texto de
  pessoa: 3 escolhas iguais em 3 execuções validam pelo sistema, e ele publica sozinho só a que não alimenta etapa com
  efeito externo, com o modo em `on` (D1). O efeito é conferido nos planos das sucessoras observadas, isto é, nas
  VERSÕES observadas: a preferência guarda quais foram na proveniência (`provenance.versoes`), fora do conteúdo — o
  veto casa pelo `content_hash`, e uma versão nova não é uma escolha nova: o que uma pessoa desligou não volta.

Campo que é alvo de terceiro (destinatário, perfil, conversa...) NUNCA vira padrão. Uma resposta diferente depois
contradiz a preferência, e o sistema a desliga (rebaixar é automático).

Consumo, sempre como sugestão e só no modo `on`, só das PUBLICADAS:

- a resposta só PRÉ-PREENCHE (`sugestoes`): a leitura não grava nada, não cria sucessora e não muda a execução — a
  pessoa confirma;
- no desambiguador, a porta `PreferenceSource` das habilidades (`preferida`): decide sozinha só quando nenhuma das
  preferências dos perfis da execução tem efeito externo E a versão candidata é uma das conferidas em todas elas
  (`PreferenceHint.versions`; quem confere a versão de agora é a etapa); com efeito, ou numa versão nova da
  habilidade (que pode ter ganho uma etapa com efeito), a pergunta continua, com a opção pré-selecionada.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.modules.learning.application.ports import NovaEvidencia, NovoSinal, RepositorioDeAprendizado, TriagemDeTexto
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.ciclo import SYSTEM_ACTOR, ErroDeAprendizado, NaoEncontrado, SkillState
from app.modules.learning.domain.livro import Escopo, ItemDeAprendizado, NovoItem
from app.modules.learning.domain.promocao import Decisao, Limiares, veredito_de_repeticao
from app.modules.learning.domain.vocabulario import (LivroKind, Modo, Papel, Polaridade, Posicao, SignalKind,
                                                     SourceKind)
from app.util import to_iso

#: O campo da pergunta de empate entre habilidades (`ambiguity_question`).
CAMPO_DA_HABILIDADE = "skill"
#: 3 observações iguais em 3 execuções distintas, nenhuma contra. Aparelho não conta: quem escolhe é a pessoa.
LIMIARES = Limiares(n_min=3, execucoes_min=3, aparelhos_min=0, contra_max=0)
#: Quanto a varredura olha para trás ao registrar as escolhas (o evento com os candidatos tem retenção).
JANELA_DA_VARREDURA = timedelta(days=2)
#: Quem deu o sinal de um gesto do painel (a regra `_quem`: sem nome, `panel`).
PAINEL = "panel"

#: Alvo de terceiro: nunca vira padrão (responder por alguém "para quem" é decisão da vez, não preferência).
_TERCEIRO = re.compile(r"recipient|destinat|alvo|target|counterpart|contraparte|user|usuari|perfil|profile|conversa|"
                       r"thread|contato|contact|handle|pessoa|person|conta|account|quem|@", re.IGNORECASE)
_VIVOS = (SkillState.CANDIDATE, SkillState.VALIDATED, SkillState.PUBLISHED)


def campo_de_terceiro(campo: str) -> bool:
    return bool(_TERCEIRO.search(campo))


def modelo_do_comando(texto: str) -> str:
    """O modelo do comando que perguntou: o texto sem caixa, sem espaço sobrando e sem pontuação nas pontas."""
    normal = " ".join(texto.casefold().split()).strip(" .!?;:")
    return "cmd-" + hashlib.sha256(normal.encode()).hexdigest()[:16]


def chave_da_resposta(resposta_sha256: str) -> str:
    """A identidade de uma resposta no livro: um prefixo do sha256 do comando respondido. O hash inteiro (64 hex) tem
    FORMATO de token e a triagem de credencial o recusaria — com razão: nada com cara de segredo entra no livro."""
    return "resp-" + resposta_sha256.strip().lower()[:16]


def modelo_do_conjunto(skill_ids: Sequence[str]) -> str:
    """O modelo de um empate: o CONJUNTO de habilidades empatadas (sem versão), qualquer que seja o valor pedido —
    "abra @ana" e "abra @bia" empatam do mesmo jeito."""
    return "emp-" + hashlib.sha256("|".join(sorted(set(skill_ids))).encode()).hexdigest()[:16]


# ------------------------------------------------------------------ o que a leitura devolve
@dataclass(frozen=True, slots=True)
class Observacao:
    """Uma resposta (ou escolha) de uma pessoa, já com o perfil e o modelo. `valor` é a IDENTIDADE da resposta:
    `chave_da_resposta` do sha256 do comando respondido, ou o `skill_id` escolhido."""

    origem: str                      # 'signal:<id>': a evidência
    run_id: str                      # a execução que perguntou
    perfil: str
    campo: str
    modelo: str
    valor: str
    app_package: str
    run_sucessora: str | None
    simulated: bool
    opcoes: tuple[str, ...] = ()     # na escolha: o conjunto empatado
    pergunta: str = ""               # na resposta: o comando que perguntou
    versao: int | None = None        # na escolha: a versão que a sucessora rodou (`None`: não se sabe)


@dataclass(frozen=True, slots=True)
class EscolhaObservada:
    """Uma escolha no desambiguador, lida da sucessora, ainda por gravar como `escolheu_habilidade`."""

    run_id: str
    perfil: str
    conjunto: tuple[str, ...]
    escolhida: str
    run_sucessora: str
    app_package: str
    simulated: bool
    versao: int | None               # `runs.skill_version` da sucessora (o fluxo legado é sempre a 1)


@dataclass(frozen=True, slots=True)
class PerguntasAbertas:
    status: str
    comando: str
    perfis: tuple[str | None, ...]
    campos: tuple[str, ...]


class LeituraDePreferencias(Protocol):
    def respostas(self) -> list[Observacao]: ...
    def escolhas_a_registrar(self, desde: str) -> list[EscolhaObservada]: ...
    def escolhas(self) -> list[Observacao]: ...
    def comando(self, run_id: str) -> str | None: ...

    def plano_tem_efeito(self, run_id: str) -> bool:
        """Alguma etapa com efeito externo no plano da execução; plano ilegível ou ausente conta como efeito."""
        ...

    def perguntas_abertas(self, run_id: str) -> PerguntasAbertas | None: ...


# ------------------------------------------------------------------ o que o consumo devolve
@dataclass(frozen=True, slots=True)
class Sugestao:
    campo: str
    valor: str
    item_id: str


@dataclass(frozen=True, slots=True)
class Preferida:
    skill_id: str
    decide: bool
    itens: tuple[str, ...]
    #: As versões conferidas sem efeito em TODAS as preferências dos perfis: `decide` só vale para uma delas.
    versoes: tuple[int, ...] = ()

    @property
    def detalhe(self) -> str:
        quem = ", ".join(self.itens)
        return (f"preferência publicada ({quem}), sem efeito externo" if self.decide
                else f"preferência publicada ({quem}) com efeito externo: a pessoa confirma")


# ------------------------------------------------------------------ o serviço
class ServicoDePreferencias:
    """Cumpre `PassoDeCuradoria` (`nome`, `executar`): a composição o registra na curadoria periódica."""

    nome = "preferencias"

    def __init__(self, servico: LearningService, repo: RepositorioDeAprendizado, leitura: LeituraDePreferencias,
                 triagem: TriagemDeTexto) -> None:
        self._servico = servico
        self._repo = repo
        self._leitura = leitura
        self._triagem = triagem

    @property
    def modo(self) -> Modo:
        return self._servico.ajustes.modo_preferencias

    def _consome(self) -> bool:
        """Sugere e decide só com o aprendizado ligado e o modo em `on` (`enabled: false` desliga tudo)."""
        return self._servico.ajustes.enabled and self.modo is Modo.ON

    # ---------------------------------------------------------------- curadoria
    def executar(self, agora: datetime) -> int:
        return self.minerar(agora)

    def minerar(self, agora: datetime) -> int:
        modo = self.modo
        if modo is Modo.OFF:
            return 0
        feito = sum(self._registrar_escolha(e) for e in self._leitura.escolhas_a_registrar(
            to_iso(agora - JANELA_DA_VARREDURA)))
        grupos: dict[tuple[str, str, str], list[Observacao]] = defaultdict(list)
        for o in (*self._leitura.respostas(), *self._leitura.escolhas()):
            if o.simulated or not o.perfil or (o.campo != CAMPO_DA_HABILIDADE and campo_de_terceiro(o.campo)):
                continue
            grupos[(o.campo, o.modelo, o.perfil)].append(o)
        vivas: dict[tuple[str, str, str], list[ItemDeAprendizado]] = defaultdict(list)
        for i in self._repo.itens(kind=LivroKind.PREFERENCIA):
            if i.state in _VIVOS:
                vivas[(i.escopo.capability, i.escopo.step_hash, i.escopo.profile_id)].append(i)
        for chave, observacoes in grupos.items():
            feito += self._grupo(chave, observacoes, vivas.get(chave, []), modo)
        return feito

    def _registrar_escolha(self, e: EscolhaObservada) -> int:
        gravado = self._servico.registrar_sinal(NovoSinal(
            kind=SignalKind.ESCOLHEU_HABILIDADE, source_ref=f"escolha:{e.run_id}:{e.perfil}", created_by=PAINEL,
            polarity=Polaridade.NEUTRAL, run_id=e.run_id, profile_id=e.perfil, app_package=e.app_package,
            data={"conjunto": list(e.conjunto), "modelo": modelo_do_conjunto(e.conjunto), "escolhida": e.escolhida,
                  "versao": e.versao, "run_sucessora": e.run_sucessora},
            simulated=e.simulated))
        return int(gravado is not None)

    def _grupo(self, chave: tuple[str, str, str], observacoes: list[Observacao], vivas: list[ItemDeAprendizado],
               modo: Modo) -> int:
        if not vivas:
            valores = {o.valor for o in observacoes}
            execucoes = {o.run_id for o in observacoes}
            if len(valores) != 1 or len(execucoes) < LIMIARES.n_min:
                return 0                   # ainda não repetiu, ou já houve resposta diferente: nada nasce
            nascida = self._nascer(chave, observacoes)
            if nascida is None:
                return 0
            vivas = [nascida]
        feito = 0
        for item in vivas:
            feito += self._evidenciar(item, observacoes)
            feito += self._decidir(item, modo)
        return feito

    def _nascer(self, chave: tuple[str, str, str], observacoes: list[Observacao]) -> ItemDeAprendizado | None:
        campo, modelo, perfil = chave
        ultima = observacoes[-1]
        efeito = any(self._leitura.plano_tem_efeito(o.run_sucessora) if o.run_sucessora else True
                     for o in observacoes)
        proveniencia: list[str] = list(dict.fromkeys(o.run_id for o in observacoes))[:20]
        if campo == CAMPO_DA_HABILIDADE:
            # O efeito acima foi lido nos planos destas versões, e só delas: a versão nova de amanhã pode ter ganho
            # uma etapa com efeito, e aí a preferência só pré-seleciona (a etapa confere). Congeladas no nascimento, e
            # na PROVENIÊNCIA: no conteúdo, mudariam o `content_hash`, e a escolha que o dono desligou voltaria (e
            # seria publicada pelo sistema) só porque apareceu uma versão nova.
            versoes = sorted({o.versao for o in observacoes if o.versao is not None})
            vistas = ", ".join(f"v{v}" for v in versoes) or "nenhuma sabida"
            novo = NovoItem(
                kind=LivroKind.PREFERENCIA,
                escopo=Escopo(app=ultima.app_package, capability=campo, step_hash=modelo, role=Papel.RESOLVER.value,
                              profile_id=perfil),
                content={"campo": campo, "modelo": modelo, "chave": ultima.valor, "valor": ultima.valor,
                         "opcoes": list(ultima.opcoes)},
                summary=(f"No empate entre {', '.join(ultima.opcoes)}, a pessoa escolhe {ultima.valor}"
                         f" (versões observadas: {vistas})"),
                source_kind=SourceKind.DISAMBIGUATION, side_effect=efeito,
                provenance={"runs": list(proveniencia), "regra": "a9-escolha-repetida", "versoes": list(versoes)})
        else:
            texto = self._leitura.comando(ultima.run_sucessora) if ultima.run_sucessora else None
            if not texto or self._triagem.recusa(texto):
                return None
            novo = NovoItem(
                kind=LivroKind.PREFERENCIA,
                escopo=Escopo(app=ultima.app_package, capability=campo, step_hash=modelo, profile_id=perfil),
                content={"campo": campo, "modelo": modelo, "chave": ultima.valor, "valor": texto,
                         "pergunta": ultima.pergunta},
                summary=f"Resposta a “{campo}”: {_curto(texto)}", source_kind=SourceKind.ANSWER, side_effect=efeito,
                provenance={"runs": list(proveniencia), "regra": "a9-resposta-repetida"})
        try:
            return self._servico.propor(novo)
        except ErroDeAprendizado:          # vetada, texto recusado, outra sessão chegou antes
            return None

    def _evidenciar(self, item: ItemDeAprendizado, observacoes: list[Observacao]) -> int:
        chave = item.content.get("chave")
        feito = 0
        for o in observacoes:
            posicao = Posicao.FOR if o.valor == chave else Posicao.AGAINST
            feito += int(self._repo.registrar_evidencia(NovaEvidencia(
                item_ref=item.id, stance=posicao, origin_ref=o.origem, simulated=o.simulated, run_id=o.run_id,
                detail="mesma resposta" if posicao is Posicao.FOR else "resposta diferente")))
        return feito

    def _decidir(self, item: ItemDeAprendizado, modo: Modo) -> int:
        """O D1: contradita → desligada (sistema); sem texto de pessoa e repetida → validada; sem efeito e com o
        modo em 'on' → publicada. Com efeito ou com texto de pessoa, espera o dono."""
        atual = self._repo.item(item.id)
        if atual is None or atual.state not in _VIVOS:
            return 0
        veredito = veredito_de_repeticao(self._repo.evidencias(atual.id), LIMIARES)
        try:
            if veredito.decisao is Decisao.CONTRADITA:
                self._mover(atual, SkillState.DISABLED, f"contradita: {veredito.contra} resposta(s) diferente(s)")
                return 1
            if atual.human_origin:
                return 0                   # texto de pessoa: o dono valida e publica
            feito = 0
            if atual.state is SkillState.CANDIDATE and veredito.decisao is Decisao.PROMOVE:
                self._mover(atual, SkillState.VALIDATED,
                            f"repetiu: {veredito.a_favor} escolhas iguais em {veredito.execucoes} execuções")
                feito += 1
                atual = self._repo.item(atual.id) or atual
            if atual.state is SkillState.VALIDATED and not atual.side_effect and modo is Modo.ON:
                self._mover(atual, SkillState.PUBLISHED, "sem efeito externo e repetida: publicada sozinha (D1)")
                feito += 1
            return feito
        except ErroDeAprendizado:          # outra sessão decidiu antes: a próxima curadoria confere de novo
            return 0

    def _mover(self, item: ItemDeAprendizado, para: SkillState, motivo: str) -> None:
        self._servico.mudar_estado(LivroKind.PREFERENCIA, item.id, para, by=SYSTEM_ACTOR, reason=motivo)

    # ---------------------------------------------------------------- consumo
    def _publicadas(self, campo: str, modelo: str) -> dict[str, ItemDeAprendizado]:
        """As preferências publicadas deste campo e modelo, por perfil."""
        return {i.escopo.profile_id: i for i in self._repo.itens(kind=LivroKind.PREFERENCIA,
                                                                 state=SkillState.PUBLISHED)
                if i.escopo.capability == campo and i.escopo.step_hash == modelo}

    def preferida(self, candidatas: Sequence[str],
                  perfis: tuple[str | None, ...] | None) -> Preferida | None:
        """A escolha que vale para TODOS os perfis da execução neste empate, ou `None`. Perfil sem preferência (ou
        aparelho sem persona, ou prévia sem aparelho): ninguém decide por ele."""
        if not self._consome() or not perfis or any(p is None for p in perfis):
            return None
        por_perfil = self._publicadas(CAMPO_DA_HABILIDADE, modelo_do_conjunto(candidatas))
        itens = [por_perfil.get(p) for p in dict.fromkeys(p for p in perfis if p)]
        valores = {str(i.content.get("valor")) for i in itens if i is not None}
        if any(i is None for i in itens) or len(valores) != 1:
            return None
        valor = valores.pop()
        if valor not in candidatas:
            return None
        vivos = [i for i in itens if i is not None]
        conferidas = set.intersection(*(set(versoes_conferidas(i)) for i in vivos))
        return Preferida(valor, decide=not any(i.side_effect for i in vivos), itens=tuple(i.id for i in vivos),
                         versoes=tuple(sorted(conferidas)))

    def sugestoes(self, run_id: str) -> tuple[Sugestao, ...]:
        """O que PRÉ-PREENCHER nas perguntas abertas de uma execução em `needs_input`. Só leitura: nada é gravado,
        nenhuma sucessora nasce — a pessoa confirma."""
        abertas = self._leitura.perguntas_abertas(run_id)
        if abertas is None:
            raise NaoEncontrado(f"Não há execução '{run_id}'.")
        if (not self._consome() or abertas.status != "needs_input" or not abertas.perfis
                or any(p is None for p in abertas.perfis)):
            return ()
        modelo = modelo_do_comando(abertas.comando)
        saida: list[Sugestao] = []
        for campo in dict.fromkeys(abertas.campos):
            if campo == CAMPO_DA_HABILIDADE or campo_de_terceiro(campo):
                continue
            por_perfil = self._publicadas(campo, modelo)
            itens = [por_perfil.get(p) for p in dict.fromkeys(p for p in abertas.perfis if p)]
            if not itens or any(i is None or i.source_kind is not SourceKind.ANSWER for i in itens):
                continue
            valores = {str(i.content.get("valor")) for i in itens if i is not None}
            primeiro = itens[0]
            if len(valores) == 1 and primeiro is not None:
                saida.append(Sugestao(campo, valores.pop(), primeiro.id))
        return tuple(saida)


def versoes_conferidas(item: ItemDeAprendizado) -> tuple[int, ...]:
    """As versões em que a falta de efeito da escolha foi conferida (`provenance.versoes`); item sem elas (ou
    ilegível): nenhuma — a preferência só pré-seleciona."""
    bruto = item.provenance.get("versoes")
    if not isinstance(bruto, list):
        return ()
    return tuple(sorted({v for v in bruto if isinstance(v, int) and not isinstance(v, bool) and v >= 1}))


def _curto(texto: str, n: int = 80) -> str:
    limpo = " ".join(texto.split())
    return limpo if len(limpo) <= n else limpo[:n].rstrip() + "…"


__all__ = ["CAMPO_DA_HABILIDADE", "LIMIARES", "EscolhaObservada", "LeituraDePreferencias", "Observacao",
           "PerguntasAbertas", "Preferida", "ServicoDePreferencias", "Sugestao", "campo_de_terceiro",
           "chave_da_resposta", "modelo_do_comando", "modelo_do_conjunto", "versoes_conferidas"]
