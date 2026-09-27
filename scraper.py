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

BASE_URL = os.getenv("BASE_URL", "https://streamcenter.st").strip()
OUTPUT_FILE = "streams-center.json"

DEFAULT_SPORTS = [
    "american-football",
    "football",
    "baseball",
    "basketball",
    "hockey",
    "fighting",
    "motorsport",
    "wwe"
]

def get_ist_time():
    ist_offset = timezone(timedelta(hours=5, minutes=30))
    return datetime.now(ist_offset).strftime('%d/%m/%y %H:%M:%S IST')

def log_to_console(message):
    print(message, file=sys.stderr)

def deduplicate(seq):
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]

def push_to_github():
    GITHUB_TOKEN = os.getenv("GH_TOKEN")
    GITHUB_USER = os.getenv("TGITHUB_USER")
    GITHUB_REPO = os.getenv("TGITHUB_REPO")
    GITHUB_EMAIL = os.getenv("TGITHUB_EMAIL")
    
    if not GITHUB_TOKEN or not GITHUB_USER or not GITHUB_REPO:
        log_to_console("[INFO] GitHub secrets missing. Skipping GitHub push.")
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

def resolve_stream_url(scraper, origin, group):
    source = group.get("source") or group.get("provider") or "iframe"
    source_id = group.get("id")
    if not source_id:
        return None

    api_url = f"{origin}/api/stream/{source}/{source_id}"
    try:
        res = scraper.get(api_url, timeout=10)
        if res.status_code == 200:
            data = res.json()
            if isinstance(data, list) and data:
                first = data[0]
                if isinstance(first, dict):
                    return first.get("url") or first.get("src") or first.get("link")
                elif isinstance(first, str):
                    return first
            elif isinstance(data, dict):
                return data.get("url") or data.get("src") or data.get("link")
    except Exception:
        pass
    return None

def run_scraper():
    origin = BASE_URL.rstrip('/')
    if "streame.center" in origin:
        origin = "https://streamcenter.st"
    elif not origin.startswith("http"):
        origin = "https://" + origin

    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'android', 'desktop': False})
    
    log_to_console(f"[*] Connecting to catalog API at: {origin}")
    
    sports_slugs = []
    try:
        sports_res = scraper.get(f"{origin}/api/sports", timeout=12)
        if sports_res.status_code == 200:
            sports_data = sports_res.json()
            if isinstance(sports_data, list):
                for item in sports_data:
                    if isinstance(item, str):
                        sports_slugs.append(item)
                    elif isinstance(item, dict) and "id" in item:
                        sports_slugs.append(item["id"])
                    elif isinstance(item, dict) and "slug" in item:
                        sports_slugs.append(item["slug"])
    except Exception as e:
        log_to_console(f"[!] Sports list API unavailable ({e}), using default sports catalog.")

    if not sports_slugs:
        sports_slugs = DEFAULT_SPORTS

    sports_slugs = deduplicate(sports_slugs)
    log_to_console(f"[+] Sports catalog ready with {len(sports_slugs)} disciplines.")

    all_live_matches = []
    seen_match_ids = set()

    for sport in sports_slugs:
        matches_url = f"{origin}/api/matches/{sport}"
        try:
            time.sleep(random.uniform(0.3, 0.6))
            res = scraper.get(matches_url, timeout=12)
            if res.status_code != 200:
                continue

            matches = res.json()
            if not isinstance(matches, list):
                continue

            for match in matches:
                match_id = match.get("id")
                if not match_id or match_id in seen_match_ids:
                    continue

                status = str(match.get("status", "")).lower()
                if status in ["ended", "finished", "final"]:
                    continue

                seen_match_ids.add(match_id)
                raw_title = match.get("title", "")
                clean_rivals = re.sub(r'\s+@\s+|\s+\|\s+', ' vs ', raw_title).strip()
                league_name = match.get("leagueName") or match.get("categoryName") or sport.replace("-", " ").title()

                sources = match.get("sources") or []
                if not sources:
                    watch_link = f"{origin}/watch/{match_id}"
                    all_live_matches.append(OrderedDict([
                        ("Id", str(len(all_live_matches) + 1)),
                        ("Rivels", clean_rivals),
                        ("Title", f"{league_name} (S-1)"),
                        ("Link", watch_link)
                    ]))
                    continue

                for s_idx, src in enumerate(sources, 1):
                    time.sleep(random.uniform(0.2, 0.4))
                    direct_stream = resolve_stream_url(scraper, origin, src)

                    if direct_stream:
                        if ".m3u8" in direct_stream:
                            final_link = f"{direct_stream.split('?')[0].rstrip('/')}|Referer={origin}"
                        else:
                            final_link = direct_stream
                    else:
                        final_link = f"{origin}/watch/{match_id}"

                    all_live_matches.append(OrderedDict([
                        ("Id", str(len(all_live_matches) + 1)),
                        ("Rivels", clean_rivals),
                        ("Title", f"{league_name} (S-{s_idx})"),
                        ("Link", final_link)
                    ]))
        except Exception:
            continue

    if not all_live_matches:
        try:
            cards_res = scraper.get(f"{origin}/game-cards/embed", timeout=15)
            next_chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', cards_res.text)
            for chunk in next_chunks:
                clean_chunk = chunk.replace('\\"', '"').replace('\\\\', '\\')
                event_matches = re.findall(r'\{"id":"(event-[^"]+)","title":"([^"]+)".*?"leagueName":"([^"]+)"', clean_chunk)
                for eid, etitle, eleague in event_matches:
                    if eid not in seen_match_ids:
                        seen_match_ids.add(eid)
                        clean_rivals = re.sub(r'\s+@\s+|\s+\|\s+', ' vs ', etitle).strip()
                        all_live_matches.append(OrderedDict([
                            ("Id", str(len(all_live_matches) + 1)),
                            ("Rivels", clean_rivals),
                            ("Title", f"{eleague} (S-1)"),
                            ("Link", f"{origin}/watch/{eid}")
                        ]))
        except Exception:
            pass

    log_to_console(f"[+] Total live streams collected: {len(all_live_matches)}")

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
