from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

from app.wavespeed.queue import QueueStatus, WaveSpeedQueue, WaveSpeedQueueItem
from app.wavespeed.validation import (
    MODEL_ID, SEEDANCE_MODEL_ID, TERMINAL_STATUSES,
    build_reference_video_payload, build_seedance_payload,
)


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

    @staticmethod
    def _payload(item: WaveSpeedQueueItem, references: dict[str, list[str]]) -> dict[str, Any]:
        if item.model_id == SEEDANCE_MODEL_ID:
            return build_seedance_payload(
                prompt=item.prompt, **references,
                resolution=item.resolution, aspect_ratio=item.aspect_ratio, duration=item.duration,
                enable_web_search=item.enable_prompt_expansion, generate_audio=item.enable_audio,
                safety_checker=item.safety_checker,
            )
        if item.model_id != MODEL_ID:
            raise ValueError(f"Unsupported queue model: {item.model_id}")
        return build_reference_video_payload(
            prompt=item.prompt, **references,
            resolution=item.resolution, aspect_ratio=item.aspect_ratio, duration=item.duration,
            enable_prompt_expansion=item.enable_prompt_expansion, enable_audio=item.enable_audio,
            seed=item.seed, enable_sync_mode=False, enable_base64_output=False,
        )

    @staticmethod
    def _references(item: WaveSpeedQueueItem, queue: WaveSpeedQueue) -> dict[str, list[str]]:
        if not item.video_path or item.reference_images or item.reference_videos or item.reference_audios:
            return {"reference_images": list(item.reference_images),
                    "reference_videos": list(item.reference_videos),
                    "reference_audios": list(item.reference_audios)}
        return {"reference_images": [queue.frame_path] if queue.frame_path else [],
                "reference_videos": [item.video_path] if item.video_path else [],
                "reference_audios": []}

    def _prepare_payload(self, item: WaveSpeedQueueItem, queue: WaveSpeedQueue,
                         uploaded: dict[tuple[str, int, int], str]) -> dict[str, Any]:
        references = self._references(item, queue)
        self._payload(item, references)
        remote: dict[str, list[str]] = {}
        for kind, paths in references.items():
            remote[kind] = []
            for name in paths:
                path = Path(name)
                try:
                    stat = path.stat()
                    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
                except OSError:
                    key = (str(path.resolve()), -1, -1)
                if key not in uploaded:
                    uploaded[key] = self.upload_file(path)["download_url"]
                remote[kind].append(uploaded[key])
        return self._payload(item, remote)

    def _estimate(self, payload: dict[str, Any], model_id: str) -> dict[str, Any]:
        if model_id == MODEL_ID:
            return self.client.estimate_price(payload)
        return self.client.estimate_price(payload, model_id=model_id)

    def _submit(self, payload: dict[str, Any], model_id: str):
        if model_id == MODEL_ID:
            return self.client.submit(payload)
        return self.client.submit(payload, model_id=model_id)

    def run(self, queue: WaveSpeedQueue, cancel_event=None, *, parallel: bool = False) -> WaveSpeedQueue:
        if parallel:
            return self._run_parallel(queue, cancel_event)
        uploaded: dict[tuple[str, int, int], str] = {}

        for item in queue.items:
            if queue.paused or (cancel_event is not None and cancel_event.is_set()):
                break
            if item.status not in {QueueStatus.PENDING, QueueStatus.PAUSED}:
                continue

            try:
                item.status = QueueStatus.RUNNING
                item.error = ""
                self._notify(item)

                if item.task_id:
                    try:
                        prediction = self.client.get_result(item.task_id)
                    except Exception as exc:
                        item.status = QueueStatus.FAILED
                        item.error = f"No se pudo recuperar el task {item.task_id}: {exc}"
                        self._notify(item)
                        continue
                else:
                    payload = self._prepare_payload(item, queue, uploaded)
                    try:
                        item.price = self._price(self._estimate(payload, item.model_id))
                        self._notify(item)
                    except Exception:
                        item.price = None
                    prediction = self._submit(payload, item.model_id)
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

    def _run_parallel(self, queue: WaveSpeedQueue, cancel_event=None) -> WaveSpeedQueue:
        if queue.paused or (cancel_event is not None and cancel_event.is_set()):
            return queue
        uploaded: dict[tuple[str, int, int], str] = {}
        prepared: list[tuple[WaveSpeedQueueItem, dict[str, Any]]] = []

        for item in queue.items:
            if item.status not in {QueueStatus.PENDING, QueueStatus.PAUSED}:
                continue
            try:
                item.status = QueueStatus.RUNNING
                item.error = ""
                self._notify(item)
                payload = self._prepare_payload(item, queue, uploaded) if not item.task_id else {}
                if not item.task_id:
                    try:
                        item.price = self._price(self._estimate(payload, item.model_id))
                    except Exception:
                        item.price = None
                self._notify(item)
                prepared.append((item, payload))
            except Exception as exc:
                item.status = QueueStatus.FAILED
                item.error = str(exc)
                self._notify(item)

        def submit(entry):
            item, payload = entry
            if item.task_id:
                prediction = self.client.get_result(item.task_id)
            else:
                prediction = self._submit(payload, item.model_id)
                item.task_id = prediction.id
            self._notify(item)
            return item, prediction

        submitted: list[tuple[WaveSpeedQueueItem, Any]] = []
        with ThreadPoolExecutor(max_workers=max(1, len(prepared))) as executor:
            futures = [executor.submit(submit, entry) for entry in prepared]
            for future in as_completed(futures):
                try:
                    submitted.append(future.result())
                except Exception as exc:
                    item = prepared[futures.index(future)][0]
                    item.status = QueueStatus.FAILED
                    item.error = str(exc)
                    self._notify(item)

        def finish(entry):
            item, prediction = entry
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

        with ThreadPoolExecutor(max_workers=max(1, len(submitted))) as executor:
            futures = [executor.submit(finish, entry) for entry in submitted]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    item = submitted[futures.index(future)][0]
                    item.status = QueueStatus.PAUSED if cancel_event is not None and cancel_event.is_set() else QueueStatus.FAILED
                    item.error = str(exc)
                    self._notify(item)
        return queue

    def estimate_prices(self, queue: WaveSpeedQueue) -> float:
        uploaded: dict[tuple[str, int, int], str] = {}
        total = 0.0
        for item in queue.items:
            try:
                payload = self._prepare_payload(item, queue, uploaded)
                item.price = self._price(self._estimate(payload, item.model_id))
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
