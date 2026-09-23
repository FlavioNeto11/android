"""Configuração central: config/config.yaml (estrutura) + .env (segredos e escolhas de ambiente)."""
from __future__ import annotations

import os
import socket
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


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
    ai_effort_planner: str = Field(default="medium", alias="AI_EFFORT_PLANNER")
    ai_effort_actor: str = Field(default="low", alias="AI_EFFORT_ACTOR")
    ai_effort_verifier: str | None = Field(default=None, alias="AI_EFFORT_VERIFIER")     # vazio = esforço do ator
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
    ram_mb: int = 2560
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
    no_progress_limit: int = Field(4, ge=2, le=20)
    ai_max_calls_per_objective: int = Field(60, ge=1, le=1000)
    ai_max_tokens_per_run: int = Field(3_000_000, ge=1000)
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


class AiCfg(BaseModel):
    screenshot_max_side: int = 1280             # lado maior da imagem enviada ao modelo (tokens ∝ área)
    max_hierarchy_elements: int = 140           # linhas da hierarquia no prompt (priorizadas; a árvore completa fica local)
    image_policy: Literal["always", "auto", "never"] = "always"
    # auto: só hierarquia quando a árvore é rica; imagem na 1ª decisão de etapa julgada por visão, em árvore pobre,
    # após erro/ciclo, ou quando o modelo pede (observe_screen.need_image)
    rich_tree_min_elements: int = 8
    strong_model_for_side_effect: bool = True   # etapa com efeito externo decide no modelo de escalonamento
    verify_max_model_calls: int = Field(2, ge=1, le=5)
    # Depois de um "sim" numa etapa com efeito já disparado, quanto esperar antes de RECONFERIR a tela em busca
    # de marca de falha. Existe porque app de mensagem tem UI otimista: o balão aparece e o campo limpa antes de
    # o servidor confirmar, e a falha só chega depois.
    effect_settle_s: float = Field(4.0, ge=0, le=30)
    # Receitas: off = só IA · shadow = aprende e compara com a IA, sem agir · replay = repete sem IA, IA só se divergir
    recipes: Literal["off", "shadow", "replay"] = "off"
    flows: bool = False                          # reaproveita o plano de comandos repetidos (sem chamar o planejador)
    pathfinder_wait_s: int = Field(0, ge=0, le=3600)   # >0: numa execução sem receita, 1 aparelho aprende e os demais esperam
    # US$ por milhão de tokens [entrada, leitura de cache, gravação de cache, saída] — platform.claude.com/docs/en/about-claude/pricing
    prices: dict[str, list[float]] = {
        "claude-opus-5": [5.0, 0.5, 6.25, 25.0],
        "claude-sonnet-5": [2.0, 0.2, 2.5, 10.0],
        "claude-haiku-4-5": [1.0, 0.1, 1.25, 5.0],
    }


class InstagramCfg(BaseModel):
    """Automação do Instagram. Os limites existem para não bloquear a própria conta."""

    package: str = "com.instagram.android"
    # Conta diferente da esperada NUNCA continua em silêncio. Ligado, o sistema desloga e entra na conta certa.
    auto_switch_account: bool = False
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
        if inst.store:
            if inst.store not in ids:
                raise ValueError(f"instances.store: '{inst.store}' não existe (count={inst.count})")
            if inst.store in inst.external:
                raise ValueError(f"instances.store: '{inst.store}' não pode ser também aparelho externo — a loja é "
                                 "um emulador que o projeto liga e desliga")
        return self


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

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.avd_home, self.evidence_dir, self.logs_dir, self.apk_inbox):
            d.mkdir(parents=True, exist_ok=True)


def load_config(config_path: str | os.PathLike[str] | None = None, env: EnvSettings | None = None) -> Config:
    env = env or EnvSettings()
    path = Path(config_path or env.poc_config or (PROJECT_ROOT / "config" / "config.yaml"))
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Config(AppConfigFile.model_validate(raw), env)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
