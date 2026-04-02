"""
Saturn atmosphere model.
Data sources:
  - NASA Saturn Fact Sheet: https://nssdc.gsfc.nasa.gov/planetary/factsheet/saturnfact.html
  - Galileo probe Jovian atmosphere entry (analogue data)
  - Guillot T. (1999) "Interiors of Giant Planets Inside and Outside the Solar System"
  - Folkner et al. (2006) Cassini radio occultation profiles

Altitude is defined relative to the 1-bar pressure level (positive = above, negative = below).
"""

import math

# ---------------------------------------------------------------------------
# Tabulated atmosphere profile (altitude km → pressure bar, temp K, density kg/m³)
# Derived from theoretical models and Cassini/Galileo analogue data.
# Positive altitude = above 1-bar level; negative = below.
# ---------------------------------------------------------------------------

# (altitude_km, pressure_bar, temperature_K, density_kg_m3)
_PROFILE = [
    # High atmosphere / entry zone
    ( 400,  1.0e-7,   80.0,  3.0e-7),
    ( 300,  1.0e-6,   82.0,  3.5e-6),
    ( 200,  1.0e-5,   85.0,  3.4e-5),
    ( 150,  1.0e-4,   88.0,  3.3e-4),
    ( 100,  1.0e-3,   95.0,  3.0e-3),
    (  80,  5.0e-3,  100.0,  1.4e-2),
    (  60,  2.0e-2,  108.0,  5.4e-2),
    (  40,  1.0e-1,  118.0,  2.5e-1),
    (  20,  4.0e-1,  126.0,  9.3e-1),
    # 1-bar reference level
    (   0,  1.0,     134.0,  2.2   ),
    # Below 1-bar (cloud layers)
    ( -20,  2.5,     160.0,  4.6   ),   # upper ammonia clouds ~1.5 bar
    ( -50,  5.0,     200.0,  7.3   ),   # ammonium-hydrosulfide clouds ~3-5 bar
    ( -80, 10.0,     300.0, 10.0   ),   # water cloud top ~10 bar
    (-120, 25.0,     430.0, 17.0   ),
    (-170, 60.0,     700.0, 25.0   ),
    (-200,100.0,    1000.0, 30.0   ),   # probe destruction zone
]

_alts   = [r[0] for r in _PROFILE]
_press  = [r[1] for r in _PROFILE]
_temps  = [r[2] for r in _PROFILE]
_dens   = [r[3] for r in _PROFILE]


def _interp(alt_km: float, table_x: list, table_y: list) -> float:
    """Linear interpolation (log-scale for pressure and density)."""
    if alt_km >= table_x[0]:
        return table_y[0]
    if alt_km <= table_x[-1]:
        return table_y[-1]
    for i in range(len(table_x) - 1):
        x0, x1 = table_x[i], table_x[i + 1]
        if x1 <= alt_km <= x0:
            t = (alt_km - x0) / (x1 - x0)
            return table_y[i] + t * (table_y[i + 1] - table_y[i])
    return table_y[-1]


def _interp_log(alt_km: float, table_x: list, table_y: list) -> float:
    """Log-linear interpolation — better for pressure and density."""
    if alt_km >= table_x[0]:
        return table_y[0]
    if alt_km <= table_x[-1]:
        return table_y[-1]
    for i in range(len(table_x) - 1):
        x0, x1 = table_x[i], table_x[i + 1]
        if x1 <= alt_km <= x0:
            t = (alt_km - x0) / (x1 - x0)
            log_y = math.log(table_y[i]) + t * (math.log(table_y[i + 1]) - math.log(table_y[i]))
            return math.exp(log_y)
    return table_y[-1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

GRAVITY = 10.44          # m/s² at 1-bar level (NASA fact sheet)
SCALE_HEIGHT = 59_500    # m — approximate scale height for density falloff above profile

# Wind profile: horizontal wind speed (m/s) vs altitude (km)
# Saturn equatorial jet reaches ~500 m/s near the cloud tops
_WIND_PROFILE = [
    ( 400,   0.0),
    ( 200,  50.0),
    ( 100, 150.0),
    (  60, 300.0),
    (  20, 450.0),
    (   0, 400.0),
    ( -50, 300.0),
    (-200, 200.0),
]
_wind_alts  = [r[0] for r in _WIND_PROFILE]
_wind_speed = [r[1] for r in _WIND_PROFILE]

# Cloud layer definitions for renderer
CLOUD_LAYERS = [
    {"name": "Ammonia ice clouds",       "alt_km":  -10, "color": (220, 200, 160), "thickness_km": 30},
    {"name": "Ammonium hydrosulfide",    "alt_km":  -50, "color": (180, 140, 100), "thickness_km": 30},
    {"name": "Water clouds",             "alt_km":  -80, "color": (160, 180, 200), "thickness_km": 40},
]


def get_density(alt_km: float) -> float:
    """Atmospheric density in kg/m³ at given altitude (km above 1-bar level)."""
    return _interp_log(alt_km, _alts, _dens)


def get_temperature(alt_km: float) -> float:
    """Temperature in K at given altitude."""
    return _interp(alt_km, _alts, _temps)


def get_pressure(alt_km: float) -> float:
    """Pressure in bar at given altitude."""
    return _interp_log(alt_km, _alts, _press)


def get_wind_speed(alt_km: float) -> float:
    """Horizontal wind speed in m/s (equatorial jet) at given altitude."""
    return _interp(alt_km, _wind_alts, _wind_speed)
