FROM ros:humble-ros-base
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends python3-pip \
    && rm -rf /var/lib/apt/lists/*
COPY bridge/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common/ ./common/
COPY bridge/ ./bridge/
# Default: run the bridge. The demo bot is started as a second service.
CMD ["bash", "-lc", "source /opt/ros/humble/setup.bash && python3 bridge/ros_bridge.py"]
