"""31.138: passe único que põe a abertura do app na 1ª receita dos fluxos ensinados antes do 31.121 e do 31.139.

Achado da leitura dos fluxos ensinados (06/10): o único fluxo ensinado ativo e três desligados têm a 1ª receita só com
o toque, porque a gravação começou DENTRO do app (31.121) ou no lançador (31.139), e essas regras de destilação só valem
para o que se salva depois delas. Na reprodução, o aparelho está em outra tela e a receita diverge ("alvo ausente ou
ambíguo": 6 de 7 divergências, todas na 1ª ou 2ª etapa).

O passe destila de novo cada fluxo ensinado com a regra de hoje (a mesma `_destilar` do `save`) e, na etapa cuja
receita NOVA começa com `open_app`, troca a receita viva que não começa com ele. A troca é a do treino (30.79: a
demonstração substitui a receita viva da chave, na mesma versão, assinatura e variante dela), com a trilha no livro
(`TrilhaDasLojas`). Nada mais muda: nem o fluxo, nem o status dele, nem a gravação. O aparelho não entra: a identidade
da chave é a da receita que sai.
"""
from __future__ import annotations

from collections.abc import Callable

from ..db import Database, loads
from ..models import Plan
from ..modules.learning.domain.vocabulario import LivroKind
from ..modules.learning.infrastructure.ligar_nativos import TrilhaDasLojas
from ..taskqueue.recipes import MudancaDaReceita, ReceitaVista, RecipeStore, step_template_hash
from . import dado_da_persona
from .skills import Sessao, _destilar

PREFIXO = "training:"
#: Os status em que a receita segura a chave (a que a reprodução usa).
VIVAS = ("active", "validated")


class _Trilha:
    """O ouvinte mínimo da loja para o passe: registra a mudança no livro. Sem veto (a troca é a da demonstração, que a
    pessoa fez) e sem aviso ao dono (o passe roda pela operadora, com o relatório)."""

    def __init__(self, db: Database) -> None:
        self._trilha = TrilhaDasLojas(db)

    def vetada(self, receita: ReceitaVista) -> bool:          # noqa: ARG002 - a demonstração não passa pelo veto
        return False

    def exige_o_dono(self, recipe_id: int, receita: ReceitaVista) -> bool | None:   # noqa: ARG002
        return None

    def mudou(self, mudanca: MudancaDaReceita) -> None:
        self._trilha.registrar(LivroKind.RECEITA, str(mudanca.recipe_id), mudanca.de, mudanca.para,
                               motivo=mudanca.motivo, por=mudanca.por, run_id=None)


def _entradas(db: Database, sid: str) -> list[dict[str, object]]:
    """As entradas da sessão como o recorder as devolve à destilação (alvo e linhas da tela já lidos)."""
    saida = []
    for r in db.query("SELECT * FROM training_inputs WHERE session_id=? ORDER BY seq", (sid,)):
        d = dict(r)
        d["target"] = loads(d["target"])
        d["screen_lines"] = loads(d["screen_lines"], []) or []
        d["has_text"] = bool(d["has_text"])
        d["sensitive"] = bool(d["sensitive"])
        saida.append(d)
    return saida


def _sem_abertura(acoes: object) -> bool:
    return not (isinstance(acoes, list) and acoes and isinstance(acoes[0], dict) and acoes[0].get("tool") == "open_app")


def contar_sem_abertura(db: Database) -> int:
    """Quantas receitas VIVAS de 1ª etapa de fluxo ensinado começam sem `open_app` (a contagem antes e depois)."""
    n = 0
    for f in db.query("SELECT source, plan FROM flows WHERE source LIKE ? ORDER BY id", (PREFIXO + "%",)):
        passos = (loads(f["plan"], {}) or {}).get("steps") or []
        if not passos:
            continue
        chave = str(passos[0].get("key") or "")
        for r in db.query("SELECT actions FROM recipes WHERE learned_from_step=? AND step_key=? AND status IN (?,?)",
                          (f["source"], chave, *VIVAS)):
            n += int(_sem_abertura(loads(r["actions"], [])))
    return n


def abrir_nas_receitas(db: Database, variaveis: Callable[[str | None], dict[str, str]], *,
                       escrever: bool) -> dict[str, object]:
    """O passe. `variaveis(profile_id)`: os dados não sigilosos da persona (a destilação precisa do que foi digitado,
    que a gravação salva guarda com o marcador, 31.118). Devolve `{fluxos_lidos, antes, trocadas, depois, trocas}`;
    `trocas`: `[{fluxo, etapa, de, para}]` (ids)."""
    loja = RecipeStore(db, _Trilha(db))
    apps = {str(a["id"]): {"id": str(a["id"]), "name": str(a["name"] or a["id"]), "package": str(a["package"] or "")}
            for a in db.query("SELECT id, name, package FROM apps")}
    antes = contar_sem_abertura(db)
    lidos, trocas = 0, []
    for f in db.query("SELECT id, source, plan FROM flows WHERE source LIKE ? ORDER BY id", (PREFIXO + "%",)):
        sid = str(f["source"])[len(PREFIXO):]
        sessao = db.one("SELECT * FROM training_sessions WHERE id=? AND status='saved'", (sid,))
        if sessao is None:
            continue
        lidos += 1
        sess: Sessao = dict(sessao)
        sess["proposal"] = loads(sess["proposal"])
        dados = variaveis(sess.get("profile_id"))
        sess["inputs"] = dado_da_persona.com_valores(_entradas(db, sid), dados)
        p = sess["proposal"] if isinstance(sess["proposal"], dict) else {}
        plano = Plan.model_validate_json(str(f["plan"]))
        por_chave = {s.key: s for s in plano.steps}
        etapas = [st for st in p.get("steps") or [] if isinstance(st, dict) and st.get("key") in por_chave]
        exemplos = {str(x["name"]): str(x.get("example") or "") for x in p.get("parameters") or [] if x.get("name")}
        exemplos |= {k: v for k, v in dado_da_persona.demonstrados(dados, sess["inputs"]).items() if k not in exemplos}
        destiladas = _destilar(sess, {**p, "steps": etapas, "app_id": plano.app_id or p.get("app_id")},
                               [por_chave[st["key"]] for st in etapas], exemplos, apps)
        for d in destiladas:
            if not d.acoes or _sem_abertura(d.acoes):
                continue                  # só a etapa em que a regra de hoje põe a abertura
            pkg = apps.get(d.passo.app_id or plano.app_id or "", {}).get("package") or plano.app_package or ""
            for viva in db.query("SELECT id, app_version, app_signature, variant, actions FROM recipes WHERE app_package=?"
                                 " AND step_hash=? AND status IN (?,?) ORDER BY id",
                                 (pkg, step_template_hash(d.passo), *VIVAS)):
                if not _sem_abertura(loads(viva["actions"], [])):
                    continue
                troca: dict[str, object] = {"fluxo": str(f["id"]), "etapa": d.passo.key, "de": int(viva["id"]), "para": None}
                if escrever:
                    troca["para"] = loja.save(package=pkg, app_version=str(viva["app_version"]),
                                              step_hash=step_template_hash(d.passo), step_key=d.passo.key,
                                              actions=d.acoes, learned_from=f"{PREFIXO}{sid}",
                                              signature=str(viva["app_signature"] or ""), variant=str(viva["variant"] or ""))
                trocas.append(troca)
    return {"fluxos_lidos": lidos, "antes": antes, "trocadas": len(trocas),
            "depois": contar_sem_abertura(db) if escrever else antes - len(trocas), "trocas": trocas}


__all__ = ["abrir_nas_receitas", "contar_sem_abertura"]
