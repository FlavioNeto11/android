-- Identidade FÍSICA do aparelho por trás do id lógico (item 3.2 do plano-100; achados #76, #111).
--
-- O defeito, do jeito que doía: tudo o que o central afirmava sobre o disco de um aparelho — app instalado,
-- sessão do Instagram, evidência de conta — era gravado por `instance_id` LÓGICO, e nada disso era invalidado
-- quando o aparelho por baixo daquele id mudava. Em 21/09/2026 o painel dizia que o Instagram estava `ready` em
-- android-09 e android-10 com `verified_at` de 18/09: aquelas linhas foram escritas quando esses ids eram
-- emuladores desta máquina, um dia ANTES de os ids serem remapeados para AVDs do notebook. A porta do app
-- deixava a tarefa passar, e o Instagram nem estava instalado lá.
--
-- `physical_id` é a impressão digital do aparelho, não um serial: `ro.serialno` NÃO serve (medido:
-- 'EMULATOR37X1X11X0' idêntico em android-09 e android-15). O que identifica é a máquina que hospeda mais o
-- nome do AVD/modelo lidos de dentro, e o `android_id` quando dá para lê-lo. Nulo = ainda não se observou, e o
-- que não se sabe NUNCA invalida nada.
--
-- `serial` guarda o endereço de ADB do momento em que a identidade foi lida — é o que o operador reconhece no
-- cartão, e o que deixa "este id mudou de aparelho" legível no histórico.
--
-- Sem BEGIN/COMMIT: o executor de migrações já abre a transação.

ALTER TABLE instances ADD COLUMN physical_id TEXT;      -- impressão digital observada do aparelho
ALTER TABLE instances ADD COLUMN physical_id_at TEXT;   -- quando foi observada
ALTER TABLE instances ADD COLUMN observed_serial TEXT;  -- endereço de ADB no momento da observação
