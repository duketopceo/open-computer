"""R7 — dependency gaps queue, never hallucinate."""

from checks.fsproto_checks import consumer_queues_until_tombstone


def test_r7_consumer_queues_until_tombstone(fsproto):
    consumer_queues_until_tombstone(fsproto)
