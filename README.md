# Terminal Smash

An ASCII game inside your terminal. Run, jump and smash a copy of your terminal's text, or play the built-in demo. Your actual shell and running programs are unaffected.

![Terminal Smash attacking text in a captured tmux session](docs/screenshots/smash-attack.png)

<details>
<summary>More screenshots</summary>

**Free play on a captured terminal session**

![Terminal Smash free play with the health bar and keyboard controls](docs/screenshots/terminal-session.png)

**Challenge mode with ERROR enemies**

![Terminal Smash challenge with three ERROR enemies and a round timer](docs/screenshots/challenge-enemies.png)

</details>

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
terminal-smash --tower         # Climb the retained history of your tmux pane
terminal-smash --demo --tower  # Try a complete tower without tmux
```

Inside tmux, press **Ctrl+b**, then **Shift+s** to play with the visible text. Press **Esc** to return to your shell. With a custom tmux prefix, use it instead of Ctrl+b.

**Gravity is OFF by default:** untouched letters stay in place. Press **G** to change it and restart the scene, or launch with `--gravity on`.

Press **C** for a 30-second challenge with respawning `ERROR` enemies and local high scores. You have **5 HP**; enemy contact costs one. The round ends when time or health runs out. You can also start with `terminal-smash --challenge` inside tmux, or `terminal-smash --demo --challenge`.

### Scrollback Tower

Run `terminal-smash --tower` inside tmux to climb from the newest output to the oldest retained line. The game takes a snapshot of the pane's scrollback before opening; your shell keeps running. History that tmux has already discarded cannot be recovered. Snapshots are limited to 2 MiB, and an oversized history produces an error instead of silently shortening the tower.

Bright, underlined text is solid from above; dim text stays in the background. Cyan bridges fill gaps so sparse output is climbable. Use **A/D** or the arrow keys to steer, **Space/W/↑** to jump twice, and **S/↓** to drop through a platform. The camera follows upward and stays there: falling below the view ends the attempt. Land on the gold **SUMMIT** platform to finish, with your time, climbed rows and jump count shown. There is no automatic scrolling or time limit.

**R** retries from the bottom and **?** pauses the game and timer. Resizing preserves your attempt. Wall grips, attacks, falling text and the top teleport are disabled in Tower. **V** switches between Tower and free play; **C** enters challenge mode. Switching modes starts a new attempt. The V shortcut uses the text already loaded; launch with `--tower` to capture the full tmux history rather than only the visible screen.

Use `terminal-smash --file log.txt --tower` for a saved log, or `terminal-smash --demo --tower` for a finite demo history. Tower and `--challenge` cannot be combined; Tower uses fixed platforms and does not accept `--gravity on`.

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
| V | Switch Tower / free play using the loaded text |
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
