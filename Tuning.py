"""
Engine performance simulator.

Simulates RPM, manifold absolute pressure (MAP), throttle position (TPS) and
estimated horsepower across idle, cruise, acceleration (with and without boost)
and deceleration states.

Structure:
    EngineConfig      - the user's inputs plus values derived from them
    EngineSimulator   - calibration, the state machine, and HP/boost math
    SimulationResult  - time series and peak stats returned by a run
    plot_result()     - optional matplotlib PNG export
    main()            - command-line entry point (prompts for inputs)

The simulator has no global state, no sleeping and no threads, so it can be
imported and called directly from tests or a web API:

    config = EngineConfig(elevation_m=1840, na_peak_hp=300,
                          max_boost_psi=20, redline_rpm=7000, idle_rpm=800)
    result = EngineSimulator(config, seed=42).run(duration_s=45)
    result.to_dict()  # JSON-ready
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

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

# --- Input limits (shared by the CLI and EngineConfig validation) ---
ELEVATION_MIN_M, ELEVATION_MAX_M = -400, 8000
BOOST_MAX_PSI = 45
REDLINE_MIN_RPM, REDLINE_MAX_RPM = 5000, 10000
IDLE_MIN_RPM, IDLE_MAX_RPM = 500, 1000

# --- State machine ---
# state -> (min seconds, max seconds, states it can switch to)
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

    peak_hp: float = 0.0
    rpm_at_peak_hp: int = 0
    map_at_peak_hp: float = 0.0
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
            "peaks": {
                "peak_hp": self.peak_hp,
                "rpm_at_peak_hp": self.rpm_at_peak_hp,
                "map_at_peak_hp": self.map_at_peak_hp,
                "peak_rpm": self.peak_rpm,
                "max_boost_psi": self.max_boost_psi,
            },
        }


class EngineSimulator:
    """Runs the engine state machine for a given configuration."""

    def __init__(self, config: EngineConfig, seed: int | None = None):
        self.config = config
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

    def target_boost_psi(self, rpm: float) -> float:
        """Target MAP under boost at a given RPM.

        Linear ramp from atmospheric pressure at ACCEL_RPM_MIN up to
        max_boost_psi at rpm_at_max_boost. Replace this with boost-map
        interpolation later.
        """
        cfg = self.config
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

        result = SimulationResult()
        state = "idle"
        state_elapsed = 0.0
        prev_rpm = 0

        for i in range(round(duration_s / dt)):
            rpm, map_psi, tps = step_fns[state](prev_rpm)
            hp = self.estimate_hp(rpm, map_psi)

            # Record this step
            result.time_s.append(round(i * dt, 6))
            result.state.append(state)
            result.rpm.append(rpm)
            result.map_psi.append(float(map_psi))
            result.tps.append(tps)
            result.hp.append(hp)

            # Track peaks
            if hp > result.peak_hp:
                result.peak_hp = hp
                result.rpm_at_peak_hp = rpm
                result.map_at_peak_hp = map_psi
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
    """Save a MAP-over-time graph with peak stats in the title. Returns the filename."""
    import matplotlib
    matplotlib.use("Agg")  # headless: save to file without a window
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 1, figsize=(10, 6))
    fig.set_facecolor("#f0f0f0")

    ax.plot(result.time_s, result.map_psi, color="blue", linewidth=2)
    ax.set_title("MAP Over Time", fontsize=16)
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("MAP (PSI)")
    ax.set_ylim(0, config.max_boost_psi + 12.5)
    ax.set_xlim(0, result.time_s[-1] if result.time_s else 0)
    ax.grid(True)
    ax.axhline(config.max_boost_psi, color="orange", linestyle="--",
               label=f"Maximum Boost: {config.max_boost_psi:.1f} PSI")
    ax.axhline(config.atmospheric_psi, color="red", linestyle=":",
               label=f"Atmospheric Pressure: {config.atmospheric_psi:.1f} PSI")
    ax.legend(loc="upper left")

    boost_str = ("N/A (No Boost Hit)" if result.max_boost_psi is None
                 else f"{result.max_boost_psi:.1f} PSI")
    at_peak = (f" @ {result.map_at_peak_hp:.1f} PSI, {result.rpm_at_peak_hp} RPM"
               if result.peak_hp > 0 else "")
    fig.suptitle(
        f"Simulated ECU Data (Peak Levels)\n"
        f"Peak HP: {result.peak_hp:.0f}{at_peak} | "
        f"Peak RPM: {result.peak_rpm} | "
        f"Highest Boost: {boost_str}\n"
        f"Atmospheric Pressure (at elevation): {config.atmospheric_psi:.1f} PSI",
        fontsize=14, y=0.98,
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.9))
    fig.savefig(filename, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return filename


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


def main():
    print("***Welcome To Your Engine Performance Simulator!***")
    config = prompt_config()

    duration_s = 45
    print(f"Simulating {duration_s} seconds of engine data...")
    result = EngineSimulator(config).run(duration_s=duration_s)

    filename = plot_result(result, config)
    print(f"Peak HP: {result.peak_hp:.0f} at {result.rpm_at_peak_hp} RPM | "
          f"Peak RPM: {result.peak_rpm}")
    print(f"Simulation complete. Image saved to {filename}")


if __name__ == "__main__":
    main()