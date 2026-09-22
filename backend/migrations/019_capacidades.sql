-- Capacidades declaradas: o que o aparelho É, e não só que verbo ele aceita.
--
-- Até aqui a única pergunta que o modelo respondia sobre um aparelho era "ele aceita este verbo de ciclo de
-- vida?". O pedido fala em capacidades modeladas e em escalonar por capacidades e apps instalados, explicando a
-- limitação ANTES de agendar — e a incompatibilidade só aparecia no meio do caminho, como
-- `INSTALL_FAILED_NO_MATCHING_ABIS` ou `app_incompatible`. O instalador já LIA o perfil do aparelho (ABI, SDK,
-- idioma, densidade) a cada instalação e jogava fora.
--
-- Colunas nulas = "ainda não se sabe", que é diferente de "não tem". A recusa explicada só acontece sobre o que
-- foi observado ou declarado: adivinhar seria o mesmo tipo de afirmação vaga que esta fase existe para eliminar.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- O TIPO de hardware, que é diferente do papel do aparelho no parque (`InstanceDTO.kind`: emulator/external/
-- store). Um aparelho físico de outra máquina é `external` no papel e `physical` no hardware.
ALTER TABLE instances ADD COLUMN device_kind TEXT;      -- emulator | physical | container
ALTER TABLE instances ADD COLUMN system_image TEXT;     -- ex.: system-images;android-34;google_apis;x86_64
ALTER TABLE instances ADD COLUMN api_level INTEGER;     -- ro.build.version.sdk
ALTER TABLE instances ADD COLUMN abis TEXT;             -- JSON: ro.product.cpu.abilist, na ordem de preferência
ALTER TABLE instances ADD COLUMN play_store INTEGER;    -- 1 = tem GMS/Play Services; 0 = imagem AOSP; nulo = não se sabe
ALTER TABLE instances ADD COLUMN capabilities_at TEXT;  -- quando isto foi observado/declarado pela última vez

-- O outro lado da pergunta: o que o APLICATIVO exige. `min_sdk` e as ABIs já vinham do inspetor de APK; faltava
-- dizer que um fluxo depende de Play Services — a incompatibilidade que nenhuma verificação de ABI pega.
ALTER TABLE app_releases ADD COLUMN requires_gms INTEGER NOT NULL DEFAULT 0;
