"""Headless UI tests with Streamlit's AppTest: no browser, no server."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).parent.parent / "app.py")


def run(**state):
    at = AppTest.from_file(APP, default_timeout=60)
    for k, v in state.items():
        at.session_state[k] = v
    return at.run()


def test_all_tabs_render_without_errors():
    at = run()
    assert not at.exception and not at.error
    assert [t.label for t in at.tabs] == ["1 · Estimate", "2 · Optimise", "3 · Actuals", "4 · Forecast"]
    labels = [m.label for m in at.metric]
    for label in ("Variable / interview (partial)", "Best option per line", "Mean / completed", "Next 12 months",
                  "Interview cost per hire"):
        assert label in labels, label


def test_synthetic_data_is_labelled():
    assert any("Synthetic data" in w.value for w in run().warning)


def test_broken_profile_shows_an_error_not_a_crash():
    at = run(profile_text="calls: [unclosed")
    assert not at.exception and any("Profile error" in e.value for e in at.error)


def test_editing_the_profile_changes_the_estimate():
    at = run()
    before = next(m.value for m in at.metric if m.label.startswith("Variable"))
    text = at.text_area[0].value.replace("units: {characters: 7200}", "units: {characters: 72}")
    at.text_area[0].set_value(text).run()
    after = next(m.value for m in at.metric if m.label.startswith("Variable"))
    assert before != after


def test_turning_off_the_demo_log_hides_actuals():
    at = run()
    at.toggle[0].set_value(False).run()
    assert not at.exception
    assert "Mean / completed" not in [m.label for m in at.metric]
    assert "Phase 3 actuals" not in at.radio[0].options
