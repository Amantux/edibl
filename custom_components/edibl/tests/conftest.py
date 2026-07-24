"""Shared fixtures for the Edibl integration tests.

Runs under `pytest-homeassistant-custom-component` (a real Home Assistant test
environment). This is NOT part of the backend pytest suite — it needs Home
Assistant (Python 3.13+) and runs in its own CI job.
"""
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the custom `edibl` component in every test."""
    yield
