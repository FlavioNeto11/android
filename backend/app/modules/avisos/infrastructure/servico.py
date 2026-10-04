"""O serviço do aviso fora do painel: ouve o barramento, enfileira e entrega (item 28.11).

Dois papéis no mesmo laço, com garantias diferentes:

- **Enfileirar** roda em todo backend que vê o evento (o `EventBus` entrega também o que as outras réplicas
  publicaram). É seguro porque a chave da linha é a do FATO e a fila é `UNIQUE (chave)`: o mesmo fato visto por dois
  backends é uma linha.
- **Entregar** só roda no líder da trava `avisos` (migração 066). O envio não é idempotente; a trava é o que impede
  dois backends de mandarem a mesma mensagem, e o token dela cerca a reivindicação da linha (ver `fila_sql`).

Desligado (`avisos.enabled: false`) ou sem segredo, o serviço não enfileira nem envia: um aviso que ficasse na fila
até o dono configurar sairia horas depois como notícia velha. Segredo faltando com o canal ligado é um Problem na
saúde (`avisos_sem_segredo`), não uma exceção.

Limite conhecido: o que acontece enquanto o backend está parado não é reenviado na partida (o laço começa no presente
do barramento). A caixa de Pendências do painel continua sendo a fonte da verdade; o aviso é o empurrão.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable

from app.config import Config
from app.contracts.origem import PREFIXO_LOTE, e_execucao_do_sistema
from app.events import EventBus
from app.models import Problem
from app.modules.avisos.adapters.telegram import CanalTelegram
from app.modules.avisos.application.entrega import Canal, Resultado, entregar
from app.modules.avisos.domain.mensagem import Aviso, aviso_de_evento
from app.modules.avisos.infrastructure.faxina_sql import Faxina, FaxinaDosCanais
from app.modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from app.taskqueue.travas import AVISOS, Lideranca, TravaPerdida

log = logging.getLogger("poc.avisos")

#: Eventos que podem virar aviso. O filtro barato antes de montar a mensagem.
KINDS_QUE_AVISAM = frozenset({"approval.pending", "run.updated", "session.needs_person", "pedido.aviso",
                              "learning.needs_person", "pendencia.vence_em"})
#: De quanto em quanto tempo o laço varre incertos, vencidos e purga (a entrega roda a cada volta).
FAXINA_S = 3600.0


class ServicoDeAvisos:
    def __init__(self, cfg: Config, bus: EventBus, fila: FilaDeAvisos, lideranca: Lideranca, *,
                 canal: Canal | None = None, lider: Callable[[str], int | None] | None = None,
                 redigir: Callable[[str], str] | None = None, faxina_canais: FaxinaDosCanais | None = None,
                 nomes_de_persona: Callable[[], Iterable[str]] | None = None):
        self.cfg = cfg
        self.bus = bus
        self.fila = fila
        self.lideranca = lideranca
        self._canal = canal
        #: O redator do conteúdo do aviso (28.15, decisão (d)): só vale com a conversa de volta ligada.
        self._redigir = redigir
        self._nomes_de_persona = nomes_de_persona
        #: 31.50: o nome em português da capability no catálogo (`LearningService.nome_da_capability`), ligado pelo
        #: `AppState` depois de montar o Aprendizado. Sem ele, o lembrete mostra a chave.
        self.nome_da_capability: Callable[[str], str | None] | None = None
        #: Quem diz se sou o líder: o `AppState._lider` (que tolera banco fora do ar). Injetável nos testes.
        self._lider = lider or self._tomar
        self._esperar_ate = 0.0
        self._faxina_em = 0.0
        #: A faxina das tabelas de canal (28.16). Roda no mesmo laço, mas NÃO depende do Telegram pronto: o Trello pode
        #: estar ligado sem ele, e o que já foi gravado precisa sair no prazo mesmo com o canal desligado depois.
        self._faxina_canais = faxina_canais
        self._faxina_canais_em = 0.0
        #: 29.78: voltas do laço pela faxina dos canais (feita, recusada ou fora do prazo). Quem precisa saber que a
        #: volta da subida já passou (o harness dos testes) espera este número sair do zero.
        self.voltas_da_faxina_dos_canais = 0

    # ------------------------------------------------------------------ configuração e saúde
    def _segredos(self) -> tuple[str, str]:
        env = self.cfg.env
        token = env.telegram_bot_token.get_secret_value() if env.telegram_bot_token else ""
        chat = env.telegram_chat_id.get_secret_value() if env.telegram_chat_id else ""
        return token.strip(), chat.strip()

    @property
    def ligado(self) -> bool:
        return bool(self.cfg.file.avisos.enabled)

    def canal(self) -> Canal | None:
        """O canal pronto para enviar, ou `None` (desligado ou segredo faltando). Criado sob demanda: o `.env` só é
        relido na partida, e um canal montado antes do token existir não pode ficar preso ao vazio."""
        if not self.ligado:
            return None
        if self._canal is not None:
            return self._canal
        token, chat = self._segredos()
        if not token or not chat:
            return None
        return CanalTelegram(token, chat, timeout_s=self.cfg.file.avisos.timeout_s)

    def problemas(self) -> list[Problem]:
        if not self.ligado or self._canal is not None:
            return []
        token, chat = self._segredos()
        faltam = [nome for nome, valor in (("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat)) if not valor]
        if not faltam:
            return []
        return [Problem(
            code="avisos_sem_segredo",
            message=f"Aviso fora do painel ligado (avisos.enabled), mas falta no .env: {', '.join(faltam)}.",
            hint="Siga o procedimento de docs/operacao.md (Aviso fora do painel) e reinicie a tarefa farm-central. "
                 "Enquanto faltar, nenhum aviso é enviado; as pendências continuam na caixa do painel.")]

    # ------------------------------------------------------------------ entrada
    def enfileirar_evento(self, kind: str, data: dict[str, object] | None, evento_id: int | None) -> bool:
        """Transforma um evento em aviso e o enfileira. Devolve se entrou linha nova."""
        if kind not in KINDS_QUE_AVISAM or not self.ligado or self.canal() is None:
            return False
        cfg = self.cfg.file.avisos
        # Com a conversa de volta ligada, a aprovação e a pergunta levam o conteúdo redigido (decisão (d) do ADR-071):
        # o dono responde ali mesmo. Desligada, a mensagem diz onde responder. O redator e os nomes de persona valem
        # sempre (28.31): são eles que deixam o rótulo do pedido sair. Sem os nomes, nada de texto da pessoa sai.
        redigir, conversa = self._redigir, cfg.entrada.enabled
        nomes = self._nomes()
        if nomes is None:
            redigir, conversa, nomes = None, False, []
        if kind == "pendencia.vence_em":
            if not (data or {}).get("chave"):
                # O montador devolve None sem a chave do produtor: o lembrete não sairia calado.
                log.warning("avisos: pendencia.vence_em sem a chave do produtor (evento %s): o lembrete não sai", evento_id)
            data = self._com_nome_da_acao(data)
        aviso = aviso_de_evento(kind, data, evento_id, cfg.url_painel, frozenset(cfg.aprendizado_faixas), redigir,
                                nomes=nomes, conversa=conversa)
        if aviso is None or self._e_de_prova(kind, data):
            return False
        try:
            return self.fila.enfileirar(aviso)
        except Exception:  # noqa: BLE001 - o aviso nunca derruba o emissor nem o laço
            log.exception("avisos: não foi possível enfileirar %s", aviso.chave)
            return False

    def _nomes(self) -> list[str] | None:
        """Os nomes de persona que o aviso não pode carregar, ou `None` se a leitura falhou (aí o aviso sai sem texto da
        pessoa: o rótulo vira a reserva e o conteúdo fica no painel)."""
        if self._nomes_de_persona is None:
            return []
        try:
            return list(self._nomes_de_persona())
        except Exception:  # noqa: BLE001 - o aviso sai mesmo assim, só que sem texto da pessoa
            log.exception("avisos: nomes de persona indisponíveis")
            return None

    def enfileirar_aviso(self, aviso: Aviso) -> bool:
        """Enfileira um aviso JÁ montado (o leitor do Trello avisa o dono do pedido de um convidado, 32.2). Mesma guarda
        do evento: sem o aviso fora do painel ligado e com o canal pronto, nada entra (a linha ficaria `pendente` para
        sempre). Idempotente pela chave."""
        if not self.ligado or self.canal() is None:
            return False
        try:
            return self.fila.enfileirar(aviso)
        except Exception:  # noqa: BLE001 - o aviso nunca derruba quem o pediu
            log.exception("avisos: não foi possível enfileirar %s", aviso.chave)
            return False

    def _com_nome_da_acao(self, data: dict[str, object] | None) -> dict[str, object] | None:
        """31.50: põe `acao_nome` (o nome do catálogo) ao lado da `acao` do lembrete. A falha da leitura só tira o nome."""
        acao = (data or {}).get("acao")
        if self.nome_da_capability is None or not isinstance(acao, str) or not acao:
            return data
        try:
            nome = self.nome_da_capability(acao)
        except Exception:  # noqa: BLE001 - o lembrete sai com a chave
            log.exception("avisos: nome da capability %s não lido", acao)
            return data
        return {**(data or {}), "acao_nome": nome} if nome else data

    def _e_de_prova(self, kind: str, data: dict[str, object] | None) -> bool:
        """30.37 e 28.19: a execução do SISTEMA não é de uma pessoa: a pergunta dela (`run.updated` em `needs_input`)
        não vira aviso ao dono. São a prova de fluxo (`runs.prova_fluxo_id`), a validação do QA e o lote de uma frente
        (pela chave de idempotência; a regra mora em `contracts/origem.e_execucao_do_sistema`). Em 04/10 um lote de
        medida sem marca mandou 11 avisos seguidos.

        A APROVAÇÃO é diferente: só o dono decide (orquestradora, 04/10 01:20Z). A aprovação que um LOTE abre segue
        avisando (agrupada, se vier em série, pela regra da rajada). A da prova e a da validação seguem caladas, como o
        30.37 decidiu.
        Só consulto o banco para o evento que AVISARIA; falha na consulta deixa o aviso seguir (o dono recebe um aviso
        a mais, nunca perde um de pessoa)."""
        if kind not in ("run.updated", "approval.pending", "pendencia.vence_em"):
            return False
        # 31.50: o lembrete do vencimento segue a regra do item que lembra (a aprovação de lote avisa; o resto do sistema não).
        lembrete = kind == "pendencia.vence_em"
        aprovacao = kind == "approval.pending" or (lembrete and (data or {}).get("o_que") == "aprovacao")
        filho = (data or {}) if lembrete else (data or {}).get("run" if kind == "run.updated" else "approval")
        run_id = filho.get("id" if kind == "run.updated" else "run_id") if isinstance(filho, dict) else None
        if isinstance(filho, dict) and filho.get("prova_fluxo_id"):
            return True
        if not isinstance(run_id, str) or not run_id:
            return False
        try:
            linha = self.fila.db.one("SELECT prova_fluxo_id, idempotency_key FROM runs WHERE id=?", (run_id,))
        except Exception:  # noqa: BLE001 - ver a docstring: na dúvida, avisa
            log.exception("avisos: não foi possível conferir se a execução %s é de prova", run_id)
            return False
        if linha is None:
            return False
        if aprovacao and str(linha["idempotency_key"] or "").startswith(PREFIXO_LOTE):
            return False                                   # aprovação de lote: o dono decide, então avisa
        return e_execucao_do_sistema(linha["prova_fluxo_id"], linha["idempotency_key"])

    # ------------------------------------------------------------------ saída (líder)
    def _tomar(self, nome: str) -> int | None:
        try:
            return self.lideranca.tomar(nome)
        except Exception:  # noqa: BLE001 - sem banco não há como saber quem é o líder: pular é o lado seguro
            log.exception("trava %s: não foi possível conferir o líder", nome)
            return None

    async def entregar_uma_vez(self) -> Resultado | None:
        """Uma volta de entrega, só no líder e com canal pronto. `None` quando pulou."""
        canal = self.canal()
        if canal is None or time.monotonic() < self._esperar_ate:
            return None
        token = self._lider(AVISOS)
        if token is None:
            return None
        cfg = self.cfg.file.avisos

        def cerca():  # noqa: ANN202 - context manager do mandato
            return self.lideranca.cercada(AVISOS, token)

        try:
            self._faxina(cerca)
            resultado = await entregar(self.fila, canal, cerca=cerca, agora=self.fila.relogio, limite=cfg.lote,
                                       max_tentativas=cfg.max_tentativas, backoff_s=cfg.backoff_s,
                                       agrupar_s=cfg.agrupar_s, agrupar_a_partir_de=cfg.agrupar_a_partir_de)
        except TravaPerdida as exc:
            log.warning("avisos: entrega recusada, %s", exc)
            return None
        except Exception:  # noqa: BLE001 - o canal fora do ar não derruba o processo
            log.exception("avisos: volta de entrega")
            return None
        if resultado.esperar_s is not None:
            self._esperar_ate = time.monotonic() + resultado.esperar_s
        if resultado.falharam:
            log.warning("avisos: %d aviso(s) falharam de vez nesta volta", resultado.falharam)
        return resultado

    def _faxina(self, cerca: Callable[[], object]) -> None:
        if time.monotonic() < self._faxina_em:
            return
        cfg = self.cfg.file.avisos
        incertos = self.fila.varrer_incertos(cerca=cerca, parado_ha_s=cfg.incerto_apos_s)      # type: ignore[arg-type]
        vencidos = self.fila.vencer(cerca=cerca, validade_h=cfg.validade_h)                    # type: ignore[arg-type]
        purgados = self.fila.purgar(cerca=cerca, retencao_dias=cfg.retencao_dias)              # type: ignore[arg-type]
        if incertos:
            log.warning("avisos: %d envio(s) interrompido(s) por queda ficaram como incertos (não reenviados)", incertos)
        if vencidos or purgados:
            log.info("avisos: %d vencido(s) e %d antigo(s) purgado(s)", vencidos, purgados)
        self._faxina_em = time.monotonic() + FAXINA_S

    def faxinar_canais(self) -> list[Faxina]:
        """Uma volta da faxina dos canais (28.16), de hora em hora e só no líder da trava `avisos`. Devolve o que fez."""
        if self._faxina_canais is None or time.monotonic() < self._faxina_canais_em:
            return []
        token = self._lider(AVISOS)
        if token is None:
            return []

        def cerca():  # noqa: ANN202 - context manager do mandato
            return self.lideranca.cercada(AVISOS, token)

        feitas: list[Faxina] = []
        try:
            for canal, dias in (("telegram", self.cfg.file.avisos.entrada.retencao_dias),
                                ("trello", self.cfg.file.trello.retencao_dias)):
                f = self._faxina_canais.faxinar(cerca=cerca, canal=canal, retencao_dias=dias)
                feitas.append(f)
                if f.algo:
                    log.info("canais: faxina do %s (%d zerada(s), %d apagada(s), %d enviada(s), %d cartão(ões),"
                             " %d evento(s) de contato, %d anexo(s))", canal, f.zeradas, f.apagadas, f.enviadas,
                             f.cartoes, f.eventos, f.anexos)
        except TravaPerdida as exc:
            log.warning("canais: faxina recusada, %s", exc)
            return feitas
        self._faxina_canais_em = time.monotonic() + FAXINA_S
        return feitas

    def _faxinar_canais_contando(self) -> None:
        try:
            self.faxinar_canais()
        except Exception:  # noqa: BLE001 - a faxina nunca derruba o laço de aviso
            log.exception("canais: faxina")
        finally:
            self.voltas_da_faxina_dos_canais += 1

    # ------------------------------------------------------------------ laço
    async def laco(self) -> None:
        """Escuta o barramento e entrega a cada `intervalo_s`. Assinatura que o barramento descartou (consumidor lento)
        é refeita lendo o que ficou para trás por `since`: o dedupe por chave torna a releitura inofensiva."""
        fila_de_eventos = self.bus.subscribe()
        ultimo = self.bus.last_id()
        # 29.78: a primeira faxina dos canais é já na subida, como a retenção e a expiração, e não depois do primeiro
        # `intervalo_s` sem evento: o que venceu com o processo parado sai agora, e nada mais apaga por uma hora. Só
        # com o aviso ligado: a faxina toma a trava `avisos`, que o backend com ele desligado não renova (28.4).
        if self.ligado:
            self._faxinar_canais_contando()
        else:
            self.voltas_da_faxina_dos_canais += 1
        while True:
            try:
                if not self.bus.is_subscribed(fila_de_eventos):
                    fila_de_eventos = self.bus.subscribe()
                    for rec in self.bus.since(ultimo):
                        self.enfileirar_evento(rec.kind, rec.data, rec.id)
                        ultimo = max(ultimo, rec.id or 0)
                intervalo = float(self.cfg.file.avisos.intervalo_s)
                try:
                    rec = await asyncio.wait_for(fila_de_eventos.get(), timeout=intervalo)
                except asyncio.TimeoutError:
                    rec = None
                while rec is not None:
                    self.enfileirar_evento(rec.kind, rec.data, rec.id)
                    ultimo = max(ultimo, rec.id or 0)
                    rec = fila_de_eventos.get_nowait() if not fila_de_eventos.empty() else None
                await self.entregar_uma_vez()
                self._faxinar_canais_contando()
            except asyncio.CancelledError:
                self.bus.unsubscribe(fila_de_eventos)
                raise
            except Exception:  # noqa: BLE001 - o laço de aviso nunca morre
                log.exception("avisos: laço")
                await asyncio.sleep(5)

