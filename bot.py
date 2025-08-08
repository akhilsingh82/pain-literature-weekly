# Pain Literature Weekly Bot
# This script fetches new PubMed articles weekly and emails a summary

import os, datetime, html, smtplib, ssl, requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv

load_dotenv()

JOURNALS = [
    "Pain", "Journal of Pain", "Neurology", "Pain Medicine", "PAIN Reports"
]
KEYWORDS = [
    "chronic pain", "neuropathic pain", "low back pain", "radiofrequency ablation"
]

# Build PubMed query
def pubmed_query():
    j = " OR ".join([f'\"{x}\"[ta]' for x in JOURNALS])
    k = " OR ".join(KEYWORDS)
    return f"(({j})) AND ({k})"

# Search PubMed
def esearch(term):
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    params = {
        "db": "pubmed", "term": term,
        "retmode": "json", "retmax": 200,
        "datetype": "edat", "reldate": 7
    }
    r = requests.get(base, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("esearchresult", {}).get("idlist", [])

# Fetch summaries

def esummary(pmids):
    if not pmids: return []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db":"pubmed","retmode":"json","id": ",".join(pmids)}
    r = requests.get(base, params=params, timeout=30); r.raise_for_status()
    result = r.json().get("result", {})
    items = []
    for pid, v in result.items():
        if pid == "uids": continue
        items.append({
            "title": v.get("title", ""),
            "journal": v.get("fulljournalname", v.get("source", "")),
            "pubdate": v.get("pubdate", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
            "doi": next((x.get("value") for x in v.get("articleids", []) if x.get("idtype") == "doi"), None)
        })
    return sorted(items, key=lambda x: x["pubdate"], reverse=True)

# Generate HTML email

def build_html(items):
    if not items:
        return "<p>No new articles this week.</p>"
    html_rows = []
    for i in items:
        title = html.escape(i["title"])
        journal = html.escape(i["journal"])
        doi = f'<br>DOI: <a href="https://doi.org/{i["doi"]}">{i["doi"]}</a>' if i["doi"] else ""
        html_rows.append(f'<li><a href="{i["url"]}">{title}</a><br><em>{journal}</em> ({i["pubdate"]}){doi}</li>')
    return f"<h2>Pain Literature Weekly</h2><ol>{''.join(html_rows)}</ol>"

# Send email

def send_email(html_body):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Pain Literature Weekly — {datetime.date.today()}"
    msg["From"] = os.environ["EMAIL_FROM"]
    msg["To"] = os.environ["EMAIL_TO"]
    msg.attach(MIMEText("See HTML version.", "plain"))
    msg.attach(MIMEText(html_body, "html"))
    ctx = ssl.create_default_context()
    with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])) as s:
        s.starttls(context=ctx)
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)

# Main
if __name__ == "__main__":
    query = pubmed_query()
    ids = esearch(query)
    articles = esummary(ids)
    html_email = build_html(articles)
    send_email(html_email)
