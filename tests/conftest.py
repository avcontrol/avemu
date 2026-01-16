"""Pytest fixtures for avemu tests."""

import pytest

from pyavcontrol import EmulatorClient, ProtocolLibrary


@pytest.fixture
def library() -> ProtocolLibrary:
    """Create a ProtocolLibrary instance."""
    return ProtocolLibrary()


@pytest.fixture
def mcintosh_protocol(library: ProtocolLibrary):
    """Load McIntosh MX160 protocol definition."""
    return library.load('mcintosh/mx160')


@pytest.fixture
def mcintosh_emulator(mcintosh_protocol) -> EmulatorClient:
    """Create EmulatorClient for McIntosh MX160."""
    return EmulatorClient(mcintosh_protocol)
