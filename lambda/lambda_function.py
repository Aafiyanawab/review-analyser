"""
Lambda function — triggered by S3 upload.
Processes entire CSV/Excel file automatically.
Adds TTL so DynamoDB auto-deletes records after 24 hours.
Supports multilingual review detection.
"""
import json, boto3, csv, io, os, uuid, time
from decimal import Decimal
from datetime import datetime

REGION         = os.environ.get('AWS_REGION', 'us-east-1')
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE', 'review-results')
LANG_DEFAULT   = os.environ.get('COMPREHEND_LANGUAGE', 'en')
TTL_HOURS      = int(os.environ.get('DATA_TTL_HOURS', '24'))
SUPPORTED_LANG = ['en','hi','de','fr','es','it','ja','ko','zh','ar','pt']

comprehend = boto3.client('comprehend', region_name=REGION)
dynamodb   = boto3.resource('dynamodb', region_name=REGION)
s3         = boto3.client('s3', region_name=REGION)
table      = dynamodb.Table(DYNAMODB_TABLE)

def detect_language(text):
    try:
        r = comprehend.detect_dominant_language(Text=text[:300])
        l = r['Languages'][0]['LanguageCode']
        return l if l in SUPPORTED_LANG else 'en'
    except:
        return 'en'

def analyse_review(text, session_id, batch_id):
    text = text.strip()[:4900]
    if not text or len(text) < 3:
        return None
    lang = detect_language(text)
    sr   = comprehend.detect_sentiment(Text=text, LanguageCode=lang)
    sent = sr['Sentiment']
    raw  = sr['SentimentScore']
    scores = {
        'POSITIVE': Decimal(str(round(raw['Positive']*100,1))),
        'NEGATIVE': Decimal(str(round(raw['Negative']*100,1))),
        'NEUTRAL':  Decimal(str(round(raw['Neutral'] *100,1))),
        'MIXED':    Decimal(str(round(raw['Mixed']   *100,1))),
    }
    confidence  = Decimal(str(round(raw[sent.title()]*100,1)))
    pr          = comprehend.detect_key_phrases(Text=text, LanguageCode=lang)
    key_phrases = [p['Text'] for p in pr.get('KeyPhrases',[]) if p.get('Score',0)>0.90][:8]
    er          = comprehend.detect_entities(Text=text, LanguageCode=lang)
    entities    = [{'text':e['Text'],'type':e['Type'],'score':Decimal(str(round(e['Score']*100,1)))} for e in er.get('Entities',[]) if e.get('Score',0)>0.90][:8]
    record = {
        'id':str(uuid.uuid4()), 'session_id':session_id, 'batch_id':batch_id,
        'text':text[:500], 'sentiment':sent, 'scores':scores, 'confidence':confidence,
        'key_phrases':key_phrases, 'entities':entities, 'language':lang,
        'timestamp':datetime.utcnow().isoformat(), 'source':'lambda',
        'ttl': int(time.time()) + (TTL_HOURS * 3600),
    }
    table.put_item(Item=record)
    return record

REVIEW_CANDIDATES = ['review','reviews','review_text','review text','comment','comments',
                     'feedback','customer_feedback','customer_review','customer review',
                     'opinion','description','text','content']
EXCLUDE_PATTERNS = ['id','sku','product','category','rating','score','price','date',
                     'qty','quantity','stock','code','status','name','type','brand']

def find_review_column(headers, rows=None):
    # Step 1 — exact known names (case-insensitive)
    for h in headers:
        if h.lower().strip() in REVIEW_CANDIDATES:
            return h
    # Step 2 — heuristic: longest average text column, excluding ID/SKU/product/rating/date-like headers
    if rows:
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
            candidates.sort(key=lambda x: -x[1])
            return candidates[0][0]
    return headers[0] if headers else None

def lambda_handler(event, context):
    # S3 trigger
    if 'Records' in event and event['Records'][0].get('eventSource') == 'aws:s3':
        processed = errors = 0
        for rec in event['Records']:
            bucket   = rec['s3']['bucket']['name']
            key      = rec['s3']['object']['key']
            parts    = key.split('/')
            session_id = parts[1] if len(parts) >= 3 else 'lambda'
            batch_id   = parts[2] if len(parts) >= 4 else 'lambda'
            filename   = parts[-1].lower()
            try:
                file_bytes = s3.get_object(Bucket=bucket, Key=key)['Body'].read()
                if filename.endswith('.csv'):
                    reader  = csv.DictReader(io.StringIO(file_bytes.decode('utf-8', errors='replace')))
                    rows    = list(reader)
                    headers = list(reader.fieldnames or [])
                elif filename.endswith(('.xlsx','.xls')):
                    import openpyxl
                    wb       = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
                    ws       = wb.active
                    all_rows = list(ws.iter_rows(values_only=True))
                    headers  = [str(h) if h else '' for h in all_rows[0]]
                    rows     = [{headers[i]:(str(r[i]) if r[i] else '') for i in range(len(headers))} for r in all_rows[1:]]
                else:
                    continue
                col = find_review_column(headers, rows)
                if not col: continue
                for row in rows:
                    try:
                        if analyse_review((row.get(col) or ''), session_id, batch_id):
                            processed += 1
                    except Exception as e:
                        print(f'Row error: {e}'); errors += 1
            except Exception as e:
                print(f'File error: {e}'); errors += 1
        return {'statusCode':200,'body':json.dumps({'processed':processed,'errors':errors})}

    # API Gateway trigger (kept for direct API calls)
    method = event.get('httpMethod','')
    path   = event.get('path','')
    CORS   = {'Access-Control-Allow-Origin':'*','Content-Type':'application/json'}
    def respond(s,b): return {'statusCode':s,'headers':CORS,'body':json.dumps(b)}
    if method=='OPTIONS': return {'statusCode':200,'headers':CORS,'body':''}
    return respond(404, {'error': 'route not found'})