
"""Regression test for a destination created after move planning."""

from pathlib import Path

import shutil

from assistant import executor


def test_destination_created_after_planning_is_not_overwritten(base):
    source = base / "Downloads" / "collision_test.txt"
    source.write_text("Original source content", encoding="utf-8")

    target_dir = base / "Documents"
    target = target_dir / source.name

    # Plan the move while the destination is available.
    steps = executor.move_steps(
        [source],
        target_dir,
        reason="Move the test document",
    )

    assert len(steps) == 1
    assert Path(steps[0]["dst"]) == target

    # Simulate another program creating the destination
    # after planning but before execution.
    target.write_text("Important existing content", encoding="utf-8")

    # Execute the previously approved plan.
    try:
        executor.apply(steps)
    except (OSError, shutil.Error):
        pass

    # The newly created destination must remain untouched.
    assert target.read_text(encoding="utf-8") == "Important existing content"
