package com.signalarchive.app;

import android.content.ContentResolver;
import android.net.Uri;
import android.database.sqlite.SQLiteDatabase;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.security.DigestInputStream;
import java.security.MessageDigest;
import java.util.Locale;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;

final class JsonlImporter {
    static final class Result {
        int imported;
        int duplicates;
        int invalid;
        String error;
        boolean verifiedBundle;
    }

    static Result importUri(ContentResolver resolver, Uri uri, ArchiveDb db, File cacheDir) {
        Result result = new Result();
        File staged = null;
        try {
            String name = resolver.getType(uri);
            // Stage the selected document once so ZIP central-directory validation is possible.
            staged = File.createTempFile("signal-import-", ".bin", cacheDir);
            try (InputStream in = resolver.openInputStream(uri);
                 FileOutputStream out = new FileOutputStream(staged)) {
                if (in == null) throw new IllegalArgumentException("Unable to open selected file");
                byte[] buffer = new byte[64 * 1024];
                long total = 0;
                int n;
                while ((n = in.read(buffer)) != -1) {
                    total += n;
                    if (total > 2L * 1024 * 1024 * 1024)
                        throw new IllegalArgumentException("Import stopped: file exceeds 2 GiB limit");
                    out.write(buffer, 0, n);
                }
            }
            if (isZip(staged)) importBundle(staged, db, result);
            else try (InputStream in = new FileInputStream(staged)) { importJsonl(in, db, result); }
        } catch (Exception e) {
            result.error = e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage();
        } finally {
            if (staged != null) staged.delete();
        }
        return result;
    }

    private static boolean isZip(File file) throws Exception {
        try (InputStream in = new FileInputStream(file)) {
            int a = in.read(), b = in.read();
            return a == 'P' && b == 'K';
        }
    }

    private static void importBundle(File file, ArchiveDb db, Result result) throws Exception {
        try (ZipFile zip = new ZipFile(file)) {
            ZipEntry manifestEntry = findEntry(zip, "manifest.json");
            ZipEntry messagesEntry = findEntry(zip, "messages.jsonl");
            if (manifestEntry == null || messagesEntry == null)
                throw new IllegalArgumentException("ZIP must contain manifest.json and messages.jsonl");
            if (messagesEntry.getSize() > 2L * 1024 * 1024 * 1024)
                throw new IllegalArgumentException("messages.jsonl exceeds 2 GiB safety limit");
            JSONObject manifest;
            try (InputStream in = zip.getInputStream(manifestEntry)) {
                manifest = new JSONObject(readLimited(in, 2 * 1024 * 1024));
            }
            if (!"signal-archive-export".equals(manifest.optString("format")))
                throw new IllegalArgumentException("Unsupported archive bundle format");
            JSONObject files = manifest.optJSONObject("files");
            JSONObject expected = files == null ? null : files.optJSONObject("messages.jsonl");
            if (expected == null) throw new IllegalArgumentException("Bundle manifest has no messages.jsonl checksum");
            long expectedBytes = expected.optLong("bytes", -1);
            String expectedHash = expected.optString("sha256", "");
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            long actualBytes = 0;
            try (InputStream raw = zip.getInputStream(messagesEntry);
                 DigestInputStream checked = new DigestInputStream(raw, digest)) {
                byte[] buffer = new byte[64 * 1024];
                int n;
                while ((n = checked.read(buffer)) != -1) {
                    actualBytes += n;
                    if (actualBytes > 2L * 1024 * 1024 * 1024)
                        throw new IllegalArgumentException("messages.jsonl exceeds 2 GiB safety limit");
                }
            }
            String actualHash = hex(digest.digest());
            if ((expectedBytes >= 0 && actualBytes != expectedBytes) || !expectedHash.equalsIgnoreCase(actualHash))
                throw new IllegalArgumentException("messages.jsonl integrity check failed; import rejected");
            try (InputStream in = zip.getInputStream(messagesEntry)) { importJsonl(in, db, result); }
            result.verifiedBundle = true;
        }
    }

    private static ZipEntry findEntry(ZipFile zip, String suffix) {
        java.util.Enumeration<? extends ZipEntry> entries = zip.entries();
        while (entries.hasMoreElements()) {
            ZipEntry entry = entries.nextElement();
            if (!entry.isDirectory() && (entry.getName().equals(suffix) || entry.getName().endsWith("/" + suffix)))
                return entry;
        }
        return null;
    }

    private static String readLimited(InputStream in, int limit) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int n;
        while ((n = in.read(buffer)) != -1) {
            if (out.size() + n > limit) throw new IllegalArgumentException("Manifest is too large");
            out.write(buffer, 0, n);
        }
        return out.toString("UTF-8");
    }

    private static void importJsonl(InputStream input, ArchiveDb db, Result result) throws Exception {
        long totalChars = 0;
        final long MAX_FILE_CHARS = 2L * 1024 * 1024 * 1024;
        final int MAX_LINE_CHARS = 5 * 1024 * 1024;
        try (BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
            SQLiteDatabase sql = db.getWritableDatabase();
            sql.beginTransaction();
            try {
                String line;
                while ((line = reader.readLine()) != null) {
                    totalChars += line.length();
                    if (totalChars > MAX_FILE_CHARS) throw new IllegalArgumentException("JSONL exceeds 2 GiB safety limit");
                    if (line.length() > MAX_LINE_CHARS) { result.invalid++; continue; }
                    if (line.trim().isEmpty()) continue;
                    try {
                        JSONObject obj = new JSONObject(line);
                        String sourceId = first(obj, "source_id", "chat_id", "source");
                        if (sourceId.isEmpty()) sourceId = "imported";
                        String messageId = first(obj, "external_message_id", "message_id", "id");
                        String date = first(obj, "date", "date_unixtime", "timestamp");
                        String sender = first(obj, "sender", "from", "from_name");
                        String body = first(obj, "text", "message");
                        String reply = first(obj, "reply_to", "reply_to_message_id");
                        db.ensureSource(sourceId, sourceId, "imported");
                        long inserted = db.insertMessage(sourceId, emptyToNull(messageId), date,
                                sender, body, emptyToNull(reply), obj.toString());
                        if (inserted == -1) result.duplicates++; else result.imported++;
                    } catch (Exception parseError) { result.invalid++; }
                }
                sql.setTransactionSuccessful();
            } finally { sql.endTransaction(); }
        }
    }

    private static String first(JSONObject obj, String... keys) {
        for (String key : keys) {
            Object value = obj.opt(key);
            if (value != null && value != JSONObject.NULL) {
                if (value instanceof String) return (String) value;
                if (value instanceof JSONObject) {
                    JSONObject nested = (JSONObject) value;
                    String name = nested.optString("name", "");
                    return name.isEmpty() ? nested.toString() : name;
                }
                return String.valueOf(value);
            }
        }
        return "";
    }

    private static String emptyToNull(String value) { return value == null || value.isEmpty() ? null : value; }
    private static String hex(byte[] bytes) {
        StringBuilder out = new StringBuilder();
        for (byte b : bytes) out.append(String.format(Locale.ROOT, "%02x", b));
        return out.toString();
    }
}
