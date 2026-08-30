# Historia błędów i poprawek — TIMDR-Industrial-Predict

Ten plik zawiera pełną, szczegółową historię znalezionych błędów,
sposobu ich weryfikacji i wprowadzonych poprawek — wydzieloną z głównego
`README.md`, żeby ten drugi mógł skupić się wyłącznie na tym, jak
uruchomić i używać programu. Jeśli interesuje Cię tylko obsługa
(monitoring na żywo, dashboard, API, dane demo) — wystarczy `README.md`.
Ten plik jest dla osób, które chcą wiedzieć, CO dokładnie było nie tak,
jak to znaleziono i jak to naprawiono (przydatne przy audycie,
code review, albo przy rozbudowie kodu w przyszłości).

## Status testów

61/61 testów (`pytest -q`, dwa pliki: `test_timdr_industrial_fusion.py`,
`test_timdr_industrial_predict.py`, `test_demo_scenarios.py`). Znalezione
i naprawione po drodze: 3 błędy w `timdr_industrial_fusion.py` (w tym
jeden mylący trend z rytmem), 3 błędy w `timdr_industrial_predict.py`
(w tym jeden krytyczny — TTF zależne od bezwzględnego znacznika czasu),
5 dalszych błędów/właściwości znalezionych przy testach na REALNYCH
danych przemysłowych (NASA C-MAPSS, emulowany OBD-II) — patrz niżej.

## 🐛 Poprawka: wykresy dashboardu (w tym czujniki) nie pokazywały się w ogóle

Pierwsza wersja dashboardu ładowała Chart.js z zewnętrznego CDN
(`cdnjs.cloudflare.com`). Jeśli przeglądarka nie ma dostępu do tego
akurat adresu (firewall firmowy, offline, DNS/proxy) — CDN nie ładuje
się CICHO, `Chart` zostaje niezdefiniowane, i **wszystkie** wykresy
(nie tylko czujniki — też E(t), trend, a nawet reszta inicjalizacji
strony) przestają działać bez żadnego widocznego komunikatu w UI (błąd
trafia tylko do konsoli deweloperskiej przeglądarki). Zweryfikowano
wprost w tym środowisku: próba pobrania tego samego pliku z CDN
zablokowana przez proxy sandboxa — dokładnie ten typ awarii sieciowej,
na który dashboard był podatny.

Naprawiono przez usunięcie zależności od CDN całkowicie: wykresy
rysowane są własnym, ok. 100-liniowym silnikiem opartym o `<canvas>` i
2D Context API (funkcja `drawChart()` w `dashboard.html`) — zero
zależności zewnętrznych, więc strona działa identycznie z internetem i
bez niego. Dodatkowo każdy krok inicjalizacji (`loadScenarios`,
`loadDemo`, `runAnalyze`) ma teraz `.catch()`, który wyświetla błąd w
widocznym polu `#err` zamiast cichego `Uncaught (in promise)` w
konsoli — jeśli coś pójdzie nie tak, będzie to widoczne na stronie, nie
tylko w devtools.

Zweryfikowano: (1) `node --check` — składnia, (2) uruchomienie
faktycznej logiki `drawChart()`/`parseCsv()` w Node z podstawionym
fałszywym `canvas`/`document` (7 przypadków: normalny sygnał, puste
dane, seria z markerami anomalii/twist + progiem, wartości
null/NaN/Infinity, pojedynczy punkt, wąski kontener, błędny CSV) — żaden
nie rzuca wyjątku, (3) porównanie wszystkich `getElementById()` w JS z
`id=` w HTML — brak rozbieżności, (4) `grep` po `http`/`https` w
`dashboard.html` — zero zewnętrznych adresów URL.

**Uczciwe zastrzeżenie**: w tym środowisku (piaskownica bez
przeglądarki/wyświetlacza) nadal nie da się dosłownie zobaczyć
wyrenderowanych pikseli — ale zamiast tylko czytania kodu,
zweryfikowałem: (1) całe API prawdziwymi zapytaniami HTTP z serwerem
faktycznie uruchomionym, (2) składnię JS (`node --check`), (3) FAKTYCZNE
WYKONANIE logiki rysującej wykresy w Node z podstawionym fałszywym
`canvas`/DOM na 7 przypadkach brzegowych — żaden nie rzucił wyjątku,
(4) zgodność wszystkich `getElementById()` z `id=` w HTML, (5) brak
jakichkolwiek zewnętrznych adresów URL na stronie. To najsurowsza
weryfikacja frontendu, jaką dało się zrobić bez realnej przeglądarki.

## 🎲 3 nieoczywiste właściwości fuzji wielu czujników (znalezione przy budowie 5 scenariuszy demo)

Nie błędy w kodzie — właściwości samej metody median/MAD, warte
znajomości przy projektowaniu własnych scenariuszy/czujników:

1. **Rozcieńczanie rytmu przez niezwiązane czujniki**: `_mad_z()`
   normalizuje każdą cechę do porównywalnej skali — to dobra poprawka
   przeciw dominacji skali, ale oznacza, że czysto szumowy, niezwiązany
   z badanym zjawiskiem czujnik wnosi do E(t) TYLE SAMO znormalizowanej
   "energii" co prawdziwy sygnał okresowy. Zweryfikowano: dodanie 2
   niezwiązanych czujników (szum) do sygnału z czystą periodycznością
   zmniejszało `rhythm_score` z 0,73 do 0,24 (poniżej progu 0,4).
   Rozwiązanie: fuzuj czujniki fizycznie związane z badanym zjawiskiem.
2. **Ekstremalne odstające punkty niszczą wykrywalność rytmu**:
   znormalizowana autokorelacja nie jest odporna na pojedyncze, bardzo
   duże wartości odstające (dominują wariancję w mianowniku) — zbyt
   duży/szeroki skok "awarii" potrafił zrzucić `rhythm_score` z ~0,8 do
   0,0, mimo niezmienionej periodyczności reszty sygnału.
3. **Powolny dryf bazowy ginie w lokalnej zmienności**: `_mad_z()`
   mierzy odległość od GLOBALNEJ (nie ruchomej) mediany — dryf słabszy
   niż lokalne wahania cyklu (np. duty-cycle) może zostać "wchłonięty" i
   nie być widoczny jako trend w ogóle, nawet gdy realnie rośnie.

## 🐛 Błędy w `timdr_industrial_fusion.py`

### 1. `rhythm()` myli trend z periodycznością

Oryginalny kod tylko odejmował średnią i zgłaszał **każdy** lag powyżej
progu, nie tylko lokalne maksima autokorelacji. Zweryfikowano: czysty
rosnący trend (zero periodyczności, z realistycznym szumem czujnika)
dawał `rhythm_score≈0,99` i **48 "wykrytych okresów"** — dokładnie to,
czego ten moduł ma NIE robić, bo trend to główny sygnał degradacji, nie
szum do zignorowania. Naprawiono: pełny detrend (nachylenie + wyraz
wolny) przed autokorelacją + zgłaszanie tylko lokalnych maksimów.
Zweryfikowano też, że prawdziwa okresowość (okres 15) nałożona na silny
trend nadal poprawnie wychodzi po poprawce.

### 2. Krótkie sygnały (n<2) crashowały `twist()`/`rhythm()`

`np.gradient` wywoływany bez ochrony na sygnałach 0-1-elementowych dawał
`IndexError`. Naprawiono zgodnie ze standardem reszty modułów TIMDR w
tym zestawie repozytoriów.

### 3. `fusion_score()` na pustych tablicach

`np.max([])` rzuca `ValueError` — może się zdarzyć dla bardzo krótkich
sygnałów po poprawce #2. Naprawiono (`safe_max`).

## 🐛 Błędy w `timdr_industrial_predict.py`

### 1. TTF zwracane jako współrzędna czasu, nie czas pozostały (najpoważniejszy błąd)

Oryginalny kod zwracał `(threshold - b) / a` wprost — czyli punkt na osi
`t`, w którym model przewiduje przekroczenie progu, **nie** odejmując
bieżącego czasu. Zweryfikowano wprost: przesunięcie całego `t` o
+1000s (fizycznie identyczna sytuacja maszyny) zmieniało zwrócone "TTF"
też dokładnie o +1000s. Z realnymi znacznikami epoch (~1,75 miliarda)
dawało to **"czas do awarii: ~330 lat"** zamiast realnej wartości.
Naprawiono: TTF liczone jest jako różnica względem ostatniej próbki
(`t[-1]`), w tej samej, wycentrowanej skali co dopasowanie regresji
(patrz błąd #3 niżej).

### 2. TTF zależne 16-krotnie od długości historii danych, nie od stanu maszyny

Regresja (liniowa i wykładnicza) liczona była na CAŁEJ historii E(t) od
`t=0`. Zweryfikowano: dla FIZYCZNIE IDENTYCZNEJ ostatniej fazy
degradacji (te same 100 próbek), ale różnej długości wcześniejszej
zdrowej historii, przewidywany TTF wychodził:

| długość zdrowej historii | TTF |
|---|---|
| 50s | 135s |
| 150s | 273s |
| 400s | 818s |
| 800s | 2254s |

16-krotna różnica dla tej samej aktualnej sytuacji maszyny. Naprawiono:
regresja liczona tylko na ostatnich `window` próbkach (domyślnie 60). Po
poprawce te same 4 warianty dają wynik w zakresie 42,6-43,0s —
zbieżność, nie rozjazd rzędów wielkości.

### 3. Niestabilność numeryczna regresji przy realnych znacznikach czasu

Nawet po poprawce #2, `lstsq` na surowych wartościach `t` (kolumna `[t,
1]`) jest źle uwarunkowane dla dużych, przesuniętych wartości czasu
(epoch, rząd 1e9) — kolumny różnią się o ~9 rzędów wielkości. Naprawiono:
`t` jest centrowane (`t - t[0]` okna) przed dopasowaniem.

### 4. `health_score()` permanentnie zatruty starym zdarzeniem

Oryginalny kod liczył z-score E względem WŁASNEJ CAŁEJ historii i brał
`max()` po wszystkim — jeden stary, jednorazowy skok (np. chwilowe
zakłócenie czujnika) blokował wynik na zawsze. Zweryfikowano: 500
próbek, jednorazowy skok w próbce 50, reszta (450 próbek = 90% danych)
w normie → `health_score=0,000` (permanentnie "krytyczny"), mimo że
maszyna od dawna pracuje normalnie. Dodatkowy problem: skala
health_score (z-score własnej historii /5) nie miała żadnego związku z
`threshold` używanym w `predict_failure()` — "krytyczny" w obu
miejscach mogło oznaczać zupełnie różne wartości E. Naprawiono:
health_score liczy medianę z ostatnich `window` próbek (domyślnie 20)
względem TEGO SAMEGO `threshold`, co `predict_failure()`.

## 🧪 Pierwszy test na PRAWDZIWYCH danych przemysłowych (NASA C-MAPSS) i błędy stąd znalezione

Pierwszy test fuzji wielu czujników na realnym urządzeniu (nie demo):
NASA C-MAPSS FD001, silnik turbowentylatorowy nr 1, pełna trajektoria
run-to-failure, 192 prawdziwe cykle, 10 z 21 realnych czujników (znany w
literaturze niestały/informatywny podzbiór dla FD001).

**Wynik pozytywny (metryka pre-zarejestrowana przed uruchomieniem)**:
korelacja Spearmana fuzowanego E(t) z numerem cyklu: rho=0,39,
p=2×10⁻⁸ — realny, istotny statystycznie sygnał degradacji. Kontrola
negatywna (czujnik znany jako stały w FD001) dała zerową wariancję,
zgodnie z oczekiwaniem. Detektor anomalii złapał 27 zdarzeń, 100% w
ostatnich 20% życia silnika.

### Błąd 1: samoreferencyjne `fuse()` daje fałszywy alarm krytyczny na zdrowym rozruchu

Przy symulacji PRZYCZYNOWEJ monitoringu na żywo (statystyki liczone
tylko z danych dostępnych DO danego cyklu, bez podglądu przyszłości —
pierwsza wersja testu miała tu błąd look-ahead, złapany i poprawiony
przed wyciągnięciem wniosków): na cyklu 10 — praktycznie nowym, zdrowym
silniku — `health_score()`/`predict_failure()` zwracały
`health_score=0,000`/`TTF=0` ("już awaria, teraz"). Przyczyna: MAD-z
liczone z bardzo krótkiego, biegnącego okna jest niestabilne przy małej
próbie.

**Naprawiono przez `calibrate()`/`fuse_calibrated()`** (nowe metody w
`timdr_industrial_fusion.py`): zamiast liczyć median/MAD z bieżącego,
mogącego być zbyt krótkiego okna, kalibruje się je RAZ z osobno
dostarczonego zdrowego zbioru referencyjnego (test odbiorczy, znana
zdrowa faza pracy). To rozwiązuje problem inaczej niż "poczekaj na
więcej próbek" (co ukryłoby prawdziwą usterkę, gdyby wystąpiła od razu)
— zweryfikowano wprost dwoma testami jednostkowymi i jednym na realnych
danych: zdrowy rozruch nie daje już fałszywego alarmu, a wstrzyknięta
prawdziwa usterka od pierwszej próbki nadal wychodzi natychmiast.

### Błąd 2 (głębszy, znaleziony przy tym samym teście): stały próg 3.0 nie skaluje się z liczbą fuzowanych czujników

`E=sqrt(sum(Z**2))` w oryginalnym `fuse()` rośnie z **pierwiastkiem z
liczby czujników** nawet dla czysto zdrowych danych — to własność
rozkładu chi z k stopniami swobody, nie usterka konkretnych danych.
Zweryfikowano na realnym silniku: mediana E w zdrowym oknie kalibracji
przy k=4 czujnikach (jak oryginalne demo) = 1,73 (blisko teoretycznego
sqrt(4)=2,0); przy k=10 czujnikach = 3,03 (blisko sqrt(10)=3,16). Czyli
dokładnie ta sama zdrowa maszyna z większą liczbą podłączonych
czujników wygląda na coraz bardziej "chorą" przy tym samym stałym progu
3.0 — wyłącznie z powodu liczby kanałów, nie stanu maszyny.

**Naprawiono w `fuse_calibrated()`** (nie w `fuse()` — tam zostawiono
oryginalną skalę dla wstecznej zgodności z już zweryfikowanymi progami
5 scenariuszy demo, wszystkie zbudowane na k=4): `E=sqrt(mean(Z**2))`
(RMS zamiast sumy), więc zdrowa wartość E oscyluje koło 1,0 niezależnie
od liczby fuzowanych czujników. Zweryfikowano testem jednostkowym (k=3
vs 6 vs 12 zdrowych kanałów, mediana E w granicach 1,5x) oraz na
realnym silniku: po obu poprawkach health_score poprawnie utrzymuje się
wysoko (0,55-0,69) przez pierwsze ~100 zdrowych cykli, po czym spada
monotonicznie do 0,000 dokładnie w realnej fazie końca życia silnika
(cykl 150-192).

### Błąd 3: TTF trzeba unormować czasem — opóźnienie + mediana z okresu

Powyższy problem (TTF przeskakujące np. inf → 87,7 → inf → 737,4 → inf
na fizycznie tej samej, zdrowej fazie silnika) nie był jeszcze
naprawiony — to osobna usterka od błędów 1-2 (tamte dotyczyły SKALI E,
ta dotyczy NIESTABILNOŚCI pojedynczego punktowego dopasowania regresji
na prawie płaskim/zaszumionym sygnale).

**Naprawiono nową metodą `predict_failure_smoothed()`** w
`timdr_industrial_predict.py`, dwoma mechanizmami naraz:
- **opóźnienie** — przy historii krótszej niż `min_len` (domyślnie 5) w
  ogóle nie próbuje się szacować TTF (zwraca `inf`, `confirmed=False`)
  — zbyt krótka historia to gwarantowany szum, nie sygnał;
- **mediana z okresu** — zamiast jednego punktowego dopasowania, liczy
  `predict_failure()` osobno dla każdego z ostatnich `smooth_window`
  (domyślnie 10) kończących punktów historii i zwraca MEDIANĘ tych
  surowych oszacowań (mediana, nie średnia arytmetyczna — żeby
  pojedyncze `inf` nie psuło wyniku); dodatkowo `confirmed=False`, jeśli
  większość ostatnich surowych oszacowań to `inf` (brak wykrywalnego
  trendu), zamiast zwracać pojedynczy, potencjalnie przypadkowy wynik.

Zweryfikowano na realnym silniku C-MAPSS: w fazie zdrowej (cykle 20-50)
surowy TTF dawał losowe skoki (inf, 737,4, inf), unormowana wersja
poprawnie odpowiada `confirmed=False` zamiast zgadywać konkretną
liczbę. W fazie realnej degradacji (cykl 100+) obie wersje się zgadzają
co do rzędu wielkości i obie poprawnie łapią krytyczny stan pod koniec
życia silnika (cykl 180-192). Dodatkowy test jednostkowy potwierdza:
wariancja szacowań TTF w czasie jest niższa dla wersji unormowanej niż
dla pojedynczego odczytu na tym samym zaszumionym, płaskim sygnale.

**Wciąż otwarte, uczciwie nie ukrywane**: `confirmed=False` samo w
sobie nie mówi, CZY maszyna jest zdrowa, tylko że model nie ma jeszcze
wystarczająco spójnego trendu, by podać liczbę — to świadomie
konserwatywne "nie wiem", nie "wszystko OK". Dobór `smooth_window=10`
nie był strojony pod ten konkretny silnik (żeby uniknąć post-hoc
dopasowania do wyniku), ale też nie był testowany na innym realnym
przebiegu — może wymagać dostrojenia do dynamiki konkretnej maszyny.

Pliki: `timdr_industrial_fusion.py` (`calibrate()`, `fuse_calibrated()`),
`timdr_industrial_predict.py` (`predict_failure_smoothed()`),
`test_timdr_industrial_fusion.py` (4 nowe testy),
`test_timdr_industrial_predict.py` (3 nowe testy).

### Błąd 4: autokalibracja przed uruchomieniem na innym silniku

`calibrate()` wymaga PODANIA zdrowego okresu odniesienia — dobre przy
znanym, ustabilizowanym urządzeniu, ale niewygodne (i ryzykowne) przy
przenoszeniu na nowy, nieznany silnik: jeśli operator poda jako "zdrowy"
fragment, który akurat jest przejściowy (rozruch, rozpędzanie),
kalibracja dziedziczy ten błąd. Zweryfikowano wprost na emulowanym
OBD-II: silnik przyspieszający od 620 do 1000+ obr/min podczas "zdrowej"
referencji dawał `health_score=0,10`, `ALARM` po 25 próbkach, mimo braku
jakiejkolwiek usterki — bo KAŻDA kolejna próbka na tej samej rampie musi
wyglądać na odchylenie od punktu wziętego z początku rampy.

**Naprawiono `auto_calibrate()`** w `timdr_industrial_fusion.py`: zamiast
brać pierwsze próbki na wiarę, przeszukuje pierwsze `probe_window`
próbek w poszukiwaniu najbardziej STABILNEGO ciągłego podokna (najniższa
łączna znormalizowana zmienność — `std/|mediana|` sumowane po
czujnikach) i kalibruje z niego. `monitor.py` ma `--auto-calibrate` jako
alternatywę dla `--healthy-ref`.

Zweryfikowano na realnym silniku C-MAPSS: wybrało okno zaczynające się
na cyklu 30 (nie 0), z niższą zmiennością niż naiwne pierwsze 20 cykli —
health_score po tym pozostaje wysoki (0,64-0,71) przez pierwsze ~50
cykli i poprawnie spada do 0,000 na realnym końcu życia silnika.

**Uczciwie zweryfikowano też przypadek, w którym autokalibracja NIE
POMAGA w pełni** — na tym samym emulowanym OBD-II (cała 80-próbkowa
sesja to jedna ciągła rampa przyspieszenia, bez ŻADNEGO prawdziwie
stabilnego odcinka): autokalibracja wybrała obiektywnie lepsze okno
(zmienność 0,156 vs 0,654 dla naiwnego pierwszego — 4x lepiej), ale mimo
to system ostatecznie zgłasza `ALARM`, bo dane naprawdę nigdy się nie
stabilizują. To fundamentalne ograniczenie DANYCH, nie coś, co dowolny
algorytm kalibracji może naprawić — `auto_calibrate()` zwraca pełną
diagnostykę (`chosen_start`, `variability_chosen` vs
`variability_naive_first`), żeby ten wybór był sprawdzalny, a nie ukryty
w czarnej skrzynce.

Pliki: `timdr_industrial_fusion.py` (`auto_calibrate()`), `monitor.py`
(`--auto-calibrate`, `--calib-probe`, `--calib-window`),
`test_timdr_industrial_fusion.py` (3 nowe testy).

### Błąd 5: ile próbek trzeba do stabilizacji + auto-walidacja sprawdzonymi testami statystycznymi

Dwa pytania w jednym: (1) ile próbek system faktycznie potrzebuje, żeby
jego statystyki kalibracyjne się "wygładziły", i (2) czy istnieje
sprawdzona (nie domowej roboty) metoda auto-walidacji wybranego okna
kalibracyjnego.

**(2) Auto-walidacja**: dodano `_mann_kendall()` — standardowy,
nieparametryczny test na trend monotoniczny (Mann 1945, Kendall 1975),
napisany ręcznie wprost ze wzoru (statystyka S z sumy znaków par,
wariancja `n(n-1)(2n+5)/18`, z-statystyka z korekcją ciągłości), zamiast
domowej heurystyki. `validate_window()` uruchamia go na każdym czujniku
w wybranym oknie kalibracyjnym i odrzuca okno, jeśli którykolwiek
czujnik wykazuje istotny statystycznie trend (p<0,05) — okno
kalibracyjne z założenia ma reprezentować STABILNY stan, a trend w
środku niego oznacza, że okno wcale nie jest tak stabilne, jak sugerował
sam wskaźnik zmienności. Wpięto to bezpośrednio w `auto_calibrate()`,
które teraz zwraca dodatkowo `validated` i `validation_detail`.

Zweryfikowano na realnym silniku C-MAPSS: okno wybrane przez
`auto_calibrate()` (cykle 30-50, najniższa zmienność) dostaje
`validated: False` — jeden z 10 czujników (S=-65, p=0,038) wykazuje
istotny trend nawet w tym nominalnie najlepszym oknie. To NIE jest błąd
kodu — to uczciwe, prawdziwe odkrycie: silniki C-MAPSS to dane
"run-to-failure" zaprojektowane tak, by degradacja była ciągła od
początku życia silnika, więc nawet "najzdrowszy" dostępny fragment może
mieć statystycznie wykrywalny (choć praktycznie mały) trend. Mann-Kendall
robi dokładnie to, do czego został zaprojektowany — wykrywa trend, nawet
słaby, przy wystarczającej liczbie próbek. To rozróżnienie "istotne
statystycznie" vs "istotne praktycznie" trzeba rozumieć przy
interpretacji wyniku, a nie traktować `validated: False` jako "system
nie działa". Ten sam wzorzec potwierdzono niezależnie na DRUGIM realnym
silniku (unit 2, pierwsze 85 cykli) — jeden czujnik wykazał istotny
trend (p=0,006) nawet w automatycznie wybranym, "najbardziej stabilnym"
oknie.

**(1) Ile próbek do stabilizacji**: dodano `calibration_convergence()` —
śledzi względną zmianę wektora median (po wszystkich czujnikach) w miarę
wzrostu liczby próbek i wymaga kilku kolejnych potwierdzeń małej zmiany
pod rząd, zanim uzna zbieżność (analogia do testów zbieżności Monte
Carlo / batch-means).

**Prawdziwy błąd znaleziony i naprawiony w trakcie tej pracy**: pierwsza
wersja normalizowała zmianę względem BIEŻĄCEJ (rosnącej z n) mediany. Na
realnych danych OBD-II (ciągła rampa RPM 620→2200+, bez żadnego
spłaszczenia) dawało to `n_required=45` — FAŁSZYWĄ zbieżność, mimo że
mediana rosła liniowo bez przerwy (potwierdzone wprost: mediana dla
n=5..80 to 660, 710, 810, 910, 1010, 1060, 1110, ... — czysta linia
prosta). Przyczyna: stały krok bezwzględny (np. +50) dzielony przez
CORAZ WIĘKSZY mianownik (rosnącą medianę) w końcu spada poniżej progu
5%, mimo że sygnał wcale się nie ustabilizował — to fałszywa zbieżność
wynikająca z rosnącego punktu odniesienia, nie z faktycznego
wypłaszczenia. Znaleziono to samodzielnie przy testowaniu, nie zostało
zgłoszone przez użytkownika.

**Naprawiono**: mianownik to teraz STAŁA skala rozrzutu (MAD, liczona
raz z całego dostępnego okresu próbnego), a nie rosnąca mediana. Po
naprawie: ta sama rampa OBD poprawnie zwraca `n_required=None`
(rel_change utrzymuje się na stałym poziomie ~8,4%, nigdy nie spada
poniżej progu — dokładnie tak, jak powinno wyglądać dla sygnału bez
żadnej stabilizacji). Dodano dedykowany test regresyjny
(`test_calibration_convergence_rampa_z_duzym_punktem_startowym_nie_zbiega_falszywie`)
i potwierdzono wprost, że STARA (błędna) formuła na tym samym teście
zwraca `n_required=50` (fałszywy pozytyw), a naprawiona — `None`.

Wyniki po naprawie na realnych danych:

- **OBD-II, ciągła rampa (nigdy się nie stabilizuje)**: `n_required=None` — poprawnie.
- **C-MAPSS, realny silnik 1, 10 czujników, pierwsze 100 cykli, próg 5%**:
  `n_required=None` — silnik NIE stabilizuje się w sensie ścisłym w
  pierwszych 100 cyklach (zgodne z odkryciem Mann-Kendalla wyżej —
  ciągła, niska-amplitudowa degradacja od początku). Przy złagodzeniu
  progu do 10%: `n_required=80`.
- **Kontrola pozytywna (sztuczny szum wokół stałej, brak trendu)**:
  `n_required=20` — szybka, poprawna zbieżność, jak oczekiwano.

Uczciwy wniosek: nie ma jednej uniwersalnej liczby "ile próbek trzeba" —
zależy to od tego, jak blisko prawdziwie stabilnego stanu jest urządzenie
w ogóle. System teraz to POKAZUJE (konkretną liczbę albo `None`) zamiast
zakładać z góry stałą liczbę próbek rozruchowych.

Pliki: `timdr_industrial_fusion.py` (`calibration_convergence()`,
`_mann_kendall()`, `validate_window()`, `auto_calibrate()` teraz zwraca
też `validated`/`validation_detail`), `test_timdr_industrial_fusion.py`
(11 nowych testów, w tym pozytywne i negatywne kontrole dla
Mann-Kendalla oraz dedykowany test regresyjny na opisany wyżej błąd).

## Drugi realny silnik (C-MAPSS unit 2) — potwierdzenie na niezależnych danych

Żeby nie wyciągać wniosków z jednego przypadku, cały pipeline
(auto-kalibracja, `calibration_convergence()`, `validate_window()`,
symulacja monitoringu na żywo) uruchomiono także na DRUGIM, niezależnym
silniku (unit 2, pierwsze 85 cykli — uczciwie: fragment, nie pełny
przebieg, patrz `data/real_engines/README.md`).

Wyniki żywej symulacji (n=40→50→60→70→85 próbek, kalibracja policzona
raz na pierwszych 40 próbkach i ponownie użyta): health_score stabilnie
0,47-0,55, TTF rosnące (85→92→112→147→296), brak alarmu przez cały
czas — spójne z silnikiem faktycznie znajdującym się we wczesnej,
zdrowej fazie życia. Walidacja Mann-Kendalla dała ten sam wzorzec co na
silniku 1 (patrz Błąd 5 wyżej): jeden czujnik z istotnym trendem nawet
w najlepszym wybranym oknie — potwierdza to, że to nie przypadek
jednego silnika, tylko właściwość samych danych run-to-failure.

## Usunięcie zależności od scipy — blokowana przez Device Guard/WDAC na maszynie firmowej

Jedyne użycie scipy w całym repo (`timdr_industrial_fusion.py`,
`validate_window()`, test Manna-Kendalla): `p = 2 * (1 - norm.cdf(abs(z)))`
z `scipy.stats`. Na komputerze firmowym użytkownika (Windows z polityką
Device Guard/WDAC) import `scipy.stats` wywalał się z
`ImportError: DLL load failed while importing _quadpack: Twoja
organizacja zablokowała tę aplikację za pomocą funkcji Device Guard` —
mimo że Python był już zainstalowany systemowo w `C:\Program Files\`
(inny skompilowany DLL z tego samego środowiska, `pandas`, ładował się
bez problemu — czyli blokada jest bardziej granularna niż "ścieżka
user-profile vs systemowa", raczej per-plik/wydawca).

Naprawione bez utraty dokładności: `2*(1-norm.cdf(|z|))` to dokładna
tożsamość matematyczna `math.erfc(|z|/sqrt(2))` dla standardowego
rozkładu normalnego — nie przybliżenie. `math.erfc` jest w bibliotece
standardowej Pythona (wbudowany w interpreter, bez własnego ładowanego
DLL), więc eliminuje jedyną zależność od scipy w tym repo. `scipy`
usunięte też z `requirements.txt`. 61/61 testów przechodzi bez zmian po
tej podmianie.

**Ten sam plik (`timdr_ev_fusion.py`) był skopiowany 1:1 do
`TIMDR-EV-Predict` — identyczna poprawka zastosowana tam równolegle.**

## ✅ Co było już poprawnie zaprojektowane (bez zmian)

- `fuse()`: normalizacja każdej cechy (median/MAD) przed połączeniem w
  normę — unika błędu "dominacji skali" znalezionego wcześniej w innych
  modułach tej rodziny (`timdr_security.py`, `timdr_rhythm.py`).
- `twist()`: `np.gradient(E, t)` z realnym `t`, nie po indeksie.
- `_mad_z()`: median/MAD zamiast mean/std — odporne na to, że pojedyncza
  anomalia zawyży własny próg detekcji (sprawdzone empirycznie).
- Podstawowy szkielet autokorelacji w `rhythm()` (korekta malejącego
  okna nakładania przez dzielenie przez `n-lag`) — ta sama poprawna
  technika, którą wprowadziliśmy wcześniej w
  `TIMDR-Security-Module/timdr_rhythm.py`.
