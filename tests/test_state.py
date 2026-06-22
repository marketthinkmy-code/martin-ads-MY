import adbot.state as state


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    state.save("media_cache", {"file1": {"meta_id": "vid_9"}})
    assert state.load("media_cache") == {"file1": {"meta_id": "vid_9"}}


def test_load_missing_returns_default(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    assert state.load("nope") == {}
    assert state.load("nope", default=[]) == []


def test_append_pause_log(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    state.append_pause_log("ad1", "ad", "cpl_over_threshold", {"cpl": 55.0})
    state.append_pause_log("ad2", "ad", "weekly_off", {"name": "x"})
    log = state.load("pause_log", default=[])
    assert len(log) == 2
    assert log[0]["entity_id"] == "ad1" and log[0]["reason"] == "cpl_over_threshold"
    assert log[1]["entity_type"] == "ad" and "ts" in log[1]
