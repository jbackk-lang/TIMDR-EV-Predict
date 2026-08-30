import math

import pytest
from timdr_ev_vehicle import vehicle_health


def test_pusty_slownik_rzuca_czytelny_blad():
    with pytest.raises(ValueError):
        vehicle_health({})


def test_brakujace_pole_rzuca_czytelny_blad():
    with pytest.raises(ValueError):
        vehicle_health({"battery": {"health_score": 0.5}})  # brak "ttf"


def test_najslabsze_ogniwo_wygrywa_niezaleznie_od_pozostalych():
    """Bateria i ladowanie w idealnym stanie NIE powinny zamaskowac
    silnika tuz przed awaria - to sedno zasady 'weakest link' zamiast
    sredniej (patrz docstring modulu)."""
    res = vehicle_health({
        "battery": {"health_score": 0.99, "ttf": 100000.0},
        "motor": {"health_score": 0.05, "ttf": 12.0},
        "charging": {"health_score": 0.95, "ttf": float("inf")},
    })
    assert res["weakest_subsystem"] == "motor"
    assert res["vehicle_health_score"] == pytest.approx(0.05)
    assert res["soonest_subsystem"] == "motor"
    assert res["vehicle_ttf"] == pytest.approx(12.0)


def test_srednia_bylaby_falszywie_optymistyczna():
    """Kontrast wprost: gdyby uzyc sredniej zamiast minimum, wynik
    (0.99+0.05+0.95)/3 ~= 0.66 sugerowalby 'dobry' stan pojazdu, mimo
    silnika tuz przed awaria."""
    res = vehicle_health({
        "battery": {"health_score": 0.99, "ttf": 100000.0},
        "motor": {"health_score": 0.05, "ttf": 12.0},
        "charging": {"health_score": 0.95, "ttf": float("inf")},
    })
    naive_average = (0.99 + 0.05 + 0.95) / 3
    assert res["vehicle_health_score"] < naive_average


def test_dziala_na_podzbiorze_podsystemow():
    """Pojazd, ktory akurat sie nie laduje, moze nie miec danych
    'charging' - agregacja powinna dzialac na dostepnym podzbiorze."""
    res = vehicle_health({
        "battery": {"health_score": 0.7, "ttf": 200.0},
        "motor": {"health_score": 0.8, "ttf": 500.0},
    })
    assert res["weakest_subsystem"] == "battery"
    assert "charging" not in res["per_subsystem"]


def test_ttf_inf_nie_wygrywa_nad_ttf_skonczonym():
    res = vehicle_health({
        "battery": {"health_score": 0.9, "ttf": float("inf")},
        "motor": {"health_score": 0.2, "ttf": 300.0},
    })
    assert res["soonest_subsystem"] == "motor"
    assert res["vehicle_ttf"] == pytest.approx(300.0)


def test_wszystkie_ttf_inf_daje_vehicle_ttf_inf():
    res = vehicle_health({
        "battery": {"health_score": 0.9, "ttf": float("inf")},
        "motor": {"health_score": 0.8, "ttf": float("inf")},
    })
    assert math.isinf(res["vehicle_ttf"])
