import requests

TOKEN = "8432167062:AAHjB9sNdbC5QEoN-LzRAu_MEpMH0umvhwo"
CHAT_ID = "-1003568639742"

def test_telegram():
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": "🔍 Test Message from Debugger"
    }
    
    print(f"Sending to {CHAT_ID}...")
    try:
        response = requests.post(url, json=payload, timeout=10)
        print(f"Status Code: {response.status_code}")
        print(f"Response Body: {response.text}")
    except Exception as e:
        print(f"Exception: {e}")

if __name__ == "__main__":
    test_telegram()
