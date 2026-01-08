"""Event parser service for normalizing Facebook events.

Parses raw Facebook event data and extracts structured information
including venue, artists, dates, and music links.
"""
import re
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timezone
import structlog

from ..clients.facebook_client import FacebookEvent

logger = structlog.get_logger(__name__)


@dataclass
class ParsedEvent:
    """Normalized event data extracted from Facebook event."""
    source_id: str  # Original Facebook event ID
    source_type: str = "facebook"
    
    # Event details
    name: str = ""
    description: Optional[str] = None
    
    # Date/time
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    
    # Location
    venue_name: Optional[str] = None
    city: Optional[str] = None
    country: Optional[str] = None
    address: Optional[str] = None
    
    # Extracted data
    artists: List[str] = field(default_factory=list)
    music_links: List[str] = field(default_factory=list)
    ticket_price: Optional[str] = None
    
    # Metadata
    attending_count: int = 0
    interested_count: int = 0
    source_url: str = ""
    
    # Processing status
    is_valid: bool = True
    parse_errors: List[str] = field(default_factory=list)


class EventParser:
    """Service for parsing and normalizing Facebook events.
    
    Extracts structured data from raw Facebook event information,
    including artist names, venues, and music links.
    
    Usage:
        parser = EventParser()
        
        parsed = parser.parse(facebook_event)
        if parsed.is_valid:
            print(f"Artists: {parsed.artists}")
            print(f"Music links: {parsed.music_links}")
    """
    
    # Patterns for extracting information
    YOUTUBE_PATTERN = re.compile(
        r'https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[\w-]+'
    )
    BANDCAMP_PATTERN = re.compile(
        r'https?://[\w-]+\.bandcamp\.com/(?:track|album)/[\w-]+'
    )
    SOUNDCLOUD_PATTERN = re.compile(
        r'https?://(?:www\.)?soundcloud\.com/[\w-]+/[\w-]+'
    )
    SPOTIFY_PATTERN = re.compile(
        r'https?://open\.spotify\.com/(?:track|album)/[\w]+'
    )
    
    # Price patterns
    PRICE_PATTERN = re.compile(
        r'(?:tickets?|entry|vstopnina)[:\s]*(\d+(?:[.,]\d{2})?)\s*€',
        re.IGNORECASE
    )
    
    def __init__(self):
        """Initialize event parser."""
        logger.info("event_parser_initialized")
    
    def parse(self, event: FacebookEvent) -> ParsedEvent:
        """Parse a Facebook event into normalized format.
        
        Args:
            event: Raw Facebook event
            
        Returns:
            ParsedEvent with extracted/normalized data
        """
        parsed = ParsedEvent(
            source_id=event.event_id,
            source_type="facebook",
            name=event.name,
            description=event.description,
            start_time=event.start_time,
            end_time=event.end_time,
            attending_count=event.attending_count,
            interested_count=event.interested_count,
            source_url=event.event_url,
        )
        
        # Parse location
        if event.place:
            parsed.venue_name = event.place.get("name")
            location = event.place.get("location", {})
            parsed.city = location.get("city")
            parsed.country = location.get("country")
            parsed.address = location.get("street")
        
        # Extract music links from description
        if event.description:
            parsed.music_links = self._extract_music_links(event.description)
            parsed.artists = self._extract_artists(event.name, event.description)
            parsed.ticket_price = self._extract_price(event.description)
        
        # Validate
        self._validate(parsed)
        
        logger.debug(
            "event_parsed",
            source_id=parsed.source_id,
            artists=parsed.artists,
            music_links_count=len(parsed.music_links),
            is_valid=parsed.is_valid,
        )
        
        return parsed
    
    def parse_batch(self, events: List[FacebookEvent]) -> List[ParsedEvent]:
        """Parse multiple events.
        
        Args:
            events: List of Facebook events
            
        Returns:
            List of parsed events
        """
        parsed_events = []
        
        for event in events:
            try:
                parsed = self.parse(event)
                parsed_events.append(parsed)
            except Exception as e:
                logger.error(
                    "event_parse_failed",
                    event_id=event.event_id,
                    error=str(e),
                )
                # Create a failed parse result
                parsed_events.append(ParsedEvent(
                    source_id=event.event_id,
                    name=event.name,
                    is_valid=False,
                    parse_errors=[str(e)],
                ))
        
        valid_count = sum(1 for e in parsed_events if e.is_valid)
        logger.info(
            "events_batch_parsed",
            total=len(events),
            valid=valid_count,
            invalid=len(events) - valid_count,
        )
        
        return parsed_events
    
    def _extract_music_links(self, text: str) -> List[str]:
        """Extract music platform links from text.
        
        Args:
            text: Text to search (usually event description)
            
        Returns:
            List of unique music URLs
        """
        links = set()
        
        # YouTube
        links.update(self.YOUTUBE_PATTERN.findall(text))
        
        # Bandcamp
        links.update(self.BANDCAMP_PATTERN.findall(text))
        
        # SoundCloud
        links.update(self.SOUNDCLOUD_PATTERN.findall(text))
        
        # Spotify
        links.update(self.SPOTIFY_PATTERN.findall(text))
        
        return list(links)
    
    def _extract_artists(self, name: str, description: str) -> List[str]:
        """Extract artist names from event name and description.
        
        This is a simple heuristic-based extraction. In production,
        could use NLP or artist database matching.
        
        Args:
            name: Event name
            description: Event description
            
        Returns:
            List of potential artist names
        """
        artists = []
        
        # Common patterns in event names
        # "Artist Name Live at Venue"
        live_match = re.match(r'^(.+?)\s+(?:Live|LIVE)\s+(?:at|@)', name)
        if live_match:
            artists.append(live_match.group(1).strip())
        
        # "Artist Name @ Venue"
        at_match = re.match(r'^(.+?)\s+[@]\s+', name)
        if at_match and not artists:
            artists.append(at_match.group(1).strip())
        
        # "Artist Name - Venue"
        dash_match = re.match(r'^(.+?)\s+-\s+', name)
        if dash_match and not artists:
            artists.append(dash_match.group(1).strip())
        
        # Look for "featuring" patterns
        feat_pattern = re.compile(
            r'(?:feat\.|featuring|ft\.)\s+([^,\n]+)',
            re.IGNORECASE
        )
        for match in feat_pattern.finditer(description):
            artists.append(match.group(1).strip())
        
        # Remove duplicates while preserving order
        seen = set()
        unique_artists = []
        for artist in artists:
            if artist.lower() not in seen:
                seen.add(artist.lower())
                unique_artists.append(artist)
        
        return unique_artists
    
    def _extract_price(self, text: str) -> Optional[str]:
        """Extract ticket price from description.
        
        Args:
            text: Text to search
            
        Returns:
            Price string if found
        """
        match = self.PRICE_PATTERN.search(text)
        if match:
            return f"{match.group(1)}€"
        return None
    
    def _validate(self, parsed: ParsedEvent) -> None:
        """Validate parsed event data.
        
        Args:
            parsed: Event to validate (modified in place)
        """
        errors = []
        
        if not parsed.name:
            errors.append("Missing event name")
        
        if not parsed.start_time:
            errors.append("Missing start time")
        
        if parsed.start_time:
            # Make comparison timezone-aware
            now = datetime.now(timezone.utc)
            event_time = parsed.start_time
            # Convert to UTC if timezone-aware, or assume UTC if naive
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
            if event_time < now:
                errors.append("Event is in the past")
        
        if errors:
            parsed.is_valid = False
            parsed.parse_errors.extend(errors)

