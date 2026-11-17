import asyncio
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

from signal_archive import catalog
from signal_archive.direct_service import DirectService


class BlockIntegrationTests(unittest.TestCase):
    def test_export_writes_grouped_files_and_chat_manifest(self):
        async def run(root):
            service = DirectService(None, root / 'state', root / 'exports')
            service.state['dialogs'] = [{'id': '1', 'title': 'One'}, {'id': '2', 'title': 'Two'}]
            block = catalog.create_block(service.state, 'Friends')
            catalog.assign_block(service.state, block, ['1'])
            second = catalog.create_block(service.state, 'Work')
            catalog.assign_block(service.state, second, ['1'])
            service.select(catalog.expand_selection(service.state, ['2'], [block]))
            async def export(client, chat_id, out_dir, **kwargs):
                path = out_dir / f'{chat_id}.csv'
                path.write_text('id\n1\n')
                return {'path': str(path), 'messages': 1, 'last_id': 1}
            with patch('signal_archive.direct_service.export_chat', export), \
                 patch('signal_archive.direct_service.asyncio.sleep', new_callable=AsyncMock), \
                 patch('signal_archive.export_notifications.notify_export_completed'):
                results = await service.export_selected()
            with zipfile.ZipFile(results[0]['path']) as archive:
                names = archive.namelist()
                self.assertIn('chats.txt', names)
                self.assertTrue(any(name.startswith('Friends_') and name.endswith('/1.csv') for name in names))
                self.assertTrue(any(name.startswith('Work_') and name.endswith('/1.csv') for name in names))
                self.assertIn('2.csv', names)
                self.assertIn('One', archive.read('chats.txt').decode('utf-8-sig'))
            self.assertIn(Path(results[0]['path']).name, service.state['archive_files'])
            self.assertTrue((root / 'state' / 'chats.csv').is_file())
        with tempfile.TemporaryDirectory() as temp:
            asyncio.run(run(Path(temp)))
