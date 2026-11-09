"""Tests for the refactored engine simulator. Run with: pytest"""

import json
import time

import pytest

from Tuning import (
    ACCEL_RPM_MIN,
    EngineConfig,
    EngineSimulator,
    calculate_atmospheric_pressure,
    get_float_input,
    plot_result,
)


def make_config(**overrides):
    values = dict(elevation_m=0, na_peak_hp=300, max_boost_psi=22,
                  redline_rpm=7000, idle_rpm=800)
    values.update(overrides)
    return EngineConfig(**values)


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


# --- HP and boost math ---

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
