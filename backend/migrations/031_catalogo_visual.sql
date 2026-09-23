-- Fase 6 do plano-100 — item 6.3 (catálogo visual de aplicativos, E11). Achados #66, #82.
--
-- O catálogo sabia dizer `com.instagram.android 447.0.0` e mais nada. Pacote é identidade técnica: quem olha a
-- tela reconhece NOME e ÍCONE, e o pedido (seção 7) pede os dois. Os dois já estão dentro do APK — o
-- `aapt2 dump badging` devolve `application-label:` e `application-icon-<densidade>:` na mesma leitura que já
-- fazemos para pacote, versão, SDK e ABIs. Não havia onde guardar.
--
-- `label` — o nome que o app mostra ao usuário, lido do base.apk. NULO = release importada antes desta migração
-- (a interface cai no rótulo do registro de apps, e daí no pacote) ou APK que não declara rótulo.
--
-- `icon_file` — nome do arquivo do ícone DENTRO da pasta imutável da release (`icon.png`/`icon.webp`), extraído
-- do próprio APK na importação. Guarda-se o nome, não os bytes: o ícone segue o mesmo caminho dos APKs (pasta
-- do catálogo + storage compartilhado), e uma coluna BLOB faria o banco carregar imagem em toda listagem.
-- NULO = sem ícone servível — inclui o caso comum do ícone adaptativo (`ic_launcher.xml`), que só o Android
-- compõe e que um navegador não abre.
--
-- Nenhuma das duas participa da identidade do conjunto (`set_hash`) nem de `app_release_files`: ícone não se
-- instala. Se entrasse na lista de arquivos, `files_for_install` mandaria um PNG para o `install-multiple`.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE app_releases ADD COLUMN label TEXT;
ALTER TABLE app_releases ADD COLUMN icon_file TEXT;
