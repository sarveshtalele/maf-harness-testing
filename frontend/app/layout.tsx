import type { Metadata } from 'next';
import './globals.css';
export const metadata: Metadata = { title: 'Agentic QE Platform', description: 'Human-approved repository testing workspace' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
