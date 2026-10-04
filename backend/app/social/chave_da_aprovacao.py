"""30.61: a chave do item aprovado no plano, o que a execução (31.49) recalcula para saber se é o MESMO item.

Uma aprovação antecipada só afrouxa a parada, e só para item idêntico (desenho 30.61, §0.1). A chave junta tudo o que
muda o que sai para fora: quem faz (perfil, aparelho, app), o quê (a ação), para quem (a pessoa-alvo normalizada) e
sobre o quê (o OBJETO-alvo que a ação declara, 30.64), o texto exato que será digitado e a mídia pelo sha256 do
CONTEÚDO, mais o escopo (execução e objetivo). O texto entra literal: uma vírgula a mais é outro item.

Falha fechado: sem chave (`None`), o item não se aprova no plano e fica para a execução. Isso acontece com objeto-alvo
não declarado ou ainda por resolver (`{item}`, `{{saida:…}}`), com o texto ainda por escrever (briefing, rascunho na
execução) e com uma imagem citada cujo sha256 não se conhece.

Função pura: quem chama lê a etapa de novo e o sha256 da mídia (`midia_da_etapa`) e passa os valores.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from ..db import Database
from ..planning.capabilities import TEXTO, Capability, contraparte, objeto_da_acao, texto_a_gerar

#: Versão do formato da chave. Mudar o que entra muda a versão: chave velha nunca casa com chave nova.
VERSAO_DA_CHAVE = 1
#: O argumento da etapa que aponta a imagem da persona (29.30).
ARGUMENTO_DA_IMAGEM = "image_id"


def texto_exato(cap: Capability, bindings: Mapping[str, object]) -> tuple[bool, str | None]:
    """`(fechado, texto)`. Fechado quando a etapa não escreve nada, ou escreve um texto já final (`content_verbatim`);
    aberto quando o texto ainda vai ser escrito (briefing): aí não há o que aprovar no plano."""
    valores = {k: v for k, v in bindings.items() if v is not None}
    if texto_a_gerar(valores) is not None:              # type: ignore[arg-type]
        return False, None
    texto = valores.get(TEXTO)
    if texto is None or not str(texto).strip():
        return (not cap.needs_draft), None
    return True, str(texto)


def midia_da_etapa(db: Database, bindings: Mapping[str, object]) -> tuple[bool, str | None]:
    """`(tem_imagem, sha256)`: a imagem que a etapa vai publicar, pelo sha256 dos BYTES (não pelo id). Imagem citada sem
    sha256 conhecido (ainda gerando, falhou, id que não existe) dá `(True, None)`, e quem chama falha fechado."""
    valor = bindings.get(ARGUMENTO_DA_IMAGEM)
    imagem = str(valor).strip() if valor is not None else ""
    if not imagem:
        return False, None
    if "{" in imagem:
        return True, None
    sha = db.scalar("SELECT bytes_sha256 FROM persona_images WHERE id=? AND status='ready'", (imagem,))
    return True, (str(sha) if sha else None)


def chave_da_aprovacao(bindings: Mapping[str, object], cap: Capability, *, perfil: str, aparelho: str,
                       pacote: str | None, run_id: str, objective_id: str, tem_imagem: bool = False,
                       midia_sha256: str | None = None) -> str | None:
    """A chave (sha256 hex) do item, ou `None` quando ele não pode ser aprovado no plano (falha fechado).

    `bindings` são os da etapa RELIDA (depois de `resolver_saidas`, na execução). `tem_imagem`/`midia_sha256` vêm de
    `midia_da_etapa`."""
    objeto = objeto_da_acao(cap, bindings)
    if objeto is None:
        return None
    fechado, texto = texto_exato(cap, bindings)
    if not fechado:
        return None
    if tem_imagem and not midia_sha256:
        return None
    conteudo = {"v": VERSAO_DA_CHAVE, "perfil": perfil, "aparelho": aparelho, "pacote": pacote or "",
                "capability": cap.key, "alvo": contraparte(cap, bindings) or "", "objeto": objeto,
                "texto": texto, "midia_sha256": midia_sha256 if tem_imagem else None,
                "run_id": run_id, "objective_id": objective_id}
    canonico = json.dumps(conteudo, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()
