package com.pocqa.messenger;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

/** Banco unico do app: tabelas messages, flags e profile. */
public final class Db extends SQLiteOpenHelper {

    public static final String DB_NAME = "qa_messenger.db";
    private static final int DB_VERSION = 1;

    public static final String T_MESSAGES = "messages";
    public static final String T_FLAGS = "flags";
    public static final String T_PROFILE = "profile";

    private static Db instance;

    public static synchronized Db get(Context c) {
        if (instance == null) {
            Context app = c.getApplicationContext();
            instance = new Db(app != null ? app : c);
        }
        return instance;
    }

    private Db(Context c) {
        super(c, DB_NAME, null, DB_VERSION);
    }

    @Override
    public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE messages("
                + "_id INTEGER PRIMARY KEY AUTOINCREMENT, "
                + "account TEXT, contact TEXT, body TEXT, status TEXT, "
                + "created_at INTEGER, updated_at INTEGER)");
        db.execSQL("CREATE INDEX idx_messages_conv ON messages(account, contact)");
        db.execSQL("CREATE TABLE flags(key TEXT PRIMARY KEY, value TEXT)");
        db.execSQL("CREATE TABLE profile("
                + "account TEXT PRIMARY KEY, name TEXT, email TEXT, city TEXT, "
                + "notifications INTEGER, saved_at INTEGER)");
        // Valores padrao visiveis via provider (o codigo tambem assume estes padroes se a linha faltar).
        putFlag(db, Contract.FLAG_INTERSTITIAL, "0");
        putFlag(db, Contract.FLAG_FORCE_REAUTH, "0");
        putFlag(db, Contract.FLAG_SEND_DELAY_MS, String.valueOf(Contract.DEFAULT_SEND_DELAY_MS));
        putFlag(db, Contract.FLAG_SEND_FAIL, "0");
    }

    @Override
    public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        // Versao unica.
    }

    // ------------------------------------------------------------------ messages

    public static final class Message {
        public long id;
        public String account;
        public String contact;
        public String body;
        public String status;
        public long createdAt;
        public long updatedAt;
    }

    public long insertMessage(String account, String contact, String body, String status, long now) {
        ContentValues v = new ContentValues();
        v.put("account", account);
        v.put("contact", contact);
        v.put("body", body);
        v.put("status", status);
        v.put("created_at", now);
        v.put("updated_at", now);
        return getWritableDatabase().insertOrThrow(T_MESSAGES, null, v);
    }

    /** Transicao atomica de status: so altera se o status atual ainda for {@code from}. */
    public boolean casStatus(long id, String from, String to, long now) {
        ContentValues v = new ContentValues();
        v.put("status", to);
        v.put("updated_at", now);
        int n = getWritableDatabase().update(T_MESSAGES, v, "_id=? AND status=?",
                new String[]{String.valueOf(id), from});
        return n == 1;
    }

    public List<Message> conversation(String account, String contact) {
        return readMessages("account=? AND contact=?", new String[]{account, contact});
    }

    public List<Message> messagesWithStatus(String... statuses) {
        StringBuilder sel = new StringBuilder("status IN (");
        for (int i = 0; i < statuses.length; i++) {
            sel.append(i == 0 ? "?" : ",?");
        }
        sel.append(")");
        return readMessages(sel.toString(), statuses);
    }

    private List<Message> readMessages(String selection, String[] args) {
        List<Message> out = new ArrayList<>();
        try (Cursor c = getReadableDatabase().query(T_MESSAGES,
                new String[]{"_id", "account", "contact", "body", "status", "created_at", "updated_at"},
                selection, args, null, null, "_id ASC")) {
            while (c.moveToNext()) {
                Message m = new Message();
                m.id = c.getLong(0);
                m.account = c.getString(1);
                m.contact = c.getString(2);
                m.body = c.getString(3);
                m.status = c.getString(4);
                m.createdAt = c.getLong(5);
                m.updatedAt = c.getLong(6);
                out.add(m);
            }
        }
        return out;
    }

    /** Ultima mensagem (body) por contato para a conta informada. */
    public Map<String, String> lastBodies(String account) {
        Map<String, String> out = new HashMap<>();
        try (Cursor c = getReadableDatabase().rawQuery(
                "SELECT m.contact, m.body FROM messages m "
                        + "JOIN (SELECT contact, MAX(_id) AS max_id FROM messages WHERE account=? GROUP BY contact) t "
                        + "ON m._id = t.max_id",
                new String[]{account})) {
            while (c.moveToNext()) {
                out.put(c.getString(0), c.getString(1));
            }
        }
        return out;
    }

    // ------------------------------------------------------------------ flags

    private static void putFlag(SQLiteDatabase db, String key, String value) {
        ContentValues v = new ContentValues();
        v.put("key", key);
        v.put("value", value);
        db.insertWithOnConflict(T_FLAGS, null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }

    public void setFlag(String key, String value) {
        putFlag(getWritableDatabase(), key, value);
    }

    public String flag(String key, String def) {
        try (Cursor c = getReadableDatabase().query(T_FLAGS, new String[]{"value"}, "key=?",
                new String[]{key}, null, null, null)) {
            if (c.moveToFirst() && !c.isNull(0)) {
                return c.getString(0).trim();
            }
        }
        return def;
    }

    public boolean flagOn(String key) {
        return "1".equals(flag(key, "0"));
    }

    public long sendDelayMs() {
        try {
            long v = Long.parseLong(flag(Contract.FLAG_SEND_DELAY_MS,
                    String.valueOf(Contract.DEFAULT_SEND_DELAY_MS)));
            return Math.max(0L, Math.min(v, Contract.MAX_SEND_DELAY_MS));
        } catch (NumberFormatException e) {
            return Contract.DEFAULT_SEND_DELAY_MS;
        }
    }

    // ------------------------------------------------------------------ profile

    public static final class Profile {
        public String name = "";
        public String email = "";
        public String city = "";
        public boolean notifications;
        public long savedAt;
    }

    /** @return perfil salvo da conta ou {@code null}. */
    public Profile profile(String account) {
        try (Cursor c = getReadableDatabase().query(T_PROFILE,
                new String[]{"name", "email", "city", "notifications", "saved_at"},
                "account=?", new String[]{account}, null, null, null)) {
            if (!c.moveToFirst()) {
                return null;
            }
            Profile p = new Profile();
            p.name = c.isNull(0) ? "" : c.getString(0);
            p.email = c.isNull(1) ? "" : c.getString(1);
            p.city = c.isNull(2) ? "" : c.getString(2);
            p.notifications = c.getInt(3) != 0;
            p.savedAt = c.getLong(4);
            return p;
        }
    }

    public void saveProfile(String account, String name, String email, String city,
                            boolean notifications, long now) {
        ContentValues v = new ContentValues();
        v.put("account", account);
        v.put("name", name);
        v.put("email", email);
        v.put("city", city);
        v.put("notifications", notifications ? 1 : 0);
        v.put("saved_at", now);
        getWritableDatabase().insertWithOnConflict(T_PROFILE, null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }
}
