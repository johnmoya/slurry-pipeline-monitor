"""Pipeline, fluid and instrumentation parameters for the synthetic case study.

All values describe a fictitious long-distance copper-concentrate pipeline.
They are order-of-magnitude realistic, not taken from any real operation.
"""
from dataclasses import dataclass

import numpy as np

G = 9.81  # m/s2


@dataclass
class PipelineConfig:
    # Geometry
    length_km: float = 80.0
    diameter_m: float = 0.20          # inner diameter
    cell_m: float = 500.0             # spatial resolution of the hydraulic model

    # Elevation profile (km, m a.s.l.): plant -> ridge -> filter plant
    profile_km: tuple = (0, 10, 25, 35, 45, 55, 65, 72, 80)
    profile_z: tuple = (900, 1020, 1150, 1080, 950, 880, 760, 700, 600)

    # Pressure transmitters (id, name, km). PT-01 = pump discharge, PT-08 = terminal
    stations: tuple = (
        ("PT-01", "Estación de Bombeo PS-1", 0.0),
        ("PT-02", "Monitoreo km 12", 12.0),
        ("PT-03", "Monitoreo km 25", 25.0),
        ("PT-04", "Monitoreo km 38", 38.0),
        ("PT-05", "Monitoreo km 50", 50.0),
        ("PT-06", "Monitoreo km 62", 62.0),
        ("PT-07", "Monitoreo km 72", 72.0),
        ("PT-08", "Terminal / Planta de Filtros", 80.0),
    )
    # Maximum allowable operating pressure by station (bar), set by wall thickness
    maop_bar: tuple = (175, 140, 110, 100, 90, 80, 70, 60)
    maop_alert_frac: float = 0.90     # alert when p > 90 % MAOP
    min_pressure_bar: float = 3.0     # keep the line full (avoid slack flow)

    # Hydraulics
    friction_true: float = 0.0150     # Darcy friction factor (true pipe)
    friction_segment_var: float = 0.03  # +-3% segment-wise roughness variation
    wave_speed: float = 1000.0        # m/s, used by the detectors
    wave_speed_true: float = 950.0    # m/s, "real" value (slightly unknown)
    wave_atten_km: float = 40.0       # amplitude e-folding distance of pressure waves
    relax_s: float = 60.0             # transient -> new steady state time constant

    # Fluid
    rho_water: float = 1000.0
    rho_slurry: float = 1600.0
    rho_solids: float = 4200.0        # copper concentrate SG ~4.2

    # Operation
    q_nom_m3s: float = 0.055          # ~1.75 m/s in a 0.20 m line
    p_out_nom_bar: float = 30.0       # terminal back-pressure (choke station)
    v_deposition: float = 1.30        # m/s, from (fictitious) test work
    v_margin: float = 1.10            # alert when v < margin * v_deposition

    # Energy
    pump_efficiency: float = 0.80
    # Reference grid factor (tCO2e/MWh). Placeholder: replace with the
    # current official factor for the Chilean grid before quoting results.
    emission_factor_t_mwh: float = 0.30

    # Instrumentation
    p_noise_bar: float = 0.02         # white noise of pressure transmitters
    q_noise_frac: float = 0.005       # white noise of magnetic flowmeters (of q_nom)
    q_drift_frac: float = 0.002       # correlated meter noise (of q_nom)
    q_drift_tau_s: float = 60.0
    q_in_bias_frac: float = 0.0
    q_out_bias_frac: float = 0.0035   # outlet meter reads +0.35 %
    q_process_frac: float = 0.0015    # real flow fluctuation (pump pulsation)
    q_process_tau_s: float = 30.0
    rho_noise: float = 5.0            # kg/m3

    # Simulation
    dt_coarse_s: int = 20             # resolution of the quasi-steady solver
    roughness_seed: int = 7           # the physical pipe is the same in every run

    @property
    def length_m(self) -> float:
        return self.length_km * 1000.0

    @property
    def area(self) -> float:
        return np.pi * self.diameter_m ** 2 / 4.0

    @property
    def station_ids(self):
        return [s[0] for s in self.stations]

    @property
    def station_km(self) -> np.ndarray:
        return np.array([s[2] for s in self.stations])

    @property
    def station_m(self) -> np.ndarray:
        return self.station_km * 1000.0

    @property
    def v_nom(self) -> float:
        return self.q_nom_m3s / self.area

    def elevation(self, km):
        return np.interp(km, self.profile_km, self.profile_z)

    def solids_vol_frac(self, rho):
        return (np.asarray(rho) - self.rho_water) / (self.rho_solids - self.rho_water)
