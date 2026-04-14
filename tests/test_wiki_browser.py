import json
from pathlib import Path
from threading import Thread
from urllib.request import urlopen

from meta_compiler.artifacts import build_paths, ensure_layout
from meta_compiler.io import dump_yaml, render_frontmatter
from meta_compiler.wiki_browser import create_wiki_browser_server


def _write_page(path: Path, page_id: str, title: str, body_lines: list[str]) -> None:
    frontmatter = {
        "id": page_id,
        "type": "concept",
        "created": "2026-01-01T00:00:00Z",
        "sources": ["src-test-001"],
        "related": [],
        "status": "reviewed",
    }
    path.write_text(
        "---\n"
        + render_frontmatter(frontmatter)
        + "\n---\n"
        + f"# {title}\n\n"
        + "\n".join(body_lines)
        + "\n",
        encoding="utf-8",
    )


def _load_json(url: str) -> dict:
    with urlopen(url) as response:  # noqa: S310 - local ephemeral test server
        return json.loads(response.read().decode("utf-8"))


def test_wiki_browser_falls_back_to_v1_and_serves_page(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    dump_yaml(
        paths.citations_index_path,
        {
            "citations": {
                "src-test-001": {
                    "human": "Test Source (2026), section 1",
                    "source": {"type": "seed", "path": "/seeds/test.pdf"},
                    "metadata": {"title": "Test Source", "year": 2026, "authors": ["Tester"]},
                    "status": "verified",
                }
            }
        },
    )
    _write_page(
        paths.wiki_v1_pages_dir / "concept-x.md",
        page_id="concept-x",
        title="Concept X",
        body_lines=[
            "## Definition",
            "Concept X covers the first test concept.",
            "",
            "## Key Claims",
            "- Claim with evidence [src-test-001]",
            "",
            "## Relationships",
            "- depends_on: []",
            "",
            "## Open Questions",
            "- None recorded.",
            "",
            "## Source Notes",
            "- Extracted from the seed.",
        ],
    )

    server, url, source_version = create_wiki_browser_server(artifacts_root=artifacts_root, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        assert source_version == "v1"
        index_payload = _load_json(url + "api/index")
        assert index_payload["source_version"] == "v1"
        assert index_payload["pages"][0]["id"] == "concept-x"

        page_payload = _load_json(url + "api/page?id=concept-x")
        assert page_payload["id"] == "concept-x"
        assert "<h1>Concept X</h1>" in page_payload["body_html"]
        assert page_payload["citations"][0]["citation_id"] == "src-test-001"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_wiki_browser_search_endpoint_returns_results(tmp_path: Path):
    workspace_root = tmp_path / "workspace"
    artifacts_root = workspace_root / "workspace-artifacts"
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    dump_yaml(paths.citations_index_path, {"citations": {}})
    _write_page(
        paths.wiki_v2_pages_dir / "sensor-noise.md",
        page_id="sensor-noise",
        title="Sensor Noise",
        body_lines=[
            "## Definition",
            "Sensor noise captures stochastic variation in the measurement process.",
            "",
            "## Key Claims",
            "- The wiki browser should find this page when searching for sensor.",
            "",
            "## Relationships",
            "- depends_on: []",
            "",
            "## Open Questions",
            "- How should low-light behavior be modeled?",
            "",
            "## Source Notes",
            "- Captured during testing.",
        ],
    )

    server, url, source_version = create_wiki_browser_server(artifacts_root=artifacts_root, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        assert source_version == "v2"
        search_payload = _load_json(url + "api/search?q=sensor")
        assert search_payload["results"]
        assert search_payload["results"][0]["concept_id"] == "sensor-noise"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)