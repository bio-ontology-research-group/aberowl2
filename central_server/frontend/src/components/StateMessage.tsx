interface Props {
  kind?: 'error' | 'empty'
  title: string
  detail?: string
  onRetry?: () => void
}

/** Inline error / empty placeholder with an optional retry action. */
export default function StateMessage({ kind = 'empty', title, detail, onRetry }: Props) {
  const isError = kind === 'error'
  return (
    <div className={`rounded-lg border p-6 text-center text-sm ${
      isError ? 'border-red-200 bg-red-50 text-red-700' : 'border-gray-200 bg-white text-gray-500'
    }`}>
      <p className="font-medium">{title}</p>
      {detail && <p className="mt-1 text-xs opacity-80 break-words">{detail}</p>}
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-3 px-3 py-1.5 rounded-md border border-current text-xs font-medium hover:opacity-80"
        >
          Retry
        </button>
      )}
    </div>
  )
}
