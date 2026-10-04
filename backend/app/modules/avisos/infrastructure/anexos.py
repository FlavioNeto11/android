"""O armazém dos anexos dos canais (item 28.24, F1; migração 101): o arquivo em `data/anexos/` e a linha em `canal_anexos`.

Regras (as do dono para os canais, `docs/dominios/canais.md` §6):
- o arquivo se chama pelo sha256 do conteúdo e pela extensão da lista de tipos (`<2 primeiros>/<sha256>.<ext>`). O nome
  que o remetente deu NUNCA entra: nem no disco, nem no banco, nem na resposta;
- o tipo vem do CONTEÚDO (assinatura), não do declarado; a divergência é recusa, e o que não está na lista não é guardado;
- a escrita é atômica (arquivo temporário e `os.replace`) e deduplicada: o mesmo conteúdo é um arquivo só;
- NADA aqui executa nem abre o arquivo por programa externo: só bytes entram e saem;
- o caminho que sai daqui só existe dentro da pasta do armazém (`caminho_em`): a referência é id ou sha256, e um caminho
  de fora é recusado (`CaminhoForaDoArmazem`). Link simbólico também.
"""
from __future__ import annotations

import hashlib
import logging
import os
import secrets
from collections.abc import Callable, Collection
from datetime import datetime, timedelta
from pathlib import Path

from app.db import Database
from app.modules.avisos.domain.anexos import EXTENSAO, detectar_mime, normalizar_mime, sha256_valido, tamanho_legivel
from app.util import to_iso

log = logging.getLogger("poc.avisos.anexos")

Linha = dict[str, object]
MAX_MOTIVO = 200


class CaminhoForaDoArmazem(Exception):
    """A referência não aponta para um arquivo do armazém (fora de `data/anexos`, link, sha malformado, tipo fora da lista)."""


class AnexoJaResolvido(Exception):
    """A linha `pendente` já fora resolvida (guardada ou recusada) quando se tentou resolvê-la de novo: dupla resolução
    (dois líderes lendo a mesma mensagem). Não é recusa do conteúdo; `linha` é o estado atual dela, para o chamador dizer
    ao dono o que aconteceu em vez de perder a resposta."""

    def __init__(self, linha: "Linha | None"):
        super().__init__("o anexo pendente já foi resolvido")
        self.linha = linha


class AnexoRecusado(Exception):
    """O conteúdo não pode ser guardado ou enviado. `motivo` é português simples e não ecoa o conteúdo nem o nome do arquivo."""

    def __init__(self, motivo: str):
        super().__init__(motivo)
        self.motivo = motivo


def caminho_em(pasta: Path, sha: str, mime: str) -> Path:
    """Onde mora o arquivo de `sha` e `mime` DENTRO de `pasta`. O destino é montado só de sha validado (64 hex) e de
    extensão da lista; ainda assim confere que, resolvido (links incluídos), continua dentro da pasta."""
    ext = EXTENSAO.get(mime)
    if ext is None or not sha256_valido(sha):
        raise CaminhoForaDoArmazem("referência de anexo inválida")
    alvo = pasta / sha[:2] / f"{sha}.{ext}"
    if not alvo.resolve().is_relative_to(pasta.resolve()):
        raise CaminhoForaDoArmazem("o anexo ficaria fora de data/anexos")
    return alvo


def _gravar_atomico(destino: Path, conteudo: bytes) -> bool:
    """Grava `conteudo` em `destino` sem deixar arquivo pela metade. False quando o arquivo já existia (a deduplicação:
    o mesmo sha256 é o mesmo conteúdo)."""
    if destino.is_file() and not destino.is_symlink():
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.parent / f".{destino.name}.{secrets.token_hex(4)}.tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(conteudo)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, destino)
    finally:
        tmp.unlink(missing_ok=True)
    return True


class ArmazemDeAnexos:
    def __init__(self, db: Database, pasta: Path, *, canal: str = "telegram",
                 relogio: Callable[[], datetime] | None = None):
        self.db = db
        self.pasta = pasta
        self.canal = canal
        self.relogio: Callable[[], datetime] = relogio if relogio is not None else db.agora

    def _agora(self) -> str:
        return to_iso(self.relogio())

    # ------------------------------------------------------------------ gravação
    def _inserir(self, *, entrada_id: int | None, direcao: str, estado: str, sha: str | None, mime: str | None,
                 tamanho: int, motivo: str | None, linha_id: int | None = None) -> Linha:
        if linha_id is not None:
            # Resolve a linha `pendente` (a mensagem já estava gravada, o anexo esperava o download): a referência sai.
            cur = self.db.execute(
                "UPDATE canal_anexos SET sha256=?, mime=?, bytes=?, estado=?, motivo_recusa=?, ref_externa=NULL,"
                " mime_declarado=NULL WHERE id=? AND estado='pendente'",
                (sha, mime, int(tamanho), estado, motivo[:MAX_MOTIVO] if motivo else None, int(linha_id)))
            linha = self.linha(int(linha_id))
            if linha is None or (cur.rowcount or 0) != 1:
                raise AnexoJaResolvido(linha)
            return linha
        ident = self.db.inserted_id(
            "INSERT INTO canal_anexos(canal, entrada_id, direcao, sha256, mime, bytes, estado, motivo_recusa, criado_em)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (self.canal, entrada_id, direcao, sha, mime, int(tamanho), estado,
             motivo[:MAX_MOTIVO] if motivo else None, self._agora()))
        return self._lida(int(ident))

    def _lida(self, ident: int) -> Linha:
        linha = self.linha(ident)
        if linha is None:                                    # invariante da gravação: checada mesmo com `python -O`
            raise RuntimeError("a linha do anexo gravada não foi encontrada")
        return linha

    def pendente(self, ref: str, *, entrada_id: int, mime_declarado: str | None, tamanho: int) -> Linha:
        """O anexo da mensagem do dono que vai ser baixado. Gravado ANTES do download, na mesma transação da mensagem: uma
        queda no meio deixa esta linha, e `a_retomar` a acha. `mime_declarado` só entra se for um tipo da lista."""
        ident = self.db.inserted_id(
            "INSERT INTO canal_anexos(canal, entrada_id, direcao, bytes, estado, criado_em, ref_externa, mime_declarado)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (self.canal, entrada_id, "entrada", int(tamanho), "pendente", self._agora(), ref,
             mime_declarado if mime_declarado in EXTENSAO else None))
        return self._lida(int(ident))

    def a_retomar(self, idade_s: float, limite: int = 20) -> list[Linha]:
        """Os `pendente` mais velhos que `idade_s`: a Central caiu entre gravar a mensagem e baixar o anexo."""
        desde = to_iso(self.relogio() - timedelta(seconds=idade_s))
        return [dict(r) for r in self.db.query(
            "SELECT * FROM canal_anexos WHERE canal=? AND estado='pendente' AND criado_em < ? ORDER BY id LIMIT ?",
            (self.canal, desde, limite))]

    def recusar(self, motivo: str, *, entrada_id: int | None = None, direcao: str = "entrada", tamanho: int = 0,
                mime: str | None = None, linha_id: int | None = None) -> Linha:
        """Registra o anexo que NÃO foi guardado (voz, tipo fora da lista, grande demais, divergente, falha ao baixar).
        `mime` só se for um da lista (o declarado é do remetente e não entra no banco)."""
        return self._inserir(entrada_id=entrada_id, direcao=direcao, estado="recusado", sha=None,
                             mime=mime if mime in EXTENSAO else None, tamanho=tamanho, motivo=motivo, linha_id=linha_id)

    @staticmethod
    def verificar(conteudo: bytes, *, tipos: Collection[str], max_bytes: int, mime_declarado: str | None = None) -> str:
        """O mime DETECTADO do conteúdo, ou `AnexoRecusado`: vazio, grande demais, fora da lista ou divergente do declarado.
        Não toca em disco nem em banco."""
        if not conteudo:
            raise AnexoRecusado("O arquivo está vazio.")
        if len(conteudo) > max_bytes:
            raise AnexoRecusado(f"Arquivo grande demais (o limite é {tamanho_legivel(max_bytes)}).")
        detectado = detectar_mime(conteudo)
        if detectado is None or detectado not in tipos:
            raise AnexoRecusado("O conteúdo não é um tipo que a Central guarda (imagem JPEG, PNG ou WEBP, PDF ou texto).")
        declarado = normalizar_mime(mime_declarado)
        if declarado is not None and declarado != detectado:
            raise AnexoRecusado("O conteúdo não bate com o tipo que o arquivo diz ter; não guardei.")
        return detectado

    def guardar(self, conteudo: bytes, *, tipos: Collection[str], max_bytes: int, entrada_id: int | None = None,
                direcao: str = "entrada", mime_declarado: str | None = None, linha_id: int | None = None) -> Linha:
        """Confere e guarda o conteúdo. Devolve a linha `guardado`; levanta `AnexoRecusado` (sem gravar nada em disco ou
        no banco) quando o conteúdo não serve. Quem chama registra a recusa com `recusar`."""
        detectado = self.verificar(conteudo, tipos=tipos, max_bytes=max_bytes, mime_declarado=mime_declarado)
        sha = hashlib.sha256(conteudo).hexdigest()
        destino = caminho_em(self.pasta, sha, detectado)
        try:
            novo = _gravar_atomico(destino, conteudo)
        except OSError as exc:
            log.error("anexos: não consegui gravar o arquivo (%s)", type(exc).__name__)
            raise AnexoRecusado("Não consegui guardar o arquivo na Central (erro de disco).") from None
        if not novo:
            log.info("anexos: conteúdo repetido; o arquivo existente é reaproveitado")
        linha = self._inserir(entrada_id=entrada_id, direcao=direcao, estado="guardado", sha=sha, mime=detectado,
                              tamanho=len(conteudo), motivo=None, linha_id=linha_id)
        if not novo:
            # O arquivo existia quando olhei, mas a faxina (28.16) pode tê-lo tirado do lugar entre aquela conferência e a
            # gravação desta linha. Com a linha já gravada ela o deixaria; se ele não está lá, grava de novo.
            try:
                _gravar_atomico(destino, conteudo)
            except OSError as exc:
                log.error("anexos: a linha %s ficou sem arquivo (%s)", linha.get("id"), type(exc).__name__)
        return linha

    # ------------------------------------------------------------------ leitura
    def linha(self, ident: int) -> Linha | None:
        r = self.db.one("SELECT * FROM canal_anexos WHERE id=?", (int(ident),))
        return dict(r) if r is not None else None

    def listar(self, *, canal: str | None = None, direcao: str | None = None, do_dono: bool | None = None,
               estado: str | None = None, desde: str | None = None, ate: str | None = None, limite: int = 24,
               deslocamento: int = 0) -> tuple[list[Linha], int]:
        """A página de anexos (a tela Anexos, 28.24 F4), do mais novo ao mais velho, e o total que bate com os filtros.
        `do_dono` olha a MENSAGEM de origem (`canal_entradas.do_dono`): só a entrada do dono conta; a saída é da Central.
        O que sai são só colunas seguras: nada de `ref_externa`, `mime_declarado` nem caminho (o produto não guarda o nome
        do remetente). O `pendente` (esperando o download) não é anexo ainda e não entra."""
        onde, par = ["a.estado <> 'pendente'"], []
        if canal:
            onde.append("a.canal = ?")
            par.append(canal)
        if direcao:
            onde.append("a.direcao = ?")
            par.append(direcao)
        if estado:
            onde.append("a.estado = ?")
            par.append(estado)
        if do_dono is True:
            onde.append("a.direcao = 'entrada' AND e.do_dono = 1")
        elif do_dono is False:
            onde.append("NOT (a.direcao = 'entrada' AND e.do_dono = 1)")
        if desde:
            onde.append("a.criado_em >= ?")
            par.append(desde)
        if ate:
            onde.append("a.criado_em < ?")
            par.append(ate)
        base = (" FROM canal_anexos a LEFT JOIN canal_entradas e ON e.canal = a.canal AND e.id = a.entrada_id"
                " WHERE " + " AND ".join(onde))
        total = int(self.db.scalar("SELECT COUNT(*)" + base, tuple(par)) or 0)
        linhas = self.db.query(
            "SELECT a.id, a.canal, a.direcao, a.mime, a.bytes, a.estado, a.motivo_recusa, a.criado_em, a.apagado_em,"
            " a.descricao, a.lida_em,"
            " CASE WHEN a.direcao = 'entrada' AND e.do_dono = 1 THEN 1 ELSE 0 END AS do_dono,"
            " CASE WHEN a.direcao = 'entrada' AND e.do_dono = 1 AND e.tipo = 'mensagem' THEN 1 ELSE 0 END AS de_mensagem_do_dono"
            + base + " ORDER BY a.id DESC LIMIT ? OFFSET ?", (*par, int(limite), int(deslocamento)))
        return [dict(r) for r in linhas], total

    def origem_e_de_convidado(self, anexo: Linha) -> bool:
        """A mensagem de origem existe e NÃO é do dono. O convidado nunca tem anexo baixado, então uma linha assim só
        existiria por defeito: o conteúdo não sai por nenhuma rota. Sem mensagem de origem (saída, teste), não é convidado."""
        if anexo.get("direcao") != "entrada" or anexo.get("entrada_id") is None:
            return False
        r = self.db.one("SELECT do_dono FROM canal_entradas WHERE canal=? AND id=?",
                        (anexo.get("canal"), int(str(anexo["entrada_id"]))))
        return r is not None and int(r["do_dono"]) != 1

    def da_entrada(self, entrada_id: int) -> list[Linha]:
        return [dict(r) for r in self.db.query(
            "SELECT * FROM canal_anexos WHERE canal=? AND entrada_id=? ORDER BY id", (self.canal, int(entrada_id)))]

    def abrir(self, ident: int) -> tuple[Linha, Path] | None:
        """A linha e o caminho do arquivo de um anexo `guardado`, ou None (não existe, foi recusado ou apagado, ou o
        arquivo sumiu do disco)."""
        linha = self.linha(ident)
        if linha is None or linha.get("estado") != "guardado":
            return None
        try:
            caminho = caminho_em(self.pasta, str(linha.get("sha256") or ""), str(linha.get("mime") or ""))
        except CaminhoForaDoArmazem:
            return None
        if not caminho.is_file() or caminho.is_symlink():
            return None
        return linha, caminho

    def conteudo_de(self, referencia: int | str | Path, *, tipos: Collection[str],
                    max_bytes: int) -> tuple[bytes, str, str]:
        """Os bytes que podem SAIR por um canal: `(conteudo, mime, sha256)`. A referência é o id do anexo, o sha256 de um
        anexo guardado ou um caminho DENTRO de `data/anexos`; qualquer outro caminho é `CaminhoForaDoArmazem`. O que
        sai é conferido de novo pelo conteúdo (tipo da lista e sha256 igual ao do nome)."""
        caminho: Path
        if isinstance(referencia, Path) or (isinstance(referencia, str) and not _e_id_ou_sha(referencia)):
            caminho = Path(referencia)
            real = caminho.resolve()
            if not real.is_relative_to(self.pasta.resolve()) or caminho.is_symlink():
                raise CaminhoForaDoArmazem("só sai arquivo que está em data/anexos")
            caminho = real
        else:
            if isinstance(referencia, str) and sha256_valido(referencia):
                r = self.db.one("SELECT id FROM canal_anexos WHERE sha256=? AND estado='guardado' ORDER BY id LIMIT 1",
                                (referencia,))
                ident = int(r["id"]) if r is not None else None
            else:
                ident = int(referencia)
            aberto = self.abrir(ident) if ident is not None else None
            if aberto is None:
                raise CaminhoForaDoArmazem("não há anexo guardado com essa referência")
            caminho = aberto[1]
        if not caminho.is_file():
            raise CaminhoForaDoArmazem("o arquivo não existe em data/anexos")
        if caminho.stat().st_size > max_bytes:
            raise AnexoRecusado("Arquivo grande demais para enviar.")
        dados = caminho.read_bytes()
        mime = detectar_mime(dados)
        if mime is None or mime not in tipos:
            raise AnexoRecusado("O conteúdo não é um tipo que a Central envia.")
        sha = hashlib.sha256(dados).hexdigest()
        # O nome É o sha256 do conteúdo (`<sha>.<ext>`): um `.tmp` que sobrou de `_gravar_atomico`, um arquivo que o
        # operador largou na pasta ou um conteúdo que mudou depois de guardado não saem por um canal.
        if caminho.name != f"{sha}.{EXTENSAO[mime]}":
            raise AnexoRecusado("O arquivo não é o conteúdo que o nome diz; não envio.")
        return dados, mime, sha

    def registrar_saida(self, sha: str, mime: str, tamanho: int, *, entrada_id: int | None = None) -> Linha:
        """A linha do que a Central mandou (a referência por id ou sha256 não grava de novo o arquivo)."""
        return self._inserir(entrada_id=entrada_id, direcao="saida", estado="guardado", sha=sha, mime=mime,
                             tamanho=tamanho, motivo=None)


def _e_id_ou_sha(texto: str) -> bool:
    return texto.isdigit() or sha256_valido(texto)
