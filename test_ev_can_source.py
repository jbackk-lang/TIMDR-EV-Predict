"""
test_ev_can_source.py — weryfikacja backendu CAN (ev_can_source.py) wobec
PRAWDZIWEJ (nie napisanej na potrzeby tej sesji), wbudowanej w python-can
magistrali wirtualnej ('virtual' interface) i PRAWDZIWEGO dekodowania
cantools wedlug pliku test_vehicle.dbc.

UCZCIWA GRANICA: to test protokolu/kodu integracyjnego (prawdziwe ramki
CAN, prawdziwy DBC, prawdziwe biblioteki can/cantools) - NIE test na
fizycznej magistrali/pojezdzie (niedostepne w tym srodowisku). Backend
`obd` w ev_can_source.py NIE jest tu testowany (wymagałby fizycznego
adaptera albo ELM327-emulator - patrz UCZCIWA GRANICA w docstringu
ev_can_source.py).
"""
import argparse
import csv
import os
import threading
import time

import can
import cantools
import pytest

from ev_can_source import run_can

DBC_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_vehicle.dbc")


def _make_args(csv_path, signals, n, channel):
    return argparse.Namespace(
        channel=channel, interface="virtual", bitrate=500000,
        dbc=DBC_PATH, signals=signals, timeout=2.0, n=n, csv=csv_path,
    )


def test_run_can_dekoduje_prawdziwe_ramki_z_wirtualnej_magistrali(tmp_path):
    csv_path = str(tmp_path / "battery_live.csv")
    channel = "timdr-ev-test-1"
    db = cantools.database.load_file(DBC_PATH)
    bms = db.get_message_by_name("BMS_Status")

    # nadajnik dziala w osobnym watku, zeby run_can() (blokujacy bus.recv())
    # mial na co czekac - to PRAWDZIWA komunikacja miedzyprocesowa/watkowa
    # przez wirtualna magistrale can, nie zamockowana funkcja.
    sent_values = [
        {"SOC": 91, "PackVoltage": 360.0, "PackCurrent": 5.0},
        {"SOC": 90, "PackVoltage": 358.5, "PackCurrent": 6.2},
        {"SOC": 89, "PackVoltage": 357.1, "PackCurrent": 7.8},
    ]

    def sender():
        tx_bus = can.interface.Bus(channel=channel, interface="virtual")
        time.sleep(0.2)  # daj odbiorcy czas na start bus.recv()
        for vals in sent_values:
            data = bms.encode(vals)
            tx_bus.send(can.Message(arbitration_id=bms.frame_id, data=data, is_extended_id=False))
            time.sleep(0.1)
        tx_bus.shutdown()

    args = _make_args(csv_path, ["SOC", "PackVoltage", "PackCurrent"], n=len(sent_values), channel=channel)
    t = threading.Thread(target=sender)
    t.start()
    run_can(args)
    t.join(timeout=5)

    assert os.path.exists(csv_path)
    with open(csv_path) as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == len(sent_values)
    for row, expected in zip(rows, sent_values):
        assert float(row["SOC"]) == pytest.approx(expected["SOC"])
        assert float(row["PackVoltage"]) == pytest.approx(expected["PackVoltage"], abs=0.2)
        assert float(row["PackCurrent"]) == pytest.approx(expected["PackCurrent"], abs=0.2)


def test_run_can_ignoruje_ramki_spoza_zadanych_sygnalow(tmp_path):
    """Ramka Motor_Status (inne frame_id) NIE powinna trafic do CSV, jesli
    prosimy tylko o sygnaly z BMS_Status."""
    csv_path = str(tmp_path / "battery_live2.csv")
    channel = "timdr-ev-test-2"
    db = cantools.database.load_file(DBC_PATH)
    bms = db.get_message_by_name("BMS_Status")
    motor = db.get_message_by_name("Motor_Status")

    def sender():
        tx_bus = can.interface.Bus(channel=channel, interface="virtual")
        time.sleep(0.2)
        # ramka NIEZWIAZANA z zadanymi sygnalami - powinna byc zignorowana
        tx_bus.send(can.Message(
            arbitration_id=motor.frame_id,
            data=motor.encode({"MotorRPM": 4000, "MotorTemp": 70}),
            is_extended_id=False,
        ))
        time.sleep(0.1)
        # ramka WLASCIWA
        tx_bus.send(can.Message(
            arbitration_id=bms.frame_id,
            data=bms.encode({"SOC": 50, "PackVoltage": 340.0, "PackCurrent": 1.0}),
            is_extended_id=False,
        ))
        tx_bus.shutdown()

    args = _make_args(csv_path, ["SOC", "PackVoltage", "PackCurrent"], n=1, channel=channel)
    t = threading.Thread(target=sender)
    t.start()
    run_can(args)
    t.join(timeout=5)

    with open(csv_path) as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert float(rows[0]["SOC"]) == pytest.approx(50)
