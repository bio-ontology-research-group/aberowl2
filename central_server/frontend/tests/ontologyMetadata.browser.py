"""Browser regression checks for ontology metadata. Requires Playwright Chromium.
Run from frontend: python tests/ontologyMetadata.browser.py
"""
from pathlib import Path
import functools, http.server, json, subprocess, tempfile, threading
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).resolve().parents[1]
CHECKS = 'async () => {\n    const {ontologyDisplayTitle:title, ontologyDescriptionText:text} = await import(\'/test/ontologyMetadata.js\');let count=0;\n    const equal=(a,b)=>{if(a!==b)throw new Error(JSON.stringify({actual:a,expected:b}));count++};\n    equal(title({name:\'ICPS network\',title:\'agent role\'}),\'ICPS network\');equal(title({name:\' \',title:\'Native title\'}),\'Native title\');equal(title({ontology:\'abc\'}),\'ABC\');equal(title(null,\'icps\'),\'ICPS\');\n    equal(text(\'Plain description\'),\'Plain description\');equal(text(\'<p>First</p><p>Second &amp; third</p>\'),\'First Second & third\');equal(text(\'A &lt; B\'),\'A < B\');equal(text(\'<a href="javascript:alert(1)">License</a>\'),\'License\');equal(text(\'<script>window.pwned=1</script><style>bad</style><iframe src="https://invalid.test/"></iframe><img src="https://invalid.test/x" onerror="window.pwned=1">Safe\'),\'Safe\');equal(text(\'&lt;img src=x onerror=alert(1)&gt;\'),\' <img src=x onerror=alert(1)>\'.trim());equal(text(\'A < B and C > D\'),\'A < B and C > D\');equal(text(\'\'), \'\');equal(text(undefined),\'\');equal(window.pwned, undefined);return count;\n  }'

def main():
    with tempfile.TemporaryDirectory() as temporary:
        out = Path(temporary)
        subprocess.run([str(ROOT/'node_modules/.bin/tsc'), str(ROOT/'src/utils/ontologyMetadata.ts'), '--outDir', str(out), '--target', 'ES2022', '--module', 'ES2022', '--skipLibCheck'], check=True)
        (out/'index.html').write_text('<!doctype html><title>Metadata regression checks</title>')
        class Handler(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args): pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(out)))
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=['--disable-dev-shm-usage'])
                page = browser.new_page(); external = []
                origin = f'http://127.0.0.1:{server.server_port}'
                page.on('request', lambda request: external.append(request.url) if not request.url.startswith(origin) else None)
                page.goto(origin+'/index.html')
                count = page.evaluate(CHECKS.replace('/test/ontologyMetadata.js', '/ontologyMetadata.js'))
                assert not external, external
                browser.close()
                print(json.dumps({'passed': count, 'external_requests': len(external)}))
        finally:
            server.shutdown(); server.server_close(); thread.join()

if __name__ == '__main__': main()
