import { redirect } from 'next/navigation';

// Legacy ?run= and ?page= links are redirected in next.config.ts; the bare root opens the run list.
export default function Page() {
  redirect('/runs');
}
