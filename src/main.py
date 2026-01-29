"""Event Manager service main application.

Handles Facebook Events integration, polling, and linking
to CloudSound concerts.

PLACEHOLDER: Facebook API integration is mocked. To enable real API:
1. Set USE_MOCK_APIS=false
2. Provide FACEBOOK_ACCESS_TOKEN
3. Configure FACEBOOK_PAGE_IDS
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import structlog

from cloudsound_shared.health import router as health_router
from cloudsound_shared.metrics import get_metrics
from cloudsound_shared.middleware.error_handler import register_exception_handlers
from cloudsound_shared.middleware.correlation import CorrelationIDMiddleware
from cloudsound_shared.logging import configure_logging, get_logger
from cloudsound_shared.config.settings import app_settings

from .metrics import init_metrics
from .clients.facebook_client import FacebookEventsClient
from .services.event_parser import EventParser
from .services.enrichment_service import EnrichmentService
from .services.linking_service import LinkingService
from .jobs.facebook_poller import FacebookPoller
from .producers.kafka_producer import EventProducer
from .consumers.kafka_consumer import EventPipelineConsumer

# Configure logging
configure_logging(log_level=app_settings.log_level, log_format=app_settings.log_format)
logger = get_logger(__name__)

# Global service instances
facebook_client: Optional[FacebookEventsClient] = None
event_parser: Optional[EventParser] = None
enrichment_service: Optional[EnrichmentService] = None
linking_service: Optional[LinkingService] = None
event_producer: Optional[EventProducer] = None
facebook_poller: Optional[FacebookPoller] = None
pipeline_consumer: Optional[EventPipelineConsumer] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global facebook_client, event_parser, enrichment_service, linking_service
    global event_producer, facebook_poller, pipeline_consumer

    # Startup
    logger.info("event_manager_service_starting", version=app_settings.app_version)

    # Initialize metrics
    init_metrics(app_settings.app_version)

    # Initialize Facebook client
    # Read Facebook config directly from environment to avoid timing issues
    # with module-level app_settings singleton
    import os

    facebook_token = os.getenv("FACEBOOK_ACCESS_TOKEN")
    facebook_page_ids_str = os.getenv("FACEBOOK_PAGE_IDS", "")

    # Parse page IDs from comma-separated string
    page_ids = [p.strip() for p in facebook_page_ids_str.split(",") if p.strip()]

    # Use real API if token is provided, otherwise mock
    use_mock = app_settings.use_mock_apis or not facebook_token

    # Initialize Facebook client
    # Note: fetch_days_back parameter may not be available in older versions
    # of cloudsound-shared, so we'll use it only if the client supports it
    client_kwargs = {
        "access_token": facebook_token,
        "page_ids": page_ids,
        "use_mock": use_mock,
    }

    # Try to add fetch_days_back if available (newer version)
    try:
        import inspect

        sig = inspect.signature(FacebookEventsClient.__init__)
        if "fetch_days_back" in sig.parameters:
            fetch_days_back = getattr(app_settings, "facebook_fetch_days_back", None)
            if fetch_days_back is None:
                # Default to 180 days (6 months) to sync past events as mentioned in requirements
                fetch_days_back = int(os.getenv("FACEBOOK_FETCH_DAYS_BACK", "180"))
            client_kwargs["fetch_days_back"] = fetch_days_back
    except (AttributeError, TypeError):
        pass  # Older version doesn't support it, skip

    facebook_client = FacebookEventsClient(**client_kwargs)

    # Initialize services
    event_parser = EventParser()
    enrichment_service = EnrichmentService()
    linking_service = LinkingService()

    # Initialize Kafka producer
    try:
        event_producer = EventProducer()
        event_producer.connect()
    except Exception as e:
        logger.warning("kafka_producer_init_failed", error=str(e))
        event_producer = None

    # Initialize and start poller (enabled when Facebook token is configured)
    poller_enabled = bool(facebook_token and page_ids)
    facebook_poller = FacebookPoller(
        facebook_client=facebook_client,
        event_producer=event_producer,
        poll_interval_minutes=app_settings.facebook_poll_interval_minutes,
        enabled=poller_enabled,
    )
    if poller_enabled:
        facebook_poller.start()

    # Initialize pipeline consumer
    try:
        pipeline_consumer = EventPipelineConsumer(
            parser=event_parser,
            enricher=enrichment_service,
            linker=linking_service,
        )
        pipeline_consumer.start_async()
    except Exception as e:
        logger.warning("kafka_consumer_init_failed", error=str(e))
        pipeline_consumer = None

    logger.info(
        "event_manager_service_started",
        version=app_settings.app_version,
        mock_mode=app_settings.use_mock_apis,
    )

    if app_settings.use_mock_apis:
        logger.warning(
            "event_manager_mock_mode",
            message="Using mock Facebook API. Configure credentials for real integration.",
        )

    yield

    # Shutdown
    logger.info("event_manager_service_shutting_down")

    # Stop poller
    if facebook_poller:
        facebook_poller.stop()

    # Stop consumer
    if pipeline_consumer:
        pipeline_consumer.stop()

    # Close producer
    if event_producer:
        event_producer.close()

    # Close clients
    if facebook_client:
        await facebook_client.close()
    if linking_service:
        await linking_service.close()

    logger.info("event_manager_service_shutdown")


# Create FastAPI app
app = FastAPI(
    title="CloudSound Event Manager Service",
    version=app_settings.app_version,
    description="Facebook Events integration and concert linking",
    lifespan=lifespan,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Correlation ID middleware
app.add_middleware(CorrelationIDMiddleware)

# Register all exception handlers
register_exception_handlers(app)

# Include health router
app.include_router(health_router)


# Request/Response models
class PollResponse(BaseModel):
    """Response from manual poll."""

    events_fetched: int
    events: List[Dict[str, Any]]


class ProcessEventRequest(BaseModel):
    """Request to process a single event."""

    event_id: str
    name: str
    description: Optional[str] = None
    start_time: Optional[str] = None
    place: Optional[Dict[str, Any]] = None


class ProcessEventResponse(BaseModel):
    """Response from event processing."""

    event_id: str
    parsed_valid: bool
    artists: List[str]
    music_links: List[str]
    action: str
    concert_id: Optional[str] = None


# API endpoints
@app.get("/metrics")
async def metrics() -> Response:
    """Prometheus metrics endpoint."""
    return Response(content=get_metrics(), media_type="text/plain")


@app.post(
    f"{app_settings.api_prefix}/events/poll",
    response_model=PollResponse,
)
async def trigger_poll() -> PollResponse:
    """Trigger an immediate Facebook poll.

    Fetches events from configured Facebook pages and returns them.
    For manual polls, fetches ALL available events (no date filtering)
    to ensure nothing is missed. In mock mode, returns sample events.
    """
    logger.info("events_poll_triggered", use_date_filter=False)

    # Use use_date_filter=False for manual polls to get all events
    events = await facebook_client.poll_all_pages(use_date_filter=False)

    logger.info(
        "events_poll_completed",
        events_fetched=len(events),
        sample_event_ids=[e.event_id for e in events[:5]],
        sample_event_names=[e.name for e in events[:5]],
    )

    return PollResponse(
        events_fetched=len(events),
        events=[
            {
                "event_id": e.event_id,
                "name": e.name,
                "start_time": e.start_time.isoformat() if e.start_time else None,
                "place": e.place,
                "attending_count": e.attending_count,
            }
            for e in events
        ],
    )


@app.post(
    f"{app_settings.api_prefix}/events/sync",
    response_model=Dict[str, Any],
)
async def sync_events() -> Dict[str, Any]:
    """Sync Facebook events and create/update concerts.

    This endpoint:
    1. Fetches events from Facebook
    2. Processes them through the pipeline (parse → enrich → link)
    3. Creates/updates concerts directly
    4. Bypasses Event Hubs for immediate processing

    Returns summary of actions taken.
    """
    import json
    import os
    from datetime import datetime

    # #region agent log
    log_path = "/home/tef/Gits/Cloudsound-Workspace/CloudSound/.cursor/debug.log"
    try:
        with open(log_path, "a") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "A",
                        "location": "main.py:281",
                        "message": "sync_events entry",
                        "data": {
                            "fetch_days_back": getattr(facebook_client, "fetch_days_back", None)
                            if facebook_client
                            else None,
                            "page_ids": facebook_client.page_ids if facebook_client else None,
                            "use_mock": facebook_client.use_mock if facebook_client else None,
                        },
                        "timestamp": int(datetime.now().timestamp() * 1000),
                    }
                )
                + "\n"
            )
    except:
        pass
    # #endregion agent log

    from .consumers.kafka_consumer import EventPipelineConsumer

    # Fetch events - use use_date_filter=False to get all events for manual sync
    events = await facebook_client.poll_all_pages(use_date_filter=False)

    # #region agent log
    try:
        with open(log_path, "a") as f:
            f.write(
                json.dumps(
                    {
                        "sessionId": "debug-session",
                        "runId": "run1",
                        "hypothesisId": "A",
                        "location": "main.py:310",
                        "message": "poll_all_pages returned",
                        "data": {
                            "events_count": len(events),
                            "event_ids": [e.event_id for e in events[:5]],
                            "event_names": [e.name for e in events[:5]],
                            "event_start_times": [
                                e.start_time.isoformat() if e.start_time else None
                                for e in events[:5]
                            ],
                        },
                        "timestamp": int(datetime.now().timestamp() * 1000),
                    }
                )
                + "\n"
            )
    except:
        pass
    # #endregion agent log

    if not events:
        # #region agent log
        try:
            with open(log_path, "a") as f:
                f.write(
                    json.dumps(
                        {
                            "sessionId": "debug-session",
                            "runId": "run1",
                            "hypothesisId": "A",
                            "location": "main.py:315",
                            "message": "no events found - returning early",
                            "data": {},
                            "timestamp": int(datetime.now().timestamp() * 1000),
                        }
                    )
                    + "\n"
                )
        except:
            pass
        # #endregion agent log
        return {
            "events_fetched": 0,
            "processed": 0,
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "message": "No events found",
        }

    # Process each event through the pipeline
    results = []
    created_count = 0
    updated_count = 0
    skipped_count = 0

    for fb_event in events:
        try:
            # #region agent log
            try:
                with open(log_path, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "sessionId": "debug-session",
                                "runId": "run1",
                                "hypothesisId": "C",
                                "location": "main.py:340",
                                "message": "processing event",
                                "data": {
                                    "event_id": fb_event.event_id,
                                    "event_name": fb_event.name,
                                    "start_time": fb_event.start_time.isoformat()
                                    if fb_event.start_time
                                    else None,
                                },
                                "timestamp": int(datetime.now().timestamp() * 1000),
                            }
                        )
                        + "\n"
                    )
            except:
                pass
            # #endregion agent log

            # Parse
            parsed = event_parser.parse(fb_event)

            # #region agent log
            try:
                with open(log_path, "a") as f:
                    f.write(
                        json.dumps(
                            {
                                "sessionId": "debug-session",
                                "runId": "run1",
                                "hypothesisId": "C",
                                "location": "main.py:355",
                                "message": "parsed event",
                                "data": {
                                    "event_id": fb_event.event_id,
                                    "is_valid": parsed.is_valid,
                                    "parse_errors": parsed.parse_errors
                                    if hasattr(parsed, "parse_errors")
                                    else [],
                                },
                                "timestamp": int(datetime.now().timestamp() * 1000),
                            }
                        )
                        + "\n"
                    )
            except:
                pass
            # #endregion agent log

            if not parsed.is_valid:
                skipped_count += 1
                results.append(
                    {
                        "event_id": fb_event.event_id,
                        "status": "skipped",
                        "reason": "Invalid event data",
                    }
                )
                continue

            # Enrich
            enriched = await enrichment_service.enrich(parsed)

            # Link (creates/updates concert)
            link_result = await linking_service.link_event(enriched)

            if link_result.action == "created":
                created_count += 1
            elif link_result.action == "updated":
                updated_count += 1
            else:
                skipped_count += 1

            results.append(
                {
                    "event_id": fb_event.event_id,
                    "status": "processed",
                    "action": link_result.action,
                    "concert_id": link_result.concert_id,
                }
            )

        except Exception as e:
            import traceback

            error_traceback = traceback.format_exc()
            logger.error(
                "event_sync_failed",
                event_id=fb_event.event_id,
                error=str(e),
                traceback=error_traceback,
            )
            skipped_count += 1
            results.append(
                {
                    "event_id": fb_event.event_id,
                    "status": "error",
                    "error": str(e),
                    "traceback": error_traceback,
                }
            )

    return {
        "events_fetched": len(events),
        "processed": len(results),
        "created": created_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "results": results,
    }


@app.post(
    f"{app_settings.api_prefix}/events/process",
    response_model=ProcessEventResponse,
)
async def process_event(request: ProcessEventRequest) -> ProcessEventResponse:
    """Process a single event through the pipeline.

    Parses, enriches, and attempts to link the event to a concert.
    """
    from datetime import datetime
    from .clients.facebook_client import FacebookEvent

    # Convert to FacebookEvent
    start_time = None
    if request.start_time:
        try:
            start_time = datetime.fromisoformat(request.start_time)
        except ValueError:
            pass

    fb_event = FacebookEvent(
        event_id=request.event_id,
        name=request.name,
        description=request.description,
        start_time=start_time,
        place=request.place,
    )

    # Process through pipeline
    parsed = event_parser.parse(fb_event)
    enriched = await enrichment_service.enrich(parsed)
    result = await linking_service.link_event(enriched)

    return ProcessEventResponse(
        event_id=request.event_id,
        parsed_valid=parsed.is_valid,
        artists=parsed.artists,
        music_links=parsed.music_links,
        action=result.action,
        concert_id=result.concert_id,
    )


@app.get(f"{app_settings.api_prefix}/events/status")
async def get_status() -> Dict[str, Any]:
    """Get service status."""
    return {
        "service": "event-manager",
        "version": app_settings.app_version,
        "mock_mode": app_settings.use_mock_apis,
        "poller": facebook_poller.get_status() if facebook_poller else None,
        "facebook_client": facebook_client.get_circuit_breaker_stats() if facebook_client else None,
    }


@app.get(f"{app_settings.api_prefix}/events/mock")
async def get_mock_events() -> Dict[str, Any]:
    """Get sample mock events (for testing).

    Returns mock Facebook events that would be fetched from the API.
    """
    events = await facebook_client.get_events(limit=5)

    return {
        "count": len(events.events),
        "events": [
            {
                "event_id": e.event_id,
                "name": e.name,
                "description": e.description[:200] + "..."
                if e.description and len(e.description) > 200
                else e.description,
                "start_time": e.start_time.isoformat() if e.start_time else None,
                "place": e.place,
                "url": e.event_url,
            }
            for e in events.events
        ],
    }


@app.get(f"{app_settings.api_prefix}/events/verify-token")
async def verify_facebook_token() -> Dict[str, Any]:
    """Verify Facebook access token validity.

    Tests the token by making a simple API call. Page Access Tokens
    generated from long-lived user tokens don't expire.

    Returns token information including validity status.
    """
    if not facebook_client:
        return {"status": "error", "error": "Facebook client not initialized"}

    try:
        token_info = await facebook_client.verify_token()
        return {
            "status": "success" if token_info.get("valid") else "error",
            "token_info": token_info,
            "recommendation": "Token is valid. Page Access Tokens from long-lived user tokens don't expire."
            if token_info.get("valid") and token_info.get("token_type") == "Page Access Token"
            else "Token verification completed. Check token_info for details.",
        }
    except Exception as e:
        logger.error("token_verification_failed", error=str(e))
        return {"status": "error", "error": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8002)
