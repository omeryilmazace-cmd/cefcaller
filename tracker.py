import requests
import json
import time
import os
import datetime
import yfinance as yf
from colorama import Fore, Style, init

# Initialize colorama
init(autoreset=True)

# Config: Try local secrets first, then Env Vars
API_KEY = None
TELEGRAM_BOT_TOKEN = None
TELEGRAM_CHAT_ID = None

try:
    from secrets_config import API_KEY as LOC_KEY, TELEGRAM_BOT_TOKEN as LOC_TOK, TELEGRAM_CHAT_ID as LOC_ID
    API_KEY = LOC_KEY
    TELEGRAM_BOT_TOKEN = LOC_TOK
    TELEGRAM_CHAT_ID = LOC_ID
except ImportError:
    pass

if not API_KEY:
    API_KEY = os.environ.get("FINNHUB_KEY")
if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_CHAT_ID:
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HOLDINGS_FILE = "all_holdings.json"
DASHBOARD_FILE = "dashboard_data.json"
REFERENCE_FILE = "reference.json"

def get_trt_date():
    """Get current date in TRT (UTC+3)."""
    return datetime.datetime.now().strftime("%Y-%m-%d")

def send_telegram_message(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass

def fetch_yahoo_snapshot(symbols):
    """
    Improved Yahoo Fetch:
    1. Fetches daily data to get the 'Reference Close' (Last Session).
    2. Fetches 1m data (including pre-market) to get the 'Live Price'.
    3. Calculates change % relative to that Reference Close.
    """
    print(f"{Fore.MAGENTA}Fetching Yahoo Real-time for {len(symbols)} tickers...")
    try:
        tickers_str = " ".join(symbols)
        
        # 1. Get Daily Data for the baseline (Last 5 days)
        # We need the most recent COMPLETED day's close.
        daily_df = yf.download(tickers_str, period="5d", interval="1d", progress=False, threads=True)
        
        # 2. Get Live Data (Pre-market included)
        # Period 1d, Interval 1m is the most up-to-date
        live_df = yf.download(tickers_str, period="1d", interval="1m", include_prepost=True, progress=False, threads=True)
        
        results = {}
        is_multi = len(symbols) > 1
        
        for sym in symbols:
            try:
                # --- Find Baseline (Prev Close) ---
                if is_multi:
                    if 'Close' not in daily_df or sym not in daily_df['Close']: continue
                    daily_series = daily_df['Close'][sym].dropna()
                else:
                    daily_series = daily_df['Close'].dropna()
                
                if daily_series.empty: continue
                
                # --- Find Live Price ---
                if is_multi:
                    if 'Close' not in live_df or sym not in live_df['Close']:
                        # If no live row for today yet, use daily's last row
                        live_price = float(daily_series.iloc[-1])
                        last_session_close = float(daily_series.iloc[-2]) if len(daily_series) > 1 else None
                    else:
                        live_series = live_df['Close'][sym].dropna()
                        if live_series.empty:
                            live_price = float(daily_series.iloc[-1])
                            last_session_close = float(daily_series.iloc[-2]) if len(daily_series) > 1 else None
                        else:
                            live_price = float(live_series.iloc[-1])
                            # In this case, daily_series[-1] IS the baseline (Friday's close)
                            last_session_close = float(daily_series.iloc[-1])
                else:
                    live_series = live_df['Close'].dropna()
                    if live_series.empty:
                        live_price = float(daily_series.iloc[-1])
                        last_session_close = float(daily_series.iloc[-2]) if len(daily_series) > 1 else None
                    else:
                        live_price = float(live_series.iloc[-1])
                        last_session_close = float(daily_series.iloc[-1])

                if last_session_close and last_session_close > 0:
                    change_pct = ((live_price - last_session_close) / last_session_close) * 100
                    results[sym] = {
                        "price": live_price,
                        "change_percent": change_pct,
                        "source": "YAHOO"
                    }
            except Exception:
                continue
                
        return results
    except Exception as e:
        print(f"{Fore.RED}Yahoo Live Error: {e}")
        return {}

def load_reference():
    if os.path.exists(REFERENCE_FILE):
        try:
            with open(REFERENCE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"date": "", "prices": {}}

def save_reference(ref_data):
    with open(REFERENCE_FILE, 'w') as f:
        json.dump(ref_data, f)

def export_dashboard(all_cefs, price_db, ref_prices, cef_alert_states):
    timestamp = time.strftime('%H:%M:%S')
    dashboard_data = {
        "last_updated": timestamp,
        "cefs": []
    }

    for cef_name, holdings in all_cefs.items():
        total_weighted_change = 0.0
        total_weight_tracked = 0.0
        
        detailed_holdings = []
        for stock in holdings:
            sym = stock['symbol']
            weight = stock['weight']
            
            p_data = price_db.get(sym, {})
            chg = p_data.get('change_percent') # Uses restored/fetched change directly
            src = p_data.get('source', '')
            
            calc_chg = chg if chg is not None else 0.0
            impact = (calc_chg * weight) / 100
            total_weighted_change += impact
            
            if chg is not None:
                total_weight_tracked += weight
            
            detailed_holdings.append({
                "symbol": sym,
                "weight": weight,
                "change": chg,
                "source": src
            })

        # Alert Logic
        current_abs_change = abs(total_weighted_change)
        alert_level = 0
        emoji = ""
        
        if current_abs_change >= 1.0:
            alert_level = 2
            emoji = "🚨🚨"
        elif current_abs_change >= 0.5:
            alert_level = 1
            emoji = "⚠️"
        
        last_lvl = cef_alert_states[cef_name]
        
        if alert_level > last_lvl:
            direction = "UP" if total_weighted_change > 0 else "DOWN"
            alert_msg = (
                f"{emoji} {cef_name} NAV Alert\n"
                f"Implied NAV: {direction} {total_weighted_change:+.2f}%\n"
                f"Driven by {total_weight_tracked:.1f}% reported holdings."
            )
            print(f"{Fore.YELLOW}>>> Sending Alert for {cef_name} <<<")
            send_telegram_message(alert_msg)
            cef_alert_states[cef_name] = alert_level
        # elif alert_level < last_lvl and alert_level == 0:
        #     cef_alert_states[cef_name] = 0

        dashboard_data["cefs"].append({
            "name": cef_name,
            "implied_move": round(total_weighted_change, 3),
            "tracked_weight": round(total_weight_tracked, 1),
            "status": "UP" if total_weighted_change >= 0 else "DOWN",
            "holdings": detailed_holdings
        })

    temp_file = DASHBOARD_FILE + ".tmp"
    with open(temp_file, 'w') as f:
        json.dump(dashboard_data, f)
    os.replace(temp_file, DASHBOARD_FILE)

def restore_state():
    """Attempt to restore price_db from existing dashboard data."""
    restored_db = {}
    if os.path.exists(DASHBOARD_FILE):
        try:
            with open(DASHBOARD_FILE, 'r') as f:
                data = json.load(f)
                count = 0
                for cef in data.get('cefs', []):
                    for h in cef.get('holdings', []):
                        sym = h.get('symbol')
                        chg = h.get('change')
                        src = h.get('source')
                        if sym and chg is not None:
                            restored_db[sym] = {
                                "change_percent": chg,
                                "source": src,
                                "price": None, 
                                "prev_close": None
                            }
                            count += 1
                print(f"{Fore.GREEN}Restored {count} symbols from previous state.")
        except Exception as e:
            print(f"{Fore.RED}Failed to restore state: {e}")
    return restored_db

def main():
    if not os.path.exists(HOLDINGS_FILE):
        print(f"{Fore.RED}Holdings file not found!")
        return

    with open(HOLDINGS_FILE, 'r') as f:
        all_cefs = json.load(f)

    unique_symbols = set()
    for holdings in all_cefs.values():
        for stock in holdings:
            if "_PVT" not in stock['symbol']:
                unique_symbols.add(stock['symbol'])
    unique_list = list(unique_symbols)
    
    cef_alert_states = {cef: 0 for cef in all_cefs.keys()}
    
    # Load Initial Reference
    ref_data = load_reference()
    
    # Restore State
    price_db = restore_state()
    export_dashboard(all_cefs, price_db, ref_data['prices'], cef_alert_states)

    while True:
        today_str = get_trt_date()
        timestamp = time.strftime('%H:%M:%S')
        
        # --- MIDNIGHT RESET LOGIC ---
        if ref_data["date"] != today_str:
            print(f"{Fore.MAGENTA}>>> NEW DAY DETECTED ({today_str}) - RESETTING BASELINES <<<")
            ref_data = {"date": today_str, "prices": {}}
            price_db = {} 
            cef_alert_states = {cef: 0 for cef in all_cefs.keys()}
            save_reference(ref_data)

        print(f"\n{Fore.CYAN}--- Yahoo Cycle {timestamp} ---{Style.RESET_ALL}")

        # --- YAHOO BATCH FETCH ---
        yahoo_data = fetch_yahoo_snapshot(unique_list)
        if yahoo_data:
            print(f"Yahoo: Received {len(yahoo_data)} updates.")
            price_db.update(yahoo_data)
            export_dashboard(all_cefs, price_db, ref_data['prices'], cef_alert_states)
        else:
            print("Yahoo: No data returned. Keeping old values.")

        print("Cycle complete. Waiting 60s...")
        time.sleep(60)

if __name__ == "__main__":
    main()
