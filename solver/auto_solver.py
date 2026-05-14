"""
Kociemba-based automatic solver.

这个模块负责把当前 cubies 状态转换成 Kociemba facelets 字符串，
并调用可选依赖 kociemba 计算还原步骤。

安装依赖：
    pip install kociemba

注意：
- 如果没有安装 kociemba，程序仍然可运行，只是自动求解会提示安装依赖。
- 历史反向求解仍然保留。
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple


SOLVED_FACELETS = (
    "UUUUUUUUU"
    "RRRRRRRRR"
    "FFFFFFFFF"
    "DDDDDDDDD"
    "LLLLLLLLL"
    "BBBBBBBBB"
)


# 颜色到面的映射不能写死为 W=U、Y=D。
# 求解器支持不同初始朝向（例如“黄顶绿前”），因此要根据当前 6 个中心块
# 动态判断：当前 U 面中心是什么颜色，这个颜色就代表 U。
def color_to_face_from_centers(cubies):
    cubie_by_pos = {cubie.pos: cubie for cubie in cubies}
    mapping = {}

    for face, normal in FACE_NORMALS.items():
        center = cubie_by_pos.get(normal)
        if center is None:
            raise ValueError(f"missing center at {normal}")
        color_key = center.stickers.get(normal)
        if color_key is None:
            raise ValueError(f"missing center sticker at {normal}")
        mapping[color_key] = face

    if len(mapping) != 6:
        raise ValueError("invalid center color mapping")

    return mapping


FACE_NORMALS = {
    "U": (0, 1, 0),
    "R": (1, 0, 0),
    "F": (0, 0, 1),
    "D": (0, -1, 0),
    "L": (-1, 0, 0),
    "B": (0, 0, -1),
}


# Kociemba 常见 facelet 顺序：U, R, F, D, L, B，每个面 9 格。
# 坐标系与本项目一致：x 右，y 上，z 前。
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


@dataclass
class AutoSolveResult:
    ok: bool
    moves: List[str]
    message: str
    facelets: str = ""


def cubies_to_facelets(cubies) -> str:
    cubie_by_pos = {cubie.pos: cubie for cubie in cubies}
    color_to_face = color_to_face_from_centers(cubies)

    facelets = []

    for face in "URFDLB":
        normal = FACE_NORMALS[face]

        for pos in FACE_POSITIONS[face]:
            cubie = cubie_by_pos.get(pos)

            if cubie is None:
                raise ValueError(f"missing cubie at {pos}")

            color_key = cubie.stickers.get(normal)

            if color_key is None:
                raise ValueError(f"missing sticker at pos={pos}, normal={normal}")

            mapped_face = color_to_face.get(color_key)
            if mapped_face is None:
                raise ValueError(f"unknown sticker color: {color_key}")

            facelets.append(mapped_face)

    return "".join(facelets)


def is_solved(cubies) -> bool:
    try:
        return cubies_to_facelets(cubies) == SOLVED_FACELETS
    except Exception:
        return False


def parse_solution(solution_text: str) -> List[str]:
    if not solution_text:
        return []

    tokens = []

    for token in solution_text.replace("\n", " ").split():
        token = token.strip()

        if not token:
            continue

        # kociemba 通常只输出 U/R/F/D/L/B + 可选 ' 或 2。
        face = token[0].upper()
        suffix = token[1:]

        if face not in "URFDLB":
            continue

        if suffix not in ("", "'", "2"):
            continue

        tokens.append(face + suffix)

    return tokens


def solve_auto(cubies) -> AutoSolveResult:
    facelets = ""

    try:
        facelets = cubies_to_facelets(cubies)
    except Exception as e:
        return AutoSolveResult(
            ok=False,
            moves=[],
            message=f"状态转换失败：{e}",
            facelets="",
        )

    if facelets == SOLVED_FACELETS:
        return AutoSolveResult(
            ok=True,
            moves=[],
            message="当前已处于复原状态",
            facelets=facelets,
        )

    try:
        import kociemba
    except Exception:
        return AutoSolveResult(
            ok=False,
            moves=[],
            message="未安装自动求解依赖，请运行：pip install kociemba",
            facelets=facelets,
        )

    try:
        solution_text = kociemba.solve(facelets)
        moves = parse_solution(solution_text)

        return AutoSolveResult(
            ok=True,
            moves=moves,
            message=f"自动求解成功：{len(moves)} 步",
            facelets=facelets,
        )

    except Exception as e:
        return AutoSolveResult(
            ok=False,
            moves=[],
            message=f"自动求解失败：{e}",
            facelets=facelets,
        )
