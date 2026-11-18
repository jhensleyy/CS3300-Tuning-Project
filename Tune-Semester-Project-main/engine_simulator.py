"""
Engine performance simulator.

Simulates RPM, manifold absolute pressure (MAP), throttle position (TPS),
estimated horsepower and torque across idle, cruise, acceleration (with and
without boost) and deceleration states.

Structure:
    EngineConfig      - the user's inputs plus values derived from them
    EngineSimulator   - calibration, the state machine, and HP/boost math
    torque_from_hp()  - torque calculator (Torque = HP x 5252 / RPM)
    BoostMap          - a boost curve loaded from maps/boost/*.json
    SimulationResult  - time series and peak stats returned by a run
    plot_result()     - optional matplotlib PNG export (MAP, RPM, HP/torque, TPS)
    print_summary()   - prints inputs, peaks and time in each state
    main()            - command-line entry point (prompts for inputs)

The simulator has no global state, no sleeping and no threads, so it can be
imported and called directly from tests or a web API:

    config = EngineConfig(elevation_m=1840, na_peak_hp=300,
                          max_boost_psi=20, redline_rpm=7000, idle_rpm=800)
    result = EngineSimulator(config, seed=42).run(duration_s=45)
    result.to_dict()  # JSON-ready

    # With a boost map
    sim = EngineSimulator(config, seed=42, boost_map=get_boost_map("early_spool"))
"""

from __future__ import annotations

import json
import random
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path

# --- Physical constants ---
KPA_TO_PSI = 0.14503773773  # 1 kPa = ~0.145 PSI

# --- Engine state parameters ---
# IDLE: throttle closed
IDLE_TPS = 0

# CRUISE: moderate throttle
CRUISE_TPS_MIN = 10
CRUISE_TPS_MAX = 30

# ACCELERATION: lower RPM bound and heavy throttle
ACCEL_RPM_MIN = 5000
ACCEL_TPS_MIN = 70
ACCEL_TPS_MAX = 100

# DECELERATION: throttle closed, manifold vacuum
DECEL_MAP_KPA = 20
DECEL_TPS = 0

# --- Calibration ratios (fraction of atmospheric pressure) ---
IDLE_MAP_RATIO = 0.3
CRUISE_MAP_MIN_RATIO = 0.4
CRUISE_MAP_MAX_RATIO = 0.7
NA_WOT_MAP_RATIO = 0.95  # MAP at wide-open throttle without boost

# HP is capped at this multiple of the naturally aspirated peak
HP_CAP_MULTIPLIER = 2.5

# Random noise added to MAP readings (PSI, +/-)
MAP_NOISE_PSI = 0.5

# --- Torque ---
# Torque (lb-ft) = HP x 5252 / RPM. 5252 is the RPM where HP and lb-ft are
# equal (33,000 / 2*pi)
HP_TORQUE_CONSTANT = 5252

# --- Boost maps ---
# Folder holding the boost map JSON files
BOOST_MAP_DIR = Path(__file__).resolve().parent / "maps" / "boost"

# --- Input limits (shared by the CLI and EngineConfig validation) ---
ELEVATION_MIN_M, ELEVATION_MAX_M = -400, 8000
BOOST_MAX_PSI = 45
REDLINE_MIN_RPM, REDLINE_MAX_RPM = 5000, 10000
IDLE_MIN_RPM, IDLE_MAX_RPM = 500, 1000

# --- State machine ---
# state = (min seconds, max seconds, states it can switch to)
# Each step, the state ends once its elapsed time exceeds a random whole
# number of seconds drawn from [min, max].
STATE_TRANSITIONS = {
    "idle":        (2, 5, ["cruise", "accel_na", "accel_boost"]),
    "cruise":      (3, 7, ["accel_boost", "accel_na", "decel", "idle"]),
    "accel_na":    (2, 5, ["cruise", "decel", "accel_boost", "idle"]),
    "accel_boost": (2, 5, ["cruise", "decel", "idle"]),
    "decel":       (2, 5, ["idle"]),
}


def calculate_atmospheric_pressure(elevation_m: float) -> float:
    """Atmospheric pressure (PSI) at an elevation, using the barometric formula."""
    p0_kpa = 101.325   # Sea level standard pressure (kPa)
    lapse = 0.0065     # Temperature lapse rate (K/m)
    t0 = 288.15        # Sea level standard temperature (K)
    g = 9.80665        # Gravitational acceleration (m/s^2)
    molar = 0.0289644  # Molar mass of air (kg/mol)
    r = 8.31447        # Universal gas constant (J/(mol*K))

    # Above the formula's valid range: avoid a math domain error
    if lapse * elevation_m >= t0:
        return 0.0

    pressure_kpa = p0_kpa * (1 - lapse * elevation_m / t0) ** (g * molar / (r * lapse))
    return pressure_kpa * KPA_TO_PSI


# --- Torque calculator ---

def torque_from_hp(hp: float, rpm: float) -> float:
    """Torque in lb-ft from horsepower at a given RPM. Returns 0 at RPM <= 0."""
    if rpm <= 0:
        return 0.0
    return hp * HP_TORQUE_CONSTANT / rpm


def hp_from_torque(torque_lbft: float, rpm: float) -> float:
    """Horsepower from torque in lb-ft at a given RPM (the inverse formula)."""
    return torque_lbft * rpm / HP_TORQUE_CONSTANT


# --- Boost map class and loaders ---
#
# Boost maps: target boost as a function of RPM, loaded from JSON files.
#
# A boost map says how much of the configured boost the turbo/supercharger
# delivers at each point in the rev range. Both axes are fractions, so one map
# works for any engine the user enters:
#
#     rpm_fraction    RPM / redline_rpm                       (0.0 to 1.0)
#     boost_fraction  share of the configured boost above      (0.0 to 1.0)
#                     atmospheric: 0.0 = atmospheric pressure,
#                     1.0 = max_boost_psi
#
# Between points the map is linearly interpolated. Below the first point and
# above the last point it holds the end value.
#
# JSON format (maps/boost/<name>.json):
#
#     {
#       "name": "early_spool",
#       "description": "Small turbo: full boost early, holds it to redline",
#       "points": [[0.40, 0.0], [0.55, 0.8], [0.65, 1.0], [1.0, 1.0]]
#     }

@dataclass(frozen=True)
class BoostMap:
    """A validated boost curve: (rpm_fraction, boost_fraction) points."""

    name: str
    points: tuple[tuple[float, float], ...]
    description: str = ""

    def __post_init__(self):
        if len(self.points) < 2:
            raise ValueError(f"boost map '{self.name}' needs at least 2 points")
        prev_rpm = None
        for rpm_frac, boost_frac in self.points:
            if not 0.0 <= rpm_frac <= 1.0:
                raise ValueError(
                    f"boost map '{self.name}': rpm fraction {rpm_frac} is outside 0-1")
            if not 0.0 <= boost_frac <= 1.0:
                raise ValueError(
                    f"boost map '{self.name}': boost fraction {boost_frac} is outside 0-1")
            if prev_rpm is not None and rpm_frac <= prev_rpm:
                raise ValueError(
                    f"boost map '{self.name}': rpm fractions must be strictly increasing")
            prev_rpm = rpm_frac

    def boost_fraction(self, rpm_fraction: float) -> float:
        """Share of configured boost (0-1) at a given RPM / redline."""
        first_rpm, first_boost = self.points[0]
        last_rpm, last_boost = self.points[-1]
        if rpm_fraction <= first_rpm:
            return first_boost
        if rpm_fraction >= last_rpm:
            return last_boost

        rpm_axis = [p[0] for p in self.points]
        i = bisect_right(rpm_axis, rpm_fraction)  # rpm_axis[i-1] <= x < rpm_axis[i]
        (x0, y0), (x1, y1) = self.points[i - 1], self.points[i]
        return y0 + (rpm_fraction - x0) / (x1 - x0) * (y1 - y0)

    def to_dict(self) -> dict:
        """JSON-serializable form (same shape as the map files)."""
        return {
            "name": self.name,
            "description": self.description,
            "points": [list(p) for p in self.points],
        }

    @classmethod
    def from_dict(cls, data: dict) -> BoostMap:
        if not isinstance(data, dict):
            raise ValueError("boost map must be a JSON object")
        try:
            points = tuple((float(rpm), float(boost)) for rpm, boost in data["points"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                "boost map needs a 'points' list of [rpm_fraction, boost_fraction] pairs"
            ) from exc
        return cls(name=str(data.get("name", "unnamed")),
                   points=points,
                   description=str(data.get("description", "")))


def load_boost_map(path: str | Path) -> BoostMap:
    """Load and validate one boost map JSON file."""
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data.setdefault("name", path.stem)
    return BoostMap.from_dict(data)


def list_boost_maps(directory: str | Path = BOOST_MAP_DIR) -> list[str]:
    """Names of the boost maps available in a folder (file names without .json)."""
    return sorted(p.stem for p in Path(directory).glob("*.json"))


def get_boost_map(name: str, directory: str | Path = BOOST_MAP_DIR) -> BoostMap:
    """Load a bundled boost map by name, e.g. get_boost_map("early_spool").

    Only names that exist in the folder are accepted, so a name coming from a
    web request can't be used to read other files.
    """
    available = list_boost_maps(directory)
    if name not in available:
        raise ValueError(
            f"unknown boost map '{name}'. Available: {', '.join(available) or 'none'}")
    return load_boost_map(Path(directory) / f"{name}.json")


@dataclass(frozen=True)
class EngineConfig:
    """User inputs, validated, plus values derived from them."""

    elevation_m: float
    na_peak_hp: float
    max_boost_psi: float  # absolute: atmospheric + boost
    redline_rpm: int
    idle_rpm: int

    def __post_init__(self):
        if not ELEVATION_MIN_M <= self.elevation_m <= ELEVATION_MAX_M:
            raise ValueError(
                f"elevation_m must be between {ELEVATION_MIN_M} and {ELEVATION_MAX_M}")
        if self.na_peak_hp < 1:
            raise ValueError("na_peak_hp must be at least 1")
        min_boost = round(self.atmospheric_psi)
        if not min_boost <= self.max_boost_psi <= BOOST_MAX_PSI:
            raise ValueError(
                f"max_boost_psi must be between {min_boost} and {BOOST_MAX_PSI} "
                f"at this elevation")
        if not REDLINE_MIN_RPM <= self.redline_rpm <= REDLINE_MAX_RPM:
            raise ValueError(
                f"redline_rpm must be between {REDLINE_MIN_RPM} and {REDLINE_MAX_RPM}")
        if not IDLE_MIN_RPM <= self.idle_rpm <= IDLE_MAX_RPM:
            raise ValueError(
                f"idle_rpm must be between {IDLE_MIN_RPM} and {IDLE_MAX_RPM}")

    # --- Derived values ---

    @property
    def atmospheric_psi(self) -> float:
        return calculate_atmospheric_pressure(self.elevation_m)

    @property
    def boost_active_psi(self) -> float:
        """MAP above this counts as boost."""
        return self.atmospheric_psi

    @property
    def rpm_at_max_boost(self) -> int:
        """Boost reaches its maximum at ~90% of redline."""
        rpm = int(self.redline_rpm * 0.9)
        return rpm if rpm >= ACCEL_RPM_MIN else ACCEL_RPM_MIN + 500

    @property
    def peak_hp_rpm(self) -> int:
        """Naturally aspirated peak HP is assumed at ~80% of redline."""
        rpm = int(self.redline_rpm * 0.8)
        return rpm if rpm >= ACCEL_RPM_MIN else ACCEL_RPM_MIN + 500

    @property
    def cruise_rpm_min(self) -> int:
        return int(self.idle_rpm * 1.5)

    @property
    def cruise_rpm_max(self) -> int:
        return int(ACCEL_RPM_MIN * 0.9)

    @property
    def decel_rpm_min(self) -> int:
        return self.idle_rpm + 100

    @property
    def decel_rpm_max(self) -> int:
        return ACCEL_RPM_MIN - 100


@dataclass
class SimulationResult:
    """Time series and peak statistics from one simulation run."""

    time_s: list[float] = field(default_factory=list)
    state: list[str] = field(default_factory=list)
    rpm: list[int] = field(default_factory=list)
    map_psi: list[float] = field(default_factory=list)
    tps: list[int] = field(default_factory=list)
    hp: list[float] = field(default_factory=list)
    torque_lbft: list[float] = field(default_factory=list)

    boost_map: str = "default"  # name of the boost map used

    peak_hp: float = 0.0
    rpm_at_peak_hp: int = 0
    map_at_peak_hp: float = 0.0
    peak_torque_lbft: float = 0.0
    rpm_at_peak_torque: int = 0
    peak_rpm: int = 0
    max_boost_psi: float | None = None  # None if boost was never reached

    def to_dict(self) -> dict:
        """JSON-serializable form, e.g. for a web API response."""
        return {
            "time_s": self.time_s,
            "state": self.state,
            "rpm": self.rpm,
            "map_psi": self.map_psi,
            "tps": self.tps,
            "hp": self.hp,
            "torque_lbft": self.torque_lbft,
            "boost_map": self.boost_map,
            "peaks": {
                "peak_hp": self.peak_hp,
                "rpm_at_peak_hp": self.rpm_at_peak_hp,
                "map_at_peak_hp": self.map_at_peak_hp,
                "peak_torque_lbft": self.peak_torque_lbft,
                "rpm_at_peak_torque": self.rpm_at_peak_torque,
                "peak_rpm": self.peak_rpm,
                "max_boost_psi": self.max_boost_psi,
            },
        }


class EngineSimulator:
    """Runs the engine state machine for a given configuration."""

    def __init__(self, config: EngineConfig, seed: int | None = None,
                 boost_map: BoostMap | None = None):
        self.config = config
        # None keeps the original linear boost ramp
        self.boost_map = boost_map
        # Own random generator: a fixed seed gives reproducible runs for tests
        self.rng = random.Random(seed)
        self._calibrate()

    def _calibrate(self):
        """Compute MAP targets and the HP factor from the configuration."""
        atm = self.config.atmospheric_psi
        self.idle_map = atm * IDLE_MAP_RATIO
        self.cruise_map_min = atm * CRUISE_MAP_MIN_RATIO
        self.cruise_map_max = atm * CRUISE_MAP_MAX_RATIO
        self.na_wot_map = atm * NA_WOT_MAP_RATIO
        self.decel_map = DECEL_MAP_KPA * KPA_TO_PSI

        # HP = factor * RPM * MAP, calibrated so the engine makes its
        # naturally aspirated peak HP at peak_hp_rpm and NA wide-open MAP
        denominator = self.config.peak_hp_rpm * self.na_wot_map
        self.hp_unit_factor = self.config.na_peak_hp / denominator if denominator > 0 else 0.0

    # --- Physics helpers (the extension points for torque, boost maps, fuel) ---

    def estimate_hp(self, rpm: float, map_psi: float) -> float:
        """Estimated horsepower at a given RPM and MAP."""
        if self.hp_unit_factor == 0 or rpm < 500 or map_psi < 2:
            return 0.0
        hp = self.hp_unit_factor * rpm * map_psi
        return min(hp, self.config.na_peak_hp * HP_CAP_MULTIPLIER)

    def estimate_torque(self, rpm: float, map_psi: float) -> float:
        """Estimated torque (lb-ft) at a given RPM and MAP."""
        return torque_from_hp(self.estimate_hp(rpm, map_psi), rpm)

    def target_boost_psi(self, rpm: float) -> float:
        """Target MAP under boost at a given RPM.

        With a boost map: atmospheric pressure plus the map's share of the
        configured boost at RPM / redline.
        Without one: linear ramp from atmospheric pressure at ACCEL_RPM_MIN up
        to max_boost_psi at rpm_at_max_boost.
        """
        cfg = self.config
        if self.boost_map is not None:
            fraction = self.boost_map.boost_fraction(rpm / cfg.redline_rpm)
            target = cfg.atmospheric_psi + fraction * (cfg.max_boost_psi - cfg.atmospheric_psi)
            return max(self.na_wot_map, target)

        if cfg.rpm_at_max_boost <= ACCEL_RPM_MIN:
            return cfg.max_boost_psi

        progress = (rpm - ACCEL_RPM_MIN) / (cfg.rpm_at_max_boost - ACCEL_RPM_MIN)
        progress = max(0.0, min(1.0, progress))
        target = cfg.atmospheric_psi + progress * (cfg.max_boost_psi - cfg.atmospheric_psi)
        return max(self.na_wot_map, target)

    # --- One step of each engine state: returns (rpm, map_psi, tps) ---

    def _idle(self, prev_rpm):
        idle = self.config.idle_rpm
        return (self.rng.randint(idle - 50, idle + 50),
                self.rng.uniform(self.idle_map - MAP_NOISE_PSI, self.idle_map + MAP_NOISE_PSI),
                IDLE_TPS)

    def _cruise(self, prev_rpm):
        return (self.rng.randint(self.config.cruise_rpm_min, self.config.cruise_rpm_max),
                self.rng.uniform(self.cruise_map_min - MAP_NOISE_PSI,
                                 self.cruise_map_max + MAP_NOISE_PSI),
                self.rng.randint(CRUISE_TPS_MIN, CRUISE_TPS_MAX))

    def _accel_na(self, prev_rpm):
        cfg = self.config
        rpm_high = max(ACCEL_RPM_MIN, min(cfg.redline_rpm, cfg.rpm_at_max_boost - 500))
        return (self.rng.randint(ACCEL_RPM_MIN, rpm_high),
                self.rng.uniform(self.na_wot_map - MAP_NOISE_PSI, self.na_wot_map + MAP_NOISE_PSI),
                self.rng.randint(ACCEL_TPS_MIN, ACCEL_TPS_MAX))

    def _accel_boost(self, prev_rpm):
        cfg = self.config
        target_rpm = self.rng.randint(ACCEL_RPM_MIN, cfg.redline_rpm)

        # Climb toward the target RPM, then hover around it
        if prev_rpm < target_rpm:
            rpm = min(prev_rpm + self.rng.randint(100, 300), cfg.redline_rpm)
        else:
            rpm = self.rng.randint(target_rpm - 100, target_rpm + 100)

        target_map = self.target_boost_psi(rpm)
        map_psi = self.rng.uniform(target_map - MAP_NOISE_PSI, target_map + MAP_NOISE_PSI)
        map_psi = max(cfg.atmospheric_psi * 0.1, min(map_psi, cfg.max_boost_psi))

        return rpm, map_psi, self.rng.randint(ACCEL_TPS_MIN, ACCEL_TPS_MAX)

    def _decel(self, prev_rpm):
        return (self.rng.randint(self.config.decel_rpm_min, self.config.decel_rpm_max),
                self.rng.uniform(self.decel_map - MAP_NOISE_PSI, self.decel_map + MAP_NOISE_PSI),
                DECEL_TPS)

    # --- Main loop ---

    def run(self, duration_s: float = 45.0, dt: float = 0.1) -> SimulationResult:
        """Simulate `duration_s` seconds in steps of `dt`. Returns instantly."""
        step_fns = {
            "idle": self._idle,
            "cruise": self._cruise,
            "accel_na": self._accel_na,
            "accel_boost": self._accel_boost,
            "decel": self._decel,
        }

        result = SimulationResult(
            boost_map=self.boost_map.name if self.boost_map else "default")
        state = "idle"
        state_elapsed = 0.0
        prev_rpm = 0

        for i in range(round(duration_s / dt)):
            rpm, map_psi, tps = step_fns[state](prev_rpm)
            hp = self.estimate_hp(rpm, map_psi)
            torque = torque_from_hp(hp, rpm)

            # Record this step
            result.time_s.append(round(i * dt, 6))
            result.state.append(state)
            result.rpm.append(rpm)
            result.map_psi.append(float(map_psi))
            result.tps.append(tps)
            result.hp.append(hp)
            result.torque_lbft.append(torque)

            # Track peaks
            if hp > result.peak_hp:
                result.peak_hp = hp
                result.rpm_at_peak_hp = rpm
                result.map_at_peak_hp = map_psi
            if torque > result.peak_torque_lbft:
                result.peak_torque_lbft = torque
                result.rpm_at_peak_torque = rpm
            result.peak_rpm = max(result.peak_rpm, rpm)
            if map_psi > self.config.boost_active_psi:
                result.max_boost_psi = max(result.max_boost_psi or 0.0, map_psi)

            # Decide whether to switch state
            min_s, max_s, next_states = STATE_TRANSITIONS[state]
            if state_elapsed > self.rng.randint(min_s, max_s):
                state = self.rng.choice(next_states)
                state_elapsed = 0.0

            prev_rpm = rpm
            state_elapsed += dt

        return result


def plot_result(result: SimulationResult, config: EngineConfig,
                filename: str = "engine_simulation.png") -> str:
    """Save a 4-panel graph (MAP, RPM, HP/torque, TPS) with peak stats in the
    title. Returns the filename."""
    import matplotlib
    matplotlib.use("Agg")  # headless: save to file without a window
    import matplotlib.pyplot as plt

    t = result.time_s
    fig, (ax_map, ax_rpm, ax_hp, ax_tps) = plt.subplots(
        4, 1, figsize=(11, 13), sharex=True,
        gridspec_kw={"height_ratios": [3, 2, 3, 1.5]})
    fig.set_facecolor("#f0f0f0")

    # --- MAP ---
    ax_map.plot(t, result.map_psi, color="blue", linewidth=1.5)
    ax_map.set_title("MAP Over Time", fontsize=13)
    ax_map.set_ylabel("MAP (PSI)")
    ax_map.set_ylim(0, config.max_boost_psi + 12.5)
    ax_map.axhline(config.max_boost_psi, color="orange", linestyle="--",
                   label=f"Maximum Boost: {config.max_boost_psi:.1f} PSI")
    ax_map.axhline(config.atmospheric_psi, color="red", linestyle=":",
                   label=f"Atmospheric Pressure: {config.atmospheric_psi:.1f} PSI")
    ax_map.legend(loc="upper left", fontsize=9)

    # --- RPM ---
    ax_rpm.plot(t, result.rpm, color="purple", linewidth=1.5)
    ax_rpm.set_title("RPM Over Time", fontsize=13)
    ax_rpm.set_ylabel("RPM")
    ax_rpm.set_ylim(0, config.redline_rpm * 1.1)
    ax_rpm.axhline(config.redline_rpm, color="red", linestyle="--",
                   label=f"Redline: {config.redline_rpm} RPM")
    ax_rpm.legend(loc="upper left", fontsize=9)

    # --- Horsepower and torque (torque on a second y-axis) ---
    hp_line, = ax_hp.plot(t, result.hp, color="green", linewidth=1.5, label="Horsepower")
    ax_hp.set_title("Horsepower and Torque Over Time", fontsize=13)
    ax_hp.set_ylabel("HP", color="green")
    ax_hp.set_ylim(bottom=0)
    ax_tq = ax_hp.twinx()
    tq_line, = ax_tq.plot(t, result.torque_lbft, color="darkorange", linewidth=1.2,
                          alpha=0.85, label="Torque (lb-ft)")
    ax_tq.set_ylabel("Torque (lb-ft)", color="darkorange")
    ax_tq.set_ylim(bottom=0)
    na_line = ax_hp.axhline(config.na_peak_hp, color="gray", linestyle=":",
                            label=f"NA Peak HP: {config.na_peak_hp:.0f}")
    ax_hp.legend(handles=[hp_line, tq_line, na_line], loc="upper left", fontsize=9)

    # --- Throttle position ---
    ax_tps.plot(t, result.tps, color="brown", linewidth=1.2)
    ax_tps.set_title("Throttle Position Over Time", fontsize=13)
    ax_tps.set_ylabel("TPS (%)")
    ax_tps.set_ylim(0, 105)
    ax_tps.set_xlabel("Time (s)")

    for ax in (ax_map, ax_rpm, ax_hp, ax_tps):
        ax.grid(True)
        ax.set_xlim(0, t[-1] if t else 0)

    boost_str = ("N/A (No Boost Hit)" if result.max_boost_psi is None
                 else f"{result.max_boost_psi:.1f} PSI")
    at_peak = (f" @ {result.map_at_peak_hp:.1f} PSI, {result.rpm_at_peak_hp} RPM"
               if result.peak_hp > 0 else "")
    torque_str = (f"{result.peak_torque_lbft:.0f} lb-ft @ {result.rpm_at_peak_torque} RPM"
                  if result.peak_torque_lbft > 0 else "N/A")
    fig.suptitle(
        f"Simulated ECU Data (Peak Levels) - Boost Map: {result.boost_map}\n"
        f"Peak HP: {result.peak_hp:.0f}{at_peak} | "
        f"Peak Torque: {torque_str}\n"
        f"Peak RPM: {result.peak_rpm} | "
        f"Highest Boost: {boost_str} | "
        f"Atmospheric Pressure (at elevation): {config.atmospheric_psi:.1f} PSI",
        fontsize=13, y=0.995,
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.955))
    fig.savefig(filename, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return filename


def print_summary(result: SimulationResult, config: EngineConfig) -> None:
    """Print every input, peak value and the time spent in each engine state."""
    boost_str = ("not reached" if result.max_boost_psi is None
                 else f"{result.max_boost_psi:.1f} PSI "
                      f"({result.max_boost_psi - config.atmospheric_psi:.1f} PSI above atmospheric)")
    print()
    print("=== Inputs ===")
    print(f"  Elevation:              {config.elevation_m:.0f} m")
    print(f"  Atmospheric pressure:   {config.atmospheric_psi:.1f} PSI")
    print(f"  NA peak horsepower:     {config.na_peak_hp:.0f} HP")
    print(f"  Target max boost (abs): {config.max_boost_psi:.1f} PSI")
    print(f"  Redline / idle:         {config.redline_rpm} / {config.idle_rpm} RPM")
    print(f"  Boost map:              {result.boost_map}")
    print("=== Peaks ===")
    print(f"  Peak HP:      {result.peak_hp:.0f} HP at {result.rpm_at_peak_hp} RPM, "
          f"{result.map_at_peak_hp:.1f} PSI")
    print(f"  Peak torque:  {result.peak_torque_lbft:.0f} lb-ft at {result.rpm_at_peak_torque} RPM")
    print(f"  Peak RPM:     {result.peak_rpm}")
    print(f"  Highest boost: {boost_str}")
    print(f"  Peak TPS:     {max(result.tps) if result.tps else 0}%")
    print("=== Time in each state ===")
    dt = result.time_s[1] - result.time_s[0] if len(result.time_s) > 1 else 0
    for name in STATE_TRANSITIONS:
        secs = result.state.count(name) * dt
        print(f"  {name:<12} {secs:5.1f} s")
    print()


# --- Command-line interface ---

def get_float_input(prompt, min_val=None, max_val=None, input_fn=input):
    """Prompt until the user enters a number within [min_val, max_val]."""
    while True:
        try:
            val = float(input_fn(prompt))
        except ValueError:
            print("Invalid input. Please enter a number.")
            continue
        if min_val is not None and val < min_val:
            print(f"Value must be at least {min_val}. Try again.")
            continue
        if max_val is not None and val > max_val:
            print(f"Value must be no more than {max_val}. Try again.")
            continue
        return val


def get_int_input(prompt, min_val=None, max_val=None, input_fn=input):
    return int(get_float_input(prompt, min_val, max_val, input_fn))


def prompt_config(input_fn=input) -> EngineConfig:
    """Ask the user for each input and build an EngineConfig."""
    elevation = get_float_input("Enter elevation (meters): ",
                                ELEVATION_MIN_M, ELEVATION_MAX_M, input_fn)
    atm = calculate_atmospheric_pressure(elevation)
    print(f"Estimated atmospheric pressure at {elevation} meters: {atm:.2f} PSI")

    na_hp = get_float_input("Enter naturally aspirated peak horsepower: ",
                            1, None, input_fn)

    max_boost = get_float_input(
        "Enter target peak boost pressure (PSI):\n"
        "*To find target peak boost PSI, add your boost system's\n"
        "pressure to the atmospheric pressure. For example, if\n"
        "the atmospheric pressure is 10 PSI and your boost\n"
        "system provides 6 PSI, the target peak boost pressure would\n"
        "be 10+6=16 PSI.*\n",
        round(atm), BOOST_MAX_PSI, input_fn)

    redline = get_int_input("Enter redline RPM: ",
                            REDLINE_MIN_RPM, REDLINE_MAX_RPM, input_fn)
    idle = get_int_input("Enter idle RPM: ", IDLE_MIN_RPM, IDLE_MAX_RPM, input_fn)

    return EngineConfig(elevation_m=elevation, na_peak_hp=na_hp,
                        max_boost_psi=max_boost, redline_rpm=redline, idle_rpm=idle)


def prompt_boost_map(input_fn=input) -> BoostMap | None:
    """Let the user pick a bundled boost map. Blank input keeps the default ramp."""
    names = list_boost_maps()
    if not names:
        return None

    print("Boost maps:")
    print("  0) default (linear ramp)")
    for i, name in enumerate(names, start=1):
        print(f"  {i}) {name} - {get_boost_map(name).description}")

    while True:
        choice = input_fn("Choose a boost map (number or name, blank for default): ").strip()
        if choice in ("", "0", "default"):
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(names):
            return get_boost_map(names[int(choice) - 1])
        if choice in names:
            return get_boost_map(choice)
        print("Invalid choice. Try again.")


def main():
    print("***Welcome To Your Engine Performance Simulator!***")
    config = prompt_config()
    boost_map = prompt_boost_map()

    duration_s = 45
    print(f"Simulating {duration_s} seconds of engine data...")
    result = EngineSimulator(config, boost_map=boost_map).run(duration_s=duration_s)

    filename = plot_result(result, config)
    print_summary(result, config)
    print(f"Simulation complete. Image saved to {filename}")


if __name__ == "__main__":
    main()