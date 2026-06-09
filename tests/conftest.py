"""Shared test setup."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_browser(monkeypatch):
    """Never let a test actually launch a browser. The device-flow login path
    (`pandm login`) calls webbrowser.open, which on a desktop/WSLg session would
    pop open a real Chrome window mid-test."""
    monkeypatch.setattr("webbrowser.open", lambda *a, **k: False)
