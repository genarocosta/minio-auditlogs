# Minio Audit Logs

This is a simple service that can handle the ingest for the MinIO audit webhook, push them to a elasticsearch with a 90days retention. The service also allows the search from the MinIO interface.

The service also pushes the logs to another minio, using the parquet format. Generating one file per day. That can be used as a long-term storage. 

The minio that will send the logs and query from the interface can be configure as bellow:

```
MINIO_AUDIT_WEBHOOK_ENABLE_1="on"
MINIO_AUDIT_WEBHOOK_ENDPOINT_1="http://audit-logger:8000/api/ingest?token=aca1fefd79bdcab38c095ad495768394"
MINIO_LOG_QUERY_URL="http://audit-logger:8000"
MINIO_LOG_QUERY_AUTH_TOKEN="aca1fefd79bdcab38c095ad495768394"
```

The [compose.yml](compose.yml) have a complete example using the environment variables from the [.env](.env) environment file.

Make sure that all *AUTH_TOKEN* values have the same value.

The audit service use the environment variables bellow for its configuration.

```
# Elasticsearch configuration
ELASTIC_USERNAME="elastic"
ELASTIC_PASSWORD="changeme2025el"
ELASTIC_URL="http://elasticsearch:9200"

# the prefix for the elasticsearch indices
INDEX_PREFIX="audit_logs"

# these config are used to start a different minio for receiving the raw audit
MINIO_ENDPOINT="s3store:9000"
MINIO_ACCESS_KEY="minioadmin"
MINIO_SECRET_KEY="changeme2025s3"

# token config for the audit-logger (must be the same used on the minio config)
AUTH_TOKEN="aca1fefd79bdcab38c095ad495768394"
```

## Design considerations

- I used a different thread to push the logs to elasticsearch and the second minio, so the thread that receive the ingest request will not wait for any processing.
- I coded all in one python file, for simplicity.
- I chose to write the full ingest also on another minio, for long term storage.
- The search allows only one filter. That is not my choice. The MinIO interface just send one filter. So if you provide the bucket on the filter and also the object location, it sends only the bucket. It would be nice if the MinIO sends all the filter values, but the change must be done on the MinIO code.
