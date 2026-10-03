"""Consumidor de SOMBRA da intenção (item 31.9, ADR-069): R2 (etapa semântica) e R3 (desempate), FORA da cadeia.

O que ele faz, depois que o `_plan` de uma execução terminou: monta UM pedido `DecisaoFechada` (origem `intencao`, classe C3,
modo `shadow`) com o comando sanitizado e duas perguntas `choice`, e deixa a porta gravar a sombra (31.5). Depois casa, nas
linhas gravadas, o que a cadeia REAL resolveu. Nada do que o Jev responde volta ao caminho de trabalho: a cadeia
(`intent_resolver`, `intent_ports`) não é tocada e a sombra só observa.

- **R2** (`intencao_catalogo`): `choice` sobre o catálogo inteiro (habilidades publicadas e fluxos ativos, como ids opacos com
  descrição C2) mais `nenhuma`. Só vai com 1 a 254 entradas: truncar enviaria um catálogo incompleto e a sombra mediria o
  que o Jev NÃO viu. Decisão real: a habilidade que a cadeia resolveu (ou de que fala, quando falta parâmetro), ou `nenhuma`
  quando nada casou. Empate sem desfecho: fica vazio (ninguém decidiu ainda).
- **R3** (`intencao_desempate`): `choice` entre os candidatos que a cadeia registrou como empatados (2 ou mais). Sem decisão
  real aqui: a cadeia em empate não escolhe; o rótulo é a escolha da pessoa ou o desfecho (31.10).
- **C3**: o comando passa por `redact` e pelo filtro SENSATO de `remover_entidades` (ADR-069 item 10: dado pessoal pode ir;
  e-mail, telefone, link, `@handle`, número e o que está entre aspas viram marcador; endereço, documento, e-mail ofuscado
  e numeral ditado recusam; nome e palavra comum passam). Não depende do catálogo. Recusa = estado VAZIO, e a porta grava
  `privacidade`: a linha da sombra existe, com o motivo, e nada sai. A sombra NÃO reduz o risco: em `shadow` o corpo sai
  igual para o decisor; a única proteção é o filtro.
- **C2** (as opções da R2): nome e descrição do catálogo passam por `mascarar_catalogo` ANTES do corte em
  `_DESCRICAO_MAX` (as mesmas máscaras de forma da C3: handle, aspas, e-mail, telefone e número).
- **C7** (senha, código, 2FA, captcha, chave, PIN, em prosa ou não, também com homóglifo, letra de largura cheia ou
  separada por ponto): marcador `credencial`, e a porta recusa o pedido inteiro.
- **Desligado** (padrão: `enabled=false`, consumidor `off`; ou envio não aprovado; ou C3 fora das classes): `ativo()` é falso
  e o chamador não faz NADA, nem a RESOLVE.

O `desfecho` (`casar_desfecho`) não é preenchido aqui: precisa do fim da execução, que é núcleo compartilhado, e fica para o
31.10. O módulo não importa `modules.skills` nem o serviço de fila: quem o liga (`taskqueue/sombra_intencao.py`) converte.
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from ...security.redaction import looks_secret, mentions_credential, redact
from . import privacidade
from .contrato import ID_NENHUMA, MAX_OPCOES, PedidoDeDecisao, Pergunta, pergunta_choice
from .entidades import (
    mascarar_catalogo, mistura_alfabetos, normalizar, remover_entidades, sem_acento,
)
from .porta import Porta, modo_efetivo
from .sombra import RepositorioDeSombra

log = logging.getLogger("poc.ai")

PERGUNTA_CATALOGO: Final = "intencao_catalogo"
PERGUNTA_DESEMPATE: Final = "intencao_desempate"

#: Entradas do catálogo que cabem em R2 (a `nenhuma` ocupa a 255ª).
MAX_CATALOGO: Final = MAX_OPCOES - 1
_DESCRICAO_MAX: Final = 200
_APP: Final = re.compile(r"^[A-Za-z0-9_.\-]{1,120}$")
#: Assunto de C7 em qualquer formato, além do que `mentions_credential` já pega: na dúvida, o pedido inteiro é recusado
#: (ADR-069: C7 nunca sai, nem em sombra). Casa no texto normalizado, sem acento e em minúsculas, com até um separador
#: entre as letras ("p.i.n", "s e n h a", "palavra-passe") e plural. "passe" sozinho NÃO entra: é o imperativo de passar
#: ("passe para o próximo post"); "pass" e "palavra passe" entram (reverificação do 31.9, 03/10).
_PALAVRAS_C7: Final = (
    "codigo", "code", "captcha", "verificacao", "verification", "autenticacao", "authentication", "autenticador",
    "authenticator", "desafio", "senha", "password", "passwd", "pwd", "pass", "passcode", "passphrase", "palavrapasse",
    "contrasena", "clave", "chave", "key", "pin", "otp", "2fa", "mfa", "twofactor", "token", "segredo", "secret",
    "secreto")
_ASSUNTO_C7: Final = re.compile(
    r"(?<![^\W_])(?:" + "|".join(r"[\s.\-_*]?".join(map(re.escape, p)) for p in _PALAVRAS_C7) + r")s?(?![^\W_])")

_INSTRUCOES_CATALOGO: Final = (
    "The state is a command written by the owner of a device fleet, in Portuguese, with names and numbers masked. Pick the "
    "catalog entry that this command asks to run, or none if no entry clearly fits.")
_INSTRUCOES_DESEMPATE: Final = (
    "The state is a command written by the owner of a device fleet, in Portuguese, with names and numbers masked. Several "
    "catalog entries match it equally. Pick the one the command asks for, or none if it cannot be told.")


@dataclass(frozen=True)
class EntradaDeCatalogo:
    """Uma habilidade publicada ou fluxo ativo. `descricao` é texto do catálogo do dono (C2)."""

    skill_id: str
    nome: str
    descricao: str = ""


@dataclass(frozen=True)
class CadeiaObservada:
    """O que a cadeia REAL fez com o comando, em ids de habilidade (sem versão).

    `resolvida`: a habilidade escolhida, ou a única de que a cadeia fala quando falta parâmetro; `sem_casamento`: nada casou
    (o planejador fica com o comando); `empatados`: as habilidades entre as quais nada decidiu, ou que a cadeia desempatou;
    `ambiguos`: quantas etapas da RESOLVE terminaram em AMBIGUOUS (RA-2: o relatório do 31.10 conta por execução)."""

    resolvida: str | None = None
    sem_casamento: bool = False
    empatados: tuple[str, ...] = ()
    ambiguos: int = 0


def id_opaco(skill_id: str) -> str:
    """Id opaco e estável da opção: o nome da habilidade nunca vai no id, e o id cabe no formato que a sombra aceita."""
    return "opt:" + hashlib.sha1(skill_id.encode("utf-8")).hexdigest()[:12]


def _descricao(e: EntradaDeCatalogo) -> str:
    # C2 é o catálogo do dono, liberado, mas o nome de fluxo legado é o resumo de um comando antigo, com o destino dentro
    # (reverificação de 03/10): `mascarar_catalogo` ANTES do corte, para o corte não deixar meia aspa nem meio handle. O
    # `redact` é a rede para um segredo que tenha ido parar numa descrição.
    texto = " ".join(f"{e.nome}: {e.descricao}".split() if e.descricao else e.nome.split())
    return mascarar_catalogo(redact(texto) or "")[:_DESCRICAO_MAX] or "(sem nome)"


def _opcoes(entradas: Sequence[EntradaDeCatalogo]) -> dict[str, str]:
    return {id_opaco(e.skill_id): _descricao(e) for e in sorted(entradas, key=lambda e: e.skill_id)}


def menciona_c7(comando: str) -> bool:
    """O comando fala de credencial, código, 2FA ou desafio (C7), em qualquer formato, ou tem cara de segredo. Confere o
    texto como veio e normalizado (NFKC, sem caractere invisível), e palavra com alfabetos misturados conta como C7: o
    homóglifo ("senhа" com "а" cirílico) é o jeito de a palavra-chave passar."""
    normal = normalizar(comando)
    return (mentions_credential(comando) or mentions_credential(normal) or looks_secret(comando)
            or looks_secret(normal) or mistura_alfabetos(normal) or bool(_ASSUNTO_C7.search(sem_acento(normal))))


class ConsumidorDeIntencao:
    def __init__(self, porta: Porta, repositorio: RepositorioDeSombra) -> None:
        self._porta = porta
        self._repositorio = repositorio
        self._avisou_teto = False

    def ativo(self) -> bool:
        """Falso, e então NADA é feito (nem RESOLVE, nem leitura do banco), quando: a porta não está em `shadow` para a
        intenção (padrão: `enabled=false`), o envio não está aprovado no código (`JEV_RUNTIME_SEND_APPROVED`, lido na hora)
        ou a C3 não está entre as classes efetivas (teto do código ∩ `classes_permitidas`). Desligado custa zero."""
        if not privacidade.JEV_RUNTIME_SEND_APPROVED:
            return False
        cfg = self._porta.cfg
        if modo_efetivo("shadow", cfg, "intencao") != "shadow":
            return False
        classes = privacidade.JEV_ALLOWED_CLASSES
        if cfg is not None and cfg.classes_permitidas is not None:
            classes = classes & frozenset(cfg.classes_permitidas)
        return "C3" in classes

    def pedido(self, *, run_id: str, comando: str, app: str | None, catalogo: Sequence[EntradaDeCatalogo],
               cadeia: CadeiaObservada) -> PedidoDeDecisao | None:
        """O pedido de sombra, ou `None` se não há pergunta a fazer.

        Sanitiza o comando (C3) pelo filtro sensato (`remover_entidades`). C7 no texto (senha, código, 2FA, captcha, em
        prosa ou não) marca `credencial`: a porta recusa o pedido INTEIRO e grava `privacidade`. O que esconde e-mail,
        telefone ou documento = estado vazio, que a porta também recusa."""
        perguntas: list[Pergunta] = []
        if 0 < len(catalogo) <= MAX_CATALOGO:
            perguntas.append(pergunta_choice(PERGUNTA_CATALOGO, _INSTRUCOES_CATALOGO, _opcoes(catalogo)))
        elif catalogo and not self._avisou_teto:
            self._avisou_teto = True             # uma vez por processo: a R2 some da medição enquanto o catálogo não cabe
            log.warning("decisao_fechada: catálogo de %d entradas acima do teto (%d); a R2 da intenção não vai",
                        len(catalogo), MAX_CATALOGO)
        por_id = {e.skill_id: e for e in catalogo}
        empatados = [por_id[s] for s in dict.fromkeys(cadeia.empatados) if s in por_id]
        if len(empatados) >= 2:
            perguntas.append(pergunta_choice(PERGUNTA_DESEMPATE, _INSTRUCOES_DESEMPATE, _opcoes(empatados)))
        if not perguntas:
            return None
        if menciona_c7(comando):
            return PedidoDeDecisao(origem="intencao", classe="C3", estado={}, perguntas=tuple(perguntas), modo="shadow",
                                   run_id=run_id, ref=run_id, marcadores=frozenset({"credencial"}))
        limpo = remover_entidades(redact(comando) or "") if comando.strip() else None
        estado: dict[str, str] = {}
        if limpo:                                    # `None` (forma do piso) ou vazio: estado vazio, a porta recusa
            estado["comando"] = limpo
            if app and _APP.fullmatch(app):
                estado["app"] = app
        return PedidoDeDecisao(origem="intencao", classe="C3", estado=estado, perguntas=tuple(perguntas), modo="shadow",
                               run_id=run_id, ref=run_id)

    def observar(self, *, run_id: str, comando: str, app: str | None, catalogo: Sequence[EntradaDeCatalogo],
                 cadeia: CadeiaObservada) -> None:
        """Bloqueante (roda numa thread, nunca no laço): consulta a porta em shadow e casa a decisão real.

        O casamento vai como `ao_registrar`: a porta o chama na MESMA thread, logo depois de gravar a linha (também na
        recusa por privacidade). Sem polling, sem espera fixa. Qualquer falha é engolida: medir nunca derruba o trabalho."""
        try:
            if not self.ativo():
                return
            pedido = self.pedido(run_id=run_id, comando=comando, app=app, catalogo=catalogo, cadeia=cadeia)
            if pedido is None:
                return
            reais = self.decisoes_reais(cadeia, {p.id for p in pedido.perguntas})

            def ao_registrar() -> None:
                if reais:
                    self._repositorio.casar_decisao_real(reais, ref=run_id)
                self._repositorio.anotar_ambiguos(cadeia.ambiguos, ref=run_id)

            self._porta.consultar(pedido, ao_registrar=ao_registrar)
        except Exception:  # noqa: BLE001
            log.warning("decisao_fechada: falha na sombra da intenção")

    @staticmethod
    def decisoes_reais(cadeia: CadeiaObservada, perguntas: set[str]) -> dict[str, str]:
        """{pergunta: id opaco do que a cadeia real resolveu}. Pergunta sem decisão real conhecida fica de fora.

        Só a R2 tem decisão real aqui. A R3 (desempate) NÃO: a cadeia que termina em empate não escolhe nenhum candidato
        (`AMBIGUOUS` volta para a pessoa), e a que desempata não devolve os candidatos. O rótulo da R3 é a escolha da
        pessoa ou o desfecho, casados no 31.10 (`docs/design/jev-golden-set.md` §3)."""
        reais: dict[str, str] = {}
        if PERGUNTA_CATALOGO in perguntas:
            if cadeia.resolvida is not None:
                reais[PERGUNTA_CATALOGO] = id_opaco(cadeia.resolvida)
            elif cadeia.sem_casamento:
                reais[PERGUNTA_CATALOGO] = ID_NENHUMA
        return reais


__all__ = ["CadeiaObservada", "ConsumidorDeIntencao", "EntradaDeCatalogo", "MAX_CATALOGO", "PERGUNTA_CATALOGO",
           "PERGUNTA_DESEMPATE", "id_opaco"]
