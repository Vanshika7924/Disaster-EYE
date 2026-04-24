# =============================================================================
# pipeline.py — COMPLETE FINAL VERSION
# All filters included and verified:
#  1. BERT disaster classification
#  2. Metaphor / political / social media noise
#  3. Govt scheme / relief distribution / non-events
#  4. India sending aid abroad (Afghanistan etc.)
#  5. Political visits after disaster
#  6. Explainer / forecast / feature articles
#  7. Foreign country filter (US, Kuwait, Iran, Singapore, Papua New Guinea etc.)
#  8. Local/industrial incidents (steel plant, ONGC, factory etc.)
#  9. Fire verification — only forest/wildfire kept
# 10. India signal required
# 11. Score-based event check
# 12. No location+state → remove
# 13. Dedup on link before saving CSV
# =============================================================================

import os
import re
import logging
import pandas as pd
from datetime import datetime, timezone

from data_fetcher   import fetch_rss_news
from classifier     import get_classifier
from ner_model      import extract_location, is_india_news
from time_extractor import extract_disaster_time
from config         import DISASTER_LABELS, RAW_RSS_FILE, MASTER_CSV_FILE

try:
    from db import get_db
    MONGO_AVAILABLE = True
except Exception:
    MONGO_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── Newspaper source suffix — strip before processing ─────────────────────────
SOURCE_SUFFIX_PAT = re.compile(
    r'\s*[\|\-\u2013\u2014]\s*(?:the\s+)?(?:times of india|hindustan times|ndtv|'
    r'india today|financial express|economic times|the hindu|republic world|'
    r'news18|ani|pti|lokmattimes|theprint|scroll|mint|business standard|'
    r'tribune india|punjabkesari|punjab kesari|devdiscourse|'
    r'[a-z]+times\.(?:com|in)|[a-z]+news\.(?:com|in)|[a-z]+express\.[a-z]+'
    r'|[a-z]+kesari\.[a-z]+|[a-z]+discourse\.[a-z]+)'
    r'(?:\s+[a-z]+)?$',
    re.IGNORECASE
)

TIMEZONE_PAT = re.compile(r'\([A-Za-z\s]+time\)', re.IGNORECASE)

# =============================================================================
# FILTER 1: Metaphor / political / social media noise
# =============================================================================
METAPHOR_PHRASES = [
    'financial earthquake', 'political earthquake', 'economic earthquake',
    'earthquake in politics', 'political storm', 'financial storm',
    'flood of votes', 'flood of memes', 'flood social media',
    'landslide victory', 'landslide win', 'landslide margin',
    'election', 'manifesto', 'sankalp patra', 'polls',
    'bjp', 'congress', 'lok sabha', 'rajya sabha', 'assembly election',
    'west asia tensions', 'west asia war', 'middle east tensions',
    'relief scam', 'flood scam', 'disaster scam',
    'viral video', 'memes', 'ipl', 'bollywood', 'sensex', 'nifty', 'markets',
    'social media erupts', 'erupts on social media', 'floods social media',
    'floodgates of controversy', 'opens floodgates',
    'dmk', 'aiadmk', 'tdp ', 'ysrcp', 'shiv sena',
    'seats in', 'lok poll survey', 'exit poll',
]

# =============================================================================
# FILTER 2: Non-events (govt scheme, aid, visits, forecasts, features)
# =============================================================================
NON_EVENT_PHRASES = [
    # Training / awareness
    'workshop', 'seminar', 'conference', 'summit', 'preparedness',
    'drill', 'mock drill', 'training', 'organized', 'organised', 'conducted',
    'held at', 'historical', 'history', 'anniversary', 'explainer',
    # Tech articles
    'harnesses ai', 'ai-generated', 'ai generated',
    'riskiest', 'climate action', 'disaster resilience', 'seismic zone',
    'prone to earthquake', 'prone to flood', 'prone to cyclone',
    # Govt scheme / relief
    'relief materials', 'flood relief for farmers', 'flood relief package',
    'demands flood relief', 'crore flood relief',
    'demands rs', 'approves rs', 'approves crore',
    'mitigation scheme', 'government approves', 'govt approves', 'centre approves',
    'compensation announced', 'ex gratia', 'relief fund allocated',
    'allocated rs', 'announces scheme', 'announces package',
    # India sending aid ABROAD
    'sends aid to', 'sends relief to', 'sends humanitarian',
    'sends emergency relief', 'dispatches aid to',
    'humanitarian aid to', 'india sends', 'india dispatches',
    'relief to flood-hit', 'relief to earthquake-hit', 'relief to cyclone-hit',
    # Political visits
    'visits flood-hit', 'visits cyclone-hit', 'visits earthquake-hit',
    'visits landslide-hit', 'pm visits', 'cm visits',
    'minister visits', 'rahul visits', 'modi visits', 'opposition visits',
    # Forecast / explainer / travel
    'means for travel', 'alert means for', 'what it means',
    'potential risk to', 'impact on travel', 'travel advisory',
    'tourism impact', 'monsoon outlook',
    'check forecast', 'weather update', 'weather latest',
    # Reconstruction / post-disaster
    'rebuilds homes', 'gets new homes', 'reconstruction work', 'new homes built',
    # Infrastructure cost
    'rs 5 crore', 'rs 50 crore', 'rs 500 crore',
    'cost of rs', 'built at cost', 'constructed at cost', 'worth rs',
    # Feature articles
    'uneasy minds', 'flooded fields',
    # Explainer / listicle format (not real event)
    'in 5 points', 'in 10 points', 'in points',
    'points: magnitude', 'key points', 'all you need to know',
    'what you need to know', 'explained', 'here is what',
    'everything you need',
    # Compensation / legal / court order (not real disaster)
    'deposits compensation', 'crore compensation',
    'firm deposits', 'company deposits', 'pays compensation',
    'compensation for victims', 'compensation to victims',
    'deposits rs', 'deposits crore',
    # Govt project / infrastructure work (not real disaster)
    'protection projects', 'govt undertakes', 'undertakes project',
    'projects worth', 'flood-protection', 'flood protection project',
    'mitigation project', 'prevention project',
    # Govt infrastructure / drain work (not real disaster)
    'desilting', 'desilting work', 'desilting target',
    'drain cleaning', 'flood control dept', 'flood control minister',
    'per cent target', 'per cent desilting',
    # Risk assessment / forecast (not real disaster)
    'holding ponds', 'monsoon flood risk', 'raising monsoon',
    'flood risk', 'capacity shrinks', 'raising flood risk',
    'risk assessment', 'flood prone', 'risk rises',
    # Tech / research / early warning (not real disaster)
    'early warning system', 'warning system could',
    'iit mandi', 'could save lives', 'save lives before',
    'lives ahead of', 'ai-powered system', 'powered early warning',
    # Military / gunfire
    'open fire', 'security forces fire', 'crpf fire',
    'iran fire', 'military fire', 'base attack', 'opens fire',
]

# =============================================================================
# FILTER 3: Foreign countries — always reject
# =============================================================================
FOREIGN_PATTERNS = [
    r'\bvanuatu\b', r'\btonga\b', r'\bfiji\b', r'\bsouth pacific\b',
    r'\bwestern australia\b', r'\bnew south wales\b',
    r'\balaska\b', r'\bcalifornia\b', r'\bflorida\b', r'\btexas\b',
    r'\bsan diego\b', r'\bhernando county\b', r'\belk park\b', r'\bsantee\b',
    r'\b\w[\w\s]+ county\b',
    r'\bindian ocean\b', r'\bgfz\b',
    r'\bindonesia\b', r'\baustralia\b', r'\bwest asia\b',
    r'\busa\b', r'\bunited states\b', r'\bu\.s\.\b', r'\bus base\b',
    r'\bchina\b', r'\bjapan\b', r'\brussia\b', r'\bukraine\b',
    r'\bturkey\b', r'\bsyria\b', r'\byemen\b',
    r'\bsri lanka\b', r'\bbangladesh\b', r'\bmyanmar\b', r'\bthailand\b',
    r'\bafghanistan\b', r'\biran\b', r'\biraq\b', r'\bisrael\b',
    r'\bpakistan\b', r'\bnepal\b', r'\bbhutan\b',
    r'\buae\b', r'\bdubai\b', r'\bkuwait\b', r'\bsaudi arabia\b', r'\boman\b',
    r'\bsingapore\b', r'\bpapua new guinea\b', r'\bsolomon islands\b',
    r'\bindo-pak border\b', r'\bpakistan border\b',
    r'\bmalaysia\b', r'\bphilippines\b', r'\bvietnam\b',
    r'\bnew zealand\b', r'\bcanada\b', r'\buk\b', r'\bengland\b',
    r'\bfrance\b', r'\bgermany\b', r'\bitaly\b', r'\bspain\b',
    r'\bbrazil\b', r'\bmexicoo\b', r'\bargentina\b',
]

# =============================================================================
# FILTER 4: Always reject — industrial/commercial regardless of scale
# =============================================================================
ALWAYS_REJECT_PATTERNS = [
    r'\bsteel plant\b', r'\bsteel mill\b',
    r'\bpower plant\b', r'\bthermal plant\b',
    r'\boil platform\b', r'\bongc\b',
    r'\bturbine explosion\b',
    r'\bcrackers.*unit\b', r'\bmanufacturing unit.*fire\b',
    r'\bfirecracker.*unit\b', r'\bfirecracker.*factor\b',
    r'\bblinkit\b', r'\bswiggy.*fire\b',
    r'\bindus.*blast\b', r'\brefinery.*fire\b',
]

# =============================================================================
# FILTER 5: Local incidents — reject unless large-scale
# =============================================================================
LOCAL_INCIDENT_PATTERNS = [
    r'\bfactory fire\b', r'\bshop fire\b', r'\bstore fire\b',
    r'\bwarehouse fire\b', r'\bgodown fire\b', r'\bbuilding fire\b',
    r'\bhouse fire\b', r'\bapartment fire\b', r'\bflat fire\b',
    r'\bhospital fire\b', r'\bschool fire\b', r'\bcollege fire\b',
    r'\boffice fire\b', r'\brestaurant fire\b', r'\bhotel fire\b',
    r'\bvehicle fire\b', r'\bcar fire\b', r'\bbus fire\b',
    r'\btruck fire\b', r'\btrain coach fire\b',
    r'\bboiler blast\b', r'\bgas cylinder blast\b',
    r'\bshort circuit\b', r'\bindustrial accident\b',
    r'\bfactory blast\b', r'\bboiler explosion\b',
    r'\bchemical leak\b', r'\bminor fire\b',
    r'\brefrigerator fire\b', r'\bfridge fire\b',
    r'\bfire in a refrigerator\b', r'\bfire in the refrigerator\b',
    r'\bfire breaks out in a\b', r'\bfire breaks out at a\b',
    r'\bac fire\b', r'\bair conditioner fire\b', r'\belectric fire\b',
    r'\bvehicle catches fire\b', r'\btruck.*catches fire\b',
    r'\bcar catches fire\b', r'\bbus catches fire\b',
    r'\bresidential fire\b', r'\bhome fire\b',
]

PUBLIC_DISASTER_SIGNALS = [
    'forest fire', 'wildfire', 'wild fire',
    'major fire', 'massive fire', 'huge fire', 'large fire',
    'spread rapidly', 'spreading rapidly',
    'village affected', 'villages affected',
    'evacuation', 'evacuated',
    'ndrf', 'sdrf', 'army deployed',
    'highway blocked', 'roads submerged',
    'flood warning', 'red alert', 'orange alert',
    'fire engines', 'fire brigade',
]

# =============================================================================
# FILTER 6: Forest fire verification
# If BERT says 'fire', verify it is actually forest/wildfire
# =============================================================================
FOREST_FIRE_SIGNALS = [
    'forest fire', 'wildfire', 'wild fire', 'jungle fire',
    'forest blaze', 'forest fires', 'wildfire spreads',
    'forest department', 'trees burnt', 'acres burnt',
    'hectares burnt', 'hectares of forest', 'forest area',
    'wildlife sanctuary', 'national park', 'tiger reserve',
    'biosphere reserve', 'iaf deployed', 'air force fire',
    'forest cover', 'van vibhag',
]

EVENT_SIGNALS = [
    'hits', 'hit', 'struck', 'jolts', 'shakes', 'shook',
    'injured', 'killed', 'dead', 'missing', 'evacuated',
    'warning issued', 'alert issued', 'death toll',
    'rescue operation', 'flooded', 'overflow', 'collapsed',
    'houses damaged', 'highway blocked', 'landfall',
    'erupts', 'rages', 'sweeps through',
    'blocks', 'blocked', 'stranded', 'trapped', 'washed away',
]

IMPACT_SIGNALS = [
    'damage', 'damaged', 'injured', 'killed', 'dead', 'missing',
    'evacuated', 'warning', 'alert', 'rescue', 'flooded', 'collapsed',
    'blocked', 'destroyed', 'death toll', 'heavy rain', 'overflow', 'submerged',
]

DISASTER_WORDS = [
    'earthquake', 'flood', 'wildfire', 'forest fire', 'landslide',
    'cyclone', 'storm', 'cloudburst', 'avalanche', 'flash flood',
]


def clean_text(text: str) -> str:
    if not text:
        return ""
    text = str(text)
    text = text.replace("\u2019", "'").replace("`", "'")
    text = re.sub(r"[\u201c\u201d]", '"', text)
    text = re.sub(r"[\u2018\u2019]", "'", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Strip newspaper source suffixes
    text = SOURCE_SUFFIX_PAT.sub('', text).strip()
    # Strip timezone references
    text = TIMEZONE_PAT.sub('', text).strip()
    return text


def is_real_disaster_news(text: str, disaster_type: str = '', confidence: float = 0.0) -> bool:
    text = clean_text(text)
    tl = text.lower()

    if confidence < 0.50:
        return False

    # 1. Metaphor / political
    for phrase in METAPHOR_PHRASES:
        if phrase in tl:
            return False

    # 2. Non-event / govt scheme / aid abroad / visits
    for phrase in NON_EVENT_PHRASES:
        if phrase in tl:
            return False

    # 3. Foreign country
    for pat in FOREIGN_PATTERNS:
        if re.search(pat, tl):
            return False

    # 4. Always reject industrial
    if any(re.search(pat, tl) for pat in ALWAYS_REJECT_PATTERNS):
        return False

    # 5. Local incident
    if any(re.search(pat, tl) for pat in LOCAL_INCIDENT_PATTERNS):
        if not any(sig in tl for sig in PUBLIC_DISASTER_SIGNALS):
            return False

    # 6. Fire verification — only forest/wildfire
    if disaster_type in ('fire', 'forest fire'):
        if not any(sig in tl for sig in FOREST_FIRE_SIGNALS):
            return False

    # 7. Score check
    score = 0
    if any(s in tl for s in EVENT_SIGNALS):    score += 2
    if any(s in tl for s in IMPACT_SIGNALS):   score += 2
    if re.search(r'\b\d+(\.\d+)?\b', tl):     score += 1
    if any(dw in tl for dw in DISASTER_WORDS): score += 2
    if 'fire' in tl and any(s in tl for s in PUBLIC_DISASTER_SIGNALS):
        score += 2

    return score >= 3


def save_raw_rss(df: pd.DataFrame):
    os.makedirs(os.path.dirname(RAW_RSS_FILE), exist_ok=True)
    try:
        df.to_csv(RAW_RSS_FILE, index=False)
        logger.info(f"Raw RSS CSV saved: {RAW_RSS_FILE}")
    except PermissionError:
        logger.warning(f"{RAW_RSS_FILE} is locked.")


def save_master_csv(records: list):
    if not records:
        return
    os.makedirs(os.path.dirname(MASTER_CSV_FILE), exist_ok=True)
    new_df = pd.DataFrame(records)
    if os.path.exists(MASTER_CSV_FILE):
        try:
            old_df = pd.read_csv(MASTER_CSV_FILE, encoding="utf-8")
        except Exception:
            try:
                old_df = pd.read_csv(MASTER_CSV_FILE, encoding="latin1")
            except Exception:
                old_df = pd.DataFrame()
        combined_df = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined_df = new_df.copy()
    # Deduplicate on link
    if "link" in combined_df.columns:
        combined_df = combined_df.drop_duplicates(subset=["link"], keep="last").reset_index(drop=True)
    try:
        combined_df.to_csv(MASTER_CSV_FILE, index=False)
        logger.info(f"Master CSV: {len(combined_df)} unique rows")
    except PermissionError:
        logger.warning(f"{MASTER_CSV_FILE} is locked.")


def run_pipeline(rss_items=None, save_csv=True, use_mongo=True):
    logger.info("=" * 60)
    logger.info("Disaster Eye Pipeline Started")
    logger.info("=" * 60)

    summary = {
        "fetched": 0, "bert_disaster": 0,
        "real_disaster_filter": 0, "india_filter": 0,
        "location_filter": 0, "inserted": 0,
        "duplicates": 0, "final_rows": 0,
    }

    # STEP 1
    if rss_items is None:
        logger.info("Step 1: Fetching RSS news...")
        rss_items = fetch_rss_news()
    summary["fetched"] = len(rss_items)
    logger.info(f"Fetched {len(rss_items)} articles")
    if not rss_items:
        return summary

    df = pd.DataFrame(rss_items)
    for col in ["title", "summary", "text", "link", "published"]:
        if col not in df.columns:
            df[col] = ""
    df["title"]     = df["title"].fillna("").astype(str).apply(clean_text)
    df["summary"]   = df["summary"].fillna("").astype(str).apply(clean_text)
    df["text"]      = df["text"].fillna("").astype(str).apply(clean_text)
    df["link"]      = df["link"].fillna("").astype(str)
    df["published"] = df["published"].fillna("").astype(str)
    df["text"] = df.apply(
        lambda r: r["text"].strip() if r["text"].strip()
        else (r["title"] + " " + r["summary"]).strip(), axis=1)
    if save_csv:
        save_raw_rss(df)

    # STEP 2: BERT
    logger.info("Step 2: Running BERT predictions...")
    clf = get_classifier()
    results = clf.predict_batch(df["text"].tolist())
    df["disaster_type"] = [r[0] for r in results]
    df["confidence"]    = [r[1] for r in results]
    df["is_disaster"]   = df["disaster_type"].isin(DISASTER_LABELS)
    df_dis = df[df["is_disaster"]].copy().reset_index(drop=True)
    summary["bert_disaster"] = len(df_dis)
    logger.info(f"BERT: {len(df_dis)} disaster rows")
    if df_dis.empty:
        return summary

    # STEP 3: Real disaster filter
    logger.info("Step 3: Removing non-real/noise news...")
    before = len(df_dis)
    df_dis = df_dis[
        df_dis.apply(lambda row: is_real_disaster_news(
            str(row.get("text", "")),
            str(row.get("disaster_type", "")),
            row.get("confidence", 0.0)
        ), axis=1)
    ].copy().reset_index(drop=True)
    summary["real_disaster_filter"] = len(df_dis)
    logger.info(f"Removed {before - len(df_dis)} noise rows | {len(df_dis)} remaining")
    if df_dis.empty:
        return summary

    # STEP 4: India filter
    logger.info("Step 4: Filtering non-India news...")
    before = len(df_dis)
    df_dis["is_india"] = df_dis["text"].apply(is_india_news)
    df_dis = df_dis[df_dis["is_india"]].copy().reset_index(drop=True)
    summary["india_filter"] = len(df_dis)
    logger.info(f"Removed {before - len(df_dis)} foreign rows | {len(df_dis)} remaining")
    if df_dis.empty:
        return summary

    # STEP 5: Location extraction
    logger.info("Step 5: Extracting locations...")
    loc_results = [extract_location(t) for t in df_dis["text"]]
    df_dis["location"] = [r[0] for r in loc_results]
    df_dis["state"]    = [r[1] for r in loc_results]
    df_dis["country"]  = [r[2] if r[2] else "India" for r in loc_results]
    before = len(df_dis)
    df_dis = df_dis[
        (df_dis["location"].notna() & (df_dis["location"].astype(str).str.strip() != "")) |
        (df_dis["state"].notna() & (df_dis["state"].astype(str).str.strip() != ""))
    ].copy().reset_index(drop=True)
    summary["location_filter"] = len(df_dis)
    logger.info(f"Removed {before - len(df_dis)} no-location rows | {len(df_dis)} remaining")
    if df_dis.empty:
        return summary

    # STEP 6: Time extraction
    logger.info("Step 6: Extracting disaster time...")
    time_results = [
        extract_disaster_time(row["text"], row.get("published"))
        for _, row in df_dis.iterrows()
    ]
    df_dis["disaster_time"] = [r[0] for r in time_results]
    df_dis["time_source"]   = [r[1] for r in time_results]

    # STEP 7: Build records
    now = datetime.now(timezone.utc).isoformat()
    records = []
    for _, row in df_dis.iterrows():
        records.append({
            "headline":      row.get("title", ""),
            "summary":       row.get("summary", ""),
            "text":          row.get("text", ""),
            "link":          row.get("link", ""),
            "published_time": row.get("published", ""),
            "disaster_time": row.get("disaster_time"),
            "time_source":   row.get("time_source"),
            "disaster_type": row.get("disaster_type"),
            "confidence":    float(row.get("confidence", 0.0)),
            "location":      row.get("location"),
            "state":         row.get("state"),
            "country":       row.get("country", "India"),
            "is_disaster":   True,
            "processed_at":  now,
        })
    summary["final_rows"] = len(records)

    if save_csv and records:
        save_master_csv(records)

    if use_mongo and MONGO_AVAILABLE and records:
        try:
            db_result = get_db().insert_alerts(records)
            summary["inserted"]   = db_result.get("inserted", 0)
            summary["duplicates"] = db_result.get("duplicates", 0)
        except Exception as e:
            logger.warning(f"MongoDB insert failed: {e}")

    logger.info("=" * 60)
    logger.info(f"Pipeline Complete: {summary}")
    logger.info("=" * 60)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    summary = run_pipeline(save_csv=True, use_mongo=True)
    print("\nFINAL SUMMARY:", summary)
