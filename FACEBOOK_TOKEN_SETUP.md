# Facebook Long-Lived Token Setup

## Current Status

✅ **Token Found**: A Facebook Page Access Token is configured in `infrastructure/helm/cloudsound/values-secrets.yaml`

✅ **Real API Enabled**: The Helm templates have been updated to use the real Facebook API when a token is provided.

## Token Verification

The token in your secrets file appears to be a **Page Access Token** (starts with `EAAD...`). 

**Important**: Page Access Tokens can be:
- **Long-lived (never-expiring)**: If generated from a long-lived user token
- **Short-lived**: If generated from a short-lived user token (expires in 1-2 hours)

## How to Verify Your Token is Long-Lived

### Option 1: Use the API Endpoint (Recommended)

After deploying, call the verification endpoint:

```bash
curl http://your-event-manager-url/api/v1/events/verify-token
```

This will return:
- Token validity status
- Expiration date (if any)
- Whether it's long-lived
- Scopes/permissions

### Option 2: Use Facebook Graph API Explorer

1. Go to https://developers.facebook.com/tools/explorer/
2. Select your app
3. Click "Get Token" → "Get User Access Token"
4. Select permissions: `pages_read_engagement`, `pages_read_user_content`, `pages_show_list`
5. Use the token to call: `GET /debug_token?input_token={your-token}`

Look for `expires_at`:
- `expires_at: 0` or `null` = Never-expiring (long-lived) ✅
- `expires_at: <timestamp>` = Will expire ❌

## How to Get a Long-Lived Page Access Token

If your current token is not long-lived, follow these steps:

### Step 1: Get a Long-Lived User Access Token

1. Go to https://developers.facebook.com/tools/explorer/
2. Select your Facebook App
3. Click "Get Token" → "Get User Access Token"
4. Select these permissions:
   - `pages_read_engagement`
   - `pages_read_user_content`
   - `pages_show_list`
   - `pages_manage_metadata` (if you need to create events)
5. Copy the short-lived token (expires in 1-2 hours)

6. Exchange it for a long-lived token:
```bash
curl -X GET "https://graph.facebook.com/v24.0/oauth/access_token?grant_type=fb_exchange_token&client_id={YOUR_APP_ID}&client_secret={YOUR_APP_SECRET}&fb_exchange_token={SHORT_LIVED_TOKEN}"
```

This returns a long-lived user token (valid for ~60 days).

### Step 2: Get Page Access Token from Long-Lived User Token

```bash
curl -X GET "https://graph.facebook.com/v24.0/me/accounts?access_token={LONG_LIVED_USER_TOKEN}"
```

This returns a list of pages you manage. Find your page and copy the `access_token` field.

**Important**: Page Access Tokens generated from long-lived user tokens are **never-expiring** ✅

### Step 3: Update Your Configuration

Update `infrastructure/helm/cloudsound/values-secrets.yaml`:

```yaml
secrets:
  facebookAccessToken: "YOUR_NEW_LONG_LIVED_PAGE_TOKEN"
  facebookPageIds: "137101232817215"  # Your page ID
```

## Configuration Changes Made

1. ✅ **Helm Template Updated**: `infrastructure/helm/cloudsound/templates/services.yaml`
   - Added `FACEBOOK_ACCESS_TOKEN` environment variable from secrets
   - Added `FACEBOOK_PAGE_IDS` environment variable
   - Set `USE_MOCK_APIS=false` when token is provided

2. ✅ **Token Verification Added**: `src/clients/facebook_client.py`
   - Added `verify_token()` method to check token expiration
   - Added `/api/v1/events/verify-token` endpoint

3. ✅ **Real API Implementation**: Already complete!
   - The Facebook client already has full Graph API implementation
   - Just needed to disable mock mode

## Testing

After deploying with the token:

1. **Verify Token**:
   ```bash
   curl http://localhost:8002/api/v1/events/verify-token
   ```

2. **Test Event Fetching**:
   ```bash
   curl -X POST http://localhost:8002/api/v1/events/poll
   ```

3. **Check Status**:
   ```bash
   curl http://localhost:8002/api/v1/events/status
   ```
   Should show `"mock_mode": false`

## Troubleshooting

### Token Expired
If you get authentication errors:
- Token may have expired
- Get a new long-lived Page Access Token (see steps above)

### Rate Limits
Facebook has rate limits:
- 200 calls per hour per user
- Use the circuit breaker (already implemented) to handle rate limits gracefully

### Permissions
Ensure your token has these permissions:
- `pages_read_engagement` - Read page events
- `pages_read_user_content` - Read page content
- `pages_show_list` - List pages you manage

## References

- [Facebook Long-Lived Tokens](https://developers.facebook.com/docs/facebook-login/guides/access-tokens/get-long-lived/)
- [Page Access Tokens](https://developers.facebook.com/docs/pages/access-tokens/)
- [Graph API Events](https://developers.facebook.com/docs/graph-api/reference/page/events/)
