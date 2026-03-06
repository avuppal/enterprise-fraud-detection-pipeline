"""
Kafka consumer for the fraud detection pipeline (Phase 3).

Reads transactions from the 'transactions' topic and calls the
PredictionService /predict endpoint for each message.

Design notes:
  - Kafka consumer group: "fraud-scorer" — allows horizontal scaling
    by spinning up multiple consumer instances; Kafka handles partition
    assignment automatically.
  - Back-pressure via manual commit: we only commit the offset *after* a
    successful HTTP response, so no transaction is silently dropped if
    the prediction service is temporarily unavailable.
  - Exponential back-off on HTTP failures: avoids hammering a degraded
    downstream service while still recovering automatically.
  - Dead-letter logic: after MAX_RETRIES failures the message is written
    to a local DLQ file for offline inspection / replay.

Usage:
    python consumer.py [--bootstrap-servers localhost:9092] \
                       [--topic transactions] \
                       [--prediction-url http://localhost:8001/predict] \
                       [--group-id fraud-scorer]

Environment variables (override CLI defaults):
    KAFKA_BOOTSTRAP_SERVERS
    KAFKA_TOPIC
    PREDICTION_SERVICE_URL
    KAFKA_GROUP_ID
"""

import argparse
import json
import logging
import os
import time
from pathlib import Path

import requests

# kafka-python is the standard OSS Kafka client for Python.
# If confluent-kafka is preferred in production, the API surface is nearly
# identical; swap the import and Consumer constructor accordingly.
try:
    from kafka import KafkaConsumer
    from kafka.errors import NoBrokersAvailable
    KAFKA_AVAILABLE = True
except ImportError:
    KAFKA_AVAILABLE = False

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("fraud-consumer")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_RETRIES = 3          # HTTP retries per message before DLQ
INITIAL_BACKOFF_S = 1.0  # first retry wait in seconds
BACKOFF_MULTIPLIER = 2.0 # doubles on each retry
DLQ_PATH = Path("data_streamer/dead_letter.jsonl")

# ---------------------------------------------------------------------------
# Dead-letter queue helper
# ---------------------------------------------------------------------------

def write_to_dlq(message_value: dict, reason: str) -> None:
    """
    Append a failed message to the dead-letter queue file.

    In production this would publish to a separate Kafka topic or an
    S3 bucket; a local file is sufficient for the dev/demo stage.
    """
    DLQ_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": time.time(),
        "reason": reason,
        "payload": message_value,
    }
    with DLQ_PATH.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")
    logger.warning("Message written to DLQ: %s", DLQ_PATH)

# ---------------------------------------------------------------------------
# Prediction call with retry + back-off
# ---------------------------------------------------------------------------

def call_prediction_service(
    prediction_url: str,
    transaction: dict,
) -> dict | None:
    """
    POST *transaction* to the prediction service.

    Returns the parsed JSON response on success, None after all retries.
    Implements truncated exponential back-off: waits 1s, 2s, 4s before
    giving up, capping at MAX_RETRIES attempts.
    """
    backoff = INITIAL_BACKOFF_S
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(prediction_url, json=transaction, timeout=5.0)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning(
                "Prediction call failed (attempt %d/%d): %s",
                attempt, MAX_RETRIES, exc,
            )
            if attempt < MAX_RETRIES:
                logger.info("Retrying in %.1fs…", backoff)
                time.sleep(backoff)
                backoff *= BACKOFF_MULTIPLIER
    return None

# ---------------------------------------------------------------------------
# Scoring decision
# ---------------------------------------------------------------------------

FRAUD_THRESHOLD = float(os.getenv("FRAUD_THRESHOLD", "0.80"))

def handle_prediction(transaction: dict, result: dict) -> None:
    """
    Act on the score returned by the prediction service.

    In production, high-risk transactions would be published to an
    alerting topic (e.g. 'fraud-alerts') or written to a PostgreSQL
    audit table.  Here we log the outcome so the pipeline is observable.
    """
    score = result.get("fraud_score", 0.0)
    tx_id = transaction.get("tx_id", "unknown")
    req_id = result.get("request_id", "?")

    if score >= FRAUD_THRESHOLD:
        logger.warning(
            "[HIGH RISK] tx=%s req=%s score=%.3f — flagged for review",
            tx_id, req_id, score,
        )
    else:
        logger.info(
            "[ok] tx=%s req=%s score=%.3f", tx_id, req_id, score
        )

# ---------------------------------------------------------------------------
# Main consumer loop
# ---------------------------------------------------------------------------

def run_consumer(
    bootstrap_servers: str,
    topic: str,
    prediction_url: str,
    group_id: str,
) -> None:
    """
    Connect to Kafka and process messages in a blocking loop.

    The consumer is configured with:
      - auto_offset_reset='earliest': replay from the beginning if the
        group has no committed offset (useful for first-run / recovery).
      - enable_auto_commit=False: we commit manually after a successful
        prediction so we never lose a message silently.
    """
    if not KAFKA_AVAILABLE:
        raise RuntimeError(
            "kafka-python is not installed. Run: pip install kafka-python"
        )

    logger.info(
        "Connecting to Kafka at %s, topic='%s', group='%s'",
        bootstrap_servers, topic, group_id,
    )

    try:
        consumer = KafkaConsumer(
            topic,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=False,
            value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
        )
    except NoBrokersAvailable as exc:
        logger.error("Could not connect to Kafka: %s", exc)
        raise

    logger.info("Consumer ready. Waiting for messages…")
    processed = 0
    errors = 0

    for msg in consumer:
        transaction = msg.value
        logger.debug(
            "Received offset=%d partition=%d: %s",
            msg.offset, msg.partition, transaction,
        )

        result = call_prediction_service(prediction_url, transaction)

        if result is None:
            errors += 1
            write_to_dlq(transaction, reason="prediction_service_unreachable")
            # Still commit the offset — we've safely persisted the message
            # in the DLQ, so re-consuming it would just produce another DLQ entry.
            consumer.commit()
            continue

        handle_prediction(transaction, result)
        consumer.commit()
        processed += 1

        if processed % 100 == 0:
            logger.info("Throughput checkpoint: processed=%d errors=%d", processed, errors)

# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fraud detection Kafka consumer → PredictionService bridge"
    )
    parser.add_argument(
        "--bootstrap-servers",
        default=os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        help="Comma-separated Kafka broker list",
    )
    parser.add_argument(
        "--topic",
        default=os.getenv("KAFKA_TOPIC", "transactions"),
        help="Kafka topic to consume from",
    )
    parser.add_argument(
        "--prediction-url",
        default=os.getenv("PREDICTION_SERVICE_URL", "http://localhost:8001/predict"),
        help="PredictionService /predict endpoint",
    )
    parser.add_argument(
        "--group-id",
        default=os.getenv("KAFKA_GROUP_ID", "fraud-scorer"),
        help="Kafka consumer group ID",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    run_consumer(
        bootstrap_servers=args.bootstrap_servers,
        topic=args.topic,
        prediction_url=args.prediction_url,
        group_id=args.group_id,
    )
