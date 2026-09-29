# Terminal Smash

An ASCII game inside your terminal. Run, jump and smash a copy of your terminal's text, or play the built-in demo. Your actual shell and running programs are unaffected.

## Install

Requires Linux or WSL, Python 3.10+ with `curses`, and a terminal at least 44×14 characters. Capturing your terminal also requires tmux 3.4+; the demo works without tmux.

Download or clone this repository, then open its directory:

```bash
# Ubuntu / WSL dependencies
sudo apt install python3 tmux

./install.sh
terminal-smash --demo
```

No pip packages are needed. The installer uses `~/.local`, adds a tmux shortcut and backs up your tmux configuration before changing it. If the command is not found, use `~/.local/bin/terminal-smash` or add `~/.local/bin` to your `PATH`.

## Play

```bash
terminal-smash --demo          # Built-in playground
terminal-smash --session       # Open a tmux session
terminal-smash --file log.txt  # Play with a text file
```

Inside tmux, press **Ctrl+b**, then **Shift+s** to play with the visible text. Press **Esc** to return to your shell. With a custom tmux prefix, use it instead of Ctrl+b.

**Gravity is OFF by default:** untouched letters stay in place. Press **G** to change it and restart the scene, or launch with `--gravity on`.

Press **C** for a 30-second challenge with respawning `ERROR` enemies and local high scores. You have **5 HP**; enemy contact costs one. The round ends when time or health runs out. You can also start with `terminal-smash --challenge` inside tmux, or `terminal-smash --demo --challenge`.

## Controls

| Key | Action |
| --- | --- |
| A / D or ← / → | Move |
| Space | Jump twice; release a wall or ceiling |
| W / ↑ | Jump, or climb up a wall |
| S / ↓ | Drop through text, climb down, or release the ceiling |
| J / K | Punch / smash |
| L / X | Dash / downward slam |
| G | Toggle text gravity and restart |
| C / R | Switch challenge mode / restart |
| T / Home | Return to the top |
| ? | Help and pause |
| Esc / Q | Exit |

Walls and the ceiling are grabbed automatically. Climb a side wall, move along the ceiling with A/D, then drop onto an otherwise unreachable platform. Attacks affect a small area; falling text does not trigger extra explosions.

## Update, remove, or check

Run these from the repository directory:

```bash
./install.sh                      # Install the current version again
./install.sh --uninstall          # Keep modified files and config backups
./terminal-smash --doctor         # Check requirements
python3 -m unittest discover -s tests -q
```
