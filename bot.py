# Pain Literature Weekly Bot with 5-Line Digest using ChatGPT

import openai
import os, datetime, html, smtplib, ssl, requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from xml.etree import ElementTree as ET

load_dotenv()
openai.api_key = os.getenv("OPENAI_API_KEY")

JOURNALS = [
    "Pain", "Journal of Pain", "Neurology", "Pain Medicine", "PAIN Reports", "Pain Physician", "Regional Anesthesia and Pain Medicine",
    "International Journal of Pain", "Journal of Pain & Relief", "Interventional Pain Medicine", 
    "Cephalgia Rep", "Can J Pain", "BJA Educ", "Br J Pain", "Pain Management"]
KEYWORDS = [
    "Pain", "Pain, Acute", "Pain, Chronic", "Pain Management", "Pain Measurement", "Referred Pain", "Intractable Pain",
    "Myofascial Pain Syndromes", "Neuropathic Pain", "Nociceptive Pain", "Visceral Pain", "Somatic Pain", "Central Pain",
    "Neuralgia", "Pain Management", "Pain Measurement", "Analgesia", "Analgesics, Non-Narcotic", "Analgesics, Opioid",
    "Nerve Block", "Epidural Analgesia", "Spinal Cord Stimulation", "Neuromodulation", "Local Anesthesia", "Anesthesia, Local",
    "Anesthesia, Epidural", "Injections", "Acupuncture Therapy", "Physical Therapy Modalities",
    "Surgical Procedures, Operative", "Therapeutics"
]

def pubmed_query():
    j = " OR ".join([f'"{x}"[ta]' for x in JOURNALS])
    k = " OR ".join(KEYWORDS)
    return f"(({j})) AND ({k})"

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

def esummary(pmids):
    if not pmids: return []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
    params = {"db":"pubmed","retmode":"json","id": ",".join(pmids)}
    r = requests.get(base, params=params, timeout=30)
    r.raise_for_status()
    result = r.json().get("result", {})
    items = []
    for pid, v in result.items():
        if pid == "uids": continue
        items.append({
            "pmid": pid,
            "title": v.get("title", ""),
            "journal": v.get("fulljournalname", v.get("source", "")),
            "pubdate": v.get("pubdate", ""),
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pid}/",
            "doi": next((x.get("value") for x in v.get("articleids", []) if x.get("idtype") == "doi"), None)
        })
    return sorted(items, key=lambda x: x["pubdate"], reverse=True)

def efetch_abstracts(pmids):
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    params = {
        "db": "pubmed",
        "retmode": "xml",
        "id": ",".join(pmids)
    }
    r = requests.get(base, params=params, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    abstracts = {}
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext(".//PMID")
        abstract_parts = article.findall(".//AbstractText")
        abstract = " ".join([part.text or "" for part in abstract_parts])
        abstracts[pmid] = abstract
    return abstracts

def get_digest(text):
    if not text:
        return "No abstract available."
    prompt = f"Summarise this medical abstract in 5 concise lines:\n\n{text}"
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",  # or "gpt-3.5-turbo"
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return "Summary not available."

def build_html(items):
    if not items:
        return "<p>No new articles this week.</p>"
    html_rows = []
    for i in items:
        title = html.escape(i["title"])
        journal = html.escape(i["journal"])
        doi = f'<br>DOI: <a href="https://doi.org/{i["doi"]}">{i["doi"]}</a>' if i["doi"] else ""
        digest = f'<p><strong>Summary:</strong><br>{html.escape(i.get("digest", ""))}</p>' if i.get("digest") else ""
        html_rows.append(f'<li><a href="{i["url"]}">{title}</a><br><em>{journal}</em> ({i["pubdate"]}){doi}{digest}</li>')
    return f"<h2>Pain Literature Weekly</h2><ol>{''.join(html_rows)}</ol>"

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

if __name__ == "__main__":
    query = pubmed_query()
    ids = esearch(query)
    articles = esummary(ids)
    abstracts = efetch_abstracts(ids)
    for a in articles:
        pmid = a.get("pmid")
        abstract = abstracts.get(pmid, "")
        a["digest"] = get_digest(abstract)
    html_email = build_html(articles)
    send_email(html_email)
