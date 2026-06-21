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
"""
from __future__ import annotations

import os
import sys

import rclpy
import redis
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu, Temperature

# common/ is mounted into the image at /app/common
sys.path.insert(0, "/app")
from common.schema import STREAM, pose, scalar  # noqa: E402

REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
ROBOT_ID = os.environ.get("ROBOT_ID", "ros-1")


class Bridge(Node):
    def __init__(self) -> None:
        super().__init__("rosscope_bridge")
        self.r = redis.from_url(REDIS_URL)
        self.create_subscription(BatteryState, "/battery_state", self.on_battery, 10)
        self.create_subscription(Imu, "/imu", self.on_imu, 50)
        self.create_subscription(Odometry, "/odom", self.on_odom, 50)
        self.create_subscription(Temperature, "/diagnostics", self.on_temp, 10)
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
