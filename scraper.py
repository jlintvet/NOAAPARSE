import json
import random
from datetime import datetime

# 1. Create some dummy weather data
data = {
    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    "temperature": random.randint(60, 90),
    "condition": "Sunny"
}

# 2. Print it to the logs (so you can see it in Actions)
print(f"Scraped data: {data}")

# 3. SAVE IT to a JSON file (This is the critical part!)
with open('weather_data.json', 'w') as f:
    json.dump(data, f, indent=4)

print("Successfully saved weather_data.json")
