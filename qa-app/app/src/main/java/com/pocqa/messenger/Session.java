package com.pocqa.messenger;

import android.content.Context;
import android.content.SharedPreferences;

/** Sessao (conta logada) em SharedPreferences; sobrevive a reinicios do app/emulador. */
public final class Session {

    private static final String PREFS = "session";
    private static final String KEY_ACCOUNT = "account";

    private Session() {
    }

    private static SharedPreferences prefs(Context c) {
        Context app = c.getApplicationContext();
        return (app != null ? app : c).getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** @return conta logada ou {@code null} se nao ha sessao. */
    public static String account(Context c) {
        String a = prefs(c).getString(KEY_ACCOUNT, null);
        return (a == null || a.isEmpty()) ? null : a;
    }

    public static void login(Context c, String account) {
        prefs(c).edit().putString(KEY_ACCOUNT, account).commit();
    }

    public static void clear(Context c) {
        prefs(c).edit().remove(KEY_ACCOUNT).commit();
    }
}
