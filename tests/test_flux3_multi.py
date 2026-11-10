from pathlib import Path

import pytest

from app.wavespeed.flux3_multi import prepare_flux3_multi_jobs, run_flux3_multi_jobs
from app.wavespeed.schemas import WaveSpeedPrediction
from app.wavespeed.validation import FLUX_3_KLEIN_BASE_EDIT_MODEL_ID


def _files(tmp_path: Path):
    paths = [tmp_path / name for name in ("a.png", "b.png", "face.png", "body.png")]
    for path in paths:
        path.write_bytes(b"image")
    return paths


def test_flux3_multi_prepares_one_three_reference_job_per_source(tmp_path: Path):
    a, b, face, body = _files(tmp_path)
    jobs = prepare_flux3_multi_jobs(
        sources=[str(a), str(b)], face=str(face), body=str(body),
        prompt="Keep the model identity", settings={"resolution": "1.5k", "seed": 42},
        output_dir=str(tmp_path),
    )
    assert len(jobs) == 2
    assert [job["images"] for job in jobs] == [[str(a), str(face), str(body)],
                                             [str(b), str(face), str(body)]]
    assert all(job["prompt"] == "Keep the model identity" for job in jobs)
    assert all(job["resolution"] == "1.5k" and job["seed"] == 42 for job in jobs)


def test_flux3_multi_rejects_invalid_batch_before_submission(tmp_path: Path):
    a, _, face, body = _files(tmp_path)
    with pytest.raises(ValueError, match="Ref 3"):
        prepare_flux3_multi_jobs(sources=[str(a)], face=str(face), body="",
                                 prompt="Edit", settings={}, output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="prompt"):
        prepare_flux3_multi_jobs(sources=[str(a)], face=str(face), body=str(body),
                                 prompt=" ", settings={}, output_dir=str(tmp_path))


def test_flux3_multi_allows_reusing_one_photo_in_more_than_one_reference(tmp_path: Path):
    a, _, _, _ = _files(tmp_path)
    jobs = prepare_flux3_multi_jobs(sources=[str(a)], face=str(a), body=str(a),
                                    prompt="Edit", settings={}, output_dir=str(tmp_path))
    assert jobs[0]["images"] == [str(a), str(a), str(a)]


def test_flux3_multi_submits_individual_jobs_with_fixed_refs_and_isolates_failure(tmp_path: Path):
    a, b, face, body = _files(tmp_path)
    jobs = prepare_flux3_multi_jobs(
        sources=[str(a), str(b)], face=str(face), body=str(body),
        prompt="Edit", settings={"resolution": "1k", "output_format": "png"},
        output_dir=str(tmp_path),
    )
    submitted = []
    uploads = []
    updates = []

    class Client:
        def submit(self, payload, *, model_id):
            assert model_id == FLUX_3_KLEIN_BASE_EDIT_MODEL_ID
            submitted.append(payload)
            if len(submitted) == 1:
                return WaveSpeedPrediction("task-a", "failed", error="provider error")
            return WaveSpeedPrediction("task-b", "completed", outputs=["https://cdn/out.png"])

    def upload(path):
        uploads.append(path.name)
        return {"download_url": f"https://cdn/{path.name}"}

    run_flux3_multi_jobs(
        jobs, Client(), upload_file=upload,
        save_output=lambda _output, _job, task_id, _suffix: str(tmp_path / f"{task_id}.png"),
        on_update=lambda job: updates.append((job["source"], job["status"])),
    )

    assert [payload["images"] for payload in submitted] == [
        ["https://cdn/a.png", "https://cdn/face.png", "https://cdn/body.png"],
        ["https://cdn/b.png", "https://cdn/face.png", "https://cdn/body.png"],
    ]
    assert uploads == ["a.png", "face.png", "body.png", "b.png"]
    assert [job["status"] for job in jobs] == ["failed", "completed"]
    assert jobs[0]["error"] == "provider error"
    assert jobs[1]["output_file"].endswith("task-b.png")
    assert updates[-1] == (str(b), "completed")
