from dataclasses import dataclass, field
from typing import List, Optional

from solver.history_solver import invert_move, move_from_amount, normalize_move


def move_dir(move: str) -> str:
    move = normalize_move(move)
    return "'" if move.endswith("'") else ""


def move_amount(move: str) -> int:
    move = normalize_move(move)

    if move.endswith("'"):
        return 3

    if move.endswith("2"):
        return 2

    return 1


@dataclass
class GuideResult:
    accepted: bool
    correct: bool
    finished: bool
    expected: Optional[str] = None
    actual: Optional[str] = None
    message: str = ""
    recompute: bool = False
    dynamic_fix: bool = False


@dataclass
class SolutionGuide:
    active: bool = False
    solution_moves: List[str] = field(default_factory=list)
    current_index: int = 0
    last_error: str = ""
    finished: bool = False
    pending_double_move: Optional[str] = None
    pending_double_dir: Optional[str] = None

    def start(self, moves):
        self.solution_moves = [normalize_move(move) for move in moves]
        self.current_index = 0
        self.last_error = ""
        self.finished = False
        self.pending_double_move = None
        self.pending_double_dir = None
        self.active = len(self.solution_moves) > 0

    def stop(self):
        self.active = False
        self.solution_moves = []
        self.current_index = 0
        self.last_error = ""
        self.finished = False
        self.pending_double_move = None
        self.pending_double_dir = None

    def current_move(self):
        if not self.active:
            return None

        if self.current_index >= len(self.solution_moves):
            return None

        return self.solution_moves[self.current_index]

    def progress_text(self):
        if not self.solution_moves:
            return "0/0"
        return f"{min(self.current_index + 1, len(self.solution_moves))}/{len(self.solution_moves)}"

    def display_partial_index(self) -> int:
        if self.active and self.pending_double_move and self.current_index < len(self.solution_moves):
            return self.current_index
        return -1

    def is_finished(self):
        return self.finished

    def _finish_current_step(self, expected, actual, message="正确"):
        self.current_index += 1
        self.last_error = ""
        self.pending_double_move = None
        self.pending_double_dir = None

        if self.current_index >= len(self.solution_moves):
            self.finished = True
            self.active = False
            return GuideResult(
                accepted=True,
                correct=True,
                finished=True,
                expected=expected,
                actual=actual,
                message="还原完成",
            )

        return GuideResult(
            accepted=True,
            correct=True,
            finished=False,
            expected=expected,
            actual=actual,
            message=message,
        )

    def handle_user_move(self, move):
        if not self.active:
            return GuideResult(
                accepted=True,
                correct=True,
                finished=False,
                actual=move,
                message="guide inactive",
            )

        actual = normalize_move(move)
        expected = self.current_move()

        if expected is None:
            self.finished = True
            self.active = False
            return GuideResult(
                accepted=True,
                correct=True,
                finished=True,
                expected=None,
                actual=actual,
                message="已经完成",
            )

        if actual == expected:
            return self._finish_current_step(expected, actual)

        # R2/U2 等双转步骤的跟随显示：允许用户按两次同方向 90° 完成。
        # 第一次同面单转后不推进步骤，只把当前 chip 标成“完成一半”；
        # 第二次同方向单转后才推进到下一步。
        if expected.endswith("2"):
            face = expected[0]
            if actual[0] == face and not actual.endswith("2"):
                actual_dir = move_dir(actual)

                if self.pending_double_move is None:
                    self.pending_double_move = expected
                    self.pending_double_dir = actual_dir
                    self.last_error = f"{expected} 已完成一半，请再转一次 {actual}"
                    return GuideResult(
                        accepted=True,
                        correct=True,
                        finished=False,
                        expected=expected,
                        actual=actual,
                        message=self.last_error,
                        dynamic_fix=True,
                    )

                if actual_dir == self.pending_double_dir:
                    return self._finish_current_step(expected, actual)

                # 第二下反向会抵消第一下，回到执行该 R2/U2 之前。
                self.pending_double_move = None
                self.pending_double_dir = None
                self.last_error = f"刚才两下方向相反已抵消，请重新执行 {expected}"
                return GuideResult(
                    accepted=True,
                    correct=False,
                    finished=False,
                    expected=expected,
                    actual=actual,
                    message=self.last_error,
                    dynamic_fix=True,
                )

            if self.pending_double_move is not None:
                # 已完成半步时又做了其他面：先补偿这个错误面，再补齐剩下的半步。
                pending_face = self.pending_double_move[0]
                remaining = pending_face + ("'" if self.pending_double_dir == "'" else "")
                correction = invert_move(actual)
                self.solution_moves[self.current_index] = remaining
                self.solution_moves.insert(self.current_index, correction)
                self.pending_double_move = None
                self.pending_double_dir = None
                self.last_error = f"{expected} 已完成一半，但你转了 {actual}。请先补偿 {correction}，再转 {remaining}"
                return GuideResult(
                    accepted=True,
                    correct=False,
                    finished=False,
                    expected=expected,
                    actual=actual,
                    message=self.last_error,
                    recompute=False,
                    dynamic_fix=True,
                )

        # 动态修正策略：
        # 如果用户转的是同一个面，但方向/次数错了，不要求重新 Solve，
        # 而是把当前步骤替换成“从当前错误状态走回原目标状态”所需的修正步骤。
        #
        # 例：当前应转 R，但用户转了 R'：
        # 原目标是 +1，实际是 -1，相差 +2，所以当前步骤动态改为 R2。
        if actual[0] == expected[0]:
            face = expected[0]
            correction_amount = (move_amount(expected) - move_amount(actual)) % 4
            correction = move_from_amount(face, correction_amount)

            if correction is None:
                return self._finish_current_step(expected, actual)

            self.solution_moves[self.current_index] = correction
            self.pending_double_move = None
            self.pending_double_dir = None
            self.last_error = f"已动态修正：请转 {correction} 补偿刚才的 {actual}"

            return GuideResult(
                accepted=True,
                correct=False,
                finished=False,
                expected=expected,
                actual=actual,
                message=self.last_error,
                dynamic_fix=True,
            )

        # 如果用户转了完全不同的面，把这一步当成“额外做错的一步”。
        # 先插入它的反向补偿步，再继续原来的还原步骤。
        # 例：当前应转 R，用户多转了 U，则下一步提示 U'，补偿完成后再回到 R。
        correction = invert_move(actual)
        self.solution_moves.insert(self.current_index, correction)
        self.pending_double_move = None
        self.pending_double_dir = None
        self.last_error = f"当前应转 {expected}，你转了 {actual}。已记录偏差，请先补偿 {correction}"

        return GuideResult(
            accepted=True,
            correct=False,
            finished=False,
            expected=expected,
            actual=actual,
            message=self.last_error,
            recompute=False,
            dynamic_fix=True,
        )
