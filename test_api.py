"""
test_api.py — testy REST API TIMDR-EV-Predict (Flask test client, bez
uruchamiania prawdziwego serwera). Uzupelnia recznie zweryfikowany
przebieg end-to-end (demo -> analyze -> vehicle), zeby ta sciezka byla
chroniona regresyjnie, nie tylko sprawdzona raz recznie.
"""

import json
from datetime import datetime, timezone

import pytest
from api import app
from demo_scenarios import SCENARIOS


@pytest.fixture
def client():
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.get_json()["status"] == "ok"


def test_scenarios_lista_ma_wszystkie_3_podsystemy(client):
    res = client.get("/api/scenarios")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data) == len(SCENARIOS)
    subsystems = {s["subsystem"] for s in data}
    assert subsystems == {"battery", "motor", "charging"}


def test_demo_nieznany_scenariusz_zwraca_400(client):
    res = client.get("/api/demo?scenario=nie_istnieje")
    assert res.status_code == 400


def _get_demo(client, scenario):
    res = client.get(f"/api/demo?scenario={scenario}")
    assert res.status_code == 200
    return res.get_json()


def _analyze(client, demo):
    body = {
        "t": demo["t"],
        "sensors": demo["sensors"],
        "threshold": demo["default_threshold"],
        "window": 60,
    }
    if demo.get("calibrated_ref_n"):
        body["calibrated_ref_n"] = demo["calibrated_ref_n"]
    res = client.post("/api/analyze", data=json.dumps(body), content_type="application/json")
    assert res.status_code == 200
    return res.get_json()


def test_analyze_pelen_przebieg_battery_real_nasa_kalibrowany(client):
    """Regresja dla znalezionego bledu: naiwny fuse() na realnych danych
    NASA B0047 daje E malejace (fizycznie odwrocone), kalibrowany -
    rosnace. To sprawdza droge PRZEZ API (nie tylko bezposrednio przez
    TIMDREVFusion jak w test_demo_scenarios.py)."""
    demo = _get_demo(client, "battery_real_nasa_b0047")
    assert demo["calibrated_ref_n"] == 5
    result = _analyze(client, demo)
    assert result["E"][-1] > result["E"][0]
    assert result["health_score"] == pytest.approx(0.0, abs=1e-6)  # po ~31% ubytku - juz krytyczny


def test_analyze_brak_wymaganych_pol_zwraca_400(client):
    res = client.post("/api/analyze", data=json.dumps({}), content_type="application/json")
    assert res.status_code == 400


def test_vehicle_agreguje_3_podsystemy_zasada_najslabszego_ogniwa(client):
    battery = _analyze(client, _get_demo(client, "battery_degradation"))
    motor = _analyze(client, _get_demo(client, "motor_winding_short"))
    charging = _analyze(client, _get_demo(client, "charging_connector_overheat"))

    res = client.post(
        "/api/vehicle",
        data=json.dumps({"battery": battery, "motor": motor, "charging": charging}),
        content_type="application/json",
    )
    assert res.status_code == 200
    veh = res.get_json()
    assert veh["vehicle_health_score"] == min(battery["health_score"], motor["health_score"], charging["health_score"])
    assert veh["weakest_subsystem"] in ("battery", "motor", "charging")


def test_vehicle_pusty_zwraca_400(client):
    res = client.post("/api/vehicle", data=json.dumps({}), content_type="application/json")
    assert res.status_code == 400


def test_vehicle_dziala_na_podzbiorze_1_podsystemu(client):
    battery = _analyze(client, _get_demo(client, "battery_degradation"))
    res = client.post(
        "/api/vehicle",
        data=json.dumps({"battery": battery}),
        content_type="application/json",
    )
    assert res.status_code == 200
    veh = res.get_json()
    assert veh["weakest_subsystem"] == "battery"


def test_dashboard_index_serwuje_html(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"TIMDR EV Predict" in res.data


def test_monitor_status_brak_pliku_zwraca_running_false(client, monkeypatch, tmp_path):
    """Jesli monitor_ev.py nigdy nie byl uruchomiony, endpoint powinien
    zwrocic 'brak monitoringu', nie blad - ten sam wzorzec co
    TIMDR-Industrial-Predict."""
    import api as api_module
    monkeypatch.setattr(api_module, "STATUS_FILE", str(tmp_path / "nie_istnieje.json"))
    res = client.get("/api/monitor/status")
    assert res.status_code == 200
    assert res.get_json()["running"] is False


def test_monitor_status_z_plikiem_liczy_checker_online_i_data_live(client, monkeypatch, tmp_path):
    import api as api_module
    status_file = tmp_path / "status.json"
    now = datetime.now(timezone.utc).isoformat()
    status_file.write_text(json.dumps({
        "timestamp": now,
        "interval": 5.0,
        "subsystems": {
            "battery": {"timestamp": now, "last_data_change": now, "health_score": 0.8, "ttf": None},
        },
        "vehicle": {"vehicle_health_score": 0.8, "weakest_subsystem": "battery"},
    }))
    monkeypatch.setattr(api_module, "STATUS_FILE", str(status_file))
    res = client.get("/api/monitor/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["running"] is True
    assert data["subsystems"]["battery"]["checker_online"] is True
    assert data["subsystems"]["battery"]["data_live"] is True


def test_monitor_status_once_mode_daje_none_dla_live_flag(client, monkeypatch, tmp_path):
    """interval=None (tryb --once/cron) - brak jednego stalego rytmu, wiec
    checker_online/data_live musza byc None, nie False (nie da sie
    stwierdzic 'nieaktywny' bez znanego oczekiwanego rytmu)."""
    import api as api_module
    status_file = tmp_path / "status.json"
    now = datetime.now(timezone.utc).isoformat()
    status_file.write_text(json.dumps({
        "timestamp": now,
        "interval": None,
        "subsystems": {"battery": {"timestamp": now, "last_data_change": now, "health_score": 0.8, "ttf": None}},
        "vehicle": None,
    }))
    monkeypatch.setattr(api_module, "STATUS_FILE", str(status_file))
    res = client.get("/api/monitor/status")
    data = res.get_json()
    assert data["subsystems"]["battery"]["checker_online"] is None
    assert data["subsystems"]["battery"]["data_live"] is None
