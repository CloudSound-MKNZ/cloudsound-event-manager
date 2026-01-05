# Event Manager Service

Fetches events from Facebook Events API and publishes them to Kafka topics.

## Features

- Facebook Events API integration
- Event parsing and enrichment
- Kafka producer for event streaming
- Scheduled polling job
- Circuit breaker for API resilience

## Development

```bash
cd backend/event-manager
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn src.main:app --reload --port 8002
```

