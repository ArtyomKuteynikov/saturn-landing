"""
Probe state machine and configuration.

Phases:
  ENTRY        — hypersonic entry, heat shield active
  DROGUE       — drogue parachute deployed
  MAIN_CHUTE   — main parachute deployed
  SCIENCE      — science instruments active (reached target pressure)
  SUCCESS      — mission complete (survived to target pressure + required data time)
  FAILED       — destroyed by heat / pressure / g-load / parachute failure
"""

import math
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Phase(Enum):
    ENTRY = auto()
    DROGUE = auto()
    MAIN_CHUTE = auto()
    SCIENCE = auto()
    SUCCESS = auto()
    FAILED = auto()


class FailureReason(Enum):
    OVERHEAT = "Перегрев: температура превысила допустимый предел"
    OVERPRESSURE = "Превышение давления: более 100 бар"
    GLOAD = "Разрушение конструкции: перегрузка превысила лимит"
    CHUTE_FAIL = "Отказ парашюта: раскрытие на гиперзвуковой скорости"
    SHIELD_FAIL = "Тепловой щит сброшен на опасной скорости"


# ---------------------------------------------------------------------------
# Probe physical parameters
# ---------------------------------------------------------------------------
@dataclass
class ProbeConfig:
    # --- Entry conditions (set from config screen) ---
    entry_speed_ms: float = 29_500.0  # m/s — speed at atmospheric interface
    entry_angle_deg: float = 15.0  # degrees below horizontal

    # --- Geometry ---
    mass_kg: float = 340.0  # kg
    cone_radius_m: float = 0.9  # m — base radius of capsule
    cone_half_angle_deg: float = 45.0  # degrees — half-angle of nose cone

    # --- Parachute system ---
    num_parachutes: int = 2  # 1 = main only; 2 = drogue + main
    drogue_area_m2: float = 2.5  # m²
    main_chute_area_m2: float = 20.0  # m²
    auto_deploy_drogue_ms: float = 600.0  # m/s — auto-deploy drogue below this speed
    auto_deploy_main_ms: float = 150.0  # m/s — auto-deploy main chute below this speed

    # --- Mission targets ---
    target_pressure_bar: float = 10.0  # bar — science phase begins here
    science_duration_s: float = 1800.0  # seconds of data transmission required

    # --- Critical limits ---
    max_temperature_k: float = 500.0  # K — operational limit (no shield)
    max_pressure_bar: float = 100.0  # bar
    max_gload: float = 100.0  # g

    # --- Derived (computed in __post_init__) ---
    frontal_area_m2: float = field(init=False)
    cd_shield: float = field(init=False)
    cd_drogue: float = field(init=False)
    cd_main: float = field(init=False)

    def __post_init__(self):
        self._recompute()

    def _recompute(self):
        """Recompute derived values from geometry inputs."""
        # Frontal area from cone base radius
        self.frontal_area_m2 = math.pi * self.cone_radius_m ** 2

        # Cd based on cone half-angle (Newton sine-squared law for hypersonic flow)
        # Blunter cone (large angle) → higher drag → better deceleration, more heating
        # Range: 20° → ~0.7,  45° → ~1.2,  70° → ~1.7
        half_rad = math.radians(self.cone_half_angle_deg)
        self.cd_shield = 0.5 + 1.5 * math.sin(half_rad) ** 2

        # With drogue/main chute: reduced body drag (canopy dominates)
        self.cd_drogue = 0.6
        self.cd_main = 0.5

    @classmethod
    def from_dict(cls, d: dict) -> "ProbeConfig":
        """Build config from config_screen parameter dict."""
        cfg = cls(
            entry_speed_ms=d["entry_speed_kms"] * 1000.0,
            entry_angle_deg=d["entry_angle_deg"],
            mass_kg=d["mass_kg"],
            cone_radius_m=d["cone_radius_m"],
            cone_half_angle_deg=d["cone_half_angle_deg"],
            num_parachutes=int(d["num_parachutes"]),
            drogue_area_m2=d["drogue_area_m2"],
            main_chute_area_m2=d["main_chute_area_m2"],
            auto_deploy_drogue_ms=d["auto_drogue_ms"],
            auto_deploy_main_ms=d["auto_main_ms"],
            target_pressure_bar=d["target_pressure_bar"],
            # science_duration stored as minutes in UI
            science_duration_s=d["science_duration_s"] * 60.0,
        )
        return cfg


# ---------------------------------------------------------------------------
# Probe state
# ---------------------------------------------------------------------------
@dataclass
class ProbeState:
    # Position & kinematics
    altitude_km: float = 400.0
    velocity_ms: float = 0.0  # vertical speed, m/s  (positive = descending)
    horiz_velocity: float = 0.0  # horizontal speed, m/s
    horiz_position_km: float = 0.0

    # Time
    elapsed_s: float = 0.0

    # Phase
    phase: Phase = Phase.ENTRY

    # Systems
    heat_shield_on: bool = True
    drogue_deployed: bool = False
    main_chute_deployed: bool = False
    instruments_on: bool = False

    # Auto-deploy flags (set once per deployment to avoid repeat messages)
    auto_drogue_triggered: bool = False
    auto_main_triggered: bool = False

    # Atmospheric readings (updated each step)
    temperature_k: float = 80.0
    pressure_bar: float = 1e-7
    density: float = 3e-7
    wind_speed: float = 0.0

    # Derived quantities
    gload: float = 0.0
    heat_flux: float = 0.0  # W/m² approx, for display

    # Failure
    failure_reason: Optional[FailureReason] = None

    # Science data collected
    science_data_s: float = 0.0

    # Peak values
    peak_gload: float = 0.0
    peak_temperature: float = 0.0
    peak_pressure: float = 0.0

    @classmethod
    def from_config(cls, config: "ProbeConfig") -> "ProbeState":
        """Initialise state from config (entry angle sets velocity components)."""
        angle_rad = math.radians(config.entry_angle_deg)
        v_vert = config.entry_speed_ms * math.sin(angle_rad)  # downward
        v_horiz = config.entry_speed_ms * math.cos(angle_rad)  # horizontal
        return cls(
            altitude_km=400.0,
            velocity_ms=v_vert,
            horiz_velocity=v_horiz,
        )

    # ------------------------------------------------------------------
    def effective_cd_area(self, config: "ProbeConfig") -> tuple[float, float]:
        """Returns (Cd, effective_area_m2) for current phase."""
        if self.main_chute_deployed:
            return config.cd_main, config.frontal_area_m2 + config.main_chute_area_m2
        if self.drogue_deployed:
            return config.cd_drogue, config.frontal_area_m2 + config.drogue_area_m2
        return config.cd_shield, config.frontal_area_m2

    def is_alive(self) -> bool:
        return self.phase not in (Phase.SUCCESS, Phase.FAILED)

    def is_terminal(self) -> bool:
        return self.phase in (Phase.SUCCESS, Phase.FAILED)

    # ------------------------------------------------------------------
    # Manual controls
    # ------------------------------------------------------------------
    def deploy_drogue(self, config: "ProbeConfig") -> Optional[str]:
        if not self.is_alive():
            return "Миссия уже завершена"
        if self.drogue_deployed:
            return "Тормозной парашют уже раскрыт"
        if config.num_parachutes < 2:
            return "Тормозной парашют не предусмотрен"
        # Catastrophic failure if still hypersonic
        if self.velocity_ms > config.auto_deploy_drogue_ms * 2.5:
            self.failure_reason = FailureReason.CHUTE_FAIL
            self.phase = Phase.FAILED
            return f"FAILURE: {self.failure_reason.value}"
        self.drogue_deployed = True
        self.auto_drogue_triggered = True
        if self.phase == Phase.ENTRY:
            self.phase = Phase.DROGUE
        return None

    def deploy_main_chute(self, config: "ProbeConfig") -> Optional[str]:
        if not self.is_alive():
            return "Миссия уже завершена"
        if self.main_chute_deployed:
            return "Основной парашют уже раскрыт"
        if config.num_parachutes >= 2 and not self.drogue_deployed:
            return "Сначала раскройте тормозной парашют"
        if self.velocity_ms > config.auto_deploy_main_ms * 4:
            self.failure_reason = FailureReason.CHUTE_FAIL
            self.phase = Phase.FAILED
            return f"FAILURE: {self.failure_reason.value}"
        self.main_chute_deployed = True
        self.auto_main_triggered = True
        self.phase = Phase.MAIN_CHUTE
        return None

    def jettison_heat_shield(self) -> Optional[str]:
        if not self.heat_shield_on:
            return "Тепловой щит уже сброшен"
        if self.velocity_ms > 3000:
            self.failure_reason = FailureReason.SHIELD_FAIL
            self.phase = Phase.FAILED
            return f"FAILURE: {self.failure_reason.value}"
        self.heat_shield_on = False
        return None

    def toggle_instruments(self) -> Optional[str]:
        if not self.is_alive():
            return "Миссия завершена"
        self.instruments_on = not self.instruments_on
        return None

    # ------------------------------------------------------------------
    # Limit checks
    # ------------------------------------------------------------------
    def check_limits(self, config: "ProbeConfig") -> bool:
        """Returns False and sets failed state if any limit exceeded."""
        if not self.heat_shield_on and self.temperature_k > config.max_temperature_k:
            self.failure_reason = FailureReason.OVERHEAT
            self.phase = Phase.FAILED
            return False
        if self.pressure_bar > config.max_pressure_bar:
            self.failure_reason = FailureReason.OVERPRESSURE
            self.phase = Phase.FAILED
            return False
        if self.gload > config.max_gload:
            self.failure_reason = FailureReason.GLOAD
            self.phase = Phase.FAILED
            return False
        return True

    def check_success(self, config: "ProbeConfig"):
        if self.phase in (Phase.MAIN_CHUTE, Phase.DROGUE, Phase.ENTRY):
            if self.pressure_bar >= config.target_pressure_bar:
                self.phase = Phase.SCIENCE
                self.instruments_on = True
        if self.phase == Phase.SCIENCE:
            if self.science_data_s >= config.science_duration_s:
                self.phase = Phase.SUCCESS
