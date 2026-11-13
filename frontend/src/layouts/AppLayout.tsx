import clsx from 'clsx'
import { FlaskConical, LayoutDashboard, Library, Plus, Telescope } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'
import { useAsync } from '../hooks/useAsync'
import { api } from '../services/api'

const nav = [
  { to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true },
  { to: '/new', label: 'New Research', icon: Plus },
  { to: '/library', label: 'Document Library', icon: Library },
]

export function AppLayout() {
  const cfg = useAsync(() => api.config(), [])
  return (
    <div className="flex h-full">
      <aside className="no-print hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white md:flex">
        <div className="flex items-center gap-2.5 px-5 py-5">
          <div className="rounded-lg bg-indigo-600 p-1.5 text-white"><Telescope className="h-5 w-5" /></div>
          <div>
            <div className="text-sm font-bold tracking-wide text-slate-900">SCOUT AI</div>
            <div className="text-[11px] text-slate-500">Autonomous research</div>
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5 px-3">
          {nav.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                clsx('flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm font-medium transition',
                  isActive ? 'bg-indigo-50 text-indigo-700' : 'text-slate-600 hover:bg-slate-50 hover:text-slate-900')
              }
            >
              <Icon className="h-4 w-4" /> {label}
            </NavLink>
          ))}
        </nav>
        <div className="m-3 rounded-lg border border-slate-100 bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-500">
          {cfg.data ? (
            <>
              <div className="mb-1 font-semibold text-slate-600">Providers</div>
              LLM: {cfg.data.providers.llm === 'openai' ? cfg.data.providers.model : 'mock'}<br />
              Search: {cfg.data.providers.search}<br />
              Embeddings: {cfg.data.providers.embeddings}
            </>
          ) : cfg.error ? <span className="text-rose-600">Backend unreachable</span> : '…'}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        {/* Mobile top bar */}
        <div className="no-print flex items-center justify-between border-b border-slate-200 bg-white px-4 py-3 md:hidden">
          <div className="flex items-center gap-2 font-bold"><Telescope className="h-5 w-5 text-indigo-600" /> SCOUT AI</div>
          <div className="flex gap-3 text-sm">
            {nav.map((n) => <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? 'text-indigo-600' : 'text-slate-500')}>{n.label.split(' ')[0]}</NavLink>)}
          </div>
        </div>
        {cfg.data?.mock_mode && (
          <div className="no-print flex items-center gap-2 border-b border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-800">
            <FlaskConical className="h-4 w-4" />
            <span><b>Mock mode.</b> Offline providers and a fictional fixture corpus are active. Results are not real research. Configure API keys in <code>.env</code> for real research.</span>
          </div>
        )}
        {cfg.data?.demo.enabled && (
          <div className="no-print flex flex-wrap items-center gap-x-2 border-b border-indigo-200 bg-indigo-50 px-6 py-2 text-xs text-indigo-800">
            <Telescope className="h-4 w-4" />
            <span><b>Public demo.</b> Runs on a shared daily budget: up to {cfg.data.demo.jobs_per_day} sessions per visitor per day, depth up to “{cfg.data.demo.max_depth}”. Your sessions are visible only to you.</span>
            <span className="text-indigo-600">{cfg.data.demo.jobs_used_today}/{cfg.data.demo.jobs_per_day} used today</span>
          </div>
        )}
        <main className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8"><Outlet /></div>
        </main>
      </div>
    </div>
  )
}
