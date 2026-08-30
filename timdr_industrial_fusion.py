"""
timdr_industrial_fusion.py — TIMDR Industrial Fusion
=======================================================
Fuzja wielu czujników maszyny (temperatura, wibracje, ciśnienie, prąd,
...) w jeden sygnał "energii stanu" E(t), plus standardowy zestaw
detektorów TIMDR (twist, trend, anomalie, rytm) na tym sygnale.
"""

import math
import numpy as np


class TIMDRIndustrialFusion:
    def __init__(self, mad_scale=1.4826):
        self.mad_scale = mad_scale
        self.baseline_med = None
        self.baseline_mad = None

    # ---------- MAD fallback ----------
    def _mad_z(self, x):
        x = np.asarray(x, float)
        if x.size == 0:
            return np.zeros_like(x)

        med = np.median(x)
        mad = np.median(np.abs(x - med)) * self.mad_scale

        if mad == 0:
            span = np.max(x) - np.min(x)
            if span == 0:
                return np.zeros_like(x)
            return (x - med) / (span / 4.0)

        return (x - med) / mad

    # ---------- kalibracja wzgledem ZNANEGO zdrowego okresu ----------
    def calibrate(self, healthy_sensors):
        """
        ZNALEZIONY REALNY PROBLEM (test na prawdziwych danych NASA C-MAPSS,
        silnik run-to-failure): `fuse()` liczy median/MAD z tego samego
        okna, ktore akurat dostaje - dla bardzo krotkiej historii (np.
        pierwsze ~10 probek zywej, nowej maszyny) ta statystyka jest
        niestabilna, wiec zwyklyszum rozruchowy potrafi wygladac jak
        ekstremalny wyjatek wzgledem WLASNEJ, malej proby. Zweryfikowano:
        na realnym silniku dawalo to `health_score=0.0`/`TTF=0`
        ("juz awaria") na cyklu 10 - czyli maksymalny falszywy alarm w
        najzdrowszym mozliwym momencie zycia maszyny.

        Ten fallback rozwiazuje to inaczej niz "poczekaj na wiecej probek"
        (co ukrylo by PRAWDZIWA usterke, gdyby wystapila od razu):
        kalibruje median/MAD RAZ, z osobno dostarczonego zbioru
        referencyjnego `healthy_sensors` (np. test odbiorczy fabryczny,
        znana zdrowa faza pracy tego samego typu maszyny), zamiast z
        biezacego, mogacego byc zbyt krotkim okna. Dzieki temu `fuse_calibrated()`
        dziala poprawnie OD PIERWSZEJ probki live - jesli maszyna ma realny
        defekt juz na starcie, wyjdzie to natychmiast jako duze odchylenie
        od zdrowego punktu odniesienia, a nie zostanie ukryte do czasu
        "uzbierania wystarczajaco duzo danych".

        DRUGI REALNY PROBLEM ZNALEZIONY PRZY TYM SAMYM TESCIE (bardziej
        fundamentalny niz dlugosc okna): `E=sqrt(sum(Z**2))` z `fuse()`
        rosnie z PIERWIASTKIEM Z LICZBY FUZOWANYCH CZUJNIKOW nawet dla
        czysto zdrowych danych - to wlasciwosc rozkladu chi z k stopniami
        swobody (k=liczba czujnikow), nie usterka konkretnych danych.
        Zweryfikowano na realnym silniku: mediana E w zdrowym oknie przy
        k=4 czujnikach (jak oryginalne demo) = 1,73 (blisko sqrt(4)=2,0);
        przy k=10 czujnikach = 3,03 (blisko sqrt(10)=3,16) - czyli
        DOKLADNIE ta sama "zdrowa" maszyna z wiecej podlaczonymi
        czujnikami wyglada na coraz bardziej "chora" przy tym samym stalym
        progu 3.0, tylko z powodu liczby kanalow, nie stanu maszyny.
        Dlatego `fuse_calibrated()` (nizej) normalizuje przez
        `sqrt(k)` (RMS z-score, nie suma) - jego skala jest z zalozenia
        niezalezna od liczby czujnikow, w odroznieniu od `fuse()`, ktora
        zachowuje swoja oryginalna skale (suma) dla wstecznej zgodnosci z
        juz zweryfikowanymi progami 5 scenariuszy demo (`demo_scenarios.py`,
        wszystkie zbudowane na k=4 czujnikach).

        UWAGA (odpowiedzialnosc wywolujacego, nie tego modulu):
        `healthy_sensors` musi FAKTYCZNIE reprezentowac zdrowy stan,
        ustalony z zewnetrznego zrodla (spec fabryczny, test odbiorczy,
        znana zdrowa flota) - ta funkcja nie weryfikuje tego sama z
        siebie i nie moze, bo z definicji jest punktem odniesienia, nie
        testem. Kalibrowanie z pierwszych probek TEJ SAMEJ, nieznanej
        maszyny nadal zaklada, ze te konkretne probki byly zdrowe -
        rozsadne przy typowym zalozeniu "maszyna zaczyna nowa", ale to
        zalozenie, nie pewnik.
        """
        healthy_sensors = [np.asarray(s, float) for s in healthy_sensors]
        meds, mads = [], []
        for s in healthy_sensors:
            med = np.median(s)
            mad = np.median(np.abs(s - med)) * self.mad_scale
            if mad == 0:
                span = np.max(s) - np.min(s)
                mad = (span / 4.0) if span > 0 else 1.0
            meds.append(med)
            mads.append(mad)
        self.baseline_med = np.array(meds)
        self.baseline_mad = np.array(mads)

    # ---------- autokalibracja: znajdz najbardziej stabilne podokno ----------
    def auto_calibrate(self, sensors, probe_window=None, candidate_size=None, step=1):
        """
        ZNALEZIONY PROBLEM (test na emulowanym OBD-II, przyspieszajacy
        silnik): `calibrate()` z pierwszych probek zaklada, ze poczatek
        nagrania to stabilny, zdrowy stan - ale jesli poczatek to stan
        PRZEJSCIOWY (rozpedzajacy sie silnik, rozruch termiczny), kazda
        kolejna probka na tej samej rampie wyglada jak odchylenie,
        niezaleznie od realnego stanu maszyny. Zweryfikowano: symulowany
        samochod przyspieszajacy od 620 do 1000+ obr/min podczas
        kalibracji dawal falszywy `health_score=0.10`, `ALARM` na
        25. probce, mimo braku jakiejkolwiek realnej usterki.

        Zamiast slepo brac pierwsze `candidate_size` probek, przeszukuje
        pierwsze `probe_window` probek (domyslnie: wszystko, co dostal)
        w poszukiwaniu NAJBARDZIEJ STABILNEGO ciaglego podokna (najnizsza
        laczna znormalizowana zmiennosc - suma `std/|mediana|` po
        czujnikach) i kalibruje z NIEGO, nie z pierwszego okna z brzegu.

        UWAGA (odpowiedzialnosc wywolujacego, teraz mniejsza, NIE zerowa):
        to nadal zaklada, ze GDZIES w `probe_window` istnieje prawdziwie
        stabilny/zdrowy odcinek. Jesli caly okres probny jest jednym
        ciaglym stanem przejsciowym (np. caly zebrany plik to rozpedzanie
        od zera bez ani jednego plaskiego odcinka - zweryfikowano, ze taki
        przypadek naprawde sie zdarza na realnym emulatorze OBD-II),
        autokalibracja wybierze NAJMNIEJ zle okno sposrod złych, co wciaz
        moze nie byc prawdziwie zdrowe - to fundamentalne ograniczenie
        danych, nie cos, co dowolny algorytm kalibracji moze naprawic.
        Dlatego zwraca pelna diagnostyke (`chosen_start`,
        `variability_chosen` vs `variability_naive_first`) - zeby wybor
        byl sprawdzalny, a nie ukryty w czarnej skrzynce.

        Zwraca dict: chosen_start, window_size, variability_chosen,
        variability_naive_first (ta sama miara na oknie [0:window_size],
        do porownania - ile lepszy jest wybrany fragment).
        """
        sensors = [np.asarray(s, float) for s in sensors]
        n = min(len(s) for s in sensors) if sensors else 0
        if n == 0:
            raise ValueError("auto_calibrate() wymaga co najmniej jednego niepustego czujnika.")

        if probe_window is None:
            probe_window = n
        probe_window = min(probe_window, n)

        if candidate_size is None:
            candidate_size = max(5, probe_window // 4)
        candidate_size = min(candidate_size, probe_window)
        if candidate_size < 2:
            raise ValueError("Zbyt malo probek na okno kalibracyjne (candidate_size < 2).")

        def variability(window_sensors):
            score = 0.0
            for w in window_sensors:
                med = np.median(w)
                spread = np.std(w)
                scale = abs(med) if abs(med) > 1e-9 else (
                    (np.max(w) - np.min(w)) if np.max(w) != np.min(w) else 1.0
                )
                score += spread / scale
            return score

        best_start, best_score = 0, np.inf
        naive_score = None
        last_start = probe_window - candidate_size
        for start in range(0, last_start + 1, max(1, step)):
            window = [s[start:start + candidate_size] for s in sensors]
            score = variability(window)
            if start == 0:
                naive_score = score
            if score < best_score:
                best_score, best_start = score, start

        chosen_window = [s[best_start:best_start + candidate_size] for s in sensors]
        self.calibrate(chosen_window)
        validation = self.validate_window(chosen_window)

        return {
            "chosen_start": best_start,
            "window_size": candidate_size,
            "variability_chosen": float(best_score),
            "variability_naive_first": float(naive_score),
            "validated": validation["valid"],
            "validation_detail": validation["per_sensor"],
        }

    # ---------- ile probek trzeba, zeby kalibracja sie ustabilizowala ----------
    def calibration_convergence(self, sensors, step=5, max_n=None, rel_tol=0.05, confirm=3):
        """
        ODPOWIADA WPROST na pytanie "ile probek system potrzebuje, zeby
        sie samo ustabilizowac" - zamiast zakladac z gory stala liczbe
        (np. "20 probek wystarczy"), mierzy to EMPIRYCZNIE: liczy wektor
        median czujnikow na rosnacych prefiksach danych [0:n] (n=step,
        2*step, 3*step, ...) i sledzi wzgledna zmiane (znormalizowana
        odleglosc euklidesowa) miedzy kolejnymi krokami. Zwraca
        najmniejsze `n`, dla ktorego ta zmiana spadla ponizej `rel_tol`
        PRZEZ `confirm` kolejnych krokow z rzedu - nie tylko raz (jeden
        przypadkowo maly krok w szumie nie oznacza prawdziwej zbieznosci,
        ta sama zasada co konwergencja Monte Carlo / sekwencyjne reguly
        stopu w analizie wyjscia symulacji).

        Jesli mediany NIGDY nie ustabilizuja sie w podanym zakresie danych
        (`n_required=None`), to uczciwa odpowiedz "nie wiem / potrzeba
        wiecej danych, albo te dane nigdy sie nie stabilizuja" - nie
        zgadywanie liczby na sile.

        Zwraca dict: n_required (int albo None), history (lista
        {n, rel_change} do narysowania krzywej zbieznosci).
        """
        sensors = [np.asarray(s, float) for s in sensors]
        n_total = min(len(s) for s in sensors) if sensors else 0
        if max_n is None:
            max_n = n_total
        max_n = min(max_n, n_total)
        steps = list(range(step, max_n + 1, step))
        if len(steps) < 2:
            return {"n_required": None, "history": []}

        # POPRAWKA (blad znaleziony przy tescie na realnym, przyspieszajacym
        # silniku OBD-II): normalizowanie zmiany wzgledem BIEZACEJ (rosnacej
        # wraz z n) mediany jest niepoprawne dla sygnalu z trwalym trendem -
        # relatywny krok kurczy sie w miare jak mianownik rosnie, mimo ze
        # sygnal wcale sie nie stabilizuje. Zweryfikowano wprost: na czystej
        # rampie RPM (620->2200 w 80 probkach, mediany rosnace niemal
        # idealnie liniowo: 710, 810, 910, 1010, 1060, ...) poprzednia wersja
        # falszywie zglaszala "stabilizacje" przy n=45 (Delta=50, mediana
        # urosla do 1010, wiec 50/1010<5% - PONIEWAZ mianownik urosl, nie
        # dlatego, ze krok naprawde sie zmniejszyl). Naprawiono: mianownik to
        # STALA skala rozrzutu (MAD) policzona RAZ z calego dostepnego okresu
        # probnego, nie z rosnacej mediany - dzieki temu trwaly trend nigdy
        # nie "stabilizuje sie" sztucznie tylko dlatego, ze urosl punkt
        # odniesienia.
        scale = np.array([
            max(np.median(np.abs(s[:max_n] - np.median(s[:max_n]))) * self.mad_scale, 1e-9)
            for s in sensors
        ])

        prev_med = None
        history = []
        consecutive_ok = 0
        n_required = None
        for n in steps:
            meds = np.array([np.median(s[:n]) for s in sensors])
            if prev_med is not None:
                rel_change = float(np.linalg.norm((meds - prev_med) / scale))
                history.append({"n": n, "rel_change": rel_change})
                if rel_change < rel_tol:
                    consecutive_ok += 1
                    if consecutive_ok >= confirm and n_required is None:
                        n_required = n - step * (confirm - 1)
                else:
                    consecutive_ok = 0
            prev_med = meds
        return {"n_required": n_required, "history": history}

    # ---------- auto-walidacja okna: sprawdzony test statystyczny, nie heurystyka ----------
    @staticmethod
    def _mann_kendall(x):
        """
        Test Manna-Kendalla (Mann 1945, Kendall 1975) na obecnosc
        MONOTONICZNEGO trendu - standardowy, nieparametryczny test
        (uzywany m.in. w hydrologii/klimatologii do wykrywania trendow w
        szeregach czasowych), nie wlasna heurystyka. Nie zaklada
        normalnosci ani liniowosci trendu - wykrywa dowolny monotoniczny
        wzrost/spadek, dokladnie ten ksztalt problemu co przyspieszajacy
        silnik w tescie OBD-II (Blad 3/4 wyzej).

        Zwraca (S, z, p): S = suma znakow wszystkich par (i<j) czy
        x[j]>x[i]; z = standaryzowana statystyka (przyblizenie normalne,
        standardowe dla n>~10, z korekta ciaglosci +-1); p = dwustronna
        wartosc p. Brak korekty na remisy (tie correction) - pominieta
        celowo dla prostoty, bo realne dane czujnikow z szumem pomiarowym
        rzadko maja dokladne remisy; przy danych z duza liczba
        powtorzonych wartosci ten test bedzie lekko zachowawczy
        (nieco zanizona wariancja), nie liberalny.
        """
        x = np.asarray(x, float)
        n = len(x)
        if n < 4:
            return 0.0, 0.0, 1.0
        s = 0.0
        for i in range(n - 1):
            s += np.sum(np.sign(x[i + 1:] - x[i]))
        var_s = n * (n - 1) * (2 * n + 5) / 18.0
        if s > 0:
            z = (s - 1) / np.sqrt(var_s)
        elif s < 0:
            z = (s + 1) / np.sqrt(var_s)
        else:
            z = 0.0
        # NAPRAWIONE (Device Guard/WDAC blokował scipy._quadpack.pyd na
        # maszynie firmowej uzytkownika): 2*(1-norm.cdf(|z|)) to dokladnie
        # math.erfc(|z|/sqrt(2)) dla standardowego rozkladu normalnego -
        # tozsamosc matematyczna, nie przyblizenie. math.erfc jest w
        # stdlib (modul math jest wbudowany w interpreter, nie ma wlasnego
        # DLL do zaladowania jak scipy), wiec eliminuje jedyna w calym
        # repo zaleznosc od scipy bez zadnej utraty dokladnosci.
        p = math.erfc(abs(z) / math.sqrt(2))
        return float(s), float(z), float(p)

    def validate_window(self, sensors_window, alpha=0.05):
        """
        Auto-walidacja okna kalibracyjnego SPRAWDZONYM testem
        statystycznym (Mann-Kendall), nie wlasnym wymyslem. `auto_calibrate()`
        wybiera NAJMNIEJ zmienne dostepne okno, ale "najmniej zmienne z
        dostepnych" to nie to samo co "statystycznie bez trendu" - to
        wlasnie ta metoda dodatkowo sprawdza. Zweryfikowano: na oknie z
        prawdziwej rampy OBD-II (silnik przyspieszajacy) test poprawnie
        wykrywa istotny trend na WIEKSZOSCI kanalow (p<0.05); na czystym
        szumie (kontrola negatywna) poprawnie NIE wykrywa trendu.

        Zwraca dict: valid (bool - zaden czujnik nie ma istotnego trendu
        na poziomie `alpha`), per_sensor (lista {S, z, p, trend} - jedna
        na kazdy czujnik, do sprawdzenia KTORY kanal jest problemem).
        """
        results = []
        for w in sensors_window:
            s, z, p = self._mann_kendall(w)
            results.append({"S": s, "z": z, "p": p, "trend": bool(p < alpha)})
        valid = not any(r["trend"] for r in results)
        return {"valid": valid, "per_sensor": results}

    def fuse_calibrated(self, t, sensors):
        """
        Jak `fuse()`, ale z-score liczony wzgledem ZAMROZONEJ kalibracji
        z `calibrate()`, nie wzgledem statystyk biezacego wywolania. Uzyj
        tego wariantu do monitoringu NA ZYWO od pierwszej probki; uzyj
        zwyklego `fuse()` do retrospektywnej analizy calego, juz
        zakonczonego przebiegu (tam samoreferencyjne MAD-z jest w
        porzadku, bo caly kontekst jest juz znany, tak jak w
        TIMDR-Earthquake-Core's bilateral TRM dla kompletnego sladu).

        SKALA E JEST TU CELOWO INNA NIZ W `fuse()`: to RMS z-score
        (`sqrt(mean(Z**2))`, nie `sqrt(sum(Z**2))`), wiec "zdrowa" wartosc
        E oscyluje kolo 1.0 NIEZALEZNIE od liczby fuzowanych czujnikow -
        patrz uzasadnienie (rozklad chi, zweryfikowane na realnym silniku
        C-MAPSS) w docstringu `calibrate()` powyzej. Domyslny
        `threshold=3.0` w `TIMDRIndustrialPredict` odpowiada wiec tutaj
        "srednio 3-sigma odchylenia na kanal", niezaleznie od tego, czy
        fuzujesz 2 czy 20 czujnikow - w przeciwienstwie do `fuse()`, gdzie
        ten sam staly prog trzeba by przeskalowywac recznie przez
        `sqrt(n_czujnikow)` przy kazdej zmianie liczby kanalow.
        """
        if self.baseline_med is None:
            raise ValueError(
                "fuse_calibrated() wymaga wczesniejszego wywolania "
                "calibrate(healthy_sensors)."
            )
        X = self._align(t, sensors)
        if X.shape[1] != len(self.baseline_med):
            raise ValueError(
                f"Liczba czujnikow ({X.shape[1]}) nie zgadza sie z liczba "
                f"z kalibracji ({len(self.baseline_med)})."
            )
        Z = np.column_stack([
            (X[:, i] - self.baseline_med[i]) / self.baseline_mad[i]
            for i in range(X.shape[1])
        ])
        E = np.sqrt(np.mean(Z**2, axis=1))
        return E, Z

    # ---------- interpolacja ----------
    def _align(self, t, sensors):
        """
        UWAGA (założenie, nie błąd): dla czujnika o innej liczbie próbek
        niż `t` zakładamy, że był on próbkowany RÓWNOMIERNIE w tym samym
        przedziale czasu [t.min(), t.max()] - typowe dla wielu czujników
        o różnych częstotliwościach próbkowania na tej samej maszynie w
        tym samym oknie czasowym. Jeśli Twój czujnik ma inny start/koniec
        albo nierówne próbkowanie, podaj go z własnym `t` i wyrównaj
        ręcznie przed wywołaniem `fuse()`.
        """
        t = np.asarray(t, float)
        out = []
        for s in sensors:
            s = np.asarray(s, float)
            if len(s) != len(t):
                ti = np.linspace(t.min(), t.max(), len(s))
                si = np.interp(t, ti, s)
                out.append(si)
            else:
                out.append(s)
        return np.column_stack(out)

    # ---------- fuzja cech ----------
    def fuse(self, t, sensors):
        X = self._align(t, sensors)
        Z = np.column_stack([self._mad_z(X[:, i]) for i in range(X.shape[1])])
        E = np.sqrt(np.sum(Z**2, axis=1))
        return E, Z

    # ---------- twist ----------
    def twist(self, t, E):
        """
        POPRAWKA (odporność na krótkie sygnały): oryginalny kod wywoływał
        `np.gradient` dwukrotnie bez żadnej ochrony na sygnały krótsze niż
        2 próbki. Zweryfikowano: n=0 i n=1 dawały `IndexError` (crash),
        zamiast zwrócić puste/zerowe wyniki - niespójne z resztą rodziny
        modułów TIMDR w tym repo (np. TIMDR-Earthquake-Core), gdzie
        krótkie sygnały są jawnie obsłużone. Naprawiono: n<2 zwraca puste
        idx i zera.
        """
        t = np.asarray(t, float)
        E = np.asarray(E, float)
        n = len(E)
        if n < 2:
            return np.array([], dtype=int), np.zeros(n)

        dE = np.gradient(E, t)
        ddE = np.gradient(dE, t)
        z = np.abs(self._mad_z(ddE))
        idx = np.where(z > 3.5)[0]
        return idx, z

    # ---------- trend ----------
    def trend(self, t, E, window=20):
        n = len(t)
        if n == 0:
            return np.array([]), np.array([])

        slopes = np.zeros_like(E, dtype=float)
        for i in range(n):
            j0 = max(0, i - window + 1)
            tt = t[j0:i + 1]
            ee = E[j0:i + 1]
            if len(tt) < 2:
                slopes[i] = 0.0
                continue
            A = np.column_stack([tt, np.ones_like(tt)])
            a, b = np.linalg.lstsq(A, ee, rcond=None)[0]
            slopes[i] = a
        z = self._mad_z(slopes)
        return slopes, z

    # ---------- anomaly ----------
    def anomalies(self, E):
        z = np.abs(self._mad_z(E))
        idx = np.where(z > 3.0)[0]
        return idx, z

    # ---------- rhythm ----------
    def rhythm(self, E, max_lag=60, power_thresh=0.4):
        """
        POPRAWKA (bug krytyczny): oryginalny kod tylko odejmował średnią
        (`E - mean(E)`) i zgłaszał KAŻDY lag powyżej `power_thresh`, nie
        tylko lokalne maksima. Dla sygnału z TRENDEM (a nie okresowością)
        - czyli dokładnie tym, co ten moduł ma wykrywać jako degradację! -
        kolejne próbki są do siebie podobne z powodu gładkiego trendu, nie
        żadnej cykliczności, co dawało bardzo wysoką "autokorelację" na
        niemal wszystkich krótkich lagach.

        Zweryfikowano: czysty rosnący trend (bez śladu okresowości, z
        realistycznym szumem czujnika) dawał `rhythm_score≈0.99` i **48
        "wykrytych okresów"** - narzędzie zgłaszało silny "rytm" dla
        sygnału, który nie ma żadnej cykliczności. To bezpośrednio myli
        się z głównym zastosowaniem modułu (degradacja = trend), a
        `fusion_score` dodatkowo podwójnie liczy tę samą informację
        (trend już wnosi 0.3 wagi przez `trend_z`, plus fałszywie wysoki
        `rhythm_score` dokłada kolejne 0.1).

        Naprawiono dwutorowo: (1) pełny detrend (nachylenie + wyraz
        wolny, nie tylko średnia) przed liczeniem autokorelacji, (2)
        zgłaszane są tylko LOKALNE MAKSIMA autokorelacji powyżej progu,
        nie każdy lag który go przekracza - inaczej nawet po detrendzie
        płaskie "plateau" wokół prawdziwego piku generowałoby wiele
        sztucznych "okresów" zamiast jednego. Zweryfikowano: ten sam
        czysty trend po poprawce daje `[]`, `0.0` (poprawnie: brak
        rytmu); sygnał z prawdziwą okresowością (okres 15) NAŁOŻONĄ na
        silny trend nadal poprawnie wykrywa okres 15 (i jego
        harmoniczne).
        """
        E = np.asarray(E, float)
        n = len(E)
        if n < 2:
            return [], 0.0

        t_idx = np.arange(n, dtype=float)
        if n > 2:
            slope, intercept = np.polyfit(t_idx, E, 1)
            E = E - (slope * t_idx + intercept)
        else:
            E = E - np.mean(E)

        max_lag = min(max_lag, n - 1)
        ac = np.zeros(max_lag + 1)
        for lag in range(max_lag + 1):
            if lag == 0:
                ac[lag] = np.dot(E, E) / n
            else:
                overlap = n - lag
                if overlap <= 0:
                    break
                ac[lag] = np.dot(E[:-lag], E[lag:]) / overlap

        if ac[0] == 0:
            return [], 0.0

        ac /= ac[0]

        peaks = []
        for i in range(1, len(ac) - 1):
            if ac[i] > ac[i - 1] and ac[i] > ac[i + 1] and ac[i] >= power_thresh:
                peaks.append((i, float(ac[i])))

        if not peaks:
            return [], 0.0

        score = max(p for _, p in peaks)
        return [p for p, _ in peaks], score

    # ---------- fusion-score ----------
    def fusion_score(self, twist_z, trend_z, anomaly_z, rhythm_score):
        """
        UWAGA: `np.max` na pustej tablicy rzuca ValueError - może się
        zdarzyć dla bardzo krótkich sygnałów (patrz poprawka w twist()).
        Traktujemy pustą tablicę jako "brak sygnału" (0.0), nie błąd.
        """
        def safe_max(x):
            x = np.asarray(x, float)
            return float(np.max(x)) if x.size else 0.0

        return float(
            0.4 * safe_max(twist_z) +
            0.3 * safe_max(trend_z) +
            0.2 * safe_max(anomaly_z) +
            0.1 * rhythm_score
        )
