import logging

from faststream.rabbit import RabbitBroker

from payment_service.core.config import get_settings
from payment_service.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
outbox_broker = RabbitBroker(settings.rabbitmq_url, fail_fast=False)
