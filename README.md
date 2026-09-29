# Terminal Smash

Ludzik ASCII, który chodzi po tekście terminala i rozbija litery na odłamki. Działa w **Linuxie/WSL**, w zwykłym terminalu tekstowym. Animacja w `curses` odświeża się z docelową szybkością 90 klatek/s; nie jest płynną grafiką wektorową jak animacje przeglądarkowe.

Naciśnięcia klawiszy budzą pętlę gry od razu, a nieruchomy tekst jest buforowany. Ruch jest szybszy, skoki krótsze w czasie, a ataki mają krótsze przerwy. Eksplozje animują odłamki bez przesuwania całego ekranu.

W trybie tmux gra używa kolorowej migawki widocznego panelu. Tekst staje się platformami, a uderzenia wyrzucają znaki w powietrze. Wyjście z gry wraca do prawdziwego terminala; uruchomione programy i historia powłoki działają dalej.

## Szybki start w WSL

Wymagania: Python 3.10+ z `curses` oraz tmux 3.4+. Projekt nie potrzebuje pakietów z pip ani internetu podczas instalacji. Sprawdzono na Pythonie 3.12 i tmux 3.4.

```bash
# Jeśli brakuje tmux (Ubuntu/WSL):
sudo apt install tmux

# Z katalogu tego projektu:
./terminal-smash --demo
./install.sh
~/.local/bin/terminal-smash --session
```

W sesji tmux używaj terminala normalnie. Aby rozbić aktualny tekst, naciśnij kolejno **Ctrl+b**, potem **Shift+s**. Jeżeli masz własny prefiks tmux, użyj go zamiast Ctrl+b. Możesz też wpisać `terminal-smash` w panelu; wtedy sama ta komenda pojawi się w migawce.

Jeśli `~/.local/bin` nie znajduje się w `PATH`, instalator pokaże polecenie dodające go do `~/.bashrc`. Do tego czasu działa pełna ścieżka `~/.local/bin/terminal-smash`.

PowerShell może otworzyć wersję linuksową przez `wsl`; grę i tmux uruchamiaj wewnątrz swojej dystrybucji WSL. To nie jest natywny plugin PowerShella.

## Sterowanie

| Klawisz | Działanie |
| --- | --- |
| A / D lub ← / → | Ruch |
| W, ↑ lub Spacja | Skok |
| J | Uderzenie w stronę ruchu |
| K | Wybuch wokół postaci |
| S lub ↓ | Zejście przez platformę |
| T lub Home | Powrót postaci na górę bez odtwarzania tekstu |
| R | Odtworzenie tekstu i reset |
| ? | Pomoc |
| Esc lub Q | Powrót do terminala |

## Tryby uruchomienia

```bash
terminal-smash               # migawka bieżącego panelu tmux
terminal-smash --demo        # kolorowa plansza pokazowa; tmux nie jest potrzebny
terminal-smash --file log.txt # tekst z pliku UTF-8, do 2 MiB
terminal-smash --session     # utwórz/przyłącz sesję tmux „smash”
terminal-smash --doctor      # diagnostyka środowiska
python3 -m terminal_smash --demo # uruchomienie z katalogu źródeł
```

`--pane %0` i `--client /dev/pts/0` służą do jawnego wskazania panelu i klienta tmux; skrót klawiszowy przekazuje je automatycznie.

## Instalacja i usunięcie

Instalator kopiuje program do `~/.local/share/terminal-smash`, tworzy launcher `~/.local/bin/terminal-smash` oraz plik `~/.config/terminal-smash/tmux.conf` (respektuje `XDG_CONFIG_HOME`). Dopisuje oznaczony blok `source-file` do istniejącej konfiguracji tmux i robi kopię tego pliku przed zmianą. Wybiera kolejno `~/.tmux.conf`, `$XDG_CONFIG_HOME/tmux/tmux.conf` albo `~/.config/tmux/tmux.conf`; jeśli żadnego nie ma, tworzy `~/.tmux.conf`. Jeśli serwer tmux już działa, ładuje skrót do niego od razu. Ponowne uruchomienie instalatora aktualizuje program bez mnożenia bloków konfiguracji.

Skrót zajmuje **S w tabeli prefiksu tmux**. Jeżeli korzystasz już z tego skrótu, zmień klawisz w wygenerowanym pliku konfiguracji. Instalator chroni zmodyfikowany plik przed nadpisaniem przy kolejnej instalacji.

```bash
# Z katalogu źródeł:
./install.sh --uninstall
```

Odinstalowanie usuwa oznaczony blok, launcher i niezmienione pliki programu. Zachowuje własne modyfikacje plików oraz kopie konfiguracji. Pozostałe ustawienia tmux pozostają w pliku. Przy niestandardowej instalacji użyj tych samych opcji `--prefix`, `--config-dir` i `--tmux-config` co podczas instalacji; `--no-reload` pomija przeładowanie działającego serwera.

## Ograniczenia

- To interaktywna kopia widocznego tekstu: niszczenie nie edytuje plików ani nie wykonuje poleceń z ekranu. Migawka trafia chwilowo do prywatnego katalogu tymczasowego i jest usuwana po zamknięciu popupu.
- Przechwytywany jest bieżący ekran jednego panelu, bez całej historii przewijania. Procesy pod animacją mogą nadal wypisywać nowe dane, których migawka już nie aktualizuje.
- Pełnoekranowy popup zasłania bieżący klient tmux. Przy podziale okna plansza bazuje na wybranym panelu.
- Zmiana rozmiaru okna odtwarza migawkę i resetuje zniszczenia.
- Kolory i wygląd znaków zależą od terminala i czcionki; nietypowe symbole, emoji i znaki szerokie mogą zostać uproszczone.
- Demonstracja i tryb pliku wymagają interaktywnego terminala. `--doctor` działa także bez niego.

## Testy

```bash
python3 -m unittest discover -s tests -v
```

Testy integracji instalatora pracują wyłącznie w katalogach tymczasowych i nie modyfikują konfiguracji użytkownika.
