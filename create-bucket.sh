#!/bin/sh

until mc alias set local http://127.0.0.1:9000 ${MINIO_ROOT_USER} ${MINIO_ROOT_PASSWORD} ; do
  echo "Waiting for MinIO..."
  sleep 2
done

# Check if the bucket exists
if mc ls local/audit-logs ; then
  echo "Bucket already exists"
else
  mc mb local/audit-logs
  echo "Bucket 'audit-logs' created"
fi
