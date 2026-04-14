from __future__ import annotations

import json
import re
import webbrowser
from dataclasses import dataclass
from html import escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .artifacts import ArtifactPaths, build_paths, ensure_layout
from .wiki_interface import WikiQueryInterface


@dataclass(frozen=True)
class WikiBrowserState:
    paths: ArtifactPaths
    query_interface: WikiQueryInterface
    source_version: str


def _render_inline_markdown(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f'<a href="{escape(match.group(2), quote=True)}" target="_blank" rel="noreferrer">{match.group(1)}</a>',
        escaped,
    )
    return escaped


def _render_markdown_html(markdown_text: str) -> str:
    lines = markdown_text.splitlines()
    html_parts: list[str] = []
    paragraph_lines: list[str] = []
    code_lines: list[str] = []
    in_code_block = False
    in_list = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if paragraph_lines:
            html_parts.append(f"<p>{_render_inline_markdown(' '.join(paragraph_lines))}</p>")
            paragraph_lines = []

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_parts.append("</ul>")
            in_list = False

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            if in_code_block:
                html_parts.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(raw_line)
            continue

        if not stripped:
            flush_paragraph()
            close_list()
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            close_list()
            html_parts.append(f"<h3>{_render_inline_markdown(stripped[4:])}</h3>")
            continue
        if stripped.startswith("## "):
            flush_paragraph()
            close_list()
            html_parts.append(f"<h2>{_render_inline_markdown(stripped[3:])}</h2>")
            continue
        if stripped.startswith("# "):
            flush_paragraph()
            close_list()
            html_parts.append(f"<h1>{_render_inline_markdown(stripped[2:])}</h1>")
            continue
        if stripped.startswith("- "):
            flush_paragraph()
            if not in_list:
                html_parts.append("<ul>")
                in_list = True
            html_parts.append(f"<li>{_render_inline_markdown(stripped[2:])}</li>")
            continue

        paragraph_lines.append(stripped)

    flush_paragraph()
    close_list()
    if in_code_block:
        html_parts.append("<pre><code>" + escape("\n".join(code_lines)) + "</code></pre>")

    return "\n".join(html_parts)


def _extract_title(body: str, fallback: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or fallback
    return fallback


def build_page_payload(query_interface: WikiQueryInterface, page_id: str) -> dict[str, object] | None:
    concept = query_interface.get_concept(page_id)
    if concept is None:
        return None

    body = str(concept.get("body", ""))
    concept_id = str(concept.get("id", page_id))

    return {
        "id": concept_id,
        "title": _extract_title(body, concept_id),
        "type": concept.get("frontmatter", {}).get("type", "concept"),
        "path": concept.get("path", ""),
        "frontmatter": concept.get("frontmatter", {}),
        "body_markdown": body,
        "body_html": _render_markdown_html(body),
        "relationships": query_interface.get_relationships(concept_id),
        "citations": query_interface.get_citations(concept_id),
        "equations": query_interface.get_equations(concept_id),
    }


def _render_shell_html(source_version: str) -> str:
    return f"""<!DOCTYPE html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>META-COMPILER Wiki Browser</title>
  <style>
    :root {{
      --bg: #f6f3ea;
      --panel: #fffdf8;
      --ink: #1f1a14;
      --muted: #6d6255;
      --accent: #146356;
      --border: #d7ccbb;
      --shadow: 0 20px 40px rgba(77, 60, 35, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(20, 99, 86, 0.08), transparent 28%),
        linear-gradient(180deg, #fbf8f1 0%, var(--bg) 100%);
      min-height: 100vh;
    }}
    .shell {{
      display: grid;
      grid-template-columns: minmax(260px, 340px) 1fr;
      gap: 1rem;
      min-height: 100vh;
      padding: 1rem;
    }}
    aside {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 1rem;
      box-shadow: var(--shadow);
      resize: horizontal;
      overflow: auto;
      min-width: 240px;
      max-width: 45vw;
    }}
    main {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 1.5rem;
      box-shadow: var(--shadow);
      overflow: auto;
    }}
    h1, h2, h3 {{ line-height: 1.15; }}
    .eyebrow {{ color: var(--accent); font-size: 0.78rem; letter-spacing: 0.08em; text-transform: uppercase; }}
    .status {{ color: var(--muted); font-size: 0.92rem; margin-top: 0.25rem; }}
    .search {{ display: flex; gap: 0.5rem; margin: 1rem 0; }}
    .search input {{
      flex: 1;
      padding: 0.75rem 0.9rem;
      border-radius: 12px;
      border: 1px solid var(--border);
      background: #fff;
      font: inherit;
    }}
    .search button, .page-link {{
      border: 0;
      border-radius: 12px;
      padding: 0.75rem 0.95rem;
      background: var(--accent);
      color: white;
      font: inherit;
      cursor: pointer;
    }}
    .page-list {{ display: grid; gap: 0.55rem; }}
    .page-link {{
      width: 100%;
      text-align: left;
      background: #f0eadf;
      color: var(--ink);
      border: 1px solid transparent;
    }}
    .page-link:hover, .page-link.active {{ border-color: var(--accent); background: #e3f0ed; }}
    .page-type {{ display: block; color: var(--muted); font-size: 0.82rem; margin-top: 0.2rem; }}
    .content-meta {{ display: flex; flex-wrap: wrap; gap: 0.75rem; color: var(--muted); font-size: 0.92rem; margin-bottom: 1rem; }}
    .content-grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 1rem; }}
    .card {{ border: 1px solid var(--border); border-radius: 14px; padding: 1rem; background: #fff; }}
    .markdown-view h1:first-child {{ margin-top: 0; }}
    .markdown-view pre {{ background: #1f1a14; color: #f8f5ef; padding: 0.9rem; border-radius: 12px; overflow: auto; }}
    .markdown-view code {{ background: #f0eadf; padding: 0.1rem 0.35rem; border-radius: 6px; }}
    ul {{ padding-left: 1.2rem; }}
    details {{ margin-top: 1rem; }}
    @media (max-width: 900px) {{
      .shell {{ grid-template-columns: 1fr; }}
      aside {{ max-width: none; resize: none; }}
      .content-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class=\"shell\">
    <aside>
      <div class=\"eyebrow\">META-COMPILER Wiki Browser</div>
      <h1>Wiki {escape(source_version.upper())}</h1>
      <div class=\"status\" id=\"status\">Loading pages...</div>
      <form class=\"search\" id=\"search-form\">
        <input id=\"search-input\" type=\"search\" placeholder=\"Search concepts, equations, citations\" />
        <button type=\"submit\">Find</button>
      </form>
      <div class=\"page-list\" id=\"page-list\"></div>
    </aside>
    <main>
      <div class=\"eyebrow\">Selected Page</div>
      <h1 id=\"page-title\">Choose a page</h1>
      <div class=\"content-meta\" id=\"page-meta\"></div>
      <div class=\"content-grid\">
        <section class=\"card markdown-view\" id=\"page-body\">Use the list to open a wiki page.</section>
        <section class=\"card\">
          <h2>Relationships</h2>
          <div id=\"page-relationships\">No page loaded.</div>
          <h2>Citations</h2>
          <div id=\"page-citations\">No page loaded.</div>
        </section>
      </div>
      <details>
        <summary>Raw Markdown</summary>
        <pre id=\"page-raw\"></pre>
      </details>
    </main>
  </div>
  <script>
    const state = {{ pages: [], activePage: null }};

    function pageParam() {{
      const params = new URLSearchParams(window.location.search);
      return params.get('page');
    }}

    function updateStatus(text) {{
      document.getElementById('status').textContent = text;
    }}

    function renderList(items) {{
      const list = document.getElementById('page-list');
      list.innerHTML = '';
      for (const item of items) {{
        const button = document.createElement('button');
        button.className = 'page-link' + (state.activePage === item.id ? ' active' : '');
        button.innerHTML = `<strong>${{item.title}}</strong><span class=\"page-type\">${{item.type}}</span>`;
        button.addEventListener('click', () => loadPage(item.id, true));
        list.appendChild(button);
      }}
    }}

    function renderRelationships(relationships) {{
      const lines = [];
      if (relationships.outbound && relationships.outbound.length) {{
        lines.push('<h3>Outbound</h3><ul>' + relationships.outbound.map(item => `<li>${{item.target || item.value}}</li>`).join('') + '</ul>');
      }}
      if (relationships.inbound && relationships.inbound.length) {{
        lines.push('<h3>Inbound</h3><ul>' + relationships.inbound.map(item => `<li>${{item.source || item.value}}</li>`).join('') + '</ul>');
      }}
      return lines.join('') || 'No relationships recorded.';
    }}

    function renderCitations(citations) {{
      if (!citations.length) {{
        return 'No citations recorded.';
      }}
      return '<ul>' + citations.map(item => `<li><strong>${{item.citation_id}}</strong><br>${{item.human || 'No human-readable label'}} </li>`).join('') + '</ul>';
    }}

    async function loadIndex() {{
      const response = await fetch('/api/index');
      const payload = await response.json();
      state.pages = payload.pages;
      updateStatus(`${{payload.pages.length}} pages loaded from ${payload.source_version}.`);
      renderList(state.pages);
      const firstPage = pageParam() || (state.pages[0] && state.pages[0].id);
      if (firstPage) {{
        await loadPage(firstPage, false);
      }}
    }}

    async function loadPage(pageId, pushState) {{
      const response = await fetch(`/api/page?id=${{encodeURIComponent(pageId)}}`);
      if (!response.ok) {{
        updateStatus(`Failed to load page ${{pageId}}.`);
        return;
      }}
      const page = await response.json();
      state.activePage = page.id;
      renderList(state.pages);
      document.getElementById('page-title').textContent = page.title;
      document.getElementById('page-meta').innerHTML = [
        `<span>Type: ${{page.type}}</span>`,
        `<span>Path: ${{page.path}}</span>`,
        `<span>ID: ${{page.id}}</span>`
      ].join('');
      document.getElementById('page-body').innerHTML = page.body_html;
      document.getElementById('page-relationships').innerHTML = renderRelationships(page.relationships);
      document.getElementById('page-citations').innerHTML = renderCitations(page.citations);
      document.getElementById('page-raw').textContent = page.body_markdown;
      updateStatus(`Viewing ${{page.id}}.`);
      if (pushState) {{
        const url = new URL(window.location.href);
        url.searchParams.set('page', page.id);
        window.history.pushState({{ page: page.id }}, '', url);
      }}
    }}

    document.getElementById('search-form').addEventListener('submit', async (event) => {{
      event.preventDefault();
      const query = document.getElementById('search-input').value.trim();
      if (!query) {{
        renderList(state.pages);
        updateStatus(`${{state.pages.length}} pages loaded from {escape(source_version)}.`);
        return;
      }}

      const response = await fetch(`/api/search?q=${{encodeURIComponent(query)}}`);
      const payload = await response.json();
      updateStatus(`${{payload.results.length}} search results for "${{query}}".`);
      renderList(payload.results.map(item => ({{ id: item.concept_id, title: item.concept_id, type: item.type }})));
    }});

    window.addEventListener('popstate', async () => {{
      const page = pageParam();
      if (page) {{
        await loadPage(page, false);
      }}
    }});

    loadIndex();
  </script>
</body>
</html>
"""


def _json_response(handler: BaseHTTPRequestHandler, payload: dict[str, object], status: HTTPStatus = HTTPStatus.OK) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _html_response(handler: BaseHTTPRequestHandler, html: str) -> None:
    body = html.encode("utf-8")
    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _make_handler(state: WikiBrowserState):
    class WikiBrowserHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)

            if parsed.path == "/":
                _html_response(self, _render_shell_html(state.source_version))
                return

            if parsed.path == "/api/index":
                _json_response(
                    self,
                    {
                        "pages": state.query_interface.list_pages(),
                        "source_version": state.source_version,
                    },
                )
                return

            if parsed.path == "/api/search":
                query = parse_qs(parsed.query).get("q", [""])[0]
                _json_response(
                    self,
                    {
                        "query": query,
                        "results": state.query_interface.search_wiki(query, limit=24) if query else [],
                    },
                )
                return

            if parsed.path == "/api/page":
                page_id = parse_qs(parsed.query).get("id", [""])[0]
                payload = build_page_payload(state.query_interface, page_id)
                if payload is None:
                    _json_response(self, {"error": f"Unknown page: {page_id}"}, status=HTTPStatus.NOT_FOUND)
                    return
                _json_response(self, payload)
                return

            _json_response(self, {"error": "Not found"}, status=HTTPStatus.NOT_FOUND)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A003
            return

    return WikiBrowserHandler


def _bind_server(handler_cls, port: int) -> ThreadingHTTPServer:
    host = "127.0.0.1"
    if port == 0:
        return ThreadingHTTPServer((host, 0), handler_cls)

    for offset in range(10):
        try:
            return ThreadingHTTPServer((host, port + offset), handler_cls)
        except OSError:
            continue
    raise RuntimeError(f"Unable to bind wiki browser server near port {port}")


def create_wiki_browser_server(
    artifacts_root: Path,
    port: int = 7777,
    prefer_v1: bool = False,
) -> tuple[ThreadingHTTPServer, str, str]:
    paths = build_paths(artifacts_root)
    ensure_layout(paths)

    query_interface = WikiQueryInterface(paths, prefer_v2=not prefer_v1)
    source_version = "v2" if query_interface.pages_dir == paths.wiki_v2_pages_dir else "v1"
    state = WikiBrowserState(paths=paths, query_interface=query_interface, source_version=source_version)
    server = _bind_server(_make_handler(state), port=port)
    actual_port = int(server.server_address[1])
    url = f"http://127.0.0.1:{actual_port}/"
    return server, url, source_version


def run_wiki_browser(
    artifacts_root: Path,
    port: int = 7777,
    no_open: bool = False,
    prefer_v1: bool = False,
) -> dict[str, object]:
    server, url, source_version = create_wiki_browser_server(
        artifacts_root=artifacts_root,
        port=port,
        prefer_v1=prefer_v1,
    )

    print(f"Wiki browser serving {source_version} at {url}", flush=True)
    if not no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

    return {
        "url": url,
        "source_version": source_version,
    }