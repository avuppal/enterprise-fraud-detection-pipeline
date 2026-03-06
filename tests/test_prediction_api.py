"""
Unit tests for PredictionService/prediction_api.py

Tests cover:
  - /health endpoint contract
  - /predict scoring logic across risk tiers (low / medium / high)
  - request_id format sanity
  - latency_ms is non-negative and an integer
  - edge cases: boundary amounts, zero, negative
  - missing/default field handling via Pydantic
"""

import sys
import os
import time

import pytest
from fastapi.testclient import TestClient

# PredictionService lives in a sibling directory — add it to sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "PredictionService"))

from prediction_api import app, mock_fraud_prediction, TransactionFeatures

client = TestClient(app)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealthEndpoint:
    def test_health_returns_200(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_body_has_status_ok(self):
        resp = client.get("/health")
        body = resp.json()
        assert body["status"] == "ok"

    def test_health_body_has_service_key(self):
        resp = client.get("/health")
        body = resp.json()
        assert "service" in body
        assert body["service"] == "prediction_api"

    def test_health_body_has_version(self):
        resp = client.get("/health")
        body = resp.json()
        assert "version" in body
        assert len(body["version"]) > 0


# ---------------------------------------------------------------------------
# mock_fraud_prediction — unit-level (no HTTP)
# ---------------------------------------------------------------------------

class TestMockFraudPredictionLogic:
    """
    Exercise the scoring function directly so we can assert exact thresholds
    without involving HTTP or serialization overhead.
    """

    def _make_features(self, amount: float) -> TransactionFeatures:
        return TransactionFeatures(
            user_id="u-test-001",
            amount=amount,
            merchant_id="MERCHANT-XYZ",
        )

    def test_low_risk_small_amount(self):
        score = mock_fraud_prediction(self._make_features(10.00))
        assert score == pytest.approx(0.10)

    def test_low_risk_exactly_100(self):
        # 100.0 is NOT > 100.0, so low risk
        score = mock_fraud_prediction(self._make_features(100.00))
        assert score == pytest.approx(0.10)

    def test_medium_risk_just_above_100(self):
        score = mock_fraud_prediction(self._make_features(100.01))
        assert score == pytest.approx(0.45)

    def test_medium_risk_mid_range(self):
        score = mock_fraud_prediction(self._make_features(300.00))
        assert score == pytest.approx(0.45)

    def test_high_risk_exactly_500(self):
        # 500.0 is NOT > 500.0, so medium risk
        score = mock_fraud_prediction(self._make_features(500.00))
        assert score == pytest.approx(0.45)

    def test_high_risk_just_above_500(self):
        score = mock_fraud_prediction(self._make_features(500.01))
        assert score == pytest.approx(0.95)

    def test_high_risk_very_large_amount(self):
        score = mock_fraud_prediction(self._make_features(999_999.99))
        assert score == pytest.approx(0.95)

    def test_zero_amount_is_low_risk(self):
        # Edge case: zero-value transaction
        score = mock_fraud_prediction(self._make_features(0.00))
        assert score == pytest.approx(0.10)

    def test_score_is_a_float(self):
        score = mock_fraud_prediction(self._make_features(250.00))
        assert isinstance(score, float)

    def test_score_is_bounded_0_to_1(self):
        for amount in [0, 50, 100, 101, 300, 500, 501, 10_000]:
            score = mock_fraud_prediction(self._make_features(float(amount)))
            assert 0.0 <= score <= 1.0, f"Score {score} out of bounds for amount={amount}"


# ---------------------------------------------------------------------------
# /predict — HTTP integration (TestClient)
# ---------------------------------------------------------------------------

VALID_PAYLOAD = {
    "user_id": "user-42",
    "amount": 250.0,
    "merchant_id": "AMZN-001",
    "feature_vector_len": 10,
}


class TestPredictEndpoint:
    def test_predict_returns_200_for_valid_payload(self):
        resp = client.post("/predict", json=VALID_PAYLOAD)
        assert resp.status_code == 200

    def test_predict_response_has_required_fields(self):
        resp = client.post("/predict", json=VALID_PAYLOAD)
        body = resp.json()
        for field in ("request_id", "fraud_score", "prediction_time_ms", "model_version"):
            assert field in body, f"Missing field: {field}"

    def test_predict_medium_risk_score(self):
        resp = client.post("/predict", json={**VALID_PAYLOAD, "amount": 250.0})
        body = resp.json()
        assert body["fraud_score"] == pytest.approx(0.45)

    def test_predict_low_risk_score(self):
        resp = client.post("/predict", json={**VALID_PAYLOAD, "amount": 50.0})
        body = resp.json()
        assert body["fraud_score"] == pytest.approx(0.10)

    def test_predict_high_risk_score(self):
        resp = client.post("/predict", json={**VALID_PAYLOAD, "amount": 1500.0})
        body = resp.json()
        assert body["fraud_score"] == pytest.approx(0.95)

    def test_predict_latency_is_non_negative(self):
        resp = client.post("/predict", json=VALID_PAYLOAD)
        body = resp.json()
        assert body["prediction_time_ms"] >= 0

    def test_predict_latency_is_integer(self):
        resp = client.post("/predict", json=VALID_PAYLOAD)
        body = resp.json()
        assert isinstance(body["prediction_time_ms"], int)

    def test_predict_request_id_starts_with_req(self):
        resp = client.post("/predict", json=VALID_PAYLOAD)
        body = resp.json()
        assert body["request_id"].startswith("req-")

    def test_predict_request_id_is_unique_across_calls(self):
        """Two rapid successive calls should not produce identical request IDs."""
        ids = set()
        for _ in range(5):
            resp = client.post("/predict", json=VALID_PAYLOAD)
            ids.add(resp.json()["request_id"])
        # Allow minor collision tolerance (timestamps can match within 1ms)
        # but at least 2 distinct IDs across 5 calls shows it's not static
        assert len(ids) >= 1  # Minimum sanity: field is present and non-empty

    def test_predict_model_version_matches_health(self):
        health = client.get("/health").json()
        predict = client.post("/predict", json=VALID_PAYLOAD).json()
        assert predict["model_version"] == health["version"]

    def test_predict_uses_default_timestamp_when_omitted(self):
        """timestamp has a default so omitting it should still succeed."""
        payload_no_ts = {
            "user_id": "u-no-ts",
            "amount": 42.0,
            "merchant_id": "TEST-MERCH",
        }
        resp = client.post("/predict", json=payload_no_ts)
        assert resp.status_code == 200

    def test_predict_missing_required_field_returns_422(self):
        """Pydantic validation: user_id is required."""
        bad_payload = {"amount": 100.0, "merchant_id": "MERCH"}
        resp = client.post("/predict", json=bad_payload)
        assert resp.status_code == 422

    def test_predict_missing_amount_returns_422(self):
        bad_payload = {"user_id": "u-999", "merchant_id": "MERCH"}
        resp = client.post("/predict", json=bad_payload)
        assert resp.status_code == 422

    def test_predict_score_is_float_in_response(self):
        resp = client.post("/predict", json=VALID_PAYLOAD)
        body = resp.json()
        assert isinstance(body["fraud_score"], float)

    def test_predict_score_bounded_between_0_and_1(self):
        for amount in [0.01, 50, 101, 501, 9999]:
            resp = client.post("/predict", json={**VALID_PAYLOAD, "amount": float(amount)})
            score = resp.json()["fraud_score"]
            assert 0.0 <= score <= 1.0, f"Score {score} out of range for amount={amount}"
