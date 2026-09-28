import csv
import json
import re
from pathlib import Path

from app.core.campaign_manager import CampaignManager
from app.core.filename_builder import FilenameBuilder, FilenameContext
from app.core.model_settings import extract_duration_seconds
from app.core.validators import default_outfit_name, prepare_minimax_h3_prompt, validate_minimax_h3_prompt, validate_speaker_binding
from app.database.db import Database
from app.database.repositories import CampaignRepository
from app.sogni.client import SogniClient
from app.sogni.schemas import ModelDescriptor
from app.core.prompt_importer import import_prompts


def test_default_outfit_name_is_derived_without_ai():
    assert default_outfit_name("outfit_01_pink_dress.png") == "Pink Dress"
    assert default_outfit_name("BLACK-top__02.webp") == "Black Top"


def test_import_prompts_keeps_name_and_text_separate(tmp_path: Path):
    source = tmp_path / "prompts.csv"
    with source.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "name", "text"])
        writer.writeheader()
        writer.writerow({"id": "P01", "name": "Quick Question", "text": "Look at camera"})

    prompts = import_prompts(source)

    assert prompts[0].prompt_code == "P01"
    assert prompts[0].prompt_name == "Quick Question"
    assert prompts[0].prompt_text == "Look at camera"


def test_import_txt_uses_blocks_and_default_names(tmp_path: Path):
    source = tmp_path / "prompts.txt"
    source.write_text("First prompt line\n\nSecond prompt\nwith detail\n", encoding="utf-8")

    prompts = import_prompts(source)

    assert [p.prompt_code for p in prompts] == ["P01", "P02"]
    assert [p.prompt_name for p in prompts] == ["Prompt 01", "Prompt 02"]
    assert prompts[1].prompt_text == "Second prompt\nwith detail"


def test_filename_builder_sanitizes_windows_names_and_collisions(tmp_path: Path):
    builder = FilenameBuilder()
    context = FilenameContext(
        campaign="Eve: September",
        job_number=7,
        outfit='Pink/Dress*',
        frame_name="frame 01.png",
        prompt_id="P01",
        prompt_name='Quick? "Question"',
        model="WAN 2.2",
    )

    first = builder.build_output_path(tmp_path, "flat", "{outfit}__{prompt_id}_{prompt_name}.mp4", context)
    first.write_bytes(b"existing")
    second = builder.build_output_path(tmp_path, "flat", "{outfit}__{prompt_id}_{prompt_name}.mp4", context)

    assert first.name == "Pink_Dress__P01_Quick_Question.mp4"
    assert second.name == "Pink_Dress__P01_Quick_Question__02.mp4"


def test_campaign_creation_persists_full_matrix_before_running(tmp_path: Path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "outfit_01_pink_dress.png").write_bytes(b"pink")
    (frames_dir / "outfit_02_black_top.jpg").write_bytes(b"black")
    prompt_source = tmp_path / "prompts.json"
    prompt_source.write_text(
        json.dumps(
            [
                {"id": "P01", "name": "Quick Question", "text": "Look at camera"},
                {"id": "P02", "name": "Come Closer", "text": "Lean forward"},
                {"id": "P03", "name": "Hair Flip", "text": "Move hair"},
            ]
        ),
        encoding="utf-8",
    )
    output_dir = tmp_path / "out"
    db = Database(tmp_path / "app.db")
    db.initialize()

    campaign = CampaignManager(CampaignRepository(db)).create_campaign(
        name="Eve Batch",
        frames_folder=frames_dir,
        prompts_source=prompt_source,
        output_folder=output_dir,
        model=ModelDescriptor(id="wan22", name="WAN 2.2", media_type="video", parameters={}),
        settings={},
    )

    repo = CampaignRepository(db)
    assert repo.count_jobs(campaign.id) == 6
    assert [job.order_index for job in repo.list_jobs(campaign.id)] == [1, 2, 3, 4, 5, 6]
    assert {job.idempotency_key for job in repo.list_jobs(campaign.id)} == {
        f"sva:{campaign.id}:{idx:04d}" for idx in range(1, 7)
    }


def test_add_jobs_to_existing_campaign_keeps_campaign_and_orders_jobs_after_existing(tmp_path: Path):
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    (frames_dir / "outfit_01_pink_dress.png").write_bytes(b"pink")
    prompt_source = tmp_path / "prompts.json"
    prompt_source.write_text(json.dumps([{"id": "P01", "name": "One", "text": "First"}]), encoding="utf-8")
    db = Database(tmp_path / "app.db")
    db.initialize()
    repo = CampaignRepository(db)
    manager = CampaignManager(repo)
    campaign = manager.create_campaign(
        name="Existing",
        frames_folder=frames_dir,
        prompts_source=prompt_source,
        output_folder=tmp_path / "out",
        model=ModelDescriptor(id="wan22", name="WAN 2.2", media_type="video", parameters={}),
        settings={},
    )

    extra_frames = tmp_path / "extra_frames"
    extra_frames.mkdir()
    (extra_frames / "outfit_02_black_top.png").write_bytes(b"black")
    extra_prompts = tmp_path / "extra_prompts.json"
    extra_prompts.write_text(
        json.dumps([
            {"id": "P02", "name": "Two", "text": "Second"},
            {"id": "P03", "name": "Three", "text": "Third"},
        ]),
        encoding="utf-8",
    )

    added = manager.add_jobs_to_campaign(campaign.id, extra_frames, extra_prompts)

    jobs = repo.list_jobs(campaign.id)
    assert added == 2
    assert repo.get_campaign(campaign.id).id == campaign.id
    assert [job.order_index for job in jobs] == [1, 2, 3]
    assert [job.idempotency_key for job in jobs] == [
        f"sva:{campaign.id}:0001", f"sva:{campaign.id}:0002", f"sva:{campaign.id}:0003"
    ]


def test_atomic_job_claim_claims_one_pending_job(tmp_path: Path):
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
            "output_folder": str(tmp_path),
            "filename_template": "{outfit}__{prompt_id}_{prompt_name}.mp4",
            "organization_mode": "flat",
            "concurrency": 1,
            "settings_json": "{}",
        }
    )
    frame_id = repo.insert_frame(campaign_id, str(tmp_path / "a.png"), "a.png", "A", "sha")
    prompt_id = repo.insert_prompt(campaign_id, "P01", "Name", "Text")
    repo.insert_job(campaign_id, frame_id, prompt_id, 1, "sva:test:0001")

    claimed = repo.claim_next_job(campaign_id)
    second = repo.claim_next_job(campaign_id)

    assert claimed is not None
    assert claimed.status == "PREPARING"
    assert second is None


def test_sogni_workflow_payload_uses_video_model_and_reference_media():
    payload = SogniClient.build_image_to_video_payload(
        title="Job 1",
        prompt="Animate this",
        model_id="wan3",
        settings={"duration": 5, "resolution": "720p"},
        media_reference={"kind": "image", "url": "https://example.com/frame.png"},
    )

    step = payload["input"]["steps"][0]
    assert step["toolName"] == "generate_video"
    assert step["arguments"]["videoModel"] == "wan3"
    assert step["arguments"]["prompt"] == "Animate this"
    assert payload["media_references"][0]["kind"] == "image"
    assert payload["media_references"][0]["url"] == "https://example.com/frame.png"
    assert step["arguments"]["referenceImageIndices"] == [-1]


def test_sogni_workflow_rejects_ltx_external_reference_locally():
    try:
        SogniClient.build_image_to_video_payload(
            title="Job 1",
            prompt="Animate this",
            model_id="ltx25",
            settings={"duration": 5},
            media_reference={"kind": "image", "url": "https://example.com/frame.png"},
        )
        raise AssertionError("Expected ValueError for LTX external reference")
    except ValueError as exc:
        assert "External reference URLs are supported only" in str(exc)
        assert "ltx25" in str(exc)


def test_sogni_workflow_maps_catalog_i2v_model_to_i2v_workflow_model():
    payload = SogniClient.build_image_to_video_payload(
        title="Job 1",
        prompt="Animate this",
        model_id="minimax-h3-fl2va-fp8_i2v_balanced",
        settings={"duration": 8},
        media_reference={"kind": "image", "url": "https://example.com/frame.png"},
    )

    step = payload["input"]["steps"][0]
    assert step["toolName"] == "animate_photo"
    assert step["arguments"]["videoModel"] == "minimax-h3-i2v-balanced"
    assert step["arguments"]["sourceImageIndex"] == -1
    assert "referenceImageIndices" not in step["arguments"]


def test_h3_prepare_does_not_remove_aspect_ratio_or_rewrite_prompt():
    raw = """
    integrated_multimodal_description:
    [Shot 1] The woman (S1) says: <d>[English] Look at camera.</d>

    overall_soundscape:
    Natural room tone.

    non_diegetic_music:
    None.
    """.strip()

    rendered = prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
    payload = SogniClient.build_image_to_video_payload(
        title="Job 73",
        prompt=rendered,
        model_id="minimax-h3-fl2va-fp8_i2v_balanced",
        settings={"duration": 8, "aspectRatio": "9:16"},
        media_reference=None,
    )

    assert rendered == raw
    assert payload["input"]["steps"][0]["arguments"]["prompt"] == raw
    assert payload["input"]["steps"][0]["arguments"]["aspectRatio"] == "9:16"
    assert payload["input"]["steps"][0]["arguments"]["skipPromptProcessing"] is True


def test_h3_payload_omits_prompt_processing_flag_when_disabled():
    payload = SogniClient.build_image_to_video_payload(
        title="Job 74",
        prompt="integrated_multimodal_description:\n[Shot 1] The subject speaks.\n\noverall_soundscape:\nRoom tone.\n\nnon_diegetic_music:\nNone.",
        model_id="minimax-h3-fl2va-fp8_i2v_balanced",
        settings={"duration": 8},
        media_reference=None,
        skip_prompt_processing=False,
    )

    assert "skipPromptProcessing" not in payload["input"]["steps"][0]["arguments"]


def test_h3_validator_rejects_all_numeric_colon_tokens_in_single_shot():
    for token in ("9:16", "1:30", "00:08", "12:45"):
        prompt = f"""
        integrated_multimodal_description:
        [Shot 1] The subject moves with a {token} notation.

        overall_soundscape:
        Room tone.

        non_diegetic_music:
        None.
        """.strip()
        try:
            validate_minimax_h3_prompt(prompt, "minimax-h3-fl2va-fp8_i2v_balanced")
            raise AssertionError(f"Expected ValueError for numeric colon token {token}")
        except ValueError as exc:
            assert "clock times belong only" in str(exc)


def test_h3_validator_allows_numeric_colon_token_only_on_shot_two_cut_marker():
    prompt = """
    integrated_multimodal_description:
    [Shot 1] The subject looks toward camera.
    [Shot 2] At 00:04.000, cut to a closer shot.

    overall_soundscape:
    Room tone.

    non_diegetic_music:
    None.
    """.strip()

    assert validate_minimax_h3_prompt(prompt, "minimax-h3-fl2va-fp8_i2v_balanced") == prompt


def test_validate_minimax_h3_prompt_accepts_structured_prompt():
    prompt = """
    integrated_multimodal_description:
    A woman looks directly at camera.

    overall_soundscape:
    Natural room tone and light ambient breathing.

    non_diegetic_music:
    None.

    [Shot 1] She looks down.
    Then she raises her gaze.
    The woman (S1) says:
    <d>[English] Love advice for grown men.</d>
    """.strip()

    assert validate_minimax_h3_prompt(prompt, "minimax-h3-fl2va-fp8_i2v_balanced") == prompt


def test_validate_minimax_h3_prompt_accepts_multiple_shots_and_valid_clock_marker():
    prompt = """
    integrated_multimodal_description:
    A woman turns toward the camera.

    overall_soundscape:
    Room tone.

    non_diegetic_music:
    None.

    [Shot 1] She looks down.
    Then the eyes rise to camera.

    [Shot 2] At 00:04.000, she smiles.
    After that, the expression settles.
    """.strip()

    assert validate_minimax_h3_prompt(prompt, "minimax-h3-fl2va-fp8_i2v_balanced") == prompt


def test_validate_minimax_h3_prompt_rejects_loose_timestamp_without_shot_marker():
    prompt = """
    integrated_multimodal_description:
    A woman looks at camera.

    overall_soundscape:
    Room tone.

    non_diegetic_music:
    None.

    00:01 She looks up.
    """.strip()

    try:
        validate_minimax_h3_prompt(prompt, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected ValueError for loose H3 timestamp")
    except ValueError as exc:
        assert "Invalid MiniMax H3 prompt contract" in str(exc)


def test_validate_minimax_h3_prompt_rejects_second_loose_timestamp_without_shot_marker():
    prompt = """
    integrated_multimodal_description:
    A woman looks at camera.

    overall_soundscape:
    Room tone.

    non_diegetic_music:
    None.

    00:02 She smiles.
    """.strip()

    try:
        validate_minimax_h3_prompt(prompt, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected ValueError for loose H3 timestamp")
    except ValueError as exc:
        assert "Invalid MiniMax H3 prompt contract" in str(exc)


def test_prepare_minimax_h3_prompt_rejects_legacy_timeline_instead_of_converting_it():
    raw = """
    00:00 She looks down.
    00:01 She raises her gaze.
    00:02 She says: "Love advice for grown men,"
    00:03 She continues: "stop hiding your"
    00:04 She says: "softness."
    """.strip()

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_prepare_minimax_h3_prompt_keeps_valid_h3_prompt_intact():
    raw = """
    integrated_multimodal_description:
    [Shot 1] She looks down.
    Then she raises her gaze.

    overall_soundscape:
    Clear synchronized speech with subtle natural room ambience.

    non_diegetic_music:
    None.
    """.strip()

    rendered = prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
    assert rendered == raw


def test_prepare_minimax_h3_prompt_does_not_add_first_frame_preamble_for_i2v():
    raw = "00:00 She looks down."

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_prepare_minimax_h3_prompt_leaves_non_h3_models_untouched():
    raw = "00:00 She looks down."
    assert prepare_minimax_h3_prompt(raw, "wan22") == raw


def test_raw_prompt_is_not_mutated_by_prepare_minimax_h3_prompt():
    raw = """
    integrated_multimodal_description:
    [Shot 1] The woman (S1) says: <d>[English] Hello there.</d>

    overall_soundscape:
    Room tone.

    non_diegetic_music:
    None.
    """.strip()

    assert prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced") == raw


def test_single_shot_legacy_prompt_reports_clock_timestamps_without_rewriting():
    raw = """
    The video is a single continuous shot lasting approximately 8 seconds.
    00:00 She looks down.
    00:01 She raises her gaze.
    00:08 She holds the final expression.
    """.strip()

    assert extract_duration_seconds(raw) == 8
    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_single_shot_prompt_numeric_colon_tokens_are_not_removed_by_prepare():
    raw = "The video is a single continuous vertical 9:16 aspect ratio shot lasting approximately 8 seconds. 00:08 She smiles."

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_prepare_minimax_h3_prompt_requires_full_h3_contract_for_explicit_shots():
    raw = """
    [Shot 1] Establishing shot of the subject.
    [Shot 2] At 00:04.000, cut to a closer shot.
    """.strip()

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_consecutive_dialogue_fragments_are_not_merged_by_prepare():
    raw = """
    00:01 She begins speaking:
    "Love advice for grown men,"
    00:02 She continues:
    "stop hiding your"
    00:03 She continues:
    "softness."
    00:04 She smiles at the camera.
    """.strip()

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_pause_between_dialogue_groups_is_not_rewritten_by_prepare():
    raw = """
    00:01 She begins speaking:
    "Love advice for grown men,"
    00:02 She continues:
    "stop hiding your"
    00:03 She continues:
    "softness."
    00:05 She resumes:
    "The right woman finds it"
    00:06 She continues:
    "more attractive than your"
    00:07 She concludes:
    "strength."
    """.strip()

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_single_speaker_prompt_is_not_auto_rewritten():
    raw = """
    00:01 The woman begins speaking: "Hello there."
    00:03 The woman resumes: "Good to see you."
    """.strip()

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_two_distinct_speakers_are_not_auto_rewritten():
    raw = """
    00:01 The woman says: "Hello there."
    00:02 The man replies: "Hello."
    00:04 The woman continues: "Welcome back."
    """.strip()

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_returning_speaker_ids_are_not_auto_rewritten():
    raw = """
    [Shot 1] The woman says: "First line."
    [Shot 2] At 00:04.000, The same woman says: "Second line."
    """.strip()

    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)


def test_validate_speaker_binding_rejects_dialogue_without_nearby_id():
    prompt = "The woman says:\n<d>[English] Hello there.</d>"

    try:
        validate_speaker_binding(prompt)
        raise AssertionError("Expected ValueError for unbound dialogue")
    except ValueError as exc:
        assert "nearby stable" in str(exc)


def test_speaker_id_stays_outside_dialogue_and_non_speaking_subjects_are_unbound_in_raw_h3():
    raw = """
    integrated_multimodal_description:
    A man stands silently in the background.
    [Shot 1] The woman (S1) says: <d>[English] Hello there.</d>

    overall_soundscape:
    Room tone.

    non_diegetic_music:
    None.
    """.strip()

    rendered = prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")

    assert "A man (S" not in rendered
    assert "<d>[English] (S1)" not in rendered
    assert "The woman (S1)" in rendered
    validate_speaker_binding(rendered)


def test_duration_detection_uses_raw_prompt_without_h3_adaptation():
    raw = "The video is a single continuous shot lasting approximately 8 seconds. 00:08 She smiles."

    assert extract_duration_seconds(raw) == 8
    try:
        prepare_minimax_h3_prompt(raw, "minimax-h3-fl2va-fp8_i2v_balanced")
        raise AssertionError("Expected invalid raw H3 prompt to be reported, not rewritten")
    except ValueError as exc:
        assert "prompt must include integrated_multimodal_description" in str(exc)
