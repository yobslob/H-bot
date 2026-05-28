import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file in project root
load_dotenv()

class Config:
    SMTP_HOST = os.getenv("SMTP_HOST", "smtp.hostinger.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER = os.getenv("SMTP_USER")
    SMTP_PASS = os.getenv("SMTP_PASS")
    FROM_NAME = os.getenv("FROM_NAME", "")

    # Scheduler config: delay bounds in minutes
    MIN_DELAY_MINUTES = int(os.getenv("MIN_DELAY_MINUTES", "0"))
    MAX_DELAY_MINUTES = int(os.getenv("MAX_DELAY_MINUTES", "50"))

    # Resolve paths relative to this file's directory
    BASE_DIR = Path(__file__).resolve().parent
    
    # Path configuration
    LEADS_CSV_PATH = BASE_DIR / os.getenv("LEADS_CSV_PATH", "leads.csv")
    STATE_JSON_PATH = BASE_DIR / os.getenv("STATE_JSON_PATH", "state.json")
    EMAIL_TEMPLATES_DIR = BASE_DIR / os.getenv("EMAIL_TEMPLATES_DIR", "email-templates")

    @classmethod
    def validate(cls):
        """Validates that all required environment variables are set."""
        missing = []
        if not cls.SMTP_USER:
            missing.append("SMTP_USER")
        if not cls.SMTP_PASS:
            missing.append("SMTP_PASS")
        if missing:
            raise ValueError(
                f"Missing required configurations: {', '.join(missing)}. "
                "Please configure them in your .env file."
            )
