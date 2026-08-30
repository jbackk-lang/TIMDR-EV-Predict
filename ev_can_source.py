"""
ev_can_source.py — most z prawdziwego pojazdu (OBD-II albo surowy CAN) do
formatu CSV oczekiwanego przez monitor_ev.py
====================================================================
DWA niezalezne backendy, bo dla pojazdow elektrycznych maja realnie
rozne zastosowania - patrz "UCZCIWA GRANICA" ponizej po wyjasnienie
KIEDY ktory ma sens:

  --backend obd   - python-obd (ELM327/ISO 15765-4 CAN pod spodem).
                    DZIALA na kazdym aucie z zlaczem OBD-II, ale
                    STANDARDOWE PID-y sa w praktyce prawie wylacznie
                    silnikowe/emisyjne (SPE/ICE) - dla czystego BEV
                    (bez silnika spalinowego) wiekszosc z nich zwraca
                    brak danych. Realnie uzyteczne, ustandaryzowane
                    PID-y dla hybryd/EV w SAE J1979 to w praktyce
                    tylko `HYBRID_BATTERY_REMAINING` (0x5B, % pojemnosci
                    pakietu) i `CONTROL_MODULE_VOLTAGE` (0x42) - obydwa
                    NAPRAWDE istnieja w python-obd (zweryfikowano:
                    `obd.commands.HYBRID_BATTERY_REMAINING` istnieje),
                    ale to bardzo waski wycinek tego, co faktycznie
                    dzieje sie w baterii/silniku/ladowaniu.

  --backend can   - surowy CAN (python-can, dowolny interfejs:
                    socketcan/vcan na Linuksie, pcan/kvaser/vector/ixxat
                    na Windows/Linux z odpowiednim sterownikiem) +
                    dekodowanie sygnalow z pliku DBC (cantools). To jest
                    REALNA droga do bogatych danych EV (SOC, napiecie/
                    prad pakietu, temperatura/obroty silnika, stan
                    ladowania) - ale WYMAGA pliku DBC danego producenta/
                    modelu (proprietarne ID-y ramek CAN, nie
                    ustandaryzowane jak OBD-II PID-y). Bez DBC surowe
                    ramki CAN sa nieczytelnymi bajtami.

UCZCIWA GRANICA TEGO, CO JEST TU "REALNE" (analogicznie do
obd_source.py w TIMDR-Industrial-Predict): backend `obd` uzywa tej samej,
niezmodyfikowanej biblioteki `python-obd`, ktora w tamtym repo byla juz
zweryfikowana wobec prawdziwego, niezaleznego emulatora protokolu ELM327
(`ELM327-emulator`) - kod tutaj to bezposrednia adaptacja tamtego,
zweryfikowanego wzorca (inna domyslna lista PID-ow), NIE ponownie
testowana w tej sesji wobec emulatora z braku czasu - jesli cos w samej
integracji z python-obd nie dziala, to byloby identyczne z juz znanym,
dzialajacym kodem. Backend `can` uzyto NAPRAWDE, niezmodyfikowanej
biblioteki `python-can` (interfejs `virtual` - prawdziwa, wbudowana w
te biblioteke szyna testowa, nie napisana na potrzeby tej sesji) +
`cantools` do dekodowania wedlug prawdziwego formatu DBC - zweryfikowano
bezposrednio w tej sesji (patrz `test_ev_can_source.py`): wyslane ramki
CAN sa poprawnie dekodowane na nazwane sygnaly i zapisywane do CSV.
W OBU przypadkach: NIE testowano wobec fizycznego pojazdu (brak
dostepu w tym środowisku) - to test protokolu/kodu integracyjnego,
nie test na prawdziwym aucie.

PRZYKLADY:
  OBD-II, prawdziwy adapter (Linux):
    python ev_can_source.py --backend obd --port /dev/ttyUSB0 --csv battery_live.csv --interval 1
  Surowy CAN, prawdziwa magistrala (Linux, SocketCAN):
    python ev_can_source.py --backend can --channel can0 --interface socketcan \\
        --dbc moj_pojazd.dbc --signals SOC PackVoltage PackCurrent --csv battery_live.csv
  Surowy CAN, wirtualna szyna (test bez sprzetu, patrz test_ev_can_source.py):
    python ev_can_source.py --backend can --channel test --interface virtual \\
        --dbc moj_pojazd.dbc --signals SOC PackVoltage --csv battery_live.csv --n 20
"""
import argparse
import csv
import os
import sys
import time

DEFAULT_OBD_PIDS = ["HYBRID_BATTERY_REMAINING", "CONTROL_MODULE_VOLTAGE", "SPEED"]


def _csv_writer(path):
    """Otwiera CSV do dopisywania (tworzy z naglowkiem, jesli nie istnieje) -
    ten sam format co obd_source.py: kolumna `t` + jedna kolumna na sygnal."""
    file_exists = os.path.exists(path) and os.path.getsize(path) > 0
    f = open(path, "a", newline="")
    return f, csv.writer(f), file_exists


# ---------------------------------------------------------------------------
# Backend OBD-II (python-obd)
# ---------------------------------------------------------------------------

def run_obd(args):
    import obd

    cmds = []
    for name in args.pids:
        cmd = getattr(obd.commands, name, None)
        if cmd is None:
            print(f"UWAGA: PID '{name}' nie istnieje w python-obd - pomijam.", file=sys.stderr)
            continue
        cmds.append(cmd)
    if not cmds:
        print("BLAD: zaden z podanych PID-ow nie istnieje w python-obd.", file=sys.stderr)
        sys.exit(1)

    conn = obd.OBD(args.port, fast=args.fast, timeout=10)
    if not conn.is_connected():
        print(f"BLAD: nie udalo sie polaczyc z {args.port} (status: {conn.status()})", file=sys.stderr)
        sys.exit(1)
    print(f"Polaczono (OBD-II): {args.port}  status={conn.status()}  protokol={conn.protocol_name()}")

    f, writer, file_exists = _csv_writer(args.csv)
    if not file_exists:
        writer.writerow(["t"] + [c.name for c in cmds])

    t0 = time.time()
    i = 0
    try:
        while args.n is None or i < args.n:
            row_t = time.time() - t0
            values = []
            for c in cmds:
                r = conn.query(c)
                v = r.value
                if hasattr(v, "magnitude"):
                    v = v.magnitude
                if v is None:
                    v = float("nan")
                values.append(v)
            writer.writerow([row_t] + values)
            f.flush()
            print(f"  t={row_t:6.2f}s  " + "  ".join(f"{c.name}={v}" for c, v in zip(cmds, values)))
            i += 1
            if args.n is None or i < args.n:
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nZatrzymano.")
    finally:
        f.close()
        conn.close()


# ---------------------------------------------------------------------------
# Backend CAN + DBC (python-can + cantools)
# ---------------------------------------------------------------------------

def run_can(args):
    import can
    import cantools

    db = cantools.database.load_file(args.dbc)

    # Zbuduj mape: nazwa sygnalu -> (frame_id wiadomosci, obiekt Message) -
    # trzeba wiedziec, w KTOREJ ramce CAN szukac kazdego zadanego sygnalu,
    # zeby nie dekodowac na sile kazdej odebranej ramki.
    signal_to_message = {}
    for message in db.messages:
        for sig in message.signals:
            if sig.name in args.signals:
                signal_to_message[sig.name] = message
    missing = [s for s in args.signals if s not in signal_to_message]
    if missing:
        print(f"BLAD: sygnaly nie znalezione w DBC '{args.dbc}': {missing}", file=sys.stderr)
        sys.exit(1)
    relevant_frame_ids = {m.frame_id for m in signal_to_message.values()}

    bus = can.interface.Bus(channel=args.channel, interface=args.interface, bitrate=args.bitrate)
    print(f"Polaczono (CAN): interface={args.interface} channel={args.channel} "
          f"sledzone ramki: {sorted(relevant_frame_ids)}")

    f, writer, file_exists = _csv_writer(args.csv)
    if not file_exists:
        writer.writerow(["t"] + args.signals)

    latest = {name: float("nan") for name in args.signals}
    t0 = time.time()
    i = 0
    try:
        while args.n is None or i < args.n:
            msg = bus.recv(timeout=args.timeout)
            if msg is None:
                continue
            if msg.arbitration_id not in relevant_frame_ids:
                continue
            message_def = db.get_message_by_frame_id(msg.arbitration_id)
            try:
                decoded = message_def.decode(msg.data)
            except Exception as exc:  # noqa: BLE001 - ramka innego formatu/uszkodzona, nie przerywaj
                print(f"UWAGA: nie udalo sie zdekodowac ramki {hex(msg.arbitration_id)}: {exc}", file=sys.stderr)
                continue

            updated = False
            for name in args.signals:
                if name in decoded:
                    latest[name] = float(decoded[name])
                    updated = True
            if not updated:
                continue

            row_t = time.time() - t0
            writer.writerow([row_t] + [latest[name] for name in args.signals])
            f.flush()
            print(f"  t={row_t:6.2f}s  " + "  ".join(f"{name}={latest[name]}" for name in args.signals))
            i += 1
    except KeyboardInterrupt:
        print("\nZatrzymano.")
    finally:
        f.close()
        bus.shutdown()


def build_parser():
    ap = argparse.ArgumentParser(description="Most OBD-II/CAN -> CSV dla monitor_ev.py")
    ap.add_argument("--backend", choices=["obd", "can"], required=True)
    ap.add_argument("--csv", required=True, help="Docelowy plik CSV (dopisywany, tworzony jesli nie istnieje)")
    ap.add_argument("--interval", type=float, default=1.0, help="[obd] sekund miedzy odczytami")
    ap.add_argument("--n", type=int, default=None, help="Liczba odczytow (domyslnie: bez limitu, do Ctrl+C)")

    g_obd = ap.add_argument_group("backend obd")
    g_obd.add_argument("--port", help="[obd] Port szeregowy adaptera (np. /dev/ttyUSB0, COM5)")
    g_obd.add_argument("--pids", nargs="*", default=DEFAULT_OBD_PIDS,
                        help="[obd] Lista nazw PID-ow python-obd (domyslnie: HYBRID_BATTERY_REMAINING, "
                             "CONTROL_MODULE_VOLTAGE, SPEED - patrz docstring modulu, dlaczego to tak malo)")
    g_obd.add_argument("--fast", action="store_true", help="[obd] Tryb 'fast' python-obd")

    g_can = ap.add_argument_group("backend can")
    g_can.add_argument("--channel", help="[can] Kanal CAN (np. can0, vcan0, PCAN_USBBUS1)")
    g_can.add_argument("--interface", default="socketcan",
                        help="[can] Interfejs python-can (socketcan/pcan/kvaser/vector/ixxat/virtual, domyslnie socketcan)")
    g_can.add_argument("--bitrate", type=int, default=500000, help="[can] Bitrate magistrali (domyslnie 500000)")
    g_can.add_argument("--dbc", help="[can] Plik DBC z definicjami ramek/sygnalow (wymagany dla --backend can)")
    g_can.add_argument("--signals", nargs="*", default=[], help="[can] Nazwy sygnalow z DBC do wyodrebnienia")
    g_can.add_argument("--timeout", type=float, default=5.0, help="[can] Timeout pojedynczego odbioru ramki (s)")
    return ap


def main():
    args = build_parser().parse_args()
    if args.backend == "obd":
        if not args.port:
            print("BLAD: --backend obd wymaga --port.", file=sys.stderr)
            sys.exit(1)
        run_obd(args)
    else:
        if not args.channel or not args.dbc or not args.signals:
            print("BLAD: --backend can wymaga --channel, --dbc i co najmniej 1 --signals.", file=sys.stderr)
            sys.exit(1)
        run_can(args)


if __name__ == "__main__":
    main()
