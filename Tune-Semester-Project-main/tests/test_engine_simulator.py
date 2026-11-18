"""
Tests for engine_simulator.py

    Core engine:  atmospheric pressure, config limits, HP math, default boost
                  ramp, full runs, plotting, CLI input
    Boost maps:   each map loads and changes the boost curve
    Torque:       compare to hand calc (Torque = HP x 5252 / RPM)

Run from the project folder:
    python -m pytest tests
"""

import json
import time

import pytest

from engine_simulator import (
    ACCEL_RPM_MIN,
    HP_TORQUE_CONSTANT,
    BoostMap,
    EngineConfig,
    EngineSimulator,
    calculate_atmospheric_pressure,
    get_boost_map,
    get_float_input,
    hp_from_torque,
    list_boost_maps,
    load_boost_map,
    plot_result,
    torque_from_hp,
)


def make_config(**overrides):
    """Sea-level config by default; pass keyword args to change any field."""
    values = dict(elevation_m=0, na_peak_hp=300, max_boost_psi=22,
                  redline_rpm=7000, idle_rpm=800)
    values.update(overrides)
    return EngineConfig(**values)


# Colorado Springs elevation, used for the boost map and torque tests
CONFIG = make_config(elevation_m=1840, max_boost_psi=20)

# RPMs where boost applies: ACCEL_RPM_MIN up to redline, every 100 RPM
BOOST_RPMS = range(ACCEL_RPM_MIN, CONFIG.redline_rpm + 1, 100)


def boost_curve(boost_map):
    sim = EngineSimulator(CONFIG, seed=0, boost_map=boost_map)
    return [sim.target_boost_psi(rpm) for rpm in BOOST_RPMS]


# =========================================================================
# Core engine
# =========================================================================

# --- Atmospheric pressure ---

def test_sea_level_pressure():
    assert calculate_atmospheric_pressure(0) == pytest.approx(14.696, abs=0.01)


def test_pressure_drops_with_elevation():
    assert calculate_atmospheric_pressure(1840) < calculate_atmospheric_pressure(0)
    assert calculate_atmospheric_pressure(1840) == pytest.approx(11.8, abs=0.2)


def test_pressure_above_formula_range_is_zero():
    assert calculate_atmospheric_pressure(50000) == 0.0


# --- Config validation and derived values ---

@pytest.mark.parametrize("field,value", [
    ("elevation_m", 9000),
    ("na_peak_hp", 0),
    ("max_boost_psi", 50),
    ("max_boost_psi", 5),
    ("redline_rpm", 4000),
    ("idle_rpm", 1200),
])
def test_config_rejects_out_of_range(field, value):
    with pytest.raises(ValueError):
        make_config(**{field: value})


def test_rpm_at_max_boost_is_90_percent_of_redline():
    assert make_config(redline_rpm=7000).rpm_at_max_boost == 6300


def test_rpm_at_max_boost_has_floor():
    assert make_config(redline_rpm=5000).rpm_at_max_boost == ACCEL_RPM_MIN + 500


def test_derived_rpm_ranges():
    cfg = make_config(idle_rpm=800)
    assert cfg.cruise_rpm_min == 1200
    assert cfg.cruise_rpm_max == 4500
    assert cfg.decel_rpm_min == 900
    assert cfg.decel_rpm_max == 4900


# --- HP and default boost ramp ---

def test_hp_calibration_matches_na_peak():
    sim = EngineSimulator(make_config(na_peak_hp=300))
    hp = sim.estimate_hp(sim.config.peak_hp_rpm, sim.na_wot_map)
    assert hp == pytest.approx(300)


def test_hp_zero_below_thresholds():
    sim = EngineSimulator(make_config())
    assert sim.estimate_hp(400, 14) == 0.0
    assert sim.estimate_hp(3000, 1.5) == 0.0


def test_hp_is_capped():
    sim = EngineSimulator(make_config(na_peak_hp=300))
    assert sim.estimate_hp(7000, 45) == pytest.approx(300 * 2.5)


def test_boost_ramp_endpoints_and_midpoint():
    cfg = make_config(max_boost_psi=22)
    sim = EngineSimulator(cfg)
    assert sim.target_boost_psi(cfg.rpm_at_max_boost) == pytest.approx(22)
    assert sim.target_boost_psi(cfg.redline_rpm) == pytest.approx(22)  # clamped
    assert sim.target_boost_psi(ACCEL_RPM_MIN) == pytest.approx(cfg.atmospheric_psi)
    mid = (ACCEL_RPM_MIN + cfg.rpm_at_max_boost) / 2
    expected = (cfg.atmospheric_psi + 22) / 2
    assert sim.target_boost_psi(mid) == pytest.approx(expected)


# --- Full runs ---

def test_run_length_and_speed():
    start = time.perf_counter()
    result = EngineSimulator(make_config(), seed=1).run(duration_s=45, dt=0.1)
    assert len(result.time_s) == len(result.rpm) == len(result.map_psi) == 450
    assert time.perf_counter() - start < 2  # no real-time sleeping


def test_same_seed_same_result():
    a = EngineSimulator(make_config(), seed=7).run()
    b = EngineSimulator(make_config(), seed=7).run()
    assert a.to_dict() == b.to_dict()


@pytest.mark.parametrize("seed", range(20))
def test_values_stay_in_bounds(seed):
    cfg = make_config()
    result = EngineSimulator(cfg, seed=seed).run()
    assert max(result.map_psi) <= cfg.max_boost_psi
    assert min(result.rpm) >= cfg.idle_rpm - 50
    assert max(result.rpm) <= cfg.redline_rpm
    assert max(result.hp) <= cfg.na_peak_hp * 2.5


@pytest.mark.parametrize("seed", range(20))
def test_peak_stats_match_series(seed):
    result = EngineSimulator(make_config(), seed=seed).run()
    assert result.peak_rpm == max(result.rpm)  # true max RPM, not RPM at peak HP
    assert result.peak_hp == max(result.hp)
    i = result.hp.index(result.peak_hp)
    assert result.rpm_at_peak_hp == result.rpm[i]


def test_to_dict_is_json_serializable():
    result = EngineSimulator(make_config(), seed=3).run(duration_s=5)
    json.dumps(result.to_dict())


def test_plot_result_writes_png(tmp_path):
    cfg = make_config()
    result = EngineSimulator(cfg, seed=3).run(duration_s=5)
    out = plot_result(result, cfg, str(tmp_path / "out.png"))
    assert (tmp_path / "out.png").stat().st_size > 0
    assert out.endswith("out.png")


# --- CLI input helper ---

def test_get_float_input_retries_until_valid(capsys):
    answers = iter(["abc", "-5", "500", "42"])
    val = get_float_input("? ", min_val=0, max_val=100, input_fn=lambda _: next(answers))
    assert val == 42
    out = capsys.readouterr().out
    assert "Invalid input" in out
    assert "at least 0" in out
    assert "no more than 100" in out


# =========================================================================
# Boost maps
# =========================================================================

# --- Bundled map files (maps/boost/*.json) ---

def test_bundled_maps_exist():
    assert len(list_boost_maps()) >= 2


@pytest.mark.parametrize("name", list_boost_maps())
def test_each_map_loads(name):
    boost_map = get_boost_map(name)
    assert isinstance(boost_map, BoostMap)
    assert boost_map.name == name
    assert len(boost_map.points) >= 2


@pytest.mark.parametrize("name", list_boost_maps())
def test_each_map_changes_the_boost_curve(name):
    default_curve = boost_curve(None)
    curve = boost_curve(get_boost_map(name))
    biggest_change = max(abs(a - b) for a, b in zip(curve, default_curve))
    assert biggest_change > 0.5, f"{name} barely changes the default boost curve"


def test_maps_differ_from_each_other():
    curves = {name: boost_curve(get_boost_map(name)) for name in list_boost_maps()}
    names = list(curves)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            assert curves[a] != curves[b], f"{a} and {b} give the same curve"


@pytest.mark.parametrize("name", list_boost_maps())
def test_boost_stays_within_atmospheric_and_max(name):
    for psi in boost_curve(get_boost_map(name)):
        assert psi >= CONFIG.atmospheric_psi * 0.95 - 1e-9
        assert psi <= CONFIG.max_boost_psi + 1e-9


@pytest.mark.parametrize("name", list_boost_maps())
def test_each_map_changes_simulation_output(name):
    # Same seed -> same state sequence, so only the MAP values should differ
    duration = 300
    default = EngineSimulator(CONFIG, seed=7).run(duration_s=duration)
    assert "accel_boost" in default.state, "pick a seed that reaches boost"
    result = EngineSimulator(CONFIG, seed=7,
                             boost_map=get_boost_map(name)).run(duration_s=duration)
    assert result.state == default.state
    assert result.boost_map == name
    assert result.map_psi != default.map_psi


# --- Interpolation ---

@pytest.fixture
def test_map():
    return BoostMap("test", ((0.5, 0.0), (0.7, 1.0), (1.0, 0.5)))


def test_map_exact_points(test_map):
    assert test_map.boost_fraction(0.5) == pytest.approx(0.0)
    assert test_map.boost_fraction(0.7) == pytest.approx(1.0)
    assert test_map.boost_fraction(1.0) == pytest.approx(0.5)


def test_map_interpolates_between_points(test_map):
    assert test_map.boost_fraction(0.6) == pytest.approx(0.5)
    assert test_map.boost_fraction(0.85) == pytest.approx(0.75)


def test_map_holds_end_values_outside_range(test_map):
    assert test_map.boost_fraction(0.2) == pytest.approx(0.0)
    assert test_map.boost_fraction(1.2) == pytest.approx(0.5)


# --- Validation and loading ---

@pytest.mark.parametrize("points", [
    ((0.5, 0.5),),                # fewer than 2 points
    ((0.7, 0.0), (0.5, 1.0)),     # RPM not increasing
    ((0.5, 0.0), (0.5, 1.0)),     # duplicate RPM
    ((0.5, 0.0), (1.5, 1.0)),     # RPM fraction above 1
    ((0.5, 0.0), (1.0, 1.2)),     # boost fraction above 1
])
def test_rejects_bad_maps(points):
    with pytest.raises(ValueError):
        BoostMap("bad", points)


def test_rejects_unknown_name():
    with pytest.raises(ValueError):
        get_boost_map("does_not_exist")
    with pytest.raises(ValueError):
        get_boost_map("../boost_maps")


def test_load_custom_file(tmp_path):
    path = tmp_path / "custom.json"
    path.write_text(json.dumps({"points": [[0.5, 0.0], [1.0, 1.0]]}))
    boost_map = load_boost_map(path)
    assert boost_map.name == "custom"  # defaults to file name
    assert get_boost_map("custom", tmp_path).points == boost_map.points


def test_rejects_file_without_points(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"name": "broken"}))
    with pytest.raises(ValueError):
        load_boost_map(path)


# =========================================================================
# Torque
# =========================================================================

@pytest.mark.parametrize("hp,rpm,expected", [
    (300, 5252, 300.0),      # HP and torque cross at 5252 RPM
    (400, 6000, 350.13),     # 400 * 5252 / 6000 = 350.133...
    (150, 2626, 300.0),      # 150 * 5252 / 2626 = 300
    (500, 7000, 375.14),     # 500 * 5252 / 7000 = 375.142...
    (250, 3000, 437.67),     # 250 * 5252 / 3000 = 437.666...
])
def test_torque_hand_calculations(hp, rpm, expected):
    assert torque_from_hp(hp, rpm) == pytest.approx(expected, abs=0.005)


def test_torque_constant_is_5252():
    assert HP_TORQUE_CONSTANT == 5252


def test_zero_or_negative_rpm_gives_zero_torque():
    assert torque_from_hp(300, 0) == 0.0
    assert torque_from_hp(300, -100) == 0.0


def test_inverse_formula():
    assert hp_from_torque(torque_from_hp(412.5, 6100), 6100) == pytest.approx(412.5)


@pytest.fixture(scope="module")
def torque_run():
    return EngineSimulator(CONFIG, seed=42).run(duration_s=60)


def test_every_step_matches_hand_formula(torque_run):
    assert len(torque_run.torque_lbft) == len(torque_run.hp)
    for hp, rpm, torque in zip(torque_run.hp, torque_run.rpm, torque_run.torque_lbft):
        assert torque == pytest.approx(hp * 5252 / rpm, abs=1e-6)


def test_peak_torque_is_tracked(torque_run):
    assert torque_run.peak_torque_lbft == pytest.approx(max(torque_run.torque_lbft))
    i = torque_run.torque_lbft.index(torque_run.peak_torque_lbft)
    assert torque_run.rpm_at_peak_torque == torque_run.rpm[i]


def test_torque_in_dict_output(torque_run):
    data = torque_run.to_dict()
    assert data["torque_lbft"] == torque_run.torque_lbft
    assert "peak_torque_lbft" in data["peaks"]
    assert "rpm_at_peak_torque" in data["peaks"]


def test_estimate_torque_method():
    sim = EngineSimulator(CONFIG)
    hp = sim.estimate_hp(5600, 11.0)
    assert sim.estimate_torque(5600, 11.0) == pytest.approx(hp * 5252 / 5600)
