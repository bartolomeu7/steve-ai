"""Pure animation math for the orb — no Tkinter, no I/O, no rendering here, so it's
unit-testable without a display. `orb.py` calls `OrbAnimator.tick(dt)` once per frame
and draws whatever `OrbFrame` it returns; how the shape gets painted is a rendering
concern, not an animation one.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ui.desktop.orb.states import OrbState

TRANSITION_SECONDS = 0.5
DEFAULT_VERTEX_COUNT = 28


@dataclass(frozen=True)
class OrbVisualParams:
    base_scale: float          # resting size multiplier
    pulse_speed: float         # radians/second for the idle "breathing" pulse
    pulse_depth: float         # how much the idle pulse changes scale (0..1)
    amplitude_gain: float      # how strongly external amplitude pushes the scale
    inner_color: str
    outer_color: str
    noise_speed: float         # how fast the organic wobble morphs over time
    ring_speed: float = 0.0    # idle / thinking orbital ring (rad/s)
    ripple_gain: float = 0.0   # listening ripple strength
    orbit_speed: float = 0.0   # thinking scan orbit (rad/s)


# Cyber palette — stronger idle breath, clearer per-state motion. Colors still match
# ui/desktop/styles.py::DARK_CYBER. Motion stays EventBus-driven in the UI layer.
# ChatGPT-like fluid blob + Steve cyan glow. No hard plastic rings.
STATE_PARAMS: dict[OrbState, OrbVisualParams] = {
    OrbState.IDLE: OrbVisualParams(
        1.00, 0.95, 0.08, 0.00, "#B8F4FF", "#00E5FF", 0.55, ring_speed=0.0, ripple_gain=0.0, orbit_speed=0.0
    ),
    OrbState.LISTENING: OrbVisualParams(
        1.06, 1.4, 0.05, 0.9, "#E0FBFF", "#00F0FF", 0.85, ring_speed=0.0, ripple_gain=0.7, orbit_speed=0.0
    ),
    OrbState.THINKING: OrbVisualParams(
        1.02, 1.8, 0.07, 0.0, "#D4C4FF", "#7AE0FF", 1.1, ring_speed=0.0, ripple_gain=0.0, orbit_speed=1.2
    ),
    OrbState.TOOL_EXECUTION: OrbVisualParams(
        1.04, 1.6, 0.06, 0.2, "#FFE0B8", "#00E5FF", 0.9, ring_speed=0.0, ripple_gain=0.2, orbit_speed=0.8
    ),
    OrbState.SPEAKING: OrbVisualParams(
        1.08, 1.5, 0.05, 0.95, "#B8FFE8", "#00F5A0", 0.75, ring_speed=0.0, ripple_gain=0.55, orbit_speed=0.0
    ),
    OrbState.ERROR: OrbVisualParams(
        0.96, 2.2, 0.06, 0.0, "#FFC0C8", "#FF5A7A", 0.5, ring_speed=0.0, ripple_gain=0.0, orbit_speed=0.0
    ),
}


@dataclass
class OrbFrame:
    scale: float
    wobble: list[float]
    inner_color: str
    outer_color: str
    ring_phase: float = 0.0
    ripple: float = 0.0
    orbit_phase: float = 0.0
    state: OrbState = OrbState.IDLE


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 1 - (1 - t) ** 3


def _lerp_color(c1: str, c2: str, t: float) -> str:
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = round(_lerp(r1, r2, t))
    g = round(_lerp(g1, g2, t))
    b = round(_lerp(b1, b2, t))
    return f"#{r:02x}{g:02x}{b:02x}"


class OrbAnimator:
    """Advances the orb's organic motion by `dt` seconds each tick. Two independent
    inputs drive it: the discrete `state` (what Steve is doing) and a continuous
    `amplitude` in [0, 1] (how loud the relevant audio is right now — mic while
    LISTENING, TTS output while SPEAKING, ignored otherwise)."""

    def __init__(self, vertex_count: int = DEFAULT_VERTEX_COUNT):
        self.vertex_count = vertex_count
        self.state = OrbState.IDLE
        self._time = 0.0
        self._amplitude = 0.0
        self._amplitude_smoothed = 0.0
        self._state_blend = 1.0
        self._prev_params = STATE_PARAMS[OrbState.IDLE]

    def set_state(self, state: OrbState) -> None:
        if state is self.state:
            return
        self._prev_params = self._current_params()
        self.state = state
        self._state_blend = 0.0

    def set_amplitude(self, amplitude: float) -> None:
        self._amplitude = max(0.0, min(1.0, amplitude))

    def _current_params(self) -> OrbVisualParams:
        target = STATE_PARAMS[self.state]
        if self._state_blend >= 1.0:
            return target
        t = _ease_out_cubic(self._state_blend)
        return OrbVisualParams(
            base_scale=_lerp(self._prev_params.base_scale, target.base_scale, t),
            pulse_speed=_lerp(self._prev_params.pulse_speed, target.pulse_speed, t),
            pulse_depth=_lerp(self._prev_params.pulse_depth, target.pulse_depth, t),
            amplitude_gain=_lerp(self._prev_params.amplitude_gain, target.amplitude_gain, t),
            inner_color=_lerp_color(self._prev_params.inner_color, target.inner_color, t),
            outer_color=_lerp_color(self._prev_params.outer_color, target.outer_color, t),
            noise_speed=_lerp(self._prev_params.noise_speed, target.noise_speed, t),
            ring_speed=_lerp(self._prev_params.ring_speed, target.ring_speed, t),
            ripple_gain=_lerp(self._prev_params.ripple_gain, target.ripple_gain, t),
            orbit_speed=_lerp(self._prev_params.orbit_speed, target.orbit_speed, t),
        )

    def tick(self, dt: float) -> OrbFrame:
        dt = max(0.0, min(dt, 0.1))  # clamp so a stall (e.g. GC pause) can't jump far
        self._time += dt
        if self._state_blend < 1.0:
            self._state_blend = min(1.0, self._state_blend + dt / TRANSITION_SECONDS)

        # exponential smoothing so amplitude-driven motion doesn't jitter frame to frame
        self._amplitude_smoothed += (self._amplitude - self._amplitude_smoothed) * min(1.0, dt * 8.0)

        params = self._current_params()
        idle_pulse = math.sin(self._time * params.pulse_speed) * params.pulse_depth
        amp_boost = self._amplitude_smoothed * params.amplitude_gain
        scale = params.base_scale + idle_pulse + amp_boost

        wobble_amplitude = 0.02 + amp_boost * 0.07
        wobble = [
            math.sin(self._time * params.noise_speed + i * 0.9) * wobble_amplitude
            for i in range(self.vertex_count)
        ]

        ripple = (0.35 + self._amplitude_smoothed * 0.65) * params.ripple_gain
        return OrbFrame(
            scale=scale,
            wobble=wobble,
            inner_color=params.inner_color,
            outer_color=params.outer_color,
            ring_phase=self._time * params.ring_speed,
            ripple=ripple,
            orbit_phase=self._time * params.orbit_speed,
            state=self.state,
        )
