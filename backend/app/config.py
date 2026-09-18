"""Configuração central: config/config.yaml (estrutura) + .env (segredos e escolhas de ambiente)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr
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
    instagram_credentials_master_key: str | None = Field(default=None, alias="INSTAGRAM_CREDENTIALS_MASTER_KEY")
    android_sdk_root: str | None = Field(default=None, alias="ANDROID_SDK_ROOT")
    poc_config: str | None = Field(default=None, alias="POC_CONFIG")
    poc_db_path: str | None = Field(default=None, alias="POC_DB_PATH")


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
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

    max_active_devices: int = Field(10, ge=1, le=10)
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
    max_online_devices: int = Field(10, ge=1, le=10)
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
    settle_s: float = Field(3.0, ge=0.5, le=30)         # espera depois de abrir o app, antes de classificar
    submit_wait_s: float = Field(25.0, ge=5, le=120)    # quanto observar depois do toque em Entrar
    verify_timeout_s: float = Field(45.0, ge=5, le=300)


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
    apps: list[AppSeed] = []


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

    def instance_android(self, instance_id: str) -> AndroidCfg:
        """Configuração Android efetiva da instância (padrão + overrides)."""
        over = self.file.instances.overrides.get(instance_id) or {}
        return self.file.android.model_copy(update=over)

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
