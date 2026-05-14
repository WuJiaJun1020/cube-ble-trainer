import math
from collections import deque

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QSizePolicy
from OpenGL.GL import *
from OpenGL.GLU import gluPerspective

from animation import choose_move_duration, ease_in_out_cubic, make_move
from config import AA_SAMPLE_OPTIONS, COLORS, CUBIE_SIZE, DEFAULT_AA_SAMPLES, SPACING, STICKER_RADIUS, STICKER_SIZE
from cube_model import AXIS_VECTOR, MOVE_DEFS, commit_move, create_solved_cube, facelets_to_cubies, is_in_layer, layer_center
from math3d import Quaternion, vec_add, vec_mul


def normalize_aa_samples(samples):
    try:
        samples = int(samples)
    except Exception:
        samples = DEFAULT_AA_SAMPLES
    valid = tuple(int(v) for v in AA_SAMPLE_OPTIONS)
    if samples in valid:
        return samples
    return min(valid, key=lambda value: abs(value - samples))


def make_gl_format(samples):
    fmt = QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(normalize_aa_samples(samples))
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    return fmt


class CubeGLWidget(QOpenGLWidget):
    move_committed = Signal(str)

    def __init__(self, parent=None, aa_samples=DEFAULT_AA_SAMPLES):
        super().__init__(parent)
        self.aa_samples = normalize_aa_samples(aa_samples)
        self.setFormat(make_gl_format(self.aa_samples))
        self.cubies = create_solved_cube()
        self.move_queue = deque()
        self.active_move = None
        self.rot_x = 26.0
        self.rot_y = -34.0
        self.zoom = -8.0
        self.guide_move = None
        self.elapsed = 0.0

        self.use_gyro = False
        self.gyro_follow_enabled = True
        self.target_quat = Quaternion()
        self.current_quat = Quaternion()
        self.base_quat = Quaternion()
        self._gyro_initialized = False
        self._dragging = False
        self._last_pos = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.tick)
        self._timer.start(int(1000 / 60))
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(120, 80)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_antialiasing_samples(self, samples):
        self.aa_samples = normalize_aa_samples(samples)
        self.setFormat(make_gl_format(self.aa_samples))
        self.update()

    def _apply_antialiasing_state(self):
        if self.aa_samples > 0:
            glEnable(GL_MULTISAMPLE)
            glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)
        else:
            glDisable(GL_MULTISAMPLE)

    def reset_cube(self, orientation_preset=None):
        self.cubies = create_solved_cube(orientation_preset)
        self.move_queue.clear()
        self.active_move = None
        self.reset_gyro_origin()
        self.update()

    def set_cube_from_facelets(self, facelets, orientation_preset=None):
        """Replace the 3D cube with a complete state read from the GAN cube."""
        self.cubies = facelets_to_cubies(facelets, orientation_preset)
        self.move_queue.clear()
        self.active_move = None
        self.update()

    def set_view_preset(self, preset=None):
        # 求解器的“初始朝向”现在通过重建魔方状态实现，
        # 相机保持固定的上前视角：上方显示当前 U 面，正面显示当前 F 面。
        self.rot_x = 26.0
        self.rot_y = -34.0
        self.update()

    def enqueue_move(self, label):
        if label in MOVE_DEFS:
            self.move_queue.append(label)

    def apply_moves_instant(self, moves):
        for label in moves:
            if label in MOVE_DEFS:
                move = make_move(label)
                commit_move(self.cubies, move)
        self.update()

    def replay_from_scramble(self, scramble_moves, solve_moves):
        self.reset_cube()
        self.apply_moves_instant(scramble_moves)
        self.move_queue.clear()
        self.active_move = None
        for label in solve_moves:
            self.enqueue_move(label)
        self.update()

    def set_guide_move(self, label):
        self.guide_move = label if label in MOVE_DEFS else None
        self.update()

    def set_gyro_follow_enabled(self, enabled: bool):
        self.gyro_follow_enabled = bool(enabled)

        if not self.gyro_follow_enabled:
            self.use_gyro = False
        else:
            # 重新启用时以下一次陀螺仪数据作为新的当前基准，避免突然跳变。
            self._gyro_initialized = False

        self.update()

    def set_gyro_quaternion(self, qw, qx, qy, qz):
        if not self.gyro_follow_enabled:
            return

        q = Quaternion(qw, qx, qy, qz).normalize()

        if not self._gyro_initialized:
            self.current_quat = q
            self.target_quat = q
            self.base_quat = q.inverse()
            self._gyro_initialized = True
        else:
            self.target_quat = q

        self.use_gyro = True
        self.update()

    def reset_gyro_origin(self):
        if self.use_gyro:
            self.base_quat = self.current_quat.inverse()

    def tick(self):
        dt = 1.0 / 60.0
        self.elapsed += dt
        if self.use_gyro:
            self.current_quat = self.current_quat.slerp(self.target_quat, 0.22)

        if self.active_move is None and self.move_queue:
            duration = choose_move_duration(len(self.move_queue))
            self.active_move = make_move(self.move_queue.popleft(), duration)
        if self.active_move is not None:
            self.active_move["progress"] += dt / self.active_move["duration"]
            t = min(1.0, self.active_move["progress"])
            self.active_move["angle"] = self.active_move.get("target_angle", 90.0) * ease_in_out_cubic(t)
            if t >= 1.0:
                self.active_move["angle"] = self.active_move.get("target_angle", 90.0)
                label = self.active_move["label"]
                commit_move(self.cubies, self.active_move)
                self.active_move = None
                self.move_committed.emit(label)
        self.update()

    def initializeGL(self):
        glEnable(GL_DEPTH_TEST)
        glDepthFunc(GL_LEQUAL)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self._apply_antialiasing_state()
        glEnable(GL_LINE_SMOOTH)
        glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)
        glShadeModel(GL_SMOOTH)
        glClearColor(0.86, 0.94, 1.0, 1.0)

    def resizeGL(self, w, h):
        glViewport(0, 0, max(1, w), max(1, h))

    def paintGL(self):
        self._apply_antialiasing_state()
        w = max(1, self.width())
        h = max(1, self.height())
        self._draw_background(w, h)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(45.0, w / h, 0.1, 100.0)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glTranslatef(0, 0, self.zoom)
        glRotatef(self.rot_x, 1, 0, 0)
        glRotatef(self.rot_y, 0, 1, 0)

        if self.use_gyro:
            (self.base_quat * self.current_quat).apply_gl()

        # 不再绘制地面椭圆线，避免它们出现在魔方表面附近造成干扰。
        self._draw_cube()
        if self.guide_move:
            self._draw_move_arrow(self.guide_move)

    def _draw_background(self, w, h):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glMatrixMode(GL_PROJECTION)
        glPushMatrix()
        glLoadIdentity()
        glOrtho(0, w, h, 0, -1, 1)
        glMatrixMode(GL_MODELVIEW)
        glPushMatrix()
        glLoadIdentity()
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_TEXTURE_2D)
        glBegin(GL_QUADS)
        glColor4f(0.86, 0.94, 1.0, 1.0)
        glVertex2f(0, 0)
        glVertex2f(w, 0)
        glColor4f(0.72, 0.88, 1.0, 1.0)
        glVertex2f(w, h)
        glVertex2f(0, h)
        glEnd()
        glEnable(GL_DEPTH_TEST)
        glMatrixMode(GL_MODELVIEW)
        glPopMatrix()
        glMatrixMode(GL_PROJECTION)
        glPopMatrix()

    def _draw_floor_rings(self):
        glDisable(GL_TEXTURE_2D)
        glLineWidth(1.2)
        glColor4f(1.0, 1.0, 1.0, 0.30)
        y = -1.95
        for rx, rz in [(3.1, 1.45), (2.2, 1.02), (1.25, 0.58)]:
            glBegin(GL_LINE_LOOP)
            for i in range(128):
                a = 2 * math.pi * i / 128
                glVertex3f(math.cos(a) * rx, y, math.sin(a) * rz)
            glEnd()

    def _draw_cube(self):
        # Draw a matte-black inner core first so the gaps between cubies never
        # reveal the light blue background. This removes the light/white hairline
        # look around cubie boundaries while keeping the sticker spacing.
        self._draw_core()
        for cubie in self.cubies:
            glPushMatrix()
            if self.active_move is not None and is_in_layer(cubie, self.active_move["axis"], self.active_move["layer"]):
                axis_vec = AXIS_VECTOR[self.active_move["axis"]]
                angle = self.active_move["direction"] * self.active_move["angle"]
                glRotatef(angle, *axis_vec)
            self._draw_cubie(cubie)
            glPopMatrix()

    def _draw_core(self):
        # Half-extent chosen to sit just behind the inner faces of the outer
        # layer cubies, filling visible seams without protruding through them.
        self._draw_box((0.0, 0.0, 0.0), 1.18, draw_edges=False, uniform_color=COLORS["BODY_DARK"])

    def _draw_cubie(self, cubie):
        center = (cubie.pos[0] * SPACING, cubie.pos[1] * SPACING, cubie.pos[2] * SPACING)
        self._draw_box(center, CUBIE_SIZE)
        for normal, color_key in cubie.stickers.items():
            self._draw_sticker(center, normal, color_key)

    def _draw_box(self, center, size, draw_edges=True, uniform_color=None):
        cx, cy, cz = center
        s = size / 2
        v = [
            (cx - s, cy - s, cz - s), (cx + s, cy - s, cz - s), (cx + s, cy + s, cz - s), (cx - s, cy + s, cz - s),
            (cx - s, cy - s, cz + s), (cx + s, cy - s, cz + s), (cx + s, cy + s, cz + s), (cx - s, cy + s, cz + s),
        ]
        if uniform_color is not None:
            faces = [
                ([0, 1, 2, 3], uniform_color), ([4, 5, 6, 7], uniform_color), ([0, 4, 7, 3], uniform_color),
                ([1, 5, 6, 2], uniform_color), ([3, 2, 6, 7], uniform_color), ([0, 1, 5, 4], uniform_color),
            ]
        else:
            # Keep the body nearly uniform matte black so edges read black rather
            # than bright specular ridges.
            faces = [
                ([0, 1, 2, 3], COLORS["BODY_DARK"]), ([4, 5, 6, 7], COLORS["BODY"]), ([0, 4, 7, 3], COLORS["BODY"]),
                ([1, 5, 6, 2], COLORS["BODY"]), ([3, 2, 6, 7], COLORS["BODY"]), ([0, 1, 5, 4], COLORS["BODY_DARK"]),
            ]
        glBegin(GL_QUADS)
        for face, color in faces:
            glColor3f(*color)
            for idx in face:
                glVertex3f(*v[idx])
        glEnd()

        if draw_edges:
            # Draw crisp opaque black edges. Disabling line smoothing/blending for
            # these lines avoids semi-transparent fringe pixels that can read as
            # white hairlines against the light background.
            glDisable(GL_BLEND)
            glDisable(GL_LINE_SMOOTH)
            glLineWidth(2.0)
            glColor3f(*COLORS["BODY_DARK"])
            glBegin(GL_LINES)
            for a, b in [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7)]:
                glVertex3f(*v[a])
                glVertex3f(*v[b])
            glEnd()
            glEnable(GL_BLEND)

    def _sticker_axes(self, normal):
        if normal == (0, 1, 0): return (1, 0, 0), (0, 0, -1)
        if normal == (0, -1, 0): return (1, 0, 0), (0, 0, 1)
        if normal == (0, 0, 1): return (1, 0, 0), (0, 1, 0)
        if normal == (0, 0, -1): return (-1, 0, 0), (0, 1, 0)
        if normal == (1, 0, 0): return (0, 0, -1), (0, 1, 0)
        if normal == (-1, 0, 0): return (0, 0, 1), (0, 1, 0)
        return (1, 0, 0), (0, 1, 0)

    def _draw_rounded_rect_on_face(self, center, normal, color, size, radius, z_offset):
        u, v = self._sticker_axes(normal)
        c = vec_add(center, vec_mul(normal, CUBIE_SIZE / 2 + z_offset))
        half = size / 2
        r = min(radius, half * 0.45)
        corners = [
            ((-half + r, -half + r), math.pi, 1.5 * math.pi),
            ((half - r, -half + r), 1.5 * math.pi, 2.0 * math.pi),
            ((half - r, half - r), 0.0, 0.5 * math.pi),
            ((-half + r, half - r), 0.5 * math.pi, math.pi),
        ]
        points = []
        for (corner_x, corner_y), start, end in corners:
            for i in range(8):
                a = start + (end - start) * i / 7
                x = corner_x + math.cos(a) * r
                y = corner_y + math.sin(a) * r
                points.append(vec_add(vec_add(c, vec_mul(u, x)), vec_mul(v, y)))
        glColor3f(*color)
        glBegin(GL_TRIANGLE_FAN)
        glVertex3f(*c)
        for p in points:
            glVertex3f(*p)
        glVertex3f(*points[0])
        glEnd()

    def _draw_sticker(self, center, normal, color_key):
        self._draw_rounded_rect_on_face(center, normal, COLORS["STICKER_BORDER"], STICKER_SIZE + 0.09, STICKER_RADIUS + 0.025, 0.008)
        self._draw_rounded_rect_on_face(center, normal, COLORS[color_key], STICKER_SIZE, STICKER_RADIUS, 0.014)

    def _move_planes(self, move_label):
        if not move_label or move_label not in MOVE_DEFS:
            return []
        axis, layer, direction = MOVE_DEFS[move_label]

        # The guide ring must wrap around the center of the moving layer, not be
        # shifted outside the cube.  Use the same layer coordinates as the cube
        # model, then make the ring radius larger than the cube footprint so the
        # guide never cuts through the stickers visually.
        layer_offset = SPACING
        guide_radius = 2.58
        center_radius = 2.50

        def axes_for(axis_name, layer_value, radius):
            if axis_name == "y":
                return (0.0, layer_value * layer_offset, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, -1.0), radius, direction
            if axis_name == "z":
                return (0.0, 0.0, layer_value * layer_offset), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), radius, direction
            return (layer_value * layer_offset, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0), radius, direction

        if layer == "all":
            # x/y/z whole-cube rotations: draw guides around all three layers.
            return [axes_for(axis, lv, guide_radius) for lv in (-1, 0, 1)]
        if isinstance(layer, tuple):
            # Wide moves such as r/u/f rotate two layers. Draw one guide around
            # each moving layer.
            return [axes_for(axis, lv, guide_radius) for lv in layer]
        return [axes_for(axis, layer_center(layer), center_radius if layer == 0 else guide_radius)]

    def _point(self, center, u, v, radius, angle):
        return (
            center[0] + u[0] * math.cos(angle) * radius + v[0] * math.sin(angle) * radius,
            center[1] + u[1] * math.cos(angle) * radius + v[1] * math.sin(angle) * radius,
            center[2] + u[2] * math.cos(angle) * radius + v[2] * math.sin(angle) * radius,
        )

    def _draw_arrow_head(self, tip, prev, center, scale=1.0):
        # Build a large 3D arrow head in the arrow plane.  It is deliberately
        # oversized to read like the common cube-app guide arrows: black outline,
        # opaque white fill, and a thin blue accent.
        tangent = (tip[0] - prev[0], tip[1] - prev[1], tip[2] - prev[2])
        tl = math.sqrt(tangent[0] ** 2 + tangent[1] ** 2 + tangent[2] ** 2) or 1.0
        tangent = (tangent[0] / tl, tangent[1] / tl, tangent[2] / tl)
        radial = (tip[0] - center[0], tip[1] - center[1], tip[2] - center[2])
        rl = math.sqrt(radial[0] ** 2 + radial[1] ** 2 + radial[2] ** 2) or 1.0
        radial = (radial[0] / rl, radial[1] / rl, radial[2] / rl)
        length = 0.54 * scale
        width = 0.44 * scale

        def add(a, b): return (a[0] + b[0], a[1] + b[1], a[2] + b[2])
        def mul(a, k): return (a[0] * k, a[1] * k, a[2] * k)
        base = add(tip, mul(tangent, -length))
        left = add(base, mul(radial, width))
        right = add(base, mul(radial, -width))

        outline_base = add(tip, mul(tangent, -length * 1.18))
        outline_left = add(outline_base, mul(radial, width * 1.28))
        outline_right = add(outline_base, mul(radial, -width * 1.28))
        glColor4f(0.02, 0.05, 0.10, 0.84)
        glBegin(GL_TRIANGLES)
        glVertex3f(*tip); glVertex3f(*outline_left); glVertex3f(*outline_right)
        glEnd()

        glColor4f(1.0, 1.0, 1.0, 0.99)
        glBegin(GL_TRIANGLES)
        glVertex3f(*tip); glVertex3f(*left); glVertex3f(*right)
        glEnd()

        glLineWidth(3.0)
        glColor4f(0.20, 0.58, 1.0, 0.95)
        glBegin(GL_LINE_LOOP)
        glVertex3f(*tip); glVertex3f(*left); glVertex3f(*right)
        glEnd()

    def _draw_arrow_segment(self, center, u, v, radius, direction, start_angle, span):
        end_angle = start_angle + direction * span
        points = [self._point(center, u, v, radius, start_angle + (end_angle - start_angle) * (i / 64)) for i in range(65)]

        # Very thick dark outline, then a broad opaque white band.  The target is
        # roughly the visual width of one cube layer rather than a thin sketch
        # line.
        glLineWidth(34.0)
        glColor4f(0.02, 0.05, 0.10, 0.70)
        glBegin(GL_LINE_STRIP)
        for p in points:
            glVertex3f(*p)
        glEnd()

        glLineWidth(24.0)
        glColor4f(1.0, 1.0, 1.0, 0.98)
        glBegin(GL_LINE_STRIP)
        for p in points:
            glVertex3f(*p)
        glEnd()

        glLineWidth(4.0)
        glColor4f(0.18, 0.56, 1.0, 0.92)
        glBegin(GL_LINE_STRIP)
        for p in points:
            glVertex3f(*p)
        glEnd()

        self._draw_arrow_head(points[-1], points[-5], center, scale=max(0.92, radius / 2.25))

    def _draw_single_move_arrow(self, center, u, v, radius, direction, phase_offset=0.0):
        # Rotate continuously so the guide is visibly alive.  Draw two arrows on
        # the same orbit, 180 degrees apart, matching the two-arrow reference.
        phase = self.elapsed * 1.35 * direction + phase_offset
        span = math.radians(126)
        for i in range(2):
            self._draw_arrow_segment(center, u, v, radius, direction, phase + i * math.pi, span)

    def _draw_move_arrow(self, move_label):
        planes = self._move_planes(move_label)
        if not planes:
            return

        # Draw the guide arrows in the same 3D depth space as the cube.  The
        # old overlay-style drawing disabled depth testing, so the part of an
        # arrow that should be behind the cube was still visible and visually
        # noisy.  Keep depth testing enabled, but avoid writing guide geometry
        # into the depth buffer so the cube remains the occlusion source.
        glDisable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        if self.aa_samples > 0:
            glEnable(GL_LINE_SMOOTH)
            glHint(GL_LINE_SMOOTH_HINT, GL_NICEST)
        glEnable(GL_DEPTH_TEST)
        glDepthMask(GL_FALSE)

        # All moving-layer guide rings share the exact same phase.  This keeps
        # wide moves and x/y/z rotations visually synchronized instead of
        # looking like several independent arrows moving chaotically.
        for center, u, v, radius, direction in planes:
            self._draw_single_move_arrow(center, u, v, radius, direction, phase_offset=0.0)

        glDepthMask(GL_TRUE)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._last_pos = event.position()

    def mouseMoveEvent(self, event):
        if self._dragging and self._last_pos is not None:
            pos = event.position()
            dx = pos.x() - self._last_pos.x()
            dy = pos.y() - self._last_pos.y()
            self.rot_y += dx * 0.35
            self.rot_x += dy * 0.35
            self._last_pos = pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False
            self._last_pos = None

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        self.zoom += 0.4 if delta > 0 else -0.4
        self.update()
