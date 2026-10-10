import os
import socket

import pytest


@pytest.fixture(autouse=True)
def deny_external_network(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        if os.getenv("SMARTGATE_LIVE") != "1":
            pytest.skip("Live checks require SMARTGATE_LIVE=1 and a separate empty test profile")
        return

    def blocked(*args, **kwargs):
        raise AssertionError("Offline tests must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(autouse=True)
def isolate_actions_context(monkeypatch):
    # Each test opts into Actions presentation explicitly; runner context is not a fixture.
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
