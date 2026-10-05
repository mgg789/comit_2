"""Fixed independent family allocation shared by generation and activation checks."""

DIRECTIONS = ("backend", "frontend", "fullstack", "mobile", "devops", "data", "qa", "ml")


def expected_families() -> list[dict]:
    """Return the frozen 32/8/8/12 allocation; grades are candidate facts, not strata."""
    rows = []
    for direction in DIRECTIONS:
        splits = ["train"] * 4 + ["validation", "calibration", "golden"]
        if direction in ("backend", "frontend", "data", "ml"):
            splits.append("golden")
        for index, split in enumerate(splits, 1):
            identifier = f"{direction}-{index:02d}"
            rows.append({"family_id": identifier, "source_id": identifier, "direction": direction, "split": split})
    return rows
