"""
monitor_ev.py — TIMDR EV Predict, monitoring ciagly/okresowy CALEGO POJAZDU
====================================================================
Jak `monitor.py` w TIMDR-Industrial-Predict, ale dla TRZECH niezaleznie
rosnacych plikow CSV naraz (bateria/silnik elektryczny/ladowanie-siec,
kazdy zasilany np. przez `ev_can_source.py` z realnego CAN/OBD-II) -
kazdy podsystem jest kalibrowany i analizowany OSOBNO (rozne czujniki,
rozna skala E, patrz timdr_ev_fusion.py), a wyniki skladane w jeden
wynik pojazdu przez `timdr_ev_vehicle.vehicle_health()` (zasada
"najslabsze ogniwo" - patrz HISTORIA_BLEDOW.md).

KAZDY podsystem jest OPCJONALNY - pojazd, ktory akurat sie nie laduje,
moze nie miec dzialajacego zrodla `charging`; monitor po prostu pomija
brakujace podsystemy w agregacji (identycznie jak `vehicle_health()` na
podzbiorze, patrz timdr_ev_vehicle.py).

PRZYKLAD (3 podsystemy, kazdy z wlasna kalibracja):
  python monitor_ev.py \\
      --battery-csv battery_live.csv --battery-auto-calibrate --battery-threshold 10.0 \\
      --motor-csv motor_live.csv --motor-auto-calibrate --motor-threshold 10.0 \\
      --charging-csv charging_live.csv --charging-auto-calibrate --charging-threshold 3.0 \\
      --interval 5

Monitoring OKRESOWY (jedno sprawdzenie, np. z crona):
  python monitor_ev.py --battery-csv battery_live.csv --battery-auto-calibrate --once

Wyjscie: jedna linia na stdout na podsystem + wynik pojazdu, plus zapis
pelnego stanu do JSON (--state-file, domyslnie timdr_ev_status.json) -
`api.py` (/api/monitor/status) czyta ten plik dla dashboardu.
"""
import argparse
import csv as csvmod
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np

from timdr_ev_fusion import TIMDREVFusion
from timdr_ev_predict import TIMDREVPredict
from timdr_ev_vehicle import vehicle_health

SUBSYSTEMS = ("battery", "motor", "charging")
DEFAULT_THRESHOLD = {"battery": 10.0, "motor": 10.0, "charging": 3.0}


def _fmt_duration(seconds):
    """Patrz identyczna funkcja w TIMDR-Industrial-Predict/monitor.py -
    spojne formatowanie TTF w konsoli, JSON i dashboardzie."""
    if seconds is None:
        return "inf"
    if seconds < 60:
        return f"{seconds:.1f}s"
    s = round(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return (f"{h}h {m}min {sec}s" if h else f"{m}min {sec}s")


def load_csv(path, t_col=None, sensor_cols=None):
    with open(path, "r", newline="") as f:
        reader = csvmod.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        return np.array([]), [], []

    if sensor_cols is None:
        candidate = [c for c in fieldnames if c != t_col]
        sensor_cols = []
        for c in candidate:
            try:
                float(rows[0][c])
                sensor_cols.append(c)
            except (TypeError, ValueError):
                continue

    if t_col and t_col in fieldnames:
        t = np.array([float(r[t_col]) for r in rows])
    else:
        t = np.arange(len(rows), dtype=float)

    sensors = [np.array([float(r[c]) for r in rows]) for c in sensor_cols]
    return t, sensors, sensor_cols


def save_calibration(fusion, path):
    with open(path, "w") as f:
        json.dump({"baseline_med": fusion.baseline_med.tolist(), "baseline_mad": fusion.baseline_mad.tolist()}, f)


def load_calibration(fusion, path):
    with open(path) as f:
        d = json.load(f)
    fusion.baseline_med = np.array(d["baseline_med"])
    fusion.baseline_mad = np.array(d["baseline_mad"])


def calibrate_subsystem(sub, sub_args, fusion):
    """Kalibruje JEDEN podsystem (healthy-ref/auto-calibrate/juz-zapisana
    kalibracja) - identyczna logika co w monitor.py, powtorzona per
    podsystem. Zwraca True, jesli podsystem jest aktywny (ma --csv)."""
    if sub_args["csv"] is None:
        return False

    if os.path.exists(sub_args["calib_cache"]):
        load_calibration(fusion, sub_args["calib_cache"])
        print(f"[{sub}] Wczytano kalibracje z {sub_args['calib_cache']}.")
    elif sub_args["healthy_ref"]:
        t_ref, sensors_ref, _ = load_csv(sub_args["healthy_ref"], t_col=sub_args["t_col"], sensor_cols=sub_args["sensor_cols"])
        fusion.calibrate(sensors_ref)
        save_calibration(fusion, sub_args["calib_cache"])
        print(f"[{sub}] Skalibrowano z {sub_args['healthy_ref']} ({len(t_ref)} probek).")
    elif sub_args["auto_calibrate"]:
        t_probe, sensors_probe, _ = load_csv(sub_args["csv"], t_col=sub_args["t_col"], sensor_cols=sub_args["sensor_cols"])
        if len(t_probe) == 0:
            print(f"BLAD [{sub}]: --{sub}-auto-calibrate wymaga, zeby --{sub}-csv mial juz jakies dane.", file=sys.stderr)
            sys.exit(1)
        info = fusion.auto_calibrate(sensors_probe, probe_window=sub_args["calib_probe"], candidate_size=sub_args["calib_window"])
        save_calibration(fusion, sub_args["calib_cache"])
        print(f"[{sub}] Autokalibracja z {sub_args['csv']}: okno [{info['chosen_start']}:"
              f"{info['chosen_start']+info['window_size']}], zmiennosc {info['variability_chosen']:.3f}.")
        if not info["validated"]:
            print(f"UWAGA [{sub}]: Mann-Kendall wykryl istotny trend w oknie kalibracyjnym.")
    else:
        print(f"BLAD [{sub}]: brak zapisanej kalibracji i nie podano --{sub}-healthy-ref ani --{sub}-auto-calibrate.", file=sys.stderr)
        sys.exit(1)
    return True


def check_subsystem(sub, sub_args, fusion, predict, prev_status):
    t, sensors, sensor_cols = load_csv(sub_args["csv"], t_col=sub_args["t_col"], sensor_cols=sub_args["sensor_cols"])
    if len(t) == 0:
        print(f"[{sub}] BRAK DANYCH w pliku live - pomijam to sprawdzenie.")
        return None

    E, _ = fusion.fuse_calibrated(t, sensors)
    threshold = sub_args["threshold"]
    hs = predict.health_score(E, threshold=threshold, window=sub_args["hs_window"])
    ttf, confirmed, _raw = predict.predict_failure_smoothed(
        t, E, threshold=threshold, window=sub_args["ttf_window"], smooth_window=sub_args["smooth_window"],
    )

    now_iso = datetime.now(timezone.utc).isoformat()
    n_samples = int(len(t))

    # ROZROZNIENIE checker_online vs data_live - patrz api.py w
    # TIMDR-Industrial-Predict po pelne uzasadnienie; tu identyczna logika
    # zastosowana OSOBNO per podsystem (adapter CAN/OBD baterii moze sie
    # rozlaczyc niezaleznie od adaptera silnika, np.).
    last_data_change = now_iso
    if prev_status and prev_status.get("n_samples") == n_samples and prev_status.get("last_data_change"):
        last_data_change = prev_status["last_data_change"]

    return {
        "timestamp": now_iso,
        "last_data_change": last_data_change,
        "n_samples": n_samples,
        "sensor_cols": sensor_cols,
        "E_last": float(E[-1]),
        "health_score": float(hs),
        "ttf": (None if not np.isfinite(ttf) else float(ttf)),
        "ttf_confirmed": bool(confirmed),
        "threshold": threshold,
    }


def build_parser():
    ap = argparse.ArgumentParser(description="TIMDR EV Predict - monitoring ciagly/okresowy calego pojazdu")
    for sub in SUBSYSTEMS:
        g = ap.add_argument_group(f"podsystem: {sub}")
        g.add_argument(f"--{sub}-csv", default=None, help=f"Plik CSV z live danymi podsystemu '{sub}' (pomin, zeby wylaczyc ten podsystem)")
        g.add_argument(f"--{sub}-healthy-ref", default=None, help="Plik CSV ze znanym zdrowym oknem odniesienia")
        g.add_argument(f"--{sub}-auto-calibrate", action="store_true", help="Automatyczna kalibracja z --*-csv")
        g.add_argument(f"--{sub}-calib-probe", type=int, default=None)
        g.add_argument(f"--{sub}-calib-window", type=int, default=None)
        g.add_argument(f"--{sub}-calib-cache", default=f"timdr_ev_calibration_{sub}.json")
        g.add_argument(f"--{sub}-t-col", default="t")
        g.add_argument(f"--{sub}-sensor-cols", nargs="*", default=None)
        g.add_argument(f"--{sub}-threshold", type=float, default=DEFAULT_THRESHOLD[sub])
        g.add_argument(f"--{sub}-hs-window", type=int, default=20)
        g.add_argument(f"--{sub}-ttf-window", type=int, default=60)
        g.add_argument(f"--{sub}-smooth-window", type=int, default=10)

    ap.add_argument("--state-file", default="timdr_ev_status.json")
    ap.add_argument("--alert-health", type=float, default=0.2)
    ap.add_argument("--alert-ttf", type=float, default=24.0)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    return ap


def _sub_args(args, sub):
    prefix = f"{sub}_"
    return {k[len(prefix):]: v for k, v in vars(args).items() if k.startswith(prefix)}


def run_all_checks(args, fusions, predict, active):
    prev = {}
    if os.path.exists(args.state_file):
        try:
            with open(args.state_file) as f:
                prev = json.load(f).get("subsystems", {})
        except (OSError, json.JSONDecodeError):
            prev = {}

    subsystem_status = {}
    for sub in SUBSYSTEMS:
        if not active[sub]:
            continue
        status = check_subsystem(sub, _sub_args(args, sub), fusions[sub], predict, prev.get(sub))
        if status is not None:
            subsystem_status[sub] = status

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "interval": (None if args.once else args.interval),
        "subsystems": subsystem_status,
    }

    if subsystem_status:
        veh_input = {sub: {"health_score": s["health_score"], "ttf": (s["ttf"] if s["ttf"] is not None else float("inf"))}
                     for sub, s in subsystem_status.items()}
        veh = vehicle_health(veh_input)
        result["vehicle"] = {
            "vehicle_health_score": veh["vehicle_health_score"],
            "weakest_subsystem": veh["weakest_subsystem"],
            "vehicle_ttf": (None if not np.isfinite(veh["vehicle_ttf"]) else veh["vehicle_ttf"]),
            "soonest_subsystem": veh["soonest_subsystem"],
        }
        result["alert"] = bool(
            veh["vehicle_health_score"] < args.alert_health
            or (np.isfinite(veh["vehicle_ttf"]) and veh["vehicle_ttf"] < args.alert_ttf)
        )
    else:
        result["vehicle"] = None
        result["alert"] = False

    with open(args.state_file, "w") as f:
        json.dump(result, f, indent=2)

    for sub, s in subsystem_status.items():
        print(f"[{sub}] n={s['n_samples']:5d}  E={s['E_last']:.2f}  health={s['health_score']:.3f}  "
              f"TTF={_fmt_duration(s['ttf'])} (confirmed={s['ttf_confirmed']})")
    if result["vehicle"]:
        v = result["vehicle"]
        alert_txt = " !!! ALARM !!!" if result["alert"] else ""
        print(f"[POJAZD] health={v['vehicle_health_score']:.3f} (najslabszy: {v['weakest_subsystem']})  "
              f"TTF={_fmt_duration(v['vehicle_ttf'])} (najszybszy: {v['soonest_subsystem']}){alert_txt}")
    return result


def main():
    args = build_parser().parse_args()

    active = {}
    fusions = {sub: TIMDREVFusion() for sub in SUBSYSTEMS}
    predict = TIMDREVPredict()

    for sub in SUBSYSTEMS:
        active[sub] = calibrate_subsystem(sub, _sub_args(args, sub), fusions[sub])

    if not any(active.values()):
        print("BLAD: nie podano zadnego --*-csv - potrzeba co najmniej 1 podsystemu.", file=sys.stderr)
        sys.exit(1)

    if args.once:
        run_all_checks(args, fusions, predict, active)
        return

    print(f"Monitoring CIAGLY (podsystemy: {[s for s in SUBSYSTEMS if active[s]]}): sprawdzanie co {args.interval}s. Ctrl+C konczy.")
    try:
        while True:
            run_all_checks(args, fusions, predict, active)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nZatrzymano monitoring.")


if __name__ == "__main__":
    main()
