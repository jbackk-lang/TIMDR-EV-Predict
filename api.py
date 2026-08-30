"""
api.py — TIMDR EV Predict, lokalne REST API + dashboard
====================================================================
Serwer Flask udostepniajacy:
  GET  /                  -> dashboard (static/dashboard.html)
  GET  /api/scenarios     -> lista dostepnych scenariuszy demo, pogrupowana
                              wg podsystemu (battery/motor/charging), z opisem,
                              sugerowanym progiem i opcjonalna flaga kalibracji
  GET  /api/demo          -> syntetyczny/realny zestaw czujnikow dla jednego
                              scenariusza (?scenario=<nazwa>)
  POST /api/analyze       -> pelna analiza TIMDR dla JEDNEGO podsystemu
                              (fuse/fuse_calibrated + twist/trend/anomalies/
                              rhythm + TTF + health_score)
  POST /api/vehicle       -> agreguje 1-3 wyniki /api/analyze (po jednym na
                              podsystem) w JEDEN wynik pojazdu - "najslabsze
                              ogniwo" (patrz timdr_ev_vehicle.py)
  GET  /api/monitor/status -> ostatni stan zapisany przez monitor_ev.py
                              (timdr_ev_status.json) - live monitoring z
                              realnego CAN/OBD-II przez ev_can_source.py,
                              per podsystem + zagregowany wynik pojazdu
  GET  /api/health        -> healthcheck samego API

Uruchomienie: `python api.py` (albo `run.bat` na Windows), potem
http://127.0.0.1:5000 w przegladarce.
"""

import json
import os
from datetime import datetime, timezone

import numpy as np
from flask import Flask, jsonify, request, send_from_directory

from demo_scenarios import (
    CALIBRATED_REF_N,
    DEFAULT_THRESHOLDS,
    SCENARIOS,
    SUBSYSTEM_OF,
    make_demo_data,
)
from timdr_ev_fusion import TIMDREVFusion
from timdr_ev_predict import TIMDREVPredict
from timdr_ev_vehicle import vehicle_health

app = Flask(__name__, static_folder="static", static_url_path="")

# Plik zapisywany przez monitor_ev.py (--state-file) - domyslnie w tym
# samym katalogu co api.py. Jesli monitor_ev.py dziala z innego katalogu
# roboczego z innym --state-file, ustaw zmienna srodowiskowa
# TIMDR_EV_STATUS_FILE.
STATUS_FILE = os.environ.get("TIMDR_EV_STATUS_FILE", "timdr_ev_status.json")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "dashboard.html")


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok"})


@app.route("/api/scenarios")
def api_scenarios():
    return jsonify([
        {
            "id": name,
            "description": desc,
            "default_threshold": DEFAULT_THRESHOLDS[name],
            "subsystem": SUBSYSTEM_OF[name],
            "calibrated_ref_n": CALIBRATED_REF_N.get(name),
        }
        for name, desc in SCENARIOS.items()
    ])


@app.route("/api/demo")
def api_demo():
    scenario = request.args.get("scenario", "battery_degradation")
    try:
        t, sensors = make_demo_data(scenario)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({
        "scenario": scenario,
        "subsystem": SUBSYSTEM_OF.get(scenario),
        "default_threshold": DEFAULT_THRESHOLDS.get(scenario, 3.0),
        "calibrated_ref_n": CALIBRATED_REF_N.get(scenario),
        "t": t.tolist(),
        "sensors": {k: v.tolist() for k, v in sensors.items()},
    })


def _clean(x):
    # JSON nie ma inf/nan - zamieniamy na null, dashboard obsluzy to jako "brak predykcji"
    x = float(x)
    return None if not np.isfinite(x) else x


@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    """
    Body (JSON):
      t: [..]                 - znaczniki czasu
      sensors: {nazwa: [..]}  - dowolna liczba czujnikow (min. 1)
      threshold: float=3.0    - prog "awarii" dla E(t)
      window: int=60          - okno regresji/health_score
      calibrated_ref_n: int|None - jesli podane, kalibruje TIMDREVFusion
                                    wzgledem PIERWSZYCH N probek KAZDEGO
                                    czujnika (jako "stan jak nowy") i uzywa
                                    fuse_calibrated() zamiast fuse(). Patrz
                                    demo_scenarios.CALIBRATED_REF_N i
                                    HISTORIA_BLEDOW.md - potrzebne dla
                                    danych, ktore degraduja sie OD PROBKI 0
                                    (brak stabilnego zdrowego platou), np.
                                    realne dane baterii NASA.

    Zwraca pelny wynik analizy jako JSON - wszystko, co dashboard
    potrzebuje do narysowania wykresow, wskaznikow i (opcjonalnie)
    zlozenia w wynik calego pojazdu przez /api/vehicle.
    """
    body = request.get_json(force=True, silent=True) or {}

    if "t" not in body or "sensors" not in body:
        return jsonify({"error": "wymagane pola: 't' (lista) i 'sensors' (obiekt nazwa->lista)"}), 400

    try:
        t = np.asarray(body["t"], dtype=float)
        sensor_names = list(body["sensors"].keys())
        sensor_arrays = [np.asarray(body["sensors"][name], dtype=float) for name in sensor_names]
    except (TypeError, ValueError) as exc:
        return jsonify({"error": f"niepoprawne dane wejsciowe: {exc}"}), 400

    if len(t) == 0 or not sensor_arrays:
        return jsonify({"error": "t i sensors nie moga byc puste"}), 400

    threshold = float(body.get("threshold", 3.0))
    window = int(body.get("window", 60))
    calibrated_ref_n = body.get("calibrated_ref_n")

    fusion = TIMDREVFusion()
    predict = TIMDREVPredict()

    try:
        if calibrated_ref_n:
            n = int(calibrated_ref_n)
            fusion.calibrate([arr[:n] for arr in sensor_arrays])
            E, Z = fusion.fuse_calibrated(t, sensor_arrays)
        else:
            E, Z = fusion.fuse(t, sensor_arrays)

        tw_idx, tw_z = fusion.twist(t, E)
        tr_sl, tr_z = fusion.trend(t, E, window=window)
        an_idx, an_z = fusion.anomalies(E)
        periods, r_score = fusion.rhythm(E)
        score = fusion.fusion_score(tw_z, tr_z, an_z, r_score)

        ttf, ttf_lin, ttf_exp = predict.predict_failure(t, E, threshold=threshold, window=window)
        health = predict.health_score(E, threshold=threshold, window=window)
    except Exception as exc:  # noqa: BLE001 - czytelny blad do dashboardu, nie 500 bez opisu
        return jsonify({"error": f"blad analizy: {exc}"}), 400

    return jsonify({
        "t": t.tolist(),
        "E": E.tolist(),
        "sensor_names": sensor_names,
        "twist_idx": tw_idx.tolist(),
        "trend_slopes": tr_sl.tolist(),
        "anomaly_idx": an_idx.tolist(),
        "rhythm_periods": periods,
        "rhythm_score": float(r_score),
        "fusion_score": float(score),
        "ttf": _clean(ttf),
        "ttf_linear": _clean(ttf_lin),
        "ttf_exp": _clean(ttf_exp),
        "health_score": float(health),
        "threshold": threshold,
        "window": window,
        "calibrated_ref_n": calibrated_ref_n,
    })


@app.route("/api/vehicle", methods=["POST"])
def api_vehicle():
    """
    Body (JSON): {"battery": {...}, "motor": {...}, "charging": {...}}
    - 1 do 3 kluczy z SUBSYSTEMS (timdr_ev_vehicle.SUBSYSTEMS), kazdy
      obiekt to (podzbior) wynik z /api/analyze - wymagane pola
      "health_score" i "ttf" (dashboard przekazuje je wprost).

    Zwraca zagregowany wynik pojazdu - patrz timdr_ev_vehicle.py po
    uzasadnienie zasady "najslabsze ogniwo" (nie srednia).
    """
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict) or not body:
        return jsonify({"error": "wymagany obiekt {podsystem: wynik_analizy, ...}, co najmniej 1 podsystem"}), 400

    try:
        result = vehicle_health(body)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)


def _seconds_since(iso_timestamp):
    try:
        ts = datetime.fromisoformat(iso_timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except (TypeError, ValueError):
        return None


@app.route("/api/monitor/status")
def api_monitor_status():
    """
    Zwraca ostatni stan zapisany przez `monitor_ev.py` (ciagly albo
    okresowy) do `STATUS_FILE` - PER PODSYSTEM (battery/motor/charging,
    kazdy opcjonalny - pojazd moze akurat nie miec dzialajacego zrodla
    danych ladowania, np.) plus zagregowany wynik pojazdu.

    Dla kazdego AKTYWNEGO podsystemu liczy DWA NIEZALEZNE sygnaly "na
    zywo" (identyczna logika i uzasadnienie co /api/monitor/status w
    TIMDR-Industrial-Predict, zastosowana OSOBNO per podsystem - adapter
    CAN/OBD baterii moze sie rozlaczyc niezaleznie od adaptera silnika):
      - `checker_online`: czy PETLA monitor_ev.py w ogole jeszcze dziala
        dla tego podsystemu (swiezosc `timestamp` calego sprawdzenia).
      - `data_live`: czy ZRODLO DANYCH tego podsystemu (ev_can_source.py/
        adapter) faktycznie jeszcze dostarcza nowe probki (swiezosc
        `last_data_change`, aktualizowane w monitor_ev.py TYLKO gdy
        n_samples realnie wzrosnie).
    Oba pola sa `None`, gdy `interval` nie jest znany (monitor_ev.py w
    trybie `--once`/cron).
    """
    if not os.path.exists(STATUS_FILE):
        return jsonify({"running": False})
    try:
        with open(STATUS_FILE) as f:
            status = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return jsonify({"running": True, "error": f"chwilowy blad odczytu: {exc}"}), 200
    status["running"] = True

    interval = status.get("interval")
    for sub, sub_status in status.get("subsystems", {}).items():
        if interval:
            threshold = max(3 * float(interval), 15.0)
            age_checker = _seconds_since(sub_status.get("timestamp"))
            age_data = _seconds_since(sub_status.get("last_data_change"))
            sub_status["checker_online"] = (age_checker is not None and age_checker < threshold)
            sub_status["data_live"] = (age_data is not None and age_data < threshold)
            sub_status["seconds_since_data_change"] = age_data
        else:
            sub_status["checker_online"] = None
            sub_status["data_live"] = None
            sub_status["seconds_since_data_change"] = None

    return jsonify(status)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
