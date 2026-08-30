"""
timdr_ev_vehicle.py — zagregowany wynik zdrowia calego pojazdu elektrycznego
====================================================================================
Laczy WYNIKI JUZ POLICZONE osobno dla kazdego podsystemu (bateria, silnik
elektryczny, ladowanie/siec - kazdy przez wlasne wywolanie
TIMDREVFusion/TIMDREVPredict, bo maja rozne czujniki, rozne skale E i,
dla baterii, inna metode fuzji - patrz demo_scenarios.CALIBRATED_REF_N)
w JEDEN wynik na poziomie pojazdu.

Zasada agregacji: "najslabsze ogniwo" (weakest link), nie srednia.
UZASADNIENIE (analogicznie do samego pojazdu w realnym swiecie): pojazd
z bateria w idealnym stanie (health=1.0) ale z silnikiem tuz przed
zwarciem (health=0.05) NIE jest pojazdem w "sredniowo dobrym" stanie
(0.53) - jest pojazdem, ktory zaraz stanie z powodu silnika. Usrednianie
zamaskowaloby krytyczny podsystem dobrym wynikiem pozostalych. To ta sama
logika co health_score() w TIMDREVPredict (bierze MEDIANE Z OSTATNIEGO
OKNA, nie cala historie) - tu analogicznie bierzemy MINIMUM po
podsystemach, nie srednia.

Podobnie: TTF calego pojazdu = MINIMUM z TTF podsystemow (pojazd stanie,
gdy PIERWSZY podsystem osiagnie awarie, nie gdy wszystkie razem).
"""

import math

SUBSYSTEMS = ("battery", "motor", "charging")


def vehicle_health(subsystem_results):
    """
    subsystem_results: dict {nazwa_podsystemu: {"health_score": float,
    "ttf": float, "E_last": float, "threshold": float}} - co najmniej
    jeden podsystem wymagany, ale NIE wszystkie trzy naraz (np. pojazd
    zaparkowany i nie ladujacy sie moze nie miec danych "charging" -
    agregacja dziala na dostepnym podzbiorze, patrz test).

    Zwraca dict:
      vehicle_health_score: MIN health_score po dostepnych podsystemach
      weakest_subsystem: nazwa podsystemu odpowiadajacego za powyzsze minimum
      vehicle_ttf: MIN ttf po dostepnych podsystemach (najszybsza awaria wygrywa)
      soonest_subsystem: nazwa podsystemu odpowiadajacego za powyzsze minimum TTF
      per_subsystem: echo wejscia (do wyswietlenia w dashboardzie)
    """
    if not subsystem_results:
        raise ValueError("subsystem_results nie moze byc puste - potrzeba co najmniej 1 podsystemu")

    for name, r in subsystem_results.items():
        for key in ("health_score", "ttf"):
            if key not in r:
                raise ValueError(f"podsystem '{name}': brakuje pola '{key}'")

    weakest_subsystem = min(subsystem_results, key=lambda k: subsystem_results[k]["health_score"])
    vehicle_health_score = subsystem_results[weakest_subsystem]["health_score"]

    def _ttf_sort_key(name):
        ttf = subsystem_results[name]["ttf"]
        return ttf if ttf is not None and math.isfinite(ttf) else float("inf")

    soonest_subsystem = min(subsystem_results, key=_ttf_sort_key)
    vehicle_ttf = subsystem_results[soonest_subsystem]["ttf"]

    return {
        "vehicle_health_score": float(vehicle_health_score),
        "weakest_subsystem": weakest_subsystem,
        "vehicle_ttf": vehicle_ttf,
        "soonest_subsystem": soonest_subsystem,
        "per_subsystem": subsystem_results,
    }
