from travel_agent.agent import _multi_stop_server_env, _route_server_env


def test_explicit_proxy_override_is_forwarded_with_storage_without_other_environment(monkeypatch):
    monkeypatch.setenv("ROUTE_PROXY_URL", "http://127.0.0.1:7897")
    monkeypatch.setenv("MULTI_STOP_STORAGE_PATH", "isolated/multi.sqlite")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-forward")
    assert _route_server_env() == {"ROUTE_PROXY_URL": "http://127.0.0.1:7897"}
    assert _multi_stop_server_env() == {
        "ROUTE_PROXY_URL": "http://127.0.0.1:7897",
        "MULTI_STOP_STORAGE_PATH": "isolated/multi.sqlite",
    }


def test_absent_overrides_preserve_server_dotenv_defaults(monkeypatch):
    monkeypatch.delenv("ROUTE_PROXY_URL", raising=False)
    monkeypatch.delenv("MULTI_STOP_STORAGE_PATH", raising=False)
    assert _route_server_env() is None
    assert _multi_stop_server_env() is None
