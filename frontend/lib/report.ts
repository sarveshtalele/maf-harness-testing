/**
 * Turns a saved step report into structured blocks for the UI. The Markdown source is never displayed; it is parsed once
 * into sections, tables, lists and verdicts, and the Markdown file itself is offered only as a download.
 */
export type Block =
  | { type: 'p'; text: string }
  | { type: 'h3'; text: string }
  | { type: 'list'; ordered: boolean; items: string[] }
  | { type: 'table'; headers: string[]; rows: string[][] }
  | { type: 'code'; language: string; text: string }
  | { type: 'quote'; text: string }
  | { type: 'verdict'; name: string; meta: string };
export type ReportSection = { title: string; blocks: Block[] };
export type Report = { title: string; sections: ReportSection[] };

const VERDICT = /^\*\*Verdict:\s*([^*]+?)\*\*(.*)$/;
const TABLE_RULE = /^\|[\s:|-]+\|\s*$/;
export const plain = (value: string) => value.replace(/\[([^\]]+)\]\([^)]+\)/g, '$1').replace(/[`*]/g, '').trim();

const cells = (row: string) => row.trim().replace(/^\||\|$/g, '').split(/(?<!\\)\|/).map(c => c.trim().replace(/\\\|/g, '|'));

export function parseReport(source: string): Report {
  const lines = source.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n/, '').split(/\r?\n/);
  let title = '';
  const sections: ReportSection[] = [];
  let current: ReportSection = { title: '', blocks: [] };
  const flush = () => { if (current.title || current.blocks.length) sections.push(current); };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (/^\s*```/.test(line)) {
      const language = line.trim().slice(3).trim(); const code: string[] = []; i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++; current.blocks.push({ type: 'code', language, text: code.join('\n') }); continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      if (heading[1].length === 1) { if (!title) title = plain(heading[2]); i++; continue; }
      if (heading[1].length === 2) { flush(); current = { title: plain(heading[2]), blocks: [] }; i++; continue; }
      current.blocks.push({ type: 'h3', text: plain(heading[2]) }); i++; continue;
    }
    if (/^\|/.test(line) && TABLE_RULE.test(lines[i + 1] || '')) {
      const headers = cells(line).map(plain); const rows: string[][] = []; i += 2;
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(cells(lines[i++]));
      current.blocks.push({ type: 'table', headers, rows }); continue;
    }
    if (/^\s*(?:[-*+] |\d+\. )/.test(line)) {
      const ordered = /^\s*\d+\./.test(line); const match = ordered ? /^\s*\d+\. / : /^\s*[-*+] /; const items: string[] = [];
      while (i < lines.length && match.test(lines[i])) items.push(lines[i++].replace(match, ''));
      current.blocks.push({ type: 'list', ordered, items }); continue;
    }
    if (/^\s*([-*_])\1\1+\s*$/.test(line)) { i++; continue; }
    if (/^>/.test(line)) { current.blocks.push({ type: 'quote', text: line.replace(/^>\s?/, '') }); i++; continue; }
    const paragraph = [line]; i++;
    while (i < lines.length && lines[i].trim() && !/^(?:#{1,6}\s|\s*```|\||>|\s*[-*+] |\s*\d+\. )/.test(lines[i])) paragraph.push(lines[i++]);
    const verdict = paragraph.length === 1 ? line.trim().match(VERDICT) : null;
    if (verdict) { current.blocks.push({ type: 'verdict', name: verdict[1].trim().toUpperCase(), meta: verdict[2].replace(/^\s*·\s*/, '').trim() }); continue; }
    current.blocks.push({ type: 'p', text: paragraph.join(' ') });
  }
  flush();
  return { title, sections };
}
