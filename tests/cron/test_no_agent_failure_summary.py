from cron.scheduler import _summarize_cron_failure_for_delivery


def test_no_agent_timeout_is_reported_as_script_timeout():
    job = {"id": "4c4b6b29aac1", "name": "qdrant-auto-intake", "no_agent": True}

    message = _summarize_cron_failure_for_delivery(
        job,
        "subprocess.TimeoutExpired: rclone copy timed out after 600 seconds",
    )

    assert "script timeout" in message
    assert "provider timeout" not in message
    assert "Fallback chain" not in message
