# scripts/set_cache_control_b2.py
import boto3
from django.conf import settings

session = boto3.session.Session(
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=getattr(settings, 'AWS_S3_REGION_NAME', None),
)
s3 = session.client('s3', endpoint_url=getattr(settings, 'AWS_S3_ENDPOINT_URL', None))

bucket = settings.AWS_STORAGE_BUCKET_NAME
paginator = s3.get_paginator('list_objects_v2')

for page in paginator.paginate(Bucket=bucket):
    for obj in page.get('Contents', []):
        key = obj['Key']
        # Copy object onto itself replacing metadata and setting CacheControl
        s3.copy_object(
            Bucket=bucket,
            CopySource={'Bucket': bucket, 'Key': key},
            Key=key,
            MetadataDirective='REPLACE',
            CacheControl='public, max-age=31536000',
        )
        print('Updated', key)