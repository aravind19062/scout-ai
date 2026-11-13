import { api } from '../services/api'
import type { DemoStatus } from '../types/api'
import { useAsync } from './useAsync'

/** Public-demo limits from the server (`DEMO_MODE`). `undefined` while loading. */
export function useDemo(): DemoStatus | undefined {
  return useAsync(() => api.config(), []).data?.demo
}
