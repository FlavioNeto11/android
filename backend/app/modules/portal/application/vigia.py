"""O vigia da borda (29.97): de hora em hora, o central pede o site e o painel pelo nome público, como um visitante, e
confere o que a borda da Cloudflare fez com eles.

Os dois defeitos do portal na semana de 05/10 nasceram de configuração da ZONA, que muda a qualquer hora e não só no
deploy: o beacon do Web Analytics injetado na raiz e no painel (29.85, 29.91) e o CSS e o JS guardados por 4 h (29.95).
A prova de fora só roda quando alguém lembra; o vigia roda sozinho. O que é defeito é decidido pela régua comum
(`domain/borda.py`), a mesma da prova de fora.

Uma volta faz no máximo 5 GET, um por conferência, sem retry e sem credencial: a API (29.101, só o status, o corpo
nunca é lido), a raiz e o painel como navegador, e o CSS e o JS pelo endereço com `?v=` que a raiz aponta. O estado
fica em memória: a saúde só o lê, sem pedir nada à rede.
O aviso ao dono sai pela Canais (`avisar_borda_do_portal`, contrato dela) uma vez por código e por dia, e o "não
consegui conferir" só depois de N voltas seguidas, contado à parte para o site e para a API.
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
                                    "/assets/site.js": "js", borda.CAMINHO_DA_API: "api"}


@dataclass(frozen=True, slots=True)
class Resposta:
    status: int
    cabecalhos: Mapping[str, str]
    corpo: bytes


class SemResposta(Exception):
    """A rede, o tempo esgotado ou o nome que não resolve: a volta não diz nada sobre a página."""


class Buscar(Protocol):
    def __call__(self, url: str, *, aceita: str | None, ler_corpo: bool = True) -> Resposta: ...


class AvisarBorda(Protocol):
    """`avisos.avisar_borda_do_portal` (contrato da Canais, 29.97). Devolve um `ContatoAvisado` (`enfileirado`)."""

    def __call__(self, codigo: str, onde: str, agora: datetime, *, achado: str | None = None,
                 horas_sem_conferir: int | None = None) -> object: ...


def _pedir(buscar: Buscar, url: str, aceita: str | None, *, ler_corpo: bool = True) -> Resposta | Desfecho:
    try:
        return buscar(url, aceita=aceita, ler_corpo=ler_corpo)
    except SemResposta as exc:
        return borda.sem_conferir(str(exc) or "sem resposta")


def uma_volta(buscar: Buscar, *, host: str, site_ligado: bool, csp_do_painel: str) -> Desfecho:
    """As conferências de uma volta, juntas. A API e o painel sempre; a raiz e a versão do CSS e do JS só com o site
    ligado."""
    return borda.juntar(uma_volta_em_partes(buscar, host=host, site_ligado=site_ligado, csp_do_painel=csp_do_painel))


def uma_volta_em_partes(buscar: Buscar, *, host: str, site_ligado: bool,
                        csp_do_painel: str) -> tuple[Desfecho, Desfecho]:
    """`(api, site)`. Separados porque a API tem o próprio "não consegui conferir" (C3 da leitura do #383): uma API em
    404 com o site perfeito não pode virar aviso de que a página está fora de alcance."""
    base = f"https://{host}"
    # A API primeiro: é o defeito mais grave. Só o status importa; aberta, o corpo seria dado do central (29.101). Dos
    # cabeçalhos, só o `cf-mitigated` é lido (C1), e nada deles vai ao log nem à saúde.
    resposta = _pedir(buscar, base + borda.CAMINHO_DA_API, None, ler_corpo=False)
    api = resposta if isinstance(resposta, Desfecho) else borda.conferir_api(resposta.status, resposta.cabecalhos)
    return api, borda.juntar(_o_site(buscar, base, host, site_ligado=site_ligado, csp_do_painel=csp_do_painel))


def _o_site(buscar: Buscar, base: str, host: str, *, site_ligado: bool, csp_do_painel: str) -> list[Desfecho]:
    desfechos: list[Desfecho] = []
    painel = _pedir(buscar, base + "/central/", borda.ACEITA)
    if isinstance(painel, Desfecho):
        desfechos.append(painel)
    else:
        desfechos += _pagina("/central/", painel, host, sem_transformar=True,
                             csp=None if csp_do_painel == "desligada" else csp_do_painel)
    if not site_ligado:
        return desfechos

    raiz = _pedir(buscar, base + "/", borda.ACEITA)
    if isinstance(raiz, Desfecho):
        desfechos.append(raiz)
        return desfechos
    desfechos += _pagina("/", raiz, host, sem_transformar=True, gzip_da_origem=True, csp="site", sem_cookie=True)
    if raiz.status != 200 or not _corpo_legivel(raiz):
        return desfechos
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
    return desfechos


def _pagina(onde: str, resposta: Resposta, host: str, *, sem_transformar: bool = False, gzip_da_origem: bool = False,
            csp: str | None = None, sem_cookie: bool = False) -> list[Desfecho]:
    """A página e os cabeçalhos dela. Fora do 200 só vale o `pagina_fora` do HTML: um desafio da borda (403) não tem
    `no-transform`, CSP nem gzip, e conferir cabeçalho nele viraria 3 ou 4 avisos com gesto errado (V1). Corpo em br
    ou zstd não se lê aqui: a borda abriu e recomprimiu. Na raiz o `gzip_da_origem` já acusa; no painel, que a origem
    não comprime, o próprio corpo ilegível vira o `html_transformado` (V4)."""
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


#: As chaves do "não consegui conferir" no dia do aviso: uma do site e outra da API (a Canais também separa).
_CHAVE_DO_SITE, _CHAVE_DA_API = borda.SEM_CONFERIR, "sem_conferir_api"
_CHAVES_SEM_CONFERIR = frozenset({_CHAVE_DO_SITE, _CHAVE_DA_API})


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
        self.ultima_do_site: Desfecho | None = None
        self.ultima_da_api: Desfecho | None = None
        self.quando: datetime | None = None
        self.seguidas_sem_conferir = 0              # o site (painel, raiz, arquivos), ou o túnel inteiro
        self.seguidas_sem_conferir_api = 0          # a API sem recusar nem abrir, com o site conferido: C3 e E1
        self._avisado_em: dict[str, date] = {}

    def volta(self, agora: datetime) -> Desfecho | None:
        """Uma volta inteira. `None` quando não há nome público (nada roda)."""
        host = self._host()
        if not host:
            return None
        api, site = uma_volta_em_partes(self._buscar, host=host, site_ligado=self._site_ligado(),
                                        csp_do_painel=self._csp_do_painel())
        desfecho = borda.juntar((api, site))
        self.ultima, self.ultima_do_site, self.quando = desfecho, site, agora
        self.seguidas_sem_conferir = self._sem_conferir(site, self.seguidas_sem_conferir, "raiz", _CHAVE_DO_SITE, agora)
        # A API sem resposta (rede, túnel, tempo esgotado) com o site TAMBÉM sem conferir é a mesma queda: quem conta é
        # o site, sem aviso dobrado. Com o site conferido na mesma volta, a falha é só da API (rota lenta, regra da zona
        # só em /api/*, origem que derruba só ali) e conta para ela (E1 da leitura do #383). A volta pulada não mexe no
        # contador da API nem no motivo que a saúde mostra.
        mesma_queda = (api.estado == borda.SEM_CONFERIR and not api.motivo.startswith("api ")
                       and site.estado == borda.SEM_CONFERIR)
        if not mesma_queda:
            self.ultima_da_api = api
            self.seguidas_sem_conferir_api = self._sem_conferir(api, self.seguidas_sem_conferir_api, "api",
                                                                _CHAVE_DA_API, agora)
        if desfecho.estado == borda.OK:
            log.info("portal: vigia da borda ok")
            self._avisado_em.clear()
            return desfecho
        if desfecho.estado == borda.SEM_CONFERIR:
            return desfecho
        log.warning("portal: vigia da borda achou defeito: %s",
                    "; ".join(f"{a.codigo} em {a.onde}" for a in desfecho.achados))
        presentes: dict[str, Achado] = {}
        for achado in desfecho.achados:
            presentes.setdefault(achado.codigo, achado)
        for codigo in [c for c in self._avisado_em if c not in presentes and c not in _CHAVES_SEM_CONFERIR]:
            del self._avisado_em[codigo]                      # resolvido: se voltar, avisa de novo
        for codigo, achado in presentes.items():
            self._avisar_uma_vez(codigo, ONDE_DO_AVISO.get(achado.onde, "raiz"), agora, achado=achado.item or None)
        return desfecho

    def _sem_conferir(self, parte: Desfecho, seguidas: int, onde: str, chave: str, agora: datetime) -> int:
        """As voltas seguidas sem conferir de uma parte; na N-ésima, o aviso. Conferiu: zera."""
        if parte.estado != borda.SEM_CONFERIR:
            self._avisado_em.pop(chave, None)
            return 0
        seguidas += 1
        log.warning("portal: vigia da borda sem conferir %s (%s), %s volta(s) seguida(s)", onde, parte.motivo, seguidas)
        if seguidas >= self._n():
            # O aviso diz o código (`borda-502`, `tempo-esgotado`, `api-404`): o filtro da Canais só deixa
            # [A-Za-z0-9._/-].
            codigo = parte.motivo.split(",", 1)[0].strip().replace(" ", "-")
            self._avisar_uma_vez(borda.SEM_CONFERIR, onde, agora, achado=codigo or None, horas=self._horas(seguidas),
                                 chave=chave)
        return seguidas

    def _horas(self, seguidas: int) -> int:
        return max(1, math.ceil(seguidas * self._intervalo_s() / 3600))

    def _avisar_uma_vez(self, codigo: str, onde: str, agora: datetime, *, achado: str | None = None,
                        horas: int | None = None, chave: str | None = None) -> None:
        """Uma vez por código e por dia UTC (a chave da Canais também é por dia); o "não consegui conferir" tem uma
        chave para o site e outra para a API. Se a Canais não enfileirou (canal desligado, falha), tenta de novo na
        volta seguinte."""
        chave = chave or codigo
        dia = agora.date()
        if self._avisado_em.get(chave) == dia:
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
            self._avisado_em[chave] = dia
        else:
            log.warning("portal: aviso da borda (%s) não entrou na fila: %s", codigo, getattr(resposta, "motivo", "?"))

    def problemas(self) -> list[tuple[str, str, str]]:
        """Para o `/api/health`: só o estado da última volta, nenhum pedido de rede aqui."""
        achados: list[tuple[str, str, str]] = []
        quando = f"{self.quando:%H:%M}Z" if self.quando else "?"
        defeitos = self.ultima.achados if self.ultima is not None and self.ultima.estado == borda.DEFEITO else ()
        if any(a.codigo == borda.API_ABERTA for a in defeitos):
            # Separado e primeiro: não é a página mexida pelo caminho, é o central aberto para a internet (29.101).
            achados.append((
                "portal_api_aberta",
                f"O vigia da borda (volta das {quando}) pediu {borda.CAMINHO_DA_API} pelo nome público SEM credencial "
                "e a API respondeu.",
                borda.GESTOS[borda.API_ABERTA]))
        defeitos = tuple(a for a in defeitos if a.codigo != borda.API_ABERTA)
        if defeitos:
            # O detalhe pode trazer valor de terceiro (cabeçalho, nome de cookie): nenhum controle entra na saúde (U1).
            detalhes = borda.linha_sem_controle("; ".join(f"{a.onde}: {a.detalhe}" for a in defeitos))
            codigos = list(dict.fromkeys(a.codigo for a in defeitos))
            achados.append((
                "portal_borda_defeito",
                f"O vigia da borda (volta das {quando}) achou a página mexida pelo caminho: {detalhes}.",
                " ".join(borda.GESTOS[c] for c in codigos if c in borda.GESTOS)))
        if self.seguidas_sem_conferir_api >= self._n():
            motivo = self.ultima_da_api.motivo if self.ultima_da_api is not None else "?"
            # O portão do central recusa antes da rota: um 500 sem credencial sugere que o pedido passou dele ou que
            # ele quebrou (nota da leitura do #383).
            extra = (" Um 500 sem credencial sugere que o pedido passou do portão do central ou que o portão quebrou."
                     if motivo == "api 500" else "")
            achados.append((
                "portal_api_sem_conferir",
                f"Há {self.seguidas_sem_conferir_api} volta(s) seguida(s) a API pedida sem credencial pelo nome "
                f"público não recusa nem abre ({motivo}; última às {quando}).{extra}",
                "Confira se uma regra da zona (redirecionamento, desafio, página de erro) responde por /api/* no lugar "
                "do central; o vigia não consegue dizer se a API está fechada."))
        if self.seguidas_sem_conferir >= self._n():
            parte = self.ultima_do_site or self.ultima
            motivo = parte.motivo if parte is not None else "?"
            achados.append((
                "portal_borda_sem_conferir",
                f"Há {self.seguidas_sem_conferir} volta(s) seguida(s) o vigia da borda não consegue conferir o site pelo "
                f"nome público ({motivo}; última às {quando}).",
                "Confira o túnel do central (cloudflared) e o DNS do nome público; a página pode estar fora para "
                "visitantes."))
        return achados
