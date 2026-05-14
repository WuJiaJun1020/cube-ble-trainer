import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from practice.scramble_generator import generate_scramble
from practice.timer_history import append_record, load_history
from solver.auto_solver import is_solved
from solver.history_solver import compress_algorithm, invert_algorithm, normalize_move


STATE_IDLE = "idle"
STATE_SCRAMBLING = "scrambling"
STATE_OBSERVING = "observing"
STATE_TIMING = "timing"
STATE_FINISHED = "finished"
STATE_ABANDONED = "abandoned"
STATE_INVALID = "invalid"

CFOP_SEGMENTS = ["C", "F", "F1", "F2", "F3", "F4", "O", "P"]


def format_time_ms(ms: int) -> str:
    ms = max(0, int(ms))
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes:02d}:{seconds:02d}:{millis:03d}"


def format_seconds_time(seconds: float) -> str:
    return format_time_ms(int(seconds * 1000))


def move_face(move: str) -> str:
    return normalize_move(move)[0]


def move_dir(move: str) -> str:
    move = normalize_move(move)
    return "'" if move.endswith("'") else ""


@dataclass
class TimerEvent:
    message: str = ""
    state_changed: bool = False
    started_timer: bool = False
    finished: bool = False
    abandoned: bool = False
    scramble_complete: bool = False


class TimerPractice:
    def __init__(self):
        self.state = STATE_IDLE

        self.target_scramble_moves: List[str] = []
        self.actual_scramble_moves: List[str] = []
        self.scramble_moves: List[str] = []
        self.scramble_index = 0

        # For current U2/R2... step partial display.
        self.pending_double_move: Optional[str] = None
        self.pending_double_dir: Optional[str] = None

        self.observation_unlimited = False
        self.observation_total = 15.0
        self.observation_remaining = 15.0

        self.elapsed = 0.0
        self.final_time_ms: Optional[int] = None

        self.solve_moves = 0
        self.solve_move_log: List[dict] = []
        self.cfop_stats: List[dict] = self.empty_cfop_stats()

        self.status_message = "点击“新打乱”开始计时练习"

        self.history = load_history()

        self._saved_current_result = False

    def empty_cfop_stats(self):
        return [
            {
                "name": name,
                "time_ms": 0,
                "time_text": "00:00:000",
                "moves": 0,
                "tps": 0.0,
            }
            for name in CFOP_SEGMENTS
        ]

    def reset_session(self):
        self.state = STATE_IDLE
        self.target_scramble_moves = []
        self.actual_scramble_moves = []
        self.scramble_moves = []
        self.scramble_index = 0
        self.pending_double_move = None
        self.pending_double_dir = None
        self.observation_remaining = self.observation_total
        self.elapsed = 0.0
        self.final_time_ms = None
        self.solve_moves = 0
        self.solve_move_log = []
        self.cfop_stats = self.empty_cfop_stats()
        self.status_message = "点击“新打乱”开始计时练习"
        self._saved_current_result = False

    def start_new_scramble(self, length=20):
        self.state = STATE_SCRAMBLING
        self.target_scramble_moves = generate_scramble(length)
        self.actual_scramble_moves = []
        self.scramble_moves = list(self.target_scramble_moves)
        self.scramble_index = 0
        self.pending_double_move = None
        self.pending_double_dir = None
        self.observation_remaining = self.observation_total
        self.elapsed = 0.0
        self.final_time_ms = None
        self.solve_moves = 0
        self.solve_move_log = []
        self.cfop_stats = self.empty_cfop_stats()
        self.status_message = "请按照下方打乱步骤完成打乱"
        self._saved_current_result = False

    def toggle_observation_mode(self):
        self.observation_unlimited = not self.observation_unlimited

        if self.observation_unlimited:
            self.status_message = "观察模式：不限时，转动魔方后开始计时"
        else:
            self.observation_remaining = self.observation_total
            self.status_message = "观察模式：15 秒，倒计时结束自动开始计时"

    def expected_scramble_move(self):
        if self.state != STATE_SCRAMBLING:
            return None

        if not self.scramble_moves:
            return None

        return self.scramble_moves[0]

    def _recompute_remaining_scramble(self):
        self.scramble_moves = compress_algorithm(
            invert_algorithm(self.actual_scramble_moves) + self.target_scramble_moves
        )
        self.scramble_index = 0

    def _handle_scramble_move(self, move):
        try:
            actual = normalize_move(move)
        except Exception:
            self.status_message = f"无法识别打乱转动：{move}"
            return TimerEvent(message=self.status_message)

        expected_before = self.expected_scramble_move()
        had_pending = self.pending_double_move is not None
        pending_dir = self.pending_double_dir
        pending_move = self.pending_double_move

        # Treat direct U2 and two U / two U' as valid for double turns.
        self.actual_scramble_moves.append(actual)
        self._recompute_remaining_scramble()

        # Partial U2 handling for display. This does not change the actual dynamic move sequence,
        # only tells the UI to render a half-green / half-blue current chip.
        self.pending_double_move = None
        self.pending_double_dir = None

        if expected_before and expected_before.endswith("2"):
            same_face = move_face(actual) == expected_before[0]
            if same_face and not actual.endswith("2"):
                actual_dir = move_dir(actual)

                if not had_pending:
                    # First half of U2/R2...
                    if self.scramble_moves:
                        self.pending_double_move = expected_before
                        self.pending_double_dir = actual_dir
                else:
                    # Second half. Same direction completes U2; opposite direction cancels
                    # and the recomputed sequence will ask for U2 again.
                    if actual_dir != pending_dir and self.scramble_moves:
                        self.pending_double_move = pending_move
                        self.pending_double_dir = None

        if not self.scramble_moves:
            self.pending_double_move = None
            self.pending_double_dir = None
            self.start_observation()
            return TimerEvent(
                message=self.status_message,
                state_changed=True,
                scramble_complete=True,
            )

        next_move = self.display_scramble_moves()[0]

        if expected_before == actual:
            self.status_message = f"打乱中：剩余 {len(self.scramble_moves)} 步"
        else:
            self.status_message = (
                f"已动态修正打乱：刚才转了 {actual}，现在请继续 {next_move}"
            )

        return TimerEvent(message=self.status_message)

    def display_scramble_moves(self) -> List[str]:
        if self.pending_double_move and self.scramble_moves:
            first = self.scramble_moves[0]
            if first[0] == self.pending_double_move[0] and not first.endswith("2"):
                return [self.pending_double_move] + self.scramble_moves[1:]
        return list(self.scramble_moves)

    def display_partial_index(self) -> int:
        return 0 if self.pending_double_move else -1

    def start_observation(self):
        self.state = STATE_OBSERVING
        self.observation_remaining = self.observation_total
        self.elapsed = 0.0
        self.solve_moves = 0
        self.solve_move_log = []
        self.cfop_stats = self.empty_cfop_stats()

        if self.observation_unlimited:
            self.status_message = "打乱完成：不限观察，第一次转动开始计时"
        else:
            self.status_message = "打乱完成：15 秒观察倒计时开始"

    def start_timing(self, message="开始计时"):
        self.state = STATE_TIMING
        self.elapsed = 0.0
        self.final_time_ms = None
        self.solve_moves = 0
        self.solve_move_log = []
        self.cfop_stats = self.empty_cfop_stats()
        self.status_message = message
        return TimerEvent(message=self.status_message, state_changed=True, started_timer=True)

    def _log_solve_move(self, move):
        self.solve_moves += 1
        self.solve_move_log.append(
            {
                "move": normalize_move(move),
                "time_ms": int(self.elapsed * 1000),
            }
        )

    def handle_user_move(self, move):
        if self.state == STATE_SCRAMBLING:
            return self._handle_scramble_move(move)

        if self.state == STATE_OBSERVING:
            event = self.start_timing("已开始计时")
            self._log_solve_move(move)
            return event

        if self.state == STATE_TIMING:
            self._log_solve_move(move)
            self.status_message = "计时中"
            return TimerEvent(message=self.status_message)

        return TimerEvent(message=self.status_message)

    def update(self, dt):
        if self.state == STATE_OBSERVING and not self.observation_unlimited:
            self.observation_remaining -= dt

            if self.observation_remaining <= 0:
                self.observation_remaining = 0
                return self.start_timing("观察结束，自动开始计时")

        if self.state == STATE_TIMING:
            self.elapsed += dt

        return TimerEvent(message=self.status_message)

    def check_solved(self, cubies):
        if self.state != STATE_TIMING:
            return TimerEvent(message=self.status_message)

        if self.solve_moves <= 0:
            return TimerEvent(message=self.status_message)

        if is_solved(cubies):
            return self.finish_ok()

        return TimerEvent(message=self.status_message)

    def compute_cfop_stats(self, total_ms: int):
        log = self.solve_move_log
        total_moves = len(log)

        if total_moves <= 0:
            return self.empty_cfop_stats()

        # MVP heuristic split. Real CFOP phase detection needs cube-state recognizers.
        # This gives useful timing/move/TPS buckets and is saved for replay/analysis.
        ratios = [0.10, 0.12, 0.10, 0.10, 0.10, 0.10, 0.18, 0.20]
        raw_counts = [max(0, int(round(total_moves * r))) for r in ratios]

        # Ensure total count matches.
        diff = total_moves - sum(raw_counts)
        raw_counts[-1] += diff

        # Avoid negative last bucket caused by rounding.
        if raw_counts[-1] < 0:
            for i in range(len(raw_counts) - 1):
                if raw_counts[i] > 0 and raw_counts[-1] < 0:
                    take = min(raw_counts[i], -raw_counts[-1])
                    raw_counts[i] -= take
                    raw_counts[-1] += take

        stats = []
        start_idx = 0
        start_time = 0

        for name, count in zip(CFOP_SEGMENTS, raw_counts):
            end_idx = min(total_moves, start_idx + count)

            if end_idx <= 0:
                end_time = 0
            elif end_idx >= total_moves:
                end_time = total_ms
            else:
                end_time = log[end_idx - 1]["time_ms"]

            seg_ms = max(0, end_time - start_time)
            tps = (count / (seg_ms / 1000.0)) if seg_ms > 0 else 0.0

            stats.append(
                {
                    "name": name,
                    "time_ms": seg_ms,
                    "time_text": format_time_ms(seg_ms),
                    "moves": count,
                    "tps": round(tps, 2),
                }
            )

            start_idx = end_idx
            start_time = end_time

        return stats

    def finish_ok(self):
        if self._saved_current_result:
            return TimerEvent(message=self.status_message)

        self.state = STATE_FINISHED
        self.final_time_ms = int(self.elapsed * 1000)
        self.cfop_stats = self.compute_cfop_stats(self.final_time_ms)
        self.status_message = f"还原完成：{format_time_ms(self.final_time_ms)}"

        record = {
            "timestamp": int(time.time()),
            "result": "OK",
            "time_ms": self.final_time_ms,
            "time_text": format_time_ms(self.final_time_ms),
            "scramble": " ".join(self.target_scramble_moves),
            "moves": self.solve_moves,
            "observation": "unlimited" if self.observation_unlimited else "15s",
            "move_log": list(self.solve_move_log),
            "cfop_stats": list(self.cfop_stats),
        }

        self.history = append_record(record)
        self._saved_current_result = True

        return TimerEvent(
            message=self.status_message,
            state_changed=True,
            finished=True,
        )

    def abandon(self):
        if self.state not in (STATE_SCRAMBLING, STATE_OBSERVING, STATE_TIMING, STATE_INVALID):
            self.status_message = "当前没有正在进行的还原"
            return TimerEvent(message=self.status_message)

        if not self._saved_current_result:
            partial_ms = int(self.elapsed * 1000) if self.state == STATE_TIMING else None

            record = {
                "timestamp": int(time.time()),
                "result": "DNF",
                "time_ms": partial_ms,
                "time_text": "DNF",
                "scramble": " ".join(self.target_scramble_moves),
                "moves": self.solve_moves,
                "observation": "unlimited" if self.observation_unlimited else "15s",
                "move_log": list(self.solve_move_log),
                "cfop_stats": self.compute_cfop_stats(partial_ms or 0),
            }

            self.history = append_record(record)
            self._saved_current_result = True

        self.state = STATE_ABANDONED
        self.status_message = "已放弃本次还原，结果记为 DNF"
        return TimerEvent(message=self.status_message, state_changed=True, abandoned=True)

    def main_time_text(self):
        if self.state == STATE_OBSERVING:
            if self.observation_unlimited:
                return "--:--:---"
            return f"观察 {max(0, int(self.observation_remaining + 0.999)):02d}"

        if self.state == STATE_FINISHED and self.final_time_ms is not None:
            return format_time_ms(self.final_time_ms)

        if self.state == STATE_TIMING:
            return format_seconds_time(self.elapsed)

        if self.state == STATE_ABANDONED:
            return "DNF"

        return "00:00:000"
