# PROMPT: Add focused unit tests for non-hardcoded staff uniform detection after
# observing that Store 2 staff wear pink shirts while other clips use dark
# uniforms.
# CHANGES MADE: Used synthetic OpenCV image patches to validate color heuristics
# without needing model inference or CCTV assets.

import os
import sys

import numpy as np
import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))

import detect


def solid_bgr_frame(color):
    frame = np.zeros((100, 80, 3), dtype=np.uint8)
    frame[:] = color
    return frame


def test_black_uniform_scores_as_staff_for_any_store():
    pytest.importorskip("cv2")
    frame = solid_bgr_frame((20, 20, 20))
    bbox = [10, 10, 70, 95]

    original_store = detect.STORE_ID
    try:
        detect.STORE_ID = "ST1"
        assert detect.is_staff_uniform(frame, bbox)
    finally:
        detect.STORE_ID = original_store


def test_pink_uniform_scores_as_staff_for_store_2():
    cv2 = pytest.importorskip("cv2")
    pink_bgr = cv2.cvtColor(np.uint8([[[165, 120, 230]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    frame = solid_bgr_frame(pink_bgr)
    bbox = [10, 10, 70, 95]

    original_store = detect.STORE_ID
    try:
        detect.STORE_ID = "ST2"
        assert detect.is_staff_uniform(frame, bbox)
    finally:
        detect.STORE_ID = original_store


def test_pink_uniform_does_not_apply_to_store_1():
    cv2 = pytest.importorskip("cv2")
    pink_bgr = cv2.cvtColor(np.uint8([[[165, 120, 230]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    frame = solid_bgr_frame(pink_bgr)
    bbox = [10, 10, 70, 95]

    original_store = detect.STORE_ID
    try:
        detect.STORE_ID = "ST1"
        assert not detect.is_staff_uniform(frame, bbox)
    finally:
        detect.STORE_ID = original_store


def test_store_config_declares_store_2_pink_uniform():
    assert "pink" in detect.configured_staff_uniforms("ST2")
    assert "black" in detect.configured_staff_uniforms("ST1")


def test_detection_summary_counts_events_without_video_model():
    original_store = detect.STORE_ID
    original_camera = detect.CAMERA_ID
    original_zone = detect.ZONE_ID
    try:
        detect.STORE_ID = "ST2"
        detect.CAMERA_ID = "STORE2_BILLING_01"
        detect.ZONE_ID = "BILLING"
        events = [
            {"visitor_id": "VIS_1", "event_type": "BILLING_QUEUE_JOIN", "is_staff": False},
            {"visitor_id": "STAFF_001", "event_type": "BILLING_QUEUE_JOIN", "is_staff": True},
            {"visitor_id": "VIS_1", "event_type": "ZONE_DWELL", "is_staff": False},
        ]

        summary = detect.detection_summary(
            events,
            raw_track_ids={"VIS_1", "VIS_2", "STAFF_001"},
            staff_ids={"STAFF_001"},
            role="billing",
            output_path="outputs/example.jsonl",
        )

        assert summary["store_id"] == "ST2"
        assert summary["zone_label"] == "Billing Counter"
        assert summary["staff_uniforms"] == ["black", "pink"]
        assert summary["raw_tracks"] == 3
        assert summary["staff_identities"] == 1
        assert summary["qualified_visitors"] == 1
        assert summary["event_counts"]["BILLING_QUEUE_JOIN"] == 2
    finally:
        detect.STORE_ID = original_store
        detect.CAMERA_ID = original_camera
        detect.ZONE_ID = original_zone
