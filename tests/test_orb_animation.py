"""OrbAnimator tests — pure math, no Tkinter/display needed."""
from __future__ import annotations

from ui.desktop.orb.animation import STATE_PARAMS, TRANSITION_SECONDS, OrbAnimator
from ui.desktop.orb.states import OrbState


def test_initial_state_is_idle():
    assert OrbAnimator().state is OrbState.IDLE


def test_set_state_changes_current_state():
    animator = OrbAnimator()
    animator.set_state(OrbState.LISTENING)
    assert animator.state is OrbState.LISTENING


def test_setting_the_same_state_does_not_restart_the_transition():
    animator = OrbAnimator()
    for _ in range(30):
        animator.tick(0.05)  # let IDLE fully settle (blend reaches 1.0)
    animator.set_state(OrbState.IDLE)
    assert animator._state_blend == 1.0


def test_tick_wobble_length_matches_vertex_count():
    animator = OrbAnimator(vertex_count=12)
    frame = animator.tick(0.016)
    assert len(frame.wobble) == 12


def test_amplitude_is_clamped_to_0_1():
    animator = OrbAnimator()
    animator.set_amplitude(5.0)
    assert animator._amplitude == 1.0
    animator.set_amplitude(-3.0)
    assert animator._amplitude == 0.0


def test_amplitude_increases_scale_while_listening():
    animator = OrbAnimator()
    animator.set_state(OrbState.LISTENING)
    for _ in range(30):
        animator.tick(0.05)  # settle the state transition

    animator.set_amplitude(0.0)
    for _ in range(15):
        quiet = animator.tick(0.02)

    animator.set_amplitude(1.0)
    for _ in range(15):
        loud = animator.tick(0.02)

    assert loud.scale > quiet.scale


def test_amplitude_has_negligible_effect_while_idle():
    """IDLE has amplitude_gain=0 — a loud mic shouldn't move an orb that's supposed to
    be resting (amplitude only matters during LISTENING/SPEAKING)."""
    animator = OrbAnimator()
    for _ in range(30):
        animator.tick(0.05)

    animator.set_amplitude(0.0)
    quiet = animator.tick(0.001).scale

    animator.set_amplitude(1.0)
    for _ in range(15):
        loud = animator.tick(0.02).scale

    assert abs(loud - quiet) < 0.05


def test_state_transition_eventually_reaches_target_colors():
    animator = OrbAnimator()
    animator.set_state(OrbState.ERROR)
    for _ in range(int(TRANSITION_SECONDS / 0.02) + 10):
        frame = animator.tick(0.02)

    target = STATE_PARAMS[OrbState.ERROR]
    assert frame.inner_color == target.inner_color
    assert frame.outer_color == target.outer_color


def test_transition_colors_move_gradually_not_instantly():
    animator = OrbAnimator()
    for _ in range(30):
        animator.tick(0.05)  # settle IDLE

    animator.set_state(OrbState.THINKING)
    animator.tick(0.001)

    # a single ~1ms tick against a 350ms transition should barely have started
    assert animator._state_blend < 0.02


def test_tick_clamps_a_large_dt_so_a_stall_cannot_jump_far():
    animator = OrbAnimator()
    # a huge dt (e.g. after a GC pause) must not explode the animation state
    frame = animator.tick(10.0)
    assert -2.0 < frame.scale < 4.0
