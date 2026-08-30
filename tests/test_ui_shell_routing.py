from pathlib import Path


def test_project_cards_install_living_graph_navigation_guard():
    source = Path("frontend/web/prototype.js").read_text(encoding="utf-8")
    assert "installProjectCardLivingGraphNavigation" in source
    assert "[data-project-card-open]" in source
    assert "[data-project-view=\"architecture\"]" in source
    assert "installProjectCardLivingGraphNavigation(document)" in source
