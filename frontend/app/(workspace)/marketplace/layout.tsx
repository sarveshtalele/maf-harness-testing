'use client';
import MarketplaceLayout from '@/components/marketplace';

export default function Layout({ children }: { children: React.ReactNode }) {
  return <MarketplaceLayout>{children}</MarketplaceLayout>;
}
