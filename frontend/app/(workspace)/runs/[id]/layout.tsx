'use client';
import { useParams } from 'next/navigation';
import RunLayout from '@/components/run-shell';

// Keyed by run ID so switching runs resets the run state, polling and dialogs.
export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  const { id = '' } = useParams<{ id: string }>();
  return <RunLayout key={id} id={id}>{children}</RunLayout>;
}
