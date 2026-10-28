# Production edge nginx — AberOWL routing

These files are installed on the production central host at
`/etc/nginx/aberowl/` and pulled in by a single line inside the
`aber-owl.net` server block of `/etc/nginx/conf.d/default.conf`:

```nginx
include /etc/nginx/aberowl/*.conf;
```

That include is the only edit ever needed to the shared file, which also serves
`chem.aber-owl.net` and is not ours. Everything AberOWL-specific lives here.

## Files

| file | why |
|---|---|
| `media.conf` | Serves `/media/` from the application. Replaced an `alias` to the AberOWL 1 store at `/opt/aberowl/aberowlweb/media/`, which meant `download_url` pointed at files nobody was serving any more and 404'd for all 971 ontologies. |
| `api.conf` | Raises `proxy_buffer_size` for `/api/`. `/api/sparql` answers an AberOWL 1 query with a 302 whose `Location` header carries the rewritten query — about 5.8 KB when a frame resolves to ~95 classes. nginx buffers response *headers* in `proxy_buffer_size` regardless of `proxy_buffering off`, which governs the body, so the 4 KB default rejected the response with 502. |

## Installing

```bash
# as root on the central host
cp /etc/nginx/conf.d/default.conf /etc/nginx/conf.d/default.conf.bak-$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p /etc/nginx/aberowl
install -m 644 media.conf api.conf /etc/nginx/aberowl/
# add the include line to the aber-owl.net server block, once
nginx -t && systemctl reload nginx
```

`nginx -t` must pass before reloading. Reload is graceful; in-flight requests finish.

## Rolling back

```bash
cp $(ls -t /etc/nginx/conf.d/default.conf.bak-* | head -1) /etc/nginx/conf.d/default.conf
rm -rf /etc/nginx/aberowl
nginx -t && systemctl reload nginx
```

## Note

Archived AberOWL 1 download URLs keep resolving, but return the **current** file:
the application accepts any submission-number segment and serves what is held
now. `/media/ontologies/FMA/139/fma.owl` therefore returns today's FMA, not the
2020 copy the old store held.
