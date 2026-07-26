import os
import re
import sys
import json
import time
import random
import shutil
from datetime import datetime, timezone, timedelta
from collections import OrderedDict
from urllib.parse import urlparse, urljoin
import cloudscraper
from bs4 import BeautifulSoup

BASE_URL = os.getenv("BASE_URL", "https://streame.center/").strip()
OUTPUT_FILE = "streams-center.json"

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

def extract_stream_token(scraper, player_url):
    try:
        res = scraper.get(player_url, timeout=10)
        res.encoding = 'utf-8'
        html = res.text
        
        stream_ids = re.findall(r'(?:stream=|hls\.php\?stream=)([a-zA-Z0-9_.-]+)', html)
        if stream_ids:
            return deduplicate(stream_ids)
            
        iframe_matches = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for iframe_url in iframe_matches:
            if not iframe_url.startswith('http'):
                if iframe_url.startswith('//'):
                    iframe_url = 'https:' + iframe_url
                else:
                    iframe_url = urljoin(player_url, iframe_url)
            
            time.sleep(random.uniform(0.3, 0.5))
            iframe_res = scraper.get(iframe_url, timeout=10)
            iframe_res.encoding = 'utf-8'
            iframe_html = iframe_res.text
            
            inner_stream_ids = re.findall(r'(?:stream=|hls\.php\?stream=)([a-zA-Z0-9_.-]+)', iframe_html)
            if inner_stream_ids:
                return deduplicate(inner_stream_ids)
                
    except Exception as e:
        log_to_console(f"    [!] Error during token extraction from {player_url}: {str(e)}")
    return []

def run_scraper():
    target_url = BASE_URL
    if not target_url.startswith("http"):
        target_url = "https://" + target_url

    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'android', 'desktop': False})
    
    log_to_console(f"[*] Loading homepage HTML: {target_url}")
    try:
        res = scraper.get(target_url, timeout=15)
        res.encoding = 'utf-8'
        homepage_html = res.text
        log_to_console("[+] Homepage loaded successfully.")
    except Exception as e:
        error_package = OrderedDict([
            ("Owner", "Ivan-FluX"),
            ("App name", "streams-center"),
            ("Status", "Failed"),
            ("Error", "Could not connect to the website."),
            ("Details", str(e))
        ])
        print(json.dumps(error_package, indent=4, ensure_ascii=False))
        return

    soup = BeautifulSoup(homepage_html, 'html.parser')
    categories = soup.find_all('div', class_='tg-cat')
    
    raw_matches = []

    for cat in categories:
        cat_head = cat.find('div', class_='tg-cat-head')
        cat_name = "General"
        if cat_head and cat_head.find('h2'):
            cat_name = cat_head.find('h2').get_text(strip=True)

        games = cat.find_all('div', class_='tg-game')
        for game in games:
            title_elem = game.find('div', class_='tg-title')
            if not title_elem:
                continue
            
            match_title = title_elem.get_text(strip=True)
            clean_rivals = re.sub(r'\s+@\s+|\s+\|\s+', ' vs ', match_title)

            player_urls = []
            links_div = game.find('div', class_='tg-links')
            if links_div:
                for a_tag in links_div.find_all('a', href=True):
                    href = a_tag['href'].strip()
                    if href and href != '#':
                        player_urls.append(urljoin(target_url, href))
                
                for btn in links_div.find_all('button', attrs={'data-link': True}):
                    data_link = btn['data-link'].strip()
                    if data_link:
                        player_urls.append(urljoin(target_url, data_link))

            player_urls = deduplicate(player_urls)
            if player_urls:
                raw_matches.append({
                    "cat_name": cat_name,
                    "clean_rivals": clean_rivals,
                    "player_urls": player_urls
                })

    log_to_console(f"[+] Found {len(raw_matches)} total scheduled matches in HTML.")

    all_live_matches = []
    
    for item in raw_matches:
        log_to_console(f"[*] Match: {item['clean_rivals']}...")
        
        for s_idx, p_url in enumerate(item["player_urls"], 1):
            time.sleep(random.uniform(0.3, 0.6))
            
            tokens = extract_stream_token(scraper, p_url)
            
            if tokens:
                for token in tokens:
                    final_link = f"https://edgestream2.pro/hls/{token}.m3u8|Referer=https://streame.center"
                    log_to_console(f"      >>> [SUCCESS] Link Created (S-{s_idx}): {final_link}")
                    
                    all_live_matches.append(OrderedDict([
                        ("Id", str(len(all_live_matches) + 1)),
                        ("Rivels", item["clean_rivals"]),
                        ("Title", f"{item['cat_name']} (S-{s_idx})"),
                        ("Link", final_link)
                    ]))
            else:
                log_to_console(f"      >>> [FAILED] Could not find stream token from {p_url}")

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
