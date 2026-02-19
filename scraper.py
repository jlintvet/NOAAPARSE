import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime, timedelta

def get_forecast_date(period_text, run_date):
    """
    Calculates the date based on the period name.
    """
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

    for day_name, index in day_mapping.items():
        if day_name in period_lower:
            target_day_index = index
            found_day = True
            break 
    
    if found_day:
        current_day_index = run_date.weekday()
        days_ahead = (target_day_index - current_day_index) % 7
        target_date = run_date + timedelta(days=days_ahead)
    
    date_str = target_date.strftime("%-m/%-d")
    return f"{period_text} {date_str}"

def parse_marine_forecast(text):
    data = {}
    text = text.replace('\n', ' ').strip()
    data['raw_text'] = text 

    # --- 1. WIND EXTRACTION ---
    wind_match = re.search(r'(N|S|E|W|NE|SE|SW|NW)\s+winds?\s+(?:around|up\s+to|increasing\s+to)?\s*(\d+\s+to\s+\d+\s+kt|\d+\s+kt)', text, re.IGNORECASE)
    if wind_match:
        data['wind_direction'] = wind_match.group(1)
        data['wind_speed'] = wind_match.group(2)

    # --- 2. WIND COMMENTARY ---
    change_match = re.search(r'(becoming|increasing|decreasing|diminishing)\s+((?:N|S|E|W|NE|SE|SW|NW)+\s+)?.*?(?=\.|,)', text, re.IGNORECASE)
    if change_match:
        data['wind_commentary'] = change_match.group(0)

    # --- 3. GUSTS ---
    gust_match = re.search(r'Gusts\s+up\s+to\s+(\d+\s+kt)', text, re.IGNORECASE)
    if gust_match:
        data['wind_gusts'] = gust_match.group(1)

    # --- 4. WAVE HEIGHT ---
    seas_match = re.search(r'Seas\s+(?:around|up\s+to)?\s*(\d+\s+to\s+\d+\s+ft|\d+\s+ft)', text, re.IGNORECASE)
    if seas_match:
        data['wave_height'] = seas_match.group(1)

    # --- 5. WAVE COMMENTARY ---
    wave_change_match = re.search(r'(building|subsiding)\s+to\s+(\d+\s+to\s+\d+\s+ft|\d+\s+ft)', text, re.IGNORECASE)
    if wave_change_match:
        data['wave_commentary'] = wave_change_match.group(0)

    # --- 6. WAVE DETAIL & COMPONENT PARSING ---
    detail_match = re.search(r'Wave detail:\s+(.*?)(?=\.|$)', text, re.IGNORECASE)
    
    if detail_match:
        full_detail_string = detail_match.group(1)
        data['wave_detail_string'] = full_detail_string
        
        component_pattern = r'(N|S|E|W|NE|SE|SW|NW)\s+(\d+\s+ft)\s+at\s+(\d+\s+seconds?)'
        components = re.findall(component_pattern, full_detail_string, re.IGNORECASE)
        
        if components:
            data['swell_components'] = []
            for comp in components:
                data['swell_components'].append({
                    "direction": comp[0],
                    "height": comp[1],
                    "period": comp[2]
                })

            data['primary_swell_direction'] = components[0][0]
            data['primary_wave_height'] = components[0][1]
            data['primary_wave_period'] = components[0][2]

    return data

def scrape_and_save(url, filename):
    """
    Performs the actual scrape and saves to the specified JSON file.
    """
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
                    
                    formatted_period = get_forecast_date(original_period_name, run_date)
                    parsed_info = parse_marine_forecast(raw_text)
                    parsed_info['period'] = formatted_period
                    
                    final_data['forecasts'].append(parsed_info)

        with open(filename, 'w') as f:
            json.dump(final_data, f, indent=4)
        
        print(f"Success! Saved to {filename}")

    except Exception as e:
        print(f"Error scraping {url}: {e}")

def main():
    # 1. Scrape Oregon Inlet (Original URL)
    oregon_inlet_url = "https://forecast.weather.gov/MapClick.php?x=348&y=111&site=mhx&zmx=&zmy=&map_x=348&map_y=111"
    scrape_and_save(oregon_inlet_url, 'weather_data.json')

    # 2. Scrape Hatteras NC (New URL)
    hatteras_url = "https://marine.weather.gov/MapClick.php?lon=-75.75892&lat=35.05471"
    scrape_and_save(hatteras_url, 'hatterasncnoaa.json')

if __name__ == "__main__":
    main()
