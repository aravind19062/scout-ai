import { Link, Route, Routes } from 'react-router-dom'
import { EmptyState } from './components/ui'
import { AppLayout } from './layouts/AppLayout'
import { Dashboard } from './pages/Dashboard'
import { Library } from './pages/Library'
import { NewResearch } from './pages/NewResearch'
import { ResearchPage } from './pages/ResearchPage'

export default function App() {
  return (
    <Routes>
      <Route element={<AppLayout />}>
        <Route index element={<Dashboard />} />
        <Route path="new" element={<NewResearch />} />
        <Route path="research/:id" element={<ResearchPage />} />
        <Route path="library" element={<Library />} />
        <Route path="*" element={<EmptyState title="Page not found" action={<Link className="text-indigo-600" to="/">Back to dashboard</Link>} />} />
      </Route>
    </Routes>
  )
}
