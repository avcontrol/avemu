# avemu Demo - VHS Tape
# Demonstrates avemu emulating a Lyngdorf CD-2 player
# Run with: cd /path/to/avemu && vhs demo/avemu.tape

Output demo/avemu-demo.gif

Set Shell "bash"
Set FontSize 13
Set Width 900
Set Height 500
Set Theme "Catppuccin Mocha"
Set Padding 20
Set TypingSpeed 50ms

# Setup environment
Hide
Type "source .venv/bin/activate"
Enter
Sleep 500ms
Show

# Start with title
Type "# avemu - A/V Equipment Protocol Emulator"
Enter
Sleep 1s
Ctrl+L

# Start emulator in background
Type "python avemu.py --model lyngdorf/cd2 &"
Enter
Sleep 2s

# Power commands
Type "# Power Control"
Enter
Sleep 300ms

Type "echo '!ON' | nc -w1 localhost 84"
Enter
Sleep 1s

Type "echo '!PWR' | nc -w1 localhost 84"
Enter
Sleep 1s

Type "echo '!OFF' | nc -w1 localhost 84"
Enter
Sleep 1s

# Playback commands
Type ""
Enter
Type "# Playback Control"
Enter
Sleep 300ms

Type "echo '!PLAY' | nc -w1 localhost 84"
Enter
Sleep 800ms

Type "echo '!STATE?' | nc -w1 localhost 84"
Enter
Sleep 800ms

Type "echo '!STOP' | nc -w1 localhost 84"
Enter
Sleep 800ms

Type "echo '!STATE?' | nc -w1 localhost 84"
Enter
Sleep 1s

# Cleanup
Hide
Type "kill %1 2>/dev/null"
Enter
Sleep 300ms
Show

Type ""
Enter
Type "# Emulator responds with protocol-accurate messages!"
Enter
Sleep 2s
