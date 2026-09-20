from app.core.model_settings import build_campaign_settings, extract_duration_seconds, resolve_job_settings


def test_extract_duration_seconds_from_long_prompt_text():
    prompt = "The video is a single continuous shot lasting approximately 8 seconds in a vertical 9:16 aspect ratio."

    assert extract_duration_seconds(prompt) == 8


def test_extract_duration_seconds_supports_shorter_wording():
    prompt = "The video begins as a 5 second vertical clip with no cuts."

    assert extract_duration_seconds(prompt) == 5


def test_extract_duration_seconds_falls_back_to_last_timestamp():
    prompt = """
    The video begins in a vertical 9:16 aspect ratio with a steady close-up shot.

    At 00:01, she speaks, saying, "Be real."
    At 00:07, the video concludes abruptly.
    """

    assert extract_duration_seconds(prompt) == 7


def test_build_campaign_settings_uses_manual_duration_and_aspect_ratio():
    settings = build_campaign_settings(duration_mode="manual", duration_seconds="6", aspect_ratio="9:16")

    assert settings == {
        "duration_mode": "manual",
        "duration": 6,
        "aspectRatio": "9:16",
        "skipPromptProcessing": True,
    }


def test_build_campaign_settings_persists_prompt_processing_choice():
    settings = build_campaign_settings(
        duration_mode="auto",
        duration_seconds="8",
        aspect_ratio="9:16",
        skip_prompt_processing=False,
    )

    assert settings["skipPromptProcessing"] is False


def test_resolve_job_settings_replaces_auto_duration_from_prompt():
    campaign_settings = {"duration_mode": "auto", "aspectRatio": "9:16"}
    prompt = "The video is a single continuous shot lasting approximately 7 seconds in a 9:16 aspect ratio."

    assert resolve_job_settings(campaign_settings, prompt) == {"duration": 7, "aspectRatio": "9:16"}
