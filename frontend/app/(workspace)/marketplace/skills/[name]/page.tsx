'use client';
import { useParams } from 'next/navigation';
import { SkillDetail } from '@/components/skills';

export default function Page() {
  const { name = '' } = useParams<{ name: string }>();
  return <SkillDetail name={decodeURIComponent(name)} />;
}
