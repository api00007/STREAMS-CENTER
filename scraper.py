import os
import re
import sys
import json
import time
import random
import shutil
from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from urllib.parse import urljoin
import cloudscraper

API_ORIGIN = "https://streamcenter.st"
OUTPUT_FILE = "streams-center.json"

DEFAULT_SPORTS = ["football", "soccer", "basketball", "baseball", "boxing"]

def get_ist_time():
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist_offset).strftime('%d/%m/%y %H:%M:%S IST')

def log_to_console(message):
    print(message, file=sys.stderr)

def deduplicate(seq):
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]

def safe_json_parse(text):
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        if "][" in text:
            first_part = text.split("][")[0] + "]"
            try:
                return json.loads(first_part)
            except Exception:
                pass
        match = re.search(r'(\[.*\]|\{.*\})', text, re.S)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                pass
    return None

def push_to_github():
    GITHUB_TOKEN = os.getenv("GH_TOKEN")
    GITHUB_USER = os.getenv("TGITHUB_USER")
    GITHUB_REPO = os.getenv("TGITHUB_REPO")
    GITHUB_EMAIL = os.getenv("TGITHUB_EMAIL")
    
    if not GITHUB_TOKEN or not GITHUB_USER or not GITHUB_REPO:
        log_to_console("[INFO] GitHub secrets missing. Skipping push.")
        return

    temp_dir = "temp_external_repo"
    remote_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            
        clone_status = os.system(f"git clone {remote_url} {temp_dir}")
        if clone_status != 0:
            raise Exception("Git clone failed. Please check repository permissions or token.")
        
        shutil.copy(OUTPUT_FILE, os.path.join(temp_dir, OUTPUT_FILE))
        
        current_dir = os.getcwd()
        os.chdir(temp_dir)
        
        os.system(f'git config user.email "{GITHUB_EMAIL if GITHUB_EMAIL else "action@github.com"}"')
        os.system(f'git config user.name "{GITHUB_USER}"')
        os.system(f"git add {OUTPUT_FILE}")
        os.system(f'git commit -m "Auto Update: {get_ist_time()}" || echo "No changes"')
        push_status = os.system("git push origin main")
        
        os.chdir(current_dir)
        shutil.rmtree(temp_dir)
        
        if push_status == 0:
            log_to_console(f"[SUCCESS] {OUTPUT_FILE} successfully updated in {GITHUB_USER}/{GITHUB_REPO}.")
        else:
            log_to_console("[ERROR] Git push command failed.")
            
    except Exception as e:
        log_to_console(f"[ERROR] Push failed: {e}")

def scan_m3u8_from_html(html_text):
    stream_tokens = re.findall(r'(?:stream=|hls\.php\?stream=)([a-zA-Z0-9_.-]+)', html_text)
    for token in stream_tokens:
        return f"https://edgestream2.pro/hls/{token}.m3u8|Referer=https://streame.center"
        
    direct_m3u8 = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html_text)
    for link in direct_m3u8:
        clean_link = link.replace('\\/', '/').strip('"\';')
        return f"{clean_link}|Referer=https://streame.center"
        
    return None

def extract_stream_from_embed(scraper, embed_url):
    try:
        headers = {
            'Referer': f"{API_ORIGIN}/",
            'Origin': API_ORIGIN
        }
        res = scraper.get(embed_url, headers=headers, timeout=12)
        res.encoding = 'utf-8'
        html = res.text
        
        link = scan_m3u8_from_html(html)
        if link:
            return link

        iframes = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for ifr in iframes:
            if ifr.startswith('//'):
                ifr_url = 'https:' + ifr
            elif not ifr.startswith('http'):
                ifr_url = urljoin(embed_url, ifr)
            else:
                ifr_url = ifr

            time.sleep(random.uniform(0.3, 0.5))
            ifr_res = scraper.get(ifr_url, headers={'Referer': embed_url}, timeout=10)
            ifr_res.encoding = 'utf-8'
            ifr_link = scan_m3u8_from_html(ifr_res.text)
            if ifr_link:
                return ifr_link
    except Exception as e:
        log_to_console(f"    [!] Error loading embed {embed_url}: {e}")
        
    return None

def run_scraper():
    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'android', 'desktop': False})
    
    log_to_console(f"[*] Connecting to: {API_ORIGIN}")
    sports_slugs = []

    try:
        sports_res = scraper.get(f"{API_ORIGIN}/api/sports", timeout=12)
        if sports_res.status_code == 200:
            parsed = safe_json_parse(sports_res.text)
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and "id" in item:
                        sports_slugs.append(item["id"])
                    elif isinstance(item, str):
                        sports_slugs.append(item)
    except Exception as e:
        log_to_console(f"[!] Failed to fetch sports catalog: {e}")

    if not sports_slugs:
        sports_slugs = DEFAULT_SPORTS

    sports_slugs = deduplicate(sports_slugs)
    log_to_console(f"[+] Total sports categories: {len(sports_slugs)}")

    all_live_matches = []
    seen_match_ids = set()

    for sport in sports_slugs:
        log_to_console(f"[*] Scanning matches for sport: {sport}")
        try:
            time.sleep(random.uniform(0.4, 0.7))
            matches_res = scraper.get(f"{API_ORIGIN}/api/matches/{sport}", timeout=12)
            if matches_res.status_code != 200:
                continue

            matches_data = safe_json_parse(matches_res.text)
            if not isinstance(matches_data, list):
                continue

            for match in matches_data:
                match_id = match.get("id")
                if not match_id or match_id in seen_match_ids:
                    continue

                seen_match_ids.add(match_id)
                raw_title = match.get("title", "")
                clean_rivals = re.sub(r'\s+@\s+|\s+\|\s+', ' vs ', raw_title).strip()
                cat_name = match.get("category", sport).replace("-", " ").title()

                sources = match.get("sources", [])
                if not sources:
                    continue

                for s_idx, src in enumerate(sources, 1):
                    src_type = src.get("source", "iframe")
                    src_id = src.get("id")
                    if not src_id:
                        continue

                    time.sleep(random.uniform(0.3, 0.6))
                    stream_api = f"{API_ORIGIN}/api/stream/{src_type}/{src_id}"
                    
                    try:
                        stream_res = scraper.get(stream_api, timeout=10)
                        if stream_res.status_code != 200:
                            continue

                        streams_info = safe_json_parse(stream_res.text)
                        if not isinstance(streams_info, list) or not streams_info:
                            continue

                        for stream_entry in streams_info:
                            embed_url = stream_entry.get("embedUrl")
                            stream_num = stream_entry.get("streamNo", s_idx)
                            
                            if not embed_url:
                                continue

                            final_m3u8 = extract_stream_from_embed(scraper, embed_url)
                            if final_m3u8:
                                log_to_console(f"    [+] Link Created: {clean_rivals} (S-{stream_num})")
                                all_live_matches.append(OrderedDict([
                                    ("Id", str(len(all_live_matches) + 1)),
                                    ("Rivels", clean_rivals),
                                    ("Title", f"{cat_name} (S-{stream_num})"),
                                    ("Link", final_m3u8)
                                ]))
                    except Exception:
                        continue
        except Exception:
            continue

    log_to_console(f"\n[+] Total Live Streams Collected: {len(all_live_matches)}")

    final_package = OrderedDict([
        ("Owner", "Ivan-FluX"),
        ("App name", "streams-center"),
        ("Last update", get_ist_time()),
        ("Total_Matches", len(all_live_matches)),
        ("Live_Data", all_live_matches)
    ])

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_package, f, indent=4, ensure_ascii=False)

    push_to_github()
    print(json.dumps(final_package, indent=4, ensure_ascii=False))

if __name__ == "__main__":
    run_scraper()
