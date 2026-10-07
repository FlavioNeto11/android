"""31.240: a reserva de frota por alvo, tomada na passagem pela porta, para o espaçamento entre contas valer na corrida.

O problema (onda 2 de 07/10, op-20261007100755-096a28). O espaçamento entre contas sobre o MESMO alvo
(`fleet_min_spacing_between_accounts_s` + `fleet_spacing_jitter_s`, em `SocialPolicy`) compara com a interação mais
recente das OUTRAS contas. A interação só nasce no commit, e a porta não roda de novo antes dele. As três aprovações
saíram juntas (10:12:51Z), as três portas passaram em 10:12:52–53Z sem interação nenhuma, e dois comentários saíram a
0,4 s um do outro. A guarda do "mesmo pedido noutras contas" só olha a MESMA execução, e cada alvo da operação é uma.

A forma. Uma linha de `travas` por (app, alvo): `frota:<app>:<alvo>`, com `dono` = `<perfil>:<etapa>`. A porta toma a
reserva por compare-and-swap no instante em que libera o efeito (depois da aprovação, nunca no preparo). Outra conta
que chega com a reserva viva é adiada (`retry_at`); a primeira a chegar ganha, e ninguém se adia em roda. A reserva cai
sozinha quando o dono registra o efeito sobre o alvo (daí em diante vale a regra do espaçamento, que conta do commit),
quando a etapa dele termina sem efeito, ou quando vence (`RESERVA_TTL_S`, para a etapa que travou).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..db import Database
from ..util import to_iso

#: Validade da reserva sem efeito registrado: cobre o caminho porta → commit (segundos) com folga larga; só pesa quando a
#: etapa dona trava sem terminar.
RESERVA_TTL_S = 600
#: De quanto em quanto a conta adiada confere de novo: a reserva cai no commit do dono, e esperar o TTL inteiro
#: atrasaria a frota sem motivo.
REVER_RESERVA_S = 30
#: Estados de etapa que não vão mais registrar efeito.
ETAPA_TERMINADA = ("succeeded", "failed", "cancelled", "skipped")


def nome_da_reserva(app_id: str | None, alvo: str) -> str:
    return f"frota:{app_id or '-'}:{alvo}"


class ReservaDaFrota:
    def __init__(self, db: Database) -> None:
        self.db = db

    def tomar(self, *, app_id: str | None, alvo: str, profile_id: str, step_id: str,
              statuses: tuple[str, ...], agora: datetime) -> str | None:
        """`None` = a reserva é desta conta (tomada ou renovada); senão o `retry_at` de quem tem de esperar."""
        nome = nome_da_reserva(app_id, alvo)
        dono = f"{profile_id}:{step_id}"
        quando, vence = to_iso(agora), to_iso(agora + timedelta(seconds=RESERVA_TTL_S))
        for _ in range(3):                                    # perdeu a corrida? relê e decide de novo
            linha = self.db.one("SELECT dono, expira_em, tomada_em FROM travas WHERE nome=?", (nome,))
            if linha is None:
                if self.db.execute("INSERT INTO travas(nome, dono, token, expira_em, tomada_em) VALUES (?,?,1,?,?)"
                                   " ON CONFLICT(nome) DO NOTHING", (nome, dono, vence, quando)).rowcount == 1:
                    return None
                continue
            atual = str(linha["dono"] or "")
            if atual and not atual.startswith(f"{profile_id}:") and not self._livre(linha, alvo, statuses, quando):
                return min(str(linha["expira_em"]), to_iso(agora + timedelta(seconds=REVER_RESERVA_S)))
            # Livre (ou já desta conta): toma com CAS sobre o dono e a hora lidos, para duas contas não tomarem juntas.
            if self.db.execute("UPDATE travas SET dono=?, token=token+1, expira_em=?, tomada_em=? WHERE nome=?"
                               " AND COALESCE(dono,'')=? AND COALESCE(tomada_em,'')=?",
                               (dono, vence, quando, nome, atual, str(linha["tomada_em"] or ""))).rowcount == 1:
                return None
        return to_iso(agora + timedelta(seconds=REVER_RESERVA_S))

    def _livre(self, linha: object, alvo: str, statuses: tuple[str, ...], agora: str) -> bool:
        """A reserva de outra conta já não segura o alvo: venceu, o dono registrou o efeito, ou a etapa dele acabou."""
        dono, vence, tomada = str(linha["dono"]), linha["expira_em"], str(linha["tomada_em"] or "")  # type: ignore[index]
        if not vence or str(vence) <= agora:
            return True
        perfil, _, etapa = dono.partition(":")
        marcas = ",".join("?" * len(statuses))
        efeito = self.db.scalar(
            f"SELECT MAX(occurred_at) FROM social_interactions WHERE profile_id=? AND counterparty=?"
            f" AND direction='outbound' AND occurred_at>=? AND status IN ({marcas})",
            (perfil, alvo, tomada, *statuses))
        if efeito:
            return True
        # Etapa que não se acha não prova que acabou: a reserva fica até o efeito ou o vencimento.
        estado = self._estado_da_etapa(etapa)
        return estado is not None and estado in ETAPA_TERMINADA

    def _estado_da_etapa(self, etapa: str) -> str | None:
        estado = self.db.scalar("SELECT status FROM steps WHERE id=?", (etapa,))
        return str(estado) if estado is not None else None
