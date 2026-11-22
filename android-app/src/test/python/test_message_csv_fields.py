import csv
import io
import unittest

from tg_observatory.services.export_service import _csv_bytes


class MessageCsvFieldsTests(unittest.TestCase):
    def test_post_author_survives_intermediate_csv(self):
        header = _csv_bytes(None, header=True)
        row = _csv_bytes({"message_id": 42, "raw": {"post_author": "Channel author"}})
        parsed = next(csv.DictReader(io.StringIO((header + row).decode("utf-8"))))
        self.assertEqual(parsed["post_author"], "Channel author")


if __name__ == "__main__":
    unittest.main()
