"""Lambda 1 entrypoint: wiring de config/store/pipeline. Contrato ASL intacto."""

from __future__ import annotations

from pipeline import ExtractionJob, FrameExtractionPipeline
from config import ExtractorConfig
from storage import S3FrameStore

_config = ExtractorConfig.from_env()
_pipeline = FrameExtractionPipeline(
    config=_config,
    store=S3FrameStore(_config.frames_bucket),
)

def lambda_handler(event, context):
    result = _pipeline.run(
        ExtractionJob(
            bucket=event["bucket"],
            key=event["key"],
            execution_name=event["execution_name"],
        )
    )
    return result.to_dict()
