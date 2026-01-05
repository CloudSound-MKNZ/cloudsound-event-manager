"""Prometheus metrics for event manager service."""
from prometheus_client import Counter, Histogram, Gauge, Info
import structlog

logger = structlog.get_logger(__name__)

# Service info
SERVICE_INFO = Info(
    "event_manager_service",
    "Event manager service information",
)

# Polling metrics
POLLS_TOTAL = Counter(
    "event_manager_polls_total",
    "Total number of Facebook API polls",
    ["status"],
)

POLL_DURATION = Histogram(
    "event_manager_poll_duration_seconds",
    "Time spent polling Facebook API",
    buckets=[1, 5, 10, 30, 60],
)

EVENTS_FETCHED = Counter(
    "event_manager_events_fetched_total",
    "Total events fetched from Facebook",
)

# Processing metrics
EVENTS_PROCESSED = Counter(
    "event_manager_events_processed_total",
    "Total events processed",
    ["status"],
)

EVENTS_PARSED = Counter(
    "event_manager_events_parsed_total",
    "Total events parsed",
    ["valid"],
)

EVENTS_LINKED = Counter(
    "event_manager_events_linked_total",
    "Total events linked to concerts",
    ["action"],
)

# Pipeline metrics
PIPELINE_DURATION = Histogram(
    "event_manager_pipeline_duration_seconds",
    "Time spent processing an event through the pipeline",
    buckets=[0.1, 0.5, 1, 2, 5],
)

# API client metrics
API_REQUESTS = Counter(
    "event_manager_api_requests_total",
    "Total API requests",
    ["service", "status"],
)

CIRCUIT_BREAKER_STATE = Gauge(
    "event_manager_circuit_breaker_state",
    "Circuit breaker state (0=closed, 1=half-open, 2=open)",
    ["name"],
)

# Queue metrics
KAFKA_MESSAGES_CONSUMED = Counter(
    "event_manager_kafka_messages_consumed_total",
    "Total Kafka messages consumed",
    ["topic"],
)

KAFKA_MESSAGES_PRODUCED = Counter(
    "event_manager_kafka_messages_produced_total",
    "Total Kafka messages produced",
    ["topic"],
)


def init_metrics(version: str = "1.0.0") -> None:
    """Initialize service metrics."""
    SERVICE_INFO.info({
        "version": version,
        "service": "event-manager",
    })
    logger.info("metrics_initialized", version=version)


def record_poll(status: str, duration: float = 0, events_count: int = 0) -> None:
    """Record a poll attempt."""
    POLLS_TOTAL.labels(status=status).inc()
    if duration > 0:
        POLL_DURATION.observe(duration)
    if events_count > 0:
        EVENTS_FETCHED.inc(events_count)


def record_event_processed(status: str) -> None:
    """Record an event processing."""
    EVENTS_PROCESSED.labels(status=status).inc()


def record_event_parsed(valid: bool) -> None:
    """Record an event parsing result."""
    EVENTS_PARSED.labels(valid=str(valid).lower()).inc()


def record_event_linked(action: str) -> None:
    """Record an event linking result."""
    EVENTS_LINKED.labels(action=action).inc()


def record_pipeline_duration(duration: float) -> None:
    """Record pipeline processing duration."""
    PIPELINE_DURATION.observe(duration)


def update_circuit_breaker(name: str, state: str) -> None:
    """Update circuit breaker state metric."""
    state_values = {"closed": 0, "half_open": 1, "open": 2}
    CIRCUIT_BREAKER_STATE.labels(name=name).set(state_values.get(state, 0))

