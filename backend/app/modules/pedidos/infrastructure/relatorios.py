"""Memória, observações e relatório do pedido, ligados ao laço (item 28.7; docs/design/pedidos-laco.md, "28.7").

Três gestos, todos sobre o que o domínio já decidiu:

    1. `observacoes_do_fechamento`  LÊ o que a execução da ocorrência leu entre etapas (`step_outputs`), antes de a
                                    purga apagá-la, e prepara as linhas e as atualizações de memória (`fonte`);
    2. `gravar_do_fechamento`       escreve tudo isso, reentrante, na MESMA transação cercada que fecha a ocorrência: ou a
                                    ocorrência fecha com as observações, ou nada muda e a varredura repete;
    3. `preparar_relatorio` / `gravar_relatorio` / `gerar`
                                    monta o relatório DETERMINÍSTICO (`domain/relatorio.py`) e o grava. O resumo por IA é
                                    opcional e DESLIGADO: sem `resumidor` injetado (ou com `resumo_ia` falso) nada é chamado.

O relógio é injetado: nada aqui lê `datetime.now()`. A leitura (montar o relatório, chamar um resumidor) fica FORA da
transação; só a escrita entra nela.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.db import Database, Row
from app.modules.pedidos.domain import memoria as dominio_memoria
from app.modules.pedidos.domain import observacao as dominio_observacao
from app.modules.pedidos.domain import relatorio as dominio_relatorio
from app.modules.pedidos.domain.chave import formatar_instante
from app.modules.pedidos.domain.resumo import ResumidorDeRelatorio, SemResumo
from app.modules.pedidos.infrastructure.repositorio_memoria import NovaObservacao, RepositorioDeMemoria
from app.security.redaction import looks_secret, mentions_credential
from app.util import to_iso

log = logging.getLogger("poc.pedidos")

GATILHOS = ("sob_demanda", "periodo", "encerramento")      # CHECK de `pedido_relatorios.gatilho` (070)


def parece_segredo(texto: str) -> bool:
    """A recusa de segredo por FORMATO: credencial ou menção a credencial/código (a leitura entre apps nunca leva código
    de verificação, ADR-009). Conservadora de propósito: recusar um valor inofensivo custa menos que guardar um código."""
    return looks_secret(texto) or mentions_credential(texto)


@dataclass(frozen=True)
class PreparoDoFechamento:
    observacoes: tuple[NovaObservacao, ...] = ()
    fontes: tuple[tuple[str, str], ...] = ()      # (chave de memória, valor) das fontes cujo conteúdo foi comprovado


@dataclass(frozen=True)
class RelatorioPreparado:
    pedido_id: str
    gatilho: str
    pedido_versao: int
    periodo_de: str | None
    periodo_ate: str
    conteudo: str
    sha256: str
    gerado_em: str
    resumo_texto: str | None = None
    resumo_por: str | None = None
    custo_usd: float = 0.0
    relatorio: dict[str, object] = field(default_factory=dict)


class ServicoDeRelatorios:
    def __init__(self, db: Database, relogio: Callable[[], datetime], *, resumidor: ResumidorDeRelatorio | None = None,
                 resumo_ia: bool = False, resumo_ia_teto_usd: float = 0.05):
        self.db = db
        self.relogio = relogio
        self.repo = RepositorioDeMemoria(db)
        self.resumidor: ResumidorDeRelatorio = resumidor if (resumo_ia and resumidor is not None) else SemResumo()
        self.resumo_ia_teto_usd = resumo_ia_teto_usd

    # ------------------------------------------------------------------ observações do fechamento
    def observacoes_do_fechamento(self, o: Row, *, estado: str, motivo: str | None, versao_do_pedido: int
                                  ) -> PreparoDoFechamento:
        """O que a ocorrência `o`, fechada em `estado`, observou. Só leitura. Sem saída estruturada (execução sem etapa
        de leitura, ou já purgada), grava UMA observação `resultado` com valor ausente e o motivo do fechamento."""
        run_id = o["run_id"]
        saidas = self.repo.saidas_da_execucao(run_id) if run_id else []
        capturado_padrao = to_iso(self.relogio())
        feitas: list[NovaObservacao] = []
        fontes: list[tuple[str, str]] = []
        for s in saidas:
            p = dominio_observacao.preparar(s["value"], tipo=s["value_kind"], estado_final=estado, motivo=motivo,
                                            parece_segredo=parece_segredo)
            fonte = dominio_observacao.curto(" / ".join(x for x in (s["app"], s["etapa"]) if x), dominio_observacao.FONTE_MAX)
            feitas.append(NovaObservacao(
                pedido_id=o["pedido_id"], pedido_versao=int(versao_do_pedido), ocorrencia_id=o["id"], run_id=run_id,
                step_id=s["step_id"], alvo=s["alvo"] or "", nome=s["name"], tipo=p.tipo, situacao=p.situacao,
                valor=p.valor, fonte=fonte or "", trecho=p.trecho, sha256=p.sha256,
                capturado_em=s["created_at"] or capturado_padrao))
            if p.situacao == "observado" and p.sha256:
                chave = f"fonte:{s['name']}" + (f":{s['alvo']}" if s["alvo"] else "")
                if dominio_memoria.CHAVE_RE.fullmatch(chave):
                    fontes.append((chave, json.dumps({"captura": p.sha256[:16], "capturado_em": feitas[-1].capturado_em},
                                                     sort_keys=True, separators=(",", ":"))))
        if not feitas:
            p = dominio_observacao.preparar(None, tipo="resultado", estado_final=estado, motivo=motivo,
                                            parece_segredo=parece_segredo)
            feitas.append(NovaObservacao(
                pedido_id=o["pedido_id"], pedido_versao=int(versao_do_pedido), ocorrencia_id=o["id"], run_id=run_id,
                step_id=None, alvo="", nome=dominio_observacao.NOME_DO_RESULTADO, tipo=p.tipo, situacao=p.situacao,
                valor=None, fonte="", trecho=p.trecho or dominio_observacao.curto(f"ocorrência {estado}", 200),
                sha256=None, capturado_em=capturado_padrao))
        return PreparoDoFechamento(tuple(feitas), tuple(fontes))

    def gravar_do_fechamento(self, preparo: PreparoDoFechamento, *, pedido_id: str, ocorrencia_id: str) -> int:
        """Escreve as observações e a memória de `fonte`. Reentrante; quem chama já está na transação cercada."""
        gravadas = self.repo.inserir_observacoes(preparo.observacoes)
        agora = to_iso(self.relogio())
        for chave, valor in preparo.fontes:
            self.gravar_memoria(pedido_id, chave, "fonte", valor, agora=agora, ocorrencia_id=ocorrencia_id)
        return gravadas

    # ------------------------------------------------------------------ memória
    def gravar_memoria(self, pedido_id: str, chave: str, tipo: str, valor: str, *, agora: str | None = None,
                       ocorrencia_id: str | None = None) -> dominio_memoria.Entrada:
        """Grava (ou atualiza, subindo a versão SÓ se o valor mudou) uma entrada de memória. `MemoriaInvalida` se recusada."""
        quando = agora or to_iso(self.relogio())
        for _ in range(3):                                    # perde a corrida? relê e decide de novo
            antes = self.repo.entrada(pedido_id, chave)
            escrita = dominio_memoria.escrever(antes, chave=chave, tipo=tipo, valor=valor, agora=quando,
                                               ocorrencia_id=ocorrencia_id, parece_segredo=parece_segredo)
            if not escrita.mudou or self.repo.gravar_entrada(pedido_id, antes, escrita.entrada):
                return escrita.entrada
        raise dominio_memoria.MemoriaInvalida(f"memória '{chave}' mudou o tempo todo durante a gravação")

    def resolver_pendencia(self, pedido_id: str, chave: str) -> dominio_memoria.Entrada | None:
        antes = self.repo.entrada(pedido_id, chave)
        if antes is None:
            return None
        escrita = dominio_memoria.resolver(antes, agora=to_iso(self.relogio()))
        if escrita.mudou:
            self.repo.gravar_entrada(pedido_id, antes, escrita.entrada)
        return escrita.entrada

    def memoria_para_o_plano(self, pedido_id: str, *, teto: int = dominio_memoria.TETO_DO_BLOCO
                             ) -> tuple[dominio_memoria.Entrada, ...]:
        """O bloco compactado (§8.1), sem IA. O 28.5 o leva ao plano da ocorrência; aqui só se monta."""
        return dominio_memoria.compactar(self.repo.entradas(pedido_id), teto=teto)

    # ------------------------------------------------------------------ relatório
    def preparar_relatorio(self, pedido: Row, *, gatilho: str, ate: str | None = None, de: str | None = None
                           ) -> RelatorioPreparado:
        """Só leitura (e, se houver, o resumo de IA): monta o relatório sem escrever. `ate` e `de` são instantes no
        formato canônico (`formatar_instante`); `ate` omitido = agora."""
        if gatilho not in GATILHOS:
            raise ValueError(f"gatilho de relatório inválido: {gatilho!r}")
        agora = self.relogio()
        periodo_ate = ate or formatar_instante(agora)
        entrada = dominio_relatorio.EntradaDoRelatorio(
            pedido_id=pedido["id"], pedido_versao=int(pedido["versao"]), periodo_ate=periodo_ate, periodo_de=de,
            criterios=tuple(_criterios(pedido["criterios_sucesso"])),
            ocorrencias=tuple(dominio_relatorio.OcorrenciaVista(
                id=r["id"], previsto_para=r["previsto_para"], estado=r["estado"], motivo=r["motivo"],
                custo_usd=float(r["custo_usd"] or 0.0), origem=r["origem"]) for r in self.repo.ocorrencias(pedido["id"])),
            observacoes=tuple(dominio_relatorio.ObservacaoVista(
                id=r["id"], ocorrencia_id=r["ocorrencia_id"], alvo=r["alvo"], nome=r["nome"], situacao=r["situacao"],
                valor=r["valor"], tipo=r["tipo"], fonte=r["fonte"], trecho=r["trecho"], sha256=r["sha256"],
                capturado_em=r["capturado_em"]) for r in self.repo.todas_as_observacoes(pedido["id"])),
            pendencias=tuple(self.repo.pendencias_abertas(pedido["id"])))
        relatorio = dominio_relatorio.montar(entrada)
        conteudo = dominio_relatorio.serializar(relatorio)
        texto = por = None
        custo = 0.0
        try:
            resumo = self.resumidor.resumir(relatorio, teto_usd=self.resumo_ia_teto_usd)
        except Exception:  # noqa: BLE001 - o resumo é opcional: o relatório determinístico sai de qualquer jeito
            log.exception("pedidos: resumo por IA do relatório do pedido %s falhou; segue só o determinístico", pedido["id"])
            resumo = None
        if resumo is not None and resumo.texto.strip():
            texto, por, custo = resumo.texto.strip(), resumo.papel, float(resumo.custo_usd)
        return RelatorioPreparado(pedido_id=pedido["id"], gatilho=gatilho, pedido_versao=int(pedido["versao"]),
                                  periodo_de=de, periodo_ate=periodo_ate, conteudo=conteudo,
                                  sha256=dominio_relatorio.sha256_de(conteudo), gerado_em=to_iso(agora),
                                  resumo_texto=texto, resumo_por=por, custo_usd=custo, relatorio=relatorio)

    def gravar_relatorio(self, p: RelatorioPreparado) -> Row | None:
        """Escreve o relatório preparado. O de encerramento é UM por pedido: já existindo, devolve o que existe."""
        if p.gatilho == "encerramento":
            ja = self.repo.relatorio_de_encerramento(p.pedido_id)
            if ja is not None:
                return ja
        for _ in range(5):                                    # corrida pela mesma `sequencia`: relê o MAX e tenta de novo
            linha = self.repo.inserir_relatorio(
                pedido_id=p.pedido_id, gatilho=p.gatilho, pedido_versao=p.pedido_versao, periodo_de=p.periodo_de,
                periodo_ate=p.periodo_ate, conteudo=p.conteudo, sha256=p.sha256, gerado_em=p.gerado_em,
                resumo_texto=p.resumo_texto, resumo_por=p.resumo_por, custo_usd=p.custo_usd,
                gerado_por="deterministico")
            if linha is not None:
                return linha
            if p.gatilho == "encerramento":
                ja = self.repo.relatorio_de_encerramento(p.pedido_id)
                if ja is not None:
                    return ja
        return None

    def gerar(self, pedido_id: str, *, gatilho: str = "sob_demanda", ate: str | None = None, de: str | None = None
              ) -> Row | None:
        """Monta e grava (leitura fora da transação, escrita dentro). `None` se o pedido não existe."""
        pedido = self.db.one("SELECT * FROM pedidos WHERE id=?", (pedido_id,))
        if pedido is None:
            return None
        preparado = self.preparar_relatorio(pedido, gatilho=gatilho, ate=ate, de=de)
        with self.db.tx():
            return self.gravar_relatorio(preparado)


def _criterios(bruto: str | None) -> list[str]:
    """`criterios_sucesso` é JSON: lista de textos (contrato 28.9). Valor que não é lista de texto vira nenhum critério."""
    if not bruto:
        return []
    try:
        dado = json.loads(bruto)
    except (TypeError, ValueError):
        return []
    return [str(x) for x in dado if isinstance(x, str) and x.strip()] if isinstance(dado, list) else []
