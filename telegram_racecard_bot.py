# racecard_bot_full.py
import os
import csv
import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from telegram import InputFile, Update
from telegram.ext import Updater, CommandHandler, CallbackContext

# ----------------------------
# CONFIG
# ----------------------------
BOT_TOKEN = "8093787434:AAHOhybQgLcPAghmZd0MgsrraYBcVRZBymU"  # <- replace if needed
ALLOWED_USER_ID = None  # set to your numeric Telegram ID to restrict access, or None to allow anyone
VENUES = [1, 2, 4, 5, 6, 8]  # Change / extend as needed
DAYS_AHEAD = 5  # today + next (DAYS_AHEAD-1) days to check
OUTPUT_DIR = "racecards"

# ----------------------------
# HELPERS / SCRAPER
# ----------------------------
def safe_filename(s: str) -> str:
    s = s.replace("/", "-").replace(":", "-")
    s = re.sub(r"[<>\"\\|?*]", "", s)
    s = s.strip()
    return s

def capitalize_words(text: str) -> str:
    if not text:
        return ""
    return " ".join(w.capitalize() for w in text.strip().split())

def scrape_one_racecard(url: str, date_label: str):
    """Scrape a single racecard URL and write CSV if data present. Returns filename or None."""
    print(f"🔍 Fetching: {url}")
    try:
        resp = requests.get(url, timeout=15)
    except Exception as e:
        print(f"⚠️ Request error for {url}: {e}")
        return None

    if resp.status_code != 200:
        print(f"⚠️ HTTP {resp.status_code} for {url}")
        return None

    text = resp.text
    # quick early skip if page indicates no races
    if re.search(r"No\s+Races|No races scheduled|No Race Card", text, re.I):
        print("⚠️ Page indicates no races.")
        return None

    soup = BeautifulSoup(text, "html.parser")

    header = soup.select_one(".home.headline_home h3.border_bottom")
    if header:
        header_text = header.get_text(strip=True)
        m = re.search(r"Race Card\s*-\s*(.+?)\s*-\s*(\d{2}\s\w+\s\d{4})", header_text, re.I)
        if m:
            race_location = m.group(1).strip()
            race_date = m.group(2).strip()
        else:
            # fallback: split by '-' if possible
            parts = [p.strip() for p in header_text.split("-")]
            race_location = parts[1] if len(parts) > 1 else "Unknown"
            race_date = parts[2] if len(parts) > 2 else date_label
    else:
        race_location = "Unknown"
        race_date = date_label

    filename = f"{safe_filename(race_location)} Race Card {safe_filename(race_date)}.csv"
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    filepath = os.path.join(OUTPUT_DIR, filename)
    print(f"📁 Preparing file: {filepath}")

    races = soup.select(".race-card-new")
    if not races:
        print("⚠️ No .race-card-new elements found.")
        return None

    # page-level country/ground if present
    country_el = soup.select_one(".race-country")
    ground_el = soup.select_one(".race-ground")
    country_text = country_el.get_text(strip=True) if country_el else ""
    ground_text = ground_el.get_text(strip=True) if ground_el else ""

    rows_out = [["Race", "Country", "Ground", "Time", "Horse Number", "Horse Name", "HR NAME",
                 "Horse Jockey", "Horse Trainer", "Horse Age", "Horse Draw"]]

    for i, race in enumerate(races, start=1):
        race_no = i
        # try to find a time element specific to this race
        time_el = soup.select_one(f"#race-{i} h4:nth-child(2)")
        race_time = time_el.get_text(strip=True) if time_el else ""

        # For this race, show the time only on the first horse row
        first_row = True

        horse_rows = race.select("tr.dividend_tr")
        if not horse_rows:
            # if structure differs, try table rows inside race
            horse_rows = race.select("tr")

        for hr in horse_rows:
            # get columns (safe)
            cols = hr.find_all("td")
            # sometimes there are header rows etc.
            if len(cols) < 3:
                continue

            no_text = cols[0].get_text(strip=True)
            # horse number (strip draw)
            horse_number = re.sub(r"\(\d+\)", "", no_text).strip()
            draw_m = re.search(r"\((\d+)\)", no_text)
            draw = draw_m.group(1) if draw_m else ""

            # horse name in 3rd column, within h5 a
            horse_el = None
            try:
                horse_el = cols[2].select_one("h5 a")
            except Exception:
                horse_el = None
            horse_name = capitalize_words(horse_el.get_text(strip=True)) if horse_el else capitalize_words(cols[2].get_text(strip=True))

            # age
            age = ""
            if len(cols) >= 4:
                age_m = re.search(r"\d+", cols[3].get_text(strip=True))
                age = age_m.group(0) if age_m else ""

            trainer = cols[5].get_text(strip=True) if len(cols) >= 6 else ""
            jockey = cols[6].get_text(strip=True) if len(cols) >= 7 else ""

            time_to_show = race_time if first_row else ""
            first_row = False

            rows_out.append([race_no, country_text, ground_text, time_to_show,
                             horse_number, horse_name, "", jockey, trainer, age, draw])

        # blank line between races
        rows_out.append([""] * 11)

    # Write CSV
    try:
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerows(rows_out)
        print(f"✅ Saved CSV: {filepath}")
        return filepath
    except Exception as e:
        print(f"❌ Could not write CSV {filepath}: {e}")
        return None

def scrape_race_cards_for_venues(venues, days_ahead=DAYS_AHEAD):
    base = "https://www.indiarace.com/Home/racingCenterEvent?venueId={venue}&event_date={date}&race_type=RACECARD"
    today = datetime.now().date()
    saved_files = []
    for delta in range(days_ahead):
        d = today + timedelta(days=delta)
        date_label = d.strftime("%d %b %Y")
        date_param = d.strftime("%Y-%m-%d")
        for v in venues:
            url = base.format(venue=v, date=date_param)
            try:
                saved = scrape_one_racecard(url, date_label)
                if saved:
                    saved_files.append(saved)
            except Exception as e:
                print(f"⚠️ Error scraping {url}: {e}")
    return saved_files

# ----------------------------
# TELEGRAM HANDLERS
# ----------------------------
def start(update: Update, context: CallbackContext):
    update.message.reply_text("👋 Welcome to Horse Race Bot!\nUse /fetch to download available race cards (today + next days).")

def fetch(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        update.message.reply_text("⛔ You are not authorized to use this bot.")
        return

    update.message.reply_text("🏇 Checking for available race cards... please wait ⏳")
    try:
        files = scrape_race_cards_for_venues(VENUES, DAYS_AHEAD)
        if not files:
            update.message.reply_text("❌ No available race cards found for upcoming days.")
            return

        # sort by mtime ascending so older first (or reverse True for newest first)
        files.sort(key=lambda p: os.path.getmtime(p))
        for fpath in files:
            fname = os.path.basename(fpath)
            update.message.reply_text(f"📤 Sending: {fname}")
            with open(fpath, "rb") as fh:
                context.bot.send_document(chat_id=update.effective_chat.id, document=fh, filename=fname)

        update.message.reply_text("✅ All available race cards sent.")
    except Exception as e:
        update.message.reply_text(f"❌ Unexpected error: {e}")

# ----------------------------
# MAIN
# ----------------------------
def main():
    print("🚀 Starting RaceCard Telegram bot...")
    updater = Updater(BOT_TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("fetch", fetch))
    print("🤖 Bot running. Send /fetch to fetch race cards.")
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()
