"""
Pygame renderer — scientific mission-control aesthetic.

Layout:
  Left  (0 .. SCENE_W):        Scrolling atmosphere view, probe fixed at 35% height
  Right (SCENE_W .. SCREEN_W): Telemetry + systems + event log + controls
"""

import math
import random

import pygame

import atmosphere as atm
from probe import ProbeState, ProbeConfig, Phase

# ─────────────────────────────────────────────────────────────────────────────
# Dimensions
# ─────────────────────────────────────────────────────────────────────────────
SCREEN_W = 1280
SCREEN_H = 720
SCENE_W = 820
HUD_X = SCENE_W
HUD_W = SCREEN_W - SCENE_W

# Probe is rendered at this fixed Y inside the scene
PROBE_Y = int(SCREEN_H * 0.38)

# Visible altitude window: probe_alt ± these km
KM_ABOVE = 56.0
KM_BELOW = 104.0
VIEW_KM = KM_ABOVE + KM_BELOW
PX_PER_KM = SCREEN_H / VIEW_KM  # ~4.5 px/km

# ─────────────────────────────────────────────────────────────────────────────
# Colour palette  (mission-control / scientific terminal)
# ─────────────────────────────────────────────────────────────────────────────
C_BG = (5, 8, 14)  # near-black space
C_PANEL = (8, 10, 18)  # HUD background
C_BORDER = (35, 45, 65)  # thin panel borders
C_GRID = (18, 24, 36)  # scene grid lines
C_DIM = (60, 72, 88)  # secondary/disabled text
C_BRIGHT = (195, 210, 220)  # primary text
C_CYAN = (0, 210, 230)  # accent — primary data readouts
C_AMBER = (220, 155, 0)  # accent — labels / section headers
C_OK = (0, 175, 80)  # nominal status
C_WARN = (215, 165, 0)  # caution
C_CRIT = (200, 40, 40)  # critical / failure
C_NOMINAL = (0, 130, 195)  # informational blue

# Atmosphere gradient stops  (altitude_km → RGB)
_ATM_GRADIENT = [
    (400, (5, 8, 14)),  # space
    (200, (12, 12, 20)),
    (100, (20, 18, 24)),
    (60, (35, 28, 18)),  # haze begins
    (20, (55, 42, 18)),
    (0, (75, 58, 22)),  # 1-bar level
    (-30, (90, 68, 24)),  # ammonia cloud layer
    (-60, (70, 55, 28)),  # NH4SH clouds
    (-90, (65, 75, 85)),  # water clouds — slightly blue-grey
    (-150, (85, 50, 20)),
    (-200, (105, 45, 10)),  # deep atmosphere, reddish
]


def _lerp_color(c1, c2, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))


def _atm_color_at(alt_km: float) -> tuple:
    stops = _ATM_GRADIENT
    if alt_km >= stops[0][0]:  return stops[0][1]
    if alt_km <= stops[-1][0]: return stops[-1][1]
    for i in range(len(stops) - 1):
        a0, c0 = stops[i]
        a1, c1 = stops[i + 1]
        if a1 <= alt_km <= a0:
            t = (a0 - alt_km) / (a0 - a1)
            return _lerp_color(c0, c1, t)
    return stops[-1][1]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _scene_y(probe_alt_km: float, element_alt_km: float) -> int:
    """Y-pixel in scene for an element at element_alt_km, given current probe altitude."""
    delta_km = probe_alt_km - element_alt_km  # positive → element below probe
    return int(PROBE_Y + delta_km * PX_PER_KM)


def _status_color(value: float, warn: float, crit: float) -> tuple:
    if value >= crit: return C_CRIT
    if value >= warn: return C_WARN
    return C_OK


# ─────────────────────────────────────────────────────────────────────────────
# Buttons
# ─────────────────────────────────────────────────────────────────────────────
BUTTONS = [
    {"id": "drogue", "label": "ТОРМОЗНОЙ ПАРАШЮТ", "key": "D"},
    {"id": "main_chute", "label": "ОСНОВНОЙ ПАРАШЮТ", "key": "M"},
    {"id": "jettison", "label": "СБРОС ЩИТА", "key": "J"},
    {"id": "instruments", "label": "ПРИБОРЫ", "key": "I"},
    {"id": "restart", "label": "ПЕРЕЗАПУСК", "key": "R"},
]
_BTN_W, _BTN_H, _BTN_GAP = 215, 32, 6


# ─────────────────────────────────────────────────────────────────────────────
# Renderer
# ─────────────────────────────────────────────────────────────────────────────
class Renderer:
    def __init__(self, screen: pygame.Surface):
        self.screen = screen
        pygame.font.init()
        self.f_head = pygame.font.SysFont("Consolas", 13, bold=True)
        self.f_data = pygame.font.SysFont("Consolas", 15, bold=True)
        self.f_label = pygame.font.SysFont("Consolas", 13)
        self.f_small = pygame.font.SysFont("Consolas", 11)
        self.f_phase = pygame.font.SysFont("Consolas", 16, bold=True)

        # Stars fixed positions (only visible in upper portion of scene)
        rng = random.Random(7)
        self.stars = [
            (rng.randint(0, SCENE_W), rng.randint(0, SCREEN_H // 3),
             rng.random() * 0.6 + 0.4)
            for _ in range(280)
        ]

        # Button rects
        self.button_rects: dict[str, pygame.Rect] = {}
        bx = HUD_X + (HUD_W - _BTN_W) // 2
        by = SCREEN_H - len(BUTTONS) * (_BTN_H + _BTN_GAP) - 14
        for btn in BUTTONS:
            self.button_rects[btn["id"]] = pygame.Rect(bx, by, _BTN_W, _BTN_H)
            by += _BTN_H + _BTN_GAP

        # Pre-built scene surface for atmosphere gradient (rebuilt when scrolled)
        self._atm_surf: pygame.Surface | None = None
        self._atm_center_alt: float | None = None  # altitude when we last built the surface

        self._frame = 0

    # ─────────────────────────────────────────────────────────────────────────
    def draw(self, state: ProbeState, config: ProbeConfig, messages: list[str]):
        self._frame += 1
        self._draw_scene(state)
        self._draw_hud(state, config, messages)
        pygame.display.flip()

    # ─────────────────────────────────────────────────────────────────────────
    # Scene
    # ─────────────────────────────────────────────────────────────────────────
    def _draw_scene(self, state: ProbeState):
        surf = self.screen
        alt = state.altitude_km

        # --- Atmosphere gradient background ---
        # Rebuild only when probe has moved significantly
        if (self._atm_center_alt is None or
                abs(self._atm_center_alt - alt) > 0.5):
            self._build_atm_surface(alt)
            self._atm_center_alt = alt

        surf.blit(self._atm_surf, (0, 0))

        # --- Stars (fade in above ~150 km) ---
        star_alpha = max(0.0, min(1.0, (alt - 80) / 120.0))
        if star_alpha > 0:
            for sx, sy, brightness in self.stars:
                twinkle = 0.75 + 0.25 * math.sin(self._frame * 0.04 + sx * 0.3)
                a = int(star_alpha * brightness * twinkle * 200)
                pygame.draw.circle(surf, (a, a, a), (sx, sy), 1)

        # --- Altitude grid lines & labels ---
        self._draw_altitude_ruler(surf, alt)

        # --- Rings (visible when alt > 100 km, fade in) ---
        ring_alpha = max(0.0, min(1.0, (alt - 100) / 150.0))
        if ring_alpha > 0:
            self._draw_rings(surf, ring_alpha)

        # --- Cloud layers ---
        self._draw_cloud_layers(surf, alt)

        # --- 1-bar reference line ---
        y_one_bar = _scene_y(alt, 0.0)
        if -5 < y_one_bar < SCREEN_H + 5:
            pygame.draw.line(surf, (60, 55, 35), (0, y_one_bar), (SCENE_W, y_one_bar), 1)
            lbl = self.f_small.render("▶ 1 BAR", True, (90, 80, 40))
            surf.blit(lbl, (6, y_one_bar + 2))

        # --- Probe ---
        self._draw_probe(surf, SCENE_W // 2, PROBE_Y, state)

        # --- Entry plasma glow ---
        if state.heat_shield_on and state.velocity_ms > 4000:
            intensity = min(140, int(state.velocity_ms / 250))
            glow = pygame.Surface((SCENE_W, SCREEN_H), pygame.SRCALPHA)
            radius = int(60 + state.velocity_ms / 500)
            pygame.draw.circle(glow, (255, 90, 10, intensity // 5),
                               (SCENE_W // 2, PROBE_Y + 15), radius)
            surf.blit(glow, (0, 0))

        # --- Phase banner ---
        self._draw_phase_banner(surf, state)

        # --- Scene border ---
        pygame.draw.line(surf, C_BORDER, (SCENE_W - 1, 0), (SCENE_W - 1, SCREEN_H), 1)

    def _build_atm_surface(self, center_alt_km: float):
        """Pre-render the atmosphere gradient background into a surface."""
        s = pygame.Surface((SCENE_W, SCREEN_H))
        for py in range(SCREEN_H):
            # altitude corresponding to this pixel row
            row_alt = center_alt_km + (PROBE_Y - py) / PX_PER_KM
            col = _atm_color_at(row_alt)
            pygame.draw.line(s, col, (0, py), (SCENE_W, py))
        self._atm_surf = s

    def _draw_altitude_ruler(self, surf: pygame.Surface, probe_alt: float):
        """Thin ruler on the left edge with altitude tick marks."""
        visible_top = probe_alt + KM_ABOVE
        visible_bot = probe_alt - KM_BELOW

        # Pick tick spacing based on speed (wide when fast, fine when slow)
        if abs(probe_alt) > 200:
            spacing = 100
        elif abs(probe_alt) > 50:
            spacing = 50
        else:
            spacing = 10

        first = int(visible_bot // spacing) * spacing
        km = first
        while km <= visible_top:
            py = _scene_y(probe_alt, km)
            if 0 <= py <= SCREEN_H:
                is_major = (km % (spacing * 5) == 0) or spacing <= 10
                col = C_DIM if not is_major else (80, 95, 115)
                tick_w = 14 if is_major else 7
                pygame.draw.line(surf, col, (SCENE_W - tick_w, py), (SCENE_W - 1, py), 1)
                if is_major:
                    lbl = self.f_small.render(f"{km:+d}", True, C_DIM)
                    surf.blit(lbl, (SCENE_W - tick_w - lbl.get_width() - 2, py - 6))
            km += spacing

        # Current altitude marker
        pygame.draw.line(surf, C_CYAN, (SCENE_W - 20, PROBE_Y), (SCENE_W - 1, PROBE_Y), 1)

    def _draw_rings(self, surf: pygame.Surface, alpha: float):
        """Saturn rings in background (high altitude only)."""
        ring_data = [
            (SCREEN_H // 7 + 10, 340, 16, (180, 155, 90)),
            (SCREEN_H // 7 - 4, 260, 10, (150, 128, 72)),
            (SCREEN_H // 7 + 26, 180, 7, (130, 110, 60)),
        ]
        for ry, half_w, ring_h, col in ring_data:
            a = int(alpha * 80)
            rs = pygame.Surface((SCENE_W, ring_h + 4), pygame.SRCALPHA)
            r, g, b = col
            pygame.draw.ellipse(rs, (r, g, b, a),
                                pygame.Rect(SCENE_W // 2 - half_w, 2, half_w * 2, ring_h))
            surf.blit(rs, (0, ry))

    def _draw_cloud_layers(self, surf: pygame.Surface, probe_alt: float):
        for layer in atm.CLOUD_LAYERS:
            center_alt = layer["alt_km"]
            half_px = int(layer["thickness_km"] * PX_PER_KM / 2)
            cy = _scene_y(probe_alt, center_alt)
            if cy + half_px < 0 or cy - half_px > SCREEN_H:
                continue
            col_base = layer["color"]
            # Proximity to probe affects opacity (more visible when near)
            dist_km = abs(probe_alt - center_alt)
            fade = max(0, min(100, int(110 - dist_km * 0.6)))
            cs = pygame.Surface((SCENE_W, half_px * 2 + 1), pygame.SRCALPHA)
            r, g, b = col_base[:3]
            cs.fill((r, g, b, fade))
            surf.blit(cs, (0, cy - half_px))
            # Label only when close
            if dist_km < 60:
                lbl = self.f_small.render(layer["name"].upper(), True,
                                          tuple(min(255, c + 40) for c in col_base[:3]))
                surf.blit(lbl, (8, cy - half_px + 2))

    def _draw_probe(self, surf: pygame.Surface, x: int, y: int, state: ProbeState):
        # Main chute
        if state.main_chute_deployed:
            # Canopy
            canopy_pts = []
            for i in range(11):
                a = math.pi * i / 10
                cx = x + int(math.cos(a) * 40)
                cy = y - 75 + int(math.sin(a) * 22)
                canopy_pts.append((cx, cy))
            canopy_pts += [(x + 8, y - 16), (x - 8, y - 16)]
            pygame.draw.polygon(surf, (160, 160, 170), canopy_pts, 0)
            pygame.draw.polygon(surf, (200, 200, 210), canopy_pts, 1)
            # Risers
            for dx in [-38, -15, 15, 38]:
                pygame.draw.line(surf, (100, 100, 110), (x + dx, y - 70), (x, y - 16), 1)

        # Drogue chute
        if state.drogue_deployed and not state.main_chute_deployed:
            for i in range(5):
                a = math.pi * i / 4
                cx = x + int(math.cos(a) * 14)
                cy = y - 28 + int(math.sin(a) * 8)
                pygame.draw.line(surf, (150, 150, 160), (cx, cy), (x, y - 16), 1)
            pygame.draw.arc(surf, (180, 180, 190),
                            pygame.Rect(x - 14, y - 36, 28, 16), 0, math.pi, 2)

        # Heat shield glow (plasma trail)
        if state.heat_shield_on and state.velocity_ms > 1500:
            trail_len = min(80, int(state.velocity_ms / 400))
            for i in range(trail_len):
                a = int(80 * (1 - i / trail_len))
                tc = pygame.Surface((20, 4), pygame.SRCALPHA)
                heat_r = min(255, 180 + int(state.velocity_ms / 200))
                tc.fill((heat_r, 60, 10, a))
                surf.blit(tc, (x - 10, y + 14 + i))

        # Probe body — angular capsule shape
        body_pts = [
            (x, y - 18),  # nose tip
            (x + 9, y - 10),
            (x + 10, y + 2),
            (x + 7, y + 12),
            (x - 7, y + 12),
            (x - 10, y + 2),
            (x - 9, y - 10),
        ]
        pygame.draw.polygon(surf, (140, 148, 158), body_pts)
        pygame.draw.polygon(surf, (180, 190, 200), body_pts, 1)

        # Heat shield (flat bottom disc)
        if state.heat_shield_on:
            shield_col = (200, 80, 20) if state.velocity_ms > 3000 else (160, 120, 50)
            pygame.draw.ellipse(surf, shield_col,
                                pygame.Rect(x - 11, y + 9, 22, 7))
            pygame.draw.ellipse(surf, (220, 100, 30),
                                pygame.Rect(x - 11, y + 9, 22, 7), 1)

        # Instruments antenna
        if state.instruments_on:
            pygame.draw.line(surf, C_OK, (x, y - 18), (x, y - 30), 1)
            pygame.draw.line(surf, C_OK, (x, y - 26), (x - 6, y - 30), 1)
            pygame.draw.line(surf, C_OK, (x, y - 26), (x + 6, y - 30), 1)

    def _draw_phase_banner(self, surf: pygame.Surface, state: ProbeState):
        phase_info = {
            Phase.ENTRY: ("ГИПЕРЗВУКОВОЙ ВХОД", C_CRIT),
            Phase.DROGUE: ("ТОРМОЗНОЙ ПАРАШЮТ", C_WARN),
            Phase.MAIN_CHUTE: ("ОСНОВНОЙ ПАРАШЮТ", C_NOMINAL),
            Phase.SCIENCE: ("НАУЧНЫЕ ОПЕРАЦИИ", C_OK),
            Phase.SUCCESS: ("МИССИЯ ВЫПОЛНЕНА", C_OK),
            Phase.FAILED: ("МИССИЯ ПРЕРВАНА", C_CRIT),
        }
        text, col = phase_info.get(state.phase, ("", C_BRIGHT))
        if not text:
            return
        lbl = self.f_phase.render(f"[ {text} ]", True, col)
        blink = (self._frame % 40 < 30) or state.phase not in (Phase.ENTRY, Phase.FAILED)
        if blink:
            surf.blit(lbl, (SCENE_W // 2 - lbl.get_width() // 2, 8))

    # ─────────────────────────────────────────────────────────────────────────
    # HUD panel
    # ─────────────────────────────────────────────────────────────────────────
    def _draw_hud(self, state: ProbeState, config: ProbeConfig, messages: list[str]):
        surf = self.screen
        x0 = HUD_X + 8
        x1 = SCREEN_W - 8
        mid = HUD_X + HUD_W // 2

        # Background
        pygame.draw.rect(surf, C_PANEL, pygame.Rect(HUD_X, 0, HUD_W, SCREEN_H))

        # Title bar
        pygame.draw.rect(surf, (12, 18, 30), pygame.Rect(HUD_X, 0, HUD_W, 26))
        t = self.f_head.render("АТМОСФЕРНЫЙ ЗОНД САТУРНА  //  ТЕЛЕМЕТРИЯ", True, C_AMBER)
        surf.blit(t, (mid - t.get_width() // 2, 6))

        y = 30
        y = self._section(surf, y, x0, x1, "НАВИГАЦИЯ")
        y = self._trow(surf, y, x0, x1, "ВЫСОТА",
                       f"{state.altitude_km:+9.2f} км", C_CYAN)
        y = self._trow(surf, y, x0, x1, "СКОР. СНИЖЕНИЯ",
                       f"{state.velocity_ms:9.1f} м/с",
                       _status_color(state.velocity_ms, 5000, 15000))
        y = self._trow(surf, y, x0, x1, "ГОРИЗОНТ. СКОР.",
                       f"{state.horiz_velocity:8.1f} м/с", C_BRIGHT)
        y = self._trow(surf, y, x0, x1, "СНОС",
                       f"{state.horiz_position_km:+7.2f} км", C_DIM)
        y = self._trow(surf, y, x0, x1, "ВРЕМЯ ПОЛЁТА",
                       self._fmt_time(state.elapsed_s), C_BRIGHT)
        y += 3

        y = self._section(surf, y, x0, x1, "АТМОСФЕРНЫЕ УСЛОВИЯ")
        y = self._trow(surf, y, x0, x1, "ДАВЛЕНИЕ",
                       f"{state.pressure_bar:10.4f} бар",
                       _status_color(state.pressure_bar,
                                     config.target_pressure_bar * 0.5,
                                     config.max_pressure_bar * 0.8))
        y = self._trow(surf, y, x0, x1, "ТЕМПЕРАТУРА",
                       f"{state.temperature_k:8.1f} К",
                       _status_color(state.temperature_k,
                                     config.max_temperature_k * 0.6,
                                     config.max_temperature_k * 0.9))
        y = self._trow(surf, y, x0, x1, "ПЛОТНОСТЬ",
                       f"{state.density:.3e} кг/м³", C_DIM)
        y = self._trow(surf, y, x0, x1, "СКОР. ВЕТРА",
                       f"{state.wind_speed:7.1f} м/с", C_DIM)
        y += 3

        y = self._section(surf, y, x0, x1, "ДИНАМИКА ЗОНДА")
        g_col = _status_color(state.gload, 30, 70)
        y = self._trow(surf, y, x0, x1, "ПЕРЕГРУЗКА",
                       f"{state.gload:9.2f} g", g_col)
        # Bar drawn BELOW the text row, before next item
        self._bar(surf, y + 1, x0, x1, state.gload / config.max_gload, g_col)
        y += 9
        y = self._trow(surf, y, x0, x1, "ТЕПЛОВОЙ ПОТОК",
                       f"{max(0, state.heat_flux):.2e} Вт/м²",
                       C_WARN if state.heat_flux > 1e6 else C_DIM)
        y += 3

        # Science progress (only when active)
        if state.phase == Phase.SCIENCE:
            y = self._section(surf, y, x0, x1, "ПЕРЕДАЧА ДАННЫХ")
            pct = min(1.0, state.science_data_s / config.science_duration_s)
            y = self._trow(surf, y, x0, x1, "ПЕРЕДАНО",
                           self._fmt_time(state.science_data_s), C_OK)
            self._bar(surf, y + 1, x0, x1, pct, C_OK, label=f"{pct * 100:.0f}%")
            y += 9
            required = self._fmt_time(config.science_duration_s)
            y = self._trow(surf, y, x0, x1, "ТРЕБУЕТСЯ",
                           required, C_DIM)
            y += 3

        y = self._section(surf, y, x0, x1, "СИСТЕМЫ")
        y = self._sysrow(surf, y, x0, "ТЕПЛОВОЙ ЩИТ",
                         state.heat_shield_on, "АКТИВЕН", "СБРОШЕН")
        y = self._sysrow(surf, y, x0, "ТОРМОЗНОЙ ПАР.",
                         state.drogue_deployed, "РАСКРЫТ", "УБРАН")
        y = self._sysrow(surf, y, x0, "ОСНОВНОЙ ПАР.",
                         state.main_chute_deployed, "РАСКРЫТ", "УБРАН")
        y = self._sysrow(surf, y, x0, "ПРИБОРЫ",
                         state.instruments_on, "АКТИВНЫ", "ОЖИДАНИЕ")
        y += 3

        y = self._section(surf, y, x0, x1, "ПИКОВЫЕ ЗНАЧЕНИЯ")
        y = self._trow(surf, y, x0, x1, "МАКС. ПЕРЕГРУЗКА",
                       f"{state.peak_gload:.2f} g", C_DIM)
        y = self._trow(surf, y, x0, x1, "МАКС. ТЕМПЕРАТУРА",
                       f"{state.peak_temperature:.0f} К", C_DIM)
        y = self._trow(surf, y, x0, x1, "МАКС. ДАВЛЕНИЕ",
                       f"{state.peak_pressure:.3f} бар", C_DIM)
        y += 3

        y = self._section(surf, y, x0, x1, "ЖУРНАЛ СОБЫТИЙ")
        for msg in messages[-5:]:
            if "ОТКАЗ" in msg or "РАЗРУШ" in msg or "СБРОС" in msg.upper() or "FAILURE" in msg.upper():
                col = C_CRIT
            elif "УСПЕХ" in msg or "АВТО" in msg or "РАСКРЫТ" in msg or "AUTO" in msg.upper():
                col = C_OK
            else:
                col = (100, 118, 135)
            lbl = self.f_small.render(msg[:40], True, col)
            surf.blit(lbl, (x0, y))
            y += 14

        # Buttons
        self._draw_buttons(surf, state)

        # Terminal overlay on failure/success
        if state.phase == Phase.FAILED and state.failure_reason:
            self._draw_terminal_msg(state.failure_reason.value, C_CRIT)
        elif state.phase == Phase.SUCCESS:
            self._draw_terminal_msg("МИССИЯ ВЫПОЛНЕНА — ДАННЫЕ ПЕРЕДАНЫ", C_OK)

    # ─── HUD helpers ─────────────────────────────────────────────────────────

    def _section(self, surf, y, x0, x1, title: str) -> int:
        """Draw a section header line. Returns new y."""
        pygame.draw.line(surf, C_BORDER, (x0, y + 5), (x1, y + 5), 1)
        lbl = self.f_head.render(f" {title} ", True, C_AMBER)
        # Overlay label on the line
        surf.blit(lbl, (x0 + 4, y - 1))
        return y + 16

    def _trow(self, surf, y, x0, x1, label: str, value: str, vcol=None) -> int:
        """Single telemetry row: label left, value right."""
        vcol = vcol or C_BRIGHT
        lbl = self.f_label.render(label, True, C_DIM)
        val = self.f_data.render(value, True, vcol)
        surf.blit(lbl, (x0 + 2, y))
        surf.blit(val, (x1 - val.get_width(), y - 1))
        return y + 17

    def _bar(self, surf, y, x0, x1, fraction: float, col: tuple,
             label: str = "") -> None:
        """Thin horizontal fill bar."""
        w = x1 - x0
        pygame.draw.rect(surf, C_GRID, pygame.Rect(x0, y, w, 5))
        filled = int(w * max(0.0, min(1.0, fraction)))
        if filled > 0:
            pygame.draw.rect(surf, col, pygame.Rect(x0, y, filled, 5))
        pygame.draw.rect(surf, C_BORDER, pygame.Rect(x0, y, w, 5), 1)
        if label:
            lbl = self.f_small.render(label, True, col)
            surf.blit(lbl, (x1 - lbl.get_width(), y - 13))

    def _sysrow(self, surf, y, x0, name: str,
                active: bool, on_label: str, off_label: str) -> int:
        # LED dot
        led_col = C_OK if active else C_DIM
        pygame.draw.circle(surf, led_col, (x0 + 5, y + 7), 4)
        pygame.draw.circle(surf, (led_col[0] // 2, led_col[1] // 2, led_col[2] // 2),
                           (x0 + 5, y + 7), 4, 1)
        lbl = self.f_label.render(f"  {name:<18}", True,
                                  C_BRIGHT if active else C_DIM)
        val = self.f_label.render(on_label if active else off_label,
                                  True, led_col)
        surf.blit(lbl, (x0 + 8, y))
        surf.blit(val, (HUD_X + HUD_W - val.get_width() - 8, y))
        return y + 17

    def _draw_buttons(self, surf: pygame.Surface, state: ProbeState):
        mx, my = pygame.mouse.get_pos()
        for btn in BUTTONS:
            rect = self.button_rects[btn["id"]]
            active = not state.is_terminal() or btn["id"] == "restart"
            hover = rect.collidepoint(mx, my) and active

            bg = (22, 30, 48) if active else (10, 12, 18)
            brd = (C_NOMINAL if hover else C_BORDER) if active else (25, 28, 35)
            pygame.draw.rect(surf, bg, rect)
            pygame.draw.rect(surf, brd, rect, 1)

            # Key badge
            key_rect = pygame.Rect(rect.x + rect.w - 22, rect.y + 6, 16, 20)
            pygame.draw.rect(surf, C_BORDER, key_rect)
            key_lbl = self.f_small.render(btn["key"], True, C_DIM)
            surf.blit(key_lbl, (key_rect.x + 4, key_rect.y + 4))

            col = C_BRIGHT if active else C_DIM
            lbl = self.f_label.render(btn["label"], True, col)
            surf.blit(lbl, (rect.x + 8,
                            rect.y + (rect.h - lbl.get_height()) // 2))

    def _draw_terminal_msg(self, text: str, col: tuple):
        """Full-width banner across scene bottom."""
        surf = self.screen
        banner = pygame.Surface((SCENE_W, 36), pygame.SRCALPHA)
        banner.fill((0, 0, 0, 200))
        surf.blit(banner, (0, SCREEN_H // 2 - 18))
        lbl = self.f_phase.render(f"// {text} //", True, col)
        surf.blit(lbl, (SCENE_W // 2 - lbl.get_width() // 2,
                        SCREEN_H // 2 - lbl.get_height() // 2))

    # ─────────────────────────────────────────────────────────────────────────
    def get_button_at(self, pos: tuple) -> str | None:
        for btn_id, rect in self.button_rects.items():
            if rect.collidepoint(pos):
                return btn_id
        return None

    @staticmethod
    def _fmt_time(seconds: float) -> str:
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m:02d}:{s:02d}"
