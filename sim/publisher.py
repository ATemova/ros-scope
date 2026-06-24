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
from common.schema import STREAM, laser_scan, occupancy_map, pose, scalar

log = get_logger("sim")

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
ROBOTS = [r.strip() for r in os.environ.get("ROBOTS", "alpha,bravo,charlie").split(",") if r.strip()]
RATE_HZ = float(os.environ.get("RATE_HZ", "10"))

# Shared arena the fleet drives around in: a 20 m square room. The synthetic
# occupancy map marks the border as occupied; the laser scans below ray-cast to
# these same walls, so the map and the live scans are consistent.
MAP_HALF = 10.0          # metres from centre to wall
MAP_RES = 0.25           # metres / cell
MAP_CELLS = int(2 * MAP_HALF / MAP_RES)   # 80 x 80
SCAN_RAYS = 120
SCAN_RANGE_MAX = 12.0


def make_room_map() -> dict:
    """A bordered empty room as a nav_msgs/OccupancyGrid-shaped dict."""
    w = h = MAP_CELLS
    data = [0] * (w * h)
    for i in range(w):
        for j in range(h):
            if i == 0 or j == 0 or i == w - 1 or j == h - 1:
                data[j * w + i] = 100        # walls
    return {"resolution": MAP_RES, "width": w, "height": h,
            "origin_x": -MAP_HALF, "origin_y": -MAP_HALF, "data": data}


def ray_to_walls(px: float, py: float, yaw: float) -> list[float]:
    """Ray-cast SCAN_RAYS beams from (px,py) to the room walls; robot-frame."""
    amin, inc = -math.pi, math.tau / SCAN_RAYS
    ranges = []
    for k in range(SCAN_RAYS):
        a = yaw + amin + k * inc
        dx, dy = math.cos(a), math.sin(a)
        ts = []
        if dx > 1e-6:
            ts.append((MAP_HALF - px) / dx)
        elif dx < -1e-6:
            ts.append((-MAP_HALF - px) / dx)
        if dy > 1e-6:
            ts.append((MAP_HALF - py) / dy)
        elif dy < -1e-6:
            ts.append((-MAP_HALF - py) / dy)
        d = min([t for t in ts if t > 0], default=SCAN_RANGE_MAX)
        d = min(SCAN_RANGE_MAX, d) + random.uniform(-0.02, 0.02)
        ranges.append(round(max(0.0, d), 3))
    return ranges


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
        self.tick = 0

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
        self.tick += 1
        scan_stale = self.robot_id == "bravo" and (int(t) % 30) < 5
        if not scan_stale:
            samples.append(scalar(self.robot_id, "/scan",
                                  {"range_min": round(0.4 + abs(math.sin(t)) * 2.5, 3)}, now))
            # Full scan for the 3D point cloud at ~5 Hz (every other tick).
            if self.tick % 2 == 0:
                samples.append(laser_scan(self.robot_id, "/scan",
                                          {"angle_min": round(-math.pi, 4),
                                           "angle_increment": round(math.tau / SCAN_RAYS, 5),
                                           "range_max": SCAN_RANGE_MAX,
                                           "ranges": ray_to_walls(px, py, yaw)}, now))
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
    room = make_room_map()
    map_every = max(1, int(10 * RATE_HZ))   # re-publish the map ~every 10s
    tick = 0

    while True:
        now = time.time()
        pipe = r.pipeline()
        if tick % map_every == 0:
            pipe.xadd(STREAM, {"data": occupancy_map("global", "/map", room, now).to_json()},
                      maxlen=100_000, approximate=True)
        for s in sims:
            for sample in s.step(now):
                pipe.xadd(STREAM, {"data": sample.to_json()}, maxlen=100_000, approximate=True)
        pipe.execute()
        tick += 1
        time.sleep(max(0.0, period - (time.time() - now)))


if __name__ == "__main__":
    main()
