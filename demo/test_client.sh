#!/bin/bash
# Test client script for avemu demo
# Sends commands to the emulator and displays responses

HOST="${1:-localhost}"
PORT="${2:-84}"

echo "Connecting to avemu at $HOST:$PORT"
echo ""

send_cmd() {
    local cmd="$1"
    local desc="$2"
    echo ">>> $desc"
    echo "    Sending: $cmd"
    response=$(echo "$cmd" | nc -w 1 "$HOST" "$PORT")
    echo "    Response: $response"
    echo ""
}

send_cmd "!POWER?" "Query power state"
send_cmd "!POWER(1)" "Turn power ON"
send_cmd "!POWER?" "Verify power is ON"
send_cmd "!VOL?" "Query volume"
send_cmd "!VOL(-25)" "Set volume to -25"
send_cmd "!VOL?" "Verify volume change"
send_cmd "!MUTE?" "Query mute state"
send_cmd "!MUTE(1)" "Enable mute"
send_cmd "!MUTE?" "Verify mute is ON"

echo "Test complete!"
