#!/usr/bin/env python3
"""Test Facebook integration locally without full service deployment."""
import asyncio
import sys
import os
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

# Set environment variables from secrets file
try:
    import yaml
    secrets_path = Path(__file__).parent.parent / "CloudSound" / "infrastructure" / "helm" / "cloudsound" / "values-secrets.yaml"
    if secrets_path.exists():
        with open(secrets_path) as f:
            secrets = yaml.safe_load(f)
            os.environ["FACEBOOK_ACCESS_TOKEN"] = secrets.get("secrets", {}).get("facebookAccessToken", "")
            os.environ["FACEBOOK_PAGE_IDS"] = secrets.get("secrets", {}).get("facebookPageIds", "")
            os.environ["USE_MOCK_APIS"] = "false"
except Exception as e:
    print(f"Warning: Could not load secrets: {e}")

async def test_facebook_integration():
    """Test Facebook client integration."""
    print("="*60)
    print("Facebook Integration Test")
    print("="*60)
    
    # Import after setting env vars
    from clients.facebook_client import FacebookEventsClient
    
    token = os.getenv("FACEBOOK_ACCESS_TOKEN")
    page_ids = os.getenv("FACEBOOK_PAGE_IDS", "").split(",")
    
    if not token:
        print("❌ ERROR: FACEBOOK_ACCESS_TOKEN not set")
        return False
    
    print(f"\n✓ Token found: {token[:20]}...{token[-10:]}")
    print(f"✓ Page IDs: {page_ids}")
    print()
    
    # Initialize client
    print("1. Initializing Facebook client...")
    client = FacebookEventsClient(
        access_token=token,
        page_ids=[p.strip() for p in page_ids if p.strip()],
        use_mock=False,
    )
    print("   ✓ Client initialized (real API mode)\n")
    
    # Test token verification
    print("2. Verifying token...")
    try:
        token_info = await client.verify_token()
        if token_info.get("valid"):
            print(f"   ✓ Token is valid!")
            print(f"   ✓ Page: {token_info.get('page_name')} (ID: {token_info.get('page_id')})")
            print(f"   ✓ Token type: {token_info.get('token_type')}")
        else:
            print(f"   ❌ Token invalid: {token_info.get('error')}")
            return False
    except Exception as e:
        print(f"   ❌ Verification failed: {e}")
        return False
    print()
    
    # Test fetching events
    print("3. Fetching events from Facebook...")
    try:
        if page_ids and page_ids[0].strip():
            page_id = page_ids[0].strip()
            print(f"   Fetching from page: {page_id}")
            response = await client.get_events(page_id=page_id, limit=5)
            
            print(f"   ✓ Successfully fetched {len(response.events)} events")
            print(f"   ✓ Has more: {response.has_more}")
            print()
            
            if response.events:
                print("   Events found:")
                for i, event in enumerate(response.events[:5], 1):
                    print(f"   {i}. {event.name}")
                    if event.start_time:
                        print(f"      Date: {event.start_time.strftime('%Y-%m-%d %H:%M')}")
                    if event.place:
                        place_name = event.place.get('name', 'Unknown')
                        print(f"      Location: {place_name}")
                    print(f"      URL: {event.event_url}")
                    print()
            else:
                print("   (No events found)")
        else:
            print("   ⚠ No page IDs configured")
    except Exception as e:
        print(f"   ❌ Failed to fetch events: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    # Test poll all pages
    print("4. Testing poll_all_pages()...")
    try:
        all_events = await client.poll_all_pages()
        print(f"   ✓ Polled all pages, found {len(all_events)} total events")
    except Exception as e:
        print(f"   ⚠ Poll failed: {e}")
    
    # Cleanup
    await client.close()
    
    print("\n" + "="*60)
    print("✅ All tests passed! Facebook integration is working!")
    print("="*60)
    return True

if __name__ == "__main__":
    try:
        success = asyncio.run(test_facebook_integration())
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
