"""Item 10.1 — achados #136 e #38: nada religava o backend do central.

O boot provava que o TÚNEL volta (tarefa com gatilho de boot); o backend existia porque alguém o iniciara à mão
numa sessão do console. Logoff, Windows Update ou crash derrubavam API, agendador e Appium até intervenção
humana, com o agente do worker reconectando no vazio e a fila parada.

O que este arquivo trava, sem subir processo nenhum (tudo entra pelo construtor do `Supervisor`):

* processo que morre é religado;
* `/api/health` que para de responder **três vezes seguidas** derruba e religa — porque uma falha isolada é
  coleta de lixo, não travamento;
* `degraded` NÃO reinicia: é resposta, e reiniciar trocaria um problema visível por um laço de reinício (e
  apagaria o Appium que o próprio backend acabou de subir);
* a espera entre reinícios cresce e volta ao mínimo quando a saúde volta.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from app.supervisor import Supervisor, saude_responde

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


class ProcessoFalso:
    """O mínimo do `Popen` que o supervisor usa, com o fim do processo sob controle do teste."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.codigo: int | None = None
        self.encerrado = False

    def morrer(self, codigo: int = 1) -> None:
        self.codigo = codigo

    def poll(self) -> int | None:
        return self.codigo

    def terminate(self) -> None:
        self.encerrado = True
        self.codigo = 0

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout: float | None = None) -> int:
        return self.codigo or 0


class Bancada:
    """Um supervisor com tudo falso: partida, saúde, encerramento e sono."""

    def __init__(self, saude: list[bool], porta_de_outro: bool = False) -> None:
        self.saude = list(saude)
        #: Alguém FORA deste supervisor responde na porta — o backend iniciado à mão, que é o estado do central
        #: hoje. É a situação que não pode virar um laço de subidas.
        self.porta_de_outro = porta_de_outro
        self.processos: list[ProcessoFalso] = []
        self.sonos: list[float] = []
        self.encerrados: list[ProcessoFalso] = []
        self.sup = Supervisor(iniciar=self._iniciar, saudavel=self._saudavel, encerrar=self._encerrar,
                              dormir=self.sonos.append, carencia_s=90.0, intervalo_s=15.0,
                              espera_min_s=5.0, espera_max_s=60.0)

    def _iniciar(self) -> ProcessoFalso:
        p = ProcessoFalso(pid=1000 + len(self.processos))
        self.processos.append(p)
        return p

    def _saudavel(self) -> bool:
        """Sem processo vivo ninguém responde — a não ser que a porta seja de outro backend. Só com processo de
        pé é que o roteiro do teste manda; assim a conferência ANTES da partida é realista e não consome o
        roteiro das conferências de depois."""
        if self.porta_de_outro:
            return True
        vivo = self.processos and self.processos[-1].poll() is None
        if not vivo:
            return False
        return self.saude.pop(0) if self.saude else True

    def _encerrar(self, proc: ProcessoFalso) -> None:
        proc.terminate()
        self.encerrados.append(proc)

    @property
    def atual(self) -> ProcessoFalso:
        return self.processos[-1]


def test_a_primeira_partida_sobe_o_backend_e_espera_a_carencia() -> None:
    b = Bancada(saude=[True])
    b.sup.run(ciclos=1)
    assert len(b.processos) == 1
    # A primeira conferência não pode acontecer em cima da partida: o backend migra o banco e sobe o Appium.
    assert b.sonos == [90.0]


def test_processo_que_morre_e_religado_sem_esperar_tres_falhas() -> None:
    b = Bancada(saude=[True, True])
    b.sup.run(ciclos=1)
    b.atual.morrer()
    b.sup.run(ciclos=1)
    assert len(b.processos) == 2, "o backend não voltou depois de morrer"
    assert b.sup.relatorio.reiniciou_por_morte == 1
    assert b.encerrados == [], "processo já morto não precisa ser encerrado"


def test_silencio_de_saude_so_reinicia_na_terceira_falha_seguida() -> None:
    b = Bancada(saude=[False, False, False])
    b.sup.run(ciclos=1)                       # partida
    b.sup.run(ciclos=2)                       # duas falhas: ainda não reinicia
    assert len(b.processos) == 1, "reiniciou cedo demais: uma falha isolada não é travamento"
    b.sup.run(ciclos=1)                       # terceira falha
    assert len(b.processos) == 2
    assert b.sup.relatorio.reiniciou_por_silencio == 1
    assert b.encerrados[0] is b.processos[0], "o processo travado tem de ser encerrado antes do novo subir"


def test_uma_conferencia_boa_no_meio_zera_a_contagem() -> None:
    b = Bancada(saude=[False, False, True, False, False])
    b.sup.run(ciclos=1)
    b.sup.run(ciclos=5)
    assert len(b.processos) == 1, "duas falhas, uma boa e mais duas falhas não são três seguidas"
    assert b.sup.falhas == 2


def test_a_espera_entre_reinicios_cresce_e_volta_ao_minimo_quando_a_saude_volta() -> None:
    b = Bancada(saude=[False, False, False, False, False, False])
    b.sup.run(ciclos=1)
    b.sup.run(ciclos=3)                       # primeiro reinício: dorme o mínimo
    assert b.sup.espera == 10.0
    b.sup.run(ciclos=3)                       # segundo reinício seguido: dobra
    assert b.sup.espera == 20.0
    b.saude = [True]
    b.sup.run(ciclos=1)
    assert b.sup.espera == 5.0, "sessão que vive devolve a espera ao mínimo"


def test_degraded_nao_reinicia_nada() -> None:
    """`degraded` é RESPOSTA — API de pé, dizendo o que está ruim (Appium fora, IA sem chave). Reiniciar por
    causa disso é trocar um problema visível por um laço de reinício, e apagaria o Appium que o backend subiu.
    O supervisor pergunta 'respondeu?', não 'está bom?'."""
    b = Bancada(saude=[True] * 10)            # `saude_responde` devolve True para 200 E para 503
    b.sup.run(ciclos=1)
    b.sup.run(ciclos=9)
    assert len(b.processos) == 1
    assert b.sup.relatorio.reiniciou_por_silencio == 0


def test_saude_responde_trata_503_da_farm_como_vivo_e_porta_fechada_como_morto() -> None:
    # Antes o corpo era só `{"status":"degraded"}` e passava: QUALQUER erro HTTP contava como vivo — foi assim que
    # o 404 do `cartorio-api-1` segurou a subida da Farm em 26/09/2026. Agora o 503 precisa ser DA Farm
    # (`service`); os casos estrangeiros estão em `test_identidade_do_backend.py`.
    import http.server
    import threading

    from app.identidade import SERVICO

    class Degradado(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:                      # noqa: N802 - assinatura do http.server
            self.send_response(503)
            self.end_headers()
            self.wfile.write(('{"service":"%s","status":"degraded"}' % SERVICO).encode())

        def log_message(self, *a: object) -> None:     # silêncio no pytest
            return

    srv = http.server.HTTPServer(("127.0.0.1", 0), Degradado)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert saude_responde(f"http://127.0.0.1:{srv.server_port}/api/health", timeout=3) is True
    finally:
        srv.shutdown()
    # Mesma porta, agora sem ninguém do outro lado: é isto que conta como silêncio.
    assert saude_responde(f"http://127.0.0.1:{srv.server_port}/api/health", timeout=2) is False


# ------------------------------------------------------------------ o registro da tarefa, sem registrar nada
# `shutil.which` e não `where`: `where` só existe no Windows, e num Linux o próprio marcador estouraria na
# coleta do pytest — o arquivo inteiro deixaria de rodar.
# Estes scripts são do Windows (tarefa agendada, %LOCALAPPDATA%, contas locais): o pwsh do Linux do CI existe,
# mas quebra nos caminhos do Windows antes de chegar ao que o teste prova (backlog B13).
pwsh = pytest.mark.skipif(shutil.which("pwsh") is None or os.name != "nt",
                              reason="pwsh no Windows é pré-requisito destes scripts")
# O instalador recusa árvore sem o venv do backend (é o executável que a tarefa aponta). No checkout do CI no
# runner próprio da máquina central (Windows, venv por job em RUNNER_TEMP) ele não existe: mesmo critério do
# `test_backup` — o teste roda onde o venv do projeto existe (máquina central e worktrees com a junção).
venv_do_projeto = pytest.mark.skipif(not (SCRIPTS.parent / "backend" / ".venv" / "Scripts" / "python.exe").exists(),
                                     reason="install-central-service.ps1 exige o venv do backend nesta árvore")


def _simular(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["pwsh", "-NoProfile", "-File", str(SCRIPTS / script), "-Simular", *args],
                          capture_output=True, text=True, timeout=180)


@pwsh
@venv_do_projeto
def test_a_tarefa_do_central_aponta_para_um_executavel_de_caminho_estavel() -> None:
    """Não é `pwsh`: neste central o único pwsh é o pacote da Store, cujo caminho carrega a versão e some na
    próxima atualização (achado #138). O python do venv é caminho desta árvore, e não muda sozinho."""
    r = _simular("install-central-service.ps1")
    assert r.returncode == 0, r.stdout + r.stderr
    executavel = next(l for l in r.stdout.splitlines() if l.startswith("executavel: "))
    assert executavel.endswith(r".venv\Scripts\python.exe"), executavel
    assert "WindowsApps" not in executavel
    assert "-m app.supervisor" in r.stdout                  # supervisor, não `app.main` cru
    assert "gatilho: AtStartup" in r.stdout
    assert "RestartCount=999" in r.stdout
    assert "simulacao: nada foi registrado nem iniciado" in r.stdout


@pwsh
@venv_do_projeto
def test_o_ensaio_do_start_nao_registra_tarefa_com_nome_de_parametro() -> None:
    """`start.ps1 -Instalar -Simular` repassava as opções por splat de array, e `@('-Simular')` liga ao primeiro
    parâmetro POSICIONAL do instalador (`-Tarefa`): o ensaio registrou e INICIOU uma tarefa chamada `-Simular`.
    Duas travas agora: o repasse é explícito, e o instalador recusa nome de tarefa que comece por `-`."""
    r = _simular("start.ps1", "-Instalar")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "tarefa: farm-central" in r.stdout
    assert "simulacao: nada foi registrado nem iniciado" in r.stdout

    # `-Command` com o valor entre aspas é o jeito de fazer o texto CHEGAR como valor (por `-File`, o próprio
    # analisador do PowerShell já recusa antes). É a forma exata que o splat produzia.
    ruim = subprocess.run(
        ["pwsh", "-NoProfile", "-Command",
         f"& '{SCRIPTS / 'install-central-service.ps1'}' -Tarefa '-Simular'"],
        capture_output=True, text=True, timeout=180)
    assert ruim.returncode != 0
    assert "nome de tarefa" in (ruim.stdout + ruim.stderr)


# ---------------------------------------------------------- o outro lado: o worker que voltou do reboot
# Achados #137 e #38. Reiniciar o notebook do parque deixava os seis aparelhos fora: o agente voltava, declarava
# tudo `stopped`, e ninguém confrontava isso com o que o central QUERIA. `desired_state` já era gravado para
# aparelho de worker; faltava alguém agir sobre ele na reconexão.
class _Rt:
    def __init__(self, iid: str, worker_id: str | None, desired: str | None) -> None:
        self.id, self.worker_id, self.desired_state = iid, worker_id, desired


class _Dev:
    def __init__(self, instance_id: str, state: str) -> None:
        self.instance_id, self.state = instance_id, state


class _EstadoFalso:
    def __init__(self, rts: list[_Rt]) -> None:
        class _D:
            devices = {rt.id: rt for rt in rts}

        class _Bus:
            linhas: list[str] = []

            def emit(self, _tipo: str, mensagem: str, **_kw: object) -> None:
                self.linhas.append(mensagem)

        self.devices = _D()
        self.bus = _Bus()


def _reconciliar(monkeypatch: pytest.MonkeyPatch, rts: list[_Rt], declarados: list[_Dev]) -> list[str]:
    from app.commands import despacho

    pedidos: list[tuple[str, str]] = []

    def falso(_s: object, instance_id: str, verb: str, _motivo: str, **_kw: object) -> str:
        pedidos.append((instance_id, verb))
        return "cmd-1"

    monkeypatch.setattr(despacho, "pedir_ciclo_de_vida", falso)
    devolvidos = despacho.reconciliar_estado_desejado(_EstadoFalso(rts), "worker-lan-01", declarados)  # type: ignore[arg-type]
    assert [i for i, _ in pedidos] == devolvidos
    return [f"{i}:{v}" for i, v in pedidos]


def test_o_aparelho_que_devia_estar_no_ar_volta_quando_o_worker_reconecta(
        monkeypatch: pytest.MonkeyPatch) -> None:
    rts = [_Rt("android-09", "worker-lan-01", "online"), _Rt("android-10", "worker-lan-01", "online")]
    declarados = [_Dev("android-09", "stopped"), _Dev("android-10", "absent")]
    assert _reconciliar(monkeypatch, rts, declarados) == ["android-09:start", "android-10:start"]


def test_quem_foi_parado_de_proposito_continua_parado(monkeypatch: pytest.MonkeyPatch) -> None:
    """`desired_state=stopped` é decisão de alguém. Religar por cima seria o central desfazendo um clique."""
    rts = [_Rt("android-09", "worker-lan-01", "stopped"), _Rt("android-10", "worker-lan-01", None)]
    declarados = [_Dev("android-09", "stopped"), _Dev("android-10", "stopped")]
    assert _reconciliar(monkeypatch, rts, declarados) == []


def test_aparelho_ja_no_ar_ou_de_estado_desconhecido_nao_e_tocado(monkeypatch: pytest.MonkeyPatch) -> None:
    """`unknown` é "não sei", e "não sei" nunca autoriza ligar nada — inclusive porque a sondagem do agente
    demora dezenas de segundos e o `hello` sai antes dela."""
    rts = [_Rt("android-09", "worker-lan-01", "online"), _Rt("android-10", "worker-lan-01", "online"),
           _Rt("android-11", "worker-lan-01", "online")]
    declarados = [_Dev("android-09", "online"), _Dev("android-10", "unknown"), _Dev("android-11", "booting")]
    assert _reconciliar(monkeypatch, rts, declarados) == []


def test_aparelho_de_outra_maquina_nao_e_religado_por_este_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Um `worker.yaml` desatualizado declara aparelho que é de outra máquina; agir nisso ligaria o aparelho
    errado. A divergência já é denunciada por `conferir_inventario` — aqui ela só não vira ação."""
    rts = [_Rt("android-09", "worker-lan-02", "online"), _Rt("android-99", None, "online")]
    declarados = [_Dev("android-09", "stopped"), _Dev("android-99", "stopped"), _Dev("android-77", "stopped")]
    assert _reconciliar(monkeypatch, rts, declarados) == []


def test_nao_sobe_um_segundo_backend_quando_a_porta_ja_responde() -> None:
    """O central de hoje tem um backend iniciado à mão. Sem esta guarda, registrar o serviço viraria um laço:
    o filho sobe, roda `AppState.__init__` (que MIGRA o banco), não consegue ligar a porta e morre — e o
    supervisor tenta de novo em até 60 s. Aconteceu uma vez, por um ensaio mal repassado; não pode virar rotina."""
    b = Bancada(saude=[], porta_de_outro=True)
    b.sup.run(ciclos=3)
    assert b.processos == [], "subiu um backend concorrente com outro já respondendo na porta"
    assert b.sup.relatorio.recusou_por_ja_haver_backend == 3
    assert b.sonos == [15.0, 15.0, 15.0], "deve esperar o intervalo normal, não martelar"


class _Link:
    """O mínimo do `WorkerLink` que a reconciliação usa: a marca de "já reconciliei NESTA conexão"."""

    def __init__(self) -> None:
        self.reconciliado = False


def test_o_hello_nao_religa_nada_porque_ele_declara_tudo_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """`agent._declarados()` manda todo aparelho como `unknown` de propósito: sondar seis custa ~28 s e o
    handshake morreria antes. Uma reconciliação pendurada no `hello` seria código morto contra o agente real."""
    from app.commands import despacho

    pedidos: list[str] = []
    monkeypatch.setattr(despacho, "pedir_ciclo_de_vida",
                        lambda _s, iid, *_a, **_k: pedidos.append(iid) or "cmd-1")
    estado = _EstadoFalso([_Rt("android-09", "worker-lan-01", "online")])
    link = _Link()
    despacho._reconciliar_uma_vez(estado, "worker-lan-01", link, [_Dev("android-09", "unknown")])  # type: ignore[arg-type]
    assert pedidos == [] and link.reconciliado is False, "gastou a única reconciliação com 'não sei'"


def test_a_primeira_batida_com_estado_de_verdade_religa_e_a_segunda_nao(
        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.commands import despacho

    pedidos: list[str] = []
    monkeypatch.setattr(despacho, "pedir_ciclo_de_vida",
                        lambda _s, iid, *_a, **_k: pedidos.append(iid) or "cmd-1")
    estado = _EstadoFalso([_Rt("android-09", "worker-lan-01", "online")])
    link = _Link()
    despacho._reconciliar_uma_vez(estado, "worker-lan-01", link, [_Dev("android-09", "stopped")])  # type: ignore[arg-type]
    assert pedidos == ["android-09"]
    # Batida seguinte: o aparelho está `booting`; um segundo pedido só disputaria com o primeiro.
    despacho._reconciliar_uma_vez(estado, "worker-lan-01", link, [_Dev("android-09", "stopped")])  # type: ignore[arg-type]
    assert pedidos == ["android-09"], "reconciliou duas vezes na mesma conexão"

    # Conexão nova (link novo) = reconciliação nova: é o worker que voltou de outro reboot.
    despacho._reconciliar_uma_vez(estado, "worker-lan-01", _Link(), [_Dev("android-09", "stopped")])  # type: ignore[arg-type]
    assert pedidos == ["android-09", "android-09"]


# ---------------------------------------------------------------- os emuladores sobrevivem ao reinício
class _FilhoFalso:
    def __init__(self, nome: str) -> None:
        self._nome, self.morto = nome, False

    def name(self) -> str:
        return self._nome

    def kill(self) -> None:
        self.morto = True


def test_a_varredura_de_filhos_mata_o_appium_e_poupa_os_emuladores(monkeypatch: pytest.MonkeyPatch) -> None:
    """`children(recursive=True)` alcançava os emuladores e a docstring só falava do Appium: cada reinício por falha
    de saúde derrubava o parque local inteiro — e o backend seguinte READOTA emulador vivo pelo PID. Matar era
    perder boot e estado à toa."""
    import psutil

    from app.supervisor import _matar_filhos

    filhos = [_FilhoFalso("node.exe"), _FilhoFalso("emulator.exe"), _FilhoFalso("qemu-system-x86_64.exe"),
              _FilhoFalso("adb.exe")]

    class _PaiFalso:
        def __init__(self, _pid: int) -> None: ...
        def children(self, recursive: bool = False) -> list[_FilhoFalso]:
            assert recursive
            return filhos

    monkeypatch.setattr(psutil, "Process", _PaiFalso)
    _matar_filhos(4242)
    assert [f.morto for f in filhos] == [True, False, False, True]
