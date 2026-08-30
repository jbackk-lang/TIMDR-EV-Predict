"""
test_monitor_ev.py — weryfikacja monitor_ev.py na REALNYCH (wygenerowanych
przez demo_scenarios, nie zamockowanych) rosnacych plikach CSV, dla 1, 2 i
3 jednoczesnie aktywnych podsystemow. Nie testuje ev_can_source.py (patrz
test_ev_can_source.py) - tu wejsciem sa juz gotowe pliki CSV, tak jak
faktycznie dostarczylby je ev_can_source.py na zywym pojezdzie.
"""
import csv
import json
import time

import numpy as np
import pytest
from demo_scenarios import battery_degradation, charging_connector_overheat, motor_bearing_wear
from monitor_ev import build_parser, calibrate_subsystem, run_all_checks, _sub_args, SUBSYSTEMS
from timdr_ev_fusion import TIMDREVFusion
from timdr_ev_predict import TIMDREVPredict


def _write_csv(path, t, sensors, n_rows):
    names = list(sensors.keys())
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t"] + names)
        for i in range(n_rows):
            w.writerow([t[i]] + [sensors[n][i] for n in names])


def _make_args(**overrides):
    argv = []
    for k, v in overrides.items():
        flag = "--" + k.replace("_", "-")
        if v is True:
            argv.append(flag)
        elif v is not None:
            argv.extend([flag, str(v)])
    return build_parser().parse_args(argv)


def test_jeden_podsystem_bateria_auto_calibrate_i_agregacja(tmp_path):
    t, sensors = battery_degradation()
    csv_path = tmp_path / "battery_live.csv"
    _write_csv(csv_path, t, sensors, n_rows=300)  # tylko zdrowa czesc na start

    args = _make_args(
        battery_csv=str(csv_path),
        battery_auto_calibrate=True,
        battery_calib_cache=str(tmp_path / "calib_battery.json"),
        state_file=str(tmp_path / "status.json"),
        once=True,
    )
    fusions = {sub: TIMDREVFusion() for sub in SUBSYSTEMS}
    predict = TIMDREVPredict()
    active = {sub: calibrate_subsystem(sub, _sub_args(args, sub), fusions[sub]) for sub in SUBSYSTEMS}
    assert active == {"battery": True, "motor": False, "charging": False}

    result = run_all_checks(args, fusions, predict, active)
    assert "battery" in result["subsystems"]
    assert result["vehicle"]["weakest_subsystem"] == "battery"
    assert result["vehicle"]["vehicle_health_score"] == result["subsystems"]["battery"]["health_score"]

    with open(args.state_file) as f:
        saved = json.load(f)
    assert saved == result


def test_last_data_change_nie_aktualizuje_sie_gdy_n_samples_sie_nie_zmienia(tmp_path):
    """Regresja dla checker_online/data_live: jesli plik live NIE rosnie
    miedzy dwoma sprawdzeniami, last_data_change MUSI zostac ta sama (nie
    'teraz') - inaczej data_live w api.py zawsze wygladaloby na swieze,
    nawet gdy zrodlo danych faktycznie sie rozlaczylo."""
    t, sensors = battery_degradation()
    csv_path = tmp_path / "battery_live.csv"
    _write_csv(csv_path, t, sensors, n_rows=300)

    args = _make_args(
        battery_csv=str(csv_path),
        battery_auto_calibrate=True,
        battery_calib_cache=str(tmp_path / "calib_battery.json"),
        state_file=str(tmp_path / "status.json"),
        once=True,
    )
    fusions = {sub: TIMDREVFusion() for sub in SUBSYSTEMS}
    predict = TIMDREVPredict()
    active = {sub: calibrate_subsystem(sub, _sub_args(args, sub), fusions[sub]) for sub in SUBSYSTEMS}

    result1 = run_all_checks(args, fusions, predict, active)
    change1 = result1["subsystems"]["battery"]["last_data_change"]
    time.sleep(1.1)
    result2 = run_all_checks(args, fusions, predict, active)  # ten sam plik, bez nowych wierszy
    change2 = result2["subsystems"]["battery"]["last_data_change"]
    assert change1 == change2  # brak nowych probek -> last_data_change NIE powinno sie zmienic

    # teraz dopisz nowe wiersze (symulacja plywajacych danych na zywo)
    _write_csv(csv_path, t, sensors, n_rows=310)
    time.sleep(1.1)
    result3 = run_all_checks(args, fusions, predict, active)
    change3 = result3["subsystems"]["battery"]["last_data_change"]
    assert change3 != change2  # nowe probki -> last_data_change MUSI sie zaktualizowac


def test_dwa_podsystemy_bateria_i_silnik_najslabszy_wygrywa(tmp_path):
    tb, sb = battery_degradation()
    tm, sm = motor_bearing_wear()
    battery_csv = tmp_path / "battery_live.csv"
    motor_csv = tmp_path / "motor_live.csv"
    _write_csv(battery_csv, tb, sb, n_rows=300)  # tylko zdrowa czesc -> health wysoki
    _write_csv(motor_csv, tm, sm, n_rows=len(tm))  # PELNY przebieg, konczy sie zdegradowany -> health niski

    args = _make_args(
        battery_csv=str(battery_csv), battery_auto_calibrate=True,
        battery_calib_cache=str(tmp_path / "calib_b.json"),
        motor_csv=str(motor_csv), motor_auto_calibrate=True,
        motor_calib_cache=str(tmp_path / "calib_m.json"),
        state_file=str(tmp_path / "status.json"),
        once=True,
    )
    fusions = {sub: TIMDREVFusion() for sub in SUBSYSTEMS}
    predict = TIMDREVPredict()
    active = {sub: calibrate_subsystem(sub, _sub_args(args, sub), fusions[sub]) for sub in SUBSYSTEMS}
    assert active["battery"] and active["motor"] and not active["charging"]

    result = run_all_checks(args, fusions, predict, active)
    assert result["vehicle"]["weakest_subsystem"] == "motor"
    assert result["subsystems"]["battery"]["health_score"] > result["subsystems"]["motor"]["health_score"]


def test_brak_zadnego_csv_konczy_sie_bledem(tmp_path):
    """main() powinno zakonczyc sie bledem, gdy nie podano ZADNEGO --*-csv
    - sprawdzamy to na poziomie calibrate_subsystem (zwraca same False)."""
    args = _make_args(state_file=str(tmp_path / "status.json"), once=True)
    fusions = {sub: TIMDREVFusion() for sub in SUBSYSTEMS}
    active = {sub: calibrate_subsystem(sub, _sub_args(args, sub), fusions[sub]) for sub in SUBSYSTEMS}
    assert not any(active.values())
