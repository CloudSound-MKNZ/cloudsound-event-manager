"""Kafka consumer for event processing pipeline.

Consumes raw Facebook events and processes them through
the parsing, enrichment, and linking pipeline.
"""
import asyncio
import threading
from typing import Optional, Dict, Any, List
from datetime import datetime
import structlog

from cloudsound_shared.kafka import KafkaConsumerClient
from cloudsound_shared.config.settings import app_settings

from ..services.event_parser import EventParser, ParsedEvent
from ..services.enrichment_service import EnrichmentService
from ..services.linking_service import LinkingService
from ..clients.facebook_client import FacebookEvent

logger = structlog.get_logger(__name__)

# Topics
RAW_EVENTS_TOPIC = "facebook.events.raw"
PROCESSED_EVENTS_TOPIC = "facebook.events.processed"


class EventPipelineConsumer:
    """Kafka consumer for processing Facebook events.
    
    Implements the event processing pipeline:
    1. Consume raw events from Kafka
    2. Parse event data
    3. Enrich with additional metadata
    4. Link to CloudSound concerts
    
    Usage:
        consumer = EventPipelineConsumer(
            parser=EventParser(),
            enricher=EnrichmentService(),
            linker=LinkingService(),
        )
        
        consumer.start()  # Blocking
        # or
        consumer.start_async()  # Background thread
    """
    
    def __init__(
        self,
        parser: Optional[EventParser] = None,
        enricher: Optional[EnrichmentService] = None,
        linker: Optional[LinkingService] = None,
        topic: str = RAW_EVENTS_TOPIC,
        group_id: str = "event-manager",
    ):
        """Initialize event pipeline consumer.
        
        Args:
            parser: Event parser service
            enricher: Enrichment service
            linker: Linking service
            topic: Kafka topic to consume
            group_id: Consumer group ID
        """
        self.parser = parser or EventParser()
        self.enricher = enricher or EnrichmentService()
        self.linker = linker or LinkingService()
        self.topic = topic
        self.group_id = group_id
        
        self._consumer: Optional[KafkaConsumerClient] = None
        self._running = False
        self._consumer_thread: Optional[threading.Thread] = None
        self._event_loop: Optional[asyncio.AbstractEventLoop] = None
        
        logger.info(
            "event_pipeline_consumer_initialized",
            topic=topic,
            group_id=group_id,
        )
    
    def _create_consumer(self) -> KafkaConsumerClient:
        """Create Kafka consumer client."""
        return KafkaConsumerClient(
            topics=[self.topic],
            group_id=self.group_id,
            bootstrap_servers=app_settings.kafka_bootstrap_servers,
            auto_offset_reset="earliest",
        )
    
    def start(self) -> None:
        """Start consuming messages (blocking)."""
        logger.info("event_pipeline_consumer_starting", topic=self.topic)
        
        self._consumer = self._create_consumer()
        self._running = True
        
        # Create event loop for async operations
        self._event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._event_loop)
        
        try:
            for message in self._consumer.consume():
                if not self._running:
                    break
                
                try:
                    self._handle_message(message)
                except Exception as e:
                    logger.error(
                        "event_pipeline_message_error",
                        topic=message.topic,
                        error=str(e),
                    )
        finally:
            if self._event_loop:
                self._event_loop.close()
            self._consumer.close()
            logger.info("event_pipeline_consumer_stopped")
    
    def start_async(self) -> None:
        """Start consuming in a background thread."""
        if self._consumer_thread and self._consumer_thread.is_alive():
            logger.warning("event_pipeline_consumer_already_running")
            return
        
        self._consumer_thread = threading.Thread(
            target=self.start,
            daemon=True,
            name="event-pipeline-consumer",
        )
        self._consumer_thread.start()
        logger.info("event_pipeline_consumer_started_async")
    
    def stop(self) -> None:
        """Stop consuming messages."""
        self._running = False
        
        if self._consumer_thread:
            self._consumer_thread.join(timeout=5)
        
        logger.info("event_pipeline_consumer_stop_requested")
    
    def _handle_message(self, message: Any) -> None:
        """Handle a Kafka message through the pipeline.
        
        Args:
            message: Kafka message with raw event data
        """
        value = message.value
        
        logger.debug(
            "event_pipeline_message_received",
            topic=message.topic,
            key=message.key,
        )
        
        # Convert to FacebookEvent
        fb_event = self._to_facebook_event(value)
        
        # Process through pipeline
        self._event_loop.run_until_complete(
            self._process_event(fb_event)
        )
    
    async def _process_event(self, fb_event: FacebookEvent) -> None:
        """Process event through the full pipeline.
        
        Args:
            fb_event: Facebook event to process
        """
        # Parse
        parsed = self.parser.parse(fb_event)
        
        if not parsed.is_valid:
            logger.warning(
                "event_parse_invalid",
                event_id=fb_event.event_id,
                errors=parsed.parse_errors,
            )
            return
        
        # Enrich
        enriched = await self.enricher.enrich(parsed)
        
        # Link to concert
        result = await self.linker.link_event(enriched)
        
        logger.info(
            "event_pipeline_completed",
            event_id=fb_event.event_id,
            action=result.action,
            concert_id=result.concert_id,
        )
    
    @staticmethod
    def _to_facebook_event(data: Dict[str, Any]) -> FacebookEvent:
        """Convert Kafka message data to FacebookEvent.
        
        Args:
            data: Raw event data from Kafka
            
        Returns:
            FacebookEvent instance
        """
        start_time = None
        if data.get("start_time"):
            try:
                start_time = datetime.fromisoformat(data["start_time"])
            except (ValueError, TypeError):
                pass
        
        return FacebookEvent(
            event_id=data.get("event_id", ""),
            name=data.get("name", ""),
            description=data.get("description"),
            start_time=start_time,
            place=data.get("place"),
            attending_count=data.get("attending_count", 0),
            interested_count=data.get("interested_count", 0),
            event_url=data.get("source_url", ""),
        )
    
    async def process_event_async(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process an event asynchronously (for testing/API).
        
        Args:
            data: Raw event data
            
        Returns:
            Processing result
        """
        fb_event = self._to_facebook_event(data)
        
        parsed = self.parser.parse(fb_event)
        enriched = await self.enricher.enrich(parsed)
        result = await self.linker.link_event(enriched)
        
        return {
            "event_id": fb_event.event_id,
            "parsed_valid": parsed.is_valid,
            "artists": parsed.artists,
            "music_links": parsed.music_links,
            "action": result.action,
            "concert_id": result.concert_id,
        }

