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
    # Compass direction — full words (Northeast) and abbreviations (NE, NNE, etc.)
    _DIR = r'(?:North(?:east|west)?|South(?:east|west)?|East|West|NNE|NE|ENE|ESE|SE|SSE|SSW|SW|WSW|WNW|NW|NNW|N|S|E|W)'
    _SPEED = r'(?:\d+\s+to\s+\d+\s+(?:kt|knots?)|\d+\s+(?:kt|knots?))'
    _HEIGHT = r'(?:\d+\s+to\s+\d+\s+(?:ft|feet|foot)|\d+\s+(?:ft|feet|foot))'
    _DIR_NORM = {'northeast':'NE','northwest':'NW','southeast':'SE','southwest':'SW','north':'N','south':'S','east':'E','west':'W'}
    wind_match = re.search(rf'({_DIR})\s+winds?\s+(?:around\s+|up\s+to\s+|increasing\s+to\s+)?({_SPEED})', text, re.IGNORECASE)
    if wind_match:
        raw_dir = wind_match.group(1)
        data['wind_direction'] = _DIR_NORM.get(raw_dir.lower(), raw_dir.upper())
        data['wind_speed'] = wind_match.group(2)

    # --- 2. WIND COMMENTARY ---
    # Only search before "Wave detail:" -- that clause describes swell
    # direction/height changes (also using becoming/increasing), not wind,
    # and searching the full text lets it steal a swell "becoming" clause.
    _wind_commentary_text = re.split(r'Wave detail:', text, maxsplit=1, flags=re.IGNORECASE)[0]
    change_match = re.search(
        r'(becoming|increasing|decreasing|diminishing)\s+'
        r'(?![^.]*?\d+\s+to\s+\d+\s+nm)'
        rf'(?:{_DIR}\s+)?.*?(?=\.|,)',
        _wind_commentary_text, re.IGNORECASE
    )
    if change_match:
        commentary = change_match.group(0)
        has_speed = re.search(r'\d+\s+(?:kt|knots?)', commentary, re.IGNORECASE)
        has_direction_change = re.search(
            rf'(becoming|increasing|decreasing|diminishing)\s+({_DIR})\b',
            commentary, re.IGNORECASE
        )
        if has_speed or has_direction_change:
            data['wind_commentary'] = commentary

    # --- 3. GUSTS ---
    gust_match = re.search(rf'[Gg]usts?\s+(?:up\s+to\s+)?({_SPEED})', text, re.IGNORECASE)
    if gust_match:
        data['wind_gusts'] = gust_match.group(1)

    # --- 4. WAVE HEIGHT ---
    seas_match = re.search(rf'(?:Seas|Waves)\s+(?:around\s+|up\s+to\s+)?({_HEIGHT})', text, re.IGNORECASE)
    if seas_match:
        data['wave_height'] = seas_match.group(1)

    # --- 5. WAVE COMMENTARY ---
    wave_change_match = re.search(
        rf'(building|subsiding)\s+to\s+({_HEIGHT})'
        rf'(?:,\s*occasionally\s+to\s+{_HEIGHT}(?:\s+in\s+the\s+\w+)?)?',
        text, re.IGNORECASE
    )
    if wave_change_match:
        data['wave_commentary'] = wave_change_match.group(0)

    # --- 6. WAVE DETAIL & COMPONENT PARSING ---
    detail_match = re.search(r'Wave detail:\s+(.*?)(?=\.|$)', text, re.IGNORECASE)

    if detail_match:
        full_detail_string = detail_match.group(1)
        data['wave_detail_string'] = full_detail_string

        component_pattern = rf'({_DIR})\s+(\d+\s+(?:ft|feet|foot))\s+at\s+(\d+\s+seconds?)'
        components = re.findall(component_pattern, full_detail_string, re.IGNORECASE)

        if components:
            data['swell_components'] = []
            for comp in components:
                direction = _DIR_NORM.get(comp[0].lower(), comp[0].upper())
                data['swell_components'].append({
                    "direction": direction,
                    "height": comp[1],
                    "period": comp[2]
                })
            data['primary_swell_direction'] = data['swell_components'][0]['direction']
            data['primary_wave_height'] = data['swell_components'][0]['height']
            data['primary_wave_period'] = data['swell_components'][0]['period']

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
    url = f"https://marine.weather.gov/MapClick.php?lat={lat}&lon={lon}"
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
            print(f"Warning: No forecast data found for {filename}. marine.weather.gov lat/lon endpoint is client-rendered.")

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
    # 1. Oregon Inlet (NWS office: MHX — AMZ180, 20-60nm)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=AMZ180",
        'weather_data.json'
    )
    # 2. Hatteras NC (NWS office: MHX — AMZ184, 20-60nm)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=AMZ184",
        'hatterasncnoaa.json'
    )
    # 3. Beaufort Inlet (NWS office: MHX — AMZ186, 20-60nm)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=AMZ186",
        'beaufortinletnoaa.json'
    )
    # 4. Virginia Beach (NWS office: AKQ — ANZ686, 20-60nm)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=ANZ686",
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

    # ── mid_atlantic nearshore (0-20nm) zones — feature: nearshore/offshore toggle ──
    # Paired with the 20-60nm offshore zones above. Verified individually against
    # live tgftp.nws.noaa.gov zone text (not pattern-guessed from the offshore ID —
    # nearshore/offshore zone spans are not always the same coastline stretch or
    # even the same WFO, e.g. Ocean City's ANZ485/ANZ650 pair below).
    # 1n. Oregon Inlet nearshore — AMZ150, S of Currituck Beach Light to Oregon Inlet NC, 0-20nm (MHX)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=AMZ150",
        'weather_data_nearshore.json'
    )
    # 2n. Hatteras Inlet nearshore — AMZ154, S of Cape Hatteras to Ocracoke Inlet NC, 0-20nm (MHX)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=AMZ154",
        'hatterasncnoaa_nearshore.json'
    )
    # 3n. Beaufort Inlet nearshore — AMZ156, S of Ocracoke Inlet to Cape Lookout NC, 0-20nm (MHX)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=AMZ156",
        'beaufortinletnoaa_nearshore.json'
    )
    # 4n. Virginia Beach nearshore — ANZ656, Cape Charles Light to VA-NC border, 0-20nm (AKQ)
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=ANZ656",
        'virginiabeachnoaa_nearshore.json'
    )
    # 5n. Ocean City Inlet nearshore — ANZ650, Fenwick Island DE to Chincoteague VA, 0-20nm (AKQ)
    #     Note: offshore ANZ485 (Cape May NJ to Fenwick Island DE) is issued by KPHI;
    #     nearshore ANZ650 is issued by KAKQ and starts where ANZ485 ends — not a mirrored span.
    scrape_and_save(
        "https://forecast.weather.gov/MapClick.php?zoneid=ANZ650",
        'oceancitynoaa_nearshore.json'
    )


    # ── GA/SC Region ─────────────────────────────────────────────────────────
    # ILM outer zones (AMZ270-276) and CHS outer zones (AMZ370-374) return
    # 400 when queried by zone ID from marine.weather.gov/MapClick.php, and
    # 404 from api.weather.gov/zones/forecast/. Use scrape_and_save_latlon()
    # instead: pass an offshore coordinate ~30nm from the port (inside the
    # zone) and marine.weather.gov auto-detects the correct zone.
    # JAX zones (AMZ470, AMZ472, AMZ474) — all 20-60nm, marine.weather.gov zone ID approach.
    #
    # NC/SC/GA nearshore zones — forecast.weather.gov zone ID approach (same as mid-atlantic ANZ zones)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ280", 'wrightsvillebeachnc_noaa.json')  # Surf City to Cape Fear NC, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ280", 'carolinabeachnc_noaa.json')      # Surf City to Cape Fear NC, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ280", 'southportnc_noaa.json')          # Cape Fear to Little River Inlet SC, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ284", 'littleriversc_noaa.json')        # Little River Inlet to Murrells Inlet SC, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ284", 'myrtlebeachsc_noaa.json')        # Little River Inlet to Murrells Inlet SC, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ284", 'murrellsinletsc_noaa.json')      # Murrells Inlet to South Santee River SC, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ284", 'georgetownsc_noaa.json')         # Murrells Inlet to South Santee River SC, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ380", 'charlestonsc_noaa.json')         # South Santee River to Edisto Beach SC, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ382", 'beaufortsc_noaa.json')           # Edisto Beach SC to Savannah GA, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ382", 'hiltonheadsc_noaa.json')         # Edisto Beach SC to Savannah GA, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ384", 'tybeega_noaa.json')              # Savannah GA to Altamaha Sound GA, 0-20nm
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ384", 'darienga_noaa.json')             # Savannah GA to Altamaha Sound GA, 0-20nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ470", 'stsimonsgaga_noaa.json')  # St. Simons Island GA — 20-60nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ470", 'jekyllga_noaa.json')      # Jekyll Island GA — 20-60nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ470", 'fernandinafl_noaa.json')  # Fernandina Beach FL — out 20nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ472", 'mayportfl_noaa.json')     # Mayport FL — out 20nm
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ474", 'staugustinefl_noaa.json') # St. Augustine FL — out 20nm

    # ── GA/SC nearshore (0-20nm) zones — feature: nearshore/offshore toggle ──
    # Verified individually against live forecast.weather.gov CWF text products
    # (ILM/CHS/JAX), not pattern-guessed. Two offshore zones split into two
    # nearshore zones each based on port position within the offshore span:
    # AMZ280 (Surf City-Little River Inlet) -> AMZ250 (Surf City-Cape Fear) north
    # portion / AMZ252 (Cape Fear-Little River Inlet) south portion; AMZ284
    # (Little River Inlet-S. Santee River) -> AMZ254 (Little River Inlet-Murrells
    # Inlet) north portion / AMZ256 (Murrells Inlet-S. Santee River) south portion.
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ250", 'wrightsvillebeachnc_noaa_nearshore.json')  # Surf City to Cape Fear NC, 0-20nm (ILM)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ250", 'carolinabeachnc_noaa_nearshore.json')      # Surf City to Cape Fear NC, 0-20nm (ILM)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ252", 'southportnc_noaa_nearshore.json')          # Cape Fear to Little River Inlet SC, 0-20nm (ILM)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ254", 'littleriversc_noaa_nearshore.json')        # Little River Inlet to Murrells Inlet SC, 0-20nm (ILM)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ254", 'myrtlebeachsc_noaa_nearshore.json')        # Little River Inlet to Murrells Inlet SC, 0-20nm (ILM)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ256", 'murrellsinletsc_noaa_nearshore.json')      # Murrells Inlet to S. Santee River SC, 0-20nm (ILM)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ256", 'georgetownsc_noaa_nearshore.json')         # Murrells Inlet to S. Santee River SC, 0-20nm (ILM)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ360", 'charlestonsc_noaa_nearshore.json')         # S. Santee River to Edisto Beach SC, 0-20nm (CHS)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ362", 'beaufortsc_noaa_nearshore.json')           # Edisto Beach SC to Savannah GA, 0-20nm (CHS)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ362", 'hiltonheadsc_noaa_nearshore.json')        # Edisto Beach SC to Savannah GA, 0-20nm (CHS)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ364", 'tybeega_noaa_nearshore.json')             # Savannah GA to Altamaha Sound GA, 0-20nm (CHS)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ364", 'darienga_noaa_nearshore.json')            # Savannah GA to Altamaha Sound GA, 0-20nm (CHS)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ450", 'stsimonsgaga_noaa_nearshore.json')        # Altamaha Sound GA to Fernandina Beach FL, 0-20nm (JAX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ450", 'jekyllga_noaa_nearshore.json')            # Altamaha Sound GA to Fernandina Beach FL, 0-20nm (JAX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ450", 'fernandinafl_noaa_nearshore.json')        # Altamaha Sound GA to Fernandina Beach FL, 0-20nm (JAX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ452", 'mayportfl_noaa_nearshore.json')           # Fernandina Beach to St. Augustine FL, 0-20nm (JAX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=AMZ454", 'staugustinefl_noaa_nearshore.json')       # St. Augustine to Flagler Beach FL, 0-20nm (JAX)

    # ── Northeast Florida Region ────────────────────────────────────────────
    # MLB (Melbourne) zones AMZ570/572/575 and MFL (Miami) zones AMZ670/671,
    # all 20-60nm, marine.weather.gov zone ID approach (verified live).
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ570", 'ponceinletfl_noaa.json')      # Flagler Beach to Volusia-Brevard County Line FL — 20-60nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ572", 'portcanaveralfl_noaa.json')   # Volusia-Brevard County Line to Sebastian Inlet FL — 20-60nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ575", 'sebastianinletfl_noaa.json')  # Sebastian Inlet to Jupiter Inlet FL — 20-60nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ575", 'fortpiercefl_noaa.json')      # Sebastian Inlet to Jupiter Inlet FL — 20-60nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ575", 'stuartfl_noaa.json')          # Sebastian Inlet to Jupiter Inlet FL — 20-60nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ670", 'lakeworthinletfl_noaa.json')  # Jupiter Inlet to Deerfield Beach FL — 20-60nm (MFL)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ671", 'fortlauderdalefl_noaa.json')  # Deerfield Beach to Ocean Reef FL — 20-60nm (MFL)

    # ── Northeast Florida nearshore (0-20nm) zones — feature: nearshore/offshore toggle ──
    # Verified individually against live forecast.weather.gov CWF text products
    # (MLB/MFL), not pattern-guessed. Sebastian Inlet, Fort Pierce, and Stuart
    # all fall within the same MLB nearshore span (AMZ555) as their shared
    # offshore zone (AMZ575).
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ550", 'ponceinletfl_noaa_nearshore.json')      # Flagler Beach to Volusia-Brevard County Line FL, 0-20nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ552", 'portcanaveralfl_noaa_nearshore.json')   # Volusia-Brevard County Line to Sebastian Inlet FL, 0-20nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ555", 'sebastianinletfl_noaa_nearshore.json')  # Sebastian Inlet to Jupiter Inlet FL, 0-20nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ555", 'fortpiercefl_noaa_nearshore.json')      # Sebastian Inlet to Jupiter Inlet FL, 0-20nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ555", 'stuartfl_noaa_nearshore.json')         # Sebastian Inlet to Jupiter Inlet FL, 0-20nm (MLB)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ650", 'lakeworthinletfl_noaa_nearshore.json')  # Jupiter Inlet to Deerfield Beach FL, 0-20nm (MFL)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ651", 'fortlauderdalefl_noaa_nearshore.json')  # Deerfield Beach to Ocean Reef FL, 0-20nm (MFL)

    # ── Virginia to Rhode Island Region ─────────────────────────────────────
    # "Virginia Beach, VA" and "Ocean City, MD" reuse the existing
    # virginiabeachnoaa.json / oceancitynoaa.json files above (same physical
    # ports, same zones) — only the 15 new ports below need scrapes.
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ684", 'wachapreagueva_noaa.json')     # Parramore Island VA to Cape Charles Light, 20-60nm (AKQ)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ682", 'chincoteagueva_noaa.json')     # Chincoteague VA to Parramore Island VA, 20-60nm (AKQ)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ485", 'indianriverinletde_noaa.json') # Cape May NJ to Fenwick Island DE, 20-60nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ485", 'capemaynj_noaa.json')          # Cape May NJ to Fenwick Island DE, 20-60nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ482", 'atlanticcitynj_noaa.json')     # Little Egg Inlet NJ to Great Egg Inlet NJ, 20-60nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ481", 'barnegatlightnj_noaa.json')    # Manasquan Inlet NJ to Little Egg Inlet NJ, 20-60nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ480", 'manasquannj_noaa.json')        # Sandy Hook NJ to Manasquan Inlet NJ, 20-40nm only (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ385", 'sandyhooknj_noaa.json')        # Sandy Hook NJ to Fire Island Inlet NY, 20-60nm (OKX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ385", 'freeportny_noaa.json')         # Sandy Hook NJ to Fire Island Inlet NY, 20-60nm (OKX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ385", 'captreeny_noaa.json')          # Sandy Hook NJ to Fire Island Inlet NY, 20-60nm (OKX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ380", 'shinnecockny_noaa.json')       # Moriches Inlet NY to Montauk Point NY, 20-60nm (OKX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ380", 'montaukny_noaa.json')          # Moriches Inlet NY to Montauk Point NY, 20-60nm (OKX)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=ANZ237", 'stoningtonct_noaa.json')         # Block Island Sound, bay waters (BOX)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=ANZ283", 'pointjudithri_noaa.json')        # Montauk NY to Martha's Vineyard, 25-60nm (BOX)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=ANZ283", 'newportri_noaa.json')            # Montauk NY to Martha's Vineyard, 25-60nm (BOX)

    # ── Virginia to Rhode Island nearshore (0-20nm) zones — feature: nearshore/offshore toggle ──
    # Verified individually against live forecast.weather.gov CWF text products
    # (AKQ/PHI/OKX/BOX), not pattern-guessed. Cape May NJ and Indian River Inlet
    # DE share offshore ANZ485 but have DIFFERENT nearshore zones (ANZ454 vs
    # ANZ455) issued by the same PHI office — same pattern as Ocean City's
    # ANZ485/ANZ650 split already noted above. Sandy Hook/Freeport/Captree
    # share one nearshore zone (ANZ355); Shinnecock Inlet/Montauk share another
    # (ANZ350); Point Judith/Newport share ANZ256 (BOX). Stonington, CT
    # (ANZ237) is bay-only with no offshore equivalent — no nearshore scrape.
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ654", 'wachapreagueva_noaa_nearshore.json')     # Parramore Island to Cape Charles Light VA, 0-20nm (AKQ)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ652", 'chincoteagueva_noaa_nearshore.json')     # Chincoteague to Parramore Island VA, 0-20nm (AKQ)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ455", 'indianriverinletde_noaa_nearshore.json') # Cape Henlopen to Fenwick Island DE, 0-20nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ454", 'capemaynj_noaa_nearshore.json')         # Cape May NJ to Cape Henlopen DE, 0-20nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ452", 'atlanticcitynj_noaa_nearshore.json')    # Little Egg Inlet to Great Egg Inlet NJ, 0-20nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ451", 'barnegatlightnj_noaa_nearshore.json')   # Manasquan Inlet to Little Egg Inlet NJ, 0-20nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ450", 'manasquannj_noaa_nearshore.json')       # Sandy Hook to Manasquan Inlet NJ, 0-20nm (PHI)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ355", 'sandyhooknj_noaa_nearshore.json')       # Sandy Hook NJ to Fire Island Inlet NY, 0-20nm (OKX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ355", 'freeportny_noaa_nearshore.json')        # Sandy Hook NJ to Fire Island Inlet NY, 0-20nm (OKX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ355", 'captreeny_noaa_nearshore.json')         # Sandy Hook NJ to Fire Island Inlet NY, 0-20nm (OKX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ350", 'shinnecockny_noaa_nearshore.json')      # Moriches Inlet NY to Montauk Point NY, 0-20nm (OKX)
    scrape_and_save("https://forecast.weather.gov/MapClick.php?zoneid=ANZ350", 'montaukny_noaa_nearshore.json')         # Moriches Inlet NY to Montauk Point NY, 0-20nm (OKX)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=ANZ256", 'pointjudithri_noaa_nearshore.json')       # Montauk NY to Martha's Vineyard, 0-20nm (BOX)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=ANZ256", 'newportri_noaa_nearshore.json')           # Montauk NY to Martha's Vineyard, 0-20nm (BOX)

    # ── Southern Florida Region (offshore only -- no nearshore toggle yet) ──
    # Fort Pierce, Stuart, Lake Worth Inlet, and Fort Lauderdale reuse the
    # ne_fl scrapes above (same physical ports, same zones/files). Only the
    # 7 new ports below need scrapes. All zones verified live 2026-07-19.
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=AMZ671", 'miamifl_noaa.json')          # Deerfield Beach to Ocean Reef FL, 20-60nm (MFL)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=GMZ072", 'islamoradafl_noaa.json')     # Straits of FL, Ocean Reef to Craig Key, 20-60nm (KEY)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=GMZ073", 'marathonfl_noaa.json')       # Straits of FL, Craig Key to west end of Seven Mile Bridge, 20-60nm (KEY)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=GMZ074", 'keywestfl_noaa.json')        # Straits of FL, Seven Mile Bridge to Halfmoon Shoal, 20-60nm (KEY)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=GMZ676", 'naplesfl_noaa.json')         # Chokoloskee to Bonita Beach FL, 20-60nm (MFL, Gulf side)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=GMZ676", 'marcoislandfl_noaa.json')    # Chokoloskee to Bonita Beach FL, 20-60nm (MFL, Gulf side -- same zone as Naples)
    scrape_and_save("https://marine.weather.gov/MapClick.php?zoneid=GMZ876", 'ftmyersbeachfl_noaa.json')   # Bonita Beach to Englewood FL, 20-60nm (TBW, Gulf side)


def scrape_and_save_cwf(wfo, zone_keywords, filename):
    """
    Fetch the Coastal/Offshore Waters Forecast text product for a WFO
    and parse the zone section matching any of zone_keywords.

    Used for AMZ270-374 (ILM/CHS OPC-managed zones) where the
    marine.weather.gov MapClick endpoint doesn't serve parseable HTML.
    Tries the Offshore (OFF) product first, then Coastal (CWF).

    wfo: NWS WFO site code (e.g. 'ILM', 'CHS')
    zone_keywords: list of strings — case-insensitive; any match selects the section
    filename: output JSON filename
    """
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36'}
    run_date = datetime.now()
    product_text = None

    for product in ['OFF', 'CWF']:
        url = (f'https://forecast.weather.gov/product.php?site={wfo}'
               f'&product={product}&issuedby={wfo}&format=txt&version=1&glossary=0')
        try:
            print(f'Fetching {product} text product for {wfo} ({filename})...')
            resp = requests.get(url, headers=headers, timeout=20)
            resp.raise_for_status()
            # The product page wraps the text in <pre> tags; extract it
            from bs4 import BeautifulSoup as _BS
            soup = _BS(resp.content, 'html.parser')
            pre = soup.find('pre')
            if pre and len(pre.get_text(strip=True)) > 100:
                product_text = pre.get_text()
                print(f'  Got {product} text ({len(product_text)} chars)')
                break
        except Exception as e:
            print(f'  {product} fetch error: {e}')
            continue

    final_data = {'timestamp': run_date.strftime('%Y-%m-%d %H:%M:%S'), 'forecasts': []}

    if not product_text:
        print(f'Warning: Could not fetch any text product for {wfo}/{filename}')
        with open(filename, 'w') as f:
            json.dump(final_data, f, indent=4)
        return

    # Split into zone sections — each zone ends with $$ or starts with a new zone code line
    # Zone sections are separated by $$ in NWS text products
    sections = re.split(r'\$\$', product_text)

    matched_section = None
    kw_lower = [kw.lower() for kw in zone_keywords]
    for section in sections:
        section_lower = section.lower()
        if any(kw in section_lower for kw in kw_lower):
            matched_section = section
            break

    if not matched_section:
        print(f'Warning: No matching zone section found for {zone_keywords} in {wfo} product')
        # Debug: show first 1500 chars of product to help tune keywords
        print(f'Product preview:\n{product_text[:1500]}')
        with open(filename, 'w') as f:
            json.dump(final_data, f, indent=4)
        return

    # Parse forecast periods from the matched section
    # NWS text format: .PERIOD NAME... followed by forecast text
    period_blocks = re.split(r'\.(?=[A-Z][A-Z])', matched_section)

    for block in period_blocks:
        block = block.strip()
        if not block:
            continue
        # First line is the period name, rest is the forecast text
        lines = block.split('\n')
        period_name_raw = lines[0].strip().rstrip('.').rstrip('...').strip()
        if not period_name_raw or len(period_name_raw) < 3:
            continue
        # Skip header/metadata lines
        if re.match(r'^(FZUS|FZAK|NWS|NATIONAL|COASTAL|OFFSHORE|\d{3,})', period_name_raw, re.IGNORECASE):
            continue

        forecast_text = ' '.join(l.strip() for l in lines[1:] if l.strip())
        if len(forecast_text) < 10:
            continue

        formatted_period = get_forecast_date(period_name_raw, run_date)
        parsed = parse_marine_forecast(forecast_text)
        parsed['period'] = formatted_period
        final_data['forecasts'].append(parsed)

    if not final_data['forecasts']:
        print(f'Warning: Matched section but parsed 0 periods for {filename}')
        print(f'Matched section preview:\n{matched_section[:800]}')
    else:
        print(f'Success! {len(final_data["forecasts"])} periods saved to {filename}')

    with open(filename, 'w') as f:
        json.dump(final_data, f, indent=4)


if __name__ == "__main__":
    main()
