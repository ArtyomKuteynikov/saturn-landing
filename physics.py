"""
Physics integration for the Saturn descent simulator.

Mathematical model from PDF (НОЦ ПИШ задание 4):
  - Variable gravity: g(y) = GM / (R_planet + y)²
  - Exponential atmosphere: rho(y) = rho0 · exp(-y / H_scale)
  - Drag: F_drag = 0.5 · rho · C_D · S · v²
  - Lift: F_lift = 0.5 · rho · C_L · S · v²
  - Heat flux: q = C_planet · v³ · sqrt(rho) / sqrt(R_nose)

Equations of motion (trajectory-angle formulation):
  dv/dt   = -F_drag / m - g · sin(theta)
  dθ/dt   = F_lift / (m · v) - (g / v) · cos(theta)
            + v · cos(theta) / (R_planet + y)
  dy/dt   = v · sin(theta)
  dx/dt   = R_planet / (R_planet + y) · v · cos(theta)

Integration: semi-implicit Euler, fixed dt = 0.05 s.
"""

import math

import atmosphere as atm
from probe import ProbeState, ProbeConfig, Phase

DT = 0.05  # integration step, seconds


def step(state: ProbeState, config: ProbeConfig) -> list[str]:
    """
    Advance simulation by one DT step. Mutates state in-place.
    Returns list of event messages generated this step (may be empty).
    """
    events: list[str] = []

    state.elapsed_s += DT

    # Current state
    y_m = state.altitude_km * 1000.0   # altitude in metres
    v = state.velocity_ms              # total speed, m/s
    theta = state.theta_rad            # trajectory angle, rad (negative = descending)
    x_m = state.horiz_position_km * 1000.0  # downrange in metres

    # --- Atmospheric properties ---
    # Use exponential model for density (PDF), tabulated for T, P, wind (display)
    rho = atm.get_density_exp(y_m)
    T = atm.get_temperature(state.altitude_km)
    P = atm.get_pressure(state.altitude_km)
    v_wind = atm.get_wind_speed(state.altitude_km)

    state.density = rho
    state.temperature_k = T
    state.pressure_bar = P
    state.wind_speed = v_wind

    # Track peaks
    if T > state.peak_temperature:
        state.peak_temperature = T
    if P > state.peak_pressure:
        state.peak_pressure = P

    # --- Variable gravity (PDF: g = GM / (R_planet + y)²) ---
    g_cur = atm.get_gravity(y_m)

    # --- Drag & lift parameters ---
    cd, area = state.effective_cd_area(config)
    cl = config.cl_vehicle  # lift coefficient from PDF

    # Forces (PDF formulas)
    F_drag = 0.5 * rho * cd * area * v * v
    F_lift = 0.5 * rho * cl * area * v * v

    # --- Equations of motion (PDF trajectory-angle formulation) ---
    sin_theta = math.sin(theta)
    cos_theta = math.cos(theta)

    # dv/dt = -F_drag / m - g · sin(theta)
    dv_dt = -F_drag / config.mass_kg - g_cur * sin_theta

    # dθ/dt = F_lift / (m·v) - (g/v)·cos(theta) + v·cos(theta) / (R_planet + y)
    r_cur = atm.R_PLANET + y_m
    if v > 1.0:  # avoid division by zero at very low speeds
        dtheta_dt = (F_lift / (config.mass_kg * v)
                     - (g_cur / v) * cos_theta
                     + (v * cos_theta) / r_cur)
    else:
        dtheta_dt = 0.0

    # dy/dt = v · sin(theta)   (positive theta → ascending)
    dy_dt = v * sin_theta

    # dx/dt = R_planet / (R_planet + y) · v · cos(theta)
    dx_dt = (atm.R_PLANET / r_cur) * v * cos_theta

    # --- G-load (deceleration / g_earth) ---
    decel = abs(dv_dt)
    state.gload = decel / atm.G_EARTH
    if state.gload > state.peak_gload:
        state.peak_gload = state.gload

    # --- Heat flux (PDF: q = C_planet · v³ · sqrt(rho) / sqrt(R_nose)) ---
    rho_safe = max(rho, 1e-10)
    state.heat_flux = (config.c_planet * (v ** 3)
                       * math.sqrt(rho_safe)
                       / math.sqrt(config.r_nose_m))

    # --- Integrate (semi-implicit Euler) ---
    v_new = v + dv_dt * DT
    v_new = max(v_new, 0.0)

    theta_new = theta + dtheta_dt * DT

    y_new = y_m + dy_dt * DT
    x_new = x_m + dx_dt * DT

    # --- Update state ---
    state.velocity_ms = v_new
    state.theta_rad = theta_new
    state.altitude_km = y_new / 1000.0
    state.horiz_position_km = x_new / 1000.0
    state.horiz_velocity = v_new * math.cos(theta_new)  # for HUD display

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
