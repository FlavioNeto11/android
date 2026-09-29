"""Pacote A9 do ADR-054: a leitura das preferências no banco e o adaptador da porta de desambiguação das habilidades.

- `PreferenciasSql` cumpre `LeituraDePreferencias`: as respostas vêm do sinal `respondeu_pergunta` do A2 (o campo e o
  sha256, nunca o valor), com o perfil e o comando lidos da execução que perguntou (a foto `runs.targets`); as
  escolhas no desambiguador vêm do mesmo sinal com o campo `skill`, cruzado com o evento do empate (os candidatos) e
  com a habilidade que a sucessora resolveu (`runs.skill_id`, ou `runs.flow_id` como `flow:<id>`);
- `PreferenciasDoLivro` cumpre `PreferenceSource` (`skills.application.intent_ports`): o DAG é `learning → skills`, e
  as habilidades nunca importam o aprendizado;
- `montar_preferencias(db, servico)`: o serviço pronto para a rota e para a composição.

Só leitura (a escrita passa pelo serviço do livro). Nada de texto de tela, nada de IA.
"""
from __future__ import annotations

import json
from collections.abc import Sequence

from app.db import Database, Row
from app.modules.learning.application.preferencias import (CAMPO_DA_HABILIDADE, EscolhaObservada, Observacao,
                                                           PerguntasAbertas, ServicoDePreferencias,
                                                           chave_da_resposta, modelo_do_comando, modelo_do_conjunto)
from app.modules.learning.application.servico import LearningService
from app.modules.learning.domain.vocabulario import SignalKind
from app.modules.learning.infrastructure import linhas
from app.modules.learning.infrastructure.segredo import TriagemDeCredencial
from app.modules.learning.infrastructure.sql_repository import SqlLearningRepository
from app.modules.skills.application.intent_ports import PreferenceHint
from app.modules.skills.domain.intent import SkillMatch
from app.modules.skills.domain.refs import LEGACY_PREFIX

#: Perguntas de DESTINO não são respondidas por texto (ADR-047): nunca viram preferência nem sugestão.
_CAMPOS_DE_DESTINO = frozenset({"profile_id", "instance_id"})


def _objeto(bruto: object) -> dict[str, object]:
    if not isinstance(bruto, str) or not bruto:
        return {}
    try:
        valor = json.loads(bruto)
    except ValueError:
        return {}
    return valor if isinstance(valor, dict) else {}


def _texto(valor: object) -> str:
    return valor.strip() if isinstance(valor, str) else ""


def _skill_id(ref: str) -> str:
    """`ig.a@1` → `ig.a`; `flow:x@1` → `flow:x`."""
    base, sep, _ = ref.rpartition("@")
    return base if sep else ref


class PreferenciasSql:
    def __init__(self, db: Database) -> None:
        self._db = db

    # ---------------------------------------------------------------- a execução que perguntou
    def _pedido(self, run: Row) -> tuple[str, tuple[str | None, ...]]:
        """O comando (sem destinos, como a foto o guardou) e os perfis da execução, da foto `runs.targets`; sem foto,
        os perfis dos objetivos."""
        foto = _objeto(run["targets"])
        comando = _texto(foto.get("command_sem_destinos")) or _texto(run["command"])
        alvos = foto.get("alvos")
        perfis: list[str | None] = []
        if isinstance(alvos, list):
            perfis = [_texto(a.get("profile_id")) or None for a in alvos if isinstance(a, dict)]
        if not perfis:
            pedido = foto.get("pedido")
            ids = pedido.get("profile_ids") if isinstance(pedido, dict) else None
            perfis = [_texto(p) or None for p in ids] if isinstance(ids, list) else []
        if not perfis:
            perfis = [linhas.texto_ou_nulo(o, "profile_id") for o in self._db.query(
                "SELECT profile_id FROM objectives WHERE run_id=? ORDER BY id", (linhas.texto(run, "id"),))]
        return comando, tuple(dict.fromkeys(perfis))

    def _respondidas(self, *, desde: str | None = None) -> list[tuple[Row, dict[str, object], Row]]:
        sql = ("SELECT s.id, s.run_id, s.data, s.app_package, s.simulated, s.created_at FROM learning_signals s"
               " WHERE s.kind=?")
        params: list[object] = [SignalKind.RESPONDEU_PERGUNTA.value]
        if desde is not None:
            sql += " AND s.created_at >= ?"
            params.append(desde)
        saida: list[tuple[Row, dict[str, object], Row]] = []
        for s in self._db.query(sql + " ORDER BY s.id", tuple(params)):
            run = self._db.one("SELECT id, command, targets FROM runs WHERE id=?", (s["run_id"],))
            if run is not None:
                saida.append((s, _objeto(s["data"]), run))
        return saida

    # ---------------------------------------------------------------- LeituraDePreferencias
    def respostas(self) -> list[Observacao]:
        saida: list[Observacao] = []
        for s, dado, run in self._respondidas():
            campo = _texto(dado.get("campo"))
            chave = _texto(dado.get("resposta_sha256"))
            if not campo or campo == CAMPO_DA_HABILIDADE or campo in _CAMPOS_DE_DESTINO or not chave:
                continue
            comando, perfis = self._pedido(run)
            for perfil in perfis:
                if perfil:
                    saida.append(Observacao(
                        origem=f"signal:{s['id']}", run_id=linhas.texto(run, "id"), perfil=perfil, campo=campo,
                        modelo=modelo_do_comando(comando), valor=chave_da_resposta(chave),
                        app_package=linhas.texto(s, "app_package"),
                        run_sucessora=_texto(dado.get("run_sucessora")) or None,
                        simulated=bool(linhas.inteiro(s, "simulated")), pergunta=comando))
        return saida

    def escolhas_a_registrar(self, desde: str) -> list[EscolhaObservada]:
        saida: list[EscolhaObservada] = []
        for s, dado, run in self._respondidas(desde=desde):
            sucessora = _texto(dado.get("run_sucessora"))
            if _texto(dado.get("campo")) != CAMPO_DA_HABILIDADE or not sucessora:
                continue
            conjunto = self._empate(linhas.texto(run, "id"))
            escolhida = self._resolvida(sucessora)
            if len(conjunto) < 2 or escolhida is None or escolhida not in conjunto:
                continue                   # reescreveu para outra coisa, ou a sucessora ainda não resolveu
            _, perfis = self._pedido(run)
            for perfil in perfis:
                if perfil:
                    saida.append(EscolhaObservada(
                        run_id=linhas.texto(run, "id"), perfil=perfil, conjunto=conjunto, escolhida=escolhida,
                        run_sucessora=sucessora, app_package=linhas.texto(s, "app_package"),
                        simulated=bool(linhas.inteiro(s, "simulated"))))
        return saida

    def _empate(self, run_id: str) -> tuple[str, ...]:
        """Os candidatos do empate, do evento que pôs a execução em `needs_input` (o mais recente que os traz)."""
        for ev in self._db.query("SELECT data FROM events WHERE run_id=? AND data IS NOT NULL ORDER BY id DESC",
                                 (run_id,)):
            candidatos = _objeto(ev["data"]).get("candidates")
            if isinstance(candidatos, list) and candidatos:
                return tuple(sorted({_skill_id(c) for c in candidatos if isinstance(c, str) and c}))
        return ()

    def _resolvida(self, run_id: str) -> str | None:
        run = self._db.one("SELECT skill_id, flow_id FROM runs WHERE id=?", (run_id,))
        if run is None:
            return None
        skill = linhas.texto_ou_nulo(run, "skill_id")
        fluxo = linhas.texto_ou_nulo(run, "flow_id")
        return skill or (LEGACY_PREFIX + fluxo if fluxo else None)

    def escolhas(self) -> list[Observacao]:
        saida: list[Observacao] = []
        for s in self._db.query("SELECT id, run_id, profile_id, data, app_package, simulated FROM learning_signals"
                                " WHERE kind=? ORDER BY id", (SignalKind.ESCOLHEU_HABILIDADE.value,)):
            dado = _objeto(s["data"])
            conjunto = dado.get("conjunto")
            opcoes = tuple(c for c in conjunto if isinstance(c, str)) if isinstance(conjunto, list) else ()
            perfil = linhas.texto_ou_nulo(s, "profile_id")
            escolhida = _texto(dado.get("escolhida"))
            if not perfil or not escolhida or len(opcoes) < 2:
                continue
            saida.append(Observacao(
                origem=f"signal:{s['id']}", run_id=linhas.texto_ou_nulo(s, "run_id") or "", perfil=perfil,
                campo=CAMPO_DA_HABILIDADE, modelo=modelo_do_conjunto(opcoes), valor=escolhida,
                app_package=linhas.texto(s, "app_package"), run_sucessora=_texto(dado.get("run_sucessora")) or None,
                simulated=bool(linhas.inteiro(s, "simulated")), opcoes=opcoes))
        return saida

    def comando(self, run_id: str) -> str | None:
        run = self._db.one("SELECT command FROM runs WHERE id=?", (run_id,))
        return linhas.texto_ou_nulo(run, "command") if run is not None else None

    def plano_tem_efeito(self, run_id: str) -> bool:
        run = self._db.one("SELECT plan FROM runs WHERE id=?", (run_id,))
        plano = _objeto(run["plan"]) if run is not None else {}
        passos = plano.get("steps")
        if not isinstance(passos, list) or not passos:
            return True                    # sem plano legível, na dúvida tem efeito: o dono decide
        return any(isinstance(p, dict) and p.get("side_effect") is True for p in passos)

    def perguntas_abertas(self, run_id: str) -> PerguntasAbertas | None:
        """As perguntas abertas pelo mesmo critério do assistente (`perguntas_da_execucao`): as do plano (`missing`)
        ou, sem plano, as do evento mais recente que as traz. Sem os campos de destino."""
        run = self._db.one("SELECT id, status, command, plan, targets FROM runs WHERE id=?", (run_id,))
        if run is None:
            return None
        comando, perfis = self._pedido(run)
        faltando = _objeto(run["plan"]).get("missing")
        perguntas: list[object] = list(faltando) if isinstance(faltando, list) else []
        if not perguntas:
            for ev in self._db.query("SELECT data FROM events WHERE run_id=? AND data IS NOT NULL ORDER BY id DESC",
                                     (run_id,)):
                achadas = _objeto(ev["data"]).get("questions")
                if isinstance(achadas, list) and achadas:
                    perguntas = list(achadas)
                    break
        campos = tuple(dict.fromkeys(c for c in (_texto(q.get("field")) for q in perguntas if isinstance(q, dict))
                                     if c and c not in _CAMPOS_DE_DESTINO))
        return PerguntasAbertas(status=linhas.texto(run, "status"), comando=comando, perfis=perfis, campos=campos)


class PreferenciasDoLivro:
    """A porta `PreferenceSource` das habilidades, cumprida pelo livro. Só leitura, sem IA."""

    def __init__(self, servico: ServicoDePreferencias) -> None:
        self._servico = servico

    def preferred(self, command: str, candidates: Sequence[SkillMatch],
                  profile_ids: tuple[str | None, ...] | None) -> PreferenceHint | None:
        preferida = self._servico.preferida(tuple(c.ref.skill_id for c in candidates), profile_ids)
        if preferida is None:
            return None
        return PreferenceHint(preferida.skill_id, decide=preferida.decide, detail=preferida.detalhe)


def montar_preferencias(db: object, servico: object) -> ServicoDePreferencias | None:
    """O serviço das preferências sobre o banco do central (`None` quando o estado ainda não foi composto)."""
    if not isinstance(db, Database) or not isinstance(servico, LearningService):
        return None
    return ServicoDePreferencias(servico, SqlLearningRepository(db), PreferenciasSql(db), TriagemDeCredencial())


__all__ = ["PreferenciasDoLivro", "PreferenciasSql", "montar_preferencias"]
