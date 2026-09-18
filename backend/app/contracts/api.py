"""Published API contract: all S1–S4 routes are implemented."""

from app.core.config import Settings
from app.main import create_app

contract_app = create_app(Settings(_env_file=None))
