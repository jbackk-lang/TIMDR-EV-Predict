# TIMDR EV Predict

<!-- TIMDR HUB LINKS -->
> **Ekosystem TIMDR:** [Knowledge Hub — dokumentacja i wyszukiwarka](https://jbackk-lang.github.io/TIMDR-AI-Knowledge-Base/) · [Panel badawczy](https://jbackk-lang.github.io/) · [GIA-TIMDR — źródła](https://github.com/jbackk-lang/GIA-TIMDR) · [Research AI — lokalne wyszukiwanie semantyczne](https://github.com/jbackk-lang/TIMDR-AI-Knowledge-Base). Publiczna wyszukiwarka Huba jest tekstowa; semantyczna wymaga lokalnego uruchomienia.
<!-- /TIMDR HUB LINKS -->

Lokalne narzędzie badawcze do obserwacji **akumulatora trakcyjnego,
silnika elektrycznego i układu ładowania**. Łączy pomiary każdego
podsystemu i pokazuje, gdzie sygnały odbiegają od odniesienia.
Wspólny wskaźnik wskazuje najsłabszy z analizowanych podsystemów.
Pole TTF pokazuje prognozowany czas przekroczenia ustawionego progu
sygnału. Prognoza awarii rzeczywistego pojazdu wymaga osobnej walidacji.

## Co sprawdzono dla pojazdów elektrycznych

| Obszar | Wynik i rodzaj danych |
|---|---|
| Akumulator trakcyjny | **69 użytecznych cykli laboratoryjnych** ogniwa NASA B0047. Pojemność spadła z **1,674 do 1,157 Ah**. Próba wykazała, że odniesienie do mediany całej historii może odwrócić ocenę trendu. W scenariuszu użyto odniesienia z pierwszych pięciu cykli. [Dane i proweniencja](data/real_battery/README.md) · [NASA](https://data.nasa.gov/dataset/li-ion-battery-aging-datasets) |
| Napęd elektryczny | W scenariuszach syntetycznych są zużycie łożysk i zwarcie międzyzwojowe. Sygnały wejściowe obejmują m.in. drgania, temperaturę uzwojeń i asymetrię prądu. Do oceny skuteczności na samochodzie potrzebne są oznaczone pomiary napędu. |
| Ładowanie | Scenariusze syntetyczne pokazują zapad napięcia i przegrzanie złącza. Wyniki z demo opisują zachowanie algorytmu w zadanych warunkach. |
| Elektronika i magistrala CAN | Test mostu potwierdził zapis wybranych sygnałów do CSV po dekodowaniu ramek plikiem DBC na wirtualnej magistrali. To sprawdzenie transmisji i interpretacji ramek. Podłączenie do samochodu wymaga właściwego opisu sygnałów dla danego modelu. [Test mostu CAN](test_ev_can_source.py) |

**Próba prognozy pojemności B0047.** Po pierwszych 55 dostępnych cyklach odłożyliśmy 14 ostatnich. Na tym odcinku utrzymanie ostatniej zmierzonej pojemności dało średni błąd bezwzględny **0,028 Ah**, a ekstrapolacja trendu liniowego **0,150 Ah**. To porównanie dwóch prostych metod na jednym ogniwie. Wynik pokazuje, że prognozę trzeba porównywać z ostatnim pomiarem i sprawdzać na innych ogniwach.

**Elektronika do dalszych prób.** Dla BMS przydadzą się zapis napięć ogniw, prąd i temperatura. Dla falownika warto zestawiać prądy fazowe, temperaturę oraz zadany i zmierzony moment, jeśli pojazd udostępnia te kanały. Dla układu ładowania przydadzą się napięcie, prąd i temperatura złącza. Skuteczność rozróżniania usterek tych podsystemów wymaga porównania z potwierdzoną diagnozą warsztatową.

## Dashboard i REST API

```
run.bat
```

(albo ręcznie: `pip install -r requirements.txt` i `python api.py`) -
otwiera `http://127.0.0.1:5000` w przeglądarce. Wybierz scenariusz dla
każdego z trzech podsystemów (albo "— brak / nie dotyczy —", jeśli dany
podsystem nie ma teraz danych, np. pojazd się nie ładuje) - analiza
uruchamia się automatycznie po każdej zmianie, bez osobnego przycisku.

Dashboard pokazuje wspólny wskaźnik dla dostępnych podsystemów, modelowy
czas przekroczenia progu oraz osobne wykresy E(t), progi i anomalie.

## Scenariusze demo

| Podsystem | Scenariusz | Co wykrywa |
|---|---|---|
| 🔋 Bateria | `battery_degradation` | Starzenie ogniw (syntetyczne) - malejąca pojemność, rosnąca rezystancja/temperatura ładowania (trend) |
| 🔋 Bateria | `battery_real_nasa_b0047` | **REALNE dane** (NASA PCoE, ogniwo B0047) - 69 cykli, realny spadek pojemności 1.674→1.157 Ah (~31%) |
| ⚙️ Silnik | `motor_bearing_wear` | Zużycie łożysk - rosnąca wibracja/temperatura uzwojeń, spadek sprawności (trend) |
| ⚙️ Silnik | `motor_winding_short` | Zwarcie międzyzwojowe - nagły skok asymetrii prądu/momentu (anomalia), potem przegrzanie |
| 🔌 Ładowanie | `charging_grid_voltage_sag` | Zapady napięcia sieci - cykliczne wahania (rytm) + głębokie zapady (anomalia) |
| 🔌 Ładowanie | `charging_connector_overheat` | Przegrzewające się złącze - cykl ładowania (rytm) + jedna przegrzana sesja (anomalia) |

Scenariusz z realnymi danymi baterii ujawnił konkretny, udokumentowany
problem (naiwna fuzja daje na tych danych fizycznie odwrócony wynik) -
patrz `HISTORIA_BLEDOW.md`, sekcja 1, oraz `data/real_battery/README.md`
po pełną proweniencję danych.

## 📡 Podłączenie do realnego pojazdu (OBD-II / CAN)

Dashboard powyżej działa na danych demo. Do monitoringu PRAWDZIWEGO
pojazdu na żywo służą dwa dodatkowe skrypty (nie wymagane do samego
dashboardu, więc NIE są w `requirements.txt` - doinstaluj osobno):

```
pip install obd python-can cantools
```

**1. `ev_can_source.py`** — most z pojazdu do CSV, DWA backendy:

```
# OBD-II (adapter ELM327 USB/Bluetooth) - dziala na kazdym aucie z
# zlaczem OBD-II, ale dla czystego BEV daje bardzo waski zestaw
# uzytecznych PID-ow (patrz UWAGA nizej)
python ev_can_source.py --backend obd --port /dev/ttyUSB0 --csv battery_live.csv --interval 1

# Surowy CAN + dekodowanie wedlug pliku DBC producenta/modelu - realna
# droga do bogatych danych EV (SOC, napiecie/prad pakietu, obroty/
# temperatura silnika, stan ladowania)
python ev_can_source.py --backend can --channel can0 --interface socketcan \
    --dbc moj_pojazd.dbc --signals SOC PackVoltage PackCurrent --csv battery_live.csv
```

**UWAGA (uczciwa granica, ważna decyzja projektowa):** standardowe PID-y
OBD-II (SAE J1979) są w praktyce prawie wyłącznie silnikowe/emisyjne -
dla czystego pojazdu elektrycznego (bez silnika spalinowego) tylko
`HYBRID_BATTERY_REMAINING` (PID 0x5B) i `CONTROL_MODULE_VOLTAGE` (0x42)
są realnie użyteczne i faktycznie istnieją w bibliotece `python-obd`
(zweryfikowano). To bardzo wąski wycinek tego, co dzieje się w
baterii/silniku/ładowaniu. **Backend CAN + DBC to realna droga do
pełnych danych EV** - ale wymaga pliku DBC konkretnego producenta/modelu
(identyfikatory ramek CAN w EV są w większości proprietarne, nie
ustandaryzowane jak PID-y OBD-II). Pełne uzasadnienie i wynik
weryfikacji obu backendów (CAN zweryfikowany realną wirtualną magistralą
python-can + realnym dekodowaniem cantools, patrz `test_ev_can_source.py`)
w `HISTORIA_BLEDOW.md`.

**2. `monitor_ev.py`** — monitoring ciągły/okresowy WSZYSTKICH 3
podsystemów naraz (każdy opcjonalny), z agregacją do wyniku pojazdu:

```
python monitor_ev.py \
    --battery-csv battery_live.csv --battery-auto-calibrate --battery-threshold 10.0 \
    --motor-csv motor_live.csv --motor-auto-calibrate --motor-threshold 10.0 \
    --charging-csv charging_live.csv --charging-auto-calibrate --charging-threshold 3.0 \
    --interval 5
```

Zapisuje stan do `timdr_ev_status.json` - dashboard pokazuje go w panelu
"📡 Monitoring na żywo" (wskaźniki online/offline per podsystem + wynik
pojazdu na żywo), odpytywanym co 5s przez `/api/monitor/status`. Panel
jest ukryty, dopóki `monitor_ev.py` nie zapisze pierwszego stanu - brak
monitoringu na żywo nie jest traktowany jako błąd dashboardu.

Podobnie jak `--auto-calibrate` w `TIMDR-Industrial-Predict`,
kalibracja wymaga, żeby GDZIEŚ w dostępnej próbce istniał prawdziwie
stabilny odcinek (np. początek jazdy) - nie wymyśla go z niczego.
Alternatywa: `--<podsystem>-healthy-ref plik.csv` ze znanym zdrowym
oknem odniesienia.

## REST API

```
GET  /api/scenarios     -> lista scenariuszy, pogrupowana wg podsystemu, z progiem i (opcjonalnie) calibrated_ref_n
GET  /api/demo?scenario=<id>  -> surowe dane czujników dla jednego scenariusza
POST /api/analyze       -> pelna analiza TIMDR dla JEDNEGO podsystemu (E(t), trend, anomalie, rytm, TTF, health_score)
POST /api/vehicle       -> agreguje 1-3 wyniki /api/analyze (po jednym na podsystem) w wynik calego pojazdu
GET  /api/health        -> healthcheck samego API
```

Przykład (`POST /api/analyze`):

```json
{
  "t": [0, 1, 2, ...],
  "sensors": {"vibration": [...], "winding_temp_c": [...]},
  "threshold": 10.0,
  "window": 60,
  "calibrated_ref_n": null
}
```

`calibrated_ref_n` (opcjonalne): jeśli podane, kalibruje fuzję względem
PIERWSZYCH N próbek każdego czujnika jako "stan jak nowy" i używa
`fuse_calibrated()` zamiast zwykłego `fuse()` - potrzebne dla danych bez
stabilnego zdrowego platou na początku (patrz sekcja wyżej i
`HISTORIA_BLEDOW.md`).

Przykład (`POST /api/vehicle`, wynik agregujący):

```json
{
  "vehicle_health_score": 0.05,
  "weakest_subsystem": "motor",
  "vehicle_ttf": 12.0,
  "soonest_subsystem": "motor",
  "per_subsystem": {"battery": {...}, "motor": {...}, "charging": {...}}
}
```

## Architektura

```
timdr_ev_fusion.py    - fuzja czujników (E(t), twist/trend/anomalie/rytm, kalibracja) - przeniesione z TIMDR-Industrial-Predict
timdr_ev_predict.py   - health_score, predict_failure/TTF (liniowy+wykładniczy) - przeniesione z TIMDR-Industrial-Predict
timdr_ev_vehicle.py   - agregacja 1-3 podsystemów w jeden wynik pojazdu (zasada "najsłabsze ogniwo")
demo_scenarios.py     - 6 scenariuszy demo (2 na podsystem), w tym 1 z realnymi danymi NASA
api.py                - Flask REST API + serwowanie dashboardu
static/dashboard.html - dashboard (vanilla JS, własny silnik wykresów canvas, zero zależności CDN)
data/real_battery/    - realne dane NASA PCoE (ogniwo B0047) + pełna proweniencja
ev_can_source.py      - most z realnego pojazdu (OBD-II lub CAN+DBC) do CSV, opcjonalny (obd/python-can/cantools)
monitor_ev.py         - monitoring ciągły/okresowy 3 podsystemów naraz + agregacja pojazdu, live
```

## Testy

```
python -m pytest
```

87 testów: rdzeń fuzji/predykcji (przeniesiony, zweryfikowany ponownie
po zmianie nazw), scenariusze demo (w tym regresje dla obu problemów z
sekcji "Historia błędów"), agregacja pojazdu, pełna ścieżka REST API
(Flask test client) - demo → analyze → vehicle → monitor/status, backend
CAN `ev_can_source.py` (zweryfikowany prawdziwą wirtualną magistralą
python-can + prawdziwym dekodowaniem cantools) oraz `monitor_ev.py` na
realnie wygenerowanych, rosnących plikach CSV (w tym regresja
`last_data_change`).

## Historia błędów i decyzji projektowych

Zobacz `HISTORIA_BLEDOW.md` - opisuje TYLKO problemy specyficzne dla tego
repo (dane baterii, scenariusze ładowania, agregacja pojazdu). Historia
błędów samego rdzenia fuzji/predykcji (przeniesionego z
`TIMDR-Industrial-Predict`) jest w
`TIMDR-Industrial-Predict/HISTORIA_BLEDOW.md`.
