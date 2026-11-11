import gzip
import hashlib
import io
import stat
import zipfile
import sys
from pathlib import Path
import pytest
REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO / "central_server"))
from app.intake import updater
from tests.test_update_download import OWL, _Resp, download, leftovers
pytestmark = pytest.mark.unit

def package(members, compression=zipfile.ZIP_DEFLATED):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=compression) as archive:
        for name, content in members:
            archive.writestr(name, content)
    return output.getvalue()

@pytest.mark.parametrize('metadata_first', [False, True])
@pytest.mark.parametrize('wrapped', [False, True])
async def test_matching_project_files_preserve_exact_ontology_bytes(tmp_path, metadata_first, wrapped):
    members=[('release/native.owl', OWL), ('release/native.pprj', b'project metadata'), ('release/native.repository', b'repository metadata')]
    if metadata_first:members=members[1:]+members[:1]
    body=package(members)
    if wrapped:body=gzip.compress(body)
    result,dest,_=await download(tmp_path,_Resp(body,chunk=7))
    assert result=={'md5':hashlib.md5(OWL).hexdigest(),'size':len(OWL),'received':len(body)}
    assert dest.read_bytes()==OWL and leftovers(dest)==[dest.name]
    assert not (dest.parent/'release').exists()

async def test_companion_content_does_not_change_source_fingerprint(tmp_path):
    a,dest,_=await download(tmp_path,_Resp(package([('native.owl',OWL),('native.pprj',b'old')])) )
    b,_,_=await download(tmp_path,_Resp(package([('native.pprj',b'new'),('native.owl',OWL)])))
    assert a['md5']==b['md5']==hashlib.md5(OWL).hexdigest() and dest.read_bytes()==OWL

@pytest.mark.parametrize('members,fragment',[
    ([('native.owl',OWL),('second.owl',OWL)],'exactly one'),
    ([('native.owl',OWL),('different.pprj',b'x')],'matching'),
    ([('native.owl',OWL),('native.txt',b'x')],'matching'),
    ([('native.owl',OWL),('../native.pprj',b'x')],'safe relative'),
    ([('native.owl',OWL),('native.pprj',b'x'),('native.pprj',b'y')],'unique'),
    ([('native.owl',OWL),('native.pprj',b'x'),('native.repository',b'x'),('extra.txt',b'x')],'exactly one'),
])
async def test_ambiguous_or_unsafe_package_keeps_existing_file(tmp_path,members,fragment):
    dest=tmp_path/'flopo/updates/txn-1/flopo.owl';dest.parent.mkdir(parents=True);dest.write_bytes(b'served')
    result,_,_=await download(tmp_path,_Resp(package(members)))
    assert fragment in result['error'] and dest.read_bytes()==b'served' and leftovers(dest)==[dest.name]

async def test_companion_bound_is_independent_of_ontology_limit(tmp_path,monkeypatch):
    monkeypatch.setattr(updater,'_MAX_ZIP_COMPANION_BYTES',64)
    result,dest,_=await download(tmp_path,_Resp(package([('native.owl',OWL),('native.pprj',b'x'*65)])))
    assert 'companion exceeds' in result['error'] and leftovers(dest)==[]

async def test_corrupt_companion_crc_is_not_ignored(tmp_path):
    body=bytearray(package([('native.owl',OWL),('native.pprj',b'project-metadata')],zipfile.ZIP_STORED))
    body[body.index(b'project-metadata')]^=1
    result,dest,_=await download(tmp_path,_Resp(bytes(body)))
    assert 'ZIP' in result['error'] and leftovers(dest)==[]

async def test_symlink_companion_is_refused(tmp_path):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        archive.writestr('native.owl',OWL)
        item=zipfile.ZipInfo('native.pprj');item.create_system=3;item.external_attr=(stat.S_IFLNK|0o777)<<16;archive.writestr(item,b'target')
    result,dest,_=await download(tmp_path,_Resp(output.getvalue()))
    assert 'regular file' in result['error'] and leftovers(dest)==[]

async def test_companion_respects_stricter_operator_expansion_limit(tmp_path,monkeypatch):
    monkeypatch.setattr(updater,'MAX_EXPANDED_BYTES',1024)
    body=package([('native.owl',b'<rdf:RDF/>'),('native.pprj',b'x'*2048)])
    assert len(body)<1024
    result,dest,_=await download(tmp_path,_Resp(body))
    assert 'companion exceeds' in result['error'] and leftovers(dest)==[]
