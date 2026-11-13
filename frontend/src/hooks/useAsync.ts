import { useCallback, useEffect, useRef, useState } from 'react'

export interface AsyncState<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
  reload: () => Promise<void>
  setData: (d: T) => void
}

/** Minimal data-fetching hook: tracks loading/error, ignores stale responses. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T>()
  const [error, setError] = useState<Error>()
  const [loading, setLoading] = useState(true)
  const seq = useRef(0)

  const reload = useCallback(async () => {
    const id = ++seq.current
    try {
      const d = await fn()
      if (id === seq.current) {
        setData(d)
        setError(undefined)
      }
    } catch (e) {
      if (id === seq.current) setError(e as Error)
    } finally {
      if (id === seq.current) setLoading(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  useEffect(() => {
    setLoading(true)
    void reload()
  }, [reload])

  return { data, error, loading, reload, setData }
}
