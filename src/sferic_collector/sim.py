"""An in-process fake station so the collector is exercisable end to end
with no hardware and no Redis/Qdrant. Answers the `sensors.*` headless tools
with plausible IMU data (same shape as the firmware's `imuSnapshot()`).
"""

from __future__ import annotations

import math
import random

from .models import Capability
from .transport import StationError, StationTransport

_FACES = ["flat", "flat", "flat", "left", "right", "top", "bottom", "inverted"]


class SimulatedStation(StationTransport):
    kind = "sim"

    def __init__(self, station_id: str = "sim-1") -> None:
        self.id = station_id
        self.model = "m5stack-sim"
        self.fw_version = "0.1.0-sim"
        self._cap = Capability(
            toolsets=["sensors"],
            radios=["ble", "ir-tx", "ir-rx"],
        )

    async def capability(self) -> Capability:
        return self._cap

    async def run(self, tool: str, params: dict) -> dict:
        if tool in ("sensors.motion", "sensors.gesture"):
            return self._imu_snapshot()
        raise StationError(f"sim station has no headless tool {tool!r}")

    @staticmethod
    def _imu_snapshot() -> dict:
        ax, ay = random.uniform(-0.05, 0.05), random.uniform(-0.05, 0.05)
        az = 1.0 + random.uniform(-0.02, 0.02)
        dev = abs(math.sqrt(ax * ax + ay * ay + az * az) - 1.0)
        gyro = random.uniform(0.0, 3.0)
        return {
            "ax": round(ax, 3),
            "ay": round(ay, 3),
            "az": round(az, 3),
            "accel_dev_g": round(dev, 3),
            "gyro_dps": round(gyro, 1),
            "moving": dev > 0.16 or gyro > 40.0,
            "face": random.choice(_FACES),
        }
