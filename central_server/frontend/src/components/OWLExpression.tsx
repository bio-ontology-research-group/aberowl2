/**
 * Manchester OWL Syntax highlighting component.
 *
 * Highlights OWL keywords (some, only, and, or, not, that, min, max, exactly,
 * value, Self, inverse), class names (in single quotes or CamelCase),
 * property names, IRIs, and restrictions.
 */

const OWL_KEYWORDS = new Set([
  'some', 'only', 'and', 'or', 'not', 'that',
  'min', 'max', 'exactly', 'value', 'Self', 'inverse',
  'SubClassOf', 'EquivalentTo', 'DisjointWith', 'Type',
  'SubPropertyOf', 'InverseOf', 'Domain', 'Range',
])

const OWL_BUILTINS = new Set([
  'owl:Thing', 'owl:Nothing', 'Thing', 'Nothing',
])

interface Props {
  expression: string
  className?: string
}

export default function OWLExpression({ expression, className = '' }: Props) {
  const tokens = tokenize(expression)

  return (
    <span className={`font-mono text-[13px] leading-relaxed ${className}`}>
      {tokens.map((tok, i) => {
        switch (tok.type) {
          case 'keyword':
            return <span key={i} className="text-amber-600 font-bold">{tok.text}</span>
          case 'quoted':
            return <span key={i} className="text-emerald-700">{tok.text}</span>
          case 'iri':
            return <span key={i} className="text-blue-600 break-all">{tok.text}</span>
          case 'builtin':
            return <span key={i} className="text-purple-600 font-semibold">{tok.text}</span>
          case 'paren':
            return <span key={i} className="text-gray-400 font-bold">{tok.text}</span>
          case 'number':
            return <span key={i} className="text-rose-600">{tok.text}</span>
          default:
            return <span key={i} className="text-gray-700">{tok.text}</span>
        }
      })}
    </span>
  )
}

interface Token {
  type: 'keyword' | 'quoted' | 'iri' | 'builtin' | 'paren' | 'number' | 'text'
  text: string
}

function tokenize(expr: string): Token[] {
  const tokens: Token[] = []
  let i = 0

  while (i < expr.length) {
    // Whitespace
    if (/\s/.test(expr[i])) {
      let ws = ''
      while (i < expr.length && /\s/.test(expr[i])) { ws += expr[i]; i++ }
      tokens.push({ type: 'text', text: ws })
      continue
    }

    // Quoted name: 'name with spaces'
    if (expr[i] === "'") {
      let q = "'"
      i++
      while (i < expr.length && expr[i] !== "'") { q += expr[i]; i++ }
      if (i < expr.length) { q += "'"; i++ }
      tokens.push({ type: 'quoted', text: q })
      continue
    }

    // IRI: <http://...>
    if (expr[i] === '<') {
      let iri = '<'
      i++
      while (i < expr.length && expr[i] !== '>') { iri += expr[i]; i++ }
      if (i < expr.length) { iri += '>'; i++ }
      tokens.push({ type: 'iri', text: iri })
      continue
    }

    // Parentheses and braces
    if ('(){}[]'.includes(expr[i])) {
      tokens.push({ type: 'paren', text: expr[i] })
      i++
      continue
    }

    // Word or number
    if (/[a-zA-Z0-9_:.\-]/.test(expr[i])) {
      let word = ''
      while (i < expr.length && /[a-zA-Z0-9_:.\-#\/]/.test(expr[i])) { word += expr[i]; i++ }
      if (OWL_KEYWORDS.has(word)) {
        tokens.push({ type: 'keyword', text: word })
      } else if (OWL_BUILTINS.has(word)) {
        tokens.push({ type: 'builtin', text: word })
      } else if (/^\d+$/.test(word)) {
        tokens.push({ type: 'number', text: word })
      } else {
        tokens.push({ type: 'quoted', text: word })
      }
      continue
    }

    // Other characters
    tokens.push({ type: 'text', text: expr[i] })
    i++
  }

  return tokens
}
