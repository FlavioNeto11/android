"""O Android está PRONTO? — uma definição só, usada pelo worker (`start`/`wake`) e pelo central (entrada no ar).

Wake do android-09 em 25/09/2026 (análise forense em `docs/conhecimento/aprendizados.md`, K-028): adb `device`,
`boot_completed=1`, e o `prepare_for_automation` do worker ficou 40 s sem resposta — `settings put`/`wm`/
`locksettings` passam pelo `system_server`. O worker tratou isso como aviso e fechou `succeeded` 0,3 s depois. O
`service check` que o PR #5 exige teria passado também: ele pergunta ao `servicemanager`, que só travou minutos
depois. A captura de tela (SurfaceFlinger) não respondeu em nenhum momento depois do restore.

Por isso a prontidão é uma escada de três subsistemas, em ordem, cada um com uma leitura SÓ LEITURA e barata:

1. `service_manager` — `service check activity/package/settings`: os serviços essenciais estão registrados.
   Sozinho NÃO é "framework pronto".
2. `system_server` — `settings get global window_animation_scale`: o SettingsProvider mora no `system_server`,
   então a resposta atravessa o processo que atende apps, instalação e automação.
3. `display` — `screencap > /dev/null`: o SurfaceFlinger produziu um quadro. Nada é gravado; o conteúdo não importa.

Pronto = os três responderam. Qualquer tempo esgotado é "não pronto / não se sabe", nunca pronto.

CONTRATO TEMPORAL: pronto não é "os três responderam em algum momento"; é "NESTA tentativa, os três responderam
DEPOIS do último sinal de não-resposta relevante ao boot/wake/readoção": nenhuma prontidão positiva sobrevive a um
timeout nem a uma operação local ainda em execução. Vale para o preparo (`prepare_for_automation`) e para o acerto do
relógio pós-boot/wake (`sync_clock`), no worker e no central:

- ESTOURO DE PRAZO numa dessas operações deixa a TENTATIVA não pronta, mesmo que as sondas respondam logo depois.
  `AdbTimeout` encerra só o cliente adb local: o efeito no aparelho segue incerto (o mesmo motivo de o desfecho de
  comando virar `uncertain`, `devices/adb.py`). `DriverTimeout` (o executor desistiu; a chamada segue "zumbi" na
  thread do aparelho) é drenado com teto — para a próxima tentativa não concorrer com ele —, mas o `drain` prova só o
  fim da thread local. A forense de 25/09 viu 3 s de recuperação parcial logo depois do timeout do preparo, antes do
  travamento: uma rodada nessa janela enganaria. Worker: `uncertain`. Central: a próxima passagem (readoção
  periódica), o boot a frio (depois do wake) ou a escada de reparo decidem.
- ERRO RÁPIDO (`AdbError`) é retorno conhecido, mas não distingue "o comando recusou" de `device offline` (`Adb.shell`
  levanta para qualquer saída não-zero): antes da escada, a escada decide; DEPOIS de uma prontidão positiva, ela
  deixa de valer e decide uma rodada nova e completa. Erro benigno não bloqueia (a rodada nova passa em < 2 s).

LIMITAÇÃO CONHECIDA (não resolvida aqui): um `AdbTimeout` pode deixar efeito remoto TARDIO no mesmo guest — uma
transação binder já entregue a um `system_server` congelado executa quando ele destrava, mesmo com o cliente morto.
Não há isolamento entre tentativas nem quarentena por geração de processo: a próxima tentativa no mesmo guest é
recuperação funcional, não prova de que o efeito anterior acabou. Quase todo o preparo é idempotente (`settings put`
com constantes, `svc power stayon`, `wm dismiss-keyguard`); os efeitos tardios NÃO idempotentes identificados são o
`input tap` de `dismiss_system_dialog` e o `cmd alarm set-time` de timestamp absoluto do `sync_clock`.

O orçamento é por rodada (a soma dos prazos, cortada pelo que resta do prazo de boot/wake de quem chama): três
timeouts em série não esticam um wake de 90 s para minutos.

Sem importar nada além de `devices`: o agente do worker leva este pacote.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

#: A ordem é a escada: cada degrau pressupõe o anterior (sem `servicemanager` não há `system_server` a consultar).
SUBSISTEMAS = ("service_manager", "system_server", "display")

#: Método do alvo (Adb ou DeviceIO) que sonda cada subsistema — os mesmos nomes nos dois, de propósito.
METODO = {"service_manager": "framework_alive", "system_server": "system_server_alive", "display": "display_alive"}

#: Prazo de cada sonda numa rodada. Medido num Android saudável (android-06, 26/09/2026): 0,1-0,4 s cada; no wake
#: do android-09, `service check` respondeu 9 s depois do `adb device` e o primeiro `screencap` levou 4,5-5,5 s.
PRAZO_S = {"service_manager": 8.0, "system_server": 8.0, "display": 12.0}

ROTULO = {"service_manager": "servicemanager", "system_server": "system_server", "display": "display (SurfaceFlinger)"}


@dataclass(frozen=True, slots=True)
class Prontidao:
    """`estado`: `ok` (os três responderam), `mudo` (algum não respondeu a tempo ou respondeu errado: não se sabe)
    ou `morto` (o `servicemanager` respondeu que os serviços essenciais NÃO existem). `falta` é o primeiro
    subsistema que não passou; `respondeu`, os que passaram antes dele."""

    estado: str
    falta: str | None = None
    erro: str = ""
    respondeu: tuple[str, ...] = ()

    @property
    def pronto(self) -> bool:
        return self.estado == "ok"

    def detalhe(self) -> str:
        if self.pronto:
            return "servicemanager, system_server e display responderam"
        if self.falta is None:
            return f"prontidão desconhecida ({self.erro})"
        antes = f"{ROTULO[self.respondeu[-1]]} respondeu; " if self.respondeu else "boot concluído; "
        motivo = f" ({self.erro})" if self.erro else ""
        return f"{antes}aguardando {ROTULO[self.falta]}{motivo}"


def prazo_da_rodada() -> float:
    """O pior caso de UMA rodada — quem roda `avaliar` numa thread usa isto (mais folga) como teto externo."""
    return sum(PRAZO_S.values())


def avaliar(alvo: Any, *, restante_s: float | None = None) -> Prontidao:
    """Uma rodada da escada. `restante_s` corta cada prazo pelo que ainda resta de quem chama (boot/wake), com piso
    de 1 s para a sonda nunca virar "tempo zero". Para no primeiro degrau que não passa: não adianta perguntar ao
    display com o `system_server` mudo, e o tempo economizado fica para a próxima rodada."""
    fim = None if restante_s is None else time.monotonic() + max(0.0, restante_s)
    respondeu: list[str] = []
    for sub in SUBSISTEMAS:
        prazo = PRAZO_S[sub]
        if fim is not None:
            prazo = max(1.0, min(prazo, fim - time.monotonic()))
        try:
            ok = getattr(alvo, METODO[sub])(timeout=prazo)
        except Exception as exc:  # noqa: BLE001 - AdbTimeout/AdbError/DriverError/dublê: não respondeu = não se sabe
            return Prontidao("mudo", sub, str(exc)[:160] or type(exc).__name__, tuple(respondeu))
        if not ok:
            morto = sub == "service_manager"          # só o `servicemanager` diz "não existe" com certeza
            return Prontidao("morto" if morto else "mudo", sub,
                             "serviços do sistema ausentes" if morto else "respondeu sem o resultado esperado",
                             tuple(respondeu))
        respondeu.append(sub)
    return Prontidao("ok", None, "", tuple(respondeu))
