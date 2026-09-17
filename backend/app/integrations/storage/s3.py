from typing import Any, BinaryIO

import boto3  # type: ignore[import-untyped]
from botocore.config import Config  # type: ignore[import-untyped]

from app.core.config import Settings


class Storage:
    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self.client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name="us-east-1",
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"max_attempts": 2},
                s3={"addressing_style": "path"},
            ),
        )

    def put(self, key: str, stream: BinaryIO, mime: str) -> None:
        self.client.upload_fileobj(stream, self.bucket, key, ExtraArgs={"ContentType": mime})

    def read(self, key: str) -> bytes:
        body = self.client.get_object(Bucket=self.bucket, Key=key)["Body"]
        try:
            return bytes(body.read())
        finally:
            body.close()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)
