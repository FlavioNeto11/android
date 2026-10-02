"""A saída do próprio central e o veredito "sai pela casa", item 29.20 (ADR-056). Tudo `simulated`: medidor injetado
(nenhum teste mede a rede de verdade) e, no `medir_familia`, um eco falso em 127.0.0.1.

O que se prova:
- o julgamento da resposta HTTP é o mesmo da sonda do aparelho (`ip_da_resposta_http`), e `ler_ip_de_saida` segue igual;
- o cache respeita o TTL, não bloqueia, e falha vira "não medida" com o motivo (nunca um IP inventado nem "limpo");
  uma falha solta não apaga a medida anterior enquanto ela vale; passada a validade, volta a "não medida";
- `mesma_saida`: IPv4 igual; IPv6 no mesmo /64; ausente em qualquer lado = `None`;
- `perfil_leva_ipv6`: gerenciado pelo central não leva; WireGuard externo exige endereço E rota IPv6 global;
- `veredito`: acusa IPv4 e IPv6 iguais ao central, acusa IPv6 fora do perfil, e nunca vira "limpo" por falta de medida.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config import RedeSondaCfg
from app.devices import rede_saida_central as rsc
from app.devices.rede_saida_central import (SaidaDoCentral, mesma_saida, perfil_leva_ipv6, veredito, veredito_sem_rede)
from app.devices.sonda_rede import IpDeSaida, ip_da_resposta_http, ler_ip_de_saida

CASA4, CASA6 = "177.10.20.30", "2804:14c:1:2::9"
OUTRO4, OUTRO6 = "45.162.8.9", "2804:99:1:2::9"


def _eco(ip: str, status: str = "200 OK") -> str:
    return f"HTTP/1.0 {status}\nContent-Type: text/plain\n\n{ip}"


# ---------------------------------------------------------------------------- o parser comum
def test_ip_da_resposta_http_julga_como_a_sonda_do_aparelho() -> None:
    assert ip_da_resposta_http(_eco(CASA4), 4) == IpDeSaida(CASA4, "ok")
    assert ip_da_resposta_http(_eco(CASA6), 6) == IpDeSaida(CASA6, "ok")
    assert "IPv6, não IPv4" in ip_da_resposta_http(_eco(CASA6), 4).motivo
    assert "não é endereço público" in ip_da_resposta_http(_eco("10.0.2.15"), 4).motivo
    assert ip_da_resposta_http(_eco("x", "503 Service Unavailable"), 4).motivo.startswith("o eco respondeu HTTP/1.0 503")
    assert "não é IP" in ip_da_resposta_http(_eco("<html>"), 4).motivo


def test_ler_ip_de_saida_do_nc_segue_igual_depois_da_fatoracao() -> None:
    from app.devices import sonda_rede                     # os marcadores são constantes privadas do módulo
    texto = (f"U=2000\n{sonda_rede._INICIO}\n{_eco(CASA4)}\n{sonda_rede._FIM}\nX=0\n")
    assert ler_ip_de_saida(texto, 4) == IpDeSaida(CASA4, "ok")
    vazio = f"U=2000\n{sonda_rede._INICIO}\n\n{sonda_rede._FIM}\nX=124\n"
    assert ler_ip_de_saida(vazio, 4) == IpDeSaida(None, "sem resposta (tempo esgotado)")
    with pytest.raises(ValueError):
        ler_ip_de_saida(texto.replace(sonda_rede._FIM, ""), 4)


# ---------------------------------------------------------------------------- o cache da saída do central
class _Relogio:
    def __init__(self) -> None:
        self.t = 1_000_000.0

    def __call__(self) -> float:
        return self.t


def _cfg(**kw: Any) -> RedeSondaCfg:
    return RedeSondaCfg(**kw)


def _central(medidor: Any, relogio: _Relogio, **kw: Any) -> SaidaDoCentral:
    cfg = _cfg(**kw)
    return SaidaDoCentral(lambda: cfg, medidor=medidor, relogio=relogio)


async def test_antes_da_primeira_medida_e_com_a_medida_desligada_nada_vale() -> None:
    rel = _Relogio()
    c = _central(None, rel)
    a = c.atual()
    assert a["ipv4"] is None and a["ipv6"] is None and a["measured_at"] is None
    assert "ainda não medido" in str(a["reason"]) and "ROLE=api" in str(a["reason"])

    chamadas: list[int] = []

    async def _nunca(hosts: list[str], v: int, prazo: float) -> IpDeSaida:
        chamadas.append(v)
        return IpDeSaida(CASA4, "ok")

    off = _central(_nunca, rel, medir_central=False)
    assert await off.atualizar_se_vencida() is False and chamadas == []
    assert "desligada" in str(off.atual()["reason"]) and off.atual()["ipv4"] is None


async def test_mede_as_duas_familias_respeita_o_ttl_e_remede_depois() -> None:
    rel = _Relogio()
    chamadas: list[tuple[int, list[str], float]] = []

    async def _medidor(hosts: list[str], v: int, prazo: float) -> IpDeSaida:
        chamadas.append((v, hosts, prazo))
        return IpDeSaida(CASA4 if v == 4 else CASA6, "ok")

    c = _central(_medidor, rel, central_ttl_s=600, central_prazo_s=4)
    assert await c.atualizar_se_vencida() is True
    a = c.atual()
    assert (a["ipv4"], a["ipv6"], a["reason"]) == (CASA4, CASA6, "ok") and a["measured_at"]
    # Os hosts e o prazo vêm da config; as duas famílias foram medidas.
    assert sorted(v for v, _h, _p in chamadas) == [4, 6] and all(p == 4 for _v, _h, p in chamadas)
    assert chamadas[0][1] == ["api.ipify.org", "ipv4.icanhazip.com"] or chamadas[1][1] == ["api.ipify.org", "ipv4.icanhazip.com"]
    # Dentro do TTL: não mede de novo.
    rel.t += 599
    assert await c.atualizar_se_vencida() is False and len(chamadas) == 2
    rel.t += 2
    assert await c.atualizar_se_vencida() is True and len(chamadas) == 4


async def test_falha_vira_nao_medida_com_motivo_e_sem_ipv6_o_ipv4_segue() -> None:
    rel = _Relogio()

    async def _sem_v6(hosts: list[str], v: int, prazo: float) -> IpDeSaida:
        return IpDeSaida(CASA4, "ok") if v == 4 else IpDeSaida(None, "api6.ipify.org: Network is unreachable")

    c = _central(_sem_v6, rel)
    await c.atualizar_se_vencida()
    a = c.atual()
    assert a["ipv4"] == CASA4 and a["ipv6"] is None
    assert "IPv6: api6.ipify.org: Network is unreachable" in str(a["reason"])

    async def _explode(hosts: list[str], v: int, prazo: float) -> IpDeSaida:
        raise RuntimeError("segredo-que-nao-deve-vazar")

    q = _central(_explode, rel)
    assert await q.atualizar_se_vencida() is True                   # não levanta
    a = q.atual()
    assert a["ipv4"] is None and a["ipv6"] is None and a["measured_at"] is None
    assert "falha ao medir (RuntimeError)" in str(a["reason"]) and "segredo" not in str(a["reason"])


async def test_falha_solta_mantem_a_medida_anterior_ate_vencer_e_depois_volta_a_nao_medida() -> None:
    rel = _Relogio()
    deu_certo = {"v": True}

    async def _medidor(hosts: list[str], v: int, prazo: float) -> IpDeSaida:
        if v == 6:
            return IpDeSaida(None, "sem IPv6")
        return IpDeSaida(CASA4, "ok") if deu_certo["v"] else IpDeSaida(None, "sem resposta")

    c = _central(_medidor, rel, central_ttl_s=600)
    await c.atualizar_se_vencida()
    deu_certo["v"] = False
    rel.t += 601
    assert await c.atualizar_se_vencida() is True
    a = c.atual()
    assert a["ipv4"] == CASA4                                        # a anterior ainda vale (até 3 x TTL)
    assert "medida anterior mantida" in str(a["reason"]) and "sem resposta" in str(a["reason"])
    rel.t += 3 * 600
    a = c.atual()
    assert a["ipv4"] is None and a["measured_at"] is None            # vencida: não medida, nunca "limpo"
    assert "venceu" in str(a["reason"]) or "sem resposta" in str(a["reason"])


async def test_familia_sem_ip_e_tentada_de_novo_antes_do_ttl() -> None:
    rel = _Relogio()
    n = {"v6": 0}

    async def _medidor(hosts: list[str], v: int, prazo: float) -> IpDeSaida:
        if v == 4:
            return IpDeSaida(CASA4, "ok")
        n["v6"] += 1
        return IpDeSaida(None, "sem IPv6")

    c = _central(_medidor, rel, central_ttl_s=3600)
    await c.atualizar_se_vencida()
    rel.t += 60
    assert await c.atualizar_se_vencida() is False                   # cedo demais para insistir
    rel.t += 70
    assert await c.atualizar_se_vencida() is True and n["v6"] == 2   # a família sem IP volta antes do TTL


async def test_o_laco_nao_morre_com_uma_volta_ruim(monkeypatch: pytest.MonkeyPatch) -> None:
    rel = _Relogio()
    voltas = {"n": 0}

    async def _medidor(hosts: list[str], v: int, prazo: float) -> IpDeSaida:
        return IpDeSaida(CASA4, "ok")

    c = _central(_medidor, rel)
    original = c.atualizar_se_vencida

    async def _quebra_na_primeira() -> bool:
        voltas["n"] += 1
        if voltas["n"] == 1:
            raise RuntimeError("volta ruim")
        return await original()

    c.atualizar_se_vencida = _quebra_na_primeira                      # type: ignore[method-assign]
    tarefa = asyncio.create_task(c.laco(intervalo_s=0.01))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if c.atual()["ipv4"]:
            break
    tarefa.cancel()
    assert voltas["n"] >= 2 and c.atual()["ipv4"] == CASA4


# ---------------------------------------------------------------------------- o medidor de verdade, contra um eco local
async def test_medir_familia_fala_http_por_socket_e_cai_para_o_proximo_host(monkeypatch: pytest.MonkeyPatch) -> None:
    pedidos: list[bytes] = []

    async def _eco_local(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        pedidos.append(await leitor.readuntil(b"\r\n\r\n"))
        escritor.write(f"HTTP/1.0 200 OK\r\nContent-Type: text/plain\r\n\r\n{CASA4}\n".encode())
        await escritor.drain()
        escritor.close()

    servidor = await asyncio.start_server(_eco_local, "127.0.0.1", 0)
    porta = servidor.sockets[0].getsockname()[1]
    monkeypatch.setattr(rsc, "_PORTA", porta)
    try:
        # O primeiro host recusa a conexão (nada escuta em 127.0.0.2:porta), o segundo é o eco local.
        lido = await rsc.medir_familia(["127.0.0.2", "127.0.0.1"], 4, 3)
        assert lido == IpDeSaida(CASA4, "ok")
        assert pedidos and pedidos[0].startswith(b"GET / HTTP/1.0\r\nHost: 127.0.0.1\r\n")
        # Família 6 em host só IPv4: sem IP, com o motivo de cada host (e sem levantar).
        sem = await rsc.medir_familia(["127.0.0.1"], 6, 3)
        assert sem.ip is None and sem.motivo.startswith("127.0.0.1:")
    finally:
        servidor.close()
        await servidor.wait_closed()


async def test_medir_familia_sem_resposta_estoura_o_prazo(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _mudo(leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        await asyncio.sleep(5)

    servidor = await asyncio.start_server(_mudo, "127.0.0.1", 0)
    monkeypatch.setattr(rsc, "_PORTA", servidor.sockets[0].getsockname()[1])
    try:
        lido = await rsc.medir_familia(["127.0.0.1"], 4, 0.3)
        assert lido.ip is None and "sem resposta em 0,3 s" in lido.motivo.replace(".", ",")
    finally:
        servidor.close()


# ---------------------------------------------------------------------------- funções puras
def test_mesma_saida_ipv4_igual_ipv6_no_mesmo_slash_64_e_ausente_e_none() -> None:
    assert mesma_saida(4, CASA4, CASA4) is True and mesma_saida(4, OUTRO4, CASA4) is False
    assert mesma_saida(6, "2804:14c:1:2::abcd", CASA6) is True             # outro endereço do mesmo /64
    assert mesma_saida(6, OUTRO6, CASA6) is False
    for a, c in ((None, CASA4), (CASA4, None), ("", CASA4), (CASA4, "lixo"), (CASA6, CASA6)):
        assert mesma_saida(4, a, c) is None, (a, c)                         # sem medida, ou família trocada


def test_perfil_leva_ipv6() -> None:
    wg = {"peer_public_key": "x", "address": "10.9.0.2/32"}
    assert perfil_leva_ipv6(None, None) is False                           # sem perfil
    assert perfil_leva_ipv6({"servidor": "central"}, "singbox") is False   # gerenciado pelo central: túnel só IPv4
    assert perfil_leva_ipv6({"servidor": "central", "address": "fd00::2/128"}, "wireguard") is False
    assert perfil_leva_ipv6(wg, "wireguard") is False                      # só endereço IPv4
    assert perfil_leva_ipv6({**wg, "address": "10.9.0.2/32, fd00::2/128"}, "wireguard") is True   # padrão: ::/0
    assert perfil_leva_ipv6({**wg, "address": ["10.9.0.2/32", "fd00::2/128"]}, "wireguard") is True
    assert perfil_leva_ipv6({**wg, "address": "10.9.0.2/32, fd00::2/128",
                             "allowed_ips": ["0.0.0.0/0"]}, "wireguard") is False        # sem rota IPv6
    assert perfil_leva_ipv6({**wg, "address": "10.9.0.2/32, fd00::2/128",
                             "allowed_ips": ["10.0.0.0/8", "fd00::/8"]}, "wireguard") is False   # rota que não é a internet
    assert perfil_leva_ipv6({**wg, "address": "10.9.0.2/32, fd00::2/128",
                             "allowed_ips": ["2000::/3"]}, "wireguard") is True
    assert perfil_leva_ipv6({**wg, "address": "isto-nao-e-ip"}, "wireguard") is False    # malformado: não se presume


_CENTRAL = {"ipv4": CASA4, "ipv6": CASA6, "measured_at": "2026-10-02T12:00:00+00:00", "reason": "ok"}
_CENTRAL_SEM = {"ipv4": None, "ipv6": None, "measured_at": None, "reason": "IPv4: ainda não medido"}


def _v(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(medido=True, ipv4=OUTRO4, ipv6=None, central=_CENTRAL, leva_ipv6=False,
                                com_rede_pedida=True)
    base.update(kw)
    return veredito(**base).como_dict()


def test_veredito_acusa_ipv4_ipv6_e_ipv6_fora_do_perfil() -> None:
    limpo = _v()
    assert limpo == {"ipv4": False, "ipv6": False, "ipv6_outside_profile": False, "leaves_by_home": False,
                     "basis": "measured", "reason": "a saída medida não é a do central"}
    v4 = _v(ipv4=CASA4)
    assert v4["ipv4"] is True and v4["leaves_by_home"] is True and CASA4 in str(v4["reason"])
    v6 = _v(ipv6="2804:14c:1:2::77")
    assert v6["ipv6"] is True and v6["leaves_by_home"] is True and "/64" in str(v6["reason"])
    # IPv6 medido (de outro prefixo) e o perfil não o leva: acusa FORA DO PERFIL, sem ser a casa por igualdade.
    fora = _v(ipv6=OUTRO6, leva_ipv6=False)
    assert fora["ipv6"] is False and fora["ipv6_outside_profile"] is True and fora["leaves_by_home"] is True
    # O mesmo IPv6 com o perfil que o leva: não acusa.
    dentro = _v(ipv6=OUTRO6, leva_ipv6=True)
    assert dentro["ipv6_outside_profile"] is False and dentro["leaves_by_home"] is False


def test_veredito_nunca_vira_limpo_por_falta_de_medida() -> None:
    # Central sem medida: o aparelho com IPv4 medido fica None, com o motivo do central.
    sem_central = _v(central=_CENTRAL_SEM)
    assert sem_central["ipv4"] is None and sem_central["leaves_by_home"] is None
    assert "IPv4 do central sem medida" in str(sem_central["reason"]) and "ainda não medido" in str(sem_central["reason"])
    # Central só com IPv4 e o aparelho com IPv6: o IPv6 é incerto, então o resumo também.
    meio = _v(ipv6=OUTRO6, leva_ipv6=True, central={**_CENTRAL, "ipv6": None, "reason": "IPv6: sem rota"})
    assert meio["ipv4"] is False and meio["ipv6"] is None and meio["leaves_by_home"] is None
    # Aparelho nunca medido, com e sem rede pedida.
    nunca = _v(ipv4=None, ipv6=None)
    assert nunca["leaves_by_home"] is None and nunca["ipv4"] is None and "nunca foi medida" in str(nunca["reason"])
    sem_rede = _v(ipv4=None, ipv6=None, com_rede_pedida=False)
    assert "sem rede pedida" in str(sem_rede["reason"]) and sem_rede["leaves_by_home"] is None


def test_veredito_com_a_revisao_pedida_ainda_nao_medida_so_o_positivo_vale() -> None:
    # A saída da linha é de uma revisão anterior: "não é casa" não vale (incerto); "é casa" ainda acusa.
    antigo_limpo = _v(medido=False)
    assert antigo_limpo["ipv4"] is None and antigo_limpo["leaves_by_home"] is None
    assert "ainda não foi medida" in str(antigo_limpo["reason"])
    antigo_casa = _v(medido=False, ipv4=CASA4)
    assert antigo_casa["ipv4"] is True and antigo_casa["leaves_by_home"] is True
    assert _v(medido=False, ipv6=OUTRO6, leva_ipv6=False)["ipv6_outside_profile"] is True


def test_caso_notebook_da_lan_e_vpn_central_wireguard_sao_acusados() -> None:
    """O caso que o item existe para pegar: o WireGuard que termina no central faz SNAT pela casa, então o IPv4 medido
    no aparelho é o do central — e o túnel do central não leva IPv6, então o IPv6 medido sai fora dele."""
    r = veredito(medido=True, ipv4=CASA4, ipv6="2804:14c:1:2::1234", central=_CENTRAL, leva_ipv6=False,
                 com_rede_pedida=True).como_dict()
    assert r["ipv4"] is True and r["ipv6"] is True and r["ipv6_outside_profile"] is True
    assert r["leaves_by_home"] is True



# ---------------------------------------------------------------------------- aparelho sem rede pedida (29.20, extensão)
def _vs(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(ipv4=None, ipv6=None, central=_CENTRAL, falha=None)
    base.update(kw)
    return veredito_sem_rede(**base).como_dict()


def test_sem_rede_pedida_sem_medida_e_presumido_nunca_nulo_nem_limpo() -> None:
    r = _vs()
    assert r["leaves_by_home"] is True and r["basis"] == "presumed"
    assert r["ipv4"] is None and r["ipv6"] is None and r["ipv6_outside_profile"] is None
    assert "presumido: sem rede pedida" in str(r["reason"]) and "ainda não foi medida" in str(r["reason"])
    # Com o central também sem medida: nada vira "limpo" (continua presumido).
    assert _vs(central=_CENTRAL_SEM)["basis"] == "presumed"


def test_sem_rede_pedida_medido_igual_ao_central_e_medido_nao_presumido() -> None:
    r = _vs(ipv4=CASA4)
    assert r["leaves_by_home"] is True and r["basis"] == "measured" and r["ipv4"] is True
    assert CASA4 in str(r["reason"]) and str(r["reason"]).startswith("medido:")
    v6 = _vs(ipv4=OUTRO4, ipv6="2804:14c:1:2::77")                    # IPv4 diferente, mas IPv6 no /64 do central
    assert v6["leaves_by_home"] is True and v6["basis"] == "measured" and v6["ipv6"] is True


def test_sem_rede_pedida_medido_diferente_mostra_que_nao_e_casa() -> None:
    r = _vs(ipv4=OUTRO4)
    assert r["leaves_by_home"] is False and r["basis"] == "measured" and r["ipv4"] is False
    assert OUTRO4 in str(r["reason"]) and "diferente do central" in str(r["reason"])
    # Central sem medida: não dá para dizer que é diferente. Medido, mas presumido.
    incerto = _vs(ipv4=OUTRO4, central=_CENTRAL_SEM)
    assert incerto["leaves_by_home"] is True and incerto["basis"] == "presumed"
    assert "sem medida" in str(incerto["reason"])


def test_sem_rede_pedida_com_falha_de_sonda_continua_presumido_com_o_motivo() -> None:
    r = _vs(falha="IPv4 sem IP: api.ipify.org: sem resposta (tempo esgotado)")
    assert r["leaves_by_home"] is True and r["basis"] == "presumed" and r["ipv4"] is None
    assert "a última sonda de IP falhou" in str(r["reason"]) and "tempo esgotado" in str(r["reason"])
