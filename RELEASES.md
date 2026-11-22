# Preview releases

Android and desktop releases use separate tags, `android-vX.Y.Z` and
`desktop-vX.Y.Z`. The applications check for newer published stable releases
with the matching platform tag and offer to open the GitHub release page.
They do not download or install updates automatically. Draft and pre-release
versions are excluded from the update check.

The Android APK is a debug-signed arm64 preview. It is intended for testing,
not production distribution. A future production release needs a durable
release signing key and an upgrade path for existing installations.

The Linux ZIP is a source distribution and needs Python and its documented
dependencies. The Windows EXE is a windowed x86-64 preview built with Wine;
verify its operation on Windows before calling it a stable release. Linux,
Windows and Android currently differ in functionality; see
[the release plan](RELEASE_IMPLEMENTATION_PLAN.md).
