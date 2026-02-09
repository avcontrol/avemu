"""Integration tests for avemu emulator with pyavcontrol."""

from __future__ import annotations

import pytest

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

    def test_response_ends_with_protocol_eol(
        self,
        sample_emulator: EmulatorClient,
        sample_protocol: ProtocolDefinition,
    ) -> None:
        """Test that every response ends with protocol-defined response_eol.

        TCP clients use the EOL delimiter to know when a response is
        complete. Without it, clients buffer forever and timeout.

        Protocols with IGNORED or TIMEOUT framing return empty bytes
        for unrecognized commands, which is correct behavior.
        """
        response_eol = sample_protocol.protocol.response_eol or '\r'
        eol_bytes = response_eol.encode('ascii')

        # any command (valid or not) should produce a response ending with EOL
        # unless the protocol silently ignores invalid commands (empty response)
        response = sample_emulator.process_command(b'\r')
        if len(response) > 0:
            assert response.endswith(eol_bytes), (
                f'response {response!r} does not end with {eol_bytes!r}'
            )

    def test_response_eol_on_valid_command(
        self,
        library: ProtocolLibrary,
    ) -> None:
        """Test that a recognized command response includes response_eol."""
        protocol = library.load('mcintosh/mx122')
        emulator = EmulatorClient(protocol)

        response = emulator.process_command(b'!POFF\r')
        assert response.endswith(b'\r'), (
            f'response {response!r} should end with \\r'
        )
        # response body should be present before the delimiter
        assert len(response) > 1


class TestGroupedCommandResponseSubstitution:
    """Test that grouped commands produce properly substituted responses."""

    @pytest.fixture
    def mx160_emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('mcintosh/mx160'))

    def test_trim_surround_get_substitutes_value(
        self, mx160_emulator: EmulatorClient
    ) -> None:
        """trim_surround.get should return a numeric value, not raw regex."""
        response = mx160_emulator.process_command(b'!TRIMSURRS?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, (
            f'raw regex in response: {decoded!r}'
        )
        # default state is 0 => !TRIMSURRS(0)\r
        assert '!TRIMSURRS(0)' in decoded

    def test_roomperfect_focus_get_substitutes_value(
        self, mx160_emulator: EmulatorClient
    ) -> None:
        """roomperfect_focus.get should return a numeric value, not raw regex."""
        response = mx160_emulator.process_command(b'!RPFOC?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, (
            f'raw regex in response: {decoded!r}'
        )
        # default state is 9 => !RPFOC(9)\r
        assert '!RPFOC(9)' in decoded

    def test_max_volume_get_substitutes_value(
        self, mx160_emulator: EmulatorClient
    ) -> None:
        """max_volume.get should return a numeric value, not raw regex."""
        response = mx160_emulator.process_command(b'!MAXVOL?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, (
            f'raw regex in response: {decoded!r}'
        )
        # default state is 99 => !MAXVOL(99)\r
        assert '!MAXVOL(99)' in decoded

    def test_audio_mode_get_substitutes_mode_field(
        self, mx160_emulator: EmulatorClient
    ) -> None:
        """audio_mode.get should substitute the mode field from state.

        The 'name' field has no state backing so it may remain unresolved,
        but 'mode' should be a concrete value.
        """
        response = mx160_emulator.process_command(b'!AUDMODE?\r')
        decoded = response.decode('ascii')
        # mode capture group should be substituted (default=0)
        assert '(?P<mode>' not in decoded, (
            f'mode field not substituted: {decoded!r}'
        )

    def test_volume_offset_get_substitutes_value(
        self, mx160_emulator: EmulatorClient
    ) -> None:
        """volume_offset.get should return a numeric value, not raw regex."""
        response = mx160_emulator.process_command(b'!SRCOFF?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, (
            f'raw regex in response: {decoded!r}'
        )
        # default state is 0 => !SRCOFF(0)\r
        assert '!SRCOFF(0)' in decoded


class TestNonStandardEolCommandMatching:
    """Test command matching with non-standard EOL characters."""

    @pytest.fixture
    def mrc88_emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('xantech/mrc88'))

    def test_volume_query_with_plus_eol(
        self, mrc88_emulator: EmulatorClient
    ) -> None:
        """MRC88 volume query with + terminator should match."""
        response = mrc88_emulator.process_command(b'?1VO+')
        decoded = response.decode('ascii')
        # should not return ERROR
        assert 'ERROR' not in decoded

    def test_mute_query_with_plus_eol(
        self, mrc88_emulator: EmulatorClient
    ) -> None:
        """MRC88 mute query with + terminator should match."""
        response = mrc88_emulator.process_command(b'?1MU+')
        decoded = response.decode('ascii')
        assert 'ERROR' not in decoded

    def test_bass_query_with_plus_eol(
        self, mrc88_emulator: EmulatorClient
    ) -> None:
        """MRC88 bass query with + terminator should match."""
        response = mrc88_emulator.process_command(b'?1BS+')
        decoded = response.decode('ascii')
        assert 'ERROR' not in decoded

    def test_treble_query_with_plus_eol(
        self, mrc88_emulator: EmulatorClient
    ) -> None:
        """MRC88 treble query with + terminator should match."""
        response = mrc88_emulator.process_command(b'?1TR+')
        decoded = response.decode('ascii')
        assert 'ERROR' not in decoded


class TestAnthemAVM60:
    """Anthem AVM60: semicolon EOL, grouped commands, unsolicited response mapping."""

    @pytest.fixture
    def emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('anthem/avm60'))

    def test_command_matching_with_semicolon_eol(
        self, emulator: EmulatorClient
    ) -> None:
        """Commands terminated with ; should match."""
        response = emulator.process_command(b'Z1POW?;')
        assert response.endswith(b';')
        assert len(response) > 1

    def test_response_ends_with_semicolon(
        self, emulator: EmulatorClient
    ) -> None:
        """All responses should end with ; delimiter."""
        for cmd in [b'Z1POW?;', b'Z1VOL?;', b'Z1MUT?;', b'Z1INP?;']:
            response = emulator.process_command(cmd)
            assert response.endswith(b';'), (
                f'{cmd!r} response {response!r} missing ; terminator'
            )

    def test_power_query_resolves_state(
        self, emulator: EmulatorClient
    ) -> None:
        """Power query should resolve via unsolicited response mapping."""
        response = emulator.process_command(b'Z1POW?;')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, (
            f'raw regex in response: {decoded!r}'
        )
        assert decoded.startswith('Z1POW')

    def test_volume_query_returns_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Volume query should return numeric value, not regex."""
        response = emulator.process_command(b'Z1VOL?;')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded
        assert decoded.startswith('Z1VOL')

    def test_source_query_returns_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Source query should return numeric value."""
        response = emulator.process_command(b'Z1INP?;')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded
        assert decoded.startswith('Z1INP')

    def test_set_command_returns_ok(
        self, emulator: EmulatorClient
    ) -> None:
        """Set commands should be recognized and return OK."""
        response = emulator.process_command(b'Z1POW1;')
        assert b'ERROR' not in response
        assert response.endswith(b';')

    def test_invalid_command_returns_error(
        self, emulator: EmulatorClient
    ) -> None:
        """Unrecognized commands should return ERROR with ; terminator."""
        response = emulator.process_command(b'XYZGARBAGE;')
        assert b'ERROR' in response
        assert response.endswith(b';')


class TestEpson5050UB:
    """Epson 5050UB: mixed EOL (cmd \\r / resp :), grouped commands."""

    @pytest.fixture
    def emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('epson/5050ub'))

    def test_response_ends_with_colon(
        self, emulator: EmulatorClient
    ) -> None:
        """All responses should end with : delimiter."""
        response = emulator.process_command(b'PWR?\r')
        assert response.endswith(b':'), (
            f'response {response!r} missing : terminator'
        )

    def test_power_query_returns_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Power query should return status code, not regex."""
        response = emulator.process_command(b'PWR?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded
        # default power off = 00
        assert ':00:' in decoded

    def test_source_query_returns_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Source query should return source code."""
        response = emulator.process_command(b'SOURCE?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded

    def test_power_on_acknowledged(
        self, emulator: EmulatorClient
    ) -> None:
        """Power on command should be recognized."""
        response = emulator.process_command(b'PWR ON\r')
        assert b'ERROR' not in response
        assert response.endswith(b':')

    def test_power_off_acknowledged(
        self, emulator: EmulatorClient
    ) -> None:
        """Power off command should be recognized."""
        response = emulator.process_command(b'PWR OFF\r')
        assert b'ERROR' not in response
        assert response.endswith(b':')


class TestPioneerVSX934:
    """Pioneer VSX934: SUB char (\\x1a) response EOL, flat + grouped commands."""

    @pytest.fixture
    def emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('pioneer/vsx934'))

    def test_response_ends_with_sub_char(
        self, emulator: EmulatorClient
    ) -> None:
        """All responses should end with \\x1a (SUB) delimiter."""
        response = emulator.process_command(b'?P\r')
        assert response.endswith(b'\x1a'), (
            f'response {response!r} missing \\x1a terminator'
        )

    def test_power_on_command_matches(
        self, emulator: EmulatorClient
    ) -> None:
        """PO (power on) should be recognized."""
        response = emulator.process_command(b'PO\r')
        assert b'ERROR' not in response

    def test_power_off_command_matches(
        self, emulator: EmulatorClient
    ) -> None:
        """PF (power off) should be recognized."""
        response = emulator.process_command(b'PF\r')
        assert b'ERROR' not in response

    def test_power_query_matches(
        self, emulator: EmulatorClient
    ) -> None:
        """?P (power query) should be recognized."""
        response = emulator.process_command(b'?P\r')
        assert b'ERROR' not in response

    def test_volume_query_matches(
        self, emulator: EmulatorClient
    ) -> None:
        """?V (volume query) should be recognized."""
        response = emulator.process_command(b'?V\r')
        assert b'ERROR' not in response

    def test_mute_on_command_matches(
        self, emulator: EmulatorClient
    ) -> None:
        """MO (mute on) should be recognized."""
        response = emulator.process_command(b'MO\r')
        assert b'ERROR' not in response

    def test_invalid_command_ignored(
        self, emulator: EmulatorClient
    ) -> None:
        """Pioneer ignores invalid commands (returns empty bytes)."""
        response = emulator.process_command(b'XYZGARBAGE\r')
        assert response == b''


class TestLyngdorfCD2:
    """Lyngdorf CD2: flat commands with response templates."""

    @pytest.fixture
    def emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('lyngdorf/cd2'))

    def test_flat_command_generates_response(
        self, emulator: EmulatorClient
    ) -> None:
        """Flat commands with templates should generate actual responses."""
        response = emulator.process_command(b'!STATE?\r')
        decoded = response.decode('ascii')
        # should use the template, not return OK
        assert 'OK' not in decoded
        assert '!STATE(' in decoded

    def test_playback_state_query_returns_default(
        self, emulator: EmulatorClient
    ) -> None:
        """Playback state query should return default state value."""
        response = emulator.process_command(b'!STATE?\r')
        decoded = response.decode('ascii')
        assert '!STATE(OFF)' in decoded

    def test_gain_query_returns_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Gain query should return numeric value from state."""
        response = emulator.process_command(b'!GAIN?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded
        # default gain is 0
        assert '!GAIN(0)' in decoded

    def test_playback_mode_query_returns_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Playback mode query should return value from state."""
        response = emulator.process_command(b'!PLAYMODE?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded
        assert '!PLAYMODE(0)' in decoded

    def test_display_mode_query_returns_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Display mode query should return value from state."""
        response = emulator.process_command(b'!DISPMODE?\r')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded
        assert '!DISPMODE(0)' in decoded

    def test_power_on_returns_echo(
        self, emulator: EmulatorClient
    ) -> None:
        """Power on command should return echo response."""
        response = emulator.process_command(b'!ON\r')
        decoded = response.decode('ascii')
        assert '!ON' in decoded

    def test_play_command_returns_echo(
        self, emulator: EmulatorClient
    ) -> None:
        """Play command should return echo response."""
        response = emulator.process_command(b'!PLAY\r')
        decoded = response.decode('ascii')
        assert '!PLAY' in decoded

    def test_response_ends_with_cr(
        self, emulator: EmulatorClient
    ) -> None:
        """All responses should end with \\r."""
        for cmd in [b'!STATE?\r', b'!GAIN?\r', b'!ON\r']:
            response = emulator.process_command(cmd)
            assert response.endswith(b'\r'), (
                f'{cmd!r} response {response!r} missing \\r terminator'
            )


class TestXantechMX88ai:
    """Xantech MX88ai: + EOL, grouped + flat commands, multi-zone."""

    @pytest.fixture
    def emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('xantech/mx88ai'))

    def test_set_command_with_plus_eol(
        self, emulator: EmulatorClient
    ) -> None:
        """Set commands terminated with + should match."""
        response = emulator.process_command(b'!1PR1+')
        assert b'ERROR' not in response
        assert response.endswith(b'+')

    def test_response_ends_with_plus(
        self, emulator: EmulatorClient
    ) -> None:
        """All responses should end with + delimiter."""
        response = emulator.process_command(b'!1PR1+')
        assert response.endswith(b'+'), (
            f'response {response!r} missing + terminator'
        )

    def test_power_set_different_zones(
        self, emulator: EmulatorClient
    ) -> None:
        """Power set should work for multiple zones."""
        for zone in [1, 2, 3]:
            cmd = f'!{zone}PR1+'.encode('ascii')
            response = emulator.process_command(cmd)
            assert b'ERROR' not in response, (
                f'zone {zone} power set failed: {response!r}'
            )

    def test_source_set_with_plus_eol(
        self, emulator: EmulatorClient
    ) -> None:
        """Source selection should match with + EOL."""
        response = emulator.process_command(b'!1SS3+')
        assert b'ERROR' not in response

    def test_volume_set_with_plus_eol(
        self, emulator: EmulatorClient
    ) -> None:
        """Volume set should match with + EOL."""
        response = emulator.process_command(b'!1VO15+')
        assert b'ERROR' not in response

    def test_invalid_command_ignored(
        self, emulator: EmulatorClient
    ) -> None:
        """MX88ai ignores invalid commands (returns empty bytes)."""
        response = emulator.process_command(b'XYZGARBAGE+')
        assert response == b''


class TestOnkyoEmbeddedEol:
    """Test Onkyo ISCP commands with EOL embedded in command templates.

    Onkyo commands like '!1PWRQSTN\\r' include \\r in the template itself,
    which is also the command_eol. The match pattern must strip the
    embedded EOL to match correctly.
    """

    @pytest.fixture
    def emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('onkyo/tx-nr6100'))

    @pytest.fixture
    def protocol(self, library: ProtocolLibrary) -> ProtocolDefinition:
        return library.load('onkyo/tx-nr6100')

    def test_power_query_matches(self, emulator: EmulatorClient) -> None:
        """Power query command with embedded \\r should match."""
        response = emulator.process_command(b'!1PWRQSTN\r')
        decoded = response.decode('ascii', errors='replace')
        assert '(?P<' not in decoded, f'raw regex in response: {decoded!r}'

    def test_volume_query_matches(self, emulator: EmulatorClient) -> None:
        """Volume query command should match despite embedded \\r in template."""
        response = emulator.process_command(b'!1MVLQSTN\r')
        decoded = response.decode('ascii', errors='replace')
        assert '(?P<' not in decoded, f'raw regex in response: {decoded!r}'

    def test_power_on_matches(self, emulator: EmulatorClient) -> None:
        """Power on command should match."""
        response = emulator.process_command(b'!1PWR01\r')
        # should not return error - either OK or empty
        assert b'ERROR' not in response

    def test_mute_query_matches(self, emulator: EmulatorClient) -> None:
        """Mute query with embedded \\r should match."""
        response = emulator.process_command(b'!1AMTQSTN\r')
        decoded = response.decode('ascii', errors='replace')
        assert '(?P<' not in decoded, f'raw regex in response: {decoded!r}'


class TestMRC88ZoneQueryResolution:
    """Test zone-aware response resolution for flat commands.

    MRC88 queries like ?1VO should resolve zone1_volume from state
    and substitute it into the response pattern as 'volume'.
    """

    @pytest.fixture
    def emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('xantech/mrc88'))

    def test_volume_query_resolves_zone_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Volume query for zone 1 should return zone1_volume value."""
        emulator.state.set('zone1_volume', 25)
        response = emulator.process_command(b'?1VO+')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, f'raw regex in response: {decoded!r}'
        assert '25' in decoded

    def test_volume_query_zone2(self, emulator: EmulatorClient) -> None:
        """Volume query for zone 2 should return zone2_volume value."""
        emulator.state.set('zone2_volume', 42)
        response = emulator.process_command(b'?2VO+')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, f'raw regex in response: {decoded!r}'
        assert '42' in decoded

    def test_source_query_resolves_zone_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Source query for zone 1 should return zone1_source value."""
        emulator.state.set('zone1_source', 3)
        response = emulator.process_command(b'?1SS+')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, f'raw regex in response: {decoded!r}'
        assert '3' in decoded

    def test_mute_query_resolves_zone_value(
        self, emulator: EmulatorClient
    ) -> None:
        """Mute query for zone 1 should return zone1_mute value."""
        emulator.state.set('zone1_mute', 1)
        response = emulator.process_command(b'?1MU+')
        decoded = response.decode('ascii')
        assert '(?P<' not in decoded, f'raw regex in response: {decoded!r}'
        assert '1' in decoded


class TestAnthemBoolEncoding:
    """Test that bool state values are encoded as 0/1 for int-typed fields.

    Anthem power state is stored as bool (True/False) but the response
    pattern expects 0/1. The encoder must convert bools to ints.
    """

    @pytest.fixture
    def emulator(self, library: ProtocolLibrary) -> EmulatorClient:
        return EmulatorClient(library.load('anthem/avm60'))

    def test_power_query_returns_0_when_off(
        self, emulator: EmulatorClient
    ) -> None:
        """Power query should return Z1POW0, not Z1POWFalse."""
        # default state is power=false
        response = emulator.process_command(b'Z1POW?;')
        decoded = response.decode('ascii')
        assert 'False' not in decoded, f'bool literal in response: {decoded!r}'
        assert 'Z1POW0' in decoded

    def test_power_query_returns_1_when_on(
        self, emulator: EmulatorClient
    ) -> None:
        """Power query should return Z1POW1, not Z1POWTrue."""
        emulator.state.set('power', True)
        response = emulator.process_command(b'Z1POW?;')
        decoded = response.decode('ascii')
        assert 'True' not in decoded, f'bool literal in response: {decoded!r}'
        assert 'Z1POW1' in decoded

    def test_mute_query_returns_0(self, emulator: EmulatorClient) -> None:
        """Mute query should return Z1MUT0, not Z1MUTFalse."""
        response = emulator.process_command(b'Z1MUT?;')
        decoded = response.decode('ascii')
        assert 'False' not in decoded, f'bool literal in response: {decoded!r}'
        assert 'Z1MUT0' in decoded


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
