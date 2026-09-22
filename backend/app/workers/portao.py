"""O freio do handshake do worker: quem ainda não provou quem é não pode custar caro.

`/api/worker/ws` é o único endpoint pensado para ficar exposto, e até aqui ele aceitava a conexão **antes** de
qualquer conferência e depois segurava o socket por 30 s esperando a primeira mensagem. Qualquer cliente abria N
sockets e os mantinha, quantas vezes quisesse, sem credencial nenhuma — negação de serviço de custo quase zero
(classe slowloris). E a tentativa com credencial errada não deixava rastro: quem tentasse se passar por um worker
não aparecia em lugar nenhum, então o operador não ficava sabendo.

O que este freio **não** é: proteção contra adivinhação de credencial. A credencial tem 32 bytes aleatórios
(`secrets.token_urlsafe(32)`) comparados com `compare_digest`; força bruta ali é inviável e não é o problema. O
problema é custo de socket e silêncio.

Estado em memória de propósito: é um freio de processo, não uma política durável. Reinício limpa, e está certo —
o que tem de sobreviver ao reinício é o **registro** da recusa, que vai para a tabela de eventos.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

#: Handshakes não autenticados simultâneos por IP. Um worker legítimo abre UM socket e o mantém; reconexão vem
#: depois da queda do anterior. Quatro dá folga para reconexão que se cruza com a detecção de queda.
PENDENTES_POR_IP = 4

#: Falhas de credencial de um mesmo IP antes do bloqueio temporário, e quanto ele dura.
FALHAS_ATE_BLOQUEIO = 5
JANELA_DE_FALHAS_S = 300.0
BLOQUEIO_S = 60.0


@dataclass
class PortaoDoWorker:
    """Um por processo (vive no `WorkerRegistry`), para a suíte poder criar um limpo a cada teste."""

    pendentes: dict[str, int] = field(default_factory=dict)
    falhas: dict[str, list[float]] = field(default_factory=dict)
    bloqueados: dict[str, float] = field(default_factory=dict)

    def bloqueado(self, ip: str, agora: float | None = None) -> bool:
        agora = time.monotonic() if agora is None else agora
        ate = self.bloqueados.get(ip)
        if ate is None:
            return False
        if ate <= agora:
            self.bloqueados.pop(ip, None)
            return False
        return True

    def entrar(self, ip: str, agora: float | None = None) -> bool:
        """`True` quando o handshake pode começar. Quem recebe `False` é fechado antes do `accept()`."""
        if self.bloqueado(ip, agora):
            return False
        if self.pendentes.get(ip, 0) >= PENDENTES_POR_IP:
            return False
        self.pendentes[ip] = self.pendentes.get(ip, 0) + 1
        return True

    def sair(self, ip: str) -> None:
        """Chamado quando o handshake termina — autenticado OU recusado. Sempre em `finally`: esquecer aqui
        transformaria o freio numa porta que fecha sozinha depois de quatro conexões."""
        restante = self.pendentes.get(ip, 0) - 1
        if restante > 0:
            self.pendentes[ip] = restante
        else:
            self.pendentes.pop(ip, None)

    def falhou(self, ip: str, agora: float | None = None) -> bool:
        """Registra uma credencial recusada. Devolve `True` quando esta falha acabou de acionar o bloqueio."""
        agora = time.monotonic() if agora is None else agora
        recentes = [t for t in self.falhas.get(ip, []) if agora - t < JANELA_DE_FALHAS_S]
        recentes.append(agora)
        self.falhas[ip] = recentes
        if len(recentes) >= FALHAS_ATE_BLOQUEIO:
            self.bloqueados[ip] = agora + BLOQUEIO_S
            self.falhas.pop(ip, None)
            return True
        return False

    def perdoou(self, ip: str) -> None:
        """Handshake bem-sucedido zera o histórico: o worker que errou a credencial uma vez e depois acertou não
        carrega a falha para a próxima queda de rede."""
        self.falhas.pop(ip, None)
        self.bloqueados.pop(ip, None)
