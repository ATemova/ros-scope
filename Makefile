.PHONY: up down logs ps demo ros test fmt clean
up:        ## start the default synthetic-fleet stack
	docker compose up --build
demo: up   ## alias for `up`
ros:       ## start the stack plus the ROS 2 bridge + demo bot
	docker compose --profile ros up --build
down:      ## stop and remove containers
	docker compose down
clean:     ## stop and wipe the database volume
	docker compose down -v
logs:
	docker compose logs -f --tail=100
ps:
	docker compose ps
test:      ## run unit tests for the alert rule engine (no containers needed)
	python -m pytest -q tests
