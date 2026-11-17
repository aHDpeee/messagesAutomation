package com.signalarchive.app;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private static final int PICK_ARCHIVE = 41;
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private ArchiveDb db;
    private TextView status;
    private TextView results;
    private EditText searchInput;
    private Uri lastImportedUri;
    private Button shareButton;

    private int dp(float value) {
        return (int) (value * getResources().getDisplayMetrics().density + 0.5f);
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        db = new ArchiveDb(this);
        String savedUri = getPreferences(MODE_PRIVATE).getString("last_import_uri", null);
        if (savedUri != null) { try { lastImportedUri = Uri.parse(savedUri); } catch (Exception ignored) {} }
        buildUi();
        refreshStatus();
    }

    private TextView label(String text, int size, int color) {
        TextView v = new TextView(this);
        v.setText(text);
        v.setTextSize(size);
        v.setTextColor(color);
        return v;
    }

    private void buildUi() {
        LinearLayout page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(20), dp(28), dp(20), dp(20));
        page.setBackgroundColor(Color.rgb(17, 19, 21));

        TextView brand = label("SIGNAL ARCHIVE  /  0.3.5", 12, Color.rgb(255, 112, 55));
        page.addView(brand);
        TextView title = label("Your archive", 30, Color.WHITE);
        title.setPadding(0, dp(6), 0, dp(2));
        page.addView(title);
        page.addView(label("Offline research workspace", 14, Color.LTGRAY));

        status = label("Loading archive…", 14, Color.WHITE);
        status.setPadding(0, dp(20), 0, dp(16));
        page.addView(status);

        Button importButton = new Button(this);
        importButton.setText("Import archive bundle / JSONL");
        importButton.setOnClickListener(v -> pickSnapshot());
        page.addView(importButton);

        TextView note = label("Choose a desktop export ZIP or messages.jsonl. ZIP bundles are checksum-verified before import. Everything stays on this device.", 13,
                Color.rgb(180, 185, 192));
        note.setPadding(0, dp(10), 0, dp(20));
        page.addView(note);

        shareButton = new Button(this);
        shareButton.setText("Share imported archive…");
        shareButton.setEnabled(lastImportedUri != null);
        shareButton.setOnClickListener(v -> shareImportedArchive());
        page.addView(shareButton);

        Button sourcesButton = new Button(this);
        sourcesButton.setText("Browse sources");
        sourcesButton.setOnClickListener(v -> showSources());
        page.addView(sourcesButton);

        searchInput = new EditText(this);
        searchInput.setSingleLine(true);
        searchInput.setHint("Search messages");
        searchInput.setTextColor(Color.WHITE);
        searchInput.setHintTextColor(Color.GRAY);
        page.addView(searchInput);

        Button searchButton = new Button(this);
        searchButton.setText("Search archive");
        searchButton.setOnClickListener(v -> runSearch());
        page.addView(searchButton);

        results = label("Import a snapshot to start searching.", 14, Color.WHITE);
        results.setPadding(0, dp(18), 0, dp(8));
        ScrollView scroll = new ScrollView(this);
        scroll.addView(results);
        page.addView(scroll, new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.MATCH_PARENT, 0, 1));

        TextView footer = label("LOCAL STORAGE  ·  NO EXTERNAL AI REQUESTS", 10,
                Color.rgb(140, 145, 152));
        footer.setGravity(Gravity.CENTER);
        footer.setPadding(0, dp(14), 0, 0);
        page.addView(footer);
        setContentView(page);
    }

    private void pickSnapshot() {
        Intent intent = new Intent(Intent.ACTION_OPEN_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("*/*");
        intent.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{"application/json", "text/plain", "application/octet-stream", "application/zip", "application/x-zip-compressed"});
        startActivityForResult(intent, PICK_ARCHIVE);
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == PICK_ARCHIVE && resultCode == RESULT_OK && data != null) {
            Uri uri = data.getData();
            if (uri == null) return;
            try { getContentResolver().takePersistableUriPermission(uri, Intent.FLAG_GRANT_READ_URI_PERMISSION); } catch (SecurityException ignored) {}
            lastImportedUri = uri;
            getPreferences(MODE_PRIVATE).edit().putString("last_import_uri", uri.toString()).apply();
            if (shareButton != null) shareButton.setEnabled(true);
            status.setText("Importing selected snapshot…");
            worker.execute(() -> {
                JsonlImporter.Result result = JsonlImporter.importUri(getContentResolver(), uri, db, getCacheDir());
                runOnUiThread(() -> {
                    if (result.error != null) {
                        status.setText("Import failed: " + result.error);
                    } else {
                        status.setText("Import complete" + (result.verifiedBundle ? " · bundle verified" : " · JSONL") +
                                " · " + result.imported + " new · " + result.duplicates +
                                " duplicates · " + result.invalid + " invalid");
                        refreshStatus();
                    }
                });
            });
        }
    }

    private void shareImportedArchive() {
        if (lastImportedUri == null) return;
        String mime = getContentResolver().getType(lastImportedUri);
        if (mime == null || mime.trim().isEmpty()) mime = "application/octet-stream";
        Intent send = new Intent(Intent.ACTION_SEND);
        send.setType(mime);
        send.putExtra(Intent.EXTRA_STREAM, lastImportedUri);
        send.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        startActivity(Intent.createChooser(send, "Share archive"));
    }

    private void refreshStatus() {
        worker.execute(() -> {
            int sources = db.countSources();
            int messages = db.countMessages();
            runOnUiThread(() -> status.setText(sources + " sources  ·  " + messages + " messages"));
        });
    }

    private void showSources() {
        results.setText("Loading sources…");
        worker.execute(() -> {
            List<String> rows = db.listSources();
            StringBuilder text = new StringBuilder("SOURCES  ·  ").append(rows.size()).append("\n\n");
            for (String row : rows) text.append(row).append("\n\n");
            runOnUiThread(() -> results.setText(text.toString()));
        });
    }

    private void runSearch() {
        String query = searchInput.getText().toString().trim();
        if (query.isEmpty()) {
            results.setText("Enter a search term.");
            return;
        }
        results.setText("Searching…");
        worker.execute(() -> {
            List<String> found = db.search(query, 100);
            StringBuilder text = new StringBuilder();
            text.append(found.size()).append(" results shown\n\n");
            for (String row : found) text.append(row).append("\n\n────────────────────\n\n");
            runOnUiThread(() -> results.setText(text.toString()));
        });
    }

    @Override protected void onDestroy() {
        super.onDestroy();
        worker.shutdown();
        if (db != null) db.close();
    }
}
