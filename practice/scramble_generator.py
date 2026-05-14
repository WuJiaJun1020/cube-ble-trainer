"""
WCA-style scramble generator.

说明：
这里先实现“WCA 风格”的练习用打乱：
- 使用 U/D/R/L/F/B 六个面
- 后缀包含空、'、2
- 避免连续同一面
- 避免连续同一轴，降低明显冗余

严格 WCA 比赛使用随机状态打乱，不是简单 random-move。
后续如果要完全对齐官方，可以再接 TNoodle 或 random-state scrambler。
"""

import random


FACES = ["U", "D", "R", "L", "F", "B"]
SUFFIXES = ["", "'", "2"]

AXIS = {
    "U": "y",
    "D": "y",
    "R": "x",
    "L": "x",
    "F": "z",
    "B": "z",
}


def generate_scramble(length=20):
    rng = random.SystemRandom()
    result = []

    last_face = None
    last_axis = None

    while len(result) < length:
        face = rng.choice(FACES)
        axis = AXIS[face]

        if face == last_face:
            continue

        # 练习版：避免同轴连续，打乱序列更干净。
        if axis == last_axis:
            continue

        suffix = rng.choice(SUFFIXES)
        result.append(face + suffix)

        last_face = face
        last_axis = axis

    return result
