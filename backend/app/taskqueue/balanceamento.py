"""Distribuir um comando entre os servidores do parque (Limites → Por servidor, item 4 do pedido de 24/09).

Até aqui toda execução ia para os aparelhos que a pessoa marcava, um por um. Para comando que não depende de
conta — "mande a mensagem de teste em 6 aparelhos do QA Messenger" — quem deveria escolher os aparelhos é quem
sabe a carga de cada máquina. Esta função escolhe; o pré-voo normal da execução continua valendo depois dela.

A regra, em ordem:

1. **Só entra aparelho que pode trabalhar agora ou logo:** do app pedido, fora da loja, sem trabalho aberto,
   numa máquina conectada e fora de manutenção. Desligado só entra se a máquina dele tiver vaga para ligá-lo
   (e cada escolhido desligado consome uma vaga).
2. **A máquina menos carregada recebe o próximo:** carga = (trabalhando + já escolhidos) ÷ capacidade, onde
   capacidade é o teto de "trabalhando ao mesmo tempo" daquela máquina ou, sem teto próprio, as vagas dela.
   Máquina no teto não recebe mais.
3. **Desempates:** aparelho já ligado antes de um que precisa ligar (boot custa minutos); depois a máquina com
   menos CPU em uso; depois a com mais RAM livre.

Pura de propósito — sem banco, sem aparelho — para ser testada com números e não com emuladores.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Servidor:
    id: str
    nome: str
    disponivel: bool                    # conectado e fora de manutenção
    capacidade: int                     # teto de "trabalhando" ou, sem ele, as vagas da máquina
    tem_teto_proprio: bool              # `capacidade` veio de `max_working`?
    trabalhando: int                    # aparelhos com trabalho em execução agora
    vagas_livres: int                   # quantos aparelhos ainda podem ser LIGADOS nela
    cpu_percent: float | None = None
    ram_free_mb: int | None = None
    motivo_indisponivel: str | None = None


@dataclass
class Candidato:
    instance_id: str
    servidor: str
    ligado: bool                        # online agora
    acordavel: bool                     # desligado/hibernado, e o rodízio consegue ligá-lo
    ocupado: bool                       # já tem trabalho aberto


@dataclass
class Escolha:
    instance_id: str
    servidor: str
    precisa_ligar: bool


@dataclass
class Distribuicao:
    escolhidos: list[Escolha] = field(default_factory=list)
    pedidos: int = 0
    #: Por que não deu para escolher mais (uma frase por causa, sem repetição).
    faltas: list[str] = field(default_factory=list)

    @property
    def faltaram(self) -> int:
        return max(0, self.pedidos - len(self.escolhidos))

    def por_servidor(self) -> dict[str, int]:
        cont: dict[str, int] = {}
        for e in self.escolhidos:
            cont[e.servidor] = cont.get(e.servidor, 0) + 1
        return cont


def distribuir(quantos: int, candidatos: list[Candidato], servidores: dict[str, Servidor]) -> Distribuicao:
    """Escolhe até `quantos` aparelhos espalhando pela carga relativa de cada máquina."""
    resultado = Distribuicao(pedidos=quantos)
    faltas: list[str] = []

    def anotar(frase: str) -> None:
        if frase not in faltas:
            faltas.append(frase)

    livres: dict[str, list[Candidato]] = {}
    ocupados = 0
    for c in candidatos:
        srv = servidores.get(c.servidor)
        if srv is None:
            anotar(f"{c.instance_id}: máquina desconhecida")
            continue
        if not srv.disponivel:
            anotar(srv.motivo_indisponivel or f"“{srv.nome}” indisponível")
            continue
        if c.ocupado:
            ocupados += 1
            continue
        if not (c.ligado or c.acordavel):
            continue
        livres.setdefault(c.servidor, []).append(c)
    # Dentro de cada máquina: os ligados primeiro, em ordem estável de id.
    for lista in livres.values():
        lista.sort(key=lambda c: (not c.ligado, c.instance_id))

    escolhidos_em: dict[str, int] = {}
    vagas = {sid: s.vagas_livres for sid, s in servidores.items()}

    def proximo_de(sid: str) -> Candidato | None:
        for c in livres.get(sid, []):
            if c.ligado or vagas.get(sid, 0) > 0:
                return c
        return None

    while len(resultado.escolhidos) < quantos:
        opcoes: list[tuple[tuple[float, int, float, int], str, Candidato]] = []
        for sid, srv in servidores.items():
            cand = proximo_de(sid)
            if cand is None:
                continue
            ocupacao = srv.trabalhando + escolhidos_em.get(sid, 0)
            if srv.tem_teto_proprio and ocupacao >= srv.capacidade:
                anotar(f"“{srv.nome}” está no teto de {srv.capacidade} aparelho(s) trabalhando ao mesmo tempo")
                continue
            carga = ocupacao / max(1, srv.capacidade)
            chave = (carga, 0 if cand.ligado else 1, srv.cpu_percent if srv.cpu_percent is not None else 50.0,
                     -(srv.ram_free_mb or 0))
            opcoes.append((chave, sid, cand))
        if not opcoes:
            break
        opcoes.sort(key=lambda o: (o[0], o[1]))
        _, sid, cand = opcoes[0]
        livres[sid].remove(cand)
        if not cand.ligado:
            vagas[sid] = vagas.get(sid, 0) - 1
        escolhidos_em[sid] = escolhidos_em.get(sid, 0) + 1
        resultado.escolhidos.append(Escolha(cand.instance_id, sid, precisa_ligar=not cand.ligado))

    if resultado.faltaram:
        sobram_desligados = [c for lista in livres.values() for c in lista if not c.ligado]
        if sobram_desligados:
            anotar("as máquinas com aparelhos desligados não têm vaga para ligar mais nenhum")
        if not candidatos:
            anotar("nenhum aparelho do parque está vinculado a este app")
        elif ocupados:
            anotar(f"{ocupados} aparelho(s) deste app já estão ocupados com outro trabalho")
        elif not faltas:
            anotar(f"o parque só tem {len(candidatos)} aparelho(s) deste app")
    # Frase de falta só faz sentido quando faltou: sem isto, o teto visto no meio do caminho viraria aviso.
    resultado.faltas = faltas if resultado.faltaram else []
    return resultado
