from config import MOVE_DURATION_FAST, MOVE_DURATION_NORMAL
from cube_model import MOVE_DEFS, move_turns


def ease_in_out_cubic(t):
    t = max(0.0, min(1.0, t))

    if t < 0.5:
        return 4.0 * t * t * t

    return 1.0 - pow(-2.0 * t + 2.0, 3) / 2.0


def make_move(label, duration=MOVE_DURATION_NORMAL):
    axis, layer, direction = MOVE_DEFS[label]
    turns = move_turns(label)

    return {
        "label": label,
        "axis": axis,
        "layer": layer,
        "direction": direction,
        "turns": turns,
        "target_angle": 90.0 * turns,
        "angle": 0.0,
        "progress": 0.0,
        "duration": duration * (1.35 if turns == 2 else 1.0),
    }


def choose_move_duration(queue_length):
    return MOVE_DURATION_FAST if queue_length >= 3 else MOVE_DURATION_NORMAL
