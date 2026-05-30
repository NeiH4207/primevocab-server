from aiforen.core.config import get_settings

from .base import PaymentProvider
from .mock import MockPaymentProvider

_settings = get_settings()


def get_payment_provider() -> PaymentProvider:
    return MockPaymentProvider()
