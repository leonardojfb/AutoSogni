from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from app.wavespeed.queue import QueueStatus, WaveSpeedQueue, WaveSpeedQueueItem
from app.wavespeed.validation import TERMINAL_STATUSES, build_reference_video_payload


class WaveSpeedQueueRunner:
    def __init__(
        self,
        client,
        *,
        upload_file: Callable[[Path], dict[str, Any]],
        save_output: Callable[[object, str, str], str],
        on_update: Callable[[WaveSpeedQueueItem], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.upload_file = upload_file
        self.save_output = save_output
        self.on_update = on_update
        self.sleep = sleep

    def _notify(self, item: WaveSpeedQueueItem) -> None:
        if self.on_update:
            self.on_update(item)

    @staticmethod
    def _price(value: dict[str, Any]) -> float | None:
        raw = value.get("discounted_price", value.get("price"))
        try:
            return float(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    def run(self, queue: WaveSpeedQueue, cancel_event=None) -> WaveSpeedQueue:
        frame_url: str | None = None
        uploaded_videos: dict[str, str] = {}

        for item in queue.items:
            if queue.paused or (cancel_event is not None and cancel_event.is_set()):
                break
            if item.status not in {QueueStatus.PENDING, QueueStatus.PAUSED}:
                continue

            try:
                item.status = QueueStatus.RUNNING
                item.error = ""
                self._notify(item)

                if frame_url is None:
                    frame_url = self.upload_file(Path(queue.frame_path))["download_url"]
                video_key = str(Path(item.video_path).resolve())
                video_url = uploaded_videos.get(video_key)
                if video_url is None:
                    video_url = self.upload_file(Path(item.video_path))["download_url"]
                    uploaded_videos[video_key] = video_url

                payload = build_reference_video_payload(
                    prompt=item.prompt,
                    reference_images=[frame_url],
                    reference_videos=[video_url],
                    resolution=item.resolution,
                    aspect_ratio=item.aspect_ratio,
                    duration=item.duration,
                    enable_prompt_expansion=item.enable_prompt_expansion,
                    enable_audio=item.enable_audio,
                    seed=item.seed,
                    enable_sync_mode=False,
                    enable_base64_output=False,
                )
                try:
                    item.price = self._price(self.client.estimate_price(payload))
                    self._notify(item)
                except Exception:
                    item.price = None

                if item.task_id:
                    try:
                        prediction = self.client.get_result(item.task_id)
                    except Exception as exc:
                        item.status = QueueStatus.FAILED
                        item.error = f"No se pudo recuperar el task {item.task_id}: {exc}"
                        self._notify(item)
                        continue
                else:
                    prediction = self.client.submit(payload)
                    item.task_id = prediction.id
                    self._notify(item)
                if prediction.status in TERMINAL_STATUSES:
                    result = prediction
                else:
                    result = self.client.poll_result(
                        prediction.id,
                        sleep=self.sleep,
                        on_update=lambda update: self._notify_status(item, update),
                        cancel_event=cancel_event,
                    )

                if result.status == "completed":
                    if result.outputs:
                        item.output_file = self.save_output(result.outputs[0], queue.output_dir, result.id)
                    item.status = QueueStatus.COMPLETED
                    item.error = ""
                else:
                    item.status = QueueStatus.FAILED
                    item.error = result.error or f"WaveSpeed task ended with status: {result.status}"
                self._notify(item)
            except Exception as exc:
                if cancel_event is not None and cancel_event.is_set():
                    item.status = QueueStatus.PAUSED
                else:
                    item.status = QueueStatus.FAILED
                item.error = str(exc)
                self._notify(item)

        return queue

    def estimate_prices(self, queue: WaveSpeedQueue) -> float:
        frame_url: str | None = None
        uploaded_videos: dict[str, str] = {}
        total = 0.0
        for item in queue.items:
            try:
                if frame_url is None:
                    frame_url = self.upload_file(Path(queue.frame_path))["download_url"]
                video_key = str(Path(item.video_path).resolve())
                video_url = uploaded_videos.get(video_key)
                if video_url is None:
                    video_url = self.upload_file(Path(item.video_path))["download_url"]
                    uploaded_videos[video_key] = video_url
                payload = build_reference_video_payload(
                    prompt=item.prompt,
                    reference_images=[frame_url],
                    reference_videos=[video_url],
                    resolution=item.resolution,
                    aspect_ratio=item.aspect_ratio,
                    duration=item.duration,
                    enable_prompt_expansion=item.enable_prompt_expansion,
                    enable_audio=item.enable_audio,
                    seed=item.seed,
                    enable_sync_mode=False,
                    enable_base64_output=False,
                )
                item.price = self._price(self.client.estimate_price(payload))
                item.error = ""
                if item.price is not None:
                    total += item.price
            except Exception as exc:
                item.price = None
                item.error = f"No se pudo estimar: {exc}"
            self._notify(item)
        return round(total, 8)

    def _notify_status(self, item: WaveSpeedQueueItem, prediction) -> None:
        item.task_id = prediction.id
        if prediction.error:
            item.error = prediction.error
        self._notify(item)
