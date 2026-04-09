from flask import Flask, request, jsonify
from flask_cors import CORS
from pymongo import MongoClient
import feedparser
import urllib.parse

app = Flask(__name__)
CORS(app)

# ==============================
# Database Connection
# ==============================

client = MongoClient("mongodb://localhost:27017/")
db = client["disaster_eye"]
collection = db["news"]

# Create indexes (professional practice)
collection.create_index("url", unique=True)
collection.create_index("publishedAt")

# ==============================
# Disaster Keywords
# ==============================

DISASTER_KEYWORDS = {
    "earthquake": ["earthquake", "tremor", "seismic", "quake"],
    "flood": ["flood", "flooding", "waterlogging", "heavy rain", "monsoon"],
    "cyclone": ["cyclone", "storm", "hurricane", "typhoon"],
    "landslide": ["landslide", "mudslide", "hill collapse"],
    "wildfire": ["fire", "wildfire", "forest fire", "blaze"]
}

# Impact words to confirm real disaster event
IMPACT_KEYWORDS = [
    "killed",
    "injured",
    "dead",
    "death",
    "damage",
    "destroyed",
    "evacuated",
    "rescue",
    "missing",
    "collapse",
    "collapsed",
    "flooded",
    "devastated",
    "wrecked"
]

# ==============================
# Fetch News from Google RSS
# ==============================

@app.route("/fetch-news")
def fetch_news():

    disaster_type = request.args.get("type", "all").lower()

    if disaster_type in DISASTER_KEYWORDS:
        query = disaster_type + " India"
    else:
        query = "earthquake OR flood OR cyclone OR landslide OR fire India"

    encoded_query = urllib.parse.quote(query)

    rss_url = f"https://news.google.com/rss/search?q={encoded_query}&hl=en-IN&gl=IN&ceid=IN:en"

    feed = feedparser.parse(rss_url)

    inserted_count = 0

    for entry in feed.entries:

        title = entry.title
        link = entry.link
        published = entry.get("published", "")

        full_text = title.lower()

        detected_type = "other"

        for dtype, keywords in DISASTER_KEYWORDS.items():
            if any(keyword in full_text for keyword in keywords):

                # Second layer: must contain impact keyword
                if any(impact in full_text for impact in IMPACT_KEYWORDS):
                    detected_type = dtype
                    break

        # Skip non-impact or non-disaster news
        if detected_type == "other":
            continue

        try:
            collection.insert_one({
                "title": title,
                "url": link,
                "publishedAt": published,
                "source": "Google News",
                "type": detected_type
            })
            inserted_count += 1
        except:
            # Skip duplicate errors
            pass

    return jsonify({
        "message": "Filtered disaster news fetched successfully",
        "inserted": inserted_count
    })


# ==============================
# Get Stored News
# ==============================

@app.route("/disaster-news")
def get_disaster_news():

    disaster_type = request.args.get("type", "all").lower()

    if disaster_type == "all":
        news = list(collection.find({}, {"_id": 0}).sort("publishedAt", -1))
    else:
        news = list(collection.find(
            {"type": disaster_type},
            {"_id": 0}
        ).sort("publishedAt", -1))

    return jsonify(news)


# ==============================
# Clear Database (Development Only)
# ==============================

@app.route("/clear-db")
def clear_db():
    result = collection.delete_many({})
    return jsonify({
        "message": "Database cleared",
        "deleted_documents": result.deleted_count
    })


# ==============================
# Run Server
# ==============================

if __name__ == "__main__":
    print("Disaster Eye Backend Running...")
    app.run(debug=True)
