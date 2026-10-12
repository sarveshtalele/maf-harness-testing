'use client';
import { Suspense } from 'react';
import Marketplace from '@/components/marketplace';

// Suspense is required because the skill detail reads ?skill= with useSearchParams.
export default function Page() {
  return <Suspense fallback={null}><Marketplace /></Suspense>;
}
