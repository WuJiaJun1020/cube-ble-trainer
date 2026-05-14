import sys
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication
from config import DEFAULT_AA_SAMPLES
from ui.main_window import MainWindow


def configure_opengl_format(samples=DEFAULT_AA_SAMPLES):
    fmt = QSurfaceFormat()
    fmt.setDepthBufferSize(24)
    fmt.setStencilBufferSize(8)
    fmt.setSamples(max(0, int(samples)))
    fmt.setSwapBehavior(QSurfaceFormat.DoubleBuffer)
    QSurfaceFormat.setDefaultFormat(fmt)


def main():
    # The default format must be configured before QApplication/QOpenGLWidget
    # creates any OpenGL context. Individual cube widgets can still request a
    # different sample count when they are rebuilt from the UI option.
    configure_opengl_format(DEFAULT_AA_SAMPLES)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
