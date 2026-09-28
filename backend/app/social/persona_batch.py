"""Personas em LOTE (`POST /personas/generate/batch`, adendo v0.34): N rascunhos pelo mesmo pedido, em segundo plano.

Pedido do dono (28/09): gerar em lote a partir do "Nova persona a partir de um prompt". Cada item passa pelo MESMO
caminho de `SocialService.generate_persona_draft` (mesmas regras do rascunho, mesma recusa de texto com cara de
segredo, mesma proveniência); o que o lote acrescenta:

- **variedade de verdade**: cada item leva `variation` (o índice) e `avoid` (as pessoas que já existem e as já
  geradas neste lote) no pedido; e DEPOIS da geração o nome é conferido contra o que existe naquele instante — com
  dois itens em paralelo, um não vê o outro no pedido. Nome repetido vira `failed` com o motivo, sem nova chamada
  paga (a estimativa de custo mostrada antes de confirmar continua verdadeira);
- **concorrência 2**: o bastante para o lote andar, pouco para disputar as vagas do papel `social` com o resto;
- **teto de gasto**: recusa por orçamento (`ai_budget`) ou sem provedor (`ai_unavailable`) PARA o lote — nenhum
  item novo começa, o que estava em voo termina, e os pendentes viram `failed` com o motivo. Não se insiste;
- `create: true` grava cada rascunho válido pela mesma porta de `POST /personas` (quem chama passa `criar`, que
  também agenda a foto automática de `ai.image.on_create`); `create: false` deixa o rascunho no estado do lote para
  o painel criar os escolhidos.

**O estado vive na memória deste processo** e some num reinício: são rascunhos, e o que já foi criado está no banco.
Por isso o `GET` de um lote antigo pode dar 404, e o painel trata isso como "o lote se perdeu", não como erro. Os
últimos `LOTES_GUARDADOS` ficam; os mais velhos saem.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
from collections.abc import Callable, Iterator
from typing import Literal

from pydantic import BaseModel, Field

from ..events import EventBus
from ..models import PersonaCreate, PersonaDTO
from ..modules.identity.domain.persona_generation import PersonaEvitada, nome_repetido
from ..modules.identity.presentation.schemas import PersonaBatchBody, PersonaGenerateBody
from ..util import now, now_iso
from .service import SocialError, SocialService

log = logging.getLogger("poc.social.lote")

#: Evento a cada mudança de item (e um último com `done`): o painel relê o lote ao recebê-lo.
EVENTO_LOTE = "persona.batch.updated"
#: Quantos itens do mesmo lote geram ao mesmo tempo.
CONCORRENCIA = 2
#: Quantos lotes a memória guarda (os mais velhos saem primeiro).
LOTES_GUARDADOS = 20
#: Recusas que valem para o lote inteiro: insistir só gastaria (orçamento) ou falharia igual (sem provedor).
PARAM_O_LOTE = frozenset({"ai_budget", "ai_unavailable"})

StatusDoItem = Literal["pending", "generating", "ready", "created", "failed"]


class PersonaBatchItemDTO(BaseModel):
    index: int
    status: StatusDoItem = "pending"
    name: str | None = None
    persona_id: str | None = None
    #: O rascunho, só com `create: false` e status `ready`: o painel o manda em `POST /personas` se a pessoa quiser.
    draft: PersonaCreate | None = None
    error: str | None = None


class PersonaBatchDTO(BaseModel):
    batch_id: str
    prompt: str
    count: int
    create: bool
    items: list[PersonaBatchItemDTO] = Field(default_factory=list)
    done: bool = False
    created_at: str


class PersonaBatchAccepted(BaseModel):
    batch_id: str
    count: int


def novo_id_de_lote() -> str:
    return f"lote-{now().strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(3)}"


class LotesDePersona:
    """Os lotes deste processo: começa, guarda o estado, anuncia cada mudança."""

    def __init__(self, social: SocialService, bus: EventBus, *, criar: Callable[[PersonaCreate], PersonaDTO],
                 ao_agendar: Callable[[asyncio.Task[None]], None] | None = None,
                 novo_id: Callable[[], str] = novo_id_de_lote) -> None:
        self.social = social
        self.bus = bus
        self._criar = criar
        self._ao_agendar = ao_agendar
        self._novo_id = novo_id
        self._lotes: dict[str, PersonaBatchDTO] = {}
        self._tarefas: dict[str, asyncio.Task[None]] = {}
        self._parada: dict[str, str] = {}

    # ------------------------------------------------------------------ consulta
    def obter(self, batch_id: str) -> PersonaBatchDTO | None:
        return self._lotes.get(batch_id)

    async def aguardar(self, batch_id: str) -> PersonaBatchDTO | None:
        """Espera o lote terminar (testes e quem precisar do resultado inteiro sem ouvir eventos)."""
        tarefa = self._tarefas.get(batch_id)
        if tarefa is not None:
            await asyncio.shield(tarefa)
        return self._lotes.get(batch_id)

    # ------------------------------------------------------------------ início
    def iniciar(self, body: PersonaBatchBody) -> PersonaBatchDTO:
        """Aceita o lote e agenda a geração. Sem provedor de IA, recusa ANTES de aceitar: todos os itens falhariam."""
        if self.social.provider is None:
            raise SocialError("ai_unavailable", "Nenhum provedor de IA disponível para gerar personas.", 503)
        lote = PersonaBatchDTO(batch_id=self._novo_id(), prompt=body.prompt, count=body.count, create=body.create,
                               items=[PersonaBatchItemDTO(index=i) for i in range(body.count)], created_at=now_iso())
        self._lotes[lote.batch_id] = lote
        self._podar()
        tarefa = asyncio.create_task(self._rodar(lote, body), name=f"lote-de-personas-{lote.batch_id}")
        self._tarefas[lote.batch_id] = tarefa
        if self._ao_agendar is not None:
            self._ao_agendar(tarefa)
        self.bus.emit("log", f"Lote de {body.count} persona(s) iniciado"
                             + (" (cria direto)" if body.create else " (rascunhos para revisar)"),
                      data={"batch_id": lote.batch_id})
        return lote

    def _podar(self) -> None:
        while len(self._lotes) > LOTES_GUARDADOS:
            velho = next(iter(self._lotes))          # dict guarda a ordem de chegada
            self._lotes.pop(velho, None)
            self._parada.pop(velho, None)
            tarefa = self._tarefas.pop(velho, None)
            if tarefa is not None and not tarefa.done():
                tarefa.cancel()

    # ------------------------------------------------------------------ execução
    async def _rodar(self, lote: PersonaBatchDTO, body: PersonaBatchBody) -> None:
        indices: Iterator[int] = iter(range(lote.count))

        async def trabalhador() -> None:
            # Um iterador para os dois: `next` roda sem `await` no meio, então cada índice sai uma vez só.
            for i in indices:
                if lote.batch_id in self._parada:
                    return
                await self._um_item(lote, body, lote.items[i])

        try:
            await asyncio.gather(*(trabalhador() for _ in range(min(CONCORRENCIA, lote.count))))
        finally:
            motivo = self._parada.get(lote.batch_id)
            for item in lote.items:
                if item.status in ("pending", "generating"):
                    item.status = "failed"
                    item.error = f"O lote parou antes deste item: {motivo}" if motivo else "O lote foi interrompido."
                    self._anunciar(lote, item)
            lote.done = True
            self._anunciar(lote, None)

    async def _um_item(self, lote: PersonaBatchDTO, body: PersonaBatchBody, item: PersonaBatchItemDTO) -> None:
        item.status = "generating"
        self._anunciar(lote, item)
        pedido = PersonaGenerateBody(prompt=body.prompt, locale=body.locale, constraints=body.constraints)
        try:
            rascunho = await self.social.generate_persona_draft(pedido, avoid=self._evitar(lote, fora=item.index),
                                                                variation=item.index)
            # Conferência DEPOIS da geração, contra o que existe AGORA (inclusive o item irmão que terminou antes).
            repetida = nome_repetido(rascunho.name, self._evitar(lote, fora=item.index))
            if repetida is not None:
                raise SocialError("persona_draft_invalid", f"O modelo repetiu o nome {repetida.nome}, que já existe "
                                                           "ou já saiu neste lote. Gere de novo este item.", 422)
            item.name = rascunho.name
            if lote.create:
                pessoa = self._criar(rascunho)
                item.status, item.persona_id, item.name = "created", pessoa.id, pessoa.name
            else:
                item.status, item.draft = "ready", rascunho
        except SocialError as exc:
            item.status, item.error = "failed", exc.message
            if exc.code in PARAM_O_LOTE:
                self._parada.setdefault(lote.batch_id, exc.message)
        except Exception as exc:  # noqa: BLE001 - um item com defeito não derruba os outros do lote
            log.exception("item %d do lote %s falhou", item.index, lote.batch_id)
            item.status, item.error = "failed", f"Erro inesperado ao gerar este item: {type(exc).__name__}."
        self._anunciar(lote, item)

    def _evitar(self, lote: PersonaBatchDTO, *, fora: int) -> list[PersonaEvitada]:
        """Quem o item `fora` não pode repetir: as irmãs do lote que já têm nome (primeiro, para nunca caírem do
        corte de `MAX_EVITAR`) e as pessoas que já existem, as mais recentes antes."""
        irmas = [PersonaEvitada(nome=i.name, resumo=(i.draft.summary or "") if i.draft else "")
                 for i in lote.items if i.index != fora and i.name and i.status in ("ready", "created")]
        nomes_das_irmas = {p.nome for p in irmas}
        existentes = sorted(self.social.list_personas(), key=lambda p: p.created_at or "", reverse=True)
        return irmas + [PersonaEvitada(nome=p.name, resumo=p.summary or "") for p in existentes
                        if p.name and p.name not in nomes_das_irmas]

    def _anunciar(self, lote: PersonaBatchDTO, item: PersonaBatchItemDTO | None) -> None:
        prontos = sum(1 for i in lote.items if i.status in ("ready", "created", "failed"))
        if item is None:
            ok = sum(1 for i in lote.items if i.status in ("ready", "created"))
            mensagem = f"Lote de personas terminado: {ok} de {lote.count} ok"
        else:
            rotulo = {"pending": "na fila", "generating": "gerando", "ready": "rascunho pronto", "created": "criada",
                      "failed": "falhou"}[item.status]
            mensagem = f"Lote de personas: item {item.index + 1} de {lote.count} {rotulo}" \
                       + (f" ({item.name})" if item.name else "") + (f": {item.error}" if item.error else "")
        self.bus.emit(EVENTO_LOTE, mensagem, level="warn" if item is not None and item.status == "failed" else "info",
                      data={"batch_id": lote.batch_id, "index": item.index if item is not None else None,
                            "status": item.status if item is not None else None,
                            "name": item.name if item is not None else None,
                            "persona_id": item.persona_id if item is not None else None,
                            "error": item.error if item is not None else None,
                            "finished": prontos, "count": lote.count, "done": lote.done})
