# Android-only Python wheel

`pyaes-1.6.1-py3-none-any.whl` is the pure-Python `pyaes` 1.6.1 package distributed under the MIT license. It is included because PyPI publishes this release as a source archive, while Chaquopy 17.2 installs wheels only. Telethon requires pyaes. The normal desktop Python dependency remains declared through Telethon in `pyproject.toml`.

Upstream: https://pypi.org/project/pyaes/1.6.1/
