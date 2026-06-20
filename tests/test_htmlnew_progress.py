"""Per-slide fill progress mapping for the htmlnew pipeline."""
from schemas.session import Stage
from worker.tasks.htmlnew import map_progress


def test_fill_stage_start_maps_to_designing():
    assert map_progress("fill: заполняю 6 слайдов (параллельно)") == (Stage.DESIGNING, 45)


def test_per_slide_fill_interpolates_pct_monotonically():
    pcts = [map_progress(f"fill: слайд {k}/6")[1] for k in range(1, 7)]
    # all within the fill band, strictly increasing, ending below assemble (65)
    assert pcts == sorted(pcts)
    assert pcts[0] >= 45 and pcts[-1] <= 63
    assert len(set(pcts)) > 1  # actually moves, not a frozen number
    for _, pct in (map_progress(f"fill: слайд {k}/6") for k in range(1, 7)):
        assert isinstance(pct, int)


def test_per_slide_fill_stage_is_designing():
    stage, _ = map_progress("fill: слайд 2/9")
    assert stage == Stage.DESIGNING


def test_other_prefixes_unaffected():
    assert map_progress("assemble: собираю HTML") == (Stage.RENDERING, 65)
    assert map_progress("warn: что-то пошло не так") is None
