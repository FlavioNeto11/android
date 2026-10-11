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
from datetime import datetime

from app.config import Config
from app.db import loads
from app.contracts import persona_de_teste
from app.contracts.origem import PREFIXO_LOTE, PREFIXO_OPERACAO, canal_da_pergunta, e_execucao_do_sistema
from app.events import EventBus
from app.models import Problem
from app.planning import costs
from app.modules.avisos.adapters.telegram import CanalTelegram
from app.modules.avisos.application.entrega import Canal, Resultado, entregar
from app.modules.avisos.domain.mensagem import Aviso, aviso_de_evento
from app.modules.avisos.domain.portal import (
    APAGADO_EM_ENVIO,
    APAGADO_FALHOU,
    APAGADO_OK,
    CAMPO_INVALIDO,
    CANAL_DESLIGADO,
    FALHA_INTERNA,
    TIPO_DO_CONTATO,
    TITULO_DO_CONTATO,
    ApagadoNoCanal,
    ContatoAvisado,
    ContatoDoPortal,
    aviso_da_borda,
    aviso_do_contato,
    aviso_do_resumo,
    chave_do_contato,
    da_para_apagar,
    motivo_de_recusa,
)
from app.modules.avisos.infrastructure.faxina_sql import Faxina, FaxinaDosCanais
from app.modules.avisos.infrastructure.fila_sql import FilaDeAvisos
from app.taskqueue.travas import AVISOS, Lideranca, TravaPerdida

log = logging.getLogger("poc.avisos")

#: Eventos que podem virar aviso. O filtro barato antes de montar a mensagem.
KINDS_QUE_AVISAM = frozenset({"approval.pending", "run.updated", "session.needs_person", "pedido.aviso",
                              "learning.needs_person", "pendencia.vence_em", "objective.updated",
                              # 28.50 (30.80 B): o que a pessoa ensinou e o sistema rebaixou.
                              "learning.ensinado_rebaixado", "learning.ensinado_sem_receita",
                              # 28.62: o fim da operação com N agentes (rotina, um aviso por operação).
                              "operacao.encerrada",
                              # 31.298: a exploração fora do catálogo começou / terminou (só ids, contagens e US$).
                              "exploracao.iniciada", "exploracao.concluida", "exploracao.efeito_liberado"})
#: De quanto em quanto tempo o laço varre incertos, vencidos e purga (a entrega roda a cada volta).
FAXINA_S = 3600.0
#: De quanto em quanto tempo, com o canal desligado, vencem os contatos do site pendentes (28.32).
PESSOAIS_S = 300.0


def trava_de_avisos_em_uso(cfg: Config) -> bool:
    """28.35: a trava `avisos` é de quem usa um canal: o aviso (Telegram) ou o Trello (espelho e leitor). É a mesma
    condição da renovação (`AppState._manter_travas`): quem toma sem renovar seguraria a trava até o prazo e deixaria o
    backend com o canal ligado sem líder (regra 28.4/28.11)."""
    return bool(cfg.file.avisos.enabled or cfg.file.trello.enabled)


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
        self._pessoais_em = 0.0
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
        if kind == "objective.updated":
            # 28.40: o filtro barato antes de ler o banco; o montador confere de novo.
            obj = (data or {}).get("objective")
            if not isinstance(obj, dict) or obj.get("status") != "waiting_user" or obj.get("blocked_kind") == "approval":
                return False
            if self._conta_ja_avisou(obj, data):
                return False
            data = self._com_nome_da_acao(self._com_a_etapa_que_espera(data, obj))
        if kind == "exploracao.concluida":
            data = self._com_o_custo_da_exploracao(data)
        aviso = aviso_de_evento(kind, data, evento_id, cfg.url_painel, frozenset(cfg.aprendizado_faixas), redigir,
                                nomes=nomes, conversa=conversa)
        if (aviso is None or self._e_de_prova(kind, data) or self._e_de_persona_de_teste(kind, data)
                or self._pergunta_de_outro_canal(aviso, data)):
            return False
        try:
            return self.fila.enfileirar(aviso)
        except Exception:  # noqa: BLE001 - o aviso nunca derruba o emissor nem o laço
            log.exception("avisos: não foi possível enfileirar %s", aviso.chave)
            return False

    def _com_o_custo_da_exploracao(self, data: dict[str, object] | None) -> dict[str, object] | None:
        """31.298: o custo (tokens x preço, `planning.costs.spent_usd`) e as chamadas de IA da execução que explorou. A
        falha da leitura só tira a linha do custo: o aviso sai dizendo que ele está indisponível."""
        run_id = (data or {}).get("run_id")
        if not isinstance(run_id, str):
            return data
        try:
            custo = costs.spent_usd(self.fila.db, self.cfg.file.ai.prices, run_id=run_id)
            chamadas = int(self.fila.db.scalar("SELECT COUNT(*) FROM ai_calls WHERE run_id=?", (run_id,)) or 0)
        except Exception:  # noqa: BLE001
            log.exception("avisos: custo da exploração %s não lido", run_id)
            return data
        return {**(data or {}), "custo_usd": custo, "chamadas": chamadas}

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

    def avisar_contato_do_portal(self, contato: ContatoDoPortal) -> ContatoAvisado:
        """28.32: a mensagem de um visitante do site ao Telegram do dono. A rota do Portal (29.77) chama depois de validar,
        aplicar a taxa e gravar o contato (o `contato_id` é a linha dela). Idempotente pela chave `portal:<id>`: chamar
        de novo devolve `enfileirado=True` sem segunda mensagem. Recusa (`campo_invalido`, `canal_desligado`) e falha
        (`falha_interna`) não gravam nada, e a rota chama de novo depois. O log leva só o id e o motivo, nunca o texto.
        Sem `texto_seguro` nem redator (ADR-075): o contato serve para o dono responder."""
        cid = contato.contato_id if isinstance(contato.contato_id, int) else "?"
        try:
            if motivo_de_recusa(contato) is not None:
                log.info("avisos: contato do portal %s recusado: %s", cid, CAMPO_INVALIDO)
                return ContatoAvisado(False, CAMPO_INVALIDO)
            aviso = aviso_do_contato(contato)
            if aviso is None:
                return ContatoAvisado(False, CAMPO_INVALIDO)
            if not self.ligado or self.canal() is None:
                return ContatoAvisado(False, CANAL_DESLIGADO)
            if not self.fila.enfileirar(aviso):          # False = já estava: a chave garante uma mensagem só
                # Só o id. A chave já existir é o reenvio da rota (normal) ou um id reusado (que cairia na lápide de um
                # excluído e sumiria calado, revisão do #340): o log deixa a premissa falhar com barulho.
                log.warning("avisos: contato do portal %s: a chave já existia na fila", cid)
            return ContatoAvisado(True)
        except Exception as exc:  # noqa: BLE001 - a rota guarda o contato e tenta de novo; o texto não vai ao log
            log.error("avisos: contato do portal %s não entrou na fila: %s", cid, type(exc).__name__)
            return ContatoAvisado(False, FALHA_INTERNA)

    def avisar_resumo_do_portal(self, retidos: int, descartados: int, janela_h: int) -> ContatoAvisado:
        """28.32, pedido do 29.77: os contatos acima dos tetos da rota (`retidos` acima de 20 por hora, `descartados`
        acima de 500 por dia) numa mensagem só de contagens. Chave por hora UTC: duas chamadas na mesma hora dão uma
        mensagem, e a segunda volta `enfileirado=True`. Os motivos são os do contato."""
        try:
            aviso = aviso_do_resumo(retidos, descartados, janela_h, self.fila.relogio())
            if aviso is None:
                log.info("avisos: resumo do portal recusado: %s", CAMPO_INVALIDO)
                return ContatoAvisado(False, CAMPO_INVALIDO)
            if not self.ligado or self.canal() is None:
                return ContatoAvisado(False, CANAL_DESLIGADO)
            self.fila.enfileirar(aviso)
            return ContatoAvisado(True)
        except Exception as exc:  # noqa: BLE001 - o laço do Portal tenta de novo na hora seguinte
            log.error("avisos: resumo do portal não entrou na fila: %s", type(exc).__name__)
            return ContatoAvisado(False, FALHA_INTERNA)

    def avisar_borda_do_portal(self, codigo: str, onde: str, agora: datetime, *, achado: str | None = None,
                               horas_sem_conferir: int | None = None) -> ContatoAvisado:
        """29.97: o vigia da borda do site (laço do Portal) achou um defeito, ou não consegue conferir há N voltas.
        Contrato combinado com o Portal em 05/10 04:25Z: ele chama só na TRANSIÇÃO; a chave é por código e dia UTC
        (`portal-borda:<código>:<AAAA-MM-DD>`), então chamar de novo no mesmo dia devolve `enfileirado=True` sem segunda
        mensagem. Código ou lugar fora do contrato, ou `sem_conferir` sem as horas: `campo_invalido`. O log leva só o
        código e o motivo."""
        try:
            aviso = aviso_da_borda(codigo, onde, agora, achado=achado, horas_sem_conferir=horas_sem_conferir)
            if aviso is None:
                log.info("avisos: borda do portal recusada (%s): %s", codigo if isinstance(codigo, str) else "?",
                         CAMPO_INVALIDO)
                return ContatoAvisado(False, CAMPO_INVALIDO)
            if not self.ligado or self.canal() is None:
                return ContatoAvisado(False, CANAL_DESLIGADO)
            self.fila.enfileirar(aviso)
            return ContatoAvisado(True)
        except Exception as exc:  # noqa: BLE001 - o laço do Portal tenta de novo na volta seguinte
            log.error("avisos: borda do portal não entrou na fila: %s", type(exc).__name__)
            return ContatoAvisado(False, FALHA_INTERNA)

    async def apagar_avisos_do_portal(self, contato_id: int, agora: datetime) -> ApagadoNoCanal:
        """28.34, a exclusão de um contato do site a pedido do titular (29.83; contrato em `docs/dominios/canais.md`
        C-27). Nesta ordem:

        1. a fila da chave `portal:<id>` (`FilaDeAvisos.descartar_do_contato`): `enviando` devolve `em_envio` sem mexer
           em nada mais; o resto fica `descartado` sem corpo, ou entra uma lápide, e nada sai depois;
        2. o texto das respostas do dono a essas mensagens sai do banco;
        3. as mensagens do chat (as do bot e as respostas do dono) com menos de 47 h são apagadas pelo `deleteMessage`;
           a mais velha, a de canal desligado, a que o Telegram recusa e a que pode ter saído sem registro vão para
           `a_mao`, com a hora.

        `ok` quando 1 e 2 terminaram, mesmo com `a_mao`; `falhou` com erro de banco em 1 ou 2 (o Portal mantém o
        contato). Erro de rede em 3 não é falha: vai para `a_mao`. O log leva só o id e as contagens."""
        chave = chave_do_contato(contato_id)
        try:
            # Os passos 1 e 2 numa transação (revisão do #340, E2): se um passo falha depois de o `incerto` virar
            # `descartado`, tudo volta, e a nova tentativa ainda vê a hora dele para `a_mao`.
            with self.fila.db.tx():
                estado, a_mao = self.fila.descartar_do_contato(chave, tipo=TIPO_DO_CONTATO, titulo=TITULO_DO_CONTATO)
                if estado not in ("enviado", "descartado"):
                    # `enviando`, ou qualquer estado que ainda pode sair (defesa do E1): nada de `ok`, tente de novo.
                    log.info("avisos: exclusão do contato do portal %s: a mensagem pode estar saindo agora", contato_id)
                    return ApagadoNoCanal(APAGADO_EM_ENVIO)
                respostas = self.fila.tirar_texto_das_respostas(chave)
                no_chat = self.fila.respostas_ao_fato(chave)
        except Exception as exc:  # noqa: BLE001 - o Portal mantém o contato e mostra o motivo
            log.error("avisos: exclusão do contato do portal %s falhou no banco: %s", contato_id, type(exc).__name__)
            return ApagadoNoCanal(APAGADO_FALHOU)
        try:
            canal = self.canal()
        except Exception as exc:  # noqa: BLE001 - a fila e as respostas já terminaram: o chat fica para o dono (N1)
            log.warning("avisos: exclusão do contato do portal %s: canal ilegível (%s)", contato_id, type(exc).__name__)
            canal = None
        apagar = getattr(canal, "apagar", None) if canal is not None else None
        apagadas = 0
        for ref, hora in no_chat:
            if apagar is None or not da_para_apagar(hora, agora) or not ref.isdigit():
                a_mao.append(hora)
                continue
            try:
                ok = bool(await apagar(int(ref)))
            except Exception as exc:  # noqa: BLE001 - a rede não desfaz a exclusão: o dono apaga à mão
                log.warning("avisos: exclusão do contato do portal %s: o chat não apagou uma mensagem (%s)", contato_id,
                            type(exc).__name__)
                ok = False
            if ok:
                apagadas += 1
            else:
                a_mao.append(hora)
        log.info("avisos: contato do portal %s excluído do canal: %d apagada(s), %d à mão, %d resposta(s) sem texto",
                 contato_id, apagadas, len(a_mao), respostas)
        return ApagadoNoCanal(APAGADO_OK, apagadas, tuple(a_mao))

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

    def _com_a_etapa_que_espera(self, data: dict[str, object] | None, obj: dict[str, object]) -> dict[str, object] | None:
        """28.40: põe em `acao` a capability da etapa em `waiting_user` do objetivo (a mesma leitura do lembrete do 31.50,
        `taskqueue/service.py`). Só a chave do catálogo; o título da etapa não sai. Sem etapa esperando (a política e o
        pré-voo param antes da etapa) ou com a leitura falhando, a mensagem sai sem a linha da etapa."""
        try:
            etapa = self.fila.db.one("SELECT capability FROM steps WHERE objective_id=? AND status='waiting_user' "
                                     "ORDER BY seq DESC, id DESC LIMIT 1", (obj.get("id"),))
        except Exception:  # noqa: BLE001 - o aviso sai sem a etapa
            log.exception("avisos: etapa do objetivo %s não lida", obj.get("id"))
            return data
        if etapa is None or not etapa["capability"]:
            return data
        return {**(data or {}), "acao": str(etapa["capability"])}

    def _conta_ja_avisou(self, obj: dict[str, object], data: dict[str, object] | None) -> bool:
        """28.40: o objetivo parou porque a CONTA pede a pessoa, e o `session.needs_person` já avisou: uma mensagem só.

        O evento da conta não leva a execução (`identity/application/session_rules.py`), só o aparelho, a persona e,
        quando quem emite sabe, a conta (`account_id`). A regra é uma APROXIMAÇÃO de "a mesma execução", aceita pela
        orquestradora (05/10): o último `session.needs_person` do mesmo aparelho está ativo e saiu depois de a execução
        do objetivo ser criada. O aviso de conta de uma execução ANTERIOR não cala o objetivo novo. Além disso (revisão
        do #372, O1, regra da orquestradora às 04:09Z), a conta tem de ser a do objetivo:

        - com `account_id` no aviso: é a conta do objetivo (`_conta_do_objetivo`). A outra persona no mesmo aparelho e
          o outro app da mesma persona (o Outlook junto do Instagram) avisam;
        - só com `profile_id`: é a mesma persona;
        - e, nos dois casos (28.41, N1), o objetivo parou por motivo de conta (`_parou_pela_conta`).

        Faltando a conta ou a persona de um dos lados, não cala: um aviso em dobro custa menos que uma parada muda.
        Limites conhecidos: se o objetivo parar ANTES de a conta mudar de estado, saem as duas mensagens; e outra
        execução no mesmo aparelho e na mesma conta, criada antes de a conta pedir a pessoa, fica calada. Leitura
        falhando: avisa."""
        aparelho, run_id, oid = obj.get("instance_id"), obj.get("run_id"), obj.get("id")
        if not isinstance(aparelho, str) or not isinstance(run_id, str) or not isinstance(oid, str):
            return False
        try:
            aviso = self.fila.db.one("SELECT ts, data FROM events WHERE kind='session.needs_person' AND instance_id=? "
                                     "ORDER BY id DESC LIMIT 1", (aparelho,))
            criada = self.fila.db.scalar("SELECT created_at FROM runs WHERE id=?", (run_id,))
            persona = self.fila.db.scalar("SELECT profile_id FROM objectives WHERE id=?", (oid,))
            if aviso is None or criada is None or not persona:
                return False
            dados = loads(aviso["data"], {})
            if not (isinstance(dados, dict) and dados.get("active") is True and str(aviso["ts"]) >= str(criada)):
                return False
            if dados.get("account_id"):
                mesma = self._conta_do_objetivo(oid, str(persona)) == dados["account_id"]
            else:
                mesma = dados.get("profile_id") == persona
            # 28.41 (N1 da leitura do #372): o motivo de conta vale nos DOIS ramos. Com a conta igual, o objetivo que
            # parou por falta de informação ou por um aviso do app ainda é outra notícia.
            return mesma and self._parou_pela_conta(oid, data)
        except Exception:  # noqa: BLE001 - ver a docstring: na dúvida, avisa
            log.exception("avisos: não foi possível conferir o aviso de conta do objetivo %s", oid)
            return False

    def _parou_pela_conta(self, oid: str, data: dict[str, object] | None) -> bool:
        """O objetivo parou por motivo de conta: `failure_kind` `autenticacao` ou `conta_errada`, ou o bloqueio da porta
        de sessão (`Scheduler._portas_do_app`), que para o objetivo ANTES de a etapa rodar (sem etapa em
        `waiting_user`)."""
        if (data or {}).get("failure_kind") in ("autenticacao", "conta_errada"):
            return True
        if (data or {}).get("failure_kind"):
            return False
        return self.fila.db.scalar("SELECT 1 FROM steps WHERE objective_id=? AND status='waiting_user' LIMIT 1",
                                   (oid,)) is None

    def _conta_do_objetivo(self, oid: str, persona: str) -> str | None:
        """A conta (`profile_accounts.id`) em que o objetivo parou: a da persona no app da etapa que espera ou, sem ela (a
        porta de sessão para antes da etapa), no app da próxima etapa a rodar. Só quando a etapa DECLARA o app e a persona
        tem UMA conta ativa nele; senão `None` (o app herdado do plano ou do aparelho não é adivinhado aqui)."""
        etapa = self.fila.db.one("SELECT app_id FROM steps WHERE objective_id=? AND status IN "
                                 "('waiting_user', 'pending', 'ready', 'retry_wait') "
                                 "ORDER BY CASE WHEN status='waiting_user' THEN 0 ELSE 1 END, seq, id LIMIT 1", (oid,))
        if etapa is None or not etapa["app_id"]:
            return None
        contas = self.fila.db.query("SELECT id FROM profile_accounts WHERE profile_id=? AND app_id=? AND status='active'",
                                    (persona, etapa["app_id"]))
        return str(contas[0]["id"]) if len(contas) == 1 else None

    def _pergunta_de_outro_canal(self, aviso: Aviso, data: dict[str, object] | None) -> bool:
        """31.279 (ADR-086): a pergunta da execução (`run.needs_input`) e o objetivo parado esperando pessoa
        (`objective.waiting_user`) vão só ao canal de ORIGEM do comando. Este é o Telegram: o aviso só sai se o comando veio
        de lá (`telegram:` na chave). Comando do painel pergunta no painel; do Trello, no Trello (o espelho). A execução do
        sistema também não passa (canal `None`), mesmo que `_e_de_prova` não a pegue. A que nasceu de um pedido persistente
        segue avisando, como antes: o canal dela é o do pedido. A aprovação NÃO é pergunta: segue o canal de aprovação.
        Sem execução achada, ou com falha na consulta, o aviso segue (o dono nunca perde um de pessoa)."""
        if aviso.tipo not in ("run.needs_input", "objective.waiting_user"):
            return False
        d = data or {}
        filho = d.get("run") if aviso.tipo == "run.needs_input" else d.get("objective")
        run_id = filho.get("id" if aviso.tipo == "run.needs_input" else "run_id") if isinstance(filho, dict) else None
        if not isinstance(run_id, str) or not run_id:
            return False
        try:
            linha = self.fila.db.one("SELECT prova_fluxo_id, idempotency_key, pedido_id FROM runs WHERE id=?", (run_id,))
        except Exception:  # noqa: BLE001 - na dúvida, avisa
            log.exception("avisos: não foi possível conferir o canal de origem da execução %s", run_id)
            return False
        if linha is None or linha["pedido_id"]:
            return False
        return canal_da_pergunta(linha["prova_fluxo_id"], linha["idempotency_key"]) != "telegram"

    def _e_de_persona_de_teste(self, kind: str, data: dict[str, object] | None) -> bool:
        """31.314: o fato é SÓ de persona de teste? Então o dono não é avisado (a persona de teste existe para provar o produto,
        e cada prova mandava aviso ao Telegram). Os perfis vêm de onde o evento os traz: `profile_id` do próprio dado
        (`session.needs_person`), do `objective`/`approval` embutido, ou dos objetivos da execução (`run.updated`). Só se TODOS
        os perfis achados são de teste: execução de uma persona de teste e de uma de verdade segue avisando. Sem perfil
        achado, ou com falha na consulta, o aviso segue (o dono nunca perde um de pessoa)."""
        if kind not in ("run.updated", "approval.pending", "objective.updated", "session.needs_person"):
            return False
        d = data or {}
        perfis: set[str] = set()
        for fonte in (d, d.get("objective"), d.get("approval")):
            if isinstance(fonte, dict) and isinstance(fonte.get("profile_id"), str) and fonte["profile_id"]:
                perfis.add(str(fonte["profile_id"]))
        run = d.get("run")
        run_id = run.get("id") if isinstance(run, dict) else None
        try:
            if isinstance(run_id, str) and run_id:
                perfis.update(str(r["profile_id"]) for r in self.fila.db.query(
                    "SELECT DISTINCT profile_id FROM objectives WHERE run_id=? AND profile_id IS NOT NULL", (run_id,)))
            if not perfis:
                return False
            marcas = ",".join("?" for _ in perfis)
            de_teste = {str(r["id"]) for r in self.fila.db.query(
                f"SELECT id FROM instagram_profiles WHERE id IN ({marcas}) AND {persona_de_teste.so_teste()}", tuple(perfis))}
        except Exception:  # noqa: BLE001 - na dúvida, avisa
            log.exception("avisos: não foi possível conferir se o fato é de persona de teste")
            return False
        return perfis <= de_teste

    def _e_de_prova(self, kind: str, data: dict[str, object] | None) -> bool:
        """30.37 e 28.19: a execução do SISTEMA não é de uma pessoa: a pergunta dela (`run.updated` em `needs_input`)
        não vira aviso ao dono. São a prova de fluxo (`runs.prova_fluxo_id`), a validação do QA e o lote de uma frente
        (pela chave de idempotência; a regra mora em `contracts/origem.e_execucao_do_sistema`). Em 04/10 um lote de
        medida sem marca mandou 11 avisos seguidos.

        A APROVAÇÃO é diferente: só o dono decide (orquestradora, 04/10 01:20Z). A aprovação que um LOTE abre segue
        avisando (agrupada, se vier em série, pela regra da rajada). A da prova e a da validação seguem caladas, como o
        30.37 decidiu.
        O objetivo parado (`objective.updated`, 28.40) segue a regra da pergunta: o do lote de uma frente é calado
        (orquestradora, 05/10). É o MESMO predicado, sem cópia: se ele mudar, as duas regras mudam juntas.
        Só consulto o banco para o evento que AVISARIA; falha na consulta deixa o aviso seguir (o dono recebe um aviso
        a mais, nunca perde um de pessoa)."""
        if kind not in ("run.updated", "approval.pending", "pendencia.vence_em", "objective.updated"):
            return False
        # 31.50: o lembrete do vencimento segue a regra do item que lembra (a aprovação de lote avisa; o resto do sistema não).
        lembrete = kind == "pendencia.vence_em"
        aprovacao = kind == "approval.pending" or (lembrete and (data or {}).get("o_que") == "aprovacao")
        filho = (data or {}) if lembrete else (data or {}).get(
            {"run.updated": "run", "objective.updated": "objective"}.get(kind, "approval"))
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
        if aprovacao and str(linha["idempotency_key"] or "").startswith((PREFIXO_LOTE, PREFIXO_OPERACAO)):
            return False                                   # aprovação de lote ou de operação (31.154): o dono decide, então avisa
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
        if canal is None:
            # 31.343: o UPDATE da faxina dos contatos sai da thread do laço (a leitura de 3 s do exame exaustivo o flagrou com o canal desligado).
            await asyncio.to_thread(self._vencer_pessoais)
            return None
        if time.monotonic() < self._esperar_ate:
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

    def _vencer_pessoais(self) -> None:
        """Canal desligado: a faxina da entrega não roda, mas o corpo do contato do site (28.32) vence do mesmo jeito. No
        máximo uma vez por `PESSOAIS_S`."""
        if time.monotonic() < self._pessoais_em:
            return
        self._pessoais_em = time.monotonic() + PESSOAIS_S
        try:
            vencidos = self.fila.vencer_pessoais(validade_h=self.cfg.file.avisos.validade_h)
        except Exception as exc:  # noqa: BLE001 - a faxina nunca derruba o laço; tenta de novo na próxima volta
            log.error("avisos: vencer os contatos pendentes com o canal desligado: %s", type(exc).__name__)
            return
        if vencidos:
            log.info("avisos: %d contato(s) do site vencido(s) com o canal desligado; corpo apagado", vencidos)

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
        """Uma volta da faxina dos canais (28.16), de hora em hora e só no líder da trava `avisos`. Devolve o que fez.

        Roda também com os dois canais desligados (o que já foi gravado sai no prazo), mas aí a trava não é renovada
        por ninguém deste backend: ela é SOLTA logo depois, para não segurar o líder de outro backend (28.35)."""
        if self._faxina_canais is None or time.monotonic() < self._faxina_canais_em:
            return []
        token = self._lider(AVISOS)
        if token is None:
            return []
        try:
            return self._faxinar_canais(token)
        finally:
            if not trava_de_avisos_em_uso(self.cfg):
                try:
                    self.lideranca.soltar(AVISOS)
                except Exception:  # noqa: BLE001 - registro à parte: não é erro da faxina, e a trava cai no TTL
                    log.exception("canais: soltar a trava avisos depois da faxina")

    def _faxinar_canais(self, token: int) -> list[Faxina]:
        assert self._faxina_canais is not None

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
        # `intervalo_s` sem evento: o que venceu com o processo parado sai agora, e nada mais apaga por uma hora. A
        # mesma regra do laço (28.35): com os canais desligados ela roda e SOLTA a trava `avisos` logo depois, e com a
        # trava de outro backend vivo não faz nada (o líder faz).
        self._faxinar_canais_contando()
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

