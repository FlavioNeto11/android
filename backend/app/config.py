"""Configuração central: config/config.yaml (estrutura) + .env (segredos e escolhas de ambiente)."""
from __future__ import annotations

import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Só stdlib e sem import de `app` (vai ao agente do worker junto com este arquivo): não fecha ciclo.
from .devices.apps_de_fundo import PADRAO as PADRAO_DE_APPS_DE_FUNDO
from .devices.apps_de_fundo import validar_lista as validar_apps_de_fundo
from .devices.sonda_rede import host_valido

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Esforço aceito pela API. Fora desta lista é erro de configuração, não um 400 a ser "aprendido".
Effort = Literal["low", "medium", "high", "xhigh", "max"]

#: As funções de IA. A ordem é a que o painel mostra. `persona` (item 17.8) é a geração/enriquecimento de persona:
#: SEM bloco próprio ela herda a configuração de `social` por inteiro (`Config.ai_role`), então existe para poder ir a
#: outro provedor sem arrastar o `social` da execução (comentário, DM) junto.
AI_ROLES = ("plan", "decide", "verify", "escalation", "social", "persona")
#: Funções que SÓ existem quando escritas em `ai.roles` (item 12.5, ADR-070). `leitura` é o segundo leitor da leitura
#: visual: transcreve o recorte de uma linha que a árvore não expõe, sem ver o valor do ator. NÃO herda nada de outra
#: função (nem provedor, nem modelo, nem `fallback_provider`), porque a conferência vale pela independência dele, e não
#: aparece em `ai.profiles` (o perfil troca o ator, não o conferente). Sem bloco, `Config.ai_leitura()` é `None` e a
#: leitura visual recusa com `sem_leitor`.
ROLES_OPCIONAIS = ("leitura",)


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=str(PROJECT_ROOT / ".env"), env_file_encoding="utf-8", extra="ignore")

    ai_provider: str = Field(default="anthropic", alias="AI_PROVIDER")
    anthropic_api_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    #: Chave da API de IMAGENS (`ai.image.provider: openai`). Outra conta, outro saldo: o crédito da Anthropic não
    #: compra imagem. `SecretStr`, lida daqui e nunca de `os.environ` solto — não aparece em repr nem em log.
    openai_api_key: SecretStr | None = Field(default=None, alias="OPENAI_API_KEY")
    #: Chaves de ADMINISTRADOR, só para ler o custo da organização e conciliar o saldo estimado (ADR-051). Nunca
    #: vão a chamada de modelo. Na OpenAI, crie com permissão só de leitura; a da Anthropic não tem escopo.
    anthropic_admin_key: SecretStr | None = Field(default=None, alias="ANTHROPIC_ADMIN_KEY")
    openai_admin_key: SecretStr | None = Field(default=None, alias="OPENAI_ADMIN_KEY")
    #: Chaves de outros provedores compatíveis com OpenAI (`ai.providers.<nome>.api_key_env`, Fase 17). Declaradas
    #: aqui porque o `.env` é lido pelo pydantic e NÃO vai para `os.environ`: um provedor que procurasse só no
    #: ambiente do processo recebia 401 com a chave escrita no `.env` (o Ollama não usa chave e escondeu isso).
    gemini_api_key: SecretStr | None = Field(default=None,
                                             validation_alias=AliasChoices("GEMINI_API_KEY", "GOOGLE_API_KEY"))
    deepseek_api_key: SecretStr | None = Field(default=None, alias="DEEPSEEK_API_KEY")
    dashscope_api_key: SecretStr | None = Field(default=None, alias="DASHSCOPE_API_KEY")
    #: Venice (api.venice.ai, compatível com OpenAI; validação de 07/10/2026 em relatorio-validacao.md §31). Sem este
    #: campo, `VENICE_API_KEY` escrito no `.env` cairia em `os.environ` e a primeira chamada voltaria 401.
    venice_api_key: SecretStr | None = Field(default=None, alias="VENICE_API_KEY")
    #: Chave do provedor semântico de retrieval de contexto (`context_retrieval.semantic.provider: jev`). Só do ambiente
    #: ou do `.env`, nunca do `config.yaml`; ausente = provedor indisponível e o retrieval cai no local (ADR-063).
    typesafe_api_key: SecretStr | None = Field(default=None, alias="TYPESAFE_API_KEY")
    #: Aviso fora do painel (item 28.11): o token do bot criado no @BotFather e o chat para onde ele escreve. Nomes
    #: FIXOS, só do `.env` ou do ambiente, nunca do `config.yaml`. `SecretStr`: não aparecem em repr nem em log.
    telegram_bot_token: SecretStr | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: SecretStr | None = Field(default=None, alias="TELEGRAM_CHAT_ID")
    #: E-mail do parque (docs/email-do-parque.md): caixa compartilhada catch-all `*@EMAIL_DOMINIO` lida por IMAP.
    #: Nomes FIXOS, só do `.env` ou do ambiente, nunca do `config.yaml`. Sem host, usuário e senha só se gera e
    #: valida endereço; ler código dá `email_indisponivel`. A senha é `SecretStr`: fora de repr e de log.
    email_dominio: str = Field(default="", alias="EMAIL_DOMINIO")
    email_imap_host: str = Field(default="", alias="EMAIL_IMAP_HOST")
    email_imap_port: int = Field(default=993, alias="EMAIL_IMAP_PORT")
    email_imap_user: str = Field(default="", alias="EMAIL_IMAP_USER")
    email_imap_pass: SecretStr | None = Field(default=None, alias="EMAIL_IMAP_PASS")
    #: Domínios que a API aceita gerar/registrar, separados por vírgula; vazio = só `EMAIL_DOMINIO`.
    email_allowlist_dominios: str = Field(default="", alias="EMAIL_ALLOWLIST_DOMINIOS")

    @field_validator("email_imap_port", mode="before")
    @classmethod
    def _porta_vazia_e_a_padrao(cls, v: object) -> object:
        """`EMAIL_IMAP_PORT=` em branco (cópia do exemplo) não pode derrubar a partida."""
        return 993 if isinstance(v, str) and not v.strip() else v

    def email_allowlist(self) -> tuple[str, ...]:
        """`EMAIL_ALLOWLIST_DOMINIOS` em tupla: minúsculo, sem vazios nem repetidos, na ordem escrita."""
        return tuple(dict.fromkeys(d for d in (x.strip().lower() for x in self.email_allowlist_dominios.split(","))
                                   if d))
    #: Trello do dono (item 32.2, ADR-072): a chave do Power-Up, o token do dono e o segredo do aplicativo (que assina o
    #: webhook). Nomes FIXOS, só do `.env` ou do ambiente, nunca do `config.yaml`. A autenticação vai no cabeçalho
    #: `Authorization`, nunca na URL.
    trello_api_key: SecretStr | None = Field(default=None, alias="TRELLO_API_KEY")
    trello_token: SecretStr | None = Field(default=None, alias="TRELLO_TOKEN")
    trello_api_secret: SecretStr | None = Field(default=None, alias="TRELLO_API_SECRET")
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

    def chave(self, nome: str | None) -> str:
        """Valor da chave pelo NOME da variável (`api_key_env`): o campo declarado acima, que o pydantic lê do `.env`
        e do ambiente; nome não declarado cai em `os.environ`. Vazio quando não há — um endpoint local não precisa."""
        if not nome:
            return ""
        for campo, info in type(self).model_fields.items():
            apelidos = {info.alias, *(getattr(info.validation_alias, "choices", None) or [])}
            if nome in apelidos:
                valor = getattr(self, campo)
                if isinstance(valor, SecretStr):
                    return valor.get_secret_value()
                return str(valor or "")
        return os.environ.get(nome, "")


#: Os modos de `server.csp_do_painel` (29.91). Um tipo só para a configuração e para o `PainelEstatico`: um valor
#: digitado errado no código não passa no mypy em vez de falhar aberto.
ModoDaCspDoPainel = Literal["aplicar", "so_relatar", "desligada"]


#: 31.72, releitura do #386: sufixos públicos que `ai.consentimento_aceito_em` recusa (um host vale para os
#: subdomínios). Lista fechada e curta, sem rede: os do Brasil e os genéricos mais comuns.
_SUFIXOS_PUBLICOS = frozenset({
    "com.br", "net.br", "org.br", "gov.br", "edu.br", "jus.br", "leg.br", "mil.br", "art.br", "blog.br", "app.br",
    "co.uk", "org.uk", "gov.uk", "ac.uk", "com.au", "com.ar", "com.mx", "com.pt", "co.jp", "github.io",
    "herokuapp.com", "vercel.app", "netlify.app", "pages.dev", "web.app", "firebaseapp.com", "blogspot.com",
})


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
    #: A CSP do `index.html` do painel (29.91). `aplicar` barra o que não for do próprio painel; `so_relatar` manda o
    #: mesmo texto como `Content-Security-Policy-Report-Only` (nada é barrado, e cada violação aparece no console do
    #: navegador); `desligada` não manda nenhuma. Existe para desfazer sem deploy, só com o reinício da `farm-central`,
    #: se a CSP quebrar uma tela no central.
    csp_do_painel: ModoDaCspDoPainel = "aplicar"


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
    #: `-crash-report-mode` do emulador (29.55). Com relatório de falha pendente (`emu-crash-*.db/reports/*.dmp`,
    #: deixado por um emulador que caiu na saída), o padrão do emulador é PERGUNTAR, e a subida parava no
    #: diálogo ("Showing crashdialog to get consent") sem ninguém para responder (incidente de 03/10/2026 19:00Z).
    #: `never` não pergunta nem envia. `""` não passa a flag (volta ao padrão do emulador).
    crash_report_mode: Literal["never", "disabled", "ask", "always", ""] = "never"
    boot_timeout_s: int = 480
    est_instance_ram_mb: int | None = None      # RAM real por instância no host; None = ram_mb + 1100 (medido)
    min_free_ram_mb_after_boot: int = 1500      # folga que o host deve manter depois de cada boot
    #: Admissão por CPU (29.33, RA-4): boot novo numa máquina com a CPU ACIMA disto espera (host: recusa com espera
    #: crescente, como a RAM; worker: o rodízio segura pela CPU da última batida). 100 desliga. CPU desconhecida nunca
    #: recusa. Subir emulador com a máquina saturada alonga todos os boots em voo, e o preparo deles estourava.
    max_cpu_percent_before_boot: float = Field(85.0, ge=1.0, le=100.0)
    extra_emulator_args: list[str] = []
    #: DNS que o emulador entrega ao convidado (`-dns-server`). Vazio = autodetecção do emulador, que pega os
    #: primeiros DNS do host. Medido em 25/09/2026 no central: o DHCP do roteador entregava `1.178.36.77` (morto)
    #: antes de `8.8.8.8`; o Windows contorna, o emulador não — a rede móvel (`10.0.2.3`) só consultava o morto e
    #: nenhum nome resolvia. É POR MÁQUINA (central e cada worker têm a sua rede): use DNS que respondem ali.
    dns_servers: list[str] = []
    hibernation: bool = False                   # rodízio desliga salvando snapshot; acordar leva segundos (medido: ~7 s)
    wake_timeout_s: int = 90                    # acordar que não chega à interface nesse tempo, CONTADO DO SNAPSHOT CARREGADO (RA-15) → descarta, boot a frio
    # Sobe o emulador COM janela. Existe para o aparelho-loja: a conta Google é digitada direto na janela do
    # emulador, e assim nenhuma tecla passa pelo backend, pelo Appium ou pelo adb. O parque segue sem janela.
    window: bool = False
    #: Apps de fundo que o preparo DESATIVA nos aparelhos da automação (`pm disable-user --user 0`), para caber nos
    #: 2 GB do convidado — medido em 29/09/2026: app Google 101 MB, ASI 37 MB, Mensagens 22 MB no android-06; load
    #: 14,7 e 82 MB livres no android-04, com YouTube e Gmail subindo sozinhos (`devices/apps_de_fundo.py`). O padrão é
    #: o conservador de lá. Reversível: tirado da lista, o pacote volta (`pm enable`) no preparo seguinte — só o que o
    #: próprio preparo desativou; `[]` devolve tudo. Pacote protegido (Play Store, GMS, GSF, WebView, teclado,
    #: launcher, SystemUI, Chrome, `io.appium.*`) ou app alvo (declarado em `app/conhecimento/apps/`, em `apps` ou em
    #: `contas.sessao`) é RECUSADO na carga. Vale para o que o CENTRAL prepara (os emuladores dele e os dos workers,
    #: pelo túnel), menos a loja e o celular físico; no `worker.yaml` não tem efeito.
    desativar_apps: list[str] = Field(default_factory=lambda: list(PADRAO_DE_APPS_DE_FUNDO))

    # ------------------------------------------------------------------ o que vale de fato
    # As três perguntas que o resto do código faz — quanto de RAM dar ao AVD, com que flags subir, quanto o host
    # vai pagar — respondidas num lugar só. Antes cada chamador lia `ram_mb` cru, e o perfil da imagem não
    # existia: o android-12 subiu com 1536 MB numa imagem que precisa de 2048 e entrou em thrash pós-boot.
    def perfil(self) -> Any:
        from .devices.perfis import perfil_por_imagem  # noqa: PLC0415 - evita ciclo config → devices → config
        return perfil_por_imagem(self.system_image)

    @field_validator("dns_servers")
    @classmethod
    def _dns_validos(cls, v: list[str]) -> list[str]:
        import ipaddress  # noqa: PLC0415
        limpos = [str(x).strip() for x in v if str(x).strip()]
        for x in limpos:
            ipaddress.ip_address(x)          # ValueError → erro de validação na carga da config, não no boot
        return limpos

    @field_validator("desativar_apps", mode="before")
    @classmethod
    def _apps_desativaveis(cls, v: object) -> list[str]:
        if not isinstance(v, (list, tuple)):
            raise ValueError("desativar_apps: é uma lista de pacotes (ex.: [com.google.android.gm])")
        return validar_apps_de_fundo(v)          # protegido ou nome inválido → erro na carga, com o pacote e o motivo

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
    #: CPU desta máquina (%) a partir da qual o reparo automático de aparelho ESPERA em vez de subir de degrau: com a
    #: máquina saturada o convidado "degrada" por falta de CPU, e reiniciá-lo só piora (29/09, android-01, ADR-055).
    #: 101 desliga (a suíte de testes usa, porque roda com a máquina carregada).
    remediation_host_cpu_max: float = Field(90.0, ge=10, le=101)


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
    # 30.61: por quantas horas vale o sim dado na prévia da porta (aprovação antecipada no plano). Vencida conta como
    # ausente: a porta pergunta de novo na execução; "Renovar" na prévia estende sem reabrir os itens.
    aprovacao_no_plano_validade_h: int = Field(24, ge=1, le=72)
    max_actions_per_step: int = Field(12, ge=1, le=60)
    # 31.273 (ADR-084): o pedido fora do catálogo do app explora em vez de recusar. Tetos por exploração, lidos ao vivo
    # (PUT /api/settings): medir e ajustar. Estourar um teto NÃO é falha silenciosa: a etapa para com o motivo dito.
    exploracao_ligada: bool = True              # false = a recusa de antes (31.33)
    exploracao_max_acoes: int = Field(25, ge=1, le=200)       # ações do executor na etapa exploratória
    exploracao_max_chamadas_ia: int = Field(30, ge=1, le=500)  # chamadas de IA nas etapas exploratórias da execução
    exploracao_max_usd: float = Field(0.60, ge=0, le=1000)    # US$ da execução (tokens x preço); 0 = sem teto em dinheiro
    exploracao_max_por_dia: int = Field(5, ge=0, le=1000)     # explorações por dia por app; 0 = sem teto
    # 31.297 (ADR-091): o pedido de EFEITO (enviar, publicar, apagar…) também explora, julgado pela política do perfil
    # (porta 13.2) como uma ação do catálogo: sem a política liberar, pede aprovação antes de tocar no aparelho. Desligado de
    # fábrica: ligar é decisão do dono, e o efeito segue recusado como antes.
    exploracao_efeito_ligada: bool = False
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
    # T.2 (achado #164, fatia que faltava): o `await asyncio.sleep(2)` de `DeviceManager._wait_boot` era o
    # último número solto do laço de boot — sondar `boot_completed`/`ui_ready` a cada 2 s é o jeito certo com um
    # emulador de verdade, mas travava qualquer teste que quisesse exercitar esse laço diretamente (estouro de
    # prazo, por exemplo) a pagar em tempo real. Mesmo padrão de `scheduler_tick_s`: nunca zero.
    boot_poll_s: float = Field(2.0, ge=0.01, le=30)
    no_progress_limit: int = Field(4, ge=2, le=20)
    # Teto de reobservações automáticas seguidas quando a sessão fica `unknown` (achado #104): uma tela que
    # `classify()` não reconhece (sinal ausente da tabela, onboarding fora do mapa) não pode reabrir o app e
    # reobservar a cada tick para sempre. Ao alcançar o teto, a porta do despacho trata como "só uma pessoa
    # resolve" — mesmo caminho de auth_challenge/wrong_account — em vez de insistir sozinha. Item 29.92: aparelho com
    # vínculo ativo (conta real) tem teto 1 na porta, e a porta só existe com vínculo; este teto deixou de agir nela
    # e só vale onde não há vínculo (hoje, nenhum caminho automático). Continua limitando o `unknown_streak`. Não
    # remova nem renomeie: o `config.yaml` de uma instalação pode ter a chave.
    session_unknown_retry_cap: int = Field(3, ge=1, le=20)
    orquestracao_max_escolhidas: int = Field(30, ge=1, le=64)
    orquestracao_max_candidatas: int = Field(60, ge=1, le=120)
    operacao_max_acoes_executadas: int = Field(3, ge=0, le=64)
    # 31.220: de quanto em quanto tempo (s) o laço do sistema lê as operações abertas e as avança sem leitura externa
    # (`modules/operacoes/infrastructure/laco.py`). 0 = desligado, o padrão do corte 61 (a prova de 07/10 usa o laço
    # da Canais). Relido a cada volta.
    operacao_laco_s: int = Field(0, ge=0, le=3600)
    # 28.61 (dono, 06/10 19:46Z: "crie um grupo com tudo liberado para todas as personas … para que nao seja necessario
    # permissoes por enquanto"): o id do grupo de política cujas personas NÃO passam pela aprovação de POLÍTICA
    # (a escolha do perfil ou do grupo, a DM fria, a publicação no feed, a pessoa real num pedido, a citação da família e a
    # repetição já conhecida no `check`). Continuam: as recusas (frota, tetos, repetido, retirada), a exceção do 30.65, o
    # teto `preparar` da execução (a operação segue pelo liberar e por `operacao_max_acoes_executadas`), as confirmações
    # do despacho (a repetição e a família vistas depois do rascunho), a conduta e a
    # proteção de conta. Vazio = desligado. Lido ao vivo; desfazer é `PUT /api/settings {"grupo_sem_aprovacao": ""}`.
    grupo_sem_aprovacao: str = Field("", max_length=80)
    # 31.253 / ADR-082 (dono, 07/10: "colocar todas as personas em um grupo que libera tudo para não precisar de
    # permissão pra nada"; substitui em parte o 28.23): na operação com `acao_final=executar`, o alvo cuja persona está
    # no grupo acima nasce com o teto `agir`, e não `preparar` — sem pedido de aprovação, sem pergunta no Telegram e sem
    # passar pelo liberar. Fora do grupo, ou com isto desligado, segue `preparar`. Lido na criação de cada alvo.
    operacao_grupo_liberado_executa: bool = True
    # 30.60 (N4): publicar no feed (balde `posts`) passa por uma pessoa mesmo com perfil ou grupo `autonomous`, como a DM
    # fria do ADR-055. Só a instalação afrouxa, aqui; um perfil não tem esse poder.
    publicar_sem_aprovacao: bool = False
    ai_max_calls_per_objective: int = Field(60, ge=1, le=1000)
    # Item 17.12: o teto acima é de UM objetivo sem repetição. Num `for_each`, cada item a mais soma `ai_max_calls_per_item`
    # (`teto = ai_max_calls_per_objective + por_item × (itens − 1)`), até `ai_max_calls_absolute`. 12 = ~8 chamadas medidas
    # por envio (r-20261002181642-eff15b, com o rejulgamento do 17.10) mais folga para uma nova tentativa; 0 desliga a
    # proporção. O rejulgamento CONTINUA contando: o guarda não esconde chamada paga. Os tetos em US$ seguem valendo.
    ai_max_calls_per_item: int = Field(12, ge=0, le=200)
    ai_max_calls_absolute: int = Field(300, ge=1, le=5000)
    ai_max_tokens_per_run: int = Field(3_000_000, ge=1000)
    # Teto em DINHEIRO (achado #95). Os dois de cima estão em unidades que não se traduzem em US$ — e o de tokens
    # conta cache lido como token cheio. Estes somam `ai_calls × ai.prices`, que é a mesma conta de /api/usage.
    # Em 80 % vira aviso (evento + Problem em /api/health); em 100 % a chamada é recusada com kind='budget'.
    # `0` desliga. O VALOR do teto diário é decisão do dono: sai de fábrica desligado.
    ai_max_usd_per_run: float = Field(15.0, ge=0, le=10_000)
    ai_max_usd_per_day: float = Field(0.0, ge=0, le=100_000)
    capture_grid_interval_s: float = Field(5, ge=1, le=120)
    capture_focus_interval_s: float = Field(1, ge=0.3, le=30)
    #: Item 31.34: segundos em que a prévia da GRADE de um aparelho cede a vez a uma leitura da árvore dele (0 desliga).
    capture_yield_to_tree_s: float = Field(2.0, ge=0, le=30)
    frame_max_age_ms: int = Field(6000, ge=500, le=120000)
    # Prévia sob demanda (evolução de desempenho, contrato C2 do adendo v0.20). `on_demand`: só se captura prévia
    # de aparelho que algum painel está olhando (grade visível ou foco) ou que está sob controle manual — sem
    # espectador não há screencap periódico. `always` é o laço antigo (todo aparelho online a cada
    # `capture_grid_interval_s`, com ou sem espectador) e existe como volta atrás SEM reinício, por
    # PUT /api/settings. Bandeira temporária: sai quando `on_demand` tiver prova real no parque.
    preview_mode: Literal["on_demand", "always"] = "on_demand"
    log_retention_days: int = Field(14, ge=1, le=365)
    evidence_retention_days: int = Field(14, ge=1, le=365)
    # Rodízio: N contas sobre K vagas de RAM. O scheduler liga o aparelho quando há tarefa para ele e desliga um
    # ocioso quando falta vaga; `idle_stop_s` > 0 também desliga por ociosidade mesmo sem disputa.
    # ADR-085 (dono, 07/10): a automação liga o aparelho de que o trabalho precisa. Padrão `true`; `false` só em
    # instalação que quer o religamento na mão (a sugestão automática então avisa, em vez de prometer).
    auto_start_devices: bool = True
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
    #: Nome do campo do teto de saída no corpo do `/chat/completions` (Fase 17). `max_tokens` é o de sempre (Ollama,
    #: vLLM); a referência da OpenAI o dá como obsoleto em favor de `max_completion_tokens`. Só o provedor
    #: compatível com OpenAI lê isto.
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"
    #: Campos extras do corpo POR MODELO, aplicados DEPOIS do `extra_body` do provedor (Fase 17). É onde mora o que
    #: um modelo exige e o vizinho no mesmo endpoint não aceita: `reasoning_effort: none` no gpt-6-luna (sem isso o
    #: Chat Completions não chama ferramenta), `thinking: {type: disabled}` no deepseek-flash.
    extra_body: dict[str, object] | None = None


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
    #: Campos extras do corpo do POST `/chat/completions`, repassados TAL QUAL (item 7.8). É o que liga, por
    #: exemplo, `options: {num_ctx: 16384}` do Ollama — mas o TAMANHO DE CONTEXTO do servidor não é isto: fica em
    #: `OLLAMA_CONTEXT_LENGTH` (variável de ambiente do serviço) ou no `Modelfile` do modelo. Sem um dos dois no
    #: SERVIDOR, o contexto padrão dele (muitas vezes 2048–4096) trunca o prompt em silêncio — nenhum erro aqui,
    #: só uma resposta pior porque a árvore da tela não coube inteira.
    extra_body: dict[str, Any] | None = None


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
    # --- item 17.14: o que um perfil (17.7) precisa trocar para medir latência sem mexer no padrão. Vazio = como hoje.
    #: Esforço DESTA função (Anthropic: `output_config.effort`). Vazio = o do `.env` (AI_EFFORT_PLANNER/ACTOR/VERIFIER).
    effort: Effort | None = None
    #: `false` não manda `thinking` (o ator sem pensamento adaptativo, LT-10). Vazio ou `true` = a capacidade do modelo
    #: decide (`ai.models.<m>.thinking`), como hoje. Não liga thinking num modelo declarado sem ele.
    thinking: bool | None = None
    #: RA-17 (dieta do contexto 2): na decisão do ator, o bloco estável da etapa (passo e lições) vai ANTES da imagem e
    #: leva um 2º ponto de cache; a imagem e o resto da observação vêm depois. Vazio/`false` = imagem primeiro, um ponto
    #: de cache só no system (byte a byte o de hoje). Só a Anthropic tem ponto de cache.
    cache_da_etapa: bool | None = None


#: Prazo e vagas por função quando o YAML não diz. Folgados diante do medido em 908 chamadas reais
#: (máx.: plan 29,5 s · social 13,4 s · decide 12,2 s · verify 7,6 s) e MUITO abaixo dos 180 s globais de antes.
ROLE_DEFAULTS: dict[str, dict[str, Any]] = {
    "plan": {"timeout_s": 120.0, "concurrency": 4},
    "decide": {"timeout_s": 45.0, "concurrency": 8},
    "verify": {"timeout_s": 30.0, "concurrency": 8},
    "escalation": {"timeout_s": 60.0, "concurrency": 4},
    "social": {"timeout_s": 60.0, "concurrency": 4},
    # Mesmos números do social: sem `ai.roles.persona` a geração de persona se comporta como sempre.
    "persona": {"timeout_s": 60.0, "concurrency": 4},
    # Recorte de ~720x150 px e saída curta: a chamada mais barata do hub (item 12.5).
    "leitura": {"timeout_s": 30.0, "concurrency": 2},
}


class AiProfileCfg(BaseModel):
    """Perfil de IA nomeado (item 17.7): o que MUDA nas funções para as execuções que o escolhem.

    Cada função do perfil soma-se por cima de `ai.roles` campo a campo — um perfil que só troca o modelo do `decide`
    deixa o resto exatamente como o padrão. É o que torna o A/B honesto: a única diferença entre os braços é a que
    está escrita aqui, não a soma de tudo o que o perfil esqueceu de repetir."""

    roles: dict[str, RoleCfg] = {}
    note: str = ""                              # para que serve: só documentação do YAML (nada o exibe ainda)
    #: Item 17.14 (RA-17, perfil `img-768`): o lado maior da imagem e o mínimo de elementos da árvore "rica" das
    #: execuções DESTE perfil, por cima de `ai.screenshot_max_side` e `ai.rich_tree_min_elements`. Vazio = os globais.
    screenshot_max_side: int | None = Field(None, ge=320, le=2560)
    rich_tree_min_elements: int | None = Field(None, ge=0, le=200)
    #: Item 31.35 (parte B): quantas ações cada decisão do ator pode trazer nas execuções DESTE perfil, por cima de
    #: `ai.acoes_por_decisao`. É o que deixa o A/B rodar por execução, sem ligar o encadeamento para o parque inteiro.
    acoes_por_decisao: int | None = Field(None, ge=1, le=3)


class AiCanaryCfg(BaseModel):
    """Canário (item 17.7): uma fatia das execuções SEM perfil escolhido vai para `profile`. Sorteio por execução,
    registrado nela (`runs.ai_profile_source = 'canary'`) para a comparação separar o canário do pedido explícito."""

    profile: str | None = None                  # vazio = sem canário
    fraction: float = Field(0.0, ge=0.0, le=1.0)


class StepBudgetCfg(BaseModel):
    """Orçamento de chamadas de IA por ETAPA, medido no histórico da ação (item 18.3; execução e31953). Etapa de uma
    ação com pelo menos `min_samples` etapas concluídas nos últimos `window_days` dias para ao passar de
    `max(p90 × p90_factor, p90 + slack)` chamadas; sem base, vale só o teto por objetivo. Passar do p90 já vira
    aviso na linha do tempo."""

    enabled: bool = True
    p90_factor: float = Field(2.0, ge=1.0, le=10.0)
    slack: int = Field(4, ge=0, le=100)
    min_samples: int = Field(5, ge=1, le=1000)
    window_days: int = Field(30, ge=1, le=365)


class ImageCfg(BaseModel):
    """Imagens da persona (evolução 2, onda A). Porta própria, FORA dos papéis de IA de texto (`AI_ROLES`): o saldo da
    Anthropic não compra imagem, então é outro provedor, outra chave (`OPENAI_API_KEY`) e preço por imagem
    DECLARADO — a API de imagens não devolve custo. `simulated` (padrão) gera um degradê determinístico com Pillow,
    sem chave e sem nada sair da máquina; `openai` é `gpt-image-1-mini` por `/v1/images/generations`."""

    provider: Literal["simulated", "openai"] = "simulated"
    #: `gpt-image-2` desde a Fase 17: o `gpt-image-1-mini` sai da API em 01/12/2026 (página de descontinuações da
    #: OpenAI, aviso de 02/06/2026). No `gpt-image-2` a imagem de entrada é sempre de alta fidelidade — o rosto da
    #: principal se mantém nas variações sem `input_fidelity`, que ele ignora.
    model: str = "gpt-image-2"
    quality: Literal["low", "medium", "high"] = "medium"
    #: Quantas imagens gerar ao criar uma persona (`on_create`); 0 desliga a geração automática.
    per_persona: int = Field(1, ge=0, le=3)
    on_create: bool = True
    #: US$ por imagem por qualidade: ESTIMATIVA para conferir o teto do dia ANTES de gerar, e o custo registrado
    #: quando a resposta não traz `usage`. O `gpt-image-2` não publica preço por imagem (só por token); estes
    #: valores são conservadores até a medição real substituí-los (Fase 17, passo 2.6).
    price_per_image: dict[str, float] = {"low": 0.02, "medium": 0.06, "high": 0.2}
    #: US$ por MILHÃO de tokens (`text_in`, `image_in`, `output`), para o custo REAL a partir do `usage` que a API
    #: de imagens devolve (developers.openai.com/api/docs/pricing, 28/09/2026: gpt-image-2 = 5 / 8 / 30). Vazio =
    #: vale o `price_per_image` declarado, como antes.
    price_per_mtok: dict[str, float] = {"text_in": 5.0, "image_in": 8.0, "output": 30.0}
    timeout_s: float = Field(180.0, ge=1, le=900)


class AiLimitsCfg(BaseModel):
    """Fatias do teto de gasto DO DIA para usos fora de execução (item 31.6; hub-de-ia-fora-de-execucao.md §3).

    São PARTES do teto do dia (`ai_max_usd_per_day`, em Configuração › Limites), nunca somas por fora: a chamada
    precisa passar no teto do dia E na fatia da própria origem. Uma fatia estourada barra só aquela origem
    (`AIError.motivo` = `fatia_curador` ou `fatia_jev`); as demais seguem. `0` desliga a fatia."""

    #: Curador do Livro (`origem='curador'`). Sem valor, a fatia é `curador_fracao_do_dia × teto do dia`; sem teto
    #: do dia (`0`), não há fatia e nada muda. A fração é o α = 10 % decidido pelo dono (30.11).
    curador_max_usd_per_day: float | None = Field(None, ge=0, le=100_000)
    curador_fracao_do_dia: float = Field(0.10, ge=0, le=1)
    #: Decisão por conjunto fechado (Jev-retrieval, `origem='decisao_fechada'`). US$ 0,50/dia é a D-J3, aprovada pelo
    #: dono no ADR-069; vale para as chamadas da porta `DecisaoFechada` (31.4).
    jev_max_usd_per_day: float = Field(0.50, ge=0, le=100_000)
    #: Leitura visual (item 12.5, `origem='leitura'`): fatia opcional do dia para o leitor das saídas de etapa. `0`
    #: (padrão) = sem fatia: valem só os tetos do pedido, da execução e do dia, que a chamada sempre confere.
    leitura_max_usd_per_day: float = Field(0.0, ge=0, le=100_000)


class LeituraVisualCfg(BaseModel):
    """Leitura visual de uma saída de etapa (item 12.5, ADR-070). Desligada por padrão: ligar manda o RECORTE de uma linha
    de tela para o provedor de `ai.roles.leitura` (outro destino das capturas quando ele não é o do ator)."""

    enabled: bool = False


class DecisaoFechadaCfg(BaseModel):
    """Porta `DecisaoFechada` (Fase 31, ADR-069): decisão por conjunto fechado no Jev, DESLIGADA por padrão.

    O YAML só RESTRINGE o que sai: o envio e as classes que podem sair são constantes de código (`planning/decisao_fechada/
    privacidade.py`: `JEV_RUNTIME_SEND_APPROVED`, `JEV_ALLOWED_CLASSES`) e nenhuma chave aqui abre o que o código fechou.
    `enabled: false` vence tudo; sem consumidor listado, vale `off`. `on` só por consumidor e com GO pré-registrado.
    `decisor` (31.14) escolhe QUEM decide quando um pedido passa. Com o envio aberto no código (31.17), sai o que esta
    seção liga: `enabled`, o consumidor em `shadow` e `decisor: jev` (mais a chave); `nulo` nunca toca rede."""

    enabled: bool = False
    consumidores: dict[Literal["curador", "intencao", "desempate", "apps"], Literal["off", "shadow", "on"]] = {}
    #: Estreita o teto de código (interseção). None = não estreita além do código.
    classes_permitidas: list[Literal["C0", "C1", "C2", "C3"]] | None = None
    #: Quem decide (31.14): `nulo` (de fábrica: nunca decide, nunca toca rede) ou `jev` (o `DecisorJev`, pelo adaptador de
    #: retrieval, com o gasto conferido antes do POST e a chamada em `ai_calls`). Com `JEV_RUNTIME_SEND_APPROVED` (código)
    #: aberto desde o 31.17, `jev` com um consumidor em `shadow` e a chave configurada é envio de verdade.
    decisor: Literal["nulo", "jev"] = "nulo"
    #: Retenção das linhas da sombra (`decisao_fechada_sombra`, migração 074): dias inteiros; o agregado diário é calculado
    #: ANTES de purgar e fica. Prazo próprio porque `ai_calls` morre em `log_retention_days` e a sombra precisa de mais.
    retencao_dias: int = Field(180, ge=1, le=3650)


class PesquisaCfg(BaseModel):
    """Pesquisa externa da operação (prova30 A2, 31.158): UMA por operação, quando o assunto dela tem lacuna. DESLIGADA de
    fábrica: ligar faz o modelo do planejador usar a ferramenta de busca do PRÓPRIO provedor (Anthropic, server tool
    `web_search`), sem roteador nem proxy. A consulta nasce só do assunto e das fontes indicadas na operação, nunca de
    texto de persona nem de tela sensível. O resultado vai para a memória da operação (fonte, fato, confiança, frescor)."""

    enabled: bool = False
    #: A versão da ferramenta no provedor. A de 2025-03-05 é a chamada direta (as mais novas filtram por execução de código).
    ferramenta: str = "web_search_20250305"
    #: Buscas por pesquisa (`max_uses` da ferramenta). Cada busca é cobrada à parte (`preco_por_busca_usd`).
    max_buscas: int = Field(3, ge=1, le=10)
    #: US$ por busca (página de preços: US$ 10 por mil). Entra em `ai_calls.usd` numa linha própria (`model='web_search'`).
    preco_por_busca_usd: float = Field(0.01, ge=0)
    #: Teto de gasto da pesquisa por operação (tokens + buscas, somados das linhas de `origem='pesquisa'` e `ref` = operação).
    #: Atingido, não se pesquisa de novo naquela operação.
    teto_usd_por_operacao: float = Field(0.25, ge=0)
    #: Quanto tempo o fato pesquisado vale; vencido, o assunto volta a ser lacuna.
    frescor_h: float = Field(24.0, gt=0, le=24 * 30)
    max_fatos: int = Field(8, ge=1, le=20)
    #: 31.231: quantos fatos confirmados e frescos do Livro, do mesmo assunto, cobrem o pedido e dispensam a pesquisa
    #: paga (critério em `learning/domain/reaproveitamento_da_pesquisa.py`). 0 desliga o reaproveitamento.
    reaproveitar_min_fatos: int = Field(2, ge=0, le=20)
    #: 31.248: a operação sem assunto pesquisa com o da leitura do alvo (o recorte público da publicação, sem menção a
    #: conta nem endereço; `reaproveitamento_da_pesquisa.assunto_da_leitura`), agendada quando o 1º agente lê o alvo.
    #: `false` = sem assunto, sem pesquisa (o de antes).
    assunto_da_leitura: bool = True


class AiCfg(BaseModel):
    #: Gerador de imagem da persona. Não é papel: `_ia_coerente` não o conhece e o hub não o roteia.
    image: ImageCfg = ImageCfg()
    #: Pesquisa externa da operação (prova30 A2). Desligada de fábrica.
    pesquisa: PesquisaCfg = PesquisaCfg()
    #: Orçamento por etapa, medido no histórico da ação (item 18.3).
    step_budget: StepBudgetCfg = StepBudgetCfg()
    screenshot_max_side: int = 1280             # lado maior da imagem enviada ao modelo (tokens ∝ área)
    max_hierarchy_elements: int = 140           # linhas da hierarquia no prompt (priorizadas; a árvore completa fica local)
    # LT-4b (latência do planejador, 03/10): o plano custa ~6,6 ms por token de SAÍDA, e o esforço `low` só tirou 13 %.
    # `curto` tira do formato da etapa livre (plano livre e parte livre do plano entre apps) o que o backend sabe
    # preencher, e pede título e objetivo curtos. Saem `postcondition.description` (derivada do `value`: no
    # `model_judged`, o verificador lê o próprio critério), `precondition` e `max_attempts` (1 no efeito, 3 nas
    # demais). `timeout_s` fica, porque é o modelo que sabe qual etapa é lenta. Estimado com count_tokens em 2 planos
    # reais do QA (a brevidade simulada por corte): −22 a −24 % de saída. `longo` é o formato de sempre, byte a byte.
    esquema_do_plano: Literal["longo", "curto"] = "longo"
    # 31.30 (latência do planejador, 04/10): validade do cache do prefixo do PLANO da execução (system + esquema).
    # Comando de pessoa chega espaçado: com 5 min, 12 de 43 planos da execução gravaram o cache sem reler, e 5
    # deles teriam relido com 1 h (a mesma família de prefixo dentro da hora). Reler tira ~0,2 a 0,7 s do plano e
    # não muda o que o planejador decide (o prompt é o mesmo byte a byte). Custo: a gravação de 1 h custa 2x a
    # entrada base contra 1,25x a de 5 min (~US$ 0,01 a mais no prefixo de 6,7 mil tokens do plano entre apps),
    # pago de volta na primeira releitura. `5m` volta ao de antes. Só os três planejamentos da execução; o
    # curador e o treino seguem em 5 min.
    cache_ttl_do_plano: Literal["5m", "1h"] = "1h"
    # Item 7.6 (dieta do contexto do ator): histórico da tentativa que vai ao ator, comprimido sem chamar o
    # modelo — linhas REJEITADA/FALHOU/(executor) (sempre relevantes: dizem o que NÃO fazer de novo) mais as
    # últimas N em ordem. O verificador continua recebendo o histórico completo que o executor lhe passa.
    actor_history_lines: int = 6
    image_policy: Literal["always", "auto", "never"] = "always"
    # auto: só hierarquia quando a árvore é rica; imagem na 1ª decisão de etapa julgada por visão, em árvore pobre,
    # após erro/ciclo, ou quando o modelo pede (observe_screen.need_image)
    rich_tree_min_elements: int = 8
    # Item 31.36: a etapa marcada `opcional` pelo planejador (só limpa a tela) roda com no máximo 3 decisões, sem
    # juiz e sem escalar, e falhar a deixa `skipped` sem derrubar o objetivo. `false` = a etapa de sempre.
    limpeza_opcional: bool = True
    #: Item 31.38: teto de decisões do ator por tentativa numa etapa de LEITURA (declara saídas, sem efeito). Medido no
    #: central em 04/10: as 16 tentativas de leitura que fecharam com sucesso desde 27/09 gastaram p50 3, p95 8, máx. 8
    #: decisões; 12 é o p95 com 50 % de folga. Estourado, vale o desfecho de `dado_ausente`. 0 desliga.
    max_decisoes_leitura: int = Field(12, ge=0, le=60)
    #: Item 31.40: a recusa do juiz por SOBREPOSIÇÃO (diálogo, banner, cookies cobrindo o alvo) numa etapa sem efeito
    #: não repete a etapa: a recuperação insere uma limpeza opcional (31.36) antes dela, uma vez por objetivo.
    limpeza_apos_sobreposicao: bool = True
    #: Item 31.72: hosts em que o ATOR pode aceitar um aviso de consentimento (cookies, privacidade). Vazia, nenhum: o
    #: toque de aceite é recusado antes de chegar ao aparelho. Preencher é decisão do dono, não de frente.
    consentimento_aceito_em: list[str] = Field(default_factory=list)

    @field_validator("consentimento_aceito_em")
    @classmethod
    def _so_hosts(cls, hosts: list[str]) -> list[str]:
        """31.72 (N6 da leitura): `*.loja.com`, `https://loja.com` e `loja.com/x` nunca casariam com o host da barra, em
        silêncio. Recusa na carga, com o nome da chave; um host já vale para os subdomínios."""
        limpos = [h.strip().casefold() for h in hosts]
        ruins = [h for h in limpos if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", h)]
        if ruins:
            raise ValueError(f"ai.consentimento_aceito_em aceita só host (loja.com.br), sem esquema, caminho nem "
                             f"curinga; os subdomínios já valem: {ruins}")
        # Releitura do #386: como os subdomínios valem, um sufixo público ("com.br", "gov.br") liberaria o aceite em
        # todos os sites dele. Lista curta e fechada (sem baixar a Public Suffix List): o que esta instalação usa.
        sufixos = [h for h in limpos if h in _SUFIXOS_PUBLICOS]
        if sufixos:
            raise ValueError(f"ai.consentimento_aceito_em não aceita sufixo público, só o host de um site: {sufixos}")
        return limpos

    #: Item 31.41: o valor lido só é gravado com evidência de RELAÇÃO com o nome pedido (seletor do catálogo, rótulo
    #: vizinho ou forma fechada; da imagem, um "sim" do verificador). Dúvida recusa a leitura (`leitura.sem_relacao`).
    relacao_do_valor: bool = True
    # Item 31.35: tira da árvore QUE VAI AO ATOR a barra do navegador (endereço, abas, menu); a árvore local fica
    # completa para seletores, guardas e pós-condições. `false` volta ao prompt de antes, sem reinício de código.
    podar_ui_do_navegador: bool = True
    # Item 31.52, diagnóstico DESLIGADO por padrão (lista vazia): os aparelhos DE TESTE em que o executor grava, como
    # evidência, a árvore do navegador antes da poda acima, para o A/B offline (`scripts/poda-ab-offline.py`). Nunca um
    # aparelho de conta real; tela sensível nunca é gravada.
    diagnostico_arvore_aparelhos: list[str] = Field(default_factory=list)
    # Item 31.54, LIGADO por padrão desde o A/B ao vivo do 31.56 (05/10, android-09, deploy 33, sites públicos): sucesso
    # 3/3 nos dois braços e 16 decisões contra 14 (+14,3 %, dentro do teto de 20 %). Tapa com um retângulo opaco a barra
    # de endereço do Chrome (bounds da `url_bar` na árvore) na imagem que vai ao ator e ao juiz; o texto limpo da barra
    # (31.52) segue na árvore. `false` volta a mandar a imagem como está.
    tapar_barra_de_endereco: bool = True
    # Item 31.71, DESLIGADO por padrão (vira padrão só com o A/B): numa etapa que entrega valor (`saidas`), a imagem vai
    # em TODA decisão enquanto faltar saída declarada (motivo `leitura_pendente`), com um lembrete só com os NOMES do
    # que falta. Achado da r-…-2e0775: sem imagem, o ator tentava concluir ou pedia `observe_screen` (4 de 8 decides).
    imagem_enquanto_falta_saida: bool = False
    # Item 31.35 (parte B), DESLIGADO por padrão: quantas ações o ator pode mandar numa decisão de etapa SEM efeito
    # (chamadas paralelas de ferramenta; o executor confere o alvo de cada uma na tela nova). 1 = uma só, como sempre.
    # Liga só depois do A/B com teto (sucesso igual e >= 30 % menos decisões).
    acoes_por_decisao: int = Field(1, ge=1, le=3)
    # Item 31.35 (parte B), DESLIGADO por padrão: depois de um `open_url` concluído, o executor espera a tela parar
    # (a espera adaptativa do 31.27) em vez de gastar uma decisão do ator num `wait_for`.
    espera_apos_open_url: bool = False
    # Quando a etapa com efeito externo decide no modelo de escalonamento: `true` = sempre (era o único modo: em
    # 19-23/09, 39 % das decisões foram ao Opus, inclusive curtir com seletor de commit declarado); `false` = nunca
    # por efeito; `by_risk` = só risco alto do catálogo, risco médio SEM seletor de commit, ou app sem catálogo
    # (exceto o app de prova, `builtin` e `apps.category='qa'`, na etapa sem capability: item 29.31).
    # Retentativa, erros seguidos e ciclo continuam escalando em qualquer modo. Receita divergida NÃO escala sozinha
    # (conferido em 26/09, frente F3: a fórmula do tier nunca leu a divergência): a IA assume a etapa no modelo de
    # ação e só sobe pelos controles acima. Escalar na divergência é decisão do dono pendente, com o custo medido
    # em `relatorio-desempenho.md` (22 etapas `recipe+ai` em 7 dias).
    strong_model_for_side_effect: bool | Literal["by_risk"] = "by_risk"
    # 31.223: quando a etapa com efeito sobe ao modelo forte (acima), ele decide SÓ o commit. A etapa começa no modelo de
    # ação (abrir o campo, digitar, focar), e a primeira decisão que dispararia o efeito (`is_commit_action`, o seletor
    # ou o verbo) é descartada e refeita no modelo forte, que segue até o fim da tentativa (como o LT-12). Medido na
    # onda 1 (07/10): o Opus decidia os 2 passos do comentário (US$ 0,112 de 0,279 do alvo). A trava de commit, a
    # política de risco e o rejulgamento do "sim" com efeito não mudam. `false` = a etapa inteira no forte (o de antes).
    strong_model_only_on_commit: bool = True
    # 31.237: quanto o plano de um alvo de operação espera o 1º plano da MESMA operação, para ler o prefixo do prompt do
    # cache em vez de gravá-lo de novo (onda 2, 07/10: 3 planos paralelos, 3 gravações, US$ 0,018 contra 0,0113). Vencido
    # o teto, segue como antes. 0 desliga.
    espera_do_plano_irmao_s: float = Field(60.0, ge=0, le=600)
    # 31.232: o modelo forte que confere o efeito (a decisão do commit refeita, 31.223, e o rejulgamento do "sim" com
    # efeito, 17.10) recebe a imagem quando o alvo do efeito NÃO está na árvore (toque por coordenada, elemento ausente
    # ou ferramenta sem elemento). Com o alvo na árvore, a régua de sempre decide (na onda 1, sem imagem). Só acrescenta
    # a imagem; nunca tira a que outra causa manda. `false` = como antes.
    imagem_quando_alvo_fora_da_arvore: bool = True
    # Item 17.10 (cascata para ator barato). `step_blocked` do tier 0 (kinds que um modelo mais forte ainda pode resolver:
    # tela inesperada, informação faltando, app incompatível, outro) sobe UMA vez ao tier 1 na mesma tela antes de pedir uma
    # pessoa. `challenge`, `auth_required` e `wrong_account` NUNCA sobem: dependem de pessoa ou do autenticador.
    cascade_blocked_to_tier1: bool = True
    # Item 17.10 (como o B14/7.10, mas para o "sim"): em etapa com EFEITO externo, o "sim" do verificador barato é rejulgado
    # UMA vez pelo modelo de escalonamento, e quem vale é o mais forte (discordou → não conta como prova). Só age quando o
    # modelo do verificador é DIFERENTE do de escalonamento (senão seria a mesma pergunta ao mesmo modelo).
    rejudge_yes_on_side_effect: bool = True
    # 31.238: o rejulgamento do "sim" com efeito é dispensado quando a PROVA LOCAL do app já comprovou o efeito na árvore
    # (o marcador do catálogo, 31.57, ou o `sent_text`, 31.26) E o app ganhou o direito: pelo menos
    # `rejulgamento_dispensa_minimo` rejulgamentos `sim_com_efeito` nos últimos `rejulgamento_dispensa_janela_dias`, sem
    # nenhuma discordância. Medido em 07/10: 0 discordâncias em 119 (QA Messenger qualifica; Instagram, com 4, não).
    # O app que deixa de ter o mínimo na janela volta a ser rejulgado e recupera o direito sozinho. `false` desliga.
    rejulgamento_dispensado_por_app: bool = True
    # 31.239: na etapa cuja ação declara a prova local `comentario:` (CREATE_COMMENT do Instagram), o comentário desta
    # execução visível na lista, atribuído à conta conectada, dispensa o PRIMEIRO julgamento (o barato). O rejulgamento
    # do 17.10 continua e decide, salvo o direito do app (31.238). Sem rejulgamento, a prova não fecha nada sozinha.
    comentario_dispensa_primeiro_juiz: bool = True
    rejulgamento_dispensa_minimo: int = Field(30, ge=1, le=10_000)
    rejulgamento_dispensa_janela_dias: int = Field(7, ge=1, le=90)
    # Item 31.26 (opção A): na etapa com nível de entrega `sent` cuja ação declara a prova local `sent_text` (a SEND_MESSAGE
    # do Instagram: o texto numa mensagem do fio e fora do campo), essa prova substitui o PRIMEIRO julgamento (o barato).
    # O rejulgamento do "sim" com efeito (17.10) continua e é quem vale; sem ele (desligado ou mesmo modelo), nada muda.
    sent_text_dispensa_primeiro_juiz: bool = True
    # Item 31.57: o marcador de entrega declarado no catálogo (`delivery_marks`), casado logo abaixo da mensagem desta
    # execução, dispensa o PRIMEIRO julgamento quando atende ao nível exigido. Mesmas travas do 31.26: o rejulgamento do
    # 17.10 continua e decide. Sem marcador declarado (hoje, nenhum app), nada muda.
    marcador_de_entrega_dispensa_primeiro_juiz: bool = True
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
    # 31.27: a espera ANTES do primeiro julgamento acompanha a tela. Sai quando a árvore fica igual por este tempo, sem
    # marca pendente do catálogo, e nunca passa de `judge_wait_s`. 0 volta à espera fixa. Medido em 04/10: as 6 esperas
    # fixas de 1,5 s das etapas com nível de entrega somaram 9 s em 149 s de parede.
    judge_wait_estavel_s: float = Field(0.6, ge=0, le=30)
    recipe_settle_s: float = Field(1.0, ge=0, le=10)
    # T.2 (achado #164): o orçamento de `_verify` eram três literais (8 / 15 / 60 s) no meio do laço, e o teste do
    # resultado ambíguo pagava os 60 s INTEIROS em tempo real para provar "sem a mensagem na tela até o prazo →
    # incerto". Agora é configuração, com o valor de hoje como padrão. `verify_budget_s` vale quando a verificação
    # é "normal"; `verify_budget_patient_s`, quando há nível de entrega exigido ou o efeito já foi disparado (o app
    # leva segundos para sair de "enviando"); `verify_budget_min_s` é o piso quando a etapa já está perto do prazo.
    verify_budget_s: float = Field(15.0, ge=0.5, le=300)
    verify_budget_patient_s: float = Field(60.0, ge=0.5, le=600)
    verify_budget_min_s: float = Field(8.0, ge=0.5, le=60)
    # Receitas: off = só IA · shadow = aprende e compara com a IA, sem agir · replay = repete sem IA, IA só se divergir
    recipes: Literal["off", "shadow", "replay"] = "off"
    # Receita que a IA aprende nasce CANDIDATA: a IA segue decidindo a etapa e a receita só é comparada (sombra), ao
    # mesmo custo de uma etapa sem receita. Depois de N execuções SEGUIDAS da etapa em que a IA fez exatamente o
    # caminho dela, vira ativa e passa a agir; uma divergência recomeça a contagem. Uma execução limpa só é um
    # caminho visto uma vez — aprender só com prova (pedido do dono). 0 = sem prova: nasce ativa (o modo anterior).
    recipes_promote_after: int = Field(1, ge=0, le=20)
    # 31.287: a prova vale POR EFEITO. `recipes_promote_after` é a de uma etapa SEM ação de efeito externo (ler, abrir, buscar:
    # 1 concordância basta, o erro é barato e a quarentena o pega). `recipes_promote_after_com_efeito` é a da etapa com
    # `commit` (seguir, curtir, comentar, enviar): 2 concordâncias, e mesmo assim para em `validated` e espera o dono
    # (D1 do ADR-054). Só vale com a prova ligada (`recipes_promote_after > 0`).
    recipes_promote_after_com_efeito: int = Field(2, ge=1, le=20)
    # 31.233: a receita ATIVA que divergiu e caiu em quarentena nesta tentativa, com a IA completando a etapa: o caminho
    # que de fato rodou (o trecho da receita e o da IA) vira candidata, em prova como qualquer outra. Medido na onda 2
    # (07/10): a 111 divergiu nos 3 alvos, 39 % do custo, e nada se aprendeu. `false` = como antes.
    candidata_da_ativa_que_divergiu: bool = True
    # RA-20: a etapa sem receita na chave atual herda, como CANDIDATA (em prova, nunca agindo), a receita provada da
    # mesma etapa noutra versão do app, noutra variante ou na legada. false = só mede a causa do "ausente". Só vale
    # com a prova (`recipes_promote_after > 0`): sem ela a receita aprendida já nasce ativa e não há o que herdar.
    recipes_heranca: bool = True
    # 31.273 (ADR-084): a etapa que a IA descobriu numa exploração e a receita comprovou é oferecida ao planejador pelo
    # nome (`planning/etapas_ensinadas`, bloco `etapas_descobertas`). A receita sem uso há tantos dias sai da oferta,
    # sem ser apagada; 0 = sem piso. Lido ao vivo (PUT /api/settings).
    descobertas_sem_uso_dias: int = Field(90, ge=0, le=3650)
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
        # Sonnet 5.5 (página de preços, 03/10/2026): o mesmo do Sonnet 5. Antes saía certo só por casar o prefixo
        # "claude-sonnet-5" em `price_for`; declarado, não depende disso (17.13, perfil planejador-sonnet).
        "claude-sonnet-5-5": [2.0, 0.2, 2.5, 10.0],
        "claude-haiku-4-5": [1.0, 0.1, 1.25, 5.0],
        # Destino documentado do fallback de recusa (achado #92): custava o mesmo do Opus 5 e não estava cadastrado,
        # então toda chamada que caísse nele virava "Total parcial" no painel de uso.
        "claude-opus-4-8": [5.0, 0.5, 6.25, 25.0],
        # Jev (TypeSafe System One, ADR-069): US$ 0,042 por milhão de tokens de entrada, saída grátis, sem cache. A chamada
        # grava o `usd` declarado (costs soma a coluna quando existe); a entrada aqui evita que o modelo, não cadastrado,
        # pague o preço MAIS CARO da tabela.
        "jev-1.13.0": [0.042, 0.0, 0.0, 0.0],
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
        # Sonnet 5.5: 512 (doc de prompt caching, 03/10/2026). Sem a linha, herdava os 1024 do Sonnet 5 pelo prefixo.
        "claude-sonnet-5-5": ModelCaps(min_cache_tokens=512),
        "claude-opus-4-8": ModelCaps(min_cache_tokens=1024),
        "claude-haiku-4-5": ModelCaps(thinking=False, effort=False, min_cache_tokens=4096),
    }
    #: Endpoints disponíveis. Vazio = só o provedor do `.env`, como sempre foi.
    providers: dict[str, ProviderCfg] = {}
    #: Página de faturamento de cada conta (ADR-051), por cima do padrão de `planning/saldos.py::CONTAS`. É a que o
    #: painel linka ("Abrir console"). Serve para fixar a conta de faturamento do AI Studio
    #: (`https://aistudio.google.com/billing?billing=<ID>`): sem o parâmetro ele abre a primeira conta da lista.
    balance_consoles: dict[str, str] = {}
    #: Provedor/modelo/prazo por FUNÇÃO. Vazio = tudo herdado do `.env` (nada muda).
    roles: dict[str, RoleCfg] = {}
    #: Perfis de IA por execução (item 17.7): `RunCreate.ai_profile` escolhe um; sem escolha vale `roles`. Trocar o
    #: modelo de UMA execução não exige reiniciar o central nem mexer no que as outras usam.
    profiles: dict[str, AiProfileCfg] = {}
    canary: AiCanaryCfg = AiCanaryCfg()
    #: Fatias do teto do dia por origem de chamada (item 31.6). Vazio = os padrões da classe.
    limits: AiLimitsCfg = AiLimitsCfg()
    decisao_fechada: DecisaoFechadaCfg = DecisaoFechadaCfg()
    #: Item 12.5 (ADR-070): a saída de etapa lida da imagem, conferida às cegas pelo papel `leitura`. Desligada.
    leitura_visual: LeituraVisualCfg = LeituraVisualCfg()


class AjustesDeSessaoCfg(BaseModel):
    """Ajustes do motor de sessão de UM app, por cima do `sessao.yaml` dele (ADR-052). Só vale o que for ESCRITO (no
    arquivo ou por atribuição); o resto vem do dado do app. Os limites existem para não bloquear a própria conta; as
    faixas são as de sempre (o antigo bloco `instagram:`), e os valores padrão e o porquê de cada um estão no
    `sessao.yaml` do app."""

    max_auth_attempts: int | None = Field(None, ge=1, le=10)        # falhas antes do envio até o intervalo
    auth_cooldown_s: int | None = Field(None, ge=0, le=86400)       # intervalo mínimo entre tentativas do perfil
    max_logins_per_day: int | None = Field(None, ge=1, le=10)       # envios de senha por conta em 24 h (ADR-055)
    open_timeout_s: float | None = Field(None, ge=5, le=300)        # teto para o app chegar ao primeiro plano
    settle_s: float | None = Field(None, ge=0.5, le=30)             # espera depois de o app aparecer
    submit_wait_s: float | None = Field(None, ge=5, le=120)         # quanto observar depois de Entrar (nunca reenvia)
    verify_timeout_s: float | None = Field(None, ge=5, le=300)


#: Os ajustes do motor de sessão que a instalação pode sobrescrever (`Config.ajustes_de_sessao`).
AJUSTES_DE_SESSAO = tuple(AjustesDeSessaoCfg.model_fields)


class ContasCfg(BaseModel):
    """Contas gerenciadas: as de qualquer app com login declarado (`sessao.yaml`, ADR-052, fatia 4)."""

    # Validade do "Conectado". Passado esse tempo a sessão é RELIDA do aparelho antes da tarefa (sem tentar
    # autenticar). Sem validade, o cache nunca expirava: havia perfis `session_ready` verificados três dias
    # antes, um deles de uma conta que o dono já tinha relatado presa num desafio. 0 desliga a reverificação.
    session_max_age_s: int = Field(43_200, ge=0, le=2_592_000)      # 12 h
    # 31.302: a releitura PERIÓDICA e leve (`observe_only`, sem IA, sem digitar) da conta âncora de cada persona com
    # sessão `session_ready` num aparelho ligado. A validade acima só reverifica ANTES de uma tarefa; uma conta que ninguém
    # usa fica "pronta" para sempre mesmo que o app já mostre o desafio. Em horas; 0 DESLIGA (padrão: o dev decide).
    verificacao_periodica_h: int = Field(0, ge=0, le=720)
    # Com a CPU do host acima disto (a última batida), a volta não toca em aparelho: o funil e a suíte pesam aí.
    verificacao_periodica_cpu_max_percent: float = Field(50.0, ge=1.0, le=100.0)
    verificacao_periodica_tique_s: int = Field(300, ge=30, le=3600)  # de quanto em quanto tempo o laço olha (uma conta por volta)
    #: 31.335 (decisão do dono, 10/10/2026): a API do igfarm deixou de ser a fonte da conta (cria `is_active:false` e o login
    #: para em 2FA sem contexto); o cadastro é NO APP e o igfarm é apoio (e-mail, código, SMS, proxy). Com `false` (padrão),
    #: `GET /api/instagram/personas-pendentes` recusa com 409 `criacao_pela_api_aposentada` e não reserva, não sugere nem gera foto
    #: (paga). Registro (`POST /instagram/contas`), consentimento, código, ciclo e proxy seguem valendo. Reversível: nada se apaga.
    criacao_pela_api_do_igfarm: bool = False
    #: pacote → ajustes do motor de sessão daquele app (`contas.sessao.<pacote>.settle_s: 5`).
    sessao: dict[str, AjustesDeSessaoCfg] = {}

    def ajustes(self, package: str) -> AjustesDeSessaoCfg:
        """Os ajustes escritos para `package`, criando o registro vazio: é o que permite ajustar em código
        (`cfg.file.contas.ajustes(pacote).settle_s = 5`), como a instalação faz no arquivo."""
        return self.sessao.setdefault(package, AjustesDeSessaoCfg())


#: Blocos do `config.yaml` que saíram, com para onde foram. Um bloco antigo esquecido no arquivo seria IGNORADO em
#: silêncio (o modelo aceita chave desconhecida) e a instalação perderia o ajuste sem saber; aqui ela não sobe e diz
#: o que fazer.
BLOCOS_QUE_SAIRAM: dict[str, str] = {
    "instagram": "ADR-052: a validade da sessão vai em `contas.session_max_age_s`, e os ajustes do login em "
                 "`contas.sessao.<pacote>` (ex.: `contas.sessao.<pacote>.settle_s: 5`)",
}


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


class ProvisioningCfg(BaseModel):
    """Provisionamento de aparelho pela plataforma, no servidor local (`POST /api/instances`, migração 050).

    O AVD nasce em `paths.avd_home` e cresce até `android.data_partition` mais a imagem: criar um sem disco para ele
    subir é falha adiada para o primeiro boot. O piso é conferido contra o disco livre da última medição do
    hospedeiro (a batida do worker local, que mede `paths.data_dir`).
    """

    min_free_disk_gb: float = Field(10, ge=0, le=100_000)


def _ip_ou_none(texto: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address((texto or "").strip())
    except ValueError:
        return None


class RedeServidorCfg(BaseModel):
    """O servidor sing-box do central (ADR-056, item 25.4): processo do usuário gerenciado pela plataforma — nunca
    serviço do Windows, sem driver, sem NAT e sem mexer na rede ou no firewall do sistema (decisão do dono P2).

    Só sobe quando algum aparelho pede um perfil de VPN com `params.servidor: "central"`, e para quando ninguém
    pede. A configuração (com a chave do servidor e as senhas do proxy) vai para um arquivo com ACL só do usuário em
    `paths.data_dir/rede/servidor/`; o argumento do processo leva o caminho, nunca o conteúdo.
    """

    # O executável do sing-box oficial. Relativo à raiz do projeto, como os outros caminhos; fora do Git.
    binario: str = "data/rede/sing-box-1.14.2-windows-amd64/sing-box.exe"
    porta_wireguard: int = Field(51820, ge=1, le=65535)
    # A sub-rede do túnel: o servidor fica no primeiro endereço (10.66.0.1), cada aparelho ganha o seu a partir do
    # segundo. Mudar depois de haver aparelho atribuído troca o endereço de quem ainda não tem chave, só.
    sub_rede: str = "10.66.0.0/24"
    # O proxy autenticado do central (inbound `mixed`), alcançado pelo túnel em <servidor>:<porta>. Escuta só em
    # 127.0.0.1: fora do túnel, ninguém chega nele.
    porta_proxy: int = Field(18080, ge=1, le=65535)
    mtu: int = Field(1408, ge=1280, le=1500)
    # Teto do log de conexões (a evidência de que o par conectou). Passado o teto, o próximo início o renomeia.
    log_max_mb: float = Field(20, ge=1, le=1024)
    # O endereço deste central NA LAN, como um aparelho de OUTRA máquina (o notebook do worker, um celular) o alcança
    # (item 25.7). O emulador local chega pelo 10.0.2.2 do perfil; o do notebook, não: lá o 10.0.2.2 é o próprio
    # notebook. Vazio = aparelho remoto não recebe a VPN do central (a aplicação recusa dizendo esta chave). O UDP da
    # porta_wireguard precisa passar pelo Firewall do Windows daqui — a plataforma só LÊ o firewall e mostra o comando
    # que o dono roda (GET /api/network/server → remote_access).
    endpoint_lan: str = ""

    @field_validator("endpoint_lan")
    @classmethod
    def _endpoint_lan(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            return ""
        v = host_valido(v)
        ip = _ip_ou_none(v)
        if ip is not None and (ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local):
            raise ValueError(f"rede.servidor.endpoint_lan: {v} não é alcançável de outra máquina")
        if v == "10.0.2.2" or v.casefold() == "localhost":
            # 10.0.2.2 é o host visto de dentro do emulador LOCAL; no notebook ele leva ao próprio notebook.
            raise ValueError(f"rede.servidor.endpoint_lan: {v} é o endereço do host visto pelo emulador local, não "
                             "o do central na LAN")
        return v

    @model_validator(mode="after")
    def _endpoint_fora_do_tunel(self) -> RedeServidorCfg:
        ip = _ip_ou_none(self.endpoint_lan)
        if ip is not None and ip in ipaddress.ip_network(self.sub_rede, strict=False):
            raise ValueError(f"rede.servidor.endpoint_lan: {self.endpoint_lan} está dentro da sub-rede do túnel "
                             f"({self.sub_rede}); o aparelho precisa alcançá-lo ANTES do túnel existir")
        return self


class RedeSondaCfg(BaseModel):
    """A sonda de saída (ADR-056, item 25.5): o que ela pergunta de dentro do aparelho, como o uid 2000.

    Os ecos de IP respondem o IP de quem pediu em HTTP/1.0 na porta 80 (o `nc` do Android não tem TLS). A família
    vem do host: o de IPv4 só tem registro A, o de IPv6 só AAAA. Tenta-se o primeiro e, sem IP, os seguintes.
    """

    hosts_ipv4: list[str] = Field(default_factory=lambda: ["api.ipify.org", "ipv4.icanhazip.com"], min_length=1)
    hosts_ipv6: list[str] = Field(default_factory=lambda: ["api6.ipify.org", "ipv6.icanhazip.com"], min_length=1)
    # UDP de ida e volta: DNS a um resolvedor explícito (o cliente o sequestra e resolve pelo túnel) e NTP, que é o
    # UDP que NÃO é DNS (na cadeia com SOCKS5 do 25.1 o NTP se perdia e o DNS seguia).
    udp_dns: str = "8.8.4.4"
    udp_ntp: str = "time.google.com"
    # Um aparelho `parcial` (app sem tráfego na janela, IP sem app) é medido de novo depois disto, na varredura ou
    # pela porta da tarefa; a janela da cobertura por app vai da medição anterior até a nova.
    reverificar_s: float = Field(600, ge=30, le=86_400)
    # Abrir o app exigido (tela inicial dele, espera e volta ao início) quando ele não teve tráfego na janela, TAMBÉM
    # sem tarefa esperando (varredura, ligou, pedido). Com uma tarefa segurada pela porta da rede, a sonda abre de
    # qualquer jeito: a tarefa é o que faria o app usar a rede, e sem isso a política exigida travaria para sempre no
    # app nunca aberto. Desligado por padrão: aparelho com app exigido é aparelho com conta real vinculada, e abrir o
    # app sem ninguém ter pedido trabalho nele é usá-la por conta própria (ADR-056 §7, K-057).
    abrir_apps: bool = False
    espera_app_s: int = Field(15, ge=3, le=120)
    # A saída do PRÓPRIO central (item 29.20): com os mesmos ecos, pelo socket do backend, para a plataforma acusar o
    # aparelho cuja saída medida é igual a ela (= sai pela rede da casa). `central_ttl_s` é a idade em que se mede de
    # novo (a varredura de 60 s só pergunta); uma medida com mais de 3 vezes isso deixa de valer ("não medida", nunca
    # "limpo"). `medir_central: false` desliga (o harness de testes não abre socket); `central_prazo_s` é o prazo por
    # host e família.
    medir_central: bool = True
    central_ttl_s: float = Field(600, ge=60, le=86_400)
    central_prazo_s: float = Field(6, ge=1, le=30)
    # O aparelho SEM rede pedida (nem linha em `device_network`) também é medido, só nos IPs v4/v6 (a mesma sonda do
    # uid 2000, sem app, DNS, UDP nem vazamento), ligado e livre, a cada `reverificar_s`: sem perfil, a saída é a da casa
    # (presumida) até a medida dizer o contrário. `false` desliga (o harness de testes não fala com adb).
    medir_sem_rede: bool = True
    # O prazo de cada leitura da rede pelo adb (observação, estado da interface, janela do start), item 29.75. Era 45 s
    # fixos no código, com a fila do aparelho dando +10 ("rede do aparelho excedeu 55s"): 6 medições ao ligar falharam
    # por prazo em 7 dias (03, 05, 06 e 09, host disputado por boot e suíte), e o aparelho ficou sem prova pós-boot até a
    # varredura seguinte. 90 s cobre o boot sob carga; a leitura normal leva poucos segundos.
    prazo_leitura_s: float = Field(90, ge=10, le=600)

    @field_validator("hosts_ipv4", "hosts_ipv6")
    @classmethod
    def _hosts(cls, v: list[str]) -> list[str]:
        return [host_valido(h) for h in v]

    @field_validator("udp_dns", "udp_ntp")
    @classmethod
    def _host(cls, v: str) -> str:
        return host_valido(v)


class RedeCfg(BaseModel):
    """Aplicação e convergência da rede por aparelho (ADR-056, item 25.4), com o cliente escolhido pela medição 25.1."""

    # O cliente VPN no aparelho: sing-box (SFA), instalado pelo fluxo de releases (a versão promovida na loja).
    cliente_pacote: str = "io.nekohasekai.sfa"
    # De quanto em quanto tempo um aparelho com a rede aplicada tem a configuração relida (deriva). A releitura é
    # leitura por adb (settings, tun0, dumpsys), sem reinício; 0 desliga a conferência periódica.
    deriva_s: float = Field(900, ge=0, le=86_400)
    # Validade de um `trafego_verificado` para a porta da tarefa (item 25.6): passado isto desde a última medição
    # (`verified_at`), a verificação conta como inválida — com política exigida a tarefa espera a sonda medir de novo.
    # A deriva (acima) só relê configuração e túnel; o que ela não vê é a SAÍDA mudar com o túnel no ar (o IP público
    # do servidor trocado pelo provedor, um app que passou a sair por fora). 6 h: a remedição é barata (sem abrir app,
    # o teste de vazamento vale para a revisão) e a varredura a antecipa com o aparelho livre, então quase nunca é
    # a tarefa que espera por ela. Sem 0: uma verificação que nunca vence seria o "confia" que o ADR-056 recusa.
    validade_verificacao_s: float = Field(21_600, ge=300, le=604_800)
    # A atividade principal do cliente VPN (`pacote/.Classe`): é pelo botão Start DELA que a plataforma religa o túnel sem
    # reiniciar o aparelho quando o cliente não sobe no boot ou fica parado depois do teste de vazamento (W8, 01/10/2026:
    # só o Start da interface recalcula o `serviceMode` do SFA 1.14.2; o tile inicia o ProxyService num cliente que só
    # importou o perfil). Vazio desliga o gesto (volta a valer só o reinício).
    cliente_atividade: str = "io.nekohasekai.sfa/.compose.MainActivity"
    # O tile de configurações rápidas do cliente (`pacote/.Classe`). LEGADO: a convergência NÃO o usa mais (não recalcula o
    # `serviceMode`); fica como dado do diagnóstico (scripts/diag-w8-tile.py) e do aparelho cujo modo se provou VPN.
    cliente_tile: str = "io.nekohasekai.sfa/.bg.TileService"
    # Quanto esperar o `tun0` depois do boot, contado do boot. Medido em 30/09 (android-05, 7 boots com o host sob
    # carga): quando sobe, o túnel aparece entre 92 e 176 s de ligado (a segunda chance é o receptor de boot do
    # próprio cliente), e nunca depois de 180 s. Com 60 s, a conferência lia "não subiu" aos 95–159 s e pedia outro
    # reinício por um túnel que ainda ia subir.
    espera_tun_s: float = Field(180, ge=5, le=600)
    # Reinícios pedidos por revisão antes de desistir. Em 30/09 a primeira tentativa do always-on falhou em 5 de 7
    # boots (ANR de início do serviço com o convidado sem CPU); antes do reinício vem o Start da interface (`cliente_atividade`).
    reinicios_max: int = Field(2, ge=1, le=10)
    # O DNS que o cliente usa pelo túnel (o `hijack-dns` do sing-box resolve por ele).
    dns: str = "1.1.1.1"
    servidor: RedeServidorCfg = RedeServidorCfg()
    sonda: RedeSondaCfg = RedeSondaCfg()


class SkillsCfg(BaseModel):
    """Habilidades versionadas (docs/design/evolucao-arquitetural.md, decisão P1).

    `enabled` liga a resolução por habilidade PUBLICADA antes do fluxo legado. Interruptor próprio, desligado por
    padrão, em vez de reaproveitar `ai.flows`: aquele já quer dizer "fluxo legado", e o valor de cada instalação não
    foi lido. Com os dois desligados, nada muda; publicar uma habilidade não liga nada sozinho.
    """

    enabled: bool = False
    #: 31.91 F1 (ADR-077): a TELA do ensino v2 (a revisão só para leitura e o "Corrigir etapa", que abre um ensino de
    #: habilidade). Desligada por padrão e reversível: nenhum dado se apaga, a API e o resolvedor seguem como camada
    #: interna do Modo treinamento. Só aparece com `enabled` também ligado; a conversão de um fluxo em habilidade
    #: ("Gerar habilidade deste fluxo") não depende desta chave.
    ensino_v2_na_tela: bool = False


class LicoesDoPapelCfg(BaseModel):
    """Teto das lições no prompt de um papel (ator ou planejador; o verificador nunca recebe lição, ADR-024)."""

    tokens: int = Field(120, ge=0, le=2000)
    max: int = Field(3, ge=0, le=20)


_PACOTE_DO_APRENDIZADO = re.compile(r"^[A-Za-z][\w]*(\.[A-Za-z][\w]*)+$")


def _conferir_pacotes_por_app(por_app: dict[str, str]) -> dict[str, str]:
    """As chaves de `por_app` são pacotes Android (o mesmo formato que as telas aprendidas aceitam): dado de instalação,
    nunca regra de código (ADR-052). Chave fora do formato é erro de config, não item ignorado."""
    for pacote in por_app:
        if not _PACOTE_DO_APRENDIZADO.match(pacote):
            raise ValueError(f"'{pacote}' não é um pacote Android (ex.: com.exemplo.app)")
    return por_app


class LicoesCfg(BaseModel):
    #: off = não grava · shadow = grava e mede sem ir ao prompt · on = publica sem efeito e vai ao prompt (em prova)
    modo: Literal["off", "shadow", "on"] = "shadow"
    #: Override por app (§8.10): `{<pacote>: off|shadow|on}`. Vazio (padrão) = o `modo` global vale para todo app; o
    #: pacote que aparece aqui usa o modo dele. Nada liga sozinho: quem escreve o pacote é a instalação.
    por_app: dict[str, Literal["off", "shadow", "on"]] = Field(default_factory=dict)

    valida_pacotes = field_validator("por_app")(_conferir_pacotes_por_app)
    ator: LicoesDoPapelCfg = LicoesDoPapelCfg()
    planejador: LicoesDoPapelCfg = LicoesDoPapelCfg(tokens=150)
    minimo_por_braco: int = Field(8, ge=1, le=1000)        # unidades por braço para o veredito de efeito
    maximo_por_braco: int = Field(20, ge=1, le=1000)       # 'neutra' aos N por braço
    holdout_publicada: float = Field(0.1, ge=0, le=0.5)    # braço de controle depois de 'ajuda'


class TelasAprendidasCfg(BaseModel):
    #: off = não observa · observe = grava, minera e valida, sem a sessão consumir · on = publica sozinha (D1)
    modo: Literal["off", "observe", "on"] = "observe"
    #: Override por app (§8.10): `{<pacote>: off|observe|on}`; vazio = o `modo` global vale para todo app.
    por_app: dict[str, Literal["off", "observe", "on"]] = Field(default_factory=dict)
    observacoes: int = Field(3, ge=1, le=100)
    execucoes: int = Field(2, ge=1, le=100)

    valida_pacotes = field_validator("por_app")(_conferir_pacotes_por_app)


class FluxoAprendidoCfg(BaseModel):
    concordancias: int = Field(1, ge=0, le=20)             # execuções concordantes, além da que gerou, para publicar
    #: D1 no fluxo aprendido de execução: nasce candidato e só publica com prova. false = sem prova: nasce ativo (o
    #: modo anterior), como `ai.recipes_promote_after: 0` — só para a suíte que prova o reaproveitamento.
    com_prova: bool = True


class ModoDoAprendizadoCfg(BaseModel):
    modo: Literal["off", "shadow", "on"] = "off"


class BacklogCfg(BaseModel):
    minimo_ocorrencias: int = Field(3, ge=1, le=1000)
    pessoa_usd: float = Field(0.25, ge=0, le=100)          # custo de uma intervenção humana, só para ordenar
    aparelho_usd_min: float = Field(0.0, ge=0, le=100)     # custo de um minuto de aparelho, só para ordenar
    prova_minimo: int = Field(10, ge=1, le=10_000)         # tentativas elegíveis depois do commit para provar
    prova_fator: float = Field(0.5, gt=0, le=1)            # taxa depois ≤ fator × linha de base → corrigido


class RetencaoDoAprendizadoCfg(BaseModel):
    sinais_dias: int = Field(180, ge=1, le=3650)
    feedback_dias: int = Field(365, ge=1, le=3650)         # o voto explícito vive mais que os sinais implícitos
    exposicoes_dias: int = Field(120, ge=1, le=3650)       # depois de preenchidas
    evidencias_por_item: int = Field(200, ge=1, le=100_000)  # as mais recentes; os contadores guardam o total
    diario_dias: int = Field(400, ge=1, le=3650)
    candidata_sem_evidencia_dias: int = Field(90, ge=1, le=3650)


class SaudeCfg(BaseModel):
    """Limiares da saúde do item do Livro (30.4; proposta D-5, medida no banco real). Os defaults são os medidos;
    mudar aqui muda o rótulo de todos os itens na próxima leitura (nada é gravado)."""

    sem_uso_dias: int = Field(14, ge=1, le=3650)           # nunca usado há isto = sem_evidencia; sem uso há mais = parado
    amostra_minima: int = Field(5, ge=1, le=10_000)        # usos para a eficácia valer (abaixo: pouca_amostra)
    taxa_minima: float = Field(0.8, ge=0, le=1)            # eficácia acumulada mínima (abaixo, com amostra: degradando)
    falhas_seguidas: int = Field(2, ge=1, le=1000)         # consecutive_fail a partir do qual degrada
    contestacao_dias: int = Field(7, ge=1, le=365)         # janela da evidência contra/conflito


class CuradorCfg(BaseModel):
    """O curador por IA (30.11, `aprendizado-vivo.md` §8.6-8.8): o laço que pede PARECER sobre itens do Livro. De
    fábrica `off` (nada roda). `shadow` revisa e grava o parecer em `learning_reviews`; `on`, nesta fatia, faz o mesmo
    (o aceite é sempre da pessoa). O orçamento é proporcional ao gasto da operação (decisão do dono, 02/10):
    `B_W = min(alfa·G_W, k·N_W·c̄)` na janela de `janela_dias`; `c_max = m_cmax × mediana` do custo por revisão."""

    modo: Literal["off", "shadow", "on"] = "off"
    intervalo_s: int = Field(3600, ge=60, le=86_400)        # de quanto em quanto tempo o laço olha os gatilhos
    cooldown_h: float = Field(24.0, ge=0, le=24 * 90)       # o mesmo item não é revisado de novo antes disto
    alfa: float = Field(0.10, ge=0, le=1)
    k: float = Field(1.5, ge=0, le=100)
    janela_dias: int = Field(7, ge=1, le=90)
    m_cmax: float = Field(4.0, gt=0, le=100)


class ValidacaoCfg(BaseModel):
    """A validação automática do "pedir evidência" do curador (30.31; desenho aprovado pela orquestradora em 03/10). De
    fábrica `off`: nenhum pedido nasce e nada roda. `on`: o parecer que pede evidência vira pedido e o despachante roda
    a execução de validação (o comando de origem noutro aparelho ocioso), só com o central saudável e sem execução em
    curso, dentro de `beta` × o gasto de IA da operação na janela (sem teto fixo em US$, como o curador) e de
    `maximo_por_hora`. `extra_usd` até `extra_ate` (ISO) é a verba única (P4 do desenho)."""

    modo: Literal["off", "on"] = "off"
    intervalo_s: int = Field(600, ge=60, le=86_400)         # de quanto em quanto tempo o despachante olha a fila
    beta: float = Field(0.05, ge=0, le=1)
    maximo_por_hora: int = Field(4, ge=0, le=60)
    janela_dias: int = Field(7, ge=1, le=90)
    extra_usd: float = Field(0.0, ge=0, le=100)
    extra_ate: str = ""
    custo_estimado_usd: float = Field(0.07, ge=0, le=10)    # antes de medir: a mediana das execuções reais do QA
    #: 30.37: o teto de gasto de IA de UM pedido (a execução de prova); o roteador barra a chamada seguinte quando a
    #: execução já gastou isto. Vai à coluna `learning_validations.teto_usd` na hora do nascimento do pedido.
    teto_por_pedido_usd: float = Field(0.10, ge=0, le=10)

    @field_validator("extra_ate")
    @classmethod
    def _extra_ate_iso(cls, v: str) -> str:
        if v.strip():
            datetime.fromisoformat(v.strip().replace("Z", "+00:00"))  # data errada recusa o config, não some calada
        return v.strip()


class AutopublicacaoCfg(BaseModel):
    """A autopublicação do fluxo de classe B (30.34; emenda de 03/10 à D1 do ADR-054). De fábrica `off` (nada roda);
    `shadow` só marca, no livro da sombra, o que publicaria. `on` (30.34-B) publica pelo caminho próprio da emenda na
    trava da D1, mas só com o balanço da sombra liberado; sem ele, `on` se comporta como `shadow`. Os limiares (≥ 2
    execuções reais, ≥ 2 aparelhos, ≥ 30 casos fechados com ≥ 90 % sem regressão) são da regra
    (`domain/autopublicacao.py`), não daqui. Ligar `on` é decisão da orquestradora com o relatório da sombra."""

    modo: Literal["off", "shadow", "on"] = "off"
    intervalo_s: int = Field(3600, ge=60, le=86_400)        # de quanto em quanto tempo o laço avalia os fluxos


class AprovacaoAutomaticaCfg(BaseModel):
    """A aprovação automática por política (30.55; pedido do dono em 04/10). De fábrica `off` (nada roda); `shadow` só
    marca, no livro da sombra, a receita ou o fluxo que a plataforma decidiria; `on` decide (publica o de "Para aprovar"
    e confirma que fica o de "Revisar"), com `decided_by = plataforma` e a regra no motivo. A régua (classe A ou B, app
    de categoria qa, evidência da versão atual, saúde e parecer) é do domínio (`domain/aprovacao_automatica.py`), não
    daqui: mudá-la é decisão do dono e muda a versão da regra."""

    modo: Literal["off", "shadow", "on"] = "off"
    intervalo_s: int = Field(900, ge=60, le=86_400)         # de quanto em quanto tempo o laço passa a régua

    @field_validator("modo", mode="before")
    @classmethod
    def _modo_do_yaml(cls, v: object) -> object:
        """30.63 (b): `modo: on` sem aspas é o booleano `true` no YAML 1.1 (e `off`, `false`). Em 04/10 isso derrubou a
        carga do config inteiro por 7 s. O booleano vale como a palavra que a pessoa escreveu."""
        if isinstance(v, bool):
            return "on" if v else "off"
        return v


class LearningCfg(BaseModel):
    """Aprendizado contínuo (ADR-054): o livro, o D1, a falha classificada e a régua durável. Nenhuma chamada de IA
    no pipeline: digest por execução e curadoria determinística. De fábrica, lições em `shadow` e telas em `observe`,
    até a primeira prova real; publicar sozinho (D1) exige o modo do tipo em `on` e nunca vale para o que tem efeito
    externo ou texto de pessoa. Itens publicados, desligados, a trilha e o backlog nunca são purgados."""

    enabled: bool = True
    curadoria_s: int = Field(900, ge=60, le=86_400)
    licoes: LicoesCfg = LicoesCfg()
    telas: TelasAprendidasCfg = TelasAprendidasCfg()
    fluxo: FluxoAprendidoCfg = FluxoAprendidoCfg()
    #: RA-19 B: o que uma execução SIMULADA ensina (`runs.simulated=1`) nunca nasce ativo, receita ou fluxo, nem com
    #: `ai.recipes_promote_after: 0` ou `fluxo.com_prova: false`; e a concordância dela na sombra não promove receita.
    #: Só evidência real publica (a sombra do fluxo já só conta execução real), e a pessoa promove à mão. `true` = o
    #: modo anterior, só para a suíte: o Harness é todo simulado e prova a reprodução e o reaproveitamento.
    simulada_publica: bool = False
    voz: ModoDoAprendizadoCfg = ModoDoAprendizadoCfg()
    preferencias: ModoDoAprendizadoCfg = ModoDoAprendizadoCfg()
    backlog: BacklogCfg = BacklogCfg()
    #: Lições escritas por IA a partir das falhas: DESCARTADAS como padrão (gastam saldo e abrem injeção). Só 0 é
    #: aceito — ligar exige código novo e decisão do dono sobre chamada paga (ADR-049/051).
    ia_resumos_por_dia: int = Field(0, ge=0, le=0)
    takeover_gravar: bool = False                          # gravar as entradas manuais da tomada fora do treino
    retencao: RetencaoDoAprendizadoCfg = RetencaoDoAprendizadoCfg()
    saude: SaudeCfg = SaudeCfg()
    curador: CuradorCfg = CuradorCfg()
    validacao: ValidacaoCfg = ValidacaoCfg()
    autopublicacao: AutopublicacaoCfg = AutopublicacaoCfg()
    aprovacao_automatica: AprovacaoAutomaticaCfg = AprovacaoAutomaticaCfg()


class ConvidadosDoTelegramCfg(BaseModel):
    """Quem fala com o bot e não é o dono (28.18, C-08 a C-11 de `docs/dominios/canais.md`): a apresentação da ANA e a
    pergunta do nome, o dono decide com sim ou não, e o convidado autorizado nunca executa nem decide. Desligado de
    fábrica, e só vale com a `entrada` ligada; desligado, quem não é o dono segue gravado sem texto e sem resposta."""

    enabled: bool = False
    max_nome: int = Field(60, ge=10, le=200)                # o nome informado é cortado aqui
    avisos_por_hora: int = Field(6, ge=1, le=60)            # mensagens de UM convidado que viram aviso ao dono por hora
    novos_por_hora: int = Field(20, ge=1, le=500)           # chats novos atendidos por hora; o excesso fica sem resposta


class LeituraDeAnexoCfg(BaseModel):
    """A IA lê a imagem que o DONO mandou (item 28.24, fatia F3; C-22). Só imagem de entrada do dono, uma chamada por imagem
    (a descrição fica gravada e a segunda leitura não paga), com teto em dólar ESTIMADO antes de chamar: acima dele, nada
    é enviado ao provedor. `modelo` vazio = o mais barato de `ai.prices` que `ai.models` declara com visão."""

    enabled: bool = True
    teto_usd: float = Field(0.05, gt=0, le=5)
    modelo: str = ""
    max_tokens: int = Field(400, ge=50, le=2000)


class AnexosDaEntradaCfg(BaseModel):
    """Anexos dos canais (item 28.24, F1; regra do dono em `docs/dominios/canais.md` §6). Só o chat do dono tem anexo
    baixado, e só os tipos da lista: o mime é conferido pelo CONTEÚDO (assinatura), nunca só pelo que o remetente
    declarou. O arquivo vai para `data/anexos/` pelo sha256 (o nome do remetente nunca é usado) e vence com a retenção
    do 28.16. O Bot API baixa até 20 MB; o teto vale antes e durante o download."""

    enabled: bool = True
    max_bytes: int = Field(10 * 1024 * 1024, ge=1024, le=20_000_000)
    tipos: list[Literal["image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain"]] = Field(
        default_factory=lambda: ["image/jpeg", "image/png", "image/webp", "application/pdf", "text/plain"])
    leitura: LeituraDeAnexoCfg = LeituraDeAnexoCfg()


class EntradaDoTelegramCfg(BaseModel):
    """A conversa de volta (item 28.15, ADR-071): o chat do `.env` dá comandos e responde à Central como no painel.
    Desligada de fábrica, e só liga com `avisos.enabled` (mesmo bot, mesmo token, mesma trava `avisos`). O chat aceito
    é o `TELEGRAM_CHAT_ID` do `.env`; nenhum chat_id mora aqui (regra do 28.11)."""

    enabled: bool = False
    limite_por_min: int = Field(10, ge=1, le=120)           # mensagens do chat por minuto; o excesso vira `limitada`
    max_chars: int = Field(1000, ge=50, le=4000)            # maior que isto é recusada (o comando do painel vai a 4000)
    long_poll_s: int = Field(50, ge=1, le=60)               # quanto o `getUpdates` segura a conexão esperando
    espera_conflito_s: float = Field(60.0, ge=5, le=3600)   # 409 (outro consumidor do bot): espera, não disputa
    ttl_previa_s: float = Field(900.0, ge=30, le=86400)     # a prévia mais velha que isto não executa (manda de novo)
    # A mensagem escrita há mais que isto (a Central estava fora e o Telegram guardou) não é tratada: um "/aprovar" ou
    # um "sim" de horas atrás não executa. Gravada sem texto; o dono é avisado uma vez para mandar de novo.
    idade_max_s: float = Field(900.0, ge=60, le=86400)
    #: Quanto o que veio e foi pelo Telegram fica guardado (28.16): depois disso o texto é zerado e a linha apagada.
    #: Mínimo de 2 dias: passa da janela em que o Telegram guarda uma update (24 h), e o dedupe segue valendo.
    retencao_dias: float = Field(30.0, ge=2, le=3650)
    convidados: ConvidadosDoTelegramCfg = ConvidadosDoTelegramCfg()
    anexos: AnexosDaEntradaCfg = AnexosDaEntradaCfg()


class DecisoesAutomaticasCfg(BaseModel):
    """O que a plataforma decide sozinha (item 28.25): o resumo agrupado no Telegram e o prazo do desfazer. O resumo só sai
    com `avisos.enabled` e o canal pronto; sem eles, o registro e a aba do painel funcionam do mesmo jeito."""

    #: No máximo UMA mensagem por janela, e só se houve decisão nova nela. Nunca um aviso por decisão.
    janela_min: float = Field(60.0, ge=1, le=1440)
    #: Quantos dias o dono pode desfazer uma decisão depois que ela aconteceu.
    desfazer_dias: float = Field(7.0, ge=0.01, le=365)
    #: De quanto em quanto tempo o laço recolhe os eventos e a trilha (o adaptador) e confere a janela do resumo.
    intervalo_s: float = Field(30.0, ge=5, le=3600)


class RestoreEnsaioAvisoCfg(BaseModel):
    """28.60: o aviso do ensaio de restauração. A Central lê o veredito que `scripts/restore-ensaio.ps1` grava e avisa
    (rotina) quando ele `falhou`, foi `pulado`, não pôde ser lido ou ficou velho. Só vale com `avisos.enabled`."""

    enabled: bool = True
    intervalo_min: int = Field(15, ge=1, le=1440)             # de quanto em quanto tempo o veredito é relido
    ultimo_json: str = "data/restore-ensaio/ultimo.json"      # relativo à raiz do projeto; ausente = nada a avisar
    #: Idade máxima do veredito. O ensaio é SEMANAL (domingo 04:30), então 48 h alarmaria toda terça: o padrão é 7 dias
    #: mais 1 de folga. A cópia com mais de 48 h já vira `falhou` no próprio script (a tarefa diária `farm-backup` parou).
    idade_max_h: float = Field(192.0, gt=0, le=8760)


class DiscoAvisoCfg(BaseModel):
    """28.58: o aviso de disco baixo no central. O livre do disco (o mesmo leitor da saúde) abaixo de `piso_gb` avisa, e
    de novo a cada `degrau_gb` abaixo dele; ao voltar ao piso, rearma. NUNCA apaga nada. Só vale com `avisos.enabled`."""

    enabled: bool = True
    piso_gb: float = Field(100.0, gt=0, le=100_000)
    degrau_gb: float = Field(20.0, gt=0, le=100_000)
    critico_gb: float = Field(60.0, ge=0, le=100_000)         # abaixo disto o aviso diz que backup e criação podem falhar
    intervalo_min: int = Field(15, ge=1, le=1440)


class AvisosCfg(BaseModel):
    """Aviso fora do painel (item 28.11; decisão do dono, 02/10: Telegram). Espelho da caixa de Pendências (ADR-062).
    Desde o 28.15 (ADR-071) o aviso leva o conteúdo (a pergunta, o que se aprova), redigido e cortado, e o mesmo bot
    recebe (`entrada`). Desligado de fábrica; ligar exige `TELEGRAM_BOT_TOKEN` e `TELEGRAM_CHAT_ID` no `.env`
    (procedimento em `docs/operacao.md`). Os valores nunca moram aqui."""

    enabled: bool = False
    canal: Literal["telegram"] = "telegram"
    #: Base pública do painel para o link `<base>/#/pendencias`. Vazia: a mensagem leva só o texto. O painel mora em
    #: `/central/` (29.54), então a base inclui o prefixo: `https://dev.nvit.com.br/central`.
    url_painel: str | None = None
    intervalo_s: int = Field(15, ge=5, le=3600)             # de quanto em quanto tempo o laço olha a fila
    lote: int = Field(5, ge=1, le=50)                       # avisos por volta (o limite do Telegram é ~1 msg/s por chat)
    timeout_s: float = Field(10.0, gt=0, le=60)
    max_tentativas: int = Field(5, ge=1, le=20)
    backoff_s: float = Field(30.0, gt=0, le=3600)
    validade_h: float = Field(24.0, gt=0, le=720)           # pendente mais velho que isto deixa de ser notícia
    incerto_apos_s: float = Field(600.0, ge=60, le=86_400)  # `enviando` parado há isto vira `incerto`
    retencao_dias: float = Field(30.0, gt=0, le=3650)
    #: Rajada (28.19): o primeiro aviso de um tipo sai na hora; os do mesmo tipo que chegam até `agrupar_s` depois
    #: esperam o fim dessa janela e saem juntos, como UMA mensagem com a contagem quando são `agrupar_a_partir_de` ou
    #: mais (com menos, um a um). 0 desliga (cada aviso sai sozinho, como antes do 28.19).
    agrupar_s: float = Field(60.0, ge=0, le=3600)
    agrupar_a_partir_de: int = Field(3, ge=2, le=100)
    #: Faixas do `learning.needs_person` (30.21) que avisam fora do painel (28.14). Padrão: só a C (item a item); a B é
    #: aprovação em lote e fica na caixa de Pendências, para não virar um aviso por receita.
    aprendizado_faixas: list[Literal["B", "C"]] = Field(default_factory=lambda: ["C"])
    entrada: EntradaDoTelegramCfg = EntradaDoTelegramCfg()
    decisoes_automaticas: DecisoesAutomaticasCfg = DecisoesAutomaticasCfg()
    restore_ensaio: RestoreEnsaioAvisoCfg = RestoreEnsaioAvisoCfg()    # 28.60
    disco: DiscoAvisoCfg = DiscoAvisoCfg()                             # 28.58


#: Os papéis que `trello.listas` aceita (32.2): onde a Central cria os cartões, as listas cujo destino vale sim e não, e
#: onde nascem os cartões de marco (um por deploy) e de custo (um por dia). 28.51: as duas listas das perguntas ao dono
#: (`perguntas` e `perguntas_respondidas`); o comentário dele nelas é a resposta e não pede confirmação no Telegram.
PAPEIS_DE_LISTA_DO_TRELLO = frozenset({"central_automatico", "aprovado", "vetado", "marcos", "custos", "perguntas",
                                       "perguntas_respondidas"})


class TrelloWebhookCfg(BaseModel):
    """O webhook do Trello (32.2, §8 de `docs/design/trello-integracao.md`): caminho principal da entrada; a
    reconciliação por leitura continua cobrindo. Desligado de fábrica e SEPARADO de `trello.enabled`. O segredo do
    aplicativo que assina o corpo é `TRELLO_API_SECRET`, só no `.env`; aqui mora só a URL pública (sem parâmetro e sem
    segredo)."""

    #: Só a ROTA responde (HEAD 200, POST verifica a assinatura). Ligar isto NUNCA cadastra nada no Trello: o primeiro
    #: cadastro é manual (`scripts/trello-webhook.py --aplicar`, com o "vai" da orquestradora).
    enabled: bool = False
    #: O recadastro de hora em hora no líder (cria o que falta, recria o desativado). Chave SEPARADA, desligada de fábrica:
    #: só se liga depois da prova real. Sem ela o líder nunca chama `/1/webhooks`.
    cadastro_automatico: bool = False
    callback_url: str | None = None                          # a URL pública inteira; entra no HMAC tal como escrita
    max_bytes: int = Field(262144, ge=1024, le=4_194_304)    # acima disso, 413 sem ler o resto


class TrelloCfg(BaseModel):
    """Trello do dono como espelho legível do plano e canal de comandos (item 32.2, ADR-072; desenho em
    `docs/design/trello-integracao.md`). Desligado de fábrica; ligar exige `TRELLO_API_KEY` e `TRELLO_TOKEN` no `.env`
    (procedimento em `docs/operacao.md`). Os valores secretos nunca moram aqui; só ids de quadro, lista e membro."""

    enabled: bool = False
    quadros: list[str] = Field(default_factory=list)         # ids dos quadros que a Central espelha e lê
    #: ids das listas, por papel: `central_automatico` (onde a Central cria os cartões), `aprovado` e `vetado` (mover o
    #: cartão para elas vale "sim" e "não" do dono). Papel ausente = a função correspondente fica desligada.
    listas: dict[str, str] = Field(default_factory=dict)
    membro_dono: str = ""                                    # o idMember do dono: só ele comanda
    espelho_s: float = Field(60.0, ge=10, le=3600)           # de quanto em quanto tempo o reconciliador do espelho roda
    reconciliar_s: float = Field(60.0, ge=10, le=3600)       # leitura das actions do quadro (a rede de segurança do webhook)
    comando_livre: bool = False                              # false = a Central só espelha; true aceita comando por cartão
    # A action escrita há mais que isto (a Central estava fora) não é tratada: um "sim" de horas atrás não executa.
    idade_max_s: float = Field(900.0, ge=60, le=86400)
    #: Quanto o que veio e foi pelo Trello fica guardado (28.16): depois disso o texto é zerado e a linha apagada, junto
    #: com os cartões já arquivados. Mínimo de 2 dias: passa da janela em que o webhook reenvia uma action.
    retencao_dias: float = Field(30.0, ge=2, le=3650)
    #: Quem o dono autorizou a PEDIR (além dele). Vazia de fábrica: o pedido de convidado vira aviso ao dono.
    membros_autorizados: list[str] = Field(default_factory=list)
    #: 28.54: ids dos apps que o DONO reconheceu como ele (o aplicativo do Trello no celular dele). A resposta do membro
    #: do dono numa pergunta escrita por um desses apps vale como digitada; qualquer outro app não conta. Vazia de
    #: fábrica: um id só entra com o sim dele, conferido no banco. Nunca o app da Central.
    apps_do_dono: list[str] = Field(default_factory=list)
    responder_convidados: bool = False                       # responde a pergunta de convidado no cartão, só com o já visível
    webhook: TrelloWebhookCfg = TrelloWebhookCfg()

    @field_validator("apps_do_dono")
    @classmethod
    def _apps_sem_vazio(cls, v: list[str]) -> list[str]:
        # Um id vazio ou com espaço nunca casaria; erra na partida em vez de calar a resposta dele.
        limpos = [x.strip() for x in v]
        if any(not x for x in limpos):
            raise ValueError("trello.apps_do_dono: id vazio")
        return list(dict.fromkeys(limpos))

    @field_validator("listas")
    @classmethod
    def _papeis_conhecidos(cls, v: dict[str, str]) -> dict[str, str]:
        # Um papel digitado errado desligaria a função em silêncio; erra na partida, com o nome da chave.
        desconhecidos = sorted(set(v) - PAPEIS_DE_LISTA_DO_TRELLO)
        if desconhecidos:
            raise ValueError(f"trello.listas: papel desconhecido {desconhecidos}; use {sorted(PAPEIS_DE_LISTA_DO_TRELLO)}")
        return v


class ContextRetrievalLexicalCfg(BaseModel):
    use_ripgrep: bool = True       # false força o caminho Python puro (mesmo resultado, mais lento)
    ripgrep_path: str | None = None  # opcional; sem ele, RIPGREP_PATH e depois o PATH; sem rg, o motor é o Python
    window_lines: int = Field(7, ge=1, le=200)


class ContextRetrievalBm25Cfg(BaseModel):
    k1: float = Field(1.2, gt=0, le=5)
    b: float = Field(0.75, ge=0, le=1)


class ContextRetrievalCacheCfg(BaseModel):
    enabled: bool = True
    directory: str = "context_retrieval"   # relativo a `paths.data_dir`; fora do Git


class ContextRetrievalSemanticCfg(BaseModel):
    """Provedor semântico e os tetos do que ele pode custar e receber. Nada daqui carrega chave."""

    provider: Literal["none", "fake", "jev"] = "none"
    model: str = ""                                 # vazio = o padrão do adaptador
    #: Classe do repositório para a política de envio. A provedor REMOTO `private` e `synthetic` são NEGADOS (constantes de
    #: código `PRIVATE_CODE_SEND_APPROVED` e `SYNTHETIC_REMOTE_SEND_APPROVED`, não deste arquivo); só `public` com
    #: `allow_public` E a prova independente de visibilidade pública (git remote + GitHub anônimo) passa. `synthetic` serve a
    #: fixtures e testes com provedor fake ou local (ADR-063).
    repository_class: Literal["private", "public", "synthetic"] = "private"
    allow_public: bool = False                      # necessário, mas NÃO suficiente: o repositório real tem de provar ser público
    timeout_ms: int = Field(5_000, ge=100, le=60_000)
    max_calls: int = Field(2, ge=1, le=10)          # por pedido (etapa A + etapa B)
    max_calls_per_session: int = Field(40, ge=1, le=10_000)
    max_input_tokens: int = Field(32_000, ge=100)
    max_cost_usd: float = Field(0.05, ge=0)
    max_map_files: int = Field(400, ge=1, le=5_000)
    max_candidate_files: int = Field(5, ge=1, le=50)
    max_chunks: int = Field(16, ge=1, le=200)
    max_bytes: int = Field(48_000, ge=1_000, le=1_000_000)


class ContextRetrievalCfg(BaseModel):
    """Retrieval de contexto de código (ADR-063). Desligado por padrão: com `enabled: false` o código existe e nada muda."""

    enabled: bool = False
    mode: Literal["disabled", "local_only", "shadow", "hybrid"] = "disabled"
    top_k: int = Field(5, ge=1, le=20)
    lexical_preserve: int = Field(1, ge=0, le=5)    # arquivos lexicais preservados no topo pela regra híbrida v1
    lexical: ContextRetrievalLexicalCfg = ContextRetrievalLexicalCfg()
    bm25: ContextRetrievalBm25Cfg = ContextRetrievalBm25Cfg()
    cache: ContextRetrievalCacheCfg = ContextRetrievalCacheCfg()
    #: Padrões extras (globs) que NUNCA entram em índice, mapa ou chunk, além dos fixos do domínio.
    sensitive_paths: list[str] = []
    semantic: ContextRetrievalSemanticCfg = ContextRetrievalSemanticCfg()

    @property
    def effective_mode(self) -> str:
        """`disabled` sempre que o interruptor está desligado, seja qual for o `mode` escrito."""
        return self.mode if self.enabled else "disabled"


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


class ColaboracaoCfg(BaseModel):
    """Colaboração entre pedidos (item 28.10, fatia F1; `docs/design/pedidos-persistentes.md` §9): pedido pai com
    sub-pedidos, dependências e papéis. A F1 é a ESTRUTURA (grava, valida e encerra os filhos com o pai); a F2 faz o laço
    ler a dependência (a ocorrência do filho só despacha com o `de` comprovado) e descontar a reserva dos filhos do orçamento
    do pai. Desligada de fábrica: com `enabled: false` a API recusa `pai_id`, `papel` e `dependencias` com
    `colaboracao_desligada`, e todo pedido se comporta como antes."""

    enabled: bool = False
    #: Níveis da árvore, contando o pai: 2 = pai → filhos (o §9). O pedido sem pai tem profundidade 1; o neto, 3.
    max_profundidade: int = Field(2, ge=2, le=4)
    #: Filhos diretos de um mesmo pai (o §9: no máximo 5): trava a explosão de sub-pedidos, cada um com o próprio custo.
    max_filhos: int = Field(5, ge=1, le=20)
    #: F2: quanto a ocorrência do filho espera a dependência ser comprovada (a partir do `previsto_para`) antes de virar
    #: `pulada` ("dependência não comprovada: <id do pedido de>"). Dentro dela fica `devida`, sem execução.
    espera_dependencia_s: int = Field(3600, ge=60, le=604_800)


class PedidosCfg(BaseModel):
    """Laço de pedidos persistentes (item 28.4; `docs/design/pedidos-laco.md`). Desligado de fábrica (D1): cada
    ocorrência despachada chama o planejador PAGO e o orçamento (28.6) já limita o gasto, mas ligar segue sendo decisão do
    dono, por instalação. Desligado, o laço nem sobe (e este backend não toma a trava `pedidos`)."""

    enabled: bool = False
    #: De quanto em quanto tempo o laço acorda sem ninguém chamar. Também é a latência máxima do fechamento das
    #: execuções que assentam sem passar pelo gancho do worker (D6).
    tick_s: float = Field(15.0, ge=1.0, le=3600.0)
    #: Quanto à frente a recorrência é materializada como `prevista` (global, D2).
    horizonte_s: int = Field(3600, ge=60, le=86_400)
    #: Quanto uma execução despachada pode ficar sem começar antes de o laço cancelá-la (global, D2).
    prazo_inicio_s: int = Field(3600, ge=60, le=604_800)
    #: Janela de recuperação de `horario` sem efeito, de `agora` e de recorrência diária ou mais lenta (D7).
    janela_padrao_s: int = Field(1800, ge=0, le=604_800)
    #: Teto de linhas materializadas por gatilho e de execuções criadas por volta.
    lote_max: int = Field(500, ge=1, le=5000)
    #: Validade da reserva de uma ocorrência por um laço (eficiência; a correção vem da chave).
    posse_s: int = Field(120, ge=10, le=3600)
    #: Saldo estimado mínimo, em US$, das contas de IA em uso (ADR-051) para despachar (28.6). Abaixo dele o laço ADIA a
    #: ocorrência: ela continua `devida`, sem falha, e sai quando o saldo volta (ou o dono recarrega). 0 desliga este
    #: mínimo; o bloqueio do dono (`block_below` da conta) adia de qualquer forma.
    saldo_minimo_usd: float = Field(0.0, ge=0.0, le=100_000.0)
    #: Resumo do relatório por IA (28.7, `docs/design/pedidos-persistentes.md` §6.6): só o ponto de extensão. O relatório
    #: determinístico é a fonte da verdade e sai sempre; ligar isto SEM um resumidor injetado não chama nada.
    resumo_ia: bool = False
    #: Teto por relatório, em USD, que o resumidor deve respeitar (e que o 28.6 conta no orçamento do pedido).
    resumo_ia_teto_usd: float = Field(0.05, ge=0.0, le=5.0)
    #: Tentativas e falhas seguidas (28.5, §7.6). Cada pedido tem as próprias colunas (`max_tentativas`, `pausa_por_falha`,
    #: 067); estes dois são o PADRÃO global, usado quando a coluna não vem (e o que a criação pela API do 28.9 grava).
    #: `max_tentativas` é o total de execuções por ocorrência (2 = a primeira e UMA repetição).
    max_tentativas: int = Field(2, ge=1, le=10)
    falhas_para_pausar: int = Field(3, ge=1, le=100)
    #: Atraso exponencial da nova tentativa: `base * 2**(n-1)`, no máximo `retentativa_teto_s`. Chega até `tick_s` tarde.
    retentativa_base_s: float = Field(60.0, ge=1.0, le=86_400.0)
    retentativa_teto_s: float = Field(900.0, ge=1.0, le=86_400.0)
    #: Piso de frequência da criação e da edição (28.9, adendo v0.45, decisão do dono 02/10): o menor intervalo entre
    #: ocorrências por teto de autonomia. `observar` e `preparar` leem e preparam (15 min); `agir` tem efeito externo e
    #: custo por ocorrência (1 h). Abaixo, a criação recusa com `frequencia_abaixo_do_piso`.
    piso_observar_s: int = Field(900, ge=60, le=86_400)
    piso_agir_s: int = Field(3600, ge=60, le=86_400)
    #: Colaboração entre pedidos (28.10, F1): estrutura sem efeito no laço. Desligada de fábrica.
    colaboracao: ColaboracaoCfg = ColaboracaoCfg()
    #: Os nomes de sessão do painel que são o DONO (28.31 F2a, migração 106). Só o pedido criado por um deles (ou pelo
    #: `trello:<membro_dono>`) grava `criado_por_tipo='dono'` e mostra o título no canal de fora. O login aceita qualquer
    #: nome, então qualquer outro grava `convidado`. Vazia de fábrica: ninguém é o dono, e o aviso sai pelo id curto.
    operadores_do_dono: list[str] = Field(default_factory=list, max_length=20)


class ExecucaoCfg(BaseModel):
    """Vencimento do que espera uma pessoa que não veio (29.50 e 31.43). Só muda ESTADO: nada responde a pergunta, digita,
    toca aparelho ou chama IA. Ligado de fábrica: sem isto a pergunta sem resposta prende a execução (`needs_input`) e o
    objetivo (`waiting_user`) para sempre. Desligado, nenhum dos dois vence."""

    vencimento_ligado: bool = True
    #: Horas sem resposta para a pergunta (`needs_input`) e o bloqueio (`waiting_user`) vencerem. Para o bloqueio, o relógio é
    #: o mais tardio entre a entrada do objetivo em `waiting_user` e o fim da execução.
    #: 31.50 (c): piso de 1 h. Com 0,01 h, um "0.05" digitado no lugar de "5" encerrava em minutos tudo o que espera uma
    #: pessoa (perguntas, bloqueios e aprovações pendentes), e o vencimento não tem desfazer.
    pergunta_vence_h: float = Field(24.0, ge=1.0, le=8760.0)


#: Telefone publicável no site (29.77): só dígitos e `+ ( ) -` e espaço. É o mesmo filtro do formulário: o campo não
#: vira canal de texto livre nem de link.
TELEFONE_PUBLICO = re.compile(r"^[0-9+()\- ]{8,30}$")


#: O bloco `portal` recusa chave desconhecida (o resto do arquivo aceita): um nome errado ali (`buscas_por_operador_hor`)
#: valeria o padrão em silêncio, e é o bloco que mexe com o que a página promete ao visitante e com a exclusão a pedido
#: do titular. A subida falha dizendo qual chave (pedido da orquestradora no 29.83). O erro nomeia a chave e o caminho,
#: nunca o valor: um `telefon:` digitado errado em `portal.contatos` poria o telefone no console do deploy e no log
#: da subida (revisão do #342, E5). Aqui vale quando o modelo é validado sozinho; pelo arquivo inteiro, quem decide é
#: o `AppConfigFile` (o pydantic usa a configuração do modelo raiz), que esconde o valor também.
_PORTAL_ESTRITO = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ContatoPublicoCfg(BaseModel):
    """Um contato comercial mostrado no site, com toque para ligar e link de WhatsApp. Vem do config de cada
    instalação, nunca do código: o repositório não carrega telefone pessoal, e trocar um número não pede mudança."""

    model_config = _PORTAL_ESTRITO

    nome: str = Field(min_length=1, max_length=80)
    telefone: str

    @field_validator("telefone")
    @classmethod
    def _telefone_publicavel(cls, valor: str) -> str:
        valor = valor.strip()
        if not TELEFONE_PUBLICO.match(valor) or sum(c.isdigit() for c in valor) < 8:
            raise ValueError("portal.contatos[].telefone: só dígitos, espaço e + ( ) -, com ao menos 8 dígitos")
        return valor


class PortalVigiaCfg(BaseModel):
    """O vigia da borda (29.97): de hora em hora, no líder da trava `avisos`, o central pede a API, o site e o painel
    pelo primeiro nome de `server.public_hosts`, como um visitante (só GET, sem credencial, no máximo 5 pedidos por
    volta), e confere que a API recusa (29.101) e o que a borda da Cloudflare fez com as páginas. A API é conferida
    mesmo com `portal.site_ligado: false`. Sem nome público, ou com `ligado: false`, nada roda, nem a API."""

    model_config = _PORTAL_ESTRITO

    ligado: bool = True
    intervalo_s: int = Field(3600, ge=600, le=86_400)
    #: O prazo de cada pedido. Esgotado, a volta não conta como defeito nem como sucesso.
    prazo_s: int = Field(10, ge=1, le=60)
    #: Voltas seguidas sem conseguir conferir (rede, tempo esgotado, a borda sem alcançar o central) até virar problema
    #: na saúde e aviso ao dono.
    voltas_sem_conferir: int = Field(3, ge=1, le=48)


class PortalContatoLimitesCfg(BaseModel):
    """Os tetos do formulário de contato (29.77, ADR-075). A Cloudflare não é a defesa: tudo isto vale no backend."""

    model_config = _PORTAL_ESTRITO

    por_cliente_hora: int = Field(3, ge=1, le=100)
    por_cliente_dia: int = Field(10, ge=1, le=1000)
    #: Mensagens ao Telegram por hora, somando todos os visitantes. Acima disso o contato fica `retido` e sai na hora
    #: seguinte: nunca some calado, e um pico não atrasa os avisos de aprovação, que dividem o mesmo bot.
    telegram_hora: int = Field(20, ge=1, le=200)
    #: Linhas guardadas por dia. Acima disso o contato é descartado e só CONTADO (o texto não é gravado): um robô que
    #: contorne o limite por cliente não enche o disco.
    guardados_dia: int = Field(500, ge=10, le=10_000)
    #: Teto do corpo do POST, conferido lendo em fluxo (o `chunked` não tem `Content-Length`).
    corpo_max_bytes: int = Field(8192, ge=2048, le=65_536)
    #: Janela do token de tempo mínimo que vai no HTML: mais cedo que isso é robô; mais tarde, a página ficou aberta
    #: tempo demais e o visitante recarrega.
    token_min_s: int = Field(3, ge=1, le=60)
    token_max_s: int = Field(7200, ge=600, le=86_400)
    #: Buscas por telefone na exclusão a pedido do titular (29.83), por operador da sessão e por hora, contadas em
    #: memória no processo. Não é o formulário: é o painel, mas a busca acha contatos e não pode virar varredura.
    buscas_por_operador_hora: int = Field(30, ge=1, le=1000)
    #: As mesmas buscas somando todos os operadores. É este que limita a varredura: o nome do operador é declarado
    #: no login, e um nome novo ganharia outro balde por operador, mas não outro balde geral (revisão do #342, E4).
    buscas_total_hora: int = Field(60, ge=1, le=10_000)


class ComandoRemotoCfg(BaseModel):
    """Comando remoto nos notebooks da rede (29.154, ADR-079). `ativo` nasce DESLIGADO aqui e no exemplo: o merge e o
    deploy não abrem nada. Vale só junto do interruptor POR WORKER (painel) e do `comando_remoto: true` do agente, e
    ligar é decisão do dono, por cartão. O `config.yaml` só vale na subida: mudar `ativo` exige reiniciar o central."""

    model_config = ConfigDict(extra="forbid")

    ativo: bool = False
    fila_max: int = Field(4, ge=0, le=20)                         # comandos esperando, além do que está rodando
    por_minuto_por_operador: int = Field(30, ge=1, le=600)
    retencao_dias: int = Field(30, ge=1, le=365)                  # a saída guardada some depois disso
    max_por_worker: int = Field(200, ge=10, le=5000)


class PortalCfg(BaseModel):
    """O site institucional na raiz do nome público e o formulário de contato (29.77, ADR-075).

    As duas bandeiras nascem DESLIGADAS, aqui e no exemplo: o merge e o deploy não abrem nada. Desligado o site, `/`
    segue no 307 para `/central/` (ADR-073); desligado o contato, a rota responde 404. Quem liga é a orquestradora, no
    `config.yaml` do central, depois do sim do dono às capturas. O prazo de guarda do contato (180 dias) é fixo e está
    escrito na própria página: mudar o prazo é mudar a página, não um número aqui."""

    model_config = _PORTAL_ESTRITO

    site_ligado: bool = False
    contato_ligado: bool = False
    contatos: list[ContatoPublicoCfg] = []
    limites: PortalContatoLimitesCfg = PortalContatoLimitesCfg()
    vigia: PortalVigiaCfg = PortalVigiaCfg()

    @model_validator(mode="after")
    def _contato_so_com_o_site(self) -> "PortalCfg":
        """O formulário mora na página: contato ligado sem o site seria uma rota que aceita (o robô recebe 202) sem
        página que emita o token, e nada seria gravado. Os dois ligam JUNTOS; a recusa é na subida (revisão do #333)."""
        if self.contato_ligado and not self.site_ligado:
            raise ValueError("portal.contato_ligado exige portal.site_ligado: o formulário mora na página do site")
        return self


class AppConfigFile(BaseModel):
    # O erro de validação nomeia a chave e o caminho, sem ecoar o valor lido do arquivo: o config de cada instalação
    # carrega telefones e nomes (portal.contatos), e a mensagem vai ao console do deploy e ao log da subida (E5).
    model_config = ConfigDict(hide_input_in_errors=True)

    server: ServerCfg = ServerCfg()
    paths: PathsCfg = PathsCfg()
    android: AndroidCfg = AndroidCfg()
    instances: InstancesCfg = InstancesCfg()
    appium: AppiumCfg = AppiumCfg()
    limits: LimitsCfg = LimitsCfg()
    execucao: ExecucaoCfg = ExecucaoCfg()
    ai: AiCfg = AiCfg()
    contas: ContasCfg = ContasCfg()
    releases: ReleasesCfg = ReleasesCfg()
    skills: SkillsCfg = SkillsCfg()
    context_retrieval: ContextRetrievalCfg = ContextRetrievalCfg()
    provisioning: ProvisioningCfg = ProvisioningCfg()
    rede: RedeCfg = RedeCfg()
    aprendizado: LearningCfg = LearningCfg()
    avisos: AvisosCfg = AvisosCfg()
    trello: TrelloCfg = TrelloCfg()
    pedidos: PedidosCfg = PedidosCfg()
    comando_remoto: ComandoRemotoCfg = ComandoRemotoCfg()   # 29.154, ADR-079; desligado de fábrica
    portal: PortalCfg = PortalCfg()          # site institucional e contato (29.77, ADR-075); desligado de fábrica
    apps: list[AppSeed] = []
    sensitive_screens: list[SensitiveScreenSeed] = []

    @model_validator(mode="before")
    @classmethod
    def _sem_bloco_que_saiu(cls, dados: object) -> object:
        if isinstance(dados, dict):
            for bloco, onde in BLOCOS_QUE_SAIRAM.items():
                if bloco in dados:
                    raise ValueError(f"o bloco `{bloco}:` do config.yaml saiu ({onde})")
        return dados

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
        # Texto pelo painel na loja: liberado pela decisão 4 do plano (dono, 24/09) — menos a SENHA da conta Google,
        # que continua sendo digitada na janela do emulador (`store_password_blocked`, `DeviceManager.manual_input`).
        return self

    @model_validator(mode="after")
    def _app_alvo_nunca_desativado(self) -> "AppConfigFile":
        """O app alvo não pode estar em `desativar_apps` — nem o do catálogo semeado aqui (`apps`), nem o de uma conta
        gerenciada (`contas.sessao`): o preparo o desligaria e a automação não teria o que abrir. O catálogo do banco
        é conferido pelo central a cada preparo (`DeviceManager.apps_de_fundo_de`)."""
        alvos = {a.package for a in self.apps} | set(self.contas.sessao)
        listas = [("android.desativar_apps", self.android.desativar_apps)]
        listas += [(f"instances.overrides.{iid}.desativar_apps", list(over.get("desativar_apps") or []))
                   for iid, over in self.instances.overrides.items()]
        for onde, lista in listas:
            conflito = sorted(alvos & {str(p).strip() for p in lista})
            if conflito:
                raise ValueError(f"{onde}: {', '.join(conflito)} é app alvo da automação (declarado em `apps` ou "
                                 "`contas.sessao`) e nunca é desativado")
        return self

    @model_validator(mode="after")
    def _ia_coerente(self) -> "AppConfigFile":
        """Papel, provedor e preço conferidos na PARTIDA — não na primeira chamada paga (achados #91 e #97)."""
        ai = self.ai
        # As funções de cada perfil passam pelas MESMAS conferências das de `ai.roles` (item 17.7): um perfil
        # inválido recusa a partida, não a primeira execução que o escolher.
        blocos = [("ai.roles", ai.roles)] + [(f"ai.profiles.{nome}.roles", p.roles) for nome, p in ai.profiles.items()]
        for onde, roles in blocos:
            for papel in roles:
                if papel in ROLES_OPCIONAIS and onde == "ai.roles":
                    continue
                if papel not in AI_ROLES:
                    raise ValueError(f"{onde}.{papel}: função desconhecida (use {', '.join(AI_ROLES)}"
                                     + (f"; `{papel}` só vale em ai.roles, não por perfil" if papel in ROLES_OPCIONAIS
                                        else "") + ")")
        leitura = ai.roles.get("leitura")
        if leitura is not None:
            # Sem herança (item 12.5): o conferente que herdasse o provedor ou o modelo do ator deixaria de ser outro.
            # O fallback também não: ele mandaria o recorte de uma conta real para um destino que ninguém escolheu.
            if not leitura.provider or not leitura.model:
                raise ValueError("ai.roles.leitura: escreva `provider` e `model` (o papel não herda de outra função)")
            if leitura.fallback_provider:
                raise ValueError("ai.roles.leitura.fallback_provider: não existe fallback para o leitor; o recorte só "
                                 "vai ao provedor escolhido aqui")
            if leitura.refusal_fallback is not None:
                raise ValueError("ai.roles.leitura.refusal_fallback: não existe para o leitor; a captura vai exatamente "
                                 "para o provedor que o aviso de privacidade nomeia (remova a linha)")
        if ai.canary.profile and ai.canary.profile not in ai.profiles:
            raise ValueError(f"ai.canary.profile: perfil '{ai.canary.profile}' não está em ai.profiles")
        for nome, prov in ai.providers.items():
            if prov.kind == "openai" and not (prov.base_url or "").strip():
                raise ValueError(f"ai.providers.{nome}: kind=openai exige base_url (ex.: http://127.0.0.1:8001/v1)")
        for onde, papel, r in ((onde, papel, r) for onde, roles in blocos for papel, r in roles.items()):
            for campo, alvo in (("provider", r.provider), ("fallback_provider", r.fallback_provider)):
                if alvo and alvo not in ai.providers:
                    raise ValueError(f"{onde}.{papel}.{campo}: provedor '{alvo}' não está em ai.providers")
            # A família manda, como em `Config.model_caps`: produção usa `claude-haiku-4-5-20251001`, e exigir a
            # chave exata aqui rejeitaria uma configuração que o runtime aceitaria.
            declarado = (r.model in ai.models or re.sub(r"-\d{8}$", "", r.model or "") in ai.models
                         or any((r.model or "").startswith(k) for k in ai.models))
            if r.model and r.provider and ai.providers[r.provider].kind != "simulated" and not declarado:
                raise ValueError(f"{onde}.{papel}.model: '{r.model}' não está declarado em ai.models "
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
    extra_body: dict[str, Any] | None      # item 7.8: repassado tal qual ao corpo do POST (ex.: options do Ollama)
    # Item 17.14: o que o YAML escreveu para ESTA função (padrão ou perfil). Vazio = o caminho de hoje, byte a byte.
    effort_declarado: Effort | None = None
    thinking: bool | None = None
    cache_da_etapa: bool = False

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
        self.validar_leitura()

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

    def ajustes_de_sessao(self, package: str) -> dict[str, int | float]:
        """O que ESTA instalação sobrescreve nos ajustes de sessão do app `package` (tetos, cooldown, prazos).

        Desde o ADR-052 (fatia 3), os padrões moram no conhecimento do app (`app/conhecimento/apps/<pacote>/
        sessao.yaml`) e o motor de sessão (`integrations/app_declarado/sessao.py`) aplica isto por cima, a cada uso.
        Vem de `contas.sessao.<pacote>` (fatia 4; era o bloco `instagram:`). Só entra o que foi ESCRITO (no arquivo
        ou por atribuição): o resto vem do dado do app.
        """
        escritos = self.file.contas.sessao.get(package)
        if escritos is None:
            return {}
        return {k: v for k in AJUSTES_DE_SESSAO
                if k in escritos.model_fields_set and (v := getattr(escritos, k)) is not None}

    # ================================================================== hub de IA (item 7.1)
    def ai_model_for(self, role: str) -> str:
        """Modelo de uma função pelo `.env`, na precedência de sempre: AI_MODEL_<PAPEL> → cadeia → AI_MODEL."""
        env, base = self.env, self.env.ai_model
        por_papel = {"plan": env.ai_model_planner or base, "decide": env.ai_model_actor or base}
        por_papel["verify"] = env.ai_model_verifier or env.ai_model_actor or base
        por_papel["escalation"] = env.ai_model_escalation or por_papel["plan"]
        # Escrever como a persona é redação, não navegação: por padrão usa o modelo do planejador.
        por_papel["social"] = env.ai_model_social or por_papel["plan"]
        por_papel["persona"] = por_papel["social"]       # 17.8: sem `ai.roles.persona`, o modelo de sempre do social
        return por_papel.get(role, base)

    def ai_effort_for(self, role: str) -> Effort:
        env = self.env
        if role in ("plan", "social", "persona"):
            return env.ai_effort_planner
        if role == "verify":
            return env.ai_effort_verifier or env.ai_effort_actor
        return env.ai_effort_actor

    def ai_role(self, role: str, profile: str | None = None) -> ResolvedRole:
        """A função resolvida: YAML manda, `.env` é o padrão, `ROLE_DEFAULTS` fecha o que ninguém disse.

        Sem bloco `ai.roles` no YAML o resultado é EXATAMENTE o de antes do hub — provedor único do `.env`,
        modelo por função pela cadeia de sempre. É o que mantém o `.env` de produção valendo sem uma linha nova.

        `profile` (item 17.7): o que `ai.profiles.<perfil>.roles.<função>` escreve vale por cima de `ai.roles`, campo
        a campo; o que ele não escreve continua o do padrão. Perfil inexistente é `KeyError` — quem chama confere.
        """
        ai = self.file.ai
        if role in ROLES_OPCIONAIS:
            lido = ai.roles.get(role)
            if lido is None:
                raise KeyError(f"ai.roles.{role} não está configurado (função opcional, sem herança)")
            profile = None                  # o perfil de execução não troca o leitor
        # `persona` (item 17.8) sem bloco em `ai.roles` É o `social`: lê o bloco dele e, em cada perfil, a camada dele
        # (depois a própria, por cima). Com bloco próprio, o `social` deixa de valer para ela — herdar só o modelo ou
        # o `fallback_provider` do social ao apontar a persona para outro provedor daria um modelo que o destino não tem.
        herda_social = role == "persona" and "persona" not in ai.roles
        r = ai.roles.get("social" if herda_social else role) or RoleCfg()
        if profile is not None:
            camadas = ai.profiles[profile].roles
            for nome in (("social",) if herda_social else ()) + (role,):
                sobre = camadas.get(nome)
                if sobre is not None:
                    r = r.model_copy(update=sobre.model_dump(exclude_none=True))
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
            # `leitura` nunca cai em fallback de recusa: o recorte vai para quem o aviso nomeia (item 12.5)
            refusal_fallback=(False if role in ROLES_OPCIONAIS else
                              self.env.ai_refusal_fallback if r.refusal_fallback is None else r.refusal_fallback),
            timeout_s=float(r.timeout_s if r.timeout_s is not None else padrao["timeout_s"]),
            max_retries=int(r.max_retries if r.max_retries is not None else 0),
            concurrency=int(r.concurrency if r.concurrency is not None else padrao["concurrency"]),
            effort=r.effort or self.ai_effort_for(role), extra_body=prov.extra_body,
            effort_declarado=r.effort, thinking=r.thinking, cache_da_etapa=bool(r.cache_da_etapa))

    def ai_roles(self, profile: str | None = None) -> dict[str, ResolvedRole]:
        return {papel: self.ai_role(papel, profile) for papel in AI_ROLES}

    def ai_da_execucao(self, profile: str | None) -> AiCfg:
        """O bloco `ai` que vale para uma execução: o global com o que o perfil escreve de imagem e de árvore por cima
        (item 17.14). Sem perfil, ou perfil sem esses campos (ou que sumiu da configuração: a chamada de IA da execução
        já falha com o nome dele), é o MESMO objeto de sempre."""
        ai = self.file.ai
        perfil = ai.profiles.get(profile) if profile else None
        if perfil is None:
            return ai
        troca = {campo: valor for campo in ("screenshot_max_side", "rich_tree_min_elements", "acoes_por_decisao")
                 if (valor := getattr(perfil, campo)) is not None}
        return ai.model_copy(update=troca) if troca else ai

    def ai_leitura(self) -> ResolvedRole | None:
        """O leitor da leitura visual (item 12.5), ou `None` quando `ai.roles.leitura` não está escrito."""
        return self.ai_role("leitura") if "leitura" in self.file.ai.roles else None

    @staticmethod
    def _nome_de_modelo(model: str | None) -> str:
        """O nome do modelo na forma de comparar: sem caixa, sem prefixo de gateway (`openai/gpt-x` -> `gpt-x`) e sem
        sufixo de data (`-20261001`), como o `model_caps` já faz. O mesmo modelo escrito de duas formas é UM modelo."""
        return re.sub(r"-\d{8}$", "", (model or "").strip().casefold().rsplit("/", 1)[-1])

    def validar_leitura(self) -> None:
        """O leitor tem de ser independente do ator e ver imagem (ADR-070). O código exige modelo DIFERENTE do `decide` e do
        `escalation` (base e perfis; comparados pelo nome normalizado) e COM VISÃO DECLARADA em `ai.models`: modelo ausente
        da tabela é recusado, porque o `ModelCaps()` padrão presume visão. Compara o modelo, e não só o provedor.

        O que o código NÃO exige é a família: o padrão decidido pelo dono é a OpenAI (gpt-6-luna), com o Gemini
        (gemini-3.1-flash-lite) de reserva. O Haiku é da mesma família do ator e só entra com nova decisão do dono."""
        r = self.ai_leitura()
        if r is None:
            return
        nome = self._nome_de_modelo(r.model)
        # Contra o `decide` e o `escalation` de BASE e de CADA perfil: o canário que troca o ator para o modelo do leitor
        # tiraria a independência da conferência só naquelas execuções.
        for perfil in (None, *self.file.ai.profiles):
            for papel in ("decide", "escalation"):
                outro = self.ai_role(papel, perfil)
                if nome == self._nome_de_modelo(outro.model) and r.kind != "simulated":
                    onde = f"ai.profiles.{perfil}.roles.{papel}" if perfil else papel
                    raise ValueError(f"ai.roles.leitura.model: '{r.model}' é o mesmo modelo de {onde}; o segundo leitor "
                                     "precisa ser outro modelo (o padrão do dono é de outra família: OpenAI ou Gemini)")
        if r.kind == "simulated":
            return
        declarado = next((v for k, v in self.file.ai.models.items() if self._nome_de_modelo(k) == nome), None)
        if declarado is None:
            raise ValueError(f"ai.roles.leitura.model: '{r.model}' não está declarado em ai.models; escreva a linha "
                             f"`ai.models.{r.model}` com `vision: true` (a visão do leitor não se presume)")
        if not declarado.vision:
            raise ValueError(f"ai.roles.leitura.model: '{r.model}' está declarado sem visão em ai.models; o leitor "
                             "transcreve uma imagem (corrija `vision` na linha `ai.models." + r.model + "`)")

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
