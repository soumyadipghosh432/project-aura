import os
import json
from dotenv import load_dotenv

# Load .env file
load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
OFFLINE_MODEL_HOME = os.getenv("OFFLINE_MODEL_HOME")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL must be set in the .env file")

if not OFFLINE_MODEL_HOME:
    raise ValueError("OFFLINE_MODEL_HOME must be set in the .env file")

# Normalize paths
OFFLINE_MODEL_HOME = os.path.abspath(OFFLINE_MODEL_HOME)

# Load config.json
CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
if not os.path.exists(CONFIG_PATH):
    raise FileNotFoundError(f"config.json not found at {CONFIG_PATH}")

with open(CONFIG_PATH, "r") as f:
    config_data = json.load(f)

models_list = config_data.get("models", [])
BATCH_LIMIT = config_data.get("batch_limit", 1500)

# Validate models list
active_count = 0
fallback_model = None
active_model = None

for m in models_list:
    name = m.get("name")
    active = m.get("active", False)
    fallback = m.get("fallback", False)
    
    if active:
        active_count += 1
        active_model = m
    if fallback:
        fallback_model = m

if active_count == 0:
    raise ValueError("Configuration Error: No active model flagged in config.json")
elif active_count > 1:
    raise ValueError("Configuration Error: Multiple active models flagged in config.json")

if not fallback_model:
    raise ValueError("Configuration Error: No fallback model flagged in config.json")

# Verify model files exist in the model directory
for m in models_list:
    model_path = os.path.join(OFFLINE_MODEL_HOME, m["name"])
    if not os.path.exists(model_path):
        raise FileNotFoundError(f"Model file {m['name']} not found at {model_path}")

print("Configuration loaded and validated successfully.")
