"""A entrega do aplicativo ao parque (15.15 K, F5b): o que o `AppState` sabe sobre qual release está em cada aparelho e como ela chega
lá. Moravam em `state.py`; aqui ficam juntos, sem mudar regra, motivo, evento nem ordem das chamadas:

* a convergência: `release_no_aparelho`, `fora_da_convergencia`, `aplicar_versao_promovida`, `adotar_promovidas`, `_entregar`,
  `_rebaixa_do_parque`, `_rollout_pending` e `distribute` (instalar em todos);
* a porta do app antes de uma tarefa: `_app_preflight`, `_app_preflight_do_pacote`, `_recusa_do_renderizador`, `_reler_antes_da_tarefa`
  e `_app_resolver`;
* o pacote de um aparelho ou de um app: `_pacote_do_app_id`, `_pacote_do_aparelho`, e `tem_o_app` / `_entregavel`.

O `AppState` guarda métodos finos com os mesmos nomes e assinaturas que delegam para cá (o scheduler recebe `state._app_resolver` e
`state._rollout_pending`, a vitrine e o despacho chamam `state._entregar`, e os testes chamam vários), e as constantes
`_ENTREGA_FALHOU`, `_ENTREGA_AUTOMATICA` e `_CANAIS_ABANDONADOS` continuam nele (a vitrine as lê de lá). Este módulo não importa `app.state`
em tempo de execução (ciclo): o estado entra pelo construtor e `AppState` só aparece em `TYPE_CHECKING`.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .commands import despacho
from .devices.compatibilidade import (
    capacidades_de,
    motivo_do_renderizador,
    motivo_incompativel,
    requisitos_de_release,
    requisitos_do_app,
)
from .devices.manager import DeviceRuntime
from .models import InstalledAppState, InstanceState
from .planning.catalog import capabilities_of
from .releases.catalog import ReleaseValidationError
from .releases.service import InstalacaoIncerta
from .util import now, parse_iso, to_iso
from .vitrine import alvos_da_distribuicao, previa_de_entrega

if TYPE_CHECKING:
    from .state import AppState

log = logging.getLogger("poc")

#: Depois de uma entrega de app que falhou, quanto esperar até a próxima tentativa automática (uma por dia).
RETENTATIVA_DE_ENTREGA_S = 24 * 3600


class Convergencia:
    """A entrega de apps de uma instalação: lê e escreve pelo estado que recebe (`release_repo`, `db`, `bus`, `devices`...)."""

    def __init__(self, state: AppState):
        self._st = state

    @staticmethod
    def tem_o_app(row: Any) -> bool:
        """O aparelho TEM este app: instalado por release conhecida, visto pelo `pm`, ou com versão já pedida.

        É o limite da convergência (ADR-026): atualizar quem tem, nunca espalhar o app para quem não tem — isso
        continua sendo "Distribuir", explícito. A exceção é o app principal do aparelho (`instances.app_id`), que já
        era estado desejado dele antes desta decisão (`aplicar_versao_promovida`, android-12..15).
        """
        return bool(row is not None and (row["installed_release_id"] or row["observed_version_code"] is not None
                                         or row["desired_release_id"]))

    def _entregavel(self, release_id: str | None) -> bool:
        rel = self._st.release_repo.release_row(release_id) if release_id else None
        return rel is not None and rel["status"] == "installable" and rel["channel"] == "promoted"

    def release_no_aparelho(self, row: Any) -> Any:
        """A release que ESTÁ no aparelho: a registrada como instalada; sem ela, a desejada, se o número que o
        aparelho respondeu é o dela.

        Revisão do PR #13: quando a instalação chega ao aparelho mas a prova de abertura falha, `install_on` guarda
        `observed_version_code` e a desejada, e limpa `installed_release_id`. Olhando só a instalada, um aparelho
        rodando a versão VOLTADA parecia ter "uma versão mais nova instalada por fora do catálogo" e nunca voltava."""
        if row is None:
            return None
        if row["installed_release_id"]:
            return self._st.release_repo.release_row(row["installed_release_id"])
        if row["observed_version_code"] is None:
            return None
        observado = int(row["observed_version_code"])
        if row["desired_release_id"]:
            desejada = self._st.release_repo.release_row(row["desired_release_id"])
            if desejada is not None and int(desejada["version_code"]) == observado:
                return desejada
        # A desejada já pode ter mudado (a convergência passou a perseguir a promovida): o catálogo diz de qual
        # release é o número observado. Voltada primeiro — é a que decide rebaixar com `-d` —, depois pelo id.
        candidatas = self._st.db.query("SELECT * FROM app_releases WHERE package_name=? AND version_code=?",
                                   (row["package_name"], observado))
        candidatas = sorted(candidatas, key=lambda r: (r["channel"] not in self._st._CANAIS_ABANDONADOS, str(r["id"])))
        return candidatas[0] if candidatas else None

    def fora_da_convergencia(self, row: Any, alvo: Any) -> str | None:
        """Por que ESTE aparelho fica na versão que tem, em vez de perseguir a promovida `alvo`. `None` = persegue.

        Dois casos, e só dois:

        * ele tem uma versão MAIS NOVA que ninguém rejeitou — a que está em prova no canário, ou uma instalada por
          fora do catálogo. Rebaixar sozinho desfaria a prova; o rebaixamento automático só acontece quando a
          versão instalada foi voltada (`_CANAIS_ABANDONADOS`);
        * ele já está numa versão PROMOVIDA de mesmo número. A produção tem duas promovidas 1.0.0/1 do app de QA
          (dois builds): trocar uma pela outra seria reinstalar o parque inteiro — e invalidar sessões — para ficar
          na mesma versão. "Atualizado" é pelo número, como na vitrine (`outdated`).
        """
        if row is None:
            return None
        instalada = self.release_no_aparelho(row)
        codigo_da_release = int(instalada["version_code"]) if instalada is not None else None
        # O que o aparelho respondeu manda (a Play Store pode ter atualizado o app por fora da release registrada).
        codigo = int(row["observed_version_code"]) if row["observed_version_code"] is not None else codigo_da_release
        if codigo is None:
            return None
        alvo_codigo = int(alvo["version_code"])
        abandonada = instalada is not None and instalada["channel"] in self._st._CANAIS_ABANDONADOS \
            and codigo == codigo_da_release
        if codigo > alvo_codigo and not abandonada:
            origem = (f"a {instalada['version_name']}, em '{instalada['channel']}'"
                      if instalada is not None and codigo == codigo_da_release
                      else f"o código {codigo}, instalado por fora do catálogo")
            return (f"tem uma versão mais nova que a promovida ({origem}); o parque não rebaixa sozinho uma versão "
                    "que ninguém voltou")
        if codigo == alvo_codigo and instalada is not None and instalada["id"] != alvo["id"] \
                and instalada["channel"] == "promoted" and instalada["status"] == "installable":
            return f"já está numa versão promovida de mesmo número ({instalada['version_name']} · {codigo})"
        return None

    def _ultima_tentativa_de_entrega(self, instance_id: str, package: str) -> datetime | None:
        """Quando a entrega deste app neste aparelho foi tentada pela última vez — o relógio da nova tentativa diária.

        Só o histórico de comandos não basta: a entrega da varredura e do "entrou no ar" roda por `run_device_job`,
        que não abre comando. Com o último comando de app de três dias atrás, cada passada da varredura (60 s)
        rearmaria e repetiria a mesma falha — o retry cego que o projeto proíbe. A prova de instalação
        (`app_release_validations`, stage `install`) é gravada a cada tentativa que chega ao aparelho, e `_entregar`
        grava a que falha antes disso.
        """
        marcas: list[datetime] = []
        # Fora os comandos de OUTRO app e os recusados antes de tocar no aparelho: com o máximo de todo `app.*` do
        # aparelho, a atividade do app principal adiava para sempre a nova tentativa de um secundário (revisão do
        # PR #13). Os comandos de app gravam `package` nos parâmetros (`_abrir_comando_de_app`); o que não diz o
        # pacote (comando antigo, sem parâmetros) continua contando — não dá para saber de quem é, e ignorá-lo faria
        # "sem tentativa no histórico" e nunca mais tentar.
        ultima = self._st.db.scalar("SELECT MAX(created_at) FROM commands WHERE instance_id=? AND verb LIKE 'app.%'"
                                " AND state <> 'rejected' AND (params LIKE ? OR params IS NULL"
                                " OR params NOT LIKE '%\"package\":%')",
                                (instance_id, f'%"package":"{package}"%'))
        if ultima:
            marcas.append(parse_iso(str(ultima)))
        prova = self._st.db.one(
            "SELECT v.observed_at FROM app_release_validations v JOIN app_releases r ON r.id = v.release_id"
            " WHERE v.instance_id=? AND r.package_name=? AND v.stage='install' ORDER BY v.id DESC LIMIT 1",
            (instance_id, package))
        if prova is not None and prova["observed_at"]:
            marcas.append(parse_iso(str(prova["observed_at"])))
        return max(marcas) if marcas else None

    def aplicar_versao_promovida(self, rt: DeviceRuntime, package: str | None = None) -> str | None:
        """A versão PROMOVIDA de um app é estado desejado do parque, não um ato pontual sobre quem existia na hora.

        "Distribuir" percorre os aparelhos daquele instante. android-12..15 foram criados um dia depois da
        distribuição do Instagram: ficaram sem linha em `device_app_state`, a porta do app não opinava, e uma
        tarefa de Instagram era despachada para um aparelho sem o aplicativo — o `open_app` falhava dentro da
        execução, consumindo tentativas. Aqui o aparelho que entra DEPOIS (ou que só agora foi vinculado ao app)
        passa a ter a mesma versão desejada dos irmãos, sem ninguém clicar em nada.

        ADR-026 (decisão do dono, 26/09: "todos devem ficar atualizados sempre"): vale para TODO app que o aparelho
        tem, não só o principal. `package=None` é o app principal (o comportamento de antes); um pacote secundário só
        é adotado por quem já o tem (`tem_o_app`) — espalhar o app continua sendo "Distribuir".

        Devolve o id da release adotada, ou `None` quando não há o que adotar. Entrega que falhou é tentada de novo
        no máximo uma vez por dia; recusa de voltar de versão nem isso — a saída dela apaga dados, é de pessoa.
        """
        if rt.store:
            return None
        principal = self._pacote_do_aparelho(rt.id)
        package = package or principal
        if not package:
            return None                       # aparelho sem app vinculado: o caminho antigo segue igual
        row = self._st.release_repo.app_state(rt.id, package)
        if package != principal and not self.tem_o_app(row):
            return None                       # nunca instala um app em quem não o tem
        rel = self._st.releases.promoted_release(package)
        if rel is None or rel.status.value != "installable":
            return None
        linha = self._st.release_repo.release_row(rel.id)
        if linha is None or motivo_incompativel(requisitos_de_release(linha), capacidades_de(rt),
                                                aparelho=rt.id) is not None:
            return None                       # mandar instalar o que não roda ali seria falha permanente
        if row is not None and row["pending_op"]:
            return None
        if self.fora_da_convergencia(row, linha) is not None:
            # Fica na versão que tem. Um desejo que apontava para uma versão que não pode mais ser entregue (a
            # voltada, a da quarentena) bloquearia a porta do app com "não pode mais ser entregue": ele passa a
            # ser a versão instalada, que é onde o aparelho vai ficar.
            if row["installed_release_id"] and row["desired_release_id"] != row["installed_release_id"] \
                    and row["desired_release_id"] and not self._entregavel(row["desired_release_id"]):
                self._st.release_repo.upsert_app_state(rt.id, package, desired_release_id=row["installed_release_id"])
            return None
        if row is not None and row["state"] in self._st._ENTREGA_FALHOU and row["drift_kind"] != "downgrade_refused" \
                and row["desired_release_id"] and row["desired_release_id"] != rel.id \
                and not self._entregavel(row["desired_release_id"]):
            # A falha foi da entrega de uma versão que o parque ABANDONOU (voltada, em quarentena): perseguir a
            # promovida é um alvo novo, não a repetição do que falhou — a trava diária não vale aqui (revisão do
            # PR #13: sem isto, o aparelho rodando a versão voltada ficava nela até alguém intervir).
            self._st.release_repo.upsert_app_state(
                rt.id, package, desired_release_id=rel.id, drift_kind=None,
                state="installed" if row["observed_version_code"] is not None else "missing",
                detail="a versão desejada saiu do parque; passa a perseguir a promovida")
            self._st.bus.emit("log", f"{rt.id}: {package} sai de uma versão que o parque abandonou e persegue a promovida "
                                 f"({rel.version_name} · {rel.version_code}).", level="info", instance_id=rt.id)
            return rel.id
        if row is not None and row["state"] in self._st._ENTREGA_FALHOU:
            if row["drift_kind"] == "downgrade_refused":
                return None               # o Android recusou voltar sem apagar os dados: quem decide é uma pessoa
            # Falha de entrega deixava de ser tentada para sempre. Continua sem "retry cego": a nova tentativa é UMA
            # por dia, contada desde a última tentativa de entrega deste app neste aparelho — o suficiente para um
            # aparelho que falhou por adb lento ou por convidado em thrash convergir sozinho depois que o motivo passou.
            quando = self._ultima_tentativa_de_entrega(rt.id, package)
            if quando is None or (now() - quando).total_seconds() < RETENTATIVA_DE_ENTREGA_S:
                return None               # sem tentativa no histórico não há "um dia depois" a contar
            self._st.release_repo.upsert_app_state(
                rt.id, package, desired_release_id=rel.id, drift_kind=None,
                state="installed" if row["observed_version_code"] is not None else "missing",
                detail="nova tentativa diária de entrega (a anterior falhou)")
            self._st.bus.emit("log", f"{rt.id}: nova tentativa diária de entregar {package} ({rel.version_name}); a "
                                 f"anterior terminou em '{row['state']}'.", level="warn", instance_id=rt.id)
            return rel.id
        if row is not None and row["desired_release_id"] == rel.id:
            return None
        if row is not None and row["installed_release_id"] == rel.id:
            if row["desired_release_id"] and not self._entregavel(row["desired_release_id"]):
                # Já está na promovida, e o desejo apontava para uma versão voltada: só alinha, nada a instalar.
                self._st.release_repo.upsert_app_state(rt.id, package, desired_release_id=rel.id)
            return None
        self._st.release_repo.upsert_app_state(rt.id, package, desired_release_id=rel.id)
        self._st.bus.emit("log", f"{rt.id}: passa a ter como desejada a versão promovida de {package} "
                             f"({rel.version_name} · {rel.version_code}).", level="info", instance_id=rt.id)
        return rel.id

    def adotar_promovidas(self, rt: DeviceRuntime) -> list[str]:
        """`aplicar_versao_promovida` para o app principal E para cada app que o aparelho tem (ADR-026).

        Só grava a versão desejada; quem instala é o trabalho do "entrou no ar", a varredura de 60 s ou a porta do
        app, pelas vias de sempre. Devolve os pacotes que passaram a ter uma versão a receber.
        """
        if rt.store:
            return []
        pacotes = [p for p in [self._pacote_do_aparelho(rt.id)] if p]
        pacotes += [r["package_name"] for r in self._st.db.query(
            "SELECT package_name FROM device_app_state WHERE instance_id=?", (rt.id,))]
        adotados: list[str] = []
        for package in dict.fromkeys(pacotes):
            try:
                if self.aplicar_versao_promovida(rt, package):
                    adotados.append(package)
            except Exception:  # noqa: BLE001 - um app com problema não impede os outros de convergir
                log.exception("%s: falha ao adotar a versão promovida de %s", rt.id, package)
        return adotados

    def _app_preflight(self, rt: DeviceRuntime, pacotes: Sequence[str] = ()) -> dict[str, str] | None:
        """Pré-voo do aplicativo: motivo para a tarefa não poder acontecer neste aparelho, sem tocar em nada.

        `None` = não impede. Inclui o caso "não se sabe": aparelho cujo aplicativo nunca foi observado não vira
        recusa aqui — quem o observa é a reobservação de quando ele entra no ar (`_reobservar_se_velho`), e o que
        ela apurar passa a valer na próxima criação. O que não se sabe nunca fecha a porta.

        Também não é recusa a entrega PENDENTE: versão promovida por instalar é resolvida pela porta do app,
        antes da tarefa. Recusa é só o que exige uma pessoa.

        Item 24.5: `pacotes` são os apps da tarefa (o conjunto do comando, ou os do plano no início). Cada um é
        conferido, depois do app principal do aparelho, pela MESMA regra; a primeira recusa vale, e a de um app que não
        é o principal diz o nome dele. Sem `pacotes`, só o principal, como antes.
        """
        principal = self._pacote_do_aparelho(rt.id)
        for package in dict.fromkeys(p for p in (principal, *pacotes) if p):
            recusa = self._app_preflight_do_pacote(rt, package)
            if recusa is not None:
                if package != principal:
                    recusa = {**recusa, "motivo": f"{capabilities_of(package).label}: {recusa['motivo']}"}
                return recusa
        return None

    @staticmethod
    def _recusa_do_renderizador(rt: DeviceRuntime, package: str) -> str | None:
        """O app declarou que não roda no renderizador DESTE aparelho (`renderizador_recusado`, 29.11)? A frase, ou
        `None`. Uma pergunta só para o pré-voo, a porta do app e a entrega: abrir o app aqui derruba o emulador."""
        porque = motivo_do_renderizador(requisitos_do_app(package), capacidades_de(rt), aparelho=rt.id)
        return None if porque is None else f"{porque[:1].upper()}{porque[1:]}."

    def _app_preflight_do_pacote(self, rt: DeviceRuntime, package: str) -> dict[str, str] | None:
        """O pré-voo de UM aplicativo neste aparelho (a regra de `_app_preflight`)."""
        # Antes de "tem linha?": o app pode já estar no aparelho (instalado quando ele era `host`, ou à mão), e a
        # tarefa o ABRIRIA. O requisito é do app naquele aparelho, com ou sem versão distribuída.
        if (renderizador := self._recusa_do_renderizador(rt, package)) is not None:
            return {"code": "app_incompativel", "motivo": renderizador,
                    "acao": "Troque o `gpu_mode` deste aparelho (e reinicie-o) ou escolha outro aparelho."}
        row = self._st.release_repo.app_state(rt.id, package)
        if row is None:
            return None
        # A ordem é a MESMA da porta (`_app_resolver` e depois `_app_gate`), de propósito: a recusa antes de
        # agendar e o bloqueio no meio contam a mesma história, com as mesmas palavras, e quem lê não precisa
        # traduzir uma na outra.
        desejada = row["desired_release_id"]
        if desejada and desejada != row["installed_release_id"]:
            rel = self._st.release_repo.release_row(desejada)
            if rel is None or rel["status"] != "installable" or rel["channel"] != "promoted":
                estado = f"{rel['status']}/{rel['channel']}" if rel is not None else "versão ausente do catálogo"
                return {"code": "app_no_release",
                        "motivo": f"a versão distribuída para este aparelho não pode mais ser entregue ({estado}).",
                        "acao": "Promova uma versão entregável na tela de Versões e repita a execução."}
            if row["state"] in self._st._ENTREGA_FALHOU:
                return {"code": "app_failed",
                        "motivo": "a entrega do aplicativo falhou neste aparelho e não é repetida sozinha: "
                                  f"{row['detail'] or row['state']}.",
                        "acao": "Use Distribuir de novo na tela de Versões e repita a execução."}
            if row["state"] in self._st._ENTREGA_AUTOMATICA:
                return None                      # entregável e sem falha: a porta do app instala antes da tarefa
        if row["state"] not in ("ready", "installed"):
            return {"code": "app_missing" if row["state"] == "missing" else "app_not_ready",
                    "motivo": f"o aplicativo não está pronto neste aparelho (estado: {row['state']})"
                              + (f": {row['detail']}" if row["detail"] else "") + ".",
                    "acao": "Distribua uma versão promovida para ele na tela de Versões e repita a execução."}
        return None

    def _pacote_do_app_id(self, app_id: str) -> str | None:
        """Id de app (o que a skill escreve) → pacote (o que o catálogo conhece). A tabela `apps` é por instalação."""
        row = self._st.apps.obter(app_id)
        return row["package"] if row is not None else None

    def _pacote_do_aparelho(self, instance_id: str) -> str | None:
        """O pacote do app que ESTE aparelho opera. Nulo quando o aparelho não tem app definido."""
        row = self._st.db.one("SELECT a.package FROM instances i JOIN apps a ON a.id = i.app_id WHERE i.id=?",
                          (instance_id,))
        return row["package"] if row else None

    def _reler_antes_da_tarefa(self, rt: DeviceRuntime, package: str, row: Any) -> tuple[str, Any | None] | None:
        """`verifying` SEM dono não bloqueia o item: ele manda reler o aparelho antes da tarefa.

        Este é o outro lado do achado #85. Com a linha parada em `verifying`, a porta do app bloqueava o
        objetivo com "O aplicativo não está pronto neste aparelho (estado: verifying)" — `waiting_user`, que
        ninguém retoma. Nem quando a releitura automática, mais tarde, resolvesse a linha para `ready`: o item
        já estava parado esperando uma pessoa.

        Aqui a releitura vira o TRABALHO da porta, no mesmo molde da sessão vencida: a tarefa espera, o aparelho
        é relido, e o tick seguinte despacha (ou bloqueia com o motivo verdadeiro: `missing`, `version_drift`).
        Operação com dono (`pending_op`) continua sem ser tocada — ali alguém ainda está trabalhando.
        """
        if row["state"] != InstalledAppState.verifying.value or row["pending_op"]:
            return None                          # installing, ou verifying com dono: a porta bloqueia pelo estado
        return ("o estado deste aplicativo ficou sem desfecho e vai ser relido do aparelho antes da tarefa",
                lambda: self._st.releases.verify_on(rt, package, self._st.installer))

    def _app_resolver(self, rt: DeviceRuntime, package: str, obj: Any) -> tuple[str, Any | None] | None:
        """Resolvedor da porta do app: há uma versão distribuída ainda por instalar neste aparelho?

        `None` = nada pendente, a porta decide só pelo estado. É o que faz o rodízio entregar o app sem ninguém pedir:
        só 4 aparelhos ficam ligados por vez, e os demais recebem a versão na próxima vez que pegarem uma tarefa
        daquele pacote — ANTES da tarefa.
        """
        # Quarentena (ADR-055): a porta do app vem ANTES da de sessão e termina na prova de ABERTURA do app —
        # instalar aqui abriria o app da conta travada. Bloqueia para uma pessoa, sem gravar versão desejada.
        if (quarentena := self._st.quarentena(rt.id)) is not None:
            return quarentena, None
        # Renderizador recusado pelo app (29.11): a tarefa abriria o app e derrubaria o emulador — com a versão já
        # instalada ou depois de instalá-la aqui. Bloqueia para uma pessoa, que troca o `gpu_mode` ou o aparelho.
        if (renderizador := self._recusa_do_renderizador(rt, package)) is not None:
            return renderizador, None
        row = self._st.release_repo.app_state(rt.id, package)
        if row is None or not row["desired_release_id"] or not self._entregavel(row["desired_release_id"]):
            # Aparelho que nunca recebeu distribuição daquele app: se existe versão promovida e este aparelho é
            # do app, ela passa a ser a desejada AQUI, antes da tarefa — em vez de a tarefa ir para um aparelho
            # sem o aplicativo e o `open_app` falhar lá dentro. ADR-026: o mesmo para o desejo que apontava para uma
            # versão voltada ou em quarentena — o aparelho persegue a promovida em vez de bloquear a tarefa.
            self.aplicar_versao_promovida(rt, package)
            row = self._st.release_repo.app_state(rt.id, package)
        # Estado SEM DESFECHO vem antes de tudo: ele não depende de haver versão distribuída. Sem esta ordem, o
        # aparelho parado em `verifying` caía no `return None` abaixo e a porta o bloqueava pelo estado.
        if row is not None and (releitura := self._reler_antes_da_tarefa(rt, package, row)) is not None:
            return releitura
        desejada = row["desired_release_id"] if row else None
        if not desejada or desejada == row["installed_release_id"]:
            return None
        rel = self._st.release_repo.release_row(desejada)
        if rel is None:
            return None
        rotulo = f"{rel['version_name']} ({rel['version_code']})"
        if rel["status"] != "installable" or rel["channel"] != "promoted":
            # Sem esta conferência, `install_on` recusaria sem mudar estado nenhum e o mesmo job voltaria a cada tick.
            return (f"A versão {rotulo} foi distribuída para este aparelho, mas não pode mais ser entregue "
                    f"(arquivo: {rel['status']}, ciclo de vida: {rel['channel']}).", None)
        if row["state"] in self._st._ENTREGA_FALHOU:
            return (f"A entrega da versão {rotulo} falhou neste aparelho e não é repetida sozinha: "
                    f"{row['detail'] or row['state']}. Use Distribuir de novo para tentar outra vez.", None)
        if row["state"] not in self._st._ENTREGA_AUTOMATICA:
            return None                          # installing (ou verifying com dono): alguém ainda trabalha nisto
        if obj["status"] != "pending":
            # Objetivo JÁ em andamento: trocar o app no meio mataria a navegação dele. Ele termina na versão que
            # tem; a entrega acontece antes do próximo objetivo.
            return None
        return (f"versão {rotulo} distribuída para o parque",
                lambda: self._entregar(rt, package, desejada))

    async def _entregar(self, rt: DeviceRuntime, package: str, release_id: str) -> Any:
        """Instala uma versão distribuída. Invólucro de `install_on` com UMA garantia a mais: falha sempre vira estado.

        `install_on` pode levantar antes de tocar no estado (arquivo ausente no catálogo, hash que não confere, adb
        que não responde ao ler o perfil). Sem registrar isso, a porta veria o aparelho "pronto para tentar" e
        dispararia o mesmo job a cada tick, para sempre.

        ADR-026: quando a versão instalada foi voltada e a promovida é MENOR, a entrega é um
        rebaixamento — vai com `-d`, preservando os dados, como o `rollback`. O Android pode recusar; a recusa fica
        nomeada (`downgrade_refused`) e não se repete sozinha: reinstalar resolve, mas apaga a sessão.
        """
        from .devices.installer import DowngradeRefused  # noqa: PLC0415

        if (quarentena := self._st.quarentena(rt.id)) is not None:
            # Última linha da quarentena (ADR-055): quem chega aqui por um caminho que não conferiu antes não
            # instala nem abre o app. Levanta ANTES do `try`: não é falha de entrega, e não vira `install_failed`.
            raise ReleaseValidationError(quarentena + ".")
        if (renderizador := self._recusa_do_renderizador(rt, package)) is not None:
            # Mesmo molde (29.11): recusa, e não falha de entrega. Como `install_failed`, ela ficaria pegajosa até
            # alguém pedir "Distribuir de novo" — e o que resolve é trocar o `gpu_mode` do aparelho, não repetir.
            raise ReleaseValidationError(renderizador)
        inicio = parse_iso(to_iso(now()))       # na resolução do banco (ms): comparável com `observed_at`
        rebaixar =self._rebaixa_do_parque(rt.id, package, release_id)
        try:
            return await self._st.releases.install_on(rt, release_id, self._st.installer, allow_downgrade=rebaixar)
        except InstalacaoIncerta:
            # Resultado DESCONHECIDO, não falha: `install_on` já deixou a linha em `verifying` sem operação
            # pendente, que é o estado com saída (a releitura automática resolve). Carimbar `install_failed`
            # aqui era justamente o defeito: um timeout de leitura virava estado pegajoso que só saía com
            # "Distribuir de novo" — o que REINSTALA um app que já estava instalado e funcionando.
            raise
        except Exception as exc:
            row = self._st.release_repo.app_state(rt.id, package)
            if row is None or row["state"] not in self._st._ENTREGA_FALHOU:
                self._st.release_repo.upsert_app_state(rt.id, package, state="install_failed", pending_op=None,
                                                   pending_op_at=None, detail=str(exc)[:300])
            if isinstance(exc, DowngradeRefused):
                self._st.release_repo.upsert_app_state(
                    rt.id, package, drift_kind="downgrade_refused",
                    detail=("A versão deste aparelho saiu do parque e o Android recusou voltar para a promovida "
                            if rebaixar else "O Android recusou instalar esta versão por cima de uma mais nova ")
                    + f"preservando os dados: {exc} Reinstalar resolve, mas apaga a sessão.")
            # O relógio da nova tentativa diária (`_ultima_tentativa_de_entrega`): a falha que acontece ANTES do
            # `adb install` (perfil, compatibilidade lida do aparelho, arquivo do catálogo) não deixou prova.
            prova = self._st.db.one("SELECT observed_at FROM app_release_validations WHERE release_id=? AND instance_id=?"
                                " AND stage='install' ORDER BY id DESC LIMIT 1", (release_id, rt.id))
            if prova is None or not prova["observed_at"] or parse_iso(str(prova["observed_at"])) < inicio:
                self._st.release_repo.record_validation(release_id, rt.id, stage="install", ok=False,
                                                    detail=str(exc)[:300])
            raise

    def _rebaixa_do_parque(self, instance_id: str, package: str, release_id: str) -> bool:
        """A entrega de `release_id` é o parque voltando de uma versão que ele abandonou (voltada)?"""
        row = self._st.release_repo.app_state(instance_id, package)
        instalada = self.release_no_aparelho(row)
        alvo = self._st.release_repo.release_row(release_id)
        return bool(instalada is not None and alvo is not None and instalada["channel"] in self._st._CANAIS_ABANDONADOS
                    and int(instalada["version_code"]) > int(alvo["version_code"]))

    def _rollout_pending(self) -> list[tuple[str, Any]]:
        """(aparelho, trabalho) de cada entrega imediata ainda por fazer. Chamado a cada tick: barato quando vazio.

        Usa as MESMAS travas da porta do app: só quem está em estado de entrega automática entra; quem falhou sai da
        fila e espera uma pessoa. Quando não sobra ninguém, a entrega imediata daquela release se encerra sozinha.
        """
        if not self._st._entrega_imediata:
            return []
        saida: list[tuple[str, Any]] = []
        for rid in list(self._st._entrega_imediata):
            rel = self._st.release_repo.release_row(rid)
            entregavel = rel is not None and rel["status"] == "installable" and rel["channel"] == "promoted"
            linhas = self._st.db.query("SELECT instance_id, state, installed_release_id FROM device_app_state"
                                   " WHERE desired_release_id=?", (rid,)) if entregavel else []
            candidatos = [r for r in linhas if r["installed_release_id"] != rid
                          and r["state"] in self._st._ENTREGA_AUTOMATICA and r["instance_id"] in self._st.devices.devices
                          and not self._st.devices.devices[r["instance_id"]].store]
            # Aparelho de outra máquina que está fora do ar NÃO é pendência desta entrega: o rodízio daqui não o
            # liga (`_rotate` exclui `rt.external`), então ele seguraria o conjunto aberto para sempre — duas
            # consultas por segundo, o aviso "Entrega imediata encerrada" nunca saindo, até alguém ligar o
            # aparelho à mão ou o backend reiniciar. A versão desejada continua gravada nele: quando voltar,
            # recebe pela porta do app, antes da tarefa, que é o caminho não-imediato de sempre.
            nao_ligaveis = [r["instance_id"] for r in candidatos
                            if self._st.devices.devices[r["instance_id"]].external
                            and self._st.devices.devices[r["instance_id"]].state != InstanceState.online]
            # Aparelho em quarentena (ADR-055) também não é pendência: ninguém entrega nada nele até uma pessoa
            # decidir, e ele seguraria a entrega imediata aberta para sempre.
            em_quarentena = [r["instance_id"] for r in candidatos if self._st.quarentena(r["instance_id"]) is not None]
            pendentes = [r for r in candidatos if r["instance_id"] not in nao_ligaveis
                         and r["instance_id"] not in em_quarentena]
            if not pendentes:
                self._st._entrega_imediata.discard(rid)
                prontos = sum(1 for r in linhas if r["installed_release_id"] == rid)
                falhas = sum(1 for r in linhas if r["state"] in self._st._ENTREGA_FALHOU)
                esperando = (f", {len(nao_ligaveis)} aguardando ser ligados em outro servidor "
                             f"({', '.join(sorted(nao_ligaveis))})" if nao_ligaveis else "")
                if em_quarentena:
                    esperando += (f", {len(em_quarentena)} em quarentena por conta travada "
                                  f"({', '.join(sorted(em_quarentena))})")
                self._st.bus.emit("log", f"Entrega imediata encerrada: {prontos} aparelho(s) na versão, {falhas} com falha"
                                     + esperando
                                     + ("" if entregavel else " — a versão deixou de poder ser entregue") + ".",
                              level="warn" if falhas or nao_ligaveis or em_quarentena or not entregavel else "info",
                              data={"release_id": rid})
                continue
            package = rel["package_name"]
            for r in pendentes:
                rt = self._st.devices.devices[r["instance_id"]]
                saida.append((rt.id, lambda rt=rt, package=package, rid=rid: self._entregar(rt, package, rid)))
        return saida

    def distribute(self, release_id: str, *, eager: bool = False, instance_ids: list[str] | None = None,
                   count: int | None = None, dry_run: bool = False) -> list[dict[str, Any]]:
        """Distribui uma versão PROMOVIDA ao parque. Canário primeiro: sem prova, não há o que distribuir.

        Grava a versão desejada em cada aparelho de tarefa. Quem está ligado e livre instala já; quem está desligado
        ou ocupado fica pendente e recebe pela porta do app, ao pegar a próxima tarefa daquele pacote. Pedir de novo
        é a nova tentativa explícita para quem tinha falhado.

        `eager` = "instalar em todos agora": além disso, os aparelhos pendentes viram demanda do rodízio, que os liga
        dentro das vagas, instala e cede a vaga ao próximo — sem esperar tarefa.

        Loja de apps (26/09): `instance_ids` restringe aos aparelhos escolhidos e `count` deixa o backend escolher N
        (ver `vitrine.escolher_para_distribuir`); sem os dois, o parque inteiro. `dry_run` é a prévia: o mesmo
        julgamento aparelho por aparelho, sem gravar nem instalar nada — o painel mostra ANTES de confirmar.
        """
        from .releases.catalog import ReleaseValidationError

        rel = self._st.release_repo.release_row(release_id)
        if rel is None:
            raise ReleaseValidationError("Release não encontrada.")
        if rel["status"] != "installable":
            raise ReleaseValidationError(f"A release está em '{rel['status']}' e não pode ser instalada.")
        if rel["channel"] != "promoted":
            raise ReleaseValidationError(
                f"Só se distribui versão PROMOVIDA; esta está em '{rel['channel']}'. Coloque-a em prova num aparelho, "
                "confira que instalou e abriu, promova — e então distribua.")
        package = rel["package_name"]
        requisitos = requisitos_de_release(rel)
        alvos = alvos_da_distribuicao(self._st, rel, instance_ids=instance_ids, count=count)
        saida: list[dict[str, Any]] = []
        for rt in alvos:
            if (quarentena := self._st.quarentena(rt.id)) is not None:
                # Conta travada logada (ADR-055): o aparelho fica como está, sem versão desejada — instalar termina
                # na prova de abertura do app. Quando uma pessoa resolver, a convergência o alcança.
                saida.append({"id": rt.id, "outcome": "kept", "reason": quarentena})
                continue
            if (porque := motivo_incompativel(requisitos, capacidades_de(rt), aparelho=rt.id)) is not None:
                # A versão desejada NÃO é gravada: mandar instalar o que não roda ali deixaria o aparelho em
                # falha permanente de entrega, e o operador sem saber por quê. A explicação sai com o resultado.
                saida.append({"id": rt.id, "outcome": "incompatible", "reason": porque})
                continue
            row = self._st.release_repo.app_state(rt.id, package)
            if row and row["installed_release_id"] == release_id and row["state"] in ("ready", "installed"):
                if not dry_run:
                    self._st.release_repo.upsert_app_state(rt.id, package, desired_release_id=release_id)
                saida.append({"id": rt.id, "outcome": "already", "reason": "já está nesta versão"})
                continue
            if dry_run:
                saida.append(previa_de_entrega(self._st, rt, row, eager=eager))
                continue
            campos: dict[str, Any] = {"desired_release_id": release_id}
            if row and row["state"] in self._st._ENTREGA_FALHOU:
                # Nova tentativa pedida por uma pessoa: rearma a ÚNICA tentativa automática.
                campos.update(state="installed" if row["observed_version_code"] is not None else "missing",
                              drift_kind=None, detail="nova tentativa de entrega pedida")
            self._st.release_repo.upsert_app_state(rt.id, package, **campos)
            if rt.state.value != "online":
                # A promessa tem de ser a do CÓDIGO: `_rotate` exclui `rt.external` da demanda, então ninguém
                # daqui liga um aparelho de outra máquina. Dizer "o rodízio vai ligá-lo para instalar agora" era
                # afirmar o que não vai acontecer — e, pior, prendia a entrega imediata aberta para sempre.
                if rt.external:
                    motivo = (f"está em outro servidor e {rt.state.value}: ninguém aqui o liga. Ligue-o pela "
                              "Infraestrutura; a versão já está marcada e instala quando ele voltar")
                elif eager:
                    motivo = f"está {rt.state.value}: o rodízio vai ligá-lo para instalar agora"
                else:
                    motivo = f"está {rt.state.value}: instala ao entrar em serviço, antes da tarefa"
                saida.append({"id": rt.id, "outcome": "pending", "reason": motivo})
            else:
                # UM COMANDO POR APARELHO: a entrega deixa de ser um `202 {"accepted": true}` coletivo cujo
                # desfecho só aparecia relendo `GET /api/app-state`. Cada aparelho ganha id acompanhável, e o
                # timeout do adb vira `uncertain` em vez de falha pegajosa.
                cmd = despacho._despachar_trabalho(
                    self._st, rt, "app.distribute", lambda rt=rt: self._entregar(rt, package, release_id),
                    label="entrega do aplicativo", params={"release_id": release_id, "package": package},
                    recusar_ocupado=False,
                    ocupado="ocupado agora: instala quando pegar a próxima tarefa")
                if cmd.get("accepted"):
                    saida.append({"id": rt.id, "outcome": "started", "reason": "instalando agora",
                                  "command_id": cmd["command_id"]})
                else:
                    saida.append({"id": rt.id, "outcome": "pending", "reason": cmd["reason"],
                                  "command_id": cmd["command_id"]})
        if dry_run:
            return saida                          # prévia: nada foi gravado, nada a acordar nem a anunciar
        if eager:
            self._st._entrega_imediata.add(release_id)
            self._st.scheduler.wake()
        self._st.bus.emit("log", f"{package} {rel['version_name']} ({rel['version_code']}) distribuída"
                             f"{' — instalar em todos agora' if eager else ''}: "
                             + ", ".join(f"{d['id']}={d['outcome']}" for d in saida),
                      data={"release_id": release_id})
        return saida
