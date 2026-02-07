"""Pytest fixtures for avemu tests."""

from __future__ import annotations

import pytest

from pyavcontrol import EmulatorClient, ProtocolLibrary
from pyavcontrol.schema import ProtocolDefinition


def _find_loadable_protocol(library: ProtocolLibrary) -> str:
    """Find the first protocol that loads successfully.

    Iterates through available protocols and returns the ID of the
    first one that passes schema validation, making tests resilient
    to individual protocol schema changes.
    """
    for protocol_id in sorted(library.list_protocols()):
        try:
            library.load(protocol_id)
            return protocol_id
        except Exception:
            continue
    raise RuntimeError('no loadable protocol found in pyavcontrol library')


@pytest.fixture(scope='session')
def library() -> ProtocolLibrary:
    """Create a ProtocolLibrary instance."""
    return ProtocolLibrary()


@pytest.fixture(scope='session')
def loadable_protocol_id(library: ProtocolLibrary) -> str:
    """Discover a protocol ID that loads without schema errors."""
    return _find_loadable_protocol(library)


@pytest.fixture(scope='session')
def sample_protocol(
    library: ProtocolLibrary,
    loadable_protocol_id: str,
) -> ProtocolDefinition:
    """Load a known-good protocol definition for testing."""
    return library.load(loadable_protocol_id)


@pytest.fixture
def sample_emulator(sample_protocol: ProtocolDefinition) -> EmulatorClient:
    """Create EmulatorClient from the sample protocol."""
    return EmulatorClient(sample_protocol)
