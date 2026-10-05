"""Supervisor do backend do central: sobe `python -m app.main`, vigia `/api/health` e religa quando ele cai.

Por que existe (achados #136 e #38). O boot do central provava que o TÚNEL volta sozinho — a tarefa
`farm-tunel-*` sobe no boot. O backend não: ele existia porque alguém o iniciou à mão, numa sessão interativa do
console. Logoff, `Windows Update` ou um crash derrubavam API, agendador e Appium **até alguém voltar à máquina**,
com o agente do worker reconectando no vazio e a fila parada no banco. A retomada do aceite 7 depende de algo
religar o processo, e esse algo não existia.

Três decisões que este arquivo carrega:

1. **Religar quando o backend PARA de responder, nunca quando ele responde `degraded`.** `degraded` é uma
   resposta: significa que a API está de pé e sabe dizer o que está ruim (Appium fora, IA sem chave, disco no
   limite). Reiniciar por causa disso trocaria um problema visível por um laço de reinício — e como é o backend
   que sobe o Appium local (`scripts/start.ps1` e `appium.autostart`), o reinício também apagaria o Appium que
   acabou de subir. O gatilho é ausência de resposta: conexão recusada, tempo esgotado, processo morto.
2. **Espera crescente.** Backend que morre na subida (migração quebrada, porta ocupada) reiniciado em laço
   apertado enche o disco de log e esconde a causa. A espera dobra até um teto.
3. **Encerramento com prazo antes do tiro, e só do backend.** `terminate` primeiro para o backend fechar o banco;
   só depois `kill`. Nada além do processo do backend morre aqui (29.125): o Appium, o servidor de rede e os
   emuladores ficam vivos de propósito e são do backend seguinte, que readota o emulador pelo PID e decide o Appium
   que achar na porta (`AppiumServer.start`, K-039: readota o que prova o mascaramento, troca o que não prova).

Testável de propósito: quem inicia, quem confere saúde, quem encerra e quem dorme entram pelo construtor. O
teste roda dezenas de ciclos em milissegundos sem subir processo nenhum.
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import marca_de_partida
from .identidade import corpo_e_da_farm
from .vigia_do_laco import ultimo_despejo

log = logging.getLogger("poc.supervisor")

#: Quantas conferências seguidas sem resposta antes de considerar o backend travado. Uma só seria pouco: a
#: primeira conferência depois de um `start` sempre falha, e uma coleta de lixo longa não é um travamento.
FALHAS_ATE_REINICIAR = 3
#: A primeira conferência só acontece depois disto: o backend leva dezenas de segundos para migrar o banco,
#: subir o Appium e responder. `start.ps1` espera até 90 s pelo mesmo motivo.
CARENCIA_S = 90.0
INTERVALO_S = 15.0
ESPERA_MIN_S = 5.0
ESPERA_MAX_S = 60.0
#: Prazo entre o pedido educado de parada e o tiro.
PRAZO_DE_SAIDA_S = 20.0
#: 29.124: enquanto a marca DESTA subida diz que a partida segue (fase antes de `no_ar`), o silêncio de `/api/health`
#: não conta como falha, até este teto desde a subida. Com o disco saturado (05/10), a partida passou dos 90 s da
#: carência e virou laço de kills.
TETO_DA_PARTIDA_S = 600.0
#: 29.124: ... e desde que a última reescrita da marca tenha menos que isto. Uma marca parada numa fase por mais
#: tempo é partida travada, não devagar; conta como falha mesmo abaixo do teto.
PRAZO_DA_FASE_S = 240.0
#: 29.124: reinícios seguidos sem uma conferência boa. Daqui em diante, a espera antes do próximo é `PAUSA_LONGA_S`:
#: reiniciar a cada minuto uma máquina sem fôlego só acrescenta partidas (cinco kills em 18 min, em 05/10).
TETO_DE_REINICIOS = 4
PAUSA_LONGA_S = 300.0


class Processo(Protocol):
    """O mínimo que o supervisor precisa saber sobre o backend — `subprocess.Popen` atende sem adaptador."""

    pid: int

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


@dataclass
class Relatorio:
    """O que aconteceu num ciclo. Existe para o teste afirmar sobre COMPORTAMENTO, não sobre log."""

    iniciou: int = 0
    reiniciou_por_morte: int = 0
    reiniciou_por_silencio: int = 0
    conferencias_ok: int = 0
    conferencias_falhas: int = 0
    recusou_por_ja_haver_backend: int = 0
    esperou_a_partida: int = 0
    pausas_longas: int = 0


#: Teto do corpo lido de `/api/health`: o da Farm tem alguns KB; ler sem teto seria confiar em quem responde.
LIMITE_DO_CORPO = 256 * 1024


def saude_responde(url: str, timeout: float = 5.0) -> bool:
    """`True` quando quem responde em `/api/health` é a FARM — com qualquer status, inclusive `degraded`/503.

    Um 503 da Farm é resposta: a API está viva e sabe dizer o que está ruim. Mas "alguém respondeu" não é "a
    Farm respondeu": em 26/09/2026 o `cartorio-api-1` (outro projeto, `0.0.0.0:8000`) devolveu 404 no lugar da
    Farm parada, e esta função — que então aceitava qualquer HTTPError — fez o supervisor recusar a subida. Agora
    o corpo precisa identificar a Farm (`app/identidade.py`); silêncio (conexão recusada, tempo esgotado) e
    serviço estrangeiro contam igual: a Farm não está no ar.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:          # noqa: S310 - URL de loopback, fixa
            corpo = r.read(LIMITE_DO_CORPO)
    except urllib.error.HTTPError as exc:
        try:
            corpo = exc.read(LIMITE_DO_CORPO)
        except OSError:
            return False
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    return corpo_e_da_farm(corpo)


def encerrar_processo(proc: Processo, prazo_s: float = PRAZO_DE_SAIDA_S) -> None:
    """Pede a saída, espera o prazo e mata. Só o processo do backend (ver o item 3 do docstring do módulo).

    Até o 29.125 havia aqui uma varredura dos filhos (`psutil.Process(pid).children`), para o Appium não segurar a
    4723. No Windows ela nunca rodava: o filho direto do supervisor é o lançador do `python.exe` do venv, e depois do
    kill o `psutil.Process(pid)` dá `NoSuchProcess` (censo de 05/10: o Appium e o sing-box do backend do deploy 37
    seguiram vivos, com o pai morto). Quem resolve o Appium que sobra é o backend seguinte, não o supervisor."""
    try:
        proc.terminate()
        proc.wait(timeout=prazo_s)
    except Exception:  # noqa: BLE001 - já morto, sem permissão, prazo estourado: o `kill` abaixo resolve
        try:
            proc.kill()
        except Exception:  # noqa: BLE001 - morreu entre uma coisa e outra
            pass


#: Processos que limpeza nenhuma encerra: os emuladores. Nome do processo em minúsculas.
_POUPADOS = ("emulator", "qemu")


def e_emulador(nome: str) -> bool:
    """Também é o critério do `AppiumServer` ao trocar o Appium órfão: emulador não se encerra em limpeza nenhuma."""
    nome = nome.lower()
    return any(marca in nome for marca in _POUPADOS)


def iniciar_backend(raiz_backend: Path, log_dir: Path, partida_id: str | None = None) -> Processo:
    """`python -m app.main` com o MESMO interpretador que roda o supervisor (o do venv), com a saída em arquivo.

    O ambiente é o do supervisor (o backend precisa da própria configuração), mais o id desta subida (29.124), que
    o backend grava na marca da partida, e a pasta do supervisor (a mãe de `log_dir`), onde o backend grava a marca e
    o vigia despeja a pilha. Um id herdado de outra subida nunca passa adiante."""
    env = {**os.environ}
    env.pop(marca_de_partida.VARIAVEL, None)
    if partida_id:
        env[marca_de_partida.VARIAVEL] = partida_id
    env[marca_de_partida.PASTA] = str(log_dir.parent)
    log_dir.mkdir(parents=True, exist_ok=True)
    saida = open(log_dir / "backend.out.log", "ab", buffering=0)          # noqa: SIM115 - herdado pelo filho
    erro = open(log_dir / "backend.err.log", "ab", buffering=0)           # noqa: SIM115 - herdado pelo filho
    try:
        return subprocess.Popen([sys.executable, "-m", "app.main"], cwd=str(raiz_backend), env=env,
                                stdout=saida, stderr=erro, stdin=subprocess.DEVNULL, close_fds=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                start_new_session=os.name != "nt")
    finally:
        saida.close()
        erro.close()


class Supervisor:
    def __init__(self, *, iniciar: Callable[[], Processo], saudavel: Callable[[], bool],
                 encerrar: Callable[[Processo], None] = encerrar_processo,
                 dormir: Callable[[float], None] = time.sleep,
                 falhas_ate_reiniciar: int = FALHAS_ATE_REINICIAR, carencia_s: float = CARENCIA_S,
                 intervalo_s: float = INTERVALO_S, espera_min_s: float = ESPERA_MIN_S,
                 espera_max_s: float = ESPERA_MAX_S, despejo: Callable[[], Path | None] = lambda: None,
                 ler_marca: Callable[[], marca_de_partida.Marca | None] = lambda: None,
                 nova_partida: Callable[[], str] = marca_de_partida.nova_partida,
                 teto_da_partida_s: float = TETO_DA_PARTIDA_S, prazo_da_fase_s: float = PRAZO_DA_FASE_S,
                 teto_de_reinicios: int = TETO_DE_REINICIOS, pausa_longa_s: float = PAUSA_LONGA_S,
                 relogio: Callable[[], float] = time.monotonic,
                 relogio_de_parede: Callable[[], float] = time.time) -> None:
        """`iniciar` lê `self.partida_id` (o id desta subida, sorteado antes de chamá-lo) e o passa ao backend."""
        self._iniciar, self._saudavel, self._encerrar, self._dormir = iniciar, saudavel, encerrar, dormir
        self._despejo = despejo
        self._ler_marca, self._nova_partida = ler_marca, nova_partida
        self._relogio, self._relogio_de_parede = relogio, relogio_de_parede
        self.teto_da_partida_s, self.prazo_da_fase_s = teto_da_partida_s, prazo_da_fase_s
        self.teto_de_reinicios, self.pausa_longa_s = teto_de_reinicios, pausa_longa_s
        self.partida_id: str | None = None
        self.subiu_em = 0.0
        self.reinicios_seguidos = 0
        self.falhas_ate_reiniciar = falhas_ate_reiniciar
        self.carencia_s, self.intervalo_s = carencia_s, intervalo_s
        self.espera_min_s, self.espera_max_s = espera_min_s, espera_max_s
        self.proc: Processo | None = None
        self.falhas = 0
        self.espera = espera_min_s
        self.relatorio = Relatorio()

    # ------------------------------------------------------------------ partida
    def _subir(self, motivo: str) -> None:
        """Sobe o backend — a menos que já haja UM respondendo na porta, que não é este supervisor.

        Sem esta guarda, registrar o serviço numa máquina onde alguém já iniciou o backend à mão (é o estado do
        central hoje) vira um laço: o filho sobe, roda `AppState.__init__` (que MIGRA o banco), não consegue
        ligar a porta e morre, e o supervisor tenta de novo em até 60 s. Medido da pior forma: um ensaio mal
        repassado subiu um segundo backend que migrou o banco de produção antes de morrer no `bind`.
        """
        if self._saudavel():
            self.relatorio.recusou_por_ja_haver_backend += 1
            log.warning("já há um backend respondendo nesta porta e ele não é meu; não vou subir outro (%s). "
                        "Pare o backend iniciado à mão (scripts\\stop.ps1) para o serviço assumir.", motivo)
            self._dormir(self.intervalo_s)
            return
        log.info("subindo o backend (%s)", motivo)
        self.partida_id = self._nova_partida()
        self.proc = self._iniciar()
        self.subiu_em = self._relogio()
        self.relatorio.iniciou += 1
        self.falhas = 0
        self._dormir(self.carencia_s)

    def _derrubar_e_resubir(self, motivo: str) -> None:
        if self.proc is not None:
            # 29.121: o vigia do laço do backend grava a pilha antes deste kill; a linha diz onde ela está. O kill não
            # depende da citação: um erro ao procurar o despejo vira "sem despejo".
            try:
                arquivo = self._despejo()
            except Exception:  # noqa: BLE001
                arquivo = None
            log.warning("encerrando o backend (pid %s): %s%s", self.proc.pid, motivo,
                        f"; pilha do laço travado em {arquivo}" if arquivo else "; sem despejo de pilha do vigia")
            self._encerrar(self.proc)
            self.proc = None
        self._esperar_antes_de_subir()
        self._subir(motivo)

    def _esperar_antes_de_subir(self) -> None:
        """A espera cresce entre reinícios SEGUIDOS; uma conferência boa a devolve ao mínimo (ver `ciclo`). Passado o
        teto de reinícios seguidos (29.124), vira a pausa longa, dita no log."""
        self.reinicios_seguidos += 1
        if self.reinicios_seguidos >= self.teto_de_reinicios:
            self.relatorio.pausas_longas += 1
            log.warning("%s reinícios seguidos sem uma conferência boa: pausa de %.0f s antes do próximo (a máquina "
                        "pode estar sem fôlego: disco, RAM)", self.reinicios_seguidos, self.pausa_longa_s)
            self._dormir(self.pausa_longa_s)
            return
        self._dormir(self.espera)
        self.espera = min(self.espera_max_s, self.espera * 2)

    def _partida_em_curso(self) -> str | None:
        """29.124: a descrição da partida que justifica o silêncio, ou `None` (vale a regra de sempre).

        Tolera só se a marca é DESTA subida (o id bate), a fase não é `no_ar`, a partida está abaixo do teto e a
        última reescrita da marca é recente. Backend `no_ar` mudo segue a regra das três falhas."""
        try:
            marca = self._ler_marca()
        except Exception:  # noqa: BLE001 - ler a marca não pode derrubar o supervisor: sem marca
            marca = None
        if marca is None or self.partida_id is None or marca.id != self.partida_id:
            return None
        if marca.fase == marca_de_partida.NO_AR:
            return None
        subindo_ha = self._relogio() - self.subiu_em
        if subindo_ha >= self.teto_da_partida_s:
            log.warning("a partida (fase %s) passou do teto: %.0f s desde a subida, teto de %.0f s; o silêncio conta "
                        "como falha", marca.fase, subindo_ha, self.teto_da_partida_s)
            return None
        parada_ha = self._relogio_de_parede() - marca.ts
        if not 0 <= parada_ha < self.prazo_da_fase_s:
            log.warning("a marca da partida está na fase %s há %.0f s, acima do prazo de %.0f s; o silêncio conta como "
                        "falha", marca.fase, parada_ha, self.prazo_da_fase_s)
            return None
        return f"fase {marca.fase} há {parada_ha:.0f} s, {subindo_ha:.0f} s desde a subida"

    # ------------------------------------------------------------------ o laço
    def ciclo(self) -> None:
        """Um passo: garante que há processo, confere a saúde uma vez e decide."""
        if self.proc is None:
            self._subir("primeira partida")
            return
        if self.proc.poll() is not None:
            # Morreu sozinho (crash, `stop.ps1`, Windows Update). Não há o que encerrar; só subir de novo. O Appium
            # que ele subiu fica na porta: quem o troca, se não o provar mascarado, é o backend seguinte, em
            # `AppiumServer.start` (K-039), que conhece as regras e o critério de "é nosso".
            self.relatorio.reiniciou_por_morte += 1
            self.proc = None
            self._esperar_antes_de_subir()
            self._subir("o processo anterior terminou")
            return
        if self._saudavel():
            self.relatorio.conferencias_ok += 1
            self.falhas = 0
            self.espera = self.espera_min_s       # sessão que vive: o próximo reinício volta a ser rápido
            self.reinicios_seguidos = 0
            self._dormir(self.intervalo_s)
            return
        partida = self._partida_em_curso()
        if partida is not None:
            # 29.124: vivo e ainda na partida desta subida. Não é travamento; é a máquina devagar.
            self.relatorio.esperou_a_partida += 1
            log.info("/api/health ainda não responde, mas o backend (pid %s) está subindo (%s); não conta como falha",
                     self.proc.pid, partida)
            self._dormir(self.intervalo_s)
            return
        self.falhas += 1
        self.relatorio.conferencias_falhas += 1
        log.warning("/api/health não respondeu (%s de %s)", self.falhas, self.falhas_ate_reiniciar)
        if self.falhas < self.falhas_ate_reiniciar:
            self._dormir(self.intervalo_s)
            return
        self.relatorio.reiniciou_por_silencio += 1
        self._derrubar_e_resubir(f"{self.falhas} conferências seguidas sem resposta")

    def run(self, ciclos: int | None = None) -> None:
        """Roda para sempre, ou `ciclos` passos (é assim que o teste o exercita)."""
        feitos = 0
        while ciclos is None or feitos < ciclos:
            self.ciclo()
            feitos += 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.supervisor",
                                description="Mantém o backend do central de pé: sobe no boot e religa se cair.")
    p.add_argument("--health", default="http://127.0.0.1:8000/api/health")
    p.add_argument("--intervalo", type=float, default=INTERVALO_S)
    p.add_argument("--carencia", type=float, default=CARENCIA_S)
    p.add_argument("--falhas", type=int, default=FALHAS_ATE_REINICIAR)
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)

    raiz_backend = Path(__file__).resolve().parent.parent
    log_dir = raiz_backend.parent / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(log_dir / "supervisor.log", encoding="utf-8"),
                                  logging.StreamHandler(sys.stderr)])
    _instalar_redacao(logging.getLogger())
    # A marca e o despejo do vigia ficam nas pastas do supervisor (`data/` e `data/logs/`), que ele passa ao backend
    # em `POC_PASTA_DO_SUPERVISOR`: um `paths.data_dir` ou `paths.logs_dir` diferente no config não as separa (N5).
    sup = Supervisor(iniciar=lambda: iniciar_backend(raiz_backend, log_dir, sup.partida_id),
                     saudavel=lambda: saude_responde(args.health),
                     carencia_s=args.carencia, intervalo_s=args.intervalo, falhas_ate_reiniciar=args.falhas,
                     despejo=lambda: ultimo_despejo(log_dir),
                     ler_marca=lambda: marca_de_partida.ler(log_dir.parent))
    log.info("supervisor no ar; vigiando %s a cada %.0f s", args.health, args.intervalo)
    try:
        sup.run()
    except KeyboardInterrupt:
        if sup.proc is not None:
            encerrar_processo(sup.proc)
        return 0
    return 1


def _instalar_redacao(raiz: logging.Logger) -> None:
    """O mesmo filtro de redação do backend e do agente: log de supervisor também vai parar em arquivo."""
    try:
        from .security.redaction import RedactingFilter  # noqa: PLC0415 - import tardio: o supervisor sobe sem
    except Exception:  # noqa: BLE001 - sem o filtro o supervisor ainda funciona; travar aqui seria pior
        return
    for handler in raiz.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())


if __name__ == "__main__":
    raise SystemExit(main())
