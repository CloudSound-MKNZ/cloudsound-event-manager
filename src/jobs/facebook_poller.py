"""Scheduled job for polling Facebook Events.

Periodically fetches events from configured Facebook pages
and publishes them for processing.
"""
import asyncio
from typing import Optional, List
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
import structlog

from ..clients.facebook_client import FacebookEventsClient, FacebookEvent
from ..producers.kafka_producer import EventProducer

logger = structlog.get_logger(__name__)


class FacebookPoller:
    """Scheduled job for polling Facebook Events API.
    
    Runs on a configurable interval and publishes new events
    to Kafka for downstream processing.
    
    Usage:
        poller = FacebookPoller(
            facebook_client=client,
            event_producer=producer,
            poll_interval_minutes=30,
        )
        
        poller.start()
        
        # Later...
        poller.stop()
    """
    
    def __init__(
        self,
        facebook_client: FacebookEventsClient,
        event_producer: Optional[EventProducer] = None,
        poll_interval_minutes: int = 30,
        enabled: bool = True,
    ):
        """Initialize Facebook poller.
        
        Args:
            facebook_client: Facebook API client
            event_producer: Kafka producer for events
            poll_interval_minutes: Polling interval in minutes
            enabled: Whether polling is enabled
        """
        self.facebook_client = facebook_client
        self.event_producer = event_producer
        self.poll_interval_minutes = poll_interval_minutes
        self.enabled = enabled
        
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._is_running = False
        self._last_poll: Optional[datetime] = None
        self._poll_count = 0
        
        logger.info(
            "facebook_poller_initialized",
            poll_interval_minutes=poll_interval_minutes,
            enabled=enabled,
        )
    
    def start(self) -> None:
        """Start the polling scheduler."""
        if not self.enabled:
            logger.info("facebook_poller_disabled")
            return
        
        if self._is_running:
            logger.warning("facebook_poller_already_running")
            return
        
        self._scheduler = AsyncIOScheduler()
        
        # Add the polling job
        self._scheduler.add_job(
            self._poll_task,
            trigger=IntervalTrigger(minutes=self.poll_interval_minutes),
            id="facebook_poll",
            name="Poll Facebook Events",
            replace_existing=True,
        )
        
        self._scheduler.start()
        self._is_running = True
        
        logger.info(
            "facebook_poller_started",
            interval_minutes=self.poll_interval_minutes,
        )
        
        # Run initial poll
        asyncio.create_task(self._poll_task())
    
    def stop(self) -> None:
        """Stop the polling scheduler."""
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
            self._scheduler = None
        
        self._is_running = False
        logger.info("facebook_poller_stopped")
    
    async def _poll_task(self) -> None:
        """Execute a single poll cycle."""
        self._poll_count += 1
        poll_id = self._poll_count
        
        logger.info("facebook_poll_started", poll_id=poll_id)
        
        try:
            # Fetch events from all configured pages
            events = await self.facebook_client.poll_all_pages()
            
            if events:
                # Publish to Kafka
                await self._publish_events(events)
            
            self._last_poll = datetime.now()
            
            logger.info(
                "facebook_poll_completed",
                poll_id=poll_id,
                event_count=len(events),
            )
            
        except Exception as e:
            logger.error(
                "facebook_poll_failed",
                poll_id=poll_id,
                error=str(e),
            )
    
    async def _publish_events(self, events: List[FacebookEvent]) -> None:
        """Publish fetched events to Kafka.
        
        Args:
            events: List of events to publish
        """
        if not self.event_producer:
            logger.debug("facebook_poller_no_producer")
            return
        
        for event in events:
            try:
                self.event_producer.publish_raw_event(
                    event_id=event.event_id,
                    name=event.name,
                    description=event.description,
                    start_time=event.start_time,
                    place=event.place,
                    attending_count=event.attending_count,
                    source_url=event.event_url,
                )
            except Exception as e:
                logger.error(
                    "facebook_event_publish_failed",
                    event_id=event.event_id,
                    error=str(e),
                )
    
    async def poll_now(self) -> List[FacebookEvent]:
        """Trigger an immediate poll (for testing/API).
        
        Returns:
            List of fetched events
        """
        await self._poll_task()
        events = await self.facebook_client.poll_all_pages()
        return events
    
    def get_status(self) -> dict:
        """Get poller status.
        
        Returns:
            Status dict
        """
        return {
            "enabled": self.enabled,
            "is_running": self._is_running,
            "poll_interval_minutes": self.poll_interval_minutes,
            "last_poll": self._last_poll.isoformat() if self._last_poll else None,
            "poll_count": self._poll_count,
        }

