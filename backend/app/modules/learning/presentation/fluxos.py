"""Os fluxos aprendidos (`/api/flows*`): listar, cobertura, "casa com este comando?", "parece com o fluxo tal", ligar e
desligar, escopo e apagar. Saíram de `api.py` no 15.15 F4 (corte 1) sem mudar caminho, método, corpo nem resposta.

Montado em `main.py` no MESMO lugar em que `api.router` entra. As rotas deste arquivo não se sobrepõem a nenhuma outra
(`/flows/{flow_id}` só tem PUT e DELETE; as literais são GET e POST).

Este módulo não importa `app.api` (ciclo): o estado é `request.app.state.poc`, tipado por `AppState` só para o verificador
(`TYPE_CHECKING`, como `execution/infrastructure/command_bus.py`), e o erro é o mesmo `{code, message}` de `api.err`.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.modules.learning.domain.uso_real import motivo_do_religamento
from app.modules.learning.domain.vocabulario import LivroKind
from app.modules.learning.presentation.livro import mudar_status_legado
from app.modules.skills.presentation.schemas import EscopoDoFluxoBody
from app.shared.costuras import autor_do_gesto
from app.social.capacidades import cobertura_do_fluxo, cobertura_dos_fluxos
from app.taskqueue.flows import id_do_fluxo

if TYPE_CHECKING:
    from app.state import AppState

router = APIRouter(prefix="/api")
#: 31.130: o tamanho máximo do motivo de quem liga ou desliga um fluxo (vai à trilha do livro).
MOTIVO_MAX = 300


def _st(request: Request) -> AppState:
    state: AppState = request.app.state.poc
    return state


def _err(status: int, code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


@router.get("/flows", response_model=None)
async def list_flows(request: Request, nascido_de_prova: bool | None = None) -> object:
    """`nascido_de_prova` (31.130): `true` só os fluxos de prova; `false` só os de uso real; sem ele, todos."""
    fluxos = _st(request).scheduler.flows.list()
    return fluxos if nascido_de_prova is None else [f for f in fluxos if f["nascido_de_prova"] is nascido_de_prova]


@router.get("/flows/cobertura", response_model=None)
async def flows_coverage(request: Request) -> object:
    """Cada fluxo com quantas etapas já têm receita ativa para a versão promovida do app: os "caminhos mapeados"
    do parque, e o custo de IA esperado ao repetir cada um (zero / parcial / total). Só leitura."""
    return cobertura_dos_fluxos(_st(request))


class FlowMatchBody(BaseModel):
    """Corpo de `POST /flows/match` (29.25): o rascunho do comando, que pode trazer e-mail e nunca deve ir para a URL
    (query string vira linha de log de acesso). O teto é o do comando de uma execução e de `/skills/resolve`."""
    model_config = ConfigDict(extra="forbid")
    command: str = Field(min_length=1, max_length=4000)


@router.post("/flows/match", response_model=None)
async def flows_match(request: Request, body: FlowMatchBody) -> object:
    """Item 7.7 ("quanto vai custar?" do Osintgram): o comando digitado casa com uma habilidade ou um fluxo
    conhecido? Devolve a cobertura e a estimativa em US$ do plano, ou `null` — sem nada casado não há o que estimar.

    Fase G (decisão P2): a MESMA resolução que o planejamento usa (`skill_planner`: habilidade publicada atrás de
    `skills.enabled`, depois fluxo ativo atrás de `ai.flows`), então a estimativa é do plano que REALMENTE rodaria.
    Mudança visível: antes a rota ignorava `ai.flows` e estimava um fluxo que a execução nunca usaria. Para
    habilidade, `flow_id` traz a versão (`ig.abrir_conversa@1`) e `skill_ref` diz que não é fluxo."""
    s = _st(request)
    casado = s.skill_planner.for_command(s.runs.sem_destinos(body.command), None)   # como a execução o vê (onda C)
    if casado is None or casado.plan is None:
        return None
    if casado.legacy_flow_id is not None:
        row = s.db.one("SELECT * FROM flows WHERE id=?", (casado.legacy_flow_id,))
        return cobertura_do_fluxo(s, row) if row is not None else None
    modelo = {"id": str(casado.ref), "app_id": casado.plan.app_id, "plan": casado.plan.model_dump_json()}
    return {**cobertura_do_fluxo(s, modelo), "skill_ref": str(casado.ref)}


@router.post("/flows/similar")
async def flows_similar(request: Request, body: FlowMatchBody) -> dict[str, object]:
    """31.89 F5: "este comando parece com o fluxo tal". Só pergunta: devolve até 3 fluxos ativos cujo texto fixo o
    comando contém (sem acento, caixa, artigo, com pequenas variações), com a referência pública, o molde e a nota. Se
    um fluxo já casa o comando por inteiro, a lista vem vazia e `matches` é verdadeiro. Não cria execução e não usa IA;
    o `/flows/match` (cobertura e custo) segue como era."""
    s = _st(request)
    comando = s.runs.sem_destinos(body.command)
    return {"matches": s.scheduler.flows.match(comando) is not None,
            "suggestions": s.scheduler.flows.parecidos(comando)}


@router.put("/flows/{flow_id}", response_model=None)
async def update_flow(request: Request, flow_id: str, patch: dict[str, object]) -> object:
    s = _st(request)
    flow_id = id_do_fluxo(s.db, flow_id)        # 30.83: o id ou a referência pública (o `href` dos avisos)
    linha = s.db.one("SELECT id, nascido_de_prova FROM flows WHERE id=?", (flow_id,))
    if linha is None:
        raise _err(404, "not_found", "Fluxo não encontrado.")
    if patch.get("status") not in ("active", "disabled"):
        raise _err(400, "invalid", "status deve ser 'active' ou 'disabled'.")
    status = str(patch["status"])
    # 31.130 (adendo v1.87): o motivo de quem liga ou desliga vai à trilha do livro ("fluxo de prova do 31.xxx, desligado
    # de propósito"); sem ele, o texto de sempre
    motivo = patch.get("motivo")
    if motivo is not None and (not isinstance(motivo, str) or not motivo.strip() or len(motivo.strip()) > MOTIVO_MAX):
        raise _err(400, "invalid", f"motivo deve ser um texto de 1 a {MOTIVO_MAX} caracteres.")
    # 31.150 (K-106): o fluxo de prova só volta ao uso real com o porquê, e pode mudar de escopo no mesmo gesto (a
    # prova o deixou em `quem_ensinou`). A trilha diz "religado para uso real: <motivo>"; a marca de origem fica.
    de_prova = bool(linha["nascido_de_prova"])
    if status == "active" and de_prova and motivo is None:
        raise _err(400, "motivo_obrigatorio", "Fluxo nascido de prova: diga por que ele volta ao uso real (motivo).")
    escopo = _escopo_do_patch(s, patch.get("escopo"))
    if escopo is not None and not (status == "active" and de_prova):
        raise _err(400, "invalid", "escopo neste PUT só vale ao religar um fluxo de prova; use PUT /api/flows/{id}/scope.")
    # Fase G (guarda apontada pela fase D): fluxo ADOTADO por uma habilidade publicada não se religa por aqui — o
    # mesmo comando ficaria vivo nos dois backends. Voltar ao fluxo é desfazer a adoção, que desabilita a versão
    # na mesma transação.
    # Fase J: nem por outra habilidade publicada com o MESMO comando (critério da fase: nenhum fluxo ativo e skill
    # publicada com o mesmo comando). A conferência e a escrita numa transação: no SQLite, a publicação concorrente
    # espera (BEGIN IMMEDIATE).
    with s.db.tx():
        if status == "active" and (adotante := s.skill_repo.published_adopter(flow_id)) is not None:
            raise _err(409, "flow_adopted", f"O fluxo foi adotado pela habilidade {adotante.ref}, que está publicada: "
                                           "religá-lo deixaria o mesmo comando vivo nos dois lugares. Desfaça a adoção "
                                           "para voltar ao fluxo.")
        chave = s.db.scalar("SELECT match_key FROM flows WHERE id=?", (flow_id,))
        if status == "active" and (outra := s.skill_repo.published_with_command(chave)) is not None:
            raise _err(409, "command_published", f"A habilidade {outra.ref} está publicada com o mesmo comando: "
                                                "religar o fluxo deixaria o comando vivo nos dois lugares. Desabilite "
                                                "a habilidade antes.")
        # ADR-054 (D1): pelo livro, na MESMA transação das guardas — status e trilha com a pessoa que decidiu, ou
        # nenhum dos dois. Sem a trilha, o fluxo que ela desligou aqui podia renascer do próximo plano (a última
        # linha da trilha seguia sendo a refutação do sistema) e o conteúdo não ficava vetado.
        antes = s.scheduler.flows.scope(flow_id) if escopo is not None else None
        if escopo is not None:
            s.scheduler.flows.set_scope(flow_id, profile_ids=escopo.profile_ids, group_ids=escopo.group_ids)
        mudar_status_legado(request, LivroKind.FLUXO, flow_id, status,
                            reason=motivo_do_religamento(motivo) if de_prova and status == "active"
                            and isinstance(motivo, str)
                            else motivo.strip() if isinstance(motivo, str)
                            else "ligado na lista de fluxos do painel" if status == "active"
                            else "desligado na lista de fluxos do painel")
    if antes is not None:
        _registrar_escopo(request, flow_id, antes)
    return next(f for f in s.scheduler.flows.list() if f["id"] == flow_id)


def _escopo_do_patch(s: AppState, bruto: object) -> EscopoDoFluxoBody | None:
    """O `escopo` opcional do PUT (31.150): `{profile_ids, group_ids}`, com as mesmas recusas do `/scope`."""
    if bruto is None:
        return None
    try:
        escopo = EscopoDoFluxoBody.model_validate(bruto)
    except ValidationError as exc:
        raise _err(400, "invalid", "escopo deve ser {profile_ids: [...], group_ids: [...]}.") from exc
    for pid in escopo.profile_ids:
        if s.social_repo.profile_row(pid) is None:
            raise _err(400, "unknown_profile", f"Perfil inexistente: {pid}.")
    for gid in escopo.group_ids:
        if s.social_repo.policy_group_row(gid) is None:
            raise _err(400, "unknown_group", f"Grupo de acesso inexistente: {gid}.")
    return escopo


def _registrar_escopo(request: Request, flow_id: str, antes: dict[str, list[str]]) -> None:
    """A trilha do escopo é o evento `log` com o antes, o depois e quem decidiu, como no `/scope`."""
    s = _st(request)
    depois = s.scheduler.flows.scope(flow_id)
    s.bus.emit("log", "Escopo da habilidade mudou", data={
        "flow_id": flow_id, "por": autor_do_gesto(getattr(request.state, "operador", None)),
        "antes": {"profile_ids": antes["profile_ids"], "group_ids": antes["group_ids"]},
        "depois": {"profile_ids": depois["profile_ids"], "group_ids": depois["group_ids"]}})


@router.put("/flows/{flow_id}/scope")
async def set_flow_scope(request: Request, flow_id: str, body: EscopoDoFluxoBody) -> dict[str, object]:
    """31.88 F2: a pessoa amplia ou restringe a quem o fluxo vale (vazio = todos). O escopo é distribuição, não
    conteúdo: não muda o status nem passa pelo livro (a decisão da prova é outra). A trilha é o evento `log` com o
    antes e o depois e quem decidiu."""
    s = _st(request)
    if s.db.one("SELECT id FROM flows WHERE id=?", (flow_id,)) is None:
        raise _err(404, "not_found", "Fluxo não encontrado.")
    for pid in body.profile_ids:
        if s.social_repo.profile_row(pid) is None:
            raise _err(400, "unknown_profile", f"Perfil inexistente: {pid}.")
    for gid in body.group_ids:
        if s.social_repo.policy_group_row(gid) is None:
            raise _err(400, "unknown_group", f"Grupo de acesso inexistente: {gid}.")
    antes = s.scheduler.flows.scope(flow_id)
    s.scheduler.flows.set_scope(flow_id, profile_ids=body.profile_ids, group_ids=body.group_ids)
    depois = s.scheduler.flows.scope(flow_id)
    quem = autor_do_gesto(getattr(request.state, "operador", None))
    s.bus.emit("log", "Escopo da habilidade mudou", data={
        "flow_id": flow_id, "por": quem,
        "antes": {"profile_ids": antes["profile_ids"], "group_ids": antes["group_ids"]},
        "depois": {"profile_ids": depois["profile_ids"], "group_ids": depois["group_ids"]}})
    return {"flow_id": flow_id, **depois}


@router.delete("/flows/{flow_id}", status_code=204)
async def delete_flow(request: Request, flow_id: str) -> Response:
    s = _st(request)
    flow_id = id_do_fluxo(s.db, flow_id)        # 30.83: antes da guarda da adoção, que lê pelo id
    # Fluxo adotado por uma habilidade é o caminho de volta da adoção (`release_flow` o religa): apagá-lo deixaria
    # a habilidade sem ter para onde desfazer. Desligar continua possível; apagar, só depois de desfazer.
    if (dona := s.skill_repo.adopter_id(flow_id)) is not None:
        raise _err(409, "flow_adopted", f"O fluxo foi adotado pela habilidade {dona}: apagá-lo tiraria o caminho de "
                                       "volta da adoção. Desfaça a adoção antes de apagar.")
    s.db.execute("DELETE FROM flows WHERE id=?", (flow_id,))
    return Response(status_code=204)
