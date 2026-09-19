package com.pocqa.messenger;

import java.text.SimpleDateFormat;
import java.util.Arrays;
import java.util.Collections;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Constantes do "contrato" do app de QA (status, contatos, contas ficticias, flags).
 * Os simbolos especiais sao montados a partir do code point (constantes de compilacao),
 * para nao depender da codificacao do arquivo-fonte.
 */
public final class Contract {

    private Contract() {
    }

    private static final char ELLIPSIS = (char) 0x2026; // reticencias
    private static final char CHECK = (char) 0x2713;    // check
    private static final char CROSS = (char) 0x2715;    // xis

    // ---- Status das mensagens (strings exatas gravadas em messages.status) ----
    /** "Enviando" + U+2026. */
    public static final String STATUS_SENDING = "Enviando" + ELLIPSIS;
    /** "Enviada " + U+2713. */
    public static final String STATUS_SENT = "Enviada " + CHECK;
    /** "Entregue " + U+2713 U+2713. */
    public static final String STATUS_DELIVERED = "Entregue " + CHECK + CHECK;
    /** "Lida " + U+2713 U+2713. */
    public static final String STATUS_READ = "Lida " + CHECK + CHECK;
    /** "Falha no envio " + U+2715. */
    public static final String STATUS_FAILED = "Falha no envio " + CROSS;

    // ---- Tempos das transicoes ----
    public static final long DEFAULT_SEND_DELAY_MS = 700L;
    public static final long DELIVER_DELAY_MS = 2000L;
    public static final long READ_DELAY_MS = 3000L;
    /** Linhas "Enviando" mais antigas que isto sao avancadas ao reiniciar o processo. */
    public static final long STALE_SENDING_MS = 10_000L;
    public static final long MAX_SEND_DELAY_MS = 600_000L;

    // ---- Contatos fixos (nesta ordem) ----
    public static final List<String> CONTACTS = Collections.unmodifiableList(Arrays.asList(
            "Suporte QA", "QA-003", "QA-002", "QA-001", "Equipe Testes", "QA-010", "QA-011", "Arquivo"));
    /** Contato que nunca passa de "Enviada". */
    public static final String CONTACT_NEVER_DELIVERED = "QA-002";
    /** Contato cujas mensagens chegam a "Lida". */
    public static final String CONTACT_READS = "QA-003";

    // ---- Contas FICTICIAS de teste: qa-user-01 a qa-user-99, PIN 1234 para todas ----
    // Era 01..10, do tempo em que o parque tinha 10 vagas. Com aparelhos em OUTRAS maquinas o numero de
    // instancias passou de 10, e a recusa aparecia como "account=" vazio no provisionamento, sem erro nenhum.
    private static final Pattern ACCOUNT_PATTERN = Pattern.compile("qa-user-(0[1-9]|[1-9][0-9])");
    private static final String TEST_PIN = "1234";

    // ---- Flags de injecao de falha (tabela flags) ----
    public static final String FLAG_INTERSTITIAL = "interstitial";
    public static final String FLAG_FORCE_REAUTH = "force_reauth";
    public static final String FLAG_SEND_DELAY_MS = "send_delay_ms";
    public static final String FLAG_SEND_FAIL = "send_fail";

    // ---- Extras de intent ----
    public static final String EXTRA_ACCOUNT = "account";
    public static final String EXTRA_PIN = "pin";
    public static final String EXTRA_NOTICE = "notice";
    public static final String NOTICE_EXPIRED = "expired";
    public static final String EXTRA_CONTACT = "contact";

    public static String normalizeAccount(String raw) {
        return raw == null ? "" : raw.trim().toLowerCase(Locale.ROOT);
    }

    /** Valida credenciais ficticias. {@code account} ja deve estar normalizada. */
    public static boolean credentialsValid(String account, String pin) {
        return account != null && pin != null
                && ACCOUNT_PATTERN.matcher(account).matches()
                && TEST_PIN.equals(pin.trim());
    }

    public static String hhmm(long epochMs) {
        return new SimpleDateFormat("HH:mm", Locale.ROOT).format(new Date(epochMs));
    }
}
