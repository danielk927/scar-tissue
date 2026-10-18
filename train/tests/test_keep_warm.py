import httpx

from scar_train.campaign import Services
from scar_train.checks import ToolCallCheckError
from scar_train.keep_warm import keep_warm, main

from .factories import model_version


class Clock:
    def __init__(self):
        self.elapsed = 0
        self.sleeps = []

    def now(self):
        return self.elapsed

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.elapsed += seconds


def test_follows_promotions_without_creating_runs_or_changing_replicas():
    clock = Clock()
    seen = []

    def handler(request):
        assert request.method == "GET"
        version = "base" if clock.now() < 120 else "D-s0-g1"
        if request.url.path == "/models/current":
            return httpx.Response(200, json={"model_version": version, "prompt_version": "system_v1"})
        assert request.url.path == "/models"
        return httpx.Response(
            200, json=[model_version(version=version, endpoint_model_id=version).model_dump(mode="json")]
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        services = Services("http://train", "http://env", "http://agent", http=http)
        result = keep_warm(
            services,
            api_key="secret",
            minutes=5,
            check=lambda url, model, key: seen.append((model, key)),
            clock=clock.now,
            sleep=clock.sleep,
            echo=lambda line: None,
        )
    assert result == (3, 0)
    assert seen == [("base", "secret"), ("D-s0-g1", "secret"), ("D-s0-g1", "secret")]
    assert clock.now() == 300 and max(clock.sleeps) <= 60


def test_failed_checks_are_visible_and_do_not_expose_keys():
    clock = Clock()
    output = []

    def handler(request):
        if request.url.path == "/models/current":
            return httpx.Response(200, json={"model_version": "base", "prompt_version": "system_v1"})
        return httpx.Response(200, json=[model_version().model_dump(mode="json")])

    def failing(url, model, key):
        raise ToolCallCheckError(f"unavailable {key}")

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result = keep_warm(
            Services("http://train", "http://env", "http://agent", http=http),
            api_key="secret",
            minutes=1,
            check=failing,
            clock=clock.now,
            sleep=clock.sleep,
            echo=output.append,
        )
    assert result == (1, 1)
    assert "failed" in output[0] and "secret" not in output[0]


def test_missing_model_key_fails_before_network(monkeypatch):
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    assert main(["--minutes", "1"]) == 1
