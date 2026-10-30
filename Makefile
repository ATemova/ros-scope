.PHONY: up down logs ps demo ros monitoring bench lint test check clean
up:          ## start the default synthetic-fleet stack
	docker compose up --build
demo: up     ## alias for `up`
ros:         ## start the stack plus the ROS 2 bridge + demo bot
	docker compose --profile ros up --build
monitoring:  ## start the stack plus Prometheus + Grafana
	docker compose --profile monitoring up --build
bench:       ## run the pipeline benchmark against a running stack
	docker compose --profile bench run --rm bench --count 100000 --latency-samples 500
down:        ## stop and remove containers
	docker compose down
clean:       ## stop and wipe the database volume
	docker compose down -v
logs:
	docker compose logs -f --tail=100
ps:
	docker compose ps
lint:        ## ruff lint
	ruff check .
test:        ## unit tests (no containers needed)
	python -m pytest -q tests
check: lint test
