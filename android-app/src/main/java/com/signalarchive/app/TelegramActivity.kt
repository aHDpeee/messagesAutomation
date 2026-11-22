package com.signalarchive.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Intent
import android.content.ClipData
import android.net.Uri
import android.os.Bundle
import android.graphics.Bitmap
import android.graphics.Color
import android.text.InputType
import android.view.WindowManager
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import org.json.JSONObject
import com.google.zxing.BarcodeFormat
import com.google.zxing.qrcode.QRCodeWriter
import java.util.concurrent.Executors
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Explicit, user-initiated Telegram authorization and dialog discovery. */
class TelegramActivity : Activity() {
    private val worker = Executors.newSingleThreadExecutor()
    private lateinit var credentials: SecureCredentialStore
    private lateinit var id: EditText
    private lateinit var hash: EditText
    private lateinit var password: EditText
    private lateinit var status: TextView
    private val actionButtons = mutableListOf<Button>()
    private var busy = false
    private enum class LoginState { CHECKING, UNKNOWN, SIGNED_OUT, PASSWORD, SIGNED_IN }
    private val setupViews = mutableListOf<View>()
    private val signedInViews = mutableListOf<View>()
    private val passwordViews = mutableListOf<View>()
    private var setupStep = 0
    private var passwordPending = false
    private lateinit var setupHint: TextView
    private lateinit var nextButton: Button
    private lateinit var backButton: Button
    private lateinit var qrButton: Button
    private lateinit var checkButton: Button
    private lateinit var restoreButton: Button
    private var autoAddPending = false
    private var sessionActive = false
    private var autoAddButton: Button? = null
    private fun showState(state: LoginState) = runOnUiThread {
        if (!alive) return@runOnUiThread
        if (state == LoginState.PASSWORD) passwordPending = true
        if (state == LoginState.SIGNED_IN || state == LoginState.SIGNED_OUT) passwordPending = false
        sessionActive = state == LoginState.SIGNED_IN
        val setup = state == LoginState.SIGNED_OUT || state == LoginState.UNKNOWN || state == LoginState.PASSWORD
        val credentialsStep = state == LoginState.UNKNOWN || (state == LoginState.SIGNED_OUT && setupStep == 0)
        setupViews.forEach { it.visibility = if (credentialsStep) View.VISIBLE else View.GONE }
        setupHint.visibility = if (setup) View.VISIBLE else View.GONE
        setupHint.text = when {
            state == LoginState.PASSWORD -> "Шаг 3 · Введите пароль двухэтапной проверки Telegram."
            credentialsStep -> "Шаг 1 · Получите API ID и API Hash на my.telegram.org и введите их ниже."
            else -> "Шаг 2 · Создайте QR-код, отправьте его себе и откройте на другом экране. Затем отсканируйте его официальным Telegram на телефоне."
        }
        signedInViews.forEach { it.visibility = if (state == LoginState.SIGNED_IN) View.VISIBLE else View.GONE }
        passwordViews.forEach { it.visibility = if (state == LoginState.PASSWORD) View.VISIBLE else View.GONE }
        qrButton.visibility = if ((state == LoginState.SIGNED_OUT && setupStep == 1) || state == LoginState.PASSWORD) View.VISIBLE else View.GONE
        nextButton.visibility = if (state == LoginState.SIGNED_OUT && setupStep == 0) View.VISIBLE else View.GONE
        backButton.visibility = if (state == LoginState.SIGNED_OUT && setupStep == 1) View.VISIBLE else View.GONE
        checkButton.visibility = if (state == LoginState.UNKNOWN) View.VISIBLE else View.GONE
        val backups = java.io.File(filesDir, "telegram-workspaces").listFiles()?.any { it.isDirectory && java.io.File(it, "snapshot.sha256").isFile } == true
        restoreButton.visibility = if (state != LoginState.CHECKING && backups) View.VISIBLE else View.GONE
    }
    @Volatile private var qrWaitThread: Thread? = null
    @Volatile private var alive = true
    private fun update(message: String) = runOnUiThread { if (alive) status.text = message }
    private fun finishOnboarding() {
        val prefs = getSharedPreferences("onboarding", MODE_PRIVATE)
        if (prefs.getBoolean("opened", false) && !prefs.getBoolean("completed", false))
            prefs.edit().putBoolean("completed", true).putBoolean("guide_pending", true).apply()
    }
    private fun field(hintText: String, secret: Boolean = false) = EditText(this).apply {
        hint = hintText
        if (secret) inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
    }
    private fun params(): JSONObject {
        val apiId = id.text.toString().trim()
        val apiHash = hash.text.toString().trim()
        require(apiId.toLongOrNull()?.let { it > 0 } == true) { "Укажите API ID" }
        require(apiHash.matches(Regex("[0-9a-fA-F]{32}"))) { "Укажите API Hash из 32 символов" }
        credentials.save(ApiCredentials(apiId, apiHash))
        return JSONObject().put("api_id", apiId).put("api_hash", apiHash)
    }
    private fun runTask(label: String, action: () -> Unit) {
        if (busy) return
        busy = true
        actionButtons.forEach { it.isEnabled = false }
        update(label)
        worker.execute {
            try { action() } catch (error: Exception) {
                val message = if (error is InterruptedException) "Операция отменена." else error.message ?: "Операция не выполнена"
                update(message)
                if (intent.getBooleanExtra("auto_add", false)) runOnUiThread { if (alive) android.widget.Toast.makeText(this, message, android.widget.Toast.LENGTH_LONG).show() }
            }
            finally { runOnUiThread { if (alive) {
                busy = false; actionButtons.forEach { it.isEnabled = true }
                if (autoAddPending && sessionActive) { autoAddPending = false; autoAddButton?.performClick() }
            } } }
        }
    }
    private fun confirmUnboundCatalog(): Boolean {
        val result = java.util.concurrent.atomic.AtomicBoolean(false)
        val done = CountDownLatch(1)
        runOnUiThread {
            if (!alive) { done.countDown(); return@runOnUiThread }
            AlertDialog.Builder(this).setTitle("Подтвердите аккаунт каталога")
                .setMessage("Существующий CSV не содержит подтверждённого аккаунта. Убедитесь, что все чаты относятся к текущему аккаунту Telegram. При ошибке чаты разных аккаунтов могут смешаться.")
                .setNegativeButton("Отмена") { _, _ -> done.countDown() }
                .setPositiveButton("Да, это мой аккаунт") { _, _ -> result.set(true); done.countDown() }
                .setOnCancelListener { done.countDown() }.show()
        }
        while (!done.await(1, TimeUnit.SECONDS)) if (!alive) return false
        return result.get()
    }
    override fun onCreate(savedInstanceState: Bundle?) {
        Appearance.apply(this)
        super.onCreate(savedInstanceState)
        java.io.File(cacheDir, "qr-share").listFiles()?.filter { it.name.matches(Regex("login-[0-9a-f-]{36}\\.png")) }?.forEach { it.delete() }
        credentials = SecureCredentialStore(this)
        autoAddPending = savedInstanceState?.getBoolean("auto_add_pending") ?: intent.getBooleanExtra("auto_add", false)
        val body = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL; setPadding(24, 24, 24, 24) }
        body.addView(TextView(this).apply { text = "Подключение Telegram"; textSize = 24f })
        setupHint = TextView(this)
        body.addView(setupHint)
        val website = Button(this).apply { text = "Открыть my.telegram.org"; setOnClickListener { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://my.telegram.org"))) } }
        body.addView(website); setupViews.add(website)
        id = field("API ID"); hash = field("API Hash", true); password = field("Пароль двухэтапной проверки", true)
        listOf(id, hash).forEach { body.addView(it); setupViews.add(it) }
        body.addView(password); passwordViews.add(password)
        var savedCredentials: ApiCredentials? = null
        var credentialsError = false
        try { savedCredentials = credentials.load()?.also { id.setText(it.apiId); hash.setText(it.apiHash) } } catch (_: Exception) { credentialsError = true }
        setupStep = savedInstanceState?.getInt("setup_step", if (savedCredentials != null) 1 else 0) ?: if (savedCredentials != null) 1 else 0
        passwordPending = savedInstanceState?.getBoolean("password_pending", false) ?: false
        status = TextView(this).apply { text = "Введите API ID и API Hash, затем войдите по QR." }
        body.addView(status)
        fun button(title: String, onClick: () -> Unit) = Button(this).apply {
            text = title
            setOnClickListener { if (!busy) try { onClick() } catch (error: Exception) { update(error.message ?: "Операция не выполнена") } }
            actionButtons.add(this)
        }
        nextButton = button("Продолжить") { params(); setupStep = 1; showState(LoginState.SIGNED_OUT); update("Подготовьте второй экран для QR-кода.") }
        backButton = button("Изменить API-данные") { setupStep = 0; showState(LoginState.SIGNED_OUT) }
        body.addView(nextButton); body.addView(backButton)
        checkButton = button("Повторить проверку") {
            val p = params(); showState(LoginState.CHECKING); runTask("Проверяем сессию…") {
                try { val r = TelegramBridge.runJob(this, "account.status", p); val active = r.optString("status") == "authenticated"; if (active) UserGreeting.save(this, r.optJSONObject("account")); showState(if (active) LoginState.SIGNED_IN else LoginState.SIGNED_OUT); update(if (active) "Telegram подключён." else "Войдите по QR-коду.") }
                catch (error: Exception) { showState(LoginState.UNKNOWN); throw error }
            }
        }; body.addView(checkButton)
        qrButton = button("Войти по QR-коду") {
            val p = params()
            runTask("Создаём QR-код…") {
                qrWaitThread = Thread.currentThread()
                var qrDialog: AlertDialog? = null
                var qrFile: java.io.File? = null
                try {
                    val result = TelegramBridge.runJob(applicationContext, "account.qr_start", p)
                    val url = result.getString("url")
                    require(url.startsWith("tg://login?token=") && url.length <= 1024) { "Telegram вернул неверный код входа" }
                    val matrix = QRCodeWriter().encode(url, BarcodeFormat.QR_CODE, 512, 512)
                    val pixels = IntArray(512 * 512) { index -> if (matrix[index % 512, index / 512]) Color.BLACK else Color.WHITE }
                    val bitmap = Bitmap.createBitmap(512, 512, Bitmap.Config.ARGB_8888).apply { setPixels(pixels, 0, 512, 0, 0, 512, 512) }
                    val folder = java.io.File(cacheDir, "qr-share")
                    check(folder.isDirectory || folder.mkdirs()) { "Не удалось подготовить QR-код" }
                    val image = java.io.File(folder, "login-${java.util.UUID.randomUUID()}.png"); qrFile = image
                    image.outputStream().use { check(bitmap.compress(Bitmap.CompressFormat.PNG, 100, it)) { "Не удалось сохранить QR-код" } }
                    val shareUri = Uri.Builder().scheme("content").authority("$packageName.qr").appendPath(image.name).build()
                    runOnUiThread {
                        if (alive) {
                            window.addFlags(WindowManager.LayoutParams.FLAG_SECURE)
                            qrDialog = AlertDialog.Builder(this).setTitle("Вход через Telegram")
                                .setMessage("Отправьте QR себе и откройте на другом экране, затем в Telegram выберите Настройки → Устройства → Подключить устройство. QR даёт доступ к аккаунту и быстро истекает: никому другому его не отправляйте.")
                                .setView(ImageView(this).apply { setImageBitmap(bitmap); adjustViewBounds = true })
                                .setNeutralButton("Поделиться QR") { _, _ -> }
                                .setNegativeButton("Отмена", null).create().apply {
                                    setOnDismissListener { this@TelegramActivity.window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE); qrWaitThread?.interrupt() }
                                    show()
                                    getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener {
                                        val intent = Intent(Intent.ACTION_SEND).setType("image/png")
                                            .putExtra(Intent.EXTRA_STREAM, shareUri)
                                            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                            .apply { clipData = ClipData.newRawUri("QR", shareUri) }
                                        runCatching { startActivity(Intent.createChooser(intent, "Отправить QR себе")) }
                                            .onFailure { status.text = "Не удалось открыть меню отправки" }
                                    }
                                }
                        }
                    }
                    val finished = TelegramBridge.runJob(applicationContext, "account.qr_wait", JSONObject())
                    if (finished.optString("status") == "authenticated") runCatching {
                        val profile = TelegramBridge.runJob(applicationContext, "account.status", p)
                        UserGreeting.save(applicationContext, profile.optJSONObject("account"))
                    }
                    if (finished.optString("status") == "authenticated") finishOnboarding()
                    update(when (finished.optString("status")) {
                        "authenticated" -> { showState(LoginState.SIGNED_IN); "Telegram подключён. Обновите список чатов." }
                        "password_required" -> { showState(LoginState.PASSWORD); "Введите пароль двухэтапной проверки и нажмите «Подтвердить пароль»." }
                        "expired" -> { showState(LoginState.SIGNED_OUT); "Срок действия QR-кода истёк. Создайте новый код." }
                        else -> "Не удалось завершить вход."
                    })
                } finally {
                    qrWaitThread = null
                    runOnUiThread { qrDialog?.dismiss(); window.clearFlags(WindowManager.LayoutParams.FLAG_SECURE) }
                    qrFile?.delete()
                }
            }
        }; body.addView(qrButton)
        val switchButton = button("Сменить аккаунт") {
            AlertDialog.Builder(this).setTitle("Сменить аккаунт Telegram?")
                .setMessage("Текущая сессия и база будут сохранены отдельно, каталог — в резервной копии. Расписание отключится; для нового аккаунта потребуется вход и новый список чатов. Сохранённые ZIP не удаляются.")
                .setNegativeButton("Отмена", null)
                .setPositiveButton("Продолжить") { _, _ ->
                    runTask("Подготавливаем новый аккаунт…") {
                        ExportLock.acquire(filesDir).use { TelegramBridge.prepareAccountSwitch(applicationContext) }
                        showState(LoginState.SIGNED_OUT)
                        update("Можно войти в другой аккаунт. После входа обновите список чатов и настройте расписание.")
                    }
                }.show()
        }; body.addView(switchButton); signedInViews.add(switchButton)
        restoreButton = button("Восстановить сохранённый аккаунт") {
            runTask("Проверяем резервные копии…") {
            val backups = WorkspaceArchive.available(filesDir)
            runOnUiThread {
            if (!alive) return@runOnUiThread
            if (backups.isEmpty()) {
                AlertDialog.Builder(this).setMessage("Проверенных копий аккаунта пока нет.").setPositiveButton("Понятно", null).show()
            } else {
                val labels = backups.map { file ->
                    val account = runCatching { JSONObject(file.resolve("account-settings.json").readText()).optJSONObject("catalog_account")?.optString("telegram_id").orEmpty() }.getOrDefault("")
                    val date = java.text.DateFormat.getDateTimeInstance().format(java.util.Date(file.lastModified()))
                    "$date · ${if (account.isBlank()) "аккаунт не указан" else "Telegram $account"}"
                }.toTypedArray()
                AlertDialog.Builder(this).setTitle("Выберите сохранённый аккаунт").setItems(labels) { _, index ->
                    AlertDialog.Builder(this).setTitle("Восстановить аккаунт?")
                        .setMessage("Текущий аккаунт будет сохранён отдельно. Расписание восстановленного аккаунта останется выключенным до проверки.")
                        .setNegativeButton("Отмена", null).setPositiveButton("Восстановить") { _, _ ->
                            runTask("Восстанавливаем аккаунт…") {
                                ExportLock.acquire(filesDir).use { TelegramBridge.restoreAccount(applicationContext, backups[index].name) }
                                val restored = credentials.load()
                                runOnUiThread { if (alive && restored != null) { id.setText(restored.apiId); hash.setText(restored.apiHash) } }
                                if (restored != null) {
                                    try {
                                        val result = TelegramBridge.runJob(applicationContext, "account.status", JSONObject().put("api_id", restored.apiId).put("api_hash", restored.apiHash))
                                        val active = result.optString("status") == "authenticated"
                                        if (active) UserGreeting.save(applicationContext, result.optJSONObject("account"))
                                        showState(if (active) LoginState.SIGNED_IN else LoginState.SIGNED_OUT)
                                        update(if (active) "Аккаунт восстановлен. Проверьте расписание." else "Аккаунт восстановлен; войдите по QR.")
                                    } catch (error: Exception) { showState(LoginState.UNKNOWN); throw error }
                                } else { showState(LoginState.UNKNOWN); update("Аккаунт восстановлен. Укажите API-данные и проверьте сессию.") }
                            }
                        }.show()
                }.setNegativeButton("Отмена", null).show()
            }
            }
            }
        }; body.addView(restoreButton)
        val passwordButton = button("Подтвердить пароль") {
            val p = params().put("password", password.text.toString()); runTask("Проверяем пароль…") { TelegramBridge.runJob(this, "account.verify_password", p); runOnUiThread { password.text.clear() }; finishOnboarding(); showState(LoginState.SIGNED_IN); update("Telegram подключён. Обновите список чатов.") }
        }; body.addView(passwordButton); passwordViews.add(passwordButton)
        val reference = field("Ссылка Telegram, @username, ID или название")
        reference.setText(savedInstanceState?.getString("reference") ?: intent.getStringExtra("reference").orEmpty())
        body.addView(reference); signedInViews.add(reference)
        val addButton = button("Добавить чат или запросить вступление") {
            val value = reference.text.toString().trim()
            require(value.isNotEmpty()) { "Укажите ссылку, имя или ID чата" }
            val p = params()
            runTask("Проверяем чат в Telegram…") {
                val account = TelegramBridge.runJob(applicationContext, "account.status", p)
                check(account.optString("status") == "authenticated") { "Сначала войдите в Telegram." }
                val accountId = account.optJSONObject("account")?.optString("telegram_id").orEmpty()
                val rows = ChatCatalog(filesDir.resolve("chats.csv")).load()
                val stored = getSharedPreferences("catalog_account", MODE_PRIVATE).getString("telegram_id", null)
                CatalogAccountBinding.validate(stored, accountId, rows.isNotEmpty())
                if (stored == null) check(getSharedPreferences("catalog_account", MODE_PRIVATE).edit().putString("telegram_id", accountId).commit()) { "Не удалось привязать каталог к аккаунту" }
                val targetBlock = intent.getStringExtra("target_block")?.trim().orEmpty()
                val result = TelegramBridge.runJob(applicationContext, "dialogs.add", JSONObject(p.toString()).put("reference", value).put("expected_account_id", accountId).put("verify_membership", PendingRequests.contains(applicationContext, value)))
                val requested = result.optString("status") == "requested"
                if (requested) {
                    PendingRequests.add(applicationContext, value, result.optString("title").ifBlank { result.optJSONObject("dialog")?.optString("title").orEmpty() }, accountId, result.optJSONObject("dialog")?.optString("id").orEmpty(), targetBlock)
                } else {
                    val dialog = result.optJSONObject("dialog") ?: error("Telegram не вернул данные чата. Обновите список.")
                    val catalog = ChatCatalog(filesDir.resolve("chats.csv"))
                    val (current, version) = catalog.loadVersioned()
                    val ident = dialog.getString("id")
                    if (current.none { it["id"] == ident }) {
                        val type = when (dialog.optString("type")) { "private" -> "chat"; "group", "channel", "bot", "chat" -> dialog.optString("type"); else -> "chat" }
                        current.add(ChatCatalog.columns.associateWith { "" }.toMutableMap().apply {
                            put("id", ident); put("title", dialog.optString("title").ifBlank { ident }); put("type", type)
                            val username = dialog.optString("username", "").let { if (it == "null") "" else it }
                            put("username", username); put("link", TelegramLink.refreshed("", "", username)); put("added_via", "Telegram: ${result.optString("method")}"); put("added_at", java.time.Instant.now().toString())
                            if (targetBlock.isNotBlank()) put("blocks", BlockEdits.canonical(current, targetBlock))
                        })
                        catalog.save(current, expectedVersion = version)
                    } else if (targetBlock.isNotBlank()) {
                        val updated = BlockEdits.assign(current, ident, targetBlock)
                        catalog.save(updated, expectedVersion = version)
                    }
                    if (result.optString("status") in listOf("joined", "already")) PendingRequests.removeReference(applicationContext, value)
                }
                val indexed = if (requested) false else runCatching { val sync = TelegramBridge.runJob(applicationContext, "dialogs.sync", p); DialogAvailabilityStore.save(applicationContext, accountId, sync); true }.getOrDefault(false)
                val message = if (requested && targetBlock.isNotBlank()) "Запрос отправлен. После одобрения обновите список, чтобы добавить чат в блок «$targetBlock»." else if (requested) "Запрос отправлен. После одобрения обновите список чатов и назначьте блок." else if (targetBlock.isNotBlank() && indexed) "Чат добавлен в блок «$targetBlock»." else if (targetBlock.isNotBlank()) "Чат добавлен в блок «$targetBlock». Обновите список Telegram перед экспортом." else if (indexed) "Чат добавлен в каталог без блока. Назначьте блок для экспорта." else "Чат добавлен в каталог. Обновите список Telegram, затем назначьте блок для экспорта."
                update(message)
                runOnUiThread { if (alive) {
                    android.widget.Toast.makeText(this, message, android.widget.Toast.LENGTH_LONG).show()
                    if (intent.getBooleanExtra("auto_add", false)) finish()
                    else AlertDialog.Builder(this).setMessage(message).setPositiveButton("Понятно", null).show()
                } }
            }
        }; body.addView(addButton); signedInViews.add(addButton); autoAddButton = addButton
        val syncButton = button("Обновить список чатов") {
            val p = params(); runTask("Получаем чаты…") {
                val account = TelegramBridge.runJob(this, "account.status", p)
                check(account.optString("status") == "authenticated") { "Сначала войдите в Telegram." }
                val accountId = account.optJSONObject("account")?.optString("telegram_id").orEmpty()
                val catalog = ChatCatalog(filesDir.resolve("chats.csv")); val (rows, catalogVersion) = catalog.loadVersioned()
                val binding = getSharedPreferences("catalog_account", MODE_PRIVATE)
                val existingAccount = binding.getString("telegram_id", null)
                val acceptedUnbound = existingAccount == null && rows.isNotEmpty() && confirmUnboundCatalog()
                CatalogAccountBinding.validate(existingAccount, accountId, rows.isNotEmpty(), acceptedUnbound)
                if (existingAccount == null) {
                    check(binding.edit().putString("telegram_id", accountId).commit()) { "Не удалось сохранить привязку каталога к аккаунту." }
                }
                val sync = TelegramBridge.runJob(this, "dialogs.sync", p) { if (it.isNotBlank()) update(it) }
                val byId = rows.associateBy { it["id"].orEmpty() }.toMutableMap()
                var offset = 0
                do {
                    val result = TelegramBridge.dispatch(this, "dialogs.list", JSONObject().put("limit", 500).put("offset", offset))
                    val items = result.getJSONArray("items")
                    for (i in 0 until items.length()) {
                        val dialog = items.getJSONObject(i); val ident = dialog.getString("telegram_id")
                        val row = byId.getOrPut(ident) { ChatCatalog.columns.associateWith { "" }.toMutableMap().also(rows::add) }
                        row["id"] = ident; row["title"] = dialog.optString("title", ident)
                        row["type"] = when (dialog.optString("kind")) { "private" -> "chat"; "group" -> "group"; "channel" -> "channel"; "bot" -> "bot"; else -> "chat" }
                        val username = dialog.optString("username", "").let { if (it == "null") "" else it }
                        row["link"] = TelegramLink.refreshed(row["username"].orEmpty(), row["link"].orEmpty(), username)
                        row["username"] = username
                        val lastMessage = dialog.optString("last_message_at", "").let { if (it == "null") "" else it }
                        if (lastMessage.isNotBlank()) row["last_message_date"] = lastMessage
                        if (row["added_via"].isNullOrBlank()) row["added_via"] = "Telegram"
                        if (row["added_at"].isNullOrBlank()) row["added_at"] = java.time.Instant.now().toString()
                    }
                    offset += items.length()
                } while (items.length() == 500)
                val approvedKeys = mutableListOf<String>()
                fun acceptPending(key: String, entry: JSONObject, chatId: String) {
                    val requestedBlock = entry.optString("target_block", "").trim()
                    if (PendingBlock.apply(rows, chatId, requestedBlock)) approvedKeys.add(key)
                }
                val synced = sync.optJSONArray("synced_ids")
                if (synced != null) {
                    val ids = (0 until synced.length()).map { synced.getString(it) }.toSet()
                    DialogAvailabilityStore.save(applicationContext, accountId, sync)
                    val pending = PendingRequests.list(applicationContext).filter { it.second.optString("account_id") == accountId }
                    pending.forEach { (key, entry) ->
                        val id = entry.optString("chat_id")
                        if (id.isNotBlank() && id in ids) acceptPending(key, entry, id)
                    }
                    val candidates = pending.filter { (_, entry) ->
                        entry.optString("account_id") == accountId && entry.optString("chat_id").isBlank() &&
                            entry.optString("reference").matches(Regex("https://(?:www\\.)?(?:t\\.me|telegram\\.me)/(?:\\+|joinchat/)[A-Za-z0-9_-]+/?", RegexOption.IGNORE_CASE))
                    }
                    val invites = PendingRequests.ordered(applicationContext, candidates, 20)
                    for ((index, item) in invites.withIndex()) {
                        if (index > 0) Thread.sleep(500)
                        try {
                            val check = TelegramBridge.runJob(applicationContext, "dialogs.check_invite", JSONObject(p.toString()).put("reference", item.second.optString("reference")).put("expected_account_id", accountId))
                            val id = check.optString("chat_id")
                            if (check.optString("status") == "already" && id in ids) acceptPending(item.first, item.second, id)
                            PendingRequests.markChecked(applicationContext, item.first)
                        } catch (error: TelegramJobException) {
                            if (error.code == "flood_wait") break
                            PendingRequests.markChecked(applicationContext, item.first)
                        }
                    }
                }
                if (rows.isNotEmpty()) {
                    catalog.save(rows, expectedVersion = catalogVersion)
                    approvedKeys.forEach { PendingRequests.remove(applicationContext, it) }
                }
                update("Обновлено чатов: ${rows.size}. Откройте каталог для выбора групп.")
            }
        }; body.addView(syncButton); signedInViews.add(syncButton)
        val doneButton = button("Перейти к чатам") { finish() }
        body.addView(doneButton); signedInViews.add(doneButton)
        setContentView(ScrollView(this).apply { addView(body) })
        SystemInsets.apply(this, findViewById<android.view.ViewGroup>(android.R.id.content).getChildAt(0))
        Appearance.tint(window.decorView, Appearance.color(this))
        showState(if (savedCredentials != null) LoginState.CHECKING else LoginState.SIGNED_OUT)
        if (credentialsError) {
            showState(LoginState.UNKNOWN)
            update("Не удалось прочитать сохранённые данные Telegram. Проверьте настройки устройства и данные API.")
        } else savedCredentials?.let { saved ->
            val existing = JSONObject().put("api_id", saved.apiId).put("api_hash", saved.apiHash)
            runTask("Проверяем сохранённую сессию…") {
                try {
                    val result = TelegramBridge.runJob(applicationContext, "account.status", existing)
                    val active = result.optString("status") == "authenticated"
                    if (active) UserGreeting.save(applicationContext, result.optJSONObject("account"))
                    showState(if (active) LoginState.SIGNED_IN else if (passwordPending) LoginState.PASSWORD else LoginState.SIGNED_OUT)
                    update(if (active) "Telegram подключён. Можно обновить список чатов." else if (passwordPending) "Введите пароль двухэтапной проверки." else "Войдите в Telegram по QR-коду.")
                } catch (error: Exception) { showState(LoginState.UNKNOWN); throw error }
            }
        }
        if (savedInstanceState?.getBoolean("busy") == true) update("Операция могла прерваться. Проверьте состояние и повторите при необходимости.")
    }
    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        outState.putBoolean("busy", busy)
        outState.putInt("setup_step", setupStep)
        outState.putBoolean("password_pending", passwordPending)
        outState.putBoolean("auto_add_pending", autoAddPending)
        outState.putString("reference", intent.getStringExtra("reference").orEmpty())
    }
    override fun onDestroy() { alive = false; worker.shutdownNow(); super.onDestroy() }
}
