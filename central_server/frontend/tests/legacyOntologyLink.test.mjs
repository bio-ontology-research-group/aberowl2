import assert from 'node:assert/strict'
import test from 'node:test'
import { legacyOntologySearch } from '../src/utils/legacyOntologyLink.ts'

const flopo = 'http://purl.obolibrary.org/obo/FLOPO_0980223'

test('opens the class in the reported classic FLOPO link', () => {
  const params = new URLSearchParams(legacyOntologySearch('', `#!${flopo}`))
  assert.equal(params.get('class'), flopo)
  assert.equal(params.get('tab'), 'browse')
})

test('keeps a class IRI fragment and raw percent escapes exactly', () => {
  const iri = 'https://example.org/has%20space#part'
  const params = new URLSearchParams(legacyOntologySearch('?q=existing', `#!${iri}`))
  assert.equal(params.get('class'), iri)
  assert.equal(params.get('q'), 'existing')
})

test('decodes a whole encoded IRI once', () => {
  const iri = 'https://example.org/has%20space#part'
  const params = new URLSearchParams(legacyOntologySearch('', `#!${encodeURIComponent(iri)}`))
  assert.equal(params.get('class'), iri)
})

test('preserves an explicit current class and tab over an old fragment', () => {
  const params = new URLSearchParams(legacyOntologySearch('?class=urn%3Acurrent&tab=info', `#!${flopo}`))
  assert.equal(params.get('class'), 'urn:current')
  assert.equal(params.get('tab'), 'info')
})

test('ignores ordinary anchors, empty and malformed legacy links', () => {
  for (const hash of ['', '#section', '#!', '#!not-an-iri', '#!http%3A%ZZ']) {
    assert.equal(legacyOntologySearch('', hash), null)
  }
})

test('supports URN class IRIs', () => {
  assert.equal(new URLSearchParams(legacyOntologySearch('', '#!urn:example:class')).get('class'), 'urn:example:class')
})
