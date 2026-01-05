"""Kafka producer for Facebook events.

Publishes raw and processed events to Kafka topics.
"""
from typing import Optional, Dict, Any
from datetime import datetime
import structlog

from cloudsound_shared.kafka import KafkaProducerClient
from cloudsound_shared.config.settings import app_settings

logger = structlog.get_logger(__name__)

# Topics
RAW_EVENTS_TOPIC = "facebook.events.raw"
PROCESSED_EVENTS_TOPIC = "facebook.events.processed"
CONCERT_CREATED_TOPIC = "concerts.created"


class EventProducer:
    """Kafka producer for Facebook event messages.
    
    Publishes events at different stages of processing:
    - Raw events from Facebook API
    - Processed/enriched events
    - Concert creation notifications
    
    Usage:
        producer = EventProducer()
        producer.connect()
        
        producer.publish_raw_event(
            event_id="123",
            name="Concert Name",
            description="...",
        )
    """
    
    def __init__(
        self,
        client: Optional[KafkaProducerClient] = None,
    ):
        """Initialize event producer.
        
        Args:
            client: Optional pre-configured Kafka client
        """
        self._client = client
        self._connected = False
        
        logger.info("event_producer_initialized")
    
    def connect(self) -> None:
        """Connect to Kafka."""
        if self._connected:
            return
        
        if not self._client:
            self._client = KafkaProducerClient(
                bootstrap_servers=app_settings.kafka_bootstrap_servers,
            )
        
        self._client.connect()
        self._connected = True
        
        logger.info("event_producer_connected")
    
    def close(self) -> None:
        """Close connection."""
        if self._client:
            self._client.close()
            self._connected = False
            logger.info("event_producer_closed")
    
    def publish_raw_event(
        self,
        event_id: str,
        name: str,
        description: Optional[str] = None,
        start_time: Optional[datetime] = None,
        place: Optional[Dict[str, Any]] = None,
        attending_count: int = 0,
        interested_count: int = 0,
        source_url: Optional[str] = None,
    ) -> None:
        """Publish raw Facebook event.
        
        Args:
            event_id: Facebook event ID
            name: Event name
            description: Event description
            start_time: Event start time
            place: Place/venue information
            attending_count: Number attending
            interested_count: Number interested
            source_url: Facebook event URL
        """
        if not self._connected:
            self.connect()
        
        event = {
            "event_type": "facebook.event.raw",
            "event_id": event_id,
            "name": name,
            "description": description,
            "start_time": start_time.isoformat() if start_time else None,
            "place": place,
            "attending_count": attending_count,
            "interested_count": interested_count,
            "source_url": source_url,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        self._client.send(
            RAW_EVENTS_TOPIC,
            value=event,
            key=event_id,
        )
        
        logger.debug(
            "raw_event_published",
            event_id=event_id,
            name=name,
        )
    
    def publish_processed_event(
        self,
        event_id: str,
        parsed_data: Dict[str, Any],
        enriched_data: Dict[str, Any],
        link_result: Dict[str, Any],
    ) -> None:
        """Publish processed event result.
        
        Args:
            event_id: Original event ID
            parsed_data: Parsed event data
            enriched_data: Enrichment results
            link_result: Linking result
        """
        if not self._connected:
            self.connect()
        
        event = {
            "event_type": "facebook.event.processed",
            "event_id": event_id,
            "parsed": parsed_data,
            "enriched": enriched_data,
            "link_result": link_result,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        self._client.send(
            PROCESSED_EVENTS_TOPIC,
            value=event,
            key=event_id,
        )
        
        logger.debug(
            "processed_event_published",
            event_id=event_id,
            action=link_result.get("action"),
        )
    
    def publish_concert_created(
        self,
        concert_id: str,
        name: str,
        source_event_id: str,
        artists: Optional[list] = None,
        venue: Optional[str] = None,
        date: Optional[datetime] = None,
    ) -> None:
        """Publish concert created notification.
        
        Args:
            concert_id: Created concert ID
            name: Concert name
            source_event_id: Facebook event ID
            artists: List of artists
            venue: Venue name
            date: Concert date
        """
        if not self._connected:
            self.connect()
        
        event = {
            "event_type": "concert.created.from_facebook",
            "concert_id": concert_id,
            "name": name,
            "source": "facebook",
            "source_event_id": source_event_id,
            "artists": artists or [],
            "venue": venue,
            "date": date.isoformat() if date else None,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        self._client.send(
            CONCERT_CREATED_TOPIC,
            value=event,
            key=concert_id,
        )
        
        logger.info(
            "concert_created_event_published",
            concert_id=concert_id,
            source_event_id=source_event_id,
        )
    
    def flush(self) -> None:
        """Flush pending messages."""
        if self._client and self._client.producer:
            self._client.producer.flush()

