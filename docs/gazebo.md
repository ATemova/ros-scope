# Running rosscope against TurtleBot3 + Nav2 in Gazebo

The synthetic fleet already exercises the occupancy-map and laser-scan path, so
you can see those features without ROS. This guide points the bridge at a **real
ROS 2 autonomy stack** — TurtleBot3 navigating with Nav2 in Gazebo — so the
dashboard renders a genuine SLAM/Nav2 map and live lidar.

> Prerequisites: ROS 2 Humble, Gazebo, and the TurtleBot3 + Nav2 packages
> installed on the host. These run on your machine (GUI + sim), while rosscope's
> storage and dashboard run in Docker.

## 1. Start rosscope (storage + API + dashboard)

```bash
docker compose up -d --build db redis ingest alerts api
```

The dashboard is at http://localhost:8000. Redis is reachable from the host on
`localhost:6379` (the bridge publishes there).

## 2. Bring up TurtleBot3 + Nav2 in Gazebo

In a ROS 2 terminal:

```bash
export TURTLEBOT3_MODEL=burger
ros2 launch turtlebot3_gazebo turtlebot3_world.launch.py
# in another terminal: Nav2 (brings up /map, AMCL, costmaps, controllers)
ros2 launch turtlebot3_navigation2 navigation2.launch.py use_sim_time:=True
```

Set a 2D pose estimate and a navigation goal in RViz so the robot drives and the
costmaps/scan update.

## 3. Point the bridge at it

Run the bridge as a normal ROS 2 process on the host so it shares the ROS graph,
and send telemetry to the Dockerized Redis:

```bash
pip install redis
REDIS_URL=redis://localhost:6379/0 ROBOT_ID=tb3 \
  python3 bridge/ros_bridge.py
```

It subscribes to `/scan`, `/odom`, `/map` (and battery/imu/diagnostics if
present) and forwards them. Within a second or two the dashboard shows:

- the Nav2 occupancy map as the scene floor,
- the live laser scan as a point cloud around the robot,
- the robot's pose and trail as it navigates.

## Notes

- `/map` is latched (transient-local QoS); the bridge matches that durability so
  it receives the map even though it's published once.
- Large Nav2 maps are downsampled to ~120 cells on the longest side and scans to
  ~120 rays at ~5 Hz, to keep the live payload light. Tune `MAP_MAX_DIM`,
  `SCAN_MAX_RAYS`, and `SCAN_MIN_PERIOD_NS` in `bridge/ros_bridge.py`.
- Maps are published under the `global` robot id (shared scene geometry); scans
  and odometry are per-robot, so multiple bridges with distinct `ROBOT_ID`s
  populate one shared map with several robots.