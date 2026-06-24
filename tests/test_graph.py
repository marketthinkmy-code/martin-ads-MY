"""Graph client resilience: Meta throttles (usually HTTP 400 with a rate-limit code) must be
retried with backoff so a whole-account weekly pause/resume or monitor sweep finishes on its
own — without a human or the backup cron re-running it. Permanent 400s must still fail fast.
"""

import pytest

from adbot.clients.graph import GraphClient, GraphError, _is_rate_limited


class _Resp:
    def __init__(self, status, payload, text=""):
        self.status_code, self._payload, self.text = status, payload, text

    def json(self):
        return self._payload


class _Session:
    """Returns the queued responses in order; counts how many requests were made."""
    def __init__(self, responses):
        self._responses, self.calls = list(responses), 0

    def request(self, *args, **kwargs):
        self.calls += 1
        return self._responses.pop(0)


def _throttle(code=80004, msg="There have been too many calls from this ad account."):
    return {"error": {"code": code, "message": msg, "type": "OAuthException"}}


def test_is_rate_limited_detects_throttles():
    assert _is_rate_limited(_throttle(80004))                                  # ad-account throttle
    assert _is_rate_limited({"error": {"code": 17, "message": "User request limit reached"}})
    assert _is_rate_limited({"error": {"is_transient": True, "message": "transient"}})
    assert _is_rate_limited({"error": {"code": 999, "message": "Please slow down, rate limit"}})


def test_is_rate_limited_ignores_permanent_400_and_empty():
    assert not _is_rate_limited({"error": {"code": 100, "message": "Invalid parameter"}})
    assert not _is_rate_limited({})
    assert not _is_rate_limited(None)


def test_request_retries_throttle_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)  # no real backoff in the test
    sess = _Session([_Resp(400, _throttle()), _Resp(400, _throttle()), _Resp(200, {"id": "ok"})])
    g = GraphClient("tok", session=sess)
    assert g._request("POST", "123", data={"status": "PAUSED"}) == {"id": "ok"}
    assert sess.calls == 3  # two throttles retried, third call succeeded


def test_request_does_not_retry_permanent_400(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    sess = _Session([_Resp(400, {"error": {"code": 100, "message": "Invalid parameter"}})])
    g = GraphClient("tok", session=sess)
    with pytest.raises(GraphError):
        g._request("POST", "123", data={})
    assert sess.calls == 1  # failed fast — a bad request is not a throttle
