import requests
from bs4 import BeautifulSoup
import json
import re  # <--- Added for text pattern matching
from datetime import datetime

def parse_marine_forecast(text):
    """
    Parses a NOAA text block into structured data.
    Example Input: "SE winds 20 to 25 kt, becoming W 25 to 30 kt... Seas 5 to 7 ft..."
    """
    data = {}
    text = text.replace('\n', ' ').strip()
    data['raw_text'] = text # Keep original text just in case

    # 1. EXTRACT WIND (Direction & Speed)
    # Looks for: "SE winds 20 to 25 kt" or "N winds 15 kt"
    wind_match = re.search(r'([N|S|E|W|NE|SE|SW|NW]+)\s+winds?\s+(\d+\s+to\s+\d+\s+kt|\d+\s+kt)', text, re.IGNORECASE)
    if wind_match:
        data['wind_direction'] = wind_match.group(1)
        data['wind_speed'] = wind_match.group(2)

    # 2. EXTRACT WIND COMMENTARY (Changes)
    # Looks for: "becoming [Direction] [Speed]..." or "increasing to..."
    # We capture the phrase until the next period or comma
    change_match = re.search(r'(becoming|increasing|decreasing)\s+([N|S|E|W|NE|SE|SW|NW]+\s+)?.*?(?=\.|,)', text, re.IGNORECASE)
    if change_match:
        data['wind_commentary'] = change_match.group(0)

    # 3. EXTRACT GUSTS
    # Looks for: "Gusts up to 35 kt"
    gust_match = re.search(r'Gusts\s+up\s+to\s+(\d+\s+kt)', text, re.IGNORECASE)
    if gust_match:
        data['wind_gusts'] = gust_match.group(1)

    # 4. EXTRACT WAVE HEIGHT
    # Looks for: "Seas 5 to 7 ft"
    seas_match = re.search(r'Seas\s+(\d+\s+to\s+\d+\s+ft|\d+\s+ft)', text, re.IGNORECASE)
    if seas_match:
        data['wave_height'] = seas_match.group(1)

    # 5. EXTRACT WAVE COMMENTARY (Building/Subsiding)
    # Looks for: "building to 7 to 10 ft"
    wave_change_match = re.search(r'(building|subsiding)\s+to\s+(\d+\s+to\s+\d+\s+ft|\d+\s+ft)', text, re.IGNORECASE)
    if wave_change_match:
        data['wave_commentary'] = wave_change_match.group(0)

    # 6. EXTRACT WAVE DETAIL (Period & Swell)
    # Looks for: "Wave detail: [everything here]"
    # It usually ends at the next sentence period.
    detail_match = re.search(r'Wave detail:\s+(.*?)(?=\.|$)', text, re.IGNORECASE)
    if detail_match:
        data['wave_detail_string'] = detail_match.group(1)
        
        # Bonus: Try to extract primary period from detail string
        # Looks for "at 7 seconds"
        period_match = re.search(r'at\s+(\d+\s+seconds?)', detail_match.group(1), re.IGNORECASE)
        if period_match:
            data['primary_wave_period'] = period_match.group(1)

    return data

def scrape_weather():
    url = "https://forecast.weather.gov/MapClick.php?x=348&y=111&site=mhx&zmx=&zmy=&map_x=348&map_y=111"
    headers = {'User-Agent': 'Mozilla/5.0 (MyMarineScraper/1.0)'}

    try:
        print(f"Fetching data from {url}...")
        response = requests.get(url, headers=headers)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        
        final_data = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "forecasts": []
        }

        # Find the detailed forecast text blocks
        forecast_div = soup.find('div', id='detailed-forecast-body')
        
        if forecast_div:
            rows = forecast_div.find_all('div', class_='row-forecast')
            for row in rows:
                period_div = row.find('div', class_='forecast-label')
                desc_div = row.find('div', class_='forecast-text')
                
                if period_div and desc_div:
                    raw_text = desc_div.text.strip()
                    period_name = period_div.text.strip()
                    
                    # RUN THE PARSER
                    parsed_info = parse_marine_forecast(raw_text)
                    parsed_info['period'] = period_name
                    
                    final_data['forecasts'].append(parsed_info)

        # Save to JSON
        filename = 'weather_data.json'
        with open(filename, 'w') as f:
            json.dump(final_data, f, indent=4)
        
        print(f"Success! Parsed {len(final_data['forecasts'])} forecast periods.")
        # Debug print the first one to verify
        if final_data['forecasts']:
            print("Sample Output:", json.dumps(final_data['forecasts'][0], indent=2))

    except Exception as e:
        print(f"Error scraping data: {e}")
        exit(1)

if __name__ == "__main__":
    scrape_weather()
