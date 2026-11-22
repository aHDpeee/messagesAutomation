#!/usr/bin/env bash
set -Eeuo pipefail

# Keep the log private: build output can contain local paths and device IDs.
umask 077
log_file=$(mktemp "${TMPDIR:-/tmp}/signal-android-build.XXXXXX.log")
latest_link="${TMPDIR:-/tmp}/signal-android-build-$(id -u).latest"
ln -sfn "$log_file" "$latest_link"
exec > >(tee -a "$log_file") 2>&1

project_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
cd "$project_dir"
printf 'Лог: %s\nПоследний лог: %s\nПроект: %s\n' "$log_file" "$latest_link" "$project_dir"
trap 'code=$?; if (( code != 0 )); then printf "\nОшибка (код %s). Лог: %s\n" "$code" "$log_file"; fi' EXIT

# Resolve conflicting SDK variables in this process only. Prefer a usable
# ANDROID_SDK_ROOT, which is already used by the local Android toolchain.
sdk_dir=""
for candidate in "${ANDROID_SDK_ROOT:-}" "${ANDROID_HOME:-}" "$HOME/Android"; do
    if [[ -n $candidate && -d "$candidate/platforms/android-36" ]]; then
        sdk_dir=$candidate
        break
    fi
done
if [[ -z $sdk_dir ]]; then
    printf 'Android SDK с платформой android-36 не найден. Укажи ANDROID_HOME или ANDROID_SDK_ROOT.\n'
    exit 1
fi
export ANDROID_HOME="$sdk_dir"
unset ANDROID_SDK_ROOT
printf 'Android SDK: %s\n' "$ANDROID_HOME"
if [[ -n $sdk_dir && -x "$sdk_dir/platform-tools/adb" ]]; then
    adb_command="$sdk_dir/platform-tools/adb"
elif command -v adb >/dev/null 2>&1; then
    adb_command=$(command -v adb)
else
    adb_command=""
fi

select_device() {
if [[ -z $adb_command ]]; then
    printf 'adb не найден. Установи Android SDK Platform Tools и повтори запуск.\n'
    exit 2
fi
printf '\nПодключённые устройства:\n'
"$adb_command" devices -l
if [[ -n ${ANDROID_SERIAL:-} ]]; then
    device_args=(-s "$ANDROID_SERIAL")
else
    mapfile -t devices < <("$adb_command" devices | awk 'NR > 1 && $2 == "device" {print $1}')
    if (( ${#devices[@]} == 0 )); then
        printf 'Телефон не найден. Подключи его, включи отладку по USB и разреши подключение на телефоне.\n'
        exit 2
    elif (( ${#devices[@]} > 1 )); then
        printf 'Подключено несколько устройств. Запусти: ANDROID_SERIAL=<серийный_номер> ./build-android.sh\n'
        exit 2
    fi
    device_args=(-s "${devices[0]}")
fi
}

if [[ ${1:-} == --diagnose ]]; then
    select_device
    printf '\nКоды последних ошибок авторизации (без номера и API-данных):\n'
    "$adb_command" "${device_args[@]}" exec-out run-as com.signalarchive.app cat files/telegram-engine/observatory.sqlite | python3 -c '
import json, re, sqlite3, sys
db = sqlite3.connect(":memory:")
db.deserialize(sys.stdin.buffer.read())
rows = db.execute("SELECT job_type, state, error_json, result_json FROM export_jobs WHERE job_type IN (?, ?, ?, ?) ORDER BY created_at DESC LIMIT 12", ("account.send_code", "account.verify_code", "account.verify_password", "account.status"))
for operation, state, raw, raw_result in rows:
    try: error = json.loads(raw or "{}")
    except (ValueError, TypeError): error = {}
    code = str(error.get("code", "-"))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", code): code = "unknown"
    kind = str(error.get("exception_type", "-"))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", kind): kind = "unknown"
    try: result = json.loads(raw_result or "{}")
    except (ValueError, TypeError): result = {}
    delivery = result.get("delivery", "-")
    if delivery not in {"telegram", "sms", "fragment_sms", "call", "email", "email_setup", "unknown", "-"}: delivery = "unknown"
    print(f"{operation}: {state}; код: {code}; тип: {kind}; доставка: {delivery}")
'
    printf '\nДиагностика сохранена: %s\n' "$log_file"
    exit 0
fi
if (( $# > 0 )); then
    printf 'Использование: ./build-android.sh [--diagnose]\n'
    exit 2
fi

printf '\nСобираем Android APK…\n'
./gradlew :android-app:assembleDebug --stacktrace
apk="$project_dir/android-app/build/outputs/apk/debug/android-app-debug.apk"
if [[ ! -f "$apk" ]]; then
    printf 'Gradle завершился без ожидаемого APK: %s\n' "$apk"
    exit 1
fi
printf '\nAPK собран: %s\n' "$apk"
select_device
printf '\nУстанавливаем APK…\n'
"$adb_command" "${device_args[@]}" install -r "$apk"
printf '\nГотово: приложение установлено. Лог: %s\n' "$log_file"
