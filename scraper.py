import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime, timedelta

def get_forecast_date(period_text, run_date):
    """
    Calculates the date based on the period name.
    Handles full names ("Wednesday") and abbreviations ("Wed").
    """
    # 1. Map days to numbers (0=Monday, ... 6=Sunday)
    # This allows it to understand "Wed" is the same as "Wednesday"
    day_mapping = {
        "mon": 0, "monday": 0,
        "tue": 1, "tuesday": 1,
        "wed": 2, "wednesday": 2,
        "thu": 3, "thursday": 3,
        "fri": 4, "friday": 4,
        "sat": 5, "saturday": 5,
        "sun": 6, "sunday": 6
    }
    
    period_lower = period_text.lower()
    target_date = run_date
    found_day = False
    target_day_index = -1

    # 2. Check if any day name (or abbreviation) is in the text
    for day_name, index in day_mapping.items():
        if day_name in period_lower:
            target_day_index = index
            found_day = True
            break # Stop once we find the day
    
    # 3. Calculate the date difference
    if found_day:
        current_day_index = run_date.weekday()
        # Calculate how many days ahead this is (modulo 7 handles next week wrapping)
        days_ahead = (target_day_index - current_day_index) % 7
        target_date = run_date + timedelta(days=days_ahead)
    
    # 4. Format the date (e.g., "2/18")
    # Using %-m and %-d removes leading zeros (Linux/GitHub Actions standard)
    date_str = target_date.strftime("%-m/%-d")
    
    return f"{period_text} {date_str}"

def parse_marine_forecast(text):
    data = {}
    text = text.replace('\n', ' ').strip()
    data['raw_text'] = text 

    # --- REGEX PATTERNS ---
    
    # Wind
    wind_match = re.search(r'([N|S|E|W|NE|SE|SW|NW]+)\s+winds?\s+(\d+\s+to\s+\d+\s+kt|\d+\s+kt)', text, re.IGNORECASE)
    if wind_match:
        data['wind_direction'] = wind_match.group(1)
        data['wind_speed'] = wind_match.group(2)

    # Wind Change (Commentary)
    change_match = re.search(r'(becoming|increasing|decreasing)\s+([N|S|E|W|NE|SE|SW|NW]+\s+)?.*?(?=\.|,)', text, re.IGNORECASE)
    if change_match:
        data['wind_commentary'] = change_match.group(0)

    # Gusts
    gust_match = re.search(r'Gusts\s+up\s+to\s+(\d+\s+kt)', text, re.IGNORECASE)
    if gust_match:
        data['wind_gusts'] = gust_match.group(1)

    # Waves
    seas_match = re.search(r'Seas\s+(\d+\s+to\s+\d+\s+ft|\d+\s+ft)', text, re.IGNORECASE)
    if seas_match:
        data['wave_height'] = seas_match.group(1)

    # Wave Change (Commentary)
    wave_change_match = re.search(r'(building|subsiding)\s+to\s+(\d+\s+to\s+\d+\s+ft|\d+\s+ft)', text, re.IGNORECASE)
    if wave_change_match:
        data['wave_commentary'] = wave_change_match.group(0)

    # Wave Detail
    detail_match = re.search(r'Wave detail:\s+(.*?)(?=\.|$)', text, re.IGNORECASE)
    if detail_match:
        data['wave_detail_string'] = detail_match.group(1)
        # Extract period from detail string
        period_match = re.search(r'at\s+(\d+\s+seconds?)', detail_match.group(1), re.IGNORECASE)
        if period_match:
            data['primary_wave_period'] = period_match.group(1)

    return data

def scrape_weather():
    url = "https://forecast.weather.gov/MapClick.php?x=348&y=111&site=mhx&zmx=&zmy=&map_x=348&map_y=111"
    headers = {'User-Agent': 'Mozilla/5.0 (MyMarineScraper/1.0)'}
    
    run_date = datetime.now()

    try:
        print(f"Fetching data from {url}...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        
        final_data = {
            "timestamp": run_date.strftime("%Y-%m-%d %H:%M:%S"),
            "forecasts": []
        }

        forecast_div = soup.find('div', id='detailed-forecast-body')
        
        if forecast_div:
            rows = forecast_div.find_all('div', class_='row-forecast')
            for row in rows:
                period_div = row.find('div', class_='forecast-label')
                desc_div = row.find('div', class_='forecast-text')
                
                if period_div and desc_div:
                    raw_text = desc_div.text.strip()
                    original_period_name = period_div.text.strip()
                    
                    # --- CALCULATE DATE ---
                    formatted_period = get_forecast_date(original_period_name, run_date)
                    
                    # --- PARSE TEXT ---
                    parsed_info = parse_marine_forecast(raw_text)
                    parsed_info['period'] = formatted_period
                    
                    final_data['forecasts'].append(parsed_info)

        filename = 'weather_data.json'
        with open(filename, 'w') as f:
            json.dump(final_data, f, indent=4)
        
        print(f"Success! Saved to {filename}")

    except Exception as e:
        print(f"Error scraping data: {e}")
        exit(1)

if __name__ == "__main__":
    scrape_weather()
