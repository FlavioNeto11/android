"""A caixa de e-mail da conta PLANEJADA, sem o igfarm (31.334, L1).

O e-mail do parque é uma caixa catch-all (`*@dominio` entregue na caixa compartilhada; `docs/email-do-parque.md`): não existe caixa por
persona a provisionar, o endereço da conta é só o destinatário que o leitor IMAP filtra. Até aqui a linha `caixas_email` só nascia no
registro do igfarm (`ponte_igfarm.py`); no cadastro feito no app (a decisão de 11/10), a conta nasce planejada e precisa do endereço ANTES
do primeiro toque, porque o app pede o e-mail e manda o código para ele.

Só grava uma linha de banco (idempotente por conta): não cria caixa no provedor, não lê nada, não usa IA e não guarda segredo. A senha da
caixa compartilhada vem do ambiente (`EMAIL_IMAP_*`) e nunca passa por aqui; a coluna `secret_ref` (NOT NULL, sem migração) recebe um
marcador, e nada a desreferencia (o leitor usa o endereço).
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.modules.email_do_parque.application.servico import EmailDoParque
from app.modules.email_do_parque.domain.endereco import endereco_valido
from app.modules.identity.infrastructure.ponte_igfarm import ArmazemSql
from app.social.erros import SocialError

if TYPE_CHECKING:
    from app.state import AppState

#: Em `caixas_email.secret_ref`/`key_id` das caixas criadas aqui: a senha é a da caixa compartilhada, que mora no ambiente, não no cofre.
MARCADOR_DA_CAIXA_COMPARTILHADA = "parque:caixa-compartilhada"
MARCADOR_DA_CHAVE = "-"


class CaixaDaContaPlanejada:
    def __init__(self, s: AppState) -> None:
        self.s = s
        self.armazem = ArmazemSql(s.db)

    def garantir(self, profile_id: str, account_id: str) -> str:
        """O endereço da conta; cria a linha se faltar. 409 `sem_caixa_de_email` se o parque não tem domínio permitido."""
        existente = self.armazem.endereco_da_conta(account_id)
        if existente is not None:
            return existente
        parque = getattr(self.s, "email_parque", None)
        dominios = parque.dominios_permitidos if parque is not None else ()
        if parque is None or not dominios:
            raise SocialError("sem_caixa_de_email", "O parque não tem domínio de e-mail permitido para criar a caixa da conta.", 409)
        endereco = self._escolher(profile_id, parque, dominios)
        dominio = endereco.rsplit("@", 1)[1]
        agora = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        with self.armazem.tx():
            if (existente := self.armazem.endereco_da_conta(account_id)) is not None:        # outra chamada ganhou a corrida
                return existente
            self.armazem.gravar_caixa(account_id=account_id, persona_id=profile_id, endereco=endereco, dominio=dominio,
                                      secret_ref=MARCADOR_DA_CAIXA_COMPARTILHADA, key_id=MARCADOR_DA_CHAVE, agora=agora)
        return endereco

    def _escolher(self, profile_id: str, parque: EmailDoParque, dominios: tuple[str, ...]) -> str:
        """Primeiro o que a persona já tem (a sugestão da ponte, depois o e-mail do perfil) se for do parque e estiver livre; senão gera."""
        # O índice de `caixas_email` é global: o endereço de qualquer caixa (inclusive de outra conta da MESMA persona) está tomado.
        tomados = self.armazem.emails_tomados(profile_id) | {str(r['e']) for r in self.s.db.query('SELECT lower(endereco) AS e FROM caixas_email')}
        sugestao = self.armazem.sugestao(profile_id)
        linha = self.s.social_repo.profile_row(profile_id)
        for candidato in (sugestao.email if sugestao is not None else "", str(linha["email"] or "") if linha is not None else ""):
            c = candidato.strip().lower()
            if c and endereco_valido(c) and c.rsplit("@", 1)[1] in dominios and c not in tomados:
                return c
        primeiro = str(linha["first_name"] or "") if linha is not None else ""
        sobrenome = str(linha["last_name"] or "") if linha is not None else ""
        if not primeiro:
            partes = str((linha["display_name"] or "") if linha is not None else "").split()
            primeiro, sobrenome = (partes[0], partes[-1] if len(partes) > 1 else "") if partes else ("", "")
        padrao = parque.config.dominio_padrao.strip().lower()
        dominio = padrao if padrao in dominios else dominios[0]
        return parque.gerar_endereco(persona_id=profile_id, primeiro_nome=primeiro, sobrenome=sobrenome, dominio=dominio,
                                     existentes=tomados)
