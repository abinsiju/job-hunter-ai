"""Terms a help-desk / IT-support ad names. Each entry: the canonical skill and the ways ads write it. Used to READ the ad (what it asks for);
it never adds anything to your resume: only your verified skills can appear there."""
IT_TERMS: dict[str, list[str]] = {
    "Microsoft 365": [r"microsoft (office )?365", r"microsoft 365", r"office 365", r"\bm365\b", r"\bo365\b", r"ms 365", r"microsoft office"],
    "Active Directory": [r"active directory", r"azure ad\b", r"entra( id)?", r"\bad\b(?= (users|accounts|admin))"],
    "Windows": [r"windows (10|11|server|7|8)", r"\bwindows\b"], "macOS": [r"\bmac ?os\b", r"\bmacs\b", r"\bapple mac"], "Linux": [r"\blinux\b", r"\bubuntu\b"],
    "Intune": [r"\bintune\b", r"endpoint manager", r"\bmdm\b"], "SCCM": [r"\bsccm\b", r"\bmecm\b", r"configuration manager"], "Group Policy": [r"group policy", r"\bgpo\b"],
    "Azure": [r"\bazure\b"], "AWS": [r"\baws\b", r"amazon web services"], "VMware": [r"vmware", r"\besxi\b"], "Hyper-V": [r"hyper-?v"],
    "PowerShell": [r"powershell"], "Python": [r"\bpython\b"], "Bash": [r"\bbash\b", r"shell script"],
    "ServiceNow": [r"servicenow"], "Jira": [r"\bjira\b"], "Zendesk": [r"zendesk"], "Freshservice": [r"freshservice"], "Confluence": [r"confluence"],
    "ITIL": [r"\bitil\b"], "Service desk": [r"service desk", r"help ?desk", r"service centre", r"service center"], "Ticketing": [r"ticketing", r"ticket (system|management)", r"\bitsm\b"],
    "Networking": [r"\bnetworking\b", r"\bnetworks\b"], "TCP/IP": [r"tcp/?ip"], "DNS": [r"\bdns\b"], "DHCP": [r"\bdhcp\b"], "VPN": [r"\bvpn\b"], "VLAN": [r"\bvlans?\b"],
    "Firewall": [r"firewalls?", r"fortigate", r"palo alto", r"sonicwall"], "Cisco": [r"\bcisco\b", r"\bccna\b", r"meraki"], "Wi-Fi": [r"wi-?fi", r"wireless"],
    "Citrix": [r"\bcitrix\b"], "VDI": [r"\bvdi\b", r"virtual desktop"], "Okta": [r"\bokta\b"], "MFA": [r"\bmfa\b", r"multi-?factor", r"\b2fa\b"],
    "Backup": [r"\bbackups?\b", r"veeam", r"disaster recovery"], "Antivirus": [r"antivirus", r"endpoint protection", r"crowdstrike", r"defender"],
    "Printers": [r"printers?", r"printing"], "Hardware": [r"hardware", r"laptops?", r"desktops?", r"peripherals?"], "Mobile devices": [r"mobile devices?", r"\bios\b", r"android"],
    "Remote support": [r"remote (support|assistance|desktop)", r"teamviewer", r"anydesk"], "Teams": [r"microsoft teams", r"\bteams\b(?= (meeting|rooms?|admin|phone|support))"], "SharePoint": [r"sharepoint"],
    "Exchange": [r"exchange (online|server)", r"\bexchange\b"], "Customer service": [r"customer service", r"customer support", r"client support"],
    "Documentation": [r"documentation", r"knowledge base", r"knowledge articles?"], "Onboarding": [r"onboarding", r"offboarding", r"user provisioning"],
    "Troubleshooting": [r"troubleshoot", r"diagnos", r"fault finding"], "SQL": [r"\bsql\b"], "Cybersecurity": [r"cyber ?security", r"information security", r"\bsecurity\b(?= (awareness|incident|operations))"],
    "CompTIA": [r"comptia", r"a\+", r"network\+", r"security\+"], "Telephony": [r"telephony", r"\bvoip\b", r"3cx", r"teams phone"],
    "Asset management": [r"asset (management|tracking)", r"device lifecycle"], "Scripting": [r"scripting", r"automation"],
}
STOP = set("""a an and are as at be been but by can do for from has have in is it its more most must of on or our out over such than that the their then there these they this to up us was we were will with within you your
about across after all also any both each etc into per role team work working job position candidate applicants applicant apply application successful required requirements experience skills ability strong good great excellent knowledge
looking join part provide support ensure including include includes other new well high level based using use used make help like need needs needed responsible responsibilities opportunity company business organisation organization""".split())
GENERIC = {"customer", "service", "technical", "technology", "information", "systems", "system", "issues", "users", "staff", "environment", "tasks", "range", "days", "hours", "week",
           "time", "first", "pressure", "fast", "paced", "fantastic", "growing", "benefits", "salary", "culture", "people", "positive", "friendly", "supportive", "flexible", "ongoing",
           "daily", "key", "main", "various", "variety", "different", "maintain", "manage", "perform", "provide", "deliver", "assist", "handle", "resolve", "resolving", "respond", "responding"}

# everyday capabilities, not named technologies. They count as ATS KEYWORDS but are not "skills the ad requires": a generic word must never be
# mistaken for a claim the resume has to prove (and the fact-checker would rightly refuse a letter that mentions one you have not verified).
CAPABILITIES = {"Service desk", "Customer service", "Troubleshooting", "Documentation", "Onboarding", "Hardware", "Printers", "Mobile devices", "Remote support", "Ticketing",
                "Backup", "Antivirus", "Scripting", "Asset management", "Networking", "Wi-Fi"}
