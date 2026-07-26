import os
import requests
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("API_FOOTBALL_KEY")
url = "https://v3.football.api-sports.io/status"
headers = {
    "x-rapidapi-key": api_key,
    "x-rapidapi-host": "v3.football.api-sports.io"
}
response = requests.request("GET", url, headers=headers)
print(response.json())
