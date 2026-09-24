"""Configuração central: config/config.yaml (estrutura) + .env (segredos e escolhas de ambiente)."""
from __future__ import annotations

import os
import re
import socket
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Esforço aceito pela API. Fora desta lista é erro de configuração, não um 400 a ser "aprendido".
Effort = Literal["low", "medium", "high", "xhigh", "max"]

#: As cinco funções de IA. A ordem é a que o painel mostra.
AI_ROLES = ("plan", "decide", "verify", "escalation", "social")


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    ai_provider: str = Field(default="anthropic", alias="AI_PROVIDER")
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    ai_model: str = Field(default="claude-opus-5", alias="AI_MODEL")
    # Modelo por função (vazio = AI_MODEL). O ator/verificador fazem ~90 % das chamadas: é onde o modelo barato paga.
    ai_model_planner: str | None = Field(default=None, alias="AI_MODEL_PLANNER")
    ai_model_actor: str | None = Field(default=None, alias="AI_MODEL_ACTOR")
    ai_model_verifier: str | None = Field(default=None, alias="AI_MODEL_VERIFIER")
    ai_model_escalation: str | None = Field(default=None, alias="AI_MODEL_ESCALATION")   # vazio = modelo do planejador
    ai_model_social: str | None = Field(default=None, alias="AI_MODEL_SOCIAL")          # geração social; vazio = planejador
    # Esforço por função. `Literal` de propósito (achado #97): era `str` livre, e um valor digitado errado só
    # aparecia como 400 do provedor — que o `_learn` interpretava como "este modelo não aceita esforço" e passava
    # a rodar no padrão mais caro com um warning. Agora erra na partida, com o nome do campo.
    ai_effort_planner: Effort = Field(default="medium", alias="AI_EFFORT_PLANNER")
    ai_effort_actor: Effort = Field(default="low", alias="AI_EFFORT_ACTOR")
    ai_effort_verifier: Effort | None = Field(default=None, alias="AI_EFFORT_VERIFIER")  # vazio = esforço do ator
    ai_refusal_fallback: bool = Field(default=True, alias="AI_REFUSAL_FALLBACK")
    # Alternativa portátil ao DPAPI: chave mestra em base64 (32 bytes). Vazia = DPAPI no Windows.
    #
    # O nome perdeu o prefixo `INSTAGRAM_` (achado #88): o cofre é genérico — ele guarda credencial de qualquer
    # app, e a loja pode vir a guardar nele texto sensível da conta Google. Amarrar a chave mestra ao nome de UM
    # aplicativo convidava a criar uma chave por app, que é exatamente o que este cofre existe para não ter.
    # O nome antigo continua valendo como alias legado: quem já o tem no `.env` não precisa mexer em nada.
    credentials_master_key: str | None = Field(
        default=None, validation_alias=AliasChoices("CREDENTIALS_MASTER_KEY", "INSTAGRAM_CREDENTIALS_MASTER_KEY"))
    android_sdk_root: str | None = Field(default=None, alias="ANDROID_SDK_ROOT")
    poc_config: str | None = Field(default=None, alias="POC_CONFIG")
    poc_db_path: str | None = Field(default=None, alias="POC_DB_PATH")
    # Endereço do banco. Vazio = SQLite no arquivo local, que continua sendo o certo para quem roda tudo numa
    # máquina. `postgresql://usuario:senha@host:5432/base` passa a valer quando os componentes se separam.
    # É o PRIMEIRO endereço de serviço configurável do projeto além do Appium — até aqui não havia nenhum.
    database_url: str | None = Field(default=None, alias="DATABASE_URL")
    # Quem é o dono das etapas que este backend executa. Vazio = hostname. Só precisa ser mexido para rodar DOIS
    # backends na MESMA máquina; entre máquinas o hostname já distingue.
    owner_id: str | None = Field(default=None, alias="OWNER_ID")
    # Que PAPEL este processo cumpre. `all` (o padrão) é o backend inteiro, como sempre foi — quem roda numa
    # máquina só nunca precisa mexer nisto. `api` sobe SÓ a API: nada de Appium, DeviceManager, worker local,
    # scheduler ou reconciliação de partida — é a réplica que atende o painel sem disputar aparelho nenhum.
    # `scheduler` é o contrário: despacha e hospeda aparelhos, e não publica a API REST (o canal do worker
    # continua, porque é por ele que os aparelhos desta máquina chegam).
    role: Literal["api", "scheduler", "all"] = Field(default="all", alias="ROLE")
    # Desvio máximo tolerado entre o relógio DESTA máquina e o do banco, em segundos. Acima disto o backend recusa
    # subir quando há OUTRO hospedeiro no mesmo banco: um relógio adiantado adota etapas em plena execução alheia.
    max_clock_skew_s: float = Field(default=30.0, alias="MAX_CLOCK_SKEW_S")
    # Segredo que autoriza chamadas à API vindas de FORA do loopback. Sem ele o backend recusa subir em endereço
    # público — ligar a porta para a rede sem autenticação nenhuma seria entregar o parque a quem estiver nela.
    api_token: SecretStr | None = Field(default=None, alias="API_TOKEN")
    # Por onde a ordem do outbox SAI (item 5.6). `websocket` é o que roda hoje: a entrega acontece dentro deste
    # processo, no mesmo canal de sempre. `nats` publica num assunto por worker em NATS JetStream, para a réplica
    # que hospeda o aparelho consumir — é a bandeira, e ela fica desligada até um comando real atravessar um
    # broker de verdade. Nenhum dos dois é exatamente-uma-vez: ver docs/parque-distribuido.md.
    command_transport: Literal["websocket", "nats"] = Field(default="websocket", alias="COMMAND_TRANSPORT")
    nats_url: str | None = Field(default=None, alias="NATS_URL")     # ex.: nats://127.0.0.1:4222
    # Onde as evidências e os avatares são gravados (item 5.7). `disk` = pasta local, como sempre foi, e é o
    # certo para quem roda tudo numa máquina. `s3` = bucket S3-compatível (MinIO inclusive), que é o que faz a
    # evidência gravada por uma réplica ser lida pela outra em vez de virar 404.
    evidence_storage: Literal["disk", "s3"] = Field(default="disk", alias="EVIDENCE_STORAGE")
    s3_endpoint_url: str | None = Field(default=None, alias="S3_ENDPOINT_URL")   # vazio = AWS
    s3_bucket: str | None = Field(default=None, alias="S3_BUCKET")
    s3_region: str | None = Field(default=None, alias="S3_REGION")
    s3_access_key_id: SecretStr | None = Field(default=None, alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: SecretStr | None = Field(default=None, alias="S3_SECRET_ACCESS_KEY")


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    #: Porta do listener DEDICADO ao canal do worker (só `/api/worker/ws`, nada de REST). Sempre em 127.0.0.1,
    #: independente de `host`: ela existe para ser o alvo do `-R` do túnel SSH.
    #:
    #: Por que uma porta a mais, depois de `docs/worker.md` ter prometido "zero porta nova em qualquer lugar":
    #: toda conexão que chega pelo túnel tem par `127.0.0.1` de verdade, então a isenção de loopback a isentava —
    #: e qualquer processo, job de CI ou usuário local da máquina do worker alcançava a API INTEIRA do central sem
    #: credencial (medido: `GET http://127.0.0.1:18000/api/workers` → 200). Olhar o par não separa os dois casos,
    #: porque os dois são 127.0.0.1. O que separa é qual porta atendeu: nesta só existe o WebSocket do worker, que
    #: autentica na primeira mensagem. `0` desliga o listener (parque numa máquina só, sem worker remoto).
    worker_port: int = 8010
    # Nomes/IPs pelos quais a API pode ser chamada de fora. Fica vazio por omissão de propósito: a lista é o que
    # sustenta a defesa contra DNS rebinding (um nome que resolve para 127.0.0.1 não passa se não estiver aqui).
    public_hosts: list[str] = []
    allowed_origins: list[str] = ["http://127.0.0.1:8000", "http://127.0.0.1:5173"]
    #: Certificado e chave servidos DIRETO pelo uvicorn (`https://` e `wss://` sem proxy na frente). Caminhos de
    #: arquivo; os dois juntos ou nenhum. Existem porque a opção "porta de rede" — a única disponível para um
    #: worker atrás de NAT que o central não alcança — entregava o `API_TOKEN` a cada requisição, a credencial do
    #: worker a cada conexão e todo screenshot em claro para quem estivesse escutando a rede.
    tls_cert: str | None = None
    tls_key: str | None = None
    #: "Tem um proxy TLS (Caddy, nginx, IIS) na frente deste processo, e é ELE quem termina o HTTPS." Declaração
    #: explícita, e não adivinhação: com `proxy_headers=False` (deliberado, veja `main.main`) o uvicorn vê sempre
    #: `http`, então o processo não tem como descobrir isto sozinho — e adivinhar errado põe `Secure` num cookie
    #: que nunca chegaria, ou deixa de pô-lo onde deveria.
    tls_behind_proxy: bool = False


class PathsCfg(BaseModel):
    data_dir: str = "data"
    avd_home: str = "data/avd"
    evidence_dir: str = "data/evidence"
    logs_dir: str = "data/logs"
    apk_dirs: list[str] = ["qa-app/dist", "apks"]
    apk_inbox: str = "apks/inbox"        # onde o usuário larga o APK ou o conjunto de splits
    apk_catalog: str = "apks"            # raiz do catálogo imutável: <catálogo>/<pacote>/<versionCode>-<hash>/


class AndroidCfg(BaseModel):
    sdk_root: str = r"C:\Android\Sdk"
    system_image: str = "system-images;android-34;google_apis;x86_64"
    #: `None` = pelo perfil da IMAGEM (`devices/perfis.py`, medido). Um número aqui é decisão do dono e vale
    #: como está — inclusive as `extra_emulator_args`, que então não ganham nada do perfil.
    ram_mb: int | None = None
    cores: int = 2
    width: int = 720
    height: int = 1280
    density: int = 320
    data_partition: str = "4G"
    gpu_mode: str = "swiftshader_indirect"
    boot_timeout_s: int = 480
    est_instance_ram_mb: int | None = None      # RAM real por instância no host; None = ram_mb + 1100 (medido)
    min_free_ram_mb_after_boot: int = 1500      # folga que o host deve manter depois de cada boot
    extra_emulator_args: list[str] = []
    hibernation: bool = False                   # rodízio desliga salvando snapshot; acordar leva segundos (medido: ~7 s)
    wake_timeout_s: int = 90                    # acordar que não chega à interface nesse tempo → descarta snapshot, boot a frio
    # Sobe o emulador COM janela. Existe para o aparelho-loja: a conta Google é digitada direto na janela do
    # emulador, e assim nenhuma tecla passa pelo backend, pelo Appium ou pelo adb. O parque segue sem janela.
    window: bool = False

    # ------------------------------------------------------------------ o que vale de fato
    # As três perguntas que o resto do código faz — quanto de RAM dar ao AVD, com que flags subir, quanto o host
    # vai pagar — respondidas num lugar só. Antes cada chamador lia `ram_mb` cru, e o perfil da imagem não
    # existia: o android-12 subiu com 1536 MB numa imagem que precisa de 2048 e entrou em thrash pós-boot.
    def perfil(self) -> Any:
        from .devices.perfis import perfil_por_imagem  # noqa: PLC0415 - evita ciclo config → devices → config
        return perfil_por_imagem(self.system_image)

    def ram_efetiva(self) -> int:
        return int(self.ram_mb) if self.ram_mb else int(self.perfil().ram_mb)

    def args_extras_efetivos(self) -> list[str]:
        """Com `ram_mb` explícito, as flags são só as declaradas (decisão do dono). Pelo perfil, as flags do perfil
        entram ANTES das declaradas, sem repetir."""
        if self.ram_mb:
            return list(self.extra_emulator_args)
        do_perfil = [f for f in self.perfil().extra_args if f not in self.extra_emulator_args]
        return [*do_perfil, *self.extra_emulator_args]

    def est_ram_host_mb(self) -> int:
        """Custo real no host: o declarado; senão o medido do perfil; senão a regra antiga `ram_mb + 1100`."""
        if self.est_instance_ram_mb:
            return int(self.est_instance_ram_mb)
        if not self.ram_mb:
            return int(self.perfil().est_real_mb)
        return int(self.ram_mb) + 1100


class InstancesCfg(BaseModel):
    count: int = 10
    id_prefix: str = "android-"
    base_console_port: int = 5554
    base_system_port: int = 8200
    base_mjpeg_port: int = 9200
    base_chromedriver_port: int = 9515
    default_app: str | None = "qa-messenger"
    accounts: dict[str, str] = {}
    overrides: dict[str, dict[str, Any]] = {}
    # Aparelho ADB externo no lugar do emulador desta instância: celular físico (serial USB) ou `host:porta`
    # (adb connect). O projeto NÃO liga, desliga, reseta nem hiberna um aparelho externo — só o usa.
    external: dict[str, str] = {}
    # Aparelho-loja: o inverso do externo. O projeto GERE o ciclo de vida dele (liga, desliga), mas NUNCA lhe
    # despacha tarefa. É o emulador com Play Store onde o usuário instala o app pela loja oficial; o backend copia
    # o pacote dali e o distribui ao parque. Id de uma instância existente (ex.: "android-11"); vazio = sem loja.
    store: str | None = None


class AppiumCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4723
    dir: str = "tools/appium"
    autostart: bool = True
    new_command_timeout_s: int = 0
    server_launch_timeout_ms: int = 180000
    adb_exec_timeout_ms: int = 60000


class LimitsCfg(BaseModel):
    """Espelha o tipo `Settings` do contrato. Editável em tempo de execução (persistido no SQLite)."""

    # Teto de aparelhos que este servidor dirige ao mesmo tempo. O 10 era do CÓDIGO, não da configuração: com
    # 14 aparelhos de tarefa e um segundo worker, ele passava a apertar sozinho. Quem limita de verdade é a
    # vaga de cada máquina (`max_online_devices` aqui, `max_slots` de cada worker).
    max_active_devices: int = Field(10, ge=1, le=64)
    max_ai_concurrency: int = Field(4, ge=1, le=16)
    boot_parallelism: int = Field(2, ge=1, le=10)
    max_steps_per_objective: int = Field(12, ge=1, le=40)
    for_each_max_items: int = Field(25, ge=1, le=200)    # teto de itens de uma coleta (nunca trunca: acima disso, bloqueia)
    max_actions_per_step: int = Field(12, ge=1, le=60)
    max_attempts_per_step: int = Field(3, ge=1, le=10)
    step_timeout_s: int = Field(180, ge=10, le=3600)
    objective_timeout_s: int = Field(900, ge=30, le=14400)
    driver_call_timeout_s: int = Field(45, ge=5, le=600)
    retry_backoff_s: int = Field(5, ge=0, le=600)
    # Achado #164: as esperas do laço de despacho e das retentativas eram números soltos no código, então a suíte
    # pagava em tempo REAL assentamentos pensados para um emulador de verdade. Aqui elas viram configuração com o
    # valor de hoje como padrão — produção não muda, e `make_config` as encurta como já fazia com `retry_backoff_s`.
    # `scheduler_tick_s` NUNCA pode ser 0: `asyncio.wait_for(..., timeout=0)` estoura na hora e o laço vira espera
    # ocupada, prendendo o event loop. Por isso `ge=0.01`.
    scheduler_tick_s: float = Field(1.0, ge=0.01, le=60)
    ai_retry_wait_s: float = Field(2.0, ge=0, le=60)      # base do recuo entre tentativas de chamada à IA (×tentativa)
    session_retry_wait_s: float = Field(8.0, ge=0, le=120)  # entre tentativas de abrir a sessão de automação
    no_progress_limit: int = Field(4, ge=2, le=20)
    # Teto de reobservações automáticas seguidas quando a sessão fica `unknown` (achado #104): uma tela que
    # `classify()` não reconhece (sinal ausente da tabela, onboarding fora do mapa) não pode reabrir o app e
    # reobservar a cada tick para sempre. Ao alcançar o teto, a porta do despacho trata como "só uma pessoa
    # resolve" — mesmo caminho de auth_challenge/wrong_account — em vez de insistir sozinha.
    session_unknown_retry_cap: int = Field(3, ge=1, le=20)
    # Coordenação de frota sobre o mesmo alvo (achado #114). Fica em LimitsCfg, não no `automation_policy` de
    # cada perfil: é regra da OPERAÇÃO como um todo — um perfil não pode afrouxar sozinho o que protege a conta
    # dos outros 7. `fleet_max_accounts_per_target`: quantos perfis DIFERENTES podem mexer com o mesmo alvo
    # dentro da janela antes de bloquear o próximo. `fleet_min_spacing_between_accounts_s` +
    # `fleet_spacing_jitter_s`: intervalo mínimo (mais aleatoriedade, para não virar um padrão regular por si
    # só) entre a ação de uma conta e a de outra sobre o MESMO alvo, mesmo abaixo do teto de contas.
    fleet_max_accounts_per_target: int = Field(3, ge=1, le=50)
    fleet_target_window_s: int = Field(3600, ge=60, le=86400)
    fleet_min_spacing_between_accounts_s: int = Field(120, ge=0, le=3600)
    fleet_spacing_jitter_s: int = Field(180, ge=0, le=3600)
    ai_max_calls_per_objective: int = Field(60, ge=1, le=1000)
    ai_max_tokens_per_run: int = Field(3_000_000, ge=1000)
    # Teto em DINHEIRO (achado #95). Os dois de cima estão em unidades que não se traduzem em US$ — e o de tokens
    # conta cache lido como token cheio. Estes somam `ai_calls × ai.prices`, que é a mesma conta de /api/usage.
    # Em 80 % vira aviso (evento + Problem em /api/health); em 100 % a chamada é recusada com kind='budget'.
    # `0` desliga. O VALOR do teto diário é decisão do dono: sai de fábrica desligado.
    ai_max_usd_per_run: float = Field(15.0, ge=0, le=10_000)
    ai_max_usd_per_day: float = Field(0.0, ge=0, le=100_000)
    capture_grid_interval_s: float = Field(5, ge=1, le=120)
    capture_focus_interval_s: float = Field(1, ge=0.3, le=30)
    frame_max_age_ms: int = Field(6000, ge=500, le=120000)
    log_retention_days: int = Field(14, ge=1, le=365)
    evidence_retention_days: int = Field(14, ge=1, le=365)
    # Rodízio: N contas sobre K vagas de RAM. O scheduler liga o aparelho quando há tarefa para ele e desliga um
    # ocioso quando falta vaga; `idle_stop_s` > 0 também desliga por ociosidade mesmo sem disputa.
    auto_start_devices: bool = False
    max_online_devices: int = Field(10, ge=1, le=64)   # vagas DESTE host; cada worker traz as dele (`max_slots`)
    min_online_dwell_s: int = Field(60, ge=0, le=3600)      # anti-vaivém: tempo mínimo ligado antes de ceder a vaga
    idle_stop_s: int = Field(0, ge=0, le=86400)             # 0 = só desliga para ceder vaga


class ModelCaps(BaseModel):
    """O que ESTE modelo aceita, **declarado** (achado #97).

    Antes isto era aprendido por erro 400 (`_learn`) e esquecido a cada reinício: cada arranque pagava uma
    requisição rejeitada por modelo antes da primeira decisão. Declarado, a checagem acontece ANTES de agendar —
    que é a única forma de recusar um modelo local sem visão em vez de descobrir isso no meio de uma execução.
    """

    vision: bool = True
    tools: bool = True
    # `false` para opus-5/sonnet-5 com o conjunto atual de 14 ferramentas: a API responde "Schema is too complex"
    # (medido: 6 passam, 8 não). Toda chamada continua revalidada por Pydantic em `validate_call`.
    strict_tools: bool = False
    structured_output: Literal["json_schema", "json_object", "none"] = "json_schema"
    thinking: bool = True
    effort: bool = True
    max_output: int | None = None               # teto de saída do modelo; None = o que o chamador pedir
    # Prefixo cacheável mínimo do modelo, INFORMATIVO (aba IA e relatório): o provedor pede o ponto de cache sempre,
    # porque a API ignora sem erro o que fica abaixo do mínimo — e a conta local subcontava (achado de 24/09).
    min_cache_tokens: int = 0
    #: Descrição curta para a aba IA ("modelo local, 8 GB de VRAM"). Só texto.
    note: str = ""


class ProviderCfg(BaseModel):
    """Um endpoint de IA. `kind=openai` é qualquer servidor compatível com /v1/chat/completions (vLLM inclusive)."""

    kind: Literal["anthropic", "openai", "simulated"] = "anthropic"
    base_url: str | None = None                 # ex.: http://127.0.0.1:8001/v1
    #: NOME da variável de ambiente que guarda a chave — nunca a chave. O YAML fica sem segredo nenhum, e um
    #: endpoint local costuma não precisar de chave (vLLM aceita qualquer valor).
    api_key_env: str | None = None
    #: Os dados SAEM desta máquina? É o que a aba IA precisa responder por função. `False` para um vLLM local.
    sends_data_externally: bool = True
    #: Modelo a usar quando ESTE provedor é o destino de um `fallback_provider`. Vazio = o modelo do `.env` para
    #: aquela função. O modelo do endpoint local quase nunca existe no provedor pago: herdá-lo daria 404.
    fallback_model: str | None = None


class RoleCfg(BaseModel):
    """Configuração de UMA função. Tudo `None` = herda o que o `.env` já dizia (comportamento de hoje, intacto)."""

    provider: str | None = None                 # chave de `ai.providers`; vazio = o provedor do .env
    model: str | None = None                    # vazio = AI_MODEL_<PAPEL> e depois AI_MODEL
    #: Para onde cair quando o provedor DESTA função falha. Vazio = não cai em lugar nenhum — é o que cumpre
    #: "sem fallback pago silencioso": a queda do endpoint local só chega ao provedor pago se estiver escrito aqui.
    fallback_provider: str | None = None
    #: Fallback de RECUSA do lado do servidor (Anthropic `fallbacks: "default"`). Vazio = AI_REFUSAL_FALLBACK.
    refusal_fallback: bool | None = None
    timeout_s: float | None = None
    max_retries: int | None = None              # novas tentativas DENTRO do SDK; 0 = só o `_ai` repete
    concurrency: int | None = None              # vagas simultâneas desta função, sob o limite global


#: Prazo e vagas por função quando o YAML não diz. Folgados diante do medido em 908 chamadas reais
#: (máx.: plan 29,5 s · social 13,4 s · decide 12,2 s · verify 7,6 s) e MUITO abaixo dos 180 s globais de antes.
ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "plan": {"timeout_s": 120.0, "concurrency": 4},
    "decide": {"timeout_s": 45.0, "concurrency": 8},
    "verify": {"timeout_s": 30.0, "concurrency": 8},
    "escalation": {"timeout_s": 60.0, "concurrency": 4},
    "social": {"timeout_s": 60.0, "concurrency": 4},
}


class AiCfg(BaseModel):
    screenshot_max_side: int = 1280             # lado maior da imagem enviada ao modelo (tokens ∝ área)
    max_hierarchy_elements: int = 140           # linhas da hierarquia no prompt (priorizadas; a árvore completa fica local)
    image_policy: Literal["always", "auto", "never"] = "always"
    # auto: só hierarquia quando a árvore é rica; imagem na 1ª decisão de etapa julgada por visão, em árvore pobre,
    # após erro/ciclo, ou quando o modelo pede (observe_screen.need_image)
    rich_tree_min_elements: int = 8
    # Quando a etapa com efeito externo decide no modelo de escalonamento: `true` = sempre (era o único modo: em
    # 19-23/09, 39 % das decisões foram ao Opus, inclusive curtir com seletor de commit declarado); `false` = nunca
    # por efeito; `by_risk` = só risco alto do catálogo, risco médio SEM seletor de commit, ou app sem catálogo.
    # Retentativa, receita divergida, erros seguidos e ciclo continuam escalando em qualquer modo.
    strong_model_for_side_effect: bool | Literal["by_risk"] = "by_risk"
    verify_max_model_calls: int = Field(2, ge=1, le=5)
    # Depois de um "sim" numa etapa com efeito já disparado, quanto esperar antes de RECONFERIR a tela em busca
    # de marca de falha. Existe porque app de mensagem tem UI otimista: o balão aparece e o campo limpa antes de
    # o servidor confirmar, e a falha só chega depois.
    effect_settle_s: float = Field(4.0, ge=0, le=30)
    # Achado #164, as outras esperas do executor, com o valor de hoje como padrão:
    # `action_settle_s` deixa a interface assentar entre uma ação e a próxima observação;
    # `judge_wait_s` espera o app sair de "enviando" antes de julgar (e entre duas sondagens do verificador) —
    # é ESPERA DE COMPORTAMENTO, não enfeite: encurtá-la demais faz pagar dois julgamentos em vez de um;
    # `recipe_settle_s` é o assentamento entre tentativas de conferir a pós-condição na reprodução de receita.
    action_settle_s: float = Field(0.6, ge=0, le=10)
    judge_wait_s: float = Field(1.5, ge=0, le=30)
    recipe_settle_s: float = Field(1.0, ge=0, le=10)
    # Receitas: off = só IA · shadow = aprende e compara com a IA, sem agir · replay = repete sem IA, IA só se divergir
    recipes: Literal["off", "shadow", "replay"] = "off"
    flows: bool = False                          # reaproveita o plano de comandos repetidos (sem chamar o planejador)
    pathfinder_wait_s: int = Field(0, ge=0, le=3600)   # >0: numa execução sem receita, 1 aparelho aprende e os demais esperam
    # US$ por milhão de tokens [entrada, leitura de cache, gravação de cache, saída] — platform.claude.com/docs/en/about-claude/pricing
    # `[0,0,0,0]` é um preço DECLARADO de zero (modelo local). Modelo SEM entrada aqui é tratado pelo preço mais
    # caro da tabela (`planning/costs.py`), nunca como zero — senão um destino de fallback sairia de graça no teto.
    prices: dict[str, list[float]] = {
        # Opus 5.5 (24/09/2026): entrada $4, cache lido $0,20 (0,05× — não os 0,1× dos demais), gravação $5, saída $20.
        "claude-opus-5-5": [4.0, 0.2, 5.0, 20.0],
        "claude-opus-5": [5.0, 0.5, 6.25, 25.0],
        "claude-sonnet-5": [2.0, 0.2, 2.5, 10.0],
        "claude-haiku-4-5": [1.0, 0.1, 1.25, 5.0],
        # Destino documentado do fallback de recusa (achado #92): custava o mesmo do Opus 5 e não estava cadastrado,
        # então toda chamada que caísse nele virava "Total parcial" no painel de uso.
        "claude-opus-4-8": [5.0, 0.5, 6.25, 25.0],
    }
    #: Capacidade DECLARADA por modelo. Chave por família (o sufixo de data é ignorado no casamento).
    #: `min_cache_tokens` (achado #100): prefixo cacheável mínimo de CADA modelo — não é monótono entre gerações
    #: (platform.claude.com/docs, "prompt caching"). É informação, não porta: o prefixo do ator (tools + system,
    #: ≈ 6 mil tokens medidos) supera o mínimo de todos; o do verificador (≈ 1 mil) só cacheia no Opus. O provedor
    #: pede o ponto de cache sempre e a API decide — a conta local que fazia de porta subcontava e deixou o Sonnet
    #: sem cache (24/09).
    models: dict[str, ModelCaps] = {
        "claude-opus-5-5": ModelCaps(min_cache_tokens=512),
        "claude-opus-5": ModelCaps(min_cache_tokens=512),
        "claude-sonnet-5": ModelCaps(min_cache_tokens=1024),
        "claude-opus-4-8": ModelCaps(min_cache_tokens=1024),
        "claude-haiku-4-5": ModelCaps(thinking=False, effort=False, min_cache_tokens=4096),
    }
    #: Endpoints disponíveis. Vazio = só o provedor do `.env`, como sempre foi.
    providers: dict[str, ProviderCfg] = {}
    #: Provedor/modelo/prazo por FUNÇÃO. Vazio = tudo herdado do `.env` (nada muda).
    roles: dict[str, RoleCfg] = {}


class InstagramCfg(BaseModel):
    """Automação do Instagram. Os limites existem para não bloquear a própria conta."""

    package: str = "com.instagram.android"
    max_auth_attempts: int = Field(3, ge=1, le=10)      # teto por perfil antes de exigir intervenção
    auth_cooldown_s: int = Field(300, ge=0, le=86400)   # intervalo mínimo entre tentativas do mesmo perfil
    # Teto para o app chegar ao primeiro plano depois de aberto: a frio, ~8 s; na primeira abertura depois de instalar,
    # 25 s (medido). Classificar antes disso lê o launcher ou tela nenhuma.
    open_timeout_s: float = Field(60.0, ge=5, le=300)
    settle_s: float = Field(3.0, ge=0.5, le=30)         # espera depois de o app aparecer, antes de classificar
    submit_wait_s: float = Field(25.0, ge=5, le=120)    # quanto observar depois do toque em Entrar
    verify_timeout_s: float = Field(45.0, ge=5, le=300)
    # Validade do "Conectado". Passado esse tempo a sessão é RELIDA do aparelho antes da tarefa (sem tentar
    # autenticar). Sem validade, o cache nunca expirava: havia perfis `session_ready` verificados três dias
    # antes, um deles de uma conta que o dono já tinha relatado presa num desafio. 0 desliga a reverificação.
    session_max_age_s: int = Field(43_200, ge=0, le=2_592_000)      # 12 h


class ReleasesCfg(BaseModel):
    """Entrega e conferência de versão no parque."""

    # Validade do "está instalado aqui". Passado esse tempo, o estado do app volta a ser lido DO APARELHO quando
    # ele entra no ar, em vez de continuar valendo por herança. Existiu um `ready` de três dias atrás, escrito
    # quando aquele id lógico era outro aparelho físico. 0 desliga a reobservação.
    verify_max_age_h: int = Field(24, ge=0, le=8760)
    # Prazos do instalador, em segundos. Estão na configuração porque o custo REAL deles muda com a máquina: no
    # worker remoto, sob disputa de CPU, `adb shell` já estourou 30 s quatro vezes durante uma entrega — e o
    # conjunto do Instagram (238 MB de base + split, por `install-multiple` numa única conexão SSH) é o caso
    # mais pesado que existe aqui. Ajustar ao medido passa a ser uma linha de YAML, não uma edição de código.
    profile_timeout_s: float = Field(30, ge=5, le=600)      # leitura do perfil do aparelho (getprop, densidade)
    inspect_timeout_s: float = Field(40, ge=5, le=600)      # leitura do estado do app (`pm path`/`dumpsys`)
    install_timeout_s: float = Field(900, ge=60, le=7200)   # `install`/`install-multiple` do conjunto inteiro
    launch_deadline_s: float = Field(90, ge=5, le=600)      # prova de abertura: o app tem de aparecer e ficar


class AppSeed(BaseModel):
    id: str
    name: str
    package: str
    activity: str | None = None
    apk_path: str | None = None
    nav_hints: str | None = None
    known_selectors: dict[str, str] | None = None
    builtin: bool = False


class SensitiveScreenSeed(BaseModel):
    """Uma tela que este parque declara sensível — nem imagem em disco, nem imagem para o provedor de IA.

    Achado #127: o critério embutido (campo de senha, desafio de 2FA, VM-loja) é genérico por natureza e não sabe
    que a tela de "dados da conta" DAQUELE app tem documento, endereço ou conversa de terceiro. Com o catálogo de
    apps, o parque passa a operar aplicativos que ninguém analisou — quem os cadastrou é quem sabe, e é aqui que
    ele diz. Sem `package`, a regra vale para qualquer app.
    """

    package: str | None = None
    resource_ids: list[str] = []      #: casa por SUFIXO do resource-id (`:id/cpf`)
    texts: list[str] = []             #: casa por texto contido, sem acento e sem caixa
    why: str | None = None            #: aparece na mensagem da etapa e na evidência


class AppConfigFile(BaseModel):
    server: ServerCfg = ServerCfg()
    paths: PathsCfg = PathsCfg()
    android: AndroidCfg = AndroidCfg()
    instances: InstancesCfg = InstancesCfg()
    appium: AppiumCfg = AppiumCfg()
    limits: LimitsCfg = LimitsCfg()
    ai: AiCfg = AiCfg()
    instagram: InstagramCfg = InstagramCfg()
    releases: ReleasesCfg = ReleasesCfg()
    apps: list[AppSeed] = []
    sensitive_screens: list[SensitiveScreenSeed] = []

    @model_validator(mode="after")
    def _instancias_coerentes(self) -> "AppConfigFile":
        """Confere o que antes só quebrava tarde — ou nunca.

        `instance_android` aplica o override com `model_copy(update=)`, que não valida nada: uma chave escrita errada
        (`hibernacao`, `sytem_image`) era ignorada em silêncio e o aparelho subia com o padrão, sem aviso. Para o
        aparelho-loja isso seria o pior desfecho possível — subir sem janela, ou com a imagem sem Play Store.
        """
        inst = self.instances
        ids = {f"{inst.id_prefix}{i:02d}" for i in range(1, inst.count + 1)}
        conhecidas = set(AndroidCfg.model_fields)
        for iid, over in inst.overrides.items():
            if iid not in ids:
                raise ValueError(f"instances.overrides.{iid}: essa instância não existe (count={inst.count})")
            estranhas = sorted(set(over) - conhecidas)
            if estranhas:
                raise ValueError(f"instances.overrides.{iid}: chave(s) desconhecida(s): {', '.join(estranhas)}")
            AndroidCfg.model_validate({**self.android.model_dump(), **over})     # tipo e faixa de cada valor
        # `external` era o único bloco NÃO conferido: um id escrito errado (`android-9` em vez de `android-09`) era
        # ignorado em silêncio, e o aparelho que devia vir da outra máquina subia como emulador local vazio.
        for iid in inst.external:
            if iid not in ids:
                raise ValueError(f"instances.external.{iid}: essa instância não existe (count={inst.count})")
        if inst.store and inst.store not in ids:
            raise ValueError(f"instances.store: '{inst.store}' não existe (count={inst.count})")
        # A loja PODE ser um aparelho de worker. Aqui havia a recusa "a loja é um emulador que o projeto liga e
        # desliga", e ela tornava a VM da conta Google impossível de ser remota POR CONSTRUÇÃO — não por decisão.
        # Quem liga e desliga um aparelho externo é o agente do worker, pelo mesmo verbo do central (fase 1), e a
        # cópia do pacote é um `adb pull` que atravessa o túnel como qualquer outro comando. O que a loja remota
        # exige a mais é a imagem com Play Store, janela e RAM próprios — e isso agora se declara por aparelho no
        # YAML do worker (`worker/settings.py: DeviceSpec`).
        #
        # O que NÃO mudou, de propósito: o painel continua recusando texto na loja (`store_text_blocked`). Com a
        # loja num worker, a janela do emulador está na área de trabalho DAQUELA máquina — o caminho para digitar
        # a conta Google lá é um acesso remoto a ela, fora da plataforma. Liberar digitação por um canal do painel
        # é a decisão 4 do plano, e ela é do dono.
        return self

    @model_validator(mode="after")
    def _ia_coerente(self) -> "AppConfigFile":
        """Papel, provedor e preço conferidos na PARTIDA — não na primeira chamada paga (achados #91 e #97)."""
        ai = self.ai
        for papel in ai.roles:
            if papel not in AI_ROLES:
                raise ValueError(f"ai.roles.{papel}: função desconhecida (use {', '.join(AI_ROLES)})")
        for nome, prov in ai.providers.items():
            if prov.kind == "openai" and not (prov.base_url or "").strip():
                raise ValueError(f"ai.providers.{nome}: kind=openai exige base_url (ex.: http://127.0.0.1:8001/v1)")
        for papel, r in ai.roles.items():
            for campo, alvo in (("provider", r.provider), ("fallback_provider", r.fallback_provider)):
                if alvo and alvo not in ai.providers:
                    raise ValueError(f"ai.roles.{papel}.{campo}: provedor '{alvo}' não está em ai.providers")
            # A família manda, como em `Config.model_caps`: produção usa `claude-haiku-4-5-20251001`, e exigir a
            # chave exata aqui rejeitaria uma configuração que o runtime aceitaria.
            declarado = (r.model in ai.models or re.sub(r"-\d{8}$", "", r.model or "") in ai.models
                         or any((r.model or "").startswith(k) for k in ai.models))
            if r.model and r.provider and ai.providers[r.provider].kind != "simulated" and not declarado:
                raise ValueError(f"ai.roles.{papel}.model: '{r.model}' não está declarado em ai.models "
                                 "(capacidade por modelo é declarada, não descoberta por erro 400)")
        return self


@dataclass(frozen=True, slots=True)
class ResolvedRole:
    """Uma função de IA depois de somar YAML + `.env` + padrões. É o que o roteador e a aba IA leem."""

    role: str
    provider: str                       # nome do provedor (chave de ai.providers, ou o do .env)
    kind: str                           # anthropic | openai | simulated
    model: str
    base_url: str | None
    api_key_env: str | None
    sends_data_externally: bool
    fallback_provider: str | None
    refusal_fallback: bool
    timeout_s: float
    max_retries: int
    concurrency: int
    effort: Effort

    @property
    def endpoint(self) -> str:
        """Só o host — a aba IA precisa responder "para onde isto vai", não repetir uma URL com credencial."""
        if not self.base_url:
            return "api.anthropic.com" if self.kind == "anthropic" else "(local)"
        sem_esquema = self.base_url.split("://", 1)[-1]
        return sem_esquema.split("/", 1)[0]


class Config:
    """Configuração resolvida (arquivo + ambiente), com caminhos absolutos."""

    def __init__(self, file: AppConfigFile, env: EnvSettings, root: Path = PROJECT_ROOT):
        self.file = file
        self.env = env
        self.root = root
        if env.android_sdk_root:
            self.file.android.sdk_root = env.android_sdk_root

    def path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else (self.root / p)

    @property
    def data_dir(self) -> Path:
        return self.path(self.file.paths.data_dir)

    @property
    def db_path(self) -> Path:
        return Path(self.env.poc_db_path) if self.env.poc_db_path else self.data_dir / "poc.sqlite3"

    @property
    def db_dsn(self) -> str:
        """O que abrir. `DATABASE_URL` manda; sem ela, o arquivo SQLite de sempre.

        Quem abre o banco usa ISTO, não `db_path`: `db_path` continua existindo porque há coisas que só fazem
        sentido com arquivo (retenção, conferir que nenhuma senha ficou em claro nos bytes).
        """
        return self.env.database_url or str(self.db_path)

    @property
    def api_token(self) -> str | None:
        """Segredo para chamadas de fora do loopback, ou None quando o backend só atende localmente."""
        return self.env.api_token.get_secret_value() if self.env.api_token else None

    @property
    def tls_direto(self) -> tuple[str, str] | None:
        """`(cert, key)` quando este processo deve subir em HTTPS por conta própria; `None` quando não."""
        cert, chave = self.file.server.tls_cert, self.file.server.tls_key
        return (cert, chave) if cert and chave else None

    @property
    def tls_ativo(self) -> bool:
        """O navegador chega por HTTPS? Verdadeiro com certificado aqui OU com proxy TLS declarado.

        Quem pergunta é o cookie de sessão: `Secure` num cookie servido por HTTP simples faz o navegador
        descartá-lo, e o painel do loopback (que é HTTP por desenho) pararia de logar.
        """
        return self.tls_direto is not None or bool(self.file.server.tls_behind_proxy)

    @property
    def owner_id(self) -> str:
        """Identidade do processo que assume etapas. É a MÁQUINA, não o PID.

        Escolha deliberada: com identidade por PID, um backend reiniciado não reconheceria as próprias etapas
        interrompidas e teria de esperar o lease vencer para retomá-las — trocaria reconciliação imediata (provada)
        por espera. Com identidade por máquina, o reinício retoma o que é dele na hora e o backend de outra máquina
        continua impedido de mexer. O preço aceito: dois backends na mesma máquina exigem `OWNER_ID` explícito.
        """
        return self.env.owner_id or socket.gethostname()

    @property
    def role(self) -> str:
        """`api` | `scheduler` | `all`. O papel deste processo (seção 3 do pedido: API sem scheduler e vice-versa)."""
        return self.env.role

    @property
    def hospeda_aparelhos(self) -> bool:
        """Este processo tem emulador/túnel e responde pelo ciclo de vida dos aparelhos da configuração dele.

        É o que decide se ele CARIMBA `instances.hosted_by`: um processo `api` que carimbasse roubaria os
        aparelhos do scheduler da mesma máquina — e depois ninguém os ligaria.
        """
        return self.env.role in ("scheduler", "all")

    @property
    def roda_scheduler(self) -> bool:
        """Este processo despacha a fila e reconcilia na partida."""
        return self.env.role in ("scheduler", "all")

    @property
    def serve_api(self) -> bool:
        """Este processo publica a API REST e o frontend. O canal do worker não depende disto: ele é o transporte
        dos aparelhos desta máquina, e quem os hospeda precisa dele mesmo sem REST."""
        return self.env.role in ("api", "all")

    @property
    def max_clock_skew_s(self) -> float:
        return float(self.env.max_clock_skew_s)

    @property
    def avd_home(self) -> Path:
        return self.path(self.file.paths.avd_home)

    @property
    def evidence_dir(self) -> Path:
        return self.path(self.file.paths.evidence_dir)

    @property
    def logs_dir(self) -> Path:
        return self.path(self.file.paths.logs_dir)

    @property
    def apk_dirs(self) -> list[Path]:
        return [self.path(d).resolve() for d in self.file.paths.apk_dirs]

    @property
    def apk_inbox(self) -> Path:
        return self.path(self.file.paths.apk_inbox)

    @property
    def apk_catalog(self) -> Path:
        return self.path(self.file.paths.apk_catalog)

    @property
    def sdk_root(self) -> Path:
        return Path(self.file.android.sdk_root)

    def instance_ids(self) -> list[str]:
        c = self.file.instances
        return [f"{c.id_prefix}{i:02d}" for i in range(1, c.count + 1)]

    @property
    def store_id(self) -> str | None:
        """Id do aparelho-loja, ou `None`. Nunca é aparelho de tarefa: quem decide isso lê daqui."""
        return self.file.instances.store or None

    def instance_android(self, instance_id: str) -> AndroidCfg:
        """Configuração Android efetiva da instância (padrão + overrides)."""
        over = self.file.instances.overrides.get(instance_id) or {}
        return self.file.android.model_copy(update=over)

    def override_images(self) -> dict[str, str]:
        """Instância → imagem de sistema, só para quem usa imagem DIFERENTE da padrão (ex.: a loja, com Play Store)."""
        padrao = self.file.android.system_image
        return {iid: img for iid in self.instance_ids()
                if (img := self.instance_android(iid).system_image) != padrao}

    # ================================================================== hub de IA (item 7.1)
    def ai_model_for(self, role: str) -> str:
        """Modelo de uma função pelo `.env`, na precedência de sempre: AI_MODEL_<PAPEL> → cadeia → AI_MODEL."""
        env, base = self.env, self.env.ai_model
        por_papel = {"plan": env.ai_model_planner or base, "decide": env.ai_model_actor or base}
        por_papel["verify"] = env.ai_model_verifier or env.ai_model_actor or base
        por_papel["escalation"] = env.ai_model_escalation or por_papel["plan"]
        # Escrever como a persona é redação, não navegação: por padrão usa o modelo do planejador.
        por_papel["social"] = env.ai_model_social or por_papel["plan"]
        return por_papel.get(role, base)

    def ai_effort_for(self, role: str) -> Effort:
        env = self.env
        if role == "plan" or role == "social":
            return env.ai_effort_planner
        if role == "verify":
            return env.ai_effort_verifier or env.ai_effort_actor
        return env.ai_effort_actor

    def ai_role(self, role: str) -> ResolvedRole:
        """A função resolvida: YAML manda, `.env` é o padrão, `ROLE_DEFAULTS` fecha o que ninguém disse.

        Sem bloco `ai.roles` no YAML o resultado é EXATAMENTE o de antes do hub — provedor único do `.env`,
        modelo por função pela cadeia de sempre. É o que mantém o `.env` de produção valendo sem uma linha nova.
        """
        ai = self.file.ai
        r = ai.roles.get(role) or RoleCfg()
        padrao = ROLE_DEFAULTS.get(role, {"timeout_s": 60.0, "concurrency": 4})
        nome = r.provider or (self.env.ai_provider or "anthropic").strip().lower()
        prov = ai.providers.get(nome)
        if prov is None:
            # Provedor não declarado no YAML: é o do `.env` (anthropic/simulated), sem endpoint próprio.
            kind = nome if nome in ("anthropic", "openai", "simulated") else "anthropic"
            prov = ProviderCfg(kind=kind, sends_data_externally=(kind != "simulated"))  # type: ignore[arg-type]
        return ResolvedRole(
            role=role, provider=nome, kind=prov.kind, model=r.model or self.ai_model_for(role),
            base_url=prov.base_url, api_key_env=prov.api_key_env,
            sends_data_externally=prov.sends_data_externally and prov.kind != "simulated",
            fallback_provider=r.fallback_provider,
            refusal_fallback=(self.env.ai_refusal_fallback if r.refusal_fallback is None else r.refusal_fallback),
            timeout_s=float(r.timeout_s if r.timeout_s is not None else padrao["timeout_s"]),
            max_retries=int(r.max_retries if r.max_retries is not None else 0),
            concurrency=int(r.concurrency if r.concurrency is not None else padrao["concurrency"]),
            effort=self.ai_effort_for(role))

    def ai_roles(self) -> dict[str, ResolvedRole]:
        return {papel: self.ai_role(papel) for papel in AI_ROLES}

    def model_caps(self, model: str) -> ModelCaps:
        """Capacidade DECLARADA do modelo. Sem declaração, o conservador: nada de strict, nada de thinking/effort.

        Conservador de propósito (achado #97): um modelo desconhecido é quase sempre um modelo LOCAL pequeno, e
        mandar-lhe `thinking` ou gramática estrita é exatamente o 400 que este registro existe para não pagar.
        """
        tabela = self.file.ai.models
        if model in tabela:
            return tabela[model]
        familia = re.sub(r"-\d{8}$", "", model or "")
        if familia in tabela:
            return tabela[familia]
        achado = next((v for k, v in tabela.items() if familia.startswith(k)), None)
        return achado or ModelCaps(strict_tools=False, thinking=False, effort=False,
                                   structured_output="json_object", note="capacidade não declarada em ai.models")

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.avd_home, self.evidence_dir, self.logs_dir, self.apk_inbox):
            d.mkdir(parents=True, exist_ok=True)


def config_file_path(config_path: str | os.PathLike[str] | None = None,
                     env: EnvSettings | None = None) -> Path | None:
    """O arquivo que a configuração vai ler de verdade — ou `None` quando não há nenhum.

    Achado #177: `config/config.yaml` deixou de ser versionado (ele é o retrato de UMA instalação). Numa
    cópia nova do repositório ele não existe ainda, e antes disso o backend subia calado nos PADRÕES do
    código — 10 instâncias que ninguém escreveu. Agora ele cai no `config.example.yaml` ao lado, que é o
    ponto de partida neutro e versionado. `scripts/start.ps1` copia o exemplo na primeira partida, como já
    fazia com o `.env`; a partir daí o arquivo do dono é que manda.
    """
    env = env or EnvSettings()
    path = Path(config_path or env.poc_config or (PROJECT_ROOT / "config" / "config.yaml"))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if path.exists():
        return path
    exemplo = path.with_name(f"{path.stem}.example{path.suffix}")
    return exemplo if exemplo.exists() else None


def load_config(config_path: str | os.PathLike[str] | None = None, env: EnvSettings | None = None) -> Config:
    env = env or EnvSettings()
    path = config_file_path(config_path, env)
    raw: dict[str, Any] = {}
    if path is not None:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config(AppConfigFile.model_validate(raw), env)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
