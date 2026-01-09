from flask import Flask, render_template, jsonify
import json
import os
import requests
import time
import yfinance as yf
from datetime import datetime

app = Flask(__name__)

# Config: Try local secrets first, then Env Vars (for Vercel)
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

# Fallback to Environment Variables if not found locally
if not API_KEY:
    API_KEY = os.environ.get("FINNHUB_KEY")
if not TELEGRAM_BOT_TOKEN:
    TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
if not TELEGRAM_CHAT_ID:
    TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

HOLDINGS_FILE = "all_holdings.json"
DASHBOARD_FILE = "dashboard_data.json" # Local fallback
CACHE_FILE = "/tmp/dashboard_cache.json" # For Vercel ephemeral storage

# Cache and State
CACHE_DURATION = 60
memory_cache = {
    "last_updated": 0,
    "data": None
}
# Alert State (In-Memory: Will reset if Vercel cold-boots, but persists on warm instances)
cef_alert_states = {} 

def send_telegram_message(message):
    try:
        if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
            print("Telegram keys missing")
            return False
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
        requests.post(url, json=payload, timeout=5)
        return True
    except Exception as e:
        print(f"Telegram Err: {e}")
        return False

def get_holdings():
    # Load holdings
    try:
        if os.path.exists(HOLDINGS_FILE):
            with open(HOLDINGS_FILE, 'r') as f:
                return json.load(f)
    except:
        pass
    return {}

def fetch_yahoo_snapshot(symbols):
    try:
        tickers_str = " ".join(symbols)
        # Use simple download
        data = yf.download(tickers_str, period="5d", interval="1d", progress=False, threads=True)
        results = {}
        is_multi = len(symbols) > 1
        
        for sym in symbols:
            try:
                if is_multi:
                    if 'Close' in data and sym in data['Close']:
                        closes = data['Close'][sym].dropna()
                    else:
                        continue
                else:
                    closes = data['Close'].dropna()
                
                if len(closes) >= 2:
                    last_price = float(closes.iloc[-1])
                    prev_close = float(closes.iloc[-2])
                    if prev_close > 0:
                        change_pct = ((last_price - prev_close) / prev_close) * 100
                        results[sym] = {
                            "price": last_price,
                            "change_percent": change_pct,
                            "source": "YAHOO"
                        }
            except:
                continue
        return results
    except Exception as e:
        print(f"Yahoo Err: {e}")
        return {}

def generate_data():
    global cef_alert_states
    all_cefs = get_holdings()
    if not all_cefs:
        return {"error": "Holdings not found"}

    # Initialize alert states if empty
    if not cef_alert_states:
        cef_alert_states = {cef: 0 for cef in all_cefs.keys()}

    unique_symbols = set()
    for holdings in all_cefs.values():
        for stock in holdings:
            if "_PVT" not in stock['symbol']:
                unique_symbols.add(stock['symbol'])
    
    # Fetch Data
    price_db = fetch_yahoo_snapshot(list(unique_symbols))
    
    # Build Response
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
            chg = p_data.get('change_percent')
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
            
        # --- ALERT LOGIC ---
        current_abs_change = abs(total_weighted_change)
        alert_level = 0
        emoji = ""
        
        if current_abs_change >= 1.0:
            alert_level = 2
            emoji = "🚨🚨"
        elif current_abs_change >= 0.5:
            alert_level = 1
            emoji = "⚠️"
        
        # Safe access to state
        last_lvl = cef_alert_states.get(cef_name, 0)
        
        if alert_level > last_lvl:
            direction = "UP" if total_weighted_change > 0 else "DOWN"
            alert_msg = (
                f"{emoji} {cef_name} NAV Alert\n"
                f"Implied NAV: {direction} {total_weighted_change:+.2f}%\n"
                f"Driven by {total_weight_tracked:.1f}% reported holdings."
            )
            print(f"ALERT TRIGGERED: {cef_name}")
            send_telegram_message(alert_msg)
            cef_alert_states[cef_name] = alert_level
        # No reset logic to check 'once per day' (imperfect on Vercel but best effort)

        dashboard_data["cefs"].append({
            "name": cef_name,
            "implied_move": round(total_weighted_change, 3),
            "tracked_weight": round(total_weight_tracked, 1),
            "status": "UP" if total_weighted_change >= 0 else "DOWN",
            "holdings": detailed_holdings
        })
    
    return dashboard_data

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/cron')
def cron_check():
    """Endpoint for Periodic Monitoring Service (e.g. Cron-job.org)"""
    # Force regeneration to check alerts
    data = generate_data()
    now = time.time()
    if "error" not in data:
        memory_cache['data'] = data
        memory_cache['last_updated'] = now
    
    return jsonify({"status": "checked", "time": data.get("last_updated")})

@app.route('/data')
def get_data():
    # 1. Try Memory Cache
    now = time.time()
    if memory_cache['data'] and (now - memory_cache['last_updated'] < CACHE_DURATION):
        return jsonify(memory_cache['data'])
    
    # 2. Try Generating Fresh Data (Yahoo)
    new_data = generate_data()
    if "error" not in new_data:
        memory_cache['data'] = new_data
        memory_cache['last_updated'] = now
    
    return jsonify(new_data)

@app.route('/send_telegram', methods=['POST'])
def trigger_telegram():
    # Trigger generation if needed to get fresh stats
    data = memory_cache['data']
    if not data:
        data = generate_data()
    
    if "error" in data:
         return jsonify({"success": False, "message": "No data available"})

    try:
        lines = ["📊 *Manual NAV Update*"]
        lines.append(f"_{data['last_updated']}_")
        lines.append("")
        for cef in data['cefs']:
            icon = "➖"
            if cef['implied_move'] > 0: icon = "🟢"
            if cef['implied_move'] < 0: icon = "🔴"
            lines.append(f"{icon} *{cef['name']}*: {cef['implied_move']:+.3f}%")
        
        message = "\n".join(lines)
        if send_telegram_message(message):
            return jsonify({"success": True, "message": "Notification sent!"})
        else:
            return jsonify({"success": False, "message": "Failed to send"})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

# Vercel requires 'app' to be exposed
if __name__ == '__main__':
    app.run(debug=True, port=5000)
