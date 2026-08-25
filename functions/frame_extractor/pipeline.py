"""Orquestación: download → extract/preprocess → upload paralelo."""

from __future__ import annotations

from naming import frames_prefix_for_execution, parse_session_and_eye
from storage import FrameStore, ParallelUploader
from preprocess import FramePreprocessor
from config import ExtractorConfig
from dataclasses import dataclass
import cv2
import os

@dataclass(frozen=True)
class ExtractionJob:
    bucket: str
    key: str
    execution_name: str

@dataclass(frozen=True)
class ExtractionResult:
    session_id: str
    eye: str
    fps: float
    total_frames: int
    frames_bucket: str
    frames_prefix: str
    execution_name: str
    source_video: str

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "eye": self.eye,
            "fps": self.fps,
            "total_frames": self.total_frames,
            "frames_bucket": self.frames_bucket,
            "frames_prefix": self.frames_prefix,
            "execution_name": self.execution_name,
            "source_video": self.source_video,
        }

class FrameExtractionPipeline:
    def __init__(
        self,
        config: ExtractorConfig,
        store: FrameStore,
        preprocessor: FramePreprocessor | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._preprocessor = preprocessor or FramePreprocessor(config)

    def run(self, job: ExtractionJob) -> ExtractionResult:
        session_id, eye = parse_session_and_eye(job.key)
        frames_prefix = frames_prefix_for_execution(job.execution_name)
        local_path = os.path.join(
            self._config.tmp_dir,
            f"{job.execution_name}_{job.key.rsplit('/', 1)[-1]}",
        )

        self._store.download_video(job.bucket, job.key, local_path)
        try:
            total_frames, fps = self._extract_and_upload(
                local_path=local_path,
                source=f"s3://{job.bucket}/{job.key}",
                frames_prefix=frames_prefix,
                session_id=session_id,
                eye=eye,
            )
        finally:
            if os.path.exists(local_path):
                os.remove(local_path)

        return ExtractionResult(
            session_id=session_id,
            eye=eye,
            fps=fps,
            total_frames=total_frames,
            frames_bucket=self._config.frames_bucket,
            frames_prefix=frames_prefix,
            execution_name=job.execution_name,
            source_video=f"s3://{job.bucket}/{job.key}",
        )

    def _extract_and_upload(
        self,
        local_path: str,
        source: str,
        frames_prefix: str,
        session_id: str,
        eye: str,
    ) -> tuple[int, float]:
        cap = cv2.VideoCapture(local_path)
        if not cap.isOpened():
            raise RuntimeError(f"OpenCV no pudo abrir el video {source}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frame_index = 0
        try:
            with ParallelUploader(self._store, self._config.upload_workers) as uploader:
                while True:
                    ok, frame = cap.read()
                    if not ok:
                        break
                    encoded = self._preprocessor.encode(frame, frame_index, fps)
                    uploader.submit(frames_prefix, encoded, session_id, eye)
                    frame_index += 1
        finally:
            cap.release()

        if frame_index == 0:
            raise RuntimeError(f"El video {source} no contiene frames legibles")
        return frame_index, fps
