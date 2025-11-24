import os

# Basic bot credentials (can come from environment or be filled here)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "YOUR_API_HASH_HERE")

# Required groups (join-gate) — user must join both
REQUIRED_GROUP_1 = os.environ.get("REQUIRED_GROUP_1", "@YourVerifyGroup1")
REQUIRED_GROUP_2 = os.environ.get("REQUIRED_GROUP_2", "@YourVerifyGroup2")

# Owner info
OWNER_ID = int(os.environ.get("OWNER_ID", 0))
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "YourOwnerUsername")

# Files
DB_NAME = os.environ.get("DB_NAME", "bot.db")
SESSIONS_DIR = os.environ.get("SESSIONS_DIR", "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)
