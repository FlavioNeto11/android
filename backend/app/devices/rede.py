"""Rede por aparelho (ADR-056, Fase 25): perfis de VPN e de proxy, o desejado × observado de cada aparelho e as
medições da saída feitas de dentro dele. Este módulo é o MODELO e o contrato (item 25.2) sobre as tabelas da 057.

O que mora aqui:
- **perfis** (`network_profiles`): tipo, protocolo, endpoint e `secret_ref`. O segredo (chave privada, senha do
  proxy, certificado) chega UMA vez no cadastro, vai direto ao cofre e nunca mais sai por aqui: a rota devolve só
  `has_secret`. Quem lê o valor é a provisão de rede (25.3/25.4), pelo canal restrito do cofre;
- **atribuição** (`device_network`): o desejado de cada aparelho, com prévia em lote. A loja e o aparelho em
  quarentena ficam fora, e aparelho com conta real só muda de saída com a confirmação da pessoa, POR APARELHO
  (ADR-056 §7) — trocar o IP de saída de uma conta logada costuma disparar verificação (K-057);
- **observação**: o único caminho que muda o `state`. Estado só AVANÇA com evidência: `configurado`/`conectado`
  pedem a evidência lida do aparelho, e `trafego_verificado` só nasce de uma medição de dentro do aparelho, por app.
  Configuração diferente não prova IP diferente (ADR-056 §1);
- o **legado da 041** (`device_proxy_state`, proxy global do Android): lido como `configurado` no máximo, porque a
  releitura do `settings` prova a configuração, não o tráfego.

O que NÃO mora aqui, de propósito: aplicar no aparelho (25.4: a receita em `rede_aplicacao.py`, o QUANDO em
`rede_convergencia.py`, o servidor do central em `rede_servidor.py`), a sonda (25.5: `rede_medicao.py`, com os
comandos em `sonda_rede.py`) e os aparelhos do worker (25.7). `verify` e `reapply` registram o pedido e respondem
202; quem executa é a convergência, no próximo ponto seguro (ou já, por `POST …/apply`). Nada neste módulo finge aplicação: o estado só sai do lugar pela observação com evidência.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from ..db import Row, dumps, loads
from ..models import (_CHAVE_DE_SEGREDO, DeviceNetworkDTO, NetworkMeasurementDTO, NetworkPolicy, NetworkProfileDTO,
                      NetworkProfileKind, NetworkProtocol, NetworkState)
from ..security.redaction import redact
from ..security.secret_store import SecretStoreLocked, SecretStoreUnavailable
from ..util import novo_id_de_app, now_iso, parse_iso
from .proxy import _HOST
from .rede_saida_central import perfil_leva_ipv6, veredito, veredito_sem_rede
from .sonda_rede import pernas_udp

if TYPE_CHECKING:
    from ..state import AppState

log = logging.getLogger(__name__)

#: Qual protocolo cabe em qual tipo. O CHECK da 057 aceita cada coluna sozinha; o par é regra daqui: um perfil
#: `vpn` com `http` não tem cliente que o aplique, e o erro só apareceria no aparelho.
PROTOCOLOS_POR_TIPO: dict[str, frozenset[str]] = {"vpn": frozenset({"wireguard", "singbox"}),
                                                  "proxy": frozenset({"http", "socks5"})}
#: Estados em que o aparelho tem a revisão pedida aplicada. `pendente` é o único que diz "ainda não".
_APLICADOS: tuple[str, ...] = ("configurado", "conectado", "trafego_verificado", "parcial")
#: Estados que só nascem de uma medição, com IP de saída, da revisão pedida: a saída gravada na linha é a DESTA revisão.
_MEDIDOS: tuple[str, ...] = ("trafego_verificado", "parcial")
#: Resultado de uma medição por app (`network_measurements.per_app`): `ok` = o app saiu pela rede pedida;
#: `fora_da_rede` = saiu por outro caminho (vazamento); `falhou` = não conectou; `nao_medido` = a sonda não conseguiu
#: medir aquele app (não instalado, não lido); `sem_trafego` = instalado e com 0 byte na janela, na VPN e na física
#: (29.44): não prova nem desprova. Só `ok` prova; o navegador não prova os outros apps (ADR-056 §3).
ResultadoPorApp = Literal["ok", "fora_da_rede", "falhou", "nao_medido", "sem_trafego"]
#: 29.44: o app parado na janela não segura o `parcial` quando outro app (a sonda do shell conta) passou pelo túnel e
#: nenhum saiu por fora. "Verificado" = tudo o que trafegou passou pelo túnel; quando o app parado trafegar numa janela
#: seguinte, a medição o reavalia e o estado muda sozinho.
SEM_TRAFEGO = "sem_trafego"
#: Teto do JSON de `params`: é configuração (MTU, DNS, lista de apps), não arquivo.
_PARAMS_MAX = 4000
#: Teto do segredo: uma configuração do sing-box com certificado cabe com folga; mais que isso não é segredo.
_SEGREDO_MAX = 16384


class RedeError(Exception):
    """Recusa de regra, com o código que o painel lê. `extra` vai no corpo do erro (a prévia do lote recusado)."""

    def __init__(self, status: int, code: str, message: str, **extra: object):
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def _host_valido(host: str) -> str:
    """Nome de host, IPv4 ou IPv6. O nome segue a regra do proxy legado (nada de espaço, aspas ou `;`): o valor
    acaba numa configuração gravada no aparelho, e nome estranho ali é injeção, não endereço."""
    host = host.strip()
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if not re.match(_HOST, host):
        raise ValueError("endpoint_host precisa ser um nome de host, um IPv4 ou um IPv6")
    return host


def _conferir_segredo(valor: object) -> None:
    """Regra do segredo sem nunca citá-lo: nem na mensagem, nem num `input` de erro de validação."""
    if valor is None:
        return
    if not isinstance(valor, str):
        raise ValueError("o segredo precisa ser texto")
    if not valor:
        raise ValueError("segredo vazio: omita o campo quando o perfil não tem segredo")
    if len(valor) > _SEGREDO_MAX:
        raise ValueError(f"segredo maior que {_SEGREDO_MAX} caracteres")


# ============================================================================ corpos das rotas
class NetworkProfileInput(BaseModel):
    """Cadastro de um perfil. `secret` chega UMA vez e vai ao cofre; nunca volta em resposta, evento ou log."""

    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=60)
    kind: NetworkProfileKind
    protocol: NetworkProtocol
    endpoint_host: str = Field(min_length=1, max_length=253)
    endpoint_port: int = Field(ge=1, le=65535)
    params: dict[str, object] = Field(default_factory=dict)
    secret: SecretStr | None = None

    @field_validator("name")
    @classmethod
    def _nome(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("o perfil precisa de nome")
        return v

    @field_validator("endpoint_host")
    @classmethod
    def _host(cls, v: str) -> str:
        return _host_valido(v)

    @field_validator("secret")
    @classmethod
    def _segredo(cls, v: SecretStr | None) -> SecretStr | None:
        _conferir_segredo(v.get_secret_value() if v is not None else None)
        return v

    @field_validator("params")
    @classmethod
    def _params(cls, params: dict[str, object]) -> dict[str, object]:
        suspeitas = sorted(k for k in params if _CHAVE_DE_SEGREDO.search(k))
        if suspeitas:
            raise ValueError(f"params não guarda segredo ({', '.join(suspeitas)}): mande-o em `secret`, que vai ao "
                             "cofre")
        texto = dumps(params)
        if len(texto) > _PARAMS_MAX:
            raise ValueError(f"params passa de {_PARAMS_MAX} caracteres: é configuração, não arquivo")
        # Pela REDAÇÃO por formato, não por lista de valores: o que ela mascararia num log (par chave/valor de
        # segredo aninhado, URL com senha e, com o 25.3, `socks5://`, `PrivateKey`) não entra em coluna de texto.
        if redact(texto) != texto:
            raise ValueError("params parece conter um segredo (credencial em URL ou par chave/valor): mande-o em "
                             "`secret`, que vai ao cofre")
        return _saida_esperada_valida(params)

    @model_validator(mode="after")
    def _par_tipo_protocolo(self) -> NetworkProfileInput:
        if self.protocol not in PROTOCOLOS_POR_TIPO[self.kind]:
            aceitos = ", ".join(sorted(PROTOCOLOS_POR_TIPO[self.kind]))
            raise ValueError(f"um perfil '{self.kind}' usa {aceitos}, não '{self.protocol}'")
        return self


def ler_cadastro(corpo: object) -> NetworkProfileInput:
    """O corpo do `POST /api/network/profiles`, com o segredo SEPARADO antes de qualquer validação.

    Por quê: o 422 do pydantic devolve o `input` de cada erro, e num campo que falta, ou numa regra do modelo
    inteiro (o par tipo × protocolo), o `input` é o CORPO inteiro — segredo junto. O tratador de `main.py` só limpa
    o erro cujo caminho tem nome sensível. Tirando o `secret` antes, nenhum erro de validação o carrega; a rota
    ainda devolve os erros sem `input` (os `params` recusados por parecerem segredo também não voltam).
    Levanta `ValueError` (segredo inválido, sem citá-lo) ou `pydantic.ValidationError` (resto do corpo)."""
    if not isinstance(corpo, dict):
        raise ValueError("o corpo precisa ser um objeto JSON")
    dados = dict(corpo)
    segredo = dados.pop("secret", None)
    body = NetworkProfileInput.model_validate(dados)
    _conferir_segredo(segredo)
    if isinstance(segredo, str):
        body.secret = SecretStr(segredo)
    return body


class NetworkAssignBody(BaseModel):
    """Atribuição em lote (ou de um aparelho só, com uma lista de um).

    Campo omitido = fica como está em cada aparelho; `vpn_profile_id: null` / `proxy_profile_id: null` = tirar.
    `policy` omitida (ou nula) = fica como está (padrão `livre` para quem nunca teve rede pedida).

    O alvo é sempre a lista explícita: o parque inteiro nunca é inferido da falta dela (uma VPN fora do ar derruba
    a internet de todas as contas de uma vez). `confirm_real_account` são os aparelhos com conta real que a pessoa
    autoriza a mudar de saída, UM A UM (ADR-056 §7): um booleano para o lote aprovaria o que ninguém olhou."""

    model_config = ConfigDict(extra="forbid")
    instance_ids: list[str] = Field(min_length=1, max_length=200)
    vpn_profile_id: str | None = Field(default=None, max_length=80)
    proxy_profile_id: str | None = Field(default=None, max_length=80)
    policy: NetworkPolicy | None = None
    confirm_real_account: list[str] = Field(default_factory=list, max_length=200)
    dry_run: bool = False


class NetworkMeasurementInput(BaseModel):
    """Uma medição feita de dentro do aparelho (quem produz é a sonda do 25.5). `None` = não medido."""

    model_config = ConfigDict(extra="forbid")
    method: str = Field(min_length=1, max_length=60)
    measured_at: str | None = None
    egress_ipv4: str | None = None
    egress_ipv6: str | None = None
    dns_resolver: str | None = Field(default=None, max_length=253)
    udp_ok: bool | None = None
    per_app: dict[str, ResultadoPorApp] = Field(default_factory=dict)
    leak_blocked: bool | None = None
    detail: str | None = Field(default=None, max_length=500)

    @field_validator("egress_ipv4")
    @classmethod
    def _v4(cls, v: str | None) -> str | None:
        return _saida_publica(v, ipaddress.IPv4Address, "egress_ipv4")

    @field_validator("egress_ipv6")
    @classmethod
    def _v6(cls, v: str | None) -> str | None:
        return _saida_publica(v, ipaddress.IPv6Address, "egress_ipv6")


def _saida_publica(v: str | None, tipo: type[ipaddress.IPv4Address] | type[ipaddress.IPv6Address],
                   campo: str) -> str | None:
    """O IP de saída é o PÚBLICO, visto de fora (ADR-056 §1, T7). O endereço do NAT do emulador (10.0.2.15), o da
    interface do túnel (10.0.0.2), o loopback, o link-local, a CGNAT e as faixas reservadas são endereço de
    interface ou configuração: uma "medição" que os devolve não mediu a saída, e não entra nem no histórico."""
    if v is None:
        return None
    ip = ipaddress.ip_address(v.strip())
    if not isinstance(ip, tipo):
        raise ValueError(f"{campo} não é {'IPv4' if tipo is ipaddress.IPv4Address else 'IPv6'}")
    if not ip.is_global:
        raise ValueError(f"{campo} não é endereço público ({ip}): interface, NAT ou túnel não são a saída medida")
    return str(ip)


# ============================================================================ saída esperada por aparelho (item 29.6)
# O objetivo é uma saída pública distinta e estável por aparelho (ADR-056 §1). O caminho já existia (um perfil
# `vpn/wireguard` com servidor externo, ou um proxy dedicado encadeado); faltava a plataforma saber QUAL saída o perfil
# deveria dar, para comparar com a medida. `params.egress_esperado` (IPv4) e `params.egress_esperado_ipv6` a declaram:
# é configuração (sem migração: `params` é JSON), validada pela mesma regra de endereço público da medição.
_CHAVES_DA_SAIDA_ESPERADA: tuple[tuple[str, type[ipaddress.IPv4Address] | type[ipaddress.IPv6Address]], ...] = (
    ("egress_esperado", ipaddress.IPv4Address), ("egress_esperado_ipv6", ipaddress.IPv6Address))


def _saida_esperada_valida(params: dict[str, object]) -> dict[str, object]:
    """`params` com a saída esperada conferida e na forma canônica (é com ela que a medição compara). Chave ausente =
    perfil sem saída esperada, e nada muda para ele."""
    saida = dict(params)
    for chave, tipo in _CHAVES_DA_SAIDA_ESPERADA:
        if chave not in params:
            continue
        valor, campo = params[chave], f"params.{chave}"
        familia = "IPv4" if tipo is ipaddress.IPv4Address else "IPv6"
        if not isinstance(valor, str) or not valor.strip():
            raise ValueError(f"{campo} precisa ser um endereço {familia} público, em texto (omita a chave no perfil "
                             "sem saída esperada)")
        try:
            ipaddress.ip_address(valor.strip())
        except ValueError:
            raise ValueError(f"{campo} não é um endereço IP ({valor.strip()[:60]!r}): é o {familia} público pelo qual "
                             "o aparelho deve sair, não um nome de host") from None
        saida[chave] = _saida_publica(valor, tipo, campo)
    return saida


@dataclass(frozen=True)
class SaidaEsperada:
    """A saída que o perfil de um aparelho declara (`params.egress_esperado*`), com o perfil que a declarou."""

    ipv4: str | None
    ipv6: str | None
    profile_id: str
    profile_name: str

    def descrever(self) -> str:
        return " e ".join(ip for ip in (self.ipv4, self.ipv6) if ip)

    def como_dict(self) -> dict[str, object]:
        return {"ipv4": self.ipv4, "ipv6": self.ipv6, "profile_id": self.profile_id, "profile_name": self.profile_name}


def perfil_da_saida(vpn_profile_id: str | None, proxy_profile_id: str | None) -> str | None:
    """O perfil que dá a saída FINAL do aparelho: o proxy, se há um (o tráfego sai do túnel e ainda passa por ele —
    `rede_aplicacao.montar_plano` o encadeia depois da VPN); senão a VPN. Sem fallback: com um proxy SEM saída
    esperada por cima de uma VPN que a tem, a esperada da VPN não é a saída do aparelho, e compará-la o deixaria
    `parcial` para sempre."""
    return proxy_profile_id or vpn_profile_id


def _esperada_do_perfil(perfil: Row | None) -> SaidaEsperada | None:
    if perfil is None:
        return None
    params = loads(perfil["params"], {}) or {}
    v4, v6 = params.get("egress_esperado"), params.get("egress_esperado_ipv6")
    if not (v4 or v6):
        return None
    return SaidaEsperada(ipv4=str(v4) if v4 else None, ipv6=str(v6) if v6 else None, profile_id=str(perfil["id"]),
                         profile_name=str(perfil["name"]))


def saida_esperada(st: AppState, vpn_profile_id: str | None, proxy_profile_id: str | None) -> SaidaEsperada | None:
    """A saída esperada de um aparelho com estes perfis, ou `None` (o perfil da saída final não a declara)."""
    pid = perfil_da_saida(vpn_profile_id, proxy_profile_id)
    if pid is None:
        return None
    return _esperada_do_perfil(st.db.one("SELECT id, name, params FROM network_profiles WHERE id=?", (pid,)))


def saida_divergente(esperada: SaidaEsperada | None, ipv4: str | None, ipv6: str | None) -> list[str]:
    """O que a saída MEDIDA tem de diferente da esperada, por família declarada. Família declarada e não medida também
    conta: a saída esperada não foi vista, e incerteza não verifica. Sem esperada, nada (a regra de antes)."""
    if esperada is None:
        return []
    falta = []
    for familia, quer, medida in (("IPv4", esperada.ipv4, ipv4), ("IPv6", esperada.ipv6, ipv6)):
        if quer and medida != quer:
            falta.append((f"saída medida {medida}" if medida else f"saída {familia} não medida")
                         + f", esperada {quer} do perfil {esperada.profile_name}")
    return falta


def saida_confere(esperada: SaidaEsperada | None, ipv4: str | None, ipv6: str | None) -> bool | None:
    """`None` = não há o que comparar (perfil sem saída esperada, ou nenhuma saída medida ainda)."""
    if esperada is None or not (ipv4 or ipv6):
        return None
    return not saida_divergente(esperada, ipv4, ipv6)


# ============================================================================ linhas → DTOs
def _perfil_dto(row: Row) -> NetworkProfileDTO:
    # A referência do cofre fica aqui dentro: o DTO só sabe SE há segredo.
    return NetworkProfileDTO(id=row["id"], name=row["name"], kind=row["kind"], protocol=row["protocol"],
                             endpoint_host=row["endpoint_host"], endpoint_port=int(row["endpoint_port"]),
                             has_secret=bool(row["secret_ref"]), params=loads(row["params"], {}) or {},
                             created_at=row["created_at"], created_by=row["created_by"])


def _aparelho_dto(row: Row) -> DeviceNetworkDTO:
    return DeviceNetworkDTO.model_validate({k: row[k] for k in DeviceNetworkDTO.model_fields})


def _bool(v: object) -> bool | None:
    return None if v is None else bool(v)


def _medicao_dto(row: Row) -> NetworkMeasurementDTO:
    return NetworkMeasurementDTO(id=int(row["id"]), instance_id=row["instance_id"], measured_at=row["measured_at"],
                                 method=row["method"], egress_ipv4=row["egress_ipv4"], egress_ipv6=row["egress_ipv6"],
                                 dns_resolver=row["dns_resolver"], udp_ok=_bool(row["udp_ok"]),
                                 per_app=loads(row["per_app"], {}) or {}, leak_blocked=_bool(row["leak_blocked"]),
                                 detail=row["detail"])


def _medicao_da_listagem(row: Row) -> dict[str, object]:
    """A medição como a listagem a mostra: o DTO mais as duas pernas de UDP (item 29.5), DERIVADAS do `detail` — a
    tabela guarda só `udp_ok` (o E das duas), e a sonda escreve cada perna num formato estável (`sonda_rede.pernas_udp`
    lê de volta). `None` = o `detail` não diz (medição de outro método, ou cortada antes do trecho)."""
    pernas = pernas_udp(row["detail"])
    return {**_medicao_dto(row).model_dump(),
            "udp_dns_ok": pernas["dns"]["ok"] if pernas is not None else None,
            "udp_ntp_ok": pernas["ntp"]["ok"] if pernas is not None else None}


def _linha(st: AppState, instance_id: str) -> Row | None:
    return st.db.one("SELECT * FROM device_network WHERE instance_id=?", (instance_id,))


def _linha_certa(st: AppState, instance_id: str) -> Row:
    row = _linha(st, instance_id)
    if row is None:
        raise RedeError(409, "nothing_requested", f"Nenhuma rede foi pedida para {instance_id}.")
    return row


def _emitir(st: AppState, mensagem: str, *, instance_id: str | None = None, **data: object) -> None:
    """`network.updated`: o painel relê. Só ids, política, revisão e estado — nunca segredo nem `secret_ref`."""
    st.bus.emit("network.updated", mensagem, instance_id=instance_id, data={"instance_id": instance_id, **data})


# ============================================================================ perfis
def _em_uso(st: AppState, profile_id: str) -> list[str]:
    return sorted(str(r["instance_id"]) for r in st.db.query(
        "SELECT instance_id FROM device_network WHERE vpn_profile_id=? OR proxy_profile_id=?",
        (profile_id, profile_id)))


def listar_perfis(st: AppState) -> dict[str, list[dict[str, object]]]:
    """Cada perfil (sem segredo) com os aparelhos que o pedem (`in_use`): é o que o painel precisa para o 409."""
    return {"profiles": [{**_perfil_dto(row).model_dump(), "in_use": _em_uso(st, str(row["id"]))}
                         for row in st.db.query("SELECT * FROM network_profiles ORDER BY name")]}


def criar_perfil(st: AppState, body: NetworkProfileInput, quem: str | None) -> NetworkProfileDTO:
    """Grava o perfil e, se houver, o segredo no cofre — os dois na MESMA transação: um INSERT recusado não deixa
    texto cifrado órfão, e um cofre fechado não deixa perfil sem a chave que ele anuncia ter."""
    if st.db.one("SELECT id FROM network_profiles WHERE name=?", (body.name,)) is not None:
        raise RedeError(409, "name_taken", f"Já existe um perfil de rede chamado '{body.name}'.")
    pid = novo_id_de_app(st.db, f"{body.kind} {body.name}", tabela="network_profiles")
    try:
        with st.db.tx():
            ref = st.secrets.store_secret(body.secret.get_secret_value()) if body.secret is not None else None
            st.db.execute("INSERT INTO network_profiles(id, name, kind, protocol, endpoint_host, endpoint_port,"
                          " secret_ref, params, created_at, created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (pid, body.name, body.kind, body.protocol, body.endpoint_host, body.endpoint_port, ref,
                           dumps(body.params), now_iso(), quem))
    except (SecretStoreLocked, SecretStoreUnavailable) as exc:
        # A mensagem do cofre fala da CHAVE MESTRA, nunca do valor; o perfil não foi gravado.
        raise RedeError(503, "secret_store_unavailable", f"O cofre não guardou o segredo: {exc}") from None
    row = st.db.one("SELECT * FROM network_profiles WHERE id=?", (pid,))
    assert row is not None
    dto = _perfil_dto(row)
    _emitir(st, f"Rede: perfil {dto.name} ({dto.kind}/{dto.protocol}) cadastrado", profile_id=pid,
            acao="perfil_criado")
    return dto


def remover_perfil(st: AppState, profile_id: str) -> None:
    """Perfil em uso é recusado (409) com os aparelhos, em vez de deixar aparelho com rede que ninguém sabe qual é.
    A 057 também recusa (FK sem `ON DELETE`); a pergunta aqui é só para a resposta dizer QUEM usa."""
    row = st.db.one("SELECT * FROM network_profiles WHERE id=?", (profile_id,))
    if row is None:
        raise RedeError(404, "not_found", "Perfil de rede não encontrado.")
    em_uso = _em_uso(st, profile_id)
    if em_uso:
        raise RedeError(409, "network_profile_in_use", f"O perfil '{row['name']}' está pedido para "
                        + ", ".join(em_uso) + ". Troque ou tire o perfil desses aparelhos antes de apagá-lo.",
                        instance_ids=em_uso)
    with st.db.tx():
        st.db.execute("DELETE FROM network_profiles WHERE id=?", (profile_id,))
        if row["secret_ref"]:
            st.secrets.delete_secret(str(row["secret_ref"]))
    _emitir(st, f"Rede: perfil {row['name']} apagado", profile_id=profile_id, acao="perfil_apagado")


# ============================================================================ quem pode receber
def _conta_real(st: AppState, instance_id: str) -> str | None:
    """As contas vinculadas ao aparelho (vínculo ativo), ou `None`. A mesma regra do reparo em escada do ADR-055
    (`despacho.remediar_reiniciando`): conta vinculada é conta real logada, até prova em contrário.

    Uma linha por persona, com os apps dos vínculos ao lado (29.142): desde a 051 o vínculo é por app, e a mesma
    persona ligada para dois apps saía repetida ("@x, @x"). Vínculo sem app não acrescenta nada ao nome."""
    contas: dict[str, list[str]] = {}
    for b in st.social_repo.profiles_of_instance(instance_id):
        perfil = st.social_repo.profile_row(str(b["profile_id"]))
        apps = contas.setdefault(f"@{perfil['username']}" if perfil is not None else str(b["profile_id"]), [])
        if b["app_id"]:
            nome_do_app = str(st.db.scalar("SELECT name FROM apps WHERE id=?", (b["app_id"],)) or b["app_id"])
            if nome_do_app not in apps:
                apps.append(nome_do_app)
    return ", ".join(f"{nome} ({', '.join(apps)})" if apps else nome for nome, apps in contas.items()) or None


def apps_exigidos(st: AppState, instance_id: str) -> list[str]:
    """Pacotes que uma medição tem de cobrir para o aparelho chegar a `trafego_verificado`: os apps das contas das
    personas vinculadas a ele (vínculo ativo; o vínculo com `app_id` restringe àquele app). É o "por app" do
    ADR-056 §3 em forma de lista: medir o navegador não prova o Instagram nem o Outlook.

    Lista vazia (aparelho sem conta vinculada) cai na regra mínima de `_falta_para_verificar`: pelo menos um app
    medido. O vínculo feito DEPOIS da verificação não desfaz o estado aqui; a porta da tarefa (25.6) confere a lista
    de hoje contra o `per_app` da medição que verificou (`apps_sem_prova`) e manda medir de novo."""
    return [str(r["package"]) for r in st.db.query(
        "SELECT DISTINCT ap.package FROM device_profile_bindings b"
        " JOIN profile_accounts a ON a.profile_id = b.profile_id AND (b.app_id IS NULL OR a.app_id = b.app_id)"
        " JOIN apps ap ON ap.id = a.app_id WHERE b.instance_id=? AND b.active=1 ORDER BY ap.package",
        (instance_id,))]


@dataclass
class _Legado:
    """O proxy global da 041 no aparelho, rebaixado: `applied` vale `configurado` NO MÁXIMO (ADR-056 §2, T5)."""

    proxy_id: str | None
    name: str | None
    value: str | None
    state: str
    observed_value: str | None
    verified_at: str | None
    detail: str | None

    @property
    def efetivo(self) -> NetworkState | None:
        if self.state == "applied":
            # Releitura do `settings` prova a configuração, não o tráfego: nunca passa de `configurado`. Aplicado
            # SEM proxy (tirado) é aparelho sem proxy legado, e não tem estado de rede nenhum a mostrar.
            return "configurado" if self.proxy_id else None
        return "pendente"                       # pending, applying ou failed: o aparelho não confirmou o pedido

    def como_dict(self) -> dict[str, object]:
        return {"proxy_id": self.proxy_id, "name": self.name, "value": self.value, "state": self.state,
                "observed_value": self.observed_value, "verified_at": self.verified_at, "detail": self.detail,
                "effective_state": self.efetivo}


def _legado(st: AppState, instance_id: str) -> _Legado | None:
    row = st.db.one("SELECT * FROM device_proxy_state WHERE instance_id=?", (instance_id,))
    if row is None:
        return None
    perfil = (st.db.one("SELECT name, host, port FROM proxy_profiles WHERE id=?", (row["desired_proxy_id"],))
              if row["desired_proxy_id"] else None)
    return _Legado(proxy_id=row["desired_proxy_id"], name=perfil["name"] if perfil else None,
                   value=f"{perfil['host']}:{perfil['port']}" if perfil else None, state=str(row["state"]),
                   observed_value=row["observed_value"], verified_at=row["verified_at"], detail=row["detail"])


def idade_da_verificacao(row: Row, agora: float | None = None) -> float | None:
    """Segundos desde a última medição que gravou a saída (`verified_at`); `None` quando não há data legível."""
    if not row["verified_at"]:
        return None
    try:
        quando = parse_iso(str(row["verified_at"])).timestamp()
    except (ValueError, TypeError):
        return None
    return (time.time() if agora is None else agora) - quando


def verificacao_vencida(row: Row, validade_s: float, agora: float | None = None) -> bool:
    """Um `trafego_verificado` com política exigida que já não vale para a porta da tarefa (item 25.6): sem data da
    medição, ou com ela mais velha que `rede.validade_verificacao_s`. Com política `livre` nada depende da rede, e
    nada vence. Fora de `trafego_verificado` a pergunta não se aplica (o estado já diz o que falta)."""
    if row["state"] != "trafego_verificado" or row["policy"] == "livre":
        return False
    idade = idade_da_verificacao(row, agora)
    return idade is None or idade >= validade_s


def inicio_do_boot(st: AppState, instance_id: str) -> float | None:
    """Quando o Android deste aparelho começou a subir pela última vez (epoch), ou `None` se não se sabe (item 29.22).

    A fonte é `instances.emulator_started_at`: o instante em que o processo do emulador nasceu (boot a frio OU acordar
    do snapshot), que sobrevive ao restart do backend — por isso um reinício do central não invalida a verificação
    de quem não rebootou. Aparelho sem processo local (worker remoto, físico) cai na entrada em `online` que este
    backend viu (`online_since_mono`): ali o restart do central conta como entrada nova, e o custo é uma medição a
    mais, o lado seguro. Aparelho que não está no ar e sem processo: sem marco (nada a invalidar; ao entrar no ar
    o marco passa a existir)."""
    iniciado = st.db.scalar("SELECT emulator_started_at FROM instances WHERE id=?", (instance_id,))
    if iniciado:
        try:
            return parse_iso(str(iniciado)).timestamp()
        except (ValueError, TypeError):
            pass
    rt = st.devices.devices.get(instance_id)
    if rt is None or rt.state != "online":
        return None
    return time.time() - max(0.0, time.monotonic() - rt.online_since_mono)


def boot_depois_da_medicao(st: AppState, row: Row) -> bool:
    """O aparelho subiu DEPOIS da medição que verificou a linha (item 29.22). A prova de tráfego era do Android de
    antes: túnel, DNS e regras de bloqueio são refeitos a cada boot, e o `trafego_verificado` não pode atravessá-lo
    (android-05, 02/10: verificado às 19:20, desligado e ligado a frio às 19:50, a porta liberou a tarefa às 19:52
    com o aparelho acusando "sem internet: DNS não responde"). Sem data legível da medição, `vencida` já responde."""
    if row["state"] != "trafego_verificado" or row["policy"] == "livre" or not row["verified_at"]:
        return False
    try:
        medido = parse_iso(str(row["verified_at"])).timestamp()
    except (ValueError, TypeError):
        return False
    boot = inicio_do_boot(st, str(row["instance_id"]))
    return boot is not None and boot > medido


def apps_sem_prova(st: AppState, row: Row) -> list[str]:
    """Apps exigidos HOJE (`apps_exigidos`) que a medição que verificou o aparelho não mediu (`ok`, ou `sem_trafego`,
    que não segura desde o 29.44, no `per_app`).
    É o vínculo feito depois da verificação: uma conta nova no aparelho não desfaz o `trafego_verificado` (o estado
    é da medição), mas a porta da tarefa não o aceita para aquele app sem medir de novo (item 25.6).

    A medição que verificou é a da data de `verified_at` (só medição com IP a grava, e uma com IP que não verificasse
    teria levado a linha a `parcial`). Com política `livre`, fora de `trafego_verificado` ou sem app exigido: nada."""
    if row["state"] != "trafego_verificado" or row["policy"] == "livre":
        return []
    exigidos = apps_exigidos(st, str(row["instance_id"]))
    if not exigidos:
        return []
    m = st.db.one("SELECT per_app FROM network_measurements WHERE instance_id=? AND measured_at=?"
                  " ORDER BY id DESC LIMIT 1", (row["instance_id"], row["verified_at"])) if row["verified_at"] else None
    por_app = (loads(m["per_app"], {}) or {}) if m is not None else {}
    return [app for app in exigidos if por_app.get(app) not in ("ok", SEM_TRAFEGO)]


def verificacao_invalida(st: AppState, row: Row) -> str | None:
    """Por que um `trafego_verificado` com política exigida não vale para a porta da tarefa (item 25.6): `vencida`
    (`rede.validade_verificacao_s`), `boot` (o aparelho subiu depois da medição, item 29.22), `bloqueio` (política com
    bloqueio sem prova de vazamento que valha para a revisão, item 29.2) ou `apps` (`apps_sem_prova`). `None` = vale
    (ou a pergunta não se aplica)."""
    if verificacao_vencida(row, _validade(st)):
        return "vencida"
    if boot_depois_da_medicao(st, row):
        return "boot"
    if row["state"] == "trafego_verificado" and not bloqueio_provado(row):
        return "bloqueio"
    if apps_sem_prova(st, row):
        return "apps"
    return None


def _pendencia(row: Row | None, st: AppState | None = None) -> str | None:
    """O que falta a este aparelho: `aplicar` (a revisão pedida não está no aparelho, ou regrediu a `pendente` por
    falha ou deriva) ou `verificar` (está, mas o tráfego não foi medido por app; ou, com política exigida, a medição
    venceu ou não cobre um app exigido hoje). É a pendência que o painel mostra; a convergência do 25.4 decide pelo
    `state` da linha (`configurado` ainda pede o reinício e a conexão)."""
    if row is None:
        return None
    if (row["applied_rev"] is None or int(row["applied_rev"]) < int(row["desired_rev"])
            or row["state"] == "pendente"):
        return "aplicar"
    if row["state"] != "trafego_verificado":
        return "verificar"
    if st is not None and verificacao_invalida(st, row) is not None:
        return "verificar"
    return None


def _validade(st: AppState) -> float:
    return float(st.cfg.file.rede.validade_verificacao_s)


def pendencias(st: AppState) -> list[dict[str, object]]:
    """Aparelhos com trabalho de rede a fazer, na ordem do id: a visão de conjunto (painel, matriz do 25.9). A
    convergência (`rede_convergencia.py`) decide aparelho a aparelho, pelo estado da linha, e age pelo comando
    `device.network`. A loja e o aparelho em quarentena não aparecem: nada toca neles (ADR-055, ADR-056 §7)."""
    saida: list[dict[str, object]] = []
    for row in st.db.query("SELECT * FROM device_network ORDER BY instance_id"):
        rt = st.devices.devices.get(str(row["instance_id"]))
        if rt is None or rt.store or st.quarentena(rt.id) is not None:
            continue
        falta = _pendencia(row, st)
        if falta is not None:
            saida.append({"instance_id": row["instance_id"], "falta": falta, "desired_rev": int(row["desired_rev"]),
                          "applied_rev": row["applied_rev"], "state": row["state"]})
    return saida


def listar_aparelhos(st: AppState) -> dict[str, object]:
    """Desejado × observado de cada aparelho do parque (a loja fica de fora: não é destino), e no topo a saída medida
    do próprio central (`central_egress`, item 29.20), com a qual cada aparelho é comparado em `egress_home`."""
    linhas = {str(r["instance_id"]): r for r in st.db.query("SELECT * FROM device_network")}
    ultimas: dict[str, Row] = {}
    for m in st.db.query("SELECT * FROM network_measurements ORDER BY measured_at, id"):
        ultimas[str(m["instance_id"])] = m                              # a última de cada aparelho fica
    perfis = {str(p["id"]): p for p in st.db.query("SELECT id, name, protocol, params FROM network_profiles")}
    central = st.rede_saida_central.atual()
    sondas = _sondas_sem_rede(st)
    aparelhos: list[dict[str, object]] = []
    for rt in sorted(st.devices.devices.values(), key=lambda r: r.id):
        if rt.store:
            continue
        row = linhas.get(rt.id)
        legado = _legado(st, rt.id)
        rede = _aparelho_dto(row) if row is not None else None
        efetivo = rede.state if rede is not None else (legado.efetivo if legado is not None else None)
        medicao = ultimas.get(rt.id)
        # A comparação entre aparelhos (ADR-056 §1): quem mais tem a MESMA última saída medida. Aviso, não bloqueio.
        iguais = sorted(outro for outro, r in linhas.items() if outro != rt.id and row is not None and (
            (row["egress_ipv4"] and r["egress_ipv4"] == row["egress_ipv4"])
            or (row["egress_ipv6"] and r["egress_ipv6"] == row["egress_ipv6"])))
        # A saída que o perfil da saída final declara × a última medida (item 29.6). Outra pergunta que a de cima: um
        # aparelho pode medir a própria saída esperada e ainda dividi-la com outro, e o contrário.
        esperada = (_esperada_do_perfil(perfis.get(perfil_da_saida(row["vpn_profile_id"], row["proxy_profile_id"])
                                                   or "")) if row is not None else None)
        aparelhos.append({
            "instance_id": rt.id, "worker_id": rt.worker_id, "external": rt.external,
            "device_state": rt.state.value,
            "network": rede.model_dump() if rede is not None else None,
            "effective_state": efetivo,
            "legacy_proxy": legado.como_dict() if legado is not None else None,
            "restriction": st.quarentena(rt.id),
            "real_account": _conta_real(st, rt.id),
            "required_apps": apps_exigidos(st, rt.id),
            "pending": _pendencia(row, st),
            "last_measurement": _medicao_da_listagem(medicao) if medicao is not None else None,
            "egress_shared_with": iguais,
            "egress_expected": esperada.como_dict() if esperada is not None else None,
            # Só com a revisão pedida MEDIDA: antes disso a saída da linha é a de um pedido anterior, e compará-la
            # com a esperada do perfil novo acusaria uma diferença que ainda não foi medida.
            "egress_matches": (saida_confere(esperada, row["egress_ipv4"], row["egress_ipv6"])
                               if row is not None and row["state"] in _MEDIDOS else None),
            "egress_home": _saida_pela_casa(row, perfis, central, sondas.get(rt.id), _validade_da_sonda(st)),
        })
    return {"devices": aparelhos, "central_egress": central}


#: O `method` da sonda de IP do aparelho SEM rede pedida (item 29.20) em `network_measurements` (≤ 60): é o que separa
#: esta medida das da rede pedida, e a convergência nunca a trata como uma (não há linha em `device_network`).
METODO_SEM_REDE = "sonda de IP sem rede pedida (uid 2000)"
#: Quantos `reverificar_s` uma medida de aparelho sem rede vale: depois disso o aparelho volta a "presumido".
_VALIDADE_SEM_REDE_EM_VEZES = 3


def _validade_da_sonda(st: AppState) -> float:
    return float(st.cfg.file.rede.sonda.reverificar_s) * _VALIDADE_SEM_REDE_EM_VEZES


def sem_rede_pedida(row: Row | None) -> bool:
    """Sem rede pedida: sem linha em `device_network`, ou linha de pedido vazio (tirou tudo)."""
    return row is None or not (row["vpn_profile_id"] or row["proxy_profile_id"])


def registrar_saida_sem_rede(st: AppState, instance_id: str, ipv4: str | None, ipv6: str | None,
                             detalhe: str) -> int | None:
    """Acrescenta ao histórico a sonda de IP de um aparelho SEM rede pedida (29.20) e devolve o id. É só leitura da
    saída: não cria linha em `device_network`, não muda estado, política nem revisão, não emite aviso. Se o aparelho
    ganhou uma rede pedida no meio da sonda, a medida se perde (`None`): a saída dele passou a ser da rede pedida, e a
    medição dela é a da convergência. Falha de sonda (sem IP) entra também, com o motivo no `detalhe`: é o que diz "não
    medida" e espaça a próxima tentativa, em vez de repetir a sonda sem parar."""
    if not sem_rede_pedida(_linha(st, instance_id)):
        return None
    medicao = NetworkMeasurementInput(method=METODO_SEM_REDE, egress_ipv4=ipv4, egress_ipv6=ipv6, detail=detalhe[:500])
    return int(st.db.inserted_id(
        "INSERT INTO network_measurements(instance_id, measured_at, method, egress_ipv4, egress_ipv6, dns_resolver,"
        " udp_ok, per_app, leak_blocked, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (instance_id, medicao.measured_at or now_iso(), METODO_SEM_REDE, medicao.egress_ipv4, medicao.egress_ipv6, None,
         None, dumps({}), None, medicao.detail)))


def sonda_sem_rede_devida(st: AppState, instance_id: str, agora: float) -> bool:
    """Já é hora de medir de novo a saída deste aparelho sem rede? A última tentativa (com ou sem IP) passou de
    `reverificar_s`, ou, se falhou, de 5 minutos (nunca mais que `reverificar_s`). Nunca medido: sim."""
    ultima = st.db.one("SELECT measured_at, egress_ipv4, egress_ipv6 FROM network_measurements"
                       " WHERE instance_id=? AND method=? ORDER BY id DESC LIMIT 1", (instance_id, METODO_SEM_REDE))
    quando = parse_iso(str(ultima["measured_at"])) if ultima is not None else None
    if ultima is None or quando is None:
        return True
    reverificar = float(st.cfg.file.rede.sonda.reverificar_s)
    espera = reverificar if (ultima["egress_ipv4"] or ultima["egress_ipv6"]) else min(300.0, reverificar)
    return agora - quando.timestamp() >= espera


def _sondas_sem_rede(st: AppState) -> dict[str, tuple[Row | None, Row | None]]:
    """Por aparelho, `(a última tentativa, a última COM IP)` da sonda de IP sem rede pedida."""
    por: dict[str, tuple[Row | None, Row | None]] = {}
    for m in st.db.query("SELECT * FROM network_measurements WHERE method=? ORDER BY measured_at, id",
                         (METODO_SEM_REDE,)):
        iid = str(m["instance_id"])
        _, com_ip = por.get(iid, (None, None))
        por[iid] = (m, m if (m["egress_ipv4"] or m["egress_ipv6"]) else com_ip)
    return por


def _saida_sem_rede(sonda: tuple[Row | None, Row | None] | None, central: dict[str, object],
                    validade_s: float) -> dict[str, object]:
    """O veredito do aparelho SEM rede pedida: a sonda de IP (a última com IP, enquanto vale) contra o central, e
    PRESUMIDO onde não houver medida que valha (item 29.20). A falha só conta se foi a tentativa mais recente."""
    ultima, com_ip = sonda if sonda is not None else (None, None)
    quando = parse_iso(str(com_ip["measured_at"])) if com_ip is not None else None
    valida = com_ip is not None and quando is not None and time.time() - quando.timestamp() <= validade_s
    falha = None
    if ultima is not None and not (ultima["egress_ipv4"] or ultima["egress_ipv6"]):
        falha = str(ultima["detail"] or "sem IP")
    medida = com_ip if valida else None
    v = veredito_sem_rede(ipv4=medida["egress_ipv4"] if medida else None,
                          ipv6=medida["egress_ipv6"] if medida else None, central=central, falha=falha)
    out = v.como_dict()
    out["measured"] = ({"ipv4": medida["egress_ipv4"], "ipv6": medida["egress_ipv6"],
                        "measured_at": medida["measured_at"], "source": "probe_no_network"} if medida else None)
    return out


def _saida_pela_casa(row: Row | None, perfis: dict[str, Row], central: dict[str, object],
                     sonda: tuple[Row | None, Row | None] | None = None,
                     validade_s: float = 1800.0) -> dict[str, object]:
    """Este aparelho ainda sai pela casa (item 29.20)? A saída medida dele (`device_network.egress_*`, a mesma fonte
    de `egress_shared_with` e `egress_matches`) contra a do central, e o IPv6 medido contra o que o perfil de VPN leva.
    O IPv6 pergunta pelo TÚNEL: com um proxy por cima, o IPv6 também depende de a VPN o carregar, então a regra olha
    sempre o perfil de VPN (sem VPN, o perfil não leva IPv6 e o IPv6 medido sai direto).

    Sem rede pedida (sem linha, ou linha de pedido vazio) o aparelho sai pela casa por DEFINIÇÃO: é "presumido" até a
    sonda de IP medir (`_saida_sem_rede`). `measured` traz os IPs em que o veredito se apoia; `basis` diz se é
    "measured" ou "presumed"."""
    if row is None or sem_rede_pedida(row):
        return _saida_sem_rede(sonda, central, validade_s)
    vpn = perfis.get(str(row["vpn_profile_id"] or ""))
    leva = perfil_leva_ipv6(loads(vpn["params"], {}) or {}, str(vpn["protocol"])) if vpn is not None else False
    out = veredito(medido=row["state"] in _MEDIDOS, ipv4=row["egress_ipv4"], ipv6=row["egress_ipv6"],
                   central=central, leva_ipv6=leva, com_rede_pedida=True).como_dict()
    out["measured"] = ({"ipv4": row["egress_ipv4"], "ipv6": row["egress_ipv6"], "measured_at": row["verified_at"],
                        "source": "device_network"} if (row["egress_ipv4"] or row["egress_ipv6"]) else None)
    return out


# ============================================================================ atribuir
@dataclass(frozen=True)
class _Desejo:
    vpn_profile_id: str | None
    proxy_profile_id: str | None
    policy: str

    @property
    def config_do_aparelho(self) -> tuple[str | None, str | None, bool]:
        """O que o APARELHO recebe: os perfis e o bloqueio de conexões fora da VPN. `livre` ↔ `exigida` é regra
        do scheduler, não configuração do Android: trocar só isso não desfaz a verificação nem pede reaplicar."""
        return self.vpn_profile_id, self.proxy_profile_id, self.policy == "exigida_com_bloqueio"

    @property
    def vazio(self) -> bool:
        return not self.vpn_profile_id and not self.proxy_profile_id and self.policy == "livre"

    def descrever(self) -> str:
        return ", ".join([f"VPN {self.vpn_profile_id}" if self.vpn_profile_id else "sem VPN",
                          f"proxy {self.proxy_profile_id}" if self.proxy_profile_id else "sem proxy",
                          f"política {self.policy}"])

    def como_dict(self) -> dict[str, object]:
        return {"vpn_profile_id": self.vpn_profile_id, "proxy_profile_id": self.proxy_profile_id,
                "policy": self.policy}


@dataclass
class _Item:
    """Uma linha da prévia do lote: de onde, para onde, se a configuração do aparelho muda e o desfecho."""

    id: str
    de: _Desejo
    para: _Desejo
    outcome: str = ""
    code: str | None = None
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    #: Avisos sobre a SAÍDA esperada (item 29.6), cada um com `code` e `message`: nunca recusam.
    egress_warnings: list[dict[str, object]] = field(default_factory=list)

    @property
    def reapply(self) -> bool:
        return self.de.config_do_aparelho != self.para.config_do_aparelho

    @property
    def pode_gravar(self) -> bool:
        """O pedido deste item ainda pode valer: não foi recusado, ou só falta a confirmação da conta real (a prévia
        a devolve como recusa, e o painel a pede em seguida)."""
        return self.outcome != "refused" or self.code == "real_account_confirm_required"

    def recusar(self, code: str, reason: str) -> None:
        self.outcome, self.code, self.reason = "refused", code, reason

    def como_dict(self) -> dict[str, object]:
        saida: dict[str, object] = {"id": self.id, "outcome": self.outcome, "reason": self.reason,
                                    "from": self.de.como_dict(), "to": self.para.como_dict(),
                                    "reapply": self.reapply, "warnings": self.warnings,
                                    "egress_warnings": self.egress_warnings}
        if self.code is not None:
            saida["code"] = self.code
        return saida


def _perfil_do_tipo(st: AppState, profile_id: str | None, kind: str) -> None:
    if profile_id is None:
        return
    row = st.db.one("SELECT name, kind FROM network_profiles WHERE id=?", (profile_id,))
    if row is None:
        raise RedeError(404, "network_profile_not_found", f"Perfil de rede '{profile_id}' não encontrado.")
    if row["kind"] != kind:
        raise RedeError(400, "wrong_profile_kind", f"'{row['name']}' é um perfil '{row['kind']}', e o campo pede um "
                                                   f"perfil '{kind}'.")


def _desejo_atual(row: Row | None) -> _Desejo:
    if row is None:
        return _Desejo(None, None, "livre")
    return _Desejo(row["vpn_profile_id"], row["proxy_profile_id"], str(row["policy"]))


def _julgar(st: AppState, item: _Item, confirmados: set[str], dry_run: bool) -> None:
    """Decide o desfecho de um aparelho do lote. A ordem importa: loja e quarentena recusam sempre (nada toca
    neles); depois a coerência do pedido; e a conta real só pesa quando a SAÍDA muda."""
    rt = st.devices.devices[item.id]
    quarentena = st.quarentena(item.id)
    conta = _conta_real(st, item.id)
    para = item.para
    if rt.store:
        item.recusar("store_instance", f"{item.id} é a loja (Play Store): não recebe rede gerenciada (ADR-056 §7)")
    elif quarentena is not None:
        item.recusar("aparelho_em_quarentena", quarentena)
    elif para.policy != "livre" and not (para.vpn_profile_id or para.proxy_profile_id):
        item.recusar("policy_without_profile", f"política '{para.policy}' sem perfil: a tarefa esperaria para "
                                               "sempre por uma rede que ninguém pediu")
    elif para.policy == "exigida_com_bloqueio" and not para.vpn_profile_id:
        item.recusar("policy_without_vpn", "'exigida_com_bloqueio' bloqueia o que sai fora da VPN: precisa de um "
                                           "perfil de VPN")
    elif para == item.de:
        item.outcome, item.reason = "unchanged", "já é o pedido"
    elif item.reapply and conta and item.id not in confirmados:
        item.recusar("real_account_confirm_required",
                     f"{item.id} tem conta real vinculada ({conta}): mudar a saída de uma conta logada pede a "
                     "confirmação da pessoa para ESTE aparelho (confirm_real_account; ADR-056 §7)")
    else:
        item.outcome = "would_assign" if dry_run else "assigned"
        item.reason = ("configuração nova: volta a pendente até a aplicação e a medição no aparelho" if item.reapply
                       else "só a política muda; a configuração do aparelho fica")
        if conta:
            item.warnings.append(f"conta real vinculada ({conta}), confirmada pela pessoa neste pedido")
    legado = _legado(st, item.id)
    if legado is not None and legado.proxy_id and legado.state == "applied":
        item.warnings.append(f"o proxy global legado {legado.name} ({legado.value}) está gravado no aparelho e "
                             "continua lá até ser tirado na aba Proxy")


def _avisar_da_saida(st: AppState, previa: list[_Item]) -> None:
    """Os avisos de SAÍDA da prévia (item 29.6), em `egress_warnings` de cada item. Só aviso: a recusa que protege a
    conta real é a de sempre (`real_account_confirm_required`, por aparelho), e ela não muda.

    - `saida_dedicada_compartilhada`: o perfil que dá a saída final do aparelho declara uma saída esperada e, com este
      pedido, fica em mais de um aparelho — contando os do lote e os que já o têm. Um perfil `wireguard` externo leva
      UMA chave e serve a um aparelho por vez; dois aparelhos nele são a mesma saída (e o mesmo par no servidor);
    - `saida_dedicada_trocada_por_compartilhada`: o aparelho sai de um perfil com saída esperada para um sem (ou para
      perfil nenhum). É a troca de IP que derruba conta (K-057), e numa reatribuição comum ela seria silenciosa."""
    perfis = {str(p["id"]): p for p in st.db.query("SELECT id, name, params FROM network_profiles")}
    no_lote = {item.id for item in previa}
    # Quem fica com cada perfil de saída DEPOIS do pedido: os do lote pelo pedido (o recusado de vez fica como está),
    # os outros pela linha de hoje.
    destino: dict[str, set[str]] = {}
    for item in previa:
        alvo = item.para if item.pode_gravar else item.de
        pid = perfil_da_saida(alvo.vpn_profile_id, alvo.proxy_profile_id)
        if pid is not None:
            destino.setdefault(pid, set()).add(item.id)
    for r in st.db.query("SELECT instance_id, vpn_profile_id, proxy_profile_id FROM device_network"):
        pid = perfil_da_saida(r["vpn_profile_id"], r["proxy_profile_id"])
        if pid is not None and str(r["instance_id"]) not in no_lote:
            destino.setdefault(str(pid), set()).add(str(r["instance_id"]))
    for item in previa:
        if not item.pode_gravar:
            continue
        pid_de = perfil_da_saida(item.de.vpn_profile_id, item.de.proxy_profile_id)
        pid_para = perfil_da_saida(item.para.vpn_profile_id, item.para.proxy_profile_id)
        antiga, nova = _esperada_do_perfil(perfis.get(pid_de or "")), _esperada_do_perfil(perfis.get(pid_para or ""))
        if nova is not None:
            outros = sorted(destino.get(nova.profile_id, set()) - {item.id})
            if outros:
                item.egress_warnings.append({
                    "code": "saida_dedicada_compartilhada", "profile_id": nova.profile_id, "shared_with": outros,
                    "message": f"o perfil {nova.profile_name} declara a saída {nova.descrever()} e, com este pedido, "
                               f"fica também em {', '.join(outros)}: a saída dedicada passa a ser compartilhada"})
        if antiga is not None and nova is None and pid_para != pid_de:
            novo_perfil = perfis.get(pid_para or "")
            para_onde = (f"o perfil {novo_perfil['name']}, que não declara saída esperada" if novo_perfil is not None
                         else "sem perfil de rede (a saída direta)")
            item.egress_warnings.append({
                "code": "saida_dedicada_trocada_por_compartilhada", "profile_id": antiga.profile_id,
                "message": f"{item.id} deixa a saída dedicada {antiga.descrever()} do perfil {antiga.profile_name} e "
                           f"passa para {para_onde}: o IP de saída muda, e a saída nova pode ser a de outros "
                           "aparelhos"})


def atribuir(st: AppState, body: NetworkAssignBody, quem: str | None) -> dict[str, object]:
    """Pede a rede para os aparelhos. Com `dry_run`, só a prévia; sem ele, tudo ou nada: uma recusa (loja,
    quarentena, conta real sem confirmação, política sem perfil) devolve 409 com a prévia inteira e nada é gravado.
    Atribuir nunca AVANÇA o estado: quem muda a configuração do aparelho volta a `pendente` e ganha revisão nova."""
    campos = body.model_fields_set
    if not ({"vpn_profile_id", "proxy_profile_id"} & campos) and body.policy is None:
        raise RedeError(400, "nothing_to_change", "Diga o que muda: `vpn_profile_id`, `proxy_profile_id` ou `policy`.")
    _perfil_do_tipo(st, body.vpn_profile_id, "vpn")
    _perfil_do_tipo(st, body.proxy_profile_id, "proxy")
    pedidos = list(dict.fromkeys(body.instance_ids))
    fora = [i for i in pedidos if i not in st.devices.devices]
    if fora:
        raise RedeError(400, "unknown_instance", "Aparelho(s) fora do parque: " + ", ".join(fora) + ".")

    previa: list[_Item] = []
    for iid in pedidos:
        de = _desejo_atual(_linha(st, iid))
        para = _Desejo(body.vpn_profile_id if "vpn_profile_id" in campos else de.vpn_profile_id,
                       body.proxy_profile_id if "proxy_profile_id" in campos else de.proxy_profile_id,
                       body.policy if body.policy is not None else de.policy)
        item = _Item(id=iid, de=de, para=para)
        _julgar(st, item, set(body.confirm_real_account), body.dry_run)
        previa.append(item)
    _avisar_da_saida(st, previa)

    if body.dry_run:
        return {"accepted": False, "dry_run": True, "devices": [i.como_dict() for i in previa]}
    recusas = [i for i in previa if i.outcome == "refused"]
    if recusas:
        codigos = {i.code for i in recusas}
        codigo = codigos.pop() if len(codigos) == 1 else None
        raise RedeError(409, codigo or "assign_refused",
                        "Nada foi gravado: " + "; ".join(f"{i.id}: {i.reason}" for i in recusas) + ".",
                        devices=[i.como_dict() for i in previa])

    agora = now_iso()
    mudados = [i for i in previa if i.outcome == "assigned"]
    with st.db.tx():
        for item in mudados:
            _gravar_desejado(st, item, quem, agora)
    for item in mudados:
        row = _linha(st, item.id)
        _emitir(st, f"Rede de {item.id}: pedida {item.para.descrever()}", instance_id=item.id, acao="assign",
                **item.para.como_dict(), desired_rev=int(row["desired_rev"]) if row else None,
                applied_rev=row["applied_rev"] if row else None, state=row["state"] if row else None)
    return {"accepted": True, "dry_run": False, "devices": [i.como_dict() for i in previa]}


def _gravar_desejado(st: AppState, item: _Item, quem: str | None, agora: str) -> None:
    iid, para = item.id, item.para
    row = _linha(st, iid)
    if row is not None and para.vazio and row["applied_rev"] is None:
        # Nada pedido e nada jamais aplicado: não há o que convergir, e a linha só diria "pendente" à toa.
        st.db.execute("DELETE FROM device_network WHERE instance_id=?", (iid,))
        return
    if row is None:
        # `leak_detail` nasce preenchido: a linha é NOVA, e o histórico do mesmo id (comandos e medições de uma rede
        # que já foi tirada, de outra configuração) não é prova dela. É essa marca que impede a adoção da transição
        # (`prova_anterior`) de valer para linha recriada.
        st.db.execute("INSERT INTO device_network(instance_id, vpn_profile_id, proxy_profile_id, policy, desired_rev,"
                      " state, detail, updated_at, updated_by, leak_detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (iid, para.vpn_profile_id, para.proxy_profile_id, para.policy, 1, "pendente",
                       f"rede pedida (rev 1) por {quem or 'painel'}; aguarda a aplicação no aparelho", agora, quem,
                       f"rede pedida em {agora}: nenhum teste de vazamento feito para esta linha"))
        return
    if item.reapply:
        rev = int(row["desired_rev"]) + 1
        st.db.execute("UPDATE device_network SET vpn_profile_id=?, proxy_profile_id=?, policy=?, desired_rev=?,"
                      " state='pendente', error=NULL, detail=?, updated_at=?, updated_by=? WHERE instance_id=?",
                      (para.vpn_profile_id, para.proxy_profile_id, para.policy, rev,
                       f"rede pedida (rev {rev}) por {quem or 'painel'}; aguarda a aplicação no aparelho", agora,
                       quem, iid))
    else:
        st.db.execute("UPDATE device_network SET policy=?, updated_at=?, updated_by=? WHERE instance_id=?",
                      (para.policy, agora, quem, iid))


# ============================================================================ verificar e reaplicar (pedido)
def _alvo_de_pedido(st: AppState, instance_id: str) -> Row:
    rt = st.devices.devices.get(instance_id)
    if rt is None:
        raise RedeError(404, "not_found", f"Instância {instance_id} não existe.")
    if rt.store:
        raise RedeError(409, "store_instance", f"{instance_id} é a loja (Play Store): não recebe rede gerenciada.")
    quarentena = st.quarentena(instance_id)
    if quarentena is not None:
        # A sonda abre apps e a aplicação mexe na saída: nada toca num aparelho com conta travada (ADR-055).
        raise RedeError(409, "aparelho_em_quarentena", f"{quarentena}.")
    row = _linha(st, instance_id)
    if row is None:
        raise RedeError(409, "nothing_requested", f"Nenhuma rede foi pedida para {instance_id}: atribua um perfil "
                                                  "antes de verificar ou reaplicar.")
    return row


def _resposta_de_pedido(st: AppState, instance_id: str, acao: str, motivo: str) -> dict[str, object]:
    row = _linha_certa(st, instance_id)
    return {"accepted": True, "instance_id": instance_id, "action": acao, "pending": _pendencia(row, st),
            "desired_rev": int(row["desired_rev"]), "applied_rev": row["applied_rev"], "state": row["state"],
            # Honesto sobre o que NÃO aconteceu: o 202 é "pedido registrado", nunca "aplicado".
            "executed": False, "reason": motivo}


def pedir_reaplicacao(st: AppState, instance_id: str, quem: str | None) -> dict[str, object]:
    """Revisão nova para a mesma configuração: `applied_rev < desired_rev` e `pendente` são a pendência durável que a
    convergência do 25.4 aplica, e sobrevivem a reinício. O estado volta a `pendente` — regredir não precisa de prova;
    avançar sim."""
    _alvo_de_pedido(st, instance_id)
    with st.db.tx():
        # O incremento é do banco e a releitura fica na mesma transação: dois pedidos simultâneos dão duas revisões,
        # nunca a mesma revisão duas vezes.
        st.db.execute("UPDATE device_network SET desired_rev=desired_rev+1, state='pendente', error=NULL,"
                      " updated_at=?, updated_by=? WHERE instance_id=?", (now_iso(), quem, instance_id))
        rev = int(_linha_certa(st, instance_id)["desired_rev"])
        st.db.execute("UPDATE device_network SET detail=? WHERE instance_id=?",
                      (f"reaplicação pedida (rev {rev}) por {quem or 'painel'}; aguarda a aplicação no aparelho",
                       instance_id))
    _emitir(st, f"Rede de {instance_id}: reaplicação pedida (rev {rev})", instance_id=instance_id, acao="reapply",
            desired_rev=rev, state="pendente")
    return _resposta_de_pedido(st, instance_id, "reapply",
                               "Reaplicação registrada como revisão nova; nada foi executado ainda. A convergência a "
                               "aplica no próximo ponto seguro (antes da tarefa que dependa da rede, quando o aparelho "
                               "ligar ou na varredura de 60 s, com ele livre); POST …/apply aplica já.")


def pedir_verificacao(st: AppState, instance_id: str, quem: str | None) -> dict[str, object]:
    """Registra o pedido de medir de novo. O estado NÃO muda: pedir verificação não é evidência de nada. Quem mede é
    a sonda de saída (25.5), pela convergência (`ConvergenciaDeRede.pedir_verificacao` marca o pedido para o próximo
    ponto seguro); aparelho aplicado e ainda não verificado já está em `pendencias()` como `verificar` e é medido de
    qualquer jeito. Sem bloqueio, o `trafego_verificado` segue valendo para a tarefa até a medição nova. Com a política
    `exigida_com_bloqueio`, o pedido APAGA a prova de vazamento (item 29.2): a tarefa espera o teste refeito."""
    _alvo_de_pedido(st, instance_id)
    agora = now_iso()
    with st.db.tx():
        row = _linha_certa(st, instance_id)
        # O pedido vai À FRENTE do detalhe anterior, sem apagá-lo: num `trafego_verificado`, o detalhe é o ponteiro
        # da evidência (a medição que provou), e ele continua valendo enquanto ninguém mediu de novo.
        pedido = f"verificação pedida por {quem or 'painel'} em {agora}; aguarda a sonda de dentro do aparelho"
        anterior = str(row["detail"] or "")
        st.db.execute("UPDATE device_network SET detail=?, updated_at=?, updated_by=? WHERE instance_id=?",
                      ((f"{pedido} | antes: {anterior}" if anterior else pedido)[:500], agora, quem, instance_id))
        if row["policy"] == "exigida_com_bloqueio":
            # Com bloqueio, verificar é refazer o teste de vazamento. A prova é apagada AQUI, no banco: o pedido
            # sobrevive a um reinício do backend (a prova ausente é o que dispara o teste), e até o teste novo provar
            # o bloqueio a tarefa com rede exigida espera — quem pediu para provar de novo não quer a prova antiga.
            apagar_prova_de_vazamento(st, instance_id, f"verificação pedida por {quem or 'painel'}")
    _emitir(st, f"Rede de {instance_id}: verificação pedida", instance_id=instance_id, acao="verify",
            desired_rev=int(row["desired_rev"]), state=row["state"])
    return _resposta_de_pedido(st, instance_id, "verify",
                               "Verificação registrada; nada foi executado no aparelho ainda, e o estado não mudou. A "
                               "sonda de saída mede de dentro dele no próximo ponto seguro (varredura de 60 s com ele "
                               "livre, ou antes da tarefa que dependa da rede); POST …/apply mede já.")


# ============================================================================ observação: o único escritor do estado
def registrar_observacao(st: AppState, instance_id: str, *, rev: int, estado: NetworkState, evidencia: str,
                         erro: str | None = None) -> DeviceNetworkDTO:
    """Grava o que o aparelho mostrou para a revisão `rev`. É por aqui (e por `registrar_medicao`) que o estado muda.

    - `rev` diferente da pedida: a observação é de um pedido velho (o desejado mudou enquanto ela rodava, como no
      `proxy._fechar`); nada muda além do `detail`, e a próxima passada aplica o pedido novo;
    - `configurado`/`conectado` pedem a evidência lida do aparelho (a configuração relida, a rede VPN no
      `dumpsys connectivity`), e gravam `applied_rev`;
    - `pendente` é regressão (falha, deriva, wipe): vale sempre, com o erro;
    - `trafego_verificado` e `parcial` NÃO entram por aqui: só uma medição de dentro do aparelho os produz.

    Ler, decidir e gravar ficam numa transação, e a gravação ainda exige a revisão lida (`AND desired_rev=?`): uma
    atribuição ou reaplicação que chegue no meio não é sobrescrita por uma observação da revisão anterior."""
    if estado in ("trafego_verificado", "parcial"):
        raise ValueError(f"'{estado}' só nasce de uma medição de dentro do aparelho (registrar_medicao)")
    evidencia = (evidencia or "").strip()
    if not evidencia:
        raise ValueError("observação sem evidência não muda estado")
    with st.db.tx():
        row = _linha_certa(st, instance_id)
        agora = now_iso()
        if rev != int(row["desired_rev"]):
            _descartar(st, instance_id, f"observação da rev {rev} descartada: a pedida agora é a "
                                        f"{row['desired_rev']} ({evidencia})", agora)
            return _aparelho_dto(_linha_certa(st, instance_id))
        if estado == "pendente":
            cur = st.db.execute("UPDATE device_network SET state='pendente', detail=?, error=?, updated_at=?"
                                " WHERE instance_id=? AND desired_rev=?",
                                (evidencia[:500], erro[:500] if erro else None, agora, instance_id, rev))
        else:
            cur = st.db.execute("UPDATE device_network SET state=?, applied_rev=?, detail=?, error=NULL, updated_at=?"
                                " WHERE instance_id=? AND desired_rev=?",
                                (estado, rev, evidencia[:500], agora, instance_id, rev))
        if cur.rowcount != 1:
            novo_rev = _linha_certa(st, instance_id)["desired_rev"]
            _descartar(st, instance_id, f"observação da rev {rev} descartada: a pedida mudou para a {novo_rev} "
                                        f"durante o registro ({evidencia})", agora)
            return _aparelho_dto(_linha_certa(st, instance_id))
        novo = _aparelho_dto(_linha_certa(st, instance_id))
    if novo.state != row["state"]:
        _emitir(st, f"Rede de {instance_id}: {row['state']} → {novo.state}", instance_id=instance_id,
                acao="observado", desired_rev=novo.desired_rev, applied_rev=novo.applied_rev, state=novo.state)
    return novo


def _descartar(st: AppState, instance_id: str, motivo: str, agora: str) -> None:
    """Registro de algo que chegou tarde para a revisão pedida: só o `detail` muda, nunca o estado."""
    st.db.execute("UPDATE device_network SET detail=?, updated_at=? WHERE instance_id=?",
                  (motivo[:500], agora, instance_id))


# ============================================================================ a prova de vazamento (item 29.2)
# O teste de vazamento para o cliente VPN e, quase sempre, custa um reinício do aparelho. A prova dele morava só na
# memória da convergência: cada reinício do backend a perdia, e a remedição seguinte refazia o teste em todos os
# aparelhos com bloqueio (30/09: um reinício do backend, 11 reinícios de aparelho, dois deles com conta real). Agora
# ela mora na linha do aparelho (colunas `leak_*`, migração 063), presa à revisão e à instalação do cliente VPN.
SituacaoDaProva = Literal["vale", "ausente", "outra_revisao", "interrompida", "outro_cliente", "vazou", "inconclusiva"]


@dataclass(frozen=True)
class ProvaDeVazamento:
    """O que a linha guarda do teste com o cliente VPN parado. `resultado`: True = o Android recusou a sonda fora da
    VPN; False = vazou; None = o teste não concluiu. `pendente` = um ensaio foi marcado e o desfecho não foi gravado."""

    rev: int | None
    cliente: str | None
    resultado: bool | None
    quando: str | None
    detalhe: str | None
    pendente: bool

    def situacao(self, rev: int, cliente: str | None = None) -> SituacaoDaProva:
        """O que a prova diz para a revisão `rev` e, quando informado, para a instalação `cliente` do cliente VPN.
        Só `vale` aprova. `vazou` e `inconclusiva` são desfechos DESTA revisão e deste cliente: não aprovam e não se
        refazem sozinhos (cada teste para o cliente VPN). `ausente`, `outra_revisao` e `outro_cliente` pedem um teste
        novo. `interrompida` é o ensaio marcado sem desfecho (o backend reiniciou no meio): quem a encontra a fecha
        como inconclusiva, sem parar o cliente de novo."""
        if self.rev is None:
            return "ausente"
        if self.rev != rev:
            return "outra_revisao"
        if self.pendente:
            return "interrompida"
        if cliente is not None and (self.cliente or "") != cliente:
            return "outro_cliente"
        if self.resultado is True:
            return "vale"
        return "vazou" if self.resultado is False else "inconclusiva"


def prova_de_vazamento(row: Row) -> ProvaDeVazamento:
    return ProvaDeVazamento(rev=None if row["leak_rev"] is None else int(row["leak_rev"]), cliente=row["leak_client"],
                            resultado=_bool(row["leak_result"]), quando=row["leak_at"], detalhe=row["leak_detail"],
                            pendente=bool(row["leak_pending"]))


def bloqueio_provado(row: Row) -> bool:
    """A política da linha está atendida no que depende do teste de vazamento? Sem bloqueio pedido, a pergunta não se
    aplica (sim). Com bloqueio, só com a prova da revisão pedida e resultado positivo. A instalação do cliente não é
    conferida aqui (precisa do aparelho): a convergência apaga a prova quando lê outra instalação."""
    if row["policy"] != "exigida_com_bloqueio":
        return True
    return prova_de_vazamento(row).situacao(int(row["desired_rev"])) == "vale"


def marcar_ensaio_de_vazamento(st: AppState, instance_id: str, *, rev: int, cliente: str) -> bool:
    """A INTENÇÃO do ensaio, gravada antes de o cliente VPN ser parado: se o backend cair no meio, a linha diz que um
    ensaio começou e não terminou, e ninguém o repete às cegas. Só com a revisão pedida aplicada (`False` = o pedido
    mudou; o ensaio não começa). A prova anterior da linha, se havia, deixa de valer aqui."""
    agora = now_iso()
    cur = st.db.execute(
        "UPDATE device_network SET leak_rev=?, leak_client=?, leak_result=NULL, leak_at=?, leak_detail=?,"
        " leak_pending=1, updated_at=? WHERE instance_id=? AND desired_rev=? AND applied_rev=?",
        (rev, cliente, agora, "ensaio em curso: o cliente VPN é parado para a sonda", agora, instance_id, rev, rev))
    return cur.rowcount == 1


def gravar_prova_de_vazamento(st: AppState, instance_id: str, *, rev: int, cliente: str, resultado: bool | None,
                              quando: str, detalhe: str) -> bool:
    """O desfecho do ensaio marcado. A gravação exige a revisão pedida, a revisão e a instalação do ensaio e a marca
    de pendente: o resultado de um ensaio de revisão antiga (reaplicação no meio) não vira prova da nova."""
    agora = now_iso()
    cur = st.db.execute(
        "UPDATE device_network SET leak_result=?, leak_at=?, leak_detail=?, leak_pending=0, updated_at=?"
        " WHERE instance_id=? AND desired_rev=? AND leak_rev=? AND leak_client=? AND leak_pending=1",
        (None if resultado is None else int(resultado), quando, detalhe[:500], agora, instance_id, rev, rev, cliente))
    if cur.rowcount != 1:
        return False
    rotulo = {True: "bloqueio provado", False: "VAZOU"}.get(resultado, "inconclusivo")  # type: ignore[arg-type]
    _emitir(st, f"Rede de {instance_id}: teste de vazamento da rev {rev} — {rotulo}", instance_id=instance_id,
            acao="vazamento", desired_rev=rev, leak_result=resultado)
    return True


def desmarcar_ensaio_de_vazamento(st: AppState, instance_id: str, *, rev: int, motivo: str) -> None:
    """O ensaio marcado nem chegou a parar o cliente (a sonda não saiu com o túnel no ar): nada foi tocado e nada foi
    provado. A linha volta a não ter prova; a medição seguinte tenta de novo."""
    st.db.execute(
        "UPDATE device_network SET leak_rev=NULL, leak_client=NULL, leak_result=NULL, leak_at=NULL, leak_detail=?,"
        " leak_pending=0, updated_at=? WHERE instance_id=? AND leak_rev=? AND leak_pending=1",
        (f"ensaio não iniciado: {motivo}"[:500], now_iso(), instance_id, rev))


def fechar_ensaio_interrompido(st: AppState, instance_id: str) -> bool:
    """Um ensaio marcado e sem desfecho, com ninguém o rodando (o backend reiniciou no meio): fecha como INCONCLUSIVO.
    O cliente VPN pode ter sido parado; quem relê o aparelho trata o túnel caído pelo caminho de sempre. O teste não é
    refeito sozinho: inconclusivo não aprova, e só `POST …/verify`, revisão nova ou cliente novo o refazem."""
    agora = now_iso()
    cur = st.db.execute(
        "UPDATE device_network SET leak_result=NULL, leak_pending=0, leak_detail=?, updated_at=?"
        " WHERE instance_id=? AND leak_pending=1",
        (f"vazamento não medido: ensaio interrompido antes do desfecho (o backend reiniciou no meio); peça Verificar "
         f"para refazer. Fechado em {agora}", agora, instance_id))
    if cur.rowcount == 1:
        _emitir(st, f"Rede de {instance_id}: ensaio de vazamento interrompido, fechado como inconclusivo",
                instance_id=instance_id, acao="vazamento", leak_result=None)
    return cur.rowcount == 1


def apagar_prova_de_vazamento(st: AppState, instance_id: str, motivo: str) -> bool:
    """A prova deixou de valer (wipe, outro aparelho atrás do id, cliente VPN de outra instalação, pedido de
    verificar): as colunas voltam a vazio, e o motivo fica em `leak_detail` — SEMPRE, mesmo quando não havia prova
    gravada. Na linha que veio de antes da 063 o motivo é o que impede a adoção da prova do histórico: quem pediu para
    refazer o teste, ou apagou o aparelho, não pode receber de volta a prova antiga."""
    agora = now_iso()
    cur = st.db.execute(
        "UPDATE device_network SET leak_rev=NULL, leak_client=NULL, leak_result=NULL, leak_at=NULL, leak_pending=0,"
        " leak_detail=?, updated_at=? WHERE instance_id=?",
        (f"prova apagada em {agora}: {motivo}"[:500], agora, instance_id))
    return cur.rowcount == 1


# ---------------------------------------------------------------------------- transição: a prova de antes da 063
# Na primeira subida com a migração 063, os aparelhos que o código anterior verificou têm a prova só no histórico (a
# memória do backend que a guardava morreu com ele). Refazer o teste em todos seria repetir, uma última vez, o defeito
# que a 063 corrige — com conta real logada em alguns. A prova anterior só é ADOTADA quando se demonstra, pelo que
# ficou registrado e pelo que o aparelho mostra, que ela é deste aparelho, desta revisão e desta instalação do cliente.
# Linha criada depois da 063 nunca passa por aqui: nela, `leak_blocked = 1` numa medição só existe com prova na linha.
@dataclass(frozen=True)
class ProvaAnterior:
    testado_em: str            # o início do comando que fez o teste (o teste não começou antes disto)
    comando: str               # o `device.network` que o registrou
    medicao: int               # a última medição do aparelho, com o bloqueio provado


def prova_anterior(st: AppState, row: Row) -> ProvaAnterior | None:
    """O teste de vazamento que o código anterior à 063 fez para ESTA revisão, se o registro o sustenta sem buraco:

    - a linha nunca foi escrita pelo mecanismo novo (`leak_*` todas vazias; uma prova apagada deixa o motivo em
      `leak_detail`, e o pedido de refazer não pode ser contornado por aqui);
    - a revisão pedida está aplicada, com bloqueio;
    - a ÚLTIMA medição do aparelho tem o bloqueio provado (um teste posterior inconclusivo teria deixado NULL);
    - dos comandos `verificar` desta revisão, do mais novo para trás, todos os que TÊM desfecho de bloqueio o têm
      provado; o mais antigo dessa sequência é o teste (ou o contém), e o começo dele é o limite inferior do instante
      do teste. Comando que falhou, ficou incerto (o backend reiniciou no meio) ou só releu o aparelho não é evidência
      de nada e é pulado: uma leitura que falha uma vez não pode fazer a passada seguinte desistir da adoção e parar o
      cliente. Andar mais para trás só deixa `testado_em` mais antigo, e a conferência da data do cliente mais estrita.

    Quem chama ainda confere, no aparelho, que o cliente VPN instalado é anterior a `testado_em`."""
    iid, rev = str(row["instance_id"]), int(row["desired_rev"])
    if (row["policy"] != "exigida_com_bloqueio" or row["applied_rev"] is None or int(row["applied_rev"]) != rev
            or row["leak_rev"] is not None or row["leak_pending"] or row["leak_detail"] is not None):
        return None
    ultima = st.db.one("SELECT id, leak_blocked FROM network_measurements WHERE instance_id=? ORDER BY id DESC LIMIT 1",
                       (iid,))
    if ultima is None or ultima["leak_blocked"] != 1:
        return None
    teste: Row | None = None
    # Só comando já terminado: o `verificar` em curso (o que está perguntando isto) ainda não tem desfecho.
    for c in st.db.query("SELECT id, state, params, result, started_at, created_at FROM commands"
                         " WHERE instance_id=? AND verb='device.network' AND finished_at IS NOT NULL"
                         " ORDER BY created_at DESC, id DESC", (iid,)):
        params = loads(c["params"], {}) or {}
        if params.get("acao") != "verificar":
            continue
        if params.get("rev") != rev:
            break                                      # outra revisão: o que vem antes não é desta
        if c["state"] != "succeeded":
            continue                                   # falhou ou ficou incerto: não diz nada do bloqueio
        desfecho = (loads(c["result"], {}) or {}).get("outcome") or {}
        if desfecho.get("measurement_id") is not None:
            m = st.db.one("SELECT leak_blocked FROM network_measurements WHERE id=? AND instance_id=?",
                          (desfecho["measurement_id"], iid))
            provou = m is not None and m["leak_blocked"] == 1
        elif "leak_blocked" in desfecho:
            provou = desfecho["leak_blocked"] is True  # o teste que deixou o cliente parado (a medição veio depois)
        else:
            continue                                   # só releu (deriva) ou dispensou a medição: sem desfecho
        if not provou:
            break                                      # mediu ou testou sem provar: a sequência acaba aqui
        teste = c
    if teste is None:
        return None
    return ProvaAnterior(testado_em=str(teste["started_at"] or teste["created_at"]), comando=str(teste["id"]),
                         medicao=int(ultima["id"]))


def adotar_prova_de_vazamento(st: AppState, instance_id: str, *, rev: int, cliente: str, anterior: ProvaAnterior,
                              instalado_em: str) -> bool:
    """Grava a prova anterior na linha. Só numa linha que o mecanismo novo nunca escreveu e com a revisão aplicada (o
    mesmo CAS do ensaio); o `leak_detail` diz de onde veio, para ninguém a ler como teste feito agora."""
    agora = now_iso()
    detalhe = (f"adotada do histórico em {agora}: teste de vazamento da rev {rev} feito a partir de "
               f"{anterior.testado_em} (comando {anterior.comando}), com o Android recusando a sonda fora da VPN "
               f"(Permission denied), confirmado até a medição #{anterior.medicao}; o cliente VPN instalado é de "
               f"{instalado_em}, anterior ao teste")
    cur = st.db.execute(
        "UPDATE device_network SET leak_rev=?, leak_client=?, leak_result=1, leak_at=?, leak_detail=?, updated_at=?"
        " WHERE instance_id=? AND desired_rev=? AND applied_rev=? AND leak_rev IS NULL AND leak_detail IS NULL"
        " AND leak_pending=0",
        (rev, cliente, anterior.testado_em, detalhe[:500], agora, instance_id, rev, rev))
    if cur.rowcount != 1:
        return False
    _emitir(st, f"Rede de {instance_id}: prova de vazamento anterior adotada (rev {rev}, sem parar o cliente VPN)",
            instance_id=instance_id, acao="vazamento", desired_rev=rev, leak_result=True, adotada=True)
    return True


def _falta_para_verificar(medicao: NetworkMeasurementInput, policy: str, exigidos: list[str], *,
                          provado: bool) -> list[str]:
    """O que impede `trafego_verificado`. `exigidos` vem de `apps_exigidos`: cada um tem de estar medido e `ok`, ou
    `sem_trafego` quando outro app passou pelo túnel (29.44, `apps_sem_trafego`: a ressalva vai no `detail`).
    Sem app exigido (aparelho sem conta vinculada), vale o mínimo: pelo menos um app medido. `provado` = a prova de
    vazamento da LINHA vale para a revisão (`bloqueio_provado`): com bloqueio, é ela que decide — o `leak_blocked` da
    medição é o registro do que a sonda levou, não uma segunda fonte da verdade (uma medição não "declara" o bloqueio)."""
    falta = []
    if not (medicao.egress_ipv4 or medicao.egress_ipv6):
        falta.append("IP de saída não medido")
    sem_medida = [app for app in exigidos if app not in medicao.per_app]
    if sem_medida:
        falta.append("apps do aparelho não medidos: " + ", ".join(sem_medida))
    elif not medicao.per_app:
        falta.append("nenhum app medido (o navegador não prova os outros apps)")
    ruins = sorted(f"{app}={r}" for app, r in medicao.per_app.items() if r not in ("ok", SEM_TRAFEGO))
    if ruins:
        falta.append("apps fora da rede pedida: " + ", ".join(ruins))
    parados = apps_sem_trafego(medicao)
    if parados and not any(r == "ok" for r in medicao.per_app.values()):
        falta.append("nenhum app trafegou na janela (sem tráfego: " + ", ".join(parados) + ")")
    if policy == "exigida_com_bloqueio" and (not provado or medicao.leak_blocked is False):
        falta.append("o bloqueio fora da VPN não foi provado")
    return falta


def apps_sem_trafego(medicao: NetworkMeasurementInput) -> list[str]:
    """Os apps com `sem_trafego` na medição (29.44), na ordem do nome."""
    return sorted(app for app, r in medicao.per_app.items() if r == SEM_TRAFEGO)


def parcial_so_de_app_parado(st: AppState, instance_id: str) -> bool:
    """29.44 (c1): o `parcial` da linha veio só de app parado na janela. A última medição tem IP de saída, todo app
    exigido medido, algum `sem_trafego`, nenhum `ok` e nada fora da rede. Medir de novo pode mudar o estado (o app pode
    ter trafegado desde então), então a verificação não dispensa a medição por falta da prova do bloqueio."""
    m = st.db.one("SELECT egress_ipv4, egress_ipv6, per_app FROM network_measurements WHERE instance_id=?"
                  " ORDER BY id DESC LIMIT 1", (instance_id,))
    if m is None or not (m["egress_ipv4"] or m["egress_ipv6"]):
        return False
    per_app = loads(m["per_app"], {}) or {}
    if not per_app or any(r != SEM_TRAFEGO for r in per_app.values()):
        return False
    return all(app in per_app for app in apps_exigidos(st, instance_id))


def ressalva_sem_trafego(parados: list[str]) -> str:
    """A ressalva do `trafego_verificado` com app parado: o que a pessoa lê no painel e na tarefa que espera.
    `parados` vem em nomes de app (`nomes_dos_apps`), não em pacotes."""
    return ", ".join(parados) + " sem tráfego na janela: não provado, não segura o estado"


def nomes_dos_apps(st: AppState, pacotes: list[str]) -> list[str]:
    """O nome que a pessoa conhece (`apps.name`, "Outlook") de cada pacote, na mesma ordem; sem cadastro, o próprio
    pacote. O `per_app` da medição continua em pacotes: só o texto do `detail` muda (validação do deploy 9)."""
    nomes = []
    for pacote in pacotes:
        linha = st.db.one("SELECT name FROM apps WHERE package=? ORDER BY builtin DESC, id LIMIT 1", (pacote,))
        nomes.append(str(linha["name"]) if linha is not None and linha["name"] else pacote)
    return nomes


def registrar_medicao(st: AppState, instance_id: str, medicao: NetworkMeasurementInput, *,
                      rev: int | None) -> tuple[int, DeviceNetworkDTO | None]:
    """Acrescenta a medição ao histórico (sempre) e decide o estado a partir dela.

    `trafego_verificado` só com tudo: a revisão pedida aplicada (`rev` = `desired_rev` = `applied_rev`), IP de saída
    PÚBLICO medido (o modelo recusa endereço de interface), cada app de `apps_exigidos` medido `ok` (ou, sem app
    exigido, ao menos um app, todos `ok`) e, na política com bloqueio, a prova de vazamento da linha valendo para a
    revisão (`bloqueio_provado`, item 29.2). O app `sem_trafego` não segura quando outro passou pelo túnel e nenhum
    saiu por fora (29.44): o estado é `trafego_verificado` com a ressalva no `detail`. IP medido com algo
    faltando é `parcial`, com o que falta no `detail`. Sem IP medido, o estado não sai do lugar. `rev=None` = medição
    de base (sem rede aplicada): entra no histórico e não mexe em nada.

    Como em `registrar_observacao`, a decisão e a gravação ficam numa transação, e a gravação do estado exige a
    revisão, a aplicada e o estado lidos: uma reaplicação no meio faz a medição valer só como histórico, e o aparelho
    fica em `pendente` com a revisão nova — nunca `trafego_verificado` com `applied_rev < desired_rev`.

    A saída medida é comparada com a dos outros aparelhos (ADR-056 §1, T7): o mesmo IP gera AVISO (no `detail` da
    medição e num evento `warn`), não bloqueio — no piloto sem provedor, todos saem pelo IP do central, e isso é o
    esperado; o aviso existe para ninguém ler "perfis diferentes" como "saídas diferentes"."""
    iguais = saidas_compartilhadas(st, instance_id, medicao.egress_ipv4, medicao.egress_ipv6)
    if iguais:
        aviso = f"aviso: a mesma saída medida em {', '.join(iguais)}"
        medicao = medicao.model_copy(update={"detail": (f"{aviso} | {medicao.detail}" if medicao.detail
                                                        else aviso)[:500]})
    mid, novo = _registrar_medicao(st, instance_id, medicao, rev=rev)
    if iguais:
        st.bus.emit("network.updated", f"Rede de {instance_id}: mesma saída medida que {', '.join(iguais)}",
                    level="warn", instance_id=instance_id,
                    data={"instance_id": instance_id, "acao": "saida_compartilhada", "measurement_id": mid,
                          "egress_ipv4": medicao.egress_ipv4, "egress_ipv6": medicao.egress_ipv6, "shared_with": iguais})
    _avisar_saida_divergente(st, instance_id, medicao, mid, rev)
    return mid, novo


def _avisar_saida_divergente(st: AppState, instance_id: str, medicao: NetworkMeasurementInput, mid: int,
                             rev: int | None) -> None:
    """O aviso da saída medida que não é a esperada (item 29.6), num evento `warn`: com a política `livre` nenhuma
    tarefa espera pela rede, e é por aqui (e pelo `parcial` da linha) que a diferença fica à vista. Só para a medição
    da revisão pedida com IP medido: a de base e a de revisão velha não dizem nada do pedido de hoje."""
    row = _linha(st, instance_id)
    if (row is None or rev is None or rev != int(row["desired_rev"]) or row["state"] not in _MEDIDOS
            or not (medicao.egress_ipv4 or medicao.egress_ipv6)):
        return
    esperada = saida_esperada(st, row["vpn_profile_id"], row["proxy_profile_id"])
    diferenca = saida_divergente(esperada, medicao.egress_ipv4, medicao.egress_ipv6)
    if esperada is None or not diferenca:
        return
    st.bus.emit("network.updated", f"Rede de {instance_id}: " + "; ".join(diferenca), level="warn",
                instance_id=instance_id,
                data={"instance_id": instance_id, "acao": "saida_divergente", "measurement_id": mid,
                      "egress_ipv4": medicao.egress_ipv4, "egress_ipv6": medicao.egress_ipv6,
                      "expected_ipv4": esperada.ipv4, "expected_ipv6": esperada.ipv6,
                      "profile_id": esperada.profile_id})


def saidas_compartilhadas(st: AppState, instance_id: str, ipv4: str | None, ipv6: str | None) -> list[str]:
    """Os OUTROS aparelhos cuja última saída medida (`device_network.egress_*`) é a mesma deste IP."""
    if not (ipv4 or ipv6):
        return []
    return sorted(str(r["instance_id"]) for r in st.db.query(
        "SELECT instance_id FROM device_network WHERE instance_id<>?"
        " AND ((egress_ipv4 IS NOT NULL AND egress_ipv4=?) OR (egress_ipv6 IS NOT NULL AND egress_ipv6=?))",
        (instance_id, ipv4, ipv6)))


def _registrar_medicao(st: AppState, instance_id: str, medicao: NetworkMeasurementInput, *,
                       rev: int | None) -> tuple[int, DeviceNetworkDTO | None]:
    quando = medicao.measured_at or now_iso()
    with st.db.tx():
        mid = int(st.db.inserted_id(
            "INSERT INTO network_measurements(instance_id, measured_at, method, egress_ipv4, egress_ipv6,"
            " dns_resolver, udp_ok, per_app, leak_blocked, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (instance_id, quando, medicao.method, medicao.egress_ipv4, medicao.egress_ipv6, medicao.dns_resolver,
             None if medicao.udp_ok is None else int(medicao.udp_ok), dumps(medicao.per_app),
             None if medicao.leak_blocked is None else int(medicao.leak_blocked), medicao.detail)))
        row = _linha(st, instance_id)
        if row is None:
            return mid, None
        agora = now_iso()
        mediu_ip = bool(medicao.egress_ipv4 or medicao.egress_ipv6)
        if mediu_ip:
            # A última saída MEDIDA fica no aparelho, com a data: configuração não prova IP (ADR-056 §1).
            st.db.execute("UPDATE device_network SET egress_ipv4=?, egress_ipv6=?, verified_at=?, updated_at=?"
                          " WHERE instance_id=?",
                          (medicao.egress_ipv4, medicao.egress_ipv6, quando, agora, instance_id))
        aplicado = (rev is not None and row["applied_rev"] is not None
                    and rev == int(row["desired_rev"]) == int(row["applied_rev"]) and row["state"] in _APLICADOS)
        if not aplicado:
            motivo = ("medição de base, sem rede aplicada" if rev is None
                      else f"medição da rev {rev}, e o aparelho está em {row['state']} com a rev "
                           f"{row['desired_rev']} pedida")
            _descartar(st, instance_id, f"medição #{mid} registrada; o estado não muda ({motivo})", agora)
            return mid, _aparelho_dto(_linha_certa(st, instance_id))
        falta = _falta_para_verificar(medicao, str(row["policy"]), apps_exigidos(st, instance_id),
                                      provado=bloqueio_provado(row))
        # A saída MEDIDA contra a que o perfil da saída final declara (item 29.6). Sem esperada, nada muda.
        falta = falta + saida_divergente(saida_esperada(st, row["vpn_profile_id"], row["proxy_profile_id"]),
                                         medicao.egress_ipv4, medicao.egress_ipv6)
        if not falta:
            estado, detalhe = "trafego_verificado", f"medição #{mid} ({medicao.method}): saída e apps provados"
            if parados := apps_sem_trafego(medicao):
                detalhe += f"; {ressalva_sem_trafego(nomes_dos_apps(st, parados))}"
        elif mediu_ip:
            estado, detalhe = "parcial", f"medição #{mid} ({medicao.method}): " + "; ".join(falta)
        else:
            estado = str(row["state"])
            detalhe = f"medição #{mid} ({medicao.method}) sem IP de saída: o estado não muda"
        cur = st.db.execute("UPDATE device_network SET state=?, detail=?, updated_at=? WHERE instance_id=?"
                            " AND desired_rev=? AND applied_rev=? AND state=?",
                            (estado, detalhe[:500], agora, instance_id, rev, rev, row["state"]))
        if cur.rowcount != 1:
            atual = _linha_certa(st, instance_id)
            _descartar(st, instance_id, f"medição #{mid} registrada só no histórico: o pedido mudou durante o registro "
                                        f"(agora rev {atual['desired_rev']}, {atual['state']})", agora)
            return mid, _aparelho_dto(_linha_certa(st, instance_id))
        novo = _aparelho_dto(_linha_certa(st, instance_id))
    if novo.state != row["state"]:
        _emitir(st, f"Rede de {instance_id}: {row['state']} → {novo.state}", instance_id=instance_id, acao="medicao",
                measurement_id=mid, desired_rev=novo.desired_rev, state=novo.state)
    return mid, novo
