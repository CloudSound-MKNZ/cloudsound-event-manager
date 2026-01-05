"""Circuit breaker implementation for external API calls.

Re-exports from music-discovery for consistency. In production,
this would be moved to the shared package.
"""
import time
import threading
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass
import structlog

logger = structlog.get_logger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpen(Exception):
    """Exception raised when circuit breaker is open."""
    
    def __init__(self, name: str, reset_time: float):
        self.name = name
        self.reset_time = reset_time
        super().__init__(f"Circuit breaker '{name}' is open. Retry after {reset_time:.1f}s")


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5
    success_threshold: int = 2
    timeout: float = 30.0
    excluded_exceptions: tuple = ()


class CircuitBreaker:
    """Circuit breaker for protecting external API calls."""
    
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        success_threshold: int = 2,
        timeout: float = 30.0,
        excluded_exceptions: tuple = (),
    ):
        self.name = name
        self.config = CircuitBreakerConfig(
            failure_threshold=failure_threshold,
            success_threshold=success_threshold,
            timeout=timeout,
            excluded_exceptions=excluded_exceptions,
        )
        
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.RLock()
    
    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                if self._should_try_reset():
                    self._state = CircuitState.HALF_OPEN
            return self._state
    
    def _should_try_reset(self) -> bool:
        if self._last_failure_time is None:
            return False
        return time.time() - self._last_failure_time >= self.config.timeout
    
    def _record_failure(self, error: Exception) -> None:
        with self._lock:
            self._failure_count += 1
            self._success_count = 0
            self._last_failure_time = time.time()
            
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.config.failure_threshold:
                self._state = CircuitState.OPEN
    
    def _record_success(self) -> None:
        with self._lock:
            self._success_count += 1
            
            if self._state == CircuitState.HALF_OPEN:
                if self._success_count >= self.config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0
    
    async def call_async(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        current_state = self.state
        
        if current_state == CircuitState.OPEN:
            time_until_reset = (
                self._last_failure_time + self.config.timeout - time.time()
                if self._last_failure_time else 0
            )
            raise CircuitBreakerOpen(self.name, max(0, time_until_reset))
        
        try:
            result = await func(*args, **kwargs)
            self._record_success()
            return result
        except self.config.excluded_exceptions:
            raise
        except Exception as e:
            self._record_failure(e)
            raise
    
    def get_stats(self) -> dict:
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
            }

