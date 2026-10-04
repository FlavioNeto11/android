"""30.65: exceção de uso único à regra de uma conta por alvo (ADR-055), criada por pessoa, com prazo.

A porta de frota recusa, sem caminho de aprovação, o efeito sobre um alvo que outra conta da frota já tocou na janela.
O dono pode autorizar UMA exceção pontual (a DM de prova do 31.26 entre duas contas nossas): um perfil, um alvo, uma
ação, por no máximo 72 h. O alvo é sempre conta NOSSA viva: abrir a exceção para pessoa real é decisão do dono, não da
rota. A exceção não libera sozinha. A etapa casada vira `approval_required` (Pendências mostra o
porquê) e o resto vale de novo: conta retirada, espaçamento, tetos, DM fria, repetição do 30.64.

Ciclo: criada pela rota → presa à etapa que a porta casou (`prender`, no `_policy_gate`, que é quem escreve; o `check`
só lê) → RESERVADA pelo executor logo antes do gesto (`reservar`: UPDATE condicional, `em_uso`) → ligada à interação
no `open_effect` (`disparou`) → liquidada no `settle_effect` (`liquidar`): usada se o efeito saiu ou pode ter saído,
encerrada `sem_efeito` se não saiu. Em uso nunca volta a aberta, nem vence, nem é revogada (uso único fecha fechado; a
queda do processo a deixa `em_uso`, visível no GET). Antes da reserva, termina de três jeitos: vencida no prazo
(`vencer`, que roda na porta do despacho e na leitura da rota; não há varredura em segundo plano), recusada (o dono
rejeitou o cartão da etapa presa) ou revogada pela rota. Em qualquer desses, a reserva falha e o gesto não acontece. Cada passo vira evento na trilha (`politica.excecao_*`). Não
entra no registro de decisões automáticas (28.25): é decisão de pessoa.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from ..db import Database
from ..modules.identity.application.ports import ProfileStore
from ..planning.capabilities import normalizar_alvo
from ..util import new_token, now, parse_iso, to_iso
from .contas_nossas import eh_conta_nossa, foi_retirada

#: A única regra que uma exceção afrouxa.
UMA_CONTA_POR_ALVO = "uma_conta_por_alvo"
#: Prazo máximo, decidido com a orquestradora (04/10): exceção é pontual, não configuração.
VALIDADE_MAXIMA = timedelta(hours=72)
#: A etapa presa que terminou nestes estados sem efeito libera a exceção para a versão revisada dela.
_TERMINOU_SEM_EFEITO = ("failed", "cancelled", "skipped")
#: Em aberto: nem reservada, nem usada, nem vencida, nem encerrada (recusa do cartão, revogação, uso sem efeito).
_EM_ABERTO = "em_uso_em IS NULL AND usada_em IS NULL AND vencida_em IS NULL AND encerrada_em IS NULL"
#: Reservada pelo executor e ainda não liquidada.
_EM_USO = "em_uso_em IS NOT NULL AND usada_em IS NULL AND encerrada_em IS NULL"

Emitir = Callable[[str, str, dict[str, object]], object]


class ExcecaoInvalida(ValueError):
    """O pedido de exceção não se sustenta (prazo, alvo, perfil). A mensagem vai para quem chamou a rota."""


class ExcecaoEmUso(ExcecaoInvalida):
    """Revogar perdeu para a reserva do executor: o gesto já pode ter acontecido."""


@dataclass(frozen=True, slots=True)
class Excecao:
    id: str
    regra: str
    profile_id: str
    alvo: str
    capability: str
    motivo: str
    autorizacao: str
    autor: str
    #: O autor tinha sessão de operador no painel. Só assim o cartão diz "autorizada pelo dono"; sem sessão (o
    #: loopback aceita) o cartão diz quem criou e cita a autorização como texto, sem atestá-la.
    autor_com_sessao: bool
    criada_em: str
    expira_em: str
    step_id: str | None
    presa_em: str | None
    usada_em: str | None
    interaction_id: str | None
    vencida_em: str | None
    #: Quando o executor a reservou, logo antes do gesto (uso único: não volta a aberta).
    em_uso_em: str | None
    #: A etapa e a execução que a reservaram: gravadas na reserva e nunca limpas (a trilha da exceção). O `step_id` é
    #: a ligação viva com a porta e pode ser solto depois.
    etapa_do_uso: str | None
    run_do_uso: str | None
    encerrada_em: str | None
    encerrada_por: str | None
    #: `recusada` (o dono rejeitou o cartão da etapa presa), `revogada` (pela rota), `sem_efeito` (reservada, e o
    #: gesto terminou sem efeito: fecha fechado; outra exceção, se for o caso, é criada de novo) ou `incerta`
    #: (reservada, e a etapa terminou sem liquidação: o efeito pode ter saído; conta como usada).
    encerramento: str | None

    @property
    def estado(self) -> str:
        if self.usada_em:
            return "usada"
        if self.vencida_em:
            return "vencida"
        if self.encerrada_em:
            return self.encerramento or "revogada"
        if self.em_uso_em:
            return "em_uso"
        return "presa" if self.step_id else "ativa"

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "regra": self.regra, "profile_id": self.profile_id, "alvo": self.alvo,
                "capability": self.capability, "motivo": self.motivo, "autorizacao": self.autorizacao,
                "autor": self.autor, "autor_com_sessao": self.autor_com_sessao, "criada_em": self.criada_em,
                "expira_em": self.expira_em, "step_id": self.step_id, "presa_em": self.presa_em,
                "usada_em": self.usada_em, "interaction_id": self.interaction_id, "vencida_em": self.vencida_em,
                "em_uso_em": self.em_uso_em, "etapa_do_uso": self.etapa_do_uso, "run_do_uso": self.run_do_uso,
                "encerrada_em": self.encerrada_em, "encerrada_por": self.encerrada_por,
                "encerramento": self.encerramento, "estado": self.estado}


def _excecao(row: object) -> Excecao:
    campos = {campo: row[campo] for campo in Excecao.__slots__}  # type: ignore[index]
    campos["autor_com_sessao"] = bool(campos["autor_com_sessao"])
    return Excecao(**campos)


#: A etapa que terminou assim com a exceção ainda `em_uso` (sem liquidação: queda, `open_effect` que não rodou) a
#: encerra: `succeeded` como usada; o resto como `incerta` (o efeito pode ter saído). Nunca volta a aberta.
_ETAPA_TERMINADA = ("succeeded", "failed", "cancelled", "skipped", "uncertain")


class ExcecoesDePolitica:
    def __init__(self, db: Database, emitir: Emitir | None = None, perfis: ProfileStore | None = None):
        self.db = db
        self.emitir = emitir
        # Quem diz se o perfil existe é o registro de perfis (o mesmo porto das regras de sessão), não uma tabela de
        # app: o núcleo não decide pelo nome do Instagram (ADR-052). Sem ele, `criar` recusa (só a leitura funciona).
        self.perfis = perfis

    def _emitir(self, tipo: str, excecao_id: str, texto: str, **extra: object) -> None:
        """Evento enxuto: só ids e estado. Alvo, motivo e autorização ficam no GET; menos dado no barramento é menos
        lugar para vazar, e a regra dos avisos não depende do redator."""
        if self.emitir is None:
            return
        x = self.obter(excecao_id)
        # A etapa e a execução do USO quando houve reserva (nunca limpas); antes disso, a ligação viva com a porta.
        etapa = (x.etapa_do_uso or x.step_id) if x else None
        run = (x.run_do_uso if x and x.run_do_uso
               else self.db.scalar("SELECT run_id FROM steps WHERE id=?", (etapa,)) if etapa else None)
        self.emitir(tipo, f"exceção {excecao_id} à regra de uma conta por alvo {texto} (30.65)",
                    {"excecao_id": excecao_id, "estado": x.estado if x else None,
                     "encerrada_por": x.encerrada_por if x else None, "step_id": etapa, "run_id": run, **extra})

    # ------------------------------------------------------------------ rota
    def criar(self, *, profile_id: str, alvo: str, capability: str, motivo: str, autorizacao: str, autor: str,
              expira_em: str, autor_com_sessao: bool = False) -> Excecao:
        alvo_normal = normalizar_alvo(alvo)
        if alvo_normal is None:
            raise ExcecaoInvalida("informe o alvo (o @ da conta que recebe a ação)")
        if not eh_conta_nossa(self.db, alvo_normal) or foi_retirada(self.db, alvo_normal):
            # O dono autorizou UMA DM entre duas contas nossas. Abrir a exceção para pessoa real é decisão dele.
            raise ExcecaoInvalida(f"{alvo_normal} não é uma conta nossa viva: a exceção só vale entre contas nossas")
        acao = (capability or "").strip().upper()
        if not acao:
            raise ExcecaoInvalida("informe a ação (por exemplo SEND_MESSAGE)")
        if not (motivo or "").strip() or not (autorizacao or "").strip():
            raise ExcecaoInvalida("informe o motivo e a autorização (quem autorizou, por onde e quando)")
        if self.perfis is None or self.perfis.profile_row(profile_id) is None:
            raise ExcecaoInvalida(f"perfil {profile_id} não existe")
        agora = now()
        try:
            prazo = parse_iso(expira_em)
        except (TypeError, ValueError) as exc:
            raise ExcecaoInvalida("expira_em precisa ser uma data ISO em UTC") from exc
        if prazo <= agora:
            raise ExcecaoInvalida("expira_em já passou")
        if prazo - agora > VALIDADE_MAXIMA:
            raise ExcecaoInvalida("a exceção vale no máximo 72 h")
        aberta = self.db.scalar(
            f"SELECT id FROM excecoes_de_politica WHERE profile_id=? AND alvo=? AND capability=? AND {_EM_ABERTO}"
            " AND expira_em>? ORDER BY criada_em, id LIMIT 1", (profile_id, alvo_normal, acao, to_iso(agora)))
        if aberta is not None:
            raise ExcecaoInvalida(f"já existe a exceção {aberta} em aberto para este perfil, alvo e ação; revogue-a "
                                  "antes de criar outra")
        excecao_id = f"exc-{new_token()}"
        self.db.execute(
            "INSERT INTO excecoes_de_politica(id, regra, profile_id, alvo, capability, motivo, autorizacao, autor,"
            " autor_com_sessao, criada_em, expira_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (excecao_id, UMA_CONTA_POR_ALVO, profile_id, alvo_normal, acao, motivo.strip(), autorizacao.strip(),
             autor, 1 if autor_com_sessao else 0, to_iso(agora), to_iso(prazo)))
        criada = self.obter(excecao_id)
        assert criada is not None
        self._emitir("politica.excecao_criada", excecao_id, "criada")
        return criada

    def obter(self, excecao_id: str) -> Excecao | None:
        row = self.db.one("SELECT * FROM excecoes_de_politica WHERE id=?", (excecao_id,))
        return _excecao(row) if row else None

    def listar(self, *, profile_id: str | None = None) -> list[Excecao]:
        self.vencer()
        filtro, args = (" WHERE profile_id=?", (profile_id,)) if profile_id else ("", ())
        return [_excecao(r) for r in self.db.query(
            f"SELECT * FROM excecoes_de_politica{filtro} ORDER BY criada_em DESC, id DESC LIMIT 200", args)]

    # ------------------------------------------------------------------ porta (só leitura)
    def ativa_para(self, profile_id: str, alvo: str | None, capability: str, step_id: str | None) -> Excecao | None:
        """A exceção que vale para esta etapa, ou `None`. Só lê: o `check` é puro (30.61 o reusa na prévia).
        Vale a EM ABERTO (nem reservada, nem usada, nem vencida, nem encerrada), dentro do prazo, deste perfil, alvo e
        ação, livre ou presa a ESTA etapa, ou presa a uma etapa que terminou sem efeito (a versão revisada da mesma
        etapa a retoma). A reservada (`em_uso`) nunca: o gesto dela pode ter saído sem interação gravada."""
        alvo_normal = normalizar_alvo(alvo)
        if alvo_normal is None:
            return None
        estados = ",".join("?" * len(_TERMINOU_SEM_EFEITO))
        row = self.db.one(
            "SELECT x.* FROM excecoes_de_politica x WHERE x.profile_id=? AND x.alvo=? AND x.capability=?"
            f" AND {_EM_ABERTO} AND x.expira_em>?"
            " AND (x.step_id IS NULL OR x.step_id=? OR EXISTS (SELECT 1 FROM steps e WHERE e.id=x.step_id"
            f" AND e.status IN ({estados}) AND NOT EXISTS (SELECT 1 FROM social_interactions i"
            " WHERE i.step_id=e.id AND i.direction='outbound')))"
            " ORDER BY x.criada_em, x.id LIMIT 1",
            (profile_id, alvo_normal, capability, to_iso(now()), step_id or "", *_TERMINOU_SEM_EFEITO))
        return _excecao(row) if row else None

    # ------------------------------------------------------------------ escrita (gate, efeito, varredura)
    def prender(self, excecao_id: str, step_id: str) -> bool:
        """A porta casou a exceção com esta etapa: outra etapa não a toma enquanto esta estiver viva. `False` quando ela
        deixou de estar em aberto entre a leitura e esta escrita: a porta recusa (não há exceção para a etapa).

        Ela passa a ser a ÚNICA ligação desta etapa: as outras exceções presas a ela, de passagens anteriores pela
        porta, são soltas (salvo a reservada, que nunca se solta). É o que diz ao executor qual exceção a porta usou."""
        agora = to_iso(now())
        cur = self.db.execute(f"UPDATE excecoes_de_politica SET step_id=?, presa_em=CASE WHEN step_id=? THEN presa_em"
                              f" ELSE ? END WHERE id=? AND {_EM_ABERTO}", (step_id, step_id, agora, excecao_id))
        if int(cur.rowcount or 0) != 1:
            return False
        self.db.execute(f"UPDATE excecoes_de_politica SET step_id=NULL WHERE step_id=? AND id<>? AND NOT ({_EM_USO})",
                        (step_id, excecao_id))
        return True

    def soltar_da_etapa(self, step_id: str) -> None:
        """A porta passou esta etapa SEM exceção: nenhuma fica ligada a ela (salvo a reservada). Assim o executor não
        reserva no commit uma exceção que a porta não usou, nem falha por uma vencida antiga presa."""
        self.db.execute(f"UPDATE excecoes_de_politica SET step_id=NULL WHERE step_id=? AND NOT ({_EM_USO})", (step_id,))

    def reservar(self, step_id: str | None) -> str | None:
        """O executor, logo antes do gesto com efeito: `None` segue (a porta passou a etapa sem exceção, ou a reserva
        ganhou); um texto é o motivo literal para a etapa falhar fechada, sem gesto.

        A exceção ligada à etapa é a que a porta usou na última passagem (`prender` deixa só ela; `soltar_da_etapa`
        nenhuma). A reserva é um UPDATE condicional dela, não uma leitura seguida de ação: com duas reservas (ou uma
        revogação) ao mesmo tempo, só uma linha é afetada. Se ela venceu, foi revogada, recusada, já usada, ou se há uma
        reservada sem liquidação ligada à etapa, o efeito não sai. O motivo não cita pessoa nem alvo (vai ao desfecho)."""
        if not step_id:
            return None
        ligadas = [_excecao(r) for r in self.db.query(
            "SELECT * FROM excecoes_de_politica WHERE step_id=? ORDER BY presa_em DESC, criada_em DESC, id DESC",
            (step_id,))]
        if not ligadas:
            return None
        em_uso = [x for x in ligadas if x.estado == "em_uso"]
        x = em_uso[0] if em_uso else ligadas[0]
        if not em_uso and x.estado in ("ativa", "presa"):
            agora = to_iso(now())
            cur = self.db.execute(f"UPDATE excecoes_de_politica SET em_uso_em=?, etapa_do_uso=?, run_do_uso=(SELECT"
                                  f" run_id FROM steps WHERE id=?) WHERE id=? AND step_id=? AND {_EM_ABERTO}"
                                  " AND expira_em>?", (agora, step_id, step_id, x.id, step_id, agora))
            if int(cur.rowcount or 0) == 1:
                return None
            x = self.obter(x.id) or x
        como = {"revogada": "foi revogada", "recusada": "foi recusada",
                "sem_efeito": "já foi usada numa tentativa sem efeito", "incerta": "já foi usada (efeito incerto)",
                "usada": "já foi usada", "em_uso": "já está em uso por outro disparo"}.get(x.estado, "venceu")
        return (f"a exceção {x.id} à regra de uma conta por alvo {como} antes do efeito; o efeito não foi disparado "
                "(30.65)")

    def disparou(self, step_id: str | None, interaction_id: str) -> None:
        """O `open_effect` da etapa: a exceção reservada passa a apontar a interação, que o `liquidar` fecha."""
        if not step_id:
            return
        self.db.execute(f"UPDATE excecoes_de_politica SET interaction_id=? WHERE step_id=? AND {_EM_USO}"
                        " AND interaction_id IS NULL", (interaction_id, step_id))

    def liquidar(self, interaction_id: str, *, houve_efeito: bool) -> None:
        """A interação fechou (`settle_effect`). Efeito confirmado ou incerto: usada. Sem efeito: encerrada `sem_efeito`
        (uso único fecha fechado; não volta a aberta)."""
        row = self.db.one(f"SELECT * FROM excecoes_de_politica WHERE interaction_id=? AND {_EM_USO}"
                          " ORDER BY em_uso_em, id LIMIT 1", (interaction_id,))
        if row is None:
            return
        agora = to_iso(now())
        if houve_efeito:
            self.db.execute(f"UPDATE excecoes_de_politica SET usada_em=? WHERE id=? AND {_EM_USO}", (agora, row["id"]))
            self._emitir("politica.excecao_usada", str(row["id"]), "usada", interaction_id=interaction_id)
        else:
            self.db.execute(f"UPDATE excecoes_de_politica SET encerrada_em=?, encerrada_por='sistema',"
                            f" encerramento='sem_efeito' WHERE id=? AND {_EM_USO}", (agora, row["id"]))
            self._emitir("politica.excecao_sem_efeito", str(row["id"]), "encerrada sem efeito confirmado",
                         interaction_id=interaction_id)

    def fechar_em_uso_orfas(self) -> int:
        """A reservada cuja etapa terminou sem liquidação (queda do processo, `open_effect` que não rodou ou levantou)
        não fica `em_uso` para sempre: etapa `succeeded`, usada; senão `incerta` (o efeito pode ter saído). Efeito
        incerto conta como usado: nunca volta a aberta."""
        estados = ",".join("?" * len(_ETAPA_TERMINADA))
        orfas = self.db.query(
            f"SELECT x.id, e.status FROM excecoes_de_politica x JOIN steps e ON e.id = x.etapa_do_uso WHERE {_EM_USO}"
            f" AND e.status IN ({estados}) ORDER BY x.em_uso_em, x.id", _ETAPA_TERMINADA)
        agora = to_iso(now())
        for row in orfas:
            if row["status"] == "succeeded":
                self.db.execute(f"UPDATE excecoes_de_politica SET usada_em=? WHERE id=? AND {_EM_USO}", (agora, row["id"]))
                self._emitir("politica.excecao_usada", str(row["id"]), "usada (etapa concluída sem liquidação)")
            else:
                self.db.execute(f"UPDATE excecoes_de_politica SET encerrada_em=?, encerrada_por='sistema',"
                                f" encerramento='incerta' WHERE id=? AND {_EM_USO}", (agora, row["id"]))
                self._emitir("politica.excecao_incerta", str(row["id"]), "encerrada como incerta: o efeito pode ter saído")
        return len(orfas)

    def vencer(self) -> int:
        """Encerra as em aberto cujo prazo passou, com um evento cada, e fecha as reservadas órfãs. Idempotente. A em
        uso fica fora do alcance do prazo.

        A vencida continua ligada à etapa em que estava presa: é assim que a reserva no commit a encontra e falha
        ("venceu antes do efeito"); a porta não a usa de novo porque ela não está em aberto, e a solta se passar a
        etapa sem exceção.

        Roda na porta do despacho e na leitura da rota; não há varredura em segundo plano."""
        agora = to_iso(now())
        vencidas = self.db.query(f"SELECT * FROM excecoes_de_politica WHERE {_EM_ABERTO} AND expira_em<=?"
                                 " ORDER BY criada_em, id", (agora,))
        for row in vencidas:
            self.db.execute(f"UPDATE excecoes_de_politica SET vencida_em=? WHERE id=? AND {_EM_ABERTO}",
                            (agora, row["id"]))
            self._emitir("politica.excecao_vencida", str(row["id"]), "venceu sem uso")
        self.fechar_em_uso_orfas()
        return len(vencidas)

    # ------------------------------------------------------------------ pessoa (recusa do cartão, revogação)
    def recusar_da_etapa(self, step_id: str | None, *, por: str | None) -> list[str]:
        """O dono rejeitou o cartão da etapa presa: a exceção acaba ali. Sem isto ela voltava a valer para qualquer
        etapa do mesmo trio, até de outra execução, e a recusa dele não valeria nada."""
        if not step_id:
            return []
        ids = [str(r["id"]) for r in self.db.query(
            f"SELECT id FROM excecoes_de_politica WHERE step_id=? AND {_EM_ABERTO} ORDER BY criada_em, id", (step_id,))]
        return [i for i in ids if self._encerrar(i, como="recusada", por=por) is not None]

    def revogar(self, excecao_id: str, *, por: str | None) -> Excecao:
        """Encerra pela rota a exceção ainda em aberto (presa ou livre). `ExcecaoEmUso` se o executor já a reservou;
        `ExcecaoInvalida` se ela não existe ou já terminou."""
        self.vencer()
        if self.obter(excecao_id) is None:
            raise ExcecaoInvalida(f"exceção {excecao_id} não existe")
        encerrada = self._encerrar(excecao_id, como="revogada", por=por)
        if encerrada is None:
            atual = self.obter(excecao_id)
            if atual is not None and atual.estado == "em_uso":
                raise ExcecaoEmUso(f"a exceção {excecao_id} já está em uso; o efeito pode ter saído")
            raise ExcecaoInvalida(f"exceção {excecao_id} já terminou ({atual.estado if atual else '?'})")
        return encerrada

    def _encerrar(self, excecao_id: str, *, como: str, por: str | None) -> Excecao | None:
        """Encerra se ainda estiver em aberto; `None` se outra coisa a terminou antes (a corrida fica no WHERE)."""
        cur = self.db.execute(f"UPDATE excecoes_de_politica SET encerrada_em=?, encerrada_por=?, encerramento=?"
                              f" WHERE id=? AND {_EM_ABERTO}", (to_iso(now()), por or "", como, excecao_id))
        if int(cur.rowcount or 0) != 1:
            return None
        self._emitir(f"politica.excecao_{como}", excecao_id, como)
        return self.obter(excecao_id)
