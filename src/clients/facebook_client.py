"""Facebook Events API client with circuit breaker protection.

Fetches events from Facebook Pages using the Graph API.

Configuration:
- FACEBOOK_ACCESS_TOKEN: Page Access Token (never-expiring if from long-lived user token)
- FACEBOOK_PAGE_IDS: Comma-separated list of Facebook Page IDs to monitor

See: https://developers.facebook.com/docs/graph-api/reference/page/events/
"""
import asyncio
import aiohttp
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import random
import structlog

from ..utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen
from ..utils.retry import retry_with_backoff

logger = structlog.get_logger(__name__)


@dataclass
class FacebookEvent:
    """Represents a Facebook event."""
    event_id: str
    name: str
    description: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    place: Optional[Dict[str, Any]] = None
    cover_photo_url: Optional[str] = None
    attending_count: int = 0
    interested_count: int = 0
    is_online: bool = False
    event_url: str = ""
    
    def __post_init__(self):
        if not self.event_url and self.event_id:
            self.event_url = f"https://facebook.com/events/{self.event_id}"


@dataclass
class FacebookEventsResponse:
    """Response from Facebook Events API."""
    events: List[FacebookEvent]
    next_cursor: Optional[str] = None
    has_more: bool = False


class FacebookClientError(Exception):
    """Base exception for Facebook client errors."""
    pass


class FacebookAuthError(FacebookClientError):
    """Raised when authentication fails."""
    pass


class FacebookRateLimitError(FacebookClientError):
    """Raised when rate limit is exceeded."""
    pass


class FacebookEventsClient:
    """Client for Facebook Events API.
    
    Fetches events from configured Facebook Pages using the Graph API.
    Supports both real API calls and mock mode for development.
    
    Usage:
        client = FacebookEventsClient(
            access_token="your-page-access-token",
            page_ids=["137101232817215"],
            use_mock=False,
        )
        
        events = await client.get_events()
    """
    
    def __init__(
        self,
        access_token: Optional[str] = None,
        page_ids: Optional[List[str]] = None,
        use_mock: bool = True,
        api_version: str = "v24.0",
        circuit_breaker_threshold: int = 5,
        circuit_breaker_timeout: float = 60.0,
    ):
        """Initialize Facebook Events client.
        
        Args:
            access_token: Facebook Graph API Page Access Token
            page_ids: List of Facebook Page IDs to monitor for events
            use_mock: Use mock responses (default True for development)
            api_version: Facebook Graph API version (default v24.0)
            circuit_breaker_threshold: Failures before opening circuit
            circuit_breaker_timeout: Seconds before retry after circuit opens
        """
        self.access_token = access_token
        self.page_ids = page_ids or []
        self.use_mock = use_mock or not access_token
        self.base_url = f"https://graph.facebook.com/{api_version}"
        
        self._circuit_breaker = CircuitBreaker(
            name="facebook_api",
            failure_threshold=circuit_breaker_threshold,
            timeout=circuit_breaker_timeout,
        )
        
        # Track last poll time for each page
        self._last_poll: Dict[str, datetime] = {}
        
        # HTTP session (lazy initialized)
        self._session: Optional[aiohttp.ClientSession] = None
        
        logger.info(
            "facebook_client_initialized",
            use_mock=self.use_mock,
            page_count=len(self.page_ids),
            has_token=bool(access_token),
            api_version=api_version,
        )
        
        if self.use_mock:
            logger.warning(
                "facebook_client_mock_mode",
                message="Using mock data. Set use_mock=False and provide access_token for real API.",
            )
    
    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create HTTP session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session
    
    def _generate_mock_events(self, count: int = 5) -> List[FacebookEvent]:
        """Generate mock Facebook events for development."""
        venues = [
            {"name": "The Blue Note", "city": "Ljubljana"},
            {"name": "Metelkova", "city": "Ljubljana"},
            {"name": "Kino Šiška", "city": "Ljubljana"},
            {"name": "Channel Zero", "city": "Ljubljana"},
            {"name": "Cvetličarna", "city": "Ljubljana"},
        ]
        
        band_names = [
            "The Electric Waves", "Midnight Echoes", "Solar Flare",
            "Urban Decay", "Crystal Method", "Neon Dreams",
            "The Underground", "Velvet Thunder", "Digital Sunrise",
        ]
        
        events = []
        base_date = datetime.now() + timedelta(days=random.randint(1, 7))
        
        for i in range(count):
            venue = random.choice(venues)
            band = random.choice(band_names)
            event_date = base_date + timedelta(days=i * random.randint(2, 5))
            
            events.append(FacebookEvent(
                event_id=f"fb_mock_{random.randint(100000, 999999)}",
                name=f"{band} Live at {venue['name']}",
                description=f"""
Join us for an amazing night of live music!

🎸 {band} brings their electrifying performance to {venue['name']}!

📍 {venue['name']}, {venue['city']}
🕐 Doors open at 20:00
🎫 Tickets: 15€ / 20€ at door

Listen to the band: https://youtube.com/watch?v=mock{i}abc
Bandcamp: https://{band.lower().replace(' ', '')}.bandcamp.com/album/live

#livemusic #{venue['city'].lower()} #concert
                """.strip(),
                start_time=event_date.replace(hour=21, minute=0),
                end_time=event_date.replace(hour=23, minute=59),
                place={
                    "name": venue["name"],
                    "location": {
                        "city": venue["city"],
                        "country": "Slovenia",
                    }
                },
                attending_count=random.randint(50, 500),
                interested_count=random.randint(100, 1000),
            ))
        
        return events
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def get_events(
        self,
        page_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        limit: int = 25,
    ) -> FacebookEventsResponse:
        """Get events from Facebook page(s).
        
        Args:
            page_id: Specific page ID (or uses configured page_ids)
            since: Only events starting after this time
            until: Only events starting before this time
            limit: Maximum number of events to return
            
        Returns:
            FacebookEventsResponse with events list
        """
        if self.use_mock:
            logger.debug("facebook_get_events_mock", page_id=page_id, limit=limit)
            await asyncio.sleep(0.1)  # Simulate API latency
            
            events = self._generate_mock_events(min(limit, 5))
            
            # Filter by date if provided
            if since:
                events = [e for e in events if e.start_time and e.start_time >= since]
            if until:
                events = [e for e in events if e.start_time and e.start_time <= until]
            
            return FacebookEventsResponse(
                events=events[:limit],
                has_more=len(events) > limit,
            )
        
        # Real API implementation
        async def _fetch():
            target_page_id = page_id or (self.page_ids[0] if self.page_ids else None)
            if not target_page_id:
                raise FacebookClientError("No page_id provided and no page_ids configured")
            
            # Build request URL with fields we need
            fields = ",".join([
                "id", "name", "description", "start_time", "end_time",
                "place", "cover", "attending_count", "interested_count",
                "is_online", "event_times"
            ])
            
            params = {
                "access_token": self.access_token,
                "fields": fields,
                "limit": str(limit),
            }
            
            # Add time filters if provided
            if since:
                params["since"] = str(int(since.timestamp()))
            if until:
                params["until"] = str(int(until.timestamp()))
            
            url = f"{self.base_url}/{target_page_id}/events"
            
            logger.debug(
                "facebook_api_request",
                page_id=target_page_id,
                url=url,
            )
            
            session = await self._get_session()
            async with session.get(url, params=params) as response:
                data = await response.json()
                
                # Check for errors
                if "error" in data:
                    error = data["error"]
                    error_code = error.get("code", 0)
                    error_msg = error.get("message", "Unknown error")
                    
                    if error_code in (190, 102):  # Token errors
                        raise FacebookAuthError(f"Authentication failed: {error_msg}")
                    elif error_code == 4:  # Rate limit
                        raise FacebookRateLimitError(f"Rate limit exceeded: {error_msg}")
                    else:
                        raise FacebookClientError(f"API error ({error_code}): {error_msg}")
                
                # Parse events from response
                events = []
                for event_data in data.get("data", []):
                    event = self._parse_event(event_data)
                    if event:
                        events.append(event)
                
                # Check for pagination
                paging = data.get("paging", {})
                next_cursor = paging.get("cursors", {}).get("after")
                has_more = "next" in paging
                
                logger.info(
                    "facebook_api_response",
                    page_id=target_page_id,
                    event_count=len(events),
                    has_more=has_more,
                )
                
                return FacebookEventsResponse(
                    events=events,
                    next_cursor=next_cursor,
                    has_more=has_more,
                )
        
        return await self._circuit_breaker.call_async(_fetch)
    
    def _parse_event(self, data: Dict[str, Any]) -> Optional[FacebookEvent]:
        """Parse Facebook API event data into FacebookEvent object."""
        try:
            # Parse start/end times
            start_time = None
            end_time = None
            
            if "start_time" in data:
                try:
                    start_time = datetime.fromisoformat(data["start_time"].replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass
            
            if "end_time" in data:
                try:
                    end_time = datetime.fromisoformat(data["end_time"].replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass
            
            # Parse place
            place = data.get("place")
            
            # Parse cover photo
            cover_photo_url = None
            if "cover" in data:
                cover_photo_url = data["cover"].get("source")
            
            return FacebookEvent(
                event_id=data["id"],
                name=data.get("name", "Untitled Event"),
                description=data.get("description"),
                start_time=start_time,
                end_time=end_time,
                place=place,
                cover_photo_url=cover_photo_url,
                attending_count=data.get("attending_count", 0),
                interested_count=data.get("interested_count", 0),
                is_online=data.get("is_online", False),
            )
        except Exception as e:
            logger.warning(
                "facebook_event_parse_failed",
                event_id=data.get("id"),
                error=str(e),
            )
            return None
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def get_event_details(self, event_id: str) -> Optional[FacebookEvent]:
        """Get detailed information about a specific event.
        
        Args:
            event_id: Facebook event ID
            
        Returns:
            FacebookEvent with full details, or None if not found
        """
        if self.use_mock:
            logger.debug("facebook_get_event_details_mock", event_id=event_id)
            await asyncio.sleep(0.05)
            
            # Return a mock event
            return FacebookEvent(
                event_id=event_id,
                name=f"Mock Event {event_id[-4:]}",
                description="This is a mock event for development.",
                start_time=datetime.now() + timedelta(days=7),
                place={"name": "Mock Venue", "location": {"city": "Ljubljana"}},
                attending_count=100,
                interested_count=250,
            )
        
        # Real API implementation
        async def _fetch():
            fields = ",".join([
                "id", "name", "description", "start_time", "end_time",
                "place", "cover", "attending_count", "interested_count",
                "is_online"
            ])
            
            url = f"{self.base_url}/{event_id}"
            params = {
                "access_token": self.access_token,
                "fields": fields,
            }
            
            session = await self._get_session()
            async with session.get(url, params=params) as response:
                data = await response.json()
                
                if "error" in data:
                    error = data["error"]
                    logger.warning(
                        "facebook_event_details_error",
                        event_id=event_id,
                        error=error.get("message"),
                    )
                    return None
                
                return self._parse_event(data)
        
        return await self._circuit_breaker.call_async(_fetch)
    
    async def poll_all_pages(self) -> List[FacebookEvent]:
        """Poll all configured pages for new events.
        
        Returns:
            List of all events from all configured pages
        """
        all_events = []
        
        for page_id in self.page_ids or ["default"]:
            try:
                # Get events since last poll
                since = self._last_poll.get(page_id)
                response = await self.get_events(page_id=page_id, since=since)
                
                all_events.extend(response.events)
                self._last_poll[page_id] = datetime.now()
                
                logger.info(
                    "facebook_page_polled",
                    page_id=page_id,
                    event_count=len(response.events),
                )
                
            except Exception as e:
                logger.error(
                    "facebook_page_poll_failed",
                    page_id=page_id,
                    error=str(e),
                )
        
        return all_events
    
    def get_circuit_breaker_stats(self) -> Dict[str, Any]:
        """Get circuit breaker statistics."""
        return self._circuit_breaker.get_stats()
    
    async def close(self) -> None:
        """Close the client and cleanup resources."""
        if self._session and not self._session.closed:
            await self._session.close()
        logger.debug("facebook_client_closed")

