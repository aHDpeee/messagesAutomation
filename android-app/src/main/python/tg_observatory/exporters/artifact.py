"""Shared single-file Markdown artifact lifecycle for every report export."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from tg_observatory.exceptions import InvalidRequest
from tg_observatory.storage.repositories.exports import ExportRepository
from tg_observatory.storage.time import utc_now


def safe_filename(title: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]', '_', title).strip(' .')
    return name.encode('utf-8')[:160].decode('utf-8', errors='ignore') or 'Telegram export'


class MarkdownArtifact:
    """Stream into a private partial file; publish only after footer and checksum exist.

    Use as a synchronous context manager around cooperative async retrieval loops.
    Incomplete/failed writes remain unpublished and a restart uses a new job ID.
    """
    def __init__(self, export_dir: str | Path, job_id: str, title: str, export_type: str,
                 metadata: dict[str, Any] | None = None, *,
                 exports: ExportRepository | None = None) -> None:
        if not job_id or Path(job_id).name != job_id or job_id in {'.', '..'}:
            raise InvalidRequest('Invalid export job ID.')
        self.root = Path(export_dir)
        self.directory = self.root / job_id
        self.title, self.export_type = title, export_type
        self.metadata = metadata or {}
        self.exports = exports
        self.filename = f'{utc_now()[:10]} - {safe_filename(title)}.md'
        self.path = self.directory / self.filename
        self.part = self.directory / (self.filename + '.part')
        self._stream: Any = None
        self._digest = hashlib.sha256()
        self._complete = False

    def __enter__(self) -> MarkdownArtifact:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists() or self.part.exists():
            raise InvalidRequest('This job already has an artifact. Start a new export to preserve it.')
        self._stream = self.part.open('xb')
        os.chmod(self.part, 0o600)
        frontmatter = {
            'title': self.title, 'aliases': [self.title], 'type': f'telegram-{self.export_type}',
            'schema_version': 1, 'export_type': self.export_type, 'exported_at': utc_now(),
            'timezone': 'UTC', 'completeness': 'partial', **self.metadata,
        }
        # JSON scalar/list/map values are valid YAML; remote strings never become YAML syntax.
        encoded = '\n'.join(f'{key}: {json.dumps(value, ensure_ascii=False)}'
                            for key, value in frontmatter.items())
        self.write_text(f'---\n{encoded}\n---\n\n')
        return self

    def write_text(self, value: str) -> None:
        self.write_bytes(value.encode('utf-8'))

    def write_bytes(self, value: bytes) -> None:
        if self._stream is None or self._complete:
            raise RuntimeError('Artifact is not open for writing.')
        self._stream.write(value)
        self._digest.update(value)

    def complete(self, records: int, completeness: str = 'partial',
                 warnings: list[str] | None = None) -> dict[str, Any]:
        from tg_observatory.exporters.markdown import escape
        warnings = warnings or []
        self.write_text('\n## Export result\n\n'
                        f'- Records: {records}\n- Completeness: {escape(completeness)}\n\n## Warnings\n\n')
        self.write_text('\n'.join(f'- {escape(warning)}' for warning in warnings) if warnings else 'None.')
        self.write_text('\n')
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.close()
        os.link(self.part, self.path)
        self.part.unlink()
        self._complete = True
        size = self.path.stat().st_size
        digest = self._digest.hexdigest()
        if self.exports:
            self.exports.record_file(job_id=self.directory.name,
                                     relative_path=self.path.relative_to(self.root).as_posix(),
                                     size_bytes=size, sha256=digest)
        return {
            'format': 'md', 'export_type': self.export_type, 'records': records,
            'completeness': completeness, 'warnings': warnings, 'file_path': str(self.path),
            'size_bytes': size, 'sha256': digest,
        }

    def __exit__(self, *error: Any) -> None:
        if self._stream is not None and not self._stream.closed:
            self._stream.close()
