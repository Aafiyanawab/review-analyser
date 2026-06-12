from flask import Flask, request, jsonify, session
from flask_cors import CORS
import boto3
from decimal import Decimal
import uuid
from datetime import datetime
import io, os, time
from dotenv import load_dotenv

load_dotenv()

AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY", "")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_KEY", "")
AWS_REGION     = os.environ.get("AWS_REGION", "us-east-1")
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE", "review-results")
S3_BUCKET      = os.environ.get("S3_BUCKET", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET", "change-this-in-production")
DATA_TTL_HOURS = int(os.environ.get("DATA_TTL_HOURS", "24"))

SUPPORTED_LANGUAGES = ['en','hi','de','fr','es','it','ja','ko','zh','ar','pt']

app = Flask(__name__)
app.secret_key = SESSION_SECRET
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

def detect_language(text):
    try:
        r    = comp().detect_dominant_language(Text=text[:300])
        lang = r['Languages'][0]['LanguageCode']
        return lang if lang in SUPPORTED_LANGUAGES else 'en'
    except:
        return 'en'

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

@app.route("/api/parse", methods=["POST"])
def parse():
    try:
        f = request.files.get("file")
        if not f: return jsonify({"error": "no file"}), 400
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
            if not all_rows: return jsonify({"error": "Empty file"}), 400
            headers  = [str(h) if h is not None else "" for h in all_rows[0]]
            rows     = [{headers[i]: (str(row[i]) if row[i] is not None else "") for i in range(len(headers))} for row in all_rows[1:]]
        else:
            return jsonify({"error": "Unsupported file type"}), 400
        return jsonify({"headers": headers, "rows": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/analyse", methods=["POST"])
def analyse():
    try:
        data       = request.get_json(force=True, silent=True) or {}
        text       = (data.get("text") or "").strip()[:4900]
        batch_id   = data.get("batch_id", "web")
        session_id = get_session_id()
        if not text: return jsonify({"error": "text required"}), 400

        c    = comp()
        lang = detect_language(text)
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
        return jsonify({"error": str(e)}), 500

@app.route("/api/history")
def history():
    try:
        session_id = get_session_id()
        response   = tbl().scan(
            FilterExpression="session_id = :sid",
            ExpressionAttributeValues={":sid": session_id}
        )
        items = response.get("Items", [])
        items.sort(key=lambda x: x.get("timestamp",""), reverse=True)
        return jsonify({"history": fix(items[:100])})
    except Exception as e:
        return jsonify({"history": [], "error": str(e)})

@app.route("/api/upload", methods=["POST"])
def upload():
    try:
        f          = request.files.get("file")
        batch_id   = request.form.get("batch_id","web")
        session_id = get_session_id()
        if f:
            s3c().put_object(Bucket=S3_BUCKET, Key=f"uploads/{session_id}/{batch_id}/{f.filename}", Body=f.read())
        return jsonify({"ok": True})
    except:
        return jsonify({"ok": True})

if __name__ == "__main__":
    print("\n  Review Analyser running at: http://localhost:5000\n")
    app.run(debug=False, port=5000, host="0.0.0.0")