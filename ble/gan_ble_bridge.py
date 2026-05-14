# gan_ble_bridge.py

import sys
sys.coinit_flags = 0

import re
import time
import asyncio
import threading
from typing import Optional

from bleak import BleakScanner, BleakClient
from Crypto.Cipher import AES


PROTOCOLS = {
    "Gen2": {
        "notify": "28be4cb6-cd67-11e9-a32f-2a2ae2dbcce4",
        "write":  "28be4a4a-cd67-11e9-a32f-2a2ae2dbcce4",
        "facelets_cmd": bytes([0x04] + [0x00] * 19),
        "hardware_cmd": bytes([0x05] + [0x00] * 19),
        "battery_cmd":  bytes([0x09] + [0x00] * 19),
        "reset_cmd": bytes([
            0x0A, 0x05, 0x39, 0x77, 0x00, 0x00, 0x01, 0x23, 0x45, 0x67,
            0x89, 0xAB, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        ]),
    },
    "Gen3": {
        "notify": "8653000b-43e6-47b7-9cb0-5fc21d4ae340",
        "write":  "8653000c-43e6-47b7-9cb0-5fc21d4ae340",
        "facelets_cmd": bytes([0x68, 0x01] + [0x00] * 14),
        "hardware_cmd": bytes([0x68, 0x04] + [0x00] * 14),
        "battery_cmd":  bytes([0x68, 0x07] + [0x00] * 14),
        "reset_cmd": bytes([
            0x68, 0x05, 0x05, 0x39, 0x77, 0x00, 0x00, 0x01,
            0x23, 0x45, 0x67, 0x89, 0xAB, 0x00, 0x00, 0x00,
        ]),
    },
    "Gen4": {
        "notify": "0000fff6-0000-1000-8000-00805f9b34fb",
        "write":  "0000fff5-0000-1000-8000-00805f9b34fb",
        "facelets_cmd": bytes([0xDD, 0x04, 0x00, 0xED, 0x00, 0x00] + [0x00] * 14),
        "hardware_cmd": bytes([0xDF, 0x03, 0x00, 0x00, 0x00] + [0x00] * 15),
        "battery_cmd":  bytes([0xDD, 0x04, 0x00, 0xEF, 0x00, 0x00] + [0x00] * 14),
        # Gen4 REQUEST_RESET: reset GAN internal facelets/CP/CO/EP/EO state to solved.
        # Only send this when the physical cube is truly solved.
        "reset_cmd": bytes([
            0xD2, 0x0D, 0x05, 0x39, 0x77, 0x00, 0x00, 0x01,
            0x23, 0x45, 0x67, 0x89, 0xAB, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00,
        ]),
    },
}

BASE_KEY = bytes([
    0x01, 0x02, 0x42, 0x28,
    0x31, 0x91, 0x16, 0x07,
    0x20, 0x05, 0x18, 0x54,
    0x42, 0x11, 0x12, 0x53,
])

BASE_IV = bytes([
    0x11, 0x03, 0x32, 0x28,
    0x21, 0x01, 0x76, 0x27,
    0x20, 0x95, 0x78, 0x14,
    0x32, 0x12, 0x02, 0x43,
])

MOVE_REMAP = {
    "U": "U",   "U'": "U'",
    "D": "D",   "D'": "D'",
    "F": "F",   "F'": "F'",
    "B": "B",   "B'": "B'",
    "L": "L",   "L'": "L'",
    "R": "R",   "R'": "R'",
}

SOLVED_FACELETS = "UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB"
SOLVED_STATE = {
    "CP": list(range(8)),
    "CO": [0] * 8,
    "EP": list(range(12)),
    "EO": [0] * 12,
}


def is_solved_state(state) -> bool:
    return all(list(state.get(k, [])) == v for k, v in SOLVED_STATE.items())


def parse_mac(text: str) -> bytes:
    m = re.search(r"([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}", text or "")
    if not m:
        raise ValueError(f"Invalid MAC address: {text}")
    return bytes(int(x, 16) for x in re.split(r"[:-]", m.group(0)))


def looks_like_mac(text: str) -> bool:
    return bool(re.search(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$", text or ""))


def extract_mac_from_adv(adv) -> Optional[str]:
    if adv is None:
        return None
    manufacturer_data = getattr(adv, "manufacturer_data", None)
    if not manufacturer_data:
        return None
    for _, data in manufacturer_data.items():
        if data and len(data) >= 6:
            mac_bytes = list(reversed(data[-6:]))
            return ":".join(f"{b:02X}" for b in mac_bytes)
    return None


class GanCrypto:
    def __init__(self, mac: str):
        mac_bytes = parse_mac(mac)
        salt = list(reversed(mac_bytes))

        key = bytearray(BASE_KEY)
        iv = bytearray(BASE_IV)

        for i in range(6):
            key[i] = (key[i] + salt[i]) % 0xFF
            iv[i] = (iv[i] + salt[i]) % 0xFF

        self.key = bytes(key)
        self.iv = bytes(iv)

    def _encrypt_chunk(self, data: bytearray, offset: int):
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        data[offset:offset + 16] = cipher.encrypt(bytes(data[offset:offset + 16]))

    def _decrypt_chunk(self, data: bytearray, offset: int):
        cipher = AES.new(self.key, AES.MODE_CBC, self.iv)
        data[offset:offset + 16] = cipher.decrypt(bytes(data[offset:offset + 16]))

    def encrypt(self, data: bytes) -> bytes:
        buf = bytearray(data)
        self._encrypt_chunk(buf, 0)
        if len(buf) > 16:
            self._encrypt_chunk(buf, len(buf) - 16)
        return bytes(buf)

    def decrypt(self, data: bytes) -> bytes:
        buf = bytearray(data)
        if len(buf) > 16:
            self._decrypt_chunk(buf, len(buf) - 16)
        self._decrypt_chunk(buf, 0)
        return bytes(buf)


def bit_string(data: bytes) -> str:
    return "".join(f"{b:08b}" for b in data)


def get_bit_word(bits: str, start: int, length: int, little_endian: bool = False) -> int:
    if length <= 8 and not little_endian:
        part = bits[start:start + length]
        return int(part, 2) if part else 0
    out = bytearray()
    for i in range((length + 7) // 8):
        part = bits[start + i * 8:start + i * 8 + 8]
        if len(part) < 8:
            part = part.ljust(8, "0")
        out.append(int(part, 2))
    return int.from_bytes(out, "little" if little_endian else "big")


def sum_ints(values):
    return sum(int(v) for v in values)


CORNER_FACELET_MAP = [
    [8, 9, 20],    # URF
    [6, 18, 38],   # UFL
    [0, 36, 47],   # ULB
    [2, 45, 11],   # UBR
    [29, 26, 15],  # DFR
    [27, 44, 24],  # DLF
    [33, 53, 42],  # DBL
    [35, 17, 51],  # DRB
]

EDGE_FACELET_MAP = [
    [5, 10],   # UR
    [7, 19],   # UF
    [3, 37],   # UL
    [1, 46],   # UB
    [32, 16],  # DR
    [28, 25],  # DF
    [30, 43],  # DL
    [34, 52],  # DB
    [23, 12],  # FR
    [21, 41],  # FL
    [50, 39],  # BL
    [48, 14],  # BR
]


def to_kociemba_facelets(cp, co, ep, eo):
    faces = "URFDLB"
    facelets = [faces[i // 9] for i in range(54)]

    for i in range(8):
        for part in range(3):
            dst = CORNER_FACELET_MAP[i][(part + co[i]) % 3]
            src = CORNER_FACELET_MAP[cp[i]][part]
            facelets[dst] = faces[src // 9]

    for i in range(12):
        for part in range(2):
            dst = EDGE_FACELET_MAP[i][(part + eo[i]) % 2]
            src = EDGE_FACELET_MAP[ep[i]][part]
            facelets[dst] = faces[src // 9]

    return "".join(facelets)


def parse_state_from_bits(bits, protocol):
    if protocol == "Gen2":
        serial = get_bit_word(bits, 4, 8)
        cp_start, co_start, ep_start, eo_start = 12, 33, 47, 91
    elif protocol == "Gen3":
        serial = get_bit_word(bits, 24, 16, True)
        cp_start, co_start, ep_start, eo_start = 40, 61, 77, 121
    elif protocol == "Gen4":
        serial = get_bit_word(bits, 16, 16, True)
        cp_start, co_start, ep_start, eo_start = 32, 53, 69, 113
    else:
        raise ValueError(f"Unsupported protocol: {protocol}")

    cp, co, ep, eo = [], [], [], []
    for i in range(7):
        cp.append(get_bit_word(bits, cp_start + i * 3, 3))
        co.append(get_bit_word(bits, co_start + i * 2, 2))
    cp.append(28 - sum_ints(cp))
    co.append((3 - (sum_ints(co) % 3)) % 3)

    for i in range(11):
        ep.append(get_bit_word(bits, ep_start + i * 4, 4))
        eo.append(get_bit_word(bits, eo_start + i, 1))
    ep.append(66 - sum_ints(ep))
    eo.append((2 - (sum_ints(eo) % 2)) % 2)

    return serial & 0xFF, to_kociemba_facelets(cp, co, ep, eo), {
        "CP": cp, "CO": co, "EP": ep, "EO": eo,
    }


def signed_fraction_15(v):
    return (1 - ((v >> 15) & 1) * 2) * (v & 0x7FFF) / 32767.0


def signed_3bit_nibble(v):
    return (1 - ((v >> 3) & 1) * 2) * (v & 0x7)


class MoveParser:
    def __init__(self, protocol: str):
        self.protocol = protocol
        self.last_serial = -1          # last emitted MOVE serial
        self.serial = -1               # current cube state serial from MOVE/FACELETS
        self.last_facelets_serial = None
        self.last_local_timestamp = None
        self.last_move_timestamp = 0.0
        self.cube_timestamp = 0
        self.move_buffer = []
        self.hw_info = {}

    def parse(self, plain: bytes):
        bits = bit_string(plain)
        if self.protocol == "Gen2":
            return self._parse_gen2(bits)
        if self.protocol == "Gen3":
            return self._parse_gen3(bits)
        if self.protocol == "Gen4":
            return self._parse_gen4(plain, bits)
        return []

    def _move_event(self, serial, move, face=None, direction=None, cube_timestamp=None, recovered=False):
        return {
            "type": "MOVE",
            "serial": serial & 0xFF,
            "move": move,
            "face": face,
            "direction": direction,
            "cube_timestamp": cube_timestamp,
            "local_timestamp": None if recovered else time.time(),
            "recovered": recovered,
        }

    def _serial_in_range(self, start, end, serial, closed_start=False, closed_end=False):
        return ((end - start) & 0xFF) >= ((serial - start) & 0xFF) and \
               (closed_start or ((start - serial) & 0xFF) > 0) and \
               (closed_end or ((end - serial) & 0xFF) > 0)

    def _request_history_event(self, serial, count):
        return ("REQUEST_HISTORY", serial & 0xFF, max(1, min(int(count), 255)))

    def _inject_missed_move_to_buffer(self, move_event):
        if self.move_buffer:
            head = self.move_buffer[0]
            if any(e[1]["serial"] == move_event["serial"] for e in self.move_buffer if e[0] == "MOVE_META"):
                return
            if not self._serial_in_range(self.last_serial, head[1]["serial"], move_event["serial"]):
                return
            if move_event["serial"] == ((head[1]["serial"] - 1) & 0xFF):
                self.move_buffer.insert(0, ("MOVE_META", move_event))
        else:
            if self._serial_in_range(self.last_serial, self.serial, move_event["serial"], False, True):
                self.move_buffer.insert(0, ("MOVE_META", move_event))

    def _evict_move_buffer(self, allow_request=True):
        """Emit parsed moves immediately.

        The previous BLE-info build tried to pause live moves and request
        MOVE_HISTORY when serial numbers looked discontinuous.  That made
        formula training/debug feel delayed, and it could replay recovered
        moves late.  M/E/S detection is especially sensitive because one
        logical move is matched from two physical outer-layer signals.

        Keep the newer BLE status/hardware/battery events, but remove the
        history-recovery path from the live move stream.
        """
        events = []
        while self.move_buffer:
            head = self.move_buffer.pop(0)
            if head[0] != "MOVE_META":
                continue
            ev = head[1]
            events.append(("MOVE_META", ev))
            self.last_serial = ev["serial"]
        return events

    def _check_if_move_missed(self):
        # Do not request/replay MOVE_HISTORY during live formula/debug input.
        return []

    def _parse_gen2(self, bits: str):
        events = []
        timestamp = time.time()
        event_type = get_bit_word(bits, 0, 4)

        if event_type == 0x01:  # GYRO
            qw = get_bit_word(bits, 4, 16)
            qx = get_bit_word(bits, 20, 16)
            qy = get_bit_word(bits, 36, 16)
            qz = get_bit_word(bits, 52, 16)
            vx = get_bit_word(bits, 68, 4)
            vy = get_bit_word(bits, 72, 4)
            vz = get_bit_word(bits, 76, 4)
            events.append(("GYRO", signed_fraction_15(qw), signed_fraction_15(qx), signed_fraction_15(qy), signed_fraction_15(qz), signed_3bit_nibble(vx), signed_3bit_nibble(vy), signed_3bit_nibble(vz)))
            return events

        if event_type == 0x02:  # MOVE
            if self.last_serial == -1:
                return events
            serial = get_bit_word(bits, 4, 8)
            diff = min((serial - self.last_serial) & 0xFF, 7)
            if diff > 0:
                for i in range(diff - 1, -1, -1):
                    face = get_bit_word(bits, 12 + 5 * i, 4)
                    direction = get_bit_word(bits, 16 + 5 * i, 1)
                    elapsed = get_bit_word(bits, 47 + 16 * i, 16)
                    if elapsed == 0:
                        elapsed = int((timestamp - self.last_move_timestamp) * 1000)
                    self.cube_timestamp += elapsed
                    if 0 <= face <= 5:
                        move = "URFDLB"[face] + ("'" if direction else "")
                        events.append(("MOVE_META", self._move_event((serial - i) & 0xFF, move, face, direction, self.cube_timestamp, recovered=(i != 0))))
                self.last_serial = serial
                self.last_move_timestamp = timestamp
            return events

        if event_type == 0x04:  # FACELETS
            serial, facelets, state = parse_state_from_bits(bits, "Gen2")
            if self.last_serial == -1:
                self.last_serial = serial
            events.append(("FACELETS", serial, facelets, state))
            return events

        if event_type == 0x05:  # HARDWARE
            hw_major = get_bit_word(bits, 8, 8)
            hw_minor = get_bit_word(bits, 16, 8)
            sw_major = get_bit_word(bits, 24, 8)
            sw_minor = get_bit_word(bits, 32, 8)
            name = "".join(chr(get_bit_word(bits, 40 + i * 8, 8)) for i in range(8)).rstrip("\x00")
            gyro_supported = bool(get_bit_word(bits, 104, 1))
            events.append(("HARDWARE", {"hardwareName": name, "hardwareVersion": f"{hw_major}.{hw_minor}", "softwareVersion": f"{sw_major}.{sw_minor}", "gyroSupported": gyro_supported}))
            return events

        if event_type == 0x09:  # BATTERY
            battery = min(get_bit_word(bits, 8, 8), 100)
            events.append(("BATTERY", battery))
            return events

        if event_type == 0x0D:
            events.append(("DISCONNECT", "cube requested disconnect"))
        return events

    def _parse_gen3(self, bits: str):
        events = []
        timestamp = time.time()
        magic = get_bit_word(bits, 0, 8)
        event_type = get_bit_word(bits, 8, 8)
        data_length = get_bit_word(bits, 16, 8)
        if magic != 0x55 or data_length <= 0:
            return events

        if event_type == 0x01:  # MOVE
            if self.last_serial != -1:
                self.last_local_timestamp = timestamp
                cube_timestamp = get_bit_word(bits, 24, 32, True)
                serial = self.serial = get_bit_word(bits, 56, 16, True) & 0xFF
                direction = get_bit_word(bits, 72, 2)
                face_code = get_bit_word(bits, 74, 6)
                face_map = [2, 32, 8, 1, 16, 4]
                if face_code in face_map:
                    face = face_map.index(face_code)
                    move = "URFDLB"[face] + ("'" if direction == 1 else "")
                    self.move_buffer.append(("MOVE_META", self._move_event(serial, move, face, direction, cube_timestamp)))
                events.extend(self._evict_move_buffer(allow_request=True))
            return events

        if event_type == 0x06:  # MOVE_HISTORY
            # History replay is deliberately ignored for live formula/debug input.
            return events

        if event_type == 0x02:  # FACELETS
            serial, facelets, state = parse_state_from_bits(bits, "Gen3")
            self.serial = serial
            if self.last_serial != -1 and self.last_local_timestamp is not None and (timestamp - self.last_local_timestamp) > 0.5:
                events.extend(self._check_if_move_missed())
            if self.last_serial == -1:
                self.last_serial = serial
            events.append(("FACELETS", serial, facelets, state))
            return events

        if event_type == 0x07:  # HARDWARE
            name = "".join(chr(get_bit_word(bits, 32 + i * 8, 8)) for i in range(5)).rstrip("\x00")
            sw_major = get_bit_word(bits, 72, 4)
            sw_minor = get_bit_word(bits, 76, 4)
            hw_major = get_bit_word(bits, 80, 4)
            hw_minor = get_bit_word(bits, 84, 4)
            events.append(("HARDWARE", {"hardwareName": name, "hardwareVersion": f"{hw_major}.{hw_minor}", "softwareVersion": f"{sw_major}.{sw_minor}", "gyroSupported": False}))
            return events

        if event_type == 0x10:  # BATTERY
            battery = min(get_bit_word(bits, 24, 8), 100)
            events.append(("BATTERY", battery))
            return events

        if event_type == 0x11:
            events.append(("DISCONNECT", "cube requested disconnect"))
        return events

    def _parse_gen4(self, plain: bytes, bits: str):
        events = []
        timestamp = time.time()
        event_type = plain[0]
        data_length = plain[1] if len(plain) > 1 else 0

        if event_type == 0x01:  # MOVE
            # Gen4 notifications can contain two compact MOVE records: one at
            # offset 0 and one at offset 9.  The first BLE-info build only read
            # the first record and tried to recover the rest later via history,
            # which caused lag and incorrect M/E/S matching.  Parse both live
            # records immediately, as the stable baseline did.
            if self.last_serial != -1:
                self.last_local_timestamp = timestamp
                face_map = [2, 32, 8, 1, 16, 4]
                for offset in (0, 9):
                    if offset + 8 >= len(plain) or plain[offset] != 0x01:
                        continue
                    cube_timestamp = int.from_bytes(plain[offset + 2: offset + 6], "little") if offset + 6 <= len(plain) else None
                    serial = self.serial = int.from_bytes(plain[offset + 6: offset + 8], "little") & 0xFF
                    action_byte = plain[offset + 8]
                    direction = (action_byte >> 6) & 0x03
                    face_code = action_byte & 0x3F
                    if face_code in face_map:
                        face = face_map.index(face_code)
                        move = "URFDLB"[face] + ("'" if direction == 1 else "")
                        self.move_buffer.append(("MOVE_META", self._move_event(serial, move, face, direction, cube_timestamp)))
                    else:
                        events.append(("DEBUG", f"unknown Gen4 move: face={face_code}, dir={direction}, serial={serial}"))
                events.extend(self._evict_move_buffer(allow_request=False))
            return events

        if event_type == 0xD1:  # MOVE_HISTORY
            # History replay is deliberately ignored for live formula/debug input.
            return events

        if event_type == 0xED:  # FACELETS
            serial, facelets, state = parse_state_from_bits(bits, "Gen4")
            self.serial = serial
            if self.last_serial != -1 and self.last_local_timestamp is not None and (timestamp - self.last_local_timestamp) > 0.5:
                events.extend(self._check_if_move_missed())
            if self.last_serial == -1:
                self.last_serial = serial
            if serial != self.last_facelets_serial:
                self.last_facelets_serial = serial
                events.append(("FACELETS", serial, facelets, state))
            return events

        if 0xFA <= event_type <= 0xFE:  # HARDWARE chunks
            if event_type == 0xFA:
                year = get_bit_word(bits, 24, 16, True)
                month = get_bit_word(bits, 40, 8)
                day = get_bit_word(bits, 48, 8)
                self.hw_info[event_type] = f"{year:04d}-{month:02d}-{day:02d}"
            elif event_type == 0xFC:
                name = ""
                for i in range(max(0, data_length - 1)):
                    c = get_bit_word(bits, 24 + i * 8, 8)
                    if c:
                        name += chr(c)
                self.hw_info[event_type] = name
            elif event_type == 0xFD:
                sw_major = get_bit_word(bits, 24, 4)
                sw_minor = get_bit_word(bits, 28, 4)
                self.hw_info[event_type] = f"{sw_major}.{sw_minor}"
            elif event_type == 0xFE:
                hw_major = get_bit_word(bits, 24, 4)
                hw_minor = get_bit_word(bits, 28, 4)
                self.hw_info[event_type] = f"{hw_major}.{hw_minor}"
            if all(k in self.hw_info for k in (0xFA, 0xFC, 0xFD, 0xFE)):
                name = self.hw_info.get(0xFC, "")
                events.append(("HARDWARE", {
                    "hardwareName": name,
                    "hardwareVersion": self.hw_info.get(0xFE),
                    "softwareVersion": self.hw_info.get(0xFD),
                    "productDate": self.hw_info.get(0xFA),
                    "gyroSupported": name in ("GAN12uiM", "GAN14ui", "GAN14uiM"),
                }))
            return events

        if event_type == 0xEC:  # GYRO
            qw = get_bit_word(bits, 16, 16)
            qx = get_bit_word(bits, 32, 16)
            qy = get_bit_word(bits, 48, 16)
            qz = get_bit_word(bits, 64, 16)
            vx = get_bit_word(bits, 80, 4)
            vy = get_bit_word(bits, 84, 4)
            vz = get_bit_word(bits, 88, 4)
            events.append(("GYRO", signed_fraction_15(qw), signed_fraction_15(qx), signed_fraction_15(qy), signed_fraction_15(qz), signed_3bit_nibble(vx), signed_3bit_nibble(vy), signed_3bit_nibble(vz)))
            return events

        if event_type == 0xEF:  # BATTERY
            battery = None
            try:
                battery = get_bit_word(bits, 8 + data_length * 8, 8)
            except Exception:
                pass
            if battery is None or battery > 100:
                for idx in (3, 2, -1):
                    if -len(plain) <= idx < len(plain) and 0 <= plain[idx] <= 100:
                        battery = plain[idx]
                        break
            if battery is not None:
                events.append(("BATTERY", min(int(battery), 100)))
            return events

        if event_type == 0xEA:
            events.append(("DISCONNECT", "cube requested disconnect"))
            return events

        return events


def build_history_cmd(protocol: str, serial: int, count: int) -> Optional[bytes]:
    serial &= 0xFF
    count = int(max(1, min(count, 255)))
    if serial % 2 == 0:
        serial = (serial - 1) & 0xFF
    if count % 2 == 1:
        count += 1
    count = min(count, serial + 1)
    if protocol == "Gen3":
        msg = bytearray(16)
        msg[:6] = bytes([0x68, 0x03, serial, 0x00, count, 0x00])
        return bytes(msg)
    if protocol == "Gen4":
        msg = bytearray(20)
        msg[:6] = bytes([0xD1, 0x04, serial, 0x00, count, 0x00])
        return bytes(msg)
    return None


class GanBleBridge:
    def __init__(self, output_queue, control_queue=None, name_keyword="GAN", address=None, mac=None):
        self.output_queue = output_queue
        self.control_queue = control_queue
        self.name_keyword = name_keyword
        self.address = address
        self.manual_mac = mac

        self.client = None
        self.crypto = None
        self.parser = None
        self.protocol = None
        self.notify_uuid = None
        self.write_uuid = None
        self.awaiting_reset_verify = False
        self._recent_history_requests = {}

    def emit_status(self, status, message=""):
        """Emit the legacy STATUS event used by the PySide6 app."""
        try:
            self.output_queue.put(("STATUS", status, message))
        except Exception:
            pass

    async def run_forever(self):
        while True:
            try:
                self.emit_status("scanning", "正在扫描")
                await self.connect_and_listen()
                self.emit_status("disconnected", "连接断开")
            except Exception as e:
                print(f"[GAN] 蓝牙连接失败或断开：{repr(e)}")
                print("[GAN] 3 秒后重试...")
                self.emit_status("error", str(e))
                self.output_queue.put(("DISCONNECT", repr(e)))
                await asyncio.sleep(3)

    async def connect_and_listen(self):
        device, adv = await self.find_device()

        print(f"[GAN] 连接设备：{device.name} / {device.address}")
        self.emit_status("connecting", "正在连接")

        async with BleakClient(device) as client:
            self.client = client
            print("[GAN] 已连接")
            self.emit_status("connected", "已连接")
            self.output_queue.put(("CONNECTED", device.name or "GAN", device.address))

            chars = {}
            for service in client.services:
                for char in service.characteristics:
                    chars[char.uuid.lower()] = char

            self.protocol = self.detect_protocol(chars)
            if not self.protocol:
                raise RuntimeError("没有识别到 GAN Gen2/Gen3/Gen4 协议特征")

            self.notify_uuid = PROTOCOLS[self.protocol]["notify"]
            self.write_uuid = PROTOCOLS[self.protocol]["write"]

            mac = self.manual_mac or extract_mac_from_adv(adv)
            if not mac and looks_like_mac(device.address):
                mac = device.address
            if not mac:
                raise RuntimeError("无法取得 MAC，无法解密 GAN 数据")

            self.crypto = GanCrypto(mac)
            self.parser = MoveParser(self.protocol)
            self._recent_history_requests.clear()
            self.awaiting_reset_verify = False

            print(f"[GAN] 协议：{self.protocol}")
            print(f"[GAN] MAC：{mac}")
            self.output_queue.put(("PROTOCOL", self.protocol, mac))

            await client.start_notify(self.notify_uuid, self.on_notify)

            await self.request_hardware()
            await asyncio.sleep(0.2)
            await self.request_facelets()
            await asyncio.sleep(0.5)
            await self.request_battery()

            print("[GAN] 已开始监听。现在转动实体魔方，虚拟魔方应同步转动。")
            while client.is_connected:
                await self.process_control_commands()
                await asyncio.sleep(0.05)

        self.output_queue.put(("DISCONNECT", "connection closed"))

    async def find_device(self):
        print("[GAN] 正在扫描智能魔方...")
        result = await BleakScanner.discover(timeout=8.0, return_adv=True)
        items = []

        if isinstance(result, dict):
            for _, pair in result.items():
                device, adv = pair
                items.append((device, adv))
        else:
            for device in result:
                items.append((device, None))

        if self.address:
            for device, adv in items:
                if device.address.lower() == self.address.lower():
                    return device, adv
            raise RuntimeError(f"没找到指定地址：{self.address}")

        keyword = self.name_keyword.upper()
        for device, adv in items:
            name = device.name or ""
            if keyword in name.upper():
                return device, adv

        print("[GAN] 扫描到的设备：")
        for device, _ in items:
            print(f"  {device.name} / {device.address}")

        raise RuntimeError("没找到名称包含 GAN 的设备")

    def detect_protocol(self, chars):
        char_uuids = set(chars.keys())
        for proto, p in PROTOCOLS.items():
            if p["notify"].lower() in char_uuids and p["write"].lower() in char_uuids:
                return proto
        return None

    async def safe_write(self, plain_command: bytes):
        encrypted = self.crypto.encrypt(plain_command)
        try:
            await self.client.write_gatt_char(self.write_uuid, encrypted, response=True)
        except Exception:
            await self.client.write_gatt_char(self.write_uuid, encrypted, response=False)

    async def process_control_commands(self):
        if self.control_queue is None:
            return

        while True:
            try:
                command = self.control_queue.get_nowait()
            except Exception:
                break

            if command == "RESET_HARDWARE_STATE":
                await self.reset_hardware_state_to_solved()
            elif command == "REQUEST_FACELETS":
                await self.request_facelets()
            elif command == "REQUEST_BATTERY":
                await self.request_battery()
            elif command == "REQUEST_HARDWARE":
                await self.request_hardware()
            elif isinstance(command, tuple) and command and command[0] == "REQUEST_HISTORY":
                # Disabled: history replay interferes with live formula/debug matching.
                continue
            else:
                print(f"[GAN] 未知控制命令：{command!r}")

    async def request_facelets(self):
        cmd = PROTOCOLS.get(self.protocol, {}).get("facelets_cmd")
        if not cmd:
            print(f"[GAN] 当前协议 {self.protocol} 没有 facelets_cmd")
            return
        await self.safe_write(cmd)

    async def request_hardware(self):
        cmd = PROTOCOLS.get(self.protocol, {}).get("hardware_cmd")
        if not cmd:
            print(f"[GAN] 当前协议 {self.protocol} 没有 hardware_cmd")
            return
        if self.parser:
            self.parser.hw_info.clear()
        await self.safe_write(cmd)

    async def request_battery(self):
        cmd = PROTOCOLS.get(self.protocol, {}).get("battery_cmd")
        if not cmd:
            print(f"[GAN] 当前协议 {self.protocol} 没有 battery_cmd")
            return
        await self.safe_write(cmd)

    async def request_history(self, serial, count):
        # Disabled: do not request/replay MOVE_HISTORY during live formula/debug input.
        return

    async def reset_hardware_state_to_solved(self):
        cmd = PROTOCOLS.get(self.protocol, {}).get("reset_cmd")
        if not cmd:
            print(f"[GAN] 当前协议 {self.protocol} 暂未配置硬件 reset 命令")
            self.output_queue.put(("RESET_RESULT", False, f"当前协议 {self.protocol} 暂未配置硬件 reset 命令"))
            return

        print("[GAN] 正在发送硬件校准命令：将魔方内部状态重置为 solved...")
        print("[GAN] 注意：请确认实体魔方已经真实还原，否则会把当前乱态设为 solved 基准。")
        try:
            print(f"[GAN] reset_cmd_len={len(cmd)} bytes, plain={cmd.hex()}")
            self.awaiting_reset_verify = True
            await self.safe_write(cmd)
            await asyncio.sleep(0.8)
            await self.request_facelets()
            self.output_queue.put(("RESET_RESULT", True, "已发送硬件 reset，正在等待下一条 FACELETS/STATE 验证"))
            print("[GAN] 已发送硬件 reset，正在等待 FACELETS/STATE 验证...")
        except Exception as e:
            self.awaiting_reset_verify = False
            self.output_queue.put(("RESET_RESULT", False, repr(e)))
            print(f"[GAN] 硬件 reset 发送失败：{e!r}")

    def on_notify(self, sender, data):
        raw = bytes(data)
        try:
            plain = self.crypto.decrypt(raw)
            events = self.parser.parse(plain)

            for event in events:
                if not isinstance(event, tuple):
                    continue

                tag = event[0]
                if tag == "REQUEST_HISTORY":
                    _, serial, count = event
                    if self.control_queue is not None:
                        self.control_queue.put(("REQUEST_HISTORY", serial, count))

                elif tag == "MOVE_META":
                    meta = event[1]
                    move = meta["move"]
                    mapped = MOVE_REMAP.get(move, move)
                    recovered = " recovered" if meta.get("recovered") else ""
                    print(f"[GAN] MOVE{recovered} serial={meta['serial']} {move} -> {mapped} cube_ts={meta.get('cube_timestamp')}")
                    self.output_queue.put(("MOVE", mapped, meta))

                elif tag == "GYRO":
                    _, qw, qx, qy, qz, vx, vy, vz = event
                    self.output_queue.put(("GYRO", qw, qx, qy, qz, vx, vy, vz))

                elif tag == "FACELETS":
                    _, serial, facelets, state = event
                    print(f"[GAN] FACELETS serial={serial} facelets={facelets}")
                    print(f"[GAN] STATE CP={state['CP']} CO={state['CO']} EP={state['EP']} EO={state['EO']}")
                    if self.awaiting_reset_verify:
                        self.awaiting_reset_verify = False
                        if is_solved_state(state):
                            print("[GAN] 硬件校准验证成功：魔方内部 STATE 已经是 solved。")
                            self.output_queue.put(("RESET_RESULT", True, "验证成功：魔方内部 STATE 已是 solved"))
                        else:
                            print("[GAN] 硬件校准验证失败：STATE 仍不是 solved。")
                            print("[GAN] 请确认实体已复原；如果已复原，则说明 reset 未被固件接受或命令仍需抓包确认。")
                            self.output_queue.put(("RESET_RESULT", False, "验证失败：返回的 STATE 仍不是 solved"))
                    self.output_queue.put(event)

                elif tag == "BATTERY":
                    battery = event[1]
                    print(f"[GAN] BATTERY {battery}%")
                    self.output_queue.put(("BATTERY", battery))

                elif tag == "HARDWARE":
                    info = event[1]
                    print(f"[GAN] HARDWARE {info}")
                    self.output_queue.put(("HARDWARE", info))

                elif tag == "DISCONNECT":
                    reason = event[1] if len(event) > 1 else "cube requested disconnect"
                    print(f"[GAN] DISCONNECT event: {reason}")
                    self.output_queue.put(("DISCONNECT", reason))
                    try:
                        if self.client:
                            asyncio.create_task(self.client.disconnect())
                    except Exception:
                        pass

                elif tag == "DEBUG":
                    print(f"[GAN] {event[1]}")

        except Exception as e:
            print(f"[GAN] 通知解析失败：{repr(e)} raw={raw.hex()}")


def start_gan_bridge(output_queue, control_queue=None, name_keyword="GAN", address=None, mac=None):
    def runner():
        asyncio.run(
            GanBleBridge(
                output_queue=output_queue,
                control_queue=control_queue,
                name_keyword=name_keyword,
                address=address,
                mac=mac,
            ).run_forever()
        )
    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    return thread
