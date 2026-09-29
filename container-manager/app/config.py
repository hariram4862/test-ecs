import os

MIN_WARM_AGENTS = int(os.getenv("MIN_WARM_AGENTS", "1"))
MAX_AGENTS = int(os.getenv("MAX_AGENTS", "10"))
MAX_CONCURRENCY = int(os.getenv("MAX_CONCURRENCY_PER_AGENT", "5"))
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "100"))
MOCK_ECS = os.getenv("MOCK_ECS", "true").lower() == "true"
