import { Fragment, useMemo, type ReactNode } from 'react';

// Renders SKILL.md documents only (Agent skills detail). Reports and run evidence never pass through here: they are
// parsed into structured blocks (lib/report.ts) and shown as sections, tables and verdicts.
function inline(text: string): ReactNode {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g).map((part, i) => {
    if (part.startsWith('`') && part.length > 2) return <code key={i}>{part.slice(1, -1)}</code>;
    if (part.startsWith('**') && part.length > 4) return <strong key={i}>{part.slice(2, -2)}</strong>;
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link && /^https?:\/\//i.test(link[2])) return <a key={i} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    return <Fragment key={i}>{part}</Fragment>;
  });
}

function renderDocument(text: string): ReactNode {
  const lines = text.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').split(/\r?\n/);
  const blocks: ReactNode[] = [];
  for (let i = 0; i < lines.length;) {
    const line = lines[i]; const key = i;
    if (!line.trim()) { i++; continue; }
    if (/^\s*```/.test(line)) {
      const code: string[] = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++; blocks.push(<figure key={key}><pre><code>{code.join('\n')}</code></pre></figure>); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) { const Tag = `h${Math.min(heading[1].length + 1, 6)}` as 'h2'; blocks.push(<Tag key={key}>{inline(heading[2])}</Tag>); i++; continue; }
    if (/^\s*(?:[-*+] |\d+\. )/.test(line)) {
      const ordered = /^\s*\d+\./.test(line); const items: string[] = [];
      const match = ordered ? /^\s*\d+\. / : /^\s*[-*+] /;
      while (i < lines.length && match.test(lines[i])) items.push(lines[i++].replace(match, ''));
      const Tag = ordered ? 'ol' : 'ul'; blocks.push(<Tag key={key}>{items.map((item, j) => <li key={j}>{inline(item)}</li>)}</Tag>); continue;
    }
    if (/^\s*([-*_])\1\1+\s*$/.test(line)) { blocks.push(<hr key={key} />); i++; continue; }
    if (/^>/.test(line)) { blocks.push(<blockquote key={key}>{inline(line.replace(/^>\s?/, ''))}</blockquote>); i++; continue; }
    const paragraph = [line]; i++;
    while (i < lines.length && lines[i].trim() && !/^(?:#{1,6}\s|\s*```|>|\s*[-*+] |\s*\d+\. )/.test(lines[i])) paragraph.push(lines[i++]);
    blocks.push(<p key={key}>{paragraph.map((part, j) => <Fragment key={j}>{j > 0 && ' '}{inline(part)}</Fragment>)}</p>);
  }
  return blocks;
}

/** SKILL.md text as React nodes; embedded HTML stays inert text. Parsing is memoised. */
export default function Markdown({ text }: { text: string }) {
  return useMemo(() => <div className="markdown-document">{renderDocument(text)}</div>, [text]);
}
