"""A saúde do sistema (`GET /api/health`, o status da IA e a migração em uso): o que `AppState` calculava em dez métodos
soltos, agora num objeto próprio (15.15 F2, o MOVE). O resultado é o mesmo, problema por problema e na mesma ordem: o teste
de caracterização `tests/test_saude_caracterizacao.py` (golden em `tests/golden/`) fixa cada cenário e passou igual antes e
depois da mudança.

Não importa `app.state` em tempo de execução (ciclo): `AppState` entra só em `TYPE_CHECKING` e a saúde LÊ dele o que
precisa por `self._e`. `AppState` continua expondo `health()`, `ai_status()` e `ultima_migracao()` como delegações de uma
linha, para o resto do código (rotas, portas, testes) não mudar de chamada. Portas estreitas no lugar da referência ao
`AppState` ficam para um corte próprio.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import psutil

from app.models import AiStatus, AppiumStatus, DatabaseStatus, Health, InstanceState, Problem, SdkStatus
from app.modules.avisos.infrastructure.trello_saude import problemas_do_trello
from app.modules.identity.infrastructure.persona_images import status_de_imagem
from app.modules.learning.infrastructure.contador_pelo_observar import ContadorPeloObservar
from app.modules.skills.infrastructure.contador_do_ensino_v2 import ContadorDoEnsinoV2
from app.planning import saldos
from app.planning.decisao_fechada import transparencia
from app.planning.routing import perfis_para_o_painel
from app.security.access import publicos_de
from app.util import now, to_iso
from app.version import VERSION, commit_em_execucao

if TYPE_CHECKING:
    from app.state import AppState

log = logging.getLogger("poc")


class SaudeDoSistema:
    def __init__(self, estado: AppState) -> None:
        self._e = estado
        #: O último `health()` que virou evento `health.updated` (achado #65): só emite quando o resultado muda.
        self._last_health: dict[str, object] | None = None

    def health(self) -> Health:
        problems: list[Problem] = []
        banco, problemas_do_banco = self._saude_do_banco()
        problems.extend(problemas_do_banco)
        sdk_ok = self._e.tools.found()
        if not sdk_ok:
            problems.append(Problem(code="sdk_missing", message=f"Android SDK não encontrado em {self._e.cfg.sdk_root}.",
                                    hint="Rode scripts/install-prereqs.ps1 ou ajuste android.sdk_root / ANDROID_SDK_ROOT."))
        # A conferência da imagem só olhava a PADRÃO. Uma imagem de override ausente (a da loja, com Play Store) só
        # aparecia como erro no primeiro boot daquele aparelho — nunca aqui, onde dá tempo de resolver antes.
        for iid, imagem in (self._e.cfg.override_images().items() if sdk_ok else ()):
            if not self._e.tools.system_image_dir(imagem).exists():
                problems.append(Problem(
                    code="system_image_missing",
                    message=f"A imagem de sistema de {iid} não está instalada: {imagem}.",
                    hint=f'Instale com: sdkmanager "{imagem}" (ou scripts/install-prereqs.ps1 -ImageTags …). '
                         "Os demais aparelhos seguem funcionando."))
        # Aparelho no ar e INÚTIL (Android morto por dentro, sessão que nunca abre) entra na saúde do sistema.
        # Antes, três dos quatro aparelhos remotos ligados estavam assim e `/api/health` só listava o Appium.
        degradados = [rt.id for rt in self._e.devices.devices.values()
                      if rt.state == InstanceState.error and rt.attention]
        if degradados:
            problems.append(Problem(
                code="devices_degraded",
                message=f"{len(degradados)} aparelho(s) respondem ao ADB mas não estão utilizáveis: "
                        + ", ".join(sorted(degradados)) + ".",
                hint="Veja o motivo no cartão de cada um (Infraestrutura). Reinicie o aparelho — de preferência a "
                     "frio — e confira se a sessão de automação abre."))
        # Item 10.3 (achado #146): o alvo local já está decidido e escrito (`limits.max_online_devices`) — o que
        # faltava era o central AVISAR quando a RAM livre agora não cobre esse alvo, em vez de deixar o rodízio
        # descobrir aos trancos (recusando boot por boot). Mesma conta do portão real de boot
        # (`devices/manager.py::_boot`): `est_instance_ram_mb` por instância e `min_free_ram_mb_after_boot` de
        # folga — para o número bater com o que de fato recusa ou aceita um boot, não uma estimativa à parte.
        problema_capacidade = self._problema_de_capacidade_local()
        if problema_capacidade is not None:
            problems.append(problema_capacidade)
        # ADR-055: conta travada logada em aparelho LIGADO. O android-04 passou horas no ar com a conta no desafio
        # e a saúde não dizia nada; um aparelho assim é um risco à conta enquanto estiver de pé.
        if (travadas := self._e._contas_travadas_no_ar()):
            problems.append(Problem(
                code="locked_account_on_device",
                message=f"{len(travadas)} aparelho(s) ligado(s) com conta travada logada: " + ", ".join(travadas)
                        + ".",
                hint="O aparelho está em quarentena: nada o toca além de parar ou hibernar. O desafio é com a pessoa "
                     "(ADR-009); decida o destino do aparelho — reset ou religar só com a confirmação explícita "
                     "(confirm_locked_account)."))
        # Achado #179: o túnel SSH é o único transporte do ADB remoto e do canal do agente. Sem este problema
        # dedicado, a queda dele só aparecia como sintomas espalhados (aparelhos "sem ADB", worker "sem batida"),
        # sem nada apontando a causa comum.
        tuneis_fora = [w for w in self._e.workers.dtos() if w.transport_state == "down"]
        if tuneis_fora:
            problems.append(Problem(
                code="tunnel_down",
                message=(f"{len(tuneis_fora)} túnel(is) fora: " + ", ".join(w.name for w in tuneis_fora) + "."),
                hint="A porta LOCAL do túnel está recusando conexão — o worker remoto pode estar de pé; é o "
                     "transporte que caiu. Veja data/logs/tunel-*.log; a tarefa agendada "
                     "farm-tunel-<worker> (scripts/worker-tunnel.ps1) reconecta sozinha."))
        # Medido: `config/config.yaml` recriado do exemplo trouxe `worker_port: 0` e o backend subiu com o canal do
        # worker atendendo na porta principal — onde um `-R` do túnel expõe a API inteira à máquina do worker. O
        # padrão `0` é o certo para parque numa máquina só; com worker REMOTO inscrito ele vira problema de saúde.
        remotos = [w for w in self._e.workers.dtos() if not w.local]
        if remotos and not int(self._e.cfg.file.server.worker_port or 0):
            problems.append(Problem(
                code="worker_channel_shared",
                message=(f"{len(remotos)} worker(s) remoto(s) inscrito(s) e o listener dedicado do canal do worker "
                         "está desligado (server.worker_port: 0)."),
                hint="Ligue server.worker_port (ex.: 8010) em config/config.yaml e reinicie; aponte o -R do túnel "
                     "para ela. Com o canal na porta principal, o túnel deixa a API REST ao alcance do worker."))
        problema_exposicao = self._problema_de_exposicao_publica()
        if problema_exposicao is not None:
            problems.append(problema_exposicao)
        problems.extend(Problem(code=codigo, message=mensagem, hint=dica)
                        for codigo, mensagem, dica in self._e.portal.problemas())     # 29.77: só nomes e contagens
        appium_up = self._e.appium.is_up(timeout=1.0)
        if not appium_up:
            problems.append(Problem(code="appium_down", message=self._e.appium.detail or "Servidor Appium não está respondendo.",
                                    hint="Verifique tools/appium (npm ci) e data/logs/appium.log; o controle manual segue funcionando."))
        elif not self._e.appium.log_masking_active:
            # Sem mascaramento comprovado, o Appium gravaria em claro tudo o que for digitado — inclusive senha.
            problems.append(Problem(code="appium_log_masking_off",
                                    message="Mascaramento de log do Appium não comprovado nesta sessão.",
                                    hint="Reinicie pelo scripts/stop.ps1 + start.ps1 para o backend subir o Appium com as "
                                         "regras de mascaramento. Preenchimento de credencial fica bloqueado até lá."))
        if self._e._clock_skew_s > self._e.cfg.max_clock_skew_s:
            problems.append(Problem(
                code="clock_skew",
                message=f"O relógio desta máquina está {self._e._clock_skew_s:.1f}s longe do relógio do banco.",
                hint="A posse de etapa entre backends depende deste relógio: um backend adiantado adota etapa em "
                     "plena execução de outro. Sincronize por NTP. Com outro backend hospedando aparelhos neste "
                     "banco, este processo teria recusado subir."))
        ai = self.ai_status()
        if not ai.configured:
            problems.append(Problem(code="ai_not_configured", message="Provedor de IA sem chave.",
                                    hint="Defina ANTHROPIC_API_KEY no .env e reinicie o backend. Gerenciamento e controle manual continuam disponíveis."))
        # Teto de gasto em US$ (item 7.2): o aviso sai em 80 % e o bloqueio em 100 %, com o número na frente —
        # até aqui o custo só existia num relatório que ninguém abre antes de a conta zerar.
        limite_dia = float(getattr(self._e.settings.get(), "ai_max_usd_per_day", 0) or 0)
        if limite_dia > 0 and ai.spend_today_usd is not None:
            gasto = ai.spend_today_usd
            if gasto >= limite_dia:
                problems.append(Problem(
                    code="ai_budget_day", message=f"Teto de gasto de IA do dia atingido: "
                                                  f"US$ {gasto:.2f} de US$ {limite_dia:.2f}.",
                    hint="Nenhuma chamada nova de IA será feita hoje. Aumente ai_max_usd_per_day em "
                         "Configuração › Limites para liberar."))
            elif gasto >= limite_dia * 0.8:
                problems.append(Problem(
                    code="ai_budget_day_warning",
                    message=f"Gasto de IA do dia em US$ {gasto:.2f} de US$ {limite_dia:.2f} "
                            f"({gasto / limite_dia:.0%} do teto).",
                    hint="Em 100 % as chamadas de IA passam a ser recusadas até o dia virar (UTC) ou o teto subir."))
        breaker = self._e.scheduler.executor.ai_breaker
        if breaker is not None:
            code = {"billing": "ai_billing", "balance": "ai_balance_blocked"}.get(breaker.kind, "ai_auth_failed")
            problems.append(Problem(code=code, message=f"{breaker.message} (execução {breaker.run_id}, {breaker.at}).",
                                    hint="Disjuntor de conta de IA acionado: a execução foi pausada automaticamente e "
                                         "nenhuma tentativa foi gasta. Corrija e retome a execução para soltar."))
        problems.extend(self._problemas_de_saldo())
        problems.extend(self._e.avisos.problemas())
        problems.extend(self._e.canais_da_frota.problemas())
        problems.extend(self._e.telegram_entrada.problemas())
        achados_do_espelho = self._e.trello_espelho.problemas()
        problems.extend(achados_do_espelho)
        # A recusa do Trello é uma só para o espelho e o leitor (o mesmo token): não aparece duas vezes.
        ja_ditos = {a.code for a in achados_do_espelho}
        problems.extend(p for p in self._e.trello_leitor.problemas() if p.code not in ja_ditos)
        problems.extend(self._e.trello_webhook.problemas())
        problems.extend(self._e.trello_cadastro.problemas())
        problems.extend(problemas_do_trello(self._e.cfg))
        # Backlog B15 (bateria de 25/09): o Ollama estava fora do ar, as 89 decisões foram para o fallback — e a saúde
        # dizia `ok`. O fallback continua sendo o comportamento certo; o que faltava era ele aparecer.
        for linha in self._ia_em_fallback():
            problems.append(Problem(
                code="ai_fallback_em_uso",
                message=(f"IA em fallback: {linha['n']} chamada(s) de '{linha['role']}' nos últimos 30 min não foram "
                         f"atendidas por {linha['requested_model']} e caíram em {linha['fallback']}."),
                hint="O provedor principal dessa função não respondeu (modelo local fora do ar, por exemplo: o Ollama "
                     "sobe no login do usuário, não no boot). A execução segue pelo fallback declarado, com o custo e "
                     "a qualidade dele. Suba o provedor principal ou declare o fallback como principal em ai.roles."))
        vault = self._e.secrets.status()
        if vault == "locked":
            problems.append(Problem(code="secret_store_locked",
                                    message="O cofre de credenciais está travado nesta máquina/usuário.",
                                    hint="As credenciais cifradas foram preservadas. Recadastre a senha de cada perfil "
                                         "pelo portal para voltar a usar autenticação automática."))
        elif vault == "unavailable":
            problems.append(Problem(code="secret_store_unavailable",
                                    message="Sem chave mestra para proteger credenciais.",
                                    hint="Defina CREDENTIALS_MASTER_KEY no .env (o nome antigo, "
                                         "INSTAGRAM_CREDENTIALS_MASTER_KEY, continua valendo). Gerenciamento e "
                                         "controle manual seguem funcionando."))
        # Achado #126: dizer `ready` era metade da verdade. O cofre abre — mas abre com a chave DESTE backend, e o
        # que está guardado no banco pode ter sido cifrado por outro. Sem este problema, o sintoma era login
        # automático falhando de forma intermitente, sem nada na saúde apontando a causa.
        elif (estranhas := self._e.secrets.chaves_estranhas()):
            problems.append(Problem(
                code="secret_store_foreign_key",
                message=("Há credenciais no banco cifradas com outra chave mestra: "
                         + ", ".join(estranhas) + f" (a deste backend é {self._e.secrets.provider.key_id})."),
                hint="Outro backend gravou credencial neste banco com a chave mestra dele — este aqui não abre "
                     "essas senhas, e recadastrá-las por aqui faria o outro parar de abrir. Use a MESMA "
                     "CREDENTIALS_MASTER_KEY nos dois backends, ou rode `python -m app.security.rekey` para "
                     "recifrar o cofre inteiro para uma chave só."))
        if ai.simulated:
            problems.append(Problem(code="ai_simulated", message="MODO SIMULADO ativo: nenhuma IA é consultada.",
                                    hint="Use AI_PROVIDER=anthropic no .env para o provedor real."))
        diag = self._e._diag_cache
        accel = diag["acceleration"]["detail"] if diag else None
        if diag and not diag["acceleration"]["usable"]:
            problems.append(Problem(code="no_acceleration", message="Aceleração de virtualização indisponível.",
                                    hint="No Windows, habilite 'Windows Hypervisor Platform' (WHPX) e reinicie."))
        # `database_down` é duro: sem banco não há fila, nem posse de etapa, nem histórico — nada do que este
        # processo faz sobrevive, e chamar isso de "degradado" seria o mesmo engano do achado #33.
        hard = {"sdk_missing", "no_acceleration", "database_down"}
        status = "error" if any(p.code in hard for p in problems) else ("degraded" if problems else "ok")
        emu_version = next((t["version"] for t in (diag or {}).get("tools", []) if t["name"] == "Android Emulator"), None)
        return Health(status=status, version=VERSION, commit=commit_em_execucao(self._e.cfg.root),
                      migration=self.ultima_migracao(), database=banco, ai=ai,
                      appium=AppiumStatus(running=appium_up, port=self._e.cfg.file.appium.port, detail=self._e.appium.detail),
                      sdk=SdkStatus(found=sdk_ok, root=str(self._e.cfg.sdk_root), emulator_version=emu_version, accel=accel),
                      problems=problems,
                      features={"hibernation": self._e.cfg.file.android.hibernation, "recipes": self._e.cfg.file.ai.recipes,
                                "flows": self._e.cfg.file.ai.flows, "image_policy": self._e.cfg.file.ai.image_policy,
                                "system_image": self._e.cfg.file.android.system_image,
                                # Fase F: o painel só oferece o ensino v2 e a lista de habilidades com isto ligado.
                                "skills": self._e.cfg.file.skills.enabled,
                                # 31.91 F1: a TELA do ensino v2 (o painel exige `skills` também). Desligada por padrão.
                                "ensino_v2": self._e.cfg.file.skills.ensino_v2_na_tela,
                                # 31.91 T1 (ADR-078): chamadas às rotas OBSOLETAS do ensino v2, desde o início da medição.
                                # É a régua do T2: 14 dias com `total` parado em zero autorizam tirar o código.
                                "ensino_v2_chamadas": ContadorDoEnsinoV2(self._e.db).resumo(),
                                # 30.34: pedidos de prova nascidos do `observar` da classe B (no app de prova), por
                                # estado, desde a ligação. A régua da medida do efeito (leitura de 12/10 do 30.72).
                                "validacao_pelo_observar_b": ContadorPeloObservar(self._e.db).resumo(),
                                # Aparelhos com o reparo automático PAUSADO (experimento/manutenção): `{id: {until, reason, by,
                                # remaining_s}}`; vazio = nenhum. Informativo: não é problema de saúde.
                                "repair_pause": {rt.id: dto.model_dump(mode="json") for rt in self._e.devices.devices.values()
                                                 if (dto := self._e.devices.pausa_dto(rt)) is not None}})

    def _saude_do_banco(self) -> tuple[DatabaseStatus, list[Problem]]:
        """O banco responde? E o esquema dele ainda é o que estes arquivos de migração geram?

        Achado #33: `health()` não fazia nenhuma consulta. Com o PostgreSQL fora do ar — reinício, rede que
        piscou, sessão derrubada — o processo respondia `degraded/ok` alegremente enquanto toda operação falhava,
        e `migration` aparecia como `null` porque `ultima_migracao()` engole exceção. Aqui a pergunta é explícita
        e o silêncio vira `database_down`.

        Achado #169: e, já que a conexão está de pé, é o momento de conferir que nenhuma migração já aplicada foi
        editada no lugar — foi exatamente o que aconteceu com a 008 e ninguém viu por um mês.
        """
        problemas: list[Problem] = []
        alcancavel = self._e.db.alcancavel()
        if not alcancavel:
            problemas.append(Problem(
                code="database_down",
                message=f"O banco ({self._e.db.dialect}) não respondeu.",
                hint="Confira se o serviço do banco está no ar e alcançável desta máquina. A conexão é reaberta "
                     "sozinha na próxima consulta que der certo — não é preciso reiniciar o backend."))
        elif (mudaram := self._e.db.divergencias()):
            problemas.append(Problem(
                code="migration_changed",
                message="Migração já aplicada foi alterada no arquivo: " + ", ".join(sorted(mudaram)) + ".",
                hint="O esquema DESTE banco é o que a versão antiga do arquivo gerava, e um banco novo nasceria "
                     "diferente. Migração aplicada não se edita: crie a próxima migração com a diferença. Se a "
                     "mudança foi só de comentário, o alarme some quando o arquivo voltar ao que era."))
        return DatabaseStatus(dialect=self._e.db.dialect, reachable=alcancavel, target=self._banco_sem_segredo(),
                              open_connections=self._e.db.conexoes_abertas,
                              slow_queries_in_loop=self._e.db.consultas_lentas_no_laco), problemas

    def _banco_sem_segredo(self) -> str:
        """`postgres://host:porta/base` — o DSN sem usuário nem senha. A saúde é lida pelo painel e vai para
        relatório; o endereço ajuda a saber em que banco o processo está, a credencial não pode viajar junto."""
        if self._e.db.dialect != "postgres":
            return "sqlite"
        try:
            partes = urlsplit(self._e.db.dsn)
            return f"postgres://{partes.hostname or '?'}:{partes.port or 5432}{partes.path}"
        except Exception:               # noqa: BLE001 - endereço é enfeite; nunca derruba a saúde
            return "postgres"

    def _problema_de_capacidade_local(self) -> Problem | None:
        """Quanto cabe AGORA (RAM livre) contra o alvo decidido (as vagas do central pela regra única) — mesma
        conta do portão de boot real, para o aviso e a recusa nunca discordarem (achado #146, item 10.3)."""
        try:
            # 29.86 (R2): o alvo é o mesmo número do agendador e do painel (`vagas_que_valem` pelo `capacidade`);
            # sem a linha do central no registro (a janela da subida), o setting vivo.
            cap = self._e.workers.capacidade(self._e.cfg.owner_id)
            alvo = int(cap.max_slots if cap is not None
                       else getattr(self._e.settings.get(), "max_online_devices", 0) or 0)
            if alvo <= 0:
                return None
            a = self._e.cfg.file.android
            est_mb = a.est_ram_host_mb()
            online = sum(1 for d in self._e.devices.devices.values() if d.state == InstanceState.online)
            # Arredondado ANTES de decidir: RAM livre "verdadeira" oscila alguns MB de uma leitura para a outra
            # só por causa de cache de página do SO, e o achado #65 já corrigiu `/health` para só emitir quando
            # o resultado muda de verdade — um número bruto aqui faria a mesma checagem "mudar" a cada 30 s sem
            # nada de fato ter mudado. 100 MB é grosso o bastante para nunca balançar sozinho.
            free_mb = round(psutil.virtual_memory().available / 2**20 / 100) * 100
            fit_more = max(0, int((free_mb - a.min_free_ram_mb_after_boot) // est_mb))
            estimated_max = online + fit_more
            if estimated_max >= alvo:
                return None
            return Problem(
                code="capacity_local",
                message=f"RAM livre agora só sustenta ≈{estimated_max} aparelho(s) local(is) simultâneo(s), "
                        f"abaixo do alvo configurado ({alvo}, vagas do central pela regra única): ≈{free_mb:.0f} MB "
                        f"livres, ≈{est_mb} MB por instância, {a.min_free_ram_mb_after_boot} MB de folga exigida.",
                hint="Outro processo está usando a RAM do host (confira o WSL — `.wslconfig` — e outros "
                     "contêineres/VMs) ou o alvo local está otimista para esta máquina. O rodízio vai recusar "
                     "boot antes de estourar; isto só antecipa o aviso.")
        except Exception:  # noqa: BLE001 - aviso de capacidade nunca pode derrubar a saúde
            log.exception("cálculo de capacidade local")
            return None

    def _ia_em_fallback(self, janela_min: int = 30) -> list[dict[str, object]]:
        """Chamadas recentes que o provedor principal da função não atendeu (`ai_calls.fallback`), por função."""
        desde = to_iso(now() - timedelta(minutes=janela_min))
        try:
            return self._e.db.query(
                "SELECT role, requested_model, fallback, count(*) AS n FROM ai_calls "
                "WHERE ts >= ? AND fallback IS NOT NULL AND fallback <> '' "
                "GROUP BY role, requested_model, fallback ORDER BY n DESC", (desde,))
        except Exception:  # noqa: BLE001 - a saúde nunca cai por causa de um relatório
            log.exception("não foi possível ler os fallbacks recentes de IA")
            return []

    def _problema_de_exposicao_publica(self) -> Problem | None:
        """29.54 / ADR-073: com um nome em `server.public_hosts`, a exposição só está de pé inteira com TRÊS coisas —
        `API_TOKEN` (sem ele ninguém entra pelo endereço público), TLS declarado (`tls_behind_proxy`: o cookie de
        sessão só ganha `Secure` assim) e a origem `https://<host>` em `allowed_origins` (sem ela o POST do login
        leva 403 `forbidden_origin`). Faltando qualquer uma o portão continua fechando, mas o dono veria um login
        que não entra sem saber por quê. Só nomes de configuração e o fato de faltar; nenhum valor de segredo."""
        server = self._e.cfg.file.server
        publicos = sorted(publicos_de(self._e.cfg))
        if not publicos:
            return None
        origens = {o.strip().lower().rstrip("/") for o in server.allowed_origins}
        faltas: list[str] = []
        if not self._e.cfg.api_token:
            faltas.append("API_TOKEN no .env")
        if not self._e.cfg.tls_ativo:
            faltas.append("server.tls_behind_proxy: true")
        sem_origem = [h for h in publicos if f"https://{h}" not in origens]
        if sem_origem:
            faltas.append("server.allowed_origins com " + ", ".join(f"https://{h}" for h in sem_origem))
        if not faltas:
            return None
        return Problem(
            code="exposicao_publica_incompleta",
            message=("Há host público declarado (server.public_hosts: " + ", ".join(publicos) + ") e falta: "
                     + "; ".join(faltas) + "."),
            hint="Complete em config/config.yaml e .env e reinicie o central; sem isso o login pelo endereço público "
                 "não entra. Para recuar, tire o nome de server.public_hosts (tudo de fora volta a 403). "
                 "Procedimento em docs/operacao.md, \"Portal público pelo túnel da Cloudflare\".")

    def _problemas_de_saldo(self) -> list[Problem]:
        """Só conta EM USO vira problema: uma conta sem função nem imagem apontada para ela não para nada."""
        try:
            contas = self._e.saldos_de_ia()
        except Exception:  # noqa: BLE001 - a saúde nunca cai por causa do relatório de saldo
            log.exception("não foi possível calcular os saldos de IA")
            return []
        out: list[Problem] = []
        for c in contas:
            if not c.em_uso:
                continue
            usa = ", ".join(c.roles + (["imagem"] if c.image else []))
            if c.state in ("blocked", "exhausted"):
                out.append(Problem(code="ai_balance_blocked", message=f"{c.label}: {c.message}",
                                   hint=f"Usada por: {usa}. Recarregue no console ({c.console}) e registre a recarga "
                                        "em Configuração › IA (ou POST /api/ai/balances/<conta>/recharge)."))
            elif c.state == "low":
                out.append(Problem(code="ai_balance_low", message=f"{c.label}: {c.message}",
                                   hint=f"Usada por: {usa}. Recarregue antes de chegar ao limite de bloqueio."))
            elif c.state == "unknown":
                out.append(Problem(code="ai_balance_unknown", message=f"{c.label}: sem leitura de saldo registrada.",
                                   hint=f"Usada por: {usa}. Registre o saldo do console em Configuração › IA."))
            elif c.stale:
                out.append(Problem(code="ai_balance_stale", message=f"{c.label}: {c.message}",
                                   hint="O relatório oficial de uso do provedor não respondeu nos últimos "
                                        f"{saldos.CONCILIACAO_VELHA_MIN} min: o gasto de fora da plataforma não está "
                                        "entrando. Confira a chave de administrador no .env."))
        return out

    def ai_status(self) -> AiStatus:
        """`provider.status()` só sabe da chave; o disjuntor de conta (crédito/credencial recusados em tempo de
        execução) vive no executor — combina os dois para health(), /api/ai e a aba IA lerem uma fonte só."""
        status = self._e.provider.status()
        breaker = self._e.scheduler.executor.ai_breaker
        if breaker is not None:
            status = status.model_copy(update={"account_blocked": True, "account_blocked_reason": breaker.message})
        try:
            contas = [c.as_dict() for c in self._e.saldos_de_ia()]
        except Exception:  # noqa: BLE001 - o status da IA nunca cai por causa do relatório de saldo
            log.exception("não foi possível calcular os saldos de IA")
            contas = []
        # O gerador de imagem não é papel do hub: entra aqui, ao lado, para a aba IA dizer quem é e se está pronto.
        # Transparência do Jev (ADR-069 item 8): só a PRESENÇA da chave entra; o valor nunca é lido para este fim.
        jev = self._e.cfg.file.ai.decisao_fechada
        chave = self._e.cfg.env.typesafe_api_key is not None
        aviso_jev = transparencia.aviso(jev, chave_configurada=chave)
        extra: dict[str, object] = {"image": status_de_imagem(self._e.persona_images, self._e.cfg), "balances": contas}
        # Adendo v0.87 (I2 da validação do deploy 7): o formato do plano e os perfis vêm da configuração, não do provedor,
        # para valerem também no modo simulado. O perfil que some da configuração não derruba a aba.
        extra["esquema_do_plano"] = self._e.cfg.file.ai.esquema_do_plano
        extra["leitura_visual"] = self._e.cfg.file.ai.leitura_visual.enabled
        # 31.223: a política do modelo forte em vigor (só leitura; muda no config.yaml e vale na subida)
        forte = self._e.cfg.file.ai.strong_model_for_side_effect
        extra["strong_model_for_side_effect"] = forte if isinstance(forte, str) else str(forte).lower()
        extra["strong_model_only_on_commit"] = self._e.cfg.file.ai.strong_model_only_on_commit
        try:
            extra["profiles"] = perfis_para_o_painel(self._e.cfg)
        except Exception:  # noqa: BLE001 - o status da IA nunca cai por causa da lista de perfis
            log.exception("não foi possível montar os perfis de IA para o painel")
        if aviso_jev is not None:
            extra["notice"] = f"{status.notice} {aviso_jev}"
            extra["decisao_fechada"] = transparencia.status(jev, chave_configurada=chave)
        return status.model_copy(update=extra)

    def ultima_migracao(self) -> str | None:
        """A migração mais recente aplicada NESTE banco. Lido a cada chamada: é uma linha e responde "o esquema que
        este processo está usando é o que o código espera?" — a pergunta do deploy, e a única prova de que a
        subida migrou de verdade."""
        try:
            return self._e.db.scalar("SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1")
        except Exception:  # noqa: BLE001 - saúde nunca falha por causa de um enfeite dela
            return None

    def checar(self) -> None:
        """Recalcula `health()` e emite `health.updated` só quando o resultado mudou desde a última checagem
        (achado #65). Método separado do laço para ser testável sem `asyncio.sleep`."""
        h = self.health()
        dump = h.model_dump(mode="json")
        if dump != self._last_health:
            self._last_health = dump
            self._e.bus.emit("health.updated", f"ambiente: {h.status}", level="warn" if h.status != "ok" else "info",
                          data={"health": dump})
