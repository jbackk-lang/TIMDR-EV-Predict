"""
demo_scenarios.py — syntetyczne + realne zestawy danych demo dla TIMDR-EV-Predict
====================================================================================
Trzy grupy podsystemów pojazdu elektrycznego, każda z co najmniej jednym
scenariuszem "zdrowym" i jednym scenariuszem "usterkowym", zaprojektowane
tak, by faktycznie uruchamiały deklarowany detektor (zweryfikowane w
test_demo_scenarios.py, nie tylko założone — patrz też HISTORIA_BLEDOW.md):

  1. bateria       (battery_*)   — starzenie ogniw, spadek pojemności
  2. silnik elektr. (motor_*)    — zużycie łożysk/uzwojenia, zwarcie międzyzwojowe
  3. ładowanie/sieć (charging_*) — zapady napięcia sieci, przegrzanie złącza

Jeden scenariusz (`battery_real_nasa_b0047`) to REALNE dane pomiarowe
(NASA PCoE Li-ion Battery Aging Dataset, ogniwo B0047) — patrz
data/real_battery/README.md po pełną proweniencję i zastrzeżenia.
"""

import os

import numpy as np

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "real_battery")

SCENARIOS = {
    "battery_degradation": "🔋 Bateria: starzenie ogniw — rosnąca rezystancja wewnętrzna i temperatura ładowania, malejąca pojemność (trend)",
    "battery_real_nasa_b0047": "🔋 REALNE dane (NASA PCoE, ogniwo B0047) — 69 cykli rozładowania, realny spadek pojemności 1.67→1.16 Ah (~31%)",
    "motor_bearing_wear": "⚙️ Silnik elektryczny: zużycie łożysk — rosnąca wibracja i temperatura uzwojeń, spadająca sprawność (trend)",
    "motor_winding_short": "⚙️ Silnik elektryczny: zwarcie międzyzwojowe — nagły skok asymetrii prądu i momentu (anomalia), potem narastające przegrzanie",
    "charging_grid_voltage_sag": "🔌 Ładowanie/sieć: zapady napięcia sieci — cykliczne wahania napięcia i prądu ładowania (rytm) z pojedynczymi głębokimi zapadami (anomalia)",
    "charging_connector_overheat": "🔌 Ładowanie/sieć: przegrzewające się złącze — powtarzalne sesje ładowania (rytm) z jedną przegrzaną sesją (anomalia) i narastającym dryfem rezystancji styku (trend)",
}

DEFAULT_THRESHOLDS = {
    # Dobrane empirycznie na podstawie fuse()/fuse_calibrated() + realnych
    # wartosci E(t) dla kazdego scenariusza (patrz test_demo_scenarios.py) -
    # nie zgadywane: healthy vs zdegradowany zakres E sprawdzony wprost.
    "battery_degradation": 10.0,       # healthy E<=2.53, zdegradowany E do 47.3
    "battery_real_nasa_b0047": 7.0,    # SKALA KALIBROWANA - patrz CALIBRATED_REF_N nizej
    "motor_bearing_wear": 10.0,        # healthy E<=3.13, zdegradowany E do 55.3
    "motor_winding_short": 10.0,       # healthy E<=3.70, zwarcie E do 225 (potem >19 utrzymane)
    "charging_grid_voltage_sag": 3.0,  # rytm 0.07-1.85, zapady napiecia 5.7-7.9
    "charging_connector_overheat": 3.0,  # normalny cykl <=1.55, usterka (1 probka) = 3.68
}

# ZNALEZIONY REALNY PROBLEM (test na prawdziwych danych NASA PCoE, ogniwo
# B0047): plain `fuse()` liczy MAD-z WZGLEDEM GLOBALNEJ MEDIANY calego
# okna - dla danych, ktore degraduja sie CIAGLE OD CYKLU 0 (brak
# stabilnego "zdrowego" platou na poczatku, w odroznieniu od silnikow
# C-MAPSS), to daje FIZYCZNIE ODWROCONY wynik: najzdrowszy mozliwy
# moment (cykl 0, pojemnosc najwyzsza) dostaje NAJWYZSZE E (bo jest
# najdalej od mediany calej, juz zdegradowanej historii), a realnie
# najbardziej zdegradowane pozne cykle dostaja NISKIE E (bo skupiaja sie
# wokol nowej mediany). Zweryfikowano wprost: cykl 0 (pojemnosc 1.674 Ah,
# stan fabryczny) -> E=5.42; cykl 69 (pojemnosc 1.157 Ah, ~31% ubytku)
# -> E=0.66 - DEGRADACJA DAJE NIZSZE E NIZ STAN ZDROWY, dokladna
# odwrotnosc pozadanego zachowania. Ponadto E jest niezmiennicze na
# transformacje liniowa danej cechy (np. "capacity_fade" zamiast
# "capacity") - MAD-z oparte na jednej, samoreferencyjnej serii nie
# rozroznia KIERUNKU odchylenia, tylko jego WIELKOSC, wiec zwykla zamiana
# jednostki nie naprawia problemu.
#
# Naprawiono NIE zmiana fuse(), tylko UZYCIEM WLASCIWEGO NARZEDZIA: to
# dokladnie przypadek, do ktorego `calibrate()`/`fuse_calibrated()` zostaly
# zbudowane (patrz TIMDR-Industrial-Predict) - kalibracja wzgledem
# ZAMROZONEGO, oddzielnego okna odniesienia ("jak nowa": pierwsze
# CALIBRATED_REF_N cykli), nie wzgledem biezacej, samoreferencyjnej proby.
# Zweryfikowano: z ta kalibracja cykl 0 -> E~4.57 (delikatny naturalny
# szum startowy), cykl 69 -> E~9.67 - poprawny, MONOTONICZNIE rosnacy
# obraz degradacji. `api.py` sprawdza ten slownik i dla wymienionych
# scenariuszy automatycznie kalibruje przed fuze - patrz /api/analyze.
CALIBRATED_REF_N = {
    "battery_real_nasa_b0047": 5,
}

# Przynaleznosc scenariusza do grupy podsystemu pojazdu - uzywane przez
# dashboard (3 osobne listy wyboru) i /api/vehicle (agregacja
# per-podsystem, patrz timdr_ev_vehicle.py).
SUBSYSTEM_OF = {
    "battery_degradation": "battery",
    "battery_real_nasa_b0047": "battery",
    "motor_bearing_wear": "motor",
    "motor_winding_short": "motor",
    "charging_grid_voltage_sag": "charging",
    "charging_connector_overheat": "charging",
}


# ---------------------------------------------------------------------------
# 1. BATERIA
# ---------------------------------------------------------------------------

def battery_degradation(seed=0):
    """Starzenie ogniw: 300 cykli zdrowej pracy (pojemność stabilna
    ok. 2.0 Ah), potem 150 cykli postępującej degradacji — pojemność
    spada do ok. 1.4 Ah (klasyczne kryterium EOL: 30% fade), rezystancja
    wewnętrzna i temperatura ładowania rosną (typowy powolny trend,
    analogicznie do bearing_wear w TIMDR-Industrial-Predict)."""
    rng = np.random.default_rng(seed)
    n_healthy, n_degrade = 300, 150
    n = n_healthy + n_degrade
    t = np.arange(n, dtype=float)

    capacity = np.concatenate([
        rng.normal(2.0, 0.01, n_healthy),
        2.0 - np.linspace(0, 0.6, n_degrade) + rng.normal(0, 0.01, n_degrade),
    ])
    internal_resistance = np.concatenate([
        rng.normal(0.08, 0.002, n_healthy),
        0.08 + np.linspace(0, 0.12, n_degrade) + rng.normal(0, 0.002, n_degrade),
    ])
    charge_temp = np.concatenate([
        rng.normal(28, 0.5, n_healthy),
        28 + np.linspace(0, 15, n_degrade) + rng.normal(0, 0.5, n_degrade),
    ])
    charge_time_min = np.concatenate([
        rng.normal(45, 1, n_healthy),
        45 + np.linspace(0, 25, n_degrade) + rng.normal(0, 1, n_degrade),
    ])
    return t, {
        "capacity_ah": capacity,
        "internal_resistance_ohm": internal_resistance,
        "charge_temp_c": charge_temp,
        "charge_time_min": charge_time_min,
    }


def _load_real_battery_b0047():
    """Wczytuje REALNE dane NASA PCoE (ogniwo B0047, 72 cykle
    rozładowania). 3 wiersze maja Capacity=0 - to potwierdzone bledy
    pomiaru/przerwane testy (fizycznie niemozliwe, zeby pojemnosc spadla
    do zera i wrocila do normy w kolejnym cyklu) - odrzucamy je zamiast
    interpolowac, zeby nie fabrykowac danych. Zrodlo:
    data/real_battery/README.md."""
    path = os.path.join(DATA_DIR, "nasa_b0047_discharge_capacity.csv")
    cycles, caps = [], []
    with open(path) as f:
        next(f)  # naglowek
        for line in f:
            line = line.strip()
            if not line:
                continue
            cyc, cap = line.split(",")
            if cap == "":
                continue  # odrzucony blad pomiaru (Capacity=0 w zrodle)
            cycles.append(float(cyc))
            caps.append(float(cap))
    return np.array(cycles), np.array(caps)


def battery_real_nasa_b0047(seed=0):
    """Ogniwo B0047, NASA PCoE Li-ion Battery Aging Dataset - REALNE dane
    pomiarowe, 69 uzytecznych cykli rozladowania (z 72 nagranych - 3
    odrzucone jako bledy pomiaru). `seed` ignorowany (dane nie sa
    syntetyczne)."""
    t, capacity = _load_real_battery_b0047()
    return t, {"capacity_ah": capacity}


# ---------------------------------------------------------------------------
# 2. SILNIK ELEKTRYCZNY
# ---------------------------------------------------------------------------

def motor_bearing_wear(seed=0):
    """Zużycie łożysk silnika elektrycznego: 300s zdrowej pracy, potem
    120s narastającej wibracji i temperatury uzwojeń, ze spadającą
    sprawnością (typowy powolny trend degradacji, analogicznie do
    bearing_wear w TIMDR-Industrial-Predict — tu dodatkowo sprawność
    energetyczna jako sygnał specyficzny dla napędu EV)."""
    rng = np.random.default_rng(seed)
    n_healthy, n_degrade = 300, 120
    n = n_healthy + n_degrade
    t = np.arange(n, dtype=float)
    vib = np.concatenate([
        rng.normal(0.15, 0.015, n_healthy),
        0.15 + np.linspace(0, 1.2, n_degrade) + rng.normal(0, 0.015, n_degrade),
    ])
    winding_temp = np.concatenate([
        rng.normal(70, 1.5, n_healthy),
        70 + np.linspace(0, 40, n_degrade) + rng.normal(0, 1.5, n_degrade),
    ])
    efficiency_pct = np.concatenate([
        rng.normal(94, 0.3, n_healthy),
        94 - np.linspace(0, 12, n_degrade) + rng.normal(0, 0.3, n_degrade),
    ])
    return t, {"vibration": vib, "winding_temp_c": winding_temp, "efficiency_pct": efficiency_pct}


def motor_winding_short(seed=0):
    """Zwarcie międzyzwojowe: 250s normalnej pracy, nagły skok asymetrii
    prądu między fazami i pulsacji momentu obrotowego w momencie zwarcia
    (t=250, analogicznie do pump_seizure), potem 100s narastającego
    przegrzania uzwojeń (skutek zwarcia, nie jego przyczyna)."""
    rng = np.random.default_rng(seed)
    n_healthy, n_after = 250, 100
    n = n_healthy + n_after
    t = np.arange(n, dtype=float)
    event = n_healthy

    phase_current_imbalance_pct = np.concatenate([
        rng.normal(1.0, 0.15, n_healthy),
        rng.normal(1.0, 0.15, n_after),
    ])
    phase_current_imbalance_pct[event:event + 3] += [18.0, 27.0, 12.0]

    torque_ripple_pct = np.concatenate([
        rng.normal(2.0, 0.2, n_healthy),
        rng.normal(2.0, 0.2, n_after),
    ])
    torque_ripple_pct[event:event + 3] += [20.0, 30.0, 15.0]

    winding_temp = np.concatenate([
        rng.normal(70, 1.5, n_healthy),
        70 + np.linspace(0, 45, n_after) + rng.normal(0, 1.5, n_after),
    ])
    return t, {
        "phase_current_imbalance_pct": phase_current_imbalance_pct,
        "torque_ripple_pct": torque_ripple_pct,
        "winding_temp_c": winding_temp,
    }


# ---------------------------------------------------------------------------
# 3. ŁADOWANIE / SIEĆ
# ---------------------------------------------------------------------------

def charging_grid_voltage_sag(seed=0, n=300, period=15):
    """Zapady napięcia sieci podczas ładowania: cykliczne wahania
    napięcia i prądu ładowania W FAZIE (typowa modulacja obciążenia
    sieci lokalnej), plus kilka odosobnionych głębokich zapadów napięcia
    (np. załączenie innego dużego odbiornika w tej samej instalacji).

    UWAGA (analogicznie do uneven_motor_rotation w TIMDR-Industrial-Predict,
    zweryfikowane empirycznie): tylko 2 czujniki (grid_voltage, charge_current)
    - dodanie niezwiazanej cechy (np. temperatury otoczenia) rozmywa
    wykrywalnosc rytmu przez normalizacje per-cecha w _mad_z()."""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    phase = 2 * np.pi * t / period
    grid_voltage = 230.0 + 4.0 * np.sin(phase) + rng.normal(0, 0.5, n)
    charge_current = 32.0 - 1.0 * np.sin(phase) + rng.normal(0, 0.3, n)  # prad kompensuje spadek napiecia
    # UWAGA (zweryfikowane empirycznie, ten sam kompromis co
    # duty_cycle_problems w TIMDR-Industrial-Predict): zbyt DUZY zapad
    # (-18/+6, pierwsza wersja) calkowicie niszczyl wykrywalnosc rytmu
    # (score spadalo do 0.0, periods=[]) - pojedyncze ekstremalne probki
    # dominuja statystyki oparte na wariancji w rhythm(). Zbyt MALY zapad
    # (<9) nie przekraczal progu anomalies() (z>3.0) dla wszystkich 3
    # zdarzen. -11/+3.5 to zweryfikowany kompromis: wszystkie 3 anomalie
    # wykryte ORAZ rytm nadal wykrywalny (score=0.49, okres=15 w liscie).
    for idx in (80, 160, 240):
        grid_voltage[idx] -= 11.0  # zapad napiecia
        charge_current[idx] += 3.5  # chwilowy wzrost poboru prądu przy kompensacji
    return t, {"grid_voltage_v": grid_voltage, "charge_current_a": charge_current}


def charging_connector_overheat(seed=0, n=360, period=30):
    """Przegrzewające się złącze ładowania: powtarzalne sesje ładowania
    (cykl wysoki prąd/wysoka temperatura złącza vs. przerwa), jedna
    wyraźnie przegrzana sesja (np. luźny styk przy jednym podłączeniu),
    plus narastający dryf bazowej temperatury złącza w drugiej połowie
    nagrania (postępująca korozja/degradacja styku - analogicznie do
    duty_cycle_problems w TIMDR-Industrial-Predict)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n, dtype=float)
    phase = np.arange(n) % period
    charging = phase < (period // 2)

    drift_start = n // 2
    baseline_drift = np.concatenate([np.zeros(drift_start), np.linspace(0, 18.0, n - drift_start)])

    connector_temp = 25.0 + baseline_drift + np.where(charging, 20.0, 0.0) + rng.normal(0, 0.5, n)
    charge_current = 32.0 + np.where(charging, 0.0, -32.0) + rng.normal(0, 0.3, n)

    fault_idx = 100
    # UWAGA (zweryfikowane empirycznie): +25 bylo NIEWYSTARCZAJACE, zeby
    # anomalies() to wylapala - E(t) juz jest bimodalne (ladowanie vs
    # przerwa daje E~1.5 vs E~0.68), wiec +25 na tle tak zmiennego sygnalu
    # dawalo z<3.0 (proba odrzucona przez detektor). +50 daje wyrazny,
    # jednoznaczny wyrzut ponad OBIE fazy normalnego cyklu.
    connector_temp[fault_idx] += 50.0

    return t, {"connector_temp_c": connector_temp, "charge_current_a": charge_current}


GENERATORS = {
    "battery_degradation": battery_degradation,
    "battery_real_nasa_b0047": battery_real_nasa_b0047,
    "motor_bearing_wear": motor_bearing_wear,
    "motor_winding_short": motor_winding_short,
    "charging_grid_voltage_sag": charging_grid_voltage_sag,
    "charging_connector_overheat": charging_connector_overheat,
}


def make_demo_data(scenario="battery_degradation", seed=0):
    if scenario not in GENERATORS:
        raise ValueError(f"Nieznany scenariusz '{scenario}'. Dostepne: {list(GENERATORS)}")
    return GENERATORS[scenario](seed=seed)
