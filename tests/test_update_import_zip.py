import io,zipfile,hashlib,gzip,stat,sys
from pathlib import Path
import pytest
REPO=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(REPO / "central_server"))
from app.intake import updater
from tests.test_update_download import _Resp, download, leftovers
pytestmark=pytest.mark.unit

def owl(iri, imports=(), base=None):
    return ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns:owl="http://www.w3.org/2002/07/owl#"'+(' xml:base="'+base+'"' if base else '')+'><owl:Ontology rdf:about="'+iri+'">'+''.join('<owl:imports rdf:resource="'+x+'"/>' for x in imports)+'</owl:Ontology></rdf:RDF>').encode()
def package(files):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,body in files:z.writestr(name,body)
    return b.getvalue()
def bundle():
    root=owl('urn:root',['urn:module']);return root,[('module.owl',owl('urn:module',['urn:metadata'])),('metadata.owl',owl('urn:metadata')),('root.owl',root)]

@pytest.mark.parametrize('wrapped',[False,True])
@pytest.mark.parametrize('reverse',[False,True])
async def test_unique_import_root_preserves_bytes_not_first_filename(tmp_path,wrapped,reverse):
    root,files=bundle()
    if reverse:files=list(reversed(files))
    body=package(files)
    if wrapped:body=gzip.compress(body)
    result,dest,_=await download(tmp_path,_Resp(body,chunk=5));assert result['md5']==hashlib.md5(root).hexdigest() and result['size']==len(root) and dest.read_bytes()==root;assert leftovers(dest)==[dest.name] and not (dest.parent/'module.owl').exists()

@pytest.mark.parametrize('files,fragment',[
    ([('a.owl',owl('urn:a')),('b.owl',owl('urn:b'))],'exactly one'),
    ([('a.owl',owl('urn:a')),('b.owl',owl('urn:a'))],'duplicate'),
    ([('a.owl',owl('urn:a',['urn:b'])),('b.owl',owl('urn:b',['urn:missing']))],'completely contained'),
    ([('a.owl',owl('urn:a',['urn:b'])),('b.owl',owl('urn:b',['urn:a']))],'exactly one'),
    ([('a.owl',owl('urn:a',['urn:b'])),('b.owl',owl('urn:b')),('c.owl',owl('urn:c',['urn:c']))],'disconnected'),
    ([('a.owl',owl('urn:a',['urn:b'])),('../b.owl',owl('urn:b'))],'safe relative'),
    ([('a.owl',owl('urn:a',['urn:b'])),('b.owl',b'<invalid')],'valid RDF/XML'),
    ([('a.owl',owl('urn:a',['urn:b'])),('b.owl',b'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"/>')],'unnamed'),
])
async def test_ambiguous_incomplete_unsafe_and_disconnected_packages_refused(tmp_path,files,fragment):
    result,dest,_=await download(tmp_path,_Resp(package(files)));assert fragment in result['error'] and leftovers(dest)==[]

async def test_external_xml_entity_refused_without_dereference(tmp_path):
    module=b'<!DOCTYPE rdf:RDF [<!ENTITY leak SYSTEM "file:///etc/passwd">]>'+owl('urn:b')
    result,dest,_=await download(tmp_path,_Resp(package([('a.owl',owl('urn:a',['urn:b'])),('b.owl',module)])));assert 'External' in result['error'] and leftovers(dest)==[]

async def test_nested_entities_refused(tmp_path):
    module=b'<!DOCTYPE rdf:RDF [<!ENTITY a "urn:b"><!ENTITY b "&a;">]>'+owl('urn:b')
    result,dest,_=await download(tmp_path,_Resp(package([('a.owl',owl('urn:a',['urn:b'])),('b.owl',module)])));assert 'entities' in result['error'] and leftovers(dest)==[]

async def test_simple_internal_namespace_entities_and_xml_base_supported(tmp_path):
    module=b'<!DOCTYPE rdf:RDF [<!ENTITY ns "https://example.test/">]>'+owl('&ns;module.owl');root=owl('', ['module.owl'],base='https://example.test/root.owl');result,dest,_=await download(tmp_path,_Resp(package([('module.owl',module),('root.owl',root)])));assert 'error' not in result and dest.read_bytes()==root

async def test_expanded_bundle_total_is_bounded(tmp_path,monkeypatch):
    root,files=bundle();monkeypatch.setattr(updater,'_MAX_IMPORT_BUNDLE_BYTES',len(root));result,dest,_=await download(tmp_path,_Resp(package(files)));assert 'expanded byte limit' in result['error'] and leftovers(dest)==[]

async def test_unselected_corrupt_module_refused(tmp_path):
    root,files=bundle();b=io.BytesIO()
    with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_STORED) as z:
        for name,body in files:z.writestr(name,body)
    body=bytearray(b.getvalue());position=body.index(files[0][1]);body[position+10]^=1
    result,dest,_=await download(tmp_path,_Resp(bytes(body)));assert 'ZIP' in result['error'] and leftovers(dest)==[]

async def test_six_member_recursive_bundle(tmp_path):
    files=[('root.owl',owl('urn:root',['urn:a','urn:b','urn:c'])),('a.owl',owl('urn:a')),('b.owl',owl('urn:b')),('c.owl',owl('urn:c',['urn:d'])),('d.owl',owl('urn:d',['urn:e'])),('e.owl',owl('urn:e'))];result,dest,_=await download(tmp_path,_Resp(package(files)));assert dest.read_bytes()==files[0][1] and result['size']==len(files[0][1])

@pytest.mark.parametrize('count,accepted',[(32,True),(33,False)])
async def test_archive_member_limit_checked_before_accepting_complete_graph(tmp_path,count,accepted):
    files=[('root.owl',owl('urn:root',['urn:module:'+str(i) for i in range(count-1)]))]+[(str(i)+'.owl',owl('urn:module:'+str(i))) for i in range(count-1)];result,dest,_=await download(tmp_path,_Resp(package(files)))
    if accepted:assert dest.read_bytes()==files[0][1]
    else:assert 'error' in result and leftovers(dest)==[]

async def test_external_doctype_refused(tmp_path):
    module=b'<!DOCTYPE rdf:RDF SYSTEM "https://example.test/external.dtd">'+owl('urn:b');result,dest,_=await download(tmp_path,_Resp(package([('a.owl',owl('urn:a',['urn:b'])),('b.owl',module)])));assert 'External XML' in result['error'] and leftovers(dest)==[]
