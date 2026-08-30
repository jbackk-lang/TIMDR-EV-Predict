# Historia błędów i decyzji projektowych — TIMDR-EV-Predict

Ten plik dokumentuje realne problemy znalezione i naprawione podczas
budowy tego repo (nie tylko założone/domniemane), oraz świadome
kompromisy projektowe, które wymagały kilku iteracji, żeby zadziałały
poprawnie. Rdzeń fuzji/predykcji (`timdr_ev_fusion.py`,
`timdr_ev_predict.py`) to bezpośrednie przeniesienie z
`TIMDR-Industrial-Predict` (tam już dojrzałe, przetestowane na realnych
danych silnikowych NASA C-MAPSS) - pełna historia błędów TEGO kodu jest
w `TIMDR-Industrial-Predict/HISTORIA_BLEDOW.md`, tu dokumentowane są
tylko problemy NOWE, specyficzne dla domeny EV (bateria/silnik
elektryczny/ładowanie) i dla agregacji wielu podsystemów w jeden wynik
pojazdu.

## 1. Realne dane baterii NASA: fuse() naiwny vs kalibrowany

**Kontekst:** scenariusz `battery_real_nasa_b0047` używa PRAWDZIWYCH
pomiarów NASA PCoE (ogniwo B0047, 69 użytecznych cykli rozładowania,
patrz `data/real_battery/README.md`) - pierwsze realne, niesyntetyczne
dane baterii w tym repo.

**Znaleziony problem:** zwykłe `fuse()` liczy z-score (MAD-z) WZGLĘDEM
GLOBALNEJ MEDIANY całego dostarczonego okna. Dla silników C-MAPSS to
działa dobrze, bo mają długi, stabilny "zdrowy" początek życia - mediana
całej historii w przybliżeniu pokrywa się ze stanem zdrowym. Bateria
B0047 NIE MA takiego platou - traci pojemność praktycznie od cyklu 0.
Efekt: mediana całej 69-cyklowej historii siada gdzieś W ŚRODKU procesu
starzenia, więc:

- cykl 0 (pojemność 1.674 Ah, stan **najzdrowszy** możliwy) jest
  NAJDALEJ od tej mediany → **E=5.42** (najwyższe w całej serii)
- cykl 69 (pojemność 1.157 Ah, ~31% ubytku, stan **najbardziej
  zdegradowany**) jest BLISKO nowej, "zdegradowanej" mediany → **E=0.66**

To jest DOKŁADNA ODWROTNOŚĆ pożądanego zachowania - im gorszy stan
baterii, tym NIŻSZE E. Zweryfikowano bezpośrednio (nie domniemane):
`test_demo_scenarios.py::test_battery_real_nasa_b0047_naiwny_fuse_jest_fizycznie_odwrocony`.

Dodatkowo sprawdzono i odrzucono prostszą "naprawę": zamiana jednostki
(np. `capacity_fade = capacity[0] - capacity` zamiast surowej
`capacity`) NIE POMAGA, bo MAD-z liczony z tej samej, samoreferencyjnej
serii jest niezmienniczy na przekształcenie liniowe pojedynczej cechy -
`E = sqrt(sum(Z_i**2))` gubi znak, więc "kierunek" zmiany nigdy nie ma
znaczenia dla E, tylko jej wielkość względem reszty tej samej próby.

**Naprawiono** nie zmianą `fuse()`, tylko użyciem właściwego narzędzia:
`calibrate()` + `fuse_calibrated()` (już istniejące w przeniesionym
rdzeniu, zbudowane pierwotnie dla LIVE monitoringu OBD-II w
TIMDR-Industrial-Predict) - kalibracja względem ZAMROŻONEGO okna
odniesienia ("jak nowa": pierwsze 5 cykli), NIE względem bieżącej,
samoreferencyjnej próby. Po tej zmianie:

- cykl 0 → E≈4.57 (delikatny naturalny szum startowy, blisko zera)
- cykl 69 → E≈9.67 (wyraźnie powyżej progu EOL=7.0)

Monotonicznie rosnący, fizycznie poprawny obraz degradacji. Zweryfikowano:
`test_demo_scenarios.py::test_battery_real_nasa_b0047_kalibrowany_fuse_jest_monotonicznie_poprawny`
oraz end-to-end przez samo REST API:
`test_api.py::test_analyze_pelen_przebieg_battery_real_nasa_kalibrowany`.

**Wniosek praktyczny (ogólny, nie tylko dla tego zbioru):** naiwny,
samoreferencyjny `fuse()` jest bezpieczny TYLKO gdy dane mają stabilny,
rozpoznawalny "zdrowy" segment na początku historii. Dla danych, które
degradują się od pierwszej próbki (typowe dla baterii - starzenie
zaczyna się natychmiast, nie ma "rozruchu"), trzeba jawnie skalibrować
się względem osobnego, zaufanego punktu odniesienia (fabryczny test
odbiorczy, pierwsze N cykli "jak nowe", itp.) - dokładnie ta sama zasada,
co przy `calibrate()` w TIMDR-Industrial-Predict, tylko inny powód
(tam: krótka historia + szum rozruchowy; tu: brak platou w całym
dostępnym przebiegu).

`api.py`/`demo_scenarios.CALIBRATED_REF_N` automatyzują to rozróżnienie -
dashboard nie musi wiedzieć, który scenariusz go potrzebuje.

## 2. Kompromis rytm vs anomalia w scenariuszach ładowania (2 przypadki)

Ten sam kompromis, co przy `duty_cycle_problems` w
`TIMDR-Industrial-Predict` (pojedyncza, zbyt duża anomalia niszczy
wykrywalność rytmu opartego o wariancję) wystąpił DWUKROTNIE przy
budowie scenariuszy ładowania/sieci - i za każdym razem wymagał osobnego
strojenia empirycznego, nie dało się zgadnąć wartości z góry.

### 2a. `charging_grid_voltage_sag`

Pierwsza wersja zapadów napięcia (-18V/+6A na 3 próbki) dawała
`rhythm_score=0.0`, `periods=[]` - CAŁKOWITY brak wykrywalności rytmu
sinusoidalnej modulacji napięcia/prądu (okres=15), mimo że bez tych
zapadów rytm był wyraźnie wykrywalny (`score=0.77`). Zweryfikowano
bezpośrednio wielkość progu: -10/+3 dawało `score=0.54` (rytm wraca), ale
tylko 2 z 3 anomalii przekraczały próg detekcji (`z>3.0`). Finalna
wartość -11V/+3.5A: **wszystkie 3 anomalie wykryte ORAZ rytm nadal
wykrywalny** (`score=0.49`, okres 15 w liście wykrytych okresów).

### 2b. `charging_connector_overheat`

Odwrotny problem: pierwsza wersja usterki złącza (+25°C na tle już
bimodalnego cyklu ładowanie(~1.5)/przerwa(~0.68)) w ogóle NIE
przekraczała progu `anomalies()` (`z>3.0`) - 0 wykrytych anomalii, mimo
wyraźnego, fizycznie sensownego skoku temperatury. Powód: E(t) tego
scenariusza ma już naturalną, dużą zmienność samą z siebie (przez cykl
ładowanie/przerwa), więc +25°C nie był statystycznie odstający na tle
tej zmienności. Zwiększenie do **+50°C** dało jednoznaczną, pojedynczą
wykrytą anomalię (`z=6.6`) bez zniszczenia wykrywalności rytmu (nadal
`score=0.73`, okres=30 wykryty).

**Wniosek praktyczny:** dla sygnałów z NATURALNĄ, dużą zmiennością
periodyczną (cykle pracy/ładowania), próg wykrywalności anomalii nie
jest stały w jednostkach fizycznych - zależy od amplitudy już istniejącej
zmienności rytmicznej. Każdy nowy scenariusz z rytmem + anomalią wymaga
empirycznego sprawdzenia OBU detektorów jednocześnie, nie osobno.

## 3. Agregacja wyniku pojazdu: dlaczego minimum, nie średnia

`timdr_ev_vehicle.vehicle_health()` celowo używa MINIMUM (najsłabsze
ogniwo) po podsystemach, nie średniej ważonej. Nie jest to błąd
znaleziony w kodzie, tylko świadoma decyzja projektowa podjęta OD RAZU
(bez wcześniejszej, błędnej wersji ze średnią) - ale warto ją
udokumentować, bo jest nieoczywista i łatwo o regresję przy przyszłych
zmianach: pojazd z baterią w stanie idealnym (health=0.99) i ładowaniem
w dobrym stanie (health=0.95), ale z silnikiem TUŻ PRZED zwarciem
międzyzwojowym (health=0.05), NIE jest pojazdem w "sensownie dobrym"
stanie - jest pojazdem, który zaraz stanie z powodu silnika. Średnia
((0.99+0.95+0.05)/3 ≈ 0.66) zamaskowałaby krytyczny podsystem dobrym
wynikiem pozostałych dwóch. Test wprost porównujący te dwa podejścia:
`test_timdr_ev_vehicle.py::test_srednia_bylaby_falszywie_optymistyczna`.

To ta sama logika, co `health_score()` w `TIMDREVPredict` (mediana z
OSTATNIEGO OKNA zamiast całej historii, żeby jeden stary skok nie
"zatruwał" wyniku na stałe) - tu analogicznie: żaden zdrowy podsystem nie
powinien "rozcieńczać" jednego krytycznego.

## 4. Podłączenie do realnego pojazdu: dlaczego DWA backendy (OBD-II i CAN)

**Kontekst:** pierwsza wersja tego repo (demo + dashboard) nie miała
żadnego mostu do prawdziwego pojazdu - tylko dane syntetyczne/nagrane.
Naturalne pytanie "a jak podłączyć prawdziwe auto" wymagało decyzji
architektonicznej, nie tylko dopisania kodu.

**Zbadano i odrzucono "tylko OBD-II":** standardowe PID-y OBD-II (SAE
J1979 Mode 01) są zaprojektowane pod silniki spalinowe/emisje - dla
czystego BEV (bez silnika spalinowego) większość z nich fizycznie nie ma
czego zwrócić. Zweryfikowano bezpośrednio w bibliotece `python-obd`
(292 zdefiniowane PID-y): jedyne dwa realnie użyteczne dla hybryd/EV to
`HYBRID_BATTERY_REMAINING` (0x5B - % pojemności pakietu) i
`CONTROL_MODULE_VOLTAGE` (0x42). To za mało, żeby zasilić 3-podsystemową
fuzję (bateria potrzebuje więcej niż samego SOC - napięcie i prąd
pakietu, temperatura; silnik potrzebuje obrotów/temperatury uzwojeń;
ładowanie potrzebuje napięcia/prądu ładowania) - w praktyce tylko
producent zna te sygnały, i udostępnia je przez PROPRIETARNE ramki CAN,
nie ustandaryzowane PID-y.

**Zdecydowano: DWA backendy w `ev_can_source.py`, nie jeden.** OBD-II
zostaje jako opcja "działa od razu na każdym aucie z gniazdem OBD-II,
ale wąski zakres danych" (przeniesiony wzorzec z już zweryfikowanego
`obd_source.py` w `TIMDR-Industrial-Predict`). CAN+DBC (`python-can` +
`cantools`) to główna, pełnoprawna droga do bogatych danych EV - kosztem
wymogu posiadania pliku DBC konkretnego producenta/modelu (te pliki
bywają publicznie odtworzone przez społeczności reverse-engineeringowe
dla popularnych modeli EV, albo dostarczane przez producenta/warsztat).

**Weryfikacja - inny poziom pewności dla każdego backendu (jawnie
rozgraniczone w kodzie i tutaj):**
- Backend `can`: zweryfikowany W TEJ SESJI wobec PRAWDZIWEJ, wbudowanej
  w `python-can` magistrali wirtualnej (`interface="virtual"`) - dwa
  niezależne procesy/wątki komunikują się realnymi ramkami CAN przez tę
  magistralę, dekodowanymi PRAWDZIWYM `cantools` według realnego pliku
  DBC (`test_vehicle.dbc`, dołączony do repo). Zweryfikowano też, że
  ramki NIE dotyczące żądanych sygnałów są poprawnie ignorowane (test
  `test_run_can_ignoruje_ramki_spoza_zadanych_sygnalow`). To jest test
  protokołu/kodu integracyjnego - NIE test na fizycznej magistrali
  pojazdu (niedostępne w tym środowisku).
- Backend `obd`: NIE testowany ponownie w tej sesji (wymagałby
  fizycznego adaptera albo `ELM327-emulator`, jak w
  `TIMDR-Industrial-Predict`) - kod jest bezpośrednią adaptacją już
  zweryfikowanego tam wzorca (`obd_source.py`), z inną domyślną listą
  PID-ów. Ryzyko regresji niskie (ta sama biblioteka, ten sam kształt
  kodu), ale to uczciwie NIE jest to samo co świeża weryfikacja - jeśli
  coś nie zadziała na prawdziwym adapterze, to tutaj priorytet do
  sprawdzenia w pierwszej kolejności.

## 5. monitor_ev.py: te same sygnały online/offline, ale PER PODSYSTEM

`monitor_ev.py` + `/api/monitor/status` powtarzają rozróżnienie
`checker_online` vs `data_live` z `TIMDR-Industrial-Predict` (patrz tam
po pełne uzasadnienie), ale zastosowane OSOBNO dla każdego z 3
podsystemów - bo w prawdziwym pojeździe adapter CAN/OBD baterii może się
rozłączyć NIEZALEŻNIE od adaptera silnika (np. różne magistrale CAN,
różne fizyczne złącza). Zweryfikowano wprost (nie tylko dla jednego
strumienia jak w Industrial-Predict, ale i dla interakcji między
podsystemami):
- `last_data_change` per podsystem NIE aktualizuje się, gdy dany CSV nie
  rośnie między sprawdzeniami (`test_monitor_ev.py::test_last_data_change_nie_aktualizuje_sie_gdy_n_samples_sie_nie_zmienia`),
  nawet gdy INNE podsystemy w tym samym wywołaniu `monitor_ev.py` mają
  świeże dane.
- Agregacja pojazdu (`vehicle_health()`) poprawnie działa na PODZBIORZE
  aktywnych podsystemów w trybie live (nie tylko w testach czysto na
  `timdr_ev_vehicle.py`) - zweryfikowano end-to-end: CSV → `monitor_ev.py`
  → `timdr_ev_status.json` → `/api/monitor/status` → poprawne wartości
  `checker_online`/`data_live` (w tym `None` w trybie `--once`, gdzie nie
  ma jednego stałego oczekiwanego rytmu do porównania).

## 6. Co zostało poprawnie zaprojektowane od razu (bez błędów)

- Rdzeń fuzji/predykcji przeniesiony 1:1 z `TIMDR-Industrial-Predict`
  (już dojrzały, 47/47 testów przeszło od razu po zmianie nazw klas/modułów).
- Rozróżnienie `calibrated_ref_n` jako pole per-scenariusz
  (`demo_scenarios.CALIBRATED_REF_N`), automatycznie honorowane przez
  `/api/analyze` - dashboard nie musi znać szczegółów, które scenariusze
  tego potrzebują.
- `vehicle_health()` działa poprawnie na PODZBIORZE 1-2 podsystemów od
  razu (np. pojazd, który akurat się nie ładuje) - zweryfikowane testem
  `test_dziala_na_podzbiorze_podsystemow`, nie wymagało poprawki.
