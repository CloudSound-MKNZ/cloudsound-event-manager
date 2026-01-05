"""Event enrichment service.

Enriches parsed events with additional data from external sources,
such as artist information and venue details.
"""
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
import structlog

from .event_parser import ParsedEvent

logger = structlog.get_logger(__name__)


@dataclass
class EnrichedEvent:
    """Event with enriched metadata."""
    parsed_event: ParsedEvent
    
    # Artist enrichment
    artist_ids: List[str] = None  # IDs from our database
    artist_images: List[str] = None
    
    # Venue enrichment
    venue_id: Optional[str] = None
    venue_capacity: Optional[int] = None
    
    # Genre/category
    genres: List[str] = None
    
    # Enrichment status
    enrichment_complete: bool = False
    enrichment_errors: List[str] = None
    
    def __post_init__(self):
        if self.artist_ids is None:
            self.artist_ids = []
        if self.artist_images is None:
            self.artist_images = []
        if self.genres is None:
            self.genres = []
        if self.enrichment_errors is None:
            self.enrichment_errors = []


class EnrichmentService:
    """Service for enriching parsed events with additional data.
    
    Adds metadata from various sources:
    - Artist database matching
    - Venue database matching
    - Genre classification
    
    Usage:
        enricher = EnrichmentService()
        
        enriched = await enricher.enrich(parsed_event)
        print(f"Found {len(enriched.artist_ids)} known artists")
    """
    
    def __init__(
        self,
        artist_db_url: Optional[str] = None,
        venue_db_url: Optional[str] = None,
    ):
        """Initialize enrichment service.
        
        Args:
            artist_db_url: URL for artist database API
            venue_db_url: URL for venue database API
        """
        self.artist_db_url = artist_db_url
        self.venue_db_url = venue_db_url
        
        # In-memory cache for development
        self._artist_cache: Dict[str, str] = {}
        self._venue_cache: Dict[str, Dict[str, Any]] = {}
        
        logger.info("enrichment_service_initialized")
    
    async def enrich(self, parsed: ParsedEvent) -> EnrichedEvent:
        """Enrich a parsed event with additional data.
        
        Args:
            parsed: Parsed event to enrich
            
        Returns:
            EnrichedEvent with additional metadata
        """
        enriched = EnrichedEvent(parsed_event=parsed)
        
        try:
            # Match artists
            if parsed.artists:
                enriched.artist_ids = await self._match_artists(parsed.artists)
            
            # Match venue
            if parsed.venue_name:
                venue_info = await self._match_venue(
                    parsed.venue_name,
                    parsed.city,
                )
                if venue_info:
                    enriched.venue_id = venue_info.get("id")
                    enriched.venue_capacity = venue_info.get("capacity")
            
            # Classify genres (placeholder)
            enriched.genres = self._classify_genres(parsed)
            
            enriched.enrichment_complete = True
            
            logger.debug(
                "event_enriched",
                source_id=parsed.source_id,
                artist_matches=len(enriched.artist_ids),
                venue_matched=enriched.venue_id is not None,
            )
            
        except Exception as e:
            enriched.enrichment_errors.append(str(e))
            logger.error(
                "event_enrichment_failed",
                source_id=parsed.source_id,
                error=str(e),
            )
        
        return enriched
    
    async def enrich_batch(
        self,
        events: List[ParsedEvent],
    ) -> List[EnrichedEvent]:
        """Enrich multiple events.
        
        Args:
            events: List of parsed events
            
        Returns:
            List of enriched events
        """
        enriched_events = []
        
        for event in events:
            enriched = await self.enrich(event)
            enriched_events.append(enriched)
        
        logger.info(
            "events_batch_enriched",
            total=len(events),
            with_artists=sum(1 for e in enriched_events if e.artist_ids),
            with_venues=sum(1 for e in enriched_events if e.venue_id),
        )
        
        return enriched_events
    
    async def _match_artists(self, artist_names: List[str]) -> List[str]:
        """Match artist names to database IDs.
        
        PLACEHOLDER: Returns empty list. Real implementation would
        query artist database or use fuzzy matching.
        
        Args:
            artist_names: List of artist names to match
            
        Returns:
            List of matched artist IDs
        """
        # TODO: Implement artist database lookup
        # For now, return empty list
        return []
    
    async def _match_venue(
        self,
        venue_name: str,
        city: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        """Match venue name to database record.
        
        PLACEHOLDER: Returns mock data. Real implementation would
        query venue database.
        
        Args:
            venue_name: Venue name to match
            city: City for disambiguation
            
        Returns:
            Venue info dict or None
        """
        # TODO: Implement venue database lookup
        # Return mock data for known venues
        known_venues = {
            "metelkova": {"id": "venue-001", "capacity": 500},
            "kino šiška": {"id": "venue-002", "capacity": 1000},
            "cvetličarna": {"id": "venue-003", "capacity": 800},
            "channel zero": {"id": "venue-004", "capacity": 300},
        }
        
        venue_lower = venue_name.lower()
        return known_venues.get(venue_lower)
    
    def _classify_genres(self, parsed: ParsedEvent) -> List[str]:
        """Classify event into music genres.
        
        PLACEHOLDER: Returns generic genres. Real implementation
        could use ML classification or artist metadata.
        
        Args:
            parsed: Parsed event
            
        Returns:
            List of genre tags
        """
        genres = []
        
        # Simple keyword matching
        text = f"{parsed.name} {parsed.description or ''}".lower()
        
        if any(kw in text for kw in ["rock", "punk", "metal", "hardcore"]):
            genres.append("rock")
        if any(kw in text for kw in ["electronic", "techno", "house", "edm"]):
            genres.append("electronic")
        if any(kw in text for kw in ["jazz", "blues", "soul"]):
            genres.append("jazz")
        if any(kw in text for kw in ["hip hop", "rap", "hip-hop"]):
            genres.append("hip-hop")
        if any(kw in text for kw in ["indie", "alternative"]):
            genres.append("indie")
        
        return genres or ["unknown"]

