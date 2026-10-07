"""Da gravação à habilidade (item 13.2): a IA propõe, a pessoa revisa, salvar vira fluxo + receitas com escopo.

- `propose`: manda a gravação (só texto: elemento tocado, tela, texto digitado quando pôde ser guardado) para a
  função de IA `generalize` e guarda a proposta. Custa uma chamada do modelo do planejador; no modo simulado, zero.
- `save`: a proposta (editada ou não pela pessoa) vira
  * um FLUXO — comando com `{parâmetros}` + plano; um comando parecido de qualquer perfil no escopo reaproveita o
    plano sem chamar o planejador;
  * RECEITAS por etapa, destiladas das entradas da pessoa com a mesma identidade que o replay procura (pacote,
    versão e assinatura do app, variante de interface, hash da etapa) — onde não der receita, a IA conduz a etapa;
  * ESCOPO: os perfis e grupos de acesso que recebem a habilidade (vazio = todos).

Guarda de política: num app com catálogo (o Instagram), toda etapa com efeito externo precisa ser uma AÇÃO do
catálogo. Sem isso a habilidade contornaria aprovação, limites e coordenação de frota — o portão só olha etapas
que declaram a ação.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple, get_args

from ..db import Row, dumps, loads
from ..modules.learning.domain.ciclo import ConflitoDeEstado, NotaComCaraDeSegredo, Vetado
from ..modules.learning.domain.licoes import CorrecaoSemReceita, Recusa, licao_da_correcao
from ..models import Plan, PlannerInfo, PlanStep, Postcondition
from ..planning.capabilities import CapabilityCatalog, CapabilityNode, load_catalog
from ..planning.training import TrainingRequest
from ..taskqueue.flows import PLACEHOLDER, RESERVED, ensinado_em_prova
from ..taskqueue.recipes import ReceitaVista, distill_training, step_template_hash
from ..security.redaction import redact
from ..util import now_iso
from . import correcao, dado_da_persona, lancador, partida
from .reparo_da_gravacao import marcar_telas
from .recorder import TrainingError
from .arraste import arrastes_finais, confirmou, pode_ser_receita
from .arraste import pergunta as pergunta_do_arraste
from .respostas import Resposta, acumular, chave_da_pergunta, guardadas, sem_as_respondidas, validar_respostas


# ---------------------------------------------------------------------------- validação da proposta (item 31.83)
#: O fluxo casa o pedido com `(?P<x>.+?)` e `fullmatch`: o parâmetro engole qualquer texto. Por isso o comando
#: tem de COMEÇAR por palavra fixa (`{pedido} no instagram` casa com todo pedido que termine em "no instagram") e ter
#: texto fixo suficiente fora das chaves para o pedido de outra pessoa não cair nele. Duas palavras e seis
#: letras/dígitos deixam passar moldes curtos e legítimos ("ligue para {contato}") e barram "siga {perfil}".
COMANDO_PALAVRAS_FIXAS_MIN = 2
COMANDO_CARACTERES_FIXOS_MIN = 6
_KINDS_DE_POSCONDICAO = frozenset(get_args(Postcondition.model_fields["kind"].annotation))
Sessao = dict[str, Any]         # linha de `training_sessions` com `inputs` e `proposal` já lidos
Proposta = dict[str, Any]       # proposta e etapa: JSON livre vindo do cliente, validado à mão abaixo


def _padrao_da_chave_de_etapa() -> re.Pattern[str]:
    """O padrão de `PlanStep.key`, achado pelo item de `metadata` que tem `pattern` (e não pela posição: outra
    restrição no campo mudaria a ordem)."""
    for restricao in PlanStep.model_fields["key"].metadata:
        if getattr(restricao, "pattern", None):
            return re.compile(restricao.pattern)
    raise RuntimeError("PlanStep.key deixou de ter `pattern`: a validação do salvar do treino precisa ser revista")


_CHAVE_DE_ETAPA = _padrao_da_chave_de_etapa()
_QUALQUER_CHAVE = re.compile(r"\{[^{}]*\}")                  # qualquer `{…}`, válido ou não
_NOME_DE_PARAMETRO = re.compile(r"[a-z_][a-z0-9_]*")           # o mesmo que `PLACEHOLDER` do fluxo aceita entre as chaves


def _e_texto_ou_nulo(v: object) -> bool:
    return v is None or isinstance(v, str)


def _e_inteiro(v: object) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


#: A tela de revisão ainda não deixa atribuir nem descartar entrada, nem editar etapa (31.90): nestes códigos a
#: única saída que existe hoje é pedir outra proposta. O comando é editável na tela, por isso `comando_generico` fica fora.
_REFAZER_A_PROPOSTA = frozenset({"entradas_sem_etapa", "entrada_duplicada", "entrada_inexistente", "proposta_invalida", "parametro_fora_do_comando", "parametro_nao_declarado",
                                 "pos_condicao_vazia", "etapa_invalida"})


def _erro(code: str, mensagem: str) -> TrainingError:
    if code in _REFAZER_A_PROPOSTA:
        mensagem = f"{mensagem} Peça uma nova proposta à IA."
    return TrainingError(code, mensagem, 400)


def validar_proposta_para_salvar(p: Proposta, seqs_gravados: set[int],
                                 etapa_do_catalogo: Callable[[Proposta], bool] = lambda _st: False,
                                 ) -> tuple[Proposta, list[str]]:
    """Confere a proposta que o cliente mandou ANTES de qualquer escrita: o `propose` normaliza, o `save` aceita o que
    vier. Devolve a proposta com `inputs`/`discarded` já como inteiros e os AVISOS do que foi aceito com ressalva.

    Recusa (400) com o que a pessoa deve corrigir: `etapa_invalida`, `parametro_invalido`, `parametro_reservado`,
    `parametro_fora_do_comando`, `parametro_nao_declarado`, `comando_generico`, `pos_condicao_vazia` (etapa com efeito
    externo), `entrada_duplicada` (em duas etapas, ou em etapa e em `discarded`: o `_receitas` tiraria o toque da etapa
    calado), `entradas_sem_etapa` (gravada e sem destino: a receita nasce sem o toque, errada e calada) e
    `entrada_inexistente` (`seq` fora das gravadas) e `proposta_invalida` (tipo errado: lista que não é lista, texto que
    não é texto).
    `etapa_do_catalogo(etapa)`: a etapa vira a ação do catálogo, que traz a própria pós-condição."""
    comando = (p.get("command_template") or "").strip()
    avisos: list[str] = []
    if not _e_texto_ou_nulo(p.get("summary")) or not _e_texto_ou_nulo(p.get("app_id")):
        raise _erro("proposta_invalida", "`summary` e `app_id` da proposta têm de ser texto.")
    for campo in ("steps", "parameters", "discarded"):
        if not (p.get(campo) is None or isinstance(p[campo], list)):
            raise _erro("proposta_invalida", f"`{campo}` da proposta tem de ser uma lista.")

    etapas: list[Proposta] = []
    chaves: set[str] = set()
    for n, st in enumerate(p.get("steps") or [], 1):
        if not isinstance(st, dict):
            raise _erro("etapa_invalida", f"A etapa {n} não está no formato esperado.")
        if not all(_e_texto_ou_nulo(st.get(c)) for c in ("key", "title", "goal", "capability", "app_id")):
            raise _erro("etapa_invalida", f"A etapa {n} tem chave, título, objetivo, ação do catálogo ou app que não são texto.")
        chave = str(st.get("key") or "").strip()
        rotulo = f"A etapa {n}" + (f" (“{st.get('title')}”)" if st.get("title") else "")
        if not chave:
            raise _erro("etapa_invalida", f"{rotulo} está sem chave: dê a cada etapa uma chave própria (minúsculas, números e _).")
        if not _CHAVE_DE_ETAPA.match(chave):
            raise _erro("etapa_invalida", f"{rotulo} tem a chave “{chave}” inválida: use minúsculas, números e _, "
                                          "começando por letra, com 2 a 41 caracteres.")
        if chave in chaves:
            raise _erro("etapa_invalida", f"A chave “{chave}” aparece em mais de uma etapa: cada etapa precisa de uma chave própria.")
        chaves.add(chave)
        if not (str(st.get("title") or "").strip() or str(st.get("goal") or "").strip()):
            raise _erro("etapa_invalida", f"{rotulo} está sem título e sem objetivo: diga o que ela faz.")
        post = st.get("postcondition") or {}
        if not isinstance(post, dict) or not all(_e_texto_ou_nulo(post.get(c)) for c in ("kind", "value", "description")):
            raise _erro("etapa_invalida", f"{rotulo} tem uma pós-condição fora do formato: tipo, valor e descrição são texto.")
        if post.get("kind") and post["kind"] not in _KINDS_DE_POSCONDICAO:
            raise _erro("etapa_invalida", f"{rotulo} tem uma pós-condição inválida: o tipo deve ser um de "
                                          f"{', '.join(sorted(_KINDS_DE_POSCONDICAO))}.")
        if not (st.get("side_effect") is None or isinstance(st["side_effect"], bool)):
            raise _erro("etapa_invalida", f"{rotulo} tem `side_effect` que não é verdadeiro/falso (o texto “false” contaria como verdadeiro).")
        if not (st.get("independente") is None or isinstance(st["independente"], bool)):
            raise _erro("etapa_invalida", f"{rotulo} tem `independente` que não é verdadeiro/falso.")
        brutas = st.get("inputs")
        if brutas is None:
            brutas = []
        if not isinstance(brutas, list) or not all(_e_inteiro(i) for i in brutas):
            raise _erro("etapa_invalida", f"{rotulo} tem `inputs` que não é uma lista de números de entrada gravada.")
        vinculos = st.get("bindings")
        if vinculos is not None and not (isinstance(vinculos, list) and all(isinstance(b, dict) for b in vinculos)):
            raise _erro("etapa_invalida", f"{rotulo} tem `bindings` que não é uma lista de objetos {{name, value}}.")
        etapas.append({**st, "key": chave, "inputs": sorted(set(brutas))})

    if not all(isinstance(x, dict) and _e_texto_ou_nulo(x.get("name")) for x in p.get("parameters") or []):
        raise _erro("proposta_invalida", "Cada item de `parameters` tem de ser um objeto com `name` em texto.")
    invalidos = [c for c in _QUALQUER_CHAVE.findall(comando) if not _NOME_DE_PARAMETRO.fullmatch(c[1:-1])]
    if invalidos:
        raise _erro("parametro_invalido",
                    "O comando tem " + ", ".join(invalidos) + ", que não é um nome de parâmetro válido: use minúsculas, "
                    "sem acento, números e _, começando por letra (ex.: {contato}). Do contrário o fluxo nunca casa.")
    sobras = _QUALQUER_CHAVE.sub("", comando)
    if "{" in sobras or "}" in sobras:
        raise _erro("parametro_invalido",
                    "O comando tem uma chave `{` ou `}` sem par: feche cada parâmetro (ex.: {contato}) ou tire a chave. "
                    "Do contrário o fluxo nunca casa.")
    declarados = {str(x["name"]) for x in p.get("parameters") or [] if x.get("name")}
    usados = {m.group(1) for m in PLACEHOLDER.finditer(comando)}
    # 31.100: `{instance_id}`, `{run_id}` e `{account_label}` são do sistema. No casamento do pedido
    # (`FlowStore._extract`) eles valem como texto literal, então o fluxo só casaria com quem digitasse as chaves.
    # No comando, recusa; nos parâmetros e nas etapas do plano continuam valendo (o `materialize` os resolve).
    if reservados := sorted(usados & RESERVED):
        raise _erro("parametro_reservado",
                    "O comando usa " + ", ".join("{" + n + "}" for n in reservados)
                    + ", que é do sistema (o aparelho, a execução ou a conta) e nunca aparece num pedido: o "
                    "fluxo só casaria com quem digitasse as chaves. Troque por texto fixo ou por um parâmetro "
                    "seu (ex.: {conta}).")
    fora = sorted(n for n in declarados - usados if n not in RESERVED)
    if fora:
        raise _erro("parametro_fora_do_comando",
                    "Parâmetro declarado mas ausente do comando: " + ", ".join("{" + n + "}" for n in fora)
                    + ". Ponha cada um no comando (ex.: “… {" + fora[0] + "} …”) ou tire-o da lista: sem isso o fluxo "
                    "nunca casa com um pedido.")
    sem_declarar = sorted(n for n in usados - declarados if n not in RESERVED)
    if sem_declarar:
        raise _erro("parametro_nao_declarado",
                    "O comando usa " + ", ".join("{" + n + "}" for n in sem_declarar) + " sem declarar o parâmetro: "
                    "acrescente-o em `parameters` com um exemplo, ou troque por texto fixo.")

    palavras = re.findall(r"[^\W_]+", _QUALQUER_CHAVE.sub(" ", comando))
    primeiro = re.search(r"\{[^{}]*\}|[^\W_]+", comando)
    if (primeiro is None or primeiro.group(0).startswith("{") or len(palavras) < COMANDO_PALAVRAS_FIXAS_MIN
            or sum(len(w) for w in palavras) < COMANDO_CARACTERES_FIXOS_MIN):
        raise _erro("comando_generico",
                    f"O comando “{comando}” casaria com pedidos alheios: comece pelo verbo (nunca por um {{parâmetro}}) "
                    f"e deixe ao menos {COMANDO_PALAVRAS_FIXAS_MIN} palavras fixas (e {COMANDO_CARACTERES_FIXOS_MIN} letras) "
                    "fora das chaves, como “responda a DM de {contato} com {mensagem}”.")

    for st in etapas:
        post = st.get("postcondition") or {}
        if (post.get("value") or "").strip() or (post.get("description") or "").strip() or etapa_do_catalogo(st):
            continue
        nome = st.get("title") or st["key"]
        if st.get("side_effect"):
            raise _erro("pos_condicao_vazia",
                        f"A etapa “{nome}” muda algo fora do aparelho e não diz como comprovar que deu certo: descreva "
                        "a pós-condição (o que a tela mostra depois).")
        avisos.append(f"A etapa “{nome}” não descreve a pós-condição: o objetivo dela serve de critério.")

    descartadas: list[Proposta] = []
    for d in p.get("discarded") or []:
        if not isinstance(d, dict) or not _e_inteiro(d.get("seq")):
            raise _erro("proposta_invalida", "Há uma entrada descartada sem o número da entrada (`seq`).")
        if any(d["seq"] == outra["seq"] for outra in descartadas):
            continue    # o mesmo descarte duas vezes não muda a receita: fica o primeiro, como a etapa faz com `inputs`
        descartadas.append(d)
    por_etapa: dict[int, int] = {}
    for st in etapas:
        for i in st["inputs"]:
            por_etapa[i] = por_etapa.get(i, 0) + 1
    seqs_descartados = [d["seq"] for d in descartadas]
    inexistentes = sorted(({*por_etapa} | {*seqs_descartados}) - seqs_gravados)
    if inexistentes:
        raise _erro("entrada_inexistente",
                    "Entradas que não existem nesta gravação: " + ", ".join(f"#{i}" for i in inexistentes)
                    + ". Use só os números das entradas gravadas.")
    descartes = set(seqs_descartados)
    duplicadas = sorted(i for i, n in por_etapa.items() if n > 1 or i in descartes)
    if duplicadas:
        raise _erro("entrada_duplicada",
                    "Entradas em mais de um lugar (em duas etapas, ou em uma etapa e em descartadas): "
                    + ", ".join(f"#{i}" for i in duplicadas) + ". Cada entrada fica em UMA etapa ou é descartada, "
                    "senão a receita perde o toque sem avisar.")
    cobertas = {i for st in etapas for i in st["inputs"]} | {d["seq"] for d in descartadas}
    orfas = sorted(seqs_gravados - cobertas)
    if orfas:
        raise _erro("entradas_sem_etapa",
                    "Entradas gravadas sem etapa e sem descarte: " + ", ".join(f"#{i}" for i in orfas)
                    + ". Atribua cada uma a uma etapa ou descarte-a: do contrário a receita nasce sem esse toque e "
                    "erra a reprodução.")
    return {**p, "steps": etapas, "discarded": descartadas}, avisos


JA_HAVIA_RECEITA = "já havia receita ativa para esta etapa"
Acoes = list[dict[str, Any]]    # o que `distill_training` devolve: as ações da receita (nunca vão ao cliente)


def _alvo_marcado(e: Mapping[str, object], dados: Mapping[str, str]) -> dict[str, object]:
    """31.148: o texto e a descrição do elemento tocado vão ao prompt com o marcador da persona, como os textos da tela.
    Sem alvo, nada (a entrada fica como veio)."""
    alvo = e.get("target")
    if not isinstance(alvo, dict):
        return {}
    return {"target": {**alvo, **{k: dado_da_persona.com_marcador(str(alvo[k]), dados)
                                  for k in ("text", "desc") if alvo.get(k)}}}


def _aviso_sem_persona(sess: Sessao) -> list[str]:
    """30.81: o ensinado só vale para a persona que ensinou até a prova; sem persona no treino, não vale em lugar nenhum."""
    if sess.get("profile_id"):
        return []
    return ["O treinamento não tinha persona no aparelho: a habilidade não vale em aparelho nenhum até ser provada."]


#: 31.88 F2: a escolha de escopo do salvar. `todos` (padrão): vale para quem o corpo disser (`profile_ids`/`group_ids`,
#: vazio = todos) quando a prova acabar a espera do 30.81. `quem_ensinou`: o escopo permanente é a persona do treino.
ESCOPO_TODOS = "todos"
ESCOPO_QUEM_ENSINOU = "quem_ensinou"
ESCOPOS_AO_PROVAR = (ESCOPO_TODOS, ESCOPO_QUEM_ENSINOU)


def _escopo_ao_provar(sess: Sessao, escolha: str, profile_ids: list[str],
                      group_ids: list[str]) -> tuple[list[str], list[str]]:
    """Os perfis e grupos que o `save` grava, pela escolha `scope_on_proof`. `quem_ensinou` não soma com uma lista
    explícita (seria dizer duas coisas) e exige persona na sessão (sem ela o escopo viraria "todos" em silêncio)."""
    if escolha not in ESCOPOS_AO_PROVAR:
        raise TrainingError("invalid_scope_on_proof", f"scope_on_proof deve ser {' ou '.join(ESCOPOS_AO_PROVAR)}.", 400)
    if escolha == ESCOPO_TODOS:
        return profile_ids, group_ids
    if profile_ids or group_ids:
        raise TrainingError("scope_ambiguous", "Escolha “só quem ensinou” OU a lista de perfis e grupos, não os dois.", 400)
    persona = sess.get("profile_id")
    if not persona:
        raise TrainingError("no_teacher_persona", "Este treinamento não tinha persona no aparelho: não há “quem ensinou”. "
                                                  "Escolha os perfis ou deixe o escopo em “todos”.", 409)
    return [str(persona)], []


def _escopo_da_resposta(prep: _Preparo, escolha: str) -> dict[str, object]:
    return {"on_proof": escolha, "profile_ids": prep.profile_ids, "group_ids": prep.group_ids}


@dataclass
class _Preparo:
    """A proposta já conferida e o plano montado, ainda sem gravar nada: o miolo comum do `save`, da prévia e do
    reparo (31.86)."""
    p: Proposta
    avisos: list[str]
    comando: str
    plano: Plan
    exemplos: dict[str, str]
    apps: dict[str, dict[str, str]]
    app_id: str | None
    #: O escopo que o `save` grava (31.88 F2): o que a pessoa escolheu, ou a persona que ensinou (`quem_ensinou`).
    profile_ids: list[str] = field(default_factory=list)
    group_ids: list[str] = field(default_factory=list)
    #: 31.122: as etapas cuja pós-condição `text_visible` já vale na tela de partida (a prévia avisa, o `save` recusa).
    ja_valem: list[dict[str, object]] = field(default_factory=list)
    #: Os dados da persona do treino, para a recusa do 31.122 sair com o marcador (nunca o valor).
    persona: dict[str, str] = field(default_factory=dict)


class _Destilada(NamedTuple):
    passo: PlanStep
    acoes: Acoes | None
    motivo: str


class TrainingSkills:
    def __init__(self, state: Any):
        self.s = state

    # ------------------------------------------------------------------ apoio
    def _apps(self) -> dict[str, dict[str, str]]:
        return {r["id"]: {"id": r["id"], "name": r["name"], "package": r["package"]}
                for r in self.s.db.query("SELECT id, name, package FROM apps ORDER BY name")}

    def _app_da_sessao(self, sess: Sessao, apps: dict[str, dict[str, str]]) -> str | None:
        if sess.get("app_id"):
            return sess["app_id"]
        for e in sess["inputs"]:
            if e["type"] == "open_app" and e.get("app_id") in apps:
                return e["app_id"]
            pkg = e.get("package")
            achado = next((a["id"] for a in apps.values() if a["package"] == pkg), None) if pkg else None
            if achado:
                return achado
        return None

    @staticmethod
    def _catalogo(pacote: str | None) -> list[dict[str, Any]]:
        cat = load_catalog(pacote) if pacote else None
        if cat is None:
            return []
        return [{"key": c.key, "title": c.title, "side_effect": c.side_effect, "bindings": list(c.bindings)}
                for c in cat.offered]

    # ------------------------------------------------------------------ proposta
    async def propose(self, session_id: str, body: object = None) -> dict[str, Any]:
        """`body`: o corpo opcional `{"answers": [...]}` já lido do JSON (31.91); sem ele, o comportamento de sempre.
        Sem corpo (ou com `answers` vazio) as respostas JÁ guardadas na proposta anterior são mantidas e reenviadas ao
        provedor. O UPDATE final só vale se a sessão não mudou desde a leitura (`proposta_concorrente`, 409)."""
        sess = self.s.training.get(session_id)
        if sess["status"] == "recording":
            raise TrainingError("still_recording", "Conclua a gravação antes de pedir a proposta.", 409)
        if sess["status"] in ("saved", "discarded"):
            raise TrainingError("closed", "Este treinamento já foi salvo ou descartado.", 409)
        if not sess["inputs"]:
            raise TrainingError("empty", "Nada foi gravado neste treinamento.", 400)
        # 31.91: a forma e o formato de segredo são conferidos ANTES do provedor e sem tocar na sessão; as respostas
        # já guardadas na proposta anterior são lidas aqui, porque o UPDATE abaixo troca a proposta inteira.
        # 31.112: a proposta lida já vem com o marcador; o corpo também passa pela troca, para a pergunta devolvida com
        # o valor (cliente que ainda mostrava a antiga) casar com a guardada.
        persona = self._persona_demonstrada(sess)
        if isinstance(body, dict):
            body = dado_da_persona.nas_perguntas(body, persona)
        respostas = acumular(guardadas(sess.get("proposal")), validar_respostas(body, sess.get("proposal")))
        apps = self._apps()
        app_id = self._app_da_sessao(sess, apps)
        pacote = apps[app_id]["package"] if app_id in apps else None
        tela = await self._tela_do_treino(sess)
        # 31.148: a tela inteira de cada entrada (31.122 F2) e o que apareceu depois dela vão ao prompt, com todo dado da
        # persona trocado pelo marcador; a IA escolhe a pós-condição com o que a etapa faz aparecer
        dados = self.s.repo.variaveis_da_persona(sess.get("profile_id"))
        elementos = self.s.training.elementos_da_tela(session_id)
        com_tela = [{**e, "screen_elements": elementos.get(int(e["seq"]))} for e in sess["inputs"]]
        textos = partida.textos_da_proposta(com_tela, dados)
        req = TrainingRequest(intent=sess["intent"], app_id=app_id, apps=list(apps.values()),
                              inputs=[{**e, **_alvo_marcado(e, dados),
                                       **({"textos_da_tela": textos[int(e["seq"])]} if int(e["seq"]) in textos else {})}
                                      for e in sess["inputs"]],
                              catalog=self._catalogo(pacote), session_id=session_id, answers=respostas, tela=tela)
        proposta, usage = await self.s.provider.generalize(req)
        try:
            self.s.repo.add_usage(None, None, usage)
        except Exception:  # noqa: BLE001 - contabilidade nunca derruba a proposta
            pass
        proposta["app_id"] = app_id
        proposta.pop("answers", None)
        # 31.87 F2: o dado da persona que a pessoa digitou vira o marcador, não parâmetro do comando nem literal.
        proposta, _ = dado_da_persona.na_proposta(proposta, persona)
        proposta = dado_da_persona.nas_perguntas(proposta, persona)   # 31.112: a pergunta nova da IA, ao guardar
        if respostas:           # sem resposta nenhuma a proposta fica como sempre foi (sem a chave)
            proposta["questions"] = sem_as_respondidas([q for q in proposta.get("questions") or [] if isinstance(q, str)],
                                                       respostas)
            proposta["answers"] = respostas
        # 31.114 F2: a tela fica na proposta guardada (a destilação a lê de lá) e o arraste que termina uma etapa, sem sair da
        # borda, leva uma pergunta FIXA: só a resposta "sim" da pessoa o faz virar receita.
        if tela:                                    # sem tela lida a proposta fica com as chaves de sempre
            proposta["screen"] = list(tela)
        extras = _perguntas_do_arraste(proposta, {int(e["seq"]): e for e in sess["inputs"]}, respostas, tela)
        if extras:
            proposta["questions"] = [*[q for q in proposta.get("questions") or [] if isinstance(q, str)], *extras]
        # 31.148: se mesmo assim a IA propôs uma pós-condição que já vale na partida, a proposta já volta com o alerta e
        # as sugestões prontas (31.142), a mesma lista da prévia; nada é trocado sem a pessoa
        passos = [st for st in proposta.get("steps") or [] if isinstance(st, dict)]
        ja_valem = partida.ja_valem(passos, com_tela, evitar=dados.values())
        if ja_valem:
            proposta["pos_condicoes_ja_valem"] = partida.estruturados(ja_valem, dados)
        # N3: dois `propose` da mesma sessão: o último UPDATE ganharia e as respostas do primeiro sumiriam calado
        cur = self.s.db.execute("UPDATE training_sessions SET proposal=?, status='proposed', updated_at=? "
                                "WHERE id=? AND updated_at=?", (dumps(proposta), now_iso(), session_id, sess["updated_at"]))
        if getattr(cur, "rowcount", 1) == 0:
            raise TrainingError("proposta_concorrente", "Outra proposta desta gravação terminou antes; peça de novo.", 409)
        return self.s.training.get(session_id)

    # ------------------------------------------------------------------ salvar, prévia e refazer (31.83, 31.86)
    def _preparar(self, sess: Sessao, session_id: str, proposal: Proposta | None, profile_ids: list[str],
                  group_ids: list[str], scope_on_proof: str = ESCOPO_TODOS) -> _Preparo:
        """Tudo que o `save` confere e monta ANTES da primeira escrita; a prévia chama a mesma função, então os dois
        recusam com os mesmos códigos. Não escreve nada."""
        if sess["status"] == "saved":
            raise TrainingError("closed", "Este treinamento já virou habilidade.", 409)
        p = proposal or sess.get("proposal")
        if proposal and isinstance(p, dict):
            # N1: `answers` mora na SESSÃO; o que o cliente mandar na proposta é ignorado (nem forjado, nem apagado)
            p = {k: v for k, v in p.items() if k != "answers"}
            if resp := guardadas(sess.get("proposal")):
                p["answers"] = resp
        if not p or not p.get("steps"):
            raise TrainingError("no_proposal", "Peça a proposta da IA (ou monte as etapas) antes de salvar.", 400)
        # 31.87 F2: a mesma troca da proposta, para a que a pessoa editou à mão; antes de conferir o comando.
        persona = self._persona_demonstrada(sess)
        p, marcadores = dado_da_persona.na_proposta(p, persona)
        p = dado_da_persona.nas_perguntas(p, persona)   # 31.112: a proposta editada pelo cliente pode trazer a pergunta
        if not isinstance(p.get("command_template"), (str, type(None))):
            raise TrainingError("invalid_command", "O comando da habilidade tem de ser um texto.", 400)
        comando = (p.get("command_template") or "").strip()
        if not comando:
            raise TrainingError("invalid_command", "A habilidade precisa de um comando.", 400)
        if re.search(r"\}\s*\{", comando):
            raise TrainingError("ambiguous_command", "Há dois parâmetros colados no comando (ex.: “{a} {b}”): coloque uma "
                                                     "palavra fixa entre eles, senão não dá para separar os valores.", 400)
        profile_ids, group_ids = _escopo_ao_provar(sess, scope_on_proof, profile_ids, group_ids)
        for pid in profile_ids:
            if self.s.social_repo.profile_row(pid) is None:
                raise TrainingError("unknown_profile", f"Perfil inexistente: {pid}.", 400)
        for gid in group_ids:
            if self.s.social_repo.policy_group_row(gid) is None:
                raise TrainingError("unknown_group", f"Grupo de acesso inexistente: {gid}.", 400)

        apps = self._apps()
        app_id = p.get("app_id") or self._app_da_sessao(sess, apps)
        pacote = apps[app_id]["package"] if app_id in apps else None

        def _catalogo_da_etapa(st: Proposta) -> CapabilityCatalog | None:
            pkg = apps[st.get("app_id") or app_id]["package"] if (st.get("app_id") or app_id) in apps else pacote
            return load_catalog(pkg) if pkg else None

        # 31.83: tudo que dá para recusar sai ANTES da primeira escrita (fluxo, escopo, receita, status da sessão).
        p, avisos = validar_proposta_para_salvar(
            p, {int(e["seq"]) for e in sess["inputs"]},
            lambda st: bool(st.get("capability")) and (cat := _catalogo_da_etapa(st)) is not None and cat.has(st["capability"]))
        exemplos = {x["name"]: str(x.get("example") or "") for x in p.get("parameters") or [] if x.get("name")}
        passos: list[PlanStep] = []
        # 31.123: os pacotes vizinhos em que a demonstração de cada etapa terminou (a busca do Configurações)
        por_seq = {int(e["seq"]): e for e in sess["inputs"]}
        fora = set(_inteiros([d.get("seq") for d in p.get("discarded") or [] if isinstance(d, dict)]))
        cadastrados = [a["package"] for a in apps.values()]
        vizinhos: list[tuple[str, list[str]]] = []
        for st in p["steps"]:
            app_da_etapa = st.get("app_id") or app_id
            pkg = apps[app_da_etapa]["package"] if app_da_etapa in apps else pacote
            da_etapa = [i for i in _inteiros(st.get("inputs")) if i in por_seq and i not in fora]
            # 31.123 F2: a tela em que a etapa TERMINA (a da entrada seguinte) também conta: o toque no app que abre
            # a busca de outro pacote termina lá (prova conjunta, r-20261006102728-1157c6)
            final = partida.entrada_seguinte(sess["inputs"], da_etapa, fora)
            aceitos = partida.pacotes_vizinhos([*(por_seq[i] for i in da_etapa), *([final] if final else [])], pkg,
                                               cadastrados)
            vizinhos.append((str(st.get("title") or st["key"]), aceitos))
            cat = load_catalog(pkg) if pkg else None
            cap = st.get("capability")
            if cat is not None and st.get("side_effect") and not (cap and cat.has(cap)):
                raise TrainingError(
                    "capability_required",
                    f"A etapa “{st.get('title')}” muda algo fora do aparelho em {apps.get(app_da_etapa, {}).get('name', pkg)}: "
                    "escolha a ação do catálogo que ela realiza. Sem isso a habilidade passaria por fora da aprovação "
                    "e dos limites do perfil.", 400)
            outro_app = app_da_etapa if app_da_etapa and app_da_etapa != app_id else None
            if cat is not None and cap and cat.has(cap):
                vinculos = {b["name"]: b["value"] for b in st.get("bindings") or [] if b.get("name")}
                passo = cat.build_step(CapabilityNode(key=st["key"], capability=cap, bindings=vinculos))
                passos.append(passo.model_copy(update={"key": st["key"], "app_id": outro_app, "pacotes_aceitos": aceitos}))
            else:
                post = st.get("postcondition") or {}
                passos.append(PlanStep(
                    key=st["key"], title=st.get("title") or st["key"], goal=st.get("goal") or st.get("title") or st["key"],
                    side_effect=bool(st.get("side_effect")), app_id=outro_app, max_attempts=1 if st.get("side_effect") else 3,
                    pacotes_aceitos=aceitos,
                    postcondition=Postcondition(kind=post.get("kind") or "model_judged", value=post.get("value") or "",
                                                description=post.get("description") or st.get("goal") or "")))
        passos = em_sequencia(passos, [st.get("independente") is True for st in p["steps"]])
        avisos = [*avisos, *self.s.scheduler.flows.colisoes(comando, exemplos)]            # 31.89 F4: só avisa
        # 31.122: a pós-condição que já vale na tela em que a etapa começa deixaria a etapa passar sem agir
        # Nenhum dado da persona (nem o que não foi digitado) entra nas sugestões nem no texto (marca, como no 31.87 F2)
        dados = self.s.repo.variaveis_da_persona(sess.get("profile_id"))
        elementos = self.s.training.elementos_da_tela(session_id)        # 31.122 F2: a tela inteira de cada entrada
        ja_valem = partida.ja_valem(p["steps"], [{**e, "screen_elements": elementos.get(int(e["seq"]))}
                                                 for e in sess["inputs"]],
                                    _inteiros([d.get("seq") for d in p.get("discarded") or [] if isinstance(d, dict)]),
                                    evitar=dados.values())
        plano = Plan(summary=(p.get("summary") or sess["intent"])[:200], app_id=app_id, app_package=pacote,
                     parameters={n: "{" + n + "}" for n in exemplos}, steps=passos,
                     planner=PlannerInfo(provider="treinamento", model=f"treinamento:{session_id}", simulated=False))
        # A destilação troca o valor digitado pelo nome: os parâmetros da pessoa primeiro, a persona no que sobrar.
        variaveis = {**exemplos, **{k: v for k, v in persona.items() if k not in exemplos}}
        return _Preparo({**p, "app_id": app_id},
                        [*avisos, *dado_da_persona.aviso(marcadores), *partida.aviso(ja_valem, dados),
                         *(dado_da_persona.com_marcador(linha, dados) for linha in partida.aviso_dos_vizinhos(vizinhos))],
                        comando, plano, variaveis, apps, app_id, profile_ids, group_ids, ja_valem, dados)

    async def save(self, session_id: str, *, proposal: Proposta | None, profile_ids: list[str],
                   group_ids: list[str], scope_on_proof: str = ESCOPO_TODOS) -> dict[str, object]:
        sess = self.s.training.get(session_id)
        prep = self._preparar(sess, session_id, proposal, profile_ids, group_ids, scope_on_proof)
        if prep.ja_valem:                     # 31.122: pede outra pós-condição ANTES da primeira escrita
            raise TrainingError("pos_condicao_ja_vale", " ".join(partida.aviso(prep.ja_valem, prep.persona)), 400,
                                extra={"pos_condicoes_ja_valem": partida.estruturados(prep.ja_valem, prep.persona)})
        try:
            flow_id = self.s.scheduler.flows.learn_from_plan(prep.plano, prep.comando, source=f"training:{session_id}")
        except ValueError as exc:
            raise TrainingError("duplicate_command", str(exc), 409) from None
        self.s.scheduler.flows.set_scope(flow_id, profile_ids=prep.profile_ids, group_ids=prep.group_ids)
        if sess.get("nascido_de_prova"):        # 31.130: o fluxo de uma sessão de prova leva a marca
            self.s.db.execute("UPDATE flows SET nascido_de_prova=1 WHERE id=?", (flow_id,))
        destiladas = _destilar(sess, prep.p, prep.plano.steps, prep.exemplos, prep.apps)
        relatorio = await self._relatorio(sess, destiladas, prep, session_id, gravar=True)
        # 31.149: a sessão de correção também grava a demonstração na chave da etapa que FALHOU
        ligacao = await self._ligar_a_falha(sess, destiladas, prep, session_id) if sess.get("origin") else None
        if ligacao is not None and not ligacao["ligada"]:       # o caminho alternativo: a lição do planejador
            ligacao["licao"] = self._licao_da_correcao(sess, destiladas, prep, session_id)
        self.s.db.execute("UPDATE training_sessions SET status='saved', flow_id=?, proposal=?, updated_at=? WHERE id=?",
                          (flow_id, dumps(prep.p), now_iso(), session_id))
        # 31.118: o dado da persona que a habilidade usa sai da gravação salva; fica o marcador (quem precisa do valor
        # o lê da persona, em memória: `dado_da_persona.com_valores`)
        persona = self._persona_demonstrada(sess)
        plano = prep.plano.model_dump_json()
        self.s.training.marcar_entradas(session_id, dado_da_persona.marcas_das_entradas(
            sess["inputs"], {n: v for n, v in persona.items() if "{" + n + "}" in plano}))
        # 31.118 F2: a tela gravada também mostra o dado (o campo preenchido); todo dado da persona vira o marcador nela
        marcar_telas(self.s.db, session_id, prep.persona)
        origem = {k: v for k, v in (sess.get("origin") or {}).items() if k in ("run_id", "step_id", "attempt_id")}
        self.s.bus.emit("log", f"Habilidade “{prep.plano.summary[:60]}” salva a partir do treinamento"
                               f"{' (correção de uma execução que falhou)' if origem else ''}",
                        data={"training_session_id": session_id, "flow_id": flow_id,
                              **({"origin": origem} if origem else {}),        # 31.111 F3: a trilha do ensino liga à execução
                              **({"correcao_ligada": bool(ligacao["ligada"]), "recipe_id": ligacao.get("recipe_id"),
                                  "licao_id": _id_da_licao(ligacao)} if ligacao is not None else {})})
        return {"session": self.s.training.get(session_id), "flow_id": flow_id, "steps": relatorio,
                "warnings": [*prep.avisos, *_aviso_sem_persona(sess)], "scope": _escopo_da_resposta(prep, scope_on_proof),
                **({"correcao": ligacao} if ligacao is not None else {}), **self._em_prova(flow_id)}

    async def _ligar_a_falha(self, sess: Sessao, destiladas: list[_Destilada], prep: _Preparo,
                             session_id: str) -> dict[str, object]:
        """31.149: grava a correção na chave da etapa que falhou (`steps.template_hash` e a chave dela), para a próxima
        execução do MESMO comando a achar ali. Regras em `correcao.py`; aqui só o banco, o aparelho e a loja. A resposta
        diz se ligou e, se não, o porquê; a revisão diz "esta correção vale para o comando <molde>"."""
        origem = sess.get("origin") or {}
        etapa = self.s.db.one("SELECT s.key, s.template_hash, s.side_effect, s.app_id, o.parameters, o.profile_id,"
                              " r.command FROM steps s JOIN objectives o ON o.id = s.objective_id"
                              " JOIN runs r ON r.id = s.run_id WHERE s.id=?", (origem.get("step_id"),))
        if etapa is None:
            return {"ligada": False, "motivo": "a etapa que falhou não existe mais: a limpeza das execuções a apagou"}
        chave = str(etapa["key"])
        base: dict[str, object] = {"ligada": False, "step_key": chave}
        if not etapa["template_hash"]:
            return {**base, "motivo": "a etapa que falhou não tem identidade de receita: as receitas estavam desligadas "
                                      "quando ela foi planejada"}
        indices = correcao.escolhidas([d.passo.key for d in destiladas], chave)
        if not indices:
            return {**base, "motivo": "a proposta não tem etapa ensinada"}
        if etapa["side_effect"] or any(destiladas[i].passo.side_effect for i in indices):
            return {**base, "motivo": "a etapa tem efeito: a pós-condição confere o caminho, não o commit, e a correção "
                                      "não é ligada à etapa que falhou"}
        sem_acao = [destiladas[i].passo.key for i in indices if not destiladas[i].acoes]
        if sem_acao:
            return {**base, "motivo": f"a etapa ensinada {sem_acao[0]} não virou ação reproduzível"}
        pacotes = {a["id"]: a["package"] for a in prep.apps.values()}
        ensinados = {pacotes.get(destiladas[i].passo.app_id or prep.app_id or "") or "" for i in indices}
        da_falha = self.s.db.scalar("SELECT package FROM apps WHERE id=?", (etapa["app_id"],)) if etapa["app_id"] else None
        if len(ensinados) != 1 or "" in ensinados or (da_falha and da_falha not in ensinados):
            return {**base, "motivo": "a correção não é do app da etapa que falhou (ou passa por mais de um app)"}
        pacote = ensinados.pop()
        parametros = {k: v for k, v in (loads(etapa["parameters"], {}) or {}).items() if isinstance(v, str)}
        persona = self.s.repo.variaveis_da_persona(etapa["profile_id"])
        acoes = correcao.renomear([a for i in indices for a in (destiladas[i].acoes or [])], prep.exemplos, parametros)
        falta = correcao.faltam(acoes, set(parametros) | set(persona))
        if falta:
            return {**base, "motivo": "a receita usaria " + ", ".join("{" + n + "}" for n in falta)
                                      + ", que a execução que falhou não tem"}
        identidade = await self._identidade(sess, pacote)
        if isinstance(identidade, str):
            return {**base, "motivo": identidade}
        versao, variante, assinatura = identidade
        loja = self.s.scheduler.executor.recipes
        hash_da_falha = str(etapa["template_hash"])
        efeito, viva = loja.previa_do_treino(pacote, versao, hash_da_falha, acoes, signature=assinatura, variant=variante)
        rid = loja.save(package=pacote, app_version=versao, step_hash=hash_da_falha, step_key=chave, actions=acoes,
                        learned_from=f"training:{session_id}", signature=assinatura, variant=variante)
        linha = _linha_da_receita(efeito, viva, gravada=rid if rid else 0)
        molde = redact(correcao.molde_do_comando(str(etapa["command"] or ""), {**parametros, **persona})) or ""
        return {"ligada": bool(linha["recipe"]) or linha["reason"] == JA_HAVIA_RECEITA, "step_key": chave,
                "recipe_id": rid or (int(viva["id"]) if viva is not None else None), "reason": linha["reason"],
                "comando": molde, "texto": f"esta correção vale para o comando “{molde}”"}

    def _licao_da_correcao(self, sess: Sessao, destiladas: list[_Destilada], prep: _Preparo,
                           session_id: str) -> dict[str, object]:
        """31.149, caminho alternativo: a correção que não ligou à etapa que falhou vira lição do PLANEJADOR do app
        (`licao_da_correcao`): "quando a etapa X falhar, o caminho que uma pessoa ensinou foi A → B". Nasce candidata
        de origem humana: só o dono a publica no Livro, e só publicada ela vai ao prompt. Não depende do 31.151."""
        origem = sess.get("origin") or {}
        etapa = self.s.db.one("SELECT s.key, s.side_effect, s.app_id, s.run_id, o.parameters, o.profile_id,"
                              " r.simulated FROM steps s JOIN objectives o ON o.id = s.objective_id"
                              " JOIN runs r ON r.id = s.run_id WHERE s.id=?", (origem.get("step_id"),))
        if etapa is None:
            return {"id": None, "motivo": "a etapa que falhou não existe mais"}
        chave = str(etapa["key"])
        indices = correcao.escolhidas([d.passo.key for d in destiladas], chave)
        pacote = (self.s.db.scalar("SELECT package FROM apps WHERE id=?", (etapa["app_id"],)) if etapa["app_id"]
                  else None) or (prep.apps[prep.app_id]["package"] if prep.app_id in prep.apps else "")
        do_objetivo = loads(etapa["parameters"], {}) or {}
        da_persona = self.s.repo.variaveis_da_persona(etapa["profile_id"])
        valores = [str(v) for fonte in (do_objetivo, prep.exemplos, da_persona) for v in fonte.values()
                   if isinstance(v, str)]
        proposta = licao_da_correcao(CorrecaoSemReceita(
            app=str(pacote or ""), chave=chave, caminho=tuple(destiladas[i].passo.key for i in indices),
            sessao=session_id, run_id=str(etapa["run_id"]), step_id=str(origem.get("step_id")),
            side_effect=bool(etapa["side_effect"]) or any(destiladas[i].passo.side_effect for i in indices),
            valores=tuple(valores), simulated=bool(etapa["simulated"])))
        if isinstance(proposta, Recusa):
            return {"id": None, "motivo": f"a lição foi recusada ({proposta.motivo.value})"}
        try:
            item = self.s.learning.propor(proposta)
        except (Vetado, NotaComCaraDeSegredo, ConflitoDeEstado) as exc:
            return {"id": None, "motivo": f"a lição não entrou no livro ({type(exc).__name__})"}
        return {"id": item.id, "estado": item.state.value, "texto": item.summary}

    async def _tela_do_treino(self, sess: Sessao) -> tuple[int, int] | None:
        """31.114 F1: o tamanho da tela, lido SÓ se há arraste com coordenada na gravação (é o único uso). O aparelho fora do
        ar, ou desconhecido, dá `None` e o texto do arraste diz "borda de origem desconhecida"."""
        if not any(e["type"] == "swipe" and e.get("y") is not None and e.get("y2") is not None for e in sess["inputs"]):
            return None
        try:
            return await self.s.devices.tamanho_da_tela(self.s.devices.get(sess["instance_id"]))
        except KeyError:
            return None

    async def preview(self, session_id: str, *, proposal: Proposta | None, profile_ids: list[str],
                      group_ids: list[str], scope_on_proof: str = ESCOPO_TODOS) -> dict[str, object]:
        """O que o `save` faria com esta proposta, SEM escrever (31.86): a mesma conferência (mesmos códigos), a mesma
        destilação e o mesmo relatório por etapa. É para a pessoa ver por que uma etapa ficaria sem receita enquanto
        ainda dá para corrigir a proposta. Nada vai ao banco: nem fluxo, escopo, receita, status ou evento."""
        sess = self.s.training.get(session_id)
        prep = self._preparar(sess, session_id, proposal, profile_ids, group_ids, scope_on_proof)
        # A única recusa do `save` que só aparece ao gravar (`learn_from_plan`): o comando repetido. Lida sem escrever,
        # pela MESMA regra do `save` (30.84: o ensinado que a prova desligou pode ser ensinado de novo). 31.142: vem no
        # corpo, junto do resto (o 409 parava a prévia e escondia as pós-condições e os avisos até a pessoa trocar o
        # comando, prova F2 de 06/10); o 409 fica só no `save`.
        recusa = self.s.scheduler.flows.recusa_do_treino(prep.comando)
        relatorio = await self._relatorio(sess, _destilar(sess, prep.p, prep.plano.steps, prep.exemplos, prep.apps),
                                          prep, session_id, gravar=False)
        return {"steps": relatorio, "warnings": [*([recusa] if recusa else []), *prep.avisos, *_aviso_sem_persona(sess)],
                # adendo v1.91: o código da recusa que o `save` daria (só `duplicate_command`; `null` se não há)
                "code": "duplicate_command" if recusa else None, "message": recusa,
                # adendo v1.86: as linhas de `warnings` do 31.122, estruturadas (o alerta dentro da etapa, 31.128)
                "pos_condicoes_ja_valem": partida.estruturados(prep.ja_valem, prep.persona),
                "scope": _escopo_da_resposta(prep, scope_on_proof)}

    async def refazer_receitas(self, session_id: str) -> dict[str, object]:
        """Repara uma sessão JÁ salva: destila de novo (com a proposta guardada) e grava a receita das etapas que ainda
        não têm (31.86). Serve ao fluxo salvo com o aparelho fora do ar, ou antes de uma regra de destilação mudar.
        Idempotente: onde já há receita ativa o `recipes.save` não grava outra (a política é a de sempre)."""
        sess = self.s.training.get(session_id)
        # 31.118: a gravação salva guarda o marcador; a destilação precisa do que foi digitado (o valor, em memória)
        sess = {**sess, "inputs": dado_da_persona.com_valores(
            sess["inputs"], self.s.repo.variaveis_da_persona(sess.get("profile_id")))}
        if sess["status"] != "saved" or not sess.get("flow_id"):
            raise TrainingError("sessao_nao_salva", "Este treinamento ainda não virou habilidade: salve-o antes de "
                                                    "refazer as receitas.", 409)
        fluxo = self.s.db.one("SELECT plan, status FROM flows WHERE id=?", (sess["flow_id"],))
        if fluxo is None:
            raise TrainingError("fluxo_inexistente", "A habilidade salva deste treinamento não existe mais.", 409)
        if fluxo["status"] == "disabled":
            raise TrainingError("fluxo_desligado", "A habilidade deste treinamento está desligada: ligue-a antes de "
                                                   "refazer as receitas.", 409)
        # As etapas vêm do PLANO DO FLUXO (a chave da receita é o hash delas, igual ao que o executor calcula na
        # reprodução); as entradas de cada etapa vêm da proposta guardada. Casam pela chave da etapa.
        plano = Plan.model_validate_json(fluxo["plan"])
        p = sess.get("proposal") or {}
        por_chave = {s.key: s for s in plano.steps}
        etapas = [st for st in p.get("steps") or [] if isinstance(st, dict) and st.get("key") in por_chave]
        exemplos = {str(x["name"]): str(x.get("example") or "") for x in p.get("parameters") or [] if x.get("name")}
        exemplos |= {k: v for k, v in self._persona_demonstrada(sess).items() if k not in exemplos}     # 31.87 F2
        apps = self._apps()
        prep = _Preparo({**p, "steps": etapas}, [], "", plano, exemplos, apps, plano.app_id)
        relatorio = await self._relatorio(
            sess, _destilar(sess, prep.p, [por_chave[st["key"]] for st in etapas], exemplos, apps), prep, session_id,
            gravar=True, so_chave_virgem=True)
        return {"session": self.s.training.get(session_id), "flow_id": sess["flow_id"], "steps": relatorio,
                "created": sum(1 for linha in relatorio if linha["recipe"]), **self._em_prova(sess["flow_id"])}

    def _persona_demonstrada(self, sess: Sessao) -> dict[str, str]:
        """31.87 F2: os dados (não sigilosos) da persona do treino que a pessoa digitou na demonstração."""
        return dado_da_persona.demonstrados(self.s.repo.variaveis_da_persona(sess.get("profile_id")), sess["inputs"])

    def _em_prova(self, flow_id: str) -> dict[str, object]:
        """30.81: `ensinado_em_prova` `{persona, sessao}` no topo da resposta enquanto a habilidade espera a prova (só
        vale para a persona que ensinou); ausente quando não se aplica."""
        row = self.s.db.one("SELECT * FROM flows WHERE id=?", (flow_id,))
        espera = ensinado_em_prova(self.s.db, row) if row is not None else None
        return {"ensinado_em_prova": espera} if espera is not None else {}

    async def _identidade(self, sess: Sessao, pacote: str) -> tuple[str, str, str] | str:
        """`(versão do app, variante de interface, assinatura)` que identificam a receita NESTE aparelho, ou o MOTIVO de
        não dar para saber agora. Versão e variante são a chave que o replay procura: se não forem as certas a receita
        nasce morta e o relatório diria "gravada" — por isso nada é chutado (nem da configuração do aparelho).

        Aparelho no ar: leitura de sempre. Fora do ar: o que a última leitura deixou (cache do executor e inventário do
        app, a mesma fonte do despacho); sem isso, o motivo diz o que falta e `refazer_receitas` repara depois."""
        rt = self.s.devices.devices.get(sess["instance_id"])
        if rt is None:
            return "o aparelho do treinamento não existe mais: a etapa fica com a IA até uma execução aprender"
        assinatura = self.s.db.scalar(
            "SELECT r.signature_sha256 FROM device_app_state d JOIN app_releases r ON r.id = d.installed_release_id"
            " WHERE d.instance_id=? AND d.package_name=?", (sess["instance_id"], pacote)) or ""
        if str(getattr(rt.state, "value", rt.state)) == "online":
            try:
                return await self.s.devices.app_version(rt, pacote), await self.s.devices.variant_of(rt), assinatura
            except Exception as exc:  # noqa: BLE001
                # 31.110: "online" pode ser um aparelho ligando ou sem ADB; a leitura estoura e o motivo diz isso, em vez
                # de parecer que a etapa não tem receita.
                return (f"o aparelho do treinamento não respondeu ao ler a versão do app ({exc}): ele está ligando, "
                        "parado ou sem ADB. Refaça as receitas quando ele estiver no ar")
        _, versao, _, variante = self.s.scheduler._chave_de_compatibilidade(rt, pacote)  # noqa: SLF001 - só lê
        if not versao or not variante:
            faltam = " e ".join(nome for nome, valor in (("a versão do app", versao), ("o idioma e a densidade da tela", variante))
                                if not valor)
            return (f"aparelho do treinamento fora do ar e {faltam} ainda não foi lido: a receita nasceria com a "
                    "identidade errada. Refaça as receitas quando ele voltar")
        return versao, variante, assinatura

    async def _relatorio(self, sess: Sessao, destiladas: list[_Destilada], prep: _Preparo, session_id: str,
                         *, gravar: bool, so_chave_virgem: bool = False) -> list[dict[str, object]]:
        """Uma linha por etapa: virou receita ou não, e o porquê. `gravar=False` (a prévia) responde o mesmo sem gravar:
        `recipe: true` quer dizer "seria gravada". `so_chave_virgem` (o reparo): não grava onde a chave já teve receita
        de qualquer status, porque o `recipes.save` do treino substituiria a quarentena por uma ativa nova."""
        pacotes = {a["id"]: a["package"] for a in prep.apps.values()}
        relatorio: list[dict[str, object]] = []
        identidades: dict[str, tuple[str, str, str] | str] = {}      # 31.110: uma leitura por app, não uma por etapa
        for d in destiladas:
            passo = d.passo
            # 31.140 (v1.89): os pacotes vizinhos em que a etapa conclui (31.123), por etapa, e não só a linha de `warnings`
            linha: dict[str, object] = {"key": passo.key, "title": passo.title, "recipe": False, "reason": d.motivo,
                                        "pacotes_aceitos": list(passo.pacotes_aceitos)}
            relatorio.append(linha)
            if not d.acoes:
                continue
            pkg = pacotes.get(passo.app_id or prep.app_id or "") or ""
            if pkg not in identidades:
                identidades[pkg] = await self._identidade(sess, pkg)
            identidade = identidades[pkg]
            if isinstance(identidade, str):
                linha["reason"] = identidade
                if so_chave_virgem and self.s.scheduler.executor.recipes.tem_ativa_em_qualquer_versao(
                        pkg, step_template_hash(passo)):
                    # A etapa já tem receita (o salvar a gravou): sem a versão do app não dá para conferir a chave, mas
                    # dizer "sem receita" seria falso.
                    linha["reason"] = (f"{JA_HAVIA_RECEITA}; o aparelho não respondeu agora à leitura da versão do app, "
                                       "então a chave não foi conferida de novo")
                continue
            versao, variante, assinatura = identidade
            hash_da_etapa = step_template_hash(passo)
            if so_chave_virgem:
                antes = self.s.scheduler.executor.recipes.status_da_chave(
                    pkg, versao, hash_da_etapa, signature=assinatura, variant=variante)
                if antes is not None:
                    linha["reason"] = (JA_HAVIA_RECEITA if antes in ("active", "validated")
                                       else f"a chave já teve receita (status {antes}): o reparo não a ressuscita")
                    continue
            if so_chave_virgem and self.s.scheduler.executor.recipes.caminho_vetado(ReceitaVista(
                    package=pkg, app_version=versao, signature=assinatura, variant=variante, step_hash=hash_da_etapa,
                    actions=dumps(d.acoes), learned_from=f"training:{session_id}")):
                # O `save` do treino pula este veto (a pessoa ensina agora); o reparo roda sem ela, então o respeita.
                linha["reason"] = "a pessoa vetou esta receita: o reparo não a recria"
                continue
            # 30.79: a demonstração substitui a receita que segura a etapa quando o caminho é outro. A prévia e o
            # salvar leem a MESMA conta da loja (`previa_do_treino`), e o texto diz qual receita saiu.
            loja = self.s.scheduler.executor.recipes
            efeito, viva = loja.previa_do_treino(pkg, versao, hash_da_etapa, d.acoes, signature=assinatura,
                                                 variant=variante)
            if not gravar:
                linha.update(_linha_da_receita(efeito, viva, gravada=None))
                continue
            rid = loja.save(package=pkg, app_version=versao, step_hash=hash_da_etapa, step_key=passo.key,
                            actions=d.acoes, learned_from=f"training:{session_id}", signature=assinatura,
                            variant=variante, so_em_chave_virgem=so_chave_virgem)
            linha.update(_linha_da_receita(efeito, viva, gravada=rid if rid else 0))
        return relatorio


def em_sequencia(passos: list[PlanStep], independentes: list[bool]) -> list[PlanStep]:
    """31.127: a etapa ensinada espera a ANTERIOR ser comprovada (`depends_on = [anterior]`), como a demonstração foi
    feita. Sem isso, com a 1ª em `retry_wait`, a 2ª ficava pronta e corria fora de ordem: na prova conjunta
    (r-20261006102728-1157c6) "digitar o termo" rodou antes de "abrir a busca" ser comprovada, e o objetivo fechou
    `completed` na tela inicial. A etapa com `independente: true` na proposta (a marca da pessoa) fica sem a
    dependência. A 1ª etapa, e a que já traz dependência (a ação do catálogo), ficam como estão."""
    saida: list[PlanStep] = []
    for n, passo in enumerate(passos):
        if n and not passo.depends_on and not (n < len(independentes) and independentes[n]):
            passo = passo.model_copy(update={"depends_on": [passos[n - 1].key]})
        saida.append(passo)
    return saida


def _inteiros(valores: object) -> list[int]:
    saida: list[int] = []
    for v in valores if isinstance(valores, list) else []:
        try:
            saida.append(int(v))
        except (TypeError, ValueError):
            continue
    return saida


def _linha_da_receita(efeito: str, viva: Row | None, *, gravada: int | None) -> dict[str, object]:
    """`recipe`/`reason` da etapa no relatório. `gravada=None` é a prévia (nada gravado); `0`, o `save` que não gravou.
    O mesmo caminho da receita que já vale não grava nada, nos dois (`recipe: false`, o motivo de sempre)."""
    if efeito == "ja_vale" or gravada == 0 or (gravada is not None and viva is not None
                                                and gravada == int(viva["id"])):
        return {"recipe": False, "reason": JA_HAVIA_RECEITA}
    troca = f", substituindo a v{viva['version']} (receita {viva['id']})" if efeito == "substitui" and viva else ""
    return {"recipe": True, "reason": ("receita será gravada ao salvar" if gravada is None else "receita gravada") + troca}


def _tela_guardada(proposta: object) -> tuple[int, int] | None:
    """A tela lida no `propose` (chave `screen` da proposta guardada), ou `None` se o aparelho não respondeu."""
    tela = proposta.get("screen") if isinstance(proposta, dict) else None
    if isinstance(tela, list) and len(tela) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in tela):
        return (tela[0], tela[1])
    return None


def _perguntas_do_arraste(proposta: Proposta, por_seq: Mapping[int, Mapping[str, object]], respostas: list[Resposta],
                          tela: tuple[int, int] | None) -> list[str]:
    """31.114 F2: uma pergunta fixa por etapa que termina num arraste que PODE virar receita e que a pessoa ainda não
    respondeu. Sem tela conhecida ou com saída de borda, não pergunta (nada a confirmar: não vira receita de qualquer jeito)."""
    descartadas = set(_inteiros([d.get("seq") for d in proposta.get("discarded") or [] if isinstance(d, dict)]))
    respondidas = {chave_da_pergunta(r["question"]) for r in respostas}
    abertas = {chave_da_pergunta(q) for q in proposta.get("questions") or [] if isinstance(q, str)}
    saida: list[str] = []
    for st in proposta.get("steps") or []:
        if not isinstance(st, dict) or not st.get("key"):
            continue
        do_passo = [por_seq[i] for i in _inteiros(st.get("inputs")) if i in por_seq and i not in descartadas]
        texto = pergunta_do_arraste(str(st["key"]))
        if pode_ser_receita(arrastes_finais(do_passo), tela) and chave_da_pergunta(texto) not in respondidas | abertas:
            saida.append(texto)
    return saida


def _destilar(sess: Sessao, p: Proposta, passos: list[PlanStep], exemplos: dict[str, str],
              apps: dict[str, dict[str, str]]) -> list[_Destilada]:
    """Uma destilação por etapa, das entradas da pessoa (só lê; o aparelho não entra aqui)."""
    por_seq = {int(e["seq"]): e for e in sess["inputs"]}
    descartadas = set(_inteiros([d.get("seq") for d in p.get("discarded") or [] if isinstance(d, dict)]))
    pacotes = {a["id"]: a["package"] for a in apps.values()}
    saida: list[_Destilada] = []
    guardada = sess.get("proposal") if isinstance(sess.get("proposal"), dict) else {}
    respostas = guardadas(guardada)
    tela = _tela_guardada(guardada)
    # 31.121: a gravação que começou dentro do app (sem abri-lo) ganha a abertura na receita da etapa da 1ª entrada
    vivas = sorted(s for s in por_seq if s not in descartadas)
    primeira = por_seq[vivas[0]] if vivas else None
    app_id = p.get("app_id") if isinstance(p.get("app_id"), str) else None
    # 31.139: a gravação que começou no lançador (gaveta, ícone) e entrou no app: o trecho do lançador vira `open_app`
    pacote_do_app = pacotes.get(app_id or "")
    pelo_lancador = lancador.abertura_pelo_lancador([por_seq[s] for s in vivas], pacote_do_app)
    for st, passo in zip(p["steps"], passos):
        entradas = [por_seq[i] for i in _inteiros(st.get("inputs")) if i in por_seq and i not in descartadas]
        entradas = lancador.sem_o_lancador(entradas, pelo_lancador, primeira, app_id, pacote_do_app)
        entradas = partida.com_abertura(entradas, primeira, app_id, pacote_do_app)
        # 31.114 F2: o arraste que termina a etapa só vira receita confirmado pela pessoa, com a tela conhecida e sem borda.
        final = confirmou(respostas, str(st.get("key"))) and pode_ser_receita(arrastes_finais(entradas), tela)
        acoes, motivo = distill_training(entradas, exemplos, side_effect=passo.side_effect, app_packages=pacotes,
                                         arraste_final=final)
        saida.append(_Destilada(passo, acoes, motivo))
    return saida


def _id_da_licao(ligacao: Mapping[str, object]) -> object:
    """31.149: o id da lição do planejador que a correção não ligada gerou (o evento leva só o id)."""
    licao = ligacao.get("licao")
    return licao.get("id") if isinstance(licao, dict) else None
