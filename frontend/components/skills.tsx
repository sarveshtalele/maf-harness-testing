'use client';
import { useEffect, useState } from 'react';
import { api, label, type Row } from '@/lib/harness';
import { Badge, DataView, Metrics } from './data';
import Markdown from './markdown';

type Skill = { name: string; description: string; role: string; primary_stages: string[]; purpose?: string; instructions: string; resources: string[]; metadata: Row; reviewed_scripts: Row; sha256: string; valid: boolean; locked: boolean; errors: string[]; warnings: string[]; provenance: Row };

export default function Skills() {
  const [catalog, setCatalog] = useState<{ skills: Skill[]; upstream: Row } | null>(null);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState('');
  useEffect(() => {
    setSelected(new URLSearchParams(location.search).get('skill') || '');
    const controller = new AbortController();
    api<{ skills: Skill[]; upstream: Row }>('skills', undefined, controller.signal).then(setCatalog).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  const skills = catalog?.skills || [];
  const visible = skills.filter(s => `${s.name} ${s.description} ${s.role} ${s.primary_stages.join(' ')}`.toLowerCase().includes(search.toLowerCase()));
  const skill = skills.find(s => s.name === selected);
  if (selected && catalog) return <><a className="button small" href="/?page=skills">← All agent skills</a>{skill ? <article className="skill-document">
    <header><span className="eyebrow">Agent skill · {skill.role}</span><h1>{label(skill.name.replaceAll('-', ' '))}</h1><p>{skill.description}</p><div className="skill-tags"><Badge value={skill.valid ? 'Valid package' : 'Integrity error'} />{skill.primary_stages.map(stage => <Badge key={stage} value={stage} />)}</div></header>
    <Markdown text={skill.instructions} />
    <section><h2>Package resources</h2><ul>{skill.resources.map(resource => <li key={resource}><code>{resource}</code></li>)}</ul></section>
    <section><h2>Integrity and reviewed scripts</h2><DataView value={{ package_sha256: skill.sha256, errors: skill.errors, warnings: skill.warnings, reviewed_scripts: skill.reviewed_scripts }} /></section>
  </article> : <p role="alert">This skill is not in the installed catalog.</p>}</>;
  return <><div className="page-header"><div><span className="eyebrow">Project skill library</span><h1>Agent skills</h1><p>Installed packages, workflow roles, instructions and integrity details.</p></div></div>
    {error && <div className="notice bad" role="alert">{error}</div>}
    {!catalog && !error && <p role="status">Loading project skills…</p>}
    {catalog && <><Metrics values={{ available_skills: skills.length, valid_packages: skills.filter(s => s.valid).length }} />
      <div className="table-tools"><input aria-label="Search agent skills" type="search" placeholder="Search name, description or workflow role…" value={search} onChange={e => setSearch(e.target.value)} /><small>{visible.length} of {skills.length} skills</small></div>
      {!visible.length && <p>No skills match your search.</p>}
      <div className="skills-grid">{visible.map(skill => <article className="skill-card" key={skill.name}>
        <div className="skill-card-heading"><span className="eyebrow">{skill.role}</span><Badge value={skill.valid ? 'Valid' : 'Integrity error'} /></div>
        <h2>{label(skill.name.replaceAll('-', ' '))}</h2><code>{skill.name}</code><p>{skill.description}</p>
        <div className="skill-tags">{skill.primary_stages.map(stage => <Badge key={stage} value={stage} />)}</div>
        {skill.purpose && <p className="field-help">{skill.purpose}</p>}
        <a className="button small" href={`/?page=skills&skill=${encodeURIComponent(skill.name)}`}>View skill details →</a>
      </article>)}</div>
      <p className="field-help">Availability does not mean every skill runs on every repository. Companion skills are selected from repository facts and the approved test design.</p>
    </>}
  </>;
}
