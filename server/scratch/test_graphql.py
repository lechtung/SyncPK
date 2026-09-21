import os
import json
import requests

# Load env
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip().strip('"').strip("'")

PLEX_TOKEN = os.environ.get("PLEX_TOKEN")

url_graphql = "https://community.plex.tv/api"
headers_fetch = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "x-plex-client-identifier": "7o448fp80hf1p7gbvqvvaklv",
    "x-plex-token": PLEX_TOKEN
}

# We'll fetch more fields to see if there are grouped events
query_graphql = """
query GetActivityFeed($first: PaginationInt!, $after: String, $types: [ActivityType!]!) {
  activityFeed(first: $first, after: $after, types: $types) {
    nodes {
      id date __typename
      metadataItem { __typename title guid type }
      ... on ActivityFeedGroupNode {
         items { title guid type }
      }
      ... on ActivityEvent {
         # just guessing fields
         items { title }
         children { title }
      }
    }
  }
}
"""

payload = {
    "query": query_graphql,
    "variables": {"first": 100, "after": None, "types": ["WATCH_HISTORY", "WATCH_SESSION"]},
    "operationName": "GetActivityFeed"
}

resp = requests.post(url_graphql, headers=headers_fetch, json=payload, timeout=20)
print(f"Status: {resp.status_code}")
if resp.status_code == 200:
    data = resp.json()
    with open("graphql_dump.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("Dumped to graphql_dump.json")
else:
    print(resp.text)
