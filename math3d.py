import math
from OpenGL.GL import glRotatef


class Quaternion:
    def __init__(self, w=1.0, x=0.0, y=0.0, z=0.0):
        self.w = w
        self.x = x
        self.y = y
        self.z = z

    def normalize(self):
        mag = math.sqrt(
            self.w * self.w +
            self.x * self.x +
            self.y * self.y +
            self.z * self.z
        )

        if mag > 0.0001:
            self.w /= mag
            self.x /= mag
            self.y /= mag
            self.z /= mag

        return self

    def inverse(self):
        return Quaternion(self.w, -self.x, -self.y, -self.z)

    def __mul__(self, other):
        w = self.w * other.w - self.x * other.x - self.y * other.y - self.z * other.z
        x = self.w * other.x + self.x * other.w + self.y * other.z - self.z * other.y
        y = self.w * other.y - self.x * other.z + self.y * other.w + self.z * other.x
        z = self.w * other.z + self.x * other.y - self.y * other.x + self.z * other.w

        return Quaternion(w, x, y, z).normalize()

    def slerp(self, other, t):
        t = max(0.0, min(1.0, t))

        dot = (
            self.w * other.w +
            self.x * other.x +
            self.y * other.y +
            self.z * other.z
        )

        if dot < 0.0:
            other = Quaternion(-other.w, -other.x, -other.y, -other.z)
            dot = -dot

        if dot > 0.9995:
            w = self.w + t * (other.w - self.w)
            x = self.x + t * (other.x - self.x)
            y = self.y + t * (other.y - self.y)
            z = self.z + t * (other.z - self.z)
            return Quaternion(w, x, y, z).normalize()

        theta_0 = math.acos(dot)
        theta = theta_0 * t

        sin_theta = math.sin(theta)
        sin_theta_0 = math.sin(theta_0)

        s0 = math.cos(theta) - dot * sin_theta / sin_theta_0
        s1 = sin_theta / sin_theta_0

        w = s0 * self.w + s1 * other.w
        x = s0 * self.x + s1 * other.x
        y = s0 * self.y + s1 * other.y
        z = s0 * self.z + s1 * other.z

        return Quaternion(w, x, y, z).normalize()

    def apply_gl(self):
        w = max(-1.0, min(1.0, self.w))
        angle = 2 * math.acos(w)
        s = math.sqrt(max(0.0, 1.0 - w * w))

        if s > 0.001:
            glRotatef(math.degrees(angle), self.x, self.z, -self.y)


def vec_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vec_mul(a, k):
    return (a[0] * k, a[1] * k, a[2] * k)

