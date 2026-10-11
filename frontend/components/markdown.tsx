import { Fragment, useMemo, type ReactNode } from 'react';

// Status words are matched against the whole cell (case-insensitive). Unknown words stay plain text.
const STATUS = new Map<string, string>([
  ['passed', 'good'], ['pass', 'good'],
  ['failed', 'bad'], ['fail', 'bad'], ['error', 'bad'], ['generation_failed', 'bad'],
  ['not_executed', 'warn'], ['inconclusive', 'warn'], ['pass_with_skips', 'warn'],
  ['skipped', ''], ['not_designed', ''],
]);
const ID_CELL = /^(CTL|PLN|REQ|TC|SPEC)-/;
const VERDICT = /^\*\*Verdict:\s*([^*]+?)\*\*(.*)$/;
const VERDICT_TONE: Record<string, string> = { PASS: 'good', PASS_WITH_SKIPS: 'warn', FAIL: 'bad', ERROR: 'bad', INCONCLUSIVE: 'warn' };

const plain = (text: string) => text.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[`*]/g, '').trim();
const slug = (text: string) => plain(text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'section';

function inline(text: string): ReactNode {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith('`')) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith('**')) return <strong key={i}>{part.slice(2, -2)}</strong>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link && /^https?:\/\//i.test(link[2])) return <a key={i} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    return part;
  });
}

// Report cells are React text only: status words become badges, ID-like cells become monospace.
function cell(content: string, header: boolean, report: boolean): ReactNode {
  if (!report) return inline(content);
  const value = plain(content);
  const status = header ? undefined : STATUS.get(value.toLowerCase());
  if (status !== undefined) return <span className={`badge ${status}`}>{value}</span>;
  if (!header && ID_CELL.test(value)) return <span className="cell-id">{inline(content)}</span>;
  return inline(content);
}

function renderMarkdown(text: string, report: boolean, idPrefix: string): ReactNode {
  const prefix = idPrefix.replace(/[^a-zA-Z0-9_-]/g, '-');
  const lines = text.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').split(/\r?\n/);
  const blocks: ReactNode[] = [];
  const toc: { id: string; title: string }[] = [];
  let tocAt = -1; let caption = 'Report table'; let headings = 0; let tables = 0;
  for (let i = 0; i < lines.length;) {
    const line = lines[i]; const key = i;
    if (!line.trim()) { i++; continue; }
    if (/^\s*```/.test(line)) {
      const language = line.trim().slice(3); const code: string[] = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++; blocks.push(<figure key={key}>{language && <figcaption>{language}</figcaption>}<pre><code>{code.join('\n')}</code></pre></figure>); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const Tag = `h${Math.min(heading[1].length + 1, 6)}` as 'h2';
      caption = plain(heading[2]);
      if (!report) { blocks.push(<Tag key={key}>{inline(heading[2])}</Tag>); i++; continue; }
      // Unique by construction: the heading ordinal is part of the id. Level-2 sections (##) feed the contents list.
      const id = `${prefix}-${headings++}-${slug(heading[2])}`;
      if (heading[1].length === 2) { if (tocAt < 0) tocAt = blocks.length; toc.push({ id, title: caption }); }
      blocks.push(<Tag key={key} id={id}>{inline(heading[2])}</Tag>); i++; continue;
    }
    if (/^\|/.test(line) && /^\|[\s:|-]+\|\s*$/.test(lines[i + 1] || '')) {
      const cells = (row: string) => row.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|'));
      const headers = cells(line); const rows: string[][] = []; i += 2;
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(cells(lines[i++]));
      const captionId = `${prefix}-table-${tables++}`;
      const table = <table>{report && <caption id={captionId} className="sr-only">{caption}</caption>}<thead><tr>{headers.map((h, j) => <th key={j}>{cell(h, true, report)}</th>)}</tr></thead><tbody>{rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}>{cell(c, false, report)}</td>)}</tr>)}</tbody></table>;
      blocks.push(report
        ? <div className="markdown-table" role="region" tabIndex={0} aria-labelledby={captionId} key={key}>{table}</div>
        : <div className="markdown-table" key={key}>{table}</div>); continue;
    }
    if (/^\s*(?:[-*+] |\d+\. )/.test(line)) {
      const ordered = /^\s*\d+\./.test(line); const items: string[] = [];
      const match = ordered ? /^\s*\d+\. / : /^\s*[-*+] /;
      while (i < lines.length && match.test(lines[i])) items.push(lines[i++].replace(match, ''));
      const Tag = ordered ? 'ol' : 'ul'; blocks.push(<Tag key={key}>{items.map((item, j) => <li key={j}>{inline(item)}</li>)}</Tag>); continue;
    }
    if (/^\s*([-*_])\1\1+\s*$/.test(line)) { blocks.push(<hr key={key} />); i++; continue; }
    if (/^>/.test(line)) { blocks.push(<blockquote key={key}>{inline(line.replace(/^>\s?/, ''))}</blockquote>); i++; continue; }
    const paragraph = [line]; i++;
    while (i < lines.length && lines[i].trim() && !/^(?:#{1,6}\s|\s*```|\||>|\s*[-*+] |\s*\d+\. )/.test(lines[i])) paragraph.push(lines[i++]);
    const verdict = report && paragraph.length === 1 ? line.trim().match(VERDICT) : null;
    if (verdict) {
      const name = verdict[1].trim().toUpperCase(); const meta = verdict[2].replace(/^\s*·\s*/, '').trim();
      blocks.push(<div key={key} role="status" className={`report-verdict ${VERDICT_TONE[name] || 'neutral'}`}><span className="eyebrow">Verdict</span><strong>{name}</strong>{meta && <span className="report-verdict-meta">{inline(meta)}</span>}</div>);
      continue;
    }
    blocks.push(<p key={key}>{paragraph.map((part, j) => <Fragment key={j}>{j > 0 && ' '}{inline(part)}</Fragment>)}</p>);
  }
  if (report && toc.length >= 4 && tocAt >= 0) blocks.splice(tocAt, 0, <nav key="toc" className="report-toc" aria-label="Report contents"><strong>Contents</strong><ol>{toc.map(entry => <li key={entry.id}><a href={`#${entry.id}`}>{entry.title}</a></li>)}</ol></nav>);
  return <div className={`markdown-document${report ? ' markdown-report' : ''}`}>{blocks}</div>;
}

// Render Markdown as React nodes; embedded HTML stays inert text. Variant "report" adds badges, verdict banner,
// contents, table captions and anchors. Parsing is memoised so polling does not re-parse large reports.
export default function Markdown({ text, variant = 'document', idPrefix = 'doc' }: { text: string; variant?: 'document' | 'report'; idPrefix?: string }) {
  return useMemo(() => renderMarkdown(text, variant === 'report', idPrefix), [text, variant, idPrefix]);
}
