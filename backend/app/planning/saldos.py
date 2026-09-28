"""Saldo das contas de IA: Anthropic, OpenAI e Google AI Studio (ADR-051).

O que o dono pediu: acompanhar os três saldos na plataforma, a IDE também enxergá-los, e eles valerem como REGRA —
aviso, bloqueio e o que está ligado em cada conta —, não só como número na tela.

A limitação que molda tudo: nenhum dos três consoles publica o saldo pré-pago por API. Então o saldo daqui é
**estimado**: a última leitura registrada (âncora) menos o que `ai_calls` diz ter sido gasto nessa conta desde a
leitura, pelos mesmos preços do painel de uso (`costs.py`). É estimativa e se diz estimativa: o console é a verdade,
e uma leitura velha vira "desatualizada" em vez de continuar parecendo certa. Um erro de cobrança do próprio
provedor registra saldo 0 — depois de um 402 de verdade, a estimativa otimista seria pior que nenhuma.

Qual conta paga uma chamada sai do provedor (`ai_calls.provider` → `ai.providers.<nome>`: tipo e host) e, nas
linhas antigas sem provedor, do prefixo do modelo. Endpoint local (Ollama) e simulado não pertencem a conta nenhuma.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from ..config import AI_ROLES, Config
from ..db import Database
from ..util import now, now_iso, parse_iso, to_iso
from . import costs

log = logging.getLogger("poc.ai.saldos")

#: As contas que a plataforma acompanha. `console` é onde o dono (ou a IDE, no Chrome dele) lê o saldo.
CONTAS: dict[str, dict[str, str]] = {
    "anthropic": {"label": "Anthropic (Claude Console)", "console": "https://platform.claude.com/settings/billing",
                  "key_env": "ANTHROPIC_API_KEY"},
    "openai": {"label": "OpenAI", "console": "https://platform.openai.com/settings/organization/billing/overview",
               "key_env": "OPENAI_API_KEY"},
    "gemini": {"label": "Google AI Studio (Gemini)", "console": "https://aistudio.google.com/billing",
               "key_env": "GEMINI_API_KEY"},
}

#: Regra de fábrica por conta, enquanto `ai_billing_accounts` não tem a linha dela: aviso em US$ 2 (R$ 10 no
#: Gemini, que cobra em real; câmbio ~5,2 R$/US$ em 28/09/2026), bloqueio desligado — o valor é decisão do dono.
PADRAO: dict[str, dict[str, float | int | str | None]] = {
    "anthropic": {"currency": "USD", "units_per_usd": 1.0, "warn_below": 2.0, "block_below": None, "stale_after_h": 72},
    "openai": {"currency": "USD", "units_per_usd": 1.0, "warn_below": 2.0, "block_below": None, "stale_after_h": 72},
    "gemini": {"currency": "BRL", "units_per_usd": 5.2, "warn_below": 10.0, "block_below": None, "stale_after_h": 72},
}

FONTES = ("manual", "console", "provider_error")

_HOSTS = (("openai.com", "openai"), ("googleapis.com", "gemini"), ("anthropic.com", "anthropic"))
_CHAVES = {"OPENAI_API_KEY": "openai", "GEMINI_API_KEY": "gemini", "GOOGLE_API_KEY": "gemini",
           "ANTHROPIC_API_KEY": "anthropic"}


#: Janela máxima da conciliação: os relatórios devolvem até 31 dias por página.
DIAS_CONCILIACAO = 31


@dataclass
class Conciliacao:
    """Última busca do relatório de custo do provedor para uma conta (`planning/conciliacao.py` preenche)."""
    account: str
    snapshot_id: int             # a leitura (âncora) a que esta conciliação se refere
    window_start: str            # início da janela do relatório (ver `janela_de`)
    window_end: str              # até onde o relatório do provedor cobre (Anthropic: só dias fechados)
    provider_usd: float | None   # o que o provedor cobrou na janela; None = não conseguiu ler
    local_usd: float             # o que `ai_calls` registrou na mesma janela
    fetched_at: str
    error: str | None = None
    #: O que o provedor e `ai_calls` já tinham na janela no instante da leitura (migração 053). O gasto de fora ANTES
    #: da leitura já está no saldo do console: sem descontar a base, ele sairia duas vezes.
    provider_baseline_usd: float = 0.0
    local_baseline_usd: float = 0.0

    @property
    def externo_usd(self) -> float:
        if self.provider_usd is None:
            return 0.0
        provedor = self.provider_usd - self.provider_baseline_usd
        local = self.local_usd - self.local_baseline_usd
        return max(0.0, round(provedor - local, 6))


#: conta → última conciliação. Memória do processo: o roteador e a saúde leem daqui sem esperar HTTP.
CONCILIACOES: dict[str, Conciliacao] = {}


def chave_admin(cfg: Config, conta: str) -> str | None:
    campo = {"anthropic": cfg.env.anthropic_admin_key, "openai": cfg.env.openai_admin_key}.get(conta)
    return campo.get_secret_value() if campo is not None else None


#: Contas cujo relatório só traz DIAS FECHADOS (Anthropic): o dia da leitura não se separa em antes/depois, então a
#: janela começa na meia-noite SEGUINTE — perde o gasto de fora do resto daquele dia (otimista em no máximo meio
#: dia), em vez de descontar de novo o que o console já tinha tirado.
SO_DIAS_FECHADOS = frozenset({"anthropic"})


def janela_de(anchor_iso: str, agora: datetime | None = None, *, conta: str = "") -> str:
    """Início da janela de conciliação de uma leitura: meia-noite UTC do dia dela (ou do dia seguinte, nas contas
    `SO_DIAS_FECHADOS`), limitada a 31 dias."""
    agora = agora or now()
    meia_noite = {"hour": 0, "minute": 0, "second": 0, "microsecond": 0}
    dia = (parse_iso(anchor_iso) or agora).astimezone(timezone.utc).replace(**meia_noite)
    if conta in SO_DIAS_FECHADOS:
        dia += timedelta(days=1)
    limite = (agora - timedelta(days=DIAS_CONCILIACAO - 1)).astimezone(timezone.utc).replace(**meia_noite)
    return to_iso(max(dia, limite))


def conta_por_endpoint(kind: str | None, base_url: str | None, api_key_env: str | None) -> str | None:
    """A conta que paga um endpoint. `None` = local, simulado ou fornecedor que a plataforma não acompanha."""
    if kind == "simulated":
        return None
    if kind == "anthropic":
        return "anthropic"
    host = (base_url or "").split("://", 1)[-1].split("/", 1)[0].lower()
    for sufixo, conta in _HOSTS:
        if host == sufixo or host.endswith("." + sufixo):
            return conta
    if host:
        return None            # base_url de outro lugar (Ollama, vLLM, DeepSeek…): não é conta acompanhada
    return _CHAVES.get(api_key_env or "")


def conta_por_modelo(model: str | None) -> str | None:
    m = (model or "").lower()
    if m.startswith("claude"):
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "chatgpt", "dall-e")):
        return "openai"
    if m.startswith(("gemini", "imagen")):
        return "gemini"
    return None


def conta_do_provedor(cfg: Config, provider: str | None, model: str | None) -> str | None:
    """Conta de uma linha de `ai_calls`: pelo provedor declarado; sem ele (linhas antigas), pelo modelo."""
    nome = (provider or "").strip()
    if nome == "simulated":
        return None
    prov = cfg.file.ai.providers.get(nome) if nome else None
    if prov is not None:
        return conta_por_endpoint(prov.kind, prov.base_url, prov.api_key_env)
    if nome in CONTAS:
        return nome            # provedor do `.env` (anthropic) ou o gerador de imagem (`openai`)
    if nome and nome != "local":
        return conta_por_modelo(model)
    return None if nome == "local" else conta_por_modelo(model)


def conta_do_papel(cfg: Config, papel: str) -> str | None:
    r = cfg.ai_role(papel)
    return conta_por_endpoint(r.kind, r.base_url, r.api_key_env)


def gasto_usd_por_conta(db: Database, cfg: Config, since: str, *, until: str | None = None) -> dict[str, float]:
    """US$ gastos por conta em [`since`, `until`) (ISO UTC), pela mesma conta de `costs.spent_usd`."""
    prices = cfg.file.ai.prices
    linhas = db.query(
        "SELECT provider, model, SUM(CASE WHEN usd IS NULL THEN input_tokens ELSE 0 END) input_tokens,"
        " SUM(CASE WHEN usd IS NULL THEN cache_read ELSE 0 END) cache_read,"
        " SUM(CASE WHEN usd IS NULL THEN cache_write ELSE 0 END) cache_write,"
        " SUM(CASE WHEN usd IS NULL THEN output_tokens ELSE 0 END) output_tokens,"
        " SUM(COALESCE(usd, 0)) usd_declarado FROM ai_calls"
        " WHERE ts >= ? AND ts < ? AND COALESCE(provider,'') <> 'simulated' GROUP BY provider, model",
        (since, until or "9999"))
    out: dict[str, float] = {}
    for linha in linhas:
        conta = conta_do_provedor(cfg, linha["provider"], linha["model"])
        if conta is None:
            continue
        out[conta] = out.get(conta, 0.0) + costs.row_usd(prices, linha) + float(linha["usd_declarado"] or 0)
    return out


@dataclass
class SaldoConta:
    account: str
    label: str
    console: str
    currency: str
    units_per_usd: float
    warn_below: float | None
    block_below: float | None
    stale_after_h: int
    key_configured: bool
    roles: list[str] = field(default_factory=list)       # funções de IA que esta conta paga hoje
    image: bool = False                                  # o gerador de imagem da persona usa esta conta
    anchor_balance: float | None = None
    anchor_at: str | None = None
    anchor_source: str | None = None
    anchor_note: str | None = None
    spent_since_usd: float = 0.0
    estimated_balance: float | None = None
    estimated_balance_usd: float | None = None
    age_h: float | None = None
    #: Conciliação pelo relatório de custo do provedor (chave de administrador). `external_usd` = o que o provedor
    #: cobrou além de `ai_calls` na janela da leitura; já sai do `estimated_balance`.
    admin_key_configured: bool = False
    provider_usd: float | None = None
    external_usd: float = 0.0
    reconciled_at: str | None = None
    reconcile_error: str | None = None
    state: str = "unknown"          # unknown | ok | low | blocked | exhausted
    stale: bool = False
    message: str = ""

    @property
    def em_uso(self) -> bool:
        return bool(self.roles) or self.image

    @property
    def bloqueia(self) -> bool:
        return self.state in ("blocked", "exhausted")

    def as_dict(self) -> dict[str, object]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["in_use"] = self.em_uso
        return d


def _fmt(valor: float, moeda: str) -> str:
    simbolo = "R$" if moeda == "BRL" else "US$" if moeda == "USD" else moeda
    return f"{simbolo} {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def estado(db: Database, cfg: Config, *, agora: datetime | None = None, so: str | None = None) -> list[SaldoConta]:
    """Uma linha por conta acompanhada, com a estimativa e o que ela provoca (aviso/bloqueio). `so` = só esta conta
    (o roteador confere uma conta por chamada e não precisa varrer `ai_calls` três vezes)."""
    agora = agora or now()
    regras = {r["account"]: r for r in db.query("SELECT * FROM ai_billing_accounts")}
    papeis: dict[str, list[str]] = {}
    for papel in AI_ROLES:
        conta = conta_do_papel(cfg, papel)
        if conta:
            papeis.setdefault(conta, []).append(papel)
    imagem = cfg.file.ai.image.provider          # "openai" | "simulated"
    env = cfg.env
    chaves = {"anthropic": env.anthropic_api_key is not None, "openai": env.openai_api_key is not None,
              "gemini": env.gemini_api_key is not None}
    saida: list[SaldoConta] = []
    for conta, meta in CONTAS.items():
        if so is not None and conta != so:
            continue
        r = dict(regras[conta]) if conta in regras else PADRAO[conta]
        s = SaldoConta(
            account=conta, label=meta["label"], console=meta["console"],
            currency=str(r["currency"]), units_per_usd=float(r["units_per_usd"] or 1.0),
            warn_below=None if r["warn_below"] is None else float(r["warn_below"]),
            block_below=None if r["block_below"] is None else float(r["block_below"]),
            stale_after_h=int(r["stale_after_h"] or 72), key_configured=chaves.get(conta, False),
            roles=papeis.get(conta, []), image=(imagem == conta),
            admin_key_configured=chave_admin(cfg, conta) is not None)
        ancora = db.one("SELECT * FROM ai_balance_snapshots WHERE account=? ORDER BY observed_at DESC, id DESC LIMIT 1",
                        (conta,))
        if ancora is None:
            s.message = "Sem leitura de saldo registrada."
            saida.append(s)
            continue
        s.anchor_balance = float(ancora["balance"])
        s.anchor_at = ancora["observed_at"]
        s.anchor_source = ancora["source"]
        s.anchor_note = ancora["note"]
        s.spent_since_usd = round(gasto_usd_por_conta(db, cfg, ancora["observed_at"]).get(conta, 0.0), 6)
        conc = CONCILIACOES.get(conta)
        if conc is not None and conc.snapshot_id == int(ancora["id"]):
            s.provider_usd, s.external_usd = conc.provider_usd, conc.externo_usd
            s.reconciled_at, s.reconcile_error = conc.fetched_at, conc.error
        taxa = float(ancora["units_per_usd"] or s.units_per_usd or 1.0)
        s.estimated_balance = round(s.anchor_balance - (s.spent_since_usd + s.external_usd) * taxa, 4)
        s.estimated_balance_usd = round(s.estimated_balance / taxa, 4) if taxa else None
        lido = parse_iso(ancora["observed_at"])
        s.age_h = round((agora - lido).total_seconds() / 3600, 1) if lido else None
        s.stale = s.age_h is not None and s.age_h > s.stale_after_h
        valor = _fmt(s.estimated_balance, s.currency)
        if s.anchor_source == "provider_error" and s.estimated_balance <= 0:
            s.state, s.message = "exhausted", "O provedor recusou por falta de crédito. Recarregue e registre o saldo novo."
        elif s.block_below is not None and s.estimated_balance <= s.block_below:
            s.state = "blocked"
            s.message = (f"Saldo estimado {valor} abaixo do bloqueio ({_fmt(s.block_below, s.currency)}): "
                         "a IA desta conta está barrada.")
        elif s.warn_below is not None and s.estimated_balance <= s.warn_below:
            s.state = "low"
            s.message = f"Saldo estimado {valor}, abaixo do aviso ({_fmt(s.warn_below, s.currency)})."
        else:
            s.state, s.message = "ok", f"Saldo estimado {valor}."
        if s.external_usd > 0:
            s.message += f" Inclui US$ {s.external_usd:.2f} gastos fora da plataforma (relatório do provedor)."
        if s.stale:
            s.message += f" Leitura de {s.age_h:.0f} h atrás: confira no console."
        saida.append(s)
    return saida


def de_uma(db: Database, cfg: Config, conta: str) -> SaldoConta | None:
    return next(iter(estado(db, cfg, so=conta)), None)


def motivo_de_bloqueio(db: Database, cfg: Config, conta: str | None) -> str | None:
    """Mensagem se a conta está barrada (bloqueio do dono ou crédito esgotado); `None` libera."""
    if conta is None:
        return None
    s = de_uma(db, cfg, conta)
    if s is None or not s.bloqueia:
        return None
    return f"{s.label}: {s.message}"


def registrar_leitura(db: Database, conta: str, saldo: float, *, source: str = "manual", observed_at: str | None = None,
                      currency: str | None = None, units_per_usd: float | None = None, note: str | None = None) -> None:
    if conta not in CONTAS:
        raise ValueError(f"conta desconhecida: {conta}")
    if source not in FONTES:
        raise ValueError(f"origem desconhecida: {source}")
    regra = db.one("SELECT currency, units_per_usd FROM ai_billing_accounts WHERE account=?", (conta,))         or PADRAO[conta]
    moeda = currency or str(regra["currency"])
    taxa = units_per_usd or float(regra["units_per_usd"] or 1.0)
    agora = now_iso()
    db.execute("INSERT INTO ai_balance_snapshots(account, balance, currency, units_per_usd, source, observed_at,"
               " created_at, note) VALUES (?,?,?,?,?,?,?,?)",
               (conta, float(saldo), moeda, float(taxa), source, observed_at or agora, agora, note))


def registrar_esgotado(db: Database, cfg: Config, conta: str | None, detalhe: str) -> None:
    """Erro de cobrança do provedor: saldo 0 como âncora. Uma vez por esgotamento, não uma por chamada."""
    if conta is None or conta not in CONTAS:
        return
    ultima = db.one("SELECT source, balance FROM ai_balance_snapshots WHERE account=? "
                    "ORDER BY observed_at DESC, id DESC LIMIT 1", (conta,))
    if ultima is not None and ultima["source"] == "provider_error" and float(ultima["balance"]) <= 0:
        return
    try:
        registrar_leitura(db, conta, 0.0, source="provider_error", note=(detalhe or "")[:300])
    except Exception:  # noqa: BLE001 - registrar o esgotamento nunca derruba o tratamento do erro original
        log.exception("não foi possível registrar o esgotamento da conta %s", conta)


def ajustar_regra(db: Database, conta: str, **campos: float | int | str | None) -> None:
    permitidos = ("currency", "units_per_usd", "warn_below", "block_below", "stale_after_h")
    mudar = {k: v for k, v in campos.items() if k in permitidos}
    if conta not in CONTAS:
        raise ValueError(f"conta desconhecida: {conta}")
    if db.one("SELECT 1 FROM ai_billing_accounts WHERE account=?", (conta,)) is None:
        p = PADRAO[conta]
        db.execute("INSERT INTO ai_billing_accounts(account, currency, units_per_usd, warn_below, block_below,"
                   " stale_after_h, updated_at) VALUES (?,?,?,?,?,?,?)",
                   (conta, p["currency"], p["units_per_usd"], p["warn_below"], p["block_below"], p["stale_after_h"],
                    now_iso()))
    if not mudar:
        return
    sets = ", ".join(f"{k}=?" for k in mudar)
    db.execute(f"UPDATE ai_billing_accounts SET {sets}, updated_at=? WHERE account=?",
               (*mudar.values(), now_iso(), conta))
