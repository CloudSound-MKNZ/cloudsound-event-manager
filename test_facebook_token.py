#!/usr/bin/env python3
"""Quick test script to verify Facebook token."""
import asyncio
import aiohttp
import sys
import os

# Get token from environment or secrets file
token = os.getenv("FACEBOOK_ACCESS_TOKEN")
if not token:
    # Try to read from secrets file
    try:
        import yaml
        with open("../CloudSound/infrastructure/helm/cloudsound/values-secrets.yaml") as f:
            secrets = yaml.safe_load(f)
            token = secrets.get("secrets", {}).get("facebookAccessToken")
    except:
        pass

if not token:
    print("ERROR: No Facebook token found. Set FACEBOOK_ACCESS_TOKEN env var.")
    sys.exit(1)

page_id = os.getenv("FACEBOOK_PAGE_IDS", "137101232817215").split(",")[0]

async def test_token():
    """Test Facebook token validity."""
    base_url = "https://graph.facebook.com/v24.0"
    
    async with aiohttp.ClientSession() as session:
        # Test 1: Verify token by getting page info
        print("Test 1: Verifying token...")
        async with session.get(
            f"{base_url}/me",
            params={"access_token": token, "fields": "id,name"}
        ) as resp:
            data = await resp.json()
            if "error" in data:
                print(f"❌ Token verification failed: {data['error']['message']}")
                return False
            print(f"✅ Token valid! Page: {data.get('name')} (ID: {data.get('id')})")
        
        # Test 2: Try to fetch events
        print(f"\nTest 2: Fetching events from page {page_id}...")
        async with session.get(
            f"{base_url}/{page_id}/events",
            params={
                "access_token": token,
                "fields": "id,name,start_time,place",
                "limit": 5
            }
        ) as resp:
            data = await resp.json()
            if "error" in data:
                print(f"❌ Failed to fetch events: {data['error']['message']}")
                print(f"   Error code: {data['error'].get('code')}")
                print(f"   Error type: {data['error'].get('type')}")
                return False
            
            events = data.get("data", [])
            print(f"✅ Successfully fetched {len(events)} events!")
            for i, event in enumerate(events[:3], 1):
                print(f"   {i}. {event.get('name', 'Unnamed')} - {event.get('start_time', 'No date')}")
            
            if len(events) == 0:
                print("   (No upcoming events found)")
        
        return True

if __name__ == "__main__":
    print("Facebook Token Test\n" + "="*50)
    print(f"Token: {token[:20]}...{token[-10:]}")
    print(f"Page ID: {page_id}\n")
    
    try:
        result = asyncio.run(test_token())
        if result:
            print("\n✅ All tests passed! Token is valid and can fetch events.")
            sys.exit(0)
        else:
            print("\n❌ Tests failed. Check token validity.")
            sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
