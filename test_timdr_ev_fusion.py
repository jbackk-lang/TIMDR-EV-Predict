import numpy as np
import pytest
from timdr_ev_fusion import TIMDREVFusion


def _healthy_and_degrading(n_healthy=150, n_degrade=100, seed=0):
    rng = np.random.default_rng(seed)
    temp = np.concatenate([rng.normal(60, 1, n_healthy), 60 + np.linspace(0, 15, n_degrade)])
    vib = np.concatenate([rng.normal(0.2, 0.02, n_healthy), 0.2 + np.linspace(0, 1.0, n_degrade)])
    pres = rng.normal(5.0, 0.1, n_healthy + n_degrade)
    curr = rng.normal(10.0, 0.3, n_healthy + n_degrade)
    t = np.arange(n_healthy + n_degrade, dtype=float)
    return t, [temp, vib, pres, curr]


# -----------------------------------------------------------
# fuse() / _align()
# -----------------------------------------------------------

def test_fuse_rozne_dlugosci_czujnikow_wyrownuje_do_wspolnej_osi():
    fusion = TIMDREVFusion()
    t = np.arange(100, dtype=float)
    s1 = np.sin(np.linspace(0, 10, 100))       # ta sama dlugosc co t
    s2 = np.sin(np.linspace(0, 10, 50))        # 2x rzadziej probkowany
    s3 = np.sin(np.linspace(0, 10, 300))       # 3x gesciej probkowany
    E, Z = fusion.fuse(t, [s1, s2, s3])
    assert E.shape == (100,)
    assert Z.shape == (100, 3)
    assert np.all(np.isfinite(E))


def test_fuse_e_nieujemne():
    t, sensors = _healthy_and_degrading()
    fusion = TIMDREVFusion()
    E, _ = fusion.fuse(t, sensors)
    assert np.all(E >= 0)


# -----------------------------------------------------------
# POPRAWKA: krotkie sygnaly nie crashuja
# -----------------------------------------------------------

@pytest.mark.parametrize("n", [0, 1])
def test_twist_krotki_sygnal_nie_crashuje(n):
    fusion = TIMDREVFusion()
    t = np.arange(n, dtype=float)
    E = np.ones(n)
    idx, z = fusion.twist(t, E)
    assert len(idx) == 0
    assert len(z) == n


@pytest.mark.parametrize("n", [0, 1])
def test_rhythm_krotki_sygnal_nie_crashuje(n):
    fusion = TIMDREVFusion()
    E = np.ones(n)
    periods, score = fusion.rhythm(E)
    assert periods == []
    assert score == 0.0


def test_fusion_score_na_pustych_tablicach_nie_crashuje():
    fusion = TIMDREVFusion()
    score = fusion.fusion_score(np.array([]), np.array([]), np.array([]), 0.0)
    assert score == 0.0


# -----------------------------------------------------------
# twist / trend / anomalies - sanity checks
# -----------------------------------------------------------

def test_twist_wykrywa_gwaltowna_zmiane():
    fusion = TIMDREVFusion()
    n = 200
    t = np.arange(n, dtype=float)
    # skokowa zmiana (np. pierwsze "uderzenie" luznego elementu) + odrobina
    # realistycznego szumu tla - na CALKOWICIE bezszumnym skoku fallback
    # MAD=0 (span/4) daje mniej dyskryminujacy wynik, co nie jest bugiem,
    # tylko wlasciwoscia fallbacku dla zdegenerowanych (bezszumnych) danych,
    # ktore w praktyce nie wystepuja.
    E = np.full(n, 1.0) + np.random.default_rng(0).normal(0, 0.01, n)
    E[100:] += 10.0
    idx, z = fusion.twist(t, E)
    assert len(idx) > 0
    assert any(98 <= i <= 101 for i in idx)


def test_trend_wykrywa_narastajaca_degradacje():
    fusion = TIMDREVFusion()
    n = 100
    t = np.arange(n, dtype=float)
    E = np.linspace(0, 10, n)  # staly, dodatni trend
    slopes, z = fusion.trend(t, E, window=20)
    assert slopes[-1] > 0
    assert slopes[-1] == pytest.approx(10 / (n - 1), rel=0.05)


def test_anomalies_wykrywa_odosobniony_skok():
    fusion = TIMDREVFusion()
    rng = np.random.default_rng(0)
    E = np.abs(rng.normal(0, 0.1, 100))
    E[50] = 5.0
    idx, z = fusion.anomalies(E)
    assert 50 in idx


# -----------------------------------------------------------
# rhythm() - overlap-corrected autocorr (ta sama poprawka co w TIMDRRhythm)
# -----------------------------------------------------------

def test_rhythm_nie_myli_trendu_z_periodycznoscia():
    """Regresja dla błędu: czysty rosnący trend (zero periodyczności) z
    realistycznym szumem czujnika dawał przed poprawką score~0.99 i 48
    'wykrytych okresów'. Po poprawce (pełny detrend + tylko lokalne
    maksima) powinien dawać brak wykrytego rytmu."""
    fusion = TIMDREVFusion()
    rng = np.random.default_rng(0)
    n = 200
    E = np.linspace(0, 10, n) + rng.normal(0, 0.15, n)
    periods, score = fusion.rhythm(E, max_lag=60, power_thresh=0.4)
    assert periods == []
    assert score == 0.0


def test_rhythm_wykrywa_prawdziwa_periodycznosc_mimo_nalozonego_trendu():
    fusion = TIMDREVFusion()
    t = np.arange(300, dtype=float)
    E = 5 * np.sin(2 * np.pi * t / 15) + np.linspace(0, 10, 300)
    periods, score = fusion.rhythm(E, max_lag=60, power_thresh=0.4)
    assert 15 in periods


def test_rhythm_wykrywa_okresowosc_bez_sztucznego_spadku_mocy():
    fusion = TIMDREVFusion()
    n, period = 200, 20
    E = np.sin(2 * np.pi * np.arange(n) / period)
    periods, score = fusion.rhythm(E, max_lag=100, power_thresh=0.0)
    # z korekta overlap, moc przy lag=20,40,...,100 powinna byc ~1.0, nie malejaca
    # (pelny detrend wprowadza minimalna, oczekiwana odchylke rzedu 1e-3
    # przy niecalkowitej liczbie okresow w oknie - to nie jest regresja
    # testowanego bugu, ktory dawal spadek rzedu dziesiatek procent)
    assert score == pytest.approx(1.0, abs=2e-3)


# -----------------------------------------------------------
# fusion_score - kombinacja wynikow
# -----------------------------------------------------------

def test_fusion_score_rosnie_z_powaznoscia_sygnalow():
    fusion = TIMDREVFusion()
    low = fusion.fusion_score(np.array([0.1]), np.array([0.1]), np.array([0.1]), 0.1)
    high = fusion.fusion_score(np.array([10.0]), np.array([10.0]), np.array([10.0]), 1.0)
    assert high > low


# -----------------------------------------------------------
# calibrate() / fuse_calibrated() - zamrozony punkt odniesienia
# zamiast samoreferencyjnych statystyk krotkiego okna
# -----------------------------------------------------------

def test_fuse_calibrated_bez_calibrate_rzuca_blad():
    fusion = TIMDREVFusion()
    with pytest.raises(ValueError):
        fusion.fuse_calibrated(np.arange(5, dtype=float), [np.ones(5)])


def test_fuse_calibrated_niezgodna_liczba_czujnikow_rzuca_blad():
    fusion = TIMDREVFusion()
    fusion.calibrate([np.random.default_rng(0).normal(0, 1, 50)])
    with pytest.raises(ValueError):
        fusion.fuse_calibrated(np.arange(5, dtype=float), [np.ones(5), np.ones(5)])


def test_fuse_calibrated_zdrowy_rozruch_nie_daje_falszywego_alarmu():
    """
    Regresja dla realnego problemu znalezionego na NASA C-MAPSS: z
    samoreferencyjnym fuse() bardzo krotka historia (pierwsze probki
    zywej maszyny) potrafila wygladac jak ekstremalny wyjatek wzgledem
    WLASNEJ, malej proby, dajac falszywy alarm krytyczny od pierwszej
    probki. Przy kalibracji z osobnego zdrowego zbioru referencyjnego,
    kilka pierwszych *zdrowych* probek live nie powinno dawac wysokiego E.
    """
    rng = np.random.default_rng(1)
    healthy_ref = [rng.normal(60, 1, 200), rng.normal(0.2, 0.02, 200)]
    fusion = TIMDREVFusion()
    fusion.calibrate(healthy_ref)

    # "live" dane - tylko 5 pierwszych probek nowej maszyny, ZDROWEJ
    t_live = np.arange(5, dtype=float)
    live_zdrowe = [rng.normal(60, 1, 5), rng.normal(0.2, 0.02, 5)]
    E, _ = fusion.fuse_calibrated(t_live, live_zdrowe)
    assert np.all(E < 3.0), f"falszywy alarm na zdrowym rozruchu: E={E}"


def test_fuse_calibrated_realna_usterka_od_pierwszej_probki_wychodzi_od_razu():
    """
    Odwrotna strona tej samej poprawki: jesli maszyna ma REALNY defekt
    JUZ na starcie, kalibrowana wersja MUSI to zlapac natychmiast (nie
    dopiero po "uzbieraniu wystarczajaco danych") - to byla explicite
    prosba: "jesli bedzie blad/awaria (...) wyjdzie od razu".
    """
    rng = np.random.default_rng(2)
    healthy_ref = [rng.normal(60, 1, 200), rng.normal(0.2, 0.02, 200)]
    fusion = TIMDREVFusion()
    fusion.calibrate(healthy_ref)

    # "live" - pierwsza probka juz z powaznym defektem (temperatura +20 od normy)
    t_live = np.array([0.0])
    live_wadliwe = [np.array([80.0]), np.array([0.2])]
    E, _ = fusion.fuse_calibrated(t_live, live_wadliwe)
    assert E[0] > 3.0, f"realna usterka od pierwszej probki NIE zostala zlapana: E={E}"


def test_fuse_calibrated_skala_niezalezna_od_liczby_czujnikow():
    """
    Regresja dla realnego bledu znalezionego na NASA C-MAPSS: `fuse()`
    (suma kwadratow) daje "zdrowa" wartosc E rosnaca jak sqrt(k) z liczba
    czujnikow k - przy stalym progu 3.0 wiecej podlaczonych czujnikow
    oznacza wiecej falszywych alarmow na tej samej, fizycznie zdrowej
    maszynie. `fuse_calibrated()` (RMS) powinno dawac podobna skale E
    niezaleznie od tego, czy fuzujemy 3 czy 12 zdrowych kanalow.
    """
    rng = np.random.default_rng(3)
    E_medians = []
    for k in (3, 6, 12):
        healthy = [rng.normal(0, 1, 300) for _ in range(k)]
        live = [rng.normal(0, 1, 50) for _ in range(k)]
        fusion = TIMDREVFusion()
        fusion.calibrate(healthy)
        E, _ = fusion.fuse_calibrated(np.arange(50, dtype=float), live)
        E_medians.append(np.median(E))
    assert max(E_medians) / min(E_medians) < 1.5, (
        f"skala E zalezy od liczby czujnikow: {E_medians}"
    )


# -----------------------------------------------------------
# auto_calibrate() - szukanie najbardziej stabilnego podokna
# -----------------------------------------------------------

def test_auto_calibrate_omija_przejsciowy_rozruch():
    """
    Regresja dla realnego problemu znalezionego na emulowanym OBD-II:
    naiwna kalibracja z pierwszych probek podczas rozruchu/rampy daje
    zly punkt odniesienia. Sygnal: 30 probek rampy (rosnacej), potem
    100 probek genuinie stabilnych, potem 50 probek degradacji.
    auto_calibrate() powinno wybrac start w stabilnej czesci (>=30),
    nie w rampie (start<30).
    """
    rng = np.random.default_rng(0)
    ramp = 50 + np.linspace(0, 40, 30) + rng.normal(0, 0.5, 30)
    stable = 90 + rng.normal(0, 0.5, 100)
    degrade = 90 + np.linspace(0, 30, 50) + rng.normal(0, 0.5, 50)
    sensor1 = np.concatenate([ramp, stable, degrade])

    ramp2 = 10 + np.linspace(0, 5, 30) + rng.normal(0, 0.1, 30)
    stable2 = 15 + rng.normal(0, 0.1, 100)
    degrade2 = 15 + np.linspace(0, 8, 50) + rng.normal(0, 0.1, 50)
    sensor2 = np.concatenate([ramp2, stable2, degrade2])

    fusion = TIMDREVFusion()
    info = fusion.auto_calibrate([sensor1[:130], sensor2[:130]], candidate_size=40)

    assert info["chosen_start"] >= 25, f"autokalibracja wybrala okno w rampie: {info}"
    assert info["variability_chosen"] < info["variability_naive_first"], (
        f"wybrane okno nie jest stabilniejsze niz naiwne pierwsze: {info}"
    )


def test_auto_calibrate_stabilny_rozruch_zgadza_sie_z_pierwszym_oknem():
    """Jesli caly sygnal jest jednorodnie stabilny (czysty szum, bez
    zadnego przejsciowego stanu do omijania), kazde okno jest mniej
    wiecej rownie dobre - wybrane okno NIE powinno byc wyraznie GORSZE
    niz naiwne pierwsze (to odroznia ten przypadek od testu powyzej,
    gdzie naiwne pierwsze okno jest KONKRETNIE zle, bo lapie rampe)."""
    rng = np.random.default_rng(1)
    stable = 50 + rng.normal(0, 0.3, 200)
    fusion = TIMDREVFusion()
    info = fusion.auto_calibrate([stable], candidate_size=40)
    assert info["variability_chosen"] <= info["variability_naive_first"] * 1.5


def test_auto_calibrate_pusty_czujnik_rzuca_blad():
    fusion = TIMDREVFusion()
    with pytest.raises(ValueError):
        fusion.auto_calibrate([])


# -----------------------------------------------------------
# _mann_kendall() / validate_window() - sprawdzony test statystyczny
# -----------------------------------------------------------

def test_mann_kendall_wykrywa_prawdziwy_trend():
    """Kontrola pozytywna: silny monotoniczny trend powinien dac bardzo
    mala wartosc p."""
    fusion = TIMDREVFusion()
    x = np.linspace(0, 10, 50) + np.random.default_rng(0).normal(0, 0.1, 50)
    s, z, p = fusion._mann_kendall(x)
    assert s > 0
    assert p < 0.001


def test_mann_kendall_nie_wykrywa_trendu_w_czystym_szumie():
    """Kontrola negatywna: czysty szum bialy (bez trendu) NIE powinien
    dawac istotnego statystycznie wyniku."""
    fusion = TIMDREVFusion()
    rng = np.random.default_rng(0)
    n_significant = 0
    trials = 50
    for i in range(trials):
        x = rng.normal(0, 1, 50)
        _, _, p = fusion._mann_kendall(x)
        if p < 0.05:
            n_significant += 1
    # oczekiwany odsetek falszywie pozytywnych ~5% (alpha=0.05) - z
    # marginesem na losowosc testujemy, ze nie jest drastycznie zawyzony
    assert n_significant / trials < 0.20, f"zbyt duzo falszywych trendow: {n_significant}/{trials}"


def test_mann_kendall_krotki_sygnal_nie_crashuje():
    fusion = TIMDREVFusion()
    for n in (0, 1, 2, 3):
        s, z, p = fusion._mann_kendall(np.arange(n, dtype=float))
        assert p == 1.0


def test_validate_window_odrzuca_realna_rampe_obd():
    """Regresja na realnym problemie: rampujacy sygnal (jak przyspieszajacy
    silnik OBD-II) powinien zostac odrzucony jako niewazne okno kalibracyjne."""
    fusion = TIMDREVFusion()
    rpm_ramp = np.linspace(620, 1000, 20) + np.random.default_rng(1).normal(0, 5, 20)
    result = fusion.validate_window([rpm_ramp])
    assert result["valid"] is False
    assert result["per_sensor"][0]["trend"] is True


def test_validate_window_akceptuje_prawdziwie_stabilne_okno():
    fusion = TIMDREVFusion()
    rng = np.random.default_rng(2)
    stable = [rng.normal(50, 1, 30), rng.normal(0.2, 0.01, 30)]
    result = fusion.validate_window(stable)
    assert result["valid"] is True


def test_auto_calibrate_zwraca_walidacje():
    """auto_calibrate() powinno teraz automatycznie walidowac wybrane okno
    i zglaszac to w wyniku, nie tylko je wybierac po zmiennosci."""
    rng = np.random.default_rng(3)
    ramp = np.linspace(0, 40, 30) + rng.normal(0, 0.3, 30)
    stable = 40 + rng.normal(0, 0.3, 60)
    sensor = np.concatenate([ramp, stable])
    fusion = TIMDREVFusion()
    info = fusion.auto_calibrate([sensor], candidate_size=20)
    assert "validated" in info
    assert info["validated"] is True  # wybrane okno powinno byc w stabilnej czesci


def test_auto_calibrate_uczciwie_zglasza_niewalidne_okno_gdy_brak_alternatywy():
    """Jesli caly probny okres to jedna ciagla rampa (jak w tescie OBD-II),
    auto_calibrate() wybierze najmniej zle okno, ale walidacja powinna
    UCZCIWIE zglosic, ze wciaz nie jest ono stacjonarne - nie udawac sukcesu."""
    rng = np.random.default_rng(4)
    pure_ramp = np.linspace(0, 100, 80) + rng.normal(0, 0.5, 80)
    fusion = TIMDREVFusion()
    info = fusion.auto_calibrate([pure_ramp], candidate_size=15)
    assert info["validated"] is False


# -----------------------------------------------------------
# calibration_convergence() - ile probek trzeba do stabilizacji
# -----------------------------------------------------------

def test_calibration_convergence_wykrywa_stabilizacje():
    rng = np.random.default_rng(5)
    stable = 50 + rng.normal(0, 1, 200)
    fusion = TIMDREVFusion()
    result = fusion.calibration_convergence([stable], step=5, rel_tol=0.1, confirm=3)
    assert result["n_required"] is not None
    assert result["n_required"] <= 100


def test_calibration_convergence_zbyt_malo_danych_zwraca_none():
    fusion = TIMDREVFusion()
    result = fusion.calibration_convergence([np.array([1.0, 2.0, 3.0])], step=5)
    assert result["n_required"] is None
    assert result["history"] == []


def test_calibration_convergence_ciagla_rampa_nigdy_sie_nie_stabilizuje():
    """Uczciwa kontrola: jesli mediana caly czas rosnie (ciagla rampa),
    nigdy nie powinno zglosic zbieznosci."""
    rng = np.random.default_rng(6)
    ramp = np.linspace(0, 500, 300) + rng.normal(0, 1, 300)
    fusion = TIMDREVFusion()
    result = fusion.calibration_convergence([ramp], step=10, rel_tol=0.02, confirm=3)
    assert result["n_required"] is None


def test_calibration_convergence_rampa_z_duzym_punktem_startowym_nie_zbiega_falszywie():
    """Regresja na realny blad znaleziony na danych OBD-II: rampa startujaca
    z DUZEGO niezerowego poziomu (jak realne RPM 620->2200+) falszywie
    'zbiegala sie' w starej wersji, bo relatywna zmiana byla liczona wzgledem
    ROSNACEJ mediany (mianownik rosl, wiec staly krok bezwzgledny wygladal
    na malejacy wzglednie). Rampa ponizej ma stala szybkosc wzrostu ~12.5
    jednostki/probke, bez zadnego splaszczenia - nie powinna nigdy
    'zbiec sie' niezaleznie od tego, jak duzy jest juz punkt startowy."""
    rng = np.random.default_rng(7)
    ramp = 620 + np.linspace(0, 1580, 80) + rng.normal(0, 2, 80)
    fusion = TIMDREVFusion()
    result = fusion.calibration_convergence([ramp], step=5, rel_tol=0.05, confirm=3)
    assert result["n_required"] is None
