package com.pocqa.messenger;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.content.UriMatcher;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.database.sqlite.SQLiteDatabase;
import android.net.Uri;

/**
 * Provider EXPORTADO e SEM permissao, de proposito: este app existe apenas para QA. Ele permite a um
 * verificador independente (adb shell content ...) ler o estado real do app e injetar falhas.
 *
 *   content://com.pocqa.messenger.provider/messages  query, delete
 *   content://com.pocqa.messenger.provider/flags     query, insert (UPSERT por key), update, delete
 *   content://com.pocqa.messenger.provider/session   query (uma linha, coluna account; "" se deslogado)
 *   content://com.pocqa.messenger.provider/profile   query, delete
 */
public class QaProvider extends ContentProvider {

    public static final String AUTHORITY = "com.pocqa.messenger.provider";

    private static final int MESSAGES = 1;
    private static final int FLAGS = 2;
    private static final int SESSION = 3;
    private static final int PROFILE = 4;

    private static final UriMatcher MATCHER = new UriMatcher(UriMatcher.NO_MATCH);

    static {
        MATCHER.addURI(AUTHORITY, "messages", MESSAGES);
        MATCHER.addURI(AUTHORITY, "flags", FLAGS);
        MATCHER.addURI(AUTHORITY, "session", SESSION);
        MATCHER.addURI(AUTHORITY, "profile", PROFILE);
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    private Context ctx() {
        Context c = getContext();
        if (c == null) {
            throw new IllegalStateException("provider sem contexto");
        }
        return c;
    }

    private Db db() {
        return Db.get(ctx());
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs,
                        String sortOrder) {
        switch (MATCHER.match(uri)) {
            case MESSAGES:
                // Garante que transicoes ja vencidas estejam gravadas antes de responder.
                QaApp.engine(ctx()).processDue();
                return db().getReadableDatabase().query(Db.T_MESSAGES, projection, selection,
                        selectionArgs, null, null, isEmpty(sortOrder) ? "_id ASC" : sortOrder);
            case FLAGS:
                return db().getReadableDatabase().query(Db.T_FLAGS, projection, selection,
                        selectionArgs, null, null, isEmpty(sortOrder) ? "key ASC" : sortOrder);
            case PROFILE:
                return db().getReadableDatabase().query(Db.T_PROFILE, projection, selection,
                        selectionArgs, null, null, isEmpty(sortOrder) ? "account ASC" : sortOrder);
            case SESSION: {
                MatrixCursor c = new MatrixCursor(new String[]{"account"}, 1);
                String account = Session.account(ctx());
                c.addRow(new Object[]{account == null ? "" : account});
                return c;
            }
            default:
                throw new IllegalArgumentException("URI desconhecida: " + uri);
        }
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        if (MATCHER.match(uri) != FLAGS) {
            throw new UnsupportedOperationException("insert so e suportado em /flags: " + uri);
        }
        String key = values == null ? null : values.getAsString("key");
        if (key == null || key.trim().isEmpty()) {
            throw new IllegalArgumentException("insert em /flags exige os valores key e value");
        }
        String value = values.getAsString("value");
        db().setFlag(key.trim(), value == null ? "" : value); // UPSERT
        QaApp.notifyDataChanged();
        return uri.buildUpon().appendPath(key.trim()).build();
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        if (MATCHER.match(uri) != FLAGS) {
            throw new UnsupportedOperationException("update so e suportado em /flags: " + uri);
        }
        if (values == null || !values.containsKey("value")) {
            throw new IllegalArgumentException("update em /flags exige o valor value");
        }
        ContentValues v = new ContentValues();
        v.put("value", values.getAsString("value"));
        int n = db().getWritableDatabase().update(Db.T_FLAGS, v, selection, selectionArgs);
        QaApp.notifyDataChanged();
        return n;
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        String table;
        switch (MATCHER.match(uri)) {
            case MESSAGES:
                table = Db.T_MESSAGES;
                break;
            case FLAGS:
                table = Db.T_FLAGS;
                break;
            case PROFILE:
                table = Db.T_PROFILE;
                break;
            default:
                throw new UnsupportedOperationException("delete nao suportado: " + uri);
        }
        SQLiteDatabase w = db().getWritableDatabase();
        // "1" como whereClause faz o SQLite devolver a contagem de linhas apagadas.
        int n = w.delete(table, isEmpty(selection) ? "1" : selection, selectionArgs);
        QaApp.notifyDataChanged();
        return n;
    }

    @Override
    public String getType(Uri uri) {
        switch (MATCHER.match(uri)) {
            case MESSAGES:
                return "vnd.android.cursor.dir/vnd." + AUTHORITY + ".messages";
            case FLAGS:
                return "vnd.android.cursor.dir/vnd." + AUTHORITY + ".flags";
            case SESSION:
                return "vnd.android.cursor.item/vnd." + AUTHORITY + ".session";
            case PROFILE:
                return "vnd.android.cursor.dir/vnd." + AUTHORITY + ".profile";
            default:
                return null;
        }
    }

    private static boolean isEmpty(String s) {
        return s == null || s.trim().isEmpty();
    }
}
