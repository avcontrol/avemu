#!/usr/bin/env python3
"""TCP-based test emulator for A/V equipment RS232 control.

Uses pyavcontrol protocol definitions to emulate device responses
without requiring physical hardware.
"""

import argparse
import logging
import os
import re
import socket
import sys
import termios
import threading
import tty
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from difflib import get_close_matches
from threading import RLock

import coloredlogs

from pyavcontrol import EmulatorClient, ProtocolLibrary
from pyavcontrol.schema import ProtocolDefinition

LOG = logging.getLogger(__name__)

DEFAULT_PORT = 4999

# ============================================================================
# ERROR DETECTION
# ============================================================================

ERROR_PATTERNS = [
    r'^ERROR',
    r'^!E\(',
    r'ERR',
    r'INVALID',
    r'UNKNOWN',
    r'^NAK',
]


def is_error_response(response: str) -> bool:
    """Check if response indicates an error."""
    if not response:
        return False
    response_upper = response.upper()
    return any(re.search(pattern, response_upper) for pattern in ERROR_PATTERNS)


# ============================================================================
# COMMAND LOG ENTRY
# ============================================================================

@dataclass
class CommandLogEntry:
    """A logged command with metadata."""
    timestamp: datetime
    client_id: str
    command: str
    response: str
    is_error: bool


# ============================================================================
# THREAD-SAFE STATE
# ============================================================================

_state_lock = RLock()
_emulator_lock = RLock()
_clients: list['Server'] = []
_command_log: deque[CommandLogEntry] = deque(maxlen=100)
_stats = {'commands': 0, 'connections': 0, 'errors': 0}

# ============================================================================
# TUI STATE
# ============================================================================

@dataclass
class TUIState:
    """State for TUI panels and navigation."""
    info_panel_visible: bool = False
    detail_popup_visible: bool = False
    search_active: bool = False
    search_query: str = ''
    scroll_offset: int = 0
    selected_log_idx: int = -1  # -1 = no selection, 0+ = selected row from bottom
    selected_cmd_idx: int = 0


_tui_state = TUIState()


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def format_data_into_columns(data: list[str]) -> str:
    """Format data into terminal-width columns for display."""
    if not data:
        return ''

    try:
        terminal_width = os.get_terminal_size()[0]
    except OSError:
        terminal_width = 80

    entries_per_row = max(1, terminal_width // 30)
    lines = []
    row = []

    for entry in data:
        row.append(f'{entry:<30}')
        if len(row) >= entries_per_row:
            lines.append(''.join(row))
            row = []

    if row:
        lines.append(''.join(row))

    return '\n'.join(lines)


def normalize_protocol_id(model_arg: str) -> str:
    """Convert underscore format to slash format for ProtocolLibrary."""
    return model_arg.replace('_', '/')


def host_ip4_addresses() -> list[str]:
    """Get list of non-localhost IPv4 addresses for this host."""
    ip_list = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None):
            if info[0] is socket.AF_INET and info[1] is socket.SOCK_STREAM:
                ip = info[4][0]
                if ip != '127.0.0.1':
                    ip_list.append(ip)
    except socket.gaierror:
        pass
    return ip_list


# ============================================================================
# PROTOCOL INFO EXTRACTION
# ============================================================================

def extract_command_info(protocol: ProtocolDefinition) -> list[dict]:
    """Extract command information from protocol definition."""
    commands = []

    if not protocol.commands:
        return commands

    for name, cmd_def in protocol.commands.items():
        cmd_info = {
            'name': name,
            'description': '',
            'category': '',
            'command_syntax': '',
            'args': {},
            'state_changes': {},
            'response_pattern': '',
            'response_template': '',
        }

        # basic info
        if hasattr(cmd_def, 'description'):
            cmd_info['description'] = cmd_def.description or ''

        # command syntax (the actual string to send)
        if hasattr(cmd_def, 'command'):
            cmd_info['command_syntax'] = cmd_def.command or ''

        # arguments with types/ranges
        if hasattr(cmd_def, 'args') and cmd_def.args:
            for arg_name, arg_def in cmd_def.args.items():
                arg_info = {'type': 'unknown'}
                if hasattr(arg_def, 'type'):
                    arg_info['type'] = arg_def.type or 'unknown'
                if hasattr(arg_def, 'min'):
                    arg_info['min'] = arg_def.min
                if hasattr(arg_def, 'max'):
                    arg_info['max'] = arg_def.max
                if hasattr(arg_def, 'default'):
                    arg_info['default'] = arg_def.default
                cmd_info['args'][arg_name] = arg_info

        # state changes this command makes
        if hasattr(cmd_def, 'state_change') and cmd_def.state_change:
            cmd_info['state_changes'] = dict(cmd_def.state_change)
        elif hasattr(cmd_def, 'updates') and cmd_def.updates:
            cmd_info['state_changes'] = dict(cmd_def.updates)

        # response info
        if hasattr(cmd_def, 'response_pattern'):
            cmd_info['response_pattern'] = cmd_def.response_pattern or ''
        if hasattr(cmd_def, 'response') and cmd_def.response:
            if hasattr(cmd_def.response, 'template'):
                cmd_info['response_template'] = cmd_def.response.template or ''
            if hasattr(cmd_def.response, 'pattern') and not cmd_info['response_pattern']:
                cmd_info['response_pattern'] = cmd_def.response.pattern or ''

        commands.append(cmd_info)

    # sort by name for consistent display
    commands.sort(key=lambda x: x['name'])
    return commands


def get_all_command_syntaxes(protocol: ProtocolDefinition) -> list[str]:
    """Get all valid command syntaxes from protocol for suggestions."""
    syntaxes = []
    commands = extract_command_info(protocol)
    for cmd in commands:
        syntaxes.append(cmd['name'])
        if cmd.get('command_syntax'):
            syntaxes.append(cmd['command_syntax'])
    return syntaxes


def find_similar_commands(cmd: str, protocol: ProtocolDefinition) -> list[str]:
    """Find similar valid commands for suggestions."""
    all_cmds = get_all_command_syntaxes(protocol)
    # extract base command name (before any parameters)
    base_cmd = re.sub(r'[(\d]+.*', '', cmd).strip()
    matches = get_close_matches(base_cmd, all_cmds, n=3, cutoff=0.4)
    return matches


# ============================================================================
# SERVER CLASS
# ============================================================================

class Server(threading.Thread):
    """Handle a single client connection to the emulator."""

    def __init__(
        self,
        sock: socket.socket,
        address: tuple[str, int],
        emulator: EmulatorClient,
        protocol: ProtocolDefinition,
    ) -> None:
        threading.Thread.__init__(self, daemon=True)
        self._socket = sock
        self._address = address
        self._emulator = emulator
        self._protocol = protocol
        self._client_id = f'{address[0]}:{address[1]}'
        self._register_client()

    def _register_client(self) -> None:
        with _state_lock:
            LOG.info('client connected: addr=%s', self._client_id)
            _clients.append(self)
            _stats['connections'] += 1

    def _deregister_client(self) -> None:
        with _state_lock:
            LOG.info('client disconnected: addr=%s', self._client_id)
            if self in _clients:
                _clients.remove(self)

    def _log_command(self, command: str, response: str) -> None:
        with _state_lock:
            is_err = is_error_response(response)
            entry = CommandLogEntry(
                timestamp=datetime.now(),
                client_id=self._client_id,
                command=command,
                response=response,
                is_error=is_err,
            )
            _command_log.append(entry)
            _stats['commands'] += 1
            if is_err:
                _stats['errors'] += 1

    def run(self) -> None:
        try:
            self._socket.settimeout(300.0)

            while True:
                data = self._socket.recv(1024)
                if not data:
                    break

                cmd_str = data.decode('ascii', errors='replace').strip()
                LOG.debug('received: client=%s, cmd=%s', self._client_id, repr(cmd_str))

                with _emulator_lock:
                    response = self._emulator.process_command(data)

                response_str = response.decode('ascii', errors='replace').strip() if response else ''
                self._log_command(cmd_str, response_str)

                if response:
                    LOG.debug('sending: client=%s, response=%s', self._client_id, repr(response_str))
                    self._socket.send(response)

        except socket.timeout:
            LOG.debug('client timeout: addr=%s', self._client_id)
        except ConnectionResetError:
            LOG.debug('client reset: addr=%s', self._client_id)
        except BrokenPipeError:
            LOG.debug('client pipe broken: addr=%s', self._client_id)
        except Exception as e:
            LOG.error('connection error: addr=%s, err=%s', self._client_id, e)
        finally:
            try:
                self._socket.close()
            except Exception:
                pass
            self._deregister_client()


def get_default_port(protocol: ProtocolDefinition) -> int | None:
    """Extract default IP port from protocol connection settings."""
    if protocol.connection and protocol.connection.ip:
        return protocol.connection.ip.port
    return None


def list_supported_protocols(library: ProtocolLibrary) -> None:
    """Display all supported protocols in formatted columns."""
    protocols = library.list_protocols()
    display_list = set()
    for p in protocols:
        display_list.add(p)
        display_list.add(p.replace('/', '_'))

    print('\nModels supported by avemu:\n')
    print(format_data_into_columns(sorted(display_list)))
    print('\nUse either format: mcintosh/mx160 or mcintosh_mx160\n')


# ============================================================================
# KEYBOARD INPUT
# ============================================================================

def get_key_nonblocking(fd: int, timeout: float = 0.05) -> str | None:
    """Non-blocking key read from terminal."""
    import select
    readable, _, _ = select.select([fd], [], [], timeout)
    if readable:
        ch = os.read(fd, 3).decode('utf-8', errors='ignore')
        return ch
    return None


def handle_key(key: str | None, protocol: ProtocolDefinition) -> bool:
    """Handle keyboard input. Returns True if should quit."""
    global _tui_state

    if not key:
        return False

    # ctrl+c
    if key == '\x03':
        return True

    # escape
    if key == '\x1b' or key.startswith('\x1b'):
        # check for arrow keys
        if key == '\x1b[A':  # up arrow
            handle_navigation('up')
            return False
        elif key == '\x1b[B':  # down arrow
            handle_navigation('down')
            return False
        elif key == '\x1b':  # just escape
            if _tui_state.detail_popup_visible:
                _tui_state.detail_popup_visible = False
            elif _tui_state.search_active:
                _tui_state.search_active = False
                _tui_state.search_query = ''
            elif _tui_state.info_panel_visible:
                _tui_state.info_panel_visible = False
            else:
                _tui_state.selected_log_idx = -1
            return False

    # info panel toggle
    if key == 'i':
        if _tui_state.detail_popup_visible:
            _tui_state.detail_popup_visible = False
        else:
            _tui_state.info_panel_visible = not _tui_state.info_panel_visible
            _tui_state.scroll_offset = 0
            _tui_state.selected_cmd_idx = 0
        return False

    # search in info panel
    if key == '/' and _tui_state.info_panel_visible:
        _tui_state.search_active = True
        _tui_state.search_query = ''
        return False

    # navigation keys
    if key in ('j', 'k'):
        handle_navigation('down' if key == 'j' else 'up')
        return False

    # enter - show detail
    if key == '\r' or key == '\n':
        if _tui_state.info_panel_visible:
            _tui_state.detail_popup_visible = True
        elif _tui_state.selected_log_idx >= 0:
            _tui_state.detail_popup_visible = True
        return False

    # search input (when search is active)
    if _tui_state.search_active:
        if key == '\x7f':  # backspace
            _tui_state.search_query = _tui_state.search_query[:-1]
        elif len(key) == 1 and key.isprintable():
            _tui_state.search_query += key
        return False

    return False


def handle_navigation(direction: str) -> None:
    """Handle up/down navigation."""
    global _tui_state

    if _tui_state.info_panel_visible:
        if direction == 'up':
            _tui_state.selected_cmd_idx = max(0, _tui_state.selected_cmd_idx - 1)
            if _tui_state.selected_cmd_idx < _tui_state.scroll_offset:
                _tui_state.scroll_offset = _tui_state.selected_cmd_idx
        else:
            _tui_state.selected_cmd_idx += 1
            # scroll if needed (handled in render)
    else:
        # navigate command log
        with _state_lock:
            max_idx = len(_command_log) - 1

        if direction == 'up':
            if _tui_state.selected_log_idx < max_idx:
                _tui_state.selected_log_idx += 1
        else:
            _tui_state.selected_log_idx = max(-1, _tui_state.selected_log_idx - 1)


# ============================================================================
# TUI RENDERING
# ============================================================================

def run_with_rich_tui(
    server_socket: socket.socket,
    emulator: EmulatorClient,
    protocol: ProtocolDefinition,
    port: int,
) -> None:
    """Run server with beautiful Rich TUI."""
    from rich.console import Console, Group
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    import select

    console = Console()
    server_socket.setblocking(False)

    # extract protocol commands once
    protocol_commands = extract_command_info(protocol)

    def make_layout() -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name='header', size=3),
            Layout(name='main', ratio=1),
            Layout(name='footer', size=3),
        )
        layout['main'].split_row(
            Layout(name='left', ratio=1),
            Layout(name='right', ratio=2),
        )
        layout['left'].split_column(
            Layout(name='clients', ratio=1),
            Layout(name='state', ratio=2),
        )
        return layout

    def render_header() -> Panel:
        device_name = 'Unknown'
        if protocol.device:
            device_name = f'{protocol.device.manufacturer} {protocol.device.model}'

        ips = host_ip4_addresses()
        ip_str = f' ({", ".join(ips)})' if ips else ''

        header_text = Text()
        header_text.append('AVEmu', style='bold magenta')
        header_text.append(f' - {device_name}', style='cyan')
        header_text.append('  •  ', style='dim')
        header_text.append(f'port {port}', style='green')
        header_text.append(ip_str, style='dim')
        header_text.append('  ', style='dim')
        header_text.append('[i]', style='bold yellow')
        header_text.append(' Info', style='dim')

        return Panel(header_text, style='blue')

    def render_clients() -> Panel:
        with _state_lock:
            client_list = [c._client_id for c in _clients[:10]]
            count = len(_clients)

        if client_list:
            client_text = Text()
            for client in client_list:
                ip, cport = client.rsplit(':', 1)
                client_text.append('● ', style='green')
                client_text.append(f'{cport}', style='bold white')
                client_text.append(f' ({ip})\n', style='dim')
            if count > 10:
                client_text.append(f'... and {count - 10} more', style='dim')
        else:
            client_text = Text('No clients connected', style='dim italic')

        return Panel(
            client_text,
            title=f'[bold]Clients[/bold] ({count})',
            border_style='blue',
        )

    def render_state() -> Panel:
        try:
            state_dict = emulator.state.to_dict() if hasattr(emulator.state, 'to_dict') else {}
        except Exception:
            state_dict = {}

        if state_dict:
            table = Table(show_header=False, box=None, padding=(0, 1))
            table.add_column('Key', style='cyan')
            table.add_column('Value', style='white')
            for key, value in list(state_dict.items())[:12]:
                value_style = 'green' if value else 'red' if value is False else 'white'
                table.add_row(key, str(value), style=value_style)
            content = table
        else:
            content = Text('No state tracked', style='dim italic')

        return Panel(content, title='[bold]Device State[/bold]', border_style='green')

    def render_commands() -> Panel:
        with _state_lock:
            recent = list(_command_log)[-15:]

        table = Table(show_header=True, box=None, padding=(0, 1))
        table.add_column('Time', style='dim', width=10)
        table.add_column('Client', style='cyan', width=8)
        table.add_column('Command', style='yellow', width=25)
        table.add_column('→', style='dim', width=1)
        table.add_column('Response', width=25)

        if recent:
            for idx, entry in enumerate(recent):
                time_str = entry.timestamp.strftime('%H:%M:%S')
                cmd_display = entry.command[:23] + '..' if len(entry.command) > 25 else entry.command
                resp_display = entry.response[:23] + '..' if len(entry.response) > 25 else entry.response
                client_port = entry.client_id.rsplit(':', 1)[1]

                # calculate selection index (from bottom)
                from_bottom = len(recent) - 1 - idx
                is_selected = from_bottom == _tui_state.selected_log_idx

                # styling
                if entry.is_error:
                    resp_text = Text(resp_display, style='bold red')
                    row_style = 'on dark_red' if is_selected else None
                else:
                    resp_text = Text(resp_display, style='green')
                    row_style = 'on blue' if is_selected else None

                # selection indicator
                if is_selected:
                    time_str = '▶ ' + time_str[2:]

                table.add_row(time_str, client_port, cmd_display, '→', resp_text, style=row_style)
        else:
            table.add_row('', '', Text('Waiting for commands...', style='dim italic'), '', '')

        nav_hint = ' [↑↓] Navigate  [Enter] Details' if _tui_state.selected_log_idx >= 0 else ' [↑↓] Select'
        return Panel(table, title=f'[bold]Command Log[/bold]{nav_hint}', border_style='yellow')

    def render_footer() -> Panel:
        with _state_lock:
            cmd_count = _stats['commands']
            conn_count = _stats['connections']
            error_count = _stats['errors']

        footer = Text()
        footer.append('Commands: ', style='dim')
        footer.append(f'{cmd_count}', style='cyan bold')
        footer.append('  •  ', style='dim')
        footer.append('Errors: ', style='dim')
        footer.append(f'{error_count}', style='red bold' if error_count > 0 else 'dim')
        footer.append('  •  ', style='dim')
        footer.append('Connections: ', style='dim')
        footer.append(f'{conn_count}', style='cyan bold')
        footer.append('  •  ', style='dim')
        footer.append('[i]', style='bold yellow')
        footer.append(' Protocol  ', style='dim')
        footer.append('[Ctrl+C]', style='bold red')
        footer.append(' Quit', style='dim')
        footer.append('  •  ', style='dim')
        footer.append('© 2026 Ryan Snodgrass', style='dim')

        return Panel(footer, style='dim')

    def render_info_panel() -> Panel:
        """Render the protocol information panel."""
        content_parts = []

        # header
        device_name = 'Unknown'
        if protocol.device:
            device_name = f'{protocol.device.manufacturer} {protocol.device.model}'

        header = Text()
        header.append(f'Protocol: {device_name}', style='bold cyan')
        header.append('                                        ', style='dim')
        header.append('[i/ESC]', style='bold yellow')
        header.append(' Close', style='dim')
        content_parts.append(header)
        content_parts.append(Text(''))

        # search bar
        search_bar = Text()
        if _tui_state.search_active:
            search_bar.append('Search: [', style='dim')
            search_bar.append(_tui_state.search_query or '_', style='bold white on blue')
            search_bar.append(']', style='dim')
        else:
            search_bar.append('[/]', style='bold yellow')
            search_bar.append(' Search', style='dim')
        search_bar.append('    ', style='dim')
        search_bar.append('[j/k ↑↓]', style='bold yellow')
        search_bar.append(' Navigate', style='dim')
        search_bar.append('    ', style='dim')
        search_bar.append('[Enter]', style='bold yellow')
        search_bar.append(' Details', style='dim')
        content_parts.append(search_bar)
        content_parts.append(Text('─' * 70, style='dim'))

        # filter commands by search
        filtered_cmds = protocol_commands
        if _tui_state.search_query:
            query = _tui_state.search_query.lower()
            filtered_cmds = [
                c for c in protocol_commands
                if query in c['name'].lower() or query in c.get('description', '').lower()
            ]

        # render commands
        visible_count = 12
        start_idx = _tui_state.scroll_offset

        # ensure selection is visible
        if _tui_state.selected_cmd_idx >= start_idx + visible_count:
            _tui_state.scroll_offset = _tui_state.selected_cmd_idx - visible_count + 1
            start_idx = _tui_state.scroll_offset

        # clamp selected index
        if filtered_cmds:
            _tui_state.selected_cmd_idx = min(_tui_state.selected_cmd_idx, len(filtered_cmds) - 1)

        for idx, cmd in enumerate(filtered_cmds[start_idx:start_idx + visible_count]):
            actual_idx = start_idx + idx
            is_selected = actual_idx == _tui_state.selected_cmd_idx

            cmd_text = Text()

            # selection indicator
            if is_selected:
                cmd_text.append('▶ ', style='bold yellow')
            else:
                cmd_text.append('  ', style='dim')

            # command syntax (the actual command to send) - primary info
            syntax = cmd.get('command_syntax', '')
            cmd_text.append(f'{syntax:<20}', style='yellow bold' if is_selected else 'yellow')

            # description
            desc = cmd.get('description', '')[:45]
            cmd_text.append(f' {desc}', style='white' if is_selected else 'dim')

            content_parts.append(cmd_text)

            # show details if selected
            if is_selected:
                # response pattern
                if cmd.get('response_template') or cmd.get('response_pattern'):
                    resp_text = Text()
                    resp_text.append('      Response: ', style='dim')
                    resp_text.append(cmd.get('response_template') or cmd.get('response_pattern', ''), style='green')
                    content_parts.append(resp_text)

                # state changes - formatted nicely
                if cmd.get('state_changes'):
                    for key, value in cmd['state_changes'].items():
                        state_text = Text()
                        state_text.append('      Sets: ', style='dim')
                        state_text.append(f'{key}', style='cyan')
                        state_text.append(' = ', style='dim')
                        state_text.append(str(value), style='magenta')
                        content_parts.append(state_text)

        # scroll indicator
        if len(filtered_cmds) > visible_count:
            scroll_text = Text()
            scroll_text.append(f'\n  Showing {start_idx + 1}-{min(start_idx + visible_count, len(filtered_cmds))} of {len(filtered_cmds)}', style='dim')
            content_parts.append(scroll_text)

        return Panel(
            Group(*content_parts),
            title='[bold magenta]Protocol Information[/bold magenta]',
            border_style='magenta',
        )

    def wrap_long_text(label: str, value: str, style: str, max_width: int = 60) -> list[Text]:
        """Wrap long text with proper indentation for continuation lines."""
        lines = []
        label_width = len(label)
        indent = ' ' * label_width

        if len(value) <= max_width:
            t = Text()
            t.append(label, style='dim')
            t.append(value, style=style)
            lines.append(t)
        else:
            # first line with label
            first_chunk = value[:max_width]
            t = Text()
            t.append(label, style='dim')
            t.append(first_chunk, style=style)
            lines.append(t)

            # continuation lines with indent and visual marker
            remaining = value[max_width:]
            while remaining:
                chunk = remaining[:max_width]
                remaining = remaining[max_width:]
                cont = Text()
                cont.append(indent, style='dim')
                cont.append('↳ ', style='dim')
                cont.append(chunk, style=style)
                lines.append(cont)

        return lines

    def render_detail_popup() -> Panel:
        """Render detail popup for selected command or log entry."""
        content_parts = []

        if _tui_state.info_panel_visible:
            # show command detail
            if protocol_commands and _tui_state.selected_cmd_idx < len(protocol_commands):
                cmd = protocol_commands[_tui_state.selected_cmd_idx]

                header = Text()
                header.append(f'{cmd["name"]}', style='bold cyan')
                header.append(' - Command Details', style='dim')
                content_parts.append(header)
                content_parts.append(Text('─' * 60, style='dim'))

                if cmd.get('description'):
                    content_parts.append(Text(f'Description: {cmd["description"]}', style='white'))
                    content_parts.append(Text(''))

                # command syntax
                content_parts.append(Text('Syntax:', style='bold'))
                if cmd.get('command_syntax'):
                    syntax_text = Text()
                    syntax_text.append('  Send: ', style='dim')
                    syntax_text.append(cmd['command_syntax'], style='yellow bold')
                    content_parts.append(syntax_text)

                # arguments
                if cmd.get('args'):
                    content_parts.append(Text(''))
                    content_parts.append(Text('Arguments:', style='bold'))
                    for arg_name, arg_info in cmd['args'].items():
                        arg_text = Text()
                        arg_text.append(f'  {arg_name}', style='magenta bold')
                        if 'min' in arg_info and 'max' in arg_info:
                            arg_text.append(f'  Range: {arg_info["min"]} - {arg_info["max"]}', style='dim')
                        if arg_info.get('type'):
                            arg_text.append(f'  Type: {arg_info["type"]}', style='dim')
                        if 'default' in arg_info:
                            arg_text.append(f'  Default: {arg_info["default"]}', style='green')
                        content_parts.append(arg_text)

                # state changes
                if cmd.get('state_changes'):
                    content_parts.append(Text(''))
                    content_parts.append(Text('State Changes:', style='bold'))
                    for key, value in cmd['state_changes'].items():
                        state_text = Text()
                        state_text.append(f'  {key}', style='cyan')
                        state_text.append(' → ', style='dim')
                        state_text.append(str(value), style='green' if value else 'red')
                        content_parts.append(state_text)

                # response
                if cmd.get('response_pattern') or cmd.get('response_template'):
                    content_parts.append(Text(''))
                    content_parts.append(Text('Response:', style='bold'))
                    if cmd.get('response_template'):
                        resp_text = Text()
                        resp_text.append('  Template: ', style='dim')
                        resp_text.append(cmd['response_template'], style='green')
                        content_parts.append(resp_text)
                    if cmd.get('response_pattern'):
                        resp_text = Text()
                        resp_text.append('  Pattern: ', style='dim')
                        resp_text.append(cmd['response_pattern'], style='green dim')
                        content_parts.append(resp_text)
        else:
            # show command log detail
            with _state_lock:
                recent = list(_command_log)

            if _tui_state.selected_log_idx >= 0 and _tui_state.selected_log_idx < len(recent):
                entry = recent[-(1 + _tui_state.selected_log_idx)]

                header = Text()
                header.append('Command Detail', style='bold cyan')
                header.append('                    ', style='dim')
                header.append('[ESC]', style='bold yellow')
                header.append(' Close', style='dim')
                content_parts.append(header)
                content_parts.append(Text('─' * 50, style='dim'))

                content_parts.append(Text(f'Time:     {entry.timestamp.strftime("%H:%M:%S")}', style='dim'))

                ip, cport = entry.client_id.rsplit(':', 1)
                content_parts.append(Text(f'Client:   {cport} ({ip})', style='cyan'))

                # wrap long command
                for line in wrap_long_text('Command:  ', entry.command, 'yellow'):
                    content_parts.append(line)

                # wrap long response with appropriate style
                resp_style = 'bold red' if entry.is_error else 'green'
                for line in wrap_long_text('Response: ', entry.response, resp_style):
                    content_parts.append(line)

                content_parts.append(Text(''))
                if entry.is_error:
                    content_parts.append(Text('Status:   ❌ ERROR', style='bold red'))

                    # suggest similar commands
                    similar = find_similar_commands(entry.command, protocol)
                    if similar:
                        content_parts.append(Text(''))
                        content_parts.append(Text('Similar valid commands:', style='bold'))
                        for s in similar[:3]:
                            content_parts.append(Text(f'  • {s}', style='green'))
                else:
                    content_parts.append(Text('Status:   ✓ OK', style='bold green'))

        content_parts.append(Text(''))
        content_parts.append(Text('[ESC] Close', style='dim'))

        return Panel(
            Group(*content_parts),
            title='[bold]Details[/bold]',
            border_style='cyan',
        )

    def generate_display() -> Layout | Panel:
        # detail popup takes precedence
        if _tui_state.detail_popup_visible:
            return render_detail_popup()

        # info panel is full screen
        if _tui_state.info_panel_visible:
            return render_info_panel()

        # normal layout
        layout = make_layout()
        layout['header'].update(render_header())
        layout['clients'].update(render_clients())
        layout['state'].update(render_state())
        layout['right'].update(render_commands())
        layout['footer'].update(render_footer())
        return layout

    # setup terminal for raw input
    stdin_fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(stdin_fd)

    try:
        tty.setcbreak(stdin_fd)

        with Live(generate_display(), console=console, refresh_per_second=4, screen=True) as live:
            while True:
                # handle keyboard input
                key = get_key_nonblocking(stdin_fd, 0.05)
                if handle_key(key, protocol):
                    break

                # handle connections
                try:
                    readable, _, _ = select.select([server_socket], [], [], 0.05)
                    if readable:
                        sock, address = server_socket.accept()
                        Server(sock, address, emulator, protocol).start()
                except BlockingIOError:
                    pass

                live.update(generate_display())

    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, old_settings)

    console.print('\n[yellow]Shutting down...[/yellow]')


def run_demo_traffic(port: int) -> None:
    """Generate demo traffic in a background thread."""
    import time as _time

    def _send_demo_commands():
        _time.sleep(2.0)  # wait for server to start
        demo_commands = [
            '!ON', '!PLAY', '!BADCMD', '!STATE?',
            '!VOL(999)', '!PAUSE', '!STOP', '!OFF'
        ]
        for cmd in demo_commands:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect(('localhost', port))
                sock.send((cmd + '\r\n').encode())
                try:
                    sock.recv(1024)
                except socket.timeout:
                    pass
                _time.sleep(0.8)
                sock.close()
                _time.sleep(0.2)
            except Exception:
                _time.sleep(0.5)

    demo_thread = threading.Thread(target=_send_demo_commands, daemon=True)
    demo_thread.start()


def run_simple(
    server_socket: socket.socket,
    emulator: EmulatorClient,
    protocol: ProtocolDefinition,
) -> None:
    """Run server without TUI."""
    try:
        while True:
            sock, address = server_socket.accept()
            Server(sock, address, emulator, protocol).start()
    except KeyboardInterrupt:
        LOG.info('shutting down')


def main() -> None:
    parser = argparse.ArgumentParser(
        description='avemu - Test server that emulates A/V device protocols'
    )
    parser.add_argument(
        '--port',
        help=f"port to listen on (default: device's default or {DEFAULT_PORT})",
        type=int,
        default=DEFAULT_PORT,
    )
    parser.add_argument(
        '--model',
        help='device model (e.g. mcintosh/mx160 or mcintosh_mx160)',
    )
    parser.add_argument(
        '--supported',
        help='list supported models',
        action='store_true',
    )
    parser.add_argument(
        '--host',
        help='listener host (default=0.0.0.0)',
        default='0.0.0.0',
    )
    parser.add_argument(
        '-d', '--debug',
        action='store_true',
        help='verbose logging',
    )
    parser.add_argument(
        '--tui',
        action='store_true',
        help='enable TUI status display (requires terminal)',
    )
    parser.add_argument(
        '-q', '--quiet',
        action='store_true',
        help='minimal output',
    )
    parser.add_argument(
        '--demo',
        action='store_true',
        help='auto-generate demo traffic (for recordings)',
    )
    args = parser.parse_args()

    # configure logging
    if args.quiet:
        coloredlogs.install(level='WARNING')
    elif args.debug:
        coloredlogs.install(level='DEBUG')
    else:
        coloredlogs.install(level='INFO')

    library = ProtocolLibrary()

    if args.supported:
        list_supported_protocols(library)
        return

    if not args.model:
        parser.error('--model is required unless using --supported')

    protocol_id = normalize_protocol_id(args.model)

    try:
        protocol = library.load(protocol_id)
    except Exception as e:
        LOG.error("failed to load protocol '%s': err=%s", args.model, e)
        print(f"\nError: Could not find model '{args.model}'")
        print('Use --supported to list available models\n')
        sys.exit(1)

    LOG.info(
        'loaded protocol: manufacturer=%s, model=%s',
        protocol.device.manufacturer if protocol.device else 'unknown',
        protocol.device.model if protocol.device else 'unknown',
    )

    emulator = EmulatorClient(protocol)

    port = args.port
    if port == DEFAULT_PORT:
        if device_port := get_default_port(protocol):
            port = device_port
            LOG.info('using device default port: port=%d', port)

    all_ips = ''
    if args.host == '0.0.0.0':
        ips = host_ip4_addresses()
        if ips:
            all_ips = f" (also on {','.join(ips)})"

    LOG.info('emulating %s on socket://%s:%d/%s', args.model, args.host, port, all_ips)

    server_socket = None
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((args.host, port))
        server_socket.listen(5)

        # start demo traffic generator if requested
        if args.demo:
            run_demo_traffic(port)

        if args.tui and not args.quiet and sys.stdout.isatty():
            run_with_rich_tui(server_socket, emulator, protocol, port)
        else:
            run_simple(server_socket, emulator, protocol)

    except OSError as e:
        LOG.error('failed to bind: host=%s, port=%d, err=%s', args.host, port, e)
        sys.exit(1)
    finally:
        if server_socket:
            try:
                server_socket.close()
            except Exception:
                pass


if __name__ == '__main__':
    main()
