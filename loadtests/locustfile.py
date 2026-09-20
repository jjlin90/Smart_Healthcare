"""300-user HTTP ingress load test. This deliberately does not claim model throughput."""

from locust import HttpUser, between, task


class ApiIngressUser(HttpUser):
    wait_time = between(0.05, 0.2)

    @task
    def health(self) -> None:
        with self.client.get("/api/health", name="GET /api/health", catch_response=True) as response:
            if response.status_code != 200:
                response.failure(f"HTTP {response.status_code}")
                return
            body = response.json()
            if body.get("service") != "MedAgent AI":
                response.failure("unexpected response payload")
