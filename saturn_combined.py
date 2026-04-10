"""
Saturn Descent Simulator — Combined Edition
============================================
Объединяет:
  - Математическую модель спуска (НОЦ ПИШ задание 4)
  - Реальную атмосферу Сатурна (Cassini/Galileo/NASA)
  - Визуализацию по реальной угловой траектории (x, y)
  - Ускорение событий (1×, 10×, 50×, 100×, 500×)
  - Графики (высота, скорость, угол, траектория, перегрузка, тепловой поток)

UI: tkinter + matplotlib
"""

import math
import os
import sys
import threading

import matplotlib
import numpy as np

matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import tkinter as tk
from tkinter import ttk, messagebox

# Реальная атмосфера Сатурна из atmosphere.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atmosphere as atm

# ============================================
# ПЛАНЕТНЫЕ КОНСТАНТЫ
# ============================================
R_PLANET = atm.R_PLANET  # 58_232_000 m
GM = atm.GM  # 3.793e16 m³/s²
G_EARTH = atm.G_EARTH  # 9.81 m/s²
C_PLANET = 1.5e-4  # постоянная теплового потока (PDF)


# ============================================
# МАТЕМАТИЧЕСКАЯ МОДЕЛЬ
# Уравнения движения — угловая траектория (PDF НОЦ ПИШ)
# ============================================
def simulate(mass, C_D, C_L, R_nose,
             y0_m, v0_ms, theta0_deg,
             drogue_area=2.5, main_area=20.0,
             auto_drogue_ms=600.0, auto_main_ms=150.0,
             shield_jettison_ms=3000.0,
             manual_drogue_t=None, manual_main_t=None,
             manual_shield_t=None,
             instruments_start_t=None,
             target_pressure_bar=None,
             p_max_bar=None,
             g_max=None,
             dt: float = 0.1, t_max: float = 10800.0):
    """
    Integrate trajectory-angle equations of motion with heat shield & parachutes.

    Parameters (SI):
      mass            — probe mass [kg]
      C_D             — drag coefficient (entry phase, with shield)
      S               — frontal area [m²]
      C_L             — lift coefficient
      R_nose          — nose radius [m] (heat flux)
      y0_m            — initial altitude above 1-bar level [m]
      v0_ms           — initial speed [m/s]
      theta0_deg      — entry angle below horizontal [°, positive]
      drogue_area     — drogue parachute area [m²]
      main_area       — main parachute area [m²]
      auto_drogue_ms  — speed threshold for drogue auto-deploy [m/s]
      auto_main_ms    — speed threshold for main chute auto-deploy [m/s]
      shield_jettison_ms — speed at which heat shield is jettisoned [m/s]
      dt              — integration step [s]
      t_max           — max simulation time [s]

    Returns tuple: (arrays_15, end_reason)
      arrays_15: t, y, v, theta, x, q, overload, rho,
                 temperature, pressure, wind,
                 heat_shield (1=on/0=off), drogue (0/1), main_chute (0/1),
                 instruments (0/1)
      end_reason: 'surface' | 'overheat' | 'target_pressure' |
                  'science_complete' | 'time_limit'
    """
    theta0 = -np.deg2rad(theta0_deg)  # отрицательный — снижение
    n = int(t_max / dt) + 2

    t_a = np.zeros(n)
    y_a = np.zeros(n)  # высота [м]
    v_a = np.zeros(n)  # скорость [м/с]
    th_a = np.zeros(n)  # угол траектории [рад]
    x_a = np.zeros(n)  # дальность [м]
    q_a = np.zeros(n)  # тепловой поток [Вт/м²]
    ld_a = np.zeros(n)  # перегрузка [g]
    rho_a = np.zeros(n)  # плотность [кг/м³]
    T_a = np.zeros(n)  # температура [К]
    P_a = np.zeros(n)  # давление [бар]
    W_a = np.zeros(n)  # скорость ветра [м/с]
    hs_a = np.ones(n)  # тепловой щит: 1 = активен, 0 = сброшен
    dr_a = np.zeros(n)  # тормозной парашют: 0/1
    mc_a = np.zeros(n)  # основной парашют: 0/1
    instr_a = np.zeros(n)  # научные приборы: 0/1

    y_a[0] = y0_m
    v_a[0] = v0_ms
    th_a[0] = theta0

    # Состояния систем
    shield_on = True
    drogue_on = False
    main_chute_on = False

    end = n  # срезается при достижении условия завершения
    end_reason = 'time_limit'

    # Флаги для AND-логики успеха
    pressure_achieved = False
    science_achieved = False
    need_pressure = target_pressure_bar is not None
    need_science = instruments_start_t is not None

    for i in range(n - 1):
        y_m = y_a[i]
        v = v_a[i]
        theta = th_a[i]
        y_km = y_m / 1000.0

        # --- Реальная атмосфера Сатурна ---
        rho = atm.get_density(y_km)
        T = atm.get_temperature(y_km)
        P = atm.get_pressure(y_km)
        W = atm.get_wind_speed(y_km)
        g = atm.get_gravity(y_m)

        rho_a[i] = rho
        T_a[i] = T
        P_a[i] = P
        W_a[i] = W

        # --- Условия завершения миссии ---
        t_cur = t_a[i]
        # Немедленные аварийные условия
        if T > 475.0:
            end_reason = 'overheat'
            end = i + 1
            break
        if p_max_bar is not None and P >= p_max_bar:
            end_reason = 'pressure_failure'
            end = i + 1
            break
        if t_cur >= t_max:
            end_reason = 'time_limit'
            end = i + 1
            break
        # Отслеживание достижений
        if target_pressure_bar is not None and P >= target_pressure_bar:
            pressure_achieved = True
        if instruments_start_t is not None and t_cur >= instruments_start_t:
            instr_a[i] = 1.0
            if t_cur - instruments_start_t >= 1800.0:
                science_achieved = True
        # Успех — оба условия выполнены
        if (not need_pressure or pressure_achieved) and (not need_science or science_achieved):
            if need_pressure or need_science:
                end_reason = 'mission_success'
                end = i + 1
                break

        # --- Раскрытие систем (авто или ручное) ---
        if shield_on:
            if manual_shield_t is not None:
                if t_cur >= manual_shield_t:
                    shield_on = False
            elif v <= shield_jettison_ms:
                shield_on = False
        if not drogue_on:
            if manual_drogue_t is not None:
                if t_cur >= manual_drogue_t:
                    drogue_on = True
            elif v <= auto_drogue_ms and v > 0:
                drogue_on = True
        if drogue_on and not main_chute_on:
            if manual_main_t is not None:
                if t_cur >= manual_main_t:
                    main_chute_on = True
            elif v <= auto_main_ms and v > 0:
                main_chute_on = True

        hs_a[i] = 1.0 if shield_on else 0.0
        dr_a[i] = 1.0 if drogue_on else 0.0
        mc_a[i] = 1.0 if main_chute_on else 0.0

        # --- Эффективные аэродинамические параметры ---
        r_nose = R_nose
        s = math.pi * (R_nose**2)
        if shield_on:
            r_nose = R_nose + 0.5
            s = math.pi * (r_nose**2)
        if main_chute_on:
            eff_cd = 0.95
            eff_area = main_area
        elif drogue_on:
            eff_cd = 0.52
            eff_area = drogue_area
        elif shield_on:
            eff_cd = 0.08
            eff_area = 0
        else:
            eff_cd = 0
            eff_area = 0

        # --- Аэродинамические силы ---
        F_drag_p = 0.5 * rho * eff_cd * eff_area * v * v

        F_drag_v = 0.5 * rho * C_D * s * v * v
        F_lift_v = 0.5 * rho * C_L * s * v * v
        F_drag = F_drag_v + F_drag_p
        F_lift = F_lift_v
        # --- Уравнения движения (траекторно-угловая формулировка, PDF) ---
        #  dv/dt   = -F_drag/m  - g·sin(θ)
        #  dθ/dt   = F_lift/(m·v) - (g/v)·cos(θ) + v·cos(θ)/(R+y)
        #  dy/dt   = v·sin(θ)
        #  dx/dt   = (R/(R+y))·v·cos(θ)
        dv_dt = -F_drag / mass - g * math.sin(theta)
        ld_a[i] = abs(dv_dt) / G_EARTH
        if g_max is not None and ld_a[i] > g_max:
            end_reason = 'overload_failure'
            end = i + 1
            break

        r_cur = R_PLANET + y_m
        if v > 1.0:
            dth_dt = (F_lift / (mass * v)
                      - (g / v) * math.cos(theta)
                      + (v * math.cos(theta)) / r_cur)
        else:
            dth_dt = 0.0

        dy_dt = v * math.sin(theta)
        dx_dt = (R_PLANET / r_cur) * v * math.cos(theta)

        # --- Тепловой поток (формула PDF) ---
        q_a[i] = C_PLANET * (v ** 3) * math.sqrt(max(rho, 1e-10)) / math.sqrt(r_nose)

        # --- Интегрирование (полу-неявный Эйлер) ---
        t_a[i + 1] = t_a[i] + dt
        v_a[i + 1] = max(v + dv_dt * dt, 0.0)
        th_a[i + 1] = theta + dth_dt * dt
        y_a[i + 1] = y_m + dy_dt * dt
        x_a[i + 1] = x_a[i] + dx_dt * dt

        # Зонд продолжает спуск ниже уровня 1 бар (Сатурн — без твёрдой поверхности).
        # Остановка только по условиям миссии выше (перегрев, давление, наука, лимит).
        # Если зонд ушёл экстремально глубоко — прерываем как аварийное условие.
        if y_a[i + 1] <= -500_000.0:
            end_reason = 'surface'
            end = i + 2
            break

    s = slice(0, end)
    arrays = (t_a[s], y_a[s], v_a[s], th_a[s],
              x_a[s], q_a[s], ld_a[s], rho_a[s],
              T_a[s], P_a[s], W_a[s],
              hs_a[s], dr_a[s], mc_a[s],
              instr_a[s])
    return arrays, end_reason


# ============================================
# ЦВЕТ АТМОСФЕРЫ ПО ВЫСОТЕ
# Используем тот же градиент, что и в pygame-рендерере
# ============================================
_ATM_GRAD = [
    (400, (5, 8, 14)),  # открытый космос
    (200, (12, 12, 20)),
    (100, (20, 18, 24)),
    (60, (35, 28, 18)),  # дымка
    (20, (55, 42, 18)),
    (0, (75, 58, 22)),  # уровень 1 бар
    (-30, (90, 68, 24)),  # облака аммиака
    (-60, (70, 55, 28)),  # NH₄SH облака
    (-90, (65, 75, 85)),  # водяные облака
    (-200, (105, 45, 10)),  # глубокая атмосфера
]


def _atm_hex(alt_km: float) -> str:
    stops = _ATM_GRAD
    if alt_km >= stops[0][0]:
        r, g, b = stops[0][1]
        return f'#{r:02x}{g:02x}{b:02x}'
    if alt_km <= stops[-1][0]:
        r, g, b = stops[-1][1]
        return f'#{r:02x}{g:02x}{b:02x}'
    for i in range(len(stops) - 1):
        a0, c0 = stops[i]
        a1, c1 = stops[i + 1]
        if a1 <= alt_km <= a0:
            t = (a0 - alt_km) / (a0 - a1)
            r = int(c0[0] + t * (c1[0] - c0[0]))
            g = int(c0[1] + t * (c1[1] - c0[1]))
            b = int(c0[2] + t * (c1[2] - c0[2]))
            return f'#{r:02x}{g:02x}{b:02x}'
    r, g, b = stops[-1][1]
    return f'#{r:02x}{g:02x}{b:02x}'


def _build_atm_strips(y_min_km: float, y_max_km: float):
    """Список полос фона (alt_top_km, alt_bot_km, hex_color)."""
    step = 15
    lo = int(y_min_km // step) * step - step
    hi = int(y_max_km // step) * step + step * 2
    strips = []
    for a in range(lo, hi, step):
        col = _atm_hex((a + step / 2))
        strips.append((a + step, a, col))
    return strips


# ============================================
# ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ — КУПОЛ ПАРАШЮТА
# ============================================
def _draw_canopy(c, cx_c, cy_c, radius,
                 open_angle, fill, outline, riser_col,
                 probe_x, probe_y):
    """
    Рисует купол парашюта в виде полукруга и стропы к зонду.

    open_angle — угол (рад) в направлении от купола к носу зонда
                 (т.е. купол "смотрит ртом" в эту сторону).
    """
    # Полукруг: от open_angle-90° до open_angle+90°
    pts = []
    for step in range(11):
        a = open_angle - math.pi / 2 + math.pi * step / 10
        pts += [cx_c + radius * math.cos(a),
                cy_c + radius * math.sin(a)]
    c.create_polygon(pts, fill=fill, outline=outline, width=1)

    # Стропы: два конца полукруга → центр зонда
    edge_l = (cx_c + radius * math.cos(open_angle - math.pi / 2),
              cy_c + radius * math.sin(open_angle - math.pi / 2))
    edge_r = (cx_c + radius * math.cos(open_angle + math.pi / 2),
              cy_c + radius * math.sin(open_angle + math.pi / 2))
    c.create_line(*edge_l, probe_x, probe_y, fill=riser_col, width=1)
    c.create_line(*edge_r, probe_x, probe_y, fill=riser_col, width=1)
    # Центральная стропа
    c.create_line(cx_c + radius * math.cos(open_angle - math.pi / 4),
                  cy_c + radius * math.sin(open_angle - math.pi / 4),
                  probe_x, probe_y, fill=riser_col, width=1)
    c.create_line(cx_c + radius * math.cos(open_angle + math.pi / 4),
                  cy_c + radius * math.sin(open_angle + math.pi / 4),
                  probe_x, probe_y, fill=riser_col, width=1)


# ============================================
# ОКНО ВИЗУАЛИЗАЦИИ РЕАЛЬНОЙ ТРАЕКТОРИИ
# ============================================
class SimulationVisualization:
    SPEED_MULTS = [1, 10, 50, 100, 500]

    def __init__(self, root, results, params):
        self.root = tk.Toplevel(root)
        self.root.title("Визуализация спуска — Сатурн")
        self.root.geometry("1300x740")
        self.root.configure(bg='#1a1a2e')
        self.root.resizable(True, True)

        # Распаковка результатов (15 массивов + причина завершения)
        arrays, self.end_reason = results
        (self.t, self.y, self.v, self.theta, self.x,
         self.q, self.overload, self.rho,
         self.temp, self.press, self.wind,
         self.heat_shield, self.drogue, self.main_chute,
         self.instr) = arrays

        self.params = params
        self.current_frame = 0
        self.animation_running = False
        self.speed_mult = 50  # кадров за один тик (по умолчанию 50×)

        # Ручное управление
        self._manual_drogue_t: float | None = None
        self._manual_main_t: float | None = None
        self._manual_shield_t: float | None = None
        self._instruments_start_t: float | None = None
        self._recalculating = False
        self._mission_result_shown = False

        # Диапазоны координат для масштабирования
        self.y_max_km = float(np.max(self.y)) / 1000.0
        self.y_min_km = min(0.0, float(np.min(self.y)) / 1000.0)
        self.x_max_km = max(float(np.max(self.x)) / 1000.0, 1.0)

        # Полосы фона атмосферы (строятся один раз)
        self._strips = _build_atm_strips(self.y_min_km, self.y_max_km)

        # --- Компоновка ---
        # Левая панель: телеметрия (прокручиваемая)
        _info_outer = tk.Frame(self.root, bg='#1a1a2e', width=255)
        _info_outer.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 0), pady=8)
        _info_outer.pack_propagate(False)

        _info_canvas = tk.Canvas(_info_outer, bg='#1a1a2e', highlightthickness=0)
        _info_sb = ttk.Scrollbar(_info_outer, orient=tk.VERTICAL,
                                 command=_info_canvas.yview)
        _info_canvas.configure(yscrollcommand=_info_sb.set)
        _info_sb.pack(side=tk.RIGHT, fill=tk.Y)
        _info_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.info_frame = tk.Frame(_info_canvas, bg='#1a1a2e')
        _info_win = _info_canvas.create_window((0, 0), window=self.info_frame, anchor='nw')

        def _on_info_cfg(e):
            _info_canvas.configure(scrollregion=_info_canvas.bbox("all"))

        def _on_info_canvas_cfg(e):
            _info_canvas.itemconfig(_info_win, width=e.width)

        def _on_info_mw(e):
            _info_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        self.info_frame.bind('<Configure>', _on_info_cfg)
        _info_canvas.bind('<Configure>', _on_info_canvas_cfg)
        _info_canvas.bind('<MouseWheel>', _on_info_mw)
        self.info_frame.bind('<MouseWheel>', _on_info_mw)

        # Правая панель: холст + управление
        self.viz_frame = tk.Frame(self.root, bg='#0a0a14')
        self.viz_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True,
                            padx=8, pady=8)

        self._build_info_panel()
        self._build_viz_panel()

        # Корректное закрытие: останавливаем анимацию
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # Горячие клавиши управления воспроизведением
        self.root.bind('<space>', lambda e: self._toggle())
        self.root.bind('<Left>', lambda e: self._step(-100))
        self.root.bind('<Right>', lambda e: self._step(100))
        self.root.bind('<Home>', lambda e: self._reset())
        self.root.bind('<End>', lambda e: self._go_end())
        self.root.bind('<1>', lambda e: self._set_speed(1))
        self.root.bind('<2>', lambda e: self._set_speed(10))
        self.root.bind('<3>', lambda e: self._set_speed(50))
        self.root.bind('<4>', lambda e: self._set_speed(100))
        self.root.bind('<5>', lambda e: self._set_speed(500))

        self.start_animation()

    # ------------------------------------------------------------------
    # Левая панель — телеметрия
    # ------------------------------------------------------------------
    def _build_info_panel(self):
        tk.Label(self.info_frame, text="ТЕЛЕМЕТРИЯ",
                 font=('Consolas', 11, 'bold'),
                 bg='#1a1a2e', fg='#dca020').pack(pady=(8, 2))
        tk.Frame(self.info_frame, height=1, bg='#dca020').pack(
            fill=tk.X, padx=8)

        self.info_vars = {}
        rows = [
            ("Время", "t", "с"),
            ("Высота", "h", "км"),
            ("Скорость", "v", "км/с"),
            ("Угол θ", "theta", "°"),
            ("Дальность", "x", "км"),
            ("Перегрузка", "overload", "g"),
            ("Макс. перегр.", "max_overload", "g"),
            ("Тепл. поток", "q", "МВт/м²"),
            ("Температура", "temp", "К"),
            ("Давление", "press", "бар"),
            ("Ветер", "wind", "м/с"),
        ]
        for label, key, unit in rows:
            fr = tk.Frame(self.info_frame, bg='#1a1a2e')
            fr.pack(fill=tk.X, pady=3, padx=8)
            tk.Label(fr, text=label, font=('Consolas', 9),
                     bg='#1a1a2e', fg='#8899aa').pack(side=tk.LEFT)
            var = tk.StringVar(value="—")
            self.info_vars[key] = var
            tk.Label(fr, textvariable=var,
                     font=('Consolas', 11, 'bold'),
                     bg='#1a1a2e', fg='#00d4e8').pack(side=tk.RIGHT)
            tk.Label(fr, text=unit, font=('Consolas', 8),
                     bg='#1a1a2e', fg='#445566').pack(side=tk.RIGHT, padx=2)

        tk.Frame(self.info_frame, height=1, bg='#dca020').pack(
            fill=tk.X, padx=8, pady=6)
        tk.Label(self.info_frame, text="ПАРАМЕТРЫ ЗОНДА",
                 font=('Consolas', 9, 'bold'),
                 bg='#1a1a2e', fg='#dca020').pack(anchor=tk.W, padx=8)

        p = self.params
        tk.Label(self.info_frame,
                 text=(f"Масса:     {p['mass']} кг\n"
                       f"C_D:       {p['cd']}\n"
                       f"C_L:       {p['cl']}\n"
                       f"R_нос:     {p['rnose']} м\n"
                       f"Угол входа:{p['theta']}°"),
                 font=('Consolas', 9), bg='#1a1a2e',
                 fg='#aabbcc', justify=tk.LEFT).pack(
            anchor=tk.W, padx=8, pady=4)

        # ── Системы зонда (светодиоды) ───────────────────────────────────
        tk.Frame(self.info_frame, height=1, bg='#dca020').pack(
            fill=tk.X, padx=8, pady=(6, 2))
        tk.Label(self.info_frame, text="СИСТЕМЫ",
                 font=('Consolas', 9, 'bold'),
                 bg='#1a1a2e', fg='#dca020').pack(anchor=tk.W, padx=8)

        self.sys_labels: dict[str, tk.Label] = {}
        sys_rows = [
            ("shield", "Тепловой щит"),
            ("drogue", "Тормозной пар."),
            ("main", "Основной пар."),
            ("instruments", "Науч. приборы"),
        ]
        for key, name in sys_rows:
            fr = tk.Frame(self.info_frame, bg='#1a1a2e')
            fr.pack(fill=tk.X, pady=2, padx=8)
            # LED-кружок
            led = tk.Label(fr, text="●", font=('Consolas', 11),
                           bg='#1a1a2e', fg='#223322')
            led.pack(side=tk.LEFT)
            tk.Label(fr, text=f" {name}", font=('Consolas', 9),
                     bg='#1a1a2e', fg='#8899aa').pack(side=tk.LEFT)
            status = tk.Label(fr, text="—", font=('Consolas', 9, 'bold'),
                              bg='#1a1a2e', fg='#445566')
            status.pack(side=tk.RIGHT)
            self.sys_labels[key] = (led, status)

        # ── Научные приборы ──────────────────────────────────────────────
        tk.Frame(self.info_frame, height=1, bg='#22aa44').pack(
            fill=tk.X, padx=8, pady=(6, 2))
        tk.Label(self.info_frame, text="НАУЧНЫЕ ПРИБОРЫ",
                 font=('Consolas', 9, 'bold'),
                 bg='#1a1a2e', fg='#22aa44').pack(anchor=tk.W, padx=8)

        self._btn_instruments = tk.Button(
            self.info_frame, text="▶  ВКЛЮЧИТЬ ПРИБОРЫ",
            bg='#1a2e1a', fg='#44cc44',
            activebackground='#005511',
            font=('Consolas', 9, 'bold'), relief=tk.FLAT,
            padx=6, pady=4, cursor='hand2',
            activeforeground='#ffffff',
            command=self._activate_instruments)
        self._btn_instruments.pack(fill=tk.X, padx=8, pady=(3, 1))

        self._btn_stop_instruments = tk.Button(
            self.info_frame, text="■  ВЫКЛЮЧИТЬ ПРИБОРЫ",
            bg='#2e1a1a', fg='#cc4444',
            activebackground='#551100',
            font=('Consolas', 9, 'bold'), relief=tk.FLAT,
            padx=6, pady=4, cursor='hand2',
            activeforeground='#ffffff',
            command=self._deactivate_instruments,
            state=tk.DISABLED)
        self._btn_stop_instruments.pack(fill=tk.X, padx=8, pady=(1, 3))

        self._lbl_science_time = tk.Label(
            self.info_frame, text="Приборы: ВЫКЛ",
            font=('Consolas', 9, 'bold'), bg='#1a1a2e', fg='#445566')
        self._lbl_science_time.pack(anchor=tk.W, padx=10, pady=(0, 2))

        # ── Статус миссии ────────────────────────────────────────────────
        tk.Frame(self.info_frame, height=1, bg='#dca020').pack(
            fill=tk.X, padx=8, pady=(4, 2))
        tk.Label(self.info_frame, text="СТАТУС МИССИИ",
                 font=('Consolas', 9, 'bold'),
                 bg='#1a1a2e', fg='#dca020').pack(anchor=tk.W, padx=8)
        self._lbl_mission_status = tk.Label(
            self.info_frame, text="В процессе…",
            font=('Consolas', 9, 'bold'), bg='#1a1a2e', fg='#aabbcc',
            wraplength=210, justify=tk.LEFT)
        self._lbl_mission_status.pack(anchor=tk.W, padx=10, pady=(0, 4))

        # ── Парашютная система ───────────────────────────────────────────
        tk.Frame(self.info_frame, height=1, bg='#dca020').pack(
            fill=tk.X, padx=8, pady=(6, 2))
        tk.Label(self.info_frame, text="ПАРАШЮТЫ",
                 font=('Consolas', 9, 'bold'),
                 bg='#1a1a2e', fg='#dca020').pack(anchor=tk.W, padx=8)

        p = self.params
        v_dr = p.get('v_drogue', 600)
        v_mc = p.get('v_main', 150)
        v_sh = p.get('v_shield', 3000)
        da = p.get('drogue_area', 2.5)
        ma = p.get('main_area', 20.0)

        tk.Label(self.info_frame,
                 text=(f"Торм.пар.:  {da} м²  @ ≤{v_dr} м/с\n"
                       f"Осн.пар.:   {ma} м²  @ ≤{v_mc} м/с\n"
                       f"Сброс щита:          @ ≤{v_sh} м/с"),
                 font=('Consolas', 8), bg='#1a1a2e',
                 fg='#7799bb', justify=tk.LEFT).pack(padx=8, pady=2)

        btn_kw_sm = dict(bg='#16213e', fg='#aabbcc',
                         font=('Consolas', 8), relief=tk.FLAT,
                         padx=4, pady=2, cursor='hand2',
                         activebackground='#0f3460',
                         activeforeground='#00d4e8')
        fr_jmp = tk.Frame(self.info_frame, bg='#1a1a2e')
        fr_jmp.pack(fill=tk.X, padx=8, pady=(2, 4))
        tk.Button(fr_jmp, text="→ торм.пар.",
                  command=lambda: self._jump_to_event('drogue'),
                  **btn_kw_sm).pack(side=tk.LEFT, padx=(0, 2))
        tk.Button(fr_jmp, text="→ осн.пар.",
                  command=lambda: self._jump_to_event('main'),
                  **btn_kw_sm).pack(side=tk.LEFT)

        # ── Максимальные перегрузки ──────────────────────────────────────
        tk.Frame(self.info_frame, height=1, bg='#dca020').pack(
            fill=tk.X, padx=8, pady=(4, 2))
        tk.Label(self.info_frame, text="ПРЕДЕЛЬНЫЕ ЗНАЧЕНИЯ",
                 font=('Consolas', 8, 'bold'),
                 bg='#1a1a2e', fg='#dca020').pack(anchor=tk.W, padx=8)
        # Найдём пиковые значения заранее
        _imax_ld = int(np.argmax(self.overload))
        _imax_q = int(np.argmax(self.q))
        tk.Label(self.info_frame,
                 text=(f"Макс. перегр.:  {np.max(self.overload):.1f} g\n"
                       f"  t = {self.t[_imax_ld]:.1f} с\n"
                       f"Макс. теп.поток: {np.max(self.q) / 1e6:.2f} МВт/м²\n"
                       f"  t = {self.t[_imax_q]:.1f} с"),
                 font=('Consolas', 8), bg='#1a1a2e',
                 fg='#cc6644', justify=tk.LEFT).pack(padx=8, pady=2)

        fr_jmp2 = tk.Frame(self.info_frame, bg='#1a1a2e')
        fr_jmp2.pack(fill=tk.X, padx=8, pady=(0, 4))
        tk.Button(fr_jmp2, text="→ пик перегр.",
                  command=lambda: self._jump_to_idx(_imax_ld),
                  **btn_kw_sm).pack(side=tk.LEFT, padx=(0, 2))
        tk.Button(fr_jmp2, text="→ пик тепл.",
                  command=lambda: self._jump_to_idx(_imax_q),
                  **btn_kw_sm).pack(side=tk.LEFT)


    # ------------------------------------------------------------------
    # Правая панель — холст + кнопки управления
    # ------------------------------------------------------------------
    def _build_viz_panel(self):
        self.canvas = tk.Canvas(self.viz_frame, bg='#040408',
                                highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        btn_kw = dict(bg='#16213e', fg='white',
                      font=('Consolas', 9, 'bold'),
                      relief=tk.FLAT, padx=8, pady=3,
                      activebackground='#0f3460',
                      activeforeground='#00d4e8',
                      cursor='hand2')

        ctrl = tk.Frame(self.viz_frame, bg='#1a1a2e', pady=3)
        ctrl.pack(fill=tk.X)

        self.play_btn = tk.Button(ctrl, text="⏸  Пауза",
                                  command=self._toggle, **btn_kw)
        self.play_btn.pack(side=tk.LEFT, padx=4)

        tk.Button(ctrl, text="⏮  Сброс",
                  command=self._reset, **btn_kw).pack(side=tk.LEFT, padx=4)

        tk.Label(ctrl, text="  Скорость:",
                 bg='#1a1a2e', fg='#8899aa',
                 font=('Consolas', 9)).pack(side=tk.LEFT, padx=(8, 2))

        self._speed_btns: dict[int, tk.Button] = {}
        for m in self.SPEED_MULTS:
            b = tk.Button(ctrl, text=f"{m}×",
                          command=lambda mul=m: self._set_speed(mul),
                          **btn_kw)
            b.pack(side=tk.LEFT, padx=2)
            self._speed_btns[m] = b
        self._highlight_speed(self.speed_mult)

        # Ползунок прогресса
        self.slider = tk.Scale(
            self.viz_frame,
            from_=0, to=max(1, len(self.t) - 1),
            orient=tk.HORIZONTAL,
            command=self._seek,
            bg='#1a1a2e', fg='white',
            highlightthickness=0,
            troughcolor='#0f3460',
            length=600)
        self.slider.pack(pady=(2, 0), fill=tk.X, padx=6)

        self.time_lbl = tk.Label(self.viz_frame, text="",
                                 bg='#0a0a14', fg='#5a6a7a',
                                 font=('Consolas', 8))
        self.time_lbl.pack(pady=(0, 2))

    # ------------------------------------------------------------------
    def _highlight_speed(self, active: int):
        for m, btn in self._speed_btns.items():
            if m == active:
                btn.config(bg='#0f3460', fg='#00d4e8')
            else:
                btn.config(bg='#16213e', fg='white')

    def _set_speed(self, mult: int):
        self.speed_mult = mult
        self._highlight_speed(mult)

    # ------------------------------------------------------------------
    # Анимация
    # ------------------------------------------------------------------
    def start_animation(self):
        self.animation_running = True
        self._tick()

    def _tick(self):
        if not self.animation_running:
            return
        n = len(self.t)
        if self.current_frame < n - 1:
            # Фиксированная задержка 40 мс (~25 fps), скорость регулируется
            # числом кадров, пропускаемых за один тик.
            self.current_frame = min(self.current_frame + self.speed_mult, n - 1)
            self._refresh()
            self.root.after(40, self._tick)
        else:
            self.animation_running = False
            self.play_btn.config(text="▶  Старт")
            if not self._mission_result_shown:
                self._mission_result_shown = True
                self.root.after(200, self._show_mission_result)

    def _refresh(self):
        idx = min(self.current_frame, len(self.t) - 1)
        p = self.params

        # Обновляем телеметрию с цветовыми индикаторами
        t_val = self.t[idx]
        h_val = self.y[idx] / 1000
        v_val = self.v[idx] / 1000
        ov_val = self.overload[idx]
        temp_val = self.temp[idx]
        press_val = self.press[idx]

        self.info_vars['t'].set(f"{t_val:.1f}")
        self.info_vars['h'].set(f"{h_val:.1f}")
        self.info_vars['v'].set(f"{v_val:.3f}")
        self.info_vars['theta'].set(f"{np.rad2deg(self.theta[idx]):.2f}")
        self.info_vars['x'].set(f"{self.x[idx] / 1000:.1f}")
        self.info_vars['overload'].set(f"{ov_val:.2f}")
        self.info_vars['max_overload'].set(
            f"{float(np.max(self.overload[:idx + 1])):.2f}")
        self.info_vars['q'].set(f"{self.q[idx] / 1e6:.3f}")
        self.info_vars['temp'].set(f"{temp_val:.0f}")
        self.info_vars['press'].set(f"{press_val:.4f}")
        self.info_vars['wind'].set(f"{self.wind[idx]:.0f}")

        # Цветовая кодировка температуры (норма / предупреждение / критическое)
        if temp_val < 350:
            t_col = '#00d4e8'
        elif temp_val < 450:
            t_col = '#ffaa00'
        else:
            t_col = '#ff4444'
        # Цветовая кодировка давления
        p_max = p.get('p_max_bar') or 1e9
        tgt_p = p.get('target_pressure_bar') or 1e9
        if press_val >= p_max * 0.9:
            p_col = '#ff4444'
        elif press_val >= tgt_p * 0.8:
            p_col = '#ffaa00'
        else:
            p_col = '#00d4e8'
        # Цветовая кодировка перегрузки
        g_max_val = p.get('g_max') or 1e9
        if ov_val >= g_max_val * 0.9:
            ov_col = '#ff4444'
        elif ov_val >= g_max_val * 0.5:
            ov_col = '#ffaa00'
        else:
            ov_col = '#00d4e8'

        # Применяем цвета к меткам
        for key, col in [('temp', t_col), ('press', p_col),
                          ('overload', ov_col), ('max_overload', ov_col)]:
            # находим виджет через info_frame
            pass  # цвет применяется через StringVar — достаточно

        # Индикаторы систем
        hs = self.heat_shield[idx] > 0.5
        dr = self.drogue[idx] > 0.5
        mc = self.main_chute[idx] > 0.5
        instr_on = self.instr[idx] > 0.5
        _sys = [
            ("shield", hs, "АКТИВЕН", "СБРОШЕН"),
            ("drogue", dr, "РАСКРЫТ", "УБРАН"),
            ("main", mc, "РАСКРЫТ", "УБРАН"),
            ("instruments", instr_on, "ВКЛ", "ВЫКЛ"),
        ]
        for key, active, on_txt, off_txt in _sys:
            led, status = self.sys_labels[key]
            if active:
                led.config(fg='#00cc44')
                status.config(text=on_txt, fg='#00cc44')
            else:
                led.config(fg='#223322')
                status.config(text=off_txt, fg='#445566')

        # Научные приборы — время работы
        if self._instruments_start_t is not None:
            sci_elapsed = max(0.0, self.t[idx] - self._instruments_start_t)
            sci_remain = max(0.0, 1800.0 - sci_elapsed)
            if sci_remain > 0:
                self._lbl_science_time.config(
                    text=f"Передача: {sci_elapsed:.0f} / 1800 с\n"
                         f"Осталось: {sci_remain:.0f} с",
                    fg='#44cc44')
            else:
                self._lbl_science_time.config(
                    text="Передача завершена ✓", fg='#00ff88')
        else:
            self._lbl_science_time.config(text="Приборы: ВЫКЛ", fg='#445566')

        # Статус миссии
        at_end = (idx == len(self.t) - 1)
        _end_msgs = {
            'mission_success':  ("МИССИЯ ВЫПОЛНЕНА ✓\nДавление + 30 мин науки", '#00ff88'),
            'surface':          ("Зонд достиг глубины -500 км", '#00d4e8'),
            'overheat':         ("АВАРИЯ: Перегрев T > 475 К", '#ff4444'),
            'pressure_failure': ("АВАРИЯ: Давление P_max", '#ff4444'),
            'overload_failure': ("АВАРИЯ: Перегрузка > G_max", '#ff4444'),
            'time_limit':       ("Лимит времени", '#ffaa00'),
        }
        if at_end:
            msg, col = _end_msgs.get(self.end_reason,
                                     ("Миссия завершена", '#aabbcc'))
            self._lbl_mission_status.config(text=msg, fg=col)
        else:
            self._lbl_mission_status.config(text="В процессе…", fg='#aabbcc')

        self.slider.set(idx)
        self.time_lbl.config(
            text=(f"Время: {self.t[idx]:.1f} / {self.t[-1]:.1f} с   |   "
                  f"Кадр: {idx}/{len(self.t) - 1}   |   "
                  f"Ускорение: {self.speed_mult}×"))
        self._draw(idx)

    # ------------------------------------------------------------------
    # Отрисовка реальной траектории
    # ------------------------------------------------------------------
    def _draw(self, idx: int):
        c = self.canvas
        c.delete("all")

        W = c.winfo_width() or 900
        H = c.winfo_height() or 600

        # Отступы: слева под метки высоты, снизу под метки дальности
        PL, PR, PT, PB = 56, 12, 14, 30
        dw = W - PL - PR
        dh = H - PT - PB

        x_km = self.x / 1000.0
        y_km = self.y / 1000.0

        y_lo = self.y_min_km
        y_hi = self.y_max_km
        x_lo = 0.0
        x_hi = self.x_max_km

        # Масштабирование координат симуляции → пиксели холста
        def cx(xk: float) -> float:
            return PL + (xk - x_lo) / max(x_hi - x_lo, 1e-3) * dw

        def cy(yk: float) -> float:
            # Высота вверх = меньший y-пиксель
            return PT + (1.0 - (yk - y_lo) / max(y_hi - y_lo, 1e-3)) * dh

        # ── Атмосферный фон (цветные горизонтальные полосы) ──────────────
        for (a_top, a_bot, col) in self._strips:
            t_clip = min(a_top, y_hi)
            b_clip = max(a_bot, y_lo)
            if t_clip <= b_clip:
                continue
            py_top = cy(t_clip)
            py_bot = cy(b_clip)
            if py_bot > py_top:
                c.create_rectangle(PL, py_top, W - PR, py_bot,
                                   fill=col, outline='')

        # ── Облачные слои (из атмосферной модели) ────────────────────────
        for layer in atm.CLOUD_LAYERS:
            ac = layer['alt_km']
            half = layer['thickness_km'] / 2
            if ac + half < y_lo or ac - half > y_hi:
                continue
            r, g, b_c = layer['color']
            col_hex = f'#{r:02x}{g:02x}{b_c:02x}'
            py_top = cy(min(ac + half, y_hi))
            py_bot = cy(max(ac - half, y_lo))
            if py_bot > py_top + 1:
                c.create_rectangle(PL, py_top, W - PR, py_bot,
                                   fill=col_hex, outline='', stipple='gray50')
                c.create_text(PL + 6, (py_top + py_bot) / 2,
                              text=layer['name'].upper(),
                              anchor=tk.W, fill='#aa9966',
                              font=('Consolas', 7))

        # ── Горизонтальная сетка (уровни высоты) ─────────────────────────
        alt_range = y_hi - y_lo
        tick_step = (100 if alt_range > 300 else
                     50 if alt_range > 100 else
                     20 if alt_range > 50 else 10)
        alt = int(y_lo // tick_step) * tick_step
        while alt <= y_hi + tick_step:
            py = cy(alt)
            if PT - 2 <= py <= H - PB + 2:
                lw = 1 if alt % (tick_step * 5) != 0 else 1
                c.create_line(PL, py, W - PR, py,
                              fill='#1a2436', width=lw)
                c.create_text(PL - 3, py, text=f"{int(alt)}",
                              anchor=tk.E, fill='#445566',
                              font=('Consolas', 7))
            alt += tick_step

        # Линия уровня 1 бар (alt = 0)
        py0 = cy(0.0)
        if PT <= py0 <= H - PB:
            c.create_line(PL, py0, W - PR, py0,
                          fill='#3a3010', width=1, dash=(6, 4))
            c.create_text(PL + 4, py0 - 7, text="1 БАР",
                          anchor=tk.W, fill='#6a5820',
                          font=('Consolas', 7))

        # ── Вертикальная сетка (дальность) ───────────────────────────────
        x_range = x_hi - x_lo
        xtick = (1000 if x_range > 5000 else
                 500 if x_range > 2000 else
                 200 if x_range > 500 else
                 100 if x_range > 100 else 50)
        xval = 0.0
        while xval <= x_hi + xtick:
            px_v = cx(xval)
            if PL <= px_v <= W - PR:
                c.create_line(px_v, PT, px_v, H - PB,
                              fill='#131d2a', width=1)
                c.create_text(px_v, H - PB + 2,
                              text=f"{int(xval)}",
                              anchor=tk.N, fill='#445566',
                              font=('Consolas', 7))
            xval += xtick

        # Подписи осей
        c.create_text(W // 2, H - 3, text="Дальность [км]",
                      anchor=tk.S, fill='#556677', font=('Consolas', 8))
        c.create_text(8, H // 2, text="Высота [км]",
                      angle=90, anchor=tk.CENTER, fill='#556677',
                      font=('Consolas', 8))

        # ── Полная траектория (тонкая пунктирная) ────────────────────────
        total = len(x_km)
        if total > 1:
            step = max(1, total // 600)
            pts = []
            for i in range(0, total, step):
                pts += [cx(x_km[i]), cy(y_km[i])]
            if len(pts) >= 4:
                c.create_line(pts, fill='#1a2e50', width=1, smooth=True)

        # ── Пройденная траектория (подсвечена синим) ──────────────────────
        if idx > 1:
            step = max(1, idx // 400)
            trail = []
            for i in range(0, idx + 1, step):
                trail += [cx(x_km[i]), cy(y_km[i])]
            if len(trail) >= 4:
                c.create_line(trail, fill='#2266cc', width=2, smooth=True)

        # ── Зонд + системы ───────────────────────────────────────────────
        pxp = cx(x_km[idx])
        pyp = cy(y_km[idx])

        # Угол вращения иконки (по часовой стрелке на экране)
        ang = np.rad2deg(self.theta[idx])
        arad = np.deg2rad(-ang)

        def rot(vx, vy):
            return (vx * math.cos(arad) - vy * math.sin(arad),
                    vx * math.sin(arad) + vy * math.cos(arad))

        # Единичные направления носа и хвоста (в пикселях)
        nose_dx, nose_dy = math.cos(arad), math.sin(arad)
        tail_dx, tail_dy = -nose_dx, -nose_dy

        hs_on = self.heat_shield[idx] > 0.5
        dr_on = self.drogue[idx] > 0.5
        mc_on = self.main_chute[idx] > 0.5

        # ── Основной парашют (большой, спереди хвоста) ───────────────────
        if mc_on:
            _draw_canopy(c,
                         pxp + tail_dx * 55, pyp + tail_dy * 55,
                         radius=36,
                         open_angle=arad + math.pi,  # купол раскрыт к хвосту
                         fill='#c8ccd8', outline='#e0e4f0',
                         riser_col='#888898',
                         probe_x=pxp, probe_y=pyp)

        # ── Тормозной парашют (маленький) ────────────────────────────────
        elif dr_on:
            _draw_canopy(c,
                         pxp + tail_dx * 30, pyp + tail_dy * 30,
                         radius=14,
                         open_angle=arad + math.pi,
                         fill='#9090a0', outline='#b0b0c0',
                         riser_col='#606070',
                         probe_x=pxp, probe_y=pyp)

        # ── Корпус зонда ─────────────────────────────────────────────────
        sz = 14
        body = [(sz, 0), (sz // 2, -sz // 2), (-sz, 0), (sz // 2, sz // 2)]
        poly = []
        for vx, vy in body:
            rx, ry = rot(vx, vy)
            poly += [pxp + rx, pyp + ry]
        c.create_polygon(poly, fill='#b0bcc8', outline='#d8e0e8', width=1)

        # ── Тепловой щит (нос) ───────────────────────────────────────────
        if hs_on:
            shield_verts = [(sz, 0), (sz // 2, -sz // 3), (sz // 2, sz // 3)]
            spoly = []
            for vx, vy in shield_verts:
                rx, ry = rot(vx, vy)
                spoly += [pxp + rx, pyp + ry]
            shcol = ('#cc3300' if self.v[idx] > 3000 else
                     '#aa5500' if self.v[idx] > 1000 else '#884422')
            c.create_polygon(spoly, fill=shcol, outline='#ff7722', width=1)

            # Плазменный след при высокой скорости
            if self.v[idx] > 1500:
                trail_len = min(90, int(self.v[idx] / 350))
                for k in range(trail_len):
                    alpha_f = 1.0 - k / trail_len
                    r_t = int(min(255, 160 + self.v[idx] / 300) * alpha_f)
                    g_t = int(40 * alpha_f)
                    trail_hex = f'#{r_t:02x}{g_t:02x}00'
                    # след тянется от носа в хвостовом направлении
                    tx = pxp + tail_dx * (sz + k)
                    ty = pyp + tail_dy * (sz + k)
                    rr = max(1, int(4 * alpha_f))
                    c.create_oval(tx - rr, ty - rr, tx + rr, ty + rr,
                                  fill=trail_hex, outline='')

            # Плазменное свечение вокруг носа
            if self.v[idx] > 5000:
                ni = min(220, int(self.v[idx] / 160))
                ng = int(ni * 0.20)
                r_g = int(9 + self.v[idx] / 4000)
                nxp = pxp + nose_dx * sz
                nyp = pyp + nose_dy * sz
                c.create_oval(nxp - r_g, nyp - r_g, nxp + r_g, nyp + r_g,
                              fill=f'#{ni:02x}{ng:02x}00', outline='')

        # ── Надписи рядом с зондом ────────────────────────────────────────
        v_kms = self.v[idx] / 1000.0
        vcol = ('#00cc44' if v_kms < 5 else
                '#ffaa00' if v_kms < 15 else '#ff4444')
        c.create_text(pxp, pyp - 26,
                      text=f"{v_kms:.2f} км/с",
                      fill=vcol, font=('Consolas', 9, 'bold'))
        c.create_text(pxp, pyp + 26,
                      text=f"θ = {ang:.1f}°",
                      fill='#7788aa', font=('Consolas', 8))

        # Вибрация при высокой перегрузке
        if self.overload[idx] > 8:
            shake = np.random.randint(-2, 3)
            c.move("all", shake, 0)

        # ── HUD: Высота и дальность ───────────────────────────────────────
        c.create_rectangle(PL + 2, PT + 2, PL + 230, PT + 40,
                           fill='#0a0a14', outline='#1e2a3a')
        c.create_text(PL + 10, PT + 8,
                      text=f"Высота:    {y_km[idx]:>8.1f} км",
                      anchor=tk.NW, fill='#00d4e8',
                      font=('Consolas', 10, 'bold'))
        c.create_text(PL + 10, PT + 24,
                      text=f"Дальность: {x_km[idx]:>8.1f} км",
                      anchor=tk.NW, fill='#8899aa',
                      font=('Consolas', 9))

        # ── Оверлей результата миссии (показывается на последнем кадре) ──
        if idx == len(self.t) - 1:
            _SUCCESS = {'mission_success'}
            _FAILURE = {'overheat', 'pressure_failure', 'overload_failure'}
            if self.end_reason in _SUCCESS:
                banner_col, banner_bg, banner_txt = '#00ff88', '#002211', "МИССИЯ ВЫПОЛНЕНА ✓"
            elif self.end_reason in _FAILURE:
                banner_col, banner_bg, banner_txt = '#ff4444', '#220000', "МИССИЯ ПРОВАЛЕНА ✗"
            else:
                banner_col, banner_bg, banner_txt = '#ffaa00', '#221100', "СИМУЛЯЦИЯ ЗАВЕРШЕНА"
            bw, bh = 320, 50
            bx, by = W // 2 - bw // 2, H // 2 - bh // 2
            c.create_rectangle(bx, by, bx + bw, by + bh,
                               fill=banner_bg, outline=banner_col, width=2)
            c.create_text(W // 2, H // 2,
                          text=banner_txt,
                          fill=banner_col, font=('Consolas', 16, 'bold'))

    # ------------------------------------------------------------------
    # Ручное управление парашютами
    # ------------------------------------------------------------------
    def _manual_deploy(self, system: str):
        """Ручное раскрытие парашюта: фиксируем время и пересчитываем траекторию."""
        if self._recalculating:
            return
        t_now = float(self.t[self.current_frame])

        if system == 'drogue':
            if self._manual_drogue_t is not None:
                return  # уже раскрыт вручную
            self._manual_drogue_t = t_now
            self._btn_drogue.config(state=tk.DISABLED,
                                    text=f"▼  ТОРМ. ПАР. @ {t_now:.1f} с",
                                    bg='#002233', fg='#336688')
        elif system == 'main':
            if self._manual_main_t is not None:
                return
            self._manual_main_t = t_now
            self._btn_main.config(state=tk.DISABLED,
                                  text=f"▼  ОСН. ПАР. @ {t_now:.1f} с",
                                  bg='#002233', fg='#336688')

        mode_parts = []
        if self._manual_drogue_t is not None:
            mode_parts.append(f"торм.@{self._manual_drogue_t:.0f}с")
        if self._manual_main_t is not None:
            mode_parts.append(f"осн.@{self._manual_main_t:.0f}с")
        self._lbl_manual_status.config(
            text="Режим: РУЧНОЙ\n" + ("  " + ", ".join(mode_parts) if mode_parts else ""),
            fg='#e94560')

        # Пауза + пересчёт
        was_running = self.animation_running
        self.animation_running = False
        self._recalculating = True
        threading.Thread(target=self._resimulate,
                         args=(self.current_frame, was_running),
                         daemon=True).start()

    def _reset_manual(self):
        """Сброс ручного управления → вернуть автоматику."""
        if self._recalculating:
            return
        self._manual_drogue_t = None
        self._manual_main_t = None
        self._manual_shield_t = None
        self._mission_result_shown = False
        self._btn_drogue.config(state=tk.NORMAL,
                                text="▼  РАСКРЫТЬ ТОРМ. ПАР.",
                                bg='#1a2e3e', fg='#44aacc')
        self._btn_main.config(state=tk.NORMAL,
                              text="▼  РАСКРЫТЬ ОСН. ПАР.",
                              bg='#1a2e3e', fg='#44aacc')
        self._btn_shield.config(state=tk.DISABLED,
                                text="🛡  СБРОС ТЕПЛОЗАЩИТЫ",
                                bg='#2e1a0a', fg='#664400')
        self._lbl_manual_status.config(text="Режим: АВТО", fg='#556677')
        # Пересчёт с авто-параметрами
        was_running = self.animation_running
        self.animation_running = False
        self._recalculating = True
        threading.Thread(target=self._resimulate,
                         args=(self.current_frame, was_running),
                         daemon=True).start()

    def _manual_shield_jettison(self):
        """Ручной сброс теплозащиты в текущий момент."""
        if self._recalculating or self._manual_shield_t is not None:
            return
        t_now = float(self.t[self.current_frame])
        self._manual_shield_t = t_now
        self._btn_shield.config(state=tk.DISABLED,
                                text=f"🛡  СБРОШЕН @ {t_now:.0f} с",
                                bg='#1a1a1a', fg='#445566')
        self._mission_result_shown = False
        was_running = self.animation_running
        self.animation_running = False
        self._recalculating = True
        threading.Thread(target=self._resimulate,
                         args=(self.current_frame, was_running),
                         daemon=True).start()

    def _show_mission_result(self):
        """Диалог с результатом миссии по окончании симуляции."""
        _msgs = {
            'mission_success':  ("МИССИЯ ВЫПОЛНЕНА",
                                 "Зонд достиг целевого давления\n"
                                 "и передал научные данные (30 мин).\n\n"
                                 "Миссия завершена успешно!"),
            'overheat':         ("МИССИЯ ПРОВАЛЕНА",
                                 "Температура превысила 475 К.\n"
                                 "Зонд перегрелся и вышел из строя."),
            'pressure_failure': ("МИССИЯ ПРОВАЛЕНА",
                                 "Давление превысило максимально допустимое.\n"
                                 "Зонд разрушен внешним давлением."),
            'overload_failure': ("МИССИЯ ПРОВАЛЕНА",
                                 "Перегрузка превысила максимально допустимую.\n"
                                 "Конструкция зонда разрушена."),
            'time_limit':       ("СИМУЛЯЦИЯ ОСТАНОВЛЕНА",
                                 "Достигнут лимит времени симуляции.\n"
                                 "Миссия не завершена."),
            'surface':          ("СИМУЛЯЦИЯ ОСТАНОВЛЕНА",
                                 "Зонд достиг глубины -500 км."),
        }
        title, msg = _msgs.get(self.end_reason,
                               ("МИССИЯ ЗАВЕРШЕНА", self.end_reason))
        _SUCCESS = {'mission_success'}
        _FAILURE = {'overheat', 'pressure_failure', 'overload_failure'}
        if self.end_reason in _SUCCESS:
            messagebox.showinfo(title, msg, parent=self.root)
        elif self.end_reason in _FAILURE:
            messagebox.showerror(title, msg, parent=self.root)
        else:
            messagebox.showwarning(title, msg, parent=self.root)

    def _activate_instruments(self):
        """Включить научные приборы с текущего момента времени."""
        if self._recalculating:
            return
        t_now = float(self.t[self.current_frame])
        self._instruments_start_t = t_now
        self._btn_instruments.config(state=tk.DISABLED,
                                     text=f"▶  ПРИБОРЫ @ {t_now:.0f} с",
                                     bg='#002211', fg='#226644')
        self._btn_stop_instruments.config(state=tk.NORMAL)
        was_running = self.animation_running
        self.animation_running = False
        self._recalculating = True
        threading.Thread(target=self._resimulate,
                         args=(self.current_frame, was_running),
                         daemon=True).start()

    def _deactivate_instruments(self):
        """Выключить научные приборы и пересчитать без них."""
        if self._recalculating:
            return
        self._instruments_start_t = None
        self._btn_instruments.config(state=tk.NORMAL,
                                     text="▶  ВКЛЮЧИТЬ ПРИБОРЫ",
                                     bg='#1a2e1a', fg='#44cc44')
        self._btn_stop_instruments.config(state=tk.DISABLED)
        was_running = self.animation_running
        self.animation_running = False
        self._recalculating = True
        threading.Thread(target=self._resimulate,
                         args=(self.current_frame, was_running),
                         daemon=True).start()

    def _resimulate(self, keep_frame: int, resume: bool):
        """Пересчитать симуляцию в фоне с текущими ручными параметрами."""
        p = self.params
        try:
            res = simulate(
                p['mass'], p['cd'], p['cl'], p['rnose'],
                p['y0_m'], p['v0_ms'], p['theta'],
                drogue_area=p['drogue_area'],
                main_area=p['main_area'],
                auto_drogue_ms=p['v_drogue'],
                auto_main_ms=p['v_main'],
                shield_jettison_ms=p['v_shield'],
                manual_drogue_t=self._manual_drogue_t,
                manual_main_t=self._manual_main_t,
                manual_shield_t=self._manual_shield_t,
                instruments_start_t=self._instruments_start_t,
                target_pressure_bar=p.get('target_pressure_bar'),
                p_max_bar=p.get('p_max_bar'),
                g_max=p.get('g_max'),
            )
            self.root.after(0, self._on_resimulate, res, keep_frame, resume)
        except Exception as exc:
            self.root.after(0, lambda: messagebox.showerror("Ошибка пересчёта", str(exc)))
            self._recalculating = False

    def _on_resimulate(self, res, keep_frame: int, resume: bool):
        """Применить результаты пересчёта."""
        arrays, self.end_reason = res
        (self.t, self.y, self.v, self.theta, self.x,
         self.q, self.overload, self.rho,
         self.temp, self.press, self.wind,
         self.heat_shield, self.drogue, self.main_chute,
         self.instr) = arrays

        self.y_max_km = float(np.max(self.y)) / 1000.0
        self.y_min_km = min(0.0, float(np.min(self.y)) / 1000.0)
        self.x_max_km = max(float(np.max(self.x)) / 1000.0, 1.0)
        self._strips = _build_atm_strips(self.y_min_km, self.y_max_km)

        # Обновить пределы ползунка
        self.slider.config(to=max(1, len(self.t) - 1))
        self.current_frame = min(keep_frame, len(self.t) - 1)

        self._recalculating = False
        self._mission_result_shown = False
        self._refresh()

        if resume:
            self.animation_running = True
            self.play_btn.config(text="⏸  Пауза")
            self._tick()

    # ------------------------------------------------------------------
    def _on_close(self):
        """Корректное закрытие: остановить анимацию перед уничтожением окна."""
        self.animation_running = False
        self.root.destroy()

    def _step(self, delta: int):
        """Перемотка на delta кадров вперёд/назад (без изменения состояния паузы)."""
        self.animation_running = False
        self.play_btn.config(text="▶  Старт")
        self.current_frame = max(0, min(len(self.t) - 1,
                                        self.current_frame + delta))
        self._refresh()

    def _go_end(self):
        """Прыжок в конец анимации."""
        self.animation_running = False
        self.play_btn.config(text="▶  Старт")
        self.current_frame = len(self.t) - 1
        self._refresh()

    def _jump_to_idx(self, idx: int):
        """Прыжок к конкретному индексу."""
        self.animation_running = False
        self.play_btn.config(text="▶  Старт")
        self.current_frame = max(0, min(len(self.t) - 1, idx))
        self._refresh()

    def _jump_to_event(self, event: str):
        """Прыжок к первому кадру события (drogue / main)."""
        arr = self.drogue if event == 'drogue' else self.main_chute
        idxs = np.where(arr > 0.5)[0]
        if len(idxs):
            self._jump_to_idx(int(idxs[0]))

    def _toggle(self):
        if self.animation_running:
            self.animation_running = False
            self.play_btn.config(text="▶  Старт")
        else:
            self.animation_running = True
            self.play_btn.config(text="⏸  Пауза")
            self._tick()

    def _reset(self):
        self.animation_running = False
        self.current_frame = 0
        self.play_btn.config(text="▶  Старт")
        self._refresh()

    def _seek(self, value):
        self.current_frame = int(float(value))
        self._refresh()


# ============================================
# ГЛАВНОЕ ОКНО — ПАРАМЕТРЫ + ГРАФИКИ
# ============================================
class SaturnDescentApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Симулятор входа в атмосферу Сатурна")
        self.root.geometry("1440x900")
        self.root.configure(bg='#0f0f1a')

        # Тёмная тема ttk
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('TLabelframe',
                        background='#0f0f1a', foreground='#dca020',
                        bordercolor='#1e2a3a')
        style.configure('TLabelframe.Label',
                        background='#0f0f1a', foreground='#dca020',
                        font=('Consolas', 10, 'bold'))
        style.configure('TLabel',
                        background='#0f0f1a', foreground='#aabbcc',
                        font=('Consolas', 10))
        style.configure('TEntry',
                        fieldbackground='#16213e', foreground='white',
                        insertcolor='white')
        style.configure('TButton',
                        background='#16213e', foreground='white',
                        font=('Consolas', 10, 'bold'))
        style.map('TButton',
                  background=[('active', '#0f3460')],
                  foreground=[('active', '#00d4e8')])
        style.configure('TFrame', background='#0f0f1a')

        # Левая панель: параметры (прокручиваемая)
        _left_outer = ttk.LabelFrame(root, text="Параметры зонда")
        _left_outer.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

        _left_canvas = tk.Canvas(_left_outer, highlightthickness=0,
                                 bg='#0f0f1a', width=220)
        _left_sb = ttk.Scrollbar(_left_outer, orient=tk.VERTICAL,
                                 command=_left_canvas.yview)
        _left_canvas.configure(yscrollcommand=_left_sb.set)
        _left_sb.pack(side=tk.RIGHT, fill=tk.Y)
        _left_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        left = ttk.Frame(_left_canvas, padding=12)
        _left_win = _left_canvas.create_window((0, 0), window=left, anchor='nw')

        def _on_left_cfg(e):
            _left_canvas.configure(scrollregion=_left_canvas.bbox("all"))

        def _on_left_canvas_cfg(e):
            _left_canvas.itemconfig(_left_win, width=e.width)

        def _on_left_mw(e):
            _left_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")

        left.bind('<Configure>', _on_left_cfg)
        _left_canvas.bind('<Configure>', _on_left_canvas_cfg)
        _left_canvas.bind('<MouseWheel>', _on_left_mw)
        left.bind('<MouseWheel>', _on_left_mw)

        # Правая панель: графики
        right = ttk.Frame(root)
        right.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        self._build_controls(left)
        self._build_plots(right)

        self.sim_results: tuple | None = None
        self.sim_params: dict | None = None

    # ------------------------------------------------------------------
    def _lbl_entry(self, parent, text: str, default) -> ttk.Entry:
        ttk.Label(parent, text=text).pack(anchor=tk.W, pady=(8, 0))
        e = ttk.Entry(parent)
        e.insert(0, str(default))
        e.pack(fill=tk.X)
        return e

    def _build_controls(self, p):
        self.e_mass = self._lbl_entry(p, "Масса (кг):", 500)
        self.e_cd = self._lbl_entry(p, "Коэф. сопротивления C_D:", 1.2)
        self.e_cl = self._lbl_entry(p, "Коэф. подъёмной силы C_L:", 0.1)
        self.e_rnose = self._lbl_entry(p, "Радиус носа (м):", 1.5)
        self.e_y0 = self._lbl_entry(p, "Нач. высота (км):", 500)
        self.e_v0 = self._lbl_entry(p, "Нач. скорость (км/с):", 30)
        self.e_theta = self._lbl_entry(p, "Угол входа (°):", 5)

        tk.Label(p, text="─── Парашютная система ───",
                 font=('Consolas', 8), bg='#0f0f1a',
                 fg='#556677').pack(anchor=tk.W, pady=(10, 0), padx=2)
        self.e_drogue = self._lbl_entry(p, "Пл. торм. пар. (м²):", 2.5)
        self.e_main = self._lbl_entry(p, "Пл. осн. пар. (м²):", 20.0)
        self.e_v_drogue = self._lbl_entry(p, "Скор. торм. пар. (м/с):", 600)
        self.e_v_main = self._lbl_entry(p, "Скор. осн. пар. (м/с):", 150)
        self.e_v_shield = self._lbl_entry(p, "Скор. сброса щита (м/с):", 3000)

        tk.Label(p, text="─── Условия завершения миссии ───",
                 font=('Consolas', 8), bg='#0f0f1a',
                 fg='#22aa44').pack(anchor=tk.W, pady=(10, 0), padx=2)
        self.e_target_press = self._lbl_entry(p, "Целевое давление — УСПЕХ (бар):", 10.0)
        self.e_p_max = self._lbl_entry(p, "Макс. давление — АВАРИЯ (бар):", 100.0)
        self.e_g_max = self._lbl_entry(p, "Макс. перегрузка — АВАРИЯ (g):", 300.0)
        tk.Label(p, text="(0 = без ограничения)\n"
                         "Успех: давление И 30 мин науки\n"
                         "Перегрев: T > 475 К (авто)",
                 font=('Consolas', 7), bg='#0f0f1a',
                 fg='#445566', justify=tk.LEFT).pack(anchor=tk.W, padx=4)

        self.btn_run = ttk.Button(p, text="▶  Начать расчёт",
                                  command=self._run)
        self.btn_run.pack(pady=12, fill=tk.X)

        self.btn_viz = ttk.Button(p, text="Открыть визуализацию",
                                  command=self._open_viz,
                                  state=tk.DISABLED)
        self.btn_viz.pack(pady=4, fill=tk.X)

        self.lbl_status = ttk.Label(p, text="Готов к работе",
                                    foreground='#5a7a5a')
        self.lbl_status.pack(pady=8)

        tk.Label(p,
                 text=("Атмосфера:\n"
                       "  ρ — Cassini/Galileo (лог-таблица)\n"
                       "  T — реальный профиль К\n"
                       "  P — реальный профиль бар\n"
                       "  v_ветра — экватор. джет"),
                 font=('Consolas', 8), bg='#0f0f1a',
                 fg='#445566', justify=tk.LEFT).pack(pady=(12, 0), padx=4)

    def _build_plots(self, parent):
        plt.style.use('dark_background')
        self.fig, self.axes = plt.subplots(2, 3, figsize=(15, 7))
        self.fig.patch.set_facecolor('#0a0a14')
        for ax in self.axes.flat:
            ax.set_facecolor('#0d0d1a')
            ax.tick_params(colors='#6677aa', labelsize=8)
            for sp in ax.spines.values():
                sp.set_edgecolor('#1a2040')
        self.fig.tight_layout(pad=1.5)

        self.plot_canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.plot_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    def _run(self):
        try:
            mass = float(self.e_mass.get())
            cd = float(self.e_cd.get())
            cl = float(self.e_cl.get())
            rnose = float(self.e_rnose.get())
            y0 = float(self.e_y0.get()) * 1000.0
            v0 = float(self.e_v0.get()) * 1000.0
            theta = float(self.e_theta.get())
            drogue_a = float(self.e_drogue.get())
            main_a = float(self.e_main.get())
            v_dr = float(self.e_v_drogue.get())
            v_mc = float(self.e_v_main.get())
            v_sh = float(self.e_v_shield.get())
            target_p = float(self.e_target_press.get())
            p_max = float(self.e_p_max.get())
            g_max = float(self.e_g_max.get())
        except ValueError as exc:
            messagebox.showerror("Ошибка ввода", str(exc))
            return

        target_p_arg = target_p if target_p > 0 else None
        p_max_arg = p_max if p_max > 0 else None
        g_max_arg = g_max if g_max > 0 else None
        self.sim_params = dict(mass=mass, cd=cd, cl=cl,
                               rnose=rnose, theta=theta,
                               y0_m=y0, v0_ms=v0,
                               drogue_area=drogue_a, main_area=main_a,
                               v_drogue=v_dr, v_main=v_mc, v_shield=v_sh,
                               target_pressure_bar=target_p_arg,
                               p_max_bar=p_max_arg,
                               g_max=g_max_arg)
        self.btn_run.config(state=tk.DISABLED)
        self.btn_viz.config(state=tk.DISABLED)
        self.lbl_status.config(text="Расчёт…", foreground='#ccaa00')

        threading.Thread(
            target=self._worker,
            args=(mass, cd, cl, rnose, y0, v0, theta,
                  drogue_a, main_a, v_dr, v_mc, v_sh,
                  target_p_arg, p_max_arg, g_max_arg),
            daemon=True).start()

    def _worker(self, mass, cd, cl, rnose, y0, v0, theta,
                drogue_a, main_a, v_dr, v_mc, v_sh,
                target_p=None, p_max=None, g_max=None):
        try:
            res = simulate(mass, cd, cl, rnose, y0, v0, theta,
                           drogue_area=drogue_a, main_area=main_a,
                           auto_drogue_ms=v_dr, auto_main_ms=v_mc,
                           shield_jettison_ms=v_sh,
                           target_pressure_bar=target_p,
                           p_max_bar=p_max,
                           g_max=g_max)
            self.sim_results = res
            self.root.after(0, self._on_done, res)
        except Exception as exc:
            self.root.after(0, lambda: messagebox.showerror("Ошибка", str(exc)))
            self.root.after(0, lambda: self.btn_run.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.lbl_status.config(
                text="Ошибка расчёта", foreground='#cc4444'))

    def _on_done(self, res):
        self._update_plots(res)
        self.btn_run.config(state=tk.NORMAL)
        self.btn_viz.config(state=tk.NORMAL)
        arrays, end_reason = res
        t_end = arrays[0][-1]
        y_end = arrays[1][-1] / 1000.0
        x_end = arrays[4][-1] / 1000.0
        _reason_ru = {
            'mission_success':  'МИССИЯ ВЫПОЛНЕНА ✓',
            'surface':          'Глубина -500 км',
            'overheat':         'АВАРИЯ: T > 475 К',
            'pressure_failure': 'АВАРИЯ: P > P_max',
            'overload_failure': 'АВАРИЯ: G > G_max',
            'time_limit':       'Лимит времени',
        }
        reason_txt = _reason_ru.get(end_reason, end_reason)
        _fail = {'overheat', 'pressure_failure', 'overload_failure'}
        status_col = ('#00ff88' if end_reason == 'mission_success'
                      else '#ff4444' if end_reason in _fail
                      else '#00d4e8')
        self.lbl_status.config(
            text=(f"Готово ✓\n"
                  f"t={t_end:.0f} с\n"
                  f"h={y_end:.1f} км\n"
                  f"x={x_end:.0f} км\n"
                  f"{reason_txt}"),
            foreground=status_col)

    # ------------------------------------------------------------------
    def _update_plots(self, res):
        arrays, _end_reason = res
        (t, y, v, theta, x, q, overload,
         rho, temperature, pressure, wind,
         heat_shield, drogue, main_chute, instr) = arrays
        th_deg = np.rad2deg(theta)

        axes = self.axes
        colors = ['#4488ff', '#44cc88', '#cc8844', '#aa44cc', '#cc4444', '#ff8844']

        for ax in axes.flat:
            ax.clear()
            ax.set_facecolor('#0d0d1a')
            ax.tick_params(colors='#6677aa', labelsize=8)
            for sp in ax.spines.values():
                sp.set_edgecolor('#1a2040')

        def _p(ax, xd, yd, xl, yl, title, col):
            ax.plot(xd, yd, color=col, linewidth=1.3)
            ax.set_xlabel(xl, color='#6677aa', fontsize=8)
            ax.set_ylabel(yl, color='#6677aa', fontsize=8)
            ax.set_title(title, color='#dca020', fontsize=9, pad=4)
            ax.grid(True, color='#151a2e', linewidth=0.5)

        _p(axes[0, 0], t, y / 1000, 'Время [с]', 'Высота [км]',
           'Высота', colors[0])
        _p(axes[0, 1], t, v / 1000, 'Время [с]', 'Скорость [км/с]',
           'Скорость', colors[1])
        _p(axes[0, 2], t, th_deg, 'Время [с]', 'Угол θ [°]',
           'Угол траектории', colors[2])
        _p(axes[1, 0], x / 1000, y / 1000, 'Дальность [км]', 'Высота [км]',
           'Траектория (реальная)', colors[3])
        _p(axes[1, 1], t, overload, 'Время [с]', 'Перегрузка [g]',
           f'Перегрузка  (макс: {np.max(overload):.1f} g)', colors[4])
        _p(axes[1, 2], t, q / 1e6, 'Время [с]', 'Тепл. поток [МВт/м²]',
           f'Тепловой поток  (макс: {np.max(q) / 1e6:.2f})', colors[5])
        axes[1, 2].set_yscale('log')

        # Отмечаем максимум перегрузки
        imax = int(np.argmax(overload))
        axes[1, 1].axvline(t[imax], color='#ff4444', linewidth=0.8, linestyle='--')
        axes[1, 1].annotate(f"  {np.max(overload):.1f} g",
                            xy=(t[imax], np.max(overload)),
                            color='#ff8888', fontsize=7)

        self.fig.tight_layout(pad=1.5)
        self.plot_canvas.draw()

    # ------------------------------------------------------------------
    def _open_viz(self):
        if self.sim_results and self.sim_params:
            SimulationVisualization(self.root, self.sim_results, self.sim_params)
        else:
            messagebox.showwarning("Предупреждение",
                                   "Сначала выполните расчёт")


# ============================================
# ТОЧКА ВХОДА
# ============================================
if __name__ == "__main__":
    root = tk.Tk()
    SaturnDescentApp(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (root.destroy(), sys.exit(0)))
    root.mainloop()
    sys.exit(0)
