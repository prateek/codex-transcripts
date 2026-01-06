from __future__ import annotations

import subprocess
from pathlib import Path

import click


GIST_PREVIEW_JS = r"""
(function() {
  function isGistHost() {
    return location.hostname === 'gisthost.github.io' || location.hostname === 'gistpreview.github.io';
  }

  function getGistPrefix() {
    var qs = location.search || '';
    // Query-string preview expects everything under ?{gistId}/...
    var qm = qs.match(/^\?([a-f0-9]+)(?:\/|$)/i);
    if (qm) return '?' + qm[1] + '/';

    // Path-based preview expects everything under /{gistId}/...
    var parts = location.pathname.split('/').filter(Boolean);
    if (parts.length && /^[a-f0-9]+$/i.test(parts[0])) return '/' + parts[0] + '/';

    return null;
  }

  function fixLink(a, prefix) {
    if (!a || !a.getAttribute) return;
    var href = a.getAttribute('href');
    if (!href) return;
    if (href.startsWith('http://') || href.startsWith('https://') || href.startsWith('#') || href.startsWith('?') || href.startsWith('/')) return;
    // Rewrite relative links to include the gist prefix for gist preview.
    a.setAttribute('href', prefix + href.replace(/^\\.\\//, ''));
  }

  function fixAllLinks(prefix, root) {
    var container = root || document;
    container.querySelectorAll('a[href]').forEach(function(a) { fixLink(a, prefix); });
  }

  function init() {
    if (!isGistHost()) return;
    var prefix = getGistPrefix();
    if (!prefix) return;
    fixAllLinks(prefix, document);

    // Observe dynamic content changes (SPA navigation on gistpreview.github.io)
    var observer = new MutationObserver(function(mutations) {
      mutations.forEach(function(m) {
        m.addedNodes.forEach(function(node) {
          if (node.nodeType === 1) {
            fixAllLinks(prefix, node);
          }
        });
      });
    });

    function startObserving() {
      if (document.body) {
        observer.observe(document.body, { childList: true, subtree: true });
      } else {
        setTimeout(startObserving, 10);
      }
    }
    startObserving();

    // Handle fragment navigation after dynamic content loads
    function scrollToFragment() {
      var hash = window.location.hash;
      if (!hash) return false;
      var targetId = hash.substring(1);
      var target = document.getElementById(targetId);
      if (target) {
        target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        return true;
      }
      return false;
    }

    if (!scrollToFragment()) {
      var delays = [100, 300, 500, 1000, 2000];
      delays.forEach(function(delay) { setTimeout(scrollToFragment, delay); });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


def inject_gist_preview_js(output_dir: str | Path) -> None:
    output_dir = Path(output_dir)
    for html_file in output_dir.glob("*.html"):
        content = html_file.read_text(encoding="utf-8")
        if "</body>" in content:
            content = content.replace("</body>", f"<script>{GIST_PREVIEW_JS}</script>\n</body>")
            html_file.write_text(content, encoding="utf-8")


def create_gist(output_dir: str | Path, *, public: bool = False) -> tuple[str, str]:
    output_dir = Path(output_dir)
    html_files = list(output_dir.glob("*.html"))
    if not html_files:
        raise click.ClickException("No HTML files found to upload to gist.")

    asset_files = list((output_dir / "chunks").glob("*.js"))

    cmd: list[str] = ["gh", "gist", "create"]
    cmd.extend(str(f) for f in sorted(html_files))
    cmd.extend(str(f) for f in sorted(asset_files))
    if public:
        cmd.append("--public")

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        gist_url = result.stdout.strip()
        gist_id = gist_url.rstrip("/").split("/")[-1]
        return gist_id, gist_url
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        raise click.ClickException(f"Failed to create gist: {error_msg}") from e
    except FileNotFoundError as e:
        raise click.ClickException(
            "gh CLI not found. Install it from https://cli.github.com/ and run 'gh auth login'."
        ) from e
