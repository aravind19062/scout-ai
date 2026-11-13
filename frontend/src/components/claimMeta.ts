import { CheckCircle2, CircleDashed, CircleSlash, HelpCircle, ShieldAlert, XCircle } from 'lucide-react'
import type { ClaimStatus } from '../types/api'

export const CLAIM_META: Record<ClaimStatus, { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  supported: { label: 'Supported', cls: 'bg-emerald-50 text-emerald-700 ring-emerald-200', Icon: CheckCircle2 },
  partially_supported: { label: 'Partially supported', cls: 'bg-lime-50 text-lime-700 ring-lime-200', Icon: CircleDashed },
  conflicting: { label: 'Conflicting evidence', cls: 'bg-amber-50 text-amber-700 ring-amber-200', Icon: ShieldAlert },
  unsupported: { label: 'Unsupported', cls: 'bg-rose-50 text-rose-700 ring-rose-200', Icon: XCircle },
  unable_to_verify: { label: 'Unable to verify', cls: 'bg-slate-100 text-slate-600 ring-slate-200', Icon: HelpCircle },
  pending: { label: 'Not verified', cls: 'bg-slate-50 text-slate-500 ring-slate-200', Icon: CircleSlash },
}
