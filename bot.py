import os
import sys
import time
import random
import argparse
import logging
from datetime import datetime, timezone
import pandas as pd

from config import Config
from utils.csv_handler import SafeCSVHandler, StateHandler
from utils.smtp_mailer import SMTPMailer, PermanentEmailError, TransientEmailError
from utils.dns_verifier import DNSVerifier

# Setup Logging
log_format = "%(asctime)s [%(levelname)s] %(message)s"
logging.basicConfig(
    level=logging.INFO,
    format=log_format,
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Config.BASE_DIR / "bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("hbot")

# Map of fresh template file names to their subject lines (supporting placeholders)
FRESH_SUBJECT_TEMPLATES = {
    "template_1.html": "{Name}, collaboration request",
    "template_2.html": "Quick question for {Name}",
    "template_3.html": "Partnership proposal - {Name}"
}

def get_now_utc_iso() -> str:
    """Returns the current ISO-8601 timestamp in UTC (Zulu) format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def format_text_placeholders(text: str, lead: dict) -> str:
    """Replaces placeholders like {Name} in templates and subjects with values from the lead."""
    for key, val in lead.items():
        placeholder = f"{{{key}}}"
        if placeholder in text:
            # Handle NaN or None values
            val_str = ""
            if val is not None and not pd.isna(val):
                val_str = str(val).strip()
            text = text.replace(placeholder, val_str)
    return text

def cmd_verify_dns(args):
    """Command to run DNS deliverability checks."""
    domain = Config.SMTP_USER.split('@')[-1]
    logger.info(f"Running DNS verification for domain: {domain}")
    
    verifier = DNSVerifier(domain)
    diagnostics = verifier.run_diagnostics()
    
    all_ok = True
    critical_error = False
    print("\n=== DNS DELIVERABILITY REPORT ===")
    for record_type, (status, issues) in diagnostics.items():
        if status:
            print(f"[OK]  {record_type}")
        else:
            # We want to differentiate warnings from errors
            has_critical = False
            for issue in issues:
                if "warning" in issue.lower():
                    print(f"[WARN] {record_type}: {issue}")
                else:
                    has_critical = True
                    all_ok = False
                    critical_error = True
                    print(f"[ERR]  {record_type}: {issue}")
            if not has_critical and not status:
                # If there are only warnings, print OK or WARN status
                print(f"[WARN] {record_type}")
                
    print("=================================")
    if critical_error:
        logger.error("Critical DNS issues detected! Please fix the errors listed above to prevent deliverability failures.")
        sys.exit(1)
    elif not all_ok:
        logger.warning("DNS verified with warnings (see details above).")
        sys.exit(0)
    else:
        logger.info("All DNS checks passed! Your email domain is ready for cold outreach.")
        sys.exit(0)

def cmd_send_one(args):
    """Command to execute a single scheduled email send step."""
    logger.info("Starting send-one execution step.")
    
    # 1. Validate environment configuration
    try:
        Config.validate()
    except ValueError as e:
        logger.critical(f"Configuration error: {e}")
        sys.exit(2)

    state_handler = StateHandler(str(Config.STATE_JSON_PATH))
    
    # 2. Check for day off or duplicate hourly runs
    if not args.force and not args.dry_run:
        is_blocked, reason = state_handler.check_day_off_and_hour()
        if is_blocked:
            logger.info(f"Execution skipped: {reason}")
            sys.exit(0)
            
    # 3. Handle randomized sleep delay to mimic natural human send patterns
    if not args.force and not args.dry_run:
        min_sec = Config.MIN_DELAY_MINUTES * 60
        max_sec = Config.MAX_DELAY_MINUTES * 60
        if max_sec > min_sec:
            delay_sec = random.randint(min_sec, max_sec)
            logger.info(f"Random sleep scheduling active: sleeping for {delay_sec // 60} minutes and {delay_sec % 60} seconds...")
            time.sleep(delay_sec)
            
    # 4. Load CSV and extract eligible leads
    csv_handler = SafeCSVHandler(str(Config.LEADS_CSV_PATH))
    followups, fresh = csv_handler.get_eligible_leads(followup_delay_days=3)
    
    logger.info(f"Status update: {len(fresh)} fresh leads, {len(followups)} due follow-ups.")
    
    selected_lead = None
    kind = None
    
    # Prefer follow-ups first
    if followups:
        selected_lead = random.choice(followups)
        kind = 'followup'
        logger.info(f"Selected follow-up lead: {selected_lead['Email']}")
    elif fresh:
        selected_lead = random.choice(fresh)
        kind = 'fresh'
        logger.info(f"Selected fresh lead: {selected_lead['Email']}")
    else:
        logger.info("No eligible leads to send to. All leads are finished, currently pending, or discarded.")
        sys.exit(0)
        
    # 5. Locate and select email template
    template_folder = Config.EMAIL_TEMPLATES_DIR / kind
    if not template_folder.exists() or not template_folder.is_dir():
        logger.critical(f"Template folder not found: {template_folder}")
        sys.exit(3)
        
    templates = [t for t in os.listdir(template_folder) if t.endswith('.html') or t.endswith('.txt')]
    if not templates:
        logger.critical(f"No email templates found in {template_folder}")
        sys.exit(3)
        
    chosen_template_name = random.choice(templates)
    template_path = template_folder / chosen_template_name
    logger.info(f"Selected template: {chosen_template_name}")
    
    # 6. Read and construct email contents
    try:
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()
    except Exception as e:
        logger.critical(f"Failed to read template file: {e}")
        sys.exit(3)
        
    html_body = format_text_placeholders(template_content, selected_lead)
    
    # Determine subject line
    base_subject = format_text_placeholders("{Name}, This is a collaboration request.", selected_lead)
    if kind == 'followup':
        subject = f"Re: {base_subject}"
    else:
        subject = base_subject
            
    # 7. Execution or Dry-run display
    if args.dry_run:
        print("\n=== DRY RUN MODE: EMAIL DETAIL ===")
        print(f"To:      {selected_lead['Email']}")
        print(f"Subject: {subject}")
        print(f"Kind:    {kind.upper()} ({chosen_template_name})")
        if kind == 'followup':
            print(f"ReplyTo: {selected_lead.get('MessageId')}")
        print("--- HTML CONTENT START ---")
        print(html_body)
        print("--- HTML CONTENT END ---")
        print("==================================")
        logger.info("Dry run finished successfully. No files or emails modified.")
        sys.exit(0)
        
    # 8. Send Email using SMTPMailer
    mailer = SMTPMailer(
        host=Config.SMTP_HOST,
        port=Config.SMTP_PORT,
        user=Config.SMTP_USER,
        passwd=Config.SMTP_PASS,
        from_name=Config.FROM_NAME
    )
    
    in_reply_to_mid = selected_lead.get('MessageId') if kind == 'followup' else None
    
    try:
        msg_id, status_val = mailer.send(
            to_email=selected_lead['Email'],
            subject=subject,
            html_body=html_body,
            in_reply_to=in_reply_to_mid
        )
        
        # 9. Update state on success
        new_status = 'SENT' if kind == 'fresh' else 'FOLLOWUP_SENT'
        csv_handler.update_lead(
            email=selected_lead['Email'],
            status=new_status,
            message_id=msg_id,
            last_sent_at=get_now_utc_iso()
        )
        state_handler.record_send()
        
        logger.info(f"SUCCESS: Email sent to {selected_lead['Email']} (Status: {new_status}, MsgID: {msg_id})")
        
    except PermanentEmailError as e:
        # Permanent failure (e.g. Mailbox Full, Invalid address) -> set status as DISCARDED
        csv_handler.update_lead(
            email=selected_lead['Email'],
            status='DISCARDED',
            message_id=None,
            last_sent_at=get_now_utc_iso()
        )
        logger.error(f"PERMANENT FAILURE: Refused for {selected_lead['Email']}. Reason: {e}. Set lead status to DISCARDED.")
        
    except TransientEmailError as e:
        # Transient failure (connection drop, timeout, temp limit) -> exit with error, do NOT discard lead
        logger.error(f"TRANSIENT FAILURE: SMTP issue while sending to {selected_lead['Email']}: {e}. Retrying next run.")
        sys.exit(4)

def main():
    parser = argparse.ArgumentParser(description="hbot: A premium, human-like cold email scheduling automation tool.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Subcommand: verify-dns
    subparsers.add_parser("verify-dns", help="Verify the SPF, DKIM, MX, and DMARC settings for your outbound domain.")
    
    # Subcommand: send-one
    send_parser = subparsers.add_parser("send-one", help="Run a single email sending check (runs once per hour).")
    send_parser.add_argument("--force", action="store_true", help="Bypass day-off / hourly checks and randomized delay.")
    send_parser.add_argument("--dry-run", action="store_true", help="Process and format the email but do not send it.")
    
    args = parser.parse_args()
    
    if args.command == "verify-dns":
        cmd_verify_dns(args)
    elif args.command == "send-one":
        cmd_send_one(args)

if __name__ == "__main__":
    main()
