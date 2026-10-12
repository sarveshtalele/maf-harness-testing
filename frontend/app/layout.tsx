import type { Metadata } from 'next';
import { headers } from 'next/headers';
import './globals.css';
export const metadata: Metadata = { title: 'Agentic QE Platform', description: 'Human-approved repository testing workspace' };
// Runs before first paint: applies the saved sidebar rail and theme to <html> so returning users never see a flash. Same keys as usePreference.
const FIRST_PAINT = `try{var d=document.documentElement;if(JSON.parse(localStorage.getItem('maf:sidebar-collapsed')||'false')===true)d.dataset.sidebar='collapsed';d.dataset.theme=JSON.parse(localStorage.getItem('maf:theme')||'"light"')==='dark'?'dark':'light'}catch(e){}`;
export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // The nonce comes from proxy.ts (per request); the FIRST_PAINT script is allowed by it, not by 'unsafe-inline'.
  const nonce = (await headers()).get('x-nonce') ?? undefined;
  return <html lang="en"><head><script nonce={nonce} dangerouslySetInnerHTML={{ __html: FIRST_PAINT }} /></head><body><a className="skip-link" href="#main-content">Skip to content</a>{children}</body></html>;
}
