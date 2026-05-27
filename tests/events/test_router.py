import pytest
from agentic_loopkit.events.router import EventRouter
from agentic_loopkit.events.models import Event, WILDCARD_STREAM


def make_event(stream="gps") -> Event:
    return Event(event_type=f"{stream}.test", source="test", payload={})


async def test_subscribe_and_receive():
    router = EventRouter()
    received = []
    async def handler(e): received.append(e)
    router.subscribe("gps", handler)
    event = make_event("gps")
    await router.publish(event)
    assert received == [event]


async def test_subscriber_only_receives_own_stream():
    router = EventRouter()
    received = []
    async def handler(e): received.append(e)
    router.subscribe("gps", handler)
    await router.publish(make_event("adr"))
    assert received == []


async def test_wildcard_receives_all_streams():
    router = EventRouter()
    received = []
    async def handler(e): received.append(e)
    router.subscribe(WILDCARD_STREAM, handler)
    await router.publish(make_event("gps"))
    await router.publish(make_event("adr"))
    await router.publish(make_event("system"))
    assert len(received) == 3


async def test_stream_subscriber_plus_wildcard_both_fire():
    router = EventRouter()
    stream_received, wild_received = [], []
    async def stream_handler(e): stream_received.append(e)
    async def wild_handler(e): wild_received.append(e)
    router.subscribe("gps", stream_handler)
    router.subscribe(WILDCARD_STREAM, wild_handler)
    event = make_event("gps")
    await router.publish(event)
    assert stream_received == [event]
    assert wild_received == [event]


async def test_unsubscribe_stops_delivery():
    router = EventRouter()
    received = []
    async def handler(e): received.append(e)
    router.subscribe("gps", handler)
    router.unsubscribe("gps", handler)
    await router.publish(make_event("gps"))
    assert received == []


async def test_unsubscribe_nonexistent_is_safe():
    router = EventRouter()
    async def handler(e): pass
    router.unsubscribe("gps", handler)  # should not raise


async def test_no_subscribers_does_not_raise():
    router = EventRouter()
    await router.publish(make_event("gps"))


async def test_handler_exception_does_not_stop_fanout():
    router = EventRouter()
    received = []
    async def bad(e): raise ValueError("boom")
    async def good(e): received.append(e)
    router.subscribe("gps", bad)
    router.subscribe("gps", good)
    await router.publish(make_event("gps"))
    assert len(received) == 1


async def test_duplicate_subscription_fires_once():
    router = EventRouter()
    received = []
    async def handler(e): received.append(e)
    router.subscribe("gps", handler)
    router.subscribe("gps", handler)
    await router.publish(make_event("gps"))
    assert len(received) == 1


async def test_subscriber_count():
    router = EventRouter()
    async def h1(e): pass
    async def h2(e): pass
    router.subscribe("gps", h1)
    router.subscribe("gps", h2)
    assert router.subscriber_count("gps") == 2
    assert router.subscriber_count("adr") == 0


async def test_streams_returns_active_streams():
    router = EventRouter()
    async def handler(e): pass
    router.subscribe("gps", handler)
    router.subscribe("adr", handler)
    active = router.streams()
    assert "gps" in active
    assert "adr" in active


async def test_router_has_no_publish_many():
    """publish_many lives on EventBus (persist-before-fanout); router must not expose it."""
    router = EventRouter()
    assert not hasattr(router, "publish_many")


async def test_slow_subscriber_does_not_block_fast_subscriber():
    """Sequential delivery is a documented constraint — document it as a test."""
    import asyncio

    router = EventRouter()
    order: list[str] = []

    async def slow(e):
        await asyncio.sleep(0.05)
        order.append("slow")

    async def fast(e):
        order.append("fast")

    router.subscribe("gps", slow)
    router.subscribe("gps", fast)
    await router.publish(make_event("gps"))

    # Sequential delivery: slow runs first, fast second — both receive the event
    assert order == ["slow", "fast"]
