"""Tiny ROS 2 publisher for demoing the bridge without hardware.

Publishes the same standard topics a real robot would (/battery_state, /imu,
/odom, /diagnostics) so you can verify the `ros` profile end to end:

    docker compose --profile ros up

For a real demo, replace this with your robot or a Gazebo bringup that
publishes these topics.
"""
from __future__ import annotations

import math

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import BatteryState, Imu, Temperature


class DemoBot(Node):
    def __init__(self) -> None:
        super().__init__("rosscope_demo_bot")
        self.batt = self.create_publisher(BatteryState, "/battery_state", 10)
        self.imu = self.create_publisher(Imu, "/imu", 50)
        self.odom = self.create_publisher(Odometry, "/odom", 50)
        self.temp = self.create_publisher(Temperature, "/diagnostics", 10)
        self.t = 0.0
        self.v = 25.0
        self.create_timer(0.1, self.tick)  # 10 Hz

    def tick(self) -> None:
        self.t += 0.1
        self.v = max(19.0, self.v - 0.01)

        b = BatteryState()
        b.voltage = self.v
        b.percentage = max(0.0, (self.v - 19.8) / 5.4)
        self.batt.publish(b)

        i = Imu()
        i.linear_acceleration.z = 9.81 + 0.2 * math.sin(self.t)
        i.angular_velocity.z = 0.4 * math.sin(self.t / 3)
        self.imu.publish(i)

        t = Temperature()
        t.temperature = 50 + 8 * math.sin(self.t / 6)
        self.temp.publish(t)

        o = Odometry()
        o.pose.pose.position.x = 4 * math.sin(0.25 * self.t)
        o.pose.pose.position.y = 3 * math.sin(0.35 * self.t)
        o.pose.pose.orientation.w = 1.0
        self.odom.publish(o)


def main() -> None:
    rclpy.init()
    node = DemoBot()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
