import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Reference } from '../types/api'

/**
 * Renders report Markdown. Raw HTML in the source is NOT rendered (react-markdown default), and
 * unsafe link protocols are stripped by its default urlTransform. Citation links like [S3](url)
 * render as compact chips with the source title on hover.
 */
export function Markdown({ children, refs }: { children: string; refs?: Reference[] }) {
  const byLabel = new Map(refs?.map((r) => [r.label, r]))
  return (
    <div className="prose prose-slate prose-sm max-w-none prose-headings:font-semibold prose-a:text-indigo-600 prose-blockquote:font-normal prose-blockquote:not-italic prose-blockquote:text-slate-600 prose-code:before:content-none prose-code:after:content-none prose-code:rounded prose-code:bg-slate-100 prose-code:px-1 prose-code:font-medium">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children: c }) => {
            const text = String(Array.isArray(c) ? c.join('') : c)
            if (/^S\d+$/.test(text)) {
              const ref = byLabel.get(text)
              return (
                <a
                  href={href}
                  target="_blank"
                  rel="noopener noreferrer nofollow"
                  title={ref ? `${ref.title} — ${ref.domain ?? ''}` : href}
                  className="mx-0.5 inline-flex items-center rounded bg-indigo-50 px-1 py-px align-super text-[10px] font-semibold leading-none text-indigo-700 no-underline ring-1 ring-indigo-200 hover:bg-indigo-100"
                >
                  {text}
                </a>
              )
            }
            return <a href={href} target="_blank" rel="noopener noreferrer nofollow">{c}</a>
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  )
}
