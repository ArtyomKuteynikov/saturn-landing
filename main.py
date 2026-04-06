"""
Saturn Atmosphere Descent Simulator
====================================
Hackathon prototype — interactive simulation of a probe descending
into Saturn's hydrogen-helium atmosphere.

Flow:
  1. Config screen  — user sets all mission parameters
  2. Simulation     — real-time physics + visualization
  3. Mission end    — success or failure overlay, press R to restart

Keyboard shortcuts (during simulation):
  D     — deploy drogue parachute (manual)
  M     — deploy main parachute (manual)
  J     — jettison heat shield
  I     — toggle scientific instruments
  R     — restart (returns to config screen)
  Q / ESC — quit

Data sources:
  NASA Saturn Fact Sheet, Cassini/CIRS/RSS data,
  Guillot (1999), Folkner et al. (2006).
"""

import sys

import pygame

from config_screen import ConfigScreen
from physics import step, DT
from probe import ProbeState, ProbeConfig, Phase
from renderer import Renderer, SCREEN_W, SCREEN_H

# ---------------------------------------------------------------------------
# Simulation speed
# ---------------------------------------------------------------------------
TARGET_FPS = 60

# Physics steps per rendered frame:
#   High speed during hypersonic entry (200× fast-forward)
#   Slow near parachute phase to enjoy the view
STEPS_FAST = 200
STEPS_SLOW = 10
SLOW_THRESHOLD_MS = 800.0  # switch to slow mode below this vertical speed


# ---------------------------------------------------------------------------
# Action handler
# ---------------------------------------------------------------------------
def handle_action(action_id: str,
                  state: ProbeState,
                  config: ProbeConfig,
                  messages: list[str]) -> bool:
    """
    Process a control action. Returns True if simulation should restart
    (caller handles the restart loop).
    """
    if action_id == "restart":
        return True  # signal to caller

    err = None

    if action_id == "drogue":
        err = state.deploy_drogue(config)
        if err is None and state.drogue_deployed:
            messages.append(f"Тормозной пар. раскрыт при {state.velocity_ms:.0f} м/с")

    elif action_id == "main_chute":
        err = state.deploy_main_chute(config)
        if err is None:
            messages.append(f"Осн. парашют раскрыт при {state.velocity_ms:.0f} м/с")

    elif action_id == "jettison":
        err = state.jettison_heat_shield()
        if err is None:
            messages.append("Тепловой щит сброшен")

    elif action_id == "instruments":
        state.toggle_instruments()
        messages.append("Приборы " + ("ВКЛЮЧЕНЫ" if state.instruments_on else "ВЫКЛЮЧЕНЫ"))

    if err:
        messages.append(err)

    return False


# ---------------------------------------------------------------------------
# Simulation loop
# ---------------------------------------------------------------------------
def run_simulation(screen: pygame.Surface,
                   config: ProbeConfig) -> bool:
    """
    Run one simulation session.
    Returns True  → restart (go back to config screen)
    Returns False → quit
    """
    renderer = Renderer(screen)
    clock = pygame.time.Clock()
    state = ProbeState.from_config(config)
    messages: list[str] = [
        f"Вход: {config.entry_speed_ms / 1000:.1f} км/с  "
        f"угол {config.entry_angle_deg:.1f}°",
        f"Cd={config.cd_shield:.2f}  S={config.frontal_area_m2:.2f} м²  "
        f"CL={config.cl_vehicle:.2f}",
    ]
    physics_acc = 0.0

    while True:
        real_dt = clock.tick(TARGET_FPS) / 1000.0

        # --- Events ---
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False

            elif event.type == pygame.KEYDOWN:
                key_map = {
                    pygame.K_d: "drogue",
                    pygame.K_m: "main_chute",
                    pygame.K_j: "jettison",
                    pygame.K_i: "instruments",
                    pygame.K_r: "restart",
                    pygame.K_q: "quit",
                    pygame.K_ESCAPE: "quit",
                }
                action = key_map.get(event.key)
                if action == "quit":
                    return False
                elif action:
                    if handle_action(action, state, config, messages):
                        return True  # restart

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                btn_id = renderer.get_button_at(event.pos)
                if btn_id:
                    if handle_action(btn_id, state, config, messages):
                        return True

        # --- Physics ---
        if state.is_alive():
            spf = STEPS_SLOW if state.velocity_ms < SLOW_THRESHOLD_MS else STEPS_FAST
            physics_acc += real_dt
            steps_this_frame = min(spf, max(1, int(physics_acc / DT)))
            for _ in range(steps_this_frame):
                if not state.is_alive():
                    break
                new_events = step(state, config)
                messages.extend(new_events)

            physics_acc -= steps_this_frame * DT
            physics_acc = max(0.0, physics_acc)

            # One-shot terminal messages
            if state.phase == Phase.SCIENCE and state.science_data_s < DT * 2:
                messages.append(
                    f"Науч. фаза! Достигнуто {config.target_pressure_bar:.0f} бар")
            if state.phase == Phase.FAILED:
                reason = state.failure_reason.value if state.failure_reason else "Неизвестно"
                if not any(reason[:15] in m for m in messages[-3:]):
                    messages.append(f"ОТКАЗ: {reason}")
            if state.phase == Phase.SUCCESS:
                if not any("УСПЕХ" in m for m in messages[-3:]):
                    messages.append("УСПЕХ: данные переданы на Землю!")

        # --- Render ---
        renderer.draw(state, config, messages)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("Saturn Atmosphere Descent Simulator")

    while True:
        # --- Config screen ---
        cfg_screen = ConfigScreen()
        params = cfg_screen.run(screen)
        if params is None:
            break  # user quit on config screen

        config = ProbeConfig.from_dict(params)

        # --- Simulation ---
        restart = run_simulation(screen, config)
        if not restart:
            break  # user quit

    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
