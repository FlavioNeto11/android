"""Pacote A8 do ADR-054: liga as telas aprendidas (a fatia 5, 18.8) ao resto do central.

- `ObservadorDeTelas`: pendurado no fechamento de cada tentativa (as extensões das costuras do A2). Tira da árvore o
  que o ciclo usa — a tela que o REPOSITÓRIO reconhece, se a tela é protegida, os ids estáveis e a aba de perfil
  declarada — e entrega ao `ServicoDeTelas`. A árvore não sai daqui, e texto de tela nunca vai a lugar nenhum;
- `FornecedorDoLivro`: o que a sessão consome (`integrations/app_declarado/conhecimento.definir_regras_aprendidas`):
  as telas PUBLICADAS, só no modo `telas: on` — fora dele (o interruptor), nenhuma;
- `ObservadorDaSessaoDoLivro`: ouve cada conferência da conta (`sessao.definir_observador_da_sessao`);
- `LeituraDeTelasSql`: os sinais `tela_vista` e a evidência, nos dois dialetos;
- `ConhecimentoDoRepositorio`: o `telas.yaml` de cada app como regras declaradas e o fragmento YAML da exportação,
  conferido pelo carregador (`conhecimento_de_telas.de_dados`).

O fornecedor e o observador da sessão são estado do processo (o motor de sessão é um por app); guardam só uma
referência FRACA ao serviço — um `AppState` desmontado não deixa nada vivo nem escreve num banco fechado.
"""
from __future__ import annotations

import logging
import re
import weakref
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import datetime
from functools import partial
from pathlib import Path

from app.automation import conhecimento_de_telas as telas_
from app.automation.conhecimento_de_telas import ConhecimentoDeTelas, ConhecimentoInvalido, RegraDeTela
from app.automation.hierarchy import UiTree
from app.config import PROJECT_ROOT, TelasAprendidasCfg
from app.db import Database, Row
from app.integrations.app_declarado import conhecimento as sessao_declarada
from app.integrations.app_declarado.conhecimento import (PASTA_DOS_APPS, ConhecimentoDeSessao,
                                                         definir_regras_aprendidas, invalidar_regras_aprendidas)
from app.integrations.app_declarado.sessao import ConferenciaDaSessao, definir_observador_da_sessao
from app.modules.learning.application.ports import RepositorioDeAprendizado
from app.modules.learning.application.servico import LearningService
from app.modules.learning.application.telas import (AjustesDeTelas, ServicoDeTelas, TelaDaSessao, TelaDaTentativa,
                                                    anexar_telas)
from app.modules.learning.domain import telas as dominio
from app.modules.learning.domain.ciclo import EntradaInvalida, NaoEncontrado
from app.modules.learning.domain.livro import ItemDeAprendizado
from app.modules.learning.domain.vocabulario import ModoDeTelas, SignalKind
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.ligar_costuras import extensoes
from app.taskqueue.costuras import FechamentoDeTentativa
from app.util import now, parse_iso
from app.version import commit_em_execucao

log = logging.getLogger("poc.aprendizado")

_PACOTE_ANDROID = re.compile(r"^[A-Za-z][\w]*(\.[A-Za-z][\w]*)+$")
_VIVOS = ("candidate", "validated", "published")
#: O texto do `data` canônico (chaves ordenadas, sem espaço) de uma tela vista que o repositório não reconheceu.
_DESCONHECIDA_NO_DATA = f'%"classificada":"{dominio.DESCONHECIDA}"%'


def ajustes_de_telas(cfg: TelasAprendidasCfg) -> AjustesDeTelas:
    return AjustesDeTelas(modo=ModoDeTelas(cfg.modo), observacoes=cfg.observacoes, execucoes=cfg.execucoes,
                          por_app={pacote: ModoDeTelas(m) for pacote, m in cfg.por_app.items()})


# ------------------------------------------------------------------ leitura (sinais e evidência)
class LeituraDeTelasSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    def observacoes(self, app: str, *, desde: str, limite: int) -> list[dominio.Observacao]:
        return _observacoes(self._db.query(
            "SELECT source_ref, run_id, instance_id, capability, data, simulated, created_at FROM learning_signals"
            " WHERE kind=? AND app_package=? AND created_at >= ? AND data LIKE ? ORDER BY created_at DESC, id DESC"
            " LIMIT ?", (SignalKind.TELA_VISTA.value, app, desde, _DESCONHECIDA_NO_DATA, int(limite))))

    def amostras(self, app: str, *, por_tela: int) -> list[dominio.Observacao]:
        por: dict[str, int] = {}
        saida: list[dominio.Observacao] = []
        for o in _observacoes(self._db.query(
                "SELECT source_ref, run_id, instance_id, capability, data, simulated, created_at FROM learning_signals"
                " WHERE kind=? AND app_package=? AND data NOT LIKE ? ORDER BY created_at DESC, id DESC",
                (SignalKind.TELA_VISTA.value, app, _DESCONHECIDA_NO_DATA))):
            if por.get(o.classificada, 0) < por_tela:
                por[o.classificada] = por.get(o.classificada, 0) + 1
                saida.append(o)
        return saida

    def por_origem(self, app: str, origens: Sequence[str]) -> list[dominio.Observacao]:
        saida: list[dominio.Observacao] = []
        unicas = sorted(set(origens))
        for i in range(0, len(unicas), 200):
            lote = unicas[i:i + 200]
            marcas = ",".join("?" for _ in lote)
            saida.extend(_observacoes(self._db.query(
                "SELECT source_ref, run_id, instance_id, capability, data, simulated, created_at FROM learning_signals"
                f" WHERE kind=? AND app_package=? AND source_ref IN ({marcas})",
                (SignalKind.TELA_VISTA.value, app, *lote))))
        return saida

    def quantas_amostras(self, app: str, tela: str) -> int:
        row = self._db.one("SELECT COUNT(*) AS n FROM learning_signals WHERE kind=? AND app_package=? AND data LIKE ?",
                           (SignalKind.TELA_VISTA.value, app, f'%"classificada":"{tela}"%'))
        return linhas.inteiro(row, "n") if row else 0

    def apps_da_execucao(self, run_id: str) -> list[str]:
        return sorted({linhas.texto(r, "app_package") for r in self._db.query(
            "SELECT DISTINCT app_package FROM learning_signals WHERE kind=? AND run_id=? AND data LIKE ?",
            (SignalKind.TELA_VISTA.value, run_id, _DESCONHECIDA_NO_DATA)) if r["app_package"]})

    def apps_com_telas(self) -> list[str]:
        return sorted({linhas.texto(r, "scope_app") for r in self._db.query(
            "SELECT DISTINCT scope_app FROM learning_items WHERE kind='tela' AND state IN (?,?,?)", _VIVOS)
            if r["scope_app"]})

    def usos(self, app: str, instance_id: str, *, desde: str) -> list[dominio.Uso]:
        """O instante do uso é o da OBSERVAÇÃO (o sinal `tela_vista` da mesma origem), não o da linha de evidência:
        a evidência que o digest grava ao fazer nascer a candidata tem a hora do digest, e a janela de conflito
        acusaria um login de agora contra uma tela vista horas antes."""
        saida: list[dominio.Uso] = []
        for r in self._db.query(
                "SELECT e.item_ref, e.instance_id, COALESCE(s.created_at, e.observed_at) AS quando"
                " FROM learning_evidence e JOIN learning_items i ON i.id = e.item_ref"
                " LEFT JOIN learning_signals s ON s.source_ref = e.origin_ref AND s.kind=?"
                " WHERE i.kind='tela' AND i.scope_app=? AND i.state IN (?,?,?) AND e.stance='for'"
                " AND e.instance_id=? AND COALESCE(s.created_at, e.observed_at) >= ?",
                (SignalKind.TELA_VISTA.value, app, *_VIVOS, instance_id, desde)):
            quando = parse_iso(linhas.texto(r, "quando"))
            if quando is not None:
                saida.append(dominio.Uso(linhas.texto(r, "item_ref"), linhas.texto(r, "instance_id"), quando))
        return saida

    def versoes(self, app: str, *, desde: str) -> frozenset[str]:
        """As versões do app instaladas agora no parque (o `desde` fica para uma fonte com histórico)."""
        return frozenset(linhas.texto(r, "v") for r in self._db.query(
            "SELECT DISTINCT observed_version_name AS v FROM device_app_state WHERE package_name=?"
            " AND observed_version_name IS NOT NULL", (app,)))

    def ultima_a_favor(self, item_ref: str) -> str | None:
        row = self._db.one("SELECT MAX(observed_at) AS ultima FROM learning_evidence WHERE item_ref=? AND stance='for'",
                           (item_ref,))
        return linhas.texto_ou_nulo(row, "ultima") if row else None

    def versao_no_aparelho(self, instance_id: str, pacote: str) -> str | None:
        row = self._db.one("SELECT observed_version_name FROM device_app_state WHERE instance_id=? AND package_name=?",
                           (instance_id, pacote))
        return linhas.texto_ou_nulo(row, "observed_version_name") if row else None


def _observacoes(rows: list[Row]) -> list[dominio.Observacao]:
    saida: list[dominio.Observacao] = []
    for r in rows:
        data = linhas.json_objeto(r, "data")
        ids, classificada, versao = data.get("ids"), data.get("classificada"), data.get("versao")
        if not isinstance(ids, list) or not isinstance(classificada, str):
            continue
        saida.append(dominio.Observacao(
            origem=linhas.texto(r, "source_ref"), ids=frozenset(i for i in ids if isinstance(i, str)),
            classificada=classificada, tem_aba=data.get("tem_aba_de_perfil") is True,
            run_id=linhas.texto_ou_nulo(r, "run_id"), instance_id=linhas.texto_ou_nulo(r, "instance_id"),
            simulated=bool(linhas.inteiro(r, "simulated")), contexto=linhas.texto(r, "capability") or "*",
            versao=versao if isinstance(versao, str) else None, quando=linhas.texto(r, "created_at")))
    return saida


# ------------------------------------------------------------------ o repositório (telas.yaml)
class ConhecimentoDoRepositorio:
    """O conhecimento DECLARADO de cada app, lido uma vez por processo (o arquivo muda por commit e deploy)."""

    def __init__(self, pasta: Path = PASTA_DOS_APPS) -> None:
        self._pasta = pasta
        self._telas: dict[str, ConhecimentoDeTelas | None] = {}
        self._sessoes: dict[str, ConhecimentoDeSessao | None] = {}

    def _pasta_do(self, pacote: str) -> Path | None:
        return self._pasta / pacote if _PACOTE_ANDROID.match(pacote) else None

    def telas(self, pacote: str) -> ConhecimentoDeTelas | None:
        if pacote not in self._telas:
            pasta = self._pasta_do(pacote)
            k: ConhecimentoDeTelas | None = None
            if pasta is not None and (pasta / "telas.yaml").is_file():
                try:
                    k = telas_.carregar(pasta / "telas.yaml")
                except Exception:  # noqa: BLE001 - arquivo inválido (YAML, carga): sem telas aprendidas neste app
                    log.exception("aprendizado: o telas.yaml de %s não carrega (sem telas aprendidas nele)", pacote)
            self._telas[pacote] = k
        return self._telas[pacote]

    def sessao(self, pacote: str) -> ConhecimentoDeSessao | None:
        if pacote not in self._sessoes:
            pasta = self._pasta_do(pacote)
            k: ConhecimentoDeSessao | None = None
            if pasta is not None and (pasta / "sessao.yaml").is_file():
                try:
                    k = sessao_declarada.carregar(pasta)
                except Exception:  # noqa: BLE001 - arquivo inválido: o app fica sem a aba de perfil para o aprendizado
                    log.exception("aprendizado: o sessao.yaml de %s não carrega (sem aba de perfil)", pacote)
            self._sessoes[pacote] = k
        return self._sessoes[pacote]

    def declaradas(self, app: str) -> dominio.Declaradas | None:
        k = self.telas(app)
        if k is None:
            return None
        return dominio.Declaradas(app=k.app, casa=k.estado_conhecido.telas, regras=tuple(
            dominio.RegraDeclarada(tela=r.tela, tipo=r.tipo, ids=r.ids, ids_todos=r.ids_todos,
                                   so_por_ids=(r.sinal is None and r.extracao is None and not r.formulario_de_senha
                                               and not r.sem_elementos))
            for r in k.telas if not r.aprendida))

    def fragmento(self, app: str, itens: Sequence[ItemDeAprendizado], *, commit: str | None, agora: str) -> str:
        """O fragmento YAML das telas aprendidas, com a proveniência em comentário, CONFERIDO pelo carregador do
        motor (`conhecimento_de_telas.fragmento_de_aprendidas`). A tela cujo nome o arquivo já declara (absorvida)
        não sai de novo."""
        k, pasta = self.telas(app), self._pasta_do(app)
        if k is None or pasta is None:
            raise NaoEncontrado(f"{app} não tem telas.yaml no repositório.")
        declaradas = {r.tela for r in k.telas}
        regras: list[RegraDeTela] = []
        notas: dict[str, list[str]] = {}
        for item in itens:
            try:
                regra = telas_.regra_aprendida(item.content)
            except ConhecimentoInvalido as exc:
                log.warning("aprendizado: a tela %s não exporta (%s)", item.id, exc)
                continue
            if regra.tela in declaradas:
                continue
            regras.append(regra)
            notas[regra.tela] = _proveniencia(item)
        if not regras:
            raise NaoEncontrado(f"As telas aprendidas de {app} já estão no telas.yaml (absorvidas) ou não valem.")
        cabecalho = [
            f"Telas aprendidas de {app} (ADR-054, fatia 5), exportadas em {agora[:19]} do commit {commit or '?'}.",
            "Dado de instalação a caminho do repositório: acrescente as regras ao FIM de `telas:` do telas.yaml desta",
            "pasta (depois das declaradas) e as de casa em `estado_conhecido.telas`; commite com um caso em",
            "tests/test_conhecimento_de_telas.py. Depois do deploy, a curadoria aposenta cada uma como",
            "`absorvida:<commit>`. Só ids: nenhuma regra aprendida tem texto de tela."]
        try:
            return telas_.fragmento_de_aprendidas(pasta / "telas.yaml", regras, cabecalho=cabecalho, notas=notas)
        except ConhecimentoInvalido as exc:
            raise EntradaInvalida(f"o fragmento exportado não volta pelo carregador: {exc}") from exc


def _proveniencia(item: ItemDeAprendizado) -> list[str]:
    notas = [f"{item.id} · {item.state.value} · {item.evidence_for} observação(ões) a favor em {item.distinct_runs} "
             f"execução(ões) e {item.distinct_devices} aparelho(s) · versão {item.app_version or '?'} · desde "
             f"{item.created_at[:10]}"]
    execucoes = item.provenance.get("execucoes")
    if isinstance(execucoes, list) and execucoes:
        notas.append("  execuções: " + ", ".join(str(e) for e in execucoes[:5]))
    return notas


# ------------------------------------------------------------------ o fechamento da tentativa
def _pacote_da_frente(tree: UiTree) -> str | None:
    contagem = Counter(e.package for e in tree.elements if e.package)
    return contagem.most_common(1)[0][0] if contagem else None


class ObservadorDeTelas:
    """`ObservadorDeTentativa` (A2): tira da árvore da tentativa fechada o que o ciclo das telas usa."""

    nome = "telas"

    def __init__(self, telas: weakref.ref[ServicoDeTelas], declarado: ConhecimentoDoRepositorio,
                 leitura: LeituraDeTelasSql) -> None:
        self._telas = telas
        self._declarado = declarado
        self._leitura = leitura

    def ao_fechar(self, f: FechamentoDeTentativa) -> None:
        telas = self._telas()
        if (telas is None or f.loja or f.arvore is None or not f.app_package
                or telas.modo_efetivo(f.app_package) is ModoDeTelas.OFF):
            return
        pacote = f.app_package
        k = self._declarado.telas(pacote)
        if k is None:
            return
        tree = f.arvore
        frente = _pacote_da_frente(tree)
        sessao = self._declarado.sessao(pacote)
        # A tela como o REPOSITÓRIO a reconhece (com o formulário de login da sessão, quando o app o declara).
        r = (sessao.reconhecer(tree, package=frente) if sessao is not None
             else telas_.classificar(k, tree, package=frente))
        tipo = r.tipo
        if tipo == telas_.DESCONHECIDA and any(e.password for e in tree.elements):
            tipo = "login"                                  # campo de senha sem o formulário completo: é login
        # Protegida pelos sinais genéricos OU pelo que o app declara (o desafio do app, em qualquer idioma).
        protegida = telas_.tela_protegida(tree) or r.trava is not None or tipo in dominio.TIPOS_DE_CONFLITO
        comprovada = f.status == "succeeded" and f.verified
        ids = dominio.estaveis(telas_.sufixos(tree, pacote)) if comprovada and not protegida else ()
        telas.observar_tentativa(TelaDaTentativa(
            pacote=pacote, attempt_id=f.attempt_id, run_id=f.run_id, step_id=f.step_id, instance_id=f.instance_id,
            status=f.status, verified=f.verified, simulated=f.simulated, loja=f.loja,
            em_primeiro_plano=frente == pacote, protegida=protegida,
            classificada=r.tela if not r.outro_app else telas_.DESCONHECIDA, tipo=tipo, ids=ids,
            tem_aba=bool(ids) and sessao is not None and sessao.aba_de_perfil(tree) is not None,
            versao=self._leitura.versao_no_aparelho(f.instance_id, pacote) if ids else None,
            capability=f.capability or "*", step_hash=f.template_hash, objective_id=f.objective_id,
            profile_id=f.profile_id))


# ------------------------------------------------------------------ a sessão
class FornecedorDoLivro:
    """As telas PUBLICADAS de um pacote, como regras do motor — só no modo `telas: on`. Item publicado que não vale
    como regra é ignorado e contado; nunca derruba a sessão."""

    def __init__(self, telas: weakref.ref[ServicoDeTelas]) -> None:
        self._telas = telas
        self.invalidas = 0

    def __call__(self, pacote: str) -> tuple[RegraDeTela, ...]:
        telas = self._telas()
        if telas is None or telas.modo_efetivo(pacote) is not ModoDeTelas.ON:
            return ()
        regras: list[RegraDeTela] = []
        for item_id, conteudo in telas.publicadas(pacote):
            try:
                regras.append(telas_.regra_aprendida(conteudo))
            except ConhecimentoInvalido as exc:
                self.invalidas += 1
                log.warning("aprendizado: a tela %s publicada não vale como regra (%s); ignorada", item_id, exc)
        return tuple(regras)


class ObservadorDaSessaoDoLivro:
    """Ouve cada conferência da conta; da tela desconhecida tira só os ids (e nada da tela protegida)."""

    def __init__(self, telas: weakref.ref[ServicoDeTelas], declarado: ConhecimentoDoRepositorio) -> None:
        self._telas = telas
        self._declarado = declarado

    def ao_conferir(self, conferencia: ConferenciaDaSessao) -> None:
        telas = self._telas()
        if telas is None or telas.modo_efetivo(conferencia.pacote) is ModoDeTelas.OFF:
            return
        c = conferencia
        ids: tuple[str, ...] = ()
        tem_aba = False
        if c.desconhecida is not None and not telas_.tela_protegida(c.desconhecida):
            ids = dominio.estaveis(telas_.sufixos(c.desconhecida, c.pacote))
            sessao = self._declarado.sessao(c.pacote)
            tem_aba = sessao is not None and sessao.aba_de_perfil(c.desconhecida) is not None
        telas.observar_sessao(TelaDaSessao(
            pacote=c.pacote, instance_id=c.instance_id, profile_id=c.profile_id, desfecho=c.desfecho,
            tipo=c.tipo_da_tela, tela_aprendida=c.tela_aprendida, desconhecida=ids, tem_aba=tem_aba,
            tentou_login=c.tentou_login))


# ------------------------------------------------------------------ a composição
def ligar(servico: LearningService, repo: RepositorioDeAprendizado, db: Database, *,
          config: Callable[[], TelasAprendidasCfg], relogio: Callable[[], datetime] = now,
          commit: Callable[[], str | None] | None = None, pasta: Path | None = None) -> ServicoDeTelas:
    """Monta o `ServicoDeTelas` e o pendura: minerador do digest, passo da curadoria, observador do fechamento da
    tentativa, fornecedor e observador da sessão. `pasta`: a raiz dos pacotes de app (a do repositório por padrão)."""
    declarado = ConhecimentoDoRepositorio(pasta or PASTA_DOS_APPS)
    leitura = LeituraDeTelasSql(db)
    telas = ServicoDeTelas(servico, repo, leitura, declarado, ajustes=lambda: ajustes_de_telas(config()),
                           relogio=relogio, commit=commit or partial(commit_em_execucao, PROJECT_ROOT),
                           ao_mudar=invalidar_regras_aprendidas)
    servico.registrar_minerador(telas)
    servico.registrar_passo(telas)
    anexar_telas(servico, telas)
    ref = weakref.ref(telas)
    extensoes(servico).observar(ObservadorDeTelas(ref, declarado, leitura))
    definir_regras_aprendidas(FornecedorDoLivro(ref))
    definir_observador_da_sessao(ObservadorDaSessaoDoLivro(ref, declarado))
    return telas


__all__ = ["ConhecimentoDoRepositorio", "FornecedorDoLivro", "LeituraDeTelasSql", "ObservadorDaSessaoDoLivro",
           "ObservadorDeTelas", "ajustes_de_telas", "ligar"]
