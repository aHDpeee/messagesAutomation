package com.signalarchive.app;

import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.database.sqlite.SQLiteOpenHelper;

import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

final class ArchiveDb extends SQLiteOpenHelper {
    static final String DB_NAME = "signal-archive.db";
    static final int DB_VERSION = 1;

    ArchiveDb(Context context) { super(context, DB_NAME, null, DB_VERSION); }

    @Override public void onCreate(SQLiteDatabase db) {
        db.execSQL("CREATE TABLE sources (" +
                "source_id TEXT PRIMARY KEY, title TEXT NOT NULL, source_type TEXT NOT NULL DEFAULT 'telegram')");
        db.execSQL("CREATE TABLE messages (" +
                "record_hash TEXT PRIMARY KEY, source_id TEXT NOT NULL, message_id TEXT, " +
                "date TEXT, sender TEXT, body TEXT NOT NULL DEFAULT '', reply_to TEXT, " +
                "raw_json TEXT NOT NULL)");
        db.execSQL("CREATE INDEX idx_messages_source_date ON messages(source_id,date)");
        db.execSQL("CREATE INDEX idx_messages_message_id ON messages(source_id,message_id)");
    }

    @Override public void onUpgrade(SQLiteDatabase db, int oldVersion, int newVersion) {
        // Schema migrations must be explicit; never silently drop an archive.
        throw new IllegalStateException("Unsupported database migration: " + oldVersion + " -> " + newVersion);
    }

    static String sha256(String value) {
        try {
            byte[] bytes = MessageDigest.getInstance("SHA-256").digest(value.getBytes("UTF-8"));
            StringBuilder out = new StringBuilder();
            for (byte b : bytes) out.append(String.format(Locale.ROOT, "%02x", b));
            return out.toString();
        } catch (Exception e) { throw new IllegalStateException(e); }
    }

    long insertMessage(String sourceId, String messageId, String date, String sender,
                       String body, String replyTo, String rawJson) {
        String hash = sha256(sourceId + "\n" + (messageId == null ? "" : messageId) +
                "\n" + date + "\n" + sender + "\n" + body + "\n" + rawJson);
        ContentValues v = new ContentValues();
        v.put("record_hash", hash);
        v.put("source_id", sourceId);
        v.put("message_id", messageId);
        v.put("date", date);
        v.put("sender", sender);
        v.put("body", body);
        v.put("reply_to", replyTo);
        v.put("raw_json", rawJson);
        return getWritableDatabase().insertWithOnConflict("messages", null, v, SQLiteDatabase.CONFLICT_IGNORE);
    }

    void ensureSource(String sourceId, String title, String type) {
        ContentValues v = new ContentValues();
        v.put("source_id", sourceId);
        v.put("title", title);
        v.put("source_type", type);
        getWritableDatabase().insertWithOnConflict("sources", null, v, SQLiteDatabase.CONFLICT_REPLACE);
    }

    List<String> listSources() {
        List<String> rows = new ArrayList<>();
        try (Cursor c = getReadableDatabase().rawQuery(
                "SELECT s.source_id, COUNT(m.record_hash), MAX(m.date) FROM sources s " +
                "LEFT JOIN messages m ON m.source_id=s.source_id " +
                "GROUP BY s.source_id ORDER BY s.source_id COLLATE NOCASE", null)) {
            while (c.moveToNext()) {
                String latest = c.getString(2);
                rows.add(c.getString(0) + "  ·  " + c.getInt(1) + " messages" +
                        (latest == null ? "" : "  ·  latest " + latest));
            }
        }
        return rows;
    }

    int countMessages() {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT COUNT(*) FROM messages", null)) {
            return c.moveToFirst() ? c.getInt(0) : 0;
        }
    }

    int countSources() {
        try (Cursor c = getReadableDatabase().rawQuery("SELECT COUNT(*) FROM sources", null)) {
            return c.moveToFirst() ? c.getInt(0) : 0;
        }
    }

    List<String> search(String query, int limit) {
        List<String> rows = new ArrayList<>();
        String q = "%" + query.replace("%", "\\%").replace("_", "\\_") + "%";
        try (Cursor c = getReadableDatabase().rawQuery(
                "SELECT source_id,date,sender,body FROM messages " +
                "WHERE body LIKE ? ESCAPE '\\' OR sender LIKE ? ESCAPE '\\' " +
                "ORDER BY date DESC LIMIT ?",
                new String[]{q, q, Integer.toString(Math.max(1, Math.min(limit, 200)))})) {
            while (c.moveToNext()) {
                String source = c.getString(0);
                String date = c.getString(1);
                String sender = c.getString(2);
                String body = c.getString(3);
                rows.add(source + " · " + (date == null ? "" : date) + "\n" +
                        (sender == null ? "Unknown sender" : sender) + "\n" +
                        (body == null ? "" : body));
            }
        }
        return rows;
    }
}
