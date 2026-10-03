"""Saúde da integração com o Trello (item 32.2, ADR-072): o que falta para ela funcionar, sem nunca dizer um valor.

Só olha a CONFIGURAÇÃO (segredo no `.env`, URL do webhook). Os problemas que dependem de chamar o Trello
(`trello_recusado`, `trello_limite`, `trello_webhook_inativo`) nascem do serviço, nos passos seguintes.
"""
from __future__ import annotations

from app.config import Config
from app.models import Problem


def _tem(segredo: object) -> bool:
    return bool(segredo.get_secret_value().strip()) if hasattr(segredo, "get_secret_value") else False


def problemas_do_trello(cfg: Config) -> list[Problem]:
    trello, env = cfg.file.trello, cfg.env
    out: list[Problem] = []
    if trello.enabled:
        faltam = [nome for nome, valor in (("TRELLO_API_KEY", env.trello_api_key), ("TRELLO_TOKEN", env.trello_token))
                  if not _tem(valor)]
        if faltam:
            out.append(Problem(
                code="trello_sem_segredo",
                message=f"Trello ligado (trello.enabled), mas falta no .env: {', '.join(faltam)}.",
                hint="Siga o procedimento de docs/operacao.md (Trello) e reinicie a tarefa farm-central. Enquanto "
                     "faltar, a Central não fala com o Trello; o painel e o Telegram seguem como estão."))
    if trello.webhook.enabled:
        faltam = []
        if not _tem(env.trello_api_secret):
            faltam.append("TRELLO_API_SECRET no .env")
        if not (trello.webhook.callback_url or "").strip():
            faltam.append("trello.webhook.callback_url no config")
        if faltam:
            out.append(Problem(
                code="trello_webhook_sem_segredo",
                message=f"Webhook do Trello ligado (trello.webhook.enabled), mas falta: {', '.join(faltam)}.",
                hint="Sem o segredo do aplicativo a Central não verifica a assinatura e a rota responde 404; sem a URL "
                     "pública não há o que cadastrar no Trello. A reconciliação por leitura segue cobrindo "
                     "(docs/design/trello-integracao.md, §8)."))
    return out
