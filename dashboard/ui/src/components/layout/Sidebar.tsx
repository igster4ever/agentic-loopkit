import type { FC } from 'react';

interface NavItem {
  label: string;
  href:  string;
  icon:  string;
}

const NAV: NavItem[] = [
  { label: 'Streams',     href: '/streams',  icon: '⚡' },
  { label: 'Events',      href: '/events',   icon: '📋' },
  { label: 'Live Tail',   href: '/live',     icon: '🔴' },
  { label: 'Agents',      href: '/agents',   icon: '🤖' },
  { label: 'Adapters',    href: '/adapters', icon: '🔌' },
  { label: 'Memory',      href: '/memory',   icon: '🧠' },
];

interface SidebarProps {
  activePath: string;
  onNavigate: (href: string) => void;
}

export const Sidebar: FC<SidebarProps> = ({ activePath, onNavigate }) => (
  <nav className="w-52 shrink-0 bg-zinc-900 border-r border-zinc-800 flex flex-col py-4">
    <div className="px-4 mb-6">
      <h1 className="text-sm font-semibold text-zinc-300 tracking-widest uppercase">
        loopkit
      </h1>
      <p className="text-xs text-zinc-500 mt-0.5">event inspector</p>
    </div>

    <ul className="space-y-0.5 px-2 flex-1">
      {NAV.map(({ label, href, icon }) => {
        const active = activePath === href || (href !== '/' && activePath.startsWith(href));
        return (
          <li key={href}>
            <button
              onClick={() => onNavigate(href)}
              className={[
                'w-full text-left px-3 py-2 rounded text-sm flex items-center gap-2 transition-colors',
                active
                  ? 'bg-zinc-700 text-zinc-100'
                  : 'text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200',
              ].join(' ')}
            >
              <span>{icon}</span>
              {label}
            </button>
          </li>
        );
      })}
    </ul>

    <div className="px-4 pt-4 border-t border-zinc-800">
      <p className="text-xs text-zinc-600">agentic-loopkit</p>
    </div>
  </nav>
);
