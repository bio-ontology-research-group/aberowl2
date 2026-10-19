"""Discovery must fill source metadata without running ontology updates."""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'central_server'))
from app.intake import bioportal as bp
from app.intake.source_sync import merge_source


@pytest.mark.asyncio
@pytest.mark.parametrize('paged', [False, True])
async def test_catalogue_shapes(monkeypatch, paged):
    row = {'acronym': 'TEST', 'links': {'download': 'http://data.bioontology.org/ontologies/TEST/download'}}
    response = {'collection': [row], 'links': {}} if paged else [row]
    monkeypatch.setattr(bp, '_get_json', AsyncMock(return_value=response))
    report = {}
    assert await bp._fetch_all_ontologies(None, report) == [row]
    assert report == {'state': 'ok', 'discovered': 1}


@pytest.mark.asyncio
async def test_download_link_avoids_submission_call(monkeypatch):
    monkeypatch.setattr(bp, 'BIOPORTAL_API_KEY', 'test-only')
    rows = [dict(acronym=k, links={'download': f'http://data.bioontology.org/ontologies/{k}/download'}) for k in ['TEST','OBO','NOT_HOSTED']]
    fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr(bp, '_get_json', fetch)
    records = await bp.fetch_bioportal_ontologies({'obo'}, include_ids={'test'})
    assert len(records) == 1
    assert records[0]['source_url'] == 'https://data.bioontology.org/ontologies/TEST/download'
    assert fetch.await_count == 1


@pytest.mark.asyncio
async def test_submission_hypermedia(monkeypatch):
    monkeypatch.setattr(bp, '_get_json', AsyncMock(return_value={'links': {'download': 'https://data.bioontology.org/ontologies/T/submissions/1/download'}}))
    assert await bp._fetch_download_url(None, 'T', asyncio.Semaphore(1)) == 'https://data.bioontology.org/ontologies/T/submissions/1/download'


@pytest.mark.asyncio
@pytest.mark.parametrize('next_url', ['https://untrusted.example/x', 'https://data.bioontology.org/ontologies?page=2'])
async def test_pagination_is_bounded(monkeypatch, next_url):
    fetch = AsyncMock(return_value={'collection': [{'acronym':'T'}], 'links': {'nextPage': next_url}})
    monkeypatch.setattr(bp, '_get_json', fetch)
    report = {}
    assert await bp._fetch_all_ontologies(None, report) == []
    assert report['state'] in ('invalid_navigation_link','pagination_cycle')
    assert fetch.await_count <= 2


@pytest.mark.asyncio
async def test_missing_credentials_report(monkeypatch):
    monkeypatch.setattr(bp, 'BIOPORTAL_API_KEY', '')
    report = {}
    assert await bp.fetch_bioportal_ontologies(set(), report=report) == []
    assert report['state'] == 'missing_credentials'


@pytest.mark.parametrize('guard', ['update_txn', 'transfer_release_pending'])
def test_active_updates_are_untouched(guard):
    entry = {'url':'http://worker', guard:{'id':'job'}}
    before = dict(entry)
    assert not merge_source(entry, {'source':'obofoundry','source_url':'https://example.org/t.owl'}, 't')
    assert entry == before


def test_metadata_allowlist_and_url_validators():
    entry = {'url':'http://worker','secret_key':'private','source_md5':'served-checksum', 'active_es_index':'old', 'source_etag':'old-etag','source_last_modified':'old-date'}
    incoming = {'source':'obofoundry','source_url':'https://example.org/t.owl','url':'http://evil','secret_key':'evil','source_md5':'evil','active_es_index':'evil'}
    assert merge_source(entry, incoming, 't')
    assert entry['url'] == 'http://worker' and entry['secret_key'] == 'private'
    assert entry['source_md5'] == 'served-checksum' and entry['active_es_index'] == 'old'
    assert 'source_etag' not in entry and 'source_last_modified' not in entry
    assert not merge_source(entry, incoming, 't')
    before = dict(entry)
    assert not merge_source(entry, {'source':'bioportal','source_url':'https://example.org/other.owl'}, 't')
    assert entry == before


class Redis:
    def __init__(self, rows):
        self.rows = {k:json.dumps(v) for k,v in rows.items()}
        self.values = {}
    async def hgetall(self, key): return dict(self.rows)
    async def hget(self, key, oid): return self.rows.get(oid)
    async def hset(self, key, oid, value): self.rows[oid] = value
    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values: return False
        self.values[key] = value
        return True
    async def get(self, key): return self.values.get(key)
    async def eval(self, code, count, key, value):
        if self.values.get(key) == value: self.values.pop(key)


@pytest.mark.asyncio
@pytest.mark.parametrize('dry_run', [False, True])
async def test_sync_coverage_case_preservation_and_protection(monkeypatch, dry_run):
    import app.main as m
    r = Redis({'GO': {'url':'http://worker', 'active_es_index':'served'}, 'flopo': {'url':'http://worker','source_url':'https://protected'}, 'other': {'url':'http://worker'}})
    monkeypatch.setattr(m, 'redis_client', r)
    monkeypatch.setattr(m, 'SOURCE_SYNC_EXCLUDE', {'flopo'})
    async def obo(*,report):
        report['state']='ok'
        return [{'ontology_id':k,'source':'obofoundry','source_url':f'https://example.org/{k}.owl'} for k in ['go','flopo','unhosted']]
    async def bio(*args,report,**kwargs):
        report['state']='missing_credentials'
        return []
    monkeypatch.setattr(m, 'fetch_obofoundry_ontologies', obo)
    monkeypatch.setattr(m, 'fetch_bioportal_ontologies', bio)
    monkeypatch.setattr(m, '_load_manual_ontologies', AsyncMock(return_value=[]))
    before=dict(r.rows)
    report=await m._sync_sources_once(dry_run=dry_run)
    assert report['status']=='partial'
    assert report['changed']==['GO']
    assert report['missing_sources']==['other']
    assert set(r.rows)==set(before)
    assert r.rows['flopo']==before['flopo']
    if dry_run: assert r.rows==before
    else:
        go=json.loads(r.rows['GO'])
        assert go['active_es_index']=='served'
        assert go['source_url']=='https://example.org/go.owl'
    assert await m._claim_source_sync()
    assert await m._claim_source_sync() is None


@pytest.mark.asyncio
async def test_registration_source_preserves_existing_and_auth(monkeypatch):
    import app.main as m
    r=Redis({})
    monkeypatch.setattr(m,'redis_client',r)
    monkeypatch.setattr(m,'_write_servers_to_file',AsyncMock())
    monkeypatch.setattr(m,'fetch_and_update_server_metadata',AsyncMock())
    result=await m.register_server(m.RegistrationRequest(ontology='T',url='http://worker',source_url='https://example.org/t.owl'))
    await asyncio.sleep(0)
    assert json.loads(r.rows['T'])['source_url']=='https://example.org/t.owl'
    key=result['secret_key']
    await m.register_server(m.RegistrationRequest(ontology='T',url='http://worker',secret_key=key,source_url='https://example.org/new.owl'))
    await asyncio.sleep(0)
    assert json.loads(r.rows['T'])['source_url']=='https://example.org/t.owl'
    from fastapi import HTTPException
    before=dict(r.rows)
    with pytest.raises(HTTPException) as error:
        await m.register_server(m.RegistrationRequest(ontology='T',url='http://evil',secret_key='wrong',source_url='https://evil/source'))
    assert error.value.status_code==403 and r.rows==before


@pytest.mark.asyncio
async def test_source_report_requires_admin_and_reports_interruption(monkeypatch):
    import app.main as m
    from httpx import ASGITransport, AsyncClient
    r=Redis({})
    monkeypatch.setattr(m,'redis_client',r)
    r.values[m.SOURCE_SYNC_REPORT]=json.dumps({'run_id':'expired','status':'running'})
    async with AsyncClient(transport=ASGITransport(app=m.app),base_url='http://test') as c:
        assert (await c.get('/admin/source_sync_status')).status_code==401
        assert (await c.post('/admin/sync_sources?dry_run=true')).status_code==401
        result=await c.get('/admin/source_sync_status',auth=(m.ADMIN_USER,m.ADMIN_PASSWORD))
        assert result.json()['status']=='interrupted'
        await r.set(m.SOURCE_SYNC_LOCK,'running')
        busy=await c.post('/admin/sync_sources?dry_run=true',auth=(m.ADMIN_USER,m.ADMIN_PASSWORD))
        assert busy.status_code==409


@pytest.mark.parametrize('auto,source,expected', [('false',None,[False,False]),('true',None,[True,True]),('false','true',[False,True]),('true','false',[True,False])])
def test_independent_scheduling_flags(auto,source,expected):
    import subprocess,os
    env=dict(os.environ,ENABLE_AUTO_SYNC=auto,PYTHONPATH=str(Path(__file__).resolve().parents[1]/'central_server'))
    if source is None: env.pop('ENABLE_SOURCE_SYNC',None)
    else: env['ENABLE_SOURCE_SYNC']=source
    result=subprocess.run([sys.executable,'-c','import json;import app.main as m;print(json.dumps([m.ENABLE_AUTO_SYNC,m.ENABLE_SOURCE_SYNC]))'],env=env,capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)==expected
