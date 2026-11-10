
"""Regression test for a destination created after move planning."""

import json
from pathlib import Path

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

    # Another program creates the destination before execution.
    target.write_text("Important existing content", encoding="utf-8")

    # Execute the previously approved plan.
    log_path = executor.apply(steps)

    renamed_target = target_dir / "collision_test (2).txt"

    # Both files must retain their original contents.
    assert target.read_text(encoding="utf-8") == "Important existing content"
    assert renamed_target.read_text(encoding="utf-8") == "Original source content"
    assert not source.exists()

    # The undo log must contain the actual destination.
    logged_steps = json.loads(log_path.read_text(encoding="utf-8"))
    assert Path(logged_steps[0]["dst"]) == renamed_target

    # Undo must restore the moved file without touching the other file.
    assert executor.undo_last() == "Moved 1 file(s) back."
    assert source.read_text(encoding="utf-8") == "Original source content"
    assert target.read_text(encoding="utf-8") == "Important existing content"
    assert not renamed_target.exists()
