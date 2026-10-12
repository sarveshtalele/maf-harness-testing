'use client';
import { Suspense } from 'react';
import { EvidenceView } from '@/components/views/run-pages';

// Suspense is required because the view reads ?file= with useSearchParams.
export default function Page() {
  return <Suspense fallback={null}><EvidenceView /></Suspense>;
}
