package com.pocqa.messenger;

import android.os.Handler;
import android.os.SystemClock;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Iterator;
import java.util.List;
import java.util.Map;

/**
 * Maquina de estados do status das mensagens. Vive no nivel do processo (criada por {@link QaApp}),
 * nunca em uma Activity: as transicoes continuam mesmo com a tela fechada e sao persistidas no banco.
 *
 * Regras:
 *   Enviando -> (send_delay_ms, padrao 700 ms) -> Enviada | Falha no envio (se send_fail=1 no momento do envio)
 *   Enviada  -> (+2000 ms) -> Entregue      [exceto contato QA-002, que fica em Enviada para sempre]
 *   Entregue -> (+3000 ms) -> Lida          [somente contato QA-003]
 *
 * Ao reiniciar o processo, {@link #recover()} retoma as cadeias interrompidas; linhas "Enviando"
 * com mais de 10 s sao avancadas imediatamente (send_fail e avaliada no momento da recuperacao).
 */
final class StatusEngine {

    private static final class Step {
        final long id;
        final String contact;
        final String from;
        final String to;
        final long dueAt; // SystemClock.elapsedRealtime()

        Step(long id, String contact, String from, String to, long dueAt) {
            this.id = id;
            this.contact = contact;
            this.from = from;
            this.to = to;
            this.dueAt = dueAt;
        }
    }

    private final Db db;
    private final Handler handler;
    private final Map<Long, Step> pending = new HashMap<>();
    private final Runnable tick = this::processDue;

    StatusEngine(Db db, Handler mainHandler) {
        this.db = db;
        this.handler = mainHandler;
    }

    /** Chamado logo apos inserir a linha "Enviando". As flags sao lidas neste momento (o do envio). */
    synchronized void onMessageSent(long id, String contact) {
        long delay = db.sendDelayMs();
        boolean fail = db.flagOn(Contract.FLAG_SEND_FAIL);
        schedule(new Step(id, contact, Contract.STATUS_SENDING,
                fail ? Contract.STATUS_FAILED : Contract.STATUS_SENT,
                SystemClock.elapsedRealtime() + delay));
    }

    private synchronized void schedule(Step s) {
        pending.put(s.id, s);
        long wait = Math.max(0L, s.dueAt - SystemClock.elapsedRealtime());
        handler.postDelayed(tick, wait + 5L);
    }

    /**
     * Aplica todas as transicoes vencidas. Chamado pelo Handler e tambem pelo ContentProvider antes de
     * consultar messages (assim o verificador nunca ve um status atrasado, mesmo se o processo esteve congelado).
     */
    synchronized void processDue() {
        long now = SystemClock.elapsedRealtime();
        List<Step> due = new ArrayList<>();
        for (Iterator<Step> it = pending.values().iterator(); it.hasNext(); ) {
            Step s = it.next();
            if (s.dueAt <= now) {
                due.add(s);
                it.remove();
            }
        }
        boolean changed = false;
        for (Step s : due) {
            if (db.casStatus(s.id, s.from, s.to, System.currentTimeMillis())) {
                changed = true;
                Step next = nextStep(s.id, s.contact, s.to, Math.max(now, s.dueAt), 0L);
                if (next != null) {
                    schedule(next);
                }
            }
        }
        if (changed) {
            QaApp.notifyDataChanged();
        }
    }

    /** Proximo passo da cadeia a partir de {@code current}, ou null se o status e final para o contato. */
    private static Step nextStep(long id, String contact, String current, long baseElapsed, long alreadyWaited) {
        if (Contract.STATUS_SENT.equals(current) && !Contract.CONTACT_NEVER_DELIVERED.equals(contact)) {
            long wait = Math.max(0L, Contract.DELIVER_DELAY_MS - alreadyWaited);
            return new Step(id, contact, Contract.STATUS_SENT, Contract.STATUS_DELIVERED, baseElapsed + wait);
        }
        if (Contract.STATUS_DELIVERED.equals(current) && Contract.CONTACT_READS.equals(contact)) {
            long wait = Math.max(0L, Contract.READ_DELAY_MS - alreadyWaited);
            return new Step(id, contact, Contract.STATUS_DELIVERED, Contract.STATUS_READ, baseElapsed + wait);
        }
        return null;
    }

    /** Retoma, no inicio do processo, as cadeias que ficaram pela metade. */
    synchronized void recover() {
        long nowWall = System.currentTimeMillis();
        long nowElapsed = SystemClock.elapsedRealtime();
        List<Db.Message> open = db.messagesWithStatus(
                Contract.STATUS_SENDING, Contract.STATUS_SENT, Contract.STATUS_DELIVERED);
        for (Db.Message m : open) {
            if (pending.containsKey(m.id)) {
                continue;
            }
            Step step;
            if (Contract.STATUS_SENDING.equals(m.status)) {
                long age = Math.max(0L, nowWall - m.createdAt);
                long wait = age >= Contract.STALE_SENDING_MS
                        ? 0L
                        : Math.min(db.sendDelayMs(), Contract.STALE_SENDING_MS - age);
                boolean fail = db.flagOn(Contract.FLAG_SEND_FAIL);
                step = new Step(m.id, m.contact, Contract.STATUS_SENDING,
                        fail ? Contract.STATUS_FAILED : Contract.STATUS_SENT, nowElapsed + wait);
            } else {
                long waited = Math.max(0L, nowWall - m.updatedAt);
                step = nextStep(m.id, m.contact, m.status, nowElapsed, waited);
            }
            if (step != null) {
                schedule(step);
            }
        }
        processDue();
    }
}
