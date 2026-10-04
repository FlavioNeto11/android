"""30.61: a chave do item aprovado no plano, o que a execução (31.49) recalcula para saber se é o MESMO item.

Uma aprovação antecipada só afrouxa a parada, e só para item idêntico (desenho 30.61, §0.1). A chave junta tudo o que
muda o que sai para fora: quem faz (perfil, aparelho, app), o quê (a ação), para quem (a pessoa-alvo normalizada) e
sobre o quê (o OBJETO-alvo que a ação declara, 30.64), o texto exato que será digitado e a mídia pelo sha256 do
CONTEÚDO, mais o escopo (execução e objetivo). O texto entra literal: uma vírgula a mais é outro item.

A chave leva TODOS os argumentos que a ação declara (`bindings` e `optional_bindings`), não só o `objeto_alvo`: a mesma
resposta noutro comentário do mesmo @ é outro item, e a chave não depende de o catálogo estar bem declarado.

Falha fechado: sem chave (`None`), o item não se aprova no plano e fica para a execução. Isso acontece com:
- objeto-alvo não declarado, com argumento AUSENTE ou VAZIO (curtir "um post de @x", sem dizer qual: na execução o
  primeiro da grade pode ser outro), ou ainda por resolver (`{item}`, `{{saida:…}}`);
- alvo vazio quando a ação declara `counterparty`;
- qualquer argumento declarado ainda por resolver;
- texto ainda por escrever (briefing, rascunho na execução) ou com variável por resolver;
- imagem citada cujo sha256 não se conhece;
- ação cujo `objeto_alvo` declarado não basta para identificar o objeto do efeito (`OBJETO_INSUFICIENTE`).

Função pura: quem chama lê a etapa de novo e o sha256 da mídia (`midia_da_etapa`) e passa os valores.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from ..db import Database
from ..planning.capabilities import BRIEFING, TEXTO, VERBATIM, Capability, contraparte, objeto_da_acao, texto_a_gerar

#: Versão do formato da chave. Mudar o que entra muda a versão: chave velha nunca casa com chave nova.
VERSAO_DA_CHAVE = 1
#: O argumento da etapa que aponta a imagem da persona (29.30). Entra na chave pelo sha256 dos bytes, não pelo id.
ARGUMENTO_DA_IMAGEM = "image_id"
#: Argumentos que entram pela regra do texto (literal e final) ou da mídia, e não como argumento comum.
_PELA_REGRA_PROPRIA = frozenset({TEXTO, BRIEFING, VERBATIM, ARGUMENTO_DA_IMAGEM})
#: Ações cujo `objeto_alvo` declarado NÃO identifica o objeto do efeito: nunca se aprovam no plano (chave `None`; o item
#: fica `na_execucao` e a pergunta acontece lá, com o objeto à vista). Decisão da orquestradora, 04/10: REPLY_COMMENT
#: declara só `username`, e o mesmo @ pode ter vários comentários. Quando o catálogo ganhar o argumento que diz QUAL
#: comentário, a ação sai da lista (com o teste).
OBJETO_INSUFICIENTE = frozenset({"REPLY_COMMENT"})


#: O marcador de modelo que ainda não virou texto: `{item}`, `{nome}`, `{{saida:…}}` e a expressão do compilador de
#: habilidades `${ parameters.x }` (com ou sem espaço, `compiler.py::_EXPR`). Uma chave solta (emoticon, código) é texto
#: final (revisão do painel, B2 e N1).
_VARIAVEL = re.compile(r"\{\{|\$\{|\{[A-Za-z_][\w.:-]*\}")


def tem_variavel(texto: str) -> bool:
    return _VARIAVEL.search(texto) is not None


def texto_exato(cap: Capability, bindings: Mapping[str, object]) -> tuple[bool, str | None]:
    """`(fechado, texto)`. Fechado quando a etapa não escreve nada, ou escreve um texto já final (`content_verbatim`);
    aberto quando o texto ainda vai ser escrito (briefing): aí não há o que aprovar no plano."""
    valores = {k: v for k, v in bindings.items() if v is not None}
    if texto_a_gerar(valores) is not None:              # type: ignore[arg-type]
        return False, None
    texto = valores.get(TEXTO)
    if texto is None or not str(texto).strip():
        return (not cap.needs_draft), None
    if tem_variavel(str(texto)):                        # `{item}`, `{{saida:…}}`: ainda não é o texto que vai sair
        return False, None
    return True, str(texto)


def argumentos_da_acao(cap: Capability, bindings: Mapping[str, object]) -> dict[str, str] | None:
    """Todos os argumentos da etapa e os que a ação declara (`bindings` e `optional_bindings`; o declarado ausente entra
    vazio), fora os de texto e mídia, com o `counterparty` normalizado. Os da etapa entram mesmo sem declaração: a chave
    não depende de o catálogo estar bem declarado. `None` se algum ainda está por resolver."""
    argumentos: dict[str, str] = {}
    for nome in dict.fromkeys((*cap.bindings, *cap.optional_bindings, *sorted(bindings))):
        if nome in _PELA_REGRA_PROPRIA:
            continue
        bruto = bindings.get(nome)
        texto = "" if bruto is None else str(bruto).strip()
        # Mais estrito que `tem_variavel` DE PROPÓSITO (nota da Ferramentas, 04/10): um argumento (alvo, id, objeto)
        # com qualquer `{` não é o valor final que se aprova; falhar fechado aqui só faz o item perguntar na execução.
        if "{" in texto:
            return None
        argumentos[nome] = texto.lower().lstrip("@") if nome == cap.counterparty else texto
    return argumentos


#: 29.79: o argumento que diz se a publicação leva o rótulo de IA do Instagram ("true"/"false"), gravado pela central.
ARGUMENTO_DO_ROTULO_IA = "rotulo_ia"


def _id_da_imagem(bindings: Mapping[str, object]) -> str:
    valor = bindings.get(ARGUMENTO_DA_IMAGEM)
    return str(valor).strip() if valor is not None else ""


def midia_da_etapa(db: Database, bindings: Mapping[str, object], *,
                   perfil: str | None = None) -> tuple[bool, str | None]:
    """`(tem_imagem, sha256)`: a imagem que a etapa vai publicar, pelo sha256 dos BYTES (não pelo id). Imagem citada sem
    sha256 conhecido (ainda gerando, falhou, id que não existe) dá `(True, None)`, e quem chama falha fechado. Com
    `perfil`, a imagem de OUTRA persona também dá `(True, None)` (29.79): publicá-la na conta errada não se desfaz."""
    imagem = _id_da_imagem(bindings)
    if not imagem:
        return False, None
    if "{" in imagem:                   # mais estrito que `tem_variavel` de propósito: id de imagem não tem `{`
        return True, None
    if perfil is not None and imagem_de_outra_persona(db, bindings, perfil):
        return True, None
    sha = db.scalar("SELECT bytes_sha256 FROM persona_images WHERE id=? AND status='ready'", (imagem,))
    return True, (str(sha) if sha else None)


def imagem_de_outra_persona(db: Database, bindings: Mapping[str, object], perfil: str) -> bool:
    """A imagem citada existe e é de OUTRA persona que `perfil` (29.79)? `persona_images.persona_id` é o perfil."""
    imagem = _id_da_imagem(bindings)
    if not imagem or "{" in imagem:
        return False
    dona = db.scalar("SELECT persona_id FROM persona_images WHERE id=?", (imagem,))
    return dona is not None and str(dona) != perfil


def rotulo_ia_da_imagem(db: Database, image_id: str) -> str | None:
    """29.79: "true" se a imagem pede o rótulo de IA do Instagram, "false" se não; `None` se ela não existe. Só a
    enviada pelo dono (`upload`) pode sair sem rótulo: a gerada é foto realista de IA (regra do dono, 03/10) e a
    importada não tem origem conhecida, então leva o rótulo (o lado seguro). 29.81: o upload que o dono marcou como
    feito por IA também leva; o "não informado" sai sem, com o aviso."""
    linha = db.one("SELECT source, feita_por_ia FROM persona_images WHERE id=?", ((image_id or "").strip(),))
    if linha is None:
        return None
    if str(linha["source"]) != "upload":
        return "true"
    return "true" if linha["feita_por_ia"] is not None and bool(linha["feita_por_ia"]) else "false"


def rotulo_ia_exigido(db: Database, bindings: Mapping[str, object]) -> bool:
    """29.79, revisão R1 (em dúvida, não publica): o toque de publicar exige o rótulo de IA ligado? Decide pela ORIGEM da
    imagem que a etapa vai publicar, não pelo argumento gravado (a etapa criada antes do 29.79, ou com o `image_id`
    resolvido depois por `resolver_saidas`/`{item}`, não o tem). Sem imagem legível, imagem inexistente ou origem
    desconhecida: exige. Só o upload conhecido dispensa — e nem ele, se a etapa gravou "true"."""
    if str(bindings.get(ARGUMENTO_DO_ROTULO_IA) or "").strip().lower() == "true":
        return True
    imagem = _id_da_imagem(bindings)
    if not imagem or "{" in imagem:
        return True
    return rotulo_ia_da_imagem(db, imagem) != "false"


def chave_da_aprovacao(bindings: Mapping[str, object], cap: Capability, *, perfil: str, aparelho: str,
                       pacote: str | None, run_id: str, objective_id: str, tem_imagem: bool = False,
                       midia_sha256: str | None = None) -> str | None:
    """A chave (sha256 hex) do item, ou `None` quando ele não pode ser aprovado no plano (falha fechado).

    `bindings` são os da etapa RELIDA (depois de `resolver_saidas`, na execução). `tem_imagem`/`midia_sha256` vêm de
    `midia_da_etapa`."""
    if cap.key in OBJETO_INSUFICIENTE:
        return None                                     # o objeto declarado não diz qual é o objeto do efeito
    if tem_imagem and not str(bindings.get(ARGUMENTO_DO_ROTULO_IA) or "").strip():
        # 29.79, revisão R1: a etapa com imagem e SEM o rótulo gravado (criada antes do 29.79, ou imagem resolvida
        # depois) não fecha chave: o sim do plano não pode cobrir uma publicação cujo rótulo ninguém decidiu.
        return None
    objeto = objeto_da_acao(cap, bindings)
    if objeto is None or any(not valor for valor in objeto.values()):
        return None                                     # objeto não declarado, ausente, vazio ou por resolver
    alvo = contraparte(cap, bindings)
    if cap.counterparty and not alvo:
        return None
    argumentos = argumentos_da_acao(cap, bindings)
    if argumentos is None:
        return None
    fechado, texto = texto_exato(cap, bindings)
    if not fechado:
        return None
    if tem_imagem and not midia_sha256:
        return None
    conteudo = {"v": VERSAO_DA_CHAVE, "perfil": perfil, "aparelho": aparelho, "pacote": pacote or "",
                "capability": cap.key, "alvo": alvo or "", "objeto": objeto, "argumentos": argumentos,
                "texto": texto, "midia_sha256": midia_sha256 if tem_imagem else None,
                "run_id": run_id, "objective_id": objective_id}
    canonico = json.dumps(conteudo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()
