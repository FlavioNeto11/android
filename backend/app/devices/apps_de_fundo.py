"""Apps de fundo que o preparo DESATIVA nos aparelhos da automação (`pm disable-user --user 0`).

Por quê, medido em 29/09/2026 no central: no android-06, o app Google (`com.google.android.googlequicksearchbox`)
ocupava 101 MB, o GMS persistente 74 MB, o Android System Intelligence (`com.google.android.as`) 37 MB e o Mensagens
(`com.google.android.apps.messaging`) 22 MB. No android-04, 2,7 h depois de reiniciar: load 14,7, 82 MB livres,
500 MB de zram em uso, `kcompactd0` com 43% de CPU — e YouTube, YouTube Music, Gmail e Bem-estar digital subindo
sozinhos. O convidado tem 2 GB. Um app desativado não sobe mais em segundo plano, nem depois do boot.

O que NUNCA se desativa (`PROTEGIDOS`): a Play Store (instala o APK com a conta do dono), o GMS e o GSF (a conta
Google e tudo que depende dela), o WebView (e a biblioteca de que ele depende), o teclado, o launcher, o SystemUI, o
Chrome (sites, ADR-025), o `io.appium.*` (o servidor de automação) e o app alvo. O alvo não tem nome escrito aqui
(ADR-052: conhecimento de app é dado): é todo app DECLARADO em `app/conhecimento/apps/<pacote>/` (o Instagram
inclusive), mais, no central, o catálogo de apps do banco e o que a configuração declara (`apps`, `contas.sessao`).
Pacote protegido na lista é RECUSADO na carga da configuração (`validar_lista`) e, se chegar por código, pulado pelo
`Adb` antes de qualquer `pm` (`planejar`).

Reversível: o preparo grava no aparelho (`MARCADOR`) os pacotes da lista que ficaram desativados. Tirado da lista, o
pacote volta com `pm enable` no preparo seguinte — e só o que está no marcador: o que a pessoa desativou à mão FORA da
lista fica como está. Um pacote da lista que já estava desativado (à mão, ou a imagem o trazia assim) entra no
marcador também: estar na lista é a pessoa dizendo "desativado", e tirá-lo é dizendo "habilitado" — é também o que
recupera um marcador que não chegou a ser gravado. O marcador vive com o estado dos pacotes: vai junto no snapshot e
some junto no reset.

Idempotente: uma leitura primeiro (habilitados, desativados, marcador), e `pm` só para o que precisa mudar. Na segunda
vez não há escrita nenhuma.

Só stdlib e sem import de `app`: está no fecho do agente do worker (`adb.py` e `config.py` o importam).
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

#: O mesmo formato de `adb.PACKAGE_RE` (daqui não se importa `adb`: `config.py` também usa este módulo, e `adb.py`
#: chega a `config.py` por `sdk.py`). É o que torna seguro montar a linha do shell com o nome do pacote.
PACOTE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")

#: O padrão CONSERVADOR (modelo e `config.example.yaml`): apps do Google que a automação não usa e que sobem sozinhos.
#: Na ordem do que mais pesa, medido — um preparo que estoure o prazo no meio já terá tirado os maiores. Pacote que a
#: imagem não tem é ignorado (`ausentes`): a imagem `google_apis` não traz todos, a `google_apis_playstore` traz mais.
PADRAO: tuple[str, ...] = (
    "com.google.android.googlequicksearchbox",   # app Google: 101 MB no android-06
    "com.google.android.as",                     # Android System Intelligence: 37 MB
    "com.google.android.apps.messaging",         # Mensagens: 22 MB
    "com.google.android.youtube",                # subia sozinho no android-04
    "com.google.android.apps.youtube.music",     # idem
    "com.google.android.gm",                     # Gmail: idem
    "com.google.android.apps.wellbeing",         # Bem-estar digital: idem
    "com.google.android.apps.photos",
    "com.google.android.apps.maps",
    "com.google.android.calendar",
    "com.google.android.apps.docs",              # Drive
    "com.google.android.videos",                 # Google TV
    "com.google.android.apps.tachyon",           # Meet (antigo Duo)
)

#: Famílias protegidas → por quê. Vale o pacote exato e os filhos (`com.google.android.gms` protege também
#: `com.google.android.gms.*`; `io.appium` protege `io.appium.uiautomator2.server` e o `.test`).
PROTEGIDOS: dict[str, str] = {
    "com.android.vending": "Play Store: é por ela que o APK é instalado com a conta do dono",
    "com.google.android.gms": "Google Play Services: conta Google, loja e tudo que depende deles",
    "com.google.android.gsf": "Google Services Framework: conta Google",
    "com.google.android.webview": "WebView: telas web dos apps (login inclusive)",
    "com.android.webview": "WebView: telas web dos apps (login inclusive)",
    "com.google.android.trichromelibrary": "biblioteca de que o WebView e o Chrome dependem",
    "com.google.android.inputmethod.latin": "teclado: sem ele não se digita",
    "com.android.inputmethod.latin": "teclado: sem ele não se digita",
    "com.google.android.apps.nexuslauncher": "launcher: a tela inicial",
    "com.android.launcher3": "launcher: a tela inicial",
    "com.android.launcher": "launcher: a tela inicial",
    "com.android.systemui": "SystemUI: barra, notificações, diálogos do sistema",
    "com.android.chrome": "Chrome: é por ele que a automação usa os sites (ADR-025)",
    "io.appium": "servidor de automação (Appium/UiAutomator2)",
    "com.android.settings": "Configurações do sistema",
    "com.android.shell": "shell do adb",
    "com.android.phone": "telefonia do sistema",
    "com.android.providers": "provedores de dados do sistema",
    "com.android.packageinstaller": "instalador de pacotes",
    "com.google.android.packageinstaller": "instalador de pacotes",
    "com.android.permissioncontroller": "diálogos de permissão",
    "com.google.android.permissioncontroller": "diálogos de permissão",
    "com.android.networkstack": "pilha de rede (a sonda de internet do convidado depende dela)",
    "com.google.android.networkstack": "pilha de rede (a sonda de internet do convidado depende dela)",
    "com.android.ext.services": "serviços do sistema (autofill, classificação de notificações)",
    "com.google.android.ext.services": "serviços do sistema (autofill, classificação de notificações)",
}

#: Teto da lista: cada pacote é um `pm` na linha do shell, e a linha inteira cabe no prazo do preparo.
MAXIMO = 40

#: Onde o preparo registra, NO APARELHO, o que ele próprio desativou. `/data/local/tmp` é gravável pelo usuário do
#: `adb shell` e sobrevive ao reboot; um reset apaga o marcador junto com o estado dos pacotes.
MARCADOR = "/data/local/tmp/central-apps-desativados.txt"

#: Orçamento do passo dos apps dentro do preparo, somando leitura, escrita e marcador. O `manager` dá 60 s ao preparo
#: inteiro (`PRAZO_DO_PREPARO_S`) e a chamada dos ajustes de sempre desiste em 40 s: 40 + 12 fica abaixo de 60, e o
#: executor nunca estoura por causa dos apps. O que não couber fica para o preparo seguinte (é idempotente).
PRAZO_S = 12.0

#: Separadores da leitura: a saída de três comandos numa chamada só.
_DESATIVADOS = "__apps_desativados__"
_DO_MARCADOR = "__apps_marcador__"
_FIM = "__apps_fim__"

#: Uma chamada só lê tudo o que o plano precisa. `-e`/`-d`: habilitados e desativados do usuário 0.
COMANDO_DE_LEITURA = (f"pm list packages -e --user 0; echo {_DESATIVADOS}; pm list packages -d --user 0; "
                      f"echo {_DO_MARCADOR}; cat {MARCADOR} 2>/dev/null; echo {_FIM}")


#: Onde moram os apps DECLARADOS (ADR-052: um app é uma pasta de dado com `app.yaml`, com o nome do pacote). É daqui
#: que sai o app alvo, e não de um nome escrito em Python. No agente do worker a pasta não existe (lá o `Adb` nunca
#: recebe lista: quem prepara com lista é o central).
PASTA_DOS_APPS_DECLARADOS = Path(__file__).resolve().parents[1] / "conhecimento" / "apps"


def apps_declarados(raiz: Path | None = None) -> frozenset[str]:
    """Os pacotes com pasta de conhecimento (`<raiz>/<pacote>/app.yaml`). Lido a cada chamada: é uma pasta pequena,
    e um app declarado depois da partida já sai protegido no preparo seguinte."""
    base = raiz or PASTA_DOS_APPS_DECLARADOS
    if not base.is_dir():
        return frozenset()
    return frozenset(p.name for p in base.iterdir()
                     if p.is_dir() and PACOTE_RE.match(p.name) and (p / "app.yaml").is_file())


def protecao(pacote: str) -> str | None:
    """Por que `pacote` nunca é desativado, ou None quando ele pode ser."""
    for familia, motivo in PROTEGIDOS.items():
        if pacote == familia or pacote.startswith(familia + "."):
            return motivo
    if pacote in apps_declarados():
        return "app declarado em app/conhecimento/apps: é alvo da automação"
    return None


def validar_lista(valores: Iterable[object]) -> list[str]:
    """A lista da configuração, limpa (sem espaço, sem repetição, na ordem) — ou `ValueError` com o pacote e o motivo.
    É o que recusa na CARGA: pacote protegido na lista impede o backend de subir, e a mensagem diz qual e por quê."""
    limpos: list[str] = []
    for bruto in valores:
        pacote = str(bruto).strip()
        if not PACOTE_RE.match(pacote):
            raise ValueError(f"desativar_apps: {pacote!r} não é nome de pacote Android (ex.: com.google.android.gm)")
        motivo = protecao(pacote)
        if motivo:
            raise ValueError(f"desativar_apps: {pacote} é protegido e nunca é desativado ({motivo})")
        if pacote not in limpos:
            limpos.append(pacote)
    if len(limpos) > MAXIMO:
        raise ValueError(f"desativar_apps: {len(limpos)} pacotes; o teto é {MAXIMO}")
    return limpos


@dataclass(frozen=True, slots=True)
class EstadoDosApps:
    """O que a leitura viu no aparelho."""

    habilitados: frozenset[str]
    desativados: frozenset[str]
    marcados: frozenset[str]          # o que o preparo desativou antes (o marcador)


def ler_estado(saida: str) -> EstadoDosApps:
    """A saída de `COMANDO_DE_LEITURA`. `ValueError` quando não dá para saber: leitura cortada, ou o `pm` fora do ar
    (um Android vivo tem centenas de pacotes habilitados — lista vazia ali é o `pm` que não respondeu, nunca "nada
    instalado", o mesmo engano que `Adb.list_packages` já evita)."""
    secoes: dict[str, list[str]] = {"e": [], "d": [], "m": []}
    atual: str | None = "e"
    fim = False
    for linha in (ln.strip() for ln in saida.splitlines()):
        if linha == _DESATIVADOS:
            atual = "d"
        elif linha == _DO_MARCADOR:
            atual = "m"
        elif linha == _FIM:
            atual, fim = None, True
        elif atual is not None and linha:
            secoes[atual].append(linha)
    if not fim:
        raise ValueError("a leitura dos pacotes veio cortada")

    def pacotes(linhas: list[str]) -> frozenset[str]:
        return frozenset(ln.split(":", 1)[1].strip() for ln in linhas if ln.startswith("package:"))

    habilitados = pacotes(secoes["e"])
    if not habilitados:
        detalhe = next(iter(secoes["e"]), "")
        raise ValueError("o `pm` não listou os pacotes" + (f": {detalhe[:120]}" if detalhe else ""))
    # Linha do marcador fora do formato é ignorada: nada que não seja nome de pacote vai para uma linha de shell.
    marcados = frozenset(ln for ln in secoes["m"] if PACOTE_RE.match(ln))
    return EstadoDosApps(habilitados, pacotes(secoes["d"]), marcados)


@dataclass(frozen=True, slots=True)
class PlanoDosApps:
    desativar: tuple[str, ...]        # na lista e habilitados
    reativar: tuple[str, ...]         # no marcador, fora da lista, e desativados
    ja_desativados: tuple[str, ...]   # na lista e já desativados
    ausentes: tuple[str, ...]         # na lista e a imagem não tem
    recusados: tuple[str, ...]        # protegidos ou nome inválido que chegaram à lista por código
    lista: tuple[str, ...]            # a lista que vale (sem os recusados)


def planejar(lista: Iterable[str], estado: EstadoDosApps) -> PlanoDosApps:
    """O que muda, e nada além disso. Pacote protegido nunca entra em `desativar`, qualquer que seja a origem da lista
    (a recusa da carga é a primeira barreira; esta é a segunda)."""
    valida: list[str] = []
    recusados: list[str] = []
    for pacote in lista:
        if not PACOTE_RE.match(pacote) or protecao(pacote):
            recusados.append(pacote)
        elif pacote not in valida:
            valida.append(pacote)
    return PlanoDosApps(
        desativar=tuple(p for p in valida if p in estado.habilitados),
        reativar=tuple(sorted(p for p in estado.marcados if p not in valida and p in estado.desativados)),
        ja_desativados=tuple(p for p in valida if p in estado.desativados),
        ausentes=tuple(p for p in valida if p not in estado.habilitados and p not in estado.desativados),
        recusados=tuple(recusados), lista=tuple(valida))


def comando_de_escrita(plano: PlanoDosApps) -> str:
    """Uma linha de shell com cada `pm`, precedido de `echo @@ <pacote>`: a saída de cada um fica atribuível ao
    pacote (a mensagem de erro do `pm` nem sempre diz qual). `2>&1` traz o erro para a mesma saída."""
    partes = [f"echo '@@ {p}'; pm disable-user --user 0 {p} 2>&1" for p in plano.desativar]
    partes += [f"echo '@@ {p}'; pm enable --user 0 {p} 2>&1" for p in plano.reativar]
    return "; ".join(partes)


def ler_escrita(saida: str, plano: PlanoDosApps) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """(desativados confirmados, reativados confirmados, falhas `pacote: motivo`). Confirmado é só o que o `pm`
    disse ter mudado: "Package X new state: disabled-user" / "enabled"."""
    por_pacote: dict[str, list[str]] = {}
    atual: str | None = None
    for linha in (ln.strip() for ln in saida.splitlines()):
        if linha.startswith("@@ "):
            atual = linha[3:].strip()
            por_pacote.setdefault(atual, [])
        elif atual is not None and linha:
            por_pacote[atual].append(linha)
    falhas: list[str] = []

    def confirmados(pacotes: tuple[str, ...], estado: str) -> tuple[str, ...]:
        ok: list[str] = []
        for p in pacotes:
            linhas = por_pacote.get(p)
            if linhas is not None and f"Package {p} new state: {estado}" in linhas:
                ok.append(p)
            else:
                motivo = next((ln for ln in reversed(linhas or []) if ln), "") or "sem resposta do `pm`"
                falhas.append(f"{p}: {motivo[:160]}")
        return tuple(ok)

    desativados = confirmados(plano.desativar, "disabled-user")
    reativados = confirmados(plano.reativar, "enabled")
    return desativados, reativados, tuple(falhas)


def marcador_depois(estado: EstadoDosApps, plano: PlanoDosApps, desativados: Iterable[str],
                    reativados: Iterable[str]) -> tuple[str, ...]:
    """O marcador depois desta passagem: o que está na lista e desativado (inclusive o que já estava, à mão ou de
    fábrica — está na lista, é o dono quem o quer desativado; tirado dela, volta HABILITADO, mesmo que a imagem o
    trouxesse desativado), mais o que era nosso, saiu da lista e ainda não voltou (tenta de novo depois). Some o que
    voltou, o que a imagem não tem mais e o que já está habilitado (alguém reativou)."""
    na_lista = set(plano.ja_desativados) | set(desativados)
    voltou = set(reativados)
    pendentes = {p for p in estado.marcados
                 if p not in plano.lista and p in estado.desativados and p not in voltou}
    return tuple(sorted(na_lista | pendentes))


def comando_do_marcador(pacotes: tuple[str, ...]) -> str:
    """Grava o marcador inteiro (idempotente: o mesmo conteúdo, o mesmo arquivo), ou o apaga quando vazio. Só nomes
    que passaram por `PACOTE_RE` chegam aqui: nada de aspas, espaço ou metacaractere na linha."""
    if not pacotes:
        return f"rm -f {MARCADOR}"
    return f"printf '%s\\n' {' '.join(pacotes)} > {MARCADOR}"


@dataclass(frozen=True, slots=True)
class AjusteDosApps:
    """O registro do que o preparo fez com os apps de fundo NESTE aparelho, nesta passagem."""

    desativados: tuple[str, ...] = ()     # desativados agora
    reativados: tuple[str, ...] = ()      # reativados agora (saíram da lista)
    ja_desativados: tuple[str, ...] = ()
    ausentes: tuple[str, ...] = ()        # a imagem não tem: nada a fazer, e não é falha
    recusados: tuple[str, ...] = ()       # protegidos que chegaram à lista por código: nunca vão ao `pm`
    falhas: tuple[str, ...] = ()          # `pacote: motivo`, ou o motivo de não ter dado para ler
    incerto: str = ""                     # o prazo estourou: o efeito no aparelho não se sabe

    @property
    def mudou(self) -> bool:
        return bool(self.desativados or self.reativados)

    def resumo(self) -> str:
        partes: list[str] = []
        if self.desativados:
            partes.append(f"{len(self.desativados)} desativado(s): {', '.join(self.desativados)}")
        if self.reativados:
            partes.append(f"{len(self.reativados)} reativado(s): {', '.join(self.reativados)}")
        if self.ja_desativados:
            partes.append(f"{len(self.ja_desativados)} já desativado(s)")
        if self.ausentes:
            partes.append(f"{len(self.ausentes)} ausente(s) na imagem")
        if self.recusados:
            partes.append(f"protegido(s) recusado(s): {', '.join(self.recusados)}")
        if self.falhas:
            partes.append(f"falha(s): {'; '.join(self.falhas)}")
        if self.incerto:
            partes.append(f"incerto: {self.incerto}")
        return "apps de fundo — " + ("; ".join(partes) if partes else "nada a fazer")
