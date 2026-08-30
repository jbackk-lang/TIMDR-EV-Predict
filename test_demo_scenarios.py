import numpy as np
import pytest
from demo_scenarios import CALIBRATED_REF_N, DEFAULT_THRESHOLDS, SCENARIOS, make_demo_data
from timdr_ev_fusion import TIMDREVFusion


def test_wszystkie_scenariusze_maja_prog_i_opis():
    for name in SCENARIOS:
        assert name in DEFAULT_THRESHOLDS


def test_nieznany_scenariusz_rzuca_czytelny_blad():
    with pytest.raises(ValueError):
        make_demo_data("nie_istnieje")


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenariusz_generuje_poprawne_dane(name):
    t, sensors = make_demo_data(name)
    assert len(t) > 0
    assert len(sensors) >= 1
    for arr in sensors.values():
        assert np.all(np.isfinite(arr))


def _fuse(name, t, sensors):
    """Fuzuje dane zgodnie z tym, czego wymaga dany scenariusz - plain
    fuse() domyslnie, albo fuse_calibrated() dla scenariuszy w
    CALIBRATED_REF_N (patrz demo_scenarios.py, sekcja o realnych danych
    baterii NASA - naiwny fuse() daje tam fizycznie odwrocony wynik)."""
    fusion = TIMDREVFusion()
    arrs = list(sensors.values())
    if name in CALIBRATED_REF_N:
        n = CALIBRATED_REF_N[name]
        fusion.calibrate([a[:n] for a in arrs])
        return fusion, fusion.fuse_calibrated(t, arrs)
    return fusion, fusion.fuse(t, arrs)


# ---------------------------------------------------------------------------
# 1. BATERIA
# ---------------------------------------------------------------------------

def test_battery_degradation_wykrywa_trend_i_daje_wysokie_e_na_koncu():
    t, sensors = make_demo_data("battery_degradation")
    fusion, (E, _) = _fuse("battery_degradation", t, sensors)
    tr_sl, _ = fusion.trend(t, E)
    assert tr_sl[-1] > 0  # narastajacy trend degradacji
    assert E[-1] > DEFAULT_THRESHOLDS["battery_degradation"]  # koniec = ponizej EOL


def test_battery_real_nasa_b0047_naiwny_fuse_jest_fizycznie_odwrocony():
    """Udokumentowany, zweryfikowany realny problem: na tych danych (brak
    stabilnego zdrowego platou - fade zaczyna sie od cyklu 0) plain
    fuse() daje NAJWYZSZE E dla NAJZDROWSZEGO cyklu (0) i NISKIE E dla
    najbardziej zdegradowanego (69) - odwrotnosc pozadanego zachowania.
    Ten test dokumentuje TO ZNANE OGRANICZENIE (nie testuje poprawnego
    zachowania - patrz test ponizej dla poprawnej wersji z kalibracja)."""
    t, sensors = make_demo_data("battery_real_nasa_b0047")
    fusion = TIMDREVFusion()
    E, _ = fusion.fuse(t, list(sensors.values()))
    assert E[0] > E[-1]  # fizycznie odwrocone: "zdrowy" cykl 0 > "zdegradowany" cykl -1


def test_battery_real_nasa_b0047_kalibrowany_fuse_jest_monotonicznie_poprawny():
    """Naprawiona wersja (fuse_calibrated wzgledem pierwszych cykli jako
    'jak nowa' referencji) - E rosnie w miare realnej degradacji ogniwa,
    zgodnie z fizyka (malejaca pojemnosc = wiekszy dystans od stanu
    fabrycznego)."""
    t, sensors = make_demo_data("battery_real_nasa_b0047")
    fusion, (E, _) = _fuse("battery_real_nasa_b0047", t, sensors)
    assert E[-1] > E[5]  # pozny, zdegradowany cykl > wczesny, zdrowy cykl
    # przy ~31% ubytku pojemnosci (klasyczne kryterium EOL to 30%) system
    # powinien juz sygnalizowac przekroczenie progu
    assert E[-1] > DEFAULT_THRESHOLDS["battery_real_nasa_b0047"]


# ---------------------------------------------------------------------------
# 2. SILNIK ELEKTRYCZNY
# ---------------------------------------------------------------------------

def test_motor_bearing_wear_wykrywa_trend():
    t, sensors = make_demo_data("motor_bearing_wear")
    fusion, (E, _) = _fuse("motor_bearing_wear", t, sensors)
    tr_sl, _ = fusion.trend(t, E)
    assert tr_sl[-1] > 0
    assert E[-1] > DEFAULT_THRESHOLDS["motor_bearing_wear"]


def test_motor_winding_short_wykrywa_twist_i_anomalie_i_utrzymane_przegrzanie():
    t, sensors = make_demo_data("motor_winding_short")
    fusion, (E, _) = _fuse("motor_winding_short", t, sensors)
    tw_idx, _ = fusion.twist(t, E)
    an_idx, _ = fusion.anomalies(E)
    assert len(tw_idx) > 0
    assert len(an_idx) > 0
    # po zdarzeniu, w ostatnich 10 probkach, przegrzanie utrzymuje sie
    # wyraznie powyzej zdrowego zakresu (nie jest to tylko chwilowy skok)
    assert E[-10:].mean() > DEFAULT_THRESHOLDS["motor_winding_short"]


# ---------------------------------------------------------------------------
# 3. ŁADOWANIE / SIEĆ
# ---------------------------------------------------------------------------

def test_charging_grid_voltage_sag_wykrywa_rytm_i_anomalie():
    """Regresja: pierwsza wersja zapadow napiecia (-18/+6) niszczyla
    calkowicie wykrywalnosc rytmu (score spadal do 0.0) - dokladnie ten
    sam kompromis co w duty_cycle_problems (TIMDR-Industrial-Predict).
    Po zmniejszeniu do -11/+3.5: oba detektory dzialaja jednoczesnie."""
    t, sensors = make_demo_data("charging_grid_voltage_sag")
    fusion, (E, _) = _fuse("charging_grid_voltage_sag", t, sensors)
    periods, score = fusion.rhythm(E, max_lag=60, power_thresh=0.4)
    an_idx, _ = fusion.anomalies(E)
    assert 15 in periods
    assert score > 0.4
    assert len(an_idx) >= 2  # przynajmniej wiekszosc z 3 zapadow wykryta


def test_charging_connector_overheat_wykrywa_rytm_i_pojedyncza_anomalie():
    """Regresja: pierwsza wersja usterki (+25 na tle juz bimodalnego
    cyklu ladowanie/przerwa) byla NIEWYKRYWALNA (0 anomalii) - E(t) juz
    ma naturalna zmiennosc ladowanie(~1.5)/przerwa(~0.68), wiec +25 nie
    bylo statystycznie odstajace. Po zwiekszeniu do +50: jednoznacznie
    wykryte, bez zniszczenia wykrywalnosci rytmu."""
    t, sensors = make_demo_data("charging_connector_overheat")
    fusion, (E, _) = _fuse("charging_connector_overheat", t, sensors)
    periods, score = fusion.rhythm(E, max_lag=60, power_thresh=0.4)
    an_idx, _ = fusion.anomalies(E)
    assert 30 in periods
    assert len(an_idx) == 1
    assert E[an_idx[0]] > DEFAULT_THRESHOLDS["charging_connector_overheat"]
