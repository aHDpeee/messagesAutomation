"""Dashboard metrics backed only by locally stored data."""

from tg_observatory.storage.database import Database
from tg_observatory.storage.repositories.accounts import AccountRepository
from tg_observatory.storage.repositories.dialogs import DialogRepository


class DashboardService:
    def __init__(
        self,
        database: Database,
        accounts: AccountRepository,
        dialogs: DialogRepository,
    ) -> None:
        self._database = database
        self._accounts = accounts
        self._dialogs = dialogs

    def get(self) -> dict[str, object]:
        account = self._accounts.dashboard_record()
        if account is None:
            return {
                "account": None,
                "total_dialogs": 0,
                "dialogs_by_kind": {},
                "indexed_messages": 0,
                "downloaded_media": 0,
                "completed_exports": 0,
                "partial_exports": 0,
                "active_exports": 0,
                "recent_activity": [],
            }
        profile = self._accounts.get_profile()
        assert profile is not None
        account_id = self._accounts.local_id(profile.telegram_id)
        assert account_id is not None
        counts = self._dialogs.count_by_kind(account_id)
        return {
            "account": account,
            "total_dialogs": sum(counts.values()),
            "dialogs_by_kind": counts,
            "indexed_messages": self._count("messages"),
            "downloaded_media": self._count(
                "media", "download_state = ?", ("completed",)
            ),
            "partial_exports": self._count("export_jobs", "state = ?", ("partial",)),
            "completed_exports": self._count("export_jobs", "state = ?", ("completed",)),
            "active_exports": self._count(
                "export_jobs", "state IN (?, ?)", ("pending", "running")
            ),
            "recent_activity": self._dialogs.latest_activity(account_id),
        }

    def _count(self, table: str, where: str = "1 = 1", parameters: tuple[str, ...] = ()) -> int:
        # Table names come only from the constants above; values remain parameterized.
        row = self._database.fetchone(
            f"SELECT COUNT(*) AS count FROM {table} WHERE {where}", parameters
        )
        return int(row["count"]) if row is not None else 0
