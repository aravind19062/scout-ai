import { useEffect, useRef, useState } from 'react'
import type { ProgressEvent } from '../types/api'

/**
 * Subscribes to the research progress SSE stream. The browser's EventSource reconnects
 * automatically and sends Last-Event-ID, so the server replays anything missed.
 */
export function useResearchEvents(jobId: string | undefined, onEvent?: (e: ProgressEvent) => void) {
  const [events, setEvents] = useState<ProgressEvent[]>([])
  const [connected, setConnected] = useState(false)
  const [ended, setEnded] = useState(false)
  const [nonce, setNonce] = useState(0)
  const cb = useRef(onEvent)
  useEffect(() => {
    cb.current = onEvent
  }, [onEvent])

  useEffect(() => {
    if (!jobId) return
    setEvents([])
    setEnded(false)
    const seen = new Set<number>()
    const es = new EventSource(`/api/research/${jobId}/events`)
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.addEventListener('progress', (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as ProgressEvent
      if (seen.has(ev.id)) return
      seen.add(ev.id)
      setEvents((prev) => [...prev, ev])
      cb.current?.(ev)
    })
    es.addEventListener('end', () => {
      setEnded(true)
      setConnected(false)
      es.close()
    })
    return () => es.close()
  }, [jobId, nonce])

  /** Re-open the stream (e.g. after resume/deepen, when the server had ended it). Replays from the start. */
  return { events, connected, ended, restart: () => setNonce((n) => n + 1) }
}
