"""Shared test fixtures."""
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let HA load custom_components/bwwb_water in every test."""
    yield


# aioresponses 0.7.9 predates aiohttp 3.14's required `stream_writer` argument.
try:
    import aioresponses.core as _ar_core
    from aiohttp import ClientResponse as _ClientResponse

    class _StubWriter:
        output_size = 0

    class _CompatClientResponse(_ClientResponse):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("stream_writer", _StubWriter())
            super().__init__(*args, **kwargs)

    _ar_core.ClientResponse = _CompatClientResponse
except ImportError:  # pragma: no cover
    pass
