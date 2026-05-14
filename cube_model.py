class Cubie:
    def __init__(self, pos, stickers):
        self.pos = pos
        self.stickers = stickers


_COLOR_TO_NATIVE_VEC = {
    "white": (0, 1, 0),
    "yellow": (0, -1, 0),
    "green": (0, 0, 1),
    "blue": (0, 0, -1),
    "red": (1, 0, 0),
    "orange": (-1, 0, 0),
}

_OPPOSITE_COLOR = {
    "white": "yellow",
    "yellow": "white",
    "green": "blue",
    "blue": "green",
    "red": "orange",
    "orange": "red",
}


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _build_orientation_basis():
    color_order = ["white", "yellow", "green", "blue", "red", "orange"]
    preferred = ["white_top_green_front", "yellow_top_green_front", "yellow_top_blue_front"]
    basis = {}

    for top_color in color_order:
        top_vec = _COLOR_TO_NATIVE_VEC[top_color]
        for front_color in color_order:
            if front_color == top_color or _OPPOSITE_COLOR[front_color] == top_color:
                continue
            front_vec = _COLOR_TO_NATIVE_VEC[front_color]
            basis[f"{top_color}_top_{front_color}_front"] = {
                "x": _cross(top_vec, front_vec),
                "y": top_vec,
                "z": front_vec,
            }

    return {
        **{key: basis[key] for key in preferred if key in basis},
        **{key: value for key, value in basis.items() if key not in preferred},
    }


ORIENTATION_BASIS = _build_orientation_basis()


def native_to_oriented_vec(v, orientation_preset):
    """Map a native solved-cube vector into the selected top/front orientation.

    The returned coordinates use the app display axes: x=right, y=up, z=front.
    Example: yellow_top_green_front maps native yellow/down to display up,
    while green remains display front.
    """
    basis = ORIENTATION_BASIS.get(orientation_preset)
    if not basis:
        return v
    return (
        _dot(v, basis["x"]),
        _dot(v, basis["y"]),
        _dot(v, basis["z"]),
    )


def orient_cubies(cubies, orientation_preset):
    if not orientation_preset or orientation_preset == "white_top_green_front":
        return cubies

    for cubie in cubies:
        cubie.pos = native_to_oriented_vec(cubie.pos, orientation_preset)
        cubie.stickers = {
            native_to_oriented_vec(normal, orientation_preset): color_key
            for normal, color_key in cubie.stickers.items()
        }

    return cubies


# direction 只决定动画展示方向；180 度转动方向等价，但仍给一个默认方向用于箭头。
# x: 右, y: 上, z: 前
#
# layer 可以是：
# - int: 单层转动
# - tuple/list/set: 多层转动，例如小写 u/r/f 宽层转动
# - "all": 整体旋转，例如 x/y/z
MOVE_DEFS = {
    "U":  ("y",  1, -1), "U'": ("y",  1,  1), "U2": ("y",  1, -1),
    "D":  ("y", -1,  1), "D'": ("y", -1, -1), "D2": ("y", -1,  1),

    "F":  ("z",  1, -1), "F'": ("z",  1,  1), "F2": ("z",  1, -1),
    "B":  ("z", -1,  1), "B'": ("z", -1, -1), "B2": ("z", -1,  1),

    "R":  ("x",  1, -1), "R'": ("x",  1,  1), "R2": ("x",  1, -1),
    "L":  ("x", -1,  1), "L'": ("x", -1, -1), "L2": ("x", -1,  1),

    # 中间层
    "M":  ("x",  0,  1), "M'": ("x",  0, -1), "M2": ("x",  0,  1),
    "E":  ("y",  0,  1), "E'": ("y",  0, -1), "E2": ("y",  0,  1),
    "S":  ("z",  0, -1), "S'": ("z",  0,  1), "S2": ("z",  0, -1),

    # 小写宽层转动：两层一起转
    "u":  ("y", (0,  1), -1), "u'": ("y", (0,  1),  1), "u2": ("y", (0,  1), -1),
    "d":  ("y", (-1, 0),  1), "d'": ("y", (-1, 0), -1), "d2": ("y", (-1, 0),  1),
    "f":  ("z", (0,  1), -1), "f'": ("z", (0,  1),  1), "f2": ("z", (0,  1), -1),
    "b":  ("z", (-1, 0),  1), "b'": ("z", (-1, 0), -1), "b2": ("z", (-1, 0),  1),
    "r":  ("x", (0,  1), -1), "r'": ("x", (0,  1),  1), "r2": ("x", (0,  1), -1),
    "l":  ("x", (-1, 0),  1), "l'": ("x", (-1, 0), -1), "l2": ("x", (-1, 0),  1),

    # 整体旋转
    "x":  ("x", "all", -1), "x'": ("x", "all",  1), "x2": ("x", "all", -1),
    "y":  ("y", "all", -1), "y'": ("y", "all",  1), "y2": ("y", "all", -1),
    "z":  ("z", "all", -1), "z'": ("z", "all",  1), "z2": ("z", "all", -1),
}

AXIS_INDEX = {
    "x": 0,
    "y": 1,
    "z": 2,
}

AXIS_VECTOR = {
    "x": (1, 0, 0),
    "y": (0, 1, 0),
    "z": (0, 0, 1),
}


# Kociemba/GAN facelet order: U, R, F, D, L, B, 9 stickers per face.
# In the app's native solved orientation these correspond to:
# U=white, R=red, F=green, D=yellow, L=orange, B=blue.
FACE_TO_COLOR_KEY = {
    "U": "W",
    "R": "R",
    "F": "G",
    "D": "Y",
    "L": "O",
    "B": "B",
}

FACE_NORMALS = {
    "U": (0, 1, 0),
    "R": (1, 0, 0),
    "F": (0, 0, 1),
    "D": (0, -1, 0),
    "L": (-1, 0, 0),
    "B": (0, 0, -1),
}

FACE_POSITIONS = {
    "U": [
        (-1, 1, -1), (0, 1, -1), (1, 1, -1),
        (-1, 1,  0), (0, 1,  0), (1, 1,  0),
        (-1, 1,  1), (0, 1,  1), (1, 1,  1),
    ],
    "R": [
        (1,  1,  1), (1,  1,  0), (1,  1, -1),
        (1,  0,  1), (1,  0,  0), (1,  0, -1),
        (1, -1,  1), (1, -1,  0), (1, -1, -1),
    ],
    "F": [
        (-1,  1, 1), (0,  1, 1), (1,  1, 1),
        (-1,  0, 1), (0,  0, 1), (1,  0, 1),
        (-1, -1, 1), (0, -1, 1), (1, -1, 1),
    ],
    "D": [
        (-1, -1,  1), (0, -1,  1), (1, -1,  1),
        (-1, -1,  0), (0, -1,  0), (1, -1,  0),
        (-1, -1, -1), (0, -1, -1), (1, -1, -1),
    ],
    "L": [
        (-1,  1, -1), (-1,  1,  0), (-1,  1,  1),
        (-1,  0, -1), (-1,  0,  0), (-1,  0,  1),
        (-1, -1, -1), (-1, -1,  0), (-1, -1,  1),
    ],
    "B": [
        (1,  1, -1), (0,  1, -1), (-1,  1, -1),
        (1,  0, -1), (0,  0, -1), (-1,  0, -1),
        (1, -1, -1), (0, -1, -1), (-1, -1, -1),
    ],
}


def move_turns(label):
    return 2 if str(label).endswith("2") else 1


def create_solved_cube(orientation_preset=None):
    cubies = []

    for x in (-1, 0, 1):
        for y in (-1, 0, 1):
            for z in (-1, 0, 1):
                stickers = {}

                if y == 1:
                    stickers[(0, 1, 0)] = "W"
                if y == -1:
                    stickers[(0, -1, 0)] = "Y"

                if z == 1:
                    stickers[(0, 0, 1)] = "G"
                if z == -1:
                    stickers[(0, 0, -1)] = "B"

                if x == -1:
                    stickers[(-1, 0, 0)] = "O"
                if x == 1:
                    stickers[(1, 0, 0)] = "R"

                if stickers:
                    cubies.append(Cubie((x, y, z), stickers))

    return orient_cubies(cubies, orientation_preset)



def facelets_to_cubies(facelets, orientation_preset=None):
    """Build app cubies from a GAN/Kociemba facelet string.

    The input must use Kociemba face order U/R/F/D/L/B with 9 stickers per
    face. GAN reports these face letters in its fixed native color coordinate
    system, so the resulting cubies first use the native white-top/green-front
    orientation and are then optionally transformed into the requested app
    orientation.
    """
    facelets = str(facelets or "").strip().upper()

    if len(facelets) != 54:
        raise ValueError(f"expected 54 facelets, got {len(facelets)}")

    invalid = sorted(set(facelets) - set(FACE_TO_COLOR_KEY))
    if invalid:
        raise ValueError(f"invalid facelet symbols: {''.join(invalid)}")

    stickers_by_pos = {}
    index = 0

    for face in "URFDLB":
        normal = FACE_NORMALS[face]
        for pos in FACE_POSITIONS[face]:
            color_key = FACE_TO_COLOR_KEY[facelets[index]]
            stickers_by_pos.setdefault(pos, {})[normal] = color_key
            index += 1

    cubies = [Cubie(pos, stickers) for pos, stickers in stickers_by_pos.items() if stickers]
    return orient_cubies(cubies, orientation_preset)


def rotate_vec(v, axis, direction):
    x, y, z = v

    if axis == "x":
        return (x, -z, y) if direction == 1 else (x, z, -y)

    if axis == "y":
        return (z, y, -x) if direction == 1 else (-z, y, x)

    if axis == "z":
        return (-y, x, z) if direction == 1 else (y, -x, z)

    return v


def layer_values(layer):
    if layer == "all":
        return (-1, 0, 1)

    if isinstance(layer, (tuple, list, set)):
        return tuple(layer)

    return (layer,)


def layer_center(layer):
    values = layer_values(layer)
    if not values:
        return 0.0
    return sum(values) / len(values)


def is_in_layer(cubie, axis, layer):
    return cubie.pos[AXIS_INDEX[axis]] in layer_values(layer)


def _commit_quarter_turn(cubies, axis, layer, direction):
    for cubie in cubies:
        if is_in_layer(cubie, axis, layer):
            cubie.pos = rotate_vec(cubie.pos, axis, direction)

            new_stickers = {}

            for normal, color_key in cubie.stickers.items():
                new_stickers[rotate_vec(normal, axis, direction)] = color_key

            cubie.stickers = new_stickers


def commit_move(cubies, move):
    axis = move["axis"]
    layer = move["layer"]
    direction = move["direction"]
    turns = move.get("turns", move_turns(move.get("label", "")))

    for _ in range(turns):
        _commit_quarter_turn(cubies, axis, layer, direction)
