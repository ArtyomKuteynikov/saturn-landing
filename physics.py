"""
Physics integration for the Saturn descent simulator.

Integration scheme: semi-implicit Euler, fixed dt = 0.05 s.

Forces on the probe (vertical axis, positive = downward):
  F_gravity = m * g
  F_drag    = 0.5 * rho(h) * Cd * A * v_rel²   (opposes motion)

Horizontal axis:
  F_wind_drag = 0.5 * rho(h) * Cd * A * (v_wind - v_horiz)²

Heat flux (simplified Chapman analogue, display only):
  q ≈ K * sqrt(rho) * v³   [W/m²]

Auto-deployment: drogue and main chute deploy automatically when
vertical speed drops below the configured thresholds.
"""

import math

import atmosphere as atm
from probe import ProbeState, ProbeConfig, Phase

DT = 0.05  # integration step, seconds
G_EARTH = 9.81  # m/s² — for g-load conversion

_HEAT_COEFF = 1.0e-9  # empirical constant for heat flux display


def step(state: ProbeState, config: ProbeConfig) -> list[str]:
    """
    Advance simulation by one DT step. Mutates state in-place.
    Returns list of event messages generated this step (may be empty).
    """
    events: list[str] = []

    state.elapsed_s += DT

    alt_km = state.altitude_km

    # --- Atmospheric properties at current altitude ---
    rho = atm.get_density(alt_km)
    T = atm.get_temperature(alt_km)
    P = atm.get_pressure(alt_km)
    v_wind = atm.get_wind_speed(alt_km)

    state.density = rho
    state.temperature_k = T
    state.pressure_bar = P
    state.wind_speed = v_wind

    # Track peaks
    if T > state.peak_temperature: state.peak_temperature = T
    if P > state.peak_pressure:    state.peak_pressure = P

    # --- Drag parameters ---
    cd, area = state.effective_cd_area(config)

    # --- Vertical forces ---
    v_vert = state.velocity_ms
    f_drag_vert = 0.5 * rho * cd * area * v_vert ** 2  # always opposes descent
    a_vert = atm.GRAVITY - f_drag_vert / config.mass_kg

    # --- G-load (deceleration felt by structure) ---
    decel = abs(f_drag_vert / config.mass_kg)
    state.gload = decel / G_EARTH
    if state.gload > state.peak_gload:
        state.peak_gload = state.gload

    # --- Heat flux ---
    state.heat_flux = _HEAT_COEFF * math.sqrt(max(rho, 1e-12)) * (v_vert ** 3)

    # --- Horizontal wind drag ---
    v_rel_horiz = v_wind - state.horiz_velocity
    sign_h = 1.0 if v_rel_horiz >= 0 else -1.0
    f_drag_horiz = 0.5 * rho * cd * area * v_rel_horiz ** 2 * sign_h
    a_horiz = f_drag_horiz / config.mass_kg

    # --- Integrate ---
    state.velocity_ms += a_vert * DT
    state.horiz_velocity += a_horiz * DT
    state.velocity_ms = max(state.velocity_ms, 0.0)

    state.altitude_km -= state.velocity_ms * DT / 1000.0
    state.horiz_position_km += state.horiz_velocity * DT / 1000.0

    # --- Auto-deploy parachutes ---
    events += _check_auto_deploy(state, config)

    # --- Science data accumulation ---
    if state.phase == Phase.SCIENCE and state.instruments_on:
        state.science_data_s += DT

    # --- Survival and success checks ---
    if state.check_limits(config) and state.is_alive():
        state.check_success(config)

    return events


def _check_auto_deploy(state: ProbeState, config: ProbeConfig) -> list[str]:
    """Auto-deploy parachutes when speed drops to configured thresholds."""
    events: list[str] = []

    if not state.is_alive():
        return events

    # Auto drogue (only for 2-chute configs)
    if (config.num_parachutes >= 2
            and not state.auto_drogue_triggered
            and not state.drogue_deployed
            and state.velocity_ms <= config.auto_deploy_drogue_ms
            and state.velocity_ms > 0):
        err = state.deploy_drogue(config)
        if err is None:
            events.append(f"АВТО: Тормозной пар. раскрыт при {state.velocity_ms:.0f} м/с")
        elif err:
            events.append(err)

    # Auto main chute
    if (not state.auto_main_triggered
            and not state.main_chute_deployed
            and state.velocity_ms <= config.auto_deploy_main_ms
            and state.velocity_ms > 0
            and (config.num_parachutes < 2 or state.drogue_deployed)):
        err = state.deploy_main_chute(config)
        if err is None:
            events.append(f"АВТО: Осн. парашют раскрыт при {state.velocity_ms:.0f} м/с")
        elif err:
            events.append(err)

    return events
