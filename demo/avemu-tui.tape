# avemu TUI Demo - VHS Tape
# Shows the Rich TUI with client connections and command log
# Run with: cd /path/to/avemu && vhs demo/avemu-tui.tape

Output demo/avemu-demo.gif

Set Shell "bash"
Set FontSize 12
Set Width 1100
Set Height 650
Set Theme "Catppuccin Mocha"
Set Padding 10

# Do ALL setup hidden
Hide
Type "source .venv/bin/activate"
Enter
Sleep 300ms

# Start avemu TUI with built-in demo traffic
Type "python avemu.py --model lyngdorf/cd2 --tui --demo"
Enter

# Wait for TUI to render
Sleep 1500ms
Show

# Watch the TUI - client script starts sending after 2s delay
# Total time: 8 commands * ~1.8s each = ~15s
Sleep 16s

# Hide before exit
Hide
Ctrl+C
Sleep 300ms
