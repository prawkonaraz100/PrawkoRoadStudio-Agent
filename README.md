# RoadMakerAgent — lokalny interfejs agenta do scen drogowych

## Cel

Wspólna praca człowieka i agenta nad **tymi samymi plikami** RoadMaker.
Agent tworzy i poprawia geometrię przez natywne API silnika C++; człowiek
otwiera .xodr w RoadMaker i edytuje ją myszką. Po zapisaniu przez użytkownika
agent odczytuje **aktualny zapisany plik**, nie rekonstruuje poprzedniej sceny.

Nie ma potrzeby instalowania osobnego zdalnego serwera MCP ani otwierania portu:
ChatGPT ma połączenie Desktop Commander i wywołuje lokalne roadctl.cmd.
Ten program jest nakładką na natywny binding nanobind, a nie alternatywnym
generatorem niestandardowego OpenDRIVE.

## Instalacja (potwierdzona na DESKTOP-G2FV0GJ)

- Aktualny projekt: F:\PrawkoRoadStudio, gałąź feature/realistic-road-surfaces
- Oryginalna kopia referencyjna: F:\PrawkoRoadStudio\reference\RoadMaker, commit bazowy c7a3f5a
- GUI: F:\PrawkoRoadStudio\build\dev-windows\editor\roadmaker-editor.exe
- Python: F:\PrawkoRoadStudio\reference\RoadMaker\toolchain\Python312\python.exe (3.12.10)
- Biblioteka C++/Python: vendor\roadmaker\_roadmaker.cp312-win_amd64.pyd
- Narzędzie: F:\PrawkoRoadStudio\agent\roadctl.cmd
- Projekty: F:\PrawkoRoadStudio\agent\projects\
- Opisy scen: F:\PrawkoRoadStudio\agent\specs\

Plik vendor/roadmaker/_roadmaker.cp312-win_amd64.pyd to kopia natywnego
modułu z nowego forka F:\PrawkoRoadStudio\build\dev-windows\python.
Po każdej kompilacji `scripts/prawko-build-win.ps1 -Target editor`, `python`
lub `all` skrypt automatycznie kopiuje zbudowany moduł do `agent/vendor`
i sprawdza jego SHA-256. `scripts/prawko-verify-win.ps1` odmawia PASS,
jeśli pliki `.pyd` różnią się; dodatkowy test eksportu GLB sprawdza
wyniesione narożniki, fazy i UV. To chroni przed niezauważonym eksportem
płaskich chodników ze starego modułu. Dla projektów nowego edytora używać launchera:

    & F:\PrawkoRoadStudio\scripts\prawko-agent.cmd status

Launcher kieruje polecenia open do nowego PrawkoRoadStudio i wykorzystuje
te same pliki OpenDRIVE/OpenSCENARIO co GUI.

## Polecenia

Wywołania w PowerShell (bez wymagania zmiany PATH):

    & F:\PrawkoRoadStudio\agent\roadctl.cmd status

    & F:\PrawkoRoadStudio\agent\roadctl.cmd build --spec F:\PrawkoRoadStudio\agent\specs\urban_crossing.json --out F:\PrawkoRoadStudio\agent\projects\urban_crossing\scene.xodr

    & F:\PrawkoRoadStudio\agent\roadctl.cmd inspect F:\PrawkoRoadStudio\agent\projects\urban_crossing\scene.xodr

    & F:\PrawkoRoadStudio\agent\roadctl.cmd validate F:\PrawkoRoadStudio\agent\projects\urban_crossing\scene.xodr

    & F:\PrawkoRoadStudio\agent\roadctl.cmd edit F:\PrawkoRoadStudio\agent\projects\urban_crossing\scene.xodr --spec F:\PrawkoRoadStudio\agent\specs\adjust_road.json --out F:\PrawkoRoadStudio\agent\projects\urban_crossing_v2\scene.xodr

    & F:\PrawkoRoadStudio\agent\roadctl.cmd export F:\PrawkoRoadStudio\agent\projects\urban_crossing\scene.xodr --out F:\PrawkoRoadStudio\agent\projects\urban_crossing\scene.glb

    & F:\PrawkoRoadStudio\agent\roadctl.cmd open F:\PrawkoRoadStudio\agent\projects\urban_crossing\scene.xodr

Polecenia zwracają JSON (w tym SHA-256, nazwy i trwałe identyfikatory dróg,
błędy walidacji, PID GUI). Powtarzalność scen wynika z pliku wejściowego
i zapisanych plików OpenDRIVE/OpenSCENARIO.

## Format zlecenia dla agenta (JSON)

Przykład podwójnego pliku scene.xodr + scene.xosc:

    {
      "name": "Przykładowe skrzyżowanie",
      "intersections": [
        {"kind": "x", "pose": [0,0,0], "gap_m": 24,
         "arm_length_m": 80, "profile": "urban"}
      ],
      "features": {
        "crosswalks": true, "lane_arrows": true,
        "control": "two_phase"
      },
      "scenario": {
        "actors": [
          {"name": "Ego", "kind": "car", "road": "1",
           "lane": "-1", "s": 20, "speed_kmh": 40}
        ],
        "stop_after_s": 30
      }
    }

Wspierane warstwy:
- roads: nazwa, profil, lista punktów [x,y]; powstają drogi z krzywymi klotoidowymi;
- intersections: kind x lub t, pose [x,y,kąt w stopniach], arm_length_m, gap_m, profile;
- junctions: jawne ends: lista {"road": "ID", "end": "start|end"};
- features: crosswalks, lane_arrows, center_marks, control;
- objects: obiekty z trwałym id, road/s/t, model, typ, rozmiar;
- signs: sygnały/znaki OpenDRIVE z id, country, type, s/t, value/unit itd.;
- scenario: actors z kind car/truck/bus/motorbike/bicycle/pedestrian,
  zakotwiczeniem w drodze i pasie, prędkością km/h i opcjonalną trasą;
- scenario.maneuvers: lane_change, relative_lane, duration_s,
  trigger {"time_s": 4} lub {"distance_to": "Ego", "distance_m": 12};
- scenario.stop_after_s: czas zakończenia scenariusza w sekundach.

Profile: default, rural, urban, arterial, highway, collector, local, freeway.
Sterowanie na skrzyżowaniu: two_phase, protected_left, all_way_stop, two_way_stop.
W modelu silnika s/t i długości są w metrach, prędkość w m/s (w JSON km/h),
kąt pose w JSON w stopniach, w C++ w radianach.

Edytowanie istniejącej sceny: plik JSON z "operations": tablicą op:
create_road, move_waypoint, translate_road, rename_road, set_node_elevation,
set_lane_width, add_object, add_sign, move_object, set_object_model,
move_signal, set_signal_text, set_signal_value,
set_junction_default_corner_radius, set_corner_radius.
Operacja set_corner_radius wymaga junction, corner_index i radius_m;
indeks narożnika należy odczytać z aktualnej geometrii skrzyżowania.
Po operacjach roadctl waliduje sieć i kopiuje scenariusz .xosc
z uwzględnieniem nowego pliku drogowego.

Dodatkowe gotowe przykłady znajdują się w specs:
urban_crossing.json, t_junction.json, s_curve.json, cut_in.json, adjust_road.json.

## Bezpieczeństwo i współpraca z GUI

1. **Nie edytować niezapisanej sceny w GUI**: człowiek wykonuje Ctrl+S.
   Roadctl umie wykryć część niezapisanych zmian przez gwiazdkę w tytule
   okna; nie jest to jednak transakcyjny protokół blokad.
2. **Edycja do NOWEGO pliku** (nowej wersji) jest domyślną procedurą.
   Istniejący plik bez --force nie jest nadpisywany.
3. --force tworzy kopię *.backup-<UTC> przed podmianą i odmawia
   nadpisania, jeśli nazwa pliku jest otwarta w RoadMaker.
   --expect-sha256 pozwala dodatkowo sprawdzić wersję wyjściową.
4. Zapis OpenDRIVE odbywa się przez plik tymczasowy, walidację i
   os.replace. Gdy .xodr i .xosc są zmieniane razem, operacja NIE jest
   atomową transakcją pomiędzy dwoma plikami; kopie bezpieczeństwa
   chronią wcześniejsze wersje.
5. Tylko katalog projects/ może zawierać wyniki build/edit. Oryginalny
   klon RoadMaker oraz nasz Unreal RoadSceneEditor pozostają oddzielone.
6. Identyfikatory arena RoadId/LaneId w C++ nie są trwałe po ponownym
   otwarciu. Do pracy między sesjami używamy OpenDRIVE @id oraz nazw aktorów
   i pasów (odr_id), które są zapisywane w plikach.
7. Zewnętrzna edycja nie jest dopisywana do aktywnego stosu Ctrl+Z GUI:
   program musi ponownie otworzyć plik. To ograniczenie aktualnej integracji.

## Granice funkcjonalne

RoadMaker jest edytorem i generatorem scen, a nie pełnym symulatorem fizyki ruchu.
Zapis .xosc opisuje manewry; do ich faktycznego wykonania potrzebny jest
kompatybilny symulator (np. esmini), a walidacja pliku nie dowodzi poprawności
zachowania w każdej symulacji.

Obecna nakładka roadctl obsługuje najczęstsze budulce, nie całą publiczną
powierzchnię C++/Python. Zaawansowane zjazdy, wiadukty, drogi podporządkowane
przyłączane do boku oraz tereny wymagają rozszerzenia JSON lub bezpośredniego
skryptu z rm.edit; odpowiednie metody istnieją w jądrze. Biblioteka znaków
w zestawie źródłowym wykorzystuje m.in. katalog USA: polskie znaki i zasady
pierwszeństwa wymagają osobnego sprawdzenia przed wykorzystaniem dydaktycznym.
Brak przetestowanego w tym adapterze kreatora ronda.

## Testy regresyjne

    cd F:\PrawkoRoadStudio\agent
    F:\PrawkoRoadStudio\reference\RoadMaker\toolchain\Python312\python.exe -m unittest -v test_roadctl

Testy tworzą i usuwają własne tymczasowe projekty w projects/.
Nie modyfikują źródeł ani przykładowych plików producenta.
