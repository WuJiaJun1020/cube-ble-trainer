import queue
import time
import copy
import random
import re
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QGuiApplication, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QBoxLayout,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QComboBox,
    QMessageBox,
    QListWidget,
    QListWidgetItem,
    QLayout,
    QPlainTextEdit,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ble.gan_ble_bridge import start_gan_bridge
from config import BLE_NAME_KEYWORD, ENABLE_BLE, ENABLE_GYRO_SYNC, DEFAULT_AA_SAMPLES
from practice.timer_practice import TimerPractice
from practice.scramble_generator import generate_scramble
from render.cube_gl import CubeGLWidget
from solver.auto_solver import solve_auto
from solver.history_solver import solve_from_history, invert_algorithm, normalize_move
from solver.solution_guide import SolutionGuide
from formulas.formula_library import load_cfop_library, save_cfop_library
from cube_model import native_to_oriented_vec, MOVE_DEFS, rotate_vec, layer_values, move_turns, create_solved_cube, commit_move, facelets_to_cubies, orient_cubies


def move_text(label):
    return str(label).replace("'", "′")

def format_time_ms(ms: int) -> str:
    ms = max(0, int(ms))
    minutes = ms // 60000
    seconds = (ms % 60000) // 1000
    millis = ms % 1000
    return f"{minutes:02d}:{seconds:02d}:{millis:03d}"


UI_SCALE = 1.0

# 3D 魔方距离相机的位置。数值越小/越负，魔方越小；数值越接近 0，魔方越大。
# 你觉得求解器魔方仍然偏大时，可以继续把 -10.0 调成 -10.5、-11.0 等。
TIMER_CUBE_ZOOM = -7.1
SOLVER_CUBE_ZOOM = -10.0
DEFAULT_CUBE_ZOOM = -8.0

# 计时训练固定使用白顶绿前；求解器则使用右侧“初始朝向”下拉框。
TIMER_INITIAL_ORIENTATION = "white_top_green_front"

PAGE_TIMER = 0
PAGE_FORMULA_PRACTICE = 1
PAGE_SOLVER = 2
PAGE_DEBUG = 3
PAGE_FORMULA_TRAINING = 4

DEBUG_MOVE_FACES = [
    "U", "D", "F", "B", "L", "R",
    "M", "E", "S",
    "u", "d", "f", "b", "l", "r",
    "x", "y", "z",
]
# 单步/多步调试只生成能通过智能魔方底层面转信号验证的公式。
# x/y/z 属于整体旋转，GAN 面转信号无法可靠判断，所以不参与生成。
DEBUG_FORMULA_FACES = [
    "U", "D", "F", "B", "L", "R",
    "M", "E", "S",
    "u", "d", "f", "b", "l", "r",
]
DEBUG_MOVE_SUFFIXES = ["", "'", "2"]
DEBUG_MANUAL_MOVES = [face + suffix for face in DEBUG_MOVE_FACES for suffix in DEBUG_MOVE_SUFFIXES]


# GAN 蓝牙魔方上报的是固定配色坐标系下的面：
# U=白、D=黄、F=绿、B=蓝、R=红、L=橙。
# 求解器允许把“黄顶绿前”等设为显示初始朝向，因此蓝牙面转需要先从
# 固定配色坐标系映射到当前求解器显示坐标系；计时训练仍固定白顶绿前，
# 所以计时页不做转换。
_NATIVE_FACE_TO_VEC = {
    "U": (0, 1, 0),
    "D": (0, -1, 0),
    "F": (0, 0, 1),
    "B": (0, 0, -1),
    "R": (1, 0, 0),
    "L": (-1, 0, 0),
}
_VEC_TO_DISPLAY_FACE = {
    (0, 1, 0): "U",
    (0, -1, 0): "D",
    (0, 0, 1): "F",
    (0, 0, -1): "B",
    (1, 0, 0): "R",
    (-1, 0, 0): "L",
}

def _split_move_suffix(label):
    text = str(label or "")
    if not text:
        return "", ""
    face = text[0]
    suffix = text[1:]
    return face, suffix


def ui_px(value):
    return max(1, round(float(value) * UI_SCALE))


def ui_size(width, height):
    return QSize(ui_px(width), ui_px(height))


def set_ui_scale(scale):
    global UI_SCALE
    UI_SCALE = max(0.55, min(1.45, float(scale)))


class FlowLayout(QLayout):
    def __init__(self, parent=None, margin=0, spacing=8):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(margin, margin, margin, margin)
        self._spacing = spacing

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, index):
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index):
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def expandingDirections(self):
        return Qt.Orientations(Qt.Orientation(0))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(), margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        margins = self.contentsMargins()
        x = rect.x() + margins.left()
        y = rect.y() + margins.top()
        line_height = 0

        max_x = rect.x() + rect.width() - margins.right()

        for item in self._items:
            wid = item.widget()
            space_x = self._spacing
            space_y = self._spacing
            item_size = item.sizeHint()

            next_x = x + item_size.width() + space_x

            if next_x - space_x > max_x and line_height > 0:
                x = rect.x() + margins.left()
                y += line_height + space_y
                next_x = x + item_size.width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item_size))

            x = next_x
            line_height = max(line_height, item_size.height())

        return y + line_height - rect.y() + margins.bottom()


class MoveChip(QWidget):
    def __init__(self, text, state="normal", parent=None):
        super().__init__(parent)
        self.text = move_text(text)
        self.state = state
        self.setFixedSize(ui_size(34, 28))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = self.rect().adjusted(2, 2, -2, -2)

        if self.state == "current":
            painter.setBrush(QColor("#1476ff"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 14, 14)
            text_color = QColor("white")
        elif self.state == "partial":
            painter.setPen(Qt.NoPen)

            # 计时练习风格：左半蓝表示当前动作，右半绿表示该双转/多段动作已完成一部分。
            painter.setBrush(QColor("#1476ff"))
            painter.drawRoundedRect(rect, 14, 14)

            painter.save()
            painter.setClipRect(rect.center().x(), rect.y(), rect.width() // 2 + 1, rect.height())
            painter.setBrush(QColor("#20c76a"))
            painter.drawRoundedRect(rect, 14, 14)
            painter.restore()

            painter.setPen(QPen(QColor(255, 255, 255, 180), 1))
            painter.drawLine(rect.center().x(), rect.top() + 4, rect.center().x(), rect.bottom() - 4)
            text_color = QColor("white")
        elif self.state == "done":
            painter.setBrush(QColor(180, 198, 218, 115))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 14, 14)
            text_color = QColor("#7388a1")
        else:
            painter.setBrush(QColor(255, 255, 255, 170))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 14, 14)
            text_color = QColor("#243246")

        painter.setPen(text_color)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, self.text)




class DebugProgressDots(QWidget):
    def __init__(self, completed=0, total=0, partial=False, parent=None):
        super().__init__(parent)
        self.completed = max(0, int(completed or 0))
        self.total = max(0, int(total or 0))
        self.partial = bool(partial)
        width = max(24, self.total * 16 + 8)
        self.setFixedSize(ui_size(width, 20))

    def paintEvent(self, event):
        if self.total <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        radius = ui_px(5)
        gap = ui_px(6)
        diameter = radius * 2
        x = ui_px(4)
        y = (self.height() - diameter) // 2
        current_index = min(self.completed, self.total - 1)
        for idx in range(self.total):
            if idx < self.completed:
                color = QColor("#20c76a")
            elif idx == current_index:
                color = QColor("#1476ff")
            else:
                color = QColor(180, 198, 218, 115)
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(x, y, diameter, diameter)
            x += diameter + gap

class FormulaPreviewImageWidget(QWidget):
    _COLOR_MAP = {
        "W": QColor("#f6f6f1"),
        "Y": QColor("#ffd51a"),
        "R": QColor("#ec1d1d"),
        "O": QColor("#ff7a17"),
        "B": QColor("#1359ff"),
        "G": QColor("#00c949"),
        "X": QColor("#2b2f36"),
        None: QColor("#2b2f36"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.facelets = None
        self.preview_category = "F2L"
        self.compact_mode = False
        self.setMinimumSize(ui_size(210, 160))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    @classmethod
    def build_facelets(cls, cubies):
        facelets = {
            "U": [["X" for _ in range(3)] for _ in range(3)],
            "F": [["X" for _ in range(3)] for _ in range(3)],
            "R": [["X" for _ in range(3)] for _ in range(3)],
            "L": [["X" for _ in range(3)] for _ in range(3)],
            "B": [["X" for _ in range(3)] for _ in range(3)],
        }
        if cubies:
            for cubie in cubies:
                x, y, z = cubie.pos
                for normal, color_key in cubie.stickers.items():
                    if normal == (0, 1, 0):
                        facelets["U"][z + 1][x + 1] = color_key
                    elif normal == (0, 0, 1):
                        facelets["F"][1 - y][x + 1] = color_key
                    elif normal == (1, 0, 0):
                        facelets["R"][1 - y][1 - z] = color_key
                    elif normal == (-1, 0, 0):
                        facelets["L"][1 - y][z + 1] = color_key
                    elif normal == (0, 0, -1):
                        facelets["B"][1 - y][1 - x] = color_key
        return facelets

    def clear_preview(self):
        self.facelets = None
        self.update()

    def set_cube_state(self, cubies, category="F2L"):
        self.preview_category = str(category or "F2L").upper()
        self.facelets = self.build_facelets(cubies)
        self.update()

    def _poly(self, points):
        poly = QPolygonF()
        for x, y in points:
            poly.append(QPointF(x, y))
        return poly

    def _draw_cell(self, painter, points, color_key):
        painter.setPen(QPen(QColor(10, 14, 22), 1.25))
        painter.setBrush(self._COLOR_MAP.get(color_key, self._COLOR_MAP["X"]))
        painter.drawPolygon(self._poly(points))

    def _top_cell_points(self, ox, oy, sx, sy, x, z):
        cx = ox + (x - z) * sx
        cy = oy + (x + z) * sy
        return [
            (cx, cy),
            (cx + sx, cy + sy),
            (cx, cy + 2 * sy),
            (cx - sx, cy + sy),
        ]

    def _face_color(self, face, row, col):
        if not self.facelets:
            return "X"
        return self.facelets.get(face, [["X"] * 3 for _ in range(3)])[row][col]

    def _draw_color_line(self, painter, start, end, color_key):
        if color_key in ("X", None):
            return
        outline_pen = QPen(QColor(16, 20, 28), 6.2)
        outline_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(outline_pen)
        painter.drawLine(QPointF(*start), QPointF(*end))

        pen = QPen(self._COLOR_MAP.get(color_key, self._COLOR_MAP["X"]), 4.2)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(*start), QPointF(*end))

    def _draw_hidden_top_lines(self, painter, ox, oy, sx, sy):
        if not self.facelets:
            return
        category = self.preview_category
        if category not in ("F2L", "OLL", "PLL"):
            return

        # L face top row -> hidden left side hints.
        for z in [-1, 0, 1]:
            color = self._face_color("L", 0, z + 1)
            if color == "X":
                continue
            points = self._top_cell_points(ox, oy, sx, sy, -1, z)
            start = (points[3][0] - 6, points[3][1] - 4)
            end = (points[0][0] - 6, points[0][1] - 4)
            self._draw_color_line(painter, start, end, color)

        # B face top row -> hidden back side hints.
        for x in [-1, 0, 1]:
            color = self._face_color("B", 0, 1 - x)
            if color == "X":
                continue
            points = self._top_cell_points(ox, oy, sx, sy, x, -1)
            start = (points[0][0] + 5, points[0][1] - 5)
            end = (points[1][0] + 5, points[1][1] - 5)
            self._draw_color_line(painter, start, end, color)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        if not self.facelets:
            painter.setPen(QColor("#7C8EA6"))
            hint_rect = self.rect().adjusted(ui_px(12), ui_px(12), -ui_px(12), -ui_px(12))
            painter.drawText(hint_rect, Qt.AlignCenter | Qt.TextWordWrap, "点击“刷新预览”后，在这里显示公式立体预览图。")
            return

        if not self.compact_mode:
            painter.fillRect(self.rect(), QColor(255, 255, 255, 62))

        w = max(1, self.width())
        h = max(1, self.height())
        if self.compact_mode:
            # Draw the thumbnail directly at its final size.  Use a tighter
            # cube scale here so the table preview fills the cell, without
            # rendering a huge offscreen image and scaling it down.
            sx = min(w / 7.6, h / 6.6)
        else:
            sx = min(w / 11.0, h / 9.5)
        sy = sx * 0.5
        cell_h = sx
        ox = w / 2

        # Vertically center the cube so both the large preview panel and the
        # table thumbnails show the full top face without clipping.
        top_margin = ui_px(4) if self.compact_mode else ui_px(8)
        bottom_margin = ui_px(4) if self.compact_mode else ui_px(8)
        available_h = max(1.0, h - top_margin - bottom_margin)
        cube_h = 6.0 * sx
        top_y = top_margin + max(0.0, (available_h - cube_h) / 2.0)
        oy = top_y + sx

        # Draw order: top, right, front.
        for z in range(-1, 2):
            for x in range(-1, 2):
                points = self._top_cell_points(ox, oy, sx, sy, x, z)
                self._draw_cell(painter, points, self._face_color("U", z + 1, x + 1))

        for y in range(1, -2, -1):
            row = 1 - y
            for z in range(-1, 2):
                col = 1 - z
                base_x = ox + (1 - z) * sx
                base_y = oy + (1 + z) * sy + (1 - y) * cell_h
                points = [
                    (base_x + sx, base_y + sy),
                    (base_x, base_y + 2 * sy),
                    (base_x, base_y + 2 * sy + cell_h),
                    (base_x + sx, base_y + sy + cell_h),
                ]
                self._draw_cell(painter, points, self._face_color("R", row, col))

        for y in range(1, -2, -1):
            row = 1 - y
            for x in range(-1, 2):
                col = x + 1
                base_x = ox + (x - 1) * sx
                base_y = oy + (x + 1) * sy + (1 - y) * cell_h
                points = [
                    (base_x - sx, base_y + sy),
                    (base_x, base_y + 2 * sy),
                    (base_x, base_y + 2 * sy + cell_h),
                    (base_x - sx, base_y + sy + cell_h),
                ]
                self._draw_cell(painter, points, self._face_color("F", row, col))

        self._draw_hidden_top_lines(painter, ox, oy, sx, sy)


class FormulaTopPreviewWidget(QWidget):
    _COLOR_MAP = FormulaPreviewImageWidget._COLOR_MAP

    def __init__(self, parent=None):
        super().__init__(parent)
        self.facelets = None
        self.preview_category = "F2L"
        self.setMinimumSize(ui_size(180, 140))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def clear_preview(self):
        self.facelets = None
        self.update()

    def set_cube_state(self, cubies, category="F2L"):
        self.preview_category = str(category or "F2L").upper()
        self.facelets = FormulaPreviewImageWidget.build_facelets(cubies)
        self.update()

    def _face_color(self, face, row, col):
        if not self.facelets:
            return "X"
        return self.facelets.get(face, [["X"] * 3 for _ in range(3)])[row][col]

    def _draw_square(self, painter, x, y, size, color_key):
        painter.setPen(QPen(QColor(20, 26, 36), 1.2))
        painter.setBrush(self._COLOR_MAP.get(color_key, self._COLOR_MAP["X"]))
        painter.drawRoundedRect(x, y, size, size, 4, 4)

    def _draw_side_line(self, painter, start, end, color_key):
        if color_key in ("X", None):
            return
        # Draw a black outline first so bright colors (especially white/yellow)
        # remain visible on the light panel background, then draw a single
        # thicker colored line on top.
        outline_pen = QPen(QColor(16, 20, 28), 8.0)
        outline_pen.setCapStyle(Qt.RoundCap)
        painter.setPen(outline_pen)
        painter.drawLine(QPointF(*start), QPointF(*end))

        pen = QPen(self._COLOR_MAP.get(color_key, self._COLOR_MAP["X"]), 6.0)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(QPointF(*start), QPointF(*end))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        if not self.facelets:
            painter.setPen(QColor("#7C8EA6"))
            hint_rect = self.rect().adjusted(ui_px(12), ui_px(12), -ui_px(12), -ui_px(12))
            painter.drawText(hint_rect, Qt.AlignCenter | Qt.TextWordWrap, "俯视模式：显示顶面 9 块和外围侧色线。")
            return

        painter.fillRect(self.rect(), QColor(255, 255, 255, 62))

        w = max(1, self.width())
        h = max(1, self.height())
        gap = 0
        side_line_offset = ui_px(8)
        outer_margin = ui_px(12)
        cell = min((w - 2 * outer_margin - 2 * gap) / 3.0, (h - 2 * outer_margin - 2 * gap) / 3.0)
        cell = max(14.0, cell)
        grid_w = cell * 3 + gap * 2
        grid_h = grid_w
        left = (w - grid_w) / 2.0
        top = (h - grid_h) / 2.0

        centers_x = []
        centers_y = []
        for row in range(3):
            cy = top + row * (cell + gap) + cell / 2.0
            centers_y.append(cy)
            for col in range(3):
                if row == 0:
                    centers_x.append(left + col * (cell + gap) + cell / 2.0)
                x = left + col * (cell + gap)
                y = top + row * (cell + gap)
                self._draw_square(painter, x, y, cell, self._face_color("U", row, col))

        # Back/top edge: left->right follows x=-1..1, which maps to B row0 col 2..0.
        y_line = top - side_line_offset
        for col in range(3):
            cx = centers_x[col]
            x1 = cx - cell * 0.32
            x2 = cx + cell * 0.32
            color = self._face_color("B", 0, 2 - col)
            self._draw_side_line(painter, (x1, y_line), (x2, y_line), color)

        # Front/bottom edge.
        y_line = top + grid_h + side_line_offset
        for col in range(3):
            cx = centers_x[col]
            x1 = cx - cell * 0.32
            x2 = cx + cell * 0.32
            color = self._face_color("F", 0, col)
            self._draw_side_line(painter, (x1, y_line), (x2, y_line), color)

        # Left edge: top->bottom follows z=-1..1.
        x_line = left - side_line_offset
        for row in range(3):
            cy = centers_y[row]
            y1 = cy - cell * 0.32
            y2 = cy + cell * 0.32
            color = self._face_color("L", 0, row)
            self._draw_side_line(painter, (x_line, y1), (x_line, y2), color)

        # Right edge: top->bottom follows z=-1..1, which maps to R row0 col 2..0.
        x_line = left + grid_w + side_line_offset
        for row in range(3):
            cy = centers_y[row]
            y1 = cy - cell * 0.32
            y2 = cy + cell * 0.32
            color = self._face_color("R", 0, 2 - row)
            self._draw_side_line(painter, (x_line, y1), (x_line, y2), color)

        painter.setPen(QColor(124, 142, 166))
        font = painter.font()
        font.setPointSize(max(8, font.pointSize() - 1))
        painter.setFont(font)
        label_rect = QRect(int(left), int(top + grid_h + ui_px(16)), int(grid_w), ui_px(18))
        painter.drawText(label_rect, Qt.AlignCenter, "俯视图")


class FormulaCaseEditDialog(QDialog):
    def __init__(self, parent=None, formula=None, default_category="F2L"):
        super().__init__(parent)
        self.setWindowTitle("公式信息")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.category_combo = QComboBox()
        for item in ["F2L", "OLL", "PLL"]:
            self.category_combo.addItem(item, item)

        self.case_id_edit = QLineEdit()
        self.name_edit = QLineEdit()
        self.slot_edit = QLineEdit()
        self.algorithms_edit = QPlainTextEdit()
        self.algorithms_edit.setMinimumHeight(ui_px(130))
        self.algorithms_edit.setPlaceholderText("每行一个解法，例如：\nR U R' U'\ny R U' R'")

        form.addRow("分类", self.category_combo)
        form.addRow("编号", self.case_id_edit)
        form.addRow("名称", self.name_edit)
        form.addRow("位置/说明", self.slot_edit)
        form.addRow("解法列表", self.algorithms_edit)
        layout.addLayout(form)

        hint = QLabel("提示：解法请每行填写一个，支持 U/D/F/B/L/R/M/E/S、x/y/z、小写宽层、'、2。")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        category = default_category if default_category in ("F2L", "OLL", "PLL") else "F2L"
        if formula:
            category = formula.get("category", category)
            self.case_id_edit.setText(formula.get("case_id", ""))
            self.name_edit.setText(formula.get("name", ""))
            self.slot_edit.setText(formula.get("slot", ""))
            self.algorithms_edit.setPlainText("\n".join(formula.get("algorithms", [])))

        if category in ("F2L", "OLL", "PLL"):
            self.category_combo.setCurrentText(category)

    def _accept(self):
        if not self.case_id_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写编号。")
            return
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "提示", "请填写名称。")
            return
        algorithms = [line.strip() for line in self.algorithms_edit.toPlainText().splitlines() if line.strip()]
        if not algorithms:
            QMessageBox.warning(self, "提示", "请至少填写一个解法。")
            return
        self.accept()

    def values(self):
        return {
            "category": self.category_combo.currentText(),
            "case_id": self.case_id_edit.text().strip(),
            "name": self.name_edit.text().strip(),
            "slot": self.slot_edit.text().strip(),
            "algorithms": [line.strip() for line in self.algorithms_edit.toPlainText().splitlines() if line.strip()],
        }



class SegmentStatsPopup(QDialog):
    def __init__(self, stats, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("StatsPopup")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui_px(14), ui_px(12), ui_px(14), ui_px(12))
        layout.setSpacing(ui_px(8))

        title = QLabel("分步成绩")
        title.setObjectName("SectionTitle")
        layout.addWidget(title)

        table = QTableWidget(len(stats), 4)
        table.setObjectName("StatsTable")
        table.setHorizontalHeaderLabels(["阶段", "时间", "步数", "TPS"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setMinimumWidth(ui_px(360))
        table.setMinimumHeight(ui_px(240))

        for row, stat in enumerate(stats):
            values = [
                stat.get("name", ""),
                stat.get("time_text", "00:00:000"),
                str(stat.get("moves", 0)),
                f"{float(stat.get('tps', 0.0)):.2f}",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, col, item)

        layout.addWidget(table)


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        child_layout = item.layout()

        if widget is not None:
            widget.deleteLater()

        if child_layout is not None:
            clear_layout(child_layout)


class ClickableFrame(QFrame):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            pos = event.position().toPoint() if hasattr(event, 'position') else event.pos()
            if self.rect().contains(pos):
                self.clicked.emit()
        super().mouseReleaseEvent(event)


class MethodEntranceCard(ClickableFrame):
    def __init__(self, title, subtitle, status='进入', enabled=True, parent=None):
        super().__init__(parent)
        self.enabled_entry = enabled
        self.setObjectName('MethodCard')
        self.setMinimumSize(ui_size(180, 112))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(ui_px(14), ui_px(12), ui_px(14), ui_px(12))
        layout.setSpacing(ui_px(7))

        title_label = QLabel(title)
        title_label.setObjectName('CardTitle')
        layout.addWidget(title_label)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName('CardHint')
        subtitle_label.setWordWrap(True)
        layout.addWidget(subtitle_label, 1)

        status_label = QLabel(status)
        status_label.setObjectName('BadgeLabel')
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setFixedWidth(ui_px(76))
        layout.addWidget(status_label, 0, Qt.AlignLeft)

        if not enabled:
            self.setProperty('disabledCard', True)




class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Rubik Trainer - PySide6")
        # Qt/PySide 的 resize() 使用逻辑像素。这里把下拉框理解为“物理像素”，
        # 所以实际 resize 前会按当前屏幕 devicePixelRatio 做反向换算。
        self.base_physical_size = (1920, 1080)
        self.current_physical_size = self.base_physical_size
        self.ui_scale = 1.0
        set_ui_scale(self.ui_scale)
        self.setMinimumSize(420, 236)

        self.window_size_presets = [
            ("1280×720 物理", 1280, 720),
            ("1920×1080 物理", 1920, 1080),
            ("2560×1440 物理", 2560, 1440),
        ]
        self.aa_sample_options = [
            ("抗锯齿关", 0),
            ("2x MSAA", 2),
            ("4x MSAA", 4),
            ("8x MSAA", 8),
        ]
        self.current_aa_samples = DEFAULT_AA_SAMPLES

        # 求解器初始朝向。顺序按常用程度排列，默认白顶绿前。
        self.cube_orientation_options = [
            ("白顶绿前", "white_top_green_front"),
            ("黄顶绿前", "yellow_top_green_front"),
            ("白顶蓝前", "white_top_blue_front"),
            ("黄顶蓝前", "yellow_top_blue_front"),
            ("白顶红前", "white_top_red_front"),
            ("白顶橙前", "white_top_orange_front"),
            ("黄顶红前", "yellow_top_red_front"),
            ("黄顶橙前", "yellow_top_orange_front"),
            ("绿顶白前", "green_top_white_front"),
            ("绿顶黄前", "green_top_yellow_front"),
            ("蓝顶白前", "blue_top_white_front"),
            ("蓝顶黄前", "blue_top_yellow_front"),
            ("红顶白前", "red_top_white_front"),
            ("红顶黄前", "red_top_yellow_front"),
            ("橙顶白前", "orange_top_white_front"),
            ("橙顶黄前", "orange_top_yellow_front"),
        ]
        self.solver_initial_orientation = "white_top_green_front"
        self.debug_initial_orientation = "white_top_green_front"
        self.debug_current_orientation = self.debug_initial_orientation
        self.debug_moves = []
        self.debug_index = 0
        self.debug_active = False
        self.debug_mode = None
        self.debug_match_states = []
        self.debug_progress_completed = 0
        self.debug_progress_total = 0
        self.debug_progress_partial = False
        self._last_debug_chip_key = None

        self.ble_queue = queue.Queue()
        self.ble_control_queue = queue.Queue()
        self.ble_status = "off" if not ENABLE_BLE else "disconnected"
        self.ble_device_name = ""
        self.ble_device_address = ""
        self.ble_protocol = ""
        self.ble_mac = ""
        self.ble_battery = None
        self.ble_hardware = {}
        self.ble_last_facelets = None
        self.ble_last_state = None
        self.ble_last_move_meta = None
        self.ble_last_move_serial = None
        self.ble_recovered_move_count = 0
        self.ble_unrecovered_gap_count = 0
        self.ble_last_history_message = ""
        self.ble_last_message = ""
        self.ble_initial_facelets_applied = False
        # 保存“实体魔方当前状态”的原生坐标副本。
        # 3D 主魔方只有一个实例；切换页面时不能再无条件 reset 到还原态，
        # 而应优先把这里的实体状态按目标页面朝向重新投放到 3D。
        self.ble_physical_cubies_native = None
        self.ble_last_full_state_cubies_native = None
        self.ble_physical_state_ready = False
        self.ble_physical_move_count = 0
        # FACELETS 应用改为“按需消费”：启动首次应用一次；之后只有
        # 页面进入计时/求解器/调试时，才消费下一条 FACELETS 并覆盖 3D。
        self.ble_pending_facelets_sync_page = None
        self.ble_pending_facelets_sync_reason = ""

        self.move_history = []
        self.solution_guide = SolutionGuide()
        self.solve_mode = "auto"
        self._pending_solver_recompute_prefix = None
        self.timer_practice = TimerPractice()
        self.cfop_formula_library = load_cfop_library()
        self.formula_practice_initial_orientation = "white_top_green_front"
        self.formula_practice_category = "全部"
        self._formula_filtered_cases = []
        self.formula_preview_started = False
        self._formula_case_preview_cache = {}
        self.formula_practice_stats = self._load_formula_practice_stats()
        self.formula_training_active = False
        self.formula_training_cases = []
        self.formula_training_case_index = 0
        self.formula_training_case = None
        self.formula_training_algorithm = None
        self.formula_training_guide = SolutionGuide()
        self.formula_training_started_at = None
        self.formula_training_elapsed_ms = 0
        self._last_formula_training_timer_bucket = None
        self._formula_stats_dirty = False
        self._last_formula_training_chip_key = None
        self.formula_training_last_finish_ms = None
        self.formula_training_match_states = []
        self.formula_training_progress_completed = 0
        self.formula_training_progress_total = 0
        self.formula_training_progress_partial = False
        self.formula_training_pending_auto_moves = []
        self.formula_training_guide_arrows_enabled = True


        self._last_scramble_chip_key = None
        self._last_solution_chip_key = None
        self._last_history_key = None
        self._last_cfop_key = None
        self.segment_stats_popup = None

        self.replay_record = None
        self.replay_moves = []
        self.replay_index = 0
        self.replay_elapsed_ms = 0
        self.replay_paused = False

        self.replay_timer = QTimer(self)
        self.replay_timer.timeout.connect(self.tick_replay)
        self.replay_timer.setInterval(20)

        self._build_ui()
        self._apply_style()
        self._bind_shortcuts()
        self.apply_window_size_preset()

        if ENABLE_BLE:
            self.ble_status = "scanning"
            self._update_ble_status()
            start_gan_bridge(self.ble_queue, control_queue=self.ble_control_queue, name_keyword=BLE_NAME_KEYWORD)
        else:
            self._update_ble_status()

        self.ble_poll = QTimer(self)
        self.ble_poll.timeout.connect(self.process_ble_queue)
        self.ble_poll.start(30)

        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_timer_practice)
        self.ui_timer.start(1000 // 60)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)

        main = QVBoxLayout(root)
        self.root_layout = main
        main.setContentsMargins(self._px(12), self._px(10), self._px(12), self._px(12))
        main.setSpacing(self._px(10))

        header = QHBoxLayout()
        self.title_label = QLabel("魔方练习")
        self.title_label.setObjectName("TitleLabel")

        header.addWidget(self.title_label)
        header.addStretch()

        self.aa_label = QLabel("抗锯齿")
        self.aa_label.setObjectName("HeaderLabel")
        self.aa_combo = QComboBox()
        self.aa_combo.setObjectName("ResolutionCombo")
        self.aa_combo.setFixedWidth(self._px(104))
        self.aa_combo.setToolTip("调整 3D 魔方抗锯齿采样。数值越高边缘越平滑，但显卡负载越高。")
        for text, samples in self.aa_sample_options:
            self.aa_combo.addItem(text, samples)
        default_index = next((i for i, (_, samples) in enumerate(self.aa_sample_options) if samples == self.current_aa_samples), 2)
        self.aa_combo.setCurrentIndex(default_index)
        self.aa_combo.currentIndexChanged.connect(self.apply_antialiasing_preset)

        self.window_size_label = QLabel("窗口")
        self.window_size_label.setObjectName("HeaderLabel")
        self.window_size_combo = QComboBox()
        self.window_size_combo.setObjectName("ResolutionCombo")
        self.window_size_combo.setFixedWidth(self._px(138))
        self.window_size_combo.setToolTip("按物理像素设置窗口大小，会自动换算当前屏幕 DPI 缩放")
        for text, width, height in self.window_size_presets:
            self.window_size_combo.addItem(text, (width, height))
        self.window_size_combo.setCurrentIndex(1)
        self.window_size_combo.currentIndexChanged.connect(self.apply_window_size_preset)

        self.ble_dot = QLabel("●")
        self.ble_dot.setObjectName("BleDot")
        self.ble_label = QLabel("未连接")
        self.ble_label.setObjectName("Pill")

        self.btn_gyro_toggle = QPushButton("陀螺仪：开")
        self.btn_gyro_toggle.setObjectName("PillButton")
        self.btn_gyro_toggle.setCheckable(True)
        self.btn_gyro_toggle.setChecked(True)
        self.btn_gyro_toggle.clicked.connect(self.toggle_gyro_follow)


        header.addWidget(self.aa_label)
        header.addWidget(self.aa_combo)
        header.addWidget(self.window_size_label)
        header.addWidget(self.window_size_combo)
        header.addWidget(self.ble_dot)
        header.addWidget(self.ble_label)
        header.addWidget(self.btn_gyro_toggle)
        main.addLayout(header)

        body = QHBoxLayout()
        self.body_layout = body
        body.setSpacing(self._px(12))
        main.addLayout(body, 1)

        self.nav_panel = QFrame()
        self.nav_panel.setObjectName("NavPanel")
        self.nav_panel.setFixedWidth(self._px(140))

        nav_layout = QVBoxLayout(self.nav_panel)
        self.nav_layout = nav_layout
        nav_layout.setContentsMargins(self._px(10), self._px(14), self._px(10), self._px(14))
        nav_layout.setSpacing(self._px(8))

        self.btn_timer = QPushButton("计时练习")
        self.btn_formula_practice = QPushButton("公式练习")
        self.btn_solver = QPushButton("魔方求解器")
        self.btn_debug = QPushButton("调试板块")
        for btn in [self.btn_timer, self.btn_formula_practice, self.btn_solver, self.btn_debug]:
            btn.setCheckable(True)
            btn.setObjectName("NavButton")
            nav_layout.addWidget(btn)

        nav_layout.addStretch()
        body.addWidget(self.nav_panel)

        content = QBoxLayout(QBoxLayout.TopToBottom)
        self.content_layout = content
        content.setContentsMargins(0, self._px(8), 0, 0)
        content.setSpacing(self._px(10))
        body.addLayout(content, 1)

        self.cube_stage = QFrame()
        self.cube_stage.setObjectName("CubeStage")
        self.cube_stage.setMinimumHeight(self._px(300))
        self.cube_stage.setMaximumHeight(self._px(430))
        self.cube_stage.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        cube_stage_layout = QVBoxLayout(self.cube_stage)
        self.cube_stage_layout = cube_stage_layout
        cube_stage_layout.setContentsMargins(0, 0, 0, 0)
        cube_stage_layout.setSpacing(0)

        self.cube_top_row = QHBoxLayout()
        self.cube_top_row.setContentsMargins(0, 0, 0, 0)
        self.cube_top_row.setSpacing(self._px(10))

        self.cube = CubeGLWidget(aa_samples=self.current_aa_samples)
        self.cube.move_committed.connect(self.on_cube_move_committed)
        self.cube_top_row.addWidget(self.cube, 1)

        self.today_card = self._build_today_practice_card()
        self.today_card.setFixedWidth(self._px(190))
        self.today_card.hide()
        self.cube_top_row.addWidget(self.today_card, 0)

        cube_stage_layout.addLayout(self.cube_top_row, 1)

        self.solver_cube_actions = QWidget()
        self.solver_cube_actions.setObjectName("CubeActionBar")
        solver_cube_actions_layout = QHBoxLayout(self.solver_cube_actions)
        solver_cube_actions_layout.setContentsMargins(self._px(14), 0, self._px(14), self._px(14))
        solver_cube_actions_layout.setSpacing(self._px(8))
        self.btn_solver_random_scramble = QPushButton("随机打乱")
        self.btn_solver_random_scramble.setObjectName("PrimaryButton")
        self.btn_solver_random_scramble.clicked.connect(self.random_scramble_solver)
        solver_cube_actions_layout.addStretch()
        solver_cube_actions_layout.addWidget(self.btn_solver_random_scramble)
        solver_cube_actions_layout.addStretch()
        self.solver_cube_actions.hide()
        cube_stage_layout.addWidget(self.solver_cube_actions, 0)

        content.addWidget(self.cube_stage, 0)

        self.panel_stack = QStackedWidget()
        self.panel_stack.setMinimumHeight(self._px(170))
        self.panel_stack.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        content.addWidget(self.panel_stack, 1)

        self.timer_detail_page = self._build_timer_detail_page()
        self.timer_detail_page.hide()
        content.addWidget(self.timer_detail_page, 1)

        self._build_timer_panel()
        self._build_formula_practice_panel()
        self._build_solver_panel()
        self._build_debug_panel()
        self._build_formula_training_panel()
        self.nav_group = QButtonGroup(self)
        for i, btn in enumerate([self.btn_timer, self.btn_formula_practice, self.btn_solver, self.btn_debug]):
            self.nav_group.addButton(btn, i)

        self.nav_group.idClicked.connect(self.switch_page)
        self.btn_timer.setChecked(True)
        self.switch_page(PAGE_TIMER)

    def _px(self, value):
        return max(1, round(float(value) * self.ui_scale))

    def _update_ui_scale_from_physical_size(self, physical_width, physical_height):
        base_width, base_height = self.base_physical_size
        scale_w = physical_width / base_width
        scale_h = physical_height / base_height
        self.ui_scale = max(0.62, min(1.38, min(scale_w, scale_h)))
        set_ui_scale(self.ui_scale)

    def _apply_responsive_metrics(self):
        if hasattr(self, "root_layout"):
            self.root_layout.setContentsMargins(self._px(12), self._px(10), self._px(12), self._px(12))
            self.root_layout.setSpacing(self._px(10))
        if hasattr(self, "body_layout"):
            self.body_layout.setSpacing(self._px(12))
        if hasattr(self, "content_layout"):
            self.content_layout.setContentsMargins(0, self._px(8), 0, 0)
            self.content_layout.setSpacing(self._px(10))
        if hasattr(self, "nav_layout"):
            self.nav_layout.setContentsMargins(self._px(10), self._px(14), self._px(10), self._px(14))
            self.nav_layout.setSpacing(self._px(8))
        if hasattr(self, "aa_combo"):
            self.aa_combo.setFixedWidth(self._px(104))
        if hasattr(self, "window_size_combo"):
            self.window_size_combo.setFixedWidth(self._px(138))
        if hasattr(self, "orientation_combo"):
            self.orientation_combo.setFixedWidth(self._px(118))
        if hasattr(self, "debug_orientation_combo"):
            self.debug_orientation_combo.setFixedWidth(self._px(118))
        if hasattr(self, "nav_panel"):
            self.nav_panel.setFixedWidth(self._px(140))
        if hasattr(self, "panel_stack"):
            self.panel_stack.setMinimumHeight(self._px(170))
        if hasattr(self, "solver_cube_actions"):
            layout = self.solver_cube_actions.layout()
            if layout is not None:
                layout.setContentsMargins(self._px(14), 0, self._px(14), self._px(14))
                layout.setSpacing(self._px(8))
        if hasattr(self, "solution_chip_box"):
            self.solution_chip_box.setMinimumHeight(self._px(110))
            self.solution_chip_box.setMaximumHeight(self._px(210))
        if hasattr(self, "manual_scroll"):
            self.manual_scroll.setMinimumHeight(self._px(180))
        if hasattr(self, "history_card"):
            self.history_card.setFixedWidth(self._px(190))
        if hasattr(self, "today_card"):
            self.today_card.setFixedWidth(self._px(190))
        if hasattr(self, "today_rows"):
            for row in self.today_rows:
                row.setFixedHeight(self._px(22))
        if hasattr(self, "formula_case_table"):
            self.formula_case_table.setMinimumWidth(self._px(620))
            self.formula_case_table.setMaximumWidth(16777215)
            self.formula_case_table.verticalHeader().setDefaultSectionSize(self._px(120))
            self.formula_case_table.setColumnWidth(2, self._px(180))
            self.formula_case_table.setColumnWidth(3, self._px(104))
            self.formula_case_table.setColumnWidth(4, self._px(104))
        if hasattr(self, "formula_solution_box"):
            self.formula_solution_box.setMinimumHeight(self._px(250))
            self.formula_solution_box.setMaximumHeight(self._px(360))
        if hasattr(self, "formula_preview_cube"):
            self.formula_preview_cube.setMinimumSize(self._px(160), self._px(120))
            self.formula_preview_cube.setMaximumHeight(self._px(180))
        if hasattr(self, "formula_top_preview"):
            self.formula_top_preview.setMinimumSize(self._px(150), self._px(120))
            self.formula_top_preview.setMaximumHeight(self._px(180))
        if hasattr(self, "formula_training_preview_box"):
            self.formula_training_preview_box.setMinimumHeight(self._px(190))
            self.formula_training_preview_box.setMaximumHeight(self._px(260))
        if hasattr(self, "formula_training_preview_cube"):
            self.formula_training_preview_cube.setMinimumSize(self._px(150), self._px(110))
            self.formula_training_preview_cube.setMaximumHeight(self._px(170))
        if hasattr(self, "formula_training_top_preview"):
            self.formula_training_top_preview.setMinimumSize(self._px(150), self._px(110))
            self.formula_training_top_preview.setMaximumHeight(self._px(170))
        if hasattr(self, "cube_top_row"):
            self.cube_top_row.setSpacing(self._px(10))
        if hasattr(self, "cube_stage") and hasattr(self, "panel_stack"):
            self.switch_page(self.panel_stack.currentIndex())

    def _current_screen_dpr(self):
        """返回当前窗口所在屏幕的缩放倍率。

        Qt 的窗口尺寸 API 使用逻辑像素；Windows 高 DPI 下：
        物理像素 = 逻辑像素 × devicePixelRatio。
        """
        screen = None
        handle = self.windowHandle()
        if handle is not None:
            screen = handle.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return 1.0
        return max(1.0, float(screen.devicePixelRatio()))

    def resize_to_physical_pixels(self, physical_width, physical_height):
        """把用户选择的物理像素尺寸换算成 Qt 需要的逻辑像素尺寸。"""
        self.current_physical_size = (physical_width, physical_height)
        self._update_ui_scale_from_physical_size(physical_width, physical_height)
        dpr = self._current_screen_dpr()
        logical_width = max(1, round(physical_width / dpr))
        logical_height = max(1, round(physical_height / dpr))
        self.resize(logical_width, logical_height)
        self._apply_style()
        self._apply_responsive_metrics()

    def apply_window_size_preset(self, *_args):
        if not hasattr(self, "window_size_combo"):
            return

        size = self.window_size_combo.currentData()
        if not size:
            return

        physical_width, physical_height = size
        self.resize_to_physical_pixels(physical_width, physical_height)

    def _cube_state_snapshot(self):
        if not hasattr(self, "cube") or self.cube is None:
            return {}
        cube = self.cube
        return {
            "cubies": copy.deepcopy(cube.cubies),
            "move_queue": list(cube.move_queue),
            "active_move": copy.deepcopy(cube.active_move),
            "rot_x": cube.rot_x,
            "rot_y": cube.rot_y,
            "zoom": cube.zoom,
            "guide_move": cube.guide_move,
            "elapsed": cube.elapsed,
            "use_gyro": cube.use_gyro,
            "gyro_follow_enabled": cube.gyro_follow_enabled,
            "target_quat": cube.target_quat,
            "current_quat": cube.current_quat,
            "base_quat": cube.base_quat,
            "gyro_initialized": cube._gyro_initialized,
        }

    def _restore_cube_state(self, cube, state):
        if not state:
            return
        cube.cubies = state.get("cubies", cube.cubies)
        cube.move_queue.clear()
        cube.move_queue.extend(state.get("move_queue", []))
        cube.active_move = state.get("active_move")
        cube.rot_x = state.get("rot_x", cube.rot_x)
        cube.rot_y = state.get("rot_y", cube.rot_y)
        cube.zoom = state.get("zoom", cube.zoom)
        cube.guide_move = state.get("guide_move", cube.guide_move)
        cube.elapsed = state.get("elapsed", cube.elapsed)
        cube.use_gyro = state.get("use_gyro", cube.use_gyro)
        cube.gyro_follow_enabled = state.get("gyro_follow_enabled", cube.gyro_follow_enabled)
        cube.target_quat = state.get("target_quat", cube.target_quat)
        cube.current_quat = state.get("current_quat", cube.current_quat)
        cube.base_quat = state.get("base_quat", cube.base_quat)
        cube._gyro_initialized = state.get("gyro_initialized", cube._gyro_initialized)
        cube.update()

    def _rebuild_cube_for_antialiasing(self, samples):
        if not hasattr(self, "cube") or not hasattr(self, "cube_top_row"):
            return

        old_cube = self.cube
        state = self._cube_state_snapshot()
        insert_index = self.cube_top_row.indexOf(old_cube)
        if insert_index < 0:
            insert_index = 0

        old_cube.setParent(None)
        old_cube.deleteLater()

        self.cube = CubeGLWidget(aa_samples=samples)
        self._restore_cube_state(self.cube, state)
        self.cube.move_committed.connect(self.on_cube_move_committed)
        self.cube_top_row.insertWidget(insert_index, self.cube, 1)
        if hasattr(self, "btn_gyro_toggle"):
            self.cube.gyro_follow_enabled = self.btn_gyro_toggle.isChecked()
            if not self.cube.gyro_follow_enabled:
                self.cube.use_gyro = False

    def apply_antialiasing_preset(self, *_args):
        if not hasattr(self, "aa_combo"):
            return
        samples = self.aa_combo.currentData()
        if samples is None:
            samples = DEFAULT_AA_SAMPLES
        samples = int(samples)
        if samples == getattr(self, "current_aa_samples", DEFAULT_AA_SAMPLES):
            return
        self.current_aa_samples = samples
        # MSAA sample count is part of the OpenGL context format. Rebuild only
        # the 3D cube widget so the new sample count takes effect immediately
        # while preserving cube state, animation queue, camera and gyro state.
        self._rebuild_cube_for_antialiasing(samples)

    def _build_chip_box(self):
        box = QFrame()
        box.setObjectName("ChipBox")
        layout = FlowLayout(box, margin=8, spacing=7)
        return box, layout

    def _set_chip_row(
        self,
        layout,
        moves,
        current_index=-1,
        active=False,
        partial_index=-1,
        empty_text="暂无内容",
    ):
        clear_layout(layout)

        if not moves:
            label = QLabel(empty_text)
            label.setObjectName("MutedLabel")
            label.setFixedHeight(self._px(28))
            layout.addWidget(label)
            return

        max_visible = 26
        start = 0

        if current_index >= 0 and len(moves) > max_visible:
            start = max(0, current_index - 8)
            start = min(start, max(0, len(moves) - max_visible))

        end = min(len(moves), start + max_visible)

        if start > 0:
            dots = QLabel("...")
            dots.setObjectName("MutedLabel")
            dots.setFixedSize(self._px(24), self._px(28))
            dots.setAlignment(Qt.AlignCenter)
            layout.addWidget(dots)

        for idx in range(start, end):
            if partial_index == idx:
                state = "partial"
            elif active and idx == current_index:
                state = "current"
            elif idx < current_index:
                state = "done"
            else:
                state = "normal"

            layout.addWidget(MoveChip(moves[idx], state=state))

        if end < len(moves):
            dots = QLabel("...")
            dots.setObjectName("MutedLabel")
            dots.setFixedSize(self._px(24), self._px(28))
            dots.setAlignment(Qt.AlignCenter)
            layout.addWidget(dots)

    def _build_today_practice_card(self):
        card = QFrame()
        card.setObjectName("GlassCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(self._px(14), self._px(12), self._px(14), self._px(12))
        layout.setSpacing(self._px(8))

        top = QHBoxLayout()
        title = QLabel("今日练习")
        title.setObjectName("SectionTitle")
        self.btn_today_detail = QPushButton("详情")
        self.btn_today_detail.setObjectName("PrimaryButton")
        self.btn_today_detail.clicked.connect(self.show_timer_detail_page)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.btn_today_detail)
        layout.addLayout(top)

        self.today_rows = []
        for _ in range(6):
            row = QLabel("--")
            row.setObjectName("MutedLabel")
            row.setFixedHeight(self._px(22))
            row.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
            layout.addWidget(row)
            self.today_rows.append(row)
        layout.addStretch()
        return card

    def _build_timer_detail_page(self):
        page = QFrame()
        page.setObjectName("GlassCard")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(self._px(18), self._px(16), self._px(18), self._px(18))
        layout.setSpacing(self._px(12))

        top = QHBoxLayout()
        title = QLabel("今日练习详情")
        title.setObjectName("SectionTitle")
        self.btn_timer_detail_back = QPushButton("返回计时练习")
        self.btn_timer_detail_back.setObjectName("PrimaryButton")
        self.btn_timer_detail_back.clicked.connect(self.hide_timer_detail_page)
        top.addWidget(title)
        top.addStretch()
        top.addWidget(self.btn_timer_detail_back)
        layout.addLayout(top)

        placeholder = QLabel("详情功能待开发")
        placeholder.setObjectName("MutedLabel")
        placeholder.setAlignment(Qt.AlignCenter)
        layout.addWidget(placeholder, 1)
        return page

    def show_timer_detail_page(self):
        if not hasattr(self, "timer_detail_page"):
            return
        self.cube_stage.hide()
        self.panel_stack.hide()
        self.timer_detail_page.show()
        self.content_layout.setDirection(QBoxLayout.TopToBottom)
        self.content_layout.setStretchFactor(self.timer_detail_page, 1)

    def hide_timer_detail_page(self):
        if hasattr(self, "timer_detail_page"):
            self.timer_detail_page.hide()
        if hasattr(self, "cube_stage"):
            self.cube_stage.show()
        if hasattr(self, "panel_stack"):
            self.panel_stack.show()
        self.switch_page(PAGE_TIMER)

    def _format_ao5(self, records):
        values = []
        for rec in records[:5]:
            if rec.get("result") == "OK" and rec.get("time_ms") is not None:
                values.append(int(rec.get("time_ms")))
            else:
                values.append(None)
        if len(values) < 5:
            return "AO5  --"
        dnf_count = sum(v is None for v in values)
        if dnf_count >= 2:
            return "AO5  DNF"
        finite = [v for v in values if v is not None]
        if dnf_count == 1:
            finite.remove(min(finite))
            avg = sum(finite) / len(finite)
        else:
            finite.remove(min(finite))
            finite.remove(max(finite))
            avg = sum(finite) / len(finite)
        return f"AO5  {format_time_ms(round(avg))}"

    def _refresh_today_practice(self):
        if not hasattr(self, "today_rows"):
            return
        today_key = time.strftime("%Y-%m-%d", time.localtime())
        today_records = []
        for rec in self.timer_practice.history:
            ts = rec.get("timestamp")
            try:
                rec_day = time.strftime("%Y-%m-%d", time.localtime(int(ts)))
            except Exception:
                rec_day = today_key
            if rec_day == today_key:
                today_records.append(rec)
        records = today_records[:5]
        for i in range(5):
            if i < len(records):
                rec = records[i]
                time_text = rec.get("time_text", "DNF")
                result = rec.get("result", "")
                suffix = "" if result == "OK" else "  DNF"
                self.today_rows[i].setText(f"{i + 1}.  {time_text}{suffix}")
            else:
                self.today_rows[i].setText(f"{i + 1}.  --")
        self.today_rows[5].setText(self._format_ao5(records))

    def _build_timer_panel(self):
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self._px(10))

        timer_card = QFrame()
        timer_card.setObjectName("GlassCard")
        timer_layout = QVBoxLayout(timer_card)
        timer_layout.setContentsMargins(self._px(14), self._px(10), self._px(14), self._px(12))
        timer_layout.setSpacing(self._px(7))

        top_line = QHBoxLayout()
        self.timer_state_label = QLabel("计时练习")
        self.timer_state_label.setObjectName("SectionTitle")

        center_timer_box = QVBoxLayout()
        center_timer_box.setSpacing(self._px(2))
        self.timer_time_label = QLabel("00:00:000")
        self.timer_time_label.setObjectName("TimerLabel")
        self.timer_time_label.setAlignment(Qt.AlignCenter)

        self.summary_label = QLabel("步数 0    TPS 0.00")
        self.summary_label.setObjectName("MutedLabel")
        self.summary_label.setAlignment(Qt.AlignCenter)

        center_timer_box.addWidget(self.timer_time_label)
        center_timer_box.addWidget(self.summary_label)

        top_line.addWidget(self.timer_state_label)
        top_line.addStretch()
        top_line.addLayout(center_timer_box)
        top_line.addStretch()

        self.scramble_title_label = QLabel("打乱步骤")
        self.scramble_title_label.setObjectName("MutedLabel")
        self.scramble_chip_box, self.scramble_chips_layout = self._build_chip_box()

        controls = QHBoxLayout()
        self.btn_new_scramble = QPushButton("新打乱")
        self.btn_toggle_observe = QPushButton("观察：15秒")
        self.btn_give_up = QPushButton("放弃还原")
        self.btn_segment_stats = QPushButton("分步成绩")
        self.btn_pause_replay = QPushButton("暂停回放")
        self.btn_cancel_replay = QPushButton("取消回放")

        self.btn_new_scramble.setObjectName("PrimaryButton")
        self.btn_toggle_observe.setObjectName("PurpleButton")
        self.btn_give_up.setObjectName("DangerButton")
        self.btn_segment_stats.setObjectName("PrimaryButton")
        self.btn_pause_replay.setObjectName("PurpleButton")
        self.btn_cancel_replay.setObjectName("DangerButton")

        self.btn_pause_replay.hide()
        self.btn_cancel_replay.hide()

        controls.addStretch()
        controls.addWidget(self.btn_new_scramble)
        controls.addWidget(self.btn_toggle_observe)
        controls.addWidget(self.btn_give_up)
        controls.addWidget(self.btn_segment_stats)
        controls.addWidget(self.btn_pause_replay)
        controls.addWidget(self.btn_cancel_replay)
        controls.addStretch()

        self.timer_status_label = QLabel("准备开始")
        self.timer_status_label.setObjectName("MutedLabel")

        self.cfop_table = QTableWidget(8, 4)
        self.cfop_table.setObjectName("StatsTable")
        self.cfop_table.setHorizontalHeaderLabels(["阶段", "时间", "步数", "TPS"])
        self.cfop_table.verticalHeader().setVisible(False)
        self.cfop_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cfop_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.cfop_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.cfop_table.hide()

        timer_layout.addLayout(top_line)
        # 不再显示“打乱步骤”标题与底部状态提示，避免占用空间。
        self.scramble_title_label.hide()
        self.timer_status_label.hide()
        timer_layout.addWidget(self.scramble_chip_box)
        timer_layout.addLayout(controls)
        timer_layout.addWidget(self.cfop_table)

        layout.addWidget(timer_card, 1)

        history_card = QFrame()
        self.history_card = history_card
        history_card.setObjectName("GlassCard")
        history_card.setFixedWidth(self._px(190))

        history_layout = QVBoxLayout(history_card)
        history_layout.setContentsMargins(self._px(14), self._px(12), self._px(14), self._px(12))
        history_layout.setSpacing(self._px(8))

        history_top = QHBoxLayout()
        history_title = QLabel("历史记录")
        history_title.setObjectName("SectionTitle")
        self.btn_replay = QPushButton("回放")
        self.btn_replay.setObjectName("PrimaryButton")
        self.btn_replay.clicked.connect(self.replay_selected_history)

        history_top.addWidget(history_title)
        history_top.addStretch()
        history_top.addWidget(self.btn_replay)

        self.timer_history_list = QListWidget()
        self.timer_history_list.setObjectName("HistoryList")
        self.timer_history_list.itemDoubleClicked.connect(lambda _item: self.replay_selected_history())

        history_layout.addLayout(history_top)
        history_layout.addWidget(self.timer_history_list, 1)

        layout.addWidget(history_card)
        self.panel_stack.addWidget(panel)

        self.btn_new_scramble.clicked.connect(self.new_scramble)
        self.btn_toggle_observe.clicked.connect(self.toggle_observation)
        self.btn_give_up.clicked.connect(self.give_up_timer)
        self.btn_segment_stats.installEventFilter(self)
        self.btn_segment_stats.clicked.connect(self.show_segment_stats_popup)
        self.btn_pause_replay.clicked.connect(self.toggle_replay_pause)
        self.btn_cancel_replay.clicked.connect(self.cancel_replay)
        self._refresh_timer_ui(force=True)

    def _build_formula_practice_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self._px(10))

        card = QFrame()
        card.setObjectName("GlassCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(self._px(18), self._px(14), self._px(18), self._px(14))
        card_layout.setSpacing(self._px(10))

        title_row = QHBoxLayout()
        title = QLabel("公式练习 / 公式库")
        title.setObjectName("SectionTitle")
        self.formula_practice_status = QLabel("已导入 CFOP 公式库：F2L 仅取 Front Right，OLL / PLL 保留多种解法。")
        self.formula_practice_status.setObjectName("MutedLabel")
        self.formula_practice_status.setWordWrap(True)
        title_row.addWidget(title)
        title_row.addStretch()

        method_row = QHBoxLayout()
        method_row.setSpacing(self._px(8))
        self.formula_method_buttons = []
        for name in ["层先法", "CFOP", "桥式", "WVLS", "自定义公式"]:
            btn = QPushButton(name)
            btn.setObjectName("PrimaryButton" if name == "CFOP" else "MoveButton")
            btn.setMinimumHeight(self._px(34))
            btn.clicked.connect(lambda checked=False, n=name: self._select_formula_practice_placeholder(n))
            method_row.addWidget(btn)
            self.formula_method_buttons.append(btn)
        method_row.addStretch()

        filter_row = QHBoxLayout()
        filter_row.setSpacing(self._px(8))
        category_label = QLabel("分类")
        category_label.setObjectName("MutedLabel")
        self.formula_category_combo = QComboBox()
        self.formula_category_combo.setObjectName("ResolutionCombo")
        for item in ["全部", "F2L", "OLL", "PLL"]:
            self.formula_category_combo.addItem(item, item)
        self.formula_category_combo.currentIndexChanged.connect(self._refresh_formula_case_table)

        orientation_label = QLabel("预览朝向")
        orientation_label.setObjectName("MutedLabel")
        self.formula_orientation_combo = QComboBox()
        self.formula_orientation_combo.setObjectName("ResolutionCombo")
        self.formula_orientation_combo.setFixedWidth(self._px(128))
        for text, preset in self.cube_orientation_options:
            self.formula_orientation_combo.addItem(text, preset)
        self.formula_orientation_combo.setCurrentIndex(0)
        self.formula_orientation_combo.currentIndexChanged.connect(self.change_formula_practice_orientation)

        search_label = QLabel("查询")
        search_label.setObjectName("MutedLabel")
        self.formula_search_edit = QLineEdit()
        self.formula_search_edit.setObjectName("ResolutionCombo")
        self.formula_search_edit.setPlaceholderText("编号 / 名称 / 解法")
        self.formula_search_edit.setFixedWidth(self._px(190))
        self.formula_search_edit.textChanged.connect(self._refresh_formula_case_table)

        self.btn_formula_preview_inverse = QPushButton("刷新预览")
        self.btn_formula_preview_inverse.setObjectName("PrimaryButton")
        self.btn_formula_preview_inverse.clicked.connect(self.preview_selected_formula_inverse)
        self.btn_formula_reset_preview = QPushButton("重置预览")
        self.btn_formula_reset_preview.setObjectName("MoveButton")
        self.btn_formula_reset_preview.clicked.connect(self.reset_formula_preview_cube)
        self.btn_formula_start_training = QPushButton("开始训练选中")
        self.btn_formula_start_training.setObjectName("PrimaryButton")
        self.btn_formula_start_training.clicked.connect(self.start_formula_training_selected)
        filter_row.addWidget(category_label)
        filter_row.addWidget(self.formula_category_combo)
        filter_row.addSpacing(self._px(10))
        filter_row.addWidget(orientation_label)
        filter_row.addWidget(self.formula_orientation_combo)
        filter_row.addSpacing(self._px(10))
        filter_row.addWidget(search_label)
        filter_row.addWidget(self.formula_search_edit)
        filter_row.addStretch()
        filter_row.addWidget(self.btn_formula_preview_inverse)
        filter_row.addWidget(self.btn_formula_reset_preview)
        filter_row.addWidget(self.btn_formula_start_training)

        content_row = QHBoxLayout()
        content_row.setSpacing(self._px(10))

        self.formula_case_table = QTableWidget(0, 8)
        self.formula_case_table.setObjectName("HistoryTable")
        self.formula_case_table.setHorizontalHeaderLabels(["分类", "编号", "位置/名称", "预览图", "俯视图", "解法数", "练习次数", "平均耗时"])
        self.formula_case_table.verticalHeader().setVisible(False)
        self.formula_case_table.verticalHeader().setDefaultSectionSize(self._px(120))
        self.formula_case_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.formula_case_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.formula_case_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.formula_case_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.formula_case_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.formula_case_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.formula_case_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Fixed)
        self.formula_case_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Fixed)
        self.formula_case_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.formula_case_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.formula_case_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.formula_case_table.setColumnWidth(2, self._px(180))
        self.formula_case_table.setColumnWidth(3, self._px(104))
        self.formula_case_table.setColumnWidth(4, self._px(104))
        self.formula_case_table.setMinimumWidth(self._px(620))
        self.formula_case_table.setMaximumWidth(16777215)
        self.formula_case_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.formula_case_table.currentCellChanged.connect(lambda *_: self._on_formula_case_selected())

        self.formula_preview_hint = QLabel("当前表格已嵌入每个公式的首个解法预览图；点击条目后会同步显示立体预览和俯视预览。")
        self.formula_preview_hint.setObjectName("MutedLabel")
        self.formula_preview_hint.setWordWrap(True)
        self.formula_preview_cube = FormulaPreviewImageWidget()
        self.formula_top_preview = FormulaTopPreviewWidget()

        right_box = QFrame()
        right_box.setObjectName("ManualBox")
        right_layout = QVBoxLayout(right_box)
        right_layout.setContentsMargins(self._px(12), self._px(10), self._px(12), self._px(10))
        right_layout.setSpacing(self._px(8))
        self.formula_case_title = QLabel("请选择一个公式 case")
        self.formula_case_title.setObjectName("SectionTitle")
        self.formula_case_meta = QLabel("请选择右侧表格中的公式；支持新增、编辑、删除和查询。")
        self.formula_case_meta.setObjectName("MutedLabel")
        self.formula_case_meta.setWordWrap(True)
        self.formula_algorithm_list = QListWidget()
        self.formula_algorithm_list.setObjectName("HistoryList")
        self.formula_algorithm_list.currentRowChanged.connect(lambda _row: self._on_formula_algorithm_selected(auto_preview=True))

        self.formula_training_timer_label = QLabel("训练计时 00:00:000")
        self.formula_training_timer_label.setObjectName("SectionTitle")
        self.formula_training_timer_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.formula_training_timer_label.setStyleSheet("font-size: 34px; font-weight: 900; letter-spacing: 1px; color: #0F2747;")
        self.formula_training_info_label = QLabel("可按住 Ctrl/Shift 多选公式，点击“开始训练选中”。训练默认使用每条公式的第 1 条解法。")
        self.formula_training_info_label.setObjectName("MutedLabel")
        self.formula_training_info_label.setWordWrap(True)
        self.formula_training_chip_box, self.formula_training_chips_layout = self._build_chip_box()
        self.formula_training_chip_box.setMinimumHeight(self._px(72))
        self.formula_training_chip_box.setMaximumHeight(self._px(130))
        self.formula_training_chip_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        crud_row = QHBoxLayout()
        self.btn_formula_add = QPushButton("新增")
        self.btn_formula_add.setObjectName("MoveButton")
        self.btn_formula_add.clicked.connect(self.add_formula_case)
        self.btn_formula_edit = QPushButton("编辑")
        self.btn_formula_edit.setObjectName("MoveButton")
        self.btn_formula_edit.clicked.connect(self.edit_formula_case)
        self.btn_formula_delete = QPushButton("删除")
        self.btn_formula_delete.setObjectName("MoveButton")
        self.btn_formula_delete.clicked.connect(self.delete_formula_case)
        crud_row.addWidget(self.btn_formula_add)
        crud_row.addWidget(self.btn_formula_edit)
        crud_row.addWidget(self.btn_formula_delete)
        crud_row.addStretch()

        preview_row = QHBoxLayout()
        preview_row.setSpacing(self._px(8))

        preview_left = QFrame()
        preview_left.setObjectName("ManualBox")
        preview_left_layout = QVBoxLayout(preview_left)
        preview_left_layout.setContentsMargins(self._px(8), self._px(8), self._px(8), self._px(8))
        preview_left_layout.setSpacing(self._px(4))
        preview_left_title = QLabel("立体预览")
        preview_left_title.setObjectName("MutedLabel")
        preview_left_layout.addWidget(preview_left_title)
        preview_left_layout.addWidget(self.formula_preview_cube, 1)

        preview_right = QFrame()
        preview_right.setObjectName("ManualBox")
        preview_right_layout = QVBoxLayout(preview_right)
        preview_right_layout.setContentsMargins(self._px(8), self._px(8), self._px(8), self._px(8))
        preview_right_layout.setSpacing(self._px(4))
        preview_right_title = QLabel("俯视预览")
        preview_right_title.setObjectName("MutedLabel")
        preview_right_layout.addWidget(preview_right_title)
        preview_right_layout.addWidget(self.formula_top_preview, 1)

        preview_row.addWidget(preview_left, 1)
        preview_row.addWidget(preview_right, 1)

        right_layout.addWidget(self.formula_preview_hint)
        right_layout.addLayout(preview_row)
        right_layout.addWidget(self.formula_case_title)
        right_layout.addWidget(self.formula_case_meta)
        right_layout.addWidget(self.formula_training_timer_label)
        right_layout.addWidget(self.formula_training_info_label)
        right_layout.addWidget(self.formula_training_chip_box, 0)
        right_layout.addWidget(self.formula_algorithm_list, 1)
        right_layout.addLayout(crud_row)

        # 解法区放到左侧 3D 魔方下方：公式练习页显示，其它页面隐藏。
        self.formula_solution_box = right_box
        self.formula_solution_box.setMinimumHeight(self._px(250))
        self.formula_solution_box.setMaximumHeight(self._px(360))
        self.formula_solution_box.hide()
        if hasattr(self, "cube_stage_layout"):
            self.cube_stage_layout.addWidget(self.formula_solution_box, 0)

        # 训练页专用预览区：开始训练后放到左侧 3D 魔方下方，避免公式库界面拥挤。
        self.formula_training_preview_cube = FormulaPreviewImageWidget()
        self.formula_training_top_preview = FormulaTopPreviewWidget()
        self.formula_training_preview_box = QFrame()
        self.formula_training_preview_box.setObjectName("ManualBox")
        training_preview_layout = QHBoxLayout(self.formula_training_preview_box)
        training_preview_layout.setContentsMargins(self._px(12), self._px(10), self._px(12), self._px(10))
        training_preview_layout.setSpacing(self._px(10))

        training_preview_left = QFrame()
        training_preview_left.setObjectName("ManualBox")
        training_preview_left_layout = QVBoxLayout(training_preview_left)
        training_preview_left_layout.setContentsMargins(self._px(8), self._px(8), self._px(8), self._px(8))
        training_preview_left_layout.setSpacing(self._px(4))
        training_preview_left_title = QLabel("立体预览")
        training_preview_left_title.setObjectName("MutedLabel")
        training_preview_left_layout.addWidget(training_preview_left_title)
        training_preview_left_layout.addWidget(self.formula_training_preview_cube, 1)

        training_preview_right = QFrame()
        training_preview_right.setObjectName("ManualBox")
        training_preview_right_layout = QVBoxLayout(training_preview_right)
        training_preview_right_layout.setContentsMargins(self._px(8), self._px(8), self._px(8), self._px(8))
        training_preview_right_layout.setSpacing(self._px(4))
        training_preview_right_title = QLabel("俯视预览")
        training_preview_right_title.setObjectName("MutedLabel")
        training_preview_right_layout.addWidget(training_preview_right_title)
        training_preview_right_layout.addWidget(self.formula_training_top_preview, 1)

        training_preview_layout.addWidget(training_preview_left, 1)
        training_preview_layout.addWidget(training_preview_right, 1)
        self.formula_training_preview_box.setMinimumHeight(self._px(190))
        self.formula_training_preview_box.setMaximumHeight(self._px(260))
        self.formula_training_preview_box.hide()
        if hasattr(self, "cube_stage_layout"):
            self.cube_stage_layout.addWidget(self.formula_training_preview_box, 0)

        # 右侧绿色区域只保留公式表格。
        content_row.addWidget(self.formula_case_table, 1)

        card_layout.addLayout(title_row)
        card_layout.addWidget(self.formula_practice_status)
        card_layout.addLayout(method_row)
        card_layout.addLayout(filter_row)
        card_layout.addLayout(content_row, 1)

        layout.addWidget(card, 1)
        self.panel_stack.addWidget(panel)
        self._refresh_formula_case_table()

    def _build_formula_training_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self._px(10))

        card = QFrame()
        card.setObjectName("GlassCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(self._px(20), self._px(16), self._px(20), self._px(16))
        card_layout.setSpacing(self._px(12))

        title_row = QHBoxLayout()
        title = QLabel("公式训练")
        title.setObjectName("SectionTitle")
        self.formula_training_page_status = QLabel("选择公式后开始训练。每条公式会一直重复训练，直到手动点击下一条。")
        self.formula_training_page_status.setObjectName("MutedLabel")
        self.formula_training_page_status.setWordWrap(True)
        title_row.addWidget(title)
        title_row.addStretch()

        self.btn_formula_training_prev = QPushButton("上一条")
        self.btn_formula_training_prev.setObjectName("MoveButton")
        self.btn_formula_training_prev.clicked.connect(self.prev_formula_training_case)
        self.btn_formula_training_next = QPushButton("下一条")
        self.btn_formula_training_next.setObjectName("PrimaryButton")
        self.btn_formula_training_next.clicked.connect(self.next_formula_training_case)
        self.btn_formula_training_back = QPushButton("返回公式库")
        self.btn_formula_training_back.setObjectName("MoveButton")
        self.btn_formula_training_back.clicked.connect(self.back_to_formula_library)
        self.btn_formula_training_guide_toggle = QPushButton("引导箭头：开")
        self.btn_formula_training_guide_toggle.setObjectName("PrimaryButton")
        self.btn_formula_training_guide_toggle.setCheckable(True)
        self.btn_formula_training_guide_toggle.setChecked(True)
        self.btn_formula_training_guide_toggle.clicked.connect(self.toggle_formula_training_guide_arrows)
        self.btn_formula_training_stop = QPushButton("结束训练")
        self.btn_formula_training_stop.setObjectName("MoveButton")
        self.btn_formula_training_stop.clicked.connect(self.stop_formula_training)
        for btn in [self.btn_formula_training_prev, self.btn_formula_training_next, self.btn_formula_training_back, self.btn_formula_training_guide_toggle, self.btn_formula_training_stop]:
            title_row.addWidget(btn)

        info_card = QFrame()
        info_card.setObjectName("ManualBox")
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(self._px(14), self._px(12), self._px(14), self._px(12))
        info_layout.setSpacing(self._px(8))

        self.formula_training_case_label = QLabel("当前公式：--")
        self.formula_training_case_label.setObjectName("SectionTitle")
        self.formula_training_stats_label = QLabel("练习次数：0；平均耗时：--")
        self.formula_training_stats_label.setObjectName("MutedLabel")
        self.formula_training_stats_label.setWordWrap(True)
        info_layout.addWidget(self.formula_training_case_label)
        info_layout.addWidget(self.formula_training_stats_label)
        info_layout.addWidget(self.formula_training_timer_label)
        info_layout.addWidget(self.formula_training_info_label)

        step_card = QFrame()
        step_card.setObjectName("ManualBox")
        step_layout = QVBoxLayout(step_card)
        step_layout.setContentsMargins(self._px(14), self._px(12), self._px(14), self._px(12))
        step_layout.setSpacing(self._px(8))
        step_title = QLabel("当前解法步骤")
        step_title.setObjectName("SectionTitle")
        step_layout.addWidget(step_title)
        # 步骤区只需要显示当前公式步骤，不要占满右侧剩余空间。
        # 固定为紧凑高度，避免下面出现大片空白。
        self.formula_training_chip_box.setMinimumHeight(self._px(76))
        self.formula_training_chip_box.setMaximumHeight(self._px(118))
        self.formula_training_chip_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        step_layout.addWidget(self.formula_training_chip_box, 0)

        hint = QLabel("规则：默认训练每条公式的第 1 条解法；完成第 1 步后开始计时；中途做错会清空本次计时并从该公式第 1 步重来；完整正确完成一次后，该公式练习次数 +1，并更新平均耗时。完成后会自动重置同一公式，直到你手动点击“下一条”。")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)

        card_layout.addLayout(title_row)
        card_layout.addWidget(self.formula_training_page_status)
        card_layout.addWidget(info_card, 0)
        step_card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        card_layout.addWidget(step_card, 0)
        card_layout.addWidget(hint)
        card_layout.addStretch(1)

        layout.addWidget(card, 1)
        self.panel_stack.addWidget(panel)

    def _formula_practice_stats_path(self):
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, "formulas", "formula_practice_stats.json")

    def _load_formula_practice_stats(self):
        path = self._formula_practice_stats_path()
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_formula_practice_stats(self):
        path = self._formula_practice_stats_path()
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self.formula_practice_stats, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            if hasattr(self, "formula_practice_status"):
                self.formula_practice_status.setText(f"练习统计保存失败：{exc}")

    def _formula_case_key(self, case):
        case = case or {}
        return "|".join([
            str(case.get("category", "")),
            str(case.get("id") or case.get("case_id") or case.get("name") or ""),
        ])

    def _formula_stats_for_case(self, case):
        key = self._formula_case_key(case)
        stats = self.formula_practice_stats.get(key)
        if not isinstance(stats, dict):
            return {"count": 0, "total_ms": 0}
        return {"count": int(stats.get("count", 0)), "total_ms": int(stats.get("total_ms", 0))}

    def _record_formula_practice_time(self, case, elapsed_ms):
        key = self._formula_case_key(case)
        stats = self._formula_stats_for_case(case)
        stats["count"] = int(stats.get("count", 0)) + 1
        stats["total_ms"] = int(stats.get("total_ms", 0)) + max(0, int(elapsed_ms))
        self.formula_practice_stats[key] = stats
        self._save_formula_practice_stats()
        self._update_formula_stats_cells()

    def _update_formula_stats_cells(self):
        if not hasattr(self, "formula_case_table"):
            return
        for row, case in enumerate(getattr(self, "_formula_filtered_cases", [])):
            stats = self._formula_stats_for_case(case)
            count = int(stats.get("count", 0))
            avg_ms = int(stats.get("total_ms", 0) / count) if count else 0
            count_item = QTableWidgetItem(str(count))
            count_item.setTextAlignment(Qt.AlignCenter)
            avg_item = QTableWidgetItem(format_time_ms(avg_ms) if count else "—")
            avg_item.setTextAlignment(Qt.AlignCenter)
            self.formula_case_table.setItem(row, 6, count_item)
            self.formula_case_table.setItem(row, 7, avg_item)

    def _selected_formula_cases_for_training(self):
        if not hasattr(self, "formula_case_table"):
            return []
        rows = sorted({idx.row() for idx in self.formula_case_table.selectionModel().selectedRows()})
        if not rows and self.formula_case_table.currentRow() >= 0:
            rows = [self.formula_case_table.currentRow()]
        cases = []
        filtered = getattr(self, "_formula_filtered_cases", [])
        for row in rows:
            if 0 <= row < len(filtered) and filtered[row].get("algorithms"):
                cases.append(filtered[row])
        return cases

    def _formula_training_guide_move_label(self):
        if not getattr(self, "formula_training_guide_arrows_enabled", True):
            return None
        pending = list(getattr(self, "formula_training_pending_auto_moves", []) or [])
        if pending:
            return pending[0]
        guide = getattr(self, "formula_training_guide", None)
        if guide is not None and guide.active:
            return guide.current_move()
        return None

    def _apply_formula_training_guide_arrow(self):
        if not hasattr(self, "cube"):
            return
        if getattr(self, "panel_stack", None) is not None and self.panel_stack.currentIndex() != PAGE_FORMULA_TRAINING:
            return
        self.cube.set_guide_move(self._formula_training_guide_move_label())

    def toggle_formula_training_guide_arrows(self):
        enabled = True
        if hasattr(self, "btn_formula_training_guide_toggle"):
            enabled = bool(self.btn_formula_training_guide_toggle.isChecked())
        self.formula_training_guide_arrows_enabled = enabled
        if hasattr(self, "btn_formula_training_guide_toggle"):
            self.btn_formula_training_guide_toggle.setText("引导箭头：开" if enabled else "引导箭头：关")
            self.btn_formula_training_guide_toggle.setObjectName("PrimaryButton" if enabled else "MoveButton")
            self.btn_formula_training_guide_toggle.style().unpolish(self.btn_formula_training_guide_toggle)
            self.btn_formula_training_guide_toggle.style().polish(self.btn_formula_training_guide_toggle)
        if hasattr(self, "cube"):
            self.cube.set_guide_move(self._formula_training_guide_move_label() if enabled else None)
            self.cube.update()

    def start_formula_training_selected(self):
        if self._cube_has_pending_moves():
            self.formula_practice_status.setText("动作尚未完成，请稍后再开始公式训练。")
            return
        cases = self._selected_formula_cases_for_training()
        if not cases:
            QMessageBox.information(self, "提示", "请先在公式表格中选择至少一条含解法的公式。可按 Ctrl/Shift 批量选择。")
            return
        self.formula_training_cases = cases
        self.formula_training_case_index = 0
        self.formula_training_active = True
        self.switch_page(PAGE_FORMULA_TRAINING)
        self._load_formula_training_case(0)

    def stop_formula_training(self):
        self.formula_training_active = False
        self.formula_training_cases = []
        self.formula_training_case_index = 0
        self.formula_training_case = None
        self.formula_training_algorithm = None
        self.formula_training_started_at = None
        self.formula_training_elapsed_ms = 0
        self._last_formula_training_timer_bucket = None
        self.formula_training_initial_orientation = getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION)
        self.formula_training_current_orientation = self.formula_training_initial_orientation
        self.formula_training_guide.stop()
        self.formula_training_match_states = []
        self.formula_training_progress_completed = 0
        self.formula_training_progress_total = 0
        self.formula_training_progress_partial = False
        self.formula_training_pending_auto_moves = []
        self._last_formula_training_chip_key = None
        if hasattr(self, "cube"):
            self.cube.set_guide_move(None)
        self._refresh_formula_training_ui(force=True)
        if hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText("已停止公式训练。")
        if hasattr(self, "formula_training_preview_box"):
            self.formula_training_preview_box.hide()
        if hasattr(self, "formula_training_page_status"):
            self.formula_training_page_status.setText("已停止公式训练。")
        if hasattr(self, "panel_stack") and self.panel_stack.currentIndex() == PAGE_FORMULA_TRAINING:
            self.switch_page(PAGE_FORMULA_PRACTICE)

    def _update_formula_training_preview_widgets(self, data=None):
        if data is None:
            case = getattr(self, "formula_training_case", None)
            algorithm = getattr(self, "formula_training_algorithm", None)
            if not case or not algorithm:
                return
            try:
                data = self._build_formula_preview_data(case, algorithm)
            except Exception:
                return
        if hasattr(self, "formula_training_preview_cube"):
            self.formula_training_preview_cube.set_cube_state(data["preview_cubies"], data["preview_category"])
        if hasattr(self, "formula_training_top_preview"):
            self.formula_training_top_preview.set_cube_state(data["preview_cubies"], data["preview_category"])
        if hasattr(self, "formula_training_preview_box"):
            self.formula_training_preview_box.show()

    def _load_formula_training_case(self, index):
        if not (0 <= index < len(self.formula_training_cases)):
            self.stop_formula_training()
            return
        case = self.formula_training_cases[index]
        algorithms = case.get("algorithms", [])
        if not algorithms:
            self.formula_training_case_index += 1
            self._load_formula_training_case(self.formula_training_case_index)
            return
        algorithm = algorithms[0]
        try:
            data = self._build_formula_preview_data(case, algorithm)
            moves = data["moves"]
        except Exception as exc:
            self.formula_practice_status.setText(f"无法开始训练 {case.get('case_id', '')}：{exc}")
            return

        self.formula_training_case = case
        self.formula_training_algorithm = algorithm
        # 训练每轮都从“开始训练时选择的预览初始朝向”恢复。
        # data["inverse_end_orientation"] 正常应与 initial_orientation 一致，保留兜底但不重复赋值。
        self.formula_training_initial_orientation = data.get("inverse_end_orientation") or data.get("initial_orientation") or getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION)
        self.formula_training_current_orientation = self.formula_training_initial_orientation
        self.formula_training_started_at = None
        self.formula_training_elapsed_ms = 0
        self._last_formula_training_timer_bucket = None
        # 出错清空进度时，必须恢复到本条公式训练开始时的初始朝向，
        # 不能沿用出错前（例如 M / x / y 改变后）的当前朝向。
        self._reset_formula_training_orientation_to_initial()
        self.formula_training_guide.start(moves)
        self._reset_formula_training_match_states()
        self._last_formula_training_chip_key = None

        try:
            table_row = getattr(self, "_formula_filtered_cases", []).index(case)
            self.formula_case_table.selectRow(table_row)
            if hasattr(self, "formula_algorithm_list") and self.formula_algorithm_list.count() > 0:
                self.formula_algorithm_list.setCurrentRow(0)
        except Exception:
            pass

        if hasattr(self, "cube"):
            self.cube.cubies = data["preview_cubies"]
            self.cube.move_queue.clear()
            self.cube.active_move = None
            self.apply_formula_practice_view()
            self._apply_formula_training_guide_arrow()
            self.cube.update()
        if hasattr(self, "formula_preview_cube"):
            self.formula_preview_cube.set_cube_state(data["preview_cubies"], data["preview_category"])
        if hasattr(self, "formula_top_preview"):
            self.formula_top_preview.set_cube_state(data["preview_cubies"], data["preview_category"])
        if hasattr(self, "formula_preview_hint"):
            self.formula_preview_hint.hide()
        self._update_formula_training_preview_widgets(data)

        self._formula_training_auto_advance_rotations(reason="load")

        msg = (
            f"公式训练 {index + 1}/{len(self.formula_training_cases)}："
            f"{case.get('case_id', '')}，第 1 条解法。完成第 1 步后开始计时；中途出错会清空本次计时并从本公式重来。"
        )
        self.formula_practice_status.setText(msg)
        if hasattr(self, "formula_training_page_status"):
            self.formula_training_page_status.setText(msg)
        self._refresh_formula_training_ui(force=True)

    def _reset_formula_training_orientation_to_initial(self):
        self.formula_training_current_orientation = getattr(
            self,
            "formula_training_initial_orientation",
            getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION),
        )
        return self.formula_training_current_orientation

    def _restart_current_formula_training(self, reason):
        case = self.formula_training_case
        algorithm = self.formula_training_algorithm
        if not case or not algorithm:
            return
        try:
            data = self._build_formula_preview_data(case, algorithm)
            moves = data["moves"]
        except Exception as exc:
            self.formula_practice_status.setText(f"重置公式训练失败：{exc}")
            self.stop_formula_training()
            return
        self.formula_training_started_at = None
        self.formula_training_elapsed_ms = 0
        self._last_formula_training_timer_bucket = None
        self.formula_training_pending_auto_moves = []
        # 完成一次后自动重开同一公式，也要回到本轮训练初始朝向，
        # 再自动处理开头的 x/y/z，保证下一轮和第一次完全一致。
        self._reset_formula_training_orientation_to_initial()
        self.formula_training_guide.start(moves)
        self._reset_formula_training_match_states()
        self._last_formula_training_chip_key = None
        if hasattr(self, "cube"):
            self.cube.cubies = data["preview_cubies"]
            self.cube.move_queue.clear()
            self.cube.active_move = None
            self.apply_formula_practice_view()
            self._apply_formula_training_guide_arrow()
            self.cube.update()
        # 目标预览图只在切换公式时刷新；出错重练同一公式无需重绘两个预览控件。
        self._formula_training_auto_advance_rotations(reason="restart")
        msg = f"{reason}；本次计时已清空，进度和朝向已恢复到训练初始状态，请从第 1 步重新完成该公式。"
        self.formula_practice_status.setText(msg)
        if hasattr(self, "formula_training_page_status"):
            self.formula_training_page_status.setText(msg)
        self._refresh_formula_training_ui(force=True)

    def _formula_training_advance_orientation(self, formula_move):
        before = getattr(
            self,
            "formula_training_current_orientation",
            getattr(self, "formula_training_initial_orientation", getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION)),
        )
        after = self._orientation_after_move(before, formula_move)
        self.formula_training_current_orientation = after
        return before, after

    def _formula_training_current_move(self):
        guide = getattr(self, "formula_training_guide", None)
        if guide is None or not guide.active:
            return None
        return guide.current_move()

    def _set_formula_training_progress_from_states(self):
        states = getattr(self, "formula_training_match_states", []) or []
        if not states:
            self.formula_training_progress_completed = 0
            self.formula_training_progress_total = 0
            self.formula_training_progress_partial = False
            return

        best = None
        for state in states:
            pattern = state.get("pattern", [])
            group_index = int(state.get("group_index", 0) or 0)
            collected = set(state.get("collected", set()) or set())
            score = (group_index, len(collected), len(pattern))
            if best is None or score > best[0]:
                best = (score, pattern, group_index, collected)

        _, pattern, group_index, collected = best
        self.formula_training_progress_completed = max(0, group_index)
        self.formula_training_progress_total = max(0, len(pattern))
        self.formula_training_progress_partial = bool(collected)

    def _reset_formula_training_match_states(self):
        expected = self._formula_training_current_move()
        self.formula_training_match_states = []
        if expected:
            for pattern in self._debug_patterns_for_move(expected):
                self.formula_training_match_states.append({"pattern": pattern, "group_index": 0, "collected": set()})
        self._set_formula_training_progress_from_states()

    def _formula_training_feed_actual_move(self, actual):
        if not getattr(self, "formula_training_match_states", None):
            self._reset_formula_training_match_states()
        next_states = []
        completed = False
        progress_texts = []

        for state in self.formula_training_match_states:
            pattern = state["pattern"]
            group_index = state["group_index"]
            collected = set(state["collected"])
            if group_index >= len(pattern):
                completed = True
                continue

            group = pattern[group_index]
            if actual not in group or actual in collected:
                continue

            collected.add(actual)
            if collected == set(group):
                group_index += 1
                collected = set()
                if group_index >= len(pattern):
                    completed = True
                    continue

            progress_texts.append(f"{group_index + 1}/{len(pattern)}")
            next_states.append({"pattern": pattern, "group_index": group_index, "collected": collected})

        self.formula_training_match_states = next_states
        self._set_formula_training_progress_from_states()
        return completed, bool(next_states), ", ".join(sorted(set(progress_texts)))

    def _formula_training_expected_description(self, move=None):
        return self._debug_expected_description(move or self._formula_training_current_move())

    def _formula_training_is_auto_rotation_move(self, move):
        return self._debug_is_auto_rotation_move(move)

    def _formula_training_auto_advance_rotations(self, reason=""):
        """Auto-consume formula-level x/y/z for detection/orientation only.

        In training mode, cube rotations are special: the logical orientation must
        advance immediately (so the next real face turn is detected in the right
        orientation), but the 3D animation is deliberately deferred.  The pending
        x/y/z animations are flushed only after the next non-rotation formula step
        is completed correctly.  This keeps the guide arrow useful: users can see
        the required rotation before it visually happens.
        """
        if not getattr(self, "formula_training_active", False):
            return False
        guide = getattr(self, "formula_training_guide", None)
        if guide is None or not guide.active:
            return False

        pending = getattr(self, "formula_training_pending_auto_moves", [])
        changed = False
        while guide.active and guide.current_index < len(guide.solution_moves):
            move = guide.current_move()
            if not self._formula_training_is_auto_rotation_move(move):
                break
            pending.append(move)
            self._formula_training_advance_orientation(move)
            guide.current_index += 1
            changed = True
        self.formula_training_pending_auto_moves = pending

        if guide.current_index >= len(guide.solution_moves):
            self._finish_formula_training_once(auto_finished=True)
            return True

        if changed:
            self._reset_formula_training_match_states()
            if hasattr(self, "cube"):
                # Keep the arrow on the pending rotation first.  The actual cube
                # animation is not enqueued until the next letter step succeeds.
                self._apply_formula_training_guide_arrow()
            if hasattr(self, "formula_training_page_status"):
                pending_text = " ".join(move_text(m) for m in pending)
                self.formula_training_page_status.setText(
                    f"已逻辑处理整体旋转 {pending_text}；当前朝向 {self._orientation_text(getattr(self, 'formula_training_current_orientation', None))}；"
                    f"请执行下一步 {guide.current_index + 1}/{len(guide.solution_moves)}：{move_text(guide.current_move())}。旋转动画将在该步正确后播放。"
                )
        return False

    def _flush_formula_training_pending_auto_animations(self):
        pending = list(getattr(self, "formula_training_pending_auto_moves", []) or [])
        if pending and hasattr(self, "cube"):
            for move in pending:
                if move in MOVE_DEFS:
                    self.cube.enqueue_move(move)
        self.formula_training_pending_auto_moves = []
        return pending

    def _finish_formula_training_once(self, auto_finished=False):
        now = time.perf_counter()
        if self.formula_training_started_at is None:
            elapsed_ms = 0
        else:
            elapsed_ms = int((now - self.formula_training_started_at) * 1000)
        self.formula_training_started_at = None
        self.formula_training_elapsed_ms = max(0, elapsed_ms)

        finished_case = self.formula_training_case
        if finished_case:
            self._record_formula_practice_time(finished_case, self.formula_training_elapsed_ms)
            finished_time = format_time_ms(self.formula_training_elapsed_ms)
            count = self._formula_stats_for_case(finished_case).get("count", 0)
            msg = f"完成：{finished_case.get('case_id', '')} 用时 {finished_time}；累计练习 {count} 次。已重置同一公式，可继续练习，或手动点击下一条。"
        else:
            msg = "本次公式训练完成，已重置。"

        self.formula_training_last_finish_ms = self.formula_training_elapsed_ms
        self._formula_stats_dirty = True
        self._refresh_formula_training_timer_text(force=True)
        self._refresh_formula_training_stats_text()
        if hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText(msg)
        if hasattr(self, "formula_training_page_status"):
            self.formula_training_page_status.setText(msg)

        # 立即重置当前公式，避免完成后全局 UI 定时器继续刷新计时。
        # 这里不使用 QTimer.singleShot；否则最后一步后用户会看到计时继续跳动一小段时间。
        self._reload_current_formula_training_after_finish(msg)

    def _reload_current_formula_training_after_finish(self, status_msg):
        case = self.formula_training_case
        algorithm = self.formula_training_algorithm
        if not case or not algorithm:
            return
        try:
            data = self._build_formula_preview_data(case, algorithm)
            moves = data["moves"]
        except Exception as exc:
            self.formula_practice_status.setText(f"重置公式训练失败：{exc}")
            self.stop_formula_training()
            return

        self.formula_training_started_at = None
        self.formula_training_pending_auto_moves = []
        # 完成一次后重新进入同一条公式时，必须先把训练朝向恢复到
        # 本条公式的初始朝向。否则第一遍中由 M/r/x/y/z 等步骤改变过
        # 的 current_orientation 会残留到第二遍，导致第二遍开头的自动
        # 旋转或后续 BLE 面转映射基于错误朝向判断。
        self._reset_formula_training_orientation_to_initial()
        # 保留刚完成的用时，直到下一遍真正开始计时再清零。
        # 这样完成后用户能看到上一遍公式花了多久。
        self._last_formula_training_timer_bucket = None
        self.formula_training_guide.start(moves)
        self._reset_formula_training_match_states()
        self._last_formula_training_chip_key = None
        if hasattr(self, "cube"):
            self.cube.cubies = data["preview_cubies"]
            self.cube.move_queue.clear()
            self.cube.active_move = None
            self.apply_formula_practice_view()
            self._apply_formula_training_guide_arrow()
            self.cube.update()
        # 目标预览图没有变化，避免每次完成后额外重绘。
        self._formula_training_auto_advance_rotations(reason="finish_reload")
        if hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText(status_msg)
        if hasattr(self, "formula_training_page_status"):
            self.formula_training_page_status.setText(status_msg)
        self._refresh_formula_training_ui(force=True)

    def _formula_training_should_animate_actual_halfstep(self, expected):
        """Only animate raw user inputs before a step is complete for simple outer-face 2-turns.

        For wide moves (r/u), slice moves (M/E/S), and cube rotations, the solver
        animation must run the formula move itself because these moves change the
        displayed cube state/orientation differently from their GAN primitive
        detection signals.  Animating the primitive signals (for example L' + R
        for M, or L for r) makes PLL/OLL training visually diverge from the
        solver.
        """
        try:
            expected = normalize_move(expected)
        except Exception:
            expected = str(expected or "")
        face, suffix = _split_move_suffix(expected)
        return suffix == "2" and face in ("U", "D", "F", "B", "L", "R")

    def _handle_formula_practice_move(self, label):
        if not getattr(self, "formula_training_active", False) or not self.formula_training_guide.solution_moves:
            self.cube.enqueue_move(label)
            if hasattr(self, "formula_practice_status"):
                self.formula_practice_status.setText(f"未开始训练，已手动执行 {move_text(label)}。")
            return

        expected = self._formula_training_current_move()
        if not expected:
            self.cube.enqueue_move(label)
            return

        # 和“调试板块-多步调试”一致：M/E/S、宽层、小写、U2/M2 等公式按钮
        # 先拆成智能魔方实际可上报的底层面转信号，再用模式匹配判断是否完成当前公式步骤。
        try:
            signals = self._debug_button_simulation_moves(label)
        except Exception:
            try:
                signals = [normalize_move(label)]
            except Exception:
                signals = [str(label or "")]

        completed = False
        last_actual = None
        pending_auto_moves = list(getattr(self, "formula_training_pending_auto_moves", []) or [])
        animate_actual_halfstep = self._formula_training_should_animate_actual_halfstep(expected) and not pending_auto_moves
        for sig in signals:
            try:
                actual = normalize_move(sig)
            except Exception:
                actual = str(sig or "")
            last_actual = actual
            completed, still_possible, inner_progress = self._formula_training_feed_actual_move(actual)
            if completed or still_possible:
                # Only simple outer-face 2-turns (U2/R2/...) are animated by
                # half-step actual inputs.  For r/u/M/E/S/x/y/z, wait until the
                # full formula step is matched and enqueue the formula move
                # itself, exactly like the solver/debug animation logic.
                if animate_actual_halfstep and hasattr(self, "cube") and actual in MOVE_DEFS:
                    self.cube.enqueue_move(actual)
            if completed:
                break
            if still_possible:
                if hasattr(self, "formula_training_page_status"):
                    self.formula_training_page_status.setText(
                        f"检测中：{move_text(expected)} 已匹配部分输入 {move_text(actual)}，请连续完成剩余动作。"
                    )
                continue

            self._restart_current_formula_training(
                f"出错：当前应匹配 {move_text(expected)}，实际 {move_text(actual)}；需要：{self._formula_training_expected_description(expected)}"
            )
            return

        if not completed:
            self._refresh_formula_training_ui(force=False)
            return

        # 完整匹配后，先把之前自动消耗的 x/y/z 动画补播出来，
        # 再播放当前公式步骤本身。这样显示顺序与求解器动画逻辑一致。
        pending_played = self._flush_formula_training_pending_auto_animations()
        if (pending_played or not animate_actual_halfstep) and hasattr(self, "cube") and expected in MOVE_DEFS:
            self.cube.enqueue_move(expected)

        self._formula_training_advance_orientation(expected)
        guide = self.formula_training_guide
        guide.current_index += 1
        guide.pending_double_move = None
        guide.pending_double_dir = None

        if self.formula_training_started_at is None and guide.current_index >= 1 and guide.current_index < len(guide.solution_moves):
            self.formula_training_started_at = time.perf_counter()
            self.formula_training_elapsed_ms = 0
            self._last_formula_training_timer_bucket = None
            self._refresh_formula_training_timer_text(force=True)

        if guide.current_index >= len(guide.solution_moves):
            guide.active = False
            guide.finished = True
            self._finish_formula_training_once()
            return

        if self._formula_training_auto_advance_rotations(reason="after_user_move"):
            return
        if self.formula_training_guide.active:
            self._reset_formula_training_match_states()
            if hasattr(self, "cube"):
                self._apply_formula_training_guide_arrow()
            if hasattr(self, "formula_training_page_status"):
                self.formula_training_page_status.setText(
                    f"正确：当前朝向 {self._orientation_text(getattr(self, 'formula_training_current_orientation', None))}；下一步 {move_text(self.formula_training_guide.current_move())}；检测模式：{self._formula_training_expected_description()}"
                )
        self._refresh_formula_training_ui(force=False)

    def next_formula_training_case(self):
        if not self.formula_training_cases:
            return
        self.formula_training_case_index = min(len(self.formula_training_cases) - 1, self.formula_training_case_index + 1)
        self.formula_training_active = True
        self._load_formula_training_case(self.formula_training_case_index)

    def prev_formula_training_case(self):
        if not self.formula_training_cases:
            return
        self.formula_training_case_index = max(0, self.formula_training_case_index - 1)
        self.formula_training_active = True
        self._load_formula_training_case(self.formula_training_case_index)

    def back_to_formula_library(self):
        self.switch_page(PAGE_FORMULA_PRACTICE)
        if getattr(self, "_formula_stats_dirty", False):
            self._formula_stats_dirty = False
            self._refresh_formula_case_table()

    def _refresh_formula_training_timer_text(self, force=False):
        if not hasattr(self, "formula_training_timer_label"):
            return
        elapsed = int(getattr(self, "formula_training_elapsed_ms", 0) or 0)
        # 训练计时不需要 60 FPS 重绘；按 50ms 分桶即可看起来流畅，
        # 同时避免和左侧 3D 魔方动画抢 GUI 主线程。
        bucket = elapsed // 50
        if force or bucket != getattr(self, "_last_formula_training_timer_bucket", None):
            self._last_formula_training_timer_bucket = bucket
            self.formula_training_timer_label.setText(f"训练计时 {format_time_ms(elapsed)}")

    def _refresh_formula_training_stats_text(self):
        if not getattr(self, "formula_training_case", None):
            if hasattr(self, "formula_training_case_label"):
                self.formula_training_case_label.setText("当前公式：--")
            if hasattr(self, "formula_training_stats_label"):
                self.formula_training_stats_label.setText("练习次数：0；平均耗时：--")
            return
        case = self.formula_training_case
        if hasattr(self, "formula_training_case_label"):
            self.formula_training_case_label.setText(f"当前公式：{case.get('case_id', '')} {case.get('slot') or case.get('name', '')}")
        if hasattr(self, "formula_training_stats_label"):
            stats = self._formula_stats_for_case(case)
            count = int(stats.get("count", 0) or 0)
            avg_ms = int(stats.get("total_ms", 0) / count) if count else 0
            self.formula_training_stats_label.setText(f"练习次数：{count}；平均耗时：{format_time_ms(avg_ms) if count else '—'}")

    def _refresh_formula_training_ui(self, force=False):
        self._refresh_formula_training_timer_text(force=force)
        self._refresh_formula_training_stats_text()
        guide = getattr(self, "formula_training_guide", None)
        if not hasattr(self, "formula_training_chips_layout") or guide is None:
            return
        partial_index = guide.display_partial_index()
        # 公式训练使用自定义的多步匹配状态，不完全走 SolutionGuide 的
        # pending_double_move。U2/R2 这类双转在完成第一下后，状态通常是
        # progress_completed=1、progress_partial=False，因此只看
        # display_partial_index() 会仍然显示纯蓝。这里把“当前公式步骤内部
        # 已完成一部分”也映射到当前 chip 的半蓝半绿状态。
        inner_total = int(getattr(self, "formula_training_progress_total", 0) or 0)
        inner_completed = int(getattr(self, "formula_training_progress_completed", 0) or 0)
        inner_partial = bool(getattr(self, "formula_training_progress_partial", False))
        if (
            partial_index < 0
            and guide.active
            and guide.current_index < len(guide.solution_moves)
            and inner_total > 1
            and (inner_completed > 0 or inner_partial)
        ):
            partial_index = guide.current_index

        chip_key = (
            tuple(guide.solution_moves),
            guide.current_index,
            guide.active,
            partial_index,
            inner_completed,
            inner_total,
            inner_partial,
            getattr(self, "formula_training_initial_orientation", None),
            getattr(self, "formula_training_current_orientation", None),
        )
        if force or chip_key != self._last_formula_training_chip_key:
            self._last_formula_training_chip_key = chip_key
            self._set_chip_row(
                self.formula_training_chips_layout,
                guide.solution_moves,
                current_index=guide.current_index,
                active=guide.active,
                partial_index=partial_index,
                empty_text="选择公式后点击“开始训练选中”",
            )
        if hasattr(self, "formula_training_info_label"):
            if getattr(self, "formula_training_active", False) and self.formula_training_case:
                count = len(self.formula_training_cases) or 1
                self.formula_training_info_label.setText(
                    f"训练进度 {self.formula_training_case_index + 1}/{count}："
                    f"{self.formula_training_case.get('case_id', '')} / {move_text(self.formula_training_algorithm)}；"
                    f"当前朝向 {self._orientation_text(getattr(self, 'formula_training_current_orientation', None))}；"
                    f"当前步骤 {min(guide.current_index + 1, len(guide.solution_moves))}/{len(guide.solution_moves)}。"
                )
            else:
                self.formula_training_info_label.setText("可按住 Ctrl/Shift 多选公式，点击“开始训练选中”。训练默认使用每条公式的第 1 条解法。")

    def _select_formula_practice_placeholder(self, name):
        if name == "CFOP":
            if hasattr(self, "formula_practice_status"):
                self.formula_practice_status.setText("CFOP 公式库已导入：F2L 仅取 Front Right，OLL / PLL 每个 case 保留多种解法。请选择 case 和解法后进行逆向预览。")
            if hasattr(self, "formula_category_combo"):
                self.formula_category_combo.setCurrentIndex(0)
            return
        if hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText(f"{name}：入口已预留，公式库与练习流程暂未开发。")

    def _refresh_formula_case_table(self, *args):
        if not hasattr(self, "formula_case_table"):
            return
        category = self.formula_category_combo.currentData() if hasattr(self, "formula_category_combo") else "全部"
        query = ""
        if hasattr(self, "formula_search_edit"):
            query = self.formula_search_edit.text().strip().lower()

        cases = list(getattr(self, "cfop_formula_library", []))
        if category and category != "全部":
            cases = [item for item in cases if item.get("category") == category]
        if query:
            def match(item):
                haystack = " ".join([
                    str(item.get("category", "")),
                    str(item.get("case_id", "")),
                    str(item.get("name", "")),
                    str(item.get("slot", "")),
                    " ".join(str(a) for a in item.get("algorithms", [])),
                ]).lower()
                return query in haystack
            cases = [item for item in cases if match(item)]

        select_id = getattr(self, "_pending_formula_select_id", None)
        self._pending_formula_select_id = None
        self._formula_filtered_cases = cases

        if hasattr(self, "formula_practice_status"):
            total = len(getattr(self, "cfop_formula_library", []))
            self.formula_practice_status.setText(f"当前显示 {len(cases)} / {total} 个公式；可 Ctrl/Shift 多选后批量训练。")

        table = self.formula_case_table
        table.blockSignals(True)
        table.clearContents()
        table.setRowCount(len(cases))
        for row, item in enumerate(cases):
            stats = self._formula_stats_for_case(item)
            avg_ms = int(stats.get("total_ms", 0) / stats.get("count", 1)) if stats.get("count", 0) else 0
            values = [
                (0, item.get("category", "")),
                (1, item.get("case_id", "")),
                (2, item.get("slot") or item.get("name", "")),
                (5, str(len(item.get("algorithms", [])))),
                (6, str(int(stats.get("count", 0)))),
                (7, format_time_ms(avg_ms) if stats.get("count", 0) else "—"),
            ]
            for actual_col, value in values:
                cell = QTableWidgetItem(str(value))
                if actual_col in (0, 1, 5, 6, 7):
                    cell.setTextAlignment(Qt.AlignCenter)
                table.setItem(row, actual_col, cell)

            thumb_label = QLabel()
            thumb_label.setAlignment(Qt.AlignCenter)
            thumb_label.setStyleSheet("background: transparent;")
            thumb_label.setFixedSize(self._px(92), self._px(92))
            try:
                pixmap = self._formula_preview_pixmap_for_case(item)
            except Exception:
                pixmap = None
            if pixmap is not None:
                thumb_label.setPixmap(pixmap)
            else:
                thumb_label.setText("—")
                thumb_label.setObjectName("MutedLabel")
            table.setCellWidget(row, 3, thumb_label)

            top_thumb_label = QLabel()
            top_thumb_label.setAlignment(Qt.AlignCenter)
            top_thumb_label.setStyleSheet("background: transparent;")
            top_thumb_label.setFixedSize(self._px(92), self._px(92))
            try:
                top_pixmap = self._formula_top_preview_pixmap_for_case(item)
            except Exception:
                top_pixmap = None
            if top_pixmap is not None:
                top_thumb_label.setPixmap(top_pixmap)
            else:
                top_thumb_label.setText("—")
                top_thumb_label.setObjectName("MutedLabel")
            table.setCellWidget(row, 4, top_thumb_label)
        table.blockSignals(False)

        has_rows = bool(cases)
        if hasattr(self, "btn_formula_edit"):
            self.btn_formula_edit.setEnabled(has_rows)
        if hasattr(self, "btn_formula_delete"):
            self.btn_formula_delete.setEnabled(has_rows)

        if cases:
            target_row = 0
            if select_id:
                for idx, item in enumerate(cases):
                    if item.get("id") == select_id:
                        target_row = idx
                        break
            table.selectRow(target_row)
            self._on_formula_case_selected()
        else:
            self.formula_algorithm_list.clear()
            self.formula_case_title.setText("没有匹配的公式")
            self.formula_case_meta.setText("请切换分类、清空查询，或点击新增。")
            self._clear_formula_preview_image()

    def _formula_preview_cache_key(self, case, algorithm):
        orientation = getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION)
        return (
            str((case or {}).get("id") or (case or {}).get("case_id") or ""),
            str((case or {}).get("category") or ""),
            str(algorithm or ""),
            str(orientation or ""),
        )

    def _build_formula_preview_data(self, case, algorithm):
        if not case or not algorithm:
            raise ValueError("缺少公式或解法。")
        moves = self._formula_tokens(algorithm)
        inverse_moves = invert_algorithm(moves)
        initial_orientation = getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION)
        forward_end_orientation = self._formula_orientation_after_moves(initial_orientation, moves)
        inverse_end_orientation = self._formula_orientation_after_moves(forward_end_orientation, inverse_moves)
        preview_category = self._formula_preview_category(case)

        preview_cubies = create_solved_cube(forward_end_orientation)
        preview_cubies = self._mask_formula_preview_cubies(preview_cubies, preview_category)
        for move_label in inverse_moves:
            move_def = MOVE_DEFS.get(move_label)
            if not move_def:
                continue
            axis, layer, direction = move_def[:3]
            commit = {
                "axis": axis,
                "layer": layer,
                "direction": direction,
                "turns": move_turns(move_label),
                "label": move_label,
            }
            commit_move(preview_cubies, commit)

        return {
            "algorithm": algorithm,
            "moves": moves,
            "inverse_moves": inverse_moves,
            "initial_orientation": initial_orientation,
            "forward_end_orientation": forward_end_orientation,
            "inverse_end_orientation": inverse_end_orientation,
            "preview_category": preview_category,
            "preview_cubies": preview_cubies,
        }

    def _formula_preview_pixmap_for_case(self, case, algorithm=None, width=None, height=None):
        algorithms = (case or {}).get("algorithms", [])
        algorithm = algorithm or (algorithms[0] if algorithms else None)
        if not algorithm:
            return None
        width = int(width or self._px(92))
        height = int(height or self._px(92))
        cache_key = self._formula_preview_cache_key(case, algorithm) + (width, height, "direct")
        pixmap = getattr(self, "_formula_case_preview_cache", {}).get(cache_key)
        if pixmap is not None:
            return pixmap
        data = self._build_formula_preview_data(case, algorithm)

        # Fast path: render the thumbnail directly at the target table size.
        # Avoid the previous 4x/320px offscreen render + pixel trimming, which
        # could freeze when many rows refresh at once.  Blank space is reduced
        # by the compact drawing scale in FormulaPreviewImageWidget.paintEvent.
        widget = FormulaPreviewImageWidget()
        widget.compact_mode = True
        widget.setMinimumSize(0, 0)
        widget.setMaximumSize(16777215, 16777215)
        widget.setFixedSize(width, height)
        widget.set_cube_state(data["preview_cubies"], data["preview_category"])

        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.transparent)
        widget.render(pixmap)
        self._formula_case_preview_cache[cache_key] = pixmap
        return pixmap

    def _formula_top_preview_pixmap_for_case(self, case, algorithm=None, width=None, height=None):
        algorithms = (case or {}).get("algorithms", [])
        algorithm = algorithm or (algorithms[0] if algorithms else None)
        if not algorithm:
            return None
        width = int(width or self._px(92))
        height = int(height or self._px(92))
        cache_key = self._formula_preview_cache_key(case, algorithm) + (width, height, "top")
        pixmap = getattr(self, "_formula_case_preview_cache", {}).get(cache_key)
        if pixmap is not None:
            return pixmap
        data = self._build_formula_preview_data(case, algorithm)

        widget = FormulaTopPreviewWidget()
        widget.setMinimumSize(0, 0)
        widget.setMaximumSize(16777215, 16777215)
        widget.setFixedSize(width, height)
        widget.set_cube_state(data["preview_cubies"], data["preview_category"])

        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.transparent)
        widget.render(pixmap)
        self._formula_case_preview_cache[cache_key] = pixmap
        return pixmap

    def _current_formula_case(self):
        if not hasattr(self, "formula_case_table"):
            return None
        row = self.formula_case_table.currentRow()
        cases = getattr(self, "_formula_filtered_cases", [])
        if 0 <= row < len(cases):
            return cases[row]
        return None

    def _current_formula_algorithm(self):
        case = self._current_formula_case()
        if not case:
            return None
        row = self.formula_algorithm_list.currentRow() if hasattr(self, "formula_algorithm_list") else 0
        algorithms = case.get("algorithms", [])
        if 0 <= row < len(algorithms):
            return algorithms[row]
        return algorithms[0] if algorithms else None

    def _on_formula_case_selected(self):
        case = self._current_formula_case()
        if not case or not hasattr(self, "formula_algorithm_list"):
            return
        self.formula_case_title.setText(case.get("name", case.get("case_id", "公式")))
        slot = case.get("slot")
        meta = f"分类：{case.get('category', '-')}; 编号：{case.get('case_id', '-')}; 解法数：{len(case.get('algorithms', []))}"
        if slot:
            meta += f"; 位置：{slot}"
        self.formula_case_meta.setText(meta)

        self.formula_algorithm_list.blockSignals(True)
        self.formula_algorithm_list.clear()
        for idx, alg in enumerate(case.get("algorithms", []), start=1):
            self.formula_algorithm_list.addItem(f"解法 {idx}: {move_text(alg)}")
        self.formula_algorithm_list.blockSignals(False)

        if self.formula_algorithm_list.count() > 0:
            self.formula_algorithm_list.setCurrentRow(0)
            self._on_formula_algorithm_selected(auto_preview=True)
        else:
            self._clear_formula_preview_image()

    def _on_formula_algorithm_selected(self, auto_preview=True):
        alg = self._current_formula_algorithm()
        if not alg:
            self._clear_formula_preview_image()
            return
        should_preview = bool(auto_preview)
        if should_preview and hasattr(self, "panel_stack"):
            should_preview = self.panel_stack.currentIndex() == PAGE_FORMULA_PRACTICE
        if should_preview:
            self.preview_selected_formula_inverse()
        elif hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText(f"已选解法：{move_text(alg)}。进入公式练习页面后会显示预览。")

    def _formula_default_category(self):
        category = self.formula_category_combo.currentData() if hasattr(self, "formula_category_combo") else "F2L"
        return category if category in ("F2L", "OLL", "PLL") else "F2L"

    def _new_formula_id(self):
        existing = {str(item.get("id", "")) for item in getattr(self, "cfop_formula_library", [])}
        base = f"custom_{int(time.time() * 1000)}"
        candidate = base
        suffix = 1
        while candidate in existing:
            suffix += 1
            candidate = f"{base}_{suffix}"
        return candidate

    def _normalize_formula_algorithms_for_save(self, algorithms):
        normalized = []
        for alg in algorithms:
            alg = str(alg or "").strip()
            if not alg:
                continue
            # Validate now, but preserve the user's original spacing/style as much as possible.
            self._formula_tokens(alg)
            normalized.append(alg)
        if not normalized:
            raise ValueError("请至少填写一个有效解法。")
        return normalized

    def _save_formula_library(self):
        self.cfop_formula_library = save_cfop_library(self.cfop_formula_library)
        self._formula_case_preview_cache.clear()
        return True

    def add_formula_case(self):
        dialog = FormulaCaseEditDialog(self, default_category=self._formula_default_category())
        if dialog.exec() != QDialog.Accepted:
            return
        item = dialog.values()
        try:
            item["algorithms"] = self._normalize_formula_algorithms_for_save(item.get("algorithms", []))
        except Exception as exc:
            QMessageBox.warning(self, "公式格式错误", str(exc))
            return
        item["id"] = self._new_formula_id()
        self.cfop_formula_library.append(item)
        self._save_formula_library()
        self._pending_formula_select_id = item["id"]
        if hasattr(self, "formula_category_combo") and item.get("category") in ("F2L", "OLL", "PLL"):
            idx = self.formula_category_combo.findData(item.get("category"))
            if idx >= 0:
                self.formula_category_combo.setCurrentIndex(idx)
        self._refresh_formula_case_table()
        if hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText(f"已新增公式：{item.get('case_id', '')}。")

    def edit_formula_case(self):
        case = self._current_formula_case()
        if not case:
            QMessageBox.information(self, "提示", "请先选择一个公式。")
            return
        dialog = FormulaCaseEditDialog(self, formula=case, default_category=case.get("category", "F2L"))
        if dialog.exec() != QDialog.Accepted:
            return
        values = dialog.values()
        try:
            values["algorithms"] = self._normalize_formula_algorithms_for_save(values.get("algorithms", []))
        except Exception as exc:
            QMessageBox.warning(self, "公式格式错误", str(exc))
            return

        keep_id = case.get("id") or self._new_formula_id()
        case.clear()
        case.update(values)
        case["id"] = keep_id
        self._save_formula_library()
        self._pending_formula_select_id = keep_id
        if hasattr(self, "formula_category_combo") and case.get("category") in ("F2L", "OLL", "PLL"):
            idx = self.formula_category_combo.findData(case.get("category"))
            if idx >= 0:
                self.formula_category_combo.setCurrentIndex(idx)
        self._refresh_formula_case_table()
        if hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText(f"已更新公式：{case.get('case_id', '')}。")

    def delete_formula_case(self):
        case = self._current_formula_case()
        if not case:
            QMessageBox.information(self, "提示", "请先选择一个公式。")
            return
        name = case.get("name") or case.get("case_id") or "当前公式"
        reply = QMessageBox.question(
            self,
            "确认删除",
            f"确定删除“{name}”吗？该操作会写入公式库 JSON。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        case_id = case.get("id")
        self.cfop_formula_library = [
            item for item in self.cfop_formula_library
            if item is not case and item.get("id") != case_id
        ]
        self._save_formula_library()
        self._refresh_formula_case_table()
        if hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText(f"已删除公式：{name}。")

    def _normalize_formula_token(self, token):
        token = str(token or "").strip().replace("′", "'").replace("’", "'")
        if not token:
            raise ValueError("empty token")
        # 兼容少量资料里的 R3 / R3' 写法：R3 等价 R'，R3' 等价 R。
        m = re.fullmatch(r"([UDFBLRMESudfblrxyz])(3)('?|′?)", token)
        if m:
            face = m.group(1)
            return face if m.group(3) else face + "'"
        return normalize_move(token)

    def _formula_tokens(self, algorithm):
        tokens = []
        for raw in str(algorithm or "").split():
            tokens.append(self._normalize_formula_token(raw))
        return tokens

    def change_formula_practice_orientation(self):
        if not hasattr(self, "formula_orientation_combo"):
            return
        preset = self.formula_orientation_combo.currentData()
        if not preset:
            return
        current_case = self._current_formula_case()
        self.formula_practice_initial_orientation = preset
        self._formula_case_preview_cache.clear()
        if current_case:
            self._pending_formula_select_id = current_case.get("id")
        self._refresh_formula_case_table()
        if self._current_formula_case() and self._current_formula_algorithm():
            self.preview_selected_formula_inverse()
        else:
            self.formula_preview_started = False
            self._clear_formula_preview_image()
            if hasattr(self, "formula_practice_status"):
                self.formula_practice_status.setText(f"预览朝向已切换为 {self._orientation_text(preset)}。")

    def apply_formula_practice_view(self):
        if hasattr(self, "cube"):
            self.cube.set_view_preset("solver_default")
            self.cube.zoom = DEFAULT_CUBE_ZOOM
            self.cube.update()

    def _clear_formula_preview_image(self):
        if hasattr(self, "formula_preview_cube"):
            self.formula_preview_cube.clear_preview()
        if hasattr(self, "formula_top_preview"):
            self.formula_top_preview.clear_preview()
        if hasattr(self, "formula_preview_hint"):
            self.formula_preview_hint.show()

    def reset_formula_preview_cube(self):
        self.formula_preview_started = False
        if hasattr(self, "cube"):
            self.cube.reset_cube(getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION))
            self.apply_formula_practice_view()
            self.cube.set_guide_move(None)
        self._clear_formula_preview_image()
        if hasattr(self, "formula_practice_status"):
            self.formula_practice_status.setText(f"已按预览朝向 {self._orientation_text(getattr(self, 'formula_practice_initial_orientation', TIMER_INITIAL_ORIENTATION))} 重置魔方。请选择公式条目后会自动预览。")

    def _formula_orientation_after_moves(self, start_orientation, moves):
        """按公式顺序推导结束朝向。

        公式库里的公式默认是相对某个初始朝向书写的；遇到 M/E/S、宽层、x/y/z
        这类会移动中心块的动作后，后续步骤必须相对新的朝向继续执行。
        逆向预览也必须先从正向公式执行完后的朝向开始，再执行反向步骤。
        """
        orientation = start_orientation or TIMER_INITIAL_ORIENTATION
        for move in moves:
            orientation = self._orientation_after_move(orientation, move)
        return orientation

    def _formula_preview_category(self, case):
        return str((case or {}).get("category") or "").upper()

    def _mask_formula_preview_cubies(self, cubies, category):
        """根据公式类别生成带占位色的“已还原目标图”。

        F2L: 保留下方两层颜色；顶层仅保留 U 中心。
        OLL: 保留下方两层颜色；顶层仅保留 U 面颜色。
        PLL: 保留所有颜色。
        """
        category = str(category or "").upper()
        if category == "PLL":
            return cubies

        for cubie in cubies:
            if cubie.pos[1] != 1:
                continue
            masked = {}
            for normal, color_key in cubie.stickers.items():
                keep = True
                if category == "F2L":
                    keep = (cubie.pos == (0, 1, 0) and normal == (0, 1, 0))
                elif category == "OLL":
                    keep = (normal == (0, 1, 0))
                masked[normal] = color_key if keep else "X"
            cubie.stickers = masked
        return cubies

    def preview_selected_formula_inverse(self):
        alg = self._current_formula_algorithm()
        case = self._current_formula_case()
        if not alg or not case:
            if hasattr(self, "formula_practice_status"):
                self.formula_practice_status.setText("请先选择一个公式 case 和解法。")
            return
        try:
            data = self._build_formula_preview_data(case, alg)
            initial_orientation = data["initial_orientation"]
            forward_end_orientation = data["forward_end_orientation"]
            inverse_end_orientation = data["inverse_end_orientation"]
            inverse_moves = data["inverse_moves"]
            preview_category = data["preview_category"]
            preview_cubies = data["preview_cubies"]
        except Exception as exc:
            if hasattr(self, "formula_practice_status"):
                self.formula_practice_status.setText(f"该解法暂时无法预览：{exc}")
            return
        if hasattr(self, "cube"):
            self.cube.cubies = preview_cubies
            self.cube.move_queue.clear()
            self.cube.active_move = None
            self.apply_formula_practice_view()
            self.cube.set_guide_move(None)
            self.cube.update()
        if hasattr(self, "formula_preview_cube"):
            self.formula_preview_cube.set_cube_state(preview_cubies, preview_category)
        if hasattr(self, "formula_top_preview"):
            self.formula_top_preview.set_cube_state(preview_cubies, preview_category)
        if hasattr(self, "formula_preview_hint"):
            self.formula_preview_hint.hide()
        self.formula_preview_started = True
        if hasattr(self, "formula_practice_status"):
            mode_text = {
                "F2L": "F2L：保留下方两层，顶层仅保留中心块",
                "OLL": "OLL：保留下方两层，顶层仅保留顶面颜色",
                "PLL": "PLL：保留所有颜色",
            }.get(preview_category, f"{preview_category}：按当前类别规则预览")
            self.formula_practice_status.setText(
                f"初始状态预览：{case.get('case_id', '')} / {move_text(alg)}；"
                f"规则：{mode_text}；"
                f"预览朝向：{self._orientation_text(initial_orientation)}；"
                f"正向结束朝向：{self._orientation_text(forward_end_orientation)}；"
                f"逆向后朝向：{self._orientation_text(inverse_end_orientation)}；"
                f"逆向步骤：{move_text(' '.join(inverse_moves))}"
            )

    def _build_solver_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self._px(10))

        card = QFrame()
        card.setObjectName("GlassCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(self._px(14), self._px(10), self._px(14), self._px(10))
        card_layout.setSpacing(self._px(8))

        top = QHBoxLayout()
        self.solver_status_label = QLabel("自动求解：点击 Solve 生成还原引导")
        self.solver_status_label.setObjectName("SectionTitle")

        self.orientation_label = QLabel("初始朝向")
        self.orientation_label.setObjectName("MutedLabel")
        self.orientation_combo = QComboBox()
        self.orientation_combo.setObjectName("ResolutionCombo")
        self.orientation_combo.setFixedWidth(self._px(118))
        for text, preset in self.cube_orientation_options:
            self.orientation_combo.addItem(text, preset)
        self.orientation_combo.setCurrentIndex(0)

        self.btn_mode = QPushButton("模式：自动")
        self.btn_mode.setObjectName("PurpleButton")
        self.btn_solve = QPushButton("Solve")
        self.btn_solve.setObjectName("PrimaryButton")
        self.btn_exit_guide = QPushButton("Exit")
        self.btn_reset = QPushButton("Reset")
        self.btn_reset.setObjectName("DangerButton")

        top.addWidget(self.solver_status_label, 1)
        top.addWidget(self.orientation_label)
        top.addWidget(self.orientation_combo)
        top.addWidget(self.btn_mode)
        top.addWidget(self.btn_solve)
        top.addWidget(self.btn_exit_guide)
        top.addWidget(self.btn_reset)

        self.solution_chip_box, self.solution_chips_layout = self._build_chip_box()
        self.solution_chip_box.setMinimumHeight(self._px(110))
        self.solution_chip_box.setMaximumHeight(self._px(210))
        self.solution_chip_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.solution_title_label = QLabel("还原步骤")

        card_layout.addLayout(top)
        card_layout.addWidget(self.solution_title_label)
        card_layout.addWidget(self.solution_chip_box, 0)

        manual_box = QFrame()
        manual_box.setObjectName("ManualBox")
        manual_flow = FlowLayout(manual_box, margin=10, spacing=8)

        manual_moves = [
            "U", "U'", "U2", "D", "D'", "D2",
            "F", "F'", "F2", "B", "B'", "B2",
            "L", "L'", "L2", "R", "R'", "R2",
            "M", "M'", "M2", "E", "E'", "E2", "S", "S'", "S2",
            "u", "u'", "u2", "d", "d'", "d2",
            "f", "f'", "f2", "b", "b'", "b2",
            "l", "l'", "l2", "r", "r'", "r2",
            "x", "x'", "x2", "y", "y'", "y2", "z", "z'", "z2",
        ]

        for mv in manual_moves:
            btn = QPushButton(move_text(mv))
            btn.setObjectName("MoveButton")
            btn.setFixedWidth(self._px(44))
            btn.clicked.connect(lambda checked=False, m=mv: self.handle_user_move(m))
            manual_flow.addWidget(btn)

        manual_scroll = QScrollArea()
        manual_scroll.setWidgetResizable(True)
        manual_scroll.setFrameShape(QFrame.NoFrame)
        manual_scroll.setWidget(manual_box)
        self.manual_scroll = manual_scroll
        manual_scroll.setMinimumHeight(self._px(180))
        manual_scroll.setMaximumHeight(16777215)
        manual_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        card_layout.addWidget(manual_scroll, 1)
        layout.addWidget(card)
        self.panel_stack.addWidget(panel)

        self.orientation_combo.currentIndexChanged.connect(self.change_solver_initial_orientation)
        self.btn_mode.clicked.connect(self.toggle_solver_mode)
        self.btn_solve.clicked.connect(self.start_solution)
        self.btn_exit_guide.clicked.connect(self.exit_solution)
        self.btn_reset.clicked.connect(self.reset_all)

































































    def _build_placeholder_panel(self, title, desc):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setAlignment(Qt.AlignCenter)

        card = QFrame()
        card.setObjectName("GlassCard")
        card.setFixedWidth(self._px(500))

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(self._px(22), self._px(18), self._px(22), self._px(18))
        card_layout.setSpacing(self._px(10))

        title_label = QLabel(title)
        title_label.setObjectName("SectionTitle")
        desc_label = QLabel(desc)
        desc_label.setWordWrap(True)
        desc_label.setObjectName("MutedLabel")

        card_layout.addWidget(title_label)
        card_layout.addWidget(desc_label)
        layout.addWidget(card)

        self.panel_stack.addWidget(panel)

    def _build_debug_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(self._px(10))

        card = QFrame()
        card.setObjectName("GlassCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(self._px(14), self._px(10), self._px(14), self._px(10))
        card_layout.setSpacing(self._px(8))

        top = QHBoxLayout()
        self.debug_status_label = QLabel("选择朝向后点击单步调试或多步调试")
        self.debug_status_label.setObjectName("SectionTitle")

        self.debug_orientation_label = QLabel("初始朝向")
        self.debug_orientation_label.setObjectName("MutedLabel")
        self.debug_orientation_combo = QComboBox()
        self.debug_orientation_combo.setObjectName("ResolutionCombo")
        self.debug_orientation_combo.setFixedWidth(self._px(118))
        for text, preset in self.cube_orientation_options:
            self.debug_orientation_combo.addItem(text, preset)
        self.debug_orientation_combo.setCurrentIndex(0)

        self.btn_debug_single = QPushButton("单步调试")
        self.btn_debug_single.setObjectName("PrimaryButton")
        self.btn_debug_multi = QPushButton("多步调试")
        self.btn_debug_multi.setObjectName("PurpleButton")
        self.btn_debug_reset = QPushButton("Reset")
        self.btn_debug_reset.setObjectName("DangerButton")

        top.addWidget(self.debug_status_label, 1)
        top.addWidget(self.debug_orientation_label)
        top.addWidget(self.debug_orientation_combo)
        top.addWidget(self.btn_debug_single)
        top.addWidget(self.btn_debug_multi)
        top.addWidget(self.btn_debug_reset)

        self.debug_orientation_state_label = QLabel("初始朝向：-；当前朝向：-")
        self.debug_orientation_state_label.setObjectName("MutedLabel")

        self.debug_chip_box, self.debug_chips_layout = self._build_chip_box()
        self.debug_chip_box.setMinimumHeight(self._px(84))
        self.debug_chip_box.setMaximumHeight(self._px(150))
        self.debug_chip_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        self.debug_title_label = QLabel("调试公式")
        self.debug_title_label.setObjectName("MutedLabel")

        self.debug_log_list = QListWidget()
        self.debug_log_list.setObjectName("HistoryList")
        self.debug_log_list.setMinimumHeight(self._px(92))
        self.debug_log_list.setMaximumHeight(self._px(170))

        manual_box = QFrame()
        manual_box.setObjectName("ManualBox")
        manual_flow = FlowLayout(manual_box, margin=10, spacing=8)
        for mv in DEBUG_MANUAL_MOVES:
            btn = QPushButton(move_text(mv))
            btn.setObjectName("MoveButton")
            btn.setFixedWidth(self._px(44))
            btn.clicked.connect(lambda checked=False, m=mv: self._handle_debug_manual_button(m))
            manual_flow.addWidget(btn)

        debug_manual_scroll = QScrollArea()
        debug_manual_scroll.setWidgetResizable(True)
        debug_manual_scroll.setFrameShape(QFrame.NoFrame)
        debug_manual_scroll.setWidget(manual_box)
        debug_manual_scroll.setMinimumHeight(self._px(118))
        debug_manual_scroll.setMaximumHeight(self._px(170))
        debug_manual_scroll.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        hint = QLabel("说明：单步调试不会生成 x/y/z。多步调试会实时跟踪当前朝向：U/R/B/F/D/L 不改变朝向；其它字母会按 3D 魔方实际公式更新朝向；遇到 x/y/z 会自动执行并进入下一步。")
        hint.setObjectName("MutedLabel")
        hint.setWordWrap(True)

        card_layout.addLayout(top)
        card_layout.addWidget(self.debug_orientation_state_label)
        card_layout.addWidget(self.debug_title_label)
        card_layout.addWidget(self.debug_chip_box)
        card_layout.addWidget(hint)
        card_layout.addWidget(self.debug_log_list)
        card_layout.addWidget(debug_manual_scroll)

        layout.addWidget(card)
        self.panel_stack.addWidget(panel)

        self.debug_orientation_combo.currentIndexChanged.connect(self.change_debug_initial_orientation)
        self.btn_debug_single.clicked.connect(self.start_debug_single)
        self.btn_debug_multi.clicked.connect(self.start_debug_multi)
        self.btn_debug_reset.clicked.connect(self.reset_debug_cube)
        self._debug_update_orientation_state_label()
        self._refresh_debug_label(force=True)

    def _bind_shortcuts(self):
        for key, index in [("1", PAGE_TIMER), ("2", PAGE_FORMULA_PRACTICE), ("3", PAGE_SOLVER), ("4", PAGE_DEBUG)]:
            action = QAction(self)
            action.setShortcut(key)
            action.triggered.connect(lambda checked=False, i=index: self.switch_page(i))
            self.addAction(action)

        action_reset = QAction(self)
        action_reset.setShortcut("Space")
        action_reset.triggered.connect(self.reset_all)
        self.addAction(action_reset)

    def switch_page(self, index):
        old_index = self.panel_stack.currentIndex() if hasattr(self, "panel_stack") else None

        if hasattr(self, "timer_detail_page") and index != PAGE_TIMER:
            self.timer_detail_page.hide()
            self.cube_stage.show()
            self.panel_stack.show()

        self.panel_stack.setCurrentIndex(index)

        for i, btn in enumerate([self.btn_timer, self.btn_formula_practice, self.btn_solver, self.btn_debug]):
            btn.setChecked(i == index)

        title_map = {
            PAGE_TIMER: "计时练习",
            PAGE_FORMULA_PRACTICE: "公式练习",
            PAGE_SOLVER: "魔方求解引导",
            PAGE_DEBUG: "调试板块",
            PAGE_FORMULA_TRAINING: "公式训练",
        }
        self.title_label.setText(title_map.get(index, "公式训练"))

        # 进入公式练习/公式训练时自动关闭陀螺仪跟随。
        # 这些页面依赖固定视角与引导箭头，若陀螺仪仍在工作，箭头和用户
        # 手中的物理魔方方向会持续漂移，影响判断。
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            self.set_gyro_follow_enabled_ui(False)

        # Different pages need different layout emphasis.
        # Solver and formula practice use a left-right layout: cube on the left,
        # controls/library on the right. Timer and debug keep the original stack.
        if hasattr(self, "cube_stage"):
            if index in (PAGE_SOLVER, PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
                self.cube_stage.show()
                self.content_layout.setDirection(QBoxLayout.LeftToRight)
                self.content_layout.setSpacing(self._px(12))
                self.cube_stage.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                self.cube_stage.setMinimumWidth(self._px(400))
                self.cube_stage.setMaximumWidth(16777215)
                self.cube_stage.setMinimumHeight(self._px(420))
                self.cube_stage.setMaximumHeight(16777215)
                if index == PAGE_FORMULA_PRACTICE:
                    # 公式练习：左侧上方是魔方、下方是解法；右侧只放表格。
                    self.panel_stack.setMinimumWidth(self._px(620))
                    self.content_layout.setStretchFactor(self.cube_stage, 5)
                    self.content_layout.setStretchFactor(self.panel_stack, 7)
                elif index == PAGE_FORMULA_TRAINING:
                    # 公式训练单独页面：左侧只保留 3D 魔方，右侧显示训练步骤和统计。
                    self.panel_stack.setMinimumWidth(self._px(560))
                    self.content_layout.setStretchFactor(self.cube_stage, 6)
                    self.content_layout.setStretchFactor(self.panel_stack, 6)
                else:
                    self.panel_stack.setMinimumWidth(self._px(520))
                    self.content_layout.setStretchFactor(self.cube_stage, 5)
                    self.content_layout.setStretchFactor(self.panel_stack, 8)
                if hasattr(self, "solver_cube_actions"):
                    self.solver_cube_actions.setVisible(index == PAGE_SOLVER)
                if hasattr(self, "formula_solution_box"):
                    self.formula_solution_box.setVisible(index == PAGE_FORMULA_PRACTICE)
                if hasattr(self, "formula_training_preview_box"):
                    self.formula_training_preview_box.setVisible(index == PAGE_FORMULA_TRAINING and getattr(self, "formula_training_active", False))
                if hasattr(self, "today_card"):
                    self.today_card.hide()

                if index == PAGE_SOLVER:
                    self.cube.zoom = SOLVER_CUBE_ZOOM
                    # 计时训练会固定重置为白顶绿前。若从计时页回到求解器，
                    # 必须按当前下拉框重新生成求解器的还原态，否则会出现
                    # “显示黄顶绿前，但实际仍是白顶绿前”的状态错位。
                    if old_index != PAGE_SOLVER:
                        self.restore_solver_context_after_page_switch()
                    else:
                        self.apply_solver_initial_orientation()
                elif index == PAGE_FORMULA_TRAINING:
                    self.cube.zoom = SOLVER_CUBE_ZOOM
                    if getattr(self, "formula_training_active", False) and self.formula_training_guide.solution_moves:
                        self._apply_formula_training_guide_arrow()
                        self._refresh_formula_training_ui(force=True)
                    else:
                        self.cube.set_guide_move(None)
                else:
                    self.cube.zoom = SOLVER_CUBE_ZOOM
                    # 公式练习也要像求解器一样按当前预览朝向同步主 3D 魔方；
                    # 若已有选中公式，直接刷新预览，否则只重置到当前朝向。
                    if self._current_formula_case() and self._current_formula_algorithm():
                        self.preview_selected_formula_inverse()
                    else:
                        self.reset_formula_preview_cube()
            else:
                self.content_layout.setDirection(QBoxLayout.TopToBottom)
                self.content_layout.setSpacing(self._px(10))
                if hasattr(self, "solver_cube_actions"):
                    self.solver_cube_actions.hide()
                if hasattr(self, "formula_solution_box"):
                    self.formula_solution_box.hide()
                if hasattr(self, "formula_training_preview_box"):
                    self.formula_training_preview_box.hide()
                if hasattr(self, "today_card"):
                    self.today_card.setVisible(index == PAGE_TIMER)
                self.cube_stage.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
                self.cube_stage.setMinimumWidth(0)
                self.cube_stage.setMaximumWidth(16777215)
                self.panel_stack.setMinimumWidth(0)
                self.content_layout.setStretchFactor(self.cube_stage, 0)
                self.content_layout.setStretchFactor(self.panel_stack, 1)

                if index == PAGE_TIMER:  # 计时练习：提高 3D 魔方区域占比，压缩下方计时/历史区
                    self.cube_stage.show()
                    self.cube_stage.setMinimumHeight(self._px(300))
                    self.cube_stage.setMaximumHeight(self._px(430))
                    self.cube.zoom = TIMER_CUBE_ZOOM
                    # 计时页只在“进入页面”时同步一次实体状态；停留期间不持续 FACELETS 覆盖。
                    if old_index != PAGE_TIMER:
                        if not self._sync_ble_physical_state_once_on_page_entry(PAGE_TIMER):
                            self.cube.reset_cube(TIMER_INITIAL_ORIENTATION)
                elif index == PAGE_DEBUG:
                    self.cube_stage.show()
                    self.cube_stage.setMinimumHeight(self._px(300))
                    self.cube_stage.setMaximumHeight(self._px(430))
                    self.cube.zoom = DEFAULT_CUBE_ZOOM
                    if old_index != PAGE_DEBUG:
                        self.restore_debug_context_after_page_switch()
                    else:
                        self.apply_debug_initial_orientation()
                else:
                    self.cube_stage.show()
                    self.cube_stage.setMinimumHeight(self._px(170))
                    self.cube_stage.setMaximumHeight(self._px(220))
                    self.cube.zoom = DEFAULT_CUBE_ZOOM
            self.cube.update()

        if index == PAGE_SOLVER:
            self._refresh_solution_label(force=True)
        elif index == PAGE_DEBUG:
            self._refresh_debug_label(force=True)
            self.cube.set_guide_move(self._debug_current_move())
        elif index == PAGE_FORMULA_PRACTICE:
            self.cube.set_guide_move(None)
        elif index == PAGE_FORMULA_TRAINING:
            if getattr(self, "formula_training_active", False):
                self._apply_formula_training_guide_arrow()
                self._refresh_formula_training_ui(force=True)
            else:
                self.cube.set_guide_move(None)
        else:
            self.cube.set_guide_move(None)

    def restore_debug_context_after_page_switch(self):
        self.cancel_replay(update_status=False)
        applied = self._sync_ble_physical_state_once_on_page_entry(PAGE_DEBUG)
        if applied:
            self.debug_current_orientation = self.debug_initial_orientation
            self.debug_moves = []
            self.debug_index = 0
            self.debug_active = False
            self.debug_mode = None
            self.debug_match_states = []
            self.debug_progress_completed = 0
            self.debug_progress_total = 0
            self.debug_progress_partial = False
            self._last_debug_chip_key = None
            self.apply_debug_initial_orientation()
            if hasattr(self, "debug_status_label"):
                self.debug_status_label.setText("正在读取实体魔方当前状态" if self.ble_pending_facelets_sync_page == PAGE_DEBUG else "已同步实体魔方当前状态")
            self._debug_update_orientation_state_label()
            self._refresh_debug_label(force=True)
            self._append_debug_log("进入调试板块：已请求同步实体魔方当前状态" if self.ble_pending_facelets_sync_page == PAGE_DEBUG else "进入调试板块：已同步实体魔方当前状态")
        else:
            self.reset_debug_cube()
            self._append_debug_log("进入调试板块：暂无实体状态，已按当前初始朝向重置魔方")

    def apply_debug_initial_orientation(self):
        if hasattr(self, "cube"):
            self.cube.set_view_preset("solver_default")
            self.cube.zoom = DEFAULT_CUBE_ZOOM
            self.cube.update()

    def _display_to_native_vec_for_orientation(self, vec, orientation_preset):
        """Convert a vector from the current display axes back to native GAN axes."""
        try:
            target = tuple(int(round(v)) for v in vec)
        except Exception:
            return vec
        if not orientation_preset or orientation_preset == TIMER_INITIAL_ORIENTATION:
            return target
        for x in (-1, 0, 1):
            for y in (-1, 0, 1):
                for z in (-1, 0, 1):
                    candidate = (x, y, z)
                    if native_to_oriented_vec(candidate, orientation_preset) == target:
                        return candidate
        return target

    def _reorient_current_cube_preserving_state(self, from_orientation, to_orientation):
        """Change the displayed top/front orientation without resetting the cube state."""
        if not hasattr(self, "cube"):
            return False
        from_orientation = from_orientation or TIMER_INITIAL_ORIENTATION
        to_orientation = to_orientation or TIMER_INITIAL_ORIENTATION
        if from_orientation == to_orientation:
            self.apply_debug_initial_orientation()
            return True
        try:
            cubies = copy.deepcopy(self.cube.cubies)
            for cubie in cubies:
                native_pos = self._display_to_native_vec_for_orientation(cubie.pos, from_orientation)
                cubie.pos = native_to_oriented_vec(native_pos, to_orientation)
                cubie.stickers = {
                    native_to_oriented_vec(
                        self._display_to_native_vec_for_orientation(normal, from_orientation),
                        to_orientation,
                    ): color_key
                    for normal, color_key in cubie.stickers.items()
                }
            self.cube.cubies = cubies
            self.cube.move_queue.clear()
            self.cube.active_move = None
            self.apply_debug_initial_orientation()
            return True
        except Exception as exc:
            self._append_debug_log(f"切换调试朝向失败：{exc}")
            self.apply_debug_initial_orientation()
            return False

    def _apply_debug_selected_orientation_preserving_state(self):
        """Use the combo-box orientation for a new debug task, preserving cube state.

        Prefer the tracked physical cube state as the source of truth.  This is
        important after M/E/S, wide moves, or a previous debug task has changed
        centre orientation: the debug variables may already have been reset to
        the combo-box value while the visible 3D cube still carries the previous
        displayed axes.  Re-applying the cached physical state with the selected
        orientation changes only the display/detection frame, not the cube's
        scrambled state.
        """
        if not hasattr(self, "debug_orientation_combo"):
            return False
        preset = self.debug_orientation_combo.currentData() or self.debug_initial_orientation or TIMER_INITIAL_ORIENTATION
        old_orientation = getattr(self, "debug_current_orientation", None) or getattr(self, "debug_initial_orientation", TIMER_INITIAL_ORIENTATION)
        self.debug_initial_orientation = preset
        self.debug_current_orientation = preset

        applied = False
        if getattr(self, "ble_physical_state_ready", False):
            # Do not request fresh FACELETS here; this action is a local frame
            # switch for the current tracked physical state.
            applied = self._apply_ble_physical_state_for_page(PAGE_DEBUG, request_fresh=False)

        if not applied:
            applied = self._reorient_current_cube_preserving_state(old_orientation, preset)

        self.apply_debug_initial_orientation()
        self._debug_update_orientation_state_label()
        return applied

    def _reset_debug_visual_to_initial(self, guide_move=None, cancel_pending_facelets=True):
        """Force the visible 3D cube back to a solved debug initial state.

        This is still useful for the Reset button or when no physical cube state
        is available.  Error recovery should prefer
        _sync_debug_visual_to_physical_initial_orientation(), because the user
        expects the 3D cube to keep the real cube scramble while returning to the
        selected initial top/front placement.
        """
        if cancel_pending_facelets:
            # A delayed page-entry FACELETS packet may otherwise overwrite this
            # local reset with the physical cube state a moment later.
            self.ble_pending_facelets_sync_page = None
            self.ble_pending_facelets_sync_reason = ""
        if hasattr(self, "cube"):
            self.cube.reset_cube(self.debug_initial_orientation)
            self.apply_debug_initial_orientation()
            self.cube.set_guide_move(guide_move)
            self.cube.update()

    def _sync_debug_visual_to_physical_initial_orientation(self, guide_move=None, request_fresh=True):
        """Show the tracked physical cube state in the debug initial placement.

        A wrong step in debug mode is a real turn on the user's cube.  Restarting
        the formula from step 1 should therefore reset only the detection frame
        and top/front placement, not the cube's sticker state.  We first apply
        the cached physical state immediately, then optionally request one fresh
        FACELETS packet so BLE can correct any missed middle-layer/state update.
        """
        old_orientation = getattr(self, "debug_current_orientation", self.debug_initial_orientation)
        self.debug_current_orientation = self.debug_initial_orientation

        applied = False
        if getattr(self, "ble_physical_state_ready", False):
            applied = self._apply_ble_physical_state_for_page(PAGE_DEBUG, request_fresh=False)

        if not applied:
            applied = self._reorient_current_cube_preserving_state(old_orientation, self.debug_initial_orientation)

        if request_fresh and getattr(self, "ble_status", "") == "connected":
            self._request_ble_facelets_for_page(PAGE_DEBUG, reason="debug-error-resync")

        if hasattr(self, "cube"):
            self.apply_debug_initial_orientation()
            self.cube.set_guide_move(guide_move)
            self.cube.update()
        return applied

    def reset_debug_cube(self):
        self.debug_current_orientation = self.debug_initial_orientation
        self.debug_moves = []
        self.debug_index = 0
        self.debug_active = False
        self.debug_mode = None
        self.debug_match_states = []
        self.debug_progress_completed = 0
        self.debug_progress_total = 0
        self.debug_progress_partial = False
        self._last_debug_chip_key = None
        self._reset_debug_visual_to_initial(guide_move=None)
        if hasattr(self, "debug_status_label"):
            self.debug_status_label.setText("已重置调试魔方，等待生成公式")
        self._debug_update_orientation_state_label()
        self._refresh_debug_label(force=True)

    def change_debug_initial_orientation(self):
        if not hasattr(self, "debug_orientation_combo"):
            return
        preset = self.debug_orientation_combo.currentData()
        if not preset:
            return
        if hasattr(self, "panel_stack") and self.panel_stack.currentIndex() == PAGE_DEBUG:
            old_orientation = getattr(self, "debug_current_orientation", self.debug_initial_orientation)
            self.debug_initial_orientation = preset
            self.debug_current_orientation = preset
            if getattr(self, "ble_physical_state_ready", False):
                self._apply_ble_physical_state_for_page(PAGE_DEBUG, request_fresh=False)
            else:
                # Fallback for non-BLE/manual use: preserve the current visual
                # state as best as possible by transforming from the last known
                # debug orientation.
                self._reorient_current_cube_preserving_state(old_orientation, preset)
            self.apply_debug_initial_orientation()
            self.debug_moves = []
            self.debug_index = 0
            self.debug_active = False
            self.debug_mode = None
            self.debug_match_states = []
            self.debug_progress_completed = 0
            self.debug_progress_total = 0
            self.debug_progress_partial = False
            self._last_debug_chip_key = None
            self.cube.set_guide_move(None)
            self.debug_status_label.setText(f"已切换调试朝向为 {self._orientation_text(preset)}，未重置魔方状态")
            self._append_debug_log(f"切换调试朝向：{self.debug_orientation_combo.currentText()}；未重置魔方状态")
            self._refresh_debug_label(force=True)
        else:
            self.debug_initial_orientation = preset
            self.debug_current_orientation = preset
            self.apply_debug_initial_orientation()
        self._debug_update_orientation_state_label()

    def _random_debug_move(self):
        return random.choice(DEBUG_FORMULA_FACES) + random.choice(DEBUG_MOVE_SUFFIXES)

    def _make_debug_sequence(self, count):
        # 多步调试要覆盖所有公式按钮池，包括会改变魔方朝向的 x/y/z。
        return [random.choice(DEBUG_MOVE_FACES) + random.choice(DEBUG_MOVE_SUFFIXES) for _ in range(count)]

    def _orientation_text(self, orientation):
        color_names = {
            "white": "白", "yellow": "黄", "green": "绿",
            "blue": "蓝", "red": "红", "orange": "橙",
        }
        try:
            top, front = orientation.split("_top_")
            front = front.replace("_front", "")
            return f"{color_names.get(top, top)}顶{color_names.get(front, front)}前"
        except Exception:
            return str(orientation or "未知朝向")

    def _debug_update_orientation_state_label(self):
        if not hasattr(self, "debug_orientation_state_label"):
            return
        initial = self._orientation_text(getattr(self, "debug_initial_orientation", None))
        current = self._orientation_text(getattr(self, "debug_current_orientation", None))
        self.debug_orientation_state_label.setText(f"初始朝向：{initial}；当前朝向：{current}")

    def _orientation_after_move(self, orientation, formula_move):
        """按公式动作更新当前“顶/前”朝向。

        U/R/B/F/D/L 单层面转不会移动中心块，所以朝向不变；
        小写宽层、中层 M/E/S、整体旋转 x/y/z 会移动中心块，
        这里用和 3D 魔方 MOVE_DEFS 相同的坐标规则同步更新。
        """
        try:
            move = normalize_move(formula_move)
        except Exception:
            move = str(formula_move or "")
        if move not in MOVE_DEFS:
            return orientation

        axis, layer, direction = MOVE_DEFS[move]
        layer_set = set(layer_values(layer))
        if 0 not in layer_set and layer != "all":
            return orientation

        colors = {
            "white": (0, 1, 0), "yellow": (0, -1, 0),
            "green": (0, 0, 1), "blue": (0, 0, -1),
            "red": (1, 0, 0), "orange": (-1, 0, 0),
        }
        positions = {
            color: native_to_oriented_vec(vec, orientation)
            for color, vec in colors.items()
        }
        axis_index = {"x": 0, "y": 1, "z": 2}[axis]
        turns = move_turns(move)
        for _ in range(turns):
            for color, pos in list(positions.items()):
                if layer == "all" or pos[axis_index] in layer_set:
                    positions[color] = rotate_vec(pos, axis, direction)

        top_color = next((c for c, pos in positions.items() if pos == (0, 1, 0)), None)
        front_color = next((c for c, pos in positions.items() if pos == (0, 0, 1)), None)
        if not top_color or not front_color:
            return orientation
        return f"{top_color}_top_{front_color}_front"

    def _debug_advance_orientation(self, formula_move):
        before = getattr(self, "debug_current_orientation", self.debug_initial_orientation)
        after = self._orientation_after_move(before, formula_move)
        self.debug_current_orientation = after
        if after != before:
            self._append_debug_log(
                f"朝向更新：{self._orientation_text(before)} + {formula_move} => {self._orientation_text(after)}"
            )
        self._debug_update_orientation_state_label()

    def _debug_primitive_for_face(self, face):
        # 宽层转动在智能魔方面转信号里等价为相对外层同后缀信号。
        # 已按用户要求：d 等价于 U。
        return {
            "u": "D", "d": "U",
            "r": "L", "l": "R",
            "f": "B", "b": "F",
        }.get(face, face)

    def _debug_apply_suffix(self, face, suffix):
        return face + (suffix or "")

    def _debug_double_alternatives(self, base_face):
        # U2/D2/... 允许两个连续 U 或两个连续 U′，中间不能夹其它动作。
        return [
            [frozenset([base_face]), frozenset([base_face])],
            [frozenset([base_face + "'"]), frozenset([base_face + "'"])],
        ]

    def _debug_orderless_pair_alternatives(self, first, second, suffix):
        if suffix == "2":
            group = frozenset([first, second])
            return [[group, group]]
        if suffix == "'":
            first = first[:-1] if first.endswith("'") else first + "'"
            second = second[:-1] if second.endswith("'") else second + "'"
        return [[frozenset([first, second])]]

    def _debug_patterns_for_move(self, move):
        try:
            move = normalize_move(move)
        except Exception:
            return []
        face, suffix = _split_move_suffix(move)

        slice_pairs = {
            "M": ("L'", "R"),
            "E": ("D'", "U"),
            "S": ("F'", "B"),
        }
        if face in slice_pairs:
            # 智能魔方有两种来源：
            # 1) 外层面转信号组合，例如 M = L′ + R；
            # 2) 状态包 FACELETS 推断出的直接中层动作，例如 M。
            # 两种都要允许，否则 M/E/S 在新蓝牙状态同步逻辑下会漏判。
            direct = [[frozenset([self._debug_apply_suffix(face, suffix)])]]
            return direct + self._debug_orderless_pair_alternatives(*slice_pairs[face], suffix)

        base_face = self._debug_primitive_for_face(face)
        if suffix == "2":
            return self._debug_double_alternatives(base_face)
        return [[frozenset([self._debug_apply_suffix(base_face, suffix)])]]

    def _set_debug_progress_from_states(self):
        states = getattr(self, "debug_match_states", []) or []
        if not states:
            self.debug_progress_completed = 0
            self.debug_progress_total = 0
            self.debug_progress_partial = False
            return

        best = None
        for state in states:
            pattern = state.get("pattern", [])
            group_index = int(state.get("group_index", 0) or 0)
            collected = set(state.get("collected", set()) or set())
            score = (group_index, len(collected), len(pattern))
            if best is None or score > best[0]:
                best = (score, pattern, group_index, collected)

        _, pattern, group_index, collected = best
        self.debug_progress_completed = max(0, group_index)
        self.debug_progress_total = max(0, len(pattern))
        self.debug_progress_partial = bool(collected)

    def _reset_debug_match_states(self):
        expected = self._debug_current_move()
        self.debug_match_states = []
        for pattern in self._debug_patterns_for_move(expected):
            self.debug_match_states.append({"pattern": pattern, "group_index": 0, "collected": set()})
        self._set_debug_progress_from_states()

    def _debug_expected_description(self, move=None):
        move = move or self._debug_current_move()
        patterns = self._debug_patterns_for_move(move)
        if not patterns:
            return "无可检测模式"
        parts = []
        for pattern in patterns:
            group_texts = []
            for group in pattern:
                group_texts.append("+".join(move_text(x) for x in sorted(group)))
            parts.append(" / ".join(group_texts))
        return " 或 ".join(parts)

    def _debug_feed_actual_move(self, actual):
        if not getattr(self, "debug_match_states", None):
            self._reset_debug_match_states()
        next_states = []
        completed = False
        progress_texts = []

        for state in self.debug_match_states:
            pattern = state["pattern"]
            group_index = state["group_index"]
            collected = set(state["collected"])
            if group_index >= len(pattern):
                completed = True
                continue

            group = pattern[group_index]
            if actual not in group or actual in collected:
                continue

            collected.add(actual)
            if collected == set(group):
                group_index += 1
                collected = set()
                if group_index >= len(pattern):
                    completed = True
                    continue

            progress_texts.append(f"{group_index + 1}/{len(pattern)}")
            next_states.append({"pattern": pattern, "group_index": group_index, "collected": collected})

        self.debug_match_states = next_states
        self._set_debug_progress_from_states()
        return completed, bool(next_states), ", ".join(sorted(set(progress_texts)))

    def _debug_current_move(self):
        if self.debug_active and 0 <= self.debug_index < len(self.debug_moves):
            return self.debug_moves[self.debug_index]
        return None

    def start_debug_single(self):
        if self._cube_has_pending_moves():
            self.debug_status_label.setText("动作尚未完成，请稍后再生成公式")
            return
        # 只重开调试任务，不重置当前 3D/实体魔方状态；
        # 但要先把 3D 显示朝向切到下拉框选中的初始朝向，
        # 否则后续 BLE 面转映射会和检测朝向不一致。
        self._apply_debug_selected_orientation_preserving_state()
        self.debug_moves = [self._random_debug_move()]
        self.debug_current_orientation = self.debug_initial_orientation
        self.debug_index = 0
        self.debug_active = True
        self.debug_mode = "single"
        self.debug_match_states = []
        self.debug_progress_completed = 0
        self.debug_progress_total = 0
        self.debug_progress_partial = False
        self._last_debug_chip_key = None
        self._reset_debug_match_states()
        self.debug_status_label.setText(f"单步调试：请执行 {move_text(self.debug_moves[0])}")
        self._append_debug_log(f"生成单步公式：{self.debug_moves[0]}；检测模式：{self._debug_expected_description(self.debug_moves[0])}；未重置魔方状态")
        self._debug_update_orientation_state_label()
        self._refresh_debug_label(force=True)
        self.cube.set_guide_move(self._debug_current_move())

    def start_debug_multi(self):
        if self._cube_has_pending_moves():
            self.debug_status_label.setText("动作尚未完成，请稍后再生成公式")
            return
        # 只重开调试任务，不重置当前 3D/实体魔方状态；
        # 但要先把 3D 显示朝向切到下拉框选中的初始朝向，
        # 否则后续 BLE 面转映射会和检测朝向不一致。
        self._apply_debug_selected_orientation_preserving_state()
        self.debug_moves = self._make_debug_sequence(10)
        self.debug_current_orientation = self.debug_initial_orientation
        self.debug_index = 0
        self.debug_active = True
        self.debug_mode = "multi"
        self.debug_match_states = []
        self.debug_progress_completed = 0
        self.debug_progress_total = 0
        self.debug_progress_partial = False
        self._last_debug_chip_key = None
        self._reset_debug_match_states()
        self.debug_status_label.setText(f"多步调试：1/10，请执行 {move_text(self.debug_moves[0])}")
        self._append_debug_log("生成多步公式：" + " ".join(self.debug_moves) + "；未重置魔方状态")
        self._append_debug_log(
            f"初始朝向：{self._orientation_text(self.debug_initial_orientation)}；当前朝向：{self._orientation_text(self.debug_current_orientation)}"
        )
        self._debug_update_orientation_state_label()
        self._debug_auto_advance_rotations(reason="start")
        if self.debug_active:
            current = self._debug_current_move()
            self._append_debug_log(f"第 {self.debug_index + 1} 步检测模式：{self._debug_expected_description(current)}")
        self._refresh_debug_label(force=True)
        self.cube.set_guide_move(self._debug_current_move())

    def _append_debug_log(self, text):
        if not hasattr(self, "debug_log_list"):
            return
        timestamp = time.strftime("%H:%M:%S")
        self.debug_log_list.insertItem(0, f"[{timestamp}] {move_text(text)}")
        while self.debug_log_list.count() > 80:
            self.debug_log_list.takeItem(self.debug_log_list.count() - 1)


    def _debug_is_auto_rotation_move(self, move):
        try:
            move = normalize_move(move)
        except Exception:
            move = str(move or "")
        face, _suffix = _split_move_suffix(move)
        return face in ("x", "y", "z")

    def _debug_auto_advance_rotations(self, reason=""):
        if getattr(self, "debug_mode", None) != "multi" or not getattr(self, "debug_active", False):
            return
        changed = False
        while self.debug_active and self.debug_index < len(self.debug_moves):
            move = self._debug_current_move()
            if not self._debug_is_auto_rotation_move(move):
                break
            step_no = self.debug_index + 1
            before = self.debug_current_orientation
            self._append_debug_log(
                f"自动执行：第 {step_no}/{len(self.debug_moves)} 步 {move}，"
                f"初始朝向 {self._orientation_text(self.debug_initial_orientation)}，"
                f"执行前当前朝向 {self._orientation_text(before)}"
            )
            self._display_debug_completed_move(move)
            self._debug_advance_orientation(move)
            self.debug_index += 1
            changed = True
            self._append_debug_log(
                f"自动完成：{move}；当前朝向 {self._orientation_text(self.debug_current_orientation)}；进入下一步"
            )

        if self.debug_index >= len(self.debug_moves):
            self.debug_active = False
            self.debug_match_states = []
            self.debug_progress_completed = 0
            self.debug_progress_total = 0
            self.debug_progress_partial = False
            self.debug_status_label.setText("调试完成：全部公式执行正确")
            self._append_debug_log(
                f"多步调试完成；初始朝向 {self._orientation_text(self.debug_initial_orientation)}；"
                f"最终当前朝向 {self._orientation_text(self.debug_current_orientation)}"
            )
            if hasattr(self, "cube"):
                self.cube.set_guide_move(None)
            self._debug_update_orientation_state_label()
            return

        if changed:
            next_move = self._debug_current_move()
            self._reset_debug_match_states()
            self.debug_status_label.setText(
                f"自动完成 x/y/z 后：进度 {self.debug_index + 1}/{len(self.debug_moves)}，请执行 {move_text(next_move)}"
            )
            self._append_debug_log(
                f"当前检测：第 {self.debug_index + 1}/{len(self.debug_moves)} 步 {next_move}；"
                f"初始朝向 {self._orientation_text(self.debug_initial_orientation)}；"
                f"当前朝向 {self._orientation_text(self.debug_current_orientation)}；"
                f"检测模式：{self._debug_expected_description(next_move)}"
            )
            if hasattr(self, "cube"):
                self.cube.set_guide_move(next_move)
            self._debug_update_orientation_state_label()

    def _debug_move_changes_orientation(self, move):
        before = getattr(self, "debug_current_orientation", self.debug_initial_orientation)
        return self._orientation_after_move(before, move) != before

    def _commit_cube_move_instant(self, label):
        if not hasattr(self, "cube") or label not in MOVE_DEFS:
            return False
        axis, layer, direction = MOVE_DEFS[label][:3]
        commit_move(
            self.cube.cubies,
            {
                "axis": axis,
                "layer": layer,
                "direction": direction,
                "turns": move_turns(label),
                "label": label,
            },
        )
        return True

    def _finish_cube_animations_instant(self):
        if not hasattr(self, "cube"):
            return
        active = getattr(self.cube, "active_move", None)
        if active is not None:
            try:
                commit_move(self.cube.cubies, active)
            except Exception:
                pass
            self.cube.active_move = None

        queue_obj = getattr(self.cube, "move_queue", None)
        if queue_obj is not None:
            while queue_obj:
                queued_label = queue_obj.popleft()
                self._commit_cube_move_instant(queued_label)
        self.cube.update()

    def _display_debug_completed_move(self, move):
        """Apply a completed debug target to 3D in the same frame as the label.

        b/d/M/E/S/x/y/z 会移动中心块，也就是会改变“当前朝向”。
        之前这些动作先进入动画队列，状态栏立刻切到新朝向，导致短时间
        甚至被延迟 FACELETS 覆盖后，文字朝向和 3D 中心块朝向不一致。
        对改变朝向的调试动作直接提交到 3D，可保证当前朝向和画面同步。
        """
        if not hasattr(self, "cube") or move not in MOVE_DEFS:
            return
        if self._debug_move_changes_orientation(move):
            self._finish_cube_animations_instant()
            if self._commit_cube_move_instant(move):
                self.cube.update()
        else:
            self.cube.enqueue_move(move)


    def _debug_button_simulation_moves(self, label):
        """调试页按钮模拟的是智能魔方实际会上报的底层面转信号。

        例如按钮 u′ 表示公式层面的 u′，但智能魔方只能上报外层面转，
        因此它应当喂给检测器 D′；M 应当喂 L′ 和 R。
        """
        try:
            label = normalize_move(label)
        except Exception:
            label = str(label or "")
        face, suffix = _split_move_suffix(label)

        if face in ("x", "y", "z"):
            return [label]

        slice_pairs = {
            "M": ("L'", "R"),
            "E": ("D'", "U"),
            "S": ("F'", "B"),
        }
        if face in slice_pairs:
            first, second = slice_pairs[face]
            if suffix == "'":
                first = first[:-1] if first.endswith("'") else first + "'"
                second = second[:-1] if second.endswith("'") else second + "'"
            pair = [first, second]
            if suffix == "2":
                return pair + pair
            return pair

        base_face = self._debug_primitive_for_face(face)
        if suffix == "2":
            return [base_face, base_face]
        return [self._debug_apply_suffix(base_face, suffix)]

    def _handle_debug_manual_button(self, label):
        # 按钮是“公式按钮”。调试已开始时，按钮会先拆成智能魔方
        # 实际会上报的底层信号喂给检测器；检测通过后，3D 魔方再执行
        # 当前目标公式本身，保证 M/d/x/y 等会像求解器按钮一样更新朝向。
        if not hasattr(self, "panel_stack") or self.panel_stack.currentIndex() != PAGE_DEBUG:
            self.handle_user_move(label)
            return
        if not self._debug_current_move():
            self.cube.enqueue_move(label)
            self.debug_status_label.setText(f"未开始调试，已执行 {move_text(label)}")
            self._append_debug_log(f"手动转动：{label}（未检测）")
            return
        signals = self._debug_button_simulation_moves(label)
        if signals != [label]:
            self._append_debug_log(
                f"按钮模拟：{label} => " + " + ".join(signals)
            )
        for sig in signals:
            self._handle_debug_move(sig)

    def _handle_debug_move(self, label):
        expected = self._debug_current_move()
        if not expected:
            self.cube.enqueue_move(label)
            self.debug_status_label.setText(f"未开始调试，已执行 {move_text(label)}")
            self._append_debug_log(f"手动转动：{label}（未检测）")
            return

        try:
            actual = normalize_move(label)
        except Exception:
            actual = str(label or "")

        completed, still_possible, inner_progress = self._debug_feed_actual_move(actual)

        if completed:
            # 只在目标公式完整匹配后执行目标公式本身。
            # 这样 M/d/x/y 等改变朝向的公式不会被底层模拟信号破坏 3D 状态。
            self._display_debug_completed_move(expected)
            if self.debug_mode == "single":
                self.debug_active = False
                self.debug_index = 0
                self.debug_match_states = []
                self.debug_progress_completed = 0
                self.debug_progress_total = 0
                self.debug_progress_partial = False
                self._debug_advance_orientation(expected)
                self.debug_status_label.setText(
                    f"正确：{move_text(expected)} 已完成。下一次点击单步调试再生成下一条"
                )
                self._append_debug_log(f"正确：{actual} 完成单步目标 {expected}；等待下一条")
                self.cube.set_guide_move(None)
            else:
                self._debug_advance_orientation(expected)
                self.debug_index += 1
                if self.debug_index >= len(self.debug_moves):
                    self.debug_active = False
                    self.debug_match_states = []
                    self.debug_progress_completed = 0
                    self.debug_progress_total = 0
                    self.debug_progress_partial = False
                    self.debug_status_label.setText("调试完成：全部公式执行正确")
                    self._append_debug_log(f"正确：{actual} 完成 {expected}，调试完成")
                    self.cube.set_guide_move(None)
                else:
                    self._debug_auto_advance_rotations(reason="after_user_move")
                    if self.debug_active:
                        next_move = self._debug_current_move()
                        self._reset_debug_match_states()
                        self.debug_status_label.setText(
                            f"正确：进度 {self.debug_index + 1}/{len(self.debug_moves)}，下一步 {move_text(next_move)}"
                        )
                        self._append_debug_log(
                            f"正确：{actual} 完成 {expected}；初始朝向 {self._orientation_text(self.debug_initial_orientation)}；当前朝向 {self._orientation_text(self.debug_current_orientation)}；下一步 {next_move}；检测模式：{self._debug_expected_description(next_move)}"
                        )
                        self.cube.set_guide_move(next_move)
        elif still_possible:
            self.debug_status_label.setText(
                f"检测中：{move_text(expected)} 已匹配部分输入 {move_text(actual)}，请连续完成剩余动作"
            )
            self._append_debug_log(
                f"部分匹配：目标 {expected}，输入 {actual}，内部进度 {inner_progress or '-'}"
            )
        else:
            old_index = self.debug_index
            if self.debug_mode == "multi":
                self.debug_index = 0
                self.debug_current_orientation = self.debug_initial_orientation
                # 错误后检测进度从头开始，但 3D 不回到“还原魔方”。
                # 它应同步实体魔方当前状态，再按初始朝向摆放显示。
                self._sync_debug_visual_to_physical_initial_orientation(guide_move=None, request_fresh=True)
                self._reset_debug_match_states()
                next_move = self._debug_current_move()
                self.debug_status_label.setText(
                    f"错误：应匹配 {move_text(expected)}，实际 {move_text(actual)}。多步进度已从头开始，3D 已同步实体状态并按初始朝向显示，请执行 {move_text(next_move)}"
                )
                self._append_debug_log(
                    f"错误：第 {old_index + 1}/{len(self.debug_moves)} 步目标 {expected}，实际 {actual}；需要：{self._debug_expected_description(expected)}；已同步实体状态，朝向回到 {self._orientation_text(self.debug_current_orientation)}"
                )
                self._debug_update_orientation_state_label()
                self._debug_auto_advance_rotations(reason="after_error_reset")
                self.cube.set_guide_move(self._debug_current_move())
            else:
                self.debug_index = 0
                self.debug_active = True
                self.debug_current_orientation = self.debug_initial_orientation
                self._sync_debug_visual_to_physical_initial_orientation(guide_move=None, request_fresh=True)
                self._reset_debug_match_states()
                self.debug_status_label.setText(
                    f"错误：应匹配 {move_text(expected)}，实际 {move_text(actual)}。当前单步已重开，3D 已同步实体状态并按初始朝向显示，请重新执行 {move_text(expected)}"
                )
                self._append_debug_log(
                    f"错误：单步目标 {expected}，实际 {actual}；需要：{self._debug_expected_description(expected)}；已同步实体状态，当前公式立即重开"
                )
                self.cube.set_guide_move(expected)

        self._refresh_debug_label(force=True)

    def _refresh_debug_label(self, force=False):
        inner_total = int(getattr(self, "debug_progress_total", 0) or 0)
        inner_completed = int(getattr(self, "debug_progress_completed", 0) or 0)
        inner_partial = bool(getattr(self, "debug_progress_partial", False))
        partial_index = -1
        if (
            getattr(self, "debug_active", False)
            and getattr(self, "debug_moves", [])
            and 0 <= getattr(self, "debug_index", 0) < len(getattr(self, "debug_moves", []))
            and inner_total > 1
            and (inner_completed > 0 or inner_partial)
        ):
            partial_index = getattr(self, "debug_index", 0)

        debug_key = (
            tuple(getattr(self, "debug_moves", [])),
            getattr(self, "debug_index", 0),
            getattr(self, "debug_active", False),
            partial_index,
            inner_completed,
            inner_total,
            inner_partial,
            getattr(self, "debug_initial_orientation", None),
            getattr(self, "debug_current_orientation", None),
        )
        if force or debug_key != getattr(self, "_last_debug_chip_key", None):
            self._last_debug_chip_key = debug_key
            self._set_chip_row(
                self.debug_chips_layout,
                getattr(self, "debug_moves", []),
                current_index=getattr(self, "debug_index", 0),
                active=getattr(self, "debug_active", False),
                partial_index=partial_index,
                empty_text="点击“单步调试”或“多步调试”生成公式",
            )
        self._debug_update_orientation_state_label()
        if hasattr(self, "panel_stack") and self.panel_stack.currentIndex() == PAGE_DEBUG:
            self.cube.set_guide_move(self._debug_current_move())

    def set_gyro_follow_enabled_ui(self, enabled: bool):
        enabled = bool(enabled)
        if hasattr(self, "btn_gyro_toggle"):
            self.btn_gyro_toggle.blockSignals(True)
            self.btn_gyro_toggle.setChecked(enabled)
            self.btn_gyro_toggle.blockSignals(False)
            self.btn_gyro_toggle.setText("陀螺仪：开" if enabled else "陀螺仪：关")
        if hasattr(self, "cube"):
            self.cube.set_gyro_follow_enabled(enabled)

    def toggle_gyro_follow(self):
        enabled = self.btn_gyro_toggle.isChecked()
        self.btn_gyro_toggle.setText("陀螺仪：开" if enabled else "陀螺仪：关")
        self.cube.set_gyro_follow_enabled(enabled)

    def restore_solver_context_after_page_switch(self):
        """从其它页面回到求解器时，优先恢复实体魔方当前状态。

        应用只有一个 3D 魔方实例。旧逻辑在进入求解器时会 reset 到还原态，
        导致启动时已同步的实体乱态丢失。现在切入求解器时先使用蓝牙实体
        状态；没有实体状态时才回退到求解器还原态。
        """
        self.cancel_replay(update_status=False)
        applied = self._sync_ble_physical_state_once_on_page_entry(PAGE_SOLVER)
        if not applied:
            self.reset_solver_cube()
        self.move_history.clear()
        self.solution_guide.stop()
        self._pending_solver_recompute_prefix = None
        self._last_solution_chip_key = None
        if hasattr(self, "solver_status_label"):
            if applied:
                if self.ble_pending_facelets_sync_page == PAGE_SOLVER:
                    self.solver_status_label.setText("正在读取实体魔方当前状态，收到后将同步到求解器")
                else:
                    self.solver_status_label.setText("已同步实体魔方当前状态，点击 Solve 自动求解")
            else:
                self.solver_status_label.setText("暂无实体状态，已按当前初始朝向恢复为还原态")

    def change_solver_initial_orientation(self):
        if not hasattr(self, "orientation_combo"):
            return
        preset = self.orientation_combo.currentData()
        if not preset:
            return
        self.solver_initial_orientation = preset
        if hasattr(self, "panel_stack") and self.panel_stack.currentIndex() == PAGE_SOLVER:
            self.cancel_replay(update_status=False)
            applied = self._apply_ble_physical_state_for_page(PAGE_SOLVER, request_fresh=False)
            if not applied:
                self.reset_solver_cube()
            self.move_history.clear()
            self.solution_guide.stop()
            self._pending_solver_recompute_prefix = None
            self._last_solution_chip_key = None
            self._refresh_solution_label(force=True)
            if hasattr(self, "solver_status_label"):
                self.solver_status_label.setText("已按新朝向同步实体魔方状态" if applied else "已切换初始朝向并重置为还原态")
        else:
            self.apply_solver_initial_orientation()

    def apply_solver_initial_orientation(self):
        """恢复求解器相机缩放；魔方朝向由 reset_solver_cube() 重建状态实现。"""
        if hasattr(self, "cube"):
            self.cube.set_view_preset("solver_default")
            self.cube.zoom = SOLVER_CUBE_ZOOM
            self.cube.update()

    def reset_solver_cube(self):
        """按当前“初始朝向”生成还原态魔方。

        例如 yellow_top_green_front 会把黄色中心放到 U 面、绿色中心放到 F 面，
        后续 U/F/R 等操作都按这个显示朝向执行。
        """
        if hasattr(self, "cube"):
            self.cube.reset_cube(self.solver_initial_orientation)
            self.apply_solver_initial_orientation()

    def process_ble_queue(self):
        while not self.ble_queue.empty():
            try:
                item = self.ble_queue.get_nowait()

                if isinstance(item, tuple):
                    tag = item[0]

                    if tag == "MOVE":
                        self.ble_last_move_meta = item[2] if len(item) > 2 and isinstance(item[2], dict) else None
                        self._record_ble_move_meta(item[1], self.ble_last_move_meta)
                        self._apply_ble_physical_move(item[1])
                        self.handle_ble_move(item[1])

                    elif tag == "HISTORY_REQUEST":
                        self._record_ble_history_request(item)

                    elif tag == "HISTORY_STATUS":
                        self._record_ble_history_status(item)

                    elif tag == "STATUS":
                        _, status, message = item
                        self.ble_status = status
                        self.ble_last_message = message or ""
                        self._update_ble_status()

                    elif tag == "CONNECTED":
                        self.ble_status = "connected"
                        self.ble_device_name = item[1] if len(item) > 1 else "GAN"
                        self.ble_device_address = item[2] if len(item) > 2 else ""
                        self._update_ble_status()

                    elif tag == "PROTOCOL":
                        self.ble_protocol = item[1] if len(item) > 1 else ""
                        self.ble_mac = item[2] if len(item) > 2 else ""
                        self._update_ble_status()

                    elif tag == "BATTERY":
                        self.ble_battery = item[1] if len(item) > 1 else None
                        self._update_ble_status()

                    elif tag == "HARDWARE":
                        self.ble_hardware = item[1] if len(item) > 1 and isinstance(item[1], dict) else {}
                        self._update_ble_status()

                    elif tag == "FACELETS":
                        self.ble_last_facelets = item[2] if len(item) > 2 else None
                        self.ble_last_state = item[3] if len(item) > 3 else None
                        inferred_slice_move = self._maybe_infer_slice_move_from_facelets(self.ble_last_facelets)
                        self._capture_ble_physical_facelets(self.ble_last_facelets)
                        self._apply_initial_ble_facelets_once()
                        if inferred_slice_move:
                            self._record_ble_inferred_slice_move(inferred_slice_move)
                            self.handle_ble_move(inferred_slice_move)

                    elif tag == "DISCONNECT":
                        self.ble_status = "disconnected"
                        self.ble_last_message = item[1] if len(item) > 1 else ""
                        self._update_ble_status()

                    elif tag == "RESET_RESULT":
                        ok = bool(item[1]) if len(item) > 1 else False
                        message = item[2] if len(item) > 2 else ""
                        self.ble_last_message = message
                        if hasattr(self, "debug_status_label"):
                            self.debug_status_label.setText(("蓝牙校准成功：" if ok else "蓝牙校准失败：") + message)

                    elif tag == "GYRO" and ENABLE_GYRO_SYNC:
                        if len(item) >= 5:
                            _, qw, qx, qy, qz = item[:5]
                            if self.btn_gyro_toggle.isChecked():
                                self.cube.set_gyro_quaternion(qw, qx, qy, qz)

                elif isinstance(item, str):
                    self._apply_ble_physical_move(item)
                    self.handle_ble_move(item)

            except queue.Empty:
                break

    def _record_ble_move_meta(self, move, meta):
        if not isinstance(meta, dict):
            return

        serial = meta.get("serial")
        self.ble_last_move_serial = serial

        tags = []
        if meta.get("recovered"):
            self.ble_recovered_move_count += 1
            tags.append("recovered")
        else:
            tags.append("realtime")

        if meta.get("history_gap_unrecovered"):
            self.ble_unrecovered_gap_count += 1
            missing = meta.get("missing_before", "?")
            expected = meta.get("expected_serial", "?")
            tags.append(f"gap-unrecovered missing={missing} expected={expected}")

        tag_text = ", ".join(tags)
        self.ble_last_history_message = f"MOVE {move} serial={serial} {tag_text}"

        if self.panel_stack.currentIndex() == PAGE_DEBUG and hasattr(self, "debug_log_list"):
            display_tag = "补帧 recovered" if meta.get("recovered") else "实时 realtime"
            if meta.get("history_gap_unrecovered"):
                display_tag += f"；缺步未补回 {meta.get('missing_before', '?')} 步"
            self._append_debug_log(f"蓝牙 {move} serial={serial} [{display_tag}]")

        self._update_ble_status()

    def _record_ble_history_request(self, item):
        serial = item[1] if len(item) > 1 else "?"
        count = item[2] if len(item) > 2 else "?"
        self.ble_last_history_message = f"请求历史补帧：serial={serial}, count={count}"
        if self.panel_stack.currentIndex() == PAGE_DEBUG and hasattr(self, "debug_log_list"):
            self._append_debug_log(self.ble_last_history_message)
        self._update_ble_status()

    def _record_ble_history_status(self, item):
        kind = item[1] if len(item) > 1 else ""
        if kind == "requested":
            expected = item[2] if len(item) > 2 else "?"
            got = item[3] if len(item) > 3 else "?"
            missing = item[4] if len(item) > 4 else "?"
            retry = item[5] if len(item) > 5 else "?"
            message = f"检测到缺号：期望 serial={expected}，收到 serial={got}，缺 {missing} 步；请求历史第 {retry} 次"
        elif kind == "received":
            start = item[2] if len(item) > 2 else "?"
            count = item[3] if len(item) > 3 else "?"
            inserted = item[4] if len(item) > 4 else "?"
            message = f"收到历史包：start={start}，count={count}，插入 {inserted} 步"
        elif kind == "unrecovered":
            expected = item[2] if len(item) > 2 else "?"
            got = item[3] if len(item) > 3 else "?"
            missing = item[4] if len(item) > 4 else "?"
            retry = item[5] if len(item) > 5 else "?"
            message = f"历史补帧失败：期望 serial={expected}，实际 serial={got}，仍缺 {missing} 步；已重试 {retry} 次后继续"
        else:
            message = "历史补帧状态：" + repr(item[1:])

        self.ble_last_history_message = message
        if self.panel_stack.currentIndex() == PAGE_DEBUG and hasattr(self, "debug_log_list"):
            self._append_debug_log(message)
        self._update_ble_status()

    def _cubies_signature(self, cubies):
        """Stable signature for comparing cube states without caring about object identity."""
        result = []
        for cubie in cubies or []:
            pos = tuple(int(round(v)) for v in cubie.pos)
            stickers = tuple(sorted(
                (tuple(int(round(x)) for x in normal), color_key)
                for normal, color_key in cubie.stickers.items()
            ))
            result.append((pos, stickers))
        return tuple(sorted(result))

    def _infer_slice_move_between_native_cubies(self, before_cubies, after_cubies):
        """Infer a single native M/E/S move from two complete FACELETS states."""
        if not before_cubies or not after_cubies:
            return None
        try:
            target = self._cubies_signature(after_cubies)
            candidates = ["M", "M'", "M2", "E", "E'", "E2", "S", "S'", "S2"]
            for move in candidates:
                trial = copy.deepcopy(before_cubies)
                axis, layer, direction = MOVE_DEFS[move][:3]
                commit_move(
                    trial,
                    {
                        "axis": axis,
                        "layer": layer,
                        "direction": direction,
                        "turns": move_turns(move),
                        "label": move,
                    },
                )
                if self._cubies_signature(trial) == target:
                    return move
        except Exception:
            return None
        return None

    def _maybe_infer_slice_move_from_facelets(self, facelets):
        """Use spontaneous FACELETS packets to recover M/E/S moves.

        GAN cubes often use normal MOVE packets for outer-layer turns, but M/E/S
        can arrive as state/FACELETS changes or unknown middle-layer packets.
        Page-entry/startup FACELETS are full-state syncs and must not be treated
        as moves; only spontaneous FACELETS after initial sync are used here.
        """
        if not facelets:
            return None
        if not getattr(self, "ble_initial_facelets_applied", False):
            return None
        if getattr(self, "ble_pending_facelets_sync_page", None) is not None:
            return None
        if not getattr(self, "ble_physical_state_ready", False):
            return None
        try:
            new_cubies = facelets_to_cubies(facelets, TIMER_INITIAL_ORIENTATION)
        except Exception:
            return None

        sources = []
        if getattr(self, "ble_physical_cubies_native", None) is not None:
            sources.append(self.ble_physical_cubies_native)
        if getattr(self, "ble_last_full_state_cubies_native", None) is not None:
            # This fallback is important when an M/E/S physical turn caused an
            # incomplete outer-face MOVE before the complete FACELETS packet.
            sources.append(self.ble_last_full_state_cubies_native)

        seen = set()
        for source in sources:
            try:
                key = self._cubies_signature(source)
            except Exception:
                key = id(source)
            if key in seen:
                continue
            seen.add(key)
            move = self._infer_slice_move_between_native_cubies(source, new_cubies)
            if move:
                return move
        return None

    def _record_ble_inferred_slice_move(self, move):
        self.ble_last_history_message = f"FACELETS 推断中层动作：{move}"
        if hasattr(self, "panel_stack") and self.panel_stack.currentIndex() == PAGE_DEBUG and hasattr(self, "debug_log_list"):
            self._append_debug_log(f"蓝牙状态推断：{move}（M/E/S 由 FACELETS 补识别）")
        self._update_ble_status()

    def _request_ble_facelets_for_page(self, index, reason="page-entry"):
        """Request one fresh physical state for a specific page entry.

        The next FACELETS packet is allowed to update the 3D cube only if the
        user is still on the same non-formula page. Other FACELETS packets are
        stored for tracking but do not continuously overwrite the visible cube.
        """
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            return False
        if getattr(self, "ble_status", "") != "connected":
            return False
        self.ble_pending_facelets_sync_page = index
        self.ble_pending_facelets_sync_reason = reason
        try:
            self.ble_control_queue.put_nowait("REQUEST_FACELETS")
            return True
        except Exception:
            self.ble_pending_facelets_sync_page = None
            self.ble_pending_facelets_sync_reason = ""
            return False

    def _sync_ble_physical_state_once_on_page_entry(self, index):
        """Synchronize physical state once when entering timer/solver/debug.

        Page entry applies the cached tracked physical state immediately, then
        asks the GAN cube for one fresh FACELETS packet. Only that pending packet
        may overwrite the visible 3D cube; later FACELETS packets are stored but
        not continuously applied.
        """
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            return False
        requested = self._request_ble_facelets_for_page(index, reason="page-entry")
        applied_cached = self._apply_ble_physical_state_for_page(index, request_fresh=False)
        if requested:
            self.ble_last_message = "已按缓存状态显示，正在读取实体魔方当前状态" if applied_cached else "正在读取实体魔方当前状态"
            self._update_ble_status()
            return True
        return applied_cached

    def _orientation_for_physical_sync_page(self, index):
        if index == PAGE_SOLVER:
            return getattr(self, "solver_initial_orientation", TIMER_INITIAL_ORIENTATION)
        if index == PAGE_DEBUG:
            # 调试页的 3D 显示必须跟“当前朝向”一致，而不是始终回到
            # 初始朝向。页面进入时请求的 FACELETS 可能会延迟返回；
            # 如果用户已经完成了 b/d/M/x 等会改变中心朝向的步骤，
            # 再按 debug_initial_orientation 覆盖 3D，就会出现状态栏显示
            # “当前朝向：橙顶蓝前”，但 3D 仍按“黄顶绿前”显示的错位。
            return getattr(
                self,
                "debug_current_orientation",
                getattr(self, "debug_initial_orientation", TIMER_INITIAL_ORIENTATION),
            )
        return TIMER_INITIAL_ORIENTATION

    def _capture_ble_physical_facelets(self, facelets):
        """Store the latest complete GAN state in native white-top/green-front axes."""
        if not facelets:
            return False
        try:
            cubies = facelets_to_cubies(facelets, TIMER_INITIAL_ORIENTATION)
            self.ble_physical_cubies_native = cubies
            self.ble_last_full_state_cubies_native = copy.deepcopy(cubies)
            self.ble_physical_state_ready = True
            self.ble_physical_move_count = 0
            return True
        except Exception as exc:
            self.ble_last_message = f"实体状态读取失败：{exc}"
            if hasattr(self, "debug_status_label"):
                self.debug_status_label.setText(self.ble_last_message)
            self._update_ble_status()
            return False

    def _apply_ble_physical_move(self, label):
        """Keep the stored physical cube state current even on pages that do not show it."""
        if not getattr(self, "ble_physical_state_ready", False):
            return
        if label not in MOVE_DEFS:
            return
        try:
            axis, layer, direction = MOVE_DEFS[label][:3]
            commit_move(
                self.ble_physical_cubies_native,
                {
                    "axis": axis,
                    "layer": layer,
                    "direction": direction,
                    "turns": move_turns(label),
                    "label": label,
                },
            )
            self.ble_physical_move_count += 1
        except Exception as exc:
            self.ble_last_message = f"实体状态跟踪失败：{exc}"
            self._update_ble_status()

    def _apply_ble_physical_state_for_page(self, index=None, request_fresh=False):
        """Apply the stored physical state to the shared 3D cube for non-formula pages."""
        if index is None and hasattr(self, "panel_stack"):
            index = self.panel_stack.currentIndex()
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            return False
        if request_fresh:
            if self._request_ble_facelets_for_page(index, reason="explicit"):
                return True
        if not getattr(self, "ble_physical_state_ready", False):
            return False
        if not hasattr(self, "cube") or self.ble_physical_cubies_native is None:
            return False

        try:
            cubies = copy.deepcopy(self.ble_physical_cubies_native)
            orientation = self._orientation_for_physical_sync_page(index)
            cubies = orient_cubies(cubies, orientation)
            self.cube.cubies = cubies
            self.cube.move_queue.clear()
            self.cube.active_move = None
            self.cube.update()
            return True
        except Exception as exc:
            self.ble_last_message = f"实体状态同步到 3D 失败：{exc}"
            if hasattr(self, "debug_status_label"):
                self.debug_status_label.setText(self.ble_last_message)
            self._update_ble_status()
            return False

    def _apply_initial_ble_facelets_once(self):
        """Apply FACELETS only for startup or one pending page-entry sync.

        Later FACELETS packets keep the stored physical state fresh, but they do
        not overwrite the visible 3D cube unless a page entry explicitly requested
        one sync. This prevents timer/solver/debug from being continuously
        re-synchronized while the user is staying on that page.
        """
        if not hasattr(self, "panel_stack"):
            return
        index = self.panel_stack.currentIndex()
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            if self.ble_pending_facelets_sync_page is not None:
                self.ble_pending_facelets_sync_page = None
                self.ble_pending_facelets_sync_reason = ""
            self._update_ble_status()
            return

        pending_page = getattr(self, "ble_pending_facelets_sync_page", None)
        apply_reason = None
        if not self.ble_initial_facelets_applied:
            apply_reason = "startup"
        elif pending_page == index:
            apply_reason = "page-entry"
        elif pending_page is not None:
            # A delayed response for a page the user has already left: store it
            # for tracking, but do not apply it to the current 3D cube.
            self.ble_pending_facelets_sync_page = None
            self.ble_pending_facelets_sync_reason = ""

        if apply_reason is None:
            self._update_ble_status()
            return

        if not self._apply_ble_physical_state_for_page(index, request_fresh=False):
            return

        self.ble_initial_facelets_applied = True
        self.ble_pending_facelets_sync_page = None
        self.ble_pending_facelets_sync_reason = ""
        if apply_reason == "startup":
            self.ble_last_message = "已在启动连接时同步一次实体魔方状态"
        else:
            self.ble_last_message = "已在进入页面时同步一次实体魔方状态"
        if hasattr(self, "debug_status_label") and index == PAGE_DEBUG:
            self.debug_status_label.setText(self.ble_last_message)
            if apply_reason == "page-entry":
                self._append_debug_log("已消费本次页面进入 FACELETS，同步一次实体状态")
        if hasattr(self, "solver_status_label") and index == PAGE_SOLVER:
            self.solver_status_label.setText("已同步实体魔方当前状态，点击 Solve 自动求解")
        self._update_ble_status()

    def _update_ble_status(self):
        mapping = {
            "connected": ("GAN 已连接", "#16c76a"),
            "connecting": ("正在连接", "#ffae2b"),
            "scanning": ("正在扫描", "#ffae2b"),
            "error": ("连接失败", "#ff4d4f"),
            "disconnected": ("未连接", "#ff4d4f"),
            "off": ("BLE 关闭", "#6b7b8f"),
        }

        text, color = mapping.get(self.ble_status, ("未连接", "#ff4d4f"))
        details = []
        if self.ble_status == "connected":
            if getattr(self, "ble_protocol", ""):
                details.append(self.ble_protocol)
            if getattr(self, "ble_battery", None) is not None:
                details.append(f"{self.ble_battery}%")
            hardware_name = ""
            if isinstance(getattr(self, "ble_hardware", None), dict):
                hardware_name = self.ble_hardware.get("hardwareName") or ""
            if hardware_name:
                details.append(hardware_name)
        elif getattr(self, "ble_last_message", "") and self.ble_status in ("error", "disconnected"):
            details.append(str(self.ble_last_message)[:24])

        if details:
            text = f"{text} · {' · '.join(details)}"

        self.ble_label.setText(text)
        self.ble_label.setToolTip(self._ble_status_tooltip())
        self.ble_dot.setStyleSheet(f"color: {color}; font-size: 20px;")

    def _ble_status_tooltip(self):
        lines = []
        if getattr(self, "ble_device_name", ""):
            lines.append(f"设备：{self.ble_device_name}")
        if getattr(self, "ble_device_address", ""):
            lines.append(f"地址：{self.ble_device_address}")
        if getattr(self, "ble_protocol", ""):
            lines.append(f"协议：{self.ble_protocol}")
        if getattr(self, "ble_mac", ""):
            lines.append(f"MAC：{self.ble_mac}")
        if getattr(self, "ble_battery", None) is not None:
            lines.append(f"电量：{self.ble_battery}%")
        hardware = getattr(self, "ble_hardware", None)
        if isinstance(hardware, dict) and hardware:
            if hardware.get("hardwareName"):
                lines.append(f"硬件：{hardware.get('hardwareName')}")
            if hardware.get("hardwareVersion"):
                lines.append(f"硬件版本：{hardware.get('hardwareVersion')}")
            if hardware.get("softwareVersion"):
                lines.append(f"固件版本：{hardware.get('softwareVersion')}")
            if hardware.get("productDate"):
                lines.append(f"生产日期：{hardware.get('productDate')}")
            if "gyroSupported" in hardware:
                lines.append(f"陀螺仪支持：{'是' if hardware.get('gyroSupported') else '否'}")
        if getattr(self, "ble_last_facelets", None):
            lines.append(f"最近状态：serial 已接收，facelets={self.ble_last_facelets}")
        if getattr(self, "ble_physical_state_ready", False):
            lines.append(f"实体状态跟踪：已启用，累计 move={getattr(self, 'ble_physical_move_count', 0)}")
        if getattr(self, "ble_last_move_serial", None) is not None:
            lines.append(f"最近 move serial：{self.ble_last_move_serial}")
        if getattr(self, "ble_recovered_move_count", 0):
            lines.append(f"已补回 move：{self.ble_recovered_move_count}")
        if getattr(self, "ble_unrecovered_gap_count", 0):
            lines.append(f"未补回缺口：{self.ble_unrecovered_gap_count}")
        if getattr(self, "ble_last_history_message", ""):
            lines.append(f"历史补帧：{self.ble_last_history_message}")
        if getattr(self, "ble_last_message", ""):
            lines.append(f"消息：{self.ble_last_message}")
        return "\n".join(lines)

    def map_ble_move_to_current_orientation(self, label):
        """把 GAN 固定配色坐标系的面转映射到当前页面的显示坐标系。

        GAN 上报的 U/D/F/B/R/L 不是“当前屏幕上的上/下/前/后/右/左”，
        而是固定配色面。比如黄顶绿前时，物理顶层是黄色，GAN 通常会上报 D；
        此时应映射成显示坐标系的 U，3D 魔方才会转动屏幕上的黄色顶面。
        公式练习同样使用当前“预览朝向”下拉框来映射智能魔方面转。
        """
        face, suffix = _split_move_suffix(label)

        # 计时训练固定白顶绿前，直接使用 GAN 原始面转即可。
        if not hasattr(self, "panel_stack"):
            return label

        current_index = self.panel_stack.currentIndex()
        if current_index == PAGE_SOLVER:
            orientation = getattr(self, "solver_initial_orientation", TIMER_INITIAL_ORIENTATION)
        elif current_index == PAGE_FORMULA_TRAINING:
            orientation = getattr(self, "formula_training_current_orientation", getattr(self, "formula_training_initial_orientation", getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION)))
        elif current_index == PAGE_FORMULA_PRACTICE:
            orientation = getattr(self, "formula_practice_initial_orientation", TIMER_INITIAL_ORIENTATION)
        elif current_index == PAGE_DEBUG:
            orientation = getattr(self, "debug_current_orientation", getattr(self, "debug_initial_orientation", TIMER_INITIAL_ORIENTATION))
        else:
            return label
        if face in _NATIVE_FACE_TO_VEC:
            oriented_vec = native_to_oriented_vec(_NATIVE_FACE_TO_VEC[face], orientation)
            mapped_face = _VEC_TO_DISPLAY_FACE.get(oriented_vec, face)
            return mapped_face + suffix

        # M/E/S 是中层动作，不是普通外层面。它们也需要随当前
        # 顶/前朝向映射，否则 FACELETS 推断出的 native M/S/E 会和
        # 调试/公式检测的显示坐标系不一致。
        if face in ("M", "E", "S") and label in MOVE_DEFS:
            axis, layer, direction = MOVE_DEFS[label][:3]
            if layer != 0:
                return label
            axis_vecs = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
            oriented_axis = native_to_oriented_vec(axis_vecs.get(axis, (0, 0, 0)), orientation)
            axis_info = {
                (1, 0, 0): ("x", 1), (-1, 0, 0): ("x", -1),
                (0, 1, 0): ("y", 1), (0, -1, 0): ("y", -1),
                (0, 0, 1): ("z", 1), (0, 0, -1): ("z", -1),
            }.get(oriented_axis)
            if not axis_info:
                return label
            mapped_axis, sign = axis_info
            mapped_direction = direction * sign
            turns = move_turns(label)
            for candidate in ("M", "M'", "M2", "E", "E'", "E2", "S", "S'", "S2"):
                cand_axis, cand_layer, cand_direction = MOVE_DEFS[candidate][:3]
                if cand_layer != 0 or cand_axis != mapped_axis or move_turns(candidate) != turns:
                    continue
                if turns == 2 or cand_direction == mapped_direction:
                    return candidate

        return label

    def handle_ble_move(self, label):
        self.handle_user_move(self.map_ble_move_to_current_orientation(label))

    def handle_user_move(self, label):
        current = self.panel_stack.currentIndex()

        if current == PAGE_TIMER:
            self.timer_practice.handle_user_move(label)
            self.cube.enqueue_move(label)
            self._refresh_timer_ui(force=True)
            return

        if current == PAGE_SOLVER:
            self._handle_solver_move(label)
            return

        if current == PAGE_DEBUG:
            self._handle_debug_move(label)
            return

        if current in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            self._handle_formula_practice_move(label)
            return

        self.cube.enqueue_move(label)

    def _handle_solver_move(self, label):
        # 如果上一处错误正在等待动画提交后重算，则后续输入也只进入当前真实状态，
        # 暂时不再拿旧引导步骤比较，避免连续做错时越算越偏。
        if getattr(self, "_pending_solver_recompute_prefix", None):
            self.move_history.append(label)
            self.cube.enqueue_move(label)
            self.solver_status_label.setText("动作执行中，完成后将按当前状态重新计算")
            self._refresh_solution_label(force=True)
            return

        result = None
        finished_after_this_move = False

        if self.solution_guide.active:
            result = self.solution_guide.handle_user_move(label)

            if result.correct:
                if result.finished:
                    self.solver_status_label.setText("还原完成")
                    finished_after_this_move = True
                elif result.message and result.message != "正确":
                    self.solver_status_label.setText(result.message)
                else:
                    self.solver_status_label.setText(f"正确：下一步 {self.solution_guide.current_move()}")
            else:
                self.solver_status_label.setText(result.message)

        self.move_history.append(label)
        self.cube.enqueue_move(label)

        if finished_after_this_move:
            self.move_history.clear()
            self._pending_solver_recompute_prefix = None
            self._refresh_solution_label(force=True)
            return

        if result is not None and result.recompute:
            # enqueue_move 只把动作放入动画队列，cubies 会在动画结束时才真正 commit。
            # 之前这里立刻 solve_auto(self.cube.cubies)，会基于“错误步尚未生效”的旧状态求解，
            # 导致用户做错几步后继续按提示走也无法复原。现在改为等 move_committed 信号后再重算。
            self._pending_solver_recompute_prefix = "已自动重新计算"
            self.solution_guide.stop()
            self.cube.set_guide_move(None)
            self.solver_status_label.setText("动作执行中，完成后将按当前状态重新计算")
            self._refresh_solution_label(force=True)
        else:
            self._refresh_solution_label(force=True)

    def _cube_has_pending_moves(self):
        return bool(getattr(self.cube, "active_move", None) is not None or getattr(self.cube, "move_queue", None))

    def _consume_pending_solver_recompute_if_idle(self):
        prefix = getattr(self, "_pending_solver_recompute_prefix", None)
        if not prefix:
            return
        if self._cube_has_pending_moves():
            return

        self._pending_solver_recompute_prefix = None
        self.recompute_solution_from_current_state(prefix)

    def on_cube_move_committed(self, label):
        if self.panel_stack.currentIndex() == PAGE_TIMER:
            self.timer_practice.check_solved(self.cube.cubies)
            self._refresh_timer_ui(force=True)
            return

        if self.panel_stack.currentIndex() == PAGE_SOLVER:
            self._consume_pending_solver_recompute_if_idle()
            return

        if self.panel_stack.currentIndex() == PAGE_DEBUG:
            self._refresh_debug_label(force=True)

    def new_scramble(self):
        self.cancel_replay(update_status=False)
        # 计时训练的新打乱只刷新打乱步骤和计时状态，
        # 不重置当前 3D/实体魔方状态。用户可直接从手上魔方当前状态开始打乱。
        self.move_history.clear()
        self.solution_guide.stop()
        if hasattr(self, "cube"):
            self.cube.set_guide_move(None)
        self.timer_practice.start_new_scramble()
        self._last_scramble_chip_key = None
        self._last_cfop_key = None
        self._refresh_timer_ui(force=True)

    def toggle_observation(self):
        self.timer_practice.toggle_observation_mode()
        self._refresh_timer_ui(force=True)

    def give_up_timer(self):
        self.timer_practice.abandon()
        self._refresh_timer_ui(force=True)

    def update_timer_practice(self):
        if self.panel_stack.currentIndex() == PAGE_TIMER:
            self.timer_practice.update(1.0 / 60.0)
            self._refresh_timer_ui(force=False)
        elif self.panel_stack.currentIndex() in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            if (
                getattr(self, "formula_training_active", False)
                and getattr(self, "formula_training_guide", None) is not None
                and self.formula_training_guide.active
                and self.formula_training_started_at is not None
            ):
                self.formula_training_elapsed_ms = int((time.perf_counter() - self.formula_training_started_at) * 1000)
                self._refresh_formula_training_timer_text(force=False)

    def _refresh_timer_ui(self, force=False):
        state_map = {
            "idle": "计时练习",
            "scrambling": "请完成打乱",
            "observing": "观察时间",
            "timing": "正在计时",
            "finished": "还原完成",
            "abandoned": "已放弃",
            "invalid": "打乱错误",
        }

        current_state_text = state_map.get(self.timer_practice.state, "计时练习")
        self.timer_state_label.setText(current_state_text)
        self.timer_time_label.setText(self.timer_practice.main_time_text())
        if hasattr(self, "scramble_title_label"):
            self.scramble_title_label.hide()

        display_moves = self.timer_practice.display_scramble_moves()
        partial_index = self.timer_practice.display_partial_index()

        scramble_key = (
            tuple(display_moves),
            self.timer_practice.scramble_index,
            self.timer_practice.state,
            partial_index,
        )

        if force or scramble_key != self._last_scramble_chip_key:
            self._last_scramble_chip_key = scramble_key
            self._set_chip_row(
                self.scramble_chips_layout,
                display_moves,
                current_index=self.timer_practice.scramble_index,
                active=self.timer_practice.state == "scrambling",
                partial_index=partial_index,
                empty_text="点击“新打乱”生成 WCA 风格打乱",
            )

        self.timer_status_label.setText("")
        self.timer_status_label.hide()
        self.btn_toggle_observe.setText("观察：不限" if self.timer_practice.observation_unlimited else "观察：15秒")

        total_ms = self.timer_practice.final_time_ms
        if total_ms is None:
            total_ms = int(self.timer_practice.elapsed * 1000)

        tps = self.timer_practice.solve_moves / (total_ms / 1000.0) if total_ms > 0 else 0
        self.summary_label.setText(f"步数 {self.timer_practice.solve_moves}    TPS {tps:.2f}")

        cfop_key = tuple(
            (s.get("name"), s.get("time_ms"), s.get("moves"), s.get("tps"))
            for s in self.timer_practice.cfop_stats
        )

        if force or cfop_key != self._last_cfop_key:
            self._last_cfop_key = cfop_key
            self._refresh_cfop_table(self.timer_practice.cfop_stats)

        history_key = tuple(
            (rec.get("timestamp"), rec.get("time_text"), rec.get("result"))
            for rec in self.timer_practice.history[:20]
        )

        if force or history_key != self._last_history_key:
            self._last_history_key = history_key
            self.timer_history_list.clear()
            for rec in self.timer_practice.history[:20]:
                text = rec.get("time_text", "DNF")
                scramble = rec.get("scramble", "")
                item = QListWidgetItem(f"{text}    {scramble[:34]}")
                item.setData(Qt.UserRole, rec)
                self.timer_history_list.addItem(item)
            self._refresh_today_practice()

    def _refresh_cfop_table(self, stats):
        self.cfop_table.setRowCount(8)
        for row, stat in enumerate(stats):
            values = [
                stat.get("name", ""),
                stat.get("time_text", "00:00:000"),
                str(stat.get("moves", 0)),
                f"{float(stat.get('tps', 0.0)):.2f}",
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                self.cfop_table.setItem(row, col, item)

    def replay_selected_history(self):
        item = self.timer_history_list.currentItem()
        if item is None:
            return

        rec = item.data(Qt.UserRole)
        if not isinstance(rec, dict):
            return

        scramble = rec.get("scramble", "").split()
        move_log = rec.get("move_log", [])
        replay_moves = [
            {
                "move": m.get("move"),
                "time_ms": int(m.get("time_ms", 0)),
            }
            for m in move_log
            if isinstance(m, dict) and m.get("move")
        ]

        if not replay_moves:
            self.timer_status_label.setText("该记录没有可回放的解法步骤")
            return

        self.cancel_replay(update_status=False)

        # 直接加载该记录的打乱状态，不播放打乱动画，节约时间。
        self.cube.reset_cube(TIMER_INITIAL_ORIENTATION)
        self.cube.apply_moves_instant(scramble)
        self.cube.move_queue.clear()
        self.cube.active_move = None

        self.replay_record = rec
        self.replay_moves = replay_moves
        self.replay_index = 0
        self.replay_elapsed_ms = 0
        self.replay_paused = False

        self.btn_pause_replay.setText("暂停回放")
        self.btn_pause_replay.show()
        self.btn_cancel_replay.show()

        self.timer_status_label.setText("正在按原始时间回放历史解法")
        self.replay_timer.start()

    def tick_replay(self):
        if self.replay_paused or not self.replay_moves:
            return

        self.replay_elapsed_ms += self.replay_timer.interval()

        while self.replay_index < len(self.replay_moves):
            entry = self.replay_moves[self.replay_index]
            target_ms = int(entry.get("time_ms", 0))

            if self.replay_elapsed_ms < target_ms:
                break

            move = entry.get("move")
            if move:
                self.cube.enqueue_move(move)

            self.replay_index += 1

        if self.replay_index >= len(self.replay_moves):
            self.replay_timer.stop()
            self.btn_pause_replay.hide()
            self.btn_cancel_replay.hide()
            self.timer_status_label.setText("历史回放完成")

    def toggle_replay_pause(self):
        if not self.replay_moves:
            return

        self.replay_paused = not self.replay_paused
        self.btn_pause_replay.setText("继续回放" if self.replay_paused else "暂停回放")
        self.timer_status_label.setText("回放已暂停" if self.replay_paused else "正在按原始时间回放历史解法")

    def cancel_replay(self, update_status=True):
        self.replay_timer.stop()
        self.replay_record = None
        self.replay_moves = []
        self.replay_index = 0
        self.replay_elapsed_ms = 0
        self.replay_paused = False

        if hasattr(self, "btn_pause_replay"):
            self.btn_pause_replay.hide()
            self.btn_cancel_replay.hide()
            self.btn_pause_replay.setText("暂停回放")

        if update_status and hasattr(self, "timer_status_label"):
            self.timer_status_label.setText("已取消观看回放")

    def show_segment_stats_popup(self):
        stats = list(self.timer_practice.cfop_stats)

        item = self.timer_history_list.currentItem()
        if item is not None:
            rec = item.data(Qt.UserRole)
            if isinstance(rec, dict) and rec.get("cfop_stats"):
                stats = list(rec.get("cfop_stats"))

        self.hide_segment_stats_popup()
        popup = SegmentStatsPopup(stats, self)
        popup.adjustSize()
        x = self.btn_segment_stats.mapToGlobal(QPoint(0, 0)).x()
        y = self.btn_segment_stats.mapToGlobal(QPoint(0, 0)).y() - popup.height() - self._px(8)
        popup.move(QPoint(x, max(0, y)))
        popup.show()
        self.segment_stats_popup = popup

    def hide_segment_stats_popup(self):
        popup = getattr(self, "segment_stats_popup", None)
        if popup is not None:
            popup.close()
            self.segment_stats_popup = None

    def eventFilter(self, obj, event):
        if obj is getattr(self, "btn_segment_stats", None):
            if event.type() == QEvent.Enter:
                self.show_segment_stats_popup()
            elif event.type() == QEvent.Leave:
                self.hide_segment_stats_popup()
        return super().eventFilter(obj, event)

    def toggle_solver_mode(self):
        self.solve_mode = "auto" if self.solve_mode == "history" else "history"
        self.btn_mode.setText("模式：自动" if self.solve_mode == "auto" else "模式：倒推")
        self.solution_guide.stop()
        self._pending_solver_recompute_prefix = None
        self.cube.set_guide_move(None)
        self.solver_status_label.setText(
            "自动求解：需要 pip install kociemba"
            if self.solve_mode == "auto"
            else "倒推求解：根据历史步骤反向还原"
        )
        self._refresh_solution_label(force=True)

    def start_solution(self):
        if self._cube_has_pending_moves():
            self.solver_status_label.setText("动作尚未完成，请稍后再 Solve")
            return
        self._pending_solver_recompute_prefix = None

        if self.solve_mode == "history":
            if not self.move_history:
                self.solution_guide.stop()
                self.solver_status_label.setText("当前没有打乱历史，魔方应已复原")
                self._refresh_solution_label(force=True)
                return

            solution = solve_from_history(self.move_history)
            if not solution:
                self.solver_status_label.setText("已经没有需要还原的步骤")
                self._refresh_solution_label(force=True)
                return

            self.solution_guide.start(solution)
            self.solver_status_label.setText(f"倒推求解：{len(solution)} 步")
        else:
            result = solve_auto(self.cube.cubies)
            if not result.ok:
                self.solution_guide.stop()
                self.solver_status_label.setText(result.message)
                self._refresh_solution_label(force=True)
                return

            if not result.moves:
                self.solution_guide.stop()
                self.move_history.clear()
                self.solver_status_label.setText(result.message)
                self._refresh_solution_label(force=True)
                return

            self.solution_guide.start(result.moves)
            self.solver_status_label.setText(result.message)

        self._refresh_solution_label(force=True)

    def recompute_solution_from_current_state(self, prefix="已自动重新计算"):
        if self._cube_has_pending_moves():
            self._pending_solver_recompute_prefix = prefix
            return

        if self.solve_mode == "history":
            if not self.move_history:
                self.solution_guide.stop()
                self.solver_status_label.setText("当前已无历史步骤")
                self._refresh_solution_label(force=True)
                return

            solution = solve_from_history(self.move_history)
            if not solution:
                self.solution_guide.stop()
                self.solver_status_label.setText("已经还原")
                self._refresh_solution_label(force=True)
                return

            self.solution_guide.start(solution)
            self.solver_status_label.setText(f"{prefix}：剩余 {len(solution)} 步")
        else:
            result = solve_auto(self.cube.cubies)
            if not result.ok:
                self.solution_guide.stop()
                self.solver_status_label.setText(result.message)
                self._refresh_solution_label(force=True)
                return

            if not result.moves:
                self.solution_guide.stop()
                self.move_history.clear()
                self.solver_status_label.setText("已经还原")
                self._refresh_solution_label(force=True)
                return

            self.solution_guide.start(result.moves)
            self.solver_status_label.setText(f"{prefix}：剩余 {len(result.moves)} 步")

        self._refresh_solution_label(force=True)

    def exit_solution(self):
        self.solution_guide.stop()
        self._pending_solver_recompute_prefix = None
        self.cube.set_guide_move(None)
        self.solver_status_label.setText("已退出引导")
        self._refresh_solution_label(force=True)

    def _refresh_solution_label(self, force=False):
        partial_index = self.solution_guide.display_partial_index()
        solution_key = (
            tuple(self.solution_guide.solution_moves),
            self.solution_guide.current_index,
            self.solution_guide.active,
            partial_index,
        )

        if force or solution_key != self._last_solution_chip_key:
            self._last_solution_chip_key = solution_key

            if not self.solution_guide.solution_moves:
                self._set_chip_row(
                    self.solution_chips_layout,
                    [],
                    empty_text="暂无还原步骤",
                )
            else:
                self._set_chip_row(
                    self.solution_chips_layout,
                    self.solution_guide.solution_moves,
                    current_index=self.solution_guide.current_index,
                    active=self.solution_guide.active,
                    partial_index=partial_index,
                    empty_text="暂无还原步骤",
                )

        if self.panel_stack.currentIndex() == PAGE_SOLVER:
            self.cube.set_guide_move(self.solution_guide.current_move())

    def random_scramble_solver(self):
        moves = generate_scramble(20)
        self.cancel_replay(update_status=False)
        self.solution_guide.stop()
        self._pending_solver_recompute_prefix = None
        self.cube.set_guide_move(None)
        self.reset_solver_cube()
        self.cube.apply_moves_instant(moves)
        self.move_history = list(moves)
        self._last_solution_chip_key = None
        self.solver_status_label.setText(f"已随机打乱：{len(moves)} 步，点击 Solve 自动求解")
        self._refresh_solution_label(force=True)

    def reset_all(self):
        self.cancel_replay(update_status=False)
        if self.panel_stack.currentIndex() == PAGE_SOLVER:
            self.reset_solver_cube()
        elif self.panel_stack.currentIndex() == PAGE_DEBUG:
            self.reset_debug_cube()
        else:
            self.cube.reset_cube(TIMER_INITIAL_ORIENTATION)
        self.move_history.clear()
        self.solution_guide.stop()
        self._pending_solver_recompute_prefix = None
        self.timer_practice.reset_session()
        self._last_scramble_chip_key = None
        self._last_solution_chip_key = None
        self._last_cfop_key = None
        self._refresh_timer_ui(force=True)
        self._refresh_solution_label(force=True)
        if self.panel_stack.currentIndex() == PAGE_DEBUG and hasattr(self, "debug_status_label"):
            self.debug_status_label.setText("已重置")
        elif hasattr(self, "solver_status_label"):
            self.solver_status_label.setText("已重置")

    def _request_ble_facelets_for_page(self, index, reason="page-entry"):
        """Request one fresh physical state for a specific page entry.

        The next FACELETS packet is allowed to update the 3D cube only if the
        user is still on the same non-formula page. Other FACELETS packets are
        stored for tracking but do not continuously overwrite the visible cube.
        """
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            return False
        if getattr(self, "ble_status", "") != "connected":
            return False
        self.ble_pending_facelets_sync_page = index
        self.ble_pending_facelets_sync_reason = reason
        try:
            self.ble_control_queue.put_nowait("REQUEST_FACELETS")
            return True
        except Exception:
            self.ble_pending_facelets_sync_page = None
            self.ble_pending_facelets_sync_reason = ""
            return False

    def _sync_ble_physical_state_once_on_page_entry(self, index):
        """Synchronize physical state once when entering timer/solver/debug.

        Page entry applies the cached tracked physical state immediately, then
        asks the GAN cube for one fresh FACELETS packet. Only that pending packet
        may overwrite the visible 3D cube; later FACELETS packets are stored but
        not continuously applied.
        """
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            return False
        requested = self._request_ble_facelets_for_page(index, reason="page-entry")
        applied_cached = self._apply_ble_physical_state_for_page(index, request_fresh=False)
        if requested:
            self.ble_last_message = "已按缓存状态显示，正在读取实体魔方当前状态" if applied_cached else "正在读取实体魔方当前状态"
            self._update_ble_status()
            return True
        return applied_cached

    def _orientation_for_physical_sync_page(self, index):
        if index == PAGE_SOLVER:
            return getattr(self, "solver_initial_orientation", TIMER_INITIAL_ORIENTATION)
        if index == PAGE_DEBUG:
            # 调试页的 3D 显示必须跟“当前朝向”一致，而不是始终回到
            # 初始朝向。页面进入时请求的 FACELETS 可能会延迟返回；
            # 如果用户已经完成了 b/d/M/x 等会改变中心朝向的步骤，
            # 再按 debug_initial_orientation 覆盖 3D，就会出现状态栏显示
            # “当前朝向：橙顶蓝前”，但 3D 仍按“黄顶绿前”显示的错位。
            return getattr(
                self,
                "debug_current_orientation",
                getattr(self, "debug_initial_orientation", TIMER_INITIAL_ORIENTATION),
            )
        return TIMER_INITIAL_ORIENTATION

    def _capture_ble_physical_facelets(self, facelets):
        """Store the latest complete GAN state in native white-top/green-front axes."""
        if not facelets:
            return False
        try:
            cubies = facelets_to_cubies(facelets, TIMER_INITIAL_ORIENTATION)
            self.ble_physical_cubies_native = cubies
            self.ble_last_full_state_cubies_native = copy.deepcopy(cubies)
            self.ble_physical_state_ready = True
            self.ble_physical_move_count = 0
            return True
        except Exception as exc:
            self.ble_last_message = f"实体状态读取失败：{exc}"
            if hasattr(self, "debug_status_label"):
                self.debug_status_label.setText(self.ble_last_message)
            self._update_ble_status()
            return False

    def _apply_ble_physical_move(self, label):
        """Keep the stored physical cube state current even on pages that do not show it."""
        if not getattr(self, "ble_physical_state_ready", False):
            return
        if label not in MOVE_DEFS:
            return
        try:
            axis, layer, direction = MOVE_DEFS[label][:3]
            commit_move(
                self.ble_physical_cubies_native,
                {
                    "axis": axis,
                    "layer": layer,
                    "direction": direction,
                    "turns": move_turns(label),
                    "label": label,
                },
            )
            self.ble_physical_move_count += 1
        except Exception as exc:
            self.ble_last_message = f"实体状态跟踪失败：{exc}"
            self._update_ble_status()

    def _apply_ble_physical_state_for_page(self, index=None, request_fresh=False):
        """Apply the stored physical state to the shared 3D cube for non-formula pages."""
        if index is None and hasattr(self, "panel_stack"):
            index = self.panel_stack.currentIndex()
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            return False
        if request_fresh:
            if self._request_ble_facelets_for_page(index, reason="explicit"):
                return True
        if not getattr(self, "ble_physical_state_ready", False):
            return False
        if not hasattr(self, "cube") or self.ble_physical_cubies_native is None:
            return False

        try:
            cubies = copy.deepcopy(self.ble_physical_cubies_native)
            orientation = self._orientation_for_physical_sync_page(index)
            cubies = orient_cubies(cubies, orientation)
            self.cube.cubies = cubies
            self.cube.move_queue.clear()
            self.cube.active_move = None
            self.cube.update()
            return True
        except Exception as exc:
            self.ble_last_message = f"实体状态同步到 3D 失败：{exc}"
            if hasattr(self, "debug_status_label"):
                self.debug_status_label.setText(self.ble_last_message)
            self._update_ble_status()
            return False

    def _apply_initial_ble_facelets_once(self):
        """Apply FACELETS only for startup or one pending page-entry sync.

        Later FACELETS packets keep the stored physical state fresh, but they do
        not overwrite the visible 3D cube unless a page entry explicitly requested
        one sync. This prevents timer/solver/debug from being continuously
        re-synchronized while the user is staying on that page.
        """
        if not hasattr(self, "panel_stack"):
            return
        index = self.panel_stack.currentIndex()
        if index in (PAGE_FORMULA_PRACTICE, PAGE_FORMULA_TRAINING):
            if self.ble_pending_facelets_sync_page is not None:
                self.ble_pending_facelets_sync_page = None
                self.ble_pending_facelets_sync_reason = ""
            self._update_ble_status()
            return

        pending_page = getattr(self, "ble_pending_facelets_sync_page", None)
        apply_reason = None
        if not self.ble_initial_facelets_applied:
            apply_reason = "startup"
        elif pending_page == index:
            apply_reason = "page-entry"
        elif pending_page is not None:
            # A delayed response for a page the user has already left: store it
            # for tracking, but do not apply it to the current 3D cube.
            self.ble_pending_facelets_sync_page = None
            self.ble_pending_facelets_sync_reason = ""

        if apply_reason is None:
            self._update_ble_status()
            return

        if not self._apply_ble_physical_state_for_page(index, request_fresh=False):
            return

        self.ble_initial_facelets_applied = True
        self.ble_pending_facelets_sync_page = None
        self.ble_pending_facelets_sync_reason = ""
        if apply_reason == "startup":
            self.ble_last_message = "已在启动连接时同步一次实体魔方状态"
        else:
            self.ble_last_message = "已在进入页面时同步一次实体魔方状态"
        if hasattr(self, "debug_status_label") and index == PAGE_DEBUG:
            self.debug_status_label.setText(self.ble_last_message)
            if apply_reason == "page-entry":
                self._append_debug_log("已消费本次页面进入 FACELETS，同步一次实体状态")
        if hasattr(self, "solver_status_label") and index == PAGE_SOLVER:
            self.solver_status_label.setText("已同步实体魔方当前状态，点击 Solve 自动求解")
        self._update_ble_status()

    def _update_ble_status(self):
        mapping = {
            "connected": ("GAN 已连接", "#16c76a"),
            "connecting": ("正在连接", "#ffae2b"),
            "scanning": ("正在扫描", "#ffae2b"),
            "error": ("连接失败", "#ff4d4f"),
            "disconnected": ("未连接", "#ff4d4f"),
            "off": ("BLE 关闭", "#6b7b8f"),
        }

        text, color = mapping.get(self.ble_status, ("未连接", "#ff4d4f"))
        details = []
        if self.ble_status == "connected":
            if getattr(self, "ble_protocol", ""):
                details.append(self.ble_protocol)
            if getattr(self, "ble_battery", None) is not None:
                details.append(f"{self.ble_battery}%")
            hardware_name = ""
            if isinstance(getattr(self, "ble_hardware", None), dict):
                hardware_name = self.ble_hardware.get("hardwareName") or ""
            if hardware_name:
                details.append(hardware_name)
        elif getattr(self, "ble_last_message", "") and self.ble_status in ("error", "disconnected"):
            details.append(str(self.ble_last_message)[:24])

        if details:
            text = f"{text} · {' · '.join(details)}"

        self.ble_label.setText(text)
        self.ble_label.setToolTip(self._ble_status_tooltip())
        self.ble_dot.setStyleSheet(f"color: {color}; font-size: 20px;")

    def _ble_status_tooltip(self):
        lines = []
        if getattr(self, "ble_device_name", ""):
            lines.append(f"设备：{self.ble_device_name}")
        if getattr(self, "ble_device_address", ""):
            lines.append(f"地址：{self.ble_device_address}")
        if getattr(self, "ble_protocol", ""):
            lines.append(f"协议：{self.ble_protocol}")
        if getattr(self, "ble_mac", ""):
            lines.append(f"MAC：{self.ble_mac}")
        if getattr(self, "ble_battery", None) is not None:
            lines.append(f"电量：{self.ble_battery}%")
        hardware = getattr(self, "ble_hardware", None)
        if isinstance(hardware, dict) and hardware:
            if hardware.get("hardwareName"):
                lines.append(f"硬件：{hardware.get('hardwareName')}")
            if hardware.get("hardwareVersion"):
                lines.append(f"硬件版本：{hardware.get('hardwareVersion')}")
            if hardware.get("softwareVersion"):
                lines.append(f"固件版本：{hardware.get('softwareVersion')}")
            if hardware.get("productDate"):
                lines.append(f"生产日期：{hardware.get('productDate')}")
            if "gyroSupported" in hardware:
                lines.append(f"陀螺仪支持：{'是' if hardware.get('gyroSupported') else '否'}")
        if getattr(self, "ble_last_facelets", None):
            lines.append(f"最近状态：serial 已接收，facelets={self.ble_last_facelets}")
        if getattr(self, "ble_physical_state_ready", False):
            lines.append(f"实体状态跟踪：已启用，累计 move={getattr(self, 'ble_physical_move_count', 0)}")
        if getattr(self, "ble_last_move_serial", None) is not None:
            lines.append(f"最近 move serial：{self.ble_last_move_serial}")
        if getattr(self, "ble_recovered_move_count", 0):
            lines.append(f"已补回 move：{self.ble_recovered_move_count}")
        if getattr(self, "ble_unrecovered_gap_count", 0):
            lines.append(f"未补回缺口：{self.ble_unrecovered_gap_count}")
        if getattr(self, "ble_last_history_message", ""):
            lines.append(f"历史补帧：{self.ble_last_history_message}")
        if getattr(self, "ble_last_message", ""):
            lines.append(f"消息：{self.ble_last_message}")
        return "\n".join(lines)

    def _apply_style(self):
        app = QApplication.instance()
        if app is not None:
            app.setFont(QFont("Microsoft YaHei UI", max(8, self._px(9))))

        self.setStyleSheet(f"""
            QMainWindow {{
                background: #dff1ff;
            }}

            QLabel {{
                color: #243246;
                font-size: {self._px(10)}px;
            }}

            #TitleLabel {{
                font-size: {self._px(19)}px;
                font-weight: 700;
            }}

            #BleDot {{
                font-size: {self._px(18)}px;
            }}

            #HeaderLabel {{
                color: #4d6178;
                font-size: {self._px(11)}px;
                font-weight: 700;
                padding-left: {self._px(3)}px;
            }}

            #ResolutionCombo {{
                min-height: {self._px(28)}px;
                padding: {self._px(3)}px {self._px(10)}px;
                border-radius: {self._px(14)}px;
                background: rgba(255, 255, 255, 150);
                border: 1px solid rgba(255, 255, 255, 190);
                font-weight: 700;
                font-size: {self._px(10)}px;
            }}

            #Pill {{
                background: rgba(255, 255, 255, 150);
                border: 1px solid rgba(255, 255, 255, 190);
                border-radius: {self._px(14)}px;
                padding: {self._px(5)}px {self._px(12)}px;
                font-weight: 600;
                font-size: {self._px(10)}px;
            }}

            #PillButton {{
                min-height: {self._px(28)}px;
                padding: {self._px(5)}px {self._px(12)}px;
                border-radius: {self._px(14)}px;
                background: rgba(255, 255, 255, 150);
                border: 1px solid rgba(255, 255, 255, 190);
                font-weight: 700;
                font-size: {self._px(10)}px;
            }}

            #PillButton:checked {{
                background: #1476ff;
                color: white;
            }}

            #NavPanel, #GlassCard, #CubeStage {{
                background: rgba(255, 255, 255, 135);
                border: 1px solid rgba(255, 255, 255, 190);
                border-radius: {self._px(18)}px;
            }}

            #ChipBox {{
                background: rgba(255, 255, 255, 120);
                border-radius: {self._px(12)}px;
                padding: {self._px(3)}px;
            }}

            #InnerCard {{
                background: rgba(255, 255, 255, 80);
                border: 1px solid rgba(255, 255, 255, 130);
                border-radius: {self._px(14)}px;
            }}

            #ManualBox, #PreviewBox {{
                background: rgba(240, 247, 255, 115);
                border: 1px solid rgba(255, 255, 255, 180);
                border-radius: {self._px(15)}px;
            }}

            QScrollArea {{
                background: transparent;
                border: none;
            }}

            QPushButton {{
                min-height: {self._px(28)}px;
                padding: {self._px(4)}px {self._px(10)}px;
                border-radius: {self._px(14)}px;
                border: 1px solid rgba(255, 255, 255, 160);
                background: rgba(238, 246, 255, 190);
                color: #243246;
                font-weight: 600;
                font-size: {self._px(10)}px;
            }}

            QPushButton:hover {{
                background: rgba(255, 255, 255, 230);
            }}

            QPushButton:checked {{
                background: #1476ff;
                color: white;
            }}

            #NavButton {{
                text-align: left;
                padding-left: {self._px(14)}px;
            }}

            #PrimaryButton {{
                background: #1476ff;
                color: white;
            }}

            #PrimaryButton:hover {{
                background: #2d8cff;
            }}

            #PurpleButton {{
                background: #7b4ce3;
                color: white;
            }}

            #PurpleButton:hover {{
                background: #8d60ee;
            }}

            #DangerButton {{
                background: #ff5b4f;
                color: white;
            }}

            #DangerButton:hover {{
                background: #ff7066;
            }}

            #MoveButton {{
                min-width: {self._px(38)}px;
                padding: {self._px(3)}px {self._px(7)}px;
                border-radius: {self._px(10)}px;
            }}

            #SectionTitle {{
                font-size: {self._px(15)}px;
                font-weight: 700;
            }}

            #TimerLabel {{
                font-size: {self._px(34)}px;
                font-weight: 700;
                color: #1476ff;
            }}

            #MutedLabel {{
                color: #60758f;
                font-size: {self._px(10)}px;
            }}

            #HistoryList {{
                background: rgba(255, 255, 255, 110);
                border: none;
                border-radius: {self._px(12)}px;
                padding: {self._px(6)}px;
                font-size: {self._px(10)}px;
            }}

            #StatsPopup {{
                background: rgba(235, 246, 255, 245);
                border: 1px solid rgba(255, 255, 255, 220);
                border-radius: {self._px(15)}px;
            }}

            #StatsTable {{
                background: rgba(255, 255, 255, 150);
                border: none;
                border-radius: {self._px(10)}px;
                gridline-color: rgba(120, 150, 180, 80);
                font-size: {self._px(10)}px;
            }}

            #StatsTable::item {{
                padding: {self._px(2)}px;
            }}

            QHeaderView::section {{
                background: rgba(210, 226, 245, 130);
                border: none;
                padding: {self._px(3)}px;
                font-weight: 700;
                color: #4d6078;
                font-size: {self._px(10)}px;
            }}

            #MethodCard, #CaseCard {{
                background: rgba(255, 255, 255, 110);
                border: 1px solid rgba(255, 255, 255, 185);
                border-radius: {self._px(16)}px;
            }}

            #MethodCard:hover, #CaseCard:hover {{
                background: rgba(255, 255, 255, 160);
            }}

            #BadgeLabel {{
                background: rgba(20, 118, 255, 25);
                color: #1476ff;
                border: 1px solid rgba(20, 118, 255, 55);
                border-radius: {self._px(9)}px;
                padding: {self._px(2)}px {self._px(8)}px;
                font-weight: 700;
                font-size: {self._px(10)}px;
            }}

            #CardTitle {{
                font-size: {self._px(15)}px;
                font-weight: 700;
                color: #243246;
            }}

            #CardHint {{
                color: #5f7491;
                font-weight: 600;
                font-size: {self._px(10)}px;
            }}

            #CardStats {{
                color: #40536c;
                font-size: {self._px(12)}px;
                font-weight: 600;
            }}
        """)
