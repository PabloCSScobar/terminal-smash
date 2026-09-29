# Terminal Smash

Ludzik ASCII, który biega po tekście terminala, przebija się przez litery dashem i zrzuca całe fragmenty tekstu na niższe platformy. Działa w **Linuxie/WSL**, w zwykłym terminalu tekstowym. Animacja w `curses` odświeża się z docelową szybkością 90 klatek/s.

Dash zostawia smugę, uderzenie z powietrza wyrzuca odłamki na boki, a podcięte podpory wywołują zawalenia i reakcje łańcuchowe. Postać ma animacje biegu, skoku, ataku i lądowania. Kolejne trafienia budują combo i mnożnik punktów; czerwone słowa `ERROR` ożywają i ścigają gracza. Możesz swobodnie rozbijać planszę albo włączyć 30-sekundową demolkę z lokalnym rekordem.

Naciśnięcia klawiszy budzą pętlę gry od razu. Po trafieniu odświeżane są tylko zmienione wiersze, a całe fragmenty tekstu są rysowane zbiorczo. Spadające fragmenty sprawdzają kolizje po przekroczeniu kolejnego wiersza, a liczba odłamków pozostaje ograniczona. Eksplozje animują odłamki bez przesuwania całego ekranu.

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

Komunikaty gry, pomoc, diagnostyka, błędy oraz instalator są po angielsku. Tekst przechwycony z terminala pozostaje w swoim oryginalnym języku.

## Sterowanie

| Klawisz | Działanie |
| --- | --- |
| A / D lub ← / → | Ruch |
| W / ↑ | Skok z zachowaniem rozpędu; podczas chwytu wspinanie po ścianie |
| Spacja | Skok (także podwójny); puszczenie ściany lub sufitu |
| J | Uderzenie w stronę ruchu |
| K | Wybuch wokół postaci |
| L | Dash w stronę ruchu, także w powietrzu |
| X | Uderzenie z powietrza w dół |
| S / ↓ | Zejście przez platformę, w dół po ścianie lub puszczenie sufitu |
| T lub Home | Powrót postaci na górę bez odtwarzania tekstu |
| R | Odtworzenie tekstu i nowa runda w aktualnym trybie |
| C | Przełączenie swobodnej demolki / rundy 30 s i nowy start |
| G | Spadanie tekstu ON/OFF i rozpoczęcie planszy od nowa |
| ? | Pomoc i pauza, również zatrzymanie zegara rundy |
| Esc lub Q | Powrót do terminala |

Skok podczas biegu zachowuje ruch poziomy aż do lądowania. W powietrzu możesz zmienić kierunek przez A/D lub strzałki; skok z miejsca pozostaje pionowy. Połącz skok, dash i uderzenie w dół, aby dobrać się do podpór pod kilkoma piętrami tekstu.

Combo zwiększa mnożnik do x5 i wzmacnia zasięg ataków. Postać ma **5 punktów życia**, pokazanych wyłącznie dłuższym paskiem w nagłówku, bezpośrednio na lewo od wyniku. Kontakt z przeciwnikiem `ERROR` zabiera jeden punkt, odrzuca postać i przerywa serię. Po trafieniu działa krótka ochrona przed kolejnymi obrażeniami; dash oraz uderzenie w dół pozwalają przebić się przez przeciwnika. Przy zerowym życiu pojawia się **GAME OVER**; **R** odtwarza tekst, przeciwników i pełne życie.

Przy ustawieniu **G falling ON** naruszone fragmenty tracące ostatnią podporę spadają i rozbijają niższe linie. Przy **OFF** nietrafione znaki pozostają w powietrzu i nadal można po nich chodzić. Zmiana klawiszem **G** rozpoczyna planszę i wynik od nowa; wybór pozostaje aktywny po **R**, **C** i zmianie rozmiaru okna. Tekst wiszący od początku migawki pozostaje na miejscu do chwili naruszenia, więc plansza nie rozsypuje się sama po starcie.

Chwyt działa **automatycznie przy krawędziach** i nie wymaga włączania klawiszem. Dojdź do lewego lub prawego brzegu, aby złapać boczną ścianę, i używaj **W/↑** do wspinania, **S/↓** do schodzenia. Bez naciskania klawiszy postać trzyma się ściany. Na górze przechodzi na sufit: **A/D** przesuwa ją nad wybraną platformę, a **Spacja** lub **S/↓** pozwala spaść na tekst. Spacja na ścianie odbija do środka planszy; ruch od ściany także ją puszcza. Dash, slam i trafienie przeciwnika zwalniają chwyt na chwilę. Automatyczny chwyt działa także po **R**, **C**, **G** i zmianie rozmiaru okna. Sam start planszy nie przykleja postaci do sufitu; łapiesz go podczas wspinania lub skoku do górnej krawędzi.

Demo dopasowuje ilość tekstu do rozmiaru okna. Po obu bokach są schodki od podłogi do górnej części planszy, oddalone maksymalnie o trzy wiersze; można wspinać się zwykłymi skokami. Schodki również da się zniszczyć. **T/Home** pozostaje szybkim powrotem na górę.

## Tryby uruchomienia

```bash
terminal-smash               # migawka bieżącego panelu tmux
terminal-smash --demo        # plansza dopasowana do okna, ze schodkami
terminal-smash --demo --gravity off # tekst zostaje w powietrzu
terminal-smash --gravity on  # spadanie naruszonego tekstu w panelu tmux
terminal-smash --demo --challenge # demolka planszy pokazowej na czas
terminal-smash --challenge   # 30-sekundowa runda na migawce panelu tmux
terminal-smash --file log.txt # tekst z pliku UTF-8, do 2 MiB
terminal-smash --session     # utwórz/przyłącz sesję tmux „smash”
terminal-smash --doctor      # diagnostyka środowiska
python3 -m terminal_smash --demo # uruchomienie z katalogu źródeł
```

`--pane %0` i `--client /dev/pts/0` służą do jawnego wskazania panelu i klienta tmux; skrót klawiszowy przekazuje je automatycznie. `--challenge` i `--gravity on|off` można łączyć z `--demo`, `--file` lub przechwytywaniem panelu tmux. Nowe uruchomienie domyślnie włącza spadanie (`on`); opcja `--gravity off` uruchamia grę bez niego.

## Demolka na czas i rekordy

Challenge działa również na tekście własnej sesji tmux: otwórz grę skrótem **Ctrl+b, potem Shift+s** i naciśnij **C**, albo wpisz w panelu `terminal-smash --challenge`. `--session` służy tylko do otwarcia sesji tmux; rundę włączasz już w jej środku.

Challenge regularnie uzupełnia przeciwników, również gdy tekst nie zawiera słowa `ERROR`. Bez naturalnych przeciwników startuje trzech; brakujące miejsca są uzupełniane po jednym co około **2 sekundy**. Wrogowie mają odstęp między sobą, aby ich napisy nie zlewały się w jeden. Liczba jednoczesnych przeciwników jest ograniczona do maksymalnie ośmiu, więc kolejne odrodzenia nie zwiększają obciążenia bez końca. Swobodna demolka ożywia tylko słowa `ERROR` obecne w migawce i nie generuje następnych.

Runda trwa **30 sekund aktywnej gry lub do utraty życia**. Zniszczenie całego tekstu i aktualnych przeciwników nie kończy challenge: następni nadal się pojawiają. Buduj combo i wynik, aż skończy się czas. Po końcu wynik zostaje na ekranie; **R** rozpoczyna kolejną próbę, a **C** przełącza tryb gry. Pomoc pod **?** zatrzymuje czas. Zmiana rozmiaru terminala rozpoczyna nową rundę i odtwarza tekst.

Rekord jest osobny dla tej samej planszy, rozmiaru terminala i ustawienia spadania tekstu. Nowe zasady z życiem i odradzaniem przeciwników mają osobne rekordy względem starszych wersji; liczba odrodzeń podczas rundy nie zmienia jej kategorii. Program zapisuje wyłącznie skrót SHA-256 planszy, wynik i czas aktualizacji w `~/.local/state/terminal-smash/records.json` (respektuje `XDG_STATE_HOME`), bez treści terminala. Zachowuje maksymalnie 256 ostatnio używanych plansz. Kilka jednoczesnych sesji może bezpiecznie aktualizować rekordy. Jeśli zapis jest niedostępny, gra działa dalej i pokazuje informację na ekranie.

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
- Zmiana rozmiaru okna odtwarza migawkę, resetuje zniszczenia i rozpoczyna rundę od nowa.
- Kolory i wygląd znaków zależą od terminala i czcionki; nietypowe symbole, emoji i znaki szerokie mogą zostać uproszczone.
- Demonstracja i tryb pliku wymagają interaktywnego terminala. `--doctor` działa także bez niego.

## Testy

```bash
python3 -m unittest discover -s tests -v
```

Testy instalatora, lokalnych rekordów i sesji terminala pracują w katalogach tymczasowych i nie modyfikują konfiguracji ani rekordów użytkownika.
