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
    change_match = re.search(
        r'(becoming|increasing|decreasing|diminishing)\s+'
        r'(?!.*?\d+\s+to\s+\d+\s+nm)'
        r'((?:N|S|E|W|NE|SE|SW|NW)+\s+)?.*?(?=\.|,)',
        text, re.IGNORECASE
    )
    if change_match:
        commentary = change_match.group(0)
        has_knots = re.search(r'\d+\s+kt', commentary, re.IGNORECASE)
        has_direction_change = re.search(
            r'(becoming|increasing|decreasing|diminishing)\s+(N|S|E|W|NE|SE|SW|NW)\b',
            commentary, re.IGNORECASE
        )
        if has_knots or has_direction_change:
            data['wind_commentary'] = commentary

    # --- 3. GUSTS ---
    gust_match = re.search(r'Gusts\s+up\s+to\s+(\d+\s+kt)', text, re.IGNORECASE)
    if gust_match:
        data['wind_gusts'] = gust_match.group(1)

    # --- 4. WAVE HEIGHT ---
    seas_match = re.search(r'(?:Seas|Waves)\s+(?:around|up\s+to)?\s*(\d+\s+to\s+\d+\s+ft|\d+\s+ft)', text, re.IGNORECASE)
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


def scrape_and_save_latlon(lat, lon, filename):
    """
    Fetches a marine zone forecast from marine.weather.gov using a lat/lon
    coordinate that falls within the desired zone. marine.weather.gov
    auto-detects the appropriate zone from the coordinate.

    Use this for ILM and CHS AMZ zones that return 400 when queried by
    zone ID (e.g. AMZ270-276, AMZ370-374). Pass an offshore coordinate
    ~30nm from the port — inside the 20-40nm or 20-60nm zone.

    This is the same underlying approach as the mid-atlantic MHX pixel-coord
    entries (Oregon Inlet, Hatteras, Beaufort Inlet), just using lat/lon
    instead of map pixel coordinates.
    """
    url = f"https://marine.weather.gov/MapClick.php?lat={lat}&lon={lon}&unit=0&lg=english&FcstType=text"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    run_date = datetime.now()

    try:
        print(f"Fetching marine forecast at ({lat},{lon}) for {filename}...")
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.content, 'html.parser')

        final_data = {
            "timestamp": run_date.strftime("%Y-%m-%d %H:%M:%S"),
            "forecasts": []
        }

        forecast_container = soup.find('div', id='detailed-forecast')

        if forecast_container:
            rows = forecast_container.find_all('div', class_='row-forecast')

            for row in rows:
                period_div = row.find('div', class_='forecast-label')
                desc_div = row.find('div', class_='forecast-text')

                if period_div and desc_div:
                    raw_text = desc_div.get_text(strip=True)
                    original_period_name = period_div.get_text(strip=True)
                    formatted_period = get_forecast_date(original_period_name, run_date)
                    parsed_info = parse_marine_forecast(raw_text)
                    parsed_info['period'] = formatted_period
                    final_data['forecasts'].append(parsed_info)

        if not final_data['forecasts']:
            print(f"Warning: No forecast data found for {filename}. Check lat/lon falls in a valid marine zone.")

        with open(filename, 'w') as f:
            json.dump(final_data, f, indent=4)

        print(f"Success! Saved to {filename}")

    except Exception as e:
        print(f"Error fetching ({lat},{lon}) for {filename}: {e}")



def scrape_and_save(url, filename):
    """
    Performs the actual scrape and saves to the specified JSON file.
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
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

        forecast_container = soup.find('div', id='detailed-forecast')

        if forecast_container:
            rows = forecast_container.find_all('div', class_='row-forecast')

            for row in rows:
                period_div = row.find('div', class_='forecast-label')
                desc_div = row.find('div', class_='forecast-text')

                if period_div and desc_div:
                    raw_text = desc_div.get_text(strip=True)
                    original_period_name = period_div.get_text(strip=True)

                    formatted_period = get_forecast_date(original_period_name, run_date)
                    parsed_info = parse_marine_forecast(raw_text)
                    parsed_info['period'] = formatted_period

                    final_data['forecasts'].append(parsed_info)

        if not final_data['forecasts']:
            print(f"Warning: No forecast data found for {filename}. Check if the CSS selectors need updating.")

        with open(filename, 'w') as f:
            json.dump(final_data, f, indent=4)

        print(f"Success! Saved to {filename}")

    except Exception as e:
        print(f"Error scraping {url}: {e}")


def main():
    # 1. Oregon Inlet (NWS office: MHX — Newport/Morehead City NC)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?x=348&y=111&site=mhx&zmx=&zmy=&map_x=348&map_y=111",
        'weather_data.json'
    )
    # 2. Hatteras NC (NWS office: MHX)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?x=306&y=181&site=mhx&zmx=&zmy=&map_x=306&map_y=181",
        'hatterasncnoaa.json'
    )
    # 3. Beaufort Inlet (NWS office: MHX)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?x=195&y=256&site=mhx&zmx=&zmy=&map_x=194&map_y=256",
        'beaufortinletnoaa.json'
    )
    # 4. Virginia Beach (NWS office: AKQ — Wakefield VA)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?x=265&y=174&site=akq&zmx=&zmy=&map_x=264&map_y=173",
        'virginiabeachnoaa.json'
    )
    # 5. Poquoson, VA — ANZ632 Chesapeake Bay New Point Comfort to Little Creek
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=ANZ632",
        'poquosonnoaa.json'
    )
    # 6. Bay Bridge Tunnel, VA — ANZ634 Chesapeake Bay Little Creek to Cape Henry
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=ANZ634",
        'baybridgetunnelnoaa.json'
    )
    # 7. Ocean City, MD — ANZ485 Cape May NJ to Fenwick Island DE 20-60 NM
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=ANZ485",
        'oceancitynoaa.json'
    )
    # 8. Horn Harbor, VA — ANZ631 Chesapeake Bay Sandy Point to Windmill Point
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=ANZ631",
        'hornharbornoaa.json'
    )
    # 9. Cape Charles, VA — ANZ631 Chesapeake Bay Sandy Point to Windmill Point
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=ANZ631",
        'capecharlesnoaa.json'
    )


    # ── GA/SC Region ─────────────────────────────────────────────────────────
    # ILM outer zones (AMZ270-276) and CHS outer zones (AMZ370-374) return
    # 400 when queried by zone ID from marine.weather.gov/MapClick.php, and
    # 404 from api.weather.gov/zones/forecast/. Use scrape_and_save_latlon()
    # instead: pass an offshore coordinate ~30nm from the port (inside the
    # zone) and marine.weather.gov auto-detects the correct zone.
    # JAX zones (AMZ470, AMZ452, AMZ454) work via zone ID — kept as-is.
    #
    # Offshore coordinate rationale: ~30nm east of the coast (midpoint of
    # the 20-40nm zone), or ~40nm for 20-60nm zones. Adjust if zone
    # boundary changes cause wrong zone detection.
    scrape_and_save_latlon(34.1, -76.9, 'wrightsvillebeachnc_noaa.json')   # Wrightsville Beach NC — AMZ270 Surf City-Cape Fear 20-40nm
    scrape_and_save_latlon(34.1, -76.9, 'carolinabeachnc_noaa.json')       # Carolina Beach NC — same zone as Wrightsville
    scrape_and_save_latlon(33.7, -77.6, 'southportnc_noaa.json')           # Southport NC — AMZ272 Cape Fear-Little River 20-40nm
    scrape_and_save_latlon(33.5, -78.2, 'littleriversc_noaa.json')         # Little River Inlet SC — AMZ274 Little River-Murrells 20-40nm
    scrape_and_save_latlon(33.5, -78.2, 'myrtlebeachsc_noaa.json')         # Myrtle Beach SC — same zone as Little River
    scrape_and_save_latlon(33.1, -78.7, 'murrellsinletsc_noaa.json')       # Murrells Inlet SC — AMZ276 Murrells-S Santee 20-40nm
    scrape_and_save_latlon(33.1, -78.7, 'georgetownsc_noaa.json')          # Georgetown SC — same zone as Murrells Inlet
    scrape_and_save_latlon(32.7, -79.2, 'charlestonsc_noaa.json')          # Charleston SC — AMZ370 S Santee-Edisto 20-40nm
    scrape_and_save_latlon(32.1, -80.0, 'beaufortsc_noaa.json')            # Beaufort SC — AMZ372 Edisto-Savannah 20-40nm
    scrape_and_save_latlon(32.1, -80.0, 'hiltonheadsc_noaa.json')          # Hilton Head SC — same zone as Beaufort
    scrape_and_save_latlon(31.7, -80.3, 'tybeega_noaa.json')               # Tybee Island GA — AMZ374 Savannah-Altamaha 20-60nm
    scrape_and_save_latlon(31.7, -80.3, 'darienga_noaa.json')              # Darien GA — same zone as Tybee
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ470", 'stsimonsgaga_noaa.json')  # St. Simons Island GA — 20-60nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ470", 'jekyllga_noaa.json')      # Jekyll Island GA — 20-60nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ452", 'fernandinafl_noaa.json')  # Fernandina Beach FL — out 20nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ452", 'mayportfl_noaa.json')     # Mayport FL — out 20nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ454", 'staugustinefl_noaa.json') # St. Augustine FL — out 20nm


if __name__ == "__main__":
    main()
