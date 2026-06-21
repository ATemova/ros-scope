"""Synthetic fleet publisher.

Generates believable telemetry for a small fleet and pushes it into the Redis
stream, so the entire stack can be demoed with `docker compose up` and no
robot, no ROS install. It deliberately injects two failure conditions the
alert engine is meant to catch:

  - a slow battery drain that eventually crosses the warning/critical lines
  - a periodic "scan" dropout, so the staleness detector has something to find

Swap this out for the ROS 2 bridge (compose profile `ros`) to feed real data
through the exact same pipeline.
"""
from __future__ import annotations

import math
import os
import random
import time

from common.log import get_logger
from common.schema import STREAM, pose, scalar

log = get_logger("sim")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
ROBOTS = [r.strip() for r in os.environ.get("ROBOTS", "alpha,bravo,charlie").split(",") if r.strip()]
RATE_HZ = float(os.environ.get("RATE_HZ", "10"))


class RobotSim:
    """Per-robot state so each robot tells a slightly different story."""

    def __init__(self, robot_id: str, idx: int) -> None:
        self.robot_id = robot_id
        self.idx = idx
        self.t0 = time.time()
        self.voltage = 25.0 + random.uniform(-0.3, 0.3)   # 6S pack, full ~25.2V
        # Charlie drains fast so a critical battery alert shows up quickly in a demo.
        self.drain = 0.02 if robot_id != "charlie" else 0.12  # volts / second
        self.heading = random.uniform(0, math.tau)

    def step(self, now: float) -> list:
        t = now - self.t0
        samples = []

        # --- battery: monotonic drain plus a little measurement noise --------
        self.voltage = max(18.5, self.voltage - self.drain / RATE_HZ)
        pct = max(0.0, min(1.0, (self.voltage - 19.8) / (25.2 - 19.8)))
        samples.append(scalar(self.robot_id, "/battery_state",
                              {"voltage": round(self.voltage + random.uniform(-0.02, 0.02), 3),
                               "percentage": round(pct, 3)}, now))

        # --- cpu temperature: baseline + load wave + occasional spike --------
        temp = 48 + 6 * math.sin(t / 7.0 + self.idx) + random.uniform(-1.5, 1.5)
        if random.random() < 0.01:
            temp += random.uniform(25, 35)        # transient thermal spike
        samples.append(scalar(self.robot_id, "/diagnostics",
                              {"cpu_temp": round(temp, 2)}, now))

        # --- imu: noisy linear accel + yaw rate ------------------------------
        samples.append(scalar(self.robot_id, "/imu",
                              {"accel_z": round(9.81 + random.uniform(-0.3, 0.3), 3),
                               "yaw_rate": round(0.4 * math.sin(t / 3.0) + random.uniform(-0.05, 0.05), 4)}, now))

        # --- pose: drive a Lissajous loop so the 3D trail is interesting -----
        ax, ay = 4.0 + self.idx, 3.0
        px = ax * math.sin(0.25 * t + self.idx)
        py = ay * math.sin(0.35 * t)
        yaw = math.atan2(math.cos(0.35 * t) * 0.35 * ay, math.cos(0.25 * t + self.idx) * 0.25 * ax)
        samples.append(pose(self.robot_id, "/odom",
                            {"x": round(px, 3), "y": round(py, 3), "z": 0.0,
                             "qx": 0.0, "qy": 0.0,
                             "qz": round(math.sin(yaw / 2), 4), "qw": round(math.cos(yaw / 2), 4)}, now))

        # --- lidar scan: publish a min-range scalar, but drop out periodically
        # (~5s gap every 30s for bravo) so the staleness detector fires.
        scan_stale = self.robot_id == "bravo" and (int(t) % 30) < 5
        if not scan_stale:
            samples.append(scalar(self.robot_id, "/scan",
                                  {"range_min": round(0.4 + abs(math.sin(t)) * 2.5, 3)}, now))
        return samples


def main() -> None:
    import redis
    r = redis.from_url(REDIS_URL)
    while True:  # wait for redis to be reachable before starting the loop
        try:
            r.ping()
            break
        except redis.exceptions.ConnectionError:
            log.info("waiting for redis...")
            time.sleep(1)

    sims = [RobotSim(name, i) for i, name in enumerate(ROBOTS)]
    log.info("publishing %d robots at %s Hz -> %s", len(sims), RATE_HZ, REDIS_URL)
    period = 1.0 / RATE_HZ

    while True:
        now = time.time()
        pipe = r.pipeline()
        for s in sims:
            for sample in s.step(now):
                pipe.xadd(STREAM, {"data": sample.to_json()}, maxlen=100_000, approximate=True)
        pipe.execute()
        time.sleep(max(0.0, period - (time.time() - now)))


if __name__ == "__main__":
    main()
