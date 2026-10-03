import json
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.core.downloader import Downloader
from app.core.job_runner import JobRunner, SogniWorkflowFailure
from app.database.db import Database
from app.database.repositories import CampaignRepository
from app.sogni.client import SogniClient
from app.core.queue_manager import QueueManager


class FakeResponse:
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    def raise_for_status(self) -> None:
        return None

    def iter_bytes(self):
        yield from self.chunks


class FakeHttpClient:
    def stream(self, method: str, url: str, timeout: float):
        assert method == "GET"
        assert url == "https://artifact.example/video.mp4"
        assert timeout == 120.0
        return self

    def __enter__(self):
        return FakeResponse([b"vid", b"eo"])

    def __exit__(self, *args):
        return False


def test_downloader_writes_part_file_then_final_file(tmp_path: Path):
    output = tmp_path / "clip.mp4"

    result = Downloader(http_client=FakeHttpClient()).download("https://artifact.example/video.mp4", output)

    assert result == output
    assert output.read_bytes() == b"video"
    assert not output.with_suffix(".mp4.part").exists()


def test_job_runner_marks_done_only_after_download(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    campaign_id = repo.insert_campaign(
        {
            "name": "Run",
            "status": "READY",
            "model_id": "wan22",
            "model_name": "WAN 2.2",
            "frames_folder": "",
            "prompts_source": "",
            "output_folder": str(tmp_path / "out"),
            "filename_template": "{outfit}__{prompt_id}_{prompt_name}.mp4",
            "organization_mode": "flat",
            "concurrency": 1,
            "settings_json": json.dumps({"duration": 5}),
        }
    )
    frame_path = tmp_path / "frame.png"
    frame_path.write_bytes(b"frame")
    frame_id = repo.insert_frame(campaign_id, str(frame_path), "frame.png", "Pink Dress", "sha")
    prompt_id = repo.insert_prompt(campaign_id, "P01", "Quick Question", "Animate")
    job_id = repo.insert_job(campaign_id, frame_id, prompt_id, 1, "sva:test:0001")

    class FakeSogni:
        def get_image_upload_url(self, job_id: str, frame_path: Path):
            return {"url": "https://upload.example/upload", "fields": {"key": "2026/1/startingImage.png", "Content-Type": "image/png"}}

        def upload_image_to_presigned_post(self, upload: dict, frame_path: Path):
            return {"kind": "image", "url": "https://example.com/frame.png", "contentType": "image/png"}

        def start_image_to_video_workflow(self, **kwargs):
            return {"workflowId": "wf_1", "status": "queued"}

        def read_workflow(self, workflow_id: str):
            return {"workflowId": workflow_id, "status": "completed", "steps": [{"artifacts": [{"url": "https://artifact.example/video.mp4"}]}]}

    runner = JobRunner(repo, FakeSogni(), downloader=Downloader(http_client=FakeHttpClient()), poll_interval=0)

    runner.run(job_id)

    job = repo.get_job(job_id)
    assert job.status == "DONE"
    assert job.workflow_id == "wf_1"
    assert Path(job.output_file).exists()


def test_job_runner_auto_resumes_waiting_workflow_before_continuing():
    class FakeRepo:
        def __init__(self) -> None:
            self.statuses = []

        def set_job_status(self, job_id: int, status: str, last_error: str | None = None) -> None:
            self.statuses.append((job_id, status, last_error))

    class FakeSogni:
        def __init__(self) -> None:
            self.resumed = []

        def read_workflow(self, workflow_id: str):
            if self.resumed:
                return {"workflowId": workflow_id, "status": "completed", "artifacts": [{"url": "https://artifact.example/video.mp4"}]}
            return {"workflowId": workflow_id, "status": "waiting_for_user", "awaitingCostApproval": False}

        def resume_workflow(self, workflow_id: str):
            self.resumed.append(workflow_id)
            return {"workflowId": workflow_id, "status": "running"}

    repo = FakeRepo()
    sogni = FakeSogni()
    runner = JobRunner(repo, sogni, poll_interval=0, max_polls=2)

    artifact_url = runner._wait_for_artifact(9, "wf_paused")

    assert artifact_url == "https://artifact.example/video.mp4"
    assert sogni.resumed == ["wf_paused"]
    assert repo.statuses[0] == (9, "GENERATING", None)


def test_job_runner_does_not_resume_non_retryable_waiting_workflow():
    class FakeRepo:
        def set_job_status(self, job_id: int, status: str, last_error: str | None = None) -> None:
            return None

    class FakeSogni:
        def __init__(self) -> None:
            self.resumed = []

        def read_workflow(self, workflow_id: str):
            return {
                "workflowId": workflow_id,
                "status": "waiting_for_user",
                "events": [{"data": {"errorType": "SAFETY_REJECTED", "retryable": False}}],
            }

        def resume_workflow(self, workflow_id: str):
            self.resumed.append(workflow_id)

    sogni = FakeSogni()
    runner = JobRunner(FakeRepo(), sogni, poll_interval=0, max_polls=2)

    try:
        runner._wait_for_artifact(9, "wf_rejected")
    except SogniWorkflowFailure as exc:
        assert "waiting_for_user" in str(exc)
    else:
        raise AssertionError("Expected non-retryable workflow failure")

    assert sogni.resumed == []


def test_expired_cached_upload_is_replaced_before_workflow_submission(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    frame_path = tmp_path / "frame.png"
    frame_path.write_bytes(b"frame")
    stale_url = "https://uploads.example/startingImage.png?" + urlencode(
        {"X-Amz-Date": "20260917T011515Z", "X-Amz-Expires": "172800"}
    )
    repo.save_cached_upload("sha", {"kind": "image", "url": stale_url, "contentType": "image/png"})

    class FakeSogni:
        def __init__(self):
            self.uploads = 0

        def get_image_upload_url(self, job_id: str, image_path: Path):
            return {"url": "https://upload.example/upload", "fields": {"Content-Type": "image/png"}}

        def upload_image_to_presigned_post(self, upload: dict, image_path: Path):
            self.uploads += 1
            return {"kind": "image", "url": "https://uploads.example/fresh.png", "contentType": "image/png"}

    sogni = FakeSogni()
    runner = JobRunner(repo, sogni)

    result = runner._media_reference_for_frame("sha", 1, frame_path)

    assert result["url"] == "https://uploads.example/fresh.png"
    assert sogni.uploads == 1


def test_cached_upload_with_wrong_content_type_is_replaced(tmp_path: Path):
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    frame_path = tmp_path / "misnamed.jpg"
    frame_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"png payload")
    fresh_url = "https://uploads.example/startingImage.png?" + urlencode(
        {"X-Amz-Date": "20260920T005817Z", "X-Amz-Expires": "172800"}
    )
    repo.save_cached_upload("sha", {"kind": "image", "url": fresh_url, "contentType": "image/jpeg"})

    class FakeSogni:
        def __init__(self):
            self.uploads = 0

        def get_image_upload_url(self, job_id: str, image_path: Path):
            return {"url": "https://upload.example/upload", "fields": {"Content-Type": "image/png"}}

        def upload_image_to_presigned_post(self, upload: dict, image_path: Path):
            self.uploads += 1
            return {"kind": "image", "url": "https://uploads.example/fresh.png", "contentType": "image/png"}

    sogni = FakeSogni()
    runner = JobRunner(repo, sogni)

    result = runner._media_reference_for_frame("sha", 1, frame_path)

    assert result["contentType"] == "image/png"
    assert sogni.uploads == 1


def test_sogni_upload_detects_png_bytes_even_when_file_extension_is_jpg(tmp_path: Path):
    frame_path = tmp_path / "misnamed.jpg"
    frame_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"png payload")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"url": "https://upload.example", "fields": {"Content-Type": "image/png"}}}

    class FakeClient:
        def __init__(self):
            self.params = None

        def get(self, path, headers, params):
            self.params = params
            return FakeResponse()

    fake_client = FakeClient()
    client = SogniClient(api_key="abc", client=fake_client)

    upload = client.get_image_upload_url("1", frame_path)

    assert fake_client.params["contentType"] == "image/png"
    assert upload["_content_type"] == "image/png"


def test_sogni_client_accepts_direct_data_workflow_payload():
    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload
            self.is_error = False

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def post(self, *args, **kwargs):
            return FakeResponse({"data": {"id": "wf_abc", "status": "queued"}})

        def get(self, *args, **kwargs):
            return FakeResponse({"data": {"workflow": {"id": "wf_abc", "status": "completed", "steps": [{"artifacts": [{"url": "https://artifact.example/video.mp4"}]}]}}})

    client = SogniClient(api_key="abc", client=FakeClient())

    workflow = client.start_image_to_video_workflow(
        title="demo",
        prompt="animate",
        model_id="wan3",
        settings={"duration": 5},
        media_reference={"kind": "image", "url": "https://example.com/frame.png"},
        idempotency_key="k1",
    )

    assert workflow["id"] == "wf_abc"
    assert client.read_workflow("wf_abc")["status"] == "completed"


def test_sogni_client_resumes_workflow_endpoint():
    class FakeResponse:
        is_error = False

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": {"workflow": {"workflowId": "wf_resume", "status": "running"}, "resumed": True}}

    class FakeClient:
        def __init__(self):
            self.posts = []

        def post(self, *args, **kwargs):
            self.posts.append((args, kwargs))
            return FakeResponse()

    fake_client = FakeClient()
    client = SogniClient(api_key="abc", client=fake_client)

    workflow = client.resume_workflow("wf_resume")

    assert workflow["workflowId"] == "wf_resume"
    assert workflow["status"] == "running"
    assert fake_client.posts[0][0][0] == "/v1/creative-agent/workflows/wf_resume/resume"


def test_queue_continues_after_ordinary_job_failure():
    class Campaign:
        status = "READY"

    class Job:
        def __init__(self, job_id: int):
            self.id = job_id
            self.order_index = job_id
            self.status = "PREPARING"

    class FakeRepo:
        def __init__(self):
            self.campaign = Campaign()
            self.jobs = [Job(1), Job(2)]
            self.statuses = []
            self.failed_job_ids = set()

        def update_campaign_status(self, campaign_id, status):
            self.campaign.status = status
            self.statuses.append(status)

        def get_campaign(self, campaign_id):
            return self.campaign

        def claim_next_job(self, campaign_id):
            while self.jobs:
                job = self.jobs.pop(0)
                if job.id not in self.failed_job_ids:
                    return job
            return None

        def job_counts(self, campaign_id):
            return {"FAILED": 1}

    class FakeRunner:
        def __init__(self, repo):
            self.repo = repo
            self.ran = []

        def run(self, job_id):
            self.ran.append(job_id)
            if job_id == 1:
                self.repo.failed_job_ids.add(job_id)
                raise RuntimeError("diagnostic failure")

    repo = FakeRepo()
    runner = FakeRunner(repo)
    QueueManager(repo, runner).run_until_paused_or_complete(7)

    assert runner.ran == [1, 2]
    assert repo.campaign.status == "FAILED"
    assert "PAUSED" not in repo.statuses


def test_queue_pauses_after_sogni_auth_failure():
    class Campaign:
        status = "READY"

    class Job:
        def __init__(self, job_id: int):
            self.id = job_id
            self.order_index = job_id
            self.status = "PREPARING"

    class FakeRepo:
        def __init__(self):
            self.campaign = Campaign()
            self.jobs = [Job(1), Job(2)]
            self.statuses = []

        def update_campaign_status(self, campaign_id, status):
            self.campaign.status = status
            self.statuses.append(status)

        def get_campaign(self, campaign_id):
            return self.campaign

        def claim_next_job(self, campaign_id):
            return self.jobs.pop(0) if self.jobs else None

        def job_counts(self, campaign_id):
            return {"FAILED": len(self.statuses)}

    class FakeRunner:
        def __init__(self):
            self.ran = []

        def run(self, job_id):
            self.ran.append(job_id)
            request = httpx.Request("GET", "https://api.sogni.ai/v2/image/uploadUrl")
            response = httpx.Response(401, request=request)
            raise httpx.HTTPStatusError("401 Unauthorized", request=request, response=response)

    repo = FakeRepo()
    runner = FakeRunner()
    QueueManager(repo, runner).run_until_paused_or_complete(7)

    assert runner.ran == [1]
    assert repo.campaign.status == "PAUSED"
    assert repo.jobs[0].id == 2


def test_queue_pauses_after_permanent_configuration_failure():
    class Campaign:
        status = "READY"

    class Job:
        def __init__(self, job_id: int):
            self.id = job_id
            self.order_index = job_id
            self.status = "PREPARING"

    class FakeRepo:
        def __init__(self):
            self.campaign = Campaign()
            self.jobs = [Job(1), Job(2)]

        def update_campaign_status(self, campaign_id, status):
            self.campaign.status = status

        def get_campaign(self, campaign_id):
            return self.campaign

        def claim_next_job(self, campaign_id):
            return self.jobs.pop(0) if self.jobs else None

        def job_counts(self, campaign_id):
            return {"FAILED": 1}

    class FakeRunner:
        def __init__(self):
            self.ran = []

        def run(self, job_id):
            self.ran.append(job_id)
            raise ValueError("External reference URLs are supported only by Seedance, HappyHorse, and Wan 3 models.")

    repo = FakeRepo()
    runner = FakeRunner()
    QueueManager(repo, runner).run_until_paused_or_complete(7)

    assert runner.ran == [1]
    assert repo.campaign.status == "PAUSED"
    assert repo.jobs[0].id == 2


def test_wait_for_artifact_raises_sogni_workflow_failure_for_non_retryable_failure():
    class FakeRepo:
        def set_job_status(self, job_id: int, status: str, last_error: str | None = None) -> None:
            return None

    class FakeSogni:
        def read_workflow(self, workflow_id: str):
            return {
                "workflowId": workflow_id,
                "status": "failed",
                "events": [
                    {
                        "type": "workflow_failed",
                        "message": "Inline image data does not match declared MIME type image/jpeg",
                        "data": {"retryable": False},
                    }
                ],
            }

    runner = JobRunner(FakeRepo(), FakeSogni(), poll_interval=0, max_polls=1)

    try:
        runner._wait_for_artifact(9, "wf_failed")
        raise AssertionError("Expected SogniWorkflowFailure for non-retryable workflow failure")
    except SogniWorkflowFailure as exc:
        assert "Inline image data does not match declared MIME type image/jpeg" in str(exc)


def test_queue_continues_after_non_retryable_sogni_workflow_failure():
    class Campaign:
        status = "READY"

    class Job:
        def __init__(self, job_id: int):
            self.id = job_id
            self.order_index = job_id
            self.status = "PREPARING"

    class FakeRepo:
        def __init__(self):
            self.campaign = Campaign()
            self.jobs = [Job(1), Job(2)]

        def update_campaign_status(self, campaign_id, status):
            self.campaign.status = status

        def get_campaign(self, campaign_id):
            return self.campaign

        def claim_next_job(self, campaign_id):
            return self.jobs.pop(0) if self.jobs else None

        def job_counts(self, campaign_id):
            return {"FAILED": 1, "DONE": 1}

    class FakeRunner:
        def __init__(self):
            self.ran = []

        def run(self, job_id):
            self.ran.append(job_id)
            if job_id == 1:
                raise SogniWorkflowFailure("Sogni rejected the workflow prompt")

    repo = FakeRepo()
    runner = FakeRunner()
    QueueManager(repo, runner).run_until_paused_or_complete(7)

    assert runner.ran == [1, 2]
    assert repo.campaign.status == "FAILED"
