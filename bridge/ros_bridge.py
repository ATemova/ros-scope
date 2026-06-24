"""ROS 2 -> rosscope bridge.

A rclpy node that subscribes to a configurable set of standard ROS 2 topics
and forwards them into the same Redis stream the synthetic publisher uses, in
the same envelope. Point it at a real robot or a Gazebo sim and the rest of
the stack (storage, alerts, dashboard) works unchanged.

Run via the `ros` compose profile:  docker compose --profile ros up

Topic map (extend in topics.yaml / map_msg below):
  sensor_msgs/BatteryState     -> scalar voltage, percentage   on /battery_state
  sensor_msgs/Imu              -> scalar accel_z, yaw_rate      on /imu
  nav_msgs/Odometry            -> pose                          on /odom
  diagnostic / Temperature     -> scalar cpu_temp               on /diagnostics
  nav_msgs/OccupancyGrid       -> map (downsampled)             on /map
  sensor_msgs/LaserScan        -> scan (downsampled, ~5 Hz)     on /scan
"""
from __future__ import annotations

import math
import os
import sys

import rclpy
import redis
from nav_msgs.msg import OccupancyGrid, Odometry
from rclpy.node import Node
from rclpy.qos import QoSDurabilityPolicy, QoSProfile
from sensor_msgs.msg import BatteryState, Imu, LaserScan, Temperature

# common/ is mounted into the image at /app/common
sys.path.insert(0, "/app")
from common.schema import STREAM, laser_scan, occupancy_map, pose, scalar  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
ROBOT_ID = os.environ.get("ROBOT_ID", "ros-1")
MAP_MAX_DIM = 120        # downsample big Nav2 maps to keep them light
SCAN_MAX_RAYS = 120
SCAN_MIN_PERIOD_NS = 0.2e9   # forward scans at most ~5 Hz


class Bridge(Node):
    def __init__(self) -> None:
        super().__init__("rosscope_bridge")
        self.r = redis.from_url(REDIS_URL)
        self._last_scan_ns = 0
        self.create_subscription(BatteryState, "/battery_state", self.on_battery, 10)
        self.create_subscription(Imu, "/imu", self.on_imu, 50)
        self.create_subscription(Odometry, "/odom", self.on_odom, 50)
        self.create_subscription(Temperature, "/diagnostics", self.on_temp, 10)
        # /map is latched (transient local) in Nav2, so match that durability.
        map_qos = QoSProfile(depth=1)
        map_qos.durability = QoSDurabilityPolicy.TRANSIENT_LOCAL
        self.create_subscription(OccupancyGrid, "/map", self.on_map, map_qos)
        self.create_subscription(LaserScan, "/scan", self.on_scan, 10)
        self.get_logger().info(f"bridging ROS 2 -> {REDIS_URL} as robot '{ROBOT_ID}'")

    def _push(self, sample) -> None:
        self.r.xadd(STREAM, {"data": sample.to_json()}, maxlen=100_000, approximate=True)

    def on_battery(self, msg: BatteryState) -> None:
        self._push(scalar(ROBOT_ID, "/battery_state",
                          {"voltage": float(msg.voltage), "percentage": float(msg.percentage)}))

    def on_imu(self, msg: Imu) -> None:
        self._push(scalar(ROBOT_ID, "/imu",
                          {"accel_z": float(msg.linear_acceleration.z),
                           "yaw_rate": float(msg.angular_velocity.z)}))

    def on_temp(self, msg: Temperature) -> None:
        self._push(scalar(ROBOT_ID, "/diagnostics", {"cpu_temp": float(msg.temperature)}))

    def on_odom(self, msg: Odometry) -> None:
        p, q = msg.pose.pose.position, msg.pose.pose.orientation
        self._push(pose(ROBOT_ID, "/odom",
                        {"x": p.x, "y": p.y, "z": p.z,
                         "qx": q.x, "qy": q.y, "qz": q.z, "qw": q.w}))

    def on_map(self, msg: OccupancyGrid) -> None:
        w, h = msg.info.width, msg.info.height
        stride = max(1, max(w, h) // MAP_MAX_DIM)
        cols = list(range(0, w, stride))
        rows = list(range(0, h, stride))
        data = [int(msg.data[j * w + i]) for j in rows for i in cols]
        # Maps are shared scene geometry, so publish under "global".
        self._push(occupancy_map("global", "/map", {
            "resolution": float(msg.info.resolution) * stride,
            "width": len(cols), "height": len(rows),
            "origin_x": float(msg.info.origin.position.x),
            "origin_y": float(msg.info.origin.position.y),
            "data": data}))

    def on_scan(self, msg: LaserScan) -> None:
        now_ns = self.get_clock().now().nanoseconds
        if now_ns - self._last_scan_ns < SCAN_MIN_PERIOD_NS:
            return
        self._last_scan_ns = now_ns
        stride = max(1, len(msg.ranges) // SCAN_MAX_RAYS)
        ranges = [float(r) if math.isfinite(r) else float(msg.range_max)
                  for r in msg.ranges[::stride]]
        self._push(laser_scan(ROBOT_ID, "/scan", {
            "angle_min": float(msg.angle_min),
            "angle_increment": float(msg.angle_increment) * stride,
            "range_max": float(msg.range_max), "ranges": ranges}))


def main() -> None:
    rclpy.init()
    node = Bridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
