"""Pesquisa externa da OPERAÇÃO (prova30 A2, 31.158): lacuna → busca → fontes → consolidação → memória da operação.

Sem segundo sistema: as fontes viram `pedido_observacoes` da operação (`tipo='url'`, com título, trecho, sha256 e o
instante do acesso) e entradas `fonte` da memória; os fatos viram `descoberta` com `origem='pesquisa'`, a confiança
decidida por código (`planning/pesquisa.fatos_consolidados`), a evidência (as observações das fontes) e o frescor. Dali
o bloco `<fatos_da_operacao>` (A1) os leva a cada persona, e nenhuma delas repete a pesquisa.

Regras:
    * lacuna = a operação tem ASSUNTO e não há fato de pesquisa válido (nem marca de tentativa recente). Decisão de
      código, sem IA;
    * uma pesquisa por vez por operação: quem chama segura a trava da operação (a mesma da escrita, `gates.py`), então
      a 2ª execução já encontra os fatos e não pesquisa;
    * teto por operação (`ai.pesquisa.teto_usd_por_operacao`), somado das linhas de `ai_calls` com `origem='pesquisa'`
      e `ref` = a operação, tokens e buscas;
    * a consulta nasce só do assunto, das fontes indicadas e da leitura do alvo; nunca de persona nem de tela sensível;
    * falhou ou não achou fonte: fica a marca `pesquisa.estado` (progresso) por uma hora, para 30 agentes não pagarem
      30 tentativas;
    * 31.231: antes de pagar, os fatos do Livro do MESMO assunto e app (`fatos_do_livro`, a porta do aprendizado, que
      recebe o id da operação). Cobrindo o
      pedido pelo critério explícito (`reaproveitamento_da_pesquisa.cobertura`), eles entram na memória como
      `livro.<item>` (descoberta, confirmada, com o frescor do Livro) e a `pesquisa.estado` registra o reaproveitamento
      com o critério; nenhuma chamada de IA. Não cobrindo, a pesquisa paga roda como antes.
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.config import PesquisaCfg
from app.db import Database, loads
from app.modules.learning.domain.reaproveitamento_da_pesquisa import FatoDoLivro, cobertura
from app.modules.pedidos.domain import memoria as dominio_memoria
from app.modules.pedidos.domain.observacao import curto, sha256_do_valor
from app.modules.pedidos.infrastructure.conhecimento_da_operacao import ConhecimentoDaOperacao
from app.modules.pedidos.infrastructure.relatorios import parece_segredo
from app.modules.pedidos.infrastructure.repositorio_memoria import NovaObservacao, RepositorioDeMemoria
from app.planning import costs
from app.planning.pesquisa import PesquisaBruta, PesquisaConsolidada, PesquisaRequest, fatos_consolidados

log = logging.getLogger(__name__)

CHAVE_DO_ESTADO = "pesquisa.estado"
#: Depois de uma tentativa sem resultado, quanto esperar antes de outra na mesma operação.
ESPERA_APOS_FALHA_S = 3600
FONTES_INDICADAS_MAX = 5


@dataclass(frozen=True)
class Feito:
    """O que a pesquisa deixou na memória da operação (só contagens: o texto mora lá)."""
    fatos: int
    confirmados: int
    fontes: int
    buscas: int
    descartados: int
    #: 31.231: fatos do Livro reaproveitados no lugar da pesquisa paga (0 quando a pesquisa rodou)
    reaproveitados: int = 0


def _hash(texto: str) -> str:
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()[:12]


class PesquisaDaOperacao:
    def __init__(self, db: Database, cfg: PesquisaCfg, prices: dict[str, list[float]],
                 fatos_do_livro: Callable[[str], Sequence[FatoDoLivro]] | None = None):
        self.db = db
        self.cfg = cfg
        self.prices = prices
        self.repo = RepositorioDeMemoria(db)
        #: 31.231: os fatos do Livro do assunto e do app de uma operação, pelo id dela (a porta do aprendizado);
        #: `None` = sem reaproveitamento
        self.fatos_do_livro = fatos_do_livro

    # ------------------------------------------------------------------ o que a operação pede
    def assunto_e_fontes(self, operacao_id: str) -> tuple[str, tuple[str, ...]] | None:
        """`operacoes.assunto` e `operacoes.fontes` (124). Sem a tabela, sem a coluna ou sem assunto: `None`."""
        if "operacoes" not in self.db.tables() or "assunto" not in self.db.columns("operacoes"):
            return None
        linha = self.db.one("SELECT assunto, fontes FROM operacoes WHERE id=?", (operacao_id,))
        if linha is None or not (linha["assunto"] or "").strip():
            return None
        brutas = loads(linha["fontes"], []) or []
        fontes = tuple(str(u).strip() for u in brutas if isinstance(u, str) and u.strip().startswith(("http://", "https://")))
        return " ".join(str(linha["assunto"]).split()), fontes[:FONTES_INDICADAS_MAX]

    def lacuna(self, operacao_id: str) -> bool:
        """Não há fato de pesquisa válido nem marca de tentativa recente."""
        agora = self.db.agora_iso()
        for e in self.repo.entradas_da_operacao(operacao_id):
            if e.origem == "pesquisa" and e.vale(agora) and (e.tipo == "descoberta" or e.chave == CHAVE_DO_ESTADO):
                return False
        return True

    def gasto(self, operacao_id: str) -> float:
        """O que a pesquisa desta operação já custou: tokens × preço e as buscas declaradas (`usd`), sem o simulado."""
        linhas = self.db.query(
            "SELECT model, SUM(CASE WHEN usd IS NULL THEN input_tokens ELSE 0 END) input_tokens,"
            " SUM(CASE WHEN usd IS NULL THEN cache_read ELSE 0 END) cache_read,"
            " SUM(CASE WHEN usd IS NULL THEN cache_write ELSE 0 END) cache_write,"
            " SUM(CASE WHEN usd IS NULL THEN output_tokens ELSE 0 END) output_tokens,"
            " SUM(COALESCE(usd, 0)) usd_declarado FROM ai_calls"
            " WHERE origem='pesquisa' AND ref=? AND COALESCE(provider,'') <> 'simulated' GROUP BY model", (operacao_id,))
        return round(sum(costs.row_usd(self.prices, r) + float(r["usd_declarado"] or 0) for r in linhas), 6)

    # ------------------------------------------------------------------ a pesquisa
    async def pesquisar_se_preciso(self, operacao_id: str, *, run_id: str, contexto: str,
                                   chamar: Callable[[PesquisaRequest], Awaitable[PesquisaBruta]]) -> Feito | None:
        """`None` quando não pesquisou (desligada, sem assunto, sem lacuna, teto atingido). `chamar` é o caminho de IA
        da execução (tetos, vaga e contabilidade); quem chama segura a trava da operação."""
        if not self.cfg.enabled:
            return None
        pedido = self.assunto_e_fontes(operacao_id)
        if pedido is None or not self.lacuna(operacao_id):
            return None
        if (reaproveitado := self._reaproveitar(operacao_id, pedido, run_id=run_id)) is not None:
            return reaproveitado
        gasto = self.gasto(operacao_id)
        if gasto >= self.cfg.teto_usd_por_operacao:
            log.info("operação %s: teto da pesquisa atingido (US$ %.4f de US$ %.2f)", operacao_id, gasto,
                     self.cfg.teto_usd_por_operacao)
            return None
        assunto, fontes = pedido
        req = PesquisaRequest(operacao_id=operacao_id, run_id=run_id, assunto=assunto, fontes_indicadas=fontes,
                              contexto=contexto, max_buscas=self.cfg.max_buscas, ferramenta=self.cfg.ferramenta,
                              max_fatos=self.cfg.max_fatos, preco_por_busca_usd=self.cfg.preco_por_busca_usd)
        try:
            bruta = await chamar(req)
        except Exception as exc:  # noqa: BLE001 - pesquisa é contexto: a falha marca a espera e o texto segue
            log.info("operação %s: a pesquisa falhou (%s)", operacao_id, type(exc).__name__)
            self._marcar_estado(operacao_id, "a pesquisa falhou; nova tentativa depois da espera", run_id,
                                frescor_s=ESPERA_APOS_FALHA_S)
            return None
        consolidada = fatos_consolidados(bruta, max_fatos=self.cfg.max_fatos)
        return self._gravar(operacao_id, consolidada, buscas=bruta.buscas, run_id=run_id)

    def _reaproveitar(self, operacao_id: str, pedido: tuple[str, tuple[str, ...]], *, run_id: str) -> Feito | None:
        """31.231: os fatos do Livro do assunto no lugar da pesquisa paga, quando cobrem o pedido; `None` = pesquisar.
        A leitura do Livro que falha nunca derruba a pesquisa: ela segue paga, como antes."""
        if self.fatos_do_livro is None:
            return None
        _, fontes = pedido
        try:
            fatos = self.fatos_do_livro(operacao_id)
        except Exception:  # noqa: BLE001 - o Livro é atalho: sem ele, a pesquisa paga roda
            log.exception("operação %s: fatos do Livro não lidos; a pesquisa segue", operacao_id)
            return None
        agora = self.db.agora_iso()
        c = cobertura(fatos, fontes_indicadas=fontes, agora=agora, min_fatos=self.cfg.reaproveitar_min_fatos)
        if not c.cobre:
            log.info("operação %s: o Livro não cobre o pedido (%s); a pesquisa paga roda", operacao_id, c.motivo)
            return None
        with self.db.tx():
            gravados = sum(self._escrever(operacao_id, f"livro.{f.ref}", "descoberta", f.texto, confianca="confirmado",
                                          evidencia=(), frescor_ate=f.frescor_ate, run_id=run_id) for f in c.usados)
            itens = ", ".join(f.ref for f in c.usados)
            self._marcar_estado(operacao_id, f"reaproveitado do Livro (31.231): {len(c.usados)} fato(s), itens {itens}; "
                                             f"frescor até {c.frescor_ate}; critério: {c.motivo}",
                                run_id, frescor_s=None, prazo=c.frescor_ate)
        return Feito(fatos=gravados, confirmados=gravados, fontes=0, buscas=0, descartados=0, reaproveitados=gravados)

    def _gravar(self, operacao_id: str, c: PesquisaConsolidada, *, buscas: int, run_id: str) -> Feito:
        agora = self.db.agora_iso()
        prazo = self.db.prazo_iso(self.cfg.frescor_h * 3600)
        obs_por_url: dict[str, str] = {}
        with self.db.tx():
            for f in c.fontes:
                nome = f"fonte_{_hash(f.url)}"
                self.repo.inserir_observacoes([NovaObservacao(
                    pedido_id=None, operacao_id=operacao_id, pedido_versao=1, ocorrencia_id=run_id, run_id=run_id,
                    step_id=None, alvo="", nome=nome, tipo="url", situacao="observado", valor=f.url[:2000],
                    fonte=curto(f.titulo, 120) or "", trecho=curto(f.trecho, 200),
                    sha256=sha256_do_valor(f.trecho or f.url), capturado_em=agora)])
                linha = self.repo.observacao_da_operacao(operacao_id, nome)
                if linha is None:
                    continue
                obs_por_url[f.url] = str(linha["id"])
                quando = f" (acessado em {agora[:10]}" + (f", página de {f.idade}" if f.idade else "") + ")"
                self._escrever(operacao_id, f"fonte.{_hash(f.url)}", "fonte", f"{f.titulo} — {f.url}{quando}",
                               confianca="confirmado", evidencia=(obs_por_url[f.url],), frescor_ate=prazo, run_id=run_id)
            confirmados = 0
            for fato in c.fatos:
                provas = tuple(obs_por_url[u] for u in fato.fontes if u in obs_por_url)
                if not provas:
                    continue
                if self._escrever(operacao_id, f"pesquisa.{_hash(fato.texto)}", "descoberta", fato.texto,
                                  confianca=fato.confianca, evidencia=provas, frescor_ate=prazo, run_id=run_id):
                    confirmados += fato.confianca == "confirmado"
            resumo = (f"{len(c.fatos)} fato(s), {len(c.fontes)} fonte(s), {buscas} busca(s)"
                      + (f", {c.descartados} descartado(s) sem fonte" if c.descartados else ""))
            self._marcar_estado(operacao_id, resumo, run_id, frescor_s=None if c.fatos else ESPERA_APOS_FALHA_S,
                                prazo=prazo if c.fatos else None)
        # 31.179: a pesquisa que chega depois da leitura do alvo (nova tentativa após a espera) já se confere com ela
        ConhecimentoDaOperacao(self.db).confirmar_hipoteses(operacao_id, run_id=run_id)
        return Feito(len(c.fatos), confirmados, len(c.fontes), buscas, c.descartados)

    def _marcar_estado(self, operacao_id: str, texto: str, run_id: str, *, frescor_s: float | None,
                       prazo: str | None = None) -> None:
        ate = prazo or self.db.prazo_iso(frescor_s or ESPERA_APOS_FALHA_S)
        self._escrever(operacao_id, CHAVE_DO_ESTADO, "progresso", texto, confianca="confirmado", evidencia=(),
                       frescor_ate=ate, run_id=run_id)

    def _escrever(self, operacao_id: str, chave: str, tipo: str, valor: str, *, confianca: str,
                  evidencia: tuple[str, ...], frescor_ate: str | None, run_id: str) -> bool:
        agora = self.db.agora_iso()
        for _ in range(3):                                   # perde a corrida? relê e decide de novo
            antes = self.repo.entrada_da_operacao(operacao_id, chave)
            try:
                escrita = dominio_memoria.escrever(
                    antes, chave=chave, tipo=tipo, valor=valor[:dominio_memoria.VALOR_MAX], agora=agora,
                    ocorrencia_id=run_id, parece_segredo=parece_segredo, origem="pesquisa", confianca=confianca,
                    evidencia=evidencia, frescor_ate=frescor_ate)
            except dominio_memoria.MemoriaInvalida as exc:
                log.info("operação %s: '%s' não entrou na memória (%s)", operacao_id, chave, exc)
                return False
            if not escrita.mudou or self.repo.gravar_entrada_da_operacao(operacao_id, antes, escrita.entrada):
                return True
        return False
