'use client';
import { useEffect, useState } from 'react';
import { api, label, rows, text, type Row } from '@/lib/harness';
import { ConfirmDialog, Fields, Metrics, StatusBadge, Table } from './page';
import Markdown from './markdown';
import Icon from './icons';

const Badge = StatusBadge;

type Skill = { name: string; tree_sha256?: string; description: string; role: string; primary_stages: string[]; purpose?: string; instructions: string; resources: string[]; metadata: Row; reviewed_scripts: Row; sha256: string; source?: 'repo' | 'plugin'; verified?: boolean; approved?: boolean; stages?: string[]; kind?: string; valid: boolean; locked: boolean; errors: string[]; warnings: string[]; provenance: Row; executions: Row[] };

export default function Skills() {
  const [catalog, setCatalog] = useState<{ skills: Skill[]; upstream: Row } | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState('');
  const [notice, setNotice] = useState('');
  const [approving, setApproving] = useState<Skill | null>(null);
  const reload = (signal?: AbortSignal) => api<{ skills: Skill[]; upstream: Row }>('skills', undefined, signal).then(setCatalog).catch(e => { if (!signal?.aborted) setError(e.message); });
  useEffect(() => {
    setSelected(new URLSearchParams(location.search).get('skill') || '');
    const controller = new AbortController();
    void reload(controller.signal);
    return () => controller.abort();
  }, []);
  const addSkill = async (file?: File) => {
    if (!file) return;
    setError(''); setNotice('');
    if (file.size > 20 * 1024 * 1024) { setError('Skill archive is larger than 20 MB.'); return; }
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      let binary = ''; for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
      const added = await api<{ name: string }>('skills/import', { zip_base64: btoa(binary) });
      setNotice(`${added.name} was added as unverified. Review it, then approve it to allow it in runs.`);
      await reload();
    } catch (e) { setError(e instanceof Error ? e.message : 'Import failed.'); }
  };
  // The approval names the hash the user reviewed; the backend refuses it if the skill changed since then.
  const approveSkill = async (skill: Skill) => {
    setApproving(null); setError(''); setNotice('');
    try { await api(`skills/${encodeURIComponent(skill.name)}/approve`, { tree_sha256: skill.tree_sha256 }); setNotice(`${skill.name} approved.`); await reload(); }
    catch (e) {
      const message = e instanceof Error ? e.message : 'Approval failed.';
      setError(/changed since it was reviewed/i.test(message) ? `${message} The list has been reloaded; review the skill again before approving.` : message);
      await reload();
    }
  };
  const trustBadge = (s: Skill) => <>
    <Badge value={s.source === 'plugin' ? 'Plugin' : 'Repo'} />
    <Badge value={s.verified ? 'Verified' : s.approved ? 'Approved' : 'Unverified'} />
  </>;
  const skills = catalog?.skills || [];
  const visible = skills.filter(s => `${s.name} ${s.description} ${s.role} ${s.primary_stages.join(' ')}`.toLowerCase().includes(search.toLowerCase()));
  const skill = skills.find(s => s.name === selected);
  if (selected && catalog) return <><a className="button small" href="/marketplace">← All agent skills</a>{skill ? <article className="skill-document">
    <header><span className="eyebrow">Agent skill · {skill.role}</span><h2>{label(skill.name.replaceAll('-', ' '))}</h2><p>{skill.description}</p><div className="skill-tags"><Badge value={skill.valid ? 'Valid package' : 'Integrity error'} />{trustBadge(skill)}{skill.primary_stages.map(stage => <Badge key={stage} value={stage} />)}</div></header>
    <section><h2>Recorded execution by run and step</h2><Table caption={`${skill.name} recorded executions`} rows={rows(skill.executions)} searchable empty="No recorded executions." /><p>Package loading and script execution are different events. Historical adapter calls have no script execution record.</p></section>
    <Markdown text={skill.instructions} />
    <section><h2>Package resources</h2><ul>{skill.resources.map(resource => <li key={resource}><code>{resource}</code></li>)}</ul></section>
    <section><h2>Integrity and reviewed scripts</h2><Fields data={{ package_sha256: skill.sha256, errors: skill.errors, warnings: skill.warnings, reviewed_scripts: skill.reviewed_scripts }} /></section>
  </article> : <p role="alert">This skill is not in the installed catalog.</p>}</>;
  return <><div className="page-header"><div><span className="eyebrow">Project skill library</span><h2><Icon name="spark" />Agent skills</h2><p>Installed packages, workflow roles, instructions and integrity details.</p></div><label className="button primary">Add skill<input aria-label="Add skill (.zip)" type="file" accept=".zip,application/zip" hidden onChange={e => { void addSkill(e.target.files?.[0]); e.target.value = ''; }} /></label></div>
    {notice && <div className="notice" role="status">{notice}</div>}
    {error && <div className="notice bad" role="alert">{error}</div>}
    {!catalog && !error && <p role="status">Loading project skills…</p>}
    {catalog && <><Metrics values={{ available_skills: skills.length, valid_packages: skills.filter(s => s.valid).length }} />
      <div className="table-tools"><input aria-label="Search agent skills" type="search" placeholder="Search name, description or workflow role…" value={search} onChange={e => setSearch(e.target.value)} /><small>{visible.length} of {skills.length} skills</small></div>
      {!visible.length && <p>No skills match your search.</p>}
      <div className="skills-grid">{visible.map(skill => <article className="skill-card" key={skill.name}>
        <div className="skill-card-heading"><span className="eyebrow">{skill.role}</span><span className="skill-tags"><Badge value={skill.valid ? 'Valid' : 'Integrity error'} />{trustBadge(skill)}</span></div>
        <h2>{label(skill.name.replaceAll('-', ' '))}</h2><code>{skill.name}</code><p>{skill.description}</p>
        <div className="skill-flow" aria-label={`${skill.name} workflow map`}><span className="eyebrow">Workflow map</span>{[...new Set([...skill.primary_stages, ...rows(skill.executions).map(r => text(r.stage))])].map(stage => <div className="skill-flow-step" key={stage}><span aria-hidden="true">◇</span><strong>{label(stage)}</strong><span aria-hidden="true">→</span><small>{rows(skill.executions).some(r => r.stage === stage) ? 'Recorded' : 'Configured'}</small></div>)}{!skill.primary_stages.length && !rows(skill.executions).length && <small>{skill.role === 'Host report validator' ? 'Reference package; no recorded package execution.' : 'Selected when repository facts match this skill.'}</small>}<div className="skill-flow-counts"><span><strong>{new Set(rows(skill.executions).map(r => text(r.run_id))).size}</strong> recorded runs</span><span><strong>{rows(skill.executions).filter(r => r.status === 'script-executed').length}</strong> script executions</span></div></div>
        {!!skill.stages?.length && <div className="skill-tags" aria-label={`${skill.name} stages`}>{skill.stages.map(stage => <Badge key={stage} value={label(stage)} />)}</div>}
        {skill.purpose && <p className="field-help">{skill.purpose}</p>}
        {!skill.verified && !skill.approved && <button className="small" onClick={() => setApproving(skill)}>Approve {skill.name}</button>}
        <a className="button small" href={`/marketplace?skill=${encodeURIComponent(skill.name)}`}>View skill details →</a>
      </article>)}</div>
      <p className="field-help">Availability does not mean every skill runs on every repository. Companion skills are selected from repository facts and the approved test design.</p>
    </>}
    <ConfirmDialog open={approving !== null} title={`Approve ${approving?.name ?? 'skill'}?`} confirmLabel="Approve" onConfirm={() => { if (approving) void approveSkill(approving); }} onClose={() => setApproving(null)}>
      <p>It is not covered by the repository lock. Approving lets agents load its instructions and allows its scripts to run on this machine. Any later file change revokes the approval.</p>
      <p>You are approving the reviewed package hash <code title={approving?.tree_sha256}>{approving?.tree_sha256 ? approving.tree_sha256.slice(0, 12) : 'not provided'}</code>.</p>
    </ConfirmDialog>
  </>;
}
