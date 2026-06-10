import os
import json
import random
import portalocker
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple

class SafeCSVHandler:
    def __init__(self, filepath: str):
        self.filepath = filepath
        # Ensure file exists
        if not os.path.exists(filepath):
            df = pd.DataFrame(columns=["Name", "Email", "Status", "MessageId", "LastSentAt"])
            df.to_csv(filepath, index=False)
        self._init_columns()

    def _init_columns(self):
        """Ensures that required tracking columns exist in the CSV."""
        with portalocker.Lock(self.filepath, 'r+', timeout=10) as f:
            try:
                df = pd.read_csv(f)
            except pd.errors.EmptyDataError:
                df = pd.DataFrame(columns=["Name", "Email", "Status", "MessageId", "LastSentAt"])
            
            modified = False
            for col in ["Status", "MessageId", "LastSentAt"]:
                if col not in df.columns:
                    df[col] = pd.NA
                    modified = True
            
            if modified:
                f.seek(0)
                f.truncate()
                df.to_csv(f, index=False)
 
    def get_leads(self) -> pd.DataFrame:
        """Reads the leads CSV under a shared lock."""
        with portalocker.Lock(self.filepath, 'r', timeout=10) as f:
            return pd.read_csv(f)
 
    def update_lead(self, email: str, status: str, message_id: Optional[str] = None, 
                    last_sent_at: Optional[str] = None):
        """Updates a specific lead by email under an exclusive lock."""
        with portalocker.Lock(self.filepath, 'r+', timeout=15) as f:
            df = pd.read_csv(f)
            
            # Ensure tracking columns are of object/string dtype to prevent pandas warnings/errors
            for col in ["Status", "MessageId", "LastSentAt"]:
                if col in df.columns:
                    df[col] = df[col].astype(object)
            
            # Normalize email comparisons
            email_clean = email.strip().lower()
            df_emails = df['Email'].astype(str).str.strip().str.lower()
            
            idx = df_emails == email_clean
            if not idx.any():
                print(f"Warning: Lead with email '{email}' not found in CSV.")
                return
            
            df.loc[idx, 'Status'] = status
            if message_id is not None:
                df.loc[idx, 'MessageId'] = message_id
            if last_sent_at is not None:
                df.loc[idx, 'LastSentAt'] = last_sent_at
                
            f.seek(0)
            f.truncate()
            df.to_csv(f, index=False)

    def get_eligible_leads(self, followup_delay_days: int = 3) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Returns two lists:
        1. Due follow-ups (Status == 'SENT', MessageId not empty, LastSentAt <= now - delay_days)
        2. Fresh leads (Status is empty/NaN)
        """
        df = self.get_leads()
        
        fresh_leads = []
        followup_leads = []
        
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=followup_delay_days)
        
        def is_empty(val):
            return pd.isna(val) or str(val).strip() == '' or str(val).strip().lower() == 'nan'
        
        for _, row in df.iterrows():
            email = row.get('Email')
            if is_empty(email):
                continue
                
            status_val = row.get('Status')
            
            if is_empty(status_val):
                fresh_leads.append(row.to_dict())
            elif str(status_val).strip() == 'SENT':
                msg_id = row.get('MessageId')
                last_sent_str = row.get('LastSentAt')
                
                if not is_empty(msg_id) and not is_empty(last_sent_str):
                    try:
                        last_sent_str = str(last_sent_str).strip()
                        # Parse LastSentAt timestamp (expects ISO format)
                        if last_sent_str.endswith("Z"):
                            last_sent_str = last_sent_str[:-1] + "+00:00"
                        last_sent = datetime.fromisoformat(last_sent_str).astimezone(timezone.utc)
                        
                        if last_sent <= cutoff:
                            followup_leads.append(row.to_dict())
                    except Exception as e:
                        print(f"Error parsing timestamp for {email}: {e}")
                        
        return followup_leads, fresh_leads


class StateHandler:
    def __init__(self, filepath: str):
        self.filepath = filepath

    def load_state(self) -> Dict[str, Any]:
        """Loads state from the JSON file. If it doesn't exist, returns empty dict."""
        if not os.path.exists(self.filepath):
            return {}
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading state.json: {e}")
            return {}

    def save_state(self, state: Dict[str, Any]):
        """Saves state to the JSON file."""
        try:
            with open(self.filepath, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=4)
        except Exception as e:
            print(f"Error saving state.json: {e}")

    def check_day_off_and_hour(self) -> Tuple[bool, str]:
        """
        Determines if today is a scheduled day off or if we already sent an email in the current hour.
        Returns (is_blocked, reason_message)
        """
        now = datetime.now(timezone.utc)
        year, week, weekday = now.isocalendar()  # weekday is 1 (Monday) to 7 (Sunday)
        weekday_idx = weekday - 1  # Convert to 0 (Monday) to 6 (Sunday)
        
        current_hour = now.hour
        current_date_str = now.date().isoformat()
        
        state = self.load_state()
        
        # Check if week has changed, or if state is empty/invalid
        if (state.get("year") != year or 
            state.get("week") != week or 
            state.get("day_off") is None):
            
            # Choose a random day of the week to be the day off (0 = Monday, 6 = Sunday)
            day_off = random.randint(0, 6)
            days_map = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            print(f"New week detected ({year}, Week {week}). Selected random day off: {days_map[day_off]}")
            
            state["year"] = year
            state["week"] = week
            state["day_off"] = day_off
            # Reset daily/hourly tracking for the new week run
            state["last_sent_date"] = None
            state["last_sent_hour"] = None
            self.save_state(state)
            
        day_off_idx = state["day_off"]
        days_map = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        
        # 1. Check if today is the day off
        if weekday_idx == day_off_idx:
            return True, f"Today ({days_map[weekday_idx]}) is the scheduled day off for this week."
            
        # 2. Check if we already sent an email in this calendar hour
        if state.get("last_sent_date") == current_date_str and state.get("last_sent_hour") == current_hour:
            return True, f"An email was already sent in the current hour ({current_hour}:00 UTC)."
            
        return False, ""

    def record_send(self):
        """Records a successful send to prevent double sending in the same hour."""
        now = datetime.now(timezone.utc)
        current_hour = now.hour
        current_date_str = now.date().isoformat()
        
        state = self.load_state()
        state["last_sent_date"] = current_date_str
        state["last_sent_hour"] = current_hour
        self.save_state(state)
