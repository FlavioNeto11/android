package com.pocqa.messenger;

import android.app.Application;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;

import java.util.Set;
import java.util.concurrent.CopyOnWriteArraySet;

/**
 * Application: dona do Handler de nivel de processo e da {@link StatusEngine} que avanca o status das
 * mensagens independentemente das Activities. Tambem distribui avisos de "dados mudaram" para as telas.
 */
public class QaApp extends Application {

    private static final Handler MAIN = new Handler(Looper.getMainLooper());
    private static final Set<Runnable> LISTENERS = new CopyOnWriteArraySet<>();
    private static StatusEngine engine;

    @Override
    public void onCreate() {
        super.onCreate();
        // Cria a engine e recupera transicoes pendentes de uma execucao anterior do processo.
        engine(this);
    }

    /** Acesso preguicoso: o ContentProvider pode ser chamado antes de Application.onCreate(). */
    static synchronized StatusEngine engine(Context c) {
        if (engine == null) {
            engine = new StatusEngine(Db.get(c), MAIN);
            engine.recover();
        }
        return engine;
    }

    static void addListener(Runnable r) {
        LISTENERS.add(r);
    }

    static void removeListener(Runnable r) {
        LISTENERS.remove(r);
    }

    /** Pode ser chamado de qualquer thread; os ouvintes rodam na thread principal. */
    static void notifyDataChanged() {
        MAIN.post(() -> {
            for (Runnable r : LISTENERS) {
                r.run();
            }
        });
    }
}
