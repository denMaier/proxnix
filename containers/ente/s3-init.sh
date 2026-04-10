#!/bin/sh
set -eu

export AWS_ACCESS_KEY_ID="$(cat /run/secrets/s3_user)"
export AWS_SECRET_ACCESS_KEY="$(cat /run/secrets/s3_pass)"

until aws --endpoint-url http://ente-versitygw:3200 s3api list-buckets >/dev/null 2>&1; do
  echo "Waiting for VersityGW..."
  sleep 1
done

aws --endpoint-url http://ente-versitygw:3200 s3api create-bucket --bucket b2-eu-cen || true
aws --endpoint-url http://ente-versitygw:3200 s3api create-bucket --bucket wasabi-eu-central-2-v3 || true
aws --endpoint-url http://ente-versitygw:3200 s3api create-bucket --bucket scw-eu-fr-v3 || true
