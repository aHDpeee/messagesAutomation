package com.signalarchive.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.res.ColorStateList
import android.graphics.Color
import android.view.View
import android.view.ViewGroup
import android.widget.CompoundButton
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.SeekBar
import android.widget.TextView

internal object Appearance {
    val names = arrayOf("Оранжевый", "Голубой", "Зелёный", "Фиолетовый", "Розовый")
    private val themes = arrayOf("AppTheme", "AppThemeBlue", "AppThemeGreen", "AppThemePurple", "AppThemePink")
    private val colors = intArrayOf(Color.rgb(255, 112, 55), Color.rgb(103, 183, 255), Color.rgb(129, 201, 149), Color.rgb(207, 159, 255), Color.rgb(255, 145, 185))
    fun selected(context: Context): Int = context.getSharedPreferences("appearance", Context.MODE_PRIVATE).getInt("accent", 0).coerceIn(0, names.lastIndex)
    fun isCustom(context: Context): Boolean = context.getSharedPreferences("appearance", Context.MODE_PRIVATE).contains("custom_accent")
    fun color(context: Context): Int = context.getSharedPreferences("appearance", Context.MODE_PRIVATE).let { prefs ->
        if (prefs.contains("custom_accent")) prefs.getInt("custom_accent", colors[0]) else colors[selected(context)]
    }
    fun apply(activity: Activity) {
        val style = activity.resources.getIdentifier(themes[selected(activity)], "style", activity.packageName)
        if (style != 0) activity.setTheme(style)
    }
    fun applyExportDialog(activity: Activity) {
        val styles = intArrayOf(R.style.ExportDialogTheme, R.style.ExportDialogBlue, R.style.ExportDialogGreen, R.style.ExportDialogPurple, R.style.ExportDialogPink)
        activity.setTheme(styles[selected(activity)])
    }
    fun save(context: Context, index: Int) {
        require(index in names.indices)
        check(context.getSharedPreferences("appearance", Context.MODE_PRIVATE).edit().putInt("accent", index).remove("custom_accent").commit())
    }

    fun tint(view: View, color: Int) {
        val tint = ColorStateList.valueOf(color)
        when (view) {
            is CompoundButton -> view.buttonTintList = tint
            is EditText -> if (view.background !is android.graphics.drawable.GradientDrawable) view.backgroundTintList = tint
            is ProgressBar -> { view.progressTintList = tint; view.indeterminateTintList = tint }
        }
        if (view is ViewGroup) for (i in 0 until view.childCount) tint(view.getChildAt(i), color)
    }

    fun showOptions(activity: Activity, saved: () -> Unit) {
        AlertDialog.Builder(activity).setTitle("Цвет интерфейса")
            .setSingleChoiceItems(names, if (isCustom(activity)) -1 else selected(activity)) { dialog, index ->
                try { save(activity, index); dialog.dismiss(); saved() }
                catch (_: Exception) { android.widget.Toast.makeText(activity, "Не удалось сохранить цвет", android.widget.Toast.LENGTH_SHORT).show() }
            }.setNeutralButton("Свой цвет") { _, _ -> showPicker(activity, saved) }
            .setNegativeButton("Отмена", null).show()
    }

    fun showPicker(activity: Activity, saved: () -> Unit) {
        val initial = color(activity)
        val components = intArrayOf(Color.red(initial), Color.green(initial), Color.blue(initial))
        val layout = LinearLayout(activity).apply { orientation = LinearLayout.VERTICAL; setPadding(32, 12, 32, 12) }
        val preview = TextView(activity).apply { text = "Предпросмотр"; gravity = android.view.Gravity.CENTER; textSize = 18f; setPadding(12, 24, 12, 24) }
        layout.addView(preview)
        fun update() {
            val chosen = Color.rgb(components[0], components[1], components[2])
            preview.setBackgroundColor(chosen)
            val brightness = components[0] * 299 + components[1] * 587 + components[2] * 114
            preview.setTextColor(if (brightness >= 150_000) Color.BLACK else Color.WHITE)
        }
        listOf("Красный", "Зелёный", "Синий").forEachIndexed { index, label ->
            layout.addView(TextView(activity).apply { text = label })
            layout.addView(SeekBar(activity).apply {
                max = 255; progress = components[index]
                setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                    override fun onProgressChanged(bar: SeekBar?, value: Int, fromUser: Boolean) { components[index] = value; update() }
                    override fun onStartTrackingTouch(bar: SeekBar?) = Unit
                    override fun onStopTrackingTouch(bar: SeekBar?) = Unit
                })
            })
        }
        update()
        AlertDialog.Builder(activity).setTitle("Свой цвет").setView(layout).setNegativeButton("Отмена", null)
            .setPositiveButton("Применить") { _, _ ->
                if (activity.getSharedPreferences("appearance", Context.MODE_PRIVATE).edit()
                    .putInt("custom_accent", Color.rgb(components[0], components[1], components[2])).commit()) saved()
                else android.widget.Toast.makeText(activity, "Не удалось сохранить цвет", android.widget.Toast.LENGTH_SHORT).show()
            }.show()
    }
}
