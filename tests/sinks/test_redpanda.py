"""Unit tests for the Redpanda producer wrapper."""

from unittest.mock import MagicMock, patch

import pytest

from lumid_data.sinks.redpanda import RedpandaConfig, RedpandaProducer


def test_produce_requires_connect() -> None:
    p = RedpandaProducer(RedpandaConfig(bootstrap_servers="localhost:9092"))
    with pytest.raises(RuntimeError):
        p.produce("topic", b"x")


def test_produce_routes_to_kafka_producer() -> None:
    p = RedpandaProducer(RedpandaConfig(bootstrap_servers="localhost:9092"))
    fake_producer = MagicMock()
    with patch("confluent_kafka.Producer", return_value=fake_producer):
        p.connect()
    p.produce("topic", b"hello", key=b"k")
    fake_producer.produce.assert_called_once()
    args, kwargs = fake_producer.produce.call_args
    assert kwargs["topic"] == "topic"
    assert kwargs["value"] == b"hello"
    assert kwargs["key"] == b"k"


def test_ensure_topic_skips_if_exists() -> None:
    p = RedpandaProducer(RedpandaConfig(bootstrap_servers="localhost:9092"))
    fake_admin = MagicMock()
    fake_admin.list_topics.return_value.topics = {"topic": object()}
    with patch("confluent_kafka.admin.AdminClient", return_value=fake_admin):
        p.ensure_topic("topic")
    fake_admin.create_topics.assert_not_called()


def test_ensure_topic_creates_when_missing() -> None:
    p = RedpandaProducer(RedpandaConfig(bootstrap_servers="localhost:9092"))
    fake_admin = MagicMock()
    fake_admin.list_topics.return_value.topics = {}
    fake_future = MagicMock()
    fake_admin.create_topics.return_value = {"topic": fake_future}
    with patch("confluent_kafka.admin.AdminClient", return_value=fake_admin):
        p.ensure_topic("topic", partitions=2, replication=1)
    fake_admin.create_topics.assert_called_once()
    fake_future.result.assert_called_once()
