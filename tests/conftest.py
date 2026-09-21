"""Keep unit tests isolated from configured external observability services."""

import os


os.environ["LANGSMITH_TRACING"] = "false"
os.environ["REDIS_URL"] = ""
