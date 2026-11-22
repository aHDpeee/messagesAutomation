package com.signalarchive.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.Editable
import android.text.TextWatcher
import android.view.View
import android.view.ViewGroup
import android.view.GestureDetector
import android.view.MotionEvent
import android.graphics.drawable.GradientDrawable
import android.widget.BaseAdapter
import android.widget.Button
import android.widget.CheckBox
import android.widget.ArrayAdapter
import android.widget.AutoCompleteTextView
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.HorizontalScrollView
import android.widget.LinearLayout
import android.widget.ListView
import android.widget.ScrollView
import android.widget.TextView
import java.io.File
import java.nio.charset.StandardCharsets
import java.util.concurrent.Executors
import org.json.JSONObject

/** Locally managed chat catalog; Telegram synchronization is provided separately. */
class CatalogActivity : BackSwipeActivity() {
    override val emptySpaceBackSwipeEnabled = false
    private val inactiveTab = "\u0000inactive"
    private val allTab = "\u0000all"
    private val worker = Executors.newSingleThreadExecutor()
    @Volatile private var alive = true
    private lateinit var db: ArchiveDb
    private lateinit var store: ChatCatalog
    private lateinit var rows: MutableList<MutableMap<String, String>>
    private lateinit var list: ListView
    private var visibleRows: List<MutableMap<String, String>> = emptyList()
    private val chatAdapter = object : BaseAdapter() {
        override fun getCount() = visibleRows.size
        override fun getItem(position: Int) = visibleRows[position]
        override fun getItemId(position: Int) = position.toLong()
        override fun getView(position: Int, convertView: View?, parent: ViewGroup): View {
            val line = (convertView as? LinearLayout) ?: LinearLayout(this@CatalogActivity).apply {
                orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL
                addView(TextView(this@CatalogActivity).apply { gravity = android.view.Gravity.CENTER; textSize = 20f }, LinearLayout.LayoutParams(dp(48), dp(48)))
                addView(Button(this@CatalogActivity), LinearLayout.LayoutParams(0, -2, 1f))
                addView(CheckBox(this@CatalogActivity))
                addView(Button(this@CatalogActivity).apply { text = "⋮"; minWidth = 0; setPadding(0, 0, 0, 0) }, LinearLayout.LayoutParams(dp(40), dp(56)))
            }
            val avatar = line.getChildAt(0) as TextView
            val checkbox = line.getChildAt(2) as CheckBox
            checkbox.buttonTintList = android.content.res.ColorStateList.valueOf(Appearance.color(this@CatalogActivity))
            val button = line.getChildAt(1) as Button
            val settingsButton = line.getChildAt(3) as Button
            val row = getItem(position)
            val pending = row["type"] == "pending"
            val group = row["blocks"].orEmpty()
            checkbox.visibility = if (selectedGroup == allTab && !pending) View.VISIBLE else View.GONE
            val availability = getSharedPreferences("dialog_availability", MODE_PRIVATE)
            val account = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
            val snapshot = if (account != null && availability.getString("account_id", null) == account) availability.getStringSet("ids", null) else null
            val note = if (!pending && DialogAvailability.unavailable(row["id"].orEmpty(), snapshot)) " · недоступен при обновлении" else ""
            val username = row["username"].orEmpty().removePrefix("@").takeIf { it.isNotBlank() }?.let { "@$it" }
            val kind = when (row["type"]) { "group" -> "Группа"; "channel" -> "Канал"; "bot" -> "Бот"; else -> "Личный чат" }
            val lastDate = runCatching { java.time.Instant.parse(row["last_message_date"].orEmpty()).atZone(java.time.ZoneId.systemDefault()).toLocalDate() }.getOrNull()
            val dateLabel = lastDate?.takeIf { it.isBefore(java.time.LocalDate.now()) }?.format(java.time.format.DateTimeFormatter.ofPattern("dd.MM.yyyy"))
            val subtitle = if (pending) "Запрос отправлен" else (username ?: kind) + (dateLabel?.let { " · $it" } ?: "") + note
            val title = row["title"].orEmpty()
            button.text = android.text.SpannableString("$title\n$subtitle").apply {
                setSpan(android.text.style.ForegroundColorSpan(Color.LTGRAY), title.length + 1, length, android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
                setSpan(android.text.style.RelativeSizeSpan(0.82f), title.length + 1, length, android.text.Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
            }
            button.isAllCaps = false
            button.gravity = android.view.Gravity.CENTER_VERTICAL or android.view.Gravity.START
            val pendingLink = pending && row["link"].orEmpty().startsWith("https://", true) && TelegramLink.from(row) != null
            button.setTextColor(if (pendingLink) Color.rgb(255, 107, 107) else Color.WHITE)
            button.backgroundTintList = null
            button.setBackgroundColor(Color.TRANSPARENT); button.setPadding(dp(12), dp(6), dp(4), dp(6)); button.minHeight = dp(64)
            avatar.text = title.trim().firstOrNull()?.uppercase().orEmpty().ifEmpty { "•" }
            val accent = Appearance.color(this@CatalogActivity)
            avatar.setTextColor(accent)
            avatar.background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(Color.rgb((Color.red(accent) + 45) / 5, (Color.green(accent) + 45) / 5, (Color.blue(accent) + 45) / 5)) }
            settingsButton.isAllCaps = false; settingsButton.setTextColor(Color.LTGRAY)
            settingsButton.backgroundTintList = null; settingsButton.setBackgroundColor(Color.TRANSPARENT)
            settingsButton.visibility = if (selectedGroup == allTab) View.GONE else View.VISIBLE
            checkbox.setOnCheckedChangeListener(null)
            checkbox.isChecked = group.isNotBlank()
            checkbox.contentDescription = "Включить ${row["title"]} в экспорт"
            checkbox.setOnCheckedChangeListener { _, checked ->
                try {
                    val target = if (selectedGroup == inactiveTab || selectedGroup == allTab) "Ungrouped" else selectedGroup
                    val updated = BlockEdits.assign(rows, row["id"].orEmpty(), if (checked) target else null)
                    saveRows(updated); rows = updated; render()
                } catch (error: Exception) { render(); message("Не удалось сохранить выбор: ${error.message}") }
            }
            button.contentDescription = "Открыть ${row["title"]} в Telegram"
            settingsButton.contentDescription = "Настройки и экспорт чата ${row["title"]}"
            settingsButton.text = if (selectedGroup == inactiveTab && !pending) "✕" else "⋮"
            settingsButton.contentDescription = if (selectedGroup == inactiveTab && !pending) "Удалить или покинуть ${row["title"]}" else "Настройки и экспорт чата ${row["title"]}"
            settingsButton.setOnClickListener { if (pending) startActivity(Intent(this@CatalogActivity, InactiveChatsActivity::class.java)) else if (selectedGroup == inactiveTab) confirmRemoveDialog(row) else editGroup(row) }
            fun open() {
                val link = TelegramLink.from(row)
                if (link != null) {
                    try { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link))) }
                    catch (_: Exception) { message("Не удалось открыть ссылку Telegram") }
                } else if (pending) startActivity(Intent(this@CatalogActivity, InactiveChatsActivity::class.java))
                else message("Для этого чата нет публичной ссылки")
            }
            val gestures = GestureDetector(this@CatalogActivity, object : GestureDetector.SimpleOnGestureListener() {
                override fun onDown(e: MotionEvent): Boolean = true
                override fun onSingleTapConfirmed(e: MotionEvent): Boolean { open(); return true }
                override fun onDoubleTap(e: MotionEvent): Boolean { if (pending) startActivity(Intent(this@CatalogActivity, InactiveChatsActivity::class.java)) else editGroup(row); return true }
                override fun onLongPress(e: MotionEvent) { if (pending) startActivity(Intent(this@CatalogActivity, InactiveChatsActivity::class.java)) else showChatActions(row) }
                override fun onFling(first: MotionEvent?, last: MotionEvent, velocityX: Float, velocityY: Float): Boolean = handleSwipe(first, last, velocityX)
            })
            button.setOnClickListener { open() }
            button.setOnTouchListener { _, event -> gestures.onTouchEvent(event); true }
            return line
        }
    }
    private lateinit var status: TextView
    private lateinit var search: EditText
    private lateinit var tabs: LinearLayout
    private lateinit var greeting: TextView
    private lateinit var screenRoot: FrameLayout
    private var menuOverlay: FrameLayout? = null
    private lateinit var scheduleSummary: TextView
    private lateinit var exportProgress: android.widget.ProgressBar
    private lateinit var exportProgressText: TextView
    private val summaryHandler = Handler(Looper.getMainLooper())
    private val summaryTicker = object : Runnable {
        override fun run() {
            if (!alive) return
            val prefs = ExportSchedule.prefs(this@CatalogActivity)
            val next = if (prefs.getBoolean("enabled", false)) prefs.getString("next_due", null)?.let { runCatching { java.time.Instant.parse(it) }.getOrNull() } else null
            val value = ScheduleSummary.text(java.time.Instant.now(), next, ExportHistory.latest(this@CatalogActivity))
            scheduleSummary.text = value
            scheduleSummary.visibility = if (value.isEmpty()) View.GONE else View.VISIBLE
            val progress = ExportProgress.snapshot()
            exportProgress.visibility = if (progress == null) View.GONE else View.VISIBLE
            exportProgressText.visibility = if (progress == null) View.GONE else View.VISIBLE
            if (progress != null) {
                exportProgress.max = progress.total.coerceAtLeast(1)
                exportProgress.progress = progress.total - progress.remaining
                exportProgressText.text = "Осталось ${progress.label}: ${progress.remaining}"
            }
            summaryHandler.postDelayed(this, 1000)
        }
    }
    private var greetingRequested = false
    @Volatile private var refreshRunning = false
    private var refreshStartX = 0f
    private var refreshStartY = 0f
    private var refreshPull = false
    private var selectedGroup = "Ungrouped"
    private var loaded = true
    private var catalogVersion = "missing"
    private fun saveRows(input: List<Map<String, String>>, allowInvalidReplacement: Boolean = false) {
        check(loaded || allowInvalidReplacement) { "Сначала восстановите каталог из CSV или копии" }
        catalogVersion = store.save(input, expectedVersion = catalogVersion)
    }
    private fun dp(v: Int) = (v * resources.displayMetrics.density + .5f).toInt()
    private fun message(text: String) { status.text = text }
    private fun refreshTelegram(force: Boolean) {
        if (!loaded) { if (force) android.widget.Toast.makeText(this, "Каталог недоступен: восстановите CSV в настройках", android.widget.Toast.LENGTH_LONG).show(); return }
        if (refreshRunning) { if (force) android.widget.Toast.makeText(this, "Обновление уже выполняется", android.widget.Toast.LENGTH_SHORT).show(); return }
        val prefs = getSharedPreferences("catalog_refresh", MODE_PRIVATE)
        val elapsed = System.currentTimeMillis() - prefs.getLong("last_attempt", 0L)
        val minimum = if (force) 60_000L else 15 * 60_000L
        if (elapsed in 0 until minimum) {
            if (force) android.widget.Toast.makeText(this, "Недавнее обновление: попробуйте через ${(minimum - elapsed + 999) / 1000} сек.", android.widget.Toast.LENGTH_SHORT).show()
            return
        }
        val credentials = runCatching { SecureCredentialStore(this).load() }.getOrNull() ?: run {
            if (force) android.widget.Toast.makeText(this, "Сначала подключите Telegram в меню аккаунта", android.widget.Toast.LENGTH_LONG).show()
            return
        }
        refreshRunning = true
        prefs.edit().putLong("last_attempt", System.currentTimeMillis()).apply()
        if (force) android.widget.Toast.makeText(this, "Обновляем список чатов…", android.widget.Toast.LENGTH_SHORT).show()
        worker.execute {
            try {
                val p = JSONObject().put("api_id", credentials.apiId).put("api_hash", credentials.apiHash)
                val account = TelegramBridge.runJob(applicationContext, "account.status", p)
                check(account.optString("status") == "authenticated") { "Сначала войдите в Telegram" }
                val accountId = account.optJSONObject("account")?.optString("telegram_id").orEmpty()
                val catalog = ChatCatalog(filesDir.resolve("chats.csv"))
                val (current, version) = catalog.loadVersioned()
                val binding = getSharedPreferences("catalog_account", MODE_PRIVATE)
                val boundId = binding.getString("telegram_id", null)
                CatalogAccountBinding.validate(boundId, accountId, current.isNotEmpty())
                if (boundId == null) check(binding.edit().putString("telegram_id", accountId).commit())
                val sync = TelegramBridge.runJob(applicationContext, "dialogs.sync", p)
                check(sync.optInt("synced") > 0 || current.isEmpty()) { "Telegram вернул пустой список; текущий каталог сохранён. Проверьте соединение и аккаунт." }
                val syncedArray = sync.optJSONArray("synced_ids") ?: org.json.JSONArray()
                val liveIds = (0 until syncedArray.length()).map { syncedArray.optString(it) }.toSet()
                val removedPrefs = getSharedPreferences("removed_dialogs", MODE_PRIVATE)
                val removed = removedPrefs.getStringSet(accountId, emptySet()).orEmpty().toSet() - liveIds
                val byId = current.associateBy { it["id"].orEmpty() }.toMutableMap()
                var offset = 0
                do {
                    val items = TelegramBridge.dispatch(applicationContext, "dialogs.list", JSONObject().put("limit", 500).put("offset", offset)).getJSONArray("items")
                    for (i in 0 until items.length()) {
                        val dialog = items.getJSONObject(i); val id = dialog.getString("telegram_id")
                        if (id in removed) continue
                        val row = byId.getOrPut(id) { ChatCatalog.columns.associateWith { "" }.toMutableMap().also(current::add) }
                        row["id"] = id; row["title"] = DialogTitle.choose(dialog.optString("title", ""), row["title"], dialog.optString("username", ""), id)
                        row["type"] = when (dialog.optString("kind")) { "group" -> "group"; "channel" -> "channel"; "bot" -> "bot"; else -> "chat" }
                        val username = dialog.optString("username", "").let { if (it == "null") "" else it }
                        row["link"] = TelegramLink.refreshed(row["username"].orEmpty(), row["link"].orEmpty(), username); row["username"] = username
                        val date = dialog.optString("last_message_at", "").let { if (it == "null") "" else it }
                        if (date.isNotBlank()) row["last_message_date"] = date
                        if (row["added_via"].isNullOrBlank()) row["added_via"] = "Telegram"
                        if (row["added_at"].isNullOrBlank()) row["added_at"] = java.time.Instant.now().toString()
                    }
                    offset += items.length()
                } while (items.length() == 500)
                val folderPrefs = getSharedPreferences("telegram_folder_links", MODE_PRIVATE)
                val links = runCatching { org.json.JSONArray(folderPrefs.getString(accountId, "[]")) }.getOrDefault(org.json.JSONArray())
                if (links.length() > 0) {
                    val folders = TelegramBridge.runJob(applicationContext, "dialogs.folders", JSONObject(p.toString()).put("expected_account_id", accountId)).getJSONArray("folders")
                    for (n in 0 until links.length()) {
                        val link = links.getJSONObject(n)
                        val folder = (0 until folders.length()).map { folders.getJSONObject(it) }.firstOrNull { it.optString("id") == link.optString("id") } ?: continue
                        // An incomplete Telegram folder snapshot must never remove memberships.
                        if (folder.optInt("uncertain") != 0) continue
                        val group = link.optString("group")
                        val items = folder.getJSONArray("dialogs")
                        val incoming = (0 until items.length()).map { items.getJSONObject(it).optString("id") }.toSet()
                        val old = link.optJSONArray("managed") ?: org.json.JSONArray()
                        val previouslyManaged = (0 until old.length()).map { old.optString(it) }.toSet()
                        current.forEach { row ->
                            val id = row["id"].orEmpty(); val assigned = row["blocks"].orEmpty()
                            if (id in previouslyManaged && id !in incoming && assigned.equals(group, true)) row["blocks"] = ""
                            else if (id in incoming && assigned.isBlank()) row["blocks"] = group
                        }
                        link.put("managed", org.json.JSONArray(current.filter { it["id"] in incoming && it["blocks"].orEmpty().equals(group, true) }.map { it["id"] }))
                    }
                }
                catalog.save(current, expectedVersion = version)
                removedPrefs.edit().putStringSet(accountId, removed).apply()
                if (links.length() > 0) folderPrefs.edit().putString(accountId, links.toString()).apply()
                DialogAvailabilityStore.save(applicationContext, accountId, sync)
                runOnUiThread { if (alive) { reloadCatalog(); if (force) android.widget.Toast.makeText(this, "Обновлено чатов: ${sync.optInt("synced")}", android.widget.Toast.LENGTH_SHORT).show() } }
            } catch (error: Exception) {
                runOnUiThread { if (alive) { message("Не удалось обновить список: ${error.message}"); if (force) android.widget.Toast.makeText(this, "Не удалось обновить: ${error.message}", android.widget.Toast.LENGTH_LONG).show() } }
            } finally { refreshRunning = false }
        }
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this)
        super.onCreate(savedInstanceState)
        db = ArchiveDb(this)
        store = ChatCatalog(File(filesDir, "chats.csv"))
        val page = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(dp(16), dp(8), dp(16), dp(10)); setBackgroundColor(Color.rgb(17,19,21)) }
        fun label(text: String, size: Float = 14f) = TextView(this).apply { this.text = text; textSize = size; setTextColor(Color.WHITE) }
        val heading = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
        heading.addView(icon("☰", "Открыть меню") { openMenu() }, LinearLayout.LayoutParams(dp(48), dp(48)))
        val title = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        greeting = label(UserGreeting.text(this), 22f)
        title.addView(greeting)
        scheduleSummary = label("", 12f).apply { alpha = 0.65f; setOnClickListener { startActivity(Intent(this@CatalogActivity, ScheduleActivity::class.java)) } }
        title.addView(scheduleSummary)
        heading.addView(title, LinearLayout.LayoutParams(0, -2, 1f))
        heading.addView(icon("↥", "Экспорт выбранных чатов") { startActivity(Intent(this, ExportActivity::class.java)) }, LinearLayout.LayoutParams(dp(48), dp(48)))
        page.addView(heading)
        exportProgressText = label("", 12f).apply { visibility = View.GONE }; page.addView(exportProgressText)
        exportProgress = android.widget.ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply { visibility = View.GONE }
        page.addView(exportProgress, LinearLayout.LayoutParams(-1, dp(8)))
        tabs = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL }
        status = label("")
        search = EditText(this).apply {
            hint = "⌕  Поиск чатов"
            setSingleLine(); setTextColor(Color.WHITE); setHintTextColor(Color.LTGRAY)
            background = GradientDrawable().apply { setColor(Color.rgb(34, 36, 40)); cornerRadius = dp(28).toFloat() }
            setPadding(dp(18), dp(10), dp(18), dp(10)); minHeight = dp(48)
            addTextChangedListener(object : TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) = Unit
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) { if (::rows.isInitialized && loaded) render() }
                override fun afterTextChanged(s: Editable?) = Unit
            })
        }
        val searchGestures = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent): Boolean = true
            override fun onDoubleTap(e: MotionEvent): Boolean { addClipboardChat(); return true }
            override fun onLongPress(e: MotionEvent) { addClipboardChat() }
        })
        search.isLongClickable = false
        search.setOnTouchListener { _, event -> searchGestures.onTouchEvent(event); false }
        val searchLine = LinearLayout(this).apply { orientation = LinearLayout.HORIZONTAL; gravity = android.view.Gravity.CENTER_VERTICAL }
        searchLine.addView(search, LinearLayout.LayoutParams(0, -2, 1f))
        searchLine.addView(icon("⋮", "Фильтры поиска") {
                val names = arrayOf("@group — группы", "@channel — каналы", "@chat — личные чаты", "@active — сообщения за период активности")
                AlertDialog.Builder(this@CatalogActivity).setTitle("Добавить фильтр").setItems(names) { _, index ->
                    val token = names[index].substringBefore(' ')
                    val previous = search.text.toString().trim()
                    search.setText(if (previous.isEmpty()) token else "$previous $token")
                    search.setSelection(search.text.length)
                }.setNegativeButton("Отмена", null).show()
        }, LinearLayout.LayoutParams(dp(40), dp(48)))
        searchLine.addView(icon("+", "Добавить чат по ссылке, имени или ID") { startActivity(Intent(this, TelegramActivity::class.java)) }, LinearLayout.LayoutParams(dp(40), dp(48)))
        page.addView(searchLine)
        page.addView(HorizontalScrollView(this).apply { isHorizontalScrollBarEnabled = false; addView(tabs) })
        list = ListView(this).apply { adapter = chatAdapter }
        val swipe = GestureDetector(this, object : GestureDetector.SimpleOnGestureListener() {
            override fun onDown(e: MotionEvent): Boolean = true
            override fun onFling(first: MotionEvent?, last: MotionEvent, velocityX: Float, velocityY: Float): Boolean {
                return handleSwipe(first, last, velocityX)
            }
        })
        list.setOnTouchListener { _, event -> swipe.onTouchEvent(event); false }
        page.addView(list, LinearLayout.LayoutParams(-1, 0, 1f))
        page.addView(status)
        screenRoot = FrameLayout(this).apply { addView(page, FrameLayout.LayoutParams(-1, -1)) }
        setContentView(screenRoot)
        SystemInsets.apply(this, page)
        ReleaseUpdates.check(this)
        Appearance.tint(window.decorView, Appearance.color(this))
        savedInstanceState?.let { saved ->
            selectedGroup = saved.getString("selected_group", "Ungrouped")
            search.setText(saved.getString("query", ""))
        }
        try { val loadedCatalog = store.loadVersioned(); rows = loadedCatalog.first; catalogVersion = loadedCatalog.second; render() } catch (e: Exception) { rows = mutableListOf(); catalogVersion = store.version(); loaded = false; message("Не удалось прочитать каталог: ${e.message}. Исходный файл сохранён.") }
        val onboarding = getSharedPreferences("onboarding", MODE_PRIVATE)
        if (!onboarding.getBoolean("opened", false) && runCatching { SecureCredentialStore(this).load() == null }.getOrDefault(false)) {
            onboarding.edit().putBoolean("opened", true).apply()
            startActivity(Intent(this, TelegramActivity::class.java))
        }
    }
    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        if (::list.isInitialized && menuOverlay == null) {
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    val position = IntArray(2); list.getLocationOnScreen(position)
                    refreshStartX = event.rawX; refreshStartY = event.rawY
                    refreshPull = event.rawX >= position[0] && event.rawX < position[0] + list.width &&
                        event.rawY >= position[1] && event.rawY < position[1] + list.height &&
                        list.firstVisiblePosition == 0 && (list.getChildAt(0)?.top ?: 0) >= list.paddingTop
                }
                MotionEvent.ACTION_UP -> {
                    if (refreshPull && event.rawY - refreshStartY > dp(120) && kotlin.math.abs(event.rawX - refreshStartX) < dp(80)) refreshTelegram(true)
                    refreshPull = false
                }
                MotionEvent.ACTION_CANCEL -> refreshPull = false
            }
        }
        return super.dispatchTouchEvent(event)
    }
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putString("selected_group", selectedGroup)
        outState.putString("query", search.text.toString())
    }
    override fun onResume() {
        super.onResume()
        summaryHandler.removeCallbacks(summaryTicker); summaryHandler.post(summaryTicker)
        if (::greeting.isInitialized) greeting.text = UserGreeting.text(this)
        if (::store.isInitialized && ::rows.isInitialized && runCatching { store.version() != catalogVersion }.getOrDefault(false)) reloadCatalog()
        if (::rows.isInitialized) refreshTelegram(false)
        val onboarding = getSharedPreferences("onboarding", MODE_PRIVATE)
        if (onboarding.getBoolean("guide_pending", false)) {
            onboarding.edit().putBoolean("guide_pending", false).apply()
            window.decorView.post {
                if (alive) showWelcomeGuide(0)
            }
        }
        if (!greetingRequested && UserGreeting.text(this) == "Привет!") {
            greetingRequested = true
            worker.execute {
                runCatching {
                    val credentials = SecureCredentialStore(applicationContext).load() ?: return@runCatching
                    val account = TelegramBridge.runJob(applicationContext, "account.status", JSONObject().put("api_id", credentials.apiId).put("api_hash", credentials.apiHash))
                    if (account.optString("status") == "authenticated") UserGreeting.save(applicationContext, account.optJSONObject("account"))
                    runOnUiThread { if (alive) greeting.text = UserGreeting.text(this) }
                }
            }
        }
    }
    private fun showWelcomeGuide(step: Int) {
        val titles = arrayOf("Добро пожаловать в Tegash", "Выберите чаты", "Готово к экспорту")
        val messages = arrayOf(
            "Сначала откройте боковое меню и обновите список в разделе «Подключение Telegram».",
            "Отметьте нужные чаты галочками и распределите по вкладкам-блокам. Чаты без блока не попадут в экспорт.",
            "Нажмите ↥ для экспорта. Удерживайте чат или нажмите на него дважды, чтобы открыть дополнительные действия. Вкладки переключаются свайпом."
        )
        val builder = AlertDialog.Builder(this).setTitle("${step + 1} / ${titles.size} · ${titles[step]}")
            .setMessage(messages[step]).setPositiveButton(if (step == titles.lastIndex) "Начать" else "Далее") { _, _ ->
                if (step < titles.lastIndex && alive) showWelcomeGuide(step + 1)
            }
        if (step > 0) builder.setNegativeButton("Назад") { _, _ -> if (alive) showWelcomeGuide(step - 1) }
        else builder.setNegativeButton("Пропустить", null)
        builder.show()
    }
    override fun onPause() { summaryHandler.removeCallbacks(summaryTicker); super.onPause() }
    private fun openMenu() {
        if (menuOverlay != null) return
        val overlay = FrameLayout(this)
        val scrim = View(this).apply {
            setBackgroundColor(0x99000000.toInt()); alpha = 0f
            setOnClickListener { closeMenu() }
            var startX = 0f; var startY = 0f
            setOnTouchListener { view, event ->
                when (event.actionMasked) {
                    MotionEvent.ACTION_DOWN -> { startX = event.x; startY = event.y; true }
                    MotionEvent.ACTION_UP -> {
                        if (kotlin.math.abs(event.x - startX) > dp(48) || kotlin.math.abs(event.y - startY) > dp(48)) closeMenu()
                        else view.performClick()
                        true
                    }
                    else -> true
                }
            }
        }
        overlay.addView(scrim, FrameLayout.LayoutParams(-1, -1))
        val panel = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL; setBackgroundColor(Color.rgb(23, 25, 29)); elevation = dp(18).toFloat()
            setPadding(dp(20), dp(16), dp(16), dp(16)); isClickable = true
        }
        val width = minOf(dp(340), (resources.displayMetrics.widthPixels * 0.84f).toInt())
        overlay.addView(panel, FrameLayout.LayoutParams(width, -1, android.view.Gravity.START))
        val content = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        val name = UserGreeting.name(this).ifBlank { "Аккаунт Telegram" }
        val avatar = TextView(this).apply {
            text = name.removePrefix("@").firstOrNull()?.uppercase().orEmpty().ifBlank { "T" }
            gravity = android.view.Gravity.CENTER; textSize = 25f; setTextColor(Appearance.color(this@CatalogActivity))
            background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(Color.rgb(38, 43, 48)) }
        }
        content.addView(avatar, LinearLayout.LayoutParams(dp(64), dp(64)))
        content.addView(TextView(this).apply { text = name; textSize = 21f; setTextColor(Color.WHITE); setPadding(0, dp(14), 0, dp(4)) })
        content.addView(TextView(this).apply { text = "Tegash · Telegram"; textSize = 13f; setTextColor(Color.LTGRAY); setPadding(0, 0, 0, dp(20)) })
        fun section(title: String) { content.addView(TextView(this).apply { text = title; textSize = 12f; setTextColor(Appearance.color(this@CatalogActivity)); setPadding(dp(4), dp(14), 0, dp(6)) }) }
        fun item(icon: String, title: String, action: () -> Unit) {
            content.addView(TextView(this).apply {
                text = "$icon    $title"; textSize = 16f; setTextColor(Color.WHITE); gravity = android.view.Gravity.CENTER_VERTICAL
                setPadding(dp(8), dp(12), dp(8), dp(12)); minHeight = dp(48)
                setOnClickListener { closeMenu(); action() }
            })
        }
        section("АККАУНТ")
        item("◉", "Подключение и аккаунты") { startActivity(Intent(this, TelegramActivity::class.java)) }
        section("ЭКСПОРТ")
        item("↥", "Выбранные чаты") { startActivity(Intent(this, ExportActivity::class.java)) }
        item("◎", "Участники групп") { startActivity(Intent(this, MembersExportActivity::class.java)) }
        item("◷", "История и справка") { startActivity(Intent(this, NativeActivity::class.java)) }
        section("НАСТРОЙКИ")
        item("◷", "Расписание и хранение") { startActivity(Intent(this, ScheduleActivity::class.java)) }
        item("✉", "Отправка на почту") { startActivity(Intent(this, MailSettingsActivity::class.java)) }
        item("▤", "Каталог и CSV") { catalogSettings() }
        item("▦", "Вкладки и блоки") { blockSettings() }
        item("◉", "Цвет интерфейса") { Appearance.showOptions(this) { recreate() } }
        item("◌", "Неактивные чаты") { startActivity(Intent(this, InactiveChatsActivity::class.java)) }
        section("О ПРИЛОЖЕНИИ")
        item("ⓘ", "О Tegash") { startActivity(Intent(this, AboutActivity::class.java)) }
        panel.addView(ScrollView(this).apply { addView(content) }, LinearLayout.LayoutParams(-1, 0, 1f))
        val prefs = ExportSchedule.prefs(this)
        val next = if (prefs.getBoolean("enabled", false)) prefs.getString("next_due", null)?.let { runCatching { java.time.Instant.parse(it) }.getOrNull() } else null
        val last = ExportHistory.latest(this)
        val display = java.time.format.DateTimeFormatter.ofPattern("dd.MM HH:mm").withZone(java.time.ZoneId.systemDefault())
        panel.addView(TextView(this).apply {
            text = "Последний: ${last?.let(display::format) ?: "нет"}\nСледующий: ${next?.let(display::format) ?: "не запланирован"}"
            textSize = 12f; setTextColor(Color.LTGRAY); alpha = 0.6f
            gravity = android.view.Gravity.END; setPadding(0, dp(8), 0, dp(8))
            setOnClickListener { closeMenu(); startActivity(Intent(this@CatalogActivity, ScheduleActivity::class.java)) }
        })
        screenRoot.addView(overlay, FrameLayout.LayoutParams(-1, -1)); menuOverlay = overlay
        SystemInsets.apply(this, panel)
        panel.translationX = -width.toFloat(); panel.animate().translationX(0f).setDuration(220).start()
        scrim.animate().alpha(1f).setDuration(180).start()
    }
    private fun closeMenu() {
        val overlay = menuOverlay ?: return
        menuOverlay = null
        val panel = overlay.getChildAt(1)
        overlay.getChildAt(0).animate().alpha(0f).setDuration(140).start()
        panel.animate().translationX(-panel.width.toFloat()).setDuration(180).withEndAction { screenRoot.removeView(overlay) }.start()
    }
    @Deprecated("Back button support on Android versions without predictive back")
    override fun onBackPressed() { if (menuOverlay != null) closeMenu() else super.onBackPressed() }
    private fun addClipboardChat() {
        if (selectedGroup == inactiveTab || selectedGroup == allTab) {
            android.widget.Toast.makeText(this, "Выберите блок для нового чата", android.widget.Toast.LENGTH_SHORT).show(); return
        }
        val clipboard = getSystemService(android.content.ClipboardManager::class.java)
        val value = clipboard.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()?.trim().orEmpty()
        if (value.isEmpty() || value.length > 2048) {
            android.widget.Toast.makeText(this, "Скопируйте ссылку, имя или ID чата", android.widget.Toast.LENGTH_SHORT).show(); return
        }
        startActivity(Intent(this, TelegramActivity::class.java).putExtra("reference", value)
            .putExtra("target_block", selectedGroup).putExtra("auto_add", true))
    }
    private fun reloadCatalog() {
        try {
            require(File(filesDir, "chats.csv").isFile || catalogVersion == "missing") { "Файл каталога отсутствует" }
            val (updated, version) = store.loadVersioned()
            rows = updated; catalogVersion = version; loaded = true; render(); message("Каталог обновлён: ${rows.size} чатов")
        } catch (error: Exception) {
            loaded = false
            catalogVersion = runCatching { store.version() }.getOrDefault(catalogVersion)
            message("Не удалось обновить каталог: ${error.message}. Предыдущий список остаётся на экране; восстановите CSV.")
        }
    }
    private fun catalogSettings() {
        val options = arrayOf("Добавить чаты из локального архива", "Загрузить CSV", "Сохранить CSV", "Восстановить резервную копию", "Сохранить список выбранных чатов (TXT)", "Обновить из локального CSV", "Импортировать папку Telegram")
        AlertDialog.Builder(this).setTitle("Каталог и CSV")
            .setItems(options) { _, choice ->
                when (choice) {
                    0 -> confirmLocalSources()
                    1 -> startActivityForResult(Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*").putExtra(Intent.EXTRA_MIME_TYPES, arrayOf("text/csv", "text/plain", "application/vnd.ms-excel", "application/octet-stream")), 201)
                    2 -> startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("text/csv").putExtra(Intent.EXTRA_TITLE, "chats.csv"), 202)
                    3 -> restoreBackup()
                    4 -> startActivityForResult(Intent(Intent.ACTION_CREATE_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("text/plain").putExtra(Intent.EXTRA_TITLE, "telegram-chats.txt"), 203)
                    5 -> reloadCatalog()
                    6 -> loadTelegramFolders()
                }
            }.setNeutralButton("Где файл?") { _, _ ->
                AlertDialog.Builder(this).setTitle("Файл каталога")
                    .setMessage("${File(filesDir, "chats.csv").absolutePath}\n\nДля редактирования в другом приложении сохраните CSV, затем загрузите изменённый файл.")
                    .setPositiveButton("Понятно", null).show()
            }.setNegativeButton("Закрыть", null).show()
    }
    private fun loadTelegramFolders() {
        try {
            check(loaded) { "Сначала восстановите каталог" }
            val credentials = SecureCredentialStore(this).load() ?: error("Сначала подключите Telegram")
            val version = catalogVersion
            message("Загружаем папки Telegram…")
            worker.execute {
                try {
                    val params = JSONObject().put("api_id", credentials.apiId).put("api_hash", credentials.apiHash)
                    val account = TelegramBridge.runJob(applicationContext, "account.status", params)
                    check(account.optString("status") == "authenticated") { "Сначала войдите в Telegram" }
                    val accountId = account.optJSONObject("account")?.optString("telegram_id").orEmpty()
                    CatalogAccountBinding.validate(getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null), accountId, true)
                    // Message export requires the embedded Telegram index to know
                    // every imported chat, including chats absent from this CSV.
                    val synced = TelegramBridge.runJob(applicationContext, "dialogs.sync", params) { progress ->
                        if (progress.isNotBlank()) runOnUiThread { if (alive) message(progress) }
                    }
                    DialogAvailabilityStore.save(applicationContext, accountId, synced)
                    val result = TelegramBridge.runJob(applicationContext, "dialogs.folders", JSONObject(params.toString()).put("expected_account_id", accountId))
                    val folders = result.getJSONArray("folders")
                    runOnUiThread {
                        if (!alive) return@runOnUiThread
                        if (folders.length() == 0) { message("Папки Telegram не найдены"); return@runOnUiThread }
                        AlertDialog.Builder(this).setTitle("Папка Telegram")
                            .setItems((0 until folders.length()).map { folders.getJSONObject(it).optString("name") }.toTypedArray()) { _, index ->
                                val folder = folders.getJSONObject(index); val name = folder.optString("name").trim(); val items = folder.getJSONArray("dialogs")
                                val incoming = (0 until items.length()).map { i -> items.getJSONObject(i).let { item ->
                                    val username = if (item.isNull("username")) "" else item.optString("username", "")
                                    mapOf("id" to item.optString("id"), "title" to item.optString("title"), "type" to item.optString("type"), "username" to username)
                                } }
                                val ids = incoming.map { it["id"] }.toSet()
                                val conflicts = rows.count { it["id"] in ids && !it["blocks"].isNullOrBlank() && !it["blocks"].orEmpty().equals(name, true) }
                                AlertDialog.Builder(this).setTitle("Импортировать «$name»?")
                                    .setMessage("Чатов: ${incoming.size}; уже в других блоках: $conflicts; не удалось определить: ${folder.optInt("uncertain")}. Выберите, что делать с конфликтами.")
                                    .setNegativeButton("Отмена", null)
                                    .setNeutralButton("Оставить") { _, _ -> importFolder(version, incoming, name, false, accountId, folder.optString("id")) }
                                    .setPositiveButton("Переместить") { _, _ -> importFolder(version, incoming, name, true, accountId, folder.optString("id")) }.show()
                            }.setNegativeButton("Отмена", null).show()
                    }
                } catch (error: Exception) { runOnUiThread { if (alive) message("Не удалось загрузить папки: ${error.message}") } }
            }
        } catch (error: Exception) { message(error.message ?: "Не удалось загрузить папки") }
    }
    private fun importFolder(version: String, incoming: List<Map<String, String>>, name: String, move: Boolean, accountId: String, folderId: String) {
        try {
            require(version == catalogVersion) { "Каталог изменился. Повторите импорт папки." }
            val updated = FolderImport.apply(rows, incoming, name, move)
            saveRows(updated); rows = updated; selectedGroup = BlockEdits.canonical(rows, name); render()
            val prefs = getSharedPreferences("telegram_folder_links", MODE_PRIVATE)
            val links = runCatching { org.json.JSONArray(prefs.getString(accountId, "[]")) }.getOrDefault(org.json.JSONArray())
            val remaining = org.json.JSONArray()
            for (i in 0 until links.length()) if (links.getJSONObject(i).optString("id") != folderId) remaining.put(links.getJSONObject(i))
            val ids = incoming.map { it["id"] }.toSet()
            val managed = updated.filter { it["id"] in ids && it["blocks"].orEmpty().equals(name, true) }.map { it["id"] }
            remaining.put(JSONObject().put("id", folderId).put("group", name).put("managed", org.json.JSONArray(managed)))
            prefs.edit().putString(accountId, remaining.toString()).apply()
            message("Папка импортирована; проверьте выбранные чаты")
        } catch (error: Exception) { message("Не удалось импортировать папку: ${error.message}") }
    }
    private fun render() {
        tabs.removeAllViews()
        val groups = listOf("Ungrouped") + orderedBlocks()
        if (selectedGroup != inactiveTab && selectedGroup != allTab && selectedGroup !in groups) selectedGroup = "Ungrouped"
        groups.forEach { group ->
            tabs.addView(Button(this).apply {
                text = group; styleTab(this, group == selectedGroup)
                val gestures = GestureDetector(this@CatalogActivity, object : GestureDetector.SimpleOnGestureListener() {
                    override fun onDown(e: MotionEvent) = true
                    override fun onSingleTapConfirmed(e: MotionEvent): Boolean { selectedGroup = group; render(); return true }
                    override fun onDoubleTap(e: MotionEvent): Boolean { showBlockSettings(group); return true }
                    override fun onLongPress(e: MotionEvent) { startActivity(Intent(this@CatalogActivity, ExportActivity::class.java).putExtra("block_name", group)) }
                })
                setOnTouchListener { _, event -> gestures.onTouchEvent(event); true }
            })
        }
        tabs.addView(Button(this).apply {
            text = "Неактивные"; styleTab(this, selectedGroup == inactiveTab)
            val gestures = GestureDetector(this@CatalogActivity, object : GestureDetector.SimpleOnGestureListener() {
                override fun onDown(e: MotionEvent) = true
                override fun onSingleTapConfirmed(e: MotionEvent): Boolean { selectedGroup = inactiveTab; render(); return true }
                override fun onDoubleTap(e: MotionEvent): Boolean { startActivity(Intent(this@CatalogActivity, InactiveChatsActivity::class.java)); return true }
            })
            setOnTouchListener { _, event -> gestures.onTouchEvent(event); true }
        })
        tabs.addView(Button(this).apply {
            text = "Все чаты"; styleTab(this, selectedGroup == allTab)
            val gestures = GestureDetector(this@CatalogActivity, object : GestureDetector.SimpleOnGestureListener() {
                override fun onDown(e: MotionEvent) = true
                override fun onSingleTapConfirmed(e: MotionEvent): Boolean { selectedGroup = allTab; render(); return true }
                override fun onDoubleTap(e: MotionEvent): Boolean { blockSettings(); return true }
            })
            setOnTouchListener { _, event -> gestures.onTouchEvent(event); true }
        })
        tabs.addView(icon("+", "Добавить блок") {
            AlertDialog.Builder(this).setTitle("Добавить блок")
                .setItems(arrayOf("Создать вручную", "Из папки Telegram", "Как это работает?")) { _, choice ->
                    when (choice) {
                        0 -> createGroup()
                        1 -> loadTelegramFolders()
                        else -> AlertDialog.Builder(this).setTitle("Блок из папки Telegram")
                            .setMessage("Выберите существующую папку Telegram: Tegash создаст блок с её названием и добавит в него чаты. Если чат уже находится в другом блоке, можно оставить его там или переместить.\n\nПри обновлении списка изменения папки учитываются автоматически: новые чаты без блока добавляются, удалённые из папки выходят из связанного блока. Чаты в другом блоке не перемещаются без подтверждения. Папки Telegram при этом не изменяются.")
                            .setPositiveButton("Выбрать папку") { _, _ -> loadTelegramFolders() }.setNegativeButton("Закрыть", null).show()
                    }
                }.setNegativeButton("Отмена", null).show()
        }, LinearLayout.LayoutParams(dp(44), dp(44)))
        val query = search.text.toString().trim()
        val now = java.time.Instant.now()
        val activeDays = getSharedPreferences("catalog_settings", MODE_PRIVATE).getInt("inactive_days", 30).coerceIn(1, 36500)
        val availability = getSharedPreferences("dialog_availability", MODE_PRIVATE)
        val account = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
        val snapshot = if (account != null && availability.getString("account_id", null) == account) availability.getStringSet("ids", null) else null
        val empty = if (snapshot != null) availability.getStringSet("empty_ids", emptySet()).orEmpty() else emptySet()
        val unanswered = if (snapshot != null) availability.getStringSet("unanswered_bot_ids", emptySet()).orEmpty() else emptySet()
        val visible = rows.filter { row ->
            val group = row["blocks"].orEmpty()
            val inGroup = if (query.isNotBlank()) true else when (selectedGroup) {
                allTab -> true
                inactiveTab -> {
                    val id = row["id"].orEmpty()
                    val date = runCatching { java.time.Instant.parse(row["last_message_date"].orEmpty()) }.getOrNull()
                    id in empty || id in unanswered || DialogAvailability.unavailable(id, snapshot) || date == null || date < now.minusSeconds(activeDays.toLong() * 86400)
                }
                "Ungrouped" -> group.equals("Ungrouped", true)
                else -> group == selectedGroup
            }
            inGroup && CatalogSearch.matches(row, query, now, activeDays)
        }
        val pendingEntries = if (selectedGroup == inactiveTab) PendingRequests.list(this).filter { it.second.optString("account_id") == account }.map { (key, entry) ->
            val reference = entry.optString("reference")
            mutableMapOf("id" to key, "type" to "pending", "title" to entry.optString("title").ifBlank { reference }, "blocks" to "", "link" to reference, "username" to if (reference.startsWith("@")) reference else "")
        }.filter { CatalogSearch.matches(it, query, now, activeDays) } else emptyList()
        val recentOrder = compareByDescending<MutableMap<String, String>> { row ->
            runCatching { java.time.Instant.parse(row["last_message_date"].orEmpty()) }.getOrDefault(java.time.Instant.MIN)
        }
        val oldestOrder = compareBy<MutableMap<String, String>> { row ->
            runCatching { java.time.Instant.parse(row["last_message_date"].orEmpty()) }.getOrDefault(java.time.Instant.MAX)
        }
        visibleRows = pendingEntries.sortedBy { it["title"].orEmpty().lowercase() } + visible.sortedWith(
            compareBy<MutableMap<String, String>> { row -> selectedGroup == allTab && row["blocks"].isNullOrBlank() }
            .then(if (selectedGroup == inactiveTab) oldestOrder else recentOrder).thenBy { it["title"].orEmpty().lowercase() }
        )
        chatAdapter.notifyDataSetChanged()
        val pending = pendingEntries.size
        message("${visible.size} из ${rows.size} чатов · ${rows.count { !it["blocks"].isNullOrBlank() }} выбрано." + if (pending > 0) " Заявок: $pending · нажмите для просмотра." else "")
        status.setOnClickListener(if (pending > 0) View.OnClickListener { startActivity(Intent(this, InactiveChatsActivity::class.java)) } else null)
    }
    private fun styleTab(button: Button, selected: Boolean) {
        val color = if (selected) Appearance.color(this) else Color.rgb(32, 36, 44)
        val brightness = Color.red(color) * 299 + Color.green(color) * 587 + Color.blue(color) * 114
        button.isAllCaps = false; button.textSize = 13f
        button.minWidth = 0; button.minimumWidth = 0; button.minHeight = 0; button.minimumHeight = 0
        button.setPadding(dp(14), dp(8), dp(14), dp(8))
        button.setTextColor(if (brightness >= 150_000) Color.BLACK else Color.WHITE)
        button.backgroundTintList = null
        button.background = GradientDrawable().apply { setColor(color); cornerRadius = dp(18).toFloat() }
    }
    private fun icon(symbol: String, description: String, action: () -> Unit): TextView = TextView(this).apply {
        text = symbol; textSize = 22f; setTextColor(Color.WHITE); gravity = android.view.Gravity.CENTER
        contentDescription = description; isClickable = true; isFocusable = true; setOnClickListener { action() }
    }
    private fun moveTab(delta: Int) {
        val groups = listOf("Ungrouped") + orderedBlocks() + inactiveTab + allTab
        val index = groups.indexOf(selectedGroup).coerceAtLeast(0)
        if (delta < 0 && index == 0) { openMenu(); return }
        val next = (index + delta).coerceIn(0, groups.lastIndex)
        if (next == index) return
        list.animate().cancel()
        list.animate().translationX((-delta * dp(28)).toFloat()).alpha(0f).setDuration(100).withEndAction {
            selectedGroup = groups[next]; render()
            list.translationX = (delta * dp(28)).toFloat(); list.animate().translationX(0f).alpha(1f).setDuration(140).start()
        }.start()
    }
    private fun handleSwipe(first: MotionEvent?, last: MotionEvent, velocityX: Float): Boolean {
        if (first == null) return false
        val dx = last.x - first.x; val dy = last.y - first.y
        if (kotlin.math.abs(dx) < dp(72) || kotlin.math.abs(dx) < kotlin.math.abs(dy) * 1.5f || kotlin.math.abs(velocityX) < 250) return false
        // The content follows the finger: left goes forward, right goes back.
        moveTab(if (dx < 0) 1 else -1)
        return true
    }
    private fun showChatActions(row: Map<String, String>) {
        val actions = mutableListOf("Изменить блок")
        if (row["type"] == "group") actions.add("Экспорт участников")
        actions.add("Полный экспорт")
        actions.add("Экспорт с даты")
        AlertDialog.Builder(this).setTitle(row["title"]).setItems(actions.toTypedArray()) { _, index ->
            when (actions[index]) {
                "Изменить блок" -> rows.firstOrNull { it["id"] == row["id"] }?.let(::editGroup)
                "Экспорт участников" -> startActivity(Intent(this, MembersExportActivity::class.java).putExtra("group_id", row["id"]))
                "Полный экспорт" -> startActivity(Intent(this, ExportActivity::class.java).putExtra("chat_id", row["id"]).putExtra("full", true))
                "Экспорт с даты" -> startActivity(Intent(this, ExportActivity::class.java).putExtra("chat_id", row["id"]))
            }
        }.setNegativeButton("Отмена", null).show()
    }
    private fun confirmRemoveDialog(row: Map<String, String>) {
        val isCommunity = row["type"] in setOf("group", "channel")
        val title = row["title"].orEmpty()
        AlertDialog.Builder(this).setTitle(if (isCommunity) "Покинуть «$title»?" else "Удалить чат «$title»?")
            .setMessage(if (isCommunity) "Приложение отправит Telegram запрос на выход и уберёт чат из каталога. Уже созданные архивы сохранятся." else "Чат и его история будут удалены из вашего Telegram. У собеседника история не удаляется. Уже созданные архивы сохранятся.")
            .setNegativeButton("Отмена", null).setPositiveButton(if (isCommunity) "Покинуть" else "Удалить") { _, _ ->
                val id = row["id"].orEmpty()
                worker.execute {
                    try {
                        val credentials = SecureCredentialStore(applicationContext).load() ?: error("Сначала подключите Telegram")
                        val account = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null) ?: error("Сначала обновите каталог")
                        val params = JSONObject().put("api_id", credentials.apiId).put("api_hash", credentials.apiHash).put("expected_account_id", account).put("dialog_id", id.toLong())
                        TelegramBridge.runJob(applicationContext, "dialogs.remove", params)
                        val removedPrefs = getSharedPreferences("removed_dialogs", MODE_PRIVATE)
                        val removed = removedPrefs.getStringSet(account, emptySet()).orEmpty().toMutableSet().apply { add(id) }
                        check(removedPrefs.edit().putStringSet(account, removed).commit()) { "Не удалось сохранить состояние каталога" }
                        val catalog = ChatCatalog(filesDir.resolve("chats.csv")); val (current, version) = catalog.loadVersioned()
                        catalog.save(current.filter { it["id"] != id }, expectedVersion = version)
                        runOnUiThread { if (alive) { reloadCatalog(); android.widget.Toast.makeText(this, "Готово", android.widget.Toast.LENGTH_SHORT).show() } }
                    } catch (error: Exception) { runOnUiThread { if (alive) android.widget.Toast.makeText(this, "Не удалось завершить действие: ${error.message}", android.widget.Toast.LENGTH_LONG).show() } }
                }
            }.show()
    }
    private fun orderKey() = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", "unbound").orEmpty()
    private fun orderedBlocks(): List<String> {
        val groups = rows.mapNotNull { it["blocks"]?.takeIf { name -> name.isNotBlank() && !name.equals("Ungrouped", true) } }.distinct().sortedBy { it.lowercase() }
        val saved = runCatching { org.json.JSONArray(getSharedPreferences("tab_order", MODE_PRIVATE).getString(orderKey(), "[]")) }.getOrDefault(org.json.JSONArray())
        val order = (0 until saved.length()).map { saved.optString(it) }
        return groups.sortedWith(compareBy<String> { val position = order.indexOf(it); if (position < 0) Int.MAX_VALUE else position }.thenBy { it.lowercase() })
    }
    private fun reorderBlocks() {
        val groups = orderedBlocks()
        if (groups.isEmpty()) { message("Сначала создайте блоки"); return }
        AlertDialog.Builder(this).setTitle("Порядок блоков")
            .setItems(groups.mapIndexed { i, group -> "${i + 1}. $group" }.toTypedArray()) { _, index ->
                AlertDialog.Builder(this).setTitle(groups[index]).setItems(arrayOf("Переместить выше", "Переместить ниже")) { _, direction ->
                    val next = (index + if (direction == 0) -1 else 1).coerceIn(0, groups.lastIndex)
                    val changed = groups.toMutableList(); java.util.Collections.swap(changed, index, next)
                    getSharedPreferences("tab_order", MODE_PRIVATE).edit().putString(orderKey(), org.json.JSONArray(changed).toString()).apply()
                    render(); reorderBlocks()
                }.setNegativeButton("Назад") { _, _ -> reorderBlocks() }.show()
            }.setNegativeButton("Готово", null).show()
    }
    private fun showBlockSettings(group: String) {
        val actions = if (group == "Ungrouped") arrayOf("Промпт для ИИ", "Экспортировать блок") else arrayOf("Промпт для ИИ", "Экспортировать блок", "Переименовать или удалить")
        AlertDialog.Builder(this).setTitle(group).setItems(actions) { _, choice ->
            when (choice) {
                0 -> editPrompt(group)
                1 -> startActivity(Intent(this, ExportActivity::class.java).putExtra("block_name", group))
                else -> manageGroup(group)
            }
        }.setNegativeButton("Назад") { _, _ -> blockSettings() }.show()
    }
    private fun blockSettings() {
        val groups = listOf("Ungrouped") + orderedBlocks()
        AlertDialog.Builder(this).setTitle("Вкладки и блоки")
            .setItems((groups + listOf("Порядок вкладок", "＋ Создать блок", "Импортировать папку Telegram")).toTypedArray()) { _, index ->
                when (index) {
                    groups.size -> reorderBlocks()
                    groups.size + 1 -> createGroup()
                    groups.size + 2 -> loadTelegramFolders()
                    else -> showBlockSettings(groups[index])
                }
            }.setNegativeButton("Закрыть", null).show()
    }
    private fun editPrompt(group: String) {
        val account = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
        if (account.isNullOrBlank()) { message("Сначала подключите аккаунт и обновите каталог"); return }
        val field = EditText(this).apply {
            hint = "Инструкция для ИИ; пустое поле отключает добавление файла"
            setText(BlockPrompts.get(this@CatalogActivity, account, group))
            setMinLines(7); setMaxLines(14); setPadding(dp(16), dp(8), dp(16), dp(8))
        }
        val dialog = AlertDialog.Builder(this).setTitle("Промпт · $group")
            .setMessage("Будет сохранён в AI_PROMPT.txt внутри папки блока в ZIP. Для Ungrouped — в корне архива.")
            .setView(field).setNegativeButton("Отмена", null).setNeutralButton("Шаблон", null)
            .setPositiveButton("Сохранить") { _, _ ->
                runCatching { BlockPrompts.save(this, account, group, field.text.toString()) }
                    .onSuccess { android.widget.Toast.makeText(this, "Промпт сохранён", android.widget.Toast.LENGTH_SHORT).show() }
                    .onFailure { message("Не удалось сохранить промпт: ${it.message}") }
            }.create()
        dialog.setOnShowListener { dialog.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener {
            if (field.text.isNotBlank()) AlertDialog.Builder(this).setTitle("Заменить текст шаблоном?")
                .setNegativeButton("Отмена", null).setPositiveButton("Заменить") { _, _ -> field.setText(BlockPrompts.TEMPLATE) }.show()
            else field.setText(BlockPrompts.TEMPLATE)
        } }
        dialog.show()
    }
    private fun manageGroup(group: String) {
        AlertDialog.Builder(this).setTitle(group).setItems(arrayOf("Переименовать блок", "Удалить блок")) { _, choice ->
            if (choice == 0) {
                val name = EditText(this).apply { setText(group); setSingleLine() }
                AlertDialog.Builder(this).setTitle("Переименовать блок").setView(name).setNegativeButton("Отмена", null)
                    .setPositiveButton("Сохранить") { _, _ ->
                        try { val updated = BlockEdits.rename(rows, group, name.text.toString()); saveRows(updated); rows = updated; selectedGroup = name.text.toString().trim(); updateFolderLinks(group, selectedGroup); render() }
                        catch (error: Exception) { message("Не удалось переименовать: ${error.message}") }
                    }.show()
            } else {
                AlertDialog.Builder(this).setTitle("Удалить блок «$group»?")
                    .setMessage("Чаты останутся в каталоге, но без блока они не будут участвовать в обычном экспорте.")
                    .setNegativeButton("Отмена", null).setPositiveButton("Удалить блок") { _, _ ->
                        try { val updated = BlockEdits.remove(rows, group); saveRows(updated); rows = updated; updateFolderLinks(group, null); selectedGroup = "Ungrouped"; render() }
                        catch (error: Exception) { message("Не удалось удалить блок: ${error.message}") }
                    }.show()
            }
        }.setNegativeButton("Отмена", null).show()
    }
    private fun updateFolderLinks(group: String, replacement: String?) {
        val accountId = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null) ?: return
        BlockPrompts.move(this, accountId, group, replacement)
        val prefs = getSharedPreferences("telegram_folder_links", MODE_PRIVATE)
        val links = runCatching { org.json.JSONArray(prefs.getString(accountId, "[]")) }.getOrDefault(org.json.JSONArray())
        val result = org.json.JSONArray()
        for (i in 0 until links.length()) {
            val link = links.getJSONObject(i)
            if (link.optString("group").equals(group, true)) {
                if (replacement == null) continue
                link.put("group", replacement)
            }
            result.put(link)
        }
        prefs.edit().putString(accountId, result.toString()).apply()
    }
    private fun createGroup() {
        val name = EditText(this).apply { hint = "Название блока"; setSingleLine() }
        AlertDialog.Builder(this).setTitle("Новый блок").setView(name).setNegativeButton("Отмена", null)
            .setPositiveButton("Далее") { _, _ ->
                val group = name.text.toString().trim()
                if (group.isBlank() || group.length > 100 || group.any { it.code < 32 } || group.equals("Ungrouped", true)) {
                    message("Введите название блока до 100 символов, отличное от Ungrouped")
                } else if (rows.any { it["blocks"].orEmpty().equals(group, true) }) {
                    message("Блок с таким названием уже существует")
                } else {
                    val ordered = rows.sortedBy { it["title"].orEmpty().lowercase() }
                    val chosen = BooleanArray(ordered.size) { ordered[it]["blocks"] == group }
                    AlertDialog.Builder(this).setTitle("Чаты блока $group")
                        .setMultiChoiceItems(ordered.map { "${it["title"]} · ${it["blocks"].orEmpty().ifBlank { "без блока" }}" }.toTypedArray(), chosen) { _, index, checked -> chosen[index] = checked }
                        .setNegativeButton("Отмена", null)
                        .setPositiveButton("Продолжить") { _, _ ->
                            val ids = ordered.indices.filter { chosen[it] }.map { ordered[it]["id"].orEmpty() }.toSet()
                            if (ids.isEmpty()) { message("Выберите хотя бы один чат"); return@setPositiveButton }
                            val moved = ordered.count { it["id"] in ids && !it["blocks"].isNullOrBlank() && it["blocks"] != group }
                            AlertDialog.Builder(this).setTitle("Назначить блок?")
                                .setMessage("Выбрано чатов: ${ids.size}. Переместится из других блоков: $moved. Один чат может находиться только в одном блоке.")
                                .setNegativeButton("Отмена", null).setPositiveButton("Назначить") { _, _ ->
                                    try {
                                        val updated = rows.map { it.toMutableMap().apply { if (this["id"] in ids) this["blocks"] = group } }.toMutableList()
                                        saveRows(updated); rows = updated; selectedGroup = group; render()
                                    } catch (error: Exception) { message("Не удалось сохранить: ${error.message}") }
                                }.show()
                        }.show()
                }
            }.show()
    }
    private fun editGroup(row: MutableMap<String, String>) {
        val field = AutoCompleteTextView(this).apply {
            setSingleLine(); setText(row["blocks"].orEmpty()); hint = "Имя группы или Ungrouped; пусто — исключить"
            val groups = (listOf("Ungrouped") + rows.mapNotNull { it["blocks"]?.takeIf(String::isNotBlank) }).distinct().sorted()
            setAdapter(ArrayAdapter(this@CatalogActivity, android.R.layout.simple_dropdown_item_1line, groups))
            threshold = 1
            setOnFocusChangeListener { _, focused -> if (focused) showDropDown() }
        }
        val box = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; addView(field) }
        val dialog = AlertDialog.Builder(this).setTitle(row["title"]).setView(box).setNegativeButton("Отмена", null)
            .setPositiveButton("Сохранить") { _, _ ->
            try {
                val group = field.text.toString().trim()
                require(group.length <= 100 && group.none { it.code < 32 }) { "Неверное имя группы" }
                val old = row["blocks"]
                row["blocks"] = BlockEdits.canonical(rows, group)
                try { saveRows(rows) } catch (e: Exception) { row["blocks"] = old.orEmpty(); throw e }
                render()
            } catch (e: Exception) { message("Не сохранено: ${e.message}") }
        }
        val export = Button(this).apply { text = "Экспорт сообщений" }
        box.addView(export)
        if (row["type"] == "group") dialog.setNeutralButton("Экспорт участников") { _, _ ->
            startActivity(Intent(this, MembersExportActivity::class.java).putExtra("group_id", row["id"]))
        }
        val shown = dialog.create()
        export.setOnClickListener {
            shown.dismiss()
            startActivity(Intent(this, ExportActivity::class.java).putExtra("chat_id", row["id"]))
        }
        shown.show()
    }
    private fun confirmLocalSources() {
        if (getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null) == null) {
            syncSources(); return
        }
        AlertDialog.Builder(this).setTitle("Добавить локальные чаты?")
            .setMessage("Локальный архив не подтверждает аккаунт Telegram. Проверьте, что добавляемые чаты относятся к текущему аккаунту; иначе данные могут смешаться.")
            .setNegativeButton("Отмена", null).setPositiveButton("Да, добавить") { _, _ -> syncSources() }.show()
    }
    private fun syncSources() {
        try {
            check(loaded) { "Сначала восстановите каталог из CSV" }
            val existing = rows.map { it["id"] }.toSet()
            val additions = mutableListOf<MutableMap<String, String>>()
            db.readableDatabase.rawQuery("SELECT source_id,title,source_type FROM sources ORDER BY title COLLATE NOCASE", null).use { c ->
                while (c.moveToNext()) {
                    val id = c.getString(0) ?: continue
                    if (id in existing || id.toLongOrNull() == null || id.toLong() == 0L) continue
                    val type = c.getString(2).orEmpty().takeIf { it in listOf("chat", "group", "channel", "bot") } ?: "chat"
                    additions.add(ChatCatalog.columns.associateWith { "" }.toMutableMap().apply { put("id", id); put("title", c.getString(1) ?: id); put("type", type); put("added_via", "local archive"); put("added_at", java.time.Instant.now().toString()); put("selected", "no") })
                }
            }
            val next = (rows + additions).toMutableList()
            saveRows(next); rows = next; render(); message("Добавлено чатов: ${additions.size}")
        } catch (e: Exception) { message("Не удалось обновить каталог: ${e.message}") }
    }
    private fun restoreBackup() {
        val backups = store.backups()
        if (backups.isEmpty()) { message("Резервных копий пока нет."); return }
        AlertDialog.Builder(this).setTitle("Выберите копию (UTC)")
            .setItems(backups.map { it.name }.toTypedArray()) { _, index ->
                try {
                    val incoming = ChatCatalog.parseImported(backups[index].readBytes())
                    AlertDialog.Builder(this).setTitle("Восстановить список?")
                        .setMessage("Будет сохранена копия текущего файла. Чатов: ${incoming.size}. CSV не подтверждает аккаунт Telegram: убедитесь, что это тот же аккаунт.")
                        .setNegativeButton("Отмена", null).setPositiveButton("Восстановить") { _, _ ->
                            try { saveRows(incoming, allowInvalidReplacement = true); rows = incoming; loaded = true; render() }
                            catch (e: Exception) { message("Не удалось восстановить каталог: ${e.message}") }
                        }.show()
                } catch (e: Exception) { message("Не удалось прочитать копию: ${e.message}") }
            }.setNegativeButton("Отмена", null).show()
    }
    @Deprecated("Document picker on supported Android versions")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        val uri = data?.data ?: return
        if (resultCode != RESULT_OK) return
        try {
            if (requestCode == 201) {
                val bytes = contentResolver.openInputStream(uri)?.use { input ->
                    val output = java.io.ByteArrayOutputStream()
                    val buffer = ByteArray(8192)
                    while (true) { val n = input.read(buffer); if (n < 0) break; require(output.size() + n <= 5_000_000) { "Файл слишком большой" }; output.write(buffer, 0, n) }
                    output.toByteArray()
                } ?: throw IllegalArgumentException("Не удалось открыть файл")
                require(bytes.size <= 5_000_000) { "Файл слишком большой" }
                val incoming = ChatCatalog.parseImported(bytes)
                AlertDialog.Builder(this).setTitle("Заменить список чатов?").setMessage("Будет сохранена копия текущего файла. Записей: ${incoming.size}. CSV не подтверждает аккаунт Telegram: убедитесь, что это тот же аккаунт, иначе чаты могут смешаться.").setNegativeButton("Отмена", null).setPositiveButton("Заменить") { _, _ ->
                    try { saveRows(incoming, allowInvalidReplacement = true); rows = incoming; loaded = true; render() } catch (e: Exception) { message("Не удалось заменить каталог: ${e.message}") }
                }.show()
            } else if (requestCode == 202) {
                check(loaded) { "Сначала восстановите каталог из CSV" }
                contentResolver.openOutputStream(uri)?.use { it.write(ChatCatalog.encode(rows).toByteArray(StandardCharsets.UTF_8)) } ?: throw IllegalArgumentException("Не удалось записать файл")
                message("Каталог сохранён")
            } else if (requestCode == 203) {
                check(loaded) { "Сначала восстановите каталог из CSV" }
                val current = store.load()
                contentResolver.openOutputStream(uri)?.use { it.write(SelectedChatsText.render(current).toByteArray(StandardCharsets.UTF_8)) } ?: throw IllegalArgumentException("Не удалось записать файл")
                message("Список выбранных чатов сохранён")
            }
        } catch (e: Exception) { message("Ошибка каталога: ${e.message}") }
    }
    override fun onDestroy() { alive = false; summaryHandler.removeCallbacks(summaryTicker); worker.shutdownNow(); db.close(); super.onDestroy() }
}
