"""
timdr_ev_predict.py — TIMDR EV Predict
==========================================================
Na podstawie sygnału energii stanu E(t) z TIMDREVFusion:
przewiduje Time-To-Failure (TTF) i liczy bieżący health-score.
"""

import numpy as np


class TIMDREVPredict:
    def __init__(self, mad_scale=1.4826):
        self.mad_scale = mad_scale

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

    # ---------- model degradacji ----------
    def degradation_model(self, t, E, window=60):
        """
        POPRAWKA (bug krytyczny): oryginalny kod dopasowywał regresję
        (liniową i wykładniczą) do CAŁEJ historii E(t) od t=0, zamiast do
        NIEDAWNEJ degradacji. Dla maszyny, która długo pracowała
        normalnie, a od niedawna się degraduje, to oznacza, że długi
        "płaski" (zdrowy) odcinek historii rozwadnia/zniekształca
        oszacowanie aktualnego tempa degradacji.

        Zweryfikowano: dla IDENTYCZNEJ ostatniej fazy degradacji (te same
        100 próbek E rosnących od ~0 do 15.0), ale z różną długością
        wcześniejszej zdrowej historii, przewidywany czas do awarii
        (próg=30.0) wychodził:
          - 50s zdrowej historii  -> TTF=135s
          - 800s zdrowej historii -> TTF=2254s
        16-krotna różnica dla FIZYCZNIE TEJ SAMEJ aktualnej sytuacji
        maszyny - jedyna różnica to długość historii danych, nie stan
        maszyny. To czyni predykcję bezużyteczną w praktyce: ta sama
        maszyna w tym samym stanie dawałaby zupełnie inny alarm zależnie
        od tego, jak długo zbierano dane.

        Naprawiono: regresja (oba modele) liczona jest tylko na
        OSTATNICH `window` próbkach (domyślnie 60 - dobrane tak, by przy
        typowym próbkowaniu 1 próbka/s dawało ~1 minutę "pamięci", zbliżone
        do okna `trend()` w TIMDREVFusion). Ustaw `window` na
        wartość dopasowaną do dynamiki Twojej maszyny i częstotliwości
        próbkowania - zbyt krótkie okno = wrażliwość na szum, zbyt długie
        = powrót do oryginalnego błędu.

        POPRAWKA 2 (numeryczna): oryginalny kod robił `lstsq` wprost na
        surowych wartościach `t`. Dla realnych znaczników czasu (unix
        timestamp, rząd 1.7 miliarda) kolumna `[t, 1]` ma dwie kolumny o
        DRASTYCZNIE różnej skali (~1e9 vs 1) - to klasyczny przypadek
        źle uwarunkowanej macierzy w najmniejszych kwadratach, dający
        praktycznie losowy/niestabilny wynik `a`/`b`. Zweryfikowano: ta
        sama degradacja z `t` zaczynającym się od 0 dawała TTF=42.7s, a
        z `t` przesuniętym o +1 700 000 000 (realny epoch) dawała
        TTF rzędu **10 miliardów sekund (~330 lat)** - kompletnie
        bezużyteczny wynik. Naprawiono: `t` jest centrowane (`t - t[0]`
        okna) PRZED dopasowaniem regresji; `predict_failure()` przelicza
        wynik z powrotem na tę samą skalę przy liczeniu czasu do progu.
        """
        t = np.asarray(t, float)
        E = np.asarray(E, float)

        if window is not None and len(t) > window:
            t = t[-window:]
            E = E[-window:]

        t0 = t[0] if len(t) else 0.0
        t_rel = t - t0

        # trend (liniowy LSQ)
        A = np.column_stack([t_rel, np.ones_like(t_rel)])
        a, b = np.linalg.lstsq(A, E, rcond=None)[0]

        # wykładniczy (log(E))
        Epos = np.clip(E, 1e-6, None)
        logE = np.log(Epos)
        A2 = np.column_stack([t_rel, np.ones_like(t_rel)])
        ae, be = np.linalg.lstsq(A2, logE, rcond=None)[0]

        return (a, b), (ae, be), t0

    # ---------- przewidywanie czasu do awarii ----------
    def predict_failure(self, t, E, threshold=3.0, window=60):
        """
        Zwraca (ttf, ttf_linear, ttf_exp) - sekundy od OSTATNIEJ próbki
        do momentu, w którym model przewiduje przekroczenie `threshold`.
        Dodatkowo dwa poprzednie błędy (patrz `degradation_model()`)
        oznaczały, że oryginalny kod zwracał BEZWZGLĘDNĄ współrzędną
        czasu na osi `t`, nie CZAS POZOSTAŁY - zweryfikowano wprost:
        przesunięcie całego `t` o +1000s (fizycznie ta sama sytuacja
        maszyny, inny "zegar") zmieniało zwrócone TTF też dokładnie o
        +1000s. Naprawiono przez odejmowanie punktu odniesienia "teraz"
        (`t[-1]`) w tej samej, wycentrowanej skali co dopasowanie.

        UWAGA (poprawka opisu, nie tylko kodu): oryginalny komentarz
        mówił o "wyborze stabilniejszego" modelu, ale kod bez zmian po
        prostu bierze min(ttf_linear, ttf_exp) - czyli wariant
        BARDZIEJ PESYMISTYCZNY (ostrzega wcześniej), nie "stabilniejszy"
        w żadnym mierzonym sensie (nic w kodzie nie ocenia stabilności).
        To świadoma decyzja projektowa (ostrzegaj wcześniej, nie później),
        zachowana w poprawce, ale nazwana zgodnie z tym, co faktycznie
        robi.
        """
        t = np.asarray(t, float)
        E = np.asarray(E, float)

        if len(E) == 0:
            return float("inf"), float("inf"), float("inf")

        (a, b), (ae, be), t0 = self.degradation_model(t, E, window=window)
        t_ref = t[-1] - t0  # "teraz" w tej samej wycentrowanej skali co dopasowanie
        E_ref = E[-1]

        # liniowy model: E = a*t_rel + b -> czas do progu, liczony OD OSTATNIEJ PROBKI
        if a > 0:
            ttf_linear = (threshold - b) / a - t_ref
        else:
            ttf_linear = float("inf")

        # wykładniczy model: E = exp(ae*t_rel + be)
        if ae > 0:
            ttf_exp = (np.log(threshold) - be) / ae - t_ref
        else:
            ttf_exp = float("inf")

        ttf_linear = max(ttf_linear, 0.0) if np.isfinite(ttf_linear) else ttf_linear
        ttf_exp = max(ttf_exp, 0.0) if np.isfinite(ttf_exp) else ttf_exp

        ttf = min(ttf_linear, ttf_exp)

        if E_ref >= threshold:
            return 0.0, ttf_linear, ttf_exp

        return float(ttf), float(ttf_linear), float(ttf_exp)

    # ---------- TTF unormowany czasem: opoznienie + srednia z okresu ----------
    def predict_failure_smoothed(self, t, E, threshold=3.0, window=60,
                                  smooth_window=10, min_len=5):
        """
        ZNALEZIONY PROBLEM (test na realnym silniku NASA C-MAPSS):
        pojedyncze wywolanie `predict_failure()` jest niestabilne na
        prawie plaskim/zdrowym sygnale - regresja liniowa/wykladnicza na
        szumie latwo zmienia znak i wielkosc nachylenia z probki na
        probke. Zweryfikowano wprost: ta sama, fizycznie zdrowa faza
        silnika dawala kolejno TTF = inf, 87.7, inf, 737.4, inf w
        odstepie kilkudziesieciu cykli - nie dlatego, ze stan maszyny
        sie zmienial, tylko dlatego, ze pojedynczy punktowy fit jest
        szumowrazliwy.

        Naprawiono dwoma mechanizmami naraz, tak jak poprosil uzytkownik
        ("unormowac czasem - opoznienie i srednia z jakiegos okresu"):
        - OPOZNIENIE: przed zebraniem co najmniej `min_len` probek w
          ogole nie probojemy szacowac TTF (zwracamy inf) - zbyt krotka
          historia to gwarantowany szum, nie sygnal.
        - SREDNIA Z OKRESU: zamiast jednego punktowego oszacowania,
          liczymy `predict_failure()` OSOBNO dla kazdego z ostatnich
          `smooth_window` konczacych punktow historii (rosnace okna
          konczace sie na coraz pozniejszych probkach) i zwracamy
          MEDIANE tych surowych oszacowan - mediana, nie srednia
          arytmetyczna, zeby pojedynczy `inf` (brak wykrywalnego trendu
          w danym momencie) nie psul calego wyniku tak, jak zrobiloby to
          zwykle usrednianie.

        Dodatkowo `confirmed` mowi, czy przynajmniej POLOWA ostatnich
        surowych oszacowan byla skonczona (finite) - jesli nie, wiekszosc
        "widzianych" TTF-ow to "brak trendu", wiec zwracamy `inf` zamiast
        pojedynczego niskiego wyniku, ktory moglby byc odosobnionym
        szumem (podobny mechanizm hysterezy co `thr_on`/`thr_off` w
        STA/LTA - nie ufaj pojedynczemu przekroczeniu).

        Zwraca: (ttf_smoothed, confirmed, historia_surowych_ttf).
        """
        t = np.asarray(t, float)
        E = np.asarray(E, float)
        n = len(E)

        if n < min_len:
            return float("inf"), False, []

        start = max(min_len, n - smooth_window)
        raw_ttfs = []
        for i in range(start, n + 1):
            ttf_i, _, _ = self.predict_failure(t[:i], E[:i], threshold=threshold, window=window)
            raw_ttfs.append(ttf_i)

        finite = [x for x in raw_ttfs if np.isfinite(x)]
        confirmed = len(finite) >= max(1, len(raw_ttfs) // 2)

        if not confirmed:
            return float("inf"), False, raw_ttfs

        return float(np.median(finite)), True, raw_ttfs

    # ---------- health-score ----------
    def health_score(self, E, threshold=3.0, window=20):
        """
        POPRAWKA (bug krytyczny): oryginalny kod liczył `_mad_z(E)`
        (z-score WZGLĘDEM WŁASNEJ HISTORII E) i brał `max()` po CAŁEJ
        tablicy - jeden stary, jednorazowy skok (np. chwilowe zakłócenie
        czujnika) permanentnie "zatruwał" wynik, nawet gdy maszyna od
        dawna pracuje normalnie.

        Zweryfikowano: sygnał 500 próbek, jednorazowy skok E=8.0 w
        próbce 50, reszta (450 próbek = 90% danych) w normie ->
        `health_score()` całej historii = **0.000** (permanentnie
        "krytyczny"), podczas gdy licząc tylko z ostatnich 400 próbek
        (bez starego zdarzenia) wychodzi 0.122 - realistyczny obraz
        obecnego stanu. Dodatkowy problem: oryginalna skala (z-score
        względem WŁASNEJ historii E, /5) nie miała żadnego związku z
        `threshold` używanym w `predict_failure()` - "krytyczny" wg
        health_score i "krytyczny" wg TTF mogły oznaczać zupełnie różne
        wartości E.

        Naprawiono: health_score liczy się z OSTATNICH `window` próbek
        E (domyślnie 20 - obecny stan, nie cała historia), względem TEGO
        SAMEGO `threshold`, co `predict_failure()` - spójna definicja
        "krytyczności" w obu miejscach.
        """
        E = np.asarray(E, float)
        if E.size == 0:
            return 1.0

        recent = E[-window:] if window is not None and len(E) > window else E
        current_level = float(np.median(recent))
        score = np.clip(current_level / threshold, 0.0, 1.0)
        return float(1.0 - score)
