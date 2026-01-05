"""Retry logic with exponential backoff."""
import asyncio
import time
import random
from typing import Callable, Any, Tuple, Type
from dataclasses import dataclass, field
from functools import wraps
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class RetryConfig:
    """Configuration for retry behavior."""
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    exponential_base: float = 2.0
    jitter: bool = True
    retryable_exceptions: Tuple[Type[Exception], ...] = field(
        default_factory=lambda: (Exception,)
    )


def calculate_delay(attempt: int, config: RetryConfig) -> float:
    delay = config.base_delay * (config.exponential_base ** attempt)
    delay = min(delay, config.max_delay)
    if config.jitter:
        jitter_range = delay * 0.25
        delay = delay + random.uniform(-jitter_range, jitter_range)
    return max(0, delay)


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    retryable_exceptions: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """Decorator for retrying functions with exponential backoff."""
    config = RetryConfig(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        retryable_exceptions=retryable_exceptions,
    )
    
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> Any:
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as e:
                    if attempt == max_retries:
                        raise
                    delay = calculate_delay(attempt, config)
                    logger.warning(
                        "retry_attempt",
                        function=func.__name__,
                        attempt=attempt + 1,
                        delay=delay,
                    )
                    await asyncio.sleep(delay)
        
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return func
    
    return decorator

