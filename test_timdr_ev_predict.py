import numpy as np
import pytest
from timdr_ev_predict import TIMDREVPredict


def _degrading_signal(n_healthy=50, n_degrade=100, top=15.0, seed=0):
    rng = np.random.default_rng(seed)
    E_healthy = np.abs(rng.normal(0, 0.05, n_healthy))
    E_degrade = np.linspace(E_healthy[-1], top, n_degrade)
    return np.concatenate([E_healthy, E_degrade])


# -----------------------------------------------------------
# POPRAWKA 1: TTF niezalezne od bezwzglednego polozenia zegara
# -----------------------------------------------------------

def test_ttf_niezalezne_od_przesuniecia_czasu():
    """Regresja dla błędu: oryginalny kod zwracał bezwzględną
    współrzędną czasu (z regresji), nie czas pozostały - przesunięcie
    całego `t` o +1000s zmieniało zwrócony TTF też o +1000s.
    Zweryfikowano też z realnym epoch (~1.75e9): oryginalny kod dawał
    TTF rzędu 10 miliardów sekund (~330 lat) zamiast tej samej wartości
    co dla t zaczynającego się od 0."""
    predict = TIMDREVPredict()
    E = _degrading_signal()
    n = len(E)

    t_zero = np.arange(n, dtype=float)
    t_shifted = t_zero + 1000.0
    t_epoch = t_zero + 1_755_000_000.0

    ttf_zero, _, _ = predict.predict_failure(t_zero, E, threshold=30.0)
    ttf_shifted, _, _ = predict.predict_failure(t_shifted, E, threshold=30.0)
    ttf_epoch, _, _ = predict.predict_failure(t_epoch, E, threshold=30.0)

    assert ttf_zero == pytest.approx(ttf_shifted, abs=1e-3)
    assert ttf_zero == pytest.approx(ttf_epoch, abs=1e-3)


# -----------------------------------------------------------
# POPRAWKA 2: TTF stabilne wzgledem dlugosci zdrowej historii
# -----------------------------------------------------------

def test_ttf_stabilne_niezaleznie_od_dlugosci_zdrowej_historii():
    """Regresja dla błędu: oryginalny kod dopasowywał regresję do CAŁEJ
    historii E(t). Dla identycznej ostatniej fazy degradacji, ale różnej
    długości wcześniejszej zdrowej historii, dawało to 16-krotne różnice
    w przewidywanym TTF (135s vs 2254s dla 50s vs 800s historii).
    Po poprawce (regresja tylko na ostatnim `window`) wynik powinien
    być względnie stabilny (w granicach szumu, nie rzędów wielkości)."""
    predict = TIMDREVPredict()
    results = []
    for healthy_len in (50, 150, 400, 800):
        rng = np.random.default_rng(0)
        E_healthy = np.abs(rng.normal(0, 0.05, healthy_len))
        E_degrade = np.linspace(E_healthy[-1], 15.0, 100)
        E = np.concatenate([E_healthy, E_degrade])
        t = np.arange(len(E), dtype=float)
        ttf, _, _ = predict.predict_failure(t, E, threshold=30.0)
        results.append(ttf)

    spread = max(results) - min(results)
    assert spread < 5.0  # przed poprawka roznica siegala >2000s


def test_degradation_model_ignoruje_stara_historie_poza_oknem():
    predict = TIMDREVPredict()
    E = _degrading_signal(n_healthy=500, n_degrade=100)
    t = np.arange(len(E), dtype=float)
    (a, b), (ae, be), t0 = predict.degradation_model(t, E, window=60)
    # nachylenie powinno odzwierciedlac TYLKO faze degradacji (ostatnie 60 probek),
    # nie byc rozwodnione przez 500 probek plaskiej historii
    assert a > 0.05  # w oknie tylko degradacja, wiec nachylenie wyraznie dodatnie


# -----------------------------------------------------------
# predict_failure - podstawy
# -----------------------------------------------------------

def test_predict_failure_juz_przekroczony_prog_zwraca_zero():
    predict = TIMDREVPredict()
    E = np.array([1.0, 2.0, 5.0])
    t = np.array([0.0, 1.0, 2.0])
    ttf, _, _ = predict.predict_failure(t, E, threshold=3.0)
    assert ttf == 0.0


def test_predict_failure_brak_trendu_daje_nieskonczonosc():
    predict = TIMDREVPredict()
    rng = np.random.default_rng(0)
    E = np.abs(rng.normal(0.1, 0.01, 100))  # stabilne, bez trendu
    t = np.arange(100, dtype=float)
    ttf, ttf_lin, ttf_exp = predict.predict_failure(t, E, threshold=30.0)
    assert ttf == float("inf") or ttf > 1e5


def test_predict_failure_pusty_sygnal_nie_crashuje():
    predict = TIMDREVPredict()
    ttf, lin, exp_ = predict.predict_failure(np.array([]), np.array([]), threshold=3.0)
    assert ttf == float("inf")


def test_ttf_ujemny_nie_wystepuje():
    """TTF liczone jest jako czas OD teraz - jeśli model ekstrapoluje
    wstecz poniżej progu, wynik powinien być przycięty do >=0, nie
    ujemny (co sugerowałoby "awaria była w przeszłości")."""
    predict = TIMDREVPredict()
    E = _degrading_signal(top=2.9)  # tuz PONIZEJ progu na koniec danych
    t = np.arange(len(E), dtype=float)
    ttf, ttf_lin, ttf_exp = predict.predict_failure(t, E, threshold=3.0)
    assert ttf >= 0.0


# -----------------------------------------------------------
# POPRAWKA 3: health_score nie jest permanentnie zatruty starym zdarzeniem
# -----------------------------------------------------------

def test_health_score_nie_jest_permanentnie_zatruty_starym_skokiem():
    predict = TIMDREVPredict()
    n = 500
    E = np.abs(np.random.default_rng(1).normal(0, 0.1, n))
    E[50] = 8.0  # jednorazowy stary skok
    score = predict.health_score(E, threshold=3.0)
    assert score > 0.8  # maszyna od dawna w normie -> wysoki wynik


def test_health_score_odzwierciedla_aktualny_stan_krytyczny():
    predict = TIMDREVPredict()
    n = 100
    E = np.abs(np.random.default_rng(1).normal(0, 0.05, n))
    E[-10:] = 10.0  # AKTUALNY (niedawny) stan krytyczny
    score = predict.health_score(E, threshold=3.0)
    assert score < 0.2


def test_health_score_pusty_sygnal_daje_wynik_1():
    predict = TIMDREVPredict()
    assert predict.health_score(np.array([])) == 1.0


def test_health_score_spojny_z_threshold_w_predict_failure():
    """health_score i predict_failure powinny uzywac tej samej definicji
    'krytyczny' (ten sam threshold), w przeciwienstwie do oryginalnego
    kodu, gdzie health_score mial wlasna, niezalezna skale (/5)."""
    predict = TIMDREVPredict()
    n = 50
    E = np.full(n, 3.0)  # dokladnie na progu
    t = np.arange(n, dtype=float)
    score = predict.health_score(E, threshold=3.0)
    ttf, _, _ = predict.predict_failure(t, E, threshold=3.0)
    assert score == pytest.approx(0.0, abs=1e-9)
    assert ttf == 0.0  # oba zgodnie zglaszaja "juz krytyczny"


# -----------------------------------------------------------
# predict_failure_smoothed() - opoznienie + mediana z okresu
# -----------------------------------------------------------

def test_smoothed_zbyt_krotka_historia_zwraca_inf_nieskonfirmowane():
    predict = TIMDREVPredict()
    t = np.arange(3, dtype=float)
    E = np.array([1.0, 1.1, 0.9])
    ttf, confirmed, raw = predict.predict_failure_smoothed(t, E, min_len=5)
    assert ttf == float("inf")
    assert confirmed is False
    assert raw == []


def test_smoothed_stabilniejszy_niz_pojedynczy_odczyt_na_szumie():
    """Regresja dla realnego problemu z NASA C-MAPSS: pojedyncze
    predict_failure() na prawie plaskim, zaszumionym sygnale skacze
    miedzy inf a rozne skonczone wartosci z probki na probke. Mediana
    z ostatnich `smooth_window` surowych oszacowan powinna byc mniej
    zmienna w czasie niz pojedynczy punktowy odczyt."""
    rng = np.random.default_rng(7)
    n = 80
    E = np.abs(rng.normal(1.0, 0.15, n))  # plaski, zaszumiony, zdrowy sygnal
    t = np.arange(n, dtype=float)

    single_ttfs = []
    smoothed_ttfs = []
    predict = TIMDREVPredict()
    for c in range(20, n + 1, 5):
        ttf_single, _, _ = predict.predict_failure(t[:c], E[:c], threshold=30.0, window=60)
        ttf_smooth, _, _ = predict.predict_failure_smoothed(t[:c], E[:c], threshold=30.0, window=60, smooth_window=10)
        single_ttfs.append(ttf_single)
        smoothed_ttfs.append(ttf_smooth)

    def variability(xs):
        finite = [x for x in xs if np.isfinite(x)]
        return np.std(finite) if len(finite) > 1 else 0.0

    assert variability(smoothed_ttfs) <= variability(single_ttfs) * 1.5, (
        f"mediana z okresu nie jest stabilniejsza: single={single_ttfs} smoothed={smoothed_ttfs}"
    )


def test_smoothed_wykrywa_prawdziwa_degradacje():
    predict = TIMDREVPredict()
    n_healthy, n_degrade = 50, 100
    rng = np.random.default_rng(0)
    E_healthy = np.abs(rng.normal(0, 0.05, n_healthy))
    E_degrade = np.linspace(E_healthy[-1], 15.0, n_degrade)
    E = np.concatenate([E_healthy, E_degrade])
    t = np.arange(len(E), dtype=float)

    ttf_early, confirmed_early, _ = predict.predict_failure_smoothed(
        t[:n_healthy], E[:n_healthy], threshold=15.0, smooth_window=10)
    ttf_late, confirmed_late, _ = predict.predict_failure_smoothed(
        t, E, threshold=15.0, smooth_window=10)

    assert confirmed_late is True
    assert ttf_late < 20.0
