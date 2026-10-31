"""Message bus module for decoupled channel-agent communication."""

from aleggo.partners.bus.events import InboundMessage, OutboundMessage
from aleggo.partners.bus.queue import MessageBus

__all__ = ["MessageBus", "InboundMessage", "OutboundMessage"]
