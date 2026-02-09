"""Protocol health checks — validates every loadable protocol works with avemu.

Catches systemic issues like:
- Commands parsed as wrong type (Command vs CommandGroup)
- Empty command dictionaries (bad inheritance chain)
- Response generation returning raw regex patterns
- Bool/type encoding producing Python literals in wire responses
- Command matching failures for the protocol's own commands
- Unsupported encodings preventing emulation
"""

from __future__ import annotations

import codecs
import re

import pytest

from pyavcontrol import EmulatorClient, ProtocolLibrary
from pyavcontrol.schema import Command, CommandGroup, ProtocolDefinition


@pytest.fixture(scope='module')
def library() -> ProtocolLibrary:
    return ProtocolLibrary()


def _loadable_protocols(library: ProtocolLibrary) -> list[str]:
    """Return protocol IDs that load without schema errors."""
    loadable = []
    for pid in sorted(library.list_protocols()):
        try:
            library.load(pid)
            loadable.append(pid)
        except Exception:
            pass
    return loadable


def _encoding_supported(encoding: str) -> bool:
    """Check if Python's codec system supports this encoding."""
    try:
        codecs.lookup(encoding)
        return True
    except LookupError:
        return False


def _emulatable_protocols(library: ProtocolLibrary, loadable_ids: list[str]) -> list[str]:
    """Return protocols that have commands and a supported encoding."""
    result = []
    for pid in loadable_ids:
        proto = library.load(pid)
        if not proto.commands:
            continue
        if not _encoding_supported(proto.protocol.encoding):
            continue
        result.append(pid)
    return result


@pytest.fixture(scope='module')
def loadable_ids(library: ProtocolLibrary) -> list[str]:
    return _loadable_protocols(library)


@pytest.fixture(scope='module')
def emulatable_ids(library: ProtocolLibrary, loadable_ids: list[str]) -> list[str]:
    return _emulatable_protocols(library, loadable_ids)


# ---------------------------------------------------------------------------
# Health checks
# ---------------------------------------------------------------------------


class TestProtocolsLoad:
    """Every registered protocol should load without schema errors."""

    def test_all_load(self, library: ProtocolLibrary) -> None:
        failures: list[str] = []
        for pid in sorted(library.list_protocols()):
            try:
                library.load(pid)
            except Exception as exc:
                failures.append(f'{pid}: {type(exc).__name__}: {exc}')
        if failures:
            pytest.fail(
                f'{len(failures)} protocols fail to load:\n'
                + '\n'.join(f'  - {f}' for f in failures)
            )


class TestProtocolCommandsExist:
    """Every protocol should have at least one command defined."""

    def test_has_commands(self, library: ProtocolLibrary, loadable_ids: list[str]) -> None:
        missing: list[str] = []
        for pid in loadable_ids:
            proto = library.load(pid)
            if not proto.commands:
                missing.append(pid)
        if missing:
            pytest.fail(
                f'{len(missing)} protocols have zero commands:\n'
                + '\n'.join(f'  - {p}' for p in missing)
            )


class TestSupportedEncoding:
    """Protocols should use Python-supported encodings for emulation.

    Some protocols use non-standard encodings that Python's codec system
    doesn't recognize:

    - ``binary``: Protocols using raw binary framing (e.g., Arcam MZ8/MZ12,
      Lexicon MC series, Parasound HALO, Primare SP series, Crown HiQnet).
      These send structured byte packets, not text. Emulating them requires
      a byte-level framing engine rather than string encode/decode.

    - ``json``: HTTP/WebSocket-based protocols (e.g., Monoprice Monolith HTP-1)
      that exchange JSON payloads over HTTP rather than serial byte streams.

    These protocols are correctly documented but cannot be emulated by the
    current text-based emulator. A future binary transport layer would be
    needed to support them.
    """

    def test_encodings_supported(self, library: ProtocolLibrary, loadable_ids: list[str]) -> None:
        unsupported: list[str] = []
        for pid in loadable_ids:
            proto = library.load(pid)
            if not _encoding_supported(proto.protocol.encoding):
                unsupported.append(f'{pid}: encoding={proto.protocol.encoding!r}')
        if unsupported:
            pytest.fail(
                f'{len(unsupported)} protocols have unsupported encoding:\n'
                + '\n'.join(f'  - {u}' for u in unsupported)
            )


class TestNoEmptyCommandGroups:
    """Flat commands should parse as Command, not CommandGroup with empty actions.

    If a flat command is parsed as CommandGroup, it means the schema
    validator couldn't validate it as Command (e.g., string response field).
    """

    def test_no_empty_groups(self, library: ProtocolLibrary, loadable_ids: list[str]) -> None:
        broken: list[str] = []
        for pid in loadable_ids:
            proto = library.load(pid)
            for name, cmd in proto.commands.items():
                if isinstance(cmd, CommandGroup) and not cmd.commands:
                    broken.append(f'{pid}: {name}')
        if broken:
            pytest.fail(
                f'{len(broken)} commands parsed as empty CommandGroup:\n'
                + '\n'.join(f'  - {b}' for b in broken)
            )


class TestCommandMatchRoundtrip:
    """Commands built from their own templates should match back.

    For each command, format the template with dummy values and verify
    match_command() can identify it.
    """

    def test_own_commands_match(
        self, library: ProtocolLibrary, emulatable_ids: list[str]
    ) -> None:
        failures: list[str] = []
        for pid in emulatable_ids:
            proto = library.load(pid)
            emu = EmulatorClient(proto)
            cb = emu._command_builder
            eol = proto.protocol.command_eol or '\r'

            for group, action, command in cb._iter_commands():
                # skip commands with params we can't auto-fill
                if command.args:
                    continue
                raw = (command.command + eol).encode(proto.protocol.encoding)
                match = cb.match_command(raw)
                if match is None:
                    failures.append(f'{pid}: {group}.{action} ({command.command!r})')

        if failures:
            pytest.fail(
                f'{len(failures)} commands fail to match their own template:\n'
                + '\n'.join(f'  - {f}' for f in failures)
            )


class TestNoRawRegexInResponses:
    """Response generation should never contain raw regex patterns.

    Sends each parameterless command and checks the response for
    (?P< patterns, which indicate failed template substitution.
    """

    def test_no_regex_in_responses(
        self, library: ProtocolLibrary, emulatable_ids: list[str]
    ) -> None:
        failures: list[str] = []
        errors: list[str] = []
        for pid in emulatable_ids:
            proto = library.load(pid)
            try:
                emu = EmulatorClient(proto)
            except Exception as exc:
                errors.append(f'{pid}: EmulatorClient init: {exc}')
                continue
            cb = emu._command_builder
            eol = proto.protocol.command_eol or '\r'

            for group, action, command in cb._iter_commands():
                if command.args:
                    continue
                raw = (command.command + eol).encode(proto.protocol.encoding)
                try:
                    response = emu.process_command(raw)
                except Exception as exc:
                    errors.append(f'{pid}: {group}.{action}: {type(exc).__name__}: {exc}')
                    continue
                decoded = response.decode(proto.protocol.encoding, errors='replace')
                if '(?P<' in decoded:
                    failures.append(f'{pid}: {group}.{action} -> {decoded.strip()!r}')

        all_issues = failures + [f'ERROR {e}' for e in errors]
        if all_issues:
            pytest.fail(
                f'{len(failures)} responses contain raw regex patterns'
                + (f' ({len(errors)} protocol errors)' if errors else '')
                + ':\n'
                + '\n'.join(f'  - {i}' for i in all_issues)
            )


class TestNoPythonLiteralsInResponses:
    """Response generation should never contain Python bool/None literals.

    Checks for literal 'True', 'False', 'None' in responses which
    indicate missing type encoding.
    """

    _BAD_LITERALS = re.compile(r'\b(True|False|None)\b')

    def test_no_python_literals(
        self, library: ProtocolLibrary, emulatable_ids: list[str]
    ) -> None:
        failures: list[str] = []
        errors: list[str] = []
        for pid in emulatable_ids:
            proto = library.load(pid)
            try:
                emu = EmulatorClient(proto)
            except Exception as exc:
                errors.append(f'{pid}: EmulatorClient init: {exc}')
                continue
            cb = emu._command_builder
            eol = proto.protocol.command_eol or '\r'

            for group, action, command in cb._iter_commands():
                if command.args:
                    continue
                raw = (command.command + eol).encode(proto.protocol.encoding)
                try:
                    response = emu.process_command(raw)
                except Exception as exc:
                    errors.append(f'{pid}: {group}.{action}: {type(exc).__name__}: {exc}')
                    continue
                decoded = response.decode(proto.protocol.encoding, errors='replace')
                match = self._BAD_LITERALS.search(decoded)
                if match:
                    failures.append(
                        f'{pid}: {group}.{action} -> {decoded.strip()!r} '
                        f'(contains {match.group()!r})'
                    )

        all_issues = failures + [f'ERROR {e}' for e in errors]
        if all_issues:
            pytest.fail(
                f'{len(failures)} responses contain Python literals'
                + (f' ({len(errors)} protocol errors)' if errors else '')
                + ':\n'
                + '\n'.join(f'  - {i}' for i in all_issues)
            )
