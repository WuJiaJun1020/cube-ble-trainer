"""
History-based solver.

第一版求解器不做任意状态最短求解，而是基于“从复原状态开始记录的所有转动历史”
生成反向还原步骤。
"""

VALID_MOVES = {
    "U", "D", "F", "B", "L", "R", "M", "E", "S",
    "u", "d", "f", "b", "l", "r",
    "x", "y", "z",
}


def normalize_move(move: str) -> str:
    move = (move or "").replace("’", "'").strip()

    if len(move) == 0:
        raise ValueError("empty move")

    face = move[0]
    suffix = move[1:]

    # Accept U'2 / U2' as U2; u'2 / u2' as u2.
    if suffix in ("'2", "2'"):
        suffix = "2"

    if face.upper() in {"U", "D", "F", "B", "L", "R", "M", "E", "S"}:
        if face.islower() and face.lower() in {"u", "d", "f", "b", "l", "r"}:
            norm_face = face.lower()
        else:
            norm_face = face.upper()
    elif face.lower() in {"x", "y", "z"}:
        norm_face = face.lower()
    else:
        raise ValueError(f"invalid move face: {move}")

    if norm_face not in VALID_MOVES:
        raise ValueError(f"invalid move face: {move}")

    if suffix == "":
        return norm_face

    if suffix in ("'", "2"):
        return norm_face + suffix

    raise ValueError(f"invalid move suffix: {move}")


def invert_move(move: str) -> str:
    move = normalize_move(move)

    if move.endswith("2"):
        return move

    if move.endswith("'"):
        return move[:-1]

    return move + "'"


def invert_algorithm(moves):
    return [invert_move(move) for move in reversed(list(moves))]


def _amount(move):
    move = normalize_move(move)
    if move.endswith("'"):
        return 3
    if move.endswith("2"):
        return 2
    return 1


def move_from_amount(face, amount):
    amount %= 4

    if amount == 0:
        return None

    if amount == 1:
        return face

    if amount == 2:
        return face + "2"

    return face + "'"


def compress_algorithm(moves):
    result = []

    for raw in moves:
        move = normalize_move(raw)
        face = move[0]

        if result and result[-1][0] == face:
            prev = result.pop()
            merged = move_from_amount(face, _amount(prev) + _amount(move))
            if merged is not None:
                result.append(merged)
        else:
            result.append(move)

    return result


def solve_from_history(history):
    return compress_algorithm(invert_algorithm(history))
