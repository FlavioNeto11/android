"""O conhecimento da OPERAÇÃO no banco (prova30 A1; migração 125). As regras ficam em `domain/conhecimento_da_operacao.py`.

Uma operação (`operacoes`, 124, da Jev) tem uma execução por alvo (`runs.operacao_id`). O que as execuções sabem em comum
mora na memória e nas observações do pedido, com `operacao_id` no lugar de `pedido_id`:

    * `registrar_leitura`: a tela do alvo lida por um agente. A primeira vira a observação da operação e o fato
      `alvo.conteudo` (origem `leitura`, confirmado, com a observação como evidência e frescor); as seguintes só conferem
      o sha256. Uma leitura diferente fica registrada como observação `incerto` DAQUELE agente, sem trocar a da operação;
    * `fatos`: o bloco `<fatos_da_operacao>` que vai ao texto de cada persona, com a contagem para o estágio.

Sem operação (execução avulsa, ou a 124 ainda fora do banco), tudo devolve `None`/vazio e o caminho de hoje segue igual.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.db import Database, loads
from app.modules.pedidos.domain import conhecimento_da_operacao as dominio
from app.modules.pedidos.domain import memoria as dominio_memoria
from app.modules.pedidos.infrastructure.relatorios import parece_segredo
from app.modules.pedidos.infrastructure.repositorio_memoria import NovaObservacao, RepositorioDeMemoria

log = logging.getLogger(__name__)

#: Quanto tempo a leitura do alvo vale para as outras execuções da mesma operação. Uma operação de 30 agentes em ondas
#: dura menos que isso; depois, a próxima execução relê (o post pode ter mudado) e a leitura nova vira a da operação.
FRESCOR_DA_LEITURA_S = 6 * 3600
DIVERGENTE = "a leitura deste agente difere da leitura da operação (o alvo mudou ou é outro); a execução seguiu com a própria tela"


@dataclass(frozen=True)
class Fatos:
    texto: str
    quantos: int                                  # o que o texto recebeu da operação: o bloco e a leitura igual na tela
    refs: tuple[str, ...] = ()                    # o mesmo, como referência (`fato:<chave>`, 31.163): conhecimento_ids
    assunto: str = ""                             # `operacoes.assunto` (124): vai ao escritor junto da intenção


class ConhecimentoDaOperacao:
    def __init__(self, db: Database, *, frescor_da_leitura_s: float = FRESCOR_DA_LEITURA_S):
        self.db = db
        self.repo = RepositorioDeMemoria(db)
        self.frescor_da_leitura_s = frescor_da_leitura_s
        self._tem_coluna = False

    def operacao_da_execucao(self, run_id: str) -> str | None:
        """`runs.operacao_id` (124). Coluna ausente (a 124 ainda não chegou) ou nula: a execução não é de operação."""
        if not self._tem_coluna:
            # Coluna não some depois de existir: basta achá-la uma vez. Ausente, pergunta de novo na próxima.
            if "operacao_id" not in self.db.columns("runs"):
                return None
            self._tem_coluna = True
        valor = self.db.scalar("SELECT operacao_id FROM runs WHERE id=?", (run_id,))
        return str(valor) if valor else None

    # ------------------------------------------------------------------ leitura do alvo
    def registrar_leitura(self, operacao_id: str, *, run_id: str, step_id: str | None, agente: str, fonte: str,
                          texto: str | None, identidade: str | None = None) -> str | None:
        """`primeira`, `igual` ou `diferente`; `None` quando não há leitura a gravar (tela vazia ou com formato de
        segredo, que é recusada inteira: ADR-009). `agente` identifica a execução na observação divergente (o id do
        objetivo), nunca a persona. `identidade` (o autor e o trecho da legenda do alvo) é o que se compara entre os
        agentes, quando existe; sem ela, o texto lido."""
        leitura = dominio.preparar_leitura(texto, identidade=identidade)
        if leitura is None or parece_segredo(leitura.texto):
            return None
        agora = self.db.agora_iso()

        def observacao(alvo: str, situacao: str, trecho: str | None) -> NovaObservacao:
            return NovaObservacao(pedido_id=None, operacao_id=operacao_id, pedido_versao=1, ocorrencia_id=run_id,
                                  run_id=run_id, step_id=step_id, alvo=alvo, nome=dominio.NOME_DA_LEITURA, tipo="text",
                                  situacao=situacao, valor=leitura.texto, fonte=dominio.fonte_curta(fonte),
                                  trecho=trecho, sha256=leitura.sha256, capturado_em=agora)

        with self.db.tx():
            da_operacao = self.repo.observacao_da_operacao(operacao_id, dominio.NOME_DA_LEITURA)
            vencida = da_operacao is not None and self._fato_vencido(operacao_id, agora)
            if da_operacao is None or vencida:
                if da_operacao is not None and vencida:
                    # Leitura velha: a nova toma o lugar (a velha fica na história como observação do agente que a fez).
                    self.db.execute("UPDATE pedido_observacoes SET alvo=? WHERE id=?",
                                    (f"vencida:{da_operacao['id']}"[:120], da_operacao["id"]))
                self.repo.inserir_observacoes([observacao("", "observado",
                                                          "valor cortado no teto" if leitura.cortado else None)])
                nova = self.repo.observacao_da_operacao(operacao_id, dominio.NOME_DA_LEITURA)
                if nova is not None and nova["sha256"] == leitura.sha256 and nova["run_id"] == run_id:
                    self._gravar_fato(operacao_id, leitura.texto, evidencia=str(nova["id"]), agora=agora, run_id=run_id)
                    return dominio.PRIMEIRA
                da_operacao = nova                               # outro agente gravou primeiro: confere com a dele
            resultado = dominio.conferir(da_operacao["sha256"] if da_operacao is not None else None, leitura.sha256)
            if resultado == dominio.DIFERENTE:
                self.repo.inserir_observacoes([observacao(agente, "incerto", DIVERGENTE)])
            return resultado

    def _fato_vencido(self, operacao_id: str, agora: str) -> bool:
        fato = self.repo.entrada_da_operacao(operacao_id, dominio.CHAVE_DO_CONTEUDO)
        return fato is not None and not fato.vale(agora)

    def _gravar_fato(self, operacao_id: str, texto: str, *, evidencia: str, agora: str, run_id: str) -> None:
        prazo = self.db.prazo_iso(self.frescor_da_leitura_s)
        for _ in range(3):                                   # perde a corrida? relê e decide de novo
            antes = self.repo.entrada_da_operacao(operacao_id, dominio.CHAVE_DO_CONTEUDO)
            try:
                escrita = dominio_memoria.escrever(
                    antes, chave=dominio.CHAVE_DO_CONTEUDO, tipo="descoberta", valor=texto, agora=agora,
                    ocorrencia_id=run_id, parece_segredo=parece_segredo, origem="leitura", confianca="confirmado",
                    evidencia=(evidencia,), frescor_ate=prazo)
            except dominio_memoria.MemoriaInvalida as exc:
                log.info("operação %s: a leitura do alvo não virou fato (%s)", operacao_id, exc)
                return
            if not escrita.mudou or self.repo.gravar_entrada_da_operacao(operacao_id, antes, escrita.entrada):
                return

    # ------------------------------------------------------------------ o que vai ao texto
    def fatos(self, operacao_id: str, *, leitura: str | None = None) -> Fatos:
        """`leitura` é como a tela DESTE agente bateu com a da operação: `primeira` ou `igual` tiram a leitura do alvo
        do bloco (ela já vai inteira no `<tela>` dele, e repetir só gastaria tokens). Sem tela, ou com tela diferente,
        a leitura da operação vai no bloco."""
        entradas = self.repo.entradas_da_operacao(operacao_id)
        agora = self.db.agora_iso()
        no_bloco = ([e for e in entradas if e.chave != dominio.CHAVE_DO_CONTEUDO]
                    if leitura in (dominio.PRIMEIRA, dominio.IGUAL) else entradas)
        refs = [dominio.ref(e) for _, e in dominio.escolhidas(no_bloco, agora=agora)]
        if leitura in (dominio.PRIMEIRA, dominio.IGUAL):
            # a leitura da operação está na `<tela>` deste agente: também é conhecimento da operação usado no texto
            refs[:0] = [dominio.ref(e) for e in entradas if e.chave == dominio.CHAVE_DO_CONTEUDO and e.vale(agora)]
        return Fatos(dominio.bloco(no_bloco, agora=agora), len(refs), tuple(refs), self._assunto(operacao_id))

    def _assunto(self, operacao_id: str) -> str:
        """`operacoes.assunto` (124), em uma linha. Sem a tabela, sem a coluna ou sem assunto: vazio."""
        if "operacoes" not in self.db.tables() or "assunto" not in self.db.columns("operacoes"):
            return ""
        return " ".join(str(self.db.scalar("SELECT assunto FROM operacoes WHERE id=?", (operacao_id,)) or "").split())

    def marcar_conhecimento_usado(self, run_id: str, refs: tuple[str, ...]) -> bool:
        """`resultado.conhecimento_ids` do alvo (contrato da operação, 124: "vem da frente de Aprendizado"): os fatos da
        operação que foram ao texto deste agente, gravados em `operacao_alvos.marcas`. São os fatos ENTREGUES ao
        texto; quais o modelo usou de fato, ele não diz. Sem a tabela da 124, ou sem o alvo, não faz nada."""
        if not refs or "marcas" not in self.db.columns("operacao_alvos"):
            return False
        row = self.db.one("SELECT operacao_id, profile_id, marcas FROM operacao_alvos WHERE run_id=?", (run_id,))
        if row is None:
            return False
        marcas = loads(row["marcas"], {}) or {}
        bruto = marcas.get("conhecimento_ids")
        antes = [str(x) for x in bruto] if isinstance(bruto, list) else []
        marcas["conhecimento_ids"] = list(dict.fromkeys([*antes, *refs]))
        self.db.execute("UPDATE operacao_alvos SET marcas=? WHERE operacao_id=? AND profile_id=?",
                        (json.dumps(marcas, ensure_ascii=False), row["operacao_id"], row["profile_id"]))
        return True
