#!/usr/bin/env python3
"""Demo client script that sends commands to avemu with visible connections."""
import socket
import time

# Wait for avemu to start
time.sleep(2)

commands = ['!ON', '!PLAY', '!STATE?', '!TRACK?', '!PAUSE', '!STATE?', '!STOP', '!OFF']

for cmd in commands:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2.0)
        sock.connect(('localhost', 84))
        sock.send((cmd + '\r\n').encode())
        try:
            resp = sock.recv(1024)
        except socket.timeout:
            pass
        # Keep connection open so it's visible in TUI
        time.sleep(1.5)
        sock.close()
        time.sleep(0.3)
    except Exception as e:
        time.sleep(0.5)
