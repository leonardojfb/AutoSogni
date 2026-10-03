from app.byteplus.pricing import estimate_cost


def test_estimate_cost_uses_resolution_and_output_duration():
    assert estimate_cost({"resolution": "720p", "duration": 5, "mode": "text"}) == 1.515


def test_estimate_cost_includes_input_video_duration_for_video_modes():
    assert estimate_cost({
        "resolution": "1080p",
        "duration": 5,
        "mode": "edit",
        "input_video_duration": 10,
    }) == 6.9316
