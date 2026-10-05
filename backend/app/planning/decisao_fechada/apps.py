"""Consumidor de SOMBRA dos apps do comando (R5, item 31.13, ADR-069 item 21): um `noul` por app cadastrado, FORA da cadeia.

O que ele faz, depois que o `_plan` de uma execução terminou (o mesmo gancho da intenção, `taskqueue/sombra_intencao.py`):
monta UM pedido `DecisaoFechada` (origem `apps`, classe C3, modo `shadow`) com o comando sanitizado pelo MESMO caminho da
intenção (`intencao.pedido_c3`), SEM o app da execução (é o rótulo: `pedido_dos_apps`), e uma pergunta `noul` por app
cadastrado: "cumprir este comando exige o app X?". Depois
casa, nas linhas gravadas, o que o caminho ATUAL leu do texto (`planning/apps_do_comando.apps_citados`, a regex de nomes):
`sim` para os citados e `nao` para os demais. Nada do que o Jev responde volta ao trabalho: a candidatura de apps do
serviço e o `Plan.required_apps` do planejador não são tocados.

- **Pergunta** (`id_da_pergunta`): id opaco por app (`app:` + sha1 do id do cadastro), nunca o nome. O nome vai na
  instrução e nos critérios (C2, o cadastro do dono): os MESMOS nomes que a regex casa (`nomes_do_app`: o do cadastro e o
  rótulo do manifesto), passados por `motivo_c7` e por `mascarar_catalogo(redact(...))` antes do corte, como a descrição
  da R2. Nome C7 ou que o filtro esvazia: o app fica fora do pedido (perguntar por "(sem nome)" não mede nada).
- **Resposta**: o `noul` devolve a probabilidade do "verdadeiro". A porta só aceita acima do limiar (0,5 só em sombra; era 0,85);
  abaixo é SEM resposta, e o complemento nunca vira `nao` (B7 do roteiro). Por isso a métrica da R5 é a precisão do `sim`,
  não a concordância (`docs/design/jev-golden-set.md`, pré-registro da R5).
- **Travado no código** (`privacidade.R5_LIBERADA`, falso até o GO do 31.10): `ativo()` é falso, o chamador não lê o
  cadastro nem a execução e nada é montado, qualquer que seja o YAML. Destravado, valem as mesmas condições da intenção:
  envio aprovado, origem `apps` em `shadow` e C3 entre as classes efetivas.

O módulo não importa o planejamento nem o cadastro (`planning/provider.py` puxa a camada de módulos): quem o liga converte
o cadastro em `AppDeclarado` e entrega o conjunto dos citados.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from ...security.redaction import redact
from . import privacidade
from .contrato import PedidoDeDecisao, Pergunta
from .entidades import mascarar_catalogo
from .intencao import motivo_c7, pedido_c3
from .porta import Porta, modo_efetivo
from .sombra import RepositorioDeSombra

log = logging.getLogger("poc.ai")

#: As duas decisões reais possíveis por app: o caminho atual citou o app no comando, ou não.
SIM: Final = "sim"
NAO: Final = "nao"
#: Teto de apps num pedido (cada app é uma pergunta do fan-out, e o custo cresce com elas). Acima, a R5 não vai: mandar só
#: parte mediria o que o Jev não viu, como o teto do catálogo da R2. O central tem 7 apps cadastrados (03/10).
MAX_APPS: Final = 16
#: 31.13/29.75 (orquestradora, 04/10): 0,5, SÓ em sombra (a R5 não tem `on`; um `on` pede decisão e limiar próprios). O
#: pré-registro era 0,85; a sintética deu 4 de 16 paráfrases pegas em 0,85 e 8 de 16 em 0,5, sem falso `sim`, e a maior
#: probabilidade de um `nao` foi 0,18. A linha da sombra grava a probabilidade, e o relatório do braço offline mede
#: nos dois limiares (colunas `_0_85` de `jev-braco-offline-intencao.py`).
LIMIAR: Final = 0.5
_NOME_MAX: Final = 200

_INSTRUCOES: Final = (
    "The state is a command written by the owner of a device fleet, in Portuguese, with names and numbers masked. Answer "
    "true if carrying out this command on the device requires the app {nome}, and false otherwise.")
_VERDADEIRO: Final = "Carrying out the command requires the app {nome}: opening it, acting in it or reading something in it."
_FALSO: Final = "The command can be carried out without the app {nome}."
#: As mesmas três frases em português (D-J7: o lote offline alterna EN e PT por caso; tradução literal, pré-registrada).
_INSTRUCOES_PT: Final = (
    "O estado é um comando escrito pelo dono de um parque de aparelhos, em português, com nomes e números mascarados. "
    "Responda verdadeiro se cumprir este comando no aparelho exige o app {nome}, e falso caso contrário.")
_VERDADEIRO_PT: Final = "Cumprir o comando exige o app {nome}: abri-lo, agir nele ou ler algo nele."
_FALSO_PT: Final = "O comando pode ser cumprido sem o app {nome}."


@dataclass(frozen=True)
class AppDeclarado:
    """Um app do cadastro (`apps`), com os nomes com que uma pessoa o escreve (C2)."""

    app_id: str
    nomes: tuple[str, ...]


def id_da_pergunta(app_id: str) -> str:
    """Id opaco e estável da pergunta do app: o nome nunca vai no id, e o id cabe no formato que a sombra aceita."""
    return "app:" + hashlib.sha1(app_id.encode("utf-8")).hexdigest()[:12]


def nome_enviavel(app: AppDeclarado) -> str | None:
    """O nome que sai na pergunta (C2), ou `None` quando nada pode sair. C7 no nome recusa; a máscara vem antes do corte,
    para o corte não deixar meia aspa nem meio handle; o `redact` é a rede para um segredo que tenha ido parar no cadastro."""
    texto = " / ".join(" ".join(n.split()) for n in app.nomes if n and n.strip())
    if not texto or motivo_c7(texto) is not None:
        return None
    return mascarar_catalogo(redact(texto) or "")[:_NOME_MAX].strip() or None


def pergunta_do_app(app: AppDeclarado, *, pt: bool = False) -> Pergunta | None:
    """A pergunta `noul` do app, em inglês (o runtime) ou em português (o lote offline, D-J7); `None` sem nome enviável."""
    nome = nome_enviavel(app)
    if nome is None:
        return None
    instrucoes, verdadeiro, falso = (_INSTRUCOES_PT, _VERDADEIRO_PT, _FALSO_PT) if pt else (_INSTRUCOES, _VERDADEIRO, _FALSO)
    return Pergunta(id_da_pergunta(app.app_id), "noul", instrucoes.format(nome=nome),
                    {"true": verdadeiro.format(nome=nome), "false": falso.format(nome=nome)}, LIMIAR)


def perguntas_dos_apps(apps: Sequence[AppDeclarado], *, pt: bool = False) -> tuple[Pergunta, ...]:
    """Uma pergunta por app com nome enviável, em ordem de id (estável entre execuções). Vazio acima de `MAX_APPS`."""
    unicos = {a.app_id: a for a in apps if a.app_id}
    if len(unicos) > MAX_APPS:
        return ()
    perguntas = (pergunta_do_app(unicos[i], pt=pt) for i in sorted(unicos))
    return tuple(p for p in perguntas if p is not None)


def decisoes_reais(apps: Sequence[AppDeclarado], citados: Iterable[str]) -> dict[str, str]:
    """{pergunta: `sim` | `nao`}: o que o caminho atual leu do comando, app por app."""
    citados = frozenset(citados)
    return {id_da_pergunta(a.app_id): SIM if a.app_id in citados else NAO for a in apps if a.app_id}


def pedido_dos_apps(*, run_id: str, perguntas: Sequence[Pergunta], comando: str, original: str | None = None,
                    destinos: Iterable[str] = ()) -> PedidoDeDecisao:
    """O pedido da R5: o estado C3 da intenção (`pedido_c3`, mesma C7 e mesmo filtro) SEM o `app`. Depois do `_plan` o app
    da execução é o PRINCIPAL do plano (`save_plan` → `runs.app_ids[0]`), que é o rótulo da R5: com ele no estado, a
    métrica mediria um eco (opção A da orquestradora, 03/10). O estado é subconjunto estrito do da intenção."""
    return pedido_c3("apps", run_id=run_id, perguntas=perguntas, comando=comando, app=None, original=original,
                     destinos=destinos)


class ConsumidorDeApps:
    def __init__(self, porta: Porta, repositorio: RepositorioDeSombra) -> None:
        self._porta = porta
        self._repositorio = repositorio
        self._avisou_teto = False

    def ativo(self) -> bool:
        """Falso, e então NADA é feito (nem a leitura do cadastro), quando: a R5 está travada no código
        (`privacidade.R5_LIBERADA`, lido na hora), o envio não está aprovado (`JEV_RUNTIME_SEND_APPROVED`), a porta não
        está em `shadow` para `apps` ou a C3 não está entre as classes efetivas (teto do código ∩ `classes_permitidas`)."""
        if not (privacidade.R5_LIBERADA and privacidade.JEV_RUNTIME_SEND_APPROVED):
            return False
        cfg = self._porta.cfg
        if modo_efetivo("shadow", cfg, "apps") != "shadow":
            return False
        classes = privacidade.JEV_ALLOWED_CLASSES
        if cfg is not None and cfg.classes_permitidas is not None:
            classes = classes & frozenset(cfg.classes_permitidas)
        return "C3" in classes

    def pedido(self, *, run_id: str, comando: str, apps: Sequence[AppDeclarado], original: str | None = None,
               destinos: Iterable[str] = ()) -> PedidoDeDecisao | None:
        """O pedido de sombra, ou `None` sem pergunta a fazer (nenhum app, ou acima do teto)."""
        if len({a.app_id for a in apps if a.app_id}) > MAX_APPS and not self._avisou_teto:
            self._avisou_teto = True             # uma vez por processo: a R5 some da medição enquanto o cadastro não cabe
            log.warning("decisao_fechada: %d apps cadastrados acima do teto (%d); a R5 não vai", len(apps), MAX_APPS)
        perguntas = perguntas_dos_apps(apps)
        if not perguntas:
            return None
        return pedido_dos_apps(run_id=run_id, perguntas=perguntas, comando=comando, original=original, destinos=destinos)

    def observar(self, *, run_id: str, comando: str, apps: Sequence[AppDeclarado], citados: Iterable[str],
                 original: str | None = None, destinos: Iterable[str] = ()) -> None:
        """Bloqueante (roda numa thread, nunca no laço): consulta a porta em shadow e casa a decisão real no `ao_registrar`,
        que a porta chama na mesma thread logo depois de gravar a linha. Qualquer falha é engolida: medir nunca derruba
        o trabalho."""
        try:
            if not self.ativo():
                return
            pedido = self.pedido(run_id=run_id, comando=comando, apps=apps, original=original, destinos=destinos)
            if pedido is None:
                return
            enviadas = {p.id for p in pedido.perguntas}
            reais = {k: v for k, v in decisoes_reais(apps, citados).items() if k in enviadas}

            def ao_registrar() -> None:
                if reais:
                    self._repositorio.casar_decisao_real(reais, ref=run_id)

            self._porta.consultar(pedido, ao_registrar=ao_registrar)
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: falha na sombra dos apps do comando")


__all__ = ["AppDeclarado", "ConsumidorDeApps", "LIMIAR", "MAX_APPS", "NAO", "SIM", "decisoes_reais", "id_da_pergunta",
           "nome_enviavel", "pedido_dos_apps", "pergunta_do_app", "perguntas_dos_apps"]
