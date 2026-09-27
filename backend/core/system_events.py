"""Change notices for the admin system dashboard.

The dashboard reads everything over its REST endpoints (``core/api/system.py``). This module
only tells it *what* changed, so it refetches instead of polling: every write that changes
forecast cells, forecast jobs, recurring routes or journeys calls ``notify_system`` with its
topic, once the write is committed. ``SystemEventsConsumer`` throttles and relays them.
"""

from typing import Literal

from channels.exceptions import ChannelFull
from channels.layers import get_channel_layer
from loguru import logger
from redis.exceptions import RedisError

SYSTEM_GROUP = "system"

Topic = Literal["cells", "jobs", "routes", "journeys"]
TOPICS: tuple[Topic, ...] = ("cells", "jobs", "routes", "journeys")


async def notify_system(*topics: Topic) -> None:
    """Tell every open system dashboard that *topics* changed.

    Never raises: a stale dashboard is no reason to fail the write that caused it.
    """
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        await layer.group_send(SYSTEM_GROUP, {"type": "system.event", "topics": list(topics)})
    except (RedisError, OSError, ChannelFull) as exc:
        logger.warning(f"Could not notify the system dashboard of {topics}: {exc}")
