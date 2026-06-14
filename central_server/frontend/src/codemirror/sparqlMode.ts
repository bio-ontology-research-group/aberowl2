import { StreamLanguage } from '@codemirror/language'

const KEYWORDS = new Set([
  'select', 'construct', 'describe', 'ask', 'where', 'from', 'named', 'prefix', 'base',
  'optional', 'union', 'minus', 'filter', 'bind', 'values', 'service', 'graph',
  'order', 'by', 'group', 'having', 'limit', 'offset', 'distinct', 'reduced',
  'as', 'a', 'asc', 'desc', 'not', 'exists', 'in', 'true', 'false',
  // AberOWL DL-frame keywords
  'owl', 'subeq', 'subclass', 'superclass', 'supeq', 'equivalent',
])

const FUNCTIONS = new Set([
  'str', 'lang', 'langmatches', 'datatype', 'bound', 'iri', 'uri', 'bnode',
  'rand', 'abs', 'ceil', 'floor', 'round', 'concat', 'strlen', 'ucase', 'lcase',
  'contains', 'strstarts', 'strends', 'strbefore', 'strafter', 'regex', 'replace',
  'count', 'sum', 'min', 'max', 'avg', 'sample', 'group_concat', 'coalesce', 'if',
])

/** Minimal SPARQL (+ AberOWL OWL-frame) syntax mode for CodeMirror. */
export const sparql = StreamLanguage.define({
  name: 'sparql',
  token(stream) {
    if (stream.eatSpace()) return null

    // Comments
    if (stream.match('#')) { stream.skipToEnd(); return 'comment' }

    // IRIs <...>
    if (stream.match(/^<[^>\s]*>/)) return 'string.special'

    // Strings (single, double, triple)
    const ch = stream.peek()
    if (ch === '"' || ch === "'") {
      const q = stream.next()!
      let prev = ''
      while (!stream.eol()) {
        const c = stream.next()!
        if (c === q && prev !== '\\') break
        prev = c
      }
      return 'string'
    }

    // Variables ?x $x
    if (stream.match(/^[?$][A-Za-z_][\w]*/)) return 'variableName'

    // Numbers
    if (stream.match(/^-?\d+\.?\d*([eE][+-]?\d+)?/)) return 'number'

    // Prefixed names foo:bar  (and bare a)
    if (stream.match(/^[A-Za-z_][\w.-]*:[\w.-]*/)) return 'typeName'

    // Words / keywords
    if (stream.match(/^[A-Za-z_][\w]*/)) {
      const word = (stream.current() || '').toLowerCase()
      if (KEYWORDS.has(word)) return 'keyword'
      if (FUNCTIONS.has(word)) return 'function'
      return null
    }

    // Operators / punctuation
    if (stream.match(/^[{}().;,*=<>!+\-/|^]/)) return 'operator'

    stream.next()
    return null
  },
})
