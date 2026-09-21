-- Release de aplicativo como artefato de primeira classe: o que foi importado, o que foi validado e o que está
-- realmente instalado em cada aparelho. Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

-- Uma release lógica = (pacote, versionCode, assinatura). Os arquivos ficam em app_release_files.
CREATE TABLE app_releases (
    id                TEXT PRIMARY KEY,
    package_name      TEXT NOT NULL,
    version_name      TEXT NOT NULL,
    version_code      INTEGER NOT NULL,
    artifact_type     TEXT NOT NULL,              -- single | split_set | unverified_split_set
    signature_sha256  TEXT NOT NULL,
    min_sdk           INTEGER,
    target_sdk        INTEGER,
    supported_abis    TEXT NOT NULL DEFAULT '[]', -- JSON
    catalog_dir       TEXT NOT NULL,              -- relativo à raiz do projeto
    source_type       TEXT NOT NULL,              -- inbox | upload
    source_reference  TEXT,                       -- texto livre informado por quem importou
    imported_at       TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'imported',
    detail            TEXT
);
CREATE INDEX idx_app_releases_package ON app_releases(package_name, version_code);

-- Um conjunto de splits é uma unidade atômica: nunca se instala parte dele.
CREATE TABLE app_release_files (
    id          {{PK_AUTO}},
    release_id  TEXT NOT NULL REFERENCES app_releases(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,                    -- base | split
    split_name  TEXT,
    file_name   TEXT NOT NULL,
    sha256      TEXT NOT NULL,
    size_bytes  INTEGER NOT NULL,
    UNIQUE(release_id, file_name)
);

-- Desejado x observado por (aparelho, pacote). O banco é cache: a verdade é o que o aparelho responde.
CREATE TABLE device_app_state (
    instance_id            TEXT NOT NULL,
    package_name           TEXT NOT NULL,
    desired_release_id     TEXT REFERENCES app_releases(id) ON DELETE SET NULL,
    installed_release_id   TEXT REFERENCES app_releases(id) ON DELETE SET NULL,
    observed_version_name  TEXT,
    observed_version_code  INTEGER,
    observed_splits        TEXT NOT NULL DEFAULT '[]',   -- JSON
    first_install_time     TEXT,
    last_update_time       TEXT,
    state                  TEXT NOT NULL DEFAULT 'missing',
    pending_op             TEXT,                          -- install | verify (reconciliado ao subir)
    pending_op_at          TEXT,
    verified_at            TEXT,
    drift_kind             TEXT,
    detail                 TEXT,
    PRIMARY KEY (instance_id, package_name)
);
CREATE INDEX idx_device_app_state_package ON device_app_state(package_name, state);

-- Assinatura aprovada pelo operador, por pacote: depois da primeira, qualquer assinatura diferente é bloqueada.
CREATE TABLE app_trusted_signers (
    package_name      TEXT PRIMARY KEY,
    signature_sha256  TEXT NOT NULL,
    approved_at       TEXT NOT NULL,
    note              TEXT
);
