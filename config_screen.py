"""
Pre-simulation configuration screen.
User sets all probe and mission parameters before launching.
Returns a filled ProbeConfig (or None if user closed the window).
"""

import math
import pygame
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
SCREEN_W = 1280
SCREEN_H = 720

C_BG        = (  5,   8,  14)
C_PANEL     = (  8,  10,  18)
C_BORDER    = ( 35,  45,  65)
C_TITLE     = (220, 155,   0)   # amber
C_LABEL     = (195, 210, 220)
C_VALUE     = (  0, 210, 230)   # cyan
C_UNIT      = ( 80,  95, 110)
C_TRACK     = ( 18,  24,  36)
C_FILL      = (  0, 130, 180)
C_KNOB      = (  0, 200, 220)
C_KNOB_HOV  = ( 80, 230, 245)
C_BTN_BG    = ( 12,  20,  38)
C_BTN_HOV   = ( 22,  38,  70)
C_BTN_TXT   = (195, 210, 220)
C_DESC      = ( 65,  80,  95)
C_SECTION   = (  0, 160, 195)
C_WARN      = (215, 165,   0)


@dataclass
class ParamDef:
    key:        str
    label:      str
    unit:       str
    min_val:    float
    max_val:    float
    default:    float
    step:       float          # arrow key / scroll increment
    fmt:        str   = ".1f"  # format string for display
    desc:       str   = ""     # short description shown below label
    integer:    bool  = False  # snap to integer


# ---------------------------------------------------------------------------
# All configurable parameters
# ---------------------------------------------------------------------------
PARAM_SECTIONS = [
    {
        "title": "ENTRY CONDITIONS",
        "params": [
            ParamDef("entry_speed_kms", "Entry Speed",    "km/s",
                     min_val=24.0, max_val=35.0, default=29.5, step=0.5, fmt=".1f",
                     desc="Probe speed at atmospheric interface (~400 km)"),
            ParamDef("entry_angle_deg", "Entry Angle",    "deg",
                     min_val=5.0,  max_val=35.0, default=15.0, step=0.5, fmt=".1f",
                     desc="Angle below horizontal at entry (shallow=gentle, steep=hot)"),
        ],
    },
    {
        "title": "PROBE GEOMETRY",
        "params": [
            ParamDef("mass_kg",          "Probe Mass",      "kg",
                     min_val=200.0, max_val=600.0, default=340.0, step=10.0, fmt=".0f",
                     desc="Total probe mass including heat shield"),
            ParamDef("cone_radius_m",    "Cone Radius",     "m",
                     min_val=0.4,  max_val=2.5,  default=0.9,  step=0.05, fmt=".2f",
                     desc="Base radius of entry capsule (frontal area = π·r²)"),
            ParamDef("cone_half_angle_deg", "Cone Half-Angle", "deg",
                     min_val=15.0, max_val=75.0, default=45.0, step=1.0,  fmt=".0f",
                     desc="Half-angle of nose cone (blunter = more drag + more heat)"),
        ],
    },
    {
        "title": "PARACHUTE SYSTEM",
        "params": [
            ParamDef("num_parachutes",   "Parachutes",      "",
                     min_val=1.0,  max_val=2.0,  default=2.0,  step=1.0,  fmt=".0f",
                     integer=True,
                     desc="1 = main chute only · 2 = drogue + main chute"),
            ParamDef("drogue_area_m2",   "Drogue Area",     "m²",
                     min_val=1.0,  max_val=10.0, default=2.5,  step=0.5,  fmt=".1f",
                     desc="Drogue canopy area (active only if 2 parachutes)"),
            ParamDef("main_chute_area_m2","Main Chute Area","m²",
                     min_val=5.0,  max_val=50.0, default=20.0, step=1.0,  fmt=".0f",
                     desc="Main parachute canopy area"),
            ParamDef("auto_drogue_ms",   "Drogue Deploy",   "m/s",
                     min_val=100.0, max_val=3000.0, default=600.0, step=50.0, fmt=".0f",
                     desc="Auto-deploy drogue when speed drops below this"),
            ParamDef("auto_main_ms",     "Main Deploy",     "m/s",
                     min_val=30.0,  max_val=500.0, default=150.0, step=10.0, fmt=".0f",
                     desc="Auto-deploy main chute when speed drops below this"),
        ],
    },
    {
        "title": "MISSION TARGETS",
        "params": [
            ParamDef("target_pressure_bar", "Target Pressure", "bar",
                     min_val=5.0,  max_val=100.0, default=10.0, step=5.0,  fmt=".0f",
                     desc="Mission success: survive to this pressure"),
            ParamDef("science_duration_s",  "Science Time",    "min",
                     min_val=5.0,  max_val=120.0, default=30.0, step=5.0,  fmt=".0f",
                     desc="Minutes of science data required for full success"),
        ],
    },
]

# Flatten for indexed access
ALL_PARAMS: list[ParamDef] = [p for s in PARAM_SECTIONS for p in s["params"]]


# ---------------------------------------------------------------------------
# Slider widget
# ---------------------------------------------------------------------------
class Slider:
    HEIGHT = 8
    KNOB_R = 9

    def __init__(self, rect: pygame.Rect, pdef: ParamDef):
        self.rect  = rect   # track rectangle
        self.pdef  = pdef
        self.value = pdef.default
        self.dragging = False
        self._hover   = False

    def _val_to_x(self) -> int:
        t = (self.value - self.pdef.min_val) / (self.pdef.max_val - self.pdef.min_val)
        return int(self.rect.x + t * self.rect.width)

    def _x_to_val(self, x: int) -> float:
        t = (x - self.rect.x) / self.rect.width
        t = max(0.0, min(1.0, t))
        v = self.pdef.min_val + t * (self.pdef.max_val - self.pdef.min_val)
        if self.pdef.integer:
            v = round(v)
        else:
            # Snap to step grid
            steps = round((v - self.pdef.min_val) / self.pdef.step)
            v = self.pdef.min_val + steps * self.pdef.step
        return max(self.pdef.min_val, min(self.pdef.max_val, v))

    def handle_event(self, event: pygame.event.Event) -> bool:
        """Returns True if value changed."""
        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            kx = self._val_to_x()
            ky = self.rect.centery
            if math.hypot(event.pos[0] - kx, event.pos[1] - ky) <= self.KNOB_R + 4:
                self.dragging = True
                return False
        if event.type == pygame.MOUSEBUTTONUP and event.button == 1:
            self.dragging = False
        if event.type == pygame.MOUSEMOTION and self.dragging:
            old = self.value
            self.value = self._x_to_val(event.pos[0])
            return self.value != old
        if event.type == pygame.MOUSEWHEEL:
            kx = self._val_to_x()
            if abs(pygame.mouse.get_pos()[0] - kx) < 60 and \
               abs(pygame.mouse.get_pos()[1] - self.rect.centery) < 30:
                delta = self.pdef.step * event.y
                self.value = max(self.pdef.min_val,
                                 min(self.pdef.max_val, self.value + delta))
                if self.pdef.integer:
                    self.value = round(self.value)
                return True
        return False

    def draw(self, surf: pygame.Surface, fonts: dict):
        cx = self._val_to_x()
        cy = self.rect.centery
        mx, my = pygame.mouse.get_pos()
        hover = math.hypot(mx - cx, my - cy) <= self.KNOB_R + 6

        # Track (flat, no border-radius — scientific look)
        pygame.draw.rect(surf, C_TRACK, self.rect)
        pygame.draw.rect(surf, C_BORDER, self.rect, 1)
        # Fill
        fill_rect = pygame.Rect(self.rect.x, self.rect.y,
                                cx - self.rect.x, self.rect.height)
        if fill_rect.width > 0:
            pygame.draw.rect(surf, C_FILL, fill_rect)
        # Knob — thin vertical bar instead of circle
        knob_col = C_KNOB_HOV if (hover or self.dragging) else C_KNOB
        pygame.draw.rect(surf, knob_col,
                         pygame.Rect(cx - 2, cy - self.KNOB_R, 4, self.KNOB_R * 2))

    def get_display_value(self) -> str:
        v = self.value
        return f"{v:{self.pdef.fmt}}"


# ---------------------------------------------------------------------------
# Config screen
# ---------------------------------------------------------------------------
class ConfigScreen:
    SLIDER_W    = 320
    SLIDER_H    = Slider.HEIGHT
    ROW_H       = 68     # height per parameter row
    COL_GAP     = 60
    LEFT_COL_X  = 50
    RIGHT_COL_X = 680
    LABEL_W     = 170

    def __init__(self):
        pygame.font.init()
        self.font_title   = pygame.font.SysFont("Consolas", 17, bold=True)
        self.font_section = pygame.font.SysFont("Consolas", 13, bold=True)
        self.font_label   = pygame.font.SysFont("Consolas", 13, bold=True)
        self.font_value   = pygame.font.SysFont("Consolas", 14, bold=True)
        self.font_desc    = pygame.font.SysFont("Consolas", 11)
        self.fonts = {
            "title": self.font_title,
            "section": self.font_section,
            "label": self.font_label,
            "value": self.font_value,
            "desc": self.font_desc,
        }

        # Build layout: place params in two columns by section
        self.sliders: dict[str, Slider] = {}
        self._layout: list[dict] = []    # [{type, y, col, ...}]
        self._build_layout()

        # Launch button
        self.launch_rect = pygame.Rect(SCREEN_W // 2 - 140, SCREEN_H - 64, 280, 48)

    def _build_layout(self):
        """Assign screen positions to all sections and params (two-column layout)."""
        START_Y = 70
        col_x   = [self.LEFT_COL_X, self.RIGHT_COL_X]
        col_y   = [START_Y, START_Y]

        for section in PARAM_SECTIONS:
            # Place section heading in whichever column has less content
            col = 0 if col_y[0] <= col_y[1] else 1
            x = col_x[col]
            y = col_y[col]

            self._layout.append({
                "type": "section", "title": section["title"],
                "x": x, "y": y, "col": col,
            })
            col_y[col] += 30

            for pdef in section["params"]:
                y = col_y[col]
                slider_x = x + self.LABEL_W + 10
                slider_rect = pygame.Rect(slider_x, y + 18, self.SLIDER_W, self.SLIDER_H)
                slider = Slider(slider_rect, pdef)
                self.sliders[pdef.key] = slider

                self._layout.append({
                    "type": "param", "pdef": pdef,
                    "x": x, "y": y, "col": col,
                    "slider": slider,
                })
                col_y[col] += self.ROW_H

    def run(self, screen: pygame.Surface) -> Optional[dict]:
        """
        Run the config screen loop.
        Returns dict of param values, or None if user quit.
        """
        clock = pygame.time.Clock()

        while True:
            clock.tick(60)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return None
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        return None
                    if event.key == pygame.K_RETURN:
                        return self._collect()

                # Mouse click on launch button
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.launch_rect.collidepoint(event.pos):
                        return self._collect()

                # Delegate to sliders
                for slider in self.sliders.values():
                    slider.handle_event(event)

            self._draw(screen)

    def _collect(self) -> dict:
        return {key: s.value for key, s in self.sliders.items()}

    def _draw(self, screen: pygame.Surface):
        screen.fill(C_BG)

        # Title bar
        pygame.draw.rect(screen, (8, 12, 22), pygame.Rect(0, 0, SCREEN_W, 36))
        pygame.draw.line(screen, C_BORDER, (0, 36), (SCREEN_W, 36), 1)
        title = self.font_title.render(
            "SATURN ATMOSPHERE DESCENT SIMULATOR  //  MISSION PARAMETER CONFIGURATION",
            True, C_TITLE)
        screen.blit(title, (SCREEN_W // 2 - title.get_width() // 2, 10))

        # Column divider
        pygame.draw.line(screen, C_BORDER,
                         (SCREEN_W // 2, 44), (SCREEN_W // 2, SCREEN_H - 70), 1)

        # Layout items
        for item in self._layout:
            if item["type"] == "section":
                x, y = item["x"], item["y"]
                # Section header line style
                col_right = (SCREEN_W // 2 - 20) if item["col"] == 0 else (SCREEN_W - 20)
                pygame.draw.line(screen, C_BORDER, (x, y + 7), (col_right, y + 7), 1)
                lbl = self.font_section.render(f" {item['title']} ", True, C_SECTION)
                bg = pygame.Surface((lbl.get_width(), lbl.get_height()))
                bg.fill(C_BG)
                screen.blit(bg,  (x + 8, y - 1))
                screen.blit(lbl, (x + 8, y - 1))

            elif item["type"] == "param":
                pdef: ParamDef = item["pdef"]
                slider: Slider = item["slider"]
                x, y = item["x"], item["y"]

                # Label
                lbl = self.font_label.render(pdef.label.upper(), True, C_LABEL)
                screen.blit(lbl, (x, y))

                # Value (right-aligned in value area)
                val_str = slider.get_display_value()
                unit_x = x + self.LABEL_W + 10 + self.SLIDER_W + 10
                val_surf = self.font_value.render(val_str, True, C_VALUE)
                screen.blit(val_surf, (unit_x, y - 1))
                if pdef.unit:
                    unit_surf = self.font_desc.render(pdef.unit, True, C_UNIT)
                    screen.blit(unit_surf, (unit_x + val_surf.get_width() + 4, y + 2))

                # Slider
                slider.draw(screen, self.fonts)

                # Description + min/max below slider
                sx = x + self.LABEL_W + 10
                desc_surf = self.font_desc.render(pdef.desc, True, C_DESC)
                screen.blit(desc_surf, (x, y + 18 + self.SLIDER_H + 5))
                min_surf = self.font_desc.render(
                    f"{pdef.min_val:{pdef.fmt}}", True, (40, 52, 62))
                max_surf = self.font_desc.render(
                    f"{pdef.max_val:{pdef.fmt}}", True, (40, 52, 62))
                screen.blit(min_surf, (sx, y + 18 + self.SLIDER_H + 5))
                screen.blit(max_surf, (sx + self.SLIDER_W - max_surf.get_width(),
                                       y + 18 + self.SLIDER_H + 5))

        # Launch button — minimal scientific style
        mx, my = pygame.mouse.get_pos()
        btn_hov = self.launch_rect.collidepoint(mx, my)
        border_col = C_VALUE if btn_hov else C_SECTION
        pygame.draw.rect(screen, C_BTN_HOV if btn_hov else C_BTN_BG, self.launch_rect)
        pygame.draw.rect(screen, border_col, self.launch_rect, 1)
        btn_lbl = self.font_title.render("[ LAUNCH MISSION ]   Enter ↵", True, C_BTN_TXT)
        screen.blit(btn_lbl,
                    (self.launch_rect.centerx - btn_lbl.get_width() // 2,
                     self.launch_rect.centery - btn_lbl.get_height() // 2))

        # Warning: drogue > main
        d_val = self.sliders.get("auto_drogue_ms")
        m_val = self.sliders.get("auto_main_ms")
        if d_val and m_val and d_val.value <= m_val.value:
            warn = self.font_desc.render(
                "WARNING: Drogue deploy speed should be greater than main chute speed!", True, C_WARN)
            screen.blit(warn, (SCREEN_W // 2 - warn.get_width() // 2, SCREEN_H - 82))

        pygame.display.flip()
