# Realne dane starzenia baterii (NASA PCoE) użyte do testów TIMDR-EV-Predict

## Źródło

Oryginalny zbiór: **NASA Prognostics Center of Excellence (PCoE) Li-ion
Battery Aging Dataset** - baterie 18650 (2 Ah nominalnie) cyklicznie
ładowane/rozładowywane aż do End-Of-Life (klasyczne kryterium: 30% ubytku
pojemności, dla części ogniw 20%) - dane rządowe/badawcze, bez restrykcji
licencyjnych na same pomiary.

Oryginalny format to pliki `.mat` (MATLAB), zbyt duże (15-16MB/ogniwo) i
niewygodne do pobrania narzędziem tekstowym. Użyto zamiast tego
pre-oczyszczonej wersji CSV, wystawionej publicznie przez HuggingFace
Space `NeerajCodz/aiBatteryLifeCycle`:
`https://huggingface.co/spaces/NeerajCodz/aiBatteryLifeCycle` (plik
źródłowy: `cleaned_dataset/metadata.csv`).

## Plik

`nasa_b0047_discharge_capacity.csv` - ogniwo **B0047**, 72 kolejne cykle
rozładowania (ambient_temperature=4°C), kolumny `cycle,capacity_ah`.

Pojemność spada z **1.674 Ah (cykl 0)** do **1.157 Ah (cykl 71)** -
ubytek **30.9%**, tuż powyżej klasycznego kryterium EOL (30% fade).

## Znana wada danych (nieukryta, celowo odfiltrowana)

3 z 72 wierszy mają w źródle `Capacity=0` (cykle 19, 53, 65 w numeracji
tego pliku) - fizycznie niemożliwe, żeby pojemność ogniwa spadła do zera
w połowie eksperymentu i wróciła do normy w kolejnym cyklu. To najpewniej
przerwane/nieudane przebiegi testowe w oryginalnym zbiorze NASA, nie
prawdziwy pomiar. W `nasa_b0047_discharge_capacity.csv` te wiersze mają
PUSTĄ wartość `capacity_ah` (nie `0`, żeby jawnie odróżnić "brak danych"
od "zmierzone zero") - `demo_scenarios._load_real_battery_b0047()`
odrzuca je zamiast interpolować, żeby nie fabrykować danych.

## Do czego posłużyło - i co ZNALEZIONO

Scenariusz demo `battery_real_nasa_b0047`. Test na tych danych ujawnił
realny, konkretny problem z naiwnym `fuse()` na sygnale, który degraduje
się OD PIERWSZEJ próbki (bez stabilnego zdrowego platou na początku, w
odróżnieniu od silników C-MAPSS w `TIMDR-Industrial-Predict`) - patrz
`HISTORIA_BLEDOW.md`, sekcja "Realne dane baterii NASA: fuse() naiwny vs
kalibrowany" po pełny opis i zmierzone wartości.

## Użycie

```python
from demo_scenarios import battery_real_nasa_b0047
t, sensors = battery_real_nasa_b0047()  # t=cykle, sensors={"capacity_ah": [...]}
```

Ten scenariusz wymaga kalibracji (`calibrate()` + `fuse_calibrated()`,
nie zwykłego `fuse()`) - `demo_scenarios.CALIBRATED_REF_N` i `api.py`
(`/api/analyze`, pole `calibrated_ref_n`) obsługują to automatycznie.
