"""Read-only bounded load: real spatial SQL, model/cache inference, and ranking.

Use the guarded QA fixture database. Counts are synthetic, never model results.
"""

import json
import os
import random
from datetime import UTC, datetime
from pathlib import Path

from locust import HttpUser, between, events, task

STATION = "05c5e17c-7aa9-49b7-9da3-9db4858ec1fc"
random.seed(42)


@events.quitting.add_listener
def save_final_statistics(environment, **kwargs) -> None:
    # Periodic CSV can miss requests completed during --stop-timeout. Capture
    # the final aggregate/histograms explicitly after all users have stopped.
    target = os.environ.get("BIKE_LOAD_FINAL_JSON")
    if not target:
        return
    rows = []
    for entry in [*environment.stats.entries.values(), environment.stats.total]:
        rows.append(
            {
                "Name": entry.name,
                "Type": entry.method,
                "Request Count": entry.num_requests,
                "Failure Count": entry.num_failures,
                "Requests/s": entry.total_rps,
                "Average Response Time": entry.avg_response_time,
                **{f"{p}%": entry.get_response_time_percentile(p / 100) for p in (50, 90, 95, 99)},
                "response_time_histogram_ms": entry.response_times,
            }
        )
    Path(target).write_text(json.dumps(rows, indent=2), encoding="utf-8")


class Rider(HttpUser):
    wait_time = between(0.05, 0.15)

    @task(3)
    def stations(self) -> None:
        self.client.get(
            "/api/v1/stations?limit=100&bbox=-74,40.74,-73.97,40.77", name="stations_bbox"
        )

    @task(2)
    def nearby(self) -> None:
        self.client.get(
            "/api/v1/stations/nearby?lat=40.751&lon=-73.986&radius=800&limit=10", name="nearby"
        )

    @task(2)
    def inference(self) -> None:
        with self.client.get(
            f"/api/v1/stations/{STATION}/predictions", name="inference", catch_response=True
        ) as result:
            if result.status_code == 200 and len(result.json().get("forecasts", {})) != 4:
                result.failure("Incomplete forecast contract")

    @task(1)
    def recommendations(self) -> None:
        with self.client.post(
            "/api/v1/recommendations/trip",
            name="recommendations",
            catch_response=True,
            json={
                "origin": {"latitude": 40.751, "longitude": -73.986},
                "destination": {"latitude": 40.754, "longitude": -73.981},
                "departure_time": datetime.now(UTC).isoformat(),
                "preferences": {"max_walk_meters": 800, "reliability_weight": 0.7},
                "limit": 3,
            },
        ) as result:
            if result.status_code == 200 and not result.json().get("pickup"):
                result.failure("Empty recommendation result")
