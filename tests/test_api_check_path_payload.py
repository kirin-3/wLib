from core.api import Api

def test_check_path_payload_contains_success():
    api = Api()
    res = api.check_path("some_fake_path", "runner")
    assert "success" in res, "Missing 'success' in return payload for check_path"
    assert res["success"] is True

    res = api.check_path("", "file")
    assert "success" in res, "Missing 'success' in return payload for check_path"
    assert res["success"] is True

    res = api.check_path("unknown", "invalid_kind")
    assert "success" in res
    assert res["success"] is False
