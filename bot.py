import os
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv
from openai import OpenAI, OpenAIError
from Bio import Entrez

# Load environment variables
load_dotenv()

# Email configuration
EMAIL_FROM = os.getenv("EMAIL_FROM")
EMAIL_TO = os.getenv("EMAIL_TO")
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")

# OpenAI client
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# PubMed email
Entrez.email = EMAIL_FROM

# Journals to include
JOURNALS = [
    "Pain", "Journal of Pain", "Neurology", "Pain Medicine", "PAIN Reports", "Pain Physician",
    "Regional Anesthesia and Pain Medicine", "International Journal of Pain", "Journal of Pain & Relief",
    "Interventional Pain Medicine", "Cephalalgia Reports", "Canadian Journal of Pain",
    "BJA Education", "British Journal of Pain", "Pain Management"
]

# Keywords to include
KEYWORDS = [
    "chronic pain", "neuropathic pain", "low back pain", "interventional pain", "opioid alternatives",
    "radiofrequency ablation", "nerve block", "neuromodulation", "analgesia", "spinal cord stimulation"
]

def build_pubmed_query():
    journal_query = " OR ".join([f'"{j}"[TA]' for j in JOURNALS])
    keyword_query = " OR ".join(KEYWORDS)
    return f"({journal_query}) AND ({keyword_query}) AND 2025[dp]"

def search_pubmed(query, max_results=10):
    handle = Entrez.esearch(db="pubmed", term=query, sort="pub+date", retmax=max_results)
    record = Entrez.read(handle)
    return record["IdList"]

def fetch_title_and_abstract(pmid):
    try:
        handle = Entrez.efetch(db="pubmed", id=pmid, rettype="xml", retmode="xml")
        record = Entrez.read(handle)
        article = record["PubmedArticle"][0]["MedlineCitation"]["Article"]
        title = article["ArticleTitle"]
        abstract_parts = article.get("Abstract", {}).get("AbstractText", [])
        abstract = " ".join(abstract_parts) if abstract_parts else ""
        return title, abstract
    except Exception as e:
        print(f"Error fetching data for PMID {pmid}: {e}")
        return "Title not available", ""

def summarize_abstract(abstract):
    if not abstract.strip():
        return "No abstract available."

    messages = [
        {
            "role": "system",
            "content": "You are an expert summariser for scientific medical literature. Summarise this abstract in plain language in 2-3 sentences."
        },
        {
            "role": "user",
            "content": abstract
        }
    ]

    try:
        # Only use GPT-3.5 Turbo to avoid errors
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=messages,
            max_tokens=300,
            temperature=0.3
        )
        return response.choices[0].message.content.strip()

    except openai.OpenAIError as e:
        print("GPT-3.5 failed:", e)
        return "Summary not available."

    return response.choices[0].message.content.strip()

def format_email_content(articles):
    lines = []
    for title, abstract, summary in articles:
        lines.append(
            f"📰 Title: {title}\n"
            f"📄 Abstract Preview: {abstract[:400] + '...' if len(abstract) > 400 else abstract}\n"
            f"📝 Summary: {summary}\n"
            f"{'-'*60}\n"
        )
    return "\n".join(lines)

def send_email(subject, body):
    msg = EmailMessage()
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
        server.login(SMTP_USER, SMTP_PASS)
        server.send_message(msg)

def main():
    query = build_pubmed_query()
    pmids = search_pubmed(query, max_results=10)

    articles = []
    for pmid in pmids:
        title, abstract = fetch_title_and_abstract(pmid)
        summary = summarize_abstract(abstract)
        articles.append((title, abstract, summary))

    email_body = format_email_content(articles)
    send_email(subject="🧠 Weekly Chronic Pain Research Digest", body=email_body)
    print("✅ Email sent successfully.")

if __name__ == "__main__":
    main()
