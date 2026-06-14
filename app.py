from flask import Flask, request, jsonify, session
from flask_cors import CORS
import boto3
from decimal import Decimal
import uuid
from datetime import datetime
import io, os, time, logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("review-analyser")

AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY", "")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_KEY", "")
AWS_REGION     = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "review-results")
S3_BUCKET      = os.environ.get("S3_BUCKET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-this-in-production")
DATA_TTL_HOURS = int(os.environ.get("DATA_TTL_HOURS", "24"))

SUPPORTED_LANGUAGES = ['en','hi','de','fr','es','it','ja','ko','zh','ar','pt']
COMPREHEND_BATCH_LIMIT = 25  # AWS Comprehend hard limit per BatchDetect* call

# ── COLUMN DETECTION ─────────────────────────────────────────
# Priority order — exact (case-insensitive) header match wins immediately
REVIEW_CANDIDATES = [
    'review','reviews','review_text','review text','comment','comments',
    'feedback','customer_feedback','customer_review','customer review',
    'opinion','description','text','content'
]
# Headers containing these substrings are NEVER treated as review text
EXCLUDE_PATTERNS = [
    'id','sku','product','category','rating','score','price','date',
    'qty','quantity','stock','code','status','name','type','brand'
]

app = Flask(__name__)
app.secret_key = SESSION_SECRET
app.config["MAX_CONTENT_LENGTH"] = 10 * 1024 * 1024  # 10 MB

CORS(app, supports_credentials=True)

def comp():
    return boto3.client("comprehend", region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)

def tbl():
    return boto3.resource("dynamodb", region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY).Table(DYNAMODB_TABLE)

def s3c():
    return boto3.client("s3", region_name=AWS_REGION,
        aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY)

def fix(obj):
    if isinstance(obj, Decimal): return float(obj)
    if isinstance(obj, dict):    return {k: fix(v) for k, v in obj.items()}
    if isinstance(obj, list):    return [fix(i) for i in obj]
    return obj

def get_session_id():
    if 'session_id' not in session:
        session['session_id'] = str(uuid.uuid4())
        session.permanent = True
    return session['session_id']

def get_ttl():
    return int(time.time()) + (DATA_TTL_HOURS * 3600)


def detect_review_columns(headers, rows):
    """
    Returns (detected_column, candidate_columns).

    Step 1 — exact match against known review/feedback column names
             (case-insensitive). This is the common case and resolves instantly.

    Step 2 — heuristic fallback: look at the first 10 rows, exclude any
             column whose header looks like an ID/SKU/product/rating/date
             field, and pick text columns with an average value length
             >= 15 chars (review text is long; product names, IDs, ratings
             are short).

    Returns (None, []) if nothing looks like review text at all.
    """
    # Step 1 — exact known names
    for h in headers:
        if h.lower().strip() in REVIEW_CANDIDATES:
            return h, [h]

    # Step 2 — heuristic on content
    candidates = []
    for h in headers:
        hl = h.lower().strip()
        if any(p in hl for p in EXCLUDE_PATTERNS):
            continue
        sample = [str(r.get(h, '') or '').strip() for r in rows[:10]]
        non_empty = [s for s in sample if s]
        if not non_empty:
            continue
        avg_len = sum(len(s) for s in non_empty) / len(non_empty)
        if avg_len >= 15:
            candidates.append((h, avg_len))

    if candidates:
        candidates.sort(key=lambda x: -x[1])  # longest-text column first
        return candidates[0][0], [c[0] for c in candidates]

    return None, []


def chunked(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


@app.route("/")
def index():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates", "index.html")
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return content, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.route("/api/status")
def status():
    try:
        comp().detect_sentiment(Text="test", LanguageCode="en")
        return jsonify({"ok": True, "session_id": get_session_id()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/api/parse", methods=["POST"])
def parse():
    """
    Parse uploaded CSV/Excel.
    Returns headers, rows, and column-detection results so the frontend
    can pre-select the right column and show the user what was detected.
    """
    try:
        f = request.files.get("file")
        if not f:
            return jsonify({"error": "no file"}), 400

        filename   = f.filename.lower()
        file_bytes = f.read()

        if filename.endswith(".csv"):
            import csv
            reader  = csv.DictReader(io.StringIO(file_bytes.decode("utf-8", errors="replace")))
            rows    = [dict(r) for r in reader]
            headers = list(reader.fieldnames or [])

        elif filename.endswith((".xlsx", ".xls")):
            import openpyxl
            wb       = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
            ws       = wb.active
            all_rows = list(ws.iter_rows(values_only=True))
            if not all_rows:
                return jsonify({"error": "Empty file"}), 400
            headers  = [str(h).strip() if h is not None else "" for h in all_rows[0]]
            rows     = [{headers[i]: (str(row[i]).strip() if row[i] is not None else "") for i in range(len(headers))} for row in all_rows[1:]]
        else:
            return jsonify({"error": "Unsupported file type"}), 400

        if not rows:
            return jsonify({"error": "File contains no data rows", "headers": headers}), 400

        detected_column, text_columns = detect_review_columns(headers, rows)

        if not detected_column:
            return jsonify({
                "error": "Could not detect a review/comment column automatically. "
                         "Please select the correct column manually.",
                "headers": headers,
                "rows": rows,
                "count": len(rows),
                "detected_column": None,
                "text_columns": []
            })

        log.info("Column detection — headers=%s detected=%s candidates=%s",
                 headers, detected_column, text_columns)

        return jsonify({
            "headers": headers,
            "rows": rows,
            "count": len(rows),
            "detected_column": detected_column,
            "text_columns": text_columns
        })

    except Exception as e:
        log.exception("PARSE ERROR")
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyse", methods=["POST"])
def analyse():
    """Single-review analysis — kept for the /analyse single-text use case."""
    try:
        data       = request.get_json(force=True, silent=True) or {}
        text       = (data.get("text") or "").strip()[:4900]
        batch_id   = data.get("batch_id", "web")
        session_id = get_session_id()

        if not text or len(text) < 3:
            return jsonify({"error": "text required (min 3 characters)"}), 400

        c    = comp()
        lr   = c.detect_dominant_language(Text=text[:300])
        lang = lr['Languages'][0]['LanguageCode'] if lr.get('Languages') else 'en'
        if lang not in SUPPORTED_LANGUAGES:
            lang = 'en'

        sr   = c.detect_sentiment(Text=text, LanguageCode=lang)
        sent = sr["Sentiment"]
        raw  = sr["SentimentScore"]

        scores = {
            "POSITIVE": Decimal(str(round(raw["Positive"]*100,1))),
            "NEGATIVE": Decimal(str(round(raw["Negative"]*100,1))),
            "NEUTRAL":  Decimal(str(round(raw["Neutral"] *100,1))),
            "MIXED":    Decimal(str(round(raw["Mixed"]   *100,1))),
        }
        confidence  = Decimal(str(round(raw[sent.title()]*100,1)))
        pr          = c.detect_key_phrases(Text=text, LanguageCode=lang)
        key_phrases = [p["Text"] for p in pr.get("KeyPhrases",[]) if p.get("Score",0)>0.90][:8]
        er          = c.detect_entities(Text=text, LanguageCode=lang)
        entities    = [{"text":e["Text"],"type":e["Type"],"score":Decimal(str(round(e["Score"]*100,1)))} for e in er.get("Entities",[]) if e.get("Score",0)>0.90][:8]

        record = {
            "id": str(uuid.uuid4()), "session_id": session_id, "batch_id": batch_id,
            "text": text[:500], "sentiment": sent, "scores": scores,
            "confidence": confidence, "key_phrases": key_phrases, "entities": entities,
            "language": lang, "timestamp": datetime.utcnow().isoformat(), "ttl": get_ttl(),
        }
        tbl().put_item(Item=record)

        return jsonify({
            "id": record["id"], "text": record["text"], "sentiment": sent,
            "confidence": float(confidence), "scores": {k:float(v) for k,v in scores.items()},
            "key_phrases": key_phrases,
            "entities": [{"text":e["text"],"type":e["type"],"score":float(e["score"])} for e in entities],
            "language": lang, "timestamp": record["timestamp"],
        })
    except Exception as e:
        log.exception("ANALYSE ERROR")
        return jsonify({"error": str(e)}), 500


@app.route("/api/analyse-batch", methods=["POST"])
def analyse_batch():
    """
    Batch analysis using AWS Comprehend Batch APIs.

    Instead of 3 API calls PER REVIEW (sentiment, key phrases, entities),
    this sends up to 25 reviews in ONE call per operation:

      - BatchDetectDominantLanguage  (1 call for up to 25 texts)
      - BatchDetectSentiment         (1 call per language group)
      - BatchDetectKeyPhrases        (1 call per language group)
      - BatchDetectEntities          (1 call per language group)

    For 25 English reviews: 4 API calls total instead of 75.
    For 4 reviews: 4 API calls instead of 12 — this is the "near instant"
    target for small files.
    """
    try:
        data       = request.get_json(force=True, silent=True) or {}
        raw_texts  = data.get("texts") or []
        batch_id   = data.get("batch_id", "web")
        session_id = get_session_id()

        # ── Validation ──
        texts = [(t or "").strip()[:4900] for t in raw_texts]
        texts = [t for t in texts if len(t) >= 3]

        if not texts:
            return jsonify({"error": "No valid review text in this batch "
                                      "(all entries empty or too short)"}), 400
        if len(texts) > COMPREHEND_BATCH_LIMIT:
            return jsonify({"error": f"Max {COMPREHEND_BATCH_LIMIT} texts per "
                                      f"batch request (got {len(texts)})"}), 400

        c = comp()

        # 1 call — detect language for the whole batch
        lang_resp = c.batch_detect_dominant_language(TextList=texts)
        langs = ['en'] * len(texts)
        for r in lang_resp.get('ResultList', []):
            idx = r['Index']
            if r.get('Languages'):
                lang = r['Languages'][0]['LanguageCode']
                langs[idx] = lang if lang in SUPPORTED_LANGUAGES else 'en'

        # group indices by language so each batch call uses one LanguageCode
        groups = {}
        for i, lang in enumerate(langs):
            groups.setdefault(lang, []).append(i)

        sentiments    = [None] * len(texts)
        key_phrases_l = [[] for _ in texts]
        entities_l    = [[] for _ in texts]

        for lang, idxs in groups.items():
            group_texts = [texts[i] for i in idxs]

            sr = c.batch_detect_sentiment(TextList=group_texts, LanguageCode=lang)
            for r in sr.get('ResultList', []):
                sentiments[idxs[r['Index']]] = r

            pr = c.batch_detect_key_phrases(TextList=group_texts, LanguageCode=lang)
            for r in pr.get('ResultList', []):
                key_phrases_l[idxs[r['Index']]] = [
                    p["Text"] for p in r.get('KeyPhrases', []) if p.get("Score", 0) > 0.90
                ][:8]

            er = c.batch_detect_entities(TextList=group_texts, LanguageCode=lang)
            for r in er.get('ResultList', []):
                entities_l[idxs[r['Index']]] = [
                    {"text": e["Text"], "type": e["Type"], "score": Decimal(str(round(e["Score"]*100,1)))}
                    for e in r.get('Entities', []) if e.get("Score", 0) > 0.90
                ][:8]

        # any indices Comprehend returned as errors (rare) — skip them
        results_out = []
        ts = datetime.utcnow().isoformat()
        for i, text in enumerate(texts):
            sr = sentiments[i]
            if sr is None:
                log.warning("Comprehend returned no sentiment result for index %s, skipping", i)
                continue

            sent = sr['Sentiment']
            raw  = sr['SentimentScore']
            scores = {
                "POSITIVE": Decimal(str(round(raw["Positive"]*100,1))),
                "NEGATIVE": Decimal(str(round(raw["Negative"]*100,1))),
                "NEUTRAL":  Decimal(str(round(raw["Neutral"] *100,1))),
                "MIXED":    Decimal(str(round(raw["Mixed"]   *100,1))),
            }
            confidence = Decimal(str(round(raw[sent.title()]*100,1)))

            record = {
                "id": str(uuid.uuid4()), "session_id": session_id, "batch_id": batch_id,
                "text": text[:500], "sentiment": sent, "scores": scores,
                "confidence": confidence, "key_phrases": key_phrases_l[i],
                "entities": entities_l[i], "language": langs[i],
                "timestamp": ts, "ttl": get_ttl(),
            }
            tbl().put_item(Item=record)

            results_out.append({
                "id": record["id"], "text": record["text"], "sentiment": sent,
                "confidence": float(confidence), "scores": {k: float(v) for k, v in scores.items()},
                "key_phrases": key_phrases_l[i],
                "entities": [{"text": e["text"], "type": e["type"], "score": float(e["score"])} for e in entities_l[i]],
                "language": langs[i], "timestamp": record["timestamp"],
            })

        log.info("Batch analysed %s/%s reviews (session=%s batch=%s)",
                 len(results_out), len(raw_texts), session_id, batch_id)

        return jsonify({"results": results_out, "processed": len(results_out), "skipped": len(raw_texts) - len(results_out)})

    except Exception as e:
        log.exception("ANALYSE-BATCH ERROR")
        return jsonify({"error": str(e)}), 500


@app.route("/api/history")
def history():
    """Return only THIS session's results."""
    try:
        session_id = get_session_id()
        response   = tbl().scan(
            FilterExpression="session_id = :sid",
            ExpressionAttributeValues={":sid": session_id}
        )
        items = response.get("Items", [])
        items.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return jsonify({"history": fix(items[:100])})
    except Exception as e:
        log.exception("HISTORY ERROR")
        return jsonify({"history": [], "error": str(e)})


@app.route("/api/clear-session", methods=["POST", "DELETE"])
def clear_session():
    """
    Delete all DynamoDB records belonging to the current session.

    Called when:
      - user clicks "New Analysis"
      - browser tab/window is closed (via navigator.sendBeacon)

    DynamoDB TTL (24h) remains as a fallback for any records that
    somehow aren't cleaned up by this endpoint.
    """
    try:
        session_id = get_session_id()
        response = tbl().scan(
            FilterExpression="session_id = :sid",
            ExpressionAttributeValues={":sid": session_id},
            ProjectionExpression="id"
        )
        items = response.get("Items", [])

        with tbl().batch_writer() as batch:
            for item in items:
                batch.delete_item(Key={"id": item["id"]})

        # rotate to a fresh session id so the new "session" starts clean
        session.pop('session_id', None)
        new_sid = get_session_id()

        log.info("Cleared %s records for session %s -> new session %s", len(items), session_id, new_sid)
        return jsonify({"ok": True, "deleted": len(items), "session_id": new_sid})

    except Exception as e:
        log.exception("CLEAR-SESSION ERROR")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/upload", methods=["POST"])
def upload():
    """Upload raw file to S3 — triggers Lambda. Path includes session_id for isolation."""
    try:
        f          = request.files.get("file")
        batch_id   = request.form.get("batch_id","web")
        session_id = get_session_id()
        if f:
            s3c().put_object(Bucket=S3_BUCKET, Key=f"uploads/{session_id}/{batch_id}/{f.filename}", Body=f.read())
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("UPLOAD WARNING")
        return jsonify({"ok": True})


if __name__ == "__main__":
    print("\n  Review Analyser running at: http://localhost:5000\n")
    app.run(debug=False, port=5000, host="0.0.0.0")