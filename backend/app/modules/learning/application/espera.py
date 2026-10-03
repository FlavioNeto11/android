"""Quem decide PUBLICAR o evento `learning.needs_person` (30.21, §8.11): compara o item antes e depois de uma mudança
e avisa a ENTRADA na espera do dono e a SAÍDA dela. O serviço do Livro o chama depois de cada gravação que deu certo
(transição de item, transição de fonte nativa, nascimento, e a mudança que a própria loja de receitas ou de fluxos faz);
o barramento só é conhecido pela porta (`PortaDeEventos`).

Idempotente por (`kind:ref`, `aguardando`): duas coisas o garantem. (1) a comparação antes/depois — mover entre dois
estados que esperam (ou entre dois que não esperam) não muda a espera e não publica; (2) a memória do último aviso,
que barra a repetição se a mesma mudança chegar duas vezes (a loja e o livro). Depois de um reinício a memória está vazia:
a SAÍDA de uma espera anunciada antes dele ainda é avisada uma vez (consumidor idempotente), e a entrada só existe numa
mudança, nunca numa varredura.

Avisar NUNCA derruba o gesto que o provocou: `mudou_sem_falhar` transforma a falha em log; `mudou` a propaga para
quem está dentro da transação de uma loja (ver `LearningService.avisar_mudanca_nativa`).
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.modules.learning.application.ports import CatalogoDeRisco, PortaDeEventos
from app.modules.learning.domain.ciclo import SkillState
from app.modules.learning.domain.espera import (AvisoDeEspera, FatosDoCatalogo, Faixa,
                                                MotivoDeEntrada, classificar_espera, motivo_de_saida)
from app.modules.learning.domain.livro import EntradaDoLivro, para_aprovar
from app.modules.learning.domain.politica_de_risco import EtapaDeRisco
from app.modules.learning.domain.vocabulario import LivroKind
from app.util import to_iso

log = logging.getLogger("poc.aprendizado")


@dataclass(frozen=True, slots=True)
class _Ultimo:
    aguardando: bool
    desde: str


def aguarda_a_pessoa(e: EntradaDoLivro | None) -> bool:
    """O item está na fila do dono (a regra do "Para aprovar"). Habilidade fica de fora: o ciclo dela é outro e ela
    não passa pelo serviço do Livro."""
    return e is not None and e.kind is not LivroKind.HABILIDADE and para_aprovar(e)


class AvisadorDeEspera:
    def __init__(self, porta: PortaDeEventos | None, catalogo: CatalogoDeRisco | None,
                 relogio: Callable[[], datetime],
                 etapas_do_fluxo: Callable[[EntradaDoLivro], tuple[EtapaDeRisco, ...]] | None = None) -> None:
        """`etapas_do_fluxo`: as etapas do FLUXO com os fatos do catálogo de cada uma, o mesmo leitor do dossiê do
        curador (30.33). Sem ele, o fluxo é classificado sem as etapas: a lacuna de sempre (B)."""
        self._porta = porta
        self._catalogo = catalogo
        self._relogio = relogio
        self._etapas_do_fluxo = etapas_do_fluxo
        self._ultimo: dict[str, _Ultimo] = {}

    def mudou(self, antes: EntradaDoLivro | None, depois: EntradaDoLivro, *, por_sistema: bool,
              capability: str = "", sessao_ou_autenticacao: bool = False) -> None:
        """`antes=None`: o item acabou de nascer. `capability`/`sessao_ou_autenticacao`: o que só o item de
        `learning_items` sabe dizer (o escopo e a origem dele). PROPAGA a falha da porta: quem chama dentro da
        transação de uma loja precisa que ela chegue ao `savepoint` (PostgreSQL); `mudou_sem_falhar` é o resto."""
        if self._porta is None:
            return
        self._avisar(antes, depois, por_sistema=por_sistema, capability=capability, sessao=sessao_ou_autenticacao)

    def mudou_sem_falhar(self, antes: EntradaDoLivro | None, depois: EntradaDoLivro, *, por_sistema: bool,
                         capability: str = "", sessao_ou_autenticacao: bool = False) -> None:
        try:
            self.mudou(antes, depois, por_sistema=por_sistema, capability=capability,
                       sessao_ou_autenticacao=sessao_ou_autenticacao)
        except Exception:  # noqa: BLE001 - o aviso informa; a transição já foi gravada e não cai por causa dele
            log.exception("aprendizado: aviso de espera de %s %s", depois.kind.value, depois.ref)

    def _avisar(self, antes: EntradaDoLivro | None, depois: EntradaDoLivro, *, por_sistema: bool, capability: str,
                sessao: bool) -> None:
        esperava, espera = aguarda_a_pessoa(antes), aguarda_a_pessoa(depois)
        if esperava == espera or self._porta is None:
            return
        base = depois if espera else antes                      # a classificação é a do item enquanto ESPERAVA
        if base is None:
            return
        chave = f"{depois.kind.value}:{depois.ref}"
        ultimo = self._ultimo.get(chave)
        if ultimo is not None and ultimo.aguardando == espera:
            return
        classe = self._classificar(base, capability=capability, sessao=sessao)
        if classe is None:
            return
        faixa, motivo_de_entrada = classe
        agora = to_iso(self._relogio())
        if espera:
            desde, motivo = agora, motivo_de_entrada.value
        else:
            desde = ultimo.desde if ultimo is not None else (base.state_at or agora)
            motivo = motivo_de_saida(por_sistema=por_sistema, para_aposentado=depois.state is SkillState.DEPRECATED).value
        self._porta.esperando_a_pessoa(AvisoDeEspera(kind=depois.kind.value, ref=depois.ref, app=depois.app or "",
                                                     faixa=faixa, aguardando=espera, motivo=motivo, desde=desde))
        self._ultimo[chave] = _Ultimo(espera, desde)

    def parecer_disponivel(self, e: EntradaDoLivro, faixa: Faixa) -> bool:
        """Um parecer B ou C novo e válido do curador (30.11) ficou disponível para o dono: `motivo = parecer_da_ia`
        (§8.11, ponto 2). Só para item que ESTÁ esperando a pessoa; o item continua na espera, então a memória do último
        aviso não muda (a saída posterior é avisada normalmente). Cada parecer novo avisa uma vez: quem garante é a
        chave única (item, dossiê) de `learning_reviews`. Nunca levanta. Devolve se publicou."""
        if self._porta is None or faixa not in (Faixa.B, Faixa.C) or not aguarda_a_pessoa(e):
            return False
        ultimo = self._ultimo.get(f"{e.kind.value}:{e.ref}")
        agora = to_iso(self._relogio())
        desde = ultimo.desde if ultimo is not None and ultimo.aguardando else (e.state_at or agora)
        try:
            self._porta.esperando_a_pessoa(AvisoDeEspera(kind=e.kind.value, ref=e.ref, app=e.app or "", faixa=faixa,
                                                         aguardando=True, motivo=MotivoDeEntrada.PARECER_DA_IA.value,
                                                         desde=desde))
        except Exception:  # noqa: BLE001 - o aviso informa; o parecer já foi gravado
            log.exception("aprendizado: aviso de parecer de %s %s", e.kind.value, e.ref)
            return False
        return True

    def _classificar(self, e: EntradaDoLivro, *, capability: str, sessao: bool) -> tuple[Faixa, MotivoDeEntrada] | None:
        app = e.app or ""
        tem = bool(app) and self._catalogo is not None and self._catalogo.tem_catalogo(app)
        fatos: FatosDoCatalogo | None = None
        if tem and self._catalogo is not None and capability and capability != "*":
            fatos = self._catalogo.da_capability(app, capability)
        # 30.33: o fluxo pela etapa mais restritiva, como o dossiê (30.32): a faixa do aviso é a classe do parecer.
        etapas = (self._etapas_do_fluxo(e) if e.kind is LivroKind.FLUXO and self._etapas_do_fluxo is not None
                  else ())
        return classificar_espera(side_effect=e.side_effect, human_origin=e.human_origin, tem_catalogo=tem,
                                  catalogo=fatos, sessao_ou_autenticacao=sessao,
                                  reaprendido=e.reaprendido is not None, etapas=etapas)


__all__ = ["AvisadorDeEspera", "aguarda_a_pessoa"]
