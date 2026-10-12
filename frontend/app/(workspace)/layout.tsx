import Shell from '@/components/shell';

export default function WorkspaceLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <Shell>{children}</Shell>;
}
