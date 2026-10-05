"""O vigia da borda (29.97): de hora em hora, o central pede o site e o painel pelo nome público, como um visitante, e
confere o que a borda da Cloudflare fez com eles.

Os dois defeitos do portal na semana de 05/10 nasceram de configuração da ZONA, que muda a qualquer hora e não só no
deploy: o beacon do Web Analytics injetado na raiz e no painel (29.85, 29.91) e o CSS e o JS guardados por 4 h (29.95).
A prova de fora só roda quando alguém lembra; o vigia roda sozinho. O que é defeito é decidido pela régua comum
(`domain/borda.py`), a mesma da prova de fora.

Uma volta faz no máximo 4 GET, um por conferência, sem retry e sem credencial: a raiz e o painel como navegador, e o
CSS e o JS pelo endereço com `?v=` que a raiz aponta. O estado fica em memória: a saúde só o lê, sem pedir nada à rede.
O aviso ao dono sai pela Canais (`avisar_borda_do_portal`, contrato dela) uma vez por código e por dia, e o "não
consegui conferir" só depois de N voltas seguidas.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from app.modules.portal.domain import borda
from app.modules.portal.domain.borda import Achado, Desfecho

log = logging.getLogger("poc.portal")

#: O caminho conferido → o `onde` do contrato da Canais.
ONDE_DO_AVISO: Mapping[str, str] = {"/": "raiz", "/central/": "painel", "/assets/site.css": "css",
                                    "/assets/site.js": "js"}


@dataclass(frozen=True, slots=True)
class Resposta:
    status: int
    cabecalhos: Mapping[str, str]
    corpo: bytes


class SemResposta(Exception):
    """A rede, o tempo esgotado ou o nome que não resolve: a volta não diz nada sobre a página."""


class Buscar(Protocol):
    def __call__(self, url: str, *, aceita: str | None) -> Resposta: ...


class AvisarBorda(Protocol):
    """`avisos.avisar_borda_do_portal` (contrato da Canais, 29.97). Devolve um `ContatoAvisado` (`enfileirado`)."""

    def __call__(self, codigo: str, onde: str, agora: datetime, *, achado: str | None = None,
                 horas_sem_conferir: int | None = None) -> object: ...


def _pedir(buscar: Buscar, url: str, aceita: str | None) -> Resposta | Desfecho:
    try:
        return buscar(url, aceita=aceita)
    except SemResposta as exc:
        return borda.sem_conferir(str(exc) or "sem resposta")


def uma_volta(buscar: Buscar, *, host: str, site_ligado: bool, csp_do_painel: str) -> Desfecho:
    """As conferências de uma volta, juntas. O painel sempre; a raiz e a versão do CSS e do JS só com o site ligado."""
    base = f"https://{host}"
    desfechos: list[Desfecho] = []

    painel = _pedir(buscar, base + "/central/", borda.ACEITA)
    if isinstance(painel, Desfecho):
        desfechos.append(painel)
    else:
        desfechos += _pagina("/central/", painel, host, sem_transformar=True,
                             csp=None if csp_do_painel == "desligada" else csp_do_painel)
    if not site_ligado:
        return borda.juntar(desfechos)

    raiz = _pedir(buscar, base + "/", borda.ACEITA)
    if isinstance(raiz, Desfecho):
        desfechos.append(raiz)
        return borda.juntar(desfechos)
    desfechos += _pagina("/", raiz, host, sem_transformar=True, gzip_da_origem=True, csp="site", sem_cookie=True)
    if raiz.status != 200 or not _corpo_legivel(raiz):
        return borda.juntar(desfechos)
    html = _texto(raiz)
    versoes = borda.versoes_pedidas(html)
    for caminho in ("/assets/site.css", "/assets/site.js"):
        versao = versoes.get(caminho)
        if versao is None:
            desfechos.append(borda.conferir_versao(caminho, None))
            continue
        # Só gzip: um `brotli` que entre no venv não pode virar corpo ilegível e `versao_divergente` falso (V3).
        arquivo = _pedir(buscar, f"{base}{caminho}?v={versao}", "gzip")
        if isinstance(arquivo, Desfecho):
            desfechos.append(arquivo)
        else:
            desfechos.append(borda.conferir_versao(caminho, versao, arquivo.status, arquivo.corpo))
    return borda.juntar(desfechos)


def _pagina(onde: str, resposta: Resposta, host: str, *, sem_transformar: bool = False, gzip_da_origem: bool = False,
            csp: str | None = None, sem_cookie: bool = False) -> list[Desfecho]:
    """A página e os cabeçalhos dela. Fora do 200 só vale o `pagina_fora` do HTML: um desafio da borda (403) não tem
    `no-transform`, CSP nem gzip, e conferir cabeçalho nele viraria 3 ou 4 avisos com gesto errado (V1). Corpo em br
    ou zstd não se lê aqui: a borda abriu e recomprimiu, e o `html_transformado` dos cabeçalhos já diz isso."""
    if resposta.status != 200:
        return [borda.conferir_html(onde, resposta.status, "", host=host)]
    desfechos = [borda.conferir_cabecalhos(onde, resposta.status, resposta.cabecalhos, sem_transformar=sem_transformar,
                                           gzip_da_origem=gzip_da_origem, csp=csp, sem_cookie=sem_cookie)]
    if _corpo_legivel(resposta):
        desfechos.append(borda.conferir_html(onde, resposta.status, _texto(resposta), host=host))
    elif not any(a.codigo == borda.HTML_TRANSFORMADO for d in desfechos for a in d.achados):
        codificacao = borda.cabecalho(resposta.cabecalhos, "content-encoding")
        desfechos.append(borda.corpo_recomprimido(onde, codificacao))
    return desfechos


def _corpo_legivel(resposta: Resposta) -> bool:
    codificacao = borda.cabecalho(resposta.cabecalhos, "content-encoding").lower()
    return codificacao in ("", "identity", "gzip")          # o cliente HTTP desfaz o gzip


def _texto(resposta: Resposta) -> str:
    return resposta.corpo.decode("utf-8", "replace")


class Vigia:
    """O estado entre voltas, em memória: a última volta, as voltas seguidas sem conferir e o dia em que cada código já
    foi avisado. Um processo, um vigia; só o líder dá voltas (o laço confere)."""

    def __init__(self, buscar: Buscar, *, host: Callable[[], str | None], site_ligado: Callable[[], bool],
                 csp_do_painel: Callable[[], str], voltas_sem_conferir: Callable[[], int],
                 intervalo_s: Callable[[], int], avisar: Callable[[], AvisarBorda | None]) -> None:
        self._buscar = buscar
        self._host = host
        self._site_ligado = site_ligado
        self._csp_do_painel = csp_do_painel
        self._n = voltas_sem_conferir
        self._intervalo_s = intervalo_s
        self._avisar = avisar
        self.ultima: Desfecho | None = None
        self.quando: datetime | None = None
        self.seguidas_sem_conferir = 0
        self._avisado_em: dict[str, date] = {}

    def volta(self, agora: datetime) -> Desfecho | None:
        """Uma volta inteira. `None` quando não há nome público (nada roda)."""
        host = self._host()
        if not host:
            return None
        desfecho = uma_volta(self._buscar, host=host, site_ligado=self._site_ligado(),
                             csp_do_painel=self._csp_do_painel())
        self.ultima, self.quando = desfecho, agora
        if desfecho.estado == borda.SEM_CONFERIR:
            self.seguidas_sem_conferir += 1
            log.warning("portal: vigia da borda sem conferir (%s), %s volta(s) seguida(s)", desfecho.motivo,
                        self.seguidas_sem_conferir)
            if self.seguidas_sem_conferir >= self._n():
                # O aviso diz o código (`borda-502`, `tempo-esgotado`): o filtro da Canais só deixa [A-Za-z0-9._/-].
                codigo = desfecho.motivo.split(",", 1)[0].strip().replace(" ", "-")
                self._avisar_uma_vez(borda.SEM_CONFERIR, "raiz", agora, achado=codigo or None, horas=self._horas())
            return desfecho
        self.seguidas_sem_conferir = 0
        self._avisado_em.pop(borda.SEM_CONFERIR, None)
        if desfecho.estado == borda.OK:
            log.info("portal: vigia da borda ok")
            self._avisado_em.clear()
            return desfecho
        log.warning("portal: vigia da borda achou defeito: %s",
                    "; ".join(f"{a.codigo} em {a.onde}" for a in desfecho.achados))
        presentes: dict[str, Achado] = {}
        for achado in desfecho.achados:
            presentes.setdefault(achado.codigo, achado)
        for codigo in [c for c in self._avisado_em if c not in presentes]:
            del self._avisado_em[codigo]                      # resolvido: se voltar, avisa de novo
        for codigo, achado in presentes.items():
            self._avisar_uma_vez(codigo, ONDE_DO_AVISO.get(achado.onde, "raiz"), agora, achado=achado.item or None)
        return desfecho

    def _horas(self) -> int:
        return max(1, math.ceil(self.seguidas_sem_conferir * self._intervalo_s() / 3600))

    def _avisar_uma_vez(self, codigo: str, onde: str, agora: datetime, *, achado: str | None = None,
                        horas: int | None = None) -> None:
        """Uma vez por código e por dia UTC (a chave da Canais também é por dia). Se a Canais não enfileirou (canal
        desligado, falha), tenta de novo na volta seguinte."""
        dia = agora.date()
        if self._avisado_em.get(codigo) == dia:
            return
        avisar = self._avisar()
        if avisar is None:
            return                                            # sem a função da Canais: fica só na saúde e no log
        try:
            resposta = avisar(codigo, onde, agora, achado=achado, horas_sem_conferir=horas)
        except Exception as exc:  # noqa: BLE001 - o vigia nunca derruba o laço por causa do aviso
            log.error("portal: aviso da borda (%s) não saiu: %s", codigo, type(exc).__name__)
            return
        if getattr(resposta, "enfileirado", False):
            self._avisado_em[codigo] = dia
        else:
            log.warning("portal: aviso da borda (%s) não entrou na fila: %s", codigo, getattr(resposta, "motivo", "?"))

    def problemas(self) -> list[tuple[str, str, str]]:
        """Para o `/api/health`: só o estado da última volta, nenhum pedido de rede aqui."""
        achados: list[tuple[str, str, str]] = []
        quando = f"{self.quando:%H:%M}Z" if self.quando else "?"
        if self.ultima is not None and self.ultima.estado == borda.DEFEITO:
            detalhes = "; ".join(f"{a.onde}: {a.detalhe}" for a in self.ultima.achados)
            codigos = list(dict.fromkeys(a.codigo for a in self.ultima.achados))
            achados.append((
                "portal_borda_defeito",
                f"O vigia da borda (volta das {quando}) achou a página mexida pelo caminho: {detalhes}.",
                " ".join(borda.GESTOS[c] for c in codigos if c in borda.GESTOS)))
        if self.seguidas_sem_conferir >= self._n():
            motivo = self.ultima.motivo if self.ultima is not None else "?"
            achados.append((
                "portal_borda_sem_conferir",
                f"Há {self.seguidas_sem_conferir} volta(s) seguida(s) o vigia da borda não consegue conferir o site pelo "
                f"nome público ({motivo}; última às {quando}).",
                "Confira o túnel do central (cloudflared) e o DNS do nome público; a página pode estar fora para "
                "visitantes."))
        return achados
