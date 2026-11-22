# Tegash — Android preview

Tegash is an experimental Kotlin Android app with embedded Telethon. It connects
to Telegram, exports messages as CSV files in ZIP archives, and can export
visible group members. It also opens **unencrypted** CSV and ZIP exports from
the desktop app for local search. Media is not downloaded.

The chat catalog can add chats already present in the local
archive, assign each chat to one named group, select `Ungrouped`, or leave it
unassigned. The catalog can be exchanged with the desktop app as `chats.csv`;
replacement is validated and confirmed, with a local backup. Tabs represent
Ungrouped, user blocks and inactive chats; long-press or double-tap a chat to
open its actions. A chat belongs to at most one export block.

The in-progress Telegram integration uses embedded Python and Telethon via
Chaquopy. The Android sign-in screen uses a QR code which can be shared to
another screen and scanned with an already signed-in Telegram app. It includes
dialog sync, message and member CSV exports, and an Android-scheduled daily
export. The user confirmed QR sign-in on a device; the remaining workflows
still need end-to-end verification. Android may delay scheduled work;
member visibility depends on Telegram. The current build targets arm64-v8a
only. Password-protected ZIP support and desktop feature parity are not yet
complete; this is not yet a replacement for the desktop exporter.

The Telegram screen has an experimental account-switch action. It disables
the schedule, backs up the catalog, closes an idle engine, and preserves its
database, account settings and encrypted session before starting a new one.
The Telegram screen can also restore a verified saved account, keeping the
current account separately; the restored schedule stays disabled until the
user checks it. These flows have not yet been verified on a device.

Build an experimental debug APK with Android SDK 36, JDK 17, and a host
`python3.14` executable on `PATH`. Gradle needs network access on the first
build to resolve Android, Chaquopy, and Telethon dependencies. Configure
`ANDROID_HOME` to point to the Android SDK if it is not already configured:

On Linux, `./build-android.sh` builds and installs the APK on a connected,
authorized Android device. It prints the private log path; the latest log is
also linked at `/tmp/signal-android-build-<uid>.latest`. If several devices are
connected, set `ANDROID_SERIAL` to choose one.

```sh
ANDROID_HOME="$HOME/Android" ./gradlew :android-app:assembleDebug
```

Adjust the SDK path if needed. The output is
`android-app/build/outputs/apk/debug/android-app-debug.apk`; install it only
on an arm64 Android 8.0 or later device. It is a debug build. QR sign-in and
installation have been exercised on a real device, but successful installation
does not establish that every export and scheduled workflow works correctly.
The Gradle wrapper and Android/Kotlin plugin versions match the local
`crmOfficialMvp` toolchain. Import is transactional: a malformed archive does
not leave a partially imported database. Exports are limited to 256 MiB of
uncompressed content per import; the input is read without extracting paths.

Parser-only checks can run without an Android device:

```sh
kotlinc -jvm-target 17 -include-runtime -d /tmp/signal-csv-check.jar \
  android-app/src/main/java/com/signalarchive/app/CsvReader.kt \
  android-app/src/test/kotlin/com/signalarchive/app/CsvReaderCheck.kt
java -jar /tmp/signal-csv-check.jar
```

With Telethon installed in a Python environment, run the Android backend tests:

```sh
PYTHONPATH="$PWD/android-app/src/main/python" python -m unittest discover -s android-app/src/test/python -v
```
