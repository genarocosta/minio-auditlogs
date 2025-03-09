from flask import Flask, request, Response
import requests, json, datetime, threading, time
import pandas as pd
import pyarrow.parquet as pq
import pyarrow as pa
from minio import Minio
import io, sys, os 
import urllib3
from elasticsearch import Elasticsearch, helpers
import logging
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

ELASTICSEARCH_URL = os.environ.get('ELASTIC_URL', '')
INDEX_NAME = os.environ.get('INDEX_PREFIX', 'audit_log_events')
MINIO_ENDPOINT = os.environ.get('MINIO_ENDPOINT', '')
ACCESS_KEY = os.environ.get('MINIO_ACCESS_KEY', '')
SECRET_KEY = os.environ.get('MINIO_SECRET_KEY', '')
AUTH_TOKEN = os.environ.get('AUTH_TOKEN', '')
es_user = os.getenv("ELASTIC_USER", "elastic")
es_password = os.getenv("ELASTIC_PASSWORD", "")
es = Elasticsearch([ELASTICSEARCH_URL], request_timeout=30, basic_auth=(es_user, es_password))

vars = ['ELASTIC_URL', 'INDEX_PREFIX', 'MINIO_ENDPOINT', 'MINIO_ACCESS_KEY', 'MINIO_SECRET_KEY', 'AUTH_TOKEN' ]
for v in vars:
    if os.environ.get(v, '') == '':
        print(f"Missing environment variable {v}")
        sys.exit(1)

http_client = urllib3.PoolManager(timeout=urllib3.Timeout(connect=1.0, read=3.0))
minio_client = Minio(
    MINIO_ENDPOINT,
    access_key=ACCESS_KEY,
    secret_key=SECRET_KEY, 
    secure=MINIO_ENDPOINT[0:5]=='https',
    http_client=http_client
)

index_pattern = f"{INDEX_NAME}-*"
policy_name = f"delete_{INDEX_NAME}_after_90_days"
policy = {
    "policy": {
        "phases": {
            "delete": {
                "min_age": "90d",
                "actions": {
                    "delete": {}
                }
            }
        }
    }
}
index_settings = {
    "settings": {
        "index.lifecycle.name": policy_name
    },
    "mappings": {
        "properties": {
            "timestamp": {"type": "date"}
        }
    }
}
try:
  es.ilm.put_lifecycle(name=policy_name, body=policy)
  es.indices.put_index_template(name="auditlogs_template", body={
    "index_patterns": [index_pattern],
    "template": {
        "settings": index_settings["settings"],
        "mappings": index_settings["mappings"]
    }
  })
except Exception as e:
  print(f"Error config policy: {e}")
  time.sleep(5)
  os._exit(1)

app = Flask(__name__)

def log_to_file(filename, data):
    with open(filename, "a") as f:
        f.write(json.dumps(data, indent=4) + "\n\n")

@app.route("/health", methods=["GET"])
def health():
    global es
    health = es.cluster.health()
    return health['status']

mutex = threading.Lock()
bulk = []
def index(data):
    global bulk, mutex
    with mutex:
        bulk.append(data)   

def save_to_minio(data, bucket_name, prefix):
    global minio_client
    today = datetime.datetime.today().strftime('%Y-%m-%d')
    object_name = f"{prefix}_{today}.parquet"
    df = pd.DataFrame(data)
    buffer = io.BytesIO()
    table = pa.Table.from_pandas(df)
    pq.write_table(table, buffer)
    buffer.seek(0)
    # print('Upload new Parquet data in append mode')
    minio_client.put_object(
        bucket_name, object_name, buffer, length=buffer.getbuffer().nbytes, content_type="application/octet-stream"
    )
    # print(f"Data saved to Minio bucket={bucket_name} object={object_name}")

from pprint import pprint

def search_indexes(params):
    global index_pattern
    query = {'bool': {'must': []}}
    rangePart = { 'range': {'time': {}}}
    if not params.get('timeStart') is None:
        rangePart["range"]["time"]["gte"] = params.get('timeStart')
        query["bool"]["must"].append(rangePart)
    if not params.get('timeEnd') is None:
        rangePart["range"]["time"]["lte"] = params.get('timeEnd')
        if len(query["bool"]["must"]) == 0: # add once
            query["bool"]["must"].append(rangePart) 
    if not params.get('fp') is None:
        fp = params.get('fp')
        k,v = fp.split(':')
        query["bool"]["must"].append({"term": {f"{k}.keyword": v}}) 
    page = int(params.get('pageNo', 0))
    size = int(params.get('pageSize', 10))
    bodySearch = {
            "from": page * size,
            "size": size,
            "sort": []
        }
    if not params.get('timeDesc') is None: 
        bodySearch["sort"].append({"time": "desc"})
    if not params.get('timeAsc') is None:
        bodySearch["sort"].append({"time": "asc"})
    if len(query["bool"]["must"])>0:
        bodySearch['query']= query   
    #pprint(params)
    #pprint(bodySearch)
    response = es.search(
        index=index_pattern,
        body=bodySearch
    )
    rc=0
    result = []
    for hit in response['hits']['hits']:
        result.append(hit['_source'])
        rc+=1
        if rc<2: pprint(hit['_source'])
    #pprint(response)
    #print(f"patter = {index_pattern}")
    return result

def get_datalist(data):
    now = datetime.datetime.now()
    ymd = now.strftime('%Y-%m-%d')
    index = f"{INDEX_NAME}-{ymd}"
    edb = []
    for rec in data:
        try:
            erec = {
                "time": rec["time"],
                "api_name": rec["api"]["name"],
                "time_to_response_ns": rec["api"]["timeToResponseInNS"],
                "remote_host": rec["remotehost"],
                "request_id": rec["requestID"],
                "user_agent": rec["userAgent"],
                "response_status": rec["api"]["status"],
                "response_status_code": rec["api"]["statusCode"],
                "access_key": rec["accessKey"],
                }
            if 'bucket' in rec['api']:
                erec['bucket'] = rec['api']['bucket']
            if 'object' in rec['api']:
                erec['object'] = rec['api']['object']
            if 'request_content_length' in rec['requestHeader']:
                erec['request_content_length'] : rec["requestHeader"]["Content-Length"]
            if 'response_content_length' in rec['responseHeader']:
                erec['response_content_length'] : rec["responseHeader"]["Content-Length"]
            doc = { 
                '_index': index,
                '_op_type': 'index',
                '_source': erec
            }
            edb.append(doc)
        except Exception as e:
            pprint(rec)
            print(f"Fatal error saving bulk errors: {e}")
            time.sleep(5)
            os._exit(1)
    return edb

def process():
    global bulk, mutex
    while True:
        with mutex:
            documents = bulk
            bulk = []
        if len(documents) > 0:
            try:
                start = time.time()
                data = get_datalist(documents)
                regcount, failed = helpers.bulk(es, data)
                #print(f"Bulk inserted success: {regcount} - fail: {len(failed)} - time {time.time()-start:.2f}s")
            except Exception as e:
                print(f"Error: {e}")
                try:
                    save_to_minio(failed, "audit-logs", "fail-auditlogs")
                except Exception as e:
                    print(f"Fatal error saving bulk errors: {e}")
                    time.sleep(5)
                    os._exit(1)
            try:
                save_to_minio(documents, "audit-logs", "auditlogs")
            except Exception as e:
                print(f"Fatal error saving documents: {e}")
                time.sleep(5)
                os._exit(1)
        with mutex:
            bulklen = len(bulk)        
        if bulklen==0: time.sleep(1)
        
def index_single(data):
    global es
    # print(data)
    now = datetime.datetime.now()
    ymd = now.strftime('%Y-%m-%d')
    index = f"{INDEX_NAME}-{ymd}"
    if not es.indices.exists(index=index):
        print(f"Index '{index}' not found. Creating index...")
        es.indices.create(index=index)
    try:
        # print("Writing to elasticsearch index={index}")
        resp = es.index(index=index, document=data)
    except Exception as e:
        print(f"Error: {e}")

@app.route("/api/ingest", methods=["GET", "POST"])
def ingest():
    global AUTH_TOKEN
    if request.args.get('token', 'bad') != AUTH_TOKEN: 
        # print(f"DEBUG AUTH_TOKEN={AUTH_TOKEN} and request.args.get = {request.args.get('token', 'bad')}")
        return Response("Unauthorized", status=401)
    data = request.get_json()
    index(data)
    return Response("OK", status=200)
    
@app.get("/api/query")
def query():
    global AUTH_TOKEN
    if request.args.get('token', 'bad') != AUTH_TOKEN: return Response("Unauthorized", status=401)
    data = search_indexes(request.args)
    return Response(json.dumps(data), status=200, headers={"Content-Type": "application/json"})

'''PROXY_HOST = "http://logsearchapi.dataops.local"
reqno=0        
@app.route("/<path:path>", methods=["GET", "POST"])
def proxy(path):
    global reqno
    url = f"{PROXY_HOST}/{path}"
    
    headers = {key: value for key, value in request.headers if key.lower() != 'host'}
    data = request.get_json() if request.is_json else request.data
    params = request.args.to_dict()
    
    #if path=='api/ingest':
    #    index(data)
    #elif path=='api/query':
    #    data = search_indexes('audit_log_events-*', request.args)
    #    return Response(json.dumps(data), status=200, headers={"Content-Type": "application/json"})

    if request.method == "GET":
        resp = requests.get(url, headers=headers, params=request.args)
    else:
        purl = url
        if request.query_string != "":
            purl+='?'+request.query_string.decode("utf-8")
        resp = requests.post(purl, headers=headers, json=request.get_json() if request.is_json else request.form)
    
    if path!='api/ingest':
        reqno+=1
        log_to_file(f"/tmp/request-{reqno}.log", {
            "timestamp": str(datetime.datetime.now()),
            "method": request.method,
            "path": path,
            "headers": dict(request.headers),
            "parameters": params,
            "body": data.decode() if isinstance(data, bytes) else data
        })
        pprint(data)
        log_to_file(f"/tmp/response-{reqno}.log", {
            "timestamp": str(datetime.datetime.now()),
            "status_code": resp.status_code,
            "headers": dict(resp.headers),
            "body": resp.text
        })

    excluded_headers = ["transfer-encoding", "content-encoding", "content-length"]
    headers = {k: v for k, v in resp.headers.items() if k.lower() not in excluded_headers}
    
    return Response(resp.content, status=resp.status_code, headers=headers)
'''

thread = threading.Thread(target=process, daemon=True)
thread.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
