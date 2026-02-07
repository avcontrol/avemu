"""Integration tests for avemu emulator with pyavcontrol."""

from __future__ import annotations

from pyavcontrol import EmulatorClient, ProtocolLibrary
from pyavcontrol.schema import ProtocolDefinition


class TestProtocolLibrary:
    """Test ProtocolLibrary functionality."""

    def test_list_protocols(self, library: ProtocolLibrary) -> None:
        """Test that protocols can be listed."""
        protocols = library.list_protocols()
        assert len(protocols) > 0
        assert all('/' in p for p in protocols)

    def test_load_protocol_slash_format(
        self,
        library: ProtocolLibrary,
        loadable_protocol_id: str,
    ) -> None:
        """Test loading protocol with slash format."""
        protocol = library.load(loadable_protocol_id)
        assert protocol is not None
        assert protocol.device is not None

    def test_protocol_has_device_info(
        self,
        sample_protocol: ProtocolDefinition,
    ) -> None:
        """Test that protocol has device information."""
        assert sample_protocol.device is not None
        assert isinstance(sample_protocol.device.manufacturer, str)
        assert len(sample_protocol.device.manufacturer) > 0
        assert isinstance(sample_protocol.device.model, str)
        assert len(sample_protocol.device.model) > 0


class TestEmulatorClient:
    """Test EmulatorClient functionality."""

    def test_emulator_creation(
        self,
        sample_protocol: ProtocolDefinition,
    ) -> None:
        """Test that emulator can be created."""
        emulator = EmulatorClient(sample_protocol)
        assert emulator is not None
        assert emulator.state is not None

    def test_emulator_has_protocol(
        self,
        sample_emulator: EmulatorClient,
    ) -> None:
        """Test that emulator has protocol reference."""
        assert sample_emulator.protocol is not None

    def test_process_command_returns_bytes(
        self,
        sample_emulator: EmulatorClient,
    ) -> None:
        """Test that process_command returns bytes."""
        # send a generic probe; exact command content doesn't matter
        # since we only check the return type
        response = sample_emulator.process_command(b'\r')
        assert isinstance(response, bytes)


class TestProtocolIdNormalization:
    """Test protocol ID format handling."""

    def test_underscore_to_slash_conversion(self) -> None:
        """Test that underscore format is converted to slash."""
        from avemu import normalize_protocol_id

        assert normalize_protocol_id('mcintosh_mx160') == 'mcintosh/mx160'
        assert normalize_protocol_id('lyngdorf_cd2') == 'lyngdorf/cd2'

    def test_slash_format_unchanged(self) -> None:
        """Test that slash format is unchanged."""
        from avemu import normalize_protocol_id

        assert normalize_protocol_id('mcintosh/mx160') == 'mcintosh/mx160'


class TestConnectionSettings:
    """Test connection settings extraction."""

    def test_get_default_port(
        self,
        sample_protocol: ProtocolDefinition,
    ) -> None:
        """Test extracting default port from protocol."""
        from avemu import get_default_port

        port = get_default_port(sample_protocol)
        if sample_protocol.connection and sample_protocol.connection.ip:
            assert port is not None
            assert isinstance(port, int)


class TestUtilities:
    """Test utility functions."""

    def test_format_data_into_columns(self) -> None:
        """Test column formatting utility."""
        from avemu import format_data_into_columns

        data = ['item1', 'item2', 'item3']
        result = format_data_into_columns(data)
        assert 'item1' in result
        assert 'item2' in result
        assert 'item3' in result

    def test_format_empty_data(self) -> None:
        """Test formatting empty data."""
        from avemu import format_data_into_columns

        result = format_data_into_columns([])
        assert result == ''
