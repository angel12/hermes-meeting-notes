import pytest


@pytest.fixture(autouse=True)
def configured_asr_endpoint(request, monkeypatch):
    """Most tests need a configured custom endpoint; the product ships without one."""
    if 'unconfigured_endpoint' not in request.keywords:
        from meeting_notes import connections
        monkeypatch.setattr(connections, 'CUSTOM_ENDPOINT', 'http://asr.test/v1')


def pytest_configure(config):
    config.addinivalue_line('markers', 'unconfigured_endpoint: use the shipped empty ASR endpoint default')
