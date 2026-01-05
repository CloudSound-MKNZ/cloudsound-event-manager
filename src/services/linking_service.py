"""Event linking service.

Links Facebook events to concerts in the CloudSound system,
creating or updating concert records as needed.
"""
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass
from datetime import datetime, timedelta
import aiohttp
import structlog

from cloudsound_shared.config.settings import app_settings
from .event_parser import ParsedEvent
from .enrichment_service import EnrichedEvent

logger = structlog.get_logger(__name__)


@dataclass
class LinkResult:
    """Result of linking an event to a concert."""
    event_id: str
    concert_id: Optional[str] = None
    action: str = "none"  # created, updated, linked, skipped
    reason: Optional[str] = None
    success: bool = True


class LinkingService:
    """Service for linking Facebook events to CloudSound concerts.
    
    Handles:
    - Creating new concerts from Facebook events
    - Updating existing concerts with Facebook event data
    - Linking events to existing concerts by date/venue
    
    Usage:
        linker = LinkingService()
        
        result = await linker.link_event(enriched_event)
        if result.concert_id:
            print(f"Linked to concert: {result.concert_id}")
    """
    
    def __init__(
        self,
        concert_service_url: Optional[str] = None,
    ):
        """Initialize linking service.
        
        Args:
            concert_service_url: URL for concert management service
        """
        self.concert_service_url = (
            concert_service_url or 
            app_settings.concert_management_url
        )
        
        self._session: Optional[aiohttp.ClientSession] = None
        
        logger.info(
            "linking_service_initialized",
            concert_service_url=self.concert_service_url,
        )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session
    
    async def close(self) -> None:
        """Close HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
    
    async def link_event(self, enriched: EnrichedEvent) -> LinkResult:
        """Link a Facebook event to a CloudSound concert.
        
        Args:
            enriched: Enriched event data
            
        Returns:
            LinkResult with action taken
        """
        parsed = enriched.parsed_event
        
        # Skip invalid events
        if not parsed.is_valid:
            return LinkResult(
                event_id=parsed.source_id,
                action="skipped",
                reason="Invalid event data",
            )
        
        try:
            # Try to find existing concert by date and venue
            existing = await self._find_matching_concert(parsed)
            
            if existing:
                # Update existing concert with Facebook data
                updated = await self._update_concert(existing["id"], parsed)
                return LinkResult(
                    event_id=parsed.source_id,
                    concert_id=existing["id"],
                    action="updated" if updated else "linked",
                )
            else:
                # Create new concert
                concert_id = await self._create_concert(enriched)
                if concert_id:
                    return LinkResult(
                        event_id=parsed.source_id,
                        concert_id=concert_id,
                        action="created",
                    )
                else:
                    return LinkResult(
                        event_id=parsed.source_id,
                        action="skipped",
                        reason="Could not create concert",
                        success=False,
                    )
                    
        except Exception as e:
            logger.error(
                "event_linking_failed",
                event_id=parsed.source_id,
                error=str(e),
            )
            return LinkResult(
                event_id=parsed.source_id,
                action="skipped",
                reason=str(e),
                success=False,
            )
    
    async def link_batch(
        self,
        events: List[EnrichedEvent],
    ) -> List[LinkResult]:
        """Link multiple events.
        
        Args:
            events: List of enriched events
            
        Returns:
            List of link results
        """
        results = []
        
        for event in events:
            result = await self.link_event(event)
            results.append(result)
        
        # Log summary
        created = sum(1 for r in results if r.action == "created")
        updated = sum(1 for r in results if r.action == "updated")
        linked = sum(1 for r in results if r.action == "linked")
        skipped = sum(1 for r in results if r.action == "skipped")
        
        logger.info(
            "events_batch_linked",
            total=len(events),
            created=created,
            updated=updated,
            linked=linked,
            skipped=skipped,
        )
        
        return results
    
    async def _find_matching_concert(
        self,
        parsed: ParsedEvent,
    ) -> Optional[Dict[str, Any]]:
        """Find existing concert matching the event.
        
        Matches by date (±1 day) and venue name.
        
        Args:
            parsed: Parsed event data
            
        Returns:
            Concert dict if found, None otherwise
        """
        if not parsed.start_time:
            return None
        
        try:
            session = await self._get_session()
            
            # Query concerts around the event date
            date_str = parsed.start_time.strftime("%Y-%m-%d")
            url = f"{self.concert_service_url}/api/v1/concerts"
            
            params = {
                "date_from": (parsed.start_time - timedelta(days=1)).isoformat(),
                "date_to": (parsed.start_time + timedelta(days=1)).isoformat(),
            }
            
            async with session.get(url, params=params) as response:
                if response.status != 200:
                    return None
                
                concerts = await response.json()
                
                # Find matching by venue
                for concert in concerts:
                    if self._venues_match(
                        concert.get("venue", ""),
                        parsed.venue_name or "",
                    ):
                        return concert
                
                return None
                
        except Exception as e:
            logger.warning(
                "concert_search_failed",
                error=str(e),
            )
            return None
    
    async def _create_concert(
        self,
        enriched: EnrichedEvent,
    ) -> Optional[str]:
        """Create a new concert from Facebook event.
        
        Args:
            enriched: Enriched event data
            
        Returns:
            Concert ID if created, None otherwise
        """
        parsed = enriched.parsed_event
        
        try:
            session = await self._get_session()
            
            # Build concert data
            concert_data = {
                "name": parsed.name,
                "description": parsed.description,
                "date": parsed.start_time.isoformat() if parsed.start_time else None,
                "venue": parsed.venue_name,
                "city": parsed.city,
                "artists": parsed.artists,
                "source": "facebook",
                "source_id": parsed.source_id,
                "source_url": parsed.source_url,
            }
            
            url = f"{self.concert_service_url}/api/v1/concerts"
            
            async with session.post(url, json=concert_data) as response:
                if response.status in [200, 201]:
                    data = await response.json()
                    return data.get("id")
                else:
                    logger.warning(
                        "concert_create_failed",
                        status=response.status,
                    )
                    return None
                    
        except Exception as e:
            logger.error(
                "concert_create_error",
                error=str(e),
            )
            return None
    
    async def _update_concert(
        self,
        concert_id: str,
        parsed: ParsedEvent,
    ) -> bool:
        """Update existing concert with Facebook data.
        
        Args:
            concert_id: Concert to update
            parsed: Parsed event with new data
            
        Returns:
            True if updated successfully
        """
        try:
            session = await self._get_session()
            
            # Only update description if we have music links
            if not parsed.music_links:
                return False
            
            update_data = {
                "description": parsed.description,
                "source_url": parsed.source_url,
            }
            
            url = f"{self.concert_service_url}/api/v1/concerts/{concert_id}"
            
            async with session.patch(url, json=update_data) as response:
                return response.status in [200, 204]
                
        except Exception as e:
            logger.warning(
                "concert_update_failed",
                concert_id=concert_id,
                error=str(e),
            )
            return False
    
    @staticmethod
    def _venues_match(venue1: str, venue2: str) -> bool:
        """Check if two venue names match (fuzzy).
        
        Args:
            venue1: First venue name
            venue2: Second venue name
            
        Returns:
            True if venues likely match
        """
        if not venue1 or not venue2:
            return False
        
        # Normalize and compare
        v1 = venue1.lower().strip()
        v2 = venue2.lower().strip()
        
        # Exact match
        if v1 == v2:
            return True
        
        # One contains the other
        if v1 in v2 or v2 in v1:
            return True
        
        return False

