"""Scenarios: a 14-day operating history for the dashboard and random
short scenarios for the Monte Carlo performance study."""
from datetime import datetime

import numpy as np

from .simulator import Faults, Leak, OpEvent

DAY = 86400
H = 3600
START = datetime(2026, 10, 1, 0, 0, 0)


def t_(day, hh, mm=0, ss=0):
    return int(day * DAY + hh * H + mm * 60 + ss)


def main_scenario():
    """14 days. Day 0 is commissioning (leak-free) and is used to calibrate."""
    ev = []
    # daily pump rotation at 07:00
    for d in range(0, 14):
        ev.append(OpEvent(t_(d, 7), "cambio_bomba"))
    ev += [
        # day 0 (commissioning): representative operating changes
        OpEvent(t_(0, 6), "cambio_caudal", 1.05), OpEvent(t_(0, 10), "cambio_caudal", 1.00),
        OpEvent(t_(0, 14), "batch_agua", 2 * H),
        OpEvent(t_(0, 18), "ajuste_valvula", 31), OpEvent(t_(0, 22), "ajuste_valvula", 30),
        # day 1
        OpEvent(t_(1, 8), "cambio_caudal", 1.08), OpEvent(t_(1, 20), "cambio_caudal", 1.00),
        # day 2
        OpEvent(t_(2, 3), "batch_agua", 3 * H),
        OpEvent(t_(2, 12), "ajuste_valvula", 32),
        OpEvent(t_(2, 14), "cambio_caudal", 0.90), OpEvent(t_(2, 18), "cambio_caudal", 1.00),
        # day 4: deep turndown -> deposition-velocity alert
        OpEvent(t_(4, 9), "cambio_caudal", 0.80), OpEvent(t_(4, 9, 40), "cambio_caudal", 1.00),
        OpEvent(t_(4, 22), "batch_agua", 2 * H),
        # day 5
        OpEvent(t_(5, 15), "cambio_caudal", 1.06), OpEvent(t_(5, 23), "cambio_caudal", 1.00),
        # day 6
        OpEvent(t_(6, 10), "ajuste_valvula", 30),
        OpEvent(t_(6, 16), "batch_agua", 2 * H),
        # day 7: rate change in the middle of the gradual leak
        OpEvent(t_(7, 3, 30), "cambio_caudal", 0.92), OpEvent(t_(7, 6), "cambio_caudal", 1.00),
        # day 8: high-rate day -> MAOP envelope alert at the pump discharge
        OpEvent(t_(8, 5), "batch_agua", 3 * H),
        OpEvent(t_(8, 13), "cambio_caudal", 1.10), OpEvent(t_(8, 17), "cambio_caudal", 1.00),
        # day 9
        OpEvent(t_(9, 15), "ajuste_valvula", 31),
        # day 10: leak while a water batch is in the line
        OpEvent(t_(10, 11), "batch_agua", 3 * H),
        # day 11
        OpEvent(t_(11, 8), "cambio_caudal", 0.90), OpEvent(t_(11, 12), "cambio_caudal", 1.00),
        OpEvent(t_(11, 20), "batch_agua", 2 * H),
        # day 13
        OpEvent(t_(13, 9), "cambio_caudal", 1.05), OpEvent(t_(13, 21), "cambio_caudal", 1.00),
    ]
    leaks = [
        Leak("F-01", t_(3, 10, 15), 52.3, 0.040, "repentina", 45 * 60),
        Leak("F-02", t_(7, 2), 23.6, 0.015, "gradual", 5 * H, ramp_s=3 * H),
        Leak("F-03", t_(10, 12, 20), 67.8, 0.012, "repentina", 60 * 60),
        Leak("F-04", t_(12, 16, 40), 8.4, 0.025, "repentina", 40 * 60),
    ]
    faults = Faults(spikes=[(1, t_(5, 11), 3.0)],
                    flatlines=[(4, t_(9, 4), t_(9, 7))])
    return 14 * DAY, ev, leaks, faults


def random_scenario(rng, size_frac, kind, duration_s=6 * H, leak=True, idx=0):
    """Short scenario with random operations and (optionally) one leak."""
    ev = []
    # one pump rotation and one rate change at random times
    ev.append(OpEvent(int(rng.uniform(0.2, 0.9) * duration_s), "cambio_bomba"))
    t_rate = int(rng.uniform(0.15, 0.6) * duration_s)
    new = float(rng.choice([0.92, 0.95, 1.05, 1.08]))
    ev.append(OpEvent(t_rate, "cambio_caudal", new))
    ev.append(OpEvent(t_rate + int(rng.uniform(1, 2) * H), "cambio_caudal", 1.0))
    if rng.random() < 0.4:
        ev.append(OpEvent(int(rng.uniform(0.0, 0.5) * duration_s), "batch_agua", int(rng.uniform(1, 2) * H)))
    if rng.random() < 0.3:
        ev.append(OpEvent(int(rng.uniform(0.1, 0.9) * duration_s), "ajuste_valvula", float(rng.choice([29, 31, 32]))))
    leaks = []
    if leak:
        start = int(rng.uniform(2.0, 3.0) * H)
        km = float(np.round(rng.uniform(3, 77), 1))
        ramp = 2 if kind == "repentina" else int(1.5 * H)
        leaks.append(Leak(f"MC-{idx:03d}", start, km, size_frac, kind, duration_s - start - 120, ramp_s=ramp))
    return duration_s, ev, leaks, Faults()
