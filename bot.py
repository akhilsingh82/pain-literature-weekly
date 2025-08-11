import os, datetime, html, smtplib, ssl, requests, re
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from zoneinfo import ZoneInfo
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import xml.etree.ElementTree as ET

# -------------------
# Config
# -------------------
load_dotenv()

# Use MEDLINE/NLM abbreviations with [ta]
JOURNALS = [
    "Pain[ta]",
    "Pain Physician[ta]",
    "Pain Med[ta]",
    "Reg Anesth Pain Med[ta]",
    "J Pain[ta]",
    "Interv Pain Med[ta]",
    "Cephalalgia[ta]",
    "J Headache Pain[ta]",
    "Pain Rep[ta]",
    "J Pain Res[ta]",
    "Eur J Pain[ta]",
    "Pain Ther[ta]",
    "Scand J Pain[ta]",
    "Mol Pain[ta]",
    "Pain Pract[ta]",
    "Pain Res Manag[ta]"
]

KEYWORDS = [
    "Pain Management",
    "Pain Measurement",
    "Analgesia",
    "\"Analgesics, Non-Narcotic\"",
    "\"Analgesics, Opioid\"",
    "\"Nerve Block\"",
    "\"Epidural Analgesia\"",
    "\"Spinal Cord Stimulation\"",
    "Neuromodulation",
    "\"Local Anesthesia\"",
    "\"Anesthesia, Local\"",
    "\"Anesthesia, Epidural\"",
    "Injections",
    "\"Acupuncture Therapy\"",
    "\"Physical Therapy Modalities\"",
    "\"Surgical Procedures, Operative\"",
    "Therapeutics"
]

ADD_HUMANS_FILTER = False  # set True to bias toward human studies
INCLUDE_CONCLUSION_SNIPPET = True  # Option 4: show Conclusion/last lines from abstract (no AI)
SNIPPET_MAX_WORDS = 70  # keep email compact

EMAIL_TO = os.environ["EMAIL_TO"]
EMAIL_FROM = os.environ["EMAIL_FROM"]
SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ["SMTP_PORT"])
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASS"]

NCBI_TOOL = os.environ.get("NCBI_TOOL", "pain-weekly-bot")
NCBI_EMAIL = os.environ.get("NCBI_EMAIL", "")
NCBI_API_KEY = os.environ.get("NCBI_API_KEY", "")

IST = ZoneInfo("Asia/Kolkata")

# -------------------
# HTTP Session with retries
# -------------------
def make_session():
    s = requests.Session()
    retries = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "POST"])
    )
    s.mount("https://", HTTPAdapter(max_retries=retries))
    s.mount("http://", HTTPAdapter(max_retries=retries))
    return s

SESSION = make_session()

# -------------------
# Query construction
# -------------------
def pubmed_query(journals, keywords, humans=False):
    # journals already include [ta]
    j = " OR ".join(journals)
    k = " OR ".join(keywords) if keywords else ""
    core = f"({j})" if j else ""
    if k:
        core = f"{core} AND ({k})"
    if humans:
        core = f"{core} AND (humans[MeSH Terms])"
    return core.strip()

def last_7d_window_ist(today_ist=None):
    # Deterministic 7-day window in IST using ENTRY date (edat)
    if today_ist is None:
        today_ist = datetime.datetime.now(IST).date()
    start = today_ist - datetime.timedelta(days=7)
    # PubMed accepts YYYY/MM/DD
    mindate = start.strftime("%Y/%m/%d")
    maxdate = today_ist.strftime("%Y/%m/%d")
    return mindate, maxdate

# -------------------
# PubMed helpers
# -------------------
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

def eutils_params(extra=None):
    p = {"tool": NCBI_TOOL, "email": NCBI_EMAIL}
    if NCBI_API_KEY:
        p["api_key"] = NCBI_API_KEY
    if extra:
        p.update(extra)
    return p

def esearch(term, mindate, maxdate):
    params = eutils_params({
        "db": "pubmed",
        "term": term,
        "retmode": "json",
        "retmax": 300,
        "datetype": "edat",
        "mindate": mindate,
        "maxdate": maxdate
    })
    r = SESSION.get(f"{EUTILS}/esearch.fcgi", params=params, timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("esearchresult", {}).get("idlist", [])

def esummary(pmids):
    if not pmids:
        return []
    params = eutils_params({
        "db": "pubmed",
        "retmode": "json",
        "id": ",".join(pmids)
    })
    r = SESSION.get(f"{EUTILS}/esummary.fcgi", params=params, timeout=30)
    r.raise_for_status()
    result = r.json().get("result", {})
    items = []
    for pid, v in result.items():
        if pid == "uids":
            continue
        title = (v.get("title") or "").strip()
        journal = v.get("fulljournalname") or v.get("source") or ""
        sortdate = v.get("sortpubdate") or v.get("pubdate") or ""
        doi = ""
        for idv in v.get("articleids", []):
            if idv.get("idtype") == "doi":
                doi = idv.get("value")
                break
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pid}/"
        items.append({
            "pmid": pid,
            "title": title,
            "journal": journal,
            "date": sortdate,
            "doi": doi,
            "url": url
        })
    # Deduplicate primarily by DOI, else PMID
    dedup = {}
    for it in items:
        key = ("doi", (it["doi"] or "").lower()) if it["doi"] else ("pmid", it["pmid"])
        if key not in dedup:
            dedup[key] = it
    items = list(dedup.values())

    def parse_sortdate(s):
        try:
            return datetime.datetime.strptime(s, "%Y/%m/%d")
        except Exception:
            return datetime.datetime.min

    items.sort(key=lambda x: parse_sortdate(x["date"]), reverse=True)
    return items

# -------------------
# Abstract / Conclusion extraction (no AI)
# -------------------
def efetch_abstract_map(pmids):
    """
    Returns { pmid: {"abstract": str|None, "conclusion": str|None} }
    """
    out = {}
    if not pmids:
        return out
    # Batch to keep URLs and responses sane
    BATCH = 100
    for i in range(0, len(pmids), BATCH):
        chunk = pmids[i:i+BATCH]
        params = eutils_params({
            "db": "pubmed",
            "id": ",".join(chunk),
            "retmode": "xml"
        })
        r = SESSION.get(f"{EUTILS}/efetch.fcgi", params=params, timeout=60)
        r.raise_for_status()
        # Parse XML
        root = ET.fromstring(r.text)
        # PubmedArticleSet/PubmedArticle/MedlineCitation/Article/Abstract/AbstractText
        for art in root.findall(".//PubmedArticle"):
            pmid_el = art.find(".//MedlineCitation/PMID")
            if pmid_el is None or not pmid_el.text:
                continue
            pid = pmid_el.text.strip()
            abstract_el = art.find(".//MedlineCitation/Article/Abstract")
            abstract_texts = []
            conclusion_texts = []
            if abstract_el is not None:
                for t in abstract_el.findall("./AbstractText"):
                    label = (t.get("Label") or t.get("NlmCategory") or "").strip().lower()
                    text = "".join(t.itertext()).strip()
                    if not text:
                        continue
                    abstract_texts.append(text)
                    if "conclusion" in label or "conclusions" in label:
                        conclusion_texts.append(text)
            abstract = " ".join(abstract_texts).strip() if abstract_texts else None
            conclusion = " ".join(conclusion_texts).strip() if conclusion_texts else None
            out[pid] = {"abstract": abstract, "conclusion": conclusion}
    return out

def last_sentences(text, n=2):
    # Split into sentences and take the last n
    # Simple split; handles periods, question marks, exclamations
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    parts = [p for p in parts if p]
    if not parts:
        return ""
    return " ".join(parts[-n:]).strip()

def trim_words(text, max_words):
    words = text.split()
    if len(words) <= max_words:
        return text
    return " ".join(words[:max_words]) + "…"

def build_snippet(meta_map_entry):
    if not meta_map_entry:
        return None, None
    abstract = (meta_map_entry.get("abstract") or "").strip()
    concl = (meta_map_entry.get("conclusion") or "").strip()
    if concl:
        return "Conclusion", trim_words(concl, SNIPPET_MAX_WORDS)
    if abstract:
        # fallback: last 1–2 sentences of abstract
        fallback = last_sentences(abstract, n=2)
        if not fallback:
            fallback = abstract
        return "From abstract", trim_words(fallback, SNIPPET_MAX_WORDS)
    return None, None

# -------------------
# Email formatting
# -------------------
def build_html(items, mindate, maxdate, meta_map=None):
    if not items:
        return f"<p>No new items between {mindate} and {maxdate}.</p>"
    rows = []
    for it in items:
        title = html.escape(it["title"])
        j = html.escape(it["journal"])
        date = html.escape(it["date"])
        doi = it["doi"]
        doi_link = f' | DOI: <a href="https://doi.org/{doi}">{html.escape(doi)}</a>' if doi else ""
        snippet_html = ""
        if INCLUDE_CONCLUSION_SNIPPET and meta_map is not None:
            label, snip = build_snippet(meta_map.get(it["pmid"]))
            if snip:
                snippet_html = f'<div><strong>{html.escape(label)}:</strong> {html.escape(snip)}</div>'
        rows.append(
            f'<li><a href="{it["url"]}">{title}</a>'
            f' — <em>{j}</em> ({date}){doi_link}'
            f'{snippet_html}</li>'
        )
    return f"""
    <h2>Pain Literature Weekly</h2>
    <p>Coverage (EDAT): {mindate} to {maxdate}; journals: {', '.join([j.replace('[ta]','') for j in JOURNALS])}</p>
    <ol>
    {''.join(rows)}
    </ol>
    """

def build_text(items, mindate, maxdate, meta_map=None):
    if not items:
        return f"No new items between {mindate} and {maxdate}."
    lines = [f"Pain Literature Weekly", f"Coverage (EDAT): {mindate} to {maxdate}"]
    for it in items:
        doi_part = f" | DOI: https://doi.org/{it['doi']}" if it["doi"] else ""
        base = f"- {it['title']} — {it['journal']} ({it['date']}) {it['url']}{doi_part}"
        if INCLUDE_CONCLUSION_SNIPPET and meta_map is not None:
            label, snip = build_snippet(meta_map.get(it["pmid"]))
            if snip:
                base += f"\n  {label}: {snip}"
        lines.append(base)
    return "\n".join(lines)

def send_email(html_body, text_body, subject):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(text_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls(context=ctx)
        s.login(SMTP_USER, SMTP_PASS)
        s.send_message(msg)

# -------------------
# Main
# -------------------
if __name__ == "__main__":
    today_ist = datetime.datetime.now(IST).date()
    mindate, maxdate = last_7d_window_ist(today_ist)

    term = pubmed_query(JOURNALS, KEYWORDS, humans=ADD_HUMANS_FILTER)
    pmids = esearch(term, mindate, maxdate)
    items = esummary(pmids)

    # Fetch abstracts & extract conclusions (no AI)
    meta_map = efetch_abstract_map([it["pmid"] for it in items]) if INCLUDE_CONCLUSION_SNIPPET else None

    html_body = build_html(items, mindate, maxdate, meta_map)
    text_body = build_text(items, mindate, maxdate, meta_map)
    subject = f"Pain Literature Weekly — {mindate} to {maxdate}"

    send_email(html_body, text_body, subject)
