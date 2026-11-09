"""Quasi-steady hydraulic model shared by the simulator ("true pipe") and the
digital twin ("model pipe").

Pressure is integrated backwards from the terminal, cell by cell:

    p_i = p_{i+1} + rho_i * g * (z_{i+1} - z_i) + rho_i * f_i * dx / D * v_i|v_i| / 2

Batches (water / slurry) are tracked as plug flow using the cumulative
volume pumped into the line.
"""
import numpy as np

from .config import G, PipelineConfig


class Pipe:
    def __init__(self, cfg: PipelineConfig, friction_cells=None):
        self.cfg = cfg
        self.dx = cfg.cell_m
        self.x_nodes = np.arange(0.0, cfg.length_m + self.dx / 2, self.dx)
        self.n_cells = len(self.x_nodes) - 1
        self.x_mid = 0.5 * (self.x_nodes[1:] + self.x_nodes[:-1])
        self.z_nodes = cfg.elevation(self.x_nodes / 1000.0)
        self.dz = np.diff(self.z_nodes)
        self.area = cfg.area
        self.D = cfg.diameter_m
        if friction_cells is None:
            friction_cells = np.full(self.n_cells, cfg.friction_true)
        self.f = np.asarray(friction_cells, dtype=float)
        self.station_nodes = np.rint(cfg.station_m / self.dx).astype(int)

    @classmethod
    def true_pipe(cls, cfg: PipelineConfig):
        """Real pipe: segment-wise roughness differs a little from design."""
        rng = np.random.default_rng(cfg.roughness_seed)
        n = int(round(cfg.length_m / cfg.cell_m))
        # roughness varies in ~5 km segments
        seg = rng.uniform(-cfg.friction_segment_var, cfg.friction_segment_var, size=n // 10 + 1)
        f = cfg.friction_true * (1.0 + np.repeat(seg, 10)[:n])
        return cls(cfg, f)

    def cell_drops(self, q_cells, rho_cells, friction_scale=1.0):
        """Pressure drop across each cell (Pa), shape (..., n_cells)."""
        v = q_cells / self.area
        return (rho_cells * G * self.dz
                + rho_cells * (self.f * friction_scale) * self.dx / self.D * v * np.abs(v) / 2.0)

    def node_pressures(self, q_cells, rho_cells, p_out, friction_scale=1.0):
        """Pressure at every node (Pa), shape (..., n_nodes)."""
        c = self.cell_drops(q_cells, rho_cells, friction_scale)
        rev = np.cumsum(c[..., ::-1], axis=-1)[..., ::-1]
        zeros = np.zeros(rev.shape[:-1] + (1,))
        return np.concatenate([rev, zeros], axis=-1) + np.asarray(p_out)[..., None]

    def station_pressures(self, q_cells, rho_cells, p_out, friction_scale=1.0):
        return self.node_pressures(q_cells, rho_cells, p_out, friction_scale)[..., self.station_nodes]

    def leak_flow_cells(self, q_in, leak_km, q_leak):
        """Flow per cell when q_leak leaves the line at leak_km.

        q_in, q_leak: arrays (...,) ; returns (..., n_cells)."""
        downstream = (self.x_mid > leak_km * 1000.0).astype(float)
        return np.asarray(q_in)[..., None] - np.asarray(q_leak)[..., None] * downstream


def cumulative_volume(q_fine, dt=1.0):
    """Volume pumped (m3) at the start of each fine time step."""
    return np.concatenate([[0.0], np.cumsum(q_fine[:-1]) * dt])


def track_density(t_query, t_fine, q_fine, rho_in_fine, x_mid, area, rho_initial):
    """Plug-flow batch tracking.

    A parcel at position x at time t entered the line when the cumulative
    pumped volume was V(t) - A*x. Returns rho (len(t_query), n_cells)."""
    V = cumulative_volume(q_fine, t_fine[1] - t_fine[0])
    Vq = np.interp(t_query, t_fine, V)
    target = Vq[:, None] - area * x_mid[None, :]
    rho = np.interp(target.ravel(), V, rho_in_fine, left=rho_initial)
    return rho.reshape(target.shape)


def block_mean(x, n):
    """Mean over consecutive blocks of n samples along axis 0."""
    m = (len(x) // n) * n
    x = np.asarray(x)[:m]
    return x.reshape((-1, n) + x.shape[1:]).mean(axis=1)
