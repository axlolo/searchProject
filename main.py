from serpapi import GoogleSearch
from openai import OpenAI
import requests
from bs4 import BeautifulSoup
import json, os
from urllib.parse import urlparse

from dotenv import load_dotenv
load_dotenv()

#pip install google-search-results
#pip install python-dotenv

OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY")
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

price_input_per_million = 0.1
price_output_per_million = 0.4

client = OpenAI(api_key=OPENAI_API_KEY)

# ─── 1) paywall map persistence ───────────────────────────────────────────────

DOMAIN_STATUS_FILE = "domain_status.json"

def load_domain_map():
    if os.path.exists(DOMAIN_STATUS_FILE):
        try:
            with open(DOMAIN_STATUS_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return {}
    return {}

def save_domain_map(m):
    with open(DOMAIN_STATUS_FILE, "w") as f:
        json.dump(m, f, indent=2)

# ensure file exists and is valid JSON
if not os.path.exists(DOMAIN_STATUS_FILE) or os.path.getsize(DOMAIN_STATUS_FILE) == 0:
    save_domain_map({})

domain_map = load_domain_map()

def get_root_domain(url):
    netloc = urlparse(url).netloc.lower()
    parts = netloc.split(".")
    if len(parts) >= 2:
        return ".".join(parts[-2:])
    return netloc

# paywall detection on first‐seen domains

PAYWALL_SELECTORS = [
    ".paywall",             # generic
    ".subscription-wall",   # some publishers
    "#gateway-content",     # NYTimes “gateway” div
    ".meteredContent",      # WSJ, FT, etc.
]

def is_accessible(url):
    domain = get_root_domain(url)
    if domain in domain_map:
        return domain_map[domain]

    try:
        resp = requests.get(url, timeout=5)
    except Exception:
        domain_map[domain] = False
        save_domain_map(domain_map)
        return False

    # 1) outright forbidden
    if resp.status_code == 403:
        domain_map[domain] = False
        save_domain_map(domain_map)
        return False

    # parse HTML
    soup = BeautifulSoup(resp.text, "html.parser")

    # 2) overlay detection
    for sel in PAYWALL_SELECTORS:
        if soup.select_one(sel):
            domain_map[domain] = False
            save_domain_map(domain_map)
            return False

    # otherwise assume accessible
    domain_map[domain] = True
    save_domain_map(domain_map)
    return True

resultList = []
summaryList = []

def search(query, num_results):
    params = {
        "engine":   "google_news",
        "q":        query,
        "hl":       "en",
        "gl":       "us",
        "num":      num_results,
        "api_key":  SERPAPI_API_KEY,
    }
    try:
        results = GoogleSearch(params).get_dict().get("news_results", [])
        resultList.extend(results[:num_results])
    except Exception as e:
        print(f"Error during search: {e}")


def get_snippet(country, topic, date):
    resultList.clear()
    summaryList.clear()

    query = f"most important {topic} news on {date} in {country}"
    search(query, 10)

    # filter out paywalled domains before fetching
    original = list(resultList)
    filtered = []
    for res in original:
        url = res.get("link")
        if not url:
            print("  ⏭ no URL in result, skipping")
            continue

        domain = get_root_domain(url)
        status = domain_map.get(domain)   # True / False / None
        # 1) explicitly paywalled?
        if status is False:
            print(f"{domain} known paywalled, skipping")
            continue

        # 2) explicitly accessible?
        if status is True:
            print(f" {domain} known accessible, keeping")
            filtered.append(res)
            continue

        # 3) unknown domain → test it now
        print(f" {domain} unknown, testing…")
        if is_accessible(url):
            print(f"accessible, keeping")
            filtered.append(res)
        else:
            print(f"paywalled, skipping")

    resultList[:] = filtered
    print(f"Filter: started with {len(original)} links, kept {len(filtered)}")

    for res in resultList:
        input_tokens = 0
        output_tokens = 0

        url = res["link"]
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
        except Exception as e:
            print(f"Could not fetch {url}: {e}")
            continue

        text = " ".join(p.get_text() for p in BeautifulSoup(r.text, "html.parser").find_all("p"))
        if len(text) < 200:
            continue

        resp = client.chat.completions.create(
            model="gpt-4.1-nano-2025-04-14",
            messages=[
                {"role": "system", "content": "You are an analytical and concise news reporter."},
                {"role": "user",   "content": "Summarize the following article in 2 sentences:\n\n" + text}
            ],
            max_tokens=150,
        )
        summaryList.append(resp.choices[0].message.content.strip())
        input_tokens += resp.usage.prompt_tokens
        output_tokens += resp.usage.completion_tokens

    combined = "\n".join(summaryList)
    prompt = (
        "You are an analytical and concise news reporter. You will be given search results "
        "and are tasked with determining which news are most relevant. You must take all of the "
        "given information and generate a short, 2 sentence summary for the headline of the news.\n\n"
        + combined
    )
    final = client.chat.completions.create(
        model="gpt-4.1-nano-2025-04-14",
        messages=[
            {"role": "system", "content": "You are an analytical and concise news reporter."},
            {"role": "user",   "content": prompt}
        ],
        max_tokens=100,
    )
    input_tokens += resp.usage.prompt_tokens
    output_tokens += resp.usage.completion_tokens
    print(f"Price ={round(price(input_tokens, output_tokens), 6)}, # tokens ={input_tokens}, # output ={output_tokens}")

    save_domain_map(domain_map)

    return f"{final.choices[0].message.content.strip()} ({len(resultList)} links)"

def price (input, output):
    priceI = price_input_per_million/1000000
    priceO = price_output_per_million/1000000

    return input*priceI + output*priceO

if __name__ == "__main__":
    print(get_snippet("United States", "Politics", "May 1st, 2025"))