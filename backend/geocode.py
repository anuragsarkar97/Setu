import os
from math import asin, cos, radians, sin, sqrt

import httpx
from dotenv import load_dotenv

load_dotenv()

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
MAPS_API_KEY = os.environ["GOOGLE_MAPS_API_KEY"]


async def geocode(query: str) -> tuple[float, float]:
    """Resolve a location string to (lat, lng). Raises ValueError on failure."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(GEOCODE_URL, params={"address": query, "key": MAPS_API_KEY})
        resp.raise_for_status()
        data = resp.json()

    if data["status"] != "OK" or not data["results"]:
        raise ValueError(f"Geocoding failed: {data['status']}")

    loc = data["results"][0]["geometry"]["location"]
    return loc["lat"], loc["lng"]


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Great-circle distance between two points in kilometres.
    Uses the Haversine formula — accurate enough for city-scale matching.
    """
    R = 6371.0
    dlat = radians(lat2 - lat1)
    dlng = radians(lng2 - lng1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlng / 2) ** 2
    return R * 2 * asin(sqrt(a))
