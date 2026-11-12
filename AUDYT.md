# Audyt RoadMaker — architektura, funkcje i integracja agenta

Data: 2026-11-12. Lokalny klon Robomous/RoadMaker:
F:\RoadMaker-evaluation, main c7a3f5a (v0.0.1).

## Architektura

| Warstwa | Kod | Ustalenie |
|---|---|---|
| Jądro C++20 | core/, cmake/deps.cmake | Model dróg, geometria klotoidowa, OpenDRIVE, OpenSCENARIO, siatki, komendy edycji |
| Python | python/src/bindings.cpp, python/src/roadmaker/__init__.py | Natywne API jądra bez potrzeby uruchamiania Qt |
| Qt6 GUI | editor/src/document/document.cpp, editor/src/viewport/ | Edycja myszką, Undo/Redo, kamera, drzewo scen, odczyt i zapis |
| Persistencja | docs/architecture/persistence.md | Dane drogowe w .xodr, scenariusze w .xosc, stan widoku w .rmscene.json, katalog projektu w project.json |

Nie budujemy kruchego bota klikającego po ekranie. Wtyczka agenta używa
oficjalnych rm.edit i rm.osc.edit z tej samej biblioteki C++, którą
wywołuje aplikacja Qt. Zmiany są przekazywane przez zapisane pliki.

## Macierz możliwości

Status TEST = lokalnie wykonana kontrola. Status API = funkcja w kodzie i
przykładach, lecz niezintegrowana/testowana w tej iteracji.

| Funkcja | Status | Uwagi |
|---|---|---|
| Drogi proste i łukowe | TEST | create_road, move_waypoint, s_curve |
| Profile i szerokości pasów | TEST | LaneProfile, set_lane_width |
| Skrzyżowania X i T | TEST | assembly.x_intersection / t_intersection |
| Niestandardowe skrzyżowanie z końców dróg | TEST | create_junction |
| Sygnalizacja i STOP | TEST | signalize_junction |
| Przejścia i strzałki na pasach | TEST | junction_crosswalks, junction_lane_arrows |
| Drzewa i sygnalizatory | TEST | add_object, add_signal |
| Pojazdy oraz ich trasy | TEST | OpenSCENARIO, LanePosition, Route |
| Scenariusz zmiany pasa | TEST | LaneChangeAction z wyzwalaczem czasu lub odległości |
| Zapis, odczyt i walidacja | TEST | .xodr / .xosc / walidacja między plikami |
| Eksport glTF .glb | TEST | plik 83748 B dla sceny miejskiej |
| Uruchomienie GUI z generowaną sceną | TEST | Windows proces i odpowiadające okno |
| Edycja myszką i GUI Save | API | kod Qt obecny; bez ręcznej próby drag-and-drop podczas tego audytu |
| Przyłączanie drogi do jej boku | API | attach_t_junction |
| Mosty i teren | API | przykłady bridge_span / terrain |
| OSM, GIS, lidar | API | obecne moduły i skrypty, nie testowane |
| OpenSCENARIO 2.x | API | częściowy eksport DSL, bez czytnika 2.x |
| USD | API | opcjonalny build; w tej konfiguracji nie zweryfikowano |
| Gotowy kreator ronda w assembly | BRAK | brak fabryki roundabout w API |
| Zdalne wstrzykiwanie komend do aktywnego GUI | BRAK | obecna integracja jest plikowa |

## Trwałe identyfikatory i współpraca

W pamięci C++ RoadId / LaneId są generacyjnymi uchwytami i nie powinny być
przenoszone między uruchomieniami. Do identyfikacji plikowej służą OpenDRIVE
odr_id, identyfikatory znaków i obiektów oraz nazwy aktorów scenariusza.
Scenariusze wykorzystują odwołania do drogi/pasa zamiast pozycji punktu
w przestrzeni, co pomaga zachować znaczenie trasy po edycji geometrii.

W Document::load() program zeruje Undo, odczytuje .xodr, tworzy siatkę,
odczytuje .rmscene.json, następnie paruje sąsiednie .xosc o tej samej
nazwie bazowej. W Document::save() zapisuje oba dokumenty. To potwierdza
możliwość współdzielenia pliku, nie oznacza natomiast automatycznego
odświeżania otwartego dokumentu po zapisie z zewnątrz.

## Co zbudowano

F:\RoadMakerAgent\roadctl.py oraz roadctl.cmd mają polecenia:
status, build, inspect, validate, edit, export i open.

- Programowa budowa sieci drogowych i scenariuszy, kontrola błędów.
- Modyfikacja istniejącego projektu bez generowania sceny od zera.
- Informacja o SHA-256, drogach, pasach, aktorach i sygnalizatorach.
- Ograniczenie zapisu wyników do własnego folderu projects.
- Odmowa domyślnego nadpisania i kopie bezpieczeństwa przy --force.
- Zapis do pliku tymczasowego z walidacją i podmianą.
- Bez serwera MCP, nasłuchujących portów i zmian w źródłach RoadMaker.

## Wyniki

- Natywne Python/C++: build PASS oraz import PASS.
- urban_crossing: 16 dróg (w tym łączniki), 1 skrzyżowanie,
  11 obiektów, 4 sygnały, 2 kontrolery, 2 aktorów, 0 błędów.
- urban_crossing_v2: zmiana nazwy i szerokości pasa, 16 dróg,
  zachowana scena .xosc, 0 błędów.
- t_junction: 9 dróg (z łącznikami), STOP, 0 błędów.
- s_curve: droga łukowa i trasa aktora, 0 błędów.
- cut_in: aktorzy Ego/Target, zdarzenie LaneChangeAction, 0 błędów.
- Eksport .glb: PASS.
- Otwarcie wygenerowanego pliku w Qt GUI na Windows: PASS.
- 5/5 testów regresyjnych PASS.

Walidacja strukturalna ASAM nie jest tym samym co audyt przepisów
polskiego ruchu drogowego, fizyki symulacji ani realności wykonania manewru.
Domyślne zasoby RoadMaker wykorzystują także oznakowanie amerykańskie.
Dla scen edukacyjnych w Polsce potrzebna będzie polska biblioteka znaków
i weryfikacja treści.

## Procedura przyszłych zleceń

1. Agent mapuje wymagania do JSON spec i buduje projekt przez jądro C++.
2. Walidacja .xodr, .xosc, referencji między nimi oraz opcjonalny .glb.
3. RoadMaker GUI otwiera wygenerowany projekt, człowiek może edytować go myszką.
4. Człowiek zapisuje plik. Agent odczytuje najnowszy zapis i identyfikatory.
5. Agent wykonuje nową modyfikację w osobnym wariancie, po czym otwiera wynik.
6. Rozszerzenie adaptera o nowe funkcje dopiero po potwierdzeniu API/testem.

Do bezpośredniego dostępu z ChatGPT wystarcza działający konektor Desktop
Commander, który uruchamia lokalne polecenie roadctl.cmd. Osobny MCP
byłby dodatkowym serwerem z ryzykiem kolizji stanów i nie daje dziś korzyści.
