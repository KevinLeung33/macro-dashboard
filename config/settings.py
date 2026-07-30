import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent

# Load .env file (falls back gracefully if not exists)
env_file = PROJECT_ROOT / ".env"
if env_file.exists():
    load_dotenv(env_file)

DB_PATH = PROJECT_ROOT / "macro_data.db"

# -- API keys: .env > 环境变量 > 空字符串 --
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
EIA_API_KEY = os.getenv("EIA_API_KEY", "")

# -- API endpoints --
FRED_BASE_URL = "https://api.stlouisfed.org/fred"
EIA_BASE_URL = "https://api.eia.gov/v2"
TIC_DATA_URL = (
    "https://ticdata.treasury.gov/resource-center/data-chart-center/"
    "tic/Documents/slt_table5.txt"
)

# -- Scheduler --
FETCH_INTERVAL_DAILY = 24
FETCH_INTERVAL_WEEKLY = 168
