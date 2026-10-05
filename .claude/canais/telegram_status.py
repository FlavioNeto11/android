"""Envia ao dono, pelo bot do Telegram configurado na plataforma, o texto de um arquivo (HTML do Telegram).

Usa o MESMO mecanismo do backend e de `scripts/avisos-telegram.py`: `EnvSettings` lê TELEGRAM_BOT_TOKEN e
TELEGRAM_CHAT_ID do `.env` do central e `CanalTelegram` faz o `sendMessage`. Nada do segredo é impresso: a saída
é só "enviado" ou o motivo da falha já limpo pelo adaptador.

O arquivo é texto em HTML do Telegram (<b>, <i>, <code>, <a href>); "<", ">" e "&" literais devem ir como
&lt; &gt; &amp;. Se o Telegram recusar o HTML (400), reenvia como texto puro, sem as tags, para a mensagem
nunca se perder.

Uso: backend/.venv/Scripts/python.exe .claude/canais/telegram_status.py <arquivo> [--reply-to <message_id>]

28.45, a imagem de uma etapa (só ao dono): `--foto <step_id> --previa <porta.json>`. Os bytes vêm pela MESMA regra da
porta (`PortasReais.imagem_da_etapa`: o `image_id` dos argumentos da etapa, lido do armazém dos avatares) e só saem se
o sha256 bater com o `imagem_sha256` daquela etapa na prévia gravada. O `<arquivo>` vira a legenda (texto puro, sem
HTML). Nada de caminho livre: a foto é sempre a da etapa.
"""
from __future__ import annotations

import argparse
import json
import unicodedata
import asyncio
import logging
import html
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r"C:\git\android")
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.config import EnvSettings  # noqa: E402

# O httpx loga a URL (com o token do bot) em INFO: fora do backend não há RedactingFilter, então cala o httpx.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
from app.modules.avisos.adapters.telegram import TEXTO_MAX, CanalTelegram, TokenAusente, _json  # noqa: E402
from app.modules.avisos.application.entrega import FalhaDeEnvio  # noqa: E402

_TAGS = re.compile(r"</?(b|i|u|s|code|pre|a)(\s[^>]*)?>")


def _segredo(valor: object) -> str:
    obter = getattr(valor, "get_secret_value", None)
    return str(obter()) if callable(obter) else ""


def _sem_tags(texto: str) -> str:
    return html.unescape(_TAGS.sub("", texto))


#: Quem pode receber fora do dono: só convidado registrado no arquivo local (fora do Git) com o chat já conhecido.
MEMBROS = ROOT / ".claude" / "handoffs" / "canais" / "membros-trello.json"     # fora do Git: vínculo id↔pessoa


def _chat_de_convidado(chat: str) -> bool:
    try:
        dados = json.loads(MEMBROS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return any(str(c.get("telegram_chat_id")) == chat for c in dados.get("convidados") or [] if c.get("telegram_chat_id"))


#: Modelo das boas-vindas a um convidado recém-autorizado (C-10). Sem nome de pessoa: no chat do convidado, o dono é
#: "o dono" (C-02; as boas-vindas de 03/10 22:53Z saíram com nomes e tiveram de ser editadas).
BOAS_VINDAS = ("Obrigada! O dono da Central confirmou que você é convidado dele, então pode falar comigo por aqui.\n\n"
               "Eu sou a ANA, a IA Gerente de Operações da Central de Aparelhos. Você pode me perguntar como andam as "
               "frentes, os prazos e o que mudou. Quando for um pedido, eu levo ao dono, e ele autoriza antes.")


def _nomes_proibidos() -> list[str]:
    """Nomes de pessoa que nunca vão ao chat de um convidado: os dos convidados e os do dono, do arquivo local
    (`nome` de cada convidado e `_nomes_do_dono`). Cada parte do nome com 3+ letras conta."""
    try:
        dados = json.loads(MEMBROS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    nomes = [str(c.get("nome") or "") for c in dados.get("convidados") or []] + list(dados.get("_nomes_do_dono") or [])
    # O nome da própria IA (ANA) não conta, mesmo que coincida com o de uma pessoa: a ANA sempre se apresenta (C-06).
    return sorted({p for n in nomes for p in re.findall(r"\w{3,}", n.lower())} - {"ana"})


#: O histórico de quem fala com o bot (dono, 03/10 22:44Z) leva também o que a ANA respondeu ao convidado.
CONTATOS = ROOT / ".claude" / "handoffs" / "canais" / "contatos-telegram.json"


def _historico_de_saida(chat: str, message_id: object, texto: str) -> None:
    try:
        contatos = json.loads(CONTATOS.read_text(encoding="utf-8"))
        entrada = contatos[str(chat)]
    except (OSError, ValueError, KeyError):
        return                                         # sem registro do chat: a caixa ainda não o viu; nada a anexar
    quando = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entrada.setdefault("historico", []).append({"quando": quando, "evento": "resposta_da_ana",
                                                "message_id": message_id, "texto": texto[:2000]})
    CONTATOS.write_text(json.dumps(contatos, ensure_ascii=False, indent=1), encoding="utf-8")


def _sem_acento(t: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", t) if unicodedata.category(ch) != "Mn")


def _abrir_repositorio_do_produto():  # noqa: ANN202 - o repositório do produto, aberto pela mesma config
    """O MESMO repositório que o produto usa para `canal_enviadas` (`EntradasDoCanal`), sobre o banco que a config da
    instalação aponta. Abrir não migra. Separado para o teste trocar por um falso."""
    from app.config import load_config
    from app.db import Database
    from app.modules.avisos.infrastructure.entrada_sql import EntradasDoCanal

    return EntradasDoCanal(Database(load_config().db_dsn), canal="telegram")


#: Uma opção de pergunta de escolha (28.44): um número ou UMA letra (decisão da orquestradora na leitura do #412). Palavra
#: nenhuma: "sim", "ok", "pode" ou "publica" como opção fariam a resposta solta parecer um aval.
_OPCAO = re.compile(r"^(?:\d{1,3}|[A-Za-z])$")
#: As letras que a conversa lê como sim ou não ("s", "n"): também não servem de opção.
_LETRAS_DE_AVAL = {"s", "n"}


def opcoes_da_escolha(valor: str) -> str:
    """`--escolha 1,2,3` → `1-2-3`, o detalhe do fato `escolha:<msg>:<opções>`. Pelo menos duas opções, sem repetir,
    números ou letras; fora disso, ValueError antes de qualquer envio."""
    opcoes = [o.strip() for o in valor.split(",") if o.strip()]
    if (len(opcoes) < 2 or len({o.lower() for o in opcoes}) != len(opcoes) or not all(_OPCAO.match(o) for o in opcoes)
            or any(o.lower() in _LETRAS_DE_AVAL for o in opcoes)):
        raise ValueError("--escolha pede de 2 opções em diante, separadas por vírgula, cada uma um número ou uma letra "
                         "(ex.: 1,2,3 ou A,B,C), sem S nem N")
    return "-".join(opcoes)


def _gravar_enviada(mid: object, abrir=None, *, escolha: str | None = None,  # noqa: ANN001
                    substitui: int | None = None) -> bool:
    """Grava a mensagem que o script mandou ao DONO em `canal_enviadas` (`origem='ana'`), para a resposta (reply) do dono a
    ela ser reconhecida como resposta à ANA, e não cair como texto livre. Falhar aqui NUNCA falha o envio: só avisa. Só
    o chat do dono (os `message_id` de um convidado podem colidir com os do dono na mesma tabela).

    28.44: com `escolha` (as opções já em `1-2-3`), o fato é `escolha:<mid>:<opções>`, e a resposta solta do dono pode
    casar com ela. Com `substitui`, a pergunta antiga deixa de estar aberta (`registrar_substituta`)."""
    try:
        if mid is None:
            return False
        repo = (abrir or _abrir_repositorio_do_produto)()
        repo.registrar_enviada(str(mid), "ana", fato=f"escolha:{mid}:{escolha}" if escolha else None)
        if substitui is not None:
            repo.registrar_substituta(str(mid), str(substitui))
        return True
    except Exception as exc:  # noqa: BLE001 - a mensagem já saiu; o banco não pode desfazer isso
        print(f"aviso: a mensagem saiu, mas não foi registrada em canal_enviadas ({type(exc).__name__})")
        return False


class FotoRecusada(Exception):
    """A imagem da etapa não pode sair: o motivo, sem segredo nem nome."""


def sha_da_previa(previa: dict[str, object], step_id: str) -> str:
    """O `imagem_sha256` da etapa na prévia da porta gravada (`itens[].step_id`)."""
    for item in previa.get("itens") or []:
        if isinstance(item, dict) and item.get("step_id") == step_id:
            sha = item.get("imagem_sha256")
            if isinstance(sha, str) and len(sha) == 64:
                return sha
            raise FotoRecusada("a etapa está na prévia, mas sem imagem_sha256")
    raise FotoRecusada("a etapa não está na prévia")


def imagem_conferida(step_id: str, sha_esperado: str, ler) -> tuple[bytes, str]:  # noqa: ANN001 - (run, step) → bytes
    """Os bytes da imagem da etapa e o mime pela assinatura, só se o sha256 bate com o da prévia (28.45)."""
    import hashlib

    from app.modules.avisos.domain.anexos import detectar_mime
    run_id = step_id.split(":", 1)[0]
    lida = ler(run_id, step_id)
    if lida is None:
        raise FotoRecusada("a etapa não tem imagem pronta no armazém")
    conteudo = lida[0]
    if hashlib.sha256(conteudo).hexdigest() != sha_esperado:
        raise FotoRecusada("o sha256 da imagem não bate com o da prévia")
    mime = detectar_mime(conteudo)
    if not (mime or "").startswith("image/"):
        raise FotoRecusada("o conteúdo não é uma imagem conhecida")
    return conteudo, str(mime)


def _da_central():  # noqa: ANN202 - (ler a imagem da etapa, ler o sha256 da prévia da porta), pelo banco e pelo armazém
    """As duas leituras da Central (28.46): a imagem da etapa, do armazém dos avatares, e o `imagem_sha256` que a prévia
    da porta mostra, pela mesma conta dela. Separado para o teste trocar por um falso."""
    from app.config import load_config
    from app.db import Database
    from app.modules.avisos.infrastructure.portas_da_central import imagem_da_etapa, sha_da_imagem_na_porta
    from app.storage import DISK, DiskStorage, build_storage
    cfg = load_config()
    env = cfg.env
    storage = build_storage(env.evidence_storage, evidence_dir=cfg.evidence_dir, bucket=env.s3_bucket,
                            endpoint_url=env.s3_endpoint_url, region=env.s3_region,
                            access_key=_segredo(env.s3_access_key_id) or None,
                            secret_key=_segredo(env.s3_secret_access_key) or None)
    avatares = DiskStorage(cfg.data_dir) if storage.name == DISK else storage
    db = Database(cfg.db_dsn)
    return (lambda run_id, step_id: imagem_da_etapa(db, avatares.get, run_id, step_id),
            lambda run_id, step_id: sha_da_imagem_na_porta(db, run_id, step_id))


def _canal_do_dono():  # noqa: ANN202 - o CanalTelegram do chat do dono, ou o motivo de não haver
    env = EnvSettings()
    chat = _segredo(env.telegram_chat_id)
    if not chat:
        return None, "TELEGRAM_CHAT_ID vazio no .env do central"
    try:
        return CanalTelegram(_segredo(env.telegram_bot_token), chat), None
    except TokenAusente as exc:
        return None, str(exc)


async def _enviar_foto(legenda: str, reply_to: int | None, step_id: str, previa: Path | None, *,
                       central=None, canal=None) -> int:  # noqa: ANN001 - os falsos do teste
    """28.45/28.46: a foto da etapa ao dono, só se o sha256 da imagem bate com o que a prévia da porta mostra, lido da
    PRÓPRIA Central. `previa` (o JSON gravado) é opcional: com ele, o arquivo também tem de bater com a Central."""
    run_id = step_id.split(":", 1)[0]
    try:
        ler, sha_na_porta = (central or _da_central)()
        sha = sha_na_porta(run_id, step_id)
    except Exception as exc:  # noqa: BLE001 - sem ler a Central, nada sai; o tipo basta (sem texto do banco)
        print(f"recusado, nada enviado: a Central não foi lida ({type(exc).__name__})")
        return 2
    if not sha:
        print("recusado, nada enviado: a prévia da porta não tem imagem_sha256 para esta etapa")
        return 2
    if previa is not None:
        try:
            sha_do_arquivo = sha_da_previa(json.loads(previa.read_text(encoding="utf-8")), step_id)
        except FotoRecusada as exc:
            print(f"recusado, nada enviado: {exc}")
            return 2
        except (OSError, ValueError) as exc:
            print(f"recusado, nada enviado: o arquivo da prévia não foi lido ({type(exc).__name__})")
            return 2
        if sha_do_arquivo != sha:
            print("recusado, nada enviado: o arquivo da prévia não é o que a Central mostra (sha256 diferente)")
            return 2
    try:
        conteudo, mime = imagem_conferida(step_id, sha, ler)
    except FotoRecusada as exc:
        print(f"recusado, nada enviado: {exc}")
        return 2
    except OSError as exc:
        print(f"recusado, nada enviado: a imagem não foi lida do armazém ({type(exc).__name__})")
        return 2
    except Exception as exc:  # noqa: BLE001 - sem a imagem conferida, nada sai
        print(f"recusado, nada enviado: a imagem não foi conferida ({type(exc).__name__})")
        return 2
    if canal is None:
        canal, motivo = _canal_do_dono()
        if canal is None:
            print(f"{motivo}; nada enviado")
            return 2
    try:
        mid = await canal.enviar_anexo(conteudo, mime, _sem_tags(legenda), responde_a=reply_to)
    except FalhaDeEnvio as exc:
        # Sem resposta do Telegram (tempo esgotado, conexão caída) ou com 5xx, a foto pode ter saído: repetir às cegas
        # manda duas. Recusa clara (4xx, definitiva) não saiu.
        incerta = not exc.definitiva and (exc.status is None or exc.status >= 500)
        print(f"Falhou: {exc.motivo}" + (". A foto pode ter saído: confira o chat antes de repetir." if incerta else ""))
        return 1
    print(f"enviada a foto ({mime}, sha256 {sha[:8]}…, {len(conteudo)} bytes) message_id={mid}")
    _gravar_enviada(mid)
    return 0


async def _enviar(texto: str, reply_to: int | None, chat: str | None = None, *, escolha: str | None = None,
                  substitui: int | None = None) -> int:
    env = EnvSettings()
    token = _segredo(env.telegram_bot_token)
    chat_id = _segredo(env.telegram_chat_id)
    if chat:
        if not _chat_de_convidado(chat):
            print("chat não registrado como convidado em membros-trello.json; nada enviado")
            return 2
        palavras = set(re.findall(r"\w+", _sem_acento(_sem_tags(texto).lower())))
        achados = [n for n in _nomes_proibidos() if _sem_acento(n) in palavras]
        if achados:
            # C-02: nome de pessoa só no chat do dono. Não imprime qual nome (o log fica no terminal da sessão).
            print(f"recusado: o texto para o convidado tem {len(achados)} nome(s) de pessoa; use 'o dono' e nada enviado")
            return 2
        chat_id = chat
    try:
        canal = CanalTelegram(token, chat_id)
    except TokenAusente as exc:
        print(str(exc))
        return 2
    if not chat_id:
        print("TELEGRAM_CHAT_ID vazio no .env do central.")
        return 2
    corpo: dict[str, object] = {"chat_id": chat_id, "text": texto[:TEXTO_MAX], "parse_mode": "HTML",
                                "disable_web_page_preview": True}
    if reply_to:
        corpo["reply_parameters"] = {"message_id": reply_to}
    try:
        resposta = await canal._chamar("sendMessage", json=corpo)
        if resposta.status_code == 400:
            # HTML recusado: manda o texto puro para a mensagem chegar de qualquer jeito.
            corpo.pop("parse_mode")
            corpo["text"] = _sem_tags(texto)[:TEXTO_MAX]
            resposta = await canal._chamar("sendMessage", json=corpo)
            modo = "texto puro (HTML recusado)"
        else:
            modo = "HTML"
        if resposta.status_code != 200 or _json(resposta).get("ok") is not True:
            raise canal._falha(resposta)
        mid = (_json(resposta).get("result") or {}).get("message_id")
        print(f"enviado {modo} ({len(texto)} chars) message_id={mid}")
        if chat:
            _historico_de_saida(chat, mid, _sem_tags(texto))
        elif not _gravar_enviada(mid, escolha=escolha, substitui=substitui) and (escolha or substitui is not None):
            # A pergunta saiu sem a marca: a resposta solta não casa com ela, e a substituída segue aberta. Erro visível,
            # para quem mandou registrar à mão ou avisar (leitura do #412).
            print("ERRO: a mensagem saiu, mas a marca da escolha ou da substituição não foi gravada; a resposta solta "
                  "não vai casar com ela")
            return 3
        return 0
    except FalhaDeEnvio as exc:
        print(f"Falhou: {exc.motivo}")
        return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arquivo")
    ap.add_argument("--reply-to", type=int, default=None)
    ap.add_argument("--chat", default=None, help="convidado registrado (membros-trello.json); sem isto, vai ao dono")
    ap.add_argument("--titulo", default=None, help="compatibilidade: vira a 1ª linha em negrito")
    ap.add_argument("--foto", default=None, metavar="STEP_ID", help="28.45: manda a imagem desta etapa (só ao dono)")
    ap.add_argument("--previa", default=None, help="opcional (28.46): a prévia da porta gravada (JSON); se vier, tem de bater com a da Central")
    ap.add_argument("--escolha", default=None, metavar="OPÇÕES",
                    help="28.44: a mensagem pede uma escolha (ex.: 1,2,3); a resposta solta do dono casa com ela")
    ap.add_argument("--substitui", type=int, default=None, metavar="MESSAGE_ID",
                    help="28.44: esta mensagem substitui a pergunta de escolha anterior, que deixa de estar aberta")
    args = ap.parse_args()
    escolha = None
    if args.escolha is not None or args.substitui is not None:
        if args.chat or args.foto:
            print("--escolha e --substitui valem só para texto ao dono; nada enviado")
            return 2
        try:
            escolha = opcoes_da_escolha(args.escolha) if args.escolha is not None else None
        except ValueError as exc:
            print(f"{exc}; nada enviado")
            return 2
    texto = Path(args.arquivo).read_text(encoding="utf-8").strip()
    if not texto:
        print("arquivo vazio")
        return 2
    if args.titulo:
        texto = f"<b>{html.escape(args.titulo)}</b>\n{texto}"
    if args.foto:
        if args.chat:
            print("--foto vai só ao dono; nada enviado")
            return 2
        return asyncio.run(_enviar_foto(texto, args.reply_to, args.foto, Path(args.previa) if args.previa else None))
    return asyncio.run(_enviar(texto, args.reply_to, args.chat, escolha=escolha, substitui=args.substitui))


if __name__ == "__main__":
    raise SystemExit(main())
