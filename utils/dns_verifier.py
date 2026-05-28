import dns.resolver
import re
from typing import Dict, List, Tuple

class DNSVerifier:
    def __init__(self, domain: str):
        self.domain = domain.strip().lower()

    def verify_spf(self) -> Tuple[bool, List[str]]:
        """Queries and checks the SPF record."""
        issues = []
        try:
            answers = dns.resolver.resolve(self.domain, 'TXT')
            spf_records = []
            for rdata in answers:
                for string in rdata.strings:
                    decoded = string.decode('utf-8')
                    if decoded.startswith('v=spf1'):
                        spf_records.append(decoded)
            
            if not spf_records:
                return False, ["No SPF record (v=spf1) found on the root domain."]
            if len(spf_records) > 1:
                issues.append(f"Multiple SPF records found ({len(spf_records)}). You must merge them into a single record.")
            
            spf = spf_records[0]
            # Check for common spaces/formatting issues
            if "include :" in spf or "include: " in spf or " :_" in spf or "include:_ spf" in spf:
                issues.append(
                    f"CRITICAL: SPF record syntax issue: Space/format error detected in record: '{spf}'. "
                    "Ensure it is exactly 'v=spf1 include:_spf.mail.hostinger.com ~all' without spaces after 'include:'"
                )
            elif "_spf.mail.hostinger.com" not in spf:
                issues.append(
                    f"Warning: Hostinger SPF include not found. Recommended to include:_spf.mail.hostinger.com "
                    f"in record: '{spf}'"
                )
            
            return len(issues) == 0, issues
        except Exception as e:
            return False, [f"Failed to query SPF: {str(e)}"]

    def verify_dkim(self) -> Tuple[bool, List[str]]:
        """Verifies Hostinger DKIM CNAME records."""
        issues = []
        selectors = ['hostingermail-a', 'hostingermail-b', 'hostingermail-c']
        for sel in selectors:
            sub = f"{sel}._domainkey.{self.domain}"
            expected = f"{sel}.dkim.mail.hostinger.com"
            try:
                answers = dns.resolver.resolve(sub, 'CNAME')
                target = str(answers[0].target).rstrip('.')
                if target != expected:
                    issues.append(f"DKIM selector '{sel}' points to '{target}' instead of '{expected}'")
            except dns.resolver.NXDOMAIN:
                issues.append(
                    f"DKIM record not found for host: '{sel}._domainkey'. "
                    "Make sure you added it as a CNAME without spaces."
                )
            except Exception as e:
                issues.append(f"Error querying DKIM for selector '{sel}': {str(e)}")
        
        return len(issues) == 0, issues

    def verify_mx(self) -> Tuple[bool, List[str]]:
        """Queries and checks MX records."""
        issues = []
        try:
            answers = dns.resolver.resolve(self.domain, 'MX')
            mx_list = []
            for rdata in answers:
                mx_list.append((rdata.preference, str(rdata.exchange).rstrip('.')))
            
            # Sort by preference (lower is higher priority)
            mx_list.sort()
            
            hostinger_mx = ['mx1.hostinger.com', 'mx2.hostinger.com']
            found_mx = [mx[1] for mx in mx_list]
            
            if not any(h in found_mx for h in hostinger_mx):
                issues.append(f"No Hostinger MX records found. Found records: {found_mx}")
            else:
                if 'mx1.hostinger.com' not in found_mx:
                    issues.append("mx1.hostinger.com not found in root MX records.")
                if 'mx2.hostinger.com' not in found_mx:
                    issues.append(
                        "mx2.hostinger.com not found in root MX records. Check if the DNS record was "
                        "misconfigured with host 'a' (which makes it apply to a.binarygrowth.org instead of the root domain)."
                    )
            
            return len(issues) == 0, issues
        except Exception as e:
            return False, [f"Failed to query MX records: {str(e)}"]

    def verify_dmarc(self) -> Tuple[bool, List[str]]:
        """Queries and checks DMARC record."""
        issues = []
        sub = f"_dmarc.{self.domain}"
        try:
            answers = dns.resolver.resolve(sub, 'TXT')
            dmarc_records = []
            for rdata in answers:
                for string in rdata.strings:
                    decoded = string.decode('utf-8')
                    if decoded.startswith('v=DMARC1'):
                        dmarc_records.append(decoded)
            
            if not dmarc_records:
                return False, ["No DMARC record found (TXT record on _dmarc)."]
            
            dmarc = dmarc_records[0]
            if "rua=" in dmarc:
                match = re.search(r'rua=mailto:([^;]+)', dmarc)
                if match:
                    rua_email = match.group(1).strip()
                    if rua_email == f"yogesh@{self.domain}":
                        issues.append(
                            f"Warning: DMARC rua reports are configured to send to '{rua_email}'. "
                            "This will flood your main outreach mailbox with XML reports. "
                            "We recommend using a dedicated mailbox (e.g. dmarc-reports@yourdomain.com)."
                        )
            
            return len(issues) == 0, issues
        except dns.resolver.NXDOMAIN:
            return False, ["DMARC record not found. Create a TXT record for '_dmarc' with your policy."]
        except Exception as e:
            return False, [f"Failed to query DMARC: {str(e)}"]

    def run_diagnostics(self) -> Dict[str, Tuple[bool, List[str]]]:
        """Runs all checks and returns a summary dictionary."""
        return {
            "SPF": self.verify_spf(),
            "DKIM": self.verify_dkim(),
            "MX": self.verify_mx(),
            "DMARC": self.verify_dmarc()
        }
