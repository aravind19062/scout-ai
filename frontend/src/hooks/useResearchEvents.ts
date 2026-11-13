import { useEffect, useRef, useState } from 'react'
import type { ProgressEvent } from '../types/api'

const NO_EVENTS: ProgressEvent[] = []

/**
 * Subscribes to the research progress SSE stream. The browser's EventSource reconnects
 * automatically and sends Last-Event-ID, so the server replays anything missed.
 */
export function useResearchEvents(jobId: string | undefined, onEvent?: (e: ProgressEvent) => void) {
  const [nonce, setNonce] = useState(0)
  const key = `${jobId}:${nonce}`
  // State is tagged with the stream it belongs to, so a new jobId/restart starts empty
  // without having to reset state synchronously inside the effect.
  const [stream, setStream] = useState({ key, events: [] as ProgressEvent[], ended: false })
  const [connected, setConnected] = useState(false)
  const current = stream.key === key ? stream : { key, events: NO_EVENTS, ended: false }
  const cb = useRef(onEvent)
  useEffect(() => {
    cb.current = onEvent
  }, [onEvent])

  useEffect(() => {
    if (!jobId) return
    const streamKey = `${jobId}:${nonce}`
    const seen = new Set<number>()
    const es = new EventSource(`/api/research/${jobId}/events`)
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.addEventListener('progress', (msg) => {
      const ev = JSON.parse((msg as MessageEvent).data) as ProgressEvent
      if (seen.has(ev.id)) return
      seen.add(ev.id)
      setStream((prev) =>
        prev.key === streamKey ? { ...prev, events: [...prev.events, ev] } : { key: streamKey, events: [ev], ended: false },
      )
      cb.current?.(ev)
    })
    es.addEventListener('end', () => {
      setStream((prev) => ({ key: streamKey, events: prev.key === streamKey ? prev.events : [], ended: true }))
      setConnected(false)
      es.close()
    })
    return () => es.close()
  }, [jobId, nonce])

  /** Re-open the stream (e.g. after resume/deepen, when the server had ended it). Replays from the start. */
  return { events: current.events, connected, ended: current.ended, restart: () => setNonce((n) => n + 1) }
}
